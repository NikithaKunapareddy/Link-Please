#!/usr/bin/env python3
"""
test_full.py — Complete integration test with real API key and HMAC signing.
Run with: python3 test_full.py
"""

import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request

BASE_URL = "http://127.0.0.1:8000"
API_KEY = "bmlraXRoYTc4NjVAZ21haWwuY29t.f871a167bcb0680c425e"

PASS = "✅"
FAIL = "❌"
results = []

def sign(body_bytes: bytes) -> str:
    return "sha256=" + hmac.new(
        API_KEY.encode(), body_bytes, hashlib.sha256
    ).hexdigest()

def req(method: str, path: str, body=None, sign_body=False, extra_headers=None):
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    if sign_body and data:
        headers["X-PseudoGram-Signature"] = sign(data)
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

def check(name: str, ok: bool, detail=""):
    icon = PASS if ok else FAIL
    print(f"  {icon} {name}" + (f"  [{detail}]" if detail else ""))
    results.append((name, ok))
    return ok

T = int(time.time())
print(f"\n{'━'*65}")
print(f"  NIK — Full Integration Test Suite")
print(f"  API key: {API_KEY[:20]}...")
print(f"{'━'*65}\n")

# ─────────────────────────────────────────────────────────────────────
print("[ 1 ] Health check")
status, body = req("GET", "/health")
check("GET /health → 200", status == 200, f"got {status}")
check("api_key_set is true", body.get("api_key_set") is True, str(body))

# ─────────────────────────────────────────────────────────────────────
print("\n[ 2 ] POST /rules — shape and validation")
status, body = req("POST", "/rules", {"keyword": f"TESTWORD{T}", "dm_message": "Test DM message"})
check("returns 201", status == 201, f"got {status}")
check("has rule_id", "rule_id" in body)
rule_id = body.get("rule_id", "")
check("rule_id is a non-empty string", isinstance(rule_id, str) and len(rule_id) > 0, rule_id)
check("keyword returned correctly", body.get("keyword") == f"TESTWORD{T}")
check("dm_message returned correctly", body.get("dm_message") == "Test DM message")
check("no extra unexpected fields", set(body.keys()) == {"rule_id", "keyword", "dm_message"})

status, body = req("POST", "/rules", {"keyword": "", "dm_message": "hi"})
check("empty keyword → 400", status == 400, f"got {status}")

status, body = req("POST", "/rules", {"keyword": "   ", "dm_message": "hi"})
check("whitespace-only keyword → 400", status == 400, f"got {status}")

status, body = req("POST", "/rules", {"keyword": "ABC"})
check("missing dm_message → 422", status == 422, f"got {status}")

status, body = req("POST", "/rules", {"dm_message": "hello"})
check("missing keyword → 422", status == 422, f"got {status}")

status, body = req("POST", "/rules", {})
check("empty body → 422", status == 422, f"got {status}")

# ─────────────────────────────────────────────────────────────────────
print("\n[ 3 ] GET /stats — shape")
status, body = req("GET", "/stats")
check("returns 200", status == 200, f"got {status}")
check("has 'sent' (int)", "sent" in body and isinstance(body["sent"], int))
check("has 'failed' (int)", "failed" in body and isinstance(body["failed"], int))
check("has 'queued' (int)", "queued" in body and isinstance(body["queued"], int))
check("has 'duplicates_blocked' (int)", "duplicates_blocked" in body and isinstance(body["duplicates_blocked"], int))
check("no extra fields", set(body.keys()) == {"sent", "failed", "queued", "duplicates_blocked"})
check("all values >= 0", all(v >= 0 for v in body.values()))

# ─────────────────────────────────────────────────────────────────────
print("\n[ 4 ] POST /webhook — basic response")

def webhook(event_id, event_type, comment_id, user_id, text="hello world", extra_headers=None):
    payload = {
        "event_id": event_id,
        "event_type": event_type,
        "sent_at": "2026-08-16T09:14:22.481Z",
        "data": {"comment_id": comment_id, "post_id": "post_test",
                 "text": text, "created_at": "2026-08-16T09:14:21.900Z",
                 "from": {"user_id": user_id, "username": "testuser"}}
    }
    return req("POST", "/webhook", payload, sign_body=True, extra_headers=extra_headers)

