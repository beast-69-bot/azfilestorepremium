from __future__ import annotations

import os
import time
from typing import Any, Optional

import aiosqlite


def _now() -> int:
    return int(time.time())


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._ensure_schema()

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None
        return self._conn

    async def _ensure_schema(self) -> None:
        await self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              user_id       INTEGER PRIMARY KEY,
              first_name    TEXT,
              username      TEXT,
              premium_until INTEGER NOT NULL DEFAULT 0,
              created_at    INTEGER NOT NULL,
              last_seen     INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS admins (
              user_id  INTEGER PRIMARY KEY,
              added_by INTEGER NOT NULL,
              added_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
              key   TEXT PRIMARY KEY,
              value TEXT
            );

            CREATE TABLE IF NOT EXISTS force_channels (
              channel_id  INTEGER PRIMARY KEY,
              invite_link TEXT,
              title       TEXT,
              username    TEXT,
              added_by    INTEGER NOT NULL,
              added_at    INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS files (
              id             INTEGER PRIMARY KEY AUTOINCREMENT,
              tg_file_id     TEXT NOT NULL,
              file_unique_id TEXT,
              file_type      TEXT NOT NULL,
              file_name      TEXT,
              added_by       INTEGER NOT NULL,
              added_at       INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_files_unique ON files(file_unique_id);

            CREATE TABLE IF NOT EXISTS batches (
              id         INTEGER PRIMARY KEY AUTOINCREMENT,
              created_by INTEGER NOT NULL,
              created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS batch_items (
              batch_id INTEGER NOT NULL,
              file_id  INTEGER NOT NULL,
              ord      INTEGER NOT NULL,
              PRIMARY KEY (batch_id, file_id),
              FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE,
              FOREIGN KEY (file_id)  REFERENCES files(id)   ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_batch_items_batch ON batch_items(batch_id, ord);

            CREATE TABLE IF NOT EXISTS links (
              code         TEXT PRIMARY KEY,
              target_type  TEXT NOT NULL, -- file|batch
              target_id    INTEGER NOT NULL,
              access       TEXT NOT NULL, -- normal|premium
              created_by   INTEGER NOT NULL,
              created_at   INTEGER NOT NULL,
              last_used_at INTEGER,
              uses         INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_type, target_id, access);

            CREATE TABLE IF NOT EXISTS tokens (
              token         TEXT PRIMARY KEY,
              created_by    INTEGER NOT NULL,
              created_at    INTEGER NOT NULL,
              used_by       INTEGER,
              used_at       INTEGER,
              grant_seconds INTEGER NOT NULL
            );
            """
        )
        await self.conn.commit()

    # Users
    async def upsert_user(self, user_id: int, first_name: str | None, username: str | None) -> None:
        now = _now()
        await self.conn.execute(
            """
            INSERT INTO users(user_id, first_name, username, premium_until, created_at, last_seen)
            VALUES(?, ?, ?, 0, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              first_name=excluded.first_name,
              username=excluded.username,
              last_seen=excluded.last_seen
            """,
            (int(user_id), first_name, username, now, now),
        )
        await self.conn.commit()

    async def is_premium_active(self, user_id: int) -> bool:
        now = _now()
        cur = await self.conn.execute("SELECT premium_until FROM users WHERE user_id=?", (int(user_id),))
        row = await cur.fetchone()
        await cur.close()
        return bool(row and int(row[0]) >= now)

    async def add_premium_seconds(self, user_id: int, seconds: int) -> int:
        now = _now()
        cur = await self.conn.execute("SELECT premium_until FROM users WHERE user_id=?", (int(user_id),))
        row = await cur.fetchone()
        await cur.close()
        current = int(row[0]) if row else 0
        new_until = max(current, now) + int(seconds)
        await self.conn.execute(
            """
            INSERT INTO users(user_id, premium_until, created_at, last_seen)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET premium_until=excluded.premium_until, last_seen=excluded.last_seen
            """,
            (int(user_id), int(new_until), now, now),
        )
        await self.conn.commit()
        return int(new_until)

    async def set_premium_until(self, user_id: int, premium_until: int) -> None:
        now = _now()
        await self.conn.execute(
            """
            INSERT INTO users(user_id, premium_until, created_at, last_seen)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET premium_until=excluded.premium_until, last_seen=excluded.last_seen
            """,
            (int(user_id), int(premium_until), now, now),
        )
        await self.conn.commit()

    async def list_user_ids(self) -> list[int]:
        cur = await self.conn.execute("SELECT user_id FROM users")
        rows = await cur.fetchall()
        await cur.close()
        return [int(r[0]) for r in rows]

    # Admins
    async def is_admin(self, user_id: int) -> bool:
        cur = await self.conn.execute("SELECT 1 FROM admins WHERE user_id=?", (int(user_id),))
        row = await cur.fetchone()
        await cur.close()
        return bool(row)

    async def add_admin(self, user_id: int, added_by: int) -> None:
        now = _now()
        await self.conn.execute(
            "INSERT OR REPLACE INTO admins(user_id, added_by, added_at) VALUES(?, ?, ?)",
            (int(user_id), int(added_by), now),
        )
        await self.conn.commit()

    async def remove_admin(self, user_id: int) -> None:
        await self.conn.execute("DELETE FROM admins WHERE user_id=?", (int(user_id),))
        await self.conn.commit()

    # Settings
    async def set_setting(self, key: str, value: str | None) -> None:
        if value is None:
            await self.conn.execute("DELETE FROM settings WHERE key=?", (key,))
        else:
            await self.conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        await self.conn.commit()

    async def get_setting(self, key: str) -> Optional[str]:
        cur = await self.conn.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        await cur.close()
        return row[0] if row else None

    # Force channels
    async def add_force_channel(
        self,
        channel_id: int,
        invite_link: str | None,
        title: str | None,
        username: str | None,
        added_by: int,
    ) -> None:
        now = _now()
        await self.conn.execute(
            """
            INSERT INTO force_channels(channel_id, invite_link, title, username, added_by, added_at)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
              invite_link=excluded.invite_link,
              title=excluded.title,
              username=excluded.username,
              added_by=excluded.added_by,
              added_at=excluded.added_at
            """,
            (int(channel_id), invite_link, title, username, int(added_by), now),
        )
        await self.conn.commit()

    async def remove_force_channel(self, channel_id: int) -> None:
        await self.conn.execute("DELETE FROM force_channels WHERE channel_id=?", (int(channel_id),))
        await self.conn.commit()

    async def list_force_channels(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute("SELECT channel_id, invite_link, title, username FROM force_channels ORDER BY channel_id")
        rows = await cur.fetchall()
        await cur.close()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append({"channel_id": int(r[0]), "invite_link": r[1], "title": r[2], "username": r[3]})
        return out

    # Files
    async def save_file(
        self,
        tg_file_id: str,
        file_unique_id: str | None,
        file_type: str,
        file_name: str | None,
        added_by: int,
    ) -> int:
        now = _now()
        if file_unique_id:
            cur = await self.conn.execute("SELECT id FROM files WHERE file_unique_id=?", (file_unique_id,))
            row = await cur.fetchone()
            await cur.close()
            if row:
                return int(row[0])
        cur = await self.conn.execute(
            "INSERT INTO files(tg_file_id, file_unique_id, file_type, file_name, added_by, added_at) VALUES(?, ?, ?, ?, ?, ?)",
            (tg_file_id, file_unique_id, file_type, file_name, int(added_by), now),
        )
        await self.conn.commit()
        return int(cur.lastrowid)

    async def get_file(self, file_id: int) -> Optional[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT id, tg_file_id, file_unique_id, file_type, file_name, added_by, added_at FROM files WHERE id=?",
            (int(file_id),),
        )
        row = await cur.fetchone()
        await cur.close()
        if not row:
            return None
        return {
            "id": int(row[0]),
            "tg_file_id": row[1],
            "file_unique_id": row[2],
            "file_type": row[3],
            "file_name": row[4],
            "added_by": int(row[5]),
            "added_at": int(row[6]),
        }

    async def list_recent_files(self, limit: int = 15) -> list[dict[str, Any]]:
        cur = await self.conn.execute("SELECT id, file_type, file_name, added_at FROM files ORDER BY id DESC LIMIT ?", (int(limit),))
        rows = await cur.fetchall()
        await cur.close()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append({"id": int(r[0]), "file_type": r[1], "file_name": r[2], "added_at": int(r[3])})
        return out

    # Batches
    async def create_batch(self, created_by: int, file_ids: list[int]) -> int:
        now = _now()
        cur = await self.conn.execute("INSERT INTO batches(created_by, created_at) VALUES(?, ?)", (int(created_by), now))
        batch_id = int(cur.lastrowid)
        for ord_, fid in enumerate(file_ids):
            await self.conn.execute(
                "INSERT INTO batch_items(batch_id, file_id, ord) VALUES(?, ?, ?)",
                (batch_id, int(fid), int(ord_)),
            )
        await self.conn.commit()
        return batch_id

    async def get_batch_file_ids(self, batch_id: int) -> list[int]:
        cur = await self.conn.execute("SELECT file_id FROM batch_items WHERE batch_id=? ORDER BY ord", (int(batch_id),))
        rows = await cur.fetchall()
        await cur.close()
        return [int(r[0]) for r in rows]

    # Links
    async def create_link(self, code: str, target_type: str, target_id: int, access: str, created_by: int) -> None:
        now = _now()
        await self.conn.execute(
            """
            INSERT OR REPLACE INTO links(code, target_type, target_id, access, created_by, created_at, last_used_at, uses)
            VALUES(?, ?, ?, ?, ?, ?, NULL, 0)
            """,
            (code, target_type, int(target_id), access, int(created_by), now),
        )
        await self.conn.commit()

    async def get_link(self, code: str) -> Optional[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT code, target_type, target_id, access, created_by, created_at, last_used_at, uses FROM links WHERE code=?",
            (code,),
        )
        row = await cur.fetchone()
        await cur.close()
        if not row:
            return None
        return {
            "code": row[0],
            "target_type": row[1],
            "target_id": int(row[2]),
            "access": row[3],
            "created_by": int(row[4]),
            "created_at": int(row[5]),
            "last_used_at": row[6],
            "uses": int(row[7]),
        }

    async def mark_link_used(self, code: str) -> None:
        now = _now()
        await self.conn.execute("UPDATE links SET last_used_at=?, uses=uses+1 WHERE code=?", (now, code))
        await self.conn.commit()

    # Tokens
    async def create_token(self, token: str, created_by: int, grant_seconds: int) -> None:
        now = _now()
        await self.conn.execute(
            "INSERT OR REPLACE INTO tokens(token, created_by, created_at, used_by, used_at, grant_seconds) "
            "VALUES(?, ?, ?, NULL, NULL, ?)",
            (token, int(created_by), now, int(grant_seconds)),
        )
        await self.conn.commit()

    async def redeem_token(self, token: str, user_id: int) -> Optional[int]:
        cur = await self.conn.execute("SELECT used_by, grant_seconds FROM tokens WHERE token=?", (token,))
        row = await cur.fetchone()
        await cur.close()
        if not row or row[0] is not None:
            return None

        grant_seconds = int(row[1])
        now = _now()
        await self.conn.execute(
            "UPDATE tokens SET used_by=?, used_at=? WHERE token=? AND used_by IS NULL",
            (int(user_id), now, token),
        )
        # aiosqlite/pysqlite rowcount can be unreliable; use SQLite `changes()`.
        cur3 = await self.conn.execute("SELECT changes()")
        changes_row = await cur3.fetchone()
        await cur3.close()
        await self.conn.commit()
        if not changes_row or int(changes_row[0]) != 1:
            return None
        return grant_seconds

    # Stats
    async def stats(self) -> dict[str, int]:
        now = _now()
        out: dict[str, int] = {}
        queries = [
            ("users", "SELECT COUNT(*) FROM users", ()),
            ("admins", "SELECT COUNT(*) FROM admins", ()),
            ("files", "SELECT COUNT(*) FROM files", ()),
            ("batches", "SELECT COUNT(*) FROM batches", ()),
            ("links", "SELECT COUNT(*) FROM links", ()),
            ("premium_active", "SELECT COUNT(*) FROM users WHERE premium_until>=?", (now,)),
            ("tokens_total", "SELECT COUNT(*) FROM tokens", ()),
            ("tokens_used", "SELECT COUNT(*) FROM tokens WHERE used_by IS NOT NULL", ()),
        ]
        for key, q, params in queries:
            cur = await self.conn.execute(q, params)
            row = await cur.fetchone()
            await cur.close()
            out[key] = int(row[0]) if row else 0
        return out
