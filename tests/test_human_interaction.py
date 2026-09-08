import asyncio
from pathlib import Path

import pytest

from app.human_interaction.service import (
    HumanInteractionService,
    HumanInteractionValidationError,
    ask_user_enabled,
)
from app.runtime_v2.ui_projection import RuntimeUiProjection


@pytest.mark.parametrize(
    "decision",
    ["allow_external_workspace", "allow_external_workspace_once"],
)
def test_approval_gate_returns_raw_decision_for_two_step_flow(decision):
    """wait_tool_ui_approval_after_emit(return_decision=True) returns the raw
    decision token so the Agent Loop can re-prompt the command after the
    workspace permission is granted (two independent authorization axes)."""
    from app.tool_approval_gate import (
        new_approval_id,
        resolve_tool_approval_decision,
        wait_tool_ui_approval_after_emit,
    )

    async def flow():
        sid = "gate-decision-test"
        aid = new_approval_id()

        async def emit():
            return None

        waiter = asyncio.ensure_future(
            wait_tool_ui_approval_after_emit(
                sid, aid, emit, return_decision=True
            )
        )
        await asyncio.sleep(0.05)
        resolve_tool_approval_decision(sid, aid, decision)
        return await waiter

    result = asyncio.run(flow())
    assert result == decision


def test_approval_review_context_lives_only_while_approval_is_pending():
    from app.security.models import CapabilityRequest
    from app.tool_approval_gate import (
        get_live_approval_review_context,
        resolve_tool_approval,
        wait_tool_ui_approval_after_emit,
    )

    request = CapabilityRequest.create(
        action="process.exec",
        resource="git status",
        effect="workspace_write",
    )

    async def flow():
        waiter = asyncio.create_task(
            wait_tool_ui_approval_after_emit(
                "review-session",
                "review-approval",
                lambda: asyncio.sleep(0),
                review_context={"request": request, "user_intent": "inspect repository"},
            )
        )
        await asyncio.sleep(0.05)
        context = get_live_approval_review_context(
            "review-session", "review-approval"
        )
        assert context == {"request": request, "user_intent": "inspect repository"}
        assert resolve_tool_approval(
            "review-session", "review-approval", False
        ) is True
        await waiter
        assert get_live_approval_review_context(
            "review-session", "review-approval"
        ) is None

    asyncio.run(flow())


def test_approval_gate_returns_rejection_reason_to_waiting_agent():
    from app.tool_approval_gate import (
        resolve_tool_approval,
        wait_tool_ui_approval_after_emit,
    )

    async def flow():
        waiter = asyncio.create_task(
            wait_tool_ui_approval_after_emit(
                "reason-session",
                "reason-approval",
                lambda: asyncio.sleep(0),
                return_resolution=True,
            )
        )
        await asyncio.sleep(0.05)
        assert resolve_tool_approval(
            "reason-session",
            "reason-approval",
            False,
            "目标目录不正确，请先检查路径。",
        ) is True
        return await waiter

    assert asyncio.run(flow()) == {
        "approved": False,
        "decision": "deny",
        "rejection_reason": "目标目录不正确，请先检查路径。",
    }


@pytest.fixture(autouse=True)
def _enable_ask_user_for_interaction_tests(monkeypatch):
    monkeypatch.setenv("ASK_USER_ENABLED", "1")


def _service(tmp_path):
    return HumanInteractionService(tmp_path, path_resolver=lambda sid: tmp_path / sid)


def _questions():
    return {
        "questions": [
            {
                "header": "实现范围",
                "question": "这次按哪个范围实现？",
                "options": [
                    {"label": "完整闭环（推荐）", "description": "包含持久化、恢复和前端。"},
                    {"label": "仅后端", "description": "只实现服务端能力。"},
                ],
                "multi_select": False,
            }
        ],
        "metadata": {"source": "test"},
    }


def test_question_request_resolve_and_rebuild(tmp_path):
    service = _service(tmp_path)
    created = service.create_question(
        "session-1", _questions(), run_id="run-1", tool_call_id="call-1", interaction_id="ask-1"
    )

    assert created["status"] == "pending"
    assert created["questions"][0]["question_id"] == "q1"
    assert created["questions"][0]["options"][0]["option_id"] == "q1o1"
    assert service.pending_counts("session-1") == {"questions": 1, "approvals": 0, "total": 1}

    resolved = service.resolve_question(
        "session-1",
        "ask-1",
        {"answers": [{"question_id": "q1", "selected_option_ids": ["q1o1"]}]},
        resolver={"channel": "test"},
    )
    duplicate = service.resolve_question(
        "session-1",
        "ask-1",
        {"answers": [{"question_id": "q1", "selected_option_ids": ["q1o2"]}]},
    )

    assert resolved["status"] == "resolved"
    assert resolved["answers"][0]["selected_labels"] == ["完整闭环（推荐）"]
    assert duplicate["answers"] == resolved["answers"]
    assert service.pending_counts("session-1")["total"] == 0

    rebuilt = service.mirror.projector.project(service.mirror.event_log.read_all("session-1"))
    assert rebuilt["interactions"]["ask-1"]["status"] == "resolved"
    assert rebuilt["pending_interactions"] == []

    projection = RuntimeUiProjection(tmp_path, path_resolver=lambda sid: tmp_path / sid)
    event_types = [row["type"] for row in projection.read_ui_events_fast("session-1")]
    assert event_types[-2:] == ["interaction_requested", "interaction_resolved"]