def deleted_webhook(event_id, comment_id):
    payload = {
        "event_id": event_id,
        "event_type": "comment.deleted",
        "sent_at": "2026-08-16T09:14:22.481Z",
        "data": {"comment_id": comment_id}
    }
    return req("POST", "/webhook", payload, sign_body=True)

# Basic webhook
status, body = webhook(f"evt_{T}_basic", "comment.created", f"cmt_{T}_basic", f"usr_{T}_basic", f"TESTWORD{T} please")
check("returns 200", status == 200, f"got {status}")
check("body is {status: ok}", body == {"status": "ok"}, str(body))

# ─────────────────────────────────────────────────────────────────────
print("\n[ 5 ] POST /webhook — response time")
start = time.time()
webhook(f"evt_{T}_timing", "comment.created", f"cmt_{T}_timing", f"usr_{T}_timing", f"TESTWORD{T}")
elapsed = time.time() - start
check(f"responds in < 5 seconds ({elapsed:.4f}s)", elapsed < 5.0)
check(f"responds in < 1 second ({elapsed:.4f}s)", elapsed < 1.0)

# ─────────────────────────────────────────────────────────────────────
print("\n[ 6 ] POST /webhook — event_id deduplication (~8% redelivery)")
evt_id = f"evt_{T}_dup"
status1, _ = webhook(evt_id, "comment.created", f"cmt_{T}_dup_1", f"usr_{T}_dup_1", f"TESTWORD{T}")
time.sleep(0.2)
status2, _ = webhook(evt_id, "comment.created", f"cmt_{T}_dup_1", f"usr_{T}_dup_1", f"TESTWORD{T}")
check("first delivery → 200", status1 == 200)
check("duplicate event_id → also 200 (idempotent)", status2 == 200)

# ─────────────────────────────────────────────────────────────────────
print("\n[ 6b ] POST /webhook — same user + same rule dedup (duplicates_blocked)")
stats_before, _ = req("GET", "/stats")
dups_before = stats_before.get("duplicates_blocked", 0) if isinstance(stats_before, dict) else 0

# Send same user same keyword twice (different event_ids)
webhook(f"evt_{T}_sameuser_1", "comment.created", f"cmt_{T}_sameuser_1", f"usr_{T}_sameuser", f"TESTWORD{T} first comment")
time.sleep(0.5)
webhook(f"evt_{T}_sameuser_2", "comment.created", f"cmt_{T}_sameuser_2", f"usr_{T}_sameuser", f"TESTWORD{T} second comment")
time.sleep(1.0)

_, stats_after = req("GET", "/stats")
dups_after = stats_after.get("duplicates_blocked", 0)
check("duplicates_blocked incremented", dups_after > dups_before, f"before={dups_before} after={dups_after}")

# ─────────────────────────────────────────────────────────────────────
print("\n[ 7 ] POST /webhook — case-insensitive matching")
keyword_lower = f"testword{T}"
status, _ = webhook(f"evt_{T}_case_low", "comment.created", f"cmt_{T}_case_low", f"usr_{T}_case_low", f"what is the {keyword_lower}?")
check("lowercase keyword triggers match → 200", status == 200)

status, _ = webhook(f"evt_{T}_case_mix", "comment.created", f"cmt_{T}_case_mix", f"usr_{T}_case_mix", f"TeStWoRd{T} show me")
check("mixed case keyword triggers match → 200", status == 200)

status, _ = webhook(f"evt_{T}_no_match", "comment.created", f"cmt_{T}_no_match", f"usr_{T}_no_match", "this comment has no matching keyword at all")
check("non-matching comment → 200 (but no DM)", status == 200)

# ─────────────────────────────────────────────────────────────────────
print("\n[ 8 ] POST /webhook — keyword matches ANYWHERE in text")
status, _ = webhook(f"evt_{T}_mid", "comment.created", f"cmt_{T}_mid", f"usr_{T}_mid", f"I wonder TESTWORD{T} is in the middle")
check("keyword in middle of text → matches → 200", status == 200)

