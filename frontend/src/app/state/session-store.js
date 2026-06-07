const sessionStore = {
    seq: 0,
    sessionsById: new Map(),
    sessionOrder: [],
    currentSessionId: null,
    runsBySession: new Map(),
    archivedCount: 0,
    archivedLoaded: false,
    archivedSessions: null,
    unreadComplete: new Set(),
    ui: {
        loadingSessions: false,
        loadingMessages: false,
    },
    streamActiveById: Object.create(null),

    applySnapshot(sessions, archivedCount) {
        const nextById = new Map();
        const nextOrder = [];
        const nextStreamActive = Object.create(null);
        const list = Array.isArray(sessions) ? sessions : [];
        for (let i = 0; i < list.length; i += 1) {
            const s = list[i];
            if (!s || !s.id) continue;
            const sid = String(s.id);
            nextById.set(sid, s);
            nextOrder.push(sid);
            nextStreamActive[sid] = !!s.stream_active;
        }
        this.sessionsById = nextById;
        this.sessionOrder = nextOrder;
        this.streamActiveById = nextStreamActive;
        if (Number.isFinite(Number(archivedCount)) && Number(archivedCount) >= 0) {
            this.archivedCount = Number(archivedCount);
        }
    },

    upsert(session) {
        if (!session || !session.id) return;
        const sid = String(session.id);
        this.sessionsById.set(sid, session);
        if (this.sessionOrder.indexOf(sid) < 0) this.sessionOrder.unshift(sid);
        if (Object.prototype.hasOwnProperty.call(session, 'stream_active')) {
            this.streamActiveById[sid] = !!session.stream_active;
        }
    },

    remove(sessionId) {
        const sid = String(sessionId || '');
        if (!sid) return;
        this.sessionsById.delete(sid);
        delete this.streamActiveById[sid];
        this.runsBySession.delete(sid);
        this.unreadComplete.delete(sid);
        this.sessionOrder = this.sessionOrder.filter(function (id) { return id !== sid; });
    },

    list() {
        const out = [];
        for (let i = 0; i < this.sessionOrder.length; i += 1) {
            const s = this.sessionsById.get(this.sessionOrder[i]);
            if (s) out.push(s);
        }
        return out;
    },

    get(sessionId) {
        return this.sessionsById.get(String(sessionId || '')) || null;
    },

    setCurrentSession(sessionId) {
        this.currentSessionId = sessionId ? String(sessionId) : null;
    },

    setArchivedCount(count) {
        if (Number.isFinite(Number(count)) && Number(count) >= 0) {
            this.archivedCount = Number(count);
        }
    },

    setArchivedLoaded(sessions) {
        const list = Array.isArray(sessions)
            ? sessions.filter(function (s) { return s && s.id && !!s.archived; })
            : [];
        this.archivedLoaded = true;
        this.archivedSessions = list;
        this.archivedCount = list.length;
    },

    clearArchivedLoaded() {
        this.archivedLoaded = false;
        this.archivedSessions = null;
    },

    archivedList() {
        return this.archivedLoaded && Array.isArray(this.archivedSessions) ? this.archivedSessions : [];
    },

    isStreamActive(sessionId) {
        const sid = String(sessionId || '');
        if (!sid) return false;
        if (Object.prototype.hasOwnProperty.call(this.streamActiveById, sid)) {
            return !!this.streamActiveById[sid];
        }
        const sess = this.get(sid);
        return !!(sess && sess.stream_active);
    },

    setStreamActive(sessionId, active) {
        const sid = String(sessionId || '');
        if (!sid) return;
        this.streamActiveById[sid] = !!active;
        const sess = this.sessionsById.get(sid);
        if (sess) sess.stream_active = !!active;
    },

    applyStreamActiveMap(activeMap) {
        const next = Object.create(null);
        const src = activeMap || {};
        Object.keys(src).forEach(function (sid) {
            next[String(sid)] = !!src[sid];
        });
        this.streamActiveById = next;
        this.sessionsById.forEach(function (sess, sid) {
            sess.stream_active = !!next[sid];
        });
    },

    setRun(sessionId, run) {
        const sid = String(sessionId || '');
        if (!sid) return;
        if (run) this.runsBySession.set(sid, run);
        else this.runsBySession.delete(sid);
    },

    getRun(sessionId) {
        return this.runsBySession.get(String(sessionId || '')) || null;
    },

    hasRun(sessionId) {
        return this.runsBySession.has(String(sessionId || ''));
    },
};

const SESSION_STREAM_STOP_SUPPRESS_MS = 15000;
const sessionStreamStopSuppressUntil = Object.create(null);

function isSessionStreamStopSuppressed(sessionId) {
    const sid = String(sessionId || '');
    if (!sid) return false;
    const until = Number(sessionStreamStopSuppressUntil[sid] || 0);
    if (!until) return false;
    if (Date.now() <= until) return true;
    delete sessionStreamStopSuppressUntil[sid];
    return false;
}

function suppressSessionServerStreamActive(sessionId, ms) {
    const sid = String(sessionId || '');
    if (!sid) return;
    sessionStreamStopSuppressUntil[sid] = Date.now() + (Number(ms) > 0 ? Number(ms) : SESSION_STREAM_STOP_SUPPRESS_MS);
    sessionStore.setStreamActive(sid, false);
}

function setSessionServerStreamActive(sessionId, active) {
    const sid = String(sessionId || '');
    if (!sid) return;
    if (!active) delete sessionStreamStopSuppressUntil[sid];
    if (active && isSessionStreamStopSuppressed(sid)) active = false;
    sessionStore.setStreamActive(sid, !!active);
}

function isServerStreamActive(sessionId) {
    const sid = String(sessionId || '');
    if (!sid) return false;
    if (isSessionStreamStopSuppressed(sid)) return false;
    return sessionStore.isStreamActive(sid);
}

function applyServerStreamActiveMap(activeMap) {
    const src = activeMap || Object.create(null);
    const m = Object.create(null);
    Object.keys(src).forEach(function (sid) {
        var active = !!src[sid];
        if (!active) delete sessionStreamStopSuppressUntil[sid];
        if (active && isSessionStreamStopSuppressed(sid)) active = false;
        m[sid] = active;
    });
    sessionStore.applyStreamActiveMap(m);
}
