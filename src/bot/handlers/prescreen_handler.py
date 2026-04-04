"""
Pre-screen flow:
  1. Bot sends raw message + Telegram link with [Defer] [Send to AI] buttons.
  2. Defer  → verification_status = 'deferred', auto-send next pre-screen card.
  3. Send to AI → blocking Gemini extraction → immediately show verification card.
"""
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from src.config import VERIFICATION_CHAT_ID
from src.database.db import (
    get_album,
    get_albums_pending_prescreen,
    get_or_create_artist,
    get_tracks_for_album,
    link_album_artist,
    update_album_ai_fields,
)
from src.ai.gemini_client import extract_metadata

logger = logging.getLogger(__name__)


def _message_link(group_id: int, message_id: int) -> str:
    channel_id = str(abs(group_id))[3:]
    return f"https://t.me/c/{channel_id}/{message_id}"


def _he(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_prescreen_card(album: dict, track_count: int) -> str:
    link = _message_link(album["telegram_group_id"], album["info_message_id"])
    return (
        f'🔍 <b>PRE-SCREEN</b> (ID: {album["id"]}) — <a href="{link}">source</a>\n\n'
        f"🎵 Tracks: {track_count}\n\n"
        f"📄 <b>Original message:</b>\n<pre>{_he(album['raw_text'] or '')}</pre>"
    )


def _prescreen_keyboard(album_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⏭️ Defer", callback_data=f"ps_defer:{album_id}"),
        InlineKeyboardButton("🤖 Send to AI", callback_data=f"ps_ai:{album_id}"),
    ]])


async def send_next_prescreen(context: ContextTypes.DEFAULT_TYPE):
    """Send the next pre_screen album card, or notify if the queue is empty."""
    albums = get_albums_pending_prescreen()
    if not albums:
        await context.bot.send_message(VERIFICATION_CHAT_ID, "✅ No albums left to pre-screen.")
        return

    album = dict(albums[0])
    tracks = get_tracks_for_album(album["id"])
    await context.bot.send_message(
        VERIFICATION_CHAT_ID,
        _format_prescreen_card(album, len(tracks)),
        parse_mode="HTML",
        reply_markup=_prescreen_keyboard(album["id"]),
    )


async def prescreen_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, album_id_str = query.data.split(":", 1)
    album_id = int(album_id_str)
    album = dict(get_album(album_id))

    if action == "ps_defer":
        update_album_ai_fields(album_id, {"verification_status": "deferred"})
        await query.edit_message_text(f"⏭️ Album {album_id} deferred.")
        await send_next_prescreen(context)

    elif action == "ps_ai":
        await query.edit_message_text(
            f"⏳ Sending album {album_id} to AI…",
            parse_mode="HTML",
        )
        try:
            result = await extract_metadata(album_id, album["raw_text"])
            for name in result.artists:
                name = name.strip()
                if name:
                    artist_id = get_or_create_artist(name)
                    link_album_artist(album_id, artist_id)

            await query.edit_message_text(
                f"✅ AI extracted album {album_id} (confidence: {int(result.confidence * 100)}%). Sending for verification…",
                parse_mode="HTML",
            )
            from src.bot.handlers.verification_handler import send_album_card
            await send_album_card(context, album_id)

        except Exception as e:
            logger.error("AI extraction failed for album %d: %s", album_id, e)
            await query.edit_message_text(
                f"❌ AI extraction failed for album {album_id}: {_he(str(e))}",
                parse_mode="HTML",
            )


def build_prescreen_handler() -> CallbackQueryHandler:
    return CallbackQueryHandler(prescreen_callback, pattern=r"^ps_(defer|ai):")
