"""
Verification flow:
  1. Bot sends album card with Approve / Edit / Reject inline buttons.
  2. Approve  → verified
  3. Reject   → asks for reason text, then sets rejected + reason
  4. Edit     → ConversationHandler walks through each editable field
"""
import asyncio
import logging
import os
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from src.config import VERIFICATION_CHAT_ID
from src.database.db import (
    get_album,
    get_album_artists,
    get_albums_pending_verification,
    get_tracks_for_album,
    set_album_verification,
    update_album_ai_fields,
)

logger = logging.getLogger(__name__)

# Conversation states
(
    EDIT_ALBUM_TYPE,
    EDIT_ALBUM_NAME,
    EDIT_OCCASION,
    EDIT_HIJRI_DATE,
    EDIT_LOCATION,
    EDIT_NOTES,
    REJECT_REASON,
) = range(7)

EDITABLE_FIELDS = [
    ("album_type", "Type (شريط/إصدار)"),
    ("album_name_ar", "Album Name"),
    ("occasion_ar", "Occasion"),
    ("hijri_date", "Hijri Date"),
    ("location_ar", "Location"),
    ("notes_ar", "Notes"),
]


def _message_link(group_id: int, message_id: int) -> str:
    # Supergroup IDs are -100XXXXXXXXXX; strip the leading -100 for the t.me/c/ link
    channel_id = str(abs(group_id))[3:]
    return f"https://t.me/c/{channel_id}/{message_id}"


def _format_album_card(album: dict, artists: list, track_count: int) -> str:
    artist_names = ", ".join(a["name_ar"] for a in artists) if artists else "—"
    confidence_pct = int((album["ai_confidence"] or 0) * 100)
    link = _message_link(album["telegram_group_id"], album["info_message_id"])
    return (
        f"📋 *NEW ALBUM FOR REVIEW* (ID: {album['id']}) — [source]({link})\n\n"
        f"📀 Type: {album['album_type'] or '—'}\n"
        f"🎵 Name: {album['album_name_ar'] or '—'}\n"
        f"🎤 Artist(s): {artist_names}\n"
        f"🎶 Tracks: {track_count}\n"
        f"📅 Date: {_format_date(album)}\n"
        f"🕌 Occasion: {album['occasion_ar'] or '—'}\n"
        f"📍 Location: {album['location_ar'] or '—'}\n"
        f"🎚️ Audio Eng: {album['audio_engineer'] or '—'}\n"
        f"📝 Notes: {album['notes_ar'] or '—'}\n\n"
        f"AI Confidence: {confidence_pct}%\n\n"
        f"📄 *Original text:*\n```\n{album['raw_text']}\n```"
    )


def _format_date(album: dict) -> str:
    parts = [p for p in [album["hijri_day"], album["hijri_month"], album["hijri_date"]] if p]
    return " ".join(parts) if parts else "—"


def _review_keyboard(album_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{album_id}"),
            InlineKeyboardButton("✏️ Edit", callback_data=f"edit:{album_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject:{album_id}"),
        ]
    ])


async def send_next_pending(context: ContextTypes.DEFAULT_TYPE):
    """Send the next pending album to VERIFICATION_CHAT_ID."""
    albums = get_albums_pending_verification()
    if not albums:
        await context.bot.send_message(VERIFICATION_CHAT_ID, "✅ No pending albums.")
        return

    album = dict(albums[0])
    artists = get_album_artists(album["id"])
    tracks = get_tracks_for_album(album["id"])
    text = _format_album_card(album, artists, len(tracks))

    cover_path = album.get("cover_local_path")
    if cover_path and album.get("cover_downloaded") and os.path.exists(cover_path):
        with open(cover_path, "rb") as f:
            await context.bot.send_photo(VERIFICATION_CHAT_ID, photo=f)

    await context.bot.send_message(
        VERIFICATION_CHAT_ID,
        text,
        parse_mode="Markdown",
        reply_markup=_review_keyboard(album["id"]),
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, album_id_str = query.data.split(":", 1)
    album_id = int(album_id_str)

    if action == "approve":
        set_album_verification(album_id, "verified", str(query.from_user.id))
        album = dict(get_album(album_id))
        link = _message_link(album["telegram_group_id"], album["info_message_id"])
        approved_by = query.from_user.first_name
        msg = await query.edit_message_text(
            f"⏳ Album {album_id} approved by {approved_by}. Downloading… [source]({link})",
            parse_mode="Markdown",
        )

        from src.config import TARGET_GROUP_ID
        from src.scraper.asset_downloader import download_album

        async def _download_and_update():
            await download_album(album_id, TARGET_GROUP_ID)
            await context.bot.edit_message_text(
                chat_id=msg.chat_id,
                message_id=msg.message_id,
                text=f"✅ Album {album_id} approved by {approved_by}. Downloaded. [source]({link})",
                parse_mode="Markdown",
            )

        asyncio.create_task(_download_and_update())

        await send_next_pending(context)

    elif action == "reject":
        context.user_data["rejecting_album_id"] = album_id
        await query.edit_message_text(f"❌ Rejecting album {album_id}. Please send the rejection reason:")
        return REJECT_REASON

    elif action == "edit":
        context.user_data["editing_album_id"] = album_id
        context.user_data["edit_field_index"] = 0
        album = dict(get_album(album_id))
        field_key, field_label = EDITABLE_FIELDS[0]
        current = album.get(field_key) or "—"
        await query.edit_message_text(
            f"✏️ Editing album {album_id}.\n\n"
            f"**{field_label}** (current: {current})\n"
            f"Send new value or /skip to keep:"
        )
        return EDIT_ALBUM_TYPE


async def receive_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    album_id = context.user_data.get("rejecting_album_id")
    reason = update.message.text
    set_album_verification(album_id, "rejected", str(update.effective_user.id), reason)
    await update.message.reply_text(f"❌ Album {album_id} rejected. Reason recorded.")
    await send_next_pending(context)
    return ConversationHandler.END


async def _advance_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, new_value: str | None):
    album_id = context.user_data["editing_album_id"]
    idx = context.user_data["edit_field_index"]
    field_key, _ = EDITABLE_FIELDS[idx]

    if new_value is not None:
        update_album_ai_fields(album_id, {field_key: new_value})

    idx += 1
    context.user_data["edit_field_index"] = idx

    if idx >= len(EDITABLE_FIELDS):
        await update.message.reply_text(
            f"✏️ Edit complete. Use /approve_{album_id} or /reject_{album_id} to finalise."
        )
        return ConversationHandler.END

    field_key, field_label = EDITABLE_FIELDS[idx]
    album = dict(get_album(album_id))
    current = album.get(field_key) or "—"
    await update.message.reply_text(
        f"**{field_label}** (current: {current})\nSend new value or /skip:"
    )
    return EDIT_ALBUM_TYPE + idx


async def edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _advance_edit(update, context, update.message.text)


async def skip_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _advance_edit(update, context, None)


def build_verification_conversation() -> ConversationHandler:
    field_handlers = [
        MessageHandler(filters.TEXT & ~filters.COMMAND, edit_field)
        for _ in EDITABLE_FIELDS
    ]

    return ConversationHandler(
        entry_points=[CallbackQueryHandler(button_callback, pattern=r"^(approve|edit|reject):")],
        states={
            REJECT_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reject_reason)],
            **{EDIT_ALBUM_TYPE + i: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_field),
                CommandHandler("skip", skip_field),
            ] for i in range(len(EDITABLE_FIELDS))},
        },
        fallbacks=[],
        per_chat=True,
    )
