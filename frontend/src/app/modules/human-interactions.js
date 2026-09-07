const humanInteractionStoreBySession = Object.create(null);
const HUMAN_INTERACTION_DRAFT_PREFIX = 'myagent-human-interaction-draft:';

function humanInteractionSessionState(sessionId) {
    var sid = String(sessionId || '');
    if (!humanInteractionStoreBySession[sid]) {
        humanInteractionStoreBySession[sid] = {
            interactions: Object.create(null),
            approvals: Object.create(null),
            loaded: false,
            refreshEpoch: 0,
        };
    }
    return humanInteractionStoreBySession[sid];
}

function isHumanInteractionEventType(type) {
    var t = String(type || '');
    return t.indexOf('interaction_') === 0 || t.indexOf('approval_') === 0;
}

function humanInteractionKindForEvent(event) {
    return String((event && event.type) || '').indexOf('approval_') === 0 ? 'approval' : 'question';
}

function humanInteractionId(event, kind) {
    return String(kind === 'approval' ? (event.approval_id || '') : (event.interaction_id || ''));
}

function humanInteractionStatusFromEvent(event) {
    var explicit = String((event && event.status) || '');
    if (explicit) return explicit;
    var type = String((event && event.type) || '');
    if (type.endsWith('_resolved')) return 'resolved';
    if (type.endsWith('_cancelled')) return 'cancelled';
    if (type.endsWith('_expired')) return 'expired';
    return 'pending';
}

function applyHumanInteractionEvent(sessionId, event) {
    if (!event || !isHumanInteractionEventType(event.type)) return null;
    var sid = String(sessionId || event.session_id || '');
    if (!sid) return null;
    var kind = humanInteractionKindForEvent(event);
    var id = humanInteractionId(event, kind);
    if (!id) return null;
    var state = humanInteractionSessionState(sid);
    state.refreshEpoch += 1;
    var collection = kind === 'approval' ? state.approvals : state.interactions;
    var previous = collection[id] || {};
    var terminalStatuses = { resolved: true, cancelled: true, expired: true };
    var incomingStatus = humanInteractionStatusFromEvent(event);
    var previousVersion = Number(previous.request_version || 0);
    var incomingVersion = Number(event.request_version || previousVersion || 0);
    if (previousVersion && incomingVersion && incomingVersion < previousVersion) return previous;
    if (terminalStatuses[previous.status] && incomingStatus === 'pending') return previous;
    var record = Object.assign({}, previous, event, {
        kind: kind,
        status: incomingStatus,
    });
    collection[id] = record;
    state.loaded = true;
    syncHumanInteractionSessionSummary(sid);
    updateHumanInteractionBanner(currentSessionId);
    return record;
}

function pendingHumanInteractionRecords(sessionId) {
    var state = humanInteractionSessionState(sessionId);
    var rows = [];
    Object.keys(state.interactions).forEach(function (id) {
        var row = state.interactions[id];
        if (row && row.status === 'pending') rows.push(row);
    });
    Object.keys(state.approvals).forEach(function (id) {
        var row = state.approvals[id];
        if (row && row.status === 'pending') rows.push(row);
    });
    rows.sort(function (a, b) {
        var kindOrder = (a.kind === 'approval' ? 0 : 1) - (b.kind === 'approval' ? 0 : 1);
        if (kindOrder) return kindOrder;
        return String(a.created_at || '').localeCompare(String(b.created_at || ''));
    });
    return rows;
}

function humanInteractionPendingCounts(sessionId) {
    var rows = pendingHumanInteractionRecords(sessionId);
    var questions = rows.filter(function (row) { return row.kind === 'question'; }).length;
    return { questions: questions, approvals: rows.length - questions, total: rows.length };
}

function pendingHumanQuestions(sessionId) {
    return pendingHumanInteractionRecords(sessionId).filter(function (row) { return row.kind === 'question'; });
}

async function confirmAndCancelPendingHumanQuestionsForHistoryMutation(sessionId) {
    var sid = String(sessionId || '');
    var rows = pendingHumanQuestions(sid);
    if (!rows.length) return true;
    var confirmed = typeof openUiModal === 'function'
        ? await openUiModal({
            title: '修改历史并取消待回答问题？',
            message: '这次修改会移除当前问题所属的对话历史。继续前必须先取消待回答问题，避免它变成无法处理的待办。',
            confirmText: '取消问题并继续',
            cancelText: '返回回答问题',
        })
        : false;
    if (!confirmed) return false;
    try {
        var resolved = await Promise.all(rows.map(async function (row) {
            var response = await fetch('/sessions/' + encodeURIComponent(sid) + '/interactions/' + encodeURIComponent(row.interaction_id) + '/cancel', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ reason: 'superseded_by_history_mutation' }),
            });
            var data = await response.json();
            if (!response.ok || !data.ok) throw new Error(data.error || ('HTTP ' + response.status));
            return data.interaction || row;
        }));
        resolved.forEach(function (row) {
            clearHumanInteractionDraft(sid, row.interaction_id, row.request_version);
            var record = applyHumanInteractionEvent(sid, Object.assign({ type: 'interaction_cancelled' }, row));
            renderHumanInteractionRecord(record, sid);
        });
        return true;
    } catch (err) {
        if (typeof showUiAlert === 'function') {
            showUiAlert({
                title: '无法修改历史',
                message: '取消待回答问题失败：' + String(err && err.message ? err.message : err),
                variant: 'error',
            });
        }
        return false;
    }
}

function syncHumanInteractionSessionSummary(sessionId) {
    var sid = String(sessionId || '');
    var counts = humanInteractionPendingCounts(sid);
    var session = typeof sessionStore !== 'undefined' ? sessionStore.get(sid) : null;
    if (session) session.pending_human_interactions = counts;
    updateHumanInteractionSessionBadge(sid);
    updateHumanInteractionBanner(currentSessionId);
    if (typeof renderFollowupQueue === 'function') renderFollowupQueue(sid);
}

function sessionPendingHumanCounts(sessionId) {
    var sid = String(sessionId || '');
    var state = humanInteractionStoreBySession[sid];
    if (state && state.loaded) return humanInteractionPendingCounts(sid);
    var session = typeof sessionStore !== 'undefined' ? sessionStore.get(sid) : null;
    var pending = session && session.pending_human_interactions;
    var questions = Math.max(0, Number(pending && pending.questions) || 0);
    var approvals = Math.max(0, Number(pending && pending.approvals) || 0);
    var total = Math.max(questions + approvals, Number(pending && pending.total) || 0);
    return { questions: questions, approvals: approvals, total: total };
}

function sessionListForPendingCounts() {
    if (typeof sessionStore !== 'undefined' && sessionStore && typeof sessionStore.list === 'function') {
        return sessionStore.list();
    }
    return [];
}

function globalHumanInteractionPendingCounts() {
    var questions = 0;
    var approvals = 0;
    sessionListForPendingCounts().forEach(function (session) {
        if (!session || !session.id) return;
        var counts = sessionPendingHumanCounts(session.id);
        questions += counts.questions;
        approvals += counts.approvals;
    });
    return { questions: questions, approvals: approvals, total: questions + approvals };
}

function firstSessionWithPendingHumanInteractions() {
    var sessions = sessionListForPendingCounts();
    for (var i = 0; i < sessions.length; i += 1) {
        var session = sessions[i];
        if (session && session.id && sessionPendingHumanCounts(session.id).total > 0) return session;
    }
    return null;
}

function pendingCountDetailText(counts) {
    var parts = [];
    if (counts.approvals > 0) parts.push(counts.approvals + ' 个审批');
    if (counts.questions > 0) parts.push(counts.questions + ' 个回答');
    return parts.join('、') || '无待办';
}

