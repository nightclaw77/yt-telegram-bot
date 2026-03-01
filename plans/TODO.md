# YouTube Telegram Bot - Project Plan

## Goal
Build a feature-rich Telegram bot for YouTube downloading, live capturing, and AI summarization.

## Infrastructure
- Directory: `/root/.openclaw/workspace/projects/yt-telegram-bot`
- Core: Python (`aiogram` or `python-telegram-bot`)
- Dependencies: `yt-dlp`, `ffmpeg`, local `ytsummarize` CLI

## Todo
- [ ] Setup project directory and virtual environment
- [ ] Research/Design bot architecture (Async handlers + worker queue)
- [ ] Implement YouTube Metadata Prober (extracting formats and live status)
- [ ] Implement Download Handlers (Video/Audio)
- [ ] Implement Live Capture Logic (`--live-from-start`)
- [ ] Implement AI Summary Integration (calling `/usr/local/bin/ytsummarize`)
- [ ] Add Telegram UI (Inline buttons, Progress bars)
- [ ] Handle Telegram 2GB upload limits (splitting if necessary)
- [ ] Deployment and Environment configuration

## Required from User
- [ ] Telegram Bot Token (from @BotFather)
- [ ] Authorized User IDs (who can use the bot)
