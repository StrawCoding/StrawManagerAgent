"""OpenCode executor — CLIRunner (MVP); ServerRunner stub for v1.x."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any, AsyncIterator

from sma.executors.base import RunHandle, StartRequest
from sma.models import ExecutorCapabilities, ExecutorType, FailureEnvelope


class OpenCodeCLIRunner:
    name = ExecutorType.OPENCODE
    capabilities = ExecutorCapabilities(
        cancel=True,
        pause=False,
        inject=False,
        resume=False,
        diff=True,
    )

    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary or shutil.which("opencode") or "/root/.opencode/bin/opencode"
        self._procs: dict[str, asyncio.subprocess.Process] = {}

    async def start(self, req: StartRequest) -> RunHandle:
        if not Path(self.binary).exists() and not shutil.which(self.binary):
            raise FileNotFoundError(f"opencode binary not found: {self.binary}")
        cwd = req.cwd or req.repo_path
        cmd = [
            self.binary,
            "run",
            "--auto",
            "--format",
            "json",
            "--dir",
            cwd,
            req.prompt,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=os.environ.copy(),
        )
        run_id = f"ocli_{req.worker_id}"
        self._procs[run_id] = proc
        return RunHandle(
            executor_type=ExecutorType.OPENCODE,
            run_id=run_id,
            pid=proc.pid,
        )

    async def cancel(self, handle: RunHandle) -> None:
        proc = self._procs.get(handle.run_id)
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except TimeoutError:
                proc.kill()

    async def stream(self, handle: RunHandle) -> AsyncIterator[dict[str, Any]]:
        proc = self._procs.get(handle.run_id)
        if proc is None or proc.stdout is None:
            yield {"type": "executor.error", "message": "process missing"}
            return
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode(errors="replace").strip()
            if not text:
                continue
            try:
                yield json.loads(text)
            except json.JSONDecodeError:
                yield {"type": "executor.log", "line": text}
        code = await proc.wait()
        if code != 0:
            err = ""
            if proc.stderr:
                err = (await proc.stderr.read()).decode(errors="replace")[-4000:]
            fail = FailureEnvelope(
                worker_id=handle.run_id,
                task_id="",
                executor="opencode",
                reason="nonzero_exit",
                exit_code=code,
                retryable=True,
                summary=f"opencode exited {code}",
                log_ref=err[:500] if err else "",
            )
            yield {"type": "worker_failed", **fail.to_dict()}
        else:
            yield {"type": "executor.completed", "exit_code": 0}

    async def status(self, handle: RunHandle) -> dict[str, Any]:
        proc = self._procs.get(handle.run_id)
        if proc is None:
            return {"state": "unknown"}
        return {"state": "running" if proc.returncode is None else "exited", "exit_code": proc.returncode}


class OpenCodeServerRunner:
    """v1.x stub — not an MVP blocker."""

    name = ExecutorType.OPENCODE
    capabilities = ExecutorCapabilities(cancel=True, pause=False, inject=True, resume=True, diff=True)

    async def start(self, req: StartRequest) -> RunHandle:
        raise NotImplementedError("OpenCode ServerRunner is v1.x — use CLIRunner for MVP")

    async def cancel(self, handle: RunHandle) -> None:
        raise NotImplementedError

    async def stream(self, handle: RunHandle) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError
        yield  # pragma: no cover

    async def status(self, handle: RunHandle) -> dict[str, Any]:
        raise NotImplementedError


class OpenCodeExecutor(OpenCodeCLIRunner):
    """Default OpenCode executor = CLIRunner."""
