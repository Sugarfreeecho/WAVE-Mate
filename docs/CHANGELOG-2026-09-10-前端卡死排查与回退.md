# 前端“执行过程框收起 / 卡死”问题 — 排查、修改与回退日志

- 日期：2026-09-10
- 范围：会话 `f0f5659b-c638-4b70-b28d-d8b458a8eb0f`（“仓库新改动分析/提交”）相关的会话收尾/流式渲染问题
- 状态：**本次会话内所做的代码修改已全部回退**；根因尚未闭环，待现场证据

---

## 一、用户报告的现象

| # | 现象 |
|---|---|
| ① | 每次用户发出消息后，**首步（仅首步）**输出过程中“执行过程框”收起，前端卡死 |
| ② | 同 ① 且报错：“任务因 Agent 停止、重启或运行中断而暂停，可在服务恢复后继续。” |
| ③ | 任意输出过程中前端突然卡死（**后端正常**），等模型输出到下一步或完成对话时能恢复；**刷新或切换会话不可恢复** |
| ④ | 稳定规律：**消息发出后第 15 秒**触发 |

用户判断：这些现象是 **9/8–9/9 两天的改动**引入的。

---

## 二、排查结论（含证据）

### 1. 排除“Agent 进程停止/重启”
- 进程级查询：`app/main.py`（PID 16612）自 **2026-09-09 11:42:24 UTC** 起持续运行；中断发生后仍未重启。
- Windows System/Application 事件日志（09-09 23:00 ~ 09-10 02:00）**无重启、崩溃、掉电**记录（仅 RTD3 驱动噪音）。
- 结论：现象②的文案虽写“Agent 停止、重启”，但实际**并非进程重启**。

### 2. 会话事件侧证据
- 会话 `f0f5659b` 最后一次 run `d617d912` 于 `2026-09-09T16:43:05.157Z` 落盘 `run_interrupted`，`reason=unspecified`；随后前端显示现象②文案。
- 同会话 15:44（run `3fca51c4`）也曾以同样方式中断（reason=`unspecified`），重发后成功。
- 15:44 / 16:43 两次中断都发生在 **LLM 首轮流式输出中途**，日志中伴有 `suppressed post-terminal stream event: type=llm_reasoning/llm_response`（即 run 已被判定终止、后续输出被丢弃）。

### 3. `reason=unspecified` 的可能来源（代码）
- `POST /sessions/{session_id}/interrupt` 未带 `reason` → 默认 `"unspecified"`（`app/webui.py`）。
- `should_stop()` 命中但 `get_interrupt_reason()` 为空（会话被标记 deleted，或 chat SSE 的 `stop_event` 路径）。
- 文案模板来自 `app/agent_loop.py` 的 `_interrupt_terminal_text()`：非“用户主动中断”统一显示为“任务因 Agent 停止、重启或运行中断而暂停…”。

### 4. 实际运行的前端不是 HEAD 产物（重要）
- `app/templates/dist/index.html` 指向工作区**新增未提交**的 bundle `main-D1AW7-g7.js`（原 `main-DN-NSKfw.js` 已被删除）。
- 即：用户实测运行的前端 = 工作区未提交版本（含 9/8–9/9 的“第二轮修复”），源码与产物已脱节。

### 5. 第 15 秒规律的精确来源（关键）
`frontend/src/app/modules/sse-handling.js` 新增：

```js
const progressTimer = setInterval(function () {
    void checkSessionStreamProgress(runSessionId, runCtx);
}, 15000);                       // ← 正好 15 秒
```

调用链：`checkSessionStreamProgress` → `reconcileRunStateFromServer()` → 命中 `!active.has(sid)` 时：

```js
abortSessionRun(sid, 'reconcile-finished');
endRunForClient(sid, run.ctx, {...});
```

而 `endRunForClient`（`sse-handling.js`）依次执行：
- `terminalAggregate.classList.add('is-collapsed')` → **收起过程框**（现象①）
- `sealProcessGroup(ctx)` → **清空渲染上下文**，后续流式增量无处渲染 → **卡死**（现象①③）
- `markSessionRunInactive` / `clearSessionRunStateIfMatch`

这同时解释了“**刷新或切换会话不可恢复**（重新加载仍走同一判定）”“**下一步或完成时恢复**”。

补充：前端 reconcile 消费的是 `/sessions/state` 快照**顶层 `active_runs`**（由 `app/webui.py` 的 `_session_run_state_fields_light` 生成），不是 per-session 的 `_session_run_state_fields`。

### 6. 相关提交定位（git blame / log -L）
| 提交 | 日期 | 与问题的关系 |
|---|---|---|
| `cfc4e1e` | 2026-06-26 | 首次将 `stop_event` 与 SSE 连接结束绑定 |
| `b7170a7` | 2026-06-27 | 定型 `finally: if not client_disconnected: stop_event.set()`（非正常断开仍误杀 run） |
| `825ab997` | 2026-07-25 | 引入“任务因 Agent 停止、重启或运行中断而暂停…”文案与 `_EXPLICIT_USER_INTERRUPT_REASONS` |
| `fee92c5` | 2026-09-08 | final 事件立即置完成（改动终态写入路径起点） |
| `f3f7a3c` | 2026-09-08 | SSE 恢复/重连逻辑（`markRunAbortReason`、`controller.abort`） |
| `2e18430` | 2026-09-09 | `endRunForClient` 折叠逻辑 / finalizing 语义（过程框提前收起的直接回归） |
| `a4eb156f` | 2026-09-09 | draft-first 新会话生命周期（首步流程重构） |

