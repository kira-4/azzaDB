import csv
import io
import logging

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from src.config import ADMIN_USER_IDS
from src.database.db import (
    get_album,
    get_album_artists,
    get_albums_deferred,
    get_albums_pending_ai,
    get_tracks_for_album,
    get_verification_stats,
    get_verified_albums_with_missing_tracks,
    update_album_ai_fields,
)
from src.ai.gemini_client import extract_metadata
from src.bot.handlers.verification_handler import send_next_pending

logger = logging.getLogger(__name__)


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    stats = get_verification_stats()
    await update.message.reply_text(
        f"📊 *Album Stats*\n\n"
        f"🔍 Pre-screen: {stats['pre_screen']}\n"
        f"⏳ Pending: {stats['pending']}\n"
        f"✅ Verified: {stats['verified']}\n"
        f"❌ Rejected: {stats['rejected']}\n"
        f"🔎 Needs Review: {stats['needs_review']}\n"
        f"⏭️ Deferred: {stats['deferred']}\n"
        f"📦 Total: {stats['total']}",
        parse_mode="Markdown",
    )


async def cmd_retry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /retry <album_id>")
        return

    try:
        album_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid album ID.")
        return

    album = get_album(album_id)
    if not album:
        await update.message.reply_text(f"Album {album_id} not found.")
        return

    await update.message.reply_text(f"🔄 Re-running AI extraction for album {album_id}…")
    try:
        result = await extract_metadata(album_id, album["raw_text"])
        await update.message.reply_text(
            f"✅ Re-extracted. Confidence: {int(result.confidence * 100)}%"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Extraction failed: {e}")


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return

    from src.database.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM albums WHERE verification_status='verified' ORDER BY id ASC"
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("No verified albums yet.")
        return

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(rows[0].keys())
    for row in rows:
        writer.writerow(list(row))

    output.seek(0)
    await update.message.reply_document(
        document=output.getvalue().encode("utf-8-sig"),
        filename="verified_albums.csv",
        caption=f"Exported {len(rows)} verified albums.",
    )


async def cmd_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    await send_next_pending(context)


async def cmd_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the next album waiting to be pre-screened."""
    if not _is_admin(update.effective_user.id):
        return
    from src.bot.handlers.prescreen_handler import send_next_prescreen
    await send_next_prescreen(context)


async def cmd_deferred(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all deferred albums."""
    if not _is_admin(update.effective_user.id):
        return
    albums = get_albums_deferred()
    if not albums:
        await update.message.reply_text("No deferred albums.")
        return

    lines = [f"⏭️ *Deferred Albums* ({len(albums)} total)\n"]
    for a in albums:
        tracks = get_tracks_for_album(a["id"])
        snippet = (a["raw_text"] or "").split("\n")[0][:60]
        channel_id = str(abs(a["telegram_group_id"]))[3:]
        link = f"https://t.me/c/{channel_id}/{a['info_message_id']}"
        lines.append(f"• ID {a['id']} ({len(tracks)} tracks) [source]({link}) — {snippet}")
    lines.append("\nUse /undefer <id> to return an album to the pre-screen queue.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_undefer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return a deferred album to the pre-screen queue."""
    if not _is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /undefer <album_id>")
        return
    try:
        album_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid album ID.")
        return

    album = get_album(album_id)
    if not album:
        await update.message.reply_text(f"Album {album_id} not found.")
        return
    if album["verification_status"] != "deferred":
        await update.message.reply_text(f"Album {album_id} is not deferred (status: {album['verification_status']}).")
        return

    update_album_ai_fields(album_id, {"verification_status": "pre_screen"})
    await update.message.reply_text(f"✅ Album {album_id} returned to pre-screen queue.")


async def cmd_failed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List verified albums with incomplete downloads."""
    if not _is_admin(update.effective_user.id):
        return
    albums = get_verified_albums_with_missing_tracks()
    if not albums:
        await update.message.reply_text("✅ No verified albums with missing downloads.")
        return
    lines = [f"⚠️ *Verified albums with missing downloads* ({len(albums)} total)\n"]
    for a in albums:
        tracks = get_tracks_for_album(a["id"])
        missing = sum(1 for t in tracks if not t["downloaded"])
        lines.append(f"• ID {a['id']} — {missing}/{len(tracks)} tracks missing — /redownload {a['id']}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_redownload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-trigger download for a verified album with missing tracks."""
    if not _is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /redownload <album_id>")
        return
    try:
        album_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid album ID.")
        return

    album = get_album(album_id)
    if not album:
        await update.message.reply_text(f"Album {album_id} not found.")
        return
    if album["verification_status"] != "verified":
        await update.message.reply_text(
            f"Album {album_id} is not verified (status: {album['verification_status']})."
        )
        return

    import asyncio
    import time
    from telegram.error import RetryAfter
    from src.config import TARGET_GROUP_ID
    from src.scraper.asset_downloader import download_album
    from src.bot.handlers.verification_handler import (
        _he, _format_size, _format_speed, _format_eta, _format_date, _message_link,
    )

    album = dict(album)
    link = _message_link(album["telegram_group_id"], album["info_message_id"])
    msg = await update.message.reply_text(
        f'⏳ Re-downloading album {album_id}… <a href="{link}">source</a>',
        parse_mode="HTML",
    )

    async def _do_download():
        _last_edit = [0.0]

        async def on_progress(track_idx, total_tracks, track_name,
                              current_bytes, total_bytes, speed_bps, eta_secs):
            now = time.monotonic()
            if now - _last_edit[0] < 5.0:
                return
            _last_edit[0] = now
            if total_bytes > 0 and current_bytes > 0:
                pct = int(current_bytes / total_bytes * 100)
                progress_line = f"↓ {pct}% · {_format_speed(speed_bps)} · ETA {_format_eta(eta_secs)}"
            else:
                progress_line = "↓ Starting…"
            text = (
                f'⏳ Re-downloading album {album_id}…\n\n'
                f'📥 {_he(track_name)} ({track_idx}/{total_tracks})\n'
                f'{progress_line}\n\n'
                f'<a href="{link}">source</a>'
            )
            try:
                await context.bot.edit_message_text(
                    chat_id=msg.chat_id, message_id=msg.message_id,
                    text=text, parse_mode="HTML",
                )
            except RetryAfter as e:
                _last_edit[0] = time.monotonic() + e.retry_after
            except Exception:
                pass

        try:
            start_time = time.monotonic()
            downloaded, total_bytes = await download_album(album_id, TARGET_GROUP_ID, on_progress=on_progress)
            elapsed = time.monotonic() - start_time

            artists = get_album_artists(album_id)
            tracks = get_tracks_for_album(album_id)

            artist_str = ", ".join(_he(a["name_ar"]) for a in artists) if artists else "—"
            name_str   = _he(album.get("album_name_ar") or "—")
            type_str   = _he(album.get("album_type") or "")
            heading    = f"{type_str}: {name_str}" if type_str else name_str

            date_str  = _format_date(album)
            occasion  = _he(album.get("occasion_ar") or "")
            meta_line = " · ".join(p for p in [
                (f"📅 {date_str}" if date_str != "—" else None),
                (f"🕌 {occasion}"  if occasion           else None),
            ] if p)

            size_str    = _format_size(total_bytes) if total_bytes > 0 else "—"
            elapsed_str = _format_eta(int(elapsed))
            avg_speed   = _format_speed(total_bytes / elapsed) if elapsed > 0 and total_bytes > 0 else "—"

            lines = [
                f"✅ <b>Album {album_id}</b> re-downloaded.\n",
                f"📀 {heading}",
                f"🎤 {artist_str}",
                f"🎶 {downloaded}/{len(tracks)} tracks · {size_str}",
            ]
            if meta_line:
                lines.append(meta_line)
            lines += [
                f"\n⏱ {elapsed_str}  ·  ↓ avg {avg_speed}",
                f'\n<a href="{link}">source</a>',
            ]

            final_text = "\n".join(lines)
            for _attempt in range(5):
                try:
                    await context.bot.edit_message_text(
                        chat_id=msg.chat_id, message_id=msg.message_id,
                        text=final_text, parse_mode="HTML",
                    )
                    break
                except RetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                except Exception:
                    break

        except Exception as e:
            logger.error("Redownload failed for album %d: %s", album_id, e)
            try:
                await context.bot.edit_message_text(
                    chat_id=msg.chat_id, message_id=msg.message_id,
                    text=f"❌ Redownload failed for album {album_id}: {e}",
                )
            except Exception:
                pass

    asyncio.create_task(_do_download())


def build_admin_handlers() -> list:
    return [
        CommandHandler("next", cmd_next),
        CommandHandler("screen", cmd_screen),
        CommandHandler("status", cmd_status),
        CommandHandler("retry", cmd_retry),
        CommandHandler("export", cmd_export),
        CommandHandler("deferred", cmd_deferred),
        CommandHandler("undefer", cmd_undefer),
        CommandHandler("failed", cmd_failed),
        CommandHandler("redownload", cmd_redownload),
    ]
