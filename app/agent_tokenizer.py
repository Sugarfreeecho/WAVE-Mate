"""
使用本仓库内 tokenizer.json（HuggingFace 格式）估算 token 数。

优先用 HuggingFace `tokenizers` 库（Rust 实现、**不依赖 PyTorch**），避免
`transformers.AutoTokenizer` 触发 torch 检测与长导入。

目录默认：<项目根>/tools/deepseek_v3_tokenizer
或通过环境变量 DEEPSEEK_TOKENIZER_DIR 指定。不可用时回退为「字符 / 4」。

另含「整包输入」token 估算（静态 system + key_context + 多轮对话），供 agent_loop /
agent_memory 与右上角占用一致；其中对 agent_harness / agent_tools 的引用在函数内延迟 import，
避免 agent_harness → agent_tokenizer → agent_harness 循环初始化。
"""

from __future__ import annotations

import logging
import os
import platform
import re
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from agent_messages import AssistantMessage, SystemMessage, ToolMessage, UserMessage

logger = logging.getLogger(__name__)

_TOKENIZER: Any = None
_LOAD_FAILED: bool = False


def _is_loop_marker_text(text: str) -> bool:
    c = (text or "").strip()
    return c == "New Agent Loop Start" or c.startswith("Loop finished")


def _strip_tool_display_prefix(text: str) -> str:
    s = str(text or "")
    return re.sub(r"^(?:\U0001f527\s*)?Tool Call:\s*[^\n]*?->\s*", "", s, count=1)


