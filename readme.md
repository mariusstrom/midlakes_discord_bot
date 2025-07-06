# Midlakes United Discord Event Bot ⚽️

This is a lightweight Discord bot that automatically syncs soccer match events from [midlakesunited.com/schedule](https://www.midlakesunited.com/schedule/) into your Discord server's **Scheduled Events** tab. It also posts event announcements and supports manual refresh via a bot command.

---

## 🔧 Features

- Scrapes Midlakes United fixture list daily (no iCal or API needed)
- Creates scheduled events in your server with proper time/location
- Posts a formatted message in `#announcements` for each new match
- `!refresh_events` command for manual resync (restricted to users with the `referees` role)

---

## ⚙️ Environment Setup

Create a `.env` file in the root of your project (this file should **never** be committed to GitHub). Add the following:

```env
DISCORD_TOKEN=your_discord_bot_token
GUILD_ID=your_discord_server_id
SCHEDULE_URL=https://www.midlakesunited.com/schedule/
```

## 📦 Dependencies

Install with:

```bash
pip install -r requirements.txt
```

## 🚀 Running the Bot

```bash
python bot.py
```

Or make it persistent on a Raspberry Pi or Linux VPS using systemd.

## 🔒 Discord Permissions

Ensure your bot has the following permissions:

- Manage Events
- Send Messages (in #announcements)
- Read Messages
- Use Application Commands

And assign the appropriate role to yourself (referees) to use the manual refresh command.

## 📢 Example Bot Command

!refresh_events: Triggers a manual sync of events. Only works for users with the referees role.

## 🛑 .gitignore Reminder

Ensure .env and venv/ are excluded in .gitignore:

## 🧪 Status

This bot is designed for hobbyist use — ideal for Raspberry Pi deployments or always-on VPS setups.

PRs welcome!
