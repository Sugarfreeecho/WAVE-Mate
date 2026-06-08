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
    await loadSessions();
    const sessions = Array.from(document.querySelectorAll('.session-item')).map(item => ({
        id: item.querySelector('.session-name').dataset.id
    }));
    let lastSessionId = localStorage.getItem('lastSessionId');
    let targetSession = null;
    if (lastSessionId && sessions.some(s => s.id === lastSessionId)) targetSession = lastSessionId;
    else if (sessions.length > 0) targetSession = sessions[0].id;
    if (targetSession) await switchSession(targetSession);
    else await createNewSession();
    bindExistingLogs();
    rebuildToc();
}
init();
function toggleTocPanel() {
    panelWasAutoCollapsed = false;
    const toc = document.getElementById('chat-toc');
    if (!toc) return;
    toc.classList.toggle('is-open');
    syncEdgeTabArrows();
    schedulePanelEdgeTabsLayout();
}

function toggleTodoPlanPanel() {
    panelWasAutoCollapsed = false;
    const root = document.getElementById('chat-todo-plan');
    if (!root) return;
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
    const todoList = document.getElementById('chat-todo-plan-list');
    const tocTab = document.getElementById('toc-edge-tab');
    const todoTab = document.getElementById('todo-edge-tab');
    if (tocTab) tocTab.classList.toggle('visible', !!(tocList && tocList.children.length));
    if (todoTab) todoTab.classList.toggle('visible', !!(todoList && todoList.children.length));
    syncEdgeTabArrows();
    schedulePanelEdgeTabsLayout();
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

/* 自动折叠：约在 750–805px 档就要收起；正文占比不足也收起；显著变宽后再展开（滞回 + 冷却） */
var panelAutoCollapseObserver = null;
var panelCollapseRaf = null;
var panelAutoCollapseCooldownUntil = 0;
var panelWasAutoCollapsed = false;

function runPanelAutoCollapseCheck() {
    var mainEl = document.querySelector('.main');
    var stage = document.querySelector('.chat-stage');
    if (!mainEl || !stage) return;
    var mainW = mainEl.clientWidth;
    var stageW = stage.clientWidth;
    var layoutW = Math.min(mainW, stageW);
    var todo = document.getElementById('chat-todo-plan');
    var toc = document.getElementById('chat-toc');
    var tocList = document.getElementById('chat-toc-list');
    var todoList = document.getElementById('chat-todo-plan-list');
    var now = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();

    var LAYOUT_COLLAPSE_AT = 805;
    var LAYOUT_EXPAND_AT = 940;

    if (panelWasAutoCollapsed && now >= panelAutoCollapseCooldownUntil && layoutW >= LAYOUT_EXPAND_AT) {
        panelWasAutoCollapsed = false;
        if (toc && tocList && tocList.children.length && !toc.classList.contains('is-open')) toc.classList.add('is-open');
        if (todo && todoList && todoList.children.length && !todo.classList.contains('is-open')) todo.classList.add('is-open');
        syncEdgeTabArrows();
        return;
    }

    var todoOpen = todo && todo.classList.contains('is-open');
    var tocOpen = toc && toc.classList.contains('is-open');
    if (!todoOpen && !tocOpen) return;

    var todoW = todoOpen ? todo.offsetWidth : 0;
    var tocW = tocOpen ? toc.offsetWidth : 0;
    var centerW = layoutW - todoW - tocW;
    var minCenterByRatio = Math.max(400, Math.floor(layoutW * 0.52));
    var layoutTooNarrow = layoutW <= LAYOUT_COLLAPSE_AT;
    var centerTooTight = centerW < minCenterByRatio;

    if (layoutTooNarrow || centerTooTight) {
        var did = false;
        if (tocOpen) { toc.classList.remove('is-open'); did = true; }
        if (todoOpen) { todo.classList.remove('is-open'); did = true; }
        if (did) {
            panelWasAutoCollapsed = true;
            panelAutoCollapseCooldownUntil = now + 420;
            syncEdgeTabArrows();
        }
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
}

/* 在 rebuildToc 和 applyTodoPlanFromPayload 之后更新箭头可见性 —— 通过 monkey-patch */
(function() {
    var _origRebuildToc = rebuildToc;
    rebuildToc = function() {
        _origRebuildToc.apply(this, arguments);
        setTimeout(updatePanelToggles, 100);
    };
    var _origApplyTodo = applyTodoPlanFromPayload;
    applyTodoPlanFromPayload = function(data) {
        _origApplyTodo.apply(this, arguments);
        setTimeout(updatePanelToggles, 100);
    };
    var _origRenderTodo = renderTodoPlanForCurrentSession;
    renderTodoPlanForCurrentSession = function() {
        _origRenderTodo.apply(this, arguments);
        setTimeout(updatePanelToggles, 100);
    };
    var _origClearTodo = clearTodoPlan;
    clearTodoPlan = async function() {
        await _origClearTodo.apply(this, arguments);
        setTimeout(updatePanelToggles, 100);
    };
})();

initPanelAutoCollapse();
initPanelEdgeTabsLayout();

// Inline HTML (onclick) still expects these on globalThis.
if (typeof globalThis !== 'undefined') {
    globalThis.clearTodoPlan = clearTodoPlan;
    globalThis.toggleTodoPlanPanel = toggleTodoPlanPanel;
    globalThis.toggleTocPanel = toggleTocPanel;
}
