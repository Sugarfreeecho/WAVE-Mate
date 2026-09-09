newSessionBtn.addEventListener('click', async () => { await createNewSession(); });

function initSidebarSash() {
    const side = document.getElementById('sidebar');
    const sash = document.getElementById('sash');
    if (!side || !sash) return;
    const KEY = 'sidebar-width-px';
    function clampW(n) {
        const max = Math.min(480, Math.floor(window.innerWidth * 0.5));
        return Math.max(120, Math.min(max, n));
    }
    const saved = localStorage.getItem(KEY);
    if (saved) { const w = parseInt(saved, 10); if (!isNaN(w)) side.style.width = clampW(w) + 'px'; }
    let startX = 0, startW = 0;
    function onMouseMove(e) { side.style.width = clampW(startW + e.clientX - startX) + 'px'; }
    function onMouseUp() {
        sash.classList.remove('is-dragging');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
        localStorage.setItem(KEY, String(Math.round(side.getBoundingClientRect().width)));
    }
    sash.addEventListener('mousedown', function (e) {
        e.preventDefault();
        startX = e.clientX;
        startW = side.getBoundingClientRect().width;
        sash.classList.add('is-dragging');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
    });
}

async function init() {
    loadUnreadFromStorage();
    initSidebarSash();
    if (typeof startRuntimeStatusHeartbeat === 'function') startRuntimeStatusHeartbeat();
    showLoading();
    const sessionsLoaded = await loadSessions();
    const sessions = sessionStore.list();
    let lastSessionId = localStorage.getItem('lastSessionId');
    let targetSession = null;
    const restoreNewSessionDraft = lastSessionId === NEW_SESSION_DRAFT_KEY;
    if (!restoreNewSessionDraft && lastSessionId && sessions.some(s => s.id === lastSessionId)) targetSession = lastSessionId;
    else if (!restoreNewSessionDraft && !sessionsLoaded && lastSessionId) targetSession = lastSessionId;
    else if (!restoreNewSessionDraft && sessions.length > 0) targetSession = sessions[0].id;
    // Restore durable approvals/questions before switchSession decides whether
    // the global pending banner should be hidden for the selected session.
    if (targetSession && typeof refreshHumanInteractions === 'function') {
        await refreshHumanInteractions(targetSession, { render: false });
    }
    if (targetSession) await switchSession(targetSession);
    else await createNewSession();
    // Recovery is server-owned and session-wide. Trigger a scan after the
    // initial view is ready so interrupted background sessions resume without
    // temporarily switching them into the visible chat.
    void fetch('/sessions/recover', { method: 'POST' }).then(function () {
        if (typeof observeServerOwnedReactRecovery === 'function' && currentSessionId) {
            observeServerOwnedReactRecovery(currentSessionId);
        }
    }).catch(function (error) {
        console.warn('恢复后台会话失败:', error);
    });
    bindExistingLogs();
}
init();
function toggleTocPanel() {
    const toc = document.getElementById('chat-toc');
    if (!toc) return;
    const isOpening = !toc.classList.contains('is-open');
    panelManualOverlapToc = isOpening;
    panelAutoCollapsedToc = false;
    panelWasAutoCollapsed = panelAutoCollapsedTodo;
    toc.classList.toggle('is-open');
    syncEdgeTabArrows();
    schedulePanelEdgeTabsLayout();
}

function toggleTodoPlanPanel() {
    const root = document.getElementById('chat-todo-plan');
    if (!root) return;
    const isOpening = !root.classList.contains('is-open');
    panelUserCollapsedTodo = !isOpening;
    panelManualOverlapTodo = isOpening;
    panelAutoCollapsedTodo = false;
    panelWasAutoCollapsed = panelAutoCollapsedToc;
    root.classList.toggle('is-open');
    syncEdgeTabArrows();
    schedulePanelEdgeTabsLayout();
}

function syncEdgeTabArrows() {
    const toc = document.getElementById('chat-toc');
    const todo = document.getElementById('chat-todo-plan');
    const tocTab = document.getElementById('toc-edge-tab');
    const todoTab = document.getElementById('todo-edge-tab');
    if (tocTab && toc) {
        tocTab.textContent = toc.classList.contains('is-open') ? '▶' : '◀';
    }
    if (todoTab && todo) {
        todoTab.textContent = todo.classList.contains('is-open') ? '◀' : '▶';
    }
}

