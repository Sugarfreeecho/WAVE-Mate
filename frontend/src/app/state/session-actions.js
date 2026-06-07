function applySessionSnapshot(snapshot) {
    snapshot = snapshot || {};
    const sessions = Array.isArray(snapshot.sessions) ? snapshot.sessions : [];
    const archivedCount = snapshot.archived_count != null ? snapshot.archived_count : snapshot.archivedCount;
    if (Number.isFinite(Number(snapshot.seq)) && Number(snapshot.seq) > sessionStore.seq) {
        sessionStore.seq = Number(snapshot.seq);
    }
    sessionStore.applySnapshot(sessions, archivedCount);
    if (snapshot.current_session_id || snapshot.currentSessionId) {
        sessionStore.setCurrentSession(snapshot.current_session_id || snapshot.currentSessionId);
    }
    if (Array.isArray(snapshot.active_runs)) {
        const active = Object.create(null);
        snapshot.active_runs.forEach(function (run) {
            const sid = typeof run === 'string' ? run : (run && run.session_id);
            if (sid) active[String(sid)] = true;
        });
        applyServerStreamActiveMap(active);
    }
}

function applySessionPatch(patch) {
    patch = patch || {};
    if (Number.isFinite(Number(patch.seq)) && Number(patch.seq) <= sessionStore.seq) return;
    if (Number.isFinite(Number(patch.seq))) sessionStore.seq = Number(patch.seq);
    if (patch.session) sessionStore.upsert(patch.session);
    if (patch.remove_session_id || patch.removedSessionId) {
        sessionStore.remove(patch.remove_session_id || patch.removedSessionId);
    }
    if (patch.current_session_id || patch.currentSessionId) {
        sessionStore.setCurrentSession(patch.current_session_id || patch.currentSessionId);
    }
    if (patch.archived_count != null || patch.archivedCount != null) {
        sessionStore.setArchivedCount(patch.archived_count != null ? patch.archived_count : patch.archivedCount);
    }
    if (patch.stream_active != null && (patch.session_id || patch.sessionId)) {
        setSessionServerStreamActive(patch.session_id || patch.sessionId, !!patch.stream_active);
    }
}

function setCurrentSessionState(sessionId) {
    currentSessionId = sessionId || null;
    sessionStore.setCurrentSession(currentSessionId);
}

function setSessionRunState(sessionId, run) {
    const sid = String(sessionId || '');
    if (!sid) return;
    sessionStore.setRun(sid, run || null);
    if (run) runningBySession[sid] = run;
    else delete runningBySession[sid];
}

function getSessionRunState(sessionId) {
    const sid = String(sessionId || '');
    if (!sid) return null;
    return sessionStore.getRun(sid) || runningBySession[sid] || null;
}

function clearSessionRunState(sessionId) {
    setSessionRunState(sessionId, null);
}
