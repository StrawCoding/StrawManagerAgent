"""RecoveryService — lease expiry → BLOCKED / RETRYABLE."""

from __future__ import annotations

from sma.db import Store, utcnow
from sma.models import TaskStatus, WorkerStatus


class RecoveryService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def recover_expired_leases(self) -> list[str]:
        """
        Find RUNNING tasks with expired lease.
        Mark active workers LOST, task BLOCKED (retryable via dispatchable flag).
        Returns recovered task ids.
        """
        now = utcnow()
        recovered: list[str] = []
        for task in self.store.list_expired_leases(now):
            with self.store.transaction() as conn:
                # Re-check under transaction
                row = conn.execute("SELECT * FROM tasks WHERE id=?", (task.id,)).fetchone()
                if row is None:
                    continue
                t = Store._task(row)
                if t.status != TaskStatus.RUNNING:
                    continue
                if not t.lease_expires_at or t.lease_expires_at >= now:
                    continue
                for wrow in conn.execute(
                    "SELECT * FROM workers WHERE task_id=? AND status IN (?,?)",
                    (t.id, WorkerStatus.STARTING.value, WorkerStatus.RUNNING.value),
                ).fetchall():
                    w = Store._worker(wrow)
                    w.status = WorkerStatus.LOST
                    w.exit_reason = "lease_expired"
                    w.heartbeat_at = now
                    self.store.save_worker(w, conn=conn)

                t.status = TaskStatus.BLOCKED
                t.dispatchable = True  # retryable
                t.lease_owner = None
                t.lease_expires_at = None
                t.updated_at = now
                self.store.save_task(t, conn=conn)

                conn.execute(
                    "INSERT INTO events(session_id, task_id, worker_id, seq, type, payload_json, created_at) "
                    "VALUES (?,?,NULL,(SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE session_id=?),?,?,?)",
                    (
                        t.session_id,
                        t.id,
                        t.session_id,
                        "worker.lease_expired",
                        __import__("json").dumps(
                            {"task_id": t.id, "reason": "lease_expired", "retryable": True},
                            ensure_ascii=False,
                        ),
                        now,
                    ),
                )
                recovered.append(t.id)
        return recovered
