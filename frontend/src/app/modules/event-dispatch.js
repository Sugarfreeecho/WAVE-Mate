function renderEvent(ctx, event, eventIndex, runSessionId) {
    if (!event || typeof event !== 'object') return;
    var eventSessionId = runSessionId || currentSessionId || '';
    if (eventSessionId && !event.__storeApplied) {
        applyMessageEvent(eventSessionId, event, eventIndex, replayingMessages ? 'history' : 'stream');
        if (event.type === 'subagent_start' || event.type === 'subagent_finish'
            || event.type === 'subagent_started' || event.type === 'subagent_finished') {
            applySubagentLifecycleToStore(eventSessionId, event);
        }
    }
    if (event.type === 'user') {
        if (typeof eventIndex === 'number') ctx.lastUserEventIndex = eventIndex;
        sealProcessGroup(ctx);
        appendMessage(ctx, 'user', event.content || '', { eventIndex: eventIndex, turnTruncateIdx: eventIndex }, runSessionId);
    } else if (event.type === 'final') {
        appendMessage(ctx, 'assistant', event.content || '', { eventIndex: eventIndex, turnTruncateIdx: ctx.lastUserEventIndex }, runSessionId);
    } else if (event.type === 'process_metrics') {
        applyProcessMetricsFromEvent(ctx, event);
    } else if (event.type === 'cache_stats') {
        applyCacheStatsFromEvent(ctx, event);
    } else if (event.type === 'tool_call') {
        var riTool = uiEventReactIter(event);
        if (event.raw_content) appendLog(ctx, event.raw_content, 'tool-call', runSessionId, riTool);
        else appendLog(ctx, formatToolDoneLine(event.tool, event.args, event.result, event.command_preview), 'tool-call', runSessionId, riTool);
    } else if (event.type === 'validate_final') {
        appendLog(ctx, '验证：' + event.result + (event.reason ? '\n' + event.reason : ''), 'status', runSessionId);
    } else if (event.type === 'llm_reasoning') {
        upsertLlmFeedRow(ctx, event.content || '', 'llm-reasoning', runSessionId, uiEventReactIter(event));
    } else if (event.type === 'llm_response') {
        upsertLlmFeedRow(ctx, event.content || '', 'llm-response', runSessionId, uiEventReactIter(event));
    } else if (event.type === 'llm_history_rollup' || event.type === 'compact_summary') {
        appendLog(ctx, String(event.content || ''), 'compact-summary', runSessionId);
    } else if (event.type === 'context_trim_progress') {
        appendProgressLog(ctx, event.content, 'context-trim', runSessionId);
    } else if (event.type === 'context_summary_progress') {
        appendProgressLog(ctx, event.content, 'context-summary', runSessionId);
    } else if (event.type === 'context_summary_delta') {
        appendProgressStreamDelta(ctx, event.delta, 'context-summary', runSessionId);
    } else if (event.type === 'context_summary_body') {
        applyProgressPersistedBody(ctx, event.content, 'context-summary', runSessionId);
    } else if (event.type === 'key_context_progress') {
        var keyProg = String(event.content || '');
        if (keyProg.indexOf('正在根据对话更新要点') >= 0) {
            finalizeProgressStreamForType(ctx, 'context-summary');
            resetKeyContextStreamFilter(ctx);
        }
        appendProgressLog(ctx, keyProg, 'key-context', runSessionId);
    } else if (event.type === 'key_context_delta') {
        appendKeyContextStreamDelta(ctx, event.delta, runSessionId);
    } else if (event.type === 'key_context_body') {
        applyProgressPersistedBody(ctx, event.content, 'key-context', runSessionId);
    } else if (event.type === 'error') {
        appendLog(ctx, String(event.content || ''), 'error-log', runSessionId);
    } else if (event.type === 'status') {
        var statusContent = String(event.content || '');
        if (statusContent.indexOf('【自动·长度策略】') >= 0) {
            finalizeProgressStreamChunks(ctx);
            resetKeyContextStreamFilter(ctx);
        }
        if (event.compress_progress) {
            var legacyLogType = 'context-trim';
            if (statusContent.indexOf('【上下文摘要】') >= 0) legacyLogType = 'context-summary';
            else if (statusContent.indexOf('【要点】') >= 0) legacyLogType = 'key-context';
            appendProgressLog(ctx, statusContent, legacyLogType, runSessionId);
            return;
        }
        // 临时状态消息处理：标记"正在思考中..."为临时状态
        var isTemporaryStatus = statusContent.indexOf('正在思考中...') >= 0;
        if (isTemporaryStatus) removeTemporaryStatus(ctx);
        var statusRow = appendLog(ctx, statusContent, 'status', runSessionId);
        if (isTemporaryStatus && statusRow) {
            statusRow.dataset.temporaryStatus = '1';
        }
    } else if (event.type === 'approval_required') {
        var leg = (event.tool_name ? String(event.tool_name) + ' ' : '') + (event.message || '');
        appendLog(ctx, '[历史/旧版事件] ' + leg.trim(), 'status', runSessionId);
    } else if (event.type === 'warning') {
        appendLog(ctx, String(event.content || ''), 'status', runSessionId);
    } else if (event.type === 'subagent_start' || event.type === 'subagent_finish') {
        if (!ctx._subagentBody) {
            handleSubagentLifecycleEvent(event);
            return;
        }
        if (event.type === 'subagent_start') ensureSubagentBlock(ctx, event);
        else updateSubagentBlockFinish(ctx, event);
    } else {
        var fallbackContent = String(event.content || '');
        if (fallbackContent.trim()) appendLog(ctx, fallbackContent, 'log-entry', runSessionId);
    }
}
