const messageStore = {
    sessions: new Map(),

    ensureSession(sessionId) {
        const sid = String(sessionId || '');
        if (!sid) return null;
        let st = this.sessions.get(sid);
        if (!st) {
            st = {
                sessionId: sid,
                events: [],
                eventsByIndex: new Map(),
                processEvents: [],
                messageEvents: [],
                rangeStart: 0,
                rangeEnd: 0,
                total: 0,
                loadedAt: 0,
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

    beginReplay(sessionId, meta) {
        const st = this.ensureSession(sessionId);
        if (!st) return null;
        st.events = [];
        st.eventsByIndex = new Map();
        st.processEvents = [];
        st.messageEvents = [];
        st.rangeStart = Number(meta && meta.range_start) || 0;
        st.rangeEnd = Number(meta && meta.range_end) || 0;
        st.total = Number(meta && meta.total) || 0;
        st.loadedAt = Date.now();
        return st;
    },

    applyEvent(sessionId, event, eventIndex, source) {
        const st = this.ensureSession(sessionId);
        if (!st || !event || typeof event !== 'object') return null;
        const idx = Number.isFinite(Number(eventIndex)) ? Number(eventIndex) : st.events.length;
        const prevRecord = st.eventsByIndex.get(idx) || null;
        const record = {
            index: idx,
            type: String(event.type || ''),
            event: event,
            source: source || 'unknown',
            at: Date.now(),
        };
        st.eventsByIndex.set(idx, record);
        const lastRecord = st.events.length ? st.events[st.events.length - 1] : null;
        if (!prevRecord && (!lastRecord || idx > lastRecord.index)) {
            st.events.push(record);
            if (record.type === 'user' || record.type === 'final') st.messageEvents.push(record);
            else st.processEvents.push(record);
        } else {
            st.events = Array.from(st.eventsByIndex.keys()).sort(function (a, b) { return a - b; })
                .map(function (key) { return st.eventsByIndex.get(key); });
            st.messageEvents = [];
            st.processEvents = [];
            st.events.forEach(function (item) {
                if (item.type === 'user' || item.type === 'final') st.messageEvents.push(item);
                else st.processEvents.push(item);
            });
        }
        st.rangeEnd = Math.max(st.rangeEnd || 0, idx + 1);
        st.total = Math.max(st.total || 0, st.rangeEnd);
        return record;
    },

    getSession(sessionId) {
        return this.sessions.get(String(sessionId || '')) || null;
    },
};

function beginMessageReplay(sessionId, meta) {
    return messageStore.beginReplay(sessionId, meta);
}

function clearMessageStateForSession(sessionId) {
    messageStore.clearSession(sessionId);
}

function applyMessageEvent(sessionId, event, eventIndex, source) {
    return messageStore.applyEvent(sessionId, event, eventIndex, source);
}
