# Design Decisions

## Why expire_on_commit=False
SQLAlchemy by default expires all object attributes after commit, 
causing surprise SELECT queries when accessing fields post-commit.
Setting expire_on_commit=False keeps objects in memory after commit,
avoiding unnecessary queries. db.refresh() is used explicitly when
fresh database state is required (e.g. fetching auto-generated UUIDs).

## Race condition in device status updates
Device status updates have a known race condition under multi-instance
deployment. Two server instances can read the same version, both attempt
to write, and the last write silently overwrites the first.

Three mitigations considered:
1. Optimistic locking — version column, conditional write (AND version=N)
2. Pessimistic locking — SELECT FOR UPDATE, blocks concurrent reads
3. Redis TTL — separates live connection state from persistent storage,
   eliminates the race entirely by making Redis the single authority

Current single-instance deployment is not affected. Phase 2 will
introduce Redis for live device state management.

## Why Postgres instead of Redis for command queue (Phase 1)
Redis would be the production choice for command queuing. For Phase 1,
Postgres serves as the command queue via a commands table with a status
column (pending → delivered → executed → failed) and idempotency keys
to prevent duplicate execution. This keeps the dependency count low
while proving the offline resilience concept. Phase 2 will migrate
command queuing to Redis when write throughput justifies it.

## Wide-row metrics schema
Metrics use a wide-row schema (one row per device snapshot, all metrics
as columns) rather than key-value (one row per metric). At 50 devices
× 25 metrics × 6 writes/minute, key-value produces 7,500 rows/minute.
Wide-row reduces this to 300 rows/minute — a 25x reduction in write
volume and index pressure.

## Dual-mode architecture
Two monitoring modes — agentless (Paramiko SSH pull) and agent-based
(WebSocket push) — serve different operational contexts:
- Agentless: no software installed on target, works on locked-down 
  corporate VMs, higher connection overhead, blocked by NAT
- Agent: offline-resilient, scales beyond 50 nodes, works behind NAT,
  requires installation on target device
Mode is a per-device config flag, not a per-deployment decision.
