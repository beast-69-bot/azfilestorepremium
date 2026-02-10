import logging

from dotenv import load_dotenv
from telegram import BotCommand, LinkPreviewOptions
from telegram.ext import Application, Defaults

from bot.config import Config
from bot.db import Database
from bot.handlers import build_handlers


async def _post_init(app: Application) -> None:
    # Runs inside PTB's event loop (safe place to init async dependencies).
    cfg: Config = app.bot_data["cfg"]
    db = Database(cfg.db_path)
    await db.init()
    app.bot_data["db"] = db

    me = await app.bot.get_me()
    app.bot_data["bot_username"] = me.username

    # Set command menu (visible in Telegram UI).
    # Note: Telegram bot commands are not access-controlled by Telegram itself; handlers still enforce permissions.
    commands = [
        BotCommand("start", "Start the bot / open a link"),
        BotCommand("getlink", "Generate normal/premium links (reply to message/file)"),
        BotCommand("batch", "Create channel-post range batch links"),
        BotCommand("custombatch", "Create batch by sending files, then generate links"),
        BotCommand("addadmin", "Add an admin (owner only)"),
        BotCommand("removeadmin", "Remove an admin (owner only)"),
        BotCommand("addpremium", "Grant premium to a user"),
        BotCommand("removepremium", "Remove premium from a user"),
        BotCommand("gencode", "Generate one-time premium token"),
        BotCommand("redeem", "Redeem token for premium"),
        BotCommand("forcech", "Manage force-join channels"),
        BotCommand("broadcast", "Broadcast a message to all users (reply + /broadcast)"),
        BotCommand("stats", "View bot stats"),
        BotCommand("setcaption", "Set default caption"),
        BotCommand("removecaption", "Remove default caption"),
        BotCommand("settime", "Auto-delete delivered files/messages after time"),
        BotCommand("setstartimg", "Set image URL for /start welcome message"),
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
        .defaults(defaults)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    app.bot_data["cfg"] = cfg

    build_handlers(app)

    logging.getLogger(__name__).info("Bot starting (polling)...")
    app.run_polling()


if __name__ == "__main__":
    main()
