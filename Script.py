import requests
from bs4 import BeautifulSoup
import ics
from ics import Calendar, Event
import datetime
import time
import os
import re
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("penn_state_football_scraper.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

CALENDAR_FILE = "penn_state_football.ics"

# Expected number of games per season for validation
EXPECTED_GAMES_PER_SEASON = {
    2025: 12,  # Penn State typically plays 12 games (12 regular season + potential bowl)
    2024: 12,
    2023: 12,
    # Add more years as needed
}

# Minimum acceptable number of games (fallback for unknown years)
MIN_GAMES_THRESHOLD = 10

def get_current_season():
    """Get the current football season based on the current date"""
    today = datetime.datetime.now()
    if today.month > 2:
        return today.year
    else:
        return today.year - 1

def get_sidearm_headers():
    """Headers optimized for SIDEARM Sports platform"""
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0',
        'Referer': 'https://www.google.com/',
    }

def parse_date_time(date_str, time_str="", year=None):
    """
    STRICT date/time parsing - returns None if parsing fails
    Uses 1pm ET default for games without specified times
    """
    try:
        if year is None:
            year = get_current_season()
            
        # Clean inputs
        date_str = date_str.strip() if date_str else ""
        time_str = time_str.strip() if time_str else ""
        
        # STRICT REQUIREMENT: Must have actual date string
        if not date_str or date_str.upper() in ["TBA", "TBD", "TIME TBA", ""]:
            logger.warning(f"No valid date string provided: '{date_str}'")
            return None
        
        logger.debug(f"Parsing date: '{date_str}', time: '{time_str}', year: {year}")
        
        # Handle various date formats
        month, day = None, None
        
        if "/" in date_str:
            # Format: MM/DD or MM/DD/YY
            parts = date_str.split("/")
            if len(parts) >= 2:
                try:
                    month = int(parts[0])
                    day = int(parts[1])
                    if len(parts) >= 3 and len(parts[2]) >= 2:
                        year_part = int(parts[2])
                        if year_part > 50:
                            year = 1900 + year_part
                        else:
                            year = 2000 + year_part
                except ValueError:
                    logger.error(f"Could not parse numeric date parts: {parts}")
                    return None
        elif re.match(r'\w+\s+\d+', date_str):
            # Handle "Sep 20", "September 20" format
            try:
                from dateutil import parser
                parsed = parser.parse(f"{date_str} {year}")
                month, day = parsed.month, parsed.day
            except:
                # Fallback manual parsing
                month_names = {
                    'Jan': 1, 'January': 1, 'Feb': 2, 'February': 2, 'Mar': 3, 'March': 3,
                    'Apr': 4, 'April': 4, 'May': 5, 'Jun': 6, 'June': 6,
                    'Jul': 7, 'July': 7, 'Aug': 8, 'August': 8, 'Sep': 9, 'September': 9,
                    'Oct': 10, 'October': 10, 'Nov': 11, 'November': 11, 'Dec': 12, 'December': 12
                }
                parts = date_str.split()
                if len(parts) >= 2:
                    month_str = parts[0]
                    # Try exact match first, then partial match
                    month = month_names.get(month_str)
                    if not month:
                        for key, val in month_names.items():
                            if month_str.lower().startswith(key.lower()[:3]):
                                month = val
                                break
                    
                    try:
                        day = int(parts[1])
                    except ValueError:
                        logger.error(f"Could not parse day from: {parts[1]}")
                        return None
                else:
                    logger.error(f"Insufficient date parts: {parts}")
                    return None
        elif re.match(r'\d{1,2}/\d{1,2}', date_str):
            # Handle MM/DD format
            parts = date_str.split('/')
            try:
                month = int(parts[0])
                day = int(parts[1])
            except ValueError:
                logger.error(f"Could not parse MM/DD format: {date_str}")
                return None
        elif re.match(r'\d{4}-\d{2}-\d{2}', date_str):
            # Handle YYYY-MM-DD format
            parts = date_str.split('-')
            try:
                year = int(parts[0])
                month = int(parts[1])
                day = int(parts[2])
            except ValueError:
                logger.error(f"Could not parse YYYY-MM-DD format: {date_str}")
                return None
        
        # STRICT REQUIREMENT: Must successfully parse month and day
        if month is None or day is None:
            logger.error(f"Failed to parse date: {date_str} - month={month}, day={day}")
            return None
        
        # Parse time - default to 1pm ET if no time provided
        hour, minute = 13, 0  # Default to 1pm ET for college football
        
        if time_str and time_str.upper() not in ["TBA", "TBD", "", "TIME TBA"]:
            is_pm = "PM" in time_str.upper()
            is_am = "AM" in time_str.upper()
            
            # Extract just the time part
            time_clean = re.sub(r'[^\d:]', '', time_str)
            
            if ":" in time_clean:
                time_parts = time_clean.split(":")
                try:
                    hour = int(time_parts[0])
                    minute = int(time_parts[1]) if len(time_parts) > 1 else 0
                except ValueError:
                    logger.warning(f"Could not parse time parts: {time_parts}, using 1pm ET default")
                    hour, minute = 13, 0
            elif time_clean.isdigit() and len(time_clean) <= 2:
                try:
                    hour = int(time_clean)
                    minute = 0
                except ValueError:
                    hour = 13  # Default to 1pm
            
            # Handle AM/PM conversion
            if is_pm and hour < 12:
                hour += 12
            elif is_am and hour == 12:
                hour = 0
            elif not is_am and not is_pm and hour < 8:
                # If no AM/PM specified and hour is small, assume PM for college games
                hour += 12
        else:
            logger.debug(f"No valid time found for {date_str}, using 1pm ET default")
        
        # Validate the date
        try:
            result = datetime.datetime(year, month, day, hour, minute)
            
            # Additional validation: check if date is reasonable for football season
            if result.month < 8 or result.month > 12:
                logger.warning(f"Date outside typical football season: {result}")
                # Still allow it, but log warning
            
            logger.debug(f"Successfully parsed: {result}")
            return result
        except ValueError as e:
            logger.error(f"Invalid date/time values: year={year}, month={month}, day={day}, hour={hour}, minute={minute} - {e}")
            return None
    
    except Exception as e:
        logger.error(f"Error parsing date/time: {date_str}, {time_str} - {str(e)}")
        return None

