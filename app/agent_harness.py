"""
agent_harness — Agent 项目的中枢模块。

内容概览
--------
- 环境变量与路径（WORK_DIR、AGENT_DEFAULT_WRITE_FILENAME、PROJECT_ROOT、各 LLM 与截断相关常量）
- OpenAI 兼容执行端客户端与 HTTP 请求日志；单轮补全（压缩摘要、会话标题等）走 executor
- 会话落盘、Todo、压缩 helper、消息与 OpenAI/会话 JSON 互转
- 日志、提示词模板加载

与 agent_loop / agent_tools 的边界
----------------------------------
- 不实现 ReAct 主循环与工具调度；只提供可复用能力与配置。
"""

import os
import sys
import json
import re
import uuid
import logging
import shutil
import copy
import tempfile
import time
import ctypes
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import dotenv
import httpx
from openai import OpenAI
import threading
import model_profiles
import cpu_pressure
from llm import (
    LLMRequestContext,
    LLMRequestPurpose,
    TransportEvent,
    build_transport,
    canonical_llm_type,
    resolve_profile_provider,
)

from agent_messages import UserMessage, AssistantMessage, ToolMessage, SystemMessage
from agent_openai import (
    _api_messages_have_media,
    _api_messages_required_modalities,
    _classify_candidate_failure,
    _candidate_retry_policy,
    _is_media_input_error,
    _media_error_modalities,
    _messages_to_params_for_client,
    _claim_additional_recovery_request,
    _serialized_messages_to_text_only,
    chat_completion,
    parse_assistant_message,
    refresh_request_recovery_config_from_env,
    run_chat_completion_stream_worker,
    single_turn_text_completion,
)
from agent_subagent_results import format_pending_subagent_notification
from agent_tokenizer import count_message_tokens

# 工程所在文件夹（General_Agent 包目录）；必须在 load_dotenv 之前定义
_PROJECT_ROOT = Path(__file__).resolve().parent


def dotenv_file_path() -> Path:
    """与分发包一致的可写 .env：exe 同级（打包）；源码为项目根。"""
    if getattr(sys, "frozen", False):
        try:
            return Path(sys.executable).resolve().parent / ".env"
        except OSError:
            pass
    return _PROJECT_ROOT / ".env"


def load_app_dotenv() -> None:
    primary = dotenv_file_path()
    if primary.is_file():
        dotenv.load_dotenv(primary, override=True)
    dotenv.load_dotenv(override=False)


load_app_dotenv()
refresh_request_recovery_config_from_env()


# ==================== 运行配置（非模型项来自 .env，模型项来自 profile）====================
# 工程根目录（app/ 的上级）；兼容旧名 PROJECT_ROOT
PROJECT_ROOT = _PROJECT_ROOT.parent


def _register_legacy_dotenv_model_profile() -> dict:
    """One-way migration from legacy .env model fields into the profile store."""
    path = dotenv_file_path()
    if not path.is_file():
        return {"ok": True, "action": "skipped_missing_env", "profile": None}
    try:
        raw = dotenv.dotenv_values(path, interpolate=False)
        values = {
            str(k).lstrip("\ufeff").strip(): v
            for k, v in raw.items()
            if str(k or "").lstrip("\ufeff").strip() and v is not None
        }
        result = model_profiles.register_legacy_env_model_profile(PROJECT_ROOT, values)
        if result.get("action") in {"created", "matched_existing"}:
            profile = result.get("profile") if isinstance(result.get("profile"), dict) else {}
            logging.getLogger(__name__).info(
                "Legacy .env model configuration registered as model profile: action=%s id=%s",
                result.get("action"),
                profile.get("id") or "",
            )
        return result
    except Exception as exc:
        logging.getLogger(__name__).warning("Failed to import legacy .env model profile: %s", exc)
        return {"ok": False, "action": "error", "profile": None}


_LEGACY_ENV_MODEL_IMPORT = _register_legacy_dotenv_model_profile()
_INITIAL_MODEL_PROFILE = model_profiles.top_profile(PROJECT_ROOT) or {}


def _env_path(name: str, default: Path | str, *, base: Path = PROJECT_ROOT) -> Path:
    """Resolve .env paths relative to the project root, not the launch cwd."""
    raw = (os.getenv(name) or "").strip()
    path = Path(raw).expanduser() if raw else Path(default).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


WORK_DIR = _env_path("WORK_DIR", PROJECT_ROOT / "workspace")


def _prompt_md_candidate_paths() -> list[Path]:
    """prompt.md 可能出现的位置（不依赖 cwd；兼容 PyInstaller / 直连 exe）。"""
    raw: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        raw.append(Path(meipass) / "prompt.md")
    raw.append(_PROJECT_ROOT / "prompt.md")
    try:
        raw.append(Path(sys.executable).resolve().parent / "prompt.md")
    except OSError:
        pass
    raw.append(Path.cwd() / "prompt.md")
    seen: set[str] = set()
    out: list[Path] = []
    for p in raw:
        key = os.path.normcase(os.path.normpath(str(p)))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


_PROMPT_MD_RESOLVED: Optional[Path] = None


def resolve_prompt_md_path() -> Path:
    """定位 prompt.md；首次成功后缓存路径。"""
    global _PROMPT_MD_RESOLVED
    if _PROMPT_MD_RESOLVED is not None:
        return _PROMPT_MD_RESOLVED
    for p in _prompt_md_candidate_paths():
        try:
            if p.is_file():
                _PROMPT_MD_RESOLVED = p.resolve()
                return _PROMPT_MD_RESOLVED
        except OSError:
            continue
    tried = "; ".join(str(p) for p in _prompt_md_candidate_paths())
    raise FileNotFoundError(f"未找到 prompt.md，已尝试: {tried}")


AGENT_DEFAULT_WRITE_FILENAME = os.getenv("AGENT_DEFAULT_WRITE_FILENAME", "output.txt")
SKILLS_DIR = _env_path("SKILLS_DIR", WORK_DIR / "skills")
LOG_DIR = _env_path("LOG_DIR", PROJECT_ROOT / "logs")
MAX_REACT_ITER = int(os.getenv("MAX_REACT_ITER", "100"))
SUBAGENT_MAX_DEPTH = max(1, int(os.getenv("SUBAGENT_MAX_DEPTH", "1")))
SUBAGENT_MAX_REACT_ITER = max(1, int(os.getenv("SUBAGENT_MAX_REACT_ITER", "100")))
SUBAGENT_BEST_OF_N = max(2, min(8, int(os.getenv("SUBAGENT_BEST_OF_N", "3"))))
SUBAGENT_INDEX_FILE = "subagent_index.json"
SUBAGENT_PENDING_RESULTS_FILE = "pending_subagent_results.json"
VERBOSE_LOGGING = os.getenv("VERBOSE_LOGGING", "True").lower() == "true"
TODO_MAX_ITEMS = int(os.getenv("TODO_MAX_ITEMS", "30"))

EXECUTOR_LLM = str(_INITIAL_MODEL_PROFILE.get("model") or "").strip()
EXECUTOR_LLM_TYPE = (
    resolve_profile_provider(_INITIAL_MODEL_PROFILE).value
    if _INITIAL_MODEL_PROFILE
    else "openai"
)
try:
    EXECUTOR_TEMPERATURE = float(str(_INITIAL_MODEL_PROFILE.get("temperature") or "0.7"))
except ValueError:
    EXECUTOR_TEMPERATURE = 0.7
EXECUTOR_THINKING_MODE = str(_INITIAL_MODEL_PROFILE.get("thinking_mode") or "").strip()
EXECUTOR_EXTRA_BODY_JSON = str(_INITIAL_MODEL_PROFILE.get("extra_body_json") or "").strip()
EXECUTOR_REASONING_EFFORT_RAW = str(_INITIAL_MODEL_PROFILE.get("reasoning_effort") or "").strip()

# 本地 OpenAI 兼容服务（根 URL + /v1）
LOCAL_LLM_HOST = str(_INITIAL_MODEL_PROFILE.get("base_url") or "http://localhost:11434").rstrip("/")
LOCAL_LLM = EXECUTOR_LLM
_LOCAL_OPENAI_DUMMY_KEY = "local"

# Compatibility globals are derived from the first usable model profile. Model
# connection settings are never read from .env.
OPENAI_BASE_URL = str(_INITIAL_MODEL_PROFILE.get("base_url") or "").strip()
OPENAI_API_KEY = str(_INITIAL_MODEL_PROFILE.get("api_key") or "").strip()
_MODEL_PROFILE_ENV_KEYS = {
    "EXECUTOR_LLM", "EXECUTOR_LLM_TYPE", "EXECUTOR_TEMPERATURE",
    "OPENAI_BASE_URL", "OPENAI_API_KEY", "CONTEXT_WINDOW", "MAX_OUTPUT_TOKENS",
    "LLM_THINKING_MODE", "LLM_REASONING_EFFORT", "LLM_EXTRA_BODY_JSON",
    "LOCAL_LLM_HOST", "LOCAL_LLM",
}

# 加密配置仅补充非模型环境变量；模型字段始终以 model profile 为准。
try:
    from secret_loader import load_encrypted_config
    _encrypted_config = load_encrypted_config()
    if _encrypted_config:
        _loaded_keys = []
        for _k, _v in _encrypted_config.items():
            if _k in _MODEL_PROFILE_ENV_KEYS:
                continue
            if not os.environ.get(_k):
                os.environ[_k] = _v
                _loaded_keys.append(_k)
        if _loaded_keys:
            logging.getLogger(__name__).info(f"从加密文件加载默认配置: {_loaded_keys}")
except Exception as e:
    logging.getLogger(__name__).warning("Failed to load encrypted config defaults: %s", e)

if not _INITIAL_MODEL_PROFILE:
    logging.getLogger(__name__).warning(
        "没有可用的 model profile；Web 将停留在配置向导（/setup）。"
    )

# 创建必要目录（可关闭：AGENT_AUTO_CREATE_DIRS=false）
_AUTO_CREATE_DIRS = os.getenv("AGENT_AUTO_CREATE_DIRS", "true").strip().lower() in ("1", "true", "yes", "on")
SESSIONS_DIR = WORK_DIR / "sessions"
if _AUTO_CREATE_DIRS:
    WORK_DIR.mkdir(exist_ok=True)
    SKILLS_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
    SESSIONS_DIR.mkdir(exist_ok=True)
INDEX_FILE = SESSIONS_DIR / "sessions.json"

# 压缩落盘常量（trim / memory / loop 共用）
COMPACT_BOUNDARY_SYSTEM_EXACT = "Conversation compacted. If you need to review earlier details of the conversation, please check this session's directory. "
COMPACT_TRUNCATED_BOUNDARY_SYSTEM_EXACT = (
    "Conversation truncated. If you need to review earlier details of the conversation, "
    "please check this session's directory. "
)
COMPACT_RECAP_USER_PREFIX = "[压缩摘要]"

# 每会话目录内：原始工作消息 JSON
SESSION_WORK_MESSAGES_FILE = "work_messages.json"

# DeepSeek 思考：LLM_THINKING_MODE=enabled/disabled（未配置则默认 enabled）；启用时再通过 LLM_REASONING_EFFORT 设档位（high/max）。
# disabled 且基准为 DeepSeek 时需显式 thinking.disabled；仅省略 thinking 时 API 仍会默认开启思考。
# LLM_EXTRA_BODY_JSON 若非空则整段覆盖自动生成；思考未开启时会忽略 LLM_REASONING_EFFORT，并移除 extra_body 中的 reasoning_effort。


def _thinking_enabled_from_extra_dict(eb: Optional[Dict[str, Any]]) -> bool:
    """由 extra_body 字典判断是否处于「服务端思考扩展」开启状态（用于 reasoning_effort / temperature）。"""
    if not eb:
        return False
    t = eb.get("thinking")
    if isinstance(t, dict):
        typ = (t.get("type") or "").lower()
        if typ == "disabled":
            return False
        return typ == "enabled" or t.get("enabled") is True
    return t is True


