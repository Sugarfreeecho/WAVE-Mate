function subagentMoreDotsHtml() {
    return '<span class="session-more-dots" aria-hidden="true"><span></span><span></span><span></span></span>';
}

function subagentSortKey(n) {
    var t = Date.parse(String((n && (n.updated_at || n.created_at)) || ''));
    return isNaN(t) ? 0 : t;
}

function sortSubagentsByUpdated(flat) {
    return (flat || []).slice().sort(function (a, b) {
        return subagentSortKey(b) - subagentSortKey(a);
    });
}

function subagentStatusFromNode(n) {
    var taskStatus = String((n && (n.task_status || n.status)) || '').toLowerCase();
    var hasFinalKnown = !!(n && Object.prototype.hasOwnProperty.call(n, 'has_final'));
    var hasPreview = !!String((n && n.result_preview) || '').trim();
    var hasFinal = !n || !hasFinalKnown ? hasPreview : !!n.has_final;
    var canTreatCompleted = hasFinal || (!hasFinalKnown && hasPreview) || (n && n.virtual_task && hasPreview && !hasFinalKnown);
    if (n && n.running) {
        return { label: n.background ? '后台运行' : '运行中', dotCls: 'is-running' };
    }
    if (taskStatus === 'running') return { label: '后台运行', dotCls: 'is-running' };
    if (taskStatus === 'completed' && canTreatCompleted) return { label: '完成', dotCls: 'is-done' };
    if (taskStatus === 'completed') return { label: '缺少 final 结果', dotCls: 'is-error' };
    if (taskStatus === 'failed') return { label: '失败', dotCls: 'is-error' };
    if (taskStatus === 'interrupted') return { label: '已中断', dotCls: 'is-error' };
    if (n && n.ok === false) {
        var err = String(n.error || n.result_preview || '').trim();
        if (/interrupt/i.test(err)) return { label: '已中断', dotCls: 'is-error' };
        return { label: '失败', dotCls: 'is-error' };
    }
    if (n && n.status === 'interrupted') return { label: '已中断', dotCls: 'is-error' };
    if (n && n.status === 'failed') return { label: '失败', dotCls: 'is-error' };
    var prev = String((n && n.result_preview) || '').trim();
    if (/^Error:|^错误|失败|异常|interrupt/i.test(prev)) {
        return { label: '失败', dotCls: 'is-error' };
    }
    return { label: '完成', dotCls: 'is-done' };
}

function subagentCardViewModel(n) {
    n = n || {};
    var id = String(n.id || '');
    var running = !!n.running && !n.virtual_task;
    var name = n.description || id.slice(0, 8);
    return {
        id: id,
        running: running,
        name: name,
        idShort: id.length > 5 ? id.slice(0, 5) + '...' : id,
        typeLabel: n.subagent_type || 'subagent',
        status: subagentStatusFromNode(n),
        resultPreview: String(n.result_preview || '').trim(),
        outputFile: !!n.output_file,
        taskStatus: n.task_status || n.status || '',
        hasFinalKnown: Object.prototype.hasOwnProperty.call(n, 'has_final'),
        hasFinal: !!n.has_final,
        executorModel: n.executor_model || '',
    };
}

