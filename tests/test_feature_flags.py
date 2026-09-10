import json
import re
import sys
from pathlib import Path
import asyncio


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _extract_feature_flags(html: str) -> dict:
    match = re.search(r"window\.__MYAGENT_FEATURES__=([^<;]+);", html)
    assert match, "feature flag injection missing"
    return json.loads(match.group(1))


def _extract_steer_mode(html: str) -> str:
    match = re.search(r"window\.__MYAGENT_STEER_MODE__=([^<;]+);", html)
    assert match, "steer mode injection missing"
    return json.loads(match.group(1))


def test_index_html_injects_default_feature_values(monkeypatch):
    import webui

    monkeypatch.delenv("AGENT_TEAM_ENABLED", raising=False)
    monkeypatch.delenv("ASK_USER_ENABLED", raising=False)
    monkeypatch.delenv("SECURITY_ENABLED", raising=False)
    monkeypatch.setenv("MYAGENT_ENABLE_FOLLOWUP_RESTART", "0")
    monkeypatch.setenv("MYAGENT_ENABLE_STREAM_RECONNECT", "0")
    monkeypatch.setenv("MYAGENT_ENABLE_FINAL_RECONCILE", "1")
    monkeypatch.delenv("MYAGENT_SMOOTH_STREAM_ENABLED", raising=False)

    flags = _extract_feature_flags(str(webui.get_index_html()))

    assert flags == {
        "askUser": True,
        "followupRestart": False,
        "streamReconnect": False,
        "finalReconcile": True,
        "smoothStream": True,
        "security": True,
    }


def test_index_html_injects_independent_feature_overrides(monkeypatch):
    import webui

    monkeypatch.delenv("ASK_USER_ENABLED", raising=False)
    monkeypatch.delenv("SECURITY_ENABLED", raising=False)
    monkeypatch.setenv("AGENT_TEAM_ENABLED", "true")
    monkeypatch.setenv("MYAGENT_ENABLE_FOLLOWUP_RESTART", "1")
    monkeypatch.setenv("MYAGENT_ENABLE_STREAM_RECONNECT", "true")
    monkeypatch.setenv("MYAGENT_ENABLE_FINAL_RECONCILE", "0")
    monkeypatch.setenv("MYAGENT_SMOOTH_STREAM_ENABLED", "yes")

    flags = _extract_feature_flags(str(webui.get_index_html()))

    assert flags == {
        "askUser": True,
        "followupRestart": True,
        "streamReconnect": True,
        "finalReconcile": False,
        "smoothStream": True,
        "security": True,
    }


def test_index_html_injects_goal_feature_override(monkeypatch):
    import webui

    monkeypatch.setenv("GOAL_ENABLED", "0")
    flags = _extract_feature_flags(str(webui.get_index_html()))
    assert "goal" not in flags


def test_index_html_injects_ask_user_feature_override(monkeypatch):
    import webui

    monkeypatch.setenv("ASK_USER_ENABLED", "on")
    flags = _extract_feature_flags(str(webui.get_index_html()))
    assert flags["askUser"] is True


def test_index_html_defaults_agent_team_disabled(monkeypatch):
    import webui

    monkeypatch.delenv("AGENT_TEAM_ENABLED", raising=False)
    flags = _extract_feature_flags(str(webui.get_index_html()))
    assert "agentTeam" not in flags


def test_index_html_defaults_stream_reconnect_enabled(monkeypatch):
    import webui

    monkeypatch.delenv("MYAGENT_ENABLE_STREAM_RECONNECT", raising=False)

    flags = _extract_feature_flags(str(webui.get_index_html()))

    assert flags["streamReconnect"] is True


def test_index_html_injects_smooth_stream_environment_switch(monkeypatch):
    import webui

    monkeypatch.delenv("MYAGENT_SMOOTH_STREAM_ENABLED", raising=False)
    assert _extract_feature_flags(str(webui.get_index_html()))["smoothStream"] is True

    monkeypatch.setenv("MYAGENT_SMOOTH_STREAM_ENABLED", "on")
    assert _extract_feature_flags(str(webui.get_index_html()))["smoothStream"] is True

    monkeypatch.setenv("MYAGENT_SMOOTH_STREAM_ENABLED", "off")
    assert _extract_feature_flags(str(webui.get_index_html()))["smoothStream"] is False


def test_index_html_injects_append_steer_default_and_environment_override(monkeypatch):
    import webui

    monkeypatch.delenv("MYAGENT_STEER_MODE", raising=False)
    assert _extract_steer_mode(str(webui.get_index_html())) == "append"
    monkeypatch.setenv("MYAGENT_STEER_MODE", "interrupt")
    assert _extract_steer_mode(str(webui.get_index_html())) == "interrupt"
    monkeypatch.setenv("MYAGENT_STEER_MODE", "invalid")
    assert _extract_steer_mode(str(webui.get_index_html())) == "append"


def test_index_html_injects_security_env_override(monkeypatch):
    import webui

    monkeypatch.setenv("SECURITY_ENABLED", "0")
    assert _extract_feature_flags(str(webui.get_index_html()))["security"] is False
    monkeypatch.setenv("SECURITY_ENABLED", "1")
    assert _extract_feature_flags(str(webui.get_index_html()))["security"] is True


def test_index_html_no_longer_injects_frontend_version():
    import webui

    html = str(webui.get_index_html())
    assert "__MYAGENT_FRONTEND_VERSION__" not in html
    assert "dataset.frontendVersion" not in html


