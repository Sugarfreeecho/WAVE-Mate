var subagentCardSyncTimer = null;
var subagentPanelOpen = false;
var subagentPanelBound = false;
var subagentDockExpanded = false;

var subagentContinueInFlight = false;
var subagentContinueBannerTimer = null;
var subagentContinueDismissedForSession = Object.create(null);
var subagentPanelRefreshSeq = 0;
var subagentBodyHtmlCache = Object.create(null);
var subagentContextFetchInFlight = Object.create(null);
var subagentTreeRefreshTimer = null;
var subagentTreeRefreshTarget = null;
var subagentTreeRefreshInflight = null;
var subagentTreeRefreshInflightSid = null;
var subagentTreeRefreshQueued = false;
var subagentCardViewportObserver = null;
var subagentCardLoadQueue = [];
var subagentCardLoadInflight = 0;
var subagentCardLoadQueued = Object.create(null);
var SUBAGENT_BODY_LOAD_CONCURRENCY = 2;
var SUBAGENT_DETAIL_RENDER_BATCH = 8;
var SUBAGENT_HISTORY_TURNS_PER_PAGE = 3;
var subagentStatsRefreshRaf = 0;
var subagentStatsPending = new Set();

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

function shouldStreamSubagentSummaryDom(card) {
    return !!(subagentPanelOpen && card);
}

function shouldStreamSubagentProcessDom(card) {
    if (!card || !subagentPanelOpen) return false;
    return card.classList.contains('is-expanded');
}

function shouldStreamSubagentCardDom(card) {
    return shouldStreamSubagentProcessDom(card);
}

function subagentBodyIsLoaded(body) {
    return !!(body && body.dataset.loaded === '1' && body.dataset.stashed !== '1'
        && body.innerHTML.trim() && !body.querySelector('.subagent-detail-empty')
        && !body.querySelector('.subagent-card-summary'));
}

function buildSubagentCardSummaryHtml(previewText, muted) {
    var t = formatSubagentSummaryText(previewText);
    if (!t) {
        return '<div class="subagent-card-summary subagent-card-summary--muted">'
            + escapeHtml(muted ? String(muted) : '展开查看执行过程') + '</div>';
    }
    if (t.length > 1200) t = t.slice(0, 1199) + '\u2026';
    return '<div class="subagent-card-summary">' + escapeHtml(t) + '</div>';
}