function renderSubagentCardHtml(n) {
    var vm = subagentCardViewModel(n);
    if (!vm.id) return '';
    var stopBtn = vm.running ? '<button type="button" class="subagent-card-menu-item subagent-card-stop" role="menuitem" data-agent-id="' + escapeHtml(vm.id) + '">停止</button>' : '';
    var outputBtn = vm.outputFile ? '<button type="button" class="subagent-card-menu-item subagent-card-output" role="menuitem" data-agent-id="' + escapeHtml(vm.id) + '">查看输出</button>' : '';
    var html = '<div class="process-aggregate subagent-grid-card" data-agent-id="' + escapeHtml(vm.id) + '"';
    if (vm.executorModel) html += ' data-executor-model="' + escapeHtml(String(vm.executorModel)) + '"';
    if (vm.outputFile) html += ' data-output-file="1"';
    if (vm.taskStatus) html += ' data-task-status="' + escapeHtml(String(vm.taskStatus)) + '"';
    if (vm.hasFinalKnown) html += ' data-has-final="' + (vm.hasFinal ? '1' : '0') + '"';
    html += ' data-subagent-running="' + (vm.running ? '1' : '0') + '"';
    html += ' data-description="' + escapeHtml(String(vm.name || '')) + '"';
    html += '>';
    html += '<div class="subagent-card-head">';
    html += '<div class="subagent-card-head-line">';
    html += '<span class="process-aggregate-title-wrap">';
    html += '<div class="subagent-card-title-row">';
    html += '<span class="subagent-status"><span class="subagent-status-dot ' + vm.status.dotCls + '" data-ui-tip="' + escapeHtml(vm.status.label) + '"></span></span>';
    html += '<span class="subagent-card-name">' + escapeHtml(vm.name) + '</span>';
    html += '<span class="subagent-card-type">' + escapeHtml(vm.typeLabel) + '</span>';
    html += '<span class="subagent-card-id">' + escapeHtml(vm.idShort) + '</span>';
    html += '</div>';
    html += '<span class="process-aggregate-stats" aria-live="polite"></span>';
    html += '</span>';
    html += '<span class="subagent-card-head-actions">';
    html += '<button type="button" class="subagent-card-expand" data-agent-id="' + escapeHtml(vm.id) + '" aria-label="放大显示" aria-pressed="false" data-ui-tip="在浮窗内全屏显示"><svg class="subagent-card-expand-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M3 6V3h3M10 3h3v3M13 10v3h-3M6 13H3v-3" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/></svg></button>';
    html += '<span class="subagent-card-menu">'
        + '<button type="button" class="subagent-card-menu-btn" aria-label="更多操作" aria-expanded="false" data-ui-tip="更多操作">' + subagentMoreDotsHtml() + '</button>'
        + '<span class="subagent-card-menu-pop" role="menu">'
        + outputBtn
        + stopBtn
        + '<button type="button" class="subagent-card-menu-item subagent-card-delete" role="menuitem" data-agent-id="' + escapeHtml(vm.id) + '">删除</button>'
        + '</span></span>';
    html += '</span>';
    html += '</div></div>';
    html += '<div class="subagent-card-body subagent-dialogue-body" data-agent-id="' + escapeHtml(vm.id) + '"'
        + (vm.resultPreview ? ' data-result-preview="' + escapeHtml(vm.resultPreview.slice(0, 400)) + '"' : '')
        + '></div>';
    html += '</div>';
    return html;
}

function buildSubagentGridHtml(flat) {
    var sorted = sortSubagentsByUpdated(flat);
    if (!sorted.length) return '<div class="subagent-grid-empty">无 Subagent</div>';
    return sorted.map(renderSubagentCardHtml).join('');
}

function ensureSubagentActionMenu(actions, id) {
    if (!actions) return null;
    var menu = actions.querySelector('.subagent-card-menu');
    if (menu) return menu;
    menu = document.createElement('span');
    menu.className = 'subagent-card-menu';
    menu.innerHTML = '<button type="button" class="subagent-card-menu-btn" aria-label="更多操作" aria-expanded="false" data-ui-tip="更多操作">'
        + subagentMoreDotsHtml() + '</button>'
        + '<span class="subagent-card-menu-pop" role="menu"></span>';
    actions.appendChild(menu);
    return menu;
}

function ensureSubagentMenuButton(menu, cls, label, agentId) {
    if (!menu) return null;
    var pop = menu.querySelector('.subagent-card-menu-pop');
    if (!pop) return null;
    var btn = pop.querySelector('.' + cls);
    if (btn) {
        btn.setAttribute('data-agent-id', agentId);
        return btn;
    }
    btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'subagent-card-menu-item ' + cls;
    btn.setAttribute('data-agent-id', agentId);
    btn.setAttribute('role', 'menuitem');
    btn.textContent = label;
    pop.appendChild(btn);
    return btn;
}

