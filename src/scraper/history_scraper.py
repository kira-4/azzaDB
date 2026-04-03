import asyncio
import logging
from datetime import datetime

from hydrogram import Client
from hydrogram.errors import FloodWait

from src.config import TELEGRAM_API_ID, TELEGRAM_API_HASH
from src.database.db import insert_raw_message

logger = logging.getLogger(__name__)

SESSION_NAME = "old/ret_mes"


def _classify_message(message) -> str:
    if message.text:
        return "text"
    if message.photo:
        return "photo"
    if message.audio:
        return "audio"
    if message.document:
        return "document"
    return "other"


def _get_file_id(message) -> str | None:
    if message.photo:
        return message.photo.file_id
    if message.audio:
        return message.audio.file_id
    if message.document:
        return message.document.file_id
    return None


async def scrape_full_history(group_id: int):
    """Iterate all messages in a group and insert them into raw_messages."""
    client = Client(SESSION_NAME, api_id=TELEGRAM_API_ID, api_hash=TELEGRAM_API_HASH)
    inserted = 0
    skipped = 0

    async with client:
        async for message in client.get_chat_history(group_id):
            msg_type = _classify_message(message)
            text = None
            if message.text:
                text = message.text
            elif message.caption:
                text = message.caption

            date = datetime.fromtimestamp(message.date) if message.date else None
            file_id = _get_file_id(message)

            was_inserted = insert_raw_message(
                message_id=message.id,
                group_id=group_id,
                message_type=msg_type,
                text_content=text,
                media_file_id=file_id,
                date=date,
            )
            if was_inserted:
                inserted += 1
            else:
                skipped += 1

            if (inserted + skipped) % 500 == 0:
                logger.info("Progress: %d inserted, %d skipped", inserted, skipped)

    logger.info("Done. Inserted: %d, Skipped (already in DB): %d", inserted, skipped)
    return inserted, skipped
