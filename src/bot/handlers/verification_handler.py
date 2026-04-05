"""
Verification flow:
  1. Bot sends album card with Approve / Edit / Reject inline buttons.
  2. Approve  → verified
  3. Reject   → asks for reason text, then sets rejected + reason
  4. Edit     → field picker; tap a field, bot prompts with ForceReply
                pre-filled with current value; send new value; repeat or Done
"""
import asyncio
import logging
import os
import time
from datetime import datetime

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import RetryAfter
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
    set_album_artists,
    set_album_verification,
    update_album_ai_fields,
)

logger = logging.getLogger(__name__)

# Conversation states
PICK_FIELD, AWAIT_FIELD_VALUE, REJECT_REASON = range(3)

EDITABLE_FIELDS = [
    ("album_type",    "Type"),
    ("album_name_ar", "Album Name"),
    ("artist",        "Artist(s)"),
    ("occasion_ar",   "Occasion"),
    ("hijri_date",    "Hijri Date"),
    ("location_ar",   "Location"),
    ("notes_ar",      "Notes"),
]
_FIELD_LABEL = dict(EDITABLE_FIELDS)


def _message_link(group_id: int, message_id: int) -> str:
    channel_id = str(abs(group_id))[3:]
    return f"https://t.me/c/{channel_id}/{message_id}"


