from __future__ import annotations

import asyncio
import datetime
import html
import json
import os
import re
import tempfile
import time
from typing import Any, Optional
from urllib.parse import parse_qs, quote, urlparse

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, Update
from telegram.constants import ChatMemberStatus, MessageEntityType
from telegram.error import RetryAfter
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
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
SETTINGS_PAY_UPI = "pay_upi"
SETTINGS_PAY_NAME = "pay_name"
SETTINGS_PAY_TEXT = "pay_text"
SETTINGS_PAY_ADMIN_MSGS_PREFIX = "pay_admin_msgs:"
UI_EMOJI_CACHE_KEY = "_ui_emoji_map_cache"
UI_EMOJI_CACHE_TS_KEY = "_ui_emoji_map_cache_ts"
UI_EMOJI_CACHE_TTL_SECONDS = 120
PRESET_UI_EMOJI_IDS = {
    "info": "6059839048065750021",
    "timer": "5440621591387980068",
    "hourglass": "6082464824111928051",
    "warning": "5420323339723881652",
    "check": "6082567989226378348",
    "cross": "5348386339777698814",
    "id": "6289762537344864636",
    "ticket": "6082162475594165182",
    "user": "6062079075374077548",
    "stats": "5231200819986047254",
    "pin": "5397782960512444700",
    "announce": "6289406458786221863",
    "outbox": "6062007740262258850",
    "box": "6082192871077712762",
    "refresh": "5382178536872223059",
    "lock_closed": "5296369303661067030",
    "lock": "5231302159739395058",
    "unlock": "5296369303661067030",
    "clock": "5440621591387980068",
    "image": "5375074927252621134",
    "trash": "5033287275287413303",
    "denied": "5348386339777698814",
    "puzzle": "6294142703907116473",
    "receipt": "5032963696746300412",
    "premium_star": "5202218878888850186",
}
UNICODE_TO_UI_NAME = {
    "ℹ": "info",
    "⏱": "timer",
    "⏳": "hourglass",
    "⚠": "warning",
    "✅": "check",
    "❌": "cross",
    "🆔": "id",
    "🎟": "ticket",
    "👤": "user",
    "📊": "stats",
    "📌": "pin",
    "📣": "announce",
    "📤": "outbox",
    "📦": "box",
    "🔄": "refresh",
    "🔐": "lock_closed",
    "🔒": "lock",
    "🔓": "unlock",
    "🕒": "clock",
    "🖼": "image",
    "🗑": "trash",
    "🚫": "denied",
    "🧩": "puzzle",
    "🧾": "receipt",
    "⭐": "premium_star",
}

PAY_PLANS: dict[str, dict[str, Any]] = {
    "1d": {"label": "1 Day", "days": 1, "amount": 9},
    "7d": {"label": "7 Days", "days": 7, "amount": 29},
    "30d": {"label": "1 Month", "days": 30, "amount": 99},
}

BSETTINGS_DOCS: dict[str, dict[str, str]] = {
    "getlink": {
        "title": "🔗 /getlink",
        "body": (
            "Purpose:\nGenerate dual links (Normal + Premium) for a file/message.\n\n"
            "Usage:\n"
            "1) Reply to a file/message and send: /getlink\n"
            "2) Or direct by id: /getlink <file_id>\n\n"
            "Output:\n"
            "• Normal Link -> normal+premium users\n"
            "• Premium Link -> premium users only\n\n"
            "Notes:\n"
            "• Force-channel checks always apply\n"
            "• Premium link checks active premium in real time"
        ),
    },
    "batch": {
        "title": "📦 /batch",
        "body": (
            "Purpose:\nCreate one channel-post range batch link pair.\n\n"
            "Flow:\n"
            "1) /batch\n"
            "2) Send START post link\n"
            "3) Send END post link\n"
            "4) Bot creates Normal + Premium links\n\n"
            "Cancel:\n"
            "• /batch cancel\n\n"
            "Requirements:\n"
            "• Bot must be admin in source channel"
        ),
    },
    "custombatch": {
        "title": "🧩 /custombatch",
        "body": (
            "Purpose:\nManually collect files/media and generate one batch pair.\n\n"
            "Flow:\n"
            "1) /custombatch\n"
            "2) Send multiple files/media\n"
            "3) Tap Generate Link\n\n"
            "Buttons:\n"
            "• Generate Link\n"
            "• Cancel Process\n\n"
            "Extra:\n"
            "• Temp upload messages are cleaned on finish/cancel"
        ),
    },
    "addadmin": {
        "title": "👮 /addadmin",
        "body": "Owner-only.\n\nUsage:\n/addadmin <user_id>\n\nAdds user as bot admin.",
    },
    "removeadmin": {
        "title": "🚫 /removeadmin",
        "body": "Owner-only.\n\nUsage:\n/removeadmin <user_id>\n\nRemoves bot admin role.",
    },
    "addpremium": {
        "title": "⭐ /addpremium",
        "body": (
            "Admin/Owner.\n\nUsage:\n"
            "/addpremium <user_id> [days]\n\n"
            "Example:\n/addpremium 123456789 7\n\n"
            "Adds premium duration (default 1 day)."
        ),
    },
    "removepremium": {
        "title": "❌ /removepremium",
        "body": "Admin/Owner.\n\nUsage:\n/removepremium <user_id>\n\nDisables premium instantly.",
    },
    "gencode": {
        "title": "🎟 /gencode",
        "body": (
            "Admin/Owner.\n\nUsage:\n"
            "/gencode\n"
            "/gencode <count>\n\n"
            "Examples:\n"
            "/gencode\n"
            "/gencode 10\n\n"
            "Generates one-time premium tokens (1 day each, max 20 at once)."
        ),
    },
    "forcech": {
        "title": "📣 /forcech",
        "body": (
            "Admin/Owner force-join management.\n\n"
            "Add flow:\n"
            "1) /forcech\n"
            "2) Send channel id/username\n"
            "3) Select mode (Direct/Request)\n\n"
            "Other:\n"
            "• /forcech list\n"
            "• /forcech remove <channel_id|@username>\n"
            "• /forcech reset"
        ),
    },
    "forcechdebug": {
        "title": "🧪 /forcechdebug",
        "body": (
            "Admin/Owner debug helper.\n\nUsage:\n"
            "/forcechdebug <user_id>\n\n"
            "Shows per-channel evaluation:\n"
            "• mode\n• joined\n• request\n• pass\n• error details"
        ),
    },
    "broadcast": {
        "title": "📢 /broadcast",
        "body": (
            "Admin/Owner.\n\nUsage:\n"
            "Reply to any message then send /broadcast\n\n"
            "Bot copies that message to all known users."
        ),
    },
    "stats": {
        "title": "📊 /stats",
        "body": "Admin/Owner.\n\nShows users, files, links, premium, tokens and other counts.",
    },
    "premiumdb": {
        "title": "📥 /premiumdb",
        "body": (
            "Admin/Owner.\n\n"
            "Exports full premium records to an Excel file.\n\n"
            "Output columns include:\n"
            "• user_id, name, username\n"
            "• premium_until (unix + UTC)\n"
            "• active status\n"
            "• created_at, last_seen"
        ),
    },
    "setcaption": {
        "title": "📝 /setcaption",
        "body": (
            "Admin/Owner.\n\nUsage:\n"
            "/setcaption <text>\n"
            "or reply to text and use /setcaption\n\n"
            "HTML supported for styling:\n"
            "<b>bold</b> <i>italic</i> <code>code</code>"
        ),
    },
    "removecaption": {
        "title": "🗑 /removecaption",
        "body": "Admin/Owner.\n\nRemoves default caption.",
    },
    "settime": {
        "title": "⏱ /settime",
        "body": (
            "Admin/Owner auto-delete setting.\n\nUsage:\n"
            "/settime 60\n"
            "/settime 5m\n"
            "/settime 1h\n"
            "/settime off\n\n"
            "Applied on files/messages delivered via links."
        ),
    },
    "setstartimg": {
        "title": "🖼 /setstartimg",
        "body": (
            "Admin/Owner.\n\nUsage:\n"
            "/setstartimg <image_url>\n"
            "/setstartimg off\n\n"
            "Adds/removes image in /start welcome."
        ),
    },
    "setpay": {
        "title": "💳 /setpay",
        "body": (
            "Admin/Owner payment config.\n\nUsage:\n"
            "/setpay view\n"
            "/setpay upi <upi_id>\n"
            "/setpay name <payee_name>\n"
            "/setpay text <instructions>\n"
            "/setpay clearupi\n\n"
            "Bot will auto-generate plan-wise UPI QR for users."
        ),
    },
    "getemojiid": {
        "title": "🆔 /getemojiid",
        "body": "Admin/Owner.\n\nReply to a message with custom emojis and run /getemojiid to extract custom_emoji_id values.",
    },
    "setuitemoji": {
        "title": "😀 /setuitemoji",
        "body": (
            "Admin/Owner.\n\nUsage:\n"
            "/setuitemoji <name> <custom_emoji_id>\n"
            "/setuitemoji <name> off\n\n"
            "Stores UI emoji id mapping."
        ),
    },
    "setemojipreset": {
        "title": "✨ /setemojipreset",
        "body": "Admin/Owner.\n\nApplies predefined custom emoji id preset in one shot.",
    },
}


def _bsettings_keyboard() -> InlineKeyboardMarkup:
    # Intentionally exclude /getlink, /batch, /custombatch from this panel.
    button_items: list[tuple[str, str]] = [
        ("addadmin", "👮 Add Admin"),
        ("removeadmin", "🚫 Remove Admin"),
        ("addpremium", "⭐ Add Premium"),
        ("removepremium", "❌ Remove Premium"),
        ("gencode", "🎟 Generate Codes"),
        ("forcech", "📣 Force Channel"),
        ("forcechdebug", "🧪 Force Debug"),
        ("broadcast", "📢 Broadcast"),
        ("stats", "📊 Stats"),
        ("premiumdb", "📥 Premium DB"),
        ("setcaption", "📝 Set Caption"),
        ("removecaption", "🗑 Remove Caption"),
        ("settime", "⏱ Set AutoDelete"),
        ("setstartimg", "🖼 Start Image"),
        ("setpay", "💳 Payment"),
        ("getemojiid", "🆔 Emoji IDs"),
        ("setuitemoji", "😀 UI Emoji"),
        ("setemojipreset", "✨ Emoji Preset"),
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(button_items), 2):
        row: list[InlineKeyboardButton] = []
        k1, label1 = button_items[i]
        row.append(InlineKeyboardButton(label1, callback_data=f"bset:{k1}"))
        if i + 1 < len(button_items):
            k2, label2 = button_items[i + 1]
            row.append(InlineKeyboardButton(label2, callback_data=f"bset:{k2}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _bset_forcech_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ Add", callback_data="bset:forcech_add"),
                InlineKeyboardButton("📋 List", callback_data="bset:forcech_list"),
            ],
            [
                InlineKeyboardButton("➖ Remove", callback_data="bset:forcech_remove"),
                InlineKeyboardButton("🗑 Reset", callback_data="bset:forcech_reset"),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="bset:back")],
        ]
    )


def _bset_forcech_panel_text() -> str:
    return (
        "📣 [b]Force Channel Manager[/b]\n\n"
        "[u]Instructions[/u]\n"
        "1) [b]Add[/b] pe click karo\n"
        "2) Channel ID/username bhejo ([c]-100xxxx[/c] ya [c]@channel[/c])\n"
        "3) Mode select karo: [b]Direct[/b] ya [b]Request[/b]\n\n"
        "[u]Quick Actions[/u]\n"
        "• [b]List[/b]: saved required channels dekho\n"
        "• [b]Remove[/b]: specific channel hatao\n"
        "• [b]Reset[/b]: sab force channels clear karo"
    )


def _bset_setpay_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👁 View", callback_data="bset:setpay_view"),
                InlineKeyboardButton("🆔 Set UPI", callback_data="bset:setpay_upi"),
            ],
            [
                InlineKeyboardButton("👤 Set Name", callback_data="bset:setpay_name"),
                InlineKeyboardButton("🧾 Set Text", callback_data="bset:setpay_text"),
            ],
            [
                InlineKeyboardButton("🗑 Clear UPI", callback_data="bset:setpay_clearupi"),
                InlineKeyboardButton("⬅️ Back", callback_data="bset:back"),
            ],
        ]
    )