def test_permission_mode_ui_regressions():
    i18n = (ROOT / "frontend/src/app/modules/i18n.js").read_text(encoding="utf-8")
    permissions = (ROOT / "frontend/src/app/modules/permissions.js").read_text(
        encoding="utf-8"
    )
    advanced_settings = (ROOT / "app/templates/advance_config.html").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "frontend/src/styles/app.css").read_text(encoding="utf-8")
    html = (ROOT / "frontend/src/shell-body.html").read_text(encoding="utf-8")
    index_html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    interactions = (ROOT / "frontend/src/app/modules/human-interactions.js").read_text(
        encoding="utf-8"
    )

    # Permission mode dropdown and security settings are translated.
    for key in (
        "权限",
        "请求批准",
        "替我审批",
        "完全访问权限",
        "应用层受限",
        "更改权限",
        "Agent 的操作应如何获得审批？",
        "工作区内写入自动执行；删除、联网、出项目按普通审批",
        "低风险自动放行，高风险仍转给你确认",
        "普通操作和工作区删除免审批；高危系统命令仍确认",
        "自动审查中：审查 Agent 正在核对你的任务意图与请求风险。",
        "自动审批已批准",
        "自动审批已拒绝",
        "自动审查不可用（已转人工确认）",
        "【命令风险】",
        "【命令目的】",
        "可人工覆盖本次请求（只此一次，不沉淀规则）",
        "始终允许",
        "本次允许",
        "工作区沙箱外处理权限",
        "工作区外处理权限",
        "权限与安全",
        "已开启：写/删/Shell 工作区外操作自动放行。",
        "撤销授权",
        "权限规则（始终允许 / 必问 / 拒绝）",
        "网页抓取预批准域名（web_fetch 免审批）",
    ):
        assert f"'{key}':" in i18n

    # Codex-style permission dropdown: menu title, icon + label + one-line
    # description per mode, and a trigger icon that follows the active mode.
    assert "permission-mode-title" in css
    assert "permission-mode-option" in css
    assert "enforcement === 'partial'" in interactions
    assert "系统级出站防护已启用（无网络命令强制断网；获批联网暂不限制目标）" in interactions
    assert "maybeShowEgressPartialNotice" in permissions
    assert "permission-mode-ico" in css
    assert "permission-mode-desc" in css
    assert "Agent 的操作应如何获得审批？" in html
    assert "permission-mode-option" in html
    # The vite entry (frontend/index.html) is the file that actually gets
    # served; shell-body.html must stay in sync with it. The legacy sandbox
    # badge must be gone from both.
    assert "Agent 的操作应如何获得审批？" in index_html
    assert "permission-mode-option" in index_html
    assert 'id="settings-security-rules-list"' not in html
    assert 'id="settings-security-rules-list"' not in index_html
    assert 'data-advanced-tab="security"' in advanced_settings
    assert 'id="advanced-security-rules-list"' in advanced_settings
    assert 'id="advanced-security-extensions-list"' in advanced_settings
    assert 'id="advanced-security-web-fetch-domains"' in advanced_settings
    assert "permission-sandbox-status" not in html
    assert "permission-sandbox-status" not in index_html
    assert "PERMISSION_MODE_ICONS" in permissions
    assert "permission-mode-ico" in permissions
    assert "permissionControlsEnabled" in permissions
    assert "control.hidden = !enabled" in permissions
    assert ".composer-permission-bar[hidden]" in css
    hidden_rule = css.split(".composer-permission-bar[hidden]", 1)[1].split("}", 1)[0]
    assert "display: none !important;" in hidden_rule

    # The three permission tiers are color-coded green / blue / amber, both in
    # the dropdown options and on the trigger for the active mode.
    assert 'data-permission-mode="ask_for_approval"] .permission-mode-label' in css
    assert 'data-permission-mode="approve_for_me"] .permission-mode-label' in css
    assert 'data-permission-mode="full_access"] .permission-mode-label' in css
    assert "trigger.setAttribute('data-mode'" in permissions

    # Auto-review emits structured status events and the frontend renders an
    # in-progress -> approved/denied status row anchored to the tool row.
    assert "renderAutoReviewStatusEvent" in interactions
    assert "appendApprovalReviewExplanation" in interactions
    assert "event && event.risk_analysis" in interactions
    assert "event && event.command_purpose" in interactions
    auto_review_renderer = interactions.split(
        "function renderAutoReviewStatusEvent", 1
    )[1].split("function persistHumanInteractionDraft", 1)[0]
    assert auto_review_renderer.index("el.textContent = '';") < auto_review_renderer.index(
        "if (status === 'in_progress')"
    )
    event_dispatch = (ROOT / "frontend/src/app/modules/event-dispatch.js").read_text(
        encoding="utf-8"
    )
    assert "event.type === 'auto_review_status'" in event_dispatch
    agent_loop = (ROOT / "app/agent_loop.py").read_text(encoding="utf-8")
    assert '"type": "auto_review_status"' in agent_loop
    assert '"status": "in_progress"' in agent_loop

    # One-time outside-workspace handling permission: approval-card button,
    # advanced security-tab management, and backend wiring.
    assert "allow_external_workspace" in interactions
    assert "allow_external_workspace_once" in interactions
    assert "external_workspace_grantable" in interactions
    assert "advanced-security-workspace-scope-status" in advanced_settings
    assert "advanced-security-workspace-scope-revoke" in advanced_settings
    assert "allow_external_workspace_ops" in advanced_settings
    assert "settings-external-ops" not in permissions
    assert "settings-external-ops" not in index_html
    assert "settings-external-ops" not in html
    assert "是否授权目录 {dir_hint}？" in agent_loop
    assert "确认执行工具（已授权目录 {hint}）" in agent_loop
    assert "确认执行工具（已允许本次目录 {hint}）" in agent_loop
    assert "return_decision=True" in agent_loop
    assert "human-approval-group" not in interactions
    gate = (ROOT / "app/tool_approval_gate.py").read_text(encoding="utf-8")
    assert "resolve_tool_approval_decision" in gate
    assert "return_decision" in gate

    # Full-access warning uses the unified UI modal, not window.confirm.
    assert "openUiModal({" in permissions
    assert "window.confirm(" not in permissions
    assert "showUiAlert({" in permissions
    assert "window.alert(" not in permissions

    # The permission selector docks on the left edge of the input panel,
    # mirroring the model selector on the right edge.
    bar = css.split(".composer-permission-bar {", 1)[1].split("}", 1)[0]
    assert "left: calc((100% - var(--composer-panel-end-gutter) - var(--composer-input-width)) / 4);" in bar
    assert "top: 50%;" in bar
    assert "transform: translate(-50%, -50%);" in bar
    panel = css.split(".panel {", 1)[1].split("}", 1)[0]
    assert "padding: 0.6rem 0.5rem 0.6rem 0;" in panel

    # Permission dropdown options use the same theme-aware frame as the model
    # selector (accent border + tint on hover/active).
    option_frame = css.split(".permission-mode-option:hover,", 1)[1].split("}", 1)[0]
    assert "rgba(var(--accent-rgb), 0.14)" in option_frame
    assert "border-color: rgba(var(--accent-rgb), 0.24);" in option_frame

    # Approval dialogs use an opaque dialog surface, not the glass layer.
    modal = css.split(".ui-modal {", 1)[1].split("}", 1)[0]
    assert "background: var(--modal-bg);" in modal

    # Approval cards in the feed are opaque too (the component the user
    # actually sees when approving a tool call).
    approval_card = css.split(".human-interaction-card {", 1)[1].split("}", 1)[0]
    assert "background: var(--modal-bg);" in approval_card
    light_card = css.split(":root.theme-light .human-interaction-card {", 1)[1].split("}", 1)[0]
    assert "background: #ffffff;" in light_card


def test_frontend_feature_entrypoints_are_flag_guarded():
    sse = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    sessions = (ROOT / "frontend/src/app/modules/session-management.js").read_text(encoding="utf-8")
    webui = (ROOT / "app/webui.py").read_text(encoding="utf-8")

    assert "isMyAgentFeatureEnabled('followupRestart', false)" in sse
    assert "isMyAgentFeatureEnabled('streamReconnect', true)" in sse
    assert "isMyAgentFeatureEnabled('finalReconcile', true)" in sse
    assert "function scheduleFinalVisibleAfterRunIfEnabled" in sse
    assert "const SSE_IDLE_TIMEOUT_MS = 120000" in sse
    assert "const SSE_RESUME_PROBE_TIMEOUT_MS = 20000" in sse
    assert "maybeAutoResumeInterruptedReact" in sse
    layout = (ROOT / "frontend/src/app/modules/layout-panels.js").read_text(encoding="utf-8")
    assert "fetch('/sessions/recover', { method: 'POST' })" in layout
    assert "observeServerOwnedReactRecovery(currentSessionId)" in layout
    refresh_row = sessions.split("async function refreshSingleSessionRow", 1)[1].split("let sessionListLoadEpoch", 1)[0]
    event_cache_set = sessions.split("const uiEventCountCache", 1)[1].split("increment(sessionId)", 1)[0]
    assert "maybeAutoResumeInterruptedReact(sessionId, sess)" in refresh_row
    assert "maybeAutoResumeInterruptedReact" not in event_cache_set
    assert "window.addEventListener('online'" in sse
    assert sse.index("setSessionRunState(submitSessionIdInitial, optimisticRunState)") < sse.index("processRewriteTruncateAsync(pendingRewrite)")
    assert sse.index("setSessionRunState(submitSessionIdInitial, optimisticRunState)") < sse.index("let preCount = await getUiEventCount")
    assert "optimisticNewSessionRun = optimisticRunState;" in sse
    assert "if (ac.signal.aborted) return;" in sse
    assert "optimisticRunState.submitted = true;" in sse
    assert "!(state.activeRun && state.activeRun.suppressFollowupButton)" in sse
    assert "readSseChunkWithIdleTimeout(reader, SSE_IDLE_TIMEOUT_MS)" in sse
    assert "parsed.type === 'sse_keepalive' || parsed.keepalive === true" in sse
    assert 'os.getenv("MYAGENT_ENABLE_STREAM_RECONNECT", "1")' in webui
    assert "CHAT_SSE_KEEPALIVE_SEC" in webui
    assert "'type': 'sse_keepalive'" in webui
    assert "function markRunFinalSeen(ctx)" in sse
    assert "function initRunFinalTracking(ctx)" in sse
    assert "if (ctx && ctx.seenFinal === true) return;" in sse
    assert "markRunFinalSeen(runCtx);" in sse
    assert "runCtx.terminalSeen = true;" in sse
    assert "Transport completion is not a run outcome" in sse
    assert "await ensureFinalVisibleAfterRunIfEnabled" in sse
    assert "function fetchLatestStoredFinalRecord" not in sse
    assert "var latestFinal = await fetchLatestStoredFinalRecord(sid);" not in sse
    assert "messages?limit=120" not in sse
    assert "function reconcileProjectedMessagesAfter" not in sse
    assert "projected-reconcile" not in sse
    assert "messages?after_index=" not in sse
    assert "function enqueueCurrentInputAsFollowup(options)" in sse
    assert "if (!options.pendingQuestion && !isMyAgentFeatureEnabled('followupRestart', false)) return false;" in sse
    assert "function dispatchComposerAction(allowStop)" in sse
    assert "function onComposerInputKeydown(e)" in sse
    assert "isInputMethodComposing(e)" in sse
    assert "async function syncFollowupQueueFromServer(sessionId)" in sse
    assert "async function fetchSteerStatus(sessionId, item)" in sse
    assert "async function recoverSteerForRestart(sessionId, item)" in sse
    assert "const sendPipelineLock = acquireSendPipelineLock(submitSessionIdInitial);" in sse
    assert "if (!sendPipelineLock) return;" in sse
    assert "releaseSendPipelineLock(sendPipelineLock);" in sse
    assert "formData.append('steer_id', String(options.steerId))" in sse
    # 普通状态刷新不会发送；自动续发只从 run 终止边界进入 drain。
    assert "function refreshPendingFollowupQueue(sessionId)" in sse
    assert "void sendFollowupNow(String(front.id), sid)" not in sse
    assert "function drainFollowupQueue(sessionId)" in sse
    assert "function withFollowupDispatch(sessionId, fn)" in sse
    assert "async function waitForSendPipelineIdle(sessionId, timeoutMs)" in sse
    assert "followupEnabled" in sessions
    assert "isMyAgentFeatureEnabled('followupRestart', false)" in sessions


