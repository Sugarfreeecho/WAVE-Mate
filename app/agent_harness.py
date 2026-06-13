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
import logging
import shutil
import copy
import uuid
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import dotenv
import httpx
from openai import OpenAI
import threading

from agent_messages import UserMessage, AssistantMessage, ToolMessage, SystemMessage
from agent_openai import (
    chat_completion,
    parse_assistant_message,
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


# ==================== 配置（环境变量，见 .env）====================
# 工程根目录（app/ 的上级）；兼容旧名 PROJECT_ROOT
PROJECT_ROOT = _PROJECT_ROOT.parent


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
TODO_MAX_ITEMS = int(os.getenv("TODO_MAX_ITEMS", "10"))

EXECUTOR_LLM = os.getenv("EXECUTOR_LLM", "deepseek-v4-flash")
EXECUTOR_LLM_TYPE = (os.getenv("EXECUTOR_LLM_TYPE") or "openai").strip().lower()
EXECUTOR_TEMPERATURE = float(os.getenv("EXECUTOR_TEMPERATURE", 0.7))

# 本地 OpenAI 兼容服务（根 URL + /v1）
LOCAL_LLM_HOST = os.getenv("LOCAL_LLM_HOST", "http://localhost:11434")
LOCAL_LLM = os.getenv("LOCAL_LLM", "qwen3.5:9b")
_LOCAL_OPENAI_DUMMY_KEY = "local"

# 默认与 .env 中 DeepSeek 兼容一致；API Key 仍只通过环境变量 / .env 提供，勿写入仓库
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 尝试从加密文件加载配置（仅在.env中没有对应值时作为默认值）
try:
    from secret_loader import load_encrypted_config
    _encrypted_config = load_encrypted_config()
    if _encrypted_config:
        _loaded_keys = []
        for _k, _v in _encrypted_config.items():
            if not os.environ.get(_k):
                os.environ[_k] = _v
                _loaded_keys.append(_k)
        if "OPENAI_API_KEY" in _loaded_keys:
            OPENAI_API_KEY = _encrypted_config["OPENAI_API_KEY"]
        if "EXECUTOR_LLM" in _loaded_keys:
            EXECUTOR_LLM = _encrypted_config["EXECUTOR_LLM"]
        if "OPENAI_BASE_URL" in _loaded_keys:
            OPENAI_BASE_URL = _encrypted_config["OPENAI_BASE_URL"]
        if _loaded_keys:
            logging.getLogger(__name__).info(f"从加密文件加载默认配置: {_loaded_keys}")
except Exception as e:
    logging.getLogger(__name__).warning("Failed to load encrypted config defaults: %s", e)

if not OPENAI_API_KEY and EXECUTOR_LLM_TYPE == "openai":
    logging.getLogger(__name__).warning(
        "OPENAI_API_KEY 未设置且 EXECUTOR_LLM_TYPE=openai；Web 将停留在配置向导（/setup），保存密钥后再使用对话。"
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


def _env_llm_thinking_wants_extra_body_enabled() -> bool:
    raw = (os.getenv("LLM_THINKING_MODE") or "").strip().lower()
    if not raw:
        return True
    if raw == "enabled":
        return True
    if raw == "disabled":
        return False
    logging.getLogger(__name__).warning(
        "LLM_THINKING_MODE 应为 enabled 或 disabled（当前 %r），已按 enabled 处理",
        os.getenv("LLM_THINKING_MODE"),
    )
    return True


def _openai_base_url_likely_deepseek() -> bool:
    return "deepseek" in (os.getenv("OPENAI_BASE_URL") or "").lower()


def _load_executor_extra_body() -> Optional[Dict[str, Any]]:
    raw = os.getenv("LLM_EXTRA_BODY_JSON", "").strip()
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
    if _env_llm_thinking_wants_extra_body_enabled():
        eb_out = {"thinking": {"type": "enabled"}}
    # 关闭思考：非 DeepSeek 基准通常可省略 thinking；DeepSeek 须显式 disabled，否则会默认仍为思考开启
    elif _openai_base_url_likely_deepseek():
        eb_out = {"thinking": {"type": "disabled"}}
    return _sanitize_extra_body_drop_reasoning_when_thinking_off(eb_out)


EXECUTOR_EXTRA_BODY: Optional[Dict[str, Any]] = _load_executor_extra_body()


def _extra_body_thinking_enabled() -> bool:
    """与 DeepSeek「思考模式」一致：extra_body 含 thinking.type == enabled 时需回传带工具调用的 reasoning。"""
    return _thinking_enabled_from_extra_dict(EXECUTOR_EXTRA_BODY)


def _executor_reasoning_effort() -> Optional[str]:
    """仅思考开启时才下发顶层 reasoning_effort；否则忽略 LLM_REASONING_EFFORT。"""
    if not _extra_body_thinking_enabled():
        v_skip = (os.getenv("LLM_REASONING_EFFORT") or "").strip()
        if v_skip:
            logging.getLogger(__name__).debug(
                "思考未开启，已忽略 LLM_REASONING_EFFORT=%r", v_skip
            )
        return None
    v = (os.getenv("LLM_REASONING_EFFORT") or "").strip()
    return v if v else "high"


# 主模型：思考开时带 reasoning_effort，关时为 None
EXECUTOR_REASONING_EFFORT: Optional[str] = _executor_reasoning_effort()


def strip_reasoning_for_api_request(messages: List[Any]) -> List[Any]:
    """?????? reasoning_content ?? token??????????????"""
    thinking_on = _extra_body_thinking_enabled()
    out: List[Any] = []
    for m in messages:
        if not isinstance(m, AssistantMessage):
            out.append(m)
            continue
        ak = getattr(m, "additional_kwargs", None)
        if not isinstance(ak, dict):
            out.append(m)
            continue

        new_ak = dict(ak)
        has_tool_calls = bool(getattr(m, "tool_calls", None))

        # ?????assistant ? tool_calls ?????? reasoning_content ???
        if has_tool_calls:
            if "reasoning_content" not in new_ak:
                new_ak["reasoning_content"] = ""
        elif not thinking_on:
            new_ak.pop("reasoning_content", None)
        else:
            new_ak["reasoning_content"] = ""

        out.append(m.model_copy(update={"additional_kwargs": new_ak}))
    return out
def _context_env_int(name: str, default: str) -> int:
    """读取上下文压缩相关 int；仅认 `name`（CONTEXT_*），不设则用 default。"""
    v = os.getenv(name)
    if v is not None:
        return int(v)
    return int(default)


# 压缩 / 记忆策略（.env 使用下列 CONTEXT_* 名，与 agent_memory 一致）
# 与压缩/应急共用同一估算 token 门限 T：若 (整包上送 > T) 或 (仅多轮 work > T) 且块数足则尝试压缩
CONTEXT_WINDOW = _context_env_int("CONTEXT_WINDOW", "128000")
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
# 单次进入压缩后「并行摘要 + key + 微压」最大迭代轮数；用尽后仍超目标则按约 50% 窗口截尾
CONTEXT_COMPRESS_MAX_ROUNDS = _context_env_int("CONTEXT_COMPRESS_MAX_ROUNDS", "3")
# 摘要第 3 轮：完整保留「最后一条 user + 其后再多 N 次 assistant（ReAct 步）」
CONTEXT_COMPRESS_ROUND3_MAX_REACT = _context_env_int("CONTEXT_COMPRESS_ROUND3_MAX_REACT", "10")
# 达标：整包估算 token（与状态行同口径）≤ CONTEXT_WINDOW × 该比例
CONTEXT_COMPRESS_TARGET_RATIO = float(os.getenv("CONTEXT_COMPRESS_TARGET_RATIO", "0.6"))

REPEAT_DETECTION_THRESHOLD_SUMMARY = int(os.getenv("REPEAT_DETECTION_THRESHOLD_SUMMARY", "2"))
REPEAT_DETECTION_THRESHOLD_ERROR = int(os.getenv("REPEAT_DETECTION_THRESHOLD_ERROR", "3"))

# ==================== 新截断配置（首尾保留方式）====================
# 日志消息截断保留字符数（首尾各保留N字符）
LOG_TRUNCATE_KEEP_CHARS = int(os.getenv("LOG_TRUNCATE_KEEP_CHARS", "200"))

# LLM上下文工具结果截断保留字符数（首尾各保留N字符）
LLM_CONTEXT_TRUNCATE_KEEP_CHARS = int(os.getenv("LLM_CONTEXT_TRUNCATE_KEEP_CHARS", "20000"))

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
    """工具结果写入 LLM 上下文前的首尾截断；过长时在开头提示模型分块阅读。"""
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= keep_chars * 2:
        return text
    notice = (
        "[系统提示：以下工具返回过长已做首尾截断，请勿当作全文；请收窄查询或分块读取（如缩小 grep/glob、"
        "read_file 指定 start_line/end_line）。]\n"
    )
    return notice + truncate_head_tail(text, keep_chars)

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
def load_prompt_template(template_name: str) -> str:
    """从 prompt.md 加载指定的模板片段"""
    if template_name == "tools_description":
        return ""
    try:
        path = resolve_prompt_md_path()
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

# ==================== 自定义 HTTP 客户端（记录 OpenAI 请求/响应，供日志中的 token 统计）====================
class RequestResponseLogger(httpx.Client):
    """包装 httpx.Client，在 interactions 中追加脱敏后的请求与 usage。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.interactions = []

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

OPENAI_HTTP_TIMEOUT = float(os.getenv("OPENAI_HTTP_TIMEOUT", "600"))
executor_http_client = RequestResponseLogger(timeout=OPENAI_HTTP_TIMEOUT)

MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "8192"))


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
    text = re.sub(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*[^\s,;]+", r"\1=***", text)
    return text


def _masked_model_label(model_name: str) -> str:
    s = str(model_name or "").strip()
    return _redact_runtime_log_text(s) if s else "(empty)"


def _masked_base_label(value: Optional[str]) -> str:
    return "configured" if str(value or "").strip() else "default"


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
        )
        return client, resolved


executor_client, executor_model = create_openai_client(
    EXECUTOR_LLM,
    EXECUTOR_LLM_TYPE,
    "executor",
    http_client=executor_http_client,
)


def refresh_executor_client_from_env() -> None:
    """
    向导 / 前端保存 .env 后调用：将热点变量从磁盘同步到本模块，并重建 executor、
    回填 agent_loop / agent_memory 中与主循环共用的引用。
    联网搜索（WEB_SEARCH_*）在 agent_tools 内按次读 os.environ，load_app_dotenv 后即生效。
    """
    global OPENAI_API_KEY, OPENAI_BASE_URL, executor_client, executor_model
    global EXECUTOR_LLM, EXECUTOR_LLM_TYPE, MAX_OUTPUT_TOKENS
    global CONTEXT_WINDOW, CONTEXT_KEEP_RECENT_TURNS, MAX_REACT_ITER, SUBAGENT_MAX_REACT_ITER, LLM_CONTEXT_TRUNCATE_KEEP_CHARS
    global CONTEXT_COMPRESS_FAILURE_MAX_TOKENS, CONTEXT_COMPRESS_MAX_ROUNDS, CONTEXT_COMPRESS_ROUND3_MAX_REACT
    global CONTEXT_COMPRESS_TARGET_RATIO
    global EXECUTOR_EXTRA_BODY, EXECUTOR_REASONING_EFFORT

    load_app_dotenv()

    EXECUTOR_LLM = os.getenv("EXECUTOR_LLM", "deepseek-v4-flash")
    EXECUTOR_LLM_TYPE = (os.getenv("EXECUTOR_LLM_TYPE") or "openai").strip().lower()
    MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "8192"))
    CONTEXT_WINDOW = _context_env_int("CONTEXT_WINDOW", "128000")
    CONTEXT_KEEP_RECENT_TURNS = _context_env_int("CONTEXT_KEEP_RECENT_TURNS", "3")
    LLM_CONTEXT_TRUNCATE_KEEP_CHARS = int(os.getenv("LLM_CONTEXT_TRUNCATE_KEEP_CHARS", "20000"))
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

    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

    # 尝试从加密文件加载配置（仅在.env中没有对应值时作为默认值）
    try:
        from secret_loader import load_encrypted_config
        _encrypted_config = load_encrypted_config()
        if _encrypted_config:
            _loaded_keys = []
            for _k, _v in _encrypted_config.items():
                if not os.environ.get(_k):
                    os.environ[_k] = _v
                    _loaded_keys.append(_k)
            if "OPENAI_API_KEY" in _loaded_keys:
                OPENAI_API_KEY = _encrypted_config["OPENAI_API_KEY"]
            if "EXECUTOR_LLM" in _loaded_keys:
                EXECUTOR_LLM = _encrypted_config["EXECUTOR_LLM"]
            if "OPENAI_BASE_URL" in _loaded_keys:
                OPENAI_BASE_URL = _encrypted_config["OPENAI_BASE_URL"]
    except Exception as e:
        logging.getLogger(__name__).warning("Failed to reload encrypted config defaults: %s", e)

    if not OPENAI_API_KEY and EXECUTOR_LLM_TYPE == "openai":
        logging.getLogger(__name__).warning(
            "OPENAI_API_KEY 未设置且 EXECUTOR_LLM_TYPE=openai；对话/API 调用将失败，请通过向导或 .env 配置密钥。"
        )

    EXECUTOR_EXTRA_BODY = _load_executor_extra_body()
    EXECUTOR_REASONING_EFFORT = _executor_reasoning_effort()

    executor_client, executor_model = create_openai_client(
        EXECUTOR_LLM,
        EXECUTOR_LLM_TYPE,
        "executor",
        http_client=executor_http_client,
    )
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


def executor_text_complete(prompt: str) -> str:
    """单轮补全，走执行端（压缩摘要、key_context 条目、会话标题等）。"""
    text, _ = single_turn_text_completion(
        executor_client,
        executor_model,
        prompt,
        temperature=EXECUTOR_TEMPERATURE,
        max_tokens=MAX_OUTPUT_TOKENS,
    )
    return text


def executor_chat_complete(messages: List[Any]) -> str:
    """多轮 chat，走执行端（compress_history_and_key 等结构化上送）。"""
    r = chat_completion(
        executor_client,
        executor_model,
        messages,
        tools=None,
        temperature=EXECUTOR_TEMPERATURE,
        max_tokens=MAX_OUTPUT_TOKENS,
    )
    return (parse_assistant_message(r.choices[0].message).content or "").strip()


def executor_chat_complete_stream(
    messages: List[Any],
    on_content_delta: Optional[Callable[[str], None]] = None,
) -> str:
    """
    执行端多轮 chat 流式补全；每收到 content 片段即回调 on_content_delta（供压缩/要点 SSE 推送）。
    返回完整正文（与 executor_chat_complete 一致）。
    """
    import queue as _queue

    sync_q: _queue.Queue = _queue.Queue()

    def _worker() -> None:
        run_chat_completion_stream_worker(
            sync_q,
            executor_client,
            executor_model,
            messages,
            tools=None,
            temperature=EXECUTOR_TEMPERATURE,
            max_tokens=MAX_OUTPUT_TOKENS,
        )

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    parts: List[str] = []
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
        elif tag == "err" and isinstance(payload, BaseException):
            err = payload
    t.join()
    if err is not None:
        raise err
    return "".join(parts)


def executor_text_and_usage(prompt: str) -> Tuple[str, Optional[Dict[str, int]]]:
    """与 executor_text_complete 相同，返回 usage。"""
    return single_turn_text_completion(
        executor_client,
        executor_model,
        prompt,
        temperature=EXECUTOR_TEMPERATURE,
        max_tokens=MAX_OUTPUT_TOKENS,
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
        with open(path, "w", encoding="utf-8") as f:
            json.dump(events, f, indent=2, ensure_ascii=False)


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

    def __init__(self, sessions_dir: Path, index_file: Path):
        self.sessions_dir = sessions_dir
        self.index_file = index_file
        self.repository = SessionRepository(sessions_dir, self._resolve_session_path)
        self.event_log = SessionEventLog(self.repository)
        self._lock = threading.Lock()
        self._metadata_session_locks: Dict[str, threading.Lock] = {}
        self._metadata_session_locks_guard = threading.Lock()
        self._load_index()
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
                name = meta.get("name") or "新会话"
                created_at = meta.get("created_at")
                if not created_at:
                    try:
                        created_at = datetime.fromtimestamp(meta_path.stat().st_ctime).isoformat()
                    except OSError:
                        created_at = datetime.now().isoformat()
                updated_at = meta.get("updated_at") or created_at
                archived = bool(meta.get("archived", False))
                pinned = bool(meta.get("pinned", False))
                pinned_at = meta.get("pinned_at")
                if pinned and not pinned_at:
                    pinned_at = updated_at
                by_id[sid] = {
                    "id": sid,
                    "name": name,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "archived": archived,
                    "pinned": pinned,
                    "pinned_at": pinned_at if pinned else None,
                    "unread_result": bool(meta.get("unread_result", False)),
                    "unread_result_at": meta.get("unread_result_at"),
                    "last_user_preview": str(meta.get("last_user_preview") or ""),
                }
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
        if not path.is_file():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}
            out: Dict[str, str] = {}
            for k, v in data.items():
                if not k or not v:
                    continue
                try:
                    ck = self._normalize_session_id(str(k))
                    pv = self._normalize_session_id(str(v))
                except ValueError:
                    continue
                out[ck] = pv
            return out
        except Exception:
            return {}

    def _save_subagent_index(self, idx: Dict[str, str]) -> None:
        path = self._subagent_index_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(idx, f, indent=2, ensure_ascii=False)

    def _register_subagent(self, child_session_id: str, parent_session_id: str) -> None:
        child_id = self._normalize_session_id(child_session_id)
        parent_id = self._normalize_session_id(parent_session_id)
        idx = self._load_subagent_index()
        idx[child_id] = parent_id
        self._save_subagent_index(idx)
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
            for parent_dir in root.iterdir():
                if not parent_dir.is_dir():
                    continue
                try:
                    self._normalize_session_id(parent_dir.name)
                except ValueError:
                    continue
                candidate = (parent_dir / "subagents" / sid).resolve()
                try:
                    candidate.relative_to(root)
                except ValueError:
                    continue
                if candidate.is_dir():
                    self._register_subagent(sid, parent_dir.name)
                    return candidate
        except FileNotFoundError:
            pass
        return None

    def _resolve_session_path(self, session_id: str, *, parent_hint: Optional[str] = None) -> Path:
        sid = self._normalize_session_id(session_id)
        root = self.sessions_dir.resolve()
        flat = (root / sid).resolve()
        try:
            flat.relative_to(root)
        except ValueError as e:
            raise ValueError("Invalid session_id") from e
        # Subagent 目录优先于同名顶层 ghost 目录（误写顶层 sessions/{child_id}/ 时仍走嵌套路径）
        if parent_hint:
            try:
                pid = self._normalize_session_id(parent_hint)
                nested = (root / pid / "subagents" / sid).resolve()
                nested.relative_to(root)
                if nested.is_dir() or self._load_subagent_index().get(sid) == pid:
                    return nested
            except ValueError:
                pass
        idx = self._load_subagent_index()
        parent_id = idx.get(sid)
        if parent_id:
            nested = (root / parent_id / "subagents" / sid).resolve()
            try:
                nested.relative_to(root)
            except ValueError as e:
                raise ValueError("Invalid session_id") from e
            return nested
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
        path = (root / parent_id / "subagents" / child_id).resolve()
        try:
            path.relative_to(root)
        except ValueError as e:
            raise ValueError("Invalid session_id") from e
        return path

    def _get_pending_subagent_results_path(self, session_id: str) -> Path:
        return self.repository.pending_subagent_results_path(session_id)

    def _get_subagent_tasks_path(self, session_id: str) -> Path:
        return self.repository.subagent_tasks_path(session_id)

    def list_subagent_tasks(self, parent_session_id: str) -> List[dict]:
        """读取父会话下的 subagent task 状态索引。"""
        return self.repository.load_json_list(self._get_subagent_tasks_path(parent_session_id))

    def upsert_subagent_task(self, parent_session_id: str, task_id: str, patch: Dict[str, Any]) -> None:
        """维护父会话下 subagent task 状态索引，供 UI/恢复/调试使用。"""
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

    def write_subagent_output(self, child_session_id: str, text: str) -> str:
        """将 subagent 最终可读输出写入子会话 output.md，返回路径。"""
        path = self._get_session_path(child_session_id) / "output.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text or ""), encoding="utf-8")
        return str(path)

    def read_subagent_task_output(self, parent_session_id: str, task_id: str) -> Dict[str, Any]:
        """读取父会话下某个 subagent/task 的可读输出文件。"""
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
        tid = str(task_id or "").strip() or "subagent"
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", tid)
        path = self._get_session_path(parent_session_id) / "subagent_outputs" / f"{safe}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text or ""), encoding="utf-8")
        return str(path)

    def append_pending_subagent_result(self, parent_session_id: str, entry: Dict[str, Any]) -> None:
        path = self._get_pending_subagent_results_path(parent_session_id)
        rows: List[dict] = self.repository.load_json_list(path)
        row = dict(entry)
        if row.get("after_final_index") is None:
            events = self._load_ui_events(parent_session_id)
            anchor = self._latest_final_index_without_later_user(events)
            if anchor >= 0:
                row["after_final_index"] = anchor
        rows.append(row)
        self.repository.save_json_list(path, rows)

    def _load_pending_subagent_results(self, session_id: str) -> List[dict]:
        path = self._get_pending_subagent_results_path(session_id)
        rows = self.repository.load_json_list(path)
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
        events = self._load_ui_events(session_id)
        if not rows or not events:
            return []
        last_idx = self._latest_final_index_without_later_user(events)
        if last_idx < 0:
            return []
        out: List[dict] = []
        for item in rows:
            if not self._pending_subagent_notification_line(item):
                continue
            afi = item.get("after_final_index")
            if afi is None:
                anchor = last_idx
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

    def can_continue_after_subagents(self, session_id: str) -> bool:
        """
        是否应续接父 Agent：有待注入结果，且 ui_events 末条 final 与 pending 记录的 after_final_index 一致
        （父轮已结束、用户尚未在同一 final 之后开新轮）。
        """
        return bool(self._actionable_pending_subagent_rows(session_id))

    def can_continue_react_session(self, session_id: str) -> bool:
        """True when a ReAct turn has user input but no final answer yet."""
        events = self._load_ui_events(session_id)
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

    def consume_pending_subagent_notifications(self, session_id: str) -> List[str]:
        """读取并消费可注入的后台 subagent 通知，供父 react_node 注入。"""
        rows = self._load_pending_subagent_results(session_id)
        events = self._load_ui_events(session_id)
        path = self._get_pending_subagent_results_path(session_id)
        last_idx = self._latest_final_index_without_later_user(events)
        if not rows or not events or last_idx < 0:
            return []
        lines: List[str] = []
        keep: List[dict] = []
        for item in rows:
            line = self._pending_subagent_notification_line(item)
            if not line:
                keep.append(item)
                continue
            try:
                anchor = last_idx if item.get("after_final_index") is None else int(item.get("after_final_index"))
            except (TypeError, ValueError):
                keep.append(item)
                continue
            if anchor == last_idx:
                lines.append(line)
            else:
                keep.append(item)
        self.repository.save_json_list(path, keep)
        return lines

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
            path = self._get_pending_subagent_results_path(session_id)
            self.repository.save_json_list(path, keep)
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
        path = self._get_pending_subagent_results_path(session_id)
        self.repository.save_json_list(path, keep)
        return removed

    def _extract_subagent_dialogue_turns(
        self, session_id: str, *, result_preview: str = ""
    ) -> List[Dict[str, str]]:
        """子 agent 问/答轮次：ui_events 主链；无 final 时回退 llm_response / result_preview。"""
        turns: List[Dict[str, str]] = []
        pending_user = ""
        last_response = ""
        try:
            for ev in self._load_ui_events(session_id):
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
            for ev in reversed(self._load_ui_events(parent_id)):
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
        }
        try:
            for ev in self._load_ui_events(session_id):
                if not isinstance(ev, dict) or str(ev.get("type") or "") != "process_metrics":
                    continue
                if ev.get("duration_ms") is None:
                    continue
                for key in totals:
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
            for ev in reversed(self._load_ui_events(session_id)):
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
            events = self._load_ui_events(cid)
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
                            for ev in self._load_ui_events(cid)
                        )
                    except Exception:
                        has_final = False
                if not result_preview and not lite_mode:
                    try:
                        for ev in reversed(self._load_ui_events(cid)):
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
                    "forked_from_parent": bool(meta.get("forked_from_parent")),
                    "executor_model": executor_model,
                    "output_file": str(meta.get("output_file") or "").strip(),
                    "running": is_running,
                    "ok": status_info.get("ok"),
                    "status": status_info.get("status"),
                    "error": status_info.get("error"),
                    "has_final": has_final,
                    "result_preview": result_preview,
                    "dialogue_turns": dialogue_turns,
                    "session_metrics": session_metrics,
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
        return self.event_log.load(session_id)

    def _save_ui_events(self, session_id: str, events: List[dict]) -> None:
        try:
            from session_lifecycle import is_session_deleted

            if is_session_deleted(session_id):
                return
        except Exception:
            pass
        self.event_log.save(session_id, events)
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

    def append_ui_event(self, session_id: str, event: Dict[str, Any]) -> None:
        """追加一条与 SSE 同结构的 UI 事件（供刷新时原样重放）。"""
        if not event or not isinstance(event, dict):
            return
        try:
            from session_lifecycle import is_session_deleted

            if is_session_deleted(session_id):
                return
        except Exception:
            pass
        try:
            events = self._load_ui_events(session_id)
            event_copy = json.loads(json.dumps(event, ensure_ascii=False))
            events.append(event_copy)
            self._save_ui_events(session_id, events)
            try:
                from runtime_v2.mirror import RuntimeMirror

                RuntimeMirror(self.repository.sessions_dir).mirror_ui_event(session_id, event_copy)
            except Exception as mirror_error:
                logger.debug("Runtime V2 mirror ui_event failed: %s", mirror_error)
            if (
                event_copy.get("type") == "user"
                and not event_copy.get("_subagent_forward")
                and not event_copy.get("_recap")
                and not event_copy.get("_micro_context_shrink")
            ):
                preview = _normalize_sidebar_preview_text(str(event_copy.get("content") or ""), 180)
                with self._session_metadata_lock(session_id):
                    meta = self._load_metadata_unlocked(session_id)
                    if not isinstance(meta, dict):
                        meta = {}
                    if meta.get("last_user_preview") != preview:
                        meta["last_user_preview"] = preview
                        self._save_metadata_unlocked(session_id, meta)
                changed = False
                with self._lock:
                    for sess in self.index:
                        if sess.get("id") == session_id:
                            sess["last_user_preview"] = preview
                            changed = True
                            break
                if changed:
                    self._save_index()
                self.clear_session_unread_result(session_id)
            elif event_copy.get("type") == "final":
                self.mark_session_unread_result(session_id)
        except Exception as e:
            logger.warning(f"append_ui_event 失败: {e}")

    def get_ui_events_for_display(self, session_id: str) -> List[dict]:
        """返回与流式接口相同结构的事件列表，供前端仅调用 renderEvent 重放。"""
        return self._load_ui_events(session_id)

    def get_ui_events_page(
        self,
        session_id: str,
        limit: int = 200,
        before_index: Optional[int] = None,
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
        events = self._load_ui_events(session_id)
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

    def get_todo_plan_snapshot(self, session_id: str) -> dict:
        """从 todo_plan.md 解析「## Todo 计划」，供左侧「当前计划」浮层展示。"""
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
        boundary_for_branch: bool = False,
    ) -> bool:
        """
        保留 ui_events[0:before_index]（下标为 before_index 及之后均丢弃），
        并据此裁剪 dialogue、work_messages、llm_history；**不清空 key_context**。
        work 按保留 user 轮数裁剪；已压缩 llm 与改写/分支同一套边界 + cprefix 对齐。
        boundary_for_branch=True 时按「分支点 final 前 user」取锚点，且不裁掉该 user 轮。
        """
        try:
            events = self._load_ui_events(session_id)
            n = len(events)
            if before_index < 0:
                return False
            if before_index > n:
                before_index = n
            new_events = events[:before_index]
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
        self, source_session_id: str, before_index: int
    ) -> Optional[dict]:
        """
        从 source 在 ui_events[0:before_index] 处复制出新会话（原会话不变）。
        返回 {"session_id", "name"}，失败返回 None。
        """
        try:
            sid = self._normalize_session_id(source_session_id)
            src_path = self._get_session_path(sid)
            if not src_path.is_dir():
                return None
            events = self._load_ui_events(sid)
            n = len(events)
            if before_index < 0:
                return None
            if before_index > n:
                before_index = n
            new_id = str(uuid.uuid4())
            dst_path = self._get_session_path(new_id)
            if dst_path.exists():
                return None
            branch_name = self._next_branch_session_name(sid)
            now_iso = datetime.now().isoformat()
            new_events = events[:before_index]
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
            return {"session_id": new_id, "name": branch_name}
        except Exception as e:
            logger.warning("branch_session_at_event_index 失败: %s", e)
            return None

    def repair_compacted_llm_history_from_ui(self, session_id: str) -> bool:
        """
        已压缩会话：按当前 ui_events / work_messages 重新对齐 llm_history（与改写/分支同一套边界逻辑）。
        若 llm 已被错误撑大且 metadata 有 branched_from，先尝试从源会话恢复 llm 再对齐。
        """
        try:
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
        以 ui_events 中 type=user 条数为唯一事实源，裁剪 llm_history / work_messages 尾部。
        用于修复：请求在「已 append user、已写 New Agent Loop Start」后因 400/异常中止，下一轮又叠
        加 human，而 ui_events 因截断或未重复记录导致比 llm 少 user 条数的情况。
        """
        try:
            events = self._load_ui_events(session_id)
            llm_raw = self._load_llm_history(session_id)
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
        with open(path, "w", encoding="utf-8") as f:
            json.dump(llm_history, f, indent=2, ensure_ascii=False)

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
        self.repository.save_metadata_atomic(session_id, metadata)

    def _load_metadata_unlocked(self, session_id: str) -> dict:
        return self.repository.load_metadata(session_id)

    def _save_metadata(self, session_id: str, metadata: dict) -> None:
        with self._session_metadata_lock(session_id):
            self._save_metadata_unlocked(session_id, metadata)

    def _load_metadata(self, session_id: str) -> dict:
        with self._session_metadata_lock(session_id):
            return self._load_metadata_unlocked(session_id)

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
            "is_subagent": True,
            "parent_session_id": parent_id,
            "subagent_type": stype,
            "subagent_description": desc,
            "subagent_depth": max(1, int(depth)),
            "subagent_max_iter": SUBAGENT_MAX_REACT_ITER,
            "readonly_strict": bool(readonly_strict),
            "forked_from_parent": bool(forked_from_parent),
        }
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
        self._save_work_messages(child_id, [])
        self._save_llm_history(child_id, [])
        self._save_key_context(child_id, "")
        self._save_metadata(child_id, metadata)
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
        executor_model: str = "",
        executor_llm_type: str = "",
        readonly_strict: bool = False,
    ) -> str:
        """resume=self：复制父会话 llm/work/key_context 到新 subagent。"""
        import copy

        parent_id = self._normalize_session_id(parent_session_id)
        child_id = self.create_subagent_session(
            parent_id,
            description,
            subagent_type,
            depth,
            executor_model=executor_model,
            executor_llm_type=executor_llm_type,
            readonly_strict=readonly_strict,
            forked_from_parent=True,
        )
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
        logger.info(
            "已删除 subagent %s ← parent=%s（含 descendants=%s）",
            child_id,
            parent_id,
            max(0, len(ids) - 1),
        )
        return True

    def get_or_create_session(self, session_id: Optional[str] = None) -> Tuple[str, List[dict], List[dict], List[dict], str, dict]:
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
            session_id = str(uuid.uuid4())
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
            }
            dialogue: List[dict] = []  # 与 dialogue_history.json 均由 ui_events 主链写入
            self._save_work_messages(session_id, work_messages)
            self._save_llm_history(session_id, llm_history)
            self._save_key_context(session_id, key_context)
            self._save_metadata(session_id, metadata)
            self._save_ui_events(session_id, [])
            self._save_dialogue_history(session_id, [])
            self.index.append({
                "id": session_id,
                "name": metadata["name"],
                "created_at": metadata["created_at"],
                "updated_at": metadata.get("updated_at") or metadata["created_at"],
                "archived": bool(metadata.get("archived", False)),
                "pinned": bool(metadata.get("pinned", False)),
                "pinned_at": metadata.get("pinned_at") if metadata.get("pinned") else None,
            })
            self._save_index()
            logger.info(f"创建新会话: {session_id}")
            return session_id, dialogue, work_messages, llm_history, key_context, metadata
        else:
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
                    if "pinned_at" in metadata:
                        sess["pinned_at"] = metadata.get("pinned_at")
                    elif metadata.get("pinned") is False:
                        sess["pinned_at"] = None
                    break
            self._save_index()

    def delete_session(self, session_id: str):
        sid = self._normalize_session_id(session_id)
        idx = self._load_subagent_index()
        children = [cid for cid, pid in idx.items() if pid == sid]
        for cid in children:
            self._unregister_subagent(cid)
        session_path = self._get_session_path(sid)
        if session_path.exists():
            shutil.rmtree(session_path)
        self._unregister_subagent(sid)
        self.index = [s for s in self.index if s["id"] != sid]
        self._save_index()
        logger.info(f"已删除会话: {sid}")

    def last_user_question_preview(self, session_id: str, max_len: int = 180) -> str:
        """最近一条用户提问的单行预览（侧栏）；优先 ui_events，其次 dialogue_history。"""
        sid = (session_id or "").strip()
        if not sid:
            return ""
        events = self._load_ui_events(sid)
        for ev in reversed(events):
            if not isinstance(ev, dict) or ev.get("type") != "user":
                continue
            raw = ev.get("content")
            text = raw if isinstance(raw, str) else str(raw or "")
            return _normalize_sidebar_preview_text(text, max_len)
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
        d["unread_result"] = bool(d.get("unread_result", False))
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
        if sid:
            # The session index only contains top-level sessions; avoid the generic
            # resolver here because it reloads the subagent index for every row.
            ui_path = self.sessions_dir / str(sid) / "ui_events.json"
            if ui_path.exists():
                try:
                    mt = ui_path.stat().st_mtime
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

    def list_sessions(self, include_archived: bool = False) -> List[dict]:
        """返回会话列表；每条含 last_activity_at。置顶在前，其余按最近活动时间倒序。"""
        if not include_archived:
            base_rows = [dict(s) for s in self.index if not s.get("archived")]
        else:
            base_rows = [dict(s) for s in self.index]
        rows = [self._session_entry_with_activity(s) for s in base_rows]

        def _iso_ts(raw: Any) -> float:
            if not raw:
                return 0.0
            try:
                iso = str(raw).replace("Z", "+00:00")
                return datetime.fromisoformat(iso).timestamp()
            except Exception:
                return 0.0

        def sort_key(r: dict) -> Tuple[int, float, float]:
            pinned = bool(r.get("pinned"))
            pt = _iso_ts(r.get("pinned_at"))
            la = r.get("last_activity_at") or r.get("updated_at") or r.get("created_at")
            lt = _iso_ts(la)
            return (0 if pinned else 1, -pt if pinned else 0.0, -lt)

        rows.sort(key=sort_key)
        return rows

    def archived_session_count(self) -> int:
        """Return the number of archived sessions without materializing session details."""
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

    def mark_session_unread_result(self, session_id: str) -> None:
        sid = self._normalize_session_id(session_id)
        meta_path = self._get_metadata_path(sid)
        if not meta_path.exists():
            return
        now = datetime.now().isoformat()
        with self._session_metadata_lock(sid):
            metadata = self._load_metadata_unlocked(sid)
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["unread_result"] = True
            metadata["unread_result_at"] = now
            self._save_metadata_unlocked(sid, metadata)
        changed = False
        with self._lock:
            for sess in self.index:
                if sess.get("id") == sid:
                    sess["unread_result"] = True
                    sess["unread_result_at"] = now
                    changed = True
                    break
        if changed:
            self._save_index()

    def clear_session_unread_result(self, session_id: str) -> None:
        sid = self._normalize_session_id(session_id)
        meta_path = self._get_metadata_path(sid)
        if not meta_path.exists():
            return
        with self._session_metadata_lock(sid):
            metadata = self._load_metadata_unlocked(sid)
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["unread_result"] = False
            metadata.pop("unread_result_at", None)
            self._save_metadata_unlocked(sid, metadata)
        changed = False
        with self._lock:
            for sess in self.index:
                if sess.get("id") == sid:
                    sess["unread_result"] = False
                    sess.pop("unread_result_at", None)
                    changed = True
                    break
        if changed:
            self._save_index()

    def request_interrupt(self, session_id: str):
        """请求中断指定会话当前执行。"""
        with self._session_metadata_lock(session_id):
            metadata = self._load_metadata_unlocked(session_id)
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["interrupt_requested"] = True
            self._save_metadata_unlocked(session_id, metadata)

    def clear_interrupt(self, session_id: str):
        """清除会话中断标记（新任务启动前调用）。始终写回 False，避免残留 true。"""
        sid = (session_id or "").strip()
        if not sid:
            return
        with self._session_metadata_lock(sid):
            metadata = self._load_metadata_unlocked(sid)
            if not metadata:
                return
            metadata["interrupt_requested"] = False
            self._save_metadata_unlocked(sid, metadata)

    def is_interrupt_requested(self, session_id: str) -> bool:
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
        metadata = self._load_metadata(session_id)
        return bool(metadata.get("interrupt_requested", False))


session_manager = SessionManager(SESSIONS_DIR, INDEX_FILE)

_executor_override_cache: Dict[str, Tuple[Any, str]] = {}


def resolve_executor_for_session(session_id: str) -> Tuple[Any, str]:
    """子会话可经 metadata.executor_model 覆盖默认 executor 模型。"""
    sid = (session_id or "").strip()
    if not sid:
        return executor_client, executor_model
    try:
        meta = session_manager._load_metadata(sid)
    except Exception:
        meta = {}
    override = ""
    llm_type = EXECUTOR_LLM_TYPE
    if isinstance(meta, dict):
        override = str(meta.get("executor_model") or "").strip()
        llm_type = str(meta.get("executor_llm_type") or EXECUTOR_LLM_TYPE).strip().lower()
    if not override:
        return executor_client, executor_model
    cache_key = f"{llm_type}:{override}"
    cached = _executor_override_cache.get(cache_key)
    if cached is not None:
        return cached
    client, model = create_openai_client(
        override,
        llm_type or EXECUTOR_LLM_TYPE,
        f"subagent:{override[:32]}",
        http_client=executor_http_client,
    )
    _executor_override_cache[cache_key] = (client, model)
    return client, model

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

    def sync_session_from_key_context(self, session_id: str, key_context: str = "") -> None:
        """从 todo_plan.md 恢复该会话的待办列表到内存（key_context 参数保留兼容，忽略）。"""
        if not session_id:
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
                try:
                    session_manager.save_todo_plan(session_id, "")
                except Exception as _se:
                    logger.warning("save_todo_plan 失败: %s", _se)
            return "当前没有待办事项。"
        if len(items) > TODO_MAX_ITEMS:
            raise ValueError(f"最多支持 {TODO_MAX_ITEMS} 个待办事项")
        validated: List[Dict] = []
        in_progress_count = 0
        for i, item in enumerate(items):
            text = str(item.get("text", "")).strip()
            status = str(item.get("status", "pending")).lower()
            item_id = str(item.get("id", str(i + 1)))
            if not text:
                raise ValueError(f"条目 {item_id}: 缺少 text")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"条目 {item_id}: 无效的状态 '{status}'")
            if status == "in_progress":
                in_progress_count += 1
            validated.append({"id": item_id, "text": text, "status": status})
        if in_progress_count > 1:
            raise ValueError("一次只能有一个任务处于 in_progress 状态")
        if validated and all(t["status"] == "completed" for t in validated):
            # 计划全部完成：清空该会话待办
            if session_id not in ("__global__",):
                self._by_session[session_id] = []
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
        items = self._by_session.get(session_id, [])
        if not items:
            return False
        return not all(t["status"] == "completed" for t in items)

todo_manager = TodoManager()

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


def is_compress_recap_user_message(m: Any) -> bool:
    """压缩产生的前情提要 user，不参与主对话派生。"""
    if not isinstance(m, UserMessage):
        return False
    c = (m.content or "") or ""
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
    c = (m.content or "").lstrip()
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
        return UserMessage(content=str(content), metadata=u_meta_d)
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