def _he(text: str) -> str:
    """Escape a string for Telegram HTML mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_album_card(album: dict, artists: list, track_count: int) -> str:
    artist_names = ", ".join(a["name_ar"] for a in artists) if artists else "—"
    confidence_pct = int((album["ai_confidence"] or 0) * 100)
    link = _message_link(album["telegram_group_id"], album["info_message_id"])
    return (
        f"📋 <b>NEW ALBUM FOR REVIEW</b> (ID: {album['id']}) — <a href=\"{link}\">source</a>\n\n"
        f"📀 Type: {_he(album['album_type'] or '—')}\n"
        f"🎵 Name: {_he(album['album_name_ar'] or '—')}\n"
        f"🎤 Artist(s): {_he(artist_names)}\n"
        f"🎶 Tracks: {track_count}\n"
        f"📅 Date: {_he(_format_date(album))}\n"
        f"🕌 Occasion: {_he(album['occasion_ar'] or '—')}\n"
        f"📍 Location: {_he(album['location_ar'] or '—')}\n"
        f"🎚️ Audio Eng: {_he(album['audio_engineer'] or '—')}\n"
        f"📝 Notes: {_he(album['notes_ar'] or '—')}\n\n"
        f"AI Confidence: {confidence_pct}%\n\n"
        f"📄 <b>Original text:</b>\n<pre>{_he(album['raw_text'] or '')}</pre>"
    )


def _format_date(album: dict) -> str:
    parts = [p for p in [album["hijri_day"], album["hijri_month"], album["hijri_date"]] if p]
    return " ".join(parts) if parts else "—"


def _format_size(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    if b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b / 1024 / 1024:.1f} MB"


def _format_speed(bps: float) -> str:
    if bps < 1024:
        return f"{bps:.0f} B/s"
    if bps < 1024 * 1024:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps / 1024 / 1024:.1f} MB/s"


def _format_eta(secs: float) -> str:
    if secs <= 0 or secs > 86400:
        return "—"
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m {secs % 60:02d}s"


def _review_keyboard(album_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{album_id}"),
            InlineKeyboardButton("✏️ Edit",    callback_data=f"edit:{album_id}"),
            InlineKeyboardButton("❌ Reject",  callback_data=f"reject:{album_id}"),
        ]
    ])


def _field_picker_keyboard(album_id: int) -> InlineKeyboardMarkup:
    rows = []
    pair = []
    for key, label in EDITABLE_FIELDS:
        pair.append(InlineKeyboardButton(f"✏️ {label}", callback_data=f"field:{album_id}:{key}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([InlineKeyboardButton("✅ Done", callback_data=f"edit_done:{album_id}")])
    return InlineKeyboardMarkup(rows)


def _format_picker_text(album_id: int, album: dict) -> str:
    artists = get_album_artists(album_id)
    artist_str = ", ".join(a["name_ar"] for a in artists) if artists else "—"
    lines = [f"✏️ <b>Editing Album {album_id}</b> — tap a field to edit:\n"]
    for key, label in EDITABLE_FIELDS:
        val = _he(artist_str if key == "artist" else str(album.get(key) or "—"))
        lines.append(f"• <b>{label}:</b> {val}")
    return "\n".join(lines)


async def send_album_card(context: ContextTypes.DEFAULT_TYPE, album_id: int):
    """Send the verification card for a specific album."""
    album = dict(get_album(album_id))
    artists = get_album_artists(album_id)
    tracks = get_tracks_for_album(album_id)
    text = _format_album_card(album, artists, len(tracks))

    cover_path = album.get("cover_local_path")
    if cover_path and album.get("cover_downloaded") and os.path.exists(cover_path):
        with open(cover_path, "rb") as f:
            await context.bot.send_photo(VERIFICATION_CHAT_ID, photo=f)

    await context.bot.send_message(
        VERIFICATION_CHAT_ID,
        text,
        parse_mode="HTML",
        reply_markup=_review_keyboard(album_id),
    )


async def send_next_pending(context: ContextTypes.DEFAULT_TYPE):
    """Send the next pending album to VERIFICATION_CHAT_ID."""
    albums = get_albums_pending_verification()
    if not albums:
        await context.bot.send_message(VERIFICATION_CHAT_ID, "✅ No pending albums.")
        return
    await send_album_card(context, albums[0]["id"])


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
            f'⏳ Album {album_id} approved by {_he(approved_by)}. Downloading… <a href="{link}">source</a>',
            parse_mode="HTML",
        )

        from src.config import TARGET_GROUP_ID
        from src.scraper.asset_downloader import download_album

        async def _download_and_update():
            _last_edit = [0.0]

            async def on_progress(completed_count, total_tracks, active_count,
                                  current_bytes, total_bytes, speed_bps, eta_secs):
                now = time.monotonic()
                if now - _last_edit[0] < 5.0:
                    return
                _last_edit[0] = now

                if total_bytes > 0 and current_bytes > 0:
                    pct = int(current_bytes / total_bytes * 100)
                    speed_str = _format_speed(speed_bps)
                    eta_str = _format_eta(eta_secs)
                    progress_line = f"↓ {pct}% · {speed_str} · ETA {eta_str}"
                else:
                    progress_line = "↓ Starting…"

                text = (
                    f'⏳ Album {album_id} approved by {_he(approved_by)}. Downloading…\n\n'
                    f'🎵 {_he(album.get("album_name_ar") or "—")}\n'
                    f'📥 {completed_count}/{total_tracks} done · {active_count} active\n'
                    f'{progress_line}\n\n'
                    f'<a href="{link}">source</a>'
                )
                try:
                    await context.bot.edit_message_text(
                        chat_id=msg.chat_id,
                        message_id=msg.message_id,
                        text=text,
                        parse_mode="HTML",
                    )
                except RetryAfter as e:
                    # Push _last_edit forward so we stay quiet during the flood window
                    _last_edit[0] = time.monotonic() + e.retry_after
                except Exception:
                    pass

            start_time = time.monotonic()
            downloaded, total_bytes = await download_album(album_id, TARGET_GROUP_ID, on_progress=on_progress)
            elapsed = time.monotonic() - start_time

            artists = get_album_artists(album_id)
            tracks  = get_tracks_for_album(album_id)

            artist_str  = ", ".join(_he(a["name_ar"]) for a in artists) if artists else "—"
            name_str    = _he(album.get("album_name_ar") or "—")
            type_str    = _he(album.get("album_type") or "")
            heading     = f"{type_str}: {name_str}" if type_str else name_str

            date_str    = _format_date(album)
            occasion    = _he(album.get("occasion_ar") or "")
            meta_line   = " · ".join(p for p in [
                (f"📅 {date_str}" if date_str != "—" else None),
                (f"🕌 {occasion}"  if occasion           else None),
            ] if p)

            size_str    = _format_size(total_bytes) if total_bytes > 0 else "—"
            elapsed_str = _format_eta(int(elapsed))
            avg_speed   = _format_speed(total_bytes / elapsed) if elapsed > 0 and total_bytes > 0 else "—"

            lines = [
                f"✅ <b>Album {album_id}</b> approved by {_he(approved_by)}.\n",
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
                        chat_id=msg.chat_id,
                        message_id=msg.message_id,
                        text=final_text,
                        parse_mode="HTML",
                    )
                    break
                except RetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                except Exception:
                    break

        asyncio.create_task(_download_and_update())
        if get_albums_pending_verification():
            await send_next_pending(context)
        else:
            from src.bot.handlers.prescreen_handler import send_next_prescreen
            await send_next_prescreen(context)

    elif action == "reject":
        context.user_data["rejecting_album_id"] = album_id
        await query.edit_message_text(f"❌ Rejecting album {album_id}. Please send the rejection reason:")
        return REJECT_REASON

    elif action == "edit":
        context.user_data["editing_album_id"] = album_id
        album = dict(get_album(album_id))
        await query.edit_message_text(
            _format_picker_text(album_id, album),
            parse_mode="HTML",
            reply_markup=_field_picker_keyboard(album_id),
        )
        return PICK_FIELD


async def field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User tapped a field button — prompt with ForceReply pre-filled with current value."""
    query = update.callback_query
    await query.answer()

    _, album_id_str, field_key = query.data.split(":", 2)
    album_id = int(album_id_str)

    context.user_data["editing_field_key"] = field_key
    context.user_data["picker_message_id"] = query.message.message_id

    album = dict(get_album(album_id))
    if field_key == "artist":
        artists = get_album_artists(album_id)
        current_val = ", ".join(a["name_ar"] for a in artists)
    else:
        current_val = str(album.get(field_key) or "")
    label = _FIELD_LABEL[field_key]

    prompt = await query.message.reply_text(
        f"✏️ <b>{label}</b>\n"
        f"Current: <code>{_he(current_val) or '—'}</code>\n\n"
        f"Send new value:",
        parse_mode="HTML",
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder=current_val[:64] if current_val else label,
        ),
    )
    context.user_data["prompt_message_id"] = prompt.message_id
    return AWAIT_FIELD_VALUE