def test_followups_auto_continue_only_after_run_end_and_sync():
    sse = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    enqueue = sse.split("function enqueueCurrentInputAsFollowup(options)", 1)[1].split(
        "function takeFollowupItem", 1
    )[0]
    end_run = sse.split("function endRunForClient", 1)[1].split(
        "async function readSseChunkWithIdleTimeout", 1
    )[0]
    consumed = sse.split("function removeConsumedFollowupSteer", 1)[1].split(
        "function isFollowupAutoDrainReady", 1
    )[0]
    drain = sse.split("function isFollowupAutoDrainReady", 1)[1].split(
        "function scheduleAcceptedFollowupWatch", 1
    )[0]
    # 入队绝不发送：入队只创建并持久化 pending，绝不触发发送。
    assert "sendFollowupNow" not in enqueue
    assert "scheduleFollowupQueueDrain" not in enqueue
    assert "setSendButtonState();" in enqueue
    assert "item.awaitingRunEnd = true;" in enqueue
    assert "queueComposerBehindPendingQuestion" in sse
    assert "enqueueCurrentInputAsFollowup({ pendingQuestion: true })" in sse
    # 上一轮结束后必须先完成服务端对账，再启动自动续发。
    assert "syncFollowupQueueFromServer(sid)" in end_run
    assert "Promise.resolve(followupSync).then" in end_run
    assert "auto-drain skipped" in end_run
    assert "getRunAbortReason(sid, ctx) !== 'user'" in end_run
    assert "scheduleFollowupQueueDrain(sid" in end_run
    # consumed 只唤醒同一套门禁；活跃 run 下不能绕过门禁顺手发下一条。
    assert "scheduleFollowupQueueDrain(sid, 0)" in consumed
    # 本地 run、服务端 stream、发送锁和 dispatcher 全部空闲才允许 drain。
    assert "!isSessionRunning(sid)" in drain
    assert "isSessionStreamStopSuppressed(sid)" in drain
    assert "!isServerStreamActive(sid)" in drain
    assert "!isSendPipelineLocked(sid)" in drain
    assert "!isFollowupDispatchBusy(sid)" in drain
    assert "pendingHumanQuestions(sid).length" in drain
    assert "var item = q[0];" in drain
    assert "sendFollowupNow(item.id, sid, { autoAfterRun: true })" in drain
    assert "sendQueuedFollowupAsChat(sid, item, itemId, options.autoDispatchEpoch)" in sse
    assert "followupManualDispatchEpochBySession[sid]" in sse
    assert "isFollowupAutoDispatchSuperseded(sid, dispatchOptions.autoDispatchEpoch)" in sse
    assert "recoverFollowupQueueDrainsFromSessionSnapshot" in sse
    assert "async function isSessionAutoResumePending(sessionId)" in sse
    assert "react_auto_resume" in sse
    assert "?recovery=true" not in sse
    assert "observeServerOwnedReactRecovery" in sse
    assert "scheduleFollowupQueueDrain(sid, 1000)" in sse
    assert ".finally(function ()" not in drain
    # 定时器按会话合并，避免 final/run_finished/finally 重复触发多个请求。
    assert "followupDrainTimers[sid]" in drain
    assert "clearTimeout(existing.timer)" in drain
    # 旧的第二套 draining 锁不得复活，自动/手动统一由 dispatcher 串行化。
    assert "followupQueueDraining" not in sse
    assert "function isFollowupDispatchBusy(sessionId)" in sse
    # 降级 /chat 前等待发送锁释放，避免静默返回导致追问丢失。
    assert "waitForSendPipelineIdle(sid, 4000)" in sse
    assert "function startFollowupChat(options)" in sse
    assert "options.onRunStarted" in sse
    assert "chatStarted = isSessionRunning(sid)" not in sse
    # 持久化所有非终态条目，提交期间刷新不丢消息。
    assert "status !== 'sent'" in sse
    # watcher 覆盖全部传输非终态，并与手动/自动发送共用 dispatcher。
    assert "['submitting', 'sending', 'accepted', 'restarting'].includes" in sse
    assert "withFollowupDispatch(sid, async function ()" in sse
    # 乐观行和实时 SSE 统一优先使用 client_id。
    assert "parsed.client_id || parsed.steer_id" in sse
    assert "parsed.steer_id || parsed.client_id" not in sse

    # Explicit Send now remains available independently on every pending row.
    assert "sendNow.addEventListener('click'" in sse
    assert "sendFollowupNow(String(item.id), sid, { manual: true })" in sse
    assert "cancelFollowupQueueDrain(sid)" in sse
    assert "sendNow.disabled = !!item.status ||" in sse
    assert "item.deferUntilRunEnd" in sse


def test_followup_supports_interrupt_and_append_modes():
    sse = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    events = (ROOT / "frontend/src/app/modules/event-dispatch.js").read_text(encoding="utf-8")
    loop = (ROOT / "app/agent_loop.py").read_text(encoding="utf-8")
    webui = (ROOT / "app/webui.py").read_text(encoding="utf-8")

    assert "function createFollowupModePicker(item, sessionId)" in sse
    assert "return item.steerMode === 'append' ? 'append' : 'interrupt';" in sse
    assert "item.steerMode = mode === 'append' ? 'append' : 'interrupt';" in sse
    assert "document.body.appendChild(menu);" in sse
    assert "followupQueueRenderSignature" in sse
    assert "panel.dataset.renderSignature === renderSignature" in sse
    assert "mode: steerMode === 'append' ? 'append' : 'interrupt'" in sse
    assert "appendPendingSteerToProcess(sid, item);" in sse
    assert "appendSteerProcessMessage(" in events
    assert "prepareSteerProcessBoundary(ctx, event.steer_mode || 'interrupt', steerOperationId);" in events
    assert "optimisticSteerRow.dataset.steerEventReserved = '1';" in sse
    assert "if (!reservedSteerIndex) streamEventIdx += 1;" in sse
    assert "if (!renderAsSteer)" in sse
    assert "prepareSteerProcessBoundary(runCtx, optimisticSteerMode, optimisticSteerOpId);" in sse
    assert '_STEER_MODES = {"interrupt", "append"}' in loop
    assert '_has_session_steers(sid, modes={"interrupt"})' in loop
    assert 'modes={"append", "interrupt"}' in loop
    assert loop.count("max_react_iter = max(max_react_iter, iter_count + 1)") == 2
    assert 'if str(item.get("mode") or steer_mode) == "append":' in webui


def test_model_profile_selector_fences_cross_session_responses():
    source = (ROOT / "frontend/src/app/modules/model-profiles.js").read_text(encoding="utf-8")

    assert "const modelProfilesRefreshPromises = Object.create(null);" in source
    assert "const modelProfileBusyBySession = Object.create(null);" in source
    assert "const requestEpoch = ++modelProfileSelectionEpoch;" in source
    assert "existing && existing.epoch === modelProfileSelectionEpoch" in source
    assert "sid !== String(currentSessionId || '') || requestEpoch !== modelProfileSelectionEpoch" in source
    assert "fetch('/sessions/' + encodeURIComponent(sid) + '/model_profile'" in source
    assert "if (sid !== String(currentSessionId || '')) return;" in source
    assert "selectContextTokens(sid)" in source
    assert "scheduleContextTokensAfterPaint(sid)" in source


