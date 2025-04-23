import requests
from bs4 import BeautifulSoup
import ics
from ics import Calendar, Event
import datetime
import time
import os
from flask import Flask, Response
import logging
import re
from apscheduler.schedulers.background import BackgroundScheduler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("football_scraper.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Base URL for Penn State football
BASE_URL = "https://gopsusports.com/sports/football/schedule?view=list"
CALENDAR_FILE = "penn_state_football.ics"

def parse_date_time(date_str, time_str):
    """Parse date and time strings into datetime object"""
    try:
        logger.info(f"Parsing date: '{date_str}', time: '{time_str}'")
        
        # First, determine the current football season year
        today = datetime.datetime.now()
        current_year = today.year
        current_month = today.month
        
        # If we're in February or later, we're likely looking at the upcoming season
        if current_month >= 2:
            football_season_year = current_year
        else:
            football_season_year = current_year - 1
            
        logger.info(f"Current date: {today}, determined football season year: {football_season_year}")
        
        # Clean up the input date string
        # Fix cases like "SaturdayNov 22" where day of week and month are joined
        weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        cleaned_date_str = date_str
        
        # Try to split day of week from month when they're joined
        for day in weekdays:
            if day.lower() in date_str.lower():
                # Find where the weekday ends
                day_pos = date_str.lower().find(day.lower()) + len(day)
                if day_pos < len(date_str):
                    # Insert a space after the weekday if there isn't one
                    if date_str[day_pos:day_pos+1] != ' ':
                        cleaned_date_str = date_str[:day_pos] + ' ' + date_str[day_pos:]
                        logger.info(f"Fixed joined weekday-month: '{date_str}' -> '{cleaned_date_str}'")
                        break
        
        date_str = cleaned_date_str
        
        # Extract year, month, day
        year = None
        month = None
        day = None
        
        # Try various date formats
        # Format 1: MM/DD/YYYY
        if "/" in date_str and len(date_str.split("/")) >= 2:
            parts = date_str.strip().split("/")
            try:
                month = int(parts[0])
                day = int(parts[1])
                if len(parts) > 2 and parts[2].strip():
                    year = int(parts[2])
                    if year < 100:
                        year += 2000
            except (ValueError, IndexError):
                logger.warning(f"Failed to parse MM/DD/YYYY format: {date_str}")
        
        # Format 2: Month + Day pattern
        # This format handles various cases like:
        # - "April 26, 2025"
        # - "April 26" 
        # - "Apr 26"
        # - "Saturday Apr 26"
        # - "Saturday, Apr 26"
        else:
            # Define month mapping
            month_map = {
                'january': 1, 'jan': 1,
                'february': 2, 'feb': 2,
                'march': 3, 'mar': 3,
                'april': 4, 'apr': 4,
                'may': 5,
                'june': 6, 'jun': 6,
                'july': 7, 'jul': 7,
                'august': 8, 'aug': 8,
                'september': 9, 'sep': 9, 'sept': 9,
                'october': 10, 'oct': 10,
                'november': 11, 'nov': 11,
                'december': 12, 'dec': 12
            }
            
            # Try to find any month name in the string
            for month_name, month_num in month_map.items():
                if month_name.lower() in date_str.lower():
                    month = month_num
                    logger.info(f"Found month '{month_name}' -> {month}")
                    
                    # Now find the day number that follows the month
                    month_pos = date_str.lower().find(month_name.lower())
                    after_month = date_str[month_pos + len(month_name):]
                    
                    # Find day after month
                    day_match = re.search(r'\b(\d{1,2})\b', after_month)
                    if day_match:
                        day = int(day_match.group(1))
                        logger.info(f"Found day: {day}")
                    else:
                        # If day is not after month, try to find any number in the string
                        day_match = re.search(r'\b(\d{1,2})\b', date_str)
                        if day_match:
                            day = int(day_match.group(1))
                            logger.info(f"Found day anywhere in string: {day}")
                    
                    break
        
        # If we have month and day but no year, determine year based on football season
        if month is not None and day is not None:
            if year is None:
                # For college football:
                # Spring games (April) are in the next calendar year
                # August-December games are in the current football season year
                # January games (bowl games) are in the next calendar year
                if month == 4:  # April (spring game)
                    year = football_season_year + 1
                elif month >= 8:  # August-December
                    year = football_season_year
                else:  # January-July (except April)
                    year = football_season_year + 1
                
                logger.info(f"Determined year {year} based on month {month} and football season")
            
            # Parse time (if available)
            if time_str and time_str.lower() not in ["tba", "tbd"]:
                # Handle AM/PM
                time_str = time_str.strip().upper()
                
                # Extract the time part if there's additional text
                time_match = re.search(r'(\d+:?\d*\s*[AP]M)', time_str)
                if time_match:
                    time_str = time_match.group(1)
                
                if ":" in time_str:
                    time_parts = time_str.replace("AM", "").replace("PM", "").strip().split(':')
                    hour = int(time_parts[0])
                    minute = int(time_parts[1]) if len(time_parts) > 1 else 0
                else:
                    # Handle cases where time might just be "12 PM" without colon
                    hour_match = re.search(r'(\d+)\s*[AP]M', time_str)
                    if hour_match:
                        hour = int(hour_match.group(1))
                        minute = 0
                    else:
                        hour, minute = 13, 0  # Default to 1 PM
                
                # Adjust for PM
                if "PM" in time_str and hour < 12:
                    hour += 12
                # Adjust for 12 AM
                if "AM" in time_str and hour == 12:
                    hour = 0
            else:
                # If time is TBA or TBD, use 1 PM ET (13:00 local)
                hour, minute = 13, 0
            
            # Create the datetime object
            try:
                game_datetime = datetime.datetime(year, month, day, hour, minute)
                logger.info(f"Final parsed date and time: {game_datetime}")
                return game_datetime
            except ValueError as e:
                logger.error(f"Invalid date components: year={year}, month={month}, day={day}, hour={hour}, minute={minute}")
                logger.error(f"ValueError: {str(e)}")
                raise
        else:
            logger.warning(f"Failed to extract complete date from: {date_str}")
            raise ValueError(f"Could not parse date from: {date_str}")
    
    except Exception as e:
        logger.error(f"Error parsing date/time: {date_str}, {time_str} - {str(e)}")
        # Return None to indicate parsing failure, caller should handle this
        return None

def scrape_schedule():
    """Scrape the Penn State football schedule and return game details"""
    logger.info("Starting schedule scraping...")
    games = []
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(BASE_URL, headers=headers)
        response.raise_for_status()
        
        logger.info(f"Successfully fetched page. Status code: {response.status_code}")
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Try multiple possible schedule containers and item selectors
        schedule_items = []
        
        # Common container class names for schedule items
        container_selectors = [
            '.sidearm-schedule-games-container',
            '.schedule-content-wrapper',
            '.s-game-listing',
            'table.schedule',
            '.schedule__content',
            '.schedule-table',
            'ul.schedule-list'
        ]
        
        # First attempt to find the main container
        main_container = None
        for selector in container_selectors:
            main_container = soup.select_one(selector)
            if main_container:
                logger.info(f"Found main container with selector: {selector}")
                break
        
        # If we found a container, find the game items within it
        if main_container:
            item_selectors = [
                '.sidearm-schedule-game',
                '.schedule-game',
                '.event-item',
                'tr.event-row',
                'li.schedule-item',
                'div[class*="game"]',
                'div[class*="event"]'
            ]
            
            for selector in item_selectors:
                items = main_container.select(selector)
                if items:
                    logger.info(f"Found {len(items)} items using selector: {selector}")
                    schedule_items = items
                    break
        
        # If we still don't have items, try to find them directly in the whole page
        if not schedule_items:
            all_selectors = [
                '.sidearm-schedule-game',
                '.s-game',
                '.event-row',
                '.schedule-card',
                'tr[data-day]',
                'div[class*="game"], div[class*="event"], div[class*="contest"]'
            ]
            
            for selector in all_selectors:
                items = soup.select(selector)
                if items:
                    logger.info(f"Found {len(items)} items using direct selector: {selector}")
                    schedule_items = items
                    break
        
        # If we found schedule items, process them
        if schedule_items:
            logger.info(f"Processing {len(schedule_items)} schedule items")
            
            for item in schedule_items:
                try:
                    # Dump the HTML of the first few items for debugging
                    if len(games) < 2:
                        logger.info(f"Sample item HTML: {item}")
                    
                    # Extract date - using improved selectors
                    date_str = ""
                    date_selectors = [
                        '.date, [data-date], [class*="date"], time, [datetime]',
                        'span[class*="date"], div[class*="date"]',
                        'time[datetime]',
                        '.event-date, .game-date, .contest-date'
                    ]
                    
                    for selector in date_selectors:
                        date_elems = item.select(selector)
                        if date_elems:
                            # Try each element to find one with actual content
                            for date_elem in date_elems:
                                if date_elem.text.strip():
                                    date_str = date_elem.text.strip()
                                    logger.info(f"Found date: {date_str}")
                                    break
                            if date_str:
                                break
                    
                    # Look for datetime attribute
                    if not date_str:
                        datetime_elem = item.select_one('[datetime]')
                        if datetime_elem:
                            date_str = datetime_elem.get('datetime')
                            logger.info(f"Found date from datetime attribute: {date_str}")
                    
                    # If we still don't have a date, try to extract from text
                    if not date_str:
                        # Try to find date pattern in item text
                        item_text = ' '.join(item.stripped_strings)
                        date_patterns = [
                            r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4}\b',
                            r'\b\d{1,2}/\d{1,2}/\d{2,4}\b',
                            r'\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}\b'
                        ]
                        
                        for pattern in date_patterns:
                            match = re.search(pattern, item_text)
                            if match:
                                date_str = match.group(0)
                                logger.info(f"Extracted date from text: {date_str}")
                                break
                    # Around line 374 in the original code, after determining the game duration
                    # and right before creating the game_info dictionary:

                    
                    # Skip if no date found
                    if not date_str:
                        logger.warning("No date found, skipping this item")
                        continue
                    
                    # Extract time
                    time_str = ""
                    time_selectors = [
                        '.time, [data-time], [class*="time"]',
                        'span[class*="time"], div[class*="time"]',
                        '.event-time, .game-time'
                    ]
                    
                    for selector in time_selectors:
                        time_elems = item.select(selector)
                        if time_elems:
                            # Try each element to find one with actual content
                            for time_elem in time_elems:
                                if time_elem.text.strip():
                                    time_str = time_elem.text.strip()
                                    logger.info(f"Found time: {time_str}")
                                    break
                            if time_str:
                                break
                    
                    # If no time found, try to extract from text
                    if not time_str:
                        item_text = ' '.join(item.stripped_strings)
                        time_patterns = [
                            r'\b\d{1,2}:\d{2}\s*[AP]M\b',
                            r'\b\d{1,2}\s*[AP]M\b',
                            r'\bTBA\b',
                            r'\bTBD\b'
                        ]
                        
                        for pattern in time_patterns:
                            match = re.search(pattern, item_text, re.IGNORECASE)
                            if match:
                                time_str = match.group(0)
                                logger.info(f"Extracted time from text: {time_str}")
                                break
                    
                    # If still no time, default to TBA
                    if not time_str:
                        time_str = "TBA"
                        logger.info("No time found, defaulting to TBA")
                    
                    # Extract opponent
                    opponent = "Unknown Opponent"
                    opponent_selectors = [
                        '.opponent, [data-opponent], [class*="opponent"]',
                        '.team-name, [class*="team-name"]',
                        '.event-opponent, .game-opponent',
                        'a[href*="team"]'
                    ]
                    
                    for selector in opponent_selectors:
                        opponent_elems = item.select(selector)
                        if opponent_elems:
                            for opponent_elem in opponent_elems:
                                if opponent_elem.text.strip():
                                    opponent = opponent_elem.text.strip()
                                    logger.info(f"Found opponent: {opponent}")
                                    break
                            if opponent != "Unknown Opponent":
                                break
                    
                    # If no opponent found, try to extract from text
                    if opponent == "Unknown Opponent":
                        item_text = ' '.join(item.stripped_strings)
                        opponent_patterns = [
                            r'(?:vs\.?|versus)\s+([A-Za-z\s&\.\']+)(?:\s|$)',
                            r'(?:at|@)\s+([A-Za-z\s&\.\']+)(?:\s|$)',
                            r'(?:vs\.?|versus|at|@)\s+(#\d+\s*[A-Za-z\s&\.\']+)(?:\s|$)'
                        ]
                        
                        for pattern in opponent_patterns:
                            match = re.search(pattern, item_text)
                            if match:
                                opponent = match.group(1).strip()
                                logger.info(f"Extracted opponent from text: {opponent}")
                                break
                    
                    # Determine if home or away
                    is_home = True
                    location = ""
                    
                    # Look for location information
                    location_selectors = [
                        '.location, [data-location], [class*="location"]',
                        '.venue, [class*="venue"]',
                        '.event-location, .game-location'
                    ]
                    
                    for selector in location_selectors:
                        location_elems = item.select(selector)
                        if location_elems:
                            for location_elem in location_elems:
                                if location_elem.text.strip():
                                    location = location_elem.text.strip()
                                    logger.info(f"Found location: {location}")
                                    break
                            if location:
                                break
                    
                    # Try to determine home/away status
                    if location:
                        is_home = any(term in location.lower() for term in ["beaver stadium", "university park", "home", "penn state"])
                    else:
                        # Check item text for location indicators
                        item_text = ' '.join(item.stripped_strings)
                        if "at " in item_text.lower() or "@ " in item_text.lower():
                            is_home = False
                        elif "vs" in item_text.lower() or "vs." in item_text.lower():
                            is_home = True
                    
                    # Clean up opponent name
                    opponent = re.sub(r'^(?:vs\.?|versus|at|@)\s+', '', opponent).strip()
                    
                    # Extract broadcast info
                    broadcast = ""
                    broadcast_selectors = [
                        '.network, [data-network], [class*="network"]',
                        '.broadcast, [class*="broadcast"], [class*="tv"]',
                        '.event-network, .game-network'
                    ]
                    
                    for selector in broadcast_selectors:
                        broadcast_elems = item.select(selector)
                        if broadcast_elems:
                            for broadcast_elem in broadcast_elems:
                                if broadcast_elem.text.strip():
                                    broadcast = broadcast_elem.text.strip()
                                    logger.info(f"Found broadcast: {broadcast}")
                                    break
                            if broadcast:
                                break
                    
                    # If no broadcast info found, check if time is TBA
                    if not broadcast and time_str and time_str.lower() in ["tba", "tbd"]:
                        broadcast = "TBA"
                        logger.info("Time is TBA, setting broadcast to TBA")
                    
                    # Create game title
                    if is_home:
                        title = f"{opponent} at Penn State"
                    else:
                        title = f"Penn State at {opponent}"
                    
                    # Get datetime object
                    game_datetime = parse_date_time(date_str, time_str)
                    
                    # Game duration (3.5 hours)
                    duration = datetime.timedelta(hours=3, minutes=30)
                    
                    # Don't add the game if essential information is missing
                    if not opponent or opponent == "Unknown Opponent":
                        logger.warning(f"Skipping game with missing opponent on {date_str}")
                        continue

                    if not location:
                        logger.warning(f"Skipping game against {opponent} with missing location")
                        continue

                    # Add the date check as well
                    if not date_str:
                        logger.warning(f"Skipping game against {opponent} with missing date")
                        continue

                    # Now we can be sure we have the required data
                    game_info = {
                        'title': title,
                        'start': game_datetime,
                        'end': game_datetime + duration,
                        'location': location,
                        'broadcast': broadcast,
                        'is_home': is_home,
                        'opponent': opponent,
                        'date_str': date_str,
                        'time_str': time_str
                    }

                    games.append(game_info)
                    logger.info(f"Added game: {title} on {game_datetime}")

                    # Create game info
                    game_info = {
                        'title': title,
                        'start': game_datetime,
                        'end': game_datetime + duration,
                        'location': location,
                        'broadcast': broadcast,
                        'is_home': is_home,
                        'opponent': opponent,
                        'date_str': date_str,
                        'time_str': time_str
                    }
                    
                    games.append(game_info)
                    logger.info(f"Added game: {title} on {game_datetime}")
                
                except Exception as e:
                    logger.error(f"Error processing game item: {str(e)}")
                    continue
        else:
            logger.error("No schedule items found on the page")
            
            # Look for dates in the page text as a fallback
            page_text = soup.get_text()
            
            # Process known 2025 schedule from extracted data
            logger.info("Attempting to create schedule from known 2025 games")
            
            # Known 2025 Penn State football schedule
            known_games = [
                {"date": "Aug 30, 2025", "opponent": "Nevada", "is_home": True, "location": "University Park, Pa. / West Shore Home Field at Beaver Stadium"},
                {"date": "Sep 6, 2025", "opponent": "FIU", "is_home": True, "location": "University Park, Pa. / West Shore Home Field at Beaver Stadium"},
                {"date": "Sep 13, 2025", "opponent": "Villanova", "is_home": True, "location": "University Park, Pa. / West Shore Home Field at Beaver Stadium"},
                {"date": "Sep 27, 2025", "opponent": "Oregon", "is_home": True, "location": "University Park, Pa. / West Shore Home Field at Beaver Stadium"},
                {"date": "Oct 4, 2025", "opponent": "UCLA", "is_home": False, "location": "Los Angeles, Calif."},
                {"date": "Oct 11, 2025", "opponent": "Northwestern", "is_home": True, "location": "University Park, Pa. / West Shore Home Field at Beaver Stadium"},
                {"date": "Oct 18, 2025", "opponent": "Iowa", "is_home": False, "location": "Iowa City, Iowa"},
                {"date": "Nov 1, 2025", "opponent": "Ohio State", "is_home": False, "location": "Columbus, Ohio"},
                {"date": "Nov 8, 2025", "opponent": "Indiana", "is_home": True, "location": "University Park, Pa. / West Shore Home Field at Beaver Stadium"},
                {"date": "Nov 15, 2025", "opponent": "Michigan State", "is_home": False, "location": "East Lansing, Mich."},
                {"date": "Nov 22, 2025", "opponent": "Nebraska", "is_home": True, "location": "University Park, Pa. / West Shore Home Field at Beaver Stadium"},
                {"date": "Nov 29, 2025", "opponent": "Rutgers", "is_home": False, "location": "Piscataway, N.J."}
            ]
            
            # Add special game - Blue-White Game
            known_games.append({
                "date": "Apr 26, 2025", 
                "opponent": "Blue-White Game", 
                "is_home": True, 
                "location": "University Park, Pa. / West Shore Home Field at Beaver Stadium"
            })
            
            # Process the known games
            for game in known_games:
                try:
                    date_str = game["date"]
                    opponent = game["opponent"]
                    is_home = game["is_home"]
                    location = game.get("location", "")
                    time_str = "TBA"  # All games initially set as TBA
                    broadcast = "TBA"
                    
                    # Create title
                    if "Blue-White" in opponent:
                        title = opponent  # Special case for Blue-White game
                    elif is_home:
                        title = f"{opponent} at Penn State"
                    else:
                        title = f"Penn State at {opponent}"
                    
                    # Parse date/time
                    game_datetime = parse_date_time(date_str, time_str)
                    
                    # Game duration
                    duration = datetime.timedelta(hours=3, minutes=30)
                    
                    # Create game info
                    game_info = {
                        'title': title,
                        'start': game_datetime,
                        'end': game_datetime + duration,
                        'location': location,
                        'broadcast': broadcast,
                        'is_home': is_home,
                        'opponent': opponent,
                        'date_str': date_str,
                        'time_str': time_str
                    }
                    
                    games.append(game_info)
                    logger.info(f"Added known game: {title} on {game_datetime}")
                
                except Exception as e:
                    logger.error(f"Error processing known game: {str(e)}")
                    continue
    
    except Exception as e:
        logger.error(f"Error scraping schedule: {str(e)}")
    
    deduplicated_games = []
    seen_games = set()
    
    for game in games:
        # Create a unique identifier based on date and opponent
        game_id = f"{game['start'].date()}_{game['opponent']}"
        
        if game_id not in seen_games:
            seen_games.add(game_id)
            deduplicated_games.append(game)
            logger.info(f"Keeping unique game: {game['title']} on {game['start'].date()}")
        else:
            logger.info(f"Skipping duplicate game: {game['title']} on {game['start'].date()}")
    
    logger.info(f"Deduplicated from {len(games)} to {len(deduplicated_games)} games")
    return deduplicated_games
    # Log final game count
    #logger.info(f"Total games found: {len(games)}")
    #return games

