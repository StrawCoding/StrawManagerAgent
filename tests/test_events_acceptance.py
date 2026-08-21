"""Additional unit tests: events, acceptance evidence, redactor, memory."""

from __future__ import annotations

from pathlib import Path

from sma.acceptance import AcceptanceGate, GateInput
from sma.db import Store, utcnow
from sma.events import EventBus
from sma.memory import MemoryStore
from sma.mode_policy import ModePolicy
from sma.models import Mode, Project, Session, new_id
from sma.redactor import SecretRedactor
from sma.reports import render_acceptance_html


def test_event_bus_durable_and_redacted(tmp_path: Path) -> None:
    store = Store(root=tmp_path / "h")
    bus = EventBus(store)
    eid = bus.publish("ses1", {"type": "log", "msg": "CLOUDFLARE_API_TOKEN=abc123secret"})
    assert eid >= 1
    events = bus.since("ses1", 0)
    assert len(events) == 1
    assert "abc123secret" not in str(events[0]["payload"])
    assert "***REDACTED***" in str(events[0]["payload"])


def test_last_event_id_resume(tmp_path: Path) -> None:
    store = Store(root=tmp_path / "h")
    bus = EventBus(store)
    a = bus.publish("s", {"type": "a"})
    b = bus.publish("s", {"type": "b"})
    assert bus.since("s", a)[0]["id"] == b


def test_acceptance_evidence_persisted(tmp_path: Path) -> None:
    home = tmp_path / "h"
    store = Store(root=home)
    policy = ModePolicy(store)
    now = utcnow()
    proj = store.create_project(Project(id=new_id("p"), name="d", repo_path=str(tmp_path), created_at=now))
    ses = store.create_session(
        Session(id=new_id("s"), project_id=proj.id, mode=Mode.MANAGER, title="t", created_at=now)
    )
    task = policy.create_task(ses.id, "t", "req")
    from sma.dispatch import DispatchService

    DispatchService(store, policy).spawn(task.id)
    task = store.get_task(task.id)
    assert task is not None
    gate = AcceptanceGate(store)
    ev = gate.evaluate(
        GateInput(
            task=task,
            worker_id=None,
            repo_path=str(tmp_path),
            requirement_snapshot="req",
            plan_snapshot="plan",
            changed_files=["src/a.py"],
            forbidden_prefixes=[],
            required_files=[],
            test_commands=[["true"]],
        )
    )
    assert ev.final_status == "passed"
    loaded = store.get_acceptance_for_task(task.id)
    assert loaded is not None
    assert loaded.id == ev.id
    path = render_acceptance_html(ev, out_dir=home / "reports")
    assert path.exists()
    assert "Acceptance Evidence" in path.read_text(encoding="utf-8")


def test_acceptance_fails_on_forbidden_path(tmp_path: Path) -> None:
    store = Store(root=tmp_path / "h")
    policy = ModePolicy(store)
    now = utcnow()
    proj = store.create_project(Project(id=new_id("p"), name="d", repo_path=str(tmp_path), created_at=now))
    ses = store.create_session(
        Session(id=new_id("s"), project_id=proj.id, mode=Mode.MANAGER, title="t", created_at=now)
    )
    task = policy.create_task(ses.id, "t", "req")
    from sma.dispatch import DispatchService

    DispatchService(store, policy).spawn(task.id)
    task = store.get_task(task.id)
    assert task is not None
    ev = AcceptanceGate(store).evaluate(
        GateInput(
            task=task,
            worker_id=None,
            repo_path=str(tmp_path),
            requirement_snapshot="req",
            plan_snapshot="plan",
            changed_files=["secrets/key.pem"],
            forbidden_prefixes=["secrets/"],
            required_files=[],
            test_commands=[["true"]],
            reviewer=lambda _: {"approve": True},
            reviewer_model="test",
        )
    )
    assert ev.final_status == "failed"
    assert ev.policy_results["pass"] is False


def test_memory_projection(tmp_path: Path) -> None:
    home = tmp_path / "h"
    store = Store(root=home)
    mem = MemoryStore(store, root=home)
    mem.add("memory", "fact1", "SMA uses SQLite canonical memory")
    path = home / "memory" / "MEMORY.md"
    assert path.exists()
    assert "SQLite canonical" in path.read_text(encoding="utf-8")


def test_redactor_patterns() -> None:
    r = SecretRedactor()
    assert "***REDACTED***" in r.redact_text("api_key=sk-live-123")
