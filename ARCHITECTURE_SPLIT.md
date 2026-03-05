# Architecture Split Notes

## Goal
Keep Telegram production bot stable while building Bale-specific behavior in an isolated codebase.

## Projects
- `projects/yt-telegram-bot` => Production Telegram bot (do not use for risky Bale experiments)
- `projects/yt-bale-bot` => Bale-focused project for rapid iteration

## Rules
1. No direct hotfix experiments in Telegram production project.
2. Implement and test Bale-specific logic in this project first.
3. Backport only verified-safe changes to Telegram project.
4. Keep separate `.env`, service unit, logs, and PID files.

## Current State
This project is bootstrapped from latest stable Telegram code and prepared for Bale-first evolution.