def create_calendar(games):
    """Create an iCalendar file from the scraped games"""
    cal = Calendar()
    valid_games = 0
    skipped_games = 0
    
    for game in games:
        # Verify that we have all required data before creating an event
        if (game['opponent'] and game['opponent'] != "Unknown Opponent" and
            game['location'] and 
            game['start']):
            
            event = Event()
            event.name = game['title']
            event.begin = game['start']
            event.end = game['end']
            event.location = game['location']
            
            # Add broadcast info to description
            description = ""
            if game['broadcast']:
                description += f"Broadcast on: {game['broadcast']}\n"
            
            # Add home/away info
            if game['is_home']:
                description += "Home Game"
            else:
                description += "Away Game"
                
            event.description = description
            cal.events.add(event)
            valid_games += 1
            logger.info(f"Added event to calendar: {game['title']} on {game['start']}")
        else:
            # Log games that were skipped due to missing data
            skipped_games += 1
            missing = []
            if not game['opponent'] or game['opponent'] == "Unknown Opponent":
                missing.append("opponent")
            if not game['location']:
                missing.append("location")
            if not game['start']:
                missing.append("date/time")
            
            logger.warning(f"Skipped game due to missing {', '.join(missing)}: {game.get('title', 'Unknown')}")
    
    # Save to file using the serialize() method instead of str()
    with open(CALENDAR_FILE, 'w') as f:
        f.write(cal.serialize())
    
    logger.info(f"Calendar created with {valid_games} events (skipped {skipped_games} incomplete entries)")
    return cal

