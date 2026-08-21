"""Secret redaction before persist / SSE / reports."""

from __future__ import annotations

import re
from typing import Any


_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[=:]\s*([^\s\"']+)"),
    re.compile(r"(?i)(bearer\s+)([a-z0-9\-._~+/]+=*)"),
    re.compile(r"(?i)(CLOUDFLARE_API_TOKEN|CURSOR_API_KEY|OPENAI_API_KEY)=([^\s]+)"),
]


class SecretRedactor:
    def redact_text(self, text: str) -> str:
        out = text
        for pat in _PATTERNS:
            out = pat.sub(lambda m: f"{m.group(1)}***REDACTED***", out)
        return out

    def redact_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._walk(payload)  # type: ignore[return-value]

    def _walk(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.redact_text(obj)
        if isinstance(obj, dict):
            return {k: self._walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._walk(x) for x in obj]
        return obj
