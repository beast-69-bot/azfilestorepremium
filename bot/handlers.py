from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.error import RetryAfter
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.db import Database
from bot.security import new_code, new_token


DAY_SECONDS = 24 * 60 * 60
MAX_CHANNEL_BATCH_POSTS = 200
SETTINGS_AUTODELETE_SECONDS = "autodelete_seconds"
SETTINGS_START_IMG_URL = "start_img_url"
SETTINGS_UI_EMOJI_PREFIX = "ui_emoji:"


def _welcome_text() -> str:
    return (
        "🔐 *Secure File Access*\n"
        "Normal + Premium content system.\n\n"
        "📌 *How to use*\n"
        "• Open the link you received (deep link)\n"
        "• Join required channels when asked\n\n"
        "⭐ *Premium*\n"
        "• Redeem token: `/redeem <token>`\n\n"
        "ℹ️ Note: Files can be accessed only via generated links."
    )


def _now() -> int:
    return int(time.time())


def _is_owner(update: Update, cfg: Any) -> bool:
    return bool(update.effective_user and update.effective_user.id == int(cfg.owner_id))


async def _is_admin_or_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    cfg = context.application.bot_data["cfg"]
    if _is_owner(update, cfg):
        return True
    db: Database = context.application.bot_data["db"]
    uid = update.effective_user.id if update.effective_user else 0
    return await db.is_admin(uid)


def _deep_link(context: ContextTypes.DEFAULT_TYPE, code: str) -> str:
    username = context.application.bot_data.get("bot_username") or "YourBot"
    return f"https://t.me/{username}?start={code}"


async def _upsert_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return
    db: Database = context.application.bot_data["db"]
    await db.upsert_user(update.effective_user.id, update.effective_user.first_name, update.effective_user.username)


