"""SQLite store — canonical persistence for SMA domain."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from sma.models import (
    AcceptanceEvidence,
    ExecutorType,
    Mode,
    Plan,
    PlanStatus,
    Project,
    Session,
    Task,
    TaskStatus,
    Worker,
    WorkerStatus,
)
from sma.paths import db_path, ensure_home


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  repo_path TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  mode TEXT NOT NULL,
  title TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  status TEXT NOT NULL,
  title TEXT NOT NULL,
  body_md TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  plan_id TEXT,
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  dispatchable INTEGER NOT NULL DEFAULT 0,
  dispatch_key TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 0,
  lease_owner TEXT,
  lease_expires_at TEXT,
  executor_type TEXT NOT NULL,
  prompt TEXT NOT NULL,
  confirmed INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(dispatch_key)
);

CREATE TABLE IF NOT EXISTS workers (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  executor_type TEXT NOT NULL,
  executor_run_id TEXT,
  pid INTEGER,
  opencode_session_id TEXT,
  agent_id TEXT,
  run_id TEXT,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  exit_reason TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  task_id TEXT,
  worker_id TEXT,
  seq INTEGER NOT NULL,
  type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_items (
  id TEXT PRIMARY KEY,
  namespace TEXT NOT NULL,
  key TEXT NOT NULL,
  content TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(namespace, key)
);

CREATE TABLE IF NOT EXISTS acceptance_evidence (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  worker_id TEXT,
  attempt INTEGER NOT NULL,
  requirement_snapshot TEXT NOT NULL,
  plan_snapshot TEXT NOT NULL,
  git_base_sha TEXT,
  git_head_sha TEXT,
  diff_hash TEXT,
  deterministic_results_json TEXT NOT NULL,
  policy_results_json TEXT NOT NULL,
  reviewer_result_json TEXT NOT NULL,
  reviewer_model TEXT,
  accepted_at TEXT NOT NULL,
  final_status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, id);
CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id);
CREATE INDEX IF NOT EXISTS idx_workers_task ON workers(task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_lease ON tasks(lease_expires_at);
"""


