const aggregateChanges = new Map();
const aggregateRecency = [];
let activeAggregate = null;
let request = null;
let drawer = null;
let bar = null;
let sheet = null;
let resizeObserver = null;
let processObserver = null;
let sessionObserver = null;
let scanTimer = null;
let renderFrame = null;
let mountedSessionId = '';
const aggregateOwners = new WeakMap();

function zh() {
    return String(document.documentElement.lang || 'zh').toLowerCase().startsWith('zh');
}
function t(cn, en) { return zh() ? cn : en; }
function activeSessionId() {
    const row = document.querySelector('#sessions-list .session-item.active[data-session-id]');
    if (row) return String(row.dataset.sessionId || '');
    if (typeof globalThis.currentSessionId === 'string') return globalThis.currentSessionId;
    return '';
}
function aggregateSessionId(aggregate, detail) {
    if (!aggregate || !aggregate.closest) return '';
    const explicit = String((detail && detail.rootSessionId) || '');
    if (explicit) return explicit;
    const grid = aggregate.closest('#subagent-grid[data-session-id]');
    if (grid && grid.dataset.sessionId) return String(grid.dataset.sessionId);
    const stream = aggregate.closest('.chat-stream');
    if (stream) {
        const streamSession = String(stream.dataset.sessionId || stream.dataset.cacheSessionId || '');
        if (streamSession) return streamSession;
        if (stream.id === 'chat-stream') return mountedSessionId || activeSessionId();
    }
    return '';
}
function aggregateIsCurrent(aggregate) {
    if (!aggregate || !aggregate.isConnected || !mountedSessionId) return false;
    const owner = aggregateOwners.get(aggregate) || aggregateSessionId(aggregate);
    if (owner && owner !== mountedSessionId) return false;
    if (aggregate.classList.contains('subagent-grid-card')) {
        const grid = aggregate.closest('#subagent-grid');
        return Boolean(grid && String(grid.dataset.sessionId || '') === mountedSessionId);
    }
    const stream = aggregate.closest('.chat-stream');
    return Boolean(stream && stream.id === 'chat-stream');
}
function changesOf(aggregate) {
    if (!aggregateChanges.has(aggregate)) aggregateChanges.set(aggregate, new Map());
    return aggregateChanges.get(aggregate);
}
function activeRows(aggregate) {
    return Array.from(changesOf(aggregate).values()).filter(function (row) {
        return row && row._rootSessionId === mountedSessionId
            && row.effective !== false && row.reverted !== true;
    });
}
function isExpanded(aggregate) {
    if (!aggregate) return false;
    return aggregate.classList.contains('subagent-grid-card')
        ? aggregate.classList.contains('is-expanded')
        : !aggregate.classList.contains('is-collapsed');
}
function remember(aggregate) {
    const index = aggregateRecency.indexOf(aggregate);
    if (index >= 0) aggregateRecency.splice(index, 1);
    aggregateRecency.push(aggregate);
}
function fallbackAggregate() {
    for (let index = aggregateRecency.length - 1; index >= 0; index -= 1) {
        const candidate = aggregateRecency[index];
        if (candidate && aggregateIsCurrent(candidate) && isExpanded(candidate)
            && activeRows(candidate).length) return candidate;
    }
    return null;
}
function stats(rows) {
    let added = 0; let removed = 0; let omitted = 0;
    rows.forEach(function (row) {
        if (Number.isFinite(Number(row.added)) && Number.isFinite(Number(row.removed))) {
            added += Number(row.added); removed += Number(row.removed);
        } else if (row.diff_omitted_reason !== 'directory') omitted += 1;
    });
    return { added, removed, omitted };
}
function appendColoredStats(container, value, includeOmitted) {
    if (!container) return;
    const add = document.createElement('span'); add.className = 'change-review-stat-added';
    add.textContent = `+${value.added}`;
    const remove = document.createElement('span'); remove.className = 'change-review-stat-removed';
    remove.textContent = `−${value.removed}`;
    container.append(add, document.createTextNode(' '), remove);
    if (includeOmitted && value.omitted) {
        container.append(document.createTextNode(` · ${value.omitted} ${t('个大文件', 'large')}`));
    }
}
function setSummary(container, rows) {
    if (!container) return;
    const value = stats(rows);
    container.replaceChildren();
    container.append(document.createTextNode(`${rows.length} ${t('个文件', 'files')} · `));
    appendColoredStats(container, value, true);
}
function updateBadge(aggregate) {
    if (!aggregate || !aggregate.querySelector) return;
    const rows = activeRows(aggregate);
    let badge = aggregate.querySelector('.change-review-process-badge');
    if (!rows.length) {
        if (badge) badge.remove();
        return;
    }
    if (!badge) {
        badge = document.createElement('span');
        badge.className = 'change-review-process-badge';
        const title = aggregate.querySelector('.process-aggregate-title');
        const subagentTitle = aggregate.querySelector('.subagent-card-title-row');
        const wrap = aggregate.querySelector('.process-aggregate-title-wrap');
        if (title) title.appendChild(badge);
        else if (subagentTitle) subagentTitle.appendChild(badge);
        else if (wrap) wrap.insertBefore(badge, wrap.querySelector('.process-aggregate-stats'));
    }
    const value = stats(rows);
    badge.replaceChildren(); appendColoredStats(badge, value, true);
}
function isRunning() {
    const stream = document.getElementById('chat-stream');
    const grid = document.getElementById('subagent-grid');
    return Boolean(
        (stream && stream.querySelector('.process-aggregate.is-running'))
        || (grid && String(grid.dataset.sessionId || '') === mountedSessionId
            && grid.querySelector('.subagent-grid-card[data-subagent-running="1"]'))
    );
}
function button(label, className) {
    const el = document.createElement('button');
    el.type = 'button'; el.className = className || ''; el.textContent = label;
    return el;
}
function formatBytes(value) {
    const number = Number(value) || 0;
    if (number < 1024) return `${number} B`;
    if (number < 1048576) return `${(number / 1024).toFixed(1)} KiB`;
    return `${(number / 1048576).toFixed(1)} MiB`;
}
function omittedText(row) {
    const reason = String(row.diff_omitted_reason || '');
    const why = reason === 'directory' ? t('目录结构', 'Directory structure')
        : reason === 'binary' ? t('二进制文件', 'Binary file')
        : reason === 'too_many_lines' ? t('超过 20,000 行', 'More than 20,000 lines')
            : t('超过 1 MiB', 'Larger than 1 MiB');
    const before = row.before || {}; const after = row.after || {};
    return `${why} · ${formatBytes(before.bytes)} / ${before.lines || 0} ${t('行', 'lines')} → `
        + `${formatBytes(after.bytes)} / ${after.lines || 0} ${t('行', 'lines')}`;
}
function renderDiff(container, row) {
    container.replaceChildren();
    if (!row.diff) {
        const omitted = document.createElement('div');
        omitted.className = 'change-review-omitted'; omitted.textContent = omittedText(row);
        container.appendChild(omitted); return;
    }
    const pre = document.createElement('pre'); pre.className = 'change-review-diff';
    String(row.diff).split('\n').forEach(function (line) {
        const span = document.createElement('span');
        if (line.startsWith('+++') || line.startsWith('---')) span.className = 'diff-file';
        else if (line.startsWith('@@')) span.className = 'diff-hunk';
        else if (line.startsWith('+')) span.className = 'diff-add';
        else if (line.startsWith('-')) span.className = 'diff-remove';
        span.textContent = line + '\n'; pre.appendChild(span);
    });
    container.appendChild(pre);
}
function markReverted(snapshotIds) {
    const ids = new Set(snapshotIds || []);
    aggregateChanges.forEach(function (rows, aggregate) {
        rows.forEach(function (row) {
            if (ids.has(String(row.snapshot_id || ''))) {
                row.reverted = true; row.effective = false;
            }
        });
        updateBadge(aggregate);
    });
    if (!activeAggregate || !activeRows(activeAggregate).length) activeAggregate = fallbackAggregate();
    render();
}
async function undo(rows) {
    if (!rows.length || !request) return;
    const sessionId = String(rows[0]._sessionId || '');
    const response = await request(`/sessions/${encodeURIComponent(sessionId)}/change-reviews/undo`, {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({
            snapshot_ids: rows.map(function (row) { return row.snapshot_id; }),
            operation_id: globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function'
                ? globalThis.crypto.randomUUID() : `undo-${Date.now()}-${Math.random()}`,
        }),
    });
    const payload = await response.json().catch(function () { return {}; });
    if (!response.ok || payload.ok !== true) {
        const paths = Array.isArray(payload.paths) && payload.paths.length ? `\n${payload.paths.join('\n')}` : '';
        throw new Error((payload.code === 'file_changed'
            ? t('文件已再次修改，撤销已中止', 'The file was modified again; undo was cancelled')
            : String(payload.error || `HTTP ${response.status}`)) + paths);
    }
    markReverted(payload.snapshot_ids);
}
function setStatus(text, error) {
    [drawer, sheet].forEach(function (host) {
        const target = host && host.querySelector('.change-review-status');
        if (target) { target.textContent = text || ''; target.classList.toggle('is-error', Boolean(error)); }
    });
}
function renderFile(row, options) {
    options = options || {};
    const allowUndo = options.allowUndo !== false;
    const item = document.createElement('article'); item.className = 'change-review-file';
    item.dataset.snapshotId = String(row.snapshot_id || '');
    const head = document.createElement('div'); head.className = 'change-review-file-head';
    const toggle = button('', 'change-review-file-toggle');
    toggle.setAttribute('aria-expanded', 'false');
    const path = document.createElement('span'); path.className = 'change-review-path'; path.textContent = row.path || '';
    const count = document.createElement('span'); count.className = 'change-review-count';
    if (row.added == null) count.textContent = t('统计', 'stats');
    else appendColoredStats(count, { added: Number(row.added) || 0, removed: Number(row.removed) || 0 }, false);
    toggle.append(path, count);
    let action = null; let confirm = null; let yes = null; let no = null;
    if (allowUndo) {
        action = button(t('撤销', 'Undo'), 'change-review-undo');
        action.disabled = isRunning();
        if (action.disabled) action.title = t('任务和子任务全部结束后才可撤销', 'Undo is available after the task tree stops');
        confirm = document.createElement('span'); confirm.className = 'change-review-inline-confirm'; confirm.hidden = true;
        yes = button(t('确认', 'Confirm'), 'change-review-confirm');
        no = button(t('取消', 'Cancel'), 'change-review-cancel'); confirm.append(yes, no);
        head.append(toggle, action, confirm);
    } else {
        item.classList.add('change-review-file--link');
        toggle.setAttribute('aria-label', `${t('查看改动', 'View changes')}: ${row.path || ''}`);
        head.append(toggle);
    }
    const body = document.createElement('div'); body.className = 'change-review-file-body'; body.hidden = true;
    body.dataset.rendered = '0';
    const ensureDiffRendered = function () {
        if (body.dataset.rendered === '1') return;
        renderDiff(body, row);
        body.dataset.rendered = '1';
    };
    // The wide drawer is only a file picker. Building thousands of diff-line
    // nodes there made history hydration and every observer-driven refresh
    // needlessly expensive even though the user had not opened the review.
    item.appendChild(head);
    if (allowUndo) item.appendChild(body);
    toggle.addEventListener('click', function () {
        if (!allowUndo && typeof options.onOpen === 'function') { options.onOpen(row); return; }
        const opening = body.hidden;
        if (opening) ensureDiffRendered();
        body.hidden = !opening; toggle.setAttribute('aria-expanded', opening ? 'true' : 'false');
    });
    if (allowUndo) {
        action.addEventListener('click', function () { action.hidden = true; confirm.hidden = false; yes.focus(); });
        no.addEventListener('click', function () { confirm.hidden = true; action.hidden = false; action.focus(); });
        yes.addEventListener('click', async function () {
            yes.disabled = true; no.disabled = true; setStatus(t('正在撤销…', 'Undoing…'));
            try { await undo([row]); setStatus(t('已撤销', 'Undone')); }
            catch (error) { setStatus(String(error.message || error), true); yes.disabled = false; no.disabled = false; }
        });
    }
    return item;
}
function renderReviewHost(host, rows, includeClose, options) {
    options = options || {};
    const list = host.querySelector('.change-review-list'); list.replaceChildren();
    rows.forEach(function (row) {
        list.appendChild(renderFile(row, {
            allowUndo: options.allowUndo !== false,
            onOpen: options.onOpen,
        }));
    });
    const summary = host.querySelector('.change-review-summary');
    setSummary(summary, rows);
    const all = host.querySelector('.change-review-undo-all');
    all.disabled = !rows.length || isRunning();
    all.title = all.disabled && rows.length ? t('任务和子任务全部结束后才可撤销', 'Wait until all tasks stop') : '';
    all.onclick = async function () {
        const confirmed = typeof globalThis.openMyAgentUiModal === 'function'
            ? await globalThis.openMyAgentUiModal({
                title: t('撤销全部改动？', 'Undo all changes?'),
                message: t('将整批恢复到工具修改前的文件内容。若任一文件已被再次修改，整批都会中止。',
                    'The batch will restore pre-tool contents. If any file changed again, the whole batch is cancelled.'),
                confirmText: t('全部撤销', 'Undo all'), cancelText: t('取消', 'Cancel'), danger: true,
            }) : globalThis.confirm(t('撤销全部改动？', 'Undo all changes?'));
        if (!confirmed) return;
        setStatus(t('正在撤销…', 'Undoing…'));
        try { await undo(rows); setStatus(t('已全部撤销', 'All changes undone')); }
        catch (error) { setStatus(String(error.message || error), true); }
    };
    const view = host.querySelector('.change-review-view');
    if (view) {
        view.hidden = !options.showView;
        view.onclick = function () { openSheet(); };
    }
    if (includeClose) {
        const close = host.querySelector('.change-review-close');
        close.onclick = closeSheet;
    }
}
function shell(className, close) {
    const host = document.createElement(close ? 'div' : 'aside'); host.className = className;
    host.innerHTML = `<div class="change-review-card"><header class="change-review-head">`
        + `<div><strong>${t('改动审查', 'Change review')}</strong><div class="change-review-summary"></div></div>`
        + (close ? `<button type="button" class="change-review-close" aria-label="${t('关闭', 'Close')}">×</button>` : '')
        + `</header><div class="change-review-list"></div><div class="change-review-status" role="status" aria-live="polite"></div>`
        + `<footer><button type="button" class="change-review-undo-all">${t('全部撤销', 'Undo all')}</button>`
        + `<button type="button" class="change-review-view" hidden>${t('查看', 'View')}</button></footer></div>`;
    return host;
}
function openSheet(selectedRow) {
    if (!sheet) return; sheet.hidden = false; document.body.classList.add('change-review-sheet-open');
    const rows = activeRows(activeAggregate);
    renderReviewHost(sheet, rows, true, { allowUndo: true, showView: false });
    const close = sheet.querySelector('.change-review-close'); if (close) close.focus();
    if (selectedRow) {
        const item = Array.from(sheet.querySelectorAll('.change-review-file')).find(function (node) {
            return node.dataset.snapshotId === String(selectedRow.snapshot_id || '');
        });
        if (item) {
            const toggle = item.querySelector('.change-review-file-toggle');
            const body = item.querySelector('.change-review-file-body');
            if (body) {
                if (body.dataset.rendered !== '1') {
                    renderDiff(body, selectedRow);
                    body.dataset.rendered = '1';
                }
                body.hidden = false;
            }
            if (toggle) { toggle.setAttribute('aria-expanded', 'true'); toggle.focus(); }
            item.scrollIntoView({ block: 'nearest' });
        }
    }
}
function closeSheet() {
    if (!sheet) return; sheet.hidden = true; document.body.classList.remove('change-review-sheet-open');
    const view = bar && !bar.hidden && bar.querySelector('.change-review-view'); if (view) view.focus();
}
function hasRoom() {
    const stage = document.querySelector('.chat-stage'); const panel = document.querySelector('.panel-inner');
    if (!stage || !panel) return false;
    const spare = Math.max(0, (stage.getBoundingClientRect().width - panel.getBoundingClientRect().width) / 2);
    const goal = document.getElementById('chat-todo-plan');
    const goalWidth = goal && goal.classList.contains('is-open') ? goal.getBoundingClientRect().width + 12 : 0;
    return spare >= 224 + goalWidth;
}
function updatePlacement(visible) {
    const wide = visible && hasRoom();
    drawer.hidden = !wide; bar.hidden = !visible || wide;
    if (visible && !wide) {
        const rows = activeRows(activeAggregate);
        setSummary(bar.querySelector('.change-review-bar-summary'), rows);
    }
    if (!visible) closeSheet();
}
function render() {
    const rows = activeAggregate ? activeRows(activeAggregate) : [];
    const visible = Boolean(activeAggregate && aggregateIsCurrent(activeAggregate) && rows.length
        && isExpanded(activeAggregate));
    if (visible) renderReviewHost(drawer, rows, false, {
        allowUndo: false,
        showView: true,
        onOpen: openSheet,
    });
    updatePlacement(visible);
    if (sheet && !sheet.hidden) renderReviewHost(sheet, rows, true, { allowUndo: true, showView: false });
}
function scheduleRender() {
    if (renderFrame !== null) return;
    const enqueue = typeof globalThis.requestAnimationFrame === 'function'
        ? globalThis.requestAnimationFrame.bind(globalThis)
        : function (callback) { return globalThis.setTimeout(callback, 0); };
    renderFrame = enqueue(function () {
        renderFrame = null;
        render();
    });
}
function scheduleScanExisting() {
    if (scanTimer !== null) return;
    scanTimer = globalThis.setTimeout(function () {
        scanTimer = null;
        scanExisting();
    }, 0);
}
function applyTool(detail, options) {
    options = options || {};
    const event = detail && detail.event; const aggregate = detail && detail.aggregate;
    const incoming = event && event.ui && Array.isArray(event.ui.changes) ? event.ui.changes : [];
    if (!aggregate || !incoming.length) return false;
    const ownerSessionId = aggregateSessionId(aggregate, detail);
    if (!ownerSessionId || ownerSessionId !== mountedSessionId || !aggregateIsCurrent(aggregate)) return false;
    aggregateOwners.set(aggregate, ownerSessionId);
    aggregate.dataset.changeReviewSessionId = ownerSessionId;
    const rows = changesOf(aggregate);
    incoming.forEach(function (raw) {
        if (!raw || !raw.snapshot_id || !raw.path) return;
        const old = rows.get(String(raw.path).toLowerCase());
        if (old && Number(old.revision || 0) > Number(raw.revision || 0)) return;
        rows.set(String(raw.path).toLowerCase(), Object.assign({}, raw, {
            _sessionId: String(detail.sessionId || ''),
            _rootSessionId: ownerSessionId,
        }));
    });
    remember(aggregate); updateBadge(aggregate);
    if (isExpanded(aggregate)) activeAggregate = aggregate;
    if (!options.deferRender) scheduleRender();
    return true;
}
function onToggle(detail) {
    const aggregate = detail && detail.aggregate;
    if (!aggregate || !aggregateChanges.has(aggregate) || !aggregateIsCurrent(aggregate)) return;
    if (detail.expanded && activeRows(aggregate).length) { remember(aggregate); activeAggregate = aggregate; }
    else if (activeAggregate === aggregate) activeAggregate = fallbackAggregate();
    render();
}
function onUiEvent(detail) {
    const event = detail && detail.event;
    const ownerSessionId = String((detail && detail.rootSessionId) || (detail && detail.sessionId) || '');
    if (ownerSessionId && ownerSessionId !== mountedSessionId) return;
    if (event && event.type === 'file_changes_reverted') markReverted(event.snapshot_ids || []);
}
function scanExisting() {
    let found = false;
    const roots = [];
    const stream = document.getElementById('chat-stream');
    if (stream) roots.push(stream);
    const grid = document.getElementById('subagent-grid');
    if (grid && String(grid.dataset.sessionId || '') === mountedSessionId) roots.push(grid);
    roots.forEach(function (root) { root.querySelectorAll('.feed-item.feed--tool').forEach(function (row) {
        if (!row._toolCallEvent) return;
        const child = row.closest('.subagent-grid-card[data-agent-id]');
        const aggregate = row.closest('.process-aggregate');
        const applied = applyTool({ event: row._toolCallEvent, row, aggregate,
            sessionId: row._toolCallEvent.session_id || (child && child.dataset.agentId) || mountedSessionId,
            rootSessionId: aggregateSessionId(aggregate) || mountedSessionId },
        { deferRender: true });
        found = found || Boolean(applied);
    }); });
    if (found) scheduleRender();
}
function resetForSession(nextSessionId) {
    if (scanTimer !== null) {
        globalThis.clearTimeout(scanTimer);
        scanTimer = null;
    }
    if (renderFrame !== null) {
        if (typeof globalThis.cancelAnimationFrame === 'function') {
            globalThis.cancelAnimationFrame(renderFrame);
        } else globalThis.clearTimeout(renderFrame);
        renderFrame = null;
    }
    mountedSessionId = String(nextSessionId || '');
    activeAggregate = null;
    aggregateChanges.clear();
    aggregateRecency.splice(0);
    closeSheet();
    render();
}
function mount() {
    const stage = document.querySelector('.chat-stage'); const inner = document.querySelector('.panel-inner');
    if (!stage || !inner) return false;
    drawer = shell('change-review-drawer', false); drawer.hidden = true; stage.appendChild(drawer);
    bar = document.createElement('div'); bar.className = 'change-review-bar'; bar.hidden = true;
    bar.innerHTML = `<strong>${t('改动审查', 'Change review')}</strong>`
        + `<span class="change-review-bar-summary"></span><button type="button" class="change-review-view">${t('查看', 'View')}</button>`;
    inner.insertBefore(bar, inner.querySelector('.composer-row'));
    sheet = shell('change-review-sheet', true); sheet.hidden = true; sheet.setAttribute('role', 'dialog');
    sheet.setAttribute('aria-modal', 'true'); document.body.appendChild(sheet);
    bar.querySelector('.change-review-view').addEventListener('click', openSheet);
    document.addEventListener('keydown', function (event) { if (event.key === 'Escape' && sheet && !sheet.hidden) closeSheet(); });
    // Resizing only changes drawer-vs-bar placement; rebuilding the complete
    // file list on every geometry notification caused ResizeObserver feedback
    // and long main-thread stalls on large histories.
    resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(function () {
        const rows = activeAggregate ? activeRows(activeAggregate) : [];
        updatePlacement(Boolean(activeAggregate && aggregateIsCurrent(activeAggregate)
            && rows.length && isExpanded(activeAggregate)));
    }) : null;
    if (resizeObserver) { resizeObserver.observe(stage); resizeObserver.observe(inner); }
    processObserver = typeof MutationObserver === 'function' ? new MutationObserver(function (mutations) {
        let hasInsertedRows = false;
        let shouldRender = false;
        mutations.forEach(function (mutation) {
            if (mutation.type === 'childList' && mutation.addedNodes && mutation.addedNodes.length) {
                // The review drawer/sheet is also mounted under chat-stage.
                // Only rescan when a real tool row was inserted; otherwise a
                // review render would observe itself and spin indefinitely.
                hasInsertedRows = hasInsertedRows || Array.from(mutation.addedNodes).some(function (node) {
                    return node && node.nodeType === 1 && (
                        (node.matches && node.matches('.feed-item.feed--tool'))
                        || (node.querySelector && node.querySelector('.feed-item.feed--tool'))
                    );
                });
            }
            // Only the aggregate's own expanded/collapsed class affects which
            // review is active. Streaming text and child-node mutations inside
            // it must not rebuild the review UI.
            if (mutation.type !== 'attributes') return;
            const aggregate = mutation.target && mutation.target.matches
                && mutation.target.matches('.process-aggregate, .subagent-grid-card')
                ? mutation.target : null;
            if (!aggregate || !aggregateChanges.has(aggregate) || !aggregateIsCurrent(aggregate)) return;
            shouldRender = true;
            if (isExpanded(aggregate) && activeRows(aggregate).length) {
                remember(aggregate); activeAggregate = aggregate;
            } else if (activeAggregate === aggregate) activeAggregate = fallbackAggregate();
        });
        // Historical process bodies are rendered lazily after expansion. Re-read
        // their tool rows so persisted ui.changes become visible immediately.
        if (hasInsertedRows) scheduleScanExisting();
        if (shouldRender) scheduleRender();
    }) : null;
    if (processObserver) processObserver.observe(stage, {
        subtree: true, attributes: true, childList: true, attributeFilter: ['class'],
    });
    return true;
}

