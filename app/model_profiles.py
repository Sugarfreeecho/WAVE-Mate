from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx

from llm import (
    LLMProvider,
    canonical_llm_type,
    normalize_provider,
    normalize_responses_state_mode,
    resolve_profile_provider,
    resolve_provider,
)


DEFAULT_UNKNOWN_MODEL_CONTEXT_WINDOW = 128_000
DEFAULT_UNKNOWN_CONTEXT_WINDOW = 119_808
DEFAULT_UNKNOWN_OUTPUT_TOKENS = 8_192
MODEL_LIMITS_TABLE_PATH = Path(__file__).resolve().parent / "data" / "models_table.md"
CONTEXT_PROBE_TOKEN_COUNT = 3_000_000
CONTEXT_PROBE_TIMEOUT = 8.0
LEGACY_ENV_IMPORT_MARKER = "imported_from_legacy_env"
MULTIMODAL_MODES = frozenset({"auto", "enabled", "disabled"})
KNOWN_INPUT_MODALITIES = ("text", "image", "audio", "video", "file")
MEDIA_INPUT_MODALITIES = frozenset({"image", "audio", "video", "file"})
LOW_COST_MAX_INPUT_USD_PER_M = 1.0
LOW_COST_MAX_OUTPUT_USD_PER_M = 5.0
HIGH_INTELLIGENCE_MIN_SCORE = 35.0
CODING_MIN_SCORE = 20.0
AGENTIC_MIN_SCORE = 15.0
LONG_CONTEXT_MIN_TOKENS = 200_000

CONTEXT_LIMIT_FIELDS = (
    "context_window",
    "context_length",
    "max_context_length",
    "max_model_len",
    "max_sequence_length",
    "input_token_limit",
)
OUTPUT_LIMIT_FIELDS = (
    "max_output_tokens",
    "output_token_limit",
    "max_completion_tokens",
)
_TOKEN_COUNT_PATTERN = r"([0-9][0-9,._ ]*(?:\.[0-9]+)?\s*[kKmM]?)"
CONTEXT_LIMIT_ERROR_PATTERNS = (
    re.compile(r"maximum context length is\s*" + _TOKEN_COUNT_PATTERN + r"\s*tokens?", re.I),
    re.compile(r"max(?:imum)?(?: model)? context(?: length| window)?(?: is|:)?\s*" + _TOKEN_COUNT_PATTERN + r"\s*tokens?", re.I),
    re.compile(r"context(?: length| window)? limit(?: is|:)?\s*" + _TOKEN_COUNT_PATTERN + r"\s*tokens?", re.I),
)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        n = int(value)
        return n if n > 0 else default
    except (TypeError, ValueError):
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return bool(default)


def responses_store_disabled(profile: Optional[dict]) -> bool:
    """Read the privacy flag, migrating legacy stateless profiles in place."""
    item = profile if isinstance(profile, dict) else {}
    if "responses_store_disabled" in item:
        return _safe_bool(item.get("responses_store_disabled"))
    try:
        return normalize_responses_state_mode(
            item.get("responses_state_mode")
        ).value == "stateless"
    except ValueError:
        return False


def _parse_token_count(value: Any) -> int:
    text = str(value or "").strip().lower().replace(",", "").replace("_", "").replace(" ", "")
    if not text:
        return 0
    multiplier = 1
    if text.endswith("k"):
        multiplier = 1_000
        text = text[:-1]
    elif text.endswith("m"):
        multiplier = 1_000_000
        text = text[:-1]
    try:
        return int(float(text) * multiplier)
    except (TypeError, ValueError):
        return 0


def _clean_reasoning_effort(value: Any) -> str:
    # UI provides max/high/medium/low, but keep custom provider values possible.
    return str(value or "").strip().lower()


def _clean_thinking_mode(value: Any) -> str:
    # UI provides enabled/disabled, but keep custom provider values possible.
    return str(value or "").strip().lower()


def _clean_thinking_format(value: Any) -> str:
    # deepseek / reasoning / think_blocks / none；未配置留空交给运行时按模型名推断。
    return str(value or "").strip().lower()


def recommended_model_windows(model_context_window: Any) -> dict[str, int]:
    max_context = _safe_int(model_context_window, 0)
    if max_context <= 0:
        return {
            "model_context_window": DEFAULT_UNKNOWN_MODEL_CONTEXT_WINDOW,
            "max_output_tokens": DEFAULT_UNKNOWN_OUTPUT_TOKENS,
            "context_window": DEFAULT_UNKNOWN_CONTEXT_WINDOW,
        }
    if max_context < 130_000:
        output = DEFAULT_UNKNOWN_OUTPUT_TOKENS
    else:
        output = min(
            max_context // 10,
            30_000 if max_context < 300_000 else 50_000,
        )
    output = max(1, min(output, max(1, max_context - 1)))
    theoretical = max(1, max_context - output)
    cap = 128_000 if max_context < 300_000 else 512_000
    return {
        "model_context_window": max_context,
        "max_output_tokens": output,
        "context_window": min(theoretical, cap),
    }


