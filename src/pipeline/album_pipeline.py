"""
Orchestrates: AI extraction → (optional) cover download
Run this after the grouper has populated the albums table.
"""
import asyncio
import logging

from src.config import TARGET_GROUP_ID, VERIFICATION_CHAT_ID
from src.database.db import get_albums_pending_ai, get_album, update_album_ai_fields
from src.ai.gemini_client import extract_metadata
from src.database.db import get_or_create_artist, link_album_artist

logger = logging.getLogger(__name__)


async def run_ai_extraction_batch(limit: int = 50) -> int:
    """Extract metadata for up to `limit` albums that haven't been processed yet.
    Returns the number of successfully extracted albums."""
    albums = get_albums_pending_ai()[:limit]
    logger.info("Running AI extraction on %d albums", len(albums))
    success = 0

    for album_row in albums:
        album_id = album_row["id"]
        try:
            result = await extract_metadata(album_id, album_row["raw_text"])

            # Persist artists
            for name in result.artists:
                name = name.strip()
                if name:
                    artist_id = get_or_create_artist(name)
                    link_album_artist(album_id, artist_id)

            success += 1
        except Exception as e:
            logger.error("AI extraction failed for album %d: %s", album_id, e)

    logger.info("AI extraction complete: %d/%d succeeded", success, len(albums))
    return success


async def notify_pending_to_bot():
    """Trigger the bot to send the next pending album for verification."""
    from telegram import Bot
    from src.config import TELEGRAM_BOT_TOKEN

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    albums = __import__("src.database.db", fromlist=["get_albums_pending_verification"]).get_albums_pending_verification()
    if not albums:
        logger.info("No pending albums to notify about.")
        return
    logger.info("%d albums pending verification.", len(albums))
