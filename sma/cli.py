"""sma CLI — Manager / Team Leader / Developer entrypoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
import uvicorn

from sma.acceptance import AcceptanceGate, GateInput
from sma.db import Store, utcnow
from sma.dispatch import DispatchService
from sma.events import EventBus
from sma.mode_policy import ModePolicy
from sma.models import ExecutorType, Mode, Project, Session, new_id
from sma.network import describe_setup, load_network_config
from sma.paths import ensure_home
from sma.reports import render_acceptance_html
from sma.workers import WorkerController

app = typer.Typer(add_completion=False, no_args_is_help=True, help="StrawManagerAgent CLI")


def _ctx(home: Optional[Path]) -> tuple[Store, ModePolicy, DispatchService, EventBus, WorkerController]:
    root = Path(home).expanduser() if home else None
    ensure_home(root)
    store = Store(root=root)
    policy = ModePolicy(store)
    dispatch = DispatchService(store, policy)
    events = EventBus(store)
    workers = WorkerController(store, dispatch, events)
    return store, policy, dispatch, events, workers


@app.command("init")
def init_home(home: Optional[Path] = typer.Option(None, help="SMA_HOME override")) -> None:
    path = ensure_home(Path(home) if home else None)
    typer.echo(f"initialized {path}")


@app.command("serve")
def serve(
    host: str = "0.0.0.0",
    port: int = 8741,
    home: Optional[Path] = typer.Option(None),
) -> None:
    root = Path(home).expanduser() if home else None
    ensure_home(root)
    from sma.api import create_app

    uvicorn.run(create_app(root=root), host=host, port=port)


@app.command("manager")
def manager_cmd(
    request: str = typer.Argument(..., help="一句話需求，全權交給 Manager"),
    repo: Path = typer.Option(..., exists=True, file_okay=False, help="目標 repo"),
    home: Optional[Path] = typer.Option(None),
    spawn: bool = typer.Option(True, help="建立後立即 spawn"),
) -> None:
    store, policy, _, _, workers = _ctx(home)
    now = utcnow()
    proj = store.create_project(
        Project(id=new_id("prj_"), name=repo.name, repo_path=str(repo.resolve()), created_at=now)
    )
    ses = store.create_session(
        Session(id=new_id("ses_"), project_id=proj.id, mode=Mode.MANAGER, title=request[:80], created_at=now)
    )
    plan = policy.create_plan_for_request(
        ses.id,
        title=request[:80],
        body_md=f"# Manager plan\n\nGoal: {request}\n\n1. Discover\n2. Implement\n3. Test\n",
        mode=Mode.MANAGER,
    )
    task = policy.create_task(ses.id, "manager-work", request, plan_id=plan.id)
    typer.echo(json.dumps({"session_id": ses.id, "plan_id": plan.id, "task_id": task.id}, ensure_ascii=False))
    if spawn:
        import asyncio

        wid = asyncio.run(workers.run_task(task.id, str(repo.resolve())))
        typer.echo(json.dumps({"worker_id": wid}, ensure_ascii=False))


@app.command("leader")
def leader_cmd(
    request: str = typer.Argument(...),
    repo: Path = typer.Option(..., exists=True, file_okay=False),
    home: Optional[Path] = typer.Option(None),
) -> None:
    store, policy, *_ = _ctx(home)
    now = utcnow()
    proj = store.create_project(
        Project(id=new_id("prj_"), name=repo.name, repo_path=str(repo.resolve()), created_at=now)
    )
    ses = store.create_session(
        Session(id=new_id("ses_"), project_id=proj.id, mode=Mode.TEAM_LEADER, title=request[:80], created_at=now)
    )
    plan = policy.create_plan_for_request(
        ses.id,
        title=request[:80],
        body_md=f"# Team Leader plan\n\n## Goal\n{request}\n\n## Steps\n1. ...\n2. ...\n\n**Awaiting approval**\n",
        mode=Mode.TEAM_LEADER,
    )
    task = policy.create_task(ses.id, "leader-work", request, plan_id=plan.id)
    typer.echo(
        json.dumps(
            {
                "session_id": ses.id,
                "plan_id": plan.id,
                "plan_status": plan.status.value,
                "task_id": task.id,
                "dispatchable": task.dispatchable,
                "next": f"sma plan approve {plan.id}",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


plan_app = typer.Typer(help="Plan commands")
app.add_typer(plan_app, name="plan")


@plan_app.command("approve")
def plan_approve(plan_id: str, home: Optional[Path] = typer.Option(None)) -> None:
    store, policy, _, _, workers = _ctx(home)
    plan = policy.approve_plan(plan_id)
    typer.echo(json.dumps({"plan_id": plan.id, "status": plan.status.value}, ensure_ascii=False))
    # spawn first dispatchable task for session
    for task in store.list_tasks(plan.session_id):
        if task.plan_id == plan.id and task.dispatchable:
            ses = store.get_session(plan.session_id)
            proj = store.get_project(ses.project_id) if ses else None
            if proj:
                import asyncio

                wid = asyncio.run(workers.run_task(task.id, proj.repo_path))
                typer.echo(json.dumps({"spawned_task": task.id, "worker_id": wid}, ensure_ascii=False))
            break


dev_app = typer.Typer(help="Developer mode")
app.add_typer(dev_app, name="dev")


@dev_app.command("task-add")
def dev_task_add(
    title: str,
    prompt: str,
    session_id: str = typer.Option(...),
    home: Optional[Path] = typer.Option(None),
) -> None:
    _, policy, *_ = _ctx(home)
    task = policy.create_task(session_id, title, prompt, confirmed=False)
    typer.echo(json.dumps({"task_id": task.id, "dispatchable": task.dispatchable}, ensure_ascii=False))


@dev_app.command("confirm")
def dev_confirm(task_id: str, home: Optional[Path] = typer.Option(None)) -> None:
    _, policy, *_ = _ctx(home)
    task = policy.confirm_task(task_id)
    typer.echo(json.dumps({"task_id": task.id, "dispatchable": task.dispatchable}, ensure_ascii=False))


@dev_app.command("run")
def dev_run(task_id: str, home: Optional[Path] = typer.Option(None)) -> None:
    store, _, _, _, workers = _ctx(home)
    task = store.get_task(task_id)
    if not task:
        raise typer.Exit(code=1)
    ses = store.get_session(task.session_id)
    proj = store.get_project(ses.project_id) if ses else None
    if not proj:
        raise typer.Exit(code=1)
    import asyncio

    wid = asyncio.run(workers.run_task(task_id, proj.repo_path))
    typer.echo(json.dumps({"worker_id": wid}, ensure_ascii=False))


@app.command("accept")
def accept_cmd(
    task_id: str,
    home: Optional[Path] = typer.Option(None),
) -> None:
    store, *_ = _ctx(home)
    task = store.get_task(task_id)
    if not task:
        raise typer.Exit(1)
    ses = store.get_session(task.session_id)
    proj = store.get_project(ses.project_id) if ses else None
    plan_snap = ""
    if task.plan_id:
        p = store.get_plan(task.plan_id)
        plan_snap = p.body_md if p else ""
    gate = AcceptanceGate(store)
    ev = gate.evaluate(
        GateInput(
            task=task,
            worker_id=None,
            repo_path=proj.repo_path if proj else ".",
            requirement_snapshot=task.prompt,
            plan_snapshot=plan_snap,
            changed_files=[],
            forbidden_prefixes=[],
            required_files=[],
            test_commands=[["true"]],
        )
    )
    path = render_acceptance_html(ev)
    typer.echo(json.dumps({"final_status": ev.final_status, "evidence_id": ev.id, "report": str(path)}, ensure_ascii=False))


@app.command("network")
def network_cmd(home: Optional[Path] = typer.Option(None)) -> None:
    root = Path(home).expanduser() if home else None
    cfg = load_network_config(root)
    typer.echo(json.dumps(describe_setup(cfg), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
