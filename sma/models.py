"""Domain enums and records — Architecture Baseline v1.1."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


def new_id(prefix: str = "") -> str:
    raw = uuid4().hex
    return f"{prefix}{raw}" if prefix else raw


class Mode(str, Enum):
    MANAGER = "manager"
    TEAM_LEADER = "team_leader"
    DEVELOPER = "developer"


class PlanStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    WAITING_APPROVAL = "waiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class TaskStatus(str, Enum):
    DRAFT = "draft"
    QUEUED = "queued"
    BLOCKED = "blocked"
    RUNNING = "running"
    PAUSED = "paused"
    REVIEWING = "reviewing"
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkerStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    WAITING = "waiting"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    LOST = "lost"


class ExecutorType(str, Enum):
    OPENCODE = "opencode"
    CURSOR = "cursor"


@dataclass
class Project:
    id: str
    name: str
    repo_path: str
    created_at: str


@dataclass
class Session:
    id: str
    project_id: str
    mode: Mode
    title: str
    created_at: str


@dataclass
class Plan:
    id: str
    session_id: str
    status: PlanStatus
    title: str
    body_md: str
    created_at: str
    updated_at: str


@dataclass
class Task:
    id: str
    session_id: str
    plan_id: str | None
    title: str
    status: TaskStatus
    dispatchable: bool
    dispatch_key: str
    attempt: int
    lease_owner: str | None
    lease_expires_at: str | None
    executor_type: ExecutorType
    prompt: str
    created_at: str
    updated_at: str
    confirmed: bool = False  # developer mode


@dataclass
class Worker:
    id: str
    task_id: str
    executor_type: ExecutorType
    executor_run_id: str | None
    pid: int | None
    session_id: str | None  # opencode server session
    agent_id: str | None
    run_id: str | None
    status: WorkerStatus
    started_at: str
    heartbeat_at: str
    attempt: int
    exit_reason: str | None = None


@dataclass
class Event:
    id: int | None
    session_id: str
    task_id: str | None
    worker_id: str | None
    seq: int
    type: str
    payload: dict[str, Any]
    created_at: str


@dataclass
class AcceptanceEvidence:
    id: str
    task_id: str
    worker_id: str | None
    attempt: int
    requirement_snapshot: str
    plan_snapshot: str
    git_base_sha: str | None
    git_head_sha: str | None
    diff_hash: str | None
    deterministic_results: dict[str, Any]
    policy_results: dict[str, Any]
    reviewer_result: dict[str, Any]
    reviewer_model: str | None
    accepted_at: str
    final_status: str  # passed | failed | blocked

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutorCapabilities:
    cancel: bool = True
    pause: bool = False
    inject: bool = False
    resume: bool = False
    diff: bool = True


@dataclass
class FailureEnvelope:
    type: str = "worker_failed"
    worker_id: str = ""
    task_id: str = ""
    executor: str = ""
    reason: str = ""
    exit_code: int | None = None
    retryable: bool = False
    summary: str = ""
    log_ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