def validate_schedule(games, season):
    """
    STRICT validation - calendar will be empty if this fails
    """
    if not games:
        logger.error("No games found in schedule")
        return False
    
    expected_count = EXPECTED_GAMES_PER_SEASON.get(season, MIN_GAMES_THRESHOLD)
    
    # Strict validation - must meet minimum threshold
    if len(games) < MIN_GAMES_THRESHOLD:
        logger.error(f"Only found {len(games)} games for season {season}, expected at least {MIN_GAMES_THRESHOLD}")
        return False
    
    # Validate that each game has required fields
    for i, game in enumerate(games):
        if not game.get('opponent'):
            logger.error(f"Game {i+1} missing opponent: {game}")
            return False
        if not game.get('start'):
            logger.error(f"Game {i+1} missing start time: {game}")
            return False
        if not game.get('title'):
            logger.error(f"Game {i+1} missing title: {game}")
            return False
    
    # Check for suspicious dates (all games on same date, etc.)
    dates = [game['start'].date() for game in games]
    unique_dates = len(set(dates))
    
    if len(games) > 1 and unique_dates < len(games) * 0.7:  # At least 70% should be on different dates
        logger.error(f"Schedule has suspicious date distribution: {unique_dates} unique dates for {len(games)} games")
        return False
    
    # Check for reasonable date range (games should span Aug-Dec for college football)
    if dates:
        earliest = min(dates)
        latest = max(dates)
        
        # Check that dates span a reasonable period
        date_span = (latest - earliest).days
        if len(games) > 3 and date_span < 30:  # If more than 3 games, should span at least a month
            logger.error(f"Schedule dates too clustered: {date_span} days for {len(games)} games")
            return False
        
        logger.info(f"Schedule validation passed: {len(games)} games from {earliest} to {latest}")
    
    return True

