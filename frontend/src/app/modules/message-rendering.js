function removeMessagesFromNode(startWrap) {
    const stream = getVisibleChatStream() || chatContainer;
    if (!stream) return;
    const kids = Array.from(stream.children);
    const i = kids.indexOf(startWrap);
    if (i < 0) return;
    for (let j = kids.length - 1; j >= i; j--) kids[j].remove();
    syncDisconnectedProcessGroups();
}

async function truncateSessionOnServer(beforeIndex) {
    if (!currentSessionId) return { ok: false, error: 'no_session' };
    if (!Number.isFinite(Number(beforeIndex)) || Number(beforeIndex) < 0) {
        return { ok: false, error: 'invalid_before_index' };
    }
    const url = '/sessions/' + encodeURIComponent(currentSessionId) + '/truncate'
        + '?before_index=' + encodeURIComponent(String(beforeIndex));
    try {
        const r = await fetch(url, { method: 'POST' });
        const j = await r.json().catch(function () { return {}; });
        return { ok: r.ok, error: (j && j.error) ? String(j.error) : '' };
    } catch (e) {
        return { ok: false, error: (e && e.message) || String(e) };
    }
}

function describeServerSyncFailure(res, fallback) {
    var base = fallback || '无法同步服务器。';
    var err = res && res.error ? String(res.error).trim() : '';
    if (!err) return base;
    var friendly = err;
    if (err === 'no_session') friendly = '当前没有选中的会话。';
    else if (err === 'invalid_before_index' || err === 'invalid before_index') friendly = '消息定位索引无效，可能需要刷新当前会话。';
    else if (err === 'refuse empty truncation') friendly = '服务端拒绝清空整个会话。';
    else if (err === 'truncation failed') friendly = '服务端裁剪历史失败，可能是历史索引已变化或会话文件暂时不一致。';
    return base + '\n原因：' + friendly;
}

function hasPreviousUserMessageBefore(wrap) {
    var node = wrap ? wrap.previousElementSibling : null;
    while (node) {
        if (node.classList && node.classList.contains('msg-wrap--user')) return true;
        node = node.previousElementSibling;
    }
    return false;
}

async function branchSessionOnServer(beforeIndex) {
    if (!currentSessionId) return { ok: false, error: 'no_session' };
    const url = '/sessions/' + encodeURIComponent(currentSessionId) + '/branch'
        + '?before_index=' + encodeURIComponent(String(beforeIndex));
    try {
        const r = await fetch(url, { method: 'POST' });
        const j = await r.json().catch(function () { return {}; });
        return {
            ok: r.ok,
            session_id: j && j.session_id,
            name: j && j.name,
            error: (j && j.error) ? String(j.error) : '',
        };
    } catch (e) {
        return { ok: false, error: (e && e.message) || String(e) };
    }
}

function normalizeBranchFinalText(text) {
    return String(text || '').replace(/\s+/g, ' ').trim();
}

function branchFinalTextMatches(eventContent, expectedText) {
    var a = normalizeBranchFinalText(eventContent);
    var b = normalizeBranchFinalText(expectedText);
    if (!a || !b) return false;
    if (a === b) return true;
    if (a.length > 80 && b.length > 80) {
        return a.indexOf(b.slice(0, 80)) >= 0 || b.indexOf(a.slice(0, 80)) >= 0;
    }
    return false;
}

async function waitForBranchFinalPersisted(sessionId, beforeIndex, expectedText) {
    if (!sessionId || !Number.isFinite(beforeIndex) || beforeIndex <= 0) {
        return { ready: true, beforeIndex: beforeIndex };
    }
    var deadline = Date.now() + 2600;
    while (Date.now() < deadline) {
        try {
            var url = '/sessions/' + encodeURIComponent(sessionId)
                + '/messages?limit=1&before_index=' + encodeURIComponent(String(beforeIndex));
            var r = await fetch(url);
            var j = await r.json().catch(function () { return null; });
            var events = Array.isArray(j) ? j : (j && Array.isArray(j.events) ? j.events : []);
            if (events.length && events[events.length - 1] && events[events.length - 1].type === 'final') {
                return { ready: true, beforeIndex: beforeIndex };
            }
            var recentUrl = '/sessions/' + encodeURIComponent(sessionId) + '/messages?limit=80';
            var rr = await fetch(recentUrl);
            var jj = await rr.json().catch(function () { return null; });
            var recent = Array.isArray(jj) ? jj : (jj && Array.isArray(jj.events) ? jj.events : []);
            var base = jj && typeof jj.range_start === 'number' ? jj.range_start : 0;
            for (var i = recent.length - 1; i >= 0; i -= 1) {
                var ev = recent[i];
                if (!ev || ev.type !== 'final') continue;
                if (branchFinalTextMatches(ev.content, expectedText)) {
                    return { ready: true, beforeIndex: base + i + 1 };
                }
            }
        } catch (e) { /* retry */ }
        await new Promise(function (resolve) { setTimeout(resolve, 180); });
    }
    return { ready: false, beforeIndex: beforeIndex };
}

function onMessageToolbarClick(wrap, role, act) {
    const msg = wrap.querySelector('.message');
    const plain = msg ? (msg.innerText || '') : '';
    const tf = wrap.dataset.truncateFrom;
    const before = tf !== undefined && tf !== '' ? parseInt(tf, 10) : NaN;
    if ((act === 'delete' || act === 'rewrite') && isSessionRunning(currentSessionId)) {
        showUiAlert({
            title: '生成中不可操作',
            message: '当前会话仍在生成。请等待完成或停止后再修改历史。',
            variant: 'warning',
        });
        return;
    }
    if (act === 'copy') {
        const raw = messageRawMarkdown.get(wrap);
        const toCopy = raw !== undefined ? String(raw) : plain;
        const done = function () { showCopyFeedback(); };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(toCopy).then(done).catch(function () {
                try {
                    const ta = document.createElement('textarea');
                    ta.value = toCopy;
                    ta.setAttribute('readonly', 'readonly');
                    document.body.appendChild(ta);
                    ta.select();
                    document.execCommand('copy');
                    document.body.removeChild(ta);
                    done();
                } catch (e) { /* ignore */ }
            });
        }
        return;
    }
    if (act === 'delete') {
        if (!Number.isFinite(before) || before < 0 || (before === 0 && hasPreviousUserMessageBefore(wrap))) {
            if (Number.isFinite(before) && (before < 0 || (before === 0 && hasPreviousUserMessageBefore(wrap)))) {
                showUiAlert({
                    title: '无法删除该条',
                    message: '消息索引异常，已阻止清空整个会话。请刷新后再试。',
                    variant: 'error'
                });
                return;
            }
            removeMessagesFromNode(wrap);
            syncDisconnectedProcessGroups();
            rebuildToc();
            return;
        }
        openUiModal({
            title: '删除消息',
            subtitle: '将同步到服务器',
            message: '确定删除本条及之后的所有对话内容吗？',
            danger: true,
            confirmText: '删除',
            cancelText: '取消',
        }).then(function (ok) {
            if (!ok) return;
            truncateSessionOnServer(before).then(function (res) {
                if (!res || !res.ok) {
                    showUiAlert({
                        title: '同步失败',
                        message: describeServerSyncFailure(res, '删除未生效。'),
                        variant: 'error'
                    });
                    return;
                }
                removeMessagesFromNode(wrap);
                syncDisconnectedProcessGroups();
                rebuildToc();
                scheduleContextTokensAfterPaint(currentSessionId);
            });
        });
        return;
    }
    if (act === 'rewrite' && role === 'user') {
        const raw = messageRawMarkdown.get(wrap);
        const toFill = raw !== undefined ? String(raw) : plain;
        if (Number.isFinite(before) && before === 0 && hasPreviousUserMessageBefore(wrap)) {
            showUiAlert({
                title: '无法改写该条',
                message: '消息索引异常，已阻止从错误位置清空会话。请刷新后再试。',
                variant: 'error'
            });
            return;
        }
        if (!Number.isFinite(before)) {
            const prev = messageInput.value;
            messageInput.value = toFill;
            rewriteInputWorkspacePaths();
            autoResizeTextarea();
            messageInput.focus();
            showRewriteUndoToast('input', { prev: prev });
            return;
        }
        pendingRewriteTruncate = {
            sessionId: currentSessionId,
            before: before,
            prevInput: messageInput.value,
        };
        messageInput.value = toFill;
        rewriteInputWorkspacePaths();
        autoResizeTextarea();
        messageInput.focus();
        showRewriteUndoToast('rewrite_pending', pendingRewriteTruncate);
        return;
    }
    if (act === 'branch' && role === 'assistant') {
        const eiRaw = wrap.dataset.eventIndex;
        const eventIdx = eiRaw !== undefined && eiRaw !== '' ? parseInt(eiRaw, 10) : NaN;
        if (!Number.isFinite(eventIdx) || eventIdx < 0) {
            showUiAlert({
                title: '无法分支',
                message: '该回答尚未与服务器同步，请刷新页面后重试。',
                variant: 'error',
            });
            return;
        }
        const branchBefore = eventIdx + 1;
        openUiModal({
            title: '创建分支会话',
            subtitle: '原会话不会被修改',
            message: '将在当前回答之后创建独立分支会话。分支点之前的内容与原会话相同，可在分支中继续提问且不影响原会话。',
            confirmText: '创建分支',
            cancelText: '取消',
        }).then(function (ok) {
            if (!ok) return;
            (async function () {
                var rawExpected = messageRawMarkdown.get(wrap);
                var expectedText = rawExpected !== undefined ? String(rawExpected) : plain;
                var ready = await waitForBranchFinalPersisted(currentSessionId, branchBefore, expectedText);
                if (!ready || !ready.ready) {
                    showUiAlert({
                        title: '分支稍后再试',
                        message: '最终回答仍在写入会话记录，请稍等一两秒后再次分支。',
                        variant: 'warning',
                    });
                    return;
                }
                var res = await branchSessionOnServer(ready.beforeIndex || branchBefore);
                if (!res || !res.ok || !res.session_id) {
                    showUiAlert({
                        title: '创建失败',
                        message: describeServerSyncFailure(res, '创建分支未生效。'),
                        variant: 'error',
                    });
                    return;
                }
                await switchSession(res.session_id);
                void loadSessions();
            })();
        });
        return;
    }
}

function attachMessageToolbar(wrap, role) {
    const bar = document.createElement('div');
    bar.className = 'msg-toolbar';
    var html = '<button type="button" class="msg-tb" data-act="copy" data-ui-tip="复制">复制</button>'
        + '<button type="button" class="msg-tb" data-act="delete" data-ui-tip="删除">删除</button>';
    if (role === 'assistant') {
        html += '<button type="button" class="msg-tb" data-act="branch" data-ui-tip="分支">分支</button>';
    }
    if (role === 'user') html += '<button type="button" class="msg-tb" data-act="rewrite" data-ui-tip="改写">改写</button>';
    bar.innerHTML = html;
    bar.querySelectorAll('.msg-tb').forEach(bindUiHoverTip);
    bar.addEventListener('click', function (e) {
        var t = e.target;
        if (!t || t.tagName !== 'BUTTON' || !t.getAttribute) return;
        e.preventDefault();
        var a = t.getAttribute('data-act');
        if (a) onMessageToolbarClick(wrap, role, a);
    });
    wrap.appendChild(bar);
}

function getFeedItemText(row) {
    const sc = row.querySelector('.feed-chunk-scroller');
    if (sc) return sc.textContent.trim();
    const ch = row.querySelector('.feed-chunk');
    return ch ? ch.textContent.trim() : '';
}

