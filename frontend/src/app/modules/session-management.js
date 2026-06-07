function setSendButtonState() {
    sendBtn.disabled = false;
    if (isSessionRunning(currentSessionId)) {
        sendBtn.innerHTML = '停止 <span class="loader" aria-hidden="true"></span>';
        sendBtn.classList.add('is-stop');
    } else {
        sendBtn.textContent = '发送';
        sendBtn.classList.remove('is-stop');
    }
}

async function requestInterrupt(sessionId) {
    if (!sessionId) return;
    try { await fetch('/sessions/' + sessionId + '/interrupt', { method: 'POST' }); }
    catch (e) { /* ignore */ }
}

function pauseCurrentRun() {
    if (!currentSessionId) return;
    const run = getSessionRunState(currentSessionId);
    const sid = currentSessionId;
    suppressSessionServerStreamActive(sid);
    if (!run) {
        setSendButtonState();
        syncSessionListIndicatorClasses();
        void requestInterrupt(sid);
        return;
    }
    const ctx = run.ctx;
    /* 先同步 abort 本地 fetch 与从 runningBySession 摘除，UI 立刻反映为「已停止」状态。
       后端 interrupt 走 fire-and-forget，避免被主线程阻塞时按钮响应迟滞。 */
    try { run.controller.abort(); } catch (e) { /* ignore */ }
    clearSessionRunState(sid);
    setSendButtonState();
    syncSessionListIndicatorClasses();
    appendLog(ctx, '已请求停止当前任务', 'status', sid);
    sealProcessGroup(ctx);
    void requestInterrupt(sid);
}

/** 在当前对话中定位最近一条用户消息并重新发送。返回 true 表示已触发展开发送。 */
function resendLastUserMessage() {
    if (!currentSessionId) return false;
    if (isSessionRunning(currentSessionId)) return false;
    var lastMsg = lastUserMessageBySession[currentSessionId];
    if (!lastMsg || !String(lastMsg).trim()) {
        var chatStream = getVisibleChatStream();
        if (chatStream) {
            var wraps = chatStream.querySelectorAll('.msg-wrap--user');
            if (wraps.length) {
                var lastWrap = wraps[wraps.length - 1];
                lastMsg = messageRawMarkdown.get(lastWrap) || (lastWrap.querySelector('.message.user') && lastWrap.querySelector('.message.user').textContent);
            }
        }
    }
    if (!lastMsg || !String(lastMsg).trim()) {
        lastMsg = draftBySession[currentSessionId];
    }
    if (!lastMsg || !String(lastMsg).trim()) return false;
    messageInput.value = String(lastMsg);
    rewriteInputWorkspacePaths();
    autoResizeTextarea();
    sendMessage();
    return true;
}

function showLoading() {
    resetSessionHistoryPaging();
    clearTocForSessionLoad();
    if (!getVisibleChatStream()) ensureVisibleChatStreamSlot();
    const vs = getVisibleChatStream();
    if (vs) emptyChatStreamKeepingStrip(vs);
    const box = document.createElement('div');
    box.className = 'skeleton';
    box.id = 'chat-loading';
    box.setAttribute('role', 'status');
    box.innerHTML = '<div class="skeleton-line" style="width:38%"></div><div class="skeleton-line" style="width:72%"></div><div class="skeleton-line" style="width:55%"></div><div class="skeleton-line" style="width:64%"></div>';
    box.setAttribute('data-ui-tip', '加载会话');
    bindUiHoverTip(box);
    (getVisibleChatStream() || chatContainer).appendChild(box);
    scrollToBottom();
}

function hideLoading() { const loader = document.getElementById('chat-loading'); if (loader) loader.remove(); }