def _safe_score(value: Any) -> Optional[float]:
    try:
        score = float(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return score if score >= 0 else None


def _safe_price(value: Any) -> Optional[float]:
    text = str(value or "").strip().replace("$", "").replace(",", "")
    try:
        price = float(text)
    except (TypeError, ValueError):
        return None
    return price if price >= 0 else None


def _normalized_model_match_key(value: Any) -> str:
    return re.sub(r"[/\-_\s]+", "", str(value or "").strip().lower())


def _model_candidate_sort_key(record: dict[str, Any]) -> tuple[Any, ...]:
    scores = [
        score
        for score in (
            record.get("intel_score"),
            record.get("coding_score"),
            record.get("agentic_score"),
        )
        if isinstance(score, (int, float))
    ]
    created = str(record.get("created") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", created):
        created = ""
    return (
        created,
        sum(scores),
        len(scores),
        float(record.get("intel_score") or -1),
        float(record.get("coding_score") or -1),
        float(record.get("agentic_score") or -1),
        _safe_int(record.get("context_window"), 0),
        str(record.get("model_id") or ""),
    )


def _select_latest_model_record(records: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    return max(records, key=_model_candidate_sort_key) if records else None


@lru_cache(maxsize=8)
def _read_model_table(
    path_text: str,
    modified_ns: int,
    file_size: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[dict[str, Any], ...]], tuple[dict[str, Any], ...]]:
    del modified_ns, file_size  # cache-key inputs; content is read only on file changes
    exact: dict[str, dict[str, Any]] = {}
    suffix_candidates: dict[str, list[dict[str, Any]]] = {}
    records: list[dict[str, Any]] = []
    try:
        lines = Path(path_text).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return exact, {}, ()
    for line in lines:
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4 or cells[1].lower() == "model id":
            continue
        model_id = cells[1].strip()
        context_window = _parse_token_count(cells[3])
        if not model_id or context_window <= 0:
            continue
        normalized_id = model_id.lower()
        suffix = normalized_id.rsplit("/", 1)[-1]
        record: dict[str, Any] = {
            "provider": cells[0],
            "model_id": model_id,
            "name": cells[2],
            "context_window": context_window,
            "modality": cells[4] if len(cells) > 4 else "",
            "input_modalities": tuple(
                part.strip().lower()
                for part in (cells[5] if len(cells) > 5 else "").split(",")
                if part.strip()
            ),
            "output_modalities": tuple(
                part.strip().lower()
                for part in (cells[6] if len(cells) > 6 else "").split(",")
                if part.strip()
            ),
            "input_price_per_m": _safe_price(cells[7] if len(cells) > 7 else None),
            "output_price_per_m": _safe_price(cells[8] if len(cells) > 8 else None),
            "intel_score": _safe_score(cells[9] if len(cells) > 9 else None),
            "coding_score": _safe_score(cells[10] if len(cells) > 10 else None),
            "agentic_score": _safe_score(cells[11] if len(cells) > 11 else None),
            "reasoning": cells[12].strip() if len(cells) > 12 else "",
            "created": cells[13].strip() if len(cells) > 13 else "",
            "normalized_id": normalized_id,
            "suffix": suffix,
            "match_key": _normalized_model_match_key(normalized_id),
            "suffix_match_key": _normalized_model_match_key(suffix),
        }
        exact[normalized_id] = record
        suffix_candidates.setdefault(suffix, []).append(record)
        records.append(record)
    suffixes = {
        suffix: tuple(candidates)
        for suffix, candidates in suffix_candidates.items()
    }
    return exact, suffixes, tuple(records)


def _model_table_record_for_model(model_id: str) -> Optional[dict[str, Any]]:
    path = Path(MODEL_LIMITS_TABLE_PATH)
    try:
        stat = path.stat()
    except OSError:
        return None
    exact, suffixes, records = _read_model_table(
        str(path.resolve()),
        int(stat.st_mtime_ns),
        int(stat.st_size),
    )
    normalized_id = str(model_id or "").strip().lower()
    if not normalized_id:
        return None
    matched = exact.get(normalized_id)
    if matched is not None:
        return matched
    suffix = normalized_id.rsplit("/", 1)[-1]
    suffix_matches = list(suffixes.get(suffix, ()))
    if suffix_matches:
        return _select_latest_model_record(suffix_matches)

    match_keys = {
        _normalized_model_match_key(normalized_id),
        _normalized_model_match_key(suffix),
    }
    match_keys.discard("")
    exact_normalized = [
        record
        for record in records
        if record.get("match_key") in match_keys or record.get("suffix_match_key") in match_keys
    ]
    if exact_normalized:
        return _select_latest_model_record(exact_normalized)

    fuzzy_matches: list[dict[str, Any]] = []
    for match_key in match_keys:
        if len(match_key) < 6:
            continue
        for record in records:
            candidate = str(record.get("suffix_match_key") or "")
            if len(candidate) < 6:
                continue
            if candidate.startswith(match_key) or match_key.startswith(candidate):
                fuzzy_matches.append(record)
    return _select_latest_model_record(fuzzy_matches)


def model_table_metadata_for_model(model_id: str) -> Optional[dict[str, Any]]:
    record = _model_table_record_for_model(model_id)
    if record is None:
        return None
    return {
        key: value
        for key, value in record.items()
        if key not in {"normalized_id", "suffix", "match_key", "suffix_match_key"}
    }


def _model_table_context_for_model(model_id: str) -> int:
    record = _model_table_record_for_model(model_id)
    return _safe_int((record or {}).get("context_window"), 0)


def is_huawei_api_domain(base_url: str) -> bool:
    normalized = _normalize_base_url(base_url)
    if not normalized:
        return False
    parsed = urlsplit(normalized if "://" in normalized else "//" + normalized)
    hostname = str(parsed.hostname or "").strip().lower()
    try:
        from trusted_domains import is_huawei_host

        return is_huawei_host(hostname)
    except Exception:
        return any("huawei" in label for label in hostname.split(".") if label)


def infer_model_limits(
    model_id: str,
    raw: Optional[dict] = None,
    base_url: str = "",
) -> dict[str, Any]:
    raw = raw or {}
    candidates = [raw.get(key) for key in CONTEXT_LIMIT_FIELDS]
    raw_ctx = next((_safe_int(v) for v in candidates if _safe_int(v) > 0), 0)
    ctx = raw_ctx
    ctx_source = "api" if raw_ctx > 0 else ""
    output_candidates = [raw.get(key) for key in OUTPUT_LIMIT_FIELDS]
    raw_out = next((_safe_int(v) for v in output_candidates if _safe_int(v) > 0), 0)
    out = raw_out
    out_source = "api" if raw_out > 0 else ""
    if ctx <= 0 and is_huawei_api_domain(base_url):
        ctx = DEFAULT_UNKNOWN_MODEL_CONTEXT_WINDOW
        ctx_source = "huawei"
    if ctx <= 0:
        table_context = _model_table_context_for_model(model_id)
        if table_context > 0:
            ctx = table_context
            ctx_source = "table"
    if raw_ctx <= 0 and ctx <= 0:
        ctx = DEFAULT_UNKNOWN_MODEL_CONTEXT_WINDOW
        ctx_source = "default"
    if raw_out <= 0:
        out = recommended_model_windows(ctx)["max_output_tokens"]
        out_source = "recommended"
    return {
        "context_window": ctx,
        "max_output_tokens": out,
        "context_source": ctx_source or "default",
        "output_source": out_source or "default",
    }


_MODALITY_LABELS = {
    "text": "文本",
    "image": "图片",
    "audio": "音频",
    "video": "视频",
    "file": "文件",
}
_MODALITY_LABELS_EN = {
    "text": "text",
    "image": "image",
    "audio": "audio",
    "video": "video",
    "file": "file",
}
def _table_supports_multimodal_input(record: Optional[dict[str, Any]]) -> bool:
    if not record:
        return False
    return any(
        modality != "text"
        for modality in (record.get("input_modalities") or ())
    )


def _table_capability_description(
    record: dict[str, Any],
    tags: list[str],
    context_window: int = 0,
    language: str = "zh-CN",
) -> str:
    english = language == "en"
    task_labels: list[str] = []
    for tag, zh_label, en_label in (
        ("low_cost_concurrency", "低成本/多并发", "low-cost/high-concurrency"),
        ("hard_reasoning", "高难度", "complex tasks"),
        ("research", "调查调研", "research"),
        ("coding", "代码", "coding"),
        ("agent", "Agent", "agent workflows"),
    ):
        if tag in tags:
            task_labels.append(en_label if english else zh_label)
    if "long_context" in tags:
        configured = f"{_safe_int(context_window, 0):,} tokens"
        task_labels.append(
            f"long-context ({configured} configured)"
            if english
            else f"长上下文（实际配置 {configured}）"
        )

    parts: list[str] = []
    if task_labels:
        parts.append(
            ("Best for: " if english else "适合：")
            + ((", ".join(task_labels)) if english else "、".join(task_labels))
        )

    modality_order = {name: index for index, name in enumerate(_MODALITY_LABELS)}
    multimodal_inputs = sorted(
        (
            modality
            for modality in (record.get("input_modalities") or ())
            if modality != "text"
        ),
        key=lambda modality: (modality_order.get(str(modality), 999), str(modality)),
    )
    if multimodal_inputs:
        labels = [
            (_MODALITY_LABELS_EN if english else _MODALITY_LABELS).get(
                str(modality), str(modality)
            )
            for modality in multimodal_inputs
        ]
        parts.append(
            ("Multimodal input: " if english else "多模态输入：")
            + ((", ".join(labels)) if english else "、".join(labels))
        )
    else:
        parts.append(
            "Multimodal input: not supported (text only)"
            if english
            else "多模态输入：不支持（仅文本）"
        )
    return ("; " if english else "；").join(parts)


def infer_model_task_capabilities(
    model_id: str,
    profile_name: str = "",
    context_window: int = 0,
) -> dict[str, Any]:
    """Build capabilities from the bundled models table without name guessing."""
    table_record = (
        _model_table_record_for_model(model_id)
        or _model_table_record_for_model(profile_name)
    )
    if table_record is None:
        return {
            "capability_tags": [],
            "capability_description": "",
            "capability_description_en": "",
            "capability_source": "unavailable",
        }
    effective_context_window = _safe_int(context_window, 0)
    tags: list[str] = []
    input_price = table_record.get("input_price_per_m")
    output_price = table_record.get("output_price_per_m")
    if (
        isinstance(input_price, (int, float))
        and isinstance(output_price, (int, float))
        and input_price <= LOW_COST_MAX_INPUT_USD_PER_M
        and output_price <= LOW_COST_MAX_OUTPUT_USD_PER_M
    ):
        tags.append("low_cost_concurrency")
    if _table_supports_multimodal_input(table_record):
        tags.append("multimodal_candidate")
    if float(table_record.get("intel_score") or 0) >= HIGH_INTELLIGENCE_MIN_SCORE:
        tags.append("hard_reasoning")
        tags.append("research")
    if float(table_record.get("coding_score") or 0) >= CODING_MIN_SCORE:
        tags.append("coding")
    if float(table_record.get("agentic_score") or 0) >= AGENTIC_MIN_SCORE:
        tags.append("agent")

    if effective_context_window >= LONG_CONTEXT_MIN_TOKENS:
        tags.append("long_context")
    return {
        "capability_tags": tags,
        "capability_description": _table_capability_description(
            table_record, tags, effective_context_window
        ),
        "capability_description_en": _table_capability_description(
            table_record, tags, effective_context_window, "en"
        ),
        "capability_source": "automatic:models-table",
        "matched_model_id": str(table_record.get("model_id") or ""),
        "model_scores": {
            "intel": table_record.get("intel_score"),
            "coding": table_record.get("coding_score"),
            "agentic": table_record.get("agentic_score"),
        },
        "model_prices": {
            "input_per_m": input_price,
            "output_per_m": output_price,
        },
        "input_modalities": list(table_record.get("input_modalities") or ()),
        "output_modalities": list(table_record.get("output_modalities") or ()),
    }


def normalize_multimodal_mode(value: Any, default: str = "auto") -> str:
    mode = str(value or "").strip().lower()
    if mode in MULTIMODAL_MODES:
        return mode
    return default if default in MULTIMODAL_MODES else "auto"


def normalize_input_modalities(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_values = re.split(r"[,\s]+", value)
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw_values = list(value)
    else:
        raw_values = []
    selected = {
        str(item or "").strip().lower()
        for item in raw_values
        if str(item or "").strip().lower() in KNOWN_INPUT_MODALITIES
    }
    return [modality for modality in KNOWN_INPUT_MODALITIES if modality in selected]


def normalize_failed_modalities(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, dict[str, str]] = {}
    for raw_modality, raw_detail in value.items():
        modality = str(raw_modality or "").strip().lower()
        if modality not in MEDIA_INPUT_MODALITIES:
            continue
        detail = raw_detail if isinstance(raw_detail, dict) else {}
        normalized[modality] = {
            "reason": str(detail.get("reason") or "provider_rejected_media_input"),
            "failed_at": str(detail.get("failed_at") or ""),
        }
    return normalized


def infer_multimodal_input(model_id: str, profile_name: str = "") -> bool:
    inferred = infer_model_task_capabilities(model_id, profile_name)
    return "multimodal_candidate" in set(inferred.get("capability_tags") or ())


def profile_input_modalities(profile: object) -> list[str]:
    if not isinstance(profile, dict):
        return ["text"]
    mode = normalize_multimodal_mode(profile.get("multimodal_mode"))
    if mode == "disabled":
        return ["text"]
    configured = normalize_input_modalities(profile.get("input_modalities"))
    if mode == "enabled":
        modalities = configured or normalize_input_modalities(
            (_model_table_record_for_model(str(profile.get("model") or "")) or {}).get(
                "input_modalities"
            )
        )
        if not any(item in MEDIA_INPUT_MODALITIES for item in modalities):
            modalities = list(KNOWN_INPUT_MODALITIES)
    else:
        record = (
            _model_table_record_for_model(str(profile.get("model") or ""))
            or _model_table_record_for_model(str(profile.get("name") or ""))
        )
        modalities = normalize_input_modalities((record or {}).get("input_modalities"))
        if not modalities:
            modalities = ["text"]
    failed = set(normalize_failed_modalities(profile.get("failed_modalities")))
    effective = [item for item in modalities if item not in failed]
    if "text" not in effective:
        effective.insert(0, "text")
    return [item for item in KNOWN_INPUT_MODALITIES if item in set(effective)]


def profile_supports_modalities(profile: object, required: Any) -> bool:
    required_set = set(normalize_input_modalities(required)) - {"text"}
    return required_set.issubset(set(profile_input_modalities(profile)))


def profile_multimodal_input(profile: object) -> bool:
    return any(
        modality in MEDIA_INPUT_MODALITIES
        for modality in profile_input_modalities(profile)
    )


def _normalize_base_url(base_url: str) -> str:
    url = str(base_url or "").strip().rstrip("/")
    if not url:
        return ""
    if url.endswith("/models"):
        return url[: -len("/models")]
    return url


def models_url_for_base(base_url: str) -> str:
    base = _normalize_base_url(base_url)
    if not base:
        return ""
    return base + "/models"


def chat_completions_url_for_base(base_url: str) -> str:
    base = _normalize_base_url(base_url)
    if not base:
        return ""
    return base + "/chat/completions"


def responses_url_for_base(base_url: str) -> str:
    base = _normalize_base_url(base_url)
    if not base:
        return ""
    return base if base.endswith("/responses") else base + "/responses"


def anthropic_messages_url_for_base(base_url: str) -> str:
    base = _normalize_base_url(base_url)
    if not base:
        return ""
    if base.endswith("/messages"):
        return base
    if base.endswith("/v1"):
        return base + "/messages"
    return base + "/v1/messages"


_WIRE_PROTOCOL_TIMEOUT = 12.0


def _wire_probe_payload(provider_value: str, model_id: str) -> dict:
    if provider_value == "openai-responses":
        return {
            "model": model_id,
            "input": [{"role": "user", "content": "ping"}],
            "max_output_tokens": 1,
            "stream": False,
        }
    if provider_value == "anthropic":
        return {
            "model": model_id,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }
    return {
        "model": model_id,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }


def _wire_probe_headers(provider_value: str, api_key: str) -> dict:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    key = str(api_key or "").strip()
    if not key:
        return headers
    if provider_value == "anthropic":
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["Authorization"] = "Bearer " + key
    return headers


def _wire_route_missing(provider_value: str, status: int, body: Any) -> bool:
    """Whether the endpoint says this wire route does not exist at all."""
    if status in (404, 405):
        return True
    if isinstance(body, (dict, list)):
        text = json.dumps(body, ensure_ascii=False)
    else:
        text = str(body or "")
    lowered = text.lower()
    if status == 400 and ("not found" in lowered or "unknown route" in lowered):
        return True
    return False


def detect_wire_protocol(
    base_url: str,
    api_key: str,
    model_id: str,
    timeout: float = _WIRE_PROTOCOL_TIMEOUT,
) -> Optional[str]:
    """Probe which wire protocol an endpoint actually serves for this model.

    Sends a minimal request over each candidate route and judges by the
    response: 2xx means supported; 4xx that argues about request fields
    (auth, context, unknown parameter) means the route exists and speaks
    that protocol; 404/405-style answers mean the route is absent.
    Returns one of ``openai-responses`` / ``openai-compatible`` / ``anthropic``
    or None when nothing could be determined (offline, quota walls, etc).
    """
    base = _normalize_base_url(base_url)
    mid = str(model_id or "").strip()
    if not base or not mid:
        return None
    candidates: list[tuple[str, str]] = [
        ("openai-compatible", chat_completions_url_for_base(base)),
        ("openai-responses", responses_url_for_base(base)),
    ]
    if mid.lower().startswith("claude"):
        candidates.insert(0, ("anthropic", anthropic_messages_url_for_base(base)))
    supported: list[str] = []
    with httpx.Client(timeout=timeout) as client:
        for provider_value, url in candidates:
            if not url:
                continue
            try:
                response = client.post(
                    url,
                    headers=_wire_probe_headers(provider_value, api_key),
                    json=_wire_probe_payload(provider_value, mid),
                )
            except Exception:
                continue
            if 200 <= response.status_code < 300:
                supported.append(provider_value)
                continue
            body: Any = None
            try:
                body = response.json()
            except Exception:
                body = response.text[:500]
            if _wire_route_missing(provider_value, response.status_code, body):
                continue
            if 400 <= response.status_code < 500:
                # The route answered with a request-level complaint (auth,
                # quota, unknown field, context window) so it exists and
                # parses this wire protocol.
                supported.append(provider_value)
    if len(supported) == 1:
        return supported[0]
    if len(supported) > 1:
        # Chat Completions is the most widely shared dialect across
        # aggregators; prefer it when a gateway mirrors multiple routes.
        if "openai-compatible" in supported:
            return "openai-compatible"
        return supported[0]
    return None


def extract_context_window_from_error(error_body: Any) -> int:
    if isinstance(error_body, (dict, list)):
        text = json.dumps(error_body, ensure_ascii=False)
    else:
        text = str(error_body or "")
    for pattern in CONTEXT_LIMIT_ERROR_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        tokens = _parse_token_count(match.group(1))
        if tokens > 0:
            return tokens
    return 0


def probe_context_window_from_error(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    model_id: str,
    timeout: float = CONTEXT_PROBE_TIMEOUT,
    llm_type: str = "openai-compatible",
) -> int:
    provider = resolve_provider(llm_type, base_url, model_id)
    if provider is LLMProvider.OPENAI:
        url = responses_url_for_base(base_url)
    elif provider is LLMProvider.ANTHROPIC:
        url = anthropic_messages_url_for_base(base_url)
    else:
        url = chat_completions_url_for_base(base_url)
    if not url or not str(model_id or "").strip():
        return 0
    probe_text = "x " * CONTEXT_PROBE_TOKEN_COUNT
    if provider is LLMProvider.OPENAI:
        payload = {
            "model": model_id,
            "input": [{"role": "user", "content": probe_text}],
            "max_output_tokens": 1,
            "stream": False,
        }
    else:
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": probe_text}],
            "max_tokens": 1,
            "stream": False,
        }
    try:
        resp = client.post(url, headers=headers, json=payload, timeout=timeout)
    except httpx.HTTPError:
        return 0
    if resp.status_code != 400:
        return 0
    bodies: list[Any] = [resp.text]
    try:
        bodies.append(resp.json())
    except ValueError:
        pass
    for body in bodies:
        context_window = extract_context_window_from_error(body)
        if context_window > 0:
            return context_window
    return 0


def probe_model_context(
    base_url: str,
    api_key: str,
    model_id: str,
    fallback: Optional[dict] = None,
    timeout: float = 20.0,
    llm_type: Optional[str] = None,
) -> Dict[str, Any]:
    base = _normalize_base_url(base_url)
    if not base:
        raise ValueError("missing base_url")
    mid = str(model_id or "").strip()
    if not mid:
        raise ValueError("missing model")
    fallback = fallback if isinstance(fallback, dict) else {}
    requested_provider = str(
        llm_type or fallback.get("llm_type") or "auto"
    ).strip().lower() or "auto"
    provider = resolve_provider(requested_provider, base, mid)
    limits = infer_model_limits(mid, fallback, base_url=base)
    headers = {}
    if str(api_key or "").strip():
        if provider is LLMProvider.ANTHROPIC:
            headers["x-api-key"] = str(api_key).strip()
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = "Bearer " + str(api_key).strip()
    probed_context = 0
    if headers:
        with httpx.Client(timeout=timeout) as client:
            probed_context = probe_context_window_from_error(
                client,
                base,
                headers,
                mid,
                llm_type=provider.value,
            )
    if probed_context > 0:
        limits["context_window"] = probed_context
        limits["context_source"] = "probe"
        if int(limits["max_output_tokens"]) >= probed_context:
            limits["max_output_tokens"] = min(DEFAULT_UNKNOWN_OUTPUT_TOKENS, max(1, probed_context - 1))
            limits["output_source"] = "default"
    return {
        "id": mid,
        "context_window": limits["context_window"],
        "model_context_window": limits["context_window"],
        "max_output_tokens": limits["max_output_tokens"],
        "limit_source": limits["context_source"],
        "context_window_source": limits["context_source"],
        "output_limit_source": limits["output_source"],
        "probe_attempted": bool(headers),
        "probe_succeeded": probed_context > 0,
    }


def profile_store_path(project_root: Path) -> Path:
    return Path(project_root).resolve() / "model_profiles.json"


def legacy_profile_store_path(project_root: Path) -> Path:
    return Path(project_root).resolve() / "app" / "model_profiles.json"


def load_store(project_root: Path) -> dict:
    path = profile_store_path(project_root)
    if not path.is_file():
        legacy_path = legacy_profile_store_path(project_root)
        if legacy_path.is_file():
            path = legacy_path
    if not path.is_file():
        return {"profiles": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"profiles": []}
    if not isinstance(data, dict):
        return {"profiles": []}
    profiles = data.get("profiles")
    if not isinstance(profiles, list):
        data["profiles"] = []
    return {"profiles": data["profiles"]}


def save_store(project_root: Path, data: dict) -> None:
    path = profile_store_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "profiles": [p for p in data.get("profiles", []) if isinstance(p, dict)],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def public_profile(profile: dict) -> dict:
    out = dict(profile)
    effective_provider = resolve_profile_provider(profile)
    out["llm_type"] = canonical_llm_type(effective_provider)
    storage_disabled = responses_store_disabled(profile)
    out["responses_store_disabled"] = storage_disabled
    # Kept for one compatibility cycle; callers must not use it to force a
    # stateful chain.  The only durable user choice is the privacy flag above.
    out["responses_state_mode"] = "stateless" if storage_disabled else "auto"
    out["enabled"] = profile.get("enabled") is not False
    out["api_key_set"] = bool(str(profile.get("api_key") or "").strip())
    out["usable"] = is_usable_profile(profile)
    inferred = infer_model_task_capabilities(
        str(profile.get("model") or ""),
        str(profile.get("name") or ""),
        _safe_int(profile.get("context_window"), 0),
    )
    out.update(inferred)
    custom_description = str(profile.get("capability_description") or "").strip()
    if custom_description:
        out["capability_description"] = custom_description
        out["capability_description_en"] = custom_description
        out["capability_source"] = "manual"
    multimodal_mode = normalize_multimodal_mode(profile.get("multimodal_mode"))
    effective_modalities = profile_input_modalities(profile)
    multimodal_input = profile_multimodal_input(profile)
    out["multimodal_mode"] = multimodal_mode
    out["multimodal_input"] = multimodal_input
    out["table_input_modalities"] = list(inferred.get("input_modalities") or [])
    out["configured_input_modalities"] = normalize_input_modalities(
        profile.get("input_modalities")
    )
    out["effective_input_modalities"] = effective_modalities
    out["failed_modalities"] = normalize_failed_modalities(
        profile.get("failed_modalities")
    )
    if out["failed_modalities"]:
        out["multimodal_source"] = (
            "partial_failure" if multimodal_input else "failure"
        )
    elif multimodal_mode == "auto":
        out["multimodal_source"] = (
            "automatic:models-table"
            if inferred.get("capability_source") == "automatic:models-table"
            else "unavailable"
        )
    else:
        out["multimodal_source"] = str(profile.get("multimodal_source") or "manual")
    if out["failed_modalities"] and out.get("capability_source") != "manual":
        failed_order = [
            modality
            for modality in KNOWN_INPUT_MODALITIES
            if modality in out["failed_modalities"]
        ]
        if failed_order:
            out["capability_description"] = (
                str(out.get("capability_description") or "")
                + "；已停用："
                + "、".join(_MODALITY_LABELS.get(item, item) for item in failed_order)
            ).strip("；")
            out["capability_description_en"] = (
                str(out.get("capability_description_en") or "")
                + "; Disabled: "
                + ", ".join(_MODALITY_LABELS_EN.get(item, item) for item in failed_order)
            ).strip("; ")
    tags = list(out.get("capability_tags") or [])
    if multimodal_input:
        if "multimodal" not in tags:
            tags.append("multimodal")
    else:
        tags = [tag for tag in tags if tag != "multimodal_candidate"]
    out["capability_tags"] = tags
    return out


def is_usable_profile(profile: object) -> bool:
    """Return whether a saved profile has everything needed for execution."""
    if not isinstance(profile, dict):
        return False
    if profile.get("enabled") is False:
        return False
    if not str(profile.get("id") or "").strip():
        return False
    if not str(profile.get("model") or "").strip():
        return False
    if not str(profile.get("base_url") or "").strip():
        return False
    try:
        provider = resolve_profile_provider(profile)
    except ValueError:
        return False
    api_key = str(profile.get("api_key") or "").strip()
    if provider is not LLMProvider.OPENAI_COMPATIBLE and (
        not api_key or "YOUR_API_KEY" in api_key.upper()
    ):
        return False
    if _safe_int(profile.get("context_window"), 0) <= 0:
        return False
    if _safe_int(profile.get("max_output_tokens"), 0) <= 0:
        return False
    return True


def _legacy_env_profile_payload(env: dict[str, Any]) -> Optional[dict]:
    """Translate a complete legacy .env model configuration into a profile payload."""
    llm_type = str(env.get("EXECUTOR_LLM_TYPE") or "auto").strip().lower() or "auto"
    # This importer only handles the legacy .env schema, where `openai`
    # historically meant "use the OpenAI SDK against any compatible endpoint".
    # Preserve that behavior while allowing the profile UI's explicit `openai`
    # value to retain its new Responses API meaning.
    if llm_type in {"openai", "@ai-sdk/openai"}:
        llm_type = "auto"
    if llm_type == "local":
        model = str(env.get("LOCAL_LLM") or env.get("EXECUTOR_LLM") or "").strip()
        local_host = str(env.get("LOCAL_LLM_HOST") or "").strip()
        base_url = local_host or str(env.get("OPENAI_BASE_URL") or "").strip()
        if local_host and base_url.rstrip("/").lower().endswith("/v1") is False:
            base_url = base_url.rstrip("/") + "/v1"
    else:
        model = str(env.get("EXECUTOR_LLM") or "").strip()
        base_url = str(env.get("OPENAI_BASE_URL") or "").strip()
    api_key = str(env.get("OPENAI_API_KEY") or "").strip()
    if not model or not base_url:
        return None
    provider = resolve_provider(llm_type, base_url, model)
    if provider is not LLMProvider.OPENAI_COMPATIBLE and (
        not api_key or "YOUR_API_KEY" in api_key.upper()
    ):
        return None

    limits = infer_model_limits(model, base_url=base_url)
    recommended = recommended_model_windows(limits["context_window"])
    context_window = _safe_int(env.get("CONTEXT_WINDOW"), recommended["context_window"])
    max_output_tokens = _safe_int(env.get("MAX_OUTPUT_TOKENS"), recommended["max_output_tokens"])
    if context_window <= 0 or max_output_tokens <= 0:
        return None
    inferred_model_window = _safe_int(limits.get("context_window"), 0)
    model_context_window = max(inferred_model_window, context_window + max_output_tokens)
    return {
        "name": model,
        "model": model,
        "llm_type": llm_type,
        "base_url": base_url,
        "api_key": api_key,
        "context_window": context_window,
        "max_output_tokens": max_output_tokens,
        "model_context_window": model_context_window,
        "thinking_mode": str(env.get("LLM_THINKING_MODE") or "").strip(),
        "reasoning_effort": str(env.get("LLM_REASONING_EFFORT") or "").strip(),
        "temperature": str(env.get("EXECUTOR_TEMPERATURE") or "").strip(),
        "extra_body_json": str(env.get("LLM_EXTRA_BODY_JSON") or "").strip(),
        "enabled": True,
    }


def _legacy_env_profile_identity(profile: dict) -> tuple[str, ...]:
    return (
        str(profile.get("model") or "").strip(),
        str(profile.get("llm_type") or "auto").strip().lower(),
        _normalize_base_url(str(profile.get("base_url") or "")),
        str(profile.get("api_key") or "").strip(),
        str(_safe_int(profile.get("context_window"), 0)),
        str(_safe_int(profile.get("max_output_tokens"), 0)),
        str(profile.get("thinking_mode") or "").strip().lower(),
        str(profile.get("reasoning_effort") or "").strip().lower(),
        str(profile.get("temperature") or "").strip(),
        str(profile.get("extra_body_json") or "").strip(),
    )


def register_legacy_env_model_profile(project_root: Path, env: dict[str, Any]) -> dict:
    """Import legacy .env model settings exactly once without making .env a runtime fallback."""
    data = load_store(project_root)
    profiles = [p for p in data.get("profiles", []) if isinstance(p, dict)]
    already_imported = next(
        (p for p in profiles if p.get(LEGACY_ENV_IMPORT_MARKER) is True),
        None,
    )
    if already_imported is not None:
        model = str(already_imported.get("model") or "").strip()
        if str(already_imported.get("name") or "").strip() == f"{model}（从 .env 导入）":
            already_imported["name"] = model
            save_store(project_root, {"profiles": profiles})
        return {"ok": True, "action": "already_imported", "profile": dict(already_imported)}

    payload = _legacy_env_profile_payload(env)
    if payload is None:
        return {"ok": True, "action": "skipped_incomplete", "profile": None}

    identity = _legacy_env_profile_identity(payload)
    existing = next(
        (p for p in profiles if _legacy_env_profile_identity(p) == identity),
        None,
    )
    if existing is not None:
        existing[LEGACY_ENV_IMPORT_MARKER] = True
        existing.setdefault("source", "legacy_env_import")
        existing.setdefault("legacy_env_imported_at", _now())
        save_store(project_root, {"profiles": profiles})
        return {"ok": True, "action": "matched_existing", "profile": dict(existing)}

    digest = hashlib.sha256("\0".join(identity).encode("utf-8")).hexdigest()[:16]
    payload["id"] = f"legacy-env-{digest}"
    payload["priority"] = len(profiles) + 1
    imported = upsert_profile(project_root, payload)

    data = load_store(project_root)
    for profile in data.get("profiles", []):
        if not isinstance(profile, dict) or profile.get("id") != imported.get("id"):
            continue
        profile[LEGACY_ENV_IMPORT_MARKER] = True
        profile["source"] = "legacy_env_import"
        profile["legacy_env_imported_at"] = _now()
        imported = dict(profile)
        break
    save_store(project_root, data)
    return {"ok": True, "action": "created", "profile": imported}


def upsert_profile(project_root: Path, payload: dict) -> dict:
    data = load_store(project_root)
    profiles = data.setdefault("profiles", [])
    pid = str(payload.get("id") or "").strip()
    if not pid:
        pid = uuid.uuid4().hex
    old_index = next((i for i, p in enumerate(profiles) if isinstance(p, dict) and p.get("id") == pid), -1)
    old = profiles[old_index] if old_index >= 0 else None
    now = _now()
    model = str(payload.get("model") or "").strip()
    name = str(payload.get("name") or model).strip()
    base_url = _normalize_base_url(str(payload.get("base_url") or ""))
    incoming_api_key = str(payload.get("api_key") or "").strip() if "api_key" in payload else ""
    existing_api_key = str((old or {}).get("api_key") or "").strip()
    if not model:
        raise ValueError("missing model")
    if not base_url:
        raise ValueError("missing base_url")
    # Persist the provider semantics v2 canonical value so the stored
    # ``llm_type`` cannot be re-interpreted differently by resolve time:
    # explicit chat stays ``openai-compatible``; explicit Responses stays
    # ``openai-responses``.  ``auto`` is kept as-is so the endpoint inference
    # still applies on every load.  The raw string is mapped directly (not
    # through LLMProvider, whose ``openai`` member predates the v2 split).
    raw_llm_type = str(payload.get("llm_type") or "auto").strip().lower() or "auto"
    provider = resolve_provider(raw_llm_type, base_url, model)
    if raw_llm_type == "auto":
        # A real probe beats hostname guessing: ask the endpoint which wire
        # protocol it actually serves for this model.  Fall back to endpoint
        # inference only when the probe cannot tell (offline, quota walls).
        detected = detect_wire_protocol(base_url, incoming_api_key or existing_api_key, model)
        if detected:
            llm_type = detected
            provider = resolve_provider(detected, base_url, model)
        else:
            llm_type = "auto"
    elif raw_llm_type in {"openai-responses", "responses", "@ai-sdk/openai"}:
        llm_type = "openai-responses"
    elif raw_llm_type in {"openai-compatible", "openai_compatible", "compatible", "chat-completions", "local", "@ai-sdk/openai-compatible"}:
        llm_type = "openai-compatible"
    elif raw_llm_type == "openai":
        # v2 UI semantics: a bare explicit ``openai`` selects chat-completions.
        llm_type = "openai-compatible"
    else:
        llm_type = canonical_llm_type(normalize_provider(raw_llm_type))
    if (
        provider is not LLMProvider.OPENAI_COMPATIBLE
        and not incoming_api_key
        and not existing_api_key
    ):
        raise ValueError("missing api_key")
    profile = dict(old or {})
    priority_default = _safe_int((old or {}).get("priority"), len(profiles) + 1)
    enabled = payload.get("enabled") if "enabled" in payload else (old or {}).get("enabled", True)
    inferred_limits = infer_model_limits(model, base_url=base_url)
    recommended = recommended_model_windows(inferred_limits["context_window"])
    context_window = _safe_int(
        payload.get("context_window"),
        _safe_int((old or {}).get("context_window"), recommended["context_window"]),
    )
    max_output_tokens = _safe_int(
        payload.get("max_output_tokens"),
        _safe_int((old or {}).get("max_output_tokens"), recommended["max_output_tokens"]),
    )
    model_context_window = _safe_int(
        payload.get("model_context_window"),
        _safe_int(
            (old or {}).get("model_context_window"),
            max(
                _safe_int(inferred_limits.get("context_window"), 0),
                context_window + max_output_tokens,
            ),
        ),
    )
    if "responses_store_disabled" in payload:
        storage_disabled = _safe_bool(payload.get("responses_store_disabled"))
    elif "responses_state_mode" in payload:
        storage_disabled = (
            normalize_responses_state_mode(payload.get("responses_state_mode")).value
            == "stateless"
        )
    else:
        storage_disabled = responses_store_disabled(old)
    profile.update(
        {
            "id": pid,
            "name": name,
            "model": model,
            "llm_type": llm_type,
            "responses_store_disabled": storage_disabled,
            "responses_state_mode": "stateless" if storage_disabled else "auto",
            "base_url": base_url,
            "context_window": context_window,
            "max_output_tokens": max_output_tokens,
            "model_context_window": model_context_window,
            "thinking_mode": _clean_thinking_mode(payload.get("thinking_mode")),
            "thinking_format": _clean_thinking_format(payload.get("thinking_format")),
            "reasoning_effort": _clean_reasoning_effort(payload.get("reasoning_effort")),
            "temperature": str(payload.get("temperature") or "").strip(),
            "extra_body_json": str(payload.get("extra_body_json") or "").strip(),
            "multimodal_mode": normalize_multimodal_mode(
                payload.get("multimodal_mode"),
                normalize_multimodal_mode((old or {}).get("multimodal_mode")),
            ),
            "priority": _safe_int(payload.get("priority"), priority_default),
            "enabled": enabled is not False,
            "updated_at": now,
        }
    )
    if "input_modalities" in payload:
        configured_modalities = normalize_input_modalities(payload.get("input_modalities"))
        if configured_modalities:
            profile["input_modalities"] = configured_modalities
        else:
            profile.pop("input_modalities", None)
    automatic_multimodal_source = (
        "automatic:models-table"
        if _model_table_record_for_model(model) is not None
        else "unavailable"
    )
    if "multimodal_mode" in payload:
        profile["multimodal_source"] = (
            automatic_multimodal_source
            if profile["multimodal_mode"] == "auto"
            else "manual"
        )
        profile.pop("multimodal_failure_at", None)
        profile.pop("multimodal_failure_reason", None)
        profile.pop("failed_modalities", None)
    elif "multimodal_source" not in profile:
        profile["multimodal_source"] = automatic_multimodal_source
    if "capability_description" in payload:
        capability_description = str(payload.get("capability_description") or "").strip()
        if capability_description:
            profile["capability_description"] = capability_description
        else:
            profile.pop("capability_description", None)
    if incoming_api_key:
        profile["api_key"] = incoming_api_key
    if not profile.get("created_at"):
        profile["created_at"] = now
    if old_index >= 0:
        profiles[old_index] = profile
    else:
        profiles.append(profile)
    save_store(project_root, data)
    return profile


def sorted_profiles(project_root: Path) -> list[dict]:
    profiles = [p for p in load_store(project_root).get("profiles", []) if isinstance(p, dict)]
    return sorted(
        profiles,
        key=lambda p: (
            _safe_int(p.get("priority"), 999999),
            str(p.get("updated_at") or ""),
            str(p.get("id") or ""),
        ),
    )


def top_profile(project_root: Path) -> Optional[dict]:
    profiles = [p for p in sorted_profiles(project_root) if is_usable_profile(p)]
    return dict(profiles[0]) if profiles else None


def reorder_profiles(project_root: Path, ordered_ids: list[str]) -> list[dict]:
    data = load_store(project_root)
    profiles = [p for p in data.get("profiles", []) if isinstance(p, dict)]
    rank = {str(pid): idx + 1 for idx, pid in enumerate(ordered_ids) if str(pid).strip()}
    next_rank = len(rank) + 1
    for p in profiles:
        pid = str(p.get("id") or "")
        if pid in rank:
            p["priority"] = rank[pid]
        else:
            p["priority"] = next_rank
            next_rank += 1
    data["profiles"] = profiles
    save_store(project_root, data)
    return sorted_profiles(project_root)


def delete_profile(project_root: Path, profile_id: str) -> bool:
    data = load_store(project_root)
    before = len(data.get("profiles", []))
    data["profiles"] = [p for p in data.get("profiles", []) if p.get("id") != profile_id]
    save_store(project_root, data)
    return len(data["profiles"]) != before


def set_profile_enabled(project_root: Path, profile_id: str, enabled: bool) -> Optional[dict]:
    """Persist a profile's availability without deleting its configuration."""
    data = load_store(project_root)
    for profile in data.get("profiles", []):
        if not isinstance(profile, dict) or str(profile.get("id") or "") != str(profile_id or ""):
            continue
        profile["enabled"] = bool(enabled)
        profile["updated_at"] = _now()
        save_store(project_root, data)
        return dict(profile)
    return None


def get_profile(project_root: Path, profile_id: str) -> Optional[dict]:
    if not profile_id:
        return None
    for profile in load_store(project_root).get("profiles", []):
        if isinstance(profile, dict) and profile.get("id") == profile_id:
            return dict(profile)
    return None


def fallback_chain(project_root: Path, selected_profile_id: str = "") -> list[dict]:
    selected = get_profile(project_root, selected_profile_id)
    chain: list[dict] = []
    seen: set[str] = set()
    if is_usable_profile(selected):
        chain.append(selected)
        seen.add(str(selected.get("id") or ""))
    # Determine primary provider for fallback filtering: only switch to same llm_type (provider)
    primary_provider = None
    if chain:
        try:
            primary_provider = resolve_profile_provider(chain[0])
        except Exception:
            primary_provider = None
    else:
        # No selected profile; primary will be the top priority profile
        for profile in sorted_profiles(project_root):
            if is_usable_profile(profile):
                try:
                    primary_provider = resolve_profile_provider(profile)
                except Exception:
                    primary_provider = None
                break
    for profile in sorted_profiles(project_root):
        if not is_usable_profile(profile):
            continue
        pid = str(profile.get("id") or "")
        if pid and pid not in seen:
            # Only fallback to same provider type (llm_type)
            if primary_provider is not None:
                try:
                    if resolve_profile_provider(profile) != primary_provider:
                        continue
                except Exception:
                    continue
            chain.append(dict(profile))
            seen.add(pid)
    return chain


def profile_cache_key(profile: dict) -> str:
    body = json.dumps(
        {
            "id": profile.get("id"),
            "model": profile.get("model"),
            "llm_type": resolve_profile_provider(profile).value,
            "responses_store_disabled": responses_store_disabled(profile),
            "base_url": profile.get("base_url"),
            "api_key": profile.get("api_key"),
            "headers": profile_request_headers(profile),
            "thinking_mode": profile.get("thinking_mode"),
            "thinking_format": profile.get("thinking_format"),
            "reasoning_effort": profile.get("reasoning_effort"),
            "temperature": profile.get("temperature"),
            "extra_body_json": profile.get("extra_body_json"),
            "multimodal_mode": normalize_multimodal_mode(profile.get("multimodal_mode")),
            "input_modalities": normalize_input_modalities(profile.get("input_modalities")),
            "failed_modalities": normalize_failed_modalities(profile.get("failed_modalities")),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def profile_request_headers(profile: dict) -> Optional[Dict[str, str]]:
    """Resolve optional custom HTTP headers declared on a model profile.

    A profile may carry a ``headers`` object whose keys are sent verbatim as
    request headers on every LLM call of that profile (for example the
    ``x-opencode-*`` headers some OpenCode endpoints require).  Returns None
    when the profile declares none, so callers can omit ``default_headers``
    entirely for every other provider.
    """
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


def profile_with_session_request_headers(profile: dict, session_id: str) -> dict:
    """Return a profile whose ``x-opencode-session`` header is stable per session.

    Providers like OpenCode Zen recommend a stable per-conversation session id
    (``x-opencode-session``) so they can optimize routing and prompt caching.
    When the profile declares such a header and a non-empty ``session_id`` is
    given, this returns a shallow copy of the profile with that header replaced
    by a value derived from ``session_id``: the same session always maps to the
    same header value, different sessions map to different values.

    The original profile object is returned unchanged (no copy) when nothing
    needs to change, so callers never mutate the shared profile catalog.
    """
    raw = profile.get("headers")
    if not isinstance(raw, dict) or "x-opencode-session" not in raw:
        return profile
    sid = str(session_id or "").strip()
    if not sid:
        return profile
    digest = hashlib.sha256(sid.encode("utf-8")).hexdigest()[:12]
    clone = dict(profile)
    headers = dict(raw)
    headers["x-opencode-session"] = "ses-myagent-" + digest
    clone["headers"] = headers
    return clone


def mark_profile_multimodal_failed(
    project_root: Path,
    profile_id: str,
) -> Optional[dict]:
    """Persist a provider capability rejection so later requests skip media."""
    pid = str(profile_id or "").strip()
    if not pid:
        return None
    data = load_store(project_root)
    for profile in data.get("profiles", []):
        if not isinstance(profile, dict) or str(profile.get("id") or "") != pid:
            continue
        profile["multimodal_mode"] = "disabled"
        profile["multimodal_source"] = "failure"
        profile["multimodal_failure_reason"] = "provider_rejected_multimodal_input"
        profile["multimodal_failure_at"] = _now()
        profile["updated_at"] = _now()
        save_store(project_root, data)
        return dict(profile)
    return None


def mark_profile_modalities_failed(
    project_root: Path,
    profile_id: str,
    modalities: Any,
    reason: str = "provider_rejected_media_input",
) -> Optional[dict]:
    """Persist provider rejection for only the concrete media modalities used."""
    pid = str(profile_id or "").strip()
    rejected = [
        modality
        for modality in normalize_input_modalities(modalities)
        if modality in MEDIA_INPUT_MODALITIES
    ]
    if not pid or not rejected:
        return None
    data = load_store(project_root)
    now = _now()
    for profile in data.get("profiles", []):
        if not isinstance(profile, dict) or str(profile.get("id") or "") != pid:
            continue
        failed = normalize_failed_modalities(profile.get("failed_modalities"))
        for modality in rejected:
            failed[modality] = {
                "reason": str(reason or "provider_rejected_media_input"),
                "failed_at": now,
            }
        profile["failed_modalities"] = failed
        profile["multimodal_source"] = "failure"
        profile["updated_at"] = now
        save_store(project_root, data)
        return dict(profile)
    return None


def discover_models(base_url: str, api_key: str, timeout: float = 20.0) -> List[Dict[str, Any]]:
    url = models_url_for_base(base_url)
    if not url:
        raise ValueError("missing base_url")
    headers = {}
    if str(api_key or "").strip():
        headers["Authorization"] = "Bearer " + str(api_key).strip()
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ValueError("models response is not a list")
        out: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            mid = str(item.get("id") or item.get("name") or "").strip()
            if not mid:
                continue
            raw_has_context = any(k in item for k in CONTEXT_LIMIT_FIELDS)
            raw_has_limits = raw_has_context or any(k in item for k in OUTPUT_LIMIT_FIELDS)
            limits = infer_model_limits(mid, item, base_url=base_url)
            capabilities = infer_model_task_capabilities(
                mid,
                context_window=recommended_model_windows(
                    limits["context_window"]
                )["context_window"],
            )
            out.append(
                {
                    "id": mid,
                    "owned_by": item.get("owned_by") or item.get("owner") or "",
                    "created": item.get("created") or None,
                    "context_window": limits["context_window"],
                    "model_context_window": limits["context_window"],
                    "max_output_tokens": limits["max_output_tokens"],
                    "raw_has_limits": raw_has_limits,
                    "limit_source": limits["context_source"],
                    "context_window_source": limits["context_source"],
                    "output_limit_source": limits["output_source"],
                    **capabilities,
                    "multimodal_input": "multimodal_candidate" in set(
                        capabilities.get("capability_tags") or ()
                    ),
                }
            )
    out.sort(key=lambda row: row["id"].lower())
    return out