def _clear_bsettings_wait_states(context: ContextTypes.DEFAULT_TYPE) -> None:
    for k in (
        "bset_addadmin_wait",
        "bset_removeadmin_wait",
        "bset_gencode_wait",
        "bset_forcech_remove_wait",
        "bset_addpremium_wait",
        "bset_removepremium_wait",
        "bset_setcaption_wait",
        "bset_settime_wait",
        "bset_setstartimg_wait",
        "bset_setpay_wait",
    ):
        context.user_data.pop(k, None)


def _welcome_text() -> str:
    return (
        "🔐 [b]Secure Access System[/b]\n\n"
        "[b]❝ Access is protected. Delivery is verified. ❞[/b]\n\n"
        "📌 [u]How It Works[/u]\n"
        "› Open your unique deep link\n"
        "› Complete channel verification\n"
        "› Unlock instantly\n\n"
        "⭐ [b]Upgrade to Premium[/b]\n"
        "› No Ads\n"
        "› Instant Delivery\n"
        "› VIP Links\n\n"
        "🎟 [c]/redeem <token>[/c]\n"
        "💎 [c]/plan[/c] | 🛒 [c]/pay[/c]\n\n"
        "🚫 Forwarded links will not work."
    )


def _now() -> int:
    return int(time.time())


def _u16len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _invalidate_ui_emoji_cache(context: ContextTypes.DEFAULT_TYPE) -> None:
    bd = context.application.bot_data
    bd.pop(UI_EMOJI_CACHE_KEY, None)
    bd.pop(UI_EMOJI_CACHE_TS_KEY, None)


async def _get_ui_emoji_map(context: ContextTypes.DEFAULT_TYPE) -> dict[str, str]:
    bd = context.application.bot_data
    cached = bd.get(UI_EMOJI_CACHE_KEY)
    cached_at = float(bd.get(UI_EMOJI_CACHE_TS_KEY, 0) or 0)
    if isinstance(cached, dict) and (time.time() - cached_at) < UI_EMOJI_CACHE_TTL_SECONDS:
        return cached

    db: Database = bd["db"]
    name_to_id: dict[str, str] = {}
    for name, preset in PRESET_UI_EMOJI_IDS.items():
        v = await db.get_setting(f"{SETTINGS_UI_EMOJI_PREFIX}{name}")
        db_val = (v or "").strip()
        preset_val = (preset or "").strip()
        if db_val.isdigit():
            name_to_id[name] = db_val
        elif preset_val.isdigit():
            name_to_id[name] = preset_val
        else:
            name_to_id[name] = ""

    bd[UI_EMOJI_CACHE_KEY] = name_to_id
    bd[UI_EMOJI_CACHE_TS_KEY] = time.time()
    return name_to_id


async def _build_custom_emoji_entities(text: str, context: ContextTypes.DEFAULT_TYPE) -> list[MessageEntity]:
    name_to_id = await _get_ui_emoji_map(context)

    entities: list[MessageEntity] = []
    off = 0
    i = 0
    vs16 = "\ufe0f"
    while i < len(text):
        ch = text[i]
        token = ch
        step = 1
        # Handle variation-selector style emoji sequences like "ℹ️", "⚠️", "🗑️".
        if i + 1 < len(text) and text[i + 1] == vs16:
            token = ch + text[i + 1]
            step = 2

        name = UNICODE_TO_UI_NAME.get(ch)
        if name:
            eid = name_to_id.get(name)
            if eid and eid.isdigit():
                entities.append(
                    MessageEntity(
                        type=MessageEntityType.CUSTOM_EMOJI,
                        offset=off,
                        length=_u16len(token),
                        custom_emoji_id=eid,
                    )
                )
        off += _u16len(token)
        i += step
    return entities


def _extract_style_entities(raw_text: str) -> tuple[str, list[MessageEntity]]:
    marker_to_type = {
        "[b]": MessageEntityType.BOLD,
        "[i]": MessageEntityType.ITALIC,
        "[u]": MessageEntityType.UNDERLINE,
        "[c]": MessageEntityType.CODE,
    }
    closing_to_open = {"[/b]": "[b]", "[/i]": "[i]", "[/u]": "[u]", "[/c]": "[c]"}
    markers = sorted(list(marker_to_type.keys()) + list(closing_to_open.keys()), key=len, reverse=True)

    out: list[str] = []
    entities: list[MessageEntity] = []
    stack: list[tuple[str, int]] = []
    i = 0
    off = 0
    while i < len(raw_text):
        matched = None
        for mk in markers:
            if raw_text.startswith(mk, i):
                matched = mk
                break
        if not matched:
            ch = raw_text[i]
            out.append(ch)
            off += _u16len(ch)
            i += 1
            continue

        if matched in marker_to_type:
            stack.append((matched, off))
        else:
            expected_open = closing_to_open[matched]
            if stack and stack[-1][0] == expected_open:
                open_mk, start_off = stack.pop()
                if off > start_off:
                    entities.append(
                        MessageEntity(
                            type=marker_to_type[open_mk],
                            offset=start_off,
                            length=off - start_off,
                        )
                    )
            else:
                # Unbalanced closing marker: keep it as literal text.
                out.append(matched)
                off += _u16len(matched)
        i += len(matched)

    return "".join(out), entities


async def _send_emoji_text(
    chat_id: int,
    text: str,
    context: ContextTypes.DEFAULT_TYPE,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    disable_web_page_preview: Optional[bool] = None,
) -> Any:
    clean_text, style_entities = _extract_style_entities(text)
    emoji_entities = await _build_custom_emoji_entities(clean_text, context)
    entities = sorted([*style_entities, *emoji_entities], key=lambda e: (e.offset, e.length))
    return await context.bot.send_message(
        chat_id=chat_id,
        text=clean_text,
        entities=entities,
        reply_markup=reply_markup,
        disable_web_page_preview=disable_web_page_preview,
    )


async def _edit_emoji_text(
    chat_id: int,
    message_id: int,
    text: str,
    context: ContextTypes.DEFAULT_TYPE,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    disable_web_page_preview: Optional[bool] = None,
) -> Any:
    clean_text, style_entities = _extract_style_entities(text)
    emoji_entities = await _build_custom_emoji_entities(clean_text, context)
    entities = sorted([*style_entities, *emoji_entities], key=lambda e: (e.offset, e.length))
    return await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=clean_text,
        entities=entities,
        reply_markup=reply_markup,
        disable_web_page_preview=disable_web_page_preview,
    )


async def _send_html_text(
    chat_id: int,
    html_text: str,
    context: ContextTypes.DEFAULT_TYPE,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    disable_web_page_preview: Optional[bool] = None,
) -> Any:
    return await context.bot.send_message(
        chat_id=chat_id,
        text=html_text,
        parse_mode="HTML",
        reply_markup=reply_markup,
        disable_web_page_preview=disable_web_page_preview,
    )


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


def _pay_admin_msgs_key(request_id: int) -> str:
    return f"{SETTINGS_PAY_ADMIN_MSGS_PREFIX}{int(request_id)}"


async def _save_pay_admin_msg_ref(db: Database, request_id: int, chat_id: int, message_id: int) -> None:
    key = _pay_admin_msgs_key(request_id)
    raw = (await db.get_setting(key)) or "[]"
    try:
        refs = json.loads(raw)
        if not isinstance(refs, list):
            refs = []
    except Exception:
        refs = []
    ref = [int(chat_id), int(message_id)]
    if ref not in refs:
        refs.append(ref)
    await db.set_setting(key, json.dumps(refs, separators=(",", ":")))


async def _get_pay_admin_msg_refs(db: Database, request_id: int) -> list[tuple[int, int]]:
    raw = (await db.get_setting(_pay_admin_msgs_key(request_id))) or "[]"
    try:
        refs = json.loads(raw)
    except Exception:
        refs = []
    out: list[tuple[int, int]] = []
    if isinstance(refs, list):
        for item in refs:
            if isinstance(item, list) and len(item) == 2:
                try:
                    out.append((int(item[0]), int(item[1])))
                except Exception:
                    pass
    return out


async def _clear_pay_admin_msg_refs(db: Database, request_id: int) -> None:
    await db.set_setting(_pay_admin_msgs_key(request_id), None)


async def _sync_pay_admin_status(
    context: ContextTypes.DEFAULT_TYPE,
    request_id: int,
    text: str,
) -> None:
    db: Database = context.application.bot_data["db"]
    refs = await _get_pay_admin_msg_refs(db, request_id)
    for chat_id, message_id in refs:
        try:
            await _edit_emoji_text(chat_id, message_id, text, context)
        except Exception:
            pass
    await _clear_pay_admin_msg_refs(db, request_id)


def _normalize_start_code(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    # Remove common invisible chars from copy/paste.
    s = s.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")

    # If full URL is provided, extract ?start=...
    if "t.me/" in s and "start=" in s:
        try:
            qs = parse_qs(urlparse(s).query)
            vals = qs.get("start") or []
            if vals:
                s = vals[0]
        except Exception:
            pass

    # If fragment includes "start=..."
    if "start=" in s:
        try:
            s = s.split("start=", 1)[1]
        except Exception:
            pass

    # Trim punctuation often included by chat copy.
    s = s.strip(" \t\r\n<>.,;:()[]{}\"'")

    # Keep only valid deep-link payload chars if noisy text is pasted.
    m = re.search(r"[A-Za-z0-9_-]{6,128}", s)
    return m.group(0) if m else s


async def _upsert_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return
    db: Database = context.application.bot_data["db"]
    await db.upsert_user(update.effective_user.id, update.effective_user.first_name, update.effective_user.username)


async def _joined_all_force_channels(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> tuple[bool, list[dict[str, Any]]]:
    # Bot owner/admin should not be blocked by force-join checks.
    cfg = context.application.bot_data["cfg"]
    db: Database = context.application.bot_data["db"]
    if int(user_id) == int(cfg.owner_id) or await db.is_admin(int(user_id)):
        return True, []
    ok, missing, _ = await _joined_all_force_channels_details(user_id, context)
    return ok, missing


async def _joined_all_force_channels_details(
    user_id: int, context: ContextTypes.DEFAULT_TYPE
) -> tuple[bool, list[dict[str, Any]], list[dict[str, Any]]]:
    db: Database = context.application.bot_data["db"]
    channels = await db.list_force_channels()
    if not channels:
        return True, [], []
    bot = context.bot
    missing: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    for ch in channels:
        cid = int(ch["channel_id"])
        mode = (ch.get("mode") or "direct").lower()
        has_request = False
        is_joined = False
        member_err = ""
        request_api_err = ""
        if mode == "request":
            has_request = await db.has_force_join_request(cid, user_id)
            if not has_request:
                has_request, request_api_err = await _has_pending_join_request_via_api(
                    cid, user_id, context, invite_link=ch.get("invite_link")
                )
                if has_request:
                    # Cache it locally so next checks are fast and resilient.
                    await db.add_force_join_request(cid, user_id)
        try:
            member = await bot.get_chat_member(chat_id=cid, user_id=user_id)
            is_joined = member.status not in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED)
        except Exception as e1:
            # Retry once for transient network/API issues.
            try:
                await asyncio.sleep(0.25)
                member = await bot.get_chat_member(chat_id=cid, user_id=user_id)
                is_joined = member.status not in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED)
            except Exception as e2:
                # If membership check fails, we can still allow request-mode users
                # if join request was captured via ChatJoinRequest update.
                is_joined = False
                member_err = f"member_check_failed:{type(e2).__name__}"

        if mode == "request":
            # OR logic: request sent OR user joined => pass
            passed = bool(has_request or is_joined)
            if not passed:
                missing.append(ch)
        else:
            passed = bool(is_joined)
            if not passed:
                missing.append(ch)
        details.append(
            {
                "channel_id": cid,
                "mode": mode,
                "joined": is_joined,
                "request": has_request,
                "passed": passed,
                "member_error": member_err,
                "request_api_error": request_api_err,
            }
        )
    return (len(missing) == 0), missing, details