function extractToolNameFromLog(text) {
    if (!text) return '工具';
    const line = (text.split(/\n/)[0] || text).trim();
    var m = line.match(/^([A-Za-z_][\w-]*)\s*\(/);
    if (m) return m[1];
    m = line.match(/^([^\s(]+)\s*\(/);
    if (m) return m[1];
    m = line.match(/^(\S+?)(?:\(|：)/);
    if (m) return m[1];
    return '工具';
}

function pushBriefLine(lines, line) {
    if (!line || !String(line).trim()) return;
    var t = String(line);
    if (lines.length && lines[lines.length - 1] === t) return;
    lines.push(t);
}

function refreshFeedChunkOverflow(chunk) {
    if (!chunk || !chunk.isConnected) return;
    const sc = chunk.querySelector('.feed-chunk-scroller');
    if (!sc) return;
    if (feedChunkInHiddenSubagentProcess(chunk)) return;
    if (chunk.classList.contains('expanded')) {
        chunk.classList.remove('is-overflowing');
        return;
    }
    function measure() {
        if (!chunk.isConnected || chunk.classList.contains('expanded')) return;
        var collapsedMax = feedChunkCollapsedMax(chunk);
        var contentH = sc.scrollHeight;
        if (contentH < 2) contentH = measureFeedChunkScrollerHeight(sc, chunk);
        if (chunk.classList.contains('is-streaming') || sc.clientHeight < 2) {
            chunk.classList.toggle('is-overflowing', contentH > collapsedMax + 1);
            return;
        }
        chunk.classList.toggle('is-overflowing', sc.scrollHeight > sc.clientHeight + 1);
    }
    requestAnimationFrame(function () { requestAnimationFrame(measure); });
}

function scheduleFeedChunkOverflowRefresh(chunk) {
    if (!chunk) return;
    var card = chunk.closest && chunk.closest('.subagent-grid-card');
    if (card && subagentPanelOpen && !card.classList.contains('is-expanded') && card.dataset.viewportVisible !== '1') return;
    /* streaming 中的块每个 delta 都会触发本函数；measure 是 layout 重操作，
       3 次 RAF × 每个 delta = 主线程灾难。streaming 时只 set class、不 measure。 */
    if (chunk.classList && chunk.classList.contains('is-streaming')) {
        refreshFeedChunkOverflow(chunk);
        return;
    }
    refreshFeedChunkOverflow(chunk);
    requestAnimationFrame(function () { refreshFeedChunkOverflow(chunk); });
}

function bindFeedChunkScrollChain(sc) {
    if (!sc || sc._wheelScrollChainBound) return;
    sc._wheelScrollChainBound = true;
    sc.addEventListener('wheel', onFeedChunkScrollerWheel, { passive: false });
}

function onFeedChunkScrollerWheel(e) {
    const sc = e.currentTarget;
    const chunk = sc.closest && sc.closest('.feed-chunk');
    if (!chunk || !chunk.classList.contains('expanded')) return;
    const dy = e.deltaY;
    const eps = 2;
    const st = sc.scrollTop;
    const ch = sc.clientHeight;
    const sh = sc.scrollHeight;
    const canScrollY = sh > ch + eps;
    if (canScrollY) {
        if (dy < 0 && st > eps) return;
        if (dy > 0 && st < sh - ch - eps) return;
    }
    e.preventDefault();
    e.stopPropagation();
    const body = sc.closest('.process-aggregate-body');
    const chat = document.getElementById('chat-container');
    if (body) {
        const bPrev = body.scrollTop;
        const bMax = Math.max(0, body.scrollHeight - body.clientHeight);
        var bt = bPrev + dy;
        if (bt < 0) bt = 0;
        if (bt > bMax) bt = bMax;
        if (bt !== bPrev) { smoothScrollBy(body, dy); return; }
    }
    if (chat) smoothScrollBy(chat, dy);
}

function bindProcessBriefScrollChain(brief) {
    if (!brief || brief._briefWheelBound) return;
    brief._briefWheelBound = true;
    brief.addEventListener('wheel', onProcessBriefWheel, { passive: false });
}

function onProcessBriefWheel(e) {
    const brief = e.currentTarget;
    const agg = brief.closest && brief.closest('.process-aggregate');
    if (!agg || !agg.classList.contains('is-collapsed')) return;
    const dy = e.deltaY;
    const eps = 2;
    const st = brief.scrollTop;
    const ch = brief.clientHeight;
    const sh = brief.scrollHeight;
    const canScrollY = sh > ch + eps;
    if (canScrollY) {
        if (dy < 0 && st > eps) return;
        if (dy > 0 && st < sh - ch - eps) return;
    }
    e.preventDefault();
    e.stopPropagation();
    const chat = document.getElementById('chat-container');
    if (chat) smoothScrollBy(chat, dy);
}

function setBriefRows(brief, texts) {
    brief.textContent = '';
    texts.forEach(function (t) {
        if (!t || !String(t).trim()) return;
        const row = document.createElement('div');
        row.className = 'process-brief-item';
        row.textContent = t;
        brief.appendChild(row);
    });
}

function updateProcessBrief(agg) {
    if (!agg || !agg.isConnected) return;
    const body = agg.querySelector('.process-aggregate-body');
    const brief = agg.querySelector('.process-aggregate-brief');
    if (!body || !brief) return;
    const items = Array.from(body.querySelectorAll('.feed-item'));
    const lines = [];
    var i = 0;
    while (i < items.length) {
        var el = items[i];
        var raw = getFeedItemText(el);
        if (el.classList.contains('feed--llm')) {
            if (raw) pushBriefLine(lines, '思·' + raw);
            i += 1;
        } else if (el.classList.contains('feed--llm2')) {
            if (raw) pushBriefLine(lines, '答·' + raw);
            i += 1;
        } else if (el.classList.contains('feed--tool')) {
            var countMap = {};
            var order = [];
            while (i < items.length && items[i].classList.contains('feed--tool')) {
                var tname = extractToolNameFromLog(getFeedItemText(items[i]));
                if (countMap[tname] === undefined) { countMap[tname] = 0; order.push(tname); }
                countMap[tname] += 1;
                i += 1;
            }
            for (var oi = 0; oi < order.length; oi += 1) {
                var nm = order[oi];
                var n = countMap[nm] || 0;
                if (n > 0) pushBriefLine(lines, '调用工具 ' + nm + ' ' + n + '次');
            }
        } else { i += 1; }
    }
    if (lines.length) setBriefRows(brief, lines);
    else {
        var st = body.querySelector('.feed-item.feed--st .feed-chunk-scroller, .feed-item.feed--st .feed-chunk');
        var tSt = st ? st.textContent.trim() : '';
        if (tSt) setBriefRows(brief, [tSt]);
        else {
            var any = body.querySelector('.feed-chunk-scroller, .feed-chunk');
            var tAny = any ? any.textContent.trim() : '';
            setBriefRows(brief, [tAny || '本段过程已折叠']);
        }
    }
}

function bindProcessAggregate(agg) {
    const procBody = agg.querySelector('.process-aggregate-body, .subagent-card-body');
    if (procBody && !procBody._streamFollowScrollBound) {
        procBody._streamFollowScrollBound = true;
        procBody.addEventListener('scroll', function () {
            if (!isSessionRunning(currentSessionId)) return;
            var active = getProcessBodyElForCurrentRun();
            if (active !== procBody) return;
            refreshLiveAutoFollowPins();
        }, { passive: true });
    }
    if (agg.classList.contains('subagent-grid-card')) return;
    const top = agg.querySelector('.process-aggregate-top');
    if (top && !top.dataset.bound) {
        top.dataset.bound = '1';
        top.addEventListener('click', function () {
            agg.classList.toggle('is-collapsed');
            const expanded = !agg.classList.contains('is-collapsed');
            top.setAttribute('aria-expanded', expanded ? 'true' : 'false');
            if (agg.classList.contains('is-collapsed')) {
                updateProcessBrief(agg);
            } else {
                requestAnimationFrame(function () {
                    requestAnimationFrame(function () {
                        agg.querySelectorAll('.process-aggregate-body .feed-chunk').forEach(refreshFeedChunkOverflow);
                        registerMermaidLazy(agg);
                    });
                });
            }
        });
        top.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); top.click(); }
        });
    }
    const briefEl = agg.querySelector('.process-aggregate-brief');
    if (briefEl) bindProcessBriefScrollChain(briefEl);
}

function procNow() {
    return (typeof performance !== 'undefined' && typeof performance.now === 'function') ? performance.now() : Date.now();
}

function formatProcDurationMs(ms) {
    if (ms == null || !Number.isFinite(ms) || ms < 0) return null;
    if (ms < 800) return Math.max(0, Math.round(ms)) + 'ms';
    if (ms < 60000) {
        var s = ms / 1000;
        return (s < 10 ? s.toFixed(1) : Math.round(s)) + 's';
    }
    var mi = Math.floor(ms / 60000);
    var sec = Math.round((ms % 60000) / 1000);
    return mi + '分' + sec + '秒';
}

function processStartedAtToProcNow(startedAt) {
    if (!startedAt) return null;
    var startedMs = Date.parse(String(startedAt));
    if (!Number.isFinite(startedMs)) return null;
    return procNow() - Math.max(0, Date.now() - startedMs);
}

function applyRunStartedAtToProcessGroup(agg, startedAt) {
    if (!agg || !startedAt) return;
    var t0 = processStartedAtToProcNow(startedAt);
    if (!Number.isFinite(Number(t0))) return;
    agg.dataset.procStartedAt = String(t0);
    delete agg.dataset.procEndedAt;
    if (!agg.dataset.procDurationMs) refreshProcessAggregateStats(agg);
}

function bumpAggregateMaxReactIter(agg, reactIter) {
    if (!agg) return;
    var n = Number(reactIter);
    if (!Number.isFinite(n) || n < 1) return;
    var flo = Math.floor(n);
    var cur = parseInt(agg.dataset.maxReactIter || '0', 10);
    if (flo > cur) agg.dataset.maxReactIter = String(flo);
}

function resolveSubagentAggFromCtx(ctx) {
    if (!ctx) return null;
    if (ctx.currentProcessGroup && ctx.currentProcessGroup.isConnected
        && ctx.currentProcessGroup.classList.contains('subagent-grid-card')) {
        return ctx.currentProcessGroup;
    }
    if (ctx._subagentBody && ctx._subagentBody.isConnected) {
        var card = ctx._subagentBody.closest('.subagent-grid-card');
        if (card) return card;
    }
    return null;
}

function applySubagentSessionMetricsToCard(card, metrics) {
    if (!card || !metrics || typeof metrics !== 'object') return;
    if (metrics.duration_ms != null && Number.isFinite(Number(metrics.duration_ms))) {
        card.dataset.procDurationMs = String(Math.max(0, Math.floor(Number(metrics.duration_ms))));
    }
    if (metrics.react_loops != null && Number.isFinite(Number(metrics.react_loops))) {
        card.dataset.procReactLoops = String(Math.max(0, Math.floor(Number(metrics.react_loops))));
    }
    if (metrics.tool_calls != null && Number.isFinite(Number(metrics.tool_calls))) {
        card.dataset.procToolCalls = String(Math.max(0, Math.floor(Number(metrics.tool_calls))));
    }
    if (metrics.tool_failures != null && Number.isFinite(Number(metrics.tool_failures))) {
        card.dataset.procToolFails = String(Math.max(0, Math.floor(Number(metrics.tool_failures))));
    }
}

function applySubagentProcessMetricsToCard(card, event) {
    if (!card || !event) return;
    var isRunEnd = event.duration_ms != null && Number.isFinite(Number(event.duration_ms));
    if (isRunEnd) {
        var runDur = Math.max(0, Math.round(Number(event.duration_ms)));
        var runLoops = event.react_loops != null && Number.isFinite(Number(event.react_loops))
            ? Math.max(0, Math.floor(Number(event.react_loops))) : 0;
        var runTools = event.tool_calls != null && Number.isFinite(Number(event.tool_calls))
            ? Math.max(0, Math.floor(Number(event.tool_calls))) : 0;
        var runFails = event.tool_failures != null && Number.isFinite(Number(event.tool_failures))
            ? Math.max(0, Math.floor(Number(event.tool_failures))) : 0;
        card.dataset.procDurationMs = String((parseInt(card.dataset.procDurationMs || '0', 10) || 0) + runDur);
        card.dataset.procReactLoops = String((parseInt(card.dataset.procReactLoops || '0', 10) || 0) + runLoops);
        card.dataset.procToolCalls = String((parseInt(card.dataset.procToolCalls || '0', 10) || 0) + runTools);
        card.dataset.procToolFails = String((parseInt(card.dataset.procToolFails || '0', 10) || 0) + runFails);
        delete card.dataset.procLiveToolCalls;
        delete card.dataset.procLiveToolFails;
    } else {
        if (event.tool_calls != null && Number.isFinite(Number(event.tool_calls))) {
            var liveTools = Math.max(0, Math.floor(Number(event.tool_calls)));
            var prevTools = parseInt(card.dataset.procLiveToolCalls || '0', 10) || 0;
            card.dataset.procLiveToolCalls = String(Math.max(prevTools, liveTools));
        }
        if (event.tool_failures != null && Number.isFinite(Number(event.tool_failures))) {
            var liveFails = Math.max(0, Math.floor(Number(event.tool_failures)));
            var prevFails = parseInt(card.dataset.procLiveToolFails || '0', 10) || 0;
            card.dataset.procLiveToolFails = String(Math.max(prevFails, liveFails));
        }
    }
}

function uiEventReactIter(ev) {
    if (!ev || ev.react_iter == null) return null;
    var n = Number(ev.react_iter);
    if (!Number.isFinite(n) || n < 1) return null;
    return n;
}

function applyCacheStatsFromEvent(ctx, event) {
    if (!event || typeof event !== 'object') return;
    var agg = resolveSubagentAggFromCtx(ctx);
    if (!agg || !agg.isConnected) {
        agg = ctx && ctx.currentProcessGroup;
        if (!agg || !agg.isConnected) {
            var st = (ctx && ctx.stream) ? ctx.stream : getVisibleChatStream();
            if (st) agg = st.querySelector('.process-aggregate:last-of-type');
        }
    }
    if (!agg) return;
    if (event.cache_hit != null) agg.dataset.procCacheHit = String(Math.max(0, Math.floor(Number(event.cache_hit))));
    if (event.cache_miss != null) agg.dataset.procCacheMiss = String(Math.max(0, Math.floor(Number(event.cache_miss))));
    if (event.hit_rate != null) agg.dataset.procCacheRate = String(Math.max(0, Number(event.hit_rate)));
    if (event.model != null) agg.dataset.procCacheModel = String(event.model);
    if (event.input_tokens != null) agg.dataset.procCacheInput = String(Math.max(0, Math.floor(Number(event.input_tokens))));
    if (event.output_tokens != null) agg.dataset.procCacheOutput = String(Math.max(0, Math.floor(Number(event.output_tokens))));
    if (event.tokens_per_sec != null) agg.dataset.procCacheTps = String(Math.max(0, Number(event.tokens_per_sec)));
    refreshAggregateStatsSmart(agg);
}

function applyProcessMetricsFromEvent(ctx, event) {
    if (!event || typeof event !== 'object') return;
    var subCard = resolveSubagentAggFromCtx(ctx);
    if (subCard && subCard.isConnected) {
        applySubagentProcessMetricsToCard(subCard, event);
        scheduleSubagentCardStats(subCard);
        return;
    }
    var agg = ctx && ctx.currentProcessGroup;
    if (!agg || !agg.isConnected) {
        var st = (ctx && ctx.stream) ? ctx.stream : getVisibleChatStream();
        if (st) agg = st.querySelector('.process-aggregate:last-of-type');
    }
    if (!agg) return;
    if (event.duration_ms != null && Number.isFinite(Number(event.duration_ms))) {
        agg.dataset.procDurationMs = String(Math.max(0, Math.round(Number(event.duration_ms))));
    }
    if (event.react_loops != null && Number.isFinite(Number(event.react_loops))) {
        agg.dataset.procReactLoops = String(Math.max(0, Math.floor(Number(event.react_loops))));
    }
    if (event.tool_calls != null && Number.isFinite(Number(event.tool_calls))) {
        agg.dataset.procToolCalls = String(Math.max(0, Math.floor(Number(event.tool_calls))));
    }
    if (event.tool_failures != null && Number.isFinite(Number(event.tool_failures))) {
        agg.dataset.procToolFails = String(Math.max(0, Math.floor(Number(event.tool_failures))));
    }
    refreshAggregateStatsSmart(agg);
}

function refreshAggregateStatsSmart(agg) {
    if (agg && agg.classList && agg.classList.contains('subagent-grid-card')) refreshSubagentCardStats(agg);
    else refreshProcessAggregateStats(agg);
}

