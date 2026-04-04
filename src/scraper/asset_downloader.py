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
    update_track_name,
)

logger = logging.getLogger(__name__)

SESSION_NAME = "old/ret_mes"

_ILLEGAL_CHARS = r'\/:*?"<>|'


def _sanitize_dir(name: str) -> str:
    """Sanitize for use as a directory name — falls back to 'Unknown'."""
    for ch in _ILLEGAL_CHARS:
        name = name.replace(ch, "")
    return name.strip() or "Unknown"


def _sanitize_name(name: str) -> str:
    """Sanitize for use as a filename — no fallback, caller handles empty."""
    for ch in _ILLEGAL_CHARS:
        name = name.replace(ch, "")
    return name.strip()


def _primary_artist(artists: list) -> str:
    if not artists:
        return "Unknown Artist"
    return _sanitize_dir(artists[0]["name_ar"])


def _album_dir(artist_name: str, album_name: str) -> str:
    return os.path.join(AUDIO_DIR, _sanitize_dir(artist_name), _sanitize_dir(album_name))


async def _fetch_and_save(client: Client, message_id: int, chat_id: int,
                           dest_dir: str) -> tuple[str, str]:
    """Download media into dest_dir. Returns (saved_path, original_stem)."""
    attempt = 0
    while True:
        try:
            message = await client.get_messages(chat_id, message_id)
            saved_path = await client.download_media(message, file_name=dest_dir + "/")
            if not saved_path:
                raise ValueError(f"No media in message {message_id}")

            # Prefer the name Telegram shows in the music player (title > file_name > saved path stem)
            media = getattr(message, "audio", None) or getattr(message, "document", None)
            original_name = None
            if media:
                original_name = getattr(media, "title", None) or getattr(media, "file_name", None)
            stem = (
                os.path.splitext(original_name)[0]
                if original_name
                else os.path.splitext(os.path.basename(saved_path))[0]
            )
            return saved_path, stem
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
                saved, _ = await _fetch_and_save(client, album["cover_message_id"], group_id, album_dir)
                os.replace(saved, cover_dest)
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

            try:
                saved_path, telegram_stem = await _fetch_and_save(
                    client, track["message_id"], group_id, album_dir
                )
                ext = os.path.splitext(saved_path)[1]

                # Use DB name if available, otherwise use Telegram's filename
                track_name = (track["track_name_ar"] or "").strip()
                if not track_name:
                    track_name = telegram_stem
                    update_track_name(track["id"], track_name)

                safe_name = _sanitize_name(track_name)
                final_filename = f"{num:02d} - {safe_name}{ext}" if safe_name else f"{num:02d}{ext}"
                final_path = os.path.join(album_dir, final_filename)
                os.replace(saved_path, final_path)

                update_track_download(track["id"], final_path)
                downloaded += 1
                logger.info("Track: %s", final_path)
            except Exception as e:
                logger.error("Track %d download failed: %s", track["id"], e)

    logger.info("Album %d: downloaded %d/%d tracks", album_id, downloaded, len(tracks))

    if downloaded > 0:
        from src.pipeline.metadata_embedder import embed_metadata_for_album
        embedded = embed_metadata_for_album(album_id)
        logger.info("Album %d: embedded metadata for %d tracks", album_id, embedded)

    return downloaded