def detect_schedule_structure(soup):
    """Dynamically detect the schedule structure on SIDEARM pages"""
    logger.info("Analyzing page structure for schedule data...")
    
    # Look for common SIDEARM schedule patterns
    possible_containers = [
        # Modern SIDEARM selectors
        '.sidearm-schedule-games',
        '.sidearm-schedule-games-container', 
        '.schedule-list',
        '.game-list',
        '.event-listing',
        
        # Table-based layouts
        'table.sidearm-table',
        'table.schedule',
        'table.schedule-table',
        '.ResponsiveTable table',
        
        # Card/item based layouts
        '.schedule-game',
        '.game-card',
        '.event-card',
        '.schedule-item',
        
        # Generic containers that might hold games
        '[data-module*="schedule"]',
        '[id*="schedule"]',
        '[class*="schedule"]'
    ]
    
    for selector in possible_containers:
        container = soup.select_one(selector)
        if container:
            # Look for individual game items within this container
            game_selectors = [
                '.sidearm-schedule-game',
                '.schedule-game', 
                '.game-item',
                '.event-item',
                'tr',  # Table rows
                '.game',
                '.event',
                '[data-game]',
                '[class*="game"]'
            ]
            
            for game_sel in game_selectors:
                games = container.select(game_sel)
                if len(games) > 3:  # Must have several games to be valid
                    logger.info(f"Found schedule structure: {selector} -> {game_sel} ({len(games)} items)")
                    return container, game_sel
    
    logger.warning("Could not detect schedule structure")
    return None, None

def extract_game_data(game_element):
    """
    STRICT extraction - returns None if cannot find opponent and date
    """
    try:
        # Try multiple strategies to extract date
        date_str = ""
        date_selectors = [
            '.date', '.game-date', '.event-date', '.schedule-date',
            '.sidearm-schedule-game-opponent-date',
            '[class*="date"]', 'time', '.datetime',
            'td:first-child', '.first-col'
        ]
        
        for sel in date_selectors:
            date_elem = game_element.select_one(sel)
            if date_elem:
                date_str = date_elem.get_text(strip=True)
                if date_str and any(char.isdigit() for char in date_str):
                    break
        
        # Try multiple strategies to extract time
        time_str = ""
        time_selectors = [
            '.time', '.game-time', '.event-time', '.schedule-time',
            '.sidearm-schedule-game-opponent-time',
            '[class*="time"]', '.kickoff'
        ]
        
        for sel in time_selectors:
            time_elem = game_element.select_one(sel)
            if time_elem:
                time_str = time_elem.get_text(strip=True)
                if time_str and time_str.upper() not in ["", "TBA", "TBD"]:
                    break
        
        # Try multiple strategies to extract opponent
        opponent = ""
        opponent_selectors = [
            '.opponent', '.team-name', '.visitor', '.away-team', '.home-team',
            '.sidearm-schedule-game-opponent-name',
            '[class*="opponent"]', '[class*="team"]',
            'a[href*="team"]', 'td:nth-child(2)'
        ]
        
        for sel in opponent_selectors:
            opp_elem = game_element.select_one(sel)
            if opp_elem:
                opponent = opp_elem.get_text(strip=True)
                if opponent and len(opponent) > 2:
                    break
        
        # If still no opponent, look in all text content
        if not opponent:
            all_text = game_element.get_text()
            # Look for patterns like "vs Team" or "at Team"
            match = re.search(r'(?:vs\.?\s+|at\s+|@\s*)([A-Za-z\s&]+)', all_text, re.IGNORECASE)
            if match:
                opponent = match.group(1).strip()
        
        # STRICT REQUIREMENT: Must have both date and opponent
        if not date_str or not opponent:
            logger.debug(f"Missing required data - date: '{date_str}', opponent: '{opponent}'")
            return None
        
        # Clean opponent name
        opponent = re.sub(r'^(vs\.?\s*|at\s*|@\s*)', '', opponent, flags=re.IGNORECASE).strip()
        
        # Final validation of opponent
        if len(opponent) < 2 or opponent.upper() in ["TBA", "TBD", "BYE"]:
            logger.debug(f"Invalid opponent after cleaning: '{opponent}'")
            return None
        
        # Determine home/away
        all_text = game_element.get_text().lower()
        is_away = any(indicator in all_text for indicator in ['at ', '@ ', 'away'])
        is_home = not is_away
        
        return {
            'date_str': date_str,
            'time_str': time_str, 
            'opponent': opponent,
            'is_home': is_home,
            'raw_text': game_element.get_text(strip=True)[:100]  # For debugging
        }
        
    except Exception as e:
        logger.error(f"Error extracting game data: {e}")
        return None

