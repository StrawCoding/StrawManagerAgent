---
name: StrawManagerAgent
description: >-
  StrawManagerAgent（SMA）是 Agent Orchestration OS：Manager／Team Leader／Developer
  三種硬契約、Dispatch lease、AcceptanceEvidence。一句話交給 Manager 全權處理。
  底層 Executor 為 OpenCode（預設）與 Cursor Python SDK。
version: 0.1.0.1
---

# StrawManagerAgent

SMA ≠ coding agent。SMA = Orchestrator / Policy / State / Acceptance。

## 三模式

| 模式 | 用法 | 行為 |
|------|------|------|
| **Manager** | `sma manager "修好登入"` | 不追問；自動計畫；直接派工 |
| **Team Leader** | `sma leader "加 OAuth"` → `sma plan approve <id>` | 先計畫報告；核准後才 spawn |
| **Developer** | `sma dev task-add` → `confirm` → `run` | 使用者控 task |

## 介入指令

```bash
sma init
sma serve --port 8741
sma manager "..." --repo /path/to/repo
sma leader "..." --repo /path/to/repo
sma plan approve <plan_id>
sma dev task-add --session-id <ses> TITLE PROMPT
sma dev confirm <task_id>
sma dev run <task_id>
sma accept <task_id>
sma network
```

控制按鈕依 `ExecutorCapabilities`（cancel／pause／inject／resume／diff）顯示；勿假設所有 executor 支援 pause/inject。

## 硬契約

- 唯一 spawn：`DispatchService.spawn()`（idempotency + lease）
- Team Leader：`plan_status=approved` 前禁 spawn
- PASS = Deterministic ∧ Policy ∧ LLM，且必須寫入 AcceptanceEvidence

## 架構基準

見專案 `Architecture Baseline v1.1`。開發原則：實作符合架構，不重新發明 worker/state model。
