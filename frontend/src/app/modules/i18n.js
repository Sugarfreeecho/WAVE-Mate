// Lightweight UI internationalisation. The UI is rendered by several legacy
// modules, so translations are applied at the DOM boundary (including nodes
// added later) instead of coupling every renderer to a framework.
const LS_UI_LANGUAGE = 'myagent-language';
const UI_TRANSLATIONS_EN = {
    '自优化通用智能平台': 'Self-optimizing general intelligence platform',
    '界面设置': 'Interface settings', '新建会话': 'New session', '新建对话': 'New chat', '切换语言': 'Switch language', '切换为英文': 'Switch to English', '切换为中文': 'Switch to Chinese',
    '会话列表': 'Session list', '拖动调整侧栏宽度': 'Drag to resize sidebar', '聊天': 'Chat',
    '选择或新建会话': 'Select or create a session', '展开 Subagent 面板': 'Expand Subagent panel',
    '会话扩展面板': 'Session extensions', '折叠会话扩展面板': 'Collapse session extensions',
    '消息': 'Messages', '历史记录': 'History',
    '折叠历史面板': 'Collapse history panel', '继续综合子任务': 'Continue synthesizing subtasks',
    '撤销': 'Undo', '说说你想做什么…（Enter 发送 · Shift/Ctrl/Cmd + Enter 换行）': 'What would you like to do? (Enter to send · Shift/Ctrl/Cmd + Enter for a new line)', 'Agent运行中，输入后续任务': 'Agent is running; enter a follow-up task', '按 Enter 发送刚加入或第一条待发送任务': 'Press Enter to send the newly queued or first pending task', '选择文件': 'Choose file',
    '选择 Skill': 'Select Skill', '发送 / 停止': 'Send / Stop', '发送': 'Send', '停止': 'Stop',
    'Enter 提交': 'Enter to submit', 'Ctrl/Cmd + Enter 提交': 'Ctrl/Cmd + Enter to submit',
    'Ctrl/Cmd + Enter 保存修改': 'Ctrl/Cmd + Enter to save changes',
    'Ctrl/Cmd + Enter 确认回答': 'Ctrl/Cmd + Enter to confirm', 'Ctrl/Cmd + Enter 确认并进入下一题': 'Ctrl/Cmd + Enter to confirm and continue', 'Ctrl/Cmd + Enter 提交答案': 'Ctrl/Cmd + Enter to submit answers',
    '搜索工作区文件（↑↓ 移动 · Enter 选择 · Esc 关闭）': 'Search workspace files (↑↓ Move · Enter to select · Esc to close)',
    '模型': 'Model', '正在加载模型配置': 'Loading model configuration', '模型配置': 'Model configuration',
    '操作提示': 'Notifications', '已复制': 'Copied', '提示': 'Notice', '取消': 'Cancel', '确定': 'Confirm',
    '暂停': 'Pause', '继续': 'Resume', '无限制': 'Unlimited', '分钟': 'min',
    '进行中': 'Active', '已暂停': 'Paused', '已完成': 'Completed', '已阻塞': 'Blocked', '已取消': 'Cancelled',
    '失败': 'failures', '已消耗': 'used', '用时': 'elapsed', '小时': 'h', '分': 'm', '秒': 's',
    '最近错误': 'Latest error', '统计信息': 'Statistics', 'Ctrl/Cmd + Enter 保存': 'Ctrl/Cmd + Enter to save', '保存修改': 'Save changes',
    '确认删除': 'Delete',
    '关闭': 'Close', '语言': 'Language', '中文': 'Chinese', '英文': 'English',
    '字体大小': 'Font size', '小号': 'Small', '标准': 'Default', '大号': 'Large',
    '界面风格': 'Appearance', '深色': 'Dark', '紫色': 'Purple', '浅色（默认）': 'Light (default)',
    '会话目录': 'Session list', '会话目录风格': 'Session list style', '会话目录显示模式': 'Session list display mode',
    '紧凑': 'Compact', '详细': 'Detailed', '环境与 API': 'Environment & API', '高级设置': 'Advanced settings',
    '编辑完整 .env，保存后立即写回磁盘（部分项需重启服务）。': 'Edit the complete .env file. Changes are saved to disk immediately (some require a service restart).',
    '如需帮助或反馈，请联系GitHub @sugarfreeecho': 'For help or feedback, contact @sugarfreeecho on GitHub.',
    '运行诊断': 'Diagnostics',
    '更多操作': 'More actions', '更多': 'More', '删除': 'Delete', '置顶': 'Pin', '取消置顶': 'Unpin',
    '归档': 'Archive', '取消归档': 'Unarchive', '删除会话': 'Delete session', '此操作不可恢复': 'This action cannot be undone',
    '未命名': 'Untitled', '重新加载': 'Reload', '加载中...': 'Loading...', '加载中…': 'Loading…',
    '生成中': 'Generating', '任务失败，点击查看': 'Task failed — click to view', '有新回复，点击查看': 'New response — click to view',
    '追问': 'Follow up', '立即发送': 'Send now', '撤回': 'Withdraw', '待发送': 'Pending', '发送中': 'Sending',
    '打断': 'Interrupt', '追加': 'Append', '追问发送模式': 'Follow-up mode', '已追加，等待下一轮': 'Appended, waiting for the next round',
    '已发送': 'Sent', '提交中': 'Submitting', '撤回中': 'Withdrawing', '已接收，等待插入': 'Received, waiting to insert',
    '正在接管当前任务': 'Taking over the current task', '选择 Skill ': 'Select Skill ', '清空': 'Clear',
    '当前没有已注册 Skill': 'No registered skills', '正在加载 Skill': 'Loading skills',
    'MCP 工具': 'MCP Tools', '正在加载 MCP 工具': 'Loading MCP tools', '当前没有已注册的 MCP 工具': 'No registered MCP tools', '当前没有已配置的 MCP 服务器': 'No configured MCP servers', 'MCP 工具加载失败': 'Failed to load MCP tools', '未命名服务器': 'Unnamed server', '未注册': 'Not registered', '服务器尚未完成工具注册；请检查连接、凭据或服务配置。': 'The server has not completed tool registration. Check the connection, credentials, or server configuration.', '未命名 Plugin': 'Unnamed plugin',
    'MCP 工具启停失败': 'Failed to change MCP tool status', '注册': 'Register', '注册中…': 'Registering…', 'MCP 注册失败': 'MCP registration failed', 'MCP 注册未完成': 'MCP registration incomplete',
    'Hooks': 'Hooks', 'Plugins': 'Plugins', '正在加载扩展': 'Loading extensions', '当前没有已注册 Hook': 'No registered hooks', '当前没有已发现插件': 'No plugins found', '扩展加载失败': 'Failed to load extensions', '无': 'None',
    '启用': 'Enable', '停用': 'Disable', '打开页面': 'Open page', '处理中…': 'Working…', '插件状态更新失败': 'Failed to update plugin status',
    '加载详情中…': 'Loading details…', '知道了': 'Got it', '允许执行': 'Allow', '拒绝': 'Deny',
    '需要确认': 'Confirmation required', '任务已中断': 'Task interrupted', '已请求停止当前任务': 'Stop requested',
    '展开': 'Expand', '收起': 'Collapse', '复制': 'Copy', '导出': 'Export', '导出选项': 'Export options', '导出图片': 'Export image', '导出文本': 'Export text', '改写': 'Rewrite', '重试': 'Retry',
    '点击查看图片': 'Click to view image', '使用系统应用打开': 'Open with system app',
    '无法在浏览器中播放此媒体，可使用系统应用打开。': 'This media cannot be played in the browser. Open it with a system app instead.'
};
Object.assign(UI_TRANSLATIONS_EN, {
    'Runtime 在线': 'Runtime online', 'Runtime 繁忙': 'Runtime busy', 'Runtime 待处理': 'Runtime awaiting input', 'Runtime 离线': 'Runtime offline',
    '已完成': 'Completed', '进行中': 'In progress', '待处理': 'Pending', '已暂停': 'Paused', '已阻塞': 'Blocked', '已取消': 'Cancelled',
    '无限制': 'Unlimited', '分钟': 'min', '继续': 'Continue',
    // Runtime status lines
    '正在思考中...': 'Thinking...', '正在重连': 'Reconnecting', '任务已中断': 'Task interrupted',
    '展开执行过程高度': 'Expand process height', '收起执行过程高度': 'Collapse process height',
    '任务已恢复，流程重启': 'Task restored; restarting workflow',
    '已请求停止当前任务': 'Stop requested for the current task', '解析事件失败': 'Failed to parse event',
    '验证': 'Verification', '正在根据对话更新要点': 'Updating key points from the conversation',
    '上下文窗口已满，开始压缩': 'Context window full; starting compression', '上下文压缩已完成': 'Context compression completed',
    '上下文摘要': 'Context summary', '要点': 'Key points', '历史/旧版事件': 'History/legacy event',
    '立即发送': 'Send now', '追问发送模式': 'Follow-up send mode', '打断': 'Interrupt', '追加': 'Append', '撤回': 'Withdraw',
    '撤回中': 'Withdrawing', '提交中': 'Submitting', '已追加，等待下一轮': 'Appended, waiting for the next round',
    '已接收，等待插入': 'Received, waiting to insert', '正在接管当前任务': 'Taking over the current task', '发送中': 'Sending', '已发送': 'Sent', '待发送': 'Pending send',
    '已选择 Skill：': 'Activated Skill: ', '激活 Skill：': 'Activated Skill: ', '追问接管已保留，等待发送通道释放。': 'Follow-up takeover retained; waiting for the send channel to become available.',
    '请求失败': 'Request failed', '撤销失败，请重试。': 'Undo failed. Please try again.'
});
Object.assign(UI_TRANSLATIONS_EN, {
    '新会话': 'New session', '停止 <span class="loader">': 'Stop <span class="loader">', '加载会话': 'Load session',
    '取消置顶': 'Unpin', '取消归档': 'Unarchive', '删除会话': 'Delete session', '此操作不可恢复': 'This action cannot be undone',
    '无法同步服务器。': 'Could not sync with the server.', '当前没有选中的会话。': 'No session is currently selected.',
    '消息定位索引无效，可能需要刷新当前会话。': 'The message index is invalid. Refresh the current session.',
    '服务端拒绝清空整个会话。': 'The server rejected clearing the entire session.',
    '服务端裁剪历史失败，可能是历史索引已变化或会话文件暂时不一致。': 'The server could not trim history; the index may have changed or the session file may be inconsistent.',
    '原因：': 'Reason: ', '无法改写': 'Cannot rewrite', '改写内容不能为空。': 'Rewrite content cannot be empty.',
    '生成中不可操作': 'Unavailable while generating', '当前会话仍在生成。请等待完成或停止后再修改历史。': 'This session is still generating. Wait for completion or stop it before editing history.',
    '无法删除该条': 'Cannot delete this message', '消息索引异常，已阻止清空整个会话。请刷新后再试。': 'The message index is invalid; clearing the session was blocked. Refresh and try again.',
    '删除消息': 'Delete message', '将同步到服务器': 'Will sync to server', '确定删除本条及之后的所有对话内容吗？': 'Delete this message and all following conversation content?',
    '同步失败': 'Sync failed', '删除未生效。': 'The deletion was not applied.', '无法改写该条': 'Cannot rewrite this message',
    '该消息尚未与服务器索引对齐，请刷新当前会话后再试。': 'This message is not aligned with the server index. Refresh the session and try again.',
    '无法分支': 'Cannot fork', '该回答尚未与服务器同步，请刷新页面后重试。': 'This response is not synced with the server. Refresh and try again.',
    '创建分支会话': 'Create fork session', '原会话不会被修改': 'The original session will not be modified',
    '问题': 'Question', '已完成': 'Completed', '进行中': 'In progress', '未开始': 'Not started',
    '折叠文件夹': 'Collapse folder', '展开文件夹': 'Expand folder', '下载保存 Mermaid 流程图为图片': 'Download Mermaid diagram as image',
    '调用工具': 'Tool calls', '次': 'times', '轮': 'rounds', '分': 'm', '秒': 's',
    '工具调用生成中...': 'Preparing tool call...', '执行中...': 'Running...', '执行结果': 'Result',
    '平均': 'Average', '累计': 'Total', '占比': 'Share', '暂无数据': 'No data', '暂无执行统计': 'No execution statistics',
    '暂无子事件': 'No sub-events', '无用户消息': 'No user message', '成功': 'Success', '会话数': 'Sessions',
    'LLM 请求': 'LLM requests', 'LLM API 流累计': 'Cumulative LLM API stream', 'Run 总耗时（墙钟）': 'Run wall-clock total', '平均首 token': 'Average first token',
    '累计输入 token': 'Cumulative input tokens', '累计输出 token': 'Cumulative output tokens', '工具调用总数': 'Total tool calls',
    '累计网络流量': 'Cumulative network traffic', 'LLM API 流耗时': 'LLM API stream duration', '首 token': 'First token',
    '输入 token': 'Input tokens', '输出 token': 'Output tokens', '上下文长度': 'Context length', '工具调用': 'Tool calls',
    '网络等待': 'Network wait', '网络流量': 'Network traffic', '时间': 'Time', '执行轮次': 'Execution round',
    '模型': 'Model', '会话': 'Session', '运行 ID': 'Run ID', 'Session ID': 'Session ID', '当前会话筛选': 'Current session filter',
    '所有会话': 'All sessions', '累计总值': 'Cumulative totals', '平均首 token': 'Average first token'
});
Object.assign(UI_TRANSLATIONS_EN, {
    // Session navigation and lifecycle
    '置顶目录': 'Pinned', '归档目录': 'Archived', '刷新归档目录': 'Refresh archived sessions',
    '加载归档目录': 'Load archived sessions', '加载更多': 'Load more', '今天': 'Today', '昨天': 'Yesterday', '近7天': 'Last 7 days',
    '近14天': 'Last 14 days', '加载会话': 'Load session', '加载会话列表失败': 'Failed to load sessions',
    '加载历史消息失败': 'Failed to load message history', '创建新会话失败': 'Failed to create session',
    '未选择会话': 'No session selected', '暂无提问': 'No questions yet', '打开工作目录': 'Open workspace',
    '打开会话目录': 'Open session folder', '确定删除会话': 'Delete this session',

    // Subagents
    '后台运行': 'Running in background', '运行中': 'Running', '完成': 'Completed', '失败': 'Failed',
    '已中断': 'Interrupted', '缺少 final 结果': 'Missing final result', '查看输出': 'View output',
    '放大显示': 'Expand view', '在浮窗内全屏显示': 'Show full-size in overlay', '无 Subagent': 'No subagents',
    '(暂无事件)': '(No events yet)', '(暂无 final 结果)': '(No final result)', '(无输出)': '(No output)',
    '删除 Subagent': 'Delete subagent', '删除失败': 'Deletion failed',
    '将删除该 subagent 的会话记录、过程卡片及其嵌套子任务。该操作不可撤销。': 'This will delete the subagent session, process card, and nested subtasks. This cannot be undone.',
    '无法删除该 Subagent，请稍后重试。': 'Could not delete this subagent. Please try again later.',
    '继续任务': 'Continue task', '继续中…': 'Continuing…', '等待主任务完成': 'Waiting for main task',

    // Model and skill controls
    '默认方案': 'Default profile', '未命名方案': 'Unnamed profile', '未加载模型配置': 'Model configuration not loaded',
    '没有可用模型配置': 'No model configurations available', '没有启用的模型配置': 'No enabled model profiles',
    '暂无已保存模型配置，可到模型配置页中保存': 'No saved model configurations. Save one on the model configuration page.',
    '请稍候': 'Please wait', '模型配置加载失败': 'Failed to load model configuration', '切换失败': 'Switch failed',
    'Skill 加载失败': 'Failed to load skills', '启用': 'Enable', '禁用': 'Disable',
    '模型配置启停失败': 'Failed to change model profile status', 'Skill 启停失败': 'Failed to change Skill status',

    // Messages, history and composer
    '开始一段新的对话': 'Start a new conversation',
    '在左侧侧栏新建或选择会话。Enter 发送，Ctrl+Enter / Shift+Enter 换行。': 'Create or select a session in the sidebar. Press Enter to send; Ctrl+Enter or Shift+Enter for a new line.',
    '分支': 'Fork', '创建分支': 'Create fork', '创建失败': 'Creation failed',
    '将在当前回答之后创建独立分支会话。分支点之前的内容与原会话相同，可在分支中继续提问且不影响原会话。': 'A separate fork session will be created after this response. Earlier messages remain the same, and continuing in the fork will not affect the original session.',
    '创建分支未生效。': 'The fork was not created.', '工具': 'Tool', '执行过程': 'Execution process',
    '本段过程已折叠': 'This process section is collapsed', '信息': 'Info', '错误': 'Error', '回复': 'Response',
    '思考': 'Reasoning', '压缩': 'Compression', '裁剪': 'Trim', '要点': 'Key points', '状态': 'Status',
    '评审': 'Review', '结论': 'Verdict', '通过': 'Passed', '需要继续': 'More work required',
    '评审失败': 'Review failed', '理由': 'Reason', '失败类型': 'Failure type',
    '工具调用生成中...': 'Preparing tool call...', '执行中...': 'Running...', '执行结果': 'Result',
    '正在思考中...': 'Thinking...', '正在重连': 'Reconnecting', '验证': 'Verification',
    'Mermaid 无法解析此图': 'Mermaid could not render this diagram',
    'Mermaid 流程图放大预览': 'Expanded Mermaid diagram preview', '关闭放大预览': 'Close expanded preview',
    '下载保存 Mermaid 流程图为图片': 'Download Mermaid diagram as an image', '下载图片': 'Download image',
    '放大显示 Mermaid 流程图': 'Expand Mermaid diagram', '点击查看图片': 'Click to view image',
    '移除文件路径': 'Remove file path', '响应异常': 'Invalid response', '已调用系统打开文件': 'Asked the system to open the file',
    '无法打开文件': 'Could not open file', '无法连接服务': 'Could not connect to the service',
    '取消改写': 'Cancel rewrite', '已截断历史，可撤销恢复': 'History truncated; you can undo to restore it',
    '已填入输入框，可撤销': 'Inserted into the input; you can undo',
    '改写待生效：发送消息后才会截断历史并发送；点此取消改写。': 'Rewrite pending: history will be truncated only when the message is sent. Click here to cancel.',
    '无法定位该条': 'Could not locate this message',

    // File picker
    '请求失败': 'Request failed', '无法打开选择对话框': 'Could not open the file picker', '上传失败': 'Upload failed',
    '正在取消上传…': 'Cancelling upload…', '上传已取消。': 'Upload cancelled.',
    '上传失败：网络连接异常。': 'Upload failed: network connection error.', '上传超时，请重试。': 'Upload timed out. Please try again.',
    '已有文件正在上传，请等待完成或先取消。': 'A file upload is already in progress. Wait for it to finish or cancel it first.',
    '本次上传总大小超过 200 MB 限制。': 'The total upload exceeds the 200 MB limit.',
    '读取工作区文件失败': 'Failed to read workspace files', '搜索工作区文件': 'Search workspace files',
    '未选择文件': 'No files selected', '选择工作目录外文件': 'Choose a file outside the workspace',
    '加载中': 'Loading', '没有匹配文件': 'No matching files', '折叠文件夹': 'Collapse folder',
    '展开文件夹': 'Expand folder', '读取失败': 'Failed to read', '浏览路径': 'Browse path', '工作区文件': 'Workspace files',

    // Confirmations, recovery and errors
    '粘贴文件失败': 'Failed to paste file', '文件上传失败': 'File upload failed', '无法保存剪贴板中的文件或图片。': 'Could not save the file or image from the clipboard.',
    '无法上传所选文件或剪贴板中的图片。': 'Could not upload the selected file or clipboard image.',
    '截断失败': 'Truncation failed', '无法同步服务器，改写未生效。': 'Could not sync with the server; the rewrite was not applied.',
    '撤销失败，请重试。': 'Undo failed. Please try again.', '检测到上次运行未完成，正在自动恢复任务…': 'The previous run was incomplete. Restoring it automatically…',
    '恢复实时流失败': 'Failed to restore the live stream', '续接失败': 'Failed to continue',
    '追问插入失败': 'Failed to insert follow-up', '追问已被接收，无法撤回': 'The follow-up was accepted and can no longer be withdrawn'
});
Object.assign(UI_TRANSLATIONS_EN, {
    '当前页面正在查看较早历史，且未能恢复最新历史尾部。请重试。': 'This page is viewing older history and could not restore the latest tail. Please try again.'
});
Object.assign(UI_TRANSLATIONS_EN, {
    // Human-in-the-loop cards and banners
    '待处理的人机交互': 'Pending human interactions', 'Agent 正在等待你处理': 'Agent is waiting for your input',
    '待处理事项': 'Pending items', '全局待办': 'Global pending', '当前会话': 'Current session', '立即处理': 'Handle now',
    '安全审批': 'Safety approval', '需要你的回答': 'Your response is required', '确认下一步': 'Confirm next step',
    '等待中': 'Waiting', '待回答': 'Waiting for answer', '待审批': 'Waiting for approval', '已处理': 'Processed', '已过期': 'Expired', '可多选': 'Select multiple', '单选': 'Select one',
    '查看预览': 'View preview', '其他': 'Other', '其他答案': 'Other answer', '输入你的答案…': 'Enter your answer…', '取消提问': 'Cancel question', '不回答': 'Skip answering', '只跳过当前题目': 'Skip only this question',
    '上一步': 'Back', '下一步': 'Next', '上一题': 'Previous question', '下一题': 'Next question', '确认': 'Confirm', '返回修改': 'Edit answers', '确认回答': 'Review answers', '提交答案': 'Submit answers', '正在提交…': 'Submitting…', '正在取消…': 'Cancelling…', '正在处理…': 'Processing…',
    '请选择一个选项。': 'Select an option.', '请至少选择一个选项。': 'Select at least one option.', '请输入其他答案。': 'Enter the other answer.', '请完成当前问题后再提交。': 'Complete the current question before submitting.',
    '返回回答问题': 'Return to question',
    'Agent 主动提问': 'Agent questions', 'Agent 主动提问功能开关': 'Agent question feature toggle',
    '已启用；Agent 可在确实需要选择时暂停并向你提问。': 'Enabled; the Agent may pause and ask when your choice is required.',
    '已关闭；Agent 不会创建结构化提问卡片。': 'Disabled; the Agent will not create structured question cards.',
    '取消失败：': 'Cancellation failed: ', '提交失败：': 'Submission failed: ', '处理失败：': 'Failed to process: ',
    '是否允许 Agent 执行此操作？': 'Allow Agent to perform this action?', '工具': 'Tool', '始终允许': 'Always allow', '本次允许': 'Allow this time',
    '允许一次': 'Allow once', '本任务内允许相同请求': 'Allow identical requests in this task', '始终允许此类操作': 'Always allow this kind of operation', '拒绝执行': 'Deny execution', '已取消': 'Cancelled', '该请求已取消。': 'This request was cancelled.', '该请求已过期。': 'This request expired.',
    '你已拒绝本次操作。': 'You denied this action.', '你已允许同类操作。': 'You allowed similar actions.', '你已允许本次操作。': 'You allowed this action.', '已回答': 'Answered', '未回答': 'Not answered',
    '展开或收起完整请求': 'Expand or collapse the full request',
    '此请求已处理，仅供查看，无法再次操作。': 'This request has been processed. It is read-only and cannot be changed.',
    '此请求已结束，仅供查看，无法再次操作。': 'This request has ended. It is read-only and cannot be changed.',
    // Session grouping and subagent continuation
    '刷新归档目录': 'Refresh archived sessions', '加载归档目录': 'Load archived sessions', '加载更多': 'Load more', '加载中...': 'Loading...',
    '个子任务已完成，点击继续让主 Agent 综合子任务结果（不会自动续跑）。': ' subtasks completed. Click continue to let the main Agent synthesize their results (no automatic continuation).',
    '个子任务结果尚未纳入上方回答，点击补充综合。': ' subtask results are not included in the answer above. Click to add a synthesis.',
    // Subagent controls
    '任务': 'Tasks', '会话': 'Sessions', '允许一次': 'Allow once', '拒绝': 'Deny',
    '收起 Subagent 面板': 'Collapse Subagent panel',
    '展开查看执行过程': 'Expand to view process', '退出全屏': 'Exit full screen', '停止': 'Stop',
    '加载失败': 'Load failed',
    // File picker dynamic errors
    '无法打开选择对话框': 'Could not open the file picker', '上传失败：网络连接异常。': 'Upload failed: network connection error.',
    '上传超时，请重试。': 'Upload timed out. Please try again.', '上传已取消。': 'Upload cancelled.',
    '已有文件正在上传，请等待完成或先取消。': 'A file upload is already in progress. Wait for it to finish or cancel it first.',
    '读取工作区文件失败': 'Failed to read workspace files', '没有匹配文件': 'No matching files', '浏览路径': 'Browse path',
    '工作区文件': 'Workspace files', '选择工作目录外文件': 'Choose a file outside the workspace', '未选择文件': 'No files selected',
    '正在取消上传…': 'Cancelling upload…', '读取失败': 'Failed to read', '取消失败': 'Cancellation failed',
    '文件上传失败': 'File upload failed', '无法上传所选文件或剪贴板中的图片。': 'Could not upload the selected file or clipboard image.',
    '当前没有选中的会话。': 'No session is currently selected.', '未选择会话': 'No session selected', '暂无提问': 'No questions yet',
    '消息索引异常，已阻止从错误位置清空会话。请刷新后再试。': 'The message index is invalid; clearing from the wrong position was blocked. Refresh and try again.',
    '消息索引异常，已阻止清空整个会话。请刷新后再试。': 'The message index is invalid; clearing the session was blocked. Refresh and try again.',
    '当前会话仍在生成。请等待完成或停止后再修改历史。': 'This session is still generating. Wait for completion or stop it before editing history.',
    '本段过程已折叠': 'This process section is collapsed', '未找到可保存的 Final 卡片': 'No Final card is available to save',
    'Final 卡片图片生成失败': 'Failed to generate the Final card image', 'Final 卡片图片保存失败': 'Failed to save the Final card image',
    '当前浏览器不支持复制文本': 'This browser does not support copying text', '无法完成复制或保存': 'Could not copy or save', '无法完成导出': 'Could not export',
    '至少选择复制文本或保存图片中的一项。': 'Select at least one of copy text or save image.', '图片已保存': 'Image saved', 'Markdown 已导出': 'Markdown exported',
    '复制选项': 'Copy options', '打开会话目录': 'Open session folder', '打开工作目录': 'Open workspace',
    '知道了': 'Got it', '已请求打开': 'Open requested', '无法定位该条': 'Could not locate this message'
});
Object.assign(UI_TRANSLATIONS_EN, {
    '文件上传失败': 'File upload failed', '无法上传所选文件或剪贴板中的图片。': 'Could not upload the selected file or clipboard image.',
    '正在保存…': 'Saving…', '正在读取状态…': 'Reading status…', '读取失败：': 'Failed to read: ',
    '已关闭；现有 task/subagent 行为不受影响。': 'Disabled; existing task/subagent behavior is unchanged.',
    '没有可用模型配置': 'No model configurations available', '没有启用的模型配置': 'No enabled model profiles',
    '模型配置加载失败': 'Failed to load model configuration', '正在加载模型配置': 'Loading model configuration',
    '模型配置切换失败: ': 'Failed to switch model configuration: ', '模型配置启停失败: ': 'Failed to change model profile status: ',
    '模型配置加载失败: ': 'Failed to load model configuration: ', '上下文窗口：': 'Context window: ', '接口类型：': 'Interface type: ',
    '最大输出：': 'Maximum output: ', '能力：': 'Capabilities: ', '状态：': 'Status: ', '可用': 'Available', '未就绪': 'Not ready', '未设置': 'Not set',
    '加载subagent历史失败:': 'Failed to load subagent history:', '加载详情中…': 'Loading details…', '加载失败: ': 'Load failed: ',
    '暂无事件': 'No events yet', '暂无 final 结果': 'No final result', '无 Subagent': 'No subagents',
    '任务失败，点击查看': 'Task failed — click to view', '有新回复，点击查看': 'New response — click to view',
    'Agent 请求执行操作': 'Agent requests permission to perform an action', '请选择操作': 'Choose an action', '复制文本': 'Copy text', '保存图片': 'Save image', '执行': 'Run',
    '原因：': 'Reason: ', '找不到可保存的 Final 卡片': 'No Final card is available to save', '操作失败': 'Operation failed', '确认': 'Confirm',
    '加载会话状态快照失败，回退至旧接口': 'Failed to load the session state snapshot; falling back to the legacy endpoint', '归档失败': 'Archive failed', '置顶失败': 'Pin failed', '待办设置失败': 'Failed to update todo status', '重命名失败': 'Rename failed',
    '加载更早记录': 'Load earlier records', '加载更早消息': 'Load earlier messages', '保存失败：': 'Save failed: ',
    '置顶会话': 'Pin session', '设为待办': 'Mark as todo', '取消待办': 'Remove todo', '待办': 'Todo', '待审核': 'Pending review', '归档会话': 'Archive session', '重命名': 'Rename', '重命名会话': 'Rename session',
    '编辑会话名称': 'Edit the session name', '会话名称': 'Session name', '保存名称': 'Save name',
    '导出会话': 'Export session', '下载会话文件': 'Download session files', '确认导出': 'Export', '输入内容': 'Input', '草稿': 'Draft'
});
Object.assign(UI_TRANSLATIONS_EN, {
    // Agent runtime status events (including compression and recovery)
    '本机网络已恢复，正在继续任务…': 'The local network is back; continuing the task…',
    '本机仍处于离线状态，Agent 正在沉睡并等待网络恢复…': 'The local machine is still offline; the Agent is sleeping until the network recovers…',
    '检测到同会话仍有未结束的上下文压缩，等待其完成后再继续 ReAct。': 'An unfinished context compression was detected for this session; waiting for it to finish before continuing ReAct.',
    '当前上下文无需进一步裁剪或摘要': 'The current context needs no further trimming or summarization',
    '对话已摘要，关键信息已写入 key_context': 'Conversation summarized; key information written to key_context',
    '子任务结果已返回，正在纳入当前回答': 'Subtask results returned; incorporating them into the current response',
    '安全确认': 'Safety confirmation', '用户已允许': 'User allowed', '用户已拒绝执行（已跳过）。': 'User denied execution (skipped).',
    '任务已由用户中断。': 'Task interrupted by the user.', '任务已由用户中断（父会话）。': 'Task interrupted by the user (parent session).',
    '任务因运行中断而暂停，服务恢复后会自动继续；若长时间未恢复，可重新发送消息继续。': 'The task was paused by an interruption and will resume automatically once the service recovers; if it does not, resend your message to continue.',
    '执行已由 Hook 暂停：': 'Execution paused by a Hook: ', '执行已由 Stop Hook 暂停：': 'Execution paused by a Stop Hook: ',
    '模型未输出最终内容': 'The model did not produce a final response', '检测到连续重复行为': 'Consecutive repeated behavior detected',
    '已插入强制提醒': 'A mandatory reminder was inserted', '已终止任务': 'Task terminated',
    '自动应急截断': 'Automatic emergency truncation', '待办更新失败：': 'Todo update failed: ', 'subagent 执行异常：': 'Subagent execution error: ',
    'MCP 调用异常：': 'MCP call error: ', '未知工具：': 'Unknown tool: ', '工具执行异常：': 'Tool execution error: ',
    '编辑说明不能为空': 'Edit instructions cannot be empty', '缺少 edit_instruction': 'Missing edit_instruction', '无效 mode': 'Invalid mode',
    '请稍候': 'Please wait', '本轮执行步骤已达到最大迭代次数。持久工作流会自动开始下一轮；普通会话可以手动继续任务。': 'This run reached the maximum number of iterations. Persistent workflows will start another round automatically; regular sessions can be continued manually.',
    '将执行的大致命令如下，请确认是否允许：': 'The approximate command to run is below. Confirm whether to allow it:', 'run_shell（放宽工作区）': 'run_shell (relaxed workspace)',
    '确认网络下载': 'Confirm network download', '将把远程文件写入工作区指定路径。': 'The remote file will be written to the specified workspace path.', 'URL：': 'URL:', '保存为（工作区内）：': 'Save as (inside workspace):', '（未指定）': '(not specified)',
    '无法继续任务': 'Unable to continue the task', '无法发送': 'Unable to send', '截断失败': 'Truncation failed',
    'Hook 请求确认': 'Hook requests confirmation', '网络连接失败': 'Network connection failed', 'API 认证失败': 'API authentication failed',
    '访问被拒绝': 'Access denied', '模型或接口不可用': 'Model or interface unavailable', '请求频率超限': 'Request rate limit exceeded',
    '请求参数错误': 'Invalid request parameters', '内容被拦截': 'Content blocked', '服务器错误': 'Server error', 'LLM 调用异常': 'LLM call failed',
    '无法连接到 API 服务器。': 'Could not connect to the API server.', 'API Key 无效或已过期。': 'The API key is invalid or expired.',
    '当前地区不支持或 API Key 被风控。': 'The current region is unsupported or the API key was restricted.',
    '请求的模型不支持当前能力（如图像输入）。': 'The requested model does not support this capability (such as image input).',
    '已重试 3 次，均因速率限制失败。': 'All three retries failed due to rate limiting.', '请求体格式不符合 API 要求。': 'The request body format does not meet the API requirements.',
    '输入内容触发了安全审核。': 'The input triggered a safety review.', '发生未知错误。': 'An unknown error occurred.',
    '请检查网络连接、当前 model profile 的 API Base URL、VPN/代理设置。': 'Check the network connection, the current model profile API base URL, and VPN/proxy settings.',
    '请检查当前 model profile 中的 API Key 是否正确。': 'Check that the API key in the current model profile is correct.',
    '请新建 API Key，或检查服务地区限制。': 'Create a new API key or check regional service restrictions.',
    '请检查模型名称是否正确，或换一个支持该能力的模型。': 'Check the model name or switch to a model that supports this capability.',
    '请稍等片刻再试，或降低请求频率；Token Plan 用户可考虑升级套餐。': 'Wait a moment and try again, or reduce the request rate; Token Plan users may consider upgrading.',
    '请检查消息格式、必填字段、模型名称是否正确。': 'Check the message format, required fields, and model name.',
    '请避免敏感或违规内容，修改后重试。': 'Avoid sensitive or disallowed content, then try again.',
    '请稍后重试；若持续出现请联系 API 服务商。': 'Try again later; contact the API provider if the issue persists.',
    '请先检查模型配置，或到 GitHub 提交 issue 反馈。': 'Check the model configuration first, or report the issue on GitHub.',
    '（无来源文本，未生成 summary）': '(No source text; no summary generated)', '本机网络已断开': 'The local network is disconnected',
    '进入沉睡状态并等待网络恢复': 'entering sleep until the network recovers', '模型输出在完整工具调用后达到长度上限；已保留并执行完整调用，未完成片段已丢弃。': 'The model output reached the length limit after a complete tool call; the complete call was retained and executed, and the unfinished fragment was discarded.',
    '工具执行异常: ': 'Tool execution error: ', '已清理临时文件': 'Cleaned up temporary files', '已移入 .trash': 'moved to .trash',
    '后台 Subagent 已完成': 'Background subagent completed', 'LLM 调用失败': 'LLM call failed', '模型输出达到输出 token 上限': 'Model output reached the output-token limit',
    '达到最大迭代次数': 'Reached the maximum iteration count', 'ReAct 已达到轮次上限': 'ReAct reached the iteration limit'
});
Object.assign(UI_TRANSLATIONS_EN, {
    // Permission mode selector & security settings
    '权限': 'Permissions',
    '请求批准': 'Ask for approval',
    '替我审批': 'Approve for me',
    '完全访问权限': 'Full access',
    '普通操作不受限制（安全红线保留）': 'Ordinary operations unrestricted (safety red lines remain enforced)',
    '应用层受限': 'App-restricted',
    '系统级出站防护': 'System-level egress protection',
    '降级防护（应用层识别）': 'Degraded protection (application-level detection)',
    '应用层受限，越界和高风险操作由你审批': 'App-restricted; out-of-bounds and high-risk actions are reviewed by you',
    '相同应用层边界，由独立审查 Agent 审批': 'Same app-level boundary; approved by an independent review agent',
    '普通操作免审批；安全红线始终执行': 'Ordinary operations need no approval; safety red lines always apply',
    '更改权限': 'Change permissions',
    'Agent 的操作应如何获得审批？': 'How should Agent actions be approved?',
    '工作区内写入自动执行；删除、联网、出项目按普通审批': 'Writes run automatically inside the workspace; deletion, network, and outside-project access use ordinary approval',
    '低风险自动放行，高风险仍转给你确认': 'Automatically approves low-risk actions and asks you to confirm high-risk ones',
    '普通操作和工作区删除免审批；高危系统命令仍确认': 'Ordinary operations and workspace deletion need no approval; high-risk system commands still require confirmation',
    '自动审查中：审查 Agent 正在核对你的任务意图与请求风险。': 'Auto-reviewing: a reviewer agent is checking this request against your task intent.',
    '自动审批已批准': 'Auto-review approved',
    '自动审批已拒绝': 'Auto-review denied',
    '自动审查不可用（已转人工确认）': 'Auto-review unavailable (switched to manual review)',
    '替我分析': 'Analyze for me',
    '调用独立审查模型给出风险解读和审批建议，不会替你执行审批': 'Ask an independent reviewer model for risk analysis and advice without approving anything',
    '正在分析…': 'Analyzing…',
    '审查 Agent 正在核对任务意图与本次操作风险…': 'A reviewer agent is checking the task intent and the risk of this operation…',
    '暂时无法给出可靠建议': 'Unable to provide reliable advice right now',
    '建议允许': 'Recommend allowing',
    '建议拒绝': 'Recommend denying',
    '【命令风险】': '[Command risk]',
    '【命令目的】': '[Command purpose]',
    '未提供具体风险说明。': 'No specific risk explanation was provided.',
    '未提供命令用途说明。': 'No command-purpose explanation was provided.',
    '审查模型未提供理由。': 'The reviewer model did not provide a reason.',
    '以上仅为分析建议，审批仍由你决定。': 'This is analysis only; the approval decision remains yours.',
    '可人工覆盖本次请求（只此一次，不沉淀规则）': 'You can override this once for this exact request (this time only; no rule is saved)',
    '授权工作区沙箱外处理权限': 'Authorize outside-workspace handling',
    '命令授权': 'Command authorization',
    '工作区沙箱外处理权限': 'Outside-workspace handling permission',
    '工作区外处理权限': 'Outside-workspace handling',
    '权限与安全': 'Security & Permissions',
    '工作区外处理权限的授权入口在审批卡片：当写、删除或 Shell 触发工作区外操作时，选择“授权工作区沙箱外处理权限”即可一次性放开；此处仅提供撤销。': 'Outside-workspace handling is granted from the approval card: when a write, delete, or shell operation touches outside the workspace, choose "Authorize outside-workspace handling" once; this section only provides revocation.',
    '已开启：写/删/Shell 工作区外操作自动放行。': 'Enabled: outside-workspace write/delete/shell run automatically.',
    '已关闭：工作区外操作恢复逐次审批。': 'Disabled: outside-workspace operations ask again.',
    '撤销授权': 'Revoke authorization',
    '撤销工作区外处理权限？': 'Revoke outside-workspace handling?',
    '撤销后，写、删除和 Shell 在工作区外的操作将恢复逐次审批。': 'After revoking, outside-workspace write/delete/shell operations will ask again.',
    '确认撤销': 'Revoke',
    '完全访问已开启：普通文件、工作区删除、命令和联网不再逐项询问；高危系统命令仍需确认，Agent 自保红线始终拦截。': 'Full access is on: ordinary files, workspace deletion, commands, and network access need no per-action approval; high-risk system commands still require confirmation and Agent self-protection always applies.',
    '完全访问已开启；普通操作和工作区删除免审批，高危系统命令和 Agent 自保红线仍受保护。': 'Full access is on; ordinary operations and workspace deletion need no approval, while high-risk system commands and Agent self-protection remain enforced.',
    '完全访问已开启：普通操作和工作区删除免审批；高危系统命令仍需确认，Agent 自保红线始终拦截。': 'Full access is on: ordinary operations and workspace deletion need no approval; high-risk system commands still require confirmation and Agent self-protection always applies.',
    '仅在信任 Agent 时才建议开启': 'Only enable this when you trust the agent.',
    '完全访问开启后，普通文件、工作区内删除、Shell 和网络操作不再逐项征求你的同意；格式化、磁盘写入、关机等高危系统命令仍需一次性人工确认，终止 Agent 或篡改控制器始终拒绝。此设置对所有会话生效，重启后也不会自动关闭，直到你手动切回“请求批准”。是否继续？': 'With full access, ordinary files, workspace deletion, shell, and network operations need no per-action approval; high-risk system commands such as formatting disks or shutting down still require one-time human confirmation, and terminating or tampering with the Agent controller is always denied. This applies to all sessions and persists across restarts until you switch back to "Ask for approval". Continue?',
    '确认切换': 'Switch now',
    '确认清除': 'Clear now',
    '切换权限失败': 'Failed to switch permissions',
    '请求批准 / 替我审批 / 完全访问均可直接切换；完全访问切换时会单独警告': 'Ask for approval / Approve for me / Full access can be switched directly; Full access shows an extra warning',
    '权限规则（始终允许 / 必问 / 拒绝）': 'Permission rules (Always allow / Always ask / Deny)',
    '规则类型': 'Rule type',
    '规则行为': 'Rule behavior',
    '清除本会话规则': 'Clear session rules',
    '正在读取…': 'Loading…',
    'Shell 命令': 'Shell command',
    '读取': 'Read',
    '写入': 'Write',
    '网络': 'Network',
    '联网搜索': 'Web search',
    '允许': 'Allow',
    '必问': 'Always ask',
    '添加规则': 'Add rule',
    '网页抓取预批准域名（web_fetch 免审批）': 'Pre-approved domains for web fetch (web_fetch without approval)',
    '仅作用于只读的 web_fetch；web_download 与 Shell 联网不受影响。每行一个域名，留空表示仅用内置清单。': 'Applies only to read-only web_fetch; web_download and shell networking are unaffected. One domain per line; leave empty to use only the built-in list.',
    '保存': 'Save',
    '权限模式': 'Permission mode',
    '预批准域名操作': 'Pre-approved domain actions',
    '暂无长期规则。审批时选择“始终允许”可自动添加可生成的长期规则。': 'No persistent rules yet. Choosing "Always allow" adds a persistent rule when one can be generated.',
    '未选择会话。': 'No session selected.',
    '清除当前会话的所有权限规则？用户级“始终允许”规则不受影响。': 'Clear all permission rules for the current session? User-level "Always allow" rules are unaffected.',
    '规则已删除。': 'Rule deleted.',
    '删除失败：': 'Failed to delete: ',
    '清除失败：': 'Failed to clear: ',
    '添加失败：': 'Failed to add: ',
    '·本会话': '· Session',
    '·项目': '· Project',
});
const UI_I18N_ATTRS = ['aria-label', 'data-ui-tip', 'title', 'placeholder'];
// These nodes contain user-, model-, or runtime-authored text. Translating them
// mutates conversation content instead of localizing UI chrome.
const UI_I18N_CONTENT_SELECTOR = [
    '.message',
    '.feed-chunk-scroller',
    '.process-brief-item',
    '.followup-queue-text',
    '.session-name',
    '.session-last-query',
    '.human-question-text',
    '.human-option-label',
    '.human-option-description',
    '.human-option-preview pre',
    '.human-review-label',
    '.human-review-value',
    '.human-approval-subtitle',
    '.human-approval-message',
    '.human-terminal-answer',
    '.subagent-card-name',
    '.subagent-card-summary',
    '.subagent-output-content',
    '.subagent-block-body',
    '.subagent-block-preview',
    '.skill-picker-option-desc',
    '[data-i18n-skip]',
].join(',');
const uiI18nTextOriginal = new WeakMap();
const uiI18nAttrOriginal = new WeakMap();
const uiI18nRuntimeOriginal = new WeakMap();
var uiLanguage = localStorage.getItem(LS_UI_LANGUAGE) === 'en' ? 'en' : 'zh-CN';
var uiI18nObserver = null;

