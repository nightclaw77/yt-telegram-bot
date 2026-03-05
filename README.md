# 🎬 Night YouTube Bale Bot (Split Project)

A split, isolated project cloned from the Telegram bot to evolve Bale-first workflows without risking Telegram production stability.

## ✨ Features

- 📥 **Video Downloader:** Support for multiple qualities (Best, 720p).
- 🎵 **Audio Extractor:** Extract high-quality MP3 from any video.
- 📝 **AI Summarizer:** Generate structured summaries of long videos using AI.
- 🗜️ **Telegram Video Compressor:** Send any Telegram video/video-document and get a smaller compressed file back.
- 🌉 **Telegram → Bale Bridge (Optional):** Automatically mirrors downloaded/compressed media to a Bale chat.
- 🛰️ **Bale Relay Fallback (Optional):** If VPS cannot reach Bale, forward through an Iran-side relay endpoint.
- ♻️ **Short-lived cache (Optional):** Reuse recent download/compression outputs for 30-60 minutes to skip reprocessing.
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

5. **(Optional) Enable Bale mirroring:**
   ```env
   BALE_FORWARD_ENABLED=true
   BALE_BOT_TOKEN=your_bale_bot_token
   BALE_CHAT_ID=your_bale_chat_id
   # Optional relay (when VPS cannot reach tapi.bale.ai)
   BALE_RELAY_URL=
   BALE_RELAY_TOKEN=
   FILE_CACHE_ENABLED=true
   FILE_CACHE_TTL_SECONDS=3600
   ```

6. **Run the bot:**
   ```bash
   python3 main.py
   ```

## 🛡 Security
This bot includes a built-in user authorization system to ensure only allowed IDs can interact with it and consume server resources.

## 🚧 Split Status

- This repository is now isolated from Telegram production runtime.
- Use this codebase for Bale-focused changes and high-risk experiments.
- Current transport is still Telegram engine + Bale bridge; Bale-native command intake is next phase.

## 📝 License
MIT License. Created by NightClaw.
