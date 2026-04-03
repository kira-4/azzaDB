# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

azzaDB is a pipeline for archiving Shia latmyaat (Islamic audio recordings) from a Telegram group. It scrapes messages, extracts metadata with AI, routes albums through a human verification bot, then downloads and tags the audio files.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Initialise DB + scrape latest N messages + group into albums
python scrape_history.py --limit 400

# Scrape only (skip grouping)
python scrape_history.py --limit 400 --skip-group

# Re-run grouper on already-scraped messages (no Telegram connection needed)
python scrape_history.py --skip-scrape

# Run AI extraction batch on all un-extracted albums
python scrape_history.py --skip-scrape --skip-group --ai

# Start the verification bot
python run_bot.py
```

## Architecture

Two separate long-running processes share a single SQLite database (`data/azzadb.sqlite`) via WAL mode:

- **`scrape_history.py`** — one-shot CLI. Uses Hydrogram (MTProto user client) to read historical messages. Bot API cannot access message history, so Hydrogram is required and kept even though the rest of the bot uses python-telegram-bot.
- **`run_bot.py`** — persistent bot. Uses python-telegram-bot for the human verification UI.

### Data flow

```
raw_messages → (grouper) → albums + audio_tracks
                                  ↓
                         (AI extraction) → albums.ai_extracted=1
                                  ↓
                         (bot /next) → human reviews card
                                  ↓ approve
                         asset_downloader → downloads/audio/{artist}/{album}/
                                  ↓
                         metadata_embedder → tags written into files
```

### Key design decisions

- `raw_messages` is the source of truth. The grouper can be re-run with improved logic without re-scraping Telegram.
- The Hydrogram session file lives at `old/ret_mes.session`. If it's invalidated (AUTH_KEY_UNREGISTERED), delete it and run the scraper interactively to re-authenticate.
- AI confidence < 0.7 auto-routes albums to `verification_status = 'needs_review'` instead of `'pending'`.
- `asyncio.create_task()` is used in the bot approval handler so downloads run in the background without blocking the verification queue.

### File organisation

- `src/config.py` — single source for all env vars and path constants
- `src/database/db.py` — all SQL via a `@contextmanager` `db_conn()` that commits/rolls back automatically
- `src/scraper/message_grouper.py` — state machine: text containing `شريط :` or `إصدار :` starts a new album group
- `src/ai/gemini_client.py` — uses `google.genai` (not deprecated `google.generativeai`); model: `gemini-2.5-flash`
- `src/bot/handlers/verification_handler.py` — PTB `ConversationHandler` for approve/edit/reject flow; `send_next_pending()` is the shared function called by both the `/next` command and the approve callback
- `src/scraper/asset_downloader.py` — downloads to `artist/album/` structure; uses Telegram's original filename as track name fallback when `track_name_ar` is null in DB
- `src/pipeline/metadata_embedder.py` — uses `mutagen.mp3.MP3` (not raw `ID3`) for MP3 tagging; genre is always `لطميات`; Hijri date is converted to Gregorian year via `hijri-converter`

### .env variables

```
TELEGRAM_API_ID      # from my.telegram.org
TELEGRAM_API_HASH    # from my.telegram.org
TARGET_GROUP_ID      # negative int, e.g. -1001869042104
TELEGRAM_BOT_TOKEN   # from @BotFather
VERIFICATION_CHAT_ID # your personal Telegram user ID (not a bot ID)
ADMIN_USER_IDS       # comma-separated user IDs, e.g. 123456,789012
GEMINI_API_KEY       # from aistudio.google.com
```

### Bot commands

- `/next` — send next pending album card for review
- `/status` — show pending/verified/rejected counts
- `/retry <album_id>` — re-run AI extraction on a specific album
- `/export` — send CSV of all verified albums