/** 根据 runningBySession / 服务端 stream_active / sessionUnreadComplete 更新黄点、绿点 */
function applySessionItemIndicators(itemDiv, sessionId, opts) {
    opts = opts || {};
    const serverStreamActive = opts.serverStreamActive === true || isServerStreamActive(sessionId);
    if (!itemDiv || !sessionId) return;
    itemDiv.classList.remove('is-generating', 'is-unread-result');
    var nameEl = itemDiv.querySelector('.session-name');
    if (nameEl) nameEl.removeAttribute('data-ui-tip');
    if (isSessionRunning(sessionId) || serverStreamActive) {
        itemDiv.classList.add('is-generating');
        if (nameEl) nameEl.setAttribute('data-ui-tip', '生成中…');
    } else if (sessionUnreadComplete.has(sessionId)) {
        itemDiv.classList.add('is-unread-result');
        if (nameEl) nameEl.setAttribute('data-ui-tip', '有新回复，点击查看');
    }
    if (nameEl) bindUiHoverTip(nameEl);
}

/** 立即刷新侧栏全部指示点与当前选中项；不依赖 loadSessions 网络往返，与是否切换会话无关 */
function syncSessionListIndicatorClasses() {
    if (!sessionsList) return;
    sessionsList.querySelectorAll('.session-item').forEach(function (div) {
        var el = div.querySelector('.session-name[data-id]');
        if (!el) return;
        var sid = el.getAttribute('data-id');
        div.classList.toggle('active', !!sid && sid === currentSessionId);
        applySessionItemIndicators(div, sid);
    });
}

function sessionSectionExpanded(key) {
    try {
        return localStorage.getItem(LS_SESSION_SECTION_PREFIX + key) !== '0';
    } catch (e) {
        return true;
    }
}
function persistSessionSectionExpanded(key, expanded) {
    try {
        localStorage.setItem(LS_SESSION_SECTION_PREFIX + key, expanded ? '1' : '0');
    } catch (e) { /* ignore */ }
}
function closeAllSessionMenus() {
    document.querySelectorAll('.session-more-wrap.is-open').forEach(function (w) {
        w.classList.remove('is-open');
        var b = w.querySelector('.session-more-btn');
        if (b) b.setAttribute('aria-expanded', 'false');
    });
}
(function bindSessionMenuDocumentCloserOnce() {
    if (window.__myAgentSessionMenuCloser) return;
    window.__myAgentSessionMenuCloser = true;
    document.addEventListener('click', closeAllSessionMenus);
})();

(function bindSessionListDelegatedSwitcherOnce() {
    if (!sessionsList || window.__myAgentSessionListSwitcher) return;
    window.__myAgentSessionListSwitcher = true;
    sessionsList.addEventListener('click', function (e) {
        var target = e.target;
        if (!target || !target.closest) return;
        if (target.closest('button, .session-more-wrap, .session-more-menu, input, textarea, a')) return;
        if (target.isContentEditable) return;
        var row = target.closest('.session-item');
        if (!row || !sessionsList.contains(row)) return;
        var sid = row.dataset.sessionId;
        if (!sid) {
            var nameEl = row.querySelector('.session-name[data-id]');
            sid = nameEl ? nameEl.getAttribute('data-id') : '';
        }
        if (sid && sid !== currentSessionId) {
            Promise.resolve(switchSession(sid)).catch(function (err) {
                console.error('切换会话失败:', err);
            });
        }
    });
})();

/**
 * 创建并绑定单行会话（更多菜单：置顶 → 删除 → 归档在末位）
 */
