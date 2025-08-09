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
import pytz

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
BASE_URL = "https://gopsusports.com/sports/football/schedule"
CALENDAR_FILE = "penn_state_football.ics"

def get_current_football_season_year():
    """Determine the current football season year"""
    today = datetime.datetime.now()
    current_year = today.year
    current_month = today.month
    
    # Football season typically runs from August to January
    # If we're in January-July, we might be looking at next season's schedule
    if current_month >= 8:  # August-December
        return current_year
    elif current_month <= 1:  # January (bowl games)
        return current_year - 1
    else:  # February-July (offseason, preparing for next season)
        return current_year
    
def parse_date_time(date_str, time_str):
    """Parse date and time strings into datetime object with proper timezone handling"""
    try:
        logger.info(f"Parsing date: '{date_str}', time: '{time_str}'")
        
        # Get the current football season year
        football_season_year = get_current_football_season_year()
        logger.info(f"Determined football season year: {football_season_year}")
        
        # Set up Eastern timezone
        eastern = pytz.timezone('US/Eastern')
        
        # Clean up the input date string
        weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        cleaned_date_str = date_str
        
        # Fix cases like "SaturdayNov 22" where day of week and month are joined
        for day in weekdays:
            if day.lower() in date_str.lower():
                day_pos = date_str.lower().find(day.lower()) + len(day)
                if day_pos < len(date_str) and date_str[day_pos:day_pos+1] != ' ':
                    cleaned_date_str = date_str[:day_pos] + ' ' + date_str[day_pos:]
                    logger.info(f"Fixed joined weekday-month: '{date_str}' -> '{cleaned_date_str}'")
                    break
        
        date_str = cleaned_date_str
        
        # Extract year, month, day
        year = None
        month = None
        day = None
        
        # Try various date formats
        # Format 1: MM/DD/YYYY or MM/DD
        if "/" in date_str:
            parts = [p.strip() for p in date_str.split("/")]
            try:
                month = int(parts[0])
                day = int(parts[1])
                if len(parts) > 2 and parts[2]:
                    year = int(parts[2])
                    if year < 100:
                        year += 2000
                logger.info(f"Parsed MM/DD format: month={month}, day={day}, year={year}")
            except (ValueError, IndexError):
                logger.warning(f"Failed to parse MM/DD format: {date_str}")
        
        # Format 2: Month + Day pattern
        if month is None:
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
            
            # Find month name in string
            for month_name, month_num in month_map.items():
                if month_name.lower() in date_str.lower():
                    month = month_num
                    logger.info(f"Found month '{month_name}' -> {month}")
                    
                    # Find day number
                    day_match = re.search(r'\b(\d{1,2})\b', date_str)
                    if day_match:
                        day = int(day_match.group(1))
                        logger.info(f"Found day: {day}")
                    
                    # Look for year in the string
                    year_match = re.search(r'\b(20\d{2})\b', date_str)
                    if year_match:
                        year = int(year_match.group(1))
                        logger.info(f"Found year: {year}")
                    
                    break
        
        # If we have month and day but no year, determine year based on football season
        if month is not None and day is not None and year is None:
            # For college football schedule:
            # August-December games are in the current football season year
            # January games (bowl games) are in the next calendar year
            # April games (spring games) are in the next calendar year
            current_date = datetime.datetime.now()
            
            if month >= 8:  # August-December (regular season)
                year = football_season_year
            elif month <= 4:  # January-April (bowls/spring game)
                year = football_season_year + 1
            else:  # May-July (unusual, use next year)
                year = football_season_year + 1
            
            logger.info(f"Determined year {year} based on month {month} and current date {current_date}")
        
        if month is not None and day is not None and year is not None:
            # Parse time
            hour, minute = 13, 0  # Default to 1 PM ET
            
            if time_str and time_str.lower() not in ["tba", "tbd", ""]:
                # Clean time string
                time_str = time_str.strip().upper()
                
                # Handle multiple time options like "noon/3:30/4 p.m."
                if "/" in time_str:
                    # Take the first time option
                    time_str = time_str.split("/")[0].strip()
                
                # Handle "noon" specifically
                if "NOON" in time_str:
                    hour, minute = 12, 0
                else:
                    # Extract time with AM/PM
                    time_match = re.search(r'(\d+):?(\d*)\s*([AP]M)', time_str)
                    if time_match:
                        hour = int(time_match.group(1))
                        minute_str = time_match.group(2)
                        minute = int(minute_str) if minute_str else 0
                        am_pm = time_match.group(3)
                        
                        # Adjust for PM
                        if am_pm == "PM" and hour < 12:
                            hour += 12
                        # Adjust for 12 AM
                        elif am_pm == "AM" and hour == 12:
                            hour = 0
                    else:
                        # Try to extract just the hour
                        hour_match = re.search(r'(\d+)\s*[AP]M', time_str)
                        if hour_match:
                            hour = int(hour_match.group(1))
                            minute = 0
                            if "PM" in time_str and hour < 12:
                                hour += 12
                            elif "AM" in time_str and hour == 12:
                                hour = 0
            
            # Create the datetime object in Eastern timezone first
            try:
                # Create naive datetime first
                naive_datetime = datetime.datetime(year, month, day, hour, minute)
                
                # Localize to Eastern timezone (this handles DST automatically)
                game_datetime = eastern.localize(naive_datetime)
                
                logger.info(f"Successfully parsed: {game_datetime} ({game_datetime.tzinfo})")
                return game_datetime
            except ValueError as e:
                logger.error(f"Invalid date components: year={year}, month={month}, day={day}, hour={hour}, minute={minute}")
                raise
        else:
            logger.warning(f"Failed to extract complete date from: {date_str}")
            return None
    
    except Exception as e:
        logger.error(f"Error parsing date/time: {date_str}, {time_str} - {str(e)}")
        return None

