SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS raw_messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id    INTEGER NOT NULL UNIQUE,
    group_id      INTEGER NOT NULL,
    message_type  TEXT CHECK(message_type IN ('text','photo','audio','document','other')),
    text_content  TEXT,
    media_file_id TEXT,
    date          DATETIME,
    album_id      INTEGER REFERENCES albums(id),
    processed     BOOLEAN DEFAULT 0,
    inserted_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS artists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name_ar     TEXT NOT NULL UNIQUE,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS albums (
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
    verification_status  TEXT DEFAULT 'pending'
                         CHECK(verification_status IN ('pending','verified','rejected','needs_review')),
    verified_by          TEXT,
    verified_at          DATETIME,
    rejection_reason     TEXT,
    all_audio_downloaded BOOLEAN DEFAULT 0,
    all_audio_embedded   BOOLEAN DEFAULT 0,

    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS album_artists (
    album_id  INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    artist_id INTEGER NOT NULL REFERENCES artists(id),
    PRIMARY KEY (album_id, artist_id)
);

CREATE TABLE IF NOT EXISTS audio_tracks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id          INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    message_id        INTEGER NOT NULL UNIQUE,
    track_number      INTEGER,
    track_name_ar     TEXT,
    duration_seconds  INTEGER,
    file_size_bytes   INTEGER,
    mime_type         TEXT,
    telegram_file_id  TEXT,
    local_path        TEXT,
    downloaded        BOOLEAN DEFAULT 0,
    metadata_embedded BOOLEAN DEFAULT 0,
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ai_extraction_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id      INTEGER NOT NULL REFERENCES albums(id),
    model_version TEXT,
    raw_response  TEXT,
    extracted_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""
