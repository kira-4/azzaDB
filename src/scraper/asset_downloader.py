import asyncio
import logging
import os
import shutil
import time

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

SESSION_NAME = "data/ret_mes"

_ILLEGAL_CHARS = r'\/:*?"<>|'
_ARABIC_RANGE = range(0x0600, 0x06FF + 1)


def _looks_arabic(text: str) -> bool:
    """Return True if text contains enough Arabic-block characters to be readable."""
    arabic_chars = sum(1 for ch in text if ord(ch) in _ARABIC_RANGE)
    return arabic_chars >= max(1, len(text) // 4)


def _fix_encoding(name: str) -> str | None:
    """
    Attempt to fix Windows-1256 mojibake (Arabic bytes read as Latin-1).
    Returns the corrected string if it looks like readable Arabic, else None.
    """
    try:
        fixed = name.encode("latin-1").decode("windows-1256")
        if _looks_arabic(fixed):
            return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return None


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
                           dest_dir: str, progress=None) -> tuple[str, str]:
    """Download media into dest_dir. Returns (saved_path, original_stem)."""
    attempt = 0
    while True:
        try:
            message = await client.get_messages(chat_id, message_id)
            saved_path = await client.download_media(message, file_name=dest_dir + "/", progress=progress)
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


async def download_album(album_id: int, group_id: int, on_progress=None):
    """Download cover + all audio tracks for an album into artist/album/ structure.

    on_progress: optional async callable(track_idx, total_tracks, track_name,
                 current_bytes, total_bytes, speed_bps, eta_secs)
    """
    album = dict(get_album(album_id))
    artists = get_album_artists(album_id)
    tracks = get_tracks_for_album(album_id)

    artist_name = _primary_artist(list(artists))
    album_name = album.get("album_name_ar") or f"album_{album_id}"
    album_dir = _album_dir(artist_name, album_name)
    os.makedirs(album_dir, exist_ok=True)

    client = Client(SESSION_NAME, api_id=TELEGRAM_API_ID, api_hash=TELEGRAM_API_HASH,
                    max_concurrent_transmissions=4)
    async with client:
        # ── Disk space pre-check ───────────────────────────────────────────────
        pending_tracks = get_tracks_for_album(album_id)
        pending_tracks = [t for t in pending_tracks if not t["downloaded"]]
        if pending_tracks:
            size_msgs = await client.get_messages(group_id, [t["message_id"] for t in pending_tracks])
            needed = sum(
                getattr(getattr(m, "audio", None) or getattr(m, "document", None), "file_size", 0) or 0
                for m in (size_msgs if isinstance(size_msgs, list) else [size_msgs])
                if m
            )
            free = shutil.disk_usage(AUDIO_DIR).free
            if needed > 0 and free < needed * 1.05:  # 5% headroom
                raise OSError(
                    f"Not enough disk space for album {album_id}: "
                    f"need {needed / 1024**2:.1f} MB, free {free / 1024**2:.1f} MB"
                )

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
        total_tracks = len(pending_tracks)
        # total_album_bytes already computed by the disk-space pre-check above
        total_album_bytes = needed if pending_tracks else 0

        downloaded = 0
        completed_bytes = 0  # bytes from fully finished tracks
        for track_idx, track in enumerate(pending_tracks, 1):
            num = track["track_number"] or 0
            display_name = (track["track_name_ar"] or "").strip() or f"Track {num or track_idx}"

            track_progress = None
            if on_progress:
                # Signal "starting this track" with album-level offsets
                await on_progress(track_idx, total_tracks, display_name,
                                  completed_bytes, total_album_bytes, 0.0, 0.0)
                _state = [0, time.monotonic()]  # [last_bytes, last_time]
                _done = completed_bytes

                async def _progress_cb(current, total,
                                       _idx=track_idx, _name=display_name, _s=_state,
                                       _prev=_done, _album_total=total_album_bytes):
                    now = time.monotonic()
                    dt = now - _s[1]
                    if dt < 0.3:
                        return
                    speed = (current - _s[0]) / dt if dt > 0 else 0.0
                    album_current = _prev + current
                    eta = (_album_total - album_current) / speed if speed > 0 and _album_total > 0 else 0.0
                    _s[0] = current
                    _s[1] = now
                    await on_progress(_idx, total_tracks, _name, album_current, _album_total, speed, eta)

                track_progress = _progress_cb

            try:
                saved_path, telegram_stem = await _fetch_and_save(
                    client, track["message_id"], group_id, album_dir, progress=track_progress
                )
                ext = os.path.splitext(saved_path)[1]

                # Use DB name if available, otherwise use Telegram's filename
                track_name = (track["track_name_ar"] or "").strip()
                if not track_name:
                    track_name = telegram_stem
                    update_track_name(track["id"], track_name)

                # Fix Windows-1256 mojibake (Arabic bytes misread as Latin-1)
                if track_name and not _looks_arabic(track_name):
                    fixed = _fix_encoding(track_name)
                    if fixed:
                        track_name = fixed
                        update_track_name(track["id"], track_name)
                    else:
                        # Still unreadable — fall back to positional name
                        track_name = f"Track {track_idx:02d}"
                        update_track_name(track["id"], track_name)

                safe_name = _sanitize_name(track_name)
                final_filename = f"{num:02d} - {safe_name}{ext}" if safe_name else f"{num:02d}{ext}"
                final_path = os.path.join(album_dir, final_filename)
                os.replace(saved_path, final_path)

                update_track_download(track["id"], final_path)
                completed_bytes += os.path.getsize(final_path)
                downloaded += 1
                logger.info("Track: %s", final_path)
            except Exception as e:
                logger.error("Track %d download failed: %s", track["id"], e)

    logger.info("Album %d: downloaded %d/%d tracks", album_id, downloaded, len(tracks))

    if downloaded > 0:
        from src.pipeline.metadata_embedder import embed_metadata_for_album
        embedded = embed_metadata_for_album(album_id)
        logger.info("Album %d: embedded metadata for %d tracks", album_id, embedded)

    return downloaded, completed_bytes