function updatePanelToggles() {
    const tocList = document.getElementById('chat-toc-list');
    const pluginPanels = document.getElementById('plugin-session-panels');
    const tocTab = document.getElementById('toc-edge-tab');
    const todoTab = document.getElementById('todo-edge-tab');
    if (tocTab) tocTab.classList.toggle('visible', !!(tocList && tocList.children.length));
    if (todoTab) {
        const hasTodoPanelContent = !!(
            pluginPanels && !pluginPanels.hidden && pluginPanels.children.length
        );
        todoTab.classList.toggle('visible', hasTodoPanelContent);
    }
    syncEdgeTabArrows();
    schedulePanelEdgeTabsLayout();
}

function notifyPanelContentChanged() {
    if (typeof updatePanelToggles !== 'function') return;
    updatePanelToggles();
    if (typeof runPanelAutoCollapseCheck === 'function') {
        requestAnimationFrame(function () {
            runPanelAutoCollapseCheck();
            schedulePanelEdgeTabsLayout();
        });
    }
}

/* 折叠三角挂在 stage 外层面，对齐面板边缘（收起后只剩按钮，不被 aside 裁切） */
var panelEdgeTabsObserver = null;
var panelEdgeTabsRaf = null;
function layoutPanelEdgeTabs() {
    var stage = document.querySelector('.chat-stage');
    var todo = document.getElementById('chat-todo-plan');
    var toc = document.getElementById('chat-toc');
    var todoTab = document.getElementById('todo-edge-tab');
    var tocTab = document.getElementById('toc-edge-tab');
    if (!stage || !todoTab || !tocTab) return;
    var sr = stage.getBoundingClientRect();
    todoTab.style.top = '50%';
    tocTab.style.top = '50%';
    /* Todo：仅用 left，与 CSS 一致（贴在面板右缘） */
    todoTab.style.right = 'auto';
    if (todo) {
        var tr = todo.getBoundingClientRect();
        todoTab.style.left = (tr.right - sr.left) + 'px';
    }
    /* TOC：仅用 right，勿写 left（否则与样式表里 right 并存导致错位 / hover 异常） */
    tocTab.style.left = 'auto';
    if (toc) {
        var cr = toc.getBoundingClientRect();
        tocTab.style.right = (sr.right - cr.left) + 'px';
    }
}

function schedulePanelEdgeTabsLayout() {
    if (panelEdgeTabsRaf != null) return;
    panelEdgeTabsRaf = requestAnimationFrame(function () {
        panelEdgeTabsRaf = null;
        layoutPanelEdgeTabs();
    });
}

function initPanelEdgeTabsLayout() {
    var stage = document.querySelector('.chat-stage');
    var todo = document.getElementById('chat-todo-plan');
    var toc = document.getElementById('chat-toc');
    if (!stage || panelEdgeTabsObserver) return;
    panelEdgeTabsObserver = new ResizeObserver(schedulePanelEdgeTabsLayout);
    panelEdgeTabsObserver.observe(stage);
    if (todo) panelEdgeTabsObserver.observe(todo);
    if (toc) panelEdgeTabsObserver.observe(toc);
    schedulePanelEdgeTabsLayout();
}

/* 自动折叠：按侧窗与真实正文列的几何边界判断，并在空间恢复后分别展开。 */
var panelAutoCollapseObserver = null;
var panelAutoCollapseMutationObserver = null;
var panelCollapseRaf = null;
var panelCollapseRecoveryTimer = null;
var panelAutoCollapseCooldownUntil = 0;
var panelWasAutoCollapsed = false;
var panelAutoCollapsedTodo = false;
var panelAutoCollapsedToc = false;
var panelManualOverlapTodo = false;
var panelManualOverlapToc = false;
var panelUserCollapsedTodo = false;

