from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_id: int
    link_secret: str
    db_path: str = "data/bot.db"

    @staticmethod
    def from_env() -> "Config":
        bot_token = os.getenv("BOT_TOKEN", "").strip()
        owner_id_raw = os.getenv("OWNER_ID", "").strip()
        link_secret = os.getenv("LINK_SECRET", "").strip()
        db_path = os.getenv("DB_PATH", "data/bot.db").strip()

        if not bot_token:
            raise RuntimeError("BOT_TOKEN is required")
        if not owner_id_raw:
            raise RuntimeError("OWNER_ID is required")
        if not link_secret:
            raise RuntimeError("LINK_SECRET is required")

        try:
            owner_id = int(owner_id_raw)
        except ValueError as e:
            raise RuntimeError("OWNER_ID must be an integer") from e

        return Config(
            bot_token=bot_token,
            owner_id=owner_id,
            link_secret=link_secret,
            db_path=db_path,
        )

