"""HTML report renderer from AcceptanceEvidence."""

from __future__ import annotations

import html
import json
from pathlib import Path

from sma.models import AcceptanceEvidence
from sma.paths import ensure_home
from sma.redactor import SecretRedactor


def render_acceptance_html(ev: AcceptanceEvidence, out_dir: Path | None = None) -> Path:
    redactor = SecretRedactor()
    safe = redactor.redact_payload(ev.to_dict())
    body = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8"/>
<title>SMA Acceptance — {html.escape(ev.task_id)}</title>
<style>
:root {{ --bg:#0f1115; --fg:#e8eaed; --muted:#9aa0a6; --ok:#3dd68c; --bad:#f07178; --card:#1a1d24; }}
body {{ margin:0; font-family: ui-sans-serif, system-ui, sans-serif; background:var(--bg); color:var(--fg); }}
main {{ max-width:920px; margin:2rem auto; padding:0 1.25rem; }}
h1 {{ font-size:1.4rem; }}
.badge {{ display:inline-block; padding:.2rem .6rem; border-radius:4px; font-weight:600; }}
.ok {{ background:rgba(61,214,140,.15); color:var(--ok); }}
.bad {{ background:rgba(240,113,120,.15); color:var(--bad); }}
section {{ background:var(--card); border:1px solid #2a2f3a; border-radius:8px; padding:1rem 1.1rem; margin:1rem 0; }}
pre {{ white-space:pre-wrap; word-break:break-word; color:var(--muted); font-size:.85rem; }}
.meta {{ color:var(--muted); font-size:.9rem; }}
</style>
</head>
<body>
<main>
  <h1>Acceptance Evidence</h1>
  <p class="meta">task={html.escape(ev.task_id)} attempt={ev.attempt} at {html.escape(ev.accepted_at)}</p>
  <p><span class="badge {'ok' if ev.final_status=='passed' else 'bad'}">{html.escape(ev.final_status.upper())}</span></p>
  <section><h2>Requirement</h2><pre>{html.escape(str(safe.get('requirement_snapshot','')))}</pre></section>
  <section><h2>Plan</h2><pre>{html.escape(str(safe.get('plan_snapshot','')))}</pre></section>
  <section><h2>Git</h2><pre>base={html.escape(str(ev.git_base_sha))}
head={html.escape(str(ev.git_head_sha))}
diff_hash={html.escape(str(ev.diff_hash))}</pre></section>
  <section><h2>Deterministic</h2><pre>{html.escape(json.dumps(safe.get('deterministic_results'), ensure_ascii=False, indent=2))}</pre></section>
  <section><h2>Policy</h2><pre>{html.escape(json.dumps(safe.get('policy_results'), ensure_ascii=False, indent=2))}</pre></section>
  <section><h2>Reviewer</h2><pre>model={html.escape(str(ev.reviewer_model))}
{html.escape(json.dumps(safe.get('reviewer_result'), ensure_ascii=False, indent=2))}</pre></section>
</main>
</body>
</html>
"""
    base = out_dir or (ensure_home() / "reports")
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"acceptance-{ev.id}.html"
    path.write_text(body, encoding="utf-8")
    return path
