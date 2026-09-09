function setSendButtonState() {
    syncMessageInputPlaceholder();
    sendBtn.disabled = false;
    const uploadBusy = isChatFileUploadBusy();
    const newSessionPreflight = !currentSessionId && optimisticNewSessionRun;
    const newSessionCreating = !currentSessionId && materializeNewSessionQueue;
    if (uploadBusy) {
        sendBtn.textContent = '上传中';
        sendBtn.classList.remove('is-stop');
        sendBtn.classList.remove('is-followup');
        sendBtn.disabled = true;
        return;
    }
    if (newSessionCreating && !newSessionPreflight) {
        sendBtn.textContent = '创建中';
        sendBtn.classList.remove('is-stop');
        sendBtn.classList.remove('is-followup');
        sendBtn.disabled = true;
        return;
    }
    if (isSessionRunning(currentSessionId) || newSessionPreflight) {
        const run = newSessionPreflight || (typeof getSessionRunState === 'function' ? getSessionRunState(currentSessionId) : null);
        const suppressFollowup = !!(run && run.suppressFollowupButton);
        const hasDraft = (typeof inputHasSendableText === 'function')
            ? inputHasSendableText()
            : !!(messageInput && String(messageInput.value || '').trim());
        const followupEnabled = (typeof isMyAgentFeatureEnabled === 'function') && isMyAgentFeatureEnabled('followupRestart', false);
        sendBtn.innerHTML = (followupEnabled && hasDraft && !suppressFollowup) ? '追问' : '停止 <span class="loader" aria-hidden="true"></span>';
        sendBtn.classList.add('is-stop');
        sendBtn.classList.toggle('is-followup', followupEnabled && hasDraft && !suppressFollowup);
    } else {
        sendBtn.textContent = '发送';
        sendBtn.classList.remove('is-stop');
        sendBtn.classList.remove('is-followup');
        sendBtn.disabled = false;
    }
}

const MESSAGE_INPUT_PLACEHOLDER_DEFAULT = '说说你想做什么…（Enter 发送 · Shift/Ctrl/Cmd + Enter 换行）';
const MESSAGE_INPUT_PLACEHOLDER_RUNNING = 'Agent运行中，输入后续任务';
const MESSAGE_INPUT_PLACEHOLDER_QUEUED = '按 Enter 发送刚加入或第一条待发送任务';

function syncMessageInputPlaceholder() {
    if (!messageInput) return;
    var queue = currentSessionId && typeof getFollowupQueue === 'function'
        ? getFollowupQueue(currentSessionId)
        : [];
    var running = !!(optimisticNewSessionRun || isSessionRunning(currentSessionId));
    var value = queue.some(function (item) { return item && !item.status; })
        ? MESSAGE_INPUT_PLACEHOLDER_QUEUED
        : (running ? MESSAGE_INPUT_PLACEHOLDER_RUNNING : MESSAGE_INPUT_PLACEHOLDER_DEFAULT);
    messageInput.placeholder = typeof translateUiString === 'function'
        ? translateUiString(value)
        : value;
}

function isChatFileUploadBusy() {
    return !!(messageInput && messageInput.dataset.fileUploadBusy === '1');
}

document.addEventListener('myagent:language-change', syncMessageInputPlaceholder);
document.addEventListener('myagent:language-change', function () {
    if (typeof renderSessionListIfChanged === 'function') renderSessionListIfChanged(true);
});

async function requestInterrupt(sessionId, runId, reason) {
    if (!sessionId) return;
    try {
        await fetch('/sessions/' + sessionId + '/interrupt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ run_id: runId || '', reason: reason || '' }),
        });
    }
    catch (e) { /* ignore */ }
}

function pauseCurrentRun() {
    if (!currentSessionId) {
        if (optimisticNewSessionRun) {
            markRunAbortReason(optimisticNewSessionRun, 'user');
            try { optimisticNewSessionRun.controller.abort(); } catch (e) { /* ignore */ }
            optimisticNewSessionRun = null;
            setSendButtonState();
        }
        return;
    }
    const run = getSessionRunState(currentSessionId);
    const sid = currentSessionId;
    const activeInfo = sessionStore.getActiveRunInfo(sid) || {};
    const runId = run && run.runId ? run.runId : (activeInfo.run_id || activeInfo.runId || '');
    if (typeof markFollowupQueueManualOnly === 'function') markFollowupQueueManualOnly(sid);
    suppressSessionServerStreamActive(sid);
    if (!run) {
        setSendButtonState();
        syncSessionListIndicatorClasses();
        renderSessionListIfChanged(false);
        void requestInterrupt(sid, runId, 'user_button');
        setTimeout(function () { reconcileRunStateFromServer({ silent: true, respectStopSuppress: true }); }, 3000);
        return;
    }
    const ctx = run.ctx;
    const reachedServer = run.submitted !== false;
    /* 先同步 abort 本地 fetch 与从 sessionStore 摘除，UI 立即反映「已停止」状态；
       后端 interrupt 走 fire-and-forget，避免被主线程阻塞时按钮响应卡顿。*/
    abortSessionRun(sid, 'user');
    setSendButtonState();
    syncSessionListIndicatorClasses();
    renderSessionListIfChanged(false);
    appendLog(ctx, '已请求停止当前任务', 'status', sid);
    sealProcessGroup(ctx);
    if (reachedServer) void requestInterrupt(sid, runId, 'user_button');
    setTimeout(function () { reconcileRunStateFromServer({ silent: true, respectStopSuppress: true }); }, 3000);
}

/** 在当前会话中定位最近一条用户消息并重新发送。返回 true 表示已触发展开发送。*/
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
    box.innerHTML = ''
        + '<div class="skeleton-page" aria-hidden="true">'
        + '<div class="skeleton-mast"><span></span><span></span></div>'
        + '<div class="skeleton-hero"><div class="skeleton-image"></div><div class="skeleton-column"><span></span><span></span><span></span><span></span></div></div>'
        + '<div class="skeleton-grid"><div><span></span><span></span><span></span></div><div><span></span><span></span><span></span></div><div><span></span><span></span><span></span></div></div>'
        + '</div><div class="skeleton-copy">加载中...</div>';
    box.setAttribute('data-ui-tip', '加载会话');
    bindUiHoverTip(box);
    (getVisibleChatStream() || chatContainer).appendChild(box);
    scrollToBottom();
}

function hideLoading() { const loader = document.getElementById('chat-loading'); if (loader) loader.remove(); }

function sessionHasUnsentDraft(sessionId) {
    if (!sessionId) return false;
    var draft = Object.prototype.hasOwnProperty.call(draftBySession, sessionId)
        ? draftBySession[sessionId]
        : readStoredInputDraft(sessionId);
    return !!String(draft || '').trim();
}

function syncSessionDraftBadge(itemDiv, sessionId) {
    if (!itemDiv || !sessionId) return;
    var badge = itemDiv.querySelector('.session-draft-badge');
    if (!badge) return;
    var visible = String(sessionId) !== String(currentSessionId || '') && sessionHasUnsentDraft(sessionId);
    badge.hidden = !visible;
    itemDiv.classList.toggle('has-unsent-draft', visible);
}

/** 只同步草稿标签，不重绘会话列表；传入 sessionId 时仅更新对应行。 */
function syncSessionDraftBadges(sessionId) {
    if (!sessionsList) return;
    var targetId = sessionId ? String(sessionId) : '';
    sessionsList.querySelectorAll('.session-item').forEach(function (div) {
        var sid = String(div.dataset.sessionId || '');
        if (!sid || (targetId && sid !== targetId)) return;
        syncSessionDraftBadge(div, sid);
    });
}

/** 根据 sessionStore / 服务端 stream_active / sessionUnreadComplete 更新红点、绿点 */
function applySessionItemIndicators(itemDiv, sessionId, opts) {
    opts = opts || {};
    if (!itemDiv || !sessionId) return;
    syncSessionDraftBadge(itemDiv, sessionId);
    itemDiv.classList.remove('is-generating', 'is-finalizing', 'is-unread-result', 'is-unread-failed');
    var nameEl = itemDiv.querySelector('.session-name');
    if (nameEl) nameEl.removeAttribute('data-ui-tip');
    var sess = sessionStore.get(sessionId);
    var localUnreadResult = sessionUnreadComplete.has(sessionId);
    var hasUnreadResult = sess ? !!sess.unread_result : localUnreadResult;
    var failed = !!(sess && sess.unread_result_status === 'failed');
    var running = isSessionRunning(sessionId);
    var finalizing = running && sessionStore.isRunFinalizing(sessionId);
    if (running) {
        itemDiv.classList.add(finalizing ? 'is-finalizing' : 'is-generating');
        if (hasUnreadResult) {
            // A completed queued turn is still unread while the next pending
            // turn is running. Combining the classes keeps the pulse animation
            // but changes the dot to the result color.
            itemDiv.classList.add(failed ? 'is-unread-failed' : 'is-unread-result');
        }
        if (nameEl) {
            nameEl.setAttribute(
                'data-ui-tip',
                hasUnreadResult
                    ? (failed ? '已有任务失败，当前任务仍在处理' : '已有任务完成，当前任务仍在处理')
                    : (finalizing ? '回复已生成，正在收尾' : '生成中')
            );
        }
    } else {
        if (!hasUnreadResult) return;
        itemDiv.classList.add(failed ? 'is-unread-failed' : 'is-unread-result');
        if (nameEl) nameEl.setAttribute('data-ui-tip', failed ? '任务失败，点击查看' : '有新回复，点击查看');
    }
    if (nameEl) bindUiHoverTip(nameEl);
}

