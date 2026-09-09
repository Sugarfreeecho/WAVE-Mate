function formatTokenCompact(n) {
    if (n == null || !Number.isFinite(Number(n))) return '—';
    const x = Math.max(0, Math.round(Number(n)));
    if (x >= 1000000) return (x / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
    if (x >= 10000) return (x / 1000).toFixed(x % 1000 === 0 ? 0 : 1).replace(/\.0$/, '') + 'k';
    if (x >= 1000) return (x / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
    return String(x);
}

function setContextTokenLabel(estimated, threshold) {
    const el = document.getElementById('ctx-tokens');
    if (!el) return;
    const label = el.querySelector('.ctx-label');
    const fill = el.querySelector('.ctx-fill');
    const pctEl = el.querySelector('.ctx-pct');
    const t = (threshold != null && Number(threshold) > 0) ? Number(threshold) : defaultCtxThreshold;
    const n = (estimated != null && Number(estimated) >= 0) ? Math.round(Number(estimated)) : null;
    if (n == null) {
        if (label) label.textContent = '— / —';
        if (pctEl) pctEl.textContent = '';
        if (fill) fill.style.width = '0%';
        el.classList.remove('is-warn', 'is-bad');
        el.setAttribute('data-ui-tip', '预估上下文 token：选择会话并加载或发送消息后显示。分母为压缩摘要阈值。');
        bindUiHoverTip(el);
        return;
    }
    const pct = (n / t) * 100;
    const pctDisp = (Math.round(pct * 10) / 10);
    if (label) label.textContent = formatTokenCompact(n) + ' / ' + formatTokenCompact(t);
    if (pctEl) pctEl.textContent = pctDisp + '%';
    if (fill) fill.style.width = Math.min(100, pct) + '%';
    el.classList.remove('is-warn', 'is-bad');
    if (pct >= 100) el.classList.add('is-bad');
    else if (pct >= 80) el.classList.add('is-warn');
    var tipPct = pct >= 100
        ? ('约 ' + pctDisp + '%，超出门限 ' + (Math.round((pct - 100) * 10) / 10) + '%')
        : ('约 ' + pctDisp + '%');
    el.setAttribute(
        'data-ui-tip',
        formatTokenCompact(n) + ' / ' + formatTokenCompact(t) + ' tokens（' + tipPct
            + '）。预估进入模型的上下文规模，含历史与系统提示；分母为当前 model profile 中触发压缩摘要的上下文门限。'
    );
    bindUiHoverTip(el);
}

let contextTokenRequestSeq = 0;
const contextTokenInFlightBySession = Object.create(null);
const CONTEXT_TOKEN_CACHE_TTL_MS = 3000;

async function refreshContextTokensFromServer(sid, seq) {
    if (!sid) return;
    const cached = selectContextTokens(sid);
    if (cached && cached.updatedAt && (Date.now() - cached.updatedAt) < CONTEXT_TOKEN_CACHE_TTL_MS) {
        if (sid === currentSessionId) setContextTokenLabel(cached.estimated, cached.threshold);
        return;
    }
    if (contextTokenInFlightBySession[sid]) return;
    contextTokenInFlightBySession[sid] = true;
    try {
        const r = await fetch('/sessions/' + encodeURIComponent(sid) + '/context_tokens');
        const j = await r.json();
        if (seq != null && seq !== contextTokenRequestSeq) return;
        if (sid !== currentSessionId) return;
        if (r.ok && j && j.ok && j.estimated != null && j.estimated >= 0) {
            recordContextTokens(sid, j.estimated, j.threshold);
            return;
        }
    } catch (e) { /* ignore */ }
    finally {
        delete contextTokenInFlightBySession[sid];
    }
    applyContextTokenLabelForCurrentSession();
}

/** 在浏览器完成首帧绘制后再请求 context_tokens，避免与切换会话/新建会话的 DOM 抢主线程。 */
function scheduleContextTokensAfterPaint(sid) {
    if (!sid) return;
    if (sid === currentSessionId) applyContextTokenLabelForCurrentSession();
    const seq = ++contextTokenRequestSeq;
    requestAnimationFrame(function () {
        requestAnimationFrame(function () {
            refreshContextTokensFromServer(sid, seq);
        });
    });
}

function recordContextTokens(sessionId, estimated, threshold) {
    if (!sessionId) return;
    setContextTokensForSession(sessionId, estimated, threshold);
    if (sessionId === currentSessionId) setContextTokenLabel(estimated, threshold);
}

function applyContextTokenLabelForCurrentSession() {
    if (!currentSessionId) { setContextTokenLabel(null, null); return; }
    const x = selectContextTokens(currentSessionId);
    if (x) setContextTokenLabel(x.estimated, x.threshold);
    else setContextTokenLabel(null, null);
}

/** 主对话区跟到底 */
function scrollChatToBottomIfFollow(runSessionId, opts) {
    opts = opts || {};
    if (shouldGateScrollByRunSession(null, runSessionId)) return;
    if (!opts.force && !liveAutoFollow) return;
    if (chatContainer) setScrollTopImmediate(chatContainer, chatContainer.scrollHeight);
}

function setScrollTopImmediate(el, y) {
    if (!el) return;
    var prev = el.style.scrollBehavior;
    el.style.scrollBehavior = 'auto';
    try {
        el.scrollTop = y;
    } finally {
        // Restore synchronously so overlapping writes cannot restore each
        // other's temporary style on later frames.
        el.style.scrollBehavior = prev;
    }
}

/** 当前运行会话对应的执行过程框滚动容器（.process-aggregate-body） */
function getProcessBodyElForCurrentRun() {
    var sid = currentSessionId;
    var run = sid && getSessionRunState(sid);
    if (!run || !run.ctx) return null;
    var c = run.ctx;
    if (c.currentProcessGroup && c.currentProcessGroup.isConnected) {
        return c.currentProcessGroup.querySelector('.process-aggregate-body');
    }
    if (!c.stream) return null;
    var agg = c.stream.querySelector('.process-aggregate:last-of-type');
    return agg ? agg.querySelector('.process-aggregate-body') : null;
}

var STREAM_PROC_NEAR_BOTTOM_PX = 96;
var STREAM_CHAT_NEAR_BOTTOM_PX = 72;

function isSmoothStreamPortNearBottom(port, thresholdPx) {
    if (!port) return false;
    if (!isSmoothStreamActive()) return isNearBottom(port, thresholdPx);
    if (smoothFollowController.isReaderDetached(port)) {
        // An intentional upward gesture must win over the legacy broad
        // near-bottom threshold. Re-arm only once the reader reaches the floor.
        if (!isNearBottom(port, 2)) return false;
        smoothFollowController.clearReaderDetached(port);
    }
    return smoothFollowController.isFollowing(port) || isNearBottom(port, thresholdPx);
}

/** 生成中时：对话区与当前执行过程区均在底部附近时才允许自动跟随流式滚动 */
function refreshLiveAutoFollowPins() {
    if (!chatContainer) return;
    if (isSessionRunning(currentSessionId)) {
        streamChatNearBottom = isSmoothStreamPortNearBottom(
            chatContainer,
            STREAM_CHAT_NEAR_BOTTOM_PX
        );
        var pb = getProcessBodyElForCurrentRun();
        streamProcNearBottom = !pb || isSmoothStreamPortNearBottom(pb, STREAM_PROC_NEAR_BOTTOM_PX);
        liveAutoFollow = streamChatNearBottom && streamProcNearBottom;
    } else {
        liveAutoFollow = isSmoothStreamPortNearBottom(chatContainer, STREAM_CHAT_NEAR_BOTTOM_PX);
    }
}

function isSubagentStreamCtx(ctx) {
    if (!ctx) return false;
    if (ctx._subagentBody && ctx._subagentBody.isConnected) return true;
    if (ctx.currentProcessGroup && ctx.currentProcessGroup.isConnected
        && ctx.currentProcessGroup.classList.contains('subagent-grid-card')) return true;
    return false;
}

/** 子 agent 卡片流式更新用 agentId 作 runSessionId，不能按主会话 currentSessionId 拦截滚动 */
function shouldGateScrollByRunSession(ctx, runSessionId) {
    if (!runSessionId) return false;
    if (isSubagentStreamCtx(ctx)) return false;
    return runSessionId !== currentSessionId;
}

function collectFeedChunkRootsFromCtx(ctx) {
    var roots = [];
    var seen = new Set();
    function addRoot(root) {
        if (!root || !root.isConnected || seen.has(root)) return;
        seen.add(root);
        roots.push(root);
    }
    if (ctx && ctx.stream && ctx.stream.isConnected) addRoot(ctx.stream);
    if (ctx && ctx._subagentTurnProcess) addRoot(ctx._subagentTurnProcess);
    if (ctx && ctx._subagentBody) addRoot(ctx._subagentBody);
    return roots;
}

function queryFeedChunksInCtx(ctx, selector) {
    var sel = selector || '.feed-chunk';
    var out = [];
    var seen = new Set();
    collectFeedChunkRootsFromCtx(ctx).forEach(function (root) {
        root.querySelectorAll(sel).forEach(function (ch) {
            if (!seen.has(ch)) {
                seen.add(ch);
                out.push(ch);
            }
        });
    });
    return out;
}

function refreshFeedChunksInCtx(ctx, selector) {
    queryFeedChunksInCtx(ctx, selector).forEach(function (ch) {
        scheduleFeedChunkOverflowRefresh(ch);
    });
}

function ensureSubagentTurnProcessOpen(ctx) {
    /* 默认折叠执行过程，不在自动滚动时强制展开 */
}

function shouldDeferSubagentProcessDom(ctx) {
    if (!ctx || !ctx.currentTurn || !ctx.currentTurn.isConnected) return true;
    return !ctx.currentTurn.classList.contains('is-process-open');
}

function deferSubagentProcessEvent(turn, event, eventIndex) {
    if (!turn || !event) return;
    if (!turn._deferredProcessEvents) turn._deferredProcessEvents = [];
    turn._deferredProcessEvents.push({ event: event, eventIndex: eventIndex });
    turn.dataset.processDeferred = '1';
}

function pinSubagentCardScrollForManualExpand(body) {
    if (!body) return { savedScroll: 0, release: function () {} };
    var ctx = body._subagentStreamCtx;
    var savedScroll = body.scrollTop;
    if (ctx) ctx._suppressSubagentScrollFollow = true;
    return {
        savedScroll: savedScroll,
        release: function () {
            if (ctx) ctx._suppressSubagentScrollFollow = false;
        },
        restoreScroll: function () {
            if (body.isConnected) body.scrollTop = savedScroll;
        }
    };
}

function restoreSubagentCardScrollAfterLayout(body, savedScroll) {
    if (!body) return;
    requestAnimationFrame(function () {
        requestAnimationFrame(function () {
            if (body.isConnected) body.scrollTop = savedScroll;
        });
    });
}

var SUBAGENT_PROCESS_HYDRATE_BATCH = 24;
var SUBAGENT_PROCESS_REFRESH_CHUNK_LIMIT = 80;

function runSubagentProcessBatch(fn) {
    if (typeof requestIdleCallback === 'function') {
        requestIdleCallback(fn, { timeout: 120 });
    } else {
        requestAnimationFrame(fn);
    }
}

function refreshSubagentProcessChunksLightly(turn) {
    if (!turn || !turn.querySelectorAll) return;
    var chunks = turn.querySelectorAll('.feed-chunk');
    var limit = Math.min(chunks.length, SUBAGENT_PROCESS_REFRESH_CHUNK_LIMIT);
    for (var i = 0; i < limit; i += 1) {
        scheduleFeedChunkOverflowRefresh(chunks[i]);
    }
}

function hydrateSubagentTurnProcess(turn, ctx, agentId) {
    if (!turn || !ctx) return;
    var processEl = turn.querySelector('.subagent-turn-process');
    if (turn.dataset.processHydrated === '1' && processEl && processEl.children.length) return;
    var items = turn._deferredProcessEvents;
    if (!items || !items.length) {
        turn.dataset.processHydrated = '1';
        return;
    }
    var body = ctx._subagentBody;
    var pin = pinSubagentCardScrollForManualExpand(body);
    ctx.currentTurn = turn;
    ctx._subagentTurnProcess = processEl;
    ctx._subagentTurnFinalSlot = turn.querySelector('.subagent-turn-final-slot');
    resetLlmState(ctx);
    finalizeProgressStreamChunks(ctx);
    function replayDeferredProcessEvent(item) {
        var ev = item && item.event;
        if (!ev || typeof ev !== 'object') return;
        if (shouldSkipSubagentProcessEvent(ev)) return;
        if (ev.ephemeral) {
            return;
        }
        reduceAndRenderMessageEvent(ctx, ev, {
            sessionId: agentId,
            eventIndex: item.eventIndex,
            source: 'subagent-history',
        });
    }
    var index = 0;
    turn.dataset.processLoading = '1';
    function finishHydrate() {
        finalizeLlmStreamChunks(ctx);
        finalizeProgressStreamChunks(ctx);
        delete turn._deferredProcessEvents;
        delete turn.dataset.processDeferred;
        delete turn.dataset.processLoading;
        turn.dataset.processHydrated = '1';
        markSubagentTurnHasProcess(turn);
        refreshSubagentProcessChunksLightly(turn);
        pin.release();
        restoreSubagentCardScrollAfterLayout(body, pin.savedScroll);
    }
    function step() {
        if (!turn.isConnected || !body || !body.isConnected) {
            delete turn.dataset.processLoading;
            pin.release();
            return;
        }
        var end = Math.min(index + SUBAGENT_PROCESS_HYDRATE_BATCH, items.length);
        for (; index < end; index += 1) {
            replayDeferredProcessEvent(items[index]);
        }
        if (index < items.length) {
            runSubagentProcessBatch(step);
        } else {
            finishHydrate();
        }
    }
    step();
}

function repairMisplacedSubagentFeedItems(body, turn) {
    if (!body || !turn) return;
    var proc = turn.querySelector('.subagent-turn-process');
    if (!proc) return;
    Array.prototype.slice.call(body.children).forEach(function (node) {
        if (!node || !node.classList || !node.classList.contains('feed-item')) return;
        proc.appendChild(node);
    });
}

function collectSubagentTurnProcessSlice(events, userEventIndex) {
    var slice = [];
    if (!events || !events.length || !Number.isFinite(userEventIndex) || userEventIndex < 0) return slice;
    for (var i = userEventIndex + 1; i < events.length; i += 1) {
        var ev = events[i];
        if (!ev || typeof ev !== 'object') continue;
        var t = ev.type;
        if (t === 'user') break;
        if (t === 'final') break;
        if (t === 'subagent_start' || t === 'subagent_finish') continue;
        if (shouldSkipSubagentProcessEvent(ev)) continue;
        slice.push({ event: ev, eventIndex: i });
    }
    return slice;
}

async function fetchAndHydrateSubagentTurnProcess(turn, body) {
    if (!turn || !body || turn.dataset.processLoading === '1' || turn.dataset.processFetching === '1') return;
    var card = body.closest('.subagent-grid-card');
    var agentId = (card && card.getAttribute('data-agent-id')) || body.getAttribute('data-agent-id') || '';
    if (!agentId) return;
    var userWrap = turn.querySelector('.msg-wrap--user');
    var userIdx = userWrap ? parseInt(userWrap.getAttribute('data-event-index') || '-1', 10) : -1;
    if (!Number.isFinite(userIdx) || userIdx < 0) return;
    var pin = pinSubagentCardScrollForManualExpand(body);
    turn.dataset.processFetching = '1';
    try {
        var resp = await fetch('/sessions/' + encodeURIComponent(agentId) + '/messages');
        if (!resp.ok) return;
        var events = normalizeSubagentMessagesPayload(await resp.json());
        if (!turn.isConnected) return;
        turn._deferredProcessEvents = collectSubagentTurnProcessSlice(events, userIdx);
        delete turn.dataset.processHydrated;
        hydrateSubagentTurnProcessFromEl(turn, body);
    } catch (e) { /* ignore */ }
    finally {
        delete turn.dataset.processFetching;
        pin.release();
        restoreSubagentCardScrollAfterLayout(body, pin.savedScroll);
    }
}

function ensureSubagentTurnProcessContent(turn, body) {
    if (!turn || !body) return;
    repairMisplacedSubagentFeedItems(body, turn);
    var processEl = turn.querySelector('.subagent-turn-process');
    if (processEl && processEl.children.length) return;
    if (turn._deferredProcessEvents && turn._deferredProcessEvents.length) {
        hydrateSubagentTurnProcessFromEl(turn, body);
        return;
    }
    if (turn.dataset.processDeferred === '1' || turn.querySelector('.msg-wrap--user.has-turn-process')) {
        void fetchAndHydrateSubagentTurnProcess(turn, body);
    }
}

function toggleSubagentTurnProcess(turn, body, userWrap) {
    if (!turn || !body || !userWrap) return;
    var open = !turn.classList.contains('is-process-open');
    turn.classList.toggle('is-process-open', open);
    userWrap.classList.toggle('is-process-open', open);
    delete body.dataset.cacheClean;
    if (open) {
        ensureSubagentTurnProcessContent(turn, body);
        refreshSubagentProcessChunksLightly(turn);
        return;
    }
}

function hydrateSubagentTurnProcessFromEl(turn, body) {
    if (!turn || !body) return;
    var card = body.closest('.subagent-grid-card');
    var agentId = (card && card.getAttribute('data-agent-id')) || body.getAttribute('data-agent-id') || '';
    var ctx = body._subagentStreamCtx || (agentId && card ? getSubagentCardStreamCtx(body, card, agentId) : null);
    if (ctx && agentId) hydrateSubagentTurnProcess(turn, ctx, agentId);
}

function feedChunkCollapsedMax(chunk) {
    var styles = getComputedStyle(chunk);
    var line = parseFloat(styles.getPropertyValue('--line')) || 21.6;
    var pad = parseFloat(styles.getPropertyValue('--scroller-pad-y')) || 4;
    return line * 2.5 + pad * 2;
}

function feedChunkInHiddenSubagentProcess(chunk) {
    var process = chunk.closest('.subagent-turn-process');
    if (!process || !process.children.length) return false;
    var turn = process.closest('.subagent-turn');
    return !!(turn && !turn.classList.contains('is-process-open'));
}

function measureFeedChunkScrollerHeight(sc, chunk) {
    if (!sc) return 0;
    var h = sc.scrollHeight;
    if (h > 1) return h;
    var process = chunk && chunk.closest('.subagent-turn-process');
    var turn = process && process.closest('.subagent-turn');
    if (!process || !turn || turn.classList.contains('is-process-open')) return h;
    var prevDisplay = process.style.display;
    var prevVis = process.style.visibility;
    var prevPos = process.style.position;
    var prevLeft = process.style.left;
    var prevRight = process.style.right;
    var prevPointer = process.style.pointerEvents;
    process.style.display = 'block';
    process.style.visibility = 'hidden';
    process.style.position = 'absolute';
    process.style.left = '0';
    process.style.right = '0';
    process.style.pointerEvents = 'none';
    h = sc.scrollHeight;
    process.style.display = prevDisplay;
    process.style.visibility = prevVis;
    process.style.position = prevPos;
    process.style.left = prevLeft;
    process.style.right = prevRight;
    process.style.pointerEvents = prevPointer;
    return h;
}

function refreshAllFeedChunksUnder(root) {
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll('.feed-chunk').forEach(scheduleFeedChunkOverflowRefresh);
}

function shouldFollowSubagentCard(ctx) {
    if (!ctx || ctx._suppressSubagentScrollFollow) return false;
    if (!ctx._subagentBody || !ctx._subagentBody.isConnected) return false;
    var aid = ctx._subagentBody.getAttribute('data-agent-id') || '';
    if (aid && subagentCardNearBottom[aid] === false) return false;
    return liveAutoFollow || subagentCardNearBottom[aid] !== false;
}

function bindSubagentCardBodyScrollFollow(body) {
    if (!body || body.dataset.subagentScrollFollowBound) return;
    body.dataset.subagentScrollFollowBound = '1';
    var aid = body.getAttribute('data-agent-id') || ('body-' + Math.random());
    if (subagentCardNearBottom[aid] == null) subagentCardNearBottom[aid] = true;
    body.addEventListener('scroll', function () {
        subagentCardNearBottom[aid] = isSmoothStreamPortNearBottom(
            body,
            SUBAGENT_CARD_NEAR_BOTTOM_PX
        );
    }, { passive: true });
}

function scrollSubagentCardBodyToBottom(ctx) {
    if (!ctx || !ctx._subagentBody || !ctx._subagentBody.isConnected) return;
    var body = ctx._subagentBody;
    var aid = body.getAttribute('data-agent-id') || '';
    if (aid) subagentCardNearBottom[aid] = true;
    requestAnimationFrame(function () {
        body.scrollTop = body.scrollHeight;
        requestAnimationFrame(function () {
            body.scrollTop = body.scrollHeight;
        });
    });
}

function scrollContentAreaIfFollow(ctx, runSessionId, channel) {
    if (typeof replayingMessages !== 'undefined' && replayingMessages) return;
    if (shouldGateScrollByRunSession(ctx, runSessionId)) return;
    // Non-token trace events (status/tool/result/etc.) arrive through this
    // generic path and default to the whole-row motion profile.
    if (isSmoothStreamActive()) {
        if (typeof isHistorySmoothScrollActive === 'function' && isHistorySmoothScrollActive()) return;
        followStreamProcessScroll(ctx, runSessionId, channel || 'row');
        return;
    }
    if (isSubagentStreamCtx(ctx)) {
        if (!shouldFollowSubagentCard(ctx)) return;
        scrollSubagentCardBodyToBottom(ctx);
        return;
    }
    if (!liveAutoFollow) return;
    scrollProcessBodyToBottom(ctx, runSessionId);
    scrollChatToBottomIfFollow(runSessionId, {});
}

/** 将当前步的执行框滚到底（流式增量主要长在这里，必须滚 procBody 而不是只滚对话区） */
function scrollProcessBodyToBottom(ctx, runSessionId) {
    if (shouldGateScrollByRunSession(ctx, runSessionId)) return;
    if (isSubagentStreamCtx(ctx)) {
        scrollSubagentCardBodyToBottom(ctx);
        return;
    }
    if (!ctx || !ctx.stream) return;
    var agg = (ctx.currentProcessGroup && ctx.currentProcessGroup.isConnected)
        ? ctx.currentProcessGroup
        : ctx.stream.querySelector('.process-aggregate:last-of-type');
    if (agg) {
        var procBody = agg.querySelector('.process-aggregate-body');
        if (procBody) procBody.scrollTop = procBody.scrollHeight;
    }
}

function followStreamProcessScroll(ctx, runSessionId, channel) {
    if (typeof replayingMessages !== 'undefined' && replayingMessages) return;
    if (shouldGateScrollByRunSession(ctx, runSessionId)) return;
    var followChannel = channel === 'text' ? 'text' : 'row';
    if (
        isSmoothStreamActive()
        && typeof isHistorySmoothScrollActive === 'function'
        && isHistorySmoothScrollActive()
    ) return;
    if (isSubagentStreamCtx(ctx)) {
        if (!shouldFollowSubagentCard(ctx)) return;
        if (isSmoothStreamActive()) {
            var smoothSubagentBody = ctx && ctx._subagentBody;
            if (!smoothSubagentBody || !smoothSubagentBody.isConnected) return;
            var smoothAgentId = smoothSubagentBody.getAttribute('data-agent-id') || '';
            smoothFollowController.request(smoothSubagentBody, {
                speedCps: ctx && ctx.llm ? ctx.llm.llmRevealCpsEma : 35,
                channel: followChannel,
                traceHeightSource: ctx && ctx._subagentTurnProcess
                    ? ctx._subagentTurnProcess
                    : smoothSubagentBody,
                onUnpin: function () {
                    if (smoothAgentId) subagentCardNearBottom[smoothAgentId] = false;
                },
            });
            if (!subagentScrollFollowRaf) {
                subagentScrollFollowRaf = requestAnimationFrame(function () {
                    subagentScrollFollowRaf = 0;
                    refreshFeedChunksInCtx(ctx, '.feed-chunk.is-streaming');
                });
            }
            return;
        }
        if (subagentScrollFollowRaf) return;
        subagentScrollFollowRaf = requestAnimationFrame(function () {
            subagentScrollFollowRaf = 0;
            scrollSubagentCardBodyToBottom(ctx);
            refreshFeedChunksInCtx(ctx, '.feed-chunk.is-streaming');
        });
        return;
    }
    if (!liveAutoFollow) return;
    if (isSmoothStreamActive()) {
        if (ctx && ctx.currentProcessGroup && ctx.currentProcessGroup.isConnected
            && ctx.currentProcessGroup.classList.contains('is-collapsed')) {
            ctx.currentProcessGroup.classList.remove('is-collapsed');
            var smoothTop = ctx.currentProcessGroup.querySelector('.process-aggregate-top');
            if (smoothTop) smoothTop.setAttribute('aria-expanded', 'true');
        }
        var smoothSpeed = ctx && ctx.llm ? ctx.llm.llmRevealCpsEma : 35;
        var smoothProcessBody = getProcessBodyElForCurrentRun();
        var releaseMainFollow = function (port) {
            if (port === chatContainer) streamChatNearBottom = false;
            else streamProcNearBottom = false;
            liveAutoFollow = false;
            smoothFollowController.cancel(port === chatContainer ? smoothProcessBody : chatContainer);
        };
        if (smoothProcessBody) {
            smoothFollowController.request(smoothProcessBody, {
                speedCps: smoothSpeed,
                channel: followChannel,
                traceHeightSource: smoothProcessBody,
                onUnpin: releaseMainFollow,
            });
        }
        if (chatContainer) {
            smoothFollowController.request(chatContainer, {
                speedCps: smoothSpeed,
                channel: followChannel,
                traceHeightSource: smoothProcessBody,
                onUnpin: releaseMainFollow,
            });
        }
        if (!streamScrollFollowRaf) {
            streamScrollFollowRaf = requestAnimationFrame(function () {
                streamScrollFollowRaf = 0;
                refreshFeedChunksInCtx(ctx, '.feed-chunk.is-streaming');
                refreshLiveAutoFollowPins();
            });
        }
        return;
    }
    if (streamScrollFollowRaf) return;
    streamScrollFollowRaf = requestAnimationFrame(function () {
        streamScrollFollowRaf = 0;
        if (!liveAutoFollow) return;
        if (ctx && ctx.currentProcessGroup && ctx.currentProcessGroup.isConnected) {
            if (ctx.currentProcessGroup.classList.contains('is-collapsed')) {
                ctx.currentProcessGroup.classList.remove('is-collapsed');
                const topN = ctx.currentProcessGroup.querySelector('.process-aggregate-top');
                if (topN) topN.setAttribute('aria-expanded', 'true');
            }
        }
        scrollProcessBodyToBottom(ctx, runSessionId);
        scrollChatToBottomIfFollow(runSessionId, {});
        refreshLiveAutoFollowPins();
    });
}

/** Finish without a long easing tail once no more stream content can arrive. */
function finishStreamScrollIfFollow(ctx, runSessionId) {
    if (isSmoothStreamActive()) {
        if (shouldGateScrollByRunSession(ctx, runSessionId)) return;
        if (isSubagentStreamCtx(ctx)) {
            if (shouldFollowSubagentCard(ctx) && ctx._subagentBody) {
                settleSmoothTraceHeightAnimations(ctx._subagentBody);
                smoothFollowController.snapToBottom(ctx._subagentBody);
            }
            return;
        }
        if (!liveAutoFollow) return;
        var processBody = getProcessBodyElForCurrentRun();
        if (processBody) {
            settleSmoothTraceHeightAnimations(processBody);
            smoothFollowController.snapToBottom(processBody);
        }
        if (chatContainer) smoothFollowController.snapToBottom(chatContainer);
        return;
    }
    scrollProcessBodyToBottom(ctx, runSessionId);
    scrollChatToBottomIfFollow(runSessionId, {});
}

/** Final answer cards keep the legacy snap and must not race an active glide. */
function cancelSmoothStreamFollowForFinal(ctx) {
    if (!isSmoothStreamActive()) return;
    if (ctx && ctx.stream === getVisibleChatStream() && !isSubagentStreamCtx(ctx)) {
        smoothFollowController.cancel(chatContainer);
    }
    var processBody = null;
    if (ctx && ctx._subagentBody && ctx._subagentBody.isConnected) {
        processBody = ctx._subagentBody;
    } else if (ctx && ctx.currentProcessGroup && ctx.currentProcessGroup.isConnected) {
        processBody = ctx.currentProcessGroup.querySelector('.process-aggregate-body');
    }
    if (processBody) smoothFollowController.cancel(processBody);
}

/** Keep the native history-load animation isolated from the live follower. */
function cancelSmoothStreamFollowForHistoryLoad() {
    smoothFollowController.cancel(chatContainer);
    var processBody = getProcessBodyElForCurrentRun();
    if (processBody) smoothFollowController.cancel(processBody);
}

/** The shared viewport must not retain the previous session's animation. */
function cancelSmoothStreamFollowForSessionSwitch() {
    if (typeof streamScrollFollowRaf !== 'undefined' && streamScrollFollowRaf) {
        cancelAnimationFrame(streamScrollFollowRaf);
        streamScrollFollowRaf = 0;
    }
    smoothFollowController.reset(chatContainer);
    var stream = getVisibleChatStream();
    if (stream) stream.querySelectorAll('.process-aggregate-body, .subagent-card-body').forEach(function (port) {
        smoothFollowController.reset(port);
    });
}

function getVisibleChatStream() { return document.getElementById('chat-stream'); }

function ensureVisibleChatStreamSlot() {
    if (getVisibleChatStream() || !chatContainer) return;
    const ns = document.createElement('div');
    ns.className = 'chat-stream';
    ns.id = 'chat-stream';
    ns.setAttribute('aria-label', '消息');
    chatContainer.appendChild(ns);
}

function emptyChatStreamKeepingStrip(streamEl) {
    if (!streamEl) return;
    const strip = streamEl.querySelector('#history-load-sentinel');
    Array.from(streamEl.children).forEach(function (ch) {
        if (strip && ch === strip) return;
        ch.remove();
    });
}

function persistHistoryPagingToStream(streamEl, paging) {
    if (!streamEl) return;
    if (!paging || paging.sessionId !== currentSessionId) {
        delete streamEl.dataset.historyPaging;
        return;
    }
    streamEl.dataset.historyPaging = JSON.stringify({
        sessionId: paging.sessionId,
        total: Number(paging.total) || 0,
        range_start: Number(paging.range_start) || 0,
        range_end: Number(paging.range_end) || 0,
        has_older: !!paging.has_older,
        has_newer: !!paging.has_newer,
    });
}

function restoreHistoryPagingFromStream(streamEl) {
    if (!streamEl || !streamEl.dataset.historyPaging) return null;
    try {
        var raw = JSON.parse(streamEl.dataset.historyPaging);
        if (!raw || raw.sessionId !== currentSessionId) return null;
        return {
            sessionId: raw.sessionId,
            total: Number(raw.total) || 0,
            range_start: Number(raw.range_start) || 0,
            range_end: Number(raw.range_end) || 0,
            has_older: !!raw.has_older,
            has_newer: !!raw.has_newer,
        };
    } catch (_e) {
        delete streamEl.dataset.historyPaging;
        return null;
    }
}

function setSessionHistoryPaging(paging) {
    sessionHistoryPaging = paging || null;
    persistHistoryPagingToStream(getVisibleChatStream(), sessionHistoryPaging);
    updateHistorySentinelVisibility();
}

function ensureHistorySentinel(streamEl) {
    if (!streamEl) return null;
    var el = streamEl.querySelector('#history-load-sentinel');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'history-load-sentinel';
    el.className = 'history-load-sentinel';
    el.hidden = true;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'history-load-older-btn';
    btn.textContent = '加载更早记录';
    btn.addEventListener('click', function () { loadOlderHistoryChunk(); });
    el.appendChild(btn);
    streamEl.insertBefore(el, streamEl.firstChild);
    return el;
}

var latestHistoryTailRestoreBySession = Object.create(null);

function getSessionHistoryPaging(sessionId) {
    var sid = String(sessionId || '');
    if (!sid) return null;
    var paging = sessionHistoryPaging;
    var stream = getVisibleChatStream();
    if ((!paging || paging.sessionId !== sid) && stream) {
        paging = restoreHistoryPagingFromStream(stream);
        if (paging) sessionHistoryPaging = paging;
    }
    return paging && paging.sessionId === sid ? paging : null;
}

function sessionHasLiveHistoryOwner(sessionId) {
    var sid = String(sessionId || '');
    return !!sid && (
        isSessionRunning(sid)
        || (typeof isServerStreamActive === 'function' && isServerStreamActive(sid))
    );
}

async function refreshSessionLiveHistoryOwner(sessionId) {
    var sid = String(sessionId || '');
    if (!sid || sessionHasLiveHistoryOwner(sid)) return !!sid;
    if (typeof fetchSessionStreamActiveMap !== 'function') return sessionHasLiveHistoryOwner(sid);
    var activeMap = await fetchSessionStreamActiveMap();
    if (activeMap && Object.prototype.hasOwnProperty.call(activeMap, sid)) {
        if (typeof applyServerStreamActiveMap === 'function') applyServerStreamActiveMap(activeMap);
        if (activeMap[sid]) return true;
    }
    return sessionHasLiveHistoryOwner(sid);
}

async function ensureLatestHistoryTailForLiveAppend(sessionId) {
    var sid = String(sessionId || '');
    if (!sid || sid !== currentSessionId) return true;
    var paging = getSessionHistoryPaging(sid);
    if (!paging || !paging.has_newer) return true;
    if (latestHistoryTailRestoreBySession[sid]) return latestHistoryTailRestoreBySession[sid];
    var restore = (async function () {
        var loaded = await loadSessionMessages(sid, 'bottom', {
            useSnapshot: false,
            preloadOlderIfShort: false,
        });
        if (sid !== currentSessionId) return true;
        var current = getSessionHistoryPaging(sid);
        return loaded === true && !(current && current.has_newer);
    })();
    latestHistoryTailRestoreBySession[sid] = restore;
    try {
        return await restore;
    } finally {
        if (latestHistoryTailRestoreBySession[sid] === restore) {
            delete latestHistoryTailRestoreBySession[sid];
        }
    }
}

var HISTORY_AUTO_LOAD_TOP_PX = 32;

/** 滚到历史顶部附近时自动向前分页；按钮仍保留为加载状态提示和手动兜底。 */
function maybeAutoLoadOlderHistory() {
    if (typeof isHistorySmoothScrollActive === 'function' && isHistorySmoothScrollActive()) return;
    if (!chatContainer || chatContainer.scrollTop > HISTORY_AUTO_LOAD_TOP_PX) return;
    void loadOlderHistoryChunk({ trigger: 'scroll-top' });
}

function updateHistorySentinelVisibility() {
    var strip = document.getElementById('history-load-sentinel');
    var btn = strip && strip.querySelector('.history-load-older-btn');
    var ph = sessionHistoryPaging;
    if (!strip || !btn) return;
    if (!ph || !ph.has_older || ph.sessionId !== currentSessionId) {
        strip.hidden = true;
        btn.disabled = false;
        btn.textContent = '加载更早记录';
        return;
    }
    strip.hidden = false;
    btn.disabled = historyOlderLoading;
    btn.textContent = historyOlderLoading ? '加载中…' : '加载更早记录';
}

function resetSessionHistoryPaging() {
    setSessionHistoryPaging(null);
    historyOlderLoading = false;
    updateHistorySentinelVisibility();
}

async function loadOlderHistoryChunk(opts) {
    opts = opts || {};
    var sid = currentSessionId;
    var stream = getVisibleChatStream();
    var ph = sessionHistoryPaging;
    if ((!ph || ph.sessionId !== sid) && stream) {
        ph = restoreHistoryPagingFromStream(stream);
        if (ph) sessionHistoryPaging = ph;
    }
    if (!sid || !ph || ph.sessionId !== sid || !ph.has_older || historyOlderLoading) return;
    historyOlderLoading = true;
    var prevReplaying = replayingMessages;
    replayingMessages = true;
    updateHistorySentinelVisibility();
    var cc = chatContainer;
    var prependScrollTop = null;
    var prependScrollHeight = null;
    var loadedOlder = false;
    try {
        var pageTurns = Math.max(1, Math.min(Number(opts.turns) || HISTORY_DIALOGUES_PER_PAGE, 50));
        var url = '/sessions/' + encodeURIComponent(sid)
            + '/messages?turns=' + encodeURIComponent(String(pageTurns))
            + '&before_index=' + ph.range_start
            + '&event_budget=' + encodeURIComponent(String(HISTORY_EVENT_BUDGET));
        var response = await fetch(url);
        var data = await response.json();
        if (!response.ok || !data || typeof data !== 'object') return;
        // 自动加载请求返回前可能已切换会话，旧页不能插入新的可见消息流。
        if (sid !== currentSessionId || stream !== getVisibleChatStream()) return;
        var events = data.events;
        if (!Array.isArray(events) || events.length === 0) {
            setSessionHistoryPaging(Object.assign({}, ph, { has_older: !!data.has_older }));
            return;
        }
        ensureHistorySentinel(stream);
        var frag = document.createDocumentFragment();
        var tmpCtx = newDomContext(frag);
        tmpCtx.lastUserEventIndex = -1;
        var rs = typeof data.range_start === 'number' ? data.range_start : 0;
        for (var i = 0; i < events.length; i += 1) {
            var ev = events[i];
            if (ev && typeof ev === 'object' && ev.type) {
                reduceAndRenderMessageEvent(tmpCtx, ev, {
                    sessionId: sid,
                    eventIndex: rs + i,
                    source: 'history-older',
                });
            }
        }
        var sen = stream && stream.querySelector('#history-load-sentinel');
        if (stream && frag.childNodes.length) {
            // fetch 期间用户仍可能滚动，所以必须在真正插入前才记录视口。
            // 插入后补上新增的高度，原来可见的内容就会停在相同屏幕位置。
            if (cc && stream.parentNode === cc) {
                prependScrollTop = cc.scrollTop;
                prependScrollHeight = cc.scrollHeight;
            }
            stream.insertBefore(frag, sen ? sen.nextSibling : stream.firstChild);
        }
        loadedOlder = true;
        setSessionHistoryPaging({
            sessionId: sid,
            total: typeof data.total === 'number' ? data.total : ph.total,
            range_start: typeof data.range_start === 'number' ? data.range_start : ph.range_start,
            range_end: ph.range_end,
            has_older: !!data.has_older,
            has_newer: !!ph.has_newer,
        });
    } catch (e) {
        console.error('加载更早消息失败:', e);
    } finally {
        historyOlderLoading = false;
        updateHistorySentinelVisibility();
        if (loadedOlder) {
            bindExistingLogs(stream);
            if (!opts.keepTocStable) rebuildToc();
            scheduleTocActiveUpdate();
        }
        if (
            cc && stream && stream.parentNode === cc
            && prependScrollTop != null && prependScrollHeight != null
            && !(typeof isHistorySmoothScrollActive === 'function' && isHistorySmoothScrollActive())
        ) {
            setScrollTopImmediate(
                cc,
                prependScrollTop + Math.max(0, cc.scrollHeight - prependScrollHeight)
            );
        }
        replayingMessages = prevReplaying;
    }
}

function insertNewEmptyChatStream() { ensureVisibleChatStreamSlot(); }

async function loadHistoryWindowAroundEventIndex(sessionId, eventIndex, opts) {
    opts = opts || {};
    var sid = String(sessionId || '');
    var ei = Number(eventIndex);
    if (!sid || !Number.isFinite(ei)) return false;
    // A running session's ctx.stream is the append target for live SSE. Never
    // replace that DOM with an isolated history window or subsequent output
    // will be inserted in the middle of history. Older pages are prepended by
    // scrollToUserTurnOrLoadOlder instead.
    if (sessionHasLiveHistoryOwner(sid)) return false;
    var prevReplaying = replayingMessages;
    try {
        var turns = Math.max(1, Math.min(Number(opts.turns) || 50, 50));
        var url = '/sessions/' + encodeURIComponent(sid)
            + '/messages?turns=' + encodeURIComponent(String(turns))
            + '&target_index=' + encodeURIComponent(String(Math.floor(ei)));
        var response = await fetch(url);
        var data = await response.json().catch(function () { return null; });
        if (!response.ok || !data || typeof data !== 'object' || !Array.isArray(data.events)) return false;
        if (sid !== currentSessionId) return false;
        // The run may have started while the target window request was in
        // flight, or the local stream-active snapshot may have been stale.
        // Revalidate against the server before mutating the live append owner.
        if (await refreshSessionLiveHistoryOwner(sid)) return false;
        if (sid !== currentSessionId) return false;
        if (!getVisibleChatStream()) ensureVisibleChatStreamSlot();
        var stream = getVisibleChatStream();
        if (!stream) return false;
        emptyChatStreamKeepingStrip(stream);
        var total = Number(data.total) || 0;
        var rangeEnd = Number(data.range_end) || 0;
        var pageMeta = {
            total: total,
            range_start: Number(data.range_start) || 0,
            range_end: rangeEnd,
            has_older: !!data.has_older,
            has_newer: data.has_newer == null ? rangeEnd < total : !!data.has_newer,
        };
        beginMessageReplay(sid, pageMeta);
        setSessionHistoryPaging({
            sessionId: sid,
            total: pageMeta.total,
            range_start: pageMeta.range_start,
            range_end: pageMeta.range_end,
            has_older: !!pageMeta.has_older,
            has_newer: !!pageMeta.has_newer,
        });
        ensureHistorySentinel(stream);
        var ctx = newDomContext(stream);
        ctx.lastUserEventIndex = -1;
        replayingMessages = true;
        for (var i = 0; i < data.events.length; i += 1) {
            var ev = data.events[i];
            if (ev && typeof ev === 'object' && ev.type) {
                reduceAndRenderMessageEvent(ctx, ev, {
                    sessionId: sid,
                    eventIndex: pageMeta.range_start + i,
                    source: 'history-target',
                });
            }
        }
        replayingMessages = prevReplaying;
        bindExistingLogs(stream);
        rebuildToc();
        updateHistorySentinelVisibility();
        return true;
    } catch (e) {
        replayingMessages = prevReplaying;
        console.error('load target history window failed:', e);
        return false;
    }
}

const SESSION_STREAM_CACHE_LIMIT = 6;
const cachedSessionStreamOrder = [];

function cssEscapeIdent(value) {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(value);
    return String(value || '').replace(/["\\]/g, '\\$&');
}

function cacheOrderTouch(sessionId) {
    var sid = String(sessionId || '');
    if (!sid) return;
    var idx = cachedSessionStreamOrder.indexOf(sid);
    if (idx >= 0) cachedSessionStreamOrder.splice(idx, 1);
    cachedSessionStreamOrder.push(sid);
}

function discardCachedSessionStream(sessionId) {
    var sid = String(sessionId || '');
    if (!sid || !offscreenRoot) return;
    var cached = offscreenRoot.querySelector('.chat-stream[data-cache-session-id="' + cssEscapeIdent(sid) + '"]');
    if (cached && cached.parentNode) cached.remove();
    var idx = cachedSessionStreamOrder.indexOf(sid);
    if (idx >= 0) cachedSessionStreamOrder.splice(idx, 1);
}

function trimCachedSessionStreams() {
    if (!offscreenRoot) return;
    while (cachedSessionStreamOrder.length > SESSION_STREAM_CACHE_LIMIT) {
        var sid = cachedSessionStreamOrder.shift();
        var cached = offscreenRoot.querySelector('.chat-stream[data-cache-session-id="' + cssEscapeIdent(sid) + '"]');
        if (cached && cached.parentNode) cached.remove();
    }
}

function isCompleteLocalRunStream(sessionId, stream) {
    var run = getSessionRunState(sessionId);
    return !!(run && run.ctx && run.ctx.stream === stream
        && stream && stream.dataset
        && stream.dataset.partialBackgroundRun !== '1'
        && stream.dataset.sessionLoadFailed !== '1');
}

function stashVisibleStreamForSession(sessionId, opts) {
    opts = opts || {};
    var sid = String(sessionId || '');
    if (!sid || !offscreenRoot) return false;
    const el = getVisibleChatStream();
    if (!el || !el.parentNode) return false;
    /* A stream owned by this tab's active run is already the authoritative,
       gap-free UI projection even when no history request was needed (notably
       a newly-created session).  Certify it before moving it offscreen so a
       same-page switch can restore the live DOM instead of fetching snapshot. */
    if (opts.certifyLocalRun && isCompleteLocalRunStream(sid, el)) {
        el.dataset.sessionLoadOk = '1';
        delete el.dataset.sessionLoading;
    }
    if (!opts.force && el.dataset.sessionLoadOk !== '1') return false;
    if (el.dataset.sessionLoadFailed === '1') return false;
    discardCachedSessionStream(sid);
    el.remove();
    el.removeAttribute('id');
    el.removeAttribute('aria-label');
    el.classList.add('is-offscreen');
    el.setAttribute('data-cache-session-id', sid);
    offscreenRoot.appendChild(el);
    cacheOrderTouch(sid);
    trimCachedSessionStreams();
    return true;
}

function prepareStashLeaving(leavingId) {
    if (!leavingId) return;
    if (isSessionRunning(leavingId)) {
        stashVisibleStreamForSession(leavingId, { force: true, certifyLocalRun: true });
        insertNewEmptyChatStream();
    } else {
        if (!stashVisibleStreamForSession(leavingId)) ensureVisibleChatStreamSlot();
        insertNewEmptyChatStream();
    }
}

function restoreStreamForRunningSession(enteringId) {
    const run = getSessionRunState(enteringId);
    if (!run || !run.ctx || !run.ctx.stream) return false;
    const st = run.ctx.stream;
    if (!st.parentNode) return false;
    if (st.parentNode === chatContainer) return st.id === 'chat-stream';
    if (offscreenRoot && st.parentNode !== offscreenRoot) return false;
    const completeLocalRun = isCompleteLocalRunStream(enteringId, st);
    if (st.dataset && (st.dataset.partialBackgroundRun === '1'
        || (st.dataset.sessionLoadOk !== '1' && !completeLocalRun))) {
        abortSessionRun(enteringId, 'reattach-incomplete-background');
        if (st.parentNode) st.remove();
        return false;
    }
    if (completeLocalRun && st.dataset.sessionLoadOk !== '1') {
        st.dataset.sessionLoadOk = '1';
        delete st.dataset.sessionLoading;
    }
    const cur = getVisibleChatStream();
    if (cur && cur.parentNode === chatContainer) cur.remove();
    st.classList.remove('is-offscreen');
    st.removeAttribute('data-cache-session-id');
    st.id = 'chat-stream';
    st.setAttribute('aria-label', '消息');
    chatContainer.appendChild(st);
    cacheOrderTouch(enteringId);
    var restoredPaging = restoreHistoryPagingFromStream(st);
    if (restoredPaging) sessionHistoryPaging = restoredPaging;
    updateHistorySentinelVisibility();
    bindExistingLogs(st);
    return true;
}

function restoreCachedSessionStream(enteringId) {
    var sid = String(enteringId || '');
    if (!sid || !offscreenRoot) return false;
    var st = offscreenRoot.querySelector('.chat-stream[data-cache-session-id="' + cssEscapeIdent(sid) + '"]');
    if (!st || !st.parentNode) return false;
    if (st.dataset.sessionLoadOk !== '1' || st.dataset.sessionLoadFailed === '1') {
        discardCachedSessionStream(sid);
        return false;
    }
    const cur = getVisibleChatStream();
    if (cur && cur.parentNode === chatContainer) cur.remove();
    st.classList.remove('is-offscreen');
    st.removeAttribute('data-cache-session-id');
    st.id = 'chat-stream';
    st.setAttribute('aria-label', '消息');
    chatContainer.appendChild(st);
    cacheOrderTouch(sid);
    var restoredPaging = restoreHistoryPagingFromStream(st);
    if (restoredPaging) sessionHistoryPaging = restoredPaging;
    updateHistorySentinelVisibility();
    bindExistingLogs(st);
    return true;
}

function scrollCurrentRunningProcessToBottom(sessionId) {
    if (!sessionId || sessionId !== currentSessionId) return;
    var run = getSessionRunState(sessionId);
    var ctx = run && run.ctx;
    var stream = ctx && ctx.stream && ctx.stream.isConnected ? ctx.stream : getVisibleChatStream();
    if (!stream) return;
    var agg = ctx && ctx.currentProcessGroup && ctx.currentProcessGroup.isConnected
        ? ctx.currentProcessGroup
        : null;
    if (!agg) {
        var runningAggs = stream.querySelectorAll('.process-aggregate.is-running');
        agg = runningAggs.length ? runningAggs[runningAggs.length - 1] : null;
    }
    // A restored server-side run may not yet have rebuilt the local run
    // context or the is-running class. Its last process block still owns the
    // newest generated entries, so use it as the authoritative fallback.
    if (!agg) {
        var allAggs = stream.querySelectorAll('.process-aggregate');
        agg = allAggs.length ? allAggs[allAggs.length - 1] : null;
    }
    if (!agg) return;
    if (agg.classList.contains('is-collapsed')) {
        agg.classList.remove('is-collapsed');
        var top = agg.querySelector('.process-aggregate-top');
        if (top) top.setAttribute('aria-expanded', 'true');
    }
    var viewports = [
        agg.querySelector('.process-aggregate-body'),
        agg.querySelector('.process-aggregate-brief'),
    ].filter(function (el) { return !!el; });
    function pinBottom() {
        viewports.forEach(function (el) {
            setScrollTopImmediate(el, el.scrollHeight);
        });
    }
    requestAnimationFrame(function () {
        pinBottom();
        requestAnimationFrame(pinBottom);
    });
}

function restoreCachedSessionScrollPosition(sessionId) {
    if (!chatContainer || !sessionId) return;
    if (sessionId !== currentSessionId) return;
    var restoreEpoch = switchSessionEpoch;
    var running = isSessionRunning(sessionId)
        || (typeof isServerStreamActive === 'function' && isServerStreamActive(sessionId));
    var saved = (typeof getSavedScrollPosition === 'function') ? getSavedScrollPosition(sessionId) : null;
    if (running) {
        setScrollTopImmediate(chatContainer, chatContainer.scrollHeight);
        scrollCurrentRunningProcessToBottom(sessionId);
        streamChatNearBottom = true;
        streamProcNearBottom = true;
        liveAutoFollow = true;
    } else if (saved !== null && Number.isFinite(Number(saved))) {
        setScrollTopImmediate(chatContainer, Number(saved));
    } else {
        setScrollTopImmediate(chatContainer, chatContainer.scrollHeight);
    }
    refreshLiveAutoFollowPins();
    scheduleTocActiveUpdate();
    requestAnimationFrame(function () {
        if (sessionId !== currentSessionId || restoreEpoch !== switchSessionEpoch) return;
        if (running) {
            setScrollTopImmediate(chatContainer, chatContainer.scrollHeight);
            scrollCurrentRunningProcessToBottom(sessionId);
        }
        else if (saved !== null && Number.isFinite(Number(saved))) setScrollTopImmediate(chatContainer, Number(saved));
        refreshLiveAutoFollowPins();
        scheduleTocActiveUpdate();
    });
}

function markVisibleSessionStreamLoadState(sessionId, state) {
    var stream = getVisibleChatStream();
    if (!stream) return;
    stream.dataset.sessionId = String(sessionId || '');
    if (state === 'ok') {
        stream.dataset.sessionLoadOk = '1';
        delete stream.dataset.sessionLoadFailed;
        delete stream.dataset.sessionLoading;
    } else if (state === 'failed') {
        stream.dataset.sessionLoadFailed = '1';
        delete stream.dataset.sessionLoadOk;
        delete stream.dataset.sessionLoading;
        discardCachedSessionStream(sessionId);
    } else if (state === 'loading') {
        stream.dataset.sessionLoading = '1';
        delete stream.dataset.sessionLoadOk;
        delete stream.dataset.sessionLoadFailed;
    }
}

function appendLogVisible(msg, type) {
    if (!getVisibleChatStream()) ensureVisibleChatStreamSlot();
    const c = newDomContext(getVisibleChatStream());
    appendLog(c, msg, type, currentSessionId);
}

function newLlmState() {
    return {
        llmStreamReasoningIter: null,
        llmStreamResponseIter: null,
        llmStreamReasoningScroller: null,
        llmStreamResponseScroller: null,
        llmDeltaLastSeq: null,
        llmPendingReasoningDelta: '',
        llmPendingResponseDelta: '',
        llmDeltaFlushRaf: 0,
        llmRevealLastTs: 0,
        llmRevealCpsEma: 35,
        llmThinkTagMode: 'response',
        llmThinkTagCarry: '',
        llmThinkTagAllowLeading: true,
    };
}

function newDomContext(streamEl) {
    return {
        stream: streamEl,
        currentProcessGroup: null,
        lastUserEventIndex: -1,
        progressScrollers: {},
        progressStream: {},
        keyContextStreamFilter: { phase: 'seek', carry: '' },
        runStartedAt: null,
        reactGeneration: 0,
        _seenStreamDeltaKeys: new Set(),
        llm: newLlmState(),
    };
}

function resetKeyContextStreamFilter(ctx) {
    if (ctx) ctx.keyContextStreamFilter = { phase: 'seek', carry: '' };
}

/** 要点流式输出：隐藏 <analysis>…</analysis>，仅展示 <summary> 内正文 */
function extractKeyContextVisibleDelta(filter, delta) {
    if (!filter) return String(delta || '');
    filter.carry += String(delta || '');
    var out = '';
    var tagTail = 24;
    while (filter.carry.length > 0) {
        var lower = filter.carry.toLowerCase();
        if (filter.phase === 'seek') {
            var ai = lower.indexOf('<analysis');
            var si = lower.indexOf('<summary');
            if (ai >= 0 && (si < 0 || ai < si)) {
                if (ai > 0) out += filter.carry.slice(0, ai);
                filter.carry = filter.carry.slice(ai);
                filter.phase = 'in_analysis';
                continue;
            }
            if (si >= 0) {
                if (si > 0) out += filter.carry.slice(0, si);
                filter.carry = filter.carry.slice(si);
                filter.phase = 'in_summary';
                continue;
            }
            if (filter.carry.length > tagTail) {
                var safe = filter.carry.length - tagTail;
                out += filter.carry.slice(0, safe);
                filter.carry = filter.carry.slice(safe);
            }
            break;
        }
        if (filter.phase === 'in_analysis') {
            var ae = lower.indexOf('</analysis>');
            if (ae >= 0) {
                var aClose = filter.carry.slice(ae).match(/^<\/analysis\s*>/i);
                var aLen = aClose ? aClose[0].length : 11;
                filter.carry = filter.carry.slice(ae + aLen);
                filter.phase = 'seek';
                continue;
            }
            filter.carry = '';
            break;
        }
        if (filter.phase === 'in_summary') {
            var se = lower.indexOf('</summary>');
            var chunk = se >= 0 ? filter.carry.slice(0, se) : filter.carry;
            chunk = chunk.replace(/^<summary[^>]*>\s*/i, '');
            out += chunk;
            if (se >= 0) {
                var sClose = filter.carry.slice(se).match(/^<\/summary\s*>/i);
                var sLen = sClose ? sClose[0].length : 10;
                filter.carry = filter.carry.slice(se + sLen);
                filter.phase = 'done';
            } else {
                filter.carry = '';
            }
            break;
        }
        if (filter.phase === 'done') {
            filter.carry = '';
            break;
        }
        break;
    }
    return out;
}

function appendKeyContextStreamDelta(ctx, delta, runSessionId) {
    if (!ctx || !delta) return;
    if (!ctx.keyContextStreamFilter) resetKeyContextStreamFilter(ctx);
    var vis = extractKeyContextVisibleDelta(ctx.keyContextStreamFilter, delta);
    if (vis) appendProgressStreamDelta(ctx, vis, 'key-context', runSessionId);
}

function isSessionRunning(sessionId) {
    return selectIsSessionRunning(sessionId);
}

function syncDisconnectedProcessGroups() {
    sessionStore.runsBySession.forEach(function (run, sid) {
        const c = run && run.ctx;
        if (c && c.currentProcessGroup && !c.currentProcessGroup.isConnected) c.currentProcessGroup = null;
    });
}

function finalizeLlmStreamChunks(ctx) {
    if (!ctx) return;
    flushLlmDeltaText(ctx);
    queryFeedChunksInCtx(ctx, '.feed-chunk.is-streaming').forEach(function (ch) {
        ch.classList.remove('is-streaming');
        var row = ch.closest ? ch.closest('.feed-item') : null;
        if (row && row.classList.contains('feed--llm')) autoCollapseLlmReasoningRow(row);
        scheduleFeedChunkOverflowRefresh(ch);
    });
    if (ctx.llm) {
        const l = ctx.llm;
        l.llmStreamReasoningIter = null;
        l.llmStreamResponseIter = null;
        l.llmStreamReasoningScroller = null;
        l.llmStreamResponseScroller = null;
        l.llmDeltaLastSeq = null;
        l.llmRevealLastTs = 0;
        l.llmRevealCpsEma = 35;
        l.llmThinkTagMode = 'response';
        l.llmThinkTagCarry = '';
        l.llmThinkTagAllowLeading = true;
    }
    var bodies = [];
    if (ctx.currentProcessGroup && !isSubagentStreamCtx(ctx)) {
        var mainBody = ctx.currentProcessGroup.querySelector('.process-aggregate-body');
        if (mainBody) bodies.push(mainBody);
    }
    if (ctx._subagentTurnProcess && ctx._subagentTurnProcess.isConnected) {
        bodies.push(ctx._subagentTurnProcess);
    }
    bodies.forEach(function (body) {
        body.querySelectorAll('.feed-item.feed--llm, .feed-item.feed--llm2').forEach(function (el) {
            var sc = el.querySelector('.feed-chunk-scroller');
            var ch = el.querySelector('.feed-chunk');
            if (sc) {
                var norm = trimSurroundingBlankLines(sc.textContent || '');
                sc.textContent = truncateLogTextForUi(norm);
                if (ch) {
                    refreshFeedChunkOverflow(ch);
                    requestAnimationFrame(function () { refreshFeedChunkOverflow(ch); });
                }
            }
            if (!getFeedItemText(el).trim()) el.remove();
        });
    });
}

function discardLlmStreamChunks(ctx, ev) {
    if (!ctx) return;
    ev = ev || {};
    if (ev.cleanup_scope === 'none') {
        finalizeLlmStreamChunks(ctx);
        return;
    }
    if (ctx.llm) {
        const l = ctx.llm;
        if (l.llmDeltaFlushRaf) {
            cancelAnimationFrame(l.llmDeltaFlushRaf);
            l.llmDeltaFlushRaf = 0;
        }
        l.llmPendingReasoningDelta = '';
        l.llmPendingResponseDelta = '';
        l.llmStreamReasoningIter = null;
        l.llmStreamResponseIter = null;
        l.llmStreamReasoningScroller = null;
        l.llmStreamResponseScroller = null;
        l.llmDeltaLastSeq = null;
        l.llmRevealLastTs = 0;
        l.llmRevealCpsEma = 35;
        l.llmThinkTagMode = 'response';
        l.llmThinkTagCarry = '';
        l.llmThinkTagAllowLeading = true;
    }
    var bodies = [];
    if (ctx.currentProcessGroup && !isSubagentStreamCtx(ctx)) {
        var mainBody = ctx.currentProcessGroup.querySelector('.process-aggregate-body');
        if (mainBody) bodies.push(mainBody);
    }
    if (ctx._subagentTurnProcess && ctx._subagentTurnProcess.isConnected) {
        bodies.push(ctx._subagentTurnProcess);
    }
    var reactIter = ev && ev.react_iter != null && Number.isFinite(Number(ev.react_iter))
        ? String(Math.max(1, Math.floor(Number(ev.react_iter))))
        : '';
    var runId = String((ev && (ev.run_id || ev.runId)) || '');
    var hasScopedAbort = !!(reactIter || runId || (ev && ev.react_generation != null));
    var reactGeneration = ev && ev.react_generation != null && Number.isFinite(Number(ev.react_generation))
        ? String(Math.max(0, Math.floor(Number(ev.react_generation))))
        : (hasScopedAbort ? String(reactGenerationForContext(ctx)) : null);
    function matchesAbortScope(el) {
        if (!el) return false;
        if (reactIter && String(el.getAttribute('data-react-iter') || '') !== reactIter) return false;
        if (reactGeneration !== null && String(el.getAttribute('data-react-generation') || '0') !== reactGeneration) return false;
        var rowRunId = String(el.getAttribute('data-run-id') || '');
        if (runId && rowRunId && rowRunId !== runId) return false;
        return true;
    }
    bodies.forEach(function (body) {
        body.querySelectorAll('.feed-item[data-llm-live-row="1"]').forEach(function (el) {
            if (matchesAbortScope(el)) el.remove();
        });
        body.querySelectorAll(
            '.feed-item.feed--tool[data-tool-draft-key], '
            + '.feed-item.feed--tool[data-tool-pending="1"]'
        ).forEach(function (el) {
            if (matchesAbortScope(el)) el.remove();
        });
    });
}

/** Retain untrimmed source separately from its bounded UI projection. */
function writeLlmStreamText(scroller, raw, part) {
    if (!scroller) return;
    scroller._llmRawText = String(raw || '');
    var row = scroller.closest ? scroller.closest('.feed-item') : null;
    if (part === 'response' && row) row._processBriefRawText = scroller._llmRawText;
    var displayed = truncateLogTextForUi(trimSurroundingBlankLines(scroller._llmRawText));
    var node = scroller.firstChild;
    var previous = node && node === scroller._llmTextNode
        ? scroller._llmRenderedText
        : (scroller.textContent || '');
    if (displayed !== previous) {
        if (node && node === scroller.lastChild && node.nodeType === 3
            && displayed.indexOf(previous) === 0) {
            node.appendData(displayed.slice(previous.length));
            if (typeof uiPerformance !== 'undefined') uiPerformance.count(currentSessionId, 'text.nodeAppends');
        } else {
            scroller.textContent = displayed;
            if (typeof uiPerformance !== 'undefined') uiPerformance.count(currentSessionId, 'text.nodeReplacements');
        }
    }
    scroller._llmTextNode = scroller.firstChild;
    scroller._llmRenderedText = displayed;
}

function appendLlmRevealedText(scroller, segment, part) {
    var row = scroller.closest ? scroller.closest('.feed-item') : null;
    var head = typeof scroller._llmRawText === 'string' ? scroller._llmRawText
        : (part === 'response' && row && typeof row._processBriefRawText === 'string'
            ? row._processBriefRawText : (scroller.textContent || ''));
    writeLlmStreamText(scroller, head + segment, part);
}

function flushLlmDeltaText(ctx, opts) {
    if (!ctx || !ctx.llm) return;
    opts = opts || {};
    const l = ctx.llm;
    if (typeof flushThinkTagCarry === 'function') flushThinkTagCarry(ctx);
    var smoothCommit = opts.smooth === true && isSmoothStreamActive();
    if (!smoothCommit && l.llmDeltaFlushRaf) {
        cancelAnimationFrame(l.llmDeltaFlushRaf);
        l.llmDeltaFlushRaf = 0;
    }
    var revealedChars = 0;
    if (l.llmPendingReasoningDelta && l.llmStreamReasoningScroller) {
        var reasoningPending = String(l.llmPendingReasoningDelta || '');
        var reasoningTake = smoothCommit
            ? takeSmoothTextPrefix(
                reasoningPending,
                computeSmoothRevealCount(reasoningPending.length, opts.dtMs || 16.67)
            )
            : { segment: reasoningPending, rest: '', count: reasoningPending.length };
        appendLlmRevealedText(l.llmStreamReasoningScroller, reasoningTake.segment, 'reasoning');
        l.llmPendingReasoningDelta = reasoningTake.rest;
        revealedChars += reasoningTake.count;
    } else if (l.llmPendingReasoningDelta && !l.llmStreamReasoningScroller && !smoothCommit) {
        l.llmPendingReasoningDelta = '';
    }
    if (l.llmPendingResponseDelta && l.llmStreamResponseScroller) {
        var responsePending = String(l.llmPendingResponseDelta || '');
        var responseTake = smoothCommit
            ? takeSmoothTextPrefix(
                responsePending,
                computeSmoothRevealCount(responsePending.length, opts.dtMs || 16.67)
            )
            : { segment: responsePending, rest: '', count: responsePending.length };
        appendLlmRevealedText(l.llmStreamResponseScroller, responseTake.segment, 'response');
        l.llmPendingResponseDelta = responseTake.rest;
        revealedChars += responseTake.count;
    } else if (l.llmPendingResponseDelta && !l.llmStreamResponseScroller && !smoothCommit) {
        l.llmPendingResponseDelta = '';
    }
    return revealedChars;
}

function scheduleLlmDeltaFlush(ctx, runSessionId) {
    const l = ctx.llm;
    if (!l || l.llmDeltaFlushRaf) return;
    l.llmDeltaFlushRaf = requestAnimationFrame(function (now) {
        l.llmDeltaFlushRaf = 0;
        var flushStartedAt = performance.now();
        if (!isSmoothStreamActive()) {
            flushLlmDeltaText(ctx);
            followStreamProcessScroll(ctx, runSessionId, 'text');
            return;
        }
        var dtMs = l.llmRevealLastTs > 0
            ? smoothStreamClamp(now - l.llmRevealLastTs, 1, 120)
            : SMOOTH_STREAM_CONFIG.referenceFrameMs;
        if (l.llmRevealLastTs > 0 && typeof uiPerformance !== 'undefined') {
            uiPerformance.sample(runSessionId, 'stream.frameGap', now - l.llmRevealLastTs);
        }
        l.llmRevealLastTs = now;
        var revealed = flushLlmDeltaText(ctx, { smooth: true, dtMs: dtMs }) || 0;
        if (typeof uiPerformance !== 'undefined') {
            uiPerformance.sample(runSessionId, 'stream.flush', performance.now() - flushStartedAt);
            uiPerformance.count(runSessionId, 'stream.revealedCodePoints', revealed);
        }
        if (revealed > 0 && dtMs > 0) {
            var instantCps = revealed * 1000 / dtMs;
            l.llmRevealCpsEma = l.llmRevealCpsEma * 0.92 + instantCps * 0.08;
        }
        followStreamProcessScroll(ctx, runSessionId, 'text');
        if (l.llmPendingReasoningDelta || l.llmPendingResponseDelta) {
            scheduleLlmDeltaFlush(ctx, runSessionId);
        } else {
            l.llmRevealLastTs = 0;
        }
    });
}

function resetLlmState(ctx) {
    if (!ctx || !ctx.llm) return;
    flushLlmDeltaText(ctx);
    const l = ctx.llm;
    l.llmStreamReasoningIter = null;
    l.llmStreamResponseIter = null;
    l.llmStreamReasoningScroller = null;
    l.llmStreamResponseScroller = null;
    l.llmDeltaLastSeq = null;
    l.llmRevealLastTs = 0;
    l.llmRevealCpsEma = 35;
    l.llmThinkTagMode = 'response';
    l.llmThinkTagCarry = '';
    l.llmThinkTagAllowLeading = true;
}

function showCopyFeedback() {
    const t = document.getElementById('copy-toast');
    if (!t) return;
    t.classList.add('is-on');
    if (t._copyTm) clearTimeout(t._copyTm);
    t._copyTm = setTimeout(function () { t.classList.remove('is-on'); }, 1500);
}

function showOpenFileFeedback(msg) {
    var t = document.getElementById('copy-toast');
    if (!t) return;
    var prev = t.getAttribute('data-default-msg') || t.textContent || '已复制';
    if (!t.getAttribute('data-default-msg')) t.setAttribute('data-default-msg', prev);
    t.textContent = msg || '已请求打开';
    t.classList.add('is-on');
    if (t._openFileTm) clearTimeout(t._openFileTm);
    t._openFileTm = setTimeout(function () {
        t.classList.remove('is-on');
        t.textContent = t.getAttribute('data-default-msg') || '已复制';
    }, 2200);
}

(function initWorkspaceFileOpenDelegation() {
    if (document.body.dataset.workspaceFileOpenBound) return;
    document.body.dataset.workspaceFileOpenBound = '1';
    document.body.addEventListener('click', function (ev) {
        var el = ev.target;
        if (!el || !el.closest) return;
        var a = el.closest('a.msg-link-workspace-open');
        if (!a) return;
        ev.preventDefault();
        var rel = a.getAttribute('data-workspace-open') || '';
        var controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
        var timer = controller ? setTimeout(function () { controller.abort(); }, 8000) : null;
        fetch('/api/open-workspace-file?rel=' + encodeURIComponent(rel), controller ? { signal: controller.signal } : undefined)
            .then(function (r) {
                if (timer) clearTimeout(timer);
                return r.json().catch(function () { return { ok: false, error: '响应异常' }; });
            })
            .then(function (j) {
                if (j && j.ok) showOpenFileFeedback('已调用系统打开文件');
                else showOpenFileFeedback((j && j.error) ? ('无法打开：' + j.error) : '无法打开文件');
            })
            .catch(function () { showOpenFileFeedback('无法连接服务'); });
    });
})();

let rewriteUndoState = null;
/** 改写待发送：仅在点击发送时调用截断；取消则丢弃 */
let pendingRewriteTruncate = null;
function hideRewriteUndoToast() {
    const t = document.getElementById('rewrite-undo-toast');
    if (t) {
        t.classList.remove('is-on');
        const btn = t.querySelector('.rewrite-undo-btn');
        if (btn) btn.textContent = '撤销';
    }
    rewriteUndoState = null;
}
function showRewriteUndoToast(type, data) {
    const t = document.getElementById('rewrite-undo-toast');
    const msgEl = t && t.querySelector('.rewrite-undo-msg');
    const btn = t && t.querySelector('.rewrite-undo-btn');
    if (!t || !msgEl) return;
    rewriteUndoState = { type: type, data: data };
    if (type === 'rewrite_pending') {
        msgEl.textContent = '改写待生效：发送消息后才会截断历史并发送；点此取消改写。';
        if (btn) btn.textContent = '取消改写';
    } else if (type === 'tail') {
        msgEl.textContent = '已截断历史，可撤销恢复';
        if (btn) btn.textContent = '撤销';
    } else {
        msgEl.textContent = '已填入输入框，可撤销';
        if (btn) btn.textContent = '撤销';
    }
    t.classList.add('is-on');
}

function smoothScrollBy(el, dy) {
    if (!el || !dy) return;
    const bMax = Math.max(0, el.scrollHeight - el.clientHeight);
    const start = el.scrollTop;
    const target = Math.max(0, Math.min(bMax, start + dy));
    const dist = target - start;
    if (Math.abs(dist) < 0.5) return;
    const frames = 3;
    let f = 0;
    function step() {
        f += 1;
        const t = f / frames;
        const ease = 1 - Math.pow(1 - t, 2);
        el.scrollTop = start + dist * ease;
        if (f < frames) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
}

function isNearBottom(el, thresholdPx) {
    if (!el) return true;
    const th = (thresholdPx == null) ? 56 : thresholdPx;
    return (el.scrollHeight - el.clientHeight - el.scrollTop) <= th;
}

async function getUiEventCount(sessionId, opts) {
    opts = opts || {};
    const sid = sessionId != null ? sessionId : currentSessionId;
    if (!sid) return 0;
    if (
        opts.preferCache
        && typeof uiEventCountCache !== 'undefined'
        && typeof uiEventCountCache.has === 'function'
        && uiEventCountCache.has(sid)
        && (typeof uiEventCountCache.isFresh !== 'function' || uiEventCountCache.isFresh(sid, opts.maxAgeMs))
    ) {
        return uiEventCountCache.get(sid);
    }
    try {
        const controller = new AbortController();
        const externalSignal = opts.signal;
        const abortFromExternal = function () { controller.abort(); };
        if (externalSignal) {
            if (externalSignal.aborted) controller.abort();
            else externalSignal.addEventListener('abort', abortFromExternal, { once: true });
        }
        const timer = setTimeout(function () { controller.abort(); }, Math.max(250, Number(opts.timeoutMs) || 5000));
        let r;
        try {
            r = await fetch('/sessions/' + encodeURIComponent(sid) + '/messages/count', {
                signal: controller.signal
            });
        } finally {
            clearTimeout(timer);
            if (externalSignal) externalSignal.removeEventListener('abort', abortFromExternal);
        }
        if (!r.ok) return 0;
        const j = await r.json();
        const count = (j && typeof j.count === 'number') ? j.count : 0;
        if (typeof uiEventCountCache !== 'undefined') uiEventCountCache.updateFromServer(sid, count);
        return count;
    } catch (e) { return 0; }
}

function loadUnreadFromStorage() {
    try {
        const raw = localStorage.getItem(LS_SESSION_UNREAD);
        if (!raw) return;
        const arr = JSON.parse(raw);
        if (!Array.isArray(arr)) return;
        arr.forEach(function (id) { sessionUnreadComplete.add(String(id)); });
    } catch (e) { /* ignore */ }
}

function persistSessionUnread() {
    try {
        localStorage.setItem(LS_SESSION_UNREAD, JSON.stringify([...sessionUnreadComplete]));
    } catch (e) { /* ignore */ }
}

function stashInputDraft(sessionId) {
    if (!messageInput) return;
    const draftKey = sessionId ? String(sessionId) : NEW_SESSION_DRAFT_KEY;
    draftBySession[draftKey] = messageInput.value;
    persistInputDraft(sessionId, messageInput.value);
}

function restoreInputDraft(sessionId) {
    if (!messageInput) return;
    const draftKey = sessionId ? String(sessionId) : NEW_SESSION_DRAFT_KEY;
    const v = Object.prototype.hasOwnProperty.call(draftBySession, draftKey)
        ? draftBySession[draftKey]
        : readStoredInputDraft(sessionId);
    messageInput.value = v != null ? String(v) : '';
    rewriteInputWorkspacePaths();
    autoResizeTextarea();
}

function inputDraftStorageKey(sessionId) {
    const draftKey = sessionId ? String(sessionId) : NEW_SESSION_DRAFT_KEY;
    return LS_INPUT_DRAFT_PREFIX + draftKey;
}

function persistInputDraft(sessionId, value) {
    const draftKey = sessionId ? String(sessionId) : NEW_SESSION_DRAFT_KEY;
    const text = String(value || '');
    draftBySession[draftKey] = text;
    try {
        const key = inputDraftStorageKey(sessionId);
        if (text) localStorage.setItem(key, text);
        else localStorage.removeItem(key);
    } catch (e) { /* ignore */ }
    if (typeof syncSessionDraftBadges === 'function') syncSessionDraftBadges(sessionId);
}

function readStoredInputDraft(sessionId) {
    try {
        return localStorage.getItem(inputDraftStorageKey(sessionId)) || '';
    } catch (e) {
        return '';
    }
}

function removeStoredInputDraft(sessionId) {
    const draftKey = sessionId ? String(sessionId) : NEW_SESSION_DRAFT_KEY;
    delete draftBySession[draftKey];
    try { localStorage.removeItem(inputDraftStorageKey(sessionId)); } catch (e) { /* ignore */ }
    if (typeof syncSessionDraftBadges === 'function') syncSessionDraftBadges(sessionId);
}

function clearStreamPoll() {
    if (streamPollTimer) {
        clearInterval(streamPollTimer);
        streamPollTimer = null;
    }
}

function maybeStartStreamPollForSession(sid, opts) {
    opts = opts || {};
    clearStreamPoll();
    if (!sid) return;
    if (!isSessionRunning(sid)) return;
    if (!getSessionRunState(sid) && typeof attachSessionEventStream === 'function') {
        void attachSessionEventStream(sid, { skipInitialLoad: !!opts.skipInitialLoad });
    }
    let pollCount = 0;
    let MAX_POLL_COUNT = 20;
    streamPollTimer = setInterval(function () {
        (async function () {
            if (currentSessionId !== sid) {
                clearStreamPoll();
                return;
            }
            pollCount += 1;
            if (typeof reconcileRunStateFromServer === 'function') {
                await reconcileRunStateFromServer({ silent: true });
            }
            const still = typeof isServerStreamActive === 'function'
                ? isServerStreamActive(sid)
                : isSessionRunning(sid);
            if (!still || pollCount >= MAX_POLL_COUNT) {
                clearStreamPoll();
                await loadSessions();
                syncSessionListIndicatorClasses();
                setSendButtonState();
                return;
            }
            if (currentSessionId === sid && document.visibilityState === 'visible') {
                syncSessionListIndicatorClasses();
                setSendButtonState();
            }
        })();
    }, 15000);
}

async function scrollToUserTurnOrLoadOlder(eventIndex, opts) {
    opts = opts || {};
    var ei = Number(eventIndex);
    if (!Number.isFinite(ei)) return false;
    var silent = !!opts.silent;
    var scrollBehavior = opts.instant ? 'auto' : 'smooth';
    var viewportOffset = Number(opts.viewportOffset);
    var hasViewportOffset = Number.isFinite(viewportOffset);
    var liveHistoryOwner = isSessionRunning(currentSessionId)
        || (typeof isServerStreamActive === 'function' && isServerStreamActive(currentSessionId));
    var allowFullReload = opts.allowFullReload !== false && !silent && !liveHistoryOwner;
    var maxOlderLoads = Number.isFinite(Number(opts.maxOlderLoads))
        ? Math.max(0, Number(opts.maxOlderLoads))
        : 120;
    function setTocJumpLoading(active) {
        var list = document.getElementById('chat-toc-list');
        var link = list && list.querySelector('a[data-event-index="' + ei + '"]');
        if (!link) return;
        link.classList.toggle('is-loading', !!active);
        if (active) link.setAttribute('aria-busy', 'true');
        else link.removeAttribute('aria-busy');
    }
    function findWrap() {
        var stream = getVisibleChatStream();
        if (!stream) return null;
        return stream.querySelector('.msg-wrap--user[data-event-index="' + ei + '"]')
            || stream.querySelector('#user-msg-' + ei);
    }
    function scrollToWrap(wrap) {
        if (!wrap) return;
        if (!hasViewportOffset || !chatContainer) {
            wrap.scrollIntoView({ behavior: scrollBehavior, block: 'start' });
            return;
        }
        var viewportRect = chatContainer.getBoundingClientRect();
        var wrapRect = wrap.getBoundingClientRect();
        var maxTop = Math.max(0, chatContainer.scrollHeight - chatContainer.clientHeight);
        var targetTop = chatContainer.scrollTop + wrapRect.top - viewportRect.top - viewportOffset;
        targetTop = Math.max(0, Math.min(maxTop, targetTop));
        if (scrollBehavior === 'smooth' && typeof chatContainer.scrollTo === 'function') {
            chatContainer.scrollTo({ top: targetTop, behavior: 'smooth' });
        } else {
            setScrollTopImmediate(chatContainer, targetTop);
        }
    }
    async function loadFullHistoryForTarget(sid) {
        if (!allowFullReload) return;
        if (sid !== currentSessionId || typeof loadSessionMessages !== 'function') return;
        try {
            await loadSessionMessages(sid, 'saved-or-bottom', { full: true });
        } catch (e) {
            console.error('reload full history for toc target failed:', e);
        }
    }
    setTocJumpLoading(true);
    try {
        var wrap = findWrap();
        if (wrap) {
            scrollToWrap(wrap);
            return true;
        }
        var sid = currentSessionId;
        if (allowFullReload) {
            var loadedTargetWindow = await loadHistoryWindowAroundEventIndex(sid, ei, { turns: 50 });
            if (loadedTargetWindow && sid === currentSessionId) {
                wrap = findWrap();
                if (wrap) {
                    scrollToWrap(wrap);
                    return true;
                }
            }
        }
        var safety = 0;
        var olderLoads = 0;
        var pagingCoveredTarget = false;
        while (sid === currentSessionId && safety < 120) {
            safety += 1;
            wrap = findWrap();
            if (wrap) {
                scrollToWrap(wrap);
                return true;
            }
            var ph = sessionHistoryPaging;
            if ((!ph || ph.sessionId !== sid) && getVisibleChatStream()) {
                ph = restoreHistoryPagingFromStream(getVisibleChatStream());
                if (ph) sessionHistoryPaging = ph;
            }
            if (!ph || ph.sessionId !== sid) {
                await loadFullHistoryForTarget(sid);
                break;
            }
            if (ei >= ph.range_start) {
                pagingCoveredTarget = true;
                break;
            }
            if (!ph.has_older) break;
            if (olderLoads >= maxOlderLoads) break;
            while (historyOlderLoading && currentSessionId === sid) {
                await new Promise(function (r) { setTimeout(r, 40); });
            }
            olderLoads += 1;
            await loadOlderHistoryChunk({ keepTocStable: true, turns: 50 });
        }
        wrap = findWrap();
        if (wrap) {
            scrollToWrap(wrap);
            return true;
        }
        if (allowFullReload && sid === currentSessionId && pagingCoveredTarget) {
            await loadFullHistoryForTarget(sid);
            if (sid !== currentSessionId) return false;
            wrap = findWrap();
            if (wrap) {
                wrap.scrollIntoView({ behavior: scrollBehavior, block: 'start' });
                return true;
            }
            rebuildToc();
        }
        if (wrap) wrap.scrollIntoView({ behavior: scrollBehavior, block: 'start' });
        else if (!silent) {
            showUiAlert({
                title: '无法定位该条',
                message: '未能加载到对应的用户提问（可能索引不一致）。可刷新页面或使用「更早 ' + HISTORY_DIALOGUES_PER_PAGE + ' 轮对话」手动分页。',
                showCancel: false,
                confirmText: '知道了',
            });
        }
        return !!wrap;
    } finally {
        setTocJumpLoading(false);
    }
}
