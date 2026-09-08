"""Provider-neutral streaming transport for the executor LLM.

The rest of the agent consumes :class:`TransportEvent` objects.  Provider
wire formats stay in this module:

* ``openai`` / ``@ai-sdk/openai`` -> OpenAI Responses API
* ``openai-compatible`` / ``@ai-sdk/openai-compatible`` -> Chat Completions
* ``anthropic`` / ``@ai-sdk/anthropic`` -> Anthropic Messages API

This module lives under :mod:`llm` so provider wire protocols do not leak back
into the harness or orchestration modules. ``auto`` is deliberately
conservative: only official OpenAI and Anthropic
hosts select their native protocols; every other endpoint is treated as an
OpenAI-compatible service.  A custom Responses proxy can be selected
explicitly with ``EXECUTOR_LLM_TYPE=openai``.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, Iterator, List, Optional
from urllib.parse import urlsplit

import httpx
from openai import OpenAI
from .provider_registry import provider_registry
from .responses import (
    CanonicalResponseItem,
    ContinuationAnchor,
    RequestShape,
    ResponsesCompactionCheckpoint,
    ResponsesErrorKind,
    canonicalize_response_items,
    classify_responses_error,
    evaluate_continuation,
    responses_capability_cache,
)
from .types import LLMRequestContext, LLMRequestPurpose, TransportEvent


class LLMProvider(str, Enum):
    AUTO = "auto"
    OPENAI = "openai"
    OPENAI_COMPATIBLE = "openai-compatible"
    ANTHROPIC = "anthropic"


class ResponsesStateMode(str, Enum):
    """How conversation state is carried between Responses API requests."""

    AUTO = "auto"
    STATEFUL = "stateful"
    STATELESS = "stateless"


_PROVIDER_ALIASES = {
    "": LLMProvider.AUTO,
    "auto": LLMProvider.AUTO,
    "openai": LLMProvider.OPENAI,
    "responses": LLMProvider.OPENAI,
    "openai-responses": LLMProvider.OPENAI,
    "@ai-sdk/openai": LLMProvider.OPENAI,
    "openai-compatible": LLMProvider.OPENAI_COMPATIBLE,
    "openai_compatible": LLMProvider.OPENAI_COMPATIBLE,
    "compatible": LLMProvider.OPENAI_COMPATIBLE,
    "chat-completions": LLMProvider.OPENAI_COMPATIBLE,
    "local": LLMProvider.OPENAI_COMPATIBLE,
    "@ai-sdk/openai-compatible": LLMProvider.OPENAI_COMPATIBLE,
    "anthropic": LLMProvider.ANTHROPIC,
    "claude": LLMProvider.ANTHROPIC,
    "messages": LLMProvider.ANTHROPIC,
    "@ai-sdk/anthropic": LLMProvider.ANTHROPIC,
}

PROVIDER_SEMANTICS_VERSION = 2

_RESPONSES_STATE_ALIASES = {
    "": ResponsesStateMode.AUTO,
    "auto": ResponsesStateMode.AUTO,
    "stateful": ResponsesStateMode.STATEFUL,
    "previous_response_id": ResponsesStateMode.STATEFUL,
    "stateless": ResponsesStateMode.STATELESS,
    "replay": ResponsesStateMode.STATELESS,
}

_RESPONSES_WEBSOCKET_MODE_ALIASES = {
    "": "auto",
    "auto": "auto",
    "1": "enabled",
    "true": "enabled",
    "yes": "enabled",
    "on": "enabled",
    "enabled": "enabled",
    "0": "disabled",
    "false": "disabled",
    "no": "disabled",
    "off": "disabled",
    "disabled": "disabled",
}


def _responses_websocket_mode(value: Any = None) -> str:
    raw = value
    if raw is None or not str(raw).strip():
        raw = os.getenv("RESPONSES_WEBSOCKET_MODE", "")
    if raw is None or not str(raw).strip():
        # One-cycle compatibility with the earlier boolean switch.  The
        # official-host gate below still prevents custom proxies from being
        # probed merely because the SDK exposes ``responses.connect``.
        raw = os.getenv("RESPONSES_WEBSOCKET_ENABLED", "auto")
    key = str(raw or "").strip().lower()
    try:
        return _RESPONSES_WEBSOCKET_MODE_ALIASES[key]
    except KeyError as exc:
        raise ValueError(
            f"unsupported Responses WebSocket mode {value!r}; expected auto, enabled, or disabled"
        ) from exc


def normalize_responses_state_mode(value: Any) -> ResponsesStateMode:
    if isinstance(value, ResponsesStateMode):
        return value
    key = str(value or "").strip().lower()
    try:
        return _RESPONSES_STATE_ALIASES[key]
    except KeyError as exc:
        raise ValueError(
            f"unsupported Responses state mode {value!r}; expected auto, stateful, or stateless"
        ) from exc


def normalize_provider(value: Any) -> LLMProvider:
    """Normalize a manual provider selector and reject silent typos."""
    key = str(value or "").strip().lower()
    try:
        return _PROVIDER_ALIASES[key]
    except KeyError as exc:
        allowed = "auto, openai, openai-compatible, anthropic"
        raise ValueError(f"unsupported LLM provider {value!r}; expected {allowed}") from exc


def detect_provider(base_url: Any, model: Any = "") -> LLMProvider:
    """Infer the wire protocol from an endpoint without making a paid call."""
    raw_url = str(base_url or "").strip()
    host = (urlsplit(raw_url).hostname or "").lower()
    model_id = str(model or "").strip().lower()
    if host == "api.openai.com" or host.endswith(".openai.com"):
        return LLMProvider.OPENAI
    if host == "api.anthropic.com" or host.endswith(".anthropic.com"):
        return LLMProvider.ANTHROPIC
    # A model-only hint is useful for a blank/official-looking Anthropic URL,
    # but must not override OpenRouter, local gateways, or other proxy hosts.
    if not host and model_id.startswith("claude-"):
        return LLMProvider.ANTHROPIC
    return LLMProvider.OPENAI_COMPATIBLE


def resolve_provider(value: Any, base_url: Any, model: Any = "") -> LLMProvider:
    selected = normalize_provider(value)
    return detect_provider(base_url, model) if selected is LLMProvider.AUTO else selected


def resolve_profile_provider(profile: Dict[str, Any]) -> LLMProvider:
    """Resolve a saved profile while preserving an explicit provider choice.

    Provider semantics v2: ``openai`` means chat-completions (openai-compatible)
    even on a custom proxy; ``openai-responses`` opts into the Responses wire
    protocol.  Only ``auto``/empty may infer a protocol from the endpoint.
    """
    raw = str(profile.get("llm_type") or "").strip().lower()
    if raw == "openai":
        return LLMProvider.OPENAI_COMPATIBLE
    return resolve_provider(
        profile.get("llm_type"),
        profile.get("base_url"),
        profile.get("model"),
    )


def canonical_llm_type(provider: LLMProvider) -> str:
    """Canonical llm_type string shared with the frontend's canonicalLlmType.

    The wire enum value for Responses is ``openai``, but profiles expose
    ``openai-responses`` so it cannot be confused with the chat semantics of
    plain ``openai``.
    """
    if provider is LLMProvider.OPENAI:
        return "openai-responses"
    if provider is LLMProvider.OPENAI_COMPATIBLE:
        return "openai-compatible"
    return provider.value


def _normalized_issuer_base_url(value: Any) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw
    hostname = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        port = None
    host_display = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host_display}:{port}" if port is not None else host_display
    return parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=netloc,
    ).geturl()


@dataclass(frozen=True)
class _ResponsesRequestPlan:
    """Internal proof material; never sent to the provider wire API."""

    full_input_items: tuple[Dict[str, Any], ...]
    request_shape: RequestShape
    history_generation: int
    continuation_anchor: Optional[ContinuationAnchor] = None
    continuation_reason: str = "no_anchor"
    compaction_applied: bool = False
    request_item_count: int = 0
    request_bytes: int = 0


def merge_streamed_tool_name(current: Any, incoming: Any) -> str:
    """Merge provider tool-name chunks that may be deltas or full snapshots.

    OpenAI-compatible gateways are inconsistent here: some split a function
    name across chunks, while others repeat the complete name beside every
    arguments delta.  Treat prefix-shaped values as snapshots and only append
    values that are genuine suffix fragments.
    """
    previous = str(current or "")
    value = str(incoming or "")
    if not value:
        return previous
    if not previous:
        return value
    if value == previous or previous.startswith(value):
        return previous
    if value.startswith(previous):
        return value
    return previous + value


def _merge_streamed_piece(current: Any, incoming: Any) -> tuple[str, str]:
    if isinstance(incoming, dict):
        import json as _js2
        incoming = _js2.dumps(incoming, ensure_ascii=False)
    """Return (complete value, new suffix) for delta-or-snapshot stream data."""
    previous = str(current or "")
    value = str(incoming or "")
    if not value:
        return previous, ""
    if not previous:
        return value, value
    if value == previous or previous.startswith(value):
        return previous, ""
    if value.startswith(previous):
        return value, value[len(previous):]
    return previous + value, value


def _get(value: Any, *path: str, default: Any = None) -> Any:
    current = value
    for key in path:
        if current is None:
            return default
        current = current.get(key) if isinstance(current, dict) else getattr(current, key, None)
    return default if current is None else current


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            str(_get(part, "text", default="") or "")
            for part in value
            if str(_get(part, "type", default="") or "") in {"text", "output_text"}
        )
    return str(value)


def _usage_dict(value: Any) -> Dict[str, int]:
    def number(*names: str) -> int:
        for name in names:
            raw = _get(value, name)
            try:
                if raw is not None:
                    return max(0, int(raw))
            except (TypeError, ValueError):
                pass
        return 0

    prompt = number("prompt_tokens", "input_tokens")
    completion = number("completion_tokens", "output_tokens")
    total = number("total_tokens") or prompt + completion
    input_details = _get(value, "input_tokens_details", default={}) or {}
    output_details = _get(value, "output_tokens_details", default={}) or {}
    prompt_details = _get(value, "prompt_tokens_details", default={}) or {}
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "prompt_cache_hit_tokens": (
            number("cache_read_input_tokens", "prompt_cache_hit_tokens")
            or int(_get(input_details, "cached_tokens", default=0) or 0)
            or int(_get(prompt_details, "cached_tokens", default=0) or 0)
        ),
        "prompt_cache_miss_tokens": number(
            "cache_creation_input_tokens", "prompt_cache_miss_tokens"
        ),
        "reasoning_tokens": int(_get(output_details, "reasoning_tokens", default=0) or 0),
    }


def _tool_schema(tool: Dict[str, Any]) -> Dict[str, Any]:
    fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
    return dict(fn or {})


def _responses_tools(tools: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for tool in tools:
        fn = _tool_schema(tool)
        item: Dict[str, Any] = {
            "type": "function",
            "name": str(fn.get("name") or ""),
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        }
        if fn.get("description"):
            item["description"] = str(fn["description"])
        if "strict" in fn:
            item["strict"] = bool(fn["strict"])
        if item["name"]:
            out.append(item)
    return out


def _responses_instructions(messages: Iterable[Dict[str, Any]]) -> tuple[str, List[Dict[str, Any]]]:
    instructions: List[str] = []
    conversation: List[Dict[str, Any]] = []
    for message in messages:
        if str(message.get("role") or "").lower() in {"system", "developer"}:
            text = _text(message.get("content"))
            if text:
                instructions.append(text)
        else:
            conversation.append(message)
    return "\n\n".join(instructions), conversation


def _plain_data(value: Any) -> Any:
    """Convert SDK response models into JSON-safe provider state."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _plain_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_data(item) for item in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _plain_data(dump(exclude_none=True))
    data = getattr(value, "__dict__", None)
    if isinstance(data, dict):
        return {
            str(key): _plain_data(item)
            for key, item in data.items()
            if not str(key).startswith("_")
        }
    return str(value)


