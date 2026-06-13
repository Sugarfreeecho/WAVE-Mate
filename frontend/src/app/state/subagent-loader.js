var subagentCardViewportObserver = null;
var subagentCardLoadQueue = [];
var subagentCardLoadInflight = 0;
var subagentCardLoadQueued = Object.create(null);
var SUBAGENT_BODY_LOAD_CONCURRENCY = 2;
var SUBAGENT_DETAIL_RENDER_BATCH = 8;
var SUBAGENT_HISTORY_TURNS_PER_PAGE = 3;

function scheduleSubagentDetailWork(fn) {
    setTimeout(fn, 0);
}

function shouldLoadSubagentCardBodies() {
    return !!subagentPanelOpen;
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

    bodyEl._hasOlderEvents = hasOlder !== false;
    bodyEl._rangeStart = typeof rangeStart === 'number' ? rangeStart : 0;
    bodyEl._historyLoadedEvents = [];

    function loadMoreHistory() {
        if (!bodyEl.isConnected || bodyEl.dataset.historyLoading === '1' || bodyEl.dataset.historyComplete === '1') return;

        if (!bodyEl._hasOlderEvents) {
            bodyEl.dataset.historyComplete = '1';
            delete bodyEl.dataset.finalOnly;
            bodyEl.classList.remove('is-final-only');
            return;
        }

        var oldScrollHeight = bodyEl.scrollHeight || 0;
        var oldScrollTop = bodyEl.scrollTop || 0;
        bodyEl.dataset.historyLoading = '1';

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

                bodyEl._hasOlderEvents = hasOlderNew;
                bodyEl._rangeStart = rangeStartNew;
                bodyEl._historyLoadedEvents = events.concat(bodyEl._historyLoadedEvents);

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

async function loadSubagentDetailInto(el, agentId, hostEl, sessionIdOpt) {
    if (!el || !agentId) return;
    if (el.dataset.loading === '1') return;
    var card = hostEl || (el.closest ? el.closest('.subagent-grid-card, .subagent-block') : null);
    el.dataset.loading = '1';
    delete el.dataset.loaded;
    el.innerHTML = '<div class="subagent-detail-empty">加载详情中…</div>';
    try {
        var isCollapsed = card && card.classList && !card.classList.contains('is-expanded') && card.classList.contains('subagent-grid-card');
        var turnsParam = isCollapsed ? '&turns=3' : '&turns=10';
        var resp = await fetch('/sessions/' + encodeURIComponent(agentId) + '/messages?' + turnsParam);
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        var data = await resp.json();

        var events, hasOlder, rangeStart;
        if (data && Array.isArray(data)) {
            events = data;
            hasOlder = false;
            rangeStart = 0;
        } else if (data && Array.isArray(data.events)) {
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
        setSubagentCardEventCount(agentId, events.length);
    } catch (e) {
        if (!el.isConnected) return;
        el.innerHTML = '<div class="subagent-detail-empty">加载失败: ' + escapeHtml(String(e)) + '</div>';
        el.dataset.loaded = '1';
    } finally {
        delete el.dataset.loading;
    }
}
