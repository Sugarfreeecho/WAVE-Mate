const contextStore = {
    tokensBySession: new Map(),
    todoBySession: new Map(),

    setTokens(sessionId, estimated, threshold) {
        const sid = String(sessionId || '');
        if (!sid) return;
        if (estimated != null && Number(estimated) >= 0) {
            this.tokensBySession.set(sid, {
                estimated: Number(estimated),
                threshold: threshold,
                updatedAt: Date.now(),
            });
        } else {
            this.tokensBySession.delete(sid);
        }
    },

    getTokens(sessionId) {
        return this.tokensBySession.get(String(sessionId || '')) || null;
    },

    clearTokens(sessionId) {
        this.tokensBySession.delete(String(sessionId || ''));
    },

    setTodo(sessionId, payload) {
        const sid = String(sessionId || '');
        if (!sid) return null;
        const data = payload && typeof payload === 'object' ? payload : {};
        const items = Array.isArray(data.items) ? data.items.slice() : [];
        const done = typeof data.done === 'number'
            ? data.done
            : items.filter(function (x) { return x && x.status === 'completed'; }).length;
        const total = typeof data.total === 'number' ? data.total : items.length;
        const snapshot = {
            has_plan: !!(data.has_plan && items.length > 0),
            items: items,
            done: done,
            total: total,
            updatedAt: Date.now(),
        };
        this.todoBySession.set(sid, snapshot);
        return snapshot;
    },

    getTodo(sessionId) {
        return this.todoBySession.get(String(sessionId || '')) || null;
    },

    clearTodo(sessionId) {
        this.todoBySession.delete(String(sessionId || ''));
    },

    clearSession(sessionId) {
        const sid = String(sessionId || '');
        if (!sid) return;
        this.clearTokens(sid);
        this.clearTodo(sid);
    },
};

function setContextTokensForSession(sessionId, estimated, threshold) {
    contextStore.setTokens(sessionId, estimated, threshold);
}

function selectContextTokens(sessionId) {
    return contextStore.getTokens(sessionId);
}

function clearContextStateForSession(sessionId) {
    contextStore.clearSession(sessionId);
}

function applyTodoPlanToStore(sessionId, payload) {
    return contextStore.setTodo(sessionId, payload);
}

function selectTodoPlan(sessionId) {
    return contextStore.getTodo(sessionId);
}

function clearTodoPlanState(sessionId) {
    contextStore.clearTodo(sessionId);
}
