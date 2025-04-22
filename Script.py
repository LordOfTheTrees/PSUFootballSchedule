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

# Base URL for Penn State football - This is the correct, updated URL
BASE_URL = "https://gopsusports.com/sports/football/schedule/"
CALENDAR_FILE = "penn_state_football.ics"

def parse_date_time(date_str, time_str):
    """Parse date and time strings into datetime object"""
    try:
        logger.info(f"Parsing date: '{date_str}', time: '{time_str}'")
        
        # Handle various date formats
        if "/" in date_str:  # Format: MM/DD/YYYY
            date_parts = date_str.strip().split('/')
            month, day, year = int(date_parts[0]), int(date_parts[1]), int(date_parts[2])
        else:  # Format: Month Day, Year (e.g., "August 31, 2024")
            # Remove any day of week prefix if present
            if "," in date_str:
                date_str = date_str.split(",", 1)[1].strip() if date_str.count(",") > 1 else date_str
            
            # Clean and parse the date string
            date_str = re.sub(r'\s+', ' ', date_str.strip())
            date_parts = date_str.replace(",", "").split()
            
            if len(date_parts) >= 3:
                month_str, day_str, year_str = date_parts[0], date_parts[1], date_parts[2]
                
                # Convert month name to number
                month_dict = {
                    'Jan': 1, 'January': 1, 
                    'Feb': 2, 'February': 2, 
                    'Mar': 3, 'March': 3, 
                    'Apr': 4, 'April': 4, 
                    'May': 5, 
                    'Jun': 6, 'June': 6, 
                    'Jul': 7, 'July': 7, 
                    'Aug': 8, 'August': 8, 
                    'Sep': 9, 'Sept': 9, 'September': 9, 
                    'Oct': 10, 'October': 10, 
                    'Nov': 11, 'November': 11, 
                    'Dec': 12, 'December': 12
                }
                
                month = month_dict.get(month_str, None)
                if month is None:
                    # Try getting the month by the first 3 letters
                    month = month_dict.get(month_str[:3], 1)
                
                day = int(day_str)
                year = int(year_str)
            else:
                logger.warning(f"Unexpected date format: {date_str}, using default")
                # Default to a future date if we can't parse
                now = datetime.datetime.now()
                month, day, year = now.month, now.day, now.year
        
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
                    hour, minute = 12, 0  # Default
            
            # Adjust for PM
            if "PM" in time_str and hour < 12:
                hour += 12
            # Adjust for 12 AM
            if "AM" in time_str and hour == 12:
                hour = 0
                
            return datetime.datetime(year, month, day, hour, minute)
        else:
            # If no time is provided, use noon as default
            return datetime.datetime(year, month, day, 12, 0)
    except Exception as e:
        logger.error(f"Error parsing date/time: {date_str}, {time_str} - {str(e)}")
        # Return a placeholder date in the future
        return datetime.datetime.now() + datetime.timedelta(days=30)

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
        
        # Attempt 1: Modern Sidearm schedule format
        items = soup.select('.sidearm-schedule-games-container .sidearm-schedule-game')
        if items:
            logger.info(f"Found {len(items)} items using .sidearm-schedule-games-container selector")
            schedule_items = items
        
        # Attempt 2: Look for event rows or schedule cards
        if not schedule_items:
            items = soup.select('.event-row, .schedule-card')
            if items:
                logger.info(f"Found {len(items)} items using .event-row or .schedule-card selectors")
                schedule_items = items
        
        # Attempt 3: Look for sports_schedule content
        if not schedule_items:
            items = soup.select('.s-game, .sports_schedule .contest')
            if items:
                logger.info(f"Found {len(items)} items using sports_schedule selectors")
                schedule_items = items
        
        # Attempt 4: Look for table rows in schedule tables
        if not schedule_items:
            tables = soup.select('table.schedule_table, table.schedule')
            if tables:
                for table in tables:
                    rows = table.select('tr[data-day]') or table.select('tr')
                    if rows:
                        logger.info(f"Found {len(rows)} rows in schedule table")
                        schedule_items.extend(rows)
                        
        # Attempt 5: Look for main content div with schedule information
        if not schedule_items:
            main_content = soup.select_one('.main-content, #main-content')
            if main_content:
                # Find elements that look like game rows/cards
                game_divs = main_content.select('div[class*="game"], div[class*="event"], div[class*="contest"]')
                if game_divs:
                    logger.info(f"Found {len(game_divs)} potential game divs in main content")
                    schedule_items = game_divs
        
        # Fallback approach - find any div with game or event in the class name
        if not schedule_items:
            all_possible_items = soup.select('[class*="game"], [class*="event"], [class*="contest"]')
            if all_possible_items:
                logger.info(f"Fallback: Found {len(all_possible_items)} potential game/event elements")
                schedule_items = all_possible_items
        
        # If we found items through any method, process them
        if schedule_items:
            logger.info(f"Processing {len(schedule_items)} schedule items")
            
            for item in schedule_items:
                try:
                    # Dump the HTML of the first few items for debugging
                    if len(games) < 2:
                        logger.info(f"Sample item HTML: {item}")
                        
                    # Extract date - try multiple possible selectors
                    date_elem = None
                    date_selectors = [
                        '.date, [data-date], [class*="date"], [id*="date"]',
                        'span[class*="date"], div[class*="date"]',
                        'time, [datetime]',
                        '.event-date, .game-date, .contest-date'
                    ]
                    
                    for selector in date_selectors:
                        date_elem = item.select_one(selector)
                        if date_elem:
                            break
                    
                    # If we still don't have a date element, try text content
                    if not date_elem:
                        # Look for text that matches date patterns
                        text_nodes = [t for t in item.stripped_strings]
                        date_patterns = [
                            r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4}\b',
                            r'\b\d{1,2}/\d{1,2}/\d{2,4}\b'
                        ]
                        
                        for text in text_nodes:
                            for pattern in date_patterns:
                                match = re.search(pattern, text)
                                if match:
                                    date_str = match.group(0)
                                    logger.info(f"Found date in text: {date_str}")
                                    # Create a simple element to hold this text
                                    date_elem = type('obj', (object,), {'text': date_str})
                                    break
                            if date_elem:
                                break
                    
                    # Get the date string
                    date_str = date_elem.text.strip() if date_elem else ""
                    if not date_str and date_elem and date_elem.get('datetime'):
                        date_str = date_elem.get('datetime')
                        
                    # Even if we don't find a date element, try to extract from item's text
                    if not date_str:
                        # Try to find a date-formatted string in the item text
                        item_text = ' '.join(item.stripped_strings)
                        date_matches = re.findall(r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b', item_text)
                        if date_matches:
                            date_str = date_matches[0]
                            logger.info(f"Extracted date from text: {date_str}")
                    
                    # Extract time - try multiple possible selectors
                    time_elem = None
                    time_selectors = [
                        '.time, [data-time], [class*="time"], [id*="time"]',
                        'span[class*="time"], div[class*="time"]',
                        '.event-time, .game-time, .contest-time'
                    ]
                    
                    for selector in time_selectors:
                        time_elem = item.select_one(selector)
                        if time_elem:
                            break
                    
                    # Try to find time in text if no element
                    time_str = time_elem.text.strip() if time_elem else ""
                    if not time_str:
                        # Try to find time pattern in the item text
                        item_text = ' '.join(item.stripped_strings)
                        time_matches = re.findall(r'\b\d{1,2}:\d{2}\s*[AP]M\b|\b\d{1,2}\s*[AP]M\b', item_text, re.IGNORECASE)
                        if time_matches:
                            time_str = time_matches[0]
                            logger.info(f"Extracted time from text: {time_str}")
                    
                    # Extract opponent - try multiple possible selectors
                    opponent_elem = None
                    opponent_selectors = [
                        '.opponent, [data-opponent], [class*="opponent"], [id*="opponent"]',
                        '.team, [class*="team-name"]',
                        '.event-opponent, .game-opponent, .contest-opponent',
                        'a[href*="opponent"], a[href*="team"]',
                        'span[class*="opponent"], div[class*="opponent"]'
                    ]
                    
                    for selector in opponent_selectors:
                        opponent_elem = item.select_one(selector)
                        if opponent_elem:
                            break
                    
                    # If we don't have an opponent element, look for text patterns
                    opponent = opponent_elem.text.strip() if opponent_elem else "Unknown Opponent"
                    if opponent == "Unknown Opponent":
                        # Try to extract team names
                        item_text = ' '.join(item.stripped_strings)
                        # Common opponent patterns like "vs Team Name" or "at Team Name"
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
                    
                    # Determine if home or away - try from location or opponent text
                    location_elem = None
                    location_selectors = [
                        '.location, [data-location], [class*="location"], [id*="location"]',
                        '.venue, [class*="venue"]',
                        '.event-location, .game-location, .contest-location',
                        'span[class*="location"], div[class*="location"]'
                    ]
                    
                    for selector in location_selectors:
                        location_elem = item.select_one(selector)
                        if location_elem:
                            break
                    
                    location = location_elem.text.strip() if location_elem else ""
                    
                    # Determine home/away status
                    is_home = False
                    
                    # Check location text
                    if location:
                        is_home = any(term in location.lower() for term in ["beaver stadium", "university park", "home", "penn state"])
                    
                    # Check class name
                    if not is_home:
                        is_home = "home" in item.get('class', [])
                    
                    # Check opponent text (contains "vs" or doesn't contain "at")
                    if not is_home:
                        is_home = (
                            ("vs" in opponent.lower() or "vs." in opponent.lower()) or
                            not any(term in opponent.lower() for term in ["at ", "@ "])
                        )
                    
                    # Check if the text of the item contains home indicators
                    if not is_home:
                        item_text = ' '.join(item.stripped_strings)
                        is_home = (
                            "home" in item_text.lower() or
                            "beaver stadium" in item_text.lower() or
                            "university park" in item_text.lower()
                        )
                    
                    # Clean up opponent name (remove "vs " or "at " prefix if present)
                    opponent = re.sub(r'^(?:vs\.?|versus|at|@)\s+', '', opponent).strip()
                    
                    # Extract broadcast info
                    broadcast_elem = None
                    broadcast_selectors = [
                        '.network, [data-network], [class*="network"], [id*="network"]',
                        '.broadcast, [class*="broadcast"], [class*="tv"]',
                        '.event-network, .game-network',
                        'span[class*="network"], div[class*="network"]'
                    ]
                    
                    for selector in broadcast_selectors:
                        broadcast_elem = item.select_one(selector)
                        if broadcast_elem:
                            break
                    
                    broadcast = broadcast_elem.text.strip() if broadcast_elem else ""
                    
                    # If we have no broadcast info, try to find TV/network info in text
                    if not broadcast:
                        item_text = ' '.join(item.stripped_strings)
                        network_patterns = [
                            r'(?:on|Live on|Watch on)\s+([A-Z0-9/&]+)',
                            r'(?:ESPN|FOX|CBS|NBC|ABC|BTN|FS1)'
                        ]
                        
                        for pattern in network_patterns:
                            match = re.search(pattern, item_text)
                            if match and len(match.groups()) > 0:
                                broadcast = match.group(1).strip()
                                logger.info(f"Extracted broadcast from text: {broadcast}")
                                break
                            elif match:
                                broadcast = match.group(0).strip()
                                logger.info(f"Extracted broadcast from text: {broadcast}")
                                break
                    
                    # Create readable title based on home/away status
                    if is_home:
                        title = f"{opponent} at Penn State"
                    else:
                        title = f"Penn State at {opponent}"
                    
                    # Skip items without date information
                    if not date_str:
                        logger.warning(f"Skipping item without date information: {title}")
                        continue
                    
                    # Get datetime object
                    game_datetime = parse_date_time(date_str, time_str)
                    
                    # Game duration (default 3.5 hours)
                    duration = datetime.timedelta(hours=3, minutes=30)
                    
                    # Create the game info dictionary
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
                    logger.info(f"Scraped game: {title} on {game_datetime}")
                    
                except Exception as e:
                    logger.error(f"Error parsing game item: {str(e)}")
                    continue
        else:
            logger.error("No schedule items found on the page")
            
            # Attempt to parse the page's entire text as a fallback
            logger.info("Attempting to extract schedule from page text")
            page_text = soup.get_text()
            
            # Save some of the page text for debugging
            logger.info(f"Sample page text: {page_text[:500]}")
            
            # Look for football schedule patterns
            schedule_section = re.search(r'(?:Football Schedule|Schedule|FOOTBALL SCHEDULE).{0,200}(?:20\d\d|SEASON)', page_text, re.IGNORECASE)
            if schedule_section:
                section_start = max(0, schedule_section.start() - 100)
                section_end = min(len(page_text), schedule_section.end() + 2000)
                schedule_text = page_text[section_start:section_end]
                
                # Look for date patterns
                date_matches = list(re.finditer(r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b', schedule_text))
                
                if date_matches:
                    logger.info(f"Found {len(date_matches)} potential dates in text")
                    
                    for i, match in enumerate(date_matches):
                        try:
                            date_str = match.group(0)
                            match_pos = match.start()
                            
                            # Look for time near this date
                            time_match = re.search(r'\b\d{1,2}:\d{2}\s*[AP]M\b|\b\d{1,2}\s*[AP]M\b', schedule_text[match_pos:match_pos+100], re.IGNORECASE)
                            time_str = time_match.group(0) if time_match else ""
                            
                            # Look for opponent info (100 chars before and after date)
                            context = schedule_text[max(0, match_pos-100):min(len(schedule_text), match_pos+200)]
                            
                            # Try to find opponent patterns
                            opponent_match = re.search(r'(?:vs\.?|versus)\s+([A-Za-z\s&\.\']+)(?:\s|$)|(?:at|@)\s+([A-Za-z\s&\.\']+)(?:\s|$)', context)
                            opponent = opponent_match.group(1) if opponent_match and opponent_match.group(1) else opponent_match.group(2) if opponent_match else "Unknown Opponent"
                            
                            # Determine if home/away
                            is_home = "vs" in context.lower() or "vs." in context.lower() or "home" in context.lower()
                            
                            # Look for location
                            location_match = re.search(r'(?:at|in)\s+([A-Za-z\s\.,]+)(?:\s|$)', context)
                            location = location_match.group(1) if location_match else ""
                            
                            # Look for broadcast info
                            broadcast_match = re.search(r'(?:on|Live on|Watch on)\s+([A-Z0-9/&]+)|(?:ESPN|FOX|CBS|NBC|ABC|BTN|FS1)', context)
                            broadcast = broadcast_match.group(1) if broadcast_match and len(broadcast_match.groups()) > 0 else broadcast_match.group(0) if broadcast_match else ""
                            
                            # Create title
                            if is_home:
                                title = f"{opponent} at Penn State"
                            else:
                                title = f"Penn State at {opponent}"
                            
                            # Get datetime
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
                            logger.info(f"Scraped game from text: {title} on {game_datetime}")
                            
                        except Exception as e:
                            logger.error(f"Error parsing game from text: {str(e)}")
                            continue
    
    except Exception as e:
        logger.error(f"Error scraping schedule: {str(e)}")
    
    logger.info(f"Scraped {len(games)} games")
    return games

def create_calendar(games):
    """Create an iCalendar file from the scraped games"""
    cal = Calendar()
    
    for game in games:
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
    
    # Save to file
    with open(CALENDAR_FILE, 'w') as f:
        f.write(str(cal))
    
    logger.info(f"Calendar created with {len(games)} events")
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
    </html
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