function buildAndBindSessionRow(sess, allSessions, nextStreamMap) {
    const div = document.createElement('div');
    div.className = 'session-item';
    div.dataset.sessionId = sess.id || '';
    if (currentSessionId === sess.id) div.classList.add('active');
    if (sess.id) nextStreamMap[sess.id] = !!sess.stream_active;
    div.innerHTML = '<div class="session-item-head">'
        + '<span class="session-name" data-id="' + sess.id + '" data-original="' + escapeHtml(sess.name) + '">' + escapeHtml(sess.name) + '</span>'
        + '<div class="session-more-wrap">'
        + '<button type="button" class="session-more-btn" aria-label="更多操作" aria-expanded="false" aria-haspopup="true" data-ui-tip="更多">'
        + '<span class="session-more-dots" aria-hidden="true"><span></span><span></span><span></span></span></button>'
        + '<div class="session-more-menu" role="menu">'
        + '<button type="button" class="session-menu-pin" role="menuitem"></button>'
        + '<button type="button" class="session-menu-delete" role="menuitem">删除</button>'
        + '<button type="button" class="session-menu-archive" role="menuitem"></button>'
        + '</div></div>'
        + '</div>'
        + '<div class="session-last-query"></div>';
    div.addEventListener('click', function (e) {
        var target = e.target;
        if (target && target.closest && target.closest('button, .session-more-wrap, .session-more-menu, input, textarea, a')) return;
        if (target && target.isContentEditable) return;
        Promise.resolve(switchSession(sess.id)).catch(function (err) {
            console.error('切换会话失败:', err);
        });
    });
    var pinMi = div.querySelector('.session-menu-pin');
    var archMi = div.querySelector('.session-menu-archive');
    if (pinMi) pinMi.textContent = sess.pinned ? '取消置顶' : '置顶';
    if (archMi) archMi.textContent = sess.archived ? '取消归档' : '归档';
    var wsLine = formatSessionListSubtitle(sess);
    var wsEl = div.querySelector('.session-last-query');
    if (wsEl) {
        wsEl.textContent = wsLine;
        wsEl.setAttribute('data-ui-tip', wsLine);
        wsEl.addEventListener('click', function () {
            Promise.resolve(switchSession(sess.id)).catch(function (err) {
                console.error('切换会话失败:', err);
            });
        });
        bindUiHoverTip(wsEl);
    }
    var moreWrap = div.querySelector('.session-more-wrap');
    var moreBtn = div.querySelector('.session-more-btn');
    if (moreBtn) bindUiHoverTip(moreBtn);
    if (moreWrap && moreBtn) {
        moreBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            var wasOpen = moreWrap.classList.contains('is-open');
            closeAllSessionMenus();
            if (pinMi) pinMi.textContent = sess.pinned ? '取消置顶' : '置顶';
            if (archMi) archMi.textContent = sess.archived ? '取消归档' : '归档';
            if (!wasOpen) {
                moreWrap.classList.add('is-open');
                moreBtn.setAttribute('aria-expanded', 'true');
            }
        });
    }
    if (pinMi) {
        pinMi.addEventListener('click', async function (e) {
            e.stopPropagation();
            closeAllSessionMenus();
            try {
                const formData = new FormData();
                formData.append('pinned', sess.pinned ? 'false' : 'true');
                await fetch('/sessions/' + encodeURIComponent(sess.id) + '/pin', { method: 'PUT', body: formData });
                sessionListCache.invalidate();
                await loadSessions();
            } catch (err) { console.error('置顶失败', err); }
        });
    }
    if (archMi) {
        archMi.addEventListener('click', async function (e) {
            e.stopPropagation();
            closeAllSessionMenus();
            try {
                const formData = new FormData();
                formData.append('archived', sess.archived ? 'false' : 'true');
                await fetch('/sessions/' + encodeURIComponent(sess.id) + '/archive', { method: 'PUT', body: formData });
                sessionListCache.invalidate();
                sessionStore.clearArchivedLoaded();
                syncArchivedSessionStateFromStore();
                await loadSessions();
            } catch (err) { console.error('归档失败', err); }
        });
    }
    var delMi = div.querySelector('.session-menu-delete');
    if (delMi) {
        delMi.addEventListener('click', async function (e) {
            e.stopPropagation();
            closeAllSessionMenus();
            const okDel = await openUiModal({
                title: '删除会话',
                subtitle: '此操作不可恢复',
                message: '确定删除会话「' + String(sess.name || '未命名') + '」吗？其中的消息与记录将被移除。',
                danger: true,
                confirmText: '删除会话',
                cancelText: '取消',
            });
            if (!okDel) return;
            await requestInterrupt(sess.id);
            if (isSessionRunning(sess.id)) {
                const r = getSessionRunState(sess.id);
                try { if (r && r.controller) r.controller.abort(); } catch (err) { /* ignore */ }
                if (r && r.ctx && r.ctx.stream && r.ctx.stream.parentNode) r.ctx.stream.remove();
                clearSessionRunState(sess.id);
                setSendButtonState();
                syncSessionListIndicatorClasses();
            }
            await fetch('/sessions/' + sess.id, { method: 'DELETE' });
            sessionStore.remove(sess.id);
            sessionListCache.invalidate();
            if (div && div.parentNode) div.remove();
            sessionUnreadComplete.delete(sess.id);
            persistSessionUnread();
            delete draftBySession[sess.id];
            delete lastUserMessageBySession[sess.id];
            delete contextTokensBySession[sess.id];
            if (currentSessionId === sess.id) {
                const remaining = allSessions.filter(function (s) { return s && s.id && s.id !== sess.id; });
                if (remaining.length > 0) await switchSession(remaining[0].id);
                else await createNewSession();
            } else if (div && div.parentNode) div.remove();
        });
    }
    const nameSpan = div.querySelector('.session-name');
    if (nameSpan) {
        nameSpan.addEventListener('dblclick', function (e) {
            e.stopPropagation();
            if (nameSpan.classList.contains('editing')) return;
            nameSpan.classList.add('editing');
            nameSpan.contentEditable = 'true';
            nameSpan.focus();
            const range = document.createRange();
            range.selectNodeContents(nameSpan);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
        });
        nameSpan.addEventListener('blur', async function () {
            if (!nameSpan.classList.contains('editing')) return;
            nameSpan.classList.remove('editing');
            nameSpan.contentEditable = 'false';
            const newName = nameSpan.innerText.trim();
            if (newName && newName !== nameSpan.dataset.original) {
                try {
                    const formData = new FormData();
                    formData.append('name', newName);
                    await fetch('/sessions/' + sess.id + '/name', { method: 'PUT', body: formData });
                    nameSpan.dataset.original = newName;
                    if (currentSessionId === sess.id) updateSessionTitle();
                } catch (err) { console.error('重命名失败', err); nameSpan.innerText = nameSpan.dataset.original; }
            } else nameSpan.innerText = nameSpan.dataset.original;
        });
        nameSpan.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); nameSpan.blur(); } });
        nameSpan.addEventListener('click', function () {
            if (!nameSpan.classList.contains('editing')) {
                Promise.resolve(switchSession(sess.id)).catch(function (err) {
                    console.error('切换会话失败:', err);
                });
            }
        });
    }
    applySessionItemIndicators(div, sess.id, { serverStreamActive: !!sess.stream_active });
    return div;
}

