# LLM 传输层

执行器通过 `app/llm/` 的 provider adapter 访问模型。主循环只处理统一的
`TransportEvent`，不解析 OpenAI/Anthropic 的线协议：

```text
model profile / EXECUTOR_LLM_TYPE
        -> ExecutorLLMClient（候选模型与故障切换）
        -> Provider Registry
        -> provider adapter
        -> TransportEvent
        -> agent_openai / ReAct
```

## Provider 选择

| 配置值 | 线协议 |
| --- | --- |
| `auto` | OpenAI 官方域名走 Responses；Anthropic 官方域名走 Messages；其他端点走 Chat Completions |
| `openai`、`responses`、`@ai-sdk/openai` | OpenAI Responses |
| `openai-compatible`、`local`、`@ai-sdk/openai-compatible` | OpenAI-compatible Chat Completions |
| `anthropic`、`@ai-sdk/anthropic` | Anthropic Messages |

这里兼容的是三个 `@ai-sdk/*` provider 的配置语义和 HTTP 线协议；Python 后端不会为每次
请求启动 Node。手动选择始终优先，因此自定义 Responses 代理必须选 `openai`，自定义
Anthropic Messages 代理必须选 `anthropic`。未知值直接报错，不会静默改成另一种协议。

Responses 档案的 `thinking_mode` 和 `reasoning_effort` 留空时均不发送。只有用户明确填写
后才会生成对应请求字段。

## Responses 自动状态机

Runtime V2 投影的当前有效模型历史是唯一事实源。`response_id`、能力探测和 compact
checkpoint 都只是可丢弃的传输优化，不能代替本地历史。

- 官方 OpenAI 默认发送 `store=true`。只有紧邻历史头的 `ContinuationAnchor` 同时通过
  issuer、模型、历史 generation、请求形状和 canonical 前缀校验时，才发送
  `previous_response_id + 新增 user/tool items`。
- 自定义 Responses 代理默认 `store=false`，完整回放原生 output items，并在支持时请求
  `reasoning.encrypted_content`。
- `previous_response_id` 不存在、过期或删除时，在尚未产生可见输出的前提下只完整重试
  一次；官方 OpenAI 以 `store=true` 建立新链，不会向后扫描旧 response ID。
- 代理拒绝 `store`、`previous_response_id`、加密 reasoning include 或 compact 时，只降低
  对应 issuer 的单项能力；429、timeout 和 5xx 不会被误记为“不支持”。
- system/developer 内容每轮都重新作为 `instructions` 发送，因为 Responses continuation
  不继承上一轮 instructions。

用户不再选择 stateful/stateless。模型配置仅保留“禁止服务端存储”隐私开关；启用后固定
`store=false` 且不发送 `previous_response_id`。旧 `responses_state_mode=stateless` 自动迁移
为该开关，旧 `stateful` 迁移为自动模式，不能强制自定义代理建立不安全的状态链。

issuer 指纹包含 provider、Base URL、model、凭据作用域、organization 和 project。原生
output/encrypted reasoning/compact item 只在相同 issuer 内回放；切换端点、模型或凭据后会
从当前本地历史重建请求。

## Responses WebSocket

`RESPONSES_WEBSOCKET_MODE=auto|enabled|disabled` 控制可选传输，默认 `auto`。`auto` 和
`enabled` 都只允许 OpenAI 官方域名使用 SDK 的持久 `/v1/responses` WebSocket；自定义
Responses 代理始终保持 HTTP/SSE，不会因为 SDK 存在 `responses.connect()` 就被盲探测。

一个 issuer 复用一条串行连接。WebSocket 在尚未产生可见输出时握手或请求失败，本逻辑请求
只降级一次到同请求体的 HTTP/SSE；HTTP 仍失败后才交给候选模型切换。404、405、501 会在
issuer 能力缓存中临时标记 WebSocket 不支持，timeout、429、鉴权错误和 5xx 不修改能力。
连接在未读完响应时被取消会直接丢弃，避免残留帧污染下一轮。

