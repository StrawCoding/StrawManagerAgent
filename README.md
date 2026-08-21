# StrawManagerAgent

**Architecture Baseline v1.1** — Agent Orchestration OS（不是另一個 coding agent）。

```text
StrawManagerAgent = Orchestrator / Policy / State / Acceptance
ExecutorRegistry → OpenCode | Cursor
```

## 快速開始

```bash
cd /mnt/data/code/project/StrawCoding/StrawManagerAgent
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

sma init
pytest -q

# Manager：一句話全權
sma manager "修好登入問題" --repo /path/to/repo --no-spawn

# Team Leader：先審計畫
sma leader "增加 OAuth" --repo /path/to/repo
sma plan approve <plan_id>

# API + SSE
sma serve --port 8741
```

資料與金鑰：`~/.sma/`（`chmod 700`）、`~/.sma/.env`（`chmod 600`）。

## 三模式硬契約

| 模式 | 行為 |
|------|------|
| Manager | 不追問 → 計畫 → 直接派工 |
| Team Leader | 計畫 → 使用者核准 → 派工 |
| Developer | 使用者建／確認 task 後才派工 |

唯一 spawn 入口：`DispatchService.spawn()`（idempotency + lease）。PASS 必須寫入 `AcceptanceEvidence`。

## 實作階段狀態

1. Domain + Dispatch/lease/recovery — 已落地
2. Durable EventBus + SecretRedactor — 已落地
3. OpenCode CLIRunner — 已落地（ServerRunner = v1.x stub）
4. AcceptanceGate + Evidence + HTML 報告 — 已落地
5. Cursor Python SDK executor — 已落地（需 `pip install .[cursor]`）
6. CLI 三模式 — 已落地
7. Web 工況台 — 已落地最小 REST+SSE 控制台（`web/index.html`，掛載 `/ui`；完整 Vue SPA 可接續）
8. NetworkExpose（LAN_mDNS / CF / CustomDNS）— 已落地

Skill：[`skills/StrawManagerAgent/SKILL.md`](skills/StrawManagerAgent/SKILL.md)
