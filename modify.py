import re
import traceback

try:
    with open(r'bot\handlers.py', 'r', encoding='utf-8') as f:
        text = f.read()

    manual_fn = '''
async def _handle_manual_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, q: Any, rid: int, plan: dict) -> None:
    db: Database = context.application.bot_data["db"]
    pay_text = await db.get_setting(SETTINGS_PAY_TEXT)
    if not pay_text:
        pay_text = (
            "Pay to buy premium.\\n"
            "After payment, tap *Send UTR* and share UTR/screenshot.\\n"
            "Admin will verify and activate plan manually."
        )
    upi_id = await db.get_setting(SETTINGS_PAY_UPI)
    pay_name = await db.get_setting(SETTINGS_PAY_NAME) or "Premium Store"
    if not upi_id:
        await _edit_emoji_text(
            update.effective_chat.id,
            q.message.message_id,
            "⚠️ Payment is not configured by admin yet.\\nPlease contact admin.",
            context,
        )
        return

    note = f"premium {plan['label']} order#{rid}"
    upi_uri = _upi_uri(upi_id=upi_id, amount_rs=int(plan["amount"]), payee_name=pay_name, note=note)
    qr_url = _upi_qr_image_url(upi_uri)
    plan_label = html.escape(str(plan["label"]))
    upi_html = html.escape(upi_id)
    caption = (
        "💎 <b>Premium Purchase</b>\\n\\n"
        f"🛍 Plan: <b>{plan_label}</b>\\n"
        f"💰 Amount: ₹{plan['amount']}\\n\\n"
        "━━━━━━━━━━━━━━\\n\\n"
        "📲 <b>Pay via UPI</b>\\n"
        "Scan the QR above\\n"
        "OR send to:\\n\\n"
        f"<code>{upi_html}</code>\\n\\n"
        f"🆔 Order ID: <code>#{rid}</code>\\n\\n"
        "━━━━━━━━━━━━━━\\n\\n"
        "📌 <b>How to Activate</b>\\n"
        "1️⃣ Complete payment\\n"
        "2️⃣ Tap \\"Submit UTR\\"\\n"
        "3️⃣ Send transaction ID\\n"
        "4️⃣ Premium activates after verification\\n\\n"
        "⏳ Request expires in 5 minutes."
    )
    # Keep optional admin-configured payment note, without clutter.
    pay_text_clean = (pay_text or "").strip()
    if pay_text_clean:
        caption = f"{caption}\\n\\n🧾 <b>Note</b>\\n{html.escape(pay_text_clean)}"

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
            "⚠️ QR load failed. Pay via UPI ID shown above.\\n"
            f"UPI URI:\\n{upi_uri}",
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
'''

    poll_fn = '''
async def _poll_and_complete(context: ContextTypes.DEFAULT_TYPE, rid: int, qr_code_id: str) -> None:
    success = await xwallet_service.wait_for_payment(qr_code_id)
    db: Database = context.application.bot_data["db"]
    req = await db.get_payment_request(rid)
    if not req or req.get("status") != "pending":
        return
        
    if success:
        await db.approve_payment_request(rid, admin_id=0)
        await db.add_premium_seconds(int(req["user_id"]), int(req["plan_days"]) * DAY_SECONDS)
        try:
            await _send_emoji_text(
                int(req["user_id"]),
                "✅ Payment Received! Premium activated successfully.",
                context,
            )
        except Exception:
            pass
        await _cleanup_payment_user_ui(req, context)
    else:
        await db.expire_payment_request_if_pending(rid)
        try:
            await _send_emoji_text(
                int(req["user_id"]),
                "⏰ Payment request expired. Please use /plan to create a new order.",
                context,
            )
        except Exception:
            pass
        await _cleanup_payment_user_ui(req, context)
'''

    xwallet_fn = '''
async def _handle_xwallet_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, q: Any, rid: int, plan: dict, cfg: Any) -> None:
    try:
        loading = await _send_emoji_text(update.effective_chat.id, "⏳ Generating payment QR...", context)
        db: Database = context.application.bot_data["db"]
        amount = float(plan["amount"])
        
        create_res = await xwallet_service.create_payment(amount, str(rid), cfg.xwallet_api_key)
        qr_code_id = create_res.get("status_data", {}).get("qr_code_id") or create_res.get("qr_code_id")
        
        qr_res = await xwallet_service.get_qr_image_url(qr_code_id)
        qr_url = qr_res.get("status_data", {}).get("qr_url") or qr_res.get("qr_url")
        
        plan_label = html.escape(str(plan["label"]))
        caption = (
            "💎 <b>Premium Purchase (XWallet)</b>\\n\\n"
            f"🛍 Plan: <b>{plan_label}</b>\\n"
            f"💰 Amount: ₹{amount}\\n"
            f"🆔 Order ID: <code>#{rid}</code>\\n\\n"
            "⏳ Pay within 10 minutes.\\n"
            "<i>Do NOT close this message. Your plan will activate automatically once you pay.</i>"
        )
        
        if loading and hasattr(loading, "message_id"):
            try:
                await loading.delete()
            except Exception:
                pass
                
        if q.message:
            try:
                await q.message.delete()
            except Exception:
                pass
                
        payment_msg = await update.effective_chat.send_photo(
            photo=qr_url,
            caption=caption,
            parse_mode="HTML"
        )
        await db.set_payment_ui_messages(
            int(rid),
            int(update.effective_chat.id),
            int(payment_msg.message_id),
            None,
        )
        
        asyncio.create_task(_poll_and_complete(context, rid, qr_code_id))
        
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Error in XWallet payment flow: %s", e)
        await _send_emoji_text(update.effective_chat.id, "⚠️ Payment gateway error. Please try again.", context)
'''

    insert_str = f"\\n{manual_fn}\\n{poll_fn}\\n{xwallet_fn}\\n"
    text = text.replace("async def pay(update: Update", insert_str + "async def pay(update: Update")

    # Replace body of pay_callback
    block_regex = re.compile(r'        rid = await db\.create_payment_request.+?name=f"pay-timeout-\{rid\}",\n            \)\n        return', re.DOTALL)

    repl = '''        rid = await db.create_payment_request(update.effective_user.id, key, int(plan["days"]), int(plan["amount"]))
        cfg = context.application.bot_data["cfg"]
        if getattr(cfg, "payment_gateway", "manual") == "xwallet" and getattr(cfg, "xwallet_api_key", ""):
            await _handle_xwallet_payment(update, context, q, rid, plan, cfg)
        else:
            await _handle_manual_payment(update, context, q, rid, plan)
        return'''
    
    new_text, count = block_regex.subn(repl, text, count=1)
    
    print(f"Replaced body? {count > 0}")

    with open(r'bot\handlers.py', 'w', encoding='utf-8') as f:
        f.write(new_text)

except Exception as e:
    traceback.print_exc()