function formatSubagentSummaryText(text) {
    var t = String(text || '').replace(/\r\n/g, '\n').trim();
    if (!t) return '';
    t = t.replace(/```[\s\S]*?```/g, function (m) {
        return m.replace(/^```[^\n]*\n?/, '').replace(/\n?```$/, '');
    });
    t = t.replace(/^\s{0,3}#{1,6}\s+/gm, '');
    t = t.replace(/^\s{0,3}[-*_]{3,}\s*$/gm, '');
    t = t.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1');
    t = t.replace(/`([^`]+)`/g, '$1');
    t = t.replace(/(\*\*|__)(.*?)\1/g, '$2');
    t = t.replace(/(\*|_)(.*?)\1/g, '$2');
    t = t.replace(/^\s{0,3}>\s?/gm, '');
    t = t.replace(/^\s{0,3}[-*+]\s+/gm, '• ');
    t = t.replace(/\n{3,}/g, '\n\n');
    return t.trim();
}

function updateSubagentCardSummaryOnly(card, previewText) {
    if (!card) return;
    var body = card.querySelector('.subagent-card-body');
    if (!body) return;
    var p = previewText != null ? String(previewText) : String(card.dataset.resultPreview || '');
    card.dataset.resultPreview = p;
    if (subagentBodyIsLoaded(body)) return;
}

function stashSubagentCardBodyForCollapse(card) {
    if (!card) return;
    var body = card.querySelector('.subagent-card-body');
    if (!body || body.dataset.stashed === '1') return;
    if (subagentBodyIsLoaded(body) && body.dataset.finalOnly !== '1') {
        var aid = card.getAttribute('data-agent-id');
        if (currentSessionId && aid) {
            var hasCleanCache = body.dataset.cacheClean === '1' && !!readSubagentBodyCache(currentSessionId, aid);
            if (!hasCleanCache) {
                rememberSubagentBodyCache(currentSessionId, aid, body.innerHTML);
                body.dataset.cacheClean = '1';
            }
        }
    }
    body.dataset.stashed = '1';
    delete body.dataset.renderToken;
    delete body.dataset.rendering;
    body.innerHTML = '';
    delete body.dataset.loaded;
    delete body.dataset.streamReady;
    delete body._subagentStreamCtx;
}

function restoreSubagentCardBodyFromStash(card, sessionId) {
    if (!card) return false;
    var body = card.querySelector('.subagent-card-body');
    var aid = card.getAttribute('data-agent-id');
    if (!body) return false;
    var cached = readSubagentBodyCache(sessionId, aid);
    if (cached && isSubagentBodyCacheComplete(cached)) {
        body.innerHTML = cached;
        body.dataset.loaded = '1';
        body.dataset.cacheClean = '1';
        delete body.dataset.stashed;
        rebindSubagentCardBody(body, card, aid);
        return true;
    }
    if (body.dataset.stashed === '1') {
        delete body.dataset.stashed;
        body.innerHTML = '';
    }
    return false;
}

function scheduleSubagentDetailWork(fn) {
    setTimeout(fn, 0);
}

function stashSubagentInactiveBodies(grid, keepCard) {
    if (!grid) return;
    grid.querySelectorAll('.subagent-grid-card').forEach(function (card) {
        if (keepCard && card === keepCard) return;
        if (card.classList.contains('is-expanded')) return;
        stashSubagentCardBodyForCollapse(card);
    });
}

function scheduleSubagentCardStats(card) {
    if (!card) return;
    /* 不可见的 card（折叠且未进入视口）不算 stats：算了用户也看不见，省一次 querySelectorAll。 */
    if (subagentPanelOpen
        && !card.classList.contains('is-expanded')
        && card.dataset.viewportVisible !== '1') return;
    subagentStatsPending.add(card);
    if (subagentStatsRefreshRaf) return;
    /* RAF 在多 subagent 流量高时每帧都触发；改为 timeout 节流 250ms，合并连续 delta 的统计。 */
    subagentStatsRefreshRaf = setTimeout(function () {
        subagentStatsRefreshRaf = 0;
        var cards = Array.from(subagentStatsPending);
        subagentStatsPending.clear();
        cards.forEach(refreshSubagentCardStats);
    }, 250);
}

function getSubagentIncrementalSyncDelay(runningCount) {
    /* SSE 已在为父会话推子 agent 增量，轮询仅作兜底；大幅退避以让出主线程。 */
    if (isSessionRunning(currentSessionId)) return 8000;
    if (runningCount > 20) return 6000;
    if (runningCount > 10) return 4000;
    if (runningCount > 5) return 3000;
    return 2200;
}

function runTasksWithConcurrency(items, limit, worker) {
    if (!items || !items.length) return Promise.resolve();
    var idx = 0;
    var n = Math.max(1, Math.min(limit || 1, items.length));
    function next() {
        if (idx >= items.length) return Promise.resolve();
        var cur = idx++;
        return Promise.resolve(worker(items[cur], cur)).then(next);
    }
    var starters = [];
    for (var i = 0; i < n; i += 1) starters.push(next());
    return Promise.all(starters);
}

function setSubagentCardEventCount(agentId, count) {
    var aid = String(agentId || '');
    var n = Number(count);
    if (!aid || !Number.isFinite(n)) return;
    n = Math.max(0, n);
    if (currentSessionId) subagentStore.setEventCount(currentSessionId, aid, n);
}

function bumpSubagentCardEventCount(agentId, eventIndex, increment) {
    var aid = String(agentId || '');
    if (!aid) return;
    var prev = currentSessionId ? subagentStore.getEventCount(currentSessionId, aid) : 0;
    if (typeof eventIndex === 'number' && eventIndex >= 0) {
        setSubagentCardEventCount(aid, Math.max(prev, eventIndex + 1));
    } else if (increment) {
        setSubagentCardEventCount(aid, prev + 1);
    }
}

function trackSubagentStreamEventLightweight(card, agentId, event, eventIndex) {
    if (!card || !agentId || !event) return;
    var t = event.type;
    bumpSubagentCardEventCount(agentId, eventIndex, !event.ephemeral);
    if (t === 'context_tokens') {
        card.dataset.procCtxEstimated = String(event.estimated);
        card.dataset.procCtxThreshold = String(event.threshold);
    } else if (t === 'process_metrics') {
        applySubagentProcessMetricsToCard(card, event);
    } else if (t === 'cache_stats') {
        if (event.cache_hit != null) card.dataset.procCacheHit = String(Math.max(0, Math.floor(Number(event.cache_hit))));
        if (event.cache_miss != null) card.dataset.procCacheMiss = String(Math.max(0, Math.floor(Number(event.cache_miss))));
        if (event.hit_rate != null) card.dataset.procCacheRate = String(Math.max(0, Number(event.hit_rate)));
        if (event.model != null) card.dataset.procCacheModel = String(event.model);
    }
    if (event.react_iter != null) bumpAggregateMaxReactIter(card, event.react_iter);
    scheduleSubagentCardStats(card);
}

function ensureSubagentCardViewportObserver(grid) {
    if (!grid || subagentCardViewportObserver) return;
    subagentCardViewportObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
            var card = entry.target;
            if (!card || !card.isConnected) return;
            if (entry.isIntersecting) {
                card.dataset.viewportVisible = '1';
                card.classList.add('is-viewport-visible');
                queueSubagentCardBodyLoad(card, currentSessionId);
            } else if (!card.classList.contains('is-expanded')) {
                card.dataset.viewportVisible = '0';
                card.classList.remove('is-viewport-visible');
                stashSubagentCardBodyForCollapse(card);
            }
        });
    }, { root: grid, rootMargin: '160px 0px', threshold: 0.01 });
}

function observeSubagentCardViewport(card) {
    if (!card) return;
    ensureSubagentCardViewportObserver(document.getElementById('subagent-grid'));
    if (subagentCardViewportObserver) subagentCardViewportObserver.observe(card);
}

function disconnectSubagentCardViewportObserver() {
    if (subagentCardViewportObserver) {
        subagentCardViewportObserver.disconnect();
        subagentCardViewportObserver = null;
    }
    subagentCardLoadQueue = [];
    subagentCardLoadInflight = 0;
    subagentCardLoadQueued = Object.create(null);
}

function drainSubagentCardLoadQueue() {
    if (!shouldLoadSubagentCardBodies()) return;
    while (subagentCardLoadInflight < SUBAGENT_BODY_LOAD_CONCURRENCY && subagentCardLoadQueue.length) {
        var job = subagentCardLoadQueue.shift();
        if (!job || !job.card || !job.card.isConnected) {
            if (job && job.agentId) delete subagentCardLoadQueued[job.agentId];
            continue;
        }
        var body = job.card.querySelector('.subagent-card-body');
        if (!job.card.classList.contains('is-expanded') && job.card.dataset.viewportVisible !== '1') {
            delete subagentCardLoadQueued[job.agentId];
            stashSubagentCardBodyForCollapse(job.card);
            continue;
        }
        var finalOnlyNeedsFull = job.card.classList.contains('is-expanded') && body && body.dataset.finalOnly === '1';
        if (!body || body.dataset.loading === '1' || (subagentBodyIsLoaded(body) && !finalOnlyNeedsFull)) {
            delete subagentCardLoadQueued[job.agentId];
            continue;
        }
        subagentCardLoadInflight += 1;
        (function (card, agentId, sessionId) {
            var cached = readSubagentBodyCache(sessionId, agentId);
            if (card.classList.contains('is-expanded') && cached && isSubagentBodyCacheComplete(cached)) {
                body.innerHTML = cached;
                body.dataset.loaded = '1';
                body.dataset.cacheClean = '1';
                delete body.dataset.finalOnly;
                body.classList.remove('is-final-only');
                delete body.dataset.loading;
                rebindSubagentCardBody(body, card, agentId);
                body._subagentStreamCtx = getSubagentCardStreamCtx(body, card, agentId);
                subagentCardLoadInflight -= 1;
                delete subagentCardLoadQueued[agentId];
                drainSubagentCardLoadQueue();
                return;
            }
            loadSubagentDetailInto(body, agentId, card, sessionId).finally(function () {
                subagentCardLoadInflight -= 1;
                delete subagentCardLoadQueued[agentId];
                drainSubagentCardLoadQueue();
            });
        })(job.card, job.agentId, job.sessionId);
    }
}

function queueSubagentCardBodyLoad(card, sessionIdOpt) {
    if (!card || !shouldLoadSubagentCardBodies()) return;
    if (!card.classList.contains('is-expanded') && card.dataset.viewportVisible !== '1') return;
    var sessionId = sessionIdOpt || currentSessionId;
    var agentId = card.getAttribute('data-agent-id');
    if (!agentId || subagentCardLoadQueued[agentId]) return;
    var body = card.querySelector('.subagent-card-body');
    if (!body || body.dataset.loading === '1') return;
    if (subagentBodyIsLoaded(body) && !(card.classList.contains('is-expanded') && body.dataset.finalOnly === '1')) return;
    subagentCardLoadQueued[agentId] = true;
    subagentCardLoadQueue.push({ card: card, agentId: agentId, sessionId: sessionId });
    drainSubagentCardLoadQueue();
}

function cardIntersectsGridViewport(card, grid) {
    if (!card || !grid || !card.isConnected) return false;
    var cr = card.getBoundingClientRect();
    var gr = grid.getBoundingClientRect();
    return cr.bottom > gr.top + 4 && cr.top < gr.bottom - 4;
}

function scheduleRefreshSubagentTreePanel(sessionId, delayMs) {
    if (!sessionId || replayingMessages) return;
    subagentTreeRefreshTarget = sessionId;
    if (subagentTreeRefreshTimer) clearTimeout(subagentTreeRefreshTimer);
    subagentTreeRefreshTimer = setTimeout(function () {
        subagentTreeRefreshTimer = null;
        var sid = subagentTreeRefreshTarget;
        subagentTreeRefreshTarget = null;
        if (sid && sid === currentSessionId) void refreshSubagentTreePanel(sid);
    }, delayMs == null ? 150 : delayMs);
}

function cancelScheduledSubagentTreeRefresh() {
    if (subagentTreeRefreshTimer) {
        clearTimeout(subagentTreeRefreshTimer);
        subagentTreeRefreshTimer = null;
    }
    subagentTreeRefreshTarget = null;
    subagentTreeRefreshQueued = false;
}

function subagentBodyCacheKey(sessionId, agentId) {
    return String(sessionId || '') + ':' + String(agentId || '');
}

function isSubagentDetailPendingHtml(html) {
    return !html || html.indexOf('加载中') >= 0;
}

function forgetSubagentBodyCache(sessionId, agentId) {
    if (sessionId && agentId) {
        delete subagentBodyHtmlCache[subagentBodyCacheKey(sessionId, agentId)];
        return;
    }
    if (sessionId) {
        var prefix = String(sessionId) + ':';
        Object.keys(subagentBodyHtmlCache).forEach(function (k) {
            if (k.indexOf(prefix) === 0) delete subagentBodyHtmlCache[k];
        });
    }
}

function isSubagentBodyCacheComplete(html) {
    if (!html || isSubagentDetailPendingHtml(html)) return false;
    if (html.indexOf('subagent-detail-empty') >= 0) return false;
    if (html.indexOf('subagent-turn-process') < 0) {
        return html.indexOf('subagent-turn') >= 0 || html.indexOf('msg-wrap--assistant') >= 0;
    }
    return html.indexOf('msg-wrap--user') >= 0;
}

function rememberSubagentBodyCache(sessionId, agentId, html) {
    if (!sessionId || !agentId || !html || !isSubagentBodyCacheComplete(html)) return;
    subagentBodyHtmlCache[subagentBodyCacheKey(sessionId, agentId)] = html;
}

function readSubagentBodyCache(sessionId, agentId) {
    return subagentBodyHtmlCache[subagentBodyCacheKey(sessionId, agentId)] || '';
}

function shouldLoadSubagentCardBodies() {
    return !!subagentPanelOpen;
}

function onSubagentDockWheel(e) {
    var dock = document.getElementById('subagent-dock');
    if (!dock || dock.classList.contains('hidden') || !dock.contains(e.target)) return;
    var dy = e.deltaY;
    var eps = 2;
    var node = e.target;
    while (node && node !== dock) {
        if (node.nodeType === 1) {
            var style = window.getComputedStyle(node);
            var scrollable = node.classList && (
                node.classList.contains('subagent-grid') ||
                node.classList.contains('process-aggregate-body') ||
                node.classList.contains('process-aggregate-brief') ||
                node.classList.contains('feed-chunk-scroller')
            );
            if (scrollable || /(auto|scroll|overlay)/.test(style.overflowY)) {
                if (node.scrollHeight > node.clientHeight + eps) {
                    var st = node.scrollTop;
                    var max = node.scrollHeight - node.clientHeight;
                    if (dy < 0 && st > eps) {
                        e.stopPropagation();
                        return;
                    }
                    if (dy > 0 && st < max - eps) {
                        e.stopPropagation();
                        return;
                    }
                }
            }
        }
        node = node.parentElement;
    }
    var grid = dock.querySelector('.subagent-grid');
    if (grid && grid.scrollHeight > grid.clientHeight + eps) {
        var gst = grid.scrollTop;
        var gmax = grid.scrollHeight - grid.clientHeight;
        var next = Math.max(0, Math.min(gmax, gst + dy));
        if (next !== gst) grid.scrollTop = next;
    }
    e.preventDefault();
    e.stopPropagation();
}

function syncSubagentDockResizeUi() {
    var dock = document.getElementById('subagent-dock');
    var resizeBtn = document.getElementById('subagent-dock-resize');
    if (!dock || !resizeBtn) return;
    dock.classList.toggle('is-expanded', subagentDockExpanded);
    resizeBtn.setAttribute('aria-label', subagentDockExpanded ? '收起 Subagent 面板' : '展开 Subagent 面板');
}

function toggleSubagentDockExpand() {
    var grid = document.getElementById('subagent-grid');
    if (grid) {
        grid.classList.add('is-resizing');
        stashSubagentInactiveBodies(grid, grid.querySelector('.subagent-grid-card.is-expanded'));
    }
    subagentDockExpanded = !subagentDockExpanded;
    syncSubagentDockResizeUi();
    if (grid) {
        requestAnimationFrame(function () {
            grid.classList.remove('is-resizing');
            loadVisibleSubagentCardBodies(grid, currentSessionId);
        });
    }
}

function bindSubagentPanelOnce() {
    if (subagentPanelBound) return;
    subagentPanelBound = true;
    var dock = document.getElementById('subagent-dock');
    var panel = dock && dock.querySelector('.subagent-panel');
    if (dock) dock.addEventListener('wheel', onSubagentDockWheel, { passive: false, capture: true });
    if (panel) panel.addEventListener('wheel', onSubagentDockWheel, { passive: false, capture: true });
    var btn = document.getElementById('subagent-toggle-btn');
    if (btn) {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            if (subagentPanelOpen) closeSubagentPanel();
            else openSubagentPanel();
        });
    }
    var resizeBtn = document.getElementById('subagent-dock-resize');
    if (resizeBtn && !resizeBtn.dataset.subagentBound) {
        resizeBtn.dataset.subagentBound = '1';
        resizeBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            toggleSubagentDockExpand();
        });
    }
    document.addEventListener('mousedown', function (e) {
        if (!subagentPanelOpen) return;
        if (!(e.target && e.target.closest && e.target.closest('.subagent-card-menu'))) {
            document.querySelectorAll('.subagent-card-menu.is-open').forEach(function (menu) {
                menu.classList.remove('is-open');
                var mb = menu.querySelector('.subagent-card-menu-btn');
                if (mb) mb.setAttribute('aria-expanded', 'false');
            });
        }
        var dock = document.getElementById('subagent-dock');
        var btnEl = document.getElementById('subagent-toggle-btn');
        if (dock && dock.contains(e.target)) return;
        if (btnEl && btnEl.contains(e.target)) return;
        closeSubagentPanel();
    });
}

function openSubagentPanel() {
    var dock = document.getElementById('subagent-dock');
    var btn = document.getElementById('subagent-toggle-btn');
    if (!dock || (btn && btn.classList.contains('hidden'))) return;
    dock.classList.remove('hidden');
    subagentPanelOpen = true;
    syncSubagentDockResizeUi();
    if (btn) {
        btn.classList.add('is-active');
        btn.setAttribute('aria-expanded', 'true');
    }
    var grid = document.getElementById('subagent-grid');
    if (grid) {
        ensureSubagentCardViewportObserver(grid);
        stashSubagentInactiveBodies(grid, grid.querySelector('.subagent-grid-card.is-expanded'));
        requestAnimationFrame(function () {
            if (subagentPanelOpen) loadVisibleSubagentCardBodies(grid, currentSessionId);
        });
        if (countRunningSubagentCards() > 0) scheduleSubagentIncrementalSync();
    }
}

function resetSubagentPanelForSession() {
    if (currentSessionId) clearSubagentStateForSession(currentSessionId);
    cancelScheduledSubagentTreeRefresh();
    disconnectSubagentCardViewportObserver();
    if (subagentContinueBannerTimer) {
        clearTimeout(subagentContinueBannerTimer);
        subagentContinueBannerTimer = null;
    }
    hideSubagentContinueBanner();
    subagentPanelRefreshSeq += 1;
    closeSubagentPanel();
    stopSubagentIncrementalSync();
    var grid = document.getElementById('subagent-grid');
    if (grid) {
        grid.innerHTML = '';
        delete grid.dataset.sessionId;
        grid.classList.remove('subagent-grid--expanded');
    }
    var toggleBtn = document.getElementById('subagent-toggle-btn');
    var toggleBadge = document.getElementById('subagent-toggle-badge');
    if (toggleBtn) toggleBtn.classList.add('hidden');
    if (toggleBadge) toggleBadge.textContent = '';
}

function closeSubagentPanel() {
    var dock = document.getElementById('subagent-dock');
    var btn = document.getElementById('subagent-toggle-btn');
    if (dock) {
        var grid = document.getElementById('subagent-grid');
        if (grid) stashSubagentInactiveBodies(grid, null);
        dock.classList.add('hidden');
    }
    subagentPanelOpen = false;
    subagentDockExpanded = false;
    syncSubagentDockResizeUi();
    if (btn) {
        btn.classList.remove('is-active');
        btn.setAttribute('aria-expanded', 'false');
    }
}

function stopSubagentIncrementalSync() {
    if (subagentCardSyncTimer) {
        clearTimeout(subagentCardSyncTimer);
        subagentCardSyncTimer = null;
    }
}

function scheduleSubagentIncrementalSync() {
    if (subagentCardSyncTimer) return;
    /* 父会话 SSE 正在推流时，subagent 增量已实时到达，延后到 4s 再兜底；
       否则用 1200ms 让 finish 事件后的状态尽快同步。 */
    var delay = isSessionRunning(currentSessionId) ? 4000 : 1200;
    subagentCardSyncTimer = setTimeout(function () {
        subagentCardSyncTimer = null;
        runSubagentIncrementalSync();
    }, delay);
}

function countRunningSubagentCards() {
    var n = 0;
    document.querySelectorAll('.subagent-grid-card .subagent-status-dot.is-running').forEach(function () { n += 1; });
    return n;
}

async function runSubagentIncrementalSync() {
    var grid = document.getElementById('subagent-grid');
    if (!grid || !currentSessionId || !subagentPanelOpen) {
        stopSubagentIncrementalSync();
        return;
    }
    /* 页面不可见时不轮询，让出 CPU。回到前台后会被 visibilitychange 重新调度。 */
    if (document.visibilityState !== 'visible') {
        subagentCardSyncTimer = setTimeout(function () {
            subagentCardSyncTimer = null;
            runSubagentIncrementalSync();
        }, 5000);
        return;
    }
    var tasks = [];
    grid.querySelectorAll('.subagent-grid-card').forEach(function (card) {
        var dot = card.querySelector('.subagent-status-dot.is-running');
        if (!dot) return;
        var aid = card.getAttribute('data-agent-id');
        if (!aid) return;
        tasks.push({ aid: aid, card: card });
    });
    if (tasks.length) {
        /* 并发降至 1，避免一次性 N×2 个 HTTP 请求与 N 次 DOM 重渲染并发占用主线程。 */
        await runTasksWithConcurrency(tasks, 1, function (t) {
            return incrementalSyncSubagentCard(t.aid, t.card);
        });
    }
    var runningN = countRunningSubagentCards();
    if (runningN === 0 && currentSessionId && !replayingMessages) {
        updateSubagentContinueBanner(currentSessionId);
        void tryMarkSessionUnreadComplete(currentSessionId);
    }
    if (runningN > 0 && subagentPanelOpen) {
        subagentCardSyncTimer = setTimeout(function () {
            subagentCardSyncTimer = null;
            runSubagentIncrementalSync();
        }, getSubagentIncrementalSyncDelay(runningN));
    }
}

function getSubagentCardStreamCtx(body, card, agentId) {
    if (!body) return null;
    if (body._subagentStreamCtx && body._subagentStreamCtx._subagentBody === body) return body._subagentStreamCtx;
    var ctx = {
        _subagentBody: body,
        currentProcessGroup: card || null,
        stream: null,
        lastUserEventIndex: null,
        progressStream: {},
        progressScrollers: {},
        keyContextStreamFilter: { phase: 'seek', carry: '' },
        llm: newLlmState(),
        currentTurn: null,
        _subagentTurnProcess: null,
        _subagentTurnFinalSlot: null
    };
    body._subagentStreamCtx = ctx;
    return ctx;
}

function resetSubagentTurnStreamState(ctx) {
    if (!ctx) return;
    resetLlmState(ctx);
    finalizeProgressStreamChunks(ctx);
    ctx.currentTurn = null;
    ctx._subagentTurnProcess = null;
    ctx._subagentTurnFinalSlot = null;
}

function sealSubagentTurn(ctx) {
    if (!ctx || !ctx.currentTurn) return;
    resetSubagentTurnStreamState(ctx);
}

function markSubagentTurnHasProcess(turn) {
    if (!turn) return;
    var processEl = turn.querySelector('.subagent-turn-process');
    var userWrap = turn.querySelector('.msg-wrap--user');
    var hasDeferred = !!(turn._deferredProcessEvents && turn._deferredProcessEvents.length) || turn.dataset.processDeferred === '1';
    if ((processEl && processEl.children.length) || hasDeferred) {
        if (userWrap) userWrap.classList.add('has-turn-process');
    }
}

function shouldSkipSubagentProcessEvent(event) {
    if (!event || typeof event !== 'object') return true;
    var t = String(event.type || '');
    var c = String(event.content || '').trim();
    if (t === 'status' && (!c || c === 'New Agent Loop Start' || c === 'Loop finished' || c === 'Subagent Continuation Start')) return true;
    if ((t === 'warning' || t === 'error') && !c) return true;
    return false;
}

function syncSubagentTurnProcessFlags(root) {
    if (!root) return;
    root.querySelectorAll('.subagent-turn').forEach(function (turn) {
        markSubagentTurnHasProcess(turn);
    });
}

function bindSubagentCardBodyInteractions(body) {
    if (!body) return;
    bindSubagentCardBodyScrollFollow(body);
    if (body.dataset.subagentBodyBound) return;
    body.dataset.subagentBodyBound = '1';
    body.addEventListener('click', function (e) {
        var userWrap = e.target.closest('.msg-wrap--user');
        if (!userWrap || !body.contains(userWrap)) return;
        if (!userWrap.classList.contains('has-turn-process')) return;
        var turn = userWrap.closest('.subagent-turn');
        if (!turn) return;
        e.preventDefault();
        e.stopPropagation();
        toggleSubagentTurnProcess(turn, body, userWrap);
    });
}

function bindSubagentTurnUserToggle(turn, userWrap) {
    /* 统一由 bindSubagentCardBodyInteractions 委托处理，避免重复 toggle */
}

function dispatchSubagentCardEvent(ctx, card, event, eventIndex, agentId) {
    if (!event || typeof event !== 'object') return;
    if (shouldSkipSubagentProcessEvent(event)) return;
    applySessionEvent(event, {
        sessionId: agentId,
        eventIndex: eventIndex,
        source: 'subagent-stream',
    });
    var t = event.type;
    if (t === 'subagent_start' || t === 'subagent_finish') return;
    if (t === 'user') {
        openSubagentTurn(ctx, event.content || '', eventIndex);
        if (typeof eventIndex === 'number') ctx.lastUserEventIndex = eventIndex;
        return;
    }
    if (t === 'final') {
        appendSubagentFinalToTurn(ctx, event.content || '', eventIndex);
        if (ctx.currentTurn) {
            ctx._subagentTurnProcess = ctx.currentTurn.querySelector('.subagent-turn-process');
            ctx._subagentTurnFinalSlot = ctx.currentTurn.querySelector('.subagent-turn-final-slot');
        }
        resetLlmState(ctx);
        finalizeProgressStreamChunks(ctx);
        return;
    }
    ensureSubagentTurnForProcess(ctx, eventIndex);
    if (shouldDeferSubagentProcessDom(ctx)) {
        deferSubagentProcessEvent(ctx.currentTurn, event, eventIndex);
        markSubagentTurnHasProcess(ctx.currentTurn);
        return;
    }
    renderEvent(ctx, event, eventIndex, agentId);
    markSubagentTurnHasProcess(ctx.currentTurn);
}


function restoreSubagentTurnCtxFromBody(ctx, body) {
    if (!ctx || !body) return;
    var turns = body.querySelectorAll('.subagent-turn');
    if (!turns.length) {
        resetSubagentTurnStreamState(ctx);
        return;
    }
    var last = turns[turns.length - 1];
    var finalSlot = last.querySelector('.subagent-turn-final-slot');
    var hasFinal = finalSlot && finalSlot.querySelector('.msg-wrap--assistant');
    if (hasFinal) {
        resetSubagentTurnStreamState(ctx);
        return;
    }
    ctx.currentTurn = last;
    ctx._subagentTurnProcess = last.querySelector('.subagent-turn-process');
    ctx._subagentTurnFinalSlot = finalSlot;
}

function rebindSubagentCardBody(body, card, agentId) {
    if (!body) return;
    bindSubagentCardBodyInteractions(body);
    body.querySelectorAll('.subagent-turn').forEach(function (turn) {
        markSubagentTurnHasProcess(turn);
    });
    bindSubagentCardFeedInteractionsLightly(body);
    var ctx = body._subagentStreamCtx || (card ? getSubagentCardStreamCtx(body, card, agentId) : null);
    if (ctx) restoreSubagentTurnCtxFromBody(ctx, body);
    if (card) {
        refreshSubagentCardStats(card);
    }
}

function bindSubagentCardFeedInteractionsLightly(root) {
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll('.feed-chunk').forEach(function (ch, idx) {
        bindFeedChunkInteraction(ch);
        var sc = ch.querySelector('.feed-chunk-scroller');
        if (sc) bindFeedChunkScrollChain(sc);
        if (idx < 24) scheduleFeedChunkOverflowRefresh(ch);
    });
}

function finalizeSubagentCardStream(agentId, card) {
    if (!card) return;
    var body = card.querySelector('.subagent-card-body');
    if (!body) return;
    var ctx = getSubagentCardStreamCtx(body, card, agentId);
    finalizeLlmStreamChunks(ctx);
    finalizeProgressStreamChunks(ctx);
}

function ensureSubagentCardStreamReady(card, aid) {
    if (!card || !aid) return;
    var body = card.querySelector('.subagent-card-body');
    if (!body || body.dataset.loading === '1') return;
    if (!card.dataset.procStartedAt) card.dataset.procStartedAt = String(procNow());
    if (body.querySelector('.subagent-detail-empty')) body.innerHTML = '';
    body.dataset.streamReady = '1';
    if (!body.dataset.loaded) body.dataset.loaded = '1';
    delete body.dataset.loading;
    bindSubagentCardBodyInteractions(body);
    getSubagentCardStreamCtx(body, card, aid);
}

function upsertSubagentCardFromStartEvent(event) {
    /* 历史回放阶段：一律不亮按钮 / 不写 grid，避免把别会话遗留的 subagent_start 闪出来；
       真实状态由稍后的 refreshSubagentTreePanel(/sessions/{sid}/subagents) 单一来源决定。 */
    if (replayingMessages) return null;
    var grid = document.getElementById('subagent-grid');
    if (!grid) return null;
    if (currentSessionId && grid.dataset.sessionId && grid.dataset.sessionId !== currentSessionId) {
        return null;
    }
    if (currentSessionId) grid.dataset.sessionId = currentSessionId;
    var aid = String(event.agent_id || event.run_id || '');
    if (!aid) return null;
    var node = {
        id: aid,
        running: !event.background ? true : true,
        description: event.description || aid.slice(0, 8),
        subagent_type: event.subagent_type || 'subagent',
        background: !!event.background,
    };
    var card = grid.querySelector('.subagent-grid-card[data-agent-id="' + aid + '"]');
    if (!card) card = appendSubagentGridCardFromNode(grid, node);
    else applySubagentNodeMetaToCard(card, node);
    if (currentSessionId) bindSubagentGridActions(grid, currentSessionId);
    var toggleBtn = document.getElementById('subagent-toggle-btn');
    var toggleBadge = document.getElementById('subagent-toggle-badge');
    if (toggleBtn) {
        toggleBtn.classList.remove('hidden');
        toggleBtn.classList.add('is-running');
    }
    var cardCount = grid.querySelectorAll('.subagent-grid-card').length;
    var runCount = grid.querySelectorAll('.subagent-status-dot.is-running').length;
    if (toggleBadge) toggleBadge.textContent = String(cardCount) + (runCount ? (' · ' + runCount) : '');
    if (toggleBtn && cardCount > 0) toggleBtn.classList.remove('hidden');
    if (shouldStreamSubagentSummaryDom(card)) ensureSubagentCardStreamReady(card, aid);
    return card;
}

function applySubagentFinishToCard(card, event) {
    if (!card || !event) return;
    card.dataset.subagentRunning = '0';
    var aidFin = card.getAttribute('data-agent-id') || '';
    var preview = String(event.result_preview || card.dataset.resultPreview || '').trim();
    if (preview) card.dataset.resultPreview = preview;
    if (Object.prototype.hasOwnProperty.call(event, 'has_final')) card.dataset.hasFinal = event.has_final ? '1' : '0';
    var hasFinal = card.dataset.hasFinal === '1'
        || !!card.querySelector('.subagent-turn-final-slot .msg-wrap--assistant, .message.assistant');
    var ok = event.ok !== false && (hasFinal || !!preview);
    markSubagentCardCompleted(card, ok, ok ? '' : String(event.error || 'missing final').trim());
    var body = card.querySelector('.subagent-card-body');
    if (currentSessionId && aidFin) forgetSubagentBodyCache(currentSessionId, aidFin);
    if (body && aidFin) {
        delete body.dataset.loaded;
        delete body.dataset.streamReady;
        delete body.dataset.loading;
        delete body.dataset.stashed;
        if (subagentPanelOpen && card.classList.contains('is-expanded')) {
            if (shouldStreamSubagentProcessDom(card)) {
                loadSubagentDetailInto(body, aidFin, card, currentSessionId);
            } else {
                queueSubagentCardBodyLoad(card, currentSessionId);
            }
        } else if (subagentPanelOpen) {
            updateSubagentCardSummaryOnly(card, preview);
        } else {
            body.innerHTML = '';
        }
    }
    if (aidFin) void refreshSubagentContextForCard(card, aidFin, true);
    scheduleSubagentCardStats(card);
}

function markSubagentCardCompleted(card, ok, errTxt) {
    if (!card) return;
    card.dataset.subagentRunning = '0';
    var dot = card.querySelector('.subagent-status-dot');
    if (dot) {
        dot.classList.remove('is-running', 'is-done', 'is-error');
        dot.classList.add(ok ? 'is-done' : 'is-error');
        var tip = ok ? '完成' : (/interrupt/i.test(String(errTxt || '')) ? '已中断' : '失败');
        dot.setAttribute('data-ui-tip', tip);
    }
    card.dataset.procEndedAt = String(procNow());
    var stopBtn = card.querySelector('.subagent-card-stop');
    if (stopBtn) stopBtn.remove();
    var toggleBtn = document.getElementById('subagent-toggle-btn');
    if (toggleBtn) toggleBtn.classList.remove('is-running');
}

function setSubagentCardExpanded(card, expand) {
    var grid = document.getElementById('subagent-grid');
    if (!grid || !card) return;
    if (expand) {
        grid.classList.add('is-resizing');
        stashSubagentInactiveBodies(grid, card);
        grid.querySelectorAll('.subagent-grid-card.is-expanded').forEach(function (c) {
            if (c !== card) {
                c.classList.remove('is-expanded');
                stashSubagentCardBodyForCollapse(c);
            }
        });
        card.classList.add('is-expanded');
        grid.classList.add('subagent-grid--expanded');
        var expandedBody = card.querySelector('.subagent-card-body');
        if (expandedBody && expandedBody.dataset.finalOnly === '1') {
            delete expandedBody.dataset.loaded;
            delete expandedBody.dataset.finalOnly;
            expandedBody.classList.remove('is-final-only');
            expandedBody.innerHTML = '';
        }
    } else {
        stashSubagentCardBodyForCollapse(card);
        card.classList.remove('is-expanded');
        if (!grid.querySelector('.subagent-grid-card.is-expanded')) {
            grid.classList.remove('subagent-grid--expanded');
        }
    }
    syncSubagentExpandButtons(grid);
    if (expand) {
        card.dataset.viewportVisible = '1';
        card.classList.add('is-viewport-visible');
        setTimeout(function () {
            grid.classList.remove('is-resizing');
            if (!card.classList.contains('is-expanded')) return;
            scheduleSubagentDetailWork(function () {
                if (!card.classList.contains('is-expanded')) return;
                if (!restoreSubagentCardBodyFromStash(card, currentSessionId)) {
                    queueSubagentCardBodyLoad(card, currentSessionId);
                }
            });
        }, 80);
    } else {
        requestAnimationFrame(function () {
            grid.classList.remove('is-resizing');
            if (card.isConnected && cardIntersectsGridViewport(card, grid)) {
                card.dataset.viewportVisible = '1';
                card.classList.add('is-viewport-visible');
                queueSubagentCardBodyLoad(card, currentSessionId);
            }
        });
    }
}

function syncSubagentExpandButtons(grid) {
    if (!grid) return;
    grid.querySelectorAll('.subagent-card-expand').forEach(function (btn) {
        var card = btn.closest('.subagent-grid-card');
        var on = !!(card && card.classList.contains('is-expanded'));
        btn.classList.toggle('is-active', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        btn.setAttribute('aria-label', on ? '退出全屏' : '放大显示');
        btn.setAttribute('data-ui-tip', on ? '退出全屏' : '在浮窗内全屏显示');
    });
}

function toggleSubagentCardExpanded(card) {
    if (!card) return;
    setSubagentCardExpanded(card, !card.classList.contains('is-expanded'));
}

function appendSubagentStreamEvent(agentId, event, eventIndex) {
    if (!agentId || !event || typeof event !== 'object') return false;
    var t = event.type;
    if (t === 'subagent_start') {
        if (currentSessionId) applySubagentLifecycleToStore(currentSessionId, event);
        upsertSubagentCardFromStartEvent(event);
        if (!replayingMessages) {
            hideSubagentContinueBanner();
            scheduleSubagentIncrementalSync();
        }
        return true;
    }
    if (t === 'subagent_finish') {
        if (currentSessionId) applySubagentLifecycleToStore(currentSessionId, event);
        var cardFin = document.querySelector('.subagent-grid-card[data-agent-id="' + agentId + '"]');
        if (cardFin) {
            if (event.result_preview) cardFin.dataset.resultPreview = String(event.result_preview);
            applySubagentFinishToCard(cardFin, event);
            finalizeSubagentCardStream(agentId, cardFin);
        }
        if (currentSessionId && !replayingMessages) {
            scheduleRefreshSubagentTreePanel(currentSessionId);
            updateSubagentContinueBanner(currentSessionId);
        }
        return true;
    }
    var grid = document.getElementById('subagent-grid');
    var card = grid && grid.querySelector('.subagent-grid-card[data-agent-id="' + agentId + '"]');
    if (!card) {
        if (event._subagent_forward) upsertSubagentCardFromStartEvent({ agent_id: agentId, description: agentId.slice(0, 8), running: true });
        card = grid && grid.querySelector('.subagent-grid-card[data-agent-id="' + agentId + '"]');
    }
    if (!card) return false;
    var body = card.querySelector('.subagent-card-body');
    if (!body) return false;
    if (t === 'user' || t === 'final') {
        if (!shouldStreamSubagentSummaryDom(card)) {
            trackSubagentStreamEventLightweight(card, agentId, event, eventIndex);
            return true;
        }
        if (body.dataset.loading === '1' && t !== 'user' && t !== 'final') return true;
        ensureSubagentCardStreamReady(card, agentId);
        if (body.dataset.loaded !== '1' && body.querySelector('.subagent-detail-empty')) {
            body.innerHTML = '';
        }
        if (body.dataset.loaded !== '1') body.dataset.loaded = '1';
        delete body.dataset.loading;
        var ctxSummary = getSubagentCardStreamCtx(body, card, agentId);
        dispatchSubagentCardEvent(ctxSummary, card, event, eventIndex, agentId);
        if (t === 'final') {
            card.dataset.hasFinal = '1';
            finalizeLlmStreamChunks(ctxSummary);
            markSubagentCardCompleted(card, true);
            refreshFeedChunksInCtx(ctxSummary);
            syncSubagentTurnProcessFlags(body);
            if (shouldStreamSubagentProcessDom(card)) {
                scrollSubagentCardBodyToBottom(ctxSummary);
                body.querySelectorAll('.feed-chunk').forEach(scheduleFeedChunkOverflowRefresh);
            }
            if (currentSessionId && agentId && body) {
                rememberSubagentBodyCache(currentSessionId, agentId, body.innerHTML);
                body.dataset.cacheClean = '1';
            }
        }
        bumpSubagentCardEventCount(agentId, eventIndex, !event.ephemeral);
        scheduleSubagentCardStats(card);
        return true;
    }
    if (!shouldStreamSubagentProcessDom(card)) {
        trackSubagentStreamEventLightweight(card, agentId, event, eventIndex);
        return true;
    }
    if (body.dataset.loading === '1' && !event.ephemeral && t !== 'user' && t !== 'final') return true;
    ensureSubagentCardStreamReady(card, agentId);
    if (body.dataset.loaded !== '1' && body.querySelector('.subagent-detail-empty')) {
        body.innerHTML = '';
    }
    if (body.dataset.loaded !== '1') body.dataset.loaded = '1';
    delete body.dataset.loading;
    var ctx = getSubagentCardStreamCtx(body, card, agentId);
    if (t === 'subagent_start' || t === 'subagent_finish') return true;
    if (event.ephemeral) {
        ensureSubagentTurnForProcess(ctx, eventIndex);
        if (shouldDeferSubagentProcessDom(ctx)) {
            deferSubagentProcessEvent(ctx.currentTurn, event, eventIndex);
            if (event.type === 'context_tokens') {
                card.dataset.procCtxEstimated = String(event.estimated);
                card.dataset.procCtxThreshold = String(event.threshold);
            } else if (event.type === 'process_metrics') {
                applySubagentProcessMetricsToCard(card, event);
            } else if (event.type === 'cache_stats') {
                if (event.model != null) card.dataset.procCacheModel = String(event.model);
            }
            if (event.react_iter != null) bumpAggregateMaxReactIter(card, event.react_iter);
            markSubagentTurnHasProcess(ctx.currentTurn);
            bumpSubagentCardEventCount(agentId, eventIndex, false);
            scheduleSubagentCardStats(card);
            return true;
        }
        if (event.type === 'llm_reasoning_delta' || event.type === 'llm_response_delta') {
            appendLlmStreamDelta(ctx, event, agentId);
        } else if (event.type === 'context_summary_delta') {
            appendProgressStreamDelta(ctx, event.delta, 'context-summary', agentId);
        } else if (event.type === 'key_context_delta') {
            appendKeyContextStreamDelta(ctx, event.delta, agentId);
        } else if (event.type === 'context_tokens') {
            card.dataset.procCtxEstimated = String(event.estimated);
            card.dataset.procCtxThreshold = String(event.threshold);
            scheduleSubagentCardStats(card);
        } else if (event.type === 'process_metrics') {
            applyProcessMetricsFromEvent(ctx, event);
        } else if (event.type === 'cache_stats') {
            applyCacheStatsFromEvent(ctx, event);
            scheduleSubagentCardStats(card);
        }
        markSubagentTurnHasProcess(ctx.currentTurn);
        bumpSubagentCardEventCount(agentId, eventIndex, false);
        scheduleSubagentCardStats(card);
        followStreamProcessScroll(ctx, agentId);
        return true;
    } else {
        dispatchSubagentCardEvent(ctx, card, event, eventIndex, agentId);
    }
    bumpSubagentCardEventCount(agentId, eventIndex, true);
    scheduleSubagentCardStats(card);
    followStreamProcessScroll(ctx, agentId);
    return true;
}

function handleSubagentStreamEvent(event, eventIndex, runSessionId) {
    if (!event || typeof event !== 'object') return false;
    var aid = String(event.agent_id || '');
    if (!aid) return false;
    /* fail-closed：父会话切走后，子 agent 事件不得 fall-through 到主对话区。
       数据已写入子 agent 自己的 ui_events，切回后由 refreshSubagentTreePanel 渲染。 */
    if (runSessionId && currentSessionId && runSessionId !== currentSessionId) {
        if (!replayingMessages && event.type === 'subagent_finish') {
            void tryMarkSessionUnreadComplete(runSessionId);
        }
        return true;
    }
    return appendSubagentStreamEvent(aid, event, eventIndex);
}

async function incrementalSyncSubagentCard(agentId, card) {
    if (!agentId || !card) return;
    var body = card.querySelector('.subagent-card-body');
    if (!body || body.dataset.loading === '1') return;
    if (!shouldLoadSubagentCardBodies() && body.dataset.loaded !== '1') return;
    /* 父会话仍在 SSE 推流：转发的 subagent 事件已实时画到卡片，再用 /messages 全量回填
       会把正在 streaming 的 llm-* 块切碎（finalize → 新开行）。轮询此时降级为仅状态校准。 */
    var parentRunning = isSessionRunning(currentSessionId);
    var prevCount = currentSessionId ? subagentStore.getEventCount(currentSessionId, agentId) : 0;
    var summaryOnly = !shouldStreamSubagentProcessDom(card);
    try {
        var countResp = await fetch('/sessions/' + encodeURIComponent(agentId) + '/messages/count');
        if (!countResp.ok) return;
        var countData = await countResp.json();
        var total = countData && countData.count != null ? Number(countData.count) : 0;
        if (!Number.isFinite(total) || total <= prevCount) return;
        /* 父 SSE 在跑：本次只更新计数（让按钮 badge 与状态点保持），不重渲染 body。
           待父 SSE 结束（isSessionRunning 转 false），下一轮会以 fresh prevCount 继续。 */
        if (parentRunning && body.dataset.loaded === '1') {
            setSubagentCardEventCount(agentId, total);
            return;
        }
        var msgResp = await fetch('/sessions/' + encodeURIComponent(agentId) + '/messages');
        if (!msgResp.ok) return;
        var events = normalizeSubagentMessagesPayload(await msgResp.json());
        if (!body.isConnected) return;
        if (events.length <= prevCount) {
            setSubagentCardEventCount(agentId, events.length);
            return;
        }
        var gotFinal = false;
        for (var fi = prevCount; fi < events.length; fi += 1) {
            if (events[fi] && events[fi].type === 'final') { gotFinal = true; break; }
        }
        if (body.dataset.loaded !== '1') {
            if (!shouldLoadSubagentCardBodies()) return;
            if (summaryOnly) {
                ensureSubagentCardStreamReady(card, agentId);
                var ctxNew = getSubagentCardStreamCtx(body, card, agentId);
                for (var si = prevCount; si < events.length; si += 1) {
                    var sev = events[si];
                    if (!sev || typeof sev !== 'object') continue;
                    if (sev.type !== 'user' && sev.type !== 'final') continue;
                    dispatchSubagentCardEvent(ctxNew, card, sev, si, agentId);
                }
                rebindSubagentCardBody(body, card, agentId);
            } else {
                renderSubagentProcessEvents(body, card, events, agentId);
            }
            setSubagentCardEventCount(agentId, events.length);
            if (gotFinal) markSubagentCardCompleted(card, true);
            return;
        }
        var ctx = getSubagentCardStreamCtx(body, card, agentId);
        for (var i = prevCount; i < events.length; i += 1) {
            if (events[i] && typeof events[i] === 'object') {
                if (summaryOnly && events[i].type !== 'user' && events[i].type !== 'final' && !events[i].ephemeral) continue;
                dispatchSubagentCardEvent(ctx, card, events[i], i, agentId);
            }
        }
        /* 不在轮询路径里 finalize 流块：finalize 由 SSE 的 [DONE] 或 subagent_finish 触发。 */
        rebindSubagentCardBody(body, card, agentId);
        setSubagentCardEventCount(agentId, events.length);
        if (gotFinal) markSubagentCardCompleted(card, true);
    } catch (e) { /* ignore */ }
}

function handleSubagentLifecycleEvent(event) {
    if (!event || !currentSessionId) return;
    applySubagentLifecycleToStore(currentSessionId, event);
    /* 历史回放：不亮按钮 / 不写 grid / 不触发 schedule，全部交给 refreshSubagentTreePanel。 */
    if (replayingMessages) return;
    if (event.type === 'subagent_start') {
        upsertSubagentCardFromStartEvent(event);
        hideSubagentContinueBanner();
        scheduleSubagentIncrementalSync();
    } else if (event.type === 'subagent_finish') {
        var aid = String(event.agent_id || event.run_id || '');
        var card = aid && document.querySelector('.subagent-grid-card[data-agent-id="' + aid + '"]');
        if (card) {
            if (event.result_preview) card.dataset.resultPreview = String(event.result_preview);
            applySubagentFinishToCard(card, event);
            finalizeSubagentCardStream(aid, card);
        }
        scheduleRefreshSubagentTreePanel(currentSessionId);
        updateSubagentContinueBanner(currentSessionId);
    }
}

function collectSubagentGridState(grid) {
    var detailCache = {};
    if (!grid) return { detailCache: detailCache };
    if (grid.dataset.sessionId && currentSessionId && grid.dataset.sessionId !== currentSessionId) {
        return { detailCache: detailCache };
    }
    var sid = currentSessionId;
    grid.querySelectorAll('.subagent-grid-card').forEach(function (card) {
        var id = card.getAttribute('data-agent-id');
        if (!id) return;
        var body = card.querySelector('.subagent-card-body');
        if (body && body.dataset.loaded === '1' && body.dataset.loading !== '1' && body.dataset.finalOnly !== '1') {
            var html = body.innerHTML;
            if (isSubagentBodyCacheComplete(html)) {
                detailCache[id] = html;
                if (sid) rememberSubagentBodyCache(sid, id, html);
            }
        }
    });
    return { detailCache: detailCache };
}

function restoreSubagentGridState(grid, detailCache, sessionId) {
    if (!grid) return;
    grid.querySelectorAll('.subagent-grid-card').forEach(function (card) {
        var id = card.getAttribute('data-agent-id');
        if (!id) return;
        var body = card.querySelector('.subagent-card-body');
        if (!body) return;
        if (!shouldLoadSubagentCardBodies()) {
            delete body.dataset.loaded;
            delete body.dataset.loading;
            body.innerHTML = '';
            return;
        }
        var shouldMount = card.classList.contains('is-expanded') || card.dataset.viewportVisible === '1';
        if (!shouldMount) {
            delete body.dataset.loaded;
            delete body.dataset.loading;
            delete body.dataset.streamReady;
            delete body.dataset.stashed;
            body.innerHTML = '';
            return;
        }
        var cached = (detailCache && detailCache[id]) || readSubagentBodyCache(sessionId, id);
        if (card.classList.contains('is-expanded') && cached && isSubagentBodyCacheComplete(cached)) {
            body.innerHTML = cached;
            body.dataset.loaded = '1';
            body.dataset.cacheClean = '1';
            delete body.dataset.finalOnly;
            body.classList.remove('is-final-only');
            delete body.dataset.loading;
            rebindSubagentCardBody(body, card, id);
            body._subagentStreamCtx = getSubagentCardStreamCtx(body, card, id);
            requestAnimationFrame(function () { refreshAllFeedChunksUnder(body); });
        } else {
            delete body.dataset.loaded;
            delete body.dataset.loading;
            queueSubagentCardBodyLoad(card, sessionId);
        }
    });
}

function findSubagentSliceStartByTurns(events, beforeIndex, turnCount) {
    var arr = events || [];
    var limit = Math.max(0, Math.min(arr.length - 1, Number(beforeIndex) || 0));
    var found = 0;
    for (var i = limit - 1; i >= 0; i -= 1) {
        if (arr[i] && arr[i].type === 'user') {
            found += 1;
            if (found >= turnCount) return i;
        }
    }
    return 0;
}

function bindSubagentFinalOnlyHistoryLoader(bodyEl, hostEl, agentId, hasOlder, rangeStart) {
    if (!bodyEl || bodyEl.dataset.finalOnlyLoaderBound === '1') return;
    bodyEl.dataset.finalOnlyLoaderBound = '1';
    
    // 存储分页状态
    bodyEl._hasOlderEvents = hasOlder !== false; // 默认假设有更早的事件
    bodyEl._rangeStart = typeof rangeStart === 'number' ? rangeStart : 0;
    bodyEl._historyLoadedEvents = []; // 存储已加载的历史事件
    
    function loadMoreHistory() {
        if (!bodyEl.isConnected || bodyEl.dataset.historyLoading === '1' || bodyEl.dataset.historyComplete === '1') return;
        
        // 如果没有更早的事件，标记完成
        if (!bodyEl._hasOlderEvents) {
            bodyEl.dataset.historyComplete = '1';
            delete bodyEl.dataset.finalOnly;
            bodyEl.classList.remove('is-final-only');
            return;
        }
        
        var oldScrollHeight = bodyEl.scrollHeight || 0;
        var oldScrollTop = bodyEl.scrollTop || 0;
        bodyEl.dataset.historyLoading = '1';
        
        // 使用分页API加载更多历史事件
        var beforeIndex = bodyEl._rangeStart;
        var turnsParam = '&turns=' + SUBAGENT_HISTORY_TURNS_PER_PAGE;
        var url = '/sessions/' + encodeURIComponent(agentId) + '/messages?before_index=' + beforeIndex + turnsParam;
        
        fetch(url)
            .then(function(resp) {
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                return resp.json();
            })
            .then(function(data) {
                if (!bodyEl.isConnected) return;
                
                var events, hasOlderNew, rangeStartNew;
                if (data && Array.isArray(data)) {
                    events = data;
                    hasOlderNew = false;
                    rangeStartNew = 0;
                } else if (data && Array.isArray(data.events)) {
                    events = data.events;
                    hasOlderNew = !!data.has_older;
                    rangeStartNew = typeof data.range_start === 'number' ? data.range_start : 0;
                } else {
                    events = [];
                    hasOlderNew = false;
                    rangeStartNew = 0;
                }
                
                // 更新分页状态
                bodyEl._hasOlderEvents = hasOlderNew;
                bodyEl._rangeStart = rangeStartNew;
                
                // 合并事件到已加载历史
                bodyEl._historyLoadedEvents = events.concat(bodyEl._historyLoadedEvents);
                
                // 渲染所有已加载的事件
                var allEvents = bodyEl._historyLoadedEvents;
                void renderSubagentProcessEvents(bodyEl, hostEl, allEvents, agentId, 0).then(function () {
                    if (!bodyEl._hasOlderEvents || events.length === 0) {
                        bodyEl.dataset.historyComplete = '1';
                        delete bodyEl.dataset.finalOnly;
                        bodyEl.classList.remove('is-final-only');
                    }
                    requestAnimationFrame(function () {
                        if (!bodyEl.isConnected) return;
                        var keepTop = Math.max(0, (bodyEl.scrollHeight || 0) - oldScrollHeight + oldScrollTop);
                        bodyEl.scrollTop = keepTop;
                    });
                });
            })
            .catch(function(err) {
                console.error('加载subagent历史失败:', err);
            })
            .finally(function() {
                delete bodyEl.dataset.historyLoading;
            });
    }
    
    bodyEl.addEventListener('wheel', function (ev) {
        if (ev.deltaY < 0) loadMoreHistory();
    }, { passive: true });
    bodyEl.addEventListener('scroll', function () {
        if (bodyEl.scrollTop <= 8) loadMoreHistory();
    }, { passive: true });
}

async function toggleSubagentOutputPanel(card, sessionId) {
    if (!card || !sessionId) return;
    var agentId = card.getAttribute('data-agent-id') || '';
    if (!agentId) return;
    var panel = card.querySelector('.subagent-output-panel');
    if (!panel) {
        panel = document.createElement('div');
        panel.className = 'subagent-output-panel';
        var body = card.querySelector('.subagent-card-body');
        if (body) card.insertBefore(panel, body);
        else card.appendChild(panel);
    }
    var wasOpen = panel.classList.contains('is-open');
    panel.classList.toggle('is-open', !wasOpen);
    var btn = card.querySelector('.subagent-card-output');
    if (btn) btn.classList.toggle('is-active', !wasOpen);
    if (wasOpen || panel.dataset.loaded === '1' || panel.dataset.loading === '1') return;
    panel.dataset.loading = '1';
    panel.innerHTML = '<div class="subagent-output-empty">加载中...</div>';
    try {
        var resp = await fetch('/sessions/' + encodeURIComponent(sessionId) + '/subagents/' + encodeURIComponent(agentId) + '/output');
        var data = await resp.json();
        if (!resp.ok || !data || !data.ok) throw new Error((data && data.error) || ('HTTP ' + resp.status));
        var content = String(data.content || '').trim();
        panel.innerHTML = content
            ? '<div class="subagent-output-content markdown-body">' + renderMarkdown(content) + '</div>'
            : '<div class="subagent-output-empty">(无输出)</div>';
        enhanceAssistantMessageContent(panel);
        panel.dataset.loaded = '1';
    } catch (e) {
        panel.innerHTML = '<div class="subagent-output-empty">加载失败: ' + escapeHtml(String(e)) + '</div>';
    } finally {
        delete panel.dataset.loading;
    }
}

function bindSubagentGridActions(grid, sessionId) {
    if (!grid) return;
    grid.querySelectorAll('.subagent-grid-card').forEach(function (card) {
        bindProcessAggregate(card);
    });
    grid.querySelectorAll('.subagent-card-stop').forEach(function (btn) {
        if (btn.dataset.subagentStopBound) return;
        btn.dataset.subagentStopBound = '1';
        btn.addEventListener('click', async function (e) {
            e.stopPropagation();
            var aid = btn.getAttribute('data-agent-id');
            if (!aid || !sessionId) return;
            try {
                await fetch('/sessions/' + encodeURIComponent(sessionId) + '/subagents/' + encodeURIComponent(aid) + '/interrupt', { method: 'POST' });
            } catch (err) { /* ignore */ }
            var menu = btn.closest('.subagent-card-menu');
            if (menu) menu.classList.remove('is-open');
            scheduleRefreshSubagentTreePanel(sessionId);
        });
    });
    grid.querySelectorAll('.subagent-card-delete').forEach(function (btn) {
        if (btn.dataset.subagentDeleteBound) return;
        btn.dataset.subagentDeleteBound = '1';
        btn.addEventListener('click', async function (e) {
            e.stopPropagation();
            var aid = btn.getAttribute('data-agent-id');
            if (!aid || !sessionId) return;
            var ok = await openUiModal({
                title: '删除 Subagent',
                subtitle: aid.slice(0, 8) + '…',
                message: '将删除该 subagent 的会话记录、过程卡片及其嵌套子任务。该操作不可撤销。',
                danger: true,
                confirmText: '删除',
                cancelText: '取消',
            });
            if (!ok) return;
            var menu = btn.closest('.subagent-card-menu');
            if (menu) menu.classList.remove('is-open');
            btn.disabled = true;
            try {
                var resp = await fetch('/sessions/' + encodeURIComponent(sessionId) + '/subagents/' + encodeURIComponent(aid), { method: 'DELETE' });
                if (!resp.ok) {
                    showUiAlert({ title: '删除失败', message: '无法删除该 Subagent，请稍后重试。', variant: 'error' });
                    btn.disabled = false;
                    return;
                }
                forgetSubagentBodyCache(sessionId, aid);
                subagentStore.remove(sessionId, aid);
                delete subagentCardLoadQueued[aid];
                var card = btn.closest('.subagent-grid-card');
                if (card) card.remove();
                scheduleRefreshSubagentTreePanel(sessionId, 0);
            } catch (err) {
                btn.disabled = false;
                showUiAlert({ title: '删除失败', message: String((err && err.message) || err || 'unknown error'), variant: 'error' });
            }
        });
    });
    grid.querySelectorAll('.subagent-card-menu-btn').forEach(function (btn) {
        if (btn.dataset.subagentMenuBound) return;
        btn.dataset.subagentMenuBound = '1';
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            var menu = btn.closest('.subagent-card-menu');
            if (!menu) return;
            var open = !menu.classList.contains('is-open');
            grid.querySelectorAll('.subagent-card-menu.is-open').forEach(function (m) {
                if (m !== menu) {
                    m.classList.remove('is-open');
                    var b = m.querySelector('.subagent-card-menu-btn');
                    if (b) b.setAttribute('aria-expanded', 'false');
                }
            });
            menu.classList.toggle('is-open', open);
            btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
    });
    grid.querySelectorAll('.subagent-card-expand').forEach(function (btn) {
        if (btn.dataset.subagentExpandBound) return;
        btn.dataset.subagentExpandBound = '1';
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            var card = btn.closest('.subagent-grid-card');
            if (card) toggleSubagentCardExpanded(card);
        });
    });
    grid.querySelectorAll('.subagent-card-body').forEach(function (body) {
        if (body.dataset.subagentBodyExpandBound) return;
        body.dataset.subagentBodyExpandBound = '1';
        body.addEventListener('click', function (e) {
            var card = body.closest('.subagent-grid-card');
            if (!card || card.classList.contains('is-expanded')) return;
            var target = e.target;
            if (target && target.closest && target.closest('button,a,input,textarea,select,.feed-chunk-scroller,.copy-btn,.subagent-card-menu,.msg-wrap--user')) return;
            var sel = window.getSelection && window.getSelection();
            if (sel && String(sel).trim()) return;
            setSubagentCardExpanded(card, true);
        });
    });
    grid.querySelectorAll('.subagent-card-output').forEach(function (btn) {
        if (btn.dataset.subagentOutputBound) return;
        btn.dataset.subagentOutputBound = '1';
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            var card = btn.closest('.subagent-grid-card');
            if (card) toggleSubagentOutputPanel(card, sessionId);
            var menu = btn.closest('.subagent-card-menu');
            if (menu) menu.classList.remove('is-open');
        });
    });
    syncSubagentExpandButtons(grid);
    initUiHoverTips(grid);
}

async function refreshSubagentContextForCard(card, agentId, force) {
    if (!card || !agentId) return;
    if (!force && !subagentPanelOpen) return;
    if (!force && card.dataset.procCtxEstimated != null && card.dataset.procCtxEstimated !== '') return;
    if (subagentContextFetchInFlight[agentId]) return subagentContextFetchInFlight[agentId];
    subagentContextFetchInFlight[agentId] = (async function () {
        try {
            var r = await fetch('/sessions/' + encodeURIComponent(agentId) + '/context_tokens');
            var j = await r.json();
            if (r.ok && j && j.ok && j.estimated != null && j.estimated >= 0) {
                card.dataset.procCtxEstimated = String(j.estimated);
                card.dataset.procCtxThreshold = String(j.threshold);
                refreshSubagentCardStats(card);
            }
        } catch (e) { /* ignore */ }
        finally {
            delete subagentContextFetchInFlight[agentId];
        }
    })();
    return subagentContextFetchInFlight[agentId];
}

function loadVisibleSubagentCardBodies(grid, sessionIdOpt) {
    if (!grid || !shouldLoadSubagentCardBodies()) return;
    ensureSubagentCardViewportObserver(grid);
    var sessionId = sessionIdOpt || currentSessionId;
    grid.querySelectorAll('.subagent-grid-card').forEach(function (card) {
        observeSubagentCardViewport(card);
        if (card.classList.contains('is-expanded')) {
            card.dataset.viewportVisible = '1';
            card.classList.add('is-viewport-visible');
            queueSubagentCardBodyLoad(card, sessionId);
        } else if (cardIntersectsGridViewport(card, grid)) {
            card.dataset.viewportVisible = '1';
            card.classList.add('is-viewport-visible');
            queueSubagentCardBodyLoad(card, sessionId);
        }
    });
}

function normalizeSubagentMessagesPayload(data) {
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.events)) return data.events;
    return [];
}

async function loadSubagentDetailInto(el, agentId, hostEl, sessionIdOpt) {
    if (!el || !agentId) return;
    if (el.dataset.loading === '1') return;
    var card = hostEl || (el.closest ? el.closest('.subagent-grid-card, .subagent-block') : null);
    el.dataset.loading = '1';
    delete el.dataset.loaded;
    el.innerHTML = '<div class="subagent-detail-empty">加载详情中…</div>';
    try {
        // 判断是否为折叠模式
        var isCollapsed = card && card.classList && !card.classList.contains('is-expanded') && card.classList.contains('subagent-grid-card');
        
        // 使用分页API：折叠模式只获取最近3轮，展开模式获取更多
        var turnsParam = isCollapsed ? '&turns=3' : '&turns=10';
        var resp = await fetch('/sessions/' + encodeURIComponent(agentId) + '/messages?' + turnsParam);
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        var data = await resp.json();
        
        // 兼容分页格式和数组格式
        var events, hasOlder, rangeStart;
        if (data && Array.isArray(data)) {
            // 旧格式：直接是数组
            events = data;
            hasOlder = false;
            rangeStart = 0;
        } else if (data && Array.isArray(data.events)) {
            // 新格式：分页对象
            events = data.events;
            hasOlder = !!data.has_older;
            rangeStart = typeof data.range_start === 'number' ? data.range_start : 0;
        } else {
            events = [];
            hasOlder = false;
            rangeStart = 0;
        }
        
        if (!el.isConnected) return;
        await new Promise(function (resolve) { setTimeout(resolve, 0); });
        
        if (isCollapsed) {
            await renderSubagentLatestFinalOnly(el, card, events, agentId);
            bindSubagentFinalOnlyHistoryLoader(el, card, agentId, hasOlder, rangeStart);
        } else {
            await renderSubagentProcessEvents(el, card, events, agentId);
        }
        el.dataset.loaded = '1';
        delete el.dataset.streamReady;
        // 对于折叠模式，事件计数使用实际加载的数量
        setSubagentCardEventCount(agentId, events.length);
    } catch (e) {
        if (!el.isConnected) return;
        el.innerHTML = '<div class="subagent-detail-empty">加载失败: ' + escapeHtml(String(e)) + '</div>';
        el.dataset.loaded = '1';
    } finally {
        delete el.dataset.loading;
    }
}

async function refreshSubagentTreePanel(sessionId) {
    if (subagentTreeRefreshInflight && subagentTreeRefreshInflightSid === sessionId) {
        subagentTreeRefreshQueued = true;
        return subagentTreeRefreshInflight;
    }
    subagentTreeRefreshInflightSid = sessionId;
    subagentTreeRefreshInflight = refreshSubagentTreePanelInner(sessionId);
    try {
        return await subagentTreeRefreshInflight;
    } finally {
        subagentTreeRefreshInflight = null;
        subagentTreeRefreshInflightSid = null;
        if (subagentTreeRefreshQueued && sessionId === currentSessionId) {
            subagentTreeRefreshQueued = false;
            void refreshSubagentTreePanel(currentSessionId);
        }
    }
}

async function refreshSubagentTreePanelInner(sessionId) {
    bindSubagentPanelOnce();
    var seq = ++subagentPanelRefreshSeq;
    var grid = document.getElementById('subagent-grid');
    var toggleBtn = document.getElementById('subagent-toggle-btn');
    if (!grid || !sessionId) {
        if (toggleBtn) toggleBtn.classList.add('hidden');
        closeSubagentPanel();
        stopSubagentIncrementalSync();
        return;
    }
    if (grid.dataset.sessionId && grid.dataset.sessionId !== sessionId) {
        grid.innerHTML = '';
        subagentStore.clearEventCounts(sessionId);
    }
    grid.dataset.sessionId = sessionId;
    try {
        var resp = await fetch('/sessions/' + encodeURIComponent(sessionId) + '/subagents?lite=1');
        if (seq !== subagentPanelRefreshSeq || sessionId !== currentSessionId) return;
        var data = await resp.json();
        var flat = (data && data.subagents) ? data.subagents : [];
        applySubagentSnapshot(sessionId, flat);
        flat = selectSubagentList(sessionId);
        if (!flat.length) {
            if (toggleBtn) toggleBtn.classList.add('hidden');
            closeSubagentPanel();
            grid.innerHTML = '';
            grid.dataset.sessionId = sessionId;
            subagentStore.clearEventCounts(sessionId);
            stopSubagentIncrementalSync();
            return;
        }
        refreshSubagentToggleFromGrid(flat);
        syncSubagentGridFromFlat(flat, sessionId);
        if (seq !== subagentPanelRefreshSeq || sessionId !== currentSessionId) return;
        if (subagentPanelOpen) {
            document.getElementById('subagent-dock').classList.remove('hidden');
            ensureSubagentCardViewportObserver(grid);
            grid.querySelectorAll('.subagent-grid-card').forEach(function (card) {
                observeSubagentCardViewport(card);
                if (card.classList.contains('is-expanded')) {
                    scheduleSubagentCardStats(card);
                }
            });
            loadVisibleSubagentCardBodies(grid, sessionId);
            flat.forEach(function (n) {
                if (!n || !n.id) return;
                var card = grid.querySelector('.subagent-grid-card[data-agent-id="' + String(n.id || '') + '"]');
                if (card && card.classList.contains('is-expanded')) {
                    refreshSubagentContextForCard(card, String(n.id || ''), true);
                }
            });
        }
        var runningN = selectSubagentRunningCount(sessionId);
        if (runningN > 0 && subagentPanelOpen) scheduleSubagentIncrementalSync();
        else {
            stopSubagentIncrementalSync();
            if (sessionId === currentSessionId) updateSubagentContinueBanner(sessionId);
        }
    } catch (e) {
        if (toggleBtn) toggleBtn.classList.add('hidden');
        closeSubagentPanel();
        stopSubagentIncrementalSync();
    }
}

function ensureSubagentBlock(ctx, event) {
    var body = getProcessBody(ctx);
    if (!body) return null;
    var aid = String(event.agent_id || event.run_id || '');
    if (!aid) return null;
    if (!ctx.subagentBlocks) ctx.subagentBlocks = {};
    var blk = ctx.subagentBlocks[aid];
    if (blk && blk.isConnected) return blk;
    blk = document.createElement('div');
    blk.className = 'subagent-block';
    blk.dataset.agentId = aid;
    var status = event.background ? '后台运行' : '运行中';
    blk.innerHTML = '<div class="subagent-block-head" role="button" tabindex="0">'
        + '<span class="subagent-block-badge is-running">' + escapeHtml(status) + '</span>'
        + '<strong>' + escapeHtml(event.description || 'subagent') + '</strong>'
        + '<span class="subagent-block-meta">' + escapeHtml(event.subagent_type || '') + '</span>'
        + '<span class="subagent-block-id">' + escapeHtml(aid.slice(0, 8)) + '…</span>'
        + '</div>'
        + '<div class="subagent-block-preview"></div>'
        + '<div class="subagent-block-body process-aggregate-body"></div>';
    body.appendChild(blk);
    var head = blk.querySelector('.subagent-block-head');
    if (head) {
        head.addEventListener('click', function () {
            blk.classList.toggle('is-open');
            var det = blk.querySelector('.subagent-block-body');
            if (blk.classList.contains('is-open') && det && det.dataset.loaded !== '1' && det.dataset.loading !== '1') {
                loadSubagentDetailInto(det, aid, blk);
            }
        });
    }
    ctx.subagentBlocks[aid] = blk;
    handleSubagentLifecycleEvent({ type: 'subagent_start', agent_id: aid, description: event.description, subagent_type: event.subagent_type, background: event.background });
    return blk;
}

function updateSubagentBlockFinish(ctx, event) {
    var aid = String(event.agent_id || event.run_id || '');
    if (!aid) return;
    var blk = (ctx.subagentBlocks && ctx.subagentBlocks[aid]) || null;
    if (!blk || !blk.isConnected) {
        var body = getProcessBody(ctx);
        if (body) blk = body.querySelector('.subagent-block[data-agent-id="' + aid + '"]');
    }
    if (!blk) {
        handleSubagentLifecycleEvent(event);
        return;
    }
    var badge = blk.querySelector('.subagent-block-badge');
    var preview = blk.querySelector('.subagent-block-preview');
    var ok = event.ok !== false;
    if (badge) {
        badge.textContent = ok ? '完成' : '失败';
        badge.classList.remove('is-running');
        badge.classList.toggle('is-done', ok);
        badge.classList.toggle('is-error', !ok);
    }
    if (preview) {
        var txt = event.result_preview || event.error || '';
        preview.textContent = txt ? String(txt).slice(0, 500) : '';
    }
    handleSubagentLifecycleEvent(event);
}
