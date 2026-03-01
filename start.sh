#!/bin/bash
# YouTube Telegram Bot Launcher

cd /root/.openclaw/workspace/projects/yt-telegram-bot

# Check if .env exists
if [ ! -f .env ]; then
    echo "❌ .env file not found! Copy .env.example to .env and configure it."
    exit 1
fi

# Install dependencies if needed
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate
pip install -r requirements.txt

# Run the bot
echo "🚀 Starting YouTube Bot..."
python3 main.py
