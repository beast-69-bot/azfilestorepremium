import logging
from typing import Any, Dict, List, Optional
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bot.db import Database
from bot.deployment import (
    RUNNING_SUB_BOTS,
    get_status,
    set_status,
    log_checkpoint,
    get_deploy_logs,
    enqueue_deployment,
)
from bot.deployment.health import get_health_metrics, record_restart
from bot.dashboard.analytics import get_stats_metrics
from bot.security import decrypt_token

logger = logging.getLogger(__name__)

async def _get_sub_bot_by_username(db: Database, username: str) -> Optional[dict[str, Any]]:
    bots = await db.list_sub_bots()
    for b in bots:
        if b.get("bot_username") == username:
            return b
    return None

async def mbot_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not update.effective_user:
        return
    await q.answer()
    
    data = q.data or ""
    user_id = update.effective_user.id
    db: Database = context.application.bot_data["db"]
    cfg = context.application.bot_data["cfg"]
    defaults = getattr(context.application, "_defaults", None)

    from bot.handlers import _welcome_text, _format_custom_emojis_html, start_sub_bot, stop_sub_bot, SETTINGS_START_IMG_URL

    if data == "mbot_create":
        bots = await db.list_sub_bots()
        user_bots = [b for b in bots if b["added_by"] == user_id]
        if len(user_bots) >= 5:
            await q.message.reply_text("❌ You've reached the limit of 5 bots per user!")
            return
            
        context.user_data["addbot_state"] = "awaiting_token"
        context.user_data["addbot_data"] = {}
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="mbot_cancel")]])
        await q.message.edit_text(
            "🤖 <b>ᴄʀᴇᴀᴛᴇ ᴀ ɴᴇᴡ ʙᴏᴛ</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            "<blockquote><b>Step 1/2:</b> Send me your Bot Token.\n\n"
            "You can get a bot token from @BotFather.\n"
            "Example: <code>123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ</code></blockquote>",
            parse_mode="HTML",
            reply_markup=kb
        )
        
    elif data == "mbot_cancel":
        context.user_data.pop("addbot_state", None)
        context.user_data.pop("addbot_data", None)
        context.user_data.pop("mbot_setchan_wait", None)
        context.user_data.pop("mbot_fsub_wait", None)
        
        await q.message.edit_text(
            "❌ Bot creation cancelled.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ᴍᴀɪɴ ᴍᴇɴᴜ", callback_data="mbot_main")]])
        )

    elif data == "mbot_main":
        img_url = await db.get_setting(SETTINGS_START_IMG_URL)
        raw_text = _welcome_text()
        formatted_text = await _format_custom_emojis_html(raw_text, context)
        formatted_text += (
            "\n\n🤖 <b>ᴄʟᴏɴᴇᴅ ʙᴏᴛ ᴄʀᴇᴀᴛᴏʀ</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Aap is bot ki help se apna khud ka FileStore bot create kar sakte hain. "
            "Niche diye buttons use karein:"
        )
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("⚡ ᴄʀᴇᴀᴛᴇ ʙᴏᴛ", callback_data="mbot_create"),
                    InlineKeyboardButton("📋 ᴍʏ ʙᴏᴛꜱ", callback_data="mbot_list"),
                ]
            ]
        )
        await q.message.edit_text(formatted_text, parse_mode="HTML", reply_markup=kb)

    elif data == "mbot_list":
        bots = await db.list_sub_bots()
        user_bots = [b for b in bots if b["added_by"] == user_id]
        
        if not user_bots:
            await q.message.edit_text(
                "📋 <b>ᴍʏ ʙᴏᴛꜱ</b>\n━━━━━━━━━━━━━━━━━━\n\n"
                "Aapne abhi tak koi cloned bot nahi banaya hai.\n"
                "Niche diye button se bot banayein:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚡ ᴄʀᴇᴀᴛᴇ ʙᴏᴛ", callback_data="mbot_create")],
                    [InlineKeyboardButton("🔙 ᴍᴀɪɴ ᴍᴇɴᴜ", callback_data="mbot_main")]
                ])
            )
            return

        buttons = []
        for b in user_bots:
            uname = b.get("bot_username") or "unknown"
            status = get_status(b["token"])
            status_indicator = "🟢" if "Running" in status else ("🔴" if "Offline" in status else "🟡")
            buttons.append([InlineKeyboardButton(f"{status_indicator} @{uname}", callback_data=f"mbot_dash:{uname}")])
            
        buttons.append([InlineKeyboardButton("🔙 ᴍᴀɪɴ ᴍᴇɴᴜ", callback_data="mbot_main")])
        
        await q.message.edit_text(
            "📋 <b>ᴍʏ ʙᴏᴛꜱ</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            "Select a bot to configure settings:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data.startswith("mbot_dash:"):
        uname = data.split(":", 1)[1]
        bot_doc = await _get_sub_bot_by_username(db, uname)
        if not bot_doc or bot_doc["added_by"] != user_id:
            await q.message.reply_text("❌ Bot not found or access denied.")
            return
            
        token = bot_doc["token"]
        status = get_status(token)
        
        # Load commercial storage statistics
        stats = get_stats_metrics(token)
        
        text = (
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🤖 <b>@{uname}</b>\n"
            f"Status: {status}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📂 <b>Storage:</b> {stats['storage']}\n"
            f"👤 <b>Total Files:</b> {stats['files']:,}\n"
            f"📥 <b>Downloads:</b> {stats['downloads']:,}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("⚙️ Settings", callback_data=f"mbot_settings:{uname}"),
                    InlineKeyboardButton("📢 Force Subscribe", callback_data=f"mbot_fsub:{uname}"),
                ],
                [
                    InlineKeyboardButton("📂 Log Channel", callback_data=f"mbot_setchan:{uname}"),
                    InlineKeyboardButton("📊 Analytics", callback_data=f"mbot_analytics:{uname}"),
                ],
                [
                    InlineKeyboardButton("🔄 Restart", callback_data=f"mbot_restart:{uname}"),
                    InlineKeyboardButton("🗑 Delete", callback_data=f"mbot_del:{uname}"),
                ],
                [InlineKeyboardButton("🔙 Back to List", callback_data="mbot_list")]
            ]
        )
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

    elif data.startswith("mbot_settings:"):
        uname = data.split(":", 1)[1]
        bot_doc = await _get_sub_bot_by_username(db, uname)
        if not bot_doc or bot_doc["added_by"] != user_id:
            return
            
        token = bot_doc["token"]
        running = token in RUNNING_SUB_BOTS
        health = get_health_metrics(token, running)
        
        text = (
            f"⚙️ <b>ʙᴏᴛ ꜱᴇᴛᴛɪɴɢꜱ</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"◈ <b>Name:</b> {uname}\n"
            f"◈ <b>Username:</b> @{uname}\n"
            f"◈ <b>Uptime:</b> {health['uptime']}\n"
            f"◈ <b>Memory:</b> {health['memory']}\n"
            f"◈ <b>CPU:</b> {health['cpu']}\n"
            f"◈ <b>Last Restart:</b> {health['last_restart']}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 View Deployment Logs", callback_data=f"mbot_logs:{uname}")],
            [InlineKeyboardButton("🔙 Back to Dash", callback_data=f"mbot_dash:{uname}")]
        ])
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

    elif data.startswith("mbot_logs:"):
        uname = data.split(":", 1)[1]
        bot_doc = await _get_sub_bot_by_username(db, uname)
        if not bot_doc or bot_doc["added_by"] != user_id:
            return
            
        token = bot_doc["token"]
        logs = get_deploy_logs(token)
        log_text = "\n".join([f"• {log}" for log in logs])
        
        text = (
            f"📋 <b>ᴅᴇᴘʟᴏʏᴍᴇɴᴛ ʟᴏɢꜱ</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"{log_text}\n\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"mbot_settings:{uname}")]])
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

    elif data.startswith("mbot_analytics:"):
        uname = data.split(":", 1)[1]
        bot_doc = await _get_sub_bot_by_username(db, uname)
        if not bot_doc or bot_doc["added_by"] != user_id:
            return
            
        token = bot_doc["token"]
        stats = get_stats_metrics(token)
        
        text = (
            f"📊 <b>ʙᴏᴛ ᴀɴᴀʟʏᴛɪᴄꜱ</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"◈ <b>Bot:</b> @{uname}\n\n"
            f"• <b>Today's Active Users:</b> {stats['today_users']}\n"
            f"• <b>Today's Downloads:</b> {stats['today_downloads']}\n"
            f"• <b>Today's Unique Visitors:</b> {stats['today_unique']}\n"
            f"• <b>Today's Links Created:</b> {stats['today_links']}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Dash", callback_data=f"mbot_dash:{uname}")]])
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

    elif data.startswith("mbot_restart:"):
        uname = data.split(":", 1)[1]
        bot_doc = await _get_sub_bot_by_username(db, uname)
        if not bot_doc or bot_doc["added_by"] != user_id:
            return
            
        token = bot_doc["token"]
        set_status(token, "🟡 Deploying")
        log_checkpoint(token, "🔄 Restarting Bot Instance")
        
        if token in RUNNING_SUB_BOTS:
            await stop_sub_bot(token)
            
        await enqueue_deployment(token)
        record_restart(token)
        
        await q.message.reply_text("🔄 Restart command queued successfully!")
        
        q.data = f"mbot_dash:{uname}"
        await mbot_callback_router(update, context)

    elif data.startswith("mbot_del:"):
        uname = data.split(":", 1)[1]
        await q.message.edit_text(
            f"⚠️ <b>ᴅᴇʟᴇᴛᴇ @{uname}</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            "Are you sure you want to permanently delete this bot and its configurations?\n\n"
            "This action cannot be undone!",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ ʏᴇꜱ, ᴅᴇʟᴇᴛᴇ", callback_data=f"mbot_dodel:{uname}"),
                    InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"mbot_dash:{uname}"),
                ]
            ])
        )

    elif data.startswith("mbot_dodel:"):
        uname = data.split(":", 1)[1]
        bot_doc = await _get_sub_bot_by_username(db, uname)
        if bot_doc and bot_doc["added_by"] == user_id:
            token = bot_doc["token"]
            await stop_sub_bot(token)
            await db.remove_sub_bot(token)
            await db.clear_force_channels(uname)
            await q.message.reply_text(f"🗑️ @{uname} deleted successfully!")
        
        q.data = "mbot_list"
        await mbot_callback_router(update, context)

    elif data.startswith("mbot_setchan:"):
        uname = data.split(":", 1)[1]
        bot_doc = await _get_sub_bot_by_username(db, uname)
        if not bot_doc or bot_doc["added_by"] != user_id:
            return
            
        context.user_data["mbot_setchan_wait"] = uname
        await q.message.edit_text(
            "📢 <b>ꜱᴇᴛ ʟᴏɢ ᴄʜᴀɴɴᴇʟ</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            f"Current channel: <code>{bot_doc.get('log_channel_id') or 'Not Set'}</code>\n\n"
            "Send the new channel ID (e.g. <code>-1001234567890</code>).\n"
            f"Make sure @{uname} is an Admin in the channel with post permissions.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"mbot_dash:{uname}")]])
        )

    elif data.startswith("mbot_fsub:"):
        uname = data.split(":", 1)[1]
        bot_doc = await _get_sub_bot_by_username(db, uname)
        if not bot_doc or bot_doc["added_by"] != user_id:
            return
            
        channels = await db.list_force_channels(uname)
        ch_list = ""
        if channels:
            for idx, ch in enumerate(channels, 1):
                mode_str = "📩 Request" if ch["mode"] == "request" else "🔒 Force Join"
                title = ch.get("title") or ch.get("username") or str(ch["channel_id"])
                ch_list += f"  {idx}. <b>{title}</b> ({mode_str})\n"
        else:
            ch_list = "  <i>No channels added</i>"
            
        await q.message.edit_text(
            f"🔗 <b>ꜰᴏʀᴄᴇ ꜱᴜʙꜱᴄʀɪʙᴇ ꜱᴇᴛᴛɪɴɢꜱ</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            f"◈ <b>Bot:</b> @{uname}\n\n"
            f"◈ <b>Current Channels:</b>\n{ch_list}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("➕ Add Channel", callback_data=f"mbot_addfsub:{uname}"),
                    InlineKeyboardButton("➖ Remove Channel", callback_data=f"mbot_remfsub:{uname}"),
                ],
                [InlineKeyboardButton("🔙 Back to Dash", callback_data=f"mbot_dash:{uname}")]
            ])
        )

    elif data.startswith("mbot_addfsub:"):
        uname = data.split(":", 1)[1]
        bot_doc = await _get_sub_bot_by_username(db, uname)
        if not bot_doc or bot_doc["added_by"] != user_id:
            return
            
        context.user_data["mbot_fsub_wait"] = uname
        await q.message.edit_text(
            "➕ <b>ᴀᴅ档 ꜰᴏʀᴄᴇ ꜱᴜʙꜱᴄʀɪʙᴇ ᴄʜᴀɴɴᴇʟ</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            "Send the channel ID (e.g. <code>-1001234567890</code>) or channel username (e.g. <code>@MyChannel</code>).\n\n"
            f"Make sure @{uname} is an Admin in the channel with invite link permission.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"mbot_fsub:{uname}")]])
        )

    elif data.startswith("mbot_remfsub:"):
        uname = data.split(":", 1)[1]
        bot_doc = await _get_sub_bot_by_username(db, uname)
        if not bot_doc or bot_doc["added_by"] != user_id:
            return
            
        channels = await db.list_force_channels(uname)
        if not channels:
            await q.message.reply_text("❌ No channels to remove.")
            return
            
        buttons = []
        for ch in channels:
            title = ch.get("title") or ch.get("username") or str(ch["channel_id"])
            buttons.append([InlineKeyboardButton(f"🗑 {title}", callback_data=f"mbot_doremfsub:{uname}:{ch['channel_id']}")])
            
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data=f"mbot_fsub:{uname}")])
        await q.message.edit_text(
            "🗑️ <b>ʀᴇᴍᴏᴠᴇ ꜰᴏʀᴄᴇ ᴄʜᴀɴɴᴇʟ</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            "Select a channel to remove from force subscribe:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data.startswith("mbot_doremfsub:"):
        parts = data.split(":", 2)
        uname = parts[1]
        cid = int(parts[2])
        
        bot_doc = await _get_sub_bot_by_username(db, uname)
        if bot_doc and bot_doc["added_by"] == user_id:
            await db.remove_force_channel(cid, uname)
            await q.message.reply_text("✅ Force channel removed successfully!")
            
        q.data = f"mbot_fsub:{uname}"
        await mbot_callback_router(update, context)

    elif data.startswith("mbot_fsubmode:"):
        parts = data.split(":", 3)
        uname = parts[1]
        mode = parts[2]
        cid = int(parts[3])
        
        bot_doc = await _get_sub_bot_by_username(db, uname)
        if not bot_doc or bot_doc["added_by"] != user_id:
            return

        title = ""
        username = ""
        invite_link = ""
        
        try:
            decrypted_token = await decrypt_token(bot_doc["token"], db)
            sub_bot_client = Bot(token=decrypted_token)
            chat = await sub_bot_client.get_chat(cid)
            title = chat.title
            username = chat.username
            
            if mode == "direct":
                try:
                    invite_link_obj = await sub_bot_client.create_chat_invite_link(chat_id=cid)
                    invite_link = invite_link_obj.invite_link
                except Exception:
                    if username:
                        invite_link = f"https://t.me/{username}"
                    else:
                        invite_link = ""
        except Exception as e:
            logger.warning("FSub mode init: failed to resolve chat member info using sub-bot: %s", e)

        await db.add_force_channel(
            channel_id=cid,
            bot_username=uname,
            mode=mode,
            invite_link=invite_link or None,
            title=title or None,
            username=username or None,
            added_by=user_id
        )
        
        await q.message.reply_text(f"✅ Channel added in {'Direct' if mode == 'direct' else 'Request'} mode!")
        
        q.data = f"mbot_fsub:{uname}"
        await mbot_callback_router(update, context)
