#!/usr/bin/env python3
"""
test_endpoints.py — Comprehensive endpoint testing script.
Run after starting the server: uvicorn app.main:app --port 8000

Usage:
    python3 test_endpoints.py [base_url]
    python3 test_endpoints.py http://localhost:8000
"""

import json
import sys
import time
import hmac
import hashlib
import urllib.request
import urllib.error
from typing import Any

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
PASS = "✅"
FAIL = "❌"
results = []


def request(method: str, path: str, body: Any = None, headers: dict = None) -> tuple[int, dict]:
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def check(name: str, condition: bool, detail: str = "") -> None:
    icon = PASS if condition else FAIL
    msg = f"{icon} {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    results.append((name, condition))


print(f"\n{'='*60}")
print(f"Testing {BASE_URL}")
print(f"{'='*60}\n")

# ── Health check ──────────────────────────────────────────────────
print("[ Health ]")
status, body = request("GET", "/health")
check("GET /health returns 200", status == 200)
check("GET /health has status field", "status" in body)

# ── Rules ─────────────────────────────────────────────────────────
print("\n[ POST /rules ]")
status, body = request("POST", "/rules", {"keyword": "TESTKEY", "dm_message": "Test DM"})
check("POST /rules returns 201", status == 201, f"got {status}")
check("Response has rule_id", "rule_id" in body)
check("Response has keyword", "keyword" in body and body["keyword"] == "TESTKEY")
check("Response has dm_message", "dm_message" in body)

# Empty keyword → 400
status, body = request("POST", "/rules", {"keyword": "", "dm_message": "hi"})
check("POST /rules empty keyword → 400", status == 400)

# Missing field → 422
status, body = request("POST", "/rules", {"keyword": "TESTKEY"})
check("POST /rules missing dm_message → 422", status == 422)

# ── Stats ─────────────────────────────────────────────────────────
print("\n[ GET /stats ]")
status, body = request("GET", "/stats")
check("GET /stats returns 200", status == 200, f"got {status}")
check("Stats has 'sent'", "sent" in body and isinstance(body["sent"], int))
check("Stats has 'failed'", "failed" in body and isinstance(body["failed"], int))
check("Stats has 'queued'", "queued" in body and isinstance(body["queued"], int))
check("Stats has 'duplicates_blocked'", "duplicates_blocked" in body and isinstance(body["duplicates_blocked"], int))

# ── Webhook ───────────────────────────────────────────────────────
print("\n[ POST /webhook ]")

def make_event(event_id, event_type, comment_id, user_id, text="TESTKEY hello"):
    if event_type == "comment.deleted":
        return {"event_id": event_id, "event_type": event_type, "sent_at": "now", "data": {"comment_id": comment_id}}
    return {
        "event_id": event_id, "event_type": event_type, "sent_at": "now",
        "data": {
            "comment_id": comment_id, "post_id": "post_test",
            "text": text, "created_at": "now",
            "from": {"user_id": user_id, "username": "testuser"}
        }
    }

import time
ts = int(time.time())

# Basic webhook
event1 = make_event(f"evt_test_{ts}_1", "comment.created", f"cmt_{ts}_1", f"usr_{ts}_1")
status, body = request("POST", "/webhook", event1)
check("POST /webhook returns 200", status == 200, f"got {status}")
check("Webhook response has status:ok", body.get("status") == "ok")

# Duplicate event_id (8% redelivery)
time.sleep(0.1)
status, body = request("POST", "/webhook", event1)  # same event_id
check("Duplicate event_id still returns 200", status == 200)

# Same user same rule (different comment) — should block
time.sleep(0.1)
event_dup = make_event(f"evt_test_{ts}_dup", "comment.created", f"cmt_{ts}_dup", f"usr_{ts}_1")
status, body = request("POST", "/webhook", event_dup)
check("Duplicate user+rule still returns 200", status == 200)

# comment.deleted
event_del = make_event(f"evt_del_{ts}", "comment.deleted", f"cmt_{ts}_1", "")
status, body = request("POST", "/webhook", event_del)
check("comment.deleted returns 200", status == 200)

# deleted before created (out-of-order)
future_cmt = f"cmt_future_{ts}"
event_del2 = make_event(f"evt_del_early_{ts}", "comment.deleted", future_cmt, "")
request("POST", "/webhook", event_del2)
event_created_late = make_event(f"evt_created_late_{ts}", "comment.created", future_cmt, f"usr_future_{ts}")
status, body = request("POST", "/webhook", event_created_late)
check("comment.deleted before created still returns 200", status == 200)

# Response time check
start = time.time()
request("POST", "/webhook", make_event(f"evt_timing_{ts}", "comment.created", f"cmt_timing_{ts}", f"usr_timing_{ts}"))
elapsed = time.time() - start
check(f"Webhook responds in <5 seconds ({elapsed:.3f}s)", elapsed < 5.0)

# Wait briefly and check stats updated
time.sleep(1)
status, body = request("GET", "/stats")
check("Stats still returns 200 after events", status == 200)

# ── Summary ───────────────────────────────────────────────────────
print(f"\n{'='*60}")
passed = sum(1 for _, ok in results if ok)
total = len(results)
print(f"Results: {passed}/{total} passed")
if passed == total:
    print(f"{PASS} ALL TESTS PASSED!")
else:
    failed = [name for name, ok in results if not ok]
    print(f"{FAIL} Failed: {', '.join(failed)}")
print(f"{'='*60}\n")
sys.exit(0 if passed == total else 1)
