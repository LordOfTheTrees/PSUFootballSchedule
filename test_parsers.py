"""
Offline parser tests - no network required.

The SIDEARM fixture below mirrors the class names the GitHub Actions run
reported from the live gopsusports.com schedule page, including AP-style dates
("Sept. 5") and a TBA broadcast slot.

Run: python test_parsers.py
"""
from __future__ import annotations

import logging
import sys

from bs4 import BeautifulSoup

from Script import (
    _DATE_RE,
    extract_game_data,
    find_game_elements,
    parse_date_time,
)

logging.disable(logging.CRITICAL)

# (date text, time text, tv text, opponent, venue modifier)
_FIXTURE_GAMES = [
    ("Sept. 5", "3:30 PM", "CBS", "Marshall", "home"),
    ("Sept. 12", "12:00 PM", "TBA", "Temple", "away"),
    ("Sept. 19", "12:00 PM", "BTN", "Buffalo", "home"),
    ("Sept. 26", "TBA", "TBA", "Wisconsin", "home"),
    ("Oct. 2", "8:00 PM", "FOX", "Northwestern", "away"),
    ("Oct. 10", "1:00 PM", "NBC", "USC", "home"),
    ("Oct. 17", "TBA", "TBA", "Michigan", "away"),
    ("Oct. 31", "TBA", "TBA", "Purdue", "home"),
    ("Nov. 7", "3:30 PM", "BTN", "Washington", "away"),
    ("Nov. 14", "TBA", "TBA", "Minnesota", "home"),
    ("Nov. 21", "12:00 PM", "FOX", "Rutgers", "home"),
    ("Nov. 28", "TBA", "TBA", "Maryland", "away"),
]


def _build_fixture() -> str:
    rows = []
    for date_text, time_text, tv_text, opponent, venue in _FIXTURE_GAMES:
        rows.append(f"""
        <div class="schedule-event">
          <div class="schedule-event__top">
            <div class="schedule-event-date__wrapper">
              <div class="schedule-event-date"><span class="schedule-event-date__day">{date_text}</span></div>
              <div class="schedule-event-date__time-wrapper">
                <span class="schedule-event-date__time">{time_text}</span>
              </div>
            </div>
            <div class="schedule-event__teams">
              <div class="schedule-event-item-team">
                <span class="schedule-event-item-team__name">Penn State</span>
              </div>
              <span class="schedule-event-item-team__divider">vs</span>
              <div class="schedule-event-item-team">
                <span class="schedule-event-item-team__name">{opponent}</span>
              </div>
            </div>
            <div class="schedule-event__venue schedule-event__venue--{venue}">
              <span class="schedule-event__venue-text">Beaver Stadium</span>
            </div>
          </div>
          <div class="schedule-event__bottom">
            <div class="schedule-event__tv-networks"><span class="schedule-event__tv-link">{tv_text}</span></div>
          </div>
        </div>""")
    return f"<html><body><div class='schedule-events__list'>{''.join(rows)}</div></body></html>"


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    return condition


def test_date_regex() -> bool:
    print("Date regex accepts the formats schedule pages actually use:")
    ok = True
    for text, expected in [
        ("Sept. 5", "Sept. 5"),
        ("Aug. 29", "Aug. 29"),
        ("Sep 5", "Sep 5"),
        ("September 5", "September 5"),
        ("Sat, Oct. 3", "Oct. 3"),
        ("Nov. 21", "Nov. 21"),
    ]:
        match = _DATE_RE.search(text)
        ok &= check(f"{text!r}", bool(match) and match.group(1) == expected,
                    f"got {match.group(1) if match else None!r}")
    return ok


def test_season_rollover() -> bool:
    print("Undated months map onto the right calendar year for season 2026:")
    ok = True
    november = parse_date_time("Nov. 21", "12:00 PM", 2026)
    ok &= check("Nov. 21 -> 2026", november is not None and november.year == 2026,
                str(november))
    january = parse_date_time("Jan. 1", "1:00 PM", 2026)
    ok &= check("Jan. 1 -> 2027 (bowl season)", january is not None and january.year == 2027,
                str(january))
    explicit = parse_date_time("2026-09-05", "3:30 PM", 2026)
    ok &= check("explicit 2026-09-05 unchanged", explicit is not None and explicit.year == 2026,
                str(explicit))
    return ok