async def _has_pending_join_request_via_api(
    channel_id: int,
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    invite_link: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Fallback for request-mode: check pending join requests directly from Telegram.
    Useful if join_request update was missed while bot was offline.
    """
    fn = getattr(context.bot, "get_chat_join_requests", None)
    if not fn:
        return await _has_pending_join_request_via_raw_api(
            channel_id=channel_id, user_id=user_id, context=context, invite_link=invite_link
        )

    # Fast path: query by user_id directly (supported by Bot API).
    # This avoids scanning hundreds of pending requests and keeps bot responsive.
    try:
        kwargs_fast: dict[str, Any] = {"chat_id": channel_id, "user_id": int(user_id), "limit": 1}
        if invite_link and invite_link.startswith("http"):
            kwargs_fast["invite_link"] = invite_link
        reqs = await asyncio.wait_for(fn(**kwargs_fast), timeout=4.0)
        if reqs:
            return True, ""
    except TypeError:
        # Some PTB versions may not expose user_id parameter; continue with fallback.
        pass
    except Exception as e:
        # Continue with fallback scan; include error only if fallback also fails.
        fast_err = f"api_error_fast:{type(e).__name__}"
    else:
        fast_err = ""

    async def _scan(kwargs: dict[str, Any]) -> tuple[bool, str]:
        offset: Optional[int] = None
        for _ in range(2):  # keep fallback small to avoid slowdown
            k = dict(kwargs)
            k["chat_id"] = channel_id
            k["limit"] = 100
            if offset is not None:
                # PTB/Bot API naming changed over versions, try both.
                k["offset_requester_user_id"] = offset
            try:
                reqs = await asyncio.wait_for(fn(**k), timeout=4.0)
            except TypeError:
                # Compatibility fallback.
                if "offset_requester_user_id" in k:
                    k.pop("offset_requester_user_id", None)
                    k["offset_user_id"] = offset
                try:
                    reqs = await asyncio.wait_for(fn(**k), timeout=4.0)
                except Exception as e:
                    return False, f"api_error:{type(e).__name__}"
            except Exception as e:
                return False, f"api_error:{type(e).__name__}"

            if not reqs:
                return False, ""
            last_uid = None
            for r in reqs:
                uid = getattr(getattr(r, "from_user", None), "id", None)
                if uid is None:
                    continue
                last_uid = int(uid)
                if int(uid) == int(user_id):
                    return True, ""
            if last_uid is None:
                return False, ""
            offset = int(last_uid) + 1
        return False, ""

    # First try invite-link scoped search for better accuracy in request mode.
    if invite_link and invite_link.startswith("http"):
        ok, err = await _scan({"invite_link": invite_link})
        if ok:
            return True, ""
        # Continue to global scan if not found.

    ok, err = await _scan({})
    if ok:
        return True, ""
    if fast_err and not err:
        return False, fast_err
    return False, err


async def _has_pending_join_request_via_raw_api(
    channel_id: int,
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    invite_link: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Raw Bot API fallback when PTB wrapper method is unavailable.
    """
    token = getattr(context.bot, "token", None)
    if not token:
        return False, "raw_no_token"
    url = f"https://api.telegram.org/bot{token}/getChatJoinRequests"
    payload: dict[str, Any] = {"chat_id": channel_id, "user_id": int(user_id), "limit": 1}
    if invite_link and invite_link.startswith("http"):
        payload["invite_link"] = invite_link
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(url, json=payload)
            data = r.json()
        if not data.get("ok"):
            return False, f"raw_api_error:{data.get('description', 'unknown')}"
        reqs = data.get("result") or []
        return (len(reqs) > 0), ""
    except Exception as e:
        return False, f"raw_api_error:{type(e).__name__}"


def _join_keyboard(channels: list[dict[str, Any]], recheck_code: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for ch in channels:
        title = ch.get("title") or str(ch["channel_id"])
        mode = (ch.get("mode") or "direct").lower()
        url = ch.get("invite_link")
        if not url and ch.get("username"):
            url = f"https://t.me/{ch['username']}"
        if url:
            if mode == "request":
                rows.append([InlineKeyboardButton(text=f"🛂 Send Join Request: {title}", url=url)])
            else:
                rows.append([InlineKeyboardButton(text=f"📢 Join Channel: {title}", url=url)])
        else:
            rows.append([InlineKeyboardButton(text=f"🔒 Required: {title}", callback_data="noop")])
    rows.append([InlineKeyboardButton(text="✅ I've Joined (Recheck)", callback_data=f"recheck:{recheck_code}")])
    return InlineKeyboardMarkup(rows)


def _access_link_keyboard(normal_url: str, premium_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(text="🔓 Open Normal Link", url=normal_url)],
            [InlineKeyboardButton(text="⭐ Open Premium Link", url=premium_url)],
        ]
    )


def _parse_channel_ref(s: str) -> Optional[str | int]:
    val = (s or "").strip()
    if not val:
        return None
    if val.startswith("@") and len(val) > 1:
        return val
    if val.startswith("-100") and val[1:].isdigit():
        try:
            return int(val)
        except ValueError:
            return None
    if val.isdigit() and val.startswith("100"):
        try:
            return -int(val)
        except ValueError:
            return None
    return None


