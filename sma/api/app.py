"""FastAPI orchestrator surface."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sma.acceptance import AcceptanceGate, GateInput
from sma.db import Store, utcnow
from sma.dispatch import DispatchError, DispatchService
from sma.events import EventBus
from sma.memory import MemoryStore
from sma.mode_policy import ModePolicy, ModePolicyError
from sma.models import ExecutorType, Mode, Project, Session, new_id
from sma.network import NetworkConfig, describe_setup, load_network_config, save_network_config
from sma.paths import ensure_home
from sma.recovery import RecoveryService
from sma.reports import render_acceptance_html
from sma.workers import WorkerController


class CreateProjectIn(BaseModel):
    name: str
    repo_path: str


class CreateSessionIn(BaseModel):
    project_id: str
    mode: Mode
    title: str = "session"


class PlanIn(BaseModel):
    title: str
    body_md: str


class TaskIn(BaseModel):
    title: str
    prompt: str
    plan_id: str | None = None
    executor: ExecutorType = ExecutorType.OPENCODE
    confirmed: bool = False


class NetworkIn(BaseModel):
    mode: str = "lan_mdns"
    bind: str = "0.0.0.0"
    port: int = 8741
    public_hostname: str | None = None
    cloudflare_tunnel_name: str | None = None


class AcceptIn(BaseModel):
    changed_files: list[str] = Field(default_factory=list)
    forbidden_prefixes: list[str] = Field(default_factory=list)
    required_files: list[str] = Field(default_factory=list)
    test_commands: list[list[str]] = Field(default_factory=lambda: [["true"]])


def create_app(root: Path | None = None) -> FastAPI:
    ensure_home(root)
    store = Store(root=root)
    policy = ModePolicy(store)
    dispatch = DispatchService(store, policy)
    events = EventBus(store)
    recovery = RecoveryService(store)
    memory = MemoryStore(store, root=root)
    workers = WorkerController(store, dispatch, events)
    gate = AcceptanceGate(store)

    app = FastAPI(title="StrawManagerAgent", version="0.1.0.1")
    app.state.store = store
    app.state.root = root

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/projects")
    def create_project(body: CreateProjectIn) -> dict[str, Any]:
        p = store.create_project(
            Project(id=new_id("prj_"), name=body.name, repo_path=body.repo_path, created_at=utcnow())
        )
        return {"id": p.id, "name": p.name, "repo_path": p.repo_path}

    @app.post("/sessions")
    def create_session(body: CreateSessionIn) -> dict[str, Any]:
        if not store.get_project(body.project_id):
            raise HTTPException(404, "project not found")
        s = store.create_session(
            Session(
                id=new_id("ses_"),
                project_id=body.project_id,
                mode=body.mode,
                title=body.title,
                created_at=utcnow(),
            )
        )
        return {"id": s.id, "mode": s.mode.value, "project_id": s.project_id}

    @app.post("/sessions/{session_id}/plans")
    def create_plan(session_id: str, body: PlanIn) -> dict[str, Any]:
        ses = store.get_session(session_id)
        if not ses:
            raise HTTPException(404, "session not found")
        plan = policy.create_plan_for_request(session_id, body.title, body.body_md, ses.mode)
        events.publish(session_id, {"type": "plan.created", "plan_id": plan.id, "status": plan.status.value})
        return {"id": plan.id, "status": plan.status.value, "title": plan.title}

    @app.post("/plans/{plan_id}/approve")
    def approve_plan(plan_id: str) -> dict[str, Any]:
        try:
            plan = policy.approve_plan(plan_id)
        except ModePolicyError as e:
            raise HTTPException(400, str(e)) from e
        events.publish(plan.session_id, {"type": "plan.approved", "plan_id": plan.id})
        return {"id": plan.id, "status": plan.status.value}

    @app.post("/plans/{plan_id}/reject")
    def reject_plan(plan_id: str) -> dict[str, Any]:
        try:
            plan = policy.reject_plan(plan_id)
        except ModePolicyError as e:
            raise HTTPException(400, str(e)) from e
        return {"id": plan.id, "status": plan.status.value}

    @app.post("/sessions/{session_id}/tasks")
    def create_task(session_id: str, body: TaskIn) -> dict[str, Any]:
        try:
            task = policy.create_task(
                session_id,
                body.title,
                body.prompt,
                plan_id=body.plan_id,
                executor=body.executor,
                confirmed=body.confirmed,
            )
        except ModePolicyError as e:
            raise HTTPException(400, str(e)) from e
        return {
            "id": task.id,
            "status": task.status.value,
            "dispatchable": task.dispatchable,
            "dispatch_key": task.dispatch_key,
        }

    @app.post("/tasks/{task_id}/confirm")
    def confirm_task(task_id: str) -> dict[str, Any]:
        try:
            task = policy.confirm_task(task_id)
        except ModePolicyError as e:
            raise HTTPException(400, str(e)) from e
        return {"id": task.id, "dispatchable": task.dispatchable, "status": task.status.value}

    @app.post("/tasks/{task_id}/spawn")
    async def spawn_task(task_id: str) -> dict[str, Any]:
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(404, "task not found")
        ses = store.get_session(task.session_id)
        if not ses:
            raise HTTPException(404, "session not found")
        proj = store.get_project(ses.project_id)
        if not proj:
            raise HTTPException(404, "project not found")
        try:
            worker_id = await workers.run_task(task_id, proj.repo_path)
        except DispatchError as e:
            raise HTTPException(409, str(e)) from e
        return {
            "worker_id": worker_id,
            "capabilities": workers.capabilities(task.executor_type),
        }

    @app.post("/recovery/leases")
    def recover_leases() -> dict[str, Any]:
        ids = recovery.recover_expired_leases()
        return {"recovered": ids}

    @app.get("/sessions/{session_id}/events")
    async def session_events(session_id: str, request: Request) -> StreamingResponse:
        last = request.headers.get("Last-Event-ID")
        after = int(last) if last and last.isdigit() else 0

        async def gen() -> AsyncIterator[str]:
            cursor = after
            while True:
                if await request.is_disconnected():
                    break
                batch = events.since(session_id, cursor)
                for ev in batch:
                    cursor = int(ev["id"])
                    data = json.dumps(ev, ensure_ascii=False)
                    yield f"id: {cursor}\nevent: {ev['type']}\ndata: {data}\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/tasks/{task_id}/acceptance")
    def get_acceptance(task_id: str) -> dict[str, Any]:
        ev = store.get_acceptance_for_task(task_id)
        if not ev:
            raise HTTPException(404, "no evidence")
        return ev.to_dict()

    @app.post("/tasks/{task_id}/accept")
    def run_acceptance(task_id: str, body: AcceptIn | None = None) -> dict[str, Any]:
        body = body or AcceptIn()
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(404, "task not found")
        ses = store.get_session(task.session_id)
        proj = store.get_project(ses.project_id) if ses else None
        if not proj:
            raise HTTPException(404, "project not found")
        plan_snap = ""
        if task.plan_id:
            plan = store.get_plan(task.plan_id)
            plan_snap = plan.body_md if plan else ""
        evidence = gate.evaluate(
            GateInput(
                task=task,
                worker_id=None,
                repo_path=proj.repo_path,
                requirement_snapshot=task.prompt,
                plan_snapshot=plan_snap,
                changed_files=list(body.changed_files),
                forbidden_prefixes=list(body.forbidden_prefixes),
                required_files=list(body.required_files),
                test_commands=list(body.test_commands) or [["true"]],
            )
        )
        path = render_acceptance_html(evidence)
        return {"evidence_id": evidence.id, "final_status": evidence.final_status, "report": str(path)}

    @app.get("/reports/{evidence_id}", response_class=HTMLResponse)
    def report(evidence_id: str) -> HTMLResponse:
        from sma.paths import sma_home

        path = sma_home(root) / "reports" / f"acceptance-{evidence_id}.html"
        if not path.exists():
            raise HTTPException(404, "report not found")
        return HTMLResponse(path.read_text(encoding="utf-8"))

    @app.get("/setup/network")
    def get_network() -> dict[str, Any]:
        cfg = load_network_config(root)
        return {"config": cfg.__dict__, "setup": describe_setup(cfg)}

    @app.post("/setup/network")
    def set_network(body: NetworkIn) -> dict[str, Any]:
        cfg = NetworkConfig(
            mode=body.mode,  # type: ignore[arg-type]
            bind=body.bind,
            port=body.port,
            public_hostname=body.public_hostname,
            cloudflare_tunnel_name=body.cloudflare_tunnel_name,
        )
        save_network_config(cfg, root)
        return {"config": cfg.__dict__, "setup": describe_setup(cfg)}

    @app.get("/memory/{namespace}")
    def mem_list(namespace: str) -> dict[str, Any]:
        return {"items": memory.list(namespace)}

    web_dir = Path(__file__).resolve().parents[2] / "web"
    if web_dir.is_dir():
        app.mount("/ui", StaticFiles(directory=str(web_dir), html=True), name="ui")

    return app