def scrape_penn_state_schedule(season=None):
    """Modern SIDEARM-aware Penn State schedule scraper with STRICT validation"""
    if season is None:
        season = get_current_season()
    
    logger.info(f"Scraping Penn State schedule for season {season}")
    games = []
    
    try:
        headers = get_sidearm_headers()
        session = requests.Session()
        session.headers.update(headers)
        
        # Try the schedule page with multiple approaches
        base_urls = [
            f"https://gopsusports.com/sports/football/schedule/{season}",
            f"https://gopsusports.com/sports/football/schedule",
            f"https://gopsusports.com/schedule?sport=football&season={season}"
        ]
        
        for url in base_urls:
            try:
                logger.info(f"Trying URL: {url}")
                
                # Add delay to avoid being flagged as bot
                time.sleep(2)
                
                response = session.get(url, timeout=30)
                
                # Check for bot detection or ad blocker messages
                if ("ad blocker" in response.text.lower() or 
                    "blocks ads hinders" in response.text.lower() or 
                    response.status_code == 403):
                    logger.warning(f"Bot/ad blocker detection triggered for {url}")
                    continue
                
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Dynamically detect schedule structure
                container, game_selector = detect_schedule_structure(soup)
                
                if not container:
                    logger.warning(f"No schedule structure found on {url}")
                    continue
                
                # Extract games using detected structure
                game_elements = container.select(game_selector)
                logger.info(f"Found {len(game_elements)} potential game elements")
                
                for game_elem in game_elements:
                    game_data = extract_game_data(game_elem)
                    
                    # STRICT: Skip if extraction failed
                    if not game_data:
                        continue
                    
                    # Create game info
                    opponent = game_data['opponent']
                    is_home = game_data['is_home']
                    
                    if is_home:
                        title = f"{opponent} at Penn State"
                        location = "University Park, Pa.\nBeaver Stadium"
                    else:
                        title = f"Penn State at {opponent}"
                        location = ""
                    
                    # STRICT: Parse datetime - if it fails, skip this game entirely
                    game_datetime = parse_date_time(game_data['date_str'], game_data['time_str'], season)
                    
                    if not game_datetime:
                        logger.warning(f"Failed to parse datetime for {title}, SKIPPING game")
                        continue
                    
                    duration = datetime.timedelta(hours=3, minutes=30)
                    
                    game_info = {
                        'title': title,
                        'start': game_datetime,
                        'end': game_datetime + duration,
                        'location': location,
                        'broadcast': "",
                        'is_home': is_home,
                        'opponent': opponent,
                        'date_str': game_data['date_str'],
                        'time_str': game_data['time_str']
                    }
                    
                    games.append(game_info)
                    logger.info(f"Successfully scraped: {title} on {game_datetime}")
                
                if games:
                    logger.info(f"Successfully scraped {len(games)} games from {url}")
                    return games
                    
            except Exception as e:
                logger.error(f"Error with {url}: {e}")
                continue
        
    except Exception as e:
        logger.error(f"Error scraping Penn State schedule: {str(e)}")
    
    return games

