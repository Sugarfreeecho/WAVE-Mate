const SSE_IDLE_TIMEOUT_MS = 120000;
const SSE_RESUME_PROBE_TIMEOUT_MS = 20000;
const STREAM_RECONNECT_MAX_ATTEMPTS = 10;
const STREAM_RECONNECT_BASE_DELAY_MS = 500;
const STREAM_RECONNECT_MAX_DELAY_MS = 15000;
const streamReconnectStateBySession = Object.create(null);

function resetStreamReconnectState(sessionId) {
    var sid = String(sessionId || '');
    var state = streamReconnectStateBySession[sid];
    if (!state) return;
    if (state.timer) clearTimeout(state.timer);
    delete streamReconnectStateBySession[sid];
}

function streamReconnectState(sessionId) {
    var sid = String(sessionId || '');
    if (!streamReconnectStateBySession[sid]) {
        streamReconnectStateBySession[sid] = { attempts: 0, timer: null, exhausted: false };
    }
    return streamReconnectStateBySession[sid];
}

function isStreamConsuming(sessionId) {
    var sid = String(sessionId || '');
    var run = typeof getSessionRunState === 'function' ? getSessionRunState(sid) : null;
    return !!(run && run.ctx && run.ctx.streamConsuming);
}

function reportStreamReconnectExhausted(sessionId) {
    var sid = String(sessionId || '');
    var run = typeof getSessionRunState === 'function' ? getSessionRunState(sid) : null;
    var ctx = run && run.ctx;
    if (ctx && sid === String(currentSessionId || '')) {
        appendLog(ctx, '实时流恢复已停止重试（' + STREAM_RECONNECT_MAX_ATTEMPTS + ' 次）。请检查网络或服务状态后刷新页面。', 'error-log', sid);
    }
}

function sendPipelineKey(sessionId) {
    return String(sessionId || '__new_session__');
}

function isSendPipelineLocked(sessionId) {
    return !!sendPipelineLocksBySession[sendPipelineKey(sessionId)];
}

function acquireSendPipelineLock(sessionId) {
    const key = sendPipelineKey(sessionId);
    if (sendPipelineLocksBySession[key]) return null;
    const token = 'send-lock-' + Date.now() + '-' + Math.random().toString(16).slice(2);
    sendPipelineLocksBySession[key] = token;
    return { key: key, token: token };
}

function transferSendPipelineLock(lock, sessionId) {
    if (!lock || sendPipelineLocksBySession[lock.key] !== lock.token) return false;
    const nextKey = sendPipelineKey(sessionId);
    if (nextKey === lock.key) return true;
    if (sendPipelineLocksBySession[nextKey]) return false;
    delete sendPipelineLocksBySession[lock.key];
    sendPipelineLocksBySession[nextKey] = lock.token;
    lock.key = nextKey;
    return true;
}

function releaseSendPipelineLock(lock) {
    if (!lock) return;
    if (sendPipelineLocksBySession[lock.key] === lock.token) {
        delete sendPipelineLocksBySession[lock.key];
    }
}

/* ---------------------------------------------------------------------------
 * 会话级追问 dispatcher：所有显式“立即发送”共用同一 per-session 互斥链，
 * 保证同一会话同一时刻只处理一条追问，避免并发 steer 竞争。
 * ------------------------------------------------------------------------- */
function withFollowupDispatch(sessionId, fn) {
    var sid = String(sessionId || '');
    if (!sid) return Promise.resolve();
    var prev = followupDispatchChain[sid] || Promise.resolve();
    var run = function () { return Promise.resolve().then(fn); };
    // 前一条无论成功/失败都继续执行本条，避免一次失败永久堵塞后续追问。
    var next = prev.then(run, run);
    var settled = next.then(function () { return null; }, function () { return null; });
    followupDispatchChain[sid] = settled;
    settled.finally(function () {
        if (followupDispatchChain[sid] === settled) delete followupDispatchChain[sid];
    });
    return next;
}

function isFollowupDispatchBusy(sessionId) {
    return !!followupDispatchChain[String(sessionId || '')];
}

async function waitForSendPipelineIdle(sessionId, timeoutMs) {
    var sid = String(sessionId || '');
    var deadline = Date.now() + Math.max(0, Number(timeoutMs) || 0);
    while (isSendPipelineLocked(sid)) {
        if (Date.now() >= deadline) return false;
        await sleepMs(40);
    }
    return true;
}

function refreshPendingFollowupQueue(sessionId) {
    var sid = String(sessionId || '');
    if (!sid) return;
    renderFollowupQueue(sid);
}

function shouldApplySseSeqFilter(parsed) {
    if (!parsed || parsed.protocol === 'runtime_v2') return false;
    if (parsed.runtime_seq != null || parsed.runtimeSeq != null) return false;
    const type = String(parsed.type || '');
    if (type === 'context_trim_progress'
        || type === 'context_summary_progress'
        || type === 'key_context_progress'
        || type === 'context_trim_delta'
        || type === 'context_summary_delta'
        || type === 'key_context_delta'
        || type === 'context_trim_body'
        || type === 'context_summary_body'
        || type === 'key_context_body') return false;
    return true;
}

function endRunForClient(sessionId, ctx, opts) {
    opts = opts || {};
    var sid = String(sessionId || '');
    if (!sid) return;
    var allowFollowupDrain = opts.drainFollowup !== false
        && getRunAbortReason(sid, ctx) !== 'user';
    var preserveInterruptedPartial = !!(
        ctx && ctx.preserveInterruptedPartial && opts.discardPartialStreams
    );
    removeTemporaryStatus(ctx);
    if (!preserveInterruptedPartial) removeAbortedToolDraftRows(ctx, {});
    if (opts.discardPartialStreams && !preserveInterruptedPartial) {
        discardLlmStreamChunks(ctx, {});
        discardProgressStreamChunks(ctx);
    } else {
        finalizeLlmStreamChunks(ctx);
        finalizeProgressStreamChunks(ctx);
    }
    if (ctx) delete ctx.preserveInterruptedPartial;
    if (opts.reconcileFinal !== false) {
        scheduleFinalVisibleAfterRunIfEnabled(sid, ctx, { delayMs: opts.finalDelayMs != null ? opts.finalDelayMs : 80 });
    }
    // Runtime V2 marks the run complete before rendering the persisted final.
    // sealProcessGroup clears currentProcessGroup, so appendMessage cannot be
    // relied on to collapse the trace afterwards (and duplicate finals skip it
    // altogether). Collapse while the terminal aggregate is still reachable.
    var terminalAggregate = ctx && ctx.currentProcessGroup;
    if (terminalAggregate && terminalAggregate.isConnected
        && !terminalAggregate.classList.contains('subagent-grid-card')) {
        terminalAggregate.classList.add('is-collapsed');
        var terminalTop = terminalAggregate.querySelector('.process-aggregate-top');
        if (terminalTop) terminalTop.setAttribute('aria-expanded', 'false');
        updateProcessBrief(terminalAggregate);
    }
    sealProcessGroup(ctx);
    // The process viewport is resolved through the active run context. Finish
    // its pending row-height animation and bottom pin before clearing that
    // context, otherwise end-of-run status rows can be left below the visible
    // edge while only the outer chat viewport is snapped.
    if (liveAutoFollow && opts.scroll !== false) {
        finishStreamScrollIfFollow(ctx, sid);
    }
    markSessionRunInactive(sid);
    resetStreamReconnectState(sid);
    if (getSessionRunState(sid)) {
        clearSessionRunStateIfMatch(sid, opts.runId || (ctx && ctx.runId));
    }
    syncSessionListIndicatorClasses();
    setSendButtonState();
    if (opts.syncFollowup !== false && typeof syncFollowupQueueFromServer === 'function') {
        // 终止边界必须先与服务端对账，再决定是否自动续发队首 pending。
        // 入队和普通同步本身都不具备发送权限。
        var followupSync = syncFollowupQueueFromServer(sid);
        if (allowFollowupDrain) {
            void Promise.resolve(followupSync).then(function () {
                scheduleFollowupQueueDrain(sid, opts.followupDelayMs || 0);
            }).catch(function (error) {
                // 未完成服务端对账时不能把“未知状态”当作发送许可。
                console.warn('follow-up reconciliation failed; auto-drain skipped', error);
            });
        }
    } else if (allowFollowupDrain) {
        scheduleFollowupQueueDrain(sid, opts.followupDelayMs || 0);
    }
}

async function readSseChunkWithIdleTimeout(reader, timeoutMs) {
    var timer = null;
    var activeTimeoutMs = timeoutMs;
    try {
        return await Promise.race([
            reader.read(),
            new Promise(function (_resolve, reject) {
                var armedAt = performance.now();
                var arm = function () {
                    timer = setTimeout(function () {
                        var elapsed = performance.now() - armedAt;
                        /* A heavily delayed timer means the browser/system was suspended.
                           Probe the old socket briefly after resume. A healthy stream will
                           deliver its <=15s keepalive; a half-open fetch must not retain the
                           local run slot for another full two minutes. */
                        if (elapsed > activeTimeoutMs + 15000) {
                            armedAt = performance.now();
                            activeTimeoutMs = Math.min(activeTimeoutMs, SSE_RESUME_PROBE_TIMEOUT_MS);
                            arm();
                            return;
                        }
                        var err = new Error('SSE idle timeout after ' + String(activeTimeoutMs) + 'ms');
                        err.name = 'SseIdleTimeout';
                        try { reader.cancel(err).catch(function () { /* ignore */ }); } catch (e) { /* ignore */ }
                        reject(err);
                    }, activeTimeoutMs);
                };
                arm();
            }),
        ]);
    } finally {
        if (timer) clearTimeout(timer);
    }
}

function handoffSessionStreamAfterHumanInteraction(sessionId, afterIndex) {
    var sid = String(sessionId || '');
    if (!sid || sid !== String(currentSessionId || '')) return;
    var run = typeof getSessionRunState === 'function' ? getSessionRunState(sid) : null;
    if (run && run.ctx && run.ctx.streamConsuming && run.controller) {
        markRunAbortReason(run, 'interaction-resolved-reattach');
        try { run.controller.abort(); } catch (e) { /* observer retry below owns recovery */ }
    }
    var delays = [0, 80, 240, 600, 1200];
    var tryAttach = function (attempt) {
        if (sid !== String(currentSessionId || '')) return;
        if (getSessionRunState(sid)) {
            if (attempt + 1 < delays.length) {
                setTimeout(function () { tryAttach(attempt + 1); }, delays[attempt + 1]);
            }
            return;
        }
        if (typeof attachSessionEventStream === 'function') {
            void attachSessionEventStream(sid, {
                skipInitialLoad: true,
                force: true,
                afterIndex: Math.max(0, Number(afterIndex) || 0),
            });
        }
    };
    tryAttach(0);
}

async function consumeAgentSseResponse(response, runCtx, runSessionId, streamEventIdx) {
    if (!response || !response.body) throw new Error('stream response missing body');
    var ct0 = (response.headers && response.headers.get ? (response.headers.get('content-type') || '') : '').toLowerCase();
    if (!response.ok || ct0.indexOf('text/event-stream') < 0) {
        throw new Error('stream response failed: ' + (response.status || 'no status'));
    }
    if (runCtx) runCtx.streamConsuming = true;
    try {
        return await consumeAgentSseResponseInner(response, runCtx, runSessionId, streamEventIdx);
    } finally {
        if (runCtx) runCtx.streamConsuming = false;
    }
}