class Store:
    def __init__(self, root: Path | None = None) -> None:
        ensure_home(root)
        self.path = db_path(root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # --- projects / sessions / plans ---

    def create_project(self, project: Project) -> Project:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO projects(id, name, repo_path, created_at) VALUES (?,?,?,?)",
                (project.id, project.name, project.repo_path, project.created_at),
            )
        return project

    def get_project(self, project_id: str) -> Project | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return self._project(row) if row else None

    def create_session(self, session: Session) -> Session:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO sessions(id, project_id, mode, title, created_at) VALUES (?,?,?,?,?)",
                (session.id, session.project_id, session.mode.value, session.title, session.created_at),
            )
        return session

    def get_session(self, session_id: str) -> Session | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return self._session(row) if row else None

    def create_plan(self, plan: Plan) -> Plan:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO plans(id, session_id, status, title, body_md, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    plan.id,
                    plan.session_id,
                    plan.status.value,
                    plan.title,
                    plan.body_md,
                    plan.created_at,
                    plan.updated_at,
                ),
            )
        return plan

    def get_plan(self, plan_id: str) -> Plan | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        return self._plan(row) if row else None

    def update_plan_status(self, plan_id: str, status: PlanStatus) -> Plan:
        now = utcnow()
        with self.transaction() as conn:
            conn.execute(
                "UPDATE plans SET status=?, updated_at=? WHERE id=?",
                (status.value, now, plan_id),
            )
        plan = self.get_plan(plan_id)
        assert plan is not None
        return plan

    # --- tasks ---

    def create_task(self, task: Task) -> Task:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO tasks(id, session_id, plan_id, title, status, dispatchable, "
                "dispatch_key, attempt, lease_owner, lease_expires_at, executor_type, prompt, "
                "confirmed, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task.id,
                    task.session_id,
                    task.plan_id,
                    task.title,
                    task.status.value,
                    1 if task.dispatchable else 0,
                    task.dispatch_key,
                    task.attempt,
                    task.lease_owner,
                    task.lease_expires_at,
                    task.executor_type.value,
                    task.prompt,
                    1 if task.confirmed else 0,
                    task.created_at,
                    task.updated_at,
                ),
            )
        return task

    def get_task(self, task_id: str) -> Task | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task(row) if row else None

    def get_task_by_dispatch_key(self, dispatch_key: str, conn: sqlite3.Connection | None = None) -> Task | None:
        if conn is None:
            with self._connect() as c:
                row = c.execute("SELECT * FROM tasks WHERE dispatch_key=?", (dispatch_key,)).fetchone()
            return self._task(row) if row else None
        row = conn.execute("SELECT * FROM tasks WHERE dispatch_key=?", (dispatch_key,)).fetchone()
        return self._task(row) if row else None

    def list_tasks(self, session_id: str) -> list[Task]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE session_id=? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [self._task(r) for r in rows]

    def save_task(self, task: Task, conn: sqlite3.Connection | None = None) -> Task:
        params = (
            task.status.value,
            1 if task.dispatchable else 0,
            task.dispatch_key,
            task.attempt,
            task.lease_owner,
            task.lease_expires_at,
            task.executor_type.value,
            task.prompt,
            1 if task.confirmed else 0,
            task.updated_at,
            task.id,
        )
        sql = (
            "UPDATE tasks SET status=?, dispatchable=?, dispatch_key=?, attempt=?, "
            "lease_owner=?, lease_expires_at=?, executor_type=?, prompt=?, confirmed=?, "
            "updated_at=? WHERE id=?"
        )
        if conn is not None:
            conn.execute(sql, params)
            return task
        with self.transaction() as c:
            c.execute(sql, params)
        return task

    def list_expired_leases(self, now: str) -> list[Task]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status=? AND lease_expires_at IS NOT NULL "
                "AND lease_expires_at < ?",
                (TaskStatus.RUNNING.value, now),
            ).fetchall()
        return [self._task(r) for r in rows]

    # --- workers ---

    def create_worker(self, worker: Worker, conn: sqlite3.Connection | None = None) -> Worker:
        params = (
            worker.id,
            worker.task_id,
            worker.executor_type.value,
            worker.executor_run_id,
            worker.pid,
            worker.session_id,
            worker.agent_id,
            worker.run_id,
            worker.status.value,
            worker.started_at,
            worker.heartbeat_at,
            worker.attempt,
            worker.exit_reason,
        )
        sql = (
            "INSERT INTO workers(id, task_id, executor_type, executor_run_id, pid, "
            "opencode_session_id, agent_id, run_id, status, started_at, heartbeat_at, "
            "attempt, exit_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
        )
        if conn is not None:
            conn.execute(sql, params)
            return worker
        with self.transaction() as c:
            c.execute(sql, params)
        return worker

    def get_worker(self, worker_id: str) -> Worker | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM workers WHERE id=?", (worker_id,)).fetchone()
        return self._worker(row) if row else None

    def list_workers_for_task(self, task_id: str) -> list[Worker]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workers WHERE task_id=? ORDER BY started_at",
                (task_id,),
            ).fetchall()
        return [self._worker(r) for r in rows]

    def save_worker(self, worker: Worker, conn: sqlite3.Connection | None = None) -> Worker:
        params = (
            worker.executor_run_id,
            worker.pid,
            worker.session_id,
            worker.agent_id,
            worker.run_id,
            worker.status.value,
            worker.heartbeat_at,
            worker.exit_reason,
            worker.id,
        )
        sql = (
            "UPDATE workers SET executor_run_id=?, pid=?, opencode_session_id=?, agent_id=?, "
            "run_id=?, status=?, heartbeat_at=?, exit_reason=? WHERE id=?"
        )
        if conn is not None:
            conn.execute(sql, params)
            return worker
        with self.transaction() as c:
            c.execute(sql, params)
        return worker

    # --- events ---

    def append_event(
        self,
        session_id: str,
        type_: str,
        payload: dict[str, Any],
        *,
        task_id: str | None = None,
        worker_id: str | None = None,
        created_at: str | None = None,
    ) -> int:
        created = created_at or utcnow()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS m FROM events WHERE session_id=?",
                (session_id,),
            ).fetchone()
            seq = int(row["m"]) + 1
            cur = conn.execute(
                "INSERT INTO events(session_id, task_id, worker_id, seq, type, payload_json, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (session_id, task_id, worker_id, seq, type_, json.dumps(payload, ensure_ascii=False), created),
            )
            return int(cur.lastrowid)

    def events_after(self, session_id: str, after_id: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE session_id=? AND id>? ORDER BY id LIMIT ?",
                (session_id, after_id, limit),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "session_id": r["session_id"],
                    "task_id": r["task_id"],
                    "worker_id": r["worker_id"],
                    "seq": r["seq"],
                    "type": r["type"],
                    "payload": json.loads(r["payload_json"]),
                    "created_at": r["created_at"],
                }
            )
        return out

    # --- memory ---

    def memory_list(self, namespace: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_items WHERE namespace=? ORDER BY updated_at",
                (namespace,),
            ).fetchall()
        return [dict(r) for r in rows]

    def memory_add(self, namespace: str, key: str, content: str, source: str = "user") -> dict[str, Any]:
        from sma.models import new_id

        now = utcnow()
        item_id = new_id("mem_")
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO memory_items(id, namespace, key, content, version, source, created_at, updated_at) "
                "VALUES (?,?,?,?,1,?,?,?)",
                (item_id, namespace, key, content, source, now, now),
            )
        return {
            "id": item_id,
            "namespace": namespace,
            "key": key,
            "content": content,
            "version": 1,
            "source": source,
        }

    def memory_replace(self, namespace: str, key: str, content: str, expected_version: int | None = None) -> dict[str, Any]:
        now = utcnow()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM memory_items WHERE namespace=? AND key=?",
                (namespace, key),
            ).fetchone()
            if row is None:
                raise KeyError(f"memory key not found: {namespace}/{key}")
            if expected_version is not None and int(row["version"]) != expected_version:
                raise ConflictError(f"version mismatch: have {row['version']} want {expected_version}")
            new_ver = int(row["version"]) + 1
            conn.execute(
                "UPDATE memory_items SET content=?, version=?, updated_at=? WHERE id=?",
                (content, new_ver, now, row["id"]),
            )
        return {"id": row["id"], "namespace": namespace, "key": key, "content": content, "version": new_ver}

    def memory_remove(self, namespace: str, key: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM memory_items WHERE namespace=? AND key=?", (namespace, key))

    # --- acceptance ---

    def save_acceptance(self, ev: AcceptanceEvidence) -> AcceptanceEvidence:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO acceptance_evidence(id, task_id, worker_id, attempt, requirement_snapshot, "
                "plan_snapshot, git_base_sha, git_head_sha, diff_hash, deterministic_results_json, "
                "policy_results_json, reviewer_result_json, reviewer_model, accepted_at, final_status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ev.id,
                    ev.task_id,
                    ev.worker_id,
                    ev.attempt,
                    ev.requirement_snapshot,
                    ev.plan_snapshot,
                    ev.git_base_sha,
                    ev.git_head_sha,
                    ev.diff_hash,
                    json.dumps(ev.deterministic_results, ensure_ascii=False),
                    json.dumps(ev.policy_results, ensure_ascii=False),
                    json.dumps(ev.reviewer_result, ensure_ascii=False),
                    ev.reviewer_model,
                    ev.accepted_at,
                    ev.final_status,
                ),
            )
        return ev

    def get_acceptance_for_task(self, task_id: str) -> AcceptanceEvidence | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM acceptance_evidence WHERE task_id=? ORDER BY accepted_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        if not row:
            return None
        return AcceptanceEvidence(
            id=row["id"],
            task_id=row["task_id"],
            worker_id=row["worker_id"],
            attempt=row["attempt"],
            requirement_snapshot=row["requirement_snapshot"],
            plan_snapshot=row["plan_snapshot"],
            git_base_sha=row["git_base_sha"],
            git_head_sha=row["git_head_sha"],
            diff_hash=row["diff_hash"],
            deterministic_results=json.loads(row["deterministic_results_json"]),
            policy_results=json.loads(row["policy_results_json"]),
            reviewer_result=json.loads(row["reviewer_result_json"]),
            reviewer_model=row["reviewer_model"],
            accepted_at=row["accepted_at"],
            final_status=row["final_status"],
        )

    # --- mappers ---

    @staticmethod
    def _project(row: sqlite3.Row) -> Project:
        return Project(id=row["id"], name=row["name"], repo_path=row["repo_path"], created_at=row["created_at"])

    @staticmethod
    def _session(row: sqlite3.Row) -> Session:
        return Session(
            id=row["id"],
            project_id=row["project_id"],
            mode=Mode(row["mode"]),
            title=row["title"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _plan(row: sqlite3.Row) -> Plan:
        return Plan(
            id=row["id"],
            session_id=row["session_id"],
            status=PlanStatus(row["status"]),
            title=row["title"],
            body_md=row["body_md"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _task(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            session_id=row["session_id"],
            plan_id=row["plan_id"],
            title=row["title"],
            status=TaskStatus(row["status"]),
            dispatchable=bool(row["dispatchable"]),
            dispatch_key=row["dispatch_key"],
            attempt=int(row["attempt"]),
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
            executor_type=ExecutorType(row["executor_type"]),
            prompt=row["prompt"],
            confirmed=bool(row["confirmed"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _worker(row: sqlite3.Row) -> Worker:
        return Worker(
            id=row["id"],
            task_id=row["task_id"],
            executor_type=ExecutorType(row["executor_type"]),
            executor_run_id=row["executor_run_id"],
            pid=row["pid"],
            session_id=row["opencode_session_id"],
            agent_id=row["agent_id"],
            run_id=row["run_id"],
            status=WorkerStatus(row["status"]),
            started_at=row["started_at"],
            heartbeat_at=row["heartbeat_at"],
            attempt=int(row["attempt"]),
            exit_reason=row["exit_reason"],
        )


class ConflictError(Exception):
    pass