function refreshSubagentCardStats(card) {
    if (!card) return;
    var el = card.querySelector('.process-aggregate-stats');
    if (!el) return;
    var body = card.querySelector('.subagent-card-body');
    var pDur = card.dataset.procDurationMs != null && card.dataset.procDurationMs !== ''
        ? parseInt(card.dataset.procDurationMs, 10) : NaN;
    var pLoops = card.dataset.procReactLoops != null && card.dataset.procReactLoops !== ''
        ? parseInt(card.dataset.procReactLoops, 10) : NaN;
    var pTools = card.dataset.procToolCalls != null && card.dataset.procToolCalls !== ''
        ? parseInt(card.dataset.procToolCalls, 10) : NaN;
    var pFails = card.dataset.procToolFails != null && card.dataset.procToolFails !== ''
        ? parseInt(card.dataset.procToolFails, 10) : NaN;
    var maxFromRows = 0;
    var bodyLoaded = subagentBodyIsLoaded(body) && body.dataset.stashed !== '1';
    if (bodyLoaded) {
        body.querySelectorAll('.subagent-turn-process .feed-item[data-react-iter]').forEach(function (row) {
            var v = parseInt(row.getAttribute('data-react-iter'), 10);
            if (Number.isFinite(v) && v > maxFromRows) maxFromRows = v;
        });
    }
    var dsRi = card.dataset.maxReactIter ? parseInt(card.dataset.maxReactIter, 10) : 0;
    var reactLoops = Math.max(maxFromRows, dsRi);
    if (!reactLoops && bodyLoaded) {
        reactLoops = body.querySelectorAll('.subagent-turn-process .feed-item[data-log-type="llm-response"]').length;
    }
    if (Number.isFinite(pLoops) && pLoops > 0) reactLoops = pLoops;
    var sessionTools = Number.isFinite(pTools) && pTools >= 0 ? pTools : 0;
    var liveTools = parseInt(card.dataset.procLiveToolCalls || '0', 10) || 0;
    var toolN = sessionTools + liveTools;
    if (!toolN && bodyLoaded) {
        toolN = body.querySelectorAll('.subagent-turn-process .feed-item[data-log-type="tool-call"]').length;
    }
    var sessionFails = Number.isFinite(pFails) && pFails >= 0 ? pFails : 0;
    var liveFails = parseInt(card.dataset.procLiveToolFails || '0', 10) || 0;
    var failN = sessionFails + liveFails;
    if (!failN && bodyLoaded) {
        body.querySelectorAll('.subagent-turn-process .feed-item[data-log-type="tool-call"]').forEach(function (row) {
            var sc = row.querySelector('.feed-chunk-scroller');
            var txt = sc ? String(sc.textContent || '') : '';
            if (/Error:|失败|异常|error executing command:/i.test(txt)) failN += 1;
        });
    }
    var t0s = card.dataset.procStartedAt;
    var t0 = (t0s != null && t0s !== '') ? Number(t0s) : NaN;
    var parts = [];
    var durStr = null;
    if (Number.isFinite(pDur) && pDur >= 0) durStr = formatProcDurationMs(pDur);
    else if (Number.isFinite(t0)) {
        var t1s = card.dataset.procEndedAt;
        var t1 = (t1s != null && t1s !== '') ? Number(t1s) : procNow();
        durStr = formatProcDurationMs(t1 - t0);
    }
    if (durStr) parts.push(durStr);
    parts.push(String(reactLoops) + ' 轮');
    parts.push('工具 ' + String(toolN) + ' 次');
    parts.push('失败 ' + String(failN) + ' 次');
    var modelStr = card.dataset.procCacheModel || card.dataset.executorModel || '—';
    var est = card.dataset.procCtxEstimated;
    var thr = card.dataset.procCtxThreshold;
    var pctStr = '—';
    if (est != null && est !== '' && thr != null && thr !== '' && Number(thr) > 0) {
        pctStr = (Math.round(Number(est) / Number(thr) * 1000) / 10) + '%';
    }
    el.innerHTML = '<span>' + parts.join(' · ') + '</span><span>' + escapeHtml(modelStr) + ' · ' + escapeHtml(pctStr) + '</span>';
}

function refreshProcessAggregateStats(agg) {
    if (!agg) return;
    var el = agg.querySelector('.process-aggregate-stats');
    if (!el) return;
    var body = agg.querySelector('.process-aggregate-body');
    if (!body) { el.textContent = ''; return; }
    var pDur = agg.dataset.procDurationMs != null && agg.dataset.procDurationMs !== ''
        ? parseInt(agg.dataset.procDurationMs, 10) : NaN;
    var pLoops = agg.dataset.procReactLoops != null && agg.dataset.procReactLoops !== ''
        ? parseInt(agg.dataset.procReactLoops, 10) : NaN;
    var pTools = agg.dataset.procToolCalls != null && agg.dataset.procToolCalls !== ''
        ? parseInt(agg.dataset.procToolCalls, 10) : NaN;
    var pFails = agg.dataset.procToolFails != null && agg.dataset.procToolFails !== ''
        ? parseInt(agg.dataset.procToolFails, 10) : NaN;
    var maxFromRows = 0;
    body.querySelectorAll('.feed-item[data-react-iter]').forEach(function (row) {
        var v = parseInt(row.getAttribute('data-react-iter'), 10);
        if (Number.isFinite(v) && v > maxFromRows) maxFromRows = v;
    });
    var dsRi = agg.dataset.maxReactIter ? parseInt(agg.dataset.maxReactIter, 10) : 0;
    var reactLoops = Math.max(maxFromRows, dsRi);
    if (!reactLoops) {
        reactLoops = body.querySelectorAll('.feed-item[data-log-type="llm-response"]').length;
    }
    if (Number.isFinite(pLoops) && pLoops >= 0) reactLoops = pLoops;
    var toolN = body.querySelectorAll('.feed-item[data-log-type="tool-call"]').length;
    if (Number.isFinite(pTools) && pTools >= 0) toolN = pTools;
    var failN = 0;
    if (Number.isFinite(pFails) && pFails >= 0) failN = pFails;
    var t0s = agg.dataset.procStartedAt;
    var t0 = (t0s != null && t0s !== '') ? Number(t0s) : NaN;
    var parts = [];
    var durStr = null;
    if (Number.isFinite(pDur) && pDur >= 0) durStr = formatProcDurationMs(pDur);
    else if (Number.isFinite(t0)) {
        var t1s = agg.dataset.procEndedAt;
        var t1 = (t1s != null && t1s !== '') ? Number(t1s) : procNow();
        durStr = formatProcDurationMs(t1 - t0);
    }
    if (durStr) parts.push(durStr);
    parts.push(String(reactLoops) + ' 轮');
    parts.push('工具 ' + String(toolN) + ' 次');
        parts.push('失败 ' + String(failN) + ' 次');
    var ch = agg.dataset.procCacheHit != null && agg.dataset.procCacheHit !== '' ? parseInt(agg.dataset.procCacheHit, 10) : 0;
    var cm = agg.dataset.procCacheMiss != null && agg.dataset.procCacheMiss !== '' ? parseInt(agg.dataset.procCacheMiss, 10) : 0;
    var cr = agg.dataset.procCacheRate != null && agg.dataset.procCacheRate !== '' ? parseFloat(agg.dataset.procCacheRate) : 0;
    var modelStr = agg.dataset.procCacheModel || '';
    var inputStr = agg.dataset.procCacheInput || '0';
    var outputStr = agg.dataset.procCacheOutput || '0';
    var tps = agg.dataset.procCacheTps;
    var cacheParts = [];
    if (modelStr) cacheParts.push(modelStr);
    cacheParts.push('input=' + inputStr);
    cacheParts.push('output=' + outputStr);
    if (tps && tps !== '0') cacheParts.push(tps + ' tok/s');
    var rateStr = (ch + cm > 0) ? (cr % 1 === 0 ? cr.toFixed(0) : cr.toFixed(1)) + '%' : '0%';
    cacheParts.push('hit_rate=' + rateStr);
    var cacheLine = cacheParts.join(' · ');
    el.innerHTML = '<span>' + parts.join(' · ') + '</span><span>' + cacheLine + '</span>';
}

function ensureProcessGroup(ctx) {
    if (!ctx || !ctx.stream) return null;
    /* DocumentFragment 或未挂上 document 的节点 isConnected 为 false；回放或「加载更早消息」预挂载时需保留同一执行过程框 */
    if (ctx.currentProcessGroup && !ctx.currentProcessGroup.isConnected && !replayingMessages) ctx.currentProcessGroup = null;
    if (ctx.currentProcessGroup) return ctx.currentProcessGroup;
    stripWelcome(ctx);
    const wrap = document.createElement('div');
    wrap.className = 'process-aggregate';
    var replayCollapsed = !!replayingMessages;
    if (replayCollapsed) wrap.classList.add('is-collapsed');
    wrap.innerHTML = '<div class="process-aggregate-top" role="button" tabindex="0" aria-expanded="' + (replayCollapsed ? 'false' : 'true') + '">'
        + '<div class="process-aggregate-top-line">'
        + '<span class="process-aggregate-title-wrap">'
        + '<span class="process-aggregate-title">执行过程</span>'
        + '<span class="process-aggregate-stats" aria-live="polite"></span>'
        + '</span>'
        + '<span class="process-chev" aria-hidden="true">▼</span></div>'
        + '<div class="process-aggregate-brief"></div></div>'
        + '<div class="process-aggregate-body"></div>';
    if (!replayingMessages) {
        if (ctx.runStartedAt) applyRunStartedAtToProcessGroup(wrap, ctx.runStartedAt);
        else wrap.dataset.procStartedAt = String(procNow());
    }
    delete wrap.dataset.maxReactIter;
    (ctx.stream || chatContainer).appendChild(wrap);
    bindProcessAggregate(wrap);
    ctx.currentProcessGroup = wrap;
    refreshProcessAggregateStats(wrap);
    return wrap;
}

function sealProcessGroup(ctx) {
    if (!ctx) return;
    if (!ctx.currentProcessGroup) return;
    const agg = ctx.currentProcessGroup;
    if (agg.isConnected) {
        updateProcessBrief(agg);
        if (agg.dataset.procStartedAt) agg.dataset.procEndedAt = String(procNow());
        refreshProcessAggregateStats(agg);
    }
    ctx.currentProcessGroup = null;
    ctx.progressScrollers = {};
    resetKeyContextStreamFilter(ctx);
    finalizeProgressStreamChunks(ctx);
}

function getProcessBody(ctx) {
    if (ctx && ctx._subagentTurnProcess && ctx._subagentTurnProcess.isConnected) return ctx._subagentTurnProcess;
    if (ctx && ctx.currentTurn && ctx.currentTurn.isConnected) {
        var subProc = ctx.currentTurn.querySelector('.subagent-turn-process');
        if (subProc) {
            ctx._subagentTurnProcess = subProc;
            return subProc;
        }
    }
    if (ctx && ctx._subagentBody && ctx._subagentBody.isConnected) return null;
    const w = ensureProcessGroup(ctx);
    if (!w) return null;
    return w.querySelector('.process-aggregate-body');
}

function autoResizeTextarea() {
    messageInput.style.height = 'auto';
    messageInput.style.height = Math.min(messageInput.scrollHeight, 120) + 'px';
}
messageInput.addEventListener('input', autoResizeTextarea);
messageInput.addEventListener('input', rewriteInputWorkspacePaths);
autoResizeTextarea();
refreshInputPathChips();

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/[&<>]/g, function(m) {
        if (m === '&') return '&amp;';
        if (m === '<') return '&lt;';
        if (m === '>') return '&gt;';
        return m;
    });
}

function scrollToBottom() {
    requestAnimationFrame(function () {
        requestAnimationFrame(function () {
            if (chatContainer) chatContainer.scrollTop = chatContainer.scrollHeight;
            requestAnimationFrame(function () {
                if (chatContainer) chatContainer.scrollTop = chatContainer.scrollHeight;
            });
        });
    });
}

// 滚动位置存储
const LS_SCROLL_POSITION_PREFIX = 'myagent-scroll-';
const LS_SCROLL_ANCHOR_PREFIX = 'myagent-scroll-anchor-';

function getScrollPositionKey(sessionId) {
    return LS_SCROLL_POSITION_PREFIX + sessionId;
}

function getScrollAnchorKey(sessionId) {
    return LS_SCROLL_ANCHOR_PREFIX + sessionId;
}

function saveScrollPosition(sessionId, scrollTop) {
    if (!sessionId) return;
    try {
        localStorage.setItem(getScrollPositionKey(sessionId), String(Math.round(scrollTop)));
    } catch (e) { /* ignore */ }
}

function saveScrollAnchorPosition(sessionId) {
    if (!chatContainer || !sessionId) return;
    try {
        if (isNearBottom(chatContainer, STREAM_CHAT_NEAR_BOTTOM_PX)) {
            localStorage.removeItem(getScrollAnchorKey(sessionId));
            return;
        }
        var rect = chatContainer.getBoundingClientRect();
        var wraps = chatContainer.querySelectorAll('.msg-wrap--user[data-event-index]');
        var best = null;
        for (var i = 0; i < wraps.length; i += 1) {
            var wr = wraps[i];
            var ei = Number(wr.getAttribute('data-event-index'));
            if (!Number.isFinite(ei)) continue;
            var top = wr.getBoundingClientRect().top;
            if (top <= rect.top + 8) best = ei;
            else if (best == null) {
                best = ei;
                break;
            }
        }
        if (best != null) localStorage.setItem(getScrollAnchorKey(sessionId), String(best));
    } catch (e) { /* ignore */ }
}

function getSavedScrollAnchorPosition(sessionId) {
    if (!sessionId) return null;
    try {
        var saved = localStorage.getItem(getScrollAnchorKey(sessionId));
        if (saved == null || saved === '') return null;
        var n = Number(saved);
        return Number.isFinite(n) ? n : null;
    } catch (e) { return null; }
}

function getSavedScrollPosition(sessionId) {
    if (!sessionId) return null;
    try {
        var saved = localStorage.getItem(getScrollPositionKey(sessionId));
        return saved ? parseInt(saved, 10) : null;
    } catch (e) { return null; }
}

