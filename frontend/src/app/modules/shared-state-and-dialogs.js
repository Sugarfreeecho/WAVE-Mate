let currentSessionId = null;
/** Blocks repeat sends while the async send pipeline is claiming a sessionStore run slot. */
const sendPipelineLocksBySession = Object.create(null);
/** Optimistic preflight before the first session id has been allocated. */
let optimisticNewSessionRun = null;
const followupQueueBySession = Object.create(null);
const followupQueueLoadedBySession = Object.create(null);
let followupQueueSeq = 1;
const followupWatchTimers = Object.create(null);
const followupServerSyncInFlight = Object.create(null);
/** 会话级自动续发定时器；同一会话只保留一个最近到期的任务。 */
const followupDrainTimers = Object.create(null);
/** 会话级追问发送互斥链：显式立即发送共用，保证同一会话同一时刻只处理一条追问。 */
const followupDispatchChain = Object.create(null);
/** 手动“立即发送”代次；用于淘汰已排队但尚未开始的旧自动队首发送。 */
const followupManualDispatchEpochBySession = Object.create(null);
/** 输入框刚加入队列的任务；仅供下一次空输入 Enter 优先立即发送。 */
let recentComposerQueuedFollowup = null;
let followupSnapshotRecoveryInitialized = false;
/** 会话在后台跑完后未点开过：侧栏绿点，点开即清除（localStorage 持久化，刷新不丢） */
const sessionUnreadComplete = new Set();
const LS_SESSION_UNREAD = 'myagent-session-unread';
const sessionUnreadClearInFlight = Object.create(null);
/** 每个会话独立的输入草稿（切换会话恢复） */
const draftBySession = Object.create(null);
/** 尚未落库的新会话也有稳定的本地草稿作用域。 */
const NEW_SESSION_DRAFT_KEY = '__new_session_draft__';
const LS_INPUT_DRAFT_PREFIX = 'myagent-input-draft-';
const LS_FOLLOWUP_QUEUE_PREFIX = 'myagent-followup-queue-';
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
/** Event-heavy turns can contain hundreds of tool/process rows; cap initial replay at turn boundaries. */
const HISTORY_EVENT_BUDGET = 500;

/** 右侧「历史记录」重建序号：防止切换会话后旧 fetch 与当前 DOM 合并导致目录串台 */
let tocRebuildEpoch = 0;
let tocActiveUpdateRaf = 0;
let tocScrollBottomOnNextBuild = false;
let suppressTocDuringSessionLoad = false;
let switchSessionEpoch = 0;
let messageLoadEpoch = 0;

/** 右侧「历史记录」链接悬停浮层（替代浏览器原生 title） */
let uiHoverTooltipEl = null;
let hoverTooltipMoveScheduled = false;
let uiHoverTipScrollReconcileScheduled = false;
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
const USER_MESSAGE_COLLAPSE_LINES = 10;
const USER_MESSAGE_VIRTUAL_LINE_CHARS = 100;

var uiModalKeyHandler = null;

function isMyAgentFeatureEnabled(name, defaultValue) {
    var features = (typeof window !== 'undefined' && window.__MYAGENT_FEATURES__ && typeof window.__MYAGENT_FEATURES__ === 'object')
        ? window.__MYAGENT_FEATURES__
        : {};
    if (Object.prototype.hasOwnProperty.call(features, name)) return !!features[name];
    return !!defaultValue;
}

function clearSessionUnreadState(sessionId, opts) {
    var sid = String(sessionId || '');
    if (!sid) return;
    opts = opts || {};
    sessionUnreadComplete.delete(sid);
    persistSessionUnread();
    if (typeof sessionStore !== 'undefined') {
        var sess = sessionStore.get(sid);
        if (sess) {
            sess.unread_result = false;
            delete sess.unread_result_at;
            delete sess.unread_result_status;
        }
    }
    if (typeof syncSessionListIndicatorClasses === 'function') syncSessionListIndicatorClasses();
    if (opts.server === false || sessionUnreadClearInFlight[sid]) return;
    sessionUnreadClearInFlight[sid] = true;
    fetch('/sessions/' + encodeURIComponent(sid) + '/unread-result/clear', { method: 'POST' })
        .catch(function () { /* ignore */ })
        .finally(function () { delete sessionUnreadClearInFlight[sid]; });
}