def update_calendar():
    """Update the football calendar"""
    try:
        games = scrape_schedule()
        create_calendar(games)
        logger.info("Calendar updated successfully")
    except Exception as e:
        logger.error(f"Error updating calendar: {str(e)}")

@app.route('/calendar.ics')
def serve_calendar():
    """Serve the calendar file"""
    try:
        with open(CALENDAR_FILE, 'r') as f:
            cal_content = f.read()
        return Response(cal_content, mimetype='text/calendar')
    except Exception as e:
        logger.error(f"Error serving calendar: {str(e)}")
        return "Calendar not available", 500

@app.route('/')
def index():
    """Simple landing page"""
    return """
    <html>
        <head>
            <title>Penn State Football Calendar</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    max-width: 800px;
                    margin: 0 auto;
                    padding: 20px;
                    line-height: 1.6;
                }
                h1 {
                    color: #041E42; /* Penn State Navy */
                }
                .container {
                    border: 1px solid #ddd;
                    padding: 20px;
                    border-radius: 5px;
                    background-color: #f9f9f9;
                }
                pre {
                    background-color: #eee;
                    padding: 10px;
                    border-radius: 5px;
                    overflow-x: auto;
                }
                a {
                    color: #041E42;
                    text-decoration: none;
                }
                a:hover {
                    text-decoration: underline;
                }
                .footer {
                    margin-top: 30px;
                    font-size: 0.8em;
                    color: #777;
                }
            </style>
        </head>
        <body>
            <h1>Penn State Football Calendar</h1>
            <div class="container">
                <p>This calendar provides a schedule of Penn State Football games that you can add to your calendar app.</p>
                <p>To subscribe to this calendar in your calendar app, use this URL:</p>
                <pre>http://YOUR_SERVER_URL/calendar.ics</pre>
                <p><a href="/calendar.ics">Download Calendar</a></p>
                <p>The calendar updates daily with the latest game information from the Penn State Athletics website.</p>
            </div>
            <div class="footer">
                <p>Data sourced from gopsusports.com. Updated daily.</p>
                <p>This service is not affiliated with Penn State University.</p>
            </div>
        </body>
    </html>
    """
    
@app.route('/debug')
def debug_info():
    """Show debugging information about scraped games"""
    try:
        games = scrape_schedule()
        return Response(
            '<html><head><title>Debug Info</title>'
            '<style>'
            'body { font-family: Arial, sans-serif; padding: 20px; }'
            'table { border-collapse: collapse; width: 100%; }'
            'th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }'
            'tr:nth-child(even) { background-color: #f2f2f2; }'
            'th { background-color: #041E42; color: white; }'
            'h1 { color: #041E42; }'
            '</style>'
            '</head><body>'
            '<h1>Penn State Football Schedule - Debug Info</h1>'
            '<p>This page shows the raw data extracted from the Penn State football schedule website.</p>'
            '<table>'
            '<tr><th>Game</th><th>Date</th><th>Time</th><th>Location</th><th>Broadcast</th></tr>'
            + ''.join([
                f'<tr><td>{g["title"]}</td><td>{g["date_str"]}</td><td>{g["time_str"]}</td>'
                f'<td>{g["location"]}</td><td>{g["broadcast"]}</td></tr>'
                for g in games
            ])
            + '</table>'
            '<p>Total games found: ' + str(len(games)) + '</p>'
            '</body></html>',
            mimetype='text/html'
        )
    except Exception as e:
        return f"Error: {str(e)}", 500

if __name__ == "__main__":
    # Create scheduler for daily updates
    scheduler = BackgroundScheduler()
    
    # Initial calendar creation
    update_calendar()
    
    # Schedule daily updates at 3 AM
    scheduler.add_job(update_calendar, 'cron', hour=3)
    scheduler.start()
    
    # Run the Flask app
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)