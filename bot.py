"""
Midlakes United Discord Bot

A Discord bot that automatically syncs soccer match schedules from the Midlakes United website
to Discord server events and announcements. The bot performs the following functions:

1. Scrapes the Midlakes United schedule website daily
2. Creates Discord server events for new matches
3. Posts announcements in a designated channel
4. Updates bot presence to show upcoming matches
5. Provides manual refresh commands for moderators

Dependencies:
- discord.py: Discord API wrapper
- beautifulsoup4: HTML parsing for web scraping
- requests: HTTP requests for web scraping
- python-dotenv: Environment variable management
- pytz: Timezone handling

Environment Variables Required:
- DISCORD_TOKEN: Bot token from Discord Developer Portal
- GUILD_ID: Discord server ID where bot operates
- SCHEDULE_URL: URL of Midlakes United schedule page (optional)
- ANNOUNCE_CHANNEL: Name of announcement channel (optional, defaults to "announcements")
"""

import discord
import asyncio
from discord.ext import tasks, commands
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from pytz import timezone, UTC
import os
import re
import requests
from dotenv import load_dotenv
import logging

load_dotenv()

# ==================== LOGGING CONFIGURATION ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== ENVIRONMENT VARIABLES ====================
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
SCHEDULE_URL = os.getenv("SCHEDULE_URL") or "https://www.midlakesunited.com/schedule/"
ANNOUNCEMENTS_CHANNEL_NAME = os.getenv("ANNOUNCE_CHANNEL") or "announcements"

# ==================== BOT CONFIGURATION ====================
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# Prevent concurrent calendar check executions
calendar_check_lock = asyncio.Lock()

# ==================== BOT EVENT HANDLERS ====================

@bot.event
async def on_ready():
    """
    Bot startup event handler.
    
    Performs initial setup when bot connects to Discord:
    - Logs connection details
    - Sets bot nickname to "Fourth Official"
    - Starts scheduled tasks for calendar checking and presence updates
    """
    logger.info(f"Bot started successfully - {bot.user.name} (ID: {bot.user.id})")
    guild = bot.get_guild(GUILD_ID)
    if guild:
        logger.info(f"Connected to guild: {guild.name} (ID: {guild.id}) with {guild.member_count} members")
        try:
            await guild.me.edit(nick="Fourth Official")
            logger.info(f"Bot nickname set to '{guild.me.nick}'")
        except discord.HTTPException as e:
            logger.warning(f"Failed to set nickname: {e}")
    else:
        logger.error(f"Could not find guild with ID {GUILD_ID}")
        return
    
    # Start background tasks
    try:
        check_calendar.start()
        logger.info("Calendar check task started successfully")
    except Exception as e:
        logger.error(f"Failed to start calendar check task: {e}")
    
    try:
        update_presence.start()
        logger.info("Presence update task started successfully")
    except Exception as e:
        logger.error(f"Failed to start presence update task: {e}")

@bot.event
async def on_error(event, *args, **kwargs):
    """Log unhandled errors in Discord events."""
    logger.error(f"Unhandled error in event {event}: {args}", exc_info=True)

@bot.event
async def on_command_error(ctx, error):
    """
    Handle command errors with appropriate logging.
    
    Args:
        ctx: Discord command context
        error: The error that occurred
    """
    if isinstance(error, commands.CommandNotFound):
        logger.debug(f"Unknown command attempted: {ctx.message.content}")
    elif isinstance(error, commands.MissingPermissions):
        logger.warning(f"Permission denied for {ctx.author} in {ctx.guild}: {error}")
    else:
        logger.error(f"Command error in {ctx.command}: {error}", exc_info=True)

@bot.event
async def on_guild_join(guild):
    """Log when bot joins a new server."""
    logger.info(f"Bot joined new guild: {guild.name} (ID: {guild.id}) with {guild.member_count} members")

@bot.event
async def on_guild_remove(guild):
    """Log when bot is removed from a server."""
    logger.info(f"Bot removed from guild: {guild.name} (ID: {guild.id})")

@bot.event
async def on_disconnect():
    """Log when bot disconnects from Discord."""
    logger.warning("Bot disconnected from Discord")

@bot.event
async def on_resumed():
    """Log when bot reconnects to Discord."""
    logger.info("Bot connection resumed")

@bot.event
async def on_message(message):
    """
    Log all messages received by the bot and process commands.
    
    Args:
        message: Discord message object
    """
    # Don't log bot's own messages to prevent spam
    if message.author == bot.user:
        return
    
    # Log message with context (channel, guild, user)
    channel_info = f"#{message.channel.name}" if hasattr(message.channel, 'name') else f"DM"
    guild_info = f" in {message.guild.name}" if message.guild else ""
    logger.info(f"Message from {message.author} ({message.author.id}) {channel_info}{guild_info}: {message.content}")
    
    # Process commands normally
    await bot.process_commands(message)