Executor facade 自己拥有候选模型切换，外层首-token hedge（agent_openai）仍会在
`OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC` 内无首 token 时并发复制整个逻辑请求（含候选循环），
用于救援"既不失败也不吐字"的卡死连接；两路共享同一个 `_LogicalRequestBudget`，因此物理请求
总数不会超过 `OPENAI_TOTAL_REQUEST_BUDGET` / `OPENAI_MAX_INFLIGHT_REQUESTS`，落败的 hedge
流在胜者产生后被关闭、不会发出模型切换状态。同一 Runtime V2 run 的失败模型熔断状态跨
executor 客户端的短 TTL 重建复用，模型切换状态只由胜出的那个逻辑请求发出。若需彻底关闭门面
路径的 hedge，设 `OPENAI_HEDGE_MAX_ATTEMPTS=0`。

## Canonical item 与历史变更

每次 Responses 终包的原生 output items 会以 schema v2 保存到对应 assistant turn：

```text
AssistantMessage.additional_kwargs._myagent_responses
    canonical_output_items   可回放事实
    continuation_anchor      可丢弃优化
```

旧 schema 只作为 replay material，永远不能直接续链。Runtime V2 的普通 user/tool/assistant
尾部追加不改变 `model_history_generation`；改写、删除、回退、分支、本地压缩、模型窗口变化
等非追加操作会递增 generation，并清除 continuation/compact 优化。分支保留 canonical
output items，但剥离父会话 response ID 和 anchor。

## 压缩

达到上下文阈值时，主循环先调用当前 Responses provider 的 `responses.compact`：

1. 用完整 canonical input、instructions 和稳定的 session prompt cache key 请求 compact；
2. 验证结果包含 opaque `compaction` item；
3. 将 checkpoint 连同源 generation 和前缀证明写入 Runtime V2；
4. 下一请求发送 compact output 加 checkpoint 之后的新尾部，并建立新的 continuation anchor。

逻辑历史本身没有被官方 compact 改写，因此 checkpoint 只要源 generation/前缀仍匹配即可继续
使用；任意非追加历史变化会自动清除它。端点没有 compact 能力或调用失败时，继续使用现有本地
摘要/截尾策略。本地 fallback 会通过 `model_history_replaced` 生成新 generation，并剥离旧
response ID/anchor，不会伪造 OpenAI compaction item。

## Prompt cache 与单次任务

主会话使用按 lineage/session、purpose、issuer 和 model 命名空间化的稳定
`prompt_cache_key`，不会随普通轮次或 generation 抖动。分支可继承共同 lineage。

Goal Judge、标题、摘要、安全审查和诊断请求使用独立 `LLMRequestPurpose`，并固定为
stateless 单次调用；它们不会读取、安装或修改主对话 continuation anchor，也不会把会话 ID
放进模型消息。除 Goal Judge 固定使用当前候选外，其余后台文本任务通过候选模型各自的原生
adapter 调用；某个候选只返回推理、空正文、拒绝或协议错误时，会继续尝试下一候选，不共享
主 ReAct 的重试预算。

## 统一流事件

adapter 输出 `content_delta`、`reasoning_delta`、`tool_call_delta`、`usage`、内部
`provider_state` 和 `finish`。Responses 工具聚合同时处理 item added、arguments delta/done、
item done 以及 completed/incomplete 最终快照。参数先到也不会丢失后到的函数名和 call ID；
只有最终快照的代理同样可以恢复完整工具调用。文本快照只补发未出现的后缀。

主 ReAct 只有在没有任何可见正文、思考或工具增量时才允许切换备用 provider；产生输出后失败
会直接报告，避免把两个模型的结果拼成一条 assistant turn。摘要等后台纯文本流会先缓冲正文前
的推理事件；如果候选最终没有正文，这些事件不会外发，并安全切换到下一候选。