function syncTodoPanelContentVisibility(hasVisibleContent) {
    var todo = document.getElementById('chat-todo-plan');
    if (!todo) return;
    if (!hasVisibleContent) {
        todo.classList.remove('is-open');
        return;
    }
    // Content refreshes only decide whether the panel may be shown. The user's
    // choice and the overlap controller own its open/closed state, otherwise a
    // delayed extension refresh can reopen a panel that was just collapsed.
    if (!panelUserCollapsedTodo && !panelAutoCollapsedTodo) {
        todo.classList.add('is-open');
    }
}
function todoPanelHasVisibleContent() {
    var pluginPanels = document.getElementById('plugin-session-panels');
    return !!(pluginPanels && !pluginPanels.hidden && pluginPanels.children.length);
}

function mainContentColumnRect(fallbackElement) {
    var candidates = document.querySelectorAll(
        '.chat-stream > .msg-wrap, .chat-stream > .process-aggregate, .chat-stream > .welcome'
    );
    for (var i = 0; i < candidates.length; i += 1) {
        var rect = candidates[i].getBoundingClientRect();
        if (rect.width > 0) return rect;
    }
    return fallbackElement.getBoundingClientRect();
}

function schedulePanelRecoveryCheck(delay) {
    if (panelCollapseRecoveryTimer != null) clearTimeout(panelCollapseRecoveryTimer);
    panelCollapseRecoveryTimer = setTimeout(function () {
        panelCollapseRecoveryTimer = null;
        runPanelAutoCollapseCheck();
    }, delay);
}

function runPanelAutoCollapseCheck() {
    var stage = document.querySelector('.chat-stage');
    var composer = document.querySelector('.panel-inner');
    if (!stage || !composer) return;
    var todo = document.getElementById('chat-todo-plan');
    var toc = document.getElementById('chat-toc');
    var tocList = document.getElementById('chat-toc-list');
    var now = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
    var stageRect = stage.getBoundingClientRect();
    var contentRect = mainContentColumnRect(composer);
    var stageStyle = getComputedStyle(stage);
    var preferredPanelWidth = composerCssLengthPx(
        stageStyle.getPropertyValue('--panel-edge-slide-width'),
        Math.max(todo ? todo.offsetWidth : 0, toc ? toc.offsetWidth : 0)
    );
    var COLLISION_GAP = 8;
    var RECOVERY_GAP = 12;
    var leftAvailable = Math.max(0, contentRect.left - stageRect.left);
    var rightAvailable = Math.max(0, stageRect.right - contentRect.right);
    var todoDockOffset = todo ? Math.max(0, todo.getBoundingClientRect().left - stageRect.left) : 0;
    var tocDockOffset = toc ? Math.max(0, stageRect.right - toc.getBoundingClientRect().right) : 0;
    var todoHasRecoveryRoom = leftAvailable >= todoDockOffset + preferredPanelWidth + RECOVERY_GAP;
    var tocHasRecoveryRoom = rightAvailable >= tocDockOffset + preferredPanelWidth + RECOVERY_GAP;
    var todoOpen = !!(todo && todo.classList.contains('is-open'));
    var tocOpen = !!(toc && toc.classList.contains('is-open'));
    var todoRect = todoOpen ? todo.getBoundingClientRect() : null;
    var tocRect = tocOpen ? toc.getBoundingClientRect() : null;
    var todoOverlaps = !!(todoRect && todoRect.width > 0 && todoRect.right + COLLISION_GAP > contentRect.left);
    var tocOverlaps = !!(tocRect && tocRect.width > 0 && tocRect.left - COLLISION_GAP < contentRect.right);
    var changed = false;

    if (panelManualOverlapTodo && (!todoPanelHasVisibleContent() || todoHasRecoveryRoom)) {
        panelManualOverlapTodo = false;
    }
    if (panelManualOverlapToc && (!(tocList && tocList.children.length) || tocHasRecoveryRoom)) {
        panelManualOverlapToc = false;
    }

    if (todoOverlaps && todo && !panelManualOverlapTodo) {
        todo.classList.remove('is-open');
        panelAutoCollapsedTodo = true;
        changed = true;
    }
    if (tocOverlaps && toc && !panelManualOverlapToc) {
        toc.classList.remove('is-open');
        panelAutoCollapsedToc = true;
        changed = true;
    }
    if (changed && (todoOverlaps || tocOverlaps)) {
        panelAutoCollapseCooldownUntil = now + 420;
        schedulePanelRecoveryCheck(440);
    }

    if (now >= panelAutoCollapseCooldownUntil) {
        if (panelAutoCollapsedTodo && !panelUserCollapsedTodo && todo && todoPanelHasVisibleContent()
                && todoHasRecoveryRoom) {
            todo.classList.add('is-open');
            panelAutoCollapsedTodo = false;
            changed = true;
        }
        if (panelAutoCollapsedToc && toc && tocList && tocList.children.length
                && tocHasRecoveryRoom) {
            toc.classList.add('is-open');
            panelAutoCollapsedToc = false;
            changed = true;
        }
    }

    panelWasAutoCollapsed = panelAutoCollapsedTodo || panelAutoCollapsedToc;
    stage.dataset.todoContentOverlap = (todoOverlaps || panelAutoCollapsedTodo) ? 'true' : 'false';
    stage.dataset.tocContentOverlap = (tocOverlaps || panelAutoCollapsedToc) ? 'true' : 'false';
    stage.dataset.todoManualOverlap = panelManualOverlapTodo ? 'true' : 'false';
    stage.dataset.tocManualOverlap = panelManualOverlapToc ? 'true' : 'false';
    if (changed) {
        syncEdgeTabArrows();
        schedulePanelEdgeTabsLayout();
    }
}