# ==================== WEB SCRAPING FUNCTIONS ====================

async def fetch_static_html_with_retry(url, max_retries=3):
    """
    Fetch HTML content from a URL with retry mechanism and exponential backoff.
    
    Args:
        url (str): The URL to fetch
        max_retries (int): Maximum number of retry attempts (default: 3)
    
    Returns:
        str: HTML content of the page
    
    Raises:
        Exception: If all retry attempts fail
    """
    logger.debug(f"Fetching HTML from {url}")
    for attempt in range(max_retries):
        try:
            # Use asyncio.to_thread to avoid blocking the event loop
            resp = await asyncio.to_thread(
                requests.get, 
                url, 
                headers={'User-Agent': 'Mozilla/5.0'}, 
                timeout=10
            )
            if resp.status_code != 200:
                raise Exception(f"Failed to load page: {resp.status_code}")
            logger.debug(f"Successfully fetched {len(resp.text)} characters from {url}")
            return resp.text
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}/{max_retries} failed to fetch {url}: {e}")
            if attempt == max_retries - 1:
                logger.error(f"All {max_retries} attempts failed to fetch {url}")
                raise
            await asyncio.sleep(2 ** attempt)  # Exponential backoff

def parse_schedule(html):
    """
    Parse the Midlakes United schedule HTML to extract event information.
    
    The function looks for elements with class 'Upcoming' and extracts:
    - Game date and time
    - Opponent name
    - Location/venue information
    
    Args:
        html (str): Raw HTML content from the schedule page
    
    Returns:
        list: List of event dictionaries with keys:
            - name: Event title (e.g., "Midlakes vs Team Name")
            - start_time: UTC datetime object
            - end_time: UTC datetime object (start_time + 2 hours)
            - location: Venue name or "TBD"
            - description: Event description
    
    Raises:
        Exception: If year cannot be determined from page header
    """
    logger.debug("Starting to parse schedule HTML")
    soup = BeautifulSoup(html, 'html.parser')
    events = []

    # Extract year from page header (required for date parsing)
    header = soup.select_one('h1')
    year = None
    if header:
        match = re.search(r'(\d{4})', header.get_text())
        if match:
            year = int(match.group(1))
            logger.debug(f"Determined event year: {year}")
    if not year:
        logger.error("Could not determine event year from page header")
        raise Exception("Could not determine event year from page header")

    # Find all upcoming event blocks
    event_blocks = soup.select('.Upcoming')
    logger.info(f"Found {len(event_blocks)} potential events to parse")

    for i, blk in enumerate(event_blocks):
        try:
            # Extract event details from HTML elements
            date_text = blk.select_one('.GameDate').get_text(strip=True)
            time_text = blk.select_one('.GameTime').get_text(strip=True)
            opponent = blk.select_one('.OpponentName').get_text(strip=True)
            loc_tag = blk.select_one('.ThemeNight')
            location = loc_tag.get_text(strip=True) if loc_tag else "TBD"

            # Parse date/time and convert to UTC
            dt = datetime.strptime(f"{date_text} {year} {time_text}", "%B %d %Y %I:%M %p")
            dt = timezone('US/Pacific').localize(dt).astimezone(UTC)
            end_dt = dt + timedelta(hours=2)  # Assume 2-hour duration

            event_data = {
                "name": f"Midlakes {opponent}",
                "start_time": dt,
                "end_time": end_dt,
                "location": location,
                "description": f"Match {opponent} at {location}"
            }
            events.append(event_data)
            logger.debug(f"Parsed event {i+1}: {event_data['name']} on {dt.strftime('%Y-%m-%d %H:%M UTC')}")
        except Exception as e:
            logger.warning(f"Error parsing event block {i+1}: {e}")

    logger.info(f"Successfully parsed {len(events)} events from schedule")
    return events

# ==================== DISCORD API HELPER FUNCTIONS ====================