def _responses_message_state(message: Dict[str, Any]) -> Dict[str, Any]:
    state = message.get("_myagent_responses")
    return dict(state) if isinstance(state, dict) else {}


def _head_continuation_anchor(
    messages: List[Dict[str, Any]],
    issuer: str,
) -> Optional[ContinuationAnchor]:
    """Return only the immediately preceding assistant turn's proven anchor.

    Tool results and a new user message may follow the assistant turn, but an
    intervening assistant turn (including one produced by another provider)
    makes every older response ID ineligible.  Legacy state has no prefix proof
    and is deliberately replay-only.
    """
    for message in reversed(messages):
        if str(message.get("role") or "") != "assistant":
            continue
        state = _responses_message_state(message)
        if int(state.get("schema_version") or 0) < 2:
            return None
        if str(state.get("issuer") or "") != issuer:
            return None
        raw_anchor = state.get("continuation_anchor")
        if not isinstance(raw_anchor, dict):
            return None
        try:
            return ContinuationAnchor.from_dict(raw_anchor)
        except (TypeError, ValueError):
            return None
    return None


def _latest_responses_state(
    messages: Iterable[Dict[str, Any]],
    issuer: str,
) -> Dict[str, Any]:
    rows = list(messages)
    for message in reversed(rows):
        if str(message.get("role") or "") != "assistant":
            continue
        state = _responses_message_state(message)
        if str(state.get("issuer") or "") == issuer:
            return state
    return {}


