# MyAgent Runtime V2 Design

## 目标

Runtime V2 是一套旁路运行时内核，目标是在不直接拆改现有 MyAgent 主流程的前提下，吸收 Claude Code、OpenClaw 和 Codex 本地会话落盘方式的经验，先建立一套可测试、可镜像、可逐步接管的运行状态与事件系统。

它不替换现有 Agent loop、工具系统、技能系统和前端 UI。第一阶段只新增独立模块和测试，等稳定后再通过镜像写入、只读调试接口、局部替换等方式逐步接入。

## 学习对象

### Claude Code

Claude Code 的价值主要在工程可靠性：

- 历史记录以追加日志为核心。
- 工具权限有明确规则。
- 运行结束、异常、退出有清理路径。
- 本地状态倾向于可恢复、可追踪。

Runtime V2 学习它的重点不是 CLI 形态，而是：

- append-only event log。
- 权限规则 allow / deny / ask。
- 失败必须有终态事件。
- 本地日志能作为恢复依据。

### OpenClaw

OpenClaw 的价值主要在控制平面：

- Gateway 统一接入消息与运行状态。
- 事件发布与订阅解耦。
- 运行时健康检查和恢复。
- 多会话、多代理、多渠道状态隔离。

Runtime V2 学习它的重点是：

- RuntimeGateway 作为后端事实入口。
- StreamPublisher 统一广播事件。
- HealthMonitor 修复孤儿 run 和超时 heartbeat。
- Repository 层隔离文件细节。

### Codex 本地 sessions

本地观察到 Codex 会话目录按日期分层：

```text
.codex/sessions/YYYY/MM/DD/rollout-...jsonl
```

单个会话文件是 JSONL transcript，每行包含：

```json
{"timestamp":"...","type":"event_msg","payload":{"type":"task_started"}}
```

Runtime V2 采用同类思路：

- 一个会话一个主事件日志。
- 每次事实变化追加一行。
- 刷新、恢复、调试都以事件日志为基础。
- 额外增加 `seq`、`run_id`，支持 Web UI、SSE 重连和子 Agent 归属。

## 核心原则

1. 事件日志是事实源。
2. 运行状态只能来自 RunRegistry 或从事件日志重建。
3. SSE 只广播事件，不维护事实。
4. 前端只消费 snapshot 和 event，不猜运行状态。
5. metadata、snapshot、index 都是缓存，可从事件日志重建。
6. 子 Agent 状态必须有明确 final、failed、consumed 事实。
7. 异常、停止、断线、进程退出都必须形成终态事件。

## 推荐落盘结构

```text
workspace/sessions/{session_id}/
├── events.jsonl
├── metadata.json
├── snapshots/
│   └── latest.json
└── blobs/
    └── {hash}.txt
```

全局索引：

```text
workspace/sessions/index.json
```

其中：

- `events.jsonl` 是唯一核心事实源。
- `metadata.json` 保存标题、归档、置顶、更新时间等快速字段。
- `snapshots/latest.json` 是加速恢复的派生数据。
- `blobs/` 保存大工具输出、长文本、附件等。
- `index.json` 只加速左侧列表，可重建。

## 标准事件

基础结构：

```json
{
  "seq": 1,
  "timestamp": "2026-06-13T12:00:00.000Z",
  "type": "run_started",
  "session_id": "session-id",
  "run_id": "run-id",
  "payload": {}
}
```

第一批事件类型：

```text
session_meta
message_user
message_assistant_delta
message_assistant_final

run_started
run_heartbeat
run_finished
run_failed
run_interrupted

tool_started
tool_delta
tool_finished
tool_failed

subagent_started
subagent_progress
subagent_finished
subagent_failed
subagent_result_consumed

context_tokens
context_summary_started
context_summary_finished
todo_updated
```

## 新模块

```text
app/runtime_v2/
├── __init__.py
├── event_schema.py
├── event_log.py
├── run_registry.py
├── stream_publisher.py
├── session_repository.py
├── subagent_repository.py
├── permission_manager.py
├── health_monitor.py
└── gateway.py
```

## 分阶段接入

### 阶段 A：只新增，不接入

完成：

- RuntimeEvent 数据结构。
- SessionEventLog 追加、读取、修复。
- RunRegistry 运行态管理。
- StreamPublisher 发布订阅。
- RuntimeGateway 统一入口。
- RuntimeProjector 从 events.jsonl 重建 session/run/subagent 快照。
- SnapshotStore 写入可重建快照缓存。
- 基础单元测试。

风险：

- 不影响现有功能。

当前状态：

- 已完成第一版阶段 A 旁路内核。
- 已覆盖 run finished / failed / interrupted 投影。
- 已覆盖坏行 repair、并发 append seq 单调性、publisher 收事件、snapshot rebuild。
- 尚未接入现有 MyAgent 主流程。

### 阶段 B：镜像写入

现有 MyAgent 继续照旧写 `ui_events.json` 和 SSE，同时 Runtime V2 旁路写 `events.jsonl`。

目标：

- 对照旧系统和 V2 的运行终态。
- 找出异常、停止、刷新不一致来源。

### 阶段 C：只读调试接口

新增：

```text
GET /runtime-v2/state
GET /runtime-v2/sessions/{id}/events
GET /runtime-v2/runs
```

目标：

- 不改变 UI。
- 可观察 V2 是否比旧状态更准确。

### 阶段 D：局部接管运行态

让 `/sessions/state` 的 active runs 来源切到 Runtime V2。

优先解决：

- 黄点卡住。
- 发送按钮不恢复。
- 停止后状态延迟。
- 流式异常后刷新卡 loading。

### 阶段 E：接管 SSE

新增：

```text
GET /runtime-v2/events?session_id=...&after_seq=...
```

目标：

- SSE 重连去重。
- 黑屏/睡眠后恢复。
- 异常一定收到 `run_failed` 或可通过 state 恢复。

### 阶段 F：接管消息历史

消息区从 Runtime V2 event log 回放。

目标：

- 历史回放和实时流同源。
- 加载更早历史不与实时事件冲突。
- 消息不会因为多文件状态不同步而消失。

## 和原 1-10 阶段重构的关系

Runtime V2 是原重构方案的干净新内核版本。

| 原阶段 | Runtime V2 覆盖情况 |
|---|---|
| 1 前端状态核心 | 后续通过 snapshot/event 简化 |
| 2 会话列表刷新 | SessionRepository + state 覆盖 |
| 3 后端状态快照 | RuntimeGateway.get_state 覆盖 |
| 4 SSE seq 化 | StreamPublisher + after_seq 覆盖 |
| 5 运行状态统一 | RunRegistry 覆盖 |
| 6 消息渲染重构 | EventLog 提供统一数据源 |
| 7 Subagent 状态 | SubagentRepository 覆盖 |
| 8 Context/Todo/Token | 标准事件覆盖来源 |
| 9 文件写入与落盘 | SessionEventLog + Repository 覆盖 |
| 10 构建保护 | 继续保留现有机制 |

## 成功标准

Runtime V2 完整接入后，应满足：

- 正常完成一定产生 `run_finished`。
- 手动停止一定产生 `run_interrupted`。
- 异常一定产生 `run_failed`。
- 刷新页面只需 state + after_seq 即可恢复。
- 左侧黄点与发送按钮来自同一事实源。
- 子 Agent 是否完成、是否有 final、结果是否读取过都有明确事件。
- 事件日志能重放出当前会话状态。
- metadata/index/snapshot 损坏时可重建。