def test_question_validation_rejects_other_and_bad_answers(tmp_path):
    service = _service(tmp_path)
    invalid = _questions()
    invalid["questions"][0]["options"][1]["label"] = "Other"
    with pytest.raises(HumanInteractionValidationError, match="supplied by the UI"):
        service.create_question("session-1", invalid)

    service.create_question("session-1", _questions(), interaction_id="ask-1")
    with pytest.raises(HumanInteractionValidationError, match="unknown option"):
        service.resolve_question(
            "session-1",
            "ask-1",
            {"answers": [{"question_id": "q1", "selected_option_ids": ["missing"]}]},
        )


def test_question_can_be_skipped_without_cancelling_interaction(tmp_path):
    service = _service(tmp_path)
    request = _questions()
    request["questions"].append(
        {
            "header": "后续范围",
            "question": "后续怎么处理？",
            "options": [
                {"label": "继续", "description": "继续处理。"},
                {"label": "停止", "description": "停止处理。"},
            ],
            "multi_select": False,
        }
    )
    service.create_question("session-1", request, interaction_id="ask-1")

    resolved = service.resolve_question(
        "session-1",
        "ask-1",
        {
            "answers": [
                {"question_id": "q1", "skipped": True},
                {"question_id": "q2", "selected_option_ids": ["q2o1"]},
            ]
        },
    )

    assert resolved["status"] == "resolved"
    assert resolved["answers"][0] == {
        "question_id": "q1",
        "selected_option_ids": [],
        "selected_labels": [],
        "other_text": None,
        "notes": None,
        "skipped": True,
    }
    assert resolved["answers"][1]["selected_labels"] == ["继续"]
    assert resolved["answers"][1]["skipped"] is False


def test_skipped_question_rejects_answer_content(tmp_path):
    service = _service(tmp_path)
    service.create_question("session-1", _questions(), interaction_id="ask-1")

    with pytest.raises(HumanInteractionValidationError, match="cannot include an answer when skipped"):
        service.resolve_question(
            "session-1",
            "ask-1",
            {"answers": [{"question_id": "q1", "selected_option_ids": ["q1o1"], "skipped": True}]},
        )


def test_question_header_allows_fifty_characters(tmp_path):
    service = _service(tmp_path)
    request = _questions()
    request["questions"][0]["header"] = "x" * 50
    created = service.create_question("session-1", request)
    assert created["questions"][0]["header"] == "x" * 50

    request["questions"][0]["header"] = "x" * 51
    with pytest.raises(HumanInteractionValidationError, match="exceeds 50 characters"):
        service.create_question("session-2", request)


@pytest.mark.parametrize("value", ["0", "false", "NO", "Off"])
def test_ask_user_switch_blocks_new_questions_but_not_approvals(tmp_path, monkeypatch, value):
    monkeypatch.setenv("ASK_USER_ENABLED", value)
    service = _service(tmp_path)

    assert ask_user_enabled() is False
    with pytest.raises(HumanInteractionValidationError, match="ASK_USER_ENABLED"):
        service.create_question("session-1", _questions())

    approval = service.create_approval("session-1", approval_id="approval-1")
    assert approval["status"] == "pending"


def test_ask_user_switch_defaults_enabled(monkeypatch):
    monkeypatch.delenv("ASK_USER_ENABLED", raising=False)
    assert ask_user_enabled() is True


def test_agent_loop_filters_ask_user_tool_when_switch_is_disabled():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app/builtin_host_tools.py").read_text(encoding="utf-8")
    loop = (root / "app/agent_loop.py").read_text(encoding="utf-8")
    assert "enabled=ask_user_enabled" in source
    assert "ask_user_enabled" not in loop


