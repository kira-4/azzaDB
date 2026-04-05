"""
Embeds ID3/MP4 tags into downloaded audio files using mutagen.
"""
import logging
import os
import re

from mutagen.mp3 import MP3, HeaderNotFoundError as MP3HeaderNotFoundError
from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TALB, TDRC, TCON, COMM, TRCK, APIC
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
    try:
        from hijri_converter import Hijri

        raw_year = (album.get("hijri_date") or "").replace("هـ", "").strip()
        if not raw_year:
            return ""
        h_year = int(raw_year)

        h_month = 6
        for name, num in _HIJRI_MONTHS.items():
            if name in (album.get("hijri_month") or ""):
                h_month = num
                break

        h_day = 15
        day_match = re.search(r"\d+", album.get("hijri_day") or "")
        if day_match:
            h_day = min(int(day_match.group()), 29)

        return str(Hijri(h_year, h_month, h_day).to_gregorian().year)
    except Exception as e:
        logger.warning("Hijri conversion failed: %s", e)
        return ""


def _load_cover_bytes(cover_path: str | None) -> bytes | None:
    if not cover_path or not os.path.exists(cover_path):
        return None
    with open(cover_path, "rb") as f:
        return f.read()


def _extract_cover_from_track(track_path: str) -> bytes | None:
    """Read the embedded cover art from an existing audio file without modifying it."""
    try:
        if track_path.lower().endswith(".m4a"):
            tags = MP4(track_path)
            covers = tags.get("covr")
            if covers:
                return bytes(covers[0])
        else:
            audio = MP3(track_path)
            if audio.tags:
                for key in audio.tags.keys():
                    if key.startswith("APIC"):
                        return audio.tags[key].data
    except Exception as e:
        logger.debug("Could not extract cover from %s: %s", track_path, e)
    return None


def _embed_mp3(track_path: str, title: str, artist_str: str, album_name: str,
               track_number: int, gregorian_year: str, comment: str,
               cover_bytes: bytes | None):
    use_raw_id3 = False
    try:
        audio = MP3(track_path)
        if audio.tags is not None:
            audio.tags.clear()
        else:
            audio.add_tags()
        tags = audio.tags
    except MP3HeaderNotFoundError:
        # Broken/non-standard MPEG frames — write tags via raw ID3 (skips audio validation)
        logger.warning("MPEG sync failed for %s, falling back to raw ID3 writer", track_path)
        use_raw_id3 = True
        try:
            tags = ID3(track_path)
            tags.clear()
        except ID3NoHeaderError:
            tags = ID3()

    tags["TIT2"] = TIT2(encoding=3, text=title)
    tags["TPE1"] = TPE1(encoding=3, text=artist_str)
    tags["TALB"] = TALB(encoding=3, text=album_name)
    tags["TCON"] = TCON(encoding=3, text=GENRE)
    tags["TDRC"] = TDRC(encoding=3, text=gregorian_year)
    tags["TRCK"] = TRCK(encoding=3, text=str(track_number))
    tags["COMM::ara"] = COMM(encoding=3, lang="ara", desc="", text=comment)

    if cover_bytes:
        tags["APIC:Cover"] = APIC(
            encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover_bytes
        )

    if use_raw_id3:
        tags.save(track_path, v2_version=4)
    else:
        audio.save(v2_version=4)


def _embed_m4a(track_path: str, title: str, artist_str: str, album_name: str,
               track_number: int, gregorian_year: str, comment: str,
               cover_bytes: bytes | None):
    tags = MP4(track_path)

    tags["\xa9nam"] = [title]
    tags["\xa9ART"] = [artist_str]
    tags["\xa9alb"] = [album_name]
    tags["\xa9gen"] = [GENRE]
    tags["\xa9day"] = [gregorian_year]
    tags["trkn"] = [(track_number, 0)]
    if comment:
        tags["\xa9cmt"] = [comment]

    if cover_bytes:
        tags["covr"] = [MP4Cover(cover_bytes, imageformat=MP4Cover.FORMAT_JPEG)]

    tags.save()


def embed_metadata_for_album(album_id: int) -> int:
    """Embed tags for all downloaded tracks of an album. Returns count embedded."""
    album = dict(get_album(album_id))
    artists = list(get_album_artists(album_id))
    tracks = get_tracks_for_album(album_id)

    artist_str = "; ".join(a["name_ar"] for a in artists) if artists else ""
    album_name = album.get("album_name_ar") or ""
    gregorian_year = _hijri_to_gregorian_year(album)
    comment = " | ".join(p for p in [album.get("occasion_ar"), album.get("location_ar")] if p)
    cover_bytes = _load_cover_bytes(album.get("cover_local_path"))
    if not cover_bytes:
        for track in tracks:
            if track["downloaded"] and track["local_path"] and os.path.exists(track["local_path"]):
                cover_bytes = _extract_cover_from_track(track["local_path"])
                if cover_bytes:
                    logger.info("Album %s: using cover extracted from %s", album_name, track["local_path"])
                    break
    embedded = 0

    for track in tracks:
        if track["metadata_embedded"] or not track["downloaded"] or not track["local_path"]:
            continue
        path = track["local_path"]
        if not os.path.exists(path):
            logger.warning("Track file not found, skipping: %s", path)
            continue

        track_num = track["track_number"] or 0
        track_name = (track["track_name_ar"] or "").strip()
        if not track_name and path.lower().endswith(".mp3"):
            try:
                existing = MP3(path)
                if existing.tags:
                    tit2 = existing.tags.get("TIT2")
                    if tit2:
                        track_name = str(tit2).strip()
            except Exception:
                pass
        title = track_name or album_name

        try:
            if path.lower().endswith(".m4a"):
                _embed_m4a(path, title, artist_str, album_name, track_num,
                           gregorian_year, comment, cover_bytes)
            else:
                _embed_mp3(path, title, artist_str, album_name, track_num,
                           gregorian_year, comment, cover_bytes)

            update_track_embedded(track["id"])
            embedded += 1
            logger.info("Embedded: %s", path)
        except Exception as e:
            logger.error("Failed to embed track %d (%s): %s", track["id"], path, e)

    return embedded
