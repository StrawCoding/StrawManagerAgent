"""Memory store — SQLite canonical + Markdown projection."""

from __future__ import annotations

from pathlib import Path

from sma.db import ConflictError, Store
from sma.paths import memory_dir


class MemoryStore:
    def __init__(self, store: Store, root: Path | None = None) -> None:
        self.store = store
        self.root = root

    def add(self, namespace: str, key: str, content: str, source: str = "user") -> dict:
        item = self.store.memory_add(namespace, key, content, source=source)
        self.render_projection(namespace)
        return item

    def replace(self, namespace: str, key: str, content: str, expected_version: int | None = None) -> dict:
        item = self.store.memory_replace(namespace, key, content, expected_version=expected_version)
        self.render_projection(namespace)
        return item

    def remove(self, namespace: str, key: str) -> None:
        self.store.memory_remove(namespace, key)
        self.render_projection(namespace)

    def list(self, namespace: str) -> list[dict]:
        return self.store.memory_list(namespace)

    def render_projection(self, namespace: str) -> Path:
        items = self.list(namespace)
        name = "USER.md" if namespace == "user" else "MEMORY.md"
        path = memory_dir(self.root) / name
        lines = [f"# {name}", ""]
        for it in items:
            lines.append(f"- ({it['key']}@v{it['version']}) {it['content']}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


__all__ = ["MemoryStore", "ConflictError"]
