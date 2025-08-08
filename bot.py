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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
SCHEDULE_URL = os.getenv("SCHEDULE_URL") or "https://www.midlakesunited.com/schedule/"
ANNOUNCEMENTS_CHANNEL_NAME = os.getenv("ANNOUNCE_CHANNEL") or "announcements"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# Lock to prevent concurrent executions of check_calendar
calendar_check_lock = asyncio.Lock()

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user.name}")
    guild = bot.get_guild(GUILD_ID)
    if guild:
        logger.info(f"Connected to guild: {guild.name} (ID: {guild.id})")
        try:
            await guild.me.edit(nick="Fourth Official")
            logger.info(f"Bot nickname set to {guild.me.nick}")
        except discord.HTTPException as e:
            logger.warning(f"Failed to set nickname: {e}")
    else:
        logger.error(f"Could not find guild with ID {GUILD_ID}")
    
    check_calendar.start()
    update_presence.start()

async def fetch_static_html_with_retry(url, max_retries=3):
    """Fetch HTML with retry mechanism"""
    for attempt in range(max_retries):
        try:
            resp = await asyncio.to_thread(
                requests.get, 
                url, 
                headers={'User-Agent': 'Mozilla/5.0'}, 
                timeout=10
            )
            if resp.status_code != 200:
                raise Exception(f"Failed to load page: {resp.status_code}")
            return resp.text
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed to fetch {url}: {e}")
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)  # Exponential backoff

def parse_schedule(html):
    soup = BeautifulSoup(html, 'html.parser')
    events = []

    # Infer year from page header
    header = soup.select_one('h1')
    year = None
    if header:
        match = re.search(r'(\d{4})', header.get_text())
        if match:
            year = int(match.group(1))
    if not year:
        raise Exception("Could not determine event year from page header")

    for blk in soup.select('.Upcoming'):
        try:
            date_text = blk.select_one('.GameDate').get_text(strip=True)
            time_text = blk.select_one('.GameTime').get_text(strip=True)
            opponent = blk.select_one('.OpponentName').get_text(strip=True)
            loc_tag = blk.select_one('.ThemeNight')
            location = loc_tag.get_text(strip=True) if loc_tag else "TBD"

            dt = datetime.strptime(f"{date_text} {year} {time_text}", "%B %d %Y %I:%M %p")
            dt = timezone('US/Pacific').localize(dt).astimezone(UTC)
            end_dt = dt + timedelta(hours=2)

            events.append({
                "name": f"Midlakes {opponent}",
                "start_time": dt,
                "end_time": end_dt,
                "location": location,
                "description": f"Match {opponent} at {location}"
            })
        except Exception as e:
            logger.warning(f"Error parsing event block: {e}")

    return events

async def create_event_safely(guild, event_data, max_retries=3):
    """Create an event with retry mechanism and rate limit handling"""
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
            return event
        except discord.HTTPException as e:
            if e.status == 429:  # Rate limited
                retry_after = getattr(e, 'retry_after', 2 ** attempt)
                logger.warning(f"Rate limited, waiting {retry_after}s")
                await asyncio.sleep(retry_after)
            elif attempt == max_retries - 1:
                raise
            else:
                logger.warning(f"Attempt {attempt + 1} failed to create event: {e}")
                await asyncio.sleep(2 ** attempt)

async def send_announcement_safely(channel, message, max_retries=3):
    """Send announcement with retry mechanism"""
    for attempt in range(max_retries):
        try:
            await channel.send(message)
            return
        except discord.HTTPException as e:
            if e.status == 429:  # Rate limited
                retry_after = getattr(e, 'retry_after', 2 ** attempt)
                logger.warning(f"Rate limited on announcement, waiting {retry_after}s")
                await asyncio.sleep(retry_after)
            elif attempt == max_retries - 1:
                raise
            else:
                logger.warning(f"Attempt {attempt + 1} failed to send announcement: {e}")
                await asyncio.sleep(2 ** attempt)