/** 立即刷新侧栏全部指示点与当前选中项；不依赖 loadSessions 网络回流，与是否切换会话无关 */
function syncSessionListIndicatorClasses() {
    if (!sessionsList) return;
    sessionsList.querySelectorAll('.session-item').forEach(function (div) {
        var el = div.querySelector('.session-name[data-id]');
        if (!el) return;
        var sid = el.getAttribute('data-id');
        div.classList.toggle('active', !!sid && sid === currentSessionId);
        applySessionItemIndicators(div, sid);
    });
    if (typeof updateAllHumanInteractionSessionBadges === 'function') updateAllHumanInteractionSessionBadges();
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

function buildSessionMoreMenuMarkup() {
    return '<div class="session-more-wrap">'
        + '<button type="button" class="session-more-btn" aria-label="更多操作" aria-expanded="false" aria-haspopup="true" data-ui-tip="更多">'
        + '<span class="session-more-dots" aria-hidden="true"><span></span><span></span><span></span></span></button>'
        + '<div class="session-more-menu" role="menu">'
        + '<button type="button" class="session-menu-pin" role="menuitem"></button>'
        + '<button type="button" class="session-menu-todo" role="menuitem"></button>'
        + '<button type="button" class="session-menu-rename" role="menuitem">重命名</button>'
        + '<button type="button" class="session-menu-archive" role="menuitem"></button>'
        + '<div class="session-menu-separator" role="separator"></div>'
        + '<button type="button" class="session-menu-export" role="menuitem">导出会话</button>'
        + '<button type="button" class="session-menu-delete" role="menuitem">删除会话</button>'
        + '</div></div>';
}

function findSessionForActions(sessionId, fallback) {
    var sid = String(sessionId || '');
    var current = sessionStore.get(sid);
    if (current) return current;
    if (sessionStore.archivedLoaded) {
        current = (sessionStore.archivedSessions || []).find(function (item) {
            return item && String(item.id) === sid;
        });
    }
    return current || fallback || null;
}

function syncSessionMenuLabels(wrap, sess) {
    if (!wrap || !sess) return;
    wrap._sessionMenuSession = sess;
    var pin = wrap.querySelector('.session-menu-pin');
    var todo = wrap.querySelector('.session-menu-todo');
    var archive = wrap.querySelector('.session-menu-archive');
    if (pin) pin.textContent = sess.pinned ? '取消置顶' : '置顶会话';
    if (todo) todo.textContent = sess.todo ? '取消待办' : '设为待办';
    if (archive) archive.textContent = sess.archived ? '取消归档' : '归档会话';
}

async function toggleSessionPinnedFromMenu(sess) {
    try {
        const formData = new FormData();
        const nextPinned = !sess.pinned;
        const previous = applyOptimisticSessionUpdate(sess.id, { pinned: nextPinned });
        formData.append('pinned', nextPinned ? 'true' : 'false');
        const response = await fetch('/sessions/' + encodeURIComponent(sess.id) + '/pin', { method: 'PUT', body: formData });
        if (!response.ok) {
            if (previous) applyOptimisticSessionUpdate(sess.id, previous);
            throw new Error('pin failed: ' + response.status);
        }
        await refreshSingleSessionRow(sess.id);
    } catch (err) { console.error('置顶失败', err); }
}

async function toggleSessionTodoFromMenu(sess) {
    try {
        const formData = new FormData();
        const nextTodo = !sess.todo;
        const previous = applyOptimisticSessionUpdate(sess.id, { todo: nextTodo });
        formData.append('todo', nextTodo ? 'true' : 'false');
        const response = await fetch('/sessions/' + encodeURIComponent(sess.id) + '/todo', { method: 'PUT', body: formData });
        if (!response.ok) {
            if (previous) applyOptimisticSessionUpdate(sess.id, previous);
            throw new Error('todo failed: ' + response.status);
        }
        await refreshSingleSessionRow(sess.id);
    } catch (err) { console.error('待办设置失败', err); }
}

async function toggleSessionArchivedFromMenu(sess) {
    try {
        const formData = new FormData();
        const nextArchived = !sess.archived;
        const previous = applyOptimisticSessionUpdate(sess.id, { archived: nextArchived });
        formData.append('archived', nextArchived ? 'true' : 'false');
        const response = await fetch('/sessions/' + encodeURIComponent(sess.id) + '/archive', { method: 'PUT', body: formData });
        if (!response.ok) {
            if (previous) applyOptimisticSessionUpdate(sess.id, previous);
            throw new Error('archive failed: ' + response.status);
        }
        await refreshSingleSessionRow(sess.id);
        if (!nextArchived && sessionStore.archivedLoaded) {
            await loadArchivedSessions({ background: true, refresh: true, forceRender: true });
        }
    } catch (err) { console.error('归档失败', err); }
}

async function renameSessionFromMenu(sess) {
    var requestedName = await openUiModal({
        title: '重命名会话',
        subtitle: '编辑会话名称',
        message: '',
        inputLabel: '会话名称',
        inputValue: String(sess.name || ''),
        inputMaxLength: 160,
        inputRequired: true,
        confirmText: '保存名称',
        cancelText: '取消',
    });
    if (typeof requestedName !== 'string') return;
    var newName = requestedName.trim().slice(0, 160);
    if (!newName || newName === String(sess.name || '')) return;
    const previous = applyOptimisticSessionUpdate(sess.id, { name: newName });
    if (currentSessionId === sess.id) updateSessionTitle();
    try {
        const formData = new FormData();
        formData.append('name', newName);
        const response = await fetch('/sessions/' + encodeURIComponent(sess.id) + '/name', { method: 'PUT', body: formData });
        if (!response.ok) throw new Error('rename failed: ' + response.status);
        await refreshSingleSessionRow(sess.id);
        if (currentSessionId === sess.id) updateSessionTitle();
    } catch (err) {
        console.error('重命名失败', err);
        if (previous) applyOptimisticSessionUpdate(sess.id, previous);
        if (currentSessionId === sess.id) updateSessionTitle();
    }
}

async function exportSessionFromMenu(sess) {
    var confirmed = await openUiModal({
        title: '导出会话',
        subtitle: '下载会话文件',
        message: '将会话「' + String(sess.name || '未命名') + '」对应的 session 文件夹压缩为 ZIP 并下载。',
        confirmText: '确认导出',
        cancelText: '取消',
    });
    if (!confirmed) return;
    var link = document.createElement('a');
    link.href = '/sessions/' + encodeURIComponent(sess.id) + '/export';
    link.download = 'session-' + String(sess.id || 'export') + '.zip';
    link.hidden = true;
    document.body.appendChild(link);
    link.click();
    link.remove();
}

async function deleteSessionFromMenu(sess, rowDiv) {
    const okDel = await openUiModal({
        title: '删除会话',
        subtitle: '此操作不可恢复',
        message: '确定删除会话「' + String(sess.name || '未命名') + '」吗？其中的消息与记录将被移除。',
        danger: true,
        confirmText: '删除会话',
        cancelText: '取消',
    });
    if (!okDel) return;
    const wasArchivedLoaded = sessionStore.archivedLoaded;
    const deletedSessionId = String(sess.id || '');
    const nextSession = sessionStore.list().find(function (s) {
        return s && s.id && String(s.id) !== deletedSessionId && !s.archived;
    }) || null;
    sessionStore.markDeletedSession(deletedSessionId);
    if (wasArchivedLoaded && sess.archived) {
        const archivedBeforeDelete = sessionStore.archivedSessions || [];
        const deletedArchiveIndex = archivedBeforeDelete.findIndex(function (s) {
            return s && String(s.id) === deletedSessionId;
        });
        sessionStore.setArchivedLoaded(archivedBeforeDelete.filter(function (s) {
            return s && String(s.id) !== deletedSessionId;
        }), {
            visibleCount: Math.max(
                0,
                sessionStore.archivedVisibleCount
                    - (deletedArchiveIndex >= 0 && deletedArchiveIndex < sessionStore.archivedVisibleCount ? 1 : 0)
            ),
            totalCount: Math.max(0, sessionStore.archivedCount - 1),
        });
        syncArchivedSessionStateFromStore();
    }
    renderSessionListIfChanged(true);
    if (rowDiv && rowDiv.parentNode) rowDiv.remove();
    sessionUnreadComplete.delete(deletedSessionId);
    scheduleTitleGenerationRefresh(deletedSessionId, false);
    persistSessionUnread();
    delete draftBySession[deletedSessionId];
    removeStoredInputDraft(deletedSessionId);
    if (typeof removeStoredFollowupQueue === 'function') removeStoredFollowupQueue(deletedSessionId);
    delete lastUserMessageBySession[deletedSessionId];
    clearContextStateForSession(deletedSessionId);
    if (typeof discardCachedSessionStream === 'function') discardCachedSessionStream(deletedSessionId);
    if (isSessionRunning(sess.id)) {
        const r = abortSessionRun(sess.id, 'delete');
        if (r && r.ctx && r.ctx.stream && r.ctx.stream.parentNode) r.ctx.stream.remove();
        setSendButtonState();
        syncSessionListIndicatorClasses();
    }
    if (currentSessionId === deletedSessionId) {
        if (nextSession) await switchSession(nextSession.id);
        else await createNewSession();
    }
    void requestInterrupt(deletedSessionId, '', 'session_deleted');
    void fetch('/sessions/' + encodeURIComponent(deletedSessionId), { method: 'DELETE' })
        .then(function (resp) {
            if (!resp.ok) throw new Error('delete failed: ' + resp.status);
        })
        .catch(function (err) {
            console.error('删除会话失败:', err);
            sessionStore.clearDeletedSessionTombstone(deletedSessionId);
            void loadSessions({ skipArchivedRefresh: true });
            if (wasArchivedLoaded) void loadArchivedSessions({ background: true });
        });
}

function bindSessionActionMenu(wrap, getSession, rowDiv) {
    if (!wrap || wrap.dataset.sessionMenuBound === '1') return;
    wrap.dataset.sessionMenuBound = '1';
    var moreBtn = wrap.querySelector('.session-more-btn');
    if (moreBtn) {
        bindUiHoverTip(moreBtn);
        moreBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            var wasOpen = wrap.classList.contains('is-open');
            closeAllSessionMenus();
            var sess = getSession();
            if (!sess) return;
            syncSessionMenuLabels(wrap, sess);
            if (!wasOpen) {
                wrap.classList.add('is-open');
                moreBtn.setAttribute('aria-expanded', 'true');
            }
        });
    }
    wrap.addEventListener('click', function (e) {
        var target = e.target && e.target.closest ? e.target.closest('[role="menuitem"]') : null;
        if (!target || !wrap.contains(target)) return;
        var handler = target.classList.contains('session-menu-pin') ? toggleSessionPinnedFromMenu
            : target.classList.contains('session-menu-todo') ? toggleSessionTodoFromMenu
                : target.classList.contains('session-menu-rename') ? renameSessionFromMenu
                    : target.classList.contains('session-menu-archive') ? toggleSessionArchivedFromMenu
                        : target.classList.contains('session-menu-export') ? exportSessionFromMenu
                            : target.classList.contains('session-menu-delete') ? deleteSessionFromMenu
                                : null;
        if (!handler) return;
        e.stopPropagation();
        closeAllSessionMenus();
        var sess = getSession();
        if (!sess) return;
        Promise.resolve(handler(sess, rowDiv)).catch(function (err) {
            console.error('会话菜单操作失败:', err);
        });
    });
}