function updateHumanInteractionSessionBadge(sessionId) {
    var sid = String(sessionId || '');
    if (!sid || !sessionsList) return;
    var row = sessionsList.querySelector('.session-item[data-session-id="' + (window.CSS && CSS.escape ? CSS.escape(sid) : sid.replace(/"/g, '\\"')) + '"]');
    if (!row) return;
    var head = row.querySelector('.session-item-head');
    if (!head) return;
    var badge = head.querySelector('.session-human-badge');
    var counts = sessionPendingHumanCounts(sid);
    var count = counts.total;
    if (count <= 0) {
        if (badge) badge.remove();
        row.classList.remove('has-human-pending');
        return;
    }
    if (!badge && count > 0) {
        badge = document.createElement('span');
        badge.className = 'session-human-badge';
        badge.setAttribute('aria-label', '待处理的人机交互');
        var more = head.querySelector('.session-more-wrap');
        head.insertBefore(badge, more || null);
    }
    if (badge) {
        var hasQuestions = counts.questions > 0;
        var hasApprovals = counts.approvals > 0;
        badge.textContent = hasQuestions && hasApprovals
            ? String(count)
            : ((hasQuestions ? '?' : '!') + (count > 1 ? String(count) : ''));
        var badgeLabel = hasQuestions && hasApprovals
            ? ('有 ' + count + ' 项待处理')
            : (hasQuestions ? ('有 ' + count + ' 个问题待回答') : ('有 ' + count + ' 个审批待处理'));
        badge.setAttribute('aria-label', badgeLabel);
        badge.setAttribute('data-ui-tip', badgeLabel);
        if (typeof bindUiHoverTip === 'function') bindUiHoverTip(badge);
    }
    row.classList.add('has-human-pending');
}

function updateAllHumanInteractionSessionBadges() {
    if (!sessionsList) return;
    sessionsList.querySelectorAll('.session-item[data-session-id]').forEach(function (row) {
        updateHumanInteractionSessionBadge(row.dataset.sessionId || '');
    });
    updateHumanInteractionBanner(currentSessionId);
}

function updateHumanInteractionBanner(sessionId) {
    var sid = String(sessionId || currentSessionId || '');
    var banner = document.getElementById('human-interaction-banner');
    if (!banner) return;
    var globalCounts = globalHumanInteractionPendingCounts();
    var sessionCounts = sid ? sessionPendingHumanCounts(sid) : { questions: 0, approvals: 0, total: 0 };
    var visible = globalCounts.total > 0;
    banner.classList.toggle('is-on', visible);
    banner.classList.toggle('hidden', !visible);
    var globalCountEl = banner.querySelector('.human-todo-count[data-scope="global"]');
    var globalDetailEl = banner.querySelector('.human-todo-detail[data-scope="global"]');
    var sessionCountEl = banner.querySelector('.human-todo-count[data-scope="session"]');
    var sessionDetailEl = banner.querySelector('.human-todo-detail[data-scope="session"]');
    if (globalCountEl) globalCountEl.textContent = globalCounts.total + ' 项';
    if (globalDetailEl) globalDetailEl.textContent = pendingCountDetailText(globalCounts);
    if (sessionCountEl) sessionCountEl.textContent = sessionCounts.total + ' 项';
    if (sessionDetailEl) sessionDetailEl.textContent = pendingCountDetailText(sessionCounts);
}

function focusFirstPendingHumanInteraction() {
    var stream = typeof getVisibleChatStream === 'function' ? getVisibleChatStream() : document.getElementById('chat-stream');
    var card = stream && stream.querySelector('.human-interaction-card[data-status="pending"]');
    if (!card) return;
    var needsLayout = false;
    var collapsedRow = card.closest ? card.closest('.feed-item.is-collapsed') : null;
    if (collapsedRow) {
        collapsedRow.classList.remove('is-collapsed');
        collapsedRow.dataset.manualToggle = '1';
        var rowBtn = collapsedRow.querySelector('.feed-row-collapse');
        if (rowBtn) {
            rowBtn.setAttribute('aria-expanded', 'true');
            rowBtn.setAttribute('aria-label', '收起工具行');
        }
        needsLayout = true;
    }
    var collapsedAgg = card.closest ? card.closest('.process-aggregate.is-collapsed') : null;
    if (collapsedAgg) {
        collapsedAgg.classList.remove('is-collapsed');
        var aggTop = collapsedAgg.querySelector('.process-aggregate-top');
        if (aggTop) aggTop.setAttribute('aria-expanded', 'true');
        needsLayout = true;
    }
    if (needsLayout && collapsedAgg) {
        requestAnimationFrame(function () {
            requestAnimationFrame(function () {
                if (typeof syncProcessAggregateHeightUi === 'function') syncProcessAggregateHeightUi(collapsedAgg);
                collapsedAgg.querySelectorAll('.process-aggregate-body .feed-chunk').forEach(function (ch) {
                    if (typeof refreshFeedChunkOverflow === 'function') refreshFeedChunkOverflow(ch);
                });
                if (typeof registerMermaidLazy === 'function') registerMermaidLazy(collapsedAgg);
            });
        });
    }
    requestAnimationFrame(function () {
        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        var focusTarget = card.querySelector('input:not(:disabled), textarea:not(:disabled), button:not(:disabled)');
        if (focusTarget) focusTarget.focus({ preventScroll: true });
        else {
            card.setAttribute('tabindex', '-1');
            card.focus({ preventScroll: true });
        }
    });
    card.classList.add('is-highlighted');
    setTimeout(function () { card.classList.remove('is-highlighted'); }, 1200);
}

async function handleHumanTodoFloaterAction() {
    var current = String(currentSessionId || '');
    var currentCounts = current ? sessionPendingHumanCounts(current) : { total: 0 };
    if (currentCounts.total > 0) {
        focusFirstPendingHumanInteraction();
        return;
    }
    var target = firstSessionWithPendingHumanInteractions();
    if (!target) return;
    if (typeof switchSession === 'function') {
        await switchSession(target.id, { forceReload: false });
    }
    requestAnimationFrame(function () { focusFirstPendingHumanInteraction(); });
}

function humanInteractionDraftKey(sessionId, interactionId, requestVersion) {
    return HUMAN_INTERACTION_DRAFT_PREFIX + String(sessionId || '') + ':' + String(interactionId || '') + ':' + String(requestVersion || 1);
}

function humanInteractionToolSlot(stream, toolCallId) {
    var tid = String(toolCallId || '');
    if (!stream || !tid || typeof CSS === 'undefined' || !CSS.escape) return null;
    var row = null;
    try {
        row = stream.querySelector('.feed-item.feed--tool[data-tool-call-id="' + CSS.escape(tid) + '"]');
    } catch (e) { row = null; }
    if (!row) return null;
    var slot = row.querySelector('.human-interaction-tool-slot');
    if (!slot) {
        slot = document.createElement('div');
        slot.className = 'human-interaction-tool-slot';
        row.appendChild(slot);
    }
    return slot;
}

function attachHumanInteractionCardsForToolCall(stream, toolCallId) {
    var tid = String(toolCallId || '');
    var slot = humanInteractionToolSlot(stream, tid);
    if (!slot) return false;
    var escaped = (window.CSS && CSS.escape) ? CSS.escape(tid) : tid.replace(/"/g, '\\"');
    var cards = Array.from(stream.querySelectorAll('.human-interaction-card[data-tool-call-id="' + escaped + '"]'));
    cards.forEach(function (card) {
        if (card.parentNode !== slot) slot.appendChild(card);
    });
    return true;
}

function attachAllHumanInteractionCards(stream) {
    if (!stream || !stream.querySelectorAll) return;
    Array.from(stream.querySelectorAll('.human-interaction-card[data-tool-call-id]')).forEach(function (card) {
        var tid = card.getAttribute('data-tool-call-id') || '';
        if (!tid) return;
        var slot = humanInteractionToolSlot(stream, tid);
        if (slot && card.parentNode !== slot) slot.appendChild(card);
    });
}

function ensurePendingQuestionToolRow(ctx, record, sessionId) {
    if (!record || record.kind === 'approval' || record.status !== 'pending') return false;
    var toolCallId = String(record.tool_call_id || '');
    var stream = ctx && ctx.stream ? ctx.stream : null;
    if (!toolCallId || !stream || typeof appendToolPendingRow !== 'function') return false;
    var existing = null;
    if (typeof CSS !== 'undefined' && CSS.escape) {
        try {
            existing = stream.querySelector('.feed-item.feed--tool[data-tool-call-id="' + CSS.escape(toolCallId) + '"]');
        } catch (e) { existing = null; }
    }
    if (!existing) {
        appendToolPendingRow(ctx, {
            type: 'tool_pending',
            ephemeral: true,
            tool: 'ask_user',
            args: { questions: record.questions || [] },
            command_preview: 'ask_user',
            tool_call_id: toolCallId,
        }, sessionId);
    }
    return true;
}

function autoReviewStatusElement(stream, toolCallId) {
    var slot = humanInteractionToolSlot(stream, toolCallId);
    if (!slot) return null;
    var el = slot.querySelector('.auto-review-status');
    if (!el) {
        el = humanElement('div', 'auto-review-status');
        slot.insertBefore(el, slot.firstChild);
    }
    return el;
}

function interceptReasonFromReviewText(interceptReason, fallbackReason) {
    var text = String(interceptReason || '').trim();
    if (text) return text;
    // 后端 reason 字段形如 "【拦截原因】…\n【命令风险】…\n【命令目的】…"。
    // 旧事件未携带独立 intercept_reason 时，从这里把拦截原因摘出来。
    var whole = String(fallbackReason || '').trim();
    var match = /【拦截原因】\s*([\s\S]*?)(?:\n【命令风险】|$)/.exec(whole);
    if (match) {
        var extracted = match[1].trim();
        if (extracted) return extracted;
    }
    return '';
}

function splitReviewExplanationText(interceptReason, riskAnalysis, commandPurpose, fallbackReason) {
    // 把 review.reason / subtitle 这类 "【拦截原因】…\n【命令风险】…\n【命令目的】…"
    // 文本拆成三段；已提供的独立字段优先。
    var sections = { intercept: '', risk: '', purpose: '' };
    var whole = String(fallbackReason || '').trim();
    var interceptMatch = /【拦截原因】\s*([\s\S]*?)(?:\n【命令风险】|$)/.exec(whole);
    var riskMatch = /【命令风险】\s*([\s\S]*?)(?:\n【命令目的】|$)/.exec(whole);
    var purposeMatch = /【命令目的】\s*([\s\S]*)$/.exec(whole);
    sections.intercept = String(interceptReason || '').trim()
        || (interceptMatch && interceptMatch[1].trim() || '');
    sections.risk = String(riskAnalysis || '').trim()
        || (riskMatch && riskMatch[1].trim() || '');
    sections.purpose = String(commandPurpose || '').trim()
        || (purposeMatch && purposeMatch[1].trim() || '');
    return sections;
}

function appendApprovalReviewExplanation(container, interceptReason, riskAnalysis, commandPurpose, fallbackReason) {
    if (!container) return;
    var interceptText = interceptReasonFromReviewText(interceptReason, fallbackReason);
    var riskText = String(riskAnalysis || fallbackReason || '未提供具体风险说明。').trim();
    var purposeText = String(commandPurpose || '未提供命令用途说明。').trim();
    var explanation = humanElement('div', 'approval-review-explanation');
    if (interceptText) {
        var interceptRow = humanElement('div', 'approval-review-section');
        interceptRow.appendChild(humanElement('strong', 'approval-review-section-label', '【拦截原因】'));
        interceptRow.appendChild(humanElement('span', 'approval-review-section-text', interceptText));
        explanation.appendChild(interceptRow);
    }
    var riskRow = humanElement('div', 'approval-review-section');
    riskRow.appendChild(humanElement('strong', 'approval-review-section-label', '【命令风险】'));
    riskRow.appendChild(humanElement('span', 'approval-review-section-text', riskText));
    explanation.appendChild(riskRow);
    var purposeRow = humanElement('div', 'approval-review-section');
    purposeRow.appendChild(humanElement('strong', 'approval-review-section-label', '【命令目的】'));
    purposeRow.appendChild(humanElement('span', 'approval-review-section-text', purposeText));
    explanation.appendChild(purposeRow);
    container.appendChild(explanation);
}

function renderAutoReviewStatusEvent(ctx, event, runSessionId) {
    var stream = ctx && ctx.stream
        ? ctx.stream
        : (typeof getVisibleChatStream === 'function'
            ? getVisibleChatStream()
            : document.getElementById('chat-stream'));
    var tid = String((event && event.tool_call_id) || '');
    var status = String((event && event.status) || '');
    if (!stream || !tid) {
        var fallback = String((event && event.content) || '');
        if (fallback && typeof appendLog === 'function') appendLog(ctx, fallback, 'status', runSessionId);
        return;
    }
    var el = autoReviewStatusElement(stream, tid);
    if (!el) return;
    el.className = 'auto-review-status';
    el.setAttribute('data-status', status);
    // Status events update one persistent row. Clear the previous loading or
    // result content before rendering the new state so in-progress copy and
    // its spinner do not remain beside the final decision.
    el.textContent = '';
    if (status === 'in_progress') {
        el.classList.add('is-in-progress');
        el.appendChild(humanElement('span', 'auto-review-spin'));
        el.appendChild(humanElement(
            'span',
            'auto-review-text',
            '自动审查中：审查 Agent 正在核对你的任务意图与请求风险。'
        ));
        return;
    }
    var approved = status === 'approved';
    var risk = String((event && event.risk) || 'unknown');
    var reason = String((event && event.reason) || '');
    var interceptReason = String((event && event.intercept_reason) || '');
    var riskAnalysis = String((event && event.risk_analysis) || '');
    var commandPurpose = String((event && event.command_purpose) || '');
    var unknown = risk === 'unknown' || risk === 'timed_out';
    el.classList.add(approved ? 'is-approved' : (unknown ? 'is-timedout' : 'is-denied'));
    var text = humanElement('div', 'auto-review-text');
    var title = humanElement(
        'span',
        'auto-review-title',
        approved
            ? '自动审批已批准'
            : (unknown ? '自动审查不可用（已转人工确认）' : '自动审批已拒绝')
    );
    if (!approved && !unknown) {
        title.appendChild(humanElement('span', 'auto-review-risk', risk));
    }
    text.appendChild(title);
    appendApprovalReviewExplanation(text, interceptReason, riskAnalysis, commandPurpose, reason);
    if (!approved && !unknown) {
        text.appendChild(humanElement(
            'div',
            'auto-review-hint',
            '可人工覆盖本次请求（只此一次，不沉淀规则）'
        ));
    }
    el.appendChild(text);
}

function persistHumanInteractionDraft(card) {
    if (!card || card.dataset.kind !== 'question') return;
    var draft = { selections: {}, others: {}, skipped: {}, step: Number(card.dataset.step || 0), updatedAt: Date.now() };
    card.querySelectorAll('.human-question-pane').forEach(function (pane) {
        var qid = pane.dataset.questionId || '';
        draft.selections[qid] = Array.from(pane.querySelectorAll('input[data-option-id]:checked')).map(function (input) {
            return input.dataset.optionId;
        });
        var other = pane.querySelector('.human-other-input');
        draft.others[qid] = other ? other.value : '';
        draft.skipped[qid] = pane.dataset.skipped === '1';
    });
    try { sessionStorage.setItem(humanInteractionDraftKey(card.dataset.sessionId, card.dataset.interactionId, card.dataset.requestVersion), JSON.stringify(draft)); } catch (e) { /* ignore */ }
}

function restoreHumanInteractionDraft(card) {
    if (!card || card.dataset.kind !== 'question') return null;
    var draft = null;
    try { draft = JSON.parse(sessionStorage.getItem(humanInteractionDraftKey(card.dataset.sessionId, card.dataset.interactionId, card.dataset.requestVersion)) || 'null'); } catch (e) { draft = null; }
    if (!draft) return null;
    card.querySelectorAll('.human-question-pane').forEach(function (pane) {
        var qid = pane.dataset.questionId || '';
        var selected = (draft.selections && draft.selections[qid]) || [];
        pane.querySelectorAll('input[data-option-id]').forEach(function (input) {
            input.checked = selected.indexOf(input.dataset.optionId) >= 0;
        });
        var other = pane.querySelector('.human-other-input');
        if (other && draft.others) {
            other.value = draft.others[qid] || '';
            var otherMark = pane.querySelector('.human-other-mark');
            if (otherMark && other.value) otherMark.checked = true;
        }
        pane.dataset.skipped = draft.skipped && draft.skipped[qid] ? '1' : '0';
    });
    return draft;
}

function clearHumanInteractionDraft(sessionId, interactionId, requestVersion) {
    try { sessionStorage.removeItem(humanInteractionDraftKey(sessionId, interactionId, requestVersion)); } catch (e) { /* ignore */ }
}

function humanElement(tag, className, text) {
    var el = document.createElement(tag);
    if (className) el.className = className;
    if (text != null) el.textContent = String(text);
    return el;
}

function appendHumanCardHeader(card, record, kind) {
    var head = humanElement('div', 'human-card-head');
    var icon = humanElement('span', 'human-card-icon', kind === 'approval' ? '!' : '?');
    icon.setAttribute('aria-hidden', 'true');
    var copy = humanElement('div', 'human-card-head-copy');
    copy.appendChild(humanElement('div', 'human-card-kicker', kind === 'approval' ? '安全审批' : '需要你的回答'));
    var title = humanElement('h3', 'human-card-title', kind === 'approval'
        ? (record.title || 'Agent 请求执行操作')
        : ((record.questions && record.questions.length > 1) ? (record.questions.length + ' 个问题待确认') : ((record.questions && record.questions[0] && record.questions[0].header) || '确认下一步')));
    var recordId = String(kind === 'approval' ? (record.approval_id || '') : (record.interaction_id || ''));
    title.id = 'human-card-title-' + recordId.replace(/[^a-zA-Z0-9_-]/g, '-');
    copy.appendChild(title);
    card.setAttribute('aria-labelledby', title.id);
    var statusText = record.status === 'pending'
        ? (kind === 'approval' ? '待审批' : '待回答')
        : ({ resolved: kind === 'approval' ? '已处理' : '已回答', cancelled: '已取消', expired: '已过期' }[record.status] || record.status);
    var status = humanElement('span', 'human-card-status', statusText);
    head.appendChild(icon);
    head.appendChild(copy);
    head.appendChild(status);
    card.appendChild(head);
}

function applyHumanQuestionTerminalAnswers(card, record) {
    var answersByQuestion = Object.create(null);
    (Array.isArray(record.answers) ? record.answers : []).forEach(function (answer) {
        answersByQuestion[String(answer.question_id || '')] = answer;
    });
    card.querySelectorAll('.human-question-pane').forEach(function (pane) {
        var answer = answersByQuestion[String(pane.dataset.questionId || '')] || {};
        var selectedIds = (answer.selected_option_ids || []).map(String);
        var selectedLabels = (answer.selected_labels || []).map(String);
        pane.querySelectorAll('input[data-option-id]').forEach(function (input) {
            var label = input.closest('.human-option');
            var labelText = label && label.querySelector('.human-option-label');
            input.checked = selectedIds.indexOf(String(input.dataset.optionId || '')) >= 0
                || (!selectedIds.length && selectedLabels.indexOf(String((labelText && labelText.textContent) || '')) >= 0);
        });
        var otherText = String(answer.other_text || '');
        var otherMark = pane.querySelector('.human-other-mark');
        var otherInput = pane.querySelector('.human-other-input');
        if (otherMark) otherMark.checked = !!otherText;
        if (otherInput) otherInput.value = otherText;
        pane.dataset.skipped = answer.skipped ? '1' : '0';
    });
}

function humanQuestionPaneState(pane) {
    var selected = Array.from(pane.querySelectorAll('input[data-option-id]:checked'));
    var otherMark = pane.querySelector('.human-other-mark');
    var otherInput = pane.querySelector('.human-other-input');
    var otherSelected = !!(otherMark && otherMark.checked);
    var otherText = otherSelected && otherInput ? normalizeSendableText(otherInput.value) : '';
    var skipped = pane.dataset.skipped === '1';
    return {
        selected: selected,
        otherSelected: otherSelected,
        otherText: otherText,
        answered: selected.length > 0 || !!otherText,
        invalidOther: otherSelected && !otherText,
        skipped: skipped,
    };
}

function validateHumanQuestionPane(card, pane) {
    var error = card.querySelector('.human-card-error');
    var state = humanQuestionPaneState(pane);
    if (state.skipped) {
        if (error) error.textContent = '';
        return true;
    }
    if (state.invalidOther) {
        if (error) error.textContent = '请输入其他答案。';
        var other = pane.querySelector('.human-other-input');
        if (other) other.focus();
        return false;
    }
    if (!state.answered) {
        if (error) error.textContent = pane.querySelector('input[type="checkbox"]') ? '请至少选择一个选项。' : '请选择一个选项。';
        var firstControl = pane.querySelector('input');
        if (firstControl) firstControl.focus();
        return false;
    }
    if (error) error.textContent = '';
    return true;
}

function isHumanQuestionPaneComplete(pane) {
    var state = humanQuestionPaneState(pane);
    return state.skipped || (state.answered && !state.invalidOther);
}

function allHumanQuestionsComplete(card) {
    var panes = Array.from(card.querySelectorAll('.human-question-pane'));
    return panes.length > 0 && panes.every(isHumanQuestionPaneComplete);
}

function nextIncompleteHumanQuestionIndex(panes, current) {
    for (var offset = 1; offset <= panes.length; offset += 1) {
        var index = (current + offset) % panes.length;
        if (!isHumanQuestionPaneComplete(panes[index])) return index;
    }
    return current;
}

function confirmCurrentHumanQuestion(card) {
    var panes = Array.from(card.querySelectorAll('.human-question-pane'));
    var current = Number(card.dataset.step || 0);
    var pane = panes[current];
    if (!pane || !validateHumanQuestionPane(card, pane)) return;
    setHumanQuestionStep(card, nextIncompleteHumanQuestionIndex(panes, current));
}

function setHumanQuestionStep(card, index) {
    var panes = Array.from(card.querySelectorAll('.human-question-pane'));
    if (!panes.length) return;
    var next = Math.max(0, Math.min(Number(index) || 0, panes.length - 1));
    card.dataset.step = String(next);
    panes.forEach(function (pane, idx) { pane.classList.toggle('is-active', idx === next); });
    card.querySelectorAll('.human-question-tab').forEach(function (tab, idx) {
        var state = humanQuestionPaneState(panes[idx]);
        tab.classList.toggle('is-active', idx === next);
        tab.classList.toggle('is-answered', state.answered);
        tab.classList.toggle('is-skipped', state.skipped);
        tab.setAttribute('aria-selected', idx === next ? 'true' : 'false');
        tab.setAttribute('tabindex', idx === next ? '0' : '-1');
    });
    var tabs = card.querySelector('.human-question-tabs');
    if (tabs) tabs.classList.remove('hidden');
    var body = card.querySelector('.human-card-body');
    if (body) body.classList.remove('hidden');
    var progress = card.querySelector('.human-question-progress');
    if (progress) progress.textContent = '问题 ' + (next + 1) + '/' + panes.length + ' · ' + String(panes[next].dataset.questionHeader || '');
    var back = card.querySelector('.human-back-btn');
    var confirmBtn = card.querySelector('.human-confirm-btn');
    var multipleQuestions = panes.length > 1;
    var allComplete = panes.every(isHumanQuestionPaneComplete);
    if (back) {
        back.textContent = '上一题';
        back.classList.toggle('hidden', !multipleQuestions);
        back.disabled = next === 0;
    }
    // 单按钮语义：未全部回答时是「确认」，全部回答完后变为「提交答案」
    if (confirmBtn) {
        confirmBtn.textContent = allComplete ? '提交答案' : '确认';
        confirmBtn.classList.toggle('is-ready', allComplete);
        confirmBtn.title = allComplete ? '全部问题已回答，提交答案' : '确认当前回答并进入下一题';
    }
    var shortcut = panes[next].querySelector('.human-other-shortcut');
    if (shortcut) shortcut.textContent = allComplete ? 'Ctrl/Cmd + Enter 提交答案' : 'Ctrl/Cmd + Enter 确认回答';
    if (card.dataset.draftReady === '1') persistHumanInteractionDraft(card);
}

function createHumanQuestionCard(record, sessionId, options) {
    options = options || {};
    var readonly = !!options.readonly;
    var card = humanElement('article', 'human-interaction-card human-question-card' + (readonly ? ' is-readonly' : ''));
    card.dataset.kind = 'question';
    card.dataset.sessionId = sessionId;
    card.dataset.interactionId = String(record.interaction_id || '');
    card.dataset.requestVersion = String(record.request_version || 1);
    appendHumanCardHeader(card, record, 'question');
    var questions = Array.isArray(record.questions) ? record.questions : [];
    if (questions.length > 1) {
        var tabs = humanElement('div', 'human-question-tabs');
        tabs.setAttribute('role', 'tablist');
        questions.forEach(function (question, index) {
            var tab = humanElement('button', 'human-question-tab', question.header || ('问题 ' + (index + 1)));
            tab.type = 'button';
            tab.id = 'human-tab-' + record.interaction_id + '-' + index;
            tab.setAttribute('role', 'tab');
            tab.setAttribute('aria-controls', 'human-pane-' + record.interaction_id + '-' + index);
            tab.addEventListener('click', function () {
                if (readonly) {
                    setHumanQuestionStep(card, index);
                    return;
                }
                var current = Number(card.dataset.step || 0);
                if (index > current && !validateHumanQuestionPane(card, card.querySelectorAll('.human-question-pane')[current])) return;
                setHumanQuestionStep(card, index);
            });
            tab.addEventListener('keydown', function (event) {
                if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
                event.preventDefault();
                var target = index + (event.key === 'ArrowRight' ? 1 : -1);
                target = Math.max(0, Math.min(target, questions.length - 1));
                var current = Number(card.dataset.step || 0);
                if (!readonly && target > current && !validateHumanQuestionPane(card, card.querySelectorAll('.human-question-pane')[current])) return;
                setHumanQuestionStep(card, target);
                var targetTab = card.querySelectorAll('.human-question-tab')[target];
                if (targetTab) targetTab.focus();
            });
            tabs.appendChild(tab);
        });
        card.appendChild(tabs);
        card.appendChild(humanElement('div', 'human-question-progress'));
    }
    var body = humanElement('div', 'human-card-body');
    questions.forEach(function (question, qIndex) {
        var pane = humanElement('fieldset', 'human-question-pane');
        pane.id = 'human-pane-' + record.interaction_id + '-' + qIndex;
        pane.setAttribute('role', 'tabpanel');
        if (questions.length > 1) pane.setAttribute('aria-labelledby', 'human-tab-' + record.interaction_id + '-' + qIndex);
        pane.dataset.questionId = String(question.question_id || ('q' + (qIndex + 1)));
        pane.dataset.questionHeader = String(question.header || ('问题 ' + (qIndex + 1)));
        pane.appendChild(humanElement('legend', 'human-question-text', question.question || ''));
        pane.appendChild(humanElement('div', 'human-question-hint', question.multi_select ? '可多选' : '单选'));
        var options = humanElement('div', 'human-options');
        (question.options || []).forEach(function (option, optionIndex) {
            var label = humanElement('label', 'human-option');
            var input = document.createElement('input');
            input.type = question.multi_select ? 'checkbox' : 'radio';
            input.name = 'human-' + record.interaction_id + '-' + pane.dataset.questionId;
            input.dataset.optionId = String(option.option_id || '');
            var copy = humanElement('span', 'human-option-copy');
            copy.appendChild(humanElement('span', 'human-option-label', option.label || ''));
            var description = humanElement('span', 'human-option-description', option.description || '');
            description.id = 'human-option-desc-' + record.interaction_id + '-' + qIndex + '-' + optionIndex;
            input.setAttribute('aria-describedby', description.id);
            copy.appendChild(description);
            if (option.preview) {
                var details = humanElement('details', 'human-option-preview');
                details.appendChild(humanElement('summary', '', '查看预览'));
                details.appendChild(humanElement('pre', '', option.preview));
                copy.appendChild(details);
            }
            label.appendChild(input);
            label.appendChild(copy);
            options.appendChild(label);
        });
        var other = humanElement('label', 'human-option human-option-other');
        var otherMark = document.createElement('input');
        otherMark.type = question.multi_select ? 'checkbox' : 'radio';
        otherMark.name = 'human-' + record.interaction_id + '-' + pane.dataset.questionId;
        otherMark.className = 'human-other-mark';
        var otherCopy = humanElement('span', 'human-option-copy');
        var otherHeader = humanElement('span', 'human-other-header');
        otherHeader.appendChild(humanElement('span', 'human-option-label', '其他'));
        otherHeader.appendChild(humanElement(
            'span',
            'input-shortcut-hint human-other-shortcut',
            'Ctrl/Cmd + Enter 确认回答'
        ));
        otherCopy.appendChild(otherHeader);
        var otherInput = document.createElement('textarea');
        otherInput.className = 'human-other-input';
        otherInput.rows = 2;
        otherInput.maxLength = 2000;
        otherInput.placeholder = '输入你的答案…';
        otherInput.setAttribute('aria-label', '其他答案');
        otherInput.addEventListener('focus', function () {
            pane.dataset.skipped = '0';
            otherMark.checked = true;
            setHumanQuestionStep(card, Number(card.dataset.step || 0));
        });
        otherCopy.appendChild(otherInput);
        other.appendChild(otherMark);
        other.appendChild(otherCopy);
        options.appendChild(other);
        options.addEventListener('change', function () {
            pane.dataset.skipped = '0';
            setHumanQuestionStep(card, Number(card.dataset.step || 0));
        });
        options.addEventListener('input', function () {
            pane.dataset.skipped = '0';
            setHumanQuestionStep(card, Number(card.dataset.step || 0));
        });
        pane.appendChild(options);
        body.appendChild(pane);
    });
    card.appendChild(body);
    var error = humanElement('div', 'human-card-error');
    error.setAttribute('role', 'alert');
    card.appendChild(error);
    var actions = humanElement('div', 'human-card-actions human-question-actions');
    var skip = humanElement('button', 'human-secondary-btn human-skip-btn', '不回答');
    skip.type = 'button';
    skip.title = '只跳过当前题目';
    skip.addEventListener('click', function () {
        var current = Number(card.dataset.step || 0);
        var panes = card.querySelectorAll('.human-question-pane');
        var pane = panes[current];
        if (!pane) return;
        pane.querySelectorAll('input').forEach(function (input) { input.checked = false; });
        var otherInput = pane.querySelector('.human-other-input');
        if (otherInput) otherInput.value = '';
        pane.dataset.skipped = '1';
        var error = card.querySelector('.human-card-error');
        if (error) error.textContent = '';
        persistHumanInteractionDraft(card);
        setHumanQuestionStep(card, nextIncompleteHumanQuestionIndex(Array.from(panes), current));
    });
    var nav = humanElement('div', 'human-card-nav');
    var back = humanElement('button', 'human-secondary-btn human-back-btn', '上一题');
    back.type = 'button';
    back.addEventListener('click', function () {
        setHumanQuestionStep(card, Number(card.dataset.step || 0) - 1);
    });
    var confirmButton = humanElement('button', 'human-primary-btn human-confirm-btn', '确认');
    confirmButton.type = 'button';
    confirmButton.addEventListener('click', function () {
        if (allHumanQuestionsComplete(card)) void submitHumanQuestion(card);
        else confirmCurrentHumanQuestion(card);
    });
    nav.appendChild(back);
    nav.appendChild(confirmButton);
    actions.appendChild(skip);
    actions.appendChild(nav);
    card.appendChild(actions);
    if (readonly) {
        applyHumanQuestionTerminalAnswers(card, record);
        setHumanQuestionStep(card, 0);
        card.querySelectorAll('input, textarea, .human-card-actions button').forEach(function (control) {
            control.disabled = true;
        });
        return card;
    }
    var draft = restoreHumanInteractionDraft(card);
    setHumanQuestionStep(card, draft && Number.isFinite(Number(draft.step)) ? Number(draft.step) : 0);
    card.dataset.draftReady = '1';
    card.addEventListener('keydown', function (event) {
        if (!isInputSubmitShortcut(event, 'editor')) return;
        event.preventDefault();
        if (allHumanQuestionsComplete(card)) void submitHumanQuestion(card);
        else confirmCurrentHumanQuestion(card);
    });
    return card;
}

function collectHumanQuestionAnswers(card) {
    var answers = [];
    var invalidPane = null;
    card.querySelectorAll('.human-question-pane').forEach(function (pane) {
        var selected = Array.from(pane.querySelectorAll('input[data-option-id]:checked')).map(function (input) { return input.dataset.optionId; });
        var otherMark = pane.querySelector('.human-other-mark');
        var otherInput = pane.querySelector('.human-other-input');
        var skipped = pane.dataset.skipped === '1';
        var otherText = !skipped && otherMark && otherMark.checked && otherInput ? normalizeSendableText(otherInput.value) : '';
        if (!skipped && ((!selected.length && !otherText) || (otherMark && otherMark.checked && !otherText)) && !invalidPane) invalidPane = pane;
        answers.push({
            question_id: pane.dataset.questionId || '',
            selected_option_ids: skipped ? [] : selected,
            other_text: otherText || null,
            notes: null,
            skipped: skipped,
        });
    });
    return { answers: answers, invalidPane: invalidPane };
}

function setHumanInteractionSubmitting(card, submitting, label) {
    if (!card) return;
    card.dataset.submitting = submitting ? '1' : '0';
    card.classList.toggle('is-submitting', !!submitting);
    card.setAttribute('aria-busy', submitting ? 'true' : 'false');
    var status = card.querySelector('.human-card-status');
    if (status) {
        if (!status.dataset.defaultLabel) status.dataset.defaultLabel = status.textContent || '';
        status.textContent = submitting ? (label || '正在提交…') : status.dataset.defaultLabel;
    }
    var primary = card.querySelector('.human-confirm-btn, .human-allow-btn');
    if (!primary) return;
    if (!primary.dataset.defaultLabel) primary.dataset.defaultLabel = primary.textContent || '';
    primary.textContent = submitting ? (label || '正在提交…') : primary.dataset.defaultLabel;
}

async function submitHumanQuestion(card) {
    if (!card || card.dataset.submitting === '1') return;
    var collected = collectHumanQuestionAnswers(card);
    var error = card.querySelector('.human-card-error');
    if (collected.invalidPane) {
        var panes = Array.from(card.querySelectorAll('.human-question-pane'));
        setHumanQuestionStep(card, panes.indexOf(collected.invalidPane));
        if (error) error.textContent = '请完成当前问题后再提交。';
        return;
    }
    setHumanInteractionSubmitting(card, true, '正在提交…');
    if (error) error.textContent = '';
    try {
        var recoveryAfterIndex = typeof getUiEventCount === 'function'
            ? await getUiEventCount(card.dataset.sessionId, { timeoutMs: 5000 })
            : 0;
        var response = await fetch('/sessions/' + encodeURIComponent(card.dataset.sessionId) + '/interactions/' + encodeURIComponent(card.dataset.interactionId) + '/resolve', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ answers: collected.answers }),
        });
        var data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || ('HTTP ' + response.status));
        clearHumanInteractionDraft(card.dataset.sessionId, card.dataset.interactionId, card.dataset.requestVersion);
        var record = applyHumanInteractionEvent(card.dataset.sessionId, Object.assign({ type: 'interaction_resolved' }, data.interaction || {}));
        renderHumanInteractionRecord(record, card.dataset.sessionId, card.parentNode);
        if (data.recovery_scheduled) {
            resumeRecoveredHumanInteractionStream(card.dataset.sessionId, recoveryAfterIndex);
        }
    } catch (err) {
        setHumanInteractionSubmitting(card, false);
        if (error) error.textContent = '提交失败：' + String(err && err.message ? err.message : err);
    }
}