def _responses_prompt_cache_key(request: Dict[str, Any], instructions: str) -> str:
    stable = json.dumps(
        {
            "model": str(request.get("model") or ""),
            "instructions": instructions,
            "tools": _responses_tools(request.get("tools") or []),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "myagent-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:32]


def _responses_request_body(
    request: Dict[str, Any],
    *,
    stream: bool,
    state_mode: ResponsesStateMode = ResponsesStateMode.STATELESS,
    issuer: str = "",
    allow_continuation: bool = True,
    include_encrypted_reasoning: bool = True,
    drop_encrypted_reasoning: bool = False,
) -> tuple[Dict[str, Any], _ResponsesRequestPlan]:
    raw_messages = list(request.get("messages") or [])
    instructions, messages = _responses_instructions(raw_messages)
    full_input_items = chat_messages_to_responses_input(messages, issuer=issuer)
    if drop_encrypted_reasoning:
        full_input_items = [
            item
            for item in full_input_items
            if not (
                str(item.get("type") or "") in {"reasoning", "compaction"}
                and item.get("encrypted_content")
            )
        ]
    request_context = LLMRequestContext.from_value(request.get("request_context"))
    prompt_cache_key = str(request.get("prompt_cache_key") or "").strip()
    if not prompt_cache_key:
        prompt_cache_key = request_context.prompt_cache_key(
            issuer=issuer,
            model=str(request.get("model") or ""),
        )
    if not prompt_cache_key:
        prompt_cache_key = _responses_prompt_cache_key(request, instructions)
    history_generation = int(
        request.get("history_generation")
        if request.get("history_generation") is not None
        else request_context.history_generation
    )
    shape = RequestShape.from_request(
        request,
        issuer=issuer,
        instructions=instructions,
        prompt_cache_key=prompt_cache_key,
        store=state_mode is ResponsesStateMode.STATEFUL,
    )
    compaction_applied = False
    compacted_input_items: Optional[List[Dict[str, Any]]] = None
    checkpoint: Optional[ResponsesCompactionCheckpoint] = None
    if request_context.responses_compaction:
        try:
            checkpoint = ResponsesCompactionCheckpoint.from_dict(
                request_context.responses_compaction
            )
            compact_match = checkpoint.match(
                issuer=issuer,
                model=str(request.get("model") or ""),
                history_generation=history_generation,
                current_items=full_input_items,
            )
            if compact_match.matched:
                compacted_input_items = checkpoint.wire_items(compact_match.suffix_items)
        except (TypeError, ValueError):
            compacted_input_items = None
    anchor = (
        _head_continuation_anchor(messages, issuer)
        if state_mode is ResponsesStateMode.STATEFUL
        and allow_continuation
        else None
    )
    if (
        anchor is not None
        and checkpoint is not None
        and compacted_input_items is not None
        and anchor.covered_item_count <= checkpoint.covered_item_count
    ):
        # The checkpoint covers at least as much logical history as the old
        # anchor, so it must be installed once.  A response produced from that
        # compacted request covers additional output items and wins here on
        # subsequent rounds.
        anchor = None
    decision = evaluate_continuation(
        anchor,
        current_items=full_input_items,
        request_shape=shape,
        history_generation=history_generation,
    )
    if decision.use_previous_response:
        input_items = list(decision.suffix_items)
    elif compacted_input_items is not None:
        input_items = compacted_input_items
        compaction_applied = True
    else:
        input_items = list(full_input_items)
    body: Dict[str, Any] = {
        "model": request["model"],
        "input": input_items,
        "max_output_tokens": int(request.get("max_tokens") or 1),
        "stream": stream,
        "store": state_mode is ResponsesStateMode.STATEFUL,
        "prompt_cache_key": prompt_cache_key,
    }
    if instructions:
        # Responses does not inherit instructions through previous_response_id.
        body["instructions"] = instructions
    if decision.use_previous_response and anchor is not None:
        body["previous_response_id"] = anchor.response_id
    if (
        state_mode is ResponsesStateMode.STATELESS
        and include_encrypted_reasoning
        and request_context.purpose is LLMRequestPurpose.MAIN
    ):
        body["include"] = ["reasoning.encrypted_content"]
    if "temperature" in request:
        body["temperature"] = request["temperature"]
    if request.get("tools"):
        body["tools"] = _responses_tools(request["tools"])
        body["tool_choice"] = request.get("tool_choice") or "auto"
        body["parallel_tool_calls"] = bool(request.get("parallel_tool_calls", True))
    if request.get("reasoning_effort"):
        body["reasoning"] = {"effort": request["reasoning_effort"]}
    if request.get("extra_body"):
        body["extra_body"] = request["extra_body"]
    if request.get("timeout") is not None:
        body["timeout"] = request["timeout"]
    return body, _ResponsesRequestPlan(
        full_input_items=tuple(dict(item) for item in full_input_items),
        request_shape=shape,
        history_generation=history_generation,
        continuation_anchor=anchor if decision.use_previous_response else None,
        continuation_reason=("compaction_checkpoint" if compaction_applied else decision.reason),
        compaction_applied=compaction_applied,
        request_item_count=len(input_items),
        request_bytes=len(
            json.dumps(
                body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ),
    )


def _responses_final_text(response: Any) -> tuple[str, str]:
    text = str(_get(response, "output_text", default="") or "")
    refusal = ""
    if text:
        return text, refusal
    parts: List[str] = []
    for item in _get(response, "output", default=[]) or []:
        if str(_get(item, "type", default="") or "") != "message":
            continue
        for part in _get(item, "content", default=[]) or []:
            part_type = str(_get(part, "type", default="") or "")
            if part_type == "output_text":
                parts.append(str(_get(part, "text", default="") or ""))
            elif part_type == "refusal":
                refusal += str(_get(part, "refusal", default="") or "")
    return "".join(parts), refusal


def _anthropic_tools(tools: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for tool in tools:
        fn = _tool_schema(tool)
        item = {
            "name": str(fn.get("name") or ""),
            "description": str(fn.get("description") or ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        }
        if item["name"]:
            out.append(item)
    return out


def _responses_content(content: Any, role: str) -> Any:
    if not isinstance(content, list):
        return content if isinstance(content, str) else str(content or "")
    out: List[Dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            out.append({"type": "input_text", "text": str(part)})
            continue
        kind = str(part.get("type") or "")
        if kind == "text":
            out.append({"type": "output_text" if role == "assistant" else "input_text", "text": str(part.get("text") or "")})
        elif kind == "image_url":
            image = part.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            out.append({"type": "input_image", "image_url": str(url or "")})
        elif kind in {"file", "input_file"}:
            file_value = part.get("file") if isinstance(part.get("file"), dict) else part
            item: Dict[str, Any] = {"type": "input_file"}
            for source, target in (("file_id", "file_id"), ("file_data", "file_data"), ("filename", "filename")):
                if file_value.get(source):
                    item[target] = file_value[source]
            out.append(item)
        else:
            out.append(part)
    return out


def _responses_replay_items(message: Dict[str, Any], issuer: str) -> List[Dict[str, Any]]:
    state = _responses_message_state(message)
    if not state or str(state.get("issuer") or "") != issuer:
        return []
    if int(state.get("schema_version") or 0) >= 2:
        canonical = state.get("canonical_output_items")
        if not isinstance(canonical, list):
            return []
        out: List[Dict[str, Any]] = []
        for value in canonical:
            if not isinstance(value, dict):
                continue
            try:
                item = CanonicalResponseItem.from_dict(value)
            except (TypeError, ValueError):
                continue
            if item.issuer == issuer and item.replayability.value != "unsupported":
                out.append(dict(item.raw_item))
        return out
    items = state.get("output_items")
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, dict) and item.get("type")]


def chat_messages_to_responses_input(
    messages: Iterable[Dict[str, Any]],
    *,
    issuer: str = "",
) -> List[Dict[str, Any]]:
    """Convert Chat-shaped history to native Responses input items.

    Assistant output items captured from a prior Responses call take priority
    over lossy Chat reconstruction. This preserves encrypted reasoning items,
    item IDs, phases, and function-call metadata for stateless replay.
    """
    out: List[Dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        if role == "tool":
            out.append({
                "type": "function_call_output",
                "call_id": str(message.get("tool_call_id") or ""),
                "output": _text(message.get("content")),
            })
            continue
        if role == "assistant":
            replay_items = _responses_replay_items(message, issuer)
            if replay_items:
                out.extend(replay_items)
                continue
        content = message.get("content")
        if content not in (None, "", []):
            out.append({"role": role, "content": _responses_content(content, role)})
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                fn = _get(call, "function", default={}) or {}
                out.append({
                    "type": "function_call",
                    "call_id": str(_get(call, "id", default="") or ""),
                    "name": str(_get(fn, "name", default="") or ""),
                    "arguments": str(_get(fn, "arguments", default="{}") or "{}"),
                })
    return out


class OpenAIResponsesTransport:
    provider = LLMProvider.OPENAI
    _WEBSOCKET_TERMINAL_EVENTS = frozenset(
        {"response.completed", "response.incomplete", "response.failed", "error"}
    )

    def __init__(
        self,
        client: OpenAI,
        *,
        base_url: str = "",
        model: str = "",
        state_mode: Any = ResponsesStateMode.AUTO,
        storage_disabled: bool = False,
        credential_scope: str = "",
        organization: str = "",
        project: str = "",
        websocket_mode: Any = None,
    ):
        self.client = client
        client_base_url = str(getattr(client, "base_url", "") or "")
        self.base_url = str(base_url or client_base_url).rstrip("/")
        self.model = str(model or "")
        self.configured_state_mode = normalize_responses_state_mode(state_mode)
        # ``state_mode`` is a one-cycle compatibility input.  ``stateless`` is
        # migrated to the privacy switch; forcing ``stateful`` no longer
        # bypasses automatic issuer/capability/prefix checks.
        self.storage_disabled = bool(
            storage_disabled
            or self.configured_state_mode is ResponsesStateMode.STATELESS
        )
        self.websocket_mode = _responses_websocket_mode(websocket_mode)
        self._websocket_lock = threading.RLock()
        self._websocket_connection: Any = None
        identity = json.dumps(
            {
                "provider": self.provider.value,
                "base_url": _normalized_issuer_base_url(self.base_url),
                "model": self.model,
                "credential_scope": str(credential_scope or ""),
                "organization": str(organization or ""),
                "project": str(project or ""),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        self.issuer = hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def _official_openai_endpoint(self) -> bool:
        host = (urlsplit(self.base_url).hostname or "").lower()
        return host == "api.openai.com" or host.endswith(".openai.com")

    def _websocket_enabled(self, body: Optional[Dict[str, Any]] = None) -> bool:
        if self.websocket_mode == "disabled" or not self._official_openai_endpoint():
            return False
        if body and body.get("extra_body"):
            # ``extra_body`` is an SDK HTTP escape hatch, not a Responses
            # WebSocket event field.  Keep such requests on the HTTP baseline.
            return False
        if responses_capability_cache.get(self.issuer).websocket is False:
            return False
        return callable(getattr(getattr(self.client, "responses", None), "connect", None))

    @staticmethod
    def _websocket_status_code(exc: BaseException) -> int:
        for value in (
            getattr(exc, "status_code", None),
            getattr(getattr(exc, "response", None), "status_code", None),
        ):
            try:
                status = int(value or 0)
            except (TypeError, ValueError):
                status = 0
            if status:
                return status
        return 0

    @classmethod
    def _websocket_definitively_unsupported(cls, exc: BaseException) -> bool:
        status = cls._websocket_status_code(exc)
        if status in {404, 405, 501}:
            return True
        text = str(exc or "").lower()
        return any(
            marker in text
            for marker in (
                "unexpected http response status: 404",
                "server rejected websocket connection: http 404",
                "websocket endpoint not found",
                "websocket is not supported",
            )
        )

    def _discard_websocket_locked(self) -> None:
        connection, self._websocket_connection = self._websocket_connection, None
        close = getattr(connection, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    def _discard_websocket(self) -> None:
        with self._websocket_lock:
            self._discard_websocket_locked()

    def close(self) -> None:
        """Close the optional persistent Responses WebSocket connection."""

        self._discard_websocket()

    def _ensure_websocket_locked(self) -> Any:
        if self._websocket_connection is not None:
            return self._websocket_connection
        connect = getattr(getattr(self.client, "responses", None), "connect", None)
        if not callable(connect):
            raise RuntimeError("OpenAI SDK does not expose Responses WebSocket support")
        manager = connect(max_retries=0)
        enter = getattr(manager, "enter", None) or getattr(manager, "__enter__", None)
        if not callable(enter):
            raise RuntimeError("OpenAI Responses WebSocket manager cannot be entered")
        self._websocket_connection = enter()
        return self._websocket_connection

    @staticmethod
    def _websocket_request_body(body: Dict[str, Any]) -> Dict[str, Any]:
        wire = dict(body)
        # These are HTTP/SDK controls, not fields in a WebSocket
        # ``response.create`` event.
        wire.pop("stream", None)
        wire.pop("stream_options", None)
        wire.pop("background", None)
        wire.pop("timeout", None)
        wire.pop("extra_body", None)
        return wire

    def _websocket_stream(self, body: Dict[str, Any]) -> Iterator[Any]:
        terminal = False
        with self._websocket_lock:
            try:
                connection = self._ensure_websocket_locked()
                create = getattr(getattr(connection, "response", None), "create", None)
                if not callable(create):
                    raise RuntimeError("OpenAI Responses WebSocket has no response.create resource")
                create(**self._websocket_request_body(body))
                while True:
                    event = connection.recv()
                    event_type = str(_get(event, "type", default="") or "")
                    terminal = event_type in self._WEBSOCKET_TERMINAL_EVENTS
                    yield event
                    if terminal:
                        return
            except BaseException:
                self._discard_websocket_locked()
                raise
            finally:
                # A cancelled/losing consumer leaves unread events on the
                # default lane.  Drop that socket instead of corrupting the
                # next logical response with stale frames.
                if not terminal:
                    self._discard_websocket_locked()

    def _effective_state_mode(self, request: Dict[str, Any]) -> ResponsesStateMode:
        request_context = LLMRequestContext.from_value(request.get("request_context"))
        if self.storage_disabled or not request_context.server_storage_allowed:
            return ResponsesStateMode.STATELESS
        capabilities = responses_capability_cache.get(self.issuer)
        if capabilities.store is False or capabilities.previous_response_id is False:
            return ResponsesStateMode.STATELESS
        host = (urlsplit(self.base_url).hostname or "").lower()
        official_openai = host == "api.openai.com" or host.endswith(".openai.com")
        # A custom Responses proxy may return response-shaped IDs without
        # retaining the referenced response. Treat stateless native replay as
        # the safe automatic baseline; callers can still explicitly request
        # stateful mode for a proxy known to implement stored continuations.
        if not official_openai:
            return ResponsesStateMode.STATELESS
        latest = _latest_responses_state(
            request.get("messages") or [],
            self.issuer,
        )
        # Official OpenAI uses stored continuation automatically unless a
        # prior request established that storage is unavailable.
        if latest.get("stateful_supported") is False:
            return ResponsesStateMode.STATELESS
        return ResponsesStateMode.STATEFUL

    def _body(
        self,
        request: Dict[str, Any],
        *,
        stream: bool,
        state_mode: Optional[ResponsesStateMode] = None,
        allow_continuation: bool = True,
        include_encrypted_reasoning: bool = True,
        drop_encrypted_reasoning: bool = False,
    ) -> tuple[Dict[str, Any], ResponsesStateMode, _ResponsesRequestPlan]:
        mode = state_mode or self._effective_state_mode(request)
        latest_state = _latest_responses_state(
            request.get("messages") or [],
            self.issuer,
        )
        if (
            str(latest_state.get("fallback_reason") or "")
            == ResponsesErrorKind.UNSUPPORTED_ENCRYPTED_REASONING.value
        ):
            responses_capability_cache.update(
                self.issuer,
                encrypted_reasoning_replay=False,
            )
        if (
            mode is ResponsesStateMode.STATELESS
            and responses_capability_cache.get(
                self.issuer
            ).encrypted_reasoning_replay is False
        ):
            include_encrypted_reasoning = False
            drop_encrypted_reasoning = True
        body, plan = _responses_request_body(
            request,
            stream=stream,
            state_mode=mode,
            issuer=self.issuer,
            allow_continuation=allow_continuation,
            include_encrypted_reasoning=include_encrypted_reasoning,
            drop_encrypted_reasoning=drop_encrypted_reasoning,
        )
        return body, mode, plan

    def stream_completion(self, **request: Any) -> Iterator[TransportEvent]:
        request_dict = dict(request)
        body, mode, plan = self._body(request_dict, stream=True)
        visible_output = False
        websocket_fallback_reason = ""

        if self._websocket_enabled(body):
            try:
                for event in self._events(
                    self._websocket_stream(body),
                    fallback_model=str(request.get("model") or ""),
                    issuer=self.issuer,
                    state_mode=mode.value,
                    stateful_supported=(mode is ResponsesStateMode.STATEFUL),
                    request_plan=plan,
                    wire_transport="websocket",
                ):
                    visible_output = visible_output or event.is_first_token
                    yield event
                responses_capability_cache.update(
                    self.issuer,
                    responses=True,
                    websocket=True,
                    **(
                        {"store": True, "previous_response_id": True}
                        if mode is ResponsesStateMode.STATEFUL
                        else {"encrypted_reasoning_replay": True}
                        if "include" in body
                        else {}
                    ),
                )
                return
            except Exception as websocket_exc:
                if visible_output:
                    raise
                self._discard_websocket()
                if self._websocket_definitively_unsupported(websocket_exc):
                    responses_capability_cache.update(self.issuer, websocket=False)
                    websocket_fallback_reason = "websocket_unsupported"
                else:
                    # Authentication, rate limits, timeouts and 5xx responses
                    # are not capability evidence.  Fall back for this logical
                    # request without poisoning later WebSocket attempts.
                    websocket_fallback_reason = (
                        "websocket_error:" + type(websocket_exc).__name__
                    )

        try:
            stream = self.client.responses.create(**body)
            for event in self._events(
                stream,
                fallback_model=str(request.get("model") or ""),
                issuer=self.issuer,
                state_mode=mode.value,
                stateful_supported=(mode is ResponsesStateMode.STATEFUL),
                request_plan=plan,
                wire_transport="http_sse",
                transport_fallback_reason=websocket_fallback_reason,
            ):
                visible_output = visible_output or event.is_first_token
                yield event
            capability_updates: Dict[str, Optional[bool]] = {"responses": True}
            if mode is ResponsesStateMode.STATEFUL:
                capability_updates.update(store=True, previous_response_id=True)
            elif "include" in body:
                capability_updates["encrypted_reasoning_replay"] = True
            responses_capability_cache.update(self.issuer, **capability_updates)
        except Exception as exc:
            error_info = classify_responses_error(exc)
            fallback_reason = error_info.kind.value
            recoverable = error_info.kind in {
                ResponsesErrorKind.INVALID_PREVIOUS,
                ResponsesErrorKind.UNSUPPORTED_STATE,
                ResponsesErrorKind.INVALID_ENCRYPTED_REASONING,
                ResponsesErrorKind.UNSUPPORTED_ENCRYPTED_REASONING,
            }
            if visible_output or not recoverable:
                raise
            official_openai = self._official_openai_endpoint()
            recovery_mode = (
                ResponsesStateMode.STATEFUL
                if error_info.kind is ResponsesErrorKind.INVALID_PREVIOUS and official_openai
                else ResponsesStateMode.STATELESS
            )
            include_encrypted = (
                error_info.kind is not ResponsesErrorKind.UNSUPPORTED_ENCRYPTED_REASONING
            )
            drop_encrypted = error_info.kind in {
                ResponsesErrorKind.INVALID_ENCRYPTED_REASONING,
                ResponsesErrorKind.UNSUPPORTED_ENCRYPTED_REASONING,
            }
            if error_info.kind is ResponsesErrorKind.UNSUPPORTED_STATE:
                responses_capability_cache.update(
                    self.issuer,
                    store=False,
                    previous_response_id=False,
                )
            elif error_info.kind is ResponsesErrorKind.UNSUPPORTED_ENCRYPTED_REASONING:
                responses_capability_cache.update(
                    self.issuer,
                    encrypted_reasoning_replay=False,
                )
            fallback_body, fallback_mode, fallback_plan = self._body(
                request_dict,
                stream=True,
                state_mode=recovery_mode,
                allow_continuation=False,
                include_encrypted_reasoning=include_encrypted,
                drop_encrypted_reasoning=drop_encrypted,
            )
            fallback_stream = self.client.responses.create(**fallback_body)
            if error_info.kind is ResponsesErrorKind.INVALID_PREVIOUS and official_openai:
                stateful_supported: Optional[bool] = True
            elif error_info.kind in {
                ResponsesErrorKind.INVALID_PREVIOUS,
                ResponsesErrorKind.UNSUPPORTED_STATE,
            }:
                stateful_supported = False
            else:
                stateful_supported = None
            yield from self._events(
                fallback_stream,
                fallback_model=str(request.get("model") or ""),
                issuer=self.issuer,
                state_mode=fallback_mode.value,
                stateful_supported=stateful_supported,
                fallback_reason=fallback_reason,
                request_plan=fallback_plan,
                wire_transport="http_sse",
                transport_fallback_reason=websocket_fallback_reason,
            )

    def complete_text(self, **request: Any) -> Dict[str, Any]:
        # Judge/title/summary calls are one-shot and never need server state.
        body, _mode, _plan = self._body(
            dict(request),
            stream=False,
            state_mode=ResponsesStateMode.STATELESS,
            include_encrypted_reasoning=False,
        )
        response = self.client.responses.create(**body)
        text, refusal = _responses_final_text(response)
        status = str(_get(response, "status", default="") or "completed")
        return {
            "text": text,
            "usage": _usage_dict(_get(response, "usage", default={}) or {}),
            "model": str(_get(response, "model", default="") or request.get("model") or ""),
            "response_id": str(_get(response, "id", default="") or ""),
            "status": status,
            "finish_reason": "length" if status == "incomplete" else "stop",
            "incomplete_reason": str(
                _get(response, "incomplete_details", "reason", default="") or ""
            ),
            "refusal": refusal,
            "error": str(_get(response, "error", "message", default="") or ""),
        }

    def compact_history(self, **request: Any) -> ResponsesCompactionCheckpoint:
        """Create a provider-scoped opaque checkpoint from complete local history."""
        capabilities = responses_capability_cache.get(self.issuer)
        if capabilities.compact is False:
            raise RuntimeError("Responses compact is disabled for this issuer")
        instructions, messages = _responses_instructions(list(request.get("messages") or []))
        source_items = chat_messages_to_responses_input(messages, issuer=self.issuer)
        request_context = LLMRequestContext.from_value(request.get("request_context"))
        prompt_cache_key = str(request.get("prompt_cache_key") or "").strip()
        if not prompt_cache_key:
            prompt_cache_key = request_context.prompt_cache_key(
                issuer=self.issuer,
                model=str(request.get("model") or self.model),
            )
        if not prompt_cache_key:
            prompt_cache_key = _responses_prompt_cache_key(dict(request), instructions)
        body: Dict[str, Any] = {
            "model": str(request.get("model") or self.model),
            "input": source_items,
            "prompt_cache_key": prompt_cache_key,
        }
        if instructions:
            body["instructions"] = instructions
        if request.get("timeout") is not None:
            body["timeout"] = request["timeout"]
        try:
            response = self.client.responses.compact(**body)
        except Exception as exc:
            info = classify_responses_error(exc, operation="compact")
            if info.kind is ResponsesErrorKind.UNSUPPORTED_COMPACT:
                responses_capability_cache.update(self.issuer, compact=False)
            raise
        plain_output = _plain_data(_get(response, "output", default=[]) or [])
        if not isinstance(plain_output, list):
            responses_capability_cache.update(self.issuer, compact=False)
            raise ValueError("Responses compact returned invalid output")
        try:
            checkpoint = ResponsesCompactionCheckpoint.create(
                issuer=self.issuer,
                model=str(request.get("model") or self.model),
                source_history_generation=request_context.history_generation,
                source_items=source_items,
                compacted_output_items=[
                    item for item in plain_output if isinstance(item, dict)
                ],
                usage=_usage_dict(_get(response, "usage", default={}) or {}),
                source_estimated_tokens=int(
                    request.get("source_estimated_tokens") or 0
                ),
            )
        except ValueError:
            responses_capability_cache.update(self.issuer, compact=False)
            raise
        responses_capability_cache.update(self.issuer, compact=True)
        return checkpoint

    @staticmethod
    def _stateful_fallback_reason(exc: BaseException) -> str:
        info = classify_responses_error(exc)
        if info.kind in {
            ResponsesErrorKind.INVALID_PREVIOUS,
            ResponsesErrorKind.UNSUPPORTED_STATE,
        }:
            return info.kind.value
        return ""

    @staticmethod
    def _events(
        stream: Iterable[Any],
        *,
        fallback_model: str,
        issuer: str = "",
        state_mode: str = "stateless",
        stateful_supported: Optional[bool] = None,
        fallback_reason: str = "",
        request_plan: Optional[_ResponsesRequestPlan] = None,
        wire_transport: str = "",
        transport_fallback_reason: str = "",
    ) -> Iterator[TransportEvent]:
        tool_state: Dict[int, Dict[str, str]] = {}
        text_state: Dict[tuple[str, int], str] = {}
        output_items: Dict[int, Dict[str, Any]] = {}
        saw_tool = False
        finish_emitted = False
        response_id = ""
        response_status = ""

        def remember_item(event: Any, item: Any) -> None:
            index = int(_get(event, "output_index", default=len(output_items)) or 0)
            plain = _plain_data(item)
            if isinstance(plain, dict) and plain.get("type"):
                output_items[index] = plain

        def provider_state_event(model: str) -> Optional[TransportEvent]:
            if not response_id and not output_items:
                return None
            ordered_output_items = [output_items[index] for index in sorted(output_items)]
            provider_data: Dict[str, Any] = {
                "api": "responses",
                "issuer": issuer,
                "state_mode": state_mode,
                "response_id": response_id,
                "status": response_status or "completed",
                "output_items": ordered_output_items,
            }
            if wire_transport:
                provider_data["wire_transport"] = str(wire_transport)
            if transport_fallback_reason:
                provider_data["transport_fallback_reason"] = str(
                    transport_fallback_reason
                )
            if request_plan is not None:
                canonical = canonicalize_response_items(
                    ordered_output_items,
                    issuer=issuer,
                )
                completed = (response_status or "completed") == "completed"
                anchor = None
                if state_mode == ResponsesStateMode.STATEFUL.value and response_id:
                    anchor = ContinuationAnchor.create(
                        response_id=response_id,
                        history_generation=request_plan.history_generation,
                        request_shape=request_plan.request_shape,
                        request_items=request_plan.full_input_items,
                        response_output_items=canonical,
                        completed=completed,
                        server_stored=completed,
                    )
                provider_data.update(
                    {
                        "schema_version": 2,
                        "canonical_output_items": [item.to_dict() for item in canonical],
                        "continuation_anchor": anchor.to_dict() if anchor is not None else None,
                        "responses_mode": (
                            "previous_id_delta"
                            if request_plan.continuation_anchor is not None
                            else (
                                "compacted_store"
                                if request_plan.compaction_applied
                                else "full_store"
                                if state_mode == ResponsesStateMode.STATEFUL.value
                                else "stateless_replay"
                            )
                        ),
                        "full_replay_reason": request_plan.continuation_reason,
                        "request_item_count": request_plan.request_item_count,
                        "request_bytes": request_plan.request_bytes,
                        "continuation_recovery_count": 1 if fallback_reason else 0,
                    }
                )
            if stateful_supported is not None:
                provider_data["stateful_supported"] = stateful_supported
            if fallback_reason:
                provider_data["fallback_reason"] = fallback_reason
            return TransportEvent(
                "provider_state",
                model=model,
                provider_data=provider_data,
            )

        def text_key(
            event: Any,
            *,
            item: Any = None,
            content_index: Optional[int] = None,
        ) -> tuple[str, int]:
            item_id = str(
                _get(event, "item_id", default="")
                or _get(item, "id", default="")
                or f"output:{int(_get(event, 'output_index', default=0) or 0)}"
            )
            index = (
                int(content_index)
                if content_index is not None
                else int(_get(event, "content_index", default=0) or 0)
            )
            return item_id, index

        def text_delta_event(event: Any, value: Any) -> Optional[TransportEvent]:
            text = str(value or "")
            if not text:
                return None
            key = text_key(event)
            text_state[key] = text_state.get(key, "") + text
            return TransportEvent("content_delta", text=text)

        def text_snapshot_event(
            event: Any,
            value: Any,
            *,
            item: Any = None,
            content_index: Optional[int] = None,
        ) -> Optional[TransportEvent]:
            text = str(value or "")
            if not text:
                return None
            key = text_key(event, item=item, content_index=content_index)
            fallback_key = (
                f"output:{int(_get(event, 'output_index', default=0) or 0)}",
                key[1],
            )
            if key not in text_state and fallback_key in text_state:
                key = fallback_key
            merged, suffix = _merge_streamed_piece(text_state.get(key, ""), text)
            text_state[key] = merged
            if not suffix:
                return None
            return TransportEvent("content_delta", text=suffix)

        def message_snapshot_events(event: Any, item: Any) -> Iterator[TransportEvent]:
            if str(_get(item, "type", default="") or "") != "message":
                return
            for content_index, part in enumerate(_get(item, "content", default=[]) or []):
                part_type = str(_get(part, "type", default="") or "")
                if part_type == "output_text":
                    normalized = text_snapshot_event(
                        event,
                        _get(part, "text", default="") or "",
                        item=item,
                        content_index=content_index,
                    )
                    if normalized is not None:
                        yield normalized

        def tool_event(event: Any, item: Any = None, arguments: Any = None) -> TransportEvent:
            nonlocal saw_tool
            saw_tool = True
            index = int(_get(event, "output_index", default=0) or 0)
            state = tool_state.setdefault(index, {"id": "", "name": "", "arguments": ""})
            call_id = str(_get(item, "call_id", default="") or _get(event, "call_id", default="") or "")
            name = str(_get(item, "name", default="") or _get(event, "name", default="") or "")
            id_delta = ""
            if call_id and call_id != state["id"]:
                state["id"] = call_id
                id_delta = call_id
            previous_name = state["name"]
            state["name"] = merge_streamed_tool_name(previous_name, name)
            name_delta = state["name"] if state["name"] != previous_name else ""
            state["arguments"], delta = _merge_streamed_piece(
                state["arguments"],
                arguments if arguments is not None else "",
            )
            return TransportEvent(
                "tool_call_delta",
                index=index,
                tool_call_id=id_delta,
                tool_name=name_delta,
                arguments_delta=delta,
            )

        for event in stream:
            event_type = str(_get(event, "type", default="") or "")
            if event_type in {"response.created", "response.in_progress"}:
                response = _get(event, "response", default={}) or {}
                response_id = str(_get(response, "id", default="") or response_id)
                response_status = str(_get(response, "status", default="") or response_status)
            elif event_type == "response.output_text.delta":
                normalized = text_delta_event(event, _get(event, "delta", default="") or "")
                if normalized is not None:
                    yield normalized
            elif event_type == "response.output_text.done":
                normalized = text_snapshot_event(event, _get(event, "text", default="") or "")
                if normalized is not None:
                    yield normalized
            elif event_type == "response.content_part.done":
                part = _get(event, "part", default={}) or {}
                if str(_get(part, "type", default="") or "") == "output_text":
                    normalized = text_snapshot_event(
                        event,
                        _get(part, "text", default="") or "",
                    )
                    if normalized is not None:
                        yield normalized
            elif event_type in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
                yield TransportEvent("reasoning_delta", text=str(_get(event, "delta", default="") or ""))
            elif event_type == "response.output_item.added":
                item = _get(event, "item", default={}) or {}
                remember_item(event, item)
                if str(_get(item, "type", default="") or "") == "function_call":
                    yield tool_event(event, item=item)
            elif event_type == "response.function_call_arguments.delta":
                yield tool_event(event, arguments=_get(event, "delta", default="") or "")
            elif event_type == "response.function_call_arguments.done":
                yield tool_event(event, arguments=_get(event, "arguments", default="") or "")
            elif event_type == "response.output_item.done":
                item = _get(event, "item", default={}) or {}
                remember_item(event, item)
                item_type = str(_get(item, "type", default="") or "")
                if item_type == "function_call":
                    yield tool_event(event, item=item, arguments=_get(item, "arguments", default="") or "")
                elif item_type == "message":
                    yield from message_snapshot_events(event, item)
            elif event_type == "response.completed":
                response = _get(event, "response", default={}) or {}
                response_id = str(_get(response, "id", default="") or response_id)
                response_status = str(_get(response, "status", default="") or "completed")
                model = str(_get(response, "model", default="") or fallback_model)
                for output_index, item in enumerate(_get(response, "output", default=[]) or []):
                    snapshot_event = {
                        "output_index": output_index,
                        "item_id": str(_get(item, "id", default="") or ""),
                    }
                    remember_item(snapshot_event, item)
                    if str(_get(item, "type", default="") or "") == "function_call":
                        yield tool_event(
                            snapshot_event,
                            item=item,
                            arguments=_get(item, "arguments", default="") or "",
                        )
                    else:
                        yield from message_snapshot_events(snapshot_event, item)
                state_event = provider_state_event(model)
                if state_event is not None:
                    yield state_event
                usage = _get(response, "usage")
                if usage is not None:
                    yield TransportEvent("usage", usage=_usage_dict(usage), model=model)
                yield TransportEvent(
                    "finish",
                    finish_reason="tool_calls" if saw_tool else "stop",
                    model=model,
                )
                finish_emitted = True
            elif event_type == "response.incomplete":
                response = _get(event, "response", default={}) or {}
                response_id = str(_get(response, "id", default="") or response_id)
                response_status = str(_get(response, "status", default="") or "incomplete")
                model = str(_get(response, "model", default="") or fallback_model)
                for output_index, item in enumerate(_get(response, "output", default=[]) or []):
                    snapshot_event = {
                        "output_index": output_index,
                        "item_id": str(_get(item, "id", default="") or ""),
                    }
                    remember_item(snapshot_event, item)
                    if str(_get(item, "type", default="") or "") == "function_call":
                        yield tool_event(
                            snapshot_event,
                            item=item,
                            arguments=_get(item, "arguments", default="") or "",
                        )
                    else:
                        yield from message_snapshot_events(snapshot_event, item)
                state_event = provider_state_event(model)
                if state_event is not None:
                    yield state_event
                usage = _get(response, "usage")
                if usage is not None:
                    yield TransportEvent("usage", usage=_usage_dict(usage), model=model)
                yield TransportEvent(
                    "finish",
                    finish_reason="length",
                    model=model,
                )
                finish_emitted = True
            elif event_type in {"response.failed", "error"}:
                error = _get(event, "response", "error") or _get(event, "error") or event
                raise RuntimeError(f"OpenAI Responses stream failed: {_get(error, 'message', default=error)}")
        if not finish_emitted:
            if response_id:
                state_event = provider_state_event(fallback_model)
                if state_event is not None:
                    yield state_event
            yield TransportEvent("finish", finish_reason="tool_calls" if saw_tool else "stop", model=fallback_model)


class OpenAICompatibleTransport:
    provider = LLMProvider.OPENAI_COMPATIBLE

    def __init__(self, client: OpenAI):
        self.client = client

    def stream_completion(self, **request: Any) -> Iterator[TransportEvent]:
        kwargs = dict(request)
        kwargs.pop("request_context", None)
        kwargs.pop("history_generation", None)
        kwargs.pop("prompt_cache_key", None)
        kwargs["messages"] = [
            {key: value for key, value in message.items() if key != "_myagent_responses"}
            for message in (kwargs.get("messages") or [])
        ]
        kwargs["stream"] = True
        response = self.client.chat.completions.create(**kwargs)
        try:
            iterator = iter(response)
        except TypeError:
            yield from self._complete_response(response, str(request.get("model") or ""))
            return
        tool_state: Dict[int, Dict[str, str]] = {}
        for chunk in iterator:
            model = str(_get(chunk, "model", default="") or "")
            usage = _get(chunk, "usage")
            if usage is not None:
                yield TransportEvent("usage", usage=_usage_dict(usage), model=model)
            choices = _get(chunk, "choices", default=[]) or []
            if not choices:
                continue
            choice = choices[0]
            delta = _get(choice, "delta", default={}) or {}
            reasoning = _get(delta, "reasoning_content") or _get(delta, "reasoning")
            if reasoning:
                yield TransportEvent("reasoning_delta", text=_text(reasoning), model=model)
            content = _get(delta, "content")
            if content:
                yield TransportEvent("content_delta", text=_text(content), model=model)
            for call in _get(delta, "tool_calls", default=[]) or []:
                fn = _get(call, "function", default={}) or {}
                index = int(_get(call, "index", default=0) or 0)
                state = tool_state.setdefault(
                    index,
                    {"id": "", "name": "", "arguments": ""},
                )
                raw_id = str(_get(call, "id", default="") or "")
                id_delta = ""
                if raw_id and raw_id != state["id"]:
                    state["id"] = raw_id
                    id_delta = raw_id
                previous_name = state["name"]
                state["name"] = merge_streamed_tool_name(
                    previous_name,
                    _get(fn, "name", default="") or "",
                )
                name_delta = state["name"] if state["name"] != previous_name else ""
                # Chat Completions arguments are always pure deltas: every
                # chunk carries a fragment to append, never a snapshot to
                # dedupe.  Accumulate verbatim — a prefix-dropping merge would
                # discard recurring fragments like `{"` (every nested object
                # starts with one) and silently corrupt the JSON.
                fragment = _get(fn, "arguments", default="") or ""
                state["arguments"] += str(fragment)
                yield TransportEvent(
                    "tool_call_delta",
                    index=index,
                    tool_call_id=id_delta,
                    tool_name=name_delta,
                    arguments_delta=str(fragment),
                    model=model,
                )
            finish = _get(choice, "finish_reason")
            if finish is not None:
                yield TransportEvent(
                    "finish",
                    finish_reason=str(finish),
                    stop_reason=str(_get(choice, "stop_reason", default="") or "") or None,
                    model=model,
                )

    def complete_text(self, **request: Any) -> Dict[str, Any]:
        kwargs = dict(request)
        kwargs.pop("request_context", None)
        kwargs.pop("history_generation", None)
        kwargs.pop("prompt_cache_key", None)
        kwargs["messages"] = [
            {key: value for key, value in message.items() if key != "_myagent_responses"}
            for message in (kwargs.get("messages") or [])
        ]
        kwargs.pop("parallel_tool_calls", None)
        kwargs["stream"] = False
        response = self.client.chat.completions.create(**kwargs)
        choices = _get(response, "choices", default=[]) or []
        choice = choices[0] if choices else {}
        message = _get(choice, "message", default={}) or {}
        return {
            "text": _text(_get(message, "content", default="") or ""),
            "usage": _usage_dict(_get(response, "usage", default={}) or {}),
            "model": str(_get(response, "model", default="") or request.get("model") or ""),
            "response_id": str(_get(response, "id", default="") or ""),
            "status": "completed",
            "finish_reason": str(_get(choice, "finish_reason", default="") or "stop"),
            "incomplete_reason": "",
            "refusal": str(_get(message, "refusal", default="") or ""),
            "error": "",
        }

    @staticmethod
    def _complete_response(response: Any, fallback_model: str) -> Iterator[TransportEvent]:
        model = str(_get(response, "model", default="") or fallback_model)
        choices = _get(response, "choices", default=[]) or []
        if choices:
            choice = choices[0]
            message = _get(choice, "message", default={}) or {}
            reasoning = _get(message, "reasoning_content") or _get(message, "reasoning")
            if reasoning:
                yield TransportEvent("reasoning_delta", text=_text(reasoning), model=model)
            content = _get(message, "content")
            if content:
                yield TransportEvent("content_delta", text=_text(content), model=model)
            for index, call in enumerate(_get(message, "tool_calls", default=[]) or []):
                fn = _get(call, "function", default={}) or {}
                yield TransportEvent(
                    "tool_call_delta",
                    index=int(_get(call, "index", default=index) or index),
                    tool_call_id=str(_get(call, "id", default="") or ""),
                    tool_name=str(_get(fn, "name", default="") or ""),
                    arguments_delta=str(_get(fn, "arguments", default="") or ""),
                    model=model,
                )
            yield TransportEvent("finish", finish_reason=str(_get(choice, "finish_reason", default="stop") or "stop"), model=model)
        usage = _get(response, "usage")
        if usage is not None:
            yield TransportEvent("usage", usage=_usage_dict(usage), model=model)


def _anthropic_content(content: Any) -> List[Dict[str, Any]]:
    if not isinstance(content, list):
        return [{"type": "text", "text": str(content or "")}]
    out: List[Dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            out.append({"type": "text", "text": str(part)})
            continue
        kind = str(part.get("type") or "")
        if kind == "text":
            out.append({"type": "text", "text": str(part.get("text") or "")})
        elif kind == "image_url":
            image = part.get("image_url")
            url = str(image.get("url") if isinstance(image, dict) else image or "")
            if url.startswith("data:") and ";base64," in url:
                header, data = url.split(",", 1)
                out.append({"type": "image", "source": {"type": "base64", "media_type": header[5:].split(";", 1)[0], "data": data}})
            elif url:
                out.append({"type": "image", "source": {"type": "url", "url": url}})
        else:
            out.append({"type": "text", "text": _text(part)})
    return out


def chat_messages_to_anthropic(messages: Iterable[Dict[str, Any]]) -> tuple[str, List[Dict[str, Any]]]:
    system_parts: List[str] = []
    converted: List[Dict[str, Any]] = []

    def append(role: str, blocks: List[Dict[str, Any]]) -> None:
        if converted and converted[-1]["role"] == role:
            converted[-1]["content"].extend(blocks)
        else:
            converted.append({"role": role, "content": blocks})

    for message in messages:
        role = str(message.get("role") or "user")
        if role in {"system", "developer"}:
            system_parts.append(_text(message.get("content")))
            continue
        if role == "tool":
            append("user", [{
                "type": "tool_result",
                "tool_use_id": str(message.get("tool_call_id") or ""),
                "content": _text(message.get("content")),
            }])
            continue
        blocks = _anthropic_content(message.get("content"))
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                fn = _get(call, "function", default={}) or {}
                raw_args = _get(fn, "arguments", default="{}") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except (TypeError, ValueError):
                    args = {}
                blocks.append({
                    "type": "tool_use",
                    "id": str(_get(call, "id", default="") or ""),
                    "name": str(_get(fn, "name", default="") or ""),
                    "input": args or {},
                })
        append("assistant" if role == "assistant" else "user", blocks)
    return "\n\n".join(part for part in system_parts if part), converted


class AnthropicMessagesTransport:
    provider = LLMProvider.ANTHROPIC

    def __init__(self, *, api_key: str, base_url: str, http_client: Optional[httpx.Client] = None):
        self.api_key = str(api_key or "")
        self.base_url = str(base_url or "https://api.anthropic.com").rstrip("/")
        self.http_client = http_client or httpx.Client(timeout=60.0)

    def _messages_url(self) -> str:
        if self.base_url.endswith("/messages"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return self.base_url + "/messages"
        return self.base_url + "/v1/messages"

    def stream_completion(self, **request: Any) -> Iterator[TransportEvent]:
        system, messages = chat_messages_to_anthropic(request.get("messages") or [])
        body: Dict[str, Any] = {
            "model": request["model"],
            "messages": messages,
            "max_tokens": int(request.get("max_tokens") or 1),
            "stream": True,
        }
        if system:
            body["system"] = system
        if "temperature" in request:
            body["temperature"] = request["temperature"]
        if request.get("tools"):
            body["tools"] = _anthropic_tools(request["tools"])
            if request.get("tool_choice") == "auto":
                body["tool_choice"] = {"type": "auto"}
        extra = request.get("extra_body")
        if isinstance(extra, dict):
            body.update(extra)
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "accept": "text/event-stream",
        }
        timeout = request.get("timeout")
        stream_kwargs: Dict[str, Any] = {}
        if timeout is not None:
            stream_kwargs["timeout"] = float(timeout)
        with self.http_client.stream("POST", self._messages_url(), headers=headers, json=body, **stream_kwargs) as response:
            response.raise_for_status()
            yield from self._events(response.iter_lines(), fallback_model=str(request.get("model") or ""))

    def complete_text(self, **request: Any) -> Dict[str, Any]:
        system, messages = chat_messages_to_anthropic(request.get("messages") or [])
        body: Dict[str, Any] = {
            "model": request["model"],
            "messages": messages,
            "max_tokens": int(request.get("max_tokens") or 1),
            "stream": False,
        }
        if system:
            body["system"] = system
        if "temperature" in request:
            body["temperature"] = request["temperature"]
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        timeout = request.get("timeout")
        kwargs = {"timeout": float(timeout)} if timeout is not None else {}
        response = self.http_client.post(
            self._messages_url(), headers=headers, json=body, **kwargs
        )
        response.raise_for_status()
        payload = response.json()
        text = "".join(
            str(part.get("text") or "")
            for part in payload.get("content") or []
            if str(part.get("type") or "") == "text"
        )
        stop_reason = str(payload.get("stop_reason") or "")
        return {
            "text": text,
            "usage": _usage_dict(payload.get("usage") or {}),
            "model": str(payload.get("model") or request.get("model") or ""),
            "response_id": str(payload.get("id") or ""),
            "status": "completed",
            "finish_reason": "length" if stop_reason == "max_tokens" else "stop",
            "incomplete_reason": "max_output_tokens" if stop_reason == "max_tokens" else "",
            "refusal": "",
            "error": "",
        }

    @staticmethod
    def _events(lines: Iterable[str], *, fallback_model: str) -> Iterator[TransportEvent]:
        usage: Dict[str, int] = {}
        model = fallback_model
        finish_reason: Optional[str] = None
        tool_blocks: Dict[int, Dict[str, str]] = {}
        for raw_line in lines:
            line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            event = json.loads(payload)
            event_type = str(event.get("type") or "")
            if event_type == "error":
                error = event.get("error") or {}
                raise RuntimeError(f"Anthropic Messages stream failed: {error.get('message') or error}")
            if event_type == "message_start":
                message = event.get("message") or {}
                model = str(message.get("model") or model)
                usage.update(_usage_dict(message.get("usage") or {}))
            elif event_type == "content_block_start":
                index = int(event.get("index") or 0)
                block = event.get("content_block") or {}
                if block.get("type") == "tool_use":
                    tool_blocks[index] = {"id": str(block.get("id") or ""), "name": str(block.get("name") or "")}
                    initial = block.get("input")
                    yield TransportEvent(
                        "tool_call_delta",
                        index=index,
                        tool_call_id=tool_blocks[index]["id"],
                        tool_name=tool_blocks[index]["name"],
                        arguments_delta=json.dumps(initial, ensure_ascii=False, separators=(",", ":")) if initial else "",
                        model=model,
                    )
                elif block.get("type") == "text" and block.get("text"):
                    yield TransportEvent("content_delta", text=str(block["text"]), model=model)
                elif block.get("type") == "thinking" and block.get("thinking"):
                    yield TransportEvent("reasoning_delta", text=str(block["thinking"]), model=model)
            elif event_type == "content_block_delta":
                index = int(event.get("index") or 0)
                delta = event.get("delta") or {}
                delta_type = str(delta.get("type") or "")
                if delta_type == "text_delta":
                    yield TransportEvent("content_delta", text=str(delta.get("text") or ""), model=model)
                elif delta_type == "thinking_delta":
                    yield TransportEvent("reasoning_delta", text=str(delta.get("thinking") or ""), model=model)
                elif delta_type == "input_json_delta":
                    state = tool_blocks.get(index, {})
                    yield TransportEvent(
                        "tool_call_delta",
                        index=index,
                        tool_call_id=state.get("id", ""),
                        tool_name=state.get("name", ""),
                        arguments_delta=str(delta.get("partial_json") or ""),
                        model=model,
                    )
            elif event_type == "message_delta":
                delta = event.get("delta") or {}
                stop = str(delta.get("stop_reason") or "")
                if stop:
                    finish_reason = "tool_calls" if stop == "tool_use" else ("length" if stop == "max_tokens" else "stop")
                update = _usage_dict(event.get("usage") or {})
                if update.get("completion_tokens"):
                    usage["completion_tokens"] = update["completion_tokens"]
                usage["total_tokens"] = int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
            elif event_type == "message_stop":
                if usage:
                    yield TransportEvent("usage", usage=usage, model=model)
                yield TransportEvent("finish", finish_reason=finish_reason or ("tool_calls" if tool_blocks else "stop"), model=model)


def _profile_request_headers(profile: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Custom per-profile request headers (e.g. OpenCode ``x-opencode-*``)."""
    raw = profile.get("headers")
    if not isinstance(raw, dict) or not raw:
        return None
    out: Dict[str, str] = {}
    for key, value in raw.items():
        key = str(key or "").strip()
        if not key or value is None:
            continue
        out[key] = str(value)
    return out or None


def _ensure_builtin_provider_registry() -> None:
    snapshot = provider_registry.snapshot()
    if not all(
        item.value in snapshot
        for item in (LLMProvider.ANTHROPIC, LLMProvider.OPENAI, LLMProvider.OPENAI_COMPATIBLE)
    ):
        def anthropic(profile, *, http_client=None, **_services):
            return AnthropicMessagesTransport(
                api_key=str(profile.get("api_key") or ""),
                base_url=str(profile.get("base_url") or "https://api.anthropic.com"),
                http_client=http_client,
            )

        def openai_transport(profile, *, openai_client=None, http_client=None, **_services):
            client = openai_client or OpenAI(
                api_key=str(profile.get("api_key") or ""),
                base_url=str(profile.get("base_url") or "").rstrip("/") or None,
                http_client=http_client,
                max_retries=0,
                default_headers=_profile_request_headers(profile),
            )
            return OpenAIResponsesTransport(
                client,
                base_url=str(profile.get("base_url") or ""),
                model=str(profile.get("model") or ""),
                state_mode=profile.get("responses_state_mode") or "auto",
                storage_disabled=bool(profile.get("responses_store_disabled", False)),
                credential_scope=hashlib.sha256(
                    str(profile.get("api_key") or "").encode("utf-8")
                ).hexdigest(),
                organization=str(profile.get("organization") or ""),
                project=str(profile.get("project") or ""),
                websocket_mode=profile.get("responses_websocket_mode"),
            )

        def compatible(profile, *, openai_client=None, http_client=None, **_services):
            client = openai_client or OpenAI(
                api_key=str(profile.get("api_key") or "local"),
                base_url=str(profile.get("base_url") or "").rstrip("/") or None,
                http_client=http_client,
                max_retries=0,
                default_headers=_profile_request_headers(profile),
            )
            return OpenAICompatibleTransport(client)

        registrations = (
            (LLMProvider.ANTHROPIC.value, anthropic, "anthropic"),
            (LLMProvider.OPENAI.value, openai_transport, "responses"),
            (LLMProvider.OPENAI_COMPATIBLE.value, compatible, "chat-completions"),
        )
        current = provider_registry.snapshot()
        for provider_id, factory, dialect in registrations:
            if provider_id not in current:
                provider_registry.register(
                    provider_id,
                    factory,
                    source="core.fallback",
                    dialect=dialect,
                    capabilities={
                        "responses": dialect == "responses",
                        "chat_completions": dialect == "chat-completions",
                        "anthropic_messages": dialect == "anthropic",
                    },
                )


def build_transport(
    profile: Dict[str, Any],
    *,
    openai_client: Optional[OpenAI] = None,
    http_client: Optional[httpx.Client] = None,
) -> Any:
    """Build the selected provider adapter for one saved profile."""
    _ensure_builtin_provider_registry()
    try:
        from agent_extensions import activate_bundled_provider_extensions

        activate_bundled_provider_extensions(provider_registry)
    except ImportError:
        pass
    provider = resolve_profile_provider(profile)
    return provider_registry.build(
        provider.value,
        profile,
        openai_client=openai_client,
        http_client=http_client,
    )