async function consumeAgentSseResponseInner(response, runCtx, runSessionId, streamEventIdx) {
    if (!response || !response.body) throw new Error('stream response missing body');
    var ct0 = (response.headers && response.headers.get ? (response.headers.get('content-type') || '') : '').toLowerCase();
    if (!response.ok || ct0.indexOf('text/event-stream') < 0) {
        throw new Error('stream response failed: ' + (response.status || 'no status'));
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
        const { done, value } = await readSseChunkWithIdleTimeout(reader, SSE_IDLE_TIMEOUT_MS);
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
            if (line.startsWith(':')) continue;
            if (!line.startsWith('data: ')) continue;
            const data = line.slice(6);
            if (data === '[DONE]') {
                if (runCtx) runCtx.transportClosed = true;
                // Transport completion is not a run outcome.  If the terminal
                // event was lost, retain the local run and reconcile it from
                // the versioned Runtime V2 snapshot instead of guessing success.
                if (!runCtx || runCtx.terminalSeen !== true) {
                    if (typeof reconcileRunStateFromServer === 'function') {
                        void reconcileRunStateFromServer({ silent: true });
                    }
                    scheduleActiveSessionReconnect(runSessionId, { delayMs: 100, failure: true });
                }
                return streamEventIdx;
            }
            try {
                let parsed = JSON.parse(data);
                if (parsed && (parsed.type === 'sse_keepalive' || parsed.keepalive === true)) continue;
                if (parsed && parsed.protocol === 'runtime_v2') {
                    const envelopeSessionId = parsed.session_id || parsed.sessionId || runSessionId;
                    if (!sessionStore.shouldAcceptSseEvent(envelopeSessionId, parsed.seq, 'runtime_v2')) continue;
                    if (parsed.skip_ui) {
                        applySkippedRuntimeV2EventMetadata(parsed, runCtx, envelopeSessionId);
                        continue;
                    }
                    const uiEvent = parsed.ui_event && typeof parsed.ui_event === 'object' ? parsed.ui_event : null;
                    if (!uiEvent) continue;
                    const runtimeSeq = parsed.runtime_seq || parsed.seq;
                    parsed = Object.assign({}, uiEvent, {
                        protocol: 'runtime_v2',
                        runtime_seq: runtimeSeq,
                        seq: parsed.seq,
                        session_id: uiEvent.session_id || envelopeSessionId,
                    });
                }
                const eventSessionId = parsed.session_id || parsed.sessionId || runSessionId;
                if (shouldApplySseSeqFilter(parsed)
                    && !sessionStore.shouldAcceptSseEvent(eventSessionId, parsed.seq, parsed.seq_scope || 'legacy')) continue;
                if (parsed.type === 'user_steer' && parsed.steer) {
                    var steerOpId = String(parsed.client_id || parsed.steer_id || '');
                    var optimisticSteerRow = steerOpId ? findSteerProcessRow(runCtx, steerOpId) : null;
                    var reservedSteerIndex = !!(optimisticSteerRow && optimisticSteerRow.dataset.steerEventReserved === '1');
                    var steerEventIndex = reservedSteerIndex && Number.isFinite(Number(runCtx && runCtx.lastUserEventIndex))
                        ? Number(runCtx.lastUserEventIndex)
                        : (parsed.ephemeral && Number.isFinite(Number(parsed.seq)) ? Number(parsed.seq) : streamEventIdx);
                    try {
                        applyMessageEvent(eventSessionId, parsed, steerEventIndex, 'sse');
                    } catch (eStoreSteer) {
                        console.error('store user steer event failed:', eStoreSteer);
                    }
                    removeConsumedFollowupSteer(eventSessionId, parsed);
                    // 通过 operation-id（steer_id/client_id）提交已存在的乐观 pending 行，
                    // 而非再 appendLog 一条新行，避免 append 追问出现「一条灰色 pending + 一条 committed」。
                    // Optimistic append rows are keyed by client_id before the
                    // server steer id exists, so live commit must use the same
                    // priority to update that row instead of creating a second.
                    prepareSteerProcessBoundary(runCtx, parsed.steer_mode || 'interrupt', steerOpId);
                    markSteerEventPosition(runCtx, steerEventIndex, parsed.runtime_seq || parsed.runtimeSeq);
                    if (steerOpId && typeof appendSteerProcessMessage === 'function') {
                        var committedSteerRow = appendSteerProcessMessage(
                            eventSessionId, runCtx, parsed.content || '', steerOpId,
                            String(parsed.steer_mode || 'interrupt'), false
                        );
                        if (committedSteerRow) {
                            if (parsed.client_id) committedSteerRow.dataset.steerClientId = String(parsed.client_id);
                            if (parsed.steer_id) committedSteerRow.dataset.steerId = String(parsed.steer_id);
                            committedSteerRow.removeAttribute('data-steer-event-reserved');
                        }
                    } else {
                        appendLog(runCtx, parsed.content || '', 'user-steer', runSessionId);
                    }
                    if (!reservedSteerIndex) streamEventIdx += 1;
                    continue;
                }
                const reduced = applySessionEvent(parsed, {
                    sessionId: eventSessionId,
                    eventIndex: parsed.ephemeral && Number.isFinite(Number(parsed.seq)) ? Number(parsed.seq) : streamEventIdx,
                    source: 'sse',
                });
                if (reduced.runStateChanged) {
                    if (parsed.type === 'run_finished' || parsed.type === 'run_interrupted' || parsed.type === 'run_failed') {
                        if (runCtx) {
                            runCtx.terminalSeen = true;
                            runCtx.streamCompletedSuccessfully = parsed.type === 'run_finished';
                        }
                        if (
                            runCtx
                            && (parsed.cleanup_scope === 'none' || parsed.checkpoint_ok === false)
                        ) {
                            runCtx.preserveInterruptedPartial = true;
                        }
                        endRunForClient(eventSessionId, runCtx, {
                            finalDelayMs: 80,
                            followupDelayMs: 0,
                            runId: parsed.run_id || parsed.runId || (runCtx && runCtx.runId),
                            reconcileFinal: parsed.type === 'run_finished',
                            discardPartialStreams: parsed.type !== 'run_finished',
                            drainFollowup: true,
                        });
                        streamEventIdx += 1;
                        continue;
                    }
                    syncSessionListIndicatorClasses();
                    continue;
                }
                if (reduced.finalStateChanged) {
                    syncSessionListIndicatorClasses();
                    setSendButtonState();
                }
                if (reduced.contextStateChanged && eventSessionId === currentSessionId) {
                    if (parsed.type === 'context_tokens') applyContextTokenLabelForCurrentSession();
                    if (parsed.type === 'context_tokens') continue;
                }
                if (parsed.ephemeral) {
                    /* 任何携带 agent_id 的 ephemeral 都属于子 agent；无论投递成功与否都不能 fall-through
                       到父 ctx 的 appendLlmStreamDelta，否则会污染主对话区。 */
                    if (parsed.agent_id) { handleSubagentStreamEvent(parsed, streamEventIdx, runSessionId); continue; }
                    if (parsed.type === 'llm_stream_aborted') {
                        removeTemporaryStatus(runCtx);
                        var preserveInterruptedPartial = parsed.cleanup_scope === 'none'
                            || parsed.checkpoint_ok === false;
                        if (runCtx) runCtx.preserveInterruptedPartial = preserveInterruptedPartial;
                        discardLlmStreamChunks(runCtx, parsed);
                        if (!preserveInterruptedPartial) {
                            removeAbortedToolDraftRows(runCtx, parsed);
                            discardProgressStreamChunks(runCtx);
                        } else {
                            finalizeProgressStreamChunks(runCtx);
                        }
                        continue;
                    }
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
                    else if (parsed.type === 'context_tokens' && eventSessionId === currentSessionId) applyContextTokenLabelForCurrentSession();
                    else if (parsed.type === 'cache_stats' && eventSessionId === currentSessionId) applyCacheStatsFromEvent(runCtx, parsed, runSessionId);
                    else if (parsed.type === 'runtime_resumed') {
                        removeTemporaryStatus(runCtx);
                        var resumeSeconds = Math.max(0, Number(parsed.suspended_seconds || 0));
                        appendLog(
                            runCtx,
                            parsed.content || (
                                parsed.cause === 'system_sleep'
                                    ? ('检测到系统睡眠约 ' + Math.round(resumeSeconds) + ' 秒，任务已恢复')
                                    : ('检测到 Agent 进程暂停约 ' + Math.round(resumeSeconds) + ' 秒，任务已恢复')
                            ),
                            'status',
                            runSessionId
                        );
                    }
                    else if (parsed.type === 'status') {
                        var statusContent = String(parsed.content || '');
                        if (parsed.model_switch) {
                            appendModelSwitchStatus(runCtx, parsed, runSessionId);
                            // 使用模型与选择器绑定：fallback 接管后服务端会把
                            // 会话绑定改写为实际模型，静默刷新右下角选择器。
                            if (typeof refreshModelProfileSelectorInBackground === 'function' && runSessionId) {
                                refreshModelProfileSelectorInBackground(runSessionId, { silent: true });
                            }
                            continue;
                        }
                        var isTemporaryStatus = statusContent.indexOf('正在思考中...') >= 0;
                        isTemporaryStatus = isTemporaryStatus || !!parsed.ephemeral || statusContent.indexOf('正在重连') >= 0;
                        if (isTemporaryStatus) upsertTemporaryStatus(runCtx, statusContent, runSessionId);
                        else appendLog(runCtx, statusContent, 'status', runSessionId);
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
                if (parsed.type === 'final') {
                    if (eventSessionId === runSessionId) {
                        markRunFinalSeen(runCtx);
                    }
                    var finalStream = runCtx && runCtx.stream && runCtx.stream.isConnected ? runCtx.stream : getVisibleChatStream();
                    var finalLastUserIdx = latestVisibleUserEventIndex(finalStream);
                    if (hasDuplicateVisibleFinal(finalStream, finalLastUserIdx, parsed.content)) {
                        streamEventIdx += 1;
                        continue;
                    }
                }
                renderMessageRecord(runCtx, reduced.messageRecord || {
                    index: streamEventIdx,
                    event: parsed,
                    source: 'sse',
                }, runSessionId);
                streamEventIdx += 1;
            } catch (e) { console.error('解析事件失败:', e); }
        }
    }
    scheduleFinalVisibleAfterRunIfEnabled(runSessionId, runCtx, { delayMs: 120 });
    return streamEventIdx;
}

function latestVisibleUserEventIndex(stream) {
    var maxIdx = -1;
    if (!stream || !stream.querySelectorAll) return maxIdx;
    stream.querySelectorAll('.msg-wrap--user[data-event-index]').forEach(function (wrap) {
        var n = Number(wrap.getAttribute('data-event-index'));
        if (Number.isFinite(n)) maxIdx = Math.max(maxIdx, Math.floor(n));
    });
    return maxIdx;
}

function hasVisibleFinalAfterUser(stream, userEventIndex) {
    if (!stream || !stream.querySelectorAll) return false;
    var found = false;
    stream.querySelectorAll('.msg-wrap--assistant[data-event-index]').forEach(function (wrap) {
        if (found) return;
        var n = Number(wrap.getAttribute('data-event-index'));
        if (Number.isFinite(n) && Math.floor(n) > userEventIndex) found = true;
    });
    return found;
}

function hasDuplicateVisibleFinal(stream, userEventIndex, content) {
    if (!stream || !stream.querySelectorAll) return false;
    var expected = String(content || '').replace(/\s+/g, ' ').trim();
    if (!expected) return false;
    var found = false;
    stream.querySelectorAll('.msg-wrap--assistant[data-event-index]').forEach(function (wrap) {
        if (found) return;
        var n = Number(wrap.getAttribute('data-event-index'));
        if (!Number.isFinite(n) || Math.floor(n) <= userEventIndex) return;
        var raw = messageRawMarkdown.get(wrap);
        var actual = String(raw != null ? raw : (wrap.textContent || '')).replace(/\s+/g, ' ').trim();
        if (actual === expected) found = true;
    });
    return found;
}

function findStoredFinalAfterUser(sessionId, userEventIndex) {
    var events = [];
    try { events = selectMessageEvents(sessionId) || []; } catch (e) { events = []; }
    for (var i = events.length - 1; i >= 0; i -= 1) {
        var rec = events[i];
        if (!rec || rec.type !== 'final') continue;
        if (Number.isFinite(Number(rec.index)) && Number(rec.index) > userEventIndex) return rec;
    }
    return null;
}

function renderFinalRecordIfMissing(sessionId, ctx, stream, finalRecord, userEventIndex) {
    if (!finalRecord || !finalRecord.event || finalRecord.type !== 'final') return false;
    var content = finalRecord.event.content || '';
    if (hasVisibleFinalAfterUser(stream, userEventIndex)) return true;
    if (hasDuplicateVisibleFinal(stream, userEventIndex, content)) return true;
    var renderCtx = ctx || newDomContext(stream);
    renderCtx.stream = stream;
    renderCtx.lastUserEventIndex = Math.max(renderCtx.lastUserEventIndex || -1, userEventIndex);
    renderMessageRecord(renderCtx, finalRecord, sessionId);
    return hasVisibleFinalAfterUser(stream, userEventIndex);
}

async function ensureFinalVisibleAfterRunIfEnabled(sessionId, ctx, opts) {
    if (!isMyAgentFeatureEnabled('finalReconcile', true)) return false;
    return ensureFinalVisibleAfterRun(sessionId, ctx, opts);
}

function markRunFinalSeen(ctx) {
    if (ctx) ctx.seenFinal = true;
}

function initRunFinalTracking(ctx) {
    if (!ctx) return;
    ctx.seenFinal = false;
    ctx.terminalSeen = false;
    ctx.transportClosed = false;
}

function scheduleFinalVisibleAfterRunIfEnabled(sessionId, ctx, opts) {
    if (!isMyAgentFeatureEnabled('finalReconcile', true)) return;
    if (ctx && ctx.seenFinal === true) return;
    setTimeout(function () {
        if (ctx && ctx.seenFinal === true) return;
        ensureFinalVisibleAfterRun(sessionId, ctx, opts).catch(function (e) {
            console.error('final reconcile failed:', e);
        });
    }, 0);
}

async function ensureFinalVisibleAfterRun(sessionId, ctx, opts) {
    opts = opts || {};
    var sid = String(sessionId || '');
    if (!sid || sid !== currentSessionId) return false;
    var stream = (ctx && ctx.stream && ctx.stream.isConnected) ? ctx.stream : getVisibleChatStream();
    if (!stream) return false;
    var lastUserIdx = latestVisibleUserEventIndex(stream);
    if (hasVisibleFinalAfterUser(stream, lastUserIdx)) return true;
    var storedFinal = findStoredFinalAfterUser(sid, lastUserIdx);
    if (storedFinal) {
        if (renderFinalRecordIfMissing(sid, ctx, stream, storedFinal, lastUserIdx)) return true;
    }
    var delayMs = Math.max(0, Number(opts.delayMs) || 0);
    if (delayMs) await new Promise(function (resolve) { setTimeout(resolve, delayMs); });
    if (sid !== currentSessionId) return false;
    stream = getVisibleChatStream();
    if (!stream || hasVisibleFinalAfterUser(stream, lastUserIdx)) return true;
    return false;
}

async function startContinueAfterSubagents(sessionId) {
    if (!sessionId || sessionId !== currentSessionId) return;
    delete subagentContinueDismissedForSession[sessionId];
    if (isSessionRunning(sessionId) || subagentContinueInFlight) {
        updateSubagentContinueBanner(sessionId);
        return;
    }
    if (isSendPipelineLocked(sessionId)) {
        updateSubagentContinueBanner(sessionId);
        return;
    }
    hideSubagentContinueBanner();
    subagentContinueSessionId = sessionId;
    subagentContinueInFlight = true;
    var runCtx = null;
    var runSessionId = sessionId;
    var continuationFailed = false;
    try {
    if (typeof ensureLatestHistoryTailForLiveAppend === 'function') {
        var continuationTailReady = await ensureLatestHistoryTailForLiveAppend(sessionId);
        if (!continuationTailReady) {
            showUiAlert({
                title: '无法继续任务',
                message: '当前页面正在查看较早历史，且未能恢复最新历史尾部。请重试。',
                variant: 'error'
            });
            return;
        }
    }
    var continueUrl = '/sessions/' + encodeURIComponent(sessionId) + '/continue-subagents';
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
        const preCount = await getUiEventCount(runSessionId, { preferCache: true });
        if (!getVisibleChatStream()) ensureVisibleChatStreamSlot();
        runCtx = newDomContext(getVisibleChatStream());
        if (sessionStore && typeof sessionStore.resetSseSeq === 'function') {
            sessionStore.resetSseSeq(runSessionId);
        }
        initRunFinalTracking(runCtx);
        runCtx.runStartedAt = new Date().toISOString();
        if (getSessionRunState(runSessionId) && getSessionRunState(runSessionId).ctx) {
            runCtx = getSessionRunState(runSessionId).ctx;
            initRunFinalTracking(runCtx);
            if (!runCtx.runStartedAt) runCtx.runStartedAt = new Date().toISOString();
        } else {
            runCtx.lastUserEventIndex = Math.max(0, preCount - 1);
            resetLlmState(runCtx);
            finalizeLlmStreamChunks(runCtx);
        }
        const ac = new AbortController();
        setSessionRunState(runSessionId, { controller: ac, ctx: runCtx });
        if (sessionStore && typeof sessionStore.resetSseSeq === 'function') {
            sessionStore.resetSseSeq(runSessionId);
        }
        setSendButtonState();
        syncSessionListIndicatorClasses();
        liveAutoFollow = true;
        streamProcNearBottom = true;
        scheduleContextTokensAfterPaint(runSessionId);
        let streamEventIdx = preCount;
        try {
            await consumeAgentSseResponse(response, runCtx, runSessionId, streamEventIdx);
        } catch (error) {
            if (error.name === 'AbortError') {
                if (getRunAbortReason(runSessionId, runCtx) === 'user') appendLog(runCtx, '任务已中断', 'status', runSessionId);
            }
            else {
                continuationFailed = true;
                console.error('续接 subagent 失败:', error);
                const msg = (error && error.message) ? String(error.message) : String(error);
                appendLog(runCtx, '续接失败: ' + msg, 'error-log', runSessionId);
            }
        } finally {
            finalizeLlmStreamChunks(runCtx);
            finalizeProgressStreamChunks(runCtx);
            if (runSessionId === currentSessionId
                && getRunAbortReason(runSessionId, runCtx) !== 'user'
                && !isServerStreamActive(runSessionId)) {
                scheduleFinalVisibleAfterRunIfEnabled(runSessionId, runCtx, { delayMs: 120 });
            }
            if (liveAutoFollow) {
                finishStreamScrollIfFollow(runCtx, runSessionId);
            }
            if (getSessionRunState(runSessionId)) clearSessionRunState(runSessionId);
            setSendButtonState();
            syncSessionListIndicatorClasses();
            void refreshSingleSessionRow(runSessionId);
            applyContextTokenLabelForCurrentSession();
            if (continuationFailed || isServerStreamActive(runSessionId)) {
                scheduleActiveSessionReconnect(runSessionId, { delayMs: 120, failure: continuationFailed });
            } else {
                resetStreamReconnectState(runSessionId);
            }
        }
        hideSubagentContinueBanner();
        if (!subagentContinueDismissedForSession[sessionId]) updateSubagentContinueBanner(sessionId);
    } finally {
        if (subagentContinueSessionId === runSessionId) subagentContinueSessionId = null;
        subagentContinueInFlight = false;
        var continuationStoppedByUser = !!runCtx && getRunAbortReason(runSessionId, runCtx) === 'user';
        if (!continuationStoppedByUser
            && getFollowupQueue(runSessionId).some(function (entry) { return entry && !entry.status; })) {
            var followupSync = syncFollowupQueueFromServer(runSessionId);
            void Promise.resolve(followupSync).then(function () {
                scheduleFollowupQueueDrain(runSessionId, 0);
            }).catch(function (error) {
                console.warn('follow-up reconciliation failed; auto-drain skipped', error);
            });
        }
    }
}

var serverRecoveryObservationBySession = Object.create(null);

function observeServerOwnedReactRecovery(sessionId) {
    var sid = String(sessionId || '');
    if (!sid || serverRecoveryObservationBySession[sid]) return;
    serverRecoveryObservationBySession[sid] = (async function () {
        var delays = [0, 120, 350, 800, 1600];
        for (var i = 0; i < delays.length; i += 1) {
            if (delays[i]) await sleepMs(delays[i]);
            if (sid !== String(currentSessionId || '')) return;
            if (typeof reconcileRunStateFromServer === 'function') {
                await reconcileRunStateFromServer({ silent: true });
            }
            if (sid !== String(currentSessionId || '')) return;
            if (isServerStreamActive(sid) || isSessionRunning(sid)) {
                if (typeof maybeStartStreamPollForSession === 'function') {
                    maybeStartStreamPollForSession(sid, { skipInitialLoad: true });
                }
                return;
            }
        }
    })().catch(function (error) {
        console.warn('观察后端恢复流失败:', error);
    }).finally(function () {
        delete serverRecoveryObservationBySession[sid];
    });
}

function maybeAutoResumeInterruptedReact(sessionId, sessionDetail) {
    var sid = String(sessionId || '');
    var detail = sessionDetail || {};
    if (!sid || sid !== currentSessionId) return;
    if (typeof navigator !== 'undefined' && navigator.onLine === false) return;
    var pendingHuman = detail.pending_human_interactions || {};
    if (Number(pendingHuman.total || 0) > 0) return;
    if (typeof pendingHumanInteractionRecords === 'function'
        && pendingHumanInteractionRecords(sid).length > 0) return;
    if (!detail.react_auto_resume || !detail.react_can_continue || detail.run_active || detail.stream_active) return;
    if (isSessionRunning(sid) || subagentContinueInFlight) return;
    // Recovery execution is server-owned. The browser only waits for the
    // startup/background worker to publish an active run and then attaches an
    // observer stream; it must never start a competing /continue producer.
    observeServerOwnedReactRecovery(sid);
}

window.addEventListener('online', function () {
    var sid = String(currentSessionId || '');
    if (!sid) return;
    scheduleActiveSessionReconnect(sid, { delayMs: 100, reset: true });
    setTimeout(function () { void refreshSingleSessionRow(sid); }, 250);
});

function nowPipelineMs() {
    return (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
}

function isClientPipelineTerminalStep(label, step) {
    var s = String(step || '');
    var l = String(label || '');
    if (l.indexOf('client_send_pipeline') >= 0) {
        return s === 'release_send_lock';
    }
    if (l.indexOf('client_followup') >= 0) {
        return s === 'followup_cancel_after_steer'
            || s === 'followup_restart_takeover'
            || s === 'followup_accepted_by_running_agent'
            || s === 'followup_steer_error'
            || s === 'followup_fallback_to_chat';
    }
    return /(?:final|finish|done|error|failed|cancel|release)$/i.test(s);
}

function flushClientPipelineTiming(ctx, finalStep) {
    if (!ctx || ctx._timingFlushed) return;
    var steps = ctx._timingSteps || {};
    var names = Object.keys(steps);
    if (!names.length) return;
    var now = nowPipelineMs();
    var label = String(ctx.label || 'client_pipeline_step_timing').replace(/_step_timing$/, '_timing');
    var payload = {
        label: label,
        session_id: ctx.sessionId || '',
        run_id: ctx.runId || '',
        mode: ctx.mode || '',
        total_ms: Math.max(0, Math.round(now - Number(ctx.startedAt || now))),
        final_step: finalStep || '',
        steps: steps
    };
    ctx._timingFlushed = true;
    try {
        var stepText = names.map(function (name) {
            return name + '=' + Math.max(0, Math.round(Number(steps[name] && steps[name].ms || 0))) + 'ms';
        }).join(' ');
        console.info(
            payload.label,
            'session=' + payload.session_id,
            'total=' + payload.total_ms + 'ms',
            'run_id=' + payload.run_id,
            'mode=' + payload.mode,
            stepText
        );
    } catch (e) { /* ignore */ }
    try {
        const body = JSON.stringify(payload);
        if (navigator && typeof navigator.sendBeacon === 'function') {
            const blob = new Blob([body], { type: 'application/json' });
            if (navigator.sendBeacon('/api/client_timing', blob)) return;
        }
        fetch('/api/client_timing', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: body,
            keepalive: true
        }).catch(function () { /* ignore */ });
    } catch (e) { /* ignore */ }
}

function reportClientPipelineStep(ctx, step, startedAt, extra) {
    if (!ctx || !step) return;
    const now = nowPipelineMs();
    var stepName = String(step || '');
    if (!ctx._timingSteps) ctx._timingSteps = {};
    ctx._timingSteps[stepName] = {
        ms: Math.max(0, Math.round(now - Number(startedAt || now))),
        since_start_ms: Math.max(0, Math.round(now - Number(ctx.startedAt || now))),
        extra: extra || {}
    };
    if (isClientPipelineTerminalStep(ctx.label, stepName)) flushClientPipelineTiming(ctx, stepName);
}

function applySkippedRuntimeV2EventMetadata(event, runCtx, sessionId) {
    if (!event || !event.skip_ui) return;
    const runtimeEvent = event.runtime_event && typeof event.runtime_event === 'object' ? event.runtime_event : null;
    if (!runtimeEvent || (runtimeEvent.type !== 'message_user' && runtimeEvent.type !== 'user_turn_committed')) return;
    const runtimeSeq = Number(event.runtime_seq || event.seq);
    if (!Number.isFinite(runtimeSeq) || runtimeSeq <= 0) return;
    if (runCtx) runCtx.lastUserRuntimeSeq = Math.floor(runtimeSeq);
    if (sessionId && sessionId !== currentSessionId) return;
    const eventIndex = runCtx && Number.isFinite(Number(runCtx.lastUserEventIndex))
        ? Math.floor(Number(runCtx.lastUserEventIndex))
        : NaN;
    let wrap = null;
    const stream = (runCtx && runCtx.stream) || getVisibleChatStream();
    if (stream && Number.isFinite(eventIndex)) {
        try {
            wrap = stream.querySelector('.msg-wrap--user[data-event-index="' + String(eventIndex) + '"]');
        } catch (e) { wrap = null; }
    }
    if (!wrap && stream) {
        const users = stream.querySelectorAll('.msg-wrap--user');
        wrap = users.length ? users[users.length - 1] : null;
    }
    if (wrap) {
        wrap.setAttribute('data-runtime-seq', String(Math.floor(runtimeSeq)));
    }
}

function restoreReactGenerationFromProcessGroup(ctx, processGroup) {
    if (!ctx || !processGroup || !processGroup.querySelectorAll) return 0;
    var generation = Math.max(0, Math.floor(Number(ctx.reactGeneration) || 0));
    var rows = processGroup.querySelectorAll('.feed-item[data-react-generation]');
    rows.forEach(function (row) {
        var value = Number(row.getAttribute('data-react-generation'));
        if (Number.isFinite(value)) generation = Math.max(generation, Math.floor(value));
    });
    ctx.reactGeneration = generation;
    return generation;
}

async function attachSessionEventStream(sessionId, opts) {
    opts = opts || {};
    if (!sessionId || getSessionRunState(sessionId)) return;
    if (!opts.force && !isServerStreamActive(sessionId)) return;
    var runSessionId = sessionId;
    var runCtx = null;
    var reattachFailed = false;
    try {
        if (runSessionId !== currentSessionId) return;
        if (!opts.skipInitialLoad) {
            await loadSessionMessages(runSessionId, 'saved-or-bottom', { preloadOlderIfShort: true });
            if (runSessionId !== currentSessionId) return;
        } else if (!Number.isFinite(Number(opts.afterIndex)) && typeof ensureLatestHistoryTailForLiveAppend === 'function') {
            var attachTailReady = await ensureLatestHistoryTailForLiveAppend(runSessionId);
            if (!attachTailReady || runSessionId !== currentSessionId) return;
        }
        if (!getVisibleChatStream()) ensureVisibleChatStreamSlot();
        runCtx = newDomContext(getVisibleChatStream());
        var activeInfoForAttach = sessionStore.getActiveRunInfo(runSessionId) || {};
        runCtx.runId = String(activeInfoForAttach.run_id || activeInfoForAttach.runId || '');
        runCtx.runStartedAt = activeInfoForAttach.started_at || new Date().toISOString();
        var existingProcessGroup = runCtx.stream.querySelector('.process-aggregate:last-of-type');
        if (existingProcessGroup) {
            runCtx.currentProcessGroup = existingProcessGroup;
            // History replay may contain interrupt generations greater than zero.
            // Restore that ordering state before appending reconnected live rows.
            restoreReactGenerationFromProcessGroup(runCtx, existingProcessGroup);
            existingProcessGroup.classList.add('is-running');
            bindProcessAggregate(existingProcessGroup);
            var activeInfo = sessionStore.getActiveRunInfo(runSessionId) || {};
            if (activeInfo.started_at) {
                applyRunStartedAtToProcessGroup(existingProcessGroup, activeInfo.started_at);
            } else if (!existingProcessGroup.dataset.procStartedAt && !existingProcessGroup.dataset.procDurationMs) {
                existingProcessGroup.dataset.procStartedAt = String(procNow());
                refreshProcessAggregateStats(existingProcessGroup);
            }
            existingProcessGroup.classList.remove('is-collapsed');
            var top = existingProcessGroup.querySelector('.process-aggregate-top');
            if (top) top.setAttribute('aria-expanded', 'true');
        }
        resetLlmState(runCtx);
        initRunFinalTracking(runCtx);
        finalizeLlmStreamChunks(runCtx);
        const ac = new AbortController();
        setSessionRunState(runSessionId, {
            controller: ac,
            ctx: runCtx,
            reattached: true,
            runId: runCtx.runId,
        });
        setSendButtonState();
        syncSessionListIndicatorClasses();
        liveAutoFollow = true;
        streamProcNearBottom = true;
        const preCount = Number.isFinite(Number(opts.afterIndex))
            ? Math.max(0, Math.floor(Number(opts.afterIndex)))
            : await getUiEventCount(runSessionId, { preferCache: true });
        const streamUrl = '/sessions/' + encodeURIComponent(runSessionId)
            + '/stream?after_index=' + encodeURIComponent(String(preCount - 1));
        const response = await fetch(streamUrl, { signal: ac.signal });
        await consumeAgentSseResponse(response, runCtx, runSessionId, preCount);
    } catch (error) {
        if (error && error.name === 'AbortError') return;
        reattachFailed = true;
        console.error('reattach stream failed:', error);
        const msg = (error && error.message) ? String(error.message) : String(error);
        if (runCtx && runSessionId === currentSessionId) appendLog(runCtx, '恢复实时流失败: ' + msg, 'error-log', runSessionId);
    } finally {
        if (runCtx) {
            finalizeLlmStreamChunks(runCtx);
            finalizeProgressStreamChunks(runCtx);
        }
        if (runSessionId === currentSessionId
            && getRunAbortReason(runSessionId, runCtx) !== 'user'
            && !isServerStreamActive(runSessionId)) {
            scheduleFinalVisibleAfterRunIfEnabled(runSessionId, runCtx, { delayMs: 120 });
        }
        if (getSessionRunState(runSessionId) && getSessionRunState(runSessionId).reattached) {
            clearSessionRunState(runSessionId);
        }
        setSendButtonState();
        syncSessionListIndicatorClasses();
        void refreshSingleSessionRow(runSessionId);
        setTimeout(function () { reconcileRunStateFromServer({ silent: true }); }, 800);
        if (reattachFailed) {
            scheduleActiveSessionReconnect(runSessionId, { delayMs: 120, failure: true });
        } else if (isServerStreamActive(runSessionId)) {
            scheduleActiveSessionReconnect(runSessionId, { delayMs: 1200 });
        } else {
            resetStreamReconnectState(runSessionId);
        }
        applyContextTokenLabelForCurrentSession();
        if (runSessionId === currentSessionId) {
            updateSubagentContinueBanner(runSessionId);
        }
    }
}

function scheduleActiveSessionReconnect(sessionId, opts) {
    if (!isMyAgentFeatureEnabled('streamReconnect', true)) return;
    opts = opts || {};
    var sid = String(sessionId || '');
    if (!sid) return;
    if (opts.reset) resetStreamReconnectState(sid);
    if (isStreamConsuming(sid)) {
        resetStreamReconnectState(sid);
        return;
    }
    var state = streamReconnectState(sid);
    if (state.timer) return;
    if (state.exhausted || state.attempts >= STREAM_RECONNECT_MAX_ATTEMPTS) {
        if (!state.exhausted) {
            state.exhausted = true;
            reportStreamReconnectExhausted(sid);
        }
        return;
    }
    var countFailure = !!opts.failure;
    var baseDelay = Math.max(0, Number(opts.delayMs) || 0);
    var delayMs = Math.max(
        baseDelay,
        Math.min(STREAM_RECONNECT_MAX_DELAY_MS, STREAM_RECONNECT_BASE_DELAY_MS * Math.pow(2, state.attempts))
    );
    state.timer = setTimeout(async function () {
        state.timer = null;
        if (sid !== currentSessionId) return;
        if (countFailure) state.attempts += 1;
        try {
            if (typeof reconcileRunStateFromServer === 'function') {
                await reconcileRunStateFromServer({ silent: true });
            }
            if (sid !== currentSessionId) return;
            if (isStreamConsuming(sid)) {
                resetStreamReconnectState(sid);
                return;
            }
            if ((isServerStreamActive(sid) || isSessionRunning(sid)) && typeof maybeStartStreamPollForSession === 'function') {
                maybeStartStreamPollForSession(sid, { skipInitialLoad: true });
            } else {
                resetStreamReconnectState(sid);
            }
        } catch (e) {
            scheduleActiveSessionReconnect(sid, { failure: countFailure });
        }
    }, delayMs);
}

async function processRewriteTruncateAsync(pr) {
    try {
        const anchor = document.querySelector('.msg-wrap--user[data-truncate-from="' + String(pr.before) + '"]');
        const res = await truncateSessionOnServer(pr.before, {
            sessionId: pr.sessionId,
            beforeSeq: pr.beforeSeq,
            backup: false
        });
        if (!res || !res.ok) {
            showUiAlert({
                title: '截断失败',
                message: describeServerSyncFailure(res, '无法同步服务器，改写未生效。'),
                variant: 'error'
            });
            return false;
        }
        if (currentSessionId === pr.sessionId) {
            if (anchor) {
                if (activeInlineRewriteWrap === anchor) activeInlineRewriteWrap = null;
            }
        }
        applyClientHistoryTruncate(pr.sessionId, pr.before, anchor);
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

function getFollowupQueue(sessionId) {
    const sid = String(sessionId || '');
    if (!sid) return [];
    if (!followupQueueLoadedBySession[sid]) {
        followupQueueBySession[sid] = readStoredFollowupQueue(sid);
        followupQueueLoadedBySession[sid] = true;
    }
    if (!followupQueueBySession[sid]) followupQueueBySession[sid] = [];
    return followupQueueBySession[sid];
}

function followupQueueStorageKey(sessionId) {
    return LS_FOLLOWUP_QUEUE_PREFIX + String(sessionId || '');
}

function defaultSteerMode() {
    return String(window.__MYAGENT_STEER_MODE__ || 'append').toLowerCase() === 'interrupt'
        ? 'interrupt'
        : 'append';
}

function normalizeStoredFollowupItem(item) {
    if (!item || typeof item !== 'object') return null;
    var text = String(item.text || '').trim();
    if (!text) return null;
    var display = String(item.display || item.text || '').trim();
    var skills = Array.isArray(item.skills)
        ? item.skills.map(function (skill) { return String(skill || '').trim(); }).filter(Boolean)
        : [];
    var attachments = Array.isArray(item.attachments)
        ? item.attachments.filter(function (attachment) {
            return attachment && String(attachment.path || '').trim();
        }).map(function (attachment) {
            return {
                path: String(attachment.path),
                name: String(attachment.name || ''),
                size: Number(attachment.size || 0),
            };
        })
        : [];
    var restoredStatus = String(item.status || '');
    if (restoredStatus === 'submitting' || restoredStatus === 'sending') restoredStatus = '';
    // A browser reload cannot resume the in-flight DELETE request. If the
    // durable steer id is known, reconcile it as accepted; otherwise restore a
    // normal pending row so the user never gets a permanently disabled item.
    if (restoredStatus === 'withdrawing') {
        restoredStatus = String(item.steerId || '') ? 'accepted' : '';
    }
    return {
        id: item.id || ('stored-followup-' + (followupQueueSeq++)),
        text: text,
        display: display || text,
        skills: skills,
        attachments: attachments,
        createdAt: Number(item.createdAt) || Date.now(),
        order: Number.isFinite(Number(item.order)) ? Number(item.order) : undefined,
        steerMode: String(item.steerMode || item.mode || defaultSteerMode()) === 'interrupt' ? 'interrupt' : 'append',
        // 恢复提交期间的 in-flight 状态：刷新/崩溃后可继续恢复，不再静默丢失。
        clientId: String(item.clientId || ''),
        steerId: String(item.steerId || ''),
        status: restoredStatus,
        replacementRunId: String(item.replacementRunId || ''),
        awaitingRunEnd: item.awaitingRunEnd !== false,
        deferUntilRunEnd: !!item.deferUntilRunEnd,
    };
}

function readStoredFollowupQueue(sessionId) {
    try {
        var raw = localStorage.getItem(followupQueueStorageKey(sessionId));
        if (!raw) return [];
        var arr = JSON.parse(raw);
        if (!Array.isArray(arr)) return [];
        var out = arr.map(normalizeStoredFollowupItem).filter(Boolean);
        out.forEach(function (item) {
            var n = Number(item.id);
            if (Number.isFinite(n)) followupQueueSeq = Math.max(followupQueueSeq, Math.floor(n) + 1);
        });
        return out;
    } catch (e) {
        return [];
    }
}

function persistFollowupQueue(sessionId) {
    const sid = String(sessionId || '');
    if (!sid) return;
    var q = followupQueueBySession[sid] || [];
    // 持久化所有非终态条目：包括 submitting/sending/accepted/restarting，
    // 这样刷新/崩溃/请求未达服务端时仍可恢复。只有 'sent'（/chat 已成功开跑）
    // 视为本地终态不再持久化；consumed/cancelled 由 takeFollowupItem 直接移除。
    var pending = q.filter(function (item) {
        var status = item && item.status ? String(item.status) : '';
        return item && item.text && status !== 'sent';
    }).map(function (item) {
        return {
            id: item.id,
            text: item.text,
            display: item.display || item.text,
            skills: Array.isArray(item.skills) ? item.skills : [],
            attachments: Array.isArray(item.attachments) ? item.attachments : [],
            createdAt: item.createdAt || Date.now(),
            order: item.order,
            steerMode: item.steerMode === 'append' ? 'append' : 'interrupt',
            clientId: item.clientId || '',
            steerId: item.steerId || '',
            status: item.status || '',
            replacementRunId: item.replacementRunId || '',
            awaitingRunEnd: item.awaitingRunEnd !== false,
            deferUntilRunEnd: !!item.deferUntilRunEnd,
        };
    });
    try {
        var key = followupQueueStorageKey(sid);
        if (pending.length) localStorage.setItem(key, JSON.stringify(pending));
        else localStorage.removeItem(key);
    } catch (e) { /* ignore */ }
}

function removeStoredFollowupQueue(sessionId) {
    const sid = String(sessionId || '');
    if (!sid) return;
    delete followupQueueBySession[sid];
    delete followupQueueLoadedBySession[sid];
    delete followupManualDispatchEpochBySession[sid];
    try { localStorage.removeItem(followupQueueStorageKey(sid)); } catch (e) { /* ignore */ }
}

function inputHasSendableText() {
    if (!messageInput) return false;
    return hasSendableText(messageInput.value);
}

var followupDragState = null;
var FOLLOWUP_DRAG_TOUCH_THRESHOLD = 8;

function startFollowupDrag(sessionId, item, row, ev) {
    if (!item || item.status) return;
    if (followupDragState && followupDragState.mode === 'touch') {
        if (ev && ev.preventDefault) ev.preventDefault();
        return;
    }
    followupDragState = {
        sid: String(sessionId || ''),
        itemId: String(item.id),
        row: row,
        mode: 'html5',
    };
    if (row && row.classList) row.classList.add('is-dragging');
    if (ev && ev.dataTransfer) {
        ev.dataTransfer.effectAllowed = 'move';
        try { ev.dataTransfer.setData('text/plain', String(item.id)); } catch (e) { /* ignore */ }
    }
}

function clearFollowupDragIndicators(panel) {
    if (!panel) return;
    var rows = panel.querySelectorAll('.followup-queue-row');
    for (var i = 0; i < rows.length; i += 1) {
        rows[i].classList.remove('is-drag-over-before');
        rows[i].classList.remove('is-drag-over-after');
    }
}

function endFollowupDrag() {
    if (!followupDragState) return;
    if (followupDragState.row && followupDragState.row.classList) {
        followupDragState.row.classList.remove('is-dragging');
    }
    followupDragState = null;
    clearFollowupDragIndicators(document.getElementById('followup-queue-panel'));
}

function startFollowupTouchDrag(sessionId, item, row, ev) {
    if (!item || item.status) return;
    if (followupDragState) endFollowupDrag();
    followupDragState = {
        sid: String(sessionId || ''),
        itemId: String(item.id),
        row: row,
        mode: 'touch',
        pointerId: ev.pointerId,
        active: false,
        startX: ev.clientX,
        startY: ev.clientY,
        targetRow: null,
        after: false,
    };
    try {
        if (ev.currentTarget && ev.currentTarget.setPointerCapture) {
            ev.currentTarget.setPointerCapture(ev.pointerId);
        }
    } catch (e) { /* ignore */ }
    if (ev.preventDefault) ev.preventDefault();
}

function autoScrollFollowupQueuePanel(panel, clientY) {
    if (!panel || panel.scrollHeight <= panel.clientHeight) return;
    var rect = panel.getBoundingClientRect();
    var zone = 28;
    if (clientY < rect.top + zone) panel.scrollTop -= 10;
    else if (clientY > rect.bottom - zone) panel.scrollTop += 10;
}

function onFollowupTouchDragMove(ev) {
    var state = followupDragState;
    if (!state || state.mode !== 'touch' || state.pointerId !== ev.pointerId) return;
    if (!state.active) {
        var dx = ev.clientX - state.startX;
        var dy = ev.clientY - state.startY;
        if (Math.abs(dx) < FOLLOWUP_DRAG_TOUCH_THRESHOLD && Math.abs(dy) < FOLLOWUP_DRAG_TOUCH_THRESHOLD) return;
        state.active = true;
        if (state.row && state.row.classList) state.row.classList.add('is-dragging');
    }
    if (ev.preventDefault) ev.preventDefault();
    var panel = document.getElementById('followup-queue-panel');
    if (!panel) return;
    autoScrollFollowupQueuePanel(panel, ev.clientY);
    var el = document.elementFromPoint ? document.elementFromPoint(ev.clientX, ev.clientY) : null;
    var target = el && el.closest ? el.closest('.followup-queue-row') : null;
    if (!target || !target.dataset || !target.dataset.id
        || target.dataset.reorderable !== 'true' || target === state.row) {
        clearFollowupDragIndicators(panel);
        state.targetRow = null;
        return;
    }
    var rect = target.getBoundingClientRect();
    var after = ev.clientY > rect.top + rect.height / 2;
    clearFollowupDragIndicators(panel);
    target.classList.add(after ? 'is-drag-over-after' : 'is-drag-over-before');
    state.targetRow = target;
    state.after = after;
}

function onFollowupTouchDragEnd(ev) {
    var state = followupDragState;
    if (!state || state.mode !== 'touch' || state.pointerId !== ev.pointerId) return;
    var target = state.targetRow;
    var after = state.after;
    var sid = state.sid;
    var itemId = state.itemId;
    var active = state.active;
    endFollowupDrag();
    if (active && target && target.dataset && target.dataset.id) {
        moveFollowupQueueItem(sid, itemId, target.dataset.id, after ? 'after' : 'before');
    }
}

function ensureFollowupQueueHost() {
    var existing = document.getElementById('followup-queue-panel');
    if (existing) return existing;
    var panel = document.createElement('div');
    panel.id = 'followup-queue-panel';
    panel.className = 'followup-queue-panel';
    panel.setAttribute('aria-live', 'polite');
    if (!panel.dataset.dragReady) {
        panel.dataset.dragReady = '1';
        panel.addEventListener('dragover', function (e) {
            if (!followupDragState) return;
            var target = e.target && e.target.closest ? e.target.closest('.followup-queue-row') : null;
            if (!target || !target.dataset || !target.dataset.id
                || target.dataset.reorderable !== 'true' || target === followupDragState.row) {
                clearFollowupDragIndicators(panel);
                if (e.dataTransfer) e.dataTransfer.dropEffect = 'none';
                return;
            }
            e.preventDefault();
            if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
            var rect = target.getBoundingClientRect();
            var after = e.clientY > rect.top + rect.height / 2;
            clearFollowupDragIndicators(panel);
            target.classList.add(after ? 'is-drag-over-after' : 'is-drag-over-before');
        });
        panel.addEventListener('drop', function (e) {
            if (!followupDragState) return;
            e.preventDefault();
            var target = e.target && e.target.closest ? e.target.closest('.followup-queue-row') : null;
            if (!target || !target.dataset || !target.dataset.id
                || target.dataset.reorderable !== 'true' || target === followupDragState.row) return;
            var after = target.classList.contains('is-drag-over-after');
            var sid = followupDragState.sid;
            var itemId = followupDragState.itemId;
            endFollowupDrag();
            moveFollowupQueueItem(sid, itemId, target.dataset.id, after ? 'after' : 'before');
        });
    }
    var anchor = messageInput && messageInput.closest ? messageInput.closest('.composer-row') : null;
    var host = anchor && anchor.parentNode ? anchor.parentNode : null;
    if (host && anchor) host.insertBefore(panel, anchor);
    else document.body.appendChild(panel);
    return panel;
}

function positionFollowupQueuePanel() {
    var panel = document.getElementById('followup-queue-panel');
    if (!panel) return;
    panel.style.left = '';
    panel.style.top = '';
    panel.style.width = '';
}

var activeFollowupModePickerClose = null;

function closeActiveFollowupModePicker() {
    if (typeof activeFollowupModePickerClose !== 'function') return;
    var close = activeFollowupModePickerClose;
    activeFollowupModePickerClose = null;
    close();
}

function followupQueueRenderSignature(sessionId, queue) {
    var sid = String(sessionId || '');
    var running = !!(isSessionRunning(sid) || isServerStreamActive(sid));
    var humanQuestionCount = typeof pendingHumanQuestions === 'function'
        ? pendingHumanQuestions(sid).length
        : 0;
    var items = (Array.isArray(queue) ? queue : []).map(function (item) {
        return {
            id: String((item && item.id) || ''),
            status: String((item && item.status) || ''),
            steerMode: item && item.steerMode === 'append' ? 'append' : 'interrupt',
            display: String((item && (item.display || item.text)) || ''),
            skills: Array.isArray(item && item.skills) ? item.skills.map(String) : [],
            deferUntilRunEnd: !!(item && item.deferUntilRunEnd),
            awaitingRunEnd: !!(item && item.awaitingRunEnd),
        };
    });
    return JSON.stringify({ sid: sid, running: running, humanQuestionCount: humanQuestionCount, items: items });
}

function refreshFollowupQueueRenderSignature(sessionId) {
    var sid = String(sessionId || '');
    var panel = document.getElementById('followup-queue-panel');
    if (!panel || panel.dataset.sessionId !== sid) return;
    panel.dataset.renderSignature = followupQueueRenderSignature(sid, getFollowupQueue(sid));
}

function createFollowupModePicker(item, sessionId) {
    var picker = document.createElement('div');
    picker.className = 'followup-mode-picker';
    var choices = [
        { value: 'interrupt', label: '打断', description: '立即插入当前运行' },
        { value: 'append', label: '追加', description: '下一轮继续处理' },
    ];
    var visualSelect = document.createElement('select');
    visualSelect.className = 'followup-queue-mode';
    visualSelect.setAttribute('aria-hidden', 'true');
    visualSelect.tabIndex = -1;
    choices.forEach(function (choice) {
        var visualOption = document.createElement('option');
        visualOption.value = choice.value;
        visualOption.textContent = choice.label;
        visualSelect.appendChild(visualOption);
    });
    var direction = document.createElement('span');
    direction.className = 'followup-mode-direction';
    direction.setAttribute('aria-hidden', 'true');
    var trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'followup-mode-hit-target';
    trigger.setAttribute('aria-label', '追问发送模式');
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.disabled = !!item.status;
    visualSelect.disabled = !!item.status;

    var menu = document.createElement('div');
    menu.className = 'followup-mode-menu';
    menu.setAttribute('role', 'listbox');
    menu.setAttribute('aria-label', '选择追问发送模式');
    menu.hidden = true;
    var optionRows = [];
    var outsideHandler = null;

    function currentMode() {
        return item.steerMode === 'append' ? 'append' : 'interrupt';
    }

    function sync() {
        var mode = currentMode();
        picker.dataset.mode = mode;
        visualSelect.value = mode;
        optionRows.forEach(function (row) {
            var selected = row.dataset.mode === mode;
            row.classList.toggle('is-selected', selected);
            row.setAttribute('aria-selected', selected ? 'true' : 'false');
        });
    }

    function closeMenu() {
        menu.hidden = true;
        picker.classList.remove('is-open');
        trigger.setAttribute('aria-expanded', 'false');
        menu.style.left = '';
        menu.style.top = '';
        menu.style.width = '';
        menu.style.maxHeight = '';
        if (menu.parentNode !== picker) picker.appendChild(menu);
        if (outsideHandler) {
            document.removeEventListener('pointerdown', outsideHandler, true);
            window.removeEventListener('resize', closeMenu);
            outsideHandler = null;
        }
        if (activeFollowupModePickerClose === closeMenu) activeFollowupModePickerClose = null;
    }

    function openMenu(focusSelected) {
        if (trigger.disabled) return;
        closeActiveFollowupModePicker();
        document.body.appendChild(menu);
        menu.hidden = false;
        picker.classList.add('is-open');
        trigger.setAttribute('aria-expanded', 'true');
        var triggerRect = trigger.getBoundingClientRect();
        var viewportWidth = Math.max(document.documentElement.clientWidth || 0, window.innerWidth || 0);
        var viewportHeight = Math.max(document.documentElement.clientHeight || 0, window.innerHeight || 0);
        var menuWidth = Math.min(202, Math.max(150, viewportWidth - 16));
        menu.style.width = menuWidth + 'px';
        var menuHeight = menu.getBoundingClientRect().height;
        var roomBelow = viewportHeight - triggerRect.bottom - 8;
        var roomAbove = triggerRect.top - 8;
        var openBelow = roomBelow >= menuHeight || roomBelow >= roomAbove;
        var availableHeight = Math.max(76, (openBelow ? roomBelow : roomAbove) - 6);
        var left = Math.min(
            Math.max(8, triggerRect.right - menuWidth),
            Math.max(8, viewportWidth - menuWidth - 8)
        );
        var top = openBelow
            ? Math.min(viewportHeight - 8, triggerRect.bottom + 5)
            : Math.max(8, triggerRect.top - Math.min(menuHeight, availableHeight) - 5);
        menu.style.left = left + 'px';
        menu.style.top = top + 'px';
        menu.style.maxHeight = availableHeight + 'px';
        activeFollowupModePickerClose = closeMenu;
        outsideHandler = function (event) {
            if (!picker.contains(event.target) && !menu.contains(event.target)) closeMenu();
        };
        document.addEventListener('pointerdown', outsideHandler, true);
        window.addEventListener('resize', closeMenu);
        if (focusSelected) {
            var selectedRow = optionRows.find(function (row) { return row.getAttribute('aria-selected') === 'true'; });
            if (selectedRow) requestAnimationFrame(function () { selectedRow.focus(); });
        }
    }

    function chooseMode(mode) {
        item.steerMode = mode === 'append' ? 'append' : 'interrupt';
        persistFollowupQueue(sessionId);
        sync();
        refreshFollowupQueueRenderSignature(sessionId);
        closeMenu();
        trigger.focus();
    }

    choices.forEach(function (choice) {
        var option = document.createElement('button');
        option.type = 'button';
        option.className = 'followup-mode-option';
        option.dataset.mode = choice.value;
        option.setAttribute('role', 'option');
        var optionCopy = document.createElement('span');
        optionCopy.className = 'followup-mode-option-copy';
        var optionName = document.createElement('span');
        optionName.className = 'followup-mode-option-name';
        optionName.textContent = choice.label;
        var optionDescription = document.createElement('span');
        optionDescription.className = 'followup-mode-option-description';
        optionDescription.textContent = choice.description;
        optionCopy.appendChild(optionName);
        optionCopy.appendChild(optionDescription);
        var selectedBadge = document.createElement('span');
        selectedBadge.className = 'followup-mode-option-selected';
        selectedBadge.textContent = '当前';
        option.appendChild(optionCopy);
        option.appendChild(selectedBadge);
        option.addEventListener('click', function (event) {
            event.preventDefault(); event.stopPropagation();
            chooseMode(choice.value);
        });
        option.addEventListener('keydown', function (event) {
            var index = optionRows.indexOf(option);
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault(); event.stopPropagation();
                var delta = event.key === 'ArrowDown' ? 1 : -1;
                optionRows[(index + delta + optionRows.length) % optionRows.length].focus();
            } else if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault(); event.stopPropagation();
                chooseMode(choice.value);
            } else if (event.key === 'Escape') {
                event.preventDefault(); event.stopPropagation();
                closeMenu(); trigger.focus();
            }
        });
        optionRows.push(option);
        menu.appendChild(option);
    });

    trigger.addEventListener('click', function (event) {
        event.preventDefault(); event.stopPropagation();
        if (menu.hidden) openMenu(false); else closeMenu();
    });
    trigger.addEventListener('keydown', function (event) {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Enter' || event.key === ' ') {
            event.preventDefault(); event.stopPropagation();
            openMenu(true);
        } else if (event.key === 'Escape' && !menu.hidden) {
            event.preventDefault(); event.stopPropagation(); closeMenu();
        }
    });
    picker.appendChild(visualSelect);
    picker.appendChild(direction);
    picker.appendChild(trigger);
    picker.appendChild(menu);
    sync();
    return picker;
}

