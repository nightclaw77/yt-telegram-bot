#!/usr/bin/env bash
set -e

LOCKFILE="/tmp/yt-telegram-bot.lock"
PIDFILE="/tmp/yt-telegram-bot.pid"

# Check if already running
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Bot already running (PID: $OLD_PID)"
        exit 1
    fi
    rm -f "$PIDFILE"
fi

# Create lock
if ! mkdir "$LOCKFILE" 2>/dev/null; then
    echo "Failed to acquire lock"
    exit 1
fi

# Cleanup trap
cleanup() {
    rm -rf "$LOCKFILE"
    rm -f "$PIDFILE"
}
trap cleanup EXIT INT TERM

# Start bot
cd /root/.openclaw/workspace/projects/yt-telegram-bot
echo $$ > "$PIDFILE"
exec ./venv/bin/python main.py