async def _joined_all_force_channels(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> tuple[bool, list[dict[str, Any]]]:
    db: Database = context.application.bot_data["db"]
    channels = await db.list_force_channels()
    if not channels:
        return True, []
    bot = context.bot
    for ch in channels:
        cid = int(ch["channel_id"])
        try:
            member = await bot.get_chat_member(chat_id=cid, user_id=user_id)
            if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
                return False, channels
        except Exception:
            # If we can't verify, deny (safer).
            return False, channels
    return True, channels


def _join_keyboard(channels: list[dict[str, Any]], recheck_code: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for ch in channels:
        title = ch.get("title") or str(ch["channel_id"])
        url = ch.get("invite_link")
        if not url and ch.get("username"):
            url = f"https://t.me/{ch['username']}"
        if url:
            rows.append([InlineKeyboardButton(text=f"📣 Join: {title}", url=url)])
        else:
            rows.append([InlineKeyboardButton(text=f"🔒 Required: {title}", callback_data="noop")])
    rows.append([InlineKeyboardButton(text="✅ I've Joined (Recheck)", callback_data=f"recheck:{recheck_code}")])
    return InlineKeyboardMarkup(rows)


async def _send_file(chat_id: int, file_row: dict[str, Any], caption: Optional[str], context: ContextTypes.DEFAULT_TYPE) -> None:
    t = file_row["file_type"]
    fid = file_row["tg_file_id"]
    if caption and len(caption) > 1024:
        caption = caption[:1020] + "..."
    msg = None
    if t == "document":
        msg = await context.bot.send_document(chat_id=chat_id, document=fid, caption=caption)
    elif t == "video":
        msg = await context.bot.send_video(chat_id=chat_id, video=fid, caption=caption)
    elif t == "audio":
        msg = await context.bot.send_audio(chat_id=chat_id, audio=fid, caption=caption)
    elif t == "photo":
        msg = await context.bot.send_photo(chat_id=chat_id, photo=fid, caption=caption)
    else:
        msg = await context.bot.send_document(chat_id=chat_id, document=fid, caption=caption)

    if msg:
        await _maybe_schedule_autodelete(msg.chat_id, msg.message_id, context)


async def _maybe_schedule_autodelete(chat_id: int, message_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    raw = await db.get_setting(SETTINGS_AUTODELETE_SECONDS)
    try:
        seconds = int(raw) if raw is not None else 0
    except ValueError:
        seconds = 0
    if seconds <= 0:
        return

    async def _job() -> None:
        try:
            await asyncio.sleep(seconds)
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass

    context.application.create_task(_job())


def _parse_duration_seconds(s: str) -> Optional[int]:
    s = (s or "").strip().lower()
    if not s:
        return None
    if s in ("off", "0", "disable", "disabled", "none"):
        return 0
    mult = 1
    if s.endswith("m"):
        mult = 60
        s = s[:-1]
    elif s.endswith("h"):
        mult = 60 * 60
        s = s[:-1]
    elif s.endswith("d"):
        mult = 24 * 60 * 60
        s = s[:-1]
    if not s.isdigit():
        return None
    return int(s) * mult

async def _bot_is_admin(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        me = await context.bot.get_me()
        member = await context.bot.get_chat_member(chat_id=chat_id, user_id=me.id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        return False


def _parse_tme_post_link(raw: str) -> Optional[dict[str, Any]]:
    """
    Supported:
      - https://t.me/<username>/<msg_id>
      - https://t.me/c/<internal_id>/<msg_id>  (private channels/groups)
    Returns: {"chat": <username|chat_id:int>, "msg_id": int}
    """
    s = (raw or "").strip()
    if "t.me/" not in s:
        return None
    # normalize
    s = s.replace("http://", "https://")
    try:
        after = s.split("t.me/", 1)[1]
    except Exception:
        return None
    after = after.split("?", 1)[0].strip("/")
    parts = after.split("/")
    if len(parts) < 2:
        return None

    if parts[0] == "c" and len(parts) >= 3:
        # /c/<internal_id>/<msg_id>
        internal_id = parts[1]
        mid = parts[2]
        if not internal_id.isdigit() or not mid.isdigit():
            return None
        chat_id = int(f"-100{internal_id}")
        return {"chat": chat_id, "msg_id": int(mid)}

    # /<username>/<msg_id>
    username = parts[0]
    mid = parts[1]
    if not mid.isdigit():
        return None
    return {"chat": username, "msg_id": int(mid)}


async def _resolve_chat_id(chat_ref: Any, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    if isinstance(chat_ref, int):
        return chat_ref
    if isinstance(chat_ref, str) and chat_ref:
        try:
            chat = await context.bot.get_chat(chat_ref)
            return int(chat.id)
        except Exception:
            return None
    return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    args = context.args or []
    if not update.effective_user or not update.effective_chat:
        return
    if not args:
        db: Database = context.application.bot_data["db"]
        img_url = await db.get_setting(SETTINGS_START_IMG_URL)
        text = _welcome_text()
        if img_url:
            try:
                await update.effective_chat.send_photo(photo=img_url, caption=text, parse_mode="Markdown")
                return
            except Exception:
                # Fallback to text-only if URL is invalid/unreachable.
                pass
        await update.effective_chat.send_message(text, parse_mode="Markdown")
        return
    code = args[0].strip()
    await _deliver_by_code(update, context, code)


async def recheck_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not update.effective_user:
        return
    data = q.data or ""
    if data == "noop":
        await q.answer()
        return
    if not data.startswith("recheck:"):
        await q.answer()
        return
    code = data.split(":", 1)[1]
    await q.answer()
    await _deliver_by_code(update, context, code)


async def _deliver_by_code(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    db: Database = context.application.bot_data["db"]
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    link = await db.get_link(code)
    if not link:
        await chat.send_message("❌ Invalid or expired link.")
        return

    ok_join, channels = await _joined_all_force_channels(user.id, context)
    if not ok_join:
        await chat.send_message(
            "🚫 *Access Locked*\n\n"
            "You must join all required channels to continue.\n"
            "After joining, tap *Recheck* ✅.",
            parse_mode="Markdown",
            reply_markup=_join_keyboard(channels, code),
        )
        return

    if link["access"] == "premium" and not await db.is_premium_active(user.id):
        await chat.send_message(
            "⭐ *Premium Required*\n\n"
            "This link is for premium users only.\n"
            "Redeem a token: `/redeem <token>`",
            parse_mode="Markdown",
        )
        return

    caption = await db.get_setting("caption")

    if link["target_type"] == "file":
        file_row = await db.get_file(link["target_id"])
        if not file_row:
            await chat.send_message("❌ File not found (may have been removed).")
            return
        await _send_file(chat.id, file_row, caption, context)
        await db.mark_link_used(code)
        return

    if link["target_type"] == "batch":
        file_ids = await db.get_batch_file_ids(link["target_id"])
        if not file_ids:
            await chat.send_message("❌ Batch is empty.")
            return
        if len(file_ids) > 100:
            await chat.send_message("⚠️ Batch too large to deliver.")
            return
        for fid in file_ids:
            file_row = await db.get_file(fid)
            if file_row:
                await _send_file(chat.id, file_row, caption, context)
        await db.mark_link_used(code)
        return

    if link["target_type"] == "msg":
        msg_row = await db.get_message(link["target_id"])
        if not msg_row:
            await chat.send_message("❌ Message not found (may have been removed).")
            return
        try:
            m = await context.bot.copy_message(
                chat_id=chat.id,
                from_chat_id=msg_row["from_chat_id"],
                message_id=msg_row["message_id"],
            )
            await _maybe_schedule_autodelete(m.chat_id, m.message_id, context)
        except Exception:
            await chat.send_message("❌ Unable to deliver this message (deleted or inaccessible).")
            return
        await db.mark_link_used(code)
        return

    if link["target_type"] == "chbatch":
        chb = await db.get_channel_batch(link["target_id"])
        if not chb:
            await chat.send_message("❌ Batch not found.")
            return
        if not await _bot_is_admin(chb["channel_id"], context):
            await chat.send_message("🚫 Bot is not admin in the source channel.\n\nPehle bot ko admin banao, phir try karo.")
            return
        start_id = int(chb["start_msg_id"])
        end_id = int(chb["end_msg_id"])
        total = end_id - start_id + 1
        if total <= 0 or total > MAX_CHANNEL_BATCH_POSTS:
            await chat.send_message("⚠️ Batch range invalid or too large.")
            return
        for mid in range(start_id, end_id + 1):
            try:
                m = await context.bot.copy_message(chat_id=chat.id, from_chat_id=chb["channel_id"], message_id=mid)
                await _maybe_schedule_autodelete(m.chat_id, m.message_id, context)
            except RetryAfter as e:
                await asyncio.sleep(float(getattr(e, "retry_after", 1.0)))
                try:
                    m = await context.bot.copy_message(chat_id=chat.id, from_chat_id=chb["channel_id"], message_id=mid)
                    await _maybe_schedule_autodelete(m.chat_id, m.message_id, context)
                except Exception:
                    pass
            except Exception:
                # Skip missing/deleted/inaccessible posts silently to keep batches usable.
                pass
        await db.mark_link_used(code)
        return

    await chat.send_message("❌ Unsupported link type.")


def _extract_media_file(update: Update) -> Optional[dict[str, str]]:
    m = update.effective_message
    if not m:
        return None
    if m.document:
        return {
            "file_type": "document",
            "file_id": m.document.file_id,
            "unique_id": m.document.file_unique_id,
            "file_name": m.document.file_name,
        }
    if m.video:
        return {
            "file_type": "video",
            "file_id": m.video.file_id,
            "unique_id": m.video.file_unique_id,
            "file_name": m.video.file_name,
        }
    if m.audio:
        return {
            "file_type": "audio",
            "file_id": m.audio.file_id,
            "unique_id": m.audio.file_unique_id,
            "file_name": m.audio.file_name,
        }
    if m.photo:
        p = m.photo[-1]
        return {
            "file_type": "photo",
            "file_id": p.file_id,
            "unique_id": p.file_unique_id,
            "file_name": None,
        }
    return None


async def admin_media_ingest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        return
    media = _extract_media_file(update)
    if not media or not update.effective_chat or not update.effective_user:
        return
    db: Database = context.application.bot_data["db"]
    file_db_id = await db.save_file(
        tg_file_id=media["file_id"],
        file_unique_id=media.get("unique_id"),
        file_type=media["file_type"],
        file_name=media.get("file_name"),
        added_by=update.effective_user.id,
    )

    # If custombatch mode is active, only collect files and show a confirmation prompt.
    cb_state = context.user_data.get("custombatch_state")
    if cb_state is not None:
        cb_state.setdefault("source_message_ids", []).append(update.effective_message.message_id)
        cb_state["source_chat_id"] = update.effective_chat.id
        cb_state.setdefault("file_ids", []).append(file_db_id)
        prev_prompt = cb_state.get("prompt_message_id")
        if prev_prompt:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=int(prev_prompt))
            except Exception:
                pass
        count = len(cb_state.get("file_ids") or [])
        msg = await update.effective_chat.send_message(
            f"✅ *{count}* file save ho gayi.\n\nLink generate karu ya process cancel karu?",
            parse_mode="Markdown",
            reply_markup=_custombatch_prompt_keyboard(),
        )
        cb_state["prompt_message_id"] = msg.message_id
        return

    normal_code = new_code()
    prem_code = new_code()
    await db.create_link(normal_code, "file", file_db_id, "normal", update.effective_user.id)
    await db.create_link(prem_code, "file", file_db_id, "premium", update.effective_user.id)

    await update.effective_chat.send_message(
        "✅ *File Saved Successfully*\n\n"
        f"🆔 File ID: `{file_db_id}`\n\n"
        "🔓 *Normal Link:*\n"
        f"{_deep_link(context, normal_code)}\n\n"
        "⭐ *Premium Link:*\n"
        f"{_deep_link(context, prem_code)}",
        parse_mode="Markdown",
    )


async def getlink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("🚫 Access denied. (Admin/Owner only)")
        return

    db: Database = context.application.bot_data["db"]
    target_file_id: Optional[int] = None
    target_msg_id: Optional[int] = None

    if update.effective_message and update.effective_message.reply_to_message:
        rmsg = update.effective_message.reply_to_message
        tmp = Update(update.update_id, message=rmsg)
        media = _extract_media_file(tmp)
        if media and update.effective_user:
            target_file_id = await db.save_file(
                tg_file_id=media["file_id"],
                file_unique_id=media.get("unique_id"),
                file_type=media["file_type"],
                file_name=media.get("file_name"),
                added_by=update.effective_user.id,
            )
        elif update.effective_user:
            # Any other message type: store a reference and deliver via copy_message.
            target_msg_id = await db.save_message(
                from_chat_id=rmsg.chat_id,
                message_id=rmsg.message_id,
                added_by=update.effective_user.id,
            )

    if target_file_id is None and context.args:
        try:
            target_file_id = int(context.args[0])
        except ValueError:
            target_file_id = None

    if target_file_id is None and target_msg_id is None:
        await update.effective_chat.send_message(
            "ℹ️ *How to use /getlink*\n\n"
            "1) Reply to any message/file and send `/getlink`\n"
            "2) Or use: `/getlink <file_id>`",
            parse_mode="Markdown",
        )
        return

    normal_code = new_code()
    prem_code = new_code()
    if target_file_id is not None:
        if not await db.get_file(target_file_id):
            await update.effective_chat.send_message("❌ File not found.")
            return
        await db.create_link(normal_code, "file", target_file_id, "normal", update.effective_user.id)
        await db.create_link(prem_code, "file", target_file_id, "premium", update.effective_user.id)
    else:
        await db.create_link(normal_code, "msg", target_msg_id, "normal", update.effective_user.id)
        await db.create_link(prem_code, "msg", target_msg_id, "premium", update.effective_user.id)

    await update.effective_chat.send_message(
        "✅ *Links Generated*\n\n"
        "🔓 *Normal Link:*\n"
        f"{_deep_link(context, normal_code)}\n\n"
        "⭐ *Premium Link:*\n"
        f"{_deep_link(context, prem_code)}",
        parse_mode="Markdown",
    )


async def batch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("Not allowed.")
        return
    if not update.effective_chat or not update.effective_user:
        return

    if context.args and context.args[0].lower() == "cancel":
        st = context.user_data.pop("chbatch_state", None)
        if st and st.get("prompt_message_id"):
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=int(st["prompt_message_id"]))
            except Exception:
                pass
        await update.effective_chat.send_message("❌ Batch creation cancelled.")
        return

    # Channel-range batch (start/end post links).
    msg = await update.effective_chat.send_message(
        "📦 Channel Batch Mode Started\n\n"
        "Step 1️⃣\n"
        "Send the STARTING channel post link.\n\n"
        "Examples:\n"
        "https://t.me/channelusername/123\n"
        "or\n"
        "https://t.me/c/123456789/123\n\n"
        "❌ Cancel: /batch cancel"
    )
    context.user_data["chbatch_state"] = {"step": "start", "prompt_message_id": msg.message_id}


async def batch_link_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Handles the link inputs for /batch channel-range creation.
    if not update.effective_message or not update.effective_user or not update.effective_chat:
        return
    state = context.user_data.get("chbatch_state")
    if not state:
        return
    if not await _is_admin_or_owner(update, context):
        return

    text = (update.effective_message.text or "").strip()
    parsed = _parse_tme_post_link(text)
    if not parsed:
        await _batch_ui_error(update, context, "Invalid post link format. Send a valid t.me post link, or /batch cancel.")
        return

    chat_id = await _resolve_chat_id(parsed["chat"], context)
    if chat_id is None:
        await _batch_ui_error(update, context, "Could not resolve channel from link. Check the link and try again.")
        return

    msg_id = int(parsed["msg_id"])
    if msg_id <= 0:
        await _batch_ui_error(update, context, "Invalid post id in link.")
        return

    # Clean UI: delete the admin's link message.
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.effective_message.message_id)
    except Exception:
        pass

    if state.get("step") == "start":
        state["channel_id"] = int(chat_id)
        state["start_msg_id"] = int(msg_id)
        state["step"] = "end"
        await _batch_ui_edit(
            update,
            context,
            "✅ Starting post received.\n\n"
            "Step 2️⃣\n"
            "Now send the ENDING channel post link.",
        )
        return

    if state.get("step") != "end":
        context.user_data.pop("chbatch_state", None)
        await update.effective_chat.send_message("Batch state corrupted. Run /batch again.")
        return

    if int(chat_id) != int(state.get("channel_id", 0)):
        await _batch_ui_error(update, context, "Links are from different channels. Send the ENDING link from the same channel.")
        return

    start_id = int(state["start_msg_id"])
    end_id = int(msg_id)
    if end_id < start_id:
        await _batch_ui_error(update, context, "Ending post ID is smaller than Starting post ID. Send a valid ENDING link.")
        return

    total = end_id - start_id + 1
    if total > MAX_CHANNEL_BATCH_POSTS:
        await _batch_ui_error(update, context, f"Range too large. Max allowed posts: {MAX_CHANNEL_BATCH_POSTS}.")
        return

    if not await _bot_is_admin(int(chat_id), context):
        context.user_data.pop("chbatch_state", None)
        await update.effective_chat.send_message("Bot is not admin in that channel. Pehle bot ko admin banao, phir /batch run karo.")
        return

    await _batch_ui_edit(update, context, "🔄 Creating batch...\nPlease wait...")

    db: Database = context.application.bot_data["db"]
    chbatch_id = await db.create_channel_batch(update.effective_user.id, int(chat_id), start_id, end_id)

    normal_code = new_code()
    prem_code = new_code()
    await db.create_link(normal_code, "chbatch", chbatch_id, "normal", update.effective_user.id)
    await db.create_link(prem_code, "chbatch", chbatch_id, "premium", update.effective_user.id)

    context.user_data.pop("chbatch_state", None)
    await _batch_ui_edit(
        update,
        context,
        "✅ Channel Batch Created Successfully!\n\n"
        f"📦 Total Posts: {total}\n\n"
        "🔓 Normal Access Link:\n"
        f"{_deep_link(context, normal_code)}\n\n"
        "⭐ Premium Access Link:\n"
        f"{_deep_link(context, prem_code)}\n\n"
        "ℹ️ Rules:\n"
        "• Required channels join karna mandatory hai\n"
        "• Premium link sirf premium users ke liye kaam karega",
    )


async def _batch_ui_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    state = context.user_data.get("chbatch_state") or {}
    chat_id = update.effective_chat.id if update.effective_chat else None
    mid = state.get("prompt_message_id")
    if chat_id and mid:
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=int(mid), text=text)
            return
        except Exception:
            pass
    # Fallback if edit not possible (message deleted etc.)
    msg = await update.effective_chat.send_message(text)
    state["prompt_message_id"] = msg.message_id
    context.user_data["chbatch_state"] = state


async def _batch_ui_error(update: Update, context: ContextTypes.DEFAULT_TYPE, err: str) -> None:
    # Show a clear error without breaking the flow.
    state = context.user_data.get("chbatch_state") or {}
    step = state.get("step") or "start"
    if step == "start":
        await _batch_ui_edit(
            update,
            context,
            "📦 Channel Batch Mode Started\n\n"
            f"⚠️ Error: {err}\n\n"
            "Step 1️⃣\n"
            "Send the STARTING channel post link.\n\n"
            "Examples:\n"
            "https://t.me/channelusername/123\n"
            "or\n"
            "https://t.me/c/123456789/123\n\n"
            "❌ Cancel: /batch cancel",
        )
        return
    await _batch_ui_edit(
        update,
        context,
        "✅ Starting post received.\n\n"
        f"⚠️ Error: {err}\n\n"
        "Step 2️⃣\n"
        "Now send the ENDING channel post link.",
    )


async def batch_ingest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Backward compatibility: keep symbol, but ingest is handled in admin_media_ingest.
    return


async def custombatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("🚫 Access denied. (Admin/Owner only)")
        return
    # Start a temporary "collect files" flow.
    context.user_data["custombatch_state"] = {"file_ids": [], "prompt_message_id": None}
    await update.effective_chat.send_message(
        "🧩 *Custom Batch Mode Started*\n\n"
        "📤 Files / media bhejo.\n"
        "Main unko custom batch me add karta rahunga.\n\n"
        "❌ Cancel anytime: press *Cancel Process* button.",
        parse_mode="Markdown",
    )


def _custombatch_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(text="Generate Link", callback_data="cbgen"),
                InlineKeyboardButton(text="Cancel Process", callback_data="cbcancel"),
            ]
        ]
    )


