"""
One-shot script to re-embed comment tags for all already-downloaded albums,
picking up the new fallback logic (Hijri date when occasion_ar is absent).

Usage (inside the container or venv):
    python fix_comment_tags.py            # dry run — shows what would change
    python fix_comment_tags.py --apply    # reset embedded flags + re-embed tags
"""

import argparse
import sqlite3

from src.config import DATABASE_PATH
from src.pipeline.metadata_embedder import embed_metadata_for_album

_HIJRI_MONTHS_ORDER = [
    "محرم", "صفر", "ربيع الأول", "ربيع الثاني",
    "جمادى الأولى", "جمادى الثانية",
    "رجب", "شعبان", "رمضان",
    "شوال", "ذو القعدة", "ذو الحجة",
]


def _build_comment(album: sqlite3.Row) -> str:
    occasion = album["occasion_ar"]
    if not occasion:
        date_parts = [p for p in [album["hijri_day"], album["hijri_month"], album["hijri_date"]] if p]
        occasion = " ".join(date_parts) if date_parts else None
    parts = [p for p in [occasion, album["location_ar"]] if p]
    return " | ".join(parts)


def main(apply: bool):
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row

    # Albums with at least one embedded track
    albums = conn.execute(
        """SELECT DISTINCT a.*
           FROM albums a
           JOIN audio_tracks t ON t.album_id = a.id
           WHERE t.metadata_embedded = 1
           ORDER BY a.id ASC"""
    ).fetchall()

    print(f"Found {len(albums)} album(s) with embedded tracks.\n")

    change_count = 0
    skip_count = 0

    for album in albums:
        comment = _build_comment(album)
        occasion = album["occasion_ar"]
        # Show what changed (albums that previously had no occasion and thus an empty comment)
        old_comment = " | ".join(p for p in [occasion, album["location_ar"]] if p)
        if comment == old_comment:
            skip_count += 1
            continue

        change_count += 1
        print(f"[ALBUM {album['id']:>4}] {album['album_name_ar'] or '(unnamed)'}")
        print(f"         old comment: {old_comment!r}")
        print(f"         new comment: {comment!r}")

        if apply:
            conn.execute(
                "UPDATE audio_tracks SET metadata_embedded = 0 WHERE album_id = ?",
                (album["id"],),
            )

    if apply:
        conn.commit()
        conn.close()

        print(f"\nApplied: reset embedded flag for {change_count} album(s), {skip_count} unchanged.")

        if change_count:
            print("Re-embedding tags...")
            for album in albums:
                comment = _build_comment(album)
                old_comment = " | ".join(p for p in [album["occasion_ar"], album["location_ar"]] if p)
                if comment == old_comment:
                    continue
                embedded = embed_metadata_for_album(album["id"])
                print(f"  album {album['id']}: re-embedded {embedded} track(s)")
        print("Done.")
    else:
        conn.close()
        print(f"\nDry run: {change_count} album(s) would be updated, {skip_count} already have an occasion (no change).")
        print("Re-run with --apply to commit changes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Re-embed comment tags using the Hijri date fallback when occasion_ar is absent."
    )
    parser.add_argument("--apply", action="store_true", help="Actually apply changes (default: dry run)")
    args = parser.parse_args()
    main(apply=args.apply)
