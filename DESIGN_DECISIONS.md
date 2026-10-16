# Design Decisions

## Dual-mode architecture, chosen at launch
Two monitoring modes serve different operational contexts:
- **Agentless (SSH pull):** nothing installed on the target, works on locked-down
  VMs and client machines. Costs one SSH handshake per host per poll, needs inbound
  SSH reachability (blocked by NAT), and can't collect while the server is down.
- **Agent (WebSocket push):** the device dials out, so it works behind NAT and
  firewalls; it buffers while offline; it runs on Windows, where SSH-based
  collection isn't practical. Costs an install on every device.

`FLEET_MODE` (agentless | agent | hybrid) decides which subsystems the control plane
runs: agentless mode starts the SSH poller and refuses agent registrations; agent
mode refuses SSH hosts and starts no poller. `launch.py` asks for it at startup.
Both modes feed one write path (`services/ingest.py`), so storage, alerts and the
dashboard behave identically whichever way a sample arrived.

## Cross-platform agent via psutil
The agent originally read `/proc` directly, which only works on Linux. It now uses
psutil, which reads `/proc` on Linux, Mach/sysctl APIs on macOS and Win32 APIs on
Windows — one code path for every OS. CPU and network are **rates**, so they're
computed from deltas between two readings, not from a single counter read (a single
read of `/proc/stat` gives the average since boot).

## Command delivery: at-least-once + idempotent consumer
Exactly-once delivery over an unreliable network isn't achievable in general, so:
1. A command is **persisted before** any delivery attempt (survives a server crash).
2. On every reconnect the server re-sends commands that are `pending` **or**
   `delivered` without a result (the agent may have died mid-command).
3. The agent remembers executed command ids on disk and **replays the stored result**
   instead of running a command twice.
4. Client retries are deduplicated with an `idempotency_key` backed by a UNIQUE
   constraint; a race between two identical requests is resolved by the database.

The status is written *before* sending, so a crash between "sent" and "recorded"
can't leave a delivered command marked pending — and both states are re-sent anyway.

## Database as the command queue (no Redis yet)
The `commands` table is the queue: status column + created_at ordering + idempotency
key. At this scale it's durable, transactional with the rest of the data, and one less
service to run. Redis becomes worthwhile for higher throughput and, more importantly,
for multi-instance deployments (below).

## Offline buffering on the agent
A sampler task runs continuously into a bounded `deque` (default 1000 samples).
Online, the buffer drains immediately; offline, it fills and the oldest samples are
dropped when full (bounded memory beats unbounded memory on a small device). On
reconnect it's sent as one `metrics_batch`; samples keep their original timestamps,
and only the newest sample is evaluated for alerts, so a backlog doesn't open and
resolve a burst of stale alerts.

## Wide-row metrics schema
One row per device per sample, every metric a column, versus key-value (one row per
metric). At 50 devices × 8 metrics × 6 samples/minute, key-value writes 2,400
rows/minute; wide-row writes 300 — 8× fewer rows, index entries and round trips. One
composite index `(device_id, ts)` serves both hot queries (history for one device,
latest per device); B-trees scan in either direction, so no separate DESC index.

## Alerts: one row per incident
Each sample is classified ok / warning / critical. No open alert + breach → open one;
open alert + breach → update value/severity (dedup and escalation); back to ok →
resolve. A device that stops reporting opens an "offline" alert. Next steps would be
hysteresis (resolve below a lower threshold) and "for N minutes" conditions to avoid
flapping on noisy metrics.

## Race conditions handled
- **Reconnect race:** if an agent reconnects before its old socket is noticed dead,
  the old socket's disconnect must not mark the device offline. `ConnectionManager.
  disconnect()` only acts if the closing socket is still the current one.
- **Cancellation:** the "mark offline" write on disconnect is wrapped in
  `asyncio.shield`, so a cancelled handler (shutdown, abrupt close) still finishes it.
- **Stale status after restart:** on startup every agent is marked offline (no
  sockets exist yet), and a sweeper marks devices offline/unreachable after
  `OFFLINE_AFTER_SECONDS` without data — this also catches half-open TCP connections
  that never fire a disconnect.

## Known race: multiple server instances
Live sockets live in one process's memory, and two instances updating the same device
row could overwrite each other. Not an issue for a single instance. Options considered:
1. Optimistic locking — a version column and conditional `UPDATE … WHERE version = N`
2. Pessimistic locking — `SELECT … FOR UPDATE`
3. Redis — live connection state with TTL heartbeats plus pub/sub to route a command
   to whichever instance holds the device's socket (the real fix for horizontal scale)

## Security
- First account is created freely and is always admin; afterwards only admins create
  users (open self-registration as admin would hand over remote execution on every machine).
- bcrypt password hashes; passwords limited to 72 bytes (bcrypt's input limit).
- JWT with a pinned algorithm list (blocks `alg: none` style downgrades).
- Agents authenticate with a shared token compared in constant time, sent as a
  header — never in the URL, because URLs land in access logs.
- Agentless uses SSH keys / ssh-agent only; no passwords stored. Host keys are
  trust-on-first-use by default, `SSH_STRICT_HOST_KEYS=true` for production.
- Remote commands are admin-only and record who issued them. Future: per-device
  command allow-lists and per-device agent credentials (or mTLS) instead of one shared token.

## Agentless collection details
One SSH session per poll runs one script piped to `sh -s` (works whatever the login
shell is). The script detects Linux vs macOS via `uname`. Paramiko is blocking, so
each poll runs in a worker thread (`asyncio.to_thread`) with a semaphore capping
concurrent sessions — one slow host never stalls the event loop.

## Database portability: SQLite by default, Postgres in production
SQLite makes `python launch.py` work on any machine with no setup; Postgres is the
production target. Generic `Uuid`/`JSON` types and a `UTCDateTime` decorator (SQLite
drops timezone offsets, so they're re-attached on read) keep the models identical on
both, and the test suite runs on both. SQLite uses NullPool (fresh connection per
session, WAL mode, foreign keys enabled); Postgres uses a connection pool.

## Why expire_on_commit=False
SQLAlchemy by default expires all object attributes after commit, causing surprise
SELECT queries when fields are read afterwards (and errors in async code). With
`expire_on_commit=False` objects keep their values; `db.refresh()` is used when the
database's current state is needed.
