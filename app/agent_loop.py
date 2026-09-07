"""
agent_loop — ReAct 主循环与 SSE 事件源。

流程（单轮用户消息）
------------------
1. `astream_events` 构建 state，顺序执行 `react_node` → `validate_final` → `finish`。
2. `react_node`：在 token 阈值内循环调用 `chat.completions`（带 tools），解析正文/思考/ tool_calls；
   按策略执行工具，结果写回 `llm_history`；通过 `emit` 将 `llm_reasoning`（先）/ `llm_response` / `tool_call` 等推入队列。
3. `validate_final`：验证事件（PASS）；`finish`：落盘、生成 `final` 事件。
"""

from __future__ import annotations

import json
import os
import queue
import re
import time
import asyncio
import gc
import uuid
import threading
from datetime import datetime
import inspect
from contextlib import contextmanager
from pathlib import Path
from typing import TypedDict, List, Dict, Any, AsyncGenerator, Callable, Mapping, Optional, Tuple

from agent_harness import (
    EXECUTOR_TEMPERATURE,
    EXECUTOR_EXTRA_BODY,
    MAX_OUTPUT_TOKENS,
    executor_text_and_usage,
    load_prompt_template,
    session_manager,
    logger,
    MAX_REACT_ITER,
    SUBAGENT_MAX_REACT_ITER,
    _serialize_message,
    _message_to_dict,
    _dict_to_message,
    setup_logging,
    normalize_prompt_language,
    prompt_template_revision,
    executor_http_client,
    key_context_body_for_system_prompt,
    session_plan_store,
    CONTEXT_WINDOW,
    CONTEXT_EMERGENCY_SHRINK_MAX_RETRIES,
    REPEAT_DETECTION_THRESHOLD_SUMMARY,
    REPEAT_DETECTION_THRESHOLD_ERROR,
    COLOR_WHITE,
    COLOR_BLUE,
    COLOR_YELLOW,
    COLOR_RESET,
    apply_final_dedup_to_messages,
    derive_dialogue_from_assistant_history,
    LOG_TRUNCATE_KEEP_CHARS,
    LLM_CONTEXT_TRUNCATE_KEEP_CHARS,
    truncate_head_tail,
    truncate_tool_result_for_llm,
    MAX_PARALLEL_TOOLS,
    executor_one_shot_complete,
    strip_reasoning_for_api_request,
    resolve_executor_config_for_session,
    executor_config_generation,
    resolve_executor_candidates_for_session,
    executor_runtime_snapshot_for_session,
    EXECUTOR_REASONING_EFFORT,
    UserMessage,
    AssistantMessage,
    SystemMessage,
    ToolMessage,
    COMPACT_TRUNCATED_BOUNDARY_SYSTEM_EXACT,
    WORK_DIR,
    LocalNetworkUnavailableError,
    machine_network_available,
)
from agent_memory import (
    auto_length_strategy_status_line,
    compress_tail_fallback,
    context_will_attempt_compress,
    run_context_policy,
    run_edit_key_context_instruction,
)
from agent_openai import (
    _LogicalRequestBudget,
    chat_completion,
    compact_responses_history,
    extract_usage_dict,
    parse_assistant_message,
    run_chat_completion_stream_worker,
)
from llm import LLMRequestContext, LLMRequestPurpose, merge_streamed_tool_name
from model_profiles import extract_context_window_from_error
from tool_registry import (
    DuplicateToolError,
    ToolInvocationKind,
    ToolOutcome,
    ToolOutcomeKind,
    ToolRegistry,
    ToolRegistryError,
)
from host_tool_registry import HostToolInvocationContext, host_tool_invokers
from tool_execution_policy import (
    ToolExecutionPolicy,
    builtin_tool_policy,
    plugin_tool_policy,
)
import builtin_host_tools  # noqa: F401  # registers built-in host capabilities
from agent_reasoning import build_assistant_additional_kwargs
from agent_tools import (
    tools,
    get_skills_catalog,
    skills_catalog_generation,
    OPENAI_TOOL_DEFINITIONS,
    _compose_shell_command,
    AGENT_DEFAULT_WRITE_FILENAME,
    delete_file,
    safe_work_path,
    set_run_shell_interrupt_check,
    clear_run_shell_interrupt_check,
    redact_sensitive_tool_obj,
    redact_sensitive_tool_text,
    tool_work_dir_override,
)
from agent_tokenizer import (
    estimate_calculated_input_tokens_for_messages,
    estimate_full_input_tokens_for_messages,
    estimate_full_input_tokens_for_llm_history,
    estimate_hybrid_input_tokens_for_llm_history,
    record_prompt_tokens_for_messages,
    inject_missing_tool_messages,
    messages_for_openai_turns,
    build_env_static,
    build_static_system_segments,
)
import agent_mcp
import cpu_pressure
import execution_metrics
from runtime_observability import capture_workspace_state, diff_workspace_states
from agent_subagent_events import should_persist_ui_event
from workflow_extensions import activate_bundled_workflow_callbacks, session_workflows
from workspace_media_snapshots import snapshot_workspace_images


LLM_FULL_CALL_TRACE = str(os.getenv("LLM_FULL_CALL_TRACE", "0")).strip().lower() in {
    "1", "true", "yes", "on",
}
from session_event_bus import close_session_stream, prune_session_ephemeral, publish_session_event
from tool_approval_gate import (
    ApprovalPersistenceError,
    new_approval_id,
    wait_tool_ui_approval_after_emit,
)
from runtime_power import AgentRunPowerGuard, RuntimeResume
from security import (
    DecisionOutcome,
    PermissionMode,
    SecurityDecision,
    add_approval_grant,
    always_ask_for,
    authorize_request,
    authorize_tool,
    classify_hook,
    execution_scope,
    forced_approval_for,
    session_permission_mode,
)
from security.reviewer import review_request


def _workflow_callbacks():
    import sys

    activate_bundled_workflow_callbacks(sys.modules[__name__])
    return session_workflows

EXECUTOR_STREAM = os.getenv("EXECUTOR_STREAM", "true").lower() in ("1", "true", "yes")
NETWORK_RECONNECT_MAX_ATTEMPTS = max(0, int(os.getenv("NETWORK_RECONNECT_MAX_ATTEMPTS", "5")))
LOCAL_NETWORK_POLL_SECONDS = max(1.0, float(os.getenv("LOCAL_NETWORK_POLL_SECONDS", "5")))
TITLE_GENERATION_TIMEOUT_SEC = max(1.0, float(os.getenv("TITLE_GENERATION_TIMEOUT_SEC", "30")))
TITLE_GENERATION_WORKERS = max(1, min(4, int(os.getenv("TITLE_GENERATION_WORKERS", "2"))))
CONTEXT_POLICY_IDLE_TIMEOUT_SEC = max(
    1.0, float(os.getenv("CONTEXT_POLICY_IDLE_TIMEOUT_SEC", "30"))
)
STREAM_WORKER_ABORT_TIMEOUT_SEC = max(
    0.1, float(os.getenv("STREAM_WORKER_ABORT_TIMEOUT_SEC", "5"))
)
TODO_UPDATE_REMINDER_START_ROUNDS = max(
    0, int(os.getenv("TODO_UPDATE_REMINDER_START_ROUNDS", "20"))
)
TODO_UPDATE_REMINDER_INTERVAL_ROUNDS = max(
    1, int(os.getenv("TODO_UPDATE_REMINDER_INTERVAL_ROUNDS", "5"))
)
execution_metrics.configure(
    session_manager.sessions_dir,
    path_resolver=session_manager._resolve_session_path,
)


def _llm_runtime_policy(emit: Optional[Callable[[Dict[str, Any]], Any]]) -> Dict[str, Any]:
    """Choose one LLM request's output mode from process-wide pressure."""
    pressure = cpu_pressure.snapshot()
    # Severe pressure suppresses per-delta UI work, but transport remains
    # streaming so first-token hedge and loser cancellation still work.
    use_stream = bool(EXECUTOR_STREAM and (emit or pressure.degraded))
    emit_deltas = bool(use_stream and emit and not pressure.degraded)
    if pressure.degraded:
        reason = pressure.reason or "cpu_pressure"
    elif not EXECUTOR_STREAM:
        reason = "stream_disabled"
    elif not emit:
        reason = "no_event_sink"
    else:
        reason = ""
    return {
        "use_stream": use_stream,
        "emit_deltas": emit_deltas,
        "output_mode": (
            "stream" if emit_deltas else ("buffered_stream" if use_stream else "non_stream")
        ),
        "reason": reason,
        "cpu_pressure_mode": pressure.mode,
        "cpu_percent": pressure.cpu_percent,
        "cpu_average_percent": pressure.cpu_average_percent,
        "memory_percent": pressure.memory_percent,
        "available_memory_gb": pressure.available_memory_gb,
        "process_cpu_percent": pressure.process_cpu_percent,
        "event_loop_lag_ms": pressure.event_loop_lag_ms,
        "pressure_trigger": pressure.trigger,
        "pressure_snapshot": pressure,
    }


def _cpu_pressure_metrics_text(pressure: Any) -> str:
    parts: List[str] = []
    cpu_average = getattr(pressure, "cpu_average_percent", None)
    cpu_current = getattr(pressure, "cpu_percent", None)
    if cpu_average is not None:
        parts.append(f"CPU 滑动均值 {float(cpu_average):.1f}%")
    elif cpu_current is not None:
        parts.append(f"CPU {float(cpu_current):.1f}%")
    memory = getattr(pressure, "memory_percent", None)
    if memory is not None:
        parts.append(f"内存 {float(memory):.1f}%")
    process_cpu = getattr(pressure, "process_cpu_percent", None)
    if process_cpu is not None:
        parts.append(f"Agent CPU {float(process_cpu):.1f}%")
    loop_lag = getattr(pressure, "event_loop_lag_ms", None)
    if loop_lag is not None:
        parts.append(f"事件循环延迟 {float(loop_lag):.0f}ms")
    return "，".join(parts) or "指标暂不可用"


def _cpu_pressure_transition_event(
    previous_mode: Optional[str],
    pressure: Any,
) -> Optional[Dict[str, Any]]:
    """Build a durable process-log entry whenever the pressure level changes."""
    mode = str(getattr(pressure, "mode", "normal") or "normal")
    # Accept snapshots produced by the previous binary during a hot reload.
    if mode == "degraded":
        mode = "severe"
    if previous_mode == "degraded":
        previous_mode = "severe"
    if mode == previous_mode or (previous_mode is None and mode == "normal"):
        return None

    metrics = _cpu_pressure_metrics_text(pressure)
    if mode == "severe":
        content = (
            f"系统进入严重压力状态（{metrics}），已切换为整段输出，"
            "并限制本地资源型工具并发。"
        )
    elif mode == "busy":
        content = (
            f"系统进入繁忙状态（{metrics}），继续逐 token 流式输出，"
            "并保持低开销合并与延迟写入优化。"
        )
    else:
        content = f"系统负载已恢复（{metrics}），已还原逐 token 流式输出和正常本地并发。"

    return {
        "type": "status",
        "status_kind": "cpu_pressure",
        "cpu_pressure_mode": mode,
        "cpu_percent": getattr(pressure, "cpu_percent", None),
        "cpu_average_percent": getattr(pressure, "cpu_average_percent", None),
        "memory_percent": getattr(pressure, "memory_percent", None),
        "process_cpu_percent": getattr(pressure, "process_cpu_percent", None),
        "event_loop_lag_ms": getattr(pressure, "event_loop_lag_ms", None),
        "pressure_trigger": getattr(pressure, "trigger", ""),
        "content": content,
    }
try:
    import runtime_observability

    runtime_observability.configure(
        session_manager.sessions_dir,
        path_resolver=session_manager._resolve_session_path,
    )
except Exception:
    pass

_STEER_LOCK = threading.Lock()
_STEER_QUEUES: Dict[str, List[Dict[str, Any]]] = {}
_STEER_RUN_LOCK = threading.Lock()
_ACTIVE_STEER_RUNS: Dict[str, Any] = {}
_CONTEXT_POLICY_LOCKS_LOCK = threading.Lock()
_CONTEXT_POLICY_LOCKS: Dict[str, threading.Lock] = {}
_STEER_QUEUE_SIGNATURES: Dict[str, Tuple[int, int]] = {}
_STEER_TERMINAL_RETENTION = max(16, int(os.getenv("MYAGENT_STEER_TERMINAL_RETENTION", "128")))

_TITLE_GENERATION_QUEUE: queue.Queue = queue.Queue()
_TITLE_GENERATION_LOCK = threading.Lock()
_TITLE_GENERATION_PENDING: set[str] = set()
_TITLE_GENERATION_WORKERS_STARTED = False

_STEER_PENDING_STATES = {"queued", "claimed", "interrupting", "restarting", "deferred"}
_STEER_CLAIMABLE_STATES = {"queued", "interrupting"}
_STEER_TERMINAL_STATES = {"consumed", "cancelled", "failed"}
_STEER_MODES = {"interrupt", "append"}


def _default_steer_mode() -> str:
    mode = str(os.getenv("MYAGENT_STEER_MODE", "append") or "append").strip().lower()
    return mode if mode in _STEER_MODES else "append"


def _hook_decision_reason(result: Any, fallback: str) -> str:
    for item in getattr(result, "results", ()) or ():
        reason = str(getattr(item, "reason", "") or getattr(item, "error", "")).strip()
        if reason:
            return reason
    messages = list(getattr(result, "user_messages", ()) or ())
    return str(messages[0]).strip() if messages else fallback


def _tool_review_text(value: Any) -> str:
    """Normalize conversation values for the security review context."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, dict)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str).strip()
        except Exception:
            pass
    return str(value or "").strip()


def _capture_tool_review_assistant_context(
    state: Dict[str, Any],
    reasoning: Any,
    response: Any,
) -> None:
    rows = state.setdefault("_tool_review_assistant_context", [])
    reasoning_text = _tool_review_text(reasoning)
    response_text = _tool_review_text(response)
    if reasoning_text:
        rows.append({"kind": "reasoning", "content": reasoning_text})
    if response_text:
        rows.append({"kind": "response", "content": response_text})


def _capture_tool_review_user_followup(state: Dict[str, Any], content: Any) -> None:
    text = _tool_review_text(content)
    if text:
        state.setdefault("_tool_review_user_followups", []).append(text)


def _tool_review_conversation_from_events(session_id: str) -> Dict[str, Any]:
    sid = str(session_id or "").strip()
    if not sid or not _runtime_v2_is_primary():
        return {}
    try:
        events = list(_runtime_v2_react_history_ops().event_log.iter_events(sid))
    except Exception:
        logger.debug(
            "Could not reconstruct tool-review conversation for session=%s",
            sid,
            exc_info=True,
        )
        return {}

    visible_user_event_types = {"message_user", "user_turn_committed"}
    user_event_types = {*visible_user_event_types, "model_user"}
    assistant_event_types = {
        "message_assistant_final",
        "assistant_final_committed",
        "model_assistant",
    }
    origin_index = -1
    for index, event in enumerate(events):
        event_type = str(getattr(event, "type", "") or "").strip()
        if event_type not in visible_user_event_types:
            continue
        payload = dict(getattr(event, "payload", {}) or {})
        if (
            event_type == "user_turn_committed"
            and str(payload.get("ui_type") or "user") == "user_steer"
        ):
            continue
        origin_index = index
    if origin_index < 0:
        for index, event in enumerate(events):
            if str(getattr(event, "type", "") or "").strip() == "model_user":
                origin_index = index
    if origin_index < 0:
        return {}

    initial_user_question: Any = ""
    user_followups: List[Any] = []
    assistant_context: List[Dict[str, str]] = []
    last_user_entry: Optional[Tuple[str, str, str]] = None
    last_assistant_entry: Optional[Tuple[str, str]] = None
    for event in events[origin_index:]:
        event_type = str(getattr(event, "type", "") or "").strip()
        payload = dict(getattr(event, "payload", {}) or {})
        event_run_id = str(getattr(event, "run_id", "") or "").strip()
        if event_type in user_event_types:
            content_value = payload.get("ui_content")
            if content_value is None:
                content_value = payload.get("content")
            text = _tool_review_text(content_value)
            if not text:
                continue
            if (
                last_user_entry is not None
                and last_user_entry[:2] == (event_run_id, text)
                and last_user_entry[2] != event_type
            ):
                continue
            last_user_entry = (event_run_id, text, event_type)
            if initial_user_question in (None, ""):
                initial_user_question = content_value
            else:
                user_followups.append(content_value)
            continue
        if event_type not in assistant_event_types:
            continue
        additional = payload.get("additional_kwargs")
        if not isinstance(additional, dict):
            additional = {}
        reasoning = (
            additional.get("reasoning_content")
            or additional.get("reasoning")
            or payload.get("reasoning_content")
            or payload.get("reasoning")
        )
        for kind, value in (("reasoning", reasoning), ("response", payload.get("content"))):
            text = _tool_review_text(value)
            if not text:
                continue
            entry_key = (kind, text)
            if entry_key == last_assistant_entry:
                continue
            assistant_context.append({"kind": kind, "content": text})
            last_assistant_entry = entry_key
    return {
        "initial_user_question": initial_user_question,
        "user_followups": user_followups,
        "assistant_context": assistant_context[-10:],
    }


def _build_tool_review_context(
    state: Dict[str, Any],
    tool_arguments: Any,
) -> Dict[str, Any]:
    context = _tool_review_conversation_from_events(
        str(state.get("session_id") or "")
    )
    if not context:
        context = {
            "initial_user_question": (
                state.get("_submitted_user_input")
                or state.get("user_input")
                or ""
            ),
            "user_followups": list(
                state.get("_tool_review_user_followups") or []
            ),
            "assistant_context": list(
                state.get("_tool_review_assistant_context") or []
            )[-10:],
        }
    try:
        frozen_arguments = json.loads(
            json.dumps(tool_arguments, ensure_ascii=False, default=str)
        )
    except Exception:
        frozen_arguments = str(tool_arguments)
    return {
        **context,
        "tool_arguments": frozen_arguments,
    }


async def _authorize_hook_before_execute(
    definition: Any,
    payload: Dict[str, Any],
    state: Dict[str, Any],
    emit: Optional[Callable[[Dict[str, Any]], Any]],
    workspace: Path,
) -> tuple[bool, str]:
    """Authorize a Hook before any Hook-controlled code is started."""
    session_id = str(state.get("session_id") or "")
    request = classify_hook(definition, payload, workspace)
    decision, context = authorize_request(
        session_id=session_id, request=request, workspace=workspace
    )
    if decision.outcome == DecisionOutcome.DENY:
        return False, decision.reason
    if decision.outcome == DecisionOutcome.ALLOW:
        return True, decision.reason

    if context.mode == PermissionMode.APPROVE_FOR_ME:
        hook_review_context = _build_tool_review_context(
            state,
            {
                "command": request.resource,
                "hook": dict(request.metadata or {}),
                "payload": payload,
            },
        )
        review = await review_request(
            request,
            user_intent=str(state.get("_submitted_user_input") or ""),
            session_id=session_id,
            review_context=hook_review_context,
        )
        if not review.approved:
            return False, review.reason
        # Hook approval is intentionally session-scoped: unchanged Hook
        # configuration does not repeatedly interrupt the same task.
        add_approval_grant(session_id, decision.request_digest, "allow_session")
    else:
        if emit is None:
            return False, "Hook approval is required but no interactive channel is available."
        approval_id = new_approval_id()
        hook_id = str(getattr(definition, "id", "hook") or "hook")
        event = str(getattr(definition, "event", "") or "Hook")

        async def _emit_hook_approval() -> None:
            await _emit_tool_approval_required_sse(
                emit,
                session_id,
                approval_id,
                "hook.exec",
                "Hook 请求执行",
                f"项目或扩展 Hook `{hook_id}` 将在 `{event}` 阶段运行命令。",
                str(request.resource),
                f"hook:{hook_id}",
                extra={
                    "force_approval": False,
                    "approval_level": "warning",
                    "allow_always_available": False,
                },
            )

        approved = await wait_tool_ui_approval_after_emit(
            session_id,
            approval_id,
            _emit_hook_approval,
            metadata={
                "_durable": True,
                "run_id": str(state.get("_runtime_v2_run_id") or ""),
                "tool_call_id": f"hook:{hook_id}",
                "tool": "hook.exec",
                "title": "Hook 请求执行",
                "message": f"{event} / {hook_id}",
                "subtitle": str(request.resource),
                "security_request_digest": decision.request_digest,
                "security_rule_id": decision.rule_id,
                "allow_always_available": False,
                "force_approval": False,
                "approval_level": "warning",
            },
            review_context={
                "request": request,
                "user_intent": str(state.get("_submitted_user_input") or ""),
                "review_context": _build_tool_review_context(
                    state,
                    {
                        "command": request.resource,
                        "hook": dict(request.metadata or {}),
                        "payload": payload,
                    },
                ),
            },
        )
        if not approved:
            return False, "Hook execution was denied by the user."
        add_approval_grant(session_id, decision.request_digest, "allow_session")

    final_decision, _ = authorize_request(
        session_id=session_id, request=request, workspace=workspace
    )
    if final_decision.outcome != DecisionOutcome.ALLOW:
        return False, "Hook approval could not be bound to the final Hook request."
    return True, final_decision.reason


async def _dispatch_state_hook(
    event: str,
    state: Dict[str, Any],
    payload: Optional[Dict[str, Any]] = None,
    emit: Optional[Callable[[Dict[str, Any]], Any]] = None,
):
    """Dispatch one lifecycle Hook with common run identity and audit fields."""

    from agent_extensions import dispatch_hook

    data = dict(payload or {})
    data.setdefault("session_id", str(state.get("session_id") or ""))
    data.setdefault("run_id", str(state.get("_runtime_v2_run_id") or ""))
    data.setdefault("project_root", str(WORK_DIR))
    try:
        hook_meta = session_manager._load_metadata(data["session_id"]) or {}
    except Exception:
        hook_meta = {}
    hook_workspace = str(
        hook_meta.get("subagent_work_dir")
        or hook_meta.get("git_worktree_path")
        or ""
    ).strip()
    if hook_workspace:
        data.setdefault("workspace_root", hook_workspace)
        data.setdefault("worktree_isolated", bool(hook_meta.get("git_worktree_managed")))
    security_workspace = Path(hook_workspace or WORK_DIR).resolve()
    data["_hook_authorizer"] = lambda definition, call_payload: _authorize_hook_before_execute(
        definition,
        dict(call_payload or {}),
        state,
        emit,
        security_workspace,
    )
    hook_workspace_before = (
        await asyncio.to_thread(capture_workspace_state, hook_workspace)
        if hook_workspace
        else None
    )
    result = await dispatch_hook(
        event,
        data,
        session_manager=session_manager,
        session_id=str(state.get("session_id") or ""),
        run_id=str(state.get("_runtime_v2_run_id") or ""),
    )
    if hook_workspace:
        hook_workspace_after = await asyncio.to_thread(
            capture_workspace_state,
            hook_workspace,
        )
        hook_changes = diff_workspace_states(
            hook_workspace_before,
            hook_workspace_after,
        )
        if hook_changes:
            try:
                import runtime_observability

                runtime_observability.record_file_changes(
                    str(state.get("session_id") or ""),
                    str(state.get("_runtime_v2_run_id") or ""),
                    hook_changes,
                    tool=f"hook:{event}",
                )
            except Exception:
                logger.debug("Hook file audit failed", exc_info=True)
    notices = [*list(result.warnings or ()), *list(result.user_messages or ())]
    if event == "SessionStart":
        notices.extend(f"Hook configuration: {item}" for item in result.config_errors or ())
        try:
            from agent_extensions import load_plugins

            notices.extend(f"Plugin load: {item}" for item in load_plugins().errors)
        except Exception as exc:
            notices.append(f"Plugin registry: {exc}")
    if emit and notices:
        await _push_stream_event(
            state,
            {
                "type": "status",
                "content": "【Hook · %s】%s" % (event, "\n".join(str(x) for x in notices if x)),
            },
            emit=emit,
        )
    return result


def _append_hook_context(state: Dict[str, Any], text: str, event: str) -> None:
    content = str(text or "").strip()
    if not content:
        return
    message = SystemMessage(content=f"[Hook additional context · {event}]\n{content}")
    state.setdefault("work_messages", []).append(message)
    state.setdefault("llm_history", []).append(message)
    _persist_state_with_model_append(state, message)


def _blocked_tool_result(
    tool_name: str,
    tool_args: Dict[str, Any],
    tool_id: str,
    reason: str,
    *,
    paused: bool = False,
) -> Dict[str, Any]:
    status = "paused" if paused else "blocked"
    message = f"Hook {status} `{tool_name}`: {reason}"
    return {
        "type": "tool",
        "tool_name": tool_name,
        "tool_args": tool_args,
        "tool_id": tool_id,
        "result": message,
        "tool_detail_log": message,
        "tool_detail_llm": message,
        "tool_detail_ui": message,
        "result_for_log": message,
        "tool_failed": True,
        "tool_status": _tool_result_status(tool_name, message, failed=True),
    }


def _unknown_tool_result(
    tool_name: str,
    tool_args: Dict[str, Any],
    tool_id: str,
) -> Dict[str, Any]:
    """Return a tool-protocol error without running Hooks or security review."""
    message = f"Unknown or unavailable tool: {tool_name}"
    return {
        "type": "tool",
        "tool_name": tool_name,
        "tool_args": tool_args,
        "tool_id": tool_id,
        "result": message,
        "tool_detail_log": message,
        "tool_detail_llm": message,
        "tool_detail_ui": message,
        "result_for_log": message,
        "tool_failed": True,
        "tool_status": _tool_result_status(tool_name, message, failed=True),
    }


_STREAM_CORRUPTED_ARGUMENTS_KEY = "_stream_corrupted_arguments"


def _corrupted_stream_args_result(
    tool_name: str,
    tool_id: str,
    tool_call_index: Any = None,
    corrupted_length: int = 0,
) -> Dict[str, Any]:
    """Tool-protocol error for arguments lost/corrupted in the LLM stream.

    Marks the failure as a transport fault so the model retries the same call
    instead of rewriting arguments it believes were wrong.
    """
    message = (
        f"传输层错误：`{tool_name}` 的流式参数 JSON 损坏（收到 {corrupted_length} 字符但无法解析），"
        "本次调用未执行。这不是参数构造问题；请直接重试同样的工具调用。"
        "若连续失败，请改用参数更简单的替代方案。"
    )
    result = {
        "type": "tool",
        "tool_name": tool_name,
        "tool_args": {},
        "tool_id": tool_id,
        "result": message,
        "tool_detail_log": message,
        "tool_detail_llm": message,
        "tool_detail_ui": message,
        "result_for_log": message,
        "tool_failed": True,
        "tool_status": _tool_result_status(tool_name, message, failed=True),
    }
    if tool_call_index is not None:
        result["tool_call_index"] = tool_call_index
    return result


async def _apply_stop_hooks(
    state: Dict[str, Any],
    emit: Optional[Callable[[Dict[str, Any]], Any]],
) -> Dict[str, Any]:
    """Let a Stop Hook request more work, with a hard retry boundary."""

    max_retries = max(0, min(10, int(os.getenv("STOP_HOOK_MAX_RETRIES", "3"))))
    for attempt in range(max_retries + 1):
        result = await _dispatch_state_hook(
            "Stop",
            state,
            {
                "matcher_value": "stop",
                "attempt": attempt + 1,
                "final_response": str(state.get("final_response") or ""),
            },
            emit,
        )
        if result.additional_context:
            _append_hook_context(state, result.additional_context, "Stop")
        if result.should_pause or result.requires_approval:
            reason = _hook_decision_reason(result, "Stop Hook paused the run.")
            state["final_response"] = f"执行已由 Stop Hook 暂停：{reason}"
            return state
        if not result.blocked:
            return state
        reason = _hook_decision_reason(result, "Stop Hook requested more work.")
        if attempt >= max_retries:
            state["final_response"] = (
                f"Stop Hook 在 {max_retries + 1} 次检查后仍阻止结束：{reason}"
            )
            return state
        continuation = SystemMessage(
            content=(
                "[Stop Hook blocked completion]\n"
                f"{reason}\nContinue working and address this requirement before stopping again."
            )
        )
        state.setdefault("work_messages", []).append(continuation)
        state.setdefault("llm_history", []).append(continuation)
        _persist_state_with_model_append(state, continuation)
        state["final_response"] = ""
        state = await _run_react_node_off_loop(state, emit)
    return state


def _active_session_path(session_id: str) -> Path:
    resolver = getattr(session_manager, "_resolve_session_path", None)
    if callable(resolver):
        return Path(resolver(str(session_id)))
    return Path(session_manager.sessions_dir) / str(session_id)


def _steer_inbox_path(session_id: str) -> Path:
    return _active_session_path(session_id) / "steer_inbox.json"


def _load_steer_queue_locked(session_id: str) -> List[Dict[str, Any]]:
    path = _steer_inbox_path(session_id)
    try:
        stat = path.stat()
        signature = (int(stat.st_mtime_ns), int(stat.st_size))
    except OSError:
        signature = (-1, -1)
    if session_id in _STEER_QUEUES and _STEER_QUEUE_SIGNATURES.get(session_id) == signature:
        return _STEER_QUEUES[session_id]
    rows: List[Dict[str, Any]] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        rows = [dict(x) for x in data if isinstance(x, dict)] if isinstance(data, list) else []
    except Exception:
        logger.warning("failed to load steer inbox for %s", session_id, exc_info=True)
    if rows:
        _STEER_QUEUES[session_id] = rows
    else:
        _STEER_QUEUES.pop(session_id, None)
    _STEER_QUEUE_SIGNATURES[session_id] = signature
    return rows


def _save_steer_queue_locked(session_id: str, rows: List[Dict[str, Any]]) -> None:
    path = _steer_inbox_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        _STEER_QUEUES.pop(session_id, None)
        _STEER_QUEUE_SIGNATURES.pop(session_id, None)
        try:
            path.unlink(missing_ok=True)
        except Exception:
            logger.warning("failed to remove steer inbox for %s", session_id, exc_info=True)
        return
    _STEER_QUEUES[session_id] = rows
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    stat = path.stat()
    _STEER_QUEUE_SIGNATURES[session_id] = (int(stat.st_mtime_ns), int(stat.st_size))


def _normalize_steer_item(item: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(item or {})
    row["state"] = str(row.get("state") or "queued").strip().lower()
    if row["state"] not in _STEER_PENDING_STATES | _STEER_TERMINAL_STATES:
        row["state"] = "queued"
    row["version"] = max(0, int(row.get("version") or 0))
    row["source_run_id"] = str(row.get("source_run_id") or "").strip()
    row["replacement_run_id"] = str(row.get("replacement_run_id") or "").strip()
    row["mode"] = str(row.get("mode") or "interrupt").strip().lower()
    if row["mode"] not in _STEER_MODES:
        row["mode"] = "interrupt"
    return row


def _trim_steer_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    pending = [_normalize_steer_item(x) for x in rows if str((x or {}).get("state") or "queued") not in _STEER_TERMINAL_STATES]
    terminal = [_normalize_steer_item(x) for x in rows if str((x or {}).get("state") or "queued") in _STEER_TERMINAL_STATES]
    terminal.sort(key=lambda x: float(x.get("updated_at") or x.get("created_at") or 0.0))
    return pending + terminal[-_STEER_TERMINAL_RETENTION:]


@contextmanager
def _steer_transaction(session_id: str):
    session_dir = _active_session_path(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    lock_path = session_dir / ".steer.lock"
    with lock_path.open("a+b") as fh:
        if os.name == "nt":
            import msvcrt

            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                fh.write(b"0")
                fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class _SteerRestartRequested(Exception):
    """Raised inside the active ReAct run when a user steer should restart the turn."""


class _SteerRunControl:
    def __init__(self, session_id: str, run_id: str):
        self.session_id = str(session_id or "").strip()
        self.run_id = str(run_id or "").strip()
        self.abort_event = threading.Event()
        self.reason = ""
        self.created_at = time.time()
        self.fence_token = str(uuid.uuid4())
        self._listener_lock = threading.Lock()
        self._abort_listeners: set[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = set()

    def abort(self, reason: str = "steer") -> None:
        self.reason = str(reason or "steer")
        self.abort_event.set()
        with self._listener_lock:
            listeners = list(self._abort_listeners)
        for loop, event in listeners:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                pass

    def reset(self) -> None:
        self.reason = ""
        self.abort_event.clear()
        with self._listener_lock:
            listeners = list(self._abort_listeners)
        for loop, event in listeners:
            try:
                loop.call_soon_threadsafe(event.clear)
            except RuntimeError:
                pass

    def is_aborted(self) -> bool:
        return self.abort_event.is_set()

    def subscribe_abort(self, loop: asyncio.AbstractEventLoop) -> asyncio.Event:
        event = asyncio.Event()
        with self._listener_lock:
            self._abort_listeners.add((loop, event))
        if self.abort_event.is_set():
            event.set()
        return event

    def unsubscribe_abort(self, loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
        with self._listener_lock:
            self._abort_listeners.discard((loop, event))


def _register_steer_run_control(session_id: str, run_id: str) -> _SteerRunControl:
    control = _SteerRunControl(session_id, run_id)
    sid = control.session_id
    if sid:
        with _STEER_RUN_LOCK:
            _ACTIVE_STEER_RUNS[sid] = control
        try:
            fence_path = _active_session_path(sid) / "active_run_fence.json"
            fence_path.parent.mkdir(parents=True, exist_ok=True)
            with _steer_transaction(sid):
                tmp = fence_path.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps({"run_id": control.run_id, "token": control.fence_token, "created_at": control.created_at}),
                    encoding="utf-8",
                )
                tmp.replace(fence_path)
        except Exception:
            logger.warning("failed to persist active run fence for %s", sid, exc_info=True)
    return control


def _clear_steer_run_control(session_id: str, control: _SteerRunControl) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    with _STEER_RUN_LOCK:
        if _ACTIVE_STEER_RUNS.get(sid) is control:
            _ACTIVE_STEER_RUNS.pop(sid, None)
    try:
        fence_path = _active_session_path(sid) / "active_run_fence.json"
        with _steer_transaction(sid):
            current = json.loads(fence_path.read_text(encoding="utf-8")) if fence_path.exists() else {}
            if str(current.get("token") or "") == control.fence_token:
                fence_path.unlink(missing_ok=True)
    except Exception:
        logger.debug("failed to clear active run fence for %s", sid, exc_info=True)


def abort_session_steer_run(session_id: str, reason: str = "steer") -> bool:
    sid = str(session_id or "").strip()
    if not sid:
        return False
    with _STEER_RUN_LOCK:
        control = _ACTIVE_STEER_RUNS.get(sid)
    if not control:
        return False
    try:
        control.abort(reason)
        return True
    except Exception:
        logger.debug("abort steer run failed: session_id=%s", sid, exc_info=True)
        return False


def _state_run_has_write_fence(state: State) -> bool:
    """Whether this run still owns process-local writes for its session."""
    control = _steer_control_from_state(state) if isinstance(state, dict) else None
    if control is None:
        return True
    sid = str(state.get("session_id") or "").strip()
    with _STEER_RUN_LOCK:
        local_current = _ACTIVE_STEER_RUNS.get(sid)
    if local_current is not control:
        return False
    try:
        fence_path = _active_session_path(sid) / "active_run_fence.json"
        current = json.loads(fence_path.read_text(encoding="utf-8")) if fence_path.exists() else {}
        token = str(current.get("token") or "")
        return not token or token == control.fence_token
    except Exception:
        return local_current is control


def _context_policy_lock_for_session(session_id: str) -> threading.Lock:
    sid = str(session_id or "").strip()
    with _CONTEXT_POLICY_LOCKS_LOCK:
        lock = _CONTEXT_POLICY_LOCKS.get(sid)
        if lock is None:
            lock = threading.Lock()
            _CONTEXT_POLICY_LOCKS[sid] = lock
        return lock


def _run_context_policy_serialized(
    llm_history: List,
    key_context: str,
    session_id: str,
    *,
    force_user_compact: bool,
    hint_sink: Optional[Callable[[Any], None]] = None,
    context_window: Optional[int] = None,
    prompt_language: Optional[str] = None,
    should_stop: Optional[Callable[[], bool]] = None,
):
    lock = _context_policy_lock_for_session(session_id)
    with lock:
        return run_context_policy(
            llm_history,
            key_context,
            session_id,
            force_user_compact=force_user_compact,
            hint_sink=hint_sink,
            context_window=context_window,
            prompt_language=prompt_language,
            should_stop=should_stop,
        )


def _wait_context_policy_idle(
    session_id: str,
    timeout_sec: Optional[float] = None,
) -> bool:
    lock = _context_policy_lock_for_session(session_id)
    if timeout_sec is None:
        acquired = lock.acquire()
    else:
        acquired = lock.acquire(timeout=max(0.0, float(timeout_sec)))
    if not acquired:
        return False
    lock.release()
    return True


def enqueue_session_steer(
    session_id: str,
    content: str,
    client_id: str = "",
    ui_content: str = "",
    *,
    source_run_id: str = "",
    mode: str = "",
) -> Dict[str, Any]:
    sid = str(session_id or "").strip()
    text = str(content or "").strip()
    if not sid:
        return {"ok": False, "error": "missing session_id"}
    if not text:
        return {"ok": False, "error": "empty message"}
    steer_mode = str(mode or _default_steer_mode()).strip().lower()
    if steer_mode not in _STEER_MODES:
        return {"ok": False, "error": "invalid steer mode"}
    item = {
        "id": str(uuid.uuid4()),
        "content": text,
        "ui_content": str(ui_content or "").strip() or text,
        "client_id": str(client_id or "").strip(),
        "created_at": time.time(),
        "updated_at": time.time(),
        "state": "queued",
        "version": 1,
        "source_run_id": str(source_run_id or "").strip(),
        "replacement_run_id": "",
        "mode": steer_mode,
    }
    with _STEER_LOCK:
        with _steer_transaction(sid):
            q = [_normalize_steer_item(x) for x in _load_steer_queue_locked(sid)]
            client = str(client_id or "").strip()
            if client:
                for existing in q:
                    if str(existing.get("client_id") or "") == client:
                        return {"ok": True, "item": dict(existing), "queued": len(q), "deduplicated": True}
            q.append(item)
            _save_steer_queue_locked(sid, _trim_steer_rows(q))
            depth = sum(1 for x in q if x.get("state") in _STEER_PENDING_STATES)
    return {"ok": True, "item": item, "queued": depth}


def remove_session_steer(session_id: str, steer_id: str = "", client_id: str = "") -> Dict[str, Any]:
    sid = str(session_id or "").strip()
    target_id = str(steer_id or "").strip()
    target_client = str(client_id or "").strip()
    if not sid:
        return {"ok": False, "error": "missing session_id"}
    if not target_id and not target_client:
        return {"ok": False, "error": "missing steer id"}
    with _STEER_LOCK:
        with _steer_transaction(sid):
            q = [_normalize_steer_item(x) for x in _load_steer_queue_locked(sid)]
            keep: List[Dict[str, Any]] = []
            removed: Optional[Dict[str, Any]] = None
            for item in q:
                same_id = target_id and str(item.get("id") or "") == target_id
                same_client = target_client and str(item.get("client_id") or "") == target_client
                if removed is None and (same_id or same_client) and item.get("state") in {"queued", "interrupting"}:
                    item["state"] = "cancelled"
                    item["version"] = int(item.get("version") or 0) + 1
                    item["updated_at"] = time.time()
                    item["cancelled_at"] = item["updated_at"]
                    removed = dict(item)
                keep.append(item)
            if removed is None:
                return {"ok": False, "error": "steer already claimed or not pending"}
            _save_steer_queue_locked(sid, _trim_steer_rows(keep))
    return {"ok": True, "item": removed, "queued": sum(1 for x in keep if x.get("state") in _STEER_PENDING_STATES)}


def _normalize_steer_modes(modes: Optional[set[str]]) -> set[str]:
    if modes is None:
        return set(_STEER_MODES)
    return {str(mode or "").strip().lower() for mode in modes} & _STEER_MODES


def _pop_session_steers(session_id: str, *, modes: Optional[set[str]] = None) -> List[Dict[str, Any]]:
    sid = str(session_id or "").strip()
    if not sid:
        return []
    allowed_modes = _normalize_steer_modes(modes)
    with _STEER_LOCK:
        # Peek, then acknowledge each item only after its durable Runtime V2
        # user-turn commit succeeds. This survives process loss mid-consume.
        items = [
            _normalize_steer_item(x)
            for x in _load_steer_queue_locked(sid)
            if str((x or {}).get("state") or "queued") in _STEER_CLAIMABLE_STATES
            and str((x or {}).get("mode") or "interrupt").strip().lower() in allowed_modes
        ]
    return items


def _has_session_steers(session_id: str, *, modes: Optional[set[str]] = None) -> bool:
    sid = str(session_id or "").strip()
    if not sid:
        return False
    allowed_modes = _normalize_steer_modes(modes)
    with _STEER_LOCK:
        return any(
            str((x or {}).get("state") or "queued") in _STEER_CLAIMABLE_STATES
            and str((x or {}).get("mode") or "interrupt").strip().lower() in allowed_modes
            for x in _load_steer_queue_locked(sid)
        )


def get_session_steer(session_id: str, steer_id: str = "", client_id: str = "") -> Dict[str, Any]:
    sid = str(session_id or "").strip()
    target_id = str(steer_id or "").strip()
    target_client = str(client_id or "").strip()
    if not sid or (not target_id and not target_client):
        return {"ok": False, "error": "missing steer id"}
    with _STEER_LOCK:
        rows = [_normalize_steer_item(x) for x in _load_steer_queue_locked(sid)]
        for item in rows:
            if (target_id and item.get("id") == target_id) or (target_client and item.get("client_id") == target_client):
                return {"ok": True, "item": dict(item)}
    return {"ok": False, "error": "steer not found"}


def list_session_steers(session_id: str, *, include_terminal: bool = False) -> Dict[str, Any]:
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "missing session_id"}
    with _STEER_LOCK:
        rows = [_normalize_steer_item(x) for x in _load_steer_queue_locked(sid)]
    if not include_terminal:
        rows = [x for x in rows if x.get("state") in _STEER_PENDING_STATES]
    rows.sort(key=lambda x: float(x.get("created_at") or 0.0))
    return {"ok": True, "items": rows}


def transition_session_steer(
    session_id: str,
    steer_id: str,
    from_states: set[str],
    to_state: str,
    **updates: Any,
) -> Dict[str, Any]:
    sid = str(session_id or "").strip()
    target_id = str(steer_id or "").strip()
    target_state = str(to_state or "").strip().lower()
    if not sid or not target_id or target_state not in _STEER_PENDING_STATES | _STEER_TERMINAL_STATES:
        return {"ok": False, "error": "invalid steer transition"}
    allowed = {str(x).strip().lower() for x in from_states}
    with _STEER_LOCK:
        with _steer_transaction(sid):
            rows = [_normalize_steer_item(x) for x in _load_steer_queue_locked(sid)]
            for index, item in enumerate(rows):
                if item.get("id") != target_id:
                    continue
                if item.get("state") == target_state:
                    changed_updates = {k: v for k, v in updates.items() if v is not None and item.get(k) != v}
                    if changed_updates:
                        item.update(changed_updates)
                        item["version"] = int(item.get("version") or 0) + 1
                        item["updated_at"] = time.time()
                        rows[index] = item
                        _save_steer_queue_locked(sid, _trim_steer_rows(rows))
                    return {"ok": True, "item": dict(item), "deduplicated": True}
                if item.get("state") not in allowed:
                    return {"ok": False, "error": f"steer is {item.get('state')}"}
                item.update({k: v for k, v in updates.items() if v is not None})
                item["state"] = target_state
                item["version"] = int(item.get("version") or 0) + 1
                item["updated_at"] = time.time()
                rows[index] = item
                _save_steer_queue_locked(sid, _trim_steer_rows(rows))
                return {"ok": True, "item": dict(item)}
    return {"ok": False, "error": "steer not found"}


def _claim_session_steers(
    session_id: str,
    run_id: str,
    *,
    modes: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    sid = str(session_id or "").strip()
    owner = str(run_id or "").strip()
    if not sid:
        return []
    allowed_modes = _normalize_steer_modes(modes)
    now = time.time()
    claimed: List[Dict[str, Any]] = []
    with _STEER_LOCK:
        with _steer_transaction(sid):
            rows = [_normalize_steer_item(x) for x in _load_steer_queue_locked(sid)]
            changed = False
            for index, item in enumerate(rows):
                if str(item.get("mode") or "interrupt") not in allowed_modes:
                    continue
                state_name = str(item.get("state") or "queued")
                stale_claim = state_name == "claimed" and now - float(item.get("claimed_at") or 0.0) >= 30.0
                same_owner = state_name == "claimed" and owner and str(item.get("claimed_by") or "") == owner
                if state_name not in _STEER_CLAIMABLE_STATES and not stale_claim and not same_owner:
                    continue
                item["state"] = "claimed"
                item["claimed_by"] = owner
                item["claimed_at"] = now
                item["updated_at"] = now
                item["version"] = int(item.get("version") or 0) + 1
                rows[index] = item
                claimed.append(dict(item))
                changed = True
            if changed:
                _save_steer_queue_locked(sid, _trim_steer_rows(rows))
    return claimed


def _set_session_steers_deferred(session_id: str, deferred: bool) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    source_states = {"queued", "interrupting"} if deferred else {"deferred"}
    target_state = "deferred" if deferred else "interrupting"
    with _STEER_LOCK:
        with _steer_transaction(sid):
            rows = [_normalize_steer_item(x) for x in _load_steer_queue_locked(sid)]
            changed = False
            for item in rows:
                if str(item.get("mode") or "interrupt") != "interrupt":
                    continue
                if item.get("state") not in source_states:
                    continue
                item["state"] = target_state
                item["version"] = int(item.get("version") or 0) + 1
                item["updated_at"] = time.time()
                changed = True
            if changed:
                _save_steer_queue_locked(sid, _trim_steer_rows(rows))


def _is_followup_interrupt(session_id: str) -> bool:
    try:
        return session_manager.get_interrupt_reason(session_id) == "followup"
    except Exception:
        return False

# ---------------------------------------------------------------------------
# 压缩兜底（agent_memory：compress_tail_fallback）
# ---------------------------------------------------------------------------


def _compress_history_fallback_kind(nl: Optional[List[Any]]) -> str:
    """非空表示走了截尾兜底：llm 首条为 COMPACT_TRUNCATED_BOUNDARY 或旧版中文截尾通知。"""
    if not nl:
        return ""
    m0 = nl[0]
    if not isinstance(m0, SystemMessage):
        return ""
    c = str(m0.content or "").strip()
    if c == COMPACT_TRUNCATED_BOUNDARY_SYSTEM_EXACT.strip():
        return "truncated"
    if any(
        s in c
        for s in (
            "上下文摘要异常",
            "上下文压缩异常",
            "已达最大轮次",
            "Conversation truncated",
        )
    ):
        return "truncated"
    return ""

# ---------------------------------------------------------------------------
# 工具调用：OpenAI 返回的是参数字典，必须 **kwargs 传入 Python 函数
# ---------------------------------------------------------------------------


def _filter_kwargs_for_callable(func: Callable[..., Any], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """只传入可调用对象签名中接受的形参，避免模型多传键导致 TypeError。若带 **kwargs 则原样传递。"""
    if not isinstance(kwargs, dict):
        return {}
    try:
        sig = inspect.signature(func)
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            return dict(kwargs)
        accept = {
            p.name
            for p in sig.parameters.values()
            if p.kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }
        return {k: v for k, v in kwargs.items() if k in accept}
    except (TypeError, ValueError):
        return dict(kwargs)


# ============================================================
# 定时器：检测 reasoning/content 停止后发送"正在思考中..."
# ============================================================
async def _thinking_timer(emit, state, delay=8):
    """等待 delay 秒后，发送'正在思考中...'状态"""
    await asyncio.sleep(delay)
    if emit:
        await _push_stream_event(state, {"type": "status", "content": "正在思考中...", "ephemeral": True}, emit=emit)
        await asyncio.sleep(0)


async def _invoke_plain_tool(tool_func: Callable[..., Any], tool_args: Any) -> Any:
    """
    使用 OpenAI 返回的参数字典调用纯 Python 工具。
    必须 ** 解包，不能用 func(tool_args) 把整包 dict 当作第一个位置参数（否则会引发各类 'dict' has no attribute ...）。
    """
    if not isinstance(tool_args, dict):
        tool_args = {}
    ka = _filter_kwargs_for_callable(tool_func, tool_args)
    if inspect.iscoroutinefunction(tool_func):
        return await tool_func(**ka)
    return await asyncio.to_thread(lambda: tool_func(**ka))


# ---------------------------------------------------------------------------
# 图状态：llm_history 为唯一完整多轮；运行中 dialogue 由 llm derive（与模型侧主链一致）；dialogue_history.json 落盘来自 ui_events（完整用户可见主链）。
# ---------------------------------------------------------------------------
class State(TypedDict):
    dialogue: List                                    # 由 llm_history 派生的主对话（用户 + 对用户的最终助手）
    work_messages: List                               # 原始工作消息（全量，与前端/落盘 work_messages 一致）
    llm_history: List                                 # LLM 上下文历史（可压缩，已持久化）
    user_input: str                                  # 当前用户输入
    final_response: str                               # 最终响应
    stream_events: List[Dict[str, Any]]               # 流式事件队列
    final_printed: bool                               # 是否已输出最终结果
    session_id: str                                   # 会话 ID
    llm_calls: List[Dict[str, Any]]                   # 记录所有 LLM 调用
    key_context: str                                 # 持久化关键信息块（与 key_context.md 同步）
    # 重复检测状态（持久化）
    repeat_count: int                                 # 连续重复次数
    last_response_content: str                        # 上一次响应内容
    last_tool_calls_signature: str                    # 上一次工具调用签名
    reminder_inserted: bool                           # 是否已插入提醒


def _truncate_xml_content_blocks(xml_text: str, keep_chars: int) -> str:
    """
    仅截断 XML 文本内每个 <content>...</content> 块的内容，
    不对整段 XML 做整体截断。
    """
    if not isinstance(xml_text, str):
        xml_text = str(xml_text)

    pattern = re.compile(r"(<content>)(.*?)(</content>)", re.DOTALL)

    def _repl(match: re.Match) -> str:
        start_tag, inner_text, end_tag = match.groups()
        return f"{start_tag}{truncate_head_tail(inner_text, keep_chars)}{end_tag}"

    return pattern.sub(_repl, xml_text)


tools_dict = {k: v for k, v in tools.items()}

def _can_execute_closed_stream_tool(
    tool_name: str, descriptor: Any = None
) -> bool:
    """A closed, schema-valid streamed call may run before the turn finishes.

    context_manage is a ReAct control operation that replaces the very history
    currently being used to assemble this assistant turn, so it remains in the
    post-turn phase. External/read/write/Shell/MCP tools have no such dependency.
    """
    name = str(tool_name or "").strip()
    policy = (
        descriptor
        if descriptor is not None
        else host_tool_invokers.policy(name)
        if host_tool_invokers.has(name)
        else builtin_tool_policy(name)
    )
    return bool(name) and bool(policy.early_stream_safe)


def _tool_steer_policy(tool_name: str, descriptor: Any = None) -> Dict[str, str]:
    """Describe cancellation semantics without claiming external rollback."""
    name = str(tool_name or "").strip()
    policy = (
        descriptor
        if descriptor is not None
        else host_tool_invokers.policy(name)
        if host_tool_invokers.has(name)
        else builtin_tool_policy(name)
    )
    side_effect = (
        "none"
        if policy.effect == "read"
        else "reversible"
        if policy.effect == "control"
        else "irreversible"
    )
    return {"interruptibility": policy.interruptibility, "side_effect": side_effect}
READ_ONLY_TOOL_VIRTUAL_LINE_CHARS = 1000


def _wrap_read_only_tool_output_lines(text: Any, max_chars: int = READ_ONLY_TOOL_VIRTUAL_LINE_CHARS) -> str:
    raw = redact_sensitive_tool_text(text)
    limit = max(1, int(max_chars or READ_ONLY_TOOL_VIRTUAL_LINE_CHARS))
    out: List[str] = []
    for line in raw.splitlines(keepends=True):
        newline = ""
        if line.endswith("\r\n"):
            body = line[:-2]
            newline = "\r\n"
        elif line.endswith("\n") or line.endswith("\r"):
            body = line[:-1]
            newline = line[-1]
        else:
            body = line
        if body == "":
            out.append(line)
            continue
        for i in range(0, len(body), limit):
            chunk = body[i : i + limit]
            if i + limit < len(body):
                chunk += "\n"
            else:
                chunk += newline
            out.append(chunk)
    return "".join(out) if out else raw


def compute_context_tokens_for_session(
    session_id: str,
    tool_definitions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    与 react_node 中发往模型前的整包输入 token 估算一致；不依赖前端缓存。

    不含仅在循环中途临时插入的系统条目不包含在内（与稳定快照相比误差通常很小）。
    """
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "invalid session_id"}
    if _runtime_v2_is_primary():
        llm_history_dicts = _load_runtime_v2_model_history_dicts(sid)
        key_context = _load_runtime_v2_context_summary(sid)
    else:
        try:
            _sid, _dialogue, _wm, llm_history_dicts, key_context, _md = session_manager.get_or_create_session(sid)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    llm_history = [_dict_to_message(m) for m in llm_history_dicts]
    try:
        prompt_language = normalize_prompt_language(
            (session_manager._load_metadata(sid) or {}).get("prompt_language")
        )
    except Exception:
        prompt_language = "zh-CN"
    token_mode = get_context_token_mode()
    if token_mode == "calculated":
        full_input_est = estimate_full_input_tokens_for_llm_history(
            sid,
            llm_history,
            key_context or "",
            prompt_language,
            tools=tool_definitions,
        )
        token_source = "local_calculated"
    else:
        full_input_est, token_source = estimate_hybrid_input_tokens_for_llm_history(
            sid,
            llm_history,
            key_context or "",
            prompt_language,
            tools=tool_definitions,
        )
    _client, active_model, _max_out, active_context_window = resolve_executor_config_for_session(
        sid
    )
    return {
        "ok": True,
        "estimated": int(full_input_est),
        "threshold": int(active_context_window),
        "model": active_model,
        "source": "runtime_v2_projection" if _runtime_v2_is_primary() else "legacy_history",
        "token_source": token_source,
        "token_mode": token_mode,
    }


def get_context_token_mode(value: Any = None) -> str:
    raw = value
    if raw is None or str(raw).strip() == "":
        raw = os.getenv("CONTEXT_TOKEN_MODE", os.getenv("CONTEXT_TOKEN_ACCOUNTING_MODE", "hybrid"))
    mode = str(raw or "").strip().lower()
    if "#" in mode:
        mode = mode.split("#", 1)[0].strip()
    if mode:
        mode = mode.split()[0]
    if mode in {"calculated", "compute", "pure", "pure_compute"}:
        return "calculated"
    return "hybrid"

# ==================== 辅助函数：实时持久化 ====================
def _persist_session_messages(state: State) -> None:
    """Persist the current model/work/context state if this run owns the fence."""
    if not _state_run_has_write_fence(state):
        logger.info("suppressed stale run persistence: session=%s run=%s", state.get("session_id"), state.get("_runtime_v2_run_id"))
        return
    state["dialogue"] = derive_dialogue_from_assistant_history(state["llm_history"])
    if _runtime_v2_is_primary():
        _runtime_v2_commit_context_summary(state)
        return
    _materialize_lazy_work_messages(state)
    session_manager.update_session(
        state["session_id"],
        [_message_to_dict(m) for m in state["work_messages"]],
        [_message_to_dict(m) for m in state["llm_history"]],
        state.get("key_context", ""),
        dialogue_history=session_manager.dialogue_dicts_from_ui_events_file(state["session_id"]),
    )


def _persist_state(state: State):
    """实时保存当前会话的所有状态到磁盘。"""
    try:
        _persist_session_messages(state)
    except Exception as e:
        logger.warning(f"实时持久化失败: {e}")
        if _runtime_v2_is_primary():
            raise


def _assistant_tool_call_ids(msg: Any) -> List[str]:
    if not isinstance(msg, AssistantMessage):
        return []
    tool_calls = getattr(msg, "tool_calls", None)
    if not isinstance(tool_calls, list) or not tool_calls:
        return []
    ids: List[str] = []
    for idx, tc in enumerate(tool_calls):
        if isinstance(tc, dict):
            raw = tc.get("id") or tc.get("tool_call_id") or ""
        else:
            raw = getattr(tc, "id", "") or getattr(tc, "tool_call_id", "") or ""
        tid = str(raw or "").strip()
        ids.append(tid or f"__missing_tool_call_id_{idx}")
    return ids


def _first_unclosed_tool_call_index(messages: List[Any]) -> Optional[int]:
    i = 0
    n = len(messages)
    while i < n:
        ids = _assistant_tool_call_ids(messages[i])
        if not ids:
            i += 1
            continue
        required = set(ids)
        seen: List[str] = []
        j = i + 1
        while j < n and isinstance(messages[j], ToolMessage):
            seen.append(str(getattr(messages[j], "tool_call_id", "") or "").strip())
            j += 1
        if len(seen) < len(ids) or not required.issubset(set(seen)):
            return i
        i = j
    return None


def _truncate_unclosed_tool_call_tail(messages: List[Any]) -> tuple[List[Any], Optional[int]]:
    idx = _first_unclosed_tool_call_index(list(messages or []))
    if idx is None:
        return list(messages or []), None
    return list(messages or [])[:idx], idx


def _trim_unclosed_tool_call_tail_preserve_completed(
    messages: List[Any],
) -> tuple[List[Any], Optional[int]]:
    src = list(messages or [])
    out: List[Any] = []
    i = 0
    changed_at: Optional[int] = None
    while i < len(src):
        msg = src[i]
        ids = _assistant_tool_call_ids(msg)
        if not ids:
            out.append(msg)
            i += 1
            continue

        tool_rows: List[ToolMessage] = []
        j = i + 1
        while j < len(src) and isinstance(src[j], ToolMessage):
            tool_rows.append(src[j])
            j += 1

        seen_ids = {
            str(getattr(t, "tool_call_id", "") or "").strip()
            for t in tool_rows
        }
        if len(tool_rows) >= len(ids) and set(ids).issubset(seen_ids):
            out.extend(src[i:j])
            i = j
            continue

        changed_at = len(out)
        completed_ids = [tid for tid in ids if tid in seen_ids]
        raw_calls = list(getattr(msg, "tool_calls", None) or [])
        kept_calls: List[Any] = []
        if completed_ids:
            completed_set = set(completed_ids)
            for idx, tc in enumerate(raw_calls):
                if isinstance(tc, dict):
                    raw = tc.get("id") or tc.get("tool_call_id") or ""
                else:
                    raw = getattr(tc, "id", "") or getattr(tc, "tool_call_id", "") or ""
                tid = str(raw or "").strip() or f"__missing_tool_call_id_{idx}"
                if tid in completed_set:
                    kept_calls.append(tc)
        if kept_calls:
            out.append(msg.model_copy(update={"tool_calls": kept_calls}))
            out.extend([t for t in tool_rows if str(getattr(t, "tool_call_id", "") or "").strip() in completed_set])
        else:
            content = str(getattr(msg, "content", "") or "")
            additional = getattr(msg, "additional_kwargs", None) or {}
            reasoning = ""
            if isinstance(additional, dict):
                reasoning = str(
                    additional.get("reasoning_content")
                    or additional.get("reasoning")
                    or additional.get("reasoning_text")
                    or ""
                )
            if content or reasoning:
                out.append(msg.model_copy(update={"tool_calls": None}))
        return out, changed_at
    return out, changed_at


def _sanitize_loaded_histories_for_new_run(
    session_id: str,
    work_messages: List[Any],
    llm_history: List[Any],
    key_context: str,
    reason: str,
) -> tuple[List[Any], List[Any]]:
    clean_llm, llm_cut = _truncate_unclosed_tool_call_tail(llm_history)
    clean_work, work_cut = _truncate_unclosed_tool_call_tail(work_messages)
    if llm_cut is None and work_cut is None:
        return work_messages, llm_history
    state: State = {
        "session_id": session_id,
        "work_messages": clean_work,
        "llm_history": clean_llm,
        "key_context": key_context or "",
        "dialogue": derive_dialogue_from_assistant_history(clean_llm),
    }
    logger.warning(
        "Sanitized unclosed tool_call tail before run: session=%s reason=%s llm_cut=%s work_cut=%s",
        session_id,
        reason,
        llm_cut,
        work_cut,
    )
    _persist_state_with_model_replace(state, clean_llm, reason)
    return clean_work, clean_llm


def _load_runtime_v2_model_history_dicts(session_id: str) -> List[Dict[str, Any]]:
    from runtime_v2 import RuntimeModelProjection

    # An empty projection is a valid new session. A projection exception is
    # not: converting it to [] would send an API request with silently lost
    # context and can permanently fork the model history.
    return RuntimeModelProjection(
        session_manager.sessions_dir,
        path_resolver=getattr(session_manager, "_resolve_session_path", None),
    ).read_message_dicts(session_id)


def _load_runtime_v2_run_bootstrap(session_id: str) -> Dict[str, Any]:
    """Load the V2 context summary and model history from one projection."""
    from runtime_v2 import RuntimeModelProjection

    return RuntimeModelProjection(
        session_manager.sessions_dir,
        path_resolver=getattr(session_manager, "_resolve_session_path", None),
    ).read_run_bootstrap(session_id)


def _llm_request_context(session_id: str) -> LLMRequestContext:
    sid = str(session_id or "").strip()
    values: Dict[str, Any] = {
        "session_id": sid,
        "history_generation": 0,
        "purpose": LLMRequestPurpose.MAIN.value,
    }
    if _runtime_v2_is_primary() and sid:
        try:
            from runtime_v2 import RuntimeModelProjection

            values.update(
                RuntimeModelProjection(
                    session_manager.sessions_dir,
                    path_resolver=session_manager._get_session_path,
                ).read_request_context(sid)
            )
        except Exception:
            logger.warning(
                "Runtime V2 LLM request context read failed for %s",
                sid,
                exc_info=True,
            )
    return LLMRequestContext.from_value(values)


def _responses_compacted_input_estimate(
    client: Any,
    model: str,
    request_context: LLMRequestContext,
    full_input_estimate: int,
) -> Optional[int]:
    raw = request_context.responses_compaction
    if not isinstance(raw, Mapping) or not raw:
        return None
    candidate_getter = getattr(client, "next_candidate", None)
    if not callable(candidate_getter):
        candidate_getter = getattr(client, "current_candidate", None)
    candidate = candidate_getter() if callable(candidate_getter) else {}
    transport = candidate.get("transport") if isinstance(candidate, dict) else None
    issuer = str(getattr(transport, "issuer", "") or "")
    active_model = str(
        candidate.get("model") if isinstance(candidate, dict) else ""
    ) or str(model or "")
    if (
        str(raw.get("issuer") or "") != issuer
        or str(raw.get("model") or "") != active_model
        or int(raw.get("source_history_generation") or 0)
        != int(request_context.history_generation)
    ):
        return None
    source_tokens = int(raw.get("source_estimated_tokens") or 0)
    usage = raw.get("usage") if isinstance(raw.get("usage"), Mapping) else {}
    compacted_tokens = int(
        usage.get("completion_tokens")
        or usage.get("output_tokens")
        or 0
    )
    if compacted_tokens <= 0:
        compacted_items = raw.get("compacted_output_items")
        if isinstance(compacted_items, list) and compacted_items:
            compacted_tokens = max(
                1,
                len(
                    json.dumps(
                        compacted_items,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    )
                )
                // 4,
            )
    if source_tokens <= 0 or compacted_tokens <= 0:
        return None
    return max(0, int(full_input_estimate) - source_tokens) + compacted_tokens


def _load_runtime_v2_context_summary(session_id: str) -> str:
    from runtime_v2 import SnapshotStore

    snapshot = SnapshotStore(
        session_manager.sessions_dir,
        path_resolver=getattr(session_manager, "_resolve_session_path", None),
    ).read_consistent_view(session_id)
    context = snapshot.get("context") if isinstance(snapshot, dict) else {}
    summary = context.get("summary") if isinstance(context, dict) else {}
    if isinstance(summary, dict):
        return str(summary.get("summary") or "")
    return ""


def _load_key_context_for_run(session_id: str) -> str:
    if _runtime_v2_is_primary():
        return _load_runtime_v2_context_summary(session_id)
    key_context = session_manager._load_key_context(session_id)
    migrated = _workflow_callbacks().call(
        "migrate_key_context", session_id, key_context
    )
    return str(migrated) if migrated is not None else key_context


def _load_model_history_dicts_v2_primary(session_id: str, *, reconcile_legacy: bool) -> List[Dict[str, Any]]:
    if _runtime_v2_is_primary():
        return _load_runtime_v2_model_history_dicts(session_id)
    if reconcile_legacy:
        session_manager.reconcile_llm_work_to_ui_user_count(session_id, include_work=False)
    return session_manager._load_llm_history(session_id)


def _load_work_history_dicts_for_run(session_id: str) -> List[Dict[str, Any]]:
    if _runtime_v2_is_primary():
        return []
    return session_manager._load_work_messages(session_id)


def _pre_api_timing_mark(timings: Dict[str, int], name: str, start: float) -> None:
    timings[name] = int(max(0.0, (time.perf_counter() - start) * 1000.0))


# Process-level cache for the static system prompt segments, keyed by
# (session_id, is_subagent, has_inherited_segments).  Each value carries the
# revision it was built at, so a revision drift (skill toggle, plugin change)
# can serve the last-known-good segments while rebuilding off the critical
# path (stale-while-revalidate).
_STATIC_SEGMENTS_PROCESS_CACHE: Dict[tuple, tuple] = {}
_STATIC_SEGMENTS_REBUILD_INFLIGHT: set = set()


def _build_static_segments_for_session(
    session_id: str,
    session_meta: Any,
    prompt_language: str,
) -> tuple[str, ...]:
    """Build static system segments exactly as the inline pre_api path would."""

    skills_catalog = get_skills_catalog()
    env_static = build_env_static(session_id or None)
    segments = tuple(build_static_system_segments(
        skills_catalog,
        env_static,
        prompt_language,
    ))
    fork_runtime_config = (
        session_meta.get("fork_runtime_config") if isinstance(session_meta, dict) else None
    )
    inherited = (
        fork_runtime_config.get("system_segments")
        if isinstance(fork_runtime_config, dict)
        else None
    )
    if isinstance(inherited, list) and inherited and all(isinstance(item, str) for item in inherited):
        return tuple(inherited)
    if isinstance(session_meta, dict) and session_meta.get("is_subagent"):
        from agent_subagent import SUBAGENT_RUN_INSTRUCTION

        return ("## Subagent 运行约束\n\n" + SUBAGENT_RUN_INSTRUCTION.strip(), *segments)
    return segments


def _schedule_static_segments_rebuild(
    state: State,
    session_meta: Any,
    prompt_language: str,
    target_revision: tuple,
    static_key: tuple,
) -> None:
    """Rebuild the static system segments off the critical path.

    Called when the cached segments' revision no longer matches.  The rebuild
    lands in ``_STATIC_SEGMENTS_PROCESS_CACHE`` for the next request; only one
    rebuild per cache key is in flight at a time.
    """
    if static_key in _STATIC_SEGMENTS_REBUILD_INFLIGHT:
        return
    _STATIC_SEGMENTS_REBUILD_INFLIGHT.add(static_key)
    sid = str(state.get("session_id") or "")
    rebuild_meta = dict(session_meta) if isinstance(session_meta, Mapping) else None

    async def _rebuild() -> None:
        started = time.perf_counter()
        try:
            segments = await asyncio.to_thread(
                _build_static_segments_for_session, sid, rebuild_meta, prompt_language
            )
            _STATIC_SEGMENTS_PROCESS_CACHE[static_key] = (target_revision, segments)
            logger.info(
                "background static segments rebuilt: session=%s ms=%d",
                sid,
                int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            logger.warning(
                "background static segments rebuild failed: session=%s error=%s",
                sid,
                exc,
            )
        finally:
            _STATIC_SEGMENTS_REBUILD_INFLIGHT.discard(static_key)

    state["_static_segments_revalidate_task"] = asyncio.create_task(_rebuild())


def _pre_api_timing_log(session_id: str, timings: Dict[str, int], **extra: Any) -> None:
    try:
        # build_messages is an aggregate (static_segments + turn_cache); it is
        # printed but excluded from the total to avoid double counting.
        total = int(sum(int(v or 0) for k, v in timings.items() if k != "build_messages"))
        parts = [f"{k}={int(v)}ms" for k, v in timings.items()]
        for k, v in extra.items():
            parts.append(f"{k}={v}")
        logger.info("pre_api_timing session=%s total=%sms %s", session_id, total, " ".join(parts))
    except Exception:
        logger.debug("pre_api_timing log failed", exc_info=True)


def _gc_probe_extras(state: State) -> Dict[str, Any]:
    """Gen2/GC deltas + event-loop stall + inter-round wall gap, since the last tool_to_next_api anchor."""
    extras: Dict[str, Any] = {}
    round_gap = int(state.get("_last_round_gap_ms") or 0)
    if round_gap:
        extras["round_gap_ms"] = round_gap
    anchor = state.pop("_gc_round_anchor", None)
    loop_anchor = state.pop("_loop_stall_anchor", None)
    if loop_anchor:
        try:
            wall_delta = time.perf_counter() - loop_anchor[0]
            loop_delta = asyncio.get_running_loop().time() - loop_anchor[1]
            extras["loop_stall_ms"] = int(
                max(0, round((wall_delta - loop_delta) * 1000))
            )
        except Exception:
            pass
    if not anchor:
        return extras
    try:
        stats = gc.get_stats()
        extras["gc2_runs"] = max(0, int(stats[2]["collections"]) - int(anchor[0]))
        extras["gc2_objs"] = max(0, int(stats[2]["collected"]) - int(anchor[1]))
        extras["gc0_runs"] = max(0, int(stats[0]["collections"]) - int(anchor[2]))
    except Exception:
        pass
    return extras


def _timing_ms(start: float, end: Optional[float] = None) -> int:
    if end is None:
        end = time.perf_counter()
    return int(max(0.0, (end - start) * 1000.0))


def _pipeline_timing_log(label: str, session_id: str, timings: Dict[str, int], **extra: Any) -> None:
    try:
        total = int(sum(int(v or 0) for v in timings.values()))
        parts = [f"{k}={int(v)}ms" for k, v in timings.items()]
        for k, v in extra.items():
            parts.append(f"{k}={v}")
        logger.info("%s session=%s total=%sms %s", label, session_id, total, " ".join(parts))
    except Exception:
        logger.debug("%s log failed", label, exc_info=True)


def _pipeline_step_timing_log(label: str, session_id: str, step: str, ms: int, **extra: Any) -> None:
    # Step timings are accumulated by the caller and emitted through the
    # corresponding phase-level *_timing log. Keep this as a compatibility
    # shim for older call sites without adding one log line per step.
    return


def _llm_stream_timing_log(
    session_id: str,
    react_iter: int,
    model: str,
    timings: List[Dict[str, Any]],
) -> None:
    if not timings:
        return
    try:
        parts: List[str] = []
        total_since_api = 0
        for item in timings:
            step = str(item.get("step") or "").strip()
            if not step:
                continue
            try:
                ms_since_api_start = int(float(item.get("ms_since_api_start") or 0))
            except Exception:
                ms_since_api_start = 0
            total_since_api = max(total_since_api, ms_since_api_start)
            extras: List[str] = []
            for k, v in item.items():
                if k in {"step", "ms_since_api_start", "model"} or v is None:
                    continue
                extras.append(f"{k}={v}")
            detail = f"{step}@{max(0, ms_since_api_start)}ms"
            if extras:
                detail += "(" + ",".join(extras) + ")"
            parts.append(detail)
        if not parts:
            return
        logger.info(
            "llm_stream_timing session=%s react_iter=%s total_since_api=%sms model=%s steps=%s",
            session_id,
            int(react_iter),
            max(0, total_since_api),
            redact_sensitive_tool_text(str(model or "")),
            " ".join(parts),
        )
    except Exception:
        logger.debug("llm_stream_timing log failed", exc_info=True)


def _runtime_v2_react_transaction_timeout_seconds() -> Optional[float]:
    from runtime_v2 import runtime_v2_react_transaction_timeout_seconds

    return runtime_v2_react_transaction_timeout_seconds()


def _runtime_v2_react_history_ops():
    from runtime_v2 import RuntimeHistoryOps

    return RuntimeHistoryOps(
        session_manager.sessions_dir,
        path_resolver=getattr(session_manager, "_resolve_session_path", None),
        transaction_timeout_seconds=_runtime_v2_react_transaction_timeout_seconds(),
    )


def _runtime_v2_append_model_message(state: State, msg: Any) -> None:
    sid = str(state.get("session_id") or "").strip()
    if not sid or not _runtime_v2_is_primary():
        return
    if not _state_run_has_write_fence(state):
        return
    t0 = time.perf_counter()
    role = ""
    try:
        t_pre_dict = time.perf_counter()
        data = _message_to_dict(msg)
        t_post_dict = time.perf_counter()
        msg_type = str(data.get("type") or "").strip()
        role = {
            "human": "user",
            "llm": "assistant",
            "ai": "assistant",
            "agent": "assistant",
        }.get(msg_type, msg_type)
        if role not in {"user", "assistant", "tool", "system"}:
            return
        payload = dict(data)
        content = str(payload.pop("content", "") or "")
        payload.pop("type", None)
        run_id = str(state.get("_runtime_v2_run_id") or "").strip()
        if run_id:
            payload["run_id"] = run_id
        t_pre_call = time.perf_counter()
        state["_runtime_stage"] = "persist_model_message"
        logger.info(
            "runtime_v2_write_started session=%s op=append_model_message role=%s",
            sid,
            role,
        )
        _runtime_v2_react_history_ops().append_model_message(
            sid,
            role,
            content,
            **payload,
        )
        t_post_call = time.perf_counter()
        step_dict_ms = _timing_ms(t_pre_dict, t_post_dict)
        step_outer_overhead_ms = _timing_ms(t_post_dict, t_pre_call)
        step_inner_total_ms = _timing_ms(t_pre_call, t_post_call)
        logger.info(
            "runtime_v2_write_timing session=%s op=append_model_message role=%s ms=%s "
            "step_dict_ms=%s step_outer_overhead_ms=%s step_inner_total_ms=%s",
            sid,
            role,
            _timing_ms(t0),
            step_dict_ms,
            step_outer_overhead_ms,
            step_inner_total_ms,
        )
        state["_runtime_stage"] = "react"
    except Exception as exc:
        state["_runtime_stage"] = "react"
        logger.warning("Runtime V2 model append failed for %s: %s", sid, exc)
        raise


_EXPLICIT_USER_INTERRUPT_REASONS = {"user", "user_button", "user_cancelled"}


def _interrupt_terminal_text(session_id: str, *, parent: bool = False) -> str:
    try:
        reason = str(session_manager.get_interrupt_reason(session_id) or "unspecified").strip()
    except Exception:
        reason = "unspecified"
    if reason in _EXPLICIT_USER_INTERRUPT_REASONS:
        return "任务已由用户中断（父会话）。" if parent else "任务已由用户中断。"
    return "任务因 Agent 停止、重启或运行中断而暂停，可在服务恢复后继续。"


def _runtime_v2_commit_user_turn(
    state: State,
    msg: Any,
    *,
    ui_content: str,
    ui_type: str = "user",
    operation_id: str = "",
    ui_metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    sid = str(state.get("session_id") or "").strip()
    if not sid or not _runtime_v2_is_primary():
        return False
    try:
        data = _message_to_dict(msg)
        model_content = str(data.pop("content", "") or "")
        data.pop("type", None)
        ui_event_metadata = dict(ui_metadata or {})
        if ui_type == "user_steer":
            op_id = str(operation_id or "").strip()
            if op_id:
                ui_event_metadata.setdefault("steer_id", op_id)
                try:
                    steer_status = get_session_steer(sid, steer_id=op_id)
                    steer_item = steer_status.get("item") if isinstance(steer_status.get("item"), dict) else {}
                except Exception:
                    steer_item = {}
                if steer_item:
                    ui_event_metadata.setdefault("client_id", str(steer_item.get("client_id") or ""))
                    ui_event_metadata.setdefault("steer_mode", str(steer_item.get("mode") or "interrupt"))
            for key in ("steer_id", "client_id", "steer_mode"):
                value = str(ui_event_metadata.get(key) or "").strip()
                if value:
                    data[key] = value
        run_id = str(state.get("_runtime_v2_run_id") or "").strip()
        committed_event = _runtime_v2_react_history_ops().commit_user_turn(
            sid,
            model_content,
            ui_content=ui_content,
            ui_type=ui_type,
            operation_id=operation_id or (f"user:{run_id}" if run_id else ""),
            run_id=run_id or None,
            model_payload=data,
        )
        state["_last_user_turn_was_deduplicated"] = committed_event is None and bool(operation_id)
        side_effects = getattr(session_manager, "_apply_appended_ui_event_side_effects", None)
        side_effect_event = {
            "type": ui_type,
            "content": ui_content,
            "steer": ui_type == "user_steer",
            **{key: value for key, value in ui_event_metadata.items() if value not in (None, "")},
        }
        if callable(side_effects):
            side_effects(sid, side_effect_event)
        else:
            # Lightweight/test SessionManager implementations may expose only
            # append_ui_event. Production uses the side-effect-only path above
            # to avoid duplicating the already committed Runtime V2 event.
            append_ui = getattr(session_manager, "append_ui_event", None)
            if callable(append_ui):
                append_ui(sid, side_effect_event)
        return True
    except Exception as exc:
        logger.warning("Runtime V2 atomic user turn commit failed for %s: %s", sid, exc)
        raise


def _snapshot_assistant_ui_content(session_id: str, content: str) -> str:
    try:
        return snapshot_workspace_images(str(content or ""), WORK_DIR)
    except Exception as exc:
        logger.warning("Workspace image snapshot failed for %s: %s", session_id, exc)
        return str(content or "")


def _runtime_v2_commit_assistant_final(
    state: State,
    content: str,
    *,
    ui_content: Optional[str] = None,
) -> bool:
    sid = str(state.get("session_id") or "").strip()
    if not sid or not _runtime_v2_is_primary():
        return False
    if not _state_run_has_write_fence(state):
        return False
    try:
        run_id = str(state.get("_runtime_v2_run_id") or "").strip()
        _runtime_v2_react_history_ops().commit_assistant_final(
            sid,
            str(content or ""),
            ui_content=ui_content,
            operation_id=f"final:{run_id}" if run_id else "",
            run_id=run_id or None,
            model_payload={"metadata": {"is_final": True}},
        )
        side_effects = getattr(session_manager, "_apply_appended_ui_event_side_effects", None)
        event = {
            "type": "final",
            "content": str(ui_content if ui_content is not None else content or ""),
        }
        if callable(side_effects):
            side_effects(sid, event)
        else:
            append_ui = getattr(session_manager, "append_ui_event", None)
            if callable(append_ui):
                append_ui(sid, event)
        return True
    except Exception as exc:
        logger.warning("Runtime V2 atomic final commit failed for %s: %s", sid, exc)
        raise


def _runtime_v2_replace_model_history(state: State, messages: List[Any], reason: str) -> None:
    sid = str(state.get("session_id") or "").strip()
    if not sid or not _runtime_v2_is_primary():
        return
    if not _state_run_has_write_fence(state):
        return
    t0 = time.perf_counter()
    try:
        event = _runtime_v2_react_history_ops().reconcile_model_history(
            sid,
            [_message_to_dict(m) for m in list(messages or [])],
            reason=reason,
            summary=str(state.get("key_context") or ""),
        )
        logger.info(
            "runtime_v2_write_timing session=%s op=reconcile_model_history event=%s reason=%s messages=%s ms=%s",
            sid,
            getattr(event, "type", "unchanged") if event is not None else "unchanged",
            reason,
            len(list(messages or [])),
            _timing_ms(t0),
        )
    except Exception as exc:
        logger.warning("Runtime V2 model reconciliation failed for %s: %s", sid, exc)
        raise


def _runtime_v2_commit_context_summary(state: State) -> None:
    sid = str(state.get("session_id") or "").strip()
    summary = str(state.get("key_context") or "")
    if not sid or not _runtime_v2_is_primary():
        return
    try:
        from runtime_v2 import SnapshotStore

        resolver = getattr(session_manager, "_resolve_session_path", None)
        # read_consistent_view returns the published projection without the
        # full deep copy; we only compare the summary string here, and every
        # persist in a ReAct run pays this path.
        snapshot = SnapshotStore(
            session_manager.sessions_dir,
            path_resolver=resolver,
        ).read_consistent_view(sid)
        current = snapshot.get("context", {}).get("summary", {}) if isinstance(snapshot, dict) else {}
        if isinstance(current, dict) and str(current.get("summary") or "") == summary:
            return
        _runtime_v2_react_history_ops().commit_context_summary(sid, summary)
    except Exception as exc:
        logger.warning("Runtime V2 context summary commit failed for %s: %s", sid, exc)
        raise


def _runtime_v2_commit_responses_compaction(
    state: State,
    checkpoint: Any,
    *,
    reason: str,
) -> None:
    sid = str(state.get("session_id") or "").strip()
    if not sid or not _runtime_v2_is_primary() or not _state_run_has_write_fence(state):
        raise RuntimeError("Responses compaction requires an active Runtime V2 write fence")
    payload = checkpoint.to_dict() if hasattr(checkpoint, "to_dict") else dict(checkpoint or {})
    _runtime_v2_react_history_ops().commit_responses_compaction(
        sid,
        payload,
        reason=reason,
    )


def _runtime_v2_checkpoint_context_tokens(state: State, payload: Dict[str, Any]) -> None:
    sid = str(state.get("session_id") or "").strip()
    if not sid or not _runtime_v2_is_primary() or not _state_run_has_write_fence(state):
        return
    try:
        _runtime_v2_react_history_ops().checkpoint_context_tokens(sid, payload)
    except Exception as exc:
        logger.warning("Runtime V2 context token checkpoint failed for %s: %s", sid, exc)
        raise


def _runtime_v2_is_primary() -> bool:
    try:
        from runtime_v2 import runtime_v2_primary

        return runtime_v2_primary()
    except Exception:
        return True


def _persist_state_with_model_append(state: State, msg: Any) -> None:
    if _runtime_v2_is_primary():
        _runtime_v2_append_model_message(state, msg)
        _persist_state(state)
    else:
        _persist_state(state)


def _persist_state_with_model_replace(state: State, messages: List[Any], reason: str) -> None:
    if _runtime_v2_is_primary():
        _runtime_v2_replace_model_history(state, messages, reason)
        _persist_state(state)
    else:
        _persist_state(state)


def _persist_session_messages_with_model_replace(state: State, messages: List[Any], reason: str) -> None:
    if _runtime_v2_is_primary():
        _runtime_v2_replace_model_history(state, messages, reason)
        _persist_session_messages(state)
    else:
        _persist_session_messages(state)


def _materialize_lazy_work_messages(state: State) -> None:
    if not state.pop("_lazy_prepend_work_messages", False):
        return
    if _runtime_v2_is_primary():
        return
    sid = str(state.get("session_id") or "").strip()
    suffix = list(state.get("work_messages", []))
    if not sid:
        state["work_messages"] = suffix
        return
    try:
        prev = [_dict_to_message(m) for m in session_manager._load_work_messages(sid)]
    except Exception as e:
        logger.warning("lazy load work_messages failed: %s", e)
        prev = []
    if prev and suffix:
        try:
            p = prev[-1]
            s = suffix[0]
            if type(p) is type(s) and getattr(p, "content", None) == getattr(s, "content", None):
                suffix = suffix[1:]
        except Exception:
            pass
    state["work_messages"] = prev + suffix


async def _push_stream_event(
    state: State,
    event: Dict[str, Any],
    emit: Optional[Callable[[Dict[str, Any]], Any]] = None,
):
    """追加 stream_events；若提供 emit（async 可调用），则同步推给前端。"""
    if not _state_run_has_write_fence(state):
        logger.info(
            "suppressed stale run event: session=%s run=%s type=%s",
            state.get("session_id"), state.get("_runtime_v2_run_id"), event.get("type"),
        )
        return
    state["stream_events"].append(event)
    if emit:
        try:
            r = emit(event)
            if inspect.isawaitable(r):
                await r
        except Exception:
            pass


def _set_model_switch_status_callback(
    client: Any,
    callback: Optional[Callable[[Dict[str, Any]], None]],
) -> None:
    setter = getattr(client, "set_status_callback", None)
    if not callable(setter):
        return
    try:
        setter(callback)
    except Exception:
        logger.debug("设置模型切换状态回调失败", exc_info=True)


def _should_suppress_model_switch_status(state: State, event: Dict[str, Any]) -> bool:
    if not isinstance(event, dict) or not event.get("model_switch"):
        return False
    if not event.get("network_error"):
        return False
    return int(state.get("_network_reconnect_attempts", 0) or 0) > 0


def _queue_get_with_timeout(q: queue.Queue, timeout: float):
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return ("__timeout__", None)


class _ThreadToAsyncQueue:
    """Bridge worker-thread events while coalescing text within one UI frame."""

    _TEXT_FLUSH_SECONDS = max(
        0.001,
        min(0.016, float(os.getenv("LLM_STREAM_COALESCE_MS", "12")) / 1000.0),
    )

    def __init__(self, loop: asyncio.AbstractEventLoop, target: asyncio.Queue):
        self._loop = loop
        self._target = target
        self._lock = threading.Lock()
        self._pending_text: List[Tuple[str, str]] = []
        self._flush_scheduled = False
        self._flush_handle: Optional[asyncio.Handle] = None
        self._sent_first_text = False

    def _drain_pending(self) -> List[Tuple[str, str]]:
        with self._lock:
            rows = self._pending_text
            self._pending_text = []
            self._flush_scheduled = False
            self._flush_handle = None
        return rows

    def _flush_text(self) -> None:
        for row in self._drain_pending():
            self._target.put_nowait(row)

    def _arm_delayed_flush(self) -> None:
        with self._lock:
            if not self._pending_text:
                self._flush_scheduled = False
                return
            self._flush_handle = self._loop.call_later(
                self._TEXT_FLUSH_SECONDS,
                self._flush_text,
            )

    def _flush_then_put(self, item: Any) -> None:
        handle = self._flush_handle
        if handle is not None:
            handle.cancel()
        self._flush_text()
        self._target.put_nowait(item)

    def put(self, item: Any) -> None:
        try:
            is_text = (
                isinstance(item, tuple)
                and len(item) >= 2
                and item[0] in {"reasoning", "content"}
                and isinstance(item[1], str)
            )
            if not is_text:
                self._loop.call_soon_threadsafe(self._flush_then_put, item)
                return
            tag, payload = item[0], item[1]
            with self._lock:
                if self._pending_text and self._pending_text[-1][0] == tag:
                    previous_tag, previous_payload = self._pending_text[-1]
                    self._pending_text[-1] = (previous_tag, previous_payload + payload)
                else:
                    self._pending_text.append((tag, payload))
                if self._flush_scheduled:
                    return
                self._flush_scheduled = True
                first_text = not self._sent_first_text
                self._sent_first_text = True
            if first_text:
                # Preserve time-to-first-token; only subsequent deltas are
                # coalesced inside the frontend's existing frame budget.
                self._loop.call_soon_threadsafe(self._flush_text)
            else:
                self._loop.call_soon_threadsafe(self._arm_delayed_flush)
        except RuntimeError:
            pass


def _discard_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.debug("background task finished after cancellation", exc_info=True)


async def _await_maybe(awaitable_or_value):
    if inspect.isawaitable(awaitable_or_value):
        return await awaitable_or_value
    return awaitable_or_value


async def _run_react_node_off_loop(
    state: State,
    emit: Optional[Callable[[Dict[str, Any]], Any]],
) -> State:
    if emit is None:
        return await asyncio.to_thread(lambda: asyncio.run(react_node(state, emit=None)))
    main_loop = asyncio.get_running_loop()

    async def bridged_emit(ev: Dict[str, Any]) -> None:
        fut = asyncio.run_coroutine_threadsafe(_await_maybe(emit(ev)), main_loop)
        await asyncio.wrap_future(fut)

    return await asyncio.to_thread(lambda: asyncio.run(react_node(state, emit=bridged_emit)))


def _steer_control_from_state(state: State) -> Optional[_SteerRunControl]:
    control = state.get("_steer_control") if isinstance(state, dict) else None
    if isinstance(control, _SteerRunControl):
        return control
    return None


def _steer_requested(state: State) -> bool:
    sid = str(state.get("session_id") or "").strip() if isinstance(state, dict) else ""
    control = _steer_control_from_state(state)
    # Append-mode steers are deliberately invisible to interruption polling.
    # They are claimed only at a completed ReAct boundary, after any tool
    # results have been persisted and before the next model request is built.
    return bool(
        (control and control.is_aborted())
        or _has_session_steers(sid, modes={"interrupt"})
    )


def _reset_steer_control(state: State) -> None:
    control = _steer_control_from_state(state)
    if control:
        control.reset()
    if isinstance(state, dict):
        state.pop("_steer_abort_event_emitted", None)


async def _emit_steer_abort_event(
    state: State,
    emit: Optional[Callable[[Dict[str, Any]], Any]],
    stage: str,
    *,
    checkpoint_ok: bool = True,
    cleanup_scope: str = "drafts_only",
) -> None:
    if not isinstance(state, dict):
        return
    if state.get("_steer_abort_event_emitted"):
        return
    state["_steer_abort_event_emitted"] = True
    event: Dict[str, Any] = {
        "type": "llm_stream_aborted",
        "reason": "user_steer",
        "stage": str(stage or "react"),
        "checkpoint_ok": bool(checkpoint_ok),
        "cleanup_scope": (
            "drafts_only" if str(cleanup_scope or "") == "drafts_only" else "none"
        ),
        "ephemeral": True,
    }
    react_iter = state.get("_current_react_iter")
    try:
        if react_iter is not None:
            event["react_iter"] = int(react_iter)
    except Exception:
        pass
    try:
        await prune_session_ephemeral(
            str(state.get("session_id") or ""),
            types={
                "tool_pending",
                "tool_call_delta",
                "tool_command_delta",
                "llm_reasoning_delta",
                "llm_response_delta",
                "context_trim_delta",
                "context_summary_delta",
                "key_context_delta",
            },
            run_id=state.get("_runtime_v2_run_id"),
        )
    except Exception:
        logger.debug("failed to prune aborted tool stream ephemerals", exc_info=True)
    await _push_stream_event(
        state,
        event,
        emit=emit,
    )


async def _raise_if_steer_requested(
    state: State,
    emit: Optional[Callable[[Dict[str, Any]], Any]],
    stage: str,
) -> None:
    if not _steer_requested(state):
        return
    await _emit_steer_abort_event(state, emit, stage)
    raise _SteerRestartRequested()


async def _await_steerable(
    state: State,
    awaitable,
    emit: Optional[Callable[[Dict[str, Any]], Any]],
    stage: str,
    poll_sec: float = 0.05,
    defer_steer: bool = False,
):
    try:
        await _raise_if_steer_requested(state, emit, stage)
    except _SteerRestartRequested:
        close_fn = getattr(awaitable, "close", None)
        if callable(close_fn):
            close_fn()
        raise
    task = asyncio.ensure_future(awaitable)
    if defer_steer:
        # Serialized write tools may have irreversible side effects. Once they
        # have started, preserve their real result and apply the steer at the
        # next checkpoint instead of pretending cancellation rolled them back.
        deferred_seen = False
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=poll_sec)
                if task in done:
                    return task.result()
                if _steer_requested(state) and not deferred_seen:
                    deferred_seen = True
                    _set_session_steers_deferred(str(state.get("session_id") or ""), True)
        finally:
            if deferred_seen:
                _set_session_steers_deferred(str(state.get("session_id") or ""), False)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=poll_sec)
            if task in done:
                return task.result()
            await _raise_if_steer_requested(state, emit, stage)
    except _SteerRestartRequested:
        if not task.done():
            task.add_done_callback(_discard_task_result)
            task.cancel()
        raise


def _model_tool_definition_name(definition: Any) -> str:
    if not isinstance(definition, dict):
        return ""
    function = definition.get("function")
    if not isinstance(function, dict):
        return ""
    return str(function.get("name") or "").strip()


async def _combined_tool_registry_revision(
    session_meta: Mapping[str, Any],
) -> tuple[Any, ...]:
    """Return cheap generations for every source that shapes the catalog."""
    from agent_extensions import extension_catalog_generation

    mcp_revision = await agent_mcp.get_tool_catalog_revision()
    session_shape = json.dumps(
        {
            "is_subagent": bool(session_meta.get("is_subagent")),
            "agent_team_member_id": session_meta.get("agent_team_member_id"),
            "subagent_depth": session_meta.get("subagent_depth"),
            "readonly_strict": session_meta.get("readonly_strict"),
            "fork_runtime_config": session_meta.get("fork_runtime_config"),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return (
        mcp_revision,
        extension_catalog_generation(),
        host_tool_invokers.catalog_revision(),
        executor_config_generation(),
        session_shape,
    )


def _short_registry_revision(revision: Any) -> str:
    """Compact repr of a combined registry revision for drift diagnostics."""
    try:
        parts = [str(item) for item in tuple(revision)]
    except Exception:
        return repr(revision)[:120]
    return "|".join(part[:48] for part in parts)


def _schedule_tool_registry_revalidate(
    state: State,
    session_meta: Mapping[str, Any],
    target_revision: tuple[Any, ...],
) -> bool:
    """Rebuild the combined tool registry off the critical path.

    Called when the cached registry's revision no longer matches.  Returns
    True so the caller may serve the stale catalog for the current request;
    the rebuild lands in ``_combined_tool_registry_cache`` for the next one.
    Only one rebuild per session state is in flight at a time.
    """
    sid = str(state.get("session_id") or "").strip()
    if not sid or state.get("_tool_registry_revalidating"):
        return True
    state["_tool_registry_revalidating"] = True
    rebuild_meta = dict(session_meta) if isinstance(session_meta, Mapping) else None

    async def _rebuild() -> None:
        started = time.perf_counter()
        try:
            registry = await build_combined_tool_registry_for_session(
                sid,
                session_meta=rebuild_meta,
            )
            state["_combined_tool_registry_cache"] = {
                "revision": target_revision,
                "registry": registry,
                "definitions": registry.definitions(),
            }
            logger.info(
                "background tool registry rebuilt: session=%s ms=%d",
                sid,
                int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            logger.warning(
                "background tool registry rebuild failed: session=%s error=%s",
                sid,
                exc,
            )
        finally:
            state.pop("_tool_registry_revalidating", None)

    task = asyncio.create_task(_rebuild())
    state["_tool_registry_revalidate_task"] = task
    return True


async def build_combined_tool_registry_for_session(
    session_id: str,
    *,
    session_meta: Optional[Dict[str, Any]] = None,
    awaiter: Optional[Callable[[Any, str], Any]] = None,
    timing_callback: Optional[Callable[[str, float], None]] = None,
    include_mcp: bool = True,
) -> ToolRegistry:
    """Build the exact source-aware tool catalog for a main-model request.

    The context-token preview and the live ReAct request share this builder so
    tool schemas cannot silently disappear from the pre-send estimate.  Tool
    ownership is recorded at registration time; execution must not infer it
    from a model-visible function-name prefix.  ``include_mcp=False`` skips
    the MCP section entirely: on a cold process start the stdio servers may
    take tens of seconds to start, and the request must not block on them —
    a background rebuild picks the MCP tools up once they are registered.
    """
    sid = str(session_id or "").strip()
    if session_meta is None:
        try:
            session_meta = session_manager._load_metadata(sid) or {}
        except Exception:
            session_meta = {}
    if not isinstance(session_meta, dict):
        session_meta = {}

    combined_tools: List[Dict[str, Any]] = list(OPENAI_TOOL_DEFINITIONS)
    source_by_name: Dict[str, tuple[ToolInvocationKind, str]] = {}
    plugin_contracts_by_name: Dict[str, Dict[str, Any]] = {}

    def _remember_sources(
        definitions: List[Dict[str, Any]],
        invocation_kind: ToolInvocationKind,
        owner: str,
    ) -> None:
        for definition in definitions:
            name = _model_tool_definition_name(definition)
            if name:
                source_by_name.setdefault(name, (invocation_kind, owner))

    for definition in combined_tools:
        name = _model_tool_definition_name(definition)
        if not name:
            continue
        if host_tool_invokers.has(name):
            source_by_name[name] = (
                ToolInvocationKind.HOST_SERVICE,
                host_tool_invokers.owner(name),
            )
        elif name in tools_dict:
            source_by_name[name] = (ToolInvocationKind.HOST, "core.tools")
        else:
            source_by_name[name] = (ToolInvocationKind.HOST_SERVICE, "core.services")
    combined_tools = [
        item
        for item in combined_tools
        if not (
            host_tool_invokers.has(_model_tool_definition_name(item))
            and not host_tool_invokers.is_enabled(_model_tool_definition_name(item))
        )
    ]

    try:
        from agent_extensions import bundled_host_tool_definitions

        bundled_tools = bundled_host_tool_definitions(session_meta=session_meta)
        combined_tools.extend(bundled_tools)
        for definition in bundled_tools:
            name = _model_tool_definition_name(definition)
            if name and host_tool_invokers.has(name) and host_tool_invokers.is_enabled(name):
                source_by_name[name] = (
                    ToolInvocationKind.HOST_SERVICE,
                    host_tool_invokers.owner(name),
                )
    except Exception as exc:
        logger.warning("bundled host tool definitions failed and were skipped: %s", exc)

    async def _load_optional_tools(
        timing_stage: str,
        factory,
        *,
        await_stage: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        started = time.perf_counter()
        try:
            pending = factory()
            rows = (
                await awaiter(pending, await_stage or timing_stage)
                if awaiter is not None
                else await pending
            )
            return [dict(item) for item in (rows or []) if isinstance(item, dict)]
        except _SteerRestartRequested:
            raise
        except Exception as exc:
            logger.warning(
                "%s tool definitions failed and were skipped: %s",
                timing_stage,
                exc,
            )
            return []
        finally:
            if timing_callback is not None:
                timing_callback(timing_stage, started)

    mcp_tools: List[Dict[str, Any]] = []
    if include_mcp:
        mcp_tools = await _load_optional_tools(
            "mcp_tool_definitions",
            agent_mcp.get_tool_definitions,
            await_stage="tool_definitions",
        )
    _remember_sources(mcp_tools, ToolInvocationKind.MCP, "mcp")
    combined_tools.extend(mcp_tools)
    try:
        from agent_extensions import plugin_tool_definitions

        plugin_tools = await _load_optional_tools(
            "plugin_tool_definitions", plugin_tool_definitions
        )
        from agent_extensions import plugin_tool_contracts

        try:
            plugin_contracts_by_name = await plugin_tool_contracts()
        except Exception as exc:
            logger.warning("plugin tool contracts failed and were skipped: %s", exc)
        _remember_sources(plugin_tools, ToolInvocationKind.PLUGIN, "plugins")
        combined_tools.extend(plugin_tools)
    except _SteerRestartRequested:
        raise
    except Exception as exc:
        logger.warning("plugin_tool_definitions tool definitions failed and were skipped: %s", exc)

    started = time.perf_counter()
    try:
        from agent_subagent import filter_tools_for_session, inject_task_model_profiles

        combined_tools = filter_tools_for_session(combined_tools, session_meta)
        combined_tools = inject_task_model_profiles(combined_tools)
    except Exception as exc:
        logger.warning("subagent tool filtering failed and was skipped: %s", exc)
    finally:
        if timing_callback is not None:
            timing_callback("subagent_tool_filter", started)

    fork_runtime_config = session_meta.get("fork_runtime_config")
    inherited_tools = (
        fork_runtime_config.get("tools")
        if isinstance(fork_runtime_config, dict)
        else None
    )
    if isinstance(inherited_tools, list) and inherited_tools:
        try:
            combined_tools = json.loads(json.dumps(inherited_tools, ensure_ascii=False))
        except (TypeError, ValueError):
            logger.warning(
                "Ignoring invalid inherited fork tool definitions for session=%s",
                sid,
            )

    registry = ToolRegistry()
    for definition in combined_tools:
        name = _model_tool_definition_name(definition)
        invocation_kind, owner = source_by_name.get(
            name,
            (ToolInvocationKind.UNAVAILABLE, "inherited"),
        )
        policy = ToolExecutionPolicy()
        if invocation_kind is ToolInvocationKind.HOST_SERVICE and host_tool_invokers.has(name):
            policy = host_tool_invokers.policy(name)
        elif invocation_kind is ToolInvocationKind.HOST:
            policy = builtin_tool_policy(name)
        elif invocation_kind is ToolInvocationKind.PLUGIN:
            policy = plugin_tool_policy(
                (plugin_contracts_by_name.get(name) or {}).get("effect", "")
            )
        try:
            registry.register_definition(
                definition,
                invocation_kind=invocation_kind,
                owner=owner,
                invoker_id=(
                    name
                    if invocation_kind is ToolInvocationKind.HOST_SERVICE
                    and host_tool_invokers.has(name)
                    else ""
                ),
                emit_pending=(
                    host_tool_invokers.emits_pending(name)
                    if invocation_kind is ToolInvocationKind.HOST_SERVICE
                    and host_tool_invokers.has(name)
                    else True
                ),
                effect=(
                    policy.effect
                ),
                parallel_safe=policy.parallel_safe,
                pressure_limited=policy.pressure_limited,
                interactive=policy.interactive,
                early_stream_safe=policy.early_stream_safe,
                interruptibility=policy.interruptibility,
            )
        except DuplicateToolError as exc:
            # A later optional source must never shadow an already registered
            # capability.  Keep the first registration and fail the conflict
            # closed without breaking the rest of the Agent request.
            logger.warning("Duplicate tool registration was skipped: %s", exc)
        except ToolRegistryError as exc:
            logger.warning("Invalid tool registration was skipped: %s", exc)
    return registry


async def build_combined_tool_definitions_for_session(
    session_id: str,
    *,
    session_meta: Optional[Dict[str, Any]] = None,
    awaiter: Optional[Callable[[Any, str], Any]] = None,
    timing_callback: Optional[Callable[[str, float], None]] = None,
) -> List[Dict[str, Any]]:
    """Compatibility wrapper returning only model-facing JSON definitions."""

    registry = await build_combined_tool_registry_for_session(
        session_id,
        session_meta=session_meta,
        awaiter=awaiter,
        timing_callback=timing_callback,
        # Callers of this wrapper sit on user-facing paths (token preview);
        # never block them on stdio MCP startup — the background rebuild adds
        # the MCP tools once servers register.
        include_mcp=agent_mcp.mcp_started(),
    )
    return registry.definitions()


async def _await_retry_delay_or_interrupt(
    state: State,
    emit: Optional[Callable[[Dict[str, Any]], Any]],
    delay_sec: float,
) -> bool:
    """Return False when the current run should stop instead of retrying."""
    sid = str(state.get("session_id") or "").strip()
    deadline = time.monotonic() + max(0.0, float(delay_sec or 0.0))
    while time.monotonic() < deadline:
        await _raise_if_steer_requested(state, emit, "network_reconnect")
        if sid and session_manager.is_interrupt_requested(sid):
            return False
        await asyncio.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
    return True


async def _wait_for_local_network_recovery(
    state: State,
    emit: Optional[Callable[[Dict[str, Any]], Any]],
    poll_seconds: float = 15.0,
) -> bool:
    """Sleep without model retries only while the local machine is offline."""
    sid = str(state.get("session_id") or "").strip()
    state["_runtime_stage"] = "network_waiting"
    announced_at = 0.0
    while True:
        await _raise_if_steer_requested(state, emit, "network_waiting")
        if sid and session_manager.is_interrupt_requested(sid):
            return False
        recovered = await asyncio.to_thread(machine_network_available)
        if recovered:
            state["_runtime_stage"] = "react"
            if emit:
                await _push_stream_event(
                    state,
                    {
                        "type": "status",
                        "content": "本机网络已恢复，正在继续任务…",
                        "network_recovered": True,
                        "ephemeral": True,
                    },
                    emit=emit,
                )
            return True
        now = time.monotonic()
        if emit and (not announced_at or now - announced_at >= 60.0):
            announced_at = now
            await _push_stream_event(
                state,
                {
                    "type": "status",
                    "content": "本机仍处于离线状态，Agent 正在沉睡并等待网络恢复…",
                    "network_waiting": True,
                    "local_network_offline": True,
                    "ephemeral": True,
                },
                emit=emit,
            )
        if not await _await_retry_delay_or_interrupt(state, emit, poll_seconds):
            return False


def _rollback_steer_partial_turn(state: State) -> None:
    marker = state.pop("_steer_rollback_marker", None) if isinstance(state, dict) else None
    if not isinstance(marker, dict):
        return
    llm_history = list(state.get("llm_history", []))
    work_messages = list(state.get("work_messages", []))
    llm_history, llm_cut = _trim_unclosed_tool_call_tail_preserve_completed(llm_history)
    work_messages, work_cut = _trim_unclosed_tool_call_tail_preserve_completed(work_messages)
    if llm_cut is None and work_cut is None:
        return
    kept_tool_ids = _completed_tool_call_ids_from_messages(llm_history)
    state["llm_history"] = llm_history
    state["work_messages"] = work_messages
    state["dialogue"] = derive_dialogue_from_assistant_history(llm_history)
    _persist_state_with_model_replace(state, llm_history, "steer_restart_trim_unclosed_tools")
    _runtime_v2_delete_unfinished_tool_events_after_marker(state, marker, kept_tool_ids)


def _completed_tool_call_ids_from_messages(messages: List[Any]) -> set[str]:
    out: set[str] = set()
    for msg in list(messages or []):
        if not isinstance(msg, ToolMessage):
            continue
        tid = str(getattr(msg, "tool_call_id", "") or "").strip()
        if tid:
            out.add(tid)
    return out


def _runtime_v2_latest_seq(session_id: str) -> int:
    sid = str(session_id or "").strip()
    if not sid or not _runtime_v2_is_primary():
        return 0
    try:
        from runtime_v2.event_log import SessionEventLog

        log = SessionEventLog(
            session_manager.sessions_dir,
            path_resolver=getattr(session_manager, "_resolve_session_path", None),
        )
        return max(0, int(log.next_seq(sid)) - 1)
    except Exception:
        return 0


def _runtime_v2_delete_unfinished_tool_events_after_marker(
    state: State,
    marker: Dict[str, Any],
    kept_tool_ids: set[str],
) -> None:
    sid = str(state.get("session_id") or "").strip() if isinstance(state, dict) else ""
    if not sid or not _runtime_v2_is_primary():
        return
    try:
        marker_seq = int(marker.get("runtime_seq") or 0)
    except Exception:
        marker_seq = 0
    if marker_seq <= 0:
        return
    try:
        from runtime_v2.event_log import SessionEventLog

        resolver = getattr(session_manager, "_resolve_session_path", None)
        log = SessionEventLog(session_manager.sessions_dir, path_resolver=resolver)
        ops = _runtime_v2_react_history_ops()
        for ev in log.read_after_seq(sid, marker_seq):
            payload = dict(ev.payload or {})
            ev_type = str(ev.type or "")
            ui_type = str(payload.get("type") or "")
            if ev_type not in {"tool_started", "legacy_ui_event"}:
                continue
            if ev_type == "legacy_ui_event" and ui_type != "tool_call":
                continue
            tid = str(
                payload.get("tool_call_id")
                or payload.get("id")
                or payload.get("tool_id")
                or ""
            ).strip()
            has_result = payload.get("result") is not None or payload.get("raw_content") is not None
            if tid and tid in kept_tool_ids and has_result:
                continue
            ops.delete_message(sid, int(ev.seq), reason="steer_restart_remove_unfinished_tool")
    except Exception:
        logger.debug("failed to hide unfinished tool events after steer rollback", exc_info=True)


async def _consume_steer_messages(
    state: State,
    emit: Optional[Callable[[Dict[str, Any]], Any]] = None,
    *,
    modes: Optional[set[str]] = None,
) -> bool:
    sid = str(state.get("session_id") or "").strip()
    run_id = str(state.get("_runtime_v2_run_id") or "").strip()
    items = _claim_session_steers(sid, run_id, modes=modes)
    if not items:
        return False
    work_messages = list(state.get("work_messages", []))
    llm_history = list(state.get("llm_history", []))
    changed = False
    for item in items:
        text = str((item or {}).get("content") or "").strip()
        ui_text = str((item or {}).get("ui_content") or text).strip()
        if not text:
            continue
        msg = UserMessage(content=text)
        steer_id = str((item or {}).get("id") or "")
        steer_mode = str((item or {}).get("mode") or "interrupt")
        try:
            committed = _runtime_v2_commit_user_turn(
                state,
                msg,
                ui_content=ui_text,
                ui_type="user_steer",
                operation_id=steer_id or str((item or {}).get("client_id") or ""),
                ui_metadata={
                    "steer_id": steer_id,
                    "client_id": str((item or {}).get("client_id") or ""),
                    "steer_mode": steer_mode,
                },
            )
            if not committed:
                _persist_state_with_model_append(state, msg)
        except Exception:
            transition_session_steer(sid, steer_id, {"claimed"}, "failed", error="user turn commit failed")
            raise
        deduplicated_commit = bool(state.pop("_last_user_turn_was_deduplicated", False))
        if deduplicated_commit and _runtime_v2_is_primary():
            llm_history = [_dict_to_message(m) for m in _load_runtime_v2_model_history_dicts(sid)]
            work_messages = list(llm_history)
        else:
            work_messages.append(msg)
            llm_history.append(msg)
        state["user_input"] = text
        _workflow_callbacks().call("capture_dialogue",
            state,
            "user",
            ui_text,
            kind="followup",
        )
        _capture_tool_review_user_followup(state, ui_text)
        state["dialogue"] = derive_dialogue_from_assistant_history(llm_history)
        state["work_messages"] = work_messages
        state["llm_history"] = llm_history
        if not deduplicated_commit:
            await _push_stream_event(
                state,
                {
                    "type": "user_steer",
                    "content": ui_text,
                    "steer": True,
                    "steer_id": steer_id,
                    "client_id": str((item or {}).get("client_id") or ""),
                    "steer_mode": steer_mode,
                    "_runtime_v2_committed": committed,
                },
                emit=emit,
            )
        transition_session_steer(
            sid,
            steer_id,
            {"claimed"},
            "consumed",
            consumed_by=run_id,
            consumed_at=time.time(),
        )
        changed = True
    return changed


def _progress_hint_to_stream_event(item: Any) -> Dict[str, Any]:
    """将 agent_memory 进度回调转为 SSE 事件（裁剪 / 压缩摘要 / 要点分轨）。"""
    if isinstance(item, dict) and item.get("type"):
        return item
    if isinstance(item, str):
        item = {"content": item, "progress_kind": "trim"}
    kind = str((item or {}).get("progress_kind") or "trim")
    if item.get("persist_body") is not None:
        body_map = {
            "trim": "context_trim_body",
            "summary": "context_summary_body",
            "key": "key_context_body",
        }
        return {
            "type": body_map.get(kind, "context_summary_body"),
            "content": str(item.get("persist_body") or ""),
        }
    if item.get("stream_delta") is not None:
        delta_map = {
            "trim": "context_trim_delta",
            "summary": "context_summary_delta",
            "key": "key_context_delta",
        }
        return {
            "type": delta_map.get(kind, "context_summary_delta"),
            "delta": str(item.get("stream_delta") or ""),
            "ephemeral": True,
        }
    type_map = {
        "trim": "context_trim_progress",
        "summary": "context_summary_progress",
        "key": "key_context_progress",
    }
    return {
        "type": type_map.get(kind, "context_trim_progress"),
        "content": str((item or {}).get("content") or ""),
    }


async def _await_thread_with_sse_keepalive(
    factory,
    state: State,
    emit: Optional[Callable[[Dict[str, Any]], Any]],
    interval_sec: float = 12.0,
    *,
    thread_hint_queue: Optional[queue.Queue] = None,
    keepalive_event: Optional[Dict[str, Any]] = None,
):
    """
    在线程中运行无参 factory()；等待期间周期性推送 ephemeral keepalive，
    避免上下文压缩等长时间同步调用期间 SSE 无字节，被反向代理/浏览器判定掉线。
    若提供 thread_hint_queue，则在等待循环中即时 drain 并推送 status（压缩阶段进度）。
    """
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(asyncio.to_thread(factory))
    last_keep = loop.time()
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=0.05)
            await _raise_if_steer_requested(state, emit, "thread_wait")
            if (
                not _state_run_has_write_fence(state)
                or session_manager.is_interrupt_requested(str(state.get("session_id") or ""))
            ):
                raise asyncio.CancelledError()
            if thread_hint_queue is not None and emit:
                while True:
                    try:
                        item = thread_hint_queue.get_nowait()
                    except queue.Empty:
                        break
                    ev = _progress_hint_to_stream_event(item)
                    await _push_stream_event(state, ev, emit=emit)
                    await asyncio.sleep(0)
            if task in done:
                if thread_hint_queue is not None and emit:
                    while True:
                        try:
                            item = thread_hint_queue.get_nowait()
                        except queue.Empty:
                            break
                        ev = _progress_hint_to_stream_event(item)
                        await _push_stream_event(state, ev, emit=emit)
                        await asyncio.sleep(0)
                return task.result()
            now = loop.time()
            if emit and now - last_keep >= interval_sec:
                ev = dict(keepalive_event or {"type": "sse_keepalive", "ephemeral": True})
                if not ev.get("type"):
                    ev["type"] = "sse_keepalive"
                if "ephemeral" not in ev:
                    ev["ephemeral"] = True
                await _push_stream_event(state, ev, emit=emit)
                last_keep = now
    finally:
        if not task.done():
            task.add_done_callback(_discard_task_result)
            task.cancel()


async def _await_context_policy_idle_for_session(
    state: State,
    emit: Optional[Callable[[Dict[str, Any]], Any]],
) -> None:
    sid = str(state.get("session_id") or "").strip()
    if not sid:
        return
    lock = _context_policy_lock_for_session(sid)
    if lock.acquire(blocking=False):
        lock.release()
        return
    await _push_stream_event(
        state,
        {
            "type": "status",
            "ephemeral": True,
            "content": "检测到同会话仍有未结束的上下文压缩，等待其完成后再继续 ReAct。",
        },
        emit=emit,
    )
    became_idle = await _await_thread_with_sse_keepalive(
        lambda: _wait_context_policy_idle(sid, CONTEXT_POLICY_IDLE_TIMEOUT_SEC),
        state,
        emit,
        interval_sec=5.0,
    )
    if not became_idle:
        raise RuntimeError(
            "context compression worker did not stop within "
            f"{CONTEXT_POLICY_IDLE_TIMEOUT_SEC:g}s; this run was terminated "
            "instead of waiting indefinitely"
        )


async def _emit_tool_pending_sse(
    emit: Optional[Callable],
    tool_name: str,
    tool_args: Any,
    tool_call_id: str,
    react_iter: int,
    tool_call_index: Optional[int] = None,
) -> None:
    """工具实际执行前推送占位（不落 ui_events），前端显示「xxx 工具执行中」。"""
    if not emit:
        return
    try:
        payload = {
            "type": "tool_pending",
            "ephemeral": True,
            "tool": redact_sensitive_tool_text(tool_name),
            "args": redact_sensitive_tool_obj(tool_args),
            "command_preview": _tool_command_preview(tool_name, tool_args),
            "tool_call_id": tool_call_id or "",
            "tool_call_index": tool_call_index,
            "react_iter": int(react_iter),
        }
        r = emit(payload)
        if inspect.isawaitable(r):
            await r
        await asyncio.sleep(0)
    except Exception:
        pass


async def _emit_tool_approval_required_sse(
    emit: Optional[Callable],
    session_id: str,
    approval_id: str,
    tool_name: str,
    title: str,
    message: str,
    subtitle: str = "",
    tool_call_id: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Emit the already-persisted approval request to connected clients."""
    if not emit:
        return
    try:
        payload = {
            "type": "approval_requested",
            "status": "pending",
            "kind": "approval",
            "_runtime_v2_committed": True,
            "approval_id": approval_id,
            "session_id": session_id,
            "tool": redact_sensitive_tool_text(tool_name),
            "title": redact_sensitive_tool_text(title),
            "message": redact_sensitive_tool_text(message),
            "subtitle": redact_sensitive_tool_text(subtitle or ""),
            "tool_call_id": str(tool_call_id or ""),
        }
        if extra:
            for key, value in dict(extra).items():
                payload[str(key)] = (
                    redact_sensitive_tool_text(value)
                    if isinstance(value, str)
                    else value
                )
        r = emit(payload)
        if inspect.isawaitable(r):
            await r
        await asyncio.sleep(0)
    except Exception:
        pass


def _tool_ui_approval_enabled() -> bool:
    # Security prompts are policy-controlled. A model-writable environment file
    # must never be able to disable them.
    return True


def _tool_ui_approval_spec(tool_name: str, tool_args: Any) -> Optional[Dict[str, str]]:
    if tool_name == "web_download":
        url = redact_sensitive_tool_text(str(tool_args.get("url") or "").strip())
        fp = str(
            tool_args.get("path")
            or tool_args.get("target_directory")
            or tool_args.get("file_path")
            or ""
        ).strip()
        fp = redact_sensitive_tool_text(fp)
        return {
            "title": "确认网络下载",
            "subtitle": "将把远程文件写入工作区指定路径。",
            "message": "URL：\n" + url + "\n\n保存为（工作区内）：\n" + (fp or "（未指定）"),
            "brief": "web_download → " + url[:120],
        }
    return None


_DANGER_APPROVAL_CONSEQUENCES = {
    "process.destructive": (
        "后果：这是破坏性命令（如强制删除、格式化、磁盘写入、关机/重启等），"
        "执行后可能导致文件永久丢失或系统不可用，且无法撤销。"
        "请再次核对目标路径与影响范围后决定。"
    ),
}


def _security_approval_spec(
    tool_name: str,
    tool_args: Dict[str, Any],
    decision: SecurityDecision,
    request: Any,
    workspace: Path,
) -> Dict[str, Any]:
    preview = _tool_command_preview(tool_name, tool_args)
    danger = forced_approval_for(decision)
    force_ask = always_ask_for(decision)
    metadata = dict(getattr(request, "metadata", {}) or {})
    egress_intent = str(metadata.get("egress_intent") or "none")
    one_time_only = bool(decision.constraints.get("one_time_only"))
    # Only forced-approval operations (destructive/dynamic commands) reject a
    # persistent rule. Everything else may be granted "same kind" (删除、网络、
    # 外部路径、MCP/插件均按普通审批处理，与 Claude Code 的规则层一致)。
    unsafe_persistent = danger or force_ask
    rule_info: Dict[str, str] = {}
    if not unsafe_persistent:
        try:
            from security.policy import suggest_rule_pattern

            suggested = suggest_rule_pattern(request, workspace)
            if suggested:
                rule_info = suggested
        except Exception:
            rule_info = {}
    required_dirs = list(decision.constraints.get("required_dirs") or metadata.get("required_dirs") or [])
    external_grantable = (
        decision.rule_id in {"external.write", "delete.review", "process.external"}
        and not danger
        and not force_ask
        and bool(required_dirs)
    )
    if egress_intent == "upload":
        approval_title = "数据发送需要确认"
    elif egress_intent == "read":
        approval_title = "网络读取需要确认"
    elif egress_intent in {"unknown", "interactive"}:
        approval_title = "未知网络操作需要确认"
    else:
        approval_title = "危险命令，需要确认" if danger else "安全权限请求"
    try:
        from security import sandbox_health

        guard = sandbox_health()
        enforcement_level = guard.level
        enforcement_reason = guard.reason
    except Exception:
        enforcement_level = "degraded"
        enforcement_reason = "Native egress helper health is unavailable."
    def _format_dirs(dirs: list[str]) -> str:
        return ", ".join(dirs[:3]) + (f" 等{len(dirs)}个目录" if len(dirs) > 3 else "")
    dir_hint = _format_dirs(required_dirs) if external_grantable and required_dirs else ""
    return {
        "title": (
            f"是否授权目录 {dir_hint}？"
            if external_grantable
            else approval_title
        ),
        "subtitle": (
            f"该操作需访问未授权目录 {dir_hint}。授权后，该目录及子目录的读写、删除和 Shell 操作将在本会话中自动放行（分支/子Agent可继承），同级或上层目录仍需单独授权。"
            if external_grantable
            else decision.reason
        ),
        "message": preview,
        "consequence": _DANGER_APPROVAL_CONSEQUENCES.get(decision.rule_id) if danger else "",
        "brief": preview[:180],
        "request_digest": decision.request_digest,
        "rule_id": decision.rule_id,
        # "始终允许同类操作" is only offered when a durable rule pattern can
        # actually be generated (Claude Code alignment: no suggestion, no
        # "don't ask again" button).
        "allow_always_available": bool(rule_info) and not unsafe_persistent,
        "allow_session_available": not force_ask and not one_time_only,
        "force_approval": force_ask,
        # External write/delete/shell approvals may offer the one-time
        # "workspace-outside handling permission" instead of asking every time.
        "external_workspace_grantable": external_grantable,
        "approval_level": "danger" if danger else "warning",
        "rule_action": rule_info.get("action", ""),
        "rule_pattern": rule_info.get("pattern", ""),
        "security_grant_digest": str(metadata.get("session_grant_digest") or ""),
        "egress_intent": egress_intent,
        "destinations": list(metadata.get("destinations") or []),
        "data_sources": list(metadata.get("data_sources") or []),
        "analysis_confidence": str(metadata.get("analysis_confidence") or ""),
        "enforcement_level": enforcement_level,
        "enforcement_reason": enforcement_reason,
        "grant_scope": "once" if one_time_only else ("task" if egress_intent == "upload" else "durable"),
        "required_dirs": required_dirs,
        "required_dirs_hint": dir_hint if external_grantable else "",
    }


def _tool_command_preview(tool_name: str, tool_args: Any) -> str:
    def _j(v: Any) -> str:
        return json.dumps(v, ensure_ascii=False)

    def _fmt_pair(k: str, v: Any) -> str:
        if k in ("content", "contents", "patch") and isinstance(v, str) and len(v) > 240:
            v = f"<{len(v)} chars>"
        return f"{_j(k)}: {_j(v)}"

    def _ordered_pairs(args: Dict[str, Any]) -> List[str]:
        preferred = [
            "path", "target_directory", "file_path", "command", "args", "url",
            "start_line", "end_line", "pattern", "query", "search", "replace",
            "old_string", "new_string", "workdir", "timeout_ms", "login",
            "working_dir", "timeout", "temporary", "patch", "content", "contents",
        ]
        keys: List[str] = []
        for k in preferred:
            if k in args:
                keys.append(k)
        for k in sorted(args.keys()):
            if k not in keys:
                keys.append(k)
        return [_fmt_pair(k, args.get(k)) for k in keys]

    if tool_name == "run_shell":
        try:
            args = dict(tool_args or {})
            args["command"] = _compose_shell_command(
                str(args.get("command") or ""),
                args.get("args"),
            ).strip()
            args.pop("args", None)
            return redact_sensitive_tool_text(f"{tool_name}({', '.join(_ordered_pairs(args))})")
        except Exception:
            pass
    if isinstance(tool_args, dict):
        return redact_sensitive_tool_text(f"{tool_name}({', '.join(_ordered_pairs(tool_args))})")
    try:
        arg_text = _j(tool_args if tool_args is not None else {})
    except Exception:
        arg_text = str(tool_args)
    return redact_sensitive_tool_text(f"{tool_name}({arg_text})")


def _record_temporary_write_file(state: Dict[str, Any], tool_name: str, tool_args: Any, failed: bool) -> None:
    if failed or tool_name != "write_file" or not isinstance(tool_args, dict):
        return
    if not bool(tool_args.get("temporary")):
        return
    raw = (
        tool_args.get("path")
        or tool_args.get("target_directory")
        or tool_args.get("file_path")
        or ""
    )
    try:
        p = safe_work_path(str(raw)) if str(raw).strip() else safe_work_path(AGENT_DEFAULT_WRITE_FILENAME)
    except Exception as e:
        logger.warning("temporary write_file path cannot be registered: %s", e)
        return
    bucket = list(state.get("_temporary_write_files") or [])
    sp = str(p)
    if sp not in bucket:
        bucket.append(sp)
    state["_temporary_write_files"] = bucket


def _save_result_to_tempfile(
    result_str: str,
    tool_name: str,
    state: Dict[str, Any],
    preview_chars: Optional[int] = None,
) -> str:
    """
    工具结果超过 LLM 上下文阈值时：
    1. 完整内容写入 .tool_results/tool_result_{ts}_{tool}.txt
    2. 注册到 _temporary_write_files 跟踪列表
    3. 返回替换后的 result_for_llm（预览 + 路径 + 提示）
    """
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"tool_result_{ts}_{tool_name}.txt"
        temp_path = safe_work_path(f".tool_results/{filename}")
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(result_str, encoding="utf-8")

        # 注册到临时文件跟踪列表，session 结束时自动清理
        bucket = list(state.get("_temporary_write_files") or [])
        sp = str(temp_path)
        if sp not in bucket:
            bucket.append(sp)
        state["_temporary_write_files"] = bucket

        # 用虚拟路径展示给模型（和 read_file 的输出格式一致）
        virtual_path = f"/.tool_results/{filename}"
        if preview_chars is None:
            preview_chars = max(0, int(LLM_CONTEXT_TRUNCATE_KEEP_CHARS)) // 2
        preview_chars = max(0, int(preview_chars))
        preview = result_str[:preview_chars]
        total_chars = len(result_str)
        hint = (
            f"[系统提示：返回结果已被截断；原始长度 {total_chars} 字符，"
            f"仅保留开头 {preview_chars} 字符。完整结果已落盘保存在 {virtual_path}，"
            "请使用 read_file 分块阅读。]"
        )
        if tool_name == "read_file":
            hint = (
                f"[系统提示：read_file 返回结果已被截断；原始长度 {total_chars} 字符，"
                f"仅保留开头 {preview_chars} 字符。完整结果已落盘保存在 {virtual_path}，"
                "请缩小 start_line/end_line 或使用 read_file 分块阅读该文件。]"
            )
        return f"{hint}\n\n{preview}\n\n{hint}"
    except Exception as e:
        logger.warning("save_result_to_tempfile failed, falling back to head-only truncation: %s", e)
        return truncate_tool_result_for_llm(result_str, LLM_CONTEXT_TRUNCATE_KEEP_CHARS)


def _tool_result_details_for_views(
    result_str: str,
    tool_name: str,
    state: Dict[str, Any],
) -> Tuple[str, str, str]:
    """Return (log, llm, ui) views for a tool result with one shared UI/LLM cap."""
    result_for_log = truncate_head_tail(result_str, LOG_TRUNCATE_KEEP_CHARS)
    limit = max(0, int(LLM_CONTEXT_TRUNCATE_KEEP_CHARS))
    preview_chars = limit // 2
    if len(result_str) > limit:
        result_for_display = _save_result_to_tempfile(
            result_str,
            tool_name,
            state,
            preview_chars=preview_chars,
        )
    else:
        result_for_display = result_str
    return result_for_log, result_for_display, result_for_display


def _cleanup_temporary_write_files(state: Dict[str, Any]) -> List[str]:
    files = list(state.get("_temporary_write_files") or [])
    if not files:
        return []
    cleaned: List[str] = []
    remaining: List[str] = []
    for p in files:
        try:
            result = delete_file(path=p)
            if str(result).lower().startswith("error:") or str(result).lower().startswith("failed"):
                remaining.append(p)
                logger.warning("temporary write_file cleanup failed for %s: %s", p, result)
            else:
                cleaned.append(p)
        except Exception as e:
            remaining.append(p)
            logger.warning("temporary write_file cleanup exception for %s: %s", p, e)
    state["_temporary_write_files"] = remaining
    return cleaned


def _tool_result_user_denied_ui(
    tool_name: str,
    tool_args: Any,
    tool_id: str,
    rejection_reason: str = "",
) -> Dict[str, Any]:
    result_str = "Error: User denied tool execution in UI (web confirmation)."
    reason = str(rejection_reason or "").strip()
    if reason:
        result_str += " User-provided rejection reason: " + reason
    result_for_log, result_for_llm, result_for_ui = _tool_result_details_for_views(
        result_str,
        tool_name,
        {},
    )
    return {
        "type": "tool",
        "tool_name": redact_sensitive_tool_text(tool_name),
        "tool_args": redact_sensitive_tool_obj(tool_args),
        "tool_id": tool_id,
        "result": result_str,
        "tool_detail_log": result_for_log,
        "tool_detail_llm": result_for_llm,
        "tool_detail_ui": result_for_ui,
        "result_for_log": result_for_log,
        "tool_failed": True,
    }


async def _emit_tool_call_sse(
    emit: Optional[Callable],
    res: Dict[str, Any],
    react_iter: int,
    state: Optional[Dict[str, Any]] = None,
) -> None:
    """
    将单个 tool 结果推入 SSE 队列。并行工具在各自完成时立即调用，不等到整批 gather 结束。
    调用方应在 res 上置 _sse_emitted = True 以免在后续统一处理里重复发。
    """
    if not emit or not isinstance(res, dict) or res.get("type") != "tool":
        return
    try:
        r = emit(
            {
                "type": "tool_call",
                "tool": redact_sensitive_tool_text(res["tool_name"]),
                "args": redact_sensitive_tool_obj(res["tool_args"]),
                "command_preview": _tool_command_preview(res["tool_name"], res["tool_args"]),
                "result": redact_sensitive_tool_text(res.get("result", "")),
                "status": redact_sensitive_tool_obj(res.get("tool_status") or {}),
                "tool_call_id": res.get("tool_id") or "",
                "tool_call_index": res.get("tool_call_index"),
                "react_iter": int(react_iter),
                "ui": redact_sensitive_tool_obj(res.get("ui") or {}),
            }
        )
        if inspect.isawaitable(r):
            await r
        if state is not None:
            state["_react_ui_tool_count"] = int(state.get("_react_ui_tool_count", 0) or 0) + 1
            if emit:
                await _emit_live_metrics(state, emit)
        # 让事件循环把 chunk 刷到 ASGI/uvicorn，再跑后续工具
        await asyncio.sleep(0)
    except Exception:
        pass

async def _emit_live_metrics(state, emit):
    """Push live tool counts to frontend in real-time."""
    if not emit:
        return
    await _push_stream_event(
        state,
        {
            "type": "process_metrics",
            "ephemeral": True,
            "tool_calls": int(state.get("_react_ui_tool_count", 0) or 0),
            "tool_failures": int(state.get("_react_ui_tool_fail_count", 0) or 0),
        },
        emit=emit,
    )


def _tool_result_indicates_failure(_tool_name: str, result: Any) -> bool:
    """
    工具未抛异常仍可能失败（与过程区可见的「错误输出」一致），例如：
    - run_shell：非零 Exit code、任意位置的 Error:（勿仅扫描前缀：stdout 很长时错误在末尾）
    - 各工具返回 JSON 含 \"error\" 字段
    """
    if result is None:
        return False
    if isinstance(result, dict):
        if result.get("error") is not None:
            return True
        if result.get("ok") is False:
            return True
    s = str(result).strip()
    if not s:
        return False
    # run_shell / 校验失败等多以 \"Error:\" 标明（可能在全文任意位置）
    if re.search(r"(?i)\berror\s*:", s):
        return True
    if "error executing command:" in s.lower():
        return True
    if "regex error:" in s.lower():
        return True
    if s.startswith("{") and '"error"' in s[:1200]:
        try:
            j = json.loads(s)
            if isinstance(j, dict) and j.get("error") is not None:
                return True
        except Exception:
            pass
    matches = list(re.finditer(r"(?mi)Exit code:\s*(-?\d+)", s))
    if matches:
        try:
            if int(matches[-1].group(1)) != 0:
                return True
        except ValueError:
            pass
    return False


def _tool_result_status(
    tool_name: str,
    result: Any,
    *,
    failed: Optional[bool] = None,
    duration_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Structured status metadata while preserving the existing text result API."""
    text = str(result or "")
    sample = text if len(text) <= 16_384 else text[:8192] + "\n" + text[-8192:]
    exit_code = None
    matches = list(re.finditer(r"(?mi)Exit code:\s*(-?\d+)", sample))
    if matches:
        try:
            exit_code = int(matches[-1].group(1))
        except (TypeError, ValueError):
            exit_code = None
    is_failed = _tool_result_indicates_failure(tool_name, result) if failed is None else bool(failed)
    status: Dict[str, Any] = {
        "ok": not is_failed,
        "truncated": "truncated" in sample.lower() or "截断" in sample,
        "timed_out": "timed out" in sample.lower() or "timeout" in sample.lower(),
    }
    if exit_code is not None:
        status["exit_code"] = exit_code
    if duration_ms is not None:
        status["duration_ms"] = max(0, int(duration_ms))
    return status


# ==================== 节点函数 ====================
_CONTEXT_LIMIT_ERROR_MARKERS = (
    "context_length_exceeded",
    "context_window_exceeded",
    "context limit exceeded",
    "context length exceeded",
    "context window exceeded",
    "maximum context length",
    "maximum context window",
    "max context length",
    "max context window",
    "prompt is too long",
    "prompt too long",
    "input is too long",
    "input too long",
    "too many input tokens",
    "request too large for model",
)


def _api_error_search_text(exc: BaseException) -> str:
    """Collect provider error details across common OpenAI-compatible SDK shapes."""
    values: List[Any] = []
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    for _ in range(3):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        values.extend(
            [
                current,
                getattr(current, "message", None),
                getattr(current, "body", None),
                getattr(current, "code", None),
            ]
        )
        response = getattr(current, "response", None)
        if response is not None:
            values.extend(
                [
                    getattr(response, "text", None),
                    getattr(response, "reason_phrase", None),
                ]
            )
            response_json = getattr(response, "json", None)
            if callable(response_json):
                try:
                    values.append(response_json())
                except Exception:
                    pass
        current = getattr(current, "__cause__", None) or getattr(
            current, "__context__", None
        )

    parts: List[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, (dict, list, tuple)):
            try:
                text = json.dumps(value, ensure_ascii=False)
            except Exception:
                text = str(value)
        else:
            text = str(value)
        if text and text not in parts:
            parts.append(text)
    return " ".join(parts)


def _context_limit_error_info(exc: BaseException) -> Dict[str, Any]:
    """Return normalized context-overflow metadata for a provider exception."""
    text = _api_error_search_text(exc)
    lowered = text.lower()
    context_window = extract_context_window_from_error(text)
    matched = context_window > 0 or any(
        marker in lowered for marker in _CONTEXT_LIMIT_ERROR_MARKERS
    )
    if not matched:
        matched = bool(
            re.search(
                r"(?:requested|resulted in|has)\s+[\d,._ ]+\s+tokens?.{0,120}"
                r"(?:maximum|max(?:imum)? allowed|context limit)",
                lowered,
            )
        )
    return {
        "matched": bool(matched),
        "context_window": max(0, int(context_window or 0)),
    }


def _context_limit_recovery_window(configured: int, reported: int) -> int:
    """Use a provider-reported limit for one recovery without mutating its profile."""
    configured_window = max(1, int(configured))
    reported_window = max(0, int(reported or 0))
    if reported_window <= 0:
        return configured_window
    return min(configured_window, reported_window)


def _classify_api_error(exc: BaseException) -> dict:
    """将 LLM API 异常分类为结构化错误信息（错误码 + 中文描述 + 解决方案）。"""
    try:
        from openai import APIConnectionError, APITimeoutError, AuthenticationError, BadRequestError, InternalServerError, NotFoundError, PermissionDeniedError, RateLimitError, UnprocessableEntityError
    except ImportError:
        AuthenticationError = BadRequestError = InternalServerError = NotFoundError = PermissionDeniedError = RateLimitError = UnprocessableEntityError = type(None)

    msg = str(exc).lower()
    context_limit = _context_limit_error_info(exc)

    if context_limit["matched"]:
        return {
            "code": "CTX",
            "title": "模型上下文窗口已满",
            "msg": "模型拒绝了超过其最大上下文窗口的请求。",
            "solution": "系统将尝试压缩当前上下文；若仍失败，请新建会话或检查模型上下文窗口配置。",
            "retry": 0,
            "context_window": int(context_limit["context_window"]),
        }

    if isinstance(exc, (APIConnectionError, APITimeoutError)) or 'timeout' in msg or 'timed out' in msg or 'connection' in msg:
        return {"code": "NET", "title": "网络连接失败",
                "msg": "无法连接到 API 服务器。",
                "solution": "请检查网络连接、当前 model profile 的 API Base URL、VPN/代理设置。",
                "retry": 0}
    if isinstance(exc, AuthenticationError):
        return {"code": "401", "title": "API 认证失败",
                "msg": "API Key 无效或已过期。",
                "solution": "请检查当前 model profile 中的 API Key 是否正确。",
                "retry": 0}
    if isinstance(exc, PermissionDeniedError):
        return {"code": "403", "title": "访问被拒绝",
                "msg": "当前地区不支持或 API Key 被风控。",
                "solution": "请新建 API Key，或检查服务地区限制。",
                "retry": 0}
    if isinstance(exc, NotFoundError):
        return {"code": "404", "title": "模型或接口不可用",
                "msg": "请求的模型不支持当前能力（如图像输入）。",
                "solution": "请检查模型名称是否正确，或换一个支持该能力的模型。",
                "retry": 0}
    if isinstance(exc, RateLimitError) or ("rate" in msg and "limit" in msg):
        return {"code": "429", "title": "请求频率超限",
                "msg": "已重试 3 次，均因速率限制失败。",
                "solution": "请稍等片刻再试，或降低请求频率；Token Plan 用户可考虑升级套餐。",
                "retry": 3}
    if isinstance(exc, BadRequestError) or isinstance(exc, UnprocessableEntityError):
        return {"code": "400", "title": "请求参数错误",
                "msg": "请求体格式不符合 API 要求。",
                "solution": "请检查消息格式、必填字段、模型名称是否正确。",
                "retry": 0}
    if '421' in msg or 'content' in msg and ('moderation' in msg or 'flag' in msg or 'block' in msg):
        return {"code": "421", "title": "内容被拦截",
                "msg": "输入内容触发了安全审核。",
                "solution": "请避免敏感或违规内容，修改后重试。",
                "retry": 0}
    if isinstance(exc, InternalServerError) or "500" in msg or "502" in msg or "503" in msg:
        code = "502" if "502" in msg else ("503" if "503" in msg else "500")
        return {"code": code, "title": f"服务器错误（{code}）",
                "msg": "已重试 3 次，服务器仍返回错误。",
                "solution": "请稍后重试；若持续出现请联系 API 服务商。",
                "retry": 3}
    return {"code": "OTHER", "title": "LLM 调用异常",
            "msg": "发生未知错误。",
            "solution": "请先检查模型配置，或到 GitHub 提交 issue 反馈。",
            "retry": 0}


async def _react_node_once(state: State, emit: Optional[Callable[[Dict[str, Any]], Any]] = None) -> State:
    """ReAct 循环执行，集成 todo、技能、压缩、重复检测，支持并行工具调用。"""
    # ========== 1. 初始化状态 ==========
    if "user_input" not in state:
        for msg in reversed(state["dialogue"]):
            if isinstance(msg, UserMessage):
                state["user_input"] = msg.content
                break
        else:
            state["user_input"] = ""
        logger.warning("user_input 缺失，已从对话记录中恢复")

    if not _runtime_v2_is_primary():
        _materialize_lazy_work_messages(state)
    work_messages = list(state["work_messages"])
    llm_history = list(state["llm_history"])

    # 添加循环开始标记（仅内部使用，不在前端实时打印）
    if not (llm_history and isinstance(llm_history[-1], SystemMessage) and llm_history[-1].content == "New Agent Loop Start"):
        start_msg = SystemMessage(content="New Agent Loop Start")
        llm_history.append(start_msg)
        state["llm_history"] = llm_history
        _persist_state_with_model_append(state, start_msg)
    logger.info("react_init marker_done session=%s", state.get("session_id"))


    # ========== 2. 循环变量初始化 ==========
    iter_count = 0
    tool_results = []
    final_content = ""
    llm_stream_seq = 0
    compress_attempts = 0
    context_limit_recovery_attempts = 0
    context_limit_recovery_pending = False
    context_limit_reported_window = 0
    context_limit_last_error_detail = ""
    final_result_retries = int(
        state.get("final_result_retries", state.get("empty_final_retries", 0)) or 0
    )
    final_result_retry_max = max(
        0,
        int(os.getenv("FINAL_RESULT_RETRY_MAX", os.getenv("FINAL_EMPTY_RETRY_MAX", "3"))),
    )

    # 重复检测状态
    repeat_count = state.get("repeat_count", 0)
    last_response_content = state.get("last_response_content", None)
    last_tool_calls_signature = state.get("last_tool_calls_signature", None)
    reminder_inserted = state.get("reminder_inserted", False)

    react_wall_start = time.monotonic()
    state["_react_ui_tool_count"] = 0
    state["_react_ui_tool_fail_count"] = 0
    # 步进墙钟/步间缝隙是本次 run 内的一次性计时锚点，避免跨 run 复用残留值。
    state.pop("_req_wall_start", None)
    state.pop("_last_round_end_perf", None)
    state.pop("_round_gap_ms_total", None)
    state.pop("_last_round_gap_ms", None)
    state.pop("_run_startup_ms", None)

    session_meta = dict(session_manager._load_metadata(state["session_id"]) or {})
    logger.info("react_init meta_done session=%s", state.get("session_id"))
    session_meta["_active_session_id"] = str(state.get("session_id") or "")
    max_react_iter = MAX_REACT_ITER
    if isinstance(session_meta, dict) and session_meta.get("is_subagent"):
        max_react_iter = max(
            1,
            int(session_meta.get("subagent_max_iter") or SUBAGENT_MAX_REACT_ITER),
        )
    parent_session_id = str(
        state.get("_subagent_parent_session_id")
        or (session_meta.get("parent_session_id") if isinstance(session_meta, dict) else "")
        or ""
    ).strip()

    def _inject_pending_subagent_notes(*, current_run_only: bool = False) -> bool:
        if isinstance(session_meta, dict) and session_meta.get("is_subagent"):
            return False
        claim_id = "%s:%s" % (
            str(state.get("_runtime_v2_run_id") or "continuation"), uuid.uuid4().hex
        )
        claimed = session_manager.claim_pending_subagent_notifications(
            state["session_id"],
            claim_id,
            parent_run_id=(str(state.get("_runtime_v2_run_id") or "") if current_run_only else ""),
        )
        pending_notes = [
            session_manager._pending_subagent_notification_line(item)
            for item in claimed
        ]
        pending_notes = [line for line in pending_notes if line]
        if pending_notes:
            try:
                note = SystemMessage(content="[后台 Subagent 已完成]\n" + "\n".join(pending_notes))
                llm_history.append(note)
                work_messages.append(note)
                state["llm_history"] = llm_history
                state["work_messages"] = work_messages
                _persist_state_with_model_append(state, note)
                session_manager.resolve_pending_subagent_claim(
                    state["session_id"], claim_id, consumed=True
                )
                return True
            except Exception:
                session_manager.resolve_pending_subagent_claim(
                    state["session_id"], claim_id, consumed=False
                )
                raise
        if claimed:
            session_manager.resolve_pending_subagent_claim(
                state["session_id"], claim_id, consumed=False
            )
        return False

    _t_initial_inject = time.perf_counter()
    _inject_pending_subagent_notes()
    logger.info(
        "react_init subagent_claim_done session=%s ms=%s",
        state.get("session_id"),
        int(max(0.0, (time.perf_counter() - _t_initial_inject) * 1000)),
    )

    try:
        while iter_count < max_react_iter:
            if not _state_run_has_write_fence(state):
                logger.info(
                    "stopping stale ReAct run at iteration boundary: session=%s run=%s",
                    state.get("session_id"),
                    state.get("_runtime_v2_run_id"),
                )
                raise asyncio.CancelledError()
            workflow_pause_reason = str(
                state.pop("_workflow_pause_requested", "") or ""
            ).strip()
            if workflow_pause_reason:
                final_content = workflow_pause_reason
                await _push_stream_event(
                    state,
                    {"type": "status", "content": workflow_pause_reason},
                    emit=emit,
                )
                break
            _inject_pending_subagent_notes(current_run_only=True)
            pre_api_timings: Dict[str, int] = dict(state.pop("_pre_run_timings", {}) or {})
            _t_pre_api = time.perf_counter()
            _req_wall_start = time.perf_counter()
            _last_round_end_perf = state.pop("_last_round_end_perf", None)
            if _last_round_end_perf is not None:
                _round_gap_ms = int(max(0.0, (_req_wall_start - _last_round_end_perf) * 1000.0))
                state["_round_gap_ms_total"] = int(state.get("_round_gap_ms_total") or 0) + _round_gap_ms
                state["_last_round_gap_ms"] = _round_gap_ms
            else:
                state["_last_round_gap_ms"] = 0
            state["_req_wall_start"] = _req_wall_start
            _pre_api_timing_mark(pre_api_timings, "inject_subagent_notes", _t_pre_api)
            _t_pre_api = time.perf_counter()
            await _raise_if_steer_requested(state, emit, "react")
            if not _state_run_has_write_fence(state):
                raise asyncio.CancelledError()
            if session_manager.is_interrupt_requested(state["session_id"]):
                if _is_followup_interrupt(state["session_id"]):
                    raise asyncio.CancelledError()
                final_content = _interrupt_terminal_text(state["session_id"])
                await _push_stream_event(state, {"type": "status", "content": final_content.rstrip("。")}, emit=emit)
                break
            if parent_session_id and session_manager.is_interrupt_requested(parent_session_id):
                final_content = _interrupt_terminal_text(parent_session_id, parent=True)
                await _push_stream_event(
                    state,
                    {"type": "status", "content": final_content.rstrip("。")},
                    emit=emit,
                )
                break
            _pre_api_timing_mark(pre_api_timings, "early_interrupt_checks", _t_pre_api)
            _t_pre_api = time.perf_counter()
            await _await_context_policy_idle_for_session(state, emit)
            _pre_api_timing_mark(pre_api_timings, "context_policy_wait_prebuild", _t_pre_api)
            _t_pre_api = time.perf_counter()
            await _raise_if_steer_requested(state, emit, "react")
            iter_count += 1
            state["_current_react_iter"] = int(iter_count)

            reminder = _workflow_callbacks().call("before_round", state)
            if isinstance(reminder, Mapping) and reminder.get("message") is not None:
                reminder_message = reminder["message"]
                llm_history.append(reminder_message)
                work_messages.append(reminder_message)
                _runtime_v2_append_model_message(state, reminder_message)
                state["llm_history"] = llm_history
                state["work_messages"] = work_messages
                _persist_state(state)
                await _push_stream_event(
                    state,
                    {
                        "type": "status",
                        "content": str(reminder.get("status") or "Workflow reminder inserted"),
                        "ephemeral": True,
                    },
                    emit=emit,
                )
            _pre_api_timing_mark(pre_api_timings, "before_round", _t_pre_api)
            _t_pre_api = time.perf_counter()
            _t_build_start = _t_pre_api

            # ---------- 2.2 构建 LLM 输入（静态 system 多段 + key_context，优化前缀缓存与维护） ----------
            prompt_language = state.get("_prompt_language", "zh-CN")
            static_revision = (
                skills_catalog_generation(),
                prompt_template_revision(prompt_language),
                str(prompt_language),
            )
            # Process-level cache: static segments only depend on the session
            # identity, so later runs of the same session skip the cold rebuild
            # (skills scan + env assembly) that otherwise pays seconds on the
            # first round of every user turn.  A revision drift serves the
            # last-known-good segments and rebuilds off the critical path, so
            # a skill/plugin change costs one request of staleness at most.
            _static_key = (
                str(state.get("session_id") or ""),
                bool(session_meta.get("is_subagent")) if isinstance(session_meta, dict) else False,
                isinstance(session_meta.get("fork_runtime_config"), dict)
                and bool((session_meta.get("fork_runtime_config") or {}).get("system_segments"))
                if isinstance(session_meta, dict)
                else False,
            )
            static_entry = _STATIC_SEGMENTS_PROCESS_CACHE.get(_static_key)
            static_segments_cached = isinstance(static_entry, tuple)
            static_segments: tuple
            if static_segments_cached:
                cached_revision, static_segments = static_entry
                if cached_revision != static_revision:
                    state["_prompt_static_segments_drift"] = True
                    _schedule_static_segments_rebuild(
                        state, session_meta, prompt_language, static_revision, _static_key
                    )
            else:
                static_segments = _build_static_segments_for_session(
                    str(state.get("session_id") or ""), session_meta, prompt_language
                )
                _STATIC_SEGMENTS_PROCESS_CACHE[_static_key] = (static_revision, static_segments)
            # key_context body（随压缩变化）
            kc_body = key_context_body_for_system_prompt(state.get("key_context", "") or "")
            _pre_api_timing_mark(pre_api_timings, "static_segments", _t_pre_api)
            _t_pre_api = time.perf_counter()

            turn_cache = state.get("_prompt_turn_cache")
            if (
                isinstance(turn_cache, dict)
                and int(turn_cache.get("source_id") or 0) == id(llm_history)
                and 0 <= int(turn_cache.get("source_count") or 0) <= len(llm_history)
            ):
                source_count = int(turn_cache.get("source_count") or 0)
                cached_turns = list(turn_cache.get("turns") or [])
                if source_count < len(llm_history):
                    cached_turns.extend(
                        messages_for_openai_turns(llm_history[source_count:])
                    )
                raw_turn_msgs = cached_turns
            else:
                raw_turn_msgs = messages_for_openai_turns(llm_history)
            state["_prompt_turn_cache"] = {
                "source_id": id(llm_history),
                "source_count": len(llm_history),
                "turns": raw_turn_msgs,
            }
            turn_msgs = inject_missing_tool_messages(raw_turn_msgs)

            llm_messages: List[Any] = [SystemMessage(content=s) for s in static_segments]
            if kc_body:
                llm_messages.append(SystemMessage(content=kc_body))
            llm_messages.extend(turn_msgs)
            _pre_api_timing_mark(pre_api_timings, "turn_cache", _t_pre_api)
            _t_pre_api = time.perf_counter()
            pre_api_timings["build_messages"] = int(max(0.0, (_t_pre_api - _t_build_start) * 1000.0))

            async def _live_tool_definition_awaiter(awaitable, stage: str):
                return await _await_steerable(state, awaitable, emit, stage)

            def _record_tool_definition_timing(stage: str, started: float) -> None:
                _pre_api_timing_mark(pre_api_timings, stage, started)

            tool_revision = await _combined_tool_registry_revision(session_meta)
            tool_cache = state.get("_combined_tool_registry_cache")
            if isinstance(tool_cache, dict) and tool_cache.get("revision") == tool_revision:
                tool_registry = tool_cache["registry"]
                combined_tools = tool_cache["definitions"]
                _record_tool_definition_timing("tool_registry_cache_hit", _t_pre_api)
            elif (
                isinstance(tool_cache, dict)
                and tool_cache.get("definitions")
                and _schedule_tool_registry_revalidate(state, session_meta, tool_revision)
            ):
                # Revision drifted mid-run (MCP server re-registered tools, a
                # plugin/host tool toggle, ...).  Serve the last-known-good
                # catalog for this request and rebuild in the background so the
                # MCP/plugin definition round-trip never blocks the next ReAct
                # step.  Drop the prompt runtime config so the rebuilt catalog
                # is picked up on the following request.
                tool_registry = tool_cache["registry"]
                combined_tools = tool_cache["definitions"]
                state.pop("_last_prompt_runtime_config", None)
                _record_tool_definition_timing("tool_registry_stale_hit", _t_pre_api)
                logger.info(
                    "tool registry revision drift: session=%s cached=%s current=%s",
                    state.get("session_id"),
                    _short_registry_revision(tool_cache.get("revision")),
                    _short_registry_revision(tool_revision),
                )
            else:
                tool_registry = await build_combined_tool_registry_for_session(
                    state["session_id"],
                    session_meta=session_meta,
                    awaiter=_live_tool_definition_awaiter,
                    timing_callback=_record_tool_definition_timing,
                    # Cold process start: stdio MCP servers may take tens of
                    # seconds; ship the built-in catalog now and let the
                    # background rebuild add MCP tools once they register.
                    include_mcp=agent_mcp.mcp_started(),
                )
                combined_tools = tool_registry.definitions()
                state["_combined_tool_registry_cache"] = {
                    "revision": tool_revision,
                    "registry": tool_registry,
                    "definitions": combined_tools,
                }
            runtime_config_revision = (static_revision, tool_revision)
            last_prompt_runtime_config = state.get("_last_prompt_runtime_config")
            if (
                not isinstance(last_prompt_runtime_config, dict)
                or last_prompt_runtime_config.get("_cache_revision")
                != runtime_config_revision
            ):
                last_prompt_runtime_config = {
                    "version": 1,
                    "system_segments": list(static_segments),
                    "tools": combined_tools,
                    "_cache_revision": runtime_config_revision,
                }
                state["_last_prompt_runtime_config"] = last_prompt_runtime_config
            last_prompt_runtime_config["model_runtime"] = (
                executor_runtime_snapshot_for_session(state["session_id"])
            )
            _t_pre_api = time.perf_counter()

            # 调试：仅记录多轮消息数量与首段截断（避免整段 XML 日志）
            logger.debug(
                "LLM 多轮 messages: count=%s, last_roles=%s",
                len(llm_messages),
                [type(m).__name__ for m in llm_messages[-5:]],
            )

            # ---------- 2.1 上下文压缩：单轨 + key_context
            # In calculated mode keep the trigger on the same pure local
            # tokenizer path as agent_memory's compression decision.  Hybrid
            # mode intentionally retains the provider-usage baseline.
            if state.get("_context_token_mode") == "calculated":
                full_input_est = estimate_calculated_input_tokens_for_messages(
                    llm_messages,
                    tools=combined_tools,
                )
                token_estimate_source = "local_calculated"
            else:
                full_input_est, token_estimate_source = estimate_full_input_tokens_for_messages(
                    state["session_id"],
                    llm_messages,
                    tools=combined_tools,
                    return_source=True,
                )
            _pre_api_timing_mark(pre_api_timings, "token_estimate", _t_pre_api)
            _t_pre_api = time.perf_counter()
            # Resolve at every LLM boundary.  The resolver's hot cache keeps
            # the unchanged path cheap, while a profile update invalidates the
            # session entry so the very next ReAct request uses the new model,
            # limits and client.
            _t_resolve_model = time.perf_counter()
            iter_client, iter_model, iter_max_output_tokens, iter_context_window = (
                resolve_executor_config_for_session(state["session_id"])
            )
            transport_request_context = _llm_request_context(state["session_id"])
            compacted_input_est = _responses_compacted_input_estimate(
                iter_client,
                iter_model,
                transport_request_context,
                int(full_input_est),
            )
            effective_input_est = (
                int(compacted_input_est)
                if compacted_input_est is not None
                else int(full_input_est)
            )
            effective_token_source = (
                "responses_compaction"
                if compacted_input_est is not None
                else token_estimate_source
            )
            _pre_api_timing_mark(pre_api_timings, "resolve_model_config", _t_resolve_model)
            forced_context_limit_compress = bool(context_limit_recovery_pending)
            active_context_window = (
                _context_limit_recovery_window(
                    iter_context_window,
                    context_limit_reported_window,
                )
                if forced_context_limit_compress
                else int(iter_context_window)
            )
            if emit:
                await _push_stream_event(
                    state,
                    {
                        "type": "context_tokens",
                        "estimated": int(effective_input_est),
                        "threshold": int(active_context_window),
                        "model": iter_model,
                        "token_mode": state.get("_context_token_mode", "hybrid"),
                        "source": effective_token_source,
                        "reason": (
                            "api_context_limit_recovery"
                            if forced_context_limit_compress
                            else "pre_request"
                        ),
                        "ephemeral": True,
                    },
                    emit=emit,
                )
            # 上一步已成功压缩时本步不再压：key 追加后 system 变长，若再压会反复套娃并刷爆状态行
            _skip_compress = state.pop("_compress_skip_next", False)
            # 仅按 token 策略自动压缩；是否主动压由模型调用 context_manage(compact) 决定
            if forced_context_limit_compress or not _skip_compress:
                kcur = state.get("key_context", "") or ""
                sid = state["session_id"]
                if forced_context_limit_compress or effective_input_est > active_context_window:
                    _t_pre_api = time.perf_counter()
                    if emit:
                        await _push_stream_event(
                            state,
                            {
                                "type": "status",
                                "content": (
                                    "【模型报告上下文已满，开始压缩】正在进行上下文裁剪后重试原请求"
                                    "（可能需数秒，请稍候）…"
                                    if forced_context_limit_compress
                                    else "【上下文窗口已满，开始压缩】正在进行上下文裁剪以控制 token"
                                    "（可能需数秒，请稍候）…"
                                ),
                            },
                            emit=emit,
                        )
                        # 让出事件循环，避免同步压缩阻塞时「开始」提示迟迟刷不到界面
                        await asyncio.sleep(0)
                    _hint_q: queue.Queue = queue.Queue()

                    def _compress_hint_emit(item: Any) -> None:
                        _hint_q.put(_progress_hint_to_stream_event(item))

                    # 压缩内为同步 LLM 调用，放线程执行以免阻塞 SSE；hint_sink 实时灌入队列由上层 drain
                    pre_compact_hook = await _dispatch_state_hook(
                        "PreCompact",
                        state,
                        {
                            "matcher_value": "automatic",
                            "mode": (
                                "api_context_limit"
                                if forced_context_limit_compress
                                else "automatic"
                            ),
                            "estimated_tokens": int(effective_input_est),
                            "context_window": int(active_context_window),
                        },
                        emit,
                    )
                    if (
                        pre_compact_hook.blocked
                        or pre_compact_hook.should_pause
                        or pre_compact_hook.requires_approval
                    ):
                        raise RuntimeError(
                            _hook_decision_reason(
                                pre_compact_hook,
                                "PreCompact Hook stopped automatic compaction.",
                            )
                        )
                    official_compacted = False
                    official_checkpoint = None
                    if _runtime_v2_is_primary():
                        try:
                            official_checkpoint = await _await_thread_with_sse_keepalive(
                                lambda: compact_responses_history(
                                    iter_client,
                                    iter_model,
                                    strip_reasoning_for_api_request(llm_messages),
                                    request_context=transport_request_context,
                                    source_estimated_tokens=int(full_input_est),
                                ),
                                state,
                                emit,
                                interval_sec=6.0,
                                keepalive_event={
                                    "type": "context_summary_progress",
                                    "content": "【Responses 压缩】官方 compact 仍在生成，请稍候…",
                                    "ephemeral": True,
                                },
                            )
                        except Exception as compact_exc:
                            logger.info(
                                "native Responses compact unavailable; using local fallback: %s",
                                compact_exc,
                            )
                    if official_checkpoint is not None:
                        _runtime_v2_commit_responses_compaction(
                            state,
                            official_checkpoint,
                            reason=(
                                "api_context_limit_recovery"
                                if forced_context_limit_compress
                                else "automatic"
                            ),
                        )
                        transport_request_context = _llm_request_context(state["session_id"])
                        refreshed_estimate = _responses_compacted_input_estimate(
                            iter_client,
                            iter_model,
                            transport_request_context,
                            int(full_input_est),
                        )
                        if refreshed_estimate is None:
                            raise RuntimeError("committed Responses compaction is not usable")
                        effective_input_est = int(refreshed_estimate)
                        effective_token_source = "responses_compaction"
                        nl, nk, chg = llm_history, kcur, False
                        used_llm_summary = False
                        new_recap = None
                        official_compacted = True
                        if emit:
                            await _push_stream_event(
                                state,
                                {
                                    "type": "status",
                                    "content": "【Responses 压缩已完成】已安装官方 opaque compaction checkpoint",
                                },
                                emit=emit,
                            )
                    else:
                        nl, nk, chg, _, used_llm_summary, new_recap = await _await_thread_with_sse_keepalive(
                            lambda: _run_context_policy_serialized(
                                llm_history,
                                kcur,
                                sid,
                                # `full_input_est` is calculated from the exact request package
                                # assembled above.  The policy's history-only preview can be
                                # smaller (notably when provider-usage cache is available), so do
                                # not let it turn an already-confirmed overflow into a no-op and
                                # immediately fall through to the emergency half-window truncate.
                                force_user_compact=True,
                                hint_sink=_compress_hint_emit,
                                context_window=int(active_context_window),
                                prompt_language=state.get("_prompt_language", "zh-CN"),
                                should_stop=lambda: (
                                    not _state_run_has_write_fence(state)
                                    or session_manager.is_interrupt_requested(sid)
                                ),
                            ),
                            state,
                            emit,
                            interval_sec=6.0,
                            thread_hint_queue=_hint_q,
                            keepalive_event={
                                "type": "context_summary_progress",
                                "content": "【上下文摘要】摘要模型仍在生成或等待响应中，请稍候…",
                                "ephemeral": True,
                            },
                        )
                    post_compact_hook = await _dispatch_state_hook(
                        "PostCompact",
                        state,
                        {
                            "matcher_value": "automatic",
                            "mode": (
                                "openai"
                                if official_compacted
                                else (
                                    "api_context_limit"
                                    if forced_context_limit_compress
                                    else "automatic"
                                )
                            ),
                            "changed": bool(chg or official_compacted),
                            "used_llm_summary": bool(used_llm_summary),
                        },
                        emit,
                    )
                    if (
                        post_compact_hook.blocked
                        or post_compact_hook.should_pause
                        or post_compact_hook.requires_approval
                    ):
                        raise RuntimeError(
                            _hook_decision_reason(
                                post_compact_hook,
                                "PostCompact Hook stopped the run.",
                            )
                    )
                    _pre_api_timing_mark(pre_api_timings, "context_policy_run", _t_pre_api)
                    if forced_context_limit_compress and not official_compacted:
                        context_limit_recovery_pending = False
                        before_recovery_tokens = estimate_full_input_tokens_for_llm_history(
                            state["session_id"],
                            llm_history,
                            kcur,
                            state.get("_prompt_language", "zh-CN"),
                            tools=combined_tools,
                        )
                        after_recovery_tokens = estimate_full_input_tokens_for_llm_history(
                            state["session_id"],
                            nl,
                            nk,
                            state.get("_prompt_language", "zh-CN"),
                            tools=combined_tools,
                        )
                        if after_recovery_tokens >= before_recovery_tokens:
                            emergency_history, emergency_changed, _ = compress_tail_fallback(
                                nl,
                                reason="emergency",
                                max_tokens=max(1, int(active_context_window) // 2),
                            )
                            emergency_tokens = estimate_full_input_tokens_for_llm_history(
                                state["session_id"],
                                emergency_history,
                                nk,
                                state.get("_prompt_language", "zh-CN"),
                                tools=combined_tools,
                            )
                            if emergency_changed and emergency_tokens < before_recovery_tokens:
                                nl = emergency_history
                                chg = True
                                after_recovery_tokens = emergency_tokens
                            else:
                                logger.error(
                                    "API 上下文超限恢复无法继续缩小历史：before=%s after=%s window=%s",
                                    before_recovery_tokens,
                                    after_recovery_tokens,
                                    active_context_window,
                                )
                                _cls = {
                                    "code": "CTX",
                                    "title": "模型上下文窗口已满",
                                    "msg": "压缩流程无法继续缩小当前请求。",
                                    "solution": "请新建会话，或检查当前模型配置的上下文窗口是否正确。",
                                }
                                if emit:
                                    _err_data = {
                                        "c": _cls["code"],
                                        "t": _cls["title"],
                                        "m": _cls["msg"],
                                        "s": _cls["solution"],
                                        "d": context_limit_last_error_detail,
                                    }
                                    await _push_stream_event(
                                        state,
                                        {
                                            "type": "error",
                                            "content": "__ERR_CARD__"
                                            + json.dumps(_err_data, ensure_ascii=False),
                                        },
                                        emit=emit,
                                    )
                                final_content = (
                                    f"LLM 调用失败 [{_cls['code']}] {_cls['title']}："
                                    f"{_cls['msg']}\n{_cls['solution']}"
                                )
                                break
                else:
                    nl, nk, chg, used_llm_summary, new_recap = llm_history, kcur, False, False, None
            else:
                nl, nk, chg = llm_history, (state.get("key_context", "") or ""), False
            if chg:
                state["llm_history"] = nl
                state["dialogue"] = derive_dialogue_from_assistant_history(nl)
                state["key_context"] = nk
                _workflow_callbacks().call("context_changed", state)
                llm_history = nl
                work_messages = state.get("work_messages", [])
                _fb_kind_wm = _compress_history_fallback_kind(nl)
                if _fb_kind_wm == "truncated":
                    _wm_compact_note = (
                        "[系统通知：上下文已截尾（Conversation truncated）；更早内容请查本会话目录。]"
                    )
                    _st_base = (
                        "【自动·长度策略】上下文已截尾（Conversation truncated），"
                        "保留约半窗 token 尾部；更早内容请查本会话目录。"
                    )
                elif used_llm_summary:
                    _wm_compact_note = "[系统通知：上下文已按策略完成裁剪与摘要]"
                    _st_base = "【上下文压缩已完成】已完成上下文裁剪与摘要以控制长度"
                else:
                    _wm_compact_note = "[系统通知：上下文已按策略完成裁剪]"
                    _st_base = "【自动·长度策略】已完成上下文裁剪以控制长度"
                work_messages.append(SystemMessage(content=_wm_compact_note))
                state["work_messages"] = work_messages
                _persist_state_with_model_replace(
                    state,
                    nl,
                    (
                        "api_context_limit_recovery"
                        if forced_context_limit_compress
                        else "auto_context_policy"
                    ),
                )
                state["_compress_skip_next"] = True
                _st = auto_length_strategy_status_line(
                    _st_base,
                    session_id=state["session_id"],
                    llm_history=nl,
                    key_context=nk,
                )
                if state.get("_context_token_mode") == "calculated":
                    post_compress_est = estimate_full_input_tokens_for_llm_history(
                        state["session_id"],
                        nl,
                        nk or "",
                        state.get("_prompt_language", "zh-CN"),
                        tools=combined_tools,
                    )
                    post_compress_token_source = "local_calculated"
                else:
                    post_compress_est, post_compress_token_source = estimate_hybrid_input_tokens_for_llm_history(
                        state["session_id"],
                        nl,
                        nk or "",
                        state.get("_prompt_language", "zh-CN"),
                        tools=combined_tools,
                    )
                post_compress_tokens = {
                    "estimated": int(post_compress_est),
                    "threshold": int(iter_context_window),
                    "model": iter_model,
                    "token_mode": state.get("_context_token_mode", "hybrid"),
                    "token_source": post_compress_token_source,
                    "source": post_compress_token_source,
                    "reason": "post_compress_checkpoint",
                }
                _runtime_v2_checkpoint_context_tokens(state, post_compress_tokens)
                await _push_stream_event(
                    state,
                    {
                        "type": "context_tokens",
                        **post_compress_tokens,
                        "ephemeral": True,
                    },
                    emit=emit,
                )
                await _push_stream_event(
                    state,
                    {"type": "status", "content": _st},
                    emit=emit,
                )
                compress_attempts = 0
                if forced_context_limit_compress:
                    iter_count = max(0, iter_count - 1)
                    state["_current_react_iter"] = int(iter_count)
                continue
            if effective_input_est > active_context_window:
                compress_attempts += 1
                if compress_attempts > CONTEXT_EMERGENCY_SHRINK_MAX_RETRIES:
                    logger.warning(
                        "自动应急截断已重试 %s 次仍可能超过整包阈值；将直接请求主模型。可新建会话或调低环境变量 CONTEXT_WINDOW（当前 %s）",
                        CONTEXT_EMERGENCY_SHRINK_MAX_RETRIES,
                        active_context_window,
                    )
                else:
                    old_tok = estimate_full_input_tokens_for_llm_history(
                        state["session_id"],
                        llm_history,
                        state.get("key_context", "") or "",
                        state.get("_prompt_language", "zh-CN"),
                        tools=combined_tools,
                    )
                    new_llm_history, did_shrink, _ = compress_tail_fallback(
                        llm_history, reason="emergency"
                    )
                    if did_shrink and estimate_full_input_tokens_for_llm_history(
                        state["session_id"],
                        new_llm_history,
                        state.get("key_context", "") or "",
                        state.get("_prompt_language", "zh-CN"),
                        tools=combined_tools,
                    ) < old_tok:
                        llm_history = new_llm_history
                        state["llm_history"] = llm_history
                        _persist_state_with_model_replace(state, new_llm_history, "emergency_truncate")
                        logger.info(
                            "已按 CONTEXT_COMPRESS_FAILURE_MAX_TOKENS（与压缩失败兜底同款）裁剪对话尾部并继续本步"
                        )
                        compress_attempts = 0
                        continue
            else:
                compress_attempts = 0

            executable_tool_names = tool_registry.names(executable_only=True)
            # Side-effecting calls in one assistant turn are serialized.  The
            # workspace state captured after one call is therefore also the
            # state immediately before the next call.  Reuse it so a queued
            # run_shell can switch from "generating" to "executing" without
            # first rescanning the entire workspace.
            workspace_audit_tail_by_root: Dict[str, dict] = {}

            async def _execute_one_core(tool_call):
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]
                tool_id = tool_call["id"]
                tool_call_index = tool_call.get("index")
                tool_descriptor = tool_registry.resolve(tool_name)
                if state.get("_tool_batch_first_started_at") is None:
                    state["_tool_batch_first_started_at"] = time.perf_counter()
                state["_runtime_stage"] = "running_tool:%s" % tool_name
                await _raise_if_steer_requested(state, emit, "tool")

                security_workspace = Path(
                    session_meta.get("subagent_work_dir")
                    or session_meta.get("git_worktree_path")
                    or WORK_DIR
                ).resolve()
                sec_request, sec_decision, sec_context = authorize_tool(
                    session_id=state["session_id"],
                    tool_name=tool_name,
                    arguments=tool_args if isinstance(tool_args, dict) else {},
                    workspace=security_workspace,
                )
                tool_review_context = _build_tool_review_context(
                    state,
                    tool_args,
                )
                hook_approval_spec = tool_call.get("_hook_approval_spec")
                if sec_context.mode == PermissionMode.FULL_ACCESS:
                    hook_approval_spec = None
                if sec_decision.outcome == DecisionOutcome.DENY:
                    return _blocked_tool_result(
                        tool_name, tool_args, tool_id, sec_decision.reason
                    )

                # Announce the tool row before any approval dialog. The approval
                # card anchors to this row, so it never renders at the bottom of
                # the stream and jumps back into the tool row later. ask_user
                # context_manage keeps its dedicated flow. ask_user also needs
                # a normal pending row: its durable interaction card is
                # attached to that row, and the row remains the visual anchor
                # while the model waits for an answer.
                if emit and (tool_descriptor is None or tool_descriptor.emit_pending):
                    await _emit_tool_pending_sse(
                        emit,
                        tool_name,
                        tool_args,
                        tool_id,
                        iter_count,
                        tool_call_index,
                    )

                if sec_decision.outcome == DecisionOutcome.ASK or hook_approval_spec:
                    spec = (
                        hook_approval_spec or _tool_ui_approval_spec(tool_name, tool_args)
                        or _security_approval_spec(
                            tool_name, tool_args, sec_decision, sec_request,
                            security_workspace,
                        )
                    )
                    approved = False
                    rejection_reason = ""
                    use_auto_review = (
                        sec_context.mode == PermissionMode.APPROVE_FOR_ME
                        and not hook_approval_spec
                        and not always_ask_for(sec_decision)
                    )
                    normal_pending = not use_auto_review
                    if use_auto_review:
                        await _push_stream_event(
                            state,
                            {
                                "type": "auto_review_status",
                                "status": "in_progress",
                                "tool_call_id": str(tool_id or ""),
                                "session_id": state["session_id"],
                                "content": (
                                    "自动审查中：审查 Agent 正在核对你的任务意图与请求风险。"
                                ),
                            },
                            emit=emit,
                        )
                        review = await review_request(
                            sec_request,
                            user_intent=str(state.get("_submitted_user_input") or ""),
                            session_id=str(state.get("session_id") or ""),
                            review_context=tool_review_context,
                        )
                        approved = review.approved
                        if not approved and review.available:
                            rejection_reason = str(review.reason or "").strip()
                        await _push_stream_event(
                            state,
                            {
                                "type": "auto_review_status",
                                "status": (
                                    "approved"
                                    if approved
                                    else (
                                        "unavailable"
                                        if not review.available
                                        else "denied"
                                    )
                                ),
                                "tool_call_id": str(tool_id or ""),
                                "session_id": state["session_id"],
                                "risk": review.risk,
                                "reason": review.reason,
                                "intercept_reason": review.intercept_reason,
                                "risk_analysis": review.risk_analysis,
                                "command_purpose": review.command_purpose,
                                "content": (
                                    f"【自动审批·{review.risk}】"
                                    + (
                                        "已批准："
                                        if approved
                                        else (
                                            "审查不可用："
                                            if not review.available
                                            else "已拒绝："
                                        )
                                    )
                                    + review.reason
                                ),
                            },
                            emit=emit,
                        )
                        if approved:
                            add_approval_grant(
                                state["session_id"],
                                sec_decision.request_digest,
                                "allow_once",
                            )
                        elif review.available and emit is not None:
                            # Auto-review never expands the application boundary. A denial
                            # may be overridden once by the user, but only for
                            # this exact digest-bound request.
                            appr_id = new_approval_id()
                            override_spec = dict(spec)
                            override_spec["title"] = "自动审批已拒绝：人工覆盖"
                            override_spec["subtitle"] = review.reason

                            async def _emit_auto_review_override():
                                await _emit_tool_approval_required_sse(
                                    emit,
                                    state["session_id"],
                                    appr_id,
                                    tool_name,
                                    override_spec["title"],
                                    override_spec["message"],
                                    override_spec.get("subtitle") or "",
                                    str(tool_id or ""),
                                    extra={
                                        "allow_always_available": bool(
                                            override_spec.get(
                                                "allow_always_available", False
                                            )
                                        ),
                                        "force_approval": bool(
                                            override_spec.get("force_approval", False)
                                        ),
                                        # Override cards (auto-review denial)
                                        # never offer the workspace grant; the
                                        # two-step flow lives on normal cards.
                                        "external_workspace_grantable": False,
                                        "approval_level": str(
                                            override_spec.get("approval_level")
                                            or "warning"
                                        ),
                                        "rule_action": str(
                                            override_spec.get("rule_action") or ""
                                        ),
                                        "rule_pattern": str(
                                            override_spec.get("rule_pattern") or ""
                                        ),
                                        "allow_session_available": bool(
                                            override_spec.get("allow_session_available", True)
                                        ),
                                        "egress_intent": str(override_spec.get("egress_intent") or "none"),
                                        "destinations": list(override_spec.get("destinations") or []),
                                        "data_sources": list(override_spec.get("data_sources") or []),
                                        "analysis_confidence": str(override_spec.get("analysis_confidence") or ""),
                                        "enforcement_level": str(override_spec.get("enforcement_level") or ""),
                                        "enforcement_reason": str(override_spec.get("enforcement_reason") or ""),
                                        "grant_scope": str(override_spec.get("grant_scope") or ""),
                                        "auto_review_override": True,
                                        "rejection_reason_suggestion": str(
                                            review.reason or ""
                                        ),
                                    },
                                )

                            try:
                                approval_resolution = await _await_steerable(
                                    state,
                                    wait_tool_ui_approval_after_emit(
                                        state["session_id"],
                                        appr_id,
                                        _emit_auto_review_override,
                                        metadata={
                                        "_durable": True,
                                        "run_id": str(state.get("_runtime_v2_run_id") or ""),
                                        "tool_call_id": str(tool_id or ""),
                                        "tool": redact_sensitive_tool_text(tool_name),
                                        "title": override_spec["title"],
                                        "message": redact_sensitive_tool_text(
                                            override_spec["message"]
                                        ),
                                        "subtitle": redact_sensitive_tool_text(review.reason),
                                        "security_request_digest": sec_decision.request_digest,
                                        "security_grant_digest": str(override_spec.get("security_grant_digest") or ""),
                                        "security_rule_id": sec_decision.rule_id,
                                        # The override window offers the same
                                        # "始终允许同类操作" choice as a normal
                                        # approval whenever a durable rule can
                                        # actually be generated. Forced
                                        # approvals (dangerous/dynamic) never
                                        # reach this path.
                                        "allow_always_available": bool(
                                            override_spec.get(
                                                "allow_always_available", False
                                            )
                                        ),
                                        "force_approval": bool(
                                            override_spec.get("force_approval", False)
                                        ),
                                        "approval_level": str(
                                            override_spec.get("approval_level")
                                            or "warning"
                                        ),
                                        "rule_action": str(
                                            override_spec.get("rule_action") or ""
                                        ),
                                        "rule_pattern": str(
                                            override_spec.get("rule_pattern") or ""
                                        ),
                                        "allow_session_available": bool(
                                            override_spec.get("allow_session_available", True)
                                        ),
                                        "egress_intent": str(override_spec.get("egress_intent") or "none"),
                                        "destinations": list(override_spec.get("destinations") or []),
                                        "data_sources": list(override_spec.get("data_sources") or []),
                                        "analysis_confidence": str(override_spec.get("analysis_confidence") or ""),
                                        "enforcement_level": str(override_spec.get("enforcement_level") or ""),
                                        "enforcement_reason": str(override_spec.get("enforcement_reason") or ""),
                                        "grant_scope": str(override_spec.get("grant_scope") or ""),
                                        "auto_review_override": True,
                                        "rejection_reason_suggestion": redact_sensitive_tool_text(
                                            review.reason
                                        ),
                                        },
                                        return_resolution=True,
                                        review_context={
                                            "request": sec_request,
                                            "user_intent": str(
                                                state.get("_submitted_user_input") or ""
                                            ),
                                            "review_context": tool_review_context,
                                        },
                                    ),
                                    emit,
                                    "tool_approval",
                                )
                                if isinstance(approval_resolution, dict):
                                    approved = bool(
                                        approval_resolution.get("approved")
                                    )
                                    rejection_reason = str(
                                        approval_resolution.get("rejection_reason")
                                        or rejection_reason
                                    ).strip()
                                else:
                                    approved = bool(approval_resolution)
                            except ApprovalPersistenceError as exc:
                                return _blocked_tool_result(
                                    tool_name, tool_args, tool_id, str(exc)
                                )
                        elif not review.available and emit is not None:
                            # The automatic reviewer itself failed (invalid
                            # output/timeout). Fail-closed still requires a
                            # human decision, but the card must be honest:
                            # "reviewer unavailable", not "auto-review denied".
                            spec = dict(spec)
                            spec["title"] = "审查不可用，请人工确认"
                            spec["subtitle"] = review.reason
                            normal_pending = True
                    if normal_pending and not approved:
                        if emit is None:
                            return _blocked_tool_result(
                                tool_name,
                                tool_args,
                                tool_id,
                                "Approval is required but no interactive approval channel is available.",
                            )
                        while True:
                            appr_id = new_approval_id()

                            async def _emit_appr():
                                await _emit_tool_approval_required_sse(
                                    emit,
                                    state["session_id"],
                                    appr_id,
                                    tool_name,
                                    spec["title"],
                                    spec["message"],
                                    spec.get("subtitle") or "",
                                    str(tool_id or ""),
                                    extra={
                                        "allow_always_available": bool(
                                            spec.get("allow_always_available", False)
                                        ),
                                        "force_approval": bool(
                                            spec.get("force_approval", False)
                                        ),
                                        "external_workspace_grantable": bool(
                                            spec.get("external_workspace_grantable", False)
                                        ),
                                        "approval_level": str(
                                            spec.get("approval_level") or "warning"
                                        ),
                                        "consequence": str(
                                            spec.get("consequence") or ""
                                        ),
                                        "rule_action": str(
                                            spec.get("rule_action") or ""
                                        ),
                                        "rule_pattern": str(
                                            spec.get("rule_pattern") or ""
                                        ),
                                        "allow_session_available": bool(
                                            spec.get("allow_session_available", True)
                                        ),
                                        "egress_intent": str(spec.get("egress_intent") or "none"),
                                        "destinations": list(spec.get("destinations") or []),
                                        "data_sources": list(spec.get("data_sources") or []),
                                        "analysis_confidence": str(spec.get("analysis_confidence") or ""),
                                        "enforcement_level": str(spec.get("enforcement_level") or ""),
                                        "enforcement_reason": str(spec.get("enforcement_reason") or ""),
                                        "grant_scope": str(spec.get("grant_scope") or ""),
                                        "required_dirs": list(spec.get("required_dirs") or []),
                                        "required_dirs_hint": str(spec.get("required_dirs_hint") or ""),
                                    },
                                )

                            try:
                                approval_resolution = await _await_steerable(
                                    state,
                                    wait_tool_ui_approval_after_emit(
                                        state["session_id"],
                                        appr_id,
                                        _emit_appr,
                                        metadata={
                                        "_durable": True,
                                        "run_id": str(state.get("_runtime_v2_run_id") or ""),
                                        "tool_call_id": str(tool_id or ""),
                                        "tool": redact_sensitive_tool_text(tool_name),
                                        "title": redact_sensitive_tool_text(spec["title"]),
                                        "message": redact_sensitive_tool_text(spec["message"]),
                                        "subtitle": redact_sensitive_tool_text(spec.get("subtitle") or ""),
                                        "security_request_digest": sec_decision.request_digest,
                                        "security_grant_digest": str(spec.get("security_grant_digest") or ""),
                                        "security_rule_id": sec_decision.rule_id,
                                        "allow_always_available": bool(
                                            spec.get("allow_always_available", False)
                                        ),
                                        "force_approval": bool(
                                            spec.get("force_approval", False)
                                        ),
                                        "approval_level": str(
                                            spec.get("approval_level") or "warning"
                                        ),
                                        "consequence": str(
                                            spec.get("consequence") or ""
                                        ),
                                        "rule_action": str(
                                            spec.get("rule_action") or ""
                                        ),
                                        "rule_pattern": str(
                                            spec.get("rule_pattern") or ""
                                        ),
                                        "allow_session_available": bool(
                                            spec.get("allow_session_available", True)
                                        ),
                                        "egress_intent": str(spec.get("egress_intent") or "none"),
                                        "destinations": list(spec.get("destinations") or []),
                                        "data_sources": list(spec.get("data_sources") or []),
                                        "analysis_confidence": str(spec.get("analysis_confidence") or ""),
                                        "enforcement_level": str(spec.get("enforcement_level") or ""),
                                        "enforcement_reason": str(spec.get("enforcement_reason") or ""),
                                        "grant_scope": str(spec.get("grant_scope") or ""),
                                        "required_dirs": list(spec.get("required_dirs") or []),
                                        "required_dirs_hint": str(spec.get("required_dirs_hint") or ""),
                                        },
                                        return_decision=True,
                                        return_resolution=True,
                                        review_context={
                                            "request": sec_request,
                                            "user_intent": str(
                                                state.get("_submitted_user_input") or ""
                                            ),
                                            "review_context": tool_review_context,
                                        },
                                    ),
                                    emit,
                                    "tool_approval",
                                )
                            except ApprovalPersistenceError as exc:
                                return _blocked_tool_result(
                                    tool_name, tool_args, tool_id, str(exc)
                                )
                            if isinstance(approval_resolution, dict):
                                approval_decision = str(
                                    approval_resolution.get("decision") or "deny"
                                )
                                rejection_reason = str(
                                    approval_resolution.get("rejection_reason")
                                    or rejection_reason
                                ).strip()
                            else:
                                approval_decision = str(
                                    approval_resolution or "deny"
                                )
                            if approval_decision in {
                                "allow_external_workspace",
                                "allow_external_workspace_once",
                            }:
                                try:
                                    from session_authorized_dirs import add_authorized_dir
                                    req_dirs = list(spec.get("required_dirs") or [])
                                    if not req_dirs:
                                        req_dirs = list(sec_decision.constraints.get("required_dirs") or [])
                                    for rd in req_dirs:
                                        add_authorized_dir(state["session_id"], rd)
                                except Exception as e:
                                    import logging
                                    logging.getLogger(__name__).warning("add_authorized_dir failed: %s", e)
                                spec = dict(spec)
                                spec["external_workspace_grantable"] = False
                                durable_workspace_grant = (
                                    approval_decision == "allow_external_workspace"
                                )
                                hint = ", ".join(req_dirs[:2]) if req_dirs else "目录"
                                spec["title"] = (
                                    f"确认执行工具（已授权目录 {hint}）"
                                    if durable_workspace_grant
                                    else f"确认执行工具（已允许本次目录 {hint}）"
                                )
                                spec["subtitle"] = (
                                    "目录授权已单独允许；"
                                    "请继续确认本次工具操作本身。（同级或上层目录仍需单独授权）"
                                )
                                sec_request2, sec_decision2, sec_context2 = authorize_tool(
                                    session_id=state["session_id"],
                                    tool_name=tool_name,
                                    arguments=tool_args if isinstance(tool_args, dict) else {},
                                    workspace=security_workspace,
                                )
                                if sec_decision2.outcome == DecisionOutcome.ALLOW:
                                    approved = True
                                    sec_request, sec_decision, sec_context = sec_request2, sec_decision2, sec_context2
                                    break
                                sec_request, sec_decision, sec_context = sec_request2, sec_decision2, sec_context2
                                continue
                            approved = (
                                bool(approval_resolution.get("approved"))
                                if isinstance(approval_resolution, dict)
                                else approval_decision != "deny"
                            )
                            break
                    if not approved:
                        return _tool_result_user_denied_ui(
                            tool_name,
                            tool_args,
                            tool_id,
                            rejection_reason,
                        )
                    sec_request, sec_decision, sec_context = authorize_tool(
                        session_id=state["session_id"],
                        tool_name=tool_name,
                        arguments=tool_args if isinstance(tool_args, dict) else {},
                        workspace=security_workspace,
                    )
                    if sec_decision.outcome != DecisionOutcome.ALLOW:
                        return _blocked_tool_result(
                            tool_name,
                            tool_args,
                            tool_id,
                            "Approval could not be bound to the final tool request.",
                        )

                # Run the final steer check before announcing that execution started.
                await _raise_if_steer_requested(state, emit, "tool")

                def _response_from_outcome(
                    outcome: ToolOutcome,
                    started: float,
                    *,
                    json_output: bool = False,
                    read_output: bool = False,
                ) -> Dict[str, Any]:
                    raw_result = (
                        outcome.content
                        if outcome.content is not None
                        else f"Error: {outcome.message or outcome.code}"
                    )
                    control_result = outcome.metadata.get("control_result")
                    if json_output and not isinstance(raw_result, str):
                        result_str = json.dumps(raw_result, ensure_ascii=False, default=str)
                    elif read_output:
                        result_str = _wrap_read_only_tool_output_lines(raw_result)
                    else:
                        result_str = redact_sensitive_tool_text(raw_result)
                    tool_failed = bool(
                        outcome.kind is ToolOutcomeKind.FAILED
                        or _tool_result_indicates_failure(tool_name, raw_result)
                    )
                    result_for_log, result_for_llm, result_for_ui = (
                        _tool_result_details_for_views(result_str, tool_name, state)
                    )
                    response = {
                        "type": "tool",
                        "tool_name": redact_sensitive_tool_text(tool_name),
                        "tool_args": redact_sensitive_tool_obj(tool_args),
                        "tool_id": tool_id,
                        "tool_call_index": tool_call_index,
                        "result": result_str,
                        "tool_detail_log": result_for_log,
                        "tool_detail_llm": result_for_llm,
                        "tool_detail_ui": result_for_ui,
                        "result_for_log": result_for_log,
                        "tool_failed": tool_failed,
                        "tool_status": _tool_result_status(
                            tool_name,
                            result_str,
                            failed=tool_failed,
                            duration_ms=_timing_ms(started),
                        ),
                        "_tool_outcome_metadata": dict(outcome.metadata),
                    }
                    if isinstance(control_result, dict):
                        response["_control_result"] = dict(control_result)
                    return response

                if tool_descriptor is not None and tool_descriptor.invoker_id:
                    started = time.perf_counter()

                    async def _publish_host_tool_event(event: Dict[str, Any]) -> None:
                        await _push_stream_event(state, dict(event), emit=emit)

                    host_context = HostToolInvocationContext(
                        session_id=state["session_id"],
                        run_id=str(state.get("_runtime_v2_run_id") or ""),
                        tool_call_id=str(tool_id or ""),
                        state=state,
                        emit_event=_publish_host_tool_event,
                        services={
                            "tool_name": tool_name,
                            "session_manager": session_manager,
                            "session_plan_store": session_plan_store,
                            "context_changed": lambda current_state: _workflow_callbacks().call(
                                "context_changed", current_state
                            ),
                            "runtime_v2_primary": _runtime_v2_is_primary(),
                            "raw_emit": emit,
                            "interaction_interrupt_check": lambda: (
                                not _state_run_has_write_fence(state)
                                or session_manager.is_interrupt_requested(state["session_id"])
                                or _steer_requested(state)
                            ),
                            "run_interrupt_check": lambda: (
                                not _state_run_has_write_fence(state)
                                or session_manager.is_interrupt_requested(state["session_id"])
                            ),
                            "llm_history": llm_history,
                            "context_window": int(iter_context_window),
                            "run_context_policy": _run_context_policy_serialized,
                            "edit_key_context": run_edit_key_context_instruction,
                            "progress_hint_event": _progress_hint_to_stream_event,
                            "derive_dialogue": derive_dialogue_from_assistant_history,
                            "persist_state": _persist_state,
                            "await_thread_keepalive": lambda work, hints, keepalive: (
                                _await_thread_with_sse_keepalive(
                                    work,
                                    state,
                                    emit,
                                    interval_sec=6.0,
                                    thread_hint_queue=hints,
                                    keepalive_event=keepalive,
                                )
                            ),
                            "session_meta": session_meta,
                            "await_steerable": lambda awaitable, stage: _await_steerable(
                                state, awaitable, emit, stage
                            ),
                            "should_propagate_exception": lambda exc: isinstance(
                                exc, _SteerRestartRequested
                            ),
                        },
                    )
                    outcome = await host_tool_invokers.invoke(
                        tool_descriptor.invoker_id,
                        host_context,
                        tool_args if isinstance(tool_args, dict) else {},
                    )
                    return _response_from_outcome(outcome, started)

                # MCP external tools are selected by their registered
                # invocation kind, never by a model-visible name prefix.
                if (
                    tool_descriptor is not None
                    and tool_descriptor.invocation_kind is ToolInvocationKind.MCP
                ):
                    started = time.perf_counter()
                    try:
                        mcp_work_dir = str(
                            session_meta.get("subagent_work_dir")
                            or session_meta.get("git_worktree_path")
                            or ""
                        ).strip()
                        result = await _await_steerable(
                            state,
                            agent_mcp.invoke_tool_by_fname(
                                tool_name,
                                tool_args if isinstance(tool_args, dict) else {},
                                work_dir=mcp_work_dir,
                                require_worktree_isolation=bool(
                                    mcp_work_dir
                                    and session_meta.get("git_worktree_managed")
                                ),
                            ),
                            emit,
                            "tool_mcp",
                        )
                        outcome = ToolOutcome.completed(result)
                    except _SteerRestartRequested:
                        raise
                    except Exception as e:
                        message = f"MCP 调用异常：{e}"
                        outcome = ToolOutcome.failed(
                            "mcp_invocation_error", message, content=message
                        )
                    return _response_from_outcome(outcome, started, json_output=True)

                # Native Plugin API v1 tool. The entrypoint is loaded only in
                # a worker process; Pre/PostToolUse hooks still wrap this path.
                if (
                    tool_descriptor is not None
                    and tool_descriptor.invocation_kind is ToolInvocationKind.PLUGIN
                ):
                    started = time.perf_counter()
                    try:
                        from agent_extensions import invoke_plugin_tool

                        async def _publish_plugin_extension_event(event: Mapping[str, Any]) -> None:
                            await _push_stream_event(state, dict(event), emit=emit)

                        result = await _await_steerable(
                            state,
                            invoke_plugin_tool(
                                tool_name,
                                tool_args if isinstance(tool_args, dict) else {},
                                work_dir=str(
                                    session_meta.get("subagent_work_dir")
                                    or session_meta.get("git_worktree_path")
                                    or ""
                                ).strip(),
                                require_worktree_isolation=bool(
                                    session_meta.get("git_worktree_managed")
                                    and (
                                        session_meta.get("subagent_work_dir")
                                        or session_meta.get("git_worktree_path")
                                    )
                                ),
                                session_id=str(state.get("session_id") or ""),
                                run_id=str(state.get("_runtime_v2_run_id") or ""),
                                cancellation_id=str(tool_id or ""),
                                should_cancel=lambda: (
                                    _steer_requested(state)
                                    or session_manager.is_interrupt_requested(
                                        str(state.get("session_id") or "")
                                    )
                                ),
                                publish_event=_publish_plugin_extension_event,
                            ),
                            emit,
                            "tool_plugin",
                        )
                        outcome = ToolOutcome.completed(result)
                    except _SteerRestartRequested:
                        raise
                    except Exception as e:
                        message = f"Plugin tool error ({tool_name}): {e}"
                        outcome = ToolOutcome.failed(
                            "plugin_invocation_error", message, content=message
                        )
                    return _response_from_outcome(outcome, started, json_output=True)

                tool_func = tools_dict.get(tool_name)
                tool_failed = False
                tool_invoke_started = time.perf_counter()
                change_review_capture = None
                change_review_changes = []
                change_review_tool_failed = False
                if not tool_func:
                    result = f"未知工具：{tool_name}"
                    tool_failed = True
                else:
                    workspace_lock = None
                    workspace_lock_acquired = False
                    try:
                        from workspace_lease_policy import (
                            acquire_workspace_write_lock,
                            workspace_write_lock,
                        )

                        workspace_lock = workspace_write_lock(session_meta, tool_name)
                        if workspace_lock is not None:
                            await acquire_workspace_write_lock(workspace_lock)
                            workspace_lock_acquired = True
                        # 注入 interrupt 回调，让 run_shell 能感知 interrupt 并杀子进程
                        _sid = state.get("session_id", "") if isinstance(state, dict) else ""
                        if _sid and tool_name == "run_shell":
                            set_run_shell_interrupt_check(
                                lambda: (
                                    not _state_run_has_write_fence(state)
                                    or session_manager.is_interrupt_requested(_sid)
                                    or _steer_requested(state)
                                )
                            )
                        worktree_root = ""
                        if session_meta.get("is_subagent"):
                            worktree_root = str(
                                session_meta.get("subagent_work_dir")
                                or session_meta.get("git_worktree_path")
                                or ""
                            ).strip()
                        with tool_work_dir_override(worktree_root or None):
                            with execution_scope(
                                session_id=state["session_id"],
                                context=sec_context,
                                request=sec_request,
                                decision=sec_decision,
                                workspace=security_workspace,
                            ):
                                try:
                                    change_review_capture = _workflow_callbacks().call(
                                        "before_native_file_tool",
                                        state,
                                        tool_name,
                                        tool_args,
                                        tool_id,
                                        worktree_root,
                                    )
                                except Exception:
                                    logger.warning(
                                        "change review pre-tool snapshot failed for %s",
                                        tool_name,
                                        exc_info=True,
                                    )
                                try:
                                    if hasattr(tool_func, "ainvoke"):
                                        result = await _await_steerable(
                                            state,
                                            tool_func.ainvoke(tool_args),
                                            emit,
                                            "tool",
                                        )
                                    elif hasattr(tool_func, "invoke"):
                                        result = await _await_steerable(
                                            state,
                                            asyncio.to_thread(lambda: tool_func.invoke(tool_args)),
                                            emit,
                                            "tool",
                                        )
                                    else:
                                        result = await _await_steerable(
                                            state,
                                            _invoke_plain_tool(tool_func, tool_args),
                                            emit,
                                            "tool",
                                        )
                                except BaseException:
                                    change_review_tool_failed = True
                                    raise
                                finally:
                                    if change_review_capture is not None:
                                        try:
                                            change_review_changes = _workflow_callbacks().call(
                                                "after_native_file_tool",
                                                state,
                                                change_review_capture,
                                                not change_review_tool_failed,
                                            ) or []
                                        except Exception:
                                            logger.warning(
                                                "change review post-tool snapshot failed for %s",
                                                tool_name,
                                                exc_info=True,
                                            )
                    except _SteerRestartRequested:
                        raise
                    except Exception as e:
                        result = f"工具执行异常：{str(e)}"
                        tool_failed = True
                    finally:
                        if workspace_lock is not None and workspace_lock_acquired:
                            try:
                                workspace_lock.release()
                            except RuntimeError:
                                pass
                        clear_run_shell_interrupt_check()

                logger.info(
                    "tool_execution_timing session=%s tool=%s invoke_ms=%s react_iter=%s",
                    state.get("session_id", ""),
                    redact_sensitive_tool_text(tool_name),
                    _timing_ms(tool_invoke_started),
                    int(iter_count),
                )
                tool_invoke_ms = _timing_ms(tool_invoke_started)
                outcome = (
                    ToolOutcome.failed("host_tool_error", str(result), content=result)
                    if tool_failed
                    else ToolOutcome.completed(result)
                )
                response = _response_from_outcome(
                    outcome,
                    tool_invoke_started,
                    json_output=not isinstance(result, str),
                    read_output=bool(
                        tool_descriptor is not None
                        and tool_descriptor.effect == "read"
                    ),
                )
                response["tool_status"]["duration_ms"] = tool_invoke_ms
                if change_review_changes:
                    # UI-only metadata: model history reads the original result
                    # fields and never consumes this plugin-owned value.
                    response["ui"] = {"changes": change_review_changes}
                return response


            async def execute_one(tool_call):
                """Execute a closed tool call through the Hook lifecycle."""

                call = dict(tool_call or {})
                tool_name = str(call.get("name") or "")
                tool_args = call.get("args") if isinstance(call.get("args"), dict) else {}
                tool_id = str(call.get("id") or "")
                tool_descriptor = tool_registry.resolve(tool_name)

                def short_circuit(result: Dict[str, Any]) -> Dict[str, Any]:
                    """Persist metrics for rejected calls that never reach execution."""
                    execution_metrics.record_tool(
                        state["session_id"],
                        str(state.get("_runtime_v2_run_id") or ""),
                        int(iter_count),
                        redact_sensitive_tool_text(tool_name or "unknown"),
                        0,
                        bool(result.get("tool_failed", True)),
                    )
                    return result

                if tool_name not in executable_tool_names:
                    return short_circuit(
                        _unknown_tool_result(tool_name, tool_args, tool_id)
                    )
                if isinstance(tool_args.get(_STREAM_CORRUPTED_ARGUMENTS_KEY), str):
                    corrupted = _corrupted_stream_args_result(
                        tool_name,
                        tool_id,
                        call.get("index"),
                        corrupted_length=len(tool_args[_STREAM_CORRUPTED_ARGUMENTS_KEY]),
                    )
                    return short_circuit(corrupted)
                pre = await _dispatch_state_hook(
                    "PreToolUse",
                    state,
                    {
                        "tool_name": tool_name,
                        "tool_input": dict(tool_args),
                        "tool_call_id": tool_id,
                    },
                    emit,
                )
                if pre.updated_input is not None:
                    tool_args = dict(pre.updated_input)
                    call["args"] = tool_args
                if (
                    pre.requires_approval
                    and session_permission_mode(state["session_id"]) != PermissionMode.FULL_ACCESS
                ):
                    if emit is None:
                        return short_circuit(
                            _blocked_tool_result(
                                tool_name,
                                tool_args,
                                tool_id,
                                _hook_decision_reason(pre, "Hook requested approval but no UI approval channel is available."),
                            )
                        )
                    call["_hook_approval_spec"] = {
                        "title": "Hook 请求确认",
                        "message": _hook_decision_reason(
                            pre,
                            f"Hook requested approval before executing `{tool_name}`.",
                        ),
                        "subtitle": tool_name,
                        "brief": f"Hook / {tool_name}",
                    }
                if pre.blocked or pre.should_pause:
                    reason = _hook_decision_reason(pre, "execution rejected by PreToolUse Hook")
                    if pre.should_pause:
                        state["_hook_pause_requested"] = reason
                    blocked = _blocked_tool_result(
                        tool_name,
                        tool_args,
                        tool_id,
                        reason,
                        paused=pre.should_pause,
                    )
                    blocked["tool_call_index"] = call.get("index")
                    return short_circuit(blocked)

                before_event = None
                before_context = ""
                if tool_descriptor is not None and tool_descriptor.invoker_id:
                    registered_events = host_tool_invokers.before_hook_events(
                        tool_descriptor.invoker_id, tool_args
                    )
                    before_event = registered_events[0] if registered_events else None
                if before_event:
                    before = await _dispatch_state_hook(
                        before_event,
                        state,
                        {
                            "tool_name": tool_name,
                            "tool_input": dict(tool_args),
                            "tool_call_id": tool_id,
                        },
                        emit,
                    )
                    before_context = str(before.additional_context or "")
                    if (
                        before.requires_approval
                        and session_permission_mode(state["session_id"]) != PermissionMode.FULL_ACCESS
                    ):
                        if emit is None:
                            blocked = _blocked_tool_result(
                                tool_name,
                                tool_args,
                                tool_id,
                                _hook_decision_reason(
                                    before,
                                    f"{before_event} Hook requested approval but no UI approval channel is available.",
                                ),
                            )
                            blocked["tool_call_index"] = call.get("index")
                            return short_circuit(blocked)
                        call["_hook_approval_spec"] = {
                            "title": "Hook 请求确认",
                            "message": _hook_decision_reason(
                                before,
                                f"{before_event} Hook requested approval before `{tool_name}`.",
                            ),
                            "subtitle": tool_name,
                            "brief": f"{before_event} / {tool_name}",
                        }
                    if before.blocked or before.should_pause:
                        reason = _hook_decision_reason(before, f"execution rejected by {before_event} Hook")
                        if before.should_pause:
                            state["_hook_pause_requested"] = reason
                        blocked = _blocked_tool_result(
                            tool_name,
                            tool_args,
                            tool_id,
                            reason,
                            paused=before.should_pause,
                        )
                        blocked["tool_call_index"] = call.get("index")
                        return short_circuit(blocked)

                audit_root = str(
                    session_meta.get("subagent_work_dir")
                    or session_meta.get("git_worktree_path")
                    or WORK_DIR
                )
                audit_candidate = (
                    tool_descriptor is None
                    or tool_descriptor.effect not in {"read", "control"}
                )
                audit_before_started = time.perf_counter()
                audit_before_source = "none"
                if audit_candidate and audit_root in workspace_audit_tail_by_root:
                    before_workspace = workspace_audit_tail_by_root[audit_root]
                    audit_before_source = "previous_after"
                elif audit_candidate:
                    before_workspace = await asyncio.to_thread(
                        capture_workspace_state, audit_root
                    )
                    audit_before_source = "captured"
                else:
                    before_workspace = None
                audit_before_ms = _timing_ms(audit_before_started)
                observed_tool_started = time.perf_counter()
                result = await _execute_one_core(call)
                observed_tool_ms = _timing_ms(observed_tool_started)
                audit_after_started = time.perf_counter()
                if audit_candidate:
                    after_workspace = await asyncio.to_thread(
                        capture_workspace_state, audit_root
                    )
                    workspace_audit_tail_by_root[audit_root] = after_workspace
                else:
                    after_workspace = None
                audit_after_ms = _timing_ms(audit_after_started)
                file_changes = diff_workspace_states(before_workspace, after_workspace)
                if audit_candidate:
                    logger.info(
                        "tool_workspace_audit_timing session=%s tool=%s "
                        "before_ms=%s before_source=%s after_ms=%s react_iter=%s",
                        state.get("session_id", ""),
                        redact_sensitive_tool_text(tool_name),
                        audit_before_ms,
                        audit_before_source,
                        audit_after_ms,
                        int(iter_count),
                    )
                failed = bool(isinstance(result, dict) and result.get("tool_failed"))
                execution_metrics.record_tool(
                    state["session_id"],
                    str(state.get("_runtime_v2_run_id") or ""),
                    int(iter_count),
                    redact_sensitive_tool_text(tool_name),
                    observed_tool_ms,
                    failed,
                    file_changes=file_changes,
                )
                if isinstance(result, dict) and file_changes:
                    result["file_changes"] = file_changes
                after_event = "PostToolUseFailure" if failed else "PostToolUse"
                after = await _dispatch_state_hook(
                    after_event,
                    state,
                    {
                        "tool_name": tool_name,
                        "tool_input": dict(tool_args),
                        "tool_call_id": tool_id,
                        "tool_result": result,
                        "success": not failed,
                    },
                    emit,
                )

                lifecycle_events: List[str] = []
                if not failed and isinstance(result, dict):
                    outcome_metadata = result.get("_tool_outcome_metadata") or {}
                    if isinstance(outcome_metadata, dict):
                        lifecycle_events.extend(
                            str(item)
                            for item in outcome_metadata.get("lifecycle_events") or []
                            if str(item).strip()
                        )
                lifecycle_results = []
                for lifecycle_event in lifecycle_events:
                    lifecycle_results.append(
                        await _dispatch_state_hook(
                            lifecycle_event,
                            state,
                            {
                                "tool_name": tool_name,
                                "tool_input": dict(tool_args),
                                "tool_call_id": tool_id,
                                "tool_result": result,
                                "success": not failed,
                            },
                            emit,
                        )
                    )

                hook_results = [after, *lifecycle_results]
                contexts = [str(pre.additional_context or ""), before_context]
                contexts.extend(str(item.additional_context or "") for item in hook_results)
                extra_context = "\n".join(item.strip() for item in contexts if item.strip())
                stop_result = next(
                    (
                        item
                        for item in hook_results
                        if item.blocked or item.should_pause or item.requires_approval
                    ),
                    None,
                )
                if stop_result is not None:
                    hook_stop_reason = _hook_decision_reason(
                        stop_result,
                        "post-execution Hook stopped continuation",
                    )
                    state["_hook_pause_requested"] = hook_stop_reason
                    await _workflow_callbacks().call_async("pause", state, hook_stop_reason, emit)
                if extra_context and isinstance(result, dict) and result.get("type") == "tool":
                    suffix = f"\n\n[Hook additional context]\n{extra_context}"
                    result["tool_detail_llm"] = str(
                        result.get("tool_detail_llm") or result.get("result") or ""
                    ) + suffix
                return result

            # ---------- 2.6 调用 LLM ----------
            _t_pre_api = time.perf_counter()
            await _await_context_policy_idle_for_session(state, emit)
            _pre_api_timing_mark(pre_api_timings, "context_policy_wait_pre_api", _t_pre_api)
            _t_pre_api = time.perf_counter()
            await _raise_if_steer_requested(state, emit, "react")
            if not _state_run_has_write_fence(state):
                raise asyncio.CancelledError()
            if session_manager.is_interrupt_requested(state["session_id"]):
                if _is_followup_interrupt(state["session_id"]):
                    raise asyncio.CancelledError()
                final_content = _interrupt_terminal_text(state["session_id"])
                await _push_stream_event(state, {"type": "status", "content": final_content.rstrip("。")}, emit=emit)
                break
            _pre_api_timing_mark(pre_api_timings, "final_interrupt_checks", _t_pre_api)
            _t_pre_api = time.perf_counter()
            _pre_api_timing_log(
                state["session_id"],
                pre_api_timings,
                react_iter=int(iter_count),
                messages=len(llm_messages),
                tools=len(combined_tools),
                estimated_tokens=int(effective_input_est),
                token_estimate_source=str(effective_token_source or ""),
                model=iter_model,
                **_gc_probe_extras(state),
            )
            _metrics_run_id = str(state.get("_runtime_v2_run_id") or "")
            _metrics_extra: Dict[str, int] = {}
            if int(iter_count) == 1 and state.get("_run_start_perf") is not None:
                _startup_anchor = state.get("_req_wall_start")
                if _startup_anchor is not None:
                    _metrics_extra["startup_ms"] = int(
                        max(0.0, (_startup_anchor - state["_run_start_perf"]) * 1000.0)
                    )
                else:
                    _metrics_extra["startup_ms"] = int(
                        max(0.0, (time.perf_counter() - state["_run_start_perf"]) * 1000.0)
                    )
                state["_run_startup_ms"] = _metrics_extra["startup_ms"]
                execution_metrics.record_run_fields(
                    state["session_id"], _metrics_run_id,
                    startup_ms=_metrics_extra["startup_ms"],
                )
            if state.get("_last_round_gap_ms"):
                _metrics_extra["round_gap_ms"] = int(state["_last_round_gap_ms"])
            execution_metrics.record_request(
                state["session_id"], _metrics_run_id, int(iter_count),
                **_metrics_extra,
                model=iter_model,
                status="waiting_first_token",
                context={
                    "estimated_tokens": int(effective_input_est),
                    "context_window": int(iter_context_window),
                    "messages": len(llm_messages),
                    "tools": len(combined_tools),
                    "max_output_tokens": int(iter_max_output_tokens),
                    "source": effective_token_source,
                },
            )
            execution_metrics.record_phase(
                state["session_id"], _metrics_run_id, int(iter_count),
                "pre_api", pre_api_timings,
                total_ms=sum(int(v or 0) for v in pre_api_timings.values()),
            )
            # Preserve the existing lightweight thinking indicator; detailed
            # phase diagnostics stay in backend timing logs only.
            if emit:
                await _push_stream_event(
                    state,
                    {"type": "status", "content": "正在思考中...", "ephemeral": True},
                    emit=emit,
                )
                await asyncio.sleep(0)
            llm_messages_to_send = strip_reasoning_for_api_request(llm_messages)
            llm_runtime_policy = _llm_runtime_policy(emit)
            pressure_snapshot = llm_runtime_policy["pressure_snapshot"]
            pressure_mode = str(llm_runtime_policy["cpu_pressure_mode"] or "normal")
            previous_pressure_mode = state.get("_cpu_pressure_mode")
            state["_cpu_pressure_mode"] = pressure_mode
            cpu_transition_event = _cpu_pressure_transition_event(
                previous_pressure_mode,
                pressure_snapshot,
            )
            if emit and cpu_transition_event is not None:
                await _push_stream_event(
                    state,
                    cpu_transition_event,
                    emit=emit,
                )
            execution_metrics.record_request(
                state["session_id"],
                str(state.get("_runtime_v2_run_id") or ""),
                int(iter_count),
                output_mode=llm_runtime_policy["output_mode"],
                output_mode_reason=llm_runtime_policy["reason"],
                cpu_pressure={
                    "mode": llm_runtime_policy["cpu_pressure_mode"],
                    "cpu_percent": llm_runtime_policy["cpu_percent"],
                    "cpu_average_percent": llm_runtime_policy["cpu_average_percent"],
                    "memory_percent": llm_runtime_policy["memory_percent"],
                    "available_memory_gb": llm_runtime_policy["available_memory_gb"],
                    "process_cpu_percent": llm_runtime_policy["process_cpu_percent"],
                    "event_loop_lag_ms": llm_runtime_policy["event_loop_lag_ms"],
                    "trigger": llm_runtime_policy["pressure_trigger"],
                },
            )
            llm_stream_seq += 1
            llm_delta_seq = 0
            tool_delta_seq = 0
            turn = None
            streamed_this_call = False
            early_tool_detected = False
            seen_tool_call_ids: set = set()
            early_tool_acc: Dict[int, Dict[str, str]] = {}
            early_tool_tasks: Dict[int, asyncio.Task] = {}
            early_tool_results: Dict[int, Any] = {}
            early_ordered_tool_lock = asyncio.Lock()
            # 定时器：检测 reasoning/content 停止
            thinking_timer_task = None
            api_resp: Any = None
            llm_call_usage: Optional[Dict[str, int]] = None
            llm_call_finish: Dict[str, Any] = {"finish_reason": None, "stop_reason": None}
            actual_response_model = ""
            steer_interrupted_this_call = False
            streamed_reasoning_parts: List[str] = []
            streamed_response_parts: List[str] = []

            request_scope_setter = getattr(iter_client, "set_request_scope", None)
            if callable(request_scope_setter):
                request_scope_setter(str(state.get("_runtime_v2_run_id") or ""))
                # 登记该会话的 live run scope，手动切换模型时可清空
                # 该 run 的熔断记录，让新选择立即生效。
                register_run_scope = getattr(iter_client, "note_scope_session", None)
                if callable(register_run_scope):
                    register_run_scope(str(state.get("session_id") or ""))

            def _early_tool_call_from_acc(idx: int) -> Optional[Dict[str, Any]]:
                row = early_tool_acc.get(int(idx))
                if not row:
                    return None
                name = (row.get("name") or "").strip()
                tid = row.get("id") or ""
                raw_args = row.get("arguments") or ""
                if not name or not tid:
                    return None
                if not raw_args:
                    return None
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except Exception:
                    return None
                return {"name": name, "args": args if isinstance(args, dict) else {}, "id": tid, "index": int(idx)}

            async def _run_early_tool_call(idx: int, tc: Dict[str, Any]) -> Any:
                try:
                    async def execute_closed_call() -> Any:
                        policy = _tool_steer_policy(
                            str(tc.get("name") or ""),
                            tool_registry.resolve(str(tc.get("name") or "")),
                        )
                        return await _await_steerable(
                            state,
                            execute_one(tc),
                            emit,
                            "tool",
                            defer_steer=policy["interruptibility"] == "non_interruptible",
                        )

                    early_descriptor = tool_registry.resolve(str(tc.get("name") or ""))
                    if early_descriptor is not None and early_descriptor.parallel_safe:
                        r = await execute_closed_call()
                    else:
                        # Preserve model order for writes/Shell/other side effects
                        # while still starting the queue before finish_reason.
                        async with early_ordered_tool_lock:
                            r = await execute_closed_call()
                except _SteerRestartRequested:
                    raise
                except Exception as e:
                    r = e
                early_tool_results[int(idx)] = r
                # The tool may execute as soon as its streamed JSON arguments
                # close, but its durable/live completed row must not overtake
                # this iteration's reasoning/response commit.  The normal
                # checkpoint below emits it after that LLM barrier; the steer
                # interruption path has the same ordered commit explicitly.
                return r

            def _maybe_start_closed_tool_call(payload_dict: Dict[str, Any]) -> None:
                try:
                    idx = int(payload_dict.get("index", 0) or 0)
                except Exception:
                    idx = 0
                row = early_tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if payload_dict.get("id"):
                    row["id"] = str(payload_dict.get("id") or "")
                if payload_dict.get("name_delta"):
                    row["name"] = merge_streamed_tool_name(
                        row.get("name"),
                        payload_dict.get("name_delta"),
                    )
                if payload_dict.get("arguments_delta"):
                    row["arguments"] = row.get("arguments", "") + str(payload_dict.get("arguments_delta") or "")
                if idx in early_tool_tasks:
                    return
                tc = _early_tool_call_from_acc(idx)
                if not tc or not _can_execute_closed_stream_tool(
                    str(tc.get("name") or ""),
                    tool_registry.resolve(str(tc.get("name") or "")),
                ):
                    return
                if tc.get("_hook_approval_spec"):
                    return
                try:
                    early_workspace = Path(
                        session_meta.get("subagent_work_dir")
                        or session_meta.get("git_worktree_path")
                        or WORK_DIR
                    ).resolve()
                    _early_request, early_decision, _early_context = authorize_tool(
                        session_id=state["session_id"],
                        tool_name=str(tc.get("name") or ""),
                        arguments=(tc.get("args") if isinstance(tc.get("args"), dict) else {}),
                        workspace=early_workspace,
                    )
                    if early_decision.outcome != DecisionOutcome.ALLOW:
                        return
                except Exception:
                    # If authorization cannot be classified yet, defer to the
                    # normal post-response path instead of reviewing against a
                    # partial assistant turn.
                    return
                # JSON parsing above is the completeness boundary. Tool category
                # controls concurrency/approval/cancellation, not start timing.
                early_tool_tasks[idx] = asyncio.create_task(_run_early_tool_call(idx, tc))

            if llm_runtime_policy["use_stream"]:
                _pre_api_tail_ms = int(max(0.0, (time.perf_counter() - _t_pre_api) * 1000.0))
                execution_metrics.record_request(
                    state["session_id"],
                    str(state.get("_runtime_v2_run_id") or ""),
                    int(iter_count),
                    pre_api_tail_ms=_pre_api_tail_ms,
                )
                t_llm_start = time.monotonic()
                state["_runtime_stage"] = "waiting_model"
                logger.info(
                    "llm_worker_started session=%s react_iter=%s model=%s",
                    state["session_id"],
                    int(iter_count),
                    iter_model,
                )
                def _run_stream_worker_logged():
                    try:
                        return run_chat_completion_stream_worker(
                            sync_q,
                            iter_client,
                            iter_model,
                            llm_messages_to_send,
                            tools=combined_tools,
                            temperature=EXECUTOR_TEMPERATURE,
                            max_tokens=iter_max_output_tokens,
                            extra_body=EXECUTOR_EXTRA_BODY,
                            parallel_tool_calls=True,
                            reasoning_effort=EXECUTOR_REASONING_EFFORT,
                            request_context=transport_request_context,
                            should_abort=stream_abort_event.is_set,
                            transport_observer=executor_http_client,
                            emit_deltas=bool(llm_runtime_policy.get("emit_deltas", True)),
                        )
                    finally:
                        stream_worker_done_event.set()
                        logger.info(
                            "llm_worker_completed session=%s react_iter=%s model=%s",
                            state["session_id"],
                            int(iter_count),
                            iter_model,
                        )
                async_stream_q: asyncio.Queue = asyncio.Queue()
                sync_q = _ThreadToAsyncQueue(asyncio.get_running_loop(), async_stream_q)
                stream_abort_event = threading.Event()
                stream_worker_done_event = threading.Event()
                stream_timing_events: List[Dict[str, Any]] = []
                def _stream_model_switch_status(ev: Dict[str, Any]) -> None:
                    if not _should_suppress_model_switch_status(state, ev):
                        sync_q.put(("status", ev))

                _set_model_switch_status_callback(
                    iter_client,
                    _stream_model_switch_status,
                )
                stream_task = asyncio.create_task(
                    asyncio.to_thread(_run_stream_worker_logged)
                )
                stream_error: Optional[BaseException] = None
                stream_loop = asyncio.get_running_loop()
                steer_control = _steer_control_from_state(state)
                steer_signal = (
                    steer_control.subscribe_abort(stream_loop)
                    if steer_control is not None
                    else None
                )
                queue_get_task: Optional[asyncio.Task] = asyncio.create_task(async_stream_q.get())
                steer_wait_task: Optional[asyncio.Task] = (
                    asyncio.create_task(steer_signal.wait())
                    if steer_signal is not None
                    else None
                )
                steer_fallback_task: Optional[asyncio.Task] = asyncio.create_task(asyncio.sleep(0.25))
                try:
                    while True:
                        if _steer_requested(state):
                            steer_interrupted_this_call = True
                            stream_abort_event.set()
                            break
                        waiters = {queue_get_task, steer_fallback_task}
                        if steer_wait_task is not None:
                            waiters.add(steer_wait_task)
                        done, _ = await asyncio.wait(
                            waiters,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if steer_wait_task is not None and steer_wait_task in done:
                            steer_interrupted_this_call = True
                            stream_abort_event.set()
                            break
                        if steer_fallback_task in done:
                            steer_fallback_task = asyncio.create_task(asyncio.sleep(0.25))
                            if _steer_requested(state):
                                steer_interrupted_this_call = True
                                stream_abort_event.set()
                                break
                        if queue_get_task not in done:
                            continue
                        item = queue_get_task.result()
                        queue_get_task = asyncio.create_task(async_stream_q.get())
                        if item is None:
                            break
                        tag, payload = item[0], item[1]
                        if tag == "err":
                            stream_error = payload
                            continue
                        if tag == "status":
                            if isinstance(payload, dict):
                                status_event = dict(payload)
                            else:
                                status_event = {"type": "status", "content": str(payload or "")}
                            if status_event.get("content"):
                                await _push_stream_event(state, status_event, emit=emit)
                            continue
                        if tag == "stream_timing":
                            payload_dict = payload if isinstance(payload, dict) else {}
                            step = str(payload_dict.get("step") or "").strip()
                            if step:
                                stream_timing_events.append(dict(payload_dict))
                                execution_metrics.record_stream_event(
                                    state["session_id"],
                                    str(state.get("_runtime_v2_run_id") or ""),
                                    int(iter_count),
                                    payload_dict,
                                )
                                if step == "transport_breakdown":
                                    transport_parts = []
                                    transport_values = {}
                                    for key, value in payload_dict.items():
                                        if key in {"step", "model"}:
                                            continue
                                        if isinstance(value, (int, float)):
                                            transport_parts.append(f"{key}={int(value)}{'B' if 'bytes' in key or 'length' in key else 'ms'}")
                                            if key != "ms_since_api_start":
                                                transport_values[key] = int(value)
                                    execution_metrics.record_phase(
                                        state["session_id"],
                                        str(state.get("_runtime_v2_run_id") or ""),
                                        int(iter_count),
                                        "network_transport",
                                        transport_values,
                                        total_ms=int(payload_dict.get("trace_elapsed_ms") or payload_dict.get("ms_since_api_start") or 0),
                                    )
                                    logger.info(
                                        "http_transport_timing session=%s react_iter=%s model=%s %s",
                                        state["session_id"],
                                        int(iter_count),
                                        redact_sensitive_tool_text(str(iter_model or "")),
                                        " ".join(transport_parts),
                                    )
                            continue
                        if tag == "usage":
                            llm_call_usage = payload
                            usage_timing = dict((payload or {}).get("_timing") or {})
                            measured_tps_ms = int(usage_timing.get("measured_total_ms") or 0)
                            measured_tps = round(
                                int((payload or {}).get("completion_tokens", 0) or 0)
                                / max(0.001, measured_tps_ms / 1000.0),
                                1,
                            ) if measured_tps_ms > 0 else 0.0
                            execution_metrics.record_usage(
                                state["session_id"],
                                str(state.get("_runtime_v2_run_id") or ""),
                                int(iter_count),
                                dict(payload or {}),
                            )
                            record_prompt_tokens_for_messages(
                                state["session_id"],
                                llm_messages_to_send,
                                int((payload or {}).get("prompt_tokens", 0) or 0),
                                tools=combined_tools,
                            )
                            actual_response_model = str((payload or {}).get("model") or actual_response_model or "").strip()
                            ch = int((payload or {}).get("prompt_cache_hit_tokens", 0) or 0)
                            cm = int((payload or {}).get("prompt_cache_miss_tokens", 0) or 0)
                            if ch + cm > 0:
                                logger.info(
                                    "流式缓存: hit=%s miss=%s 命中率=%.1f%%",
                                    ch, cm, ch / (ch + cm) * 100
                                )
                            if emit:
                                r = emit({
                                    "type": "cache_stats",
                                    "stream": True,
                                    "cache_hit": ch,
                                    "cache_miss": cm,
                                    "hit_rate": round(ch / (ch + cm) * 100, 1) if (ch + cm) > 0 else 0,
                                    "input_tokens": int((payload or {}).get("prompt_tokens", 0) or 0),
                                    "output_tokens": int((payload or {}).get("completion_tokens", 0) or 0),
                                    "threshold": int(iter_context_window),
                                    "tokens_per_sec": measured_tps,
                                    "first_token_wait_ms": int(usage_timing.get("first_token_wait_ms") or 0),
                                    "token_generation_ms": int(usage_timing.get("token_generation_ms") or 0),
                                    "usage_return_ms": int(usage_timing.get("usage_return_ms") or 0),
                                    "model": actual_response_model or iter_model,
                                    "context_token_mode": state.get("_context_token_mode", "hybrid"),
                                })
                                await r
                                await asyncio.sleep(0)
                            continue
                        if tag == "finish" and isinstance(payload, dict):
                            actual_response_model = str(payload.get("model") or actual_response_model or "").strip()
                            llm_call_finish = {
                                "finish_reason": payload.get("finish_reason"),
                                "stop_reason": payload.get("stop_reason"),
                                "model": actual_response_model or None,
                            }
                            continue
                        if tag == "turn":
                            turn = payload
                            continue
                        if tag == "tool_call_delta" and payload:
                            _tool_batch_completed_at = state.pop("_tool_batch_completed_at", None)
                            if _tool_batch_completed_at is not None:
                                logger.info(
                                    "tool_to_next_llm_first_delta_timing session=%s react_iter=%s total=%sms delta_type=tool_call",
                                    state["session_id"],
                                    int(iter_count),
                                    _timing_ms(float(_tool_batch_completed_at)),
                                )
                            tool_delta_seq += 1
                            # 取消定时器
                            if thinking_timer_task and not thinking_timer_task.done():
                                thinking_timer_task.cancel()
                            payload_dict = payload if isinstance(payload, dict) else {}
                            tool_delta_id = str(payload_dict.get("id", "") or "").strip()
                            if tool_delta_id and tool_delta_id not in seen_tool_call_ids:
                                seen_tool_call_ids.add(tool_delta_id)
                                early_tool_detected = True
                            _maybe_start_closed_tool_call(payload_dict)
                            if emit:
                                r = emit(
                                    {
                                        "type": "tool_call_delta",
                                        "ephemeral": True,
                                        "stream_seq": llm_stream_seq,
                                        "delta_seq": tool_delta_seq,
                                        "react_iter": iter_count,
                                        "index": payload_dict.get("index", 0),
                                        "id": payload_dict.get("id", ""),
                                        "name_delta": payload_dict.get("name_delta", ""),
                                        "arguments_delta": payload_dict.get("arguments_delta", ""),
                                    }
                                )
                                if inspect.isawaitable(r):
                                    await r
                                await asyncio.sleep(0)
                            streamed_this_call = True
                            continue
                        if tag == "reasoning" and payload:
                            _tool_batch_completed_at = state.pop("_tool_batch_completed_at", None)
                            if _tool_batch_completed_at is not None:
                                logger.info(
                                    "tool_to_next_llm_first_delta_timing session=%s react_iter=%s total=%sms delta_type=reasoning",
                                    state["session_id"],
                                    int(iter_count),
                                    _timing_ms(float(_tool_batch_completed_at)),
                                )
                            streamed_reasoning_parts.append(str(payload))
                            llm_delta_seq += 1
                            # 启动/重置定时器
                            if thinking_timer_task and not thinking_timer_task.done():
                                thinking_timer_task.cancel()
                            thinking_timer_task = asyncio.create_task(_thinking_timer(emit, state))
                            r = emit(
                                {
                                    "type": "llm_reasoning_delta",
                                    "delta": payload,
                                    "react_iter": iter_count,
                                    "stream_seq": llm_stream_seq,
                                    "delta_seq": llm_delta_seq,
                                    "ephemeral": True,
                                }
                            )
                            if inspect.isawaitable(r):
                                await r
                            await asyncio.sleep(0)
                            streamed_this_call = True
                        elif tag == "content" and payload:
                            _tool_batch_completed_at = state.pop("_tool_batch_completed_at", None)
                            if _tool_batch_completed_at is not None:
                                logger.info(
                                    "tool_to_next_llm_first_delta_timing session=%s react_iter=%s total=%sms delta_type=content",
                                    state["session_id"],
                                    int(iter_count),
                                    _timing_ms(float(_tool_batch_completed_at)),
                                )
                            streamed_response_parts.append(str(payload))
                            llm_delta_seq += 1
                            # 启动/重置定时器
                            if thinking_timer_task and not thinking_timer_task.done():
                                thinking_timer_task.cancel()
                            thinking_timer_task = asyncio.create_task(_thinking_timer(emit, state))
                            r = emit(
                                {
                                    "type": "llm_response_delta",
                                    "delta": payload,
                                    "react_iter": iter_count,
                                    "stream_seq": llm_stream_seq,
                                    "delta_seq": llm_delta_seq,
                                    "ephemeral": True,
                                }
                            )
                            if inspect.isawaitable(r):
                                await r
                            await asyncio.sleep(0)
                            streamed_this_call = True
                except asyncio.CancelledError:
                    stream_abort_event.set()
                    raise
                finally:
                    for pending_waiter in (
                        queue_get_task,
                        steer_wait_task,
                        steer_fallback_task,
                    ):
                        if pending_waiter is not None and not pending_waiter.done():
                            pending_waiter.cancel()
                    if steer_control is not None and steer_signal is not None:
                        steer_control.unsubscribe_abort(stream_loop, steer_signal)
                    # 取消定时器
                    if thinking_timer_task and not thinking_timer_task.done():
                        thinking_timer_task.cancel()
                    try:
                        if steer_interrupted_this_call or stream_abort_event.is_set():
                            stream_abort_event.set()
                            stream_task.add_done_callback(_discard_task_result)
                            stream_task.cancel()
                            worker_stopped = await asyncio.shield(
                                asyncio.to_thread(
                                    stream_worker_done_event.wait,
                                    STREAM_WORKER_ABORT_TIMEOUT_SEC,
                                )
                            )
                            if not worker_stopped:
                                logger.warning(
                                    "stream worker did not stop within %.1fs after abort; "
                                    "detaching it so the run can reach a terminal state "
                                    "session=%s react_iter=%s",
                                    STREAM_WORKER_ABORT_TIMEOUT_SEC,
                                    state.get("session_id"),
                                    int(iter_count),
                                )
                        else:
                            await stream_task
                    except Exception:
                        pass
                    _set_model_switch_status_callback(iter_client, None)
                    _llm_stream_timing_log(
                        state["session_id"],
                        int(iter_count),
                        actual_response_model or iter_model,
                        stream_timing_events,
                    )
                    _stream_steps = {
                        str(item.get("step") or ""): int(item.get("ms_since_api_start") or 0)
                        for item in stream_timing_events
                        if isinstance(item, dict) and item.get("step")
                    }
                    _request_start_ms = int(_stream_steps.get("request_start", 0))
                    _stream_created_ms = int(_stream_steps.get("stream_created", _request_start_ms))
                    _first_delta_ms = int(_stream_steps.get("first_delta", _stream_created_ms))
                    _stream_end_ms = int(_stream_steps.get("stream_exhausted", _stream_steps.get("turn_ready", _first_delta_ms)))
                    _first_chunk_ms = int(_stream_steps.get("first_chunk", _stream_created_ms))
                    _usage_chunk_ms = int(_stream_steps.get("usage_chunk", _stream_end_ms))
                    _transport_breakdown = next((item for item in stream_timing_events if item.get("step") == "transport_breakdown"), {})
                    _transport_final = next((item for item in stream_timing_events if item.get("step") == "transport_final"), {})
                    _metrics_run_id = str(state.get("_runtime_v2_run_id") or "")
                    _api_send_events = {"request_start_to_stream_created": max(0, _stream_created_ms - _request_start_ms)}
                    for _key, _value in _transport_breakdown.items():
                        if _key.endswith("_ms") and _key not in {"ms_since_api_start", "trace_elapsed_ms"}:
                            _api_send_events[_key] = int(_value or 0)
                    execution_metrics.record_phase(
                        state["session_id"], _metrics_run_id, int(iter_count), "api_send",
                        _api_send_events,
                        total_ms=max(0, _stream_created_ms - _request_start_ms),
                    )
                    execution_metrics.record_phase(
                        state["session_id"], _metrics_run_id, int(iter_count), "first_token",
                        {
                            "stream_created_to_first_chunk": max(0, _first_chunk_ms - _stream_created_ms),
                            "first_chunk_to_first_delta": max(0, _first_delta_ms - _first_chunk_ms),
                        },
                        total_ms=max(0, _first_delta_ms - _stream_created_ms),
                    )
                    execution_metrics.record_phase(
                        state["session_id"], _metrics_run_id, int(iter_count), "llm_output",
                        {
                            "first_delta_to_usage": max(0, _usage_chunk_ms - _first_delta_ms),
                            "usage_to_stream_end": max(0, _stream_end_ms - _usage_chunk_ms),
                        },
                        total_ms=max(0, _stream_end_ms - _first_delta_ms),
                    )
                    execution_metrics.record_request(
                        state["session_id"], _metrics_run_id, int(iter_count),
                        network={
                            "request_bytes": int(_transport_final.get("request_bytes") or _transport_breakdown.get("request_bytes") or 0),
                            "response_content_length": int(_transport_final.get("response_content_length") or 0),
                            "response_payload_bytes_estimated": int(_transport_final.get("response_payload_bytes_estimated") or 0),
                            "request_to_first_token_ms": max(0, _first_delta_ms - _request_start_ms),
                            "transport_elapsed_ms": int(_transport_final.get("trace_elapsed_ms") or _transport_breakdown.get("trace_elapsed_ms") or 0),
                        },
                    )
                    logger.info(
                        "llm_result_consumed session=%s react_iter=%s model=%s",
                        state["session_id"],
                        int(iter_count),
                        actual_response_model or iter_model,
                    )
                    state["_runtime_stage"] = "react"
                if steer_interrupted_this_call:
                    for _idx, _task in list(early_tool_tasks.items()):
                        if not _task.done():
                            _task.add_done_callback(_discard_task_result)
                            _task.cancel()
                    partial_reasoning = "".join(streamed_reasoning_parts).strip()
                    partial_response = "".join(streamed_response_parts)
                    completed_early_tool_calls: List[Dict[str, Any]] = []
                    completed_early_tool_results: List[Dict[str, Any]] = []
                    for _idx in sorted(early_tool_results.keys()):
                        _res = early_tool_results.get(_idx)
                        _tc = _early_tool_call_from_acc(_idx)
                        if isinstance(_res, dict) and _res.get("type") == "tool" and _tc:
                            completed_early_tool_calls.append(_tc)
                            completed_early_tool_results.append(_res)
                    steer_checkpoint_ok = True
                    if partial_reasoning or partial_response.strip() or completed_early_tool_calls:
                        try:
                            if partial_reasoning:
                                await _push_stream_event(
                                    state,
                                    {
                                        "type": "llm_reasoning",
                                        "content": partial_reasoning,
                                        "react_iter": int(iter_count),
                                    },
                                    emit=emit,
                                )
                                await prune_session_ephemeral(
                                    state["session_id"],
                                    types={"llm_reasoning_delta"},
                                    react_iter=int(iter_count),
                                )
                            if partial_response.strip():
                                await _push_stream_event(
                                    state,
                                    {
                                        "type": "llm_response",
                                        "content": partial_response,
                                        "react_iter": int(iter_count),
                                    },
                                    emit=emit,
                                )
                                await prune_session_ephemeral(
                                    state["session_id"],
                                    types={"llm_response_delta"},
                                    react_iter=int(iter_count),
                                )
                            partial_msg = AssistantMessage(
                                content=partial_response if partial_response is not None else "",
                                tool_calls=completed_early_tool_calls or None,
                                metadata={"is_assistant_response": True},
                                additional_kwargs=build_assistant_additional_kwargs(partial_reasoning),
                            )
                            llm_history.append(partial_msg)
                            work_messages.append(partial_msg)
                            state["llm_history"] = llm_history
                            state["work_messages"] = work_messages
                            state["dialogue"] = derive_dialogue_from_assistant_history(llm_history)
                            _persist_state_with_model_append(state, partial_msg)
                            for _res in completed_early_tool_results:
                                _ui_msg = ToolMessage(content=_res["tool_detail_ui"], tool_call_id=_res["tool_id"])
                                _llm_msg = ToolMessage(content=_res["tool_detail_llm"], tool_call_id=_res["tool_id"])
                                work_messages.append(_ui_msg)
                                llm_history.append(_llm_msg)
                                _runtime_v2_append_model_message(state, _llm_msg)
                                if not _res.get("_sse_emitted"):
                                    await _emit_tool_call_sse(emit, _res, iter_count, state)
                                    _res["_sse_emitted"] = True
                            state["llm_history"] = llm_history
                            state["work_messages"] = work_messages
                            state["dialogue"] = derive_dialogue_from_assistant_history(llm_history)
                        except Exception:
                            steer_checkpoint_ok = False
                            logger.debug("failed to preserve steer partial assistant output", exc_info=True)
                    # Commit the interrupted assistant/tool checkpoint before
                    # telling clients to discard live rows.  Durable LLM/tool
                    # events upgrade their existing DOM rows first; the abort
                    # event then removes only drafts that remain uncommitted.
                    await _emit_steer_abort_event(
                        state,
                        emit,
                        "llm_stream",
                        checkpoint_ok=steer_checkpoint_ok,
                        cleanup_scope="drafts_only" if steer_checkpoint_ok else "none",
                    )
                    if await _consume_steer_messages(state, emit=emit, modes={"interrupt"}):
                        _reset_steer_control(state)
                        llm_history = list(state["llm_history"])
                        work_messages = list(state["work_messages"])
                        final_result_retries = 0
                        state["final_result_retries"] = 0
                        state["empty_final_retries"] = 0
                        continue
                    _reset_steer_control(state)
                if stream_error is not None:
                    logger.warning("流式输出失败，降级为整段响应: %s", stream_error)
                    turn = None
                    streamed_this_call = False
                elif turn is None:
                    logger.warning("流式未完成（无 turn），降级为整段响应")
                    streamed_this_call = False

            if turn is None and early_tool_tasks:
                _network_recovered_calls = [
                    _tc
                    for _idx in sorted(early_tool_tasks)
                    if (_tc := _early_tool_call_from_acc(_idx)) is not None
                ]
                if _network_recovered_calls:
                    # Never reissue an LLM request after a complete side-effecting
                    # call may already have started. Continue from the append-only
                    # streamed calls and their actual results instead.
                    turn = AssistantTurn(
                        content="".join(streamed_response_parts),
                        tool_calls=_network_recovered_calls,
                        reasoning_content="".join(streamed_reasoning_parts).strip() or None,
                    )
                    stream_error = None
                    streamed_this_call = True
                    llm_call_finish = {
                        "finish_reason": "recovered_closed_tool_calls",
                        "stop_reason": None,
                        "model": actual_response_model or None,
                    }
                    await prune_session_ephemeral(
                        state["session_id"],
                        types={"tool_pending", "tool_call_delta", "tool_command_delta"},
                        react_iter=int(iter_count),
                    )
                    await _push_stream_event(
                        state,
                        {
                            "type": "llm_stream_aborted",
                            "reason": "transport_after_closed_tool_call",
                            "react_iter": int(iter_count),
                            "stream_seq": llm_stream_seq,
                            "ephemeral": True,
                        },
                        emit=emit,
                    )

            if turn is None:
                model_switch_status_events: List[Dict[str, Any]] = []
                def _collect_model_switch_status(ev: Dict[str, Any]) -> None:
                    if not _should_suppress_model_switch_status(state, ev):
                        model_switch_status_events.append(dict(ev))

                _set_model_switch_status_callback(
                    iter_client,
                    _collect_model_switch_status,
                )
                try:
                    if stream_error is not None:
                        stream_error_code = _classify_api_error(stream_error).get("code")
                        if stream_error_code in {"NET", "CTX"}:
                            # Do not immediately duplicate a failed streaming
                            # request through the non-streaming fallback.
                            # Bounded reconnect/context-recovery paths own the
                            # retry and live-row cleanup.
                            raise stream_error
                    t_llm_fallback_start = time.monotonic()
                    api_resp = await _await_steerable(
                        state,
                        asyncio.to_thread(
                            lambda: chat_completion(
                                iter_client,
                                iter_model,
                                llm_messages_to_send,
                                tools=combined_tools,
                                temperature=EXECUTOR_TEMPERATURE,
                                max_tokens=iter_max_output_tokens,
                                extra_body=EXECUTOR_EXTRA_BODY,
                                parallel_tool_calls=True,
                                reasoning_effort=EXECUTOR_REASONING_EFFORT,
                                request_context=transport_request_context,
                            )
                        ),
                        emit,
                        "llm_request",
                    )
                    choice0 = api_resp.choices[0]
                    actual_response_model = str(getattr(api_resp, "model", None) or actual_response_model or "").strip()
                    turn = parse_assistant_message(choice0.message, tools=combined_tools)
                    llm_call_finish = {
                        "finish_reason": getattr(choice0, "finish_reason", None),
                        "stop_reason": getattr(choice0, "stop_reason", None),
                        "model": actual_response_model or None,
                    }
                    if emit:
                        for _switch_ev in model_switch_status_events:
                            await _push_stream_event(state, _switch_ev, emit=emit)
                        model_switch_status_events.clear()
                    u = getattr(api_resp, "usage", None)
                    if u is not None and not llm_call_usage:
                        llm_call_usage = extract_usage_dict(u)
                        execution_metrics.record_usage(
                            state["session_id"],
                            str(state.get("_runtime_v2_run_id") or ""),
                            int(iter_count),
                            dict(llm_call_usage or {}),
                        )
                        record_prompt_tokens_for_messages(
                            state["session_id"],
                            llm_messages_to_send,
                            int((llm_call_usage or {}).get("prompt_tokens", 0) or 0),
                            tools=combined_tools,
                        )
                        ch = llm_call_usage.get("prompt_cache_hit_tokens", 0)
                        cm = llm_call_usage.get("prompt_cache_miss_tokens", 0)
                        if ch + cm > 0:
                            logger.info(
                                "非流式缓存: hit=%s miss=%s 命中率=%.1f%%",
                                ch, cm, ch / (ch + cm) * 100
                            )
                        if emit:
                            await _push_stream_event(
                                state,
                                {
                                    "type": "cache_stats",
                                    "stream": False,
                                    "cache_hit": ch,
                                    "cache_miss": cm,
                                    "hit_rate": round(ch / (ch + cm) * 100, 1) if (ch + cm) > 0 else 0,
                                    "input_tokens": llm_call_usage.get("prompt_tokens", 0),
                                    "output_tokens": llm_call_usage.get("completion_tokens", 0),
                                    "threshold": int(iter_context_window),
                                    "tokens_per_sec": round(llm_call_usage.get("completion_tokens", 0) / max(0.001, time.monotonic() - t_llm_fallback_start), 1),
                                    "model": actual_response_model or iter_model,
                                    "context_token_mode": state.get("_context_token_mode", "hybrid"),
                                },
                                emit=emit,
                            )
                except _SteerRestartRequested:
                    raise
                except Exception as _llm_exc:
                    if emit:
                        for _switch_ev in model_switch_status_events:
                            await _push_stream_event(state, _switch_ev, emit=emit)
                        model_switch_status_events.clear()
                    _cls = _classify_api_error(_llm_exc)
                    _err_detail = f"{type(_llm_exc).__name__}: {_llm_exc}"
                    logger.error("LLM 调用失败 [iter %s] %s %s: %s", iter_count, _cls["code"], _cls["title"], _err_detail)
                    if _cls.get("code") == "CTX":
                        if (
                            context_limit_recovery_attempts
                            < CONTEXT_EMERGENCY_SHRINK_MAX_RETRIES
                        ):
                            context_limit_recovery_attempts += 1
                            context_limit_recovery_pending = True
                            context_limit_reported_window = max(
                                0,
                                int(_cls.get("context_window") or 0),
                            )
                            context_limit_last_error_detail = _err_detail
                            logger.warning(
                                "模型报告上下文超限，进入压缩恢复 %s/%s：reported_window=%s",
                                context_limit_recovery_attempts,
                                CONTEXT_EMERGENCY_SHRINK_MAX_RETRIES,
                                context_limit_reported_window or "unknown",
                            )
                            if emit:
                                await _push_stream_event(
                                    state,
                                    {
                                        "type": "llm_stream_aborted",
                                        "reason": "context_window_exceeded",
                                        "react_iter": int(iter_count),
                                        "stream_seq": llm_stream_seq,
                                        "ephemeral": True,
                                    },
                                    emit=emit,
                                )
                                await _push_stream_event(
                                    state,
                                    {
                                        "type": "status",
                                        "content": (
                                            "模型报告已达到最大上下文窗口，正在进入压缩流程"
                                            f"（第 {context_limit_recovery_attempts} 次恢复）…"
                                        ),
                                        "ephemeral": True,
                                    },
                                    emit=emit,
                                )
                            iter_count = max(0, iter_count - 1)
                            state["_current_react_iter"] = int(iter_count)
                            continue
                        logger.error(
                            "模型上下文超限恢复达到次数上限：%s",
                            CONTEXT_EMERGENCY_SHRINK_MAX_RETRIES,
                        )
                    if _cls.get("code") == "NET":
                        local_network_offline = isinstance(_llm_exc, LocalNetworkUnavailableError)
                        if not local_network_offline:
                            local_network_offline = not await asyncio.to_thread(machine_network_available)
                        if local_network_offline:
                            if emit:
                                await _push_stream_event(
                                    state,
                                    {
                                        "type": "llm_stream_aborted",
                                        "reason": "local_network_offline",
                                        "react_iter": int(iter_count),
                                        "stream_seq": llm_stream_seq,
                                        "ephemeral": True,
                                    },
                                    emit=emit,
                                )
                                await _push_stream_event(
                                    state,
                                    {
                                        "type": "status",
                                        "content": "检测到本机网络已断开，Agent 进入沉睡状态并等待网络恢复…",
                                        "network_waiting": True,
                                        "local_network_offline": True,
                                        "ephemeral": True,
                                    },
                                    emit=emit,
                                )
                            if not await _wait_for_local_network_recovery(
                                state,
                                emit,
                                poll_seconds=LOCAL_NETWORK_POLL_SECONDS,
                            ):
                                final_content = _interrupt_terminal_text(state["session_id"])
                                break
                            state.pop("_network_reconnect_attempts", None)
                            iter_count = max(0, iter_count - 1)
                            state["_current_react_iter"] = int(iter_count)
                            continue
                        attempt = int(state.get("_network_reconnect_attempts", 0) or 0) + 1
                        state["_network_reconnect_attempts"] = attempt
                        if attempt <= NETWORK_RECONNECT_MAX_ATTEMPTS:
                            delay = min(30.0, 2.0 * (2 ** min(attempt - 1, 4)))
                            if emit:
                                await _push_stream_event(
                                    state,
                                    {
                                        "type": "llm_stream_aborted",
                                        "reason": "network_reconnect",
                                        "react_iter": int(iter_count),
                                        "stream_seq": llm_stream_seq,
                                        "ephemeral": True,
                                    },
                                    emit=emit,
                                )
                                await _push_stream_event(
                                    state,
                                    {
                                        "type": "status",
                                        "content": f"网络连接失败，正在重连（第 {attempt} 次，{delay:g}s 后重试）...",
                                        "ephemeral": True,
                                    },
                                    emit=emit,
                                )
                            if not await _await_retry_delay_or_interrupt(state, emit, delay):
                                final_content = _interrupt_terminal_text(state["session_id"])
                                await _push_stream_event(
                                    state,
                                    {"type": "status", "content": final_content.rstrip("。")},
                                    emit=emit,
                                )
                                break
                            iter_count = max(0, iter_count - 1)
                            state["_current_react_iter"] = int(iter_count)
                            continue
                    state.pop("_network_reconnect_attempts", None)
                    if emit:
                        import json as _json
                        _err_data = {"c": _cls["code"], "t": _cls["title"], "m": _cls["msg"], "s": _cls["solution"], "d": _err_detail}
                        await _push_stream_event(
                            state,
                            {"type": "error", "content": "__ERR_CARD__" + _json.dumps(_err_data, ensure_ascii=False)},
                            emit=emit,
                        )
                    final_content = f"LLM 调用失败 [{_cls['code']}] {_cls['title']}：{_cls['msg']}\n{_cls['solution']}"
                    break
                finally:
                    _set_model_switch_status_callback(iter_client, None)
            state.pop("_network_reconnect_attempts", None)
            context_limit_recovery_attempts = 0
            context_limit_recovery_pending = False
            context_limit_reported_window = 0
            context_limit_last_error_detail = ""
            # 正文与思考严格分源
            response_text = turn.content or ""
            reasoning_text = (turn.reasoning_content or "").strip()
            response_log_text = truncate_head_tail(response_text, LOG_TRUNCATE_KEEP_CHARS)
            reasoning_log_text = truncate_head_tail(reasoning_text, LOG_TRUNCATE_KEEP_CHARS) if reasoning_text else ""

            if reasoning_text:
                logger.info(
                    f"LLM Reasoning (iter {iter_count}): "
                    f"{reasoning_log_text}"
                )
            logger.info(
                f"LLM Response (iter {iter_count}): "
                f"{response_log_text if (response_text or '').strip() else '(无正文)'}"
            )

            # 推送给前端：流式时已在 delta 中展示；非流式仍发整段事件（与持久化一致）
            finish_reason_norm = str(llm_call_finish.get("finish_reason") or "").strip().lower()
            stop_reason_norm = str(llm_call_finish.get("stop_reason") or "").strip().lower()
            output_truncated = (
                finish_reason_norm in {"length", "max_tokens", "max_output_tokens"}
                or stop_reason_norm in {"length", "max_tokens", "max_output_tokens"}
            )
            recovered_truncated_tool_calls: List[Dict[str, Any]] = []
            if output_truncated and early_tool_tasks:
                for _idx in sorted(early_tool_tasks):
                    _closed_call = _early_tool_call_from_acc(_idx)
                    if _closed_call is not None:
                        recovered_truncated_tool_calls.append(_closed_call)
                if recovered_truncated_tool_calls:
                    # The assistant turn may have been truncated after one or
                    # more independently complete calls. Keep those calls and
                    # their real results in history; discard only unfinished
                    # draft fragments instead of pretending executed writes did
                    # not happen.
                    turn.tool_calls = recovered_truncated_tool_calls
                    output_truncated = False
                    await prune_session_ephemeral(
                        state["session_id"],
                        types={"tool_pending", "tool_call_delta", "tool_command_delta"},
                        react_iter=int(iter_count),
                    )
                    await _push_stream_event(
                        state,
                        {
                            "type": "llm_stream_aborted",
                            "reason": "truncated_after_closed_tool_call",
                            "react_iter": int(iter_count),
                            "stream_seq": llm_stream_seq,
                            "ephemeral": True,
                        },
                        emit=emit,
                    )
                    await _push_stream_event(
                        state,
                        {
                            "type": "status",
                            "content": "模型输出在完整工具调用后达到长度上限；已保留并执行完整调用，未完成片段已丢弃。",
                            "ephemeral": True,
                        },
                        emit=emit,
                    )
            if output_truncated:
                for _task in list(early_tool_tasks.values()):
                    if not _task.done():
                        _task.add_done_callback(_discard_task_result)
                        _task.cancel()
                length_retry = int(state.get("_output_length_retries", 0) or 0) + 1
                state["_output_length_retries"] = length_retry
                max_length_retries = max(0, int(os.getenv("OUTPUT_LENGTH_RETRY_MAX", "2")))
                if length_retry <= max_length_retries:
                    retry_msg = SystemMessage(
                        content=(
                            "[系统通知：上一轮 assistant 输出因为 max_tokens/max_output_tokens 上限被截断，"
                            "可能包含未闭合或不完整的 tool_call。该半截输出已丢弃，不能当作最终答案。"
                            "请重新生成一个完整且更短的下一步；如果需要写入长文件，请拆成多个较小的工具调用，"
                            "或先写入较小脚本/片段再继续。]"
                        )
                    )
                    llm_history.append(retry_msg)
                    work_messages.append(retry_msg)
                    _runtime_v2_append_model_message(state, retry_msg)
                    state["llm_history"] = llm_history
                    state["work_messages"] = work_messages
                    _persist_state(state)
                    await _push_stream_event(
                        state,
                        {
                            "type": "status",
                            "content": f"模型输出达到输出 token 上限，已丢弃半截工具调用并重试（{length_retry}/{max_length_retries}）",
                        },
                        emit=emit,
                    )
                    continue
                final_content = (
                    "模型输出达到 max_tokens/max_output_tokens 上限，工具调用可能被截断。"
                    "请调大输出窗口，或把长文件写入拆成更小的步骤后重试。"
                )
                break
            state.pop("_output_length_retries", None)

            if emit:
                if streamed_this_call:
                    sid = state["session_id"]
                    if (reasoning_text or "").strip():
                        session_manager.append_ui_event(
                            sid,
                            {
                                "type": "llm_reasoning",
                                "content": reasoning_text,
                                "react_iter": int(iter_count),
                            },
                        )
                        await prune_session_ephemeral(
                            sid,
                            types={"llm_reasoning_delta"},
                            react_iter=int(iter_count),
                        )
                        await _push_stream_event(
                            state,
                            {
                                "type": "llm_reasoning",
                                "content": reasoning_text,
                                "react_iter": int(iter_count),
                                "_skip_persist": True,
                                "metadata": {"live_commit": True},
                            },
                            emit=emit,
                        )
                    if (response_text or "").strip():
                        session_manager.append_ui_event(
                            sid,
                            {
                                "type": "llm_response",
                                "content": response_text,
                                "react_iter": int(iter_count),
                            },
                        )
                        await prune_session_ephemeral(
                            sid,
                            types={"llm_response_delta"},
                            react_iter=int(iter_count),
                        )
                        await _push_stream_event(
                            state,
                            {
                                "type": "llm_response",
                                "content": response_text,
                                "react_iter": int(iter_count),
                                "_skip_persist": True,
                                "metadata": {"live_commit": True},
                            },
                            emit=emit,
                        )
                else:
                    if (reasoning_text or "").strip():
                        r = emit(
                            {
                                "type": "llm_reasoning",
                                "content": reasoning_text,
                                "react_iter": int(iter_count),
                            }
                        )
                        if inspect.isawaitable(r):
                            await r
                        await asyncio.sleep(0)
                    if (response_text or "").strip():
                        r = emit(
                            {
                                "type": "llm_response",
                                "content": response_text,
                                "react_iter": int(iter_count),
                            }
                        )
                        if inspect.isawaitable(r):
                            await r
                        await asyncio.sleep(0)

            tool_calls_list = recovered_truncated_tool_calls or turn.tool_calls
            if not isinstance(tool_calls_list, list) or len(tool_calls_list) == 0:
                tool_calls_list = None
                for _task in list(early_tool_tasks.values()):
                    if not _task.done():
                        _task.add_done_callback(_discard_task_result)
                        _task.cancel()
            invalid_interactive_batch = bool(
                tool_calls_list
                and any(
                    bool(
                        tool_registry.resolve(str((tc or {}).get("name") or ""))
                        and tool_registry.require(
                            str((tc or {}).get("name") or "")
                        ).interactive
                    )
                    for tc in tool_calls_list
                )
                and len(tool_calls_list) != 1
            )
            if invalid_interactive_batch:
                for _task in list(early_tool_tasks.values()):
                    if not _task.done():
                        _task.add_done_callback(_discard_task_result)
                        _task.cancel()
            if tool_calls_list is not None:
                state["_steer_rollback_marker"] = {
                    "llm_len": len(llm_history),
                    "work_len": len(work_messages),
                    "runtime_seq": _runtime_v2_latest_seq(state["session_id"]),
                }
            else:
                state.pop("_steer_rollback_marker", None)

            # 将本步助手输出写入历史（OpenAI 多轮消息链：AssistantMessage，含 tool_calls）
            _ak = build_assistant_additional_kwargs(
                reasoning_text,
                getattr(turn, "reasoning_field", None),
            )
            provider_data = getattr(turn, "provider_data", None)
            if isinstance(provider_data, dict) and provider_data:
                _ak["_myagent_responses"] = dict(provider_data)
            _ai_kw: Dict[str, Any] = {
                "content": response_text if response_text is not None else "",
                "metadata": {"is_assistant_response": True},
                "additional_kwargs": _ak,
            }
            if tool_calls_list is not None:
                _ai_kw["tool_calls"] = tool_calls_list
            interim_msg = AssistantMessage(**_ai_kw)
            llm_history.append(interim_msg)
            work_messages.append(interim_msg)
            state["llm_history"] = llm_history
            state["work_messages"] = work_messages
            _workflow_callbacks().call("capture_dialogue",
                state,
                "assistant",
                response_text,
                kind="response",
            )
            _capture_tool_review_assistant_context(
                state,
                reasoning_text,
                response_text,
            )
            _persist_state_with_model_append(state, interim_msg)

            # Checkpoint every completed tool before another tool is awaited.
            # Otherwise a steer between tool calls cannot distinguish a finished
            # result from an unfinished call during rollback.
            checkpointed_tool_result_ids: set[str] = set()

            async def checkpoint_completed_tool_result(res: Any) -> Any:
                if not isinstance(res, dict) or res.get("type") != "tool":
                    return res
                tool_id = str(res.get("tool_id") or "").strip()
                if not tool_id or tool_id in checkpointed_tool_result_ids:
                    return res
                control_result = res.get("_control_result")
                replacement_history = (
                    control_result.get("new_llm_history")
                    if isinstance(control_result, dict)
                    and control_result.get("type") == "compact"
                    else None
                )
                if isinstance(replacement_history, list):
                    llm_history[:] = list(replacement_history)
                tool_msg_ui = ToolMessage(content=res["tool_detail_ui"], tool_call_id=tool_id)
                tool_msg_llm = ToolMessage(content=res["tool_detail_llm"], tool_call_id=tool_id)
                work_messages.append(tool_msg_ui)
                llm_history.append(tool_msg_llm)
                state["llm_history"] = llm_history
                state["work_messages"] = work_messages
                state["dialogue"] = derive_dialogue_from_assistant_history(llm_history)
                if isinstance(replacement_history, list):
                    _persist_state_with_model_replace(
                        state, llm_history, "manual_context_manage"
                    )
                else:
                    _persist_state_with_model_append(state, tool_msg_llm)
                checkpointed_tool_result_ids.add(tool_id)
                res["_history_persisted"] = True
                if not res.get("_sse_emitted"):
                    await _emit_tool_call_sse(emit, res, iter_count, state)
                    res["_sse_emitted"] = True
                return res

            # 记录 LLM 调用详情（可选；与实际上送内容一致，已剥历史 reasoning）
            # Keep the actual request path untouched. The diagnostic trace is
            # compact by default so growing prompts are not retained N times.
            trace_messages = (
                list(llm_messages_to_send)
                if LLM_FULL_CALL_TRACE
                else list(llm_messages_to_send)[-6:]
            )
            request_msgs = [_serialize_message(msg) for msg in trace_messages]
            if not LLM_FULL_CALL_TRACE:
                for trace_message in request_msgs:
                    if "content" in trace_message:
                        trace_message["content"] = truncate_head_tail(
                            str(trace_message.get("content") or ""),
                            max(400, LOG_TRUNCATE_KEEP_CHARS),
                        )
            if LLM_FULL_CALL_TRACE:
                response_content_for_trace = response_text if response_text else None
                reasoning_content_for_trace = reasoning_text if reasoning_text else None
                tool_calls_for_trace = [
                    {"name": tc["name"], "args": tc["args"], "id": tc.get("id", "")}
                    for tc in tool_calls_list
                ] if tool_calls_list else None
            else:
                response_content_for_trace = (
                    truncate_head_tail(response_text, max(800, LOG_TRUNCATE_KEEP_CHARS))
                    if response_text
                    else None
                )
                reasoning_content_for_trace = (
                    truncate_head_tail(reasoning_text, max(800, LOG_TRUNCATE_KEEP_CHARS))
                    if reasoning_text
                    else None
                )
                tool_calls_for_trace = [
                    {
                        "name": tc["name"],
                        "id": tc.get("id", ""),
                        "args_preview": truncate_head_tail(
                            json.dumps(tc.get("args") or {}, ensure_ascii=False, default=str),
                            max(400, LOG_TRUNCATE_KEEP_CHARS),
                        ),
                    }
                    for tc in tool_calls_list
                ] if tool_calls_list else None
            call_record = {
                "model": actual_response_model or iter_model,
                "requested_model": iter_model,
                "request": request_msgs,
                "request_message_count": len(llm_messages_to_send),
                "request_compacted": not LLM_FULL_CALL_TRACE,
                "response": {
                    "content": response_content_for_trace,
                    "reasoning_content": reasoning_content_for_trace,
                    "finish_reason": llm_call_finish.get("finish_reason"),
                    "stop_reason": llm_call_finish.get("stop_reason"),
                    "tool_calls": tool_calls_for_trace,
                },
                "usage": llm_call_usage,
            }
            state["llm_calls"].append(call_record)
            workflow_after_call = _workflow_callbacks().call("record_call_usage", state)
            if (
                workflow_after_call
                and workflow_after_call.get("status") == "paused"
                and workflow_after_call.get("pause_reason") == "token_budget_exhausted"
            ):
                state["_workflow_pause_requested"] = (
                    "Workflow token budget is exhausted; execution is paused after this tool batch."
                )
                if emit:
                    workflow_event = _workflow_callbacks().call(
                        "state_event", workflow_after_call, "budget_exhausted"
                    )
                    await _push_stream_event(
                        state,
                        workflow_event or {"type": "extension_state_changed", "ephemeral": True},
                        emit=emit,
                    )

            # ---------- 2.7 并行工具调用（须先于重复检测插入的系统消息，保证 assistant(tool_calls) 后紧跟 tool） ----------
            if tool_calls_list:
                final_tool_indexes = set()
                for _tc in tool_calls_list:
                    try:
                        if _tc.get("index") is not None:
                            final_tool_indexes.add(int(_tc.get("index")))
                    except Exception:
                        pass
                for _idx, _task in list(early_tool_tasks.items()):
                    if _idx not in final_tool_indexes and not _task.done():
                        _task.add_done_callback(_discard_task_result)
                        _task.cancel()
                def is_read_only_tool(tool_call: Dict[str, Any]) -> bool:
                    n = tool_call.get("name") or ""
                    descriptor = tool_registry.resolve(str(n))
                    return bool(descriptor and descriptor.parallel_safe)

                async def run_group(group_calls: List[Dict[str, Any]]) -> List[Any]:
                    """
                    并行执行一批只读工具；用 as_completed 使「每个工具一结束就发 tool_call」，
                    再按原 tool_calls 顺序组装返回值供后续写 llm_history（顺序与 OpenAI 要求一致）。
                    """
                    if not group_calls:
                        return []

                    local_pressure_limit = cpu_pressure.tool_parallelism(MAX_PARALLEL_TOOLS)
                    local_pressure_semaphore = asyncio.Semaphore(local_pressure_limit)

                    async def run_one_with_tc(tc: Dict[str, Any]):
                        try:
                            tool_name = str((tc or {}).get("name") or "")
                            descriptor = tool_registry.resolve(tool_name)
                            if descriptor is not None and descriptor.pressure_limited:
                                async with local_pressure_semaphore:
                                    r = await _await_steerable(state, execute_one(tc), emit, "tool")
                            else:
                                r = await _await_steerable(state, execute_one(tc), emit, "tool")
                        except _SteerRestartRequested:
                            raise
                        except Exception as e:
                            r = e
                        return (tc, r)

                    tasks = [asyncio.create_task(run_one_with_tc(tc)) for tc in group_calls]
                    by_id: Dict[str, Any] = {}
                    try:
                        pending_tasks = set(tasks)
                        while pending_tasks:
                            done_tasks, pending_tasks = await asyncio.wait(
                                pending_tasks,
                                timeout=0.03,
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            if not done_tasks:
                                await _raise_if_steer_requested(state, emit, "tool")
                                continue
                            for done in done_tasks:
                                tc, r = await done
                                r = await checkpoint_completed_tool_result(r)
                                tid = (tc or {}).get("id", "")
                                if tid is not None:
                                    by_id[tid] = r
                                if (
                                    emit
                                    and isinstance(r, dict)
                                    and r.get("type") == "tool"
                                    and not r.get("_sse_emitted")
                                ):
                                    r["_sse_emitted"] = True
                                    await _emit_tool_call_sse(emit, r, iter_count, state)
                            await _raise_if_steer_requested(state, emit, "tool")
                    except _SteerRestartRequested:
                        for task in tasks:
                            if not task.done():
                                task.add_done_callback(_discard_task_result)
                                task.cancel()
                        raise
                    out: List[Any] = []
                    for tc in group_calls:
                        tid = tc.get("id", "")
                        if tid in by_id:
                            out.append(by_id[tid])
                    return out

                # 分类执行策略：
                # 1) 只读工具同组并行（带并发上限）
                # 2) 写操作工具逐个串行
                # 3) 保持原始 tool_calls 顺序边界（读组/写组分段）
                exec_results = []
                pending_read_only = []

                async def flush_read_only():
                    nonlocal pending_read_only, exec_results
                    while pending_read_only:
                        # Keep network-bound tools at their normal concurrency;
                        # run_group applies the severe-pressure semaphore only
                        # to local filesystem/indexing work.
                        chunk = pending_read_only[:MAX_PARALLEL_TOOLS]
                        pending_read_only = pending_read_only[MAX_PARALLEL_TOOLS:]
                        chunk_results = await run_group(chunk)
                        exec_results.extend(chunk_results)

                for tool_call in tool_calls_list:
                    await _raise_if_steer_requested(state, emit, "tool")
                    if not _state_run_has_write_fence(state):
                        raise asyncio.CancelledError()
                    if session_manager.is_interrupt_requested(state["session_id"]):
                        if _is_followup_interrupt(state["session_id"]):
                            raise asyncio.CancelledError()
                        break
                    if invalid_interactive_batch:
                        rejected = _blocked_tool_result(
                            str(tool_call.get("name") or "tool"),
                            tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {},
                            str(tool_call.get("id") or ""),
                            "ask_user must be the only tool call in its assistant turn; no tool in this mixed batch was executed.",
                        )
                        rejected["tool_call_index"] = tool_call.get("index")
                        rejected = await checkpoint_completed_tool_result(rejected)
                        exec_results.append(rejected)
                        continue
                    try:
                        early_idx = int(tool_call.get("index")) if tool_call.get("index") is not None else None
                    except Exception:
                        early_idx = None
                    if early_idx is not None and early_idx in early_tool_tasks:
                        await flush_read_only()
                        early_result = await _await_steerable(
                            state,
                            early_tool_tasks[early_idx],
                            emit,
                            "tool",
                        )
                        early_result = await checkpoint_completed_tool_result(early_result)
                        exec_results.append(early_result)
                        continue
                    if is_read_only_tool(tool_call):
                        pending_read_only.append(tool_call)
                        continue

                    # 写操作前先清空当前只读并行队列
                    await flush_read_only()
                    write_result = await _await_steerable(
                        state,
                        execute_one(tool_call),
                        emit,
                        "tool",
                        defer_steer=_tool_steer_policy(
                            str(tool_call.get("name") or ""),
                            tool_registry.resolve(str(tool_call.get("name") or "")),
                        )["interruptibility"] == "non_interruptible",
                    )
                    write_result = await checkpoint_completed_tool_result(write_result)
                    exec_results.append(write_result)

                # 末尾残留的只读工具并行执行
                await _raise_if_steer_requested(state, emit, "tool")
                await flush_read_only()

                _tool_batch_started_at = state.pop("_tool_batch_first_started_at", None)
                _tool_batch_duration_ms = _timing_ms(float(_tool_batch_started_at)) if _tool_batch_started_at is not None else 0
                _tool_phase_events = {"first_tool_start_to_all_results": _tool_batch_duration_ms}
                for _tool_result_index, _tool_result in enumerate(exec_results, start=1):
                    if isinstance(_tool_result, dict) and _tool_result.get("type") == "tool":
                        _tool_status = dict(_tool_result.get("tool_status") or {})
                        _tool_label = str(_tool_result.get("tool_name") or "tool")
                        _tool_phase_events[f"tool:{_tool_result_index}:{_tool_label}"] = int(_tool_status.get("duration_ms") or 0)
                execution_metrics.record_phase(
                    state["session_id"], str(state.get("_runtime_v2_run_id") or ""), int(iter_count),
                    "tool_execution", _tool_phase_events,
                    total_ms=_tool_batch_duration_ms,
                )

                # 处理每个工具的返回结果
                _round_tool_post_total_ms = 0
                for res in exec_results:
                    await _raise_if_steer_requested(state, emit, "tool")
                    if isinstance(res, Exception):
                        logger.error(f"工具执行异常: {res}")
                        state["_react_ui_tool_fail_count"] = int(state.get("_react_ui_tool_fail_count", 0) or 0) + 1
                        await _emit_live_metrics(state, emit)
                        error_msg = ToolMessage(content=f"工具执行异常: {str(res)}", tool_call_id="unknown")
                        work_messages.append(error_msg)
                        llm_history.append(error_msg)
                        _runtime_v2_append_model_message(state, error_msg)
                        # 追加流式事件
                        await _push_stream_event(state, {"type": "error", "content": f"工具执行异常: {str(res)}"}, emit=emit)
                        continue
                    if res is None:
                        continue

                    control_result = res.get("_control_result")
                    if (
                        isinstance(control_result, dict)
                        and control_result.get("type") == "compact"
                    ):
                        # 更新压缩后的历史
                        llm_history = list(state["llm_history"])
                        state["llm_history"] = llm_history
                        _fb_ck = _compress_history_fallback_kind(llm_history)
                        if _fb_ck == "truncated":
                            _compact_note = (
                                "[系统通知：上下文已截尾（Conversation truncated）；更早内容请查本会话目录。]"
                            )
                            _status = (
                                "【context_manage·compact】上下文已截尾（Conversation truncated），"
                                "保留约半窗 token 尾部。"
                            )
                        elif bool(control_result.get("used_llm_summary")):
                            _compact_note = "[系统通知：对话已摘要，关键信息已写入 key_context]"
                            _status = "【context_manage·compact】已完成上下文裁剪与摘要"
                        else:
                            _compact_note = "[系统通知：对话已摘要，关键信息已写入 key_context]"
                            _status = "【context_manage·compact】已完成上下文裁剪"
                        work_messages.append(SystemMessage(content=_compact_note))
                        state["work_messages"] = work_messages
                        await _push_stream_event(
                            state,
                            {"type": "status", "content": _status},
                            emit=emit,
                        )
                        continue

                    if (
                        isinstance(control_result, dict)
                        and control_result.get("type") == "compact_noop"
                    ):
                        await _push_stream_event(
                            state,
                            {"type": "status", "content": "【context_manage·compact】当前上下文无需进一步裁剪或摘要"},
                            emit=emit,
                        )
                        continue

                    # 普通工具：添加到历史
                    # UI消息使用完整内容（tool_detail_ui），LLM消息使用截断内容（tool_detail_llm）
                    tool_post_timings: Dict[str, int] = {}
                    _t_tool_post = time.perf_counter()
                    if res.get("type") == "tool":
                        res = redact_sensitive_tool_obj(res)
                        res.setdefault(
                            "tool_status",
                            _tool_result_status(
                                str(res.get("tool_name") or ""),
                                res.get("result"),
                                failed=bool(res.get("tool_failed")),
                            ),
                        )
                    tool_post_timings["redact_result"] = _timing_ms(_t_tool_post)
                    _pipeline_step_timing_log("tool_result_post_step_timing", state["session_id"], "redact_result", tool_post_timings["redact_result"], react_iter=int(iter_count))

                    _t_tool_post = time.perf_counter()
                    if res.get("tool_failed"):
                        state["_react_ui_tool_fail_count"] = int(state.get("_react_ui_tool_fail_count", 0) or 0) + 1
                        await _emit_live_metrics(state, emit)
                    tool_post_timings["failure_metrics"] = _timing_ms(_t_tool_post)
                    _pipeline_step_timing_log("tool_result_post_step_timing", state["session_id"], "failure_metrics", tool_post_timings["failure_metrics"], react_iter=int(iter_count))
                    _t_tool_post = time.perf_counter()
                    _record_temporary_write_file(
                        state,
                        str(res.get("tool_name") or ""),
                        res.get("tool_args"),
                        bool(res.get("tool_failed")),
                    )
                    tool_post_timings["record_temp_file"] = _timing_ms(_t_tool_post)
                    _pipeline_step_timing_log("tool_result_post_step_timing", state["session_id"], "record_temp_file", tool_post_timings["record_temp_file"], react_iter=int(iter_count))
                    _t_tool_post = time.perf_counter()
                    if not res.get("_history_persisted"):
                        tool_msg_ui = ToolMessage(content=res["tool_detail_ui"], tool_call_id=res["tool_id"])
                        tool_msg_llm = ToolMessage(content=res["tool_detail_llm"], tool_call_id=res["tool_id"])
                        work_messages.append(tool_msg_ui)
                        llm_history.append(tool_msg_llm)
                        _persist_state_with_model_append(state, tool_msg_llm)
                        state["llm_history"] = llm_history
                        state["work_messages"] = work_messages
                    tool_post_timings["append_model_history"] = _timing_ms(_t_tool_post)
                    _pipeline_step_timing_log("tool_result_post_step_timing", state["session_id"], "append_model_history", tool_post_timings["append_model_history"], react_iter=int(iter_count))

                    tool_results.append({
                        "tool": res["tool_name"],
                        "args": res["tool_args"],
                        "result": res["result"]
                    })
                    logger.info(f"工具调用: {res['tool_name']}({str(res['tool_args'])}) -> {res['result_for_log']}")

                    # 并行只读批已在 run_group 内发 SSE；单工具/写路径在此发
                    _t_tool_post = time.perf_counter()
                    if emit and not (isinstance(res, dict) and res.get("_sse_emitted")):
                        r = emit({
                            "type": "tool_call",
                            "tool": redact_sensitive_tool_text(res["tool_name"]),
                            "args": redact_sensitive_tool_obj(res["tool_args"]),
                            "command_preview": _tool_command_preview(res["tool_name"], res["tool_args"]),
                            "result": redact_sensitive_tool_text(res["result"]),
                            "status": redact_sensitive_tool_obj(res.get("tool_status") or {}),
                            "tool_call_id": res.get("tool_id") or "",
                            "tool_call_index": res.get("tool_call_index"),
                            "react_iter": int(iter_count),
                            "ui": redact_sensitive_tool_obj(res.get("ui") or {}),
                        })
                        if inspect.isawaitable(r):
                            await r
                        state["_react_ui_tool_count"] = int(state.get("_react_ui_tool_count", 0) or 0) + 1
                        await _emit_live_metrics(state, emit)
                        await asyncio.sleep(0)
                    tool_post_timings["ui_emit"] = _timing_ms(_t_tool_post)
                    _pipeline_step_timing_log("tool_result_post_step_timing", state["session_id"], "ui_emit", tool_post_timings["ui_emit"], react_iter=int(iter_count))
                    _pipeline_timing_log(
                        "tool_result_post_timing",
                        state["session_id"],
                        tool_post_timings,
                        react_iter=int(iter_count),
                        tool=redact_sensitive_tool_text(res.get("tool_name") or ""),
                        tool_failed=bool(res.get("tool_failed")),
                        sse_emitted=bool(isinstance(res, dict) and res.get("_sse_emitted")),
                    )
                    execution_metrics.record_phase(
                        state["session_id"],
                        str(state.get("_runtime_v2_run_id") or ""),
                        int(iter_count),
                        "tool_result_post",
                        {
                            f"{str(res.get('tool_name') or 'tool')}:{key}": value
                            for key, value in tool_post_timings.items()
                        },
                        total_ms=sum(int(v or 0) for v in tool_post_timings.values()),
                    )
                    _round_tool_post_total_ms += sum(int(v or 0) for v in tool_post_timings.values())

                _t_tool_post_all = time.perf_counter()
                state["llm_history"] = llm_history
                state["work_messages"] = work_messages
                state["_runtime_stage"] = "persist_after_tools"
                state["_tool_batch_completed_at"] = time.perf_counter()
                _persist_state(state)
                persist_after_tools_ms = _timing_ms(_t_tool_post_all)
                _pipeline_step_timing_log("tool_to_next_api_step_timing", state["session_id"], "persist_state", persist_after_tools_ms, react_iter=int(iter_count), tools=len(tool_calls_list or []))
                completion_judge_requested = any(
                    isinstance(result, dict)
                    and not bool(result.get("tool_failed"))
                    and bool(
                        (result.get("_tool_outcome_metadata") or {}).get(
                            "workflow_completion_requested"
                        )
                    )
                    for result in exec_results
                )
                if completion_judge_requested:
                    await _workflow_callbacks().call_async("completion_boundary", state, emit)
                _t_tool_post_all = time.perf_counter()
                if await _consume_steer_messages(state, emit=emit, modes={"append", "interrupt"}):
                    steer_check_ms = _timing_ms(_t_tool_post_all)
                    _pipeline_step_timing_log("tool_to_next_api_step_timing", state["session_id"], "steer_check", steer_check_ms, react_iter=int(iter_count), tools=len(tool_calls_list or []), outcome="steer_restart")
                    _pipeline_timing_log(
                        "tool_to_next_api_timing",
                        state["session_id"],
                        {"persist_state": persist_after_tools_ms, "steer_check": steer_check_ms},
                        react_iter=int(iter_count),
                        tools=len(tool_calls_list or []),
                        outcome="steer_restart",
                    )
                    execution_metrics.record_phase(
                        state["session_id"], str(state.get("_runtime_v2_run_id") or ""), int(iter_count),
                        "tool_to_next_api", {"persist_state": persist_after_tools_ms, "steer_check": steer_check_ms},
                        total_ms=int(persist_after_tools_ms + steer_check_ms), outcome="steer_restart",
                    )
                    execution_metrics.record_phase(
                        state["session_id"], str(state.get("_runtime_v2_run_id") or ""), int(iter_count),
                        "round_postprocess",
                        {"tool_result_post": _round_tool_post_total_ms, "persist_state": persist_after_tools_ms, "steer_check": steer_check_ms},
                        total_ms=int(_round_tool_post_total_ms + persist_after_tools_ms + steer_check_ms),
                    )
                    _req_wall_end = state.pop("_req_wall_start", None)
                    if _req_wall_end is not None:
                        execution_metrics.record_request(
                            state["session_id"], str(state.get("_runtime_v2_run_id") or ""), int(iter_count),
                            wall_ms=int(max(0.0, (time.perf_counter() - _req_wall_end) * 1000.0)),
                        )
                        state["_last_round_end_perf"] = time.perf_counter()
                    state.pop("_steer_rollback_marker", None)
                    _reset_steer_control(state)
                    llm_history = list(state["llm_history"])
                    work_messages = list(state["work_messages"])
                    final_result_retries = 0
                    state["final_result_retries"] = 0
                    state["empty_final_retries"] = 0
                    # A steer consumed on the configured final iteration must
                    # still receive the promised next model request.
                    max_react_iter = max(max_react_iter, iter_count + 1)
                    continue
                steer_check_ms = _timing_ms(_t_tool_post_all)
                _pipeline_step_timing_log("tool_to_next_api_step_timing", state["session_id"], "steer_check", steer_check_ms, react_iter=int(iter_count), tools=len(tool_calls_list or []), outcome="next_react_iter")
                _pipeline_timing_log(
                    "tool_to_next_api_timing",
                    state["session_id"],
                    {"persist_state": persist_after_tools_ms, "steer_check": steer_check_ms},
                    react_iter=int(iter_count),
                    tools=len(tool_calls_list or []),
                    outcome="next_react_iter",
                )
                # GC probe anchor: deltas reported in the next pre_api_timing
                # cover the whole inter-round gap where unaccounted wall time
                # was observed (suspected gen2 pauses over the large runtime
                # projection).
                try:
                    _gc_stats = gc.get_stats()
                    state["_gc_round_anchor"] = (
                        int(_gc_stats[2]["collections"]),
                        int(_gc_stats[2]["collected"]),
                        int(_gc_stats[0]["collections"]),
                    )
                    # Event-loop stall anchor: (wall clock, loop clock) pair.
                    # wall_delta - loop_delta at the next pre_api log is the
                    # time the event loop was not running callbacks (GIL
                    # starvation, blocking sync work) during the gap.
                    state["_loop_stall_anchor"] = (
                        time.perf_counter(),
                        asyncio.get_running_loop().time(),
                    )
                except Exception:
                    state.pop("_gc_round_anchor", None)
                    state.pop("_loop_stall_anchor", None)
                execution_metrics.record_phase(
                    state["session_id"], str(state.get("_runtime_v2_run_id") or ""), int(iter_count),
                    "tool_to_next_api", {"persist_state": persist_after_tools_ms, "steer_check": steer_check_ms},
                    total_ms=int(persist_after_tools_ms + steer_check_ms), outcome="next_react_iter",
                )
                execution_metrics.record_phase(
                    state["session_id"], str(state.get("_runtime_v2_run_id") or ""), int(iter_count),
                    "round_postprocess",
                    {"tool_result_post": _round_tool_post_total_ms, "persist_state": persist_after_tools_ms, "steer_check": steer_check_ms},
                    total_ms=int(_round_tool_post_total_ms + persist_after_tools_ms + steer_check_ms),
                )
                _req_wall_end = state.pop("_req_wall_start", None)
                if _req_wall_end is not None:
                    execution_metrics.record_request(
                        state["session_id"], str(state.get("_runtime_v2_run_id") or ""), int(iter_count),
                        wall_ms=int(max(0.0, (time.perf_counter() - _req_wall_end) * 1000.0)),
                    )
                    state["_last_round_end_perf"] = time.perf_counter()
                state.pop("_steer_rollback_marker", None)
                state["_runtime_stage"] = "react"

            hook_pause_reason = str(state.pop("_hook_pause_requested", "") or "").strip()
            if hook_pause_reason:
                final_content = f"执行已由 Hook 暂停：{hook_pause_reason}"
                await _push_stream_event(
                    state,
                    {"type": "status", "content": final_content},
                    emit=emit,
                )
                break

            # ---------- 2.8 重复检测（须在工具结果写入 llm_history 之后，避免 OpenAI 报 tool_calls 顺序错误） ----------
            # 文本重复检测只对比「正文」；思考单独存在于 reasoning 字段，不参与与 last_response 的混比
            current_content = (response_text or "").strip()
            # 仅调工具、assistant 正文为空时，多步会得到 ""==""，不能算作「重复输出」
            is_text_repeat = bool(current_content) and (current_content == last_response_content)
            current_tool_calls = tool_calls_list if tool_calls_list else []
            current_tool_signature = None
            if current_tool_calls:
                signature_parts = []
                for tc in current_tool_calls:
                    tool_name = tc.get("name", "")
                    args = tc.get("args", {})
                    args_str = json.dumps(args, sort_keys=True)
                    signature_parts.append(f"{tool_name}:{args_str}")
                current_tool_signature = "|".join(signature_parts)
            is_tool_repeat = (current_tool_signature and last_tool_calls_signature and current_tool_signature == last_tool_calls_signature)

            if is_text_repeat or is_tool_repeat:
                repeat_count += 1
                logger.warning(f"检测到重复行为（{repeat_count}/{REPEAT_DETECTION_THRESHOLD_ERROR}）：文本重复={is_text_repeat}, 工具重复={is_tool_repeat}")
                if repeat_count >= REPEAT_DETECTION_THRESHOLD_SUMMARY and not reminder_inserted:
                    logger.info("重复输出达到摘要阈值，插入强制提醒")
                    if is_tool_repeat and current_tool_signature:
                        repeat_tool_info = f"重复调用工具: {current_tool_calls[0].get('name')}，参数: {current_tool_calls[0].get('args')}"
                    else:
                        repeat_tool_info = "重复输出相同内容"
                    reminder_msg = SystemMessage(
                        content=f"[系统强制提醒] 检测到连续重复行为（{repeat_count}次）。{repeat_tool_info}。请立即停止当前重复模式，回顾任务目标，采取以下措施之一：\n"
                                f"1. 如果任务已完成，请输出最终结果。\n"
                                f"2. 如果遇到障碍，请更新可用的计划状态，并尝试不同的工具或方法。\n"
                                f"3. 如果无法继续，请输出一条错误说明。\n"
                                f"禁止继续重复相同的工具调用或输出。"
                    )
                    llm_history.append(reminder_msg)
                    work_messages.append(reminder_msg)
                    _runtime_v2_append_model_message(state, reminder_msg)
                    state["llm_history"] = llm_history
                    state["work_messages"] = work_messages
                    _persist_state(state)
                    reminder_inserted = True
                    state["reminder_inserted"] = True
                    await _push_stream_event(state, {"type": "status", "content": f"检测到连续重复行为（{repeat_count}次），已插入强制提醒"}, emit=emit)
                if repeat_count >= REPEAT_DETECTION_THRESHOLD_ERROR:
                    logger.error(f"重复行为超过阈值（{REPEAT_DETECTION_THRESHOLD_ERROR}次），终止循环")
                    _snippet = (response_text or "").strip() or (reasoning_text or "")[:200]
                    final_content = f"检测到连续重复行为，已终止任务。最近输出：{_snippet}"
                    state["repeat_count"] = 0
                    state["last_response_content"] = None
                    state["last_tool_calls_signature"] = None
                    state["reminder_inserted"] = False
                    break
            else:
                repeat_count = 0
                reminder_inserted = False
                last_response_content = current_content
                last_tool_calls_signature = current_tool_signature
                state["repeat_count"] = 0
                state["last_response_content"] = current_content
                state["last_tool_calls_signature"] = current_tool_signature
                state["reminder_inserted"] = False

            if not tool_calls_list:
                # 没有工具调用 → 终稿只取正文；仅有思考、无正文时由前端 llm_reasoning 展示，不当作最终回答文本
                if await _consume_steer_messages(state, emit=emit, modes={"append", "interrupt"}):
                    _reset_steer_control(state)
                    llm_history = list(state["llm_history"])
                    work_messages = list(state["work_messages"])
                    final_result_retries = 0
                    state["final_result_retries"] = 0
                    state["empty_final_retries"] = 0
                    max_react_iter = max(max_react_iter, iter_count + 1)
                    continue
                if iter_count < max_react_iter and _inject_pending_subagent_notes(current_run_only=True):
                    final_result_retries = 0
                    state["final_result_retries"] = 0
                    state["empty_final_retries"] = 0
                    await _push_stream_event(
                        state,
                        {"type": "status", "content": "子任务结果已返回，正在纳入当前回答"},
                        emit=emit,
                    )
                    continue
                final_content = _strip_think_tags_for_final(response_text)
                if not final_content and final_result_retries < final_result_retry_max:
                    final_result_retries += 1
                    state["final_result_retries"] = final_result_retries
                    state["empty_final_retries"] = final_result_retries
                    _persist_state(state)
                    await _push_stream_event(
                        state,
                        {
                            "type": "status",
                            "content": f"模型未输出最终内容，正在重试（{final_result_retries}/{final_result_retry_max}）",
                        },
                        emit=emit,
                    )
                    continue
                state["final_result_retries"] = 0
                state["empty_final_retries"] = 0
                break

        else:
            # 达到最大迭代次数
            state["react_limit_reached"] = True
            final_content = (
                "本轮执行步骤已达到最大迭代次数。持久工作流会自动开始下一轮；"
                "普通会话可以手动继续任务。"
            )

    except _SteerRestartRequested:
        raise
    finally:
        pass

    # 本次运行 ReAct 统计：写入 ui_events，刷新页面后仍可显示耗时/步数/工具次数
    # 最后一步（通常是无工具收尾步）的步进墙钟与 run 级对账字段。
    _final_react_iter = int(state.get("_current_react_iter") or iter_count or 1)
    _final_req_wall_start = state.pop("_req_wall_start", None)
    if _final_req_wall_start is not None:
        execution_metrics.record_request(
            state["session_id"], str(state.get("_runtime_v2_run_id") or ""), _final_react_iter,
            wall_ms=int(max(0.0, (time.perf_counter() - _final_req_wall_start) * 1000.0)),
        )
    execution_metrics.record_run_fields(
        state["session_id"], str(state.get("_runtime_v2_run_id") or ""),
        startup_ms=int(state.get("_run_startup_ms") or 0),
        round_gap_ms=int(state.get("_round_gap_ms_total") or 0),
    )

    dur_ms = int(max(0.0, (time.monotonic() - react_wall_start) * 1000.0))
    tool_n = int(state.pop("_react_ui_tool_count", 0) or 0)
    fail_n = int(state.pop("_react_ui_tool_fail_count", 0) or 0)
    if emit:
        await _push_stream_event(
            state,
            {
                "type": "process_metrics",
                "duration_ms": dur_ms,
                "react_loops": int(iter_count),
                "tool_calls": tool_n,
                "tool_failures": fail_n,
            },
            emit=emit,
        )

    # ========== 3. 循环结束，添加标记并保存（仅内部使用，不在前端实时打印） ==========
    _workflow_callbacks().call("after_run", state)
    if not (llm_history and isinstance(llm_history[-1], SystemMessage) and llm_history[-1].content == "Loop finished"):
        end_msg = SystemMessage(content="Loop finished")
        llm_history.append(end_msg)
        work_messages = list(state.get("work_messages", []))
        work_messages.append(end_msg)
        _runtime_v2_append_model_message(state, end_msg)
        state["llm_history"] = llm_history
        state["work_messages"] = work_messages
        _persist_state(state)

    state["final_response"] = final_content
    return state


async def react_node(state: State, emit: Optional[Callable[[Dict[str, Any]], Any]] = None) -> State:
    """Run ReAct with non-recursive steer replanning.

    A steer restarts the logical planning pass without stacking coroutine frames.
    Completed history remains in ``state``; only an unclosed tool tail is rolled
    back before the durable steer message is claimed and committed.
    """
    while True:
        try:
            return await _react_node_once(state, emit=emit)
        except _SteerRestartRequested:
            _rollback_steer_partial_turn(state)
            consumed = await _consume_steer_messages(state, emit=emit, modes={"interrupt"})
            _reset_steer_control(state)
            if not consumed:
                continue
            state["final_result_retries"] = 0
            state["empty_final_retries"] = 0
            state["repeat_count"] = 0
            state["last_response_content"] = None
            state["last_tool_calls_signature"] = None
            state["reminder_inserted"] = False

def validate_final(state: State) -> State:
    """终稿已由 ReAct 产出；不再调用独立校验模型，仅推送 PASS 占位事件以保持 SSE/UI 兼容。"""
    if state.get("final_printed", False):
        return state
    cleaned = _cleanup_temporary_write_files(state)
    if cleaned:
        state["stream_events"].append(
            {
                "type": "status",
                "content": f"已清理临时文件 {len(cleaned)} 个（已移入 .trash）",
            }
        )
    state["stream_events"].append({"type": "validate_final", "result": "PASS", "reason": ""})
    _persist_state(state)
    return state


def prepare_final_event(state: State) -> State:
    """Prepare the final UI event without running slower finish-side work."""
    # 确保 user_input 存在
    if "user_input" not in state:
        for msg in reversed(state["dialogue"]):
            if isinstance(msg, UserMessage):
                state["user_input"] = msg.content
                break
        else:
            state["user_input"] = ""
        logger.warning("finish: user_input 缺失，已从对话记录中恢复")

    if state.get("final_printed", False):
        return state

    if not state.get("final_response"):
        state["final_response"] = "No result"

    if not state.get("_final_event_prepared"):
        state["stream_events"].append({"type": "final", "content": state["final_response"]})
        state["_final_event_prepared"] = True
    return state


def _strip_think_tags_for_final(text: str) -> str:
    raw = str(text or "")
    raw = re.sub(r"<think\b[^>]*>[\s\S]*?</think>", "", raw, flags=re.IGNORECASE).strip()
    raw = re.sub(r"^\s*<think\b[^>]*>[\s\S]*$", "", raw, flags=re.IGNORECASE).strip()
    raw = re.sub(r"</think\s*>", "", raw, flags=re.IGNORECASE).strip()
    return raw


_TITLE_PLACEHOLDER_NAMES = {"", "新会话", "未命名", "New Chat", "New Session"}
SESSION_TITLE_MAX_CHARS = 100


def _first_user_text_for_title(state: State) -> str:
    for msg in state.get("dialogue", []) or []:
        if isinstance(msg, UserMessage):
            return str(msg.content or "").strip()
    return str(state.get("user_input") or "").strip()


def _session_title_ui_inputs(session_id: str) -> tuple[str, str]:
    """Return the first visible user request and latest visible final answer."""
    loader = getattr(session_manager, "_load_ui_events_for_active_runtime", None)
    if not callable(loader):
        loader = getattr(session_manager, "get_ui_events_for_display", None)
    if not callable(loader):
        return "", ""
    try:
        events = list(loader(session_id) or [])
    except Exception as exc:
        logger.debug(
            "Unable to load persisted title inputs for session=%s: %s",
            session_id,
            exc,
        )
        return "", ""

    first_user = ""
    first_steer = ""
    latest_final = ""
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "").strip()
        content = str(event.get("content") or "").strip()
        if event_type in {"user", "human"} and content and not first_user:
            first_user = content
        elif event_type == "user_steer" and content and not first_steer:
            first_steer = content
        elif event_type == "final" and content:
            latest_final = content
    return first_user or first_steer, latest_final


def _session_title_inputs(state: State) -> tuple[str, str]:
    session_id = str(state.get("session_id") or "").strip()
    persisted_first, persisted_final = _session_title_ui_inputs(session_id)
    return (
        persisted_first or _first_user_text_for_title(state),
        persisted_final or str(state.get("final_response") or "").strip(),
    )


def _session_workflow_is_active_for_title(session_id: str) -> bool:
    """Keep a session untitled while an optional workflow remains active."""
    return bool(
        _workflow_callbacks().call("is_active_for_title", str(session_id or ""))
    )


def _normalize_generated_session_title(title: str) -> str:
    text = str(title or "").strip()
    text = re.sub(r"^```(?:text|md|markdown)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    text = re.sub(r"^\s*<think\b[^>]*>[\s\S]*?</think>\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^\s*<think\b[^>]*>[\s\S]*$", "", text, flags=re.IGNORECASE).strip()
    text = text.strip(" \t\r\n\"'`“”‘’《》")
    text = re.sub(r"^[标题：:]+", "", text).strip()
    text = re.sub(r"[\r\n]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:SESSION_TITLE_MAX_CHARS]


def _looks_like_local_path_title(text: str) -> bool:
    s = str(text or "")
    return bool(re.search(r"[A-Za-z]:[\\/]", s) or re.search(r"[/\\].+[/\\]", s))


def _looks_like_reasoning_tag_title(text: str) -> bool:
    s = str(text or "").strip().lower()
    return bool(
        s.startswith("<think")
        or s.startswith("</think")
        or "</think>" in s
        or s.startswith("is. the conversation")
    )


def _session_title_needs_generation(current_name: str, first_user: str) -> bool:
    name = str(current_name or "").strip()
    if name in _TITLE_PLACEHOLDER_NAMES:
        return True
    if _looks_like_reasoning_tag_title(name):
        return True
    user = str(first_user or "").strip()
    if user and name == user[: len(name)] and len(name) <= SESSION_TITLE_MAX_CHARS:
        return True
    if _looks_like_local_path_title(name):
        return True
    return False


def _generate_session_title_with_diagnostics(
    session_id: str,
    first_user: str,
    final_response: str,
    prompt_language: Optional[str] = None,
) -> tuple[str, Optional[Dict[str, int]]]:
    if prompt_language is None:
        try:
            prompt_language = session_manager.get_session_prompt_language(session_id)
        except Exception:
            prompt_language = "zh-CN"
    title_template = (
        load_prompt_template("title_generator", "en")
        if normalize_prompt_language(prompt_language) == "en"
        else load_prompt_template("title_generator")
    )
    title_prompt = title_template.format(
        first_user=first_user,
        final_response=final_response or "",
    )
    def _usable_title(result: Dict[str, Any]) -> bool:
        raw = str(result.get("text") or "").strip()
        normalized = _normalize_generated_session_title(raw)
        return bool(
            normalized
            and not _looks_like_local_path_title(normalized)
            and not _looks_like_reasoning_tag_title(normalized)
            and str(result.get("finish_reason") or "").lower() != "length"
            and str(result.get("status") or "completed").lower() != "incomplete"
            and not str(result.get("refusal") or "").strip()
            and not str(result.get("error") or "").strip()
        )

    result = executor_one_shot_complete(
        [UserMessage(content=title_prompt)],
        session_id=session_id,
        purpose=LLMRequestPurpose.TITLE,
        temperature=0,
        timeout=TITLE_GENERATION_TIMEOUT_SEC,
        response_validator=_usable_title,
        # Title generation is provider-neutral. In particular, never inject a
        # DeepSeek-style `thinking` toggle into a Responses request.
        include_candidate_controls=False,
    )
    raw_title = str(result.get("text") or "").strip()
    candidate = result.get("_myagent_candidate")
    usage = result.get("usage")
    logger.info(
        "生成会话标题返回: session=%s model=%s provider=%s finish_reason=%s content_len=%s usable=true",
        session_id,
        redact_sensitive_tool_text(
            str(candidate.get("model") or "") if isinstance(candidate, dict) else ""
        ),
        str(candidate.get("provider") or "") if isinstance(candidate, dict) else "",
        str(result.get("finish_reason") or "") or None,
        len(raw_title),
    )
    return raw_title, dict(usage) if isinstance(usage, dict) else None


def _fallback_session_title(first_user: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(first_user or ""))
    text = re.sub(r"https?://\S+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[A-Za-z]:[\\/]\S+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n\"'`“”‘’《》")
    return _normalize_generated_session_title(text) or "新会话"


def _session_still_exists_for_title(session_id: str) -> bool:
    get_summary = getattr(session_manager, "get_session_summary", None)
    if not callable(get_summary):
        return True
    try:
        return get_summary(session_id) is not None
    except Exception:
        return True


def _run_session_title_generation(
    session_id: str,
    first_user: str,
    final_response: str,
) -> None:
    """Generate and apply a title without participating in the chat run lifecycle."""
    if not _session_still_exists_for_title(session_id):
        return
    if _session_workflow_is_active_for_title(session_id):
        return
    persisted_first, persisted_final = _session_title_ui_inputs(session_id)
    first_user = persisted_first or str(first_user or "").strip()
    final_response = persisted_final or str(final_response or "").strip()
    if not first_user:
        return
    metadata = session_manager._load_metadata(session_id)
    if not _session_title_needs_generation(str(metadata.get("name") or ""), first_user):
        return

    try:
        title, title_usage = _generate_session_title_with_diagnostics(
            session_id,
            first_user,
            final_response,
        )
    except Exception as exc:
        logger.warning("生成会话标题失败: session=%s error=%s", session_id, exc)
        title = _fallback_session_title(first_user)
        title_usage = None

    title = _normalize_generated_session_title(title)
    if not title or _looks_like_local_path_title(title):
        logger.warning("生成会话标题结果不可用，保留当前会话名: %r", title)
        return
    if title_usage:
        logger.info(
            "Session title (executor) tokens: input=%s, output=%s",
            int(title_usage.get("prompt_tokens", 0) or 0),
            int(title_usage.get("completion_tokens", 0) or 0),
        )

    # The user may rename or delete the session while the detached request is
    # running.  Never recreate a deleted session or overwrite a manual rename.
    if not _session_still_exists_for_title(session_id):
        return
    if _session_workflow_is_active_for_title(session_id):
        return
    latest_metadata = session_manager._load_metadata(session_id)
    if not _session_title_needs_generation(
        str(latest_metadata.get("name") or ""),
        first_user,
    ):
        return
    session_manager.set_session_name(session_id, title)


def _session_title_worker() -> None:
    while True:
        session_id, first_user, final_response = _TITLE_GENERATION_QUEUE.get()
        try:
            _run_session_title_generation(session_id, first_user, final_response)
        except Exception:
            logger.exception("后台生成会话标题异常: session=%s", session_id)
        finally:
            with _TITLE_GENERATION_LOCK:
                _TITLE_GENERATION_PENDING.discard(session_id)
            _TITLE_GENERATION_QUEUE.task_done()


def _ensure_session_title_workers_started() -> None:
    global _TITLE_GENERATION_WORKERS_STARTED
    with _TITLE_GENERATION_LOCK:
        if _TITLE_GENERATION_WORKERS_STARTED:
            return
        _TITLE_GENERATION_WORKERS_STARTED = True
        for index in range(TITLE_GENERATION_WORKERS):
            threading.Thread(
                target=_session_title_worker,
                name=f"session-title-{index + 1}",
                daemon=True,
            ).start()


def schedule_session_title_generation(state: State) -> bool:
    """Queue title generation once the session has stable title inputs."""
    session_id = str(state.get("session_id") or "").strip()
    if not session_id:
        return False
    if _session_workflow_is_active_for_title(session_id):
        return False
    first_user, final_response = _session_title_inputs(state)
    if not first_user:
        return False
    try:
        metadata = session_manager._load_metadata(session_id)
    except Exception as exc:
        logger.warning("读取会话标题状态失败: session=%s error=%s", session_id, exc)
        return False
    if not _session_title_needs_generation(str(metadata.get("name") or ""), first_user):
        return False

    with _TITLE_GENERATION_LOCK:
        if session_id in _TITLE_GENERATION_PENDING:
            return False
        _TITLE_GENERATION_PENDING.add(session_id)
    _ensure_session_title_workers_started()
    _TITLE_GENERATION_QUEUE.put(
        (session_id, first_user, final_response)
    )
    return True


def is_session_title_generation_pending(session_id: str) -> bool:
    with _TITLE_GENERATION_LOCK:
        return str(session_id or "").strip() in _TITLE_GENERATION_PENDING


def finish(state: State) -> State:
    """最终处理：保存会话并输出最终结果；标题由独立后台任务生成。"""
    state = prepare_final_event(state)

    if state.get("final_printed", False):
        return state

    # 记录 LLM 调用详情及 token 统计（以各步 call 上记录的 usage 为准）
    total_input_tokens = 0
    total_output_tokens = 0
    if state.get("llm_calls"):
        logger.info("=== LLM Call Details ===")
        for call in state["llm_calls"]:
            usage = call.get("usage")
            if usage:
                total_input_tokens += int(usage.get("prompt_tokens", 0) or 0)
                total_output_tokens += int(usage.get("completion_tokens", 0) or 0)

            logger.info("Model: %s", redact_sensitive_tool_text(call.get("model")))
            logger.info(">>> Agent to LLM:")
            for msg in call["request"]:
                role = msg.get('role', 'unknown')
                content = msg.get('content', '')
                if role in ("user", "user&history"):
                    content = _truncate_xml_content_blocks(content, LOG_TRUNCATE_KEEP_CHARS)
                else:
                    content = truncate_head_tail(content, LOG_TRUNCATE_KEEP_CHARS)
                logger.info(f"{COLOR_WHITE}  {role}: {content}{COLOR_RESET}")

            logger.info("<<< LLM Response:")
            resp = call["response"]
            if resp.get("tool_calls"):
                tool_calls_text = truncate_head_tail(str(resp["tool_calls"]), LOG_TRUNCATE_KEEP_CHARS)
                logger.info(f"{COLOR_BLUE}  tool_calls: {tool_calls_text}{COLOR_RESET}")
            else:
                content = resp.get('content', '')
                content = truncate_head_tail(content, LOG_TRUNCATE_KEEP_CHARS)
                logger.info(f"{COLOR_YELLOW}  {content}{COLOR_RESET}")
            logger.info("<<Finish>>")
    else:
        logger.info("=== LLM Call Details ===")
        logger.info("(No LLM calls recorded)")

    if state.get("llm_calls"):
        logger.info(
            f"Total tokens: input={total_input_tokens}, output={total_output_tokens}, total={total_input_tokens+total_output_tokens}"
        )

    # 终稿：与最后一条 is_assistant_response 同文则只保留一条，标 is_final 并去 is_assistant_response（无重复 agent/llm）
    fr = (state.get("final_response") or "").strip()
    llm2, need_llm = apply_final_dedup_to_messages(state["llm_history"], fr)
    state["llm_history"] = llm2
    _final_ai_kw: Dict[str, Any] = {
        "content": fr,
        "metadata": {"is_final": True},
        "additional_kwargs": build_assistant_additional_kwargs(""),
    }
    if need_llm:
        state["llm_history"].append(AssistantMessage(**_final_ai_kw))
    wm0 = list(state.get("work_messages", []))
    wm2, need_wm = apply_final_dedup_to_messages(wm0, fr)
    state["work_messages"] = wm2
    if need_wm:
        state["work_messages"].append(AssistantMessage(**_final_ai_kw))
    _persist_session_messages_with_model_replace(state, state["llm_history"], "finish")
    state["final_printed"] = True
    return state


# ==================== 流式执行辅助 ====================
async def astream_events(
    user_input: str,
    session_id: str = None,
    should_stop: Optional[Callable[[str], bool]] = None,
    run_id: Optional[str] = None,
    ui_user_event_type: str = "user",
    ui_user_content: Optional[str] = None,
    context_token_mode: Optional[str] = None,
    user_operation_id: str = "",
    prompt_language: str = "zh-CN",
    user_content: Optional[Any] = None,
    preserve_unread_result: bool = False,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    顺序执行 react_node → validate_final（无独立校验 LLM）→ finish，通过队列实时向前端推送事件。
    """
    executor_http_client.interactions.clear()
    submitted_user_input = user_input
    pre_run_timings: Dict[str, int] = {}
    run_bootstrap: Optional[Dict[str, Any]] = None
    _t_stream_entry = time.perf_counter()
    logger.info("run_phase astream_entry session=%s", session_id)

    requested_prompt_language = str(prompt_language or "").strip()
    sid_in = str(session_id or "").strip()
    if sid_in:
        try:
            from session_lifecycle import is_session_deleted

            if is_session_deleted(sid_in):
                raise ValueError(f"Session {sid_in} was deleted")
        except ValueError:
            raise
        except Exception:
            pass
        session_id = sid_in
        if _runtime_v2_is_primary():
            _t_bootstrap = time.perf_counter()
            run_bootstrap = _load_runtime_v2_run_bootstrap(session_id)
            key_context = str(run_bootstrap.get("context_summary") or "")
            _pre_api_timing_mark(pre_run_timings, "load_run_bootstrap", _t_bootstrap)
        else:
            key_context = _load_key_context_for_run(session_id)
    else:
        session_id, _, _, _, key_context, _metadata = (
            session_manager.get_or_create_session(session_id)
        )
    if requested_prompt_language:
        prompt_language = normalize_prompt_language(requested_prompt_language)
    else:
        try:
            prompt_language = session_manager.get_session_prompt_language(session_id)
        except Exception:
            prompt_language = "zh-CN"
    try:
        session_manager.set_session_prompt_language(session_id, prompt_language)
    except Exception:
        logger.debug("Unable to persist prompt language for session=%s", session_id, exc_info=True)
    runtime_v2_run_id = str(run_id or "").strip() or str(uuid.uuid4())
    plugin_command_context = ""
    _t_plugin_dispatch = time.perf_counter()
    try:
        from agent_extensions import dispatch_plugin_command

        command_result = await dispatch_plugin_command(
            user_input,
            {
                "session_id": session_id,
                "run_id": runtime_v2_run_id,
                "project_root": str(WORK_DIR),
                "prompt_language": prompt_language,
            },
        )
        logger.info(
            "run_phase plugin_dispatch_done session=%s ms=%s matched=%s",
            session_id,
            int(max(0.0, (time.perf_counter() - _t_plugin_dispatch) * 1000)),
            bool(command_result.get("matched")),
        )
        if command_result.get("matched"):
            user_input = str(command_result.get("prompt") or "")
            plugin_command_context = str(
                command_result.get("additional_context") or ""
            ).strip()
            if ui_user_content is None:
                ui_user_content = submitted_user_input
    except Exception as exc:
        logger.exception("Plugin command dispatch failed")
        yield {"type": "error", "content": f"Plugin command failed: {exc}"}
        return
    prompt_hook_context = plugin_command_context
    try:
        # UserPromptSubmit runs before the main state/runner exists. Pump its
        # approval events through this generator so the UI can resolve them
        # before any Hook-controlled process starts.
        pre_hook_state: Dict[str, Any] = {
            "session_id": session_id,
            "user_input": user_input,
            "stream_events": [],
            "_runtime_v2_run_id": runtime_v2_run_id,
            "_submitted_user_input": submitted_user_input,
        }
        pre_hook_events: asyncio.Queue = asyncio.Queue()
        prompt_hook_task = asyncio.create_task(
            _dispatch_state_hook(
                "UserPromptSubmit",
                pre_hook_state,
                {
                    "matcher_value": user_input,
                    "input": {"prompt": user_input},
                },
                emit=pre_hook_events.put,
            )
        )
        while not prompt_hook_task.done():
            try:
                pre_hook_event = await asyncio.wait_for(
                    pre_hook_events.get(), timeout=0.25
                )
            except asyncio.TimeoutError:
                continue
            yield pre_hook_event
        prompt_hook = await prompt_hook_task
        logger.info(
            "run_phase prompt_hook_done session=%s ms=%s blocked=%s",
            session_id,
            int(max(0.0, (time.perf_counter() - _t_plugin_dispatch) * 1000)),
            bool(getattr(prompt_hook, "blocked", False)),
        )
        while not pre_hook_events.empty():
            yield pre_hook_events.get_nowait()
        if prompt_hook.updated_input is not None:
            user_input = str(
                prompt_hook.updated_input.get("prompt", prompt_hook.updated_input.get("user_input", user_input))
            )
        hook_context = str(prompt_hook.additional_context or "").strip()
        prompt_hook_context = "\n".join(
            item for item in (plugin_command_context, hook_context) if item
        )
        if prompt_hook.blocked or prompt_hook.should_pause or prompt_hook.requires_approval:
            reason = _hook_decision_reason(
                prompt_hook,
                "UserPromptSubmit Hook rejected this prompt.",
            )
            yield {"type": "error", "content": f"Hook stopped the prompt: {reason}"}
            return
    except Exception as exc:
        logger.exception("UserPromptSubmit Hook dispatch failed")
        yield {"type": "error", "content": f"Hook dispatch failed: {exc}"}
        return
    setup_logging(user_input, session_id or "")
    if run_bootstrap is not None:
        llm_history_dicts = list(run_bootstrap.get("messages") or [])
    else:
        _t_pre = time.perf_counter()
        llm_history_dicts = _load_model_history_dicts_v2_primary(session_id, reconcile_legacy=True)
        _pre_api_timing_mark(pre_run_timings, "load_model_history", _t_pre)
    _t_pre = time.perf_counter()
    work_messages_dicts = _load_work_history_dicts_for_run(session_id)
    _pre_api_timing_mark(pre_run_timings, "load_work_messages", _t_pre)

    _t_pre = time.perf_counter()
    prev_work_messages = [_dict_to_message(m) for m in work_messages_dicts]
    prev_llm_history = [_dict_to_message(m) for m in llm_history_dicts]
    _pre_api_timing_mark(pre_run_timings, "decode_histories", _t_pre)
    _t_pre = time.perf_counter()
    prev_work_messages, prev_llm_history = _sanitize_loaded_histories_for_new_run(
        session_id,
        prev_work_messages,
        prev_llm_history,
        key_context,
        "sanitize_unclosed_tool_calls_before_chat",
    )
    _pre_api_timing_mark(pre_run_timings, "sanitize_histories", _t_pre)

    user_message = UserMessage(
        content=user_content if isinstance(user_content, list) else user_input
    )
    context_token_mode = get_context_token_mode(context_token_mode)

    new_work_messages = prev_work_messages + [user_message]
    new_llm_history = prev_llm_history + [user_message]
    if prompt_hook_context:
        prompt_context_message = SystemMessage(
            content=f"[Hook additional context · UserPromptSubmit]\n{prompt_hook_context}"
        )
        new_work_messages.append(prompt_context_message)
        new_llm_history.append(prompt_context_message)
    workflow_note = _workflow_callbacks().call("continuation_message", session_id)
    if workflow_note is not None:
        new_work_messages.append(workflow_note)
        new_llm_history.append(workflow_note)
    state: State = {
        "dialogue": derive_dialogue_from_assistant_history(new_llm_history),
        "work_messages": new_work_messages,
        "llm_history": new_llm_history,
        "user_input": user_input,
        "final_response": "",
        "stream_events": [],
        "final_printed": False,
        "session_id": session_id,
        "llm_calls": [],
        "key_context": key_context,
        "_runtime_v2_run_id": runtime_v2_run_id,
        "_submitted_user_input": submitted_user_input,
        "_tool_review_assistant_context": [],
        "_tool_review_user_followups": [],
        "_pre_run_timings": pre_run_timings,
        "_context_token_mode": context_token_mode,
        "_prompt_language": prompt_language,
    }
    _workflow_callbacks().call("initialize_state", state, prev_work_messages)
    _workflow_callbacks().call("capture_dialogue",
        state,
        "user",
        user_message.content,
        kind="question",
    )
    session_manager.clear_interrupt(session_id, runtime_v2_run_id)
    steer_control = _register_steer_run_control(session_id, runtime_v2_run_id)
    state["_steer_control"] = steer_control

    queue: asyncio.Queue = asyncio.Queue()
    consumer_attached = True
    runtime_v2_terminal_mirrored = False

    def mirror_runtime_v2_sync(event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if not _runtime_v2_is_primary():
            return
        try:
            from runtime_v2.mirror import RuntimeMirror

            mirror = RuntimeMirror(
                session_manager.sessions_dir,
                path_resolver=getattr(session_manager, "_resolve_session_path", None),
                transaction_timeout_seconds=_runtime_v2_react_transaction_timeout_seconds(),
            )
            if event_type == "run_started":
                mirror.mirror_run_started(session_id, runtime_v2_run_id, payload)
            elif event_type == "run_finished":
                mirror.mirror_run_finished(session_id, runtime_v2_run_id, payload)
            elif event_type == "run_interrupted":
                mirror.mirror_run_interrupted(session_id, runtime_v2_run_id, payload)
            elif event_type == "run_failed":
                mirror.mirror_run_failed(session_id, str((payload or {}).get("error") or "unknown error"), runtime_v2_run_id, payload)
            else:
                mirror.append(session_id, event_type, payload or {}, run_id=runtime_v2_run_id)
        except Exception as mirror_error:
            logger.debug("Runtime V2 mirror run event failed: %s", mirror_error)

    def mirror_runtime_v2(event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        nonlocal runtime_v2_terminal_mirrored
        if runtime_v2_terminal_mirrored and event_type not in {"run_finished", "run_interrupted", "run_failed"}:
            logger.info(
                "suppressed post-terminal runtime event: session=%s run_id=%s type=%s",
                session_id,
                runtime_v2_run_id,
                event_type,
            )
            return
        if event_type in {"run_finished", "run_interrupted", "run_failed"}:
            if runtime_v2_terminal_mirrored:
                return
            runtime_v2_terminal_mirrored = True
        mirror_runtime_v2_sync(event_type, dict(payload or {}))

    async def emit(ev: Dict[str, Any]) -> None:
        # 与浏览器 SSE 一致；ephemeral（如 llm_*_delta）仅实时推送，不写入 ui_events
        # 子 agent 转发事件仅推 SSE，不写入父会话 ui_events
        ev = dict(ev)
        ev.setdefault("run_id", runtime_v2_run_id)
        event_type = str(ev.get("type") or "")
        if runtime_v2_terminal_mirrored and event_type not in {"run_finished", "run_interrupted", "run_failed"}:
            logger.info(
                "suppressed post-terminal stream event: session=%s run_id=%s type=%s",
                session_id,
                runtime_v2_run_id,
                event_type,
            )
            return
        runtime_committed = bool(ev.get("_runtime_v2_committed"))
        if ev.get("type") == "final":
            model_content = str(ev.get("content") or "")
            ui_content = await asyncio.to_thread(
                _snapshot_assistant_ui_content, session_id, model_content
            )
            ev["content"] = ui_content
            if not runtime_committed:
                runtime_committed = _runtime_v2_commit_assistant_final(
                    state, model_content, ui_content=ui_content
                )
        public_event = {k: v for k, v in ev.items() if k != "_runtime_v2_committed"}
        persist = should_persist_ui_event(ev) and not runtime_committed
        if persist and ev.get("type") != "tool_call":
            session_manager.append_ui_event(session_id, ev)
        if persist and ev.get("type") == "tool_call":
            # A completed tool row is a durable UI claim.  Persist it before
            # either the originating response or reconnecting observers can
            # display it, so interrupt/refresh cannot expose an uncommitted
            # result.
            await asyncio.to_thread(session_manager.append_ui_event, session_id, ev)
            await publish_session_event(session_id, public_event)
            if consumer_attached:
                await queue.put(public_event)
            return
        await publish_session_event(session_id, public_event)
        if consumer_attached:
            await queue.put(public_event)

    power_guard = AgentRunPowerGuard()

    async def on_runtime_resume(resume: RuntimeResume) -> None:
        state["_accumulated_suspend_seconds"] = power_guard.monitor.accumulated_suspend_seconds
        stage = str(state.get("_runtime_stage") or "unknown")
        payload = {
            "gap_seconds": round(resume.gap_seconds, 3),
            "suspended_seconds": round(resume.suspended_seconds, 3),
            "accumulated_suspend_seconds": round(power_guard.monitor.accumulated_suspend_seconds, 3),
            "previous_stage": stage,
            "cause": resume.cause,
        }
        logger.warning(
            "runtime_resumed session=%s run_id=%s suspended_seconds=%.3f previous_stage=%s",
            session_id,
            runtime_v2_run_id,
            resume.suspended_seconds,
            stage,
        )
        if resume.cause != "system_sleep":
            # A Python watchdog thread can itself be delayed by GIL/CPU
            # starvation.  That is useful diagnostics, but it is not reliable
            # evidence that the process was externally suspended and must not
            # be presented to the user as a resume event.
            logger.info(
                "runtime_watchdog_delay_suppressed session=%s run_id=%s gap_seconds=%.3f",
                session_id,
                runtime_v2_run_id,
                resume.gap_seconds,
            )
            power_guard.monitor.mark_progress()
            return
        await asyncio.to_thread(mirror_runtime_v2, "runtime_resumed", payload)
        await emit({
            "type": "runtime_resumed",
            "content": (
                "检测到系统睡眠约 %.0f 秒，任务已恢复"
                if resume.cause == "system_sleep"
                else "检测到 Agent 进程暂停约 %.0f 秒，任务已恢复"
            ) % resume.suspended_seconds,
            "ephemeral": True,
            **payload,
        })
        power_guard.monitor.mark_progress()

    async def runner():
        nonlocal state
        completed = False
        terminal_event = {"type": "run_interrupted", "ephemeral": True}
        try:
            await power_guard.start(on_runtime_resume)
            execution_metrics.start_run(
                session_id,
                runtime_v2_run_id,
                "chat",
                user_input,
            )
            state["_run_start_perf"] = time.perf_counter()
            logger.info(
                "run_phase runner_started session=%s since_entry_ms=%s",
                session_id,
                int(max(0.0, (state["_run_start_perf"] - _t_stream_entry) * 1000)),
            )
            # 用户气泡由前端已画；此处只写入与流顺序一致的持久化，供刷新与 SSE 同源
            run_start_timings: Dict[str, int] = {}
            _t_run_start = time.perf_counter()
            mirror_runtime_v2("run_started", {"mode": "chat"})
            run_start_timings["mirror_run_started"] = _timing_ms(_t_run_start)
            _pipeline_step_timing_log(
                "run_start_step_timing",
                session_id,
                "mirror_run_started",
                run_start_timings["mirror_run_started"],
                run_id=runtime_v2_run_id,
                mode="chat",
            )
            _t_run_start = time.perf_counter()
            user_ui_type = "user_steer" if str(ui_user_event_type or "") == "user_steer" else "user"
            user_ui_event = {"type": user_ui_type, "content": ui_user_content if ui_user_content is not None else user_input}
            if user_ui_type == "user_steer":
                user_ui_event["steer"] = True
            if preserve_unread_result:
                user_ui_event["preserve_unread_result"] = True
            atomic_user_turn = _runtime_v2_commit_user_turn(
                state,
                user_message,
                ui_content=str(user_ui_event.get("content") or ""),
                ui_type=user_ui_type,
                operation_id=str(user_operation_id or "").strip(),
                ui_metadata={"preserve_unread_result": True} if preserve_unread_result else None,
            )
            if not atomic_user_turn:
                _runtime_v2_append_model_message(state, user_message)
            if user_ui_type == "user_steer" and str(user_operation_id or "").strip():
                transition_session_steer(
                    session_id,
                    str(user_operation_id).strip(),
                    {"restarting", "claimed", "interrupting", "queued"},
                    "consumed",
                    consumed_by=runtime_v2_run_id,
                    consumed_at=time.time(),
                )
            run_start_timings["append_user_model"] = _timing_ms(_t_run_start)
            _pipeline_step_timing_log(
                "run_start_step_timing",
                session_id,
                "append_user_model",
                run_start_timings["append_user_model"],
                run_id=runtime_v2_run_id,
                mode="chat",
            )
            _t_run_start = time.perf_counter()
            await emit({"type": "run_started", "run_id": runtime_v2_run_id, "ephemeral": True})
            run_start_timings["emit_run_started"] = _timing_ms(_t_run_start)
            _pipeline_step_timing_log(
                "run_start_step_timing",
                session_id,
                "emit_run_started",
                run_start_timings["emit_run_started"],
                run_id=runtime_v2_run_id,
                mode="chat",
            )
            _t_run_start = time.perf_counter()
            if not atomic_user_turn:
                session_manager.append_ui_event(session_id, user_ui_event)
            run_start_timings["append_user_ui"] = _timing_ms(_t_run_start)
            _pipeline_step_timing_log(
                "run_start_step_timing",
                session_id,
                "append_user_ui",
                run_start_timings["append_user_ui"],
                run_id=runtime_v2_run_id,
                mode="chat",
                user_event_type=user_ui_type,
            )
            session_start_hook = await _dispatch_state_hook(
                "SessionStart",
                state,
                {"matcher_value": "chat", "mode": "chat"},
                emit,
            )
            if session_start_hook.additional_context:
                _append_hook_context(state, session_start_hook.additional_context, "SessionStart")
            if (
                session_start_hook.blocked
                or session_start_hook.should_pause
                or session_start_hook.requires_approval
            ):
                session_start_reason = _hook_decision_reason(
                    session_start_hook,
                    "SessionStart Hook stopped the run.",
                )
                await _workflow_callbacks().call_async("pause", state, session_start_reason, emit)
                raise RuntimeError(session_start_reason)
            await _workflow_callbacks().call_async("completion_boundary", state, emit)
            _t_run_start = time.perf_counter()
            await emit({"type": "status", "content": "New Agent Loop Start"})
            run_start_timings["emit_start_status"] = _timing_ms(_t_run_start)
            _pipeline_step_timing_log(
                "run_start_step_timing",
                session_id,
                "emit_start_status",
                run_start_timings["emit_start_status"],
                run_id=runtime_v2_run_id,
                mode="chat",
            )
            _pipeline_timing_log(
                "run_start_timing",
                session_id,
                run_start_timings,
                run_id=runtime_v2_run_id,
                user_event_type=user_ui_type,
            )
            state = await _run_react_node_off_loop(state, emit)
            state = await _apply_stop_hooks(state, emit)
            final_timings: Dict[str, int] = {}
            _t_final = time.perf_counter()
            await emit({"type": "status", "content": "Loop finished"})
            final_timings["emit_loop_finished"] = _timing_ms(_t_final)
            _pipeline_step_timing_log("final_pipeline_step_timing", session_id, "emit_loop_finished", final_timings["emit_loop_finished"], run_id=runtime_v2_run_id, mode="chat")
            stream_event_count_after_react = len(state["stream_events"])
            _t_final = time.perf_counter()
            state = validate_final(state)
            final_timings["validate_final"] = _timing_ms(_t_final)
            _pipeline_step_timing_log("final_pipeline_step_timing", session_id, "validate_final", final_timings["validate_final"], run_id=runtime_v2_run_id, mode="chat")
            _t_final = time.perf_counter()
            for evt in state["stream_events"][stream_event_count_after_react:]:
                if evt.get("type") in ("status", "validate_final", "final"):
                    await emit(evt)
            final_timings["emit_validate_events"] = _timing_ms(_t_final)
            _pipeline_step_timing_log("final_pipeline_step_timing", session_id, "emit_validate_events", final_timings["emit_validate_events"], run_id=runtime_v2_run_id, mode="chat")
            stream_event_count_after_validate = len(state["stream_events"])
            _t_final = time.perf_counter()
            state = prepare_final_event(state)
            final_timings["prepare_final_event"] = _timing_ms(_t_final)
            _pipeline_step_timing_log("final_pipeline_step_timing", session_id, "prepare_final_event", final_timings["prepare_final_event"], run_id=runtime_v2_run_id, mode="chat")
            _t_final = time.perf_counter()
            for evt in state["stream_events"][stream_event_count_after_validate:]:
                if evt.get("type") in ("status", "validate_final", "final"):
                    await emit(evt)
            final_timings["emit_final_event"] = _timing_ms(_t_final)
            _pipeline_step_timing_log("final_pipeline_step_timing", session_id, "emit_final_event", final_timings["emit_final_event"], run_id=runtime_v2_run_id, mode="chat")
            _t_final = time.perf_counter()
            await asyncio.sleep(0)
            final_timings["yield_after_final"] = _timing_ms(_t_final)
            _pipeline_step_timing_log("final_pipeline_step_timing", session_id, "yield_after_final", final_timings["yield_after_final"], run_id=runtime_v2_run_id, mode="chat")
            stream_event_count_after_final = len(state["stream_events"])
            schedule_session_title_generation(state)
            _t_final = time.perf_counter()
            state = finish(state)
            final_timings["finish_after_final"] = _timing_ms(_t_final)
            _pipeline_step_timing_log("final_pipeline_step_timing", session_id, "finish_after_final", final_timings["finish_after_final"], run_id=runtime_v2_run_id, mode="chat")
            _t_final = time.perf_counter()
            for evt in state["stream_events"][stream_event_count_after_final:]:
                if evt.get("type") in ("status", "validate_final", "final"):
                    await emit(evt)
            final_timings["emit_finish_events"] = _timing_ms(_t_final)
            _pipeline_step_timing_log("final_pipeline_step_timing", session_id, "emit_finish_events", final_timings["emit_finish_events"], run_id=runtime_v2_run_id, mode="chat")
            _pipeline_timing_log(
                "final_pipeline_timing",
                session_id,
                final_timings,
                run_id=runtime_v2_run_id,
                final_chars=len(str(state.get("final_response") or "")),
            )
            execution_metrics.record_phase(
                session_id, runtime_v2_run_id, max(1, int(state.get("_current_react_iter") or 1)),
                "final_pipeline", final_timings,
                total_ms=sum(int(v or 0) for v in final_timings.values()),
            )
            anchor_pending = getattr(
                session_manager, "anchor_pending_subagent_results_for_run", None
            )
            if callable(anchor_pending):
                anchor_pending(session_id, runtime_v2_run_id)
            await _dispatch_state_hook(
                "SessionEnd",
                state,
                {"matcher_value": "chat", "mode": "chat", "status": "finished"},
                emit,
            )
            completed = True
        except asyncio.CancelledError:
            terminal_event = {"type": "run_interrupted", "run_id": runtime_v2_run_id, "ephemeral": True}
            cancel_reason = session_manager.get_interrupt_reason(session_id) or "cancelled"
            mirror_runtime_v2("run_interrupted", {"reason": cancel_reason})
            session_manager.mark_session_unread_result(session_id, status="failed")
            raise
        except Exception as exc:
            try:
                await _dispatch_state_hook(
                    "RunFailed",
                    state,
                    {"matcher_value": "chat", "mode": "chat", "error": str(exc)},
                    emit,
                )
            except Exception:
                logger.debug("RunFailed Hook dispatch failed", exc_info=True)
            terminal_event = {"type": "run_failed", "run_id": runtime_v2_run_id, "error": str(exc), "ephemeral": True}
            mirror_runtime_v2("run_failed", {"error": str(exc)})
            session_manager.mark_session_unread_result(session_id, status="failed")
            raise
        finally:
            await power_guard.close()
            react_limit_reached = bool(state.get("react_limit_reached"))
            workflow_outcome = (
                "react_limit"
                if react_limit_reached
                else ("finished" if completed else ("failed" if terminal_event.get("type") == "run_failed" else "interrupted"))
            )
            workflow_after_run = _workflow_callbacks().call("record_run_usage",
                state,
                continuation=False,
                outcome=workflow_outcome,
                error=(
                    "ReAct reached the maximum iteration limit."
                    if react_limit_reached
                    else str(terminal_event.get("error") or "")
                ),
            )
            if workflow_after_run:
                workflow_event = _workflow_callbacks().call(
                    "state_event", workflow_after_run, "run_accounted"
                )
                if workflow_event:
                    await emit(workflow_event)
                _workflow_callbacks().call(
                    "sync_unread_result", session_id, workflow_after_run, workflow_outcome
                )
            execution_metrics.finish_run(
                session_id,
                runtime_v2_run_id,
                "react_limit" if react_limit_reached else (
                    "finished" if completed else ("failed" if terminal_event.get("type") == "run_failed" else "interrupted")
                ),
            )
            _clear_steer_run_control(session_id, steer_control)
            if completed:
                mirror_runtime_v2("run_finished", {"mode": "chat"})
                terminal_event = {"type": "run_finished", "run_id": runtime_v2_run_id, "ephemeral": True}
            await emit(terminal_event)
            await close_session_stream(session_id)
            if consumer_attached:
                await queue.put(None)

    task = asyncio.create_task(runner())
    from session_lifecycle import register_run_task

    register_run_task(session_id, task)
    cancel_requested_by_consumer = False
    try:
        while True:
            if should_stop and should_stop(session_id):
                reason = session_manager.get_interrupt_reason(session_id) or "unspecified"
                if reason == "followup":
                    mirror_runtime_v2("run_interrupted", {"reason": reason})
                    task.cancel()
                    cancel_requested_by_consumer = True
                    break
                terminal_text = _interrupt_terminal_text(session_id)
                ev1 = {"type": "status", "content": terminal_text.rstrip("。")}
                ev2 = {"type": "final", "content": terminal_text}
                mirror_runtime_v2("run_interrupted", {"reason": reason})
                session_manager.mark_session_unread_result(session_id, status="failed")
                session_manager.append_ui_event(session_id, ev1)
                session_manager.append_ui_event(session_id, ev2)
                yield ev1
                yield ev2
                task.cancel()
                cancel_requested_by_consumer = True
                break
            item = await queue.get()
            if item is None:
                break
            yield item
    finally:
        consumer_attached = False
        if task.done() or cancel_requested_by_consumer:
            try:
                await task
            except asyncio.CancelledError:
                pass
        else:
            # A browser refresh/network detach only removes this consumer.  The
            # registered run keeps publishing through the session event bus so a
            # replacement observer can attach without converting the disconnect
            # into a user interruption.
            task.add_done_callback(_discard_task_result)


async def astream_events_continuation(
    session_id: str,
    should_stop: Optional[Callable[[str], bool]] = None,
    require_pending_subagents: bool = True,
    recovery_reason: str = "",
    run_id: Optional[str] = None,
    prompt_language: Optional[str] = None,
    continuation_source: str = "subagent",
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Continue an existing Agent run without appending a user bubble.

    ``continuation_source`` keeps optional workflows, interrupted-run recovery,
    and subagent continuation distinct in the user-visible start status.
    """
    executor_http_client.interactions.clear()

    sid = (session_id or "").strip()
    if not sid:
        return
    if require_pending_subagents and not session_manager.can_continue_after_subagents(sid):
        return

    session_id = sid
    if prompt_language:
        prompt_language = normalize_prompt_language(prompt_language)
    else:
        try:
            prompt_language = normalize_prompt_language(
                session_manager.get_session_prompt_language(session_id)
            )
        except Exception:
            prompt_language = "zh-CN"
    key_context = _load_key_context_for_run(session_id)
    continuation_source = str(continuation_source or "subagent").strip().lower()
    is_workflow_continuation = continuation_source not in {"", "recovery", "subagent"}
    is_recovery_continuation = continuation_source == "recovery"
    setup_logging(
        f"[workflow-continuation:{continuation_source}]" if is_workflow_continuation else (
            "[recovery-continuation]" if is_recovery_continuation else "[subagent-continuation]"
        ),
        session_id,
    )
    pre_run_timings: Dict[str, int] = {}
    _t_pre = time.perf_counter()
    if _runtime_v2_is_primary():
        runtime_v2_llm_history_dicts = _load_runtime_v2_model_history_dicts(session_id)
        if not runtime_v2_llm_history_dicts:
            logger.warning(
                "Runtime V2 continuation skipped because model projection is empty: session=%s",
                session_id,
            )
            return
        llm_history_dicts = runtime_v2_llm_history_dicts
    else:
        session_manager.reconcile_llm_work_to_ui_user_count(session_id)
        llm_history_dicts = _load_model_history_dicts_v2_primary(session_id, reconcile_legacy=False)
    _pre_api_timing_mark(pre_run_timings, "load_model_history", _t_pre)
    _t_pre = time.perf_counter()
    work_messages_dicts = _load_work_history_dicts_for_run(session_id)
    _pre_api_timing_mark(pre_run_timings, "load_work_messages", _t_pre)

    _t_pre = time.perf_counter()
    prev_work_messages = [_dict_to_message(m) for m in work_messages_dicts]
    prev_llm_history = [_dict_to_message(m) for m in llm_history_dicts]
    _pre_api_timing_mark(pre_run_timings, "decode_histories", _t_pre)
    _t_pre = time.perf_counter()
    prev_work_messages, prev_llm_history = _sanitize_loaded_histories_for_new_run(
        session_id,
        prev_work_messages,
        prev_llm_history,
        key_context,
        "sanitize_unclosed_tool_calls_before_continuation",
    )
    _pre_api_timing_mark(pre_run_timings, "sanitize_histories", _t_pre)
    prior_work_messages_for_judge = list(prev_work_messages)

    if str(recovery_reason or "").strip():
        recovery_note = SystemMessage(content=(
            "[Runtime recovery] The previous run stopped unexpectedly and is now resuming. "
            "Continue from the persisted state. Before repeating any write, external side effect, "
            "message send, purchase, or destructive action whose completion is uncertain, first "
            "inspect or verify whether it already succeeded. Do not duplicate an uncertain side effect."
        ))
        prev_work_messages.append(recovery_note)
        prev_llm_history.append(recovery_note)

    workflow_note = _workflow_callbacks().call("continuation_message", session_id)
    if workflow_note is not None:
        prev_work_messages.append(workflow_note)
        prev_llm_history.append(workflow_note)

    user_input = ""
    for msg in reversed(prev_llm_history):
        if isinstance(msg, UserMessage):
            user_input = msg.content
            break

    runtime_v2_run_id = str(run_id or "").strip() or str(uuid.uuid4())

    state: State = {
        "dialogue": derive_dialogue_from_assistant_history(prev_llm_history),
        "work_messages": prev_work_messages,
        "llm_history": prev_llm_history,
        "user_input": user_input,
        "final_response": "",
        "stream_events": [],
        "final_printed": False,
        "session_id": session_id,
        "llm_calls": [],
        "key_context": key_context,
        "_runtime_v2_run_id": runtime_v2_run_id,
        "_tool_review_assistant_context": [],
        "_tool_review_user_followups": [],
        "_pre_run_timings": pre_run_timings,
        "_prompt_language": prompt_language,
    }
    _workflow_callbacks().call(
        "initialize_state", state, prior_work_messages_for_judge
    )
    session_manager.clear_interrupt(session_id)
    steer_control = _register_steer_run_control(session_id, runtime_v2_run_id)
    state["_steer_control"] = steer_control

    queue: asyncio.Queue = asyncio.Queue()
    consumer_attached = True
    runtime_v2_terminal_mirrored = False

    def mirror_runtime_v2_sync(event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if not _runtime_v2_is_primary():
            return
        try:
            from runtime_v2.mirror import RuntimeMirror

            mirror = RuntimeMirror(
                session_manager.sessions_dir,
                path_resolver=getattr(session_manager, "_resolve_session_path", None),
                transaction_timeout_seconds=_runtime_v2_react_transaction_timeout_seconds(),
            )
            if event_type == "run_started":
                mirror.mirror_run_started(session_id, runtime_v2_run_id, payload)
            elif event_type == "run_finished":
                mirror.mirror_run_finished(session_id, runtime_v2_run_id, payload)
            elif event_type == "run_interrupted":
                mirror.mirror_run_interrupted(session_id, runtime_v2_run_id, payload)
            elif event_type == "run_failed":
                mirror.mirror_run_failed(session_id, str((payload or {}).get("error") or "unknown error"), runtime_v2_run_id, payload)
            else:
                mirror.append(session_id, event_type, payload or {}, run_id=runtime_v2_run_id)
        except Exception as mirror_error:
            logger.debug("Runtime V2 mirror continuation run event failed: %s", mirror_error)

    def mirror_runtime_v2(event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        nonlocal runtime_v2_terminal_mirrored
        if runtime_v2_terminal_mirrored and event_type not in {"run_finished", "run_interrupted", "run_failed"}:
            logger.info(
                "suppressed post-terminal continuation runtime event: session=%s run_id=%s type=%s",
                session_id,
                runtime_v2_run_id,
                event_type,
            )
            return
        if event_type in {"run_finished", "run_interrupted", "run_failed"}:
            if runtime_v2_terminal_mirrored:
                return
            runtime_v2_terminal_mirrored = True
        mirror_runtime_v2_sync(event_type, dict(payload or {}))

    async def emit(ev: Dict[str, Any]) -> None:
        ev = dict(ev)
        ev.setdefault("run_id", runtime_v2_run_id)
        event_type = str(ev.get("type") or "")
        if runtime_v2_terminal_mirrored and event_type not in {"run_finished", "run_interrupted", "run_failed"}:
            logger.info(
                "suppressed post-terminal continuation stream event: session=%s run_id=%s type=%s",
                session_id,
                runtime_v2_run_id,
                event_type,
            )
            return
        runtime_committed = bool(ev.get("_runtime_v2_committed"))
        if ev.get("type") == "final":
            model_content = str(ev.get("content") or "")
            ui_content = await asyncio.to_thread(
                _snapshot_assistant_ui_content, session_id, model_content
            )
            ev["content"] = ui_content
            if not runtime_committed:
                runtime_committed = _runtime_v2_commit_assistant_final(
                    state, model_content, ui_content=ui_content
                )
        public_event = {k: v for k, v in ev.items() if k != "_runtime_v2_committed"}
        persist = should_persist_ui_event(ev) and not runtime_committed
        if persist and ev.get("type") != "tool_call":
            session_manager.append_ui_event(session_id, ev)
        if persist and ev.get("type") == "tool_call":
            if consumer_attached:
                await queue.put(public_event)
            await asyncio.to_thread(session_manager.append_ui_event, session_id, ev)
            await publish_session_event(session_id, public_event)
            return
        await publish_session_event(session_id, public_event)
        if consumer_attached:
            await queue.put(public_event)

    power_guard = AgentRunPowerGuard()

    async def on_runtime_resume(resume: RuntimeResume) -> None:
        state["_accumulated_suspend_seconds"] = power_guard.monitor.accumulated_suspend_seconds
        stage = str(state.get("_runtime_stage") or "unknown")
        payload = {
            "gap_seconds": round(resume.gap_seconds, 3),
            "suspended_seconds": round(resume.suspended_seconds, 3),
            "accumulated_suspend_seconds": round(power_guard.monitor.accumulated_suspend_seconds, 3),
            "previous_stage": stage,
            "cause": resume.cause,
        }
        logger.warning(
            "runtime_resumed session=%s run_id=%s suspended_seconds=%.3f previous_stage=%s",
            session_id,
            runtime_v2_run_id,
            resume.suspended_seconds,
            stage,
        )
        if resume.cause != "system_sleep":
            logger.info(
                "runtime_watchdog_delay_suppressed session=%s run_id=%s gap_seconds=%.3f mode=continuation",
                session_id,
                runtime_v2_run_id,
                resume.gap_seconds,
            )
            power_guard.monitor.mark_progress()
            return
        await asyncio.to_thread(mirror_runtime_v2, "runtime_resumed", payload)
        await emit({
            "type": "runtime_resumed",
            "content": (
                "System resumed after approximately %.0f seconds of sleep"
                if resume.cause == "system_sleep"
                else "Agent process resumed after approximately %.0f seconds"
            ) % resume.suspended_seconds,
            "ephemeral": True,
            **payload,
        })
        power_guard.monitor.mark_progress()

    async def runner():
        nonlocal state
        completed = False
        terminal_event = {"type": "run_interrupted", "ephemeral": True}
        try:
            await power_guard.start(on_runtime_resume)
            execution_metrics.start_run(
                session_id,
                runtime_v2_run_id,
                "continuation",
                str(state.get("user_input") or ""),
            )
            state["_run_start_perf"] = time.perf_counter()
            run_start_timings: Dict[str, int] = {}
            _t_run_start = time.perf_counter()
            mirror_runtime_v2("run_started", {"mode": "continuation"})
            run_start_timings["mirror_run_started"] = _timing_ms(_t_run_start)
            _pipeline_step_timing_log("run_start_step_timing", session_id, "mirror_run_started", run_start_timings["mirror_run_started"], run_id=runtime_v2_run_id, mode="continuation")
            _t_run_start = time.perf_counter()
            await emit({"type": "run_started", "ephemeral": True})
            run_start_timings["emit_run_started"] = _timing_ms(_t_run_start)
            _pipeline_step_timing_log("run_start_step_timing", session_id, "emit_run_started", run_start_timings["emit_run_started"], run_id=runtime_v2_run_id, mode="continuation")
            session_start_hook = await _dispatch_state_hook(
                "SessionStart",
                state,
                {"matcher_value": "continuation", "mode": "continuation"},
                emit,
            )
            if session_start_hook.additional_context:
                _append_hook_context(state, session_start_hook.additional_context, "SessionStart")
            if (
                session_start_hook.blocked
                or session_start_hook.should_pause
                or session_start_hook.requires_approval
            ):
                session_start_reason = _hook_decision_reason(
                    session_start_hook,
                    "SessionStart Hook stopped continuation.",
                )
                await _workflow_callbacks().call_async("pause", state, session_start_reason, emit)
                raise RuntimeError(session_start_reason)

            await _workflow_callbacks().call_async("completion_boundary", state, emit)
            if is_workflow_continuation:
                await _workflow_callbacks().call_async("before_continue", state, emit)
            _t_run_start = time.perf_counter()
            await emit({
                "type": "status",
                "content": "Workflow continuation started" if is_workflow_continuation else (
                    "任务已恢复，流程重启" if is_recovery_continuation else "Subagent Continuation Start"
                ),
            })
            run_start_timings["emit_start_status"] = _timing_ms(_t_run_start)
            _pipeline_step_timing_log("run_start_step_timing", session_id, "emit_start_status", run_start_timings["emit_start_status"], run_id=runtime_v2_run_id, mode="continuation")
            _pipeline_timing_log(
                "run_start_timing",
                session_id,
                run_start_timings,
                run_id=runtime_v2_run_id,
                mode="continuation",
            )
            state = await _run_react_node_off_loop(state, emit)
            state = await _apply_stop_hooks(state, emit)
            final_timings: Dict[str, int] = {}
            _t_final = time.perf_counter()
            await emit({"type": "status", "content": "Loop finished"})
            final_timings["emit_loop_finished"] = _timing_ms(_t_final)
            _pipeline_step_timing_log("final_pipeline_step_timing", session_id, "emit_loop_finished", final_timings["emit_loop_finished"], run_id=runtime_v2_run_id, mode="continuation")
            stream_event_count_after_react = len(state["stream_events"])
            _t_final = time.perf_counter()
            state = validate_final(state)
            final_timings["validate_final"] = _timing_ms(_t_final)
            _pipeline_step_timing_log("final_pipeline_step_timing", session_id, "validate_final", final_timings["validate_final"], run_id=runtime_v2_run_id, mode="continuation")
            _t_final = time.perf_counter()
            for evt in state["stream_events"][stream_event_count_after_react:]:
                if evt.get("type") in ("status", "validate_final", "final"):
                    await emit(evt)
            final_timings["emit_validate_events"] = _timing_ms(_t_final)
            _pipeline_step_timing_log("final_pipeline_step_timing", session_id, "emit_validate_events", final_timings["emit_validate_events"], run_id=runtime_v2_run_id, mode="continuation")
            stream_event_count_after_validate = len(state["stream_events"])
            _t_final = time.perf_counter()
            state = prepare_final_event(state)
            final_timings["prepare_final_event"] = _timing_ms(_t_final)
            _pipeline_step_timing_log("final_pipeline_step_timing", session_id, "prepare_final_event", final_timings["prepare_final_event"], run_id=runtime_v2_run_id, mode="continuation")
            _t_final = time.perf_counter()
            for evt in state["stream_events"][stream_event_count_after_validate:]:
                if evt.get("type") in ("status", "validate_final", "final"):
                    await emit(evt)
            final_timings["emit_final_event"] = _timing_ms(_t_final)
            _pipeline_step_timing_log("final_pipeline_step_timing", session_id, "emit_final_event", final_timings["emit_final_event"], run_id=runtime_v2_run_id, mode="continuation")
            _t_final = time.perf_counter()
            await asyncio.sleep(0)
            final_timings["yield_after_final"] = _timing_ms(_t_final)
            _pipeline_step_timing_log("final_pipeline_step_timing", session_id, "yield_after_final", final_timings["yield_after_final"], run_id=runtime_v2_run_id, mode="continuation")
            stream_event_count_after_final = len(state["stream_events"])
            schedule_session_title_generation(state)
            _t_final = time.perf_counter()
            state = finish(state)
            final_timings["finish_after_final"] = _timing_ms(_t_final)
            _pipeline_step_timing_log("final_pipeline_step_timing", session_id, "finish_after_final", final_timings["finish_after_final"], run_id=runtime_v2_run_id, mode="continuation")
            _t_final = time.perf_counter()
            for evt in state["stream_events"][stream_event_count_after_final:]:
                if evt.get("type") in ("status", "validate_final", "final"):
                    await emit(evt)
            final_timings["emit_finish_events"] = _timing_ms(_t_final)
            _pipeline_step_timing_log("final_pipeline_step_timing", session_id, "emit_finish_events", final_timings["emit_finish_events"], run_id=runtime_v2_run_id, mode="continuation")
            _pipeline_timing_log(
                "final_pipeline_timing",
                session_id,
                final_timings,
                run_id=runtime_v2_run_id,
                mode="continuation",
                final_chars=len(str(state.get("final_response") or "")),
            )
            execution_metrics.record_phase(
                session_id, runtime_v2_run_id, max(1, int(state.get("_current_react_iter") or 1)),
                "final_pipeline", final_timings,
                total_ms=sum(int(v or 0) for v in final_timings.values()),
            )
            anchor_pending = getattr(
                session_manager, "anchor_pending_subagent_results_for_run", None
            )
            if callable(anchor_pending):
                anchor_pending(session_id, runtime_v2_run_id)
            await _dispatch_state_hook(
                "SessionEnd",
                state,
                {
                    "matcher_value": "continuation",
                    "mode": "continuation",
                    "status": "finished",
                },
                emit,
            )
            completed = True
        except asyncio.CancelledError:
            terminal_event = {"type": "run_interrupted", "ephemeral": True}
            cancel_reason = session_manager.get_interrupt_reason(session_id) or "cancelled"
            mirror_runtime_v2("run_interrupted", {"reason": cancel_reason})
            session_manager.mark_session_unread_result(session_id, status="failed")
            raise
        except Exception as exc:
            try:
                await _dispatch_state_hook(
                    "RunFailed",
                    state,
                    {
                        "matcher_value": "continuation",
                        "mode": "continuation",
                        "error": str(exc),
                    },
                    emit,
                )
            except Exception:
                logger.debug("RunFailed Hook dispatch failed", exc_info=True)
            terminal_event = {"type": "run_failed", "error": str(exc), "ephemeral": True}
            mirror_runtime_v2("run_failed", {"error": str(exc)})
            session_manager.mark_session_unread_result(session_id, status="failed")
            raise
        finally:
            await power_guard.close()
            react_limit_reached = bool(state.get("react_limit_reached"))
            workflow_outcome = (
                "react_limit"
                if react_limit_reached
                else ("finished" if completed else ("failed" if terminal_event.get("type") == "run_failed" else "interrupted"))
            )
            workflow_after_run = _workflow_callbacks().call("record_run_usage",
                state,
                continuation=True,
                outcome=workflow_outcome,
                error=(
                    "ReAct reached the maximum iteration limit."
                    if react_limit_reached
                    else str(terminal_event.get("error") or "")
                ),
            )
            if workflow_after_run:
                workflow_event = _workflow_callbacks().call(
                    "state_event", workflow_after_run, "run_accounted"
                )
                if workflow_event:
                    await emit(workflow_event)
                _workflow_callbacks().call(
                    "sync_unread_result", session_id, workflow_after_run, workflow_outcome
                )
            execution_metrics.finish_run(
                session_id,
                runtime_v2_run_id,
                "react_limit" if react_limit_reached else (
                    "finished" if completed else ("failed" if terminal_event.get("type") == "run_failed" else "interrupted")
                ),
            )
            _clear_steer_run_control(session_id, steer_control)
            if completed:
                mirror_runtime_v2("run_finished", {"mode": "continuation"})
                terminal_event = {"type": "run_finished", "ephemeral": True}
            await emit(terminal_event)
            await close_session_stream(session_id)
            if consumer_attached:
                await queue.put(None)

    task = asyncio.create_task(runner())
    from session_lifecycle import register_run_task

    register_run_task(session_id, task)
    cancel_requested_by_consumer = False
    try:
        while True:
            if should_stop and should_stop(session_id):
                reason = session_manager.get_interrupt_reason(session_id) or "unspecified"
                if reason == "followup":
                    mirror_runtime_v2("run_interrupted", {"reason": reason})
                    task.cancel()
                    cancel_requested_by_consumer = True
                    break
                terminal_text = _interrupt_terminal_text(session_id)
                ev1 = {"type": "status", "content": terminal_text.rstrip("。")}
                ev2 = {"type": "final", "content": terminal_text}
                mirror_runtime_v2("run_interrupted", {"reason": reason})
                session_manager.mark_session_unread_result(session_id, status="failed")
                session_manager.append_ui_event(session_id, ev1)
                session_manager.append_ui_event(session_id, ev2)
                yield ev1
                yield ev2
                task.cancel()
                cancel_requested_by_consumer = True
                break
            item = await queue.get()
            if item is None:
                break
            yield item
    finally:
        consumer_attached = False
        if task.done() or cancel_requested_by_consumer:
            try:
                await task
            except asyncio.CancelledError:
                pass
        else:
            task.add_done_callback(_discard_task_result)
