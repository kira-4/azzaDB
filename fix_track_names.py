"""
One-shot script to fix Windows-1256 mojibake in existing track names.

Checks every downloaded track whose name doesn't look like readable Arabic,
attempts the latin-1 → windows-1256 re-encoding, and falls back to
"Track NN" when that still yields garbage.  Renames the file on disk,
updates the DB, and re-embeds the title tag inside the audio file.

Usage (inside the container or venv):
    python fix_track_names.py            # dry run — shows what would change
    python fix_track_names.py --apply    # rename files + update DB + re-embed tags
"""

import argparse
import os
import sqlite3

from src.config import DATABASE_PATH
from src.pipeline.metadata_embedder import embed_metadata_for_album

# ── encoding helpers (mirror of asset_downloader) ───────────────────────────

_ARABIC_RANGE = range(0x0600, 0x06FF + 1)


def _looks_arabic(text: str) -> bool:
    arabic_chars = sum(1 for ch in text if ord(ch) in _ARABIC_RANGE)
    return arabic_chars >= max(1, len(text) // 4)


def _fix_encoding(name: str) -> str | None:
    try:
        fixed = name.encode("latin-1").decode("windows-1256")
        if _looks_arabic(fixed):
            return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return None


_ILLEGAL_CHARS = r'\/:*?"<>|'


def _sanitize(name: str) -> str:
    for ch in _ILLEGAL_CHARS:
        name = name.replace(ch, "")
    return name.strip()


# ── main ─────────────────────────────────────────────────────────────────────

def main(apply: bool):
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row

    tracks = conn.execute(
        """SELECT id, album_id, track_number, track_name_ar, local_path
           FROM audio_tracks
           WHERE downloaded = 1
             AND track_name_ar IS NOT NULL
             AND local_path IS NOT NULL"""
    ).fetchall()

    fixed_count = 0
    fallback_count = 0
    skip_count = 0
    affected_album_ids = set()

    for t in tracks:
        name = t["track_name_ar"].strip()

        if _looks_arabic(name):
            skip_count += 1
            continue  # already fine

        corrected = _fix_encoding(name)
        num = t["track_number"] or 0

        if corrected:
            new_name = corrected
            tag = "FIXED"
            fixed_count += 1
        else:
            siblings = conn.execute(
                "SELECT id FROM audio_tracks WHERE album_id = ? ORDER BY track_number ASC",
                (t["album_id"],),
            ).fetchall()
            pos = next((i + 1 for i, r in enumerate(siblings) if r["id"] == t["id"]), num or 1)
            new_name = f"Track {pos:02d}"
            tag = "FALLBACK"
            fallback_count += 1

        old_path = t["local_path"]
        ext = os.path.splitext(old_path)[1]
        directory = os.path.dirname(old_path)
        safe_new = _sanitize(new_name)
        new_filename = f"{num:02d} - {safe_new}{ext}" if safe_new else f"{num:02d}{ext}"
        new_path = os.path.join(directory, new_filename)

        print(f"[{tag}] track {t['id']:>5}  {name!r}")
        print(f"         → {new_name!r}")
        if old_path != new_path:
            print(f"         file: {os.path.basename(old_path)!r} → {new_filename!r}")

        if apply:
            if old_path != new_path and os.path.exists(old_path):
                os.rename(old_path, new_path)
            conn.execute(
                """UPDATE audio_tracks
                   SET track_name_ar = ?, local_path = ?, metadata_embedded = 0
                   WHERE id = ?""",
                (new_name, new_path, t["id"]),
            )
            affected_album_ids.add(t["album_id"])

    if apply:
        conn.commit()
        conn.close()

        print(f"\nApplied: {fixed_count} fixed, {fallback_count} fallback, {skip_count} skipped.")

        if affected_album_ids:
            print(f"Re-embedding tags for {len(affected_album_ids)} album(s)...")
            for album_id in sorted(affected_album_ids):
                embedded = embed_metadata_for_album(album_id)
                print(f"  album {album_id}: re-embedded {embedded} track(s)")
        print("Done.")
    else:
        conn.close()
        print(f"\nDry run: {fixed_count} would be fixed, {fallback_count} would fall back, {skip_count} already OK.")
        print("Re-run with --apply to commit changes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fix mojibake track names in the DB, on disk, and in embedded tags.")
    parser.add_argument("--apply", action="store_true", help="Actually apply changes (default: dry run)")
    args = parser.parse_args()
    main(apply=args.apply)