function renderFollowupQueue(sessionId) {
    var sid = String(sessionId != null ? sessionId : (currentSessionId || ''));
    var panel = ensureFollowupQueueHost();
    if (!panel) return;
    if (!sid || sid !== currentSessionId) {
        if (!currentSessionId) {
            panel.innerHTML = '';
            panel.classList.remove('is-visible');
            panel.removeAttribute('data-session-id');
            panel.removeAttribute('data-render-signature');
        }
        return;
    }
    var q = getFollowupQueue(sid);
    syncMessageInputPlaceholder();
    var renderSignature = followupQueueRenderSignature(sid, q);
    if (
        panel.dataset.sessionId === sid
        && panel.dataset.renderSignature === renderSignature
        && panel.querySelectorAll('.followup-queue-row').length === q.length
    ) {
        positionFollowupQueuePanel();
        return;
    }
    closeActiveFollowupModePicker();
    panel.innerHTML = '';
    panel.dataset.sessionId = sid;
    panel.dataset.renderSignature = renderSignature;
    panel.classList.toggle('is-visible', !!q.length);
    if (!q.length) {
        positionFollowupQueuePanel();
        return;
    }
    q.forEach(function (item, idx) {
        if (item && ['submitting', 'sending', 'accepted', 'restarting'].includes(String(item.status || ''))) {
            scheduleAcceptedFollowupWatch(sid, item.id);
        }
        var row = document.createElement('div');
        row.className = 'followup-queue-row';
        row.classList.toggle('is-sending', item.status === 'sending' || item.status === 'submitting');
        row.classList.toggle('is-accepted', item.status === 'accepted');
        row.classList.toggle('is-sent', item.status === 'sent');
        row.dataset.id = String(item.id);
        row.dataset.reorderable = item.status ? 'false' : 'true';
        var dragHandle = document.createElement('div');
        dragHandle.className = 'followup-queue-drag';
        dragHandle.textContent = '⠿';
        dragHandle.setAttribute('title', '拖拽调整顺序');
        dragHandle.draggable = !item.status;
        dragHandle.classList.toggle('is-disabled', !!item.status);
        dragHandle.addEventListener('dragstart', function (ev) {
            startFollowupDrag(sid, item, row, ev);
        });
        dragHandle.addEventListener('dragend', endFollowupDrag);
        dragHandle.addEventListener('pointerdown', function (ev) {
            if (ev.pointerType === 'touch' || ev.pointerType === 'pen') {
                startFollowupTouchDrag(sid, item, row, ev);
            }
        });
        dragHandle.addEventListener('pointermove', onFollowupTouchDragMove);
        dragHandle.addEventListener('pointerup', onFollowupTouchDragEnd);
        dragHandle.addEventListener('pointercancel', endFollowupDrag);
        var order = document.createElement('div');
        order.className = 'followup-queue-order';
        order.textContent = String(idx + 1);
        var text = document.createElement('div');
        text.className = 'followup-queue-text';
        var itemSkills = Array.isArray(item.skills) ? item.skills : [];
        var itemDisplay = String(item.display || item.text || '');
        var itemDetail = itemDisplay + (itemSkills.length ? ('\n\nSkill: ' + itemSkills.join('、')) : '');
        text.textContent = itemDisplay + (itemSkills.length ? ('  · Skill: ' + itemSkills.join('、')) : '');
        text.setAttribute('data-ui-tip', itemDetail);
        var status = document.createElement('div');
        status.className = 'followup-queue-status';
        status.textContent = getFollowupStatusText(item);
        var sendNow = document.createElement('button');
        sendNow.type = 'button';
        sendNow.className = 'followup-queue-action followup-queue-send';
        sendNow.textContent = '立即发送';
        sendNow.disabled = !!item.status || !!(
            (typeof pendingHumanQuestions === 'function' && pendingHumanQuestions(sid).length)
            || (item.deferUntilRunEnd && (isSessionRunning(sid) || isServerStreamActive(sid)))
        );
        var modePicker = createFollowupModePicker(item, sid);
        var undo = document.createElement('button');
        undo.type = 'button';
        undo.className = 'followup-queue-action followup-queue-undo';
        undo.textContent = '撤回';
        undo.disabled = item.status === 'sent' || item.status === 'withdrawing';
        sendNow.addEventListener('click', function (ev) {
            ev.preventDefault();
            void sendFollowupNow(String(item.id), sid, { manual: true });
        });
        undo.addEventListener('click', function (ev) {
            ev.preventDefault();
            withdrawFollowup(String(item.id));
        });
        row.appendChild(dragHandle);
        row.appendChild(order);
        row.appendChild(text);
        row.appendChild(status);
        row.appendChild(modePicker);
        row.appendChild(sendNow);
        row.appendChild(undo);
        panel.appendChild(row);
        if (typeof initUiHoverTips === 'function') initUiHoverTips(row);
    });
    positionFollowupQueuePanel();
    if (typeof scrollChatToBottomIfFollow === 'function') {
        scrollChatToBottomIfFollow(sid, {});
    }
}