async def receive_field_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive the new field value, save it, clean up, and return to the picker."""
    album_id = context.user_data["editing_album_id"]
    field_key = context.user_data["editing_field_key"]
    new_value = update.message.text.strip()

    if field_key == "artist":
        names = [n.strip() for n in new_value.split(",") if n.strip()]
        set_album_artists(album_id, names)
    else:
        update_album_ai_fields(album_id, {field_key: new_value})

    # Clean up the ForceReply prompt and the user's reply message
    for msg_id in (context.user_data.pop("prompt_message_id", None), update.message.message_id):
        if msg_id:
            try:
                await context.bot.delete_message(update.effective_chat.id, msg_id)
            except Exception:
                pass

    # Refresh the picker with updated values
    album = dict(get_album(album_id))
    picker_msg_id = context.user_data.get("picker_message_id")
    if picker_msg_id:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=picker_msg_id,
            text=_format_picker_text(album_id, album),
            parse_mode="HTML",
            reply_markup=_field_picker_keyboard(album_id),
        )

    return PICK_FIELD


async def edit_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User tapped Done — restore the full album card with approve/reject buttons."""
    query = update.callback_query
    await query.answer()

    album_id = context.user_data.get("editing_album_id") or int(query.data.split(":", 1)[1])
    album = dict(get_album(album_id))
    artists = get_album_artists(album_id)
    tracks = get_tracks_for_album(album_id)

    await query.edit_message_text(
        _format_album_card(album, artists, len(tracks)),
        parse_mode="HTML",
        reply_markup=_review_keyboard(album_id),
    )
    return ConversationHandler.END


async def receive_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    album_id = context.user_data.get("rejecting_album_id")
    reason = update.message.text
    set_album_verification(album_id, "rejected", str(update.effective_user.id), reason)
    await update.message.reply_text(f"❌ Album {album_id} rejected. Reason recorded.")
    if get_albums_pending_verification():
        await send_next_pending(context)
    else:
        from src.bot.handlers.prescreen_handler import send_next_prescreen
        await send_next_prescreen(context)
    return ConversationHandler.END


def build_verification_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(button_callback, pattern=r"^(approve|edit|reject):")],
        states={
            PICK_FIELD: [
                CallbackQueryHandler(field_callback,    pattern=r"^field:"),
                CallbackQueryHandler(edit_done_callback, pattern=r"^edit_done:"),
            ],
            AWAIT_FIELD_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_field_value),
            ],
            REJECT_REASON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reject_reason),
            ],
        },
        fallbacks=[],
        per_chat=True,
    )
