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

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
SCHEDULE_URL = os.getenv("SCHEDULE_URL") or "https://www.midlakesunited.com/schedule/"
ANNOUNCEMENTS_CHANNEL_NAME = "announcements"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")
    check_calendar.start()

def fetch_static_html(url):
    resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
    if resp.status_code != 200:
        raise Exception(f"Failed to load page: {resp.status_code}")
    return resp.text

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
            print(f"Error parsing event block: {e}")

    return events

@tasks.loop(hours=24)
async def check_calendar():
    print("Checking for new events...")
    try:
        html = await asyncio.to_thread(fetch_static_html, SCHEDULE_URL)
        new_events = parse_schedule(html)

        guild = bot.get_guild(GUILD_ID)
        if not guild:
            print("Guild not found.")
            return

        announcements_channel = discord.utils.get(guild.text_channels, name=ANNOUNCEMENTS_CHANNEL_NAME)
        if not announcements_channel:
            print(f"Announcements channel '{ANNOUNCEMENTS_CHANNEL_NAME}' not found.")
            return

        existing = await guild.fetch_scheduled_events()
        existing_keys = {(ev.name, ev.start_time) for ev in existing}

        for e in new_events:
            key = (e["name"], e["start_time"])
            if key in existing_keys:
                print("Skipping duplicate event", key)
                continue

            event = await guild.create_scheduled_event(
                name=e["name"],
                start_time=e["start_time"],
                end_time=e["end_time"],
                description=e["description"],
                location=e["location"],
                entity_type=discord.EntityType.external,
                privacy_level=discord.PrivacyLevel.guild_only,
            )
            print(f"Created event: {e['name']}")

            # Post to announcements channel
            await announcements_channel.send(
                f"📅 New Match Scheduled: **{event.name}**\n"
                f"🕒 When: <t:{int(event.start_time.timestamp())}:F>\n"
                f"📍 Where: {e['location']}\n"
                f"🔗 RSVP via the Events tab!"
            )

    except Exception as ex:
        print(f"Error in calendar sync: {ex}")

@bot.command(name="refresh_events")
async def refresh_events(ctx):
    moderator_role = discord.utils.get(ctx.guild.roles, name="referees")
    if ctx.guild and ctx.guild.id == GUILD_ID and moderator_role in ctx.author.roles:
        await ctx.send("🔄 Manually refreshing events from the schedule...")
        await check_calendar()
        await ctx.send("✅ Refresh complete.")
    else:
        await ctx.send("❌ You don't have permission to run this command.")

bot.run(TOKEN)
