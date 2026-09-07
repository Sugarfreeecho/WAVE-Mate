function applyPluginExtensionEventView(row, event) {
    if (!row || !event || typeof globalThis.resolvePluginExtensionEvent !== 'function') return;
    var view = globalThis.resolvePluginExtensionEvent(event);
    if (!view || !view.handled || view.pending) {
        row.hidden = true;
        return;
    }
    if (view.hidden) {
        row.remove();
        return;
    }
    row.hidden = false;
    row.classList.remove(
        'feed--plugin-neutral',
        'feed--plugin-info',
        'feed--plugin-success',
        'feed--plugin-warning',
        'feed--plugin-danger'
    );
    row.classList.add('feed--plugin-' + String(view.variant || 'neutral'));
    var label = row.querySelector('.feed-label');
    var content = row.querySelector('.feed-chunk-scroller');
    if (label) label.textContent = String(view.title || 'Extension');
    if (content) content.textContent = String(view.content || view.description || '');
    if (view.description) row.title = String(view.description);
}

function renderPluginExtensionEvent(ctx, event, runSessionId) {
    if (!event || event.type !== 'extension_event') return false;
    if (typeof globalThis.resolvePluginExtensionEvent !== 'function') return true;
    var view = globalThis.resolvePluginExtensionEvent(event);
    if (!view || !view.handled || view.hidden) return true;
    var scroller = appendLog(ctx, '', 'plugin-extension', runSessionId);
    var row = scroller && scroller.closest ? scroller.closest('.feed-item') : null;
    if (!row) return true;
    row._pluginExtensionEvent = event;
    row.dataset.pluginId = String(event.plugin_id || '');
    row.dataset.extensionEventName = String(event.event_name || '');
    applyPluginExtensionEventView(row, event);
    return true;
}

document.addEventListener('myagent:plugin-ui-ready', function () {
    document.querySelectorAll('.feed-item.feed--plugin-extension').forEach(function (row) {
        if (row._pluginExtensionEvent) applyPluginExtensionEventView(row, row._pluginExtensionEvent);
    });
});