async def create_event_safely(guild, event_data, max_retries=3):
    """
    Create a Discord scheduled event with retry mechanism and rate limit handling.
    
    Args:
        guild: Discord guild object where event will be created
        event_data (dict): Event information containing name, start_time, end_time, location, description
        max_retries (int): Maximum number of retry attempts (default: 3)
    
    Returns:
        discord.ScheduledEvent: The created Discord event object
    
    Raises:
        discord.HTTPException: If all retry attempts fail
    """
    logger.debug(f"Creating event: {event_data['name']} at {event_data['start_time']}")
    for attempt in range(max_retries):
        try:
            event = await guild.create_scheduled_event(
                name=event_data["name"],
                start_time=event_data["start_time"],
                end_time=event_data["end_time"],
                description=event_data["description"],
                location=event_data["location"],
                entity_type=discord.EntityType.external,
                privacy_level=discord.PrivacyLevel.guild_only,
            )
            logger.info(f"Successfully created Discord event: {event.name} (ID: {event.id})")
            return event
        except discord.HTTPException as e:
            if e.status == 429:  # Rate limited
                retry_after = getattr(e, 'retry_after', 2 ** attempt)
                logger.warning(f"Rate limited creating event, waiting {retry_after}s")
                await asyncio.sleep(retry_after)
            elif attempt == max_retries - 1:
                logger.error(f"Failed to create event after {max_retries} attempts: {e}")
                raise
            else:
                logger.warning(f"Attempt {attempt + 1}/{max_retries} failed to create event: {e}")
                await asyncio.sleep(2 ** attempt)

async def send_announcement_safely(channel, message, max_retries=3):
    """
    Send a message to a Discord channel with retry mechanism and rate limit handling.
    
    Args:
        channel: Discord channel object where message will be sent
        message (str): The message content to send
        max_retries (int): Maximum number of retry attempts (default: 3)
    
    Raises:
        discord.HTTPException: If all retry attempts fail
    """
    logger.debug(f"Sending announcement to #{channel.name}")
    for attempt in range(max_retries):
        try:
            sent_message = await channel.send(message)
            logger.info(f"Announcement sent to #{channel.name} (Message ID: {sent_message.id})")
            return
        except discord.HTTPException as e:
            if e.status == 429:  # Rate limited
                retry_after = getattr(e, 'retry_after', 2 ** attempt)
                logger.warning(f"Rate limited on announcement, waiting {retry_after}s")
                await asyncio.sleep(retry_after)
            elif attempt == max_retries - 1:
                logger.error(f"Failed to send announcement after {max_retries} attempts: {e}")
                raise
            else:
                logger.warning(f"Attempt {attempt + 1}/{max_retries} failed to send announcement: {e}")
                await asyncio.sleep(2 ** attempt)

# ==================== SCHEDULED TASKS ====================

@tasks.loop(hours=24)
async def check_calendar():
    """
    Scheduled task that runs every 24 hours to check for new events.
    
    This function:
    1. Fetches the latest schedule from the Midlakes United website
    2. Parses the HTML to extract event information
    3. Compares with existing Discord events to avoid duplicates
    4. Creates new Discord events for any new matches found
    5. Posts announcements in the designated channel
    
    Uses a lock to prevent concurrent executions and includes comprehensive
    error handling with retry mechanisms for network and API operations.
    """
    # Prevent concurrent executions using asyncio lock
    if calendar_check_lock.locked():
        logger.info("Calendar check already in progress, skipping...")
        return
    
    async with calendar_check_lock:
        start_time = datetime.now()
        logger.info("Starting scheduled calendar check...")
        
        try:
            # Fetch and parse the schedule
            html = await fetch_static_html_with_retry(SCHEDULE_URL)
            new_events = parse_schedule(html)

            # Get Discord guild and announcement channel
            guild = bot.get_guild(GUILD_ID)
            if not guild:
                logger.error("Guild not found during calendar check")
                return

            announcements_channel = discord.utils.get(guild.text_channels, name=ANNOUNCEMENTS_CHANNEL_NAME)
            if not announcements_channel:
                logger.error(f"Announcements channel '{ANNOUNCEMENTS_CHANNEL_NAME}' not found")
                return

            # Fetch existing Discord events with retry mechanism
            logger.debug("Fetching existing Discord events...")
            existing = None
            for attempt in range(3):
                try:
                    existing = await guild.fetch_scheduled_events()
                    logger.debug(f"Found {len(existing)} existing events")
                    break
                except discord.HTTPException as e:
                    if e.status == 429:
                        retry_after = getattr(e, 'retry_after', 2 ** attempt)
                        logger.warning(f"Rate limited fetching events, waiting {retry_after}s")
                        await asyncio.sleep(retry_after)
                    elif attempt == 2:
                        logger.error(f"Failed to fetch existing events after 3 attempts: {e}")
                        raise
                    else:
                        await asyncio.sleep(2 ** attempt)

            if existing is None:
                logger.error("Failed to fetch existing events after retries")
                return

            # Create a set of existing events to avoid duplicates
            existing_keys = {(ev.name, ev.start_time) for ev in existing}
            events_created = 0

            # Process each new event
            for e in new_events:
                key = (e["name"], e["start_time"])
                if key in existing_keys:
                    logger.debug(f"Skipping duplicate event: {e['name']} at {e['start_time']}")
                    continue

                try:
                    # Create the Discord event
                    event = await create_event_safely(guild, e)

                    # Post announcement in the channel
                    message = (
                        f"📅 New Match Scheduled: **{event.name}**\n"
                        f"🕒 When: <t:{int(event.start_time.timestamp())}:F>\n"
                        f"📍 Where: {e['location']}\n"
                        f"🔗 RSVP via the Events tab!"
                    )
                    await send_announcement_safely(announcements_channel, message)
                    
                    # Update local tracking to prevent duplicates in this batch
                    existing_keys.add(key)
                    events_created += 1
                    
                    # Rate limiting: small delay between events
                    await asyncio.sleep(1)
                    
                except Exception as event_error:
                    logger.error(f"Failed to create/announce event {e['name']}: {event_error}")

            # Log completion statistics
            duration = datetime.now() - start_time
            logger.info(f"Calendar check completed in {duration.total_seconds():.2f}s. Created {events_created} new events.")

        except Exception as ex:
            duration = datetime.now() - start_time
            logger.error(f"Calendar check failed after {duration.total_seconds():.2f}s: {ex}", exc_info=True)

