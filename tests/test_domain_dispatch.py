"""Phase 1 domain + mode + dispatch + lease tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sma.db import Store, utcnow
from sma.dispatch import DispatchError, DispatchService
from sma.mode_policy import ModePolicy, ModePolicyError
from sma.models import Mode, PlanStatus, Project, Session, TaskStatus, WorkerStatus, new_id
from sma.recovery import RecoveryService


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(root=tmp_path / "sma-home")


@pytest.fixture
def session_factory(store: Store):
    def _make(mode: Mode) -> Session:
        now = utcnow()
        proj = store.create_project(
            Project(id=new_id("prj_"), name="demo", repo_path="/tmp/demo", created_at=now)
        )
        return store.create_session(
            Session(
                id=new_id("ses_"),
                project_id=proj.id,
                mode=mode,
                title="t",
                created_at=now,
            )
        )

    return _make


def test_team_leader_cannot_spawn_before_approve(store: Store, session_factory) -> None:
    policy = ModePolicy(store)
    dispatch = DispatchService(store, policy, lease_ttl_seconds=60)
    ses = session_factory(Mode.TEAM_LEADER)
    plan = policy.create_plan_for_request(ses.id, "OAuth", "## plan", Mode.TEAM_LEADER)
    assert plan.status == PlanStatus.WAITING_APPROVAL
    task = policy.create_task(ses.id, "impl", "do oauth", plan_id=plan.id)
    assert task.dispatchable is False
    with pytest.raises(DispatchError, match="not dispatchable|not approved"):
        dispatch.spawn(task.id)


def test_team_leader_spawn_after_approve(store: Store, session_factory) -> None:
    policy = ModePolicy(store)
    dispatch = DispatchService(store, policy)
    ses = session_factory(Mode.TEAM_LEADER)
    plan = policy.create_plan_for_request(ses.id, "OAuth", "## plan", Mode.TEAM_LEADER)
    task = policy.create_task(ses.id, "impl", "do oauth", plan_id=plan.id)
    policy.approve_plan(plan.id)
    task = store.get_task(task.id)
    assert task is not None and task.dispatchable is True
    worker = dispatch.spawn(task.id)
    assert worker.status == WorkerStatus.STARTING
    task2 = store.get_task(task.id)
    assert task2 is not None
    assert task2.status == TaskStatus.RUNNING
    assert task2.attempt == 1
    assert task2.lease_owner == "local"


def test_manager_auto_dispatchable(store: Store, session_factory) -> None:
    policy = ModePolicy(store)
    dispatch = DispatchService(store, policy)
    ses = session_factory(Mode.MANAGER)
    plan = policy.create_plan_for_request(ses.id, "fix login", "## p", Mode.MANAGER)
    assert plan.status == PlanStatus.APPROVED
    task = policy.create_task(ses.id, "fix", "fix it", plan_id=plan.id)
    assert task.dispatchable is True
    w = dispatch.spawn(task.id)
    assert w.task_id == task.id


def test_developer_requires_confirm(store: Store, session_factory) -> None:
    policy = ModePolicy(store)
    dispatch = DispatchService(store, policy)
    ses = session_factory(Mode.DEVELOPER)
    task = policy.create_task(ses.id, "t", "do", confirmed=False)
    assert task.dispatchable is False
    with pytest.raises(DispatchError):
        dispatch.spawn(task.id)
    policy.confirm_task(task.id)
    w = dispatch.spawn(task.id)
    assert w.attempt == 1


def test_spawn_idempotent_while_lease_held(store: Store, session_factory) -> None:
    policy = ModePolicy(store)
    dispatch = DispatchService(store, policy, lease_ttl_seconds=300)
    ses = session_factory(Mode.MANAGER)
    task = policy.create_task(ses.id, "t", "do")
    w1 = dispatch.spawn(task.id)
    w2 = dispatch.spawn(task.id)
    assert w1.id == w2.id
    workers = store.list_workers_for_task(task.id)
    starting_or_running = [
        w for w in workers if w.status in (WorkerStatus.STARTING, WorkerStatus.RUNNING)
    ]
    assert len(starting_or_running) == 1


def test_lease_expiry_recovery(store: Store, session_factory) -> None:
    policy = ModePolicy(store)
    dispatch = DispatchService(store, policy, lease_ttl_seconds=1)
    recovery = RecoveryService(store)
    ses = session_factory(Mode.MANAGER)
    task = policy.create_task(ses.id, "t", "do")
    worker = dispatch.spawn(task.id)

    # Force expire lease
    t = store.get_task(task.id)
    assert t is not None
    past = (datetime.now(timezone.utc) - timedelta(seconds=30)).replace(microsecond=0).isoformat()
    t.lease_expires_at = past
    t.updated_at = utcnow()
    store.save_task(t)

    recovered = recovery.recover_expired_leases()
    assert task.id in recovered
    t2 = store.get_task(task.id)
    assert t2 is not None
    assert t2.status == TaskStatus.BLOCKED
    assert t2.dispatchable is True
    w = store.get_worker(worker.id)
    assert w is not None
    assert w.status == WorkerStatus.LOST

    # Can respawn after release
    dispatch.release_for_retry(task.id)
    w3 = dispatch.spawn(task.id)
    assert w3.attempt == 2
    assert w3.id != worker.id


def test_approve_wrong_mode(store: Store, session_factory) -> None:
    policy = ModePolicy(store)
    ses = session_factory(Mode.MANAGER)
    plan = policy.create_plan_for_request(ses.id, "x", "y", Mode.MANAGER)
    with pytest.raises(ModePolicyError):
        policy.approve_plan(plan.id)