def test_chat_input_supports_clipboard_files_and_images_as_paths():
    sources = [
        ROOT / "frontend/src/vendor/myagent_path_picker.js",
        ROOT / "app/templates/static/myagent_path_picker.js",
    ]
    for path in sources:
        source = path.read_text(encoding="utf-8")
        assert "function clipboardFilesFromEvent(ev)" in source
        assert "function clipboardHasUsableText(ev)" in source
        assert "textarea.addEventListener('paste'" in source
        assert "if (clipboardHasUsableText(ev)) return;" in source
        assert source.index("if (clipboardHasUsableText(ev)) return;") < source.index(
            "var files = clipboardFilesFromEvent(ev);"
        )
        assert "item.kind !== 'file'" in source
        assert "insertUploadedFiles(textarea, files, options)" in source
        assert "new XMLHttpRequest()" in source
        assert "xhr.upload.onprogress" in source
        assert "xhr.abort()" in source
        assert "MAX_CHAT_UPLOAD_FILE_BYTES = 100 * 1024 * 1024" in source
        assert "MAX_CHAT_UPLOAD_TOTAL_BYTES = 200 * 1024 * 1024" in source
        assert "textarea.dataset.fileUploadBusy = '1'" in source
        assert "class=\"chat-upload-cancel\"" in source
        assert "quotePickedPath(item.path || item.rel || item.name)" in source

    session_management = (ROOT / "frontend/src/app/modules/session-management.js").read_text(encoding="utf-8")
    sse = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    assert "function isChatFileUploadBusy()" in session_management
    assert "sendBtn.textContent = '上传中';" in session_management
    assert "sendBtn.disabled = true;" in session_management
    assert "isChatFileUploadBusy()) return;" in sse
    assert "if (isChatFileUploadBusy()) return false;" in sse
    assert "if (state.uploadBusy) return false;" in sse


def test_image_followup_waits_for_run_end_and_keeps_structured_attachments():
    sse = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    picker = (ROOT / "frontend/src/vendor/myagent_path_picker.js").read_text(encoding="utf-8")

    assert "attachments: Array.isArray(attachments) ? attachments.slice() : []" in sse
    assert "if (options.pendingQuestion || attachments.length)" in sse
    assert "attachments: Array.isArray(item.attachments) ? item.attachments : []" in sse
    assert "attachments: item.attachments || []" in sse
    assert "const rememberedAttachments = Array.isArray(options.attachments)" in sse
    assert "function addChatAttachments(textarea, attachments)" in picker


def test_branch_completion_does_not_hijack_a_later_session_switch():
    source = (ROOT / "frontend/src/app/modules/message-rendering.js").read_text(encoding="utf-8")

    assert "const sourceSwitchEpoch" in source
    assert "const sourceStillActive = currentSessionId === sourceSessionId" in source
    assert "sourceSwitchEpoch === switchSessionEpoch" in source
    assert "if (!sourceStillActive)" in source


def test_chat_busy_response_rolls_back_optimistic_message_into_queue():
    sse = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    busy = sse.split("if (response.status === 409)", 1)[1].split("streamEventIdx = await", 1)[0]

    assert "rollbackOptimisticUserEvent(runSessionId, preCount)" in busy
    assert "appendFollowupQueueItem(" in busy
    assert "scheduleActiveSessionReconnect(runSessionId" in busy
    assert "truncateMessageStateForSession(sid, before)" in sse
    assert "uiEventCountCache.updateFromServer(sid, before)" in sse


def test_frontend_final_reconcile_checks_local_store_before_bounded_server_recovery():
    sse = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    final_block = re.search(
        r"async function ensureFinalVisibleAfterRun\(sessionId, ctx, opts\) \{(?P<body>.*?)\n\}",
        sse,
        re.S,
    )
    assert final_block, "final visibility reconcile must stay explicit"
    body = final_block.group("body")

    assert "findStoredFinalAfterUser(sid, lastUserIdx)" in body
    assert "renderFinalRecordIfMissing(sid, ctx, stream, storedFinal, lastUserIdx)" in body
    assert "/messages?turns=1&event_budget=128" in body
    assert "stillOwnsView()" in body


def test_removed_high_risk_dom_stream_shims_do_not_return():
    bundle_sources = [
        ROOT / "frontend/src/app/modules/sse-handling.js",
        ROOT / "frontend/src/app/modules/session-scroll-history.js",
        ROOT / "frontend/src/app/modules/toc-todo.js",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in bundle_sources)

    for symbol in [
        "ensureDomContextForSession",
        "resolveRenderStreamForSession",
        "shouldIgnoreMainProcessAfterFinal",
        "tocRebuildPendingAfterLoad",
    ]:
        assert symbol not in combined


def test_frontend_session_load_lets_snapshot_own_toc_build():
    sessions = (ROOT / "frontend/src/app/modules/session-management.js").read_text(encoding="utf-8")
    toc = (ROOT / "frontend/src/app/modules/toc-todo.js").read_text(encoding="utf-8")

    assert "function startTocForSessionLoad(sessionId)" in toc
    assert "startTocForSessionLoad(sessionId)" in sessions
    assert "const tocAlreadyStarted = opts.useSnapshot === false" in sessions
    assert "tocAlreadyStarted: tocAlreadyStarted" in sessions
    assert "tocAlreadyStarted: true" not in sessions
    assert "if (!opts.tocAlreadyStarted) rebuildToc();" in sessions
    assert "/history_snapshot?turns=" in sessions
    assert "setTocTurnsForSession(sessionId, snapshot.user_turns)" in sessions
    assert "sessionStore.applyActiveRunForSession(" in sessions
    assert "snapshot.active_run || (__snapActive ?" in sessions
    assert "snapshot.todo_plan" not in sessions
    assert "renderLoadedTodoPlanForSession" not in sessions
    assert "opts.useSnapshot === false && typeof startTocForSessionLoad === 'function'" in sessions


def test_frontend_session_load_logs_open_session_timing_from_snapshot():
    sessions = (ROOT / "frontend/src/app/modules/session-management.js").read_text(encoding="utf-8")

    assert "let snapshotTiming = null;" in sessions
    assert "snapshotTiming = snapshot.timing && typeof snapshot.timing === 'object'" in sessions
    assert "function logOpenSessionTiming(sessionId, data)" in sessions
    assert "'open_session_timing session=%s source=%s total=%sms events=%s backend_total=%sms read_page=%sms count=%sms user_turns=%sms context_tokens=%sms'" in sessions
    assert "logOpenSessionTiming(sessionId, {" in sessions


def test_frontend_session_switch_async_work_is_session_scoped():
    sessions = (ROOT / "frontend/src/app/modules/session-management.js").read_text(encoding="utf-8")
    subagent_sync = (ROOT / "frontend/src/app/state/subagent-sync.js").read_text(encoding="utf-8")

    assert "if (loadToken !== messageLoadEpoch || sessionId !== currentSessionId) return false;" in sessions
    assert "rebuildToc({ localOnly: true });" in sessions
    assert sessions.count("if (switchToken === switchSessionEpoch && sessionId === currentSessionId)") >= 3
    assert "subagentTreeRefreshInflightBySession" in subagent_sync
    assert "subagentTreeRefreshQueuedBySession" in subagent_sync
    assert "if (!sessionId || sessionId !== currentSessionId) return;" in subagent_sync
    assert "if (seq !== subagentPanelRefreshSeq || sessionId !== currentSessionId) return;" in subagent_sync


def test_running_pending_turn_keeps_a_pulsing_completed_result_indicator():
    sessions = (ROOT / "frontend/src/app/modules/session-management.js").read_text(encoding="utf-8")
    sse = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    styles = (ROOT / "frontend/src/styles/app.css").read_text(encoding="utf-8")
    webui = (ROOT / "app/webui.py").read_text(encoding="utf-8")
    loop = (ROOT / "app/agent_loop.py").read_text(encoding="utf-8")

    indicator = sessions.split("function applySessionItemIndicators", 1)[1].split(
        "function syncSessionListIndicatorClasses", 1
    )[0]
    assert "if (running)" in indicator
    assert "itemDiv.classList.add(failed ? 'is-unread-failed' : 'is-unread-result')" in indicator
    assert "已有任务完成，当前任务仍在处理" in indicator
    assert "回复已生成，正在收尾" in indicator
    assert ".session-item.is-generating.is-unread-result" in styles
    assert ".session-item.is-finalizing .session-name::before" in styles
    assert "formData.append('preserve_unread_result', 'true')" in sse
    assert "preserve_unread_result: bool = Form(False)" in webui
    assert "preserve_unread_result=preserve_unread_result" in webui
    assert 'ui_metadata={"preserve_unread_result": True} if preserve_unread_result else None' in loop