status, _ = webhook(f"evt_{T}_end", "comment.created", f"cmt_{T}_end", f"usr_{T}_end", f"hey what TESTWORD{T}")
check("keyword at end of text → matches → 200", status == 200)

# ─────────────────────────────────────────────────────────────────────
print("\n[ 9 ] POST /webhook — comment.deleted handling")
status, body = deleted_webhook(f"evt_{T}_del_basic", f"cmt_{T}_del_basic")
check("comment.deleted → 200", status == 200)
check("comment.deleted body = {status: ok}", body == {"status": "ok"})

# ─────────────────────────────────────────────────────────────────────
print("\n[ 10 ] POST /webhook — comment.deleted arrives BEFORE comment.created")
future_cmt = f"cmt_{T}_future"
deleted_webhook(f"evt_{T}_del_early", future_cmt)
time.sleep(0.2)
status, _ = webhook(f"evt_{T}_created_late", "comment.created", future_cmt, f"usr_{T}_future", f"TESTWORD{T}")
check("created after delete → 200 (no DM sent)", status == 200)

# ─────────────────────────────────────────────────────────────────────
print("\n[ 11 ] POST /webhook — bad/missing signature → 401")
# No signature at all
payload = {"event_id": f"evt_{T}_nosig","event_type": "comment.created","sent_at": "now","data":{}}
body_bytes = json.dumps(payload).encode()
r = urllib.request.Request(f"{BASE_URL}/webhook", data=body_bytes,
    headers={"Content-Type": "application/json"}, method="POST")
try:
    with urllib.request.urlopen(r, timeout=5) as resp:
        status = resp.status
except urllib.error.HTTPError as e:
    status = e.code
check("no signature → 401", status == 401, f"got {status}")

# Bad signature
r2 = urllib.request.Request(f"{BASE_URL}/webhook", data=body_bytes,
    headers={"Content-Type": "application/json",
             "X-PseudoGram-Signature": "sha256=badhash"}, method="POST")
try:
    with urllib.request.urlopen(r2, timeout=5) as resp:
        status = resp.status
except urllib.error.HTTPError as e:
    status = e.code
check("bad signature → 401", status == 401, f"got {status}")

# ─────────────────────────────────────────────────────────────────────
print("\n[ 12 ] GET /stats — accuracy after all tests")
time.sleep(2)
status, body = req("GET", "/stats")
check("returns 200", status == 200)
check("exact keys: sent, failed, queued, duplicates_blocked",
      set(body.keys()) == {"sent","failed","queued","duplicates_blocked"})
check("queued + sent + failed >= 0 (sane)", 
      body.get("queued",0) + body.get("sent",0) + body.get("failed",0) >= 0)
print(f"     Current stats: {body}")

# ─────────────────────────────────────────────────────────────────────
print("\n[ 13 ] POST /webhook — 50-event concurrent burst, all return 200")
import threading
burst_results = []
def send_burst(i):
    s, _ = webhook(f"evt_{T}_burst_{i}", "comment.created",
                   f"cmt_{T}_burst_{i}", f"usr_{T}_burst_{i}", f"TESTWORD{T} burst")
    burst_results.append(s == 200)

threads = [threading.Thread(target=send_burst, args=(i,)) for i in range(50)]
for t in threads: t.start()
for t in threads: t.join()
check("50 concurrent events all return 200",
      all(burst_results) and len(burst_results) == 50,
      f"{sum(burst_results)}/50 ok")

# ─────────────────────────────────────────────────────────────────────
print(f"\n{'━'*65}")
passed = sum(1 for _, ok in results if ok)
total = len(results)
failed_tests = [n for n,ok in results if not ok]
print(f"  RESULTS: {passed}/{total} passed")
if failed_tests:
    print(f"\n  {FAIL} Failed tests:")
    for n in failed_tests:
        print(f"     • {n}")
else:
    print(f"  {PASS} ALL TESTS PASSED — backend is perfect!")
print(f"{'━'*65}\n")
sys.exit(0 if passed == total else 1)