async def _send_file(chat_id: int, file_row: dict[str, Any], caption: Optional[str], context: ContextTypes.DEFAULT_TYPE) -> None:
    t = file_row["file_type"]
    fid = file_row["tg_file_id"]
    if caption and len(caption) > 1024:
        caption = caption[:1020] + "..."
    msg = None
    if t == "document":
        msg = await context.bot.send_document(chat_id=chat_id, document=fid, caption=caption, parse_mode="HTML")
    elif t == "video":
        msg = await context.bot.send_video(chat_id=chat_id, video=fid, caption=caption, parse_mode="HTML")
    elif t == "audio":
        msg = await context.bot.send_audio(chat_id=chat_id, audio=fid, caption=caption, parse_mode="HTML")
    elif t == "photo":
        msg = await context.bot.send_photo(chat_id=chat_id, photo=fid, caption=caption, parse_mode="HTML")
    else:
        msg = await context.bot.send_document(chat_id=chat_id, document=fid, caption=caption, parse_mode="HTML")

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
    # Preferred source: PTB-parsed args.
    code = " ".join(args).strip() if args else ""
    # Fallback: parse from raw command text for edge clients/copy-paste cases.
    if not code and update.effective_message and update.effective_message.text:
        t = update.effective_message.text.strip()
        parts = t.split(maxsplit=1)
        if len(parts) > 1:
            code = parts[1]
    code = _normalize_start_code(code)
    if not code:
        db: Database = context.application.bot_data["db"]
        img_url = await db.get_setting(SETTINGS_START_IMG_URL)
        raw_text = _welcome_text()
        text, style_entities = _extract_style_entities(raw_text)
        emoji_entities = await _build_custom_emoji_entities(text, context)
        entities = sorted([*style_entities, *emoji_entities], key=lambda e: (e.offset, e.length))
        if img_url:
            try:
                await update.effective_chat.send_photo(photo=img_url, caption=text, caption_entities=entities)
                return
            except Exception:
                # Fallback to text-only if URL is invalid/unreachable.
                pass
        await update.effective_chat.send_message(text=text, entities=entities)
        return
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
        await _send_emoji_text(chat.id, "❌ Invalid or expired link.", context)
        return

    ok_join, channels = await _joined_all_force_channels(user.id, context)
    if not ok_join:
        await _send_emoji_text(
            chat.id,
            "🚫 Access Locked\n\n"
            "You must join all required channels to continue.\n"
            "After joining, tap Recheck ✅.",
            context,
            reply_markup=_join_keyboard(channels, code),
        )
        return

    if link["access"] == "premium" and not await db.is_premium_active(user.id):
        await _send_emoji_text(
            chat.id,
            "⭐ [b]Premium Required[/b]\n\n"
            "This link is for premium users only.\n"
            "• Redeem token: [c]/redeem <token>[/c]\n"
            "• Plan buy karo: [c]/pay[/c]",
            context,
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
        msg = await _send_emoji_text(
            update.effective_chat.id,
            f"✅ {count} file save ho gayi.\n\nLink generate karu ya process cancel karu?",
            context,
            reply_markup=_custombatch_prompt_keyboard(),
        )
        cb_state["prompt_message_id"] = msg.message_id
        return

    normal_code = new_code()
    prem_code = new_code()
    await db.create_link(normal_code, "file", file_db_id, "normal", update.effective_user.id)
    await db.create_link(prem_code, "file", file_db_id, "premium", update.effective_user.id)
    normal_url = _deep_link(context, normal_code)
    premium_url = _deep_link(context, prem_code)

    await update.effective_chat.send_message(
        "✅ File Saved Successfully\n\n"
        f"🆔 File ID: {file_db_id}\n\n"
        "🔓 Normal Link:\n"
        f"{normal_url}\n\n"
        "⭐ Premium Link:\n"
        f"{premium_url}",
        reply_markup=_access_link_keyboard(normal_url, premium_url),
    )


async def getlink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await _send_emoji_text(update.effective_chat.id, "🚫 Access denied. (Admin/Owner only)", context)
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
        await _send_emoji_text(
            update.effective_chat.id,
            "ℹ️ How to use /getlink\n\n"
            "1) Reply to any message/file and send /getlink\n"
            "2) Or use: /getlink <file_id>",
            context,
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
    normal_url = _deep_link(context, normal_code)
    premium_url = _deep_link(context, prem_code)

    await _send_emoji_text(
        update.effective_chat.id,
        "✅ Links Generated\n\n"
        "🔓 Normal Link:\n"
        f"{normal_url}\n\n"
        "⭐ Premium Link:\n"
        f"{premium_url}",
        context,
        reply_markup=_access_link_keyboard(normal_url, premium_url),
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
    # When /forcech guided flow is active, ignore this handler.
    if context.user_data.get("forcech_state"):
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
        await _send_emoji_text(update.effective_chat.id, "🚫 Access denied. (Admin/Owner only)", context)
        return
    # Start a temporary "collect files" flow.
    context.user_data["custombatch_state"] = {"file_ids": [], "prompt_message_id": None}
    await _send_emoji_text(
        update.effective_chat.id,
        "🧩 Custom Batch Mode Started\n\n"
        "📤 Files / media bhejo.\n"
        "Main unko custom batch me add karta rahunga.\n\n"
        "❌ Cancel anytime: press Cancel Process button.",
        context,
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
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            "✅ Custom Batch Created Successfully!\n\n"
            f"📦 Total Files: {len(file_ids)}\n\n"
            "🔓 Normal Access Link:\n"
            f"{_deep_link(context, normal_code)}\n\n"
            "⭐ Premium Access Link:\n"
            f"{_deep_link(context, prem_code)}\n\n"
            "ℹ️ Rules:\n"
            "• Required channels join karna mandatory hai\n"
            "• Premium link sirf premium users ke liye kaam karega",
            context,
        )
        return


async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    cfg = context.application.bot_data["cfg"]
    if not _is_owner(update, cfg):
        await _send_emoji_text(update.effective_chat.id, "🚫 Owner only.", context)
        return
    if not context.args:
        await _send_emoji_text(update.effective_chat.id, "ℹ️ Usage: /addadmin <user_id>", context)
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await _send_emoji_text(update.effective_chat.id, "❌ Invalid user_id.", context)
        return
    db: Database = context.application.bot_data["db"]
    await db.add_admin(uid, update.effective_user.id)
    await _send_emoji_text(update.effective_chat.id, f"✅ Admin added: {uid}", context)


async def removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    cfg = context.application.bot_data["cfg"]
    if not _is_owner(update, cfg):
        await _send_emoji_text(update.effective_chat.id, "🚫 Owner only.", context)
        return
    if not context.args:
        await _send_emoji_text(update.effective_chat.id, "ℹ️ Usage: /removeadmin <user_id>", context)
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await _send_emoji_text(update.effective_chat.id, "❌ Invalid user_id.", context)
        return
    db: Database = context.application.bot_data["db"]
    await db.remove_admin(uid)
    await _send_emoji_text(update.effective_chat.id, f"✅ Admin removed: {uid}", context)


async def addpremium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await _send_emoji_text(update.effective_chat.id, "🚫 Access denied. (Admin/Owner only)", context)
        return
    if not context.args:
        await _send_emoji_text(update.effective_chat.id, "ℹ️ Usage: /addpremium <user_id> [days]", context)
        return
    try:
        uid = int(context.args[0])
        days = int(context.args[1]) if len(context.args) > 1 else 1
    except ValueError:
        await _send_emoji_text(update.effective_chat.id, "❌ Invalid args.", context)
        return
    db: Database = context.application.bot_data["db"]
    grant_days = max(1, days)
    until = await db.add_premium_seconds(uid, grant_days * DAY_SECONDS)
    expiry_utc = datetime.datetime.utcfromtimestamp(until).strftime("%Y-%m-%d %H:%M:%S UTC")

    user_notified = False
    try:
        await _send_html_text(
            uid,
            "✅ <b>Premium Activated</b>\n"
            "<i>Your premium access has been enabled.</i>\n\n"
            f"⏳ <b>Duration:</b> {grant_days} day(s)\n"
            f"🕒 <b>Expires:</b> <code>{html.escape(expiry_utc)}</code>",
            context,
        )
        user_notified = True
    except Exception:
        user_notified = False

    await _send_emoji_text(
        update.effective_chat.id,
        "✅ Premium Granted\n\n"
        f"👤 User: {uid}\n"
        f"⏳ Days: {grant_days}\n"
        f"🕒 premium_until (unix): {until}\n"
        f"📣 User notified: {'Yes' if user_notified else 'No (user must /start bot)'}",
        context,
    )


async def removepremium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await _send_emoji_text(update.effective_chat.id, "🚫 Access denied. (Admin/Owner only)", context)
        return
    if not context.args:
        await _send_emoji_text(update.effective_chat.id, "ℹ️ Usage: /removepremium <user_id>", context)
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await _send_emoji_text(update.effective_chat.id, "❌ Invalid user_id.", context)
        return
    db: Database = context.application.bot_data["db"]
    await db.set_premium_until(uid, 0)
    await _send_emoji_text(update.effective_chat.id, f"✅ Premium removed for {uid}.", context)


async def gencode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await _send_emoji_text(update.effective_chat.id, "🚫 Access denied. (Admin/Owner only)", context)
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
    await _generate_tokens_and_send(update.effective_chat.id, update.effective_user.id, count, db, context)


async def _generate_tokens_and_send(
    chat_id: int,
    generated_by: int,
    count: int,
    db: Database,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    tokens: list[str] = []
    for _ in range(count):
        t = new_token()
        await db.create_token(t, generated_by, DAY_SECONDS)
        tokens.append(t)

    if count == 1:
        await _send_emoji_text(
            chat_id,
            "🎟️ Token Generated\n\n"
            f"{tokens[0]}\n\n"
            "⭐ Grants: 1 day premium\n"
            "🔒 One-time use only",
            context,
        )
        return

    token_lines = "\n".join(tokens)
    await _send_emoji_text(
        chat_id,
        "🎟️ Tokens Generated\n\n"
        f"🧾 Total: {count}\n\n"
        f"{token_lines}\n\n"
        "⭐ Each grants: 1 day premium\n"
        "🔒 Each is one-time use only\n\n"
        "ℹ️ Users redeem: /redeem <token>",
        context,
    )


async def redeem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not update.effective_chat or not update.effective_user:
        return
    if not context.args:
        await _send_emoji_text(update.effective_chat.id, "ℹ️ Usage: /redeem <token>", context)
        return
    token = context.args[0].strip()
    db: Database = context.application.bot_data["db"]
    grant = await db.redeem_token(token, update.effective_user.id)
    if not grant:
        await _send_emoji_text(update.effective_chat.id, "❌ Invalid or already-used token.", context)
        return
    until = await db.add_premium_seconds(update.effective_user.id, grant)
    await _send_emoji_text(
        update.effective_chat.id,
        "✅ Token Redeemed Successfully\n\n"
        f"⭐ Premium active until (unix): {until}",
        context,
    )


async def plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not update.effective_user or not update.effective_chat:
        return
    db: Database = context.application.bot_data["db"]
    uid = update.effective_user.id
    premium_until = await db.get_premium_until(uid)
    now = int(time.time())
    active = premium_until >= now
    if active:
        dt = datetime.datetime.utcfromtimestamp(premium_until).strftime("%Y-%m-%d %H:%M:%S UTC")
        status = f"✅ [b]Active[/b]\n⏳ Expires: [c]{dt}[/c]"
    else:
        status = "❌ [b]Not Active[/b]"

    await _send_emoji_text(
        update.effective_chat.id,
        "💎 [b]Premium Plans[/b]\n\n"
        "• [b]1 Day[/b]: ₹9\n"
        "• [b]7 Days[/b]: ₹29\n"
        "• [b]1 Month[/b]: ₹99\n\n"
        "🔓 [b]Normal User Benefit[/b]\n"
        "• Final link access ke liye ads dekhne honge\n\n"
        "⭐ [b]Premium User Benefit[/b]\n"
        "• Direct access milta hai (no ads)\n\n"
        "🛒 [b]Buy Premium[/b]: [c]/pay[/c]",
        context,
    )

    await _send_emoji_text(
        update.effective_chat.id,
        f"👤 [b]Your Premium Status[/b]\n\n{status}",
        context,
    )


def _pay_plan_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💎 1 Day - ₹9", callback_data="payplan:1d")],
            [InlineKeyboardButton("💎 7 Days - ₹29", callback_data="payplan:7d")],
            [InlineKeyboardButton("💎 1 Month - ₹99", callback_data="payplan:30d")],
        ]
    )


def _upi_uri(upi_id: str, amount_rs: int, payee_name: str, note: str) -> str:
    pa = quote(upi_id, safe="")
    pn = quote(payee_name, safe="")
    tn = quote(note, safe="")
    return f"upi://pay?pa={pa}&pn={pn}&am={amount_rs}&cu=INR&tn={tn}"


def _upi_qr_image_url(upi_uri: str) -> str:
    # Public QR endpoint; Telegram fetches this URL and shows image in chat.
    return f"https://api.qrserver.com/v1/create-qr-code/?size=700x700&data={quote(upi_uri, safe='')}"


async def _cleanup_payment_user_ui(req: dict[str, Any], context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    chat_id = req.get("user_chat_id")
    details_id = req.get("details_msg_id")
    qr_id = req.get("qr_msg_id")
    if chat_id:
        if details_id:
            try:
                await context.bot.delete_message(chat_id=int(chat_id), message_id=int(details_id))
            except Exception:
                pass
        if qr_id:
            try:
                await context.bot.delete_message(chat_id=int(chat_id), message_id=int(qr_id))
            except Exception:
                pass
    await db.clear_payment_ui_messages(int(req["id"]))


async def _payment_timeout_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data or {}
    rid = data.get("request_id")
    if not rid:
        return
    db: Database = context.application.bot_data["db"]
    req = await db.get_payment_request(int(rid))
    if not req:
        return
    # Expire only if no UTR submitted within timeout window.
    if req["status"] != "pending":
        return
    now = int(time.time())
    if int(req.get("expires_at") or 0) > now:
        return
    changed = await db.expire_payment_request_if_pending(int(rid))
    if not changed:
        return
    await _cleanup_payment_user_ui(req, context)
    await db.delete_payment_request(int(rid))
    try:
        await _send_emoji_text(
            int(req["user_id"]),
            "⏳ Payment request expired.\n\n"
            "No UTR received within 5 minutes.\n"
            "Please run /pay again.",
            context,
        )
    except Exception:
        pass


async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    await _send_emoji_text(
        update.effective_chat.id,
        "🛒 Choose Your Plan\n\nSelect one plan to continue payment:",
        context,
        reply_markup=_pay_plan_keyboard(),
    )


async def pay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not update.effective_user or not update.effective_chat:
        return
    await q.answer()
    data = q.data or ""
    db: Database = context.application.bot_data["db"]

    if data.startswith("payplan:"):
        existing = await db.get_latest_open_payment_request(update.effective_user.id)
        if existing:
            now_ts = int(time.time())
            if existing.get("status") == "submitted":
                await q.answer("Payment already submitted. Admin verification pending.", show_alert=True)
                return
            if existing.get("status") == "pending" and int(existing.get("expires_at") or 0) > now_ts:
                await q.answer("Active payment request already exists. Use Send UTR.", show_alert=True)
                return
            if existing.get("status") == "pending":
                changed = await db.expire_payment_request_if_pending(int(existing["id"]))
                if changed:
                    await db.delete_payment_request(int(existing["id"]))

        key = data.split(":", 1)[1]
        plan = PAY_PLANS.get(key)
        if not plan:
            await _edit_emoji_text(update.effective_chat.id, q.message.message_id, "❌ Invalid plan.", context)
            return
        rid = await db.create_payment_request(update.effective_user.id, key, int(plan["days"]), int(plan["amount"]))
        pay_text = await db.get_setting(SETTINGS_PAY_TEXT)
        if not pay_text:
            pay_text = (
                "Pay to buy premium.\n"
                "After payment, tap *Send UTR* and share UTR/screenshot.\n"
                "Admin will verify and activate plan manually."
            )
        upi_id = await db.get_setting(SETTINGS_PAY_UPI)
        pay_name = await db.get_setting(SETTINGS_PAY_NAME) or "Premium Store"
        if not upi_id:
            await _edit_emoji_text(
                update.effective_chat.id,
                q.message.message_id,
                "⚠️ Payment is not configured by admin yet.\nPlease contact admin.",
                context,
            )
            return

        note = f"premium {plan['label']} order#{rid}"
        upi_uri = _upi_uri(upi_id=upi_id, amount_rs=int(plan["amount"]), payee_name=pay_name, note=note)
        qr_url = _upi_qr_image_url(upi_uri)
        plan_label = html.escape(str(plan["label"]))
        upi_html = html.escape(upi_id)
        caption = (
            "💎 <b>Premium Purchase</b>\n\n"
            f"🛍 Plan: <b>{plan_label}</b>\n"
            f"💰 Amount: ₹{plan['amount']}\n\n"
            "━━━━━━━━━━━━━━\n\n"
            "📲 <b>Pay via UPI</b>\n"
            "Scan the QR above\n"
            "OR send to:\n\n"
            f"<code>{upi_html}</code>\n\n"
            f"🆔 Order ID: <code>#{rid}</code>\n\n"
            "━━━━━━━━━━━━━━\n\n"
            "📌 <b>How to Activate</b>\n"
            "1️⃣ Complete payment\n"
            "2️⃣ Tap \"Submit UTR\"\n"
            "3️⃣ Send transaction ID\n"
            "4️⃣ Premium activates after verification\n\n"
            "⏳ Request expires in 5 minutes."
        )
        # Keep optional admin-configured payment note, without clutter.
        pay_text_clean = (pay_text or "").strip()
        if pay_text_clean:
            caption = f"{caption}\n\n🧾 <b>Note</b>\n{html.escape(pay_text_clean)}"

        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📩 Submit UTR", callback_data=f"payutr:{rid}")]])

        # Remove plan picker message to keep payment UI clean.
        try:
            if q.message:
                await q.message.delete()
        except Exception:
            pass

        payment_msg_id: Optional[int] = None
        try:
            payment_msg = await update.effective_chat.send_photo(
                photo=qr_url,
                caption=caption,
                parse_mode="HTML",
                reply_markup=kb,
            )
            payment_msg_id = payment_msg.message_id
        except Exception:
            # Fallback if remote QR URL fails for any reason.
            await _send_emoji_text(
                update.effective_chat.id,
                "⚠️ QR load failed. Pay via UPI ID shown above.\n"
                f"UPI URI:\n{upi_uri}",
                context,
            )
        await db.set_payment_ui_messages(
            int(rid),
            int(update.effective_chat.id),
            int(payment_msg_id) if payment_msg_id is not None else int(q.message.message_id),
            None,
        )
        if context.application.job_queue:
            context.application.job_queue.run_once(
                _payment_timeout_job,
                when=300,
                data={"request_id": int(rid)},
                name=f"pay-timeout-{rid}",
            )
        return

    if data.startswith("payutr:"):
        rid_raw = data.split(":", 1)[1]
        try:
            rid = int(rid_raw)
        except ValueError:
            await q.answer("Invalid request id", show_alert=True)
            return
        context.user_data["pay_utr_request_id"] = rid
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            f"📩 Send your UTR now for Request ID {rid}.\n\n"
            "You can send text UTR or payment screenshot.\n"
            "Admin will verify and activate manually.",
            context,
        )
        return


async def _notify_payment_admins(update: Update, context: ContextTypes.DEFAULT_TYPE, req: dict[str, Any], utr_preview: str) -> None:
    db: Database = context.application.bot_data["db"]
    cfg = context.application.bot_data["cfg"]
    admin_ids = await db.list_admin_ids()
    targets = {int(cfg.owner_id), *[int(x) for x in admin_ids]}
    user = update.effective_user
    username = f"@{user.username}" if user and user.username else "-"
    note = (
        "💰 New Payment UTR Submitted\n\n"
        f"Request ID: {req['id']}\n"
        f"User ID: {req['user_id']}\n"
        f"Username: {username}\n"
        f"Plan: {req['plan_key']} ({req['plan_days']} days)\n"
        f"Amount: ₹{req['amount_rs']}\n"
        f"UTR: {utr_preview}\n\n"
        "Review and use buttons below."
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"payadm:approve:{req['id']}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"payadm:reject:{req['id']}"),
            ]
        ]
    )
    for aid in targets:
        try:
            sent = await _send_emoji_text(aid, note, context, reply_markup=kb)
            if sent and getattr(sent, "message_id", None):
                await _save_pay_admin_msg_ref(db, int(req["id"]), int(aid), int(sent.message_id))
            # If user sent media, forward copy for proof.
            if update.effective_message and (update.effective_message.photo or update.effective_message.document):
                await context.bot.copy_message(
                    chat_id=aid,
                    from_chat_id=update.effective_chat.id,
                    message_id=update.effective_message.message_id,
                )
        except Exception:
            pass