function getFollowupStatusText(item) {
    var status = item && item.status ? String(item.status) : '';
    if (status === 'withdrawing') return '撤回中';
    if (status === 'submitting') return '提交中';
    if (status === 'accepted') return item && item.steerMode === 'append' ? '已追加，等待下一轮' : '已接收，等待插入';
    if (status === 'restarting') return '正在接管当前任务';
    if (status === 'sending') return '发送中';
    if (status === 'sent') return '已发送';
    return '待发送';
}

function appendFollowupQueueItem(sessionId, text, display, selectedSkills, attachments) {
    const sid = String(sessionId || '');
    if (!sid || !hasSendableText(text)) return null;
    const item = {
        id: followupQueueSeq++,
        text: String(text),
        display: String(display || text),
        skills: Array.isArray(selectedSkills) ? selectedSkills.slice() : [],
        attachments: Array.isArray(attachments) ? attachments.slice() : [],
        createdAt: Date.now(),
        steerMode: defaultSteerMode(),
        awaitingRunEnd: isSessionRunning(sid) || isServerStreamActive(sid),
    };
    getFollowupQueue(sid).push(item);
    persistFollowupQueue(sid);
    renderFollowupQueue(sid);
    setSendButtonState();
    return item;
}

function buildSelectedSkillsDisplayMessage(rawMessage, selectedSkills) {
    var message = String(rawMessage || '');
    var names = Array.isArray(selectedSkills)
        ? selectedSkills.map(function (skill) { return String(skill || '').trim(); }).filter(Boolean)
        : [];
    if (!names.length) return message;
    var suffix = '\n\nActivated Skill: ' + names.join(', ');
    return message.endsWith(suffix) ? message : message + suffix;
}

