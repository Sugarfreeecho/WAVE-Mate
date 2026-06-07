const sessionStore = {
    sessionsById: new Map(),
    order: [],
    archivedCount: 0,
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
        this.order = nextOrder;
        this.streamActiveById = nextStreamActive;
        if (Number.isFinite(Number(archivedCount)) && Number(archivedCount) >= 0) {
            this.archivedCount = Number(archivedCount);
        }
    },

    upsert(session) {
        if (!session || !session.id) return;
        const sid = String(session.id);
        this.sessionsById.set(sid, session);
        if (this.order.indexOf(sid) < 0) this.order.unshift(sid);
        if (Object.prototype.hasOwnProperty.call(session, 'stream_active')) {
            this.streamActiveById[sid] = !!session.stream_active;
        }
    },

    remove(sessionId) {
        const sid = String(sessionId || '');
        if (!sid) return;
        this.sessionsById.delete(sid);
        delete this.streamActiveById[sid];
        this.order = this.order.filter(function (id) { return id !== sid; });
    },

    list() {
        const out = [];
        for (let i = 0; i < this.order.length; i += 1) {
            const s = this.sessionsById.get(this.order[i]);
            if (s) out.push(s);
        }
        return out;
    },

    get(sessionId) {
        return this.sessionsById.get(String(sessionId || '')) || null;
    },

    setArchivedCount(count) {
        if (Number.isFinite(Number(count)) && Number(count) >= 0) {
            this.archivedCount = Number(count);
        }
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
    if (typeof serverStreamActiveBySession !== 'undefined' && serverStreamActiveBySession) {
        serverStreamActiveBySession[sid] = false;
    }
}

function setSessionServerStreamActive(sessionId, active) {
    const sid = String(sessionId || '');
    if (!sid) return;
    if (!active) delete sessionStreamStopSuppressUntil[sid];
    if (active && isSessionStreamStopSuppressed(sid)) active = false;
    sessionStore.setStreamActive(sid, !!active);
    if (typeof serverStreamActiveBySession !== 'undefined' && serverStreamActiveBySession) {
        serverStreamActiveBySession[sid] = !!active;
    }
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
    if (typeof serverStreamActiveBySession !== 'undefined') {
        serverStreamActiveBySession = m;
    }
}