def _sanitize_extra_body_drop_reasoning_when_thinking_off(
    eb: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """防止 LLM_EXTRA_BODY_JSON 等在思考关闭时仍携带 reasoning_effort 被发往兼容端。"""
    if eb is None:
        return None
    out = dict(eb)
    if _thinking_enabled_from_extra_dict(out) or "reasoning_effort" not in out:
        return out
    logging.getLogger(__name__).warning(
        "思考未开启（extra_body 中非 enabled），已从 extra_body 中移除 reasoning_effort"
    )
    out.pop("reasoning_effort", None)
    return out


def _profile_llm_thinking_wants_extra_body_enabled() -> bool:
    raw = EXECUTOR_THINKING_MODE.lower()
    if not raw:
        return True
    if raw == "enabled":
        return True
    if raw == "disabled":
        return False
    logging.getLogger(__name__).warning(
        "LLM_THINKING_MODE 应为 enabled 或 disabled（当前 %r），已按 enabled 处理",
        EXECUTOR_THINKING_MODE,
    )
    return True


def _profile_base_url_likely_deepseek() -> bool:
    return "deepseek" in OPENAI_BASE_URL.lower()


def _base_url_likely_deepseek(base_url: str) -> bool:
    return "deepseek" in str(base_url or "").lower()


def _load_executor_extra_body() -> Optional[Dict[str, Any]]:
    raw = EXECUTOR_EXTRA_BODY_JSON
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logging.getLogger(__name__).warning("LLM_EXTRA_BODY_JSON 不是合法 JSON，已忽略")
            return None
        if not isinstance(data, dict):
            logging.getLogger(__name__).warning(
                "LLM_EXTRA_BODY_JSON 须为 JSON 对象，已忽略: %s", type(data).__name__
            )
            return None
        return _sanitize_extra_body_drop_reasoning_when_thinking_off(data)
    eb_out: Optional[Dict[str, Any]] = None
    if _profile_llm_thinking_wants_extra_body_enabled():
        eb_out = {"thinking": {"type": "enabled"}}
    # 关闭思考：非 DeepSeek 基准通常可省略 thinking；DeepSeek 须显式 disabled，否则会默认仍为思考开启
    elif _profile_base_url_likely_deepseek():
        eb_out = {"thinking": {"type": "disabled"}}
    return _sanitize_extra_body_drop_reasoning_when_thinking_off(eb_out)


EXECUTOR_EXTRA_BODY: Optional[Dict[str, Any]] = _load_executor_extra_body()


def _extra_body_thinking_enabled() -> bool:
    """与 DeepSeek「思考模式」一致：extra_body 含 thinking.type == enabled 时需回传带工具调用的 reasoning。"""
    return _thinking_enabled_from_extra_dict(EXECUTOR_EXTRA_BODY)


def _executor_reasoning_effort() -> Optional[str]:
    """仅思考开启时才下发顶层 reasoning_effort；否则忽略 LLM_REASONING_EFFORT。"""
    if not _extra_body_thinking_enabled():
        v_skip = EXECUTOR_REASONING_EFFORT_RAW
        if v_skip:
            logging.getLogger(__name__).debug(
                "思考未开启，已忽略 LLM_REASONING_EFFORT=%r", v_skip
            )
        return None
    v = EXECUTOR_REASONING_EFFORT_RAW
    return v if v else "high"


# 主模型：思考开时带 reasoning_effort，关时为 None
EXECUTOR_REASONING_EFFORT: Optional[str] = _executor_reasoning_effort()


def _profile_extra_body(profile: dict) -> Optional[Dict[str, Any]]:
    raw_extra = str(profile.get("extra_body_json") or "").strip()
    thinking_mode = str(profile.get("thinking_mode") or "").strip().lower()
    if raw_extra:
        try:
            data = json.loads(raw_extra)
        except json.JSONDecodeError:
            logging.getLogger(__name__).warning("模型配置 LLM_EXTRA_BODY_JSON 不是合法 JSON，已忽略")
            return None
        if not isinstance(data, dict):
            logging.getLogger(__name__).warning("模型配置 LLM_EXTRA_BODY_JSON 须为 JSON 对象，已忽略")
            return None
        return _sanitize_extra_body_drop_reasoning_when_thinking_off(data)
    # Responses profiles are safest when an empty setting means "automatic":
    # do not synthesize provider-specific fields.  Compatible profiles retain
    # the historical thinking defaults used by DeepSeek-style endpoints.
    if not thinking_mode and resolve_profile_provider(profile).value == "openai":
        return None
    if not thinking_mode:
        thinking_mode = "enabled"
    if thinking_mode == "enabled":
        return _sanitize_extra_body_drop_reasoning_when_thinking_off({"thinking": {"type": "enabled"}})
    if thinking_mode == "disabled" and _base_url_likely_deepseek(str(profile.get("base_url") or "")):
        return _sanitize_extra_body_drop_reasoning_when_thinking_off({"thinking": {"type": "disabled"}})
    return None


def _profile_reasoning_effort(profile: dict, extra_body: Optional[Dict[str, Any]]) -> Optional[str]:
    v = str(profile.get("reasoning_effort") or "").strip()
    if resolve_profile_provider(profile).value == "openai":
        # Responses has a native `reasoning.effort` field.  Never invent an
        # effort when the profile leaves it on automatic.
        return v or None
    if not _thinking_enabled_from_extra_dict(extra_body):
        return None
    return v if v else "high"


THINKING_FORMATS = {"deepseek", "reasoning", "think_blocks", "none"}


def _profile_thinking_format(profile: dict) -> str:
    """目标模型期望的思考格式（发送侧按当前模型自适应）：
    - deepseek     ：assistant 输出 reasoning_content 字段，内容剥离 <think>；
    - reasoning    ：assistant 输出 reasoning 字段（mimo 等），内容剥离 <think>；
    - think_blocks ：不发思考字段，内容原样保留 <think>（思考内联在内容里的模型）；
    - none         ：不发思考字段，内容剥离 <think>（纯文本模型）。
    显式配置 thinking_format 优先；未配置时按模型名推断（mimo 只看模型名，不看 oczen）。
    """
    raw = str(profile.get("thinking_format") or "").strip().lower()
    if raw in THINKING_FORMATS:
        return raw
    model = str(profile.get("model") or "").strip().lower()
    base_url = str(profile.get("base_url") or "").strip().lower()
    if "deepseek" in model:
        return "deepseek"
    if "mimo" in model:
        return "reasoning"
    if "deepseek" in base_url:
        return "deepseek"
    return "deepseek"


def _profile_temperature(profile: dict) -> float:
    raw = str(profile.get("temperature") or "").strip()
    if not raw:
        return float(EXECUTOR_TEMPERATURE)
    try:
        return float(raw)
    except ValueError:
        logging.getLogger(__name__).warning("模型配置 EXECUTOR_TEMPERATURE=%r 非数字，已使用默认值", raw)
        return float(EXECUTOR_TEMPERATURE)


def strip_reasoning_for_api_request(messages: List[Any]) -> List[Any]:
    """?????? reasoning_content ?? token??????????????"""
    from agent_think import strip_think_blocks

    thinking_on = _extra_body_thinking_enabled()
    out: List[Any] = []
    for m in messages:
        if not isinstance(m, AssistantMessage):
            out.append(m)
            continue
        clean_content = strip_think_blocks(str(getattr(m, "content", "") or ""))
        ak = getattr(m, "additional_kwargs", None)
        if not isinstance(ak, dict):
            out.append(m.model_copy(update={"content": clean_content}))
            continue

        new_ak = dict(ak)
        has_tool_calls = bool(getattr(m, "tool_calls", None))
        reasoning_field = str(new_ak.get("reasoning_field") or "").strip()
        if reasoning_field not in {"reasoning", "reasoning_content"}:
            reasoning_field = "reasoning_content"

        # ?????assistant ? tool_calls ?????? reasoning_content ???
        if has_tool_calls:
            if reasoning_field not in new_ak:
                new_ak[reasoning_field] = str(new_ak.get("reasoning_content") or new_ak.get("reasoning") or "")
        elif not thinking_on:
            new_ak.pop("reasoning_content", None)
            new_ak.pop("reasoning", None)
            new_ak.pop("reasoning_field", None)
        else:
            new_ak.pop("reasoning_content", None)
            new_ak.pop("reasoning", None)
            new_ak[reasoning_field] = ""
            new_ak["reasoning_field"] = reasoning_field

        out.append(m.model_copy(update={"content": clean_content, "additional_kwargs": new_ak}))
    return out


def _remap_serialized_reasoning_format(
    messages: List[Dict[str, Any]],
    thinking_format: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """将规范序列化消息（保留 <think> 内容 + reasoning_content 字段）按目标模型格式转换。

    供 fallback 客户端在逐个候选重试时调用：每个候选拿到自己期望的思考字段名，
    并决定内容中的 <think> 保留还是剥离。
    """
    from agent_think import strip_think_blocks

    fmt = str(thinking_format or "deepseek").strip().lower()
    if fmt not in THINKING_FORMATS:
        fmt = "deepseek"
    strip_think = fmt != "think_blocks"
    out: List[Dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict) or str(msg.get("role") or "") != "assistant":
            out.append(msg)
            continue
        new_msg = dict(msg)
        content = new_msg.get("content")
        if isinstance(content, str) and strip_think:
            new_msg["content"] = strip_think_blocks(content)
        if fmt == "reasoning":
            rc = new_msg.pop("reasoning_content", None)
            new_msg.pop("reasoning", None)
            if rc is not None:
                new_msg["reasoning"] = rc
        elif fmt == "deepseek":
            new_msg.pop("reasoning", None)
        else:  # think_blocks / none：不发思考字段
            new_msg.pop("reasoning_content", None)
            new_msg.pop("reasoning", None)
        out.append(new_msg)
    return out


def _context_env_int(name: str, default: str) -> int:
    """读取上下文压缩相关 int；仅认 `name`（CONTEXT_*），不设则用 default。"""
    v = os.getenv(name)
    if v is not None:
        return int(v)
    return int(default)


# 压缩 / 记忆策略（.env 使用下列 CONTEXT_* 名，与 agent_memory 一致）
# 与压缩/应急共用同一估算 token 门限 T：若 (整包上送 > T) 或 (仅多轮 work > T) 且块数足则尝试压缩
CONTEXT_WINDOW = model_profiles._safe_int(_INITIAL_MODEL_PROFILE.get("context_window"), 128000)
# 从块序列尾部完整保留的「对话轮」数（一轮 = 一条 user 起至下一条 user 之前）；微压扫描仍用 legacy 块（见 agent_memory._collect_blocks）
CONTEXT_KEEP_RECENT_TURNS = _context_env_int("CONTEXT_KEEP_RECENT_TURNS", "3")
# 紧挨全量保留区之前的块数，做微压
CONTEXT_MICRO_WORK_ROUNDS = _context_env_int("CONTEXT_MICRO_WORK_ROUNDS", "20")
# 整包仍超 CONTEXT_WINDOW 时，应急截断重试次数上限
CONTEXT_EMERGENCY_SHRINK_MAX_RETRIES = _context_env_int("CONTEXT_EMERGENCY_SHRINK_MAX_RETRIES", "3")
# 压缩摘要模型输入相对阈值的上浮比例（默认 110%）；超出部分从更早对话裁掉，优先保留较新内容
CONTEXT_COMPRESS_PROMPT_TOKEN_RATIO = float(os.getenv("CONTEXT_COMPRESS_PROMPT_TOKEN_RATIO", "1.1"))
# 压缩流程异常兜底：保留尾部对话的 token 上限（近似）。未设置 env 时默认 CONTEXT_WINDOW//2
_failure_cap_raw = os.getenv("CONTEXT_COMPRESS_FAILURE_MAX_TOKENS")
CONTEXT_COMPRESS_FAILURE_MAX_TOKENS = (
    int(_failure_cap_raw)
    if (_failure_cap_raw is not None and str(_failure_cap_raw).strip() != "")
    else max(4096, int(CONTEXT_WINDOW) // 2)
)
# 单次进入压缩后「并行摘要 + key + 微压」最大迭代次数；用尽后仍超目标则按约 50% 窗口截尾
CONTEXT_COMPRESS_MAX_ROUNDS = _context_env_int("CONTEXT_COMPRESS_MAX_ROUNDS", "3")
# 摘要第 3 遍：完整保留「最后一条 user + 其后再多 N 条 assistant（ReAct 步）」
CONTEXT_COMPRESS_ROUND3_MAX_REACT = _context_env_int("CONTEXT_COMPRESS_ROUND3_MAX_REACT", "10")
# 达标：整包估算 token（与状态行同口径）≤ CONTEXT_WINDOW × 该比例
CONTEXT_COMPRESS_TARGET_RATIO = float(os.getenv("CONTEXT_COMPRESS_TARGET_RATIO", "0.6"))

REPEAT_DETECTION_THRESHOLD_SUMMARY = int(os.getenv("REPEAT_DETECTION_THRESHOLD_SUMMARY", "2"))
REPEAT_DETECTION_THRESHOLD_ERROR = int(os.getenv("REPEAT_DETECTION_THRESHOLD_ERROR", "3"))

# ==================== 新截断配置（首尾保留方式）====================
# 日志消息截断保留字符数（首尾各保留N字符）
LOG_TRUNCATE_KEEP_CHARS = int(os.getenv("LOG_TRUNCATE_KEEP_CHARS", "200"))

def _tool_result_truncate_keep_chars_from_env() -> int:
    raw = os.getenv("TOOL_RESULT_TRUNCATE_KEEP_CHARS")
    if raw is None or str(raw).strip() == "":
        raw = os.getenv("LLM_CONTEXT_TRUNCATE_KEEP_CHARS", "40000")
    try:
        return max(0, int(str(raw).strip()))
    except (TypeError, ValueError):
        return 40000


# 兼容旧名称；实际配置统一使用 TOOL_RESULT_TRUNCATE_KEEP_CHARS，值表示落盘触发阈值。
TOOL_RESULT_TRUNCATE_KEEP_CHARS = _tool_result_truncate_keep_chars_from_env()
LLM_CONTEXT_TRUNCATE_KEEP_CHARS = TOOL_RESULT_TRUNCATE_KEEP_CHARS

MAX_PARALLEL_TOOLS = int(os.getenv("MAX_PARALLEL_TOOLS", "10"))

# 内部链微压：推理 / 助手正文 / 工具结果 截断
MICRO_SHRINK_REASONING_CHARS = int(os.getenv("MICRO_SHRINK_REASONING_CHARS", "100"))
MICRO_SHRINK_ASSISTANT_CHARS = int(os.getenv("MICRO_SHRINK_ASSISTANT_CHARS", "100"))
MICRO_SHRINK_TOOL_CHARS = int(os.getenv("MICRO_SHRINK_TOOL_CHARS", "100"))
# 微压块内 truncate_fat 下限（与 TOOL 上限协调）
MICRO_SHRINK_FAT_TOOL_FLOOR = int(os.getenv("MICRO_SHRINK_FAT_TOOL_FLOOR", "100"))


def apply_final_dedup_to_messages(
    messages: List[Any],
    final_text: str,
) -> Tuple[List[Any], bool]:
    """
    对正文与终稿 `final_text` 相同、且无 tool_calls 的若干 AssistantMessage 去重，保留一条并标
    is_final、取消 is_assistant_response（落盘为 type=assistant，统一 OpenAI 标准）。
    若列表中无匹配条，返回 (原列表, True)，由调用方再 append 终稿；否则 (新列表, False)。
    """
    ft = (final_text or "").strip()
    if not ft or not messages:
        return list(messages), True
    mlist = list(messages)
    def _plain_match(m) -> bool:
        return bool(
            isinstance(m, AssistantMessage)
            and not (getattr(m, "tool_calls", None) or None)
            and str(m.content or "").strip() == ft
        )
    match_idx = [i for i, m in enumerate(mlist) if _plain_match(m)]
    if not match_idx:
        return mlist, True
    keep: Optional[int] = None
    for i in match_idx:
        md = getattr(mlist[i], "metadata", None) or {}
        if md.get("is_assistant_response") and not md.get("is_final"):
            keep = i
            break
    if keep is None:
        for i in match_idx:
            if not (getattr(mlist[i], "metadata", None) or {}).get("is_final"):
                keep = i
                break
    if keep is None:
        keep = match_idx[-1]
    out: List[Any] = []
    for i, m in enumerate(mlist):
        if i == keep:
            old_md = dict(getattr(m, "metadata", None) or {})
            old_md["is_final"] = True
            old_md["is_assistant_response"] = False
            out.append(m.model_copy(update={"metadata": old_md}))
        elif i in match_idx and i != keep:
            continue
        else:
            out.append(m)
    return out, False


# ==================== 截断函数 ====================
def truncate_head_tail(text: str, keep_chars: int) -> str:
    """保留首尾各keep_chars字符，中间替换为省略提示"""
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= keep_chars * 2:
        return text
    head = text[:keep_chars]
    tail = text[-keep_chars:]
    omitted = len(text) - 2 * keep_chars
    return f"{head}\n... (省略 {omitted} 字符) ...\n{tail}"


def truncate_tool_result_for_llm(text: Any, keep_chars: int) -> str:
    """工具结果写入 UI/LLM 前的头部截断；过长时提示模型分块阅读。"""
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= keep_chars * 2:
        return text
    notice = (
        "[系统提示：工具返回结果已被截断；仅保留开头内容。请收窄查询或分块读取完整结果。]\n"
    )
    return notice + text[: max(0, int(keep_chars))]

# ==================== 日志配置 ====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ANSI 颜色码
COLOR_WHITE = "\033[97m"
COLOR_BLUE = "\033[94m"
COLOR_YELLOW = "\033[93m"
COLOR_RESET = "\033[0m"

def setup_logging(user_input: str, session_id: str = ""):
    """初始化日志，每次会话创建独立日志文件"""
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_input = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff]', '_', user_input)[:30]
    if session_id:
        log_filename = f"{session_id[:8]}_{safe_input}.log"
    else:
        log_filename = f"{timestamp}_{safe_input}.log"
    log_path = LOG_DIR / log_filename

    file_handler = logging.FileHandler(log_path, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)

    if VERBOSE_LOGGING:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(console_handler)

    logger.info(f"日志文件已创建: {log_path}")
    logger.info(f"用户输入: {user_input}")

# ==================== 提示词模板加载 ====================
def normalize_prompt_language(language: str = "zh-CN") -> str:
    """Normalize the UI language value used to select model-facing prompts."""
    value = str(language or "zh-CN").strip().lower()
    return "en" if value in {"en", "en-us", "en-gb", "english"} else "zh-CN"


def load_prompt_template(template_name: str, language: str = "zh-CN") -> str:
    """从对应语言的 prompt 模板文件加载指定片段。"""
    if template_name == "tools_description":
        return ""
    try:
        path = resolve_prompt_md_path()
        normalized_language = normalize_prompt_language(language)
        if normalized_language == "en":
            english_path = path.with_name("prompt.en.md")
            if english_path.is_file():
                path = english_path
        with path.open("r", encoding="utf-8") as f:
            content = f.read()
        pattern = rf"## {template_name}\n(.*?)(?=\n## |$)"
        match = re.search(pattern, content, re.DOTALL)
        if match:
            return match.group(1).strip()
        else:
            raise ValueError(f"未找到模板 {template_name}，请检查 prompt.md")
    except Exception as e:
        logger.error(f"加载提示词模板失败: {e}")
        raise


def prompt_template_revision(language: str = "zh-CN") -> tuple[str, int, int]:
    """Return a cheap stat signature for the selected prompt template."""
    path = resolve_prompt_md_path()
    if normalize_prompt_language(language) == "en":
        english_path = path.with_name("prompt.en.md")
        if english_path.is_file():
            path = english_path
    try:
        stat = path.stat()
        return str(path), int(stat.st_mtime_ns), int(stat.st_size)
    except OSError:
        return str(path), 0, 0

# ==================== 自定义 HTTP 客户端（记录 OpenAI 请求/响应，供日志中的 token 统计）====================
class RequestResponseLogger(httpx.Client):
    """包装 httpx.Client，在 interactions 中追加脱敏后的请求与 usage。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.interactions = []
        self._trace_local = threading.local()

    def start_transport_trace(self) -> None:
        """Start a per-thread httpcore trace for the next OpenAI request."""
        self._trace_local.current = {
            "started_at": time.perf_counter(),
            "events": [],
            "metrics": {},
        }

    def snapshot_transport_trace(self) -> Dict[str, Any]:
        current = getattr(self._trace_local, "current", None)
        if not isinstance(current, dict):
            return {}
        started_at = float(current.get("started_at") or time.perf_counter())
        return {
            "elapsed_ms": int(max(0.0, (time.perf_counter() - started_at) * 1000.0)),
            "events": [dict(item) for item in current.get("events", [])],
            "metrics": dict(current.get("metrics") or {}),
        }

    def finish_transport_trace(self) -> Dict[str, Any]:
        snapshot = self.snapshot_transport_trace()
        try:
            del self._trace_local.current
        except AttributeError:
            pass
        return snapshot

    def send(self, request, *args, **kwargs):
        current = getattr(self._trace_local, "current", None)
        if isinstance(current, dict):
            started_at = float(current.get("started_at") or time.perf_counter())
            try:
                content_length = int(request.headers.get("content-length") or 0)
            except (TypeError, ValueError):
                content_length = 0
            current.setdefault("metrics", {})["request_bytes"] = max(0, content_length)

            def _trace(event_name: str, info: Dict[str, Any]) -> None:
                # httpcore trace names are intentionally retained verbatim so
                # upgrades do not silently collapse new transport phases.
                current["events"].append({
                    "event": str(event_name),
                    "at_ms": int(max(0.0, (time.perf_counter() - started_at) * 1000.0)),
                })

            request.extensions = dict(getattr(request, "extensions", {}) or {})
            request.extensions["trace"] = _trace
        response = super().send(request, *args, **kwargs)
        if isinstance(current, dict):
            try:
                response_length = int(response.headers.get("content-length") or 0)
            except (TypeError, ValueError):
                response_length = 0
            current.setdefault("metrics", {})["response_content_length"] = max(0, response_length)
        return response

    def request(self, method, url, **kwargs):
        headers = dict(kwargs.get("headers", {}))
        if "Authorization" in headers:
            headers["Authorization"] = "***REDACTED***"
        request_data = {
            "method": method,
            "url": str(url),
            "headers": headers,
            "body": kwargs.get("json") or kwargs.get("data") or None
        }
        response = super().request(method, url, **kwargs)
        response_data = {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": response.text
        }
        usage = None
        if hasattr(response, "json") and callable(response.json):
            try:
                resp_json = response.json()
                if "usage" in resp_json:
                    usage = resp_json["usage"]
            except Exception as e:
                logger.debug("Failed to parse response JSON for usage logging: %s", e)
        self.interactions.append({
            "request": request_data,
            "response": response_data,
            "usage": usage
        })
        return response

OPENAI_HTTP_TIMEOUT = float(os.getenv("OPENAI_HTTP_TIMEOUT", "300"))
executor_http_client = RequestResponseLogger(timeout=OPENAI_HTTP_TIMEOUT)

MAX_OUTPUT_TOKENS = model_profiles._safe_int(_INITIAL_MODEL_PROFILE.get("max_output_tokens"), 8192)


def _openai_sdk_base_url(for_local: bool) -> Optional[str]:
    if for_local:
        return LOCAL_LLM_HOST.rstrip("/") + "/v1"
    if OPENAI_BASE_URL:
        return OPENAI_BASE_URL.rstrip("/")
    return None


def _redact_runtime_log_text(value: Any) -> str:
    text = value if isinstance(value, str) else str(value)
    for item in (OPENAI_API_KEY, OPENAI_BASE_URL, LOCAL_LLM_HOST):
        if item:
            text = text.replace(str(item), "***")
    text = re.sub(r"https?://[^\s,;]+", "***", text)
    text = re.sub(
        r"(?i)\b(authorization\s*[:=]\s*bearer\s+|bearer\s+)[^\s,;]+",
        r"\1***",
        text,
    )
    text = re.sub(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*[^\s,;]+", r"\1=***", text)
    text = re.sub(r"(?i)\b(?:sk|tp)-[a-z0-9_-]{12,}\b", "***", text)
    text = re.sub(
        r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|key)=)[^&\s,;]+",
        r"\1***",
        text,
    )
    return text


def _masked_model_label(model_name: str) -> str:
    s = str(model_name or "").strip()
    return _redact_runtime_log_text(s) if s else "(empty)"


def _masked_base_label(value: Optional[str]) -> str:
    return "configured" if str(value or "").strip() else "default"


def _is_network_connectivity_error(exc: BaseException) -> bool:
    msg = str(exc or "").lower()
    if "timeout" in msg or "timed out" in msg:
        return True
    if "connection" in msg or "connect" in msg:
        return True
    try:
        from openai import APIConnectionError, APITimeoutError

        return isinstance(exc, (APIConnectionError, APITimeoutError))
    except Exception:
        return False


class LocalNetworkUnavailableError(ConnectionError):
    """The machine is offline, so provider fallback must not fan out."""


def _exception_http_status(exc: BaseException) -> Optional[int]:
    """Extract an HTTP status without retaining an SDK response object."""
    values = [getattr(exc, "status_code", None)]
    response = getattr(exc, "response", None)
    if response is not None:
        values.append(getattr(response, "status_code", None))
    for value in values:
        try:
            status = int(value)
        except (TypeError, ValueError):
            continue
        if 100 <= status <= 599:
            return status
    return None


class CandidateFailureSnapshot(RuntimeError):
    """Traceback-free, redacted failure retained by the run circuit breaker."""

    def __init__(
        self,
        *,
        candidate_key: str,
        model: str,
        provider: str,
        error: BaseException,
    ) -> None:
        self.candidate_key = str(candidate_key or "unknown")
        self.model = _masked_model_label(str(model or ""))
        self.provider = str(provider or "unknown")
        self.original_type = type(error).__name__
        self.status_code = _exception_http_status(error)
        message = _redact_runtime_log_text(error).replace("\r", " ").replace("\n", " ")
        self.redacted_message = (
            message if len(message) <= 800 else message[:799] + "…"
        )
        status = f" HTTP {self.status_code}" if self.status_code else ""
        super().__init__(f"{self.original_type}{status}: {self.redacted_message}")

    def summary(self) -> str:
        label = self.model or self.candidate_key
        return f"{label} ({self.provider}): {self}"


class ModelCandidatesUnavailableError(RuntimeError):
    """All candidates are circuit-broken, with safe per-candidate diagnostics."""

    def __init__(self, failures: List[CandidateFailureSnapshot]) -> None:
        super().__init__("all model candidates are unavailable for this run")
        self.candidate_failures = tuple(failures)


def _probe_network_connectivity(timeout: float = 0.75) -> bool:
    """Resolve WinINet false negatives with a small, configurable TCP probe."""
    configured = str(
        os.getenv("LOCAL_NETWORK_PROBE_TARGETS", "1.1.1.1:443,8.8.8.8:53")
        or ""
    )
    endpoints: List[Tuple[str, int]] = []
    for raw in configured.split(","):
        item = raw.strip()
        if not item:
            continue
        host, separator, port_text = item.rpartition(":")
        if not separator or not host:
            continue
        try:
            port = int(port_text)
        except (TypeError, ValueError):
            continue
        if 0 < port <= 65535:
            endpoints.append((host.strip(), port))
    for endpoint in endpoints:
        try:
            connection = socket.create_connection(endpoint, timeout=max(0.05, float(timeout)))
            connection.close()
            return True
        except OSError:
            continue
    return False


def machine_network_available() -> bool:
    """Return False only when the operating system positively reports offline."""
    if os.name != "nt":
        return True
    try:
        flags = ctypes.c_ulong(0)
        connected = ctypes.windll.wininet.InternetGetConnectedState(
            ctypes.byref(flags),
            0,
        )
        if connected:
            return True
        # WinINet can report offline while VPN/proxy routes are usable.  Only
        # suspend provider fallback when both the OS hint and an active probe
        # say the machine has no external route.
        return _probe_network_connectivity()
    except Exception:
        # An unavailable probe is not evidence of an outage. Keep the normal
        # provider/model fallback behavior in that case.
        logger.debug("Windows local network state probe unavailable", exc_info=True)
        return True


# 应用层已有候选模型切换与运行级重试；SDK 内部重试（默认 2 次、遵守
# Retry-After）只会把 429/5xx 的等待时间放大数倍，必须关闭。
_OPENAI_SDK_KWARGS = {"max_retries": 0}


def create_openai_client(
    model_name: str,
    model_type: str,
    role: str,
    http_client: Optional[httpx.Client] = None,
) -> Tuple[OpenAI, str]:
    """创建 OpenAI 兼容客户端；返回 (client, 实际请求的 model id)。"""
    try:
        if model_type == "openai":
            logger.info(
                "创建 %s 客户端 (OpenAI 兼容): model=%s, base_url=%s",
                role,
                _masked_model_label(model_name),
                _masked_base_label(OPENAI_BASE_URL),
            )
            client = OpenAI(
                api_key=OPENAI_API_KEY or "",
                base_url=_openai_sdk_base_url(False),
                http_client=http_client,
                timeout=OPENAI_HTTP_TIMEOUT,
                **_OPENAI_SDK_KWARGS,
            )
            return client, model_name
        if model_type == "local":
            resolved = LOCAL_LLM if LOCAL_LLM else model_name
            logger.info(
                "创建 %s 客户端 (本地 OpenAI 兼容 /v1): model=%s, base=%s",
                role,
                _masked_model_label(resolved),
                _masked_base_label(LOCAL_LLM_HOST),
            )
            client = OpenAI(
                api_key=_LOCAL_OPENAI_DUMMY_KEY,
                base_url=_openai_sdk_base_url(True),
                http_client=http_client,
                timeout=OPENAI_HTTP_TIMEOUT,
                **_OPENAI_SDK_KWARGS,
            )
            return client, resolved
        raise ValueError(f"不支持的 LLM 类型: {model_type}（需 openai 或 local）")
    except Exception as e:
        logger.error(
            "%s 客户端创建失败: %s；降级到本地 OpenAI 兼容服务",
            role,
            _redact_runtime_log_text(e),
        )
        resolved = LOCAL_LLM if LOCAL_LLM else model_name
        client = OpenAI(
            api_key=_LOCAL_OPENAI_DUMMY_KEY,
            base_url=_openai_sdk_base_url(True),
            http_client=http_client,
            timeout=OPENAI_HTTP_TIMEOUT,
            **_OPENAI_SDK_KWARGS,
        )
        return client, resolved


def create_openai_client_for_profile(
    profile: dict,
    role: str,
    http_client: Optional[httpx.Client] = None,
) -> Tuple[OpenAI, str]:
    """Create an OpenAI-compatible client from a saved model profile."""
    model_name = str(profile.get("model") or "").strip()
    base_url = str(profile.get("base_url") or "").strip().rstrip("/") or None
    api_key = str(profile.get("api_key") or "").strip()
    provider = resolve_profile_provider(profile)
    if provider.value == "openai-compatible" and not api_key:
        api_key = _LOCAL_OPENAI_DUMMY_KEY
    logger.info(
        "创建 %s 客户端 (模型档案): model=%s, base_url=%s",
        role,
        _masked_model_label(model_name),
        _masked_base_label(base_url),
    )
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=http_client,
        timeout=OPENAI_HTTP_TIMEOUT,
        **_OPENAI_SDK_KWARGS,
    )
    request_headers = model_profiles.profile_request_headers(profile)
    if request_headers:
        try:
            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                http_client=http_client,
                timeout=OPENAI_HTTP_TIMEOUT,
                default_headers=request_headers,
                **_OPENAI_SDK_KWARGS,
            )
        except Exception:
            logger.debug("模型档案自定义请求头不受支持，忽略", exc_info=True)
    try:
        setattr(
            client,
            "_myagent_input_modalities",
            model_profiles.profile_input_modalities(profile),
        )
        setattr(
            client,
            "_myagent_multimodal_input",
            model_profiles.profile_multimodal_input(profile),
        )
        setattr(
            client,
            "_myagent_thinking_format",
            _profile_thinking_format(profile),
        )
    except Exception:
        logger.debug("无法向模型客户端附加多模态能力元数据", exc_info=True)
    return client, model_name


def _candidate_input_modalities(item: Dict[str, Any]) -> set[str]:
    raw = item.get("input_modalities")
    if isinstance(raw, (list, tuple, set, frozenset)) and raw:
        return {str(value or "").strip().lower() for value in raw}
    if bool(item.get("multimodal_input")):
        return {"text", "image", "audio", "video", "file"}
    return {"text"}


class _FallbackCompletions:
    def __init__(
        self,
        candidates: List[Dict[str, Any]],
        status_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self._candidates = candidates
        self._status_callback = status_callback

    def set_status_callback(
        self,
        status_callback: Optional[Callable[[Dict[str, Any]], None]],
    ) -> None:
        self._status_callback = status_callback

    def _emit_model_switch_status(
        self,
        from_model: str,
        to_model: str,
        error: BaseException,
    ) -> None:
        error_text = _redact_runtime_log_text(error)
        event = {
            "type": "status",
            "content": (
                f"【模型自动切换】{_masked_model_label(from_model)} 调用失败，"
                f"已切换到 {_masked_model_label(to_model)}。\n"
                f"原模型错误：{error_text}"
            ),
            "model_switch": True,
            "from_model": from_model,
            "to_model": to_model,
            "error": error_text,
            "network_error": _is_network_connectivity_error(error),
        }
        cb = self._status_callback
        if not cb:
            return
        try:
            cb(event)
        except Exception:
            logger.debug("模型自动切换状态回调失败", exc_info=True)

    def create(self, **kwargs: Any) -> Any:
        last_error: Optional[BaseException] = None
        last_model = ""
        required_modalities = _api_messages_required_modalities(
            list(kwargs.get("messages") or [])
        )
        # Keep the user's selected profile as the main reasoning model.  When
        # it cannot read the attached media, the per-candidate text fallback
        # below preserves the media reference and asks it to delegate the
        # inspection through the task tool instead of silently replacing the
        # main model with a multimodal profile.
        candidates = list(self._candidates)
        for idx, item in enumerate(candidates):
            # The first candidate is covered by the caller's primary/retry/
            # hedge claim. Every additional fallback model consumes the same
            # logical request budget instead of multiplying retries invisibly.
            if idx > 0 and not _claim_additional_recovery_request():
                raise RuntimeError("LLM request budget exhausted before model fallback")
            call_kwargs = dict(kwargs)
            call_kwargs["messages"] = _remap_serialized_reasoning_format(
                list(call_kwargs.get("messages") or []),
                item.get("thinking_format") or "deepseek",
            )
            call_kwargs["model"] = item["model"]
            candidate_max_tokens = int(item.get("max_output_tokens") or MAX_OUTPUT_TOKENS)
            # The model profile that actually handles this attempt owns the
            # output budget.  Do not carry a smaller scenario/primary-model
            # limit into a fallback candidate.
            call_kwargs["max_tokens"] = candidate_max_tokens
            call_kwargs["temperature"] = float(item.get("temperature", EXECUTOR_TEMPERATURE))
            if item.get("extra_body") is not None:
                call_kwargs["extra_body"] = item.get("extra_body")
            else:
                call_kwargs.pop("extra_body", None)
            if item.get("reasoning_effort"):
                call_kwargs["reasoning_effort"] = item.get("reasoning_effort")
            else:
                call_kwargs.pop("reasoning_effort", None)
            request_has_media = _api_messages_have_media(
                list(call_kwargs.get("messages") or [])
            )
            if request_has_media and not required_modalities.issubset(
                _candidate_input_modalities(item)
            ):
                call_kwargs["messages"] = _serialized_messages_to_text_only(
                    list(call_kwargs.get("messages") or [])
                )
            try:
                if idx > 0:
                    if last_error is not None:
                        self._emit_model_switch_status(
                            last_model,
                            str(item.get("model") or ""),
                            last_error,
                        )
                    logger.warning(
                        "当前模型故障，按优先级切换到备用模型: %s",
                        _masked_model_label(str(item.get("model") or "")),
                    )
                retry_attempts, retry_backoff = _candidate_retry_policy()
                retry_index = 0
                while True:
                    try:
                        result = item["client"].chat.completions.create(**call_kwargs)
                        adopt = getattr(self, "_maybe_adopt_fallback_profile", None)
                        if callable(adopt):
                            adopt(item)
                        return result
                    except Exception as exc:
                        if _is_network_connectivity_error(exc) and not machine_network_available():
                            raise LocalNetworkUnavailableError(
                                "The local machine is offline; waiting for network recovery."
                            ) from exc
                        if (
                            retry_index < retry_attempts
                            and _classify_candidate_failure(exc) == "retry"
                            and _claim_additional_recovery_request()
                        ):
                            retry_index += 1
                            logger.warning(
                                "模型瞬时故障，同模型重试 %s/%s: model=%s error=%s",
                                retry_index,
                                retry_attempts,
                                _masked_model_label(str(item.get("model") or "")),
                                _redact_runtime_log_text(exc),
                            )
                            if retry_backoff > 0:
                                time.sleep(retry_backoff)
                            continue
                        if (
                            request_has_media
                            and required_modalities.issubset(
                                _candidate_input_modalities(item)
                            )
                            and _is_media_input_error(exc)
                        ):
                            rejected_modalities = _media_error_modalities(
                                exc, required_modalities
                            )
                            item["input_modalities"] = [
                                modality
                                for modality in (item.get("input_modalities") or [])
                                if modality not in rejected_modalities
                            ]
                            item["multimodal_input"] = any(
                                modality in {"image", "audio", "video", "file"}
                                for modality in item["input_modalities"]
                            )
                            mark_failed = item.get("mark_modalities_failed")
                            if callable(mark_failed):
                                mark_failed(sorted(rejected_modalities), exc)
                            else:
                                legacy_mark_failed = item.get("mark_multimodal_failed")
                                if callable(legacy_mark_failed):
                                    legacy_mark_failed(exc)
                            self._emit_multimodal_fallback_status(
                                str(item.get("model") or "")
                            )
                        raise
                raise
            except Exception as exc:
                if isinstance(exc, LocalNetworkUnavailableError):
                    # 内层重试循环已判定本机离线并包装；此处必须放行，
                    # 不能把“暂停回退”信号当作普通候选失败继续切换。
                    raise
                if _is_network_connectivity_error(exc) and not machine_network_available():
                    logger.info(
                        "本机网络不可用，暂停模型回退: model=%s",
                        _masked_model_label(str(item.get("model") or "")),
                    )
                    raise LocalNetworkUnavailableError(
                        "The local machine is offline; waiting for network recovery."
                    ) from exc
                last_error = exc
                last_model = str(item.get("model") or "")
                logger.warning(
                    "模型调用失败: model=%s error=%s",
                    _masked_model_label(str(item.get("model") or "")),
                    _redact_runtime_log_text(exc),
                )
        if last_error is not None:
            raise last_error
        raise RuntimeError("no model candidates configured")

    def _emit_multimodal_fallback_status(self, model: str) -> None:
        cb = self._status_callback
        if not cb:
            return
        try:
            cb(
                {
                    "type": "status",
                    "content": (
                        f"[提示] {_masked_model_label(model)} 拒绝了本次媒体输入，"
                        "已记录对应输入类型并尝试其他兼容模型"
                    ),
                    "multimodal_fallback": True,
                    "model": model,
                }
            )
        except Exception:
            logger.debug("多模态回退状态回调失败", exc_info=True)


class _ScopeClientRegistry:
    """Track which ExecutorLLMClient instances recently served each run scope.

    The failure circuit map is keyed by run id, but a manual model switch
    arrives with only the session id.  This registry bridges the two so the
    switch endpoint can drop the run's circuit and sticky-model records.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # scope -> weak-ish list of clients (bounded, deduplicated)
        self._by_scope: Dict[str, List["ExecutorLLMClient"]] = {}

    def register(self, scope: str, client: "ExecutorLLMClient") -> None:
        key = str(scope or "").strip()
        if not key:
            return
        with self._lock:
            clients = [c for c in self._by_scope.get(key, []) if c is not client]
            clients.append(client)
            self._by_scope[key] = clients[-8:]
            while len(self._by_scope) > 256:
                oldest = next(iter(self._by_scope))
                self._by_scope.pop(oldest, None)

    def reset_scope(self, scope: str) -> int:
        key = str(scope or "").strip()
        if not key:
            return 0
        reset_count = 0
        with self._lock:
            clients = list(self._by_scope.get(key, []))
        for client in clients:
            reset = getattr(client, "reset_failure_state", None)
            if callable(reset):
                try:
                    reset()
                    reset_count += 1
                except Exception:
                    logger.debug("重置客户端模型熔断状态失败", exc_info=True)
        with self._lock:
            self._by_scope.pop(key, None)
        return reset_count


_scope_client_registry = _ScopeClientRegistry()

# session_id -> {run scope, ...}：记录会话的 live run，供 fallback 接管后
# 把会话绑定同步为实际服务的模型。
_session_run_scopes: Dict[str, set] = {}


def adopt_fallback_profile_for_session(session_id: str, profile_id: str) -> bool:
    """Persist a fallback takeover as the session's bound model profile.

    用户要求：右下角选择器与实际使用的模型绑定。fallback 接管后把会话的
    ``model_profile_id`` 改写为实际服务的 profile，下一次请求以及前端
    选择器都以它为准；原失败的模型留在候选链里仍可被再次兜底。
    """
    sid = str(session_id or "").strip()
    pid = str(profile_id or "").strip()
    if not sid or not pid:
        return False
    try:
        meta = session_manager._load_metadata(sid)
        if not isinstance(meta, dict):
            meta = {}
        previous_pid = str(meta.get("model_profile_id") or "").strip()
        if previous_pid == pid:
            return True
        meta["model_profile_id"] = pid
        meta["updated_at"] = datetime.now().isoformat()
        history = meta.get("model_switch_history")
        if not isinstance(history, list):
            history = []
        meta["model_switch_history"] = [
            *history[-49:],
            {
                "switch_id": uuid.uuid4().hex,
                "from_profile_id": previous_pid,
                "to_profile_id": pid,
                "requested_by": "fallback",
                "switched_at": datetime.now(timezone.utc).isoformat(),
            },
        ]
        with session_manager._session_metadata_lock(sid):
            session_manager._save_metadata_unlocked(sid, meta)
        _invalidate_executor_config_cache(sid)
        return True
    except Exception:
        logger.debug("fallback 接管后同步会话模型绑定失败", exc_info=True)
        return False


def reset_executor_failure_state_for_session(session_id: str) -> int:
    """Clear run-scoped model circuits for a session's live runs.

    Manual profile switches keep the failure circuit and the sticky
    last-successful candidate by design; this helper exists for paths that
    explicitly need a clean slate (currently unused by the switch endpoint).
    """
    sid = str(session_id or "").strip()
    if not sid:
        return 0
    scopes: List[str] = []
    with _executor_failure_lock:
        scopes = list(_session_run_scopes.get(sid, set()))
    reset_count = 0
    for scope in scopes:
        reset_count += _scope_client_registry.reset_scope(scope)
    _session_run_scopes.pop(sid, None)
    return reset_count


class _FallbackChat:
    def __init__(
        self,
        candidates: List[Dict[str, Any]],
        status_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.completions = _FallbackCompletions(candidates, status_callback)

    def set_status_callback(
        self,
        status_callback: Optional[Callable[[Dict[str, Any]], None]],
    ) -> None:
        self.completions.set_status_callback(status_callback)


class ExecutorLLMClient:
    """Executor facade that retries provider-neutral model transports."""

    def __init__(
        self,
        candidates: List[Dict[str, Any]],
        *,
        failure_lock: Optional[Any] = None,
        failed_candidates_by_scope: Optional[
            Dict[str, Dict[str, CandidateFailureSnapshot]]
        ] = None,
    ):
        self.candidates = candidates
        self.chat = _FallbackChat(candidates)
        self._failure_lock = failure_lock or threading.RLock()
        self._request_scope = ""
        self._bound_session_id = ""
        self._fallback_adopted_callback: Optional[Callable[[str, str, str], None]] = None
        self._failed_candidates_by_scope = (
            failed_candidates_by_scope
            if failed_candidates_by_scope is not None
            else {}
        )
        self._last_successful_candidate_key = ""
        # Historical marker kept for duck-typing compatibility.  It no longer
        # gates the first-token hedge: agent_openai applies the same hedge to
        # every logical request (facade or bare client) and relies on
        # _LogicalRequestBudget to cap physical duplicates.  Set
        # OPENAI_HEDGE_MAX_ATTEMPTS=0 to disable hedging globally.
        self._myagent_logical_fallback_owner = True
        self._myagent_transport_enabled = bool(
            candidates and all(item.get("transport") is not None for item in candidates)
        )
        first = candidates[0] if candidates else {}
        # 规范序列化标记：先保留 <think> 内容 + reasoning_content 字段，
        # 具体目标格式在 _FallbackCompletions.create 内按候选 remap。
        self._myagent_thinking_format = "canonical"
        preferred_modalities = _candidate_input_modalities(first) if first else {"text"}
        self._myagent_input_modalities = sorted(preferred_modalities or {"text"})
        self._myagent_multimodal_input = bool(
            preferred_modalities & {"image", "audio", "video", "file"}
        )
        self._myagent_mark_multimodal_failed = None
        self._myagent_mark_modalities_failed = lambda _modalities, _error: None

    def set_request_scope(self, scope: str) -> None:
        """Select the run whose failed-provider circuit should be reused."""
        value = str(scope or "").strip()
        with self._failure_lock:
            self._request_scope = value
            if value:
                if not isinstance(self._failed_candidates_by_scope.get(value), dict):
                    # A running process may still hold the pre-upgrade set shape.
                    # Its reasons were never retained, so retry those candidates.
                    self._failed_candidates_by_scope[value] = {}
                # 记录 scope → 最近使用该 scope 的客户端实例，供手动切换
                # 模型时按会话清空熔断记录。
                _scope_client_registry.register(value, self)
            while len(self._failed_candidates_by_scope) > 128:
                oldest = next(iter(self._failed_candidates_by_scope))
                self._failed_candidates_by_scope.pop(oldest, None)

    def reset_failure_state(self) -> None:
        """Drop run-scoped failure circuits and the sticky successful model.

        Called when the user manually switches the session model: the new
        choice must start clean instead of being skipped because the same
        profile failed earlier in the current run, and UI-facing
        ``current_candidate`` must not keep reporting a model the user just
        replaced.
        """
        with self._failure_lock:
            scope = self._request_scope
            if scope:
                self._failed_candidates_by_scope.pop(scope, None)
            self._last_successful_candidate_key = ""

    def clear_latest_request_scope_failure(self) -> None:
        """Retry the most recent candidate after rewriting an invalid request."""
        with self._failure_lock:
            scope = self._request_scope
            failures = self._failed_candidates_by_scope.get(scope)
            if not scope or not isinstance(failures, dict) or not failures:
                return
            latest_key = next(reversed(failures))
            failures.pop(latest_key, None)
            if not failures:
                self._failed_candidates_by_scope.pop(scope, None)

    def note_scope_session(self, session_id: str) -> None:
        """Associate the current run scope with its session for later resets."""
        sid = str(session_id or "").strip()
        with self._failure_lock:
            scope = self._request_scope
        if not sid or not scope:
            return
        self._bound_session_id = sid
        with _executor_failure_lock:
            scopes = _session_run_scopes.setdefault(sid, set())
            scopes.add(scope)
            while len(scopes) > 8:
                scopes.discard(next(iter(scopes)))
            while len(_session_run_scopes) > 512:
                oldest = next(iter(_session_run_scopes))
                _session_run_scopes.pop(oldest, None)

    def _maybe_adopt_fallback_profile(self, candidate: Dict[str, Any]) -> None:
        """Bind the session to the profile that actually served the request.

        用户要求“使用模型与右下角选择绑定”：fallback 接管成功后把会话
        绑定改写为实际服务的 profile，前端选择器随事件流同步刷新。
        熔断记录与粘滞的“最近成功模型”保持不变——那是兜底机制的基石。
        """
        sid = str(self._bound_session_id or "").strip()
        if not sid:
            return
        pid = str(candidate.get("profile_id") or "").strip()
        if not pid:
            return
        try:
            meta = session_manager._load_metadata(sid)
            bound = str((meta or {}).get("model_profile_id") or "").strip()
        except Exception:
            bound = ""
        if bound == pid:
            return
        if adopt_fallback_profile_for_session(sid, pid):
            cb = self._fallback_adopted_callback
            if callable(cb):
                try:
                    cb(sid, pid, str(candidate.get("model") or ""))
                except Exception:
                    logger.debug("fallback 接管通知回调失败", exc_info=True)

    def current_candidate(self) -> Dict[str, Any]:
        """Return the model that most recently completed a main-agent request."""
        with self._failure_lock:
            successful_key = self._last_successful_candidate_key
        if successful_key:
            for index, item in enumerate(self.candidates):
                if self._candidate_circuit_key(index, item) == successful_key:
                    return dict(item)
        return dict(self.candidates[0]) if self.candidates else {}

    def next_candidate(self) -> Dict[str, Any]:
        """Return the candidate the next main request will try first."""
        with self._failure_lock:
            failed = set(
                self._failed_candidates_by_scope.get(self._request_scope, set())
            )
        for index, item in enumerate(self.candidates):
            if self._candidate_circuit_key(index, item) not in failed:
                return dict(item)
        return dict(self.candidates[0]) if self.candidates else {}

    def compact_history(self, **kwargs: Any) -> Any:
        """Use the active provider's native compaction without cross-provider fallback."""
        candidate = self.next_candidate()
        transport = candidate.get("transport") if isinstance(candidate, dict) else None
        compact = getattr(transport, "compact_history", None)
        if not callable(compact):
            raise NotImplementedError("active LLM provider has no native compaction")
        call_kwargs = dict(kwargs)
        call_kwargs["model"] = str(candidate.get("model") or call_kwargs.get("model") or "")
        call_kwargs["messages"] = _remap_serialized_reasoning_format(
            list(call_kwargs.get("messages") or []),
            candidate.get("thinking_format") or "deepseek",
        )
        return compact(**call_kwargs)

    def complete_text(
        self,
        *,
        response_validator: Optional[Callable[[Dict[str, Any]], bool]] = None,
        include_candidate_controls: bool = True,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Run one stateless text request through each candidate's native protocol.

        One-shot background work must not share the main ReAct retry budget: a
        failing primary profile should not consume every retry before a healthy
        backup profile gets a chance to answer.
        """
        last_error: Optional[BaseException] = None
        last_model = ""
        for index, item in enumerate(self.candidates):
            model = str(item.get("model") or "")
            transport = item.get("transport")
            if transport is None:
                last_error = RuntimeError("model candidate has no LLM transport")
                continue
            call_kwargs = dict(kwargs)
            call_kwargs["messages"] = _messages_to_params_for_client(
                item.get("client"),
                list(call_kwargs.get("messages") or []),
                thinking_format=item.get("thinking_format") or "deepseek",
            )
            call_kwargs["model"] = model
            candidate_max_tokens = int(
                item.get("max_output_tokens") or MAX_OUTPUT_TOKENS
            )
            call_kwargs["max_tokens"] = candidate_max_tokens
            if call_kwargs.get("temperature") is None:
                call_kwargs["temperature"] = float(
                    item.get("temperature", EXECUTOR_TEMPERATURE)
                )
            if include_candidate_controls:
                if item.get("extra_body") is not None:
                    call_kwargs["extra_body"] = item.get("extra_body")
                if item.get("reasoning_effort"):
                    call_kwargs["reasoning_effort"] = item.get("reasoning_effort")
            else:
                call_kwargs.pop("extra_body", None)
                call_kwargs.pop("reasoning_effort", None)
            try:
                if index > 0:
                    if last_error is not None:
                        self.chat.completions._emit_model_switch_status(
                            last_model,
                            model,
                            last_error,
                        )
                    logger.warning(
                        "一次性模型请求自动切换: from=%s to=%s purpose=%s",
                        _masked_model_label(last_model),
                        _masked_model_label(model),
                        LLMRequestContext.from_value(
                            call_kwargs.get("request_context")
                        ).purpose.value,
                    )
                result = transport.complete_text(**call_kwargs)
                if not isinstance(result, dict):
                    raise ValueError("LLM transport returned a non-object completion")
                if response_validator is not None and not response_validator(result):
                    raise ValueError("LLM transport returned an unusable completion")
                result = dict(result)
                result["_myagent_candidate"] = {
                    "profile_id": str(item.get("profile_id") or ""),
                    "provider": str(item.get("provider") or ""),
                    "model": model,
                }
                return result
            except Exception as exc:
                if _is_network_connectivity_error(exc) and not machine_network_available():
                    raise LocalNetworkUnavailableError(
                        "The local machine is offline; waiting for network recovery."
                    ) from exc
                last_error = exc
                last_model = model
                logger.warning(
                    "一次性模型请求失败: model=%s provider=%s error=%s",
                    _masked_model_label(model),
                    item.get("provider") or "unknown",
                    _redact_runtime_log_text(exc),
                )
        if last_error is not None:
            raise last_error
        raise RuntimeError("no model candidates configured")

    @staticmethod
    def _candidate_circuit_key(index: int, item: Dict[str, Any]) -> str:
        return str(item.get("profile_id") or f"candidate:{index}")

    def stream_completion(self, **kwargs: Any) -> Any:
        """Return a normalized stream, switching profiles only before first output."""
        return self._stream_completion_iter(dict(kwargs))

    def _stream_completion_iter(self, kwargs: Dict[str, Any]) -> Any:
        last_error: Optional[BaseException] = None
        last_model = ""
        request_context = LLMRequestContext.from_value(kwargs.get("request_context"))
        background_text_request = request_context.purpose is not LLMRequestPurpose.MAIN
        with self._failure_lock:
            request_scope = self._request_scope
            failed_candidates = set(
                self._failed_candidates_by_scope.get(request_scope, set())
            )
        required_modalities = _api_messages_required_modalities(
            list(kwargs.get("messages") or [])
        )
        attempted_candidates = 0
        for idx, item in enumerate(self.candidates):
            circuit_key = self._candidate_circuit_key(idx, item)
            if circuit_key in failed_candidates:
                logger.info(
                    "本轮运行跳过已失败模型: model=%s provider=%s",
                    _masked_model_label(str(item.get("model") or "")),
                    item.get("provider") or "unknown",
                )
                continue
            if attempted_candidates > 0 and not _claim_additional_recovery_request():
                raise RuntimeError("LLM request budget exhausted before model fallback")
            call_kwargs = dict(kwargs)
            call_kwargs["messages"] = _remap_serialized_reasoning_format(
                list(call_kwargs.get("messages") or []),
                item.get("thinking_format") or "deepseek",
            )
            call_kwargs["model"] = item["model"]
            candidate_max_tokens = int(item.get("max_output_tokens") or MAX_OUTPUT_TOKENS)
            call_kwargs["max_tokens"] = candidate_max_tokens
            call_kwargs["temperature"] = float(
                item.get("temperature", EXECUTOR_TEMPERATURE)
            )
            if item.get("extra_body") is not None:
                call_kwargs["extra_body"] = item.get("extra_body")
            else:
                call_kwargs.pop("extra_body", None)
            if item.get("reasoning_effort"):
                call_kwargs["reasoning_effort"] = item.get("reasoning_effort")
            else:
                call_kwargs.pop("reasoning_effort", None)
            request_has_media = _api_messages_have_media(
                list(call_kwargs.get("messages") or [])
            )
            if request_has_media and not required_modalities.issubset(
                _candidate_input_modalities(item)
            ):
                call_kwargs["messages"] = _serialized_messages_to_text_only(
                    list(call_kwargs.get("messages") or [])
                )
            transport = item.get("transport")
            if transport is None:
                raise RuntimeError("model candidate has no LLM transport")
            emitted_output = False
            retry_attempts, retry_backoff = _candidate_retry_policy()
            retry_index = 0
            while True:
                try:
                    if attempted_candidates > 0:
                        if last_error is not None:
                            self.chat.completions._emit_model_switch_status(
                                last_model,
                                str(item.get("model") or ""),
                                last_error,
                            )
                        logger.warning(
                            "当前模型故障，按优先级切换到备用模型: %s",
                            _masked_model_label(str(item.get("model") or "")),
                        )
                    attempted_candidates += 1
                    pending_events: List[TransportEvent] = []
                    emitted_content = False
                    _attempt_started = time.perf_counter()
                    for event in transport.stream_completion(**call_kwargs):
                        if background_text_request and not emitted_content:
                            pending_events.append(event)
                            if (
                                isinstance(event, TransportEvent)
                                and event.kind == "content_delta"
                                and bool(event.text)
                            ):
                                emitted_content = True
                                emitted_output = True
                                yield from pending_events
                                pending_events = []
                            continue
                        if isinstance(event, TransportEvent) and event.is_first_token:
                            emitted_output = True
                        yield event
                    if background_text_request and not emitted_content:
                        raise ValueError("background LLM stream returned no text content")
                    with self._failure_lock:
                        self._last_successful_candidate_key = circuit_key
                    # 会话绑定跟随实际服务者（右下角选择器同步）；仅在
                    # fallback 接管（非第一候选）或绑定漂移时真正写盘。
                    self._maybe_adopt_fallback_profile(item)
                    return
                except Exception as exc:
                    # Once visible output exists, switching providers would splice two
                    # different answers into one assistant turn.
                    if emitted_output:
                        raise
                    if _is_network_connectivity_error(exc) and not machine_network_available():
                        raise LocalNetworkUnavailableError(
                            "The local machine is offline; waiting for network recovery."
                        ) from exc
                    if (
                        retry_index < retry_attempts
                        and _classify_candidate_failure(exc) == "retry"
                        and _claim_additional_recovery_request()
                    ):
                        retry_index += 1
                        logger.warning(
                            "模型瞬时故障，同模型重试 %s/%s: model=%s error=%s",
                            retry_index,
                            retry_attempts,
                            _masked_model_label(str(item.get("model") or "")),
                            _redact_runtime_log_text(exc),
                        )
                        if retry_backoff > 0:
                            time.sleep(retry_backoff)
                        continue
                    if (
                        request_has_media
                        and required_modalities.issubset(_candidate_input_modalities(item))
                        and _is_media_input_error(exc)
                    ):
                        rejected_modalities = _media_error_modalities(exc, required_modalities)
                        item["input_modalities"] = [
                            modality
                            for modality in (item.get("input_modalities") or [])
                            if modality not in rejected_modalities
                        ]
                        item["multimodal_input"] = any(
                            modality in {"image", "audio", "video", "file"}
                            for modality in item["input_modalities"]
                        )
                        mark_failed = item.get("mark_modalities_failed")
                        if callable(mark_failed):
                            mark_failed(sorted(rejected_modalities), exc)
                        self.chat.completions._emit_multimodal_fallback_status(
                            str(item.get("model") or "")
                        )
                    media_only_failure = request_has_media and _is_media_input_error(exc)
                    if request_scope and not media_only_failure:
                        with self._failure_lock:
                            scoped_failures = self._failed_candidates_by_scope.get(
                                request_scope
                            )
                            if not isinstance(scoped_failures, dict):
                                # Normalize a legacy in-memory set after hot reload.
                                scoped_failures = {}
                                self._failed_candidates_by_scope[request_scope] = (
                                    scoped_failures
                                )
                            # 保留该候选最近一次的真实异常，供"全部候选不可用"
                            # 时沿异常链透传真实原因（403/400/429…），避免
                            # 错误信息被笼统的 RuntimeError 覆盖。
                            snapshot = CandidateFailureSnapshot(
                                candidate_key=circuit_key,
                                model=str(item.get("model") or ""),
                                provider=str(item.get("provider") or "unknown"),
                                error=exc,
                            )
                            # Reinsert so dict order represents failure recency even
                            # when concurrent hedges update the same candidate.
                            scoped_failures.pop(circuit_key, None)
                            scoped_failures[circuit_key] = snapshot
                        failed_candidates.add(circuit_key)
                    last_error = exc
                    last_model = str(item.get("model") or "")
                    logger.warning(
                        "模型调用失败: model=%s provider=%s ms=%s error=%s",
                        _masked_model_label(last_model),
                        item.get("provider") or "unknown",
                        int(max(0.0, (time.perf_counter() - _attempt_started) * 1000)),
                        _redact_runtime_log_text(exc),
                    )
                    break
        if last_error is not None:
            raise last_error
        if failed_candidates:
            retained_failures: List[CandidateFailureSnapshot] = []
            with self._failure_lock:
                failures = self._failed_candidates_by_scope.get(request_scope)
                if isinstance(failures, dict) and failures:
                    retained_failures = [
                        failure
                        for failure in failures.values()
                        if isinstance(failure, CandidateFailureSnapshot)
                    ]
            if retained_failures:
                raise ModelCandidatesUnavailableError(retained_failures) from (
                    retained_failures[-1]
                )
            raise RuntimeError("all model candidates are unavailable for this run")
        raise RuntimeError("no model candidates configured")

    def set_status_callback(
        self,
        status_callback: Optional[Callable[[Dict[str, Any]], None]],
    ) -> None:
        self.chat.set_status_callback(status_callback)


# Backward-compatible public name for plugins/tests that imported the old
# OpenAI-specific facade before the transport layer became provider-neutral.
FallbackOpenAIClient = ExecutorLLMClient


if _INITIAL_MODEL_PROFILE:
    executor_client, executor_model = create_openai_client_for_profile(
        _INITIAL_MODEL_PROFILE,
        "executor",
        http_client=executor_http_client,
    )
else:
    executor_client, executor_model = None, ""


def refresh_executor_client_from_env() -> None:
    """
    Reload non-model .env settings and the first usable model profile, then
    rebuild the compatibility executor globals shared with agent_loop/memory.
    联网搜索（WEB_SEARCH_*）在 agent_tools 内按次读 os.environ，load_app_dotenv 后即生效。
    """
    global OPENAI_API_KEY, OPENAI_BASE_URL, executor_client, executor_model
    global EXECUTOR_LLM, EXECUTOR_LLM_TYPE, MAX_OUTPUT_TOKENS
    global CONTEXT_WINDOW, CONTEXT_KEEP_RECENT_TURNS, MAX_REACT_ITER, SUBAGENT_MAX_REACT_ITER
    global TOOL_RESULT_TRUNCATE_KEEP_CHARS, LLM_CONTEXT_TRUNCATE_KEEP_CHARS
    global CONTEXT_COMPRESS_FAILURE_MAX_TOKENS, CONTEXT_COMPRESS_MAX_ROUNDS, CONTEXT_COMPRESS_ROUND3_MAX_REACT
    global CONTEXT_COMPRESS_TARGET_RATIO
    global EXECUTOR_EXTRA_BODY, EXECUTOR_REASONING_EFFORT
    global EXECUTOR_TEMPERATURE, EXECUTOR_THINKING_MODE, EXECUTOR_EXTRA_BODY_JSON
    global EXECUTOR_REASONING_EFFORT_RAW, LOCAL_LLM_HOST, LOCAL_LLM

    load_app_dotenv()
    refresh_request_recovery_config_from_env()
    _register_legacy_dotenv_model_profile()

    profile = model_profiles.top_profile(PROJECT_ROOT) or {}
    EXECUTOR_LLM = str(profile.get("model") or "").strip()
    EXECUTOR_LLM_TYPE = resolve_profile_provider(profile).value if profile else "openai"
    MAX_OUTPUT_TOKENS = model_profiles._safe_int(profile.get("max_output_tokens"), 8192)
    CONTEXT_WINDOW = model_profiles._safe_int(profile.get("context_window"), 128000)
    try:
        EXECUTOR_TEMPERATURE = float(str(profile.get("temperature") or "0.7"))
    except ValueError:
        EXECUTOR_TEMPERATURE = 0.7
    EXECUTOR_THINKING_MODE = str(profile.get("thinking_mode") or "").strip()
    EXECUTOR_EXTRA_BODY_JSON = str(profile.get("extra_body_json") or "").strip()
    EXECUTOR_REASONING_EFFORT_RAW = str(profile.get("reasoning_effort") or "").strip()
    OPENAI_BASE_URL = str(profile.get("base_url") or "").strip()
    OPENAI_API_KEY = str(profile.get("api_key") or "").strip()
    LOCAL_LLM_HOST = OPENAI_BASE_URL.rstrip("/") or "http://localhost:11434"
    LOCAL_LLM = EXECUTOR_LLM
    CONTEXT_KEEP_RECENT_TURNS = _context_env_int("CONTEXT_KEEP_RECENT_TURNS", "3")
    TOOL_RESULT_TRUNCATE_KEEP_CHARS = _tool_result_truncate_keep_chars_from_env()
    LLM_CONTEXT_TRUNCATE_KEEP_CHARS = TOOL_RESULT_TRUNCATE_KEEP_CHARS
    MAX_REACT_ITER = int(os.getenv("MAX_REACT_ITER", "100"))
    SUBAGENT_MAX_REACT_ITER = max(1, int(os.getenv("SUBAGENT_MAX_REACT_ITER", "100")))
    _failure_cap_raw = os.getenv("CONTEXT_COMPRESS_FAILURE_MAX_TOKENS")
    CONTEXT_COMPRESS_FAILURE_MAX_TOKENS = (
        int(_failure_cap_raw)
        if (_failure_cap_raw is not None and str(_failure_cap_raw).strip() != "")
        else max(4096, int(CONTEXT_WINDOW) // 2)
    )
    CONTEXT_COMPRESS_MAX_ROUNDS = _context_env_int("CONTEXT_COMPRESS_MAX_ROUNDS", "3")
    CONTEXT_COMPRESS_ROUND3_MAX_REACT = _context_env_int("CONTEXT_COMPRESS_ROUND3_MAX_REACT", "10")
    CONTEXT_COMPRESS_TARGET_RATIO = float(os.getenv("CONTEXT_COMPRESS_TARGET_RATIO", "0.6"))

    if not profile:
        logging.getLogger(__name__).warning(
            "没有可用的 model profile；对话/API 调用不可用。"
        )

    EXECUTOR_EXTRA_BODY = _load_executor_extra_body()
    EXECUTOR_REASONING_EFFORT = _executor_reasoning_effort()

    if profile:
        executor_client, executor_model = create_openai_client_for_profile(
            profile,
            "executor",
            http_client=executor_http_client,
        )
    else:
        executor_client, executor_model = None, ""
    import agent_loop as _agent_loop
    import agent_memory as _agent_memory

    _agent_loop.executor_client = executor_client
    _agent_loop.executor_model = executor_model
    _agent_loop.MAX_REACT_ITER = MAX_REACT_ITER
    _agent_loop.SUBAGENT_MAX_REACT_ITER = SUBAGENT_MAX_REACT_ITER
    _agent_loop.MAX_OUTPUT_TOKENS = MAX_OUTPUT_TOKENS
    _agent_loop.CONTEXT_WINDOW = CONTEXT_WINDOW
    _agent_loop.CONTEXT_COMPRESS_FAILURE_MAX_TOKENS = CONTEXT_COMPRESS_FAILURE_MAX_TOKENS
    _agent_loop.EXECUTOR_EXTRA_BODY = EXECUTOR_EXTRA_BODY
    _agent_loop.EXECUTOR_REASONING_EFFORT = EXECUTOR_REASONING_EFFORT
    _agent_loop.LLM_CONTEXT_TRUNCATE_KEEP_CHARS = LLM_CONTEXT_TRUNCATE_KEEP_CHARS

    _agent_memory.CONTEXT_WINDOW = CONTEXT_WINDOW
    _agent_memory.CONTEXT_KEEP_RECENT_TURNS = CONTEXT_KEEP_RECENT_TURNS
    _agent_memory.CONTEXT_COMPRESS_FAILURE_MAX_TOKENS = CONTEXT_COMPRESS_FAILURE_MAX_TOKENS
    _agent_memory.CONTEXT_COMPRESS_MAX_ROUNDS = CONTEXT_COMPRESS_MAX_ROUNDS
    _agent_memory.CONTEXT_COMPRESS_ROUND3_MAX_REACT = CONTEXT_COMPRESS_ROUND3_MAX_REACT
    _agent_memory.CONTEXT_COMPRESS_TARGET_RATIO = CONTEXT_COMPRESS_TARGET_RATIO
    _invalidate_executor_config_cache()


def executor_one_shot_complete(
    messages: List[Any],
    *,
    session_id: str = "",
    purpose: LLMRequestPurpose = LLMRequestPurpose.SUMMARY,
    temperature: Optional[float] = None,
    timeout: Optional[float] = None,
    response_validator: Optional[Callable[[Dict[str, Any]], bool]] = None,
    include_candidate_controls: bool = True,
) -> Dict[str, Any]:
    """Complete independent background work without exposing session identity."""
    candidates = resolve_executor_candidates_for_session(session_id)
    if not candidates:
        raise RuntimeError("no usable model profile configured")
    client = ExecutorLLMClient(candidates)
    kwargs: Dict[str, Any] = {
        "messages": messages,
        "request_context": LLMRequestContext(
            session_id=str(session_id or "").strip(),
            purpose=purpose,
            server_storage_allowed=False,
        ),
    }
    if temperature is not None:
        kwargs["temperature"] = float(temperature)
    if timeout is not None:
        kwargs["timeout"] = float(timeout)
    if response_validator is None:
        response_validator = lambda value: bool(
            str(value.get("text") or "").strip()
            and not str(value.get("refusal") or "").strip()
            and not str(value.get("error") or "").strip()
        )
    return client.complete_text(
        response_validator=response_validator,
        include_candidate_controls=include_candidate_controls,
        **kwargs,
    )


def executor_text_complete(prompt: str, session_id: str = "") -> str:
    """单轮补全，走执行端（压缩摘要、key_context 条目、会话标题等）。"""
    result = executor_one_shot_complete(
        [UserMessage(content=prompt)],
        session_id=session_id,
        purpose=LLMRequestPurpose.SUMMARY,
        temperature=EXECUTOR_TEMPERATURE,
    )
    return str(result.get("text") or "").strip()


def executor_chat_complete(messages: List[Any], session_id: str = "") -> str:
    """多轮 chat，走执行端（compress_history_and_key 等结构化上送）。"""
    result = executor_one_shot_complete(
        messages,
        session_id=session_id,
        purpose=LLMRequestPurpose.SUMMARY,
        temperature=EXECUTOR_TEMPERATURE,
    )
    return str(result.get("text") or "").strip()


def executor_chat_complete_stream(
    messages: List[Any],
    on_content_delta: Optional[Callable[[str], None]] = None,
    session_id: str = "",
) -> str:
    """
    执行端多轮 chat 流式补全；每收到 content 片段即回调 on_content_delta（供压缩/要点 SSE 推送）。
    返回完整正文（与 executor_chat_complete 一致）。
    """
    buffer_only = bool(cpu_pressure.snapshot().degraded)

    import queue as _queue

    sync_q: _queue.Queue = _queue.Queue()
    client, model, max_tokens, _ctx = resolve_executor_config_for_session(session_id)

    def _worker() -> None:
        run_chat_completion_stream_worker(
            sync_q,
            client,
            model,
            messages,
            tools=None,
            temperature=EXECUTOR_TEMPERATURE,
            max_tokens=max_tokens,
            emit_deltas=not buffer_only,
            request_context=LLMRequestContext(
                session_id=str(session_id or "").strip(),
                purpose=LLMRequestPurpose.SUMMARY,
                server_storage_allowed=False,
            ),
        )

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    parts: List[str] = []
    buffered_final = ""
    err: Optional[BaseException] = None
    while True:
        item = sync_q.get()
        if item is None:
            break
        tag, payload = item
        if tag == "content" and payload:
            piece = payload if isinstance(payload, str) else str(payload)
            parts.append(piece)
            if on_content_delta:
                try:
                    on_content_delta(piece)
                except Exception:
                    pass
        elif tag == "turn" and payload is not None:
            buffered_final = str(getattr(payload, "content", "") or "")
        elif tag == "err" and isinstance(payload, BaseException):
            err = payload
    t.join()
    if err is not None:
        raise err
    text = "".join(parts) or buffered_final
    if buffer_only and text and on_content_delta:
        try:
            on_content_delta(text)
        except Exception:
            pass
    return text


def executor_text_and_usage(
    prompt: str,
    session_id: str = "",
) -> Tuple[str, Optional[Dict[str, int]]]:
    """与 executor_text_complete 相同，返回 usage。"""
    result = executor_one_shot_complete(
        [UserMessage(content=prompt)],
        session_id=session_id,
        purpose=LLMRequestPurpose.SUMMARY,
        temperature=EXECUTOR_TEMPERATURE,
    )
    usage = result.get("usage")
    return (
        str(result.get("text") or "").strip(),
        dict(usage) if isinstance(usage, dict) else None,
    )

# ==================== 从 ui_events 还原主对话链（与 SSE 同源）====================
def rebuild_core_messages_from_ui_events(events: List[dict]) -> List:
    """
    从 ui_events 还原仅含 user / final 主链，用于截断后重建 dialogue、工作消息、llm_history。
    """
    out: List = []
    for e in events:
        if not isinstance(e, dict):
            continue
        t = e.get("type")
        if t == "user":
            out.append(UserMessage(content=str(e.get("content", ""))))
        elif t == "final":
            out.append(AssistantMessage(content=str(e.get("content", ""))))
    return out


def _is_compress_recap_user_dict(d: dict) -> bool:
    if d.get("type") != "user":
        return False
    return str(d.get("content") or "").lstrip().startswith(COMPACT_RECAP_USER_PREFIX)


def _is_micro_shrink_user_dict(d: dict) -> bool:
    """落盘 dict：微压段中的 user，与 `[压缩摘要]` 一样不计入用户轮。"""
    if d.get("type") != "user":
        return False
    md = d.get("metadata")
    return isinstance(md, dict) and bool(md.get("micro_shrink"))


def _dict_is_micro_shrink_legacy_user(d: dict) -> bool:
    """微压 legacy user：metadata 标记或正文含微压省略标记。"""
    if _is_micro_shrink_user_dict(d):
        return True
    if d.get("type") != "user":
        return False
    return "已微压省略" in str(d.get("content") or "")


def _strip_micro_shrink_legacy_user_turns(msg_dicts: List[dict]) -> List[dict]:
    """
    去掉微压区 metadata.micro_shrink 的 user 轮及其后至下一条 user 前的 assistant/tool。
    分支/改写已压缩会话时，该段多为摘要前的 legacy 副本，不应与尾窗或 ui 保留问句并存。
    """
    out: List[dict] = []
    i, n = 0, len(msg_dicts or [])
    while i < n:
        d = msg_dicts[i]
        if isinstance(d, dict) and _dict_is_micro_shrink_legacy_user(d):
            i += 1
            while i < n:
                nd = msg_dicts[i]
                if isinstance(nd, dict) and nd.get("type") == "user":
                    break
                i += 1
            continue
        out.append(d)
        i += 1
    return out


def _counts_toward_session_user_turns_dict(d: dict) -> bool:
    """计入与 ui_events 用户数对齐裁剪的真人 user。"""
    return isinstance(d, dict) and d.get("type") == "user" and not _is_compress_recap_user_dict(
        d
    ) and not _is_micro_shrink_user_dict(d)


def _normalize_user_plain_for_rewrite_match(text: str) -> str:
    return " ".join((text or "").strip().split())


def _rewrite_user_plain_matches_relaxed(rew_plain_norm: str, stored_raw: str) -> bool:
    """
    判断「被改写的 user 文本」是否对应这条落盘 user。
    - 完全 normalize 后相等则命中。
    - 微压/截断后正文可能短于 UI 原问：取「已微压省略」前、首行前的片段与 rew 比前缀。
    - 仍不一致时，用较长公共前缀（≥24 字 norm）对齐，避免 cprefix 匹配失败而回退到 head+recap。
    """
    rew = (rew_plain_norm or "").strip()
    if not rew:
        return False
    st = _normalize_user_plain_for_rewrite_match(stored_raw)
    if not st:
        return False
    if st == rew:
        return True
    head = (stored_raw or "").split("已微压省略", 1)[0]
    head = head.split("\n", 1)[0].strip()
    frag = _normalize_user_plain_for_rewrite_match(head)
    if len(frag) >= 12:
        if rew.startswith(frag):
            return True
        mf = min(len(frag), len(rew))
        if mf >= 12 and frag[:mf] == rew[:mf]:
            return True
    need = max(24, min(len(st), len(rew)) // 2)
    if need >= min(len(st), len(rew)):
        need = min(len(st), len(rew))
    if need >= 12 and st[:need] == rew[:need]:
        return True
    return False


def _session_loop_marker_content(text: str) -> bool:
    c = (text or "").strip()
    return c in ("New Agent Loop Start",) or c.startswith("Loop finished")


def _is_session_marker_system_dict(d: dict) -> bool:
    return isinstance(d, dict) and d.get("type") == "system" and _session_loop_marker_content(
        str(d.get("content") or "")
    )


def deepcopy_json_dict(d: dict) -> dict:
    """深拷贝单个消息 dict（与 json 往返一致，避免共享引用）。"""
    return json.loads(json.dumps(d, ensure_ascii=False))


def llm_history_dicts_appear_compacted(msg_dicts: List[dict]) -> bool:
    """当前落盘 llm_history 是否含压缩产物（摘要边界或 [压缩摘要] user）。"""
    for d in msg_dicts or []:
        if not isinstance(d, dict):
            continue
        if d.get("type") == "system" and (d.get("content") or "").strip() == COMPACT_BOUNDARY_SYSTEM_EXACT:
            return True
        if _is_compress_recap_user_dict(d):
            return True
    return False


def _last_n_session_user_turn_slice_start(
    items: List,
    n_keep: int,
    *,
    counts_toward_user_turn: Callable[[Any], bool],
) -> int:
    """保留最后 n_keep 个计入会话的 user 轮时，切片 items[idx:] 的起点（与 agent_memory._full_keep_start_index 同规则）。"""
    nk = int(n_keep)
    if nk <= 0:
        return len(items)
    idxs = [i for i, x in enumerate(items) if counts_toward_user_turn(x)]
    if len(idxs) <= nk:
        return 0
    return idxs[len(idxs) - nk]


def trim_message_dicts_by_kept_user_turns(msg_dicts: List[dict], n_kept_users: int) -> List[dict]:
    """
    保留前 n_kept_users 个真实用户轮（与 ui_events 中 type=user 条数对齐）；`[压缩摘要]` user 与
    metadata.micro_shrink 的微压 legacy user 均不计入用户数。
    计轮谓词须与 `_counts_toward_session_user_turns_dict` / memory 侧 `_counts_toward_session_user_turns_message` 一致。
    """
    if n_kept_users <= 0:
        return []
    raw = list(msg_dicts or [])
    out: List[dict] = []
    i = 0
    n = len(raw)
    users = 0
    while i < n:
        d = raw[i]
        if d.get("type") == "user" and (
            _is_compress_recap_user_dict(d) or _is_micro_shrink_user_dict(d)
        ):
            out.append(d)
            i += 1
            continue
        if d.get("type") == "user":
            users += 1
            while i < n:
                out.append(raw[i])
                i += 1
                if i < n and raw[i].get("type") == "user":
                    break
            if users >= n_kept_users:
                break
        else:
            out.append(d)
            i += 1
    return out


def _compacted_tail_start_index(raw: List[dict]) -> int:
    """压缩产物中尾窗第一条真实 user 的下标；无则 len(raw)。"""
    recap_i: Optional[int] = None
    for i, d in enumerate(raw):
        if isinstance(d, dict) and _is_compress_recap_user_dict(d):
            recap_i = i
            break
    if recap_i is None:
        return 0
    i = recap_i + 1
    n = len(raw)
    while i < n:
        d = raw[i]
        if isinstance(d, dict) and _counts_toward_session_user_turns_dict(d):
            return i
        i += 1
    return n


def _slice_prefix_dicts_before_matching_user(
    prefix: List[dict],
    user_plain: str,
) -> Optional[List[dict]]:
    """待压缩段中首个匹配 user 之前的前缀（不含该 user 整轮），供改写截断对齐 ui。"""
    if not user_plain or not prefix:
        return None
    rew_n = _normalize_user_plain_for_rewrite_match(user_plain)
    n = len(prefix)
    i = 0
    while i < n:
        d = prefix[i]
        if isinstance(d, dict) and d.get("type") == "user" and not _is_compress_recap_user_dict(d):
            if _rewrite_user_plain_matches_relaxed(
                rew_n,
                str(d.get("content") or ""),
            ):
                return [deepcopy_json_dict(x) for x in prefix[0:i] if isinstance(x, dict)]
        i += 1
    return None


def _drop_user_turn_by_plain(msg_dicts: List[dict], user_plain: str) -> List[dict]:
    """删除与 user_plain 匹配的首个真实 user 轮（含其后至下一条 user 前的 assistant/tool/loop 标记）。"""
    if not user_plain:
        return list(msg_dicts or [])
    raw = list(msg_dicts or [])
    rew_n = _normalize_user_plain_for_rewrite_match(user_plain)
    out: List[dict] = []
    i, n = 0, len(raw)
    while i < n:
        d = raw[i]
        if (
            isinstance(d, dict)
            and d.get("type") == "user"
            and not _is_compress_recap_user_dict(d)
            and _rewrite_user_plain_matches_relaxed(
                rew_n,
                str(d.get("content") or ""),
            )
        ):
            i += 1
            while i < n:
                nxt = raw[i]
                if isinstance(nxt, dict) and nxt.get("type") == "user":
                    break
                i += 1
            continue
        out.append(d)
        i += 1
    return out


def _filter_tail_turns_by_ui_user_plains(tail: List[dict], ui_plains: set[str]) -> List[dict]:
    """尾窗：仅保留 user 文本在 ui 保留集合中的整轮；丢弃轮次间的孤立 loop 标记。"""
    out: List[dict] = []
    i, n = 0, len(tail)
    while i < n:
        d = tail[i]
        if not isinstance(d, dict):
            i += 1
            continue
        if d.get("type") == "user" and _counts_toward_session_user_turns_dict(d):
            plain = _normalize_user_plain_for_rewrite_match(str(d.get("content") or ""))
            j = i + 1
            while j < n:
                nd = tail[j]
                if isinstance(nd, dict) and nd.get("type") == "user" and _counts_toward_session_user_turns_dict(
                    nd
                ):
                    break
                j += 1
            if plain in ui_plains:
                out.extend(tail[i:j])
            i = j
            continue
        if _is_session_marker_system_dict(d):
            i += 1
            continue
        out.append(d)
        i += 1
    return out


def _cprefix_backup_paths_newest_first(session_id: str) -> List[Path]:
    """会话目录下全部 llm_cprefix_*.json，按修改时间新→旧排序（改写时用最新快照优先命中）。"""
    sess = SESSIONS_DIR / session_id
    if not sess.is_dir():
        return []
    try:
        out = [p for p in sess.glob("llm_cprefix_*.json") if p.is_file()]
    except Exception:
        return []
    try:
        out.sort(key=lambda p: (-float(p.stat().st_mtime), str(p.resolve())))
    except OSError:
        out.sort(key=lambda p: str(p.resolve()), reverse=True)
    return out


def _extract_user_turn_dicts(
    msg_dicts: List[dict], user_plain: str, *, skip_micro: bool = False
) -> List[dict]:
    """提取与 user_plain 对齐的一条 user 轮（含其后至下一条 user 前的消息）。"""
    rew_n = _normalize_user_plain_for_rewrite_match(user_plain)
    if not rew_n:
        return []
    raw = [x for x in (msg_dicts or []) if isinstance(x, dict)]
    i, n = 0, len(raw)
    while i < n:
        d = raw[i]
        if d.get("type") != "user" or _is_compress_recap_user_dict(d):
            i += 1
            continue
        if skip_micro and _is_micro_shrink_user_dict(d):
            i += 1
            continue
        if not _rewrite_user_plain_matches_relaxed(rew_n, str(d.get("content") or "")):
            i += 1
            continue
        j = i + 1
        while j < n and not (
            raw[j].get("type") == "user" and not _is_compress_recap_user_dict(raw[j])
        ):
            j += 1
        return [deepcopy_json_dict(x) for x in raw[i:j]]
    return []


def _boundary_user_from_truncate(
    kept_events: List[dict],
    all_events: List[dict],
    before_index: int,
    *,
    for_branch: bool = False,
) -> Tuple[str, str]:
    """改写：before_index 处 user；分支：kept 末条 final 前紧邻 user。"""
    kept = list(kept_events or [])
    all_ev = list(all_events or [])
    bi = int(before_index)
    if not for_branch and bi < len(all_ev) and all_ev[bi].get("type") == "user":
        raw = str(all_ev[bi].get("content") or "")
        plain = _normalize_user_plain_for_rewrite_match(raw)
        if plain and not any(
            k.get("type") == "user"
            and _normalize_user_plain_for_rewrite_match(str(k.get("content") or "")) == plain
            for k in kept
            if isinstance(k, dict)
        ):
            return plain, raw
    for i in range(len(kept) - 1, -1, -1):
        if kept[i].get("type") != "final":
            continue
        for j in range(i - 1, -1, -1):
            if kept[j].get("type") == "user":
                raw = str(kept[j].get("content") or "")
                return _normalize_user_plain_for_rewrite_match(raw), raw
        break
    for e in reversed(kept):
        if e.get("type") == "user":
            raw = str(e.get("content") or "")
            return _normalize_user_plain_for_rewrite_match(raw), raw
    return "", ""


def _load_cprefix_at_anchor(
    session_id: str, anchor_plain: str
) -> Tuple[Optional[List[dict]], Optional[str], List[dict]]:
    """最新 cprefix 中边界 user 之前的前缀 + 该 user 整轮；无则 (None, None, [])。"""
    if not (session_id and anchor_plain):
        return None, None, []
    for p in _cprefix_backup_paths_newest_first(session_id):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            prefix = obj.get("prefix") if isinstance(obj, dict) else None
            if not isinstance(prefix, list):
                continue
            prefix = [x for x in prefix if isinstance(x, dict)]
            core = _slice_prefix_dicts_before_matching_user(prefix, anchor_plain)
            if core is not None:
                turn = _extract_user_turn_dicts(prefix, anchor_plain)
                logger.info(
                    "cprefix 还原 %s：边界前 %s 条，边界轮 %s 条",
                    p.name,
                    len(core),
                    len(turn),
                )
                return core, p.name, turn
        except Exception:
            continue
    return None, None, []


def trim_llm_dicts_for_rewrite(
    msg_dicts: List[dict],
    *,
    n_kept_users: int,
    ui_user_plains: List[str],
    session_id: Optional[str] = None,
    work_dicts: Optional[List[dict]] = None,
    anchor_plain: str = "",
    anchor_user_raw: str = "",
    drop_anchor_turn: bool = False,
) -> Tuple[List[dict], Optional[str]]:
    """改写/分支：已压缩且 cprefix 命中 → 备份前缀至锚点；分支加边界轮，改写不加。未命中则剥微压+滤尾窗。"""
    raw = [x for x in (msg_dicts or []) if isinstance(x, dict)]
    anchor = (anchor_plain or "").strip()
    anchor_raw = (anchor_user_raw or "").strip()

    if not llm_history_dicts_appear_compacted(raw):
        base = _drop_user_turn_by_plain(raw, anchor) if drop_anchor_turn and anchor else raw
        return trim_message_dicts_by_kept_user_turns(base, n_kept_users), None

    core, fname, turn = (
        _load_cprefix_at_anchor(session_id, anchor) if session_id and anchor else (None, None, [])
    )
    if core is not None:
        out = _strip_micro_shrink_legacy_user_turns(core)
        if not drop_anchor_turn:
            out = out + (
                turn
                or _extract_user_turn_dicts(work_dicts or [], anchor_raw, skip_micro=True)
            )
        return out, fname

    ui_set = {
        _normalize_user_plain_for_rewrite_match(t)
        for t in (ui_user_plains or [])
        if (t or "").strip()
    }
    t0 = _compacted_tail_start_index(raw)
    tail = raw[t0:]
    if drop_anchor_turn and anchor:
        tail = _drop_user_turn_by_plain(tail, anchor)
    tail = _filter_tail_turns_by_ui_user_plains(tail, ui_set)
    out = _strip_micro_shrink_legacy_user_turns(raw[:t0]) + tail
    if not drop_anchor_turn and anchor_raw:
        if not _extract_user_turn_dicts(out, anchor):
            out += _extract_user_turn_dicts(work_dicts or [], anchor_raw, skip_micro=True)
    return out, None


def _ui_user_plains_from_events(events: List[dict]) -> List[str]:
    return [
        str(e.get("content") or "")
        for e in (events or [])
        if isinstance(e, dict) and e.get("type") == "user"
    ]


def _count_ui_user_events(events: List[dict]) -> int:
    return sum(1 for e in (events or []) if isinstance(e, dict) and e.get("type") == "user")


def _count_session_user_dicts(msg_dicts: List[dict]) -> int:
    return sum(1 for d in (msg_dicts or []) if _counts_toward_session_user_turns_dict(d))


def _normalize_sidebar_preview_text(text: str, max_len: int = 180) -> str:
    """侧栏单行预览：折叠空白并限制长度。"""
    s = (text or "").strip()
    if not s:
        return ""
    one_line = " ".join(s.split())
    if len(one_line) > max_len:
        return one_line[: max_len - 1] + "…"
    return one_line


_LLM_HISTORY_BACKUP_FN_RE = re.compile(r"llm_history_\d{8}_\d{6}_\d+\.json")
_LLM_COMPRESS_PREFIX_BACKUP_FN_RE = re.compile(r"llm_cprefix_\d{8}_\d{6}_\d+\.json")


def _parse_metadata_json_raw(raw: str) -> dict:
    """
    解析 metadata.json 正文；容忍尾随损坏（并发写或非原子写入可能导致首段 JSON 后拼接碎片）。
    """
    s = (raw or "").strip()
    if not s:
        return {}
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {}
    except json.JSONDecodeError:
        pass
    try:
        v, end = json.JSONDecoder().raw_decode(s)
        if not isinstance(v, dict):
            return {}
        tail = s[end:].strip()
        if tail:
            lst = v.get("llm_history_compress_backups")
            if not isinstance(lst, list):
                lst = []
            seen = {str(x) for x in lst if isinstance(x, str)}
            for m in _LLM_HISTORY_BACKUP_FN_RE.finditer(tail):
                fn = m.group(0)
                if fn not in seen:
                    lst.append(fn)
                    seen.add(fn)
            v["llm_history_compress_backups"] = lst
            pfx_lst = v.get("llm_compress_prefix_backups")
            if not isinstance(pfx_lst, list):
                pfx_lst = []
            p_seen = {str(x) for x in pfx_lst if isinstance(x, str)}
            for m in _LLM_COMPRESS_PREFIX_BACKUP_FN_RE.finditer(tail):
                fn = m.group(0)
                if fn not in p_seen:
                    pfx_lst.append(fn)
                    p_seen.add(fn)
            v["llm_compress_prefix_backups"] = pfx_lst
            logger.warning(
                "metadata.json 尾随内容损坏，已读取首段 JSON 并从尾部回收 llm 备份文件名（session 文件请留意）。"
            )
        return v
    except json.JSONDecodeError:
        logger.error("metadata.json 无法解析，回退空元数据。")
        return {}


# Todo 计划 Markdown 标题（独立落盘 todo_plan.md；兼容旧 key_context 内嵌）
TODO_SECTION_HEADER = "## Todo 计划"


# ==================== 会话管理器（持久化 work_messages, llm_history, key_context；对话主链快照来自 ui_events）====================
class SessionRepository:
    """Thin path boundary for session files; SessionManager owns behavior for now."""

    def __init__(self, sessions_dir: Path, path_resolver=None):
        self.sessions_dir = sessions_dir
        self._path_resolver = path_resolver

    def session_path(self, session_id: str) -> Path:
        if self._path_resolver is not None:
            return self._path_resolver(session_id)
        return self.sessions_dir / str(session_id)

    def metadata_path(self, session_id: str) -> Path:
        return self.session_path(session_id) / "metadata.json"

    def ui_events_path(self, session_id: str) -> Path:
        return self.session_path(session_id) / "ui_events.json"

    def pending_subagent_results_path(self, session_id: str) -> Path:
        return self.session_path(session_id) / SUBAGENT_PENDING_RESULTS_FILE

    def subagent_tasks_path(self, session_id: str) -> Path:
        return self.session_path(session_id) / "subagent_tasks.json"

    def load_json_list(self, path: Path) -> List[dict]:
        if not path.is_file():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return [x for x in data if isinstance(x, dict)]

    def save_json_list(self, path: Path, rows: List[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, ensure_ascii=False)

    def load_index(self, index_file: Path) -> List[dict]:
        if not index_file.exists():
            return []
        with open(index_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("sessions", []) if isinstance(data, dict) else []
        return rows if isinstance(rows, list) else []

    def save_index(self, index_file: Path, rows: List[dict]) -> None:
        index_file.parent.mkdir(parents=True, exist_ok=True)
        with open(index_file, "w", encoding="utf-8") as f:
            json.dump({"sessions": rows}, f, indent=2, ensure_ascii=False)

    def load_metadata(self, session_id: str) -> dict:
        path = self.metadata_path(session_id)
        if not path.exists():
            return {}
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return {}
        v = _parse_metadata_json_raw(raw)
        return v if isinstance(v, dict) else {}

    def save_metadata_atomic(self, session_id: str, metadata: dict) -> None:
        path = self.metadata_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".metadata_",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(metadata if isinstance(metadata, dict) else {}, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, path)
        except BaseException:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass
            raise


class SessionEventLog:
    """Read/write UI event log JSON through one boundary."""

    def __init__(self, repository: SessionRepository):
        self.repository = repository

    def load(self, session_id: str) -> List[dict]:
        path = self.repository.ui_events_path(session_id)
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning("Failed to load ui_events for session %s: %s", session_id, e)
            return []

    def save(self, session_id: str, events: List[dict]) -> None:
        path = self.repository.ui_events_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(events, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)


class SessionManager:
    """
    管理会话的持久化：
    - work_messages.json: 原始工作消息序列（所有消息 dict，供落盘/与 ui_events 对照）
    - ui_events.json: 与 SSE 一致的 UI 事件序列（刷新的唯一显示来源）
    - llm_history.json: 含 ReAct 的唯一完整多轮（可被上下文压缩改写）
    - dialogue_history.json: 仅 user/final 主链，由 ui_events 派生（完整用户可见历史，不受 llm 压缩删减）
    - key_context.md: 会话关键信息与摘要（Todo 见 todo_plan.md）
    - metadata.json: 会话元数据（名称、创建时间等）
    """

    AUTO_ARCHIVE_AFTER_DAYS = 14
    AUTO_ARCHIVE_CHECK_INTERVAL_SEC = 300.0

    def __init__(self, sessions_dir: Path, index_file: Path):
        self.sessions_dir = sessions_dir
        self.index_file = index_file
        self.repository = SessionRepository(sessions_dir, self._resolve_session_path)
        self.event_log = SessionEventLog(self.repository)
        self._lock = threading.Lock()
        self._metadata_session_locks: Dict[str, threading.Lock] = {}
        self._metadata_session_locks_guard = threading.Lock()
        self._interrupt_cache: Dict[str, Tuple[bool, str, str]] = {}
        self._interrupt_cache_lock = threading.Lock()
        self._ui_events_cache_lock = threading.Lock()
        self._ui_events_cache: Dict[str, Tuple[Tuple[bool, int, int], List[dict]]] = {}
        self._ui_events_cache_order: List[str] = []
        self._ui_events_cache_max = 128
        self._ui_user_turns_cache: Dict[str, Tuple[Tuple[bool, int, int], List[dict]]] = {}
        self._subagent_index_cache_lock = threading.RLock()
        self._subagent_index_cache: Dict[str, str] = {}
        self._subagent_index_cache_signature: Optional[Tuple[bool, int, int]] = None
        self._known_root_session_ids: set[str] = set()
        self._auto_archive_check_lock = threading.Lock()
        self._auto_archive_last_check = 0.0
        self._load_index()
        # 每次 Agent 启动都以磁盘上的会话目录为准重建索引，避免已存在但陈旧的
        # sessions.json 隐藏新增会话，或继续展示已从磁盘移除的会话。
        self.refresh_sessions_index_from_disk()

    @staticmethod
    def _normalize_session_id(session_id: str) -> str:
        sid = str(session_id or "").strip()
        if not sid:
            raise ValueError("Invalid session_id")
        try:
            parsed = uuid.UUID(sid)
        except (TypeError, ValueError) as e:
            raise ValueError("Invalid session_id") from e
        normalized = str(parsed)
        if sid.lower() not in (normalized, normalized.replace("-", "")):
            raise ValueError("Invalid session_id")
        return normalized

    def _is_valid_session_id(self, session_id: str) -> bool:
        try:
            self._normalize_session_id(session_id)
            return True
        except ValueError:
            return False

    def _is_deleted_session(self, session_id: str) -> bool:
        sid = str(session_id or "").strip()
        if not sid:
            return False
        try:
            from session_lifecycle import is_session_deleted

            return bool(is_session_deleted(sid))
        except Exception:
            return False

    @staticmethod
    def _has_session_payload_files(session_path: Path) -> bool:
        payload_files = (
            "ui_events.json",
            "dialogue_history.json",
            SESSION_WORK_MESSAGES_FILE,
            "llm_history.json",
            "events.jsonl",
            "key_context.md",
            "todo_plan.md",
        )
        return any((session_path / name).exists() for name in payload_files) or (
            session_path / "snapshots" / "latest.json"
        ).exists()

    def _is_metadata_only_delete_remnant(self, session_path: Path, metadata: dict) -> bool:
        if self._has_session_payload_files(session_path):
            return False
        if not isinstance(metadata, dict):
            return False
        durable_fields = ("name", "created_at", "updated_at", "last_user_preview", "model_profile_id")
        return not any(str(metadata.get(k) or "").strip() for k in durable_fields)

    def _session_metadata_lock(self, session_id: str) -> threading.Lock:
        sid = str(session_id or "").strip() or "__empty__"
        with self._metadata_session_locks_guard:
            lk = self._metadata_session_locks.get(sid)
            if lk is None:
                lk = threading.Lock()
                self._metadata_session_locks[sid] = lk
            return lk

    def refresh_sessions_index_from_disk(self) -> None:
        """根据 sessions 目录内存在的会话文件夹重建索引（sessions.json），磁盘与 metadata 为准。"""
        by_id: Dict[str, dict] = {}
        sub_idx = self._load_subagent_index()
        runtime_v2_primary = self._runtime_v2_primary()
        try:
            for p in self.sessions_dir.iterdir():
                if not p.is_dir():
                    continue
                try:
                    sid = self._normalize_session_id(p.name)
                except ValueError:
                    logger.warning("Skipping invalid session directory name: %s", p.name)
                    continue
                if p.name != sid:
                    logger.warning("Skipping non-canonical session directory name: %s", p.name)
                    continue
                if sid in sub_idx:
                    continue
                meta_path = p / "metadata.json"
                if not meta_path.exists():
                    continue
                try:
                    meta_raw = meta_path.read_text(encoding="utf-8")
                    meta = _parse_metadata_json_raw(meta_raw)
                except Exception:
                    continue
                if not isinstance(meta, dict):
                    meta = {}
                if meta.get("is_subagent"):
                    continue
                if (
                    self._is_deleted_session(sid)
                    or bool(meta.get("deleted"))
                    or self._is_metadata_only_delete_remnant(p, meta)
                ):
                    continue
                name = meta.get("name") or "新会话"
                created_at = meta.get("created_at")
                if not created_at:
                    try:
                        created_at = datetime.fromtimestamp(meta_path.stat().st_ctime).isoformat()
                    except OSError:
                        created_at = datetime.now().isoformat()
                updated_at = meta.get("updated_at") or created_at
                # Reconcile filesystem-only activity once during the explicit
                # startup/repair scan. Polling can then trust the in-memory index
                # instead of stat'ing every event log on every request.
                activity_path = p / ("events.jsonl" if runtime_v2_primary else "ui_events.json")
                try:
                    activity_ts = activity_path.stat().st_mtime
                    if activity_ts > self._iso_ts(updated_at):
                        updated_at = datetime.fromtimestamp(
                            activity_ts,
                            tz=timezone.utc,
                        ).isoformat().replace("+00:00", "Z")
                except OSError:
                    pass
                archived = bool(meta.get("archived", False))
                pinned = bool(meta.get("pinned", False))
                todo = bool(meta.get("todo", False))
                goal_review_pending = bool(meta.get("goal_review_pending", False))
                pinned_at = meta.get("pinned_at")
                if pinned and not pinned_at:
                    pinned_at = updated_at
                entry = {
                    "id": sid,
                    "name": name,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "archived": archived,
                    "pinned": pinned,
                    "todo": todo,
                    "goal_review_pending": goal_review_pending,
                    "pinned_at": pinned_at if pinned else None,
                    "unread_result": bool(meta.get("unread_result", False)),
                    "unread_result_at": meta.get("unread_result_at"),
                    "unread_result_status": str(meta.get("unread_result_status") or "success"),
                    "last_user_preview": str(meta.get("last_user_preview") or ""),
                }
                unread_result_run_id = str(meta.get("unread_result_run_id") or "").strip()
                if unread_result_run_id:
                    entry["unread_result_run_id"] = unread_result_run_id
                by_id[sid] = entry
        except FileNotFoundError:
            pass
        self.index = sorted(
            by_id.values(),
            key=lambda e: str(e.get("updated_at") or e.get("created_at") or ""),
            reverse=True,
        )
        self._save_index()

    def _load_index(self):
        with self._lock:
            if self.index_file.exists():
                try:
                    rows = self.repository.load_index(self.index_file)
                    self.index = [
                        s for s in rows
                        if isinstance(s, dict) and self._is_valid_session_id(str(s.get("id") or ""))
                    ]
                except Exception as e:
                    logger.warning("Failed to load session index %s; resetting index: %s", self.index_file, e)
                    self.index = []
            else:
                self.index = []

    def _save_index(self):
        with self._lock:
            self.repository.save_index(self.index_file, self.index)

    def _subagent_index_file(self) -> Path:
        return self.sessions_dir / SUBAGENT_INDEX_FILE

    def _load_subagent_index(self) -> Dict[str, str]:
        path = self._subagent_index_file()
        lock = getattr(self, "_subagent_index_cache_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._subagent_index_cache_lock = lock
        with lock:
            try:
                stat = path.stat()
                signature = (True, int(stat.st_mtime_ns), int(stat.st_size))
            except OSError:
                signature = (False, 0, 0)
            cached_signature = getattr(self, "_subagent_index_cache_signature", None)
            if cached_signature == signature:
                return dict(getattr(self, "_subagent_index_cache", {}) or {})
            if not signature[0]:
                out: Dict[str, str] = {}
            else:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if not isinstance(data, dict):
                        data = {}
                    out = {}
                    for k, v in data.items():
                        if not k or not v:
                            continue
                        try:
                            ck = self._normalize_session_id(str(k))
                            pv = self._normalize_session_id(str(v))
                        except ValueError:
                            continue
                        out[ck] = pv
                except Exception:
                    out = {}
            self._subagent_index_cache = dict(out)
            self._subagent_index_cache_signature = signature
            return dict(out)

    def _save_subagent_index(self, idx: Dict[str, str]) -> None:
        path = self._subagent_index_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = getattr(self, "_subagent_index_cache_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._subagent_index_cache_lock = lock
        with lock:
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(idx, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
            stat = path.stat()
            self._subagent_index_cache = dict(idx)
            self._subagent_index_cache_signature = (True, int(stat.st_mtime_ns), int(stat.st_size))

    def _register_subagent(self, child_session_id: str, parent_session_id: str) -> None:
        child_id = self._normalize_session_id(child_session_id)
        parent_id = self._normalize_session_id(parent_session_id)
        lock = getattr(self, "_subagent_index_cache_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._subagent_index_cache_lock = lock
        with lock:
            idx = self._load_subagent_index()
            idx[child_id] = parent_id
            self._save_subagent_index(idx)
        getattr(self, "_known_root_session_ids", set()).discard(child_id)
        try:
            with self._session_metadata_lock(parent_id):
                meta = self._load_metadata_unlocked(parent_id)
                if not isinstance(meta, dict):
                    meta = {}
                lst = meta.get("subagent_ids")
                if not isinstance(lst, list):
                    lst = []
                if child_id not in lst:
                    lst.append(child_id)
                meta["subagent_ids"] = lst
                meta["updated_at"] = datetime.now().isoformat()
                self._save_metadata_unlocked(parent_id, meta)
        except Exception as e:
            logger.debug("更新父会话 subagent_ids 失败: %s", e)

    def _unregister_subagent(self, child_session_id: str) -> None:
        try:
            child_id = self._normalize_session_id(child_session_id)
        except ValueError:
            return
        lock = getattr(self, "_subagent_index_cache_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._subagent_index_cache_lock = lock
        with lock:
            idx = self._load_subagent_index()
            parent_id = idx.pop(child_id, None)
            self._save_subagent_index(idx)
        if parent_id:
            try:
                with self._session_metadata_lock(parent_id):
                    meta = self._load_metadata_unlocked(parent_id)
                    if isinstance(meta, dict):
                        lst = meta.get("subagent_ids")
                        if isinstance(lst, list) and child_id in lst:
                            meta["subagent_ids"] = [x for x in lst if x != child_id]
                            self._save_metadata_unlocked(parent_id, meta)
            except Exception:
                pass

    def _scan_nested_subagent_path(self, session_id: str) -> Optional[Path]:
        sid = self._normalize_session_id(session_id)
        root = self.sessions_dir.resolve()
        try:
            for candidate in root.rglob(sid):
                if not candidate.is_dir() or candidate.parent.name != "subagents":
                    continue
                try:
                    relative = candidate.resolve().relative_to(root)
                    # Ignore archive/backup trees whose first component is not
                    # an actual root session id.
                    self._normalize_session_id(relative.parts[0])
                    parent_id = self._normalize_session_id(candidate.parent.parent.name)
                except ValueError:
                    continue
                self._register_subagent(sid, parent_id)
                return candidate.resolve()
        except FileNotFoundError:
            pass
        return None

    def _resolve_session_path(
        self,
        session_id: str,
        *,
        parent_hint: Optional[str] = None,
        _seen: Optional[set[str]] = None,
    ) -> Path:
        sid = self._normalize_session_id(session_id)
        seen = set(_seen or set())
        if sid in seen:
            raise ValueError("Cyclic subagent parent index")
        seen.add(sid)
        root = self.sessions_dir.resolve()
        flat = (root / sid).resolve()
        try:
            flat.relative_to(root)
        except ValueError as e:
            raise ValueError("Invalid session_id") from e
        idx = self._load_subagent_index()
        # Subagent 目录优先于同名顶层 ghost 目录（误写顶层 sessions/{child_id}/ 时仍走嵌套路径）
        if parent_hint:
            try:
                pid = self._normalize_session_id(parent_hint)
                parent_path = self._resolve_session_path(pid, _seen=seen)
                nested = (parent_path / "subagents" / sid).resolve()
                nested.relative_to(root)
                if nested.is_dir() or idx.get(sid) == pid:
                    return nested
            except ValueError:
                pass
        parent_id = idx.get(sid)
        if parent_id and parent_id != sid:
            parent_path = self._resolve_session_path(parent_id, _seen=seen)
            nested = (parent_path / "subagents" / sid).resolve()
            try:
                nested.relative_to(root)
            except ValueError as e:
                raise ValueError("Invalid session_id") from e
            return nested
        known_roots = getattr(self, "_known_root_session_ids", None)
        if known_roots is None:
            known_roots = set()
            self._known_root_session_ids = known_roots
        if sid in known_roots and flat.is_dir():
            return flat
        # Root sessions have metadata beside the directory. This fast path
        # avoids a recursive scan on every V2 event append.
        if flat.is_dir() and (flat / "metadata.json").is_file():
            known_roots.add(sid)
            return flat
        if flat.is_dir():
            try:
                if not any(flat.iterdir()):
                    known_roots.add(sid)
                    return flat
            except OSError:
                pass
        found = self._scan_nested_subagent_path(sid)
        if found is not None:
            return found
        if flat.is_dir():
            return flat
        return flat

    def get_subagent_parent_id(self, session_id: str) -> Optional[str]:
        try:
            sid = self._normalize_session_id(session_id)
        except ValueError:
            return None
        idx = self._load_subagent_index()
        if sid in idx:
            return idx[sid]
        meta = self._load_metadata(sid)
        if isinstance(meta, dict):
            pid = str(meta.get("parent_session_id") or "").strip()
            if pid:
                return pid
        return None

    def _get_session_path(self, session_id: str) -> Path:
        return self._resolve_session_path(session_id)

    def _get_subagent_session_path(self, parent_session_id: str, child_session_id: str) -> Path:
        parent_id = self._normalize_session_id(parent_session_id)
        child_id = self._normalize_session_id(child_session_id)
        root = self.sessions_dir.resolve()
        # The parent may itself be a nested subagent. Resolve it first so
        # grandchildren stay under the complete parent chain.
        path = (self._resolve_session_path(parent_id) / "subagents" / child_id).resolve()
        try:
            path.relative_to(root)
        except ValueError as e:
            raise ValueError("Invalid session_id") from e
        return path

    def _get_pending_subagent_results_path(self, session_id: str) -> Path:
        return self.repository.pending_subagent_results_path(session_id)

    def _get_subagent_tasks_path(self, session_id: str) -> Path:
        return self.repository.subagent_tasks_path(session_id)

    def _runtime_v2_primary(self) -> bool:
        try:
            from runtime_v2 import runtime_v2_primary

            return runtime_v2_primary()
        except Exception:
            return True

    def _runtime_subagent_store(self):
        from runtime_v2.subagent_store import RuntimeSubagentStore

        return RuntimeSubagentStore(
            self.repository.sessions_dir,
            path_resolver=self._resolve_session_path,
        )

    def _runtime_mirror(self):
        from runtime_v2 import runtime_v2_react_transaction_timeout_seconds
        from runtime_v2.mirror import RuntimeMirror

        return RuntimeMirror(
            self.repository.sessions_dir,
            path_resolver=self._resolve_session_path,
            transaction_timeout_seconds=runtime_v2_react_transaction_timeout_seconds(),
        )

    def _mirror_ui_event_to_runtime_v2(self, session_id: str, event: Dict[str, Any]):
        mirror = self._runtime_mirror()
        mirrored = mirror.mirror_ui_event(session_id, event)
        if mirrored is not None:
            return mirrored
        # Newly introduced UI shapes remain native V2 facts.  The
        # ``legacy_ui_event`` type is reserved for explicit migration only.
        return mirror.append(session_id, "ui_event", dict(event or {}))

    def _list_subagent_tasks_v1(self, parent_session_id: str) -> List[dict]:
        return self.repository.load_json_list(self._get_subagent_tasks_path(parent_session_id))

    def _list_subagent_tasks_v2(self, parent_session_id: str) -> List[dict]:
        return self._runtime_subagent_store().list_tasks(parent_session_id)

    def list_subagent_tasks(self, parent_session_id: str) -> List[dict]:
        """读取父会话下的 subagent task 状态索引。"""
        if self._runtime_v2_primary():
            return self._list_subagent_tasks_v2(parent_session_id)
        return self._list_subagent_tasks_v1(parent_session_id)

    def _upsert_subagent_task_v1(self, parent_session_id: str, task_id: str, patch: Dict[str, Any]) -> None:
        tid = str(task_id or "").strip()
        if not tid:
            return
        path = self._get_subagent_tasks_path(parent_session_id)
        rows: List[dict] = self.repository.load_json_list(path)
        now = datetime.now(timezone.utc).isoformat()
        found = False
        for row in rows:
            if str(row.get("task_id") or "") != tid:
                continue
            row.update({k: v for k, v in (patch or {}).items() if v is not None})
            row["updated_at"] = now
            found = True
            break
        if not found:
            row = {"task_id": tid, "created_at": now, "updated_at": now}
            row.update({k: v for k, v in (patch or {}).items() if v is not None})
            rows.append(row)
        self.repository.save_json_list(path, rows)

    def _upsert_subagent_task_v2(self, parent_session_id: str, task_id: str, patch: Dict[str, Any]) -> None:
        self._runtime_subagent_store().upsert_task(parent_session_id, task_id, patch)

    def upsert_subagent_task(self, parent_session_id: str, task_id: str, patch: Dict[str, Any]) -> None:
        """维护父会话下 subagent task 状态索引，供 UI/恢复/调试使用。"""
        tid = str(task_id or "").strip()
        if not tid:
            return
        if self._runtime_v2_primary():
            self._upsert_subagent_task_v2(parent_session_id, tid, patch)
        else:
            self._upsert_subagent_task_v1(parent_session_id, tid, patch)

    def write_subagent_output(self, child_session_id: str, text: str) -> str:
        """将 subagent 最终可读输出写入子会话 output.md，返回路径。"""
        path = self._get_session_path(child_session_id) / "output.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text or ""), encoding="utf-8")
        return str(path)

    def read_subagent_task_output(self, parent_session_id: str, task_id: str) -> Dict[str, Any]:
        """读取父会话下某个 subagent/task 的可读输出文件。"""
        if self._runtime_v2_primary():
            try:
                return self._runtime_subagent_store().read_task_output(parent_session_id, task_id)
            except Exception as exc:
                logger.debug("Runtime V2 read subagent task output failed: %s", exc)
                return {"ok": False, "error": str(exc)}
        tid = str(task_id or "").strip()
        if not tid:
            return {"ok": False, "error": "missing task_id"}
        rows = self.list_subagent_tasks(parent_session_id)
        task = next(
            (
                x
                for x in rows
                if str(x.get("task_id") or x.get("agent_id") or x.get("id") or "") == tid
            ),
            None,
        )
        child_id = self.validate_subagent_resume(parent_session_id, tid)
        output_file = str((task or {}).get("output_file") or "").strip()
        if not output_file and child_id:
            output_file = str(self._get_session_path(child_id) / "output.md")
        if not output_file:
            return {"ok": False, "error": "output not found"}
        try:
            path = Path(output_file).expanduser().resolve()
        except Exception:
            return {"ok": False, "error": "invalid output path"}
        allowed_roots = [self._get_session_path(parent_session_id).resolve()]
        if child_id:
            allowed_roots.append(self._get_session_path(child_id).resolve())
        allowed = False
        for root in allowed_roots:
            try:
                path.relative_to(root)
                allowed = True
                break
            except ValueError:
                continue
        if not allowed:
            return {"ok": False, "error": "output path outside session"}
        if not path.is_file():
            return {"ok": False, "error": "output not found"}
        try:
            return {
                "ok": True,
                "task_id": tid,
                "path": str(path),
                "content": path.read_text(encoding="utf-8"),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def write_subagent_task_output(self, parent_session_id: str, task_id: str, text: str) -> str:
        """将虚拟 subagent task（如 best-of-n runner）输出写入父会话 outputs 目录。"""
        if self._runtime_v2_primary():
            try:
                return self._runtime_subagent_store().write_task_output(parent_session_id, task_id, text)
            except Exception as exc:
                logger.warning("Runtime V2 write subagent task output failed: %s", exc)
                raise
        tid = str(task_id or "").strip() or "subagent"
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", tid)
        path = self._get_session_path(parent_session_id) / "subagent_outputs" / f"{safe}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text or ""), encoding="utf-8")
        return str(path)

    def append_pending_subagent_result(self, parent_session_id: str, entry: Dict[str, Any]) -> None:
        row = dict(entry)
        row.setdefault("result_id", uuid.uuid4().hex)
        row.setdefault("delivery_state", "pending")
        parent_run_id = str(row.get("parent_run_id") or "").strip()
        if row.get("after_final_index") is None and parent_run_id:
            row.setdefault("delivery_scope", "parent_run")
        elif row.get("after_final_index") is None:
            events = self._load_ui_events_for_active_runtime(parent_session_id)
            anchor = self._latest_final_index_without_later_user(events)
            if anchor >= 0:
                row["after_final_index"] = anchor
                row.setdefault("delivery_scope", "after_final")
            else:
                row.setdefault("delivery_scope", "unanchored")
        if self._runtime_v2_primary():
            self._runtime_subagent_store().append_pending_result(parent_session_id, row)
        else:
            path = self._get_pending_subagent_results_path(parent_session_id)
            rows: List[dict] = self.repository.load_json_list(path)
            rows.append(row)
            self.repository.save_json_list(path, rows)

    def _load_pending_subagent_results(self, session_id: str) -> List[dict]:
        if self._runtime_v2_primary():
            rows = self._runtime_subagent_store().list_pending_results(session_id)
        else:
            rows = self.repository.load_json_list(self._get_pending_subagent_results_path(session_id))
        try:
            meta = self._load_metadata(session_id)
            created_at = str((meta or {}).get("created_at") or "")
            if (meta or {}).get("branched_from") and created_at:
                rows = [
                    x for x in rows
                    if not str(x.get("finished_at") or "")
                    or str(x.get("finished_at") or "") >= created_at
                ]
        except Exception:
            pass
        return rows

    def _save_pending_subagent_results(self, session_id: str, rows: List[dict]) -> None:
        rows = [x for x in (rows or []) if isinstance(x, dict)]
        if self._runtime_v2_primary():
            self._runtime_subagent_store().save_pending_results(session_id, rows)
        else:
            path = self._get_pending_subagent_results_path(session_id)
            self.repository.save_json_list(path, rows)

    def has_pending_subagent_notifications(self, session_id: str) -> bool:
        """父会话是否有尚未注入模型的后台 subagent 完成结果。"""
        for item in self._load_pending_subagent_results(session_id):
            if self._pending_subagent_notification_line(item):
                return True
        return False

    def _pending_subagent_notification_line(self, item: Dict[str, Any]) -> str:
        """将一条 terminal pending 记录格式化为可注入父 Agent 的通知行。"""
        return format_pending_subagent_notification(item)

    def _latest_final_index_without_later_user(self, events: List[dict]) -> int:
        """Return the latest final event index when no newer user turn exists."""
        last_final_idx = -1
        last_user_idx = -1
        for i, ev in enumerate(events or []):
            if not isinstance(ev, dict):
                continue
            t = str(ev.get("type") or "")
            if t == "user":
                last_user_idx = i
            elif t == "final":
                last_final_idx = i
        if last_final_idx < 0 or last_user_idx > last_final_idx:
            return -1
        return last_final_idx

    def _actionable_pending_subagent_rows(self, session_id: str) -> List[dict]:
        """
        可注入父 Agent 的 pending 子任务行：terminal、有通知正文，且 after_final_index 与当前末条 final 对齐。
        """
        rows = self._load_pending_subagent_results(session_id)
        events = self._load_ui_events_for_active_runtime(session_id)
        if not rows or not events:
            return []
        last_idx = self._latest_final_index_without_later_user(events)
        if last_idx < 0:
            return []
        out: List[dict] = []
        for item in rows:
            if not self._pending_subagent_notification_line(item):
                continue
            if str(item.get("delivery_state") or "pending") != "pending":
                continue
            afi = item.get("after_final_index")
            if afi is None:
                # A result completed while a parent run was active belongs to that
                # run.  It must not be rebound to a future final event.
                continue
            else:
                try:
                    anchor = int(afi)
                except (TypeError, ValueError):
                    continue
            if anchor < 0 or anchor > last_idx:
                continue
            if anchor < last_idx:
                continue
            if anchor == last_idx:
                out.append(item)
        return out

    def count_actionable_pending_subagent_results(self, session_id: str) -> int:
        return len(self._actionable_pending_subagent_rows(session_id))

    def anchor_pending_subagent_results_for_run(self, session_id: str, run_id: str) -> int:
        """Make unconsumed results from a finished parent run manually actionable."""
        rid = str(run_id or "").strip()
        if not rid:
            return 0
        events = self._load_ui_events_for_active_runtime(session_id)
        final_idx = self._latest_final_index_without_later_user(events)
        if final_idx < 0:
            return 0
        rows = self._load_pending_subagent_results(session_id)
        changed = 0
        for item in rows:
            if str(item.get("parent_run_id") or "").strip() != rid:
                continue
            if item.get("after_final_index") is not None:
                continue
            item["after_final_index"] = final_idx
            item["delivery_scope"] = "after_final"
            changed += 1
        if changed:
            self._save_pending_subagent_results(session_id, rows)
        return changed

    def can_continue_after_subagents(self, session_id: str) -> bool:
        """
        是否应续接父 Agent：有待注入结果，且 ui_events 末条 final 与 pending 记录的 after_final_index 一致
        （父轮已结束、用户尚未在同一 final 之后开新轮）。
        """
        return bool(self._actionable_pending_subagent_rows(session_id))

    def can_continue_react_session(self, session_id: str) -> bool:
        """True when a ReAct turn has user input but no final answer yet."""
        events = self._load_ui_events_for_active_runtime(session_id)
        if not events:
            return False
        last_user_idx = -1
        for i, ev in enumerate(events):
            if isinstance(ev, dict) and str(ev.get("type") or "") == "user":
                last_user_idx = i
        if last_user_idx < 0:
            return False
        for ev in events[last_user_idx + 1:]:
            if isinstance(ev, dict) and str(ev.get("type") or "") == "final":
                return False
        return True

    def consume_pending_subagent_notifications(
        self, session_id: str, *, parent_run_id: str = ""
    ) -> List[str]:
        """读取并消费可注入的后台 subagent 通知，供父 react_node 注入。"""
        rows = self._load_pending_subagent_results(session_id)
        events = self._load_ui_events_for_active_runtime(session_id)
        last_idx = self._latest_final_index_without_later_user(events)
        run_id = str(parent_run_id or "").strip()
        if not rows:
            return []
        lines: List[str] = []
        keep: List[dict] = []
        for item in rows:
            line = self._pending_subagent_notification_line(item)
            if not line:
                keep.append(item)
                continue
            if str(item.get("delivery_state") or "pending") != "pending":
                keep.append(item)
                continue
            item_run_id = str(item.get("parent_run_id") or "").strip()
            if run_id and item_run_id == run_id:
                lines.append(line)
                continue
            if run_id or not events or last_idx < 0:
                keep.append(item)
                continue
            try:
                if item.get("after_final_index") is None:
                    keep.append(item)
                    continue
                anchor = int(item.get("after_final_index"))
            except (TypeError, ValueError):
                keep.append(item)
                continue
            if anchor == last_idx:
                lines.append(line)
            else:
                keep.append(item)
        self._save_pending_subagent_results(session_id, keep)
        return lines

    def claim_pending_subagent_notifications(
        self, session_id: str, claim_id: str, *, parent_run_id: str = ""
    ) -> List[dict]:
        """Claim a delivery batch so a failed model-history write can roll it back."""
        cid = str(claim_id or "").strip()
        if not cid:
            return []
        rows = self._load_pending_subagent_results(session_id)
        events = self._load_ui_events_for_active_runtime(session_id)
        last_idx = self._latest_final_index_without_later_user(events)
        rid = str(parent_run_id or "").strip()
        claimed: List[dict] = []
        for item in rows:
            if str(item.get("delivery_state") or "pending") != "pending":
                continue
            if not self._pending_subagent_notification_line(item):
                continue
            selected = bool(rid and str(item.get("parent_run_id") or "").strip() == rid)
            if not rid and last_idx >= 0 and item.get("after_final_index") is not None:
                try:
                    selected = int(item.get("after_final_index")) == last_idx
                except (TypeError, ValueError):
                    selected = False
            if selected:
                item["delivery_state"] = "claimed"
                item["claimed_by"] = cid
                item["claimed_at"] = datetime.now(timezone.utc).isoformat()
                claimed.append(dict(item))
        if claimed:
            self._save_pending_subagent_results(session_id, rows)
        return claimed

    def resolve_pending_subagent_claim(
        self, session_id: str, claim_id: str, *, consumed: bool
    ) -> int:
        cid = str(claim_id or "").strip()
        rows = self._load_pending_subagent_results(session_id)
        keep: List[dict] = []
        changed = 0
        for item in rows:
            if not cid or str(item.get("claimed_by") or "") != cid:
                keep.append(item)
                continue
            changed += 1
            if not consumed:
                item.pop("claimed_by", None)
                item.pop("claimed_at", None)
                item["delivery_state"] = "pending"
                keep.append(item)
        if changed:
            self._save_pending_subagent_results(session_id, keep)
        return changed

    def clear_pending_subagent_results_by_agent_ids(self, session_id: str, agent_ids: List[str]) -> int:
        """清除已被显式读取/注入的 subagent pending 结果，避免续接横幅重复出现。"""
        ids = {str(x or "").strip() for x in (agent_ids or []) if str(x or "").strip()}
        if not ids:
            return 0
        rows = self._load_pending_subagent_results(session_id)
        if not rows:
            return 0
        keep: List[dict] = []
        removed = 0
        for item in rows:
            aid = str(item.get("agent_id") or item.get("task_id") or "").strip()
            if aid in ids:
                removed += 1
                continue
            keep.append(item)
        if removed:
            self._save_pending_subagent_results(session_id, keep)
        return removed

    def dismiss_pending_subagent_notifications(self, session_id: str) -> int:
        """用户关闭续接提示时，清除当前可注入的 pending subagent 通知。"""
        rows = self._load_pending_subagent_results(session_id)
        if not rows:
            return 0
        actionable = self._actionable_pending_subagent_rows(session_id)
        def _pending_key(item: Dict[str, Any]) -> tuple:
            return (
                str(item.get("agent_id") or ""),
                str(item.get("status") or ""),
                str(item.get("finished_at") or ""),
                str(item.get("output_file") or ""),
            )
        actionable_keys = {_pending_key(x) for x in actionable}
        keep = [x for x in rows if _pending_key(x) not in actionable_keys]
        removed = len(rows) - len(keep)
        self._save_pending_subagent_results(session_id, keep)
        return removed

    def _extract_subagent_dialogue_turns(
        self, session_id: str, *, result_preview: str = ""
    ) -> List[Dict[str, str]]:
        """子 agent 问/答轮次：ui_events 主链；无 final 时回退 llm_response / result_preview。"""
        turns: List[Dict[str, str]] = []
        pending_user = ""
        last_response = ""
        try:
            for ev in self._load_ui_events_for_active_runtime(session_id):
                if not isinstance(ev, dict):
                    continue
                et = str(ev.get("type") or "")
                content = str(ev.get("content") or "").strip()
                if et == "user":
                    if pending_user and last_response:
                        turns.append(
                            {"user": pending_user[:300], "final": last_response[:300]}
                        )
                    pending_user = content
                    last_response = ""
                elif et == "final" and content:
                    turns.append({"user": pending_user[:300], "final": content[:300]})
                    pending_user = ""
                    last_response = ""
                elif et == "llm_response" and content:
                    last_response = content
            if pending_user and last_response:
                turns.append({"user": pending_user[:300], "final": last_response[:300]})
            elif pending_user and result_preview:
                turns.append({"user": pending_user[:300], "final": str(result_preview)[:300]})
        except Exception:
            turns = []
        if turns:
            return turns
        turns = self._dialogue_turns_from_llm_history(session_id)
        if turns:
            return turns
        preview = str(result_preview or "").strip()
        if preview:
            return [{"user": "", "final": preview[:300]}]
        return []

    def _dialogue_turns_from_llm_history(self, session_id: str) -> List[Dict[str, str]]:
        """从 llm_history 还原 user/assistant 问答（subagent ui_events 缺 final 时的兜底）。"""
        turns: List[Dict[str, str]] = []
        try:
            if self._runtime_v2_primary():
                from runtime_v2.model_projection import RuntimeModelProjection

                raw = RuntimeModelProjection(
                    self.repository.sessions_dir,
                    path_resolver=getattr(self.repository, "_path_resolver", None),
                ).read_message_dicts(session_id)
            else:
                path = self._get_llm_history_path(session_id)
                if not path.is_file():
                    return turns
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            if not isinstance(raw, list):
                return turns
            pending_user = ""
            for item in raw:
                if not isinstance(item, dict):
                    continue
                t = str(item.get("type") or "")
                content = str(item.get("content") or "").strip()
                if not content or content == "Loop finished":
                    continue
                if t == "user":
                    if _is_compress_recap_user_dict(item):
                        continue
                    pending_user = content
                elif t == "assistant":
                    if item.get("tool_calls"):
                        continue
                    turns.append({"user": pending_user[:300], "final": content[:300]})
                    pending_user = ""
        except Exception:
            return []
        return turns

    def _subagent_finish_from_parent_events(
        self, parent_id: str, child_id: str
    ) -> Optional[Dict[str, Any]]:
        """从父会话 ui_events 查找该 subagent 最近一次 finish 事件。"""
        try:
            for ev in reversed(self._load_ui_events_for_active_runtime(parent_id)):
                if not isinstance(ev, dict):
                    continue
                if str(ev.get("type") or "") != "subagent_finish":
                    continue
                aid = str(ev.get("agent_id") or ev.get("run_id") or "")
                if aid == child_id:
                    return ev
        except Exception:
            pass
        return None

    def _aggregate_subagent_session_metrics(self, session_id: str) -> Dict[str, int]:
        """汇总 subagent 子会话 ui_events 中的 process_metrics（跨多轮 resume 累加）。

        仅统计带 duration_ms 的回合结束快照；略过工具执行过程中的 live 快照（tool_calls 为累计值，不可相加）。
        """
        totals = {
            "duration_ms": 0,
            "react_loops": 0,
            "tool_calls": 0,
            "tool_failures": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 0,
        }
        try:
            for ev in self._load_ui_events_for_active_runtime(session_id):
                if not isinstance(ev, dict):
                    continue
                event_type = str(ev.get("type") or "")
                if event_type == "cache_stats":
                    for source, target in (
                        ("input_tokens", "input_tokens"),
                        ("output_tokens", "output_tokens"),
                        ("cache_hit", "cache_hit_tokens"),
                        ("cache_miss", "cache_miss_tokens"),
                    ):
                        try:
                            totals[target] += max(0, int(ev.get(source) or 0))
                        except (TypeError, ValueError):
                            pass
                    continue
                if event_type != "process_metrics" or ev.get("duration_ms") is None:
                    continue
                for key in ("duration_ms", "react_loops", "tool_calls", "tool_failures"):
                    val = ev.get(key)
                    if val is None:
                        continue
                    try:
                        totals[key] += max(0, int(val))
                    except (TypeError, ValueError):
                        pass
        except Exception:
            pass
        return totals

    def _last_cache_model_from_ui(self, session_id: str) -> str:
        try:
            for ev in reversed(self._load_ui_events_for_active_runtime(session_id)):
                if (
                    isinstance(ev, dict)
                    and str(ev.get("type") or "") == "cache_stats"
                    and ev.get("model")
                ):
                    return str(ev.get("model") or "").strip()
        except Exception:
            pass
        return ""

    def _resolve_subagent_status(
        self,
        cid: str,
        parent_id: str,
        meta: Dict[str, Any],
        *,
        running: bool,
        result_preview: str,
    ) -> Dict[str, Any]:
        if running:
            return {"status": "running", "ok": None, "error": ""}
        persisted_run_status = str(meta.get("subagent_run_status") or "").strip().lower()
        if persisted_run_status in {"orphaned", "unknown"}:
            return {
                "status": persisted_run_status,
                "ok": False,
                "error": str(meta.get("subagent_run_error") or meta.get("subagent_error") or persisted_run_status),
            }
        if persisted_run_status == "running":
            return {
                "status": "orphaned",
                "ok": False,
                "error": "persisted run has no live process-local execution",
            }
        finish_ev = self._subagent_finish_from_parent_events(parent_id, cid)
        ok: Optional[bool] = None
        error = ""
        if finish_ev is not None:
            ok = finish_ev.get("ok") is not False
            error = str(finish_ev.get("error") or "").strip()
        elif meta.get("subagent_ok") is not None:
            ok = bool(meta.get("subagent_ok"))
            error = str(meta.get("subagent_error") or "").strip()
        elif bool(meta.get("interrupt_requested")):
            ok = False
            error = "interrupted"
        preview = str(result_preview or "").strip()
        if ok is False:
            status = "interrupted" if "interrupt" in error.lower() else "failed"
            return {"status": status, "ok": False, "error": error or preview}
        if preview and re.search(r"(?i)^error:|失败|异常|interrupt", preview):
            return {"status": "failed", "ok": False, "error": preview}
        if bool(meta.get("interrupt_requested")):
            return {"status": "interrupted", "ok": False, "error": "interrupted"}
        has_final = False
        try:
            events = self._load_ui_events_for_active_runtime(cid)
            has_final = any(
                isinstance(e, dict) and str(e.get("type") or "") == "final"
                for e in events
            )
            has_activity = any(
                isinstance(e, dict)
                and str(e.get("type") or "")
                in ("tool_call", "llm_response", "process_metrics", "user")
                for e in events
            )
            if has_activity and not has_final:
                return {"status": "interrupted", "ok": False, "error": "interrupted"}
            if ok is True and not has_final and not preview:
                return {"status": "failed", "ok": False, "error": "missing final"}
        except Exception:
            pass
        if preview and not has_final:
            return {"status": "failed", "ok": False, "error": preview}
        if ok is True and has_final:
            return {"status": "completed", "ok": True, "error": ""}
        return {"status": "completed", "ok": True, "error": ""}

    def _resolve_subagent_status_lite(
        self,
        meta: Dict[str, Any],
        *,
        running: bool,
        result_preview: str,
        has_final: bool = False,
    ) -> Dict[str, Any]:
        if running:
            return {"status": "running", "ok": None, "error": ""}
        persisted_run_status = str(meta.get("subagent_run_status") or "").strip().lower()
        if persisted_run_status in {"orphaned", "unknown"}:
            return {
                "status": persisted_run_status,
                "ok": False,
                "error": str(meta.get("subagent_run_error") or meta.get("subagent_error") or persisted_run_status),
            }
        if persisted_run_status == "running":
            return {
                "status": "orphaned",
                "ok": False,
                "error": "persisted run has no live process-local execution",
            }
        ok_raw = meta.get("subagent_ok")
        error = str(meta.get("subagent_error") or "").strip()
        if ok_raw is False:
            status = "interrupted" if "interrupt" in error.lower() else "failed"
            return {"status": status, "ok": False, "error": error}
        if bool(meta.get("interrupt_requested")):
            return {"status": "interrupted", "ok": False, "error": error or "interrupted"}
        preview = str(result_preview or "").strip()
        if ok_raw is True and (has_final or preview):
            return {"status": "completed", "ok": True, "error": ""}
        if preview and not has_final:
            return {"status": "failed", "ok": False, "error": preview}
        if ok_raw is True and not has_final:
            return {"status": "failed", "ok": False, "error": "missing final"}
        return {"status": "failed", "ok": False, "error": "missing final"}

    def list_subagent_descendants(self, root_session_id: str) -> List[str]:
        """返回 root 下所有 subagent 会话 ID（含嵌套），不含 root 自身。"""
        root_id = self._normalize_session_id(root_session_id)
        idx = self._load_subagent_index()
        out: List[str] = []
        seen: Set[str] = set()
        frontier = [root_id]
        while frontier:
            parent = frontier.pop(0)
            for child_id, pid in idx.items():
                if pid != parent or child_id in seen:
                    continue
                seen.add(child_id)
                out.append(child_id)
                frontier.append(child_id)
        return out

    def list_subagents_flat(
        self,
        root_session_id: str,
        *,
        running_checker: Optional[Callable[[str], bool]] = None,
        include_dialogue_turns: bool = True,
    ) -> List[Dict[str, Any]]:
        """递归列出 root 会话下所有 subagent（含嵌套），供 UI 树展示。"""
        root_id = self._normalize_session_id(root_session_id)
        out: List[Dict[str, Any]] = []

        def walk(parent_id: str, container_path: Path) -> None:
            sub_dir = container_path / "subagents"
            if not sub_dir.is_dir():
                return
            try:
                entries = sorted(sub_dir.iterdir(), key=lambda p: p.name)
            except OSError:
                return
            for child_path in entries:
                if not child_path.is_dir():
                    continue
                if child_path.name.startswith("_"):
                    continue
                try:
                    cid = self._normalize_session_id(child_path.name)
                except ValueError:
                    continue
                meta: Dict[str, Any] = {}
                mp = child_path / "metadata.json"
                if mp.is_file():
                    try:
                        raw = _parse_metadata_json_raw(mp.read_text(encoding="utf-8"))
                        if isinstance(raw, dict):
                            meta = raw
                    except Exception:
                        pass
                desc = (
                    str(meta.get("subagent_description") or meta.get("name") or cid[:8])
                    .strip()
                )
                stype = str(meta.get("subagent_type") or "").strip()
                lite_mode = not include_dialogue_turns
                result_preview = str(meta.get("result_preview") or "").strip()[:1200]
                has_final = False
                if lite_mode:
                    try:
                        has_final = any(
                            isinstance(ev, dict) and ev.get("type") == "final"
                            for ev in self._load_ui_events_for_active_runtime(cid)
                        )
                    except Exception:
                        has_final = False
                if not result_preview and not lite_mode:
                    try:
                        for ev in reversed(self._load_ui_events_for_active_runtime(cid)):
                            if isinstance(ev, dict) and ev.get("type") == "final":
                                result_preview = str(ev.get("content") or "")[:1200]
                                has_final = True
                                break
                    except Exception:
                        pass
                dialogue_turns = (
                    self._extract_subagent_dialogue_turns(
                        cid, result_preview=result_preview
                    )
                    if include_dialogue_turns
                    else []
                )
                is_running = bool(running_checker(cid)) if running_checker else False
                if lite_mode:
                    status_info = self._resolve_subagent_status_lite(
                        meta,
                        running=is_running,
                        result_preview=result_preview,
                        has_final=has_final,
                    )
                    session_metrics = {}
                    cache_model = ""
                else:
                    status_info = self._resolve_subagent_status(
                        cid,
                        parent_id,
                        meta,
                        running=is_running,
                        result_preview=result_preview,
                    )
                    session_metrics = self._aggregate_subagent_session_metrics(cid)
                    cache_model = self._last_cache_model_from_ui(cid)
                executor_model = str(meta.get("executor_model") or "").strip()
                if not executor_model and cache_model:
                    executor_model = cache_model
                shared_observability: Dict[str, Any] = {}
                try:
                    import runtime_observability

                    observed = runtime_observability.snapshot(cid)
                    observed_runs = observed.get("runs") if isinstance(observed, dict) else []
                    if isinstance(observed_runs, list) and observed_runs:
                        shared_observability = dict(observed_runs[-1] or {})
                except Exception:
                    shared_observability = {}
                node: Dict[str, Any] = {
                    "id": cid,
                    "parent_id": parent_id,
                    "description": desc,
                    "subagent_type": stype,
                    "depth": int(meta.get("subagent_depth") or 1),
                    "created_at": meta.get("created_at"),
                    "updated_at": meta.get("updated_at"),
                    "best_of_run_id": str(meta.get("best_of_run_id") or ""),
                    "best_of_attempt": int(meta.get("best_of_attempt") or 0),
                    "git_worktree_path": str(meta.get("git_worktree_path") or ""),
                    "subagent_work_dir": str(meta.get("subagent_work_dir") or ""),
                    "git_worktree_state": str(meta.get("git_worktree_state") or ""),
                    "forked_from_parent": bool(meta.get("forked_from_parent")),
                    "model_profile_id": str(meta.get("model_profile_id") or ""),
                    "executor_model": executor_model,
                    "last_model_switch": dict(meta.get("last_model_switch") or {}),
                    "output_file": str(meta.get("output_file") or "").strip(),
                    "running": is_running,
                    "ok": status_info.get("ok"),
                    "status": status_info.get("status"),
                    "error": status_info.get("error"),
                    "has_final": has_final,
                    "result_preview": result_preview,
                    "dialogue_turns": dialogue_turns,
                    "session_metrics": session_metrics,
                    "run_heartbeat_at": str(
                        shared_observability.get("heartbeat_at")
                        or meta.get("subagent_run_heartbeat_at")
                        or ""
                    ),
                    "files_touched": list(
                        dict.fromkeys(
                            [
                                *list(meta.get("subagent_files_touched") or []),
                                *[
                                    str(item.get("path") or "")
                                    for item in shared_observability.get("file_changes") or []
                                    if isinstance(item, dict) and str(item.get("path") or "")
                                ],
                            ]
                        )
                    ),
                    "file_changes": list(shared_observability.get("file_changes") or []),
                }
                out.append(node)
                walk(cid, child_path)

        walk(root_id, self._get_session_path(root_id))
        return out

    def count_subagents(self, root_session_id: str) -> int:
        return len(self.list_subagents_flat(root_session_id))

    def patch_subagent_metadata(self, child_session_id: str, patch: Dict[str, Any]) -> None:
        """合并更新 subagent metadata 字段。"""
        cid = self._normalize_session_id(child_session_id)
        with self._session_metadata_lock(cid):
            meta = self._load_metadata_unlocked(cid)
            if not isinstance(meta, dict):
                meta = {}
            meta.update({k: v for k, v in (patch or {}).items() if v is not None})
            meta["updated_at"] = datetime.now().isoformat()
            self._save_metadata_unlocked(cid, meta)

    def switch_subagent_model_profile(
        self,
        child_session_id: str,
        profile_id: str,
        *,
        executor_model: str = "",
        switch_id: str = "",
        requested_by: str = "user",
    ) -> Dict[str, Any]:
        """Atomically retarget an existing subagent at its next model boundary."""
        cid = self._normalize_session_id(child_session_id)
        target_profile_id = str(profile_id or "").strip()
        if not target_profile_id:
            raise ValueError("profile_id is required")
        now = datetime.now(timezone.utc).isoformat()
        with self._session_metadata_lock(cid):
            meta = self._load_metadata_unlocked(cid)
            if not isinstance(meta, dict) or not meta.get("is_subagent"):
                raise ValueError("session is not a subagent")
            previous_profile_id = str(meta.get("model_profile_id") or "").strip()
            previous_model = str(meta.get("executor_model") or "").strip()
            history = meta.get("model_switch_history")
            if not isinstance(history, list):
                history = []
            record = {
                "switch_id": str(switch_id or uuid.uuid4().hex),
                "from_profile_id": previous_profile_id,
                "to_profile_id": target_profile_id,
                "from_model": previous_model,
                "to_model": str(executor_model or "").strip(),
                "requested_by": str(requested_by or "user"),
                "switched_at": now,
            }
            meta["model_profile_id"] = target_profile_id
            meta["executor_model"] = str(executor_model or "").strip()
            # A fork snapshot intentionally freezes its original model runtime.
            # Once the user switches profiles, keep the inherited tools/system
            # prompt but release that frozen model so the profile can take over.
            meta["fork_model_runtime"] = {}
            fork_runtime = meta.get("fork_runtime_config")
            if isinstance(fork_runtime, dict) and "model_runtime" in fork_runtime:
                fork_runtime = dict(fork_runtime)
                fork_runtime.pop("model_runtime", None)
                meta["fork_runtime_config"] = fork_runtime
            meta["model_switch_history"] = [*history[-49:], record]
            meta["last_model_switch"] = record
            meta["updated_at"] = now
            self._save_metadata_unlocked(cid, meta)
        _invalidate_executor_config_cache(cid)
        return record

    def _get_dialogue_history_path(self, session_id: str) -> Path:
        return self._get_session_path(session_id) / "dialogue_history.json"

    def _get_work_messages_path(self, session_id: str) -> Path:
        return self._get_session_path(session_id) / SESSION_WORK_MESSAGES_FILE

    def _get_llm_history_path(self, session_id: str) -> Path:
        return self._get_session_path(session_id) / "llm_history.json"

    def _get_key_context_path(self, session_id: str) -> Path:
        return self._get_session_path(session_id) / "key_context.md"

    def _get_key_context_history_path(self, session_id: str) -> Path:
        return self._get_session_path(session_id) / "key_context_history.md"

    def _get_todo_plan_path(self, session_id: str) -> Path:
        return self._get_session_path(session_id) / "todo_plan.md"

    def load_todo_plan(self, session_id: str) -> str:
        p = self._get_todo_plan_path(session_id)
        if not p.is_file():
            return ""
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return ""

    def save_todo_plan(self, session_id: str, text: str) -> None:
        p = self._get_todo_plan_path(session_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        body = (text or "").strip()
        if not body or body == "当前没有待办事项。":
            p.write_text("", encoding="utf-8")
            return
        if not body.startswith(TODO_SECTION_HEADER):
            body = f"{TODO_SECTION_HEADER}\n\n{body}\n"
        p.write_text(body.rstrip() + "\n", encoding="utf-8")

    def migrate_todo_plan_off_key_context(self, session_id: str, key_context: str) -> str:
        """
        首次迁移：若 todo_plan.md 为空且 key_context 中含独立成行的 ## Todo 计划（且为真实任务列表），
        则抽出至 todo_plan.md 并从 key 中删除。
        返回应写回状态的 key_context 字符串（可能与入参相同）。
        """
        sid = (session_id or "").strip()
        if not sid:
            return key_context or ""
        kc = _repair_mis_split_todo_plan(sid, key_context or "")
        tp = self._get_todo_plan_path(sid)
        if tp.is_file():
            try:
                if (tp.read_text(encoding="utf-8") or "").strip():
                    return kc
            except Exception:
                pass
        kc = kc or ""
        section = _extract_todo_plan_section_raw(kc)
        if not section or not _todo_section_looks_like_real_plan(section):
            return kc
        try:
            self.save_todo_plan(sid, section)
            cleaned = _strip_todo_plan_from_key_context(kc)
            self._save_key_context(sid, cleaned)
            return cleaned
        except Exception as e:
            logger.warning("migrate_todo_plan_off_key_context 失败: %s", e)
            return kc

    def _get_metadata_path(self, session_id: str) -> Path:
        return self.repository.metadata_path(session_id)

    def _get_ui_events_path(self, session_id: str) -> Path:
        return self.repository.ui_events_path(session_id)

    def _load_ui_events(self, session_id: str) -> List[dict]:
        sig = self._ui_events_file_signature(session_id)
        key = str(self._get_ui_events_path(session_id).resolve())
        with self._ui_events_cache_lock:
            cached = self._ui_events_cache.get(key)
            if cached and cached[0] == sig:
                return [dict(row) for row in cached[1]]
        events = self.event_log.load(session_id)
        with self._ui_events_cache_lock:
            self._ui_events_cache[key] = (sig, [dict(row) for row in events])
            if key in self._ui_events_cache_order:
                self._ui_events_cache_order.remove(key)
            self._ui_events_cache_order.append(key)
            while len(self._ui_events_cache_order) > self._ui_events_cache_max:
                old = self._ui_events_cache_order.pop(0)
                self._ui_events_cache.pop(old, None)
        return [dict(row) for row in events]

    def _invalidate_ui_events_cache(self, session_id: str) -> None:
        try:
            key = str(self._get_ui_events_path(session_id).resolve())
        except Exception:
            key = ""
        if not key:
            return
        with self._ui_events_cache_lock:
            self._ui_events_cache.pop(key, None)
            try:
                self._ui_events_cache_order.remove(key)
            except ValueError:
                pass

    def _ui_events_file_signature(self, session_id: str) -> Tuple[bool, int, int]:
        path = self._get_ui_events_path(session_id)
        try:
            st = path.stat()
            return True, int(st.st_mtime_ns), int(st.st_size)
        except OSError:
            return False, 0, 0

    def _save_ui_events(self, session_id: str, events: List[dict]) -> None:
        try:
            from session_lifecycle import is_session_deleted

            if is_session_deleted(session_id):
                return
        except Exception:
            pass
        self.event_log.save(session_id, events)
        self._invalidate_ui_events_cache(session_id)
        self._ui_user_turns_cache.pop(session_id, None)
        self._sync_ui_event_count_in_metadata(session_id, len(events))

    def _sync_ui_event_count_in_metadata(self, session_id: str, count: int) -> None:
        try:
            with self._session_metadata_lock(session_id):
                meta = self._load_metadata_unlocked(session_id)
                if not isinstance(meta, dict):
                    meta = {}
                meta["ui_event_count"] = int(count)
                self._save_metadata_unlocked(session_id, meta)
        except Exception as e:
            logger.debug("同步 ui_event_count 失败: %s", e)

    def get_ui_event_count(self, session_id: str) -> int:
        """轻量：优先读 metadata 中的 ui_event_count，避免为计数拉全量 ui_events。"""
        meta = self._load_metadata(session_id)
        if meta and "ui_event_count" in meta:
            try:
                return int(meta["ui_event_count"])
            except (TypeError, ValueError):
                pass
        n = len(self._load_ui_events(session_id))
        self._sync_ui_event_count_in_metadata(session_id, n)
        return n

    def _apply_appended_ui_event_side_effects(self, session_id: str, event_copy: Dict[str, Any]) -> None:
        if (
            event_copy.get("type") == "user"
            and not event_copy.get("_subagent_forward")
            and not event_copy.get("_recap")
            and not event_copy.get("_micro_context_shrink")
        ):
            preview = _normalize_sidebar_preview_text(str(event_copy.get("content") or ""), 180)
            activity_at = str(event_copy.get("created_at") or "").strip()
            if not activity_at:
                activity_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            with self._session_metadata_lock(session_id):
                meta = self._load_metadata_unlocked(session_id)
                if not isinstance(meta, dict):
                    meta = {}
                meta["last_user_preview"] = preview
                # Runtime V2 does not rewrite ui_events.json. Persist activity on
                # the same user-turn side effect so /sessions can reorder the row
                # immediately and retain that order after a process restart.
                meta["updated_at"] = activity_at
                self._save_metadata_unlocked(session_id, meta)
            changed = False
            with self._lock:
                for sess in self.index:
                    if sess.get("id") == session_id:
                        sess["last_user_preview"] = preview
                        sess["updated_at"] = activity_at
                        changed = True
                        break
            if changed:
                self._save_index()
            if not event_copy.get("preserve_unread_result"):
                self.clear_session_unread_result(session_id)
        elif event_copy.get("type") == "final":
            # ``final`` commits visible assistant text, but the run can still
            # fail in history finalization, SessionEnd hooks, or its durable
            # terminal commit.
            # Result attention is updated only by the terminal lifecycle path.
            pass
        elif event_copy.get("type") in ("run_interrupted", "run_failed"):
            self.mark_session_unread_result(session_id, status="failed")

    def append_ui_event(self, session_id: str, event: Dict[str, Any]) -> None:
        """追加一条与 SSE 同结构的 UI 事件（供刷新时原样重放）。"""
        if not event or not isinstance(event, dict):
            return
        runtime_v2_active = False
        runtime_v2_strict_mode = False
        try:
            from session_lifecycle import is_session_deleted

            if is_session_deleted(session_id):
                return
        except Exception:
            pass
        try:
            event_copy = json.loads(json.dumps(event, ensure_ascii=False))
            event_copy.setdefault(
                "created_at",
                datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            )
            runtime_v2_primary = None
            runtime_v2_strict = None
            try:
                from runtime_v2 import runtime_v2_primary, runtime_v2_strict

                if runtime_v2_primary():
                    runtime_v2_active = True
                    runtime_v2_strict_mode = runtime_v2_strict()
                    mirrored = self._mirror_ui_event_to_runtime_v2(session_id, event_copy)
                    if mirrored is None:
                        raise RuntimeError("Runtime V2 did not accept ui_event")
                    self._apply_appended_ui_event_side_effects(session_id, event_copy)
                    return
            except Exception as mirror_error:
                if runtime_v2_primary is not None and runtime_v2_primary():
                    if runtime_v2_strict is None or runtime_v2_strict():
                        raise
                    logger.warning("Runtime V2 mirror ui_event failed for %s: %s", session_id, mirror_error)
                    return
                logger.warning("Runtime V2 mirror ui_event failed for %s: %s", session_id, mirror_error)
            events = self._load_ui_events(session_id)
            events.append(event_copy)
            self._save_ui_events(session_id, events)
            self._apply_appended_ui_event_side_effects(session_id, event_copy)
        except Exception as e:
            logger.warning(f"append_ui_event 失败: {e}")
            if runtime_v2_active and runtime_v2_strict_mode:
                raise

    def _observe_runtime_v2_history(self, method_name: str, session_id: str, **kwargs) -> bool:
        try:
            from runtime_v2 import (
                runtime_v2_primary,
                runtime_v2_react_transaction_timeout_seconds,
            )
            from runtime_v2.history_ops import RuntimeHistoryOps

            if not runtime_v2_primary():
                return False
            ops = RuntimeHistoryOps(
                self.repository.sessions_dir,
                path_resolver=getattr(self.repository, "_path_resolver", None),
                transaction_timeout_seconds=runtime_v2_react_transaction_timeout_seconds(),
            )
            method = getattr(ops, method_name)
            method(session_id, **kwargs)
            return True
        except Exception as exc:
            logger.debug("Runtime V2 observe %s failed for session %s: %s", method_name, session_id, exc)
            return False

    def get_ui_events_for_display(self, session_id: str) -> List[dict]:
        """返回与流式接口相同结构的事件列表，供前端仅调用 renderEvent 重放。"""
        return self._load_ui_events(session_id)

    def _load_ui_events_for_active_runtime(self, session_id: str) -> List[dict]:
        """Load UI-visible history from the selected runtime."""
        if self._runtime_v2_primary():
            from runtime_v2.ui_projection import RuntimeUiProjection

            projection = RuntimeUiProjection(
                self.repository.sessions_dir,
                path_resolver=self._resolve_session_path,
            )
            return projection.read_ui_events(session_id)
        return self._load_ui_events(session_id)

    def get_ui_events_page(
        self,
        session_id: str,
        limit: int = 200,
        before_index: Optional[int] = None,
        target_index: Optional[int] = None,
        turns: Optional[int] = None,
    ) -> dict:
        """
        分页返回 ui_events。
        - **按条数（默认）**：before_index 为 None → 末尾最多 limit 条；before_index 为 N → events[max(0, N-limit): N]。
        - **按对话轮（turns）**：以 type==\"user\" 为一轮起点；每页最多包含 turns 条用户提问及其之间的全部事件。
          - before_index 为 None：最近 turns 轮（末尾窗口）。
          - before_index 为 N：紧贴当前窗口之前再加载 turns 轮，即 events[start:N]，start 取倒数第 turns 个用户索引。
        """
        events = self._load_ui_events(session_id)
        total = len(events)
        user_indices = [
            i
            for i, ev in enumerate(events)
            if isinstance(ev, dict) and ev.get("type") == "user"
        ]

        def _turn_slice_end_exclusive(end_exc: int, nt: int) -> int:
            """events[start:end_exc]，覆盖末尾若干完整「对话」（由 user 事件切开）。"""
            end_exc = max(0, min(int(end_exc), total))
            nt = max(1, min(int(nt), 50))
            before_users = [i for i in user_indices if i < end_exc]
            if not before_users:
                return 0
            if len(before_users) <= nt:
                return 0
            return before_users[len(before_users) - nt]

        if turns is not None:
            nt = max(1, min(int(turns), 50))
            if before_index is None:
                start = _turn_slice_end_exclusive(total, nt)
                slice_ev = events[start:total]
                return {
                    "events": slice_ev,
                    "total": total,
                    "range_start": start,
                    "range_end": total,
                    "has_older": start > 0,
                    "has_newer": False,
                }
            bi = max(0, min(int(before_index), total))
            start = _turn_slice_end_exclusive(bi, nt)
            slice_ev = events[start:bi]
            return {
                "events": slice_ev,
                "total": total,
                "range_start": start,
                "range_end": bi,
                "has_older": start > 0,
                "has_newer": bi < total,
            }

        lim = max(1, min(int(limit), 500))
        if target_index is not None:
            target = max(0, min(int(target_index), max(0, total - 1)))
            nt = max(1, min(int(turns) if turns is not None else 50, 50))
            target_user_pos = -1
            for pos, idx in enumerate(user_indices):
                if idx <= target:
                    target_user_pos = pos
                else:
                    break
            if target_user_pos < 0:
                start = max(0, target - (lim // 3))
                end = min(total, start + lim)
            else:
                before_turns = min(5, max(0, nt // 4))
                start_user_pos = max(0, target_user_pos - before_turns)
                end_user_pos = min(len(user_indices), target_user_pos + nt + 1)
                start = user_indices[start_user_pos]
                end = user_indices[end_user_pos] if end_user_pos < len(user_indices) else total
            if end <= start:
                end = min(total, start + 1)
            return {
                "events": events[start:end],
                "total": total,
                "range_start": start,
                "range_end": end,
                "has_older": start > 0,
                "has_newer": end < total,
                "target_index": target,
            }
        if before_index is None:
            start = max(0, total - lim)
            slice_ev = events[start:total]
            return {
                "events": slice_ev,
                "total": total,
                "range_start": start,
                "range_end": total,
                "has_older": start > 0,
                "has_newer": False,
            }
        bi = max(0, min(int(before_index), total))
        start = max(0, bi - lim)
        slice_ev = events[start:bi]
        return {
            "events": slice_ev,
            "total": total,
            "range_start": start,
            "range_end": bi,
            "has_older": start > 0,
            "has_newer": bi < total,
        }

    def get_ui_user_turns_for_toc(self, session_id: str) -> List[dict]:
        """
        侧栏「历史记录」目录：遍历 ui_events，列出每条用户消息的 event_index 与预览文案（轻量 JSON）。
        """
        def _rows_from_events(events: List[dict]) -> List[dict]:
            out: List[dict] = []
            for i, ev in enumerate(events):
                if not isinstance(ev, dict) or ev.get("type") != "user":
                    continue
                raw = ev.get("content")
                text = (raw if isinstance(raw, str) else str(raw or "")).strip()
                one_line = " ".join(text.split())
                if len(one_line) > 200:
                    one_line = one_line[:197] + "..."
                out.append({"event_index": i, "preview": one_line})
            return out

        if self._runtime_v2_primary():
            return _rows_from_events(self._load_ui_events_for_active_runtime(session_id))

        sig = self._ui_events_file_signature(session_id)
        cached = self._ui_user_turns_cache.get(session_id)
        if cached and cached[0] == sig:
            return [dict(row) for row in cached[1]]
        events = self._load_ui_events(session_id)
        out = _rows_from_events(events)
        self._ui_user_turns_cache[session_id] = (sig, [dict(row) for row in out])
        return out

    def get_todo_plan_snapshot(self, session_id: str) -> dict:
        """从 todo_plan.md 解析「## Todo 计划」，供左侧「当前计划」浮层展示。"""
        if self._runtime_v2_primary():
            try:
                from session_todo_extension import read_todo_extension

                extension = read_todo_extension(self, session_id)
                if isinstance(extension, dict):
                    return extension
            except Exception as exc:
                logger.debug("Runtime V2 Todo extension read failed for %s: %s", session_id, exc)
        raw = self.load_todo_plan(session_id)
        if not (raw or "").strip():
            kc_path = self._get_key_context_path(session_id)
            kc = ""
            if kc_path.exists():
                try:
                    kc = kc_path.read_text(encoding="utf-8")
                except Exception:
                    kc = ""
            section = _extract_todo_plan_section_raw(kc)
            if section and _todo_section_looks_like_real_plan(section):
                raw = section
        items = _parse_todo_block_from_key_context(raw) if raw.strip() else []
        done = sum(1 for t in items if t.get("status") == "completed")
        total = len(items)
        return {
            "has_plan": total > 0,
            "items": items,
            "done": done,
            "total": total,
        }

    def clear_todo_plan(self, session_id: str) -> bool:
        """用户主动清空当前会话的 todo 计划。返回是否实际清除了内容。"""
        sid = (session_id or "").strip()
        if not sid:
            return False
        todo_manager._by_session.pop(sid, None)
        if self._runtime_v2_primary():
            try:
                from session_todo_extension import read_todo_extension, write_todo_extension

                previous = read_todo_extension(self, sid)
                write_todo_extension(self, sid, [], cleared=True)
                return bool(
                    isinstance(previous, dict)
                    and (previous.get("has_plan") or previous.get("items"))
                )
            except Exception as exc:
                logger.warning("Runtime V2 clear todo failed for %s: %s", sid, exc)
                return False
        changed = False
        tp = self._get_todo_plan_path(sid)
        if tp.exists():
            try:
                prev = tp.read_text(encoding="utf-8")
            except Exception:
                prev = ""
            if prev.strip():
                changed = True
            tp.write_text("", encoding="utf-8")
        kc_path = self._get_key_context_path(sid)
        if kc_path.exists():
            try:
                kc = kc_path.read_text(encoding="utf-8")
            except Exception:
                return changed
            if not _TODO_SECTION_LINE_RE.search(kc):
                return changed
            cleaned = _strip_todo_plan_from_key_context(kc)
            if cleaned != kc:
                kc_path.write_text(cleaned, encoding="utf-8")
                changed = True
        return changed

    def dialogue_dicts_from_ui_events_file(self, session_id: str) -> List[dict]:
        """user / final 主链 dict 列表，与 dialogue_history.json 落盘规则一致；数据来自已持久化的 ui_events。"""
        events = self._load_ui_events(session_id)
        core = rebuild_core_messages_from_ui_events(events)
        return [_message_to_dict(m) for m in core]

    def backup_llm_compress_prefix(self, session_id: str, prefix_messages: List[Any]) -> Optional[str]:
        """
        在进入摘要模型、改写 llm 之前，将本轮「待压缩段」快照落盘（仅 prefix，非整段会话）。
        metadata `llm_compress_prefix_backups` 按时间追加，供改写已进摘要的用户问句时还原链路。
        """
        try:
            sid_path = self._get_session_path(session_id)
            sid_path.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            fname = f"llm_cprefix_{ts}.json"
            path = sid_path / fname
            payload = {
                "version": 1,
                "prefix": [_message_to_dict(m) for m in (prefix_messages or [])],
            }
            with path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            with self._session_metadata_lock(session_id):
                meta = self._load_metadata_unlocked(session_id)
                if not isinstance(meta, dict):
                    meta = {}
                lst = meta.get("llm_compress_prefix_backups")
                if not isinstance(lst, list):
                    lst = []
                lst.append(fname)
                meta["llm_compress_prefix_backups"] = lst
                meta["updated_at"] = datetime.now().isoformat()
                self._save_metadata_unlocked(session_id, meta)
            return fname
        except Exception as e:
            logger.warning("backup_llm_compress_prefix 失败: %s", e)
            return None

    def remove_llm_compress_prefix_backup(self, session_id: str, fname: str) -> bool:
        """
        改写流程已消费某 cprefix：删除**该文件**及「修改时间不早于」它的所有 llm_cprefix_*.json
        （即本次用到的备份 + 其后产生的较新备份），**保留**更早的备份；并同步精简 metadata。
        若磁盘上已找不到该文件名，仅尝试从 metadata 去掉对应项，不按时间批量删其它文件。
        """
        fn = (fname or "").strip()
        if not fn or not _LLM_COMPRESS_PREFIX_BACKUP_FN_RE.fullmatch(fn):
            return False
        sid = (session_id or "").strip()
        if not sid:
            return False
        sid_path = self._get_session_path(sid)
        consumed = sid_path / fn

        def _mtime_ns(p: Path) -> int:
            try:
                s = p.stat()
                return int(getattr(s, "st_mtime_ns", int(s.st_mtime * 1_000_000_000)))
            except OSError:
                return -1

        to_unlink: List[Path] = []
        if consumed.is_file():
            t0 = _mtime_ns(consumed)
            try:
                for p in sid_path.glob("llm_cprefix_*.json"):
                    if not p.is_file():
                        continue
                    if not _LLM_COMPRESS_PREFIX_BACKUP_FN_RE.fullmatch(p.name):
                        continue
                    if _mtime_ns(p) >= t0:
                        to_unlink.append(p)
            except Exception as e:
                logger.warning("remove_llm_compress_prefix_backup 枚举 cprefix 失败: %s", e)
        remove_names = {p.name for p in to_unlink}
        if not remove_names:
            remove_names = {fn}

        changed = False
        try:
            with self._session_metadata_lock(sid):
                meta = self._load_metadata_unlocked(sid)
                if not isinstance(meta, dict):
                    meta = {}
                lst = meta.get("llm_compress_prefix_backups")
                if not isinstance(lst, list):
                    lst = []
                new_lst = [x for x in lst if isinstance(x, str) and x not in remove_names]
                if new_lst != lst:
                    meta["llm_compress_prefix_backups"] = new_lst
                    meta["updated_at"] = datetime.now().isoformat()
                    self._save_metadata_unlocked(sid, meta)
                    changed = True
        except Exception as e:
            logger.warning("remove_llm_compress_prefix_backup 更新 metadata 失败: %s", e)
            return False

        for p in to_unlink:
            try:
                if p.is_file():
                    p.unlink()
                    changed = True
            except Exception as e:
                logger.warning("remove_llm_compress_prefix_backup 删文件失败 %s: %s", p, e)
        if to_unlink and len(to_unlink) > 1:
            logger.info(
                "改写后已移除 cprefix 共 %s 个（消费 %s 及同时间或更新的备份）",
                len(to_unlink),
                fn,
            )
        return changed

    def _rebuild_llm_work_from_ui(
        self,
        session_id: str,
        kept_events: List[dict],
        *,
        all_events: Optional[List[dict]] = None,
        before_index: Optional[int] = None,
        for_branch: bool = False,
        llm_raw: Optional[List] = None,
        work_raw: Optional[List] = None,
    ) -> Tuple[List[dict], List[dict], Optional[str]]:
        """以 ui_events 保留区为准对齐 llm_history / work_messages（改写/分支/repair/reconcile 共用）。"""
        n_ui = _count_ui_user_events(kept_events)
        ui_plains = _ui_user_plains_from_events(kept_events)
        if llm_raw is None:
            llm_raw = self._load_llm_history(session_id)
        if work_raw is None:
            work_raw = self._load_work_messages(session_id)
        llm_clean = [x for x in (llm_raw or []) if isinstance(x, dict)]
        work_clean = [x for x in (work_raw or []) if isinstance(x, dict)]
        new_work = trim_message_dicts_by_kept_user_turns(work_clean, n_ui)
        anchor_plain, anchor_raw = "", ""
        drop_anchor = False
        if before_index is not None and all_events is not None:
            anchor_plain, anchor_raw = _boundary_user_from_truncate(
                kept_events, all_events, before_index, for_branch=for_branch
            )
            drop_anchor = bool(
                not for_branch
                and before_index < len(all_events)
                and isinstance(all_events[before_index], dict)
                and all_events[before_index].get("type") == "user"
            )
        new_llm, consumed = trim_llm_dicts_for_rewrite(
            llm_clean,
            n_kept_users=n_ui,
            ui_user_plains=ui_plains,
            session_id=session_id,
            work_dicts=new_work,
            anchor_plain=anchor_plain,
            anchor_user_raw=anchor_raw,
            drop_anchor_turn=drop_anchor,
        )
        if llm_history_dicts_appear_compacted(new_llm):
            new_work = _strip_micro_shrink_legacy_user_turns(new_work)
        return new_llm, new_work, consumed

    def truncate_session_at_event_index(
        self,
        session_id: str,
        before_index: int,
        *,
        truncate_before_seq: Optional[int] = None,
        boundary_for_branch: bool = False,
        create_backup: bool = True,
    ) -> bool:
        """
        保留 ui_events[0:before_index]（下标为 before_index 及之后均丢弃），
        并据此裁剪 dialogue、work_messages、llm_history；**不清空 key_context**。
        work 按保留 user 轮数裁剪；已压缩 llm 与改写/分支同一套边界 + cprefix 对齐。
        boundary_for_branch=True 时按「分支点 final 前 user」取锚点，且不裁掉该 user 轮。
        """
        try:
            events = self._load_ui_events_for_active_runtime(session_id)
            n = len(events)
            if before_index < 0:
                return False
            runtime_truncate_keep_seq: Optional[int] = None
            runtime_truncate_target_seq: Optional[int] = None
            runtime_seq_truncate = False
            if self._runtime_v2_primary():
                try:
                    from runtime_v2.ui_projection import RuntimeUiProjection

                    projection = RuntimeUiProjection(
                        self.repository.sessions_dir,
                        path_resolver=self._resolve_session_path,
                    )
                    if truncate_before_seq is not None:
                        target_seq = int(truncate_before_seq)
                    else:
                        target_seq = int(projection.ui_index_to_runtime_seq(session_id, before_index) or 0)
                    if target_seq <= 0:
                        return False
                    end_index = projection.runtime_seq_to_ui_end_index(session_id, target_seq)
                    keep_seq = projection.previous_visible_runtime_seq_before(session_id, target_seq)
                    if end_index is None or keep_seq is None:
                        return False
                    before_index = max(0, int(end_index) - 1)
                    runtime_truncate_target_seq = int(target_seq)
                    runtime_truncate_keep_seq = int(keep_seq)
                    runtime_seq_truncate = True
                    events = projection.read_ui_events_fast(session_id)
                    n = len(events)
                except (TypeError, ValueError):
                    return False
            if before_index > n:
                before_index = n
            if self._runtime_v2_primary():
                if runtime_truncate_target_seq is not None and runtime_truncate_keep_seq is not None:
                    truncated = self._observe_runtime_v2_history(
                        "truncate_visible_history_before_seq",
                        session_id,
                        target_seq=runtime_truncate_target_seq,
                        keep_to_seq=runtime_truncate_keep_seq,
                        reason="runtime_v2_truncate",
                    )
                    if truncated is False:
                        logger.warning(
                            "Runtime V2 truncate failed: session=%s target_seq=%s keep_to_seq=%s",
                            session_id,
                            runtime_truncate_target_seq,
                            runtime_truncate_keep_seq,
                        )
                        return False
                else:
                    logger.warning(
                        "Runtime V2 truncate refused without runtime seq: session=%s before_index=%s truncate_before_seq=%s",
                        session_id,
                        before_index,
                        truncate_before_seq,
                    )
                    return False
                return True
            new_events = events[:before_index]
            if create_backup:
                self._backup_session_before_truncate(
                    session_id,
                    before_index,
                    event_count=n,
                )
            self._save_ui_events(session_id, new_events)
            new_llm, new_work, consumed_cprefix = self._rebuild_llm_work_from_ui(
                session_id,
                new_events,
                all_events=events,
                before_index=before_index,
                for_branch=boundary_for_branch,
            )
            self._save_llm_history(session_id, new_llm)
            self._save_work_messages(session_id, new_work)
            self._save_dialogue_history(
                session_id, self.dialogue_dicts_from_ui_events_file(session_id)
            )
            if consumed_cprefix:
                self.remove_llm_compress_prefix_backup(session_id, consumed_cprefix)
            if not runtime_seq_truncate:
                self._observe_runtime_v2_history(
                    "observe_legacy_truncate",
                    session_id,
                    before_index=before_index,
                    old_event_count=n,
                    new_event_count=len(new_events),
                    boundary_for_branch=boundary_for_branch,
                )
            return True
        except Exception as e:
            logger.warning(f"truncate_session_at_event_index 失败: {e}")
            return False

    _BRANCH_NAME_PREFIX_RE = re.compile(r"^\((\d+)\)(.*)$", re.DOTALL)

    @classmethod
    def _session_root_name(cls, name: str) -> str:
        n = (name or "").strip() or "新会话"
        while True:
            m = cls._BRANCH_NAME_PREFIX_RE.match(n)
            if not m:
                return n
            n = (m.group(2) or "").strip() or "新会话"

    def _next_branch_session_name(self, source_session_id: str) -> str:
        """在原会话根名称前分配下一个 (n) 前缀，如 (1)项目讨论、(2)项目讨论。"""
        meta = self._load_metadata(source_session_id)
        root = self._session_root_name(meta.get("name") or "新会话")
        max_n = 0
        for s in self.index:
            nm = str(s.get("name") or "")
            if nm == root:
                continue
            m = re.match(r"^\((\d+)\)" + re.escape(root) + r"$", nm)
            if m:
                max_n = max(max_n, int(m.group(1)))
        return f"({max_n + 1}){root}"

    def branch_session_at_event_index(
        self,
        source_session_id: str,
        before_index: int,
        *,
        branch_after_seq: Optional[int] = None,
    ) -> Optional[dict]:
        """
        从 source 在 ui_events[0:before_index] 处复制出新会话（原会话不变）。
        返回 {"session_id", "name"}，失败返回 None。
        """
        try:
            sid = self._normalize_session_id(source_session_id)
            src_path = self._get_session_path(sid)
            if not src_path.is_dir():
                return {"ok": False, "error": "source_not_found"}
            events = self._load_ui_events_for_active_runtime(sid)
            n = len(events)
            if before_index < 0:
                return {"ok": False, "error": "invalid_before_index"}
            if before_index > n and not (self._runtime_v2_primary() and branch_after_seq is not None):
                return {"ok": False, "error": "before_index_after_end", "event_count": n}
            branch_from_seq: Optional[int] = None
            if self._runtime_v2_primary() and branch_after_seq is not None:
                try:
                    from runtime_v2.ui_projection import RuntimeUiProjection

                    projection = RuntimeUiProjection(
                        self.repository.sessions_dir,
                        path_resolver=self._resolve_session_path,
                    )
                    seq = int(branch_after_seq)
                    source_event = None
                    for candidate in projection.event_log.iter_events(sid):
                        if int(candidate.seq) == seq:
                            source_event = candidate
                            break
                    events = projection.read_ui_events_fast(sid)
                    n = len(events)
                    if source_event is None:
                        logger.info(
                            "Runtime V2 branch after_seq not found; falling back to before_index: session=%s seq=%s before_index=%s",
                            sid,
                            seq,
                            before_index,
                        )
                    else:
                        source_ui = projection._event_to_ui(sid, source_event)
                        if not source_ui or source_ui.get("type") != "final":
                            logger.info(
                                "Runtime V2 branch after_seq is not final; falling back to before_index: session=%s seq=%s type=%s before_index=%s",
                                sid,
                                seq,
                                str(source_ui.get("type") if isinstance(source_ui, dict) else source_event.type),
                                before_index,
                            )
                        else:
                            seq_before_index = projection.runtime_seq_to_ui_end_index(sid, seq)
                            if seq_before_index is None:
                                logger.info(
                                    "Runtime V2 branch after_seq not visible; falling back to before_index: session=%s seq=%s before_index=%s",
                                    sid,
                                    seq,
                                    before_index,
                                )
                            else:
                                before_index = int(seq_before_index)
                                branch_from_seq = seq
                    if before_index > n:
                        return {"ok": False, "error": "before_index_after_end", "event_count": n}
                except (TypeError, ValueError):
                    return {"ok": False, "error": "invalid_runtime_seq"}
            new_id = str(uuid.uuid4())
            dst_path = self._get_session_path(new_id)
            if dst_path.exists():
                return {"ok": False, "error": "destination_exists"}
            branch_name = self._next_branch_session_name(sid)
            now_iso = datetime.now().isoformat()
            new_events = events[:before_index]
            if not new_events or not isinstance(new_events[-1], dict) or new_events[-1].get("type") != "final":
                last_type = ""
                if new_events and isinstance(new_events[-1], dict):
                    last_type = str(new_events[-1].get("type") or "")
                return {
                    "ok": False,
                    "error": "branch_target_not_final",
                    "event_count": n,
                    "last_type": last_type,
                }
            if self._runtime_v2_primary():
                dst_path.mkdir(parents=True, exist_ok=False)
                meta = self._load_metadata(sid)
                if not isinstance(meta, dict):
                    meta = {}
                meta["name"] = branch_name
                meta["created_at"] = now_iso
                meta["updated_at"] = now_iso
                meta["archived"] = False
                meta["pinned"] = False
                meta["todo"] = False
                meta["goal_review_pending"] = False
                meta.pop("pinned_at", None)
                try:
                    src_meta = self._load_metadata(sid) or {}
                    src_auth = src_meta.get("authorized_dirs")
                    if isinstance(src_auth, list) and src_auth:
                        meta["authorized_dirs"] = [str(x) for x in src_auth if str(x).strip()]
                    else:
                        meta["authorized_dirs"] = [str(WORK_DIR.resolve())]
                except Exception:
                    meta["authorized_dirs"] = [str(WORK_DIR.resolve())]
                meta["branched_from"] = sid
                meta["branch_before_index"] = before_index
                meta["ui_event_count"] = len(new_events)
                meta["last_user_preview"] = ""
                for ev in reversed(new_events):
                    if isinstance(ev, dict) and ev.get("type") == "user":
                        meta["last_user_preview"] = _normalize_sidebar_preview_text(
                            str(ev.get("content") or ""),
                            180,
                        )
                        break
                meta.pop("truncate_backups", None)
                meta.pop("last_truncate_backup", None)
                meta.pop("pending_subagent_notifications", None)
                if branch_from_seq is None:
                    try:
                        from runtime_v2.ui_projection import RuntimeUiProjection

                        mapped_seq = RuntimeUiProjection(
                            self.repository.sessions_dir,
                            path_resolver=self._resolve_session_path,
                        ).ui_index_to_runtime_seq(sid, before_index - 1)
                        branch_from_seq = int(mapped_seq) if mapped_seq is not None else before_index
                    except Exception as exc:
                        logger.debug("Runtime V2 branch source seq mapping failed for %s: %s", sid, exc)
                        branch_from_seq = before_index
                try:
                    branch_written = self._observe_runtime_v2_history(
                        "create_branch",
                        new_id,
                        source_session_id=sid,
                        branch_from_seq=int(branch_from_seq),
                        name=branch_name,
                    )
                    if branch_written is False:
                        raise RuntimeError("Runtime V2 branch seed failed")
                except Exception as exc:
                    # Do not publish a branch whose UI/model/context seed only
                    # completed partially. The id is newly allocated here.
                    try:
                        shutil.rmtree(dst_path)
                    except Exception:
                        logger.warning("failed to roll back partial Runtime V2 branch %s", new_id, exc_info=True)
                    return {"ok": False, "error": "branch_seed_failed", "detail": str(exc)}
                self._save_metadata(new_id, meta)
                self.index.append({
                    "id": new_id,
                    "name": branch_name,
                    "created_at": now_iso,
                    "updated_at": now_iso,
                    "archived": False,
                    "pinned": False,
                    "todo": False,
                    "goal_review_pending": False,
                    "pinned_at": None,
                })
                self._save_index()
                return {
                    "ok": True,
                    "session_id": new_id,
                    "name": branch_name,
                    "session": {
                        "id": new_id,
                        "name": branch_name,
                        "created_at": now_iso,
                        "updated_at": now_iso,
                        "archived": False,
                        "pinned": False,
                        "todo": False,
                        "goal_review_pending": False,
                        "pinned_at": None,
                    },
                }
            src_llm = self._load_llm_history(sid)
            src_work = self._load_work_messages(sid)
            new_llm, new_work, _ = self._rebuild_llm_work_from_ui(
                sid,
                new_events,
                all_events=events,
                before_index=before_index,
                for_branch=True,
                llm_raw=src_llm,
                work_raw=src_work,
            )
            dst_path.mkdir(parents=True, exist_ok=False)
            self._copy_branch_sidecar_files(sid, new_id)
            self._save_ui_events(new_id, new_events)
            self._save_llm_history(new_id, new_llm)
            self._save_work_messages(new_id, new_work)
            self._save_dialogue_history(
                new_id,
                [_message_to_dict(m) for m in rebuild_core_messages_from_ui_events(new_events)],
            )
            meta = self._load_metadata(sid)
            if not isinstance(meta, dict):
                meta = {}
            meta["name"] = branch_name
            meta["created_at"] = now_iso
            meta["updated_at"] = now_iso
            meta["archived"] = False
            meta["pinned"] = False
            meta["todo"] = False
            meta["goal_review_pending"] = False
            meta.pop("pinned_at", None)
            meta["branched_from"] = sid
            meta["branch_before_index"] = before_index
            meta["ui_event_count"] = len(new_events)
            meta["last_user_preview"] = ""
            for ev in reversed(new_events):
                if isinstance(ev, dict) and ev.get("type") == "user":
                    meta["last_user_preview"] = _normalize_sidebar_preview_text(
                        str(ev.get("content") or ""),
                        180,
                    )
                    break
            meta.pop("truncate_backups", None)
            meta.pop("last_truncate_backup", None)
            meta.pop("pending_subagent_notifications", None)
            self._save_metadata(new_id, meta)
            self.index.append({
                "id": new_id,
                "name": branch_name,
                "created_at": now_iso,
                "updated_at": now_iso,
                "archived": False,
                "pinned": False,
                "todo": False,
                "goal_review_pending": False,
                "pinned_at": None,
            })
            self._save_index()
            logger.info(
                "创建分支会话 %s ← %s before_index=%s name=%s",
                new_id,
                sid,
                before_index,
                branch_name,
            )
            summary = self.get_session_summary(new_id) or {
                "id": new_id,
                "name": branch_name,
                "created_at": now_iso,
                "updated_at": now_iso,
                "archived": False,
                "pinned": False,
                "todo": False,
                "goal_review_pending": False,
                "pinned_at": None,
            }
            return {"ok": True, "session_id": new_id, "name": branch_name, "session": summary}
        except Exception as e:
            logger.warning("branch_session_at_event_index 失败: %s", e)
            return {"ok": False, "error": "exception", "detail": str(e)}

    def repair_compacted_llm_history_from_ui(self, session_id: str) -> bool:
        """
        已压缩会话：按当前 ui_events / work_messages 重新对齐 llm_history（与改写/分支同一套边界逻辑）。
        若 llm 已被错误撑大且 metadata 有 branched_from，先尝试从源会话恢复 llm 再对齐。
        """
        try:
            if self._runtime_v2_primary():
                logger.info("skip legacy compacted llm repair in Runtime V2: session=%s", session_id)
                return False
            events = self._load_ui_events(session_id)
            if not llm_history_dicts_appear_compacted(
                [x for x in self._load_llm_history(session_id) or [] if isinstance(x, dict)]
            ):
                return False
            llm_raw = self._load_llm_history(session_id)
            work_raw = self._load_work_messages(session_id)
            meta = self._load_metadata(session_id)
            parent_id = str((meta or {}).get("branched_from") or "").strip()
            if parent_id and not llm_history_dicts_appear_compacted(
                [x for x in (llm_raw or []) if isinstance(x, dict)]
            ):
                try:
                    pllm = self._load_llm_history(parent_id)
                    if llm_history_dicts_appear_compacted(
                        [x for x in pllm if isinstance(x, dict)]
                    ):
                        llm_raw = pllm
                        logger.info(
                            "repair：llm 非压缩形态，已从源会话 %s 恢复后再对齐",
                            parent_id,
                        )
                except Exception:
                    pass
            bbi = meta.get("branch_before_index") if isinstance(meta, dict) else None
            if parent_id and bbi is not None:
                bi = int(bbi)
                new_llm, new_work, consumed = self._rebuild_llm_work_from_ui(
                    session_id,
                    events[:bi],
                    all_events=events,
                    before_index=bi,
                    for_branch=True,
                    llm_raw=llm_raw,
                    work_raw=work_raw,
                )
            else:
                new_llm, new_work, consumed = self._rebuild_llm_work_from_ui(
                    session_id,
                    events,
                    all_events=events,
                    before_index=len(events),
                    for_branch=True,
                    llm_raw=llm_raw,
                    work_raw=work_raw,
                )
            if new_llm == llm_raw and new_work == work_raw:
                return False
            self._save_llm_history(session_id, new_llm)
            self._save_work_messages(session_id, new_work)
            self._save_dialogue_history(
                session_id, self.dialogue_dicts_from_ui_events_file(session_id)
            )
            if consumed:
                self.remove_llm_compress_prefix_backup(session_id, consumed)
            logger.info(
                "repair_compacted_llm_history_from_ui: session=%s llm %s→%s",
                session_id,
                len(llm_raw),
                len(new_llm),
            )
            return True
        except Exception as e:
            logger.warning("repair_compacted_llm_history_from_ui 失败: %s", e)
            return False

    def reconcile_llm_work_to_ui_user_count(self, session_id: str, include_work: bool = True) -> bool:
        """
        以 ui_events 中 type=user 条数为边界，只裁剪 llm_history / work_messages 尾部多写盘回合。
        用于修复：请求在「已 append user、已写 New Agent Loop Start」后因 400/异常中止，下一轮又叠
        加 human，而 ui_events 因截断或未重复记录导致比 llm 少 user 条数的情况。
        禁止在用户轮数未超出 ui_events 时重建 llm_history，避免把完整 ReAct 历史降级成 user/final 主对话。
        """
        try:
            if self._runtime_v2_primary():
                logger.info("skip legacy llm/work reconcile in Runtime V2: session=%s", session_id)
                return False
            events = self._load_ui_events(session_id)
            llm_raw = self._load_llm_history(session_id)
            n_ui = _count_ui_user_events(events)
            llm_users = _count_session_user_dicts([x for x in (llm_raw or []) if isinstance(x, dict)])
            if llm_users <= n_ui:
                if include_work:
                    work_raw_check = self._load_work_messages(session_id)
                    work_users = _count_session_user_dicts(
                        [x for x in (work_raw_check or []) if isinstance(x, dict)]
                    )
                    if work_users > n_ui:
                        new_work = trim_message_dicts_by_kept_user_turns(work_raw_check, n_ui)
                        if new_work != work_raw_check:
                            self._save_work_messages(session_id, new_work)
                            logger.info(
                                "已按 ui_events 用户数=%s 裁剪 work 尾部多写盘回合 work %s→%s；llm 用户数=%s 未超出，未重建 llm_history",
                                n_ui,
                                len(work_raw_check),
                                len(new_work),
                                llm_users,
                            )
                            return True
                return False
            work_raw = self._load_work_messages(session_id) if include_work else []
            new_llm, new_work, _ = self._rebuild_llm_work_from_ui(
                session_id, events, llm_raw=llm_raw, work_raw=work_raw
            )
            if new_llm == llm_raw and (not include_work or new_work == work_raw):
                return False
            logger.info(
                "已按 ui_events 用户数=%s 对齐裁剪 llm/work（移除多写盘回合） llm %s→%s work %s→%s",
                _count_ui_user_events(events),
                len(llm_raw),
                len(new_llm),
                len(work_raw),
                len(new_work),
            )
            self._save_llm_history(session_id, new_llm)
            if include_work:
                self._save_work_messages(session_id, new_work)
            return True
        except Exception as e:
            logger.warning(f"reconcile_llm_work_to_ui_user_count 失败: {e}")
            return False

    def append_ui_events_tail(self, session_id: str, tail: List[dict]) -> bool:
        """
        在「改写」误截断后，将此前保存的 events 段接回 ui_events 末尾，并全量重算工作消息/llm 主链。
        """
        if not tail:
            return True
        try:
            clean = [deepcopy_json_dict(e) for e in tail if isinstance(e, dict)]
            if not clean:
                return True
            if self._runtime_v2_primary():
                try:
                    from runtime_v2 import RuntimeUiProjection

                    projection = RuntimeUiProjection(
                        self.repository.sessions_dir,
                        path_resolver=self._resolve_session_path,
                    )
                    current = projection.read_ui_events_fast(session_id)
                    merged = [
                        {
                            key: value
                            for key, value in deepcopy_json_dict(event).items()
                            if key not in {"runtime_seq", "runtime_event_type", "rewritten", "rewritten_by_seq"}
                        }
                        for event in list(current) + clean
                        if isinstance(event, dict)
                    ]
                    projection.replace_from_ui_events(session_id, merged, reason="runtime_v2_tail_restore_replace")
                except Exception as restore_error:
                    logger.warning("Runtime V2 append ui_events tail failed for %s: %s", session_id, restore_error)
                    return False
                return True
            merged = list(self._load_ui_events(session_id)) + clean
            self._save_ui_events(session_id, merged)
            new_llm, new_work, _ = self._rebuild_llm_work_from_ui(session_id, merged)
            self._save_work_messages(session_id, new_work)
            self._save_llm_history(session_id, new_llm)
            self._save_dialogue_history(
                session_id, self.dialogue_dicts_from_ui_events_file(session_id)
            )
            return True
        except Exception as e:
            logger.warning(f"append_ui_events_tail 失败: {e}")
            return False

    def _backup_session_before_truncate(
        self,
        session_id: str,
        before_index: int,
        *,
        event_count: int,
    ) -> Optional[str]:
        """Snapshot session files before destructive history truncation."""
        sid = self._normalize_session_id(session_id)
        if not sid:
            return None
        try:
            sess = self._get_session_path(sid)
            if not sess.is_dir():
                return None
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            backup_dir = sess / "truncate_backups" / f"{ts}_before_{before_index}"
            backup_dir.mkdir(parents=True, exist_ok=False)
            names = [
                "ui_events.json",
                "work_messages.json",
                "llm_history.json",
                "dialogue_history.json",
                "metadata.json",
                "key_context.md",
                "todo_plan.md",
            ]
            for name in names:
                src = sess / name
                if src.exists():
                    shutil.copy2(src, backup_dir / name)
            info = {
                "created_at": datetime.now().isoformat(),
                "session_id": sid,
                "before_index": int(before_index),
                "event_count": int(event_count),
            }
            with (backup_dir / "backup_info.json").open("w", encoding="utf-8") as f:
                json.dump(info, f, indent=2, ensure_ascii=False)
            with self._session_metadata_lock(sid):
                meta = self._load_metadata_unlocked(sid)
                if not isinstance(meta, dict):
                    meta = {}
                backups = meta.get("truncate_backups")
                if not isinstance(backups, list):
                    backups = []
                rel = str(backup_dir.relative_to(sess)).replace("\\", "/")
                backups.append(rel)
                meta["truncate_backups"] = backups[-20:]
                meta["last_truncate_backup"] = rel
                self._save_metadata_unlocked(sid, meta)
            logger.info(
                "truncate backup created: session=%s before_index=%s events=%s dir=%s",
                sid,
                before_index,
                event_count,
                backup_dir,
            )
            return str(backup_dir)
        except Exception as e:
            logger.warning("truncate backup failed for %s: %s", session_id, e)
            return None

    def _save_dialogue_history(self, session_id: str, dialogue_only: List[dict]) -> None:
        path = self._get_dialogue_history_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dialogue_only, f, indent=2, ensure_ascii=False)

    def _save_work_messages(self, session_id: str, work_messages: List[dict]) -> None:
        path = self._get_work_messages_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(work_messages, f, indent=2, ensure_ascii=False)

    def _load_work_messages(self, session_id: str) -> List[dict]:
        path = self._get_work_messages_path(session_id)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("Failed to load work_messages for session %s: %s", session_id, e)
                return []
        return []

    def _save_llm_history(self, session_id: str, llm_history: List[dict]):
        path = self._get_llm_history_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(llm_history, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

    def _load_llm_history(self, session_id: str) -> List[dict]:
        path = self._get_llm_history_path(session_id)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return []   # 新会话返回空列表

    def _save_key_context(self, session_id: str, text: str) -> None:
        path = self._get_key_context_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text or "")

    def _load_key_context(self, session_id: str) -> str:
        path = self._get_key_context_path(session_id)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except OSError:
                return ""
        return ""

    def load_key_context(self, session_id: str) -> str:
        return self._load_key_context(session_id)

    def save_key_context(self, session_id: str, text: str) -> None:
        self._save_key_context(session_id, text)

    def append_key_context_history(self, session_id: str, text: str, reason: str = "") -> None:
        body = (text or "").strip()
        if not body:
            return
        p = self._get_key_context_history_path(session_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().isoformat(timespec="seconds")
        title = f"\n\n---\n\n## {ts}"
        if reason:
            title += f" · {reason}"
        try:
            with p.open("a", encoding="utf-8") as f:
                f.write(title + "\n\n" + body.rstrip() + "\n")
        except Exception as e:
            logger.warning("append_key_context_history 失败: %s", e)

    def _save_metadata_unlocked(self, session_id: str, metadata: dict) -> None:
        if self._is_deleted_session(session_id):
            return
        self.repository.save_metadata_atomic(session_id, metadata)
        self._set_interrupt_cache_from_metadata(session_id, metadata)

    def _load_metadata_unlocked(self, session_id: str) -> dict:
        return self.repository.load_metadata(session_id)

    def _save_metadata(self, session_id: str, metadata: dict) -> None:
        if self._is_deleted_session(session_id):
            return
        with self._session_metadata_lock(session_id):
            self._save_metadata_unlocked(session_id, metadata)

    def _load_metadata(self, session_id: str) -> dict:
        with self._session_metadata_lock(session_id):
            return self._load_metadata_unlocked(session_id)

    def get_session_prompt_language(self, session_id: str) -> str:
        """Return the language selected by the session's frontend, defaulting to Chinese."""
        try:
            return normalize_prompt_language(
                (self._load_metadata(session_id) or {}).get("prompt_language")
            )
        except Exception:
            return "zh-CN"

    def set_session_prompt_language(self, session_id: str, language: str) -> None:
        """Persist the frontend language used for subsequent model-facing prompts."""
        sid = str(session_id or "").strip()
        if not sid:
            return
        normalized = normalize_prompt_language(language)
        with self._session_metadata_lock(sid):
            metadata = self._load_metadata_unlocked(sid)
            if not isinstance(metadata, dict):
                metadata = {}
            if metadata.get("prompt_language") == normalized:
                return
            metadata["prompt_language"] = normalized
            metadata["updated_at"] = datetime.now().isoformat()
            self._save_metadata_unlocked(sid, metadata)

    def _set_interrupt_cache_from_metadata(self, session_id: str, metadata: dict) -> None:
        sid = (session_id or "").strip()
        if not sid:
            return
        requested = bool((metadata or {}).get("interrupt_requested", False))
        run_id = str((metadata or {}).get("interrupt_run_id") or "").strip()
        reason = str((metadata or {}).get("interrupt_reason") or "").strip()
        with self._interrupt_cache_lock:
            self._interrupt_cache[sid] = (requested, run_id, reason)

    def _get_interrupt_cache(self, session_id: str) -> Optional[Tuple[bool, str, str]]:
        sid = (session_id or "").strip()
        if not sid:
            return None
        with self._interrupt_cache_lock:
            return self._interrupt_cache.get(sid)

    def _copy_branch_sidecar_files(self, source_session_id: str, new_session_id: str) -> None:
        """Copy lightweight state files needed by a branch without cloning backups/history folders."""
        src = self._get_session_path(source_session_id)
        dst = self._get_session_path(new_session_id)
        dst.mkdir(parents=True, exist_ok=True)
        names = [
            "key_context.md",
            "key_context_history.md",
            "todo_plan.md",
        ]
        for name in names:
            p = src / name
            if p.is_file():
                shutil.copy2(p, dst / name)
        try:
            for p in src.glob("llm_cprefix_*.json"):
                if p.is_file() and _LLM_COMPRESS_PREFIX_BACKUP_FN_RE.fullmatch(p.name):
                    shutil.copy2(p, dst / p.name)
        except Exception as e:
            logger.warning("copy branch cprefix backups failed: %s", e)

    def get_session_subagent_depth(self, session_id: str) -> int:
        """当前会话在 subagent 树中的深度；根会话为 0。"""
        try:
            meta = self._load_metadata(session_id)
            if not isinstance(meta, dict):
                return 0
            return max(0, int(meta.get("subagent_depth") or 0))
        except Exception:
            return 0

    def create_subagent_session(
        self,
        parent_session_id: str,
        description: str,
        subagent_type: str,
        depth: int,
        *,
        model_profile_id: str = "",
        executor_model: str = "",
        executor_llm_type: str = "",
        readonly_strict: bool = False,
        best_of_run_id: str = "",
        best_of_attempt: int = 0,
        forked_from_parent: bool = False,
    ) -> str:
        """在父会话目录 subagents/ 下创建隔离子会话。"""
        parent_id = self._normalize_session_id(parent_session_id)
        desc = (description or "subagent").strip()[:80] or "subagent"
        stype = (subagent_type or "generalPurpose").strip()
        child_id = str(uuid.uuid4())
        child_path = self._get_subagent_session_path(parent_id, child_id)
        if child_path.exists():
            raise RuntimeError(f"subagent 路径已存在: {child_id}")
        child_path.mkdir(parents=True, exist_ok=False)
        now_iso = datetime.now().isoformat()
        metadata: Dict[str, Any] = {
            "name": f"[sub] {desc}",
            "created_at": now_iso,
            "updated_at": now_iso,
            "archived": False,
            "pinned": False,
            "todo": False,
            "goal_review_pending": False,
            "is_subagent": True,
            "parent_session_id": parent_id,
            "subagent_type": stype,
            "subagent_description": desc,
            "subagent_depth": max(1, int(depth)),
            "subagent_max_iter": SUBAGENT_MAX_REACT_ITER,
            "readonly_strict": bool(readonly_strict),
            "forked_from_parent": bool(forked_from_parent),
        }
        try:
            parent_metadata = self._load_metadata(parent_id) or {}
            parent_prompt_language = parent_metadata.get("prompt_language")
            if parent_prompt_language:
                metadata["prompt_language"] = normalize_prompt_language(parent_prompt_language)
            parent_auth = parent_metadata.get("authorized_dirs")
            if isinstance(parent_auth, list) and parent_auth:
                metadata["authorized_dirs"] = [str(x) for x in parent_auth if str(x).strip()]
            else:
                metadata["authorized_dirs"] = [str(WORK_DIR.resolve())]
        except Exception:
            try:
                metadata["authorized_dirs"] = [str(WORK_DIR.resolve())]
            except Exception:
                pass
        mpi = (model_profile_id or "").strip()
        if mpi:
            metadata["model_profile_id"] = mpi
        em = (executor_model or "").strip()
        if em:
            metadata["executor_model"] = em
        elt = (executor_llm_type or "").strip()
        if elt:
            metadata["executor_llm_type"] = elt
        if best_of_run_id:
            metadata["best_of_run_id"] = str(best_of_run_id)
            metadata["best_of_attempt"] = int(best_of_attempt or 0)
        self._register_subagent(child_id, parent_id)
        self._save_metadata(child_id, metadata)
        # Permission mode is global. Subagents resolve the current global mode
        # at every authorization and never persist a session-local copy.
        if self._runtime_v2_primary():
            try:
                self._runtime_subagent_store().upsert_task(parent_id, child_id, {
                    "agent_id": child_id,
                    "parent_session_id": parent_id,
                    "description": desc,
                    "subagent_type": stype,
                    "status": "pending",
                    **metadata,
                })
            except Exception as exc:
                logger.debug("Runtime V2 subagent task seed failed: %s", exc)
        else:
            self._save_work_messages(child_id, [])
            self._save_llm_history(child_id, [])
            self._save_key_context(child_id, "")
            self._save_ui_events(child_id, [])
            self._save_dialogue_history(child_id, [])
        logger.info(
            "创建 subagent 会话 %s ← parent=%s path=%s type=%s depth=%s",
            child_id,
            parent_id,
            child_path,
            stype,
            depth,
        )
        return child_id

    def fork_subagent_from_parent(
        self,
        parent_session_id: str,
        description: str,
        subagent_type: str,
        depth: int,
        *,
        model_profile_id: str = "",
        executor_model: str = "",
        executor_llm_type: str = "",
        readonly_strict: bool = False,
        parent_runtime_config: Optional[Dict[str, Any]] = None,
        inherit_parent_model_runtime: bool = True,
    ) -> str:
        """resume=self：引用父模型前缀并精确冻结请求配置。"""
        import copy

        parent_id = self._normalize_session_id(parent_session_id)
        child_id = self.create_subagent_session(
            parent_id,
            description,
            subagent_type,
            depth,
            model_profile_id=model_profile_id,
            executor_model=executor_model,
            executor_llm_type=executor_llm_type,
            readonly_strict=readonly_strict,
            forked_from_parent=True,
        )
        runtime_config = (
            json.loads(json.dumps(parent_runtime_config, ensure_ascii=False))
            if isinstance(parent_runtime_config, dict)
            else {}
        )
        model_runtime = (
            runtime_config.get("model_runtime")
            if inherit_parent_model_runtime
            else None
        )
        if not isinstance(model_runtime, dict):
            model_runtime = executor_runtime_snapshot_for_session(
                parent_id if inherit_parent_model_runtime else child_id
            )
        runtime_config["model_runtime"] = model_runtime
        self.patch_subagent_metadata(
            child_id,
            {
                "fork_runtime_config": runtime_config,
                "fork_model_runtime": dict(model_runtime or {}),
                "fork_prefix_mode": "immutable_model_prefix",
            },
        )
        if self._runtime_v2_primary():
            try:
                from runtime_v2 import RuntimeHistoryOps

                ops = RuntimeHistoryOps(
                    self.repository.sessions_dir,
                    path_resolver=self._resolve_session_path,
                )
                anchor_seq = max(0, int(ops.event_log.next_seq(parent_id)) - 1)
                if anchor_seq > 0:
                    ops.create_reference_branch(
                        child_id,
                        parent_id,
                        anchor_seq,
                        name=description,
                    )
                    self.patch_subagent_metadata(
                        child_id,
                        {
                            "fork_source_session_id": parent_id,
                            "fork_source_runtime_seq": anchor_seq,
                        },
                    )
                else:
                    RuntimeHistoryOps(
                        self.repository.sessions_dir,
                        path_resolver=self._resolve_session_path,
                    ).replace_model_history(
                        child_id,
                        [],
                        reason="subagent_fork_empty_parent",
                    )
                self._copy_branch_sidecar_files(parent_id, child_id)
            except Exception as exc:
                logger.warning("Runtime V2 reference fork failed for %s: %s", child_id, exc)
            return child_id
        llm_raw = self._load_llm_history(parent_id)
        work_raw = self._load_work_messages(parent_id)
        kc = self._load_key_context(parent_id)
        self._save_llm_history(child_id, copy.deepcopy(llm_raw))
        self._save_work_messages(child_id, copy.deepcopy(work_raw))
        if (kc or "").strip():
            self._save_key_context(child_id, kc)
        return child_id

    def validate_subagent_resume(self, parent_session_id: str, resume_session_id: str) -> Optional[str]:
        """校验 resume 目标是否为当前父会话下的 subagent；成功返回规范化 session_id。"""
        try:
            parent_id = self._normalize_session_id(parent_session_id)
            child_id = self._normalize_session_id(resume_session_id)
        except ValueError:
            return None
        meta = self._load_metadata(child_id)
        if not isinstance(meta, dict) or not meta.get("is_subagent"):
            return None
        if str(meta.get("parent_session_id") or "").strip() != parent_id:
            return None
        if not self._get_session_path(child_id).is_dir():
            return None
        return child_id

    def _resolve_subagent_child_for_delete(self, parent_session_id: str, child_session_id: str) -> Optional[str]:
        child_id = self.validate_subagent_resume(parent_session_id, child_session_id)
        if child_id:
            return child_id
        try:
            parent_id = self._normalize_session_id(parent_session_id)
            child_id = self._normalize_session_id(child_session_id)
            path = self._get_subagent_session_path(parent_id, child_id)
        except ValueError:
            return None
        if path.is_dir():
            return child_id
        return None

    def _remove_subagent_parent_rows(self, parent_session_id: str, child_session_id: str) -> None:
        child_id = str(child_session_id or "").strip()
        if not child_id:
            return
        if self._runtime_v2_primary():
            try:
                self._runtime_subagent_store().remove_parent_rows(parent_session_id, child_id)
            except Exception as exc:
                logger.debug("Runtime V2 remove subagent parent rows failed: %s", exc)
            return
        for path_getter in (self._get_pending_subagent_results_path, self._get_subagent_tasks_path):
            path = path_getter(parent_session_id)
            if not path.is_file():
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    continue
                rows = [
                    row for row in data
                    if isinstance(row, dict)
                    and str(row.get("agent_id") or row.get("task_id") or row.get("id") or "") != child_id
                ]
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(rows, f, indent=2, ensure_ascii=False)
            except Exception:
                continue

    def delete_virtual_subagent_task(self, parent_session_id: str, task_id: str) -> bool:
        """删除没有独立会话目录的虚拟 subagent/task（如 best-of-n 输出卡片）。"""
        parent_id = self._normalize_session_id(parent_session_id)
        tid = str(task_id or "").strip()
        if not tid:
            return False
        tasks = self.list_subagent_tasks(parent_id)
        target = None
        for row in tasks:
            if isinstance(row, dict) and str(row.get("task_id") or row.get("agent_id") or row.get("id") or "") == tid:
                target = row
                break
        if target is None:
            return False
        output_file = str((target or {}).get("output_file") or "").strip()
        self._remove_subagent_parent_rows(parent_id, tid)
        if output_file:
            try:
                p = Path(output_file).expanduser().resolve()
                base = self._get_session_path(parent_id).resolve()
                if p == base or base in p.parents:
                    if p.is_file():
                        p.unlink(missing_ok=True)
                    elif p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass
        logger.info("已删除虚拟 subagent/task %s ← parent=%s", tid, parent_id)
        if self._runtime_v2_primary():
            from runtime_v2 import RuntimeHistoryOps

            RuntimeHistoryOps(
                self.repository.sessions_dir,
                path_resolver=self._resolve_session_path,
            ).delete_subagent(parent_id, tid, virtual=True)
        return True

    def delete_subagent_session(self, parent_session_id: str, child_session_id: str) -> bool:
        """删除父会话下的某个 subagent（含嵌套 descendants）并更新 subagent 索引。"""
        parent_id = self._normalize_session_id(parent_session_id)
        child_id = self._resolve_subagent_child_for_delete(parent_id, child_session_id)
        if not child_id:
            return False
        ids = [child_id, *self.list_subagent_descendants(child_id)]
        paths: Dict[str, Path] = {}
        for sid in ids:
            try:
                paths[sid] = self._get_session_path(sid)
            except Exception:
                pass
        for sid in reversed(ids):
            self._unregister_subagent(sid)
        for sid in reversed(ids):
            p = paths.get(sid)
            if p and p.exists():
                shutil.rmtree(p, ignore_errors=True)
        self._remove_subagent_parent_rows(parent_id, child_id)
        if self._runtime_v2_primary():
            from runtime_v2 import RuntimeHistoryOps

            RuntimeHistoryOps(
                self.repository.sessions_dir,
                path_resolver=self._resolve_session_path,
            ).delete_subagent(
                parent_id,
                child_id,
                descendant_count=max(0, len(ids) - 1),
            )
        logger.info(
            "已删除 subagent %s ← parent=%s（含 descendants=%s）",
            child_id,
            parent_id,
            max(0, len(ids) - 1),
        )
        return True

    def get_or_create_session(
        self,
        session_id: Optional[str] = None,
        *,
        model_profile_id: str = "",
    ) -> Tuple[str, List[dict], List[dict], List[dict], str, dict]:
        """
        获取或创建会话，返回:
        (session_id, dialogue, work_messages, llm_history, key_context, metadata)
        """
        if session_id is not None:
            sid_in = (session_id or "").strip()
            if sid_in:
                try:
                    from session_lifecycle import is_session_deleted

                    if is_session_deleted(sid_in):
                        raise ValueError(f"Session {sid_in} was deleted")
                except ValueError:
                    raise
                except Exception:
                    pass
        if session_id is None:
            create_started = time.perf_counter()
            session_id = str(uuid.uuid4())
            uuid_ready = time.perf_counter()
            work_messages: List[dict] = []
            llm_history = []           # 新会话 llm_history 为空
            key_context = ""
            now_iso = datetime.now().isoformat()
            metadata = {
                "name": "新会话",
                "created_at": now_iso,
                "updated_at": now_iso,
                "archived": False,
                "pinned": False,
                "todo": False,
                "goal_review_pending": False,
                # WORK_DIR is already normalized by _env_path() during startup.
                # Resolving it again on every click adds another filesystem call
                # to a latency-sensitive path, which can have multi-second tail
                # latency on Windows when the volume is busy or being scanned.
                "authorized_dirs": [str(WORK_DIR)],
            }
            requested_profile_id = str(model_profile_id or "").strip()
            if requested_profile_id:
                metadata["model_profile_id"] = requested_profile_id
            dialogue: List[dict] = []  # 与 dialogue_history.json 均由 ui_events 主链写入
            metadata_ready = time.perf_counter()
            # A freshly generated UUID cannot be present in the deleted-session
            # registry and has no concurrent metadata writer. Avoid the generic
            # update path's repeated registry checks and per-session lock lookup.
            self.repository.save_metadata_atomic(session_id, metadata)
            self._set_interrupt_cache_from_metadata(session_id, metadata)
            metadata_saved = time.perf_counter()
            runtime_v2_primary = self._runtime_v2_primary()
            if runtime_v2_primary:
                from runtime_v2 import RuntimeHistoryOps

                RuntimeHistoryOps(
                    self.repository.sessions_dir,
                    path_resolver=self._resolve_session_path,
                )._append_and_snapshot(session_id, "session_meta", dict(metadata))
            else:
                self._save_work_messages(session_id, work_messages)
                self._save_llm_history(session_id, llm_history)
                self._save_key_context(session_id, key_context)
                self._save_ui_events(session_id, [])
                self._save_dialogue_history(session_id, [])
            history_initialized = time.perf_counter()
            index_entry = {
                "id": session_id,
                "name": metadata["name"],
                "created_at": metadata["created_at"],
                "updated_at": metadata.get("updated_at") or metadata["created_at"],
                "archived": bool(metadata.get("archived", False)),
                "pinned": bool(metadata.get("pinned", False)),
                "todo": bool(metadata.get("todo", False)),
                "goal_review_pending": bool(metadata.get("goal_review_pending", False)),
                "pinned_at": metadata.get("pinned_at") if metadata.get("pinned") else None,
            }
            # create_session now runs outside the asyncio event loop. Protect
            # append + persistence as one operation so simultaneous tabs cannot
            # overwrite each other's newly-created index row.
            with self._lock:
                self.index.append(index_entry)
                self.repository.save_index(self.index_file, self.index)
            index_saved = time.perf_counter()
            logger.info(
                "create_session_timing session=%s total=%sms uuid=%sms metadata_prepare=%sms "
                "metadata_write=%sms history_init=%sms index_write=%sms runtime_v2=%s",
                session_id,
                int((index_saved - create_started) * 1000),
                int((uuid_ready - create_started) * 1000),
                int((metadata_ready - uuid_ready) * 1000),
                int((metadata_saved - metadata_ready) * 1000),
                int((history_initialized - metadata_saved) * 1000),
                int((index_saved - history_initialized) * 1000),
                runtime_v2_primary,
            )
            logger.info(f"创建新会话: {session_id}")
            return session_id, dialogue, work_messages, llm_history, key_context, metadata
        else:
            if self._runtime_v2_primary():
                from runtime_v2 import RuntimeModelProjection, RuntimeUiProjection, SnapshotStore

                resolver = self._resolve_session_path
                llm_history = RuntimeModelProjection(
                    self.repository.sessions_dir,
                    path_resolver=resolver,
                ).read_message_dicts(session_id)
                ui_events = RuntimeUiProjection(
                    self.repository.sessions_dir,
                    path_resolver=resolver,
                ).read_ui_events_fast(session_id)
                dialogue = [
                    _message_to_dict(message)
                    for message in rebuild_core_messages_from_ui_events(ui_events)
                ]
                snapshot = SnapshotStore(
                    self.repository.sessions_dir,
                    path_resolver=resolver,
                ).read(session_id)
                context = snapshot.get("context") if isinstance(snapshot, dict) else {}
                summary = context.get("summary") if isinstance(context, dict) else {}
                key_context = str(summary.get("summary") or "") if isinstance(summary, dict) else ""
                metadata = self._load_metadata(session_id)
                return session_id, dialogue, [], llm_history, key_context, metadata
            work_messages = self._load_work_messages(session_id)
            llm_history = self._load_llm_history(session_id)
            dialogue = self.dialogue_dicts_from_ui_events_file(session_id)
            key_context = self._load_key_context(session_id)
            key_context = self.migrate_todo_plan_off_key_context(session_id, key_context)
            metadata = self._load_metadata(session_id)
            return session_id, dialogue, work_messages, llm_history, key_context, metadata

    def update_session(
        self,
        session_id: str,
        work_messages: List[dict],
        llm_history: List[dict],
        key_context: str,
        metadata: dict = None,
        dialogue_history: List[dict] = None,
    ):
        """更新会话；若传入 dialogue_history 则另存为仅主对话的 JSON（应与 ui_events 主链一致）。"""
        self._save_work_messages(session_id, work_messages)
        self._save_llm_history(session_id, llm_history)
        self._save_key_context(session_id, key_context)
        if dialogue_history is not None:
            self._save_dialogue_history(session_id, dialogue_history)
        if metadata:
            self._save_metadata(session_id, metadata)
            now_iso = datetime.now().isoformat()
            for sess in self.index:
                if sess["id"] == session_id:
                    sess["name"] = metadata.get("name", sess.get("name", "新会话"))
                    sess["updated_at"] = now_iso
                    if "archived" in metadata:
                        sess["archived"] = bool(metadata["archived"])
                    if "pinned" in metadata:
                        sess["pinned"] = bool(metadata["pinned"])
                    if "todo" in metadata:
                        sess["todo"] = bool(metadata["todo"])
                    if "goal_review_pending" in metadata:
                        sess["goal_review_pending"] = bool(metadata["goal_review_pending"])
                    if "pinned_at" in metadata:
                        sess["pinned_at"] = metadata.get("pinned_at")
                    elif metadata.get("pinned") is False:
                        sess["pinned_at"] = None
                    break
            self._save_index()

    def update_session_model_state(
        self,
        session_id: str,
        llm_history: List[dict],
        key_context: str,
        metadata: dict = None,
        dialogue_history: List[dict] = None,
    ):
        """更新模型主链状态，不读写旧版 work_messages.json。"""
        self._save_llm_history(session_id, llm_history)
        self._save_key_context(session_id, key_context)
        if dialogue_history is not None:
            self._save_dialogue_history(session_id, dialogue_history)
        if metadata:
            self._save_metadata(session_id, metadata)
            now_iso = datetime.now().isoformat()
            for sess in self.index:
                if sess["id"] == session_id:
                    sess["updated_at"] = now_iso
                    if "archived" in metadata:
                        sess["archived"] = bool(metadata["archived"])
                    if "pinned" in metadata:
                        sess["pinned"] = bool(metadata["pinned"])
                    if "todo" in metadata:
                        sess["todo"] = bool(metadata["todo"])
                    if "goal_review_pending" in metadata:
                        sess["goal_review_pending"] = bool(metadata["goal_review_pending"])
                    if "pinned_at" in metadata:
                        sess["pinned_at"] = metadata.get("pinned_at")
                    elif metadata.get("pinned") is False:
                        sess["pinned_at"] = None
                    break
            self._save_index()

    def delete_session(self, session_id: str):
        sid = self._normalize_session_id(session_id)
        try:
            from session_lifecycle import mark_session_deleted

            mark_session_deleted(sid)
        except Exception:
            pass
        idx = self._load_subagent_index()
        descendants: List[str] = []
        stack = [sid]
        while stack:
            parent = stack.pop()
            children = [cid for cid, pid in idx.items() if pid == parent]
            for cid in children:
                if cid in descendants:
                    continue
                descendants.append(cid)
                stack.append(cid)
        delete_ids = [sid] + descendants
        for delete_id in delete_ids:
            try:
                with self._session_metadata_lock(delete_id):
                    metadata = self.repository.load_metadata(delete_id)
                    if not isinstance(metadata, dict):
                        metadata = {}
                    metadata["deleted"] = True
                    metadata["deleted_at"] = datetime.now().isoformat()
                    self.repository.save_metadata_atomic(delete_id, metadata)
            except Exception:
                logger.debug("persist session deletion tombstone failed: %s", delete_id, exc_info=True)
        try:
            from runtime_v2 import SnapshotStore

            checkpoint_store = SnapshotStore(
                self.sessions_dir,
                path_resolver=self._resolve_session_path,
            )
            for delete_id in delete_ids:
                if not checkpoint_store.cancel_checkpoint(delete_id, timeout_seconds=0.5):
                    logger.warning(
                        "delete session is waiting on a slow Runtime V2 snapshot: %s",
                        delete_id,
                    )
        except Exception:
            logger.debug("cancel Runtime V2 snapshot before session delete failed", exc_info=True)
        for delete_id in reversed(delete_ids):
            try:
                session_path = self._get_session_path(delete_id)
                if session_path.exists():
                    shutil.rmtree(session_path)
            except Exception as exc:
                logger.warning("delete session path failed: %s %s", delete_id, exc)
        idx = {cid: pid for cid, pid in idx.items() if cid not in delete_ids and pid not in delete_ids}
        self._save_subagent_index(idx)
        for delete_id in delete_ids:
            try:
                from session_lifecycle import mark_session_deleted

                mark_session_deleted(delete_id)
            except Exception:
                pass
        self.index = [s for s in self.index if s.get("id") not in set(delete_ids)]
        self._save_index()
        logger.info("已删除会话: %s descendants=%s", sid, len(descendants))

    def last_user_question_preview(self, session_id: str, max_len: int = 180) -> str:
        """最近一条用户提问的单行预览（侧栏）；优先 ui_events，其次 dialogue_history。"""
        sid = (session_id or "").strip()
        if not sid:
            return ""
        events = self._load_ui_events_for_active_runtime(sid)
        for ev in reversed(events):
            if not isinstance(ev, dict) or ev.get("type") != "user":
                continue
            raw = ev.get("content")
            text = raw if isinstance(raw, str) else str(raw or "")
            return _normalize_sidebar_preview_text(text, max_len)
        if self._runtime_v2_primary():
            return ""
        path = self._get_dialogue_history_path(sid)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    for m in reversed(data):
                        if not isinstance(m, dict) or m.get("type") != "user":
                            continue
                        raw = m.get("content", "")
                        text = raw if isinstance(raw, str) else str(raw or "")
                        return _normalize_sidebar_preview_text(text, max_len)
            except Exception:
                pass
        return ""

    def _session_entry_with_activity(self, base: dict) -> dict:
        """合并 index 条目与 ui_events 文件的 activity 时间。"""
        d = dict(base)
        d.setdefault("archived", False)
        d.setdefault("pinned", False)
        d["todo"] = bool(d.get("todo", False))
        d["goal_review_pending"] = bool(d.get("goal_review_pending", False))
        d["unread_result"] = bool(d.get("unread_result", False))
        d["unread_result_status"] = str(d.get("unread_result_status") or "success")
        if d.get("pinned") and not d.get("pinned_at"):
            d["pinned_at"] = d.get("updated_at") or d.get("created_at")
        sid = d.get("id")
        best_ts: Optional[float] = None
        for key in ("updated_at", "created_at"):
            raw = d.get(key)
            if not raw:
                continue
            try:
                iso = str(raw).replace("Z", "+00:00")
                t = datetime.fromisoformat(iso).timestamp()
                best_ts = t if best_ts is None else max(best_ts, t)
            except Exception:
                pass
        if sid and best_ts is None:
            # Normal session mutations update ``updated_at`` in metadata and the
            # in-memory index. Only malformed/legacy rows without any timestamp
            # need a filesystem fallback. This keeps sidebar polling from
            # stat'ing every events file even when nothing changed.
            session_dir = self.sessions_dir / str(sid)
            activity_path = (
                session_dir / "events.jsonl"
                if self._runtime_v2_primary()
                else session_dir / "ui_events.json"
            )
            try:
                mt = activity_path.stat().st_mtime
                best_ts = mt if best_ts is None else max(best_ts, mt)
            except OSError:
                pass
        if best_ts is not None:
            d["last_activity_at"] = datetime.fromtimestamp(best_ts, tz=timezone.utc).isoformat().replace(
                "+00:00", "Z"
            )
        else:
            d["last_activity_at"] = d.get("updated_at") or d.get("created_at")
        if sid:
            preview = str(d.get("last_user_preview") or "").strip()
            if not preview:
                try:
                    preview = self.last_user_question_preview(sid, 180)
                except Exception:
                    preview = ""
            d["last_user_preview"] = preview
        else:
            d["last_user_preview"] = ""
        return d

    @staticmethod
    def _iso_ts(raw: Any) -> float:
        if not raw:
            return 0.0
        try:
            iso = str(raw).replace("Z", "+00:00")
            return datetime.fromisoformat(iso).timestamp()
        except Exception:
            return 0.0

    def _auto_archive_stale_sessions(self) -> None:
        now = time.monotonic()
        with self._auto_archive_check_lock:
            if (
                self._auto_archive_last_check > 0.0
                and now - self._auto_archive_last_check < self.AUTO_ARCHIVE_CHECK_INTERVAL_SEC
            ):
                return
            # Claim the periodic maintenance pass before scanning so concurrent
            # sidebar polls cannot repeat it.
            self._auto_archive_last_check = now
        cutoff = datetime.now(timezone.utc).timestamp() - (self.AUTO_ARCHIVE_AFTER_DAYS * 86400)
        changed = False
        for sess in list(self.index):
            sid = str(sess.get("id") or "").strip()
            if not sid or sess.get("archived") or sess.get("pinned"):
                continue
            row = self._session_entry_with_activity(dict(sess))
            activity_ts = self._iso_ts(row.get("last_activity_at") or row.get("updated_at") or row.get("created_at"))
            if not activity_ts or activity_ts >= cutoff:
                continue
            meta_path = self._get_metadata_path(sid)
            if not meta_path.exists():
                continue
            with self._session_metadata_lock(sid):
                metadata = self._load_metadata_unlocked(sid)
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata["archived"] = True
                metadata["auto_archived"] = True
                metadata["auto_archived_at"] = datetime.now(timezone.utc).isoformat()
                self._save_metadata_unlocked(sid, metadata)
            sess["archived"] = True
            changed = True
        if changed:
            self._save_index()

    def list_sessions(self, include_archived: bool = False) -> List[dict]:
        """返回会话列表；每条含 last_activity_at。置顶在前，其余按最近活动时间倒序。"""
        self._auto_archive_stale_sessions()
        if not include_archived:
            base_rows = [dict(s) for s in self.index if not s.get("archived")]
        else:
            base_rows = [dict(s) for s in self.index]
        rows = [self._session_entry_with_activity(s) for s in base_rows]

        def sort_key(r: dict) -> Tuple[int, float, float]:
            pinned = bool(r.get("pinned"))
            pt = self._iso_ts(r.get("pinned_at"))
            la = r.get("last_activity_at") or r.get("updated_at") or r.get("created_at")
            lt = self._iso_ts(la)
            return (0 if pinned else 1, -pt if pinned else 0.0, -lt)

        rows.sort(key=sort_key)
        return rows

    def archived_session_count(self) -> int:
        """Return the number of archived sessions without materializing session details."""
        self._auto_archive_stale_sessions()
        return sum(1 for s in self.index if s.get("archived"))

    def get_session_summary(self, session_id: str) -> Optional[dict]:
        """单条会话摘要（结构与 list_sessions 元素一致），不存在则 None。"""
        for s in self.index:
            if s.get("id") == session_id:
                return self._session_entry_with_activity(dict(s))
        return None

    def set_session_archived(self, session_id: str, archived: bool) -> None:
        meta_path = self._get_metadata_path(session_id)
        if not meta_path.exists():
            self.refresh_sessions_index_from_disk()
            return
        with self._session_metadata_lock(session_id):
            metadata = self._load_metadata_unlocked(session_id)
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["archived"] = archived
            metadata["updated_at"] = datetime.now().isoformat()
            self._save_metadata_unlocked(session_id, metadata)
        for sess in self.index:
            if sess.get("id") == session_id:
                sess["archived"] = archived
                sess["updated_at"] = metadata["updated_at"]
                break
        else:
            self.refresh_sessions_index_from_disk()
            return
        self._save_index()

    def set_session_pinned(self, session_id: str, pinned: bool) -> None:
        meta_path = self._get_metadata_path(session_id)
        if not meta_path.exists():
            self.refresh_sessions_index_from_disk()
            return
        with self._session_metadata_lock(session_id):
            metadata = self._load_metadata_unlocked(session_id)
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["pinned"] = pinned
            if pinned:
                metadata["pinned_at"] = datetime.now().isoformat()
            else:
                metadata.pop("pinned_at", None)
            metadata["updated_at"] = datetime.now().isoformat()
            self._save_metadata_unlocked(session_id, metadata)
        for sess in self.index:
            if sess.get("id") == session_id:
                sess["pinned"] = pinned
                sess["pinned_at"] = metadata.get("pinned_at") if pinned else None
                sess["updated_at"] = metadata["updated_at"]
                break
        else:
            self.refresh_sessions_index_from_disk()
            return
        self._save_index()

    def set_session_todo(self, session_id: str, todo: bool) -> None:
        meta_path = self._get_metadata_path(session_id)
        if not meta_path.exists():
            self.refresh_sessions_index_from_disk()
            return
        with self._session_metadata_lock(session_id):
            metadata = self._load_metadata_unlocked(session_id)
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["todo"] = bool(todo)
            metadata["updated_at"] = datetime.now().isoformat()
            self._save_metadata_unlocked(session_id, metadata)
        for sess in self.index:
            if sess.get("id") == session_id:
                sess["todo"] = bool(todo)
                sess["updated_at"] = metadata["updated_at"]
                break
        else:
            self.refresh_sessions_index_from_disk()
            return
        self._save_index()

    def set_session_goal_review_pending(self, session_id: str, pending: bool) -> None:
        sid = str(session_id or "").strip()
        if not sid:
            return
        pending = bool(pending)
        target = next((sess for sess in self.index if sess.get("id") == sid), None)
        if target is not None and bool(target.get("goal_review_pending", False)) == pending:
            return
        meta_path = self._get_metadata_path(sid)
        if not meta_path.exists():
            self.refresh_sessions_index_from_disk()
            return
        with self._session_metadata_lock(sid):
            metadata = self._load_metadata_unlocked(sid)
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["goal_review_pending"] = pending
            metadata["updated_at"] = datetime.now().isoformat()
            self._save_metadata_unlocked(sid, metadata)
        for sess in self.index:
            if sess.get("id") == sid:
                sess["goal_review_pending"] = pending
                sess["updated_at"] = metadata["updated_at"]
                break
        else:
            self.refresh_sessions_index_from_disk()
            return
        self._save_index()

    def set_session_name(self, session_id: str, name: str):
        with self._session_metadata_lock(session_id):
            metadata = self._load_metadata_unlocked(session_id)
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["name"] = name
            metadata["updated_at"] = datetime.now().isoformat()
            self._save_metadata_unlocked(session_id, metadata)
        for sess in self.index:
            if sess["id"] == session_id:
                sess["name"] = name
                sess["updated_at"] = metadata["updated_at"]
                break
        self._save_index()

    def mark_session_unread_result(
        self,
        session_id: str,
        status: str = "success",
        run_id: str = "",
    ) -> None:
        sid = self._normalize_session_id(session_id)
        if self._is_deleted_session(sid):
            return
        meta_path = self._get_metadata_path(sid)
        if not meta_path.exists():
            return
        result_status = "failed" if str(status or "").lower() == "failed" else "success"
        result_run_id = str(run_id or "").strip()
        now = datetime.now().isoformat()
        with self._session_metadata_lock(sid):
            metadata = self._load_metadata_unlocked(sid)
            if not isinstance(metadata, dict):
                metadata = {}
            if result_status == "success" and (
                metadata.get("unread_result_status") == "failed"
                or bool(metadata.get("interrupt_requested"))
            ):
                result_status = "failed"
            metadata["unread_result"] = True
            metadata["unread_result_at"] = now
            metadata["unread_result_status"] = result_status
            if result_run_id:
                metadata["unread_result_run_id"] = result_run_id
            else:
                metadata.pop("unread_result_run_id", None)
            self._save_metadata_unlocked(sid, metadata)
        changed = False
        with self._lock:
            for sess in self.index:
                if sess.get("id") == sid:
                    if result_status == "success" and sess.get("unread_result_status") == "failed":
                        result_status = "failed"
                    sess["unread_result"] = True
                    sess["unread_result_at"] = now
                    sess["unread_result_status"] = result_status
                    if result_run_id:
                        sess["unread_result_run_id"] = result_run_id
                    else:
                        sess.pop("unread_result_run_id", None)
                    changed = True
                    break
        if changed:
            self._save_index()

    def clear_session_unread_result(
        self,
        session_id: str,
        expected_run_id: str = "",
    ) -> bool:
        sid = self._normalize_session_id(session_id)
        if self._is_deleted_session(sid):
            return False
        meta_path = self._get_metadata_path(sid)
        if not meta_path.exists():
            return False
        expected = str(expected_run_id or "").strip()
        with self._session_metadata_lock(sid):
            metadata = self._load_metadata_unlocked(sid)
            if not isinstance(metadata, dict):
                metadata = {}
            current_run_id = str(metadata.get("unread_result_run_id") or "").strip()
            if expected and current_run_id and current_run_id != expected:
                return False
            metadata["unread_result"] = False
            metadata.pop("unread_result_at", None)
            metadata.pop("unread_result_status", None)
            metadata.pop("unread_result_run_id", None)
            self._save_metadata_unlocked(sid, metadata)
        changed = False
        with self._lock:
            for sess in self.index:
                if sess.get("id") == sid:
                    current_run_id = str(sess.get("unread_result_run_id") or "").strip()
                    if expected and current_run_id and current_run_id != expected:
                        break
                    sess["unread_result"] = False
                    sess.pop("unread_result_at", None)
                    sess.pop("unread_result_status", None)
                    sess.pop("unread_result_run_id", None)
                    changed = True
                    break
        if changed:
            self._save_index()
        return True

    def request_interrupt(self, session_id: str, run_id: str = "", reason: str = "user"):
        """请求中断指定会话当前执行。"""
        sid = (session_id or "").strip()
        rid = str(run_id or "").strip()
        interrupt_reason = str(reason or "user").strip() or "user"
        if not sid:
            return
        if self._is_deleted_session(sid):
            return
        with self._session_metadata_lock(sid):
            metadata = self._load_metadata_unlocked(sid)
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["interrupt_requested"] = True
            metadata["interrupt_reason"] = interrupt_reason
            if rid:
                metadata["interrupt_run_id"] = rid
            elif metadata.get("active_run_id"):
                metadata["interrupt_run_id"] = str(metadata.get("active_run_id") or "")
            self._save_metadata_unlocked(sid, metadata)
            # ReAct runs execute on a worker event loop and poll this cache.
            # Publishing the disk update without updating the cache can leave
            # the worker observing a stale False value indefinitely.
            self._set_interrupt_cache_from_metadata(sid, metadata)

    def clear_interrupt(self, session_id: str, run_id: str = ""):
        """清除会话中断标记（新任务启动前调用）。始终写回 False，避免残留 true。"""
        sid = (session_id or "").strip()
        if not sid:
            return
        rid = str(run_id or "").strip()
        with self._session_metadata_lock(sid):
            metadata = self._load_metadata_unlocked(sid)
            if not metadata:
                return
            if rid:
                metadata["active_run_id"] = rid
                interrupt_run_id = str(metadata.get("interrupt_run_id") or "").strip()
                if bool(metadata.get("interrupt_requested")) and interrupt_run_id == rid:
                    self._save_metadata_unlocked(sid, metadata)
                    self._set_interrupt_cache_from_metadata(sid, metadata)
                    return
            metadata["interrupt_requested"] = False
            metadata.pop("interrupt_run_id", None)
            metadata.pop("interrupt_reason", None)
            self._save_metadata_unlocked(sid, metadata)
            self._set_interrupt_cache_from_metadata(sid, metadata)

    def is_interrupt_requested(self, session_id: str, run_id: str = "") -> bool:
        """判断会话是否被请求中断。"""
        sid = (session_id or "").strip()
        if not sid:
            return False
        try:
            from session_lifecycle import is_session_deleted

            if is_session_deleted(sid):
                return True
        except Exception:
            pass
        cached = self._get_interrupt_cache(sid)
        if cached is not None:
            requested, interrupt_run_id, _reason = cached
            if not requested:
                return False
            rid = str(run_id or "").strip()
            return True if not rid else (not interrupt_run_id or interrupt_run_id == rid)
        metadata = self._load_metadata(session_id)
        self._set_interrupt_cache_from_metadata(sid, metadata)
        if not bool(metadata.get("interrupt_requested", False)):
            return False
        rid = str(run_id or "").strip()
        if not rid:
            return True
        interrupt_run_id = str(metadata.get("interrupt_run_id") or "").strip()
        return not interrupt_run_id or interrupt_run_id == rid

    def get_interrupt_reason(self, session_id: str) -> str:
        sid = (session_id or "").strip()
        if not sid:
            return ""
        cached = self._get_interrupt_cache(sid)
        if cached is not None:
            requested, _run_id, reason = cached
            if not requested:
                return ""
            return reason or "user"
        metadata = self._load_metadata(sid)
        self._set_interrupt_cache_from_metadata(sid, metadata)
        if not bool(metadata.get("interrupt_requested", False)):
            return ""
        return str(metadata.get("interrupt_reason") or "user").strip() or "user"


session_manager = SessionManager(SESSIONS_DIR, INDEX_FILE)

_executor_override_cache: Dict[str, Tuple[Any, str]] = {}
_executor_transport_cache: Dict[str, Any] = {}
_executor_config_cache: Dict[str, Tuple[float, Tuple[Any, str, int, int]]] = {}
_executor_profile_catalog_cache: Optional[Tuple[float, Dict[str, dict], List[str], str]] = None
_executor_config_generation = 0
_executor_config_cache_lock = threading.Lock()
_executor_failure_lock = threading.RLock()
_executor_failed_candidates_by_scope: Dict[
    str, Dict[str, CandidateFailureSnapshot]
] = {}
_EXECUTOR_CONFIG_CACHE_TTL_SEC = 10.0


def _invalidate_executor_config_cache(session_id: str = "") -> None:
    global _executor_profile_catalog_cache, _executor_config_generation
    sid = str(session_id or "").strip()
    with _executor_config_cache_lock:
        if sid:
            _executor_config_cache.pop(sid, None)
        else:
            _executor_config_cache.clear()
            _executor_profile_catalog_cache = None
        _executor_config_generation += 1


def executor_config_generation() -> int:
    with _executor_config_cache_lock:
        return int(_executor_config_generation)


def _executor_profile_catalog(now: Optional[float] = None) -> Tuple[Dict[str, dict], List[str], str]:
    global _executor_profile_catalog_cache
    ts = time.monotonic() if now is None else float(now)
    with _executor_config_cache_lock:
        cached = _executor_profile_catalog_cache
        if cached and ts - cached[0] <= _EXECUTOR_CONFIG_CACHE_TTL_SEC:
            return cached[1], list(cached[2]), cached[3]
    ordered_profiles = [
        p for p in model_profiles.sorted_profiles(PROJECT_ROOT)
        if model_profiles.is_usable_profile(p)
    ]
    profiles = {str(p.get("id") or ""): p for p in ordered_profiles}
    ordered_ids = [str(p.get("id") or "") for p in ordered_profiles]
    first_id = ordered_ids[0] if ordered_ids else ""
    top_profile_id = first_id
    with _executor_config_cache_lock:
        _executor_profile_catalog_cache = (ts, profiles, list(ordered_ids), top_profile_id)
    return profiles, list(ordered_ids), top_profile_id


def list_executor_model_profile_choices() -> List[Dict[str, Any]]:
    """Return safe, ordered model-profile choices for the task tool schema."""
    profiles, ordered_ids, _top_profile_id = _executor_profile_catalog()
    choices: List[Dict[str, Any]] = []
    for profile_id in ordered_ids:
        pid = str(profile_id or "").strip()
        if not pid:
            continue
        profile = profiles.get(pid)
        if not isinstance(profile, dict):
            continue
        choice = {
            "id": pid,
            "name": str(profile.get("name") or profile.get("model") or pid),
            "model": str(profile.get("model") or ""),
            "llm_type": canonical_llm_type(resolve_profile_provider(profile)),
            "context_window": int(profile.get("context_window") or CONTEXT_WINDOW),
            "max_output_tokens": int(profile.get("max_output_tokens") or MAX_OUTPUT_TOKENS),
        }
        choice.update(model_profiles.infer_model_task_capabilities(
            choice["model"], choice["name"], choice["context_window"]
        ))
        custom_description = str(
            profile.get("capability_description") or ""
        ).strip()
        if custom_description:
            choice["capability_description"] = custom_description
            choice["capability_description_en"] = custom_description
            choice["capability_source"] = "manual"
        choice["table_input_modalities"] = list(choice.get("input_modalities") or [])
        choice["input_modalities"] = model_profiles.profile_input_modalities(profile)
        choice["multimodal_input"] = model_profiles.profile_multimodal_input(profile)
        choice["multimodal_mode"] = model_profiles.normalize_multimodal_mode(
            profile.get("multimodal_mode")
        )
        tags = list(choice.get("capability_tags") or [])
        if choice["multimodal_input"]:
            if "multimodal" not in tags:
                tags.append("multimodal")
        else:
            tags = [tag for tag in tags if tag != "multimodal_candidate"]
        choice["capability_tags"] = tags
        choices.append(choice)
    return choices


def inherited_executor_selection(session_id: str) -> Dict[str, str]:
    """Snapshot the parent's effective model selection for a new subagent."""
    try:
        meta = session_manager._load_metadata((session_id or "").strip())
    except Exception:
        meta = {}
    if isinstance(meta, dict):
        profile_id = str(meta.get("model_profile_id") or "").strip()
        if profile_id:
            return {"model_profile_id": profile_id}
    choices = list_executor_model_profile_choices()
    profile_id = str((choices[0] if choices else {}).get("id") or "").strip()
    return {"model_profile_id": profile_id} if profile_id else {}


def executor_runtime_snapshot_for_session(session_id: str) -> Dict[str, Any]:
    """Return a secret-free immutable request/profile snapshot for a fork."""
    candidates = resolve_executor_candidates_for_session(session_id)
    if not candidates:
        return {}
    first = candidates[0]
    return {
        "profile_id": str(first.get("profile_id") or ""),
        "model": str(first.get("model") or ""),
        "max_output_tokens": int(first.get("max_output_tokens") or MAX_OUTPUT_TOKENS),
        "context_window": int(first.get("context_window") or CONTEXT_WINDOW),
        "temperature": float(first.get("temperature", EXECUTOR_TEMPERATURE)),
        "extra_body": dict(first.get("extra_body") or {}),
        "reasoning_effort": first.get("reasoning_effort"),
        "multimodal_input": bool(first.get("multimodal_input")),
        "input_modalities": list(first.get("input_modalities") or ["text"]),
    }


def _record_profile_multimodal_failure(
    profile_id: str,
    _error: Optional[BaseException] = None,
) -> None:
    updated = model_profiles.mark_profile_multimodal_failed(PROJECT_ROOT, profile_id)
    if updated is None:
        return
    _invalidate_executor_config_cache()


def _record_profile_modalities_failure(
    profile_id: str,
    modalities: List[str],
    _error: Optional[BaseException] = None,
) -> None:
    updated = model_profiles.mark_profile_modalities_failed(
        PROJECT_ROOT,
        profile_id,
        modalities,
        reason="provider_rejected_media_input",
    )
    if updated is not None:
        _invalidate_executor_config_cache()


def _profile_candidate(profile: dict) -> Dict[str, Any]:
    cache_key = "profile:" + model_profiles.profile_cache_key(profile)
    cached = _executor_override_cache.get(cache_key)
    if cached is None:
        cached = create_openai_client_for_profile(
            profile,
            f"profile:{str(profile.get('name') or profile.get('id') or '')[:32]}",
            http_client=executor_http_client,
        )
        _executor_override_cache[cache_key] = cached
    extra_body = _profile_extra_body(profile)
    profile_id = str(profile.get("id") or "")
    input_modalities = model_profiles.profile_input_modalities(profile)
    multimodal_input = model_profiles.profile_multimodal_input(profile)
    provider = resolve_profile_provider(profile)
    transport = _executor_transport_cache.get(cache_key)
    if transport is None:
        transport = build_transport(
            profile,
            openai_client=cached[0],
            http_client=executor_http_client,
        )
        _executor_transport_cache[cache_key] = transport
    mark_multimodal_failed = (
        lambda error, pid=profile_id: _record_profile_multimodal_failure(pid, error)
    )
    mark_modalities_failed = (
        lambda modalities, error, pid=profile_id: _record_profile_modalities_failure(
            pid, modalities, error
        )
    )
    try:
        setattr(cached[0], "_myagent_input_modalities", input_modalities)
        setattr(cached[0], "_myagent_multimodal_input", multimodal_input)
        setattr(
            cached[0],
            "_myagent_mark_multimodal_failed",
            mark_multimodal_failed,
        )
        setattr(
            cached[0],
            "_myagent_mark_modalities_failed",
            mark_modalities_failed,
        )
    except Exception:
        logger.debug("无法向模型客户端附加多模态能力元数据", exc_info=True)
    return {
        "profile_id": profile_id,
        "client": cached[0],
        "transport": transport,
        "provider": provider.value,
        "model": cached[1],
        "max_output_tokens": int(profile.get("max_output_tokens") or MAX_OUTPUT_TOKENS),
        "context_window": int(profile.get("context_window") or CONTEXT_WINDOW),
        "temperature": _profile_temperature(profile),
        "extra_body": extra_body,
        "reasoning_effort": _profile_reasoning_effort(profile, extra_body),
        "thinking_format": _profile_thinking_format(profile),
        "multimodal_input": multimodal_input,
        "input_modalities": input_modalities,
        "mark_multimodal_failed": mark_multimodal_failed,
        "mark_modalities_failed": mark_modalities_failed,
    }


def resolve_executor_candidates_for_session(
    session_id: str,
    *,
    profile_id_override: Optional[str] = None,
    runtime_snapshot_override: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    sid = (session_id or "").strip()
    profile_id = str(profile_id_override or "").strip()
    runtime_snapshot: Dict[str, Any] = (
        dict(runtime_snapshot_override)
        if isinstance(runtime_snapshot_override, dict)
        else {}
    )
    if sid and profile_id_override is None:
        try:
            meta = session_manager._load_metadata(sid)
        except Exception:
            meta = {}
        if isinstance(meta, dict):
            if profile_id_override is None:
                profile_id = str(meta.get("model_profile_id") or "").strip()
            raw_snapshot = meta.get("fork_model_runtime")
            if not runtime_snapshot and isinstance(raw_snapshot, dict):
                runtime_snapshot = dict(raw_snapshot)
    profiles, ordered_ids, _top_profile_id = _executor_profile_catalog()
    candidates: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def add_candidate(pid: str) -> None:
        pid = str(pid or "").strip()
        if not pid or pid in seen:
            return
        profile = profiles.get(pid)
        if profile:
            profile = model_profiles.profile_with_session_request_headers(profile, sid)
            candidates.append(_profile_candidate(profile))
            seen.add(pid)

    if profile_id:
        add_candidate(profile_id)
    # fallback 只在同协议模型之间切换（chat↔chat、responses↔responses、
    # anthropic↔anthropic）：跨类型切换意味着 wire 协议、思考字段与
    # tool_call_id 形态全部改变，历史回放极易出错。以第一候选（会话
    # 绑定的模型）的解析 provider 为基准过滤其余候选。
    primary_provider = None
    if candidates:
        try:
            primary_provider = resolve_profile_provider(
                profiles.get(str(candidates[0].get("profile_id") or "")) or {}
            )
        except Exception:
            primary_provider = None
    for pid in ordered_ids:
        if primary_provider is not None:
            profile = profiles.get(pid)
            try:
                if resolve_profile_provider(profile or {}) != primary_provider:
                    continue
            except Exception:
                continue
        add_candidate(pid)
    if candidates and runtime_snapshot:
        frozen = dict(candidates[0])
        for key in (
            "model",
            "max_output_tokens",
            "context_window",
            "temperature",
            "extra_body",
            "reasoning_effort",
            "multimodal_input",
            "input_modalities",
        ):
            if key in runtime_snapshot:
                frozen[key] = runtime_snapshot[key]
        candidates[0] = frozen
    return candidates


def resolve_executor_for_session(session_id: str) -> Tuple[Any, str]:
    """子会话可经 metadata.executor_model 覆盖默认 executor 模型。"""
    client, model, _max_out, _ctx = resolve_executor_config_for_session(session_id)
    return client, model


def resolve_executor_config_for_session(session_id: str) -> Tuple[Any, str, int, int]:
    """Resolve executor client/model plus per-session model limits."""
    sid = (session_id or "").strip()
    now = time.monotonic()
    if sid:
        with _executor_config_cache_lock:
            cached_config = _executor_config_cache.get(sid)
            if cached_config and now - cached_config[0] <= _EXECUTOR_CONFIG_CACHE_TTL_SEC:
                return cached_config[1]
    try:
        meta = session_manager._load_metadata(sid)
    except Exception:
        meta = {}
    profile_id = ""
    if isinstance(meta, dict):
        profile_id = str(meta.get("model_profile_id") or "").strip()
    candidates = resolve_executor_candidates_for_session(
        sid,
        profile_id_override=profile_id,
    )
    runtime_snapshot = (
        meta.get("fork_model_runtime")
        if isinstance(meta, dict)
        and isinstance(meta.get("fork_model_runtime"), dict)
        else None
    )
    if candidates and runtime_snapshot:
        frozen = dict(candidates[0])
        for key in (
            "model",
            "max_output_tokens",
            "context_window",
            "temperature",
            "extra_body",
            "reasoning_effort",
            "multimodal_input",
            "input_modalities",
        ):
            if key in runtime_snapshot:
                frozen[key] = runtime_snapshot[key]
        candidates[0] = frozen
    if not candidates:
        raise RuntimeError("no usable model profile configured")
    first = candidates[0]
    # Always keep the provider-neutral facade at the executor boundary.  This
    # makes a single-profile session use the same protocol selection as model
    # fallback and the Goal judge.
    client = (
        FallbackOpenAIClient(
            candidates,
            failure_lock=_executor_failure_lock,
            failed_candidates_by_scope=_executor_failed_candidates_by_scope,
        )
        if profile_id or len(candidates) > 1 or first.get("transport") is not None
        else first.get("client")
    )
    result = (
        client,
        str(first.get("model") or ""),
        int(first.get("max_output_tokens") or MAX_OUTPUT_TOKENS),
        int(first.get("context_window") or CONTEXT_WINDOW),
    )
    if sid:
        with _executor_config_cache_lock:
            _executor_config_cache[sid] = (now, result)
    return result

# ==================== Todo 计划（todo_plan.md）与 key_context 兼容 ====================
_TODO_SECTION_LINE_RE = re.compile(r"^## Todo 计划\s*$", re.MULTILINE)


def _todo_section_looks_like_real_plan(text: str) -> bool:
    """
    仅当独立一行的「## Todo 计划」标题且正文含 checkbox 任务行时，视为真实 Todo 小节。
    避免把摘要/文档里的「## Todo 计划 (update_todo…)」误拆到 todo_plan.md。
    """
    s = text or ""
    m = _TODO_SECTION_LINE_RE.search(s)
    if not m:
        return False
    rest = s[m.end() :].lstrip()
    return bool(re.search(r"^\s*(\[ \]|\[>\]|\[x\])\s*#", rest, re.MULTILINE))


def _repair_mis_split_todo_plan(session_id: str, key_context: str) -> str:
    """若 todo_plan.md 为误拆的摘要残段，合并回 key_context 并清空 todo_plan。"""
    sid = (session_id or "").strip()
    if not sid:
        return key_context or ""
    tp = session_manager.load_todo_plan(sid)
    if not (tp or "").strip():
        return key_context or ""
    if _todo_section_looks_like_real_plan(tp):
        return key_context or ""
    kc = (key_context or "").strip()
    merged = (kc + "\n" + tp.strip()).strip() if kc else tp.strip()
    try:
        session_manager.save_key_context(sid, merged)
        session_manager.save_todo_plan(sid, "")
        logger.info("已修复误拆分的 todo_plan.md，内容已合并回 key_context: %s", sid)
    except Exception as e:
        logger.warning("repair_mis_split_todo_plan 失败: %s", e)
    return merged


def _strip_todo_plan_from_key_context(kc: str) -> str:
    """从 key_context 中移除独立成行的 `## Todo 计划` 小节（至下一 `## 标题` 或文末）。"""
    s = (kc or "").strip()
    m = _TODO_SECTION_LINE_RE.search(s)
    if not m:
        return s
    start = m.start()
    rest = s[m.end() :]
    mnext = re.search(r"\n(## [^#])", rest)
    after = rest[mnext.start() :].lstrip() if mnext else ""
    before = s[:start].rstrip()
    if before and after:
        return f"{before}\n\n{after}".strip()
    return (before or after or "").strip()


def _extract_todo_plan_section_raw(kc: str) -> str:
    """从全文截取独立成行的「## Todo 计划」小节（含标题），至下一同级 ## 或文末。"""
    s = kc or ""
    m = _TODO_SECTION_LINE_RE.search(s)
    if not m:
        return ""
    start = m.start()
    tail = s[start:]
    rest = tail[len(TODO_SECTION_HEADER) :]
    mnext = re.search(r"\n(## [^#])", rest)
    if mnext:
        return tail[: len(TODO_SECTION_HEADER) + mnext.start()].strip()
    return tail.strip()


_KEY_COMPRESS_H2 = re.compile(r"^## 上下文(?:压缩|摘要)[^\n]*$", re.MULTILINE)
_NEXT_SAME_TIER_H2 = re.compile(r"\n(## [^#])")


def strip_compress_summary_h2_sections(text: str) -> str:
    """删除全部「## 上下文摘要 / ## 上下文压缩」小节（至下一 ## 或文末），保留其余 Markdown。"""
    s = (text or "").strip()
    while True:
        m = _KEY_COMPRESS_H2.search(s)
        if not m:
            break
        start = m.start()
        rest = s[m.end() :]
        mnext = _NEXT_SAME_TIER_H2.search(rest)
        if mnext:
            s = (s[:start] + rest[mnext.start() :]).strip()
        else:
            s = s[:start].strip()
            break
        s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def merge_compress_summary_into_key_context(existing: str, summary_body: str) -> str:
    """维护当前可注入上下文：压缩结果覆盖为最新一版；旧版由 key_context_history.md 保存。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    block = f"\n\n## 上下文摘要 · {ts}\n\n{(summary_body or '').strip()}\n"
    return f"# 会话关键信息（路径 / 前提 / 要求 / 结论 等由压缩流程维护）{block}"


def key_context_body_for_system_prompt(stored: str) -> str:
    """
    供主模型注入的 key_context 视图：**不含** Todo（Todo 独立 todo_plan.md）。
    兼容旧文件：若仍存在独立成行的真实 ## Todo 计划，先剔除。
    注入 **全文**（摘要与其它小节一并上送；摘要仅保留磁盘上一份）。
    """
    s0 = _strip_todo_plan_from_key_context((stored or "").strip())
    if not s0:
        return ""
    return re.sub(r"\n{3,}", "\n\n", s0).strip()


def _parse_todo_block_from_key_context(kc: str) -> List[Dict[str, Any]]:
    """从已持久化的 key_context 中解析 `## Todo 计划` 段，供会话恢复。"""
    if not kc or TODO_SECTION_HEADER not in kc:
        return []
    i = kc.find(TODO_SECTION_HEADER) + len(TODO_SECTION_HEADER)
    rest = kc[i:].lstrip()
    m = re.search(r"\n(## [^#])", rest)
    block = rest[: m.start()] if m else rest
    items: List[Dict[str, Any]] = []
    for raw in block.splitlines():
        ln = raw.strip()
        if not ln or re.match(r"^\(\d+/\d+", ln) or "已完成" in ln:
            continue
        m0 = re.match(r"^(\[ \]|\[>\]|\[x\]) #(\S+): (.+)$", ln)
        if not m0:
            continue
        br, iid, text = m0.groups()
        st = "completed" if br == "[x]" else "in_progress" if br == "[>]" else "pending"
        items.append({"id": iid, "text": text.strip(), "status": st})
    return items


# ==================== TodoManager 类（按 session_id 隔离；计划写入 todo_plan.md）====================
class TodoManager:
    def __init__(self):
        # 兼容：旧版全局 .items
        self.items: List[Dict] = []
        self._by_session: Dict[str, List[Dict]] = {}

    def _runtime_v2_primary(self) -> bool:
        try:
            from runtime_v2 import runtime_v2_primary

            return runtime_v2_primary()
        except Exception:
            return False

    def _load_runtime_v2_items(self, session_id: str) -> List[Dict]:
        try:
            from runtime_v2 import SnapshotStore
            from session_todo_extension import read_todo_extension

            extension = read_todo_extension(session_manager, session_id)

            # read_consistent_view avoids the full deep copy of the published
            # projection; this read happens on every ReAct round via
            # has_active_plan and only reads the todo fields.
            snapshot = SnapshotStore(
                session_manager.sessions_dir,
                path_resolver=getattr(session_manager, "_resolve_session_path", None),
            ).read_consistent_view(session_id)
            todo = extension if isinstance(extension, dict) else (
                snapshot.get("todo") if isinstance(snapshot, dict) else {}
            )
            if not isinstance(todo, dict):
                todo = {}
            items = todo.get("items")
            if not isinstance(items, list):
                ctx = snapshot.get("context") if isinstance(snapshot, dict) else {}
                ctodo = ctx.get("todo") if isinstance(ctx, dict) else {}
                items = ctodo.get("items") if isinstance(ctodo, dict) else []
            out: List[Dict] = []
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").strip()
                status = str(item.get("status") or "pending").strip().lower()
                if not text or status not in ("pending", "in_progress", "completed"):
                    continue
                out.append(
                    {
                        "id": str(item.get("id") or len(out) + 1),
                        "text": text,
                        "status": status,
                    }
                )
            return out
        except Exception as exc:
            logger.debug("Runtime V2 todo snapshot read failed for %s: %s", session_id, exc)
            return []

    def sync_session_from_key_context(self, session_id: str, key_context: str = "") -> None:
        """从 todo_plan.md 恢复该会话的待办列表到内存（key_context 参数保留兼容，忽略）。"""
        if not session_id:
            return
        if self._runtime_v2_primary():
            self._by_session[session_id] = self._load_runtime_v2_items(session_id)
            return
        raw = session_manager.load_todo_plan(session_id)
        if not (raw or "").strip():
            self._by_session[session_id] = []
            return
        if TODO_SECTION_HEADER not in raw:
            raw = f"{TODO_SECTION_HEADER}\n\n{raw.strip()}\n"
        self._by_session[session_id] = _parse_todo_block_from_key_context(raw)

    def update_for_session(self, session_id: str, items: List[Dict]) -> str:
        if not session_id:
            return self._apply_items("__global__", items)
        return self._apply_items(session_id, items)

    def _apply_items(self, session_id: str, items: List[Dict]) -> str:
        if items is None:
            return "命令格式错误：缺少必填参数 items，请传入待办条目数组。"
        if not items:
            if session_id == "__global__":
                self.items = []
            else:
                self._by_session[session_id] = []
                if not self._runtime_v2_primary():
                    try:
                        session_manager.save_todo_plan(session_id, "")
                    except Exception as _se:
                        logger.warning("save_todo_plan 失败: %s", _se)
            return "当前没有待办事项。"
        if len(items) > TODO_MAX_ITEMS:
            raise ValueError(f"最多支持 {TODO_MAX_ITEMS} 个待办事项")
        validated: List[Dict] = []
        for i, item in enumerate(items):
            text = str(item.get("text", "")).strip()
            status = str(item.get("status", "pending")).lower()
            item_id = str(item.get("id", str(i + 1)))
            if not text:
                raise ValueError(f"条目 {item_id}: 缺少 text")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"条目 {item_id}: 无效的状态 '{status}'")
            validated.append({"id": item_id, "text": text, "status": status})
        if validated and all(t["status"] == "completed" for t in validated):
            # 计划全部完成：清空该会话待办
            if session_id not in ("__global__",):
                self._by_session[session_id] = []
                if not self._runtime_v2_primary():
                    try:
                        session_manager.save_todo_plan(session_id, "")
                    except Exception as _se:
                        logger.warning("save_todo_plan 失败: %s", _se)
            if session_id == "__global__":
                self.items = []
            return "当前没有待办事项。"

        if session_id == "__global__":
            self.items = validated
        else:
            self._by_session[session_id] = validated
            if not self._runtime_v2_primary():
                try:
                    session_manager.save_todo_plan(session_id, self.render_for_session(session_id))
                except Exception as _se:
                    logger.warning("save_todo_plan 失败: %s", _se)
        return self.render_for_session(session_id if session_id != "__global__" else "")

    def render(self) -> str:
        if not self.items:
            return "当前没有待办事项。"
        return self._render_list(self.items)

    def render_for_session(self, session_id: str) -> str:
        if not session_id:
            return self.render()
        items = self._by_session.get(session_id, [])
        return self._render_list(items) if items else "当前没有待办事项。"

    def _render_list(self, items: List[Dict]) -> str:
        if not items:
            return "当前没有待办事项。"
        lines = []
        for item in items:
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}[item["status"]]
            lines.append(f"{marker} #{item['id']}: {item['text']}")
        done = sum(1 for t in items if t["status"] == "completed")
        lines.append(f"\n({done}/{len(items)} 已完成)")
        return "\n".join(lines)

    def has_active_plan(self, session_id: str) -> bool:
        if not session_id:
            return False
        if self._runtime_v2_primary():
            self._by_session[session_id] = self._load_runtime_v2_items(session_id)
        items = self._by_session.get(session_id, [])
        if not items:
            return False
        return not all(t["status"] == "completed" for t in items)

todo_manager = TodoManager()
session_plan_store = todo_manager

# ==================== 压缩辅助函数 ====================
def estimate_tokens(messages: List) -> int:
    """
    整段对话（llm_history 等）消息列表 token 数：DeepSeek V3 词表或回退近似（见 agent_tokenizer）。
    与 react_node / 上下文压缩使用的「整条消息列表」估算同口径（含 tool_calls、reasoning_content 等）。
    """
    return count_message_tokens(messages)


def _is_session_marker_system(m: Any) -> bool:
    return isinstance(m, SystemMessage) and _session_loop_marker_content(
        str(m.content or "")
    )


def is_conversation_compacted_boundary_system(m: Any) -> bool:
    if not isinstance(m, SystemMessage):
        return False
    return (m.content or "").strip() == COMPACT_BOUNDARY_SYSTEM_EXACT


def is_conversation_truncated_boundary_system(m: Any) -> bool:
    if not isinstance(m, SystemMessage):
        return False
    return (m.content or "").strip() == COMPACT_TRUNCATED_BOUNDARY_SYSTEM_EXACT


def is_conversation_compress_boundary_system(m: Any) -> bool:
    """摘要压缩或截尾兜底产生的边界 system（均原样上送主模型）。"""
    return is_conversation_compacted_boundary_system(m) or is_conversation_truncated_boundary_system(m)



def _message_content_text(m: Any) -> str:
    """Return the plain-text projection of a message content.

    Content may be a str or a multimodal list ([{"type": "text", ...},
    {"type": "local_file", ...}, ...]); text parts are concatenated and
    non-text parts are ignored so string helpers never crash on lists.
    """
    content = getattr(m, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif item.get("type") in ("input_text", "output_text"):
                    parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return str(content or "")


def is_compress_recap_user_message(m: Any) -> bool:

    """压缩产生的前情提要 user，不参与主对话派生。"""
    if not isinstance(m, UserMessage):
        return False
    c = _message_content_text(m)
    return c.lstrip().startswith(COMPACT_RECAP_USER_PREFIX)


def is_micro_shrink_user_message(m: Any) -> bool:
    """微压 legacy user，与 `[压缩摘要]` 同属「不计入会话用户轮」的辅助 user。"""
    if not isinstance(m, UserMessage):
        return False
    md = getattr(m, "metadata", None) or {}
    return bool(md.get("micro_shrink"))


def is_compress_summary_system_message(m: Any) -> bool:
    """旧版：整段摘要为 system；不参与主对话派生。新版为 boundary system + `[压缩摘要]` user。"""
    if not isinstance(m, SystemMessage):
        return False
    return (m.content or "").strip().startswith("【历史上下文已压缩/摘要区】")


def is_ephemeral_system_stripped_by_compress(m: Any) -> bool:
    """
    单轨压缩产出中应**去掉**的 system：ReAct/校验/待办 等 agent 侧提醒、通知、占位。
    为 True 时从压缩后的 merged 中剔除；**不为真**的含：loop 标记、旧版「【历史上下文已压缩/摘要区】」、
    以及边界行 `Conversation compacted`。
    """
    if not isinstance(m, SystemMessage):
        return False
    if is_compress_summary_system_message(m):
        return False
    if is_conversation_compress_boundary_system(m):
        return False
    if _is_session_marker_system(m):
        return False
    return True


def is_assistant_message_micro_shrunk(m: Any) -> bool:
    """
    上下文压缩中「微压区」产出的消息（对主对话展示不友好；dialogue 派生时应避开）。
    新数据用 metadata.micro_shrink；旧落盘可凭正文前缀【微压工作块】判断。
    """
    if not isinstance(m, AssistantMessage):
        return False
    md = getattr(m, "metadata", None) or {}
    if md.get("micro_shrink"):
        return True
    c = _message_content_text(m).lstrip()
    return c.startswith("【微压工作块】")


def derive_dialogue_from_assistant_history(llm_history: List) -> List:
    """
    由完整 llm_history 派生「主对话」：每用户段**仅**在存在对用户的终稿时附带助手一条。

    只认 metadata.is_final 的 AssistantMessage 作为该轮对用户的回答；**微压区**的终稿不写入主对话
    （仍保留在 llm_history 中供 ReAct/工具序）。没有 is_final 或仅有微压终稿则本段只含用户句。

    仅含 UserMessage / AssistantMessage；用于运行中 state「与模型一致」的主链。落盘的 dialogue_history.json
    由 ui_events 派生，以免上下文压缩折叠后丢失用户可见全文。
    """
    body: List = []
    for m in llm_history:
        if (
            _is_session_marker_system(m)
            or is_compress_summary_system_message(m)
            or is_conversation_compacted_boundary_system(m)
            or is_conversation_truncated_boundary_system(m)
            or is_compress_recap_user_message(m)
            or is_micro_shrink_user_message(m)
        ):
            continue
        body.append(m)
    out: List = []
    i = 0
    n = len(body)
    while i < n:
        m = body[i]
        if not isinstance(m, UserMessage):
            i += 1
            continue
        h = m
        i += 1
        seg: List = []
        while i < n and not isinstance(body[i], UserMessage):
            seg.append(body[i])
            i += 1
        finals_all = [
            x
            for x in seg
            if isinstance(x, AssistantMessage) and (getattr(x, "metadata", None) or {}).get("is_final")
        ]
        finals = [x for x in finals_all if not is_assistant_message_micro_shrunk(x)]
        out.append(h)
        if finals:
            out.append(finals[-1])
        # 若 is_final 仅存在于微压条，不附助手，避免 dialogue_history 出现【微压工作块】/截断正文
    return out


# ==================== 消息序列化辅助 ====================
def _tool_calls_to_serializable(tool_calls) -> Optional[List[Dict[str, Any]]]:
    if not tool_calls:
        return None
    out = []
    for tc in tool_calls:
        if isinstance(tc, dict):
            out.append({
                "name": tc.get("name", ""),
                "args": tc.get("args", {}),
                "id": tc.get("id", ""),
            })
        else:
            out.append({
                "name": getattr(tc, "name", "") or "",
                "args": getattr(tc, "args", {}) or {},
                "id": getattr(tc, "id", "") or "",
            })
    return out


def _message_to_dict(msg):
    """将消息对象转换为可序列化的字典，区分不同类型。"""
    if isinstance(msg, UserMessage):
        d_u: Dict[str, Any] = {"type": "user", "content": msg.content}
        umd = getattr(msg, "metadata", None) or {}
        if umd:
            d_u["metadata"] = dict(umd)
        return d_u
    elif isinstance(msg, AssistantMessage):
        d = {"type": "assistant", "content": msg.content}
        tc = _tool_calls_to_serializable(getattr(msg, "tool_calls", None))
        if tc:
            d["tool_calls"] = tc
        md = getattr(msg, "metadata", None)
        if md:
            d["metadata"] = dict(md)
        ak = getattr(msg, "additional_kwargs", None)
        if ak:
            d["additional_kwargs"] = dict(ak)
        return d
    elif isinstance(msg, SystemMessage):
        # 检查是否为真正的系统提示（环境信息、规则等）
        # 真正的系统提示通常不包含特定前缀
        content = msg.content
        if content.startswith("🤖 LLM Response:"):
            # 这是LLM中间响应，应该使用AssistantMessage类型
            return {"type": "assistant", "content": content}
        elif content.startswith("Environment Information:") or content.startswith("New Agent Loop Start"):
            # 真正的系统提示
            return {"type": "system", "content": content}
        else:
            # 其他系统消息（状态、通知等）
            return {"type": "system", "content": content}
    elif isinstance(msg, ToolMessage):
        return {"type": "tool", "content": msg.content, "tool_call_id": msg.tool_call_id}
    else:
        return {"type": "other", "content": str(msg.content)}

def _dict_to_message(d):
    """从字典恢复消息对象；兼容旧 type 名（human/llm/ai/agent → user/assistant）。"""
    msg_type = d.get("type", "other")
    content = d.get("content", "")
    tool_calls = d.get("tool_calls")
    if not isinstance(tool_calls, list) or len(tool_calls) == 0:
        tool_calls = None

    # 向后兼容：旧 type 名 → 新 type 名（OpenAI 标准）
    _LEGACY_USER_TYPES = {"human"}
    _LEGACY_ASSISTANT_TYPES = {"llm", "ai", "agent"}

    if msg_type in _LEGACY_USER_TYPES:
        msg_type = "user"
    elif msg_type in _LEGACY_ASSISTANT_TYPES:
        msg_type = "assistant"

    if msg_type == "user":
        u_meta = d.get("metadata")
        u_meta_d = dict(u_meta) if isinstance(u_meta, dict) else {}
        restored_content = content if isinstance(content, list) else str(content)
        return UserMessage(content=restored_content, metadata=u_meta_d)
    elif msg_type == "assistant":
        if tool_calls is None:
            msg = AssistantMessage(content=content)
        else:
            msg = AssistantMessage(content=content, tool_calls=tool_calls)
        if d.get("metadata"):
            msg.metadata = d["metadata"]
        if d.get("additional_kwargs"):
            msg.additional_kwargs = d["additional_kwargs"]
        return msg
    elif msg_type == "system":
        return SystemMessage(content=content)
    elif msg_type == "tool":
        return ToolMessage(content=content, tool_call_id=d.get("tool_call_id", ""))
    else:
        return SystemMessage(content=content)

def _serialize_message(msg) -> dict:
    """将消息转换为更详细的字典（用于记录 LLM 调用）"""
    if isinstance(msg, SystemMessage):
        return {"role": "system", "content": msg.content}
    elif isinstance(msg, UserMessage):
        return {"role": "user", "content": msg.content}
    elif isinstance(msg, AssistantMessage):
        item = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            item["tool_calls"] = [
                {
                    "name": tc["name"],
                    "args": tc["args"],
                    "id": tc.get("id", "")
                } for tc in msg.tool_calls
            ]
        return item
    elif isinstance(msg, ToolMessage):
        return {"role": "tool", "content": msg.content, "tool_call_id": msg.tool_call_id}
    else:
        return {"role": "other", "content": str(msg.content)}
