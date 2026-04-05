import asyncio
import logging
import os
import shutil
import time

from hydrogram import Client
from hydrogram.errors import AuthBytesInvalid, AuthKeyUnregistered, FloodWait

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

# Max simultaneous track downloads.  Keep in sync with max_concurrent_transmissions
# (part-level parallelism inside Hydrogram) — 4 × 4 = 16 concurrent TCP chunks.
CONCURRENCY = 4


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
        except (AuthBytesInvalid, AuthKeyUnregistered) as e:
            # Parallel downloads race to ExportAuthorization for the same DC.
            # The losing coroutine gets AUTH_BYTES_INVALID or AUTH_KEY_UNREGISTERED.
            # After a short back-off the winning coroutine has established the DC
            # session and this retry will succeed.
            if attempt >= 3:
                raise
            wait = 2 ** attempt
            logger.warning("DC auth race (%s): retry %d in %ds", type(e).__name__, attempt + 1, wait)
            await asyncio.sleep(wait)
            attempt += 1


async def download_album(album_id: int, group_id: int, on_progress=None):
    """Download cover + all audio tracks for an album into artist/album/ structure.

    on_progress: optional async callable(completed_count, total_tracks, active_count,
                 current_bytes, total_bytes, speed_bps, eta_secs)
      completed_count — tracks fully finished
      active_count    — tracks currently downloading (semaphore acquired)
    """
    album = dict(get_album(album_id))
    artists = get_album_artists(album_id)
    tracks = get_tracks_for_album(album_id)

    artist_name = _primary_artist(list(artists))
    album_name = album.get("album_name_ar") or f"album_{album_id}"
    album_dir = _album_dir(artist_name, album_name)
    os.makedirs(album_dir, exist_ok=True)

    client = Client(SESSION_NAME, api_id=TELEGRAM_API_ID, api_hash=TELEGRAM_API_HASH,
                    max_concurrent_transmissions=CONCURRENCY)
    async with client:
        # ── Disk space pre-check ───────────────────────────────────────────────
        pending_tracks = [t for t in tracks if not t["downloaded"]]
        total_album_bytes = 0
        if pending_tracks:
            size_msgs = await client.get_messages(group_id, [t["message_id"] for t in pending_tracks])
            total_album_bytes = sum(
                getattr(getattr(m, "audio", None) or getattr(m, "document", None), "file_size", 0) or 0
                for m in (size_msgs if isinstance(size_msgs, list) else [size_msgs])
                if m
            )
            free = shutil.disk_usage(AUDIO_DIR).free
            if total_album_bytes > 0 and free < total_album_bytes * 1.05:
                raise OSError(
                    f"Not enough disk space for album {album_id}: "
                    f"need {total_album_bytes / 1024**2:.1f} MB, free {free / 1024**2:.1f} MB"
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

        # ── Parallel track downloads ───────────────────────────────────────────
        total_tracks = len(pending_tracks)
        if not pending_tracks:
            return 0, 0

        # Aggregate progress state — asyncio is single-threaded so no locks needed,
        # but _last_report must be written BEFORE any await to prevent duplicate fires.
        _track_bytes: dict[int, int] = {}  # message_id → in-flight bytes (semaphore held)
        _completed_bytes = 0
        _completed_count = 0

        _speed_t = time.monotonic()
        _speed_base = 0          # total bytes at last speed sample
        _current_speed = 0.0
        _last_report = 0.0
        _REPORT_INTERVAL = 0.5   # compute speed every 0.5 s; bot-side throttle handles Telegram edits

        async def _report():
            nonlocal _speed_t, _speed_base, _current_speed, _last_report
            if not on_progress:
                return
            now = time.monotonic()
            if now - _last_report < _REPORT_INTERVAL:
                return
            # Set _last_report before the await so a concurrent callback sees it immediately.
            _last_report = now

            total_now = _completed_bytes + sum(_track_bytes.values())
            dt = now - _speed_t
            if dt >= 0.3:
                _current_speed = (total_now - _speed_base) / dt
                _speed_base = total_now
                _speed_t = now

            remaining = total_album_bytes - total_now
            eta = remaining / _current_speed if _current_speed > 0 and remaining > 0 else 0.0
            await on_progress(
                _completed_count, total_tracks, len(_track_bytes),
                total_now, total_album_bytes, _current_speed, eta,
            )

        sem = asyncio.Semaphore(CONCURRENCY)
        downloaded_count = 0
        downloaded_bytes = 0

        async def download_one(track_idx: int, track: dict):
            nonlocal _completed_bytes, _completed_count, downloaded_count, downloaded_bytes

            msg_id = track["message_id"]
            num = track["track_number"] or 0
            display_name = (track["track_name_ar"] or "").strip() or f"Track {num or track_idx}"

            async def _progress_cb(current: int, total: int):
                _track_bytes[msg_id] = current
                await _report()

            async with sem:
                # Only register in _track_bytes once the semaphore is acquired
                # so active_count reflects truly downloading tracks.
                _track_bytes[msg_id] = 0
                try:
                    saved_path, telegram_stem = await _fetch_and_save(
                        client, msg_id, group_id, album_dir, progress=_progress_cb
                    )
                    ext = os.path.splitext(saved_path)[1]

                    track_name = (track["track_name_ar"] or "").strip()
                    if not track_name:
                        track_name = telegram_stem
                        update_track_name(track["id"], track_name)

                    if track_name and not _looks_arabic(track_name):
                        fixed = _fix_encoding(track_name)
                        if fixed:
                            track_name = fixed
                            update_track_name(track["id"], track_name)
                        else:
                            track_name = f"Track {track_idx:02d}"
                            update_track_name(track["id"], track_name)

                    safe_name = _sanitize_name(track_name)
                    final_filename = f"{num:02d} - {safe_name}{ext}" if safe_name else f"{num:02d}{ext}"
                    final_path = os.path.join(album_dir, final_filename)
                    os.replace(saved_path, final_path)

                    update_track_download(track["id"], final_path)

                    file_size = os.path.getsize(final_path)
                    # Remove from in-flight BEFORE adding to completed so _report
                    # never double-counts this track.
                    _track_bytes.pop(msg_id, None)
                    _completed_bytes += file_size
                    _completed_count += 1
                    downloaded_count += 1
                    downloaded_bytes += file_size
                    logger.info("Track: %s", final_path)
                except Exception as e:
                    _track_bytes.pop(msg_id, None)
                    logger.error("Track %d download failed: %s", track["id"], e)

        await asyncio.gather(
            *(download_one(i + 1, t) for i, t in enumerate(pending_tracks)),
            return_exceptions=True,
        )

    logger.info("Album %d: downloaded %d/%d tracks", album_id, downloaded_count, len(tracks))

    if downloaded_count > 0:
        from src.pipeline.metadata_embedder import embed_metadata_for_album
        embedded = embed_metadata_for_album(album_id)
        logger.info("Album %d: embedded metadata for %d tracks", album_id, embedded)

    return downloaded_count, downloaded_bytes