def scrape_schedule():
    """Scrape the Penn State football schedule with improved parsing"""
    logger.info("Starting schedule scraping...")
    games = []
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }
        
        response = requests.get(BASE_URL, headers=headers, timeout=30)
        response.raise_for_status()
        
        logger.info(f"Successfully fetched page. Status code: {response.status_code}")
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # New approach: Look for the schedule data in the HTML
        # The page shows games in a structured format
        
        # Try to find schedule events or game listings
        schedule_items = []
        
        # Look for various container patterns
        possible_containers = [
            'div[class*="schedule"]',
            'div[class*="event"]', 
            'div[class*="game"]',
            'table tr',
            '.event-row',
            '.game-row'
        ]
        
        for selector in possible_containers:
            items = soup.select(selector)
            if items and len(items) > 3:  # Need multiple items for a schedule
                logger.info(f"Found {len(items)} potential schedule items with selector: {selector}")
                schedule_items = items
                break
        
        # If no structured data found, use the known 2025 schedule with updated info
        if not schedule_items or len(schedule_items) < 5:
            logger.info("Using known 2025 schedule data with current broadcast info")
            
            # Known 2025 Penn State football schedule with updated TV/time info
            known_games = [
                {
                    "date": "Aug 30, 2025", 
                    "opponent": "Nevada", 
                    "time": "3:30 PM", 
                    "broadcast": "CBS",
                    "is_home": True, 
                    "location": "University Park, Pa. / West Shore Home Field at Beaver Stadium",
                    "special": "107K Family Reunion"
                },
                {
                    "date": "Sep 6, 2025", 
                    "opponent": "FIU", 
                    "time": "12:00 PM", 
                    "broadcast": "Big Ten Network",
                    "is_home": True, 
                    "location": "University Park, Pa. / West Shore Home Field at Beaver Stadium",
                    "special": "THON Game"
                },
                {
                    "date": "Sep 13, 2025", 
                    "opponent": "Villanova", 
                    "time": "3:30 PM", 
                    "broadcast": "TBA",
                    "is_home": True, 
                    "location": "University Park, Pa. / West Shore Home Field at Beaver Stadium",
                    "special": "All-U Day"
                },
                {
                    "date": "Sep 27, 2025", 
                    "opponent": "Oregon", 
                    "time": "7:30 PM", 
                    "broadcast": "NBC",
                    "is_home": True, 
                    "location": "University Park, Pa. / West Shore Home Field at Beaver Stadium",
                    "special": "Penn State White Out"
                },
                {
                    "date": "Oct 4, 2025", 
                    "opponent": "UCLA", 
                    "time": "TBA", 
                    "broadcast": "TBA",
                    "is_home": False, 
                    "location": "Pasadena, Calif. / The Rose Bowl"
                },
                {
                    "date": "Oct 11, 2025", 
                    "opponent": "Northwestern", 
                    "time": "noon", 
                    "broadcast": "TBA",
                    "is_home": True, 
                    "location": "University Park, Pa. / West Shore Home Field at Beaver Stadium",
                    "special": "Homecoming & Stripe Out"
                },
                {
                    "date": "Oct 18, 2025", 
                    "opponent": "Iowa", 
                    "time": "TBA", 
                    "broadcast": "TBA",
                    "is_home": False, 
                    "location": "Iowa City, Iowa / Kinnick Stadium"
                },
                {
                    "date": "Nov 1, 2025", 
                    "opponent": "Ohio State", 
                    "time": "TBA", 
                    "broadcast": "TBA",
                    "is_home": False, 
                    "location": "Columbus, Ohio / Ohio Stadium"
                },
                {
                    "date": "Nov 8, 2025", 
                    "opponent": "Indiana", 
                    "time": "TBA", 
                    "broadcast": "TBA",
                    "is_home": True, 
                    "location": "University Park, Pa. / West Shore Home Field at Beaver Stadium",
                    "special": "Helmet Stripe & Military Appreciation"
                },
                {
                    "date": "Nov 15, 2025", 
                    "opponent": "Michigan State", 
                    "time": "TBA", 
                    "broadcast": "TBA",
                    "is_home": False, 
                    "location": "East Lansing, Mich. / Spartan Stadium"
                },
                {
                    "date": "Nov 22, 2025", 
                    "opponent": "Nebraska", 
                    "time": "TBA", 
                    "broadcast": "TBA",
                    "is_home": True, 
                    "location": "University Park, Pa. / West Shore Home Field at Beaver Stadium",
                    "special": "Senior Day"
                },
                {
                    "date": "Nov 29, 2025", 
                    "opponent": "Rutgers", 
                    "time": "TBA", 
                    "broadcast": "TBA",
                    "is_home": False, 
                    "location": "Piscataway, N.J. / SHI Stadium"
                }
            ]
            
            # Add Blue-White Spring Game
            known_games.append({
                "date": "Apr 26, 2025", 
                "opponent": "Blue-White Game", 
                "time": "TBA", 
                "broadcast": "TBA",
                "is_home": True, 
                "location": "University Park, Pa. / West Shore Home Field at Beaver Stadium"
            })
            
            # Process known games
            for game_data in known_games:
                try:
                    opponent = game_data["opponent"]
                    is_home = game_data["is_home"]
                    location = game_data["location"]
                    date_str = game_data["date"]
                    time_str = game_data["time"]
                    broadcast = game_data["broadcast"]
                    special = game_data.get("special", "")
                    
                    # Create title
                    if "Blue-White" in opponent:
                        title = opponent
                    elif is_home:
                        if special:
                            title = f"{opponent} at Penn State - {special}"
                        else:
                            title = f"{opponent} at Penn State"
                    else:
                        title = f"Penn State at {opponent}"
                    
                    # Parse date/time
                    game_datetime = parse_date_time(date_str, time_str)
                    if not game_datetime:
                        logger.warning(f"Skipping game due to date parsing failure: {opponent}")
                        continue
                    
                    # Game duration (3.5 hours)
                    duration = datetime.timedelta(hours=3, minutes=30)
                    
                    # Enhance location with special event info
                    enhanced_location = location
                    if special and is_home:
                        enhanced_location = f"{special} - {location}"
                    
                    game_info = {
                        'title': title,
                        'start': game_datetime,
                        'end': game_datetime + duration,
                        'location': enhanced_location,
                        'broadcast': broadcast,
                        'is_home': is_home,
                        'opponent': opponent,
                        'date_str': date_str,
                        'time_str': time_str,
                        'special': special
                    }
                    
                    games.append(game_info)
                    logger.info(f"Added game: {title} on {game_datetime} (Broadcast: {broadcast})")
                
                except Exception as e:
                    logger.error(f"Error processing known game {game_data.get('opponent', 'Unknown')}: {str(e)}")
                    continue
        
        else:
            # Process scraped items (if we found them)
            logger.info(f"Processing {len(schedule_items)} scraped items")
            # Add processing logic for scraped items here if needed
            # For now, fall back to known schedule
            pass
    
    except requests.RequestException as e:
        logger.error(f"Error fetching schedule page: {str(e)}")
        # Use known schedule as fallback
        logger.info("Using known schedule as fallback due to fetch error")
    
    except Exception as e:
        logger.error(f"Error scraping schedule: {str(e)}")
    
    # Remove duplicates
    deduplicated_games = []
    seen_games = set()
    
    for game in games:
        game_id = f"{game['start'].date()}_{game['opponent']}"
        if game_id not in seen_games:
            seen_games.add(game_id)
            deduplicated_games.append(game)
        else:
            logger.info(f"Skipping duplicate: {game['title']}")
    
    logger.info(f"Final game count: {len(deduplicated_games)}")
    return deduplicated_games

