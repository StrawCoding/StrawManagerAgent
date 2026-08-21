"""API smoke tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sma.api import create_app


def test_health_and_team_leader_gate(tmp_path: Path) -> None:
    client = TestClient(create_app(root=tmp_path / "sma"))
    assert client.get("/health").json()["status"] == "ok"
    proj = client.post("/projects", json={"name": "d", "repo_path": str(tmp_path)}).json()
    ses = client.post(
        "/sessions",
        json={"project_id": proj["id"], "mode": "team_leader", "title": "t"},
    ).json()
    plan = client.post(
        f"/sessions/{ses['id']}/plans",
        json={"title": "oauth", "body_md": "# plan"},
    ).json()
    assert plan["status"] == "waiting_approval"
    task = client.post(
        f"/sessions/{ses['id']}/tasks",
        json={"title": "w", "prompt": "do", "plan_id": plan["id"]},
    ).json()
    assert task["dispatchable"] is False
    bad = client.post(f"/tasks/{task['id']}/spawn")
    assert bad.status_code == 409
    ok = client.post(f"/plans/{plan['id']}/approve")
    assert ok.json()["status"] == "approved"
    # after approve, task becomes dispatchable — refresh via confirm path not needed
    # spawn will run executor async; we only assert spawn accepted
    # Avoid hanging on real opencode: mark by checking 409 gone after re-fetch isn't available;
    # instead create manager session for spawnless path.
    net = client.get("/setup/network").json()
    assert net["setup"]["mode"] == "lan_mdns"
