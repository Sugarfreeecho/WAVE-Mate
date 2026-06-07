# MyAgent Developer 工程规格说明

版本日期：2026-06-07

## 1. 项目定位

MyAgent Developer 是一个本地运行的 AI Agent 开发与使用平台。系统通过浏览器 Web UI 提供会话式交互，由 Python FastAPI 后端驱动 ReAct 推理循环、工具调用、子 Agent 编排、上下文压缩、MCP 扩展和会话持久化。

本工程的目标是让用户在本机完成代码开发、文件处理、联网检索、研究分析、文档生成和多步骤自动化任务，同时保留可审计的会话记录、工具过程和运行日志。

## 2. 运行形态

### 2.1 生产运行

- 入口脚本：`RUN.bat`
- Python 入口：`app/main.py`
- 默认服务地址：`http://127.0.0.1:8192/`
- 后端应用对象：`app/webui.py` 中的 `fastapi_app`
- 前端产物目录：`app/templates/dist/`

启动流程：

1. `RUN.bat` 设置 UTF-8 输出和内置 Python 路径。
2. `RUN.bat` 启动 `app/tray_launcher.py`。
3. 后端最终通过 `app/main.py` 启动 FastAPI/uvicorn。
4. `app/main.py` 调用 `refresh_executor_client_from_env()` 刷新 LLM 配置。
5. 服务监听后自动打开浏览器，除非 `OPEN_BROWSER=0/false/no/off`。

### 2.2 前端开发运行

后端开发服务：

```bash
cd app
python -m uvicorn webui:fastapi_app --reload --port 8000
```

前端开发服务：

```bash
cd frontend
npm install
npm run dev
```

Vite 开发服务器默认端口为 `5173`，并代理：

- `/sessions` -> `http://127.0.0.1:8000`
- `/api` -> `http://127.0.0.1:8000`

### 2.3 前端构建

```bash
cd frontend
npm run build
```

构建输出必须写入 `app/templates/dist/`。后端主页优先服务该目录中的 `index.html`。如果构建产物缺失，后端应显示构建提示页，而不是回退到过期 UI。

## 3. 技术栈

### 3.1 后端

- Python 3.10，工程内置运行时位于 `python/`
- FastAPI + uvicorn
- OpenAI 兼容 API 客户端
- SSE 事件流
- MCP Python SDK
- python-dotenv 环境变量加载
- 文件、网络、PDF、Office、数据分析相关依赖见 `app/requirements.txt`

### 3.2 前端

- Vite
- 原生 JavaScript ES Module
- 原生 CSS
- 构建入口：`frontend/index.html`
- 运行入口：`frontend/src/main.js`
- UI 引导器：`frontend/src/app/index.js`

前端当前采用“按功能拆模块，但共享全局状态”的迁移形态。模块加载顺序仍然重要，后续重构必须保持旧执行顺序或显式声明模块依赖。

## 4. 目录职责

| 路径 | 职责 |
| --- | --- |
| `app/` | Python 后端、Agent 核心、路由、工具、配置页模板 |
| `app/templates/` | 后端 HTML 模板和 Vite 生产构建产物 |
| `app/tools/` | 工具辅助资源，例如 tokenizer |
| `frontend/` | Vite 前端源码 |
| `frontend/src/app/modules/` | 前端会话、SSE、消息渲染、子 Agent、设置、TOC/Todo 等功能模块 |
| `python/` | 内置 Python 运行时与依赖 |
| `workspace/` | 默认工作区、会话数据、技能目录、用户产物和临时分析文件 |
| `logs/` | 运行/对话日志 |

## 5. 核心后端模块

### 5.1 `app/webui.py`

系统的 HTTP/SSE 边界层，负责：

- 服务 Web UI 首页和静态资源。
- 创建、读取、删除、重命名、归档、置顶会话。
- 接收用户聊天请求。
- 建立会话 SSE 事件流。
- 暴露历史消息、用户轮次、Todo、上下文 token 估算。
- 处理工具审批。
- 管理子 Agent 列表、输出、停止和删除。
- 提供首次配置、高级环境变量配置和 MCP 配置页面。
- 对未配置状态进行中间件拦截，引导用户进入 `/setup`。

