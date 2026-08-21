"""Executor protocol and capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol

from sma.models import ExecutorCapabilities, ExecutorType


@dataclass
class StartRequest:
    task_id: str
    worker_id: str
    repo_path: str
    prompt: str
    cwd: str | None = None


@dataclass
class RunHandle:
    executor_type: ExecutorType
    run_id: str
    pid: int | None = None
    session_id: str | None = None
    agent_id: str | None = None


class Executor(Protocol):
    name: ExecutorType
    capabilities: ExecutorCapabilities

    async def start(self, req: StartRequest) -> RunHandle: ...

    async def cancel(self, handle: RunHandle) -> None: ...

    async def stream(self, handle: RunHandle) -> AsyncIterator[dict[str, Any]]: ...

    async def status(self, handle: RunHandle) -> dict[str, Any]: ...
