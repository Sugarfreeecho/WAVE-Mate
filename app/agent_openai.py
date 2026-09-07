"""
Agent LLM 调用层（历史文件名保留为 agent_openai）。

负责消息序列化、统一重试/首 token 竞速、流式事件聚合，以及把 provider-neutral
TransportEvent 解析为 AssistantTurn。具体 OpenAI Responses、OpenAI-compatible Chat
Completions 与 Anthropic Messages 线协议集中在 llm/ 包。

主模型在思考开时由 harness 传 extra_body.thinking、reasoning_effort，并继续传 temperature；
messages_to_openai_params 按目标模型的 thinking_format 输出思考字段：
deepseek=reasoning_content、reasoning=reasoning、think_blocks=内容保留 <think>、none=剥离全部思考。
canonical 为 fallback 前端的规范序列化（保留 <think> + reasoning_content），具体格式由候选在重试时转换。
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import mimetypes
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

from openai import OpenAI
from openai.types.chat import ChatCompletion

from agent_messages import AssistantMessage, SystemMessage, ToolMessage, UserMessage
from agent_think import strip_think_blocks
from llm import TransportEvent, merge_streamed_tool_name

logger = logging.getLogger(__name__)


def compact_responses_history(
    client: Any,
    model: str,
    messages: List[Any],
    *,
    request_context: Any,
    source_estimated_tokens: int,
) -> Any:
    """Serialize model history once and invoke the active native compact adapter."""
    compact = getattr(client, "compact_history", None)
    if not callable(compact):
        raise NotImplementedError("LLM client has no native compaction")
    return compact(
        model=model,
        messages=_messages_to_params_for_client(client, messages),
        request_context=request_context,
        source_estimated_tokens=max(0, int(source_estimated_tokens or 0)),
    )

def _env_value(primary: str, legacy: str, default: str) -> str:
    primary_value = os.getenv(primary)
    if primary_value is not None and str(primary_value).strip() != "":
        return str(primary_value)
    legacy_value = os.getenv(legacy)
    if legacy_value is not None and str(legacy_value).strip() != "":
        return str(legacy_value)
    return default


OPENAI_MAX_RETRIES = 4
OPENAI_RETRY_BASE_SEC = 1.0
OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC = 30.0
OPENAI_FIRST_TOKEN_HEDGE_MAX_RETRIES = 2
OPENAI_TOTAL_REQUEST_BUDGET = 6
OPENAI_TOTAL_DEADLINE_SEC = 600.0
OPENAI_MAX_INFLIGHT_REQUESTS = 3


def refresh_request_recovery_config_from_env() -> None:
    """Reload the shared hedge/retry/fallback limits after dotenv changes."""
    global OPENAI_MAX_RETRIES, OPENAI_RETRY_BASE_SEC
    global OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC
    global OPENAI_FIRST_TOKEN_HEDGE_MAX_RETRIES
    global OPENAI_TOTAL_REQUEST_BUDGET, OPENAI_TOTAL_DEADLINE_SEC
    global OPENAI_MAX_INFLIGHT_REQUESTS

    OPENAI_MAX_RETRIES = max(
        1, int(_env_value("OPENAI_ERROR_MAX_RETRIES", "OPENAI_MAX_RETRIES", "4"))
    )
    OPENAI_RETRY_BASE_SEC = max(
        0.0, float(os.getenv("OPENAI_RETRY_BASE_SEC", "1.0"))
    )
    OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC = max(
        0.0,
        float(
            _env_value(
                "OPENAI_HEDGE_TIMEOUT_SEC",
                "OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC",
                "30",
            )
        ),
    )
    OPENAI_FIRST_TOKEN_HEDGE_MAX_RETRIES = max(
        0,
        int(
            _env_value(
                "OPENAI_HEDGE_MAX_ATTEMPTS",
                "OPENAI_FIRST_TOKEN_HEDGE_MAX_RETRIES",
                "2",
            )
        ),
    )
    OPENAI_TOTAL_REQUEST_BUDGET = max(
        1, int(os.getenv("OPENAI_TOTAL_REQUEST_BUDGET", "6"))
    )
    OPENAI_TOTAL_DEADLINE_SEC = max(
        1.0, float(os.getenv("OPENAI_TOTAL_DEADLINE_SEC", "600"))
    )
    OPENAI_MAX_INFLIGHT_REQUESTS = max(
        1, int(os.getenv("OPENAI_MAX_INFLIGHT_REQUESTS", "3"))
    )


refresh_request_recovery_config_from_env()


def _redact_runtime_log_text(value: Any) -> str:
    text = value if isinstance(value, str) else str(value)
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "LOCAL_LLM_HOST"):
        val = os.getenv(key)
        if val:
            text = text.replace(val, "***")
    text = re.sub(r"https?://[^\s,;]+", "***", text)
    text = re.sub(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*[^\s,;]+", r"\1=***", text)
    return text


def _masked_model_label(model: str) -> str:
    s = str(model or "").strip()
    return _redact_runtime_log_text(s) if s else "(empty)"


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


_MISSING = object()


def _get_attr_or_key(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)

    val = getattr(obj, key, _MISSING)
    if val is not _MISSING:
        return val

    for extra_name in ("model_extra", "__dict__"):
        extra = getattr(obj, extra_name, None)
        if isinstance(extra, dict) and key in extra:
            return extra.get(key, default)
    return default


def _get_nested_attr_or_key(obj: Any, *path: str) -> Any:
    cur = obj
    for k in path:
        cur = _get_attr_or_key(cur, k, _MISSING)
        if cur is _MISSING:
            return None
    return cur


def extract_usage_dict(usage_obj: Any) -> Dict[str, int]:
    """
    统一提取 usage 字段，兼容：
    - OpenAI 平铺字段：prompt_cache_hit_tokens / prompt_cache_miss_tokens
    - MiMo 嵌套字段：prompt_tokens_details.cached_tokens、completion_tokens_details.reasoning_tokens
    """
    prompt_tokens = _safe_int(_get_nested_attr_or_key(usage_obj, "prompt_tokens"))
    completion_tokens = _safe_int(_get_nested_attr_or_key(usage_obj, "completion_tokens"))
    total_tokens = _safe_int(_get_nested_attr_or_key(usage_obj, "total_tokens"))

    cache_hit_flat = _safe_int(_get_nested_attr_or_key(usage_obj, "prompt_cache_hit_tokens"))
    cache_miss_flat = _safe_int(_get_nested_attr_or_key(usage_obj, "prompt_cache_miss_tokens"))
    cached_tokens_nested = _safe_int(
        _get_nested_attr_or_key(usage_obj, "prompt_tokens_details", "cached_tokens")
    )
    prompt_cache_hit_tokens = cache_hit_flat if cache_hit_flat > 0 else cached_tokens_nested

    prompt_cache_miss_tokens = cache_miss_flat
    if prompt_cache_miss_tokens <= 0 and prompt_tokens > 0 and prompt_cache_hit_tokens >= 0:
        prompt_cache_miss_tokens = max(prompt_tokens - prompt_cache_hit_tokens, 0)

    reasoning_tokens = _safe_int(
        _get_nested_attr_or_key(usage_obj, "completion_tokens_details", "reasoning_tokens")
    )
    if reasoning_tokens <= 0:
        reasoning_tokens = _safe_int(
            _get_nested_attr_or_key(usage_obj, "reasoning_tokens")
        )
    accepted_prediction_tokens = _safe_int(
        _get_nested_attr_or_key(
            usage_obj, "completion_tokens_details", "accepted_prediction_tokens"
        )
    )
    rejected_prediction_tokens = _safe_int(
        _get_nested_attr_or_key(
            usage_obj, "completion_tokens_details", "rejected_prediction_tokens"
        )
    )

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "prompt_cache_hit_tokens": prompt_cache_hit_tokens,
        "prompt_cache_miss_tokens": prompt_cache_miss_tokens,
        "reasoning_tokens": reasoning_tokens,
        "accepted_prediction_tokens": accepted_prediction_tokens,
        "rejected_prediction_tokens": rejected_prediction_tokens,
    }


def _is_retriable_openai_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if "timeout" in msg or "timed out" in msg:
        return True
    if "connection" in msg or "connect" in msg:
        return True
    if "rate" in msg and "limit" in msg:
        return True
    if "503" in msg or "502" in msg or "529" in msg:
        return True
    try:
        from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

        return isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError))
    except ImportError:
        return False


# 模型兜底前的错误分诊：
#   "switch"   —— 确定性失败（4xx 参数/鉴权/内容类），同模型重试无意义，立即换模型；
#   "retry"    —— 瞬时故障（429 限流、超时、连接抖动、5xx），同模型重试吸收抖动；
#   "fallback" —— 其余未知错误，保守起见直接换模型。
# 本机断网由调用方先行检查（暂停回退），不到达本分类器；重试次数与间隔可经
# LLM_CANDIDATE_RETRY_ATTEMPTS / LLM_CANDIDATE_RETRY_BACKOFF_SEC 调整。
def _classify_candidate_failure(exc: BaseException) -> str:
    status_code = getattr(exc, "status_code", None)
    try:
        status_code = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        status_code = None
    if status_code is not None:
        if status_code == 429:
            return "retry"
        if 400 <= status_code < 500:
            # 408/425 是超时类传输语义，与请求搁浅同性质。
            if status_code in (408, 425):
                return "retry"
            return "switch"
        if status_code >= 500:
            return "retry"
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        return "switch"
    if type(exc).__name__ == "LocalNetworkUnavailableError":
        return "switch"
    if isinstance(exc, ConnectionError):
        # 连接抖动（含 SDK APIConnectionError 的底层原因链）优先同模型重试；
        # 断网已由调用方先行检查，到达此处说明机器在线、只是端点链路抖动。
        return "retry"
    msg = str(exc).lower()
    if "timeout" in msg or "timed out" in msg:
        return "retry"
    if "connection error" in msg or "connection reset" in msg or "connect failed" in msg:
        return "retry"
    if any(code in msg for code in ("429", "502", "503", "504", "529")):
        return "retry"
    try:
        from openai import (
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )

        if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)):
            return "retry"
    except ImportError:
        pass
    return "fallback"


def _candidate_retry_policy() -> tuple[int, float]:
    """(同模型最大重试次数, 固定间隔秒)；环境变量可调，重试关闭时返回 (0, 0)。"""
    attempts = max(0, int(os.getenv("LLM_CANDIDATE_RETRY_ATTEMPTS", "10")))
    backoff = max(0.0, float(os.getenv("LLM_CANDIDATE_RETRY_BACKOFF_SEC", "1.0")))
    return attempts, backoff


@dataclass
class AssistantTurn:
    """单次 provider 调用中 assistant 消息的统一结果。"""

    content: str
    tool_calls: Optional[List[Dict[str, Any]]]
    reasoning_content: Optional[str]
    reasoning_field: Optional[str] = None
    provider_data: Optional[Dict[str, Any]] = None


_DSML_SEP = r"[|｜]"
_DSML_MARKER_RE = re.compile(
    rf"<\s*{_DSML_SEP}\s*DSML\s*{_DSML_SEP}\s*(?:tool_calls|invoke|parameter)\b",
    re.IGNORECASE,
)
_DSML_TOOL_CALLS_RE = re.compile(
    rf"<\s*{_DSML_SEP}\s*DSML\s*{_DSML_SEP}\s*tool_calls\b[^>]*>"
    rf"(?P<body>[\s\S]*?)"
    rf"</\s*{_DSML_SEP}\s*DSML\s*{_DSML_SEP}\s*tool_calls\s*>",
    re.IGNORECASE,
)
_DSML_INVOKE_RE = re.compile(
    rf"<\s*{_DSML_SEP}\s*DSML\s*{_DSML_SEP}\s*invoke\b(?P<attrs>[^>]*)>"
    rf"(?P<body>[\s\S]*?)"
    rf"</\s*{_DSML_SEP}\s*DSML\s*{_DSML_SEP}\s*invoke\s*>",
    re.IGNORECASE,
)
_DSML_PARAMETER_RE = re.compile(
    rf"<\s*{_DSML_SEP}\s*DSML\s*{_DSML_SEP}\s*parameter\b(?P<attrs>[^>]*)>"
    rf"(?P<body>[\s\S]*?)"
    rf"</\s*{_DSML_SEP}\s*DSML\s*{_DSML_SEP}\s*parameter\s*>",
    re.IGNORECASE,
)
_DSML_ATTR_RE = re.compile(
    r"""(?P<name>[A-Za-z_][\w.-]*)\s*=\s*(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)')""",
    re.IGNORECASE,
)
_DSML_STREAM_PREFIXES = ("<｜DSML｜", "<|DSML|")
_NATIVE_BOUNDARY_TOKEN_RE = re.compile(
    r"<\s*[|｜]\s*(?:begin[▁_]of[▁_]sentence|end[▁_]of[▁_]sentence)\s*[|｜]\s*>",
    re.IGNORECASE,
)
_NATIVE_BOUNDARY_TOKENS = (
    "<｜begin▁of▁sentence｜>",
    "<｜end▁of▁sentence｜>",
    "<｜begin_of_sentence｜>",
    "<｜end_of_sentence｜>",
    "<|begin_of_sentence|>",
    "<|end_of_sentence|>",
)