### 5.2 `app/agent.py`

对外轻量入口，导出：

- `astream_events`
- `astream_events_continuation`
- `session_manager`

其他模块或脚本应优先从这里导入 Agent 流式能力，而不是直接耦合内部实现。

### 5.3 `app/agent_harness.py`

Agent 调度与持久化核心，负责：

- 加载 `app/.env`。
- 解析 prompt 模板。
- 创建和刷新 OpenAI 兼容客户端。
- 管理 executor 模型调用、流式调用和 usage。
- 序列化/反序列化消息。
- 从 UI 事件重建核心消息。
- 管理 `llm_history`、`dialogue_history`、`key_context`、`metadata` 等会话文件。
- 估算 token。
- 支持历史截断、分支、压缩摘要和 key context 合并。
- 处理模型 reasoning/thinking 相关兼容逻辑。

### 5.4 `app/agent_loop.py`

ReAct 执行循环，负责：

- 调用 LLM。
- 解析 assistant tool calls。
- 执行内置工具和 MCP 工具。
- 将工具 pending、工具结果、LLM delta、进度提示、最终回答等事件推送到 SSE。
- 支持工具审批等待。
- 支持中断检查。
- 清理临时写入文件。
- 处理 API 错误分类和最终输出校验。
- 提供普通运行和 continuation 运行。

### 5.5 `app/agent_tools.py`

内置工具层，提供：

- 文件工具：`read_file`、`write_file`、`edit_file`、`delete_file`
- 目录和搜索：`ls`、`glob`、`grep`
- 命令执行：`run_shell`
- 网络工具：`web_search`、`web_fetch`、`web_download`
- 技能系统：`discover_skills`、`get_skills_catalog`、`activate_skill`
- 任务管理：`update_todo`
- 上下文管理：`context_manage`
- 子 Agent 入口：`task`

工具层必须继续承担路径限制、敏感信息脱敏、输出截断、SSRF 防护、危险命令判断和 shell 超时控制。

### 5.6 `app/agent_subagent.py`

子 Agent 编排层，负责：

- 过滤子 Agent 可用工具。
- 构造子 Agent 用户消息和附件上下文。
- 启动单个子 Agent。
- 支持 `best-of-n` 多路并行策略。
- 维护父子会话关系。
- 汇总子任务结果。
- 支持中断和清理。
- 在需要时创建/清理 git worktree 隔离环境。

### 5.7 `app/agent_memory.py`

上下文策略层，负责：

- 估算完整上下文包大小。
- 判断是否触发压缩。
- 执行渐进式压缩、微压缩、摘要合并和应急裁剪。
- 维护 `key_context` 中的压缩摘要。
- 尽量保留近期真实用户轮次和任务关键状态。

### 5.8 `app/agent_mcp.py`

MCP 扩展层，负责：

- 读取 MCP 配置。
- 启动 stdio、SSE、streamable-http MCP server。
- 将 MCP tool schema 转为 OpenAI tool definition。
- 调用 MCP 工具。
- 格式化 MCP 工具返回结果。
- 支持强制重载和统一关闭。

### 5.9 会话生命周期模块

`app/session_lifecycle.py` 负责：

- 标记会话删除状态。
- 注册运行任务。
- 判断会话是否正在运行。
- 取消指定会话或会话树的运行任务。

`app/session_event_bus.py` 负责：

- 发布会话事件。
- 订阅会话事件。
- 剪裁短期事件缓存。
- 关闭指定会话的流。

## 6. 前端模块规格

### 6.1 页面结构

- `frontend/index.html` 是页面 shell。
- `frontend/src/shell-body.html` 承载主体 HTML 片段。
- `frontend/src/main.js` 引入 CSS、路径选择器和 UI 入口。
- `frontend/src/app/index.js` 负责按既有顺序初始化 UI 模块。

