"""Home directory layout under ~/.sma (permissions enforced)."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_HOME = Path(os.environ.get("SMA_HOME", Path.home() / ".sma")).expanduser()


def sma_home(root: Path | None = None) -> Path:
    return (root or DEFAULT_HOME).expanduser().resolve()


def ensure_home(root: Path | None = None) -> Path:
    home = sma_home(root)
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    for sub in ("memory", "skills", "reports", "logs", "data"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    env = home / ".env"
    if not env.exists():
        env.write_text("# StrawManagerAgent secrets — do not commit\n", encoding="utf-8")
    os.chmod(env, 0o600)
    cfg = home / "config.yaml"
    if not cfg.exists():
        cfg.write_text(
            "version: 1\n"
            "orchestrator_id: local\n"
            "lease_ttl_seconds: 120\n"
            "default_executor: opencode\n"
            "network:\n"
            "  mode: lan_mdns\n"
            "  bind: 0.0.0.0\n"
            "  port: 8741\n",
            encoding="utf-8",
        )
    return home


def db_path(root: Path | None = None) -> Path:
    return ensure_home(root) / "data" / "sma.sqlite3"


def memory_dir(root: Path | None = None) -> Path:
    return ensure_home(root) / "memory"


def env_path(root: Path | None = None) -> Path:
    return ensure_home(root) / ".env"
