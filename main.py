import asyncio
import logging

from dotenv import load_dotenv
from telegram import LinkPreviewOptions
from telegram.ext import Application, Defaults

from bot.config import Config
from bot.db import Database
from bot.handlers import build_handlers


async def _post_init(app: Application) -> None:
    me = await app.bot.get_me()
    app.bot_data["bot_username"] = me.username


async def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = Config.from_env()
    db = Database(cfg.db_path)
    await db.init()

    defaults = Defaults(link_preview_options=LinkPreviewOptions(is_disabled=True))
    app = (
        Application.builder()
        .token(cfg.bot_token)
        .defaults(defaults)
        .post_init(_post_init)
        .build()
    )

    app.bot_data["cfg"] = cfg
    app.bot_data["db"] = db

    build_handlers(app)

    logging.getLogger(__name__).info("Bot starting (polling)...")
    await app.run_polling(close_loop=False)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