def test_frontend_background_followup_return_does_not_touch_active_composer():
    sse = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    skills = (ROOT / "frontend/src/app/modules/skill-picker.js").read_text(encoding="utf-8")

    fn = re.search(
        r"function returnFollowupToInput\(sid, item\) \{(?P<body>.*?)\n\}",
        sse,
        re.S,
    )
    assert fn
    body = fn.group("body")
    background = body.split("if (sid !== currentSessionId) {", 1)[1].split("\n    }", 1)[0]
    assert "persistInputDraft(sid, nextDraft);" in background
    assert "messageInput.value" not in background
    assert "messageInput.focus()" not in background
    assert "window.setSelectedSkillsForSession" in background
    assert "function setSelectedSkillsForSession(sessionId, skills)" in skills


def test_selected_skill_display_marker_is_not_duplicated_on_reload():
    import webui

    suffix = "\n\nActivated Skill: documents, pdf"
    assert webui._build_ui_message_with_selected_skills("整理文件", ["documents", "pdf"]) == "整理文件" + suffix
    assert webui._build_ui_message_with_selected_skills("整理文件" + suffix, ["documents", "pdf"]) == "整理文件" + suffix

    sse = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    assert "function buildSelectedSkillsDisplayMessage(rawMessage, selectedSkills)" in sse
    assert "message.endsWith(suffix) ? message : message + suffix" in sse
    assert "formData.append('ui_message', uiBaseMessage);" in sse
    assert "formData.append('attachments', JSON.stringify(attachmentsForRun));" in sse


def test_frontend_send_and_reattach_reuse_event_count_cache():
    sessions = (ROOT / "frontend/src/app/modules/session-management.js").read_text(encoding="utf-8")
    scroll = (ROOT / "frontend/src/app/modules/session-scroll-history.js").read_text(encoding="utf-8")
    sse = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    subagent_sync = (ROOT / "frontend/src/app/state/subagent-sync.js").read_text(encoding="utf-8")
    subagent_store = (ROOT / "frontend/src/app/state/subagent-store.js").read_text(encoding="utf-8")

    assert "has(sessionId)" in sessions
    assert "async function getUiEventCount(sessionId, opts)" in scroll
    assert "opts.preferCache" in scroll
    assert "uiEventCountCache.has(sid)" in scroll
    assert "uiEventCountCache.updateFromServer(sid, count)" in scroll
    assert "getUiEventCount(runSessionId, { preferCache: true })" in sse
    assert "uiEventCountCache.updateFromServer(runSessionId, preCount + 1)" in sse
    assert "getUiEventCount(submitSessionId).then" not in sse
    assert "/messages/count" not in subagent_sync
    assert "node.event_count" in subagent_store
    assert "messages?after_index=" in subagent_sync


def test_frontend_running_session_switch_restores_local_stream_without_snapshot_reload():
    scroll = (ROOT / "frontend/src/app/modules/session-scroll-history.js").read_text(encoding="utf-8")
    sessions = (ROOT / "frontend/src/app/modules/session-management.js").read_text(encoding="utf-8")

    assert "function isCompleteLocalRunStream(sessionId, stream)" in scroll
    assert "run.ctx.stream === stream" in scroll
    assert "stream.dataset.partialBackgroundRun !== '1'" in scroll
    assert "certifyLocalRun: true" in scroll
    assert "(st.dataset.sessionLoadOk !== '1' && !completeLocalRun)" in scroll
    fast_restore = sessions.split("if (!opts.forceReload && (", 1)[1]
    fast_restore = fast_restore.split("const vs = getVisibleChatStream();", 1)[0]
    assert "hideLoading();" in fast_restore
    assert "loadSessionMessages(" not in fast_restore


def test_frontend_llm_stream_seq_increments_do_not_split_chunks():
    rendering = (ROOT / "frontend/src/app/modules/message-rendering.js").read_text(encoding="utf-8")

    assert "seq < l.llmDeltaLastSeq" in rendering
    assert "seq !== l.llmDeltaLastSeq" not in rendering


def test_frontend_markdown_dependencies_do_not_block_on_public_cdn():
    html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    entry = (ROOT / "frontend/src/app/index.js").read_text(encoding="utf-8")
    rendering = (ROOT / "frontend/src/app/modules/message-rendering.js").read_text(encoding="utf-8")
    vite_config = (ROOT / "frontend/vite.config.js").read_text(encoding="utf-8")
    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))

    assert "cdn.jsdelivr.net/npm/marked" not in html
    assert "cdn.jsdelivr.net/npm/mermaid" not in html
    assert package["dependencies"]["marked"] == "15.0.12"
    assert package["dependencies"]["mermaid"] == "10.9.6"
    assert "import { marked } from 'marked';" in entry
    assert "globalThis.marked = marked;" in entry
    assert "import('mermaid')" not in entry
    assert "'/assets/vendor/mermaid.min.js'" in entry
    assert "document.createElement('script')" in entry
    assert "globalThis.loadMyAgentMermaid" in entry
    assert "myagent-mermaid-vendor" in vite_config
    assert "node_modules', 'mermaid', 'dist', 'mermaid.min.js'" in vite_config
    assert "type: 'asset'" in vite_config
    assert "await globalThis.loadMyAgentMermaid()" in rendering
    assert "data-mermaid-loading" in rendering
    assert "new IntersectionObserver" in rendering
    assert "markdown-fallback" in rendering


def test_stream_deltas_have_stable_dedupe_keys():
    agent_loop = (ROOT / "app/agent_loop.py").read_text(encoding="utf-8")
    rendering = (ROOT / "frontend/src/app/modules/message-rendering.js").read_text(encoding="utf-8")
    scroll = (ROOT / "frontend/src/app/modules/session-scroll-history.js").read_text(encoding="utf-8")

    assert "llm_delta_seq = 0" in agent_loop
    assert "tool_delta_seq = 0" in agent_loop
    assert '"delta_seq": llm_delta_seq' in agent_loop
    assert '"delta_seq": tool_delta_seq' in agent_loop
    assert "function deltaDedupeKey(ctx, parsed, scope)" in rendering
    assert "hasSeenStreamDelta(ctx, ev, 'llm_' + part)" in rendering
    assert "hasSeenStreamDelta(ctx, parsed, 'tool_call_delta')" in rendering
    assert "_seenStreamDeltaKeys: new Set()" in scroll
    assert "reactGeneration: 0" in scroll
    assert "data-react-generation" in rendering


def test_tool_pending_switches_generated_draft_to_executing():
    agent_loop = (ROOT / "app/agent_loop.py").read_text(encoding="utf-8")
    sse = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    rendering = (ROOT / "frontend/src/app/modules/message-rendering.js").read_text(encoding="utf-8")

    assert "await _emit_tool_pending_sse(" in agent_loop
    assert "tool_descriptor.emit_pending" in agent_loop
    wrapper = agent_loop.split("async def execute_one(tool_call):", 1)[1].split(
        "# ---------- 2.6 调用 LLM ----------", 1
    )[0]
    resolve_at = wrapper.index("tool_descriptor = tool_registry.resolve(tool_name)")
    first_use_at = wrapper.index("if tool_descriptor is not None")
    assert resolve_at < first_use_at
    host_tools = (ROOT / "app/builtin_host_tools.py").read_text(encoding="utf-8")
    assert "emit_pending=False" in host_tools
    assert "if (parsed.type === 'tool_pending')" in sse
    assert "appendToolPendingRow(runCtx, parsed, runSessionId);" in sse
    assert "row.getAttribute('data-tool-pending') === '1'" in rendering
    assert "draftChunk.classList.remove('is-streaming')" in rendering


def test_prompt_allows_multi_tool_generation_independent_of_execution_mode():
    prompt = (ROOT / "app/prompt.md").read_text(encoding="utf-8")

    assert "这与工具最终是并行还是串行执行无关" in prompt
    assert "依照 `tool_calls` 原始顺序串行" in prompt
    assert "不得猜测未知参数" in prompt
    assert "已被压缩或可能被压缩的历史信息" in prompt
    assert "当前会话的 session 文件夹下查询 `events.jsonl`" in prompt


