import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

from src.config import DATABASE_PATH
from src.database.models import SCHEMA_SQL


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def db_conn():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db_conn() as conn:
        conn.executescript(SCHEMA_SQL)


def run_migrations():
    """Apply pending schema migrations to an existing database."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='albums'"
        ).fetchone()
        if not row or 'pre_screen' in row[0]:
            return  # fresh install or already migrated

        logger.info("DB migration: expanding verification_status CHECK constraint")
        conn.executescript("""
            PRAGMA foreign_keys=OFF;
            BEGIN;
            CREATE TABLE albums_new (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                info_message_id      INTEGER NOT NULL UNIQUE,
                cover_message_id     INTEGER,
                telegram_group_id    INTEGER NOT NULL,
                raw_text             TEXT NOT NULL,
                album_type           TEXT,
                album_name_ar        TEXT,
                occasion_ar          TEXT,
                hijri_date           TEXT,
                hijri_month          TEXT,
                hijri_day            TEXT,
                location_ar          TEXT,
                city_ar              TEXT,
                audio_engineer       TEXT,
                recording_engineer   TEXT,
                notes_ar             TEXT,
                cover_local_path     TEXT,
                cover_downloaded     BOOLEAN DEFAULT 0,
                ai_extracted         BOOLEAN DEFAULT 0,
                ai_confidence        REAL,
                verification_status  TEXT DEFAULT 'pre_screen'
                                     CHECK(verification_status IN ('pre_screen','pending','verified','rejected','needs_review','deferred')),
                verified_by          TEXT,
                verified_at          DATETIME,
                rejection_reason     TEXT,
                all_audio_downloaded BOOLEAN DEFAULT 0,
                all_audio_embedded   BOOLEAN DEFAULT 0,
                created_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at           DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO albums_new SELECT * FROM albums;
            UPDATE albums_new
               SET verification_status = 'pre_screen'
             WHERE ai_extracted = 0 AND verification_status = 'pending';
            DROP TABLE albums;
            ALTER TABLE albums_new RENAME TO albums;
            COMMIT;
            PRAGMA foreign_keys=ON;
        """)
        logger.info("DB migration complete.")
    finally:
        conn.close()


# ─── raw_messages ────────────────────────────────────────────────────────────

def insert_raw_message(message_id: int, group_id: int, message_type: str,
                        text_content: Optional[str], media_file_id: Optional[str],
                        date: Optional[datetime]) -> bool:
    """Insert a raw message. Returns True if inserted, False if already exists."""
    with db_conn() as conn:
        try:
            conn.execute(
                """INSERT INTO raw_messages
                   (message_id, group_id, message_type, text_content, media_file_id, date)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (message_id, group_id, message_type, text_content, media_file_id, date),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def get_unprocessed_messages(group_id: int) -> list[sqlite3.Row]:
    with db_conn() as conn:
        return conn.execute(
            "SELECT * FROM raw_messages WHERE group_id=? AND processed=0 ORDER BY message_id ASC",
            (group_id,),
        ).fetchall()


def mark_message_processed(message_id: int, album_id: Optional[int] = None):
    with db_conn() as conn:
        conn.execute(
            "UPDATE raw_messages SET processed=1, album_id=? WHERE message_id=?",
            (album_id, message_id),
        )


# ─── artists ─────────────────────────────────────────────────────────────────

def get_or_create_artist(name_ar: str) -> int:
    with db_conn() as conn:
        row = conn.execute("SELECT id FROM artists WHERE name_ar=?", (name_ar,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute("INSERT INTO artists (name_ar) VALUES (?)", (name_ar,))
        return cur.lastrowid


# ─── albums ──────────────────────────────────────────────────────────────────

def insert_album(info_message_id: int, group_id: int, raw_text: str,
                 cover_message_id: Optional[int] = None) -> int:
    with db_conn() as conn:
        cur = conn.execute(
            """INSERT INTO albums (info_message_id, cover_message_id, telegram_group_id, raw_text)
               VALUES (?, ?, ?, ?)""",
            (info_message_id, cover_message_id, group_id, raw_text),
        )
        return cur.lastrowid


def get_album(album_id: int) -> Optional[sqlite3.Row]:
    with db_conn() as conn:
        return conn.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone()


def get_albums_pending_ai() -> list[sqlite3.Row]:
    with db_conn() as conn:
        return conn.execute(
            "SELECT * FROM albums WHERE ai_extracted=0 AND verification_status!='deferred' ORDER BY id ASC"
        ).fetchall()


def get_albums_pending_prescreen() -> list[sqlite3.Row]:
    with db_conn() as conn:
        return conn.execute(
            "SELECT * FROM albums WHERE verification_status='pre_screen' ORDER BY id ASC"
        ).fetchall()


def get_albums_deferred() -> list[sqlite3.Row]:
    with db_conn() as conn:
        return conn.execute(
            "SELECT * FROM albums WHERE verification_status='deferred' ORDER BY id ASC"
        ).fetchall()


def get_albums_pending_verification() -> list[sqlite3.Row]:
    with db_conn() as conn:
        return conn.execute(
            "SELECT * FROM albums WHERE verification_status='pending' AND ai_extracted=1 ORDER BY id ASC"
        ).fetchall()


def update_album_ai_fields(album_id: int, fields: dict):
    set_clause = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [album_id]
    with db_conn() as conn:
        conn.execute(
            f"UPDATE albums SET {set_clause}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            values,
        )


def set_album_verification(album_id: int, status: str, verified_by: str,
                            rejection_reason: Optional[str] = None):
    with db_conn() as conn:
        conn.execute(
            """UPDATE albums SET verification_status=?, verified_by=?, verified_at=CURRENT_TIMESTAMP,
               rejection_reason=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (status, verified_by, rejection_reason, album_id),
        )


def get_verification_stats() -> dict:
    with db_conn() as conn:
        row = conn.execute(
            """SELECT
               SUM(CASE WHEN verification_status='pre_screen' THEN 1 ELSE 0 END) AS pre_screen,
               SUM(CASE WHEN verification_status='pending' THEN 1 ELSE 0 END) AS pending,
               SUM(CASE WHEN verification_status='verified' THEN 1 ELSE 0 END) AS verified,
               SUM(CASE WHEN verification_status='rejected' THEN 1 ELSE 0 END) AS rejected,
               SUM(CASE WHEN verification_status='needs_review' THEN 1 ELSE 0 END) AS needs_review,
               SUM(CASE WHEN verification_status='deferred' THEN 1 ELSE 0 END) AS deferred,
               COUNT(*) AS total
               FROM albums"""
        ).fetchone()
        return dict(row)


# ─── album_artists ────────────────────────────────────────────────────────────

def link_album_artist(album_id: int, artist_id: int):
    with db_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO album_artists (album_id, artist_id) VALUES (?, ?)",
                (album_id, artist_id),
            )
        except sqlite3.IntegrityError:
            pass


def set_album_artists(album_id: int, artist_names: list[str]):
    """Replace all artists for an album with the given list of names."""
    with db_conn() as conn:
        conn.execute("DELETE FROM album_artists WHERE album_id=?", (album_id,))
        for name in artist_names:
            name = name.strip()
            if not name:
                continue
            row = conn.execute("SELECT id FROM artists WHERE name_ar=?", (name,)).fetchone()
            if row:
                artist_id = row["id"]
            else:
                cur = conn.execute("INSERT INTO artists (name_ar) VALUES (?)", (name,))
                artist_id = cur.lastrowid
            try:
                conn.execute(
                    "INSERT INTO album_artists (album_id, artist_id) VALUES (?, ?)",
                    (album_id, artist_id),
                )
            except sqlite3.IntegrityError:
                pass


def get_album_artists(album_id: int) -> list[sqlite3.Row]:
    with db_conn() as conn:
        return conn.execute(
            """SELECT a.* FROM artists a
               JOIN album_artists aa ON aa.artist_id = a.id
               WHERE aa.album_id=?""",
            (album_id,),
        ).fetchall()


# ─── audio_tracks ─────────────────────────────────────────────────────────────

def insert_audio_track(album_id: int, message_id: int, track_number: int,
                        duration_seconds: Optional[int], file_size_bytes: Optional[int],
                        mime_type: Optional[str], telegram_file_id: Optional[str]) -> int:
    with db_conn() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO audio_tracks
                   (album_id, message_id, track_number, duration_seconds, file_size_bytes,
                    mime_type, telegram_file_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (album_id, message_id, track_number, duration_seconds,
                 file_size_bytes, mime_type, telegram_file_id),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT id FROM audio_tracks WHERE message_id=?", (message_id,)
            ).fetchone()
            return row["id"]


def get_tracks_for_album(album_id: int) -> list[sqlite3.Row]:
    with db_conn() as conn:
        return conn.execute(
            "SELECT * FROM audio_tracks WHERE album_id=? ORDER BY track_number ASC",
            (album_id,),
        ).fetchall()


def update_track_download(track_id: int, local_path: str):
    with db_conn() as conn:
        conn.execute(
            "UPDATE audio_tracks SET downloaded=1, local_path=? WHERE id=?",
            (local_path, track_id),
        )


def update_track_name(track_id: int, name: str):
    with db_conn() as conn:
        conn.execute(
            "UPDATE audio_tracks SET track_name_ar=? WHERE id=?",
            (name, track_id),
        )


def update_track_embedded(track_id: int):
    with db_conn() as conn:
        conn.execute(
            "UPDATE audio_tracks SET metadata_embedded=1 WHERE id=?", (track_id,)
        )


# ─── grouper batch write ──────────────────────────────────────────────────────

def flush_grouper_batch(group_id: int, candidates: list) -> int:
    """Write all AlbumCandidates to DB in a single transaction.
    Returns the number of albums inserted."""
    with db_conn() as conn:
        albums_created = 0
        for candidate in candidates:
            cur = conn.execute(
                """INSERT INTO albums (info_message_id, cover_message_id, telegram_group_id, raw_text)
                   VALUES (?, ?, ?, ?)""",
                (candidate.info_message_id, candidate.cover_message_id, group_id, candidate.raw_text),
            )
            album_id = cur.lastrowid
            albums_created += 1

            conn.execute(
                "UPDATE raw_messages SET processed=1, album_id=? WHERE message_id=?",
                (album_id, candidate.info_message_id),
            )
            if candidate.cover_message_id:
                conn.execute(
                    "UPDATE raw_messages SET processed=1, album_id=? WHERE message_id=?",
                    (album_id, candidate.cover_message_id),
                )
            for i, track in enumerate(candidate.audio_messages, start=1):
                conn.execute(
                    """INSERT INTO audio_tracks
                       (album_id, message_id, track_number, duration_seconds,
                        file_size_bytes, mime_type, telegram_file_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (album_id, track["message_id"], i, track.get("duration_seconds"),
                     track.get("file_size_bytes"), track.get("mime_type"), track.get("file_id")),
                )
                conn.execute(
                    "UPDATE raw_messages SET processed=1, album_id=? WHERE message_id=?",
                    (album_id, track["message_id"]),
                )
        return albums_created


# ─── ai_extraction_log ────────────────────────────────────────────────────────

def log_ai_extraction(album_id: int, model_version: str, raw_response: str):
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO ai_extraction_log (album_id, model_version, raw_response) VALUES (?, ?, ?)",
            (album_id, model_version, raw_response),
        )
