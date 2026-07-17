import httpx
import logging
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bot.db import Database
from bot.security import encrypt_token, decrypt_token
from bot.deployment import enqueue_deployment

logger = logging.getLogger(__name__)

async def mbot_token_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    token = (update.effective_message.text or "").strip()
    user_id = update.effective_user.id
    db: Database = context.application.bot_data["db"]
    
    if ":" not in token or len(token) < 20:
        await update.effective_message.reply_text(
            "❌ Invalid token format. Send a valid token from @BotFather, or click Cancel.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="mbot_cancel")]])
        )
        return
        
    status_msg = await update.effective_message.reply_text("⏳ Validating bot token...")
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            url = f"https://api.telegram.org/bot{token}/getMe"
            resp = await client.get(url)
            if resp.status_code != 200:
                raise ValueError(f"Invalid bot token (HTTP {resp.status_code})")
            data = resp.json()
            if not data.get("ok"):
                raise ValueError(data.get("description"))
            bot_info = data["result"]
            bot_username = bot_info["username"]
            bot_first_name = bot_info["first_name"]
    except Exception as e:
        await status_msg.edit_text(
            f"❌ <b>ᴛᴏᴋᴇɴ ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ ꜰᴀɪʟᴇᴅ</b>\n\n[c]{str(e)}[/c]\n\nPlease send a valid token or click Cancel.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="mbot_cancel")]])
        )
        return
        
    existing = await db.list_sub_bots()
    
    is_registered = False
    for b in existing:
        decrypted = await decrypt_token(b["token"], db)
        if decrypted == token or b["bot_username"] == bot_username:
            is_registered = True
            break
            
    if is_registered:
        await status_msg.edit_text(
            f"⚠️ Bot @{bot_username} is already registered by someone.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ᴍᴀɪɴ ᴍᴇɴᴜ", callback_data="mbot_main")]])
        )
        context.user_data.pop("addbot_state", None)
        return
        
    context.user_data["addbot_data"] = {
        "token": token,
        "bot_username": bot_username,
        "bot_first_name": bot_first_name
    }
    context.user_data["addbot_state"] = "awaiting_channel"
    
    await status_msg.edit_text(
        f"✅ <b>ᴛᴏᴋᴇɴ ᴠᴇʀɪꜰɪᴇᴅ</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"Bot: <b>{bot_first_name}</b> (@{bot_username})\n\n"
        f"<b>Step 2/2:</b> Send the <b>Log Channel ID</b>.\n\n"
        f"1. Create a public or private Telegram channel.\n"
        f"2. Add @{bot_username} as an Admin with post/delete permissions.\n"
        f"3. Send the channel ID here (e.g. <code>-1001234567890</code>).",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="mbot_cancel")]])
    )

async def mbot_channel_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    channel_text = (update.effective_message.text or "").strip()
    user_id = update.effective_user.id
    db: Database = context.application.bot_data["db"]
    
    try:
        channel_id = int(channel_text)
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid format. Please send a numeric ID starting with -100.")
        return
        
    status_msg = await update.effective_message.reply_text("⏳ Testing channel access with sub-bot...")
    
    addbot_data = context.user_data.get("addbot_data") or {}
    token = addbot_data.get("token")
    bot_username = addbot_data.get("bot_username")
    
    if not token or not bot_username:
        await status_msg.edit_text("Session expired. Please restart bot creation.")
        context.user_data.pop("addbot_state", None)
        return

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            send_url = f"https://api.telegram.org/bot{token}/sendMessage"
            resp = await client.post(send_url, json={"chat_id": channel_id, "text": "✅ Log Channel verified successfully!"})
            if resp.status_code != 200:
                raise ValueError(resp.json().get("description") or "Failed to send verification message")
            msg_id = resp.json()["result"]["message_id"]
            
            del_url = f"https://api.telegram.org/bot{token}/deleteMessage"
            await client.post(del_url, json={"chat_id": channel_id, "message_id": msg_id})
    except Exception as e:
        await status_msg.edit_text(
            f"❌ <b>ᴄᴀɴɴᴏᴛ ᴀᴄᴄᴇꜱꜱ ᴄʜᴀɴɴᴇʟ</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            f"Error: [c]{str(e)[:120]}[/c]\n\n"
            f"Make sure @{bot_username} is added as an Admin in the channel with post permissions, then try again.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="mbot_cancel")]])
        )
        return

    # Encrypt token before saving to database
    encrypted = await encrypt_token(token, db)
    await db.add_sub_bot(token=encrypted, added_by=user_id, log_channel_id=channel_id, bot_username=bot_username)
    
    # Enqueue deployment
    await enqueue_deployment(encrypted)
    
    context.user_data.pop("addbot_state", None)
    context.user_data.pop("addbot_data", None)
    
    await status_msg.edit_text(
        f"🎉 <b>ʙᴏᴛ ᴄʀᴇᴀᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"▸ <b>Bot:</b> @{bot_username}\n"
        f"▸ <b>Log Channel:</b> <code>{channel_id}</code>\n"
        f"▸ <b>Status:</b> 🟡 Deploying (in queue)\n\n"
        "Aapka bot deploy ho raha hai. Kuch seconds me active status check karne ke liye dashboard use karein.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ ʙᴏᴛ ᴅᴀꜱʜʙᴏᴀʀᴅ", callback_data=f"mbot_dash:{bot_username}")],
            [InlineKeyboardButton("🔙 ᴍʏ ʙᴏᴛꜱ", callback_data="mbot_list")]
        ])
    )

