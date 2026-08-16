"""
database.py — SQLite persistence layer using aiosqlite.

Tables:
  rules            — keyword→message rules
  processed_events — idempotency for incoming webhook events (event_id dedup)
  dm_sends         — every DM attempt with status tracking
  deleted_comments — comment_ids that arrived as comment.deleted
"""

import asyncio
import logging
import uuid
from typing import Optional

import aiosqlite

from app.config import settings

logger = logging.getLogger(__name__)

_db_path = settings.database_path


async def init_db() -> None:
    """Create all tables if they don't exist."""
    async with aiosqlite.connect(_db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")

        # Rules table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS rules (
                rule_id     TEXT PRIMARY KEY,
                keyword     TEXT NOT NULL,
                dm_message  TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Processed webhook events (dedup by event_id)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS processed_events (
                event_id   TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # DM sends — one row per (rule_id, user_id) pair
        await db.execute("""
            CREATE TABLE IF NOT EXISTS dm_sends (
                id              TEXT PRIMARY KEY,
                rule_id         TEXT NOT NULL,
                user_id         TEXT NOT NULL,
                comment_id      TEXT NOT NULL,
                dm_id           TEXT,
                status          TEXT NOT NULL DEFAULT 'queued',
                retries         INTEGER NOT NULL DEFAULT 0,
                error_detail    TEXT,
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(rule_id, user_id)
            )
        """)

        # Deleted comment_ids — so we don't DM for deleted comments
        await db.execute("""
            CREATE TABLE IF NOT EXISTS deleted_comments (
                comment_id  TEXT PRIMARY KEY,
                deleted_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        await db.commit()
        logger.info("Database initialized at %s", _db_path)


# ─── Rules ───────────────────────────────────────────────────────────────────

async def create_rule(keyword: str, dm_message: str) -> dict:
    rule_id = f"rule_{uuid.uuid4().hex[:12]}"
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            "INSERT INTO rules (rule_id, keyword, dm_message) VALUES (?, ?, ?)",
            (rule_id, keyword.upper(), dm_message),
        )
        await db.commit()
    return {"rule_id": rule_id, "keyword": keyword, "dm_message": dm_message}


async def get_all_rules() -> list[dict]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT rule_id, keyword, dm_message FROM rules")
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ─── Event deduplication ─────────────────────────────────────────────────────

async def mark_event_processed(event_id: str) -> bool:
    """
    Returns True if the event is NEW (first time seen).
    Returns False if it's a duplicate.
    Uses INSERT OR IGNORE for atomic check-and-insert.
    """
    async with aiosqlite.connect(_db_path) as db:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO processed_events (event_id) VALUES (?)",
            (event_id,),
        )
        await db.commit()
        return cursor.rowcount == 1  # 1 = newly inserted, 0 = already existed


# ─── DM send records ─────────────────────────────────────────────────────────

async def get_dm_send(rule_id: str, user_id: str) -> Optional[dict]:
    """Check if we've already sent (or are trying to send) a DM for this rule+user."""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM dm_sends WHERE rule_id = ? AND user_id = ?",
            (rule_id, user_id),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def create_dm_send(rule_id: str, user_id: str, comment_id: str) -> Optional[str]:
    """
    Create a new dm_send record.
    Returns the send_id if created, None if a record already exists (duplicate).
    """
    send_id = f"send_{uuid.uuid4().hex[:12]}"
    try:
        async with aiosqlite.connect(_db_path) as db:
            await db.execute(
                """
                INSERT OR IGNORE INTO dm_sends (id, rule_id, user_id, comment_id, status)
                VALUES (?, ?, ?, ?, 'queued')
                """,
                (send_id, rule_id, user_id, comment_id),
            )
            cursor = await db.execute(
                "SELECT id FROM dm_sends WHERE rule_id = ? AND user_id = ?",
                (rule_id, user_id),
            )
            row = await cursor.fetchone()
            await db.commit()
            if row and row[0] == send_id:
                return send_id
            return None  # Already existed
    except Exception as e:
        logger.error("create_dm_send error: %s", e)
        return None


async def update_dm_status(
    send_id: str,
    status: str,
    dm_id: Optional[str] = None,
    error_detail: Optional[str] = None,
    increment_retries: bool = False,
) -> None:
    async with aiosqlite.connect(_db_path) as db:
        if increment_retries:
            await db.execute(
                """
                UPDATE dm_sends
                SET status = ?, dm_id = ?, error_detail = ?,
                    retries = retries + 1,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (status, dm_id, error_detail, send_id),
            )
        else:
            await db.execute(
                """
                UPDATE dm_sends
                SET status = ?, dm_id = ?,  error_detail = ?,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (status, dm_id, error_detail, send_id),
            )
        await db.commit()


async def get_queued_dm_sends(limit: int = 100) -> list[dict]:
    """Get DMs that are queued and haven't exceeded max retries."""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM dm_sends
            WHERE status = 'queued' AND retries < ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (settings.max_retries, limit),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_pending_dm_sends_for_reconciliation(limit: int = 100) -> list[dict]:
    """
    Get DMs where we got a dm_id back from the API (status='sent_to_api')
    but haven't confirmed delivery yet.
    """
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM dm_sends
            WHERE status = 'sent_to_api' AND dm_id IS NOT NULL
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ─── Stats ────────────────────────────────────────────────────────────────────

async def get_stats() -> dict:
    async with aiosqlite.connect(_db_path) as db:
        # sent = DMs confirmed delivered
        c = await db.execute("SELECT COUNT(*) FROM dm_sends WHERE status = 'delivered'")
        sent = (await c.fetchone())[0]

        # failed = gave up after retries (status = 'failed')
        c = await db.execute("SELECT COUNT(*) FROM dm_sends WHERE status = 'failed'")
        failed = (await c.fetchone())[0]

        # queued = waiting to send or waiting on retry
        c = await db.execute("SELECT COUNT(*) FROM dm_sends WHERE status IN ('queued', 'sent_to_api')")
        queued = (await c.fetchone())[0]

        # duplicates_blocked = how many events we matched a rule for but already had a dm_send record
        c = await db.execute("SELECT COUNT(*) FROM dm_sends WHERE status = 'duplicate_blocked'")
        dups = (await c.fetchone())[0]

    return {
        "sent": sent,
        "failed": failed,
        "queued": queued,
        "duplicates_blocked": dups,
    }


# ─── Deleted comments ────────────────────────────────────────────────────────

async def mark_comment_deleted(comment_id: str) -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO deleted_comments (comment_id) VALUES (?)",
            (comment_id,),
        )
        await db.commit()


async def is_comment_deleted(comment_id: str) -> bool:
    async with aiosqlite.connect(_db_path) as db:
        cursor = await db.execute(
            "SELECT 1 FROM deleted_comments WHERE comment_id = ?",
            (comment_id,),
        )
        row = await cursor.fetchone()
    return row is not None


async def cancel_queued_dm_for_comment(comment_id: str) -> None:
    """If a DM for this comment is still queued, cancel it."""
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            """
            UPDATE dm_sends
            SET status = 'cancelled_deleted', updated_at = datetime('now')
            WHERE comment_id = ? AND status = 'queued'
            """,
            (comment_id,),
        )
        await db.commit()