async def custombatch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not update.effective_user:
        return
    await q.answer()
    if not await _is_admin_or_owner(update, context):
        await q.edit_message_text("🚫 Access denied. (Admin/Owner only)")
        return

    data = q.data or ""
    state = context.user_data.get("custombatch_state")
    if not state:
        try:
            await q.edit_message_text("ℹ️ No active custom batch.\n\nStart it with: /custombatch")
        except Exception:
            pass
        return

    if data == "cbcancel":
        # Clear temp state and delete uploaded media messages (clean UI).
        st = context.user_data.pop("custombatch_state", None) or {}
        src_chat_id = st.get("source_chat_id")
        for mid in st.get("source_message_ids") or []:
            if src_chat_id:
                try:
                    await context.bot.delete_message(chat_id=int(src_chat_id), message_id=int(mid))
                except Exception:
                    pass
        try:
            await q.delete_message()
        except Exception:
            try:
                await q.edit_message_text("❌ Custom batch cancelled.")
            except Exception:
                pass
        return

    if data == "cbgen":
        file_ids = state.get("file_ids") or []
        if not file_ids:
            await q.edit_message_text("⚠️ No files received yet.\n\nSend files first, or cancel.")
            return
        db: Database = context.application.bot_data["db"]
        batch_id = await db.create_batch(update.effective_user.id, list(file_ids))

        normal_code = new_code()
        prem_code = new_code()
        await db.create_link(normal_code, "batch", batch_id, "normal", update.effective_user.id)
        await db.create_link(prem_code, "batch", batch_id, "premium", update.effective_user.id)

        # Clear temp state and delete uploaded media messages (clean UI).
        st = context.user_data.pop("custombatch_state", None) or {}
        src_chat_id = st.get("source_chat_id")
        for mid in st.get("source_message_ids") or []:
            if src_chat_id:
                try:
                    await context.bot.delete_message(chat_id=int(src_chat_id), message_id=int(mid))
                except Exception:
                    pass
        await q.edit_message_text(
            "✅ *Custom Batch Created Successfully!*\n\n"
            f"📦 Total Files: {len(file_ids)}\n\n"
            "🔓 *Normal Access Link:*\n"
            f"{_deep_link(context, normal_code)}\n\n"
            "⭐ *Premium Access Link:*\n"
            f"{_deep_link(context, prem_code)}\n\n"
            "ℹ️ Rules:\n"
            "• Required channels join karna mandatory hai\n"
            "• Premium link sirf premium users ke liye kaam karega",
            parse_mode="Markdown",
        )
        return


