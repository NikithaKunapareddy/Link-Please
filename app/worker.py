"""
worker.py — Background processing for comment events and DM sending.

Two async tasks run in the background:
  1. event_worker        — drains the in-memory event queue, matches rules, schedules DMs
  2. reconciler_worker   — polls sent DMs for delivery confirmation, retries failed ones

DM send flow:
  webhook → event_queue → event_worker → (dedup check) → send_dm() → reconciler_worker

Status lifecycle for dm_sends:
  queued → sent_to_api → delivered  (happy path)
                       → failed     (reconciler confirmed failure, max retries exceeded)
  queued → failed                   (send_dm gave up after retries)
  queued → cancelled_deleted        (comment.deleted arrived before send)
  --- (separate rows) ---
  duplicate_blocked                  (user already targeted by this rule)
"""

import asyncio
import logging

from app import database as db
from app.api_client import check_dm_status, send_dm
from app.config import settings

logger = logging.getLogger(__name__)

# In-memory async queue for incoming events (allows /webhook to return 200 fast)
event_queue: asyncio.Queue = asyncio.Queue(maxsize=10_000)

# Semaphore to stay within 10 req/60s rate limit
# We allow up to 8 concurrent sends to leave headroom
_send_semaphore = asyncio.Semaphore(3)


async def _process_event(event: dict) -> None:
    """
    Process a single webhook event:
      - Skip duplicate event_ids
      - Handle comment.deleted
      - Match against rules (case-insensitive)
      - Check user+rule dedup
      - Dispatch DM sends
    """
    event_id = event.get("event_id", "")
    event_type = event.get("event_type", "")
    data = event.get("data", {})
    comment_id = data.get("comment_id", "")

    # ── Deduplicate event_id (handles ~8% redelivery) ──────────────────────
    is_new = await db.mark_event_processed(event_id)
    if not is_new:
        logger.debug("Duplicate event_id=%s — skipping", event_id)
        return

    # ── Handle comment.deleted ──────────────────────────────────────────────
    if event_type == "comment.deleted":
        logger.info("comment.deleted for comment_id=%s", comment_id)
        await db.mark_comment_deleted(comment_id)
        # Cancel any queued DM that was waiting to be sent for this comment
        await db.cancel_queued_dm_for_comment(comment_id)
        return

    if event_type != "comment.created":
        logger.debug("Unknown event_type=%s — ignoring", event_type)
        return

    # ── Extract comment fields ───────────────────────────────────────────────
    comment_text = data.get("text", "") or ""
    from_data = data.get("from", {}) or {}
    user_id = from_data.get("user_id", "")
    username = from_data.get("username", "")

    if not comment_id or not user_id:
        logger.warning("Malformed event: missing comment_id or user_id — event_id=%s", event_id)
        return

    # ── Check if comment was already deleted (arrived before created) ────────
    if await db.is_comment_deleted(comment_id):
        logger.info("Comment %s was already deleted — not sending DM", comment_id)
        return

    # ── Match rules ───────────────────────────────────────────────────────────
    rules = await db.get_all_rules()
    comment_upper = comment_text.upper()

    for rule in rules:
        keyword = rule["keyword"].upper()  # Already stored uppercase, but defensive
        if keyword not in comment_upper:
            continue

        rule_id = rule["rule_id"]
        dm_message = rule["dm_message"]

        # ── User+Rule dedup ─────────────────────────────────────────────────
        # create_dm_send uses INSERT OR IGNORE — returns None if already exists
        send_id = await db.create_dm_send(rule_id, user_id, comment_id)

        if send_id is None:
            # Already sent (or sending) for this rule+user pair
            # The webhook handler already incremented the in-memory counter;
            # just persist a record to DB for durability.
            logger.debug(
                "Duplicate blocked (worker): rule=%s user=%s comment=%s",
                rule_id, user_id, comment_id,
            )
            await _record_duplicate_blocked(rule_id, user_id, comment_id)
            continue

        # ── Fire DM send asynchronously ────────────────────────────────────
        asyncio.create_task(
            _send_dm_with_tracking(send_id, user_id, dm_message, comment_id, rule_id)
        )