function translateUiString(value) {
    if (uiLanguage !== 'en') return value;
    var exact = UI_TRANSLATIONS_EN[value];
    if (exact) return exact;
    return String(value)
        .replace(/^更早 (\d+) 轮对话$/, 'Earlier $1 conversations')
        .replace(/^(\d+) \/ (\d+) 已完成$/, '$1 / $2 completed')
        .replace(/^(\d+) \/ (\d+) 完成$/, '$1 / $2 completed')
        .replace(/^(\d+)分钟$/, '$1 min')
        .replace(/^(\d+) 分钟$/, '$1 min')
        .replace(/^已加载 (\d+) 个自定义域名（内置清单始终生效）。$/, '$1 custom domain(s) loaded (the built-in list always applies).')
        .replace(/^已保存 (\d+) 个自定义域名，新会话立即生效。$/, '$1 custom domain(s) saved; new sessions take effect immediately.')
        .replace(/^已清除本会话规则（(\d+) 条）。$/, 'Session rules cleared ($1).')
        .replace(/^已选择 (\d+) 个 Skill$/, '$1 skills selected')
        .replace(/^已选择 (\d+) 项$/, '$1 items selected')
        .replace(/^正在上传 (\d+) 个文件… (\d+)%$/, 'Uploading $1 files… $2%')
        .replace(/预估上下文 token：选择会话并加载或发送消息后显示。分母为压缩摘要阈值。/g, 'Estimated context tokens; shown after selecting a session and loading or sending a message. The denominator is the compression-summary threshold.')
        .replace(/tokens（约 ([\d.]+)%，超出门限 ([\d.]+)%）。预估进入模型的上下文规模，含历史与系统提示；分母为当前 model profile 中触发压缩摘要的上下文门限。/g, 'tokens (about $1%; $2% over the limit). Estimated context size sent to the model, including history and system prompts; the denominator is the compression threshold for the current model profile.')
        .replace(/tokens（约 ([\d.]+)%）。预估进入模型的上下文规模，含历史与系统提示；分母为当前 model profile 中触发压缩摘要的上下文门限。/g, 'tokens (about $1%). Estimated context size sent to the model, including history and system prompts; the denominator is the compression threshold for the current model profile.')
        .replace(/（当前约占上下文窗口 ([^）]+)）/g, ' (currently about $1 of the context window)')
        .replace(/模型配置：/g, 'Model configuration: ')
        .replace(/模型 ID：/g, 'Model ID: ')
        .replace(/接口类型：/g, 'Interface type: ')
        .replace(/上下文窗口：/g, 'Context window: ')
        .replace(/最大输出：/g, 'Maximum output: ')
        .replace(/思考强度：/g, 'Thinking effort: ')
        .replace(/能力：/g, 'Capabilities: ')
        .replace(/状态：/g, 'Status: ')
        .replace(/Skill：/g, 'Skill: ')
        .replace(/描述：/g, 'Description: ')
        .replace(/(\d+) 个待处理请求/g, '$1 pending requests')
        .replace(/约 (\d+(?:\.\d+)?)%/g, 'about $1%')
        .replace(/超出门限 (\d+(?:\.\d+)?)%/g, '$1% over the limit')
        .replace(/工具调用生成中\.\.\./g, 'Preparing tool call...')
        .replace(/执行中\.\.\./g, 'Running...')
        .replace(/执行结果/g, 'Result')
        .replace(/生成中\.\.\./g, 'Generating...')
        .replace(/(\d+) 个待处理请求/g, '$1 pending requests')
        .replace(/(\d+) 个问题待确认/g, '$1 questions awaiting confirmation')
        .replace(/(\d+) 个问题/g, '$1 questions')
        .replace(/(\d+) 个审批/g, '$1 approvals')
        .replace(/文件“(.+)”超过 (.+) 限制。/g, 'File “$1” exceeds the $2 limit.')
        .replace(/本次上传总大小超过 (.+) 限制。/g, 'The total upload exceeds the $1 limit.')
        .replace(/正在上传 (\d+) 个文件… (\d+)%/g, 'Uploading $1 files… $2%')
        .replace(/^(\d+) 个子任务已完成，点击继续让主 Agent 综合子任务结果（不会自动续跑）。$/g, '$1 subtasks completed. Click continue to let the main Agent synthesize their results (no automatic continuation).')
        .replace(/^(\d+) 个子任务结果尚未纳入上方回答，点击补充综合。$/g, '$1 subtask results are not included in the answer above. Click to add a synthesis.')
        .replace(/^提交失败：(.+)$/g, 'Submission failed: $1')
        .replace(/^取消失败：(.+)$/g, 'Cancellation failed: $1')
        .replace(/^处理失败：(.+)$/g, 'Failed to process: $1')
        .replace(/^分析失败：(.+)$/g, 'Analysis failed: $1')
        .replace(/^风险：(.+)$/g, 'Risk: $1')
        .replace(/^切换会话加载失败：?(.+)$/g, 'Failed to load the session: $1')
        .replace(/^切换会话失败：?(.+)$/g, 'Failed to switch session: $1')
        .replace(/^加载会话消息失败：?(.+)$/g, 'Failed to load session messages: $1')
        .replace(/^加载会话列表失败：?(.+)$/g, 'Failed to load the session list: $1')
        .replace(/^创建新会话失败：?(.+)$/g, 'Failed to create a new session: $1')
        .replace(/^删除会话失败：?(.+)$/g, 'Failed to delete the session: $1')
        .replace(/^刷新会话摘要失败：?(.+)$/g, 'Failed to refresh the session summary: $1')
        .replace(/^加载更早(?:消息|记录)失败：?(.+)$/g, 'Failed to load earlier messages: $1')
        .replace(/^预加载下一批归档目录失败：?(.+)$/g, 'Failed to preload the next archived sessions: $1')
        .replace(/^重命名失败：?(.+)$/g, 'Rename failed: $1')
        .replace(/^归档失败：?(.+)$/g, 'Archive failed: $1')
        .replace(/^置顶失败：?(.+)$/g, 'Pin failed: $1')
        .replace(/^问题 (\d+)$/g, 'Question $1')
        .replace(/^问题 #(\d+)$/g, 'Question #$1')
        .replace(/（事件索引 (\d+)）/g, ' (event index $1)')
        .replace(/^保存失败：(.+)$/g, 'Save failed: $1')
        .replace(/^Skill 加载失败：(.+)$/g, 'Failed to load Skill: $1')
        .replace(/^MCP 工具加载失败：(.+)$/g, 'Failed to load MCP tools: $1')
        .replace(/^MCP 工具启停失败：(.+)$/g, 'Failed to change MCP tool status: $1')
        .replace(/^扩展加载失败：(.+)$/g, 'Failed to load extensions: $1')
        .replace(/^异步截断失败：?(.+)$/g, 'Asynchronous truncation failed: $1')
        .replace(/^续接 subagent 失败：?(.+)$/g, 'Failed to continue subagent: $1')
        .replace(/^检测到上次运行未完成，正在自动恢复任务…$/g, 'The previous run was incomplete; restoring the task automatically…')
        .replace(/^检测到 (?:系统睡眠|Agent 进程暂停)约 (\d+) 秒，任务已恢复$/g, 'A system sleep or Agent process pause of about $1 seconds was detected; the task resumed')
        .replace(/^未能加载到对应的用户提问（可能索引不一致）。可刷新页面或使用「更早 (.+) 轮对话」手动分页。$/g, 'Could not load the corresponding user question (the index may be inconsistent). Refresh the page or use “Earlier $1 conversations” to paginate manually.')
        .replace(/^【安全确认】用户已允许：(.+)$/g, '[Safety confirmation] User allowed: $1')
        .replace(/^【安全确认】用户已拒绝执行（已跳过）。\s*(.+)$/g, '[Safety confirmation] User denied execution (skipped): $1')
        .replace(/^任务已由用户中断（父会话）$/g, 'Task interrupted by the user (parent session)')
        .replace(/^任务已由用户中断$/g, 'Task interrupted by the user')
        .replace(/^任务因运行中断而暂停，服务恢复后会自动继续；若长时间未恢复，可重新发送消息继续$/g, 'The task was paused by an interruption and will resume automatically once the service recovers; if it does not, resend your message to continue')
        .replace(/^执行已由 Stop Hook 暂停：(.+)$/g, 'Execution paused by a Stop Hook: $1')
        .replace(/^执行已由 Hook 暂停：(.+)$/g, 'Execution paused by a Hook: $1')
        .replace(/^Stop Hook 在 (\d+) 次检查后仍阻止结束：(.+)$/g, 'Stop Hook still blocked completion after $1 checks: $2')
        .replace(/^模型未输出最终内容，正在重试（(\d+)\/(\d+)）$/g, 'The model did not produce a final response; retrying ($1/$2)')
        .replace(/^检测到连续重复行为（(\d+)次），已插入强制提醒$/g, 'Consecutive repeated behavior detected ($1 times); a mandatory reminder was inserted')
        .replace(/^检测到连续重复行为，已终止任务。最近输出：(.+)$/g, 'Consecutive repeated behavior detected; task terminated. Recent output: $1')
        .replace(/^子任务结果已返回，正在纳入当前回答$/g, 'Subtask results returned; incorporating them into the current response')
        .replace(/^待办更新失败：(.+)$/g, 'Todo update failed: $1')
        .replace(/^subagent 执行异常：(.+)$/g, 'Subagent execution error: $1')
        .replace(/^MCP 调用异常：(.+)$/g, 'MCP call error: $1')
        .replace(/^未知工具：(.+)$/g, 'Unknown tool: $1')
        .replace(/^工具执行异常：(.+)$/g, 'Tool execution error: $1')
        .replace(/^工具执行异常:\s*(.+)$/g, 'Tool execution error: $1')
        .replace(/^Todo 计划已连续 (\d+) 轮未更新，已插入更新提醒$/g, 'The Todo plan has not been updated for $1 rounds; an update reminder was inserted')
        .replace(/^检测到本机网络已断开，Agent 进入沉睡状态并等待网络恢复…$/g, 'The local network is disconnected; the Agent is sleeping until it recovers…')
        .replace(/^网络连接失败，正在重连（第 (\d+) 次，(.+)s 后重试）\.\.\.$/g, 'Network connection failed; reconnecting (attempt $1, retrying in $2s)…')
        .replace(/^LLM 调用失败 \[([^\]]+)\] (.+)：(.+)\n(.+)$/g, 'LLM call failed [$1] $2: $3\n$4')
        .replace(/^模型输出在完整工具调用后达到长度上限；已保留并执行完整调用，未完成片段已丢弃。$/g, 'The model output reached the length limit after a complete tool call; the complete call was retained and executed, and the unfinished fragment was discarded.')
        .replace(/^模型输出达到 max_tokens\/max_output_tokens 上限，工具调用可能被截断。请调大输出窗口，或把长文件写入拆成更小的步骤后重试。$/g, 'Model output reached the max_tokens/max_output_tokens limit; tool calls may be truncated. Increase the output window, or split long-file writes into smaller steps and retry.')
        .replace(/^模型输出达到输出 token 上限，已丢弃半截工具调用并重试（(\d+)\/(\d+)）$/g, 'Model output reached the output-token limit; the incomplete tool call was discarded and retried ($1/$2)')
        .replace(/^已清理临时文件 (\d+) 个（已移入 \.trash）$/g, 'Cleaned up $1 temporary files (moved to .trash)')
        .replace(/^\[后台 Subagent 已完成\]/g, '[Background subagent completed]')
        .replace(/^无效的 mode：(.+?)；仅支持 compact、edit_key_context。$/g, 'Invalid mode: $1; only compact and edit_key_context are supported.')
        .replace(/^自动应急截断已重试 (\d+) 次仍可能超过整包阈值；将直接请求主模型。可新建会话或调低环境变量 CONTEXT_WINDOW（当前 (.+)）$/g, 'Automatic emergency truncation may still exceed the full-package threshold after $1 retries; requesting the main model directly. Create a new session or lower CONTEXT_WINDOW (current: $2).')
        .replace(/网络连接失败/g, 'Network connection failed')
        .replace(/API 认证失败/g, 'API authentication failed')
        .replace(/访问被拒绝/g, 'Access denied')
        .replace(/模型或接口不可用/g, 'Model or interface unavailable')
        .replace(/请求频率超限/g, 'Request rate limit exceeded')
        .replace(/请求参数错误/g, 'Invalid request parameters')
        .replace(/内容被拦截/g, 'Content blocked')
        .replace(/服务器错误/g, 'Server error')
        .replace(/LLM 调用异常/g, 'LLM call failed')
        .replace(/无法连接到 API 服务器。/g, 'Could not connect to the API server.')
        .replace(/API Key 无效或已过期。/g, 'The API key is invalid or expired.')
        .replace(/当前地区不支持或 API Key 被风控。/g, 'The current region is unsupported or the API key was restricted.')
        .replace(/请求的模型不支持当前能力（如图像输入）。/g, 'The requested model does not support this capability (such as image input).')
        .replace(/已重试 3 次，均因速率限制失败。/g, 'All three retries failed due to rate limiting.')
        .replace(/请求体格式不符合 API 要求。/g, 'The request body format does not meet the API requirements.')
        .replace(/输入内容触发了安全审核。/g, 'The input triggered a safety review.')
        .replace(/发生未知错误。/g, 'An unknown error occurred.')
        .replace(/^已选 (\d+) \/ 共 (\d+)$/, '$1 selected / $2 total')
        .replace(/^已选 (\d+) \/ 已启用 (\d+) \/ 共 (\d+)$/, '$1 selected / $2 enabled / $3 total')
        .replace(/^共 (\d+) 个服务器 · (\d+) 个工具$/, '$1 servers · $2 tools')
        .replace(/^已启用 (\d+) \/ 共 (\d+) 个工具$/, '$1 enabled / $2 tools')
        .replace(/^已启用 (\d+) \/ 共 (\d+) 个$/, '$1 enabled / $2 total')
        .replace(/^共 (\d+)$/, '$1 total')
        .replace(/已启用/g, 'Enabled')
        .replace(/已禁用/g, 'Disabled')
        .replace(/暂无描述/g, 'No description')
        .replace(/未就绪/g, 'Not ready')
        .replace(/可用/g, 'Available')
        .replace(/未设置/g, 'Not set')
        .replace(/^加载失败: (.+)$/, 'Failed to load: $1')
        .replace(/^请求失败: (.+)$/, 'Request failed: $1')
        .replace(/^无法打开：(.+)$/, 'Could not open: $1')
        .replace(/^移除 (.+)$/, 'Remove $1')
        .replace(/^确定删除会话「(.+)」吗？其中的消息与记录将被移除。$/, 'Delete session “$1”? Its messages and records will be removed.')
        .replace(/^将会话「(.+)」对应的 session 文件夹压缩为 ZIP 并下载。$/, 'Compress the session folder for “$1” as a ZIP file and download it.')
        .replace(/^工具 (\d+) 次$/, '$1 tool calls')
        .replace(/^失败 (\d+) 次$/, '$1 failures')
        .replace(/工具\s*(\d+)\s*次/g, '$1 tool calls')
        .replace(/失败\s*(\d+)\s*次/g, '$1 failures')
        .replace(/(\d+)\s*分\s*(\d+)\s*秒/g, '$1m $2s')
        .replace(/^(\d+) 轮$/, '$1 rounds')
        .replace(/^(\d+)分(\d+)秒$/, '$1m $2s')
        .replace(/^调用工具 (.+) (\d+)次$/, 'Called tool $1 $2 times')
        .replace(/^检测到系统睡眠约 (\d+) 秒，任务已恢复$/, 'System sleep detected for about $1 seconds; task resumed')
        .replace(/^检测到 Agent 进程暂停约 (\d+) 秒，任务已恢复$/, 'Agent process pause detected for about $1 seconds; task resumed')
        .replace(/^会话 (.+)$/, 'Session $1')
        .replace(/(\d+)\s*成员/g, '$1 members')
        .replace(/(\d+)\s*任务/g, '$1 tasks')
        .replace(/^模型配置切换失败: (.+)$/, 'Failed to switch model configuration: $1')
        .replace(/^模型配置启停失败: (.+)$/, 'Failed to change model profile status: $1')
        .replace(/^Skill 启停失败：(.+)$/, 'Failed to change Skill status: $1')
        .replace(/^续接失败: (.+)$/, 'Failed to continue: $1')
        .replace(/^恢复实时流失败: (.+)$/, 'Failed to restore live stream: $1')
        .replace(/^追问插入失败: (.+)$/, 'Failed to insert follow-up: $1')
        .replace(/^追问已被接收，无法撤回: (.+)$/, 'The follow-up was accepted and cannot be withdrawn: $1')
        .replace(/^验证：(.+)$/, 'Verification: $1')
        .replace(/【上下文窗口已满，开始压缩】/g, '[Context window full; starting compression]')
        .replace(/【上下文压缩已完成】/g, '[Context compression completed]')
        .replace(/【上下文摘要】/g, '[Context summary]')
        .replace(/【上下文裁剪】/g, '[Context trimming]')
        .replace(/【要点】/g, '[Key points]')
        .replace(/上下文窗口已满，开始压缩/g, 'Context window full; starting compression')
        .replace(/上下文压缩已完成/g, 'Context compression completed')
        .replace(/正在进行上下文裁剪以控制 token（可能需数秒，请稍候）…/g, 'Trimming context to control tokens (this may take a few seconds; please wait)…')
        .replace(/摘要模型仍在生成或等待响应中，请稍候…/g, 'The summary model is still generating or waiting for a response; please wait…')
        .replace(/模型仍在更新要点或等待响应中，请稍候…/g, 'The model is still updating key points or waiting for a response; please wait…')
        .replace(/已完成上下文裁剪与摘要以控制长度/g, 'Context trimming and summarization completed to control length')
        .replace(/已完成上下文裁剪以控制长度/g, 'Context trimming completed to control length')
        .replace(/正在分析上下文并准备本地裁剪…/g, 'Analyzing context and preparing local trimming…')
        .replace(/正在执行本地裁剪与微压…/g, 'Performing local trimming and micro-compression…')
        .replace(/已对非关键信息进行裁剪/g, 'Non-critical information trimmed')
        .replace(/正在收敛较早段落中的 ReAct 过程…/g, 'Consolidating ReAct steps in earlier sections…')
        .replace(/已对较早段落中的思考过程进行裁剪/g, 'Reasoning in earlier sections trimmed')
        .replace(/裁剪后仍超限，开始生成历史摘要…/g, 'Still over the limit after trimming; generating a history summary…')
        .replace(/没有足够可摘要的历史前缀，已转入截尾兜底。/g, 'Not enough history prefix to summarize; switching to tail truncation fallback.')
        .replace(/没有足够可摘要的历史前缀，继续尝试更窄尾窗…/g, 'Not enough history prefix to summarize; trying a narrower tail window…')
        .replace(/可摘要历史不足，已丢弃更早对话（保留至多约半窗 token 的尾部）。/g, 'Not enough history to summarize; discarded earlier messages and kept up to roughly half a window of recent tokens.')
        .replace(/可摘要历史不足；对话已在半窗预算内未再截断。/g, 'Not enough history to summarize; the conversation was not truncated further within the half-window budget.')
        .replace(/摘要输出格式重试后仍无效，已改用摘录兜底。/g, 'The summary output remained invalid after retries; using an excerpt fallback.')
        .replace(/第 (\d+) 次摘要输出格式无效，已丢弃并准备重试…/g, 'Summary output for attempt $1 was invalid, discarded, and will be retried…')
        .replace(/摘要模型调用失败，已改用摘录兜底：/g, 'Summary model call failed; using an excerpt fallback: ')
        .replace(/流程异常，已切换为失败兜底截尾。/g, 'The process encountered an error; switched to the failure fallback truncation.')
        .replace(/当前上下文无需进一步裁剪或摘要/g, 'The current context needs no further trimming or summarization')
        .replace(/正在进行上下文裁剪（可能需数秒，请稍候）…/g, 'Trimming context (this may take a few seconds; please wait)…')
        .replace(/第 (\d+) 轮：正在生成历史摘要与要点…/g, 'Round $1: generating history summary and key points…')
        .replace(/第 (\d+) 轮摘要完成/g, 'Round $1 summary completed')
        .replace(/第 (\d+) 轮要点已写入/g, 'Key points for round $1 written')
        .replace(/正在根据编辑说明更新要点…/g, 'Updating key points from the edit instructions…')
        .replace(/已按说明更新要点/g, 'Key points updated according to the instructions')
        .replace(/完成 (\d+) 轮历史摘要；完成关键 信息、经验与结论 的记录/g, 'Completed $1 rounds of history summarization; recorded key information, experience, and conclusions')
        .replace(/本轮未能继续缩小本地上下文，已转入安全兜底。/g, 'This round could not reduce local context further; switched to safe fallback.')
        .replace(/已完成配置的 (\d+) 轮且尚未达到压缩比，继续进行增量摘要…/g, 'Completed the configured $1 rounds without reaching the compression ratio; continuing incremental summarization…')
        .replace(/连续摘要未再缩小本地上下文（已尝试 (\d+) 轮）/g, 'Repeated summarization did not reduce local context further (tried $1 rounds)')
        .replace(/摘要未达到目标压缩比（已尝试 (\d+) 轮）/g, 'Summary did not reach the target compression ratio (tried $1 rounds)')
        .replace(/已转入安全兜底截尾。/g, 'Switched to safe fallback truncation.')
        .replace(/对话已在半窗预算内未再截断。/g, 'The conversation was not truncated further within the half-window budget.')
        .replace(/上下文已按策略完成裁剪/g, 'Context trimmed according to policy')
        .replace(/对话已摘要，关键信息已写入 key_context/g, 'Conversation summarized; key information written to key_context')
        .replace(/\[系统通知：/g, '[System notice: ')
        .replace(/\[压缩失败，保留截断原文片段\]/g, '[Compression failed; retaining a truncated excerpt]')
        .replace(/检测到同会话仍有未结束的上下文压缩，等待其完成后再继续 ReAct。/g, 'An unfinished context compression was detected for this session; waiting for it to finish before continuing ReAct.')
        .replace(/已按 CONTEXT_COMPRESS_FAILURE_MAX_TOKENS（与压缩失败兜底同款）裁剪对话尾部并继续本步/g, 'The conversation tail was trimmed using CONTEXT_COMPRESS_FAILURE_MAX_TOKENS (same as the compression-failure fallback), then this step continued')
        .replace(/上下文已截尾（Conversation truncated）；更早内容请查本会话目录。/g, 'Context truncated (Conversation truncated); see the session directory for earlier content.')
        .replace(/上下文已截尾（Conversation truncated），保留约半窗 token 尾部。/g, 'Context truncated (Conversation truncated), keeping roughly the last half-window of tokens.')
        .replace(/已完成上下文裁剪与摘要/g, 'Context trimming and summarization completed')
        .replace(/已完成上下文裁剪/g, 'Context trimming completed')
        .replace(/【自动·长度策略】/g, '[Automatic length policy]')
        .replace(/(\d+)\s*轮/g, '$1 rounds')
        .replace(/(\d+)\s*步/g, '$1 steps')
        .replace(/正在根据对话更新要点/g, 'Updating key points from the conversation')
        .replace(/正在思考中\.\.\./g, 'Thinking...')
        .replace(/正在重连/g, 'Reconnecting')
        .replace(/^\[历史\/旧版事件\] (.+)$/, '[History/legacy event] $1')
        .replace(/^(?:已选择|激活) Skill：(.+)$/, function (_, skills) {
            return 'Activated Skill: ' + String(skills || '').replace(/、/g, ', ');
        })
        .replace(/^追问暂未发出（发送通道繁忙），已保留待重试: (.+)$/, 'Follow-up not sent (send channel busy); retained for retry: $1')
        .replace(/^追问降级发送未成功，已保留待重试: (.+)$/, 'Fallback follow-up send failed; retained for retry: $1')
        .replace(/^思·/, 'Reasoning · ')
        .replace(/^答·/, 'Response · ')
        .replace(/^平均 (.+)$/, 'Average $1')
        .replace(/^累计 (.+)$/, 'Total $1')
        .replace(/^占本阶段 (.+)$/, 'Share of phase $1')
        .replace(/^(.+) 次 LLM 请求$/, '$1 LLM requests')
        .replace(/^(.+) 个模型。$/, '$1 models.')
        .replace(/^\.\.\. \[中间省略 (\d+) 行\] \.\.\.$/, '... [$1 lines omitted] ...')
        .replace(/^\.\.\. \[中间省略约 (\d+) 字符\] \.\.\.$/, '... [about $1 characters omitted] ...');
}

function translateUiNode(root) {
    if (!root) return;
    var elements = [];
    if (root.nodeType === Node.ELEMENT_NODE) elements.push(root);
    if (root.querySelectorAll) elements = elements.concat(Array.from(root.querySelectorAll('*')));
    elements.forEach(function (el) {
        if (el.closest && el.closest('.sidebar-brand-sub')) return;
        if (el.matches && (
            el.matches(UI_I18N_CONTENT_SELECTOR)
            || (el.closest && el.closest(UI_I18N_CONTENT_SELECTOR))
        )) return;
        if (el.matches('script,style,code,pre,[contenteditable="true"]')) return;
        var originals = uiI18nAttrOriginal.get(el) || {};
        UI_I18N_ATTRS.forEach(function (attr) {
            if (!el.hasAttribute(attr)) return;
            if (!(attr in originals)) originals[attr] = el.getAttribute(attr);
            el.setAttribute(attr, uiLanguage === 'en' ? translateUiString(originals[attr]) : originals[attr]);
        });
        uiI18nAttrOriginal.set(el, originals);
        Array.from(el.childNodes).forEach(function (node) {
            if (node.nodeType !== Node.TEXT_NODE || !node.nodeValue.trim()) return;
            if (!uiI18nTextOriginal.has(node)) uiI18nTextOriginal.set(node, node.nodeValue);
            var original = uiI18nTextOriginal.get(node);
            var trimmed = original.trim();
            var translated = uiLanguage === 'en' ? translateUiString(trimmed) : trimmed;
            node.nodeValue = original.replace(trimmed, translated);
        });
    });
}

// Runtime-owned process rows are kept separate from model/user content. Store
// their source text so toggling back to Chinese always restores the original,
// even when the row was updated while English was active.
function setUiRuntimeText(el, original) {
    if (!el) return;
    var source = String(original == null ? '' : original);
    uiI18nRuntimeOriginal.set(el, source);
    el.setAttribute('data-ui-runtime-text', '1');
    el.textContent = uiLanguage === 'en' ? translateUiString(source) : source;
}

function getUiRuntimeText(el) {
    if (!el) return '';
    var source = uiI18nRuntimeOriginal.get(el);
    return source == null ? String(el.textContent || '') : source;
}

// Final cards normally contain arbitrary model output and must never be
// translated.  These are the narrow, system-generated terminal messages that
// are emitted as an assistant final event and therefore need the same runtime
// localization as process/status rows.
function isUiRuntimeFinalText(value) {
    var source = String(value == null ? '' : value).trim();
    if (!source) return false;
    return /^任务已由用户中断(?:（父会话）)?。?$/.test(source)
        || /^任务因运行中断而暂停/.test(source)
        || /^执行已由 (?:Hook|Stop Hook) 暂停：/.test(source)
        || /^Stop Hook 在 \d+ 次检查后仍阻止结束：/.test(source)
        || /^检测到连续重复行为，已终止任务。最近输出：/.test(source)
        || /^本轮执行步骤已达到最大迭代次数。/.test(source)
        || /^(?:Token 预算已耗尽|连续运行失败|ReAct 已达到轮次上限|手动暂停)$/.test(source)
        || /^LLM 调用失败 \[[^\]]+\] /.test(source)
        || /^模型输出达到 max_tokens\/max_output_tokens 上限，/.test(source)
        || /^模型输出达到输出 token 上限，/.test(source)
        || /^模型输出在完整工具调用后达到长度上限；/.test(source);
}

