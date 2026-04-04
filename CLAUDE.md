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
raw_messages → (grouper) → albums (status: pre_screen)
                                  ↓
                         (bot pre-screen card) → Defer or Send to AI
                                  ↓ Send to AI (blocking)
                         (AI extraction) → albums.ai_extracted=1
                                  ↓ (status: pending / needs_review)
                         (bot verification card) → approve / edit / reject
                                  ↓ approve
                         asset_downloader → downloads/audio/{artist}/{album}/
                                  ↓
                         metadata_embedder → tags written into files
```

Deferred albums sit at `verification_status = 'deferred'` until retrieved via `/undefer <id>`.

### Key design decisions

- `raw_messages` is the source of truth. The grouper can be re-run with improved logic without re-scraping Telegram.
- New albums start as `pre_screen`. The human decides whether to send each album to AI, avoiding wasted tokens on unwanted content.
- AI confidence < 0.7 auto-routes albums to `verification_status = 'needs_review'` instead of `'pending'`.
- After each approve/reject the bot automatically sends the next `pre_screen` card, keeping the pipeline fed.
- `asyncio.create_task()` is used in the bot approval handler so downloads run in the background without blocking the verification queue.
- `run_migrations()` must be called after `init_db()` on startup (both entry points do this). It detects and applies the `pre_screen`/`deferred` CHECK constraint migration for existing databases.

### File tree

```
azzaDB/
├── scrape_history.py
├── run_bot.py
├── requirements.txt
├── data/
│   └── azzadb.sqlite
├── downloads/
│   ├── audio/
│   └── covers/
└── src/
    ├── config.py
    ├── ai/
    │   ├── gemini_client.py
    │   └── prompts.py
    ├── bot/
    │   ├── main.py
    │   └── handlers/
    │       ├── prescreen_handler.py
    │       ├── verification_handler.py
    │       └── admin_handler.py
    ├── database/
    │   ├── db.py
    │   └── models.py
    ├── pipeline/
    │   ├── album_pipeline.py
    │   └── metadata_embedder.py
    └── scraper/
        ├── history_scraper.py
        ├── message_grouper.py
        └── asset_downloader.py
```

### File organisation

- `src/config.py` — single source for all env vars and path constants
- `src/database/db.py` — all SQL via a `@contextmanager` `db_conn()` that commits/rolls back automatically
- `src/scraper/message_grouper.py` — state machine: text containing `شريط :` or `إصدار :` starts a new album group
- `src/ai/gemini_client.py` — uses `google.genai` (not deprecated `google.generativeai`); model: `gemini-2.5-flash`
- `src/bot/handlers/prescreen_handler.py` — sends `pre_screen` album cards with Defer/Send to AI buttons; "Send to AI" fires a background `asyncio.create_task` and immediately shows the next pre-screen card; bot notifies when extraction completes; `send_next_prescreen()` is called automatically after each approve/reject
- `src/bot/handlers/verification_handler.py` — PTB `ConversationHandler` for approve/edit/reject flow; `send_album_card(context, album_id)` sends a specific album's card; `send_next_pending()` is used by `/next` for manual queue access
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

- `/screen` — send next pre-screen card (raw message + Defer / Send to AI buttons)
- `/next` — send next pending album card for verification (skips pre-screen queue)
- `/status` — show counts for all statuses (pre_screen, pending, verified, rejected, needs_review, deferred)
- `/deferred` — list all deferred albums with source links
- `/undefer <album_id>` — return a deferred album to the pre-screen queue
- `/retry <album_id>` — re-run AI extraction on a specific album
- `/export` — send CSV of all verified albums

### verification_status values

| Status | Meaning |
|---|---|
| `pre_screen` | Grouped but not yet sent to AI; awaiting human pre-screen |
| `deferred` | Skipped during pre-screen; held for later retrieval |
| `pending` | AI-extracted, confidence ≥ 0.7; ready for verification |
| `needs_review` | AI-extracted, confidence < 0.7; flagged for extra attention |
| `verified` | Approved by human; audio downloaded and tagged |
| `rejected` | Rejected by human with reason |