async def pay_utr_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rid = context.user_data.get("pay_utr_request_id")
    if not rid:
        return
    if not update.effective_user or not update.effective_chat or not update.effective_message:
        return
    db: Database = context.application.bot_data["db"]
    req = await db.get_payment_request(int(rid))
    if not req:
        context.user_data.pop("pay_utr_request_id", None)
        await _send_emoji_text(update.effective_chat.id, "❌ Payment request expired/invalid. Please run /pay again.", context)
        return
    now = int(time.time())
    if req["status"] == "pending" and int(req.get("expires_at") or 0) > 0 and int(req.get("expires_at") or 0) <= now:
        await db.expire_payment_request_if_pending(int(rid))
        await _cleanup_payment_user_ui(req, context)
        await db.delete_payment_request(int(rid))
        context.user_data.pop("pay_utr_request_id", None)
        await _send_emoji_text(update.effective_chat.id, "⏳ Payment request expired. Please run /pay again.", context)
        return

    utr_text = (update.effective_message.text or "").strip()
    if not utr_text:
        if update.effective_message.photo:
            utr_text = f"screenshot:{update.effective_message.photo[-1].file_id[:12]}"
        elif update.effective_message.document:
            utr_text = f"document:{update.effective_message.document.file_id[:12]}"
        else:
            await _send_emoji_text(update.effective_chat.id, "⚠️ Please send UTR text or payment screenshot.", context)
            return

    ok = await db.set_payment_utr(int(rid), utr_text)
    context.user_data.pop("pay_utr_request_id", None)
    if not ok:
        await _send_emoji_text(update.effective_chat.id, "❌ Payment request expired/invalid. Please run /pay again.", context)
        return
    req = await db.get_payment_request(int(rid))
    if req:
        await _notify_payment_admins(update, context, req, utr_text)
    await _send_emoji_text(
        update.effective_chat.id,
        "✅ UTR submitted successfully.\n"
        "Admin ko notify kar diya gaya hai.\n"
        "Plan verification ke baad manually activate kiya jayega.",
        context,
    )


async def pay_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not update.effective_chat or not update.effective_user:
        return
    await q.answer()
    if not await _is_admin_or_owner(update, context):
        await q.answer("Access denied", show_alert=True)
        return
    data = q.data or ""
    if not data.startswith("payadm:"):
        return
    parts = data.split(":")
    if len(parts) != 3:
        await q.answer("Invalid action", show_alert=True)
        return
    action, rid_raw = parts[1], parts[2]
    if action not in ("approve", "reject"):
        await q.answer("Invalid action", show_alert=True)
        return
    try:
        rid = int(rid_raw)
    except ValueError:
        await q.answer("Invalid request id", show_alert=True)
        return

    db: Database = context.application.bot_data["db"]
    req = await db.get_payment_request(rid)
    if not req:
        await q.answer("Request not found", show_alert=True)
        return

    # Already processed by someone else.
    if req["status"] in ("processed", "rejected"):
        who = req.get("processed_by") or "-"
        status_text = (
            "ℹ️ Payment request already handled.\n\n"
            f"Request ID: {req['id']}\n"
            f"Status: {req['status']}\n"
            f"Processed by: {who}"
        )
        await _sync_pay_admin_status(context, int(req["id"]), status_text)
        return

    if action == "approve":
        ok = await db.approve_payment_request(rid, update.effective_user.id)
        if not ok:
            await q.answer("Already handled", show_alert=True)
            return
        until = await db.add_premium_seconds(int(req["user_id"]), int(req["plan_days"]) * DAY_SECONDS)
        expiry_utc = datetime.datetime.utcfromtimestamp(until).strftime("%Y-%m-%d %H:%M:%S UTC")
        reviewed_at = datetime.datetime.utcfromtimestamp(int(time.time())).strftime("%Y-%m-%d %H:%M:%S UTC")
        admin_name = f"@{update.effective_user.username}" if update.effective_user.username else str(update.effective_user.id)
        # Notify user
        try:
            await _send_emoji_text(
                int(req["user_id"]),
                "✅ Payment Verified\n\n"
                f"💎 Plan activated: {req['plan_key']} ({req['plan_days']} days)\n"
                f"🕒 Expires: {expiry_utc}",
                context,
            )
        except Exception:
            pass
        approved_text = (
            "✅ Payment Approved\n\n"
            f"Request ID: {req['id']}\n"
            f"User ID: {req['user_id']}\n"
            f"Plan: {req['plan_key']} ({req['plan_days']} days)\n"
            f"Amount: ₹{req['amount_rs']}\n"
            f"By Admin: {admin_name}\n"
            f"Reviewed At: {reviewed_at}\n"
            f"Premium Until: {expiry_utc}"
        )
        await _sync_pay_admin_status(context, int(req["id"]), approved_text)
        await _cleanup_payment_user_ui(req, context)
        return

    ok = await db.reject_payment_request(rid, update.effective_user.id)
    if not ok:
        await q.answer("Already handled", show_alert=True)
        return
    reviewed_at = datetime.datetime.utcfromtimestamp(int(time.time())).strftime("%Y-%m-%d %H:%M:%S UTC")
    admin_name = f"@{update.effective_user.username}" if update.effective_user.username else str(update.effective_user.id)
    # Notify user
    try:
        await _send_emoji_text(
            int(req["user_id"]),
            "❌ Payment Rejected\n\n"
            "Your submitted payment could not be verified.\n"
            "Please contact admin with proper payment proof.",
            context,
        )
    except Exception:
        pass
    rejected_text = (
        "❌ Payment Rejected\n\n"
        f"Request ID: {req['id']}\n"
        f"User ID: {req['user_id']}\n"
        f"Plan: {req['plan_key']} ({req['plan_days']} days)\n"
        f"Amount: ₹{req['amount_rs']}\n"
        f"By Admin: {admin_name}\n"
        f"Reviewed At: {reviewed_at}"
    )
    await _sync_pay_admin_status(context, int(req["id"]), rejected_text)
    await _cleanup_payment_user_ui(req, context)


async def setpay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await _send_emoji_text(update.effective_chat.id, "🚫 Access denied. (Admin/Owner only)", context)
        return
    db: Database = context.application.bot_data["db"]
    if not context.args:
        await _send_emoji_text(
            update.effective_chat.id,
            "ℹ️ [b]Payment Settings[/b]\n"
            "[i]Admin configuration guide[/i]\n\n"
            "[u]Commands[/u]\n"
            "• [c]/setpay view[/c]\n"
            "• [c]/setpay upi <upi_id>[/c]\n"
            "• [c]/setpay name <payee_name>[/c]\n"
            "• [c]/setpay text <payment_instructions>[/c]\n"
            "• [c]/setpay clearupi[/c]",
            context,
        )
        return
    sub = context.args[0].lower()
    if sub == "view":
        upi = await db.get_setting(SETTINGS_PAY_UPI) or "-"
        name = await db.get_setting(SETTINGS_PAY_NAME) or "Premium Store"
        text = await db.get_setting(SETTINGS_PAY_TEXT) or "-"
        await _send_emoji_text(update.effective_chat.id, f"UPI ID: {upi}\nPayee Name: {name}\n\nText:\n{text}", context)
        return
    if sub == "clearupi":
        await db.set_setting(SETTINGS_PAY_UPI, None)
        await _send_emoji_text(update.effective_chat.id, "✅ Payment UPI cleared.", context)
        return
    if sub == "upi":
        if len(context.args) < 2:
            await _send_emoji_text(update.effective_chat.id, "❌ Usage: /setpay upi <upi_id>", context)
            return
        v = context.args[1].strip()
        await db.set_setting(SETTINGS_PAY_UPI, v)
        await _send_emoji_text(update.effective_chat.id, "✅ Payment UPI set.", context)
        return
    if sub == "name":
        if len(context.args) < 2:
            await _send_emoji_text(update.effective_chat.id, "❌ Usage: /setpay name <payee_name>", context)
            return
        v = " ".join(context.args[1:]).strip()
        await db.set_setting(SETTINGS_PAY_NAME, v)
        await _send_emoji_text(update.effective_chat.id, "✅ Payment payee name set.", context)
        return
    if sub == "text":
        text = " ".join(context.args[1:]).strip()
        if not text:
            await _send_emoji_text(update.effective_chat.id, "❌ Usage: /setpay text <payment instructions>", context)
            return
        await db.set_setting(SETTINGS_PAY_TEXT, text)
        await _send_emoji_text(update.effective_chat.id, "✅ Payment text set.", context)
        return
    await _send_emoji_text(update.effective_chat.id, "❌ Invalid /setpay option.", context)


async def bsettings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await _send_emoji_text(update.effective_chat.id, "🚫 Access denied. (Admin/Owner only)", context)
        return
    _clear_bsettings_wait_states(context)
    await _send_emoji_text(
        update.effective_chat.id,
        "⚙️ [b]Admin Settings Panel[/b]\n\nTap any option below to open guided actions.",
        context,
        reply_markup=_bsettings_keyboard(),
    )


