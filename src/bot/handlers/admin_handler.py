import csv
import io
import logging

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from src.config import ADMIN_USER_IDS
from src.database.db import (
    get_album,
    get_albums_deferred,
    get_albums_pending_ai,
    get_tracks_for_album,
    get_verification_stats,
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


def build_admin_handlers() -> list:
    return [
        CommandHandler("next", cmd_next),
        CommandHandler("screen", cmd_screen),
        CommandHandler("status", cmd_status),
        CommandHandler("retry", cmd_retry),
        CommandHandler("export", cmd_export),
        CommandHandler("deferred", cmd_deferred),
        CommandHandler("undefer", cmd_undefer),
    ]