function markSessionResultComplete(sessionId, status) {
    var sid = String(sessionId || '');
    if (!sid) return;
    var normalizedStatus = status === 'failed' ? 'failed' : 'success';
    sessionUnreadComplete.add(sid);
    if (typeof sessionStore !== 'undefined') {
        var sess = sessionStore.get(sid);
        if (sess) {
            sess.unread_result = true;
            sess.unread_result_status = normalizedStatus;
            sess.unread_result_at = new Date().toISOString();
        }
    }
    persistSessionUnread();
    if (typeof syncSessionListIndicatorClasses === 'function') syncSessionListIndicatorClasses();
}

function splitUserMessageVisualLines(text) {
    var raw = text == null ? '' : String(text);
    var physical = raw.split('\n');
    var out = [];
    for (var i = 0; i < physical.length; i += 1) {
        var line = physical[i];
        if (line.length === 0) {
            out.push('');
            continue;
        }
        for (var j = 0; j < line.length; j += USER_MESSAGE_VIRTUAL_LINE_CHARS) {
            out.push(line.slice(j, j + USER_MESSAGE_VIRTUAL_LINE_CHARS));
        }
    }
    return out;
}

function buildUserMessageSummary(text) {
    var lines = splitUserMessageVisualLines(text);
    return lines.slice(0, USER_MESSAGE_COLLAPSE_LINES).join('\n') + '\n...';
}

function userMessageShouldCollapse(text) {
    return false;
}

// The backend appends this short, system-owned decoration when Skills are
// selected. Keep the user's message verbatim, but render the decoration as a
// runtime-owned span so the English UI can translate it without changing the
// stored/user-authored content.
function splitSelectedSkillsUiMessage(text) {
    var source = String(text == null ? '' : text);
    var match = source.match(/\n\n(?:(?:已选择|激活) Skill：|Activated Skill:[ \t]*)([^\n]*)$/);
    if (!match || !String(match[1] || '').trim()) return null;
    return {
        message: source.slice(0, match.index),
        decoration: 'Activated Skill: ' + String(match[1] || '').trim().replace(/、/g, ', '),
    };
}

function renderSelectedSkillsUiMessage(container, text, linkifier) {
    if (!container) return;
    var source = String(text == null ? '' : text);
    var parts = splitSelectedSkillsUiMessage(source);
    container.textContent = '';
    if (!parts) {
        container.textContent = source;
    } else {
        container.appendChild(document.createTextNode(parts.message));
        container.appendChild(document.createTextNode('\n\n'));
        var decoration = document.createElement('span');
        decoration.className = 'user-msg-selected-skills';
        if (typeof setUiRuntimeText === 'function') setUiRuntimeText(decoration, parts.decoration);
        else decoration.textContent = parts.decoration;
        container.appendChild(decoration);
    }
    if (typeof linkifier === 'function') linkifier(container);
}

function renderUserMessageContent(wrap, div, rawStr, linkifier) {
    var applyLinks = typeof linkifier === 'function' ? linkifier : null;

    function setPlain() {
        renderSelectedSkillsUiMessage(div, rawStr, applyLinks);
    }

    function setCollapsed() {
        if (div.classList.contains('is-collapsible')) return;
        wrap.classList.add('has-turn-process');
        div.classList.add('is-collapsible');
        div.textContent = '';
        var sum = document.createElement('div');
        sum.className = 'user-msg-summary';
        renderSelectedSkillsUiMessage(sum, buildUserMessageSummary(rawStr), applyLinks);
        var ful = document.createElement('div');
        ful.className = 'user-msg-full';
        renderSelectedSkillsUiMessage(ful, rawStr, applyLinks);
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
    }

    setPlain();
    requestAnimationFrame(function () {
        if (!div.isConnected || div.classList.contains('is-collapsible')) return;
        var cs = window.getComputedStyle ? window.getComputedStyle(div) : null;
        var lineHeight = cs ? parseFloat(cs.lineHeight) : NaN;
        if (!Number.isFinite(lineHeight) || lineHeight <= 0) {
            var fontSize = cs ? parseFloat(cs.fontSize) : NaN;
            lineHeight = Number.isFinite(fontSize) && fontSize > 0 ? fontSize * 1.65 : 18;
        }
        if (div.scrollHeight > lineHeight * USER_MESSAGE_COLLAPSE_LINES + 1) {
            setCollapsed();
        }
    });
}

