function applySessionSnapshot(snapshot) {
    snapshot = snapshot || {};
    const requestSeq = Number(snapshot.client_request_seq || 0);
    if (requestSeq > 0 && requestSeq < sessionStore.lastAppliedSnapshotRequestSeq) return false;
    if (requestSeq > 0) sessionStore.lastAppliedSnapshotRequestSeq = requestSeq;
    const sessions = Array.isArray(snapshot.sessions) ? snapshot.sessions : [];
    const archivedCount = snapshot.archived_count != null ? snapshot.archived_count : snapshot.archivedCount;
    const previousActive = new Set();
    sessionStore.activeRunInfoBySession.forEach(function (_run, sid) {
        if (sid) previousActive.add(String(sid));
    });
    if (Number.isFinite(Number(snapshot.seq)) && Number(snapshot.seq) > sessionStore.seq) {
        sessionStore.seq = Number(snapshot.seq);
    }
    sessionStore.applySnapshot(sessions, archivedCount);
    if (sessionStore.archivedLoaded && (snapshot.include_archived || snapshot.includeArchived)) {
        const loadedCount = Array.isArray(sessionStore.archivedSessions)
            ? sessionStore.archivedSessions.length
            : 0;
        const visibleCount = sessionStore.archivedVisibleCount;
        const archived = sessions.filter(function (s) { return s && s.id && !!s.archived; });
        sessionStore.setArchivedLoaded(archived.slice(0, loadedCount), {
            visibleCount: visibleCount,
            totalCount: archivedCount,
        });
    }
    if (snapshot.current_session_id || snapshot.currentSessionId) {
        sessionStore.setCurrentSession(snapshot.current_session_id || snapshot.currentSessionId);
    }
    if (Array.isArray(snapshot.active_runs)) {
        sessionStore.applyActiveRuns(snapshot.active_runs);
        const active = Object.create(null);
        sessionStore.activeRunInfoBySession.forEach(function (_run, sid) {
            if (sid) active[String(sid)] = true;
        });
        applyServerStreamActiveMap(active);
        if (typeof recoverFollowupQueueDrainsFromSessionSnapshot === 'function') {
            recoverFollowupQueueDrainsFromSessionSnapshot(previousActive, new Set(Object.keys(active)));
        }
    }
    return true;
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
    if (typeof refreshPermissionModeSelector === 'function') refreshPermissionModeSelector(currentSessionId);
}

function setSessionRunState(sessionId, run) {
    const sid = String(sessionId || '');
    if (!sid) return;
    sessionStore.setRun(sid, run || null);
    if (typeof updateSidebarRuntimeStatus === 'function') updateSidebarRuntimeStatus(true);
}

function getSessionRunState(sessionId) {
    const sid = String(sessionId || '');
    if (!sid) return null;
    return sessionStore.getRun(sid) || null;
}

function clearSessionRunState(sessionId) {
    setSessionRunState(sessionId, null);
}

function clearSessionRunStateIfMatch(sessionId, runId) {
    const sid = String(sessionId || '');
    if (!sid) return;
    const expected = String(runId || '');
    if (!expected) {
        clearSessionRunState(sid);
        return;
    }
    const run = getSessionRunState(sid);
    if (!run || String(run.runId || '') === expected) {
        clearSessionRunState(sid);
    }
}

function markSessionRunInactive(sessionId) {
    const sid = String(sessionId || '');
    if (!sid) return;
    setSessionServerStreamActive(sid, false);
    sessionStore.activeRunInfoBySession.delete(sid);
    const sess = sessionStore.get(sid);
    if (sess) {
        sess.run_active = false;
        sess.run_started_at = null;
        sess.stream_active = false;
    }
    if (typeof updateSidebarRuntimeStatus === 'function') updateSidebarRuntimeStatus(true);
}

function markRunAbortReason(run, reason) {
    if (!run) return;
    var r = reason || 'cleanup';
    run.abortReason = r;
    if (run.ctx) run.ctx.abortReason = r;
}

function getRunAbortReason(sessionId, ctx) {
    const run = getSessionRunState(sessionId);
    return (run && run.abortReason) || (ctx && ctx.abortReason) || '';
}

function abortSessionRun(sessionId, reason, opts) {
    opts = opts || {};
    const run = getSessionRunState(sessionId);
    if (!run) return null;
    markRunAbortReason(run, reason || 'cleanup');
    try { if (run.controller) run.controller.abort(); } catch (e) { /* ignore */ }
    if (opts.clear !== false) clearSessionRunState(sessionId);
    return run;
}