async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    cfg = context.application.bot_data["cfg"]
    if not _is_owner(update, cfg):
        await update.effective_chat.send_message("🚫 Owner only.")
        return
    if not context.args:
        await update.effective_chat.send_message("ℹ️ Usage: `/addadmin <user_id>`", parse_mode="Markdown")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.effective_chat.send_message("❌ Invalid user_id.")
        return
    db: Database = context.application.bot_data["db"]
    await db.add_admin(uid, update.effective_user.id)
    await update.effective_chat.send_message(f"✅ Admin added: `{uid}`", parse_mode="Markdown")


async def removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    cfg = context.application.bot_data["cfg"]
    if not _is_owner(update, cfg):
        await update.effective_chat.send_message("🚫 Owner only.")
        return
    if not context.args:
        await update.effective_chat.send_message("ℹ️ Usage: `/removeadmin <user_id>`", parse_mode="Markdown")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.effective_chat.send_message("❌ Invalid user_id.")
        return
    db: Database = context.application.bot_data["db"]
    await db.remove_admin(uid)
    await update.effective_chat.send_message(f"✅ Admin removed: `{uid}`", parse_mode="Markdown")


async def addpremium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("🚫 Access denied. (Admin/Owner only)")
        return
    if not context.args:
        await update.effective_chat.send_message("ℹ️ Usage: `/addpremium <user_id> [days]`", parse_mode="Markdown")
        return
    try:
        uid = int(context.args[0])
        days = int(context.args[1]) if len(context.args) > 1 else 1
    except ValueError:
        await update.effective_chat.send_message("❌ Invalid args.")
        return
    db: Database = context.application.bot_data["db"]
    until = await db.add_premium_seconds(uid, max(1, days) * DAY_SECONDS)
    await update.effective_chat.send_message(
        "✅ *Premium Granted*\n\n"
        f"👤 User: `{uid}`\n"
        f"⏳ Days: `{max(1, days)}`\n"
        f"🕒 premium_until (unix): `{until}`",
        parse_mode="Markdown",
    )


