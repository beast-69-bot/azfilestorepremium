from __future__ import annotations

import time
from typing import Any, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
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
            rows.append([InlineKeyboardButton(text=f"Join: {title}", url=url)])
        else:
            rows.append([InlineKeyboardButton(text=f"Required: {title}", callback_data="noop")])
    rows.append([InlineKeyboardButton(text="I've Joined (Recheck)", callback_data=f"recheck:{recheck_code}")])
    return InlineKeyboardMarkup(rows)


async def _send_file(chat_id: int, file_row: dict[str, Any], caption: Optional[str], context: ContextTypes.DEFAULT_TYPE) -> None:
    t = file_row["file_type"]
    fid = file_row["tg_file_id"]
    if caption and len(caption) > 1024:
        caption = caption[:1020] + "..."
    if t == "document":
        await context.bot.send_document(chat_id=chat_id, document=fid, caption=caption)
    elif t == "video":
        await context.bot.send_video(chat_id=chat_id, video=fid, caption=caption)
    elif t == "audio":
        await context.bot.send_audio(chat_id=chat_id, audio=fid, caption=caption)
    elif t == "photo":
        await context.bot.send_photo(chat_id=chat_id, photo=fid, caption=caption)
    else:
        await context.bot.send_document(chat_id=chat_id, document=fid, caption=caption)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    args = context.args or []
    if not update.effective_user or not update.effective_chat:
        return
    if not args:
        await update.effective_chat.send_message(
            "Access files only via generated links.\n"
            "Redeem premium token: /redeem <token>"
        )
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
        await chat.send_message("Invalid link.")
        return

    ok_join, channels = await _joined_all_force_channels(user.id, context)
    if not ok_join:
        await chat.send_message(
            "You must join all required channels to access this content.",
            reply_markup=_join_keyboard(channels, code),
        )
        return

    if link["access"] == "premium" and not await db.is_premium_active(user.id):
        await chat.send_message("Premium required for this link. Redeem a token with /redeem <token>.")
        return

    caption = await db.get_setting("caption")

    if link["target_type"] == "file":
        file_row = await db.get_file(link["target_id"])
        if not file_row:
            await chat.send_message("File not found.")
            return
        await _send_file(chat.id, file_row, caption, context)
        await db.mark_link_used(code)
        return

    if link["target_type"] == "batch":
        file_ids = await db.get_batch_file_ids(link["target_id"])
        if not file_ids:
            await chat.send_message("Batch is empty.")
            return
        if len(file_ids) > 100:
            await chat.send_message("Batch too large.")
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
            await chat.send_message("Message not found.")
            return
        try:
            await context.bot.copy_message(
                chat_id=chat.id,
                from_chat_id=msg_row["from_chat_id"],
                message_id=msg_row["message_id"],
            )
        except Exception:
            await chat.send_message("Unable to deliver this message (it may have been deleted or inaccessible).")
            return
        await db.mark_link_used(code)
        return

    await chat.send_message("Unsupported link type.")


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

    # If batch mode is active, only collect files (do not generate links).
    state = context.user_data.get("batch_state")
    file_db_id = await db.save_file(
        tg_file_id=media["file_id"],
        file_unique_id=media.get("unique_id"),
        file_type=media["file_type"],
        file_name=media.get("file_name"),
        added_by=update.effective_user.id,
    )

    if state is not None:
        state.setdefault("file_ids", []).append(file_db_id)
        await update.effective_chat.send_message(f"Added to batch: `{file_db_id}`")
        return

    normal_code = new_code()
    prem_code = new_code()
    await db.create_link(normal_code, "file", file_db_id, "normal", update.effective_user.id)
    await db.create_link(prem_code, "file", file_db_id, "premium", update.effective_user.id)

    await update.effective_chat.send_message(
        f"Saved file as ID `{file_db_id}`\n"
        f"Normal link: {_deep_link(context, normal_code)}\n"
        f"Premium link: {_deep_link(context, prem_code)}"
    )


async def getlink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("Not allowed.")
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
        await update.effective_chat.send_message("Reply to a message (file or normal message) or use /getlink <file_id>.")
        return

    normal_code = new_code()
    prem_code = new_code()
    if target_file_id is not None:
        if not await db.get_file(target_file_id):
            await update.effective_chat.send_message("File not found.")
            return
        await db.create_link(normal_code, "file", target_file_id, "normal", update.effective_user.id)
        await db.create_link(prem_code, "file", target_file_id, "premium", update.effective_user.id)
    else:
        await db.create_link(normal_code, "msg", target_msg_id, "normal", update.effective_user.id)
        await db.create_link(prem_code, "msg", target_msg_id, "premium", update.effective_user.id)

    await update.effective_chat.send_message(
        f"Normal link: {_deep_link(context, normal_code)}\n"
        f"Premium link: {_deep_link(context, prem_code)}"
    )