function applySubagentNodeMetaToCard(card, n) {
    if (!card || !n) return;
    var id = String(n.id || '');
    var running = !!n.running && !n.virtual_task;
    card.dataset.subagentRunning = running ? '1' : '0';
    card.dataset.description = String(n.description || id.slice(0, 8) || '');
    if (n.result_preview) card.dataset.resultPreview = String(n.result_preview);
    if (Object.prototype.hasOwnProperty.call(n, 'has_final')) card.dataset.hasFinal = n.has_final ? '1' : '0';
    if (n.session_metrics) applySubagentSessionMetricsToCard(card, n.session_metrics);
    var st = subagentStatusFromNode(n);
    var dot = card.querySelector('.subagent-status-dot');
    if (dot) {
        dot.className = 'subagent-status-dot ' + st.dotCls;
        dot.setAttribute('data-ui-tip', st.label);
    }
    var actions = card.querySelector('.subagent-card-head-actions');
    if (actions) {
        var menu = ensureSubagentActionMenu(actions, id);
        var stopExisting = actions.querySelector('.subagent-card-stop');
        if (running && !stopExisting) {
            ensureSubagentMenuButton(menu, 'subagent-card-stop', '停止', id);
        } else if (!running && stopExisting) {
            stopExisting.remove();
        }
        var outputExisting = actions.querySelector('.subagent-card-output');
        var hasOutput = !!n.output_file;
        if (hasOutput) {
            card.dataset.outputFile = '1';
            if (!outputExisting) {
                ensureSubagentMenuButton(menu, 'subagent-card-output', '查看输出', id);
            }
        } else {
            delete card.dataset.outputFile;
            if (outputExisting) outputExisting.remove();
            var panel = card.querySelector('.subagent-output-panel');
            if (panel) panel.remove();
        }
        ensureSubagentMenuButton(menu, 'subagent-card-delete', '删除', id);
    }
    if (n.task_status || n.status) card.dataset.taskStatus = String(n.task_status || n.status);
    if (n.executor_model) {
        card.dataset.executorModel = String(n.executor_model);
        if (!card.dataset.procCacheModel) card.dataset.procCacheModel = String(n.executor_model);
    }
    if (running && !card.dataset.procStartedAt) card.dataset.procStartedAt = String(procNow());
    if (!running) {
        card.dataset.procEndedAt = String(procNow());
        if (id) void refreshSubagentContextForCard(card, id, true);
        if (!card.classList.contains('is-expanded')) {
            updateSubagentCardSummaryOnly(card, n.result_preview);
        }
    }
    refreshSubagentCardStats(card);
}

function appendSubagentGridCardFromNode(grid, n) {
    if (!grid || !n) return null;
    var html = buildSubagentGridHtml([n]);
    if (html.indexOf('subagent-grid-empty') >= 0) return null;
    var tmp = document.createElement('div');
    tmp.innerHTML = html;
    var card = tmp.firstElementChild;
    if (!card) return null;
    grid.appendChild(card);
    if (n.result_preview) card.dataset.resultPreview = String(n.result_preview);
    return card;
}

function syncSubagentGridFromFlat(flat, sessionId) {
    var grid = document.getElementById('subagent-grid');
    if (!grid) return;
    if (grid.dataset.sessionId && grid.dataset.sessionId !== sessionId) {
        grid.innerHTML = '';
        disconnectSubagentCardViewportObserver();
    }
    grid.dataset.sessionId = sessionId;
    var sorted = sortSubagentsByUpdated(flat);
    var existingIds = new Set();
    sorted.forEach(function (n) {
        var id = String(n.id || '');
        if (!id) return;
        existingIds.add(id);
        var card = grid.querySelector('.subagent-grid-card[data-agent-id="' + id + '"]');
        if (!card) {
            card = appendSubagentGridCardFromNode(grid, n);
            if (card && subagentPanelOpen) observeSubagentCardViewport(card);
        } else {
            applySubagentNodeMetaToCard(card, n);
        }
    });
    grid.querySelectorAll('.subagent-grid-card').forEach(function (card) {
        var id = card.getAttribute('data-agent-id');
        if (id && !existingIds.has(id)) {
            subagentStore.deleteEventCount(sessionId, id);
            delete subagentCardLoadQueued[id];
            card.remove();
        }
    });
    bindSubagentGridActions(grid, sessionId);
    if (shouldLoadSubagentCardBodies()) {
        loadVisibleSubagentCardBodies(grid, sessionId);
    }
}

function refreshSubagentToggleFromGrid(flat) {
    var toggleBtn = document.getElementById('subagent-toggle-btn');
    var toggleBadge = document.getElementById('subagent-toggle-badge');
    if (!toggleBtn) return;
    var list = flat || [];
    var runningN = list.filter(function (n) { return n.running; }).length;
    if (!list.length) {
        toggleBtn.classList.add('hidden');
        return;
    }
    toggleBtn.classList.remove('hidden');
    if (toggleBadge) toggleBadge.textContent = String(list.length) + (runningN ? (' · ' + runningN) : '');
    toggleBtn.classList.toggle('is-running', runningN > 0);
}

