"""
database.py — PostgreSQL persistence layer using asyncpg.

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

import asyncpg

from app.config import settings

logger = logging.getLogger(__name__)

# We'll set this pool up in main.py via init_db() and it will remain global
pool: Optional[asyncpg.Pool] = None


async def init_db() -> asyncpg.Pool:
    """Create all tables if they don't exist and return the pool."""
    global pool
    pool = await asyncpg.create_pool(
        settings.database_url,
        statement_cache_size=0,
    )
    
    async with pool.acquire() as db:
        # Rules table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS rules (
                rule_id     TEXT PRIMARY KEY,
                keyword     TEXT NOT NULL,
                dm_message  TEXT NOT NULL,
                created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Processed webhook events (dedup by event_id)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS processed_events (
                event_id   TEXT PRIMARY KEY,
                processed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
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
                created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(rule_id, user_id)
            )
        """)

        # Deleted comment_ids — so we don't DM for deleted comments
        await db.execute("""
            CREATE TABLE IF NOT EXISTS deleted_comments (
                comment_id  TEXT PRIMARY KEY,
                deleted_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        logger.info("Database initialized at %s", settings.database_url)
    return pool


async def close_db() -> None:
    if pool:
        await pool.close()

# ─── Rules ───────────────────────────────────────────────────────────────────

async def create_rule(keyword: str, dm_message: str) -> dict:
    rule_id = f"rule_{uuid.uuid4().hex[:12]}"
    async with pool.acquire() as db:
        await db.execute(
            "INSERT INTO rules (rule_id, keyword, dm_message) VALUES ($1, $2, $3)",
            rule_id, keyword.upper(), dm_message,
        )
    return {"rule_id": rule_id, "keyword": keyword, "dm_message": dm_message}


async def get_all_rules() -> list[dict]:
    async with pool.acquire() as db:
        rows = await db.fetch("SELECT rule_id, keyword, dm_message FROM rules")
    return [dict(r) for r in rows]


# ─── Event deduplication ─────────────────────────────────────────────────────

async def mark_event_processed(event_id: str) -> bool:
    """
    Returns True if the event is NEW (first time seen).
    Returns False if it's a duplicate.
    Uses INSERT ON CONFLICT DO NOTHING for atomic check-and-insert.
    """
    async with pool.acquire() as db:
        result = await db.execute(
            "INSERT INTO processed_events (event_id) VALUES ($1) ON CONFLICT DO NOTHING",
            event_id,
        )
        return result == "INSERT 0 1"


# ─── DM send records ─────────────────────────────────────────────────────────

async def get_dm_send(rule_id: str, user_id: str) -> Optional[dict]:
    """Check if we've already sent (or are trying to send) a DM for this rule+user."""
    async with pool.acquire() as db:
        row = await db.fetchrow(
            "SELECT * FROM dm_sends WHERE rule_id = $1 AND user_id = $2",
            rule_id, user_id,
        )
    return dict(row) if row else None


async def create_dm_send(rule_id: str, user_id: str, comment_id: str) -> Optional[str]:
    """
    Create a new dm_send record.
    Returns the send_id if created, None if a record already exists (duplicate).
    """
    send_id = f"send_{uuid.uuid4().hex[:12]}"
    try:
        async with pool.acquire() as db:
            result = await db.execute(
                """
                INSERT INTO dm_sends (id, rule_id, user_id, comment_id, status)
                VALUES ($1, $2, $3, $4, 'queued')
                ON CONFLICT DO NOTHING
                """,
                send_id, rule_id, user_id, comment_id,
            )
            if result == "INSERT 0 1":
                return send_id
            return None
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
    async with pool.acquire() as db:
        if increment_retries:
            await db.execute(
                """
                UPDATE dm_sends
                SET status = $1, dm_id = $2, error_detail = $3,
                    retries = retries + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = $4
                """,
                status, dm_id, error_detail, send_id,
            )
        else:
            await db.execute(
                """
                UPDATE dm_sends
                SET status = $1, dm_id = $2,  error_detail = $3,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = $4
                """,
                status, dm_id, error_detail, send_id,
            )


async def get_queued_dm_sends(limit: int = 100) -> list[dict]:
    """Get DMs that are queued and haven't exceeded max retries."""
    async with pool.acquire() as db:
        rows = await db.fetch(
            """
            SELECT * FROM dm_sends
            WHERE status = 'queued' AND retries < $1
            ORDER BY created_at ASC
            LIMIT $2
            """,
            settings.max_retries, limit,
        )
    return [dict(r) for r in rows]


async def get_pending_dm_sends_for_reconciliation(limit: int = 100) -> list[dict]:
    """
    Get DMs where we got a dm_id back from the API (status='sent_to_api')
    but haven't confirmed delivery yet.
    """
    async with pool.acquire() as db:
        rows = await db.fetch(
            """
            SELECT * FROM dm_sends
            WHERE status = 'sent_to_api' AND dm_id IS NOT NULL
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]


# ─── Stats ────────────────────────────────────────────────────────────────────

async def get_stats() -> dict:
    async with pool.acquire() as db:
        sent = await db.fetchval("SELECT COUNT(*) FROM dm_sends WHERE status = 'delivered'")
        failed = await db.fetchval("SELECT COUNT(*) FROM dm_sends WHERE status = 'failed'")
        queued = await db.fetchval("SELECT COUNT(*) FROM dm_sends WHERE status IN ('queued', 'sent_to_api')")
        dups = await db.fetchval("SELECT COUNT(*) FROM dm_sends WHERE status = 'duplicate_blocked'")

    return {
        "sent": sent,
        "failed": failed,
        "queued": queued,
        "duplicates_blocked": dups,
    }


# ─── Deleted comments ────────────────────────────────────────────────────────

async def mark_comment_deleted(comment_id: str) -> None:
    async with pool.acquire() as db:
        await db.execute(
            "INSERT INTO deleted_comments (comment_id) VALUES ($1) ON CONFLICT DO NOTHING",
            comment_id,
        )


async def is_comment_deleted(comment_id: str) -> bool:
    async with pool.acquire() as db:
        val = await db.fetchval(
            "SELECT 1 FROM deleted_comments WHERE comment_id = $1",
            comment_id,
        )
    return val is not None


async def cancel_queued_dm_for_comment(comment_id: str) -> None:
    """If a DM for this comment is still queued, cancel it."""
    async with pool.acquire() as db:
        await db.execute(
            """
            UPDATE dm_sends
            SET status = 'cancelled_deleted', updated_at = CURRENT_TIMESTAMP
            WHERE comment_id = $1 AND status = 'queued'
            """,
            comment_id,
        )