export async function installChatExtension(context) {
    request = context.request;
    if (!mount()) return;
    mountedSessionId = activeSessionId();
    const toolListener = function (event) { applyTool(event.detail || {}); };
    const toggleListener = function (event) { onToggle(event.detail || {}); };
    const uiListener = function (event) { onUiEvent(event.detail || {}); };
    const switchSessionView = function (next) {
        next = String(next || '');
        if (next === mountedSessionId) return;
        resetForSession(next);
        scheduleScanExisting();
    };
    const sessionListener = function (event) {
        const detail = event && event.detail ? event.detail : {};
        const active = activeSessionId();
        // Child-agent extension events must not switch the root conversation.
        // The session list's active marker is authoritative for the reader.
        if (detail.sessionId && active && String(detail.sessionId) !== active) return;
        switchSessionView(active || detail.sessionId || '');
    };
    sessionObserver = typeof MutationObserver === 'function' ? new MutationObserver(function () {
        switchSessionView(activeSessionId());
    }) : null;
    const sessions = document.querySelector('#sessions-list');
    if (sessionObserver && sessions) sessionObserver.observe(sessions, {
        subtree: true, attributes: true, childList: true, attributeFilter: ['class'],
    });
    document.addEventListener('myagent:tool-call-rendered', toolListener);
    document.addEventListener('myagent:process-aggregate-toggle', toggleListener);
    document.addEventListener('myagent:ui-event', uiListener);
    document.addEventListener('myagent:extension-state-changed', sessionListener);
    document.addEventListener('myagent:language-change', render);
    // Installing a chat extension is awaited by the page bootstrap. Defer the
    // historical scan so plugin discovery never blocks first paint.
    scheduleScanExisting();
    return function () {
        document.removeEventListener('myagent:tool-call-rendered', toolListener);
        document.removeEventListener('myagent:process-aggregate-toggle', toggleListener);
        document.removeEventListener('myagent:ui-event', uiListener);
        document.removeEventListener('myagent:extension-state-changed', sessionListener);
        if (resizeObserver) resizeObserver.disconnect();
        if (processObserver) processObserver.disconnect();
        if (sessionObserver) sessionObserver.disconnect();
        if (scanTimer !== null) {
            globalThis.clearTimeout(scanTimer);
            scanTimer = null;
        }
        if (renderFrame !== null) {
            if (typeof globalThis.cancelAnimationFrame === 'function') {
                globalThis.cancelAnimationFrame(renderFrame);
            } else globalThis.clearTimeout(renderFrame);
            renderFrame = null;
        }
        document.querySelectorAll('.change-review-process-badge').forEach(function (node) { node.remove(); });
        document.querySelectorAll('[data-change-review-session-id]').forEach(function (node) {
            delete node.dataset.changeReviewSessionId;
        });
        activeAggregate = null; aggregateChanges.clear(); aggregateRecency.splice(0);
        [drawer, bar, sheet].forEach(function (node) { if (node) node.remove(); });
        drawer = bar = sheet = null;
    };
}