def _attrs_dict(raw: str) -> Dict[str, str]:
    attrs: Dict[str, str] = {}
    for match in _DSML_ATTR_RE.finditer(str(raw or "")):
        value = match.group("double")
        if value is None:
            value = match.group("single") or ""
        attrs[str(match.group("name") or "").strip().lower()] = html.unescape(value)
    return attrs


def _strip_native_boundary_tokens(text: Optional[str]) -> str:
    return _NATIVE_BOUNDARY_TOKEN_RE.sub("", str(text or ""))


def _tool_schema_map(tools: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in tools or []:
        if not isinstance(item, dict):
            continue
        fn = item.get("function") if isinstance(item.get("function"), dict) else item
        name = str((fn or {}).get("name") or "").strip()
        if name:
            out[name] = fn or {}
    return out


def _matches_schema_type(value: Any, schema: Dict[str, Any]) -> bool:
    expected = schema.get("type")
    if isinstance(expected, list):
        return any(_matches_schema_type(value, {**schema, "type": item}) for item in expected)
    if not expected:
        return True
    expected = str(expected).lower()
    if expected == "null":
        ok = value is None
    elif expected == "boolean":
        ok = isinstance(value, bool)
    elif expected == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif expected == "number":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected == "string":
        ok = isinstance(value, str)
    elif expected == "array":
        ok = isinstance(value, list)
    elif expected == "object":
        ok = isinstance(value, dict)
    else:
        ok = True
    if not ok:
        return False
    enum = schema.get("enum")
    return not isinstance(enum, list) or value in enum


def _is_in_fenced_code(text: str, position: int) -> bool:
    prefix = text[: max(0, int(position))]
    return (prefix.count("```") % 2 == 1) or (prefix.count("~~~") % 2 == 1)


def _parse_dsml_invokes(
    raw: str,
    tools: Optional[List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    schema_map = _tool_schema_map(tools)
    if not schema_map:
        return [], "no tools were supplied for DSML validation"
    invoke_matches = list(_DSML_INVOKE_RE.finditer(raw))
    if not invoke_matches:
        return [], "DSML contains no complete invoke block"
    residue = _DSML_INVOKE_RE.sub("", raw)
    if residue.strip():
        return [], "unexpected text or an incomplete invoke exists inside DSML tool_calls"

    calls: List[Dict[str, Any]] = []
    for invoke in invoke_matches:
        invoke_attrs = _attrs_dict(invoke.group("attrs"))
        tool_name = str(invoke_attrs.get("name") or "").strip()
        fn_schema = schema_map.get(tool_name)
        if not tool_name or fn_schema is None:
            return [], f"unknown or missing DSML tool name: {tool_name or '(empty)'}"

        param_matches = list(_DSML_PARAMETER_RE.finditer(invoke.group("body")))
        param_residue = _DSML_PARAMETER_RE.sub("", invoke.group("body"))
        if param_residue.strip():
            return [], f"malformed parameter block for DSML tool {tool_name}"

        args: Dict[str, Any] = {}
        for param in param_matches:
            attrs = _attrs_dict(param.group("attrs"))
            param_name = str(attrs.get("name") or "").strip()
            if not param_name or param_name in args:
                return [], f"missing or duplicate DSML parameter for tool {tool_name}"
            raw_value = html.unescape(param.group("body"))
            is_string = str(attrs.get("string") or "").strip().lower() == "true"
            if is_string:
                value: Any = raw_value
            else:
                try:
                    value = json.loads(raw_value.strip())
                except (TypeError, ValueError, json.JSONDecodeError):
                    return [], f"invalid JSON value for DSML parameter {tool_name}.{param_name}"
            args[param_name] = value

        parameters = fn_schema.get("parameters")
        parameters = parameters if isinstance(parameters, dict) else {}
        properties = parameters.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        unknown = [name for name in args if properties and name not in properties]
        if unknown:
            return [], f"unknown DSML parameter for tool {tool_name}: {unknown[0]}"
        required = parameters.get("required")
        required = required if isinstance(required, list) else []
        missing = [str(name) for name in required if str(name) not in args]
        if missing:
            return [], f"missing required DSML parameter for tool {tool_name}: {missing[0]}"
        for param_name, value in args.items():
            prop_schema = properties.get(param_name)
            if isinstance(prop_schema, dict) and not _matches_schema_type(value, prop_schema):
                return [], f"wrong DSML value type for {tool_name}.{param_name}"

        digest = hashlib.sha256(invoke.group(0).encode("utf-8")).hexdigest()[:20]
        calls.append(
            {
                "name": tool_name,
                "args": args,
                "id": f"call_dsml_{digest}",
            }
        )
    return calls, None


def _recover_dsml_from_text(
    text: Optional[str],
    tools: Optional[List[Dict[str, Any]]],
) -> Tuple[str, List[Dict[str, Any]], bool]:
    raw = str(text or "")
    markers = [
        match for match in _DSML_MARKER_RE.finditer(raw)
        if not _is_in_fenced_code(raw, match.start())
    ]
    if not markers:
        return raw, [], False

    outer = [
        match for match in _DSML_TOOL_CALLS_RE.finditer(raw)
        if not _is_in_fenced_code(raw, match.start())
    ]
    candidates: List[Tuple[re.Match[str], str]] = []
    if outer:
        candidates = [(match, match.group("body")) for match in outer]
    else:
        invokes = [
            match for match in _DSML_INVOKE_RE.finditer(raw)
            if not _is_in_fenced_code(raw, match.start())
        ]
        candidates = [(match, match.group(0)) for match in invokes]

    recovered: List[Dict[str, Any]] = []
    remove_ranges: List[Tuple[int, int]] = []
    parse_failed = False
    for match, body in candidates:
        calls, error = _parse_dsml_invokes(body, tools)
        if error:
            logger.warning("丢弃无法安全恢复的 DSML 工具调用: %s", error)
            parse_failed = True
            continue
        recovered.extend(calls)
        remove_ranges.append((match.start(), match.end()))

    uncovered_marker = any(
        not any(start <= marker.start() < end for start, end in remove_ranges)
        for marker in markers
    )
    tail_start = markers[0].start()
    tail_residue_parts: List[str] = []
    tail_cursor = tail_start
    for start, end in sorted(remove_ranges):
        if end <= tail_start:
            continue
        tail_residue_parts.append(raw[tail_cursor:start])
        tail_cursor = end
    tail_residue_parts.append(raw[tail_cursor:])
    non_protocol_tail = bool("".join(tail_residue_parts).strip())
    if not recovered or parse_failed or uncovered_marker or non_protocol_tail:
        # Never expose an executable-looking, malformed protocol fragment or
        # accept its conversational prefix as a final answer. Returning an empty
        # channel lets the normal empty-result path retry the model cleanly.
        return "", [], True

    cleaned_parts: List[str] = []
    cursor = 0
    for start, end in sorted(remove_ranges):
        cleaned_parts.append(raw[cursor:start])
        cursor = end
    cleaned_parts.append(raw[cursor:])
    cleaned = "".join(cleaned_parts).strip()
    return cleaned, recovered, True


def _repair_dsml_turn(
    content: str,
    reasoning: Optional[str],
    tools: Optional[List[Dict[str, Any]]],
    existing_calls: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, Optional[str], Optional[List[Dict[str, Any]]], List[Dict[str, Any]]]:
    clean_content, content_calls, _ = _recover_dsml_from_text(content, tools)
    clean_reasoning, reasoning_calls, _ = _recover_dsml_from_text(reasoning, tools)
    merged: List[Dict[str, Any]] = list(existing_calls or [])
    if merged:
        # A standard OpenAI tool_calls array is authoritative. Raw protocol text
        # may be a duplicate provider artifact; strip it, but never combine two
        # conflicting interpretations into one executable batch.
        return clean_content, clean_reasoning.strip() or None, merged, []
    added: List[Dict[str, Any]] = []
    signatures = {
        (
            str(call.get("name") or ""),
            json.dumps(call.get("args") or {}, ensure_ascii=False, sort_keys=True, default=str),
        )
        for call in merged
    }
    for call in content_calls + reasoning_calls:
        signature = (
            str(call.get("name") or ""),
            json.dumps(call.get("args") or {}, ensure_ascii=False, sort_keys=True, default=str),
        )
        if signature in signatures:
            continue
        signatures.add(signature)
        merged.append(call)
        added.append(call)
    return clean_content, clean_reasoning.strip() or None, merged or None, added


class _DsmlStreamFilter:
    """Hold native DSML protocol text until it can be validated at turn end."""

    def __init__(self, enabled: bool):
        self.enabled = bool(enabled)
        self.pending = ""
        self.blocked = False

    def feed(self, piece: str) -> str:
        if not self.enabled:
            return piece
        if self.blocked:
            return ""
        combined = self.pending + piece
        marker_positions = [
            combined.find(prefix)
            for prefix in _DSML_STREAM_PREFIXES
            if combined.find(prefix) >= 0
        ]
        marker = _DSML_MARKER_RE.search(combined)
        if marker:
            marker_positions.append(marker.start())
        if marker_positions:
            marker_start = min(marker_positions)
            self.blocked = True
            self.pending = combined[marker_start:]
            return combined[:marker_start]
        keep = 0
        for prefix in _DSML_STREAM_PREFIXES:
            limit = min(len(prefix) - 1, len(combined))
            for size in range(limit, 0, -1):
                if combined.endswith(prefix[:size]):
                    keep = max(keep, size)
                    break
        if keep:
            self.pending = combined[-keep:]
            return combined[:-keep]
        self.pending = ""
        return combined

    def finish(self) -> str:
        if not self.enabled or not self.blocked:
            tail = self.pending
            self.pending = ""
            return tail
        return ""


class _NativeBoundaryStreamFilter:
    """Remove leaked BOS/EOS tokens, including tokens split across chunks."""

    def __init__(self):
        self.pending = ""

    def feed(self, piece: str) -> str:
        combined = self.pending + str(piece or "")
        cleaned = _NATIVE_BOUNDARY_TOKEN_RE.sub("", combined)
        keep = 0
        for token in _NATIVE_BOUNDARY_TOKENS:
            limit = min(len(token) - 1, len(cleaned))
            for size in range(limit, 0, -1):
                if cleaned.endswith(token[:size]):
                    keep = max(keep, size)
                    break
        if keep:
            self.pending = cleaned[-keep:]
            return cleaned[:-keep]
        self.pending = ""
        return cleaned

    def finish(self) -> str:
        tail = _strip_native_boundary_tokens(self.pending)
        self.pending = ""
        return tail


def normalize_content_text(content: Any) -> str:
    """将 API 返回的 content（str / dict / 多模态 list）统一成纯文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        if str(content.get("type") or "").strip().lower() in {"reasoning", "reasoning_content"}:
            return ""
        for key in ("text", "content", "message", "value", "output"):
            v = content.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, (list, dict)):
                inner = normalize_content_text(v)
                if inner:
                    return inner
        return ""
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
            elif isinstance(item, dict):
                if str(item.get("type") or "").strip().lower() in {"reasoning", "reasoning_content"}:
                    continue
                chunk = ""
                for key in (
                    "text",
                    "content",
                    "value",
                ):
                    v = item.get(key)
                    if isinstance(v, str) and v.strip():
                        chunk = v.strip()
                        break
                    if isinstance(v, (list, dict)):
                        inner = normalize_content_text(v)
                        if inner:
                            chunk = inner
                            break
                if chunk:
                    parts.append(chunk)
        return "\n".join(parts).strip()
    return str(content).strip()


def _normalize_content_text(content: Any) -> str:
    return normalize_content_text(content)


def _coerce_text_or_none(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        return text or None
    text = str(raw).strip()
    return text or None


def _extract_reasoning_from_content(value: Any) -> Tuple[Optional[str], Optional[str]]:
    if value is None or isinstance(value, str):
        return (None, None)
    if isinstance(value, dict):
        direct = _coerce_text_or_none(value.get("reasoning"))
        if direct:
            return (direct, "reasoning")
        nested = value.get("reasoning_content")
        if isinstance(nested, (dict, list)):
            return _extract_reasoning_from_content(nested)
        text = _coerce_text_or_none(nested)
        return (text, "reasoning_content") if text else (None, None)
    if isinstance(value, list):
        parts: List[str] = []
        field: Optional[str] = None
        for item in value:
            if not isinstance(item, dict):
                continue
            part_type = str(item.get("type") or "").strip().lower()
            if part_type in {"reasoning", "reasoning_content"}:
                text = (
                    _coerce_text_or_none(item.get("reasoning"))
                    or _coerce_text_or_none(item.get("reasoning_content"))
                    or _coerce_text_or_none(item.get("text"))
                    or _coerce_text_or_none(item.get("content"))
                )
                if text:
                    parts.append(text)
                    if field is None:
                        field = "reasoning_content" if part_type == "reasoning_content" else "reasoning"
                continue
            text = _coerce_text_or_none(item.get("reasoning"))
            if text:
                parts.append(text)
                if field is None:
                    field = "reasoning"
                continue
            text = _coerce_text_or_none(item.get("reasoning_content"))
            if text:
                parts.append(text)
                if field is None:
                    field = "reasoning_content"
        joined = "\n".join(parts).strip()
        return (joined or None, field if joined else None)
    return (None, None)


def _extract_reasoning_text_and_field(obj: Any) -> Tuple[Optional[str], Optional[str]]:
    text = _coerce_text_or_none(_get_nested_attr_or_key(obj, "reasoning_content"))
    if text:
        return (text, "reasoning_content")
    text = _coerce_text_or_none(_get_nested_attr_or_key(obj, "reasoning"))
    if text:
        return (text, "reasoning")
    return _extract_reasoning_from_content(_get_nested_attr_or_key(obj, "content"))


def _extract_reasoning_text(obj: Any) -> Optional[str]:
    """
    兼容不同供应商的思考字段命名：
    - reasoning_content（OpenAI/DeepSeek 常见）
    - reasoning（部分兼容端）
    """
    return _extract_reasoning_text_and_field(obj)[0]


def format_tool_calls_for_openai_api(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    将内部 tool_calls（name/args/id）转为 OpenAI Chat Completions 要求的
    tool_calls 项（含 function.name 与 JSON 字符串 arguments）。
    """
    out: List[Dict[str, Any]] = []
    for tc in tool_calls:
        name = tc.get("name", "")
        args = tc.get("args", {}) or {}
        tid = tc.get("id", "") or ""
        try:
            arg_str = json.dumps(args, ensure_ascii=False) if args else "{}"
        except TypeError:
            arg_str = "{}"
        out.append(
            {
                "id": tid,
                "type": "function",
                "function": {"name": name, "arguments": arg_str},
            }
        )
    return out


_MEDIA_TOKEN_RE = re.compile(
    r'(?P<q>["\'])(?P<qp>.+?\.(?:png|jpe?g|gif|webp|bmp|mp3|wav|ogg|flac|m4a|aac|mp4|webm|mov|avi))(?P=q)|'
    r'(?P<up>(?:[A-Za-z]:[\\/]|/|\.{1,2}[\\/])[^\s<>"\']+?\.(?:png|jpe?g|gif|webp|bmp|mp3|wav|ogg|flac|m4a|aac|mp4|webm|mov|avi))',
    re.IGNORECASE,
)
_REMOTE_IMAGE_REF_RE = re.compile(
    r'!\[[^\]]*\]\((?P<markdown>https?://[^\s)]+)\)'
    r'|(?P<bare>https?://[^\s<>"\']+?\.(?:png|jpe?g|gif|webp|bmp)(?:\?[^\s<>"\']*)?(?:#[^\s<>"\']*)?)',
    re.IGNORECASE,
)
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"}
_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi"}
_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}
_AUDIO_MIME = {
    ".mp3": "mp3",
    ".wav": "wav",
    ".ogg": "ogg",
    ".flac": "flac",
    ".m4a": "m4a",
    ".aac": "aac",
}
_VIDEO_MIME = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
}
_MAX_INLINE_MEDIA_BYTES = max(1, int(os.getenv("MULTIMODAL_INLINE_MAX_BYTES", str(10 * 1024 * 1024))))


def _expand_local_media_paths_in_text(text: str) -> Any:
    """将文本中的图片/音频/视频路径展开为多模态 content parts；无命中则返回原文本。"""
    src = str(text or "")
    matches = list(_MEDIA_TOKEN_RE.finditer(src))
    if not matches:
        return src
    parts: List[Dict[str, Any]] = []
    last = 0
    media_found = 0
    for m in matches:
        raw = m.group("qp") or m.group("up") or ""
        if m.start() > last:
            prefix = src[last:m.start()]
            if prefix:
                parts.append({"type": "text", "text": prefix})
        last = m.end()
        p = Path(raw).expanduser()
        if not p.exists() or not p.is_file():
            parts.append({"type": "text", "text": m.group(0)})
            continue
        ext = p.suffix.lower()
        try:
            size = p.stat().st_size
            if size > _MAX_INLINE_MEDIA_BYTES:
                parts.append({"type": "text", "text": f"{m.group(0)} [skipped: too large]"})
                continue
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            if ext in _IMAGE_EXTS:
                mime = _IMAGE_MIME.get(ext, "image/png")
                parts.append({"type": "text", "text": m.group(0)})
                parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
                media_found += 1
            elif ext in _AUDIO_EXTS:
                fmt = _AUDIO_MIME.get(ext, ext.lstrip("."))
                parts.append({"type": "text", "text": m.group(0)})
                parts.append({"type": "input_audio", "input_audio": {"data": b64, "format": fmt}})
                media_found += 1
            elif ext in _VIDEO_EXTS:
                # 兼容端差异较大：统一以 image_url/video data URL 透传，失败时模型仍可读文本提示。
                mime = _VIDEO_MIME.get(ext, "video/mp4")
                parts.append({"type": "text", "text": m.group(0)})
                parts.append({"type": "video_url", "video_url": {"url": f"data:{mime};base64,{b64}"}})
                media_found += 1
            else:
                parts.append({"type": "text", "text": m.group(0)})
        except Exception:
            parts.append({"type": "text", "text": m.group(0)})
    if last < len(src):
        parts.append({"type": "text", "text": src[last:]})
    if media_found <= 0:
        return src
    merged: List[Dict[str, Any]] = []
    for part in parts:
        if part.get("type") == "text" and merged and merged[-1].get("type") == "text":
            merged[-1]["text"] = str(merged[-1].get("text", "")) + str(part.get("text", ""))
        else:
            merged.append(part)
    return merged


def _expand_remote_image_urls_in_text(text: str) -> Any:
    """Expand explicit Markdown or extension-bearing HTTP image URLs."""
    src = str(text or "")
    matches = list(_REMOTE_IMAGE_REF_RE.finditer(src))
    if not matches:
        return src
    parts: List[Dict[str, Any]] = []
    last = 0
    for match in matches:
        if match.start() > last:
            parts.append({"type": "text", "text": src[last:match.start()]})
        original = match.group(0)
        url = match.group("markdown") or match.group("bare") or ""
        parts.append({"type": "text", "text": original})
        parts.append({"type": "image_url", "image_url": {"url": url}})
        last = match.end()
    if last < len(src):
        parts.append({"type": "text", "text": src[last:]})
    return parts


def _expand_media_paths_in_text(text: str) -> Any:
    """Expand local media paths and explicit remote image references."""
    remote_expanded = _expand_remote_image_urls_in_text(text)
    source_parts = (
        remote_expanded
        if isinstance(remote_expanded, list)
        else [{"type": "text", "text": str(remote_expanded)}]
    )
    expanded: List[Dict[str, Any]] = []
    for part in source_parts:
        if not isinstance(part, dict) or part.get("type") != "text":
            expanded.append(part)
            continue
        local = _expand_local_media_paths_in_text(str(part.get("text") or ""))
        if isinstance(local, list):
            expanded.extend(local)
        elif local:
            expanded.append({"type": "text", "text": str(local)})
    has_media = any(
        isinstance(part, dict)
        and part.get("type") in ("image_url", "video_url", "input_audio", "file", "input_file")
        for part in expanded
    )
    return expanded if has_media else str(text or "")


def _exception_search_text(exc: BaseException) -> str:
    """Collect provider error text without depending on one SDK exception shape."""
    values: List[Any] = [
        getattr(exc, "message", None),
        getattr(exc, "body", None),
        getattr(exc, "code", None),
        exc,
    ]
    response = getattr(exc, "response", None)
    if response is not None:
        values.extend(
            [
                getattr(response, "text", None),
                getattr(response, "reason_phrase", None),
            ]
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
    return " ".join(parts).lower()


def _is_media_input_error(exc: BaseException) -> bool:
    """Return True when a provider rejects media capability or media content parts."""
    msg = _exception_search_text(exc)
    media_kw = (
        "image input",
        "image inputs",
        "images",
        "image_url",
        "input_image",
        "vision",
        "audio input",
        "audio inputs",
        "input_audio",
        "video input",
        "video inputs",
        "video_url",
        "multimodal",
        "multi-modal",
        "图片",
        "图像",
        "音频",
        "视频",
        "多模态",
    )
    reason_kw = (
        "not supported",
        "does not support",
        "doesn't support",
        "do not support",
        "don't support",
        "unsupported",
        "not_support",
        "not available",
        "cannot process",
        "can't process",
        "cannot handle",
        "can't handle",
        "only supports text",
        "text-only",
        "text only",
        "not found",
        "no endpoint",
        "invalid content type",
        "unsupported content type",
        "unsupported_value",
        "不支持",
        "无法处理",
        "仅支持文本",
    )
    return any(keyword in msg for keyword in media_kw) and any(
        keyword in msg for keyword in reason_kw
    )


def _media_error_modalities(
    exc: BaseException,
    requested: set[str],
) -> set[str]:
    text = _exception_search_text(exc)
    detected: set[str] = set()
    if any(value in text for value in ("image", "vision", "图片", "图像")):
        detected.add("image")
    if any(value in text for value in ("audio", "input_audio", "音频")):
        detected.add("audio")
    if any(value in text for value in ("video", "video_url", "视频")):
        detected.add("video")
    if any(value in text for value in ("input_file", "file input", "文件")):
        detected.add("file")
    matched = detected & set(requested)
    return matched or set(requested)


def _is_stream_options_error(exc: BaseException) -> bool:
    """Return True only when retrying without stream_options can help."""
    msg = _exception_search_text(exc)
    stream_kw = ("stream_options", "include_usage")
    reason_kw = (
        "not supported",
        "does not support",
        "doesn't support",
        "unsupported",
        "unknown",
        "unexpected",
        "unrecognized",
        "extra fields",
        "extra inputs",
        "extra_forbidden",
        "not allowed",
        "not permitted",
        "invalid",
    )
    return any(keyword in msg for keyword in stream_kw) and any(
        keyword in msg for keyword in reason_kw
    )


def _api_messages_have_media(api_messages: List[Dict[str, Any]]) -> bool:
    for message in api_messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        if any(
            isinstance(part, dict)
            and part.get("type") in ("image_url", "video_url", "input_audio", "file", "input_file")
            for part in content
        ):
            return True
    return False


def _api_messages_required_modalities(
    api_messages: List[Dict[str, Any]],
) -> set[str]:
    required: set[str] = set()
    for message in api_messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "").strip().lower()
            if part_type in {"image_url", "input_image"}:
                required.add("image")
            elif part_type == "input_audio":
                required.add("audio")
            elif part_type == "video_url":
                required.add("video")
            elif part_type in {"input_file", "file"}:
                required.add("file")
    return required


def _strip_media_from_api_messages(api_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Replace image/audio/video content parts with placeholder text."""
    _MEDIA_PLACEHOLDER = "[该消息包含多媒体内容（图片/音频/视频），但当前模型不支持，已用此文本占位]"
    cleaned: List[Dict[str, Any]] = []
    for msg in api_messages:
        c = msg.get("content")
        if isinstance(c, list):
            has_media = any(isinstance(p, dict) and p.get("type") in ("image_url", "video_url", "input_audio", "file", "input_file") for p in c)
            text_parts = [p for p in c if isinstance(p, dict) and p.get("type") == "text"]
            media_refs: List[str] = []
            for part in c:
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type") or "").strip().lower()
                raw_ref: Any = None
                if part_type == "image_url":
                    raw_ref = part.get("image_url")
                elif part_type == "video_url":
                    raw_ref = part.get("video_url")
                if isinstance(raw_ref, dict):
                    raw_ref = raw_ref.get("url")
                ref = str(raw_ref or "").strip()
                if ref.lower().startswith(("http://", "https://")) and ref not in media_refs:
                    media_refs.append(ref)
            reference_text = (
                " [媒体原始地址: " + " ; ".join(media_refs) + "]"
                if media_refs
                else ""
            )
            if text_parts:
                combined = " ".join(str(p.get("text", "")) for p in text_parts).strip()
                if has_media:
                    combined = _MEDIA_PLACEHOLDER + reference_text + " " + combined
                cleaned.append({**msg, "content": combined})
            elif has_media:
                cleaned.append({**msg, "content": _MEDIA_PLACEHOLDER + reference_text})
            else:
                cleaned.append(msg)
        else:
            cleaned.append(msg)
    return cleaned


def _inject_multimodal_fallback_instruction(
    api_messages: List[Dict[str, Any]],
    required_modalities: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    """Inject fallback guidance without creating a trailing system turn."""
    modality_labels = {
        "image": "图片",
        "audio": "音频",
        "video": "视频",
        "file": "文件",
    }
    labels = "、".join(
        modality_labels[item]
        for item in ("image", "audio", "video", "file")
        if item in set(required_modalities or ())
    ) or "多媒体"
    instruction = (
        f"[多模态委派提示] 当前主模型不支持直接读取本次请求中的{labels}。"
        "如果回答需要理解这些内容，请调用 task 工具（action=start，run_in_background=false），"
        "从 model_profile_id 候选中选择明确支持所需输入模态的模型；"
        "将相邻用户消息中的原始图片 URL 或本地附件路径、用户问题完整写入 prompt；"
        "prompt 中的本地附件路径必须用英文双引号完整包裹，"
        "取得 subagent 的识别结果后再继续回答。不要猜测媒体内容；"
        "若没有可用的兼容模型，请明确告知用户。"
    )
    out = [dict(message) for message in api_messages]
    for index, message in enumerate(out):
        if str(message.get("role") or "").strip().lower() != "system":
            continue
        current = str(message.get("content") or "").rstrip()
        if instruction not in current:
            out[index] = {
                **message,
                "content": f"{current}\n\n{instruction}".strip(),
            }
        return out
    out.insert(0, {"role": "system", "content": instruction})
    return out


def _serialized_messages_to_text_only(
    api_messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Strip serialized media while preserving adjacent local-path text."""
    if not _api_messages_have_media(api_messages):
        return api_messages
    required_modalities = _api_messages_required_modalities(api_messages)
    return _inject_multimodal_fallback_instruction(
        _strip_media_from_api_messages(api_messages),
        required_modalities,
    )


def _is_glm_model(model: str) -> bool:
    s = str(model or "").strip().lower()
    return s.startswith("glm-")



_ANNOTATE_MEDIA_PATH_RE = re.compile(
    r'"([^"]+?\.(?:png|jpe?g|gif|webp|bmp|svg|ico|tiff?|avif|jfif|'
    r'mp3|wav|m4a|ogg|flac|aac|mp4|webm|mov|mkv))"|'
    r'([^\s"\']+?\.(?:png|jpe?g|gif|webp|bmp|svg|ico|tiff?|avif|jfif|'
    r'mp3|wav|m4a|ogg|flac|aac|mp4|webm|mov|mkv))',
    re.IGNORECASE,
)


def _media_kind_for_path(p: str) -> str:
    ext = "." + str(p or "").rsplit(".", 1)[-1].lower() if "." in str(p) else ""
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _AUDIO_EXTS:
        return "audio"
    if ext in _VIDEO_EXTS:
        return "video"
    return ""


def _annotate_local_media_paths(value: str, *, mode: str) -> str:
    """Prefix local media paths with an attachment label.

    vision mode: media is already attached as image_url/audio/video parts, so the
    label tells the model the path is only informative.
    text_only mode: the media was stripped, so the label tells the model to
    delegate inspection to a multimodal subagent via the task tool.
    """
    if mode == "vision":
        # Aligned with mainstream agent UIs (opencode/hermes): the local path
        # is replaced by a placeholder in the text part; the media itself is
        # already attached as image_url/audio/video parts.
        replacements = {
            "image": "[图片附件]",
            "audio": "[音频附件]",
            "video": "[视频附件]",
        }
    else:
        # Text-only fallback: the path must stay so the model can delegate the
        # media inspection to a multimodal subagent via the task tool.
        replacements = {
            "image": "[图片附件（如需要识图请委派给多模态子代理）]",
            "audio": "[音频附件（如需要播放请委派给多模态子代理）]",
            "video": "[视频附件（如需要播放请委派给多模态子代理）]",
        }

    def _repl(match: "re.Match[str]") -> str:
        raw = match.group(1) or match.group(2) or ""
        p = raw.strip()
        kind = _media_kind_for_path(p)
        if not kind:
            return match.group(0)
        label = replacements.get(kind, "")
        if mode == "vision":
            return label
        return label + match.group(0)

    return _ANNOTATE_MEDIA_PATH_RE.sub(_repl, str(value or ""))


def _text_only_media_part(local_path: str) -> Dict[str, str]:
    """Build the delegated text part for a stripped local media attachment."""
    kind = _media_kind_for_path(local_path)
    label = {
        "image": "[图片附件（如需要识图请委派给多模态子代理）]",
        "audio": "[音频附件（如需要播放请委派给多模态子代理）]",
        "video": "[视频附件（如需要播放请委派给多模态子代理）]",
    }.get(kind, "[附件（如需要处理请委派给多模态子代理）]")
    return {"type": "text", "text": '%s"%s"' % (label, local_path)}


def messages_to_openai_params(
    messages: List[Any],
    *,
    expand_media_paths: bool = True,
    thinking_format: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """将 UserMessage / AssistantMessage / ToolMessage / SystemMessage 转为 API messages 列表。"""
    api_msgs: List[Dict[str, Any]] = []
    tf = str(thinking_format or "deepseek").strip().lower()
    if tf not in {"deepseek", "reasoning", "think_blocks", "none", "canonical"}:
        tf = "deepseek"
    strip_think = tf not in {"think_blocks", "canonical"}
    reasoning_field: Optional[str] = (
        "reasoning_content"
        if tf in {"deepseek", "canonical"}
        else ("reasoning" if tf == "reasoning" else None)
    )
    for m in messages:
        if isinstance(m, SystemMessage):
            api_msgs.append({"role": "system", "content": m.content or ""})
        elif isinstance(m, UserMessage):
            if isinstance(m.content, list):
                content_parts: List[Dict[str, Any]] = []
                for raw_part in m.content:
                    if not isinstance(raw_part, dict):
                        content_parts.append({"type": "text", "text": str(raw_part)})
                        continue
                    part_type = str(raw_part.get("type") or "").strip().lower()
                    if part_type == "text":
                        raw_text = str(raw_part.get("text") or "")
                        if expand_media_paths:
                            annotated_text = _annotate_local_media_paths(
                                raw_text, mode="vision"
                            )
                            remote_parts = _expand_remote_image_urls_in_text(annotated_text)
                            if isinstance(remote_parts, list):
                                content_parts.extend(remote_parts)
                            else:
                                content_parts.append({"type": "text", "text": annotated_text})
                        else:
                            content_parts.append(
                                {
                                    "type": "text",
                                    "text": _annotate_local_media_paths(
                                        raw_text, mode="text_only"
                                    ),
                                }
                            )
                        continue
                    if part_type != "local_file":
                        content_parts.append(raw_part)
                        continue
                    local_file = raw_part.get("local_file")
                    local_path = str(
                        (local_file.get("path") if isinstance(local_file, dict) else local_file)
                        or raw_part.get("path")
                        or ""
                    ).strip()
                    if not local_path:
                        continue
                    if not expand_media_paths:
                        seen_text = "".join(
                            str(p.get("text") or "")
                            for p in content_parts
                            if isinstance(p, dict) and p.get("type") == "text"
                        )
                        if local_path not in seen_text:
                            content_parts.append(_text_only_media_part(local_path))
                        continue
                    expanded_local = _expand_local_media_paths_in_text(
                        json.dumps(local_path, ensure_ascii=False)
                    )
                    if isinstance(expanded_local, list):
                        content_parts.extend(
                            part
                            for part in expanded_local
                            if isinstance(part, dict) and part.get("type") != "text"
                        )
                    else:
                        path = Path(local_path).expanduser()
                        try:
                            if path.is_file() and path.stat().st_size <= _MAX_INLINE_MEDIA_BYTES:
                                mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                                content_parts.append({
                                    "type": "file",
                                    "file": {
                                        "filename": path.name,
                                        "file_data": f"data:{mime};base64,{encoded}",
                                    },
                                })
                            else:
                                content_parts.append({"type": "text", "text": local_path})
                        except OSError:
                            content_parts.append({"type": "text", "text": local_path})
                api_msgs.append({"role": "user", "content": content_parts})
            elif isinstance(m.content, str):
                content = (
                    _expand_media_paths_in_text(m.content)
                    if expand_media_paths
                    else m.content
                )
                api_msgs.append({"role": "user", "content": content})
            else:
                api_msgs.append({"role": "user", "content": str(m.content)})
        elif isinstance(m, AssistantMessage):
            raw_content = m.content or ""
            content = strip_think_blocks(raw_content) if strip_think else raw_content
            item: Dict[str, Any] = {"role": "assistant", "content": content}
            if m.tool_calls:
                item["tool_calls"] = format_tool_calls_for_openai_api(m.tool_calls)
            ak = getattr(m, "additional_kwargs", None) or {}
            rc = None
            if isinstance(ak, dict):
                rc = ak.get("reasoning_content", None)
                if rc is None:
                    rc = ak.get("reasoning", None)
                responses_state = ak.get("_myagent_responses")
                if isinstance(responses_state, dict):
                    item["_myagent_responses"] = dict(responses_state)
            if rc is not None and reasoning_field is not None:
                item[reasoning_field] = str(rc)
            api_msgs.append(item)
        elif isinstance(m, ToolMessage):
            api_msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": m.tool_call_id or "",
                    "content": m.content if isinstance(m.content, str) else str(m.content),
                }
            )
        else:
            c = getattr(m, "content", str(m))
            api_msgs.append({"role": "user", "content": str(c)})
    return api_msgs


def _messages_to_text_only_params(
    messages: List[Any],
    *,
    required_modalities: Optional[set[str]] = None,
    thinking_format: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Rebuild a failed multimodal request from the original messages.

    String user messages keep their original local paths. Already-structured
    multimodal messages have no recoverable local path, so their media parts
    use the explicit placeholder instead.
    """
    fallback_messages = _strip_media_from_api_messages(
        messages_to_openai_params(
            messages,
            expand_media_paths=False,
            thinking_format=thinking_format,
        )
    )
    return _inject_multimodal_fallback_instruction(
        fallback_messages,
        required_modalities or _messages_required_modalities(messages),
    )


def _messages_have_media_input(messages: List[Any]) -> bool:
    for message in messages:
        if not isinstance(message, UserMessage):
            continue
        content = message.content
        if isinstance(content, list):
            if any(
                isinstance(part, dict)
                and part.get("type") in (
                    "image_url", "video_url", "input_audio", "file", "input_file", "local_file"
                )
                for part in content
            ):
                return True
            continue
        if not isinstance(content, str):
            continue
        if _REMOTE_IMAGE_REF_RE.search(content):
            return True
        for match in _MEDIA_TOKEN_RE.finditer(content):
            raw = match.group("qp") or match.group("up") or ""
            path = Path(raw).expanduser()
            try:
                if (
                    path.is_file()
                    and path.suffix.lower() in (_IMAGE_EXTS | _AUDIO_EXTS | _VIDEO_EXTS)
                    and path.stat().st_size <= _MAX_INLINE_MEDIA_BYTES
                ):
                    return True
            except OSError:
                continue
    return False


def _messages_required_modalities(messages: List[Any]) -> set[str]:
    serialized = messages_to_openai_params(messages, expand_media_paths=True)
    return _api_messages_required_modalities(serialized)


def _client_input_modalities(client: Any) -> set[str]:
    raw = getattr(client, "_myagent_input_modalities", None)
    if isinstance(raw, (list, tuple, set, frozenset)):
        values = {str(item or "").strip().lower() for item in raw}
        return {item for item in values if item in {"text", "image", "audio", "video", "file"}}
    if bool(getattr(client, "_myagent_multimodal_input", False)):
        return {"text", "image", "audio", "video", "file"}
    return {"text"}


def _client_supports_modalities(client: Any, required: set[str]) -> bool:
    return set(required).issubset(_client_input_modalities(client))


def _client_multimodal_input_enabled(client: Any) -> bool:
    return bool(getattr(client, "_myagent_multimodal_input", False))


def _mark_client_multimodal_failed(client: Any, exc: BaseException) -> None:
    try:
        setattr(client, "_myagent_multimodal_input", False)
    except Exception:
        pass
    callback = getattr(client, "_myagent_mark_multimodal_failed", None)
    if callable(callback):
        try:
            callback(exc)
        except Exception:
            logger.warning("记录模型多模态能力失败", exc_info=True)


def _mark_client_modalities_failed(
    client: Any,
    modalities: set[str],
    exc: BaseException,
) -> None:
    rejected = {
        modality
        for modality in modalities
        if modality in {"image", "audio", "video", "file"}
    }
    if not rejected:
        _mark_client_multimodal_failed(client, exc)
        return
    remaining = _client_input_modalities(client) - rejected
    try:
        setattr(client, "_myagent_input_modalities", sorted(remaining | {"text"}))
        setattr(
            client,
            "_myagent_multimodal_input",
            bool(remaining & {"image", "audio", "video", "file"}),
        )
    except Exception:
        pass
    callback = getattr(client, "_myagent_mark_modalities_failed", None)
    if callable(callback):
        try:
            callback(sorted(rejected), exc)
            return
        except Exception:
            logger.warning("记录模型具体模态能力失败", exc_info=True)
    legacy_callback = getattr(client, "_myagent_mark_multimodal_failed", None)
    if callable(legacy_callback):
        try:
            legacy_callback(exc)
        except Exception:
            logger.warning("记录模型多模态能力失败", exc_info=True)


def _messages_to_params_for_client(
    client: Any,
    messages: List[Any],
    *,
    thinking_format: Optional[str] = None,
) -> List[Dict[str, Any]]:
    fmt = str(thinking_format or "").strip().lower()
    if not fmt:
        fmt = str(getattr(client, "_myagent_thinking_format", "") or "").strip().lower()
    if not fmt:
        fmt = "deepseek"
    required_modalities = _messages_required_modalities(messages)
    if required_modalities and not _client_supports_modalities(
        client, required_modalities
    ):
        return _messages_to_text_only_params(
            messages,
            required_modalities=required_modalities,
            thinking_format=fmt,
        )
    return messages_to_openai_params(
        messages,
        expand_media_paths=bool(required_modalities),
        thinking_format=fmt,
    )


def parse_assistant_message(
    msg: Any,
    tools: Optional[List[Dict[str, Any]]] = None,
) -> AssistantTurn:
    """解析 chat.completions 返回的 assistant message（content、tool_calls、reasoning_content）。"""
    content = _normalize_content_text(_get_nested_attr_or_key(msg, "content"))
    reasoning, reasoning_field = _extract_reasoning_text_and_field(msg)
    content = _strip_native_boundary_tokens(content).strip()
    reasoning = _strip_native_boundary_tokens(reasoning).strip() or None

    raw_calls = _get_nested_attr_or_key(msg, "tool_calls")
    tool_calls: Optional[List[Dict[str, Any]]] = None
    if raw_calls:
        tool_calls = []
        for tc in raw_calls:
            fn = _get_nested_attr_or_key(tc, "function")
            name = _get_nested_attr_or_key(fn, "name") if fn else ""
            raw_args = _get_nested_attr_or_key(fn, "arguments") if fn else "{}"
            tid = _get_nested_attr_or_key(tc, "id") or ""
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except json.JSONDecodeError:
                logger.warning("工具参数 JSON 解析失败，使用空对象: %s", raw_args[:200])
                args = {}
            tool_calls.append({"name": name, "args": args, "id": tid})
    if tools is not None:
        content, reasoning, tool_calls, _ = _repair_dsml_turn(
            content,
            reasoning,
            tools,
            tool_calls,
        )
    return AssistantTurn(
        content=content,
        tool_calls=tool_calls,
        reasoning_content=reasoning,
        reasoning_field=reasoning_field,
    )


class _InvalidLLMResponseError(RuntimeError):
    pass


class _LogicalRequestBudget:
    """Thread-safe budget shared by hedge, error retry, and model fallback."""

    def __init__(self) -> None:
        self.started_at = time.monotonic()
        self.deadline_at = self.started_at + OPENAI_TOTAL_DEADLINE_SEC
        self._physical_started = 0
        self._lock = threading.Lock()

    def claim(self) -> bool:
        with self._lock:
            if time.monotonic() >= self.deadline_at:
                return False
            if self._physical_started >= OPENAI_TOTAL_REQUEST_BUDGET:
                return False
            self._physical_started += 1
            return True

    @property
    def physical_started(self) -> int:
        with self._lock:
            return self._physical_started

    @property
    def exhausted(self) -> bool:
        with self._lock:
            return self._physical_started >= OPENAI_TOTAL_REQUEST_BUDGET


_RECOVERY_BUDGET_LOCAL = threading.local()


def _bind_recovery_budget(budget: Optional[_LogicalRequestBudget]) -> None:
    if budget is None:
        try:
            del _RECOVERY_BUDGET_LOCAL.current
        except AttributeError:
            pass
        return
    _RECOVERY_BUDGET_LOCAL.current = budget


def _claim_additional_recovery_request() -> bool:
    """Claim a nested physical request, such as a fallback model candidate."""
    budget = getattr(_RECOVERY_BUDGET_LOCAL, "current", None)
    return True if budget is None else bool(budget.claim())


def run_nonstream_request_with_recovery(
    call: Callable[[], Any],
    *,
    validator: Optional[Callable[[Any], bool]] = None,
    retriable_error: Optional[Callable[[BaseException], bool]] = None,
    request_name: str = "chat.completions",
    recovery_budget: Optional[_LogicalRequestBudget] = None,
) -> Any:
    """Run one non-stream request through the shared hedge/retry budget.

    A hedge is another concurrent physical request started while earlier calls
    are still unresolved. Explicit retriable failures schedule a bounded
    backoff retry. Both consume the same total request budget and deadline.
    Synchronous SDK calls cannot be force-cancelled portably, so losing worker
    threads are daemonized and their late results are discarded.
    """

    budget = recovery_budget or _LogicalRequestBudget()
    started_at = budget.started_at
    deadline_at = budget.deadline_at
    results: "Queue[Tuple[str, str, Any]]" = Queue()
    stopped = threading.Event()
    state_lock = threading.Lock()
    in_flight: set[str] = set()
    hedge_started = 0
    error_retries_started = 0
    role_counter = 0
    last_error: Optional[BaseException] = None
    retry_due_at: Optional[float] = None

    def launch(kind: str) -> bool:
        nonlocal hedge_started, error_retries_started, role_counter
        with state_lock:
            if len(in_flight) >= OPENAI_MAX_INFLIGHT_REQUESTS:
                return False
            if not budget.claim():
                return False
            role_counter += 1
            role = "primary" if role_counter == 1 else f"{kind}_{role_counter - 1}"
            if kind == "hedge":
                hedge_started += 1
            elif kind == "retry":
                error_retries_started += 1
            in_flight.add(role)

        def worker() -> None:
            _bind_recovery_budget(budget)
            try:
                response = call()
                if validator is not None and not validator(response):
                    raise _InvalidLLMResponseError(
                        f"{request_name} returned an invalid response"
                    )
                if not stopped.is_set():
                    results.put((role, "success", response))
            except BaseException as exc:
                if not stopped.is_set():
                    results.put((role, "error", exc))
            finally:
                _bind_recovery_budget(None)

        threading.Thread(
            target=worker,
            name=f"llm-nonstream-{role}",
            daemon=True,
        ).start()
        return True

    if not launch("primary"):
        raise RuntimeError("LLM request budget prevented the primary request")

    next_hedge_at = (
        started_at + OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC
        if OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC > 0
        and OPENAI_FIRST_TOKEN_HEDGE_MAX_RETRIES > 0
        else None
    )

    try:
        while True:
            now = time.monotonic()
            if now >= deadline_at:
                raise TimeoutError(
                    f"{request_name} exceeded total deadline "
                    f"{OPENAI_TOTAL_DEADLINE_SEC:.1f}s"
                )

            if (
                next_hedge_at is not None
                and now >= next_hedge_at
                and hedge_started < OPENAI_FIRST_TOKEN_HEDGE_MAX_RETRIES
            ):
                if launch("hedge"):
                    logger.warning(
                        "%s 完整响应持续等待，已发起并行 API 重试 %s/%s",
                        request_name,
                        hedge_started,
                        OPENAI_FIRST_TOKEN_HEDGE_MAX_RETRIES,
                    )
                next_hedge_at = started_at + OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC * (
                    hedge_started + 1
                )
                if hedge_started >= OPENAI_FIRST_TOKEN_HEDGE_MAX_RETRIES:
                    next_hedge_at = None

            if retry_due_at is not None and now >= retry_due_at:
                if error_retries_started < max(0, OPENAI_MAX_RETRIES - 1):
                    if launch("retry"):
                        retry_due_at = None
                    else:
                        with state_lock:
                            if budget.exhausted:
                                retry_due_at = None
                else:
                    retry_due_at = None

            with state_lock:
                active_count = len(in_flight)
                budget_exhausted = budget.exhausted
            if active_count == 0 and retry_due_at is None:
                if last_error is not None:
                    raise last_error
                if budget_exhausted:
                    raise RuntimeError(f"{request_name} exhausted its request budget")

            wake_at = deadline_at
            if next_hedge_at is not None:
                wake_at = min(wake_at, next_hedge_at)
            if retry_due_at is not None:
                wake_at = min(wake_at, retry_due_at)
            wait_for = max(0.001, min(0.05, wake_at - time.monotonic()))
            try:
                role, outcome, payload = results.get(timeout=wait_for)
            except Empty:
                continue
            with state_lock:
                in_flight.discard(role)
            if outcome == "success":
                stopped.set()
                return payload

            error = payload
            last_error = error
            retriable = (
                bool(retriable_error(error))
                if retriable_error is not None
                else (
                    isinstance(error, _InvalidLLMResponseError)
                    or _is_retriable_openai_error(error)
                )
            )
            if retriable and error_retries_started < max(0, OPENAI_MAX_RETRIES - 1):
                delay = OPENAI_RETRY_BASE_SEC * (2**error_retries_started)
                due = time.monotonic() + delay
                retry_due_at = due if retry_due_at is None else min(retry_due_at, due)
                logger.warning(
                    "%s 可重试错误，计划在 %.1fs 后重试: %s",
                    request_name,
                    delay,
                    _redact_runtime_log_text(error),
                )
            elif not in_flight:
                raise error
    finally:
        stopped.set()


def chat_completion(
    client: OpenAI,
    model: str,
    messages: List[Any],
    *,
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float,
    max_tokens: int,
    extra_body: Optional[Dict[str, Any]] = None,
    parallel_tool_calls: bool = True,
    reasoning_effort: Optional[str] = None,
    omit_temperature: bool = False,
    response_validator: Optional[Callable[[Any], bool]] = None,
    recovery_budget: Optional[_LogicalRequestBudget] = None,
    request_timeout: Optional[float] = None,
    request_context: Optional[Any] = None,
) -> ChatCompletion:
    """Return one buffered completion using first-token streaming underneath."""
    budget = recovery_budget or _LogicalRequestBudget()
    t0 = time.monotonic()
    validation_attempt = 0
    while True:
        r = _buffered_chat_completion_via_stream(
            client,
            model,
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body=extra_body,
            parallel_tool_calls=parallel_tool_calls,
            reasoning_effort=reasoning_effort,
            omit_temperature=omit_temperature,
            recovery_budget=budget,
            request_timeout=request_timeout,
            request_context=request_context,
        )
        if response_validator is None or response_validator(r):
            break
        validation_attempt += 1
        if validation_attempt >= OPENAI_MAX_RETRIES or budget.exhausted:
            raise _InvalidLLMResponseError(
                f"chat.completions[{_masked_model_label(model)}] returned an invalid response"
            )
        logger.warning(
            "chat.completions[%s] 返回无效结果，使用统一预算重试 %s/%s",
            _masked_model_label(model),
            validation_attempt,
            OPENAI_MAX_RETRIES - 1,
        )
    dt = time.monotonic() - t0
    u = getattr(r, "usage", None)
    if u:
        ud = extract_usage_dict(u)
        pt = ud.get("prompt_tokens", 0)
        ct = ud.get("completion_tokens", 0)
        prompt_cache_hit_tokens = ud.get("prompt_cache_hit_tokens", 0)
        prompt_cache_miss_tokens = ud.get("prompt_cache_miss_tokens", 0)
        cache_total = prompt_cache_hit_tokens + prompt_cache_miss_tokens
        hit_pct = prompt_cache_hit_tokens / cache_total * 100 if cache_total > 0 else None
        extra = f" hit_rate={hit_pct:.1f}%" if hit_pct is not None else ""
        logger.info(
            "chat.completions 成功 model=%s 耗时=%.2fs prompt_tokens=%s "
            "completion_tokens=%s prompt_cache_hit_tokens=%s "
            "prompt_cache_miss_tokens=%s%s",
            _masked_model_label(model),
            dt,
            pt,
            ct,
            prompt_cache_hit_tokens,
            prompt_cache_miss_tokens,
            extra,
        )
    else:
        logger.info(
            "chat.completions 成功 model=%s 耗时=%.2fs",
            _masked_model_label(model),
            dt,
        )
    return r


def _accumulate_tool_call_delta(
    tool_acc: Dict[int, Dict[str, str]],
    delta_tool_calls: Any,
) -> None:
    """合并流式 chunk 中的 tool_calls 片段（按 index）。"""
    if not delta_tool_calls:
        return
    for tc in delta_tool_calls:
        idx = getattr(tc, "index", None)
        if idx is None:
            idx = 0
        if idx not in tool_acc:
            tool_acc[idx] = {"id": "", "name": "", "arguments": ""}
        tid = getattr(tc, "id", None)
        if tid:
            tool_acc[idx]["id"] = str(tid)
        fn = getattr(tc, "function", None)
        if fn:
            name = getattr(fn, "name", None)
            if name:
                tool_acc[idx]["name"] = merge_streamed_tool_name(
                    tool_acc[idx].get("name"),
                    name,
                )
            args = getattr(fn, "arguments", None)
            if args:
                tool_acc[idx]["arguments"] += (json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args))


def _tool_call_delta_payloads(delta_tool_calls: Any) -> List[Dict[str, str]]:
    if not delta_tool_calls:
        return []
    out: List[Dict[str, str]] = []
    for tc in delta_tool_calls:
        idx = getattr(tc, "index", None)
        if idx is None:
            idx = 0
        payload: Dict[str, str] = {"index": int(idx)}
        tid = getattr(tc, "id", None)
        if tid:
            payload["id"] = str(tid)
        fn = getattr(tc, "function", None)
        if fn:
            name = getattr(fn, "name", None)
            if name:
                payload["name_delta"] = str(name)
            args = getattr(fn, "arguments", None)
            if args:
                payload["arguments_delta"] = (json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args))
        if len(payload) > 1:
            out.append(payload)
    return out


def _tool_acc_to_parsed_list(tool_acc: Dict[int, Dict[str, str]]) -> Optional[List[Dict[str, Any]]]:
    if not tool_acc:
        return None
    tool_calls: List[Dict[str, Any]] = []
    for i in sorted(tool_acc.keys()):
        row = tool_acc[i]
        name = (row.get("name") or "").strip()
        tid = row.get("id") or ""
        raw_args = row.get("arguments") or ""
        if isinstance(raw_args, str) and raw_args.strip():
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                # Unreadable streamed arguments are a transport-layer fault,
                # not a model request to validate. Keep the raw text so the
                # tool layer can answer with an identifiable transport error
                # instead of pretending the model sent an empty argument
                # object (that misdirects the model into blind retries).
                logger.warning(
                    "流式工具参数 JSON 损坏（transport 层丢参或截断）: tool=%s error=%s args[:200]=%s",
                    name or f"index{i}",
                    exc,
                    raw_args[:200],
                )
                tool_calls.append(
                    {"name": name, "args": {"_stream_corrupted_arguments": raw_args}, "id": tid, "index": i}
                )
                continue
        else:
            args = raw_args if isinstance(raw_args, dict) else {}
        if not name and not args and not tid:
            continue
        tool_calls.append({"name": name, "args": args, "id": tid, "index": i})
    return tool_calls or None


def _stream_chunk_has_first_token(chunk: Any) -> bool:
    """Whether a chunk contains the first useful model delta."""
    if isinstance(chunk, TransportEvent):
        return chunk.is_first_token
    choices = getattr(chunk, "choices", None) or []
    for choice in choices:
        delta = getattr(choice, "delta", None)
        if not delta:
            continue
        reasoning, _ = _extract_reasoning_text_and_field(delta)
        if reasoning:
            return True
        if getattr(delta, "content", None):
            return True
        if _tool_call_delta_payloads(getattr(delta, "tool_calls", None)):
            return True
    return False


def run_chat_completion_stream_worker(
    sync_q: "Queue[Optional[Tuple[str, Any]]]",
    client: OpenAI,
    model: str,
    messages: List[Any],
    *,
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float,
    max_tokens: int,
    extra_body: Optional[Dict[str, Any]] = None,
    parallel_tool_calls: bool = True,
    reasoning_effort: Optional[str] = None,
    omit_temperature: bool = False,
    should_abort: Optional[Callable[[], bool]] = None,
    transport_observer: Optional[Any] = None,
    emit_deltas: bool = True,
    recovery_budget: Optional[_LogicalRequestBudget] = None,
    request_timeout: Optional[float] = None,
    request_context: Optional[Any] = None,
) -> None:
    """
    在后台线程中跑 chat.completions(stream=True)。
    经 sync_q 投递：("reasoning", str)、("content", str)、("turn", AssistantTurn)；
    失败时 ("err", Exception)；最后一定放入 None。
    """
    api_t0 = time.perf_counter()

    def put_stream_timing(step: str, **extra: Any) -> None:
        try:
            payload: Dict[str, Any] = {
                "step": step,
                "ms_since_api_start": int(max(0.0, (time.perf_counter() - api_t0) * 1000.0)),
                "model": model,
            }
            payload.update(extra)
            sync_q.put(("stream_timing", payload))
        except Exception:
            pass

    try:
        api_messages = _messages_to_params_for_client(client, messages)
        kwargs: Dict[str, Any] = dict(
            model=model,
            messages=api_messages,
            max_tokens=max_tokens,
            parallel_tool_calls=parallel_tool_calls,
            stream=True,
        )
        if request_timeout is not None:
            kwargs["timeout"] = float(request_timeout)
        if request_context is not None and bool(
            getattr(client, "_myagent_transport_enabled", False)
        ):
            kwargs["request_context"] = request_context
        if not omit_temperature:
            kwargs["temperature"] = temperature
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if extra_body and not _is_glm_model(model):
            kwargs["extra_body"] = extra_body
        if reasoning_effort and not _is_glm_model(model):
            kwargs["reasoning_effort"] = reasoning_effort
        # include_usage 使末包返回 usage；部分兼容端会忽略或报错
        stream = None
        stream_iter = None
        prefetched_stream_chunks: List[Any] = []

        def abort_requested() -> bool:
            if should_abort is None:
                return False
            try:
                return bool(should_abort())
            except Exception:
                return False

        def close_stream_quietly(target: Any = _MISSING) -> None:
            target_stream = stream if target is _MISSING else target
            close_fn = getattr(target_stream, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except Exception:
                    pass

        media_fallback_done = False
        if transport_observer is not None:
            try:
                transport_observer.start_transport_trace()
            except Exception:
                pass
        put_stream_timing("request_serialized", messages=len(api_messages), tools=len(tools or []))
        put_stream_timing(
            "request_start",
            messages=len(api_messages),
            tools=len(tools or []),
            max_tokens=max_tokens,
        )
        attempt = 0
        hedges_used = 0
        # The executor facade owns provider/model fallback inside each logical
        # request, but the outer first-token hedge still protects the *whole*
        # logical request against a stall (no first token, no error) that
        # candidate fallback can never observe because nothing raised.  Both
        # mechanisms share the same logical budget (_LogicalRequestBudget), so
        # hedging the entire candidate loop cannot multiply physical requests
        # beyond OPENAI_TOTAL_REQUEST_BUDGET / OPENAI_MAX_INFLIGHT_REQUESTS.
        # Set OPENAI_HEDGE_MAX_ATTEMPTS=0 to disable hedge for a logical owner.
        first_token_hedge_limit = (
            0
            if OPENAI_FIRST_TOKEN_HEDGE_MAX_RETRIES <= 0
            else OPENAI_FIRST_TOKEN_HEDGE_MAX_RETRIES
        )
        request_budget = recovery_budget or _LogicalRequestBudget()
        logical_deadline_at = request_budget.deadline_at
        while attempt < OPENAI_MAX_RETRIES:
            if abort_requested():
                put_stream_timing("aborted_before_create", attempt=attempt + 1)
                return
            active_streams: Dict[str, Any] = {}
            active_streams_lock = threading.Lock()
            in_flight_roles: set[str] = set()
            race_cancelled = threading.Event()
            race_results: "Queue[Tuple[str, str, Any]]" = Queue()

            def open_until_first_token(
                request_role: str,
                results: "Queue[Tuple[str, str, Any]]" = race_results,
                cancelled: threading.Event = race_cancelled,
                active: Dict[str, Any] = active_streams,
                active_lock: threading.Lock = active_streams_lock,
                request_kwargs: Dict[str, Any] = kwargs.copy(),
                attempt_number: int = attempt + 1,
            ) -> None:
                _bind_recovery_budget(request_budget)
                local_stream = None
                local_iter = None
                chunks: List[Any] = []
                try:
                    stream_completion = getattr(client, "stream_completion", None)
                    if callable(stream_completion) and bool(
                        getattr(client, "_myagent_transport_enabled", False)
                    ):
                        request_kwargs.pop("stream", None)
                        local_stream = stream_completion(**request_kwargs)
                    else:
                        try:
                            local_stream = client.chat.completions.create(
                                **request_kwargs, stream_options={"include_usage": True}
                            )
                        except Exception as stream_options_exc:
                            if _is_media_input_error(stream_options_exc) or not _is_stream_options_error(
                                stream_options_exc
                            ):
                                raise
                            logger.debug(
                                "流式 create 无 stream_options 或端点不支持: %s",
                                _redact_runtime_log_text(stream_options_exc),
                            )
                            if not _claim_additional_recovery_request():
                                raise RuntimeError(
                                    "LLM request budget exhausted before stream-options fallback"
                                )
                            local_stream = client.chat.completions.create(**request_kwargs)
                    with active_lock:
                        active[request_role] = local_stream
                    put_stream_timing(
                        "stream_created",
                        attempt=attempt_number,
                        request_role=request_role,
                    )
                    if cancelled.is_set() or abort_requested():
                        close_stream_quietly(local_stream)
                        results.put((request_role, "aborted", None))
                        return
                    try:
                        local_iter = iter(local_stream)
                    except TypeError:
                        # Compatibility endpoints and lightweight test clients
                        # may ignore stream=True and return a complete response.
                        results.put((request_role, "complete", local_stream))
                        return
                    for chunk in local_iter:
                        chunks.append(chunk)
                        if _stream_chunk_has_first_token(chunk):
                            results.put(
                                (
                                    request_role,
                                    "token",
                                    (local_stream, local_iter, chunks),
                                )
                            )
                            return
                        if cancelled.is_set() or abort_requested():
                            close_stream_quietly(local_stream)
                            results.put((request_role, "aborted", None))
                            return
                    results.put(
                        (
                            request_role,
                            "exhausted",
                            (local_stream, local_iter, chunks),
                        )
                    )
                except Exception as exc:
                    close_stream_quietly(local_stream)
                    results.put((request_role, "error", exc))

            def start_first_token_request(request_role: str) -> bool:
                with active_streams_lock:
                    if len(in_flight_roles) >= OPENAI_MAX_INFLIGHT_REQUESTS:
                        return False
                    if not request_budget.claim():
                        return False
                    in_flight_roles.add(request_role)
                threading.Thread(
                    target=open_until_first_token,
                    args=(request_role,),
                    name=f"llm-first-token-{request_role}",
                    daemon=True,
                ).start()
                return True

            try:
                put_stream_timing("create_attempt_start", attempt=attempt + 1)
                if not start_first_token_request("primary"):
                    raise RuntimeError("LLM request budget exhausted before retry")
                race_started_at = time.monotonic()
                attempt_hedges_started = 0
                outcomes: Dict[str, Tuple[str, Any]] = {}
                winner: Optional[Tuple[str, str, Any]] = None
                while winner is None:
                    if time.monotonic() >= logical_deadline_at:
                        raise TimeoutError(
                            "streaming chat.completions exceeded total deadline "
                            f"{OPENAI_TOTAL_DEADLINE_SEC:.1f}s"
                        )
                    if abort_requested():
                        race_cancelled.set()
                        with active_streams_lock:
                            streams_to_close = list(active_streams.values())
                        for pending_stream in streams_to_close:
                            close_stream_quietly(pending_stream)
                        put_stream_timing("aborted_waiting_first_token", attempt=attempt + 1)
                        return
                    elapsed = time.monotonic() - race_started_at
                    try:
                        result = race_results.get_nowait()
                    except Empty:
                        if (
                            hedges_used < first_token_hedge_limit
                            and OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC > 0
                            and elapsed
                            >= OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC
                            * (attempt_hedges_started + 1)
                        ):
                            next_hedge_index = hedges_used + 1
                            hedge_role = f"hedge_{next_hedge_index}"
                            if start_first_token_request(hedge_role):
                                attempt_hedges_started += 1
                                hedges_used += 1
                                put_stream_timing(
                                    "first_token_hedge_started",
                                    attempt=attempt + 1,
                                    timeout_ms=int(OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC * 1000),
                                    hedge_index=hedges_used,
                                    hedge_max=first_token_hedge_limit,
                                    request_role=hedge_role,
                                )
                                logger.warning(
                                    "模型 %s 首 token 持续等待，已发起并行 API 重试 %s/%s",
                                    _masked_model_label(model),
                                    hedges_used,
                                    first_token_hedge_limit,
                                )
                        wait_timeout = 0.05
                        if (
                            hedges_used < first_token_hedge_limit
                            and OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC > 0
                        ):
                            next_hedge_at = (
                                OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC
                                * (attempt_hedges_started + 1)
                            )
                            wait_timeout = min(
                                wait_timeout,
                                max(0.001, next_hedge_at - elapsed),
                            )
                        try:
                            result = race_results.get(timeout=wait_timeout)
                        except Empty:
                            continue
                    role, outcome, payload = result
                    with active_streams_lock:
                        in_flight_roles.discard(role)
                    outcomes[role] = (outcome, payload)
                    if outcome in {"token", "complete"}:
                        winner = result
                        break
                    if attempt_hedges_started == 0:
                        if outcome == "error":
                            raise payload
                        if outcome == "exhausted":
                            winner = result
                            break
                        continue
                    if len(outcomes) < 1 + attempt_hedges_started:
                        continue
                    exhausted = next(
                        (
                            (known_role, known_outcome, known_payload)
                            for known_role, (known_outcome, known_payload) in outcomes.items()
                            if known_outcome == "exhausted"
                        ),
                        None,
                    )
                    if exhausted is not None:
                        winner = exhausted
                        break
                    error = next(
                        (
                            known_payload
                            for known_outcome, known_payload in outcomes.values()
                            if known_outcome == "error"
                        ),
                        RuntimeError("并行流式请求均未返回首 token"),
                    )
                    raise error

                assert winner is not None
                winner_role, winner_outcome, winner_payload = winner
                if winner_outcome not in {"token", "exhausted", "complete"}:
                    raise RuntimeError("流式请求未产生可用结果")
                complete_response = winner_payload if winner_outcome == "complete" else None
                if winner_outcome != "complete":
                    stream, stream_iter, prefetched_stream_chunks = winner_payload
                race_cancelled.set()
                with active_streams_lock:
                    losing_streams = [
                        candidate_stream
                        for role, candidate_stream in active_streams.items()
                        if role != winner_role
                    ]
                for losing_stream in losing_streams:
                    close_stream_quietly(losing_stream)
                if attempt_hedges_started:
                    put_stream_timing(
                        "first_token_hedge_winner",
                        attempt=attempt + 1,
                        winner=winner_role,
                        hedges_started=attempt_hedges_started,
                    )
                break
            except Exception as e:
                race_cancelled.set()
                with active_streams_lock:
                    streams_to_close = list(active_streams.values())
                for pending_stream in streams_to_close:
                    close_stream_quietly(pending_stream)
                close_stream_quietly()
                if _is_media_input_error(e) and not media_fallback_done:
                    media_fallback_done = True
                    requested_modalities = _api_messages_required_modalities(
                        list(kwargs.get("messages") or [])
                    )
                    _mark_client_modalities_failed(
                        client,
                        _media_error_modalities(e, requested_modalities),
                        e,
                    )
                    logger.warning(
                        "流式: 模型 %s 不支持多媒体输入，去掉图片/音频/视频后重试: %s",
                        _masked_model_label(model),
                        _redact_runtime_log_text(e),
                    )
                    kwargs["messages"] = _messages_to_text_only_params(messages)
                    sync_q.put(("status", "[提示] 当前模型不支持多媒体输入，已保留文件路径并切换为纯文本模式"))
                    put_stream_timing("media_fallback_retry", attempt=attempt + 1)
                    continue
                if not _is_retriable_openai_error(e) or attempt >= OPENAI_MAX_RETRIES - 1:
                    put_stream_timing("create_failed", attempt=attempt + 1, error=type(e).__name__)
                    raise
                delay = OPENAI_RETRY_BASE_SEC * (2**attempt)
                logger.warning(
                    "流式 chat.completions 重试 %s/%s 等待 %.1fs: %s",
                    attempt + 1,
                    OPENAI_MAX_RETRIES,
                    delay,
                    _redact_runtime_log_text(e),
                )
                put_stream_timing("retry_sleep", attempt=attempt + 1, delay_ms=int(delay * 1000), error=type(e).__name__)
                time.sleep(delay)
                attempt += 1
        if complete_response is not None:
            sync_q.put(("complete_response", complete_response))
            return
        if stream is None:
            raise RuntimeError("stream 创建失败")
        reasoning_parts: List[str] = []
        reasoning_field: Optional[str] = None
        content_parts: List[str] = []
        tool_acc: Dict[int, Dict[str, str]] = {}
        content_boundary_filter = _NativeBoundaryStreamFilter()
        reasoning_boundary_filter = _NativeBoundaryStreamFilter()
        content_dsml_filter = _DsmlStreamFilter(enabled=bool(tools))
        reasoning_dsml_filter = _DsmlStreamFilter(enabled=bool(tools))
        last_usage: Optional[Dict[str, int]] = None
        provider_data: Optional[Dict[str, Any]] = None
        actual_model = ""
        finish_meta: Dict[str, Any] = {"finish_reason": None, "stop_reason": None}
        first_chunk_seen = False
        first_delta_seen = False
        first_reasoning_seen = False
        first_content_seen = False
        first_tool_delta_seen = False
        transport_breakdown_emitted = False
        first_delta_at_ms: Optional[int] = None
        last_delta_at_ms: Optional[int] = None
        usage_chunk_at_ms: Optional[int] = None
        response_payload_bytes_estimated = 0
        chunk_count = 0

        def api_elapsed_ms() -> int:
            return int(max(0.0, (time.perf_counter() - api_t0) * 1000.0))

        def emit_transport_breakdown_once() -> None:
            nonlocal transport_breakdown_emitted
            if transport_breakdown_emitted or transport_observer is None:
                return
            transport_breakdown_emitted = True
            try:
                snapshot = transport_observer.snapshot_transport_trace()
                events = list(snapshot.get("events") or [])
                metrics = dict(snapshot.get("metrics") or {})
                starts: Dict[str, int] = {}
                phases: Dict[str, int] = {}
                for row in events:
                    event_name = str(row.get("event") or "")
                    at_ms = int(row.get("at_ms") or 0)
                    if event_name.endswith(".started"):
                        starts[event_name[:-8]] = at_ms
                    elif event_name.endswith(".complete"):
                        base = event_name[:-9]
                        if base in starts:
                            key = re.sub(r"[^a-zA-Z0-9]+", "_", base).strip("_") + "_ms"
                            phases[key] = max(0, at_ms - starts[base])
                put_stream_timing(
                    "transport_breakdown",
                    trace_elapsed_ms=int(snapshot.get("elapsed_ms") or 0),
                    request_bytes=int(metrics.get("request_bytes") or 0),
                    response_content_length=int(metrics.get("response_content_length") or 0),
                    **phases,
                )
            except Exception:
                pass
        def iter_stream_chunks():
            yield from prefetched_stream_chunks
            if stream_iter is not None:
                yield from stream_iter

        for chunk in iter_stream_chunks():
            if abort_requested():
                close_stream_quietly()
                put_stream_timing("aborted_during_stream")
                return
            chunk_count += 1
            if isinstance(chunk, TransportEvent):
                if chunk.kind == "provider_state":
                    if isinstance(chunk.provider_data, dict):
                        provider_data = dict(chunk.provider_data)
                    continue
                delta = SimpleNamespace(content=None, tool_calls=None)
                choices: List[Any] = []
                if chunk.kind == "reasoning_delta":
                    setattr(delta, "reasoning_content", chunk.text)
                    choices = [SimpleNamespace(delta=delta, finish_reason=None, stop_reason=None)]
                elif chunk.kind == "content_delta":
                    delta.content = chunk.text
                    choices = [SimpleNamespace(delta=delta, finish_reason=None, stop_reason=None)]
                elif chunk.kind == "tool_call_delta":
                    delta.tool_calls = [
                        SimpleNamespace(
                            index=chunk.index,
                            id=chunk.tool_call_id or None,
                            function=SimpleNamespace(
                                name=chunk.tool_name or None,
                                arguments=chunk.arguments_delta or None,
                            ),
                        )
                    ]
                    choices = [SimpleNamespace(delta=delta, finish_reason=None, stop_reason=None)]
                elif chunk.kind == "finish":
                    choices = [
                        SimpleNamespace(
                            delta=None,
                            finish_reason=chunk.finish_reason,
                            stop_reason=chunk.stop_reason,
                        )
                    ]
                chunk = SimpleNamespace(
                    model=chunk.model or None,
                    usage=chunk.usage if chunk.kind == "usage" else None,
                    choices=choices,
                )
            # This is intentionally an estimate: serializing the complete SDK
            # object for every token chunk is far more expensive than the
            # dashboard metric is worth.  Account for lightweight SSE/JSON
            # framing here and add the actual delta payload bytes below.
            response_payload_bytes_estimated += 48
            if not first_chunk_seen:
                first_chunk_seen = True
                put_stream_timing("first_chunk", chunk_count=chunk_count)
            chunk_model = str(getattr(chunk, "model", None) or "").strip()
            if chunk_model:
                actual_model = chunk_model
                response_payload_bytes_estimated += len(chunk_model.encode("utf-8"))
            uo = getattr(chunk, "usage", None)
            if uo is not None:
                last_usage = extract_usage_dict(uo)
                response_payload_bytes_estimated += 16 + sum(
                    len(str(key)) + len(str(value))
                    for key, value in (last_usage or {}).items()
                )
                usage_chunk_at_ms = api_elapsed_ms()
                put_stream_timing("usage_chunk", chunk_count=chunk_count)
            if not chunk.choices:
                continue
            choice0 = chunk.choices[0]
            fr = getattr(choice0, "finish_reason", None)
            sr = getattr(choice0, "stop_reason", None)
            if fr is not None:
                finish_meta["finish_reason"] = fr
            if sr is not None:
                finish_meta["stop_reason"] = sr
            delta = choice0.delta
            if not delta:
                continue
            rc, rc_field = _extract_reasoning_text_and_field(delta)
            if rc:
                piece = rc if isinstance(rc, str) else str(rc)
                last_delta_at_ms = api_elapsed_ms()
                reasoning_parts.append(piece)
                response_payload_bytes_estimated += len(piece.encode("utf-8"))
                visible_piece = reasoning_dsml_filter.feed(
                    reasoning_boundary_filter.feed(piece)
                )
                if reasoning_field is None and rc_field:
                    reasoning_field = rc_field
                if visible_piece and not first_delta_seen:
                    first_delta_seen = True
                    first_delta_at_ms = last_delta_at_ms
                    put_stream_timing("first_delta", delta_type="reasoning", chars=len(visible_piece), chunk_count=chunk_count)
                    emit_transport_breakdown_once()
                if visible_piece and not first_reasoning_seen:
                    first_reasoning_seen = True
                    put_stream_timing("first_reasoning_delta", chars=len(visible_piece), chunk_count=chunk_count)
                if visible_piece and emit_deltas:
                    sync_q.put(("reasoning", visible_piece))
            ct = getattr(delta, "content", None)
            if ct:
                piece = ct if isinstance(ct, str) else str(ct)
                last_delta_at_ms = api_elapsed_ms()
                content_parts.append(piece)
                response_payload_bytes_estimated += len(piece.encode("utf-8"))
                visible_piece = content_dsml_filter.feed(
                    content_boundary_filter.feed(piece)
                )
                if visible_piece and not first_delta_seen:
                    first_delta_seen = True
                    first_delta_at_ms = last_delta_at_ms
                    put_stream_timing("first_delta", delta_type="content", chars=len(visible_piece), chunk_count=chunk_count)
                    emit_transport_breakdown_once()
                if visible_piece and not first_content_seen:
                    first_content_seen = True
                    put_stream_timing("first_content_delta", chars=len(visible_piece), chunk_count=chunk_count)
                if visible_piece and emit_deltas:
                    sync_q.put(("content", visible_piece))
            delta_tool_calls = getattr(delta, "tool_calls", None)
            tool_delta_payloads = _tool_call_delta_payloads(delta_tool_calls)
            for payload in tool_delta_payloads:
                last_delta_at_ms = api_elapsed_ms()
                response_payload_bytes_estimated += 24 + sum(
                    len(str(value).encode("utf-8"))
                    for value in payload.values()
                    if value is not None
                )
                if not first_delta_seen:
                    first_delta_seen = True
                    first_delta_at_ms = last_delta_at_ms
                    put_stream_timing("first_delta", delta_type="tool_call", chunk_count=chunk_count)
                    emit_transport_breakdown_once()
                if not first_tool_delta_seen:
                    first_tool_delta_seen = True
                    put_stream_timing("first_tool_call_delta", chunk_count=chunk_count)
                if emit_deltas:
                    sync_q.put(("tool_call_delta", payload))
            _accumulate_tool_call_delta(tool_acc, delta_tool_calls)
        put_stream_timing("stream_exhausted", chunk_count=chunk_count)
        if transport_observer is not None:
            try:
                final_transport = transport_observer.snapshot_transport_trace()
                transport_metrics = dict(final_transport.get("metrics") or {})
                put_stream_timing(
                    "transport_final",
                    trace_elapsed_ms=int(final_transport.get("elapsed_ms") or 0),
                    request_bytes=int(transport_metrics.get("request_bytes") or 0),
                    response_content_length=int(transport_metrics.get("response_content_length") or 0),
                    response_payload_bytes_estimated=int(response_payload_bytes_estimated),
                )
            except Exception:
                pass
        if abort_requested():
            close_stream_quietly()
            put_stream_timing("aborted_after_stream")
            return
        reasoning_tail = reasoning_dsml_filter.feed(reasoning_boundary_filter.finish())
        reasoning_tail += reasoning_dsml_filter.finish()
        if reasoning_tail and emit_deltas:
            sync_q.put(("reasoning", reasoning_tail))
        content_tail = content_dsml_filter.feed(content_boundary_filter.finish())
        content_tail += content_dsml_filter.finish()
        if content_tail and emit_deltas:
            sync_q.put(("content", content_tail))
        tool_calls_list = _tool_acc_to_parsed_list(tool_acc)
        content_final, reasoning_final, tool_calls_list, recovered_dsml_calls = _repair_dsml_turn(
            _strip_native_boundary_tokens("".join(content_parts)),
            _strip_native_boundary_tokens("".join(reasoning_parts)).strip() or None,
            tools,
            tool_calls_list,
        )
        if recovered_dsml_calls:
            start_index = len(tool_acc)
            for offset, call in enumerate(recovered_dsml_calls):
                if emit_deltas:
                    sync_q.put(
                        (
                            "tool_call_delta",
                            {
                                "index": start_index + offset,
                                "id": str(call.get("id") or ""),
                                "name_delta": str(call.get("name") or ""),
                                "arguments_delta": json.dumps(
                                    call.get("args") or {},
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                            },
                        )
                    )
            finish_meta["finish_reason"] = "tool_calls"
            logger.warning(
                "已从模型原始 DSML 文本安全恢复 %s 个工具调用 model=%s",
                len(recovered_dsml_calls),
                _masked_model_label(actual_model or model),
            )
        turn = AssistantTurn(
            content=content_final or "",
            tool_calls=tool_calls_list,
            reasoning_content=reasoning_final,
            reasoning_field=reasoning_field,
            provider_data=provider_data,
        )
        if last_usage:
            usage_payload: Dict[str, Any] = dict(last_usage)
            usage_end_ms = int(usage_chunk_at_ms if usage_chunk_at_ms is not None else api_elapsed_ms())
            first_ms = int(first_delta_at_ms or 0)
            last_ms = int(last_delta_at_ms if last_delta_at_ms is not None else first_ms)
            usage_payload["_timing"] = {
                "first_token_wait_ms": max(0, first_ms),
                "token_generation_ms": max(0, last_ms - first_ms),
                "usage_return_ms": max(0, usage_end_ms - last_ms),
                "measured_total_ms": max(0, usage_end_ms),
            }
            if actual_model:
                usage_payload["model"] = actual_model
            sync_q.put(("usage", usage_payload))
            logger.info(
                "chat.completions stream usage model=%s prompt_tokens=%s completion_tokens=%s "
                "prompt_cache_hit_tokens=%s prompt_cache_miss_tokens=%s",
                _masked_model_label(actual_model or model),
                last_usage.get("prompt_tokens", 0),
                last_usage.get("completion_tokens", 0),
                last_usage.get("prompt_cache_hit_tokens", 0),
                last_usage.get("prompt_cache_miss_tokens", 0),
            )
        if actual_model:
            finish_meta["model"] = actual_model
        sync_q.put(("finish", finish_meta))
        sync_q.put(("turn", turn))
        put_stream_timing(
            "turn_ready",
            chunk_count=chunk_count,
            reasoning_chars=len(reasoning_final or ""),
            content_chars=len(content_final or ""),
            tool_calls=len(tool_calls_list or []),
        )
    except Exception as e:
        put_stream_timing("stream_error", error=type(e).__name__)
        logger.warning("chat.completions 流式调用异常: %s", _redact_runtime_log_text(e))
        sync_q.put(("err", e))
    finally:
        if transport_observer is not None:
            try:
                transport_observer.finish_transport_trace()
            except Exception:
                pass
        sync_q.put(None)


def _buffered_chat_completion_via_stream(
    client: OpenAI,
    model: str,
    messages: List[Any],
    *,
    tools: Optional[List[Dict[str, Any]]],
    temperature: float,
    max_tokens: int,
    extra_body: Optional[Dict[str, Any]],
    parallel_tool_calls: bool,
    reasoning_effort: Optional[str],
    omit_temperature: bool,
    recovery_budget: _LogicalRequestBudget,
    request_timeout: Optional[float],
    request_context: Optional[Any],
) -> ChatCompletion:
    """Use a streaming transport but expose one buffered completion upstream.

    The transport-level race is decided by the first useful reasoning/content/
    tool-call delta. Only the winning stream is then consumed to completion.
    """
    sync_q: "Queue[Optional[Tuple[str, Any]]]" = Queue()
    run_chat_completion_stream_worker(
        sync_q,
        client,
        model,
        messages,
        tools=tools,
        temperature=temperature,
        max_tokens=max_tokens,
        extra_body=extra_body,
        parallel_tool_calls=parallel_tool_calls,
        reasoning_effort=reasoning_effort,
        omit_temperature=omit_temperature,
        emit_deltas=False,
        recovery_budget=recovery_budget,
        request_timeout=request_timeout,
        request_context=request_context,
    )

    turn: Optional[AssistantTurn] = None
    finish_meta: Dict[str, Any] = {}
    usage: Optional[Dict[str, Any]] = None
    error: Optional[BaseException] = None
    complete_response: Optional[Any] = None
    while not sync_q.empty():
        item = sync_q.get()
        if item is None:
            continue
        kind, payload = item
        if kind == "turn":
            turn = payload
        elif kind == "finish" and isinstance(payload, dict):
            finish_meta = dict(payload)
        elif kind == "usage" and isinstance(payload, dict):
            usage = dict(payload)
        elif kind == "complete_response":
            complete_response = payload
        elif kind == "err" and isinstance(payload, BaseException):
            error = payload
    if error is not None:
        raise error
    if complete_response is not None:
        return complete_response
    if turn is None:
        raise _InvalidLLMResponseError("stream ended without an assistant response")

    message: Dict[str, Any] = {
        "role": "assistant",
        "content": turn.content or "",
    }
    if turn.reasoning_content:
        message[turn.reasoning_field or "reasoning_content"] = turn.reasoning_content
    if turn.tool_calls:
        message["tool_calls"] = [
            {
                "id": str(call.get("id") or ""),
                "type": "function",
                "function": {
                    "name": str(call.get("name") or ""),
                    "arguments": json.dumps(
                        call.get("args") or {},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            }
            for call in turn.tool_calls
        ]

    finish_reason = str(finish_meta.get("finish_reason") or "").strip()
    if finish_reason not in {"stop", "length", "tool_calls", "content_filter", "function_call"}:
        finish_reason = "tool_calls" if turn.tool_calls else "stop"
    choice: Dict[str, Any] = {
        "index": 0,
        "finish_reason": finish_reason,
        "message": message,
    }
    stop_reason = finish_meta.get("stop_reason")
    if stop_reason is not None:
        choice["stop_reason"] = stop_reason

    usage_payload: Optional[Dict[str, Any]] = None
    if usage is not None:
        usage_payload = {
            key: value
            for key, value in usage.items()
            if key not in {"_timing", "model"}
        }
    return ChatCompletion(
        id="buffered-stream",
        choices=[choice],
        created=int(time.time()),
        model=str(finish_meta.get("model") or model),
        object="chat.completion",
        usage=usage_payload,
    )


def single_turn_text_completion(
    client: OpenAI,
    model: str,
    user_text: str,
    *,
    temperature: float,
    max_tokens: int,
    text_validator: Optional[Callable[[str], bool]] = None,
) -> Tuple[str, Optional[Dict[str, int]]]:
    """Single buffered text completion used by titles and summaries."""
    def _response_validator(response: Any) -> bool:
        choices = getattr(response, "choices", None) or []
        if not choices or getattr(choices[0], "message", None) is None:
            return False
        text = _normalize_content_text(getattr(choices[0].message, "content", ""))
        if not text.strip():
            return False
        return bool(text_validator(text)) if text_validator is not None else True

    response = chat_completion(
        client,
        model,
        [UserMessage(content=user_text)],
        temperature=temperature,
        max_tokens=max_tokens,
        response_validator=_response_validator,
    )
    text = _normalize_content_text(getattr(response.choices[0].message, "content", ""))
    usage_obj = getattr(response, "usage", None)
    usage = extract_usage_dict(usage_obj) if usage_obj is not None else None
    return text, usage