async function refreshSingleSessionRow(sessionId) {
    if (!sessionId || !sessionsList) return;
    try {
        const response = await fetch('/sessions/' + encodeURIComponent(sessionId));
        if (!response.ok) {
            await loadSessions();
            return;
        }
        const sess = await response.json();
        if (!sess || !sess.id) {
            await loadSessions();
            return;
        }
        sessionStore.upsert(sess);
        sessionListCache.invalidate();
        setSessionServerStreamActive(sess.id, !!sess.stream_active);
        const item = sessionsList.querySelector('.session-name[data-id="' + sess.id + '"]');
        const div = item && item.closest('.session-item');
        if (!div) {
            await loadSessions();
            return;
        }
        const nameSpan = div.querySelector('.session-name[data-id]');
        if (nameSpan && sess.name != null) {
            nameSpan.textContent = sess.name;
            nameSpan.setAttribute('data-original', sess.name);
        }
        var wsEl2 = div.querySelector('.session-last-query');
        if (wsEl2) {
            var wsLine2 = formatSessionListSubtitle(sess);
            wsEl2.textContent = wsLine2;
            wsEl2.setAttribute('data-ui-tip', wsLine2);
            bindUiHoverTip(wsEl2);
        }
        var pinMi2 = div.querySelector('.session-menu-pin');
        var archMi2 = div.querySelector('.session-menu-archive');
        if (pinMi2) pinMi2.textContent = sess.pinned ? '取消置顶' : '置顶';
        if (archMi2) archMi2.textContent = sess.archived ? '取消归档' : '归档';
        div.classList.toggle('active', sess.id === currentSessionId);
        if (Number(sess.subagent_running || 0) > 0) {
            sessionUnreadComplete.delete(sess.id);
            persistSessionUnread();
        }
        applySessionItemIndicators(div, sess.id, { serverStreamActive: !!sess.stream_active });
        updateSessionTitle();
    } catch (e) {
        console.error('刷新会话摘要失败:', e);
        void loadSessions();
    }
}