### 6.2 功能模块

| 模块 | 职责 |
| --- | --- |
| `config.js` | 读取运行时配置 |
| `shared-state-and-dialogs.js` | 共享状态、弹窗、提示 |
| `settings.js` | 设置面板与配置交互 |
| `session-management.js` | 会话列表、切换、发送、中断、重发、归档、置顶、删除 |
| `sse-handling.js` | 建立和处理 SSE 流 |
| `message-rendering.js` | 渲染用户、assistant、工具、进度、最终回答等消息 |
| `session-scroll-history.js` | 历史分页、滚动跟随、上下文 token 标签、流式 DOM 状态 |
| `subagent.js` | 子 Agent 面板、卡片、增量同步、展开折叠、停止和删除 |
| `toc-todo.js` | 会话目录、Todo 面板、hover tooltip |
| `layout-panels.js` | 布局面板状态 |
| `event-dispatch.js` | 前端事件分发协调 |

### 6.3 前端行为要求

- 正在运行的会话切换回来时，应能恢复或同步流式状态。
- 历史消息应支持按需加载，避免一次性渲染超大 DOM。
- 子 Agent 详情应懒加载，并支持卡片级增量同步。
- 工具调用应区分 pending、streaming、done、error 和 approval required。
- Todo 和 TOC 应随会话切换清理并重新加载，不能显示上一会话残留状态。
- 前端对长文本、工具输出和流式块应做折叠、溢出处理和复制支持。

## 7. API 规格

### 7.1 页面与静态资源

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | Web UI 首页 |
| GET | `/static/myagent_path_picker.js` | 路径选择器脚本 |
| GET | `/setup` | 首次配置页 |
| GET | `/setup/env` | 高级环境变量配置页 |
| GET | `/setup/mcp` | MCP 配置页 |

### 7.2 会话

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/sessions?include_archived=false` | 获取会话列表 |
| POST | `/sessions` | 创建会话 |
| GET | `/sessions/{session_id}` | 获取会话详情 |
| DELETE | `/sessions/{session_id}` | 删除会话 |
| PUT | `/sessions/{session_id}/name` | 重命名会话 |
| PUT | `/sessions/{session_id}/archive` | 归档/取消归档 |
| PUT | `/sessions/{session_id}/pin` | 置顶/取消置顶 |
| POST | `/sessions/{session_id}/interrupt` | 中断会话运行 |
| POST | `/sessions/{session_id}/truncate` | 截断会话事件 |
| POST | `/sessions/{session_id}/branch` | 从指定位置创建分支会话 |
| POST | `/sessions/{session_id}/append_ui_events` | 追加 UI 事件 |

### 7.3 聊天与流

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/chat` | 提交用户消息并启动 Agent 运行 |
| GET | `/sessions/{session_id}/stream` | 订阅会话 SSE 事件 |
| POST | `/sessions/{session_id}/continue` | 继续 ReAct 会话 |
| POST | `/sessions/{session_id}/continue-subagents` | 子 Agent 完成后继续父会话 |
| POST | `/sessions/{session_id}/continue-subagents/dismiss` | 忽略继续提示 |

### 7.4 消息与状态

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/sessions/{session_id}/messages` | 获取会话消息，支持分页/轮次参数 |
| GET | `/sessions/{session_id}/messages/count` | 获取消息数量 |
| GET | `/sessions/{session_id}/user_turns` | 获取用户轮次 |
| GET | `/sessions/{session_id}/todo_plan` | 获取 Todo 计划 |
| DELETE | `/sessions/{session_id}/todo_plan` | 清空 Todo 计划 |
| GET | `/sessions/{session_id}/context_tokens` | 获取上下文 token 估算 |

### 7.5 子 Agent

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/sessions/{session_id}/subagents` | 获取子 Agent 树/列表 |
| GET | `/sessions/{parent_id}/subagents/{task_id}/output` | 获取子 Agent 输出 |
| POST | `/sessions/{parent_id}/subagents/{child_id}/interrupt` | 中断子 Agent |
| DELETE | `/sessions/{parent_id}/subagents/{child_id}` | 删除子 Agent |