function saveChatScrollForSession(sid) {
    if (!chatContainer || !sid) return;
    saveScrollPosition(sid, chatContainer.scrollTop);
    saveScrollAnchorPosition(sid);
}

function clampChatScrollTop(y) {
    if (!chatContainer) return 0;
    const max = Math.max(0, chatContainer.scrollHeight - chatContainer.clientHeight);
    return Math.min(Math.max(0, y), max);
}

/**
 * @param {string} sessionId
 * @param {'saved-or-bottom'|'bottom'} mode — saved-or-bottom：有离开记录则恢复，否则置底；bottom：始终置底
 */
function applyChatScrollAfterHistoryLoad(sessionId, mode) {
    if (!chatContainer || !sessionId) return;
    
    // 如果会话正在运行，执行过程块默认置底
    if (isSessionRunning(sessionId)) {
        var run = getSessionRunState(sessionId);
        if (run && run.ctx && run.ctx.stream) {
            var agg = run.ctx.stream.querySelector('.process-aggregate:last-of-type');
            if (agg) {
                var procBody = agg.querySelector('.process-aggregate-body');
                if (procBody) {
                    // 延迟一帧确保DOM已渲染
                    requestAnimationFrame(function() {
                        procBody.scrollTop = procBody.scrollHeight;
                    });
                }
            }
        }
    }
    
    if (mode === 'saved-or-bottom') {
        var savedAnchor = getSavedScrollAnchorPosition(sessionId);
        if (savedAnchor != null && typeof scrollToUserTurnOrLoadOlder === 'function') {
            requestAnimationFrame(function () {
                if (sessionId === currentSessionId) void scrollToUserTurnOrLoadOlder(savedAnchor);
            });
            streamChatNearBottom = false;
            streamProcNearBottom = true;
            liveAutoFollow = false;
            return;
        }
        var savedPosition = getSavedScrollPosition(sessionId);
        if (savedPosition !== null && savedPosition > 0) {
            // 恢复保存的滚动位置
            chatContainer.scrollTop = savedPosition;
            streamChatNearBottom = isNearBottom(chatContainer, STREAM_CHAT_NEAR_BOTTOM_PX);
            streamProcNearBottom = true;
            liveAutoFollow = streamChatNearBottom;
            return;
        }
    }
    
    // 默认行为：滚动到底部
    streamChatNearBottom = true;
    streamProcNearBottom = true;
    liveAutoFollow = true;
    scrollToBottom();
}

window.addEventListener('beforeunload', function () {
    saveChatScrollForSession(currentSessionId);
});
document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') saveChatScrollForSession(currentSessionId);
    else if (typeof reconcileRunStateFromServer === 'function') {
        void reconcileRunStateFromServer({ silent: true });
    }
});
window.addEventListener('pageshow', function () {
    if (typeof reconcileRunStateFromServer === 'function') {
        void reconcileRunStateFromServer({ silent: true });
    }
});
window.addEventListener('focus', function () {
    if (typeof reconcileRunStateFromServer === 'function') {
        void reconcileRunStateFromServer({ silent: true });
    }
});

const WELCOME_HTML = `<div class="welcome" role="status"><div class="welcome-icon" aria-hidden="true"><svg viewBox="0 0 44 22" fill="none" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;user-select:none;-webkit-user-select:none;pointer-events:none"><text x="22" y="16" text-anchor="middle" font-family="'Brush Script MT','Segoe Script','Pacifico','Dancing Script',cursive" font-size="14" font-style="italic" fill="white" stroke="none" transform="rotate(-6 22 11)">Sugar</text></svg></div><strong>开始一段新的对话</strong><p>在左侧侧栏新建或选择会话。Enter 发送，Ctrl+Enter / Shift+Enter 换行。</p></div>`;

function historyLoadScrollsToBottom(sessionId, mode) {
    return true;
}

