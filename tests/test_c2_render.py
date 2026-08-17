import urllib.request
import json
import hashlib
import hmac

API_KEY = "bmlraXRoYTc4NjVAZ21haWwuY29t.f871a167bcb0680c425e"
URL = "https://link-please-i3rq.onrender.com/webhook"

payload = json.dumps({
    "event_id": "evt_test_render_deleted",
    "event_type": "comment.deleted",
    "data": {"comment_id": "cmt_deleted_123"}
}).encode()

real_sig = "sha256=" + hmac.new(API_KEY.encode(), payload, hashlib.sha256).hexdigest()
req = urllib.request.Request(URL, data=payload, headers={"Content-Type": "application/json", "X-PseudoGram-Signature": real_sig})

print(f"Testing comment.deleted against: {URL}\n")
try:
    with urllib.request.urlopen(req) as r:
        print(f"Result: {r.status} {r.read().decode()} (SUCCESS: Render correctly accepted the deletion event!)")
except urllib.error.HTTPError as e:
    print(f"Result: Rejected with {e.code}")