// 会话列表缓存
const sessionListCache = {
    data: null,
    timestamp: 0,
    TTL: 30000, // 30秒缓存
    
    get() {
        if (this.data && (Date.now() - this.timestamp) < this.TTL) {
            return this.data;
        }
        return null;
    },
    
    set(data) {
        this.data = data;
        this.timestamp = Date.now();
    },
    
    invalidate() {
        this.data = null;
        this.timestamp = 0;
    }
};

let sessionListLoadEpoch = 0;
let createNewSessionQueue = Promise.resolve();
let archivedSessionsLoaded = false;
let archivedSessionsCache = null;
let archivedSessionsCount = 0;

function syncArchivedSessionStateFromStore() {
    archivedSessionsLoaded = !!sessionStore.archivedLoaded;
    archivedSessionsCache = sessionStore.archivedSessions;
    archivedSessionsCount = sessionStore.archivedCount;
}

// 事件计数缓存，用于乐观更新
const uiEventCountCache = {
    cache: new Map(),
    
    get(sessionId) {
        return this.cache.get(sessionId) || 0;
    },
    
    set(sessionId, count) {
        this.cache.set(sessionId, count);
    },
    
    increment(sessionId) {
        const current = this.get(sessionId);
        this.set(sessionId, current + 1);
        return current + 1;
    },
    
    updateFromServer(sessionId, count) {
        this.set(sessionId, count);
    }
};