function closeUiModal(result) {
    var root = document.getElementById('ui-modal-root');
    if (!root) return;
    root.classList.remove('is-open');
    root.setAttribute('aria-hidden', 'true');
    root.onclick = null;
    root.onpointerdown = null;
    root.onpointerup = null;
    root.onpointercancel = null;
    var okBtn = document.getElementById('ui-modal-ok');
    var cancelBtn = document.getElementById('ui-modal-cancel');
    var inputEl = document.getElementById('ui-modal-input');
    var selectEl = document.getElementById('ui-modal-select');
    var selectControlEl = document.getElementById('ui-modal-select-control');
    var selectTriggerEl = document.getElementById('ui-modal-select-trigger');
    var selectMenuEl = document.getElementById('ui-modal-select-menu');
    if (okBtn) okBtn.onclick = null;
    if (cancelBtn) cancelBtn.onclick = null;
    if (inputEl) inputEl.oninput = null;
    if (selectEl) selectEl.onchange = null;
    if (selectTriggerEl) {
        selectTriggerEl.onclick = null;
        selectTriggerEl.onkeydown = null;
        selectTriggerEl.setAttribute('aria-expanded', 'false');
    }
    if (selectMenuEl) {
        selectMenuEl.hidden = true;
        selectMenuEl.innerHTML = '';
    }
    if (selectControlEl) selectControlEl.classList.remove('is-open');
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
        var inputLabelEl = document.getElementById('ui-modal-input-label');
        var inputEl = document.getElementById('ui-modal-input');
        var inputHintEl = document.getElementById('ui-modal-input-hint');
        var selectLabelEl = document.getElementById('ui-modal-select-label');
        var selectEl = document.getElementById('ui-modal-select');
        var selectControlEl = document.getElementById('ui-modal-select-control');
        var selectTriggerEl = document.getElementById('ui-modal-select-trigger');
        var selectCurrentEl = document.getElementById('ui-modal-select-current');
        var selectCurrentMetaEl = document.getElementById('ui-modal-select-current-meta');
        var selectMenuEl = document.getElementById('ui-modal-select-menu');
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

        var hasSelect = !!(selectEl && selectLabelEl && Array.isArray(o.selectOptions));
        var hasCustomSelect = !!(
            hasSelect && selectControlEl && selectTriggerEl
            && selectCurrentEl && selectCurrentMetaEl && selectMenuEl
        );
        var selectItems = [];
        var selectOptionRows = [];
        var hasInput = !hasSelect && !!(inputEl && inputLabelEl && Object.prototype.hasOwnProperty.call(o, 'inputValue'));
        var inputRequired = hasInput && o.inputRequired !== false;
        if (inputEl && inputLabelEl) {
            inputLabelEl.hidden = !hasInput;
            inputEl.hidden = !hasInput;
            inputLabelEl.textContent = hasInput ? (o.inputLabel || '输入内容') : '';
            inputEl.value = hasInput ? String(o.inputValue == null ? '' : o.inputValue) : '';
            inputEl.placeholder = hasInput ? String(o.inputPlaceholder || '') : '';
            inputEl.maxLength = hasInput && Number(o.inputMaxLength) > 0 ? Number(o.inputMaxLength) : 524288;
            inputEl.removeAttribute('aria-invalid');
        }
        if (inputHintEl) inputHintEl.hidden = !hasInput;
        if (selectEl && selectLabelEl) {
            selectLabelEl.hidden = !hasSelect;
            selectEl.hidden = hasCustomSelect || !hasSelect;
            selectLabelEl.textContent = hasSelect ? (o.selectLabel || '选择一项') : '';
            selectEl.innerHTML = '';
            selectEl.removeAttribute('aria-invalid');
            if (hasSelect) {
                o.selectOptions.forEach(function (item) {
                    var option = document.createElement('option');
                    var normalized = item && typeof item === 'object' ? item : { value: item, label: item };
                    var normalizedItem = {
                        value: String(normalized.value == null ? '' : normalized.value),
                        label: String(normalized.label == null ? normalized.value : normalized.label),
                        meta: String(normalized.meta || ''),
                        title: String(normalized.title || ''),
                        disabled: normalized.disabled === true,
                    };
                    selectItems.push(normalizedItem);
                    option.value = normalizedItem.value;
                    option.textContent = normalizedItem.label;
                    option.disabled = normalizedItem.disabled;
                    if (normalizedItem.title) option.title = normalizedItem.title;
                    selectEl.appendChild(option);
                });
                selectEl.value = String(o.selectValue == null ? '' : o.selectValue);
                if (!selectEl.value && selectEl.options.length) selectEl.selectedIndex = 0;
            }
        }
        if (selectControlEl) {
            selectControlEl.hidden = !hasCustomSelect;
            selectControlEl.classList.remove('is-open');
        }
        if (selectMenuEl) {
            selectMenuEl.hidden = true;
            selectMenuEl.innerHTML = '';
        }

        function setSelectMenuOpen(open) {
            if (!hasCustomSelect) return;
            var nextOpen = !!open;
            selectControlEl.classList.toggle('is-open', nextOpen);
            selectMenuEl.hidden = !nextOpen;
            selectTriggerEl.setAttribute('aria-expanded', nextOpen ? 'true' : 'false');
            if (nextOpen) {
                var selectedRow = selectOptionRows.find(function (row) { return row.getAttribute('aria-selected') === 'true'; });
                var focusRow = selectedRow || selectOptionRows.find(function (row) { return !row.disabled; });
                if (focusRow) requestAnimationFrame(function () { focusRow.focus(); });
            }
        }

        function syncCustomSelect() {
            if (!hasCustomSelect) return;
            var selectedValue = String(selectEl.value || '');
            var selectedItem = selectItems.find(function (item) { return item.value === selectedValue; }) || selectItems[0];
            selectCurrentEl.textContent = selectedItem ? selectedItem.label : '';
            selectCurrentMetaEl.textContent = selectedItem ? selectedItem.meta : '';
            selectOptionRows.forEach(function (row) {
                var selected = row.dataset.value === selectedValue;
                row.classList.toggle('is-selected', selected);
                row.setAttribute('aria-selected', selected ? 'true' : 'false');
            });
        }

        function commitSelectValue(value, closeMenu) {
            if (!hasSelect) return;
            selectEl.value = String(value || '');
            syncCustomSelect();
            syncInputValidity();
            if (closeMenu) {
                setSelectMenuOpen(false);
                selectTriggerEl.focus();
            }
        }

        function moveSelectFocus(row, delta) {
            var enabledRows = selectOptionRows.filter(function (item) { return !item.disabled; });
            var index = enabledRows.indexOf(row);
            if (!enabledRows.length) return;
            var nextIndex = index < 0 ? 0 : (index + delta + enabledRows.length) % enabledRows.length;
            enabledRows[nextIndex].focus();
        }

        if (hasCustomSelect) {
            selectItems.forEach(function (item, index) {
                var row = document.createElement('button');
                row.type = 'button';
                row.id = 'ui-modal-select-option-' + index;
                row.className = 'ui-modal-select-option';
                row.setAttribute('role', 'option');
                row.dataset.value = item.value;
                row.disabled = item.disabled;
                if (item.title) row.title = item.title;

                var copy = document.createElement('span');
                copy.className = 'ui-modal-select-option-copy';
                var name = document.createElement('span');
                name.className = 'ui-modal-select-option-name';
                name.textContent = item.label;
                var meta = document.createElement('span');
                meta.className = 'ui-modal-select-option-meta';
                meta.textContent = item.meta;
                copy.appendChild(name);
                if (item.meta) copy.appendChild(meta);
                var check = document.createElement('span');
                check.className = 'ui-modal-select-option-check';
                check.textContent = '✓';
                check.setAttribute('aria-hidden', 'true');
                row.appendChild(copy);
                row.appendChild(check);
                row.onclick = function (event) {
                    event.stopPropagation();
                    commitSelectValue(item.value, true);
                };
                row.onkeydown = function (event) {
                    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                        event.preventDefault(); event.stopPropagation();
                        moveSelectFocus(row, event.key === 'ArrowDown' ? 1 : -1);
                    } else if (event.key === 'Home' || event.key === 'End') {
                        event.preventDefault(); event.stopPropagation();
                        var enabledRows = selectOptionRows.filter(function (optionRow) { return !optionRow.disabled; });
                        var targetRow = event.key === 'Home' ? enabledRows[0] : enabledRows[enabledRows.length - 1];
                        if (targetRow) targetRow.focus();
                    } else if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault(); event.stopPropagation();
                        commitSelectValue(item.value, true);
                    } else if (event.key === 'Escape') {
                        event.preventDefault(); event.stopPropagation();
                        setSelectMenuOpen(false);
                        selectTriggerEl.focus();
                    }
                };
                selectOptionRows.push(row);
                selectMenuEl.appendChild(row);
            });
            selectTriggerEl.onclick = function (event) {
                event.stopPropagation();
                setSelectMenuOpen(selectMenuEl.hidden);
            };
            selectTriggerEl.onkeydown = function (event) {
                if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault(); event.stopPropagation();
                    setSelectMenuOpen(true);
                }
            };
            syncCustomSelect();
        }

        var danger = !!o.danger;
        iconEl.className = 'ui-modal__icon ' + (danger ? 'ui-modal__icon--danger' : 'ui-modal__icon--info');
        iconEl.innerHTML = danger ? UI_MODAL_SVG_TRASH : UI_MODAL_SVG_INFO;

        okBtn.className = 'ui-modal-btn ' + (danger ? 'ui-modal-btn--danger' : 'ui-modal-btn--primary');

        function syncInputValidity() {
            if (!hasInput && !hasSelect) return;
            var invalid = hasSelect ? !String(selectEl.value || '').trim() : (inputRequired && !hasSendableText(inputEl.value));
            okBtn.disabled = invalid;
            if (hasInput) inputEl.setAttribute('aria-invalid', invalid ? 'true' : 'false');
            if (hasSelect) selectEl.setAttribute('aria-invalid', invalid ? 'true' : 'false');
            if (hasCustomSelect) selectTriggerEl.setAttribute('aria-invalid', invalid ? 'true' : 'false');
        }
        function onOk() {
            if (hasSelect) {
                var selectedValue = String(selectEl.value || '').trim();
                if (!selectedValue) {
                    syncInputValidity();
                    (hasCustomSelect ? selectTriggerEl : selectEl).focus();
                    return;
                }
                closeUiModal(selectedValue);
                return;
            }
            if (hasInput) {
                var value = normalizeSendableText(inputEl.value);
                if (inputRequired && !value) {
                    syncInputValidity();
                    inputEl.focus();
                    return;
                }
                closeUiModal(value);
                return;
            }
            closeUiModal(true);
        }
        function onCancel() { closeUiModal(false); }
        okBtn.onclick = onOk;
        cancelBtn.onclick = onCancel;
        okBtn.disabled = false;
        if (inputEl) inputEl.oninput = syncInputValidity;
        if (selectEl) selectEl.onchange = function () { syncCustomSelect(); syncInputValidity(); };
        syncInputValidity();
        var backdropPressStarted = false;
        var backdropPressCompleted = false;
        root.onpointerdown = function (e) {
            backdropPressStarted = e.target === root;
            backdropPressCompleted = false;
        };
        root.onpointerup = function (e) {
            backdropPressCompleted = backdropPressStarted && e.target === root;
        };
        root.onpointercancel = function () {
            backdropPressStarted = false;
            backdropPressCompleted = false;
        };
        root.onclick = function (e) {
            if (e.target === root && backdropPressCompleted) onCancel();
            else if (hasCustomSelect && !selectControlEl.contains(e.target)) setSelectMenuOpen(false);
            backdropPressStarted = false;
            backdropPressCompleted = false;
        };

        uiModalKeyHandler = function (e) {
            if (e.key === 'Escape') {
                e.preventDefault();
                if (hasCustomSelect && !selectMenuEl.hidden) {
                    setSelectMenuOpen(false);
                    selectTriggerEl.focus();
                } else {
                    onCancel();
                }
            }
            else if (isInputSubmitShortcut(e, 'single-line') && document.activeElement !== cancelBtn) {
                e.preventDefault();
                onOk();
            }
        };
        document.addEventListener('keydown', uiModalKeyHandler);

        root.classList.add('is-open');
        root.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';
        requestAnimationFrame(function () {
            if (hasSelect) {
                (hasCustomSelect ? selectTriggerEl : selectEl).focus();
            } else if (hasInput) {
                inputEl.focus();
                inputEl.select();
            } else {
                okBtn.focus();
            }
        });
    });
}

// Trusted chat extensions use the same accessible confirmation surface as the
// built-in UI. Only the host controls which bundled modules can load in-page.
globalThis.openMyAgentUiModal = openUiModal;

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
