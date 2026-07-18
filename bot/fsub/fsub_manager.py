import logging
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes
from bot.db import Database

logger = logging.getLogger(__name__)

def _parse_channel_ref(text: str) -> str | int | None:
    text = text.strip()
    if not text:
        return None
    if text.startswith("@"):
        return text
    try:
        return int(text)
    except ValueError:
        return None

async def mbot_new_fsub_channel_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.effective_message.text or "").strip()
    user_id = update.effective_user.id
    db: Database = context.application.bot_data["db"]
    
    uname = context.user_data.get("mbot_fsub_wait")
    if not uname:
        return
        
    from bot.dashboard import _get_sub_bot_by_username
    bot_doc = await _get_sub_bot_by_username(db, uname)
    if not bot_doc or bot_doc["added_by"] != user_id:
        return

    ref = _parse_channel_ref(text)
    if ref is None:
        await update.effective_message.reply_text("❌ Invalid channel format. Send a username like @MyChannel or ID like -100xxxx.")
        return

    status_msg = await update.effective_message.reply_text("⏳ Resolving channel details and checking permissions...")

    try:
        from bot.security import decrypt_token
        decrypted_token = await decrypt_token(bot_doc["token"], db)
        sub_bot_client = Bot(token=decrypted_token)
        chat = await sub_bot_client.get_chat(ref)
    except Exception as e:
        await status_msg.edit_text(f"❌ Channel not found or bot has no access to it. Error: {e}")
        return

    if chat.type != "channel":
        await status_msg.edit_text("❌ Only channels are supported for force subscription.")
        return

    try:
        sub_bot_me = await sub_bot_client.get_me()
        member = await sub_bot_client.get_chat_member(chat_id=chat.id, user_id=sub_bot_me.id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            await status_msg.edit_text(f"❌ Bot @{uname} is not an Admin in this channel. Add it as admin and try again.")
            return
    except Exception as e:
        await status_msg.edit_text(f"❌ Failed to verify admin status. Make sure the bot @{uname} is an Admin in the channel: {e}")
        return

    context.user_data.pop("mbot_fsub_wait", None)
    
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔓 Direct Mode", callback_data=f"mbot_fsubmode:{uname}:direct:{chat.id}"),
                InlineKeyboardButton("🛂 Request Mode", callback_data=f"mbot_fsubmode:{uname}:request:{chat.id}"),
            ],
            [InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"mbot_fsub:{uname}")]
        ]
    )
    await status_msg.edit_text(
        f"➕ <b>ᴄʜᴀɴɴᴇʟ ʀᴇꜱᴏʟᴠᴇᴅ</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"▸ <b>Title:</b> {chat.title}\n"
        f"▸ <b>ID:</b> <code>{chat.id}</code>\n\n"
        "Choose subscription mode for this channel:",
        parse_mode="HTML",
        reply_markup=kb
    )
