# 🎬 Night YouTube Telegram Bot

A powerful Telegram bot for downloading, searching, and summarizing YouTube content using AI.

## ✨ Features

- 📥 **Video Downloader:** Support for multiple qualities (Best, 720p).
- 🎵 **Audio Extractor:** Extract high-quality MP3 from any video.
- 📝 **AI Summarizer:** Generate structured summaries of long videos using AI.
- 🔍 **YouTube Search:** Search for videos directly from Telegram using `/search`.
- 🏢 **Channel Overview:** Send a channel link to see:
  - 🆕 Latest 3 uploads
  - 🔥 Top 3 most popular videos
  - 🔴 Ongoing live streams
- ⏺️ **Live Capture:** Capture ongoing live streams.

## 🛠 Tech Stack

- **Language:** Python 3.12
- **Framework:** [aiogram 3.x](https://github.com/aiogram/aiogram)
- **Core Engine:** [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- **AI Summary:** Custom `ytsummarize` CLI integration.

## 🚀 Installation & Setup

### Prerequisites
- Python 3.10+
- FFmpeg installed on your system.
- `yt-dlp` installed.

### Setup
1. **Clone the repository:**
   ```bash
   git clone https://github.com/nightclaw77/yt-telegram-bot.git
   cd yt-telegram-bot
   ```

2. **Create Virtual Environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configuration:**
   Create a `.env` file or edit `main.py` with your credentials:
   ```env
   TELEGRAM_BOT_TOKEN=your_bot_token_here
   ALLOWED_USER_IDS=971043547
   ```

5. **Run the bot:**
   ```bash
   python3 main.py
   ```

## 🛡 Security
This bot includes a built-in user authorization system to ensure only allowed IDs can interact with it and consume server resources.

## 📝 License
MIT License. Created by NightClaw.