function waitForChatScrollAfterHistoryLoad(sessionId, mode) {
    if (!chatContainer || !sessionId) return Promise.resolve(false);
    var toBottom = true;
    var started = (window.performance && performance.now) ? performance.now() : Date.now();
    var lastTop = -1;
    var stableFrames = 0;
    return new Promise(function (resolve) {
        var done = false;
        var cleanup = null;
        function nowMs() {
            return (window.performance && performance.now) ? performance.now() : Date.now();
        }
        function targetReached() {
            if (!chatContainer) return true;
            return isNearBottom(chatContainer, 24);
        }
        function finish(ok) {
            if (done) return;
            done = true;
            if (cleanup) cleanup();
            resolve(ok);
        }
        if ('onscrollend' in chatContainer) {
            var onEnd = function () {
                if (sessionId !== currentSessionId) {
                    finish(false);
                    return;
                }
                if (targetReached()) finish(true);
            };
            chatContainer.addEventListener('scrollend', onEnd, { passive: true });
            cleanup = function () {
                chatContainer.removeEventListener('scrollend', onEnd);
            };
        }
        function step() {
            if (done) return;
            if (sessionId !== currentSessionId || !chatContainer) {
                finish(false);
                return;
            }
            var top = chatContainer.scrollTop;
            var reached = targetReached();
            if (Math.abs(top - lastTop) < 0.5) stableFrames += 1;
            else stableFrames = 0;
            lastTop = top;
            if ((reached && stableFrames >= 2) || nowMs() - started > 2400) {
                finish(reached);
                return;
            }
            requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
    });
}

function setWelcome() {
    resetSessionHistoryPaging();
    const vs = getVisibleChatStream();
    if (vs) {
        emptyChatStreamKeepingStrip(vs);
        vs.insertAdjacentHTML('beforeend', WELCOME_HTML);
    } else {
        chatContainer.innerHTML = '';
        ensureVisibleChatStreamSlot();
        const vs2 = getVisibleChatStream();
        if (vs2) vs2.insertAdjacentHTML('beforeend', WELCOME_HTML);
        else chatContainer.innerHTML = WELCOME_HTML;
    }
    rebuildToc();
    void refreshTodoPlanPanel();
}

function stripWelcome(ctx) {
    if (ctx && ctx._subagentBody) return;
    const root = (ctx && ctx.stream) ? ctx.stream : (getVisibleChatStream() || chatContainer);
    if (root) root.querySelector('.welcome')?.remove();
}

function clearChat() { setWelcome(); }

function pathJoinBaseName(baseDir, name) {
    if (!baseDir) return name || '';
    if (!name) return baseDir;
    var d = String(baseDir).replace(/[\\/]+$/, '');
    var useBack = d.indexOf('\\') !== -1;
    return d + (useBack ? '\\' : '/') + name;
}

/** 将「工作区绝对路径」转为 file:// URL（Windows / Unix）；分段编码以支持空格、中文等。 */
function fileUrlFromFsPath(fsPath) {
    var norm = String(fsPath || '').replace(/\\/g, '/');
    if (/^\/\//.test(norm)) return 'file:' + norm.replace(/\//g, '/');
    var encRest = function (rel) {
        if (!rel) return '';
        return rel.split('/').map(function (seg) {
            return encodeURIComponent(seg);
        }).join('/');
    };
    if (/^[A-Za-z]:\//.test(norm)) {
        return 'file:///' + norm.slice(0, 3) + encRest(norm.slice(3));
    }
    return 'file:///' + encRest(norm.replace(/^\/+/, ''));
}

/**
 * 助手常写「保存至：📄 /报告.md」——以 / 开头表示相对工作区根目录的路径（非 URL）。
 */
function joinWorkDirAndRelativeSlashPath(workDir, slashPath) {
    var rel = String(slashPath || '').replace(/^\/+/, '');
    if (!rel || !workDir) return null;
    var d = String(workDir).replace(/[\\/]+$/, '');
    var useBack = d.indexOf('\\') !== -1;
    var segs = rel.split(/\/+/).filter(Boolean);
    if (!segs.length) return null;
    var tail = segs.join(useBack ? '\\' : '/');
    return d + (useBack ? '\\' : '/') + tail;
}

function trimTrailingPathPunct(s) {
    return String(s || '').replace(/[，。、；：）】』」\]\)\.,;:!?'"」]+$/g, '').trim();
}

function stripPathWrappingQuotes(s) {
    var t = String(s || '').trim();
    if (t.length >= 2) {
        var a = t.charAt(0);
        var b = t.charAt(t.length - 1);
        if ((a === '"' && b === '"') || (a === "'" && b === "'")) {
            return t.slice(1, -1).trim();
        }
    }
    return t;
}

/** 统一全角标点/数字等，便于识别「．xlsx」「路径：／」等变体 */
function linkifyNormalizePathToken(s) {
    try {
        return String(s || '').normalize('NFKC');
    } catch (e) {
        return String(s || '');
    }
}

/** 可链转「工作区下文件」的已知后缀（与 linkify / 虚拟路径规则共用） */
var LINKIFY_EXT_FRAGMENT = (
    'md|markdown|txt|py|jsx?|tsx?|mjs|cjs|json|ya?ml|toml|xml|html?|htm|css|s?css|less|sass|scss|' +
    'xlsx?|xlsm?|xlsb?|xlt|csv|tsv|ods|numbers|et|' +
    'pdf|docx?|docm?|dotx?|rtf|odt|pages|' +
    'pptx?|pptm?|potx?|odp|key|' +
    'png|jpe?g|gif|webp|svg|ico|bmp|tiff?|heic|avif|jfif|raw|' +
    'zip|7z|rar|gz|tgz|tar|bz2|xz|lz4|zst|' +
    'mp3|mp4|m4a|aac|flac|wav|ogg|webm|mov|avi|mkv|' +
    'log|ini|env|cfg|conf|properties|plist|' +
    'sh|bash|zsh|fish|bat|cmd|ps1|' +
    'rs|go|java|kt|kts|swift|scala|rb|php|pl|pm|' +
    '[ch]pp?|cc|hh|mm|hpp|cs|fs|fsx|vb|' +
    'vue|svelte|elm|dart|ex|exs|erl|hrl|' +
    'ipynb|rmd|qmd|tex|bib|cls|sty|rst|adoc|org|' +
    'sql|graphql|proto|thrift|cmake|gradle|mk|dockerfile|' +
    'wasm|wat|lock|patch|diff|rej|har|drawio|vsix|' +
    'sqlite3?|db|duckdb|mdb|accdb|parquet|feather|arrow|orc|ndjson|' +
    'ttf|otf|woff2?|eot|apk|ipa|exe|msi|dmg|iso|pkg|deb|rpm|bin|so|dylib|dll|lib|o|a|map|' +
    'epub|mobi|azw3|chm|cert|pem|crt|cer|pub|asc|p12|pfx|keystore'
);

var _linkifyKnownExtRe = null;
function linkifyKnownExtRegex() {
    if (!_linkifyKnownExtRe) {
        _linkifyKnownExtRe = new RegExp('\\.(' + LINKIFY_EXT_FRAGMENT + ')\\b', 'i');
    }
    return _linkifyKnownExtRe;
}

/**
 * 以 / 开头的「工作区相对路径」是否做成可点击链接。
 * 仅允许带常见文件后缀的路径，避免 ARPU/DOU/MOU、日期 2024/01 等内联斜杠被当成目录。
 * （仍排除明显的 POSIX/Git Bash 根路径，以免误链。）
 */
function workspaceRelativePathAutoLinkOk(slashPath) {
    var t = linkifyNormalizePathToken(String(slashPath || '').trim());
    if (!t || t.charAt(0) !== '/' || t.charAt(1) === '/') return false;
    var posixTop = /^\/(mingw\d*|usr|bin|etc|proc|dev|sys|opt|var|run|lib|lib64|snap|sbin|boot|srv|tmp|media|mnt)(\/|$)/i;
    var msysDrive = /^\/[a-z](\/|$)/i;
    var webish = /^\/(api|v\d+|static|assets|node_modules)(\/|$)/i;
    if (posixTop.test(t) || msysDrive.test(t) || webish.test(t)) return false;
    return linkifyKnownExtRegex().test(t);
}

function workspaceRelativePathNoSlashAutoLinkOk(relPath) {
    var t = linkifyNormalizePathToken(String(relPath || '').trim());
    if (!t || t.charAt(0) === '/' || /^https?:\/\//i.test(t)) return false;
    if (/^([A-Za-z]):[\\/]/.test(t) || /^\\\\/.test(t)) return false;
    if (!/[\\/]/.test(t)) return false;
    if (/[<>:'"|\r\n]/.test(t)) return false;
    if (/(^|[\\/])\.{1,2}([\\/]|$)/.test(t)) return false;
    return linkifyKnownExtRegex().test(t);
}

function getCurrentSessionDataPath() {
    var sdir = (typeof window.__SESSIONS_DIR__ === 'string') ? window.__SESSIONS_DIR__ : '';
    if (sdir && currentSessionId) return pathJoinBaseName(sdir, currentSessionId);
    var w = (typeof window.__WORK_DIR__ === 'string') ? window.__WORK_DIR__ : '';
    if (w && currentSessionId) return pathJoinBaseName(pathJoinBaseName(w, 'sessions'), currentSessionId);
    return '';
}

/** 标题栏与侧栏：工作目录绝对路径与会话 ID（与服务端 window.__WORK_DIR__ 一致） */
function buildSessionWorkspaceSubtitle(sessionId) {
    var w = (typeof window.__WORK_DIR__ === 'string') ? window.__WORK_DIR__ : '';
    if (!sessionId) return w || '';
    if (w) {
        var workspaceLink = '<a href="#" data-workspace-open="' + w + '" class="msg-link-workspace-open" style="color:inherit;text-decoration:inherit;cursor:pointer;" data-ui-tip="打开工作目录">' + w + '</a>';
        var sessionPath = 'sessions/' + sessionId;
        var sessionLink = '<a href="#" data-workspace-open="' + sessionPath + '" class="msg-link-workspace-open" style="color:inherit;text-decoration:inherit;cursor:pointer;" data-ui-tip="打开会话目录">' + sessionId + '</a>';
        return workspaceLink + ' | ' + sessionLink;
    }
    return String(sessionId);
}

/** 侧栏每条会话标题下方：最近一次用户提问（服务端字段 last_user_preview） */
function formatSessionListSubtitle(sess) {
    if (!sess) return '暂无提问';
    var t = sess.last_user_preview != null ? String(sess.last_user_preview).trim() : '';
    return t || '暂无提问';
}

/** 与服务端 _normalize_sidebar_preview_text 对齐：折叠空白、180 字符、省略号 */
function normalizeSidebarPreviewText(text, maxLen) {
    maxLen = maxLen || 180;
    var s = String(text || '').trim();
    if (!s) return '';
    var oneLine = s.split(/\s+/).join(' ');
    if (oneLine.length > maxLen) return oneLine.slice(0, maxLen - 1) + '\u2026';
    return oneLine;
}

/** 发送后立即更新侧栏「最近提问」（与服务器摘要规则一致）；稍后 refreshSingleSessionRow 仍会校正 */
function updateSidebarLastUserPreviewImmediate(sessionId, questionText) {
    if (!sessionId || !sessionsList) return;
    var nameEl = sessionsList.querySelector('.session-name[data-id="' + sessionId + '"]');
    var div = nameEl && nameEl.closest('.session-item');
    if (!div) return;
    var wsEl = div.querySelector('.session-last-query');
    if (!wsEl) return;
    var line = normalizeSidebarPreviewText(questionText, 180);
    if (!line) line = '暂无提问';
    wsEl.textContent = line;
    wsEl.setAttribute('data-ui-tip', line);
    bindUiHoverTip(wsEl);
}

function updateSessionTitle() {
    const br = document.getElementById('breadcrumb-text');
    const sub = document.getElementById('breadcrumb-sub');
    if (!br || !sub) return;
    if (!currentSessionId) {
        br.textContent = '未选择会话';
        sub.textContent = '';
        setContextTokenLabel(null, null);
        return;
    }
    const sess = selectCurrentSession();
    const el = document.querySelector('.session-name[data-id="' + currentSessionId + '"]');
    const raw = sess && sess.name != null ? String(sess.name) : (el ? (el.getAttribute('data-original') || el.textContent || '') : '');
    const name = (raw && raw.trim()) ? raw.trim() : 'Session';
    br.textContent = name;
    sub.innerHTML = buildSessionWorkspaceSubtitle(currentSessionId);
    initUiHoverTips(sub);
}

function ensureMermaidInitialized() {
    if (mermaidInitialized || !window.mermaid) return;
    try {
        var light = document.documentElement.classList.contains('theme-light');
        mermaid.initialize({
            startOnLoad: false,
            theme: light ? 'neutral' : 'dark',
            securityLevel: 'loose',
            themeVariables: {
                fontSize: '11px',
                fontFamily: 'Plus Jakarta Sans, system-ui, sans-serif',
            },
            flowchart: { htmlLabels: true, curve: 'basis' },
            sequence: { useMaxWidth: true },
        });
        mermaidInitialized = true;
    } catch (e) { /* ignore */ }
}

/**
 * flowchart 节点 E[文本] 内若含 <br> 且又含裸引号 "，Mermaid 10.9 会报 got 'STR'。
 * 将此类标签整体包成 ["..."] 并转义内部 ASCII 引号。
 */
function fixFlowchartBracketLabelsWithLineBreak(text) {
    return text.replace(/\[[^\]\n\r]*<br\s*\/?[^\]\n\r]*\]/gi, function (match) {
        var inner = match.slice(1, -1);
        var s = inner.trim();
        if (!s) return match;
        if (s.charAt(0) === '"' && s.charAt(s.length - 1) === '"') return match;
        var escaped = s.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
        return '["' + escaped + '"]';
    });
}

/** 未用引号包裹的 [] 节点里出现裸 " 时同样会触发词法错误 */
function fixFlowchartBracketLabelsWithRawQuotes(text) {
    return text.replace(/\[[^\]\n\r]*"[^\]\n\r]*\]/g, function (match) {
        var inner = match.slice(1, -1);
        var s = inner.trim();
        if (!s) return match;
        if (s.charAt(0) === '"' && s.charAt(s.length - 1) === '"') return match;
        var escaped = s.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
        return '["' + escaped + '"]';
    });
}

/** 去除 LLM/粘贴带来的杂讯，减少 Mermaid 10.9+ 报 Syntax error in text */
function normalizeMermaidSource(raw) {
    var t = String(raw || '')
        .replace(/^\uFEFF/, '')
        .replace(/\u200b|\u200c|\u200d/g, '')
        .replace(/\r\n/g, '\n')
        .replace(/\r/g, '\n');
    t = t.replace(/^\s*```(?:mermaid)?\s*\n/i, '');
    t = t.replace(/\n\s*```\s*$/i, '');
    t = t.replace(/[\u201C\u201D\u201E\u00AB\u00BB]/g, '"');
    t = t.replace(/<br\s*\/?>/gi, '<br/>');
    t = fixFlowchartBracketLabelsWithLineBreak(t);
    t = fixFlowchartBracketLabelsWithRawQuotes(t);
    var lines = t.split('\n');
    if (lines.length && lines[0]) {
        lines[0] = lines[0].replace(/\s*[\uFF1A：]\s*$/, '');
    }
    t = lines.map(function (line) { return line.replace(/\s+$/g, ''); }).join('\n').trim();
    return t;
}

function showMermaidRenderError(el, source, err) {
    el.classList.add('mermaid-error');
    el.removeAttribute('data-processed');
    var msg = 'Mermaid 无法解析此图';
    if (err) {
        if (typeof err === 'string') msg = err;
        else if (err.str) msg = String(err.str);
        else if (err.message) msg = String(err.message);
    }
    el.innerHTML = '<div class="mermaid-error-msg">' + escapeHtml(msg) + '</div>'
        + '<pre class="mermaid-raw">' + escapeHtml(source) + '</pre>';
}

function upgradeMermaidBlocks(root) {
    if (!root) return;
    root.querySelectorAll('pre > code').forEach(function (codeEl) {
        var cls = codeEl.getAttribute('class') || '';
        if (!/\bmermaid\b/.test(cls)) return;
        var pre = codeEl.parentNode;
        if (!pre || pre.tagName !== 'PRE') return;
        var div = document.createElement('div');
        div.className = 'mermaid';
        div.textContent = normalizeMermaidSource(codeEl.textContent || '');
        pre.parentNode.replaceChild(div, pre);
    });
}

/** 无盘符、无路径分隔符的「纯文件名 + 已知后缀」→ 相对工作区根解析 */
function isBareWorkspaceFilenameForLink(t) {
    var s = linkifyNormalizePathToken(String(t || '').trim());
    if (!s || /[/\\:]/.test(s)) return false;
    if (!/^[^\s<>'"]+$/.test(s)) return false;
    if (/^\.\.?$/.test(s)) return false;
    return linkifyKnownExtRegex().test(s);
}

function makeHrefFromAutoLinkToken(s) {
    var t = trimTrailingPathPunct(linkifyNormalizePathToken(String(s).trim()));
    if (!t) return null;
    if (/^https?:\/\//i.test(t)) return t;
    var m = /^([A-Za-z]):[\\/](.*)$/.exec(t);
    if (m) {
        var rest = (m[2] || '').replace(/\\/g, '/');
        return fileUrlFromFsPath(m[1].toUpperCase() + ':/' + rest);
    }
    if (t.charAt(0) === '/' && t.charAt(1) !== '/') {
        if (!workspaceRelativePathAutoLinkOk(t)) return null;
        var w = (typeof window.__WORK_DIR__ === 'string') ? window.__WORK_DIR__ : '';
        var abs = joinWorkDirAndRelativeSlashPath(w, t);
        if (abs) return fileUrlFromFsPath(abs);
    }
    if (workspaceRelativePathNoSlashAutoLinkOk(t)) {
        var wr = (typeof window.__WORK_DIR__ === 'string') ? window.__WORK_DIR__ : '';
        if (!wr) return null;
        var absRel = pathJoinBaseName(wr, t.replace(/\\/g, '/'));
        if (absRel) return fileUrlFromFsPath(absRel);
    }
    if (isBareWorkspaceFilenameForLink(t)) {
        var wk = (typeof window.__WORK_DIR__ === 'string') ? window.__WORK_DIR__ : '';
        if (!wk) return null;
        var absBare = pathJoinBaseName(wk, t);
        if (absBare) return fileUrlFromFsPath(absBare);
    }
    return null;
}

/**
 * 解析为可交给 /api/open-workspace-file 的路径：工作区相对、Windows/UNC 绝对路径（均由服务端校验须在 WORK_DIR 内）。
 */
function pathTokenToWorkspaceOpenRel(token) {
    var t = stripPathWrappingQuotes(trimTrailingPathPunct(linkifyNormalizePathToken(String(token || '').trim())));
    if (!t || /^https?:\/\//i.test(t)) return null;
    var w = (typeof window.__WORK_DIR__ === 'string') ? window.__WORK_DIR__ : '';
    var uncFlat = t.replace(/\//g, '\\');
    if (/^\\\\([^\\]+)\\([^\\]+)/i.test(uncFlat)) {
        return uncFlat;
    }
    var win = /^([A-Za-z]):[\\/](.*)$/.exec(t);
    if (win) {
        var rest = (win[2] || '').replace(/\\/g, '/');
        var absNorm = (win[1].toUpperCase() + ':/' + rest).replace(/\/+/g, '/');
        if (w) {
            var base = String(w).replace(/\\/g, '/').replace(/\/+$/, '');
            var absLower = absNorm.toLowerCase();
            var baseLower = base.toLowerCase();
            if (absLower.length >= baseLower.length && absLower.indexOf(baseLower) === 0) {
                return absNorm.slice(base.length).replace(/^\/+/, '');
            }
        }
        return absNorm;
    }
    if (!w) return null;
    if (t.charAt(0) === '/' && t.charAt(1) !== '/') {
        if (!workspaceRelativePathAutoLinkOk(t)) return null;
        return t.replace(/^\/+/, '').replace(/\\/g, '/');
    }
    if (t === '.env' && typeof window.__APP_DOTENV_PATH__ === 'string' && window.__APP_DOTENV_PATH__) {
        return window.__APP_DOTENV_PATH__;
    }
    if (workspaceRelativePathNoSlashAutoLinkOk(t)) return t.replace(/\\/g, '/');
    if (isBareWorkspaceFilenameForLink(t)) return t.replace(/\\/g, '/');
    return null;
}

function workspaceOpenDisplayLabel(original, wsRel) {
    var rel = String(wsRel || '').replace(/\\/g, '/').replace(/\/+$/, '');
    var name = rel.split('/').filter(Boolean).pop();
    if (name) return '@' + name;
    var raw = stripPathWrappingQuotes(trimTrailingPathPunct(original || ''));
    name = raw.replace(/\\/g, '/').replace(/\/+$/, '').split('/').filter(Boolean).pop();
    return name ? ('@' + name) : raw;
}

function escapeRegExpLiteral(s) {
    return String(s || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function quotePromptPath(p) {
    var t = stripPathWrappingQuotes(String(p || '').trim());
    if (!t) return '';
    return '"' + t.replace(/"/g, '\\"') + '"';
}

function getInputAbsolutePathRegex() {
    return /(["']?)([A-Za-z]:(?:\\|\/)(?:(?:[^\\/:*?"<>|\r\n]+)(?:\\|\/))*[^\\/:*?"<>|\r\n]+)\1/g;
}

function ensureInputPathChipHost() {
    var host = document.getElementById('input-path-chips');
    if (host || !messageInput) return host;
    var wrapper = messageInput.closest ? messageInput.closest('.input-wrapper') : null;
    var panel = wrapper && wrapper.parentNode;
    if (!panel || !wrapper) return null;
    host = document.createElement('div');
    host.id = 'input-path-chips';
    host.className = 'input-path-chips';
    panel.insertBefore(host, wrapper);
    return host;
}

function clearInputPathTokens() {
    Object.keys(inputPathTokenMap).forEach(function (k) { delete inputPathTokenMap[k]; });
    refreshInputPathChips();
}

function removeInputPathToken(label) {
    if (!label || !messageInput) return;
    delete inputPathTokenMap[label];
    var text = String(messageInput.value || '');
    var re = new RegExp('(?:\\s*)' + escapeRegExpLiteral(label), 'g');
    messageInput.value = text.replace(re, '').replace(/[ \t]{2,}/g, ' ').trimStart();
    refreshInputPathChips();
    autoResizeTextarea();
    try { messageInput.focus(); } catch (e) {}
}

function refreshInputPathChips() {
    var host = ensureInputPathChipHost();
    if (!host || !messageInput) return;
    var text = String(messageInput.value || '');
    var labels = Object.keys(inputPathTokenMap).filter(function (label) {
        return label && text.indexOf(label) >= 0;
    });
    if (!labels.length) {
        host.innerHTML = '';
        host.classList.remove('is-visible');
        return;
    }
    host.innerHTML = '';
    labels.forEach(function (label) {
        var stored = inputPathTokenMap[label];
        var rel = pathTokenToWorkspaceOpenRel(stored);
        if (!rel) return;
        var chip = document.createElement('span');
        chip.className = 'input-path-chip';
        var a = document.createElement('a');
        a.href = '#';
        a.className = 'input-path-chip-link msg-link-workspace-open';
        a.dataset.workspaceOpen = rel;
        a.textContent = label;
        a.setAttribute('data-ui-tip', String(stored || rel));
        var rm = document.createElement('button');
        rm.type = 'button';
        rm.className = 'input-path-chip-remove';
        rm.setAttribute('aria-label', '移除 ' + label);
        rm.setAttribute('data-ui-tip', '移除文件路径');
        rm.textContent = '×';
        rm.addEventListener('click', function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            removeInputPathToken(label);
        });
        chip.appendChild(a);
        chip.appendChild(rm);
        host.appendChild(chip);
    });
    host.classList.toggle('is-visible', !!host.children.length);
}

function rewriteInputWorkspacePaths() {
    if (!messageInput || inputPathRewriteGuard) return;
    var raw = String(messageInput.value || '');
    var changed = false;
    var next = raw.replace(getInputAbsolutePathRegex(), function (match, q, path) {
        var rel = pathTokenToWorkspaceOpenRel(path);
        if (!rel) return match;
        var label = workspaceOpenDisplayLabel(path, rel);
        if (!label) return match;
        inputPathTokenMap[label] = stripPathWrappingQuotes(path);
        changed = true;
        return label;
    });
    if (changed && next !== raw) {
        var wasFocused = document.activeElement === messageInput;
        inputPathRewriteGuard = true;
        messageInput.value = next;
        if (wasFocused) {
            var pos = next.length;
            try { messageInput.setSelectionRange(pos, pos); } catch (e) {}
        }
        inputPathRewriteGuard = false;
    }
    refreshInputPathChips();
}

function expandInputPathTokens(text) {
    var out = String(text || '');
    Object.keys(inputPathTokenMap)
        .sort(function (a, b) { return b.length - a.length; })
        .forEach(function (label) {
            var stored = inputPathTokenMap[label];
            if (!stored || out.indexOf(label) < 0) return;
            out = out.replace(new RegExp(escapeRegExpLiteral(label), 'g'), quotePromptPath(stored));
        });
    return out;
}

/** 整段文本是否仅为可链转的 Windows 绝对路径（用于行内 code 内路径） */
function isEntireTextNodeWindowsPath(raw) {
    var t = trimTrailingPathPunct(linkifyNormalizePathToken(String(raw || '').trim()));
    if (!t) return false;
    return /^([A-Za-z]):[\\/](?:(?:[^\\/:*?"<>|\r\n]+)(?:\\|\/))*[^\\/:*?"<>|\r\n]+$/i.test(t);
}

function isEntireBareFilenameLinkable(raw) {
    var t = trimTrailingPathPunct(linkifyNormalizePathToken(String(raw || '').trim()));
    return isBareWorkspaceFilenameForLink(t);
}

/** 行内 code 内整段为 `/工作区相对/路径.ext` 时亦允许链转（否则反引号路径永不可点） */
function isEntireWorkspaceSlashPathLinkable(raw) {
    var t = trimTrailingPathPunct(linkifyNormalizePathToken(String(raw || '').trim()));
    return workspaceRelativePathAutoLinkOk(t);
}

function isEntireWorkspaceRelativePathLinkable(raw) {
    var t = trimTrailingPathPunct(linkifyNormalizePathToken(String(raw || '').trim()));
    return workspaceRelativePathNoSlashAutoLinkOk(t);
}

/** 行内 code 内整段为 UNC \\server\share\... 时允许「本机打开」链转 */
function isEntireTextNodeUncPath(raw) {
    var t = trimTrailingPathPunct(linkifyNormalizePathToken(String(raw || '').trim()));
    if (!t) return false;
    var u = t.replace(/\//g, '\\');
    return /^\\\\[^\\]+\\[^\\]+(?:\\[^\\]*)*$/i.test(u);
}

var _assistMsgLinkifyRe = null;
function getAssistMsgLinkifyRegex() {
    if (!_assistMsgLinkifyRe) {
        // 「/路径」前仅排除 ASCII 字母，避免 2023/文件、中文后接 / 等无法匹配；仍可抑制 ARPU/DOU（U 为字母）
        _assistMsgLinkifyRe = new RegExp(
            '(https?:\\/\\/[^\\s<>\'"]+|' +
            '\\\\\\\\(?:(?:[^\\\\\\/:*?"<>|\\r\\n]+)\\\\)+(?:[^\\\\\\/:*?"<>|\\r\\n]+)|' +
            '[A-Za-z]:(?:\\\\|\\/)(?:(?:[^\\\\/:*?"<>|\\r\\n]+)(?:\\\\|\\/))*[^\\\\/:*?"<>|\\r\\n]+|' +
            '(?<![A-Za-z])\\/(?![\\s\\/])[^\\s<>\'"]+|' +
            '(?<![A-Za-z0-9./\\\\])(?:[^\\s<>\'"/\\\\:]+(?:[\\\\/][^\\s<>\'"/\\\\:]+)+\\.(' + LINKIFY_EXT_FRAGMENT + ')\\b)|' +
            '(?<![A-Za-z0-9./\\\\])([^\\s<>\'"/\\\\:]+?\\.(' + LINKIFY_EXT_FRAGMENT + ')\\b))',
            'gi'
        );
    }
    return _assistMsgLinkifyRe;
}

function linkifySingleTextNode(textNode) {
    var raw = textNode.nodeValue;
    if (!raw) return;
    var parent = textNode.parentElement;
    if (!parent || parent.closest('a, pre, script, style, textarea, svg')) return;
    if (parent.closest('code') && !isEntireTextNodeWindowsPath(raw) && !isEntireBareFilenameLinkable(raw) && !isEntireWorkspaceSlashPathLinkable(raw) && !isEntireWorkspaceRelativePathLinkable(raw) && !isEntireTextNodeUncPath(raw)) return;
    var rawForLink = linkifyNormalizePathToken(raw);
    var re = getAssistMsgLinkifyRegex();
    re.lastIndex = 0;
    var parts = [];
    var last = 0;
    var m;
    while ((m = re.exec(rawForLink)) !== null) {
        var matchStart = m.index;
        var matchEnd = m.index + m[0].length;
        var qBefore = rawForLink.charAt(matchStart - 1);
        var qAfter = rawForLink.charAt(matchEnd);
        if ((qBefore === '"' || qBefore === "'") && qAfter === qBefore) {
            matchStart -= 1;
            matchEnd += 1;
        }
        if (matchStart > last) parts.push({ k: 't', s: rawForLink.slice(last, matchStart) });
        parts.push({ k: 'l', s: m[0] });
        last = matchEnd;
    }
    if (last < rawForLink.length) parts.push({ k: 't', s: rawForLink.slice(last) });
    var hasLink = false;
    for (var pi = 0; pi < parts.length; pi++) {
        if (parts[pi].k === 'l') { hasLink = true; break; }
    }
    if (!hasLink) return;
    var frag = document.createDocumentFragment();
    parts.forEach(function (p) {
        if (p.k === 't') frag.appendChild(document.createTextNode(p.s));
        else {
            var wsRel = pathTokenToWorkspaceOpenRel(p.s);
            var show = trimTrailingPathPunct(p.s);
            if (wsRel) {
                var aw = document.createElement('a');
                aw.href = '#';
                aw.setAttribute('data-workspace-open', wsRel);
                aw.className = 'msg-link-auto msg-link-workspace-open';
                aw.setAttribute('data-ui-tip', '在本机打开（工作区文件）');
                bindUiHoverTip(aw);
                aw.textContent = show || p.s;
                frag.appendChild(aw);
                if (p.s.length > (show || '').length) {
                    frag.appendChild(document.createTextNode(p.s.slice((show || '').length)));
                }
            } else {
                var href = makeHrefFromAutoLinkToken(p.s);
                if (!href) frag.appendChild(document.createTextNode(p.s));
                else {
                    var ah = document.createElement('a');
                    ah.href = href;
                    ah.target = '_blank';
                    ah.rel = 'noopener noreferrer';
                    ah.className = 'msg-link-auto';
                    ah.textContent = show || p.s;
                    frag.appendChild(ah);
                    if (p.s.length > (show || '').length) {
                        frag.appendChild(document.createTextNode(p.s.slice((show || '').length)));
                    }
                }
            }
        }
    });
    textNode.parentNode.replaceChild(frag, textNode);
}

function linkifyAssistantTextNodes(root) {
    if (!root) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    var batch = [];
    var n;
    while ((n = walker.nextNode())) {
        var p = n.parentElement;
        if (!p || p.closest('a, pre, script, style, textarea, .mermaid')) continue;
        if (p.closest('code') && !isEntireTextNodeWindowsPath(n.nodeValue) && !isEntireBareFilenameLinkable(n.nodeValue) && !isEntireWorkspaceSlashPathLinkable(n.nodeValue) && !isEntireWorkspaceRelativePathLinkable(n.nodeValue) && !isEntireTextNodeUncPath(n.nodeValue)) continue;
        var nv = n.nodeValue;
        var nvNorm = linkifyNormalizePathToken(nv);
        if (!nv || (!/https?:\/\/|[A-Za-z]:[\\/]|\/\S/.test(nvNorm) && !nvNorm.startsWith('\\\\') && !linkifyKnownExtRegex().test(nvNorm))) continue;
        batch.push(n);
    }
    batch.forEach(linkifySingleTextNode);
}

function scheduleMermaidRun(root) {
    registerMermaidLazy(root);
}

async function runMermaidElementOnce(el) {
    if (!el || !window.mermaid || !el.isConnected) return;
    if (el.getAttribute('data-processed') === 'true' || el.classList.contains('mermaid-error')) return;
    ensureMermaidInitialized();
    var cleaned = normalizeMermaidSource(el.textContent || '');
    if (!cleaned) return;
    el.textContent = cleaned;
    if (!el.id) el.id = 'mermaid-embed-' + (++mermaidIdSeq);
    try {
        await mermaid.parse(cleaned);
    } catch (errParse) {
        showMermaidRenderError(el, cleaned, errParse);
        return;
    }
    try {
        await mermaid.run({ nodes: [el], suppressErrors: false });
    } catch (errRun) {
        showMermaidRenderError(el, cleaned, errRun);
    }
}

function ensureMermaidIoObserver() {
    if (mermaidIoObserver || typeof IntersectionObserver === 'undefined') return null;
    mermaidIoObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
            if (!en.isIntersecting) return;
            var el = en.target;
            if (!el.classList.contains('mermaid') || el.getAttribute('data-processed') === 'true') {
                if (mermaidIoObserver) mermaidIoObserver.unobserve(el);
                return;
            }
            if (mermaidIoObserver) mermaidIoObserver.unobserve(el);
            runMermaidElementOnce(el);
        });
    }, { root: null, rootMargin: '100px 0px 160px 0px', threshold: 0 });
    return mermaidIoObserver;
}

function registerMermaidLazy(root) {
    if (!root || !window.mermaid) return;
    ensureMermaidInitialized();
    var nodes = Array.from(root.querySelectorAll('.mermaid:not([data-processed]):not(.mermaid-error)'));
    if (!nodes.length) return;
    var obs = ensureMermaidIoObserver();
    if (!obs) {
        requestAnimationFrame(function () {
            (async function () {
                for (var i = 0; i < nodes.length; i += 1) {
                    await runMermaidElementOnce(nodes[i]);
                }
            })();
        });
        return;
    }
    nodes.forEach(function (el) {
        try {
            obs.observe(el);
        } catch (e) {
            runMermaidElementOnce(el);
        }
    });
}

function wrapMessageTables(container) {
    if (!container) return;
    container.querySelectorAll('table').forEach(function (table) {
        var parent = table.parentElement;
        if (parent && parent.classList && parent.classList.contains('msg-table-scroll')) return;
        var wrap = document.createElement('div');
        wrap.className = 'msg-table-scroll';
        if (table.parentNode) table.parentNode.insertBefore(wrap, table);
        wrap.appendChild(table);
    });
}

function enhanceAssistantMessageContent(div) {
    if (!div) return;
    wrapMessageTables(div);
    upgradeMermaidBlocks(div);
    linkifyAssistantTextNodes(div);
    scheduleMermaidRun(div);
}

let markedOptionsApplied = false;
function renderMarkdown(text) {
    if (!text) return '';
    if (typeof marked !== 'undefined' && !markedOptionsApplied) {
        markedOptionsApplied = true;
        try {
            marked.setOptions({ breaks: true, mangle: false, headerIds: false });
        } catch (e) { /* ignore */ }
    }
    return marked.parse(text, { mangle: false, headerIds: false });
}

const TRACE_ROW = {
    'log-entry':   { label: '信息', c: 'feed--log' },
    'tool-call':   { label: '工具', c: 'feed--tool' },
    'error-log':   { label: '错误', c: 'feed--err' },
    'llm-response':{ label: '回复', c: 'feed--llm2' },
    'llm-reasoning':{ label: '思考', c: 'feed--llm' },
    'compact-summary': { label: '压缩', c: 'feed--cmp' },
    'context-trim': { label: '裁剪', c: 'feed--trim' },
    'context-summary': { label: '压缩', c: 'feed--cmp' },
    'key-context': { label: '要点', c: 'feed--key' },
    'status':      { label: '状态', c: 'feed--st' },
};

const envKeepLines = Number(window.__UI_LOG_TRUNCATE_KEEP_LINES__);
const LOG_TRUNCATE_KEEP_LINES = Number.isFinite(envKeepLines) && envKeepLines > 0 ? Math.floor(envKeepLines) : 100;
const LOG_TRUNCATE_HEAD_LINES = LOG_TRUNCATE_KEEP_LINES;
const LOG_TRUNCATE_TAIL_LINES = LOG_TRUNCATE_KEEP_LINES;
const LOG_TRUNCATE_HEAD_CHARS = 12000;
const LOG_TRUNCATE_TAIL_CHARS = 12000;

function toolCallDraftKey(parsed) {
    var ri = parsed && parsed.react_iter != null ? String(parsed.react_iter) : '';
    var idx = parsed && parsed.tool_call_index != null ? String(parsed.tool_call_index) : (parsed && parsed.index != null ? String(parsed.index) : '0');
    return ri + ':' + idx;
}

function findToolDraftRow(ctx, parsed) {
    var key = toolCallDraftKey(parsed);
    if (!key) return null;
    var body = getProcessBody(ctx);
    if (!body || typeof CSS === 'undefined' || !CSS.escape) return null;
    try { return body.querySelector('.feed-item.feed--tool[data-tool-draft-key="' + CSS.escape(key) + '"]'); } catch (e) { return null; }
}

function setToolRowText(row, text, ctx, runSessionId) {
    if (!row) return;
    var sc = row.querySelector('.feed-chunk-scroller');
    if (sc) sc.textContent = truncateLogTextForUi(text);
    var ch = row.querySelector('.feed-chunk');
    if (ch) {
        // 工具条目流式生成时也放开高度限制
        ch.classList.add('is-streaming');
        refreshFeedChunkOverflow(ch);
    }
    // 遵守自动跟随，不强制拖拽
    if (!replayingMessages) scrollContentAreaIfFollow(ctx, runSessionId);
}

// 移除临时状态消息（移除整个 feed-item 条目）
function removeTemporaryStatus(ctx) {
    var body = getProcessBody(ctx);
    if (!body) return;
    var tempStatuses = body.querySelectorAll('[data-temporary-status="1"]');
    tempStatuses.forEach(function(el) {
        var row = el.closest ? el.closest('.feed-item') : null;
        if (row) row.remove(); else el.remove();
    });
}

function appendToolCallDelta(ctx, parsed, runSessionId) {
    var key = toolCallDraftKey(parsed);
    if (!key) return;
    var row = findToolDraftRow(ctx, parsed);
    if (!row) {
        var so = null;
        if (parsed.react_iter != null && Number.isFinite(Number(parsed.react_iter))) so = { reactIter: Number(parsed.react_iter) };
        var scNew = createProcessFeedRow(ctx, 'tool-call', '工具调用生成中...', so, runSessionId, '');
        row = scNew && scNew.closest ? scNew.closest('.feed-item') : null;
        if (row) row.setAttribute('data-tool-draft-key', key);
    }
    if (!row) return;
    if (parsed.id) row.dataset.pendingToolCallId = String(parsed.id);
    
    // 收到 tool_call_delta 时，移除临时状态，展开折叠的 process-aggregate
    removeTemporaryStatus(ctx);
    var agg = row.closest('.process-aggregate');
    if (agg && agg.classList.contains('is-collapsed')) {
        agg.classList.remove('is-collapsed');
        var topN = agg.querySelector('.process-aggregate-top');
        if (topN) topN.setAttribute('aria-expanded', 'true');
    }
    
    // 累积工具名称和参数
    if (parsed.name_delta) {
        row.dataset.pendingToolName = (row.dataset.pendingToolName || '') + String(parsed.name_delta);
    }
    if (parsed.arguments_delta) {
        row.dataset.pendingToolArgs = (row.dataset.pendingToolArgs || '') + String(parsed.arguments_delta);
    }
    
    // 生成显示文本
    var toolName = row.dataset.pendingToolName || '';
    var argsRaw = row.dataset.pendingToolArgs || '';
    var displayText = '工具调用生成中...';
    
    if (toolName) {
        // 流式显示：工具名 + 参数原始文本（逐步增长）
        var argsPreview = argsRaw;
        displayText = toolName + '(' + argsPreview + '\n生成中...';
    }
    setToolRowText(row, displayText, ctx, runSessionId);
}
function formatToolCommandLine(tool, args, commandPreview) {
    if (commandPreview != null && String(commandPreview).trim()) return String(commandPreview).trim();
    var name = String(tool || 'tool');
    var a = args && typeof args === 'object' && !Array.isArray(args) ? args : {};
    function j(v) { try { return JSON.stringify(v); } catch (e) { return String(v); } }
    function pair(k, v) {
        if ((k === 'content' || k === 'contents') && typeof v === 'string' && v.length > 240) v = '<' + v.length + ' chars>';
        return j(k) + ': ' + j(v);
    }
    var preferred = ['path','target_directory','file_path','directory','root','command','args','url','start_line','end_line','pattern','query','search','replace','old_string','new_string','working_dir','timeout','temporary','content','contents'];
    var keys = [];
    // 路径参数去重：只保留第一个存在的路径参数
    var pathKeys = ['path', 'target_directory', 'file_path', 'directory', 'root'];
    var firstPathKey = null;
    pathKeys.forEach(function (k) {
        if (!firstPathKey && Object.prototype.hasOwnProperty.call(a, k)) firstPathKey = k;
    });
    preferred.forEach(function (k) {
        if (Object.prototype.hasOwnProperty.call(a, k)) {
            if (pathKeys.indexOf(k) >= 0) {
                if (k === firstPathKey) keys.push(k);
            } else {
                keys.push(k);
            }
        }
    });
    Object.keys(a).sort().forEach(function (k) { if (keys.indexOf(k) < 0) keys.push(k); });
    if (name === 'run_shell') {
        var b = {};
        Object.keys(a).forEach(function (k) { b[k] = a[k]; });
        var cmd = b.command != null ? String(b.command) : '';
        if (Array.isArray(b.args) && b.args.length) cmd += ' ' + b.args.map(function (x) { return String(x); }).join(' ');
        b.command = cmd.trim();
        delete b.args;
        a = b;
        keys = [];
        preferred.forEach(function (k) { if (Object.prototype.hasOwnProperty.call(a, k)) keys.push(k); });
        Object.keys(a).sort().forEach(function (k) { if (keys.indexOf(k) < 0) keys.push(k); });
    }
    return name + '(' + keys.map(function (k) { return pair(k, a[k]); }).join(', ') + ')';
}

function formatToolPendingLine(tool, args, commandPreview) {
    var cmd = commandPreview != null ? String(commandPreview).trim() : '';
    if (!cmd) return '执行中...';
    return cmd + '\n执行中...';
}

function formatToolDoneLine(tool, args, result, commandPreview) {
    return formatToolCommandLine(tool, args, commandPreview) + '\n执行结果\n' + String(result != null ? result : '');
}

function appendToolPendingRow(ctx, parsed, runSessionId) {
    var line = formatToolPendingLine(parsed.tool, parsed.args, parsed.command_preview);
    var so = null;
    if (parsed.react_iter != null && Number.isFinite(Number(parsed.react_iter))) so = { reactIter: Number(parsed.react_iter) };
    var draft = findToolDraftRow(ctx, parsed);
    if (draft) {
        if (parsed.tool_call_id != null && String(parsed.tool_call_id) !== '') draft.setAttribute('data-tool-call-id', String(parsed.tool_call_id));
        draft.removeAttribute('data-tool-draft-key');
        draft.dataset.commandPreview = parsed.command_preview != null ? String(parsed.command_preview) : '';
        setToolRowText(draft, line, ctx, runSessionId);
        return;
    }
    var sc = createProcessFeedRow(ctx, 'tool-call', line, so, runSessionId, parsed.tool_call_id);
    var row = sc && sc.closest ? sc.closest('.feed-item') : null;
    if (row) row.dataset.commandPreview = parsed.command_preview != null ? String(parsed.command_preview) : '';
}

function appendToolCommandDelta(ctx, parsed, runSessionId) {
    var tid = parsed.tool_call_id != null ? String(parsed.tool_call_id) : '';
    if (!tid) return;
    var body = getProcessBody(ctx);
    var row = null;
    if (body && typeof CSS !== 'undefined' && CSS.escape) {
        try { row = body.querySelector('.feed-item.feed--tool[data-tool-call-id="' + CSS.escape(tid) + '"]'); } catch (e) { row = null; }
    }
    if (!row) {
        appendToolPendingRow(ctx, { tool_call_id: tid, command_preview: '', react_iter: parsed.react_iter }, runSessionId);
        body = getProcessBody(ctx);
        if (body && typeof CSS !== 'undefined' && CSS.escape) {
            try { row = body.querySelector('.feed-item.feed--tool[data-tool-call-id="' + CSS.escape(tid) + '"]'); } catch (e2) { row = null; }
        }
    }
    if (!row) return;
    row.dataset.commandPreview = (row.dataset.commandPreview || '') + String(parsed.delta || '');
    var text = formatToolPendingLine(parsed.tool, parsed.args, row.dataset.commandPreview);
    var sc = row.querySelector('.feed-chunk-scroller');
    if (sc) sc.textContent = truncateLogTextForUi(text);
    var ch = row.querySelector('.feed-chunk');
    if (ch) refreshFeedChunkOverflow(ch);
    if (!replayingMessages) scrollContentAreaIfFollow(ctx, runSessionId);
}
function upsertToolCallResult(ctx, parsed, runSessionId) {
    var tid = parsed.tool_call_id != null ? String(parsed.tool_call_id) : '';
    var body = getProcessBody(ctx);
    var row = null;
    if (tid && body && typeof CSS !== 'undefined' && CSS.escape) {
        try { row = body.querySelector('.feed-item.feed--tool[data-tool-call-id="' + CSS.escape(tid) + '"]'); } catch (e) { row = null; }
    }
    if (!row) row = findToolDraftRow(ctx, parsed);
    var cmdPreview = parsed.command_preview;
    if ((!cmdPreview || !String(cmdPreview).trim()) && row && row.dataset.commandPreview) cmdPreview = row.dataset.commandPreview;
    var text = formatToolDoneLine(parsed.tool, parsed.args, parsed.result, cmdPreview);
    if (row) {
        if (tid) row.setAttribute('data-tool-call-id', tid);
        row.removeAttribute('data-tool-draft-key');
        row.dataset.commandPreview = cmdPreview != null ? String(cmdPreview) : '';
        var sc = row.querySelector('.feed-chunk-scroller');
        if (sc) sc.textContent = truncateLogTextForUi(text);
        var ch = row.querySelector('.feed-chunk');
        if (ch) refreshFeedChunkOverflow(ch);
        var agg = body.closest('.process-aggregate');
        refreshAggregateStatsSmart(agg);
        if (!replayingMessages) scrollContentAreaIfFollow(ctx, runSessionId);
        return;
    }
    var ri = uiEventReactIter(parsed);
    appendLog(ctx, text, 'tool-call', runSessionId, ri);
}

/** 去掉首尾「空白行」（整行仅空格/制表也不保留），保留首行正文缩进与中间空行 */
function trimSurroundingBlankLines(raw) {
    var text = (raw == null) ? '' : String(raw);
    if (!text) return text;
    var lines = text.split('\n');
    var start = 0;
    var end = lines.length;
    while (start < end && lines[start].trim() === '') start++;
    while (end > start && lines[end - 1].trim() === '') end--;
    if (start >= end) return '';
    return lines.slice(start, end).join('\n');
}

function truncateLogTextForUi(raw) {
    const text = (raw == null) ? '' : String(raw);
    if (!text) return text;
    const lines = text.split('\n');
    if (lines.length > LOG_TRUNCATE_HEAD_LINES + LOG_TRUNCATE_TAIL_LINES) {
        const head = lines.slice(0, LOG_TRUNCATE_HEAD_LINES).join('\n');
        const tail = lines.slice(-LOG_TRUNCATE_TAIL_LINES).join('\n');
        const omitted = lines.length - LOG_TRUNCATE_HEAD_LINES - LOG_TRUNCATE_TAIL_LINES;
        return head + '\n\n... [中间省略 ' + omitted + ' 行] ...\n\n' + tail;
    }
    if (text.length > LOG_TRUNCATE_HEAD_CHARS + LOG_TRUNCATE_TAIL_CHARS) {
        const head = text.slice(0, LOG_TRUNCATE_HEAD_CHARS);
        const tail = text.slice(-LOG_TRUNCATE_TAIL_CHARS);
        const omitted = text.length - LOG_TRUNCATE_HEAD_CHARS - LOG_TRUNCATE_TAIL_CHARS;
        return head + '\n\n... [中间省略约 ' + omitted + ' 字符] ...\n\n' + tail;
    }
    return text;
}

function createProcessFeedRow(ctx, type, initialText, streamOpts, runSessionId, toolCallIdOpt) {
    streamOpts = streamOpts || {};
    if (type == null) type = 'log-entry';
    stripWelcome(ctx);
    const body = getProcessBody(ctx);
    if (!body) return;
    const meta = TRACE_ROW[type] || TRACE_ROW['log-entry'];
    const row = document.createElement('div');
    row.className = 'feed-item ' + meta.c;
    row.setAttribute('data-log-type', type);
    if (toolCallIdOpt != null && String(toolCallIdOpt) !== '') row.setAttribute('data-tool-call-id', String(toolCallIdOpt));
    row.innerHTML = '<div class="feed-row">'
        + '<span class="feed-label">' + meta.label + '</span>'
        + '<div class="feed-chunk">'
        + '<div class="feed-chunk-scroller"></div></div></div>';
    const chunk = row.querySelector('.feed-chunk');
    const sc = row.querySelector('.feed-chunk-scroller');
    var txtForUi = initialText;
    if (type === 'llm-reasoning' || type === 'llm-response') txtForUi = trimSurroundingBlankLines(txtForUi);
    sc.textContent = truncateLogTextForUi(txtForUi);
    if (streamOpts.streaming && (type === 'llm-reasoning' || type === 'llm-response')) chunk.classList.add('is-streaming');
    bindFeedChunkInteraction(chunk);
    bindFeedChunkScrollChain(sc);
    body.appendChild(row);
    if (ctx && ctx.currentTurn && body.classList && body.classList.contains('subagent-turn-process')) {
        markSubagentTurnHasProcess(ctx.currentTurn);
    }
    if (type === 'error-log') {
        var errHint = document.createElement('div');
        errHint.className = 'feed-error-contact-hint';
        errHint.textContent = '如需帮助或反馈，请联系GitHub @sugarfreeecho';
        body.appendChild(errHint);
    }
    const agg = body.closest('.process-aggregate');
    if (streamOpts.reactIter != null && Number.isFinite(Number(streamOpts.reactIter))) {
        var ri = Math.max(1, Math.floor(Number(streamOpts.reactIter)));
        row.setAttribute('data-react-iter', String(ri));
        bumpAggregateMaxReactIter(agg, ri);
    }
    if (agg && agg.classList.contains('is-collapsed')) {
        updateProcessBrief(agg);
    }
    else requestAnimationFrame(function () { scheduleFeedChunkOverflowRefresh(chunk); });
    refreshAggregateStatsSmart(agg);
    if (!streamOpts.streaming) scrollContentAreaIfFollow(ctx, runSessionId);
    return sc;
}

function appendLlmStreamDelta(ctx, ev, runSessionId) {
    if (!ctx || !ctx.llm) return;
    // 收到 reasoning/content 增量时，移除"正在思考中..."条目
    removeTemporaryStatus(ctx);
    const l = ctx.llm;
    const iter = ev.react_iter;
    const seq = Number(ev.stream_seq || 0);
    if (l.llmDeltaLastSeq !== null && seq !== l.llmDeltaLastSeq) finalizeLlmStreamChunks(ctx);
    l.llmDeltaLastSeq = seq;
    const part = ev.type === 'llm_reasoning_delta' ? 'reasoning' : 'response';
    const delta = String(ev.delta || '');
    if (!delta) return;
    if (iter != null) {
        var body0 = getProcessBody(ctx);
        if (body0) bumpAggregateMaxReactIter(body0.closest('.process-aggregate'), iter);
    }
    const streamOpt = { streaming: true };
    if (iter != null && Number.isFinite(Number(iter))) streamOpt.reactIter = Number(iter);
    if (part === 'reasoning') {
        if (l.llmStreamReasoningIter !== iter) {
            flushLlmDeltaText(ctx);
            l.llmStreamReasoningIter = iter;
            l.llmStreamReasoningScroller = createProcessFeedRow(ctx, 'llm-reasoning', '', streamOpt, runSessionId);
        }
        if (!l.llmStreamReasoningScroller) return;
        l.llmPendingReasoningDelta = (l.llmPendingReasoningDelta || '') + delta;
    } else {
        if (l.llmStreamResponseIter !== iter) {
            flushLlmDeltaText(ctx);
            l.llmStreamResponseIter = iter;
            l.llmStreamResponseScroller = createProcessFeedRow(ctx, 'llm-response', '', streamOpt, runSessionId);
        }
        if (!l.llmStreamResponseScroller) return;
        l.llmPendingResponseDelta = (l.llmPendingResponseDelta || '') + delta;
    }
    scheduleLlmDeltaFlush(ctx, runSessionId);
}

function upsertLlmFeedRow(ctx, content, logType, runSessionId, reactIter) {
    if (!ctx) return null;
    var ri = reactIter != null && Number.isFinite(Number(reactIter)) ? Math.max(1, Math.floor(Number(reactIter))) : null;
    var body = getProcessBody(ctx);
    var txt = truncateLogTextForUi(trimSurroundingBlankLines(String(content || '')));
    if (!txt.trim()) return null;
    if (body && ri != null) {
        var existing = body.querySelector('.feed-item[data-log-type="' + logType + '"][data-react-iter="' + ri + '"]');
        if (existing) {
            var sc = existing.querySelector('.feed-chunk-scroller');
            var ch = existing.querySelector('.feed-chunk');
            if (sc) sc.textContent = txt;
            if (ch) {
                ch.classList.remove('is-streaming');
                scheduleFeedChunkOverflowRefresh(ch);
            }
            if (ctx.llm) resetLlmState(ctx);
            scrollContentAreaIfFollow(ctx, runSessionId);
            return sc;
        }
    }
    if (ctx.llm) resetLlmState(ctx);
    return appendLog(ctx, content, logType, runSessionId, ri);
}

function appendMessage(ctx, role, content, meta, runSessionId) {
    meta = meta || {};
    stripWelcome(ctx);
    const wrap = document.createElement('div');
    wrap.className = 'msg-wrap msg-wrap--' + (role === 'user' ? 'user' : 'assistant');
    if (role === 'assistant') wrap.classList.add('msg-wrap--answer-frame');
    if (meta.eventIndex != null) wrap.setAttribute('data-event-index', String(meta.eventIndex));
    var tTrunc = meta.turnTruncateIdx;
    if (tTrunc == null) { if (role === 'user' && meta.eventIndex != null) tTrunc = meta.eventIndex; }
    if (tTrunc != null && tTrunc >= 0) wrap.setAttribute('data-truncate-from', String(tTrunc));
    if (role === 'user') {
        if (meta.eventIndex != null && meta.eventIndex >= 0) {
            wrap.id = 'user-msg-' + meta.eventIndex;
        } else {
            const n = (ctx.stream || chatContainer).querySelectorAll('.msg-wrap--user').length;
            wrap.id = 'user-msg-' + n;
        }
    }
    const div = document.createElement('div');
    div.className = 'message ' + (role === 'user' ? 'user' : 'assistant');
    var rawStr = content == null ? '' : String(content);
    messageRawMarkdown.set(wrap, rawStr);
    if (role === 'user') {
        var lineCount = rawStr.split('\n').length;
        if (lineCount > 10) {
            wrap.classList.add('has-turn-process');
            div.classList.add('is-collapsible');
            // 摘要
            var sum = document.createElement('div');
            sum.className = 'user-msg-summary';
            sum.textContent = rawStr.split('\n').slice(0, 10).join('\n') + '\n...';
            linkifyAssistantTextNodes(sum);
            // 完整
            var ful = document.createElement('div');
            ful.className = 'user-msg-full';
            ful.textContent = rawStr;
            linkifyAssistantTextNodes(ful);
            // chevron
            var ch = document.createElement('div');
            ch.className = 'user-msg-chevron';
            var arrow = document.createElement('span');
            arrow.className = 'chevron-arrow';
            ch.appendChild(arrow);
            ch.addEventListener('click', function(e) {
                e.stopPropagation();
                wrap.classList.toggle('user-msg-expanded');
            });
            div.appendChild(sum);
            div.appendChild(ful);
            div.appendChild(ch);
        } else {
            div.textContent = rawStr;
            linkifyAssistantTextNodes(div);
        }
    }
        else {
        div.innerHTML = renderMarkdown(rawStr);
        enhanceAssistantMessageContent(div);
    }
    wrap.appendChild(div);
    attachMessageToolbar(wrap, role);
    (ctx.stream || chatContainer).appendChild(wrap);
    if (role === 'assistant') {
        if (ctx.currentProcessGroup && ctx.currentProcessGroup.isConnected) {
            ctx.currentProcessGroup.classList.add('is-collapsed');
            const ttop = ctx.currentProcessGroup.querySelector('.process-aggregate-top');
            if (ttop) ttop.setAttribute('aria-expanded', 'false');
            updateProcessBrief(ctx.currentProcessGroup);
        }
        sealProcessGroup(ctx);
    }
    if (role === 'user' && !replayingMessages) rebuildToc();
    if (!replayingMessages) {
        if (role === 'user') scrollChatToBottomIfFollow(runSessionId, { force: true });
        else scrollChatToBottomIfFollow(runSessionId, {});
    }
}

function handleTraceChunkClick(e) {
    if (e) e.stopPropagation();
    this.classList.toggle('expanded');
    var self = this;
    requestAnimationFrame(function () {
        refreshFeedChunkOverflow(self);
        registerMermaidLazy(self);
    });
}

function bindFeedChunkInteraction(ch) {
    ch.removeEventListener('click', handleTraceChunkClick);
    ch.addEventListener('click', handleTraceChunkClick);
}

function bindExistingLogs(root) {
    const el = root || getVisibleChatStream() || chatContainer;
    if (!el) return;
    el.querySelectorAll('.feed-chunk').forEach(function (ch) {
        bindFeedChunkInteraction(ch);
        scheduleFeedChunkOverflowRefresh(ch);
        const sc = ch.querySelector('.feed-chunk-scroller');
        if (sc) bindFeedChunkScrollChain(sc);
    });
    el.querySelectorAll('.process-aggregate').forEach(function (agg) {
        bindProcessAggregate(agg);
        if (agg.classList.contains('is-collapsed')) updateProcessBrief(agg);
        refreshAggregateStatsSmart(agg);
    });
    el.querySelectorAll('.process-aggregate-brief').forEach(bindProcessBriefScrollChain);
}

function appendLog(ctx, content, type, runSessionId, reactIter) {
    if (type == null) type = 'log-entry';
    const tStr = (content == null) ? '' : String(content);
    if ((type === 'llm-reasoning' || type === 'llm-response') && !trimSurroundingBlankLines(tStr).trim()) return null;
    var so = null;
    if (reactIter != null && Number.isFinite(Number(reactIter))) so = { reactIter: Number(reactIter) };
    return createProcessFeedRow(ctx, type, tStr, so, runSessionId);
}

function flushProgressDeltaText(ctx, logType) {
    if (!ctx || !ctx.progressStream) return;
    var st = ctx.progressStream[logType];
    if (!st) return;
    if (st.flushRaf) {
        cancelAnimationFrame(st.flushRaf);
        st.flushRaf = 0;
    }
    if (st.pending && st.scroller && st.scroller.isConnected) {
        var merged = (st.scroller.textContent || '') + st.pending;
        st.scroller.textContent = truncateLogTextForUi(merged);
        var ch = st.scroller.closest('.feed-chunk');
        if (ch) refreshFeedChunkOverflow(ch);
    }
    st.pending = '';
}

function finalizeProgressStreamChunks(ctx) {
    if (!ctx) return;
    var types = ctx.progressStream ? Object.keys(ctx.progressStream) : [];
    for (var i = 0; i < types.length; i += 1) flushProgressDeltaText(ctx, types[i]);
    var streamRoot = (ctx._subagentBody && ctx._subagentBody.isConnected) ? ctx._subagentBody : ctx.stream;
    if (streamRoot) {
        streamRoot.querySelectorAll('.feed-item .feed-chunk.is-streaming').forEach(function (ch) {
            ch.classList.remove('is-streaming');
            refreshFeedChunkOverflow(ch);
        });
    }
    ctx.progressStream = {};
}

function scheduleProgressDeltaFlush(ctx, runSessionId, logType) {
    if (!ctx || !ctx.progressStream) return;
    var st = ctx.progressStream[logType];
    if (!st || st.flushRaf) return;
    st.flushRaf = requestAnimationFrame(function () {
        st.flushRaf = 0;
        flushProgressDeltaText(ctx, logType);
        followStreamProcessScroll(ctx, runSessionId);
    });
}

/** 每个压缩阶段（裁剪/压缩/要点）共用一条 feed，状态行与正文在同一 scroller */
function ensureProgressScroller(ctx, logType, runSessionId) {
    if (!ctx) return null;
    if (!ctx.progressScrollers) ctx.progressScrollers = {};
    var sc = ctx.progressScrollers[logType];
    if (sc && sc.isConnected) return sc;
    sc = appendLog(ctx, '', logType, runSessionId);
    if (sc) ctx.progressScrollers[logType] = sc;
    return sc;
}

/** 落盘正文：替换流式段或追加到状态行后，与刷新后 ui_events 回放一致 */
function applyProgressPersistedBody(ctx, content, logType, runSessionId) {
    if (!ctx) return;
    var text = String(content || '').trim();
    if (!text) return;
    var st = ctx.progressStream && ctx.progressStream[logType];
    var bodyOffset = st && typeof st.bodyOffset === 'number' ? st.bodyOffset : null;
    var hadStream = bodyOffset != null;
    finalizeProgressStreamForType(ctx, logType);
    var sc = ensureProgressScroller(ctx, logType, runSessionId);
    if (!sc) return;
    var prevTxt = sc.textContent || '';
    var merged;
    if (hadStream) {
        merged = prevTxt.slice(0, bodyOffset).replace(/\s+$/, '') + '\n\n' + text;
    } else if (prevTxt.trim()) {
        merged = prevTxt.trim() + '\n\n' + text;
    } else {
        merged = text;
    }
    sc.textContent = truncateLogTextForUi(merged);
    var chSet = sc.closest('.feed-chunk');
    if (chSet) {
        chSet.classList.remove('is-streaming');
        refreshFeedChunkOverflow(chSet);
        requestAnimationFrame(function () { refreshFeedChunkOverflow(chSet); });
    }
    ctx.progressScrollers[logType] = sc;
    scrollContentAreaIfFollow(ctx, runSessionId);
}

/** 压缩/要点执行端输出：在同一 feed 内流式追加正文（不另起 feed 块） */
function appendProgressStreamDelta(ctx, delta, logType, runSessionId) {
    if (!ctx || !delta) return;
    if (!ctx.progressStream) ctx.progressStream = {};
    var piece = String(delta);
    if (!piece) return;
    var sc = ensureProgressScroller(ctx, logType, runSessionId);
    if (!sc) return;
    var chunk = sc.closest('.feed-chunk');
    if (chunk) chunk.classList.add('is-streaming');
    var st = ctx.progressStream[logType];
    if (!st) {
        var head = (sc.textContent || '').trim();
        var bodyOffset = sc.textContent.length;
        if (head) {
            sc.textContent = head + '\n\n';
            bodyOffset = sc.textContent.length;
        }
        st = { scroller: sc, pending: '', flushRaf: 0, bodyOffset: bodyOffset };
        ctx.progressStream[logType] = st;
    }
    st.pending += piece;
    scheduleProgressDeltaFlush(ctx, runSessionId, logType);
}

/** 同类型进度行合并追加，实现裁剪/压缩/要点分轨流式展示 */
function appendProgressLog(ctx, content, logType, runSessionId) {
    if (!ctx) return;
    finalizeProgressStreamForType(ctx, logType);
    if (!ctx.progressScrollers) ctx.progressScrollers = {};
    var line = String(content || '');
    if (!line.trim()) return;
    var prev = ctx.progressScrollers[logType];
    if (prev && prev.isConnected) {
        var prevTxt = prev.textContent || '';
        prev.textContent = truncateLogTextForUi(prevTxt ? (prevTxt + '\n' + line) : line);
        var chMerge = prev.closest('.feed-chunk');
        if (chMerge) {
            refreshFeedChunkOverflow(chMerge);
            requestAnimationFrame(function () { refreshFeedChunkOverflow(chMerge); });
        }
        scrollContentAreaIfFollow(ctx, runSessionId);
        return;
    }
    var sc = ensureProgressScroller(ctx, logType, runSessionId);
    if (!sc) return;
    sc.textContent = truncateLogTextForUi(line);
    var chNew = sc.closest('.feed-chunk');
    if (chNew) {
        refreshFeedChunkOverflow(chNew);
        requestAnimationFrame(function () { refreshFeedChunkOverflow(chNew); });
    }
    scrollContentAreaIfFollow(ctx, runSessionId);
}

function finalizeProgressStreamForType(ctx, logType) {
    if (!ctx || !logType) return;
    flushProgressDeltaText(ctx, logType);
    if (ctx.progressStream && ctx.progressStream[logType]) {
        var st = ctx.progressStream[logType];
        if (st.scroller && st.scroller.isConnected) {
            var ch = st.scroller.closest('.feed-chunk');
            if (ch) {
                ch.classList.remove('is-streaming');
                refreshFeedChunkOverflow(ch);
            }
        }
        delete ctx.progressStream[logType];
    }
}

/* ── Subagent 浮层 / 过程块 ── */