function initPanelAutoCollapse() {
    var mainEl = document.querySelector('.main');
    var stage = document.querySelector('.chat-stage');
    if (!mainEl || !stage || panelAutoCollapseObserver) return;
    function schedule() {
        if (panelCollapseRaf != null) return;
        panelCollapseRaf = requestAnimationFrame(function () {
            panelCollapseRaf = null;
            runPanelAutoCollapseCheck();
        });
    }
    panelAutoCollapseObserver = new ResizeObserver(schedule);
    panelAutoCollapseObserver.observe(mainEl);
    panelAutoCollapseObserver.observe(stage);
    var composer = document.querySelector('.panel-inner');
    if (composer) panelAutoCollapseObserver.observe(composer);
    panelAutoCollapseMutationObserver = new MutationObserver(schedule);
    var todo = document.getElementById('chat-todo-plan');
    var toc = document.getElementById('chat-toc');
    var tocList = document.getElementById('chat-toc-list');
    if (todo) panelAutoCollapseObserver.observe(todo);
    if (toc) panelAutoCollapseObserver.observe(toc);
    if (todo) panelAutoCollapseMutationObserver.observe(todo, { attributes: true, attributeFilter: ['class'] });
    if (toc) panelAutoCollapseMutationObserver.observe(toc, { attributes: true, attributeFilter: ['class'] });
    if (tocList) panelAutoCollapseMutationObserver.observe(tocList, { childList: true });
}

var composerSideControlsObserver = null;
var composerSideControlsMutationObserver = null;
var composerSideControlsRaf = null;
var toastComposerHeightObserver = null;

function syncToastComposerOffset() {
    var panel = document.querySelector('.panel');
    var toastHost = document.querySelector('.toast-host');
    if (!panel || !toastHost) return;
    toastHost.style.setProperty('--toast-composer-height', Math.ceil(panel.getBoundingClientRect().height) + 'px');
}

function initToastComposerOffset() {
    var panel = document.querySelector('.panel');
    if (!panel || toastComposerHeightObserver) return;
    toastComposerHeightObserver = new ResizeObserver(syncToastComposerOffset);
    toastComposerHeightObserver.observe(panel);
    window.addEventListener('resize', syncToastComposerOffset, { passive: true });
    syncToastComposerOffset();
}

function composerCssLengthPx(rawValue, fallback) {
    var value = String(rawValue || '').trim().toLowerCase();
    var numeric = Number.parseFloat(value);
    if (!Number.isFinite(numeric)) return Number(fallback) || 0;
    if (value.endsWith('rem')) {
        var rootSize = Number.parseFloat(getComputedStyle(document.documentElement).fontSize);
        return numeric * (Number.isFinite(rootSize) ? rootSize : 16);
    }
    return numeric;
}