var titlebarSessionMenuSnapshot = null;

function getTitlebarSessionForActions(host, wrap) {
    var sessionId = (host && host.dataset.sessionId) || currentSessionId;
    return findSessionForActions(sessionId, wrap && wrap._sessionMenuSession)
        || titlebarSessionMenuSnapshot;
}

function syncTitlebarSessionMenu(sess) {
    var host = document.getElementById('breadcrumb-session-actions');
    if (!host) return;
    titlebarSessionMenuSnapshot = sess ? Object.assign({}, titlebarSessionMenuSnapshot || {}, sess) : null;
    host.dataset.sessionId = titlebarSessionMenuSnapshot ? String(titlebarSessionMenuSnapshot.id || '') : '';
    host.classList.toggle('hidden', !sess);
    var wrap = host.querySelector('.session-more-wrap');
    if (wrap && titlebarSessionMenuSnapshot) syncSessionMenuLabels(wrap, titlebarSessionMenuSnapshot);
}

(function mountTitlebarSessionMenu() {
    var host = document.getElementById('breadcrumb-session-actions');
    if (!host || host.dataset.sessionMenuMounted === '1') return;
    host.dataset.sessionMenuMounted = '1';
    host.innerHTML = buildSessionMoreMenuMarkup();
    var wrap = host.querySelector('.session-more-wrap');
    bindSessionActionMenu(wrap, function () {
        return getTitlebarSessionForActions(host, wrap);
    }, null);
    syncTitlebarSessionMenu(currentSessionId ? findSessionForActions(currentSessionId, null) : null);
})();

/** 创建并绑定单条会话及其分区操作菜单。 */
function buildAndBindSessionRow(sess, allSessions, nextStreamMap) {
    const div = document.createElement('div');
    div.className = 'session-item';
    div.dataset.sessionId = sess.id || '';
    if (currentSessionId === sess.id) div.classList.add('active');
    if (sess.id) nextStreamMap[sess.id] = !!sess.stream_active;
    if (sess.id) scheduleTitleGenerationRefresh(sess.id, !!sess.title_generation_pending);
    var displayName = typeof localizeSessionPlaceholderName === 'function'
        ? localizeSessionPlaceholderName(sess.name)
        : (sess.name || '');
    div.innerHTML = '<div class="session-item-head">'
        + '<div class="session-item-main">'
        + '<div class="session-item-title-row">'
        + '<span class="session-name" data-id="' + sess.id + '" data-original="' + escapeHtml(sess.name) + '">' + escapeHtml(displayName) + '</span>'
        + '<span class="session-todo-badge" aria-label="待办"' + (sess.todo ? '' : ' hidden') + '>待办</span>'
        + '<span class="session-draft-badge" aria-label="草稿" hidden>草稿</span>'
        + '<span class="session-item-date"></span>'
        + '</div>'
        + '<div class="session-last-query"></div>'
        + '</div>'
        + buildSessionMoreMenuMarkup()
        + '</div>';
    if (typeof updateHumanInteractionSessionBadge === 'function') {
        setTimeout(function () { updateHumanInteractionSessionBadge(sess.id); }, 0);
    }
    var wsLine = formatSessionListSubtitle(sess);
    var wsEl = div.querySelector('.session-last-query');
    if (wsEl) wsEl.textContent = wsLine;
    var dateEl = div.querySelector('.session-item-date');
    var dateLine = '';
    if (dateEl) {
        dateLine = typeof formatSessionListDate === 'function' ? formatSessionListDate(sess) : '';
        if (dateLine) {
            dateEl.innerHTML = (typeof sessionDateIcon === 'function' ? sessionDateIcon() : '') + dateLine;
        } else {
            dateEl.textContent = '';
        }
    }
    var itemTip = typeof buildSessionItemTooltip === 'function' ? buildSessionItemTooltip(sess) : '';
    if (itemTip) {
        div.setAttribute('data-ui-tip', itemTip);
        bindUiHoverTip(div);
    }
    var moreWrap = div.querySelector('.session-more-wrap');
    syncSessionMenuLabels(moreWrap, sess);
    bindSessionActionMenu(moreWrap, function () {
        return findSessionForActions(sess.id, sess);
    }, div);
    var nameEl = div.querySelector('.session-name');
    if (nameEl) {
        nameEl.addEventListener('dblclick', function (e) {
            e.preventDefault();
            e.stopPropagation();
            var current = findSessionForActions(sess.id, sess);
            if (!current) return;
            Promise.resolve(renameSessionFromMenu(current)).catch(function (err) {
                console.error('双击重命名会话失败:', err);
            });
        });
    }
    applySessionItemIndicators(div, sess.id, { serverStreamActive: !!sess.stream_active });
    return div;
}

const sessionTitleRefreshState = Object.create(null);

function scheduleTitleGenerationRefresh(sessionId, pending) {
    const sid = String(sessionId || '');
    if (!sid) return;
    let state = sessionTitleRefreshState[sid];
    if (!pending) {
        if (state && state.timer) clearTimeout(state.timer);
        delete sessionTitleRefreshState[sid];
        return;
    }
    if (!state) state = sessionTitleRefreshState[sid] = { attempts: 0, timer: null };
    if (state.timer || state.attempts >= 60) return;
    const delayMs = Math.min(10000, Math.round(1000 * Math.pow(1.45, state.attempts)));
    state.timer = setTimeout(function () {
        state.timer = null;
        state.attempts += 1;
        void refreshSingleSessionRow(sid);
    }, delayMs);
}

async function refreshSingleSessionRow(sessionId) {
    if (!sessionId || !sessionsList) return;
    try {
        const response = await fetch('/sessions/' + encodeURIComponent(sessionId));
        if (!response.ok) return;
        const sess = await response.json();
        if (!sess || !sess.id) return;
        scheduleTitleGenerationRefresh(sess.id, !!sess.title_generation_pending);
        applySessionPatch({
            session: sess,
            session_id: sess.id,
        });
        sessionStore.applyActiveRunForSession(
            sess.id,
            sess.active_run || (sess.run_active ? {
                session_id: sess.id,
                run_active: true,
                started_at: sess.run_started_at || null,
            } : null)
        );
        if (sess.unread_result) {
            if (!sessionUnreadComplete.has(sess.id)) {
                sessionUnreadComplete.add(sess.id);
                persistSessionUnread();
            }
        } else if (sessionUnreadComplete.delete(sess.id)) {
            persistSessionUnread();
        }
        if (Number(sess.subagent_running || 0) > 0) {
            sessionUnreadComplete.delete(sess.id);
            persistSessionUnread();
        }
        renderSessionListIfChanged(false);
        if (typeof maybeAutoResumeInterruptedReact === 'function') {
            maybeAutoResumeInterruptedReact(sessionId, sess);
        }
    } catch (e) {
        console.error('刷新会话摘要失败:', e);
    }
}

let sessionListLoadEpoch = 0;
let sessionListLoadPromise = null;
let sessionListRenderKey = '';
let materializeNewSessionQueue = null;
let archivedSessionsLoaded = false;
let archivedSessionsCache = null;
let archivedSessionsCount = 0;
let archivedSessionsLoadEpoch = 0;

function syncArchivedSessionStateFromStore() {
    archivedSessionsLoaded = !!sessionStore.archivedLoaded;
    archivedSessionsCache = sessionStore.archivedSessions;
    archivedSessionsCount = sessionStore.archivedCount;
}

