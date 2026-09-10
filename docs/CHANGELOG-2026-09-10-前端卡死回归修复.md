# 前端「执行过程框收起 / 卡死」回归 — 修复日志

- 日期：2026-09-10
- 范围：9/8–9/9 改动引入的前端回归（执行过程框中途收起 + 前端卡死 + 误报“Agent 停止/重启”）
- 状态：**已修复并重建前端产物**；需重启后端 + 浏览器硬刷新后生效

---

## 一、根因链条（结论）

1. **15 秒对账定时器是触发源。**
   前端存在两个 15 秒定时器：`sse-handling.js` 的 `progressTimer`（每条流一个）与
   `session-scroll-history.js` 的 `streamPoll`，都会周期性调用
   `reconcileRunStateFromServer()` 询问服务端“本轮是否仍活跃”。

2. **首步窗口内 `/sessions/state` 快照可能瞬时缺少本轮 run。**
   服务端快照由 `_session_run_state_fields_light()` 生成，`/sessions/state` 带 5s TTL
   缓存且到期时“先回旧值、后台刷新”（`d85ef0b`）；在 draft-first 新会话
   （`a4eb156`）扩宽的首步窗口内，对账请求可能读到“run 注册之前”的旧快照。

3. **旧逻辑在快照缺 run 时直接误杀本地流并封印渲染上下文。**
   `session-management.js` 的 reconcile 分支命中 `!active.has(sid)` 且本地 run 带有
   `reattached / submitted+streamConsuming / transportClosed` 任一标记时，执行
   `abortSessionRun + endRunForClient()`；而 `endRunForClient`（`2e18430` 引入）会
   **无条件** `is-collapsed + sealProcessGroup()`：
   - 收起过程框 → 现象①；
   - `seal` 清空渲染上下文，后续流式增量无处渲染 → 卡死（现象①③）；
   - 刷新/切换会话仍走同一判定 → 不可恢复；下一步重建分组时才恢复。

4. **断开动作反过来误杀后端 run，产生误导文案。**
   前端 `abort()` 断开 SSE 时，ASGI 常以 `GeneratorExit` 拆除生成器；旧 `webui.py`
   的 SSE `finally` 只识别 `client_disconnected`（原仅 `CancelledError` 分支会置位），
   于是 `stop_event.set()` 误杀仍在后台运行的 run，落盘
   `run_interrupted(reason=unspecified)`，文案来自 `agent_loop.py::_interrupt_terminal_text()`
   （现象②）。

---

## 二、本次改动

| # | 文件 | 改动 |
|---|---|---|
| 1 | `frontend/src/app/modules/session-management.js` | reconcile 新增 **streamAlive 守卫**：本地仍在消费 SSE 且未见真终态（`streamConsuming && terminalSeen !== true`）时不得 `abort/endRun`；对账路径透传 `collapseProcess: false` |
| 2 | `frontend/src/app/modules/sse-handling.js` | `endRunForClient` 的折叠/seal 收紧为**仅真终态（`terminalSeen`）或显式 `collapseProcess: true`** 才执行 |
| 3 | `app/webui.py` | chat SSE 生成器新增 `except GeneratorExit: client_disconnected = True`（观察者离开不再杀 run、不再误报停止/重启） |
| 4 | `app/webui.py` | `_reserve_session_chat_start` / `_release_session_chat_start` 时**作废 `/sessions/state` 缓存**，压缩“快照缺 run”窗口 |
| 5 | `tests/js/stream_recovery_runtime.cjs` | 恢复并保留回归用例：健康流不得被 reconcile 误杀；真收尾路径必须带 `collapseProcess: false` |
| 6 | `app/templates/dist` | 重新构建（`main-Dap0b3Ip.js`），`check_frontend_dist_sync` 通过 |

### 文件恢复说明

工作区内 `app/webui.py`、`frontend/src/app/modules/sse-handling.js`、
`frontend/src/app/modules/session-management.js` 及测试脚本
`tests/js/stream_recovery_runtime.cjs` 于 12:23 被会话 690a4e8f 的
`file_changes_reverted`（回退机制，因 before 快照缺失被当作“恢复到不存在”）
删除。本次按用户指示从 `20260909.zip` 恢复前三个文件；测试脚本从该回退事件的
全量 diff 精确复原，并对其应用了本次回归用例。

---

## 三、验证

- `python -m py_compile app/webui.py` ✅；`node --check` 两个 JS ✅
- `node tests/js/stream_recovery_runtime.cjs` → `stream recovery runtime: passed`
- Python 回归（`test_frontend_session_stream_runtime` / `test_stream_resilience` /
  `test_webui_messages` / `test_process_aggregate_ui` / `test_session_activity_sorting`）：
  **94 passed**
- 全量 `pytest tests`：**1501 passed, 4 skipped, 2 failed**；2 项失败为仓库既有不一致
  （`test_apply_patch_schema_...` 断言的工具描述字符串与 `app/agent_tools.py` 现文不符；
  `test_analyze_approval_...` 期望的字典缺少新字段 `intercept_reason`），与本次修复无关
- `npm run build` → `main-Dap0b3Ip.js`；`scripts/check_frontend_dist_sync.py` → in sync

## 四、生效方式

1. 重启 Agent 后端（托盘/RUN 脚本；请勿直接杀进程）；
2. 浏览器硬刷新（Ctrl+F5），确认加载 `main-Dap0b3Ip.js`。

若再现，请抓：15 秒时 `/sessions/*/chat` SSE 状态、Console 报错、会话 ID 与时间。

---

## 附：数据来源

- Git：`2e18430` / `fee92c5` / `f3f7a3c` / `a4eb156` / `d85ef0b`；`git show` 差异
- 代码：`frontend/src/app/modules/{sse-handling,session-management,session-scroll-history}.js`、`app/webui.py`、`app/agent_loop.py`
- 会话证据：`workspace/sessions/f0f5659b-.../events.jsonl`（run_interrupted）、
  `workspace/sessions/690a4e8f-.../events.jsonl`（file_changes_reverted、修复与用例）、
  `docs/CHANGELOG-2026-09-10-前端卡死排查与回退.md`
- 备份：`20260909.zip`（源文件恢复）