async def bsettings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()
    if not await _is_admin_or_owner(update, context):
        await _edit_emoji_text(update.effective_chat.id, q.message.message_id, "🚫 Access denied. (Admin/Owner only)", context)
        return
    data = q.data or ""
    if data == "bset:addpremium_action":
        context.user_data["bset_addpremium_wait"] = True
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            "⭐ [b]Add Premium[/b]\n\nFormat bhejo: [c]<user_id> [days][/c]\nExample: [c]123456789 7[/c]",
            context,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="bset:back")]]),
        )
        return
    if data == "bset:removepremium_action":
        context.user_data["bset_removepremium_wait"] = True
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            "❌ [b]Remove Premium[/b]\n\nUser ID bhejo:\n[c]123456789[/c]",
            context,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="bset:back")]]),
        )
        return
    if data == "bset:setcaption_action":
        context.user_data["bset_setcaption_wait"] = True
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            "📝 [b]Set Caption[/b]\n\nCaption text bhejo.\nHTML tags supported: [c]<b> <i> <code>[/c]",
            context,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🗑 Remove Caption", callback_data="bset:removecaption_action")],
                    [InlineKeyboardButton("⬅️ Back", callback_data="bset:back")],
                ]
            ),
        )
        return
    if data == "bset:removecaption_action":
        db: Database = context.application.bot_data["db"]
        await db.set_setting("caption", None)
        context.user_data.pop("bset_setcaption_wait", None)
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            "🗑 [b]Default caption removed.[/b]",
            context,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="bset:back")]]),
        )
        return
    if data == "bset:settime_action":
        context.user_data["bset_settime_wait"] = True
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            "⏱ [b]Set Auto-Delete Time[/b]\n\nValue bhejo:\n[c]60[/c], [c]5m[/c], [c]1h[/c], [c]off[/c]",
            context,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="bset:back")]]),
        )
        return
    if data == "bset:setstartimg_action":
        context.user_data["bset_setstartimg_wait"] = True
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            "🖼 [b]Set Start Image[/b]\n\nImage URL bhejo ([c]http/https[/c]) ya [c]off[/c].",
            context,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="bset:back")]]),
        )
        return
    if data == "bset:setpay_panel":
        context.user_data.pop("bset_setpay_wait", None)
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            "💳 [b]Payment Settings[/b]\n\nChoose action below:",
            context,
            reply_markup=_bset_setpay_keyboard(),
        )
        return
    if data == "bset:premiumdb_action":
        await premiumdb(update, context)
        return
    if data == "bset:setpay_view":
        db: Database = context.application.bot_data["db"]
        upi = await db.get_setting(SETTINGS_PAY_UPI) or "-"
        name = await db.get_setting(SETTINGS_PAY_NAME) or "Premium Store"
        text = await db.get_setting(SETTINGS_PAY_TEXT) or "-"
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            "💳 [b]Payment Settings[/b]\n\n"
            f"UPI ID: [c]{upi}[/c]\n"
            f"Payee Name: {name}\n\n"
            f"Text:\n{text}",
            context,
            reply_markup=_bset_setpay_keyboard(),
        )
        return
    if data == "bset:setpay_clearupi":
        db: Database = context.application.bot_data["db"]
        await db.set_setting(SETTINGS_PAY_UPI, None)
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            "✅ Payment UPI cleared.",
            context,
            reply_markup=_bset_setpay_keyboard(),
        )
        return
    if data in ("bset:setpay_upi", "bset:setpay_name", "bset:setpay_text"):
        mode = data.split(":")[-1].replace("setpay_", "")
        context.user_data["bset_setpay_wait"] = mode
        prompts = {
            "upi": "🆔 UPI ID bhejo (example: [c]name@bank[/c])",
            "name": "👤 Payee name bhejo",
            "text": "🧾 Payment instructions text bhejo",
        }
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            f"💳 [b]SetPay[/b]\n\n{prompts.get(mode, 'Value bhejo')}",
            context,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="bset:setpay_panel")]]),
        )
        return
    if data == "bset:forcech_panel":
        context.user_data.pop("bset_forcech_remove_wait", None)
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            _bset_forcech_panel_text(),
            context,
            reply_markup=_bset_forcech_panel_keyboard(),
        )
        return
    if data == "bset:forcech_add":
        context.user_data.pop("bset_forcech_remove_wait", None)
        context.user_data["forcech_state"] = {"step": "await_channel"}
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            "➕ [b]Force Channel Add[/b]\n\nChannel ID/username bhejo:\n[c]-100xxxx[/c] or [c]@channel[/c]",
            context,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="bset:forcech_panel")]]),
        )
        return
    if data == "bset:forcech_list":
        db: Database = context.application.bot_data["db"]
        chans = await db.list_force_channels()
        if not chans:
            text = "📣 [b]Force Channels[/b]\n\nNo required channels set."
        else:
            lines = []
            for ch in chans:
                extra = ch.get("invite_link") or (f"@{ch['username']}" if ch.get("username") else "")
                name = ch.get("title") or ""
                mode = (ch.get("mode") or "direct").lower()
                mode_label = "🔓 Direct" if mode == "direct" else "🛂 Request"
                lines.append(f"• {ch['channel_id']} [{mode_label}] {name} {extra}".strip())
            text = "📣 [b]Force Channels[/b]\n\n" + "\n".join(lines)
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            text,
            context,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="bset:forcech_panel")]]),
        )
        return
    if data == "bset:forcech_remove":
        context.user_data.pop("forcech_state", None)
        context.user_data["bset_forcech_remove_wait"] = True
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            "➖ [b]Force Channel Remove[/b]\n\nChannel ID/username bhejo jise remove karna hai:\n[c]-100xxxx[/c] or [c]@channel[/c]",
            context,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="bset:forcech_panel")]]),
        )
        return
    if data == "bset:forcech_reset":
        db: Database = context.application.bot_data["db"]
        await db.clear_force_channels()
        context.user_data.pop("forcech_state", None)
        context.user_data.pop("bset_forcech_remove_wait", None)
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            "✅ [b]Force channel reset done.[/b]\nAll required channels cleared.",
            context,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="bset:forcech_panel")]]),
        )
        return
    if data == "bset:addadmin_action":
        cfg = context.application.bot_data["cfg"]
        if not _is_owner(update, cfg):
            await q.answer("Owner only", show_alert=True)
            return
        context.user_data["bset_addadmin_wait"] = True
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="bset:back")]])
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            "👮 [b]Add Admin[/b]\n\n"
            "Owner ID send kare: jis user ko admin banana hai uska numeric user_id bhejo.\n\n"
            "Example: [c]123456789[/c]",
            context,
            reply_markup=kb,
        )
        return
    if data == "bset:removeadmin_action":
        cfg = context.application.bot_data["cfg"]
        if not _is_owner(update, cfg):
            await q.answer("Owner only", show_alert=True)
            return
        context.user_data["bset_removeadmin_wait"] = True
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="bset:back")]])
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            "🚫 [b]Remove Admin[/b]\n\n"
            "Owner ID send kare: jis admin ko remove karna hai uska numeric user_id bhejo.\n\n"
            "Example: [c]123456789[/c]",
            context,
            reply_markup=kb,
        )
        return
    if data == "bset:gencode_action":
        if not await _is_admin_or_owner(update, context):
            await q.answer("Access denied", show_alert=True)
            return
        context.user_data["bset_gencode_wait"] = True
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="bset:back")]])
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            "🎟 [b]Generate Codes[/b]\n\n"
            "Kitne codes generate karne hain? (1 to 20)\n\n"
            "Example: [c]5[/c]",
            context,
            reply_markup=kb,
        )
        return
    if data == "bset:back":
        _clear_bsettings_wait_states(context)
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            "⚙️ [b]Admin Settings Panel[/b]\n\nTap any option below to open guided actions.",
            context,
            reply_markup=_bsettings_keyboard(),
        )
        return
    if not data.startswith("bset:"):
        return
    key = data.split(":", 1)[1]
    doc = BSETTINGS_DOCS.get(key)
    if not doc:
        await _edit_emoji_text(update.effective_chat.id, q.message.message_id, "❌ Unknown command doc.", context)
        return
    if key == "addadmin":
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("➕ Add Admin", callback_data="bset:addadmin_action")],
                [InlineKeyboardButton("⬅️ Back", callback_data="bset:back")],
            ]
        )
    elif key == "removeadmin":
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("➖ Remove Admin", callback_data="bset:removeadmin_action")],
                [InlineKeyboardButton("⬅️ Back", callback_data="bset:back")],
            ]
        )
    elif key == "gencode":
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🎟 Generate Codes", callback_data="bset:gencode_action")],
                [InlineKeyboardButton("⬅️ Back", callback_data="bset:back")],
            ]
        )
    elif key == "forcech":
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            _bset_forcech_panel_text(),
            context,
            reply_markup=_bset_forcech_panel_keyboard(),
        )
        return
    elif key == "addpremium":
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("⭐ Add Premium", callback_data="bset:addpremium_action")],
                [InlineKeyboardButton("⬅️ Back", callback_data="bset:back")],
            ]
        )
    elif key == "removepremium":
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("❌ Remove Premium", callback_data="bset:removepremium_action")],
                [InlineKeyboardButton("⬅️ Back", callback_data="bset:back")],
            ]
        )
    elif key == "setcaption":
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📝 Set Caption", callback_data="bset:setcaption_action")],
                [InlineKeyboardButton("🗑 Remove Caption", callback_data="bset:removecaption_action")],
                [InlineKeyboardButton("⬅️ Back", callback_data="bset:back")],
            ]
        )
    elif key == "settime":
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("⏱ Set Time", callback_data="bset:settime_action")],
                [InlineKeyboardButton("⬅️ Back", callback_data="bset:back")],
            ]
        )
    elif key == "setstartimg":
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🖼 Set Start Image", callback_data="bset:setstartimg_action")],
                [InlineKeyboardButton("⬅️ Back", callback_data="bset:back")],
            ]
        )
    elif key == "setpay":
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            "💳 [b]Payment Settings[/b]\n\nChoose action below:",
            context,
            reply_markup=_bset_setpay_keyboard(),
        )
        return
    elif key == "premiumdb":
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📥 Export Premium DB", callback_data="bset:premiumdb_action")],
                [InlineKeyboardButton("⬅️ Back", callback_data="bset:back")],
            ]
        )
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="bset:back")]])
    await _edit_emoji_text(
        update.effective_chat.id,
        q.message.message_id,
        f"{doc['title']}\n\n{doc['body']}",
        context,
        reply_markup=kb,
    )


async def bsettings_owner_addadmin_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_user or not update.effective_message:
        return
    if not context.user_data.get("bset_addadmin_wait"):
        return
    cfg = context.application.bot_data["cfg"]
    if not _is_owner(update, cfg):
        context.user_data.pop("bset_addadmin_wait", None)
        return

    raw = (update.effective_message.text or "").strip()
    try:
        uid = int(raw)
    except ValueError:
        await _send_emoji_text(update.effective_chat.id, "❌ Invalid user_id. Numeric ID bhejo.", context)
        return

    db: Database = context.application.bot_data["db"]
    await db.add_admin(uid, update.effective_user.id)
    context.user_data.pop("bset_addadmin_wait", None)
    await _send_emoji_text(update.effective_chat.id, f"✅ Admin added: {uid}", context)


async def bsettings_owner_removeadmin_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_user or not update.effective_message:
        return
    if not context.user_data.get("bset_removeadmin_wait"):
        return
    cfg = context.application.bot_data["cfg"]
    if not _is_owner(update, cfg):
        context.user_data.pop("bset_removeadmin_wait", None)
        return

    raw = (update.effective_message.text or "").strip()
    try:
        uid = int(raw)
    except ValueError:
        await _send_emoji_text(update.effective_chat.id, "❌ Invalid user_id. Numeric ID bhejo.", context)
        return

    db: Database = context.application.bot_data["db"]
    await db.remove_admin(uid)
    context.user_data.pop("bset_removeadmin_wait", None)
    await _send_emoji_text(update.effective_chat.id, f"✅ Admin removed: {uid}", context)


async def bsettings_gencode_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_user or not update.effective_message:
        return
    if not context.user_data.get("bset_gencode_wait"):
        return
    if not await _is_admin_or_owner(update, context):
        context.user_data.pop("bset_gencode_wait", None)
        return

    raw = (update.effective_message.text or "").strip()
    try:
        count = int(raw)
    except ValueError:
        await _send_emoji_text(update.effective_chat.id, "❌ Invalid number. 1 to 20 bhejo.", context)
        return
    if count < 1:
        count = 1
    if count > 20:
        count = 20

    db: Database = context.application.bot_data["db"]
    context.user_data.pop("bset_gencode_wait", None)
    await _generate_tokens_and_send(update.effective_chat.id, update.effective_user.id, count, db, context)


async def bsettings_forcech_remove_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_user or not update.effective_message:
        return
    if not context.user_data.get("bset_forcech_remove_wait"):
        return
    if not await _is_admin_or_owner(update, context):
        context.user_data.pop("bset_forcech_remove_wait", None)
        return

    db: Database = context.application.bot_data["db"]
    ref = _parse_channel_ref((update.effective_message.text or "").strip())
    if ref is None:
        await _send_emoji_text(update.effective_chat.id, "❌ Invalid format. Send -100xxxx or @channelusername.", context)
        return
    try:
        chat = await context.bot.get_chat(ref)
        cid = int(chat.id)
    except Exception:
        if isinstance(ref, int):
            cid = int(ref)
        else:
            await _send_emoji_text(update.effective_chat.id, "❌ Channel not found.", context)
            return
    await db.remove_force_channel(cid)
    context.user_data.pop("bset_forcech_remove_wait", None)
    await _send_emoji_text(update.effective_chat.id, f"✅ Force channel removed: {cid}", context)


