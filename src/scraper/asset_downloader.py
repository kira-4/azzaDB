import asyncio
import logging
import os

from hydrogram import Client
from hydrogram.errors import FloodWait

from src.config import TELEGRAM_API_ID, TELEGRAM_API_HASH, COVERS_DIR, AUDIO_DIR
from src.database.db import get_album, get_tracks_for_album, update_track_download

logger = logging.getLogger(__name__)

SESSION_NAME = "old/ret_mes"


async def _download_with_backoff(client: Client, message_id: int, chat_id: int, dest_path: str):
    """Download a single file with exponential backoff on FloodWait."""
    attempt = 0
    while True:
        try:
            message = await client.get_messages(chat_id, message_id)
            await client.download_media(message, file_name=dest_path)
            return dest_path
        except FloodWait as e:
            wait = e.value * (2 ** attempt)
            logger.warning("FloodWait: sleeping %ds (attempt %d)", wait, attempt + 1)
            await asyncio.sleep(wait)
            attempt += 1
            if attempt > 5:
                raise


async def download_cover(album_id: int, message_id: int, group_id: int) -> str | None:
    os.makedirs(COVERS_DIR, exist_ok=True)
    dest = os.path.join(COVERS_DIR, f"{album_id}.jpg")
    if os.path.exists(dest):
        return dest

    client = Client(SESSION_NAME, api_id=TELEGRAM_API_ID, api_hash=TELEGRAM_API_HASH)
    async with client:
        try:
            await _download_with_backoff(client, message_id, group_id, dest)
            logger.info("Cover downloaded: %s", dest)
            return dest
        except Exception as e:
            logger.error("Cover download failed for album %d: %s", album_id, e)
            return None


async def download_audio_for_album(album_id: int, group_id: int) -> int:
    """Download all un-downloaded tracks for an album. Returns count downloaded."""
    tracks = get_tracks_for_album(album_id)
    album_audio_dir = os.path.join(AUDIO_DIR, str(album_id))
    os.makedirs(album_audio_dir, exist_ok=True)

    client = Client(SESSION_NAME, api_id=TELEGRAM_API_ID, api_hash=TELEGRAM_API_HASH)
    downloaded = 0

    async with client:
        for track in tracks:
            if track["downloaded"]:
                continue
            track_num = track["track_number"] or 0
            dest = os.path.join(album_audio_dir, f"{track_num:02d}.mp3")
            try:
                await _download_with_backoff(client, track["message_id"], group_id, dest)
                update_track_download(track["id"], dest)
                downloaded += 1
                logger.info("Track downloaded: %s", dest)
            except Exception as e:
                logger.error("Track %d download failed: %s", track["id"], e)

    return downloaded