def test_ui_translation_does_not_mutate_conversation_content():
    i18n = (ROOT / "frontend/src/app/modules/i18n.js").read_text(encoding="utf-8")

    assert "UI_I18N_CONTENT_SELECTOR" in i18n
    for selector in (
        ".message",
        ".feed-chunk-scroller",
        ".followup-queue-text",
        ".session-name",
        ".human-question-text",
        ".human-option-label",
        ".human-option-description",
        ".human-approval-message",
        ".subagent-card-summary",
        ".subagent-output-content",
        ".skill-picker-option-desc",
    ):
        assert selector in i18n
    assert "el.closest(UI_I18N_CONTENT_SELECTOR)" in i18n

    remote_i18n = (ROOT / "app/templates/static/remote_i18n.js").read_text(encoding="utf-8")
    remote_markup = (ROOT / "app/templates/remote_control.html").read_text(encoding="utf-8")
    assert "contentSelector" in remote_i18n
    assert "el.closest(contentSelector)" in remote_i18n
    assert remote_markup.count('setAttribute("data-i18n-skip","true")') >= 4


def test_followup_pending_queue_supports_manual_drag_reorder():
    sse = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    css = (ROOT / "frontend/src/styles/app.css").read_text(encoding="utf-8")

    assert "function moveFollowupQueueItem" in sse
    assert "followupDragState" in sse
    assert "dataTransfer.effectAllowed = 'move'" in sse
    assert "classList.add(after ? 'is-drag-over-after' : 'is-drag-over-before')" in sse
    assert "entry.order = idx" in sse
    assert "row.dataset.reorderable = item.status ? 'false' : 'true'" in sse
    assert "target.dataset.reorderable !== 'true'" in sse
    assert "pendingIndexes.forEach" in sse
    assert "startFollowupTouchDrag" in sse
    assert "ev.pointerType === 'touch' || ev.pointerType === 'pen'" in sse
    assert "setPointerCapture" in sse
    assert "document.elementFromPoint" in sse
    assert ".followup-queue-drag" in css
    assert ".followup-queue-row.is-dragging" in css
    assert ".followup-queue-row.is-drag-over-before" in css
    assert "touch-action: none" in css


def test_ui_cache_warmup_delay_runs_inside_background_worker():
    source = (ROOT / "app/webui.py").read_text(encoding="utf-8")
    warmup = source.split("def _warm_ui_caches()", 1)[1].split(
        "def initialize_ui_attention_notifications()", 1
    )[0]

    worker_start = warmup.index("def _run()")
    delay = warmup.index("_warm_time.sleep(2.0)")
    thread_start = warmup.index("threading.Thread")
    assert worker_start < delay < thread_start


def test_streamed_llm_commits_are_sse_fallbacks_without_repersisting():
    agent_loop = (ROOT / "app/agent_loop.py").read_text(encoding="utf-8")
    subagent_events = (ROOT / "app/agent_subagent_events.py").read_text(encoding="utf-8")

    streamed_block = re.search(
        r"if streamed_this_call:(?P<body>.*?)else:",
        agent_loop,
        re.S,
    )
    assert streamed_block, "streamed LLM branch must be explicit"
    body = streamed_block.group("body")

    assert 'session_manager.append_ui_event(' in body
    assert '"type": "llm_reasoning"' in body
    assert '"type": "llm_response"' in body
    assert '"_skip_persist": True' in body
    assert '"live_commit": True' in body
    assert "emit=emit" in body
    assert 'ev.get("_skip_persist")' in subagent_events


def test_frontend_llm_delta_recovers_missing_scrollers():
    rendering = (ROOT / "frontend/src/app/modules/message-rendering.js").read_text(encoding="utf-8")

    assert "l.llmStreamReasoningScroller && !l.llmStreamReasoningScroller.isConnected" in rendering
    assert "recoveredReasoning = findExistingLlmFeedRow" in rendering
    assert "createProcessFeedRow(ctx, 'llm-reasoning'" in rendering
    assert "l.llmStreamResponseScroller && !l.llmStreamResponseScroller.isConnected" in rendering
    assert "recoveredResponse = findExistingLlmFeedRow" in rendering
    assert "createProcessFeedRow(ctx, 'llm-response'" in rendering


def test_live_history_owner_is_never_replaced_by_target_window():
    scroll = (ROOT / "frontend/src/app/modules/session-scroll-history.js").read_text(encoding="utf-8")
    sessions = (ROOT / "frontend/src/app/modules/session-management.js").read_text(encoding="utf-8")
    sse = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    target_window = scroll.split("async function loadHistoryWindowAroundEventIndex", 1)[1].split(
        "const SESSION_STREAM_CACHE_LIMIT", 1
    )[0]
    jump = scroll.split("async function scrollToUserTurnOrLoadOlder", 1)[1]

    assert "sessionHasLiveHistoryOwner(sid)" in target_window
    assert target_window.index("sessionHasLiveHistoryOwner(sid)") < target_window.index("await fetch(url)")
    assert "await refreshSessionLiveHistoryOwner(sid)" in target_window
    assert target_window.index("await fetch(url)") < target_window.index(
        "await refreshSessionLiveHistoryOwner(sid)"
    )
    assert "has_newer: data.has_newer == null ? rangeEnd < total : !!data.has_newer" in target_window
    assert "has_newer: !!paging.has_newer" in scroll
    assert "has_newer: !!raw.has_newer" in scroll
    assert "has_newer: raw.has_newer == null ? pageRangeEnd < pageTotal : !!raw.has_newer" in sessions
    assert sse.count("ensureLatestHistoryTailForLiveAppend(") >= 3
    assert "liveHistoryOwner" in jump
    assert "!liveHistoryOwner" in jump
    assert "loadOlderHistoryChunk({ keepTocStable: true, turns: 50 })" in jump
    assert "if (!getFeedItemText(el).trim()) el.remove();" in scroll


def test_runtime_v2_todo_plan_events_are_persistable():
    host_tools = (ROOT / "plugins/session-todo/host.py").read_text(encoding="utf-8")

    assert '"type": "extension_state_changed"' in host_tools
    assert '"plugin_id": "session-todo"' in host_tools
    assert "write_todo_extension" in host_tools


def test_frontend_suppressed_toc_rebuild_does_not_clear_started_toc():
    toc = (ROOT / "frontend/src/app/modules/toc-todo.js").read_text(encoding="utf-8")
    suppress_block = re.search(
        r"if\s*\(\s*suppressTocDuringSessionLoad\s*\)\s*\{(?P<body>.*?)\}",
        toc,
        re.S,
    )
    assert suppress_block, "rebuildToc must keep an explicit suppress guard"
    body = suppress_block.group("body")

    assert "clearTocForSessionLoad" not in body
    assert re.search(r"\breturn\s*;", body), "suppressed TOC rebuild should be a no-op"


def test_frontend_toc_supports_snapshot_turns_and_skips_empty_active_update():
    toc = (ROOT / "frontend/src/app/modules/toc-todo.js").read_text(encoding="utf-8")

    assert "function setTocTurnsForSession(sessionId, turns)" in toc
    assert "Array.isArray(options.turns)" in toc
    assert "tocTurnsCacheBySession.set(sid, turns)" in toc
    assert "if (!list || !list.querySelector('a[data-event-index]')) return;" in toc


def test_frontend_session_scoped_token_and_count_guards():
    sessions = (ROOT / "frontend/src/app/modules/session-management.js").read_text(encoding="utf-8")
    scroll = (ROOT / "frontend/src/app/modules/session-scroll-history.js").read_text(encoding="utf-8")
    sse = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")

    assert "if (typeof applyContextTokenLabelForCurrentSession === 'function') applyContextTokenLabelForCurrentSession();" in sessions
    assert "if (sid === currentSessionId) applyContextTokenLabelForCurrentSession();" in scroll
    assert "isFresh(sessionId, maxAgeMs)" in sessions
    assert "uiEventCountCache.isFresh(sid, opts.maxAgeMs)" in scroll
    assert "let preCount = await getUiEventCount(submitSessionId, {" in sse
    assert "signal: ac.signal" in sse
    assert "timeoutMs: 5000" in sse
    assert "parsed.type === 'context_tokens' && eventSessionId === currentSessionId" in sse
    assert "parsed.type === 'cache_stats' && eventSessionId === currentSessionId" in sse