def test_sidearm_fixture() -> bool:
    print("SIDEARM fixture (current gopsusports DOM):")
    soup = BeautifulSoup(_build_fixture(), "html.parser")
    elements = find_game_elements(soup)
    ok = check(f"found {len(elements)} game elements", len(elements) == 12)
    if not elements:
        return False

    parsed = [extract_game_data(e) for e in elements]
    parsed = [p for p in parsed if p]
    ok &= check(f"extracted {len(parsed)} games", len(parsed) == 12)

    opponents = [p["opponent"] for p in parsed]
    ok &= check("opponent is never Penn State", "Penn State" not in opponents, str(opponents[:3]))
    ok &= check("first opponent is Marshall", opponents[:1] == ["Marshall"], str(opponents[:1]))

    ok &= check("TBA is not stored as a broadcaster",
                all(p["broadcast"].upper() != "TBA" for p in parsed))
    ok &= check("real broadcaster is kept", parsed[0]["broadcast"] == "CBS", parsed[0]["broadcast"])

    ok &= check("home/away read from venue class",
                parsed[0]["is_home"] is True and parsed[1]["is_home"] is False,
                f"{parsed[0]['is_home']}, {parsed[1]['is_home']}")

    dates = [parse_date_time(p["date_str"], p["time_str"], 2026) for p in parsed]
    ok &= check("every date parses", all(d is not None for d in dates),
                str([p["date_str"] for p, d in zip(parsed, dates) if d is None]))
    if all(d is not None for d in dates):
        ok &= check("dates are in season order and unique",
                    dates == sorted(dates) and len(set(dates)) == 12)
        ok &= check("TBA kickoff defaults to 1pm ET",
                    dates[3].hour == 13, str(dates[3]))
        ok &= check("3:30 PM kickoff preserved",
                    dates[0].hour == 15 and dates[0].minute == 30, str(dates[0]))
    return ok


class _FakeResponse:
    """Stands in for a requests/curl_cffi response."""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


def _espn_payload() -> dict:
    events = []
    for month, day, hour, opponent, psu_home in [
        (9, 5, 19, "Marshall Thundering Herd", True),
        (9, 12, 16, "Temple Owls", False),
        (9, 19, 16, "Buffalo Bulls", True),
        (9, 26, 17, "Wisconsin Badgers", True),
        (10, 3, 0, "Northwestern Wildcats", False),
        (10, 10, 17, "USC Trojans", True),
        (10, 17, 17, "Michigan Wolverines", False),
        (10, 31, 17, "Purdue Boilermakers", True),
        (11, 7, 18, "Washington Huskies", False),
        (11, 14, 18, "Minnesota Golden Gophers", True),
        (11, 21, 17, "Rutgers Scarlet Knights", True),
        (11, 28, 18, "Maryland Terrapins", False),
    ]:
        events.append({
            "date": f"2026-{month:02d}-{day:02d}T{hour:02d}:00:00Z",
            "competitions": [{
                "timeValid": hour != 0,
                "venue": {"fullName": "Beaver Stadium", "address": {"city": "University Park", "state": "PA"}},
                "broadcasts": [{"names": ["BTN"]}],
                "competitors": [
                    {"homeAway": "home" if psu_home else "away",
                     "team": {"displayName": "Penn State Nittany Lions"}},
                    {"homeAway": "away" if psu_home else "home",
                     "team": {"displayName": opponent}},
                ],
            }],
        })
    return {"events": events}


def test_espn_api_parsing() -> bool:
    print("ESPN API parsing (mocked payload):")
    import Script

    original = Script.http_get
    calls = []

    def fake_http_get(url, **kwargs):
        calls.append(url)
        # First endpoint is walled off, as it was in the failing build.
        if len(calls) == 1:
            return _FakeResponse({}, status_code=403)
        return _FakeResponse(_espn_payload())

    Script.http_get = fake_http_get
    try:
        games = Script.scrape_espn_api(2026)
    finally:
        Script.http_get = original

    ok = check("falls past a 403 endpoint to the next host", len(calls) >= 2, f"{len(calls)} calls")
    ok &= check(f"parsed {len(games)} games", len(games) == 12)
    if games:
        ok &= check("home game titled '<opponent> at Penn State'",
                    games[0]["title"] == "Marshall Thundering Herd at Penn State", games[0]["title"])
        ok &= check("away game titled 'Penn State at <opponent>'",
                    games[1]["title"] == "Penn State at Temple Owls", games[1]["title"])
        ok &= check("kickoff converted to Eastern", games[0]["start"].hour == 15,
                    str(games[0]["start"]))
        ok &= check("midnight-UTC placeholder becomes 1pm ET",
                    games[4]["start"].hour == 13, str(games[4]["start"]))
    return ok


def test_http_get_survives_failure() -> bool:
    print("http_get error handling:")
    import Script

    # Unroutable host: every transport raises, and the helper must return None
    # rather than propagating.
    result = Script.http_get("https://127.0.0.1:9/nothing", timeout=2, retries=1)
    return check("returns None when all transports fail", result is None, str(result))


def main() -> None:
    results = [
        test_date_regex(),
        test_season_rollover(),
        test_sidearm_fixture(),
        test_espn_api_parsing(),
        test_http_get_survives_failure(),
    ]
    print()
    if all(results):
        print("All parser tests passed.")
        sys.exit(0)
    print("Parser tests FAILED.")
    sys.exit(1)


if __name__ == "__main__":
    main()