async function loadSessions(opts) {
    opts = opts || {};
    const loadEpoch = ++sessionListLoadEpoch;
    sessionStore.ui.loadingSessions = true;
    try {
        // 检查缓存
        const cachedData = opts.force ? null : sessionListCache.get();
        let allSessions;
        
        if (cachedData) {
            // 使用缓存数据
            allSessions = cachedData;
        } else {
            // 从服务器获取数据
            const response = await fetch('/sessions');
            const archivedCountHeader = response.headers.get('X-Archived-Count');
            if (archivedCountHeader != null && archivedCountHeader !== '') {
                const parsedArchivedCount = Number(archivedCountHeader);
                if (Number.isFinite(parsedArchivedCount) && parsedArchivedCount >= 0) {
                    sessionStore.setArchivedCount(parsedArchivedCount);
                    syncArchivedSessionStateFromStore();
                }
            }
            const sessions = await response.json();
            if (loadEpoch !== sessionListLoadEpoch) return;
            allSessions = Array.isArray(sessions) ? sessions : [];
            
            // 更新缓存
            sessionListCache.set(allSessions);
        }
        applySessionSnapshot({ sessions: allSessions, archived_count: archivedSessionsCount });
        syncArchivedSessionStateFromStore();
        allSessions = sessionStore.list();
        
        let nextStreamMap = Object.create(null);
        const idSet = new Set();
        for (let si = 0; si < allSessions.length; si += 1) {
            if (allSessions[si] && allSessions[si].id) idSet.add(allSessions[si].id);
        }
        [...sessionUnreadComplete].forEach(function (uid) {
            if (!idSet.has(uid)) sessionUnreadComplete.delete(uid);
        });
        persistSessionUnread();

        nextStreamMap = renderSessionListFromStore();
        applyServerStreamActiveMap(nextStreamMap);
        renderSessionTitleFromStore();
        sessionStore.ui.loadingSessions = false;
        return;

        const pinnedList = [];
        const normalList = [];
        const archivedList = sessionStore.archivedList();
        for (let i = 0; i < allSessions.length; i += 1) {
            const s = allSessions[i];
            if (!s || !s.id) continue;
            const arch = !!s.archived;
            const pin = !!s.pinned;
            if (arch) continue;
            else if (pin) pinnedList.push(s);
            else normalList.push(s);
        }

        function appendSection(sectionKey, title, list) {
            if (!list.length && sectionKey !== 'archived') return;
            var displayCount = sectionKey === 'archived' && !archivedSessionsLoaded
                ? archivedSessionsCount
                : list.length;
            var expanded = sessionSectionExpanded(sectionKey);
            var sec = document.createElement('div');
            sec.className = 'session-section' + (expanded ? '' : ' is-collapsed');
            sec.dataset.section = sectionKey;
            var toggle = document.createElement('button');
            toggle.type = 'button';
            toggle.className = 'session-section-toggle';
            toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
            toggle.innerHTML = '<span class="session-section-toggle-label">' + escapeHtml(title) + '</span>'
                + '<span class="session-section-meta">'
                + '<span class="session-section-count">' + String(displayCount) + '</span>'
                + '<span class="session-section-chev" aria-hidden="true">▼</span>'
                + '</span>';
            toggle.addEventListener('click', function (e) {
                e.preventDefault();
                sec.classList.toggle('is-collapsed');
                var isExpanded = !sec.classList.contains('is-collapsed');
                persistSessionSectionExpanded(sectionKey, isExpanded);
                toggle.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
            });
            var body = document.createElement('div');
            body.className = 'session-section-body';
            if (sectionKey === 'archived') {
                var loadBtn = document.createElement('button');
                loadBtn.type = 'button';
                loadBtn.className = 'session-archive-load-btn';
                loadBtn.textContent = archivedSessionsLoaded ? '刷新归档目录' : '加载归档目录';
                loadBtn.addEventListener('click', async function (e) {
                    e.preventDefault();
                    e.stopPropagation();
                    loadBtn.disabled = true;
                    loadBtn.textContent = '加载中...';
                    try {
                        const response = await fetch('/sessions?include_archived=true');
                        const sessions = await response.json();
                        const all = Array.isArray(sessions) ? sessions : [];
                        sessionStore.setArchivedLoaded(all);
                        syncArchivedSessionStateFromStore();
                        sessionListCache.set(all.filter(function (s) { return s && s.id && !s.archived; }));
                        await loadSessions();
                    } catch (err) {
                        console.error('加载归档目录失败:', err);
                        loadBtn.disabled = false;
                        loadBtn.textContent = archivedSessionsLoaded ? '刷新归档目录' : '加载归档目录';
                    }
                });
                body.appendChild(loadBtn);
            }
            for (let j = 0; j < list.length; j += 1) {
                body.appendChild(buildAndBindSessionRow(list[j], allSessions, nextStreamMap));
            }
            sec.appendChild(toggle);
            sec.appendChild(body);
            sessionsList.appendChild(sec);
        }

        appendSection('pinned', '置顶目录', pinnedList);
        appendSection('normal', '会话目录', normalList);
        appendSection('archived', '归档目录', archivedList);

        applyServerStreamActiveMap(nextStreamMap);
        updateSessionTitle();
    } catch (error) {
        sessionStore.ui.loadingSessions = false;
        console.error('加载会话列表失败:', error);
        appendLogVisible('加载会话列表失败', 'error-log');
    }
}

