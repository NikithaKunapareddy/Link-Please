# FAILURES.md — Known Failure Modes

This documents every way the system can still lose a DM, send a duplicate, or report a wrong number.

---

## 1. In-memory queue loss on restart

The `event_queue` is an `asyncio.Queue` held in memory. If the process restarts (crash, deploy, OOM kill) while events are sitting in that queue and haven't been processed yet, those events are gone. Nothing on disk knows they were pending. The database only gets written *after* `_process_event()` runs, so any events between "received by webhook" and "written to DB" are silently lost.

**Condition:** Process restart while events are in flight or in the queue.
**Mitigation:** A persistent queue (Redis, RabbitMQ) would fix this, but was not implemented.

---

## 2. Race condition in duplicate blocking under extreme concurrency

The user+rule dedup uses SQLite `INSERT OR IGNORE` on a `UNIQUE(rule_id, user_id)` constraint, which is atomic. However, the `duplicate_blocked` counter is tracked by inserting a *separate row* with a `dup_` prefix rule_id. If two events for the same user+rule arrive within ~1–2ms and both pass the first `INSERT OR IGNORE` check before either commits the `duplicate_blocked` row, both could increment the counter. The **actual DM** is still only sent once (the UNIQUE constraint prevents that), but the `duplicates_blocked` count could be off by one in this scenario.

**Condition:** Same user+rule event arriving within ~1–2ms, extremely tight concurrency.
**Frequency:** Rare during normal operation; possible under 500-event burst.

---

## 3. `comment.deleted` arriving after DM is already sent_to_api

If a `comment.deleted` event arrives after the DM has already been dispatched to the mock API (status = `sent_to_api`), we cannot recall that DM. We correctly cancel `queued` DMs, but there is a window between "DM sent to API" and "status updated in DB" where a deletion event would not cancel anything. The user gets the DM even though the comment was deleted.

**Condition:** `comment.deleted` arrives while DM is in-flight at the API level.
**Frequency:** Proportional to API latency and delete event timing.

---

## 4. Unbounded Database Growth and Cleanup

The `processed_events` and `dm_sends` tables grow indefinitely as new events arrive. Since there is no cleanup mechanism to prune old completed records, the SQLite file size will eventually degrade database performance over millions of comments, potentially causing I/O bottlenecks and write locks during high concurrency.

**Condition:** Running continuously at high volume without manual or automated log rotation.
**Impact:** Gradual degradation of throughput over months.
