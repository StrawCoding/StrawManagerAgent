"""Cursor executor via official Python cursor-sdk (optional dependency)."""

from __future__ import annotations

from typing import Any, AsyncIterator

from sma.executors.base import RunHandle, StartRequest
from sma.models import ExecutorCapabilities, ExecutorType, FailureEnvelope


class CursorExecutor:
    name = ExecutorType.CURSOR
    capabilities = ExecutorCapabilities(
        cancel=True,
        pause=False,
        inject=False,
        resume=True,
        diff=True,
    )

    def __init__(self) -> None:
        self._handles: dict[str, Any] = {}

    def _client(self) -> Any:
        try:
            from cursor_sdk import AsyncClient  # type: ignore
        except ImportError as e:
            raise ImportError(
                "cursor-sdk not installed. pip install 'straw-manager-agent[cursor]'"
            ) from e
        return AsyncClient()

    async def start(self, req: StartRequest) -> RunHandle:
        client = self._client()
        # Public beta API surface may evolve; keep minimal Local prompt path.
        run_id = f"cur_{req.worker_id}"
        self._handles[run_id] = {
            "client": client,
            "req": req,
            "done": False,
            "result": None,
            "error": None,
        }
        return RunHandle(
            executor_type=ExecutorType.CURSOR,
            run_id=run_id,
            agent_id=None,
        )

    async def cancel(self, handle: RunHandle) -> None:
        meta = self._handles.get(handle.run_id)
        if meta:
            meta["cancelled"] = True

    async def stream(self, handle: RunHandle) -> AsyncIterator[dict[str, Any]]:
        meta = self._handles.get(handle.run_id)
        if not meta:
            yield {"type": "executor.error", "message": "handle missing"}
            return
        req: StartRequest = meta["req"]
        try:
            client = meta["client"]
            # Prefer Agent.prompt-like one-shot if available on the client.
            if hasattr(client, "prompt"):
                result = await client.prompt(
                    req.prompt,
                    cwd=req.cwd or req.repo_path,
                )
            elif hasattr(client, "run"):
                result = await client.run(req.prompt, cwd=req.cwd or req.repo_path)
            else:
                raise RuntimeError("cursor-sdk client has no prompt/run method")
            meta["result"] = result
            meta["done"] = True
            yield {"type": "executor.completed", "result": str(result)[:4000]}
        except Exception as e:  # noqa: BLE001
            meta["error"] = str(e)
            meta["done"] = True
            fail = FailureEnvelope(
                worker_id=handle.run_id,
                task_id=req.task_id,
                executor="cursor",
                reason="exception",
                retryable=True,
                summary=str(e),
            )
            yield {"type": "worker_failed", **fail.to_dict()}

    async def status(self, handle: RunHandle) -> dict[str, Any]:
        meta = self._handles.get(handle.run_id) or {}
        if meta.get("done"):
            return {"state": "exited", "error": meta.get("error")}
        return {"state": "running" if handle.run_id in self._handles else "unknown"}
