import logging

from dotenv import load_dotenv
from telegram import BotCommand, LinkPreviewOptions, Update
from telegram.ext import Application, Defaults

from bot.config import Config
from bot.db import Database
from bot.db_mongo import MongoDatabase
from bot.handlers import build_handlers


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

    # Set command menu (visible in Telegram UI).
    # Note: Telegram bot commands are not access-controlled by Telegram itself; handlers still enforce permissions.
    # Show who can use which command in the command menu descriptions.
    commands = [
        BotCommand("start", "Start / open link (User)"),
        BotCommand("cancel", "Cancel current process (User/Admin)"),
        BotCommand("redeem", "Redeem token (User)"),
        BotCommand("plan", "View normal/premium plans (User)"),
        BotCommand("pay", "Buy premium plan (User)"),
        BotCommand("getlink", "Generate links (Admin/Owner)"),
        BotCommand("batch", "Channel batch links (Admin/Owner)"),
        BotCommand("custombatch", "Custom file batch (Admin/Owner)"),
        BotCommand("bsettings", "Admin command guide panel (Admin/Owner)"),
    ]
    try:
        await app.bot.set_my_commands(commands)
    except Exception as e:
        logging.getLogger(__name__).warning("Failed to set bot commands: %r", e)


async def _post_shutdown(app: Application) -> None:
    db = app.bot_data.get("db")
    if db:
        await db.close()


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