async function loadSessionMessages(sessionId, scrollBehavior, opts) {
    scrollBehavior = scrollBehavior || 'saved-or-bottom';
    opts = opts || {};
    const loadToken = ++messageLoadEpoch;
    sessionStore.ui.loadingMessages = true;
    suppressTocDuringSessionLoad = true;
    replayingMessages = true;
    resetSessionHistoryPaging();
    try {
        let url = '/sessions/' + encodeURIComponent(sessionId) + '/messages';
        if (!opts.full) url += '?turns=' + HISTORY_DIALOGUES_PER_PAGE;
        const response = await fetch(url);
        const raw = await response.json();
        if (loadToken !== messageLoadEpoch || sessionId !== currentSessionId) return;
        document.getElementById('chat-loading')?.remove();
        if (!getVisibleChatStream()) ensureVisibleChatStreamSlot();
        const vis = getVisibleChatStream();
        if (vis) emptyChatStreamKeepingStrip(vis);
        else {
            chatContainer.innerHTML = '';
            ensureVisibleChatStreamSlot();
        }
        let events;
        let pageMeta = null;
        if (Array.isArray(raw)) {
            events = raw;
        } else if (raw && typeof raw === 'object' && Array.isArray(raw.events)) {
            events = raw.events;
            pageMeta = {
                total: Number(raw.total) || 0,
                range_start: Number(raw.range_start) || 0,
                range_end: Number(raw.range_end) || 0,
                has_older: !!raw.has_older,
            };
        } else {
            events = [];
        }
        if (!opts.full && pageMeta) {
            sessionHistoryPaging = {
                sessionId: sessionId,
                total: pageMeta.total,
                range_start: pageMeta.range_start,
                range_end: pageMeta.range_end,
                has_older: !!pageMeta.has_older,
            };
            ensureHistorySentinel(getVisibleChatStream());
            updateHistorySentinelVisibility();
        }
        if (events.length === 0) {
            suppressTocDuringSessionLoad = false;
            setWelcome();
            updateSessionTitle();
            scheduleContextTokensAfterPaint(sessionId);
            applyChatScrollAfterHistoryLoad(sessionId, scrollBehavior);
            return;
        }
        const loadCtx = newDomContext(getVisibleChatStream());
        loadCtx.lastUserEventIndex = -1;
        const indexBase = pageMeta ? pageMeta.range_start : 0;
        const batchSize = opts.full ? 64 : 512;
        for (let evi = 0; evi < events.length; evi += 1) {
            const ev = events[evi];
            if (ev && typeof ev === 'object' && ev.type) renderEvent(loadCtx, ev, indexBase + evi, sessionId);
            if (evi > 0 && evi % batchSize === 0) {
                await new Promise(function (resolve) { setTimeout(resolve, 0); });
                if (loadToken !== messageLoadEpoch || sessionId !== currentSessionId) return;
            }
        }
        if (!opts.full && opts.preloadOlderIfShort && pageMeta && pageMeta.has_older && events.length <= 2) {
            await loadOlderHistoryChunk({ keepTocStable: true });
            if (loadToken !== messageLoadEpoch || sessionId !== currentSessionId) return;
        }
        if (historyLoadScrollsToBottom(sessionId, scrollBehavior)) {
            tocScrollBottomOnNextBuild = true;
        }
        suppressTocDuringSessionLoad = false;
        rebuildToc();
        updateSessionTitle();
        updateHistorySentinelVisibility();
        applyChatScrollAfterHistoryLoad(sessionId, scrollBehavior);
        await waitForChatScrollAfterHistoryLoad(sessionId, scrollBehavior);
        if (loadToken !== messageLoadEpoch || sessionId !== currentSessionId) return;
        bindExistingLogs();
        scheduleTocActiveUpdate();
        scheduleContextTokensAfterPaint(sessionId);
        await refreshTodoPlanPanel();
    } catch (error) {
        console.error('加载会话消息失败:', error);
        document.getElementById('chat-loading')?.remove();
        appendLogVisible('加载历史消息失败', 'error-log');
    } finally {
        if (loadToken === messageLoadEpoch) sessionStore.ui.loadingMessages = false;
        if (loadToken === messageLoadEpoch) suppressTocDuringSessionLoad = false;
        if (loadToken === messageLoadEpoch) replayingMessages = false;
    }
}