function enqueueCurrentInputAsFollowup(options) {
    options = options || {};
    if (!options.pendingQuestion && !isMyAgentFeatureEnabled('followupRestart', false)) return false;
    if (isChatFileUploadBusy()) return false;
    const sid = currentSessionId;
    if (!sid) return false;
    rewriteInputWorkspacePaths();
    const visibleMessage = messageInput.value;
    const rawMessage = expandInputPathTokens(visibleMessage);
    if (!hasSendableText(rawMessage)) return false;
    var selectedSkills = [];
    if (typeof window.consumeSelectedSkillsForSend === 'function') {
        selectedSkills = window.consumeSelectedSkillsForSend();
    }
    var attachments = window.MyAgentPathPicker
        && typeof window.MyAgentPathPicker.chatAttachments === 'function'
        ? window.MyAgentPathPicker.chatAttachments(messageInput).filter(function (attachment) {
            return attachment && attachment.path && rawMessage.indexOf(String(attachment.path)) >= 0;
        })
        : [];
    var item = appendFollowupQueueItem(sid, rawMessage, visibleMessage, selectedSkills, attachments);
    if (!item) return false;
    recentComposerQueuedFollowup = { sessionId: sid, itemId: String(item.id) };
    if (options.pendingQuestion || attachments.length) {
        item.awaitingRunEnd = true;
        item.deferUntilRunEnd = true;
        persistFollowupQueue(sid);
        renderFollowupQueue(sid);
    }
    if (attachments.length && window.MyAgentPathPicker
            && typeof window.MyAgentPathPicker.clearChatAttachments === 'function') {
        window.MyAgentPathPicker.clearChatAttachments(messageInput);
    }
    messageInput.value = '';
    persistInputDraft(sid, '');
    clearInputPathTokens();
    autoResizeTextarea();
    // appendFollowupQueueItem() refreshed the button while the composer still
    // contained the follow-up. Refresh again after clearing it so the active
    // run exposes "Stop", rather than leaving a stale "Follow up" label.
    setSendButtonState();
    return true;
}

function rollbackOptimisticUserEvent(sessionId, eventIndex) {
    const sid = String(sessionId || '');
    const before = Math.max(0, Number(eventIndex) || 0);
    if (!sid) return;
    if (typeof truncateMessageStateForSession === 'function') {
        truncateMessageStateForSession(sid, before);
    }
    if (typeof uiEventCountCache !== 'undefined') {
        uiEventCountCache.updateFromServer(sid, before);
    }
    if (typeof truncateTocTurnsForSession === 'function') {
        truncateTocTurnsForSession(sid, before);
    }
    if (sid !== currentSessionId) return;
    const anchor = document.querySelector('.msg-wrap--user[data-event-index="' + String(before) + '"]');
    if (anchor) removeMessagesFromNode(anchor);
    rebuildToc({ localOnly: true });
}

function takeFollowupItem(sessionId, itemId) {
    var q = getFollowupQueue(sessionId);
    var idx = q.findIndex(function (item) { return String(item.id) === String(itemId); });
    if (idx < 0) return null;
    var item = q.splice(idx, 1)[0] || null;
    persistFollowupQueue(sessionId);
    return item;
}

function moveFollowupQueueItem(sessionId, itemId, targetId, placement) {
    var sid = String(sessionId || '');
    var q = getFollowupQueue(sid);
    var from = q.findIndex(function (item) { return item && String(item.id) === String(itemId); });
    var to = q.findIndex(function (item) { return item && String(item.id) === String(targetId); });
    if (from < 0 || to < 0 || from === to) return false;
    if (q[from].status || q[to].status) return false;

    // Reorder only the pending slots.  In-flight rows remain at their exact
    // array indexes while pending rows move around them.
    var pendingIndexes = [];
    var pendingItems = [];
    q.forEach(function (entry, idx) {
        if (entry && !entry.status) {
            pendingIndexes.push(idx);
            pendingItems.push(entry);
        }
    });
    var pendingFrom = pendingItems.findIndex(function (entry) { return String(entry.id) === String(itemId); });
    var pendingTo = pendingItems.findIndex(function (entry) { return String(entry.id) === String(targetId); });
    if (pendingFrom < 0 || pendingTo < 0 || pendingFrom === pendingTo) return false;
    var item = pendingItems.splice(pendingFrom, 1)[0];
    var insertAt = pendingTo;
    if (pendingFrom < pendingTo) {
        insertAt = pendingTo - 1;
        if (placement === 'after') insertAt = pendingTo;
    } else if (placement === 'after') {
        insertAt = pendingTo + 1;
    }
    pendingItems.splice(insertAt, 0, item);
    pendingIndexes.forEach(function (queueIndex, idx) {
        q[queueIndex] = pendingItems[idx];
    });
    q.forEach(function (entry, idx) {
        if (entry) entry.order = idx;
    });
    persistFollowupQueue(sid);
    renderFollowupQueue(sid);
    return true;
}

function withdrawFollowup(itemId) {
    const sid = currentSessionId;
    var q = getFollowupQueue(sid);
    var pendingItem = q.find(function (entry) { return String(entry.id) === String(itemId); });
    if (pendingItem && (pendingItem.status === 'sending' || pendingItem.status === 'submitting' || pendingItem.status === 'accepted' || pendingItem.status === 'restarting')) {
        pendingItem.cancelRequested = true;
        pendingItem.status = 'withdrawing';
        persistFollowupQueue(sid);
        renderFollowupQueue(sid);
        if (pendingItem.steerInFlight && !pendingItem.steerId) return;
        cancelSteerMessage(sid, pendingItem).then(function () {
            var item = takeFollowupItem(sid, itemId);
            if (item) returnFollowupToInput(sid, item);
        }).catch(function (e) {
            var item = q.find(function (entry) { return String(entry.id) === String(itemId); });
            if (item) item.status = 'sending';
            persistFollowupQueue(sid);
            renderFollowupQueue(sid);
            appendLogVisible('追问已被接收，无法撤回: ' + ((e && e.message) || String(e)), 'error-log');
        });
        return;
    }
    const item = takeFollowupItem(sid, itemId);
    if (!item) return;
    returnFollowupToInput(sid, item);
}

function returnFollowupToInput(sid, item) {
    removePendingSteerFromProcess(sid, item);
    const returned = String(item.display || item.text || '');
    if (sid !== currentSessionId) {
        const backgroundDraft = Object.prototype.hasOwnProperty.call(draftBySession, sid)
            ? String(draftBySession[sid] || '')
            : String(readStoredInputDraft(sid) || '');
        const nextDraft = backgroundDraft.trim() ? (returned + '\n' + backgroundDraft) : returned;
        persistInputDraft(sid, nextDraft);
        if (typeof window.setSelectedSkillsForSession === 'function') {
            window.setSelectedSkillsForSession(sid, item.skills || []);
        }
        renderFollowupQueue(sid);
        return;
    }
    const existing = String(messageInput.value || '');
    messageInput.value = existing.trim() ? (returned + '\n' + existing) : returned;
    if (typeof window.setSelectedSkillsForCurrentSession === 'function') {
        window.setSelectedSkillsForCurrentSession(item.skills || []);
    }
    if (window.MyAgentPathPicker
            && typeof window.MyAgentPathPicker.addChatAttachments === 'function') {
        window.MyAgentPathPicker.addChatAttachments(messageInput, item.attachments || []);
    }
    rewriteInputWorkspacePaths();
    persistInputDraft(sid, messageInput.value);
    autoResizeTextarea();
    renderFollowupQueue(sid);
    setSendButtonState();
    messageInput.focus();
}

function findSteerProcessRow(ctx, operationId) {
    var key = String(operationId || '');
    if (!ctx || !key || typeof getProcessBody !== 'function') return null;
    var body = getProcessBody(ctx);
    if (!body || !body.querySelectorAll) return null;
    var rows = body.querySelectorAll('.feed-item[data-steer-operation-id]');
    for (var i = 0; i < rows.length; i += 1) {
        if (String(rows[i].dataset.steerOperationId || '') === key
            || String(rows[i].dataset.steerClientId || '') === key
            || String(rows[i].dataset.steerId || '') === key) return rows[i];
    }
    return null;
}

function commitPendingSteerProcessRow(sessionId, item, serverItem) {
    var sid = String(sessionId || '');
    if (!sid || !item) return null;
    var run = getSessionRunState(sid);
    var ctx = run && run.ctx;
    var row = item.pendingProcessRow && item.pendingProcessRow.isConnected
        ? item.pendingProcessRow
        : findSteerProcessRow(ctx, item.clientId || item.steerId || '');
    var content = String((serverItem && (serverItem.ui_content || serverItem.content)) || item.display || item.text || '');
    if (!row && ctx) {
        row = appendSteerProcessMessage(
            sid, ctx, content, item.clientId || item.steerId || '',
            item.steerMode || (serverItem && serverItem.mode) || 'interrupt', false
        );
    }
    if (!row) return null;
    var scroller = row.querySelector('.feed-chunk-scroller');
    if (scroller && content.trim()) scroller.textContent = truncateLogTextForUi(content);
    row.dataset.steerCommitted = '1';
    row.removeAttribute('data-steer-pending');
    if (item.clientId) row.dataset.steerClientId = String(item.clientId);
    if (item.steerId) row.dataset.steerId = String(item.steerId);
    item.pendingProcessRow = row;
    return row;
}

function appendSteerProcessMessage(sessionId, ctx, content, operationId, steerMode, pending) {
    var sid = String(sessionId || '');
    var key = String(operationId || '');
    if (!sid || !ctx || !key) return null;
    var existing = findSteerProcessRow(ctx, key);
    if (existing) {
        if (!pending) {
            var existingScroller = existing.querySelector('.feed-chunk-scroller');
            if (existingScroller && String(content || '').trim()) {
                existingScroller.textContent = truncateLogTextForUi(String(content || ''));
            }
            existing.dataset.steerCommitted = '1';
            existing.removeAttribute('data-steer-pending');
        }
        return existing;
    }
    var scroller = appendLog(ctx, String(content || ''), 'user-steer', sid);
    var row = scroller && scroller.closest ? scroller.closest('.feed-item') : null;
    if (!row) return null;
    row.dataset.steerOperationId = key;
    row.dataset.steerMode = steerMode === 'append' ? 'append' : 'interrupt';
    if (pending) row.dataset.steerPending = '1';
    else row.dataset.steerCommitted = '1';
    return row;
}

function appendPendingSteerToProcess(sessionId, item) {
    var sid = String(sessionId || '');
    if (!sid || !item || item.steerMode !== 'append') return null;
    var run = getSessionRunState(sid);
    var ctx = run && run.ctx;
    if (!ctx) return null;
    var row = appendSteerProcessMessage(
        sid,
        ctx,
        buildSelectedSkillsDisplayMessage(item.display || item.text || '', item.skills || []),
        item.clientId || item.steerId || '',
        'append',
        true
    );
    if (row) {
        if (item.clientId) row.dataset.steerClientId = String(item.clientId);
        if (item.steerId) row.dataset.steerId = String(item.steerId);
        item.pendingProcessRow = row;
    }
    return row;
}

function prepareSteerProcessBoundary(ctx, steerMode, operationId) {
    if (!ctx || String(steerMode || 'interrupt') !== 'interrupt') return;
    var key = String(operationId || '');
    if (key && String(ctx.lastInterruptSteerOperationId || '') === key) return;
    // An interrupt starts a new logical ReAct generation, but remains in the
    // same execution-process aggregate.  Generation-aware row keys preserve
    // ordering when react_iter restarts at 1.
    finalizeLlmStreamChunks(ctx);
    finalizeProgressStreamChunks(ctx);
    resetLlmState(ctx);
    ctx.reactGeneration = Math.max(0, Number(ctx.reactGeneration) || 0) + 1;
    if (key) ctx.lastInterruptSteerOperationId = key;
}

function markSteerEventPosition(ctx, eventIndex, runtimeSeq) {
    if (!ctx) return;
    if (Number.isFinite(Number(eventIndex))) {
        ctx.lastUserEventIndex = Math.max(
            Number.isFinite(Number(ctx.lastUserEventIndex)) ? Number(ctx.lastUserEventIndex) : -1,
            Math.floor(Number(eventIndex))
        );
    }
    if (Number.isFinite(Number(runtimeSeq)) && Number(runtimeSeq) > 0) {
        ctx.lastUserRuntimeSeq = Math.floor(Number(runtimeSeq));
    }
}

function removePendingSteerFromProcess(sessionId, item) {
    var sid = String(sessionId || '');
    if (!sid || !item || item.steerMode !== 'append') return;
    var run = getSessionRunState(sid);
    var row = item.pendingProcessRow && item.pendingProcessRow.isConnected
        ? item.pendingProcessRow
        : findSteerProcessRow(run && run.ctx, item.clientId || item.steerId || '');
    if (row && row.dataset.steerPending === '1' && row.dataset.steerCommitted !== '1') row.remove();
}

async function sendSteerMessage(sessionId, text, clientId, selectedSkills, uiContent, steerMode) {
    var activeRun = getSessionRunState(sessionId);
    var sourceRunId = activeRun && activeRun.runId ? String(activeRun.runId) : '';
    var r = await fetch('/sessions/' + encodeURIComponent(sessionId) + '/steer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            message: text,
            client_id: clientId || '',
            selected_skills: selectedSkills || [],
            ui_content: uiContent || text,
            source_run_id: sourceRunId,
            mode: steerMode === 'append' ? 'append' : 'interrupt',
        }),
    });
    var j = await r.json().catch(function () {
        return { ok: false, error: 'steer failed' };
    });
    if (!r.ok || !j.ok) throw new Error((j && j.error) || 'steer failed');
    return j;
}

function sleepMs(ms) {
    return new Promise(function (resolve) {
        setTimeout(resolve, Math.max(0, Number(ms) || 0));
    });
}

async function refreshFollowupRunState(sessionId) {
    const sid = String(sessionId || '');
    if (!sid) return;
    try {
        if (typeof reconcileRunStateFromServer === 'function') {
            await reconcileRunStateFromServer({ silent: true });
        }
    } catch (e) { /* ignore */ }
    try {
        scheduleActiveSessionReconnect(sid, { delayMs: 0 });
    } catch (e2) { /* ignore */ }
}

async function cancelSteerMessage(sessionId, item) {
    var r = await fetch('/sessions/' + encodeURIComponent(sessionId) + '/steer', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            steer_id: (item && item.steerId) || '',
            client_id: (item && item.clientId) || '',
        }),
    });
    var j = await r.json().catch(function () {
        return { ok: false, error: 'cancel steer failed' };
    });
    if (!r.ok || !j.ok) throw new Error((j && j.error) || 'cancel steer failed');
    return j;
}

async function fetchSteerStatus(sessionId, item) {
    var steerId = String(item && item.steerId || '');
    if (!sessionId || !steerId) return null;
    var r = await fetch('/sessions/' + encodeURIComponent(sessionId) + '/steer/' + encodeURIComponent(steerId));
    var j = await r.json().catch(function () { return null; });
    if (!r.ok || !j || !j.ok) return null;
    return j.item || null;
}

async function recoverSteerForRestart(sessionId, item) {
    var steerId = String(item && item.steerId || '');
    if (!sessionId || !steerId) return null;
    var r = await fetch('/sessions/' + encodeURIComponent(sessionId) + '/steer/' + encodeURIComponent(steerId) + '/recover', {
        method: 'POST',
    });
    var j = await r.json().catch(function () { return null; });
    return r.ok && j && j.ok ? (j.item || null) : null;
}

