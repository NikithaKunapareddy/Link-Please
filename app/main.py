"""
main.py — FastAPI application entry point.

Endpoints:
  POST /webhook  — receive comment events, return 200 fast
  POST /rules    — create keyword → DM message rules
  GET  /stats    — live stats: sent, failed, queued, duplicates_blocked
"""

import asyncio
import hashlib
import hmac
import logging
import sys

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from app import database as db
from app.config import settings
from app.models import RuleCreate, RuleResponse, StatsResponse
from app.worker import event_queue, event_worker, reconciler_worker

# ─── Logging setup ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ─── Background tasks ─────────────────────────────────────────────────────────

_background_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB and start background workers."""
    await db.init_db()
    logger.info("Starting background workers…")

    _background_tasks.append(asyncio.create_task(event_worker(), name="event_worker"))
    _background_tasks.append(asyncio.create_task(reconciler_worker(), name="reconciler_worker"))

    logger.info("Application ready. API key configured: %s", bool(settings.api_key))
    yield

    # Shutdown
    for task in _background_tasks:
        task.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    logger.info("Background workers stopped")


# ─── FastAPI app ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="LinkPlease nik — Instagram DM Automation",
    description="Receives Instagram comment webhooks and auto-sends DMs based on keyword rules.",
    version="1.0.0",
    lifespan=lifespan,
)


# ─── Signature verification helper ────────────────────────────────────────────

def _verify_signature(body: bytes, signature_header: str) -> bool:
    """
    Part B: Verify HMAC-SHA256 signature.
    Header format: sha256=<hex>
    Secret: your API key.
    """
    if not settings.api_key:
        # No API key configured — skip verification (dev mode)
        return True

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected_sig = signature_header[len("sha256="):]
    computed = hmac.new(
        settings.api_key.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed, expected_sig)


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.post("/webhook", status_code=200)
async def receive_webhook(request: Request) -> JSONResponse:
    """
    Receive comment events from the Pseudogram mock API.

    MUST return 200 within 5 seconds. All actual work is done asynchronously.
    """
    raw_body = await request.body()

    # ── Part B: Signature verification ─────────────────────────────────────
    sig_header = request.headers.get("X-PseudoGram-Signature", "")
    if settings.api_key and not _verify_signature(raw_body, sig_header):
        logger.warning("Webhook signature verification FAILED — rejecting request")
        raise HTTPException(status_code=401, detail="Invalid signature")

    # ── Parse JSON body ─────────────────────────────────────────────────────
    try:
        import json
        payload = json.loads(raw_body)
    except Exception:
        logger.warning("Webhook received non-JSON body — ignoring")
        return JSONResponse(content={"status": "ok"}, status_code=200)

    # ── Enqueue for background processing (non-blocking) ────────────────────
    try:
        event_queue.put_nowait(payload)
    except asyncio.QueueFull:
        # Queue is full — log and still return 200 (don't block the sender)
        logger.error("Event queue is FULL — dropping event_id=%s", payload.get("event_id"))

    return JSONResponse(content={"status": "ok"}, status_code=200)


@app.post("/rules", status_code=201)
async def create_rule(rule: RuleCreate) -> RuleResponse:
    """
    Create a new keyword → DM message rule.

    Keyword matching is case-insensitive and matches anywhere in comment text.
    """
    if not rule.keyword or not rule.keyword.strip():
        raise HTTPException(status_code=400, detail="keyword must not be empty")
    if not rule.dm_message or not rule.dm_message.strip():
        raise HTTPException(status_code=400, detail="dm_message must not be empty")

    result = await db.create_rule(
        keyword=rule.keyword.strip(),
        dm_message=rule.dm_message.strip(),
    )
    return RuleResponse(**result)


@app.get("/stats")
async def get_stats() -> StatsResponse:
    """
    Return live stats:
      - sent: DMs confirmed delivered
      - failed: gave up after retries
      - queued: waiting to send or pending retry
      - duplicates_blocked: DMs correctly not sent (same user, same rule)
    """
    stats = await db.get_stats()
    return StatsResponse(**stats)


# ─── Health check ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check() -> dict:
    return {"status": "healthy", "api_key_set": bool(settings.api_key)}


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )
