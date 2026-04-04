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
    flush_grouper_batch,
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

    candidates: list[AlbumCandidate] = []
    current: AlbumCandidate | None = None

    for row in rows:
        msg_type = row["message_type"]
        msg_id = row["message_id"]
        text = row["text_content"]

        if msg_type == "text":
            if current is not None:
                candidates.append(current)
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
        candidates.append(current)

    albums_created = flush_grouper_batch(group_id, candidates)
    logger.info("Grouper created %d albums", albums_created)
    return albums_created
