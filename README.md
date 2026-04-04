# azzaDB

A pipeline for archiving Shia latmyaat (Islamic audio recordings) from a private Telegram group.

It scrapes raw messages via the Telegram MTProto API, groups them into albums, sends each album through an AI metadata extraction step (Gemini), routes results to a human-in-the-loop Telegram bot for verification, then downloads and ID3-tags the audio files.

---

## Prerequisites

- Python 3.11+
- A Telegram account (for the MTProto scraper)
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- A Google Gemini API key (from [aistudio.google.com](https://aistudio.google.com))

---

## Installation

```bash
git clone <repo-url>
cd azzaDB
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

> `hydrogram[fast]` is included in requirements. It installs TgCrypto (C-accelerated MTProto crypto) and uvloop (faster async event loop) automatically.

---

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```env
TELEGRAM_API_ID=          # from https://my.telegram.org
TELEGRAM_API_HASH=        # from https://my.telegram.org
TARGET_GROUP_ID=          # negative int, e.g. -1001869042104
TELEGRAM_BOT_TOKEN=       # from @BotFather
VERIFICATION_CHAT_ID=     # your personal Telegram user ID (not a bot ID)
ADMIN_USER_IDS=           # comma-separated user IDs, e.g. 123456,789012
GEMINI_API_KEY=           # from aistudio.google.com
```

### First-time Hydrogram authentication

The scraper uses a Hydrogram (MTProto) user client. On the very first run it will prompt for your phone number and a login code:

```bash
python scrape_history.py --limit 1
```

This creates `old/ret_mes.session`. All subsequent runs reuse it silently.

---

## Pipeline

The pipeline has three stages that can be run independently:

```
raw_messages → (grouper) → albums (pre_screen)
                                  ↓
                         (bot pre-screen) → Defer or Send to AI
                                  ↓
                         (Gemini extraction) → pending / needs_review
                                  ↓
                         (bot verification) → approve / edit / reject
                                  ↓ approve
                         asset_downloader → downloads/audio/{artist}/{album}/
                                  ↓
                         metadata_embedder → ID3/MP4 tags written into files
```

---

## CLI Commands

### `scrape_history.py`

```bash
# Full run: scrape latest N messages, then group into albums
python scrape_history.py --limit 400

# Scrape only, skip grouping
python scrape_history.py --limit 400 --skip-group

# Re-run grouper on already-scraped messages (no Telegram connection needed)
python scrape_history.py --skip-scrape

# Run AI extraction batch on all un-extracted albums (no scrape, no group)
python scrape_history.py --skip-scrape --skip-group --ai
```

### `run_bot.py`

```bash
# Start the verification bot (persistent process)
python run_bot.py
```

---

## Bot Commands

All commands are admin-only (controlled by `ADMIN_USER_IDS`).

| Command | Description |
|---|---|
| `/screen` | Send the next pre-screen card (raw message + Defer / Send to AI) |
| `/next` | Send the next pending album card for full verification |
| `/status` | Show counts for all pipeline statuses |
| `/deferred` | List all deferred albums with Telegram source links |
| `/undefer <id>` | Return a deferred album to the pre-screen queue |
| `/retry <id>` | Re-run AI extraction on a specific album |
| `/export` | Send a CSV of all verified albums |

### Pre-screen buttons

When `/screen` sends a card, two inline buttons appear:

- **Defer** — moves album to `deferred`; auto-sends next pre-screen card
- **Send to AI** — fires background Gemini extraction; immediately shows next pre-screen card; bot sends a completion notification when done

### Verification buttons

When `/next` sends a card, three inline buttons appear:

- **Approve** — sets `verified`; starts background download + metadata embedding; shows next album
- **Edit** — opens a field picker (tap any field, bot replies with ForceReply pre-filled with current value; send new value; repeat; tap Done to return to the card)
- **Reject** — asks for a rejection reason text; sets `rejected`; shows next album

---

## Album Status Values

| Status | Meaning |
|---|---|
| `pre_screen` | Grouped but not yet sent to AI; awaiting human pre-screen decision |
| `deferred` | Skipped during pre-screen; held for later retrieval via `/undefer` |
| `pending` | AI-extracted, confidence ≥ 0.7; ready for human verification |
| `needs_review` | AI-extracted, confidence < 0.7; flagged for extra attention |
| `verified` | Approved by human; audio downloaded and tagged |
| `rejected` | Rejected by human; rejection reason stored |

---

## File Layout

```
azzaDB/
├── scrape_history.py         # CLI entry point: scrape + group + AI batch
├── run_bot.py                # Bot entry point
├── requirements.txt
├── .env                      # secrets (gitignored)
├── data/
│   └── azzadb.sqlite         # SQLite DB (WAL mode)
├── downloads/
│   ├── audio/                # downloaded tracks: audio/{artist}/{album}/
│   └── covers/               # cover art
└── src/
    ├── config.py             # all env vars and path constants
    ├── ai/
    │   ├── gemini_client.py  # google.genai; model: gemini-2.5-flash
    │   └── prompts.py
    ├── bot/
    │   ├── main.py
    │   └── handlers/
    │       ├── prescreen_handler.py   # Defer / Send to AI flow
    │       ├── verification_handler.py # Approve / Edit / Reject flow
    │       └── admin_handler.py        # all /commands
    ├── database/
    │   ├── db.py             # all SQL via db_conn() context manager
    │   └── models.py         # schema SQL
    ├── pipeline/
    │   ├── album_pipeline.py     # AI extraction orchestrator
    │   └── metadata_embedder.py  # ID3 (MP3) + MP4 tag writer
    └── scraper/
        ├── history_scraper.py    # Hydrogram MTProto scraper
        ├── message_grouper.py    # state machine: شريط : / إصدار : triggers new album
        └── asset_downloader.py   # downloads cover + tracks; FloodWait backoff
```

---

## Key Design Notes

- **`raw_messages` is the source of truth.** Re-run the grouper any time with `--skip-scrape` to improve grouping logic without re-hitting Telegram.
- **Pre-screen gates AI spend.** Only albums the human approves reach Gemini, avoiding wasted tokens on noise.
- **Downloads are non-blocking.** `asyncio.create_task()` runs downloads in the background; the verification queue keeps moving.
- **WAL mode** on SQLite lets the scraper and the bot share the same database safely.
- **`run_migrations()`** must be called after `init_db()` on startup. It detects and applies the `pre_screen`/`deferred` status migration for existing databases automatically.
- **Tracks are named** using the Telegram audio title field first, then `file_name`, then the saved path stem — in that priority order.
- **Metadata tags**: genre is hardcoded to `لطميات`; Hijri date is converted to Gregorian year for the TDRC/`©day` tag; artist names are joined with `"; "`.