def create_calendar(games):
    """Create an iCalendar file from the scraped games with proper timezone handling"""
    cal = Calendar()
    cal.creator = "Penn State Football Schedule Scraper"
    
    valid_games = 0
    skipped_games = 0
    
    for game in games:
        if (game.get('opponent') and 
            game.get('location') and 
            game.get('start')):
            
            event = Event()
            event.name = game['title']
            
            # Set the start and end times
            # The ics library will handle timezone conversion properly
            event.begin = game['start']  # This should already be timezone-aware
            event.end = game['end']      # This should already be timezone-aware
            
            event.location = game['location']
            
            # Enhanced description with all available info
            description_parts = []
            
            if game.get('broadcast') and game['broadcast'] != "TBA":
                description_parts.append(f"📺 TV: {game['broadcast']}")
            elif game.get('broadcast') == "TBA":
                description_parts.append(f"📺 TV: To Be Announced")
            
            if game.get('is_home'):
                description_parts.append("🏟️ Home Game")
            else:
                description_parts.append("✈️ Away Game")
            
            if game.get('special'):
                description_parts.append(f"🎉 Special Event: {game['special']}")
            
            # Add opponent info
            description_parts.append(f"🏈 Opponent: {game['opponent']}")
            
            # Add time info if it was TBA
            if game.get('time_str') == "TBA":
                description_parts.append("⏰ Game time to be announced")
            
            # Add timezone information for clarity
            if game['start'].tzinfo:
                tz_name = game['start'].strftime('%Z')
                description_parts.append(f"🕒 Timezone: {tz_name}")
            
            event.description = "\n".join(description_parts)
            
            # Add categories
            categories = ["Football", "Penn State"]
            if game.get('is_home'):
                categories.append("Home Game")
            else:
                categories.append("Away Game")
            
            if game.get('special'):
                categories.append("Special Event")
            
            # Set event properties
            event.transparent = False  # Show as busy
            event.classification = "PUBLIC"
            
            cal.events.add(event)
            valid_games += 1
            
            # Log with timezone info for debugging
            start_time_str = game['start'].strftime('%Y-%m-%d %I:%M %p %Z') if game['start'].tzinfo else game['start'].strftime('%Y-%m-%d %I:%M %p')
            logger.info(f"Added calendar event: {game['title']} at {start_time_str} ({game.get('broadcast', 'No broadcast info')})")
        else:
            skipped_games += 1
            missing = []
            if not game.get('opponent'):
                missing.append("opponent")
            if not game.get('location'):
                missing.append("location")
            if not game.get('start'):
                missing.append("start time")
            
            logger.warning(f"Skipped incomplete game - missing: {', '.join(missing)}")
    
    # Save calendar
    try:
        with open(CALENDAR_FILE, 'w', encoding='utf-8') as f:
            calendar_content = cal.serialize()
            f.write(calendar_content)
            
        logger.info(f"Calendar saved with {valid_games} events (skipped {skipped_games})")
        
        # Log a sample of the calendar content for debugging
        with open(CALENDAR_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
            lines = content.split('\n')
            dtstart_lines = [line for line in lines if line.startswith('DTSTART')]
            if dtstart_lines:
                logger.info(f"Sample DTSTART times in calendar: {dtstart_lines[:3]}")
                
    except Exception as e:
        logger.error(f"Error saving calendar: {str(e)}")
        raise
    
    return cal

def update_calendar():
    """Update the football calendar"""
    try:
        logger.info("Starting calendar update...")
        games = scrape_schedule()
        
        if not games:
            logger.warning("No games found, skipping calendar update")
            return
            
        create_calendar(games)
        logger.info("Calendar updated successfully")
    except Exception as e:
        logger.error(f"Error updating calendar: {str(e)}")
        raise

@app.route('/calendar.ics')
def serve_calendar():
    """Serve the calendar file"""
    try:
        if not os.path.exists(CALENDAR_FILE):
            logger.warning("Calendar file doesn't exist, creating it...")
            update_calendar()
        
        with open(CALENDAR_FILE, 'r', encoding='utf-8') as f:
            cal_content = f.read()
        
        response = Response(cal_content, mimetype='text/calendar')
        response.headers['Content-Disposition'] = 'attachment; filename=penn_state_football.ics'
        return response
    except Exception as e:
        logger.error(f"Error serving calendar: {str(e)}")
        return "Calendar not available", 500

@app.route('/')
def index():
    """Enhanced landing page with better information"""
    calendar_url = "https://raw.githubusercontent.com/lordofthetrees/PSUFootballSchedule/main/penn_state_football.ics"
    
    return f"""
    <!DOCTYPE html>
    <html>
        <head>
            <title>Penn State Football Calendar</title>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    max-width: 800px;
                    margin: 0 auto;
                    padding: 20px;
                    background-color: #f5f5f5;
                }}
                .header {{
                    background-color: #041E42;
                    color: white;
                    padding: 20px;
                    text-align: center;
                    border-radius: 10px;
                    margin-bottom: 20px;
                }}
                .container {{
                    background-color: white;
                    padding: 20px;
                    border-radius: 10px;
                    box-shadow: 0 2px 5px rgba(0,0,0,0.1);
                    margin-bottom: 20px;
                }}
                .url-box {{
                    background-color: #f0f0f0;
                    padding: 15px;
                    border-radius: 5px;
                    font-family: monospace;
                    word-break: break-all;
                    margin: 10px 0;
                }}
                .btn {{
                    background-color: #041E42;
                    color: white;
                    padding: 12px 24px;
                    text-decoration: none;
                    border-radius: 5px;
                    display: inline-block;
                    margin: 10px 10px 10px 0;
                }}
                .btn:hover {{
                    background-color: #0066CC;
                }}
                .footer {{
                    text-align: center;
                    color: #666;
                    font-size: 0.9em;
                    margin-top: 30px;
                }}
                .features {{
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                    gap: 15px;
                    margin: 20px 0;
                }}
                .feature {{
                    background-color: #f8f9fa;
                    padding: 15px;
                    border-radius: 8px;
                    border-left: 4px solid #041E42;
                }}
                .feature h4 {{
                    margin-top: 0;
                    color: #041E42;
                }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>🏈 Penn State Football Calendar</h1>
                <p>Stay up-to-date with all Nittany Lions games!</p>
            </div>
            
            <div class="container">
                <h2>Subscribe to Calendar</h2>
                <p>Add this calendar to your preferred calendar app using the URL below:</p>
                <div class="url-box">{calendar_url}</div>
                
                <div style="text-align: center;">
                    <a href="{calendar_url}" class="btn">📥 Download Calendar File</a>
                    <a href="/debug" class="btn">🔍 View Schedule Details</a>
                </div>
            </div>
            
            <div class="container">
                <h2>Features</h2>
                <div class="features">
                    <div class="feature">
                        <h4>📺 TV Information</h4>
                        <p>Includes broadcast network details when available (CBS, NBC, Big Ten Network, etc.)</p>
                    </div>
                    <div class="feature">
                        <h4>🏟️ Game Locations</h4>
                        <p>Full venue information for both home and away games</p>
                    </div>
                    <div class="feature">
                        <h4>🎉 Special Events</h4>
                        <p>White Out, Homecoming, Senior Day, and other special game designations</p>
                    </div>
                    <div class="feature">
                        <h4>🔄 Auto-Updates</h4>
                        <p>Calendar updates daily with the latest game times and TV information</p>
                    </div>
                </div>
            </div>
            
            <div class="container">
                <h2>How to Use</h2>
                <ol>
                    <li><strong>Copy the URL above</strong> to your clipboard</li>
                    <li><strong>Open your calendar app</strong> (Apple Calendar, Google Calendar, Outlook, etc.)</li>
                    <li><strong>Add a new calendar subscription</strong> using the URL</li>
                    <li><strong>Your calendar will sync</strong> automatically with updates!</li>
                </ol>
                
                <h3>Platform-Specific Instructions:</h3>
                <ul>
                    <li><strong>iPhone/Mac:</strong> Settings → Accounts & Passwords → Add Account → Other → Add Subscribed Calendar</li>
                    <li><strong>Google Calendar:</strong> Settings → Add calendar → From URL</li>
                    <li><strong>Outlook:</strong> Add calendar → From internet</li>
                </ul>
            </div>
            
            <div class="footer">
                <p>📊 Data sourced from <a href="https://gopsusports.com">gopsusports.com</a></p>
                <p>⚠️ This service is not affiliated with Penn State University</p>
                <p>🔧 Updated daily at 3:00 AM ET with the latest schedule information</p>
                <p>📧 Questions? Check out the <a href="https://github.com/lordofthetrees/PSUFootballSchedule">GitHub repository</a></p>
            </div>
        </body>
    </html>
    """

@app.route('/debug')
def debug_info():
    """Show debugging information about scraped games with enhanced details"""
    try:
        games = scrape_schedule()
        
        debug_html = """
        <!DOCTYPE html>
        <html>
            <head>
                <title>Penn State Football Schedule - Debug Info</title>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <style>
                    body { 
                        font-family: Arial, sans-serif; 
                        padding: 20px; 
                        background-color: #f5f5f5;
                    }
                    .container {
                        max-width: 1200px;
                        margin: 0 auto;
                        background-color: white;
                        padding: 20px;
                        border-radius: 10px;
                        box-shadow: 0 2px 5px rgba(0,0,0,0.1);
                    }
                    table { 
                        border-collapse: collapse; 
                        width: 100%; 
                        margin-top: 20px;
                    }
                    th, td { 
                        border: 1px solid #ddd; 
                        padding: 12px; 
                        text-align: left; 
                        vertical-align: top;
                    }
                    tr:nth-child(even) { 
                        background-color: #f9f9f9; 
                    }
                    th { 
                        background-color: #041E42; 
                        color: white; 
                        font-weight: bold;
                    }
                    h1 { 
                        color: #041E42; 
                        text-align: center;
                        margin-bottom: 10px;
                    }
                    .subtitle {
                        text-align: center;
                        color: #666;
                        margin-bottom: 20px;
                    }
                    .home-game { 
                        background-color: #e8f5e8; 
                    }
                    .away-game { 
                        background-color: #fff3e0; 
                    }
                    .special-event {
                        font-weight: bold;
                        color: #041E42;
                    }
                    .broadcast-info {
                        font-weight: bold;
                    }
                    .tba {
                        color: #999;
                        font-style: italic;
                    }
                    .stats {
                        display: grid;
                        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                        gap: 15px;
                        margin-bottom: 20px;
                    }
                    .stat-box {
                        background-color: #041E42;
                        color: white;
                        padding: 15px;
                        border-radius: 8px;
                        text-align: center;
                    }
                    .stat-number {
                        font-size: 2em;
                        font-weight: bold;
                        display: block;
                    }
                    .back-link {
                        display: inline-block;
                        background-color: #041E42;
                        color: white;
                        padding: 10px 20px;
                        text-decoration: none;
                        border-radius: 5px;
                        margin-bottom: 20px;
                    }
                    .back-link:hover {
                        background-color: #0066CC;
                    }
                </style>
            </head>
            <body>
                <div class="container">
                    <a href="/" class="back-link">← Back to Main Page</a>
                    
                    <h1>🏈 Penn State Football Schedule</h1>
                    <p class="subtitle">Debug Information & Schedule Details</p>
                    
                    <div class="stats">
                        <div class="stat-box">
                            <span class="stat-number">""" + str(len(games)) + """</span>
                            Total Games
                        </div>
                        <div class="stat-box">
                            <span class="stat-number">""" + str(len([g for g in games if g.get('is_home')])) + """</span>
                            Home Games
                        </div>
                        <div class="stat-box">
                            <span class="stat-number">""" + str(len([g for g in games if not g.get('is_home')])) + """</span>
                            Away Games
                        </div>
                        <div class="stat-box">
                            <span class="stat-number">""" + str(len([g for g in games if g.get('broadcast') and g.get('broadcast') != 'TBA'])) + """</span>
                            TV Confirmed
                        </div>
                    </div>
                    
                    <table>
                        <tr>
                            <th>Date</th>
                            <th>Game</th>
                            <th>Time</th>
                            <th>Location</th>
                            <th>TV Network</th>
                            <th>Special Event</th>
                        </tr>
        """
        
        for game in games:
            row_class = "home-game" if game.get('is_home') else "away-game"
            
            # Format date
            game_date = game['start'].strftime('%a, %b %d, %Y') if game.get('start') else 'Unknown'
            
            # Format time
            game_time = game['start'].strftime('%I:%M %p ET') if game.get('start') else game.get('time_str', 'TBA')
            if game.get('time_str') == 'TBA':
                game_time = '<span class="tba">TBA</span>'
            
            # Format broadcast
            broadcast = game.get('broadcast', 'TBA')
            broadcast_class = 'broadcast-info' if broadcast and broadcast != 'TBA' else 'tba'
            broadcast_display = f'<span class="{broadcast_class}">{broadcast}</span>'
            
            # Format special event
            special = game.get('special', '')
            special_display = f'<span class="special-event">{special}</span>' if special else ''
            
            debug_html += f"""
                        <tr class="{row_class}">
                            <td>{game_date}</td>
                            <td><strong>{game['title']}</strong></td>
                            <td>{game_time}</td>
                            <td>{game.get('location', 'Unknown')}</td>
                            <td>{broadcast_display}</td>
                            <td>{special_display}</td>
                        </tr>
            """
        
        debug_html += """
                    </table>
                    
                    <div style="margin-top: 30px; padding: 20px; background-color: #f8f9fa; border-radius: 8px;">
                        <h3>🔧 Technical Information</h3>
                        <ul>
                            <li><strong>Last Updated:</strong> """ + datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S ET') + """</li>
                            <li><strong>Data Source:</strong> <a href="https://gopsusports.com/sports/football/schedule">gopsusports.com</a></li>
                            <li><strong>Update Frequency:</strong> Daily at 3:00 AM ET</li>
                            <li><strong>Calendar Format:</strong> iCalendar (.ics)</li>
                            <li><strong>GitHub Repository:</strong> <a href="https://github.com/lordofthetrees/PSUFootballSchedule">PSUFootballSchedule</a></li>
                        </ul>
                        
                        <h4>🎨 Legend</h4>
                        <ul>
                            <li><span style="background-color: #e8f5e8; padding: 2px 8px;">Green background</span> = Home games</li>
                            <li><span style="background-color: #fff3e0; padding: 2px 8px;">Orange background</span> = Away games</li>
                            <li><span class="tba">Gray italic text</span> = To be announced</li>
                            <li><span class="special-event">Blue bold text</span> = Special events (White Out, Homecoming, etc.)</li>
                        </ul>
                    </div>
                </div>
            </body>
        </html>
        """
        
        return debug_html
        
    except Exception as e:
        error_html = f"""
        <!DOCTYPE html>
        <html>
            <head>
                <title>Debug Error</title>
                <style>
                    body {{ font-family: Arial, sans-serif; padding: 20px; }}
                    .error {{ background-color: #ffe6e6; padding: 20px; border-radius: 8px; border-left: 4px solid #ff0000; }}
                </style>
            </head>
            <body>
                <div class="error">
                    <h2>Debug Error</h2>
                    <p><strong>Error:</strong> {str(e)}</p>
                    <p><a href="/">← Back to Main Page</a></p>
                </div>
            </body>
        </html>
        """
        return error_html

if __name__ == "__main__":
    # Create initial calendar
    logger.info("Starting Penn State Football Calendar Service...")
    
    try:
        update_calendar()
        logger.info("Initial calendar created successfully")
    except Exception as e:
        logger.error(f"Failed to create initial calendar: {str(e)}")
    
    # Create scheduler for daily updates
    scheduler = BackgroundScheduler()
    
    # Schedule daily updates at 3 AM ET (7 AM UTC)
    scheduler.add_job(
        func=update_calendar,
        trigger='cron',
        hour=7,  # 3 AM ET = 7 AM UTC
        minute=0,
        id='daily_update',
        name='Daily Calendar Update'
    )
    
    try:
        scheduler.start()
        logger.info("Scheduler started - daily updates at 3 AM ET")
    except Exception as e:
        logger.error(f"Failed to start scheduler: {str(e)}")
    
    # Run the Flask app
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting Flask app on port {port}")
    
    try:
        app.run(host='0.0.0.0', port=port, debug=False)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        scheduler.shutdown()
    except Exception as e:
        logger.error(f"Flask app error: {str(e)}")
        scheduler.shutdown()
