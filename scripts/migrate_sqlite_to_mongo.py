from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from typing import Any

from pymongo import MongoClient, UpdateOne


def _rows(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    cur = conn.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    return rows


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    cols = {str(r[1]) for r in cur.fetchall()}
    cur.close()
    return cols


def main() -> None:
    ap = argparse.ArgumentParser(description="Migrate SQLite bot DB to MongoDB Atlas")
    ap.add_argument("--sqlite", required=True, help="Path to sqlite db file, e.g. data/bot.db")
    ap.add_argument("--mongo-uri", required=True, help="Mongo connection URI")
    ap.add_argument("--mongo-db", default="azfilestorepremium", help="Mongo database name")
    args = ap.parse_args()

    sconn = sqlite3.connect(args.sqlite)
    sconn.row_factory = sqlite3.Row

    mclient = MongoClient(args.mongo_uri)
    mdb = mclient[args.mongo_db]

    # users
    users = _rows(sconn, "SELECT user_id, first_name, username, premium_until, created_at, last_seen FROM users")
    if users:
        ops = [
            UpdateOne(
                {"user_id": int(r["user_id"])},
                {
                    "$set": {
                        "user_id": int(r["user_id"]),
                        "first_name": r["first_name"],
                        "username": r["username"],
                        "premium_until": int(r["premium_until"] or 0),
                        "created_at": int(r["created_at"] or 0),
                        "last_seen": int(r["last_seen"] or 0),
                    }
                },
                upsert=True,
            )
            for r in users
        ]
        mdb.users.bulk_write(ops, ordered=False)

    # admins
    admins = _rows(sconn, "SELECT user_id, added_by, added_at FROM admins")
    if admins:
        ops = [
            UpdateOne(
                {"user_id": int(r["user_id"])},
                {
                    "$set": {
                        "user_id": int(r["user_id"]),
                        "added_by": int(r["added_by"] or 0),
                        "added_at": int(r["added_at"] or 0),
                    }
                },
                upsert=True,
            )
            for r in admins
        ]
        mdb.admins.bulk_write(ops, ordered=False)

    # settings
    settings = _rows(sconn, "SELECT key, value FROM settings")
    if settings:
        ops = [UpdateOne({"key": str(r["key"])}, {"$set": {"key": str(r["key"]), "value": r["value"]}}, upsert=True) for r in settings]
        mdb.settings.bulk_write(ops, ordered=False)

    # force channels
    fc_cols = _table_columns(sconn, "force_channels")
    if "mode" in fc_cols:
        force_channels = _rows(
            sconn,
            "SELECT channel_id, mode, invite_link, title, username, added_by, added_at FROM force_channels",
        )
    else:
        force_channels = _rows(
            sconn,
            "SELECT channel_id, 'direct' AS mode, NULL AS invite_link, NULL AS title, NULL AS username, added_by, added_at FROM force_channels",
        )
    if force_channels:
        ops = [
            UpdateOne(
                {"channel_id": int(r["channel_id"])},
                {
                    "$set": {
                        "channel_id": int(r["channel_id"]),
                        "mode": r["mode"] or "direct",
                        "invite_link": r["invite_link"],
                        "title": r["title"],
                        "username": r["username"],
                        "added_by": int(r["added_by"] or 0),
                        "added_at": int(r["added_at"] or 0),
                    }
                },
                upsert=True,
            )
            for r in force_channels
        ]
        mdb.force_channels.bulk_write(ops, ordered=False)

    fjr = _rows(sconn, "SELECT channel_id, user_id, requested_at FROM force_join_requests")
    if fjr:
        ops = [
            UpdateOne(
                {"channel_id": int(r["channel_id"]), "user_id": int(r["user_id"])},
                {
                    "$set": {
                        "channel_id": int(r["channel_id"]),
                        "user_id": int(r["user_id"]),
                        "requested_at": int(r["requested_at"] or 0),
                    }
                },
                upsert=True,
            )
            for r in fjr
        ]
        mdb.force_join_requests.bulk_write(ops, ordered=False)

    # files
    files = _rows(
        sconn,
        "SELECT id, tg_file_id, file_unique_id, file_type, file_name, added_by, added_at FROM files",
    )
    if files:
        ops = [
            UpdateOne(
                {"id": int(r["id"])},
                {
                    "$set": {
                        "id": int(r["id"]),
                        "tg_file_id": r["tg_file_id"],
                        "file_unique_id": r["file_unique_id"],
                        "file_type": r["file_type"],
                        "file_name": r["file_name"],
                        "added_by": int(r["added_by"] or 0),
                        "added_at": int(r["added_at"] or 0),
                    }
                },
                upsert=True,
            )
            for r in files
        ]
        mdb.files.bulk_write(ops, ordered=False)

    # messages
    messages = _rows(
        sconn,
        "SELECT id, from_chat_id, message_id, added_by, added_at FROM messages",
    )
    if messages:
        ops = [
            UpdateOne(
                {"id": int(r["id"])},
                {
                    "$set": {
                        "id": int(r["id"]),
                        "from_chat_id": int(r["from_chat_id"] or 0),
                        "message_id": int(r["message_id"] or 0),
                        "added_by": int(r["added_by"] or 0),
                        "added_at": int(r["added_at"] or 0),
                    }
                },
                upsert=True,
            )
            for r in messages
        ]
        mdb.messages.bulk_write(ops, ordered=False)

    # batches + batch_items
    batches = _rows(sconn, "SELECT id, created_by, created_at FROM batches")
    batch_items = _rows(sconn, "SELECT batch_id, file_id, ord FROM batch_items ORDER BY ord ASC")
    file_map: dict[int, list[int]] = defaultdict(list)
    for r in batch_items:
        file_map[int(r["batch_id"])].append(int(r["file_id"]))
    if batches:
        ops = [
            UpdateOne(
                {"id": int(r["id"])},
                {
                    "$set": {
                        "id": int(r["id"]),
                        "created_by": int(r["created_by"] or 0),
                        "created_at": int(r["created_at"] or 0),
                        "file_ids": file_map.get(int(r["id"]), []),
                    }
                },
                upsert=True,
            )
            for r in batches
        ]
        mdb.batches.bulk_write(ops, ordered=False)

    # channel batches
    chb = _rows(
        sconn,
        "SELECT id, channel_id, start_msg_id, end_msg_id, created_by, created_at FROM channel_batches",
    )
    if chb:
        ops = [
            UpdateOne(
                {"id": int(r["id"])},
                {
                    "$set": {
                        "id": int(r["id"]),
                        "channel_id": int(r["channel_id"] or 0),
                        "start_msg_id": int(r["start_msg_id"] or 0),
                        "end_msg_id": int(r["end_msg_id"] or 0),
                        "created_by": int(r["created_by"] or 0),
                        "created_at": int(r["created_at"] or 0),
                    }
                },
                upsert=True,
            )
            for r in chb
        ]
        mdb.channel_batches.bulk_write(ops, ordered=False)

    # links
    links = _rows(
        sconn,
        "SELECT code, target_type, target_id, access, created_by, created_at, last_used_at, uses FROM links",
    )
    if links:
        ops = [
            UpdateOne(
                {"code": str(r["code"])},
                {
                    "$set": {
                        "code": str(r["code"]),
                        "target_type": r["target_type"],
                        "target_id": int(r["target_id"] or 0),
                        "access": r["access"],
                        "created_by": int(r["created_by"] or 0),
                        "created_at": int(r["created_at"] or 0),
                        "last_used_at": int(r["last_used_at"]) if r["last_used_at"] is not None else None,
                        "uses": int(r["uses"] or 0),
                    }
                },
                upsert=True,
            )
            for r in links
        ]
        mdb.links.bulk_write(ops, ordered=False)

    # tokens
    tokens = _rows(
        sconn,
        "SELECT token, created_by, created_at, used_by, used_at, grant_seconds FROM tokens",
    )
    if tokens:
        ops = [
            UpdateOne(
                {"token": str(r["token"])},
                {
                    "$set": {
                        "token": str(r["token"]),
                        "created_by": int(r["created_by"] or 0),
                        "created_at": int(r["created_at"] or 0),
                        "used_by": int(r["used_by"]) if r["used_by"] is not None else None,
                        "used_at": int(r["used_at"]) if r["used_at"] is not None else None,
                        "grant_seconds": int(r["grant_seconds"] or 0),
                    }
                },
                upsert=True,
            )
            for r in tokens
        ]
        mdb.tokens.bulk_write(ops, ordered=False)

    # payment requests (supports old/new schema)
    pr_cols = _table_columns(sconn, "payment_requests")
    select_cols = [
        "id",
        "user_id",
        "plan_key",
        "plan_days",
        "amount_rs",
        "status",
        "utr_text",
        "created_at",
        "updated_at",
        "processed_by",
        "processed_at",
    ]
    for extra in ("user_chat_id", "details_msg_id", "qr_msg_id", "expires_at"):
        if extra in pr_cols:
            select_cols.append(extra)
    prs = _rows(sconn, f"SELECT {', '.join(select_cols)} FROM payment_requests")
    if prs:
        ops = []
        for r in prs:
            ops.append(
                UpdateOne(
                    {"id": int(r["id"])},
                    {
                        "$set": {
                            "id": int(r["id"]),
                            "user_id": int(r["user_id"] or 0),
                            "plan_key": r["plan_key"],
                            "plan_days": int(r["plan_days"] or 0),
                            "amount_rs": int(r["amount_rs"] or 0),
                            "status": r["status"] or "pending",
                            "utr_text": r["utr_text"],
                            "created_at": int(r["created_at"] or 0),
                            "updated_at": int(r["updated_at"] or 0),
                            "processed_by": int(r["processed_by"]) if r["processed_by"] is not None else None,
                            "processed_at": int(r["processed_at"]) if r["processed_at"] is not None else None,
                            "user_chat_id": int(r["user_chat_id"]) if "user_chat_id" in r.keys() and r["user_chat_id"] is not None else None,
                            "details_msg_id": int(r["details_msg_id"]) if "details_msg_id" in r.keys() and r["details_msg_id"] is not None else None,
                            "qr_msg_id": int(r["qr_msg_id"]) if "qr_msg_id" in r.keys() and r["qr_msg_id"] is not None else None,
                            "expires_at": int(r["expires_at"]) if "expires_at" in r.keys() and r["expires_at"] is not None else None,
                        }
                    },
                    upsert=True,
                )
            )
        mdb.payment_requests.bulk_write(ops, ordered=False)

    # counters set to max existing ids for id-based collections.
    max_map = {
        "files": _rows(sconn, "SELECT COALESCE(MAX(id), 0) AS mx FROM files")[0]["mx"],
        "messages": _rows(sconn, "SELECT COALESCE(MAX(id), 0) AS mx FROM messages")[0]["mx"],
        "batches": _rows(sconn, "SELECT COALESCE(MAX(id), 0) AS mx FROM batches")[0]["mx"],
        "channel_batches": _rows(sconn, "SELECT COALESCE(MAX(id), 0) AS mx FROM channel_batches")[0]["mx"],
        "payment_requests": _rows(sconn, "SELECT COALESCE(MAX(id), 0) AS mx FROM payment_requests")[0]["mx"],
    }
    for key, mx in max_map.items():
        mdb.counters.update_one({"_id": key}, {"$set": {"seq": int(mx or 0)}}, upsert=True)

    sconn.close()
    mclient.close()
    print("Migration completed successfully.")


if __name__ == "__main__":
    main()