def scrape_espn_schedule(season=None):
    """ESPN backup scraper with STRICT validation - using correct Penn State URL"""
    if season is None:
        season = get_current_season()
    
    logger.info(f"Scraping ESPN for Penn State season {season}")
    games = []
    
    try:
        headers = get_sidearm_headers()
        # Use the correct ESPN Penn State URL with team name slug
        url = f"https://www.espn.com/college-football/team/schedule/_/id/213/penn-state-nittany-lions"
        
        # If we need a specific season, try adding it as a parameter
        if season != get_current_season():
            url += f"?season={season}"
            
        logger.info(f"ESPN URL: {url}")
        
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # ESPN schedule parsing - try multiple table formats
        table = soup.find('table', class_='Table')
        if not table:
            table = soup.find('div', class_='ResponsiveTable')
            if table:
                table = table.find('table')
        
        if not table:
            # Try alternative ESPN layout - look for schedule containers
            schedule_containers = [
                '.Schedule',
                '.ScheduleEvents',
                '.TeamSchedule',
                '[data-module="Schedule"]'
            ]
            
            for container_sel in schedule_containers:
                container = soup.select_one(container_sel)
                if container:
                    table = container.find('table')
                    if table:
                        break
        
        if not table:
            # Last resort - find any table
            table = soup.find('table')
        
        if table:
            rows = table.find_all('tr')[1:]  # Skip header
            logger.info(f"ESPN: Found {len(rows)} table rows")
            
            for i, row in enumerate(rows):
                try:
                    cells = row.find_all(['td', 'th'])
                    if len(cells) >= 2:
                        date_str = cells[0].get_text(strip=True)
                        opponent_cell = cells[1]
                        
                        # Skip if this is a header row or separator
                        if not date_str or date_str.lower() in ['date', 'day', 'week']:
                            continue
                        
                        # Get opponent text - might be in a link
                        opponent_link = opponent_cell.find('a')
                        if opponent_link:
                            opponent_str = opponent_link.get_text(strip=True)
                        else:
                            opponent_str = opponent_cell.get_text(strip=True)
                        
                        logger.debug(f"ESPN row {i}: date='{date_str}', opponent='{opponent_str}'")
                        
                        # STRICT: Skip bye weeks and invalid entries
                        if not opponent_str or opponent_str.lower() in ['bye', 'open', 'tbd', 'tba']:
                            logger.debug(f"Skipping invalid ESPN entry: {opponent_str}")
                            continue
                        
                        # Extract time if available - could be in multiple columns
                        time_str = ""
                        time_found = False
                        
                        # Check cells for time info (usually columns 2-4)
                        for cell_idx in range(2, min(len(cells), 5)):
                            cell_text = cells[cell_idx].get_text(strip=True)
                            # Look for time patterns
                            if re.search(r'\d+:\d+\s*[AP]M', cell_text, re.IGNORECASE):
                                time_str = cell_text
                                time_found = True
                                break
                            # Look for "TBA" or "TBD" time indicators
                            elif cell_text.upper() in ['TBA', 'TBD', 'TIME TBA']:
                                time_str = ""  # Will default to 1pm
                                time_found = True
                                break
                        
                        if not time_found:
                            logger.debug(f"No time found for {opponent_str}, will use 1pm ET default")
                        
                        # Determine home/away from opponent string or other indicators
                        is_away = any(indicator in opponent_str.lower() for indicator in ['at ', '@ '])
                        
                        # Also check for home/away indicators in other cells
                        if not is_away:
                            for cell in cells:
                                cell_text = cell.get_text(strip=True).lower()
                                if any(indicator in cell_text for indicator in ['away', 'at ', '@ ']):
                                    is_away = True
                                    break
                        
                        # Clean opponent name
                        opponent = re.sub(r'^(vs\.?\s*|at\s*|@\s*)', '', opponent_str, flags=re.IGNORECASE).strip()
                        
                        # Remove common ESPN formatting artifacts
                        opponent = re.sub(r'\s*\(\d+\)\s*$', '', opponent)  # Remove rankings like "(5)"
                        opponent = re.sub(r'\s*#\d+\s*', '', opponent)      # Remove rankings like "#5"
                        
                        # STRICT: Must have valid opponent
                        if not opponent or len(opponent) < 2:
                            logger.debug(f"Invalid opponent after cleaning: '{opponent}'")
                            continue
                        
                        if is_away:
                            title = f"Penn State at {opponent}"
                            location = ""
                        else:
                            title = f"{opponent} at Penn State"
                            location = "University Park, Pa.\nBeaver Stadium"
                        
                        # STRICT: Parse datetime - if it fails, skip this game
                        game_datetime = parse_date_time(date_str, time_str, season)
                        
                        if not game_datetime:
                            logger.warning(f"Could not parse ESPN datetime for {title}, SKIPPING")
                            continue
                        
                        duration = datetime.timedelta(hours=3, minutes=30)
                        
                        game_info = {
                            'title': title,
                            'start': game_datetime,
                            'end': game_datetime + duration,
                            'location': location,
                            'broadcast': "",
                            'is_home': not is_away,
                            'opponent': opponent,
                            'date_str': date_str,
                            'time_str': time_str if time_str else "1:00 PM"  # Show default in logs
                        }
                        
                        games.append(game_info)
                        logger.info(f"ESPN: Successfully scraped {title} on {game_datetime}")
                        
                except Exception as e:
                    logger.error(f"Error parsing ESPN row {i}: {e}")
                    continue
        else:
            logger.error("ESPN: No schedule table found on page")
            # Log some page content for debugging
            logger.debug(f"Page title: {soup.title.string if soup.title else 'No title'}")
            
        
    except Exception as e:
        logger.error(f"Error scraping ESPN: {str(e)}")
    
    logger.info(f"ESPN scraper found {len(games)} games")
    return games

