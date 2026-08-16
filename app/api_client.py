"""
api_client.py — HTTP client for the Pseudogram mock API.

Handles:
  - Exponential backoff on 500 errors
  - Rate-limit 429 with Retry-After header
  - Idempotency-Key header on DM sends
  - GET /v1/dm/{dm_id} for status reconciliation
"""

import asyncio
import logging
import uuid
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BASE_URL = settings.mock_api_base_url

# How long to wait (seconds) before giving up completely on a single DM send attempt
_SEND_TIMEOUT = 20.0


def _headers(idempotency_key: Optional[str] = None) -> dict:
    h = {
        "X-API-Key": settings.api_key,
        "Content-Type": "application/json",
    }
    if idempotency_key:
        h["Idempotency-Key"] = idempotency_key
    return h


async def send_dm(
    recipient_user_id: str,
    message: str,
    comment_id: str,
    idempotency_key: Optional[str] = None,
    max_attempts: int = 6,
) -> tuple[Optional[str], str]:
    """
    Attempt to send a DM via the mock API.

    Returns:
        (dm_id, status) where status is one of:
          'sent_to_api'  — API returned 202, dm_id is set
          'failed'       — gave up after retries or got 400
          'rate_limited' — still rate-limited after waiting (caller should retry later)

    The function handles:
      - 500 errors with exponential backoff
      - 429 with Retry-After sleep
      - 202 = accepted (not delivered — use reconcile for that)
    """
    if not idempotency_key:
        idempotency_key = f"idem_{comment_id}_{uuid.uuid4().hex[:8]}"

    payload = {
        "recipient_user_id": recipient_user_id,
        "message": message,
        "comment_id": comment_id,
    }

    delay = 1.0  # initial backoff seconds

    async with httpx.AsyncClient(timeout=_SEND_TIMEOUT) as client:
        for attempt in range(1, max_attempts + 1):
            try:
                resp = await client.post(
                    f"{BASE_URL}/v1/dm/send",
                    json=payload,
                    headers=_headers(idempotency_key),
                )

                if resp.status_code in (200, 202):
                    data = resp.json()
                    dm_id = data.get("dm_id")
                    logger.info("DM accepted (HTTP %d): dm_id=%s for user=%s", resp.status_code, dm_id, recipient_user_id)
                    return dm_id, "sent_to_api"

                elif resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    logger.warning(
                        "Rate limited. Sleeping %ds (attempt %d/%d)",
                        retry_after, attempt, max_attempts,
                    )
                    await asyncio.sleep(retry_after)
                    # Don't count this as an attempt for backoff purposes
                    continue

                elif resp.status_code == 500:
                    logger.warning(
                        "500 from mock API (attempt %d/%d), backoff %.1fs",
                        attempt, max_attempts, delay,
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60.0)  # cap at 60s
                    continue

                elif resp.status_code == 400:
                    detail = resp.json().get("detail", "bad request")
                    logger.error("400 bad request: %s — will not retry", detail)
                    return None, "failed"

                else:
                    logger.error("Unexpected status %d — treating as transient", resp.status_code)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60.0)
                    continue

            except httpx.TimeoutException:
                logger.warning("Timeout on attempt %d/%d", attempt, max_attempts)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)
            except httpx.RequestError as exc:
                logger.warning("Network error on attempt %d/%d: %s", attempt, max_attempts, exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)

    logger.error("Gave up sending DM to user=%s after %d attempts", recipient_user_id, max_attempts)
    return None, "failed"


async def check_dm_status(dm_id: str) -> Optional[str]:
    """
    Poll GET /v1/dm/{dm_id} to check delivery status.
    Returns 'delivered', 'failed', 'queued', or None on error.
    Reads do not count against rate limit (per spec).
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{BASE_URL}/v1/dm/{dm_id}",
                headers=_headers(),
            )
            if resp.status_code == 200:
                data = resp.json()
                status = data.get("status")
                logger.debug("DM %s status = %s", dm_id, status)
                return status
            else:
                logger.warning("check_dm_status got %d for dm_id=%s", resp.status_code, dm_id)
                return None
        except Exception as exc:
            logger.warning("check_dm_status error for dm_id=%s: %s", dm_id, exc)
            return None