### 7.6 配置

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/env` | 获取环境变量快照 |
| POST | `/api/env` | 保存环境变量更新 |
| POST | `/api/save_config` | 保存首次配置 |
| GET | `/api/mcp_config` | 获取 MCP 配置 |
| POST | `/api/mcp_config` | 保存 MCP 配置 |
| POST | `/api/pick-path` | 调用本机路径选择 |
| GET | `/api/open-workspace-file` | 打开 workspace 文件 |

### 7.7 工具审批

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/sessions/{session_id}/tool-approval` | 提交用户对待审批工具调用的允许/拒绝决定 |

## 8. SSE 事件规格

SSE 是后端向前端展示 Agent 过程的主通道。事件至少应覆盖以下语义：

- LLM 文本 delta
- LLM reasoning/thinking delta
- 工具调用开始
- 工具命令/参数展示
- 工具执行 pending
- 工具审批 required
- 工具执行结果
- 进度提示
- Todo 更新
- 子 Agent 开始/更新/完成
- 最终回答
- 错误
- 会话关闭/中断

所有 SSE 事件必须包含足够信息让前端在以下场景恢复 UI：

- 当前会话实时运行。
- 用户切走后切回。
- 页面刷新后从持久化消息重建。
- 子 Agent 卡片懒加载详情。

## 9. 数据持久化规格

默认会话根目录位于 `workspace/sessions/`。每个会话目录通常包含：

| 文件 | 说明 |
| --- | --- |
| `metadata.json` | 会话名称、归档、置顶、更新时间等元数据 |
| `ui_events.json` | 前端可重放事件流 |
| `work_messages.json` | Agent 工作消息 |
| `llm_history.json` | 发送给模型或可重建模型上下文的历史 |
| `dialogue_history.json` | 面向对话显示/压缩的历史 |
| `key_context.md` | 压缩后的关键上下文 |
| `todo_plan.md` | Todo 计划 |
| `pending_subagent_results.json` | 子 Agent 待处理结果 |
| `subagent_tasks.json` | 子 Agent 任务索引 |
| `truncate_backups/` | 截断前备份 |

子 Agent 会话存放在父会话的 `subagents/{child_id}/` 下，结构尽量与主会话一致，并可额外包含 `output.md`。

全局会话索引：

- `workspace/sessions/sessions.json`
- `workspace/sessions/subagent_index.json`

持久化要求：

- 写入 JSON 时必须保证可恢复，避免半写入破坏会话。
- 历史截断、分支、压缩前应保留必要备份或边界标记。
- 子 Agent 输出必须可从父会话索引追溯。
- UI 事件和 LLM 历史可以不同步，但必须能通过修复/重建逻辑恢复到可显示状态。

## 10. 消息模型

后端轻量消息类型定义在 `app/agent_messages.py`：

- `UserMessage`
- `SystemMessage`
- `AssistantMessage`
- `ToolMessage`

要求：

- 消息类型命名必须与历史落盘结构兼容，不应随意重命名。
- `UserMessage.content` 支持字符串和多模态数组。
- `AssistantMessage` 支持 `tool_calls`、`metadata` 和 `additional_kwargs`。
- `ToolMessage` 必须带 `tool_call_id`，用于对应 assistant 的工具调用。
- 序列化和反序列化逻辑由 `agent_harness.py` 统一维护。

## 11. 配置规格

主要配置文件：`app/.env`

### 11.1 LLM 配置

典型字段：

- `EXECUTOR_LLM`
- `EXECUTOR_LLM_TYPE`
- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- `CONTEXT_WINDOW`
- `MAX_OUTPUT_TOKENS`
- `LLM_THINKING_MODE`
- `LLM_REASONING_EFFORT`

要求：