def test_approval_is_durable_and_terminal_transition_is_idempotent(tmp_path):
    service = _service(tmp_path)
    created = service.create_approval(
        "session-1",
        approval_id="approval-1",
        metadata={"tool": "run_shell", "title": "允许执行？"},
        tool_call_id="call-1",
    )
    assert created["status"] == "pending"
    assert service.pending_counts("session-1") == {"questions": 0, "approvals": 1, "total": 1}

    resolved = service.resolve_approval("session-1", "approval-1", "allow_once")
    duplicate = service.resolve_approval("session-1", "approval-1", "deny")
    assert resolved["decision"] == "allow_once"
    assert duplicate["decision"] == "allow_once"
    assert service.pending_counts("session-1")["total"] == 0


def test_denied_approval_persists_rejection_reason(tmp_path):
    service = _service(tmp_path)
    service.create_approval(
        "session-1",
        approval_id="approval-denied",
        metadata={"tool": "run_shell"},
    )

    resolved = service.resolve_approval(
        "session-1",
        "approval-denied",
        "deny",
        rejection_reason="命令会覆盖仍需保留的文件。",
    )

    assert resolved["decision"] == "deny"
    assert resolved["rejection_reason"] == "命令会覆盖仍需保留的文件。"


def test_rejection_reason_is_rejected_for_allow_decision(tmp_path):
    service = _service(tmp_path)
    service.create_approval("session-1", approval_id="approval-allowed")

    with pytest.raises(
        HumanInteractionValidationError,
        match="only valid when decision is deny",
    ):
        service.resolve_approval(
            "session-1",
            "approval-allowed",
            "allow_once",
            rejection_reason="不应出现在允许决定中",
        )


def test_dangerous_approval_rejects_session_and_always_grants(tmp_path):
    service = _service(tmp_path)
    created = service.create_approval(
        "session-1",
        approval_id="danger-1",
        metadata={
            "tool": "run_shell",
            "title": "危险命令，需要确认",
            "force_approval": True,
            "approval_level": "danger",
        },
    )
    assert created["force_approval"] is True

    with pytest.raises(HumanInteractionValidationError, match="allow_once or deny"):
        service.resolve_approval("session-1", "danger-1", "allow_session")
    with pytest.raises(HumanInteractionValidationError, match="allow_once or deny"):
        service.resolve_approval("session-1", "danger-1", "allow_always")
    with pytest.raises(HumanInteractionValidationError, match="allow_once or deny"):
        service.resolve_approval(
            "session-1", "danger-1", "allow_external_workspace_once"
        )
    resolved = service.resolve_approval("session-1", "danger-1", "allow_once")
    assert resolved["decision"] == "allow_once"


def test_non_dangerous_approval_still_supports_session_grant(tmp_path):
    service = _service(tmp_path)
    service.create_approval(
        "session-1",
        approval_id="normal-1",
        metadata={"tool": "write_file", "force_approval": False},
    )
    resolved = service.resolve_approval("session-1", "normal-1", "allow_session")
    assert resolved["decision"] == "allow_session"


def test_once_only_approval_rejects_session_and_durable_grants(tmp_path):
    service = _service(tmp_path)
    service.create_approval(
        "session-1",
        approval_id="egress-once",
        metadata={
            "tool": "run_shell",
            "force_approval": False,
            "allow_always_available": False,
            "allow_session_available": False,
        },
    )
    with pytest.raises(HumanInteractionValidationError):
        service.resolve_approval("session-1", "egress-once", "allow_session")
    with pytest.raises(HumanInteractionValidationError):
        service.resolve_approval("session-1", "egress-once", "allow_always")
    resolved = service.resolve_approval("session-1", "egress-once", "allow_once")
    assert resolved["decision"] == "allow_once"


def test_workspace_approval_supports_one_time_scope_without_approving_tool(tmp_path):
    service = _service(tmp_path)
    service.create_approval(
        "session-1",
        approval_id="workspace-1",
        metadata={
            "tool": "write_file",
            "force_approval": False,
            "external_workspace_grantable": True,
        },
    )
    resolved = service.resolve_approval(
        "session-1", "workspace-1", "allow_external_workspace_once"
    )
    assert resolved["decision"] == "allow_external_workspace_once"


def test_resolved_approval_keeps_rule_metadata_for_rule_creation(tmp_path):
    """allow_always resolves through the same record that carries
    rule_action/rule_pattern so webui can persist the durable rule,
    including for auto-review override cards."""
    service = _service(tmp_path)
    service.create_approval(
        "session-1",
        approval_id="rule-1",
        metadata={
            "tool": "run_shell",
            "force_approval": False,
            "allow_always_available": True,
            "rule_action": "process.exec",
            "rule_pattern": "git push:*",
        },
    )
    resolved = service.resolve_approval("session-1", "rule-1", "allow_always")
    assert resolved["decision"] == "allow_always"
    assert resolved["rule_action"] == "process.exec"
    assert resolved["rule_pattern"] == "git push:*"


