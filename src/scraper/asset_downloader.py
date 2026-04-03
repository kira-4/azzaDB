import asyncio
import logging
import os

from hydrogram import Client
from hydrogram.errors import FloodWait

from src.config import TELEGRAM_API_ID, TELEGRAM_API_HASH, AUDIO_DIR
from src.database.db import (
    get_album,
    get_album_artists,
    get_tracks_for_album,
    update_album_ai_fields,
    update_track_download,
)

logger = logging.getLogger(__name__)

SESSION_NAME = "old/ret_mes"

_ILLEGAL_CHARS = r'\/:*?"<>|'


def _sanitize(name: str) -> str:
    for ch in _ILLEGAL_CHARS:
        name = name.replace(ch, "")
    return name.strip() or "Unknown"


def _primary_artist(artists: list) -> str:
    if not artists:
        return "Unknown Artist"
    return _sanitize(artists[0]["name_ar"])


def _album_dir(artist_name: str, album_name: str) -> str:
    return os.path.join(AUDIO_DIR, _sanitize(artist_name), _sanitize(album_name))


async def _download_to_dir(client: Client, message_id: int, chat_id: int,
                            dest_dir: str, final_name_no_ext: str) -> str:
    """Download a media message into dest_dir, rename to final_name_no_ext + original ext."""
    attempt = 0
    while True:
        try:
            message = await client.get_messages(chat_id, message_id)
            # Save into dir — Hydrogram returns the actual path with original filename/ext
            tmp_path = await client.download_media(message, file_name=dest_dir + "/")
            if not tmp_path:
                raise ValueError(f"No media in message {message_id}")
            ext = os.path.splitext(tmp_path)[1]
            final_path = os.path.join(dest_dir, final_name_no_ext + ext)
            os.replace(tmp_path, final_path)
            return final_path
        except FloodWait as e:
            wait = e.value * (2 ** attempt)
            logger.warning("FloodWait: sleeping %ds (attempt %d)", wait, attempt + 1)
            await asyncio.sleep(wait)
            attempt += 1
            if attempt > 5:
                raise


async def download_album(album_id: int, group_id: int):
    """Download cover + all audio tracks for an album into artist/album/ structure."""
    album = dict(get_album(album_id))
    artists = get_album_artists(album_id)
    tracks = get_tracks_for_album(album_id)

    artist_name = _primary_artist(list(artists))
    album_name = album.get("album_name_ar") or f"album_{album_id}"
    album_dir = _album_dir(artist_name, album_name)
    os.makedirs(album_dir, exist_ok=True)

    client = Client(SESSION_NAME, api_id=TELEGRAM_API_ID, api_hash=TELEGRAM_API_HASH)
    async with client:
        # ── Cover ──────────────────────────────────────────────────────────────
        if album.get("cover_message_id") and not album.get("cover_downloaded"):
            cover_dest = os.path.join(album_dir, "cover.jpg")
            try:
                message = await client.get_messages(group_id, album["cover_message_id"])
                tmp = await client.download_media(message, file_name=album_dir + "/")
                if tmp:
                    os.replace(tmp, cover_dest)
                    update_album_ai_fields(album_id, {
                        "cover_local_path": cover_dest,
                        "cover_downloaded": 1,
                    })
                    logger.info("Cover: %s", cover_dest)
            except Exception as e:
                logger.error("Cover download failed for album %d: %s", album_id, e)

        # ── Tracks ─────────────────────────────────────────────────────────────
        downloaded = 0
        for track in tracks:
            if track["downloaded"]:
                continue
            num = track["track_number"] or 0
            name = _sanitize(track["track_name_ar"] or "")
            filename = f"{num:02d} - {name}" if name else f"{num:02d}"

            try:
                path = await _download_to_dir(
                    client, track["message_id"], group_id, album_dir, filename
                )
                update_track_download(track["id"], path)
                downloaded += 1
                logger.info("Track: %s", path)
            except Exception as e:
                logger.error("Track %d download failed: %s", track["id"], e)

    logger.info("Album %d: downloaded %d/%d tracks", album_id, downloaded, len(tracks))
    return downloaded