# ==================== BOT COMMANDS ====================

@bot.command(name="refresh_events")
async def refresh_events(ctx):
    """
    Manual command to refresh events from the schedule.
    
    This command allows users with the "referees" role to manually trigger
    a calendar check outside of the normal 24-hour schedule. Useful for
    immediate updates when new events are posted.
    
    Permissions: Requires "referees" role and must be used in the configured guild.
    
    Args:
        ctx: Discord command context
    """
    logger.info(f"Manual refresh requested by {ctx.author} ({ctx.author.id}) in {ctx.guild}")
    moderator_role = discord.utils.get(ctx.guild.roles, name="referees")
    
    # Check permissions: correct guild and referees role
    if ctx.guild and ctx.guild.id == GUILD_ID and moderator_role in ctx.author.roles:
        await ctx.send("🔄 Manually refreshing events from the schedule...")
        try:
            await check_calendar()
            await ctx.send("✅ Refresh complete.")
            logger.info(f"Manual refresh completed successfully for {ctx.author}")
        except Exception as e:
            logger.error(f"Manual refresh failed for {ctx.author}: {e}", exc_info=True)
            await ctx.send("❌ Refresh failed. Check logs for details.")
    else:
        logger.warning(f"Unauthorized refresh attempt by {ctx.author} ({ctx.author.id}) in {ctx.guild}")
        await ctx.send("❌ You don't have permission to run this command.")

@tasks.loop(hours=1)
async def update_presence():
    """
    Scheduled task that updates the bot's Discord presence every hour.
    
    The presence shows information about upcoming matches:
    - If there are upcoming events: "Matchday in Xh: Event Name"
    - If no upcoming events: "the Midlakes United Schedule"
    
    This provides a quick way for users to see when the next match is
    without needing to check the events tab.
    """
    logger.debug("Starting presence update...")
    try:
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            logger.warning("Guild not found for presence update")
            return

        # Fetch scheduled events with retry mechanism
        existing = None
        for attempt in range(3):
            try:
                existing = await guild.fetch_scheduled_events()
                break
            except discord.HTTPException as e:
                if e.status == 429:
                    retry_after = getattr(e, 'retry_after', 2 ** attempt)
                    logger.warning(f"Rate limited updating presence, waiting {retry_after}s")
                    await asyncio.sleep(retry_after)
                elif attempt == 2:
                    logger.error(f"Failed to fetch events for presence update: {e}")
                    return
                else:
                    await asyncio.sleep(2 ** attempt)

        if existing is None:
            return

        # Find upcoming events and sort by start time
        upcoming = sorted([e for e in existing if e.start_time > discord.utils.utcnow()], key=lambda x: x.start_time)

        # Set presence message based on upcoming events
        if upcoming:
            next_event = upcoming[0]
            delta = next_event.start_time - discord.utils.utcnow()
            hours = delta.total_seconds() // 3600
            status_msg = f"Matchday in {int(hours)}h: {next_event.name}"
            logger.debug(f"Next event: {next_event.name} in {hours} hours")
        else:
            status_msg = "the Midlakes United Schedule"
            logger.debug("No upcoming events found")

        # Update bot presence
        try:
            await bot.change_presence(
                activity=discord.Activity(type=discord.ActivityType.watching, name=status_msg)
            )
            logger.debug(f"Presence updated to: {status_msg}")
        except discord.HTTPException as e:
            logger.warning(f"Failed to update presence: {e}")
            
    except Exception as ex:
        logger.error(f"Error updating presence: {ex}", exc_info=True)

# ==================== BOT STARTUP ====================

logger.info("Starting Discord bot...")
try:
    bot.run(TOKEN)
except Exception as e:
    logger.critical(f"Failed to start bot: {e}", exc_info=True)
finally:
    logger.info("Bot shutdown complete")