function syncComposerSideControlLayout() {
    var mainCenter = document.querySelector('.main-center');
    var panel = document.querySelector('.panel');
    var composer = panel && panel.querySelector('.panel-inner');
    var permission = panel && panel.querySelector('.composer-permission-bar');
    var model = panel && panel.querySelector('.composer-model-bar');
    if (!mainCenter || !panel || !composer || !permission || !model) return;

    var panelRect = panel.getBoundingClientRect();
    var mainCenterRect = mainCenter.getBoundingClientRect();
    var panelStyle = getComputedStyle(panel);
    var endGutter = composerCssLengthPx(panelStyle.paddingRight, 0);
    var preferredWidth = composerCssLengthPx(
        panelStyle.getPropertyValue('--panel-edge-slide-width'),
        Math.max(permission.offsetWidth, model.offsetWidth)
    );
    var safeGap = 8;
    /* Always test against the normal 68cqi column, even while the real column is expanded. */
    var naturalComposerWidth = Math.min(mainCenterRect.width * 0.68, panelRect.width - endGutter);
    var naturalComposerLeft = panelRect.left + (panelRect.width - endGutter - naturalComposerWidth) / 2;
    var naturalComposerRight = naturalComposerLeft + naturalComposerWidth;
    var leftAvailable = Math.max(0, naturalComposerLeft - panelRect.left);
    var rightAvailable = Math.max(0, panelRect.right - endGutter - naturalComposerRight);
    var permissionVisible = !permission.hidden && getComputedStyle(permission).display !== 'none';
    var modelVisible = getComputedStyle(model).display !== 'none';
    var leftOverlap = permissionVisible && preferredWidth + safeGap > leftAvailable;
    var rightOverlap = modelVisible && preferredWidth + safeGap > rightAvailable;
    var overlaps = leftOverlap || rightOverlap;

    panel.classList.toggle('composer-side-controls-stacked', overlaps);
    mainCenter.classList.toggle('content-column-expanded', overlaps);
    panel.dataset.composerControlsOverlap = overlaps ? 'true' : 'false';
    panel.dataset.composerLeftOverlap = leftOverlap ? 'true' : 'false';
    panel.dataset.composerRightOverlap = rightOverlap ? 'true' : 'false';
}

function scheduleComposerSideControlLayout() {
    if (composerSideControlsRaf != null) return;
    composerSideControlsRaf = requestAnimationFrame(function () {
        composerSideControlsRaf = null;
        syncComposerSideControlLayout();
    });
}

function initComposerSideControlLayout() {
    var panel = document.querySelector('.panel');
    var composer = panel && panel.querySelector('.panel-inner');
    var permission = panel && panel.querySelector('.composer-permission-bar');
    var model = panel && panel.querySelector('.composer-model-bar');
    if (!panel || !composer || !permission || !model || composerSideControlsObserver) return;
    composerSideControlsObserver = new ResizeObserver(scheduleComposerSideControlLayout);
    composerSideControlsObserver.observe(panel);
    composerSideControlsObserver.observe(composer);
    composerSideControlsObserver.observe(permission);
    composerSideControlsObserver.observe(model);
    composerSideControlsMutationObserver = new MutationObserver(scheduleComposerSideControlLayout);
    composerSideControlsMutationObserver.observe(permission, { attributes: true, childList: true, characterData: true, subtree: true });
    composerSideControlsMutationObserver.observe(model, { attributes: true, childList: true, characterData: true, subtree: true });
    window.addEventListener('resize', scheduleComposerSideControlLayout, { passive: true });
    document.addEventListener('myagent:language-change', scheduleComposerSideControlLayout);
    scheduleComposerSideControlLayout();
}

initPanelAutoCollapse();
initPanelEdgeTabsLayout();
initComposerSideControlLayout();
initToastComposerOffset();

// Inline HTML (onclick) still expects these on globalThis.
if (typeof globalThis !== 'undefined') {
    globalThis.toggleTodoPlanPanel = toggleTodoPlanPanel;
    globalThis.toggleTocPanel = toggleTocPanel;
}