async def bsettings_misc_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_user or not update.effective_message:
        return
    if not await _is_admin_or_owner(update, context):
        _clear_bsettings_wait_states(context)
        return
    text = (update.effective_message.text or "").strip()
    if not text:
        return

    db: Database = context.application.bot_data["db"]

    if context.user_data.get("bset_addpremium_wait"):
        parts = text.split()
        try:
            uid = int(parts[0])
            days = int(parts[1]) if len(parts) > 1 else 1
        except Exception:
            await _send_emoji_text(update.effective_chat.id, "❌ Invalid format. Use: <user_id> [days]", context)
            return
        days = max(1, days)
        until = await db.add_premium_seconds(uid, days * DAY_SECONDS)
        context.user_data.pop("bset_addpremium_wait", None)
        await _send_emoji_text(update.effective_chat.id, f"✅ Premium granted to {uid} for {days} day(s).\n🕒 Until: {until}", context)
        return

    if context.user_data.get("bset_removepremium_wait"):
        try:
            uid = int(text)
        except Exception:
            await _send_emoji_text(update.effective_chat.id, "❌ Invalid user_id.", context)
            return
        await db.set_premium_until(uid, 0)
        context.user_data.pop("bset_removepremium_wait", None)
        await _send_emoji_text(update.effective_chat.id, f"✅ Premium removed for {uid}.", context)
        return

    if context.user_data.get("bset_setcaption_wait"):
        await db.set_setting("caption", text)
        context.user_data.pop("bset_setcaption_wait", None)
        await _send_emoji_text(update.effective_chat.id, "✅ Default caption set.", context)
        return

    if context.user_data.get("bset_settime_wait"):
        seconds = _parse_duration_seconds(text)
        if seconds is None:
            await _send_emoji_text(update.effective_chat.id, "❌ Invalid time. Examples: 60, 5m, 1h, off", context)
            return
        await db.set_setting(SETTINGS_AUTODELETE_SECONDS, str(int(seconds)))
        context.user_data.pop("bset_settime_wait", None)
        if seconds <= 0:
            await _send_emoji_text(update.effective_chat.id, "✅ Auto-delete disabled.", context)
        else:
            await _send_emoji_text(update.effective_chat.id, f"✅ Auto-delete enabled: {seconds} seconds.", context)
        return

    if context.user_data.get("bset_setstartimg_wait"):
        raw = text
        if raw.lower() in ("off", "remove", "none", "disable", "disabled"):
            await db.set_setting(SETTINGS_START_IMG_URL, None)
            context.user_data.pop("bset_setstartimg_wait", None)
            await _send_emoji_text(update.effective_chat.id, "✅ Start image removed.", context)
            return
        if not (raw.startswith("https://") or raw.startswith("http://")):
            await _send_emoji_text(update.effective_chat.id, "❌ Invalid URL. Must start with http:// or https://", context)
            return
        await db.set_setting(SETTINGS_START_IMG_URL, raw)
        context.user_data.pop("bset_setstartimg_wait", None)
        await _send_emoji_text(update.effective_chat.id, "✅ Start image set.", context)
        return

    mode = context.user_data.get("bset_setpay_wait")
    if mode:
        if mode == "upi":
            await db.set_setting(SETTINGS_PAY_UPI, text)
            await _send_emoji_text(update.effective_chat.id, "✅ Payment UPI set.", context)
        elif mode == "name":
            await db.set_setting(SETTINGS_PAY_NAME, text)
            await _send_emoji_text(update.effective_chat.id, "✅ Payment payee name set.", context)
        elif mode == "text":
            await db.set_setting(SETTINGS_PAY_TEXT, text)
            await _send_emoji_text(update.effective_chat.id, "✅ Payment text set.", context)
        context.user_data.pop("bset_setpay_wait", None)
        return


async def forcech(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await _send_emoji_text(update.effective_chat.id, "🚫 Access denied. (Admin/Owner only)", context)
        return
    db: Database = context.application.bot_data["db"]

    if context.args and context.args[0].lower() == "list":
        chans = await db.list_force_channels()
        if not chans:
            await _send_emoji_text(
                update.effective_chat.id,
                "📣 Force Channels\n\n"
                "No required channels set.\n\n"
                "✅ Usage\n"
                "• /forcech (add flow)\n"
                "• /forcech list\n"
                "• /forcech remove <channel_id|@username>\n"
                "• /forcech reset",
                context,
            )
            return
        lines = []
        for ch in chans:
            extra = ch.get("invite_link") or (f"@{ch['username']}" if ch.get("username") else "")
            name = ch.get("title") or ""
            mode = (ch.get("mode") or "direct").lower()
            mode_label = "🔓 Direct" if mode == "direct" else "🛂 Request"
            lines.append(f"• {ch['channel_id']} [{mode_label}] {name} {extra}".strip())
        await _send_emoji_text(update.effective_chat.id, "📣 Force Channels\n\n" + "\n".join(lines), context)
        return

    if context.args and context.args[0].lower() == "remove":
        if len(context.args) < 2:
            await _send_emoji_text(update.effective_chat.id, "ℹ️ Usage: /forcech remove <channel_id|@username>", context)
            return
        ref = _parse_channel_ref(context.args[1])
        if ref is None:
            await _send_emoji_text(update.effective_chat.id, "❌ Invalid channel id/username.", context)
            return
        try:
            chat = await context.bot.get_chat(ref)
            cid = int(chat.id)
        except Exception:
            # fallback when chat cannot be resolved (assume raw id was passed)
            if isinstance(ref, int):
                cid = int(ref)
            else:
                await _send_emoji_text(update.effective_chat.id, "❌ Channel not found.", context)
                return
        await db.remove_force_channel(cid)
        await _send_emoji_text(update.effective_chat.id, f"✅ Force channel removed: {cid}", context)
        return

    if context.args and context.args[0].lower() == "reset":
        await db.clear_force_channels()
        context.user_data.pop("forcech_state", None)
        await _send_emoji_text(update.effective_chat.id, "✅ Force channel DB reset done. All required channels cleared.", context)
        return

    # Default: interactive add flow.
    context.user_data["forcech_state"] = {"step": "await_channel"}
    await _send_emoji_text(update.effective_chat.id, "📣 Channel ID/username bhejo", context)


async def forcechdebug(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await _send_emoji_text(update.effective_chat.id, "🚫 Access denied. (Admin/Owner only)", context)
        return
    if not context.args:
        await _send_emoji_text(update.effective_chat.id, "ℹ️ Usage: /forcechdebug <user_id>", context)
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await _send_emoji_text(update.effective_chat.id, "❌ Invalid user_id.", context)
        return
    ok, _, details = await _joined_all_force_channels_details(uid, context)
    if not details:
        await _send_emoji_text(update.effective_chat.id, "ℹ️ No force channels configured.", context)
        return
    lines = [f"Result: {'PASS' if ok else 'BLOCK'} for user {uid}", ""]
    for d in details:
        lines.append(
            f"• {d['channel_id']} mode={d['mode']} joined={d['joined']} request={d['request']} pass={d['passed']} "
            f"member_err={d['member_error'] or '-'} req_api_err={d['request_api_error'] or '-'}"
        )
    await _send_emoji_text(update.effective_chat.id, "\n".join(lines), context)


async def forcech_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_user or not update.effective_message:
        return
    state = context.user_data.get("forcech_state")
    if not state:
        return
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        return
    if state.get("step") != "await_channel":
        return

    db: Database = context.application.bot_data["db"]
    ref = _parse_channel_ref((update.effective_message.text or "").strip())
    if ref is None:
        await _send_emoji_text(update.effective_chat.id, "❌ Invalid format. Send -100xxxx or @channelusername.", context)
        return

    try:
        chat = await context.bot.get_chat(ref)
    except Exception:
        await _send_emoji_text(update.effective_chat.id, "❌ Channel not found / inaccessible.", context)
        return
    if chat.type != "channel":
        await _send_emoji_text(update.effective_chat.id, "❌ Only channel is supported for force-join.", context)
        return
    if not await _bot_is_admin(int(chat.id), context):
        context.user_data.pop("forcech_state", None)
        await _send_emoji_text(update.effective_chat.id, "❌ Bot is not admin in this channel. Add bot as admin and try again.", context)
        return

    state["channel_id"] = int(chat.id)
    state["title"] = chat.title
    state["username"] = chat.username
    state["step"] = "await_mode"
    context.user_data["forcech_state"] = state

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔓 Direct Mode", callback_data="forcech_mode:direct"),
                InlineKeyboardButton("🛂 Request Mode", callback_data="forcech_mode:request"),
            ]
        ]
    )
    await _send_emoji_text(update.effective_chat.id, "Mode select karo:", context, reply_markup=kb)


async def forcech_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not update.effective_chat or not update.effective_user:
        return
    await q.answer()
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await _edit_emoji_text(update.effective_chat.id, q.message.message_id, "🚫 Access denied. (Admin/Owner only)", context)
        return

    data = q.data or ""
    if not data.startswith("forcech_mode:"):
        return
    mode = data.split(":", 1)[1]
    if mode not in ("direct", "request"):
        await _edit_emoji_text(update.effective_chat.id, q.message.message_id, "❌ Invalid mode.", context)
        return

    state = context.user_data.get("forcech_state") or {}
    if state.get("step") != "await_mode":
        await _edit_emoji_text(update.effective_chat.id, q.message.message_id, "ℹ️ No active forcech add flow. Run /forcech again.", context)
        return

    cid = int(state.get("channel_id", 0))
    if not cid:
        context.user_data.pop("forcech_state", None)
        await _edit_emoji_text(update.effective_chat.id, q.message.message_id, "❌ Missing channel state. Run /forcech again.", context)
        return

    # Re-validate admin before saving.
    if not await _bot_is_admin(cid, context):
        context.user_data.pop("forcech_state", None)
        await _edit_emoji_text(update.effective_chat.id, q.message.message_id, "❌ Bot is not admin in this channel. Add bot as admin and try again.", context)
        return

    username = state.get("username")
    title = state.get("title")
    invite_link: Optional[str] = None
    if mode == "request":
        # Request mode should always use a join-request invite link,
        # even for public channels.
        try:
            inv = await context.bot.create_chat_invite_link(
                chat_id=cid,
                creates_join_request=True,
                name="forcech_request",
            )
            invite_link = inv.invite_link
        except Exception:
            await _edit_emoji_text(
                update.effective_chat.id,
                q.message.message_id,
                "❌ Request mode invite link generate nahi hua.\n"
                "Channel settings me Join Requests enable karo, phir /forcech fir se run karo.",
                context,
            )
            context.user_data.pop("forcech_state", None)
            return
    else:
        if username:
            invite_link = f"https://t.me/{username}"
        else:
            try:
                inv = await context.bot.create_chat_invite_link(
                    chat_id=cid,
                    creates_join_request=False,
                    name="forcech_direct",
                )
                invite_link = inv.invite_link
            except Exception:
                await _edit_emoji_text(
                    update.effective_chat.id,
                    q.message.message_id,
                    "❌ Invite link generate nahi hua. Check bot admin permissions.",
                    context,
                )
                context.user_data.pop("forcech_state", None)
                return

    db: Database = context.application.bot_data["db"]
    await db.add_force_channel(cid, mode, invite_link, title, username, update.effective_user.id)
    context.user_data.pop("forcech_state", None)
    mode_label = "🔓 Direct Mode" if mode == "direct" else "🛂 Request Mode"
    await _edit_emoji_text(
        update.effective_chat.id,
        q.message.message_id,
        "✅ Force channel saved\n\n"
        f"Channel: {cid}\n"
        f"Mode: {mode_label}\n"
        f"Join Link: {invite_link}",
        context,
        disable_web_page_preview=True,
    )


async def on_chat_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    req = update.chat_join_request
    if not req:
        return
    db: Database = context.application.bot_data["db"]
    await db.add_force_join_request(req.chat.id, req.from_user.id)


async def setcaption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await _send_emoji_text(update.effective_chat.id, "🚫 Access denied. (Admin/Owner only)", context)
        return
    text = " ".join(context.args).strip() if context.args else ""
    if not text and update.effective_message and update.effective_message.reply_to_message:
        text = (update.effective_message.reply_to_message.text or "").strip()
    if not text:
        await _send_emoji_text(
            update.effective_chat.id,
            "ℹ️ Usage: /setcaption <text> (or reply to a text message)\n"
            "HTML styles supported, e.g. `<b>bold</b> <i>italic</i> <code>code</code>`",
            context,
        )
        return
    db: Database = context.application.bot_data["db"]
    await db.set_setting("caption", text)
    await _send_emoji_text(update.effective_chat.id, "✅ Default caption set.", context)