function renderEvent(ctx, event, eventIndex, runSessionId) {
    if (!event || typeof event !== 'object') return;
    var eventSessionId = runSessionId || currentSessionId || '';
    document.dispatchEvent(new CustomEvent('myagent:ui-event', {
        detail: {
            event: event,
            eventIndex: eventIndex,
            sessionId: eventSessionId,
            rootSessionId: typeof rootSessionIdForRenderedNode === 'function'
                ? rootSessionIdForRenderedNode(ctx && ctx.stream, eventSessionId)
                : eventSessionId,
        },
    }));
    if (event.type === 'permission_mode_changed') {
        if (typeof renderPermissionMode === 'function') renderPermissionMode(event);
        return;
    }
    if (typeof isHumanInteractionEventType === 'function' && isHumanInteractionEventType(event.type)) {
        renderHumanInteractionEvent(ctx, event, eventSessionId);
        return;
    }
    if (renderPluginExtensionEvent(ctx, event, eventSessionId)) return;
    if (event.type === 'extension_state_changed') {
        document.dispatchEvent(new CustomEvent('myagent:extension-state-changed', {
            detail: {
                sessionId: eventSessionId,
                pluginId: String(event.plugin_id || ''),
                namespace: String(event.namespace || ''),
            },
        }));
        return;
    }
    if (eventSessionId && !event.__storeApplied) {
        applyMessageEvent(eventSessionId, event, eventIndex, replayingMessages ? 'history' : 'stream');
        if (event.type === 'subagent_start' || event.type === 'subagent_finish'
            || event.type === 'subagent_started' || event.type === 'subagent_finished') {
            applySubagentLifecycleToStore(eventSessionId, event);
        }
    }
    if (event.type === 'user') {
        if (typeof eventIndex === 'number') ctx.lastUserEventIndex = eventIndex;
        if (Number.isFinite(Number(event.runtime_seq || event.runtimeSeq))) {
            ctx.lastUserRuntimeSeq = Math.floor(Number(event.runtime_seq || event.runtimeSeq));
        }
        sealProcessGroup(ctx);
        appendMessage(ctx, 'user', event.content || '', {
            eventIndex: eventIndex,
            turnTruncateIdx: eventIndex,
            runtimeSeq: event.runtime_seq || event.runtimeSeq,
            createdAt: event.created_at || event.createdAt || event.timestamp,
        }, runSessionId);
    } else if (event.type === 'user_steer') {
        var steerOperationId = event.client_id || event.steer_id || '';
        if (typeof prepareSteerProcessBoundary === 'function') {
            prepareSteerProcessBoundary(ctx, event.steer_mode || 'interrupt', steerOperationId);
        }
        if (typeof markSteerEventPosition === 'function') {
            markSteerEventPosition(ctx, eventIndex, event.runtime_seq || event.runtimeSeq);
        }
        if (typeof appendSteerProcessMessage === 'function' && (event.client_id || event.steer_id)) {
            appendSteerProcessMessage(
                eventSessionId,
                ctx,
                event.content || '',
                steerOperationId,
                event.steer_mode || 'interrupt',
                false
            );
        } else {
            appendLog(ctx, event.content || '', 'user-steer', runSessionId);
        }
    } else if (event.type === 'final') {
        var finalStream = ctx && ctx.stream ? ctx.stream : getVisibleChatStream();
        // Mark the last llm-response trace row as the final answer so its
        // collapsed height gets the special 2.5-line treatment.
        if (finalStream && finalStream.querySelectorAll) {
            var finalAnswerRows = finalStream.querySelectorAll('.feed-item[data-log-type="llm-response"]');
            if (finalAnswerRows.length) finalAnswerRows[finalAnswerRows.length - 1].classList.add('feed--final');
        }
        var userIdx = (ctx && Number.isFinite(Number(ctx.lastUserEventIndex))) ? Number(ctx.lastUserEventIndex) : latestVisibleUserEventIndex(finalStream);
        if (typeof hasDuplicateVisibleFinal === 'function' && hasDuplicateVisibleFinal(finalStream, userIdx, event.content)) return;
        var finalContent = event.content || '';
        if (typeof splitThinkTagsForUi === 'function') {
            var finalThinkSplit = splitThinkTagsForUi(finalContent);
            if (finalThinkSplit.reasoning && finalThinkSplit.reasoning.trim()) {
                upsertLlmFeedRow(ctx, finalThinkSplit.reasoning, 'llm-reasoning', runSessionId, uiEventReactIter(event));
            }
        }
        appendMessage(ctx, 'assistant', finalContent, {
            eventIndex: eventIndex,
            turnTruncateIdx: ctx.lastUserEventIndex,
            runtimeSeq: event.runtime_seq || event.runtimeSeq,
            runtimeEventType: event.runtime_event_type || event.runtimeEventType,
            truncateBeforeSeq: ctx.lastUserRuntimeSeq,
            uiRuntimeText: typeof isUiRuntimeFinalText === 'function' && isUiRuntimeFinalText(finalContent),
        }, runSessionId);
    } else if (event.type === 'process_metrics') {
        applyProcessMetricsFromEvent(ctx, event);
    } else if (event.type === 'cache_stats') {
        applyCacheStatsFromEvent(ctx, event, runSessionId);
    } else if (event.type === 'tool_call') {
        // Replay through the same upsert path as live SSE so the tool row
        // carries data-tool-call-id. Pending approval cards rendered earlier in
        // the replay can then be anchored into that row by
        // attachAllHumanInteractionCards().
        upsertToolCallResult(ctx, event, runSessionId);
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
        if (event.model_switch) {
            appendModelSwitchStatus(ctx, event, runSessionId);
            // 使用模型与选择器绑定：fallback 接管后服务端会把会话绑定
            // 改写为实际模型，这里静默刷新右下角选择器跟随。
            if (typeof refreshModelProfileSelectorInBackground === 'function' && runSessionId) {
                refreshModelProfileSelectorInBackground(runSessionId, { silent: true });
            }
            return;
        }
        if (statusContent.indexOf('【上下文窗口已满，开始压缩】') >= 0 || statusContent.indexOf('【上下文压缩已完成】') >= 0) {
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
        if (isTemporaryStatus) upsertTemporaryStatus(ctx, statusContent, runSessionId);
        else appendLog(ctx, statusContent, 'status', runSessionId);
    } else if (event.type === 'auto_review_status') {
        renderAutoReviewStatusEvent(ctx, event, runSessionId);
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
