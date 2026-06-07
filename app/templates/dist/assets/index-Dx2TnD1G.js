(function(){const c=document.createElement("link").relList;if(c&&c.supports&&c.supports("modulepreload"))return;for(const o of document.querySelectorAll('link[rel="modulepreload"]'))p(o);new MutationObserver(o=>{for(const l of o)if(l.type==="childList")for(const u of l.addedNodes)u.tagName==="LINK"&&u.rel==="modulepreload"&&p(u)}).observe(document,{childList:!0,subtree:!0});function f(o){const l={};return o.integrity&&(l.integrity=o.integrity),o.referrerPolicy&&(l.referrerPolicy=o.referrerPolicy),o.crossOrigin==="use-credentials"?l.credentials="include":o.crossOrigin==="anonymous"?l.credentials="omit":l.credentials="same-origin",l}function p(o){if(o.ep)return;o.ep=!0;const l=f(o);fetch(o.href,l)}})();(function(g){var c='<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"></path></svg>';function f(){if(!document.getElementById("myagent-path-picker-styles")){var n=document.createElement("style");n.id="myagent-path-picker-styles",n.textContent='.path-input-row{display:flex;align-items:stretch;gap:0.35rem;width:100%;}.path-input-row>.ip,.path-input-row>.tx,.path-input-row>input[type="text"],.path-input-row>input:not([type]){flex:1;min-width:0;}.path-browse-btn{flex-shrink:0;width:2.35rem;padding:0;border:1px solid var(--border-glass,rgba(255,255,255,.08));border-radius:var(--radius-sm,8px);background:var(--surface-glass2,rgba(40,40,60,.94));color:var(--text-secondary,#a6adc8);cursor:pointer;display:inline-flex;align-items:center;justify-content:center;transition:color .18s,border-color .18s,background .18s;}.path-browse-btn:hover{color:var(--text-primary,#cdd6f4);border-color:var(--border-brand-accent,rgba(124,111,247,.35));background:rgba(108,92,231,.12);}.path-browse-btn:disabled{opacity:.45;cursor:not-allowed;}.path-browse-btn--ghost{background:transparent;border-color:transparent;box-shadow:none;width:2.1rem;}.path-browse-btn--ghost:hover{background:rgba(108,92,231,.1);border-color:transparent;color:var(--accent-2,#d4b8fc);}.input-wrapper .path-browse-btn--ghost{align-self:center;margin-right:-0.15rem;}',document.head.appendChild(n)}}async function p(n,e,i){var a=typeof AbortController<"u"?new AbortController:null,s=a?setTimeout(function(){a.abort()},5e4):null,t;try{t=await fetch("/api/pick-path",{method:"POST",headers:{"Content-Type":"application/json"},credentials:"same-origin",body:JSON.stringify({kind:n||"directory",initial:e||"",multiple:!!i}),signal:a?a.signal:void 0})}finally{s&&clearTimeout(s)}var r=await t.json().catch(function(){return{ok:!1,error:"请求失败"}});if(!t.ok||!r.ok){if(r&&r.cancelled)return null;var d=r&&r.error||"无法打开选择对话框";if(/取消|cancelled|800704c7|2147023673/i.test(d))return null;throw new Error(d)}return i?Array.isArray(r.paths)?r.paths:r.path?[r.path]:[]:r.path||null}async function o(n,e,i,a,s){n.disabled=!0;try{var t=await p(e,i||"",!!s);a&&a(t)}catch{return}finally{n.disabled=!1}}function l(n){var e=String(n||"").trim();return e?((e.charAt(0)==='"'&&e.charAt(e.length-1)==='"'||e.charAt(0)==="'"&&e.charAt(e.length-1)==="'")&&(e=e.slice(1,-1)),'"'+e.replace(/"/g,'\\"')+'"'):""}function u(n,e,i){if(!n||n.dataset.pathBrowseWrapped==="1")return n;f();var a=document.createElement("div");a.className="path-input-row";var s=n.parentNode;if(!s)return n;s.insertBefore(a,n),a.appendChild(n);var t=document.createElement("button");t.type="button",t.className="path-browse-btn",t.innerHTML=c;var r=i||"浏览路径";return t.setAttribute("aria-label",r),typeof bindUiHoverTip=="function"?(t.setAttribute("data-ui-tip",r),t.removeAttribute("title"),bindUiHoverTip(t)):t.title=r,t.addEventListener("click",function(d){d.stopPropagation();var m=n.getAttribute("data-path-kind")||e;m!=="file"&&m!=="directory"&&(m="directory"),o(t,m,n.value||"",function(b){if(b){var h=Array.isArray(b)?b[0]||"":String(b);h&&(n.value=h,n.dispatchEvent(new Event("input",{bubbles:!0})),n.dispatchEvent(new Event("change",{bubbles:!0})))}})}),a.appendChild(t),n.dataset.pathBrowseWrapped="1",n}function S(n,e){var i=n.selectionStart,a=n.selectionEnd,s=n.value.slice(0,i),t=n.value.slice(a),r=String(e||"");s.length&&!/\s$/.test(s)&&(r=" "+r),t.length&&!/^\s/.test(t)&&(r=r+" "),n.value=s+r+t;var d=s.length+r.length;n.selectionStart=n.selectionEnd=d,n.dispatchEvent(new Event("input",{bubbles:!0})),n.focus()}function y(n,e){!n||!e||(f(),n.classList.add("path-browse-btn","path-browse-btn--ghost"),n.innerHTML=c,n.setAttribute("aria-label","选择文件"),n.setAttribute("data-ui-tip","选择文件"),n.dataset.silentPickerUnavailable="1",n.removeAttribute("title"),n.addEventListener("click",function(i){i.stopPropagation();var a=g&&typeof g.__WORK_DIR__=="string"?g.__WORK_DIR__:"";o(n,"file",a,function(s){var t=Array.isArray(s)?s:s?[s]:[];if(t.length){var r=t.map(function(d){return l(d)}).join(" ");S(e,r)}},!0)}))}function v(n){n=n||document;var e=n.querySelectorAll("[data-path-kind]"),i;for(i=0;i<e.length;i++){var a=e[i],s=a.getAttribute("data-path-kind");(s==="file"||s==="directory")&&u(a,s)}}g.MyAgentPathPicker={pickPath:p,wrapInputWithBrowse:u,attachChatPicker:y,scan:v},document.readyState==="loading"?document.addEventListener("DOMContentLoaded",function(){v(document)}):v(document)})(typeof window<"u"?window:globalThis);const x=`// ═══════════════════════════════════════════════════════════
// MyAgent · 智能会话 — 完整逻辑
// ═══════════════════════════════════════════════════════════

const chatContainer = document.getElementById('chat-container');
const messageInput = document.getElementById('message-input');
const sendBtn = document.getElementById('send-btn');
const pickPathBtn = document.getElementById('pick-path-btn');
if (window.MyAgentPathPicker && pickPathBtn && messageInput) {
    MyAgentPathPicker.attachChatPicker(pickPathBtn, messageInput);
}
const sessionsList = document.getElementById('sessions-list');
const newSessionBtn = document.getElementById('new-session-btn');
const offscreenRoot = document.getElementById('session-offscreen-buffers');

const LS_UI_FONT = 'myagent-font-level';
const LS_UI_THEME = 'myagent-theme';
const LS_SESSION_LIST_MODE = 'myagent-session-list-mode';
/** 三档字号（rem 基准）：相对此前整体收紧一档（原大→现中、原中→现小） */
/** 三档 root 字号(px)：在「降一档」基准上整体 ×1.2 */
const UI_FONT_PX = [14, 16, 17];
var settingsModalKeyHandler = null;

function getStoredFontLevel() {
    var n = parseInt(localStorage.getItem(LS_UI_FONT), 10);
    if (isNaN(n) || n < 0 || n > 2) return 1;
    return n;
}

function getStoredSessionListMode() {
    var m = localStorage.getItem(LS_SESSION_LIST_MODE);
    return m === 'compact' ? 'compact' : 'detailed';
}

function syncSettingsModalForm() {
    var lvl = getStoredFontLevel();
    for (var i = 0; i < 3; i++) {
        var b = document.getElementById('settings-font-' + i);
        if (b) b.classList.toggle('is-active', i === lvl);
    }
    var light = document.documentElement.classList.contains('theme-light');
    var bd = document.getElementById('settings-theme-dark');
    var bl = document.getElementById('settings-theme-light');
    if (bd) bd.classList.toggle('is-active', !light);
    if (bl) bl.classList.toggle('is-active', light);
    var compact = getStoredSessionListMode() === 'compact';
    var sc = document.getElementById('settings-session-compact');
    var sd = document.getElementById('settings-session-detailed');
    if (sc) sc.classList.toggle('is-active', compact);
    if (sd) sd.classList.toggle('is-active', !compact);
}

function applyFontLevel(level, persist) {
    level = Math.max(0, Math.min(2, level));
    document.documentElement.style.fontSize = UI_FONT_PX[level] + 'px';
    document.documentElement.setAttribute('data-font-level', String(level));
    if (persist) localStorage.setItem(LS_UI_FONT, String(level));
    syncSettingsModalForm();
}

function applyUiTheme(theme, persist) {
    var light = theme === 'light';
    document.documentElement.classList.toggle('theme-light', light);
    if (persist) localStorage.setItem(LS_UI_THEME, light ? 'light' : 'dark');
    syncSettingsModalForm();
}

function applySessionListMode(mode, persist) {
    var next = mode === 'compact' ? 'compact' : 'detailed';
    document.documentElement.setAttribute('data-session-list-mode', next);
    if (persist) localStorage.setItem(LS_SESSION_LIST_MODE, next);
    syncSettingsModalForm();
}

function restoreUiPreferences() {
    applyFontLevel(getStoredFontLevel(), false);
    var t = localStorage.getItem(LS_UI_THEME);
    applyUiTheme(t === 'dark' ? 'dark' : 'light', false);
    applySessionListMode(getStoredSessionListMode(), false);
}
restoreUiPreferences();

function openSettingsModal() {
    var root = document.getElementById('settings-modal-root');
    var panel = root && root.querySelector('.settings-modal');
    if (!root || !panel) return;
    syncSettingsModalForm();
    root.classList.add('is-open');
    root.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    try { panel.focus(); } catch (e) {}
    settingsModalKeyHandler = function (ev) {
        if (ev.key === 'Escape') { ev.preventDefault(); closeSettingsModal(); }
    };
    document.addEventListener('keydown', settingsModalKeyHandler);
}

function closeSettingsModal() {
    var root = document.getElementById('settings-modal-root');
    if (!root) return;
    root.classList.remove('is-open');
    root.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
    if (settingsModalKeyHandler) {
        document.removeEventListener('keydown', settingsModalKeyHandler);
        settingsModalKeyHandler = null;
    }
}

function initUiSettingsControls() {
    var root = document.getElementById('settings-modal-root');
    var gear = document.getElementById('sidebar-settings-btn');
    var closeBtn = document.getElementById('settings-modal-close');
    if (!root) return;
    if (gear) {
        gear.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            openSettingsModal();
        });
    }
    if (closeBtn) closeBtn.addEventListener('click', function () { closeSettingsModal(); });
    root.addEventListener('click', function (e) {
        if (e.target === root) closeSettingsModal();
    });
    var pan = root.querySelector('.settings-modal');
    if (pan) pan.addEventListener('click', function (e) { e.stopPropagation(); });
    for (var i = 0; i < 3; i++) {
        (function (idx) {
            var b = document.getElementById('settings-font-' + idx);
            if (b) b.addEventListener('click', function () { applyFontLevel(idx, true); });
        })(i);
    }
    var bd = document.getElementById('settings-theme-dark');
    var bl = document.getElementById('settings-theme-light');
    if (bd) bd.addEventListener('click', function () { applyUiTheme('dark', true); });
    if (bl) bl.addEventListener('click', function () { applyUiTheme('light', true); });
    var sc = document.getElementById('settings-session-compact');
    var sd = document.getElementById('settings-session-detailed');
    if (sc) sc.addEventListener('click', function () { applySessionListMode('compact', true); });
    if (sd) sd.addEventListener('click', function () { applySessionListMode('detailed', true); });
    var envAdv = document.getElementById('settings-env-advanced');
    if (envAdv) {
        envAdv.addEventListener('click', function () {
            closeSettingsModal();
            var w = window.open('/setup/env', 'myagent-env');
            if (w) {
                try { w.focus(); } catch (e) {}
            } else {
                window.location.href = '/setup/env';
            }
        });
    }
}
initUiSettingsControls();

`,C=`let currentSessionId = null;
const contextTokensBySession = Object.create(null);
const runningBySession = Object.create(null);
/** 阻塞连击发送：在写入 runningBySession 之前的 await 空隙内仍会因 isSessionRunning 为假而误判可发 */
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
/** 服务端仍有 /chat 推流时 true，用于刷新后黄点与轮询补消息 */
let serverStreamActiveBySession = Object.create(null);
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
`,I=`function formatTokenCompact(n) {
    if (n == null || !Number.isFinite(Number(n))) return '—';
    const x = Math.max(0, Math.round(Number(n)));
    if (x >= 1000000) return (x / 1000000).toFixed(1).replace(/\\.0$/, '') + 'M';
    if (x >= 10000) return (x / 1000).toFixed(x % 1000 === 0 ? 0 : 1).replace(/\\.0$/, '') + 'k';
    if (x >= 1000) return (x / 1000).toFixed(1).replace(/\\.0$/, '') + 'k';
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
            + '）。预估进入模型的上下文规模，含历史与系统提示；分母为触发压缩摘要的门限，可在.env文件中 CONTEXT_WINDOW 修改。'
    );
    bindUiHoverTip(el);
}

async function refreshContextTokensFromServer(sid) {
    if (!sid) return;
    try {
        const r = await fetch('/sessions/' + encodeURIComponent(sid) + '/context_tokens');
        const j = await r.json();
        if (r.ok && j && j.ok && j.estimated != null && j.estimated >= 0) {
            recordContextTokens(sid, j.estimated, j.threshold);
            return;
        }
    } catch (e) { /* ignore */ }
    applyContextTokenLabelForCurrentSession();
}

/** 在浏览器完成首帧绘制后再请求 context_tokens，避免与切换会话/新建会话的 DOM 抢主线程。 */
function scheduleContextTokensAfterPaint(sid) {
    if (!sid) return;
    requestAnimationFrame(function () {
        requestAnimationFrame(function () {
            refreshContextTokensFromServer(sid);
        });
    });
}

function recordContextTokens(sessionId, estimated, threshold) {
    if (!sessionId) return;
    if (estimated != null && Number(estimated) >= 0) {
        contextTokensBySession[sessionId] = { estimated: Number(estimated), threshold: threshold };
    } else {
        delete contextTokensBySession[sessionId];
    }
    if (sessionId === currentSessionId) setContextTokenLabel(estimated, threshold);
}

function applyContextTokenLabelForCurrentSession() {
    if (!currentSessionId) { setContextTokenLabel(null, null); return; }
    const x = contextTokensBySession[currentSessionId];
    if (x) setContextTokenLabel(x.estimated, x.threshold);
    else setContextTokenLabel(null, null);
}

/** 主对话区跟到底 */
function scrollChatToBottomIfFollow(runSessionId, opts) {
    opts = opts || {};
    if (shouldGateScrollByRunSession(null, runSessionId)) return;
    if (!opts.force && !liveAutoFollow) return;
    if (chatContainer) chatContainer.scrollTop = chatContainer.scrollHeight;
}

function setScrollTopImmediate(el, y) {
    if (!el) return;
    var prev = el.style.scrollBehavior;
    el.style.scrollBehavior = 'auto';
    el.scrollTop = y;
    requestAnimationFrame(function () {
        if (el) el.style.scrollBehavior = prev;
    });
}

/** 当前运行会话对应的执行过程框滚动容器（.process-aggregate-body） */
function getProcessBodyElForCurrentRun() {
    var sid = currentSessionId;
    var run = sid && runningBySession[sid];
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

/** 生成中时：对话区与当前执行过程区均在底部附近时才允许自动跟随流式滚动 */
function refreshLiveAutoFollowPins() {
    if (!chatContainer) return;
    if (isSessionRunning(currentSessionId)) {
        streamChatNearBottom = isNearBottom(chatContainer, STREAM_CHAT_NEAR_BOTTOM_PX);
        var pb = getProcessBodyElForCurrentRun();
        streamProcNearBottom = !pb || isNearBottom(pb, STREAM_PROC_NEAR_BOTTOM_PX);
        liveAutoFollow = streamChatNearBottom && streamProcNearBottom;
    } else {
        liveAutoFollow = isNearBottom(chatContainer, STREAM_CHAT_NEAR_BOTTOM_PX);
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
            if (ev.type === 'llm_reasoning_delta' || ev.type === 'llm_response_delta') {
                appendLlmStreamDelta(ctx, ev, agentId);
            } else if (ev.type === 'context_summary_delta') {
                appendProgressStreamDelta(ctx, ev.delta, 'context-summary', agentId);
            } else if (ev.type === 'key_context_delta') {
                appendKeyContextStreamDelta(ctx, ev.delta, agentId);
            } else if (ev.type === 'context_tokens' || ev.type === 'process_metrics' || ev.type === 'cache_stats') {
                /* metrics 类事件只更新卡片统计，不在展开过程里落一条“信息”。 */
            }
            return;
        }
        renderEvent(ctx, ev, item.eventIndex, agentId);
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
        subagentCardNearBottom[aid] = isNearBottom(body, SUBAGENT_CARD_NEAR_BOTTOM_PX);
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

function scrollContentAreaIfFollow(ctx, runSessionId) {
    if (shouldGateScrollByRunSession(ctx, runSessionId)) return;
    if (isSubagentStreamCtx(ctx)) {
        if (!shouldFollowSubagentCard(ctx)) return;
        scrollSubagentCardBodyToBottom(ctx);
        return;
    }
    if (!liveAutoFollow) return;
    scrollProcessBodyToBottom(ctx, runSessionId);
    scrollChatToBottomIfFollow(runSessionId, {});
}

/** 将当前轮次的执行框滚到底（流式增量主要长在这里，必须滚 procBody 而不是只滚对话区） */
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

function followStreamProcessScroll(ctx, runSessionId) {
    if (shouldGateScrollByRunSession(ctx, runSessionId)) return;
    if (isSubagentStreamCtx(ctx)) {
        if (!shouldFollowSubagentCard(ctx)) return;
        if (subagentScrollFollowRaf) return;
        subagentScrollFollowRaf = requestAnimationFrame(function () {
            subagentScrollFollowRaf = 0;
            scrollSubagentCardBodyToBottom(ctx);
            refreshFeedChunksInCtx(ctx, '.feed-chunk.is-streaming');
        });
        return;
    }
    if (!liveAutoFollow) return;
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
    btn.textContent = '更早 ' + HISTORY_DIALOGUES_PER_PAGE + ' 轮对话';
    btn.addEventListener('click', function () { loadOlderHistoryChunk(); });
    el.appendChild(btn);
    streamEl.insertBefore(el, streamEl.firstChild);
    return el;
}

function getHistoryScrollAnchor(container) {
    if (!container) return null;
    var cr = container.getBoundingClientRect();
    var nodes = container.querySelectorAll('.msg-wrap, .process-aggregate, .welcome');
    for (var i = 0; i < nodes.length; i += 1) {
        var n = nodes[i];
        if (!n || !n.isConnected || n.id === 'chat-loading') continue;
        var r = n.getBoundingClientRect();
        if (r.bottom >= cr.top + 4) return { el: n, top: r.top };
    }
    return null;
}

function updateHistorySentinelVisibility() {
    var strip = document.getElementById('history-load-sentinel');
    var btn = strip && strip.querySelector('.history-load-older-btn');
    var ph = sessionHistoryPaging;
    if (!strip || !btn) return;
    if (!ph || !ph.has_older || ph.sessionId !== currentSessionId) {
        strip.hidden = true;
        btn.disabled = false;
        btn.textContent = '更早 ' + HISTORY_DIALOGUES_PER_PAGE + ' 轮对话';
        return;
    }
    strip.hidden = false;
    btn.disabled = historyOlderLoading;
    btn.textContent = historyOlderLoading ? '加载中…' : ('更早 ' + HISTORY_DIALOGUES_PER_PAGE + ' 轮对话');
}

function resetSessionHistoryPaging() {
    sessionHistoryPaging = null;
    historyOlderLoading = false;
    updateHistorySentinelVisibility();
}

async function loadOlderHistoryChunk(opts) {
    opts = opts || {};
    var sid = currentSessionId;
    var ph = sessionHistoryPaging;
    if (!sid || !ph || ph.sessionId !== sid || !ph.has_older || historyOlderLoading) return;
    historyOlderLoading = true;
    var prevReplaying = replayingMessages;
    replayingMessages = true;
    updateHistorySentinelVisibility();
    var stream = getVisibleChatStream();
    var cc = chatContainer;
    var prevScrollTop = cc ? cc.scrollTop : 0;
    var anchor = getHistoryScrollAnchor(cc);
    var loadedOlder = false;
    try {
        var url = '/sessions/' + encodeURIComponent(sid) + '/messages?turns=' + HISTORY_DIALOGUES_PER_PAGE + '&before_index=' + ph.range_start;
        var response = await fetch(url);
        var data = await response.json();
        if (!response.ok || !data || typeof data !== 'object') return;
        var events = data.events;
        if (!Array.isArray(events) || events.length === 0) {
            sessionHistoryPaging = Object.assign({}, ph, { has_older: !!data.has_older });
            return;
        }
        ensureHistorySentinel(stream);
        var frag = document.createDocumentFragment();
        var tmpCtx = newDomContext(frag);
        tmpCtx.lastUserEventIndex = -1;
        var rs = typeof data.range_start === 'number' ? data.range_start : 0;
        for (var i = 0; i < events.length; i += 1) {
            var ev = events[i];
            if (ev && typeof ev === 'object' && ev.type) renderEvent(tmpCtx, ev, rs + i, sid);
        }
        var sen = stream && stream.querySelector('#history-load-sentinel');
        if (stream && frag.childNodes.length) {
            stream.insertBefore(frag, sen ? sen.nextSibling : stream.firstChild);
        }
        loadedOlder = true;
        sessionHistoryPaging = {
            sessionId: sid,
            total: typeof data.total === 'number' ? data.total : ph.total,
            range_start: typeof data.range_start === 'number' ? data.range_start : ph.range_start,
            range_end: ph.range_end,
            has_older: !!data.has_older,
        };
    } catch (e) {
        console.error('加载更早消息失败:', e);
    } finally {
        historyOlderLoading = false;
        updateHistorySentinelVisibility();
        if (cc && stream && stream.parentNode === cc) {
            if (anchor && anchor.el && anchor.el.isConnected) {
                var nextTop = anchor.el.getBoundingClientRect().top;
                setScrollTopImmediate(cc, cc.scrollTop + (nextTop - anchor.top));
            } else {
                setScrollTopImmediate(cc, prevScrollTop);
            }
        }
        if (loadedOlder) {
            bindExistingLogs(stream);
            if (!opts.keepTocStable) rebuildToc();
            scheduleTocActiveUpdate();
        }
        replayingMessages = prevReplaying;
    }
}

function insertNewEmptyChatStream() { ensureVisibleChatStreamSlot(); }

function prepareStashLeaving(leavingId) {
    if (!leavingId) return;
    if (isSessionRunning(leavingId)) {
        const el = getVisibleChatStream();
        if (el && el.parentNode) {
            el.remove();
            el.removeAttribute('id');
            el.removeAttribute('aria-label');
            if (offscreenRoot) offscreenRoot.appendChild(el);
        }
        insertNewEmptyChatStream();
    } else {
        const v = getVisibleChatStream();
        if (v) {
            resetSessionHistoryPaging();
            emptyChatStreamKeepingStrip(v);
        }
        else ensureVisibleChatStreamSlot();
    }
}

function restoreStreamForRunningSession(enteringId) {
    const run = runningBySession[enteringId];
    if (!run || !run.ctx || !run.ctx.stream) return false;
    const st = run.ctx.stream;
    if (!st.parentNode) return false;
    if (st.parentNode === chatContainer) return st.id === 'chat-stream';
    if (offscreenRoot && st.parentNode !== offscreenRoot) return false;
    const cur = getVisibleChatStream();
    if (cur && cur.parentNode === chatContainer) cur.remove();
    st.id = 'chat-stream';
    st.setAttribute('aria-label', '消息');
    chatContainer.appendChild(st);
    bindExistingLogs(st);
    return true;
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
                var aClose = filter.carry.slice(ae).match(/^<\\/analysis\\s*>/i);
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
            chunk = chunk.replace(/^<summary[^>]*>\\s*/i, '');
            out += chunk;
            if (se >= 0) {
                var sClose = filter.carry.slice(se).match(/^<\\/summary\\s*>/i);
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
    return !!(sessionId && (runningBySession[sessionId] || serverStreamActiveBySession[sessionId]));
}

function syncDisconnectedProcessGroups() {
    Object.keys(runningBySession).forEach(function (sid) {
        const c = runningBySession[sid].ctx;
        if (c && c.currentProcessGroup && !c.currentProcessGroup.isConnected) c.currentProcessGroup = null;
    });
}

function finalizeLlmStreamChunks(ctx) {
    if (!ctx) return;
    flushLlmDeltaText(ctx);
    queryFeedChunksInCtx(ctx, '.feed-chunk.is-streaming').forEach(function (ch) {
        ch.classList.remove('is-streaming');
        scheduleFeedChunkOverflowRefresh(ch);
    });
    if (ctx.llm) {
        const l = ctx.llm;
        l.llmStreamReasoningIter = null;
        l.llmStreamResponseIter = null;
        l.llmStreamReasoningScroller = null;
        l.llmStreamResponseScroller = null;
        l.llmDeltaLastSeq = null;
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

function flushLlmDeltaText(ctx) {
    if (!ctx || !ctx.llm) return;
    const l = ctx.llm;
    if (l.llmDeltaFlushRaf) {
        cancelAnimationFrame(l.llmDeltaFlushRaf);
        l.llmDeltaFlushRaf = 0;
    }
    if (l.llmPendingReasoningDelta && l.llmStreamReasoningScroller) {
        var rs = trimSurroundingBlankLines((l.llmStreamReasoningScroller.textContent || '') + l.llmPendingReasoningDelta);
        l.llmStreamReasoningScroller.textContent = truncateLogTextForUi(rs);
    }
    l.llmPendingReasoningDelta = '';
    if (l.llmPendingResponseDelta && l.llmStreamResponseScroller) {
        var rsp = trimSurroundingBlankLines((l.llmStreamResponseScroller.textContent || '') + l.llmPendingResponseDelta);
        l.llmStreamResponseScroller.textContent = truncateLogTextForUi(rsp);
    }
    l.llmPendingResponseDelta = '';
}

function scheduleLlmDeltaFlush(ctx, runSessionId) {
    const l = ctx.llm;
    if (!l || l.llmDeltaFlushRaf) return;
    l.llmDeltaFlushRaf = requestAnimationFrame(function () {
        l.llmDeltaFlushRaf = 0;
        flushLlmDeltaText(ctx);
        followStreamProcessScroll(ctx, runSessionId);
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
        fetch('/api/open-workspace-file?rel=' + encodeURIComponent(rel))
            .then(function (r) {
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

async function getUiEventCount(sessionId) {
    const sid = sessionId != null ? sessionId : currentSessionId;
    if (!sid) return 0;
    try {
        const r = await fetch('/sessions/' + encodeURIComponent(sid) + '/messages/count');
        if (!r.ok) return 0;
        const j = await r.json();
        return (j && typeof j.count === 'number') ? j.count : 0;
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
    if (!messageInput || !sessionId) return;
    draftBySession[sessionId] = messageInput.value;
}

function restoreInputDraft(sessionId) {
    if (!messageInput) return;
    const v = (sessionId && Object.prototype.hasOwnProperty.call(draftBySession, sessionId))
        ? draftBySession[sessionId]
        : '';
    messageInput.value = v != null ? String(v) : '';
    rewriteInputWorkspacePaths();
    autoResizeTextarea();
}

function clearStreamPoll() {
    if (streamPollTimer) {
        clearInterval(streamPollTimer);
        streamPollTimer = null;
    }
}

async function fetchSessionStreamActiveMap() {
    try {
        const response = await fetch('/sessions');
        const sessions = await response.json();
        if (!Array.isArray(sessions)) return Object.create(null);
        const m = Object.create(null);
        for (let i = 0; i < sessions.length; i += 1) {
            const s = sessions[i];
            if (s && s.id) m[s.id] = !!s.stream_active;
        }
        return m;
    } catch (e) {
        return Object.create(null);
    }
}

function maybeStartStreamPollForSession(sid) {
    clearStreamPoll();
    if (!sid) return;
    if (!serverStreamActiveBySession[sid]) return;
    if (!runningBySession[sid] && typeof attachSessionEventStream === 'function') {
        void attachSessionEventStream(sid);
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
            const m = await fetchSessionStreamActiveMap();
            serverStreamActiveBySession = m;
            const still = !!m[sid];
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

async function scrollToUserTurnOrLoadOlder(eventIndex) {
    var ei = Number(eventIndex);
    if (!Number.isFinite(ei)) return;
    function findWrap() {
        return document.querySelector('.msg-wrap--user[data-event-index="' + ei + '"]')
            || document.getElementById('user-msg-' + ei);
    }
    var wrap = findWrap();
    if (wrap) {
        wrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return;
    }
    var sid = currentSessionId;
    var safety = 0;
    var pagingCoveredTarget = false;
    while (sid === currentSessionId && safety < 120) {
        safety += 1;
        wrap = findWrap();
        if (wrap) {
            wrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
            return;
        }
        var ph = sessionHistoryPaging;
        if (!ph || ph.sessionId !== sid) break;
        if (ei >= ph.range_start) {
            pagingCoveredTarget = true;
            break;
        }
        if (!ph.has_older) break;
        while (historyOlderLoading && currentSessionId === sid) {
            await new Promise(function (r) { setTimeout(r, 40); });
        }
        await loadOlderHistoryChunk({ keepTocStable: true });
    }
    wrap = findWrap();
    if (wrap) {
        wrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return;
    }
    if (sid === currentSessionId && pagingCoveredTarget && typeof loadSessionMessages === 'function') {
        try {
            await loadSessionMessages(sid, 'saved-or-bottom', { full: true });
        } catch (e) {
            console.error('reload full history for toc target failed:', e);
        }
        if (sid !== currentSessionId) return;
        wrap = findWrap();
        if (wrap) {
            wrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
            return;
        }
        rebuildToc();
    }
    if (wrap) wrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
    else {
        showUiAlert({
            title: '无法定位该条',
            message: '未能加载到对应的用户提问（可能索引不一致）。可刷新页面或使用「更早 ' + HISTORY_DIALOGUES_PER_PAGE + ' 轮对话」手动分页。',
            showCancel: false,
            confirmText: '知道了',
        });
    }
}
`,w=`function ensureUiHoverTooltipEl() {
    if (uiHoverTooltipEl) return uiHoverTooltipEl;
    uiHoverTooltipEl = document.getElementById('ui-hover-tooltip');
    if (!uiHoverTooltipEl) {
        uiHoverTooltipEl = document.createElement('div');
        uiHoverTooltipEl.id = 'ui-hover-tooltip';
        uiHoverTooltipEl.setAttribute('role', 'tooltip');
        document.body.appendChild(uiHoverTooltipEl);
    }
    return uiHoverTooltipEl;
}

function showUiHoverTooltip(ev, text) {
    var t = (text != null) ? String(text) : '';
    if (!t.trim()) return;
    var el = ensureUiHoverTooltipEl();
    el.textContent = t;
    el.classList.add('is-visible');
    requestAnimationFrame(function () {
        positionUiHoverTooltip(ev);
    });
}

function moveUiHoverTooltip(ev) {
    if (!uiHoverTooltipEl || !uiHoverTooltipEl.classList.contains('is-visible')) return;
    if (hoverTooltipMoveScheduled) return;
    hoverTooltipMoveScheduled = true;
    requestAnimationFrame(function () {
        hoverTooltipMoveScheduled = false;
        positionUiHoverTooltip(ev);
    });
}

function clearUiHoverTipTimer() {
    if (uiHoverTipTimer) {
        clearTimeout(uiHoverTipTimer);
        uiHoverTipTimer = null;
    }
}

function hideUiHoverTooltip() {
    clearUiHoverTipTimer();
    uiHoverTipActiveEl = null;
    uiHoverTipLastEv = null;
    if (uiHoverTooltipEl) uiHoverTooltipEl.classList.remove('is-visible');
}

function positionUiHoverTooltip(ev) {
    var el = uiHoverTooltipEl;
    if (!el) return;
    el.style.left = '-9999px';
    el.style.top = '0';
    var pad = 14;
    var bw = el.offsetWidth;
    var bh = el.offsetHeight;
    var vw = window.innerWidth;
    var vh = window.innerHeight;
    var x = ev.clientX + pad;
    var y = ev.clientY + pad;
    if (x + bw > vw - 10) x = Math.max(10, vw - bw - 10);
    if (y + bh > vh - 10) y = Math.max(10, ev.clientY - bh - pad);
    if (x < 10) x = 10;
    if (y < 10) y = 10;
    el.style.left = x + 'px';
    el.style.top = y + 'px';
}

/** 统一悬停说明（替代原生 title），文案来自 data-ui-tip；停留超过 UI_HOVER_TIP_DELAY_MS 才显示 */
function bindUiHoverTip(el) {
    if (!el || el._uiHoverTipBound) return;
    var tip = el.getAttribute('data-ui-tip');
    if (!tip || !String(tip).trim()) {
        var legacyTitle = el.getAttribute('title');
        if (legacyTitle && String(legacyTitle).trim()) {
            el.setAttribute('data-ui-tip', legacyTitle);
            tip = legacyTitle;
        }
    }
    if (!tip || !String(tip).trim()) return;
    el._uiHoverTipBound = true;
    el.removeAttribute('title');
    el.addEventListener('mouseenter', function (ev) {
        var t = el.getAttribute('data-ui-tip');
        if (t == null || !String(t).trim()) return;
        clearUiHoverTipTimer();
        hideUiHoverTooltip();
        uiHoverTipActiveEl = el;
        uiHoverTipLastEv = ev;
        uiHoverTipTimer = setTimeout(function () {
            uiHoverTipTimer = null;
            if (uiHoverTipActiveEl !== el) return;
            showUiHoverTooltip(uiHoverTipLastEv || ev, t);
        }, UI_HOVER_TIP_DELAY_MS);
    });
    el.addEventListener('mousemove', function (ev) {
        uiHoverTipLastEv = ev;
        moveUiHoverTooltip(ev);
    });
    el.addEventListener('mouseleave', function () {
        if (uiHoverTipActiveEl === el) uiHoverTipActiveEl = null;
        clearUiHoverTipTimer();
        hideUiHoverTooltip();
    });
}

function initUiHoverTips(root) {
    root = root || document;
    root.querySelectorAll('[data-ui-tip]').forEach(function (el) {
        bindUiHoverTip(el);
    });
    root.querySelectorAll('[title]').forEach(function (el) {
        bindUiHoverTip(el);
    });
}

function scheduleTocActiveUpdate() {
    if (tocActiveUpdateRaf) return;
    tocActiveUpdateRaf = requestAnimationFrame(function () {
        tocActiveUpdateRaf = 0;
        updateTocActiveFromViewport();
    });
}

function updateTocActiveFromViewport() {
    var list = document.getElementById('chat-toc-list');
    if (!list || !chatContainer) return;
    var stream = getVisibleChatStream();
    if (!stream) return;
    var users = stream.querySelectorAll('.msg-wrap--user[data-event-index]');
    if (!users.length) return;
    var cr = chatContainer.getBoundingClientRect();
    var pivot = cr.top + cr.height * 0.5;
    var chosen = null;
    for (var i = 0; i < users.length; i += 1) {
        var u = users[i];
        var r = u.getBoundingClientRect();
        if (r.top <= pivot) {
            chosen = u;
            continue;
        }
        break;
    }
    if (!chosen) chosen = users[0];
    if (!chosen) return;
    var idx = chosen.getAttribute('data-event-index');
    if (idx == null) return;
    var active = list.querySelector('a[data-event-index="' + idx + '"]');
    list.querySelectorAll('a.is-current').forEach(function (a) {
        if (a !== active) a.classList.remove('is-current');
    });
    if (!active) return;
    active.classList.add('is-current');
    var pad = 6;
    var top = active.offsetTop;
    var bottom = top + active.offsetHeight;
    if (top < list.scrollTop + pad) {
        list.scrollTop = Math.max(0, top - pad);
    } else if (bottom > list.scrollTop + list.clientHeight - pad) {
        list.scrollTop = bottom - list.clientHeight + pad;
    }
}

function clearTocForSessionLoad() {
    const toc = document.getElementById('chat-toc');
    const list = document.getElementById('chat-toc-list');
    tocRebuildEpoch += 1;
    if (list) list.textContent = '';
    if (toc) toc.classList.remove('is-open');
}

function clearTodoForSessionLoad() {
    const root = document.getElementById('chat-todo-plan');
    const statsEl = document.getElementById('chat-todo-plan-stats');
    const listEl = document.getElementById('chat-todo-plan-list');
    todoRefreshEpoch += 1;
    if (statsEl) statsEl.textContent = '';
    if (listEl) listEl.textContent = '';
    if (root) root.classList.remove('is-open');
}

function rebuildToc() {
    const toc = document.getElementById('chat-toc');
    const list = document.getElementById('chat-toc-list');
    if (!toc || !list) return;
    if (suppressTocDuringSessionLoad) {
        clearTocForSessionLoad();
        return;
    }
    if (!list._tocTipScrollHide) {
        list._tocTipScrollHide = true;
        list.addEventListener('scroll', hideUiHoverTooltip, { passive: true });
    }
    list.textContent = '';
    const sid = currentSessionId;
    const epoch = ++tocRebuildEpoch;
    (async function () {
        let turns = [];
        if (sid) {
            try {
                const r = await fetch('/sessions/' + encodeURIComponent(sid) + '/user_turns');
                if (epoch !== tocRebuildEpoch || sid !== currentSessionId) return;
                if (r.ok) {
                    const j = await r.json();
                    if (Array.isArray(j)) turns = j;
                }
            } catch (e) { /* ignore */ }
        }
        if (epoch !== tocRebuildEpoch || sid !== currentSessionId) return;
        /** event_index → 预览（服务端与当前 DOM 合并：刚发出的提问尚未写入 ui_events，由气泡补上） */
        const merged = new Map();
        turns.forEach(function (row) {
            const ei = Number(row.event_index);
            if (!Number.isFinite(ei)) return;
            merged.set(ei, String(row.preview || '').trim());
        });
        const vs = getVisibleChatStream();
        const rootForUsers = vs || chatContainer;
        if (rootForUsers) {
            rootForUsers.querySelectorAll('.msg-wrap--user[data-event-index]').forEach(function (wrap) {
                const ei = parseInt(wrap.getAttribute('data-event-index'), 10);
                if (!Number.isFinite(ei)) return;
                const text = (wrap.querySelector('.message') && wrap.querySelector('.message').innerText || '').trim();
                merged.set(ei, text);
            });
        }
        if (epoch !== tocRebuildEpoch || sid !== currentSessionId) return;
        list.replaceChildren();
        let indices = [...merged.keys()].filter(function (x) { return Number.isFinite(x); }).sort(function (a, b) { return a - b; });
        function normalizedPreviewKey(p) {
            return String(p || '').trim().replace(/\\s+/g, ' ');
        }
        const dupCountByKey = new Map();
        indices.forEach(function (ei) {
            const k = normalizedPreviewKey(merged.get(ei));
            dupCountByKey.set(k, (dupCountByKey.get(k) || 0) + 1);
        });
        function appendTocLink(label, titleFull, scrollToWrap, eventIndex) {
            const a = document.createElement('a');
            a.href = '#';
            if (eventIndex != null) a.setAttribute('data-event-index', String(eventIndex));
            var tipText = (titleFull != null && String(titleFull).trim() !== '')
                ? String(titleFull)
                : String(label || '');
            a.setAttribute('data-ui-tip', tipText);
            bindUiHoverTip(a);
            const tocSpan = document.createElement('span');
            tocSpan.className = 'chat-toc-text';
            tocSpan.textContent = label;
            a.appendChild(tocSpan);
            a.addEventListener('click', function (e) {
                e.preventDefault();
                hideUiHoverTooltip();
                if (typeof scrollToWrap === 'function') scrollToWrap();
            });
            list.appendChild(a);
        }
        if (indices.length === 0) {
            const users = rootForUsers ? rootForUsers.querySelectorAll('.msg-wrap--user') : [];
            if (users.length === 0) {
                toc.classList.remove('is-open');
                return;
            }
            toc.classList.add('is-open');
            users.forEach(function (wrap, idx) {
                if (!wrap.id) wrap.id = 'user-msg-' + idx;
                const text = (wrap.querySelector('.message') && wrap.querySelector('.message').innerText || '').trim();
                const label = text.length > 44 ? text.slice(0, 42) + '…' : (text || ('问题 ' + (idx + 1)));
                appendTocLink(label, text, function () {
                    wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }, wrap.getAttribute('data-event-index'));
            });
        } else {
            toc.classList.add('is-open');
            indices.forEach(function (ei) {
                const preview = merged.get(ei) || '';
                var label = preview.length > 44 ? preview.slice(0, 42) + '…' : (preview || ('问题 #' + (ei + 1)));
                var titleFull = preview || label;
                const nk = normalizedPreviewKey(preview);
                if ((dupCountByKey.get(nk) || 0) > 1) {
                    label = label + ' #' + (ei + 1);
                    titleFull = (preview || '') + '（事件索引 ' + ei + '）';
                }
                appendTocLink(label, titleFull, function () {
                    void scrollToUserTurnOrLoadOlder(ei);
                }, ei);
            });
        }
        if (tocScrollBottomOnNextBuild) {
            tocScrollBottomOnNextBuild = false;
            list.scrollTop = list.scrollHeight;
        } else if (!replayingMessages) {
            requestAnimationFrame(function () {
                requestAnimationFrame(function () {
                    list.scrollTop = list.scrollHeight;
                });
            });
        } else {
            scheduleTocActiveUpdate();
        }
    })();
}

function todoPlanStatusLabel(st) {
    if (st === 'completed') return '已完成';
    if (st === 'in_progress') return '进行中';
    return '待处理';
}

function hideTodoPlanPanel() {
    const root = document.getElementById('chat-todo-plan');
    if (!root) return;
    root.classList.remove('is-open');
}

async function clearTodoPlan() {
    const sid = currentSessionId;
    if (!sid) return;
    try {
        await fetch('/sessions/' + encodeURIComponent(sid) + '/todo_plan', { method: 'DELETE' });
    } catch (e) { /* ignore */ }
    hideTodoPlanPanel();
    const statsEl = document.getElementById('chat-todo-plan-stats');
    const listEl = document.getElementById('chat-todo-plan-list');
    if (statsEl) statsEl.textContent = '';
    if (listEl) listEl.textContent = '';
}

function applyTodoPlanFromPayload(data) {
    const root = document.getElementById('chat-todo-plan');
    const listEl = document.getElementById('chat-todo-plan-list');
    const statsEl = document.getElementById('chat-todo-plan-stats');
    if (!root || !listEl || !statsEl) return;
    const items = data && Array.isArray(data.items) ? data.items : [];
    const has = !!(data && data.has_plan && items.length > 0);
    if (!has) {
        listEl.textContent = '';
        statsEl.textContent = '';
        hideTodoPlanPanel();
        return;
    }
    const done = typeof data.done === 'number'
        ? data.done
        : items.filter(function (x) { return x && x.status === 'completed'; }).length;
    const total = typeof data.total === 'number' ? data.total : items.length;
    statsEl.textContent = String(done) + ' / ' + String(total) + ' 已完成';
    listEl.textContent = '';
    items.forEach(function (it) {
        const li = document.createElement('li');
        const st = (it && it.status) || 'pending';
        li.className = 'todo-plan-item todo-plan--' + String(st);
        const tag = document.createElement('span');
        tag.className = 'todo-plan-status-tag';
        tag.textContent = todoPlanStatusLabel(st);
        li.appendChild(tag);
        const text = document.createElement('span');
        text.textContent = (it && it.text != null) ? String(it.text) : '';
        li.appendChild(text);
        listEl.appendChild(li);
    });
    root.classList.add('is-open');
}

async function refreshTodoPlanPanel() {
    const sid = currentSessionId;
    if (!sid) {
        hideTodoPlanPanel();
        const statsEl = document.getElementById('chat-todo-plan-stats');
        const listEl = document.getElementById('chat-todo-plan-list');
        if (statsEl) statsEl.textContent = '';
        if (listEl) listEl.textContent = '';
        return;
    }
    try {
        const r = await fetch('/sessions/' + encodeURIComponent(sid) + '/todo_plan');
        if (!r.ok) {
            hideTodoPlanPanel();
            return;
        }
        const j = await r.json();
        if (sid !== currentSessionId) return;
        applyTodoPlanFromPayload(j);
    } catch (e) {
        hideTodoPlanPanel();
    }
}

`,T=`function removeMessagesFromNode(startWrap) {
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
    return base + '\\n原因：' + friendly;
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
    return String(text || '').replace(/\\s+/g, ' ').trim();
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
        if (!Number.isFinite(before)) {
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
    const line = (text.split(/\\n/)[0] || text).trim();
    var m = line.match(/^([A-Za-z_][\\w-]*)\\s*\\(/);
    if (m) return m[1];
    m = line.match(/^([^\\s(]+)\\s*\\(/);
    if (m) return m[1];
    m = line.match(/^(\\S+?)(?:\\(|：)/);
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
    if (!replayingMessages) wrap.dataset.procStartedAt = String(procNow());
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

function getScrollPositionKey(sessionId) {
    return LS_SCROLL_POSITION_PREFIX + sessionId;
}

function saveScrollPosition(sessionId, scrollTop) {
    if (!sessionId) return;
    try {
        localStorage.setItem(getScrollPositionKey(sessionId), String(Math.round(scrollTop)));
    } catch (e) { /* ignore */ }
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
        var run = runningBySession[sessionId];
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

const WELCOME_HTML = \`<div class="welcome" role="status"><div class="welcome-icon" aria-hidden="true"><svg viewBox="0 0 44 22" fill="none" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;user-select:none;-webkit-user-select:none;pointer-events:none"><text x="22" y="16" text-anchor="middle" font-family="'Brush Script MT','Segoe Script','Pacifico','Dancing Script',cursive" font-size="14" font-style="italic" fill="white" stroke="none" transform="rotate(-6 22 11)">Sugar</text></svg></div><strong>开始一段新的对话</strong><p>在左侧侧栏新建或选择会话。Enter 发送，Ctrl+Enter / Shift+Enter 换行。</p></div>\`;

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
    var d = String(baseDir).replace(/[\\\\/]+$/, '');
    var useBack = d.indexOf('\\\\') !== -1;
    return d + (useBack ? '\\\\' : '/') + name;
}

/** 将「工作区绝对路径」转为 file:// URL（Windows / Unix）；分段编码以支持空格、中文等。 */
function fileUrlFromFsPath(fsPath) {
    var norm = String(fsPath || '').replace(/\\\\/g, '/');
    if (/^\\/\\//.test(norm)) return 'file:' + norm.replace(/\\//g, '/');
    var encRest = function (rel) {
        if (!rel) return '';
        return rel.split('/').map(function (seg) {
            return encodeURIComponent(seg);
        }).join('/');
    };
    if (/^[A-Za-z]:\\//.test(norm)) {
        return 'file:///' + norm.slice(0, 3) + encRest(norm.slice(3));
    }
    return 'file:///' + encRest(norm.replace(/^\\/+/, ''));
}

/**
 * 助手常写「保存至：📄 /报告.md」——以 / 开头表示相对工作区根目录的路径（非 URL）。
 */
function joinWorkDirAndRelativeSlashPath(workDir, slashPath) {
    var rel = String(slashPath || '').replace(/^\\/+/, '');
    if (!rel || !workDir) return null;
    var d = String(workDir).replace(/[\\\\/]+$/, '');
    var useBack = d.indexOf('\\\\') !== -1;
    var segs = rel.split(/\\/+/).filter(Boolean);
    if (!segs.length) return null;
    var tail = segs.join(useBack ? '\\\\' : '/');
    return d + (useBack ? '\\\\' : '/') + tail;
}

function trimTrailingPathPunct(s) {
    return String(s || '').replace(/[，。、；：）】』」\\]\\)\\.,;:!?'"」]+$/g, '').trim();
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
        _linkifyKnownExtRe = new RegExp('\\\\.(' + LINKIFY_EXT_FRAGMENT + ')\\\\b', 'i');
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
    var posixTop = /^\\/(mingw\\d*|usr|bin|etc|proc|dev|sys|opt|var|run|lib|lib64|snap|sbin|boot|srv|tmp|media|mnt)(\\/|$)/i;
    var msysDrive = /^\\/[a-z](\\/|$)/i;
    var webish = /^\\/(api|v\\d+|static|assets|node_modules)(\\/|$)/i;
    if (posixTop.test(t) || msysDrive.test(t) || webish.test(t)) return false;
    return linkifyKnownExtRegex().test(t);
}

function workspaceRelativePathNoSlashAutoLinkOk(relPath) {
    var t = linkifyNormalizePathToken(String(relPath || '').trim());
    if (!t || t.charAt(0) === '/' || /^https?:\\/\\//i.test(t)) return false;
    if (/^([A-Za-z]):[\\\\/]/.test(t) || /^\\\\\\\\/.test(t)) return false;
    if (!/[\\\\/]/.test(t)) return false;
    if (/[<>:'"|\\r\\n]/.test(t)) return false;
    if (/(^|[\\\\/])\\.{1,2}([\\\\/]|$)/.test(t)) return false;
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
    var oneLine = s.split(/\\s+/).join(' ');
    if (oneLine.length > maxLen) return oneLine.slice(0, maxLen - 1) + '\\u2026';
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
    const el = document.querySelector('.session-name[data-id="' + currentSessionId + '"]');
    const raw = el ? (el.getAttribute('data-original') || el.textContent || '') : '';
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
    return text.replace(/\\[[^\\]\\n\\r]*<br\\s*\\/?[^\\]\\n\\r]*\\]/gi, function (match) {
        var inner = match.slice(1, -1);
        var s = inner.trim();
        if (!s) return match;
        if (s.charAt(0) === '"' && s.charAt(s.length - 1) === '"') return match;
        var escaped = s.replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"');
        return '["' + escaped + '"]';
    });
}

/** 未用引号包裹的 [] 节点里出现裸 " 时同样会触发词法错误 */
function fixFlowchartBracketLabelsWithRawQuotes(text) {
    return text.replace(/\\[[^\\]\\n\\r]*"[^\\]\\n\\r]*\\]/g, function (match) {
        var inner = match.slice(1, -1);
        var s = inner.trim();
        if (!s) return match;
        if (s.charAt(0) === '"' && s.charAt(s.length - 1) === '"') return match;
        var escaped = s.replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"');
        return '["' + escaped + '"]';
    });
}

/** 去除 LLM/粘贴带来的杂讯，减少 Mermaid 10.9+ 报 Syntax error in text */
function normalizeMermaidSource(raw) {
    var t = String(raw || '')
        .replace(/^\\uFEFF/, '')
        .replace(/\\u200b|\\u200c|\\u200d/g, '')
        .replace(/\\r\\n/g, '\\n')
        .replace(/\\r/g, '\\n');
    t = t.replace(/^\\s*\`\`\`(?:mermaid)?\\s*\\n/i, '');
    t = t.replace(/\\n\\s*\`\`\`\\s*$/i, '');
    t = t.replace(/[\\u201C\\u201D\\u201E\\u00AB\\u00BB]/g, '"');
    t = t.replace(/<br\\s*\\/?>/gi, '<br/>');
    t = fixFlowchartBracketLabelsWithLineBreak(t);
    t = fixFlowchartBracketLabelsWithRawQuotes(t);
    var lines = t.split('\\n');
    if (lines.length && lines[0]) {
        lines[0] = lines[0].replace(/\\s*[\\uFF1A：]\\s*$/, '');
    }
    t = lines.map(function (line) { return line.replace(/\\s+$/g, ''); }).join('\\n').trim();
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
        if (!/\\bmermaid\\b/.test(cls)) return;
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
    if (!s || /[/\\\\:]/.test(s)) return false;
    if (!/^[^\\s<>'"]+$/.test(s)) return false;
    if (/^\\.\\.?$/.test(s)) return false;
    return linkifyKnownExtRegex().test(s);
}

function makeHrefFromAutoLinkToken(s) {
    var t = trimTrailingPathPunct(linkifyNormalizePathToken(String(s).trim()));
    if (!t) return null;
    if (/^https?:\\/\\//i.test(t)) return t;
    var m = /^([A-Za-z]):[\\\\/](.*)$/.exec(t);
    if (m) {
        var rest = (m[2] || '').replace(/\\\\/g, '/');
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
        var absRel = pathJoinBaseName(wr, t.replace(/\\\\/g, '/'));
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
    if (!t || /^https?:\\/\\//i.test(t)) return null;
    var w = (typeof window.__WORK_DIR__ === 'string') ? window.__WORK_DIR__ : '';
    var uncFlat = t.replace(/\\//g, '\\\\');
    if (/^\\\\\\\\([^\\\\]+)\\\\([^\\\\]+)/i.test(uncFlat)) {
        return uncFlat;
    }
    var win = /^([A-Za-z]):[\\\\/](.*)$/.exec(t);
    if (win) {
        var rest = (win[2] || '').replace(/\\\\/g, '/');
        var absNorm = (win[1].toUpperCase() + ':/' + rest).replace(/\\/+/g, '/');
        if (w) {
            var base = String(w).replace(/\\\\/g, '/').replace(/\\/+$/, '');
            var absLower = absNorm.toLowerCase();
            var baseLower = base.toLowerCase();
            if (absLower.length >= baseLower.length && absLower.indexOf(baseLower) === 0) {
                return absNorm.slice(base.length).replace(/^\\/+/, '');
            }
        }
        return absNorm;
    }
    if (!w) return null;
    if (t.charAt(0) === '/' && t.charAt(1) !== '/') {
        if (!workspaceRelativePathAutoLinkOk(t)) return null;
        return t.replace(/^\\/+/, '').replace(/\\\\/g, '/');
    }
    if (t === '.env' && typeof window.__APP_DOTENV_PATH__ === 'string' && window.__APP_DOTENV_PATH__) {
        return window.__APP_DOTENV_PATH__;
    }
    if (workspaceRelativePathNoSlashAutoLinkOk(t)) return t.replace(/\\\\/g, '/');
    if (isBareWorkspaceFilenameForLink(t)) return t.replace(/\\\\/g, '/');
    return null;
}

function workspaceOpenDisplayLabel(original, wsRel) {
    var rel = String(wsRel || '').replace(/\\\\/g, '/').replace(/\\/+$/, '');
    var name = rel.split('/').filter(Boolean).pop();
    if (name) return '@' + name;
    var raw = stripPathWrappingQuotes(trimTrailingPathPunct(original || ''));
    name = raw.replace(/\\\\/g, '/').replace(/\\/+$/, '').split('/').filter(Boolean).pop();
    return name ? ('@' + name) : raw;
}

function escapeRegExpLiteral(s) {
    return String(s || '').replace(/[.*+?^\${}()|[\\]\\\\]/g, '\\\\$&');
}

function quotePromptPath(p) {
    var t = stripPathWrappingQuotes(String(p || '').trim());
    if (!t) return '';
    return '"' + t.replace(/"/g, '\\\\"') + '"';
}

function getInputAbsolutePathRegex() {
    return /(["']?)([A-Za-z]:(?:\\\\|\\/)(?:(?:[^\\\\/:*?"<>|\\r\\n]+)(?:\\\\|\\/))*[^\\\\/:*?"<>|\\r\\n]+)\\1/g;
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
    var re = new RegExp('(?:\\\\s*)' + escapeRegExpLiteral(label), 'g');
    messageInput.value = text.replace(re, '').replace(/[ \\t]{2,}/g, ' ').trimStart();
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
    return /^([A-Za-z]):[\\\\/](?:(?:[^\\\\/:*?"<>|\\r\\n]+)(?:\\\\|\\/))*[^\\\\/:*?"<>|\\r\\n]+$/i.test(t);
}

function isEntireBareFilenameLinkable(raw) {
    var t = trimTrailingPathPunct(linkifyNormalizePathToken(String(raw || '').trim()));
    return isBareWorkspaceFilenameForLink(t);
}

/** 行内 code 内整段为 \`/工作区相对/路径.ext\` 时亦允许链转（否则反引号路径永不可点） */
function isEntireWorkspaceSlashPathLinkable(raw) {
    var t = trimTrailingPathPunct(linkifyNormalizePathToken(String(raw || '').trim()));
    return workspaceRelativePathAutoLinkOk(t);
}

function isEntireWorkspaceRelativePathLinkable(raw) {
    var t = trimTrailingPathPunct(linkifyNormalizePathToken(String(raw || '').trim()));
    return workspaceRelativePathNoSlashAutoLinkOk(t);
}

/** 行内 code 内整段为 UNC \\\\server\\share\\... 时允许「本机打开」链转 */
function isEntireTextNodeUncPath(raw) {
    var t = trimTrailingPathPunct(linkifyNormalizePathToken(String(raw || '').trim()));
    if (!t) return false;
    var u = t.replace(/\\//g, '\\\\');
    return /^\\\\\\\\[^\\\\]+\\\\[^\\\\]+(?:\\\\[^\\\\]*)*$/i.test(u);
}

var _assistMsgLinkifyRe = null;
function getAssistMsgLinkifyRegex() {
    if (!_assistMsgLinkifyRe) {
        // 「/路径」前仅排除 ASCII 字母，避免 2023/文件、中文后接 / 等无法匹配；仍可抑制 ARPU/DOU（U 为字母）
        _assistMsgLinkifyRe = new RegExp(
            '(https?:\\\\/\\\\/[^\\\\s<>\\'"]+|' +
            '\\\\\\\\\\\\\\\\(?:(?:[^\\\\\\\\\\\\/:*?"<>|\\\\r\\\\n]+)\\\\\\\\)+(?:[^\\\\\\\\\\\\/:*?"<>|\\\\r\\\\n]+)|' +
            '[A-Za-z]:(?:\\\\\\\\|\\\\/)(?:(?:[^\\\\\\\\/:*?"<>|\\\\r\\\\n]+)(?:\\\\\\\\|\\\\/))*[^\\\\\\\\/:*?"<>|\\\\r\\\\n]+|' +
            '(?<![A-Za-z])\\\\/(?![\\\\s\\\\/])[^\\\\s<>\\'"]+|' +
            '(?<![A-Za-z0-9./\\\\\\\\])(?:[^\\\\s<>\\'"/\\\\\\\\:]+(?:[\\\\\\\\/][^\\\\s<>\\'"/\\\\\\\\:]+)+\\\\.(' + LINKIFY_EXT_FRAGMENT + ')\\\\b)|' +
            '(?<![A-Za-z0-9./\\\\\\\\])([^\\\\s<>\\'"/\\\\\\\\:]+?\\\\.(' + LINKIFY_EXT_FRAGMENT + ')\\\\b))',
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
        if (!nv || (!/https?:\\/\\/|[A-Za-z]:[\\\\/]|\\/\\S/.test(nvNorm) && !nvNorm.startsWith('\\\\\\\\') && !linkifyKnownExtRegex().test(nvNorm))) continue;
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
        displayText = toolName + '(' + argsPreview + '\\n生成中...';
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
    return cmd + '\\n执行中...';
}

function formatToolDoneLine(tool, args, result, commandPreview) {
    return formatToolCommandLine(tool, args, commandPreview) + '\\n执行结果\\n' + String(result != null ? result : '');
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
    var lines = text.split('\\n');
    var start = 0;
    var end = lines.length;
    while (start < end && lines[start].trim() === '') start++;
    while (end > start && lines[end - 1].trim() === '') end--;
    if (start >= end) return '';
    return lines.slice(start, end).join('\\n');
}

function truncateLogTextForUi(raw) {
    const text = (raw == null) ? '' : String(raw);
    if (!text) return text;
    const lines = text.split('\\n');
    if (lines.length > LOG_TRUNCATE_HEAD_LINES + LOG_TRUNCATE_TAIL_LINES) {
        const head = lines.slice(0, LOG_TRUNCATE_HEAD_LINES).join('\\n');
        const tail = lines.slice(-LOG_TRUNCATE_TAIL_LINES).join('\\n');
        const omitted = lines.length - LOG_TRUNCATE_HEAD_LINES - LOG_TRUNCATE_TAIL_LINES;
        return head + '\\n\\n... [中间省略 ' + omitted + ' 行] ...\\n\\n' + tail;
    }
    if (text.length > LOG_TRUNCATE_HEAD_CHARS + LOG_TRUNCATE_TAIL_CHARS) {
        const head = text.slice(0, LOG_TRUNCATE_HEAD_CHARS);
        const tail = text.slice(-LOG_TRUNCATE_TAIL_CHARS);
        const omitted = text.length - LOG_TRUNCATE_HEAD_CHARS - LOG_TRUNCATE_TAIL_CHARS;
        return head + '\\n\\n... [中间省略约 ' + omitted + ' 字符] ...\\n\\n' + tail;
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
        errHint.textContent = '需要帮助请联系@wushuge 00612259';
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
        var lineCount = rawStr.split('\\n').length;
        if (lineCount > 10) {
            wrap.classList.add('has-turn-process');
            div.classList.add('is-collapsible');
            // 摘要
            var sum = document.createElement('div');
            sum.className = 'user-msg-summary';
            sum.textContent = rawStr.split('\\n').slice(0, 10).join('\\n') + '\\n...';
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
        merged = prevTxt.slice(0, bodyOffset).replace(/\\s+$/, '') + '\\n\\n' + text;
    } else if (prevTxt.trim()) {
        merged = prevTxt.trim() + '\\n\\n' + text;
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
            sc.textContent = head + '\\n\\n';
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
        prev.textContent = truncateLogTextForUi(prevTxt ? (prevTxt + '\\n' + line) : line);
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
`,E=`var subagentCardSyncTimer = null;
var subagentCardEventCount = Object.create(null);
var subagentPanelOpen = false;
var subagentPanelBound = false;
var subagentDockExpanded = false;

var subagentContinueInFlight = false;
var subagentContinueBannerTimer = null;
var subagentContinueDismissedForSession = Object.create(null);
var subagentPanelRefreshSeq = 0;
var subagentBodyHtmlCache = Object.create(null);
var subagentContextFetchInFlight = Object.create(null);
var subagentTreeRefreshTimer = null;
var subagentTreeRefreshTarget = null;
var subagentTreeRefreshInflight = null;
var subagentTreeRefreshInflightSid = null;
var subagentTreeRefreshQueued = false;
var subagentCardViewportObserver = null;
var subagentCardLoadQueue = [];
var subagentCardLoadInflight = 0;
var subagentCardLoadQueued = Object.create(null);
var SUBAGENT_BODY_LOAD_CONCURRENCY = 2;
var SUBAGENT_DETAIL_RENDER_BATCH = 8;
var SUBAGENT_HISTORY_TURNS_PER_PAGE = 3;
var subagentStatsRefreshRaf = 0;
var subagentStatsPending = new Set();

function hideSubagentContinueBanner() {
    var banner = document.getElementById('subagent-continue-banner');
    if (!banner) return;
    var mode = banner && banner.dataset ? String(banner.dataset.continueMode || '') : '';
    banner.classList.remove('is-on');
}

function dismissSubagentContinueBanner(sessionId) {
    var sid = sessionId || currentSessionId;
    if (sid) subagentContinueDismissedForSession[sid] = true;
    hideSubagentContinueBanner();
    if (sid) {
        fetch('/sessions/' + encodeURIComponent(sid) + '/continue-subagents/dismiss', { method: 'POST' })
            .catch(function () { /* ignore */ });
    }
}

function showSubagentContinueBanner(pendingCount) {
    var banner = document.getElementById('subagent-continue-banner');
    if (!banner) return;
    var n = Math.max(1, parseInt(String(pendingCount), 10) || 1);
    var msg = banner.querySelector('.subagent-continue-banner-msg');
    if (msg) {
        msg.textContent = n + ' 个子任务已完成，点击继续让主 Agent 综合子任务结果（不会自动续跑）。';
    }
    banner.classList.add('is-on');
}

async function fetchSubagentContinueState(sessionId) {
    if (!sessionId) return { pending: 0, running: 0, can_continue: false };
    try {
        var r = await fetch('/sessions/' + encodeURIComponent(sessionId) + '?include_subagents=true');
        if (!r.ok) return { pending: 0, running: 0, can_continue: false };
        var j = await r.json();
        return {
            pending: Number(j.subagent_pending_continue || 0),
            running: Number(j.subagent_running || 0),
            can_continue: !!j.subagent_can_continue,
        };
    } catch (e) {
        return { pending: 0, running: 0, can_continue: false };
    }
}

function updateSubagentContinueBanner(sessionId) {
    if (!sessionId || sessionId !== currentSessionId || replayingMessages) {
        hideSubagentContinueBanner();
        return;
    }
    if (subagentContinueDismissedForSession[sessionId]) {
        hideSubagentContinueBanner();
        return;
    }
    if (subagentContinueBannerTimer) clearTimeout(subagentContinueBannerTimer);
    subagentContinueBannerTimer = setTimeout(function () {
        subagentContinueBannerTimer = null;
        void (async function () {
            var st = await fetchSubagentContinueState(sessionId);
            if (sessionId !== currentSessionId) return;
            if (st.can_continue && st.pending > 0 && st.running === 0
                && !isSessionRunning(sessionId) && !subagentContinueInFlight) {
                showSubagentContinueBanner(st.pending);
            } else {
                hideSubagentContinueBanner();
            }
        })();
    }, 280);
}

async function tryMarkSessionUnreadComplete(sessionId) {
    if (!sessionId || sessionId === currentSessionId) return;
    try {
        var r = await fetch('/sessions/' + encodeURIComponent(sessionId) + '?include_subagents=true');
        if (!r.ok) return;
        var j = await r.json();
        if (j.stream_active || Number(j.subagent_running || 0) > 0) return;
        sessionUnreadComplete.add(sessionId);
        persistSessionUnread();
        syncSessionListIndicatorClasses();
    } catch (e) { /* ignore */ }
}

function shouldStreamSubagentSummaryDom(card) {
    return !!(subagentPanelOpen && card);
}

function shouldStreamSubagentProcessDom(card) {
    if (!card || !subagentPanelOpen) return false;
    return card.classList.contains('is-expanded');
}

function shouldStreamSubagentCardDom(card) {
    return shouldStreamSubagentProcessDom(card);
}

function subagentBodyIsLoaded(body) {
    return !!(body && body.dataset.loaded === '1' && body.dataset.stashed !== '1'
        && body.innerHTML.trim() && !body.querySelector('.subagent-detail-empty')
        && !body.querySelector('.subagent-card-summary'));
}

function buildSubagentCardSummaryHtml(previewText, muted) {
    var t = formatSubagentSummaryText(previewText);
    if (!t) {
        return '<div class="subagent-card-summary subagent-card-summary--muted">'
            + escapeHtml(muted ? String(muted) : '展开查看执行过程') + '</div>';
    }
    if (t.length > 1200) t = t.slice(0, 1199) + '\\u2026';
    return '<div class="subagent-card-summary">' + escapeHtml(t) + '</div>';
}

function subagentMoreDotsHtml() {
    return '<span class="session-more-dots" aria-hidden="true"><span></span><span></span><span></span></span>';
}

function formatSubagentSummaryText(text) {
    var t = String(text || '').replace(/\\r\\n/g, '\\n').trim();
    if (!t) return '';
    t = t.replace(/\`\`\`[\\s\\S]*?\`\`\`/g, function (m) {
        return m.replace(/^\`\`\`[^\\n]*\\n?/, '').replace(/\\n?\`\`\`$/, '');
    });
    t = t.replace(/^\\s{0,3}#{1,6}\\s+/gm, '');
    t = t.replace(/^\\s{0,3}[-*_]{3,}\\s*$/gm, '');
    t = t.replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g, '$1');
    t = t.replace(/\`([^\`]+)\`/g, '$1');
    t = t.replace(/(\\*\\*|__)(.*?)\\1/g, '$2');
    t = t.replace(/(\\*|_)(.*?)\\1/g, '$2');
    t = t.replace(/^\\s{0,3}>\\s?/gm, '');
    t = t.replace(/^\\s{0,3}[-*+]\\s+/gm, '• ');
    t = t.replace(/\\n{3,}/g, '\\n\\n');
    return t.trim();
}

function updateSubagentCardSummaryOnly(card, previewText) {
    if (!card) return;
    var body = card.querySelector('.subagent-card-body');
    if (!body) return;
    var p = previewText != null ? String(previewText) : String(card.dataset.resultPreview || '');
    card.dataset.resultPreview = p;
    if (subagentBodyIsLoaded(body)) return;
}

function stashSubagentCardBodyForCollapse(card) {
    if (!card) return;
    var body = card.querySelector('.subagent-card-body');
    if (!body || body.dataset.stashed === '1') return;
    if (subagentBodyIsLoaded(body) && body.dataset.finalOnly !== '1') {
        var aid = card.getAttribute('data-agent-id');
        if (currentSessionId && aid) {
            var hasCleanCache = body.dataset.cacheClean === '1' && !!readSubagentBodyCache(currentSessionId, aid);
            if (!hasCleanCache) {
                rememberSubagentBodyCache(currentSessionId, aid, body.innerHTML);
                body.dataset.cacheClean = '1';
            }
        }
    }
    body.dataset.stashed = '1';
    delete body.dataset.renderToken;
    delete body.dataset.rendering;
    body.innerHTML = '';
    delete body.dataset.loaded;
    delete body.dataset.streamReady;
    delete body._subagentStreamCtx;
}

function restoreSubagentCardBodyFromStash(card, sessionId) {
    if (!card) return false;
    var body = card.querySelector('.subagent-card-body');
    var aid = card.getAttribute('data-agent-id');
    if (!body) return false;
    var cached = readSubagentBodyCache(sessionId, aid);
    if (cached && isSubagentBodyCacheComplete(cached)) {
        body.innerHTML = cached;
        body.dataset.loaded = '1';
        body.dataset.cacheClean = '1';
        delete body.dataset.stashed;
        rebindSubagentCardBody(body, card, aid);
        return true;
    }
    if (body.dataset.stashed === '1') {
        delete body.dataset.stashed;
        body.innerHTML = '';
    }
    return false;
}

function scheduleSubagentDetailWork(fn) {
    setTimeout(fn, 0);
}

function stashSubagentInactiveBodies(grid, keepCard) {
    if (!grid) return;
    grid.querySelectorAll('.subagent-grid-card').forEach(function (card) {
        if (keepCard && card === keepCard) return;
        if (card.classList.contains('is-expanded')) return;
        stashSubagentCardBodyForCollapse(card);
    });
}

function scheduleSubagentCardStats(card) {
    if (!card) return;
    /* 不可见的 card（折叠且未进入视口）不算 stats：算了用户也看不见，省一次 querySelectorAll。 */
    if (subagentPanelOpen
        && !card.classList.contains('is-expanded')
        && card.dataset.viewportVisible !== '1') return;
    subagentStatsPending.add(card);
    if (subagentStatsRefreshRaf) return;
    /* RAF 在多 subagent 流量高时每帧都触发；改为 timeout 节流 250ms，合并连续 delta 的统计。 */
    subagentStatsRefreshRaf = setTimeout(function () {
        subagentStatsRefreshRaf = 0;
        var cards = Array.from(subagentStatsPending);
        subagentStatsPending.clear();
        cards.forEach(refreshSubagentCardStats);
    }, 250);
}

function getSubagentIncrementalSyncDelay(runningCount) {
    /* SSE 已在为父会话推子 agent 增量，轮询仅作兜底；大幅退避以让出主线程。 */
    if (isSessionRunning(currentSessionId)) return 8000;
    if (runningCount > 20) return 6000;
    if (runningCount > 10) return 4000;
    if (runningCount > 5) return 3000;
    return 2200;
}

function runTasksWithConcurrency(items, limit, worker) {
    if (!items || !items.length) return Promise.resolve();
    var idx = 0;
    var n = Math.max(1, Math.min(limit || 1, items.length));
    function next() {
        if (idx >= items.length) return Promise.resolve();
        var cur = idx++;
        return Promise.resolve(worker(items[cur], cur)).then(next);
    }
    var starters = [];
    for (var i = 0; i < n; i += 1) starters.push(next());
    return Promise.all(starters);
}

function trackSubagentStreamEventLightweight(card, agentId, event, eventIndex) {
    if (!card || !agentId || !event) return;
    var t = event.type;
    if (typeof eventIndex === 'number' && eventIndex >= 0) {
        subagentCardEventCount[agentId] = Math.max(subagentCardEventCount[agentId] || 0, eventIndex + 1);
    } else if (!event.ephemeral) {
        subagentCardEventCount[agentId] = (subagentCardEventCount[agentId] || 0) + 1;
    }
    if (t === 'context_tokens') {
        card.dataset.procCtxEstimated = String(event.estimated);
        card.dataset.procCtxThreshold = String(event.threshold);
    } else if (t === 'process_metrics') {
        applySubagentProcessMetricsToCard(card, event);
    } else if (t === 'cache_stats') {
        if (event.cache_hit != null) card.dataset.procCacheHit = String(Math.max(0, Math.floor(Number(event.cache_hit))));
        if (event.cache_miss != null) card.dataset.procCacheMiss = String(Math.max(0, Math.floor(Number(event.cache_miss))));
        if (event.hit_rate != null) card.dataset.procCacheRate = String(Math.max(0, Number(event.hit_rate)));
        if (event.model != null) card.dataset.procCacheModel = String(event.model);
    }
    if (event.react_iter != null) bumpAggregateMaxReactIter(card, event.react_iter);
    scheduleSubagentCardStats(card);
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

function scheduleRefreshSubagentTreePanel(sessionId, delayMs) {
    if (!sessionId || replayingMessages) return;
    subagentTreeRefreshTarget = sessionId;
    if (subagentTreeRefreshTimer) clearTimeout(subagentTreeRefreshTimer);
    subagentTreeRefreshTimer = setTimeout(function () {
        subagentTreeRefreshTimer = null;
        var sid = subagentTreeRefreshTarget;
        subagentTreeRefreshTarget = null;
        if (sid && sid === currentSessionId) void refreshSubagentTreePanel(sid);
    }, delayMs == null ? 150 : delayMs);
}

function cancelScheduledSubagentTreeRefresh() {
    if (subagentTreeRefreshTimer) {
        clearTimeout(subagentTreeRefreshTimer);
        subagentTreeRefreshTimer = null;
    }
    subagentTreeRefreshTarget = null;
    subagentTreeRefreshQueued = false;
}

function subagentBodyCacheKey(sessionId, agentId) {
    return String(sessionId || '') + ':' + String(agentId || '');
}

function isSubagentDetailPendingHtml(html) {
    return !html || html.indexOf('加载中') >= 0;
}

function forgetSubagentBodyCache(sessionId, agentId) {
    if (sessionId && agentId) {
        delete subagentBodyHtmlCache[subagentBodyCacheKey(sessionId, agentId)];
        return;
    }
    if (sessionId) {
        var prefix = String(sessionId) + ':';
        Object.keys(subagentBodyHtmlCache).forEach(function (k) {
            if (k.indexOf(prefix) === 0) delete subagentBodyHtmlCache[k];
        });
    }
}

function isSubagentBodyCacheComplete(html) {
    if (!html || isSubagentDetailPendingHtml(html)) return false;
    if (html.indexOf('subagent-detail-empty') >= 0) return false;
    if (html.indexOf('subagent-turn-process') < 0) {
        return html.indexOf('subagent-turn') >= 0 || html.indexOf('msg-wrap--assistant') >= 0;
    }
    return html.indexOf('msg-wrap--user') >= 0;
}

function rememberSubagentBodyCache(sessionId, agentId, html) {
    if (!sessionId || !agentId || !html || !isSubagentBodyCacheComplete(html)) return;
    subagentBodyHtmlCache[subagentBodyCacheKey(sessionId, agentId)] = html;
}

function readSubagentBodyCache(sessionId, agentId) {
    return subagentBodyHtmlCache[subagentBodyCacheKey(sessionId, agentId)] || '';
}

function shouldLoadSubagentCardBodies() {
    return !!subagentPanelOpen;
}

function onSubagentDockWheel(e) {
    var dock = document.getElementById('subagent-dock');
    if (!dock || dock.classList.contains('hidden') || !dock.contains(e.target)) return;
    var dy = e.deltaY;
    var eps = 2;
    var node = e.target;
    while (node && node !== dock) {
        if (node.nodeType === 1) {
            var style = window.getComputedStyle(node);
            var scrollable = node.classList && (
                node.classList.contains('subagent-grid') ||
                node.classList.contains('process-aggregate-body') ||
                node.classList.contains('process-aggregate-brief') ||
                node.classList.contains('feed-chunk-scroller')
            );
            if (scrollable || /(auto|scroll|overlay)/.test(style.overflowY)) {
                if (node.scrollHeight > node.clientHeight + eps) {
                    var st = node.scrollTop;
                    var max = node.scrollHeight - node.clientHeight;
                    if (dy < 0 && st > eps) {
                        e.stopPropagation();
                        return;
                    }
                    if (dy > 0 && st < max - eps) {
                        e.stopPropagation();
                        return;
                    }
                }
            }
        }
        node = node.parentElement;
    }
    var grid = dock.querySelector('.subagent-grid');
    if (grid && grid.scrollHeight > grid.clientHeight + eps) {
        var gst = grid.scrollTop;
        var gmax = grid.scrollHeight - grid.clientHeight;
        var next = Math.max(0, Math.min(gmax, gst + dy));
        if (next !== gst) grid.scrollTop = next;
    }
    e.preventDefault();
    e.stopPropagation();
}

function syncSubagentDockResizeUi() {
    var dock = document.getElementById('subagent-dock');
    var resizeBtn = document.getElementById('subagent-dock-resize');
    if (!dock || !resizeBtn) return;
    dock.classList.toggle('is-expanded', subagentDockExpanded);
    resizeBtn.setAttribute('aria-label', subagentDockExpanded ? '收起 Subagent 面板' : '展开 Subagent 面板');
}

function toggleSubagentDockExpand() {
    var grid = document.getElementById('subagent-grid');
    if (grid) {
        grid.classList.add('is-resizing');
        stashSubagentInactiveBodies(grid, grid.querySelector('.subagent-grid-card.is-expanded'));
    }
    subagentDockExpanded = !subagentDockExpanded;
    syncSubagentDockResizeUi();
    if (grid) {
        requestAnimationFrame(function () {
            grid.classList.remove('is-resizing');
            loadVisibleSubagentCardBodies(grid, currentSessionId);
        });
    }
}

function bindSubagentPanelOnce() {
    if (subagentPanelBound) return;
    subagentPanelBound = true;
    var dock = document.getElementById('subagent-dock');
    var panel = dock && dock.querySelector('.subagent-panel');
    if (dock) dock.addEventListener('wheel', onSubagentDockWheel, { passive: false, capture: true });
    if (panel) panel.addEventListener('wheel', onSubagentDockWheel, { passive: false, capture: true });
    var btn = document.getElementById('subagent-toggle-btn');
    if (btn) {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            if (subagentPanelOpen) closeSubagentPanel();
            else openSubagentPanel();
        });
    }
    var resizeBtn = document.getElementById('subagent-dock-resize');
    if (resizeBtn && !resizeBtn.dataset.subagentBound) {
        resizeBtn.dataset.subagentBound = '1';
        resizeBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            toggleSubagentDockExpand();
        });
    }
    document.addEventListener('mousedown', function (e) {
        if (!subagentPanelOpen) return;
        if (!(e.target && e.target.closest && e.target.closest('.subagent-card-menu'))) {
            document.querySelectorAll('.subagent-card-menu.is-open').forEach(function (menu) {
                menu.classList.remove('is-open');
                var mb = menu.querySelector('.subagent-card-menu-btn');
                if (mb) mb.setAttribute('aria-expanded', 'false');
            });
        }
        var dock = document.getElementById('subagent-dock');
        var btnEl = document.getElementById('subagent-toggle-btn');
        if (dock && dock.contains(e.target)) return;
        if (btnEl && btnEl.contains(e.target)) return;
        closeSubagentPanel();
    });
}

function openSubagentPanel() {
    var dock = document.getElementById('subagent-dock');
    var btn = document.getElementById('subagent-toggle-btn');
    if (!dock || (btn && btn.classList.contains('hidden'))) return;
    dock.classList.remove('hidden');
    subagentPanelOpen = true;
    syncSubagentDockResizeUi();
    if (btn) {
        btn.classList.add('is-active');
        btn.setAttribute('aria-expanded', 'true');
    }
    var grid = document.getElementById('subagent-grid');
    if (grid) {
        ensureSubagentCardViewportObserver(grid);
        stashSubagentInactiveBodies(grid, grid.querySelector('.subagent-grid-card.is-expanded'));
        requestAnimationFrame(function () {
            if (subagentPanelOpen) loadVisibleSubagentCardBodies(grid, currentSessionId);
        });
        if (countRunningSubagentCards() > 0) scheduleSubagentIncrementalSync();
    }
}

function resetSubagentPanelForSession() {
    cancelScheduledSubagentTreeRefresh();
    disconnectSubagentCardViewportObserver();
    if (subagentContinueBannerTimer) {
        clearTimeout(subagentContinueBannerTimer);
        subagentContinueBannerTimer = null;
    }
    hideSubagentContinueBanner();
    subagentPanelRefreshSeq += 1;
    subagentCardEventCount = Object.create(null);
    closeSubagentPanel();
    stopSubagentIncrementalSync();
    var grid = document.getElementById('subagent-grid');
    if (grid) {
        grid.innerHTML = '';
        delete grid.dataset.sessionId;
        grid.classList.remove('subagent-grid--expanded');
    }
    var toggleBtn = document.getElementById('subagent-toggle-btn');
    var toggleBadge = document.getElementById('subagent-toggle-badge');
    if (toggleBtn) toggleBtn.classList.add('hidden');
    if (toggleBadge) toggleBadge.textContent = '';
}

function closeSubagentPanel() {
    var dock = document.getElementById('subagent-dock');
    var btn = document.getElementById('subagent-toggle-btn');
    if (dock) {
        var grid = document.getElementById('subagent-grid');
        if (grid) stashSubagentInactiveBodies(grid, null);
        dock.classList.add('hidden');
    }
    subagentPanelOpen = false;
    subagentDockExpanded = false;
    syncSubagentDockResizeUi();
    if (btn) {
        btn.classList.remove('is-active');
        btn.setAttribute('aria-expanded', 'false');
    }
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

function stopSubagentIncrementalSync() {
    if (subagentCardSyncTimer) {
        clearTimeout(subagentCardSyncTimer);
        subagentCardSyncTimer = null;
    }
}

function scheduleSubagentIncrementalSync() {
    if (subagentCardSyncTimer) return;
    /* 父会话 SSE 正在推流时，subagent 增量已实时到达，延后到 4s 再兜底；
       否则用 1200ms 让 finish 事件后的状态尽快同步。 */
    var delay = isSessionRunning(currentSessionId) ? 4000 : 1200;
    subagentCardSyncTimer = setTimeout(function () {
        subagentCardSyncTimer = null;
        runSubagentIncrementalSync();
    }, delay);
}

function countRunningSubagentCards() {
    var n = 0;
    document.querySelectorAll('.subagent-grid-card .subagent-status-dot.is-running').forEach(function () { n += 1; });
    return n;
}

async function runSubagentIncrementalSync() {
    var grid = document.getElementById('subagent-grid');
    if (!grid || !currentSessionId || !subagentPanelOpen) {
        stopSubagentIncrementalSync();
        return;
    }
    /* 页面不可见时不轮询，让出 CPU。回到前台后会被 visibilitychange 重新调度。 */
    if (document.visibilityState !== 'visible') {
        subagentCardSyncTimer = setTimeout(function () {
            subagentCardSyncTimer = null;
            runSubagentIncrementalSync();
        }, 5000);
        return;
    }
    var tasks = [];
    grid.querySelectorAll('.subagent-grid-card').forEach(function (card) {
        var dot = card.querySelector('.subagent-status-dot.is-running');
        if (!dot) return;
        var aid = card.getAttribute('data-agent-id');
        if (!aid) return;
        tasks.push({ aid: aid, card: card });
    });
    if (tasks.length) {
        /* 并发降至 1，避免一次性 N×2 个 HTTP 请求与 N 次 DOM 重渲染并发占用主线程。 */
        await runTasksWithConcurrency(tasks, 1, function (t) {
            return incrementalSyncSubagentCard(t.aid, t.card);
        });
    }
    var runningN = countRunningSubagentCards();
    if (runningN === 0 && currentSessionId && !replayingMessages) {
        updateSubagentContinueBanner(currentSessionId);
        void tryMarkSessionUnreadComplete(currentSessionId);
    }
    if (runningN > 0 && subagentPanelOpen) {
        subagentCardSyncTimer = setTimeout(function () {
            subagentCardSyncTimer = null;
            runSubagentIncrementalSync();
        }, getSubagentIncrementalSyncDelay(runningN));
    }
}

function getSubagentCardStreamCtx(body, card, agentId) {
    if (!body) return null;
    if (body._subagentStreamCtx && body._subagentStreamCtx._subagentBody === body) return body._subagentStreamCtx;
    var ctx = {
        _subagentBody: body,
        currentProcessGroup: card || null,
        stream: null,
        lastUserEventIndex: null,
        progressStream: {},
        progressScrollers: {},
        keyContextStreamFilter: { phase: 'seek', carry: '' },
        llm: newLlmState(),
        currentTurn: null,
        _subagentTurnProcess: null,
        _subagentTurnFinalSlot: null
    };
    body._subagentStreamCtx = ctx;
    return ctx;
}

function resetSubagentTurnStreamState(ctx) {
    if (!ctx) return;
    resetLlmState(ctx);
    finalizeProgressStreamChunks(ctx);
    ctx.currentTurn = null;
    ctx._subagentTurnProcess = null;
    ctx._subagentTurnFinalSlot = null;
}

function sealSubagentTurn(ctx) {
    if (!ctx || !ctx.currentTurn) return;
    resetSubagentTurnStreamState(ctx);
}

function markSubagentTurnHasProcess(turn) {
    if (!turn) return;
    var processEl = turn.querySelector('.subagent-turn-process');
    var userWrap = turn.querySelector('.msg-wrap--user');
    var hasDeferred = !!(turn._deferredProcessEvents && turn._deferredProcessEvents.length) || turn.dataset.processDeferred === '1';
    if ((processEl && processEl.children.length) || hasDeferred) {
        if (userWrap) userWrap.classList.add('has-turn-process');
    }
}

function shouldSkipSubagentProcessEvent(event) {
    if (!event || typeof event !== 'object') return true;
    var t = String(event.type || '');
    var c = String(event.content || '').trim();
    if (t === 'status' && (!c || c === 'New Agent Loop Start' || c === 'Loop finished' || c === 'Subagent Continuation Start')) return true;
    if ((t === 'warning' || t === 'error') && !c) return true;
    return false;
}

function syncSubagentTurnProcessFlags(root) {
    if (!root) return;
    root.querySelectorAll('.subagent-turn').forEach(function (turn) {
        markSubagentTurnHasProcess(turn);
    });
}

function bindSubagentCardBodyInteractions(body) {
    if (!body) return;
    bindSubagentCardBodyScrollFollow(body);
    if (body.dataset.subagentBodyBound) return;
    body.dataset.subagentBodyBound = '1';
    body.addEventListener('click', function (e) {
        var userWrap = e.target.closest('.msg-wrap--user');
        if (!userWrap || !body.contains(userWrap)) return;
        if (!userWrap.classList.contains('has-turn-process')) return;
        var turn = userWrap.closest('.subagent-turn');
        if (!turn) return;
        e.preventDefault();
        e.stopPropagation();
        toggleSubagentTurnProcess(turn, body, userWrap);
    });
}

function bindSubagentTurnUserToggle(turn, userWrap) {
    /* 统一由 bindSubagentCardBodyInteractions 委托处理，避免重复 toggle */
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
        var lineCount = rawStr.split('\\n').length;
        if (lineCount > 10) {
            wrap.classList.add('has-turn-process');
            div.classList.add('is-collapsible');
            var sum = document.createElement('div');
            sum.className = 'user-msg-summary';
            sum.textContent = rawStr.split('\\n').slice(0, 10).join('\\n') + '\\n...';
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
    var userRaw = userContent == null ? '' : String(userContent);
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

function dispatchSubagentCardEvent(ctx, card, event, eventIndex, agentId) {
    if (!event || typeof event !== 'object') return;
    if (shouldSkipSubagentProcessEvent(event)) return;
    var t = event.type;
    if (t === 'subagent_start' || t === 'subagent_finish') return;
    if (t === 'user') {
        openSubagentTurn(ctx, event.content || '', eventIndex);
        if (typeof eventIndex === 'number') ctx.lastUserEventIndex = eventIndex;
        return;
    }
    if (t === 'final') {
        appendSubagentFinalToTurn(ctx, event.content || '', eventIndex);
        if (ctx.currentTurn) {
            ctx._subagentTurnProcess = ctx.currentTurn.querySelector('.subagent-turn-process');
            ctx._subagentTurnFinalSlot = ctx.currentTurn.querySelector('.subagent-turn-final-slot');
        }
        resetLlmState(ctx);
        finalizeProgressStreamChunks(ctx);
        return;
    }
    ensureSubagentTurnForProcess(ctx, eventIndex);
    if (shouldDeferSubagentProcessDom(ctx)) {
        deferSubagentProcessEvent(ctx.currentTurn, event, eventIndex);
        markSubagentTurnHasProcess(ctx.currentTurn);
        return;
    }
    renderEvent(ctx, event, eventIndex, agentId);
    markSubagentTurnHasProcess(ctx.currentTurn);
}


function restoreSubagentTurnCtxFromBody(ctx, body) {
    if (!ctx || !body) return;
    var turns = body.querySelectorAll('.subagent-turn');
    if (!turns.length) {
        resetSubagentTurnStreamState(ctx);
        return;
    }
    var last = turns[turns.length - 1];
    var finalSlot = last.querySelector('.subagent-turn-final-slot');
    var hasFinal = finalSlot && finalSlot.querySelector('.msg-wrap--assistant');
    if (hasFinal) {
        resetSubagentTurnStreamState(ctx);
        return;
    }
    ctx.currentTurn = last;
    ctx._subagentTurnProcess = last.querySelector('.subagent-turn-process');
    ctx._subagentTurnFinalSlot = finalSlot;
}

function rebindSubagentCardBody(body, card, agentId) {
    if (!body) return;
    bindSubagentCardBodyInteractions(body);
    body.querySelectorAll('.subagent-turn').forEach(function (turn) {
        markSubagentTurnHasProcess(turn);
    });
    bindSubagentCardFeedInteractionsLightly(body);
    var ctx = body._subagentStreamCtx || (card ? getSubagentCardStreamCtx(body, card, agentId) : null);
    if (ctx) restoreSubagentTurnCtxFromBody(ctx, body);
    if (card) {
        refreshSubagentCardStats(card);
    }
}

function bindSubagentCardFeedInteractionsLightly(root) {
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll('.feed-chunk').forEach(function (ch, idx) {
        bindFeedChunkInteraction(ch);
        var sc = ch.querySelector('.feed-chunk-scroller');
        if (sc) bindFeedChunkScrollChain(sc);
        if (idx < 24) scheduleFeedChunkOverflowRefresh(ch);
    });
}

function finalizeSubagentCardStream(agentId, card) {
    if (!card) return;
    var body = card.querySelector('.subagent-card-body');
    if (!body) return;
    var ctx = getSubagentCardStreamCtx(body, card, agentId);
    finalizeLlmStreamChunks(ctx);
    finalizeProgressStreamChunks(ctx);
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
        subagentCardEventCount = Object.create(null);
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
            delete subagentCardEventCount[id];
            delete subagentCardLoadQueued[id];
            card.remove();
        }
    });
    if (!shouldLoadSubagentCardBodies()) {
        bindSubagentGridActions(grid, sessionId);
        return;
    }
    bindSubagentGridActions(grid, sessionId);
    loadVisibleSubagentCardBodies(grid, sessionId);
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

function ensureSubagentCardStreamReady(card, aid) {
    if (!card || !aid) return;
    var body = card.querySelector('.subagent-card-body');
    if (!body || body.dataset.loading === '1') return;
    if (!card.dataset.procStartedAt) card.dataset.procStartedAt = String(procNow());
    if (body.querySelector('.subagent-detail-empty')) body.innerHTML = '';
    body.dataset.streamReady = '1';
    if (!body.dataset.loaded) body.dataset.loaded = '1';
    delete body.dataset.loading;
    bindSubagentCardBodyInteractions(body);
    getSubagentCardStreamCtx(body, card, aid);
}

function upsertSubagentCardFromStartEvent(event) {
    /* 历史回放阶段：一律不亮按钮 / 不写 grid，避免把别会话遗留的 subagent_start 闪出来；
       真实状态由稍后的 refreshSubagentTreePanel(/sessions/{sid}/subagents) 单一来源决定。 */
    if (replayingMessages) return null;
    var grid = document.getElementById('subagent-grid');
    if (!grid) return null;
    if (currentSessionId && grid.dataset.sessionId && grid.dataset.sessionId !== currentSessionId) {
        return null;
    }
    if (currentSessionId) grid.dataset.sessionId = currentSessionId;
    var aid = String(event.agent_id || event.run_id || '');
    if (!aid) return null;
    var node = {
        id: aid,
        running: !event.background ? true : true,
        description: event.description || aid.slice(0, 8),
        subagent_type: event.subagent_type || 'subagent',
        background: !!event.background,
    };
    var card = grid.querySelector('.subagent-grid-card[data-agent-id="' + aid + '"]');
    if (!card) card = appendSubagentGridCardFromNode(grid, node);
    else applySubagentNodeMetaToCard(card, node);
    if (currentSessionId) bindSubagentGridActions(grid, currentSessionId);
    var toggleBtn = document.getElementById('subagent-toggle-btn');
    var toggleBadge = document.getElementById('subagent-toggle-badge');
    if (toggleBtn) {
        toggleBtn.classList.remove('hidden');
        toggleBtn.classList.add('is-running');
    }
    var cardCount = grid.querySelectorAll('.subagent-grid-card').length;
    var runCount = grid.querySelectorAll('.subagent-status-dot.is-running').length;
    if (toggleBadge) toggleBadge.textContent = String(cardCount) + (runCount ? (' · ' + runCount) : '');
    if (toggleBtn && cardCount > 0) toggleBtn.classList.remove('hidden');
    if (shouldStreamSubagentSummaryDom(card)) ensureSubagentCardStreamReady(card, aid);
    return card;
}

function applySubagentFinishToCard(card, event) {
    if (!card || !event) return;
    markSubagentCardCompleted(card, event.ok !== false, String(event.error || '').trim());
    card.dataset.subagentRunning = '0';
    var aidFin = card.getAttribute('data-agent-id') || '';
    var preview = String(event.result_preview || card.dataset.resultPreview || '').trim();
    if (preview) card.dataset.resultPreview = preview;
    var body = card.querySelector('.subagent-card-body');
    if (currentSessionId && aidFin) forgetSubagentBodyCache(currentSessionId, aidFin);
    if (body && aidFin) {
        delete body.dataset.loaded;
        delete body.dataset.streamReady;
        delete body.dataset.loading;
        delete body.dataset.stashed;
        if (subagentPanelOpen && card.classList.contains('is-expanded')) {
            if (shouldStreamSubagentProcessDom(card)) {
                loadSubagentDetailInto(body, aidFin, card, currentSessionId);
            } else {
                queueSubagentCardBodyLoad(card, currentSessionId);
            }
        } else if (subagentPanelOpen) {
            updateSubagentCardSummaryOnly(card, preview);
        } else {
            body.innerHTML = '';
        }
    }
    if (aidFin) void refreshSubagentContextForCard(card, aidFin, true);
    scheduleSubagentCardStats(card);
}

function markSubagentCardCompleted(card, ok, errTxt) {
    if (!card) return;
    card.dataset.subagentRunning = '0';
    var dot = card.querySelector('.subagent-status-dot');
    if (dot) {
        dot.classList.remove('is-running', 'is-done', 'is-error');
        dot.classList.add(ok ? 'is-done' : 'is-error');
        var tip = ok ? '完成' : (/interrupt/i.test(String(errTxt || '')) ? '已中断' : '失败');
        dot.setAttribute('data-ui-tip', tip);
    }
    card.dataset.procEndedAt = String(procNow());
    var stopBtn = card.querySelector('.subagent-card-stop');
    if (stopBtn) stopBtn.remove();
    var toggleBtn = document.getElementById('subagent-toggle-btn');
    if (toggleBtn) toggleBtn.classList.remove('is-running');
}

function setSubagentCardExpanded(card, expand) {
    var grid = document.getElementById('subagent-grid');
    if (!grid || !card) return;
    if (expand) {
        grid.classList.add('is-resizing');
        stashSubagentInactiveBodies(grid, card);
        grid.querySelectorAll('.subagent-grid-card.is-expanded').forEach(function (c) {
            if (c !== card) {
                c.classList.remove('is-expanded');
                stashSubagentCardBodyForCollapse(c);
            }
        });
        card.classList.add('is-expanded');
        grid.classList.add('subagent-grid--expanded');
        var expandedBody = card.querySelector('.subagent-card-body');
        if (expandedBody && expandedBody.dataset.finalOnly === '1') {
            delete expandedBody.dataset.loaded;
            delete expandedBody.dataset.finalOnly;
            expandedBody.classList.remove('is-final-only');
            expandedBody.innerHTML = '';
        }
    } else {
        stashSubagentCardBodyForCollapse(card);
        card.classList.remove('is-expanded');
        if (!grid.querySelector('.subagent-grid-card.is-expanded')) {
            grid.classList.remove('subagent-grid--expanded');
        }
    }
    syncSubagentExpandButtons(grid);
    if (expand) {
        card.dataset.viewportVisible = '1';
        card.classList.add('is-viewport-visible');
        setTimeout(function () {
            grid.classList.remove('is-resizing');
            if (!card.classList.contains('is-expanded')) return;
            scheduleSubagentDetailWork(function () {
                if (!card.classList.contains('is-expanded')) return;
                if (!restoreSubagentCardBodyFromStash(card, currentSessionId)) {
                    queueSubagentCardBodyLoad(card, currentSessionId);
                }
            });
        }, 80);
    } else {
        requestAnimationFrame(function () {
            grid.classList.remove('is-resizing');
            if (card.isConnected && cardIntersectsGridViewport(card, grid)) {
                card.dataset.viewportVisible = '1';
                card.classList.add('is-viewport-visible');
                queueSubagentCardBodyLoad(card, currentSessionId);
            }
        });
    }
}

function syncSubagentExpandButtons(grid) {
    if (!grid) return;
    grid.querySelectorAll('.subagent-card-expand').forEach(function (btn) {
        var card = btn.closest('.subagent-grid-card');
        var on = !!(card && card.classList.contains('is-expanded'));
        btn.classList.toggle('is-active', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        btn.setAttribute('aria-label', on ? '退出全屏' : '放大显示');
        btn.setAttribute('data-ui-tip', on ? '退出全屏' : '在浮窗内全屏显示');
    });
}

function toggleSubagentCardExpanded(card) {
    if (!card) return;
    setSubagentCardExpanded(card, !card.classList.contains('is-expanded'));
}

function appendSubagentStreamEvent(agentId, event, eventIndex) {
    if (!agentId || !event || typeof event !== 'object') return false;
    var t = event.type;
    if (t === 'subagent_start') {
        upsertSubagentCardFromStartEvent(event);
        if (!replayingMessages) {
            hideSubagentContinueBanner();
            scheduleSubagentIncrementalSync();
        }
        return true;
    }
    if (t === 'subagent_finish') {
        var cardFin = document.querySelector('.subagent-grid-card[data-agent-id="' + agentId + '"]');
        if (cardFin) {
            if (event.result_preview) cardFin.dataset.resultPreview = String(event.result_preview);
            applySubagentFinishToCard(cardFin, event);
            finalizeSubagentCardStream(agentId, cardFin);
        }
        if (currentSessionId && !replayingMessages) {
            scheduleRefreshSubagentTreePanel(currentSessionId);
            updateSubagentContinueBanner(currentSessionId);
        }
        return true;
    }
    var grid = document.getElementById('subagent-grid');
    var card = grid && grid.querySelector('.subagent-grid-card[data-agent-id="' + agentId + '"]');
    if (!card) {
        if (event._subagent_forward) upsertSubagentCardFromStartEvent({ agent_id: agentId, description: agentId.slice(0, 8), running: true });
        card = grid && grid.querySelector('.subagent-grid-card[data-agent-id="' + agentId + '"]');
    }
    if (!card) return false;
    var body = card.querySelector('.subagent-card-body');
    if (!body) return false;
    if (t === 'user' || t === 'final') {
        if (!shouldStreamSubagentSummaryDom(card)) {
            trackSubagentStreamEventLightweight(card, agentId, event, eventIndex);
            return true;
        }
        if (body.dataset.loading === '1' && t !== 'user' && t !== 'final') return true;
        ensureSubagentCardStreamReady(card, agentId);
        if (body.dataset.loaded !== '1' && body.querySelector('.subagent-detail-empty')) {
            body.innerHTML = '';
        }
        if (body.dataset.loaded !== '1') body.dataset.loaded = '1';
        delete body.dataset.loading;
        var ctxSummary = getSubagentCardStreamCtx(body, card, agentId);
        dispatchSubagentCardEvent(ctxSummary, card, event, eventIndex, agentId);
        if (t === 'final') {
            finalizeLlmStreamChunks(ctxSummary);
            markSubagentCardCompleted(card, true);
            refreshFeedChunksInCtx(ctxSummary);
            syncSubagentTurnProcessFlags(body);
            if (shouldStreamSubagentProcessDom(card)) {
                scrollSubagentCardBodyToBottom(ctxSummary);
                body.querySelectorAll('.feed-chunk').forEach(scheduleFeedChunkOverflowRefresh);
            }
            if (currentSessionId && agentId && body) {
                rememberSubagentBodyCache(currentSessionId, agentId, body.innerHTML);
                body.dataset.cacheClean = '1';
            }
        }
        if (typeof eventIndex === 'number' && eventIndex >= 0) {
            subagentCardEventCount[agentId] = Math.max(subagentCardEventCount[agentId] || 0, eventIndex + 1);
        } else if (!event.ephemeral) {
            subagentCardEventCount[agentId] = (subagentCardEventCount[agentId] || 0) + 1;
        }
        scheduleSubagentCardStats(card);
        return true;
    }
    if (!shouldStreamSubagentProcessDom(card)) {
        trackSubagentStreamEventLightweight(card, agentId, event, eventIndex);
        return true;
    }
    if (body.dataset.loading === '1' && !event.ephemeral && t !== 'user' && t !== 'final') return true;
    ensureSubagentCardStreamReady(card, agentId);
    if (body.dataset.loaded !== '1' && body.querySelector('.subagent-detail-empty')) {
        body.innerHTML = '';
    }
    if (body.dataset.loaded !== '1') body.dataset.loaded = '1';
    delete body.dataset.loading;
    var ctx = getSubagentCardStreamCtx(body, card, agentId);
    if (t === 'subagent_start' || t === 'subagent_finish') return true;
    if (event.ephemeral) {
        ensureSubagentTurnForProcess(ctx, eventIndex);
        if (shouldDeferSubagentProcessDom(ctx)) {
            deferSubagentProcessEvent(ctx.currentTurn, event, eventIndex);
            if (event.type === 'context_tokens') {
                card.dataset.procCtxEstimated = String(event.estimated);
                card.dataset.procCtxThreshold = String(event.threshold);
            } else if (event.type === 'process_metrics') {
                applySubagentProcessMetricsToCard(card, event);
            } else if (event.type === 'cache_stats') {
                if (event.model != null) card.dataset.procCacheModel = String(event.model);
            }
            if (event.react_iter != null) bumpAggregateMaxReactIter(card, event.react_iter);
            markSubagentTurnHasProcess(ctx.currentTurn);
            if (typeof eventIndex === 'number' && eventIndex >= 0) {
                subagentCardEventCount[agentId] = Math.max(subagentCardEventCount[agentId] || 0, eventIndex + 1);
            }
            scheduleSubagentCardStats(card);
            return true;
        }
        if (event.type === 'llm_reasoning_delta' || event.type === 'llm_response_delta') {
            appendLlmStreamDelta(ctx, event, agentId);
        } else if (event.type === 'context_summary_delta') {
            appendProgressStreamDelta(ctx, event.delta, 'context-summary', agentId);
        } else if (event.type === 'key_context_delta') {
            appendKeyContextStreamDelta(ctx, event.delta, agentId);
        } else if (event.type === 'context_tokens') {
            card.dataset.procCtxEstimated = String(event.estimated);
            card.dataset.procCtxThreshold = String(event.threshold);
            scheduleSubagentCardStats(card);
        } else if (event.type === 'process_metrics') {
            applyProcessMetricsFromEvent(ctx, event);
        } else if (event.type === 'cache_stats') {
            applyCacheStatsFromEvent(ctx, event);
            scheduleSubagentCardStats(card);
        }
        markSubagentTurnHasProcess(ctx.currentTurn);
        if (typeof eventIndex === 'number' && eventIndex >= 0) {
            subagentCardEventCount[agentId] = Math.max(subagentCardEventCount[agentId] || 0, eventIndex + 1);
        }
        scheduleSubagentCardStats(card);
        followStreamProcessScroll(ctx, agentId);
        return true;
    } else {
        dispatchSubagentCardEvent(ctx, card, event, eventIndex, agentId);
    }
    if (typeof eventIndex === 'number' && eventIndex >= 0) {
        subagentCardEventCount[agentId] = Math.max(subagentCardEventCount[agentId] || 0, eventIndex + 1);
    } else {
        subagentCardEventCount[agentId] = (subagentCardEventCount[agentId] || 0) + 1;
    }
    scheduleSubagentCardStats(card);
    followStreamProcessScroll(ctx, agentId);
    return true;
}

function handleSubagentStreamEvent(event, eventIndex, runSessionId) {
    if (!event || typeof event !== 'object') return false;
    var aid = String(event.agent_id || '');
    if (!aid) return false;
    /* fail-closed：父会话切走后，子 agent 事件不得 fall-through 到主对话区。
       数据已写入子 agent 自己的 ui_events，切回后由 refreshSubagentTreePanel 渲染。 */
    if (runSessionId && currentSessionId && runSessionId !== currentSessionId) {
        if (!replayingMessages && event.type === 'subagent_finish') {
            void tryMarkSessionUnreadComplete(runSessionId);
        }
        return true;
    }
    return appendSubagentStreamEvent(aid, event, eventIndex);
}

async function incrementalSyncSubagentCard(agentId, card) {
    if (!agentId || !card) return;
    var body = card.querySelector('.subagent-card-body');
    if (!body || body.dataset.loading === '1') return;
    if (!shouldLoadSubagentCardBodies() && body.dataset.loaded !== '1') return;
    /* 父会话仍在 SSE 推流：转发的 subagent 事件已实时画到卡片，再用 /messages 全量回填
       会把正在 streaming 的 llm-* 块切碎（finalize → 新开行）。轮询此时降级为仅状态校准。 */
    var parentRunning = isSessionRunning(currentSessionId);
    var prevCount = subagentCardEventCount[agentId] || 0;
    var summaryOnly = !shouldStreamSubagentProcessDom(card);
    try {
        var countResp = await fetch('/sessions/' + encodeURIComponent(agentId) + '/messages/count');
        if (!countResp.ok) return;
        var countData = await countResp.json();
        var total = countData && countData.count != null ? Number(countData.count) : 0;
        if (!Number.isFinite(total) || total <= prevCount) return;
        /* 父 SSE 在跑：本次只更新计数（让按钮 badge 与状态点保持），不重渲染 body。
           待父 SSE 结束（isSessionRunning 转 false），下一轮会以 fresh prevCount 继续。 */
        if (parentRunning && body.dataset.loaded === '1') {
            subagentCardEventCount[agentId] = total;
            return;
        }
        var msgResp = await fetch('/sessions/' + encodeURIComponent(agentId) + '/messages');
        if (!msgResp.ok) return;
        var events = normalizeSubagentMessagesPayload(await msgResp.json());
        if (!body.isConnected) return;
        if (events.length <= prevCount) {
            subagentCardEventCount[agentId] = events.length;
            return;
        }
        var gotFinal = false;
        for (var fi = prevCount; fi < events.length; fi += 1) {
            if (events[fi] && events[fi].type === 'final') { gotFinal = true; break; }
        }
        if (body.dataset.loaded !== '1') {
            if (!shouldLoadSubagentCardBodies()) return;
            if (summaryOnly) {
                ensureSubagentCardStreamReady(card, agentId);
                var ctxNew = getSubagentCardStreamCtx(body, card, agentId);
                for (var si = prevCount; si < events.length; si += 1) {
                    var sev = events[si];
                    if (!sev || typeof sev !== 'object') continue;
                    if (sev.type !== 'user' && sev.type !== 'final') continue;
                    dispatchSubagentCardEvent(ctxNew, card, sev, si, agentId);
                }
                rebindSubagentCardBody(body, card, agentId);
            } else {
                renderSubagentProcessEvents(body, card, events, agentId);
            }
            subagentCardEventCount[agentId] = events.length;
            if (gotFinal) markSubagentCardCompleted(card, true);
            return;
        }
        var ctx = getSubagentCardStreamCtx(body, card, agentId);
        for (var i = prevCount; i < events.length; i += 1) {
            if (events[i] && typeof events[i] === 'object') {
                if (summaryOnly && events[i].type !== 'user' && events[i].type !== 'final' && !events[i].ephemeral) continue;
                dispatchSubagentCardEvent(ctx, card, events[i], i, agentId);
            }
        }
        /* 不在轮询路径里 finalize 流块：finalize 由 SSE 的 [DONE] 或 subagent_finish 触发。 */
        rebindSubagentCardBody(body, card, agentId);
        subagentCardEventCount[agentId] = events.length;
        if (gotFinal) markSubagentCardCompleted(card, true);
    } catch (e) { /* ignore */ }
}

function handleSubagentLifecycleEvent(event) {
    if (!event || !currentSessionId) return;
    /* 历史回放：不亮按钮 / 不写 grid / 不触发 schedule，全部交给 refreshSubagentTreePanel。 */
    if (replayingMessages) return;
    if (event.type === 'subagent_start') {
        upsertSubagentCardFromStartEvent(event);
        hideSubagentContinueBanner();
        scheduleSubagentIncrementalSync();
    } else if (event.type === 'subagent_finish') {
        var aid = String(event.agent_id || event.run_id || '');
        var card = aid && document.querySelector('.subagent-grid-card[data-agent-id="' + aid + '"]');
        if (card) {
            if (event.result_preview) card.dataset.resultPreview = String(event.result_preview);
            applySubagentFinishToCard(card, event);
            finalizeSubagentCardStream(aid, card);
        }
        scheduleRefreshSubagentTreePanel(currentSessionId);
        updateSubagentContinueBanner(currentSessionId);
    }
}

function collectSubagentGridState(grid) {
    var detailCache = {};
    if (!grid) return { detailCache: detailCache };
    if (grid.dataset.sessionId && currentSessionId && grid.dataset.sessionId !== currentSessionId) {
        return { detailCache: detailCache };
    }
    var sid = currentSessionId;
    grid.querySelectorAll('.subagent-grid-card').forEach(function (card) {
        var id = card.getAttribute('data-agent-id');
        if (!id) return;
        var body = card.querySelector('.subagent-card-body');
        if (body && body.dataset.loaded === '1' && body.dataset.loading !== '1' && body.dataset.finalOnly !== '1') {
            var html = body.innerHTML;
            if (isSubagentBodyCacheComplete(html)) {
                detailCache[id] = html;
                if (sid) rememberSubagentBodyCache(sid, id, html);
            }
        }
    });
    return { detailCache: detailCache };
}

function restoreSubagentGridState(grid, detailCache, sessionId) {
    if (!grid) return;
    grid.querySelectorAll('.subagent-grid-card').forEach(function (card) {
        var id = card.getAttribute('data-agent-id');
        if (!id) return;
        var body = card.querySelector('.subagent-card-body');
        if (!body) return;
        if (!shouldLoadSubagentCardBodies()) {
            delete body.dataset.loaded;
            delete body.dataset.loading;
            body.innerHTML = '';
            return;
        }
        var shouldMount = card.classList.contains('is-expanded') || card.dataset.viewportVisible === '1';
        if (!shouldMount) {
            delete body.dataset.loaded;
            delete body.dataset.loading;
            delete body.dataset.streamReady;
            delete body.dataset.stashed;
            body.innerHTML = '';
            return;
        }
        var cached = (detailCache && detailCache[id]) || readSubagentBodyCache(sessionId, id);
        if (card.classList.contains('is-expanded') && cached && isSubagentBodyCacheComplete(cached)) {
            body.innerHTML = cached;
            body.dataset.loaded = '1';
            body.dataset.cacheClean = '1';
            delete body.dataset.finalOnly;
            body.classList.remove('is-final-only');
            delete body.dataset.loading;
            rebindSubagentCardBody(body, card, id);
            body._subagentStreamCtx = getSubagentCardStreamCtx(body, card, id);
            requestAnimationFrame(function () { refreshAllFeedChunksUnder(body); });
        } else {
            delete body.dataset.loaded;
            delete body.dataset.loading;
            queueSubagentCardBodyLoad(card, sessionId);
        }
    });
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
        subagentCardEventCount[agentId] = (events || []).length;
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
    if (finalIdx >= 0) {
        openSubagentTurn(ctx, '', finalIdx);
        appendSubagentFinalToTurn(ctx, events[finalIdx].content || '', finalIdx);
    } else {
        var lastUser = -1;
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
    
    // 存储分页状态
    bodyEl._hasOlderEvents = hasOlder !== false; // 默认假设有更早的事件
    bodyEl._rangeStart = typeof rangeStart === 'number' ? rangeStart : 0;
    bodyEl._historyLoadedEvents = []; // 存储已加载的历史事件
    
    function loadMoreHistory() {
        if (!bodyEl.isConnected || bodyEl.dataset.historyLoading === '1' || bodyEl.dataset.historyComplete === '1') return;
        
        // 如果没有更早的事件，标记完成
        if (!bodyEl._hasOlderEvents) {
            bodyEl.dataset.historyComplete = '1';
            delete bodyEl.dataset.finalOnly;
            bodyEl.classList.remove('is-final-only');
            return;
        }
        
        var oldScrollHeight = bodyEl.scrollHeight || 0;
        var oldScrollTop = bodyEl.scrollTop || 0;
        bodyEl.dataset.historyLoading = '1';
        
        // 使用分页API加载更多历史事件
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
                
                // 更新分页状态
                bodyEl._hasOlderEvents = hasOlderNew;
                bodyEl._rangeStart = rangeStartNew;
                
                // 合并事件到已加载历史
                bodyEl._historyLoadedEvents = events.concat(bodyEl._historyLoadedEvents);
                
                // 渲染所有已加载的事件
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

async function toggleSubagentOutputPanel(card, sessionId) {
    if (!card || !sessionId) return;
    var agentId = card.getAttribute('data-agent-id') || '';
    if (!agentId) return;
    var panel = card.querySelector('.subagent-output-panel');
    if (!panel) {
        panel = document.createElement('div');
        panel.className = 'subagent-output-panel';
        var body = card.querySelector('.subagent-card-body');
        if (body) card.insertBefore(panel, body);
        else card.appendChild(panel);
    }
    var wasOpen = panel.classList.contains('is-open');
    panel.classList.toggle('is-open', !wasOpen);
    var btn = card.querySelector('.subagent-card-output');
    if (btn) btn.classList.toggle('is-active', !wasOpen);
    if (wasOpen || panel.dataset.loaded === '1' || panel.dataset.loading === '1') return;
    panel.dataset.loading = '1';
    panel.innerHTML = '<div class="subagent-output-empty">加载中...</div>';
    try {
        var resp = await fetch('/sessions/' + encodeURIComponent(sessionId) + '/subagents/' + encodeURIComponent(agentId) + '/output');
        var data = await resp.json();
        if (!resp.ok || !data || !data.ok) throw new Error((data && data.error) || ('HTTP ' + resp.status));
        var content = String(data.content || '').trim();
        panel.innerHTML = content
            ? '<div class="subagent-output-content markdown-body">' + renderMarkdown(content) + '</div>'
            : '<div class="subagent-output-empty">(无输出)</div>';
        enhanceAssistantMessageContent(panel);
        panel.dataset.loaded = '1';
    } catch (e) {
        panel.innerHTML = '<div class="subagent-output-empty">加载失败: ' + escapeHtml(String(e)) + '</div>';
    } finally {
        delete panel.dataset.loading;
    }
}

function bindSubagentGridActions(grid, sessionId) {
    if (!grid) return;
    grid.querySelectorAll('.subagent-grid-card').forEach(function (card) {
        bindProcessAggregate(card);
    });
    grid.querySelectorAll('.subagent-card-stop').forEach(function (btn) {
        if (btn.dataset.subagentStopBound) return;
        btn.dataset.subagentStopBound = '1';
        btn.addEventListener('click', async function (e) {
            e.stopPropagation();
            var aid = btn.getAttribute('data-agent-id');
            if (!aid || !sessionId) return;
            try {
                await fetch('/sessions/' + encodeURIComponent(sessionId) + '/subagents/' + encodeURIComponent(aid) + '/interrupt', { method: 'POST' });
            } catch (err) { /* ignore */ }
            var menu = btn.closest('.subagent-card-menu');
            if (menu) menu.classList.remove('is-open');
            scheduleRefreshSubagentTreePanel(sessionId);
        });
    });
    grid.querySelectorAll('.subagent-card-delete').forEach(function (btn) {
        if (btn.dataset.subagentDeleteBound) return;
        btn.dataset.subagentDeleteBound = '1';
        btn.addEventListener('click', async function (e) {
            e.stopPropagation();
            var aid = btn.getAttribute('data-agent-id');
            if (!aid || !sessionId) return;
            var ok = await openUiModal({
                title: '删除 Subagent',
                subtitle: aid.slice(0, 8) + '…',
                message: '将删除该 subagent 的会话记录、过程卡片及其嵌套子任务。该操作不可撤销。',
                danger: true,
                confirmText: '删除',
                cancelText: '取消',
            });
            if (!ok) return;
            var menu = btn.closest('.subagent-card-menu');
            if (menu) menu.classList.remove('is-open');
            btn.disabled = true;
            try {
                var resp = await fetch('/sessions/' + encodeURIComponent(sessionId) + '/subagents/' + encodeURIComponent(aid), { method: 'DELETE' });
                if (!resp.ok) {
                    showUiAlert({ title: '删除失败', message: '无法删除该 Subagent，请稍后重试。', variant: 'error' });
                    btn.disabled = false;
                    return;
                }
                forgetSubagentBodyCache(sessionId, aid);
                delete subagentCardEventCount[aid];
                delete subagentCardLoadQueued[aid];
                var card = btn.closest('.subagent-grid-card');
                if (card) card.remove();
                scheduleRefreshSubagentTreePanel(sessionId, 0);
            } catch (err) {
                btn.disabled = false;
                showUiAlert({ title: '删除失败', message: String((err && err.message) || err || 'unknown error'), variant: 'error' });
            }
        });
    });
    grid.querySelectorAll('.subagent-card-menu-btn').forEach(function (btn) {
        if (btn.dataset.subagentMenuBound) return;
        btn.dataset.subagentMenuBound = '1';
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            var menu = btn.closest('.subagent-card-menu');
            if (!menu) return;
            var open = !menu.classList.contains('is-open');
            grid.querySelectorAll('.subagent-card-menu.is-open').forEach(function (m) {
                if (m !== menu) {
                    m.classList.remove('is-open');
                    var b = m.querySelector('.subagent-card-menu-btn');
                    if (b) b.setAttribute('aria-expanded', 'false');
                }
            });
            menu.classList.toggle('is-open', open);
            btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
    });
    grid.querySelectorAll('.subagent-card-expand').forEach(function (btn) {
        if (btn.dataset.subagentExpandBound) return;
        btn.dataset.subagentExpandBound = '1';
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            var card = btn.closest('.subagent-grid-card');
            if (card) toggleSubagentCardExpanded(card);
        });
    });
    grid.querySelectorAll('.subagent-card-body').forEach(function (body) {
        if (body.dataset.subagentBodyExpandBound) return;
        body.dataset.subagentBodyExpandBound = '1';
        body.addEventListener('click', function (e) {
            var card = body.closest('.subagent-grid-card');
            if (!card || card.classList.contains('is-expanded')) return;
            var target = e.target;
            if (target && target.closest && target.closest('button,a,input,textarea,select,.feed-chunk-scroller,.copy-btn,.subagent-card-menu,.msg-wrap--user')) return;
            var sel = window.getSelection && window.getSelection();
            if (sel && String(sel).trim()) return;
            setSubagentCardExpanded(card, true);
        });
    });
    grid.querySelectorAll('.subagent-card-output').forEach(function (btn) {
        if (btn.dataset.subagentOutputBound) return;
        btn.dataset.subagentOutputBound = '1';
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            var card = btn.closest('.subagent-grid-card');
            if (card) toggleSubagentOutputPanel(card, sessionId);
            var menu = btn.closest('.subagent-card-menu');
            if (menu) menu.classList.remove('is-open');
        });
    });
    syncSubagentExpandButtons(grid);
    initUiHoverTips(grid);
}

function subagentStatusFromNode(n) {
    var taskStatus = String((n && (n.task_status || n.status)) || '').toLowerCase();
    if (n && n.running) {
        return { label: n.background ? '后台运行' : '运行中', dotCls: 'is-running' };
    }
    if (taskStatus === 'running') return { label: '后台运行', dotCls: 'is-running' };
    if (taskStatus === 'completed') return { label: '完成', dotCls: 'is-done' };
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

async function refreshSubagentContextForCard(card, agentId, force) {
    if (!card || !agentId) return;
    if (!force && !subagentPanelOpen) return;
    if (!force && card.dataset.procCtxEstimated != null && card.dataset.procCtxEstimated !== '') return;
    if (subagentContextFetchInFlight[agentId]) return subagentContextFetchInFlight[agentId];
    subagentContextFetchInFlight[agentId] = (async function () {
        try {
            var r = await fetch('/sessions/' + encodeURIComponent(agentId) + '/context_tokens');
            var j = await r.json();
            if (r.ok && j && j.ok && j.estimated != null && j.estimated >= 0) {
                card.dataset.procCtxEstimated = String(j.estimated);
                card.dataset.procCtxThreshold = String(j.threshold);
                refreshSubagentCardStats(card);
            }
        } catch (e) { /* ignore */ }
        finally {
            delete subagentContextFetchInFlight[agentId];
        }
    })();
    return subagentContextFetchInFlight[agentId];
}

function buildSubagentGridHtml(flat) {
    var sorted = sortSubagentsByUpdated(flat);
    if (!sorted.length) return '<div class="subagent-grid-empty">无 Subagent</div>';
    var html = '';
    sorted.forEach(function (n, idx) {
        var id = String(n.id || '');
        var running = !!n.running && !n.virtual_task;
        var name = n.description || id.slice(0, 8);
        var idShort = id.length > 5 ? id.slice(0, 5) + '...' : id;
        var typeLabel = n.subagent_type || 'subagent';
        var st = subagentStatusFromNode(n);
        var stopBtn = running ? '<button type="button" class="subagent-card-menu-item subagent-card-stop" role="menuitem" data-agent-id="' + escapeHtml(id) + '">停止</button>' : '';
        var outputBtn = n.output_file ? '<button type="button" class="subagent-card-menu-item subagent-card-output" role="menuitem" data-agent-id="' + escapeHtml(id) + '">查看输出</button>' : '';
        html += '<div class="process-aggregate subagent-grid-card" data-agent-id="' + escapeHtml(id) + '"';
        if (n.executor_model) html += ' data-executor-model="' + escapeHtml(String(n.executor_model)) + '"';
        if (n.output_file) html += ' data-output-file="1"';
        if (n.task_status || n.status) html += ' data-task-status="' + escapeHtml(String(n.task_status || n.status)) + '"';
        html += ' data-subagent-running="' + (running ? '1' : '0') + '"';
        html += ' data-description="' + escapeHtml(String(name || '')) + '"';
        html += '>';
        html += '<div class="subagent-card-head">';
        html += '<div class="subagent-card-head-line">';
        html += '<span class="process-aggregate-title-wrap">';
        html += '<div class="subagent-card-title-row">';
        html += '<span class="subagent-status"><span class="subagent-status-dot ' + st.dotCls + '" data-ui-tip="' + escapeHtml(st.label) + '"></span></span>';
        html += '<span class="subagent-card-name">' + escapeHtml(name) + '</span>';
        html += '<span class="subagent-card-type">' + escapeHtml(typeLabel) + '</span>';
        html += '<span class="subagent-card-id">' + escapeHtml(idShort) + '</span>';
        html += '</div>';
        html += '<span class="process-aggregate-stats" aria-live="polite"></span>';
        html += '</span>';
        html += '<span class="subagent-card-head-actions">';
        html += '<button type="button" class="subagent-card-expand" data-agent-id="' + escapeHtml(id) + '" aria-label="放大显示" aria-pressed="false" data-ui-tip="在浮窗内全屏显示"><svg class="subagent-card-expand-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M3 6V3h3M10 3h3v3M13 10v3h-3M6 13H3v-3" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/></svg></button>';
        html += '<span class="subagent-card-menu">'
            + '<button type="button" class="subagent-card-menu-btn" aria-label="更多操作" aria-expanded="false" data-ui-tip="更多操作">' + subagentMoreDotsHtml() + '</button>'
            + '<span class="subagent-card-menu-pop" role="menu">'
            + outputBtn
            + stopBtn
            + '<button type="button" class="subagent-card-menu-item subagent-card-delete" role="menuitem" data-agent-id="' + escapeHtml(id) + '">删除</button>'
            + '</span></span>';
        html += '</span>';
        html += '</div></div>';
        var rp = String(n.result_preview || '').trim();
        html += '<div class="subagent-card-body subagent-dialogue-body" data-agent-id="' + escapeHtml(id) + '"'
            + (rp ? ' data-result-preview="' + escapeHtml(rp.slice(0, 400)) + '"' : '')
            + '></div>';
        html += '</div>';
    });
    return html;
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

async function loadSubagentDetailInto(el, agentId, hostEl, sessionIdOpt) {
    if (!el || !agentId) return;
    if (el.dataset.loading === '1') return;
    var card = hostEl || (el.closest ? el.closest('.subagent-grid-card, .subagent-block') : null);
    el.dataset.loading = '1';
    delete el.dataset.loaded;
    el.innerHTML = '<div class="subagent-detail-empty">加载详情中…</div>';
    try {
        // 判断是否为折叠模式
        var isCollapsed = card && card.classList && !card.classList.contains('is-expanded') && card.classList.contains('subagent-grid-card');
        
        // 使用分页API：折叠模式只获取最近3轮，展开模式获取更多
        var turnsParam = isCollapsed ? '&turns=3' : '&turns=10';
        var resp = await fetch('/sessions/' + encodeURIComponent(agentId) + '/messages?' + turnsParam);
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        var data = await resp.json();
        
        // 兼容分页格式和数组格式
        var events, hasOlder, rangeStart;
        if (data && Array.isArray(data)) {
            // 旧格式：直接是数组
            events = data;
            hasOlder = false;
            rangeStart = 0;
        } else if (data && Array.isArray(data.events)) {
            // 新格式：分页对象
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
        // 对于折叠模式，事件计数使用实际加载的数量
        subagentCardEventCount[agentId] = events.length;
    } catch (e) {
        if (!el.isConnected) return;
        el.innerHTML = '<div class="subagent-detail-empty">加载失败: ' + escapeHtml(String(e)) + '</div>';
        el.dataset.loaded = '1';
    } finally {
        delete el.dataset.loading;
    }
}

async function refreshSubagentTreePanel(sessionId) {
    if (subagentTreeRefreshInflight && subagentTreeRefreshInflightSid === sessionId) {
        subagentTreeRefreshQueued = true;
        return subagentTreeRefreshInflight;
    }
    subagentTreeRefreshInflightSid = sessionId;
    subagentTreeRefreshInflight = refreshSubagentTreePanelInner(sessionId);
    try {
        return await subagentTreeRefreshInflight;
    } finally {
        subagentTreeRefreshInflight = null;
        subagentTreeRefreshInflightSid = null;
        if (subagentTreeRefreshQueued && sessionId === currentSessionId) {
            subagentTreeRefreshQueued = false;
            void refreshSubagentTreePanel(currentSessionId);
        }
    }
}

async function refreshSubagentTreePanelInner(sessionId) {
    bindSubagentPanelOnce();
    var seq = ++subagentPanelRefreshSeq;
    var grid = document.getElementById('subagent-grid');
    var toggleBtn = document.getElementById('subagent-toggle-btn');
    if (!grid || !sessionId) {
        if (toggleBtn) toggleBtn.classList.add('hidden');
        closeSubagentPanel();
        stopSubagentIncrementalSync();
        return;
    }
    if (grid.dataset.sessionId && grid.dataset.sessionId !== sessionId) {
        grid.innerHTML = '';
        subagentCardEventCount = Object.create(null);
    }
    grid.dataset.sessionId = sessionId;
    try {
        var resp = await fetch('/sessions/' + encodeURIComponent(sessionId) + '/subagents?lite=1');
        if (seq !== subagentPanelRefreshSeq || sessionId !== currentSessionId) return;
        var data = await resp.json();
        var flat = (data && data.subagents) ? data.subagents : [];
        if (!flat.length) {
            if (toggleBtn) toggleBtn.classList.add('hidden');
            closeSubagentPanel();
            grid.innerHTML = '';
            grid.dataset.sessionId = sessionId;
            subagentCardEventCount = Object.create(null);
            stopSubagentIncrementalSync();
            return;
        }
        refreshSubagentToggleFromGrid(flat);
        syncSubagentGridFromFlat(flat, sessionId);
        if (seq !== subagentPanelRefreshSeq || sessionId !== currentSessionId) return;
        if (subagentPanelOpen) {
            document.getElementById('subagent-dock').classList.remove('hidden');
            ensureSubagentCardViewportObserver(grid);
            grid.querySelectorAll('.subagent-grid-card').forEach(function (card) {
                observeSubagentCardViewport(card);
                if (card.classList.contains('is-expanded')) {
                    scheduleSubagentCardStats(card);
                }
            });
            loadVisibleSubagentCardBodies(grid, sessionId);
            flat.forEach(function (n) {
                if (!n || !n.id) return;
                var card = grid.querySelector('.subagent-grid-card[data-agent-id="' + String(n.id || '') + '"]');
                if (card && card.classList.contains('is-expanded')) {
                    refreshSubagentContextForCard(card, String(n.id || ''), true);
                }
            });
        }
        var runningN = flat.filter(function (n) { return n.running; }).length;
        if (runningN > 0 && subagentPanelOpen) scheduleSubagentIncrementalSync();
        else {
            stopSubagentIncrementalSync();
            if (sessionId === currentSessionId) updateSubagentContinueBanner(sessionId);
        }
    } catch (e) {
        if (toggleBtn) toggleBtn.classList.add('hidden');
        closeSubagentPanel();
        stopSubagentIncrementalSync();
    }
}

function ensureSubagentBlock(ctx, event) {
    var body = getProcessBody(ctx);
    if (!body) return null;
    var aid = String(event.agent_id || event.run_id || '');
    if (!aid) return null;
    if (!ctx.subagentBlocks) ctx.subagentBlocks = {};
    var blk = ctx.subagentBlocks[aid];
    if (blk && blk.isConnected) return blk;
    blk = document.createElement('div');
    blk.className = 'subagent-block';
    blk.dataset.agentId = aid;
    var status = event.background ? '后台运行' : '运行中';
    blk.innerHTML = '<div class="subagent-block-head" role="button" tabindex="0">'
        + '<span class="subagent-block-badge is-running">' + escapeHtml(status) + '</span>'
        + '<strong>' + escapeHtml(event.description || 'subagent') + '</strong>'
        + '<span class="subagent-block-meta">' + escapeHtml(event.subagent_type || '') + '</span>'
        + '<span class="subagent-block-id">' + escapeHtml(aid.slice(0, 8)) + '…</span>'
        + '</div>'
        + '<div class="subagent-block-preview"></div>'
        + '<div class="subagent-block-body process-aggregate-body"></div>';
    body.appendChild(blk);
    var head = blk.querySelector('.subagent-block-head');
    if (head) {
        head.addEventListener('click', function () {
            blk.classList.toggle('is-open');
            var det = blk.querySelector('.subagent-block-body');
            if (blk.classList.contains('is-open') && det && det.dataset.loaded !== '1' && det.dataset.loading !== '1') {
                loadSubagentDetailInto(det, aid, blk);
            }
        });
    }
    ctx.subagentBlocks[aid] = blk;
    handleSubagentLifecycleEvent({ type: 'subagent_start', agent_id: aid, description: event.description, subagent_type: event.subagent_type, background: event.background });
    return blk;
}

function updateSubagentBlockFinish(ctx, event) {
    var aid = String(event.agent_id || event.run_id || '');
    if (!aid) return;
    var blk = (ctx.subagentBlocks && ctx.subagentBlocks[aid]) || null;
    if (!blk || !blk.isConnected) {
        var body = getProcessBody(ctx);
        if (body) blk = body.querySelector('.subagent-block[data-agent-id="' + aid + '"]');
    }
    if (!blk) {
        handleSubagentLifecycleEvent(event);
        return;
    }
    var badge = blk.querySelector('.subagent-block-badge');
    var preview = blk.querySelector('.subagent-block-preview');
    var ok = event.ok !== false;
    if (badge) {
        badge.textContent = ok ? '完成' : '失败';
        badge.classList.remove('is-running');
        badge.classList.toggle('is-done', ok);
        badge.classList.toggle('is-error', !ok);
    }
    if (preview) {
        var txt = event.result_preview || event.error || '';
        preview.textContent = txt ? String(txt).slice(0, 500) : '';
    }
    handleSubagentLifecycleEvent(event);
}

`,k=`function renderEvent(ctx, event, eventIndex, runSessionId) {
    if (!event || typeof event !== 'object') return;
    if (event.type === 'user') {
        if (typeof eventIndex === 'number') ctx.lastUserEventIndex = eventIndex;
        sealProcessGroup(ctx);
        appendMessage(ctx, 'user', event.content || '', { eventIndex: eventIndex, turnTruncateIdx: eventIndex }, runSessionId);
    } else if (event.type === 'final') {
        appendMessage(ctx, 'assistant', event.content || '', { eventIndex: eventIndex, turnTruncateIdx: ctx.lastUserEventIndex }, runSessionId);
    } else if (event.type === 'process_metrics') {
        applyProcessMetricsFromEvent(ctx, event);
    } else if (event.type === 'cache_stats') {
        applyCacheStatsFromEvent(ctx, event);
    } else if (event.type === 'tool_call') {
        var riTool = uiEventReactIter(event);
        if (event.raw_content) appendLog(ctx, event.raw_content, 'tool-call', runSessionId, riTool);
        else appendLog(ctx, formatToolDoneLine(event.tool, event.args, event.result, event.command_preview), 'tool-call', runSessionId, riTool);
    } else if (event.type === 'validate_final') {
        appendLog(ctx, '验证：' + event.result + (event.reason ? '\\n' + event.reason : ''), 'status', runSessionId);
    } else if (event.type === 'llm_reasoning') {
        upsertLlmFeedRow(ctx, event.content || '', 'llm-reasoning', runSessionId, uiEventReactIter(event));
    } else if (event.type === 'llm_response') {
        upsertLlmFeedRow(ctx, event.content || '', 'llm-response', runSessionId, uiEventReactIter(event));
    } else if (event.type === 'llm_history_rollup' || event.type === 'compact_summary') {
        appendLog(ctx, String(event.content || ''), 'compact-summary', runSessionId);
    } else if (event.type === 'context_trim_progress') {
        appendProgressLog(ctx, event.content, 'context-trim', runSessionId);
    } else if (event.type === 'context_summary_progress') {
        appendProgressLog(ctx, event.content, 'context-summary', runSessionId);
    } else if (event.type === 'context_summary_delta') {
        appendProgressStreamDelta(ctx, event.delta, 'context-summary', runSessionId);
    } else if (event.type === 'context_summary_body') {
        applyProgressPersistedBody(ctx, event.content, 'context-summary', runSessionId);
    } else if (event.type === 'key_context_progress') {
        var keyProg = String(event.content || '');
        if (keyProg.indexOf('正在根据对话更新要点') >= 0) {
            finalizeProgressStreamForType(ctx, 'context-summary');
            resetKeyContextStreamFilter(ctx);
        }
        appendProgressLog(ctx, keyProg, 'key-context', runSessionId);
    } else if (event.type === 'key_context_delta') {
        appendKeyContextStreamDelta(ctx, event.delta, runSessionId);
    } else if (event.type === 'key_context_body') {
        applyProgressPersistedBody(ctx, event.content, 'key-context', runSessionId);
    } else if (event.type === 'error') {
        appendLog(ctx, String(event.content || ''), 'error-log', runSessionId);
    } else if (event.type === 'status') {
        var statusContent = String(event.content || '');
        if (statusContent.indexOf('【自动·长度策略】') >= 0) {
            finalizeProgressStreamChunks(ctx);
            resetKeyContextStreamFilter(ctx);
        }
        if (event.compress_progress) {
            var legacyLogType = 'context-trim';
            if (statusContent.indexOf('【上下文摘要】') >= 0) legacyLogType = 'context-summary';
            else if (statusContent.indexOf('【要点】') >= 0) legacyLogType = 'key-context';
            appendProgressLog(ctx, statusContent, legacyLogType, runSessionId);
            return;
        }
        // 临时状态消息处理：标记"正在思考中..."为临时状态
        var isTemporaryStatus = statusContent.indexOf('正在思考中...') >= 0;
        if (isTemporaryStatus) removeTemporaryStatus(ctx);
        var statusRow = appendLog(ctx, statusContent, 'status', runSessionId);
        if (isTemporaryStatus && statusRow) {
            statusRow.dataset.temporaryStatus = '1';
        }
    } else if (event.type === 'approval_required') {
        var leg = (event.tool_name ? String(event.tool_name) + ' ' : '') + (event.message || '');
        appendLog(ctx, '[历史/旧版事件] ' + leg.trim(), 'status', runSessionId);
    } else if (event.type === 'warning') {
        appendLog(ctx, String(event.content || ''), 'status', runSessionId);
    } else if (event.type === 'subagent_start' || event.type === 'subagent_finish') {
        if (!ctx._subagentBody) {
            handleSubagentLifecycleEvent(event);
            return;
        }
        if (event.type === 'subagent_start') ensureSubagentBlock(ctx, event);
        else updateSubagentBlockFinish(ctx, event);
    } else {
        var fallbackContent = String(event.content || '');
        if (fallbackContent.trim()) appendLog(ctx, fallbackContent, 'log-entry', runSessionId);
    }
}
`,L=`function setSendButtonState() {
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
    const run = runningBySession[currentSessionId];
    const sid = currentSessionId;
    serverStreamActiveBySession[sid] = false;
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
    delete runningBySession[sid];
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
    const serverStreamActive = opts.serverStreamActive === true;
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
        applySessionItemIndicators(div, sid, { serverStreamActive: !!serverStreamActiveBySession[sid] });
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
                archivedSessionsLoaded = false;
                archivedSessionsCache = null;
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
                try { runningBySession[sess.id].controller.abort(); } catch (err) { /* ignore */ }
                const r = runningBySession[sess.id];
                if (r && r.ctx && r.ctx.stream && r.ctx.stream.parentNode) r.ctx.stream.remove();
                delete runningBySession[sess.id];
                setSendButtonState();
                syncSessionListIndicatorClasses();
            }
            await fetch('/sessions/' + sess.id, { method: 'DELETE' });
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
        serverStreamActiveBySession[sess.id] = !!sess.stream_active;
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

async function loadSessions() {
    const loadEpoch = ++sessionListLoadEpoch;
    try {
        // 检查缓存
        const cachedData = sessionListCache.get();
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
                    archivedSessionsCount = parsedArchivedCount;
                }
            }
            const sessions = await response.json();
            if (loadEpoch !== sessionListLoadEpoch) return;
            allSessions = Array.isArray(sessions) ? sessions : [];
            
            // 更新缓存
            sessionListCache.set(allSessions);
        }
        
        const nextStreamMap = Object.create(null);
        const idSet = new Set();
        for (let si = 0; si < allSessions.length; si += 1) {
            if (allSessions[si] && allSessions[si].id) idSet.add(allSessions[si].id);
        }
        [...sessionUnreadComplete].forEach(function (uid) {
            if (!idSet.has(uid)) sessionUnreadComplete.delete(uid);
        });
        persistSessionUnread();

        const pinnedList = [];
        const normalList = [];
        const archivedList = archivedSessionsLoaded && Array.isArray(archivedSessionsCache) ? archivedSessionsCache : [];
        for (let i = 0; i < allSessions.length; i += 1) {
            const s = allSessions[i];
            if (!s || !s.id) continue;
            const arch = !!s.archived;
            const pin = !!s.pinned;
            if (arch) continue;
            else if (pin) pinnedList.push(s);
            else normalList.push(s);
        }

        sessionsList.innerHTML = '';

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
                        archivedSessionsLoaded = true;
                        archivedSessionsCache = all.filter(function (s) { return s && s.id && !!s.archived; });
                        archivedSessionsCount = archivedSessionsCache.length;
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

        serverStreamActiveBySession = nextStreamMap;
        updateSessionTitle();
    } catch (error) {
        console.error('加载会话列表失败:', error);
        appendLogVisible('加载会话列表失败', 'error-log');
    }
}

async function loadSessionMessages(sessionId, scrollBehavior, opts) {
    scrollBehavior = scrollBehavior || 'saved-or-bottom';
    opts = opts || {};
    const loadToken = ++messageLoadEpoch;
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
    currentSessionId = sessionId;
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
        maybeStartStreamPollForSession(sessionId);
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
        await loadSessionMessages(sessionId);
        if (switchToken !== switchSessionEpoch || sessionId !== currentSessionId) return;
        hideLoading();
        /* loadSessionMessages 内部已发起 rebuildToc()；这里再延后一帧调用 subagent panel
           保证「目录 → 消息 → 子 agent 按钮」的稳定顺序（无 subagent 的会话表现一致）。 */
        setTimeout(function () { refreshSubagentTreePanel(sessionId); }, 0);
        void refreshSingleSessionRow(sessionId);
        setSendButtonState();
        maybeStartStreamPollForSession(sessionId);
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
        resetSubagentPanelForSession();
        switchSessionEpoch += 1;
        messageLoadEpoch += 1;
        currentSessionId = data.session_id;
        localStorage.setItem('lastSessionId', currentSessionId);
        restoreInputDraft(currentSessionId);
        if (!getVisibleChatStream()) ensureVisibleChatStreamSlot();
        setWelcome();
        replayingMessages = false;
        sessionListCache.invalidate();
        await loadSessions();
        setSendButtonState();
        maybeStartStreamPollForSession(currentSessionId);
        scheduleContextTokensAfterPaint(currentSessionId);
    } catch (error) {
        console.error('创建新会话失败:', error);
        appendLogVisible('创建新会话失败', 'error-log');
    }
}
`,_=`async function consumeAgentSseResponse(response, runCtx, runSessionId, streamEventIdx) {
    if (!response || !response.body) return streamEventIdx;
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\\n');
        buffer = lines.pop();
        for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const data = line.slice(6);
            if (data === '[DONE]') {
                finalizeLlmStreamChunks(runCtx);
                void refreshTodoPlanPanel();
                if (liveAutoFollow) {
                    scrollProcessBodyToBottom(runCtx, runSessionId);
                    scrollChatToBottomIfFollow(runSessionId, {});
                }
                return streamEventIdx;
            }
            try {
                const parsed = JSON.parse(data);
                if (parsed.ephemeral) {
                    /* 任何携带 agent_id 的 ephemeral 都属于子 agent；无论投递成功与否都不能 fall-through
                       到父 ctx 的 appendLlmStreamDelta，否则会污染主对话区。 */
                    if (parsed.agent_id) { handleSubagentStreamEvent(parsed, streamEventIdx, runSessionId); continue; }
                    if (parsed.type === 'tool_approval_required') {
                        finalizeLlmStreamChunks(runCtx);
                        var aidApr = parsed.approval_id != null ? String(parsed.approval_id) : '';
                        var ttlApr = parsed.title != null ? String(parsed.title) : '需要确认';
                        var msgApr = parsed.message != null ? String(parsed.message) : '';
                        var subApr = parsed.subtitle != null ? String(parsed.subtitle) : '';
                        var allowApr = false;
                        try {
                            allowApr = await openUiModal({
                                title: ttlApr,
                                subtitle: subApr,
                                message: msgApr,
                                danger: true,
                                confirmText: '允许执行',
                                cancelText: '拒绝',
                            });
                        } catch (eApr) {
                            allowApr = false;
                        }
                        try {
                            await fetch('/sessions/' + encodeURIComponent(runSessionId) + '/tool-approval', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ approval_id: aidApr, approve: allowApr }),
                            });
                        } catch (errApr) {
                            console.error('tool-approval POST failed:', errApr);
                        }
                        continue;
                    }
                    if (parsed.type === 'tool_pending') {
                        finalizeLlmStreamChunks(runCtx);
                        removeTemporaryStatus(runCtx);
                        appendToolPendingRow(runCtx, parsed, runSessionId);
                        continue;
                    }
                    if (parsed.type === 'tool_call_delta') {
                        appendToolCallDelta(runCtx, parsed, runSessionId);
                        continue;
                    }
                    if (parsed.type === 'tool_command_delta') {
                        appendToolCommandDelta(runCtx, parsed, runSessionId);
                        continue;
                    }
                    if (parsed.type === 'llm_reasoning_delta' || parsed.type === 'llm_response_delta') appendLlmStreamDelta(runCtx, parsed, runSessionId);
                    else if (parsed.type === 'context_summary_delta') appendProgressStreamDelta(runCtx, parsed.delta, 'context-summary', runSessionId);
                    else if (parsed.type === 'key_context_delta') appendKeyContextStreamDelta(runCtx, parsed.delta, runSessionId);
                    else if (parsed.type === 'context_tokens') recordContextTokens(runSessionId, parsed.estimated, parsed.threshold);
                    else if (parsed.type === 'cache_stats' && runSessionId === currentSessionId) applyCacheStatsFromEvent(runCtx, parsed);
                    else if (parsed.type === 'todo_plan' && runSessionId === currentSessionId) applyTodoPlanFromPayload(parsed);
                    else if (parsed.type === 'status') {
                        var statusContent = String(parsed.content || '');
                        var isTemporaryStatus = statusContent.indexOf('正在思考中...') >= 0;
                        if (isTemporaryStatus) removeTemporaryStatus(runCtx);
                        var statusRow = appendLog(runCtx, statusContent, 'status', runSessionId);
                        if (isTemporaryStatus && statusRow) {
                            statusRow.dataset.temporaryStatus = '1';
                        }
                    }
                    continue;
                }
                if (parsed.agent_id) {
                    /* 非 ephemeral 子 agent 事件：必须走子 agent 通道，绝不能落到 renderEvent(runCtx,...) */
                    handleSubagentStreamEvent(parsed, streamEventIdx, runSessionId);
                    streamEventIdx += 1;
                    continue;
                }
                finalizeLlmStreamChunks(runCtx);
                if (parsed.type === 'tool_call') {
                    upsertToolCallResult(runCtx, parsed, runSessionId);
                    streamEventIdx += 1;
                    continue;
                }
                renderEvent(runCtx, parsed, streamEventIdx, runSessionId);
                streamEventIdx += 1;
            } catch (e) { console.error('解析事件失败:', e); }
        }
    }
    return streamEventIdx;
}

async function startContinueAfterSubagents(sessionId) {
    if (!sessionId || sessionId !== currentSessionId) return;
    delete subagentContinueDismissedForSession[sessionId];
    if (isSessionRunning(sessionId) || subagentContinueInFlight) {
        updateSubagentContinueBanner(sessionId);
        return;
    }
    if (sendPipelineLock && sendPipelineLockSessionId === sessionId) {
        updateSubagentContinueBanner(sessionId);
        return;
    }
    hideSubagentContinueBanner();
    subagentContinueInFlight = true;
    var runCtx = null;
    var runSessionId = sessionId;
    try {
    var banner = document.getElementById('subagent-continue-banner');
    var continueMode = banner && banner.dataset && banner.dataset.continueMode === 'react' ? 'react' : 'subagents';
    var continueUrl = continueMode === 'react'
        ? '/sessions/' + encodeURIComponent(sessionId) + '/continue'
        : '/sessions/' + encodeURIComponent(sessionId) + '/continue-subagents';
        const response = await fetch(continueUrl, { method: 'POST' });
        if (response.status === 204) {
            hideSubagentContinueBanner();
            return;
        }
        if (response.status === 409) {
            updateSubagentContinueBanner(sessionId);
            return;
        }
        var ct = (response.headers.get('content-type') || '').toLowerCase();
        if (!response.ok || !response.body || ct.indexOf('text/event-stream') < 0) return;
        const preCount = await getUiEventCount();
        if (!getVisibleChatStream()) ensureVisibleChatStreamSlot();
        runCtx = newDomContext(getVisibleChatStream());
        if (runningBySession[runSessionId] && runningBySession[runSessionId].ctx) {
            runCtx = runningBySession[runSessionId].ctx;
        } else {
            runCtx.lastUserEventIndex = Math.max(0, preCount - 1);
            resetLlmState(runCtx);
            finalizeLlmStreamChunks(runCtx);
        }
        const ac = new AbortController();
        runningBySession[runSessionId] = { controller: ac, ctx: runCtx };
        setSendButtonState();
        syncSessionListIndicatorClasses();
        liveAutoFollow = true;
        streamProcNearBottom = true;
        scheduleContextTokensAfterPaint(runSessionId);
        let streamEventIdx = preCount;
        try {
            await consumeAgentSseResponse(response, runCtx, runSessionId, streamEventIdx);
        } catch (error) {
            if (error.name === 'AbortError') appendLog(runCtx, '任务已中断', 'status', runSessionId);
            else {
                console.error('续接 subagent 失败:', error);
                const msg = (error && error.message) ? String(error.message) : String(error);
                appendLog(runCtx, '续接失败: ' + msg, 'error-log', runSessionId);
            }
        } finally {
            finalizeLlmStreamChunks(runCtx);
            finalizeProgressStreamChunks(runCtx);
            void refreshTodoPlanPanel();
            if (liveAutoFollow) {
                scrollProcessBodyToBottom(runCtx, runSessionId);
                scrollChatToBottomIfFollow(runSessionId, {});
            }
            if (runningBySession[runSessionId]) delete runningBySession[runSessionId];
            setSendButtonState();
            syncSessionListIndicatorClasses();
            await refreshSingleSessionRow(runSessionId);
            await refreshContextTokensFromServer(runSessionId);
        }
        hideSubagentContinueBanner();
        if (!subagentContinueDismissedForSession[sessionId]) updateSubagentContinueBanner(sessionId);
    } finally {
        subagentContinueInFlight = false;
    }
}

async function attachSessionEventStream(sessionId) {
    if (!sessionId || runningBySession[sessionId]) return;
    if (!serverStreamActiveBySession[sessionId]) return;
    var runSessionId = sessionId;
    var runCtx = null;
    try {
        if (runSessionId !== currentSessionId) return;
        if (!getVisibleChatStream()) ensureVisibleChatStreamSlot();
        runCtx = newDomContext(getVisibleChatStream());
        var existingProcessGroup = runCtx.stream.querySelector('.process-aggregate:last-of-type');
        if (existingProcessGroup) {
            runCtx.currentProcessGroup = existingProcessGroup;
            bindProcessAggregate(existingProcessGroup);
            existingProcessGroup.classList.remove('is-collapsed');
            var top = existingProcessGroup.querySelector('.process-aggregate-top');
            if (top) top.setAttribute('aria-expanded', 'true');
        }
        resetLlmState(runCtx);
        finalizeLlmStreamChunks(runCtx);
        const ac = new AbortController();
        runningBySession[runSessionId] = { controller: ac, ctx: runCtx, reattached: true };
        setSendButtonState();
        syncSessionListIndicatorClasses();
        liveAutoFollow = true;
        streamProcNearBottom = true;
        const preCount = await getUiEventCount(runSessionId);
        const response = await fetch('/sessions/' + encodeURIComponent(runSessionId) + '/stream', { signal: ac.signal });
        var ct = (response.headers.get('content-type') || '').toLowerCase();
        if (!response.ok || !response.body || ct.indexOf('text/event-stream') < 0) return;
        await consumeAgentSseResponse(response, runCtx, runSessionId, preCount);
    } catch (error) {
        if (error && error.name === 'AbortError') return;
        console.error('reattach stream failed:', error);
    } finally {
        if (runCtx) {
            finalizeLlmStreamChunks(runCtx);
            finalizeProgressStreamChunks(runCtx);
        }
        if (runningBySession[runSessionId] && runningBySession[runSessionId].reattached) {
            delete runningBySession[runSessionId];
        }
        setSendButtonState();
        syncSessionListIndicatorClasses();
        await refreshSingleSessionRow(runSessionId);
        await refreshContextTokensFromServer(runSessionId);
        if (runSessionId === currentSessionId) updateSubagentContinueBanner(runSessionId);
    }
}

async function processRewriteTruncateAsync(pr) {
    try {
        const anchor = document.querySelector('.msg-wrap--user[data-truncate-from="' + String(pr.before) + '"]');
        const res = await truncateSessionOnServer(pr.before);
        if (!res || !res.ok) {
            showUiAlert({
                title: '截断失败',
                message: describeServerSyncFailure(res, '无法同步服务器，改写未生效。'),
                variant: 'error'
            });
            return false;
        }
        if (currentSessionId === pr.sessionId) {
            scheduleContextTokensAfterPaint(pr.sessionId);
            if (anchor) {
                removeMessagesFromNode(anchor);
                syncDisconnectedProcessGroups();
                rebuildToc();
            }
        }
        return true;
    } catch (error) {
        console.error('异步截断失败:', error);
        showUiAlert({
            title: '截断失败',
            message: describeServerSyncFailure({ error: (error && error.message) || String(error) }, '无法同步服务器，改写未生效。'),
            variant: 'error'
        });
        return false;
    }
}

async function sendMessage() {
    /* 立即快照「提交会话」：之后所有 await 都不能改变它，避免用户在 await 空隙切走后消息发到新会话。
       关键不变式：runSessionId === submitSessionId 全程恒等。 */
    const submitSessionIdInitial = currentSessionId;
    rewriteInputWorkspacePaths();
    const visibleMessage = messageInput.value;
    const rawMessage = expandInputPathTokens(visibleMessage);
    if (!String(rawMessage).trim()) return;
    if (isSessionRunning(submitSessionIdInitial)) return;
    if (sendPipelineLock && sendPipelineLockSessionId === submitSessionIdInitial) return;

    /* 立即上锁：阻止后续连击；锁的 key 是提交时的会话，而非当前会话。 */
    sendPipelineLock = true;
    sendPipelineLockSessionId = submitSessionIdInitial;
    try {

    if (pendingRewriteTruncate && pendingRewriteTruncate.sessionId === submitSessionIdInitial) {
        const pr = pendingRewriteTruncate;
        const rewriteTruncated = await processRewriteTruncateAsync(pr);
        if (!rewriteTruncated) return;
        pendingRewriteTruncate = null;
    }
    hideRewriteUndoToast();

    hideSubagentContinueBanner();

    let submitSessionId = submitSessionIdInitial;
    if (!submitSessionId) {
        await createNewSession();
        submitSessionId = currentSessionId;
        if (!submitSessionId) return;
        sendPipelineLockSessionId = submitSessionId;
    }
    // 使用缓存的事件计数，实现乐观更新
    const preCount = uiEventCountCache.get(submitSessionId);
    const runSessionId = submitSessionId;

    /* 用户在 createNewSession / getUiEventCount 期间切走：
       后台仍然发起 /chat（消息已属于 runSessionId），但不要往当前可见 stream 画用户气泡。 */
    const switchedAway = currentSessionId !== runSessionId;
    let runCtx;
    if (switchedAway) {
        const offscreen = document.createElement('div');
        offscreen.className = 'chat-stream is-offscreen';
        if (typeof offscreenRoot !== 'undefined' && offscreenRoot) offscreenRoot.appendChild(offscreen);
        runCtx = newDomContext(offscreen);
    } else {
        if (!getVisibleChatStream()) ensureVisibleChatStreamSlot();
        runCtx = newDomContext(getVisibleChatStream());
    }
    runCtx.lastUserEventIndex = preCount;
    resetLlmState(runCtx);
    finalizeLlmStreamChunks(runCtx);
    sealProcessGroup(runCtx);
    const ac = new AbortController();
    runningBySession[runSessionId] = { controller: ac, ctx: runCtx };
    setSendButtonState();
    syncSessionListIndicatorClasses();
    if (!switchedAway) {
        liveAutoFollow = true;
        streamChatNearBottom = true;
        streamProcNearBottom = true;
        appendMessage(runCtx, 'user', rawMessage, { eventIndex: preCount, turnTruncateIdx: preCount }, runSessionId);
        messageInput.value = '';
        clearInputPathTokens();
        autoResizeTextarea();
    }
    updateSidebarLastUserPreviewImmediate(runSessionId, rawMessage);
    lastUserMessageBySession[runSessionId] = rawMessage;
    const formData = new FormData();
    formData.append('message', rawMessage);
    formData.append('session_id', runSessionId);
    /* 保留右上角 token 进度条上一快照，直至 SSE /context_tokens 推送新估值，避免每次发送闪零 */
    if (!switchedAway) scheduleContextTokensAfterPaint(runSessionId);
    let streamEventIdx = preCount + 1;
    
    // 异步更新事件计数缓存（从服务器获取真实计数）
    getUiEventCount(submitSessionId).then(function(serverCount) {
        uiEventCountCache.updateFromServer(submitSessionId, serverCount);
    }).catch(function(err) {
        console.error('更新事件计数缓存失败:', err);
    });
    try {
        const response = await fetch('/chat', { method: 'POST', body: formData, signal: ac.signal });
        streamEventIdx = await consumeAgentSseResponse(response, runCtx, runSessionId, streamEventIdx);
    } catch (error) {
        if (error.name === 'AbortError') appendLog(runCtx, '任务已中断', 'status', runSessionId);
        else {
            console.error('请求失败:', error);
            const msg = (error && error.message) ? String(error.message) : String(error);
            appendLog(runCtx, '请求失败: ' + msg, 'error-log', runSessionId);
        }
    } finally {
        finalizeLlmStreamChunks(runCtx);
        finalizeProgressStreamChunks(runCtx);
        void refreshTodoPlanPanel();
        if (liveAutoFollow && !switchedAway) {
            scrollProcessBodyToBottom(runCtx, runSessionId);
            scrollChatToBottomIfFollow(runSessionId, {});
        }
        if (runSessionId !== currentSessionId) {
            void tryMarkSessionUnreadComplete(runSessionId);
        } else {
            updateSubagentContinueBanner(runSessionId);
        }
        if (runningBySession[runSessionId]) {
            delete runningBySession[runSessionId];
            if (runSessionId !== currentSessionId) {
                const el = runCtx.stream;
                if (el && el.parentNode) el.remove();
            }
        }
        setSendButtonState();
        syncSessionListIndicatorClasses();
        await refreshSingleSessionRow(runSessionId);
        await refreshContextTokensFromServer(runSessionId);
        if (runSessionId === currentSessionId && countRunningSubagentCards() > 0) {
            scheduleSubagentIncrementalSync();
        }
    }
    } finally {
        sendPipelineLock = false;
        sendPipelineLockSessionId = null;
    }
}

messageInput.addEventListener('keydown', function onInputKeydown(e) {
    if (e.key !== 'Enter') return;
    // Ctrl+Enter → 插入换行（跨浏览器兼容）
    if (e.ctrlKey && !e.shiftKey && !e.metaKey) {
        const start = this.selectionStart;
        const end = this.selectionEnd;
        this.value = this.value.substring(0, start) + '\\n' + this.value.substring(end);
        this.selectionStart = this.selectionEnd = start + 1;
        e.preventDefault();
        autoResizeTextarea();
        return;
    }
    // Shift+Enter → 浏览器默认插入换行
    if (e.shiftKey) return;
    // 纯 Enter → 发送
    if (isSessionRunning(currentSessionId)) return;
    e.preventDefault();
    sendMessage();
});
chatContainer.addEventListener('scroll', function () {
    refreshLiveAutoFollowPins();
    scheduleTocActiveUpdate();
}, { passive: true });
sendBtn.addEventListener('click', function () {
    if (isSessionRunning(currentSessionId)) pauseCurrentRun();
    else sendMessage();
});
(function bindRewriteUndo() {
    const toast = document.getElementById('rewrite-undo-toast');
    const btn = toast && toast.querySelector('.rewrite-undo-btn');
    if (!btn) return;
    btn.addEventListener('click', async function (e) {
        e.preventDefault();
        if (!rewriteUndoState) { hideRewriteUndoToast(); return; }
        const s = rewriteUndoState;
        if (s.type === 'rewrite_pending') {
            const prevIn = (s.data && s.data.prevInput != null) ? s.data.prevInput : '';
            messageInput.value = prevIn;
            rewriteInputWorkspacePaths();
            autoResizeTextarea();
            messageInput.focus();
            pendingRewriteTruncate = null;
            hideRewriteUndoToast();
            return;
        }
        if (s.type === 'input' && s.data) {
            messageInput.value = s.data.prev;
            rewriteInputWorkspacePaths();
            autoResizeTextarea();
            messageInput.focus();
            hideRewriteUndoToast();
            return;
        }
        if (s.type === 'tail' && s.data && s.data.sessionId && s.data.tail && s.data.tail.length) {
            try {
                const r = await fetch('/sessions/' + encodeURIComponent(s.data.sessionId) + '/append_ui_events',
                    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ events: s.data.tail }) });
                if (!r.ok) { alert('撤销失败，请重试。'); return; }
                if (s.data.sessionId === currentSessionId) {
                    showLoading();
                    await loadSessionMessages(s.data.sessionId, 'bottom', { full: true });
                    hideLoading();
                }
            } catch (err) { console.error(err); alert('撤销失败，请重试。'); return; }
        }
        hideRewriteUndoToast();
    });
})();
(function bindSubagentContinueBannerOnce() {
    if (window.__myAgentSubagentContinueBound) return;
    window.__myAgentSubagentContinueBound = true;
    var btn = document.getElementById('subagent-continue-btn');
    var dismissBtn = document.getElementById('subagent-continue-dismiss');
    if (btn) btn.addEventListener('click', function (e) {
        e.preventDefault();
        if (!currentSessionId || subagentContinueInFlight) return;
        void startContinueAfterSubagents(currentSessionId);
    });
    if (dismissBtn) dismissBtn.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        dismissSubagentContinueBanner(currentSessionId);
    });
})();
initUiHoverTips(document);
`,P=`newSessionBtn.addEventListener('click', async () => { await createNewSession(); });

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

`,B=[x,C,I,w,T,E,k,L,_,P];Function(`"use strict";
`+B.join(`

`)+`
//# sourceURL=myagent-ui.js`)();