function resumeRecoveredHumanInteractionStream(sessionId, afterIndex) {
    var sid = String(sessionId || '');
    if (!sid) return;
    if (typeof discardCachedSessionStream === 'function') discardCachedSessionStream(sid);
    if (sid !== String(currentSessionId || '')) return;
    var start = function () {
        if (sid !== String(currentSessionId || '')) return;
        if (typeof attachSessionEventStream === 'function') {
            void attachSessionEventStream(sid, {
                skipInitialLoad: true,
                force: true,
                afterIndex: Math.max(0, Number(afterIndex) || 0),
            });
        }
    };
    if (typeof refreshSingleSessionRow === 'function') {
        void Promise.resolve(refreshSingleSessionRow(sid)).then(start, start);
    } else {
        start();
    }
}

function createHumanApprovalCard(record, sessionId, options) {
    options = options || {};
    var readonly = !!options.readonly;
    var danger = record.approval_level === 'danger';
    var forced = !!record.force_approval;
    var workspaceApproval = !forced && !!record.external_workspace_grantable;
    var card = humanElement('article', 'human-interaction-card human-approval-card'
        + (danger ? ' is-danger' : '')
        + (readonly ? ' is-readonly' : ''));
    card.dataset.kind = 'approval';
    card.dataset.sessionId = sessionId;
    card.dataset.interactionId = String(record.approval_id || '');
    card.dataset.rejectionReasonSuggestion = String(record.rejection_reason_suggestion || '').trim();
    appendHumanCardHeader(card, record, 'approval');
    var body = humanElement('div', 'human-card-body');
    // 拦截原因 / 命令风险 / 命令目的 三段结构化展示。普通审批卡的
    // subtitle 就是策略给出的拦截原因；自动审查覆盖卡的 subtitle 是
    // "【拦截原因】…\n【命令风险】…\n【命令目的】…" 拼接文本，这里拆开。
    var subtitle = String(record.subtitle || '').trim();
    var hasReviewSections = subtitle.indexOf('【命令风险】') >= 0 || subtitle.indexOf('【命令目的】') >= 0;
    if (subtitle) {
        if (hasReviewSections) {
            var sections = splitReviewExplanationText(
                String(record.intercept_reason || ''),
                String(record.risk_analysis || ''),
                String(record.command_purpose || ''),
                subtitle
            );
            if (sections.intercept) {
                var interceptBox = humanElement('div', 'human-approval-review human-approval-review--intercept');
                interceptBox.appendChild(humanElement('div', 'human-approval-review-label', '拦截原因'));
                interceptBox.appendChild(humanElement('div', 'human-approval-review-text', sections.intercept));
                body.appendChild(interceptBox);
            }
            if (sections.risk) {
                var riskBox = humanElement('div', 'human-approval-review human-approval-review--risk');
                riskBox.appendChild(humanElement('div', 'human-approval-review-label', '命令风险'));
                riskBox.appendChild(humanElement('div', 'human-approval-review-text', sections.risk));
                body.appendChild(riskBox);
            }
            if (sections.purpose) {
                var purposeBox = humanElement('div', 'human-approval-review human-approval-review--purpose');
                purposeBox.appendChild(humanElement('div', 'human-approval-review-label', '命令目的'));
                purposeBox.appendChild(humanElement('div', 'human-approval-review-text', sections.purpose));
                body.appendChild(purposeBox);
            }
        } else {
            body.appendChild(humanElement('div', 'human-approval-subtitle', subtitle));
        }
    }
    body.appendChild(humanElement('div', 'human-approval-message', record.message || '是否允许 Agent 执行此操作？'));
    if (danger && record.consequence) {
        body.appendChild(humanElement('div', 'human-approval-consequence', record.consequence));
    }
    if (record.tool) {
        var detail = humanElement('div', 'human-approval-detail');
        detail.appendChild(humanElement('span', '', '工具'));
        detail.appendChild(humanElement('code', '', record.tool));
        body.appendChild(detail);
    }
    var egressIntent = String(record.egress_intent || 'none');
    if (egressIntent !== 'none') {
        var egress = humanElement('div', 'human-egress-summary');
        var intentLabel = egressIntent === 'upload'
            ? '数据发送'
            : (egressIntent === 'read' ? '网络读取' : '未知网络操作');
        egress.appendChild(humanElement('div', 'human-egress-label', intentLabel));
        var destinations = Array.isArray(record.destinations) ? record.destinations : [];
        if (destinations.length) {
            egress.appendChild(humanElement('div', 'human-egress-row', '目标：' + destinations.map(function (item) {
                var host = String((item && item.host) || '未知');
                var port = Number(item && item.port) || 0;
                return host + (port ? ':' + port : '');
            }).join('、')));
        } else {
            egress.appendChild(humanElement('div', 'human-egress-row is-warning', '目标：运行时动态确定'));
        }
        var sources = Array.isArray(record.data_sources) ? record.data_sources : [];
        if (sources.length) egress.appendChild(humanElement('div', 'human-egress-row', '数据来源：' + sources.join('、')));
        var enforcement = String(record.enforcement_level || '');
        if (enforcement) {
            egress.appendChild(humanElement(
                'div',
                'human-egress-enforcement ' + (enforcement === 'strong' ? 'is-strong' : (enforcement === 'partial' ? 'is-partial' : 'is-degraded')),
                enforcement === 'strong'
                    ? '系统级出站防护已启用（目标受限）'
                    : (enforcement === 'partial'
                        ? '系统级出站防护已启用（无网络命令强制断网；获批联网暂不限制目标）'
                        : (enforcement === 'disabled' ? '系统出站助手已关闭' : '降级防护：当前仅使用应用层识别'))
            ));
        }
        body.appendChild(egress);
    }
    if (!forced && !workspaceApproval && record.rule_pattern) {
        body.appendChild(
            humanElement(
                'div',
                'human-approval-rule-hint',
                '“始终允许”将保存为长期规则：' + record.rule_pattern
            )
        );
    } else if (!forced && !workspaceApproval && record.grant_scope === 'task') {
        body.appendChild(
            humanElement(
                'div',
                'human-approval-rule-hint',
                '“始终允许”仅在当前任务内允许同类操作和相同目标，不会保存跨任务规则。'
            )
        );
    }
    card.appendChild(body);
    var analysis = humanElement('div', 'human-approval-analysis');
    analysis.hidden = true;
    analysis.setAttribute('role', 'status');
    card.appendChild(analysis);
    var rejectionEditor = humanElement('div', 'human-approval-rejection-editor');
    rejectionEditor.hidden = true;
    var rejectionLabel = humanElement('label', 'human-approval-rejection-label', '拒绝原因');
    var rejectionInput = document.createElement('textarea');
    rejectionInput.className = 'human-approval-rejection-input';
    rejectionInput.rows = 3;
    rejectionInput.maxLength = 2000;
    rejectionInput.placeholder = '请填写拒绝原因，Agent 会根据原因调整后续操作';
    rejectionLabel.appendChild(rejectionInput);
    rejectionEditor.appendChild(rejectionLabel);
    rejectionEditor.appendChild(humanElement('div', 'human-approval-rejection-hint', '拒绝原因会随审批结果返回给 Agent。'));
    card.appendChild(rejectionEditor);
    var error = humanElement('div', 'human-card-error');
    error.setAttribute('role', 'alert');
    card.appendChild(error);
    var actions = humanElement('div', 'human-card-actions human-approval-actions');
    var analyze = humanElement('button', 'human-secondary-btn human-analyze-btn', '替我分析');
    analyze.type = 'button';
    analyze.title = '调用独立审查模型给出风险解读和审批建议，不会替你执行审批';
    analyze.addEventListener('click', function () { void analyzeHumanApproval(card); });
    actions.appendChild(analyze);
    var decisions = humanElement('div', 'human-approval-decisions');
    var deny = humanElement('button', 'human-secondary-btn human-deny-btn', '拒绝执行');
    deny.type = 'button';
    deny.addEventListener('click', function () { void requestHumanApprovalDenial(card); });
    decisions.appendChild(deny);
    if (!forced && record.allow_session_available !== false) {
        var durableRuleAvailable = !!record.allow_always_available && !!record.rule_pattern;
        var alwaysDecision = workspaceApproval
            ? 'allow_external_workspace'
            : (durableRuleAvailable ? 'allow_always' : 'allow_session');
        var always = humanElement('button', 'human-secondary-btn human-always-btn', '始终允许');
        always.type = 'button';
        always.title = workspaceApproval
            ? '持续允许写入、删除和 Shell 处理工作区沙箱外内容；随后仍会单独审批本次工具操作'
            : (durableRuleAvailable
                ? '保存为长期规则，后续匹配时自动放行：' + record.rule_pattern
                : (record.grant_scope === 'task'
                    ? '仅在当前任务内自动允许同类操作和相同目标，不会保存跨任务规则'
                    : '无法生成长期规则；改为在当前任务内自动允许完全相同的请求'));
        always.addEventListener('click', function () { void resolveHumanApproval(card, alwaysDecision); });
        decisions.appendChild(always);
    }
    var onceDecision = workspaceApproval ? 'allow_external_workspace_once' : 'allow_once';
    var allow = humanElement('button', 'human-primary-btn human-allow-btn', '本次允许');
    allow.type = 'button';
    allow.title = workspaceApproval
        ? '仅允许本次处理工作区沙箱外内容；随后仍会单独审批本次工具操作'
        : '仅放行这一次；执行后授权立即失效';
    allow.addEventListener('click', function () { void resolveHumanApproval(card, onceDecision); });
    decisions.appendChild(allow);
    actions.appendChild(decisions);
    card.appendChild(actions);
    if (readonly) {
        rejectionInput.disabled = true;
        if (record.decision === 'deny' && record.rejection_reason) {
            rejectionInput.value = String(record.rejection_reason);
            rejectionEditor.hidden = false;
        }
        card.querySelectorAll('.human-card-actions button').forEach(function (button) {
            button.disabled = true;
        });
        var selectedButton = record.decision === 'deny'
            ? deny
            : ((record.decision === 'allow_always'
                || record.decision === 'allow_session'
                || record.decision === 'allow_external_workspace') ? always : allow);
        if (selectedButton && record.decision) {
            selectedButton.classList.add('is-selected');
            selectedButton.setAttribute('aria-pressed', 'true');
        }
    }
    return card;
}

