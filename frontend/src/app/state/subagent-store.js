const subagentStore = {
    sessions: new Map(),

    ensureSession(sessionId) {
        const sid = String(sessionId || '');
        if (!sid) return null;
        let st = this.sessions.get(sid);
        if (!st) {
            st = {
                sessionId: sid,
                itemsById: new Map(),
                order: [],
                runningIds: new Set(),
                pendingResultIds: new Set(),
                eventCountsById: new Map(),
                snapshotLoaded: false,
                updatedAt: 0,
            };
            this.sessions.set(sid, st);
        }
        return st;
    },

    clearSession(sessionId) {
        const sid = String(sessionId || '');
        if (!sid) return;
        this.sessions.delete(sid);
    },

    applySnapshot(sessionId, flat) {
        const st = this.ensureSession(sessionId);
        if (!st) return null;
        const list = Array.isArray(flat) ? flat : [];
        const nextById = new Map();
        const nextOrder = [];
        const nextRunning = new Set();
        const nextPending = new Set();
        list.forEach(function (node) {
            if (!node || !node.id) return;
            const id = String(node.id);
            const prev = st.itemsById.get(id) || {};
            const merged = Object.assign({}, prev, node, { id: id });
            nextById.set(id, merged);
            nextOrder.push(id);
            if (merged.running) nextRunning.add(id);
            if (merged.pending_continue || merged.pending_result || merged.can_continue) nextPending.add(id);
        });
        st.itemsById = nextById;
        st.order = nextOrder;
        st.runningIds = nextRunning;
        st.pendingResultIds = nextPending;
        st.snapshotLoaded = true;
        st.updatedAt = Date.now();
        return st;
    },

    applyLifecycleEvent(sessionId, event) {
        const st = this.ensureSession(sessionId);
        if (!st || !event || typeof event !== 'object') return null;
        const id = String(event.agent_id || event.run_id || '');
        if (!id) return null;
        const prev = st.itemsById.get(id) || { id: id };
        const next = Object.assign({}, prev, {
            id: id,
            description: event.description || prev.description || id,
            subagent_type: event.subagent_type || prev.subagent_type || '',
            updated_at: Date.now(),
        });
        if (event.type === 'subagent_start' || event.type === 'subagent_started') {
            next.running = true;
            next.status = 'running';
            st.runningIds.add(id);
            st.pendingResultIds.delete(id);
        } else if (event.type === 'subagent_finish' || event.type === 'subagent_finished') {
            next.running = false;
            next.status = event.ok === false ? 'failed' : 'finished';
            if (event.result_preview) next.result_preview = String(event.result_preview);
            if (event.error) next.error = String(event.error);
            st.runningIds.delete(id);
            st.pendingResultIds.add(id);
        }
        st.itemsById.set(id, next);
        if (st.order.indexOf(id) < 0) st.order.unshift(id);
        st.updatedAt = Date.now();
        return next;
    },

    remove(sessionId, agentId) {
        const st = this.ensureSession(sessionId);
        const id = String(agentId || '');
        if (!st || !id) return;
        st.itemsById.delete(id);
        st.runningIds.delete(id);
        st.pendingResultIds.delete(id);
        st.eventCountsById.delete(id);
        st.order = st.order.filter(function (x) { return x !== id; });
        st.updatedAt = Date.now();
    },

    setEventCount(sessionId, agentId, count) {
        const st = this.ensureSession(sessionId);
        const id = String(agentId || '');
        const n = Number(count);
        if (!st || !id || !Number.isFinite(n)) return;
        st.eventCountsById.set(id, Math.max(0, n));
    },

    getEventCount(sessionId, agentId) {
        const st = this.sessions.get(String(sessionId || ''));
        if (!st) return 0;
        return Number(st.eventCountsById.get(String(agentId || '')) || 0);
    },

    getSession(sessionId) {
        return this.sessions.get(String(sessionId || '')) || null;
    },
};

function applySubagentSnapshot(sessionId, flat) {
    return subagentStore.applySnapshot(sessionId, flat);
}

function applySubagentLifecycleToStore(sessionId, event) {
    return subagentStore.applyLifecycleEvent(sessionId, event);
}

function clearSubagentStateForSession(sessionId) {
    subagentStore.clearSession(sessionId);
}