async def removepremium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("🚫 Access denied. (Admin/Owner only)")
        return
    if not context.args:
        await update.effective_chat.send_message("ℹ️ Usage: `/removepremium <user_id>`", parse_mode="Markdown")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.effective_chat.send_message("❌ Invalid user_id.")
        return
    db: Database = context.application.bot_data["db"]
    await db.set_premium_until(uid, 0)
    await update.effective_chat.send_message(f"✅ Premium removed for `{uid}`.", parse_mode="Markdown")


async def gencode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("🚫 Access denied. (Admin/Owner only)")
        return
    db: Database = context.application.bot_data["db"]
    count = 1
    if context.args:
        try:
            count = int(context.args[0])
        except ValueError:
            count = 1
    # Prevent abuse/spam and huge messages.
    if count < 1:
        count = 1
    if count > 20:
        count = 20

    tokens: list[str] = []
    for _ in range(count):
        t = new_token()
        await db.create_token(t, update.effective_user.id, DAY_SECONDS)
        tokens.append(t)

    if count == 1:
        await update.effective_chat.send_message(
            "🎟️ *Token Generated*\n\n"
            f"`{tokens[0]}`\n\n"
            "⭐ Grants: 1 day premium\n"
            "🔒 One-time use only",
            parse_mode="Markdown",
        )
        return

    # Multi-token response
    token_lines = "\n".join([f"`{t}`" for t in tokens])
    await update.effective_chat.send_message(
        "🎟️ *Tokens Generated*\n\n"
        f"🧾 Total: *{count}*\n\n"
        f"{token_lines}\n\n"
        "⭐ Each grants: 1 day premium\n"
        "🔒 Each is one-time use only\n\n"
        "ℹ️ Users redeem: `/redeem <token>`",
        parse_mode="Markdown",
    )