def test_frontend_new_session_is_local_until_first_send_and_coalesces_materialization():
    sessions = (ROOT / "frontend/src/app/modules/session-management.js").read_text(encoding="utf-8")
    sse = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    shared = (ROOT / "frontend/src/app/modules/shared-state-and-dialogs.js").read_text(encoding="utf-8")
    scroll = (ROOT / "frontend/src/app/modules/session-scroll-history.js").read_text(encoding="utf-8")
    skills = (ROOT / "frontend/src/app/modules/skill-picker.js").read_text(encoding="utf-8")
    rendering = (ROOT / "frontend/src/app/modules/message-rendering.js").read_text(encoding="utf-8")
    layout = (ROOT / "frontend/src/app/modules/layout-panels.js").read_text(encoding="utf-8")
    draft_body = sessions.split("async function createNewSession()", 1)[1].split(
        "async function materializeNewSession()", 1
    )[0]
    materialize_body = sessions.split("async function materializeNewSession()", 1)[1]

    assert "let materializeNewSessionQueue = null;" in sessions
    assert "await fetch('/sessions'" not in draft_body
    assert "setCurrentSessionState(null);" in draft_body
    assert "setWelcome();" in draft_body
    assert "restoreInputDraft(null);" in draft_body
    assert "if (materializeNewSessionQueue) return materializeNewSessionQueue;" in materialize_body
    assert "const response = await fetch('/sessions', {" in materialize_body
    assert "body: JSON.stringify(createOptions)" in materialize_body
    assert "createOptions.model_profile_id = modelProfileId;" in materialize_body
    assert "createOptions.permission_mode = permissionMode;" in materialize_body
    assert "applyNewSessionOptionsToLegacyBackend(sessionId, createOptions, data)" in materialize_body
    assert "sessionStore.protectFromSnapshots(session);" in materialize_body
    assert "restoreInputDraft(sessionId)" not in materialize_body
    assert "submitSessionId = await materializeNewSession();" in sse
    assert "const NEW_SESSION_DRAFT_KEY = '__new_session_draft__';" in shared
    assert "sessionId ? String(sessionId) : NEW_SESSION_DRAFT_KEY" in scroll
    assert "sessionId ? String(sessionId) : NEW_SESSION_DRAFT_KEY" in skills
    assert "persistInputDraft(currentSessionId, messageInput.value);" in rendering
    assert "lastSessionId === NEW_SESSION_DRAFT_KEY" in layout


def test_frontend_new_session_model_and_permission_controls_use_draft_state():
    models = (ROOT / "frontend/src/app/modules/model-profiles.js").read_text(encoding="utf-8")
    permissions = (ROOT / "frontend/src/app/modules/permissions.js").read_text(encoding="utf-8")
    sessions = (ROOT / "frontend/src/app/modules/session-management.js").read_text(encoding="utf-8")

    assert "function setNewSessionModelProfileId(profileId)" in models
    assert "if (!sid) {" in models
    assert "setNewSessionModelProfileId(selectedProfileId);" in models
    assert "function setNewSessionPermissionMode(mode)" in permissions
    assert "trigger.disabled = !controlsEnabled || permissionModeBusy;" in permissions
    assert "if (!currentSessionId) {" in permissions
    assert "setNewSessionPermissionMode(mode);" in permissions
    assert "commitNewSessionModelProfile(sessionId);" in sessions
    assert "commitNewSessionPermissionMode(data.permission_status || null);" in sessions


def test_frontend_llm_stream_rows_are_upserted_across_process_group_rebuilds():
    rendering = (ROOT / "frontend/src/app/modules/message-rendering.js").read_text(encoding="utf-8")

    assert "function findExistingLlmFeedRow(ctx, logType, reactIter, opts)" in rendering
    assert "findExistingLlmFeedRow(ctx, logType, ri)" in rendering
    assert "findExistingLlmFeedRow(ctx, 'llm-response'" in rendering
    assert "findExistingLlmFeedRow(ctx, 'llm-reasoning'" in rendering
    assert "roots.push(ctx.stream)" in rendering
    assert "function removeDuplicateLlmFeedRows(ctx, keepRow, logType, reactIter)" in rendering


def test_frontend_session_restore_distinguishes_loaded_cached_and_running_sessions():
    rendering = (ROOT / "frontend/src/app/modules/message-rendering.js").read_text(encoding="utf-8")
    scroll = (ROOT / "frontend/src/app/modules/session-scroll-history.js").read_text(encoding="utf-8")
    sessions = (ROOT / "frontend/src/app/modules/session-management.js").read_text(encoding="utf-8")

    assert "function scrollToBottom(opts)" in rendering
    assert "chatContainer.scrollTo({ top: chatContainer.scrollHeight, behavior: 'smooth' })" in rendering
    assert "scrollToBottom({ smooth: mode === 'saved-or-bottom' });" not in rendering
    assert "mode === 'saved-smooth-or-bottom'" in rendering
    assert "instant: !smoothRestore" in rendering
    assert "viewportOffset: savedAnchorOffset" in rendering
    assert "var scrollBehavior = opts.instant ? 'auto' : 'smooth';" in scroll
    assert "wrap.scrollIntoView({ behavior: scrollBehavior, block: 'start' });" in scroll
    assert "function restoreCachedSessionScrollPosition(sessionId)" in scroll
    assert "setScrollTopImmediate(chatContainer, Number(saved))" in scroll
    assert "var restoredRunningStream = false;" in sessions
    assert "var sessionIsRunningNow = !!(" in sessions
    assert "isServerStreamActive(sessionId)" in sessions
    assert "restoredFromCache && !sessionHadUnreadResult && !sessionIsRunningNow" in sessions
    assert "sessionIsRunningNow && typeof scrollCurrentRunningProcessToBottom" in sessions
    assert "var allAggs = stream.querySelectorAll('.process-aggregate');" in scroll
    assert "loadSessionMessages(sessionId, sessionHadUnreadResult ? 'bottom' : 'smooth-bottom'" in sessions
    assert "scrollToBottom({ smooth: mode === 'smooth-bottom' });" in rendering
    assert "sessionHadUnreadResult = !!(" in sessions


def test_frontend_loaded_session_defers_layout_refresh_until_smooth_bottom_finishes():
    rendering = (ROOT / "frontend/src/app/modules/message-rendering.js").read_text(encoding="utf-8")
    scroll = (ROOT / "frontend/src/app/modules/session-scroll-history.js").read_text(encoding="utf-8")
    sessions = (ROOT / "frontend/src/app/modules/session-management.js").read_text(encoding="utf-8")

    assert "function bindExistingLogInteractions(root)" in rendering
    assert "function finalizeExistingLogLayout(root)" in rendering
    assert "function isHistorySmoothScrollActive()" in rendering
    assert "function waitForHistoryImageLayout(sessionId, mode, root)" in rendering
    assert "root.querySelectorAll('.message img')" in rendering
    assert "img.loading = 'eager';" in rendering
    assert "img.setAttribute('data-history-image-fallback', '1');" in rendering
    assert "}, 2400);" in rendering
    assert "function prepareWorkspaceImageLayout(root)" in (ROOT / "frontend/src/app/modules/workspace-media.js").read_text(encoding="utf-8")
    assert "chatContainer.addEventListener('scrollend', onScrollEnd);" in rendering
    assert "retargetCount < 1" in rendering
    assert "if (typeof isHistorySmoothScrollActive === 'function' && isHistorySmoothScrollActive()) return;" in scroll
    assert "&& !(typeof isHistorySmoothScrollActive === 'function' && isHistorySmoothScrollActive())" in scroll
    image_wait_at = sessions.index("await waitForHistoryImageLayout(")
    metadata_wait_at = sessions.index("await prepareWorkspaceImageLayout(")
    hydrate_at = sessions.index("finishHistoryHydration();", image_wait_at)
    interactions_at = sessions.index("bindExistingLogInteractions();", hydrate_at)
    smooth_at = sessions.index("applyChatScrollAfterHistoryLoad(sessionId, historyScrollBehavior);", interactions_at)
    wait_at = sessions.index("await waitForChatScrollAfterHistoryLoad(sessionId, historyScrollBehavior);", smooth_at)
    layout_at = sessions.index("finalizeExistingLogLayout();", wait_at)
    assert metadata_wait_at < image_wait_at < hydrate_at < interactions_at < smooth_at < wait_at < layout_at
    assert "historyScrollBehavior === 'smooth-bottom' && initialSmoothReachedBottom" in sessions
    assert "historyScrollBehavior = 'bottom';" in sessions


