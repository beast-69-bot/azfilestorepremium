from __future__ import annotations

import time
from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ReturnDocument


def _now() -> int:
    return int(time.time())


class MongoDatabase:
    def __init__(self, uri: str, db_name: str) -> None:
        self.uri = uri
        self.db_name = db_name
        self._client: AsyncIOMotorClient | None = None
        self._db: AsyncIOMotorDatabase | None = None

    @property
    def db(self) -> AsyncIOMotorDatabase:
        assert self._db is not None
        return self._db

    async def init(self) -> None:
        self._client = AsyncIOMotorClient(self.uri)
        self._db = self._client[self.db_name]
        await self._ensure_schema()

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._db = None

    async def _ensure_schema(self) -> None:
        # Logical unique keys
        await self.db.users.create_index("user_id", unique=True)
        await self.db.admins.create_index("user_id", unique=True)
        await self.db.files.create_index("file_unique_id", sparse=True)
        await self.db.links.create_index("code", unique=True)
        await self.db.tokens.create_index("token", unique=True)
        await self.db.force_channels.create_index("channel_id", unique=True)
        await self.db.force_join_requests.create_index([("channel_id", 1), ("user_id", 1)], unique=True)
        await self.db.payment_requests.create_index([("user_id", 1), ("status", 1)])

    async def _next_id(self, name: str) -> int:
        doc = await self.db.counters.find_one_and_update(
            {"_id": name},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(doc["seq"])

    # Users
    async def upsert_user(self, user_id: int, first_name: str | None, username: str | None) -> None:
        now = _now()
        await self.db.users.update_one(
            {"user_id": int(user_id)},
            {
                "$set": {"first_name": first_name, "username": username, "last_seen": now},
                "$setOnInsert": {"premium_until": 0, "created_at": now},
            },
            upsert=True,
        )

    async def is_premium_active(self, user_id: int) -> bool:
        now = _now()
        row = await self.db.users.find_one({"user_id": int(user_id)}, {"premium_until": 1, "_id": 0})
        return bool(row and int(row.get("premium_until") or 0) >= now)

    async def get_premium_until(self, user_id: int) -> int:
        row = await self.db.users.find_one({"user_id": int(user_id)}, {"premium_until": 1, "_id": 0})
        return int(row.get("premium_until") or 0) if row else 0

    async def add_premium_seconds(self, user_id: int, seconds: int) -> int:
        now = _now()
        current = await self.get_premium_until(int(user_id))
        new_until = max(current, now) + int(seconds)
        await self.db.users.update_one(
            {"user_id": int(user_id)},
            {"$set": {"premium_until": int(new_until), "last_seen": now}, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        return int(new_until)

    async def set_premium_until(self, user_id: int, premium_until: int) -> None:
        now = _now()
        await self.db.users.update_one(
            {"user_id": int(user_id)},
            {"$set": {"premium_until": int(premium_until), "last_seen": now}, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )

    async def list_user_ids(self) -> list[int]:
        rows = self.db.users.find({}, {"user_id": 1, "_id": 0})
        out: list[int] = []
        async for r in rows:
            out.append(int(r["user_id"]))
        return out

    async def list_premium_records(self) -> list[dict[str, Any]]:
        rows = self.db.users.find(
            {"premium_until": {"$gt": 0}},
            {
                "_id": 0,
                "user_id": 1,
                "first_name": 1,
                "username": 1,
                "premium_until": 1,
                "created_at": 1,
                "last_seen": 1,
            },
            sort=[("premium_until", -1)],
        )
        out: list[dict[str, Any]] = []
        async for r in rows:
            out.append(
                {
                    "user_id": int(r.get("user_id") or 0),
                    "first_name": r.get("first_name") or "",
                    "username": r.get("username") or "",
                    "premium_until": int(r.get("premium_until") or 0),
                    "created_at": int(r.get("created_at") or 0),
                    "last_seen": int(r.get("last_seen") or 0),
                }
            )
        return out

    async def list_latest_payment_meta(self) -> dict[int, dict[str, Any]]:
        pipeline = [
            {"$sort": {"id": -1}},
            {
                "$group": {
                    "_id": "$user_id",
                    "request_id": {"$first": "$id"},
                    "amount_rs": {"$first": "$amount_rs"},
                    "plan_days": {"$first": "$plan_days"},
                    "status": {"$first": "$status"},
                    "processed_at": {"$first": "$processed_at"},
                    "created_at": {"$first": "$created_at"},
                }
            },
        ]
        out: dict[int, dict[str, Any]] = {}
        async for r in self.db.payment_requests.aggregate(pipeline):
            uid = int(r.get("_id") or 0)
            payment_ts = int(r.get("processed_at") or r.get("created_at") or 0)
            out[uid] = {
                "request_id": int(r.get("request_id") or 0),
                "amount_rs": int(r.get("amount_rs") or 0),
                "plan_days": int(r.get("plan_days") or 0),
                "status": str(r.get("status") or ""),
                "payment_ts": payment_ts,
            }
        return out

    # Admins
    async def is_admin(self, user_id: int) -> bool:
        row = await self.db.admins.find_one({"user_id": int(user_id)}, {"_id": 1})
        return bool(row)

    async def add_admin(self, user_id: int, added_by: int) -> None:
        now = _now()
        await self.db.admins.update_one(
            {"user_id": int(user_id)},
            {"$set": {"user_id": int(user_id), "added_by": int(added_by), "added_at": now}},
            upsert=True,
        )

    async def remove_admin(self, user_id: int) -> None:
        await self.db.admins.delete_one({"user_id": int(user_id)})

    async def list_admin_ids(self) -> list[int]:
        rows = self.db.admins.find({}, {"user_id": 1, "_id": 0})
        out: list[int] = []
        async for r in rows:
            out.append(int(r["user_id"]))
        return out

    # Settings
    async def set_setting(self, key: str, value: str | None) -> None:
        if value is None:
            await self.db.settings.delete_one({"key": key})
            return
        await self.db.settings.update_one({"key": key}, {"$set": {"key": key, "value": value}}, upsert=True)

    async def get_setting(self, key: str) -> Optional[str]:
        row = await self.db.settings.find_one({"key": key}, {"value": 1, "_id": 0})
        return row.get("value") if row else None

    # Force channels
    async def add_force_channel(
        self,
        channel_id: int,
        mode: str,
        invite_link: str | None,
        title: str | None,
        username: str | None,
        added_by: int,
    ) -> None:
        now = _now()
        await self.db.force_channels.update_one(
            {"channel_id": int(channel_id)},
            {
                "$set": {
                    "channel_id": int(channel_id),
                    "mode": mode,
                    "invite_link": invite_link,
                    "title": title,
                    "username": username,
                    "added_by": int(added_by),
                    "added_at": now,
                }
            },
            upsert=True,
        )

    async def remove_force_channel(self, channel_id: int) -> None:
        await self.db.force_channels.delete_one({"channel_id": int(channel_id)})

    async def clear_force_channels(self) -> None:
        await self.db.force_channels.delete_many({})
        await self.db.force_join_requests.delete_many({})

    async def list_force_channels(self) -> list[dict[str, Any]]:
        rows = self.db.force_channels.find({}, {"_id": 0}).sort("channel_id", 1)
        out: list[dict[str, Any]] = []
        async for r in rows:
            out.append(
                {
                    "channel_id": int(r["channel_id"]),
                    "mode": r.get("mode") or "direct",
                    "invite_link": r.get("invite_link"),
                    "title": r.get("title"),
                    "username": r.get("username"),
                }
            )
        return out

    async def add_force_join_request(self, channel_id: int, user_id: int) -> None:
        now = _now()
        await self.db.force_join_requests.update_one(
            {"channel_id": int(channel_id), "user_id": int(user_id)},
            {"$set": {"channel_id": int(channel_id), "user_id": int(user_id), "requested_at": now}},
            upsert=True,
        )

    async def has_force_join_request(self, channel_id: int, user_id: int) -> bool:
        row = await self.db.force_join_requests.find_one(
            {"channel_id": int(channel_id), "user_id": int(user_id)},
            {"_id": 1},
        )
        return bool(row)

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
            row = await self.db.files.find_one({"file_unique_id": file_unique_id}, {"id": 1, "_id": 0})
            if row:
                return int(row["id"])
        new_id = await self._next_id("files")
        await self.db.files.insert_one(
            {
                "id": int(new_id),
                "tg_file_id": tg_file_id,
                "file_unique_id": file_unique_id,
                "file_type": file_type,
                "file_name": file_name,
                "added_by": int(added_by),
                "added_at": now,
            }
        )
        return int(new_id)

    async def get_file(self, file_id: int) -> Optional[dict[str, Any]]:
        row = await self.db.files.find_one({"id": int(file_id)}, {"_id": 0})
        if not row:
            return None
        return {
            "id": int(row["id"]),
            "tg_file_id": row["tg_file_id"],
            "file_unique_id": row.get("file_unique_id"),
            "file_type": row["file_type"],
            "file_name": row.get("file_name"),
            "added_by": int(row["added_by"]),
            "added_at": int(row["added_at"]),
        }

    async def list_recent_files(self, limit: int = 15) -> list[dict[str, Any]]:
        rows = self.db.files.find({}, {"id": 1, "file_type": 1, "file_name": 1, "added_at": 1, "_id": 0}).sort("id", -1).limit(int(limit))
        out: list[dict[str, Any]] = []
        async for r in rows:
            out.append(
                {
                    "id": int(r["id"]),
                    "file_type": r["file_type"],
                    "file_name": r.get("file_name"),
                    "added_at": int(r["added_at"]),
                }
            )
        return out

    # Messages
    async def save_message(self, from_chat_id: int, message_id: int, added_by: int) -> int:
        now = _now()
        new_id = await self._next_id("messages")
        await self.db.messages.insert_one(
            {
                "id": int(new_id),
                "from_chat_id": int(from_chat_id),
                "message_id": int(message_id),
                "added_by": int(added_by),
                "added_at": now,
            }
        )
        return int(new_id)

    async def get_message(self, msg_id: int) -> Optional[dict[str, Any]]:
        row = await self.db.messages.find_one({"id": int(msg_id)}, {"_id": 0})
        if not row:
            return None
        return {
            "id": int(row["id"]),
            "from_chat_id": int(row["from_chat_id"]),
            "message_id": int(row["message_id"]),
            "added_by": int(row["added_by"]),
            "added_at": int(row["added_at"]),
        }

    # Batches
    async def create_batch(self, created_by: int, file_ids: list[int]) -> int:
        now = _now()
        new_id = await self._next_id("batches")
        await self.db.batches.insert_one(
            {
                "id": int(new_id),
                "created_by": int(created_by),
                "created_at": now,
                "file_ids": [int(x) for x in file_ids],
            }
        )
        return int(new_id)

    async def get_batch_file_ids(self, batch_id: int) -> list[int]:
        row = await self.db.batches.find_one({"id": int(batch_id)}, {"file_ids": 1, "_id": 0})
        if not row:
            return []
        return [int(x) for x in (row.get("file_ids") or [])]

    # Channel batches
    async def create_channel_batch(self, created_by: int, channel_id: int, start_msg_id: int, end_msg_id: int) -> int:
        now = _now()
        new_id = await self._next_id("channel_batches")
        await self.db.channel_batches.insert_one(
            {
                "id": int(new_id),
                "channel_id": int(channel_id),
                "start_msg_id": int(start_msg_id),
                "end_msg_id": int(end_msg_id),
                "created_by": int(created_by),
                "created_at": now,
            }
        )
        return int(new_id)

    async def get_channel_batch(self, batch_id: int) -> Optional[dict[str, Any]]:
        row = await self.db.channel_batches.find_one({"id": int(batch_id)}, {"_id": 0})
        if not row:
            return None
        return {
            "id": int(row["id"]),
            "channel_id": int(row["channel_id"]),
            "start_msg_id": int(row["start_msg_id"]),
            "end_msg_id": int(row["end_msg_id"]),
            "created_by": int(row["created_by"]),
            "created_at": int(row["created_at"]),
        }

    # Links
    async def create_link(self, code: str, target_type: str, target_id: int, access: str, created_by: int) -> None:
        now = _now()
        await self.db.links.update_one(
            {"code": code},
            {
                "$set": {
                    "code": code,
                    "target_type": target_type,
                    "target_id": int(target_id),
                    "access": access,
                    "created_by": int(created_by),
                    "created_at": now,
                    "last_used_at": None,
                    "uses": 0,
                }
            },
            upsert=True,
        )

    async def get_link(self, code: str) -> Optional[dict[str, Any]]:
        row = await self.db.links.find_one({"code": code}, {"_id": 0})
        if not row:
            return None
        return {
            "code": row["code"],
            "target_type": row["target_type"],
            "target_id": int(row["target_id"]),
            "access": row["access"],
            "created_by": int(row["created_by"]),
            "created_at": int(row["created_at"]),
            "last_used_at": row.get("last_used_at"),
            "uses": int(row.get("uses") or 0),
        }

    async def mark_link_used(self, code: str) -> None:
        now = _now()
        await self.db.links.update_one({"code": code}, {"$set": {"last_used_at": now}, "$inc": {"uses": 1}})

    # Tokens
    async def create_token(self, token: str, created_by: int, grant_seconds: int) -> None:
        now = _now()
        await self.db.tokens.update_one(
            {"token": token},
            {
                "$set": {
                    "token": token,
                    "created_by": int(created_by),
                    "created_at": now,
                    "used_by": None,
                    "used_at": None,
                    "grant_seconds": int(grant_seconds),
                }
            },
            upsert=True,
        )

    async def redeem_token(self, token: str, user_id: int) -> Optional[int]:
        now = _now()
        row = await self.db.tokens.find_one_and_update(
            {"token": token, "used_by": None},
            {"$set": {"used_by": int(user_id), "used_at": now}},
            return_document=ReturnDocument.BEFORE,
        )
        if not row:
            return None
        return int(row.get("grant_seconds") or 0)

    # Stats
    async def stats(self) -> dict[str, int]:
        now = _now()
        users = await self.db.users.count_documents({})
        admins = await self.db.admins.count_documents({})
        files = await self.db.files.count_documents({})
        batches = await self.db.batches.count_documents({})
        links = await self.db.links.count_documents({})
        premium_active = await self.db.users.count_documents({"premium_until": {"$gte": now}})
        tokens_total = await self.db.tokens.count_documents({})
        tokens_used = await self.db.tokens.count_documents({"used_by": {"$ne": None}})
        return {
            "users": int(users),
            "admins": int(admins),
            "files": int(files),
            "batches": int(batches),
            "links": int(links),
            "premium_active": int(premium_active),
            "tokens_total": int(tokens_total),
            "tokens_used": int(tokens_used),
        }

    # Payments
    async def create_payment_request(self, user_id: int, plan_key: str, plan_days: int, amount_rs: int) -> int:
        now = _now()
        expires_at = now + 300
        new_id = await self._next_id("payment_requests")
        await self.db.payment_requests.insert_one(
            {
                "id": int(new_id),
                "user_id": int(user_id),
                "plan_key": plan_key,
                "plan_days": int(plan_days),
                "amount_rs": int(amount_rs),
                "status": "pending",
                "utr_text": None,
                "user_chat_id": None,
                "details_msg_id": None,
                "qr_msg_id": None,
                "expires_at": int(expires_at),
                "created_at": now,
                "updated_at": now,
                "processed_by": None,
                "processed_at": None,
            }
        )
        return int(new_id)

    async def set_payment_utr(self, request_id: int, utr_text: str) -> bool:
        now = _now()
        res = await self.db.payment_requests.update_one(
            {"id": int(request_id), "status": {"$in": ["pending", "submitted"]}},
            {"$set": {"utr_text": utr_text, "status": "submitted", "updated_at": now}},
        )
        return bool(res.modified_count > 0)

    async def get_payment_request(self, request_id: int) -> Optional[dict[str, Any]]:
        row = await self.db.payment_requests.find_one({"id": int(request_id)}, {"_id": 0})
        if not row:
            return None
        return {
            "id": int(row["id"]),
            "user_id": int(row["user_id"]),
            "plan_key": row["plan_key"],
            "plan_days": int(row["plan_days"]),
            "amount_rs": int(row["amount_rs"]),
            "status": row["status"],
            "utr_text": row.get("utr_text"),
            "user_chat_id": int(row["user_chat_id"]) if row.get("user_chat_id") is not None else None,
            "details_msg_id": int(row["details_msg_id"]) if row.get("details_msg_id") is not None else None,
            "qr_msg_id": int(row["qr_msg_id"]) if row.get("qr_msg_id") is not None else None,
            "expires_at": int(row.get("expires_at") or 0),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
            "processed_by": row.get("processed_by"),
            "processed_at": row.get("processed_at"),
        }

    async def get_latest_open_payment_request(self, user_id: int) -> Optional[dict[str, Any]]:
        row = await self.db.payment_requests.find_one(
            {"user_id": int(user_id), "status": {"$in": ["pending", "submitted"]}},
            {"_id": 0},
            sort=[("id", -1)],
        )
        if not row:
            return None
        return {
            "id": int(row["id"]),
            "user_id": int(row["user_id"]),
            "plan_key": row["plan_key"],
            "plan_days": int(row["plan_days"]),
            "amount_rs": int(row["amount_rs"]),
            "status": row["status"],
            "utr_text": row.get("utr_text"),
            "user_chat_id": int(row["user_chat_id"]) if row.get("user_chat_id") is not None else None,
            "details_msg_id": int(row["details_msg_id"]) if row.get("details_msg_id") is not None else None,
            "qr_msg_id": int(row["qr_msg_id"]) if row.get("qr_msg_id") is not None else None,
            "expires_at": int(row.get("expires_at") or 0),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
            "processed_by": row.get("processed_by"),
            "processed_at": row.get("processed_at"),
        }

    async def set_payment_ui_messages(self, request_id: int, user_chat_id: int, details_msg_id: int, qr_msg_id: int | None) -> None:
        now = _now()
        await self.db.payment_requests.update_one(
            {"id": int(request_id)},
            {
                "$set": {
                    "user_chat_id": int(user_chat_id),
                    "details_msg_id": int(details_msg_id),
                    "qr_msg_id": int(qr_msg_id) if qr_msg_id is not None else None,
                    "updated_at": now,
                }
            },
        )

    async def clear_payment_ui_messages(self, request_id: int) -> None:
        now = _now()
        await self.db.payment_requests.update_one(
            {"id": int(request_id)},
            {"$set": {"user_chat_id": None, "details_msg_id": None, "qr_msg_id": None, "updated_at": now}},
        )

    async def expire_payment_request_if_pending(self, request_id: int) -> bool:
        now = _now()
        res = await self.db.payment_requests.update_one(
            {
                "id": int(request_id),
                "status": "pending",
                "$or": [{"utr_text": None}, {"utr_text": ""}],
            },
            {"$set": {"status": "expired", "updated_at": now}},
        )
        return bool(res.modified_count > 0)

    async def approve_payment_request(self, request_id: int, admin_id: int) -> bool:
        now = _now()
        res = await self.db.payment_requests.update_one(
            {"id": int(request_id), "status": {"$in": ["submitted", "pending"]}},
            {"$set": {"status": "processed", "processed_by": int(admin_id), "processed_at": now, "updated_at": now}},
        )
        return bool(res.modified_count > 0)

    async def reject_payment_request(self, request_id: int, admin_id: int) -> bool:
        now = _now()
        res = await self.db.payment_requests.update_one(
            {"id": int(request_id), "status": {"$in": ["submitted", "pending"]}},
            {"$set": {"status": "rejected", "processed_by": int(admin_id), "processed_at": now, "updated_at": now}},
        )
        return bool(res.modified_count > 0)

    async def delete_payment_request(self, request_id: int) -> None:
        await self.db.payment_requests.delete_one({"id": int(request_id)})