async def redeem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not update.effective_chat or not update.effective_user:
        return
    if not context.args:
        await update.effective_chat.send_message("ℹ️ Usage: `/redeem <token>`", parse_mode="Markdown")
        return
    token = context.args[0].strip()
    db: Database = context.application.bot_data["db"]
    grant = await db.redeem_token(token, update.effective_user.id)
    if not grant:
        await update.effective_chat.send_message("❌ Invalid or already-used token.")
        return
    until = await db.add_premium_seconds(update.effective_user.id, grant)
    await update.effective_chat.send_message(
        "✅ *Token Redeemed Successfully*\n\n"
        f"⭐ Premium active until (unix): `{until}`",
        parse_mode="Markdown",
    )


async def forcech(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("🚫 Access denied. (Admin/Owner only)")
        return
    db: Database = context.application.bot_data["db"]

    if not context.args or context.args[0].lower() == "list":
        chans = await db.list_force_channels()
        if not chans:
            await update.effective_chat.send_message(
                "📣 *Force Channels*\n\n"
                "No required channels set.\n\n"
                "✅ *Usage*\n"
                "• `/forcech add <channel_id> [invite_link]`\n"
                "• `/forcech remove <channel_id>`\n"
                "• `/forcech list`",
                parse_mode="Markdown",
            )
            return
        lines = []
        for ch in chans:
            extra = ch.get("invite_link") or (f"@{ch['username']}" if ch.get("username") else "")
            name = ch.get("title") or ""
            lines.append(f"• `{ch['channel_id']}` {name} {extra}".strip())
        await update.effective_chat.send_message("📣 *Force Channels*\n\n" + "\n".join(lines), parse_mode="Markdown")
        return

    sub = context.args[0].lower()
    if sub == "add":
        if len(context.args) < 2:
            await update.effective_chat.send_message("ℹ️ Usage: `/forcech add <channel_id> [invite_link]`", parse_mode="Markdown")
            return
        try:
            cid = int(context.args[1])
        except ValueError:
            await update.effective_chat.send_message("❌ Invalid channel_id.")
            return
        invite = context.args[2].strip() if len(context.args) > 2 else None
        title = None
        username = None
        try:
            chat = await context.bot.get_chat(cid)
            title = chat.title
            username = chat.username
        except Exception:
            pass
        await db.add_force_channel(cid, invite, title, username, update.effective_user.id)
        await update.effective_chat.send_message(f"✅ Force channel added/updated: `{cid}`", parse_mode="Markdown")
        return

    if sub == "remove":
        if len(context.args) < 2:
            await update.effective_chat.send_message("ℹ️ Usage: `/forcech remove <channel_id>`", parse_mode="Markdown")
            return
        try:
            cid = int(context.args[1])
        except ValueError:
            await update.effective_chat.send_message("❌ Invalid channel_id.")
            return
        await db.remove_force_channel(cid)
        await update.effective_chat.send_message(f"✅ Force channel removed: `{cid}`", parse_mode="Markdown")
        return

    await update.effective_chat.send_message("ℹ️ Usage: `/forcech add|remove|list ...`", parse_mode="Markdown")


async def setcaption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("🚫 Access denied. (Admin/Owner only)")
        return
    text = " ".join(context.args).strip() if context.args else ""
    if not text and update.effective_message and update.effective_message.reply_to_message:
        text = (update.effective_message.reply_to_message.text or "").strip()
    if not text:
        await update.effective_chat.send_message("ℹ️ Usage: `/setcaption <text>` (or reply to a text message)", parse_mode="Markdown")
        return
    db: Database = context.application.bot_data["db"]
    await db.set_setting("caption", text)
    await update.effective_chat.send_message("✅ Default caption set.")


async def removecaption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("🚫 Access denied. (Admin/Owner only)")
        return
    db: Database = context.application.bot_data["db"]
    await db.set_setting("caption", None)
    await update.effective_chat.send_message("🗑️ Default caption removed.")


async def settime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("🚫 Access denied. (Admin/Owner only)")
        return
    if not context.args:
        await update.effective_chat.send_message(
            "⏱️ *Auto-Delete Time*\n\n"
            "Set time after which files/messages delivered via links will be auto-deleted.\n\n"
            "✅ Usage:\n"
            "• `/settime 60` (seconds)\n"
            "• `/settime 5m`\n"
            "• `/settime 1h`\n"
            "• `/settime off`\n",
            parse_mode="Markdown",
        )
        return
    seconds = _parse_duration_seconds(context.args[0])
    if seconds is None:
        await update.effective_chat.send_message("❌ Invalid time. Examples: `60`, `5m`, `1h`, `off`", parse_mode="Markdown")
        return
    db: Database = context.application.bot_data["db"]
    await db.set_setting(SETTINGS_AUTODELETE_SECONDS, str(int(seconds)))
    if seconds <= 0:
        await update.effective_chat.send_message("✅ Auto-delete disabled.")
        return
    await update.effective_chat.send_message(f"✅ Auto-delete enabled: messages will be deleted after `{seconds}` seconds.", parse_mode="Markdown")


async def setstartimg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("🚫 Access denied. (Admin/Owner only)")
        return
    if not context.args:
        await update.effective_chat.send_message(
            "🖼️ *Start Image*\n\n"
            "Set an image URL that will be shown with `/start` message.\n\n"
            "✅ Usage:\n"
            "• `/setstartimg <image_url>`\n"
            "• `/setstartimg off`",
            parse_mode="Markdown",
        )
        return
    raw = context.args[0].strip()
    if raw.lower() in ("off", "remove", "none", "disable", "disabled"):
        db: Database = context.application.bot_data["db"]
        await db.set_setting(SETTINGS_START_IMG_URL, None)
        await update.effective_chat.send_message("✅ Start image removed.")
        return
    if not (raw.startswith("https://") or raw.startswith("http://")):
        await update.effective_chat.send_message("❌ Invalid URL. Must start with http:// or https://")
        return
    db: Database = context.application.bot_data["db"]
    await db.set_setting(SETTINGS_START_IMG_URL, raw)
    await update.effective_chat.send_message("✅ Start image set. Now `/start` will show the image.")


async def getemojiid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin helper: reply to a message containing Telegram custom (Premium) emojis to extract custom_emoji_id values.
    """
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("🚫 Access denied. (Admin/Owner only)")
        return
    if not update.effective_message or not update.effective_message.reply_to_message:
        await update.effective_chat.send_message("ℹ️ Reply to a message that contains Premium/custom emojis, then send: `/getemojiid`", parse_mode="Markdown")
        return

    msg = update.effective_message.reply_to_message
    entities = []
    if msg.entities:
        entities.extend(list(msg.entities))
    if msg.caption_entities:
        entities.extend(list(msg.caption_entities))

    ids: list[str] = []
    for e in entities:
        cid = getattr(e, "custom_emoji_id", None)
        if cid:
            ids.append(str(cid))

    uniq = []
    for cid in ids:
        if cid not in uniq:
            uniq.append(cid)

    if not uniq:
        await update.effective_chat.send_message(
            "⚠️ No custom emoji IDs found in that message.\n\n"
            "Tip: Premium/custom emojis are detectable only when they are *custom emoji entities* (not normal Unicode emojis).",
            parse_mode="Markdown",
        )
        return

    out = "\n".join([f"• `{cid}`" for cid in uniq])
    await update.effective_chat.send_message(
        "✅ *Custom Emoji IDs Found*\n\n"
        f"{out}\n\n"
        "Set for UI:\n"
        "• `/setuitemoji <name> <custom_emoji_id>`\n"
        "Example: `/setuitemoji lock 54545454545454545`",
        parse_mode="Markdown",
    )


async def setuitemoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Stores a mapping of UI emoji name -> custom_emoji_id in settings.
    (Actual UI replacement can be wired up later once you provide IDs.)
    """
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("🚫 Access denied. (Admin/Owner only)")
        return
    if len(context.args) < 1:
        await update.effective_chat.send_message(
            "🧩 *UI Emoji Settings*\n\n"
            "✅ Usage:\n"
            "• `/setuitemoji <name> <custom_emoji_id>`\n"
            "• `/setuitemoji <name> off`\n\n"
            "Example:\n"
            "• `/setuitemoji lock 54545454545454545`",
            parse_mode="Markdown",
        )
        return

    name = context.args[0].strip().lower()
    if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in name) or len(name) > 32:
        await update.effective_chat.send_message("❌ Invalid name. Use letters/numbers/`_`/`-` (max 32).", parse_mode="Markdown")
        return

    if len(context.args) < 2:
        await update.effective_chat.send_message("ℹ️ Missing value. Use: `/setuitemoji <name> <custom_emoji_id|off>`", parse_mode="Markdown")
        return

    val = context.args[1].strip()
    key = f"{SETTINGS_UI_EMOJI_PREFIX}{name}"
    db: Database = context.application.bot_data["db"]

    if val.lower() in ("off", "remove", "none", "disable", "disabled"):
        await db.set_setting(key, None)
        await update.effective_chat.send_message(f"✅ UI emoji removed for `{name}`.", parse_mode="Markdown")
        return

    if not val.isdigit():
        await update.effective_chat.send_message("❌ Invalid custom_emoji_id. It must be numeric.", parse_mode="Markdown")
        return

    await db.set_setting(key, val)
    await update.effective_chat.send_message(f"✅ UI emoji set: `{name}` -> `{val}`", parse_mode="Markdown")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("🚫 Access denied. (Admin/Owner only)")
        return
    db: Database = context.application.bot_data["db"]
    s = await db.stats()
    await update.effective_chat.send_message(
        "📊 *Bot Stats*\n\n" + "\n".join([f"• *{k}*: `{v}`" for k, v in s.items()]),
        parse_mode="Markdown",
    )


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("🚫 Access denied. (Admin/Owner only)")
        return
    if not update.effective_message or not update.effective_message.reply_to_message:
        await update.effective_chat.send_message("ℹ️ Reply to a message, then send: `/broadcast`", parse_mode="Markdown")
        return
    db: Database = context.application.bot_data["db"]
    user_ids = await db.list_user_ids()
    src = update.effective_message.reply_to_message
    ok = 0
    fail = 0
    for uid in user_ids:
        try:
            await context.bot.copy_message(chat_id=uid, from_chat_id=src.chat_id, message_id=src.message_id)
            ok += 1
        except Exception:
            fail += 1
    await update.effective_chat.send_message(
        "📣 *Broadcast Completed*\n\n"
        f"✅ Sent: `{ok}`\n"
        f"⚠️ Failed: `{fail}`",
        parse_mode="Markdown",
    )


