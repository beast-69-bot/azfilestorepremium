import logging

from dotenv import load_dotenv
from telegram import BotCommand, LabeledPrice, LinkPreviewOptions, Update
from telegram.ext import Application, Defaults

from bot.config import Config
from bot.db import Database
from bot.db_mongo import MongoDatabase
from bot.handlers import build_handlers, resume_pending_payments_polling


async def _post_init(app: Application) -> None:
    # Runs inside PTB's event loop (safe place to init async dependencies).
    cfg: Config = app.bot_data["cfg"]
    if cfg.db_backend == "mongo":
        db = MongoDatabase(cfg.mongo_uri, cfg.mongo_db_name)
    else:
        db = Database(cfg.db_path)
    await db.init()
    app.bot_data["db"] = db

    me = await app.bot.get_me()
    app.bot_data["bot_username"] = me.username

    # Generate cached donation invoice link for Telegram Stars
    try:
        prices = [LabeledPrice(label="Donation", amount=1)]
        donation_link = await app.bot.create_invoice_link(
            title="Support Bot / Test Stars",
            description="Donate 1 Star to support development or test the payment flow.",
            payload="donation:1",
            provider_token="",
            currency="XTR",
            prices=prices,
        )
        app.bot_data["donation_invoice_link"] = donation_link
        logging.getLogger(__name__).info("Cached donation invoice link successfully generated.")
    except Exception as e:
        logging.getLogger(__name__).warning("Failed to generate cached donation invoice link: %r", e)

    # Recover any pending payments that are not yet expired in the background.
    import asyncio
    asyncio.create_task(resume_pending_payments_polling(app))

    # Set command menu (visible in Telegram UI).
    # Note: Telegram bot commands are not access-controlled by Telegram itself; handlers still enforce permissions.
    # Show who can use which command in the command menu descriptions.
    commands = [
        BotCommand("start", "ꜱᴛᴀʀᴛ / ᴏᴘᴇɴ ʟɪɴᴋ (ᴜꜱᴇʀ)"),
        BotCommand("cancel", "ᴄᴀɴᴄᴇʟ ᴄᴜʀʀᴇɴᴛ ᴘʀᴏᴄᴇꜱꜱ (ᴜꜱᴇʀ/ᴀᴅᴍɪɴ)"),
        BotCommand("redeem", "ʀᴇᴅᴇᴇᴍ ᴛᴏᴋᴇɴ (ᴜꜱᴇʀ)"),
        BotCommand("plan", "ᴠɪᴇᴡ ɴᴏʀᴍᴀʟ/ᴘʀᴇᴍɪᴜᴍ ᴘʟᴀɴꜱ (ᴜꜱᴇʀ)"),
        BotCommand("pay", "ʙᴜʏ ᴘʀᴇᴍɪᴜᴍ ᴘʟᴀɴ (ᴜꜱᴇʀ)"),
        BotCommand("getlink", "ɢᴇɴᴇʀᴀᴛᴇ ʟɪɴᴋꜱ (ᴀᴅᴍɪɴ/ᴏᴡɴᴇʀ)"),
        BotCommand("batch", "ᴄʜᴀɴɴᴇʟ ʙᴀᴛᴄʜ ʟɪɴᴋꜱ (ᴀᴅᴍɪɴ/ᴏᴡɴᴇʀ)"),
        BotCommand("custombatch", "ᴄᴜꜱᴛᴏᴍ ꜰɪʟᴇ ʙᴀᴛᴄʜ (ᴀᴅᴍɪɴ/ᴏᴡɴᴇʀ)"),
        BotCommand("bsettings", "ᴀᴅᴍɪɴ ᴄᴏᴍᴍᴀɴᴅ ɢᴜɪᴅᴇ ᴘᴀɴᴇʟ (ᴀᴅᴍɪɴ/ᴏᴡɴᴇʀ)"),
        BotCommand("extendlast24h", "ᴇxᴛᴇɴᴅ +1 ᴅᴀʏ ꜰᴏʀ ʟᴀꜱᴛ 24ʜ ʙᴜʏᴇʀꜱ (ᴀᴅᴍɪɴ/ᴏᴡɴᴇʀ)"),
        BotCommand("paylookup", "ʟᴏᴏᴋᴜᴘ ᴘᴀʏᴍᴇɴᴛ ᴏʀᴅᴇʀ ᴅᴇᴛᴀɪʟꜱ (ᴀᴅᴍɪɴ/ᴏᴡɴᴇʀ)"),
        BotCommand("manualapprove", "ᴀᴘᴘʀᴏᴠᴇ ᴍᴀɴᴜᴀʟ ᴏʀᴅᴇʀ ʙʏ ɪᴅ (ᴀᴅᴍɪɴ/ᴏᴡɴᴇʀ)"),
        BotCommand("revenue", "ᴠɪᴇᴡ ᴍᴏɴᴛʜʟʏ ʀᴇᴠᴇɴᴜᴇ ꜱᴛᴀᴛꜱ (ᴀᴅᴍɪɴ/ᴏᴡɴᴇʀ)"),
    ]
    try:
        await app.bot.set_my_commands(commands)
    except Exception as e:
        logging.getLogger(__name__).warning("Failed to set bot commands: %r", e)


async def _post_shutdown(app: Application) -> None:
    db = app.bot_data.get("db")
    if db:
        await db.close()

    try:
        from bot import razorpay_service, xwallet_service
        if getattr(razorpay_service, "_session", None) and not razorpay_service._session.closed:
            await razorpay_service._session.close()
        if getattr(xwallet_service, "_session", None) and not xwallet_service._session.closed:
            await xwallet_service._session.close()
    except Exception:
        pass


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = Config.from_env()

    defaults = Defaults(link_preview_options=LinkPreviewOptions(is_disabled=True))
    app = (
        Application.builder()
        .token(cfg.bot_token)
        .connect_timeout(10)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(10)
        .get_updates_connect_timeout(10)
        .get_updates_read_timeout(30)
        .get_updates_write_timeout(30)
        .get_updates_pool_timeout(10)
        .concurrent_updates(64)
        .defaults(defaults)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    app.bot_data["cfg"] = cfg

    build_handlers(app)

    logging.getLogger(__name__).info("Bot starting (polling)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