function computeSessionListRenderKey() {
    const sessions = sessionStore.list();
    const parts = [
        'archivedLoaded=' + (sessionStore.archivedLoaded ? '1' : '0'),
        'archivedCount=' + String(sessionStore.archivedCount || 0),
    ];
    for (let i = 0; i < sessions.length; i += 1) {
        const s = sessions[i];
        if (!s || !s.id) continue;
        parts.push([
            s.id,
            s.name || '',
            s.pinned ? 'p' : '',
            s.todo ? 't' : '',
            s.archived ? 'a' : '',
            s.last_activity_at || s.updated_at || '',
            s.last_user_preview || '',
        ].join('\u001f'));
    }
    const archived = sessionStore.archivedList();
    for (let j = 0; j < archived.length; j += 1) {
        const a = archived[j];
        if (!a || !a.id) continue;
        parts.push('arch=' + [
            a.id,
            a.name || '',
            a.pinned ? 'p' : '',
            a.todo ? 't' : '',
            a.last_activity_at || a.updated_at || '',
            a.last_user_preview || '',
        ].join('\u001f'));
    }
    return parts.join('\u001e');
}

function renderSessionListIfChanged(force) {
    const nextKey = computeSessionListRenderKey();
    if (!force && nextKey === sessionListRenderKey) {
        syncSessionListIndicatorClasses();
        renderSessionTitleFromStore();
        return;
    }
    sessionListRenderKey = nextKey;
    const nextStreamMap = renderSessionListFromStore();
    applyServerStreamActiveMap(nextStreamMap);
    renderSessionTitleFromStore();
}

function clearSessionListError() {
    if (!sessionsList) return;
    sessionsList.classList.remove('sessions-list--error');
    if (sessionsList.dataset.loadError === '1') delete sessionsList.dataset.loadError;
}

function renderSessionListError(message) {
    if (!sessionsList) return;
    sessionListRenderKey = '';
    sessionsList.classList.add('sessions-list--error');
    sessionsList.dataset.loadError = '1';
    sessionsList.innerHTML = '';
    const row = document.createElement('div');
    row.className = 'session-list-error';
    row.setAttribute('role', 'status');
    row.textContent = message || '加载会话列表失败';
    sessionsList.appendChild(row);
}

function applyOptimisticSessionUpdate(sessionId, patch) {
    const sid = String(sessionId || '');
    const current = sessionStore.get(sid) || (sessionStore.archivedLoaded
        ? (sessionStore.archivedSessions || []).find(function (session) {
            return session && String(session.id) === sid;
        })
        : null);
    if (!current) return null;
    const prev = Object.assign({}, current);
    const next = Object.assign({}, current, patch || {});
    if (Object.prototype.hasOwnProperty.call(patch || {}, 'pinned')) {
        next.pinned_at = next.pinned ? (next.pinned_at || new Date().toISOString()) : null;
    }
    sessionStore.upsert(next);
    if (prev.archived || next.archived) {
        if (sessionStore.archivedLoaded) {
            const archivedList = (sessionStore.archivedSessions || []).slice();
            const archivedIndex = archivedList.findIndex(function (s) {
                return s && String(s.id) === sid;
            });
            let visibleCount = sessionStore.archivedVisibleCount;
            let totalCount = sessionStore.archivedCount;
            if (prev.archived && next.archived) {
                if (archivedIndex >= 0) archivedList[archivedIndex] = next;
            } else if (prev.archived) {
                if (archivedIndex >= 0) archivedList.splice(archivedIndex, 1);
                if (archivedIndex >= 0 && archivedIndex < visibleCount) visibleCount -= 1;
                totalCount = Math.max(0, totalCount - 1);
            } else if (next.archived) {
                archivedList.unshift(next);
                visibleCount += 1;
                totalCount += 1;
            }
            sessionStore.setArchivedLoaded(archivedList, {
                visibleCount: visibleCount,
                totalCount: totalCount,
            });
            syncArchivedSessionStateFromStore();
        } else if (!!prev.archived !== !!next.archived) {
            sessionStore.setArchivedCount(Math.max(
                0,
                sessionStore.archivedCount + (next.archived ? 1 : -1)
            ));
        }
    }
    renderSessionListIfChanged(true);
    return prev;
}

