let currentSessionId = null;
/** Blocks repeat sends while the async send pipeline is claiming a sessionStore run slot. */
let sendPipelineLock = false;
let sendPipelineLockSessionId = null;
/** 会话在后台跑完后未点开过：侧栏绿点，点开即清除（localStorage 持久化，刷新不丢） */
const sessionUnreadComplete = new Set();
const LS_SESSION_UNREAD = 'myagent-session-unread';
/** 每个会话独立的输入草稿（切换会话恢复） */
const draftBySession = Object.create(null);
const inputPathTokenMap = Object.create(null);
let inputPathRewriteGuard = false;
/** 本会话最近一次成功点击「发送」的用户消息全文（供工具确认失败后「重新发送」） */
const lastUserMessageBySession = Object.create(null);
/** 离开会话时主列表 scrollTop，切回时恢复（本页内；首次进入该会话无记录则置底） */
const LS_SESSION_SECTION_PREFIX = 'myagent-session-section-';
let streamPollTimer = null;
const messageRawMarkdown = new WeakMap();
let liveAutoFollow = true;
/** 生成中：对话区 / 执行过程区是否在底部附近（二者同时满足才跟流，见 refreshLiveAutoFollowPins） */
let streamChatNearBottom = true;
let streamProcNearBottom = true;
let mermaidInitialized = false;
let mermaidIdSeq = 0;
/** 重放历史消息时创建的过程块不记真实起止时间（仅显示步数与工具次数） */
let replayingMessages = false;

/** 历史消息分页：按「对话轮」（每条用户提问为一轮起点），每页条数见 HISTORY_DIALOGUES_PER_PAGE */
let sessionHistoryPaging = null;
let historyOlderLoading = false;
/** 每次加载末尾或更早一页时包含的用户提问轮数（含其间全部工具/过程事件） */
const HISTORY_DIALOGUES_PER_PAGE = 5;

/** 右侧「历史记录」重建序号：防止切换会话后旧 fetch 与当前 DOM 合并导致目录串台 */
let tocRebuildEpoch = 0;
let todoRefreshEpoch = 0;
let tocActiveUpdateRaf = 0;
let tocScrollBottomOnNextBuild = false;
let suppressTocDuringSessionLoad = false;
let switchSessionEpoch = 0;
let messageLoadEpoch = 0;

/** 右侧「历史记录」链接悬停浮层（替代浏览器原生 title） */
let uiHoverTooltipEl = null;
let hoverTooltipMoveScheduled = false;
const UI_HOVER_TIP_DELAY_MS = 500;
let uiHoverTipTimer = null;
let uiHoverTipActiveEl = null;
let uiHoverTipLastEv = null;

let mermaidIoObserver = null;

const defaultCtxThreshold = (typeof window.__CONTEXT_WINDOW__ === 'number' && window.__CONTEXT_WINDOW__ > 0)
    ? window.__CONTEXT_WINDOW__
    : 90000;
let streamScrollFollowRaf = 0;
let subagentScrollFollowRaf = 0;
var subagentCardNearBottom = Object.create(null);
const SUBAGENT_CARD_NEAR_BOTTOM_PX = 48;

var uiModalKeyHandler = null;

function closeUiModal(result) {
    var root = document.getElementById('ui-modal-root');
    if (!root) return;
    root.classList.remove('is-open');
    root.setAttribute('aria-hidden', 'true');
    root.onclick = null;
    var okBtn = document.getElementById('ui-modal-ok');
    var cancelBtn = document.getElementById('ui-modal-cancel');
    if (okBtn) okBtn.onclick = null;
    if (cancelBtn) cancelBtn.onclick = null;
    if (uiModalKeyHandler) {
        document.removeEventListener('keydown', uiModalKeyHandler);
        uiModalKeyHandler = null;
    }
    document.body.style.overflow = '';
    var p = root._resolve;
    root._resolve = null;
    if (typeof p === 'function') p(result);
}

var UI_MODAL_SVG_TRASH = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>';
var UI_MODAL_SVG_INFO = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>';

function openUiModal(options) {
    return new Promise(function (resolve) {
        var root = document.getElementById('ui-modal-root');
        var titleEl = document.getElementById('ui-modal-title');
        var subEl = document.getElementById('ui-modal-subtitle');
        var bodyEl = document.getElementById('ui-modal-desc');
        var iconEl = document.getElementById('ui-modal-icon');
        var okBtn = document.getElementById('ui-modal-ok');
        var cancelBtn = document.getElementById('ui-modal-cancel');
        if (!root || !titleEl || !bodyEl || !okBtn || !cancelBtn || !iconEl) {
            resolve(false);
            return;
        }
        root._resolve = resolve;
        var o = options || {};
        titleEl.textContent = o.title || '提示';
        if (subEl) {
            subEl.textContent = o.subtitle || '';
            subEl.style.display = (o.subtitle) ? '' : 'none';
        }
        bodyEl.textContent = o.message || '';
        bodyEl.style.display = (o.message) ? '' : 'none';
        var showCancel = o.showCancel !== false;
        cancelBtn.style.display = showCancel ? '' : 'none';
        okBtn.textContent = o.confirmText || (showCancel ? '确定' : '知道了');
        cancelBtn.textContent = o.cancelText || '取消';

        var danger = !!o.danger;
        iconEl.className = 'ui-modal__icon ' + (danger ? 'ui-modal__icon--danger' : 'ui-modal__icon--info');
        iconEl.innerHTML = danger ? UI_MODAL_SVG_TRASH : UI_MODAL_SVG_INFO;

        okBtn.className = 'ui-modal-btn ' + (danger ? 'ui-modal-btn--danger' : 'ui-modal-btn--primary');

        function onOk() { closeUiModal(true); }
        function onCancel() { closeUiModal(false); }
        okBtn.onclick = onOk;
        cancelBtn.onclick = onCancel;
        root.onclick = function (e) { if (e.target === root) onCancel(); };

        uiModalKeyHandler = function (e) {
            if (e.key === 'Escape') { e.preventDefault(); onCancel(); }
            else if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey && document.activeElement !== cancelBtn) {
                e.preventDefault();
                onOk();
            }
        };
        document.addEventListener('keydown', uiModalKeyHandler);

        root.classList.add('is-open');
        root.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';
        requestAnimationFrame(function () { okBtn.focus(); });
    });
}

function showUiAlert(opts) {
    var o = opts || {};
    var root = document.getElementById('ui-modal-root');
    var token = Date.now() + ':' + Math.random();
    if (root && o.autoCloseMs) root.dataset.alertToken = token;
    var p = openUiModal({
        title: o.title || '提示',
        subtitle: o.subtitle,
        message: o.message || '',
        variant: o.variant || 'info',
        danger: false,
        showCancel: false,
        confirmText: o.confirmText || '知道了',
    });
    if (root && o.autoCloseMs) {
        setTimeout(function () {
            if (!root.classList.contains('is-open')) return;
            if (root.dataset.alertToken !== token) return;
            closeUiModal(true);
        }, Math.max(800, Number(o.autoCloseMs) || 0));
    }
    return p;
}
