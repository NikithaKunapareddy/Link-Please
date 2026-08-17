"""
Quick manual webhook tester.
Run this in a second terminal while the server is running.
Usage:  python test_webhook.py
"""

import hashlib
import hmac
import json
import urllib.request

API_KEY = "bmlraXRoYTc4NjVAZ21haWwuY29t.f871a167bcb0680c425e"
BASE_URL = "http://localhost:8000"


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(
        API_KEY.encode(), body, hashlib.sha256
    ).hexdigest()


def post(path: str, payload: dict, signed: bool = True):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if signed:
        headers["X-PseudoGram-Signature"] = sign(body)
    req = urllib.request.Request(BASE_URL + path, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            print(f"  ✅ {r.status} {r.read().decode()}")
    except urllib.error.HTTPError as e:
        print(f"  ❌ {e.code} {e.read().decode()}")


def get(path: str):
    req = urllib.request.Request(BASE_URL + path)
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
        print(f"  ✅ {r.status} {json.dumps(data, indent=2)}")


print("\n── 1. Health check ──────────────────────────────")
get("/health")

print("\n── 2. Create a rule (PRICE → DM message) ────────")
post("/rules", {"keyword": "PRICE", "dm_message": "Here is our price list! 💰"}, signed=False)

print("\n── 3. Send a matching comment webhook ───────────")
post("/webhook", {
    "event_id": "evt_manual_001",
    "event_type": "comment.created",
    "sent_at": "2026-08-16T18:00:00Z",
    "data": {
        "comment_id": "cmt_manual_001",
        "text": "Hey what is the PRICE?",
        "from": {
            "user_id": "user_manual_test",
            "username": "nikitha"
        }
    }
})

print("\n── 4. Send a non-matching comment (no DM) ───────")
post("/webhook", {
    "event_id": "evt_manual_002",
    "event_type": "comment.created",
    "sent_at": "2026-08-16T18:00:00Z",
    "data": {
        "comment_id": "cmt_manual_002",
        "text": "Nice post!",
        "from": {
            "user_id": "user_manual_test2",
            "username": "randomuser"
        }
    }
})

print("\n── 5. Duplicate comment from same user ──────────")
post("/webhook", {
    "event_id": "evt_manual_003",
    "event_type": "comment.created",
    "sent_at": "2026-08-16T18:00:00Z",
    "data": {
        "comment_id": "cmt_manual_003",
        "text": "what is the PRICE again?",
        "from": {
            "user_id": "user_manual_test",   # Same user — should be BLOCKED
            "username": "nikitha"
        }
    }
})

print("\n── 6. Stats ──────────────────────────────────────")
get("/stats")

print()