async def mbot_new_channel_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    channel_text = (update.effective_message.text or "").strip()
    user_id = update.effective_user.id
    db: Database = context.application.bot_data["db"]
    
    uname = context.user_data.get("mbot_setchan_wait")
    if not uname:
        return
        
    try:
        channel_id = int(channel_text)
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid format. Please send a numeric ID starting with -100.")
        return
        
    from bot.dashboard import _get_sub_bot_by_username
    bot_doc = await _get_sub_bot_by_username(db, uname)
    if not bot_doc or bot_doc["added_by"] != user_id:
        return

    status_msg = await update.effective_message.reply_text("⏳ Testing channel access with sub-bot...")
    
    try:
        decrypted_token = await decrypt_token(bot_doc["token"], db)
        async with httpx.AsyncClient(timeout=8) as client:
            send_url = f"https://api.telegram.org/bot{decrypted_token}/sendMessage"
            resp = await client.post(send_url, json={"chat_id": channel_id, "text": "✅ New Log Channel verified successfully!"})
            if resp.status_code != 200:
                raise ValueError(resp.json().get("description") or "Failed to send verification message")
            msg_id = resp.json()["result"]["message_id"]
            
            del_url = f"https://api.telegram.org/bot{decrypted_token}/deleteMessage"
            await client.post(del_url, json={"chat_id": channel_id, "message_id": msg_id})
    except Exception as e:
        await status_msg.edit_text(
            f"❌ <b>ᴄᴀɴɴᴏᴛ ᴀᴄᴄᴇꜱꜱ ᴄʜᴀɴɴᴇʟ</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            f"Error: [c]{str(e)[:120]}[/c]\n\n"
            f"Make sure @{uname} is added as an Admin in the channel with post permissions, then try again.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"mbot_dash:{uname}")]])
        )
        return

    await db.update_sub_bot_channel(bot_doc["token"], channel_id)
    
    from bot.deployment import RUNNING_SUB_BOTS
    running_app = RUNNING_SUB_BOTS.get(bot_doc["token"])
    if running_app:
        running_app.bot_data["sub_bot_info"]["log_channel_id"] = channel_id
        
    context.user_data.pop("mbot_setchan_wait", None)
    
    await status_msg.edit_text(
        f"✅ <b>ʟᴏɢ ᴄʜᴀɴɴᴇʟ ᴜᴘᴅᴀᴛᴇᴅ</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"▸ <b>Bot:</b> @{uname}\n"
        f"▸ <b>New Channel ID:</b> <code>{channel_id}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ᴅᴀꜱʜʙᴏᴀʀᴅ", callback_data=f"mbot_dash:{uname}")]])
    )


async def mbot_new_owner_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    owner_text = (update.effective_message.text or "").strip()
    user_id = update.effective_user.id
    db: Database = context.application.bot_data["db"]
    
    uname = context.user_data.get("mbot_setowner_wait")
    if not uname:
        return
        
    try:
        new_owner_id = int(owner_text)
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid format. Please send a numeric Telegram User ID.")
        return
        
    from bot.dashboard import _get_sub_bot_by_username
    bot_doc = await _get_sub_bot_by_username(db, uname)
    if not bot_doc or bot_doc["added_by"] != user_id:
        return

    status_msg = await update.effective_message.reply_text("⏳ Updating owner ID...")
    
    await db.update_sub_bot_owner(bot_doc["token"], new_owner_id)
    
    from bot.deployment import RUNNING_SUB_BOTS
    running_app = RUNNING_SUB_BOTS.get(bot_doc["token"])
    if running_app:
        running_app.bot_data["sub_bot_info"]["owner_id"] = new_owner_id
        
    context.user_data.pop("mbot_setowner_wait", None)
    
    await status_msg.edit_text(
        f"👑 <b>ᴏᴡɴᴇʀ ɪᴅ ᴜᴘᴅᴀᴛᴇᴅ</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"▸ <b>Bot:</b> @{uname}\n"
        f"▸ <b>New Owner ID:</b> <code>{new_owner_id}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ᴅᴀꜱʜʙᴏᴀʀᴅ", callback_data=f"mbot_dash:{uname}")]])
    )