async def removecaption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await _send_emoji_text(update.effective_chat.id, "🚫 Access denied. (Admin/Owner only)", context)
        return
    db: Database = context.application.bot_data["db"]
    await db.set_setting("caption", None)
    await _send_emoji_text(update.effective_chat.id, "🗑️ Default caption removed.", context)


async def settime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await _send_emoji_text(update.effective_chat.id, "🚫 Access denied. (Admin/Owner only)", context)
        return
    if not context.args:
        await _send_emoji_text(
            update.effective_chat.id,
            "⏱️ Auto-Delete Time\n\n"
            "Set time after which files/messages delivered via links will be auto-deleted.\n\n"
            "✅ Usage:\n"
            "• /settime 60 (seconds)\n"
            "• /settime 5m\n"
            "• /settime 1h\n"
            "• /settime off\n",
            context,
        )
        return
    seconds = _parse_duration_seconds(context.args[0])
    if seconds is None:
        await _send_emoji_text(update.effective_chat.id, "❌ Invalid time. Examples: 60, 5m, 1h, off", context)
        return
    db: Database = context.application.bot_data["db"]
    await db.set_setting(SETTINGS_AUTODELETE_SECONDS, str(int(seconds)))
    if seconds <= 0:
        await _send_emoji_text(update.effective_chat.id, "✅ Auto-delete disabled.", context)
        return
    await _send_emoji_text(update.effective_chat.id, f"✅ Auto-delete enabled: messages will be deleted after {seconds} seconds.", context)


async def setstartimg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await _send_emoji_text(update.effective_chat.id, "🚫 Access denied. (Admin/Owner only)", context)
        return
    if not context.args:
        await _send_emoji_text(
            update.effective_chat.id,
            "🖼️ Start Image\n\n"
            "Set an image URL that will be shown with `/start` message.\n\n"
            "✅ Usage:\n"
            "• /setstartimg <image_url>\n"
            "• /setstartimg off",
            context,
        )
        return
    raw = context.args[0].strip()
    if raw.lower() in ("off", "remove", "none", "disable", "disabled"):
        db: Database = context.application.bot_data["db"]
        await db.set_setting(SETTINGS_START_IMG_URL, None)
        await _send_emoji_text(update.effective_chat.id, "✅ Start image removed.", context)
        return
    if not (raw.startswith("https://") or raw.startswith("http://")):
        await _send_emoji_text(update.effective_chat.id, "❌ Invalid URL. Must start with http:// or https://", context)
        return
    db: Database = context.application.bot_data["db"]
    await db.set_setting(SETTINGS_START_IMG_URL, raw)
    await _send_emoji_text(update.effective_chat.id, "✅ Start image set. Now /start will show the image.", context)


async def getemojiid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin helper: reply to a message containing Telegram custom (Premium) emojis to extract custom_emoji_id values.
    """
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await _send_emoji_text(update.effective_chat.id, "🚫 Access denied. (Admin/Owner only)", context)
        return
    if not update.effective_message or not update.effective_message.reply_to_message:
        await _send_emoji_text(
            update.effective_chat.id,
            "ℹ️ Reply to a message that contains Premium/custom emojis, then send: /getemojiid",
            context,
        )
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
        await _send_emoji_text(
            update.effective_chat.id,
            "⚠️ No custom emoji IDs found in that message.\n\n"
            "Tip: Premium/custom emojis are detectable only when they are custom emoji entities (not normal Unicode emojis).",
            context,
        )
        return

    out = "\n".join([f"• {cid}" for cid in uniq])
    await _send_emoji_text(
        update.effective_chat.id,
        "✅ Custom Emoji IDs Found\n\n"
        f"{out}\n\n"
        "Set for UI:\n"
        "• /setuitemoji <name> <custom_emoji_id>\n"
        "Example: /setuitemoji lock 54545454545454545",
        context,
    )


async def setuitemoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Stores a mapping of UI emoji name -> custom_emoji_id in settings.
    (Actual UI replacement can be wired up later once you provide IDs.)
    """
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await _send_emoji_text(update.effective_chat.id, "🚫 Access denied. (Admin/Owner only)", context)
        return
    if len(context.args) < 1:
        await _send_emoji_text(
            update.effective_chat.id,
            "🧩 UI Emoji Settings\n\n"
            "✅ Usage:\n"
            "• /setuitemoji <name> <custom_emoji_id>\n"
            "• /setuitemoji <name> off\n\n"
            "Example:\n"
            "• /setuitemoji lock 54545454545454545",
            context,
        )
        return

    name = context.args[0].strip().lower()
    if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in name) or len(name) > 32:
        await _send_emoji_text(update.effective_chat.id, "❌ Invalid name. Use letters/numbers/_/- (max 32).", context)
        return

    if len(context.args) < 2:
        await _send_emoji_text(update.effective_chat.id, "ℹ️ Missing value. Use: /setuitemoji <name> <custom_emoji_id|off>", context)
        return

    val = context.args[1].strip()
    key = f"{SETTINGS_UI_EMOJI_PREFIX}{name}"
    db: Database = context.application.bot_data["db"]

    if val.lower() in ("off", "remove", "none", "disable", "disabled"):
        await db.set_setting(key, None)
        _invalidate_ui_emoji_cache(context)
        await _send_emoji_text(update.effective_chat.id, f"✅ UI emoji removed for {name}.", context)
        return

    if not val.isdigit():
        await _send_emoji_text(update.effective_chat.id, "❌ Invalid custom_emoji_id. It must be numeric.", context)
        return

    await db.set_setting(key, val)
    _invalidate_ui_emoji_cache(context)
    await _send_emoji_text(update.effective_chat.id, f"✅ UI emoji set: {name} -> {val}", context)


async def setemojipreset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    One-shot preset loader for UI emoji IDs shared by admin.
    """
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await _send_emoji_text(update.effective_chat.id, "🚫 Access denied. (Admin/Owner only)", context)
        return
    db: Database = context.application.bot_data["db"]
    for name, eid in PRESET_UI_EMOJI_IDS.items():
        await db.set_setting(f"{SETTINGS_UI_EMOJI_PREFIX}{name}", eid)
    _invalidate_ui_emoji_cache(context)
    await _send_emoji_text(
        update.effective_chat.id,
        "✅ UI emoji preset saved.\n\n"
        f"🧾 Total mapped: {len(PRESET_UI_EMOJI_IDS)}\n"
        "Next step: UI rendering me in IDs ko apply karna.",
        context,
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await _send_emoji_text(update.effective_chat.id, "🚫 Access denied. (Admin/Owner only)", context)
        return
    db: Database = context.application.bot_data["db"]
    s = await db.stats()
    lines = [f"• [b]{k}:[/b] [c]{v}[/c]" for k, v in s.items()]
    await _send_emoji_text(
        update.effective_chat.id,
        "📊 [b]Bot Stats[/b]\n[i]Live summary[/i]\n\n" + "\n".join(lines),
        context,
    )


async def premiumdb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await _send_emoji_text(update.effective_chat.id, "🚫 Access denied. (Admin/Owner only)", context)
        return
    if not update.effective_chat:
        return
    db: Database = context.application.bot_data["db"]
    rows = await db.list_premium_records()
    payment_meta = await db.list_latest_payment_meta()
    if not rows:
        await _send_emoji_text(update.effective_chat.id, "ℹ️ No premium records found.", context)
        return

    try:
        from openpyxl import Workbook
    except Exception:
        await _send_emoji_text(
            update.effective_chat.id,
            "❌ Excel export dependency missing.\nInstall: [c]pip install openpyxl[/c]",
            context,
        )
        return

    wb = Workbook()
    ws = wb.active
    ws.title = "PremiumRecords"
    headers = [
        "user_id",
        "first_name",
        "username",
        "premium_until_unix",
        "premium_until_utc",
        "active_now",
        "last_payment_request_id",
        "last_payment_amount_rs",
        "last_payment_plan_days",
        "last_payment_status",
        "last_payment_unix",
        "last_payment_utc",
        "created_at_unix",
        "created_at_utc",
        "last_seen_unix",
        "last_seen_utc",
    ]
    ws.append(headers)
    now = int(time.time())
    for r in rows:
        pu = int(r.get("premium_until") or 0)
        ca = int(r.get("created_at") or 0)
        ls = int(r.get("last_seen") or 0)
        pm = payment_meta.get(int(r.get("user_id") or 0), {})
        pay_ts = int(pm.get("payment_ts") or 0)
        ws.append(
            [
                int(r.get("user_id") or 0),
                str(r.get("first_name") or ""),
                str(r.get("username") or ""),
                pu,
                datetime.datetime.utcfromtimestamp(pu).strftime("%Y-%m-%d %H:%M:%S UTC") if pu > 0 else "",
                "yes" if pu >= now else "no",
                int(pm.get("request_id") or 0),
                int(pm.get("amount_rs") or 0),
                int(pm.get("plan_days") or 0),
                str(pm.get("status") or ""),
                pay_ts,
                datetime.datetime.utcfromtimestamp(pay_ts).strftime("%Y-%m-%d %H:%M:%S UTC") if pay_ts > 0 else "",
                ca,
                datetime.datetime.utcfromtimestamp(ca).strftime("%Y-%m-%d %H:%M:%S UTC") if ca > 0 else "",
                ls,
                datetime.datetime.utcfromtimestamp(ls).strftime("%Y-%m-%d %H:%M:%S UTC") if ls > 0 else "",
            ]
        )

    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="premium_records_", suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name
        wb.save(tmp_path)
        date_tag = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        with open(tmp_path, "rb") as fh:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=fh,
                filename=f"premium_records_{date_tag}.xlsx",
                caption=f"📦 Premium DB export\n🧾 Total records: {len(rows)}",
            )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _upsert_user(update, context)
    if not await _is_admin_or_owner(update, context):
        await _send_emoji_text(update.effective_chat.id, "🚫 Access denied. (Admin/Owner only)", context)
        return
    if not update.effective_message or not update.effective_message.reply_to_message:
        await _send_emoji_text(update.effective_chat.id, "ℹ️ Reply to a message, then send: /broadcast", context)
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
    await _send_emoji_text(
        update.effective_chat.id,
        "📣 Broadcast Completed\n\n"
        f"✅ Sent: {ok}\n"
        f"⚠️ Failed: {fail}",
        context,
    )


def build_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(recheck_callback, pattern=r"^(recheck:|noop)"))
    app.add_handler(CallbackQueryHandler(pay_callback, pattern=r"^pay(plan|utr):"))
    app.add_handler(CallbackQueryHandler(pay_admin_callback, pattern=r"^payadm:(approve|reject):"))
    app.add_handler(CallbackQueryHandler(bsettings_callback, pattern=r"^bset:"))

    # /forcech uses guided text input + mode callback.
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, pay_utr_input), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bsettings_owner_addadmin_input), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bsettings_owner_removeadmin_input), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bsettings_gencode_input), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bsettings_forcech_remove_input), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bsettings_misc_input), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forcech_input), group=0)
    app.add_handler(CallbackQueryHandler(forcech_mode_callback, pattern=r"^forcech_mode:(direct|request)$"))
    app.add_handler(ChatJoinRequestHandler(on_chat_join_request))

    # /batch uses non-command text inputs (start/end post links).
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, batch_link_input), group=1)

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
    app.add_handler(CommandHandler("forcechdebug", forcechdebug))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("premiumdb", premiumdb))
    app.add_handler(CommandHandler("setcaption", setcaption))
    app.add_handler(CommandHandler("removecaption", removecaption))
    app.add_handler(CommandHandler("settime", settime))
    app.add_handler(CommandHandler("setstartimg", setstartimg))
    app.add_handler(CommandHandler("getemojiid", getemojiid))
    app.add_handler(CommandHandler("setuitemoji", setuitemoji))
    app.add_handler(CommandHandler("setemojipreset", setemojipreset))

    app.add_handler(CommandHandler("redeem", redeem))
    app.add_handler(CommandHandler("plan", plan))
    app.add_handler(CommandHandler("pay", pay))
    app.add_handler(CommandHandler("setpay", setpay))
    app.add_handler(CommandHandler("bsettings", bsettings))

    # PTB v20+ uses uppercase filter shortcuts (VIDEO/AUDIO/PHOTO). Document is namespaced.
    media_filter = filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.PHOTO
    app.add_handler(MessageHandler(filters.ALL & media_filter, admin_media_ingest))