async function syncFollowupQueueFromServer(sessionId) {
    var sid = String(sessionId || '');
    if (!sid || followupServerSyncInFlight[sid]) return followupServerSyncInFlight[sid] || null;
    followupServerSyncInFlight[sid] = fetch('/sessions/' + encodeURIComponent(sid) + '/steer?include_terminal=true')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (payload) {
            if (!payload || !payload.ok || !Array.isArray(payload.items)) return;
            var q = getFollowupQueue(sid);
            var pendingIds = new Set();
            payload.items.forEach(function (serverItem) {
                var steerId = String(serverItem.id || '');
                var clientId = String(serverItem.client_id || '');
                var state = String(serverItem.state || 'queued');
                var isTerminal = state === 'consumed' || state === 'cancelled' || state === 'failed';
                if (steerId && !isTerminal) pendingIds.add(steerId);
                var local = q.find(function (entry) {
                    return (steerId && String(entry.steerId || '') === steerId)
                        || (clientId && String(entry.clientId || '') === clientId);
                });
                if (!local && !isTerminal) {
                    local = {
                        id: 'server-' + (steerId || clientId || Date.now()),
                        text: String(serverItem.content || ''),
                        display: String(serverItem.ui_content || serverItem.content || ''),
                        clientId: clientId,
                        steerId: steerId,
                        createdAt: Math.round(Number(serverItem.created_at || 0) * 1000) || Date.now(),
                        steerMode: String(serverItem.mode || '') === 'append' ? 'append' : 'interrupt',
                    };
                    q.push(local);
                }
                if (!local) return;
                if (state === 'failed' || state === 'cancelled') {
                    var failedIndex = q.indexOf(local);
                    if (failedIndex >= 0) q.splice(failedIndex, 1);
                    returnFollowupToInput(sid, local);
                    return;
                }
                if (state === 'consumed') {
                    commitPendingSteerProcessRow(sid, local, serverItem);
                    var terminalIndex = q.indexOf(local);
                    if (terminalIndex >= 0) q.splice(terminalIndex, 1);
                    return;
                }
                local.steerId = steerId || local.steerId;
                local.clientId = clientId || local.clientId;
                local.replacementRunId = String(serverItem.replacement_run_id || local.replacementRunId || '');
                local.steerMode = String(serverItem.mode || local.steerMode || '') === 'append' ? 'append' : 'interrupt';
                local.status = state === 'restarting' ? 'restarting' : 'accepted';
                if (local.steerMode === 'append' && (state === 'queued' || state === 'claimed')) {
                    // Rebuild the transient tail anchor after refresh/reattach.
                    // The durable user_steer event will commit this same row.
                    appendPendingSteerToProcess(sid, local);
                }
            });
            for (var i = q.length - 1; i >= 0; i -= 1) {
                var entry = q[i];
                if (entry.steerId && (entry.status === 'accepted' || entry.status === 'restarting') && !pendingIds.has(String(entry.steerId))) {
                    q.splice(i, 1);
                }
            }
            q.sort(function (a, b) {
                var aOrder = Number(a.order);
                var bOrder = Number(b.order);
                var aHas = Number.isFinite(aOrder);
                var bHas = Number.isFinite(bOrder);
                if (aHas && bHas) return aOrder - bOrder;
                if (aHas) return -1;
                if (bHas) return 1;
                return Number(a.createdAt || 0) - Number(b.createdAt || 0);
            });
            persistFollowupQueue(sid);
            renderFollowupQueue(sid);
        })
        .finally(function () { delete followupServerSyncInFlight[sid]; });
    return followupServerSyncInFlight[sid];
}

function removeConsumedFollowupSteer(sessionId, ev) {
    const sid = String(sessionId || '');
    if (!sid || !ev || !ev.steer) return false;
    var steerId = String(ev.steer_id || '');
    var clientId = String(ev.client_id || '');
    if (!steerId && !clientId) return false;
    var q = getFollowupQueue(sid);
    var item = q.find(function (entry) {
        return (clientId && String(entry.clientId || '') === clientId)
            || (steerId && String(entry.steerId || '') === steerId);
    });
    if (!item) return false;
    takeFollowupItem(sid, item.id);
    renderFollowupQueue(sid);
    // 只发起一次门禁检查：活跃 run 会直接拦截；若终止事件先到、consumed 后到，
    // 则允许已经结束的这一轮继续 FIFO 队首。
    scheduleFollowupQueueDrain(sid, 0);
    return true;
}

function isFollowupAutoDrainReady(sessionId) {
    var sid = String(sessionId || '');
    return !!sid
        && !(typeof pendingHumanQuestions === 'function' && pendingHumanQuestions(sid).length)
        && !(typeof isSessionStreamStopSuppressed === 'function' && isSessionStreamStopSuppressed(sid))
        && !isSessionRunning(sid)
        && !isServerStreamActive(sid)
        && !isSendPipelineLocked(sid)
        && !isFollowupDispatchBusy(sid);
}

function cancelFollowupQueueDrain(sessionId) {
    var sid = String(sessionId || '');
    var existing = sid && followupDrainTimers[sid];
    if (!existing) return;
    clearTimeout(existing.timer);
    delete followupDrainTimers[sid];
}

function scheduleFollowupQueueDrain(sessionId, delayMs) {
    var sid = String(sessionId || '');
    if (!sid) return;
    var delay = Math.max(0, Number(delayMs) || 0);
    var dueAt = Date.now() + delay;
    var existing = followupDrainTimers[sid];
    if (existing && existing.dueAt <= dueAt) return;
    if (existing) clearTimeout(existing.timer);
    var timer = setTimeout(function () {
        var current = followupDrainTimers[sid];
        if (!current || current.timer !== timer) return;
        delete followupDrainTimers[sid];
        drainFollowupQueue(sid);
    }, delay);
    followupDrainTimers[sid] = { timer: timer, dueAt: dueAt };
}

function drainFollowupQueue(sessionId) {
    var sid = String(sessionId || '');
    if (!sid) return;
    if (!isFollowupAutoDrainReady(sid)) {
        // 活跃 run 会在自己的终止边界重新启动 drain；这里只重试瞬时的锁/dispatcher 竞争。
        if ((typeof pendingHumanQuestions === 'function' && pendingHumanQuestions(sid).length)
            || isSessionRunning(sid)
            || isServerStreamActive(sid)
            || (typeof isSessionStreamStopSuppressed === 'function' && isSessionStreamStopSuppressed(sid))) return;
        scheduleFollowupQueueDrain(sid, 120);
        return;
    }
    var q = getFollowupQueue(sid);
    if (!q.length) { renderFollowupQueue(sid); return; }
    var item = q[0];
    if (!item || item.status) { renderFollowupQueue(sid); return; }
    // 一个终止边界最多尝试一条。失败后 sendFollowupNow 会恢复 pending；
    // 不在这里循环重试，避免网络错误造成请求风暴或重复执行。
    void Promise.resolve(sendFollowupNow(item.id, sid, { autoAfterRun: true }))
        .catch(function (error) {
            console.error('follow-up auto-drain failed:', error);
        });
}

function markFollowupQueueManualOnly(sessionId) {
    var sid = String(sessionId || '');
    if (!sid) return;
    cancelFollowupQueueDrain(sid);
    var q = getFollowupQueue(sid);
    q.forEach(function (item) {
        if (item) item.awaitingRunEnd = false;
    });
    persistFollowupQueue(sid);
    renderFollowupQueue(sid);
}

function recoverFollowupQueueDrainsFromSessionSnapshot(previousActiveIds, currentActiveIds) {
    var previous = previousActiveIds instanceof Set ? previousActiveIds : new Set(previousActiveIds || []);
    var current = currentActiveIds instanceof Set ? currentActiveIds : new Set(currentActiveIds || []);
    var candidates = new Set();
    previous.forEach(function (sid) {
        sid = String(sid || '');
        if (sid && !current.has(sid)) candidates.add(sid);
    });
    if (!followupSnapshotRecoveryInitialized) {
        followupSnapshotRecoveryInitialized = true;
        try {
            for (var i = 0; i < localStorage.length; i += 1) {
                var key = String(localStorage.key(i) || '');
                if (key.indexOf(LS_FOLLOWUP_QUEUE_PREFIX) !== 0) continue;
                var sid = key.slice(LS_FOLLOWUP_QUEUE_PREFIX.length);
                if (sid && !current.has(sid)) candidates.add(sid);
            }
        } catch (e) { /* localStorage may be unavailable */ }
    }
    candidates.forEach(function (sid) {
        if (current.has(sid)
            || (typeof isSessionStreamStopSuppressed === 'function' && isSessionStreamStopSuppressed(sid))) return;
        var q = getFollowupQueue(sid);
        var waiting = q.some(function (item) {
            return item && !item.status && item.awaitingRunEnd !== false;
        });
        if (!waiting) return;
        void Promise.resolve(syncFollowupQueueFromServer(sid)).then(function () {
            if (!isSessionRunning(sid) && !isServerStreamActive(sid)) {
                scheduleFollowupQueueDrain(sid, 0);
            }
        }).catch(function (error) {
            console.warn('background follow-up reconciliation failed', error);
        });
    });
}

function scheduleAcceptedFollowupWatch(sid, itemId) {
    var watchKey = String(sid || '') + ':' + String(itemId || '');
    if (followupWatchTimers[watchKey]) return;
    followupWatchTimers[watchKey] = setTimeout(function () {
        delete followupWatchTimers[watchKey];
        var queued = getFollowupQueue(sid).find(function (entry) {
            return String(entry.id) === String(itemId);
        });
        if (!queued || !['submitting', 'sending', 'accepted', 'restarting'].includes(String(queued.status || ''))) return;
        // Recovery can start a replacement /chat, so it participates in the
        // same per-session dispatcher as manual and automatic sends.
        void withFollowupDispatch(sid, async function () {
            await refreshFollowupRunState(sid);
            var latest = getFollowupQueue(sid).find(function (entry) {
                return String(entry.id) === String(itemId);
            });
            if (!latest) return;
            var serverItem = latest.steerId ? await fetchSteerStatus(sid, latest) : null;
            // A request may have reached the server immediately before refresh,
            // leaving only client_id locally. Reconcile first, then resolve the
            // authoritative steer state without creating another operation.
            if (!serverItem && latest.clientId) {
                await syncFollowupQueueFromServer(sid);
                latest = getFollowupQueue(sid).find(function (entry) {
                    return String(entry.id) === String(itemId);
                });
                if (!latest) return;
                if (latest.steerId) serverItem = await fetchSteerStatus(sid, latest);
            }
            var serverState = String(serverItem && serverItem.state || '');
            if (latest.steerMode === 'append'
                && (serverState === 'queued' || serverState === 'claimed')) {
                appendPendingSteerToProcess(sid, latest);
            }
            if (serverState === 'consumed') {
                commitPendingSteerProcessRow(sid, latest, serverItem);
                takeFollowupItem(sid, itemId);
                renderFollowupQueue(sid);
                refreshPendingFollowupQueue(sid);
                scheduleFollowupQueueDrain(sid, 0);
                return;
            }
            if (serverState === 'cancelled' || serverState === 'failed') {
                var failed = takeFollowupItem(sid, itemId);
                if (failed) returnFollowupToInput(sid, failed);
                return;
            }
            if (!serverItem && (latest.status === 'submitting' || latest.status === 'sending')) {
                if (latest.steerInFlight || isSessionRunning(sid) || isServerStreamActive(sid) || isSendPipelineLocked(sid)) {
                    scheduleAcceptedFollowupWatch(sid, itemId);
                    return;
                }
                // No local activity and no authoritative server operation: the
                // previous attempt was orphaned. Restore a durable pending row.
                latest.status = '';
                persistFollowupQueue(sid);
                renderFollowupQueue(sid);
                refreshPendingFollowupQueue(sid);
                return;
            }
            if ((serverState === 'queued' || serverState === 'interrupting' || serverState === 'claimed') && !isSessionRunning(sid) && !isServerStreamActive(sid)) {
                var recovered = await recoverSteerForRestart(sid, latest);
                if (recovered) {
                    latest.status = 'restarting';
                    latest.replacementRunId = String(recovered.replacement_run_id || '');
                    persistFollowupQueue(sid);
                    renderFollowupQueue(sid);
                }
                scheduleAcceptedFollowupWatch(sid, itemId);
                return;
            }
            if (serverState === 'restarting' && !isSessionRunning(sid) && !isServerStreamActive(sid) && !latest.restartRecoveryAttempted) {
                latest.restartRecoveryAttempted = true;
                latest.replacementRunId = String(serverItem && serverItem.replacement_run_id || latest.replacementRunId || '');
                persistFollowupQueue(sid);
                var restarted = await startFollowupChat({
                    message: latest.text,
                    displayMessage: latest.display || latest.text,
                    selectedSkills: latest.skills || [],
                    attachments: latest.attachments || [],
                    fromQueue: true,
                    sessionId: sid,
                    forceStart: true,
                    preserveInput: true,
                    asSteer: true,
                    steerId: latest.steerId,
                    steerClientId: latest.clientId,
                    steerMode: latest.steerMode,
                    clientRunId: latest.replacementRunId,
                });
                if (restarted) {
                    takeFollowupItem(sid, itemId);
                    renderFollowupQueue(sid);
                } else {
                    latest.restartRecoveryAttempted = false;
                    persistFollowupQueue(sid);
                    scheduleAcceptedFollowupWatch(sid, itemId);
                }
                return;
            }
            if (isSessionRunning(sid) || isServerStreamActive(sid)) {
                scheduleActiveSessionReconnect(sid, { delayMs: 0 });
                scheduleActiveSessionReconnect(sid, { delayMs: 1200 });
            }
            scheduleAcceptedFollowupWatch(sid, itemId);
        }).catch(function () {
            scheduleAcceptedFollowupWatch(sid, itemId);
        });
    }, 1200);
}

// Resolve as soon as /chat has been accepted and its SSE stream is ready. The
// long-running sendMessage promise continues consuming the stream in the
// background, while the dispatcher is released for genuine in-run steers.
function startFollowupChat(options) {
    return new Promise(function (resolve) {
        var settled = false;
        var finish = function (started) {
            if (settled) return;
            settled = true;
            resolve(!!started);
        };
        var opts = Object.assign({}, options || {});
        var priorStarted = opts.onRunStarted;
        opts.onRunStarted = function (info) {
            if (typeof priorStarted === 'function') {
                try { priorStarted(info); } catch (e) { /* callback is observational */ }
            }
            finish(true);
        };
        var completion;
        try {
            completion = Promise.resolve(sendMessage(opts));
        } catch (e) {
            finish(false);
            return;
        }
        completion.then(function (result) {
            finish(result === true);
        }, function () {
            finish(false);
        });
    });
}

function isFollowupAutoDispatchSuperseded(sessionId, dispatchEpoch) {
    var sid = String(sessionId || '');
    if (!sid || dispatchEpoch == null) return false;
    return Number(followupManualDispatchEpochBySession[sid] || 0) !== Number(dispatchEpoch);
}

async function isSessionAutoResumePending(sessionId) {
    var sid = String(sessionId || '');
    // Auto-resume only applies to the currently open session. Background
    // sessions keep their previous pending auto-drain behavior.
    if (!sid || sid !== String(currentSessionId || '')) return false;
    if (typeof subagentContinueSessionId !== 'undefined' && subagentContinueSessionId === sid) return true;
    // During an initial session load the normal switch/refresh path will wake
    // auto-resume; do not start /continue while history is still hydrating.
    if (typeof suppressTocDuringSessionLoad !== 'undefined' && suppressTocDuringSessionLoad) return true;
    try {
        var response = await fetch('/sessions/' + encodeURIComponent(sid), { cache: 'no-store' });
        if (!response.ok) return false;
        var detail = await response.json();
        if (!detail
            || !detail.react_auto_resume
            || detail.run_active
            || detail.stream_active) return false;
        if (typeof maybeAutoResumeInterruptedReact === 'function') {
            maybeAutoResumeInterruptedReact(sid, detail);
        }
        return true;
    } catch (e) {
        return false;
    }
}

async function sendQueuedFollowupAsChat(sessionId, item, itemId, dispatchEpoch) {
    var sid = String(sessionId || '');
    if (!sid || !item) return false;
    if (isFollowupAutoDispatchSuperseded(sid, dispatchEpoch)) return false;
    if (isSessionRunning(sid) || isServerStreamActive(sid)) {
        item.awaitingRunEnd = true;
        persistFollowupQueue(sid);
        renderFollowupQueue(sid);
        return false;
    }
    if (await isSessionAutoResumePending(sid)) {
        // A process-restarted session must resume its previous run before any
        // queued follow-up may start a new ordinary /chat turn.
        item.awaitingRunEnd = true;
        persistFollowupQueue(sid);
        renderFollowupQueue(sid);
        scheduleFollowupQueueDrain(sid, 1000);
        return false;
    }
    var previousAwaitingRunEnd = item.awaitingRunEnd;
    item.awaitingRunEnd = false;
    item.status = 'sending';
    persistFollowupQueue(sid);
    renderFollowupQueue(sid);
    var lockReady = await waitForSendPipelineIdle(sid, 4000);
    if (isFollowupAutoDispatchSuperseded(sid, dispatchEpoch)) {
        item.status = '';
        item.awaitingRunEnd = previousAwaitingRunEnd;
        persistFollowupQueue(sid);
        renderFollowupQueue(sid);
        return false;
    }
    if (!lockReady || isSendPipelineLocked(sid)) {
        item.status = '';
        item.awaitingRunEnd = true;
        persistFollowupQueue(sid);
        renderFollowupQueue(sid);
        scheduleFollowupQueueDrain(sid, 120);
        return false;
    }
    var started = await startFollowupChat({
        message: item.text,
        displayMessage: item.display || item.text,
        selectedSkills: item.skills || [],
        attachments: item.attachments || [],
        fromQueue: true,
        sessionId: sid,
        forceStart: true,
    });
    if (started) {
        takeFollowupItem(sid, itemId);
        renderFollowupQueue(sid);
        return true;
    }
    item.status = '';
    persistFollowupQueue(sid);
    renderFollowupQueue(sid);
    return false;
}