async def _record_duplicate_blocked(rule_id: str, user_id: str, comment_id: str) -> None:
    """
    Record a blocked duplicate for stats.
    We insert a separate row with a unique (rule_id+dup suffix, user_id+comment_id combo)
    so that GET /stats can COUNT them via status='duplicate_blocked'.
    The UNIQUE constraint is on (rule_id, user_id) — we bypass it by making the
    dup row use a distinct composite key.
    """
    import uuid
    from app import database as db
    dup_id = f"dup_{uuid.uuid4().hex[:12]}"
    # Use comment_id as part of the user_id key to allow multiple dup records per rule+user
    dup_user_key = f"{user_id}:dup:{comment_id}"
    async with db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO dm_sends (id, rule_id, user_id, comment_id, status)
            VALUES ($1, $2, $3, $4, 'duplicate_blocked')
            ON CONFLICT DO NOTHING
            """,
            dup_id, rule_id, dup_user_key, comment_id,
        )


async def _send_dm_with_tracking(
    send_id: str,
    user_id: str,
    dm_message: str,
    comment_id: str,
    rule_id: str,
    retry_count: int = 0,
) -> None:
    """
    Send a DM and update the tracking record.
    Uses semaphore to cap concurrent sends and stay under rate limit.
    """
    async with _send_semaphore:
        # Check again if comment was deleted while we were waiting
        if await db.is_comment_deleted(comment_id):
            logger.info("Comment %s deleted before DM send — cancelling send_id=%s", comment_id, send_id)
            await db.update_dm_status(send_id, "cancelled_deleted")
            return

        dm_id, status = await send_dm(
            recipient_user_id=user_id,
            message=dm_message,
            comment_id=comment_id,
            idempotency_key=f"idem_{send_id}_{retry_count}",
            max_attempts=settings.max_retries,
        )

        await db.update_dm_status(
            send_id=send_id,
            status=status,
            dm_id=dm_id,
            increment_retries=(status == "failed"),
        )
        logger.info(
            "DM send complete: send_id=%s dm_id=%s status=%s user=%s",
            send_id, dm_id, status, user_id,
        )


async def event_worker() -> None:
    """Continuously drain the event queue and process events."""
    logger.info("Event worker started")
    while True:
        try:
            event = await event_queue.get()
            asyncio.create_task(_process_event(event))
            event_queue.task_done()
        except asyncio.CancelledError:
            logger.info("Event worker cancelled")
            break
        except Exception as exc:
            logger.exception("Event worker unexpected error: %s", exc)


async def _get_rule_message(rule_id: str) -> str:
    """Look up the dm_message for a rule, returning empty string if not found."""
    rules = await db.get_all_rules()
    for rule in rules:
        if rule["rule_id"] == rule_id:
            return rule["dm_message"]
    return ""


async def reconciler_worker() -> None:
    """
    Periodically check DMs that were accepted (sent_to_api) but not yet confirmed.
    ~15% will fail silently — we catch those and mark them failed.
    If they're still 'queued' in the API, we leave them; if 'failed', we retry.
    """
    logger.info("Reconciler worker started")
    while True:
        try:
            await asyncio.sleep(settings.reconciler_interval_seconds)
            pending = await db.get_pending_dm_sends_for_reconciliation()

            for record in pending:
                dm_id = record["dm_id"]
                send_id = record["id"]

                api_status = await check_dm_status(dm_id)

                if api_status == "delivered":
                    await db.update_dm_status(send_id, "delivered", dm_id=dm_id)
                    logger.info("Reconciler: dm_id=%s DELIVERED", dm_id)

                elif api_status == "failed":
                    retries = record.get("retries", 0)
                    if retries < settings.max_retries:
                        # Look up the DM message for this rule
                        dm_message = await _get_rule_message(record["rule_id"])
                        # Reset to queued with incremented retry count
                        await db.update_dm_status(
                            send_id, "queued", dm_id=None,
                            error_detail="failed after delivery check",
                            increment_retries=True,
                        )
                        # Re-queue for sending with the correct message
                        asyncio.create_task(
                            _send_dm_with_tracking(
                                send_id=send_id,
                                user_id=record["user_id"],
                                dm_message=dm_message,
                                comment_id=record["comment_id"],
                                rule_id=record["rule_id"],
                                retry_count=retries + 1,
                            )
                        )
                        logger.info(
                            "Reconciler: dm_id=%s FAILED — re-queuing with correct message (retry %d)",
                            dm_id, retries + 1,
                        )
                    else:
                        await db.update_dm_status(
                            send_id, "failed", dm_id=dm_id,
                            error_detail="failed after max reconcile retries",
                        )
                        logger.warning("Reconciler: dm_id=%s FAILED permanently", dm_id)

                # If api_status == 'queued', still in flight — leave it

        except asyncio.CancelledError:
            logger.info("Reconciler worker cancelled")
            break
        except Exception as exc:
            logger.exception("Reconciler unexpected error: %s", exc)
