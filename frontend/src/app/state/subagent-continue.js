var subagentContinueInFlight = false;
var subagentContinueBannerTimer = null;
var subagentContinueDismissedForSession = Object.create(null);

function hideSubagentContinueBanner() {
    var banner = document.getElementById('subagent-continue-banner');
    if (!banner) return;
    var mode = banner && banner.dataset ? String(banner.dataset.continueMode || '') : '';
    banner.classList.remove('is-on');
}

function dismissSubagentContinueBanner(sessionId) {
    var sid = sessionId || currentSessionId;
    if (sid) subagentContinueDismissedForSession[sid] = true;
    hideSubagentContinueBanner();
    if (sid) {
        fetch('/sessions/' + encodeURIComponent(sid) + '/continue-subagents/dismiss', { method: 'POST' })
            .catch(function () { /* ignore */ });
    }
}

function showSubagentContinueBanner(pendingCount) {
    var banner = document.getElementById('subagent-continue-banner');
    if (!banner) return;
    var n = Math.max(1, parseInt(String(pendingCount), 10) || 1);
    var msg = banner.querySelector('.subagent-continue-banner-msg');
    if (msg) {
        msg.textContent = n + ' 个子任务已完成，点击继续让主 Agent 综合子任务结果（不会自动续跑）。';
    }
    banner.classList.add('is-on');
}

async function fetchSubagentContinueState(sessionId) {
    if (!sessionId) return { pending: 0, running: 0, can_continue: false };
    try {
        var r = await fetch('/sessions/' + encodeURIComponent(sessionId) + '?include_subagents=true');
        if (!r.ok) return { pending: 0, running: 0, can_continue: false };
        var j = await r.json();
        return {
            pending: Number(j.subagent_pending_continue || 0),
            running: Number(j.subagent_running || 0),
            can_continue: !!j.subagent_can_continue,
        };
    } catch (e) {
        return { pending: 0, running: 0, can_continue: false };
    }
}

function updateSubagentContinueBanner(sessionId) {
    if (!sessionId || sessionId !== currentSessionId || replayingMessages) {
        hideSubagentContinueBanner();
        return;
    }
    if (subagentContinueDismissedForSession[sessionId]) {
        hideSubagentContinueBanner();
        return;
    }
    if (subagentContinueBannerTimer) clearTimeout(subagentContinueBannerTimer);
    subagentContinueBannerTimer = setTimeout(function () {
        subagentContinueBannerTimer = null;
        void (async function () {
            var st = await fetchSubagentContinueState(sessionId);
            if (sessionId !== currentSessionId) return;
            if (st.can_continue && st.pending > 0 && st.running === 0
                && !isSessionRunning(sessionId) && !subagentContinueInFlight) {
                showSubagentContinueBanner(st.pending);
            } else {
                hideSubagentContinueBanner();
            }
        })();
    }, 280);
}

async function tryMarkSessionUnreadComplete(sessionId) {
    if (!sessionId || sessionId === currentSessionId) return;
    try {
        var r = await fetch('/sessions/' + encodeURIComponent(sessionId) + '?include_subagents=true');
        if (!r.ok) return;
        var j = await r.json();
        if (j.stream_active || Number(j.subagent_running || 0) > 0) return;
        sessionUnreadComplete.add(sessionId);
        persistSessionUnread();
        syncSessionListIndicatorClasses();
    } catch (e) { /* ignore */ }
}
