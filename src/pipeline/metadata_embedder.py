"""
Embeds ID3/MP4 tags into downloaded audio files using mutagen.
"""
import logging
import os
import re

from mutagen.id3 import (
    ID3,
    TIT2, TPE1, TALB, TDRC, TCON, COMM, TRCK, APIC,
    ID3NoHeaderError,
)
from mutagen.mp4 import MP4, MP4Cover

from src.database.db import get_album, get_album_artists, get_tracks_for_album, update_track_embedded

logger = logging.getLogger(__name__)

GENRE = "لطميات"

_HIJRI_MONTHS = {
    "محرم": 1, "صفر": 2,
    "ربيع الأول": 3, "ربيع الثاني": 4,
    "جمادى الأول": 5, "جمادى الأولى": 5,
    "جمادى الآخر": 6, "جمادى الثاني": 6, "جمادى الثانية": 6,
    "رجب": 7, "شعبان": 8, "رمضان": 9,
    "شوال": 10, "ذو القعدة": 11, "ذو الحجة": 12,
}


def _hijri_to_gregorian_year(album: dict) -> str:
    """Convert Hijri date fields to a Gregorian year string for metadata."""
    try:
        from hijri_converter import Hijri

        # Parse Hijri year from "1439 هـ"
        raw_year = (album.get("hijri_date") or "").replace("هـ", "").strip()
        if not raw_year:
            return ""
        h_year = int(raw_year)

        # Parse month (name → number)
        h_month = 6  # mid-year fallback
        month_str = (album.get("hijri_month") or "").strip()
        for name, num in _HIJRI_MONTHS.items():
            if name in month_str:
                h_month = num
                break

        # Parse day — extract first number from e.g. "ليلة 13"
        h_day = 15  # mid-month fallback
        day_str = album.get("hijri_day") or ""
        day_match = re.search(r"\d+", day_str)
        if day_match:
            h_day = min(int(day_match.group()), 29)  # Hijri months max 30 days

        gregorian = Hijri(h_year, h_month, h_day).to_gregorian()
        return str(gregorian.year)

    except Exception as e:
        logger.warning("Hijri conversion failed: %s", e)
        return ""


def _load_cover_bytes(cover_path: str | None) -> bytes | None:
    if not cover_path or not os.path.exists(cover_path):
        return None
    with open(cover_path, "rb") as f:
        return f.read()


def _embed_mp3(track_path: str, album: dict, artists: list, track_number: int,
               cover_bytes: bytes | None, gregorian_year: str):
    try:
        tags = ID3(track_path)
    except ID3NoHeaderError:
        tags = ID3()

    artist_str = "; ".join(a["name_ar"] for a in artists)

    tags[TIT2.__name__] = TIT2(encoding=3, text=album.get("album_name_ar") or "")
    tags[TPE1.__name__] = TPE1(encoding=3, text=artist_str)
    tags[TALB.__name__] = TALB(encoding=3, text=album.get("album_name_ar") or "")
    tags[TCON.__name__] = TCON(encoding=3, text=GENRE)
    tags[TDRC.__name__] = TDRC(encoding=3, text=gregorian_year)
    tags[TRCK.__name__] = TRCK(encoding=3, text=str(track_number))

    comment_parts = [p for p in [album.get("occasion_ar"), album.get("location_ar")] if p]
    tags[COMM.__name__] = COMM(
        encoding=3, lang="ara", desc="", text=" | ".join(comment_parts)
    )

    if cover_bytes:
        tags[APIC.__name__] = APIC(
            encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover_bytes
        )

    tags.save(track_path, v2_version=4)


def _embed_m4a(track_path: str, album: dict, artists: list, track_number: int,
               cover_bytes: bytes | None, gregorian_year: str):
    tags = MP4(track_path)
    artist_str = "; ".join(a["name_ar"] for a in artists)

    tags["\xa9nam"] = [album.get("album_name_ar") or ""]
    tags["\xa9ART"] = [artist_str]
    tags["\xa9alb"] = [album.get("album_name_ar") or ""]
    tags["\xa9gen"] = [GENRE]
    tags["\xa9day"] = [gregorian_year]
    tags["trkn"] = [(track_number, 0)]

    comment_parts = [p for p in [album.get("occasion_ar"), album.get("location_ar")] if p]
    if comment_parts:
        tags["\xa9cmt"] = [" | ".join(comment_parts)]

    if cover_bytes:
        tags["covr"] = [MP4Cover(cover_bytes, imageformat=MP4Cover.FORMAT_JPEG)]

    tags.save()


def embed_metadata_for_album(album_id: int) -> int:
    """Embed tags for all downloaded tracks of an album. Returns count embedded."""
    album = dict(get_album(album_id))
    artists = get_album_artists(album_id)
    tracks = get_tracks_for_album(album_id)
    cover_bytes = _load_cover_bytes(album.get("cover_local_path"))
    gregorian_year = _hijri_to_gregorian_year(album)
    embedded = 0

    for track in tracks:
        if track["metadata_embedded"] or not track["downloaded"] or not track["local_path"]:
            continue
        path = track["local_path"]
        track_num = track["track_number"] or 0

        try:
            if path.lower().endswith(".m4a"):
                _embed_m4a(path, album, artists, track_num, cover_bytes, gregorian_year)
            else:
                _embed_mp3(path, album, artists, track_num, cover_bytes, gregorian_year)

            update_track_embedded(track["id"])
            embedded += 1
            logger.info("Embedded metadata: %s", path)
        except Exception as e:
            logger.error("Failed to embed metadata for track %d: %s", track["id"], e)

    return embedded