async function analyzeHumanApproval(card) {
    if (!card || card.dataset.submitting === '1') return;
    var error = card.querySelector('.human-card-error');
    var panel = card.querySelector('.human-approval-analysis');
    setHumanInteractionSubmitting(card, true, '正在分析…');
    if (error) error.textContent = '';
    if (panel) {
        panel.hidden = false;
        panel.className = 'human-approval-analysis is-loading';
        panel.textContent = '审查 Agent 正在核对任务意图与本次操作风险…';
    }
    try {
        var response = await fetch('/sessions/' + encodeURIComponent(card.dataset.sessionId) + '/approvals/' + encodeURIComponent(card.dataset.interactionId) + '/analyze', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
        });
        var data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || ('HTTP ' + response.status));
        var result = data.analysis || {};
        var recommendAllow = result.recommendation === 'allow';
        panel.className = 'human-approval-analysis ' + (recommendAllow ? 'is-allow' : 'is-deny');
        panel.textContent = '';
        var heading = humanElement(
            'div',
            'human-approval-analysis-title',
            result.available === false
                ? '暂时无法给出可靠建议'
                : (recommendAllow ? '建议允许' : '建议拒绝')
        );
        var risk = humanElement('span', 'human-approval-analysis-risk', '风险：' + String(result.risk || 'unknown'));
        heading.appendChild(risk);
        panel.appendChild(heading);
        appendApprovalReviewExplanation(
            panel,
            result.intercept_reason,
            result.risk_analysis,
            result.command_purpose,
            result.reason || '审查模型未提供理由。'
        );
        if (!recommendAllow && result.available !== false && String(result.reason || '').trim()) {
            card.dataset.rejectionReasonSuggestion = String(result.reason).trim();
            var rejectionInput = card.querySelector('.human-approval-rejection-input');
            if (rejectionInput && !String(rejectionInput.value || '').trim()) {
                rejectionInput.value = card.dataset.rejectionReasonSuggestion;
            }
        }
        panel.appendChild(humanElement('div', 'human-approval-analysis-hint', '以上仅为分析建议，审批仍由你决定。'));
    } catch (err) {
        if (panel) panel.hidden = true;
        if (error) error.textContent = '分析失败：' + String(err && err.message ? err.message : err);
    } finally {
        setHumanInteractionSubmitting(card, false);
    }
}