def scrape_schedule(season=None):
    """
    Main scraping function - STRICT validation, empty calendar if failed
    """
    if season is None:
        season = get_current_season()
    
    logger.info("Starting STRICT schedule scraping...")
    
    # Try Penn State first, then ESPN
    sources = [
        ("Penn State SIDEARM", scrape_penn_state_schedule),
        ("ESPN", scrape_espn_schedule)
    ]
    
    for source_name, scrape_func in sources:
        logger.info(f"Trying {source_name}...")
        try:
            games = scrape_func(season)
            logger.info(f"{source_name} returned {len(games)} games")
            
            # Check if we have any games at all
            if not games:
                logger.warning(f"No games from {source_name}, trying next source")
                continue
            
            # STRICT validation - must pass all checks
            if validate_schedule(games, season):
                logger.info(f"SUCCESS: {len(games)} valid games from {source_name}")
                return games
            else:
                logger.warning(f"{source_name} games failed STRICT validation, trying next source")
                continue
                
        except Exception as e:
            logger.error(f"{source_name} failed with error: {e}, trying next source")
            continue
    
    # STRICT: If all sources fail, return empty list (no calendar)
    logger.error("ALL scraping sources failed STRICT validation")
    logger.error("Calendar will be EMPTY due to parsing failure")
    return []

def create_calendar(games):
    """Create iCalendar file - empty if no games provided"""
    cal = Calendar()
    cal._prodid = "Penn State Football Schedule"
    
    if not games:
        logger.warning("Creating EMPTY calendar due to scraping failure")
        # Create empty calendar
        with open(CALENDAR_FILE, 'w') as f:
            f.write(cal.serialize())
        return cal
    
    for game in games:
        event = Event()
        event.name = game['title']
        event.begin = game['start']
        event.end = game['end']
        event.location = game['location']
        
        description = ""
        if game['broadcast']:
            description += f"Broadcast: {game['broadcast']}\n"
        description += "Home Game" if game['is_home'] else "Away Game"
        if game['opponent']:
            description += f"\nOpponent: {game['opponent']}"
            
        event.description = description
        cal.events.add(event)
    
    with open(CALENDAR_FILE, 'w') as f:
        f.write(cal.serialize())
    
    logger.info(f"Calendar created with {len(games)} events")
    return cal

def update_calendar(custom_season=None):
    """Update the calendar with STRICT validation"""
    try:
        season = custom_season or get_current_season()
        games = scrape_schedule(season)
        
        # Always create calendar, even if empty
        create_calendar(games)
        
        if games:
            logger.info(f"Calendar updated successfully with {len(games)} validated games")
            return True
        else:
            logger.warning("Calendar updated with 0 games due to parsing failures")
            return False
        
    except Exception as e:
        logger.error(f"Error updating calendar: {str(e)}")
        # Create empty calendar on error
        create_calendar([])
        return False

if __name__ == "__main__":
    # For standalone execution
    current_season = get_current_season()
    logger.info(f"Starting Penn State Football Schedule Scraper for season {current_season}")
    logger.info("Using STRICT parsing - calendar will be empty if parsing fails")
    
    success = update_calendar()
    if success:
        print(f"Calendar updated successfully for season {current_season}")
    else:
        print(f"Calendar update failed - check logs for details")