@tasks.loop(hours=24)
async def check_calendar():
    # Use lock to prevent concurrent executions
    if calendar_check_lock.locked():
        logger.info("Calendar check already in progress, skipping...")
        return
    
    async with calendar_check_lock:
        logger.info("Checking for new events...")
        try:
            html = await fetch_static_html_with_retry(SCHEDULE_URL)
            new_events = parse_schedule(html)

            guild = bot.get_guild(GUILD_ID)
            if not guild:
                logger.error("Guild not found.")
                return

            announcements_channel = discord.utils.get(guild.text_channels, name=ANNOUNCEMENTS_CHANNEL_NAME)
            if not announcements_channel:
                logger.error(f"Announcements channel '{ANNOUNCEMENTS_CHANNEL_NAME}' not found.")
                return

            # Fetch existing events with retry
            existing = None
            for attempt in range(3):
                try:
                    existing = await guild.fetch_scheduled_events()
                    break
                except discord.HTTPException as e:
                    if e.status == 429:
                        retry_after = getattr(e, 'retry_after', 2 ** attempt)
                        logger.warning(f"Rate limited fetching events, waiting {retry_after}s")
                        await asyncio.sleep(retry_after)
                    elif attempt == 2:
                        raise
                    else:
                        await asyncio.sleep(2 ** attempt)

            if existing is None:
                logger.error("Failed to fetch existing events after retries")
                return

            existing_keys = {(ev.name, ev.start_time) for ev in existing}

            # Process events one by one to avoid race conditions
            for e in new_events:
                key = (e["name"], e["start_time"])
                if key in existing_keys:
                    logger.info(f"Skipping duplicate event: {key}")
                    continue

                try:
                    event = await create_event_safely(guild, e)
                    logger.info(f"Created event: {e['name']}")

                    # Post to announcements channel
                    message = (
                        f"📅 New Match Scheduled: **{event.name}**\n"
                        f"🕒 When: <t:{int(event.start_time.timestamp())}:F>\n"
                        f"📍 Where: {e['location']}\n"
                        f"🔗 RSVP via the Events tab!"
                    )
                    await send_announcement_safely(announcements_channel, message)
                    
                    # Add to existing_keys to prevent duplicates in this batch
                    existing_keys.add(key)
                    
                    # Small delay between events to be nice to the API
                    await asyncio.sleep(1)
                    
                except Exception as event_error:
                    logger.error(f"Failed to create event {e['name']}: {event_error}")

        except Exception as ex:
            logger.error(f"Error in calendar sync: {ex}")

@bot.command(name="refresh_events")
async def refresh_events(ctx):
    moderator_role = discord.utils.get(ctx.guild.roles, name="referees")
    if ctx.guild and ctx.guild.id == GUILD_ID and moderator_role in ctx.author.roles:
        await ctx.send("🔄 Manually refreshing events from the schedule...")
        try:
            await check_calendar()
            await ctx.send("✅ Refresh complete.")
        except Exception as e:
            logger.error(f"Manual refresh failed: {e}")
            await ctx.send("❌ Refresh failed. Check logs for details.")
    else:
        await ctx.send("❌ You don't have permission to run this command.")

@tasks.loop(hours=1)
async def update_presence():
    try:
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            logger.warning("Guild not found for presence update.")
            return

        # Fetch scheduled events with retry
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

        upcoming = sorted([e for e in existing if e.start_time > discord.utils.utcnow()], key=lambda x: x.start_time)

        if upcoming:
            next_event = upcoming[0]
            delta = next_event.start_time - discord.utils.utcnow()
            hours = delta.total_seconds() // 3600
            status_msg = f"Matchday in {int(hours)}h: {next_event.name}"
        else:
            status_msg = "the Midlakes United Schedule"

        try:
            await bot.change_presence(
                activity=discord.Activity(type=discord.ActivityType.watching, name=status_msg)
            )
        except discord.HTTPException as e:
            logger.warning(f"Failed to update presence: {e}")
            
    except Exception as ex:
        logger.error(f"Error updating presence: {ex}")

bot.run(TOKEN)