async function sendFollowupNowImpl(itemId, sessionId, options) {
    options = options || {};
    const followupTimingStartedAt = nowPipelineMs();
    const followupTimingCtx = {
        label: 'client_followup_step_timing',
        sessionId: sessionId || currentSessionId || '',
        runId: '',
        mode: 'followup',
        startedAt: followupTimingStartedAt
    };
    let _followupStepStart = followupTimingStartedAt;
    const sid = String(sessionId || currentSessionId || '');
    if (!sid) return;
    followupTimingCtx.sessionId = sid;
    var q = getFollowupQueue(sid);
    var idx = q.findIndex(function (item) { return String(item.id) === String(itemId); });
    if (idx < 0) return;
    const item = q[idx];
    if (!item) return;
    if ((typeof pendingHumanQuestions === 'function' && pendingHumanQuestions(sid).length)
        || (item.deferUntilRunEnd && (isSessionRunning(sid) || isServerStreamActive(sid)))) {
        item.awaitingRunEnd = true;
        persistFollowupQueue(sid);
        renderFollowupQueue(sid);
        return false;
    }
    item.steerMode = item.steerMode === 'append' ? 'append' : 'interrupt';
    followupTimingCtx.mode = 'followup_' + item.steerMode;
    if (idx !== 0) {
        var moved = q.splice(idx, 1)[0];
        q.unshift(moved);
        persistFollowupQueue(sid);
        renderFollowupQueue(sid);
        idx = 0;
    }
    if (['submitting', 'sending', 'accepted', 'restarting', 'sent', 'withdrawing'].includes(String(item.status || ''))) {
        return;
    }
    if (options.autoAfterRun) {
        return sendQueuedFollowupAsChat(sid, item, itemId, options.autoDispatchEpoch);
    }
    item.awaitingRunEnd = false;
    item.clientId = item.clientId || ('followup-' + item.id + '-' + Date.now());
    item.status = 'submitting';
    persistFollowupQueue(sid);
    renderFollowupQueue(sid);
    reportClientPipelineStep(followupTimingCtx, 'followup_prepare_item', _followupStepStart, {
        itemId: itemId,
        running: isSessionRunning(sid)
    });
    try {
        _followupStepStart = nowPipelineMs();
        item.steerInFlight = true;
        var steerResult = await sendSteerMessage(
            sid,
            item.text,
            item.clientId,
            item.skills || [],
            item.display || item.text,
            item.steerMode
        );
        item.steerInFlight = false;
        item.steerId = steerResult && steerResult.item && steerResult.item.id ? String(steerResult.item.id) : '';
        if (steerResult && steerResult.item && steerResult.item.mode) {
            item.steerMode = String(steerResult.item.mode) === 'append' ? 'append' : 'interrupt';
        }
        reportClientPipelineStep(followupTimingCtx, 'followup_send_steer', _followupStepStart, {
            restart: !!(steerResult && steerResult.restart),
            steerId: item.steerId || ''
        });
        if (item.cancelRequested) {
            _followupStepStart = nowPipelineMs();
            await cancelSteerMessage(sid, item);
            reportClientPipelineStep(followupTimingCtx, 'followup_cancel_after_steer', _followupStepStart);
            var withdrawn = takeFollowupItem(sid, item.id);
            if (withdrawn) returnFollowupToInput(sid, withdrawn);
            return;
        }
        if (steerResult && steerResult.restart && isMyAgentFeatureEnabled('followupRestart', false)) {
            _followupStepStart = nowPipelineMs();
            var previousRun = getSessionRunState(sid);
            if (previousRun) abortSessionRun(sid, 'followup-restart');
            markSessionRunInactive(sid);
            // The server has created a durable replacement operation, but the
            // replacement /chat has not started yet. Keep it persisted and
            // watcher-visible until the new stream is actually accepted.
            item.status = 'restarting';
            item.replacementRunId = String(steerResult.replacement_run_id || '');
            persistFollowupQueue(sid);
            renderFollowupQueue(sid);
            setSendButtonState();
            syncSessionListIndicatorClasses();
            reportClientPipelineStep(followupTimingCtx, 'followup_restart_takeover', _followupStepStart, {
                hadPreviousRun: !!previousRun
            });
            var restartLockReady = await waitForSendPipelineIdle(sid, 4000);
            if (!restartLockReady || isSendPipelineLocked(sid)) {
                appendLogVisible('追问接管已保留，等待发送通道释放。', 'error-log');
                scheduleAcceptedFollowupWatch(sid, itemId);
                return;
            }
            var restartStarted = await startFollowupChat({
                message: item.text,
                displayMessage: item.display || item.text,
                selectedSkills: item.skills || [],
                attachments: item.attachments || [],
                fromQueue: true,
                sessionId: sid,
                forceStart: true,
                preserveInput: true,
                asSteer: true,
                steerId: item.steerId,
                steerClientId: item.clientId,
                steerMode: item.steerMode,
                clientRunId: String(steerResult.replacement_run_id || ''),
            });
            if (restartStarted) {
                takeFollowupItem(sid, itemId);
                renderFollowupQueue(sid);
            } else {
                item.restartRecoveryAttempted = false;
                persistFollowupQueue(sid);
                renderFollowupQueue(sid);
                scheduleAcceptedFollowupWatch(sid, itemId);
            }
            return;
        }
        item.status = 'accepted';
        persistFollowupQueue(sid);
        renderFollowupQueue(sid);
        if (item.steerMode === 'append') {
            appendPendingSteerToProcess(sid, item);
        }
        reportClientPipelineStep(followupTimingCtx, 'followup_accepted_by_running_agent', followupTimingStartedAt, {
            steerId: item.steerId || ''
        });
        scheduleAcceptedFollowupWatch(sid, itemId);
        return;
    } catch (e) {
        reportClientPipelineStep(followupTimingCtx, 'followup_steer_error', _followupStepStart, {
            error: (e && e.message) ? String(e.message) : String(e)
        });
        item.steerInFlight = false;
        var msg = (e && e.message) ? String(e.message) : String(e);
        var canFallbackToChat = /session is not running/i.test(msg);
        if (canFallbackToChat && !item.steerRetryAfterSync) {
            item.steerRetryAfterSync = true;
            item.status = 'submitting';
            persistFollowupQueue(sid);
            renderFollowupQueue(sid);
            await refreshFollowupRunState(sid);
            await sleepMs(250);
            if (isSessionRunning(sid) || isServerStreamActive(sid)) {
                try {
                    item.steerInFlight = true;
                    var retrySteerResult = await sendSteerMessage(
                        sid,
                        item.text,
                        item.clientId,
                        item.skills || [],
                        item.display || item.text,
                        item.steerMode
                    );
                    item.steerInFlight = false;
                    item.steerId = retrySteerResult && retrySteerResult.item && retrySteerResult.item.id ? String(retrySteerResult.item.id) : '';
                    if (retrySteerResult && retrySteerResult.item && retrySteerResult.item.mode) {
                        item.steerMode = String(retrySteerResult.item.mode) === 'append' ? 'append' : 'interrupt';
                    }
                    item.status = 'accepted';
                    persistFollowupQueue(sid);
                    renderFollowupQueue(sid);
                    if (item.steerMode === 'append') {
                        appendPendingSteerToProcess(sid, item);
                    }
                    reportClientPipelineStep(followupTimingCtx, 'followup_steer_retry_after_sync', _followupStepStart, {
                        steerId: item.steerId || ''
                    });
                    scheduleAcceptedFollowupWatch(sid, itemId);
                    return;
                } catch (retryError) {
                    item.steerInFlight = false;
                    msg = (retryError && retryError.message) ? String(retryError.message) : String(retryError);
                    canFallbackToChat = /session is not running/i.test(msg);
                }
            }
        }
        if (!canFallbackToChat) {
            await syncFollowupQueueFromServer(sid);
            var reconciled = getFollowupQueue(sid).find(function (entry) {
                return String(entry.id) === String(item.id);
            });
            if (reconciled && reconciled.steerId && (reconciled.status === 'accepted' || reconciled.status === 'restarting')) {
                scheduleAcceptedFollowupWatch(sid, itemId);
                return;
            }
            if (item.cancelRequested) {
                item.status = 'sending';
                item.cancelRequested = false;
                persistFollowupQueue(sid);
                renderFollowupQueue(sid);
                appendLogVisible('追问已被接收，无法撤回: ' + msg, 'error-log');
                return;
            }
            item.status = '';
            persistFollowupQueue(sid);
            renderFollowupQueue(sid);
            appendLogVisible('追问插入失败: ' + msg, 'error-log');
            return;
        }
    }
    markSessionRunInactive(sid);
    if (typeof sessionStore !== 'undefined') sessionStore.setStreamActive(sid, false);
    // 降级 /chat 前必须等待发送锁释放，否则 sendMessage 会因锁未释放而静默返回，
    // 随后定时器无条件删除条目 → 表现为「点了立即发送却没反应」「发送后内容被删」。
    var lockAcquired = await waitForSendPipelineIdle(sid, 4000);
    if (!lockAcquired || isSendPipelineLocked(sid)) {
        // 锁迟迟未释放：恢复为 pending，交由后续 drain 或手动重试，绝不删除。
        item.status = '';
        item.steerInFlight = false;
        persistFollowupQueue(sid);
        renderFollowupQueue(sid);
        appendLogVisible('追问暂未发出（发送通道繁忙），已保留待重试: ' + msg, 'error-log');
        refreshPendingFollowupQueue(sid);
        return;
    }
    if (await isSessionAutoResumePending(sid)) {
        item.status = '';
        item.steerInFlight = false;
        item.awaitingRunEnd = true;
        persistFollowupQueue(sid);
        renderFollowupQueue(sid);
        scheduleFollowupQueueDrain(sid, 1000);
        return;
    }
    item.status = 'sending';
    persistFollowupQueue(sid);
    renderFollowupQueue(sid);
    reportClientPipelineStep(followupTimingCtx, 'followup_fallback_to_chat', followupTimingStartedAt);
    var chatStarted = await startFollowupChat({
        message: item.text,
        displayMessage: item.display || item.text,
        selectedSkills: item.skills || [],
        attachments: item.attachments || [],
        fromQueue: true,
        sessionId: sid,
        forceStart: true,
    });
    if (chatStarted) {
        // /chat 已成功开跑，追问作为普通用户轮次发出，删除队列项。
        takeFollowupItem(sid, itemId);
        renderFollowupQueue(sid);
    } else {
        // /chat 未真正开跑：恢复为 pending，保留条目，交由 drain 重试。
        item.status = '';
        item.steerInFlight = false;
        persistFollowupQueue(sid);
        renderFollowupQueue(sid);
        appendLogVisible('追问降级发送未成功，已保留待重试: ' + msg, 'error-log');
        refreshPendingFollowupQueue(sid);
    }
    return;
}

/* 会话级互斥：所有显式立即发送共用同一 dispatcher 链，防止并发 steer 竞争。 */
async function sendFollowupNow(itemId, sessionId, options) {
    options = options || {};
    const sid = String(sessionId || currentSessionId || '');
    if (!sid) return;
    var dispatchOptions = Object.assign({}, options);
    var observedManualEpoch = Number(followupManualDispatchEpochBySession[sid] || 0);
    if (options.manual) {
        recentComposerQueuedFollowup = null;
        observedManualEpoch += 1;
        followupManualDispatchEpochBySession[sid] = observedManualEpoch;
        cancelFollowupQueueDrain(sid);
        var q = getFollowupQueue(sid);
        var idx = q.findIndex(function (item) { return String(item.id) === String(itemId); });
        if (idx < 0) return;
        if (idx > 0) q.unshift(q.splice(idx, 1)[0]);
        q[0].awaitingRunEnd = false;
        persistFollowupQueue(sid);
        renderFollowupQueue(sid);
    }
    dispatchOptions.autoDispatchEpoch = observedManualEpoch;
    return withFollowupDispatch(sid, function () {
        if (dispatchOptions.autoAfterRun
            && isFollowupAutoDispatchSuperseded(sid, dispatchOptions.autoDispatchEpoch)) return false;
        return sendFollowupNowImpl(itemId, sid, dispatchOptions);
    });
}