- 修改 LLM 配置后必须刷新 executor client。
- API key 等敏感字段在 UI、日志和工具输出中必须脱敏。
- OpenAI 兼容接口差异应在 `agent_harness.py` 或 `agent_openai.py` 中适配，避免散落到业务层。

### 11.2 工作区配置

典型字段：

- `WORK_DIR`
- `LOG_DIR`

要求：

- 相对路径应相对工程根目录解析。
- 工作区变化可能需要重启才能完全生效。
- 文件工具默认限制在工作区内，越界操作必须经过限制或审批。

### 11.3 搜索与网络配置

典型字段：

- `WEB_SEARCH_PROVIDER`
- `TAVILY_API_KEY`
- Brave/SearXNG/Jina 等 provider 相关配置

要求：

- 网络搜索 provider 不可用时应有明确错误。
- `web_fetch` 和 `web_download` 必须保留 SSRF 防护和下载大小限制。

### 11.4 MCP 配置

示例文件：`app/mcp_servers.json.example`

支持 transport：

- `stdio`
- `sse`
- `streamable-http`

要求：

- MCP 配置保存后应支持重新加载。
- MCP 工具名必须经过安全映射，避免函数名冲突或非法字符。
- MCP 工具是否需要 UI 审批由 `agent_mcp.py` 和审批层共同决定。

## 12. 安全规格

### 12.1 文件系统

- 默认文件操作必须限制在 `WORK_DIR`。
- 删除文件应采用软删除或受控删除策略。
- 敏感资源，如 `.env`、密钥文件、配置二进制等，不应被工具结果直接泄露。
- 路径解析必须处理 Windows/Posix 差异、引号、重定向和 shell token。

### 12.2 Shell

- `run_shell` 必须支持超时、中断、输出截断和二进制输出摘要。
- 危险命令必须被识别或要求用户审批。
- 工作区外路径访问必须受限。
- Windows 下 Bash、PowerShell、CMD 的执行差异必须集中在工具层处理。

### 12.3 网络

- `web_fetch` 和 `web_download` 必须阻止访问本机、内网、保留地址等 SSRF 风险目标。
- 重定向目标必须重新校验。
- 下载必须有最大字节数限制。

### 12.4 敏感信息

- 日志、工具输出、UI 预览和模型上下文中应尽量脱敏 API key、token、secret 等字段。
- 环境变量高级配置页应标记敏感字段，并避免明文回显不必要内容。

### 12.5 审批

- 工具审批通过 `/sessions/{session_id}/tool-approval` 完成。
- 审批状态必须绑定具体 session 和 tool call，避免跨会话串扰。
- 用户拒绝时，Agent 应收到结构化的拒绝结果，而不是静默失败。

## 13. 上下文管理规格

系统必须支持长会话运行，核心策略：

1. 保留近期用户轮次。
2. 对较旧工具结果和长文本进行微压缩。
3. 将早期对话压缩进 `key_context.md`。
4. 在上下文压力过高时进行应急裁剪。

要求：

- 压缩不得丢失当前任务目标、用户明确约束、未完成 Todo 和关键文件路径。
- 压缩边界必须在历史中可识别。
- 压缩后的 `key_context` 不应混入 Todo 计划正文，二者应可分离解析。
- 前端应能显示当前上下文 token 估算。

## 14. 子 Agent 规格

子 Agent 用于把复杂任务拆成隔离运行单元。

要求：

- 子 Agent 必须拥有独立 session id。
- 子 Agent 的事件和输出必须能在父会话中追踪。
- 父会话应能知道子 Agent running/completed/failed/interrupted 状态。
- `best-of-n` 运行必须能汇总多个候选结果。
- 子 Agent 默认不得污染父 Agent 的核心历史，除非结果被显式汇总。
- 中断父会话树时应能取消相关子 Agent 任务。

## 15. 技能系统规格

技能位于 `workspace/skills/` 或其他配置路径下，每个技能至少包含 `SKILL.md`。

要求：

