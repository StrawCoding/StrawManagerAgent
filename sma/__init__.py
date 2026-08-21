"""StrawManagerAgent — Agent Orchestration OS (Baseline v1.1)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("straw-manager-agent")
except PackageNotFoundError:
    __version__ = "0.1.0.1"

__all__ = ["__version__"]