def _default_tokenizer_dir() -> Path:
    env = (os.getenv("DEEPSEEK_TOKENIZER_DIR") or "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "tools" / "deepseek_v3_tokenizer"


def _get_tokenizer() -> Optional[Any]:
    """返回 `tokenizers.Tokenizer` 实例，失败则 None（之后始终走字符/4）。"""
    global _TOKENIZER, _LOAD_FAILED
    if _LOAD_FAILED:
        return None
    if _TOKENIZER is not None:
        return _TOKENIZER
    d = _default_tokenizer_dir()
    path = d / "tokenizer.json"
    if not path.is_file():
        _LOAD_FAILED = True
        logger.info("未找到 DeepSeek 词表（缺 tokenizer.json），token 估算使用字符/4：%s", d)
        return None
    try:
        from tokenizers import Tokenizer  # type: ignore

        _TOKENIZER = Tokenizer.from_file(str(path))
        logger.info("已加载 tokenizer.json 用于 token 估算（tokenizers，无 PyTorch 依赖）：%s", path)
        return _TOKENIZER
    except Exception as e:
        _LOAD_FAILED = True
        logger.warning("加载 tokenizer.json 失败，回退字符/4：%s", e)
        return None


def _flatten_messages_for_count(messages: List[Any]) -> str:
    """与历史上 estimate_message 口径一致：汇总 content、tool_calls、reasoning。"""
    parts: List[str] = []
    for msg in messages:
        if isinstance(msg, SystemMessage) and _is_loop_marker_text(getattr(msg, "content", "")):
            continue
        if hasattr(msg, "content"):
            c = msg.content
            if isinstance(c, str):
                parts.append(_strip_tool_display_prefix(c) if isinstance(msg, ToolMessage) else c)
            elif c is not None:
                parts.append(str(c))
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            parts.append(str(msg.tool_calls))
        ak = getattr(msg, "additional_kwargs", None) or {}
        if isinstance(ak, dict) and ak.get("reasoning_content"):
            parts.append(str(ak["reasoning_content"]))
    return "\n\n".join(parts)


def _count_by_chars_4(s: str) -> int:
    if not s:
        return 0
    return max(0, len(s) // 4)


def count_text_tokens(text: str) -> int:
    s = text or ""
    tok = _get_tokenizer()
    if tok is None:
        return _count_by_chars_4(s)
    try:
        enc = tok.encode(s)
        return len(enc.ids)
    except Exception as e:
        logger.debug("encode 失败，回退字符/4：%s", e)
        return _count_by_chars_4(s)


def count_message_tokens(messages: List[Any]) -> int:
    return count_text_tokens(_flatten_messages_for_count(messages))


# ==================== 整包输入 token（与主模型上送一致）====================


def inject_missing_tool_messages(messages: List[Any]) -> List[Any]:
    """
    含 tool_calls 的 assistant 后必须紧跟对应 id 的 tool 消息；缺则用占位 ToolMessage 补齐，避免 400。
    """
    result: List[Any] = []
    idx = 0
    n = len(messages)
    while idx < n:
        msg = messages[idx]
        result.append(msg)
        if isinstance(msg, AssistantMessage) and getattr(msg, "tool_calls", None):
            need_ids = [tc.get("id") for tc in msg.tool_calls if tc.get("id")]
            seen = set()
            idx += 1
            while idx < n and isinstance(messages[idx], ToolMessage):
                tm = messages[idx]
                tid = getattr(tm, "tool_call_id", None) or ""
                if tid:
                    seen.add(tid)
                result.append(tm)
                idx += 1
            for tid in need_ids:
                if tid and tid not in seen:
                    result.append(
                        ToolMessage(
                            content="[工具返回缺失：可能因会话中断或历史压缩未保留，此为占位。]",
                            tool_call_id=tid,
                        )
                    )
            continue
        idx += 1
    return result


def messages_for_openai_turns(llm_history: List[Any]) -> List[Any]:
    """
    从持久化 llm_history 中构造 API 多轮（user/assistant/tool），不含前置静态 system 链与 key_context system（由外部拼接）。
    压缩区在微压与全量保留之间，可为：
    - 新版：System「Conversation compacted」+ User「[压缩摘要]…」，均原样上送（user 不包一层 [系统上下文]）；
    - 旧版：System「【历史上下文已压缩/摘要区】…」仍原样上送。
    其它非以上 System（提醒等）转为带 [系统上下文] 前缀的 user，避免与专用 system 层混淆。
    """
    from agent_harness import (
        is_compress_summary_system_message,
        is_conversation_compress_boundary_system,
    )

    out: List[Any] = []
    skip_contents = {
        "New Agent Loop Start",
        "Loop finished",
    }
    for msg in llm_history:
        if isinstance(msg, UserMessage):
            out.append(msg)
        elif isinstance(msg, AssistantMessage):
            out.append(msg)
        elif isinstance(msg, ToolMessage):
            out.append(ToolMessage(content=_strip_tool_display_prefix(msg.content), tool_call_id=msg.tool_call_id))
        elif isinstance(msg, SystemMessage):
            c = (msg.content or "").strip()
            if c in skip_contents:
                continue
            if is_compress_summary_system_message(msg) or is_conversation_compress_boundary_system(msg):
                out.append(msg)
                continue
            out.append(UserMessage(content="[系统上下文]\n" + (msg.content or "")))
        else:
            out.append(UserMessage(content=str(getattr(msg, "content", ""))))
    return out


def build_env_static(session_id: Optional[str] = None) -> str:
    """Build the Environment block: calendar month, OS, paths, session storage (no live workspace listing)."""
    from agent_harness import PROJECT_ROOT, WORK_DIR
    from agent_tools import describe_run_shell_executor_for_prompt

    sid = (session_id or "").strip()

    wdir = str(WORK_DIR.resolve())
    proj = str(PROJECT_ROOT.resolve())

    session_lines = ""
    if sid:
        sdir = (WORK_DIR / "sessions" / sid).resolve()
        v_key = f"sessions/{sid}/key_context.md"
        session_lines = f"""
- **This session's directory (absolute)**: {sdir}
  - On-disk files include: `llm_history.json`, `dialogue_history.json` (user↔final from `ui_events`), `work_messages.json`, `ui_events.json`, `key_context.md`, `todo_plan.md`, `metadata.json`, and related artifacts.
  - You may **read** or **grep** under this folder to inspect the **full** persisted history or event stream when you need more detail than the in-context messages.
  - **Key information** should be written to **`key_context.md`** when it must persist across turns. Virtual path under the work root: `/{v_key}`.
  - **What the extra `key_context` `system` message contains** (when non-empty): The server injects **full `key_context.md` body** as rendered for the model (legacy sessions may still strip an embedded `## Todo 计划` if present). **Todo** lives in **`todo_plan.md`** in the same folder—use **`update_todo`** or **`read_file`** on `sessions/{sid}/todo_plan.md` for the live plan. Use **`context_manage`** with `mode=edit_key_context` to revise key text per instructions."""
    else:
        session_lines = "\n- **Session directory**: not set for this run."

    run_shell_executor_hint = describe_run_shell_executor_for_prompt()
    current_year_month = datetime.now().strftime("%Y-%m")

    text = f"""
## Environment
- **Calendar month (host local time)**: **{current_year_month}**
- **OS**: {platform.system()} | **Python**: {platform.python_version()}
{run_shell_executor_hint}
- **MCP extensions** (optional): With `mcp_servers.json` at the project root (or env `MCP_SERVERS_JSON`), or settings saved via **Advanced settings → MCP configuration**, extra tools appear as `mcp_<server_alias>_<tool_name>`. Default `MCP_UI_APPROVAL=1` prompts in the browser for each MCP call; set `0` to disable. Use `MCP_UI_APPROVAL_ALLOW_REGEX` to skip approval for matching tool names.
- **Project / repository root** (`General_Agent` tree — this agent's source root for self-location): {proj}
- **Working directory** (`WORK_DIR`; tool sandbox — virtual `/` maps here): {wdir}. **`write_file`**, **`web_download`**, **`delete_file`** `.trash/`, and **`run_shell`** (when restricted) use this tree only unless UI approves broader paths.

## This conversation's storage{session_lines}
    """.strip()
    return text


def build_static_system_segments(skills_catalog: str, env_static: str) -> List[str]:
    """
    静态 system 分段上送（不含 key_context、不含对话轮）。
    顺序兼顾可读性与前缀缓存：角色原则 → 工具清单 → 调用策略 → 技能目录 → 环境。
    """
    from agent_harness import load_prompt_template

    identity = load_prompt_template("system_identity").strip()
    contract = load_prompt_template("system_tool_contract").strip()
    skills_tpl = load_prompt_template("system_skills_intro").strip()
    skills_block = skills_tpl.format(skills_catalog=skills_catalog)
    parts = [
        "## 角色与回答原则\n\n" + identity,
        "## 工具调用策略\n\n" + contract,
        "## 技能目录\n\n" + skills_block,
        env_static.strip(),
    ]
    return [p for p in parts if p.strip()]


def estimate_full_input_tokens_for_llm_history(
    session_id: str,
    llm_history: List[Any],
    key_context: str,
) -> int:
    """
    与 react_node 发往主模型前、`compute_context_tokens_for_session`（右上角）一致的整包 token：
    静态 system 多段 + key_context 注入 + 多轮 turn（含 tool 占位补齐），reasoning 剥除口径相同。
    """
    from agent_harness import (
        estimate_tokens,
        key_context_body_for_system_prompt,
        strip_reasoning_for_api_request,
    )
    from agent_tools import get_skills_catalog

    sid = str(session_id or "").strip()
    skills_catalog = get_skills_catalog()
    env_static = build_env_static(sid if sid else None)
    kc_body = key_context_body_for_system_prompt(key_context or "")
    static_segments = build_static_system_segments(skills_catalog, env_static)
    turn_msgs = inject_missing_tool_messages(messages_for_openai_turns(llm_history))
    llm_messages: List[Any] = [SystemMessage(content=s) for s in static_segments]
    if kc_body:
        llm_messages.append(SystemMessage(content=kc_body))
    llm_messages.extend(turn_msgs)
    _for_est = strip_reasoning_for_api_request(llm_messages)
    return int(estimate_tokens(_for_est))
