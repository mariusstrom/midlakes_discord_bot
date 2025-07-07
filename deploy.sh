#!/bin/bash
set -e

echo "🔄 Pulling latest changes from GitHub..."
git pull origin main

echo "📦 Installing updated dependencies (if any)..."
source venv/bin/activate
pip install -r requirements.txt

echo "🔁 Restarting bot..."
sudo systemctl restart discord-bot.service

echo "✅ Deployment complete."
