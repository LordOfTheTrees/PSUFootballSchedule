import requests
from bs4 import BeautifulSoup
import ics
from ics import Calendar, Event
import datetime
import time
import os
from flask import Flask, Response
import logging
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
BASE_URL = "https://gopsusports.com/sports/football/schedule"
CALENDAR_FILE = "penn_state_football.ics"

def parse_date_time(date_str, time_str):
    """Parse date and time strings into datetime object"""
    try:
        # Handle various date/time formats that might appear on the website
        # This will need to be adjusted based on the actual format used on the site
        date_parts = date_str.strip().split('/')
        month, day, year = int(date_parts[0]), int(date_parts[1]), int(date_parts[2])
        
        # Parse time (if available)
        if time_str and time_str.lower() != "tba":
            # Handle AM/PM
            time_str = time_str.strip().upper()
            hour, minute = 12, 0  # Default values
            
            if ":" in time_str:
                time_parts = time_str.replace("AM", "").replace("PM", "").strip().split(':')
                hour, minute = int(time_parts[0]), int(time_parts[1])
            
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
        response = requests.get(BASE_URL)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find the schedule table/elements
        # Note: The actual selectors will need to be adjusted based on the website's HTML structure
        schedule_items = soup.select('.sidearm-schedule-games-container .sidearm-schedule-game')
        
        for item in schedule_items:
            try:
                # Extract date
                date_elem = item.select_one('.sidearm-schedule-game-opponent-date')
                date_str = date_elem.text.strip() if date_elem else ""
                
                # Extract time
                time_elem = item.select_one('.sidearm-schedule-game-time')
                time_str = time_elem.text.strip() if time_elem else "TBA"
                
                # Extract opponent
                opponent_elem = item.select_one('.sidearm-schedule-game-opponent-name')
                opponent = opponent_elem.text.strip() if opponent_elem else "Unknown Opponent"
                
                # Determine if home or away
                location_elem = item.select_one('.sidearm-schedule-game-location')
                location = location_elem.text.strip() if location_elem else ""
                is_home = "home" in item.get('class', []) or "Home" in location
                
                # Extract broadcast info
                broadcast_elem = item.select_one('.sidearm-schedule-game-network')
                broadcast = broadcast_elem.text.strip() if broadcast_elem else ""
                
                # Create readable title based on home/away status
                if is_home:
                    title = f"{opponent} at Penn State"
                else:
                    title = f"Penn State at {opponent}"
                
                # Get datetime object
                game_datetime = parse_date_time(date_str, time_str)
                
                # Game duration (default 3.5 hours)
                duration = datetime.timedelta(hours=3, minutes=30)
                
                games.append({
                    'title': title,
                    'start': game_datetime,
                    'end': game_datetime + duration,
                    'location': location,
                    'broadcast': broadcast,
                    'is_home': is_home
                })
                
                logger.info(f"Scraped game: {title} on {game_datetime}")
                
            except Exception as e:
                logger.error(f"Error parsing game item: {str(e)}")
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
        <head><title>Penn State Football Calendar</title></head>
        <body>
            <h1>Penn State Football Calendar</h1>
            <p>Add this calendar to your favorite calendar app using this URL:</p>
            <pre>http://YOUR_SERVER_URL/calendar.ics</pre>
            <p><a href="/calendar.ics">Download Calendar</a></p>
        </body>
    </html>
    """

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
