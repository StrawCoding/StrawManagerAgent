"""Three-layer AcceptanceGate + Evidence snapshot."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sma.db import Store, utcnow
from sma.models import AcceptanceEvidence, Task, TaskStatus, new_id


@dataclass
class GateInput:
    task: Task
    worker_id: str | None
    repo_path: str
    requirement_snapshot: str
    plan_snapshot: str
    changed_files: list[str]
    forbidden_prefixes: list[str]
    required_files: list[str]
    test_commands: list[list[str]]
    reviewer: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    reviewer_model: str | None = None


class AcceptanceGate:
    def __init__(self, store: Store) -> None:
        self.store = store

    def evaluate(self, inp: GateInput) -> AcceptanceEvidence:
        det = self._deterministic(inp)
        pol = self._policy(inp)
        rev = self._reviewer(inp, det, pol)

        ok = bool(det.get("pass")) and bool(pol.get("pass")) and bool(rev.get("approve"))
        final = "passed" if ok else "failed"

        base, head, diff_hash = self._git_meta(inp.repo_path)
        evidence = AcceptanceEvidence(
            id=new_id("acc_"),
            task_id=inp.task.id,
            worker_id=inp.worker_id,
            attempt=inp.task.attempt,
            requirement_snapshot=inp.requirement_snapshot,
            plan_snapshot=inp.plan_snapshot,
            git_base_sha=base,
            git_head_sha=head,
            diff_hash=diff_hash,
            deterministic_results=det,
            policy_results=pol,
            reviewer_result=rev,
            reviewer_model=inp.reviewer_model,
            accepted_at=utcnow(),
            final_status=final,
        )
        self.store.save_acceptance(evidence)

        task = self.store.get_task(inp.task.id)
        if task:
            task.status = TaskStatus.PASSED if ok else TaskStatus.FAILED
            task.updated_at = utcnow()
            self.store.save_task(task)
        return evidence

    def _deterministic(self, inp: GateInput) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        all_ok = True
        repo = Path(inp.repo_path)
        for cmd in inp.test_commands:
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=repo if repo.exists() else None,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    check=False,
                )
                ok = proc.returncode == 0
                all_ok = all_ok and ok
                results.append(
                    {
                        "cmd": cmd,
                        "exit_code": proc.returncode,
                        "stdout_tail": (proc.stdout or "")[-2000:],
                        "stderr_tail": (proc.stderr or "")[-2000:],
                        "pass": ok,
                    }
                )
            except Exception as e:  # noqa: BLE001
                all_ok = False
                results.append({"cmd": cmd, "pass": False, "error": str(e)})

        missing = [f for f in inp.required_files if not (repo / f).exists()]
        if missing:
            all_ok = False
        return {"pass": all_ok and not missing, "commands": results, "missing_files": missing}

    def _policy(self, inp: GateInput) -> dict[str, Any]:
        violations: list[str] = []
        for path in inp.changed_files:
            for pref in inp.forbidden_prefixes:
                if path.startswith(pref):
                    violations.append(f"forbidden path: {path}")
            if any(s in path.lower() for s in (".env", "credentials", "secret")):
                violations.append(f"secret-like path: {path}")
        return {
            "pass": len(violations) == 0,
            "violations": violations,
            "changed_files": list(inp.changed_files),
        }

    def _reviewer(self, inp: GateInput, det: dict[str, Any], pol: dict[str, Any]) -> dict[str, Any]:
        if inp.reviewer is None:
            return {
                "approve": bool(det.get("pass")) and bool(pol.get("pass")),
                "mode": "stub_auto",
                "notes": "No LLM reviewer configured; stub mirrors det∧policy",
            }
        payload = {
            "requirement": inp.requirement_snapshot,
            "plan": inp.plan_snapshot,
            "deterministic": det,
            "policy": pol,
            "changed_files": inp.changed_files,
        }
        result = inp.reviewer(payload)
        return {"approve": bool(result.get("approve")), "raw": result, "mode": "llm"}

    @staticmethod
    def _git_meta(repo_path: str) -> tuple[str | None, str | None, str | None]:
        repo = Path(repo_path)
        if not (repo / ".git").exists():
            return None, None, None
        try:
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            diff = subprocess.check_output(["git", "diff", "HEAD"], cwd=repo, text=True)
            diff_hash = hashlib.sha256(diff.encode()).hexdigest()[:16] if diff else None
            return None, head, diff_hash
        except Exception:  # noqa: BLE001
            return None, None, None