- `discover_skills` 应能扫描技能目录。
- `activate_skill` 应按需加载技能说明。
- 技能加载结果应进入 Agent 可见上下文，但避免一次性加载大量无关引用。
- 技能脚本和资源路径必须相对技能目录解析。

## 16. 日志与可观测性

日志目录：`logs/`

要求：

- 每次会话/用户输入应能生成可追踪日志。
- 日志中应包含必要的模型、工具、错误和运行过程信息。
- 日志中必须脱敏敏感配置。
- 前端显示的工具过程与后端日志应能互相辅助排查。

## 17. 性能规格

### 17.1 后端

- SSE 事件推送不得因长工具调用完全静默，应有 keepalive 或进度事件。
- 大工具输出必须截断或写临时文件后摘要返回。
- 上下文 token 估算和压缩应避免阻塞 UI 主流程过久。
- 会话删除、中断和子 Agent 停止必须及时释放运行任务。

### 17.2 前端

- 历史消息应分页/懒加载。
- 子 Agent 详情应懒加载，避免大量卡片一次性渲染完整过程。
- 流式文本应批量 flush，避免每个 token 都触发布局。
- TOC/Todo/上下文 token 刷新应异步调度，避免切换会话卡顿。

## 18. 验收标准

### 18.1 启动验收

- 运行 `RUN.bat` 后服务可访问 `http://127.0.0.1:8192/`。
- 缺少前端构建产物时显示明确构建提示。
- 已配置 `.env` 时不应误跳首次配置页。
- 修改 LLM 配置后重启或刷新配置可生效。

### 18.2 会话验收

- 可创建新会话。
- 可发送消息并收到流式响应。
- 可切换会话并恢复历史。
- 可重命名、归档、置顶、删除会话。
- 删除运行中会话时应中断对应运行任务。

### 18.3 工具验收

- 文件读写编辑能在 `WORK_DIR` 内正常执行。
- 工作区外或高风险操作会被限制或触发审批。
- shell 命令支持超时、中断和输出截断。
- 网络抓取阻止内网/本机地址。
- 工具错误能清晰反馈给前端和 Agent。

### 18.4 子 Agent 验收

- 主 Agent 可通过 `task` 启动子 Agent。
- 子 Agent 状态在前端面板可见。
- 子 Agent 输出可展开查看。
- 可中断和删除子 Agent。
- 子 Agent 完成后父会话可继续处理结果。

### 18.5 上下文验收

- 长会话达到阈值后可触发压缩。
- 压缩后仍能保留当前任务目标和近期对话。
- `key_context.md`、`todo_plan.md` 可分别读取。
- 前端上下文 token 显示不阻塞主交互。

### 18.6 前端验收

- 生产构建成功写入 `app/templates/dist/`。
- 会话切换不残留上一会话的 TOC/Todo/子 Agent 状态。
- SSE 流式输出、工具过程、最终回答均能正确渲染。
- 长消息、长工具输出、子 Agent 历史不会造成明显卡顿。

## 19. 变更约束

- 修改前端源码后必须运行 `npm run build`，确保生产 UI 更新到 `app/templates/dist/`。
- 修改路由时必须同步更新本 spec 的 API 表。
- 修改会话落盘结构时必须考虑旧会话兼容和迁移。
- 修改消息类型名称或序列化字段属于高风险变更，必须提供兼容层。
- 修改工具安全策略时必须补充越界路径、危险命令、敏感信息和 SSRF 测试。
- 修改上下文压缩策略时必须用长会话样例验证任务目标不丢失。

## 20. 已知工程特征

- 工程包含内置 Python，因此可在未安装系统 Python 的 Windows 环境运行。
- `workspace/` 同时承载默认工作区、会话、技能和用户产物，后续如要分离，需要迁移配置和历史路径。
- 当前前端模块化仍处于渐进迁移状态，存在共享全局状态，重构时要特别注意加载顺序。
- 旧文档中存在编码显示异常的内容，后续文档建议统一保存为 UTF-8。
