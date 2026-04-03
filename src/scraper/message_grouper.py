"""
Groups raw_messages rows into album candidates.

State machine (messages sorted ascending by message_id):
  - Any TEXT message → starts a new album group
  - Following PHOTO → album cover
  - Following AUDIO or DOCUMENT → audio track
  - Next TEXT → closes current group, opens new one
"""
import logging
from dataclasses import dataclass, field

from src.config import TARGET_GROUP_ID
from src.database.db import (
    get_unprocessed_messages,
    insert_album,
    insert_audio_track,
    mark_message_processed,
)

logger = logging.getLogger(__name__)


@dataclass
class AlbumCandidate:
    info_message_id: int
    raw_text: str
    cover_message_id: int | None = None
    audio_messages: list[dict] = field(default_factory=list)


def group_messages(group_id: int = TARGET_GROUP_ID) -> int:
    """Process unprocessed raw messages and write albums + tracks to DB.
    Returns the number of albums created."""
    rows = get_unprocessed_messages(group_id)
    logger.info("Found %d unprocessed messages", len(rows))

    current: AlbumCandidate | None = None
    albums_created = 0

    def _flush(candidate: AlbumCandidate) -> int:
        album_id = insert_album(
            info_message_id=candidate.info_message_id,
            group_id=group_id,
            raw_text=candidate.raw_text,
            cover_message_id=candidate.cover_message_id,
        )
        mark_message_processed(candidate.info_message_id, album_id)

        if candidate.cover_message_id:
            mark_message_processed(candidate.cover_message_id, album_id)

        for i, track in enumerate(candidate.audio_messages, start=1):
            insert_audio_track(
                album_id=album_id,
                message_id=track["message_id"],
                track_number=i,
                duration_seconds=track.get("duration_seconds"),
                file_size_bytes=track.get("file_size_bytes"),
                mime_type=track.get("mime_type"),
                telegram_file_id=track.get("file_id"),
            )
            mark_message_processed(track["message_id"], album_id)

        return album_id

    for row in rows:
        msg_type = row["message_type"]
        text = row["text_content"]
        msg_id = row["message_id"]

        if msg_type == "text":
            if current is not None:
                _flush(current)
                albums_created += 1
            current = AlbumCandidate(info_message_id=msg_id, raw_text=text or "")

        elif current is not None:
            if msg_type == "photo" and current.cover_message_id is None:
                current.cover_message_id = msg_id

            elif msg_type in ("audio", "document"):
                current.audio_messages.append({
                    "message_id": msg_id,
                    "file_id": row["media_file_id"],
                    "mime_type": None,
                    "duration_seconds": None,
                    "file_size_bytes": None,
                })

    if current is not None:
        _flush(current)
        albums_created += 1

    logger.info("Grouper created %d albums", albums_created)
    return albums_created
