import urllib.request, json, time

# Step 1: Wake the server up
print("Waking up Render server...")
for i in range(3):
    req = urllib.request.Request("https://link-please-i3rq.onrender.com/stats")
    with urllib.request.urlopen(req) as r:
        print(f"  ping {i+1}:", r.read().decode())
    time.sleep(1)

# Step 2: Start simulation
print("Starting simulation (500 events)...")
body = json.dumps({
    "webhook_url": "https://link-please-i3rq.onrender.com/webhook",
    "count": 500,
    "duration_seconds": 10
}).encode()
req = urllib.request.Request(
    "https://pseudogram-api.onrender.com/v1/simulate/start",
    data=body,
    headers={"Content-Type":"application/json","X-API-Key":"bmlraXRoYTc4NjVAZ21haWwuY29t.f871a167bcb0680c425e"}
)
with urllib.request.urlopen(req) as r:
    result = json.loads(r.read())
    print("Simulation started:", result)
    run_id = result["run_id"]

# Step 3: Wait
print(f"Waiting 35s for run_id={run_id}...")
time.sleep(35)

# Step 4: Get truth
req = urllib.request.Request(
    f"https://pseudogram-api.onrender.com/v1/simulate/{run_id}/truth",
    headers={"X-API-Key":"bmlraXRoYTc4NjVAZ21haWwuY29t.f871a167bcb0680c425e"}
)
with urllib.request.urlopen(req) as r:
    truth = json.loads(r.read())

# Step 5: Get stats
req2 = urllib.request.Request("https://link-please-i3rq.onrender.com/stats")
with urllib.request.urlopen(req2) as r:
    stats = json.loads(r.read())

print()
print("=== RESULTS ===")
print(f"Total events sent by PseudoGram : {truth['total_events_generated']}")
print(f"Webhooks returned 200           : {truth['webhook_200_count']}")
print(f"Expected unique DM recipients   : {truth['expected_unique_recipient_count']}")
print(f"Our stats - sent                : {stats['sent']}")
print(f"Our stats - failed              : {stats['failed']}")
print(f"Our stats - queued              : {stats['queued']}")
print(f"Our stats - duplicates_blocked  : {stats['duplicates_blocked']}")