---

## 三、本次会话内做过的修改（现已全部回退）

| # | 文件 | 位置（约） | 修改内容 | 目的 |
|---|---|---|---|---|
| 1 | `app/webui.py` | chat SSE `event_generator`（约 4994–5030） | 新增 `except GeneratorExit` / `except Exception` 分支，一律置 `client_disconnected = True`；`finally` 仅在 run 自然结束时 `stop_event.set()` | 连接以任何形态结束都视为“观察者离开”，不误杀后台 run |
| 2 | `app/webui.py` | `_session_run_state_fields()`（约 1446–1488） | 投影为空但 `_has_local_worker_activity(sid)` 为真时，兜底返回 `run_active: True`（带真实 `run_id`） | 修补 run 起始瞬间快照报“无活动 run” |
| 3 | `frontend/src/app/modules/message-rendering.js` | `appendMessage`（约 4909–4928） | 新增 `finishRunView = !!(ctx.terminalSeen \|\| replayingMessages)`，仅真终态/历史重放才收起+封印 | 避免中间步骤提前收起过程框 |
| 4 | `frontend/src/app/modules/session-management.js` | `reconcileRunStateFromServer`（约 1215–1230） | 新增 `streamAlive` 守卫：本地流仍在消费且未见终态时不做 `abort/endRunForClient` | 掐断“15 秒 reconcile 误杀”路径 |
| 5 | 构建 | `npm run build` | 重建前端产物 | 使 dist 与 src 同步 |

### 期间验证记录
- `python -m py_compile app/webui.py` ✅
- Python 测试（`test_webui_messages` / `test_session_activity_sorting` / `test_frontend_session_stream_runtime` / `test_stream_resilience` / `test_process_aggregate_ui` / `test_react_recovery_runner`）→ **106 passed**
- JS 运行时测试（`stream_recovery` / `frontend_session_stream` / `interrupt_stream` / `smooth_stream` / `session_store` / `new_session_lifecycle`）→ 全部 passed
- `npm run verify:dist` → in sync
- 浏览器实测（Playwright 实际操作 UI 跑 3 个 run：13.4s / 17.9s / 21.9s，含工具调用）→ **均正常完成，Console 0 错误，未能复现卡死**

---

## 四、回退情况

| 项目 | 结果 |
|---|---|
| `app/webui.py` 两处（#1、#2） | 已回退，恢复为原 `except asyncio.CancelledError` 分支与原始 `finally`；`_session_run_state_fields()` 兜底块已删除 |
| `message-rendering.js`（#3） | 已回退，`git status` 中该文件不再出现（与 HEAD 一致） |
| `session-management.js`（#4） | 已回退，`streamAlive` 守卫已删除 |
| 前端产物 | 已重新构建，`index.html` 与产物 hash 回到 `main-D1AW7-g7.js`（与工作区原状一致） |
| 回退验证 | `grep` 检查 `projection_pending` / `finishRunView` / `streamAlive` / `GeneratorExit` / `transport closed for session` → **无残留**；`py_compile` ✅ |

**未包含在回退内**（属工作区其他会话遗留的未提交改动，本次未触碰）：
`app/agent_harness.py`、`app/agent_loop.py`、`frontend/src/app/modules/sse-handling.js`、`session-store.js`、`session-event-reducer.js`、`shared-state-and-dialogs.js`、`session-scroll-history.js`、`model-profiles.js`、`permissions.js`、`plugins/change-review/*`、`tests/*` 等。

---

## 五、尚未解决 / 后续建议

1. **根因未闭环**：15 秒的 `reconcileRunStateFromServer` 误判路径 + 服务端快照顶层 `active_runs` 的瞬时缺失，两者叠加才会触发；本机 3 次实测均未复现（说明与特定会话上下文/时序相关）。
2. **建议抓取现场证据**（下次复现时）：
   - DevTools → Network：`/sessions/*/chat` 这条 SSE 请求在 15 秒时的状态（pending / canceled / finished）；
   - DevTools → Console：卡死前后的报错或 `client_send_pipeline_timing` 行；
   - 复现的会话 ID 与时间。
3. **候选修复方向**（待证据确认后再实施）：
   - 将 `endRunForClient` 的过程框折叠条件收紧为“仅真终态（`run_finished/interrupted/failed`）”，去掉 final / 中途折叠；
   - `reconcileRunStateFromServer` 在“本地流仍在消费且未见终态”时不得 `abort` / `endRunForClient`；
   - 明确 `/sessions/state` 顶层 `active_runs` 在本地运行任务存活期的兜底口径；
   - 统一“非用户主动中断”的提示文案，避免把连接断开显示为“Agent 停止/重启”。

---

## 附：数据来源

- 会话事件：`workspace/sessions/f0f5659b-.../events.jsonl`、`runtime_observability.json`、`metadata.json`
- 会话日志：`logs/f0f5659b_*.log`
- 进程/系统：`Get-CimInstance Win32_Process`、Windows System/Application 事件日志
- 代码：`app/webui.py`、`app/agent_loop.py`、`app/session_lifecycle.py`、`frontend/src/app/modules/sse-handling.js`、`session-management.js`、`message-rendering.js`、`app/templates/dist/index.html`
- Git：`git blame`、`git log -L`、`git log -S`、`git diff --stat`