def test_tool_approval_wait_defaults_to_no_timeout(monkeypatch):
    from app import tool_approval_gate

    monkeypatch.delenv("TOOL_UI_APPROVAL_WAIT_SEC", raising=False)
    assert tool_approval_gate.approval_wait_seconds() is None
    for value in ("0", "0.0", "", "abc"):
        monkeypatch.setenv("TOOL_UI_APPROVAL_WAIT_SEC", value)
        assert tool_approval_gate.approval_wait_seconds() is None, value
    monkeypatch.setenv("TOOL_UI_APPROVAL_WAIT_SEC", "120")
    assert tool_approval_gate.approval_wait_seconds() == 120.0


def test_approval_record_has_no_expiry_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("TOOL_UI_APPROVAL_WAIT_SEC", raising=False)
    service = _service(tmp_path)
    created = service.create_approval("session-1", approval_id="approval-no-expiry")
    assert created["status"] == "pending"
    assert created["expires_at"] is None


def test_approval_record_expires_when_timeout_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("TOOL_UI_APPROVAL_WAIT_SEC", "60")
    service = _service(tmp_path)
    created = service.create_approval("session-1", approval_id="approval-expiry")
    assert created["expires_at"] is not None


def test_frontend_human_interaction_contract_is_wired():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    module = (root / "frontend/src/app/modules/human-interactions.js").read_text(encoding="utf-8")
    shell = (root / "frontend/src/shell-body.html").read_text(encoding="utf-8")
    index_html = (root / "frontend/index.html").read_text(encoding="utf-8")
    dispatch = (root / "frontend/src/app/modules/event-dispatch.js").read_text(encoding="utf-8")
    rendering = (root / "frontend/src/app/modules/message-rendering.js").read_text(encoding="utf-8")
    assert "refreshHumanInteractions" in module
    assert "sessionStorage" in module
    assert "selected_option_ids" in module
    assert "syncHumanInteractionSessionSummary(sid);" in module
    assert 'id="human-interaction-banner"' in shell
    # frontend/index.html is Vite's real entrypoint. Checking only the shell
    # fragment can pass while the banner is absent from the served page.
    assert 'id="human-interaction-banner"' in index_html
    assert "renderHumanInteractionEvent" in dispatch
    assert "function humanInteractionToolSlot" in module
    assert "attachHumanInteractionCardsForToolCall" in module
    assert "attachHumanInteractionCardsForToolCall" in rendering
    assert "attachAllHumanInteractionCards" in module
    assert "本次允许" in module
    assert "替我分析" in module
    assert "analyzeHumanApproval(card)" in module
    assert "requestHumanApprovalDenial(card)" in module
    assert "payload.rejection_reason = String(rejectionReason || '').trim()" in module
    assert "请填写拒绝原因。" in module
    assert "拒绝原因会随审批结果返回给 Agent。" in module
    assert "/analyze'" in module
    assert "以上仅为分析建议，审批仍由你决定。" in module
    assert "human-always-btn', '始终允许'" in module
    assert "当前任务内自动允许完全相同的请求" in module
    assert "durableRuleAvailable ? 'allow_always' : 'allow_session'" in module
    assert "allow_external_workspace_once" in module
    assert "human-approval-group" not in module
    assert "if (!forced && record.allow_session_available !== false)" in module
    approval_renderer = module.split("function createHumanApprovalCard", 1)[1].split(
        "async function analyzeHumanApproval", 1
    )[0]
    assert approval_renderer.count("humanElement('button'") == 4
    assert "本任务内允许相同请求" not in approval_renderer
    assert "始终允许此类操作" not in approval_renderer
    # Tool rows fold as a whole (command + approval card). They stay expanded
    # until the tool result arrives, then auto-fold to a compact preview;
    # manual fold takes priority, clicking the command text toggles the same
    # row fold, and the banner focus expands both a collapsed row and the
    # outer process block.
    assert "feed-row-collapse" in rendering
    assert "row.classList.toggle('is-collapsed')" in rendering
    assert "collapsedRow.classList.remove('is-collapsed')" in module
    assert "row.dataset.manualToggle = '1'" in rendering
    assert "function handleToolRowChunkClick" in rendering
    assert "row.classList.contains('feed--tool')" in rendering
    assert "collapsedAgg.classList.remove('is-collapsed')" in module
    assert "function autoCollapseToolRowAfterResult" in rendering
    assert "autoCollapseToolRowAfterResult(row)" in rendering
    assert "function toolCallHasPendingApproval" not in module
    assert "function autoExpandToolRow" not in module
    assert "function collapseAutoExpandedToolRow" not in module
    # History replay must route tool_call events through the same upsert path
    # as live SSE so the tool row carries data-tool-call-id and approval cards
    # can be re-anchored after a page refresh.
    assert "upsertToolCallResult(ctx, event, runSessionId)" in dispatch
    assert "appendLog(ctx, event.raw_content, 'tool-call'" not in dispatch
    upsert_fn = rendering.split("function upsertToolCallResult", 1)[1].split(
        "function trimSurroundingBlankLines", 1
    )[0]
    assert "createProcessFeedRow(ctx, 'tool-call', text, so, runSessionId, tid)" in upsert_fn
    assert "attachHumanInteractionCardsForToolCall(ctx && ctx.stream, tid)" in upsert_fn
    css = (root / "frontend/src/styles/app.css").read_text(encoding="utf-8")
    assert ".human-approval-actions > .human-analyze-btn { flex: 0 0 auto; }" in css
    assert ".human-approval-decisions" in css
    assert ".human-approval-actions > .human-analyze-btn { flex: 0 0 auto; }" in css
    slot_card = css.split(".human-interaction-tool-slot .human-interaction-card {", 1)[1].split("}", 1)[0]
    assert "width: min(720px, calc(100% - 1rem));" in slot_card
    assert "margin: 0.65rem auto;" in slot_card
    assert "width: 100%; margin: 0.65rem 0 0;" not in slot_card
    assert ".feed-row-collapse {" in css
    assert ".feed-item.is-collapsed .human-interaction-tool-slot { display: none; }" in css
    assert ".feed-item.is-collapsed .feed-chunk {" in css
    tool_scroller = css.split(".feed-item.feed--tool .feed-chunk-scroller {", 1)[1].split("}", 1)[0]
    assert "max-height: none;" in tool_scroller
    assert "overflow: visible;" in tool_scroller
    assert ".feed-item.feed--tool .feed-chunk { cursor: default; }" not in css
    badge_update = module.split("function updateHumanInteractionSessionBadge", 1)[1].split(
        "function updateAllHumanInteractionSessionBadges", 1
    )[0]
    assert "if (count <= 0)" in badge_update
    assert "if (badge) badge.remove();" in badge_update
    assert "hasQuestions && hasApprovals" in badge_update
    assert "String(count)" in badge_update
    assert "function showHumanQuestionReview" not in module
    assert "function validateHumanQuestionPane" in module
    assert "question.multi_select || !input.checked || qIndex >= questions.length - 1" not in module
    assert "human-next-btn" not in module
    assert "back.disabled = next === 0" in module
    assert "back.textContent = '上一题'" in module
    assert "human-confirm-btn', '确认'" in module
    assert "confirmBtn.textContent = allComplete ? '提交答案' : '确认'" in module
    assert "confirmBtn.classList.toggle('is-ready', allComplete)" in module
    assert "var allComplete = panes.every(isHumanQuestionPaneComplete)" in module
    assert "human-submit-btn" not in module
    assert "function nextIncompleteHumanQuestionIndex" in module
    assert "confirmCurrentHumanQuestion(card)" in module
    assert "human-skip-btn', '不回答'" in module
    assert "pane.dataset.skipped = '1'" in module
    assert "setHumanQuestionStep(card, nextIncompleteHumanQuestionIndex(Array.from(panes), current))" in module
    assert "else void submitHumanQuestion(card)" not in module
    assert "skipped: skipped" in module
    assert "cancelHumanQuestion" not in module
    assert "human-question-review" not in module
    keydown = module.split("card.addEventListener('keydown'", 1)[1].split("return card;", 1)[0]
    assert "if (allHumanQuestionsComplete(card)) void submitHumanQuestion(card)" in keydown
    assert "else confirmCurrentHumanQuestion(card)" in keydown
    assert "Ctrl/Cmd + Enter 提交答案" in module
    submit_validation = module.split("async function submitHumanQuestion", 1)[1].split(
        "function resumeRecoveredHumanInteractionStream", 1
    )[0]
    assert "if (collected.invalidPane)" in submit_validation
    assert "setHumanQuestionStep(card, panes.indexOf(collected.invalidPane))" in submit_validation
    assert "aria-busy" in module
    assert "confirmAndCancelPendingHumanQuestionsForMessage" not in module
    assert "confirmAndCancelPendingHumanQuestionsForHistoryMutation" in module
    sse = (root / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")
    assert "queueComposerBehindPendingQuestion" in sse
    assert "enqueueCurrentInputAsFollowup({ pendingQuestion: true })" in sse
    assert "submitComposerWithPendingQuestionGuard" not in sse
    settings = (root / "frontend/src/app/modules/settings.js").read_text(encoding="utf-8")
    webui = (root / "app/webui.py").read_text(encoding="utf-8")
    assert 'id="settings-ask-user-on"' not in shell
    assert 'id="settings-ask-user-on"' not in index_html
    assert "saveAskUserFeature" not in settings
    assert '"/api/features/ask-user"' in webui
    assert 'approvals/{approval_id}/analyze' in webui
    assert '"recommendation": "allow" if review.approved else "deny"' in webui


def test_analyze_approval_returns_advice_without_resolving(monkeypatch):
    import json
    from types import SimpleNamespace

    import human_interaction
    import security.reviewer
    import tool_approval_gate
    import webui
    from security import CapabilityRequest

    request = CapabilityRequest.create(
        action="process.exec",
        resource="git status",
        effect="workspace_write",
    )
    record = {
        "approval_id": "approval-analysis",
        "status": "pending",
        "decision": None,
    }

    class Service:
        def get(self, session_id, approval_id, *, kind):
            assert (session_id, approval_id, kind) == (
                "session-analysis",
                "approval-analysis",
                "approval",
            )
            return dict(record)

    calls = []

    async def fake_review(
        review_request,
        *,
        user_intent,
        session_id="",
        review_context=None,
    ):
        calls.append((review_request, user_intent, session_id, review_context))
        return SimpleNamespace(
            approved=True,
            risk="low",
            reason="【命令风险】Read-only repository inspection.\n【命令目的】Show repository state.",
            risk_analysis="Read-only repository inspection.",
            command_purpose="Show repository state.",
            available=True,
        )

    monkeypatch.setattr(
        human_interaction, "get_human_interaction_service", lambda: Service()
    )
    monkeypatch.setattr(
        tool_approval_gate,
        "get_live_approval_review_context",
        lambda *_args: {
            "request": request,
            "user_intent": "inspect repository",
            "review_context": {
                "initial_user_question": "inspect repository",
                "user_followups": ["include ignored files"],
                "assistant_context": [],
                "tool_arguments": {"command": "git status --ignored"},
            },
        },
    )
    monkeypatch.setattr(security.reviewer, "review_request", fake_review)

    response = asyncio.run(
        webui.analyze_session_approval("session-analysis", "approval-analysis")
    )
    payload = json.loads(response.body)

    assert payload == {
        "ok": True,
        "analysis": {
            "recommendation": "allow",
            "risk": "low",
            "reason": "【命令风险】Read-only repository inspection.\n【命令目的】Show repository state.",
            "risk_analysis": "Read-only repository inspection.",
            "command_purpose": "Show repository state.",
            "available": True,
        },
    }
    assert calls == [(
        request,
        "inspect repository",
        "session-analysis",
        {
            "initial_user_question": "inspect repository",
            "user_followups": ["include ignored files"],
            "assistant_context": [],
            "tool_arguments": {"command": "git status --ignored"},
        },
    )]
    assert record == {
        "approval_id": "approval-analysis",
        "status": "pending",
        "decision": None,
    }


@pytest.mark.parametrize(
    ("decision", "expected_global_grant"),
    [
        ("allow_external_workspace", True),
        ("allow_external_workspace_once", False),
    ],
)
def test_workspace_scope_resolution_reprompts_tool_without_approving_it(
    monkeypatch, decision, expected_global_grant
):
    import json

    import human_interaction
    import security
    import tool_approval_gate
    import webui

    class Request:
        headers = {}

        async def json(self):
            return {"decision": decision}

    class Service:
        def resolve_approval(
            self, session_id, approval_id, received_decision, *, resolver
        ):
            assert (session_id, approval_id, received_decision) == (
                "workspace-session",
                "workspace-approval",
                decision,
            )
            assert resolver["channel"] == "webui"
            return {
                "approval_id": approval_id,
                "status": "resolved",
                "decision": decision,
                "security_request_digest": "sha256:test",
            }

    raw_decisions = []
    global_grants = []

    monkeypatch.setattr(
        human_interaction, "get_human_interaction_service", lambda: Service()
    )
    monkeypatch.setattr(tool_approval_gate, "has_live_approval_waiter", lambda *_: True)
    monkeypatch.setattr(
        tool_approval_gate,
        "resolve_tool_approval_decision",
        lambda session_id, approval_id, value: raw_decisions.append(
            (session_id, approval_id, value)
        )
        or True,
    )
    monkeypatch.setattr(
        security,
        "update_security_settings",
        lambda **values: global_grants.append(values) or values,
    )

    async def ignore_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(webui, "publish_session_event", ignore_event)

    response = asyncio.run(
        webui.resolve_session_approval(
            "workspace-session", "workspace-approval", Request()
        )
    )
    payload = json.loads(response.body)

    assert payload["ok"] is True
    assert raw_decisions == [
        ("workspace-session", "workspace-approval", decision)
    ]
    assert bool(global_grants) is expected_global_grant
    if global_grants:
        assert global_grants == [{"allow_external_workspace_ops": True}]


def test_denial_resolution_returns_reason_to_agent_waiter(monkeypatch):
    import json

    import human_interaction
    import tool_approval_gate
    import webui

    class Request:
        headers = {}

        async def json(self):
            return {
                "decision": "deny",
                "rejection_reason": "请改用只读命令。",
            }

    class Service:
        def resolve_approval(
            self,
            session_id,
            approval_id,
            decision,
            *,
            resolver,
            rejection_reason,
        ):
            assert (session_id, approval_id, decision) == (
                "deny-session",
                "deny-approval",
                "deny",
            )
            assert rejection_reason == "请改用只读命令。"
            return {
                "approval_id": approval_id,
                "status": "resolved",
                "decision": "deny",
                "rejection_reason": rejection_reason,
                "security_request_digest": "sha256:test",
            }

    resolutions = []
    monkeypatch.setattr(
        human_interaction,
        "get_human_interaction_service",
        lambda: Service(),
    )
    monkeypatch.setattr(
        tool_approval_gate,
        "has_live_approval_waiter",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        tool_approval_gate,
        "resolve_tool_approval",
        lambda session_id, approval_id, approved, reason="": resolutions.append(
            (session_id, approval_id, approved, reason)
        )
        or True,
    )

    async def ignore_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(webui, "publish_session_event", ignore_event)

    response = asyncio.run(
        webui.resolve_session_approval(
            "deny-session",
            "deny-approval",
            Request(),
        )
    )
    payload = json.loads(response.body)

    assert payload["ok"] is True
    assert payload["approval"]["rejection_reason"] == "请改用只读命令。"
    assert resolutions == [
        (
            "deny-session",
            "deny-approval",
            False,
            "请改用只读命令。",
        )
    ]


def test_tool_pending_is_emitted_before_approval_dialog():
    """The tool row must exist before the approval card renders so the card
    anchors inside the row instead of jumping from the bottom of the stream."""

    root = Path(__file__).resolve().parents[1]
    source = (root / "app/agent_loop.py").read_text(encoding="utf-8")
    core = source.split("async def _execute_one_core", 1)[1]
    pending_call = core.index("await _emit_tool_pending_sse(")
    approval_wait = core.index("wait_tool_ui_approval_after_emit(")
    assert pending_call < approval_wait
    pre_approval = core[: core.index("if sec_decision.outcome == DecisionOutcome.ASK or hook_approval_spec:")]
    assert "tool_descriptor.emit_pending" in pre_approval
    host_tools = (root / "app/builtin_host_tools.py").read_text(encoding="utf-8")
    assert '"context_manage",\n            _invoke_context_manage,\n            emit_pending=False' in host_tools
    assert 'tool_name not in ("context_manage", "ask_user")' not in pre_approval
    # The old post-approval emit was removed in favor of the pre-approval row.
    assert "Arguments are closed and any required approval has completed" not in source


def test_stale_approval_is_not_resolved_without_a_live_waiter(tmp_path):
    from app import tool_approval_gate

    assert tool_approval_gate.has_live_approval_waiter("missing-session", "missing-approval") is False
    assert tool_approval_gate.resolve_tool_approval("missing-session", "missing-approval", True) is False


def test_approval_card_is_not_emitted_when_durable_persistence_fails(monkeypatch):
    import human_interaction
    from app import tool_approval_gate

    class _BrokenService:
        def create_approval(self, *_args, **_kwargs):
            raise OSError("approval store is unavailable")

    emitted = []

    async def emit_card():
        emitted.append(True)

    monkeypatch.setattr(human_interaction, "get_human_interaction_service", lambda: _BrokenService())

    with pytest.raises(tool_approval_gate.ApprovalPersistenceError, match="could not be saved"):
        asyncio.run(
            tool_approval_gate.wait_tool_ui_approval_after_emit(
                "session-persist-failure",
                "approval-persist-failure",
                emit_card,
                metadata={"_durable": True, "tool": "run_shell"},
            )
        )

    assert emitted == []
    assert not tool_approval_gate.has_live_approval_waiter(
        "session-persist-failure", "approval-persist-failure"
    )


def test_pending_approval_refresh_restores_card_badge_and_banner_contract(tmp_path, monkeypatch):
    service = _service(tmp_path)
    service.create_approval(
        "session-refresh",
        approval_id="approval-refresh",
        metadata={"tool": "run_shell", "title": "Approval required"},
    )

    # Reconstructing the service simulates a browser/server refresh: the
    # approval must come from the durable event log, not an in-memory waiter.
    restored = _service(tmp_path)
    assert service._pending_counts_path("session-refresh").is_file()
    with monkeypatch.context() as context:
        context.setattr(
            restored,
            "_snapshot",
            lambda _sid: (_ for _ in ()).throw(
                AssertionError("pending count index must avoid the Runtime snapshot")
            ),
        )
        assert restored.pending_counts("session-refresh") == {
            "questions": 0,
            "approvals": 1,
            "total": 1,
        }
    pending = restored.list("session-refresh", kind="approval", status="pending")
    assert [row["approval_id"] for row in pending] == ["approval-refresh"]

    root = Path(__file__).resolve().parents[1]
    layout = (root / "frontend/src/app/modules/layout-panels.js").read_text(encoding="utf-8")
    restore_call = "await refreshHumanInteractions(targetSession, { render: false });"
    switch_call = "if (targetSession) await switchSession(targetSession);"
    assert layout.index(restore_call) < layout.index(switch_call)

    interactions = (root / "frontend/src/app/modules/human-interactions.js").read_text(encoding="utf-8")
    refresh_body = interactions.split("async function refreshHumanInteractions", 1)[1]
    assert "syncHumanInteractionSessionSummary(sid);" in refresh_body
    assert "renderPendingHumanInteractions(sid);" in refresh_body
    render_body = interactions.split("function renderPendingHumanInteractions", 1)[1].split(
        "async function refreshHumanInteractions", 1
    )[0]
    assert "renderHumanInteractionRecord(record, sid, stream)" in render_body
    assert "updateHumanInteractionBanner(sid);" in render_body


def test_pending_question_switch_and_history_mutation_frontend_contract():
    root = Path(__file__).resolve().parents[1]
    interactions = (root / "frontend/src/app/modules/human-interactions.js").read_text(encoding="utf-8")
    sessions = (root / "frontend/src/app/modules/session-management.js").read_text(encoding="utf-8")
    rendering = (root / "frontend/src/app/modules/message-rendering.js").read_text(encoding="utf-8")
    sse = (root / "frontend/src/app/modules/sse-handling.js").read_text(encoding="utf-8")

    assert "function ensurePendingQuestionToolRow" in interactions
    assert "appendToolPendingRow(ctx" in interactions
    assert "ensurePendingQuestionToolRow(ctx, record, sid);" in interactions
    assert "refreshEpoch !== state.refreshEpoch" in interactions
    assert "resumeRecoveredHumanInteractionStream" in interactions
    assert "recovery_scheduled" in interactions
    assert "afterIndex: Math.max(0, Number(afterIndex) || 0)" in interactions
    assert "!sessionHadUnreadResult" in sessions
    assert "&& !sessionHasActiveServerRun" in sessions
    assert "&& (restoredFromCache = restoreCachedSessionStream(sessionId))" in sessions
    assert "confirmAndCancelPendingHumanQuestionsForHistoryMutation" in rendering
    assert "superseded_by_history_mutation" in interactions
    assert "if (!opts.force && !isServerStreamActive(sessionId)) return;" in sse
    assert "Number.isFinite(Number(opts.afterIndex))" in sse
    assert "detail.pending_human_interactions || {}" in sse
    assert "pendingHumanInteractionRecords(sid).length > 0" in sse


def test_pending_question_tool_row_is_merged_by_stable_call_id():
    """Restoring a pending ask_user card and replaying tool_pending must
    upgrade one row instead of rendering two draft-key variants."""
    root = Path(__file__).resolve().parents[1]
    rendering = (root / "frontend/src/app/modules/message-rendering.js").read_text(
        encoding="utf-8"
    )
    pending = rendering.split("function appendToolPendingRow", 1)[1].split(
        "function appendToolCommandDelta", 1
    )[0]

    id_lookup = pending.index("findToolCallRow(ctx, parsed.tool_call_id)")
    draft_lookup = pending.index("findToolDraftRow(ctx, parsed)")
    assert id_lookup < draft_lookup
    assert "data-event-committed" in pending
    assert "data-tool-draft-key" in pending
    assert "preferredToolPendingCommandPreview(draft, parsed)" in pending


def test_pending_human_card_auto_reveals_on_live_sse() -> None:
    """A freshly inserted pending approval/question card scrolls into view on
    live SSE, but never during history replay."""
    interactions = (Path(__file__).resolve().parents[1] / "frontend/src/app/modules/human-interactions.js").read_text(encoding="utf-8")

    assert "function autoRevealPendingHumanCard(card)" in interactions
    assert "card.scrollIntoView({ behavior: 'smooth', block: 'nearest' })" in interactions
    assert "humanCardVisibleInViewport(card)" in interactions
    assert "record.status === 'pending'" in interactions
    assert "replayingMessages" in interactions
    assert "autoRevealPendingHumanCard(card)" in interactions
