function selectCurrentSession() {
    return sessionStore.get(sessionStore.currentSessionId);
}

function selectAllSessions() {
    return sessionStore.list();
}

function selectArchivedSessions() {
    return sessionStore.archivedList();
}

function selectSessionSections() {
    const pinnedList = [];
    const normalList = [];
    const allSessions = selectAllSessions();
    for (let i = 0; i < allSessions.length; i += 1) {
        const s = allSessions[i];
        if (!s || !s.id || !!s.archived) continue;
        if (s.pinned) pinnedList.push(s);
        else normalList.push(s);
    }
    return {
        pinned: pinnedList,
        normal: normalList,
        archived: selectArchivedSessions(),
    };
}

function selectArchivedDisplayCount() {
    return sessionStore.archivedLoaded ? selectArchivedSessions().length : sessionStore.archivedCount;
}

function selectIsSessionRunning(sessionId) {
    return !!(sessionId && (sessionStore.hasRun(sessionId) || isServerStreamActive(sessionId)));
}

function selectRunForSession(sessionId) {
    return sessionStore.getRun(sessionId);
}