function translateRuntimeUiNodes(root) {
    if (!root || !root.querySelectorAll) return;
    var nodes = [];
    if (root.nodeType === Node.ELEMENT_NODE && root.hasAttribute('data-ui-runtime-text')) nodes.push(root);
    nodes = nodes.concat(Array.from(root.querySelectorAll('[data-ui-runtime-text]')));
    nodes.forEach(function (el) {
        var source = uiI18nRuntimeOriginal.get(el);
        if (source != null) el.textContent = uiLanguage === 'en' ? translateUiString(source) : source;
    });
}

function applyUiLanguage(language, persist) {
    uiLanguage = language === 'en' ? 'en' : 'zh-CN';
    document.documentElement.lang = uiLanguage;
    document.documentElement.setAttribute('data-language', uiLanguage);
    document.title = uiLanguage === 'en' ? 'General Agent · Intelligent Chat' : 'General Agent · 智能会话';
    if (persist) localStorage.setItem(LS_UI_LANGUAGE, uiLanguage);
    if (uiI18nObserver) uiI18nObserver.disconnect();
    translateUiNode(document.body);
    translateRuntimeUiNodes(document.body);
    var languageButton = document.getElementById('sidebar-language-btn');
    if (languageButton) {
        languageButton.setAttribute('aria-label', uiLanguage === 'en' ? 'Switch to Chinese' : '切换为英文');
        languageButton.setAttribute('title', uiLanguage === 'en' ? 'Switch language' : '切换语言');
    }
    if (uiI18nObserver) uiI18nObserver.observe(document.body, { childList: true, subtree: true });
    document.dispatchEvent(new CustomEvent('myagent:language-change', { detail: { language: uiLanguage } }));
}

function initUiI18n() {
    uiI18nObserver = new MutationObserver(function (mutations) {
        mutations.forEach(function (mutation) {
            mutation.addedNodes.forEach(function (node) {
                if (node.nodeType === Node.ELEMENT_NODE) translateUiNode(node);
                else if (node.nodeType === Node.TEXT_NODE && node.parentElement) translateUiNode(node.parentElement);
            });
            if (mutation.type === 'characterData' && mutation.target.parentElement) {
                var textNode = mutation.target;
                var current = textNode.nodeValue || '';
                var original = uiI18nTextOriginal.get(textNode);
                if (!original || !original.trim()) {
                    original = current;
                    uiI18nTextOriginal.set(textNode, original);
                }
                var trimmed = original.trim();
                var translated = uiLanguage === 'en' ? translateUiString(trimmed) : trimmed;
                var nextValue = original.replace(trimmed, translated);
                if (nextValue !== current) textNode.nodeValue = nextValue;
            }
        });
    });
    applyUiLanguage(uiLanguage, false);
}
initUiI18n();
