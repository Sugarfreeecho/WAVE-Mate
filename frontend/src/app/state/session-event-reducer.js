function markUiEventStoreApplied(event) {
    if (!event || typeof event !== 'object') return;
    try {
        Object.defineProperty(event, '__storeApplied', {
            value: true,
            configurable: true,
            enumerable: false,
        });
    } catch (e) {
        event.__storeApplied = true;
    }
}

function applySessionEvent(event, opts) {
    if (!event || typeof event !== 'object') return { handled: false };
    opts = opts || {};
    const sessionId = String(
        opts.sessionId
        || event.session_id
        || event.sessionId
        || currentSessionId
        || ''
    );
    const eventIndex = opts.eventIndex;
    const source = opts.source || 'event';
    const type = String(event.type || '');
    const runId = String(event.run_id || event.runId || '').trim();
    let messageRecord = null;
    const ephemeral = !!event.ephemeral;
    const isLiveOnlyDelta = ephemeral && (
        type === 'llm_reasoning_delta'
        || type === 'llm_response_delta'
        || type === 'tool_call_delta'
        || type === 'tool_command_delta'
        || type === 'context_summary_delta'
        || type === 'key_context_delta'
    );
    // Ephemeral events belong to the live DOM only. Recording statuses,
    // lifecycle heartbeats, or tool drafts in the durable message index lets
    // reconnect-only event-bus sequence numbers collide with projected UI
    // indexes. The later durable event then replaces/reorders that slot and
    // makes the process panel jump.
    if (sessionId && !ephemeral && !isLiveOnlyDelta) {
        messageRecord = applyMessageEvent(sessionId, event, eventIndex, source);
        markUiEventStoreApplied(event);
    }
    if (type === 'run_started' || type === 'run_attached') {
        if (runId && sessionStore.isTerminalRun(sessionId, runId)) {
            markSessionRunInactive(sessionId);
            return { handled: true, runStateChanged: true, messageRecord: messageRecord };
        }
        const suppressed = typeof isSessionStreamStopSuppressed === 'function'
            && isSessionStreamStopSuppressed(sessionId);
        setSessionServerStreamActive(sessionId, !suppressed);
        const sess = sessionStore.get(sessionId);
        if (sess) {
            sess.run_active = !suppressed;
            sess.run_started_at = suppressed
                ? null
                : (event.started_at || event.startedAt || sess.run_started_at || new Date().toISOString());
        }
        if (typeof updateSidebarRuntimeStatus === 'function') updateSidebarRuntimeStatus(true);
        return { handled: true, runStateChanged: true, messageRecord: messageRecord };
    }
    if (type === 'run_finished' || type === 'run_interrupted' || type === 'run_failed') {
        if (runId) sessionStore.markTerminalRun(sessionId, runId);
        const localRun = getSessionRunState(sessionId);
        const localRunId = String((localRun && localRun.runId) || '').trim();
        const activeInfo = sessionStore.activeRunInfoBySession.get(sessionId);
        const activeRunId = String((activeInfo && (activeInfo.run_id || activeInfo.runId)) || '').trim();
        const knownCurrentRunId = localRunId || activeRunId;
        if (runId && knownCurrentRunId && runId !== knownCurrentRunId) {
            return { handled: true, staleTerminalIgnored: true, messageRecord: messageRecord };
        }
        if (type === 'run_finished' && typeof clearSessionStreamStopSuppress === 'function') clearSessionStreamStopSuppress(sessionId);
        markSessionRunInactive(sessionId);
        markSessionResultComplete(
            sessionId,
            (type === 'run_interrupted' || type === 'run_failed') ? 'failed' : 'success'
        );
        return {
            handled: true,
            runStateChanged: true,
            messageRecord: messageRecord,
        };
    }
    if (type === 'final' && source === 'sse') {
        return { handled: false, finalStateChanged: true, messageRecord: messageRecord };
    }
    if (type === 'context_tokens') {
        setContextTokensForSession(sessionId, event.estimated, event.threshold);
        return { handled: false, contextStateChanged: true, messageRecord: messageRecord };
    }
    if (type === 'context_summary_delta') {
        appendContextProgressForSession(sessionId, 'context-summary', event.delta);
        return { handled: false, contextStateChanged: true, messageRecord: messageRecord };
    }
    if (type === 'key_context_delta') {
        appendContextProgressForSession(sessionId, 'key-context', event.delta);
        return { handled: false, contextStateChanged: true, messageRecord: messageRecord };
    }
    if (type === 'subagent_start' || type === 'subagent_finish'
        || type === 'subagent_started' || type === 'subagent_finished') {
        applySubagentLifecycleToStore(sessionId, event);
        return { handled: false, subagentStateChanged: true, messageRecord: messageRecord };
    }
    return { handled: false, messageRecord: messageRecord };
}