async function switchSession(sessionId) {
    if (currentSessionId === sessionId) return;
    const switchToken = ++switchSessionEpoch;
    suppressTocDuringSessionLoad = true;
    clearTocForSessionLoad();
    clearTodoForSessionLoad();
    pendingRewriteTruncate = null;
    hideRewriteUndoToast();
    sessionUnreadComplete.delete(sessionId);
    persistSessionUnread();
    const leaving = currentSessionId;
    saveChatScrollForSession(leaving);
    stashInputDraft(leaving);
    prepareStashLeaving(leaving);
    hideSubagentContinueBanner();
    resetSubagentPanelForSession();
    setCurrentSessionState(sessionId);
    localStorage.setItem('lastSessionId', sessionId);
    restoreInputDraft(sessionId);
    syncSessionListIndicatorClasses();
    setSendButtonState();
    if (restoreStreamForRunningSession(sessionId)) {
        suppressTocDuringSessionLoad = false;
        hideLoading();
        rebuildToc();
        updateSessionTitle();
        scheduleContextTokensAfterPaint(sessionId);
        applyChatScrollAfterHistoryLoad(sessionId, 'saved-or-bottom');
        await refreshTodoPlanPanel();
        if (switchToken !== switchSessionEpoch || sessionId !== currentSessionId) return;
        /* 让 rebuildToc 的 /user_turns fetch 先发出，subagent 面板（含 N 个 /messages）延后一帧
           避免抢占带宽与主线程，导致目录最后才就绪。 */
        setTimeout(function () { refreshSubagentTreePanel(sessionId); }, 0);
        void refreshSingleSessionRow(sessionId);
        setSendButtonState();
        maybeStartStreamPollForSession(sessionId, { skipInitialLoad: true });
        return;
    }
    const vs = getVisibleChatStream();
    resetSessionHistoryPaging();
    if (vs) emptyChatStreamKeepingStrip(vs);
    else {
        chatContainer.innerHTML = '';
        ensureVisibleChatStreamSlot();
    }
    showLoading();
    setTimeout(async () => {
        if (switchToken !== switchSessionEpoch || sessionId !== currentSessionId) return;
        await loadSessionMessages(sessionId, undefined, { preloadOlderIfShort: isServerStreamActive(sessionId) });
        if (switchToken !== switchSessionEpoch || sessionId !== currentSessionId) return;
        hideLoading();
        /* loadSessionMessages 内部已发起 rebuildToc()；这里再延后一帧调用 subagent panel
           保证「目录 → 消息 → 子 agent 按钮」的稳定顺序（无 subagent 的会话表现一致）。 */
        setTimeout(function () { refreshSubagentTreePanel(sessionId); }, 0);
        void refreshSingleSessionRow(sessionId);
        setSendButtonState();
        maybeStartStreamPollForSession(sessionId, { skipInitialLoad: true });
    }, 20);
}

async function createNewSession() {
    createNewSessionQueue = createNewSessionQueue.then(
        function () { return createNewSessionInner(); },
        function () { return createNewSessionInner(); }
    );
    return createNewSessionQueue;
}

async function createNewSessionInner() {
    try {
        saveChatScrollForSession(currentSessionId);
        stashInputDraft(currentSessionId);
        prepareStashLeaving(currentSessionId);
        const response = await fetch('/sessions', { method: 'POST' });
        const data = await response.json();
        if (data && data.session) sessionStore.upsert(data.session);
        resetSubagentPanelForSession();
        switchSessionEpoch += 1;
        messageLoadEpoch += 1;
        setCurrentSessionState(data.session_id);
        localStorage.setItem('lastSessionId', currentSessionId);
        restoreInputDraft(currentSessionId);
        if (!getVisibleChatStream()) ensureVisibleChatStreamSlot();
        setWelcome();
        replayingMessages = false;
        if (data && data.session) {
            sessionListCache.set(sessionStore.list());
            await loadSessions();
            sessionListCache.invalidate();
            void loadSessions({ force: true });
        } else {
            sessionListCache.invalidate();
            await loadSessions({ force: true });
        }
        setSendButtonState();
        maybeStartStreamPollForSession(currentSessionId);
        scheduleContextTokensAfterPaint(currentSessionId);
    } catch (error) {
        console.error('创建新会话失败:', error);
        appendLogVisible('创建新会话失败', 'error-log');
    }
}
