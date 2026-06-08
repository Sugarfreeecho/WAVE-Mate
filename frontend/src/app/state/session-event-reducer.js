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
    if (sessionId) {
        applyMessageEvent(sessionId, event, eventIndex, source);
        markUiEventStoreApplied(event);
    }
    if (type === 'run_started' || type === 'run_attached') {
        setSessionServerStreamActive(sessionId, true);
        return { handled: true, runStateChanged: true };
    }
    if (type === 'run_finished' || type === 'run_interrupted' || type === 'run_failed') {
        setSessionServerStreamActive(sessionId, false);
        return { handled: true, runStateChanged: true };
    }
    if (type === 'context_tokens') {
        setContextTokensForSession(sessionId, event.estimated, event.threshold);
        return { handled: false, contextStateChanged: true };
    }
    if (type === 'context_summary_delta') {
        appendContextProgressForSession(sessionId, 'context-summary', event.delta);
        return { handled: false, contextStateChanged: true };
    }
    if (type === 'key_context_delta') {
        appendContextProgressForSession(sessionId, 'key-context', event.delta);
        return { handled: false, contextStateChanged: true };
    }
    if (type === 'todo_plan') {
        applyTodoPlanToStore(sessionId, event);
        return { handled: false, contextStateChanged: true };
    }
    if (type === 'subagent_start' || type === 'subagent_finish'
        || type === 'subagent_started' || type === 'subagent_finished') {
        applySubagentLifecycleToStore(sessionId, event);
        return { handled: false, subagentStateChanged: true };
    }
    return { handled: false };
}