function createSubagentMiniMessage(role, content, eventIndex) {
    var wrap = document.createElement('div');
    wrap.className = 'msg-wrap msg-wrap--' + (role === 'user' ? 'user' : 'assistant');
    if (role === 'assistant') wrap.classList.add('msg-wrap--answer-frame');
    if (eventIndex != null) wrap.setAttribute('data-event-index', String(eventIndex));
    var div = document.createElement('div');
    div.className = 'message ' + (role === 'user' ? 'user' : 'assistant');
    var rawStr = content == null ? '' : String(content);
    if (role === 'user') {
        var lineCount = rawStr.split('\n').length;
        if (lineCount > 10) {
            wrap.classList.add('has-turn-process');
            div.classList.add('is-collapsible');
            var sum = document.createElement('div');
            sum.className = 'user-msg-summary';
            sum.textContent = rawStr.split('\n').slice(0, 10).join('\n') + '\n...';
            var ful = document.createElement('div');
            ful.className = 'user-msg-full';
            ful.textContent = rawStr;
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
        }
    }
    else {
        div.innerHTML = renderMarkdown(rawStr);
        enhanceAssistantMessageContent(div);
    }
    wrap.appendChild(div);
    return wrap;
}

function openSubagentTurn(ctx, userContent, eventIndex) {
    if (!ctx || !ctx._subagentBody) return null;
    var userRaw = userContent == null ? '' : String(userContent);
    if (userRaw.trim() && ctx.currentTurn && !ctx.currentTurn.querySelector('.msg-wrap--user')) {
        var userWrap0 = createSubagentMiniMessage('user', userRaw, eventIndex);
        ctx.currentTurn.insertBefore(userWrap0, ctx.currentTurn.firstChild);
        bindSubagentTurnUserToggle(ctx.currentTurn, userWrap0);
        markSubagentTurnHasProcess(ctx.currentTurn);
        if (typeof eventIndex === 'number') ctx.lastUserEventIndex = eventIndex;
        return ctx.currentTurn;
    }
    sealSubagentTurn(ctx);
    var turn = document.createElement('div');
    turn.className = 'subagent-turn';
    var userWrap = userRaw.trim() ? createSubagentMiniMessage('user', userRaw, eventIndex) : null;
    var processEl = document.createElement('div');
    processEl.className = 'subagent-turn-process';
    var finalSlot = document.createElement('div');
    finalSlot.className = 'subagent-turn-final-slot';
    if (userWrap) turn.appendChild(userWrap);
    turn.appendChild(processEl);
    turn.appendChild(finalSlot);
    ctx._subagentBody.appendChild(turn);
    ctx.currentTurn = turn;
    ctx._subagentTurnProcess = processEl;
    ctx._subagentTurnFinalSlot = finalSlot;
    if (userWrap) bindSubagentTurnUserToggle(turn, userWrap);
    return turn;
}

function ensureSubagentTurnForProcess(ctx, eventIndex) {
    if (ctx && ctx._subagentTurnProcess && ctx.currentTurn) return ctx.currentTurn;
    return openSubagentTurn(ctx, '', eventIndex);
}

function appendSubagentFinalToTurn(ctx, content, eventIndex) {
    if (!ctx) return;
    if (!ctx.currentTurn) openSubagentTurn(ctx, '', eventIndex);
    var slot = ctx._subagentTurnFinalSlot;
    if (!slot && ctx.currentTurn) slot = ctx.currentTurn.querySelector('.subagent-turn-final-slot');
    if (!slot) return;
    var existing = slot.querySelector('.msg-wrap--assistant');
    var txt = content == null ? '' : String(content);
    if (existing) {
        var msgEl = existing.querySelector('.message.assistant');
        if (msgEl) {
            msgEl.innerHTML = renderMarkdown(txt);
            enhanceAssistantMessageContent(msgEl);
        }
        return;
    }
    slot.appendChild(createSubagentMiniMessage('assistant', txt, eventIndex));
    markSubagentTurnHasProcess(ctx.currentTurn);
}

