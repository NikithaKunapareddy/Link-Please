# LinkPlease Tech Intern Assignment

Instagram DM automation — receives comment webhooks, matches keyword rules, sends DMs.

## Stack

**Python 3.12+ · FastAPI · SQLite (aiosqlite) · httpx**

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set API_KEY=your_actual_api_key
```

## Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/webhook` | Receive comment events (returns 200 in <5s) |
| `POST` | `/rules` | Create keyword → DM message rule |
| `GET` | `/stats` | Live stats |
| `GET` | `/health` | Health check |

## Parts Completed

- **Part A** ✅ — Rules, webhook processing, DM sending, deduplication, retry on failure
- **Part B** ✅ — Webhook signature verification (HMAC-SHA256), accurate stats under load
- **Part C** ✅ — Delivery reconciliation, `comment.deleted` handling, 500-event burst

## Architecture

```
POST /webhook → asyncio.Queue → event_worker (background)
                                     ↓
                              rule matching (case-insensitive)
                                     ↓
                              user+rule dedup (SQLite UNIQUE)
                                     ↓
                              send_dm() with retry/backoff
                                     ↓
                              reconciler_worker polls delivery status
```

See [FAILURES.md](./FAILURES.md) for known failure modes.