async def batch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("Not allowed.")
        return
    if not update.effective_chat or not update.effective_user:
        return

    state = context.user_data.get("batch_state")
    if not state:
        context.user_data["batch_state"] = {"file_ids": []}
        await update.effective_chat.send_message(
            "Batch mode started. Send files now, then send /batch again to generate the links."
        )
        return

    file_ids = state.get("file_ids") or []
    if not file_ids:
        context.user_data.pop("batch_state", None)
        await update.effective_chat.send_message("Batch cancelled (no files).")
        return

    db: Database = context.application.bot_data["db"]
    batch_id = await db.create_batch(update.effective_user.id, file_ids)
    context.user_data.pop("batch_state", None)

    normal_code = new_code()
    prem_code = new_code()
    await db.create_link(normal_code, "batch", batch_id, "normal", update.effective_user.id)
    await db.create_link(prem_code, "batch", batch_id, "premium", update.effective_user.id)

    await update.effective_chat.send_message(
        f"Batch created (ID `{batch_id}`, {len(file_ids)} files)\n"
        f"Normal link: {_deep_link(context, normal_code)}\n"
        f"Premium link: {_deep_link(context, prem_code)}"
    )


async def batch_ingest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Backward compatibility: keep symbol, but ingest is handled in admin_media_ingest.
    return


async def custombatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("Not allowed.")
        return
    db: Database = context.application.bot_data["db"]
    files = await db.list_recent_files(limit=15)
    context.user_data["custom_sel"] = set()
    await update.effective_chat.send_message(
        "Select files for Custom Batch:",
        reply_markup=_custombatch_keyboard(files, set()),
    )


def _custombatch_keyboard(files: list[dict[str, Any]], selected: set[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for f in files:
        fid = int(f["id"])
        name = f.get("file_name") or f.get("file_type") or "file"
        action = "Remove" if fid in selected else "Add"
        rows.append([InlineKeyboardButton(text=f"{action} #{fid} ({name})", callback_data=f"cbsel:{fid}")])
    rows.append([InlineKeyboardButton(text="Generate Links", callback_data="cbdone")])
    rows.append([InlineKeyboardButton(text="Cancel", callback_data="cbcancel")])
    return InlineKeyboardMarkup(rows)


async def custombatch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not update.effective_user:
        return
    await q.answer()
    if not await _is_admin_or_owner(update, context):
        await q.edit_message_text("Not allowed.")
        return

    db: Database = context.application.bot_data["db"]
    selected: set[int] = context.user_data.get("custom_sel") or set()
    data = q.data or ""

    if data.startswith("cbsel:"):
        fid = int(data.split(":", 1)[1])
        if fid in selected:
            selected.remove(fid)
        else:
            selected.add(fid)
        context.user_data["custom_sel"] = selected
        files = await db.list_recent_files(limit=15)
        await q.edit_message_reply_markup(reply_markup=_custombatch_keyboard(files, selected))
        return

    if data == "cbcancel":
        context.user_data.pop("custom_sel", None)
        await q.edit_message_text("Custom batch cancelled.")
        return

    if data == "cbdone":
        if not selected:
            await q.edit_message_text("No files selected.")
            return
        file_ids = sorted(list(selected))
        batch_id = await db.create_batch(update.effective_user.id, file_ids)
        context.user_data.pop("custom_sel", None)

        normal_code = new_code()
        prem_code = new_code()
        await db.create_link(normal_code, "batch", batch_id, "normal", update.effective_user.id)
        await db.create_link(prem_code, "batch", batch_id, "premium", update.effective_user.id)

        await q.edit_message_text(
            f"Custom batch created (ID `{batch_id}`, {len(file_ids)} files)\n"
            f"Normal link: {_deep_link(context, normal_code)}\n"
            f"Premium link: {_deep_link(context, prem_code)}"
        )
        return


async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    cfg = context.application.bot_data["cfg"]
    if not _is_owner(update, cfg):
        await update.effective_chat.send_message("Owner only.")
        return
    if not context.args:
        await update.effective_chat.send_message("Usage: /addadmin <user_id>")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.effective_chat.send_message("Invalid user_id.")
        return
    db: Database = context.application.bot_data["db"]
    await db.add_admin(uid, update.effective_user.id)
    await update.effective_chat.send_message(f"Added admin: `{uid}`")


async def removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    cfg = context.application.bot_data["cfg"]
    if not _is_owner(update, cfg):
        await update.effective_chat.send_message("Owner only.")
        return
    if not context.args:
        await update.effective_chat.send_message("Usage: /removeadmin <user_id>")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.effective_chat.send_message("Invalid user_id.")
        return
    db: Database = context.application.bot_data["db"]
    await db.remove_admin(uid)
    await update.effective_chat.send_message(f"Removed admin: `{uid}`")


async def addpremium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("Not allowed.")
        return
    if not context.args:
        await update.effective_chat.send_message("Usage: /addpremium <user_id> [days]")
        return
    try:
        uid = int(context.args[0])
        days = int(context.args[1]) if len(context.args) > 1 else 1
    except ValueError:
        await update.effective_chat.send_message("Invalid args.")
        return
    db: Database = context.application.bot_data["db"]
    until = await db.add_premium_seconds(uid, max(1, days) * DAY_SECONDS)
    await update.effective_chat.send_message(f"Premium added. User `{uid}` premium_until: `{until}` (unix)")


async def removepremium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("Not allowed.")
        return
    if not context.args:
        await update.effective_chat.send_message("Usage: /removepremium <user_id>")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.effective_chat.send_message("Invalid user_id.")
        return
    db: Database = context.application.bot_data["db"]
    await db.set_premium_until(uid, 0)
    await update.effective_chat.send_message(f"Premium removed for `{uid}`.")


async def gencode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("Not allowed.")
        return
    db: Database = context.application.bot_data["db"]
    token = new_token()
    await db.create_token(token, update.effective_user.id, DAY_SECONDS)
    await update.effective_chat.send_message(f"Token (1-day premium, one-time): `{token}`")


async def redeem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not update.effective_chat or not update.effective_user:
        return
    if not context.args:
        await update.effective_chat.send_message("Usage: /redeem <token>")
        return
    token = context.args[0].strip()
    db: Database = context.application.bot_data["db"]
    grant = await db.redeem_token(token, update.effective_user.id)
    if not grant:
        await update.effective_chat.send_message("Invalid or already-used token.")
        return
    until = await db.add_premium_seconds(update.effective_user.id, grant)
    await update.effective_chat.send_message(f"Redeemed. Premium active until `{until}` (unix).")


async def forcech(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("Not allowed.")
        return
    db: Database = context.application.bot_data["db"]

    if not context.args or context.args[0].lower() == "list":
        chans = await db.list_force_channels()
        if not chans:
            await update.effective_chat.send_message(
                "No force channels set.\n"
                "Usage:\n"
                "/forcech add <channel_id> [invite_link]\n"
                "/forcech remove <channel_id>\n"
                "/forcech list"
            )
            return
        lines = []
        for ch in chans:
            extra = ch.get("invite_link") or (f"@{ch['username']}" if ch.get("username") else "")
            name = ch.get("title") or ""
            lines.append(f"- `{ch['channel_id']}` {name} {extra}".strip())
        await update.effective_chat.send_message("Force channels:\n" + "\n".join(lines))
        return

    sub = context.args[0].lower()
    if sub == "add":
        if len(context.args) < 2:
            await update.effective_chat.send_message("Usage: /forcech add <channel_id> [invite_link]")
            return
        try:
            cid = int(context.args[1])
        except ValueError:
            await update.effective_chat.send_message("Invalid channel_id.")
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
        await update.effective_chat.send_message(f"Added/updated force channel `{cid}`.")
        return

    if sub == "remove":
        if len(context.args) < 2:
            await update.effective_chat.send_message("Usage: /forcech remove <channel_id>")
            return
        try:
            cid = int(context.args[1])
        except ValueError:
            await update.effective_chat.send_message("Invalid channel_id.")
            return
        await db.remove_force_channel(cid)
        await update.effective_chat.send_message(f"Removed force channel `{cid}`.")
        return

    await update.effective_chat.send_message("Usage: /forcech add|remove|list ...")


async def setcaption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("Not allowed.")
        return
    text = " ".join(context.args).strip() if context.args else ""
    if not text and update.effective_message and update.effective_message.reply_to_message:
        text = (update.effective_message.reply_to_message.text or "").strip()
    if not text:
        await update.effective_chat.send_message("Usage: /setcaption <text> (or reply to a text message)")
        return
    db: Database = context.application.bot_data["db"]
    await db.set_setting("caption", text)
    await update.effective_chat.send_message("Caption set.")


async def removecaption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("Not allowed.")
        return
    db: Database = context.application.bot_data["db"]
    await db.set_setting("caption", None)
    await update.effective_chat.send_message("Caption removed.")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("Not allowed.")
        return
    db: Database = context.application.bot_data["db"]
    s = await db.stats()
    await update.effective_chat.send_message("Stats:\n" + "\n".join([f"- {k}: `{v}`" for k, v in s.items()]))


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await update.effective_chat.send_message("Not allowed.")
        return
    if not update.effective_message or not update.effective_message.reply_to_message:
        await update.effective_chat.send_message("Reply to a message with /broadcast to send it to all users.")
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
    await update.effective_chat.send_message(f"Broadcast done. Sent: `{ok}`, failed: `{fail}`")


def build_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(recheck_callback, pattern=r"^(recheck:|noop)"))

    app.add_handler(CommandHandler("getlink", getlink))
    app.add_handler(CommandHandler("batch", batch))
    app.add_handler(CommandHandler("custombatch", custombatch))
    app.add_handler(CallbackQueryHandler(custombatch_callback, pattern=r"^(cbsel:\d+|cbdone|cbcancel)$"))
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

    app.add_handler(CommandHandler("redeem", redeem))

    # PTB v20+ uses uppercase filter shortcuts (VIDEO/AUDIO/PHOTO). Document is namespaced.
    media_filter = filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.PHOTO
    app.add_handler(MessageHandler(filters.ALL & media_filter, admin_media_ingest))
