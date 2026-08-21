# Architecture Baseline v1.1

Status: **canonical**. Implement against this document; do not reinvent worker/state/dispatch/acceptance.

## Product

```text
StrawManagerAgent ≠ Coding Agent
StrawManagerAgent = Orchestrator / Policy / State / Acceptance Layer
```

Executors (OpenCode, Cursor, future others) are pluggable via `ExecutorRegistry`.

## Hard rules

1. Sole spawn path: `DispatchService.spawn()` with mode invariant + `dispatch_key` idempotency + lease.
2. `RecoveryService` turns expired leases into `BLOCKED` + retryable (worker `LOST`).
3. PASS = Deterministic ∧ Diff Policy ∧ LLM Reviewer, persisted as `AcceptanceEvidence`.
4. Task status ≠ Worker status.
5. Events: SQLite durable + `SecretRedactor` + SSE `Last-Event-ID`.
6. Memory: SQLite canonical; `MEMORY.md` / `USER.md` are projections.
7. Network: `CloudflareTunnel` | `CustomDNS` | `LAN_mDNS` (not DHCP).
8. OpenCode `ServerRunner` is v1.x, not MVP blocker.

## Modes

- **Manager** — no clarifying questions; auto-approved plan; dispatchable immediately.
- **Team Leader** — `waiting_approval` until human approve.
- **Developer** — task must be confirmed before dispatchable.
