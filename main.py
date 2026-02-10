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
    # Show who can use which command in the command menu descriptions.
    commands = [
        BotCommand("start", "Start / open link (User)"),
        BotCommand("redeem", "Redeem token (User)"),
        BotCommand("getlink", "Generate links (Admin/Owner)"),
        BotCommand("batch", "Channel batch links (Admin/Owner)"),
        BotCommand("custombatch", "Custom file batch (Admin/Owner)"),
        BotCommand("addadmin", "Add admin (Owner)"),
        BotCommand("removeadmin", "Remove admin (Owner)"),
        BotCommand("addpremium", "Grant premium (Admin/Owner)"),
        BotCommand("removepremium", "Remove premium (Admin/Owner)"),
        BotCommand("gencode", "Generate token(s) (Admin/Owner)"),
        BotCommand("forcech", "Force-join channels (Admin/Owner)"),
        BotCommand("broadcast", "Broadcast message (Admin/Owner)"),
        BotCommand("stats", "Bot stats (Admin/Owner)"),
        BotCommand("setcaption", "Set default caption (Admin/Owner)"),
        BotCommand("removecaption", "Remove caption (Admin/Owner)"),
        BotCommand("settime", "Auto-delete delivered msgs (Admin/Owner)"),
        BotCommand("setstartimg", "Set /start image URL (Admin/Owner)"),
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