def build_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(recheck_callback, pattern=r"^(recheck:|noop)"))

    # /batch uses non-command text inputs (start/end post links).
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, batch_link_input), group=0)

    app.add_handler(CommandHandler("getlink", getlink))
    app.add_handler(CommandHandler("batch", batch))
    app.add_handler(CommandHandler("custombatch", custombatch))
    app.add_handler(CallbackQueryHandler(custombatch_callback, pattern=r"^(cbgen|cbcancel)$"))
    app.add_handler(CommandHandler("addadmin", addadmin))
    app.add_handler(CommandHandler("removeadmin", removeadmin))
    app.add_handler(CommandHandler("addpremium", addpremium))
    app.add_handler(CommandHandler("removepremium", removepremium))
    app.add_handler(CommandHandler("gencode", gencode))
    app.add_handler(CommandHandler("forcech", forcech))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("setcaption", setcaption))
    app.add_handler(CommandHandler("removecaption", removecaption))
    app.add_handler(CommandHandler("settime", settime))
    app.add_handler(CommandHandler("setstartimg", setstartimg))
    app.add_handler(CommandHandler("getemojiid", getemojiid))
    app.add_handler(CommandHandler("setuitemoji", setuitemoji))

    app.add_handler(CommandHandler("redeem", redeem))

    # PTB v20+ uses uppercase filter shortcuts (VIDEO/AUDIO/PHOTO). Document is namespaced.
    media_filter = filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.PHOTO
    app.add_handler(MessageHandler(filters.ALL & media_filter, admin_media_ingest))