function renderSubagentProcessEvents(bodyEl, hostEl, events, agentId, eventIndexBase) {
    if (!bodyEl) return Promise.resolve();
    var card = hostEl || (bodyEl.closest ? bodyEl.closest('.subagent-grid-card, .subagent-block') : null);
    if (card) {
        delete card.dataset.procDurationMs;
        delete card.dataset.procReactLoops;
        delete card.dataset.procToolCalls;
        delete card.dataset.procToolFails;
        delete card.dataset.procLiveToolCalls;
        delete card.dataset.procLiveToolFails;
    }
    bodyEl.innerHTML = '';
    delete bodyEl.dataset.cacheClean;
    delete bodyEl.dataset.finalOnly;
    bodyEl.classList.remove('is-final-only');
    bodyEl.classList.add('subagent-dialogue-body');
    if (!events || !events.length) {
        bodyEl.innerHTML = '<div class="subagent-detail-empty">(暂无事件)</div>';
        return Promise.resolve();
    }
    var ctx = getSubagentCardStreamCtx(bodyEl, hostEl, agentId);
    resetSubagentTurnStreamState(ctx);
    var idx = 0;
    var renderToken = String(Date.now()) + ':' + Math.random();
    bodyEl.dataset.renderToken = renderToken;
    bodyEl.dataset.rendering = '1';
    return new Promise(function (resolve) {
    function finish() {
        if (bodyEl.dataset.renderToken !== renderToken) {
            resolve();
            return;
        }
        finalizeLlmStreamChunks(ctx);
        finalizeProgressStreamChunks(ctx);
        rebindSubagentCardBody(bodyEl, hostEl, agentId);
        setSubagentCardEventCount(agentId, (events || []).length);
        delete bodyEl.dataset.streamReady;
        delete bodyEl.dataset.rendering;
        refreshSubagentProcessChunksLightly(bodyEl);
        if (card && (events || []).some(function (ev) { return ev && ev.type === 'final'; })) {
            markSubagentCardCompleted(card, true);
        }
        if (currentSessionId) {
            rememberSubagentBodyCache(currentSessionId, agentId, bodyEl.innerHTML);
            bodyEl.dataset.cacheClean = '1';
        }
        resolve();
    }
    function step() {
        if (!bodyEl.isConnected || bodyEl.dataset.renderToken !== renderToken) {
            resolve();
            return;
        }
        var end = Math.min(idx + SUBAGENT_DETAIL_RENDER_BATCH, events.length);
        for (; idx < end; idx += 1) {
            var ev = events[idx];
            if (ev && typeof ev === 'object') dispatchSubagentCardEvent(ctx, hostEl, ev, (eventIndexBase || 0) + idx, agentId);
        }
        if (idx < events.length) {
            scheduleSubagentDetailWork(step);
        } else {
            finish();
        }
    }
    step();
    });
}

function renderSubagentLatestFinalOnly(bodyEl, hostEl, events, agentId) {
    if (!bodyEl) return Promise.resolve();
    bodyEl.innerHTML = '';
    delete bodyEl.dataset.cacheClean;
    delete bodyEl.dataset.renderToken;
    delete bodyEl.dataset.rendering;
    delete bodyEl.dataset.streamReady;
    bodyEl.classList.add('subagent-dialogue-body', 'is-final-only');
    var finalIdx = -1;
    for (var i = (events || []).length - 1; i >= 0; i -= 1) {
        if (events[i] && events[i].type === 'final') {
            finalIdx = i;
            break;
        }
    }
    var ctx = getSubagentCardStreamCtx(bodyEl, hostEl, agentId);
    resetSubagentTurnStreamState(ctx);
    var lastUser = -1;
    if (finalIdx >= 0) {
        openSubagentTurn(ctx, '', finalIdx);
        appendSubagentFinalToTurn(ctx, events[finalIdx].content || '', finalIdx);
    } else {
        for (var u = (events || []).length - 1; u >= 0; u -= 1) {
            if (events[u] && events[u].type === 'user') { lastUser = u; break; }
        }
        if (lastUser >= 0) openSubagentTurn(ctx, events[lastUser].content || '', lastUser);
        else bodyEl.innerHTML = '<div class="subagent-detail-empty">(暂无 final 结果)</div>';
    }
    bodyEl.dataset.loaded = '1';
    bodyEl.dataset.finalOnly = '1';
    bodyEl.dataset.subagentSliceStart = String(finalIdx >= 0 ? finalIdx : Math.max(0, lastUser));
    delete bodyEl.dataset.historyComplete;
    bodyEl._subagentEvents = events || [];
    rebindSubagentCardBody(bodyEl, hostEl, agentId);
    if (hostEl && finalIdx >= 0) markSubagentCardCompleted(hostEl, true);
    requestAnimationFrame(function () {
        if (bodyEl.isConnected) bodyEl.scrollTop = 0;
    });
    return Promise.resolve();
}