async function requestHumanApprovalDenial(card) {
    if (!card || card.dataset.submitting === '1') return;
    var suggestedReason = String(card.dataset.rejectionReasonSuggestion || '').trim();
    if (suggestedReason) {
        await resolveHumanApproval(card, 'deny', suggestedReason);
        return;
    }
    var editor = card.querySelector('.human-approval-rejection-editor');
    var input = card.querySelector('.human-approval-rejection-input');
    var deny = card.querySelector('.human-deny-btn');
    if (editor && editor.hidden) {
        editor.hidden = false;
        if (deny) deny.textContent = '确认拒绝';
        if (input) input.focus();
        return;
    }
    var reason = String((input && input.value) || '').trim();
    if (!reason) {
        var error = card.querySelector('.human-card-error');
        if (error) error.textContent = '请填写拒绝原因。';
        if (input) input.focus();
        return;
    }
    await resolveHumanApproval(card, 'deny', reason);
}

async function resolveHumanApproval(card, decision, rejectionReason) {
    if (!card || card.dataset.submitting === '1') return;
    setHumanInteractionSubmitting(card, true, '正在处理…');
    try {
        var payload = { decision: decision };
        if (decision === 'deny') payload.rejection_reason = String(rejectionReason || '').trim();
        var response = await fetch('/sessions/' + encodeURIComponent(card.dataset.sessionId) + '/approvals/' + encodeURIComponent(card.dataset.interactionId) + '/resolve', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
        var data = await response.json();
        if (!response.ok || !data.ok) {
            if (data.approval) {
                var staleRecord = applyHumanInteractionEvent(card.dataset.sessionId, Object.assign({ type: 'approval_cancelled' }, data.approval));
                renderHumanInteractionRecord(staleRecord, card.dataset.sessionId, card.parentNode);
                return;
            }
            throw new Error(data.error || ('HTTP ' + response.status));
        }
        var record = applyHumanInteractionEvent(card.dataset.sessionId, Object.assign({ type: 'approval_resolved' }, data.approval || {}));
        renderHumanInteractionRecord(record, card.dataset.sessionId, card.parentNode);
    } catch (err) {
        setHumanInteractionSubmitting(card, false);
        var error = card.querySelector('.human-card-error');
        if (error) error.textContent = '处理失败：' + String(err && err.message ? err.message : err);
    }
}

function createHumanTerminalCard(record, sessionId) {
    var kind = record.kind === 'approval' ? 'approval' : 'question';
    var card = kind === 'approval'
        ? createHumanApprovalCard(record, sessionId, { readonly: true })
        : createHumanQuestionCard(record, sessionId, { readonly: true });
    card.classList.add('is-terminal', 'is-readonly', 'is-collapsed');
    var summary = humanElement('div', 'human-terminal-summary');
    if (record.status === 'cancelled') {
        summary.textContent = record.reason || '该请求已取消。';
    } else if (record.status === 'expired') {
        summary.textContent = '该请求已过期。';
    } else if (kind === 'approval') {
        summary.textContent = record.decision === 'deny'
            ? '你已拒绝本次操作。'
            : (record.decision === 'allow_external_workspace'
                ? '已始终允许工作区沙箱外处理；本次工具操作仍需单独审批。'
                : (record.decision === 'allow_external_workspace_once'
                    ? '已允许本次工作区沙箱外处理；本次工具操作仍需单独审批。'
                    : (record.decision === 'allow_always'
                        ? ('已保存长期规则，后续匹配的操作将自动放行。' + (record.rule_pattern ? '（规则：' + record.rule_pattern + '）' : ''))
                        : (record.decision === 'allow_session'
                            ? (record.grant_scope === 'task'
                                ? '当前任务内将自动允许同类操作和相同目标。'
                                : '当前任务内将自动允许完全相同的请求。')
                            : '已允许这一次；执行后授权失效。'))));
        if (record.decision === 'deny' && record.rejection_reason) {
            summary.appendChild(humanElement(
                'div',
                'human-terminal-rejection-reason',
                '拒绝原因：' + String(record.rejection_reason)
            ));
        }
    } else {
        var answers = Array.isArray(record.answers) ? record.answers : [];
        var questionsById = Object.create(null);
        (record.questions || []).forEach(function (question) {
            questionsById[String(question.question_id || '')] = question;
        });
        answers.forEach(function (answer) {
            var line = humanElement('div', 'human-terminal-answer');
            var values = (answer.selected_labels || []).slice();
            if (answer.other_text) values.push(answer.other_text);
            var question = questionsById[String(answer.question_id || '')] || {};
            line.appendChild(humanElement('span', 'human-terminal-answer-label', question.header || '回答'));
            line.appendChild(humanElement('span', 'human-terminal-answer-value', answer.skipped ? '未回答' : (values.join('、') || '已回答')));
            summary.appendChild(line);
        });
    }
    var head = card.querySelector('.human-card-head');
    var detail = humanElement('div', 'human-terminal-detail');
    var detailId = 'human-terminal-detail-' + kind + '-' + String(card.dataset.interactionId || '').replace(/[^a-zA-Z0-9_-]/g, '-');
    detail.id = detailId;
    detail.hidden = true;
    detail.appendChild(humanElement(
        'div',
        'human-readonly-notice',
        record.status === 'resolved'
            ? '此请求已处理，仅供查看，无法再次操作。'
            : '此请求已结束，仅供查看，无法再次操作。'
    ));
    while (head && head.nextSibling) detail.appendChild(head.nextSibling);
    card.appendChild(summary);
    card.appendChild(detail);
    if (head) {
        var toggle = humanElement('button', 'human-terminal-toggle', '展开');
        toggle.type = 'button';
        toggle.title = '展开或收起完整请求';
        toggle.setAttribute('aria-expanded', 'false');
        toggle.setAttribute('aria-controls', detailId);
        toggle.addEventListener('click', function () {
            var expanded = toggle.getAttribute('aria-expanded') !== 'true';
            toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
            if (typeof setUiRuntimeText === 'function') setUiRuntimeText(toggle, expanded ? '收起' : '展开');
            else toggle.textContent = expanded ? '收起' : '展开';
            summary.hidden = expanded;
            detail.hidden = !expanded;
            card.classList.toggle('is-expanded', expanded);
            card.classList.toggle('is-collapsed', !expanded);
        });
        head.appendChild(toggle);
    }
    return card;
}

function renderHumanInteractionRecord(record, sessionId, stream) {
    if (!record) return null;
    var sid = String(sessionId || record.session_id || '');
    var kind = record.kind === 'approval' ? 'approval' : 'question';
    var id = String(kind === 'approval' ? (record.approval_id || '') : (record.interaction_id || ''));
    if (!id) return null;
    stream = stream && stream.querySelectorAll ? stream : (typeof getVisibleChatStream === 'function' ? getVisibleChatStream() : document.getElementById('chat-stream'));
    if (!stream) return null;
    var existing = Array.from(stream.querySelectorAll('.human-interaction-card')).find(function (card) {
        return card.dataset.kind === kind && card.dataset.interactionId === id;
    });
    var restoreFocus = !!(existing && existing.contains(document.activeElement));
    var card = record.status === 'pending'
        ? (kind === 'approval' ? createHumanApprovalCard(record, sid) : createHumanQuestionCard(record, sid))
        : createHumanTerminalCard(record, sid);
    card.dataset.status = record.status || 'pending';
    var toolCallId = String(record.tool_call_id || '');
    if (toolCallId) card.dataset.toolCallId = toolCallId;
    if (existing && existing.parentNode) existing.parentNode.replaceChild(card, existing);
    else {
        var slot = humanInteractionToolSlot(stream, toolCallId);
        (slot || stream).appendChild(card);
    }
    if (toolCallId) {
        attachHumanInteractionCardsForToolCall(stream, toolCallId);
    }
    if (restoreFocus && record.status !== 'pending') {
        card.setAttribute('tabindex', '-1');
        requestAnimationFrame(function () { card.focus({ preventScroll: true }); });
    }
    return card;
}

function humanCardVisibleInViewport(card) {
    if (!card || !card.getBoundingClientRect) return true;
    var r = card.getBoundingClientRect();
    if (!r.width && !r.height) return false;
    var vh = window.innerHeight || document.documentElement.clientHeight || 0;
    return r.top >= -8 && r.bottom <= vh + 8;
}

function autoRevealPendingHumanCard(card) {
    if (!card || card.dataset.status !== 'pending') return;
    requestAnimationFrame(function () {
        if (humanCardVisibleInViewport(card)) return;
        card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
}

function renderHumanInteractionEvent(ctx, event, runSessionId) {
    var sid = String(runSessionId || event.session_id || currentSessionId || '');
    var record = applyHumanInteractionEvent(sid, event);
    var stream = ctx && ctx.stream ? ctx.stream : null;
    ensurePendingQuestionToolRow(ctx, record, sid);
    var card = renderHumanInteractionRecord(record, sid, stream);
    // Live SSE only: bring a freshly-inserted pending card into view.
    if (card && record.status === 'pending' && !(typeof replayingMessages !== 'undefined' && replayingMessages)) {
        autoRevealPendingHumanCard(card);
    }
    return card;
}

function renderPendingHumanInteractions(sessionId) {
    var sid = String(sessionId || '');
    if (!sid || sid !== String(currentSessionId || '')) return;
    var stream = typeof getVisibleChatStream === 'function' ? getVisibleChatStream() : document.getElementById('chat-stream');
    var ctx = stream && typeof newDomContext === 'function' ? newDomContext(stream) : null;
    pendingHumanInteractionRecords(sid).forEach(function (record) {
        ensurePendingQuestionToolRow(ctx, record, sid);
        renderHumanInteractionRecord(record, sid, stream);
    });
    if (typeof attachAllHumanInteractionCards === 'function') attachAllHumanInteractionCards(stream);
    updateHumanInteractionBanner(sid);
}

async function refreshHumanInteractions(sessionId, options) {
    var sid = String(sessionId || '');
    if (!sid) return false;
    options = options || {};
    var state = humanInteractionSessionState(sid);
    var refreshEpoch = ++state.refreshEpoch;
    try {
        var responses = await Promise.all([
            fetch('/sessions/' + encodeURIComponent(sid) + '/interactions?status=pending'),
            fetch('/sessions/' + encodeURIComponent(sid) + '/approvals?status=pending'),
        ]);
        if (!responses[0].ok || !responses[1].ok) throw new Error('HTTP ' + responses[0].status + '/' + responses[1].status);
        var payloads = await Promise.all([responses[0].json(), responses[1].json()]);
        if (refreshEpoch !== state.refreshEpoch) return false;
        state.interactions = Object.create(null);
        state.approvals = Object.create(null);
        (payloads[0].interactions || []).forEach(function (row) {
            row.kind = 'question';
            state.interactions[String(row.interaction_id || '')] = row;
        });
        (payloads[1].approvals || []).forEach(function (row) {
            row.kind = 'approval';
            state.approvals[String(row.approval_id || '')] = row;
        });
        state.loaded = true;
        syncHumanInteractionSessionSummary(sid);
        if (options.render !== false && sid === String(currentSessionId || '')) renderPendingHumanInteractions(sid);
        return true;
    } catch (err) {
        console.error('加载待处理交互失败:', err);
        return false;
    }
}

(function bindHumanInteractionBanner() {
    var button = document.getElementById('human-interaction-banner-btn');
    if (button) button.addEventListener('click', function () { void handleHumanTodoFloaterAction(); });
})();
