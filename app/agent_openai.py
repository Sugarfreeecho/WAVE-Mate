"""
OpenAI Chat Completions 适配层。

负责把 agent_messages 中的消息转为 API 的 messages 列表，并解析 assistant 消息中的
content / tool_calls / reasoning_content。

主模型在思考开时由 harness 传 extra_body.thinking、reasoning_effort，且省略 temperature；
messages_to_openai_params 对每条 assistant 均带上 reasoning_content（可空串），兼容 DeepSeek thinking 多轮。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI
from openai.types.chat import ChatCompletion

from agent_messages import AssistantMessage, SystemMessage, ToolMessage, UserMessage

logger = logging.getLogger(__name__)

OPENAI_MAX_RETRIES = max(1, int(os.getenv("OPENAI_MAX_RETRIES", "3")))
OPENAI_RETRY_BASE_SEC = float(os.getenv("OPENAI_RETRY_BASE_SEC", "1.0"))


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


def _get_nested_attr_or_key(obj: Any, *path: str) -> Any:
    cur = obj
    for k in path:
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            cur = getattr(cur, k, None)
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


@dataclass
class AssistantTurn:
    """单次 chat.completions 中 assistant 消息的已解析结果。"""

    content: str
    tool_calls: Optional[List[Dict[str, Any]]]
    reasoning_content: Optional[str]


def normalize_content_text(content: Any) -> str:
    """将 API 返回的 content（str / dict / 多模态 list）统一成纯文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        for key in ("text", "content", "message", "value", "output", "reasoning_content"):
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
                chunk = ""
                for key in (
                    "text",
                    "content",
                    "reasoning_content",
                    "thinking",
                    "reasoning",
                    "thought",
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


def _extract_reasoning_text(obj: Any) -> Optional[str]:
    """
    兼容不同供应商的思考字段命名：
    - reasoning_content（OpenAI/DeepSeek 常见）
    - reasoning（部分兼容端）
    """
    raw = _get_nested_attr_or_key(obj, "reasoning_content")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raw = _get_nested_attr_or_key(obj, "reasoning")
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        return text or None
    text = str(raw).strip()
    return text or None


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


def _expand_media_paths_in_text(text: str) -> Any:
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
                parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
                media_found += 1
            elif ext in _AUDIO_EXTS:
                fmt = _AUDIO_MIME.get(ext, ext.lstrip("."))
                parts.append({"type": "input_audio", "input_audio": {"data": b64, "format": fmt}})
                media_found += 1
            elif ext in _VIDEO_EXTS:
                # 兼容端差异较大：统一以 image_url/video data URL 透传，失败时模型仍可读文本提示。
                mime = _VIDEO_MIME.get(ext, "video/mp4")
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


def _is_media_input_error(exc: BaseException) -> bool:
    """Check if API error indicates the model does not support image/audio/video input."""
    msg = str(getattr(exc, "message", None) or getattr(exc, "body", None) or exc).lower()
    media_kw = ("image input" in msg or "image_url" in msg or "audio input" in msg or "input_audio" in msg)
    reason_kw = ("not supported" in msg or "not found" in msg or "no endpoint" in msg)
    return media_kw and reason_kw


def _strip_media_from_api_messages(api_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Replace image/audio/video content parts with placeholder text."""
    _MEDIA_PLACEHOLDER = "[该消息包含多媒体内容（图片/音频/视频），但当前模型不支持，已用此文本占位]"
    cleaned: List[Dict[str, Any]] = []
    for msg in api_messages:
        c = msg.get("content")
        if isinstance(c, list):
            has_media = any(isinstance(p, dict) and p.get("type") in ("image_url", "video_url", "input_audio") for p in c)
            text_parts = [p for p in c if isinstance(p, dict) and p.get("type") == "text"]
            if text_parts:
                combined = " ".join(str(p.get("text", "")) for p in text_parts).strip()
                if has_media:
                    combined = _MEDIA_PLACEHOLDER + " " + combined
                cleaned.append({**msg, "content": combined})
            elif has_media:
                cleaned.append({**msg, "content": _MEDIA_PLACEHOLDER})
            else:
                cleaned.append(msg)
        else:
            cleaned.append(msg)
    return cleaned


def _is_glm_model(model: str) -> bool:
    s = str(model or "").strip().lower()
    return s.startswith("glm-")


def messages_to_openai_params(messages: List[Any]) -> List[Dict[str, Any]]:
    """将 UserMessage / AssistantMessage / ToolMessage / SystemMessage 转为 API messages 列表。"""
    api_msgs: List[Dict[str, Any]] = []
    for m in messages:
        if isinstance(m, SystemMessage):
            api_msgs.append({"role": "system", "content": m.content or ""})
        elif isinstance(m, UserMessage):
            if isinstance(m.content, list):
                api_msgs.append({"role": "user", "content": m.content})
            elif isinstance(m.content, str):
                api_msgs.append({"role": "user", "content": _expand_media_paths_in_text(m.content)})
            else:
                api_msgs.append({"role": "user", "content": str(m.content)})
        elif isinstance(m, AssistantMessage):
            item: Dict[str, Any] = {"role": "assistant", "content": m.content or ""}
            if m.tool_calls:
                item["tool_calls"] = format_tool_calls_for_openai_api(m.tool_calls)
            ak = getattr(m, "additional_kwargs", None) or {}
            rc = ak.get("reasoning_content", None) if isinstance(ak, dict) else None
            if rc is not None:
                item["reasoning_content"] = str(rc)
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


def parse_assistant_message(msg: Any) -> AssistantTurn:
    """解析 chat.completions 返回的 assistant message（content、tool_calls、reasoning_content）。"""
    content = _normalize_content_text(getattr(msg, "content", None))
    reasoning = _extract_reasoning_text(msg)

    raw_calls = getattr(msg, "tool_calls", None)
    tool_calls: Optional[List[Dict[str, Any]]] = None
    if raw_calls:
        tool_calls = []
        for tc in raw_calls:
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", "") if fn else ""
            raw_args = getattr(fn, "arguments", "") if fn else "{}"
            tid = getattr(tc, "id", "") or ""
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except json.JSONDecodeError:
                logger.warning("工具参数 JSON 解析失败，使用空对象: %s", raw_args[:200])
                args = {}
            tool_calls.append({"name": name, "args": args, "id": tid})
    return AssistantTurn(content=content, tool_calls=tool_calls, reasoning_content=reasoning)


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
) -> ChatCompletion:
    """封装 client.chat.completions.create，支持 tools、extra_body、reasoning_effort（如 DeepSeek 思考模式）。"""
    api_messages = messages_to_openai_params(messages)
    kwargs: Dict[str, Any] = dict(
        model=model,
        messages=api_messages,
        max_tokens=max_tokens,
        parallel_tool_calls=parallel_tool_calls,
    )
    if not omit_temperature:
        kwargs["temperature"] = temperature
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if extra_body and not _is_glm_model(model):
        kwargs["extra_body"] = extra_body
    if reasoning_effort and not _is_glm_model(model):
        kwargs["reasoning_effort"] = reasoning_effort

    last_exc: Optional[BaseException] = None
    _image_fallback_done = False
    for attempt in range(OPENAI_MAX_RETRIES):
        t0 = time.monotonic()
        try:
            r = client.chat.completions.create(**kwargs)
            dt = time.monotonic() - t0
            u = getattr(r, "usage", None)
            if u:
                ud = extract_usage_dict(u)
                pt = ud.get("prompt_tokens", 0)
                ct = ud.get("completion_tokens", 0)
                prompt_cache_hit_tokens = ud.get("prompt_cache_hit_tokens", 0)
                prompt_cache_miss_tokens = ud.get("prompt_cache_miss_tokens", 0)
                cache_total = prompt_cache_hit_tokens + prompt_cache_miss_tokens
                hit_pct = (
                    prompt_cache_hit_tokens / cache_total * 100 if cache_total > 0 else None
                )
                extra = ""
                if hit_pct is not None:
                    extra = f" hit_rate={hit_pct:.1f}%"
                logger.info(
                    "chat.completions 成功 model=%s 耗时=%.2fs "
                    "prompt_tokens=%s completion_tokens=%s "
                    "prompt_cache_hit_tokens=%s prompt_cache_miss_tokens=%s%s",
                    _masked_model_label(model),
                    dt,
                    pt,
                    ct,
                    prompt_cache_hit_tokens,
                    prompt_cache_miss_tokens,
                    extra,
                )
            else:
                logger.info("chat.completions 成功 model=%s 耗时=%.2fs", _masked_model_label(model), dt)
            return r
        except Exception as e:
            last_exc = e
            dt = time.monotonic() - t0
            if _is_media_input_error(e) and not _image_fallback_done:
                _image_fallback_done = True
                logger.warning(
                    "模型 %s 不支持多媒体输入，去掉图片/音频/视频后重试: %s",
                    _masked_model_label(model),
                    _redact_runtime_log_text(e),
                )
                kwargs["messages"] = _strip_media_from_api_messages(kwargs["messages"])
                continue
            if not _is_retriable_openai_error(e) or attempt >= OPENAI_MAX_RETRIES - 1:
                logger.warning(
                    "chat.completions 失败 model=%s 耗时=%.2fs: %s",
                    _masked_model_label(model),
                    dt,
                    _redact_runtime_log_text(e),
                )
                raise
            delay = OPENAI_RETRY_BASE_SEC * (2**attempt)
            logger.warning(
                "chat.completions 可重试错误 model=%s (%.2fs)：%s；%.1fs 后重试 %s/%s",
                _masked_model_label(model),
                dt,
                _redact_runtime_log_text(e),
                delay,
                attempt + 1,
                OPENAI_MAX_RETRIES,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


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
                tool_acc[idx]["name"] = str(name)
            args = getattr(fn, "arguments", None)
            if args:
                tool_acc[idx]["arguments"] += str(args)


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
                payload["arguments_delta"] = str(args)
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
        raw_args = row.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        except json.JSONDecodeError:
            logger.warning("流式工具参数 JSON 未完整或无效，使用空对象: %s", raw_args[:200])
            args = {}
        if not name and not args and not tid:
            continue
        tool_calls.append({"name": name, "args": args, "id": tid, "index": i})
    return tool_calls or None


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
) -> None:
    """
    在后台线程中跑 chat.completions(stream=True)。
    经 sync_q 投递：("reasoning", str)、("content", str)、("turn", AssistantTurn)；
    失败时 ("err", Exception)；最后一定放入 None。
    """
    try:
        api_messages = messages_to_openai_params(messages)
        kwargs: Dict[str, Any] = dict(
            model=model,
            messages=api_messages,
            max_tokens=max_tokens,
            parallel_tool_calls=parallel_tool_calls,
            stream=True,
        )
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
        _image_fallback_done = False
        for attempt in range(OPENAI_MAX_RETRIES):
            try:
                try:
                    stream = client.chat.completions.create(
                        **kwargs, stream_options={"include_usage": True}
                    )
                except Exception as e1:
                    logger.debug("流式 create 无 stream_options 或端点不支持: %s", _redact_runtime_log_text(e1))
                    stream = client.chat.completions.create(**kwargs)
                break
            except Exception as e:
                if _is_media_input_error(e) and not _image_fallback_done:
                    _image_fallback_done = True
                    logger.warning(
                        "流式: 模型 %s 不支持多媒体输入，去掉图片/音频/视频后重试: %s",
                        _masked_model_label(model),
                        _redact_runtime_log_text(e),
                    )
                    kwargs["messages"] = _strip_media_from_api_messages(kwargs["messages"])
                    sync_q.put(("status", "[提示] 当前模型不支持图片识别，已自动切换为纯文本模式"))
                    continue
                if not _is_retriable_openai_error(e) or attempt >= OPENAI_MAX_RETRIES - 1:
                    raise
                delay = OPENAI_RETRY_BASE_SEC * (2**attempt)
                logger.warning(
                    "流式 chat.completions 重试 %s/%s 等待 %.1fs: %s",
                    attempt + 1,
                    OPENAI_MAX_RETRIES,
                    delay,
                    _redact_runtime_log_text(e),
                )
                time.sleep(delay)
        if stream is None:
            raise RuntimeError("stream 创建失败")
        reasoning_buf = ""
        content_buf = ""
        tool_acc: Dict[int, Dict[str, str]] = {}
        last_usage: Optional[Dict[str, int]] = None
        finish_meta: Dict[str, Any] = {"finish_reason": None, "stop_reason": None}
        for chunk in stream:
            uo = getattr(chunk, "usage", None)
            if uo is not None:
                last_usage = extract_usage_dict(uo)
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
            rc = _extract_reasoning_text(delta)
            if rc:
                piece = rc if isinstance(rc, str) else str(rc)
                reasoning_buf += piece
                sync_q.put(("reasoning", piece))
            ct = getattr(delta, "content", None)
            if ct:
                piece = ct if isinstance(ct, str) else str(ct)
                content_buf += piece
                sync_q.put(("content", piece))
            delta_tool_calls = getattr(delta, "tool_calls", None)
            for payload in _tool_call_delta_payloads(delta_tool_calls):
                sync_q.put(("tool_call_delta", payload))
            _accumulate_tool_call_delta(tool_acc, delta_tool_calls)
        tool_calls_list = _tool_acc_to_parsed_list(tool_acc)
        reasoning_final = reasoning_buf.strip() or None
        turn = AssistantTurn(
            content=content_buf or "",
            tool_calls=tool_calls_list,
            reasoning_content=reasoning_final,
        )
        if last_usage:
            sync_q.put(("usage", last_usage))
            logger.info(
                "chat.completions stream usage model=%s prompt_tokens=%s completion_tokens=%s "
                "prompt_cache_hit_tokens=%s prompt_cache_miss_tokens=%s",
                _masked_model_label(model),
                last_usage.get("prompt_tokens", 0),
                last_usage.get("completion_tokens", 0),
                last_usage.get("prompt_cache_hit_tokens", 0),
                last_usage.get("prompt_cache_miss_tokens", 0),
            )
        sync_q.put(("finish", finish_meta))
        sync_q.put(("turn", turn))
    except Exception as e:
        logger.warning("chat.completions 流式调用异常: %s", _redact_runtime_log_text(e))
        sync_q.put(("err", e))
    finally:
        sync_q.put(None)


def single_turn_text_completion(
    client: OpenAI,
    model: str,
    user_text: str,
    *,
    temperature: float,
    max_tokens: int,
) -> Tuple[str, Optional[Dict[str, int]]]:
    """单条 user 消息的非流式补全（会话标题、压缩摘要等）。返回 (文本, usage 或 None)。"""
    last_exc: Optional[BaseException] = None
    r = None
    for attempt in range(OPENAI_MAX_RETRIES):
        try:
            r = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": user_text}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            break
        except Exception as e:
            last_exc = e
            if not _is_retriable_openai_error(e) or attempt >= OPENAI_MAX_RETRIES - 1:
                raise
            time.sleep(OPENAI_RETRY_BASE_SEC * (2**attempt))
    if r is None:
        assert last_exc is not None
        raise last_exc
    msg = r.choices[0].message
    text = _normalize_content_text(getattr(msg, "content", ""))
    usage: Optional[Dict[str, int]] = None
    u = getattr(r, "usage", None)
    if u is not None:
        usage = extract_usage_dict(u)
    return (text, usage)