// Event count cache for optimistic UI updates.
const uiEventCountCache = {
    cache: new Map(),
    maxAgeMs: 10000,
    
    get(sessionId) {
        var entry = this.cache.get(sessionId);
        if (entry && typeof entry === 'object') return Number(entry.count) || 0;
        return Number(entry) || 0;
    },

    has(sessionId) {
        return this.cache.has(sessionId);
    },

    isFresh(sessionId, maxAgeMs) {
        var entry = this.cache.get(sessionId);
        if (!entry || typeof entry !== 'object') return false;
        var age = Date.now() - Number(entry.updatedAt || 0);
        var limit = Number(maxAgeMs) > 0 ? Number(maxAgeMs) : this.maxAgeMs;
        return age >= 0 && age <= limit;
    },
    
    set(sessionId, count) {
        this.cache.set(sessionId, {
            count: Math.max(0, Number(count) || 0),
            updatedAt: Date.now(),
        });
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

async function fetchSessionsStateSnapshot(opts) {
    opts = opts || {};
    const requestSeq = ++sessionStore.snapshotRequestSeq;
    const url = '/sessions/state' + (opts.includeArchived ? '?include_archived=true' : '');
    const response = await fetchWithTimeout(url, {}, 12000);
    if (!response.ok) throw new Error('sessions state failed: ' + response.status);
    const snapshot = await response.json();
    if (!snapshot || !Array.isArray(snapshot.sessions)) {
        throw new Error('invalid sessions state response');
    }
    snapshot.include_archived = !!opts.includeArchived;
    snapshot.client_request_seq = requestSeq;
    return snapshot;
}

function deriveSidebarRuntimeStatus() {
    var busy = false;
    sessionStore.runsBySession.forEach(function () { busy = true; });
    if (!busy) {
        sessionStore.activeRunInfoBySession.forEach(function (info) {
            if (!info || info.run_active !== false) busy = true;
        });
    }
    if (busy) return 'busy';
    return 'online';
}

function updateSidebarRuntimeStatus(nextStatus) {
    var footer = document.querySelector('.sidebar-runtime');
    var status = document.getElementById('sidebar-runtime-status');
    if (!footer || !status) return;
    var state = nextStatus === false ? 'offline'
        : (nextStatus === true || !nextStatus ? deriveSidebarRuntimeStatus() : String(nextStatus));
    if (['online', 'busy', 'waiting', 'alert', 'offline'].indexOf(state) < 0) state = 'online';
    footer.classList.remove('is-online', 'is-busy', 'is-waiting', 'is-alert', 'is-offline');
    footer.classList.add('is-' + state);
    var labels = {
        online: 'Runtime 在线',
        busy: 'Runtime 繁忙',
        waiting: 'Runtime 待处理',
        alert: 'Runtime 告警',
        offline: 'Runtime 离线'
    };
    setUiRuntimeText(status, labels[state]);
    footer.dataset.runtimeStatus = state;
}

var runtimeStatusHeartbeatTimer = null;
var lastUiActivationSeq = 0;
var pendingQuerySession = (function () {
    // Deep link support: /?session=<id> selects that conversation once the
    // session list is ready, then the parameter is stripped so a manual
    // refresh does not yank the user back.
    try {
        var params = new URLSearchParams(window.location.search || '');
        var sid = String(params.get('session') || '').trim();
        if (sid && /^[A-Za-z0-9][A-Za-z0-9\-]{0,63}$/.test(sid)) {
            params.delete('session');
            var nextSearch = params.toString();
            try {
                window.history.replaceState(
                    {}, '',
                    window.location.pathname + (nextSearch ? '?' + nextSearch : '') + (window.location.hash || '')
                );
            } catch (e) { /* history may be unavailable */ }
            return sid;
        }
    } catch (e) { /* ignore */ }
    return '';
})();
async function refreshRuntimeStatus() {
    try {
        var response = await fetchWithTimeout('/api/runtime-status', { cache: 'no-store' }, 5000);
        if (!response.ok) throw new Error('runtime status failed: ' + response.status);
        var payload = await response.json();
        updateSidebarRuntimeStatus(payload && payload.status ? payload.status : true);
        var activationSeq = Number(payload && payload.activation_seq) || 0;
        if (activationSeq > lastUiActivationSeq) {
            lastUiActivationSeq = activationSeq;
            try { window.focus(); } catch (e) { /* browser policy may reject focus */ }
            var activationSession = String((payload && payload.activation_session) || '').trim();
            if (activationSession && activationSession !== currentSessionId
                    && typeof switchSession === 'function') {
                void Promise.resolve(switchSession(activationSession)).catch(function (err) { /* session may be gone */ });
            }
        }
    } catch (error) {
        updateSidebarRuntimeStatus(false);
    }
}

function startRuntimeStatusHeartbeat() {
    if (runtimeStatusHeartbeatTimer) clearInterval(runtimeStatusHeartbeatTimer);
    void refreshRuntimeStatus();
    runtimeStatusHeartbeatTimer = setInterval(refreshRuntimeStatus, 5000);
}

async function fetchWithTimeout(url, options, timeoutMs) {
    options = options || {};
    const ms = Number(timeoutMs) > 0 ? Number(timeoutMs) : 15000;
    if (options.signal) return fetch(url, options);
    const controller = new AbortController();
    const timer = setTimeout(function () { controller.abort(); }, ms);
    const nextOptions = Object.assign({}, options, { signal: controller.signal });
    try {
        return await fetch(url, nextOptions);
    } finally {
        clearTimeout(timer);
    }
}

async function fetchArchivedSessionPage(offset, limit) {
    const url = '/sessions?include_archived=true&archived_only=true&offset=' + String(offset)
        + '&limit=' + String(limit);
    const response = await fetchWithTimeout(url, {}, 15000);
    if (!response.ok) throw new Error('archived sessions failed: ' + response.status);
    const sessions = await response.json();
    const countHeader = response.headers.get('X-Archived-Count');
    const parsedCount = Number(countHeader);
    return {
        sessions: Array.isArray(sessions) ? sessions : [],
        totalCount: Number.isFinite(parsedCount) && parsedCount >= 0
            ? parsedCount
            : Math.max(offset + (Array.isArray(sessions) ? sessions.length : 0), sessionStore.archivedCount),
    };
}

function appendArchivedSessionPage(page, visibleCount) {
    const combined = (sessionStore.archivedSessions || []).concat(page.sessions || []);
    const seen = new Set();
    const deduplicated = combined.filter(function (s) {
        const sid = s && s.id ? String(s.id) : '';
        if (!sid || seen.has(sid)) return false;
        seen.add(sid);
        return true;
    });
    sessionStore.setArchivedLoaded(deduplicated, {
        visibleCount: visibleCount,
        totalCount: page.totalCount,
    });
}

async function prefetchNextArchivedPage(loadEpoch) {
    const cachedCount = Array.isArray(sessionStore.archivedSessions)
        ? sessionStore.archivedSessions.length
        : 0;
    const wantedCount = Math.min(
        sessionStore.archivedCount,
        sessionStore.archivedVisibleCount + ARCHIVED_SESSIONS_PAGE_SIZE
    );
    if (cachedCount >= wantedCount) return;
    const page = await fetchArchivedSessionPage(cachedCount, wantedCount - cachedCount);
    if (loadEpoch !== archivedSessionsLoadEpoch) return;
    appendArchivedSessionPage(page, sessionStore.archivedVisibleCount);
}

async function loadArchivedSessions(opts) {
    opts = opts || {};
    const loadEpoch = ++archivedSessionsLoadEpoch;
    try {
        if (!sessionStore.archivedLoaded) {
            const initialPage = await fetchArchivedSessionPage(0, ARCHIVED_SESSIONS_PAGE_SIZE * 2);
            if (loadEpoch !== archivedSessionsLoadEpoch) return;
            sessionStore.setArchivedLoaded(initialPage.sessions, {
                visibleCount: ARCHIVED_SESSIONS_PAGE_SIZE,
                totalCount: initialPage.totalCount,
            });
        } else if (opts.background || opts.refresh || !sessionStore.hasMoreArchivedSessions()) {
            const refreshLimit = Math.max(
                ARCHIVED_SESSIONS_PAGE_SIZE * 2,
                sessionStore.archivedVisibleCount + ARCHIVED_SESSIONS_PAGE_SIZE
            );
            const refreshedPage = await fetchArchivedSessionPage(0, refreshLimit);
            if (loadEpoch !== archivedSessionsLoadEpoch) return;
            sessionStore.setArchivedLoaded(refreshedPage.sessions, {
                visibleCount: sessionStore.archivedVisibleCount,
                totalCount: refreshedPage.totalCount,
            });
        } else {
            if (sessionStore.revealNextArchivedPage() === 0) {
                const cachedCount = Array.isArray(sessionStore.archivedSessions)
                    ? sessionStore.archivedSessions.length
                    : 0;
                const nextPage = await fetchArchivedSessionPage(cachedCount, ARCHIVED_SESSIONS_PAGE_SIZE);
                if (loadEpoch !== archivedSessionsLoadEpoch) return;
                appendArchivedSessionPage(nextPage, sessionStore.archivedVisibleCount);
                sessionStore.revealNextArchivedPage();
            }
            syncArchivedSessionStateFromStore();
            renderSessionListIfChanged(true);
            clearSessionListError();
            try {
                await prefetchNextArchivedPage(loadEpoch);
            } catch (prefetchErr) {
                console.error('预加载下一批归档目录失败:', prefetchErr);
            }
        }
        if (loadEpoch !== archivedSessionsLoadEpoch) return;
        syncArchivedSessionStateFromStore();
        renderSessionListIfChanged(!!opts.forceRender);
        clearSessionListError();
    } catch (err) {
        console.error('加载归档目录失败:', err);
        if (!opts.background) throw err;
    }
}

async function loadSessions(opts) {
    opts = opts || {};
    if (sessionListLoadPromise && !opts.force) return sessionListLoadPromise;
    sessionListLoadPromise = loadSessionsInner(opts);
    try {
        return await sessionListLoadPromise;
    } finally {
        sessionListLoadPromise = null;
    }
}

async function loadSessionsInner(opts) {
    const loadEpoch = ++sessionListLoadEpoch;
    sessionStore.ui.loadingSessions = true;
    try {
        let allSessions;
        let snapshot = null;
        
        try {
            snapshot = await fetchSessionsStateSnapshot();
            if (loadEpoch !== sessionListLoadEpoch) return;
            updateSidebarRuntimeStatus(true);
            allSessions = Array.isArray(snapshot.sessions) ? snapshot.sessions : [];
        } catch (stateErr) {
            console.error('加载会话状态快照失败，回退至旧接口', stateErr);
            const fallbackRequestSeq = ++sessionStore.snapshotRequestSeq;
            const response = await fetchWithTimeout('/sessions', {}, 12000);
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
            updateSidebarRuntimeStatus(true);
            allSessions = Array.isArray(sessions) ? sessions : [];
            snapshot = {
                sessions: allSessions,
                archived_count: archivedSessionsCount,
                client_request_seq: fallbackRequestSeq,
            };
        }
        applySessionSnapshot(snapshot || { sessions: allSessions, archived_count: archivedSessionsCount });
        syncArchivedSessionStateFromStore();
        allSessions = sessionStore.list();
        
        const idSet = new Set();
        for (let si = 0; si < allSessions.length; si += 1) {
            if (allSessions[si] && allSessions[si].id) idSet.add(allSessions[si].id);
        }
        [...sessionUnreadComplete].forEach(function (uid) {
            if (!idSet.has(uid)) sessionUnreadComplete.delete(uid);
        });
        persistSessionUnread();

        if (pendingQuerySession) {
            var queryTarget = pendingQuerySession;
            pendingQuerySession = '';
            var known = allSessions.some(function (s) { return s && s.id === queryTarget; });
            if (known && queryTarget !== currentSessionId && typeof switchSession === 'function') {
                void Promise.resolve(switchSession(queryTarget)).catch(function (err) { /* ignore */ });
            }
        }

        renderSessionListIfChanged(!!opts.forceRender);
        clearSessionListError();
        sessionStore.ui.loadingSessions = false;
        if (opts.refreshArchived && !opts.skipArchivedRefresh && sessionStore.archivedLoaded) {
            void loadArchivedSessions({ background: true });
        }
        return true;
    } catch (error) {
        sessionStore.ui.loadingSessions = false;
        updateSidebarRuntimeStatus(false);
        console.error('加载会话列表失败:', error);
        if (sessionStore.list().length > 0) {
            renderSessionListIfChanged(true);
            clearSessionListError();
        } else {
            renderSessionListError('加载会话列表失败');
        }
        return false;
    }
}

async function reconcileRunStateFromServer(opts) {
    opts = opts || {};
    const suppressedBeforeFetch = new Set();
    if (opts.respectStopSuppress) {
        sessionStore.sessionOrder.forEach(function (sid) {
            if (isSessionStreamStopSuppressed(sid)) suppressedBeforeFetch.add(String(sid));
        });
        if (currentSessionId && isSessionStreamStopSuppressed(currentSessionId)) {
            suppressedBeforeFetch.add(String(currentSessionId));
        }
    }
    let snapshot = null;
    try {
        const cur = currentSessionId ? sessionStore.get(currentSessionId) : null;
        snapshot = await fetchSessionsStateSnapshot({
            includeArchived: !!(sessionStore.archivedLoaded || (cur && cur.archived)),
        });
    } catch (e) {
        updateSidebarRuntimeStatus(false);
        if (!opts.silent) console.error('reconcile run state failed:', e);
        return;
    }
    applySessionSnapshot(snapshot);
    updateSidebarRuntimeStatus(true);
    if (opts.respectStopSuppress) {
        suppressedBeforeFetch.forEach(function (sid) {
            if (isSessionStreamStopSuppressed(sid)) {
                sessionStore.setStreamActive(sid, false);
                const sess = sessionStore.get(sid);
                if (sess) {
                    sess.stream_active = false;
                    sess.run_active = false;
                    sess.run_started_at = null;
                }
                sessionStore.activeRunInfoBySession.delete(sid);
            }
        });
    }
    const active = new Set();
    sessionStore.activeRunInfoBySession.forEach(function (info, sid) {
        if (info && info.run_active === true) active.add(String(sid));
    });
    const localIds = [];
    sessionStore.runsBySession.forEach(function (_run, sid) {
        localIds.push(String(sid));
    });
    localIds.forEach(function (sid) {
        if (!active.has(sid)) {
            var run = getSessionRunState(sid);
            var staleSubmittedStream = !!(
                run && run.submitted && run.ctx && run.ctx.streamConsuming
            );
            if (run && (run.reattached || staleSubmittedStream || run.transportClosed)) {
                abortSessionRun(sid, 'reconcile-finished');
            }
        }
    });
    if (currentSessionId && active.has(currentSessionId)) {
        const info = sessionStore.getActiveRunInfo(currentSessionId) || {};
        const run = getSessionRunState(currentSessionId);
        const ctx = run && run.ctx;
        const agg = ctx && ctx.currentProcessGroup && ctx.currentProcessGroup.isConnected
            ? ctx.currentProcessGroup
            : (getVisibleChatStream() && getVisibleChatStream().querySelector('.process-aggregate:last-of-type'));
        if (agg && info.started_at) applyRunStartedAtToProcessGroup(agg, info.started_at);
    }
    syncSessionListIndicatorClasses();
    setSendButtonState();
    renderSessionListIfChanged(false);
}

function showSessionLoadRetry(sessionId) {
    var sid = String(sessionId || '');
    var stream = getVisibleChatStream();
    if (!sid || !stream) return;
    if (stream.querySelector('.session-load-retry')) return;
    var row = document.createElement('div');
    row.className = 'feed-item feed--err session-load-retry';
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'history-load-older-btn';
    btn.textContent = '重新加载';
    btn.addEventListener('click', function (e) {
        e.preventDefault();
        if (typeof discardCachedSessionStream === 'function') discardCachedSessionStream(sid);
        void switchSession(sid, { forceReload: true });
    });
    row.appendChild(btn);
    stream.appendChild(row);
}

async function loadSessionMessages(sessionId, scrollBehavior, opts) {
    const openSessionStartedAt = (typeof performance !== 'undefined' && performance.now)
        ? performance.now()
        : Date.now();
    scrollBehavior = scrollBehavior || 'saved-or-bottom';
    opts = opts || {};
    const loadToken = ++messageLoadEpoch;
    let historyHydrationStream = null;
    const finishHistoryHydration = function () {
        if (historyHydrationStream) {
            historyHydrationStream.hidden = false;
            historyHydrationStream = null;
        }
        if (loadToken === messageLoadEpoch) hideLoading();
        if (typeof attachAllHumanInteractionCards === 'function') {
            attachAllHumanInteractionCards(getVisibleChatStream());
        }
    };
    sessionStore.ui.loadingMessages = true;
    suppressTocDuringSessionLoad = true;
    replayingMessages = true;
    if (typeof cancelSmoothStreamFollowForHistoryLoad === 'function') {
        cancelSmoothStreamFollowForHistoryLoad();
    }
    resetSessionHistoryPaging();
    try {
        let raw;
        let snapshotTocTurns = null;
        let historySource = 'messages';
        let snapshotTiming = null;
        const canUseSnapshot = !opts.full && opts.useSnapshot !== false && beforeSessionMessageSnapshotAvailable();
        if (canUseSnapshot) {
            try {
                const snapshotUrl = '/sessions/' + encodeURIComponent(sessionId)
                    + '/history_snapshot?turns=' + encodeURIComponent(String(HISTORY_DIALOGUES_PER_PAGE))
                    + '&event_budget=' + encodeURIComponent(String(HISTORY_EVENT_BUDGET))
                    + '&include_aux=false';
                for (let migrationAttempt = 0; migrationAttempt < 120; migrationAttempt += 1) {
                    const snapshotResp = await fetchWithTimeout(snapshotUrl, {}, 15000);
                    const snapshot = await snapshotResp.json().catch(function () { return null; });
                    if (snapshot && snapshot.migration_pending) {
                        if (loadToken !== messageLoadEpoch || sessionId !== currentSessionId) return;
                        const retryMs = Math.max(100, Math.min(Number(snapshot.retry_after_ms) || 250, 1000));
                        await new Promise(function (resolve) { setTimeout(resolve, retryMs); });
                        continue;
                    }
                    if (snapshotResp.ok) {
                    if (snapshot && snapshot.ok && snapshot.messages) {
                        raw = snapshot.messages;
                        historySource = 'history_snapshot';
                        snapshotTiming = snapshot.timing && typeof snapshot.timing === 'object'
                            ? snapshot.timing
                            : null;
                        if (typeof uiEventCountCache !== 'undefined' && typeof snapshot.count === 'number') {
                            uiEventCountCache.updateFromServer(sessionId, snapshot.count);
                        }
                        if (Array.isArray(snapshot.user_turns)) {
                            snapshotTocTurns = snapshot.user_turns;
                            if (typeof setTocTurnsForSession === 'function') setTocTurnsForSession(sessionId, snapshot.user_turns);
                        }
                        if (snapshot.context_tokens && snapshot.context_tokens.estimated != null) {
                            recordContextTokens(sessionId, snapshot.context_tokens.estimated, snapshot.context_tokens.threshold);
                        }
                        if (typeof snapshot.stream_active === 'boolean' || typeof snapshot.run_active === 'boolean') {
                            const __snapActive = !!(snapshot.stream_active || snapshot.run_active);
                            sessionStore.applyActiveRunForSession(
                                sessionId,
                                snapshot.active_run || (__snapActive ? {
                                    session_id: sessionId,
                                    run_active: true,
                                    started_at: snapshot.run_started_at || null,
                                    runtime_v2: snapshot.source === 'runtime_v2_snapshot',
                                } : null)
                            );
                        }
                    }
                    }
                    break;
                }
            } catch (snapshotErr) {
                console.warn('history snapshot unavailable, falling back to messages:', snapshotErr);
            }
        }
        if (!raw) {
            let url = '/sessions/' + encodeURIComponent(sessionId) + '/messages';
            if (!opts.full) {
                url += '?turns=' + HISTORY_DIALOGUES_PER_PAGE
                    + '&event_budget=' + encodeURIComponent(String(HISTORY_EVENT_BUDGET));
            }
            const response = await fetchWithTimeout(url, {}, 15000);
            if (!response.ok) throw new Error('messages failed: ' + response.status);
            raw = await response.json();
        }
        if (loadToken !== messageLoadEpoch || sessionId !== currentSessionId) return;
        if (getSessionRunState(sessionId) && !opts.allowDuringRun) return;
        if (typeof uiPerformance !== 'undefined') uiPerformance.sample(sessionId, 'history.fetch', elapsedSince(openSessionStartedAt));
        if (!getVisibleChatStream()) ensureVisibleChatStreamSlot();
        const vis = getVisibleChatStream();
        if (vis) {
            const loader = document.getElementById('chat-loading');
            if (loader && loader.parentNode === vis && chatContainer) {
                chatContainer.insertBefore(loader, vis);
            }
            vis.hidden = true;
            historyHydrationStream = vis;
            emptyChatStreamKeepingStrip(vis);
        }
        else {
            chatContainer.innerHTML = '';
            ensureVisibleChatStreamSlot();
        }
        markVisibleSessionStreamLoadState(sessionId, 'loading');
        let events;
        let pageMeta = null;
        if (Array.isArray(raw)) {
            events = raw;
        } else if (raw && typeof raw === 'object' && Array.isArray(raw.events)) {
            events = raw.events;
            const pageTotal = Number(raw.total) || 0;
            const pageRangeEnd = Number(raw.range_end) || 0;
            pageMeta = {
                total: pageTotal,
                range_start: Number(raw.range_start) || 0,
                range_end: pageRangeEnd,
                has_older: !!raw.has_older,
                has_newer: raw.has_newer == null ? pageRangeEnd < pageTotal : !!raw.has_newer,
            };
            uiEventCountCache.updateFromServer(sessionId, pageMeta.total);
        } else {
            events = [];
        }
        beginMessageReplay(sessionId, pageMeta || {
            total: events.length,
            range_start: 0,
            range_end: events.length,
        });
        if (!opts.full && pageMeta) {
            setSessionHistoryPaging({
                sessionId: sessionId,
                total: pageMeta.total,
                range_start: pageMeta.range_start,
                range_end: pageMeta.range_end,
                has_older: !!pageMeta.has_older,
                has_newer: !!pageMeta.has_newer,
            });
            ensureHistorySentinel(getVisibleChatStream());
        }
        if (events.length === 0) {
            suppressTocDuringSessionLoad = false;
            setWelcome();
            finishHistoryHydration();
            updateSessionTitle();
            scheduleContextTokensAfterPaint(sessionId);
            applyChatScrollAfterHistoryLoad(sessionId, scrollBehavior);
            markVisibleSessionStreamLoadState(sessionId, 'ok');
            logOpenSessionTiming(sessionId, {
                source: historySource,
                events: 0,
                snapshotTiming: snapshotTiming,
                totalMs: elapsedSince(openSessionStartedAt),
            });
            return true;
        }
        const loadCtx = newDomContext(getVisibleChatStream());
        const hydrationStartedAt = performance.now();
        loadCtx.lastUserEventIndex = -1;
        const indexBase = pageMeta ? pageMeta.range_start : 0;
        const batchSize = opts.full ? 64 : 512;
        for (let evi = 0; evi < events.length; evi += 1) {
            const ev = events[evi];
            if (ev && typeof ev === 'object' && ev.type) {
                reduceAndRenderMessageEvent(loadCtx, ev, {
                    sessionId: sessionId,
                    eventIndex: indexBase + evi,
                    source: 'history',
                });
            }
            if (evi > 0 && evi % batchSize === 0) {
                await new Promise(function (resolve) { setTimeout(resolve, 0); });
                if (loadToken !== messageLoadEpoch || sessionId !== currentSessionId) return;
            }
        }
        if (typeof uiPerformance !== 'undefined') {
            uiPerformance.sample(sessionId, 'history.hydrate', performance.now() - hydrationStartedAt);
            uiPerformance.count(sessionId, 'history.events', events.length);
        }
        var imageLayoutStartedAt = performance.now();
        var historyScrollBehavior = scrollBehavior;
        if ((scrollBehavior === 'smooth-bottom' || scrollBehavior === 'bottom') && typeof prepareWorkspaceImageLayout === 'function') {
            await prepareWorkspaceImageLayout(getVisibleChatStream());
            if (loadToken !== messageLoadEpoch || sessionId !== currentSessionId) return;
        }
        var historyImagesReady = await waitForHistoryImageLayout(
            sessionId,
            scrollBehavior,
            getVisibleChatStream()
        );
        if (loadToken !== messageLoadEpoch || sessionId !== currentSessionId) return;
        if (scrollBehavior === 'smooth-bottom' && !historyImagesReady) {
            historyScrollBehavior = 'bottom';
        }
        if (typeof uiPerformance !== 'undefined') {
            uiPerformance.sample(sessionId, 'history.images', performance.now() - imageLayoutStartedAt);
            if (!historyImagesReady) uiPerformance.count(sessionId, 'history.imageFallbacks');
        }
        finishHistoryHydration();
        if (!chatStreamHasConversationContent()) {
            suppressTocDuringSessionLoad = false;
            setWelcome();
            updateSessionTitle();
            scheduleContextTokensAfterPaint(sessionId);
            applyChatScrollAfterHistoryLoad(sessionId, scrollBehavior);
            markVisibleSessionStreamLoadState(sessionId, 'ok');
            logOpenSessionTiming(sessionId, {
                source: historySource,
                events: events.length,
                snapshotTiming: snapshotTiming,
                totalMs: elapsedSince(openSessionStartedAt),
            });
            return true;
        }
        if (!opts.full && opts.preloadOlderIfShort && pageMeta && pageMeta.has_older && events.length <= 2) {
            await loadOlderHistoryChunk({ keepTocStable: true });
            if (loadToken !== messageLoadEpoch || sessionId !== currentSessionId) return;
        }
        if (historyLoadScrollsToBottom(sessionId, historyScrollBehavior)) {
            tocScrollBottomOnNextBuild = true;
        }
        suppressTocDuringSessionLoad = false;
        if (snapshotTocTurns) rebuildToc({ turns: snapshotTocTurns });
        else if (!opts.tocAlreadyStarted) rebuildToc();
        updateSessionTitle();
        updateHistorySentinelVisibility();
        bindExistingLogInteractions();
        var historyScrollStartedAt = performance.now();
        // Completed unread results have no animation phase. Settle the log
        // layout before placing the viewport, including queued overflow work.
        if (historyScrollBehavior === 'bottom') finalizeExistingLogLayout();
        applyChatScrollAfterHistoryLoad(sessionId, historyScrollBehavior);
        var initialSmoothReachedBottom = await waitForChatScrollAfterHistoryLoad(sessionId, historyScrollBehavior);
        if (loadToken !== messageLoadEpoch || sessionId !== currentSessionId) return;
        if (historyScrollBehavior !== 'bottom') finalizeExistingLogLayout();
        if (typeof uiPerformance !== 'undefined') uiPerformance.sample(sessionId, 'history.scroll', performance.now() - historyScrollStartedAt);
        if (historyScrollBehavior === 'smooth-bottom' && initialSmoothReachedBottom) {
            setScrollTopImmediate(chatContainer, chatContainer.scrollHeight);
            requestAnimationFrame(function () {
                requestAnimationFrame(function () {
                    if (loadToken !== messageLoadEpoch || sessionId !== currentSessionId) return;
                    setScrollTopImmediate(chatContainer, chatContainer.scrollHeight);
                });
            });
        }
        scheduleTocActiveUpdate();
        scheduleContextTokensAfterPaint(sessionId);
        markVisibleSessionStreamLoadState(sessionId, 'ok');
        logOpenSessionTiming(sessionId, {
            source: historySource,
            events: events.length,
            snapshotTiming: snapshotTiming,
            totalMs: elapsedSince(openSessionStartedAt),
        });
        return true;
    } catch (error) {
        if (loadToken !== messageLoadEpoch || sessionId !== currentSessionId) return false;
        console.error('加载会话消息失败:', error);
        document.getElementById('chat-loading')?.remove();
        appendLogVisible('加载历史消息失败', 'error-log');
        markVisibleSessionStreamLoadState(sessionId, 'failed');
        showSessionLoadRetry(sessionId);
        return false;
    } finally {
        finishHistoryHydration();
        if (loadToken === messageLoadEpoch) sessionStore.ui.loadingMessages = false;
        if (loadToken === messageLoadEpoch) suppressTocDuringSessionLoad = false;
        if (loadToken === messageLoadEpoch) replayingMessages = false;
    }
}

function chatStreamHasConversationContent() {
    var stream = getVisibleChatStream();
    if (!stream) return false;
    return !!stream.querySelector('.msg-wrap, .process-aggregate, .human-interaction-card, .human-interaction-banner');
}

function elapsedSince(startedAt) {
    var now = (typeof performance !== 'undefined' && performance.now)
        ? performance.now()
        : Date.now();
    return Math.max(0, Math.round(now - Number(startedAt || now)));
}

function logOpenSessionTiming(sessionId, data) {
    data = data || {};
    var timing = data.snapshotTiming && typeof data.snapshotTiming === 'object' ? data.snapshotTiming : {};
    var backendTotal = Number(timing.total || 0);
    var frontendTotal = Number(data.totalMs || 0);
    if (typeof uiPerformance !== 'undefined') {
        uiPerformance.sample(sessionId, 'history.total', frontendTotal);
        if (timing.total != null && Number.isFinite(Number(timing.total))) {
            uiPerformance.sample(sessionId, 'history.backend', backendTotal);
        }
    }
    if (frontendTotal < 500 && backendTotal < 500) return;
    console.info(
        'open_session_timing session=%s source=%s total=%sms events=%s backend_total=%sms read_page=%sms count=%sms user_turns=%sms context_tokens=%sms',
        sessionId,
        data.source || 'unknown',
        frontendTotal,
        Number(data.events || 0),
        backendTotal,
        Number(timing.read_page || 0),
        Number(timing.count || 0),
        Number(timing.user_turns || 0),
        Number(timing.context_tokens || 0)
    );
}

function beforeSessionMessageSnapshotAvailable() {
    return true;
}

async function switchSession(sessionId, opts) {
    opts = opts || {};
    if (typeof endHistorySmoothScroll === 'function') endHistorySmoothScroll();
    if (currentSessionId === sessionId && !opts.forceReload) return;
    const switchStartedAt = performance.now();
    if (opts.forceReload && typeof discardCachedSessionStream === 'function') discardCachedSessionStream(sessionId);
    const switchToken = ++switchSessionEpoch;
    // Cached restores do not start a new message request. Invalidate the old
    // request here as well, including A -> B -> A switches during hydration.
    messageLoadEpoch += 1;
    sessionStore.ui.loadingMessages = false;
    replayingMessages = false;
    cancelSmoothStreamFollowForSessionSwitch();
    suppressTocDuringSessionLoad = true;
    clearTocForSessionLoad();
    clearOptionalPanelsForSessionLoad();
    pendingRewriteTruncate = null;
    hideRewriteUndoToast();
    // A green-dot session represents an unread completed result. Opening it
    // must land at the newest result, never at a stale reading anchor.
    var sessionHadUnreadResult = !!(
        (sessionStore.get(sessionId) && sessionStore.get(sessionId).unread_result)
        || sessionUnreadComplete.has(sessionId)
    );
    clearSessionUnreadState(sessionId);
    const leaving = currentSessionId;
    recentComposerQueuedFollowup = null;
    saveChatScrollForSession(leaving);
    stashInputDraft(leaving);
    if (typeof stashSkillPickerDraft === 'function') stashSkillPickerDraft(leaving);
    prepareStashLeaving(leaving);
    hideSubagentContinueBanner();
    resetSubagentPanelForSession();
    setCurrentSessionState(sessionId);
    // The session identity and its side-panel contents must cross the switch
    // boundary together. Waiting for history requests leaves the previous
    // session title or plan visible for a frame (and sometimes much longer on
    // a cold load).
    updateSessionTitle();
    if (typeof updateHumanInteractionBanner === 'function') updateHumanInteractionBanner(sessionId);
    localStorage.setItem('lastSessionId', sessionId);
    if (typeof applyContextTokenLabelForCurrentSession === 'function') applyContextTokenLabelForCurrentSession();
    restoreInputDraft(sessionId);
    if (typeof restoreSkillPickerDraft === 'function') restoreSkillPickerDraft(sessionId);
    if (typeof renderFollowupQueue === 'function') renderFollowupQueue(sessionId);
    if (typeof syncFollowupQueueFromServer === 'function') syncFollowupQueueFromServer(sessionId);
    if (typeof refreshModelProfileSelector === 'function') refreshModelProfileSelector(sessionId);
    syncSessionListIndicatorClasses();
    // Refresh extension panels only after the new row owns the active marker;
    // otherwise the projection request can render the session we just left.
    document.dispatchEvent(new CustomEvent('myagent:extension-state-changed', {
        detail: { sessionId: sessionId },
    }));
    setSendButtonState();
    if (!isSessionRunning(sessionId) && !(typeof isServerStreamActive === 'function' && isServerStreamActive(sessionId))) {
        let __shouldPreflight = true;
        try {
            const __sess = sessionStore.get(sessionId);
            const __last = __sess ? Date.parse(__sess.last_activity_at || __sess.updated_at || __sess.created_at || "") : 0;
            if (Number.isFinite(__last) && Date.now() - __last > 12 * 60 * 1000) __shouldPreflight = false;
        } catch (e) {}
        if (__shouldPreflight) {
            try {
                await Promise.race([
                    (async () => {
                        if (typeof refreshSingleSessionRow === 'function') await refreshSingleSessionRow(sessionId);
                    })(),
                    new Promise(resolve => setTimeout(resolve, 450))
                ]);
            } catch (e) { /* preflight best-effort */ }
        }
    }
    if (switchToken !== switchSessionEpoch || sessionId !== currentSessionId) {
        if (typeof uiPerformance !== 'undefined') uiPerformance.count(sessionId, 'switch.cancelled');
        return false;
    }
    if (typeof uiPerformance !== 'undefined') uiPerformance.sample(sessionId, 'switch.prepare', performance.now() - switchStartedAt);
    var restoredFromCache = false;
    var restoredRunningStream = false;
    var sessionHasActiveServerRun = !!(
        isSessionRunning(sessionId)
        || (typeof isServerStreamActive === 'function' && isServerStreamActive(sessionId))
    );
    if (!opts.forceReload && (
        (restoredRunningStream = restoreStreamForRunningSession(sessionId))
        || (!sessionHadUnreadResult
            && !sessionHasActiveServerRun
            && (restoredFromCache = restoreCachedSessionStream(sessionId)))
    )) {
        suppressTocDuringSessionLoad = false;
        hideLoading();
        rebuildToc({ localOnly: true });
        updateSessionTitle();
        scheduleContextTokensAfterPaint(sessionId);
        // Only a complete, idle stream restored from the in-memory cache may
        // return to its prior reading position. A live run and a green-dot
        // completion always open on their newest content.
        var sessionIsRunningNow = !!(
            restoredRunningStream
            || isSessionRunning(sessionId)
            || (typeof isServerStreamActive === 'function' && isServerStreamActive(sessionId))
        );
        if (restoredFromCache && !sessionHadUnreadResult && !sessionIsRunningNow) {
            restoreCachedSessionScrollPosition(sessionId);
        } else {
            streamChatNearBottom = true;
            streamProcNearBottom = true;
            liveAutoFollow = true;
            scrollToBottom();
            if (sessionIsRunningNow && typeof scrollCurrentRunningProcessToBottom === 'function') {
                scrollCurrentRunningProcessToBottom(sessionId);
            }
        }
        if (typeof refreshHumanInteractions === 'function') void refreshHumanInteractions(sessionId);
        if (switchToken !== switchSessionEpoch || sessionId !== currentSessionId) return;
        /* 让 rebuildToc 的 /user_turns fetch 先发出，subagent 面板（含 N 个 /messages）顺序后置，
           避免抢占带宽与主线程，让目录最后才稳态。*/
        setTimeout(function () {
            if (switchToken === switchSessionEpoch && sessionId === currentSessionId) {
                refreshSubagentTreePanel(sessionId);
            }
        }, 0);
        void refreshSingleSessionRow(sessionId);
        document.dispatchEvent(new CustomEvent('myagent:extension-state-changed', {
            detail: { sessionId: sessionId, phase: 'loaded' },
        }));
        setSendButtonState();
        maybeStartStreamPollForSession(sessionId, { skipInitialLoad: true });
        if (typeof uiPerformance !== 'undefined') uiPerformance.sample(sessionId,
            restoredRunningStream ? 'switch.live' : 'switch.cached', performance.now() - switchStartedAt);
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
    const tocAlreadyStarted = opts.useSnapshot === false && typeof startTocForSessionLoad === 'function';
    if (tocAlreadyStarted) startTocForSessionLoad(sessionId);
    return new Promise(function (resolve) {
        setTimeout(async function () {
        if (switchToken !== switchSessionEpoch || sessionId !== currentSessionId) { resolve(false); return; }
        try {
            // Capture unread intent before clearing the badge and preserve it
            // through the async load: completed results open without a glide.
            var loadedOk = await loadSessionMessages(sessionId, sessionHadUnreadResult ? 'bottom' : 'smooth-bottom', {
                preloadOlderIfShort: isServerStreamActive(sessionId),
                allowDuringRun: isServerStreamActive(sessionId),
                tocAlreadyStarted: tocAlreadyStarted,
            });
            if (!loadedOk) { resolve(false); return; }
        } catch (error) {
            console.error('切换会话加载失败:', error);
            resolve(false);
            return;
        } finally {
            if (switchToken === switchSessionEpoch && sessionId === currentSessionId) {
                hideLoading();
                sessionStore.ui.loadingMessages = false;
                suppressTocDuringSessionLoad = false;
                replayingMessages = false;
            }
        }
        if (switchToken !== switchSessionEpoch || sessionId !== currentSessionId) { resolve(false); return; }
        /* loadSessionMessages 内部已发起 rebuildToc()；这里再延后一步调用 subagent panel
           重建，保证「目录 → 消息 → 副 agent 按钮」的稳定顺序（无 subagent 的会话表现一致）。*/
        setTimeout(function () {
            if (switchToken === switchSessionEpoch && sessionId === currentSessionId) {
                refreshSubagentTreePanel(sessionId);
            }
        }, 0);
        void refreshSingleSessionRow(sessionId);
        document.dispatchEvent(new CustomEvent('myagent:extension-state-changed', {
            detail: { sessionId: sessionId, phase: 'loaded' },
        }));
        setSendButtonState();
        maybeStartStreamPollForSession(sessionId, { skipInitialLoad: true });
        if (typeof refreshHumanInteractions === 'function') void refreshHumanInteractions(sessionId);
        if (typeof uiPerformance !== 'undefined') uiPerformance.sample(sessionId, 'switch.loaded', performance.now() - switchStartedAt);
        resolve(true);
        }, 20);
    });
}

async function createNewSession() {
    const leavingSessionId = currentSessionId;
    if (!leavingSessionId) {
        setCurrentSessionState(null);
        localStorage.setItem('lastSessionId', NEW_SESSION_DRAFT_KEY);
        if (!getVisibleChatStream()) ensureVisibleChatStreamSlot();
        const draftStream = getVisibleChatStream();
        if (!draftStream || !draftStream.querySelector('.welcome')) setWelcome();
        restoreInputDraft(null);
        if (typeof restoreSkillPickerDraft === 'function') restoreSkillPickerDraft(null);
        updateSessionTitle();
        syncSessionListIndicatorClasses();
        setSendButtonState();
        if (messageInput) messageInput.focus();
        return null;
    }

    cancelSmoothStreamFollowForSessionSwitch();
    saveChatScrollForSession(leavingSessionId);
    stashInputDraft(leavingSessionId);
    if (typeof stashSkillPickerDraft === 'function') stashSkillPickerDraft(leavingSessionId);
    prepareStashLeaving(leavingSessionId);
    hideSubagentContinueBanner();
    resetSubagentPanelForSession();
    clearOptionalPanelsForSessionLoad();
    clearTocForSessionLoad();
    switchSessionEpoch += 1;
    messageLoadEpoch += 1;
    setCurrentSessionState(null);
    localStorage.setItem('lastSessionId', NEW_SESSION_DRAFT_KEY);
    if (!getVisibleChatStream()) ensureVisibleChatStreamSlot();
    setWelcome();
    restoreInputDraft(null);
    if (typeof restoreSkillPickerDraft === 'function') restoreSkillPickerDraft(null);
    if (typeof renderFollowupQueue === 'function') renderFollowupQueue(null);
    if (typeof refreshModelProfileSelector === 'function') refreshModelProfileSelector(null);
    updateSessionTitle();
    syncSessionListIndicatorClasses();
    replayingMessages = false;
    hideLoading();
    setSendButtonState();
    document.dispatchEvent(new CustomEvent('myagent:extension-state-changed', {
        detail: { sessionId: null, phase: 'draft' },
    }));
    if (messageInput) messageInput.focus();
    return null;
}

async function materializeNewSession() {
    if (currentSessionId) return currentSessionId;
    if (materializeNewSessionQueue) return materializeNewSessionQueue;
    materializeNewSessionQueue = Promise.resolve()
        .then(function () { return materializeNewSessionInner(); })
        .finally(function () {
            materializeNewSessionQueue = null;
        });
    return materializeNewSessionQueue;
}

async function materializeNewSessionInner() {
    const draftEpoch = switchSessionEpoch;
    const createStartedAt = performance.now();
    try {
        const response = await fetch('/sessions', { method: 'POST' });
        if (!response.ok) throw new Error('HTTP ' + response.status);
        const data = await response.json();
        if (!data || !data.session_id) throw new Error('服务端未返回会话 ID');
        const sessionId = String(data.session_id);
        const session = data.session || { id: sessionId, name: '新会话' };
        sessionStore.protectFromSnapshots(session);

        const ownsDraft = !currentSessionId && switchSessionEpoch === draftEpoch;
        const draftText = ownsDraft && messageInput
            ? messageInput.value
            : readStoredInputDraft(null);
        persistInputDraft(sessionId, draftText);
        removeStoredInputDraft(null);

        if (ownsDraft) {
            setCurrentSessionState(sessionId);
            if (typeof updateHumanInteractionBanner === 'function') updateHumanInteractionBanner(sessionId);
            localStorage.setItem('lastSessionId', sessionId);
            // Do not restore the composer here: it is already the live source
            // of truth and restoring an older value causes the visible blink.
            if (typeof renderFollowupQueue === 'function') renderFollowupQueue(sessionId);
            if (typeof syncFollowupQueueFromServer === 'function') syncFollowupQueueFromServer(sessionId);
            if (typeof refreshModelProfileSelector === 'function') refreshModelProfileSelector(sessionId);
        }
        syncArchivedSessionStateFromStore();
        renderSessionListIfChanged(false);
        document.dispatchEvent(new CustomEvent('myagent:extension-state-changed', {
            detail: { sessionId: sessionId, phase: 'created' },
        }));
        if (typeof uiPerformance !== 'undefined') {
            uiPerformance.sample(sessionId, 'session.create', performance.now() - createStartedAt);
        }
        return sessionId;
    } catch (error) {
        console.error('创建新会话失败', error);
        if (!currentSessionId && switchSessionEpoch === draftEpoch) {
            appendLogVisible('创建新会话失败，请重试发送', 'error-log');
        }
        return null;
    }
}