async function sendMessage(options) {
    options = options || {};
    if (!options.fromQueue && !options.fromInlineRewrite && isChatFileUploadBusy()) return;
    const clientPipelineStartedAt = nowPipelineMs();
    let clientTimingCtx = {
        label: 'client_send_pipeline_step_timing',
        sessionId: options.sessionId || currentSessionId || '',
        runId: '',
        mode: options.asSteer ? 'followup_steer' : (options.fromQueue ? 'followup_queue' : (options.fromInlineRewrite ? 'inline_rewrite' : 'chat')),
        startedAt: clientPipelineStartedAt
    };
    let _clientStepStart = clientPipelineStartedAt;
    /* 立即快照「提交会话」：之后所有 await 都不能改变它，避免用户在 await 空隙切走后消息发到新会话。
       关键不变式：runSessionId === submitSessionId 全程恒等。 */
    const submitSessionIdInitial = options.sessionId || currentSessionId;
    if (!options.fromQueue && !options.fromInlineRewrite) rewriteInputWorkspacePaths();
    const visibleMessage = options.message != null ? String(options.message) : messageInput.value;
    const rawMessage = (options.fromQueue || options.fromInlineRewrite) ? visibleMessage : expandInputPathTokens(visibleMessage);
    if (!hasSendableText(rawMessage)) return;
    if (isSessionRunning(submitSessionIdInitial) && !options.forceStart) return;
    /* 在任何异步检查和可消费 UI 状态之前上锁，所有发送入口共享同一会话互斥。 */
    _clientStepStart = nowPipelineMs();
    const sendPipelineLock = acquireSendPipelineLock(submitSessionIdInitial);
    if (!sendPipelineLock) return;
    let submittedRunCtx = null;
    let submittedRunSessionId = submitSessionIdInitial;
    let optimisticRunState = null;
    try {
    messageLoadEpoch += 1;
    reportClientPipelineStep(clientTimingCtx, 'acquire_send_lock', _clientStepStart);
    if (options.forceStart && submitSessionIdInitial) {
        var previousRun = getSessionRunState(submitSessionIdInitial);
        if (previousRun) abortSessionRun(submitSessionIdInitial, 'followup-restart');
    }
    if (submitSessionIdInitial && typeof ensureLatestHistoryTailForLiveAppend === 'function') {
        var sendTailReady = await ensureLatestHistoryTailForLiveAppend(submitSessionIdInitial);
        if (!sendTailReady) {
            showUiAlert({
                title: '无法发送',
                message: '当前页面正在查看较早历史，且未能恢复最新历史尾部。请重试。',
                variant: 'error'
            });
            return;
        }
    }
    var selectedSkillsForRun = [];
    var consumeSelectedSkillsAfterLock = false;
    if (Array.isArray(options.selectedSkills)) {
        selectedSkillsForRun = options.selectedSkills.map(function (skill) { return String(skill || '').trim(); }).filter(Boolean);
    } else if (!options.fromQueue && !options.fromInlineRewrite && typeof window.consumeSelectedSkillsForSend === 'function') {
        consumeSelectedSkillsAfterLock = true;
    }

    if (consumeSelectedSkillsAfterLock) {
        try {
            selectedSkillsForRun = window.consumeSelectedSkillsForSend();
        } catch (e) {
            selectedSkillsForRun = [];
        }
    }
    var uiBaseMessage = options.displayMessage != null ? String(options.displayMessage) : rawMessage;
    var displayMessage = buildSelectedSkillsDisplayMessage(uiBaseMessage, selectedSkillsForRun);
    reportClientPipelineStep(clientTimingCtx, 'preflight_checks', _clientStepStart, {
        forceStart: !!options.forceStart,
        fromQueue: !!options.fromQueue,
        fromInlineRewrite: !!options.fromInlineRewrite,
        asSteer: !!options.asSteer
    });
    _clientStepStart = nowPipelineMs();
    const clientRunId = options.clientRunId || ((window.crypto && window.crypto.randomUUID)
        ? window.crypto.randomUUID()
        : ('run-' + Date.now() + '-' + Math.random().toString(16).slice(2)));
    clientTimingCtx.runId = clientRunId;
    const ac = new AbortController();
    optimisticRunState = {
        controller: ac,
        ctx: null,
        runId: clientRunId,
        optimistic: true,
        submitted: false,
        suppressFollowupButton: true
    };
    // Publish before rewrite truncation, session creation, event-count reads,
    // or any other network await so every send path flips in the same frame.
    if (submitSessionIdInitial) {
        if (typeof clearSessionStreamStopSuppress === 'function') clearSessionStreamStopSuppress(submitSessionIdInitial);
        // A new foreground turn acknowledges the previous completion. Queued
        // turns intentionally keep it so the sidebar can show completed work
        // while the next queued turn is running.
        if (!options.fromQueue) clearSessionUnreadState(submitSessionIdInitial);
        setSessionRunState(submitSessionIdInitial, optimisticRunState);
    } else {
        optimisticNewSessionRun = optimisticRunState;
    }
    setSendButtonState();
    syncSessionListIndicatorClasses();
    reportClientPipelineStep(clientTimingCtx, 'publish_optimistic_run_state', _clientStepStart);
    if (pendingRewriteTruncate && pendingRewriteTruncate.sessionId === submitSessionIdInitial) {
        _clientStepStart = nowPipelineMs();
        const pendingRewrite = pendingRewriteTruncate;
        const truncated = await processRewriteTruncateAsync(pendingRewrite);
        reportClientPipelineStep(clientTimingCtx, 'pending_rewrite_truncate', _clientStepStart, { ok: !!truncated });
        if (!truncated) {
            pendingRewriteTruncate = null;
            return;
        }
        pendingRewriteTruncate = null;
        uiEventCountCache.updateFromServer(submitSessionIdInitial, pendingRewrite.before);
        if (ac.signal.aborted) return;
    }
    hideRewriteUndoToast();

    hideSubagentContinueBanner();
    const userSentAt = new Date().toISOString();

    let submitSessionId = submitSessionIdInitial;
    if (!submitSessionId) {
        _clientStepStart = nowPipelineMs();
        submitSessionId = await materializeNewSession();
        clientTimingCtx.sessionId = submitSessionId || clientTimingCtx.sessionId;
        reportClientPipelineStep(clientTimingCtx, 'materialize_new_session', _clientStepStart, { ok: !!submitSessionId });
        if (!submitSessionId) return;
        if (!transferSendPipelineLock(sendPipelineLock, submitSessionId)) return;
        if (ac.signal.aborted) return;
        if (optimisticNewSessionRun === optimisticRunState) optimisticNewSessionRun = null;
        setSessionRunState(submitSessionId, optimisticRunState);
        setSendButtonState();
        syncSessionListIndicatorClasses();
    }
    clientTimingCtx.sessionId = submitSessionId || clientTimingCtx.sessionId;
    const runSessionId = submitSessionId;
    submittedRunSessionId = runSessionId;
    if (typeof clearSessionStreamStopSuppress === 'function') clearSessionStreamStopSuppress(runSessionId);
    reportClientPipelineStep(clientTimingCtx, 'prepare_client_run_id', _clientStepStart);
    _clientStepStart = nowPipelineMs();
    let preCount = await getUiEventCount(submitSessionId, {
        preferCache: true,
        maxAgeMs: 10000,
        signal: ac.signal,
        timeoutMs: 5000
    });
    if (ac.signal.aborted) return;
    const existingStreamForIndex = (submitSessionId === currentSessionId) ? getVisibleChatStream() : null;
    if (existingStreamForIndex) {
        existingStreamForIndex.querySelectorAll('.msg-wrap--user[data-event-index]').forEach(function (wrap) {
            const n = Number(wrap.getAttribute('data-event-index'));
            if (Number.isFinite(n)) preCount = Math.max(preCount, Math.floor(n) + 1);
        });
    }
    reportClientPipelineStep(clientTimingCtx, 'resolve_ui_event_count', _clientStepStart, { preCount: preCount });
    _clientStepStart = nowPipelineMs();
    if (sessionStore && typeof sessionStore.resetSseSeq === 'function') {
        sessionStore.resetSseSeq(runSessionId);
    }
    reportClientPipelineStep(clientTimingCtx, 'prepare_sse_sequence_state', _clientStepStart);

    /* 用户在 createNewSession / getUiEventCount 期间切走：
       后台仍然发起 /chat（消息已属于 runSessionId），但不要往当前可见 stream 画用户气泡。 */
    const switchedAway = currentSessionId !== runSessionId;
    let runCtx;
    if (switchedAway) {
        const offscreen = document.createElement('div');
        offscreen.className = 'chat-stream is-offscreen';
        offscreen.dataset.partialBackgroundRun = '1';
        if (typeof offscreenRoot !== 'undefined' && offscreenRoot) offscreenRoot.appendChild(offscreen);
        runCtx = newDomContext(offscreen);
    } else {
        if (!getVisibleChatStream()) ensureVisibleChatStreamSlot();
        runCtx = newDomContext(getVisibleChatStream());
    }
    _clientStepStart = nowPipelineMs();
    submittedRunCtx = runCtx;
    runCtx.runId = clientRunId;
    initRunFinalTracking(runCtx);
    runCtx.runStartedAt = userSentAt;
    runCtx.lastUserEventIndex = preCount;
    resetLlmState(runCtx);
    finalizeLlmStreamChunks(runCtx);
    sealProcessGroup(runCtx);
    optimisticRunState.ctx = runCtx;
    optimisticRunState.optimistic = false;
    setSessionRunState(runSessionId, optimisticRunState);
    setSendButtonState();
    syncSessionListIndicatorClasses();
    reportClientPipelineStep(clientTimingCtx, 'prepare_run_context', _clientStepStart, { switchedAway: !!switchedAway });
    _clientStepStart = nowPipelineMs();
    const renderAsSteer = !!options.asSteer;
    if (!renderAsSteer) {
        applySessionEvent({ type: 'user', content: displayMessage, created_at: userSentAt }, {
            sessionId: runSessionId,
            eventIndex: preCount,
            source: 'local-send',
        });
    }
    uiEventCountCache.updateFromServer(runSessionId, preCount + 1);
    if (!switchedAway) {
        liveAutoFollow = true;
        streamChatNearBottom = true;
        streamProcNearBottom = true;
        if (renderAsSteer) {
            var optimisticSteerClientId = String(options.steerClientId || '');
            var optimisticSteerId = String(options.steerId || '');
            var optimisticSteerOpId = optimisticSteerClientId || optimisticSteerId || clientRunId;
            var optimisticSteerMode = String(options.steerMode || 'interrupt') === 'append' ? 'append' : 'interrupt';
            prepareSteerProcessBoundary(runCtx, optimisticSteerMode, optimisticSteerOpId);
            var optimisticSteerRow = appendSteerProcessMessage(
                runSessionId, runCtx, displayMessage, optimisticSteerOpId,
                optimisticSteerMode, true
            );
            if (optimisticSteerRow) {
                optimisticSteerRow.dataset.steerEventReserved = '1';
                if (optimisticSteerClientId) optimisticSteerRow.dataset.steerClientId = optimisticSteerClientId;
                if (optimisticSteerId) optimisticSteerRow.dataset.steerId = optimisticSteerId;
            }
        } else {
            appendMessage(runCtx, 'user', displayMessage, { eventIndex: preCount, turnTruncateIdx: preCount, createdAt: userSentAt }, runSessionId);
        }
        if (!options.fromQueue && !options.preserveInput) {
            messageInput.value = '';
            persistInputDraft(runSessionId, '');
            clearInputPathTokens();
            autoResizeTextarea();
            setSendButtonState();
        }
    }
    optimisticRunState.suppressFollowupButton = false;
    setSendButtonState();
    updateSidebarLastUserPreviewImmediate(runSessionId, displayMessage);
    lastUserMessageBySession[runSessionId] = displayMessage;
    reportClientPipelineStep(clientTimingCtx, 'local_user_render', _clientStepStart, { renderAsSteer: !!renderAsSteer, switchedAway: !!switchedAway });
    _clientStepStart = nowPipelineMs();
    const formData = new FormData();
    formData.append('message', rawMessage);
    const rememberedAttachments = Array.isArray(options.attachments)
        ? options.attachments
        : (window.MyAgentPathPicker
            && typeof window.MyAgentPathPicker.chatAttachments === 'function'
            ? window.MyAgentPathPicker.chatAttachments(messageInput)
            : []);
    const attachmentsForRun = rememberedAttachments.filter(function (item) {
        return item && item.path && rawMessage.indexOf(String(item.path)) >= 0;
    });
    if (attachmentsForRun.length) {
        formData.append('attachments', JSON.stringify(attachmentsForRun));
    }
    if (!Array.isArray(options.attachments) && window.MyAgentPathPicker
            && typeof window.MyAgentPathPicker.clearChatAttachments === 'function') {
        window.MyAgentPathPicker.clearChatAttachments(messageInput);
    }
    // The backend owns durable UI-message decoration. Sending the undecorated
    // value keeps the optimistic row and the reloaded history identical.
    formData.append('ui_message', uiBaseMessage);
    formData.append('session_id', runSessionId);
    formData.append('client_run_id', clientRunId);
    formData.append('stream_protocol', 'runtime_v2');
    if (options.fromQueue) formData.append('preserve_unread_result', 'true');
    formData.append(
        'ui_language',
        (document.documentElement && document.documentElement.getAttribute('data-language'))
            || localStorage.getItem('myagent-language')
            || 'zh-CN'
    );
    if (selectedSkillsForRun && selectedSkillsForRun.length) {
        formData.append('selected_skills', JSON.stringify(selectedSkillsForRun));
    }
    if (renderAsSteer) formData.append('followup_steer', 'true');
    if (renderAsSteer && options.steerId) formData.append('steer_id', String(options.steerId));
    /* 发送后优先使用本轮 API usage/cache_stats 刷新 token；缺少 usage 时仍保留上一快照。 */
    if (!switchedAway) applyContextTokenLabelForCurrentSession();
    let streamEventIdx = preCount + 1;
    let streamDisconnectedUnexpectedly = false;
    try {
        reportClientPipelineStep(clientTimingCtx, 'build_form_data', _clientStepStart, { followupSteer: !!renderAsSteer });
        _clientStepStart = nowPipelineMs();
        optimisticRunState.submitted = true;
        let response = null;
        for (let migrationAttempt = 0; migrationAttempt < 120; migrationAttempt += 1) {
            response = await fetch('/chat', { method: 'POST', body: formData, signal: ac.signal });
            if (response.status !== 425) break;
            const pending = await response.json().catch(function () { return null; });
            if (!pending || pending.reason !== 'runtime_migration_pending') break;
            if (migrationAttempt >= 119) {
                rollbackOptimisticUserEvent(runSessionId, preCount);
                throw new Error('Runtime V2 migration timed out');
            }
            const retryMs = Math.max(100, Math.min(Number(pending.retry_after_ms) || 250, 1000));
            await new Promise(function (resolve) { setTimeout(resolve, retryMs); });
        }
        reportClientPipelineStep(clientTimingCtx, 'fetch_chat_response_headers', _clientStepStart, { status: response && response.status });
        _clientStepStart = nowPipelineMs();
        if (response.status === 409) {
            streamDisconnectedUnexpectedly = true;
            rollbackOptimisticUserEvent(runSessionId, preCount);
            if (!options.fromQueue && isMyAgentFeatureEnabled('followupRestart', false)) {
                appendFollowupQueueItem(
                    runSessionId,
                    rawMessage,
                    displayMessage,
                    selectedSkillsForRun,
                    attachmentsForRun
                );
            } else if (!options.fromQueue && runSessionId === currentSessionId) {
                messageInput.value = visibleMessage;
                persistInputDraft(runSessionId, visibleMessage);
                if (typeof window.setSelectedSkillsForCurrentSession === 'function') {
                    window.setSelectedSkillsForCurrentSession(selectedSkillsForRun);
                }
                autoResizeTextarea();
            }
            scheduleActiveSessionReconnect(runSessionId, { delayMs: 0, failure: true });
            return false;
        }
        var responseContentType = String(response.headers && response.headers.get
            ? (response.headers.get('content-type') || '')
            : '').toLowerCase();
        if (response.ok && responseContentType.indexOf('text/event-stream') >= 0
            && typeof options.onRunStarted === 'function') {
            try {
                options.onRunStarted({ sessionId: runSessionId, runId: clientRunId });
            } catch (onStartedError) {
                console.error('run start callback failed:', onStartedError);
            }
        }
        streamEventIdx = await consumeAgentSseResponse(response, runCtx, runSessionId, streamEventIdx);
        if (!runCtx || runCtx.terminalSeen !== true) {
            streamDisconnectedUnexpectedly = true;
        }
        reportClientPipelineStep(clientTimingCtx, 'consume_sse_until_done', _clientStepStart, { streamEventIdx: streamEventIdx });
        return true;
    } catch (error) {
        reportClientPipelineStep(clientTimingCtx, 'chat_fetch_or_sse_error', _clientStepStart, { error: (error && error.message) ? String(error.message) : String(error) });
        if (error.name === 'AbortError') {
            if (getRunAbortReason(runSessionId, runCtx) === 'user') appendLog(runCtx, '任务已中断', 'status', runSessionId);
        }
        else {
            console.error('请求失败:', error);
            streamDisconnectedUnexpectedly = true;
            const msg = (error && error.message) ? String(error.message) : String(error);
            appendLog(runCtx, '请求失败: ' + msg, 'error-log', runSessionId);
        }
        return false;
    } finally {
        _clientStepStart = nowPipelineMs();
        finalizeLlmStreamChunks(runCtx);
        finalizeProgressStreamChunks(runCtx);
        if (!switchedAway && runSessionId === currentSessionId && getRunAbortReason(runSessionId, runCtx) !== 'user') {
            scheduleFinalVisibleAfterRunIfEnabled(runSessionId, runCtx, { delayMs: 120 });
        }
        if (liveAutoFollow && !switchedAway) {
            finishStreamScrollIfFollow(runCtx, runSessionId);
        }
        if (runSessionId !== currentSessionId) {
            void tryMarkSessionUnreadComplete(runSessionId);
        } else {
            updateSubagentContinueBanner(runSessionId);
        }
        if (getSessionRunState(runSessionId) && runCtx && runCtx.terminalSeen === true) {
            clearSessionRunStateIfMatch(runSessionId, clientRunId);
        }
        if (streamDisconnectedUnexpectedly && runSessionId === currentSessionId && getRunAbortReason(runSessionId, runCtx) !== 'user') {
            scheduleActiveSessionReconnect(runSessionId, { delayMs: 500, failure: true });
            scheduleActiveSessionReconnect(runSessionId, { delayMs: 2500, failure: true });
        } else {
            resetStreamReconnectState(runSessionId);
        }
        if (runSessionId !== currentSessionId) {
            const el = runCtx.stream;
            const reusableCompletedCache = !!(
                el && el.parentNode
                && !switchedAway
                && !streamDisconnectedUnexpectedly
                && getRunAbortReason(runSessionId, runCtx) !== 'user'
                && runCtx.streamCompletedSuccessfully === true
                && runCtx.seenFinal === true
                && el.dataset.partialBackgroundRun !== '1'
                && el.dataset.cacheSessionId === String(runSessionId)
                && el.dataset.sessionLoadFailed !== '1'
            );
            if (reusableCompletedCache) {
                el.dataset.sessionLoadOk = '1';
                delete el.dataset.sessionLoading;
                delete el.dataset.sessionLoadFailed;
                if (typeof cacheOrderTouch === 'function') cacheOrderTouch(runSessionId);
                if (typeof trimCachedSessionStreams === 'function') trimCachedSessionStreams();
            } else {
                // A partial background projection may coexist with an older
                // cached stream for this session. Both are stale after this
                // run, so invalidate the registered cache as well as runCtx.
                if (typeof discardCachedSessionStream === 'function') {
                    discardCachedSessionStream(runSessionId);
                }
                if (el && el.parentNode) el.remove();
            }
        }
        setSendButtonState();
        syncSessionListIndicatorClasses();
        void refreshSingleSessionRow(runSessionId);
        applyContextTokenLabelForCurrentSession();
        if (runSessionId === currentSessionId && countRunningSubagentCards() > 0) {
            scheduleSubagentIncrementalSync();
        }
        reportClientPipelineStep(clientTimingCtx, 'finalize_visible_state', _clientStepStart, {
            disconnected: !!streamDisconnectedUnexpectedly,
            currentSession: runSessionId === currentSessionId
        });
    }
    } finally {
        _clientStepStart = nowPipelineMs();
        if (optimisticNewSessionRun === optimisticRunState) optimisticNewSessionRun = null;
        if (optimisticRunState && optimisticRunState.submitted === false && submittedRunSessionId) {
            clearSessionRunStateIfMatch(submittedRunSessionId, optimisticRunState.runId);
        }
        releaseSendPipelineLock(sendPipelineLock);
        var stoppedByUser = getRunAbortReason(submittedRunSessionId, submittedRunCtx) === 'user'
            || (optimisticRunState && optimisticRunState.abortReason === 'user');
        reportClientPipelineStep(clientTimingCtx, 'release_send_lock', _clientStepStart, {
            stoppedByUser: !!stoppedByUser,
            fromQueue: !!options.fromQueue
        });
        setSendButtonState();
        syncSessionListIndicatorClasses();
        if (!stoppedByUser && getFollowupQueue(submittedRunSessionId).length) {
            renderFollowupQueue(submittedRunSessionId);
        }
    }
}

function readComposerActionState() {
    const sessionId = currentSessionId;
    const running = isSessionRunning(sessionId);
    const uploadBusy = isChatFileUploadBusy();
    const sendable = hasSendableText(messageInput.value);
    return {
        sessionId: sessionId,
        running: running,
        uploadBusy: uploadBusy,
        sendable: sendable,
        pendingQuestion: sendable && !uploadBusy
            && typeof pendingHumanQuestions === 'function'
            && pendingHumanQuestions(sessionId).length > 0,
        activeRun: running ? getSessionRunState(sessionId) : null,
    };
}

function queueComposerBehindPendingQuestion(state) {
    if (!state.pendingQuestion || !state.sendable || state.uploadBusy) return false;
    return enqueueCurrentInputAsFollowup({ pendingQuestion: true });
}

function dispatchComposerAction(allowStop) {
    const state = readComposerActionState();
    if (state.uploadBusy) return false;
    if (!state.sessionId && optimisticNewSessionRun) {
        if (allowStop) pauseCurrentRun();
        return false;
    }
    if (!allowStop && !state.sendable && state.sessionId) {
        const queue = getFollowupQueue(state.sessionId);
        const recent = recentComposerQueuedFollowup;
        recentComposerQueuedFollowup = null;
        const preferredPending = recent && recent.sessionId === state.sessionId
            ? queue.find(function (item) {
                return item && !item.status && String(item.id) === recent.itemId;
            })
            : null;
        const pendingToSend = preferredPending || queue.find(function (item) {
            return item && !item.status;
        });
        if (pendingToSend) {
            void sendFollowupNow(String(pendingToSend.id), state.sessionId, { manual: true });
            return true;
        }
    }
    if (queueComposerBehindPendingQuestion(state)) return true;
    if (state.running) {
        const canQueueFollowup = isMyAgentFeatureEnabled('followupRestart', false)
            && state.sendable
            && !state.uploadBusy
            && !(state.activeRun && state.activeRun.suppressFollowupButton);
        if (canQueueFollowup) return enqueueCurrentInputAsFollowup();
        if (allowStop) pauseCurrentRun();
        return false;
    }
    void sendMessage();
    return true;
}

messageInput.addEventListener('keydown', function onComposerInputKeydown(e) {
    if (isInputMethodComposing(e) || e.key !== 'Enter') return;
    // Ctrl+Enter → 插入换行（跨浏览器兼容）
    if (e.ctrlKey && !e.shiftKey && !e.metaKey && !e.altKey) {
        insertTextareaNewline(this, e);
        syncComposerInputState();
        return;
    }
    // 带修饰键的 Enter 保留为编辑操作；纯 Enter 才提交。
    if (!isInputSubmitShortcut(e, 'chat')) return;
    e.preventDefault();
    dispatchComposerAction(false);
});
chatContainer.addEventListener('scroll', function () {
    refreshLiveAutoFollowPins();
    scheduleTocActiveUpdate();
    maybeAutoLoadOlderHistory();
}, { passive: true });
sendBtn.addEventListener('click', function () {
    dispatchComposerAction(true);
});
window.addEventListener('resize', positionFollowupQueuePanel);
window.addEventListener('scroll', positionFollowupQueuePanel, true);
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
                const r = await historyOperationJson(
                    '/sessions/' + encodeURIComponent(s.data.sessionId) + '/append_ui_events',
                    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ events: s.data.tail }) },
                    45000
                );
                if (!r || !r.ok) { alert('撤销失败，请重试。'); return; }
                if (s.data.sessionId === currentSessionId) {
                    showLoading();
                    try {
                        await loadSessionMessages(s.data.sessionId, 'bottom', { full: true });
                    } finally {
                        hideLoading();
                    }
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
