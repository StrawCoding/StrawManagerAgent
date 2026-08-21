"""DispatchService — sole spawn entry with idempotency + lease."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sma.db import Store, utcnow
from sma.mode_policy import ModePolicy, ModePolicyError
from sma.models import Task, TaskStatus, Worker, WorkerStatus, new_id


class DispatchError(Exception):
    pass


class DispatchService:
    def __init__(
        self,
        store: Store,
        policy: ModePolicy,
        *,
        orchestrator_id: str = "local",
        lease_ttl_seconds: int = 120,
    ) -> None:
        self.store = store
        self.policy = policy
        self.orchestrator_id = orchestrator_id
        self.lease_ttl_seconds = lease_ttl_seconds

    def _lease_expiry(self) -> str:
        return (
            datetime.now(timezone.utc) + timedelta(seconds=self.lease_ttl_seconds)
        ).replace(microsecond=0).isoformat()

    def spawn(self, task_id: str, *, dispatch_key: str | None = None) -> Worker:
        """
        Unique spawn path. Transaction:
          confirm dispatchable → acquire lease → create Worker → Task RUNNING
        Idempotent on dispatch_key while lease held / task already running.
        """
        with self.store.transaction() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise DispatchError(f"task not found: {task_id}")
            task = Store._task(row)

            # Idempotency: if already running with active lease for same key, return existing worker
            key = dispatch_key or task.dispatch_key
            if dispatch_key and dispatch_key != task.dispatch_key:
                # Client-supplied key must match task key for MVP
                raise DispatchError("dispatch_key mismatch")

            if task.status == TaskStatus.RUNNING and task.lease_owner and task.lease_expires_at:
                if task.lease_expires_at >= utcnow():
                    workers = [
                        Store._worker(r)
                        for r in conn.execute(
                            "SELECT * FROM workers WHERE task_id=? AND status IN (?,?) "
                            "ORDER BY started_at DESC",
                            (task.id, WorkerStatus.STARTING.value, WorkerStatus.RUNNING.value),
                        ).fetchall()
                    ]
                    if workers:
                        return workers[0]

            # Mode invariant (uses store reads; OK outside locked row for plan checks)
            try:
                # Reload via store mapper fields already have task
                self.policy.assert_spawn_allowed(task)
            except ModePolicyError as e:
                raise DispatchError(str(e)) from e

            if task.status not in (TaskStatus.QUEUED, TaskStatus.BLOCKED):
                raise DispatchError(f"cannot spawn from status {task.status}")

            now = utcnow()
            attempt = task.attempt + 1
            worker = Worker(
                id=new_id("wkr_"),
                task_id=task.id,
                executor_type=task.executor_type,
                executor_run_id=None,
                pid=None,
                session_id=None,
                agent_id=None,
                run_id=None,
                status=WorkerStatus.STARTING,
                started_at=now,
                heartbeat_at=now,
                attempt=attempt,
            )
            self.store.create_worker(worker, conn=conn)

            task.attempt = attempt
            task.status = TaskStatus.RUNNING
            task.lease_owner = self.orchestrator_id
            task.lease_expires_at = self._lease_expiry()
            task.dispatchable = False  # held by lease; not freely re-dispatchable
            task.updated_at = now
            self.store.save_task(task, conn=conn)

            conn.execute(
                "INSERT INTO events(session_id, task_id, worker_id, seq, type, payload_json, created_at) "
                "VALUES (?,?,?,(SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE session_id=?),?,?,?)",
                (
                    task.session_id,
                    task.id,
                    worker.id,
                    task.session_id,
                    "worker.spawned",
                    __import__("json").dumps(
                        {
                            "worker_id": worker.id,
                            "task_id": task.id,
                            "attempt": attempt,
                            "executor": task.executor_type.value,
                            "dispatch_key": key,
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
            return worker

    def heartbeat(self, task_id: str, worker_id: str) -> None:
        now = utcnow()
        with self.store.transaction() as conn:
            task_row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task_row is None:
                raise DispatchError("task not found")
            task = Store._task(task_row)
            if task.lease_owner != self.orchestrator_id:
                raise DispatchError("not lease owner")
            task.lease_expires_at = self._lease_expiry()
            task.updated_at = now
            self.store.save_task(task, conn=conn)
            wrow = conn.execute("SELECT * FROM workers WHERE id=?", (worker_id,)).fetchone()
            if wrow:
                worker = Store._worker(wrow)
                worker.heartbeat_at = now
                if worker.status == WorkerStatus.STARTING:
                    worker.status = WorkerStatus.RUNNING
                self.store.save_worker(worker, conn=conn)

    def release_for_retry(self, task_id: str, *, new_dispatch_key: bool = True) -> Task:
        """After recovery or failed attempt, make task re-dispatchable."""
        task = self.store.get_task(task_id)
        if task is None:
            raise DispatchError("task not found")
        now = utcnow()
        task.status = TaskStatus.QUEUED
        task.dispatchable = True
        task.lease_owner = None
        task.lease_expires_at = None
        if new_dispatch_key:
            task.dispatch_key = new_id("dk_")
        task.updated_at = now
        return self.store.save_task(task)