def test_frontend_completed_background_stream_remains_reusable_for_green_dot_restore():
    sse = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")

    assert "runCtx.terminalSeen = true;" in sse
    assert "runCtx.streamCompletedSuccessfully = parsed.type === 'run_finished';" in sse
    assert "const reusableCompletedCache = !!(" in sse
    assert "runCtx.seenFinal === true" in sse
    assert "el.dataset.partialBackgroundRun !== '1'" in sse
    assert "el.dataset.cacheSessionId === String(runSessionId)" in sse
    assert "el.dataset.sessionLoadOk = '1';" in sse
    assert "discardCachedSessionStream(runSessionId);" in sse
    assert "if (el && el.parentNode) el.remove();" in sse


def test_frontend_older_history_auto_load_preserves_viewport():
    scroll = (ROOT / "frontend/src/app/modules/session-scroll-history.js").read_text(encoding="utf-8")
    sse = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")

    assert "function maybeAutoLoadOlderHistory()" in scroll
    assert "chatContainer.scrollTop > HISTORY_AUTO_LOAD_TOP_PX" in scroll
    assert "maybeAutoLoadOlderHistory();" in sse
    assert "prependScrollTop = cc.scrollTop" in scroll
    assert "prependScrollHeight = cc.scrollHeight" in scroll
    assert "prependScrollTop + Math.max(0, cc.scrollHeight - prependScrollHeight)" in scroll


def test_frontend_run_state_cleanup_is_run_id_scoped():
    actions = (ROOT / "frontend/src/app/state/session-actions.js").read_text(encoding="utf-8")
    sse = (ROOT / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    sessions = (ROOT / "frontend/src/app/modules/session-management.js").read_text(encoding="utf-8")

    assert "function clearSessionRunStateIfMatch(sessionId, runId)" in actions
    assert "String(run.runId || '') === expected" in actions
    assert "function endRunForClient(sessionId, ctx, opts)" in sse
    assert "runCtx.runId = clientRunId;" in sse
    assert "clearSessionRunStateIfMatch(runSessionId, clientRunId)" in sse
    assert "clearSessionRunStateIfMatch(sid, opts.runId || (ctx && ctx.runId))" in sse
    assert "run && (run.reattached || staleSubmittedStream || run.transportClosed)" in sessions
    assert "run.submitted && run.ctx && run.ctx.streamConsuming" in sessions
    assert "abortSessionRun(sid, 'reconcile-finished')" in sessions


class _FakeJsonRequest:
    def __init__(self, payload: dict):
        self._payload = payload

    async def json(self):
        return dict(self._payload)


class _FakeSessionManagerForSteer:
    def __init__(self):
        self.interrupts: list[tuple[str, str]] = []

    def request_interrupt(self, session_id: str, reason: str = ""):
        self.interrupts.append((session_id, reason))


def test_followup_restart_enabled_prefers_native_steer(monkeypatch):
    import webui

    fake_manager = _FakeSessionManagerForSteer()
    monkeypatch.setenv("MYAGENT_ENABLE_FOLLOWUP_RESTART", "1")
    monkeypatch.setattr(webui, "session_manager", fake_manager)
    monkeypatch.setattr(webui, "_is_session_stream_active", lambda sid: True)
    monkeypatch.setattr(
        webui,
        "enqueue_session_steer",
        lambda sid, message, client_id="", **_kwargs: {
            "ok": True,
            "item": {"content": message, "client_id": client_id},
        },
    )
    monkeypatch.setattr(webui, "abort_session_steer_run", lambda sid, reason="": True)
    monkeypatch.setattr(
        webui,
        "_interrupt_runtime_v2_active_runs",
        lambda sid, reason="": (_ for _ in ()).throw(AssertionError("native steer must not restart")),
    )

    response = asyncio.run(webui.post_session_steer(
        "s1",
        _FakeJsonRequest({"message": "continue now", "client_id": "cid-1", "mode": "interrupt"}),
    ))
    payload = json.loads(response.body.decode("utf-8"))

    assert payload["ok"] is True
    assert payload["restart"] is False
    assert payload["aborted"] is True
    assert payload["item"]["content"] == "continue now"
    assert payload["item"]["client_id"] == "cid-1"
    assert fake_manager.interrupts == []


def test_append_steer_is_accepted_without_aborting_active_run(monkeypatch):
    import webui

    fake_manager = _FakeSessionManagerForSteer()
    captured = {}
    monkeypatch.delenv("MYAGENT_STEER_MODE", raising=False)
    monkeypatch.setattr(webui, "session_manager", fake_manager)
    monkeypatch.setattr(webui, "_is_session_stream_active", lambda sid: True)

    def enqueue(sid, message, client_id="", **kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "item": {
                "id": "append-steer",
                "content": message,
                "client_id": client_id,
                "state": "queued",
                "mode": kwargs.get("mode"),
            },
        }

    monkeypatch.setattr(webui, "enqueue_session_steer", enqueue)
    monkeypatch.setattr(
        webui,
        "abort_session_steer_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("append mode must not abort")),
    )

    response = asyncio.run(webui.post_session_steer(
        "s-append",
        _FakeJsonRequest({
            "message": "use this on the next round",
            "client_id": "append-client",
        }),
    ))
    payload = json.loads(response.body.decode("utf-8"))

    assert payload["ok"] is True
    assert payload["restart"] is False
    assert payload["aborted"] is False
    assert payload["item"]["mode"] == "append"
    assert captured["mode"] == "append"
    assert fake_manager.interrupts == []


def test_followup_restart_keeps_same_durable_steer_until_replacement_claim(monkeypatch):
    import webui

    fake_manager = _FakeSessionManagerForSteer()
    monkeypatch.setenv("MYAGENT_ENABLE_FOLLOWUP_RESTART", "1")
    monkeypatch.setattr(webui, "session_manager", fake_manager)
    monkeypatch.setattr(webui, "_is_session_stream_active", lambda sid: True)
    monkeypatch.setattr(
        webui,
        "enqueue_session_steer",
        lambda sid, message, client_id="", **kwargs: {
            "ok": True,
            "item": {"id": "steer-stable", "content": message, "client_id": client_id, "state": "queued"},
        },
    )
    monkeypatch.setattr(webui, "abort_session_steer_run", lambda sid, reason="": False)
    monkeypatch.setattr(webui, "_interrupt_runtime_v2_active_runs", lambda sid, reason="": ["run-old"])
    transitions = []

    def transition(sid, steer_id, from_states, to_state, **updates):
        transitions.append((sid, steer_id, set(from_states), to_state, dict(updates)))
        return {
            "ok": True,
            "item": {
                "id": steer_id,
                "content": "continue now",
                "client_id": "cid-stable",
                "state": to_state,
                **updates,
            },
        }

    monkeypatch.setattr(webui, "transition_session_steer", transition)

    response = asyncio.run(webui.post_session_steer(
        "s-fallback",
        _FakeJsonRequest({"message": "continue now", "client_id": "cid-stable", "mode": "interrupt"}),
    ))
    payload = json.loads(response.body.decode("utf-8"))

    assert payload["ok"] is True
    assert payload["restart"] is True
    assert payload["item"]["id"] == "steer-stable"
    assert payload["item"]["state"] == "restarting"
    assert transitions[0][1:4] == ("steer-stable", {"queued", "interrupting"}, "restarting")


def test_followup_http_retry_after_run_finished_returns_consumed_operation(monkeypatch):
    import webui

    monkeypatch.setattr(webui, "_is_session_stream_active", lambda sid: False)
    monkeypatch.setattr(
        webui,
        "get_session_steer",
        lambda sid, steer_id="", client_id="": {
            "ok": True,
            "item": {"id": "steer-once", "client_id": client_id, "state": "consumed"},
        },
    )
    monkeypatch.setattr(
        webui,
        "enqueue_session_steer",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("retry must not enqueue again")),
    )

    response = asyncio.run(webui.post_session_steer(
        "s-finished",
        _FakeJsonRequest({"message": "same followup", "client_id": "cid-once"}),
    ))
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["deduplicated"] is True
    assert payload["item"]["state"] == "consumed"
    assert payload["restart"] is False


def test_subagent_card_falls_back_to_saved_output_when_messages_fail():
    loader = (ROOT / "frontend/src/app/state/subagent-loader.js").read_text(encoding="utf-8")
    renderers = (ROOT / "frontend/src/app/state/subagent-renderers.js").read_text(encoding="utf-8")

    assert "loadSubagentOutputAsFinalEvent" in loader
    assert "card.dataset.outputFile === '1'" in loader
    assert "card.dataset.virtualTask === '1'" in loader
    assert "data-virtual-task" in renderers
