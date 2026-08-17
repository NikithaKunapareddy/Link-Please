import urllib.request
import json
import hashlib
import hmac

API_KEY = "bmlraXRoYTc4NjVAZ21haWwuY29t.f871a167bcb0680c425e"
URL = "https://link-please-i3rq.onrender.com/webhook" # <--- YOUR RENDER DEPLOY URL

payload = json.dumps({
    "event_id": "evt_test_render_sig",
    "event_type": "comment.created",
    "data": {"comment_id": "cmt_123", "text": "PRICE", "from": {"user_id": "u1"}}
}).encode()

print(f"Testing against: {URL}\n")

print("--- 1. Testing FAKE Signature ---")
fake_sig = "sha256=wrong12345"
req1 = urllib.request.Request(URL, data=payload, headers={"Content-Type": "application/json", "X-PseudoGram-Signature": fake_sig})
try:
    with urllib.request.urlopen(req1) as r:
        print("Result: Accepted (This should NOT happen)")
except urllib.error.HTTPError as e:
    print(f"Result: Rejected with {e.code} (SUCCESS: Fake signature was blocked by Render!)")


print("\n--- 2. Testing REAL Signature ---")
real_sig = "sha256=" + hmac.new(API_KEY.encode(), payload, hashlib.sha256).hexdigest()
req2 = urllib.request.Request(URL, data=payload, headers={"Content-Type": "application/json", "X-PseudoGram-Signature": real_sig})
try:
    with urllib.request.urlopen(req2) as r:
        print(f"Result: Accepted with {r.status} (SUCCESS: Real signature accepted by Render!)")
except urllib.error.HTTPError as e:
    print(f"Result: Rejected with {e.code}")
