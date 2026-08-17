# FAILURES.md — Known Failure Modes

This documents every way the system can still lose a DM, send a duplicate, or report a wrong number.

---

## 1. In-memory queue loss on restart

The `event_queue` is an `asyncio.Queue` held in memory. If the process restarts (crash, deploy, OOM kill) while events are sitting in that queue and haven't been processed yet, those events are gone. Nothing on disk knows they were pending. The database only gets written *after* `_process_event()` runs, so any events between "received by webhook" and "written to DB" are silently lost.

**Condition:** Process restart while events are in flight or in the queue.
**Mitigation:** A persistent queue (Redis, RabbitMQ) would fix this, but was not implemented.

---

## 2. Duplicate statistics can become inaccurate under extreme concurrency

The user+rule deduplication is protected by a PostgreSQL `UNIQUE(rule_id, user_id)` constraint, so the actual DM should only be sent once. However, the duplicate-blocked statistics are recorded separately from the deduplication operation. Under extreme concurrent webhook delivery, the duplicate counter and the deduplication record are not updated as one atomic transaction, so the `duplicates_blocked` statistic could potentially become inaccurate.

**Condition:** Multiple duplicate events for the same user+rule arriving concurrently.
**Impact:** The actual DM remains protected by the UNIQUE constraint, but `duplicates_blocked` could be inaccurate.

---

## 3. `comment.deleted` arriving after DM is already sent_to_api

If a `comment.deleted` event arrives after the DM has already been dispatched to the mock API (status = `sent_to_api`), we cannot recall that DM. We correctly cancel `queued` DMs, but there is a window between "DM sent to API" and "status updated in DB" where a deletion event would not cancel anything. The user gets the DM even though the comment was deleted.

**Condition:** `comment.deleted` arrives while DM is in-flight at the API level.
**Frequency:** Proportional to API latency and delete event timing.

---

## 4. Unbounded Database Growth and Cleanup

The `processed_events` and `dm_sends` tables grow indefinitely as new events arrive. Since there is no cleanup mechanism to prune old completed records, the PostgreSQL database size will eventually degrade database performance over millions of comments, potentially causing I/O bottlenecks and write locks during high concurrency.

**Condition:** Running continuously at high volume without manual or automated log rotation.
**Impact:** Gradual degradation of throughput over months.

---

## 5. Memory Queue Overflow During Extreme Spikes

The `event_queue` in `worker.py` is initialized with `maxsize=10_000`. If a sudden, extreme burst of traffic arrives (e.g., a massive viral event) and the background workers are rate-limited or backed up such that the queue reaches capacity, `event_queue.put_nowait()` will raise `asyncio.QueueFull`. The webhook endpoint currently catches this, logs an error, and returns a `200 OK`, silently dropping the webhook event. 

**Condition:** Queue reaches capacity during a massive burst (>10,000 events before workers can drain).
**Impact:** Comment events are silently lost, and no DM will be queued or sent for those events.
