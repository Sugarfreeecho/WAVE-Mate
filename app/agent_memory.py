"""
单轨上下文裁剪与摘要：不完整轮与下一条完整轮合并为一段（连续多个不完整轮先接在一起再与后续完整轮合并）；末尾无完整轮可合并时不做裁剪。**例外**：「[压缩摘要]」User 与其后 micro 段须整段保留至下一条真实 user 之前。
待摘要段先做噪声工具与 reasoning 收敛，再在「完整保留×N 轮」之前收敛中间 ReAct。**计轮时 `[压缩摘要]` User 不计入**
（与 `trim_message_dicts_by_kept_user_turns` / ui_events 用户数对齐），避免「摘要站掉一轮」导致尾窗错位、上一轮被吃进摘要；达标判定为整包 token（与右上角同口径）≤ CONTEXT_WINDOW×CONTEXT_COMPRESS_TARGET_RATIO（默认 0.6）；达到则不再调用摘要模型。
摘要 LLM 最多 3 轮，保留尾窗逐轮放宽：第 1 轮完整保留 CONTEXT_KEEP_RECENT_TURNS（默认 3）个 user 轮；第 2 轮仅 1 个 user 轮；第 3 轮最后 1 条 user + 至多 CONTEXT_COMPRESS_ROUND3_MAX_REACT（默认 10）次 ReAct assistant 步；仍不达标则截尾兜底。
其后每轮调用一次摘要模型（`compress_history_and_key`），解析 `<recap>` / `<summary>` 后写入 llm 与 key_context；流式结束写 ui_events（context_summary_body / key_context_body）。
**更早待摘要原文从 llm_history 移除**，顺序为 System（压缩边界）→ User（历史摘要）→ 紧靠尾的微压 legacy 段 → 完整尾部；
进入**摘要模型**轮次前将本轮**待压缩段**（`work[:idx_full_start]`）快照为 `llm_cprefix_<时间戳>.json`；纯微压/裁剪达标则不备份。
改写截断时，仅当改写的用户在当前 llm 中已**无独立非 `[压缩摘要]` user 气泡**（问题已被吃进摘要）时，才从对应前缀备份截断至该用户轮末再与 loop 标记拼接，最后按保留用户数裁剪。
异常时按 CONTEXT_COMPRESS_FAILURE_MAX_TOKENS（默认亦为窗口约 50%）截尾。
要点经 compress_history_and_key 写入 key_context.md **单一**「## 上下文摘要」小节（更新制）。

触发入口（agent_loop.react_node 每轮发模型前）
    ├─ 自动：整包 token > CONTEXT_WINDOW
    ├─ 手动：context_manage(mode=compact)
    └─ 跳过：上一轮已压成功（_compress_skip_next）
    ↓
预处理
    ├─ _filter_work_messages：去掉 loop 标记 System
    └─ _normalize_incomplete_turns：不完整轮与下一条完整轮合并；连续多个不完整轮拼接（[压缩摘要] 段例外整段保留）
    ↓
入口判定 (_compress_entry_state)
    ├─ 整包估算：estimate_full_input_tokens_for_llm_history（与 UI 右上角同口径）
    ├─ pack_over = 整包 > CONTEXT_WINDOW
    ├─ should_run = 手动 compact 或 pack_over（用户轮过少时有额外条件）
    └─ tail_keep = CONTEXT_KEEP_RECENT_TURNS（默认 3；轮数不足时可能降为 1）
    ↓
should_run = false？ ──是──→ 不压缩，直接用当前 llm_history
    ↓ 否
Phase D：待摘要段噪声裁剪 (_apply_phase_d)
    范围：[0, idx_full_start)，idx_full_start = 完整保留 tail_keep 个 user 轮起点
    ├─ 噪声工具 ls/glob/grep/delete_file/web_search → 正文微压
    ├─ 带 tool_calls 的 assistant → 去 reasoning + 正文微压
    └─ 无 tool 的 assistant → 剥 reasoning
    ↓
达标检查？整包 token ≤ CONTEXT_WINDOW × CONTEXT_COMPRESS_TARGET_RATIO（默认 0.6）
    ↓ 是 → 返回微压后的 work（不调摘要 LLM）
    ↓ 否
Phase E：完整保留区×3 之前的中间 ReAct 收敛 (_apply_phase_e)
    范围：[0, idx_keep9)，idx_keep9 = tail_keep × 3 个 user 轮起点
    ├─ 每轮 user 保留
    ├─ 终稿 assistant 保留
    └─ 中间 ReAct assistant/tool → 微压
    ↓
达标检查？整包 token ≤ CONTEXT_WINDOW × 0.6
    ↓ 是 → 返回微压后的 work
    ↓ 否
LLM 摘要轮（最多 CONTEXT_COMPRESS_MAX_ROUNDS 轮，默认 3）
    每轮尾窗逐轮放宽 (_split_prefix_tail_for_summary_round)：
    ├─ 第 1 轮：tail = 最后 tail_keep（默认 3）个 user 轮
    ├─ 第 2 轮：tail = 最后 1 个 user 轮
    └─ 第 3 轮：tail = 最后 1 条 user + 至多 CONTEXT_COMPRESS_ROUND3_MAX_REACT 步 ReAct
    ↓
    单轮 (_compress_summary_round)：
    1. backup_llm_compress_prefix() → sessions/{sid}/llm_cprefix_<ts>.json
    2. compress_history_and_key（一次 LLM，解析 <recap> + <summary>）
       ├─ <recap>  → 历史前情提要（纯文本）
       ├─ <summary> → 要点写入 key_context.md「## 上下文摘要 · 时间戳」
       └─ <analysis> → 可选草稿，不落盘
    3. legacy 微压区拼装 (_assemble_micro_region_only_snap)
       └─ tail 边界前固定 CONTEXT_MICRO_WORK_ROUNDS 块（默认 20 块，位置随 prefix 切点滑动）
    4. 重组 llm (_merge_summary_into_work)：
       System(Conversation compacted…) → User([压缩摘要] recap) → 微压段 → 完整 tail
    5. prefix 原文从 llm_history 移除
    ↓
    达标检查？整包 token ≤ CONTEXT_WINDOW × 0.6
    ├─ 是 → 结束摘要 loop
    └─ 否 → 还有轮次？继续下一轮（尾窗更窄）
    ↓
仍不达标 → compress_tail_fallback(reason=max_rounds)
    └─ System(截尾边界) + 保留约 CONTEXT_WINDOW/2 token 尾部
    ↓
异常任意阶段 → compress_tail_fallback(reason=failure)
    └─ 同上，预算 CONTEXT_COMPRESS_FAILURE_MAX_TOKENS（默认 T/2）
    ↓
压缩完成后整包仍 > CONTEXT_WINDOW？
    └─ 应急截断 compress_tail_fallback(reason=emergency)
       ├─ 无边界 System，仅截尾
       └─ 最多重试 CONTEXT_EMERGENCY_SHRINK_MAX_RETRIES 次（默认 3）
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Callable, List, Literal, Optional, Tuple

from agent_tokenizer import estimate_full_input_tokens_for_llm_history

from agent_harness import (
    COMPACT_BOUNDARY_SYSTEM_EXACT,
    COMPACT_TRUNCATED_BOUNDARY_SYSTEM_EXACT,
    COMPACT_RECAP_USER_PREFIX,
    UserMessage,
    AssistantMessage,
    SystemMessage,
    ToolMessage,
    CONTEXT_WINDOW,
    CONTEXT_COMPRESS_PROMPT_TOKEN_RATIO,
    CONTEXT_COMPRESS_FAILURE_MAX_TOKENS,
    CONTEXT_COMPRESS_MAX_ROUNDS,
    CONTEXT_COMPRESS_ROUND3_MAX_REACT,
    CONTEXT_COMPRESS_TARGET_RATIO,
    CONTEXT_KEEP_RECENT_TURNS,
    CONTEXT_MICRO_WORK_ROUNDS,
    MICRO_SHRINK_ASSISTANT_CHARS,
    MICRO_SHRINK_FAT_TOOL_FLOOR,
    MICRO_SHRINK_REASONING_CHARS,
    MICRO_SHRINK_TOOL_CHARS,
    estimate_tokens,
    is_compress_recap_user_message,
    is_micro_shrink_user_message,
    _last_n_session_user_turn_slice_start,
    _session_loop_marker_content,
    executor_chat_complete,
    executor_chat_complete_stream,
    executor_text_complete,
    is_ephemeral_system_stripped_by_compress,
    load_prompt_template,
    logger,
    merge_compress_summary_into_key_context,
    session_manager,
    truncate_head_tail,
)


def _preview_llm_for_ui_estimate(work: List) -> List:
    """与压缩成功路径返回的 llm_history 形态一致，供整包 token 与右上角同口径。"""
    out = _filter_work_messages(work)
    return [m for m in out if not is_ephemeral_system_stripped_by_compress(m)]


def _compress_target_full_pack_cap_tokens() -> int:
    """与右上角一致的「达标」整包 token 上限：CONTEXT_WINDOW×CONTEXT_COMPRESS_TARGET_RATIO。"""
    return max(256, int(int(CONTEXT_WINDOW) * float(CONTEXT_COMPRESS_TARGET_RATIO)))


def _full_pack_tokens_for_session_preview(
    session_id: str,
    preview_llm_history: List,
    key_context: str,
) -> int:
    """与右上角 `/context_tokens`、`react_node` 上送前整包估算同一函数。"""
    return int(
        estimate_full_input_tokens_for_llm_history(
            session_id,
            list(preview_llm_history or []),
            key_context or "",
        )
    )


def _full_pack_tokens_compress_work(session_id: str, work: List, key_context: str) -> int:
    return _full_pack_tokens_for_session_preview(
        session_id,
        _preview_llm_for_ui_estimate(work),
        key_context,
    )


def _compress_ratio_reached(
    session_id: str,
    work: List,
    key_context: str,
) -> bool:
    """是否已达到 CONTEXT_COMPRESS_TARGET_RATIO；与 UI 一致：基于 `estimate_full_input_tokens_for_llm_history` 整包。"""
    fp = _full_pack_tokens_compress_work(session_id, work, key_context)
    cap = _compress_target_full_pack_cap_tokens()
    return fp <= cap


def _full_pack_pct_ui(
    session_id: str,
    preview_llm_history: List,
    key_context: str,
) -> float:
    """上下文窗口占比（%），与右上角同为整包口径。"""
    cw = max(1, int(CONTEXT_WINDOW))
    tok = _full_pack_tokens_for_session_preview(session_id, preview_llm_history, key_context)
    return round(100.0 * float(tok) / float(cw), 1)


def _format_context_window_pct_for_hint(pct: float) -> str:
    """压缩进度行中的占比文案；超过 100% 时不封顶，仅显示实际百分比。"""
    return f"{float(pct):.1f}%"


def auto_length_strategy_status_line(
    base: str,
    *,
    session_id: str,
    llm_history: List,
    key_context: str = "",
) -> str:
    """【自动·长度策略】终态行：附加与右上角一致的整包上下文占比。"""
    pct = _full_pack_pct_ui(session_id, list(llm_history or []), key_context or "")
    return f"{base}（当前约占上下文窗口 {_format_context_window_pct_for_hint(pct)}）"


def _push_progress_hint(
    hints: List[str],
    hint_sink: Optional[Callable[[Any], None]],
    base: str,
    *,
    kind: str,
    session_id: str = "",
    preview_llm_history: Optional[List] = None,
    key_context: str = "",
    with_pct: bool = True,
) -> None:
    """kind: trim | summary | key — 供前端分标签展示并实时推送。"""
    line = base
    if with_pct and kind in ("trim", "summary") and session_id:
        pct = _full_pack_pct_ui(session_id, preview_llm_history or [], key_context)
        line = f"{base}（当前约占上下文窗口 {_format_context_window_pct_for_hint(pct)}）"
    hints.append(line)
    if hint_sink is not None:
        try:
            hint_sink({"content": line, "progress_kind": kind})
        except Exception:
            pass


def _push_progress_persist_body(
    hint_sink: Optional[Callable[[Any], None]],
    body: str,
    *,
    kind: str,
) -> None:
    """压缩/要点流式结束后的全文，写入 ui_events 供刷新后回放。"""
    if hint_sink is None:
        return
    text = (body or "").strip()
    if not text:
        return
    try:
        hint_sink({"progress_kind": kind, "persist_body": text})
    except Exception:
        pass


def _is_marker_system(m) -> bool:
    return isinstance(m, SystemMessage) and _session_loop_marker_content(str(m.content or ""))


def _filter_work_messages(msgs: List) -> List:
    """去掉会话循环标记类 System，供分块与压缩。"""
    return [m for m in (msgs or []) if not _is_marker_system(m)]


def _counts_toward_session_user_turns_message(m) -> bool:
    """与 ui_events 中 type=user 对齐计数的「真人用户」气泡。"""
    return (
        isinstance(m, UserMessage)
        and not is_compress_recap_user_message(m)
        and not is_micro_shrink_user_message(m)
    )


def _count_user_turns(work: List) -> int:
    return sum(1 for m in work if _counts_toward_session_user_turns_message(m))


def _full_keep_start_index(work: List, n_keep_turns: int) -> int:
    """完整保留最后 n_keep_turns 个「用户轮」时，切片 work[idx:] 的起点（与 harness 落盘 trim 同计轮规则）。"""
    return _last_n_session_user_turn_slice_start(
        work, n_keep_turns, counts_toward_user_turn=_counts_toward_session_user_turns_message
    )


def _collect_blocks(msgs: List) -> List[Tuple[int, int, str]]:
    """
    微压用「块」（不计 loop 标记；普通 System 不视为块）：
    - user：单条 UserMessage
    - assistant：一条 AssistantMessage + 其后连续 ToolMessage；或仅连续 ToolMessage 的异常段
    """
    segs: List[Tuple[int, int, str]] = []
    i, n = 0, len(msgs)
    while i < n:
        m = msgs[i]
        if isinstance(m, SystemMessage):
            i += 1
            continue
        if isinstance(m, UserMessage):
            segs.append((i, i, "user"))
            i += 1
            continue
        if isinstance(m, AssistantMessage):
            st, j = i, i + 1
            while j < n and isinstance(msgs[j], ToolMessage):
                j += 1
            segs.append((st, j - 1, "assistant"))
            i = j
            continue
        if isinstance(m, ToolMessage):
            t0, j = i, i
            while j < n and isinstance(msgs[j], ToolMessage):
                j += 1
            segs.append((t0, j - 1, "assistant"))
            i = j
            continue
        i += 1
    return segs


_NOISE_TOOL_NAMES = frozenset({"ls", "glob", "grep", "delete_file", "web_search"})


def _micro_tool_keep_each_side() -> int:
    """工具正文微压：首尾各保留字符数（fat 与普通同一上限口径）。"""
    return max(int(MICRO_SHRINK_TOOL_CHARS), int(MICRO_SHRINK_FAT_TOOL_FLOOR))


def _micro_shrink_truncate_plain(text: str, keep_each_side: int) -> str:
    """
    微压截断：首尾各保留 keep_each_side，中间为「...已微压省略{n}字符...」。
    总长 ≤ 2*keep 时不截断。
    """
    s = "" if text is None else str(text)
    k = max(0, int(keep_each_side))
    if k == 0 or len(s) <= 2 * k:
        return s
    omitted = len(s) - 2 * k
    return f"{s[:k]}\n...已微压省略{omitted}字符...\n{s[-k:]}"


def _micro_shrink_tool_message_content_inplace(m: ToolMessage) -> bool:
    raw = str(m.content or "")
    t2 = _micro_shrink_truncate_plain(raw, _micro_tool_keep_each_side())
    if t2 != raw:
        m.content = t2
        return True
    return False


def _micro_shrink_tool_calling_assistant_inplace(mm: AssistantMessage) -> bool:
    """
    带 tool_calls 的 assistant：有正文则移除 reasoning，并对正文做微压；
    仅有 reasoning（或无正文）时对 reasoning 微压。
    """
    changed = False
    ak = dict(getattr(mm, "additional_kwargs", None) or {})
    rc = ak.get("reasoning_content")
    rcs = "" if rc is None else str(rc)
    raw_c = str(mm.content or "")
    has_c = bool(raw_c.strip())
    has_r = bool(rcs.strip())

    if has_c:
        if has_r:
            ak.pop("reasoning_content", None)
            changed = True
        new_c = _micro_shrink_truncate_plain(raw_c, int(MICRO_SHRINK_ASSISTANT_CHARS))
        if new_c != raw_c:
            mm.content = new_c
            changed = True
        mm.additional_kwargs = ak
        return changed

    if has_r:
        new_r = _micro_shrink_truncate_plain(rcs, int(MICRO_SHRINK_REASONING_CHARS))
        ak["reasoning_content"] = new_r
        mm.additional_kwargs = ak
        changed = True
    return changed


def _final_assistant_index(work: List, s: int, e: int) -> Optional[int]:
    """Turn [s,e] 内最后一条「无 tool_calls」的 assistant；不完整轮返回 None。"""
    for k in range(e, s - 1, -1):
        m = work[k]
        if isinstance(m, AssistantMessage) and not (m.tool_calls):
            return k
    return None


def _normalize_incomplete_turns(work: List) -> List:
    """不完整轮与下一条完整轮合并为一段；连续多个不完整轮先拼接，再与后续完整轮一并输出。

    末尾若仍无完整轮可合并，原样保留所有不完整轮消息（不再只留 user）。

    **例外**：「[压缩摘要]」User 之后的 micro 段未必以「无 tool_calls 的 assistant」收尾，
    须整段保留至下一条**真实用户**之前（遇 recap 前先 flush 已累积的不完整段）。
    """
    n = len(work)
    out: List = []
    pending: List = []
    i = 0
    while i < n:
        m = work[i]
        if not isinstance(m, UserMessage):
            if pending:
                pending.append(deepcopy(m))
            else:
                out.append(deepcopy(m))
            i += 1
            continue
        s = i
        if is_compress_recap_user_message(work[s]):
            if pending:
                out.extend(pending)
                pending = []
            j = s + 1
            while j < n and not _counts_toward_session_user_turns_message(work[j]):
                j += 1
            out.extend(deepcopy(work[s:j]))
            i = j
            continue
        j = s + 1
        while j < n and not isinstance(work[j], UserMessage):
            j += 1
        e = j - 1
        segment = deepcopy(work[s:j])
        fin = _final_assistant_index(work, s, e)
        if fin is None:
            pending.extend(segment)
        else:
            if pending:
                out.extend(pending)
                pending = []
            out.extend(segment)
        i = j
    if pending:
        out.extend(pending)
    return out


def _tool_name_for_tool_message(work: List, ti: int) -> str:
    tid = str(getattr(work[ti], "tool_call_id", "") or "")
    j = ti - 1
    while j >= 0:
        if isinstance(work[j], AssistantMessage):
            for c in work[j].tool_calls or []:
                if isinstance(c, dict) and str(c.get("id") or "") == tid:
                    return str(c.get("name") or "")
            return ""
        j -= 1
    return ""


def _strip_reasoning_inplace(mm: AssistantMessage) -> bool:
    ak = getattr(mm, "additional_kwargs", None) or {}
    if not ak.get("reasoning_content"):
        return False
    ak = dict(ak)
    ak.pop("reasoning_content", None)
    mm.additional_kwargs = ak
    return True


def _apply_phase_d(work: List, idx_cap: int) -> Tuple[List, bool]:
    """待摘要段 [0,idx_cap)：噪声工具与带 tool_calls assistant 统一微压口径。原地修改 work（须已与 llm_history 隔离）。"""
    if idx_cap <= 0:
        return work, False
    changed = False
    n = len(work)
    for i in range(min(idx_cap, n)):
        m = work[i]
        if isinstance(m, AssistantMessage):
            if m.tool_calls:
                if _micro_shrink_tool_calling_assistant_inplace(m):
                    changed = True
            else:
                if _strip_reasoning_inplace(m):
                    changed = True
        elif isinstance(m, ToolMessage):
            name = _tool_name_for_tool_message(work, i)
            if name in _NOISE_TOOL_NAMES and _micro_shrink_tool_message_content_inplace(m):
                changed = True
    return work, changed


def _apply_phase_e(work: List, idx_keep9: int) -> Tuple[List, bool]:
    """完整保留区×3 之前：压缩中间 ReAct，保留每轮 user 与终稿 assistant。原地修改 work（须已与 llm_history 隔离）。"""
    if idx_keep9 <= 0:
        return work, False
    changed = False
    n = len(work)
    for i in range(min(idx_keep9, n)):
        m = work[i]
        if isinstance(m, UserMessage):
            continue
        s = i
        while s >= 0 and not isinstance(work[s], UserMessage):
            s -= 1
        if s < 0:
            continue
        e = s + 1
        while e < n and not isinstance(work[e], UserMessage):
            e += 1
        e -= 1
        fin = _final_assistant_index(work, s, e)
        if fin is None:
            continue
        if isinstance(m, AssistantMessage):
            if i == fin:
                continue
            if m.tool_calls:
                if _micro_shrink_tool_calling_assistant_inplace(m):
                    changed = True
            else:
                if _strip_reasoning_inplace(m):
                    changed = True
                raw = str(m.content or "")
                new_c = _micro_shrink_truncate_plain(raw, int(MICRO_SHRINK_ASSISTANT_CHARS))
                if new_c != raw:
                    m.content = new_c
                    changed = True
        elif isinstance(m, ToolMessage) and _micro_shrink_tool_message_content_inplace(m):
            changed = True
    return work, changed


def _last_real_user_index(work: List) -> Optional[int]:
    for i in range(len(work) - 1, -1, -1):
        if _counts_toward_session_user_turns_message(work[i]):
            return i
    return None


def _split_prefix_tail_for_summary_round(
    work: List, round_idx: int, default_tail_keep: int
) -> Tuple[List, List]:
    """
    摘要第 round_idx 轮（1-based）的 prefix（待吃）/ tail（完整保留）切分。
    第 1 轮：完整保留 default_tail_keep 个 user 轮；第 2 轮：1 个 user 轮；
    第 3 轮：最后 1 条 user + 至多 N 次 ReAct assistant 步（更早 assistant 归入 prefix）。
    """
    w = list(work or [])
    if round_idx >= 3:
        user_idx = _last_real_user_index(w)
        if user_idx is None:
            return w, []
        max_react = max(1, int(CONTEXT_COMPRESS_ROUND3_MAX_REACT))
        asst_idxs = [
            i for i in range(user_idx + 1, len(w)) if isinstance(w[i], AssistantMessage)
        ]
        if len(asst_idxs) <= max_react:
            return w[:user_idx], w[user_idx:]
        cut = asst_idxs[len(asst_idxs) - max_react]
        prefix = w[:user_idx] + w[user_idx + 1 : cut]
        tail = [w[user_idx]] + w[cut:]
        return prefix, tail
    n_keep = default_tail_keep if round_idx <= 1 else 1
    idx = _full_keep_start_index(w, n_keep)
    return w[:idx], w[idx:]


def _assemble_micro_region_only_snap(snap_full: List, idx_full: int, n_micro: int) -> List:
    """
    仅紧靠完整保留尾之前的 N 个 legacy 块（区域 B），做微压；不包含更早块（区域 A 已从序列剔除）。
    """
    legacy_segs = _collect_blocks(snap_full)
    i_full_seg = next(
        (si for si, (s, _, _) in enumerate(legacy_segs) if s == idx_full),
        None,
    )
    if i_full_seg is None:
        i_full_seg = next(
            (
                si
                for si, (s, e, _) in enumerate(legacy_segs)
                if s <= idx_full <= e
            ),
            len(legacy_segs),
        )
    i_micro_seg = max(0, int(i_full_seg) - int(n_micro))
    merged: List = []
    for si in range(i_micro_seg, i_full_seg):
        s, e, kind = legacy_segs[si]
        merged.extend(_micro_shrink_block(snap_full, s, e, kind))
    return merged


def _merge_summary_into_work(
    summary: str, micro_prefix: List, tail: List
) -> Tuple[List, Optional[str]]:
    """摘要轮结束后重组 work；返回 (new_work, recap_text)。"""
    recap = (summary or "").strip()
    merged: List = []
    if recap:
        merged.append(SystemMessage(content=COMPACT_BOUNDARY_SYSTEM_EXACT))
        merged.append(UserMessage(content=COMPACT_RECAP_USER_PREFIX + " " + recap))
    merged.extend(micro_prefix)
    merged.extend(tail)
    return _preview_llm_for_ui_estimate(merged), (recap or None)


def _compress_summary_round(
    key_context: str,
    prefix: List,
    tail: List,
    *,
    hint_sink: Optional[Callable[[Any], None]] = None,
    hints: Optional[List[str]] = None,
    round_idx: int = 1,
    session_id: str = "",
) -> Tuple[str, str, List]:
    """单次摘要 LLM → recap + key 要点 + legacy 微压段。"""
    hint_list = hints if hints is not None else []
    idx_full = len(prefix)
    micro_prefix = _assemble_micro_region_only_snap(
        list(prefix) + list(tail), idx_full, int(CONTEXT_MICRO_WORK_ROUNDS)
    )
    preview = _preview_llm_for_ui_estimate(list(prefix) + list(tail))
    _push_progress_hint(
        hint_list,
        hint_sink,
        f"【上下文摘要】第 {round_idx} 轮：正在生成历史摘要与要点…",
        kind="summary",
        session_id=session_id,
        preview_llm_history=preview,
        key_context=key_context,
    )

    def _on_delta(piece: str) -> None:
        if hint_sink is not None:
            hint_sink({"progress_kind": "summary", "stream_delta": piece})

    summary, key_body = _run_compress_executor_dialogue(
        key_context,
        prefix,
        stream_sink=_on_delta if hint_sink is not None else None,
        hint_sink=hint_sink,
        hints=hint_list,
        session_id=session_id,
    )
    _push_progress_persist_body(hint_sink, summary, kind="summary")
    _push_progress_persist_body(hint_sink, key_body, kind="key")
    if hint_sink is not None and (summary or "").strip():
        _push_progress_hint(
            hint_list, hint_sink, f"【上下文摘要】第 {round_idx} 轮摘要完成",
            kind="summary", session_id=session_id, with_pct=False,
        )
    if hint_sink is not None and (key_body or "").strip():
        _push_progress_hint(
            hint_list, hint_sink, f"【要点】第 {round_idx} 轮要点已写入",
            kind="key", session_id=session_id, with_pct=False,
        )
    return summary, key_body, micro_prefix


def compress_tail_fallback(
    llm_history: List,
    *,
    reason: Literal["failure", "max_rounds", "emergency"],
    max_tokens: Optional[int] = None,
) -> Tuple[List, bool, bool]:
    """
    压缩兜底截尾。返回 (消息列表, 已启用兜底, 是否丢弃了前缀)。
    max_rounds / failure：System(截尾边界) + 约半窗 token 尾部；
    emergency：无边界，仅截尾（react 应急循环用）。
    """
    hist = list(llm_history or [])
    boundary = SystemMessage(content=COMPACT_TRUNCATED_BOUNDARY_SYSTEM_EXACT)

    def _with_boundary(tail: List) -> List:
        return [boundary] + list(tail or [])

    if reason == "emergency":
        mt = int(max_tokens if max_tokens is not None else CONTEXT_COMPRESS_FAILURE_MAX_TOKENS)
        tail, dropped = _llm_history_tail_within_token_budget_with_start(hist, mt)
        return tail, dropped > 0, dropped > 0
    if reason == "max_rounds":
        mt = int(max_tokens if max_tokens is not None else max(4096, int(CONTEXT_WINDOW) // 2))
        if not hist:
            return [_with_boundary([])[0]], True, False
        tail, dropped = _llm_history_tail_within_token_budget_with_start(hist, mt)
        return _with_boundary(tail), True, dropped > 0
    mt = int(max_tokens if max_tokens is not None else CONTEXT_COMPRESS_FAILURE_MAX_TOKENS)
    if not hist:
        return [_with_boundary([])[0]], True, False
    tail, dropped = _llm_history_tail_within_token_budget_with_start(hist, mt)
    return _with_boundary(tail), True, dropped > 0


# 摘要执行器：对话转 chat 时对单条 tool/reasoning 字段的字节上限
_KEY_BULLETS_SOURCE_TOOL_MAX = 500_000
_KEY_BULLETS_SOURCE_REASONING_MAX = 200_000


def _format_conversation_excerpt(
    msgs: List,
    *,
    tool_line_max: int = 8_000,
    reasoning_max: int = 8_000,
) -> str:
    """多轮非 System 内容 → 可送 LLM 的线性文本。不含 System；超长字段按行截断。"""
    lines: List[str] = []
    for m in msgs:
        if isinstance(m, SystemMessage):
            continue
        if isinstance(m, UserMessage):
            lines.append("用户: " + str(m.content or ""))
        elif isinstance(m, AssistantMessage):
            ak = getattr(m, "additional_kwargs", None) or {}
            rc = ak.get("reasoning_content")
            rstr = (str(rc) if rc is not None else "")
            rpart = f"[思考/推理] {truncate_head_tail(rstr, reasoning_max)}\n" if rstr else ""
            tcall = m.tool_calls or []
            tpart = f"[tool_calls] {tcall}\n" if tcall else ""
            lines.append(
                rpart
                + tpart
                + "[助手正文] "
                + str(m.content or "")
            )
        elif isinstance(m, ToolMessage):
            raw = str(m.content or "")
            body = raw if len(raw) <= tool_line_max else truncate_head_tail(raw, tool_line_max)
            lines.append(
                "工具结果(" + str(getattr(m, "tool_call_id", "") or "") + "): " + body
            )
    return "\n".join(lines)


def _dialogue_work_to_chat_messages(
    msgs: List,
    *,
    tool_line_max: int,
    reasoning_max: int,
) -> List:
    """待摘要 work 段 → Chat Completions 多轮（跳过 loop / 应剔除的 system）。"""
    out: List = []
    for m in msgs or []:
        if isinstance(m, SystemMessage):
            if _is_marker_system(m):
                continue
            if is_ephemeral_system_stripped_by_compress(m):
                continue
            continue
        if isinstance(m, UserMessage):
            out.append(UserMessage(content=str(m.content or "")))
        elif isinstance(m, AssistantMessage):
            mm = deepcopy(m)
            ak = dict(mm.additional_kwargs or {})
            rc = ak.get("reasoning_content")
            if rc is not None:
                rcs = str(rc)
                if len(rcs) > reasoning_max:
                    ak["reasoning_content"] = truncate_head_tail(rcs, reasoning_max)
                mm.additional_kwargs = ak
            out.append(mm)
        elif isinstance(m, ToolMessage):
            tm = deepcopy(m)
            raw = str(tm.content or "")
            if len(raw) > tool_line_max:
                tm.content = truncate_head_tail(raw, tool_line_max)
            out.append(tm)
        else:
            out.append(deepcopy(m))
    return out


def _compress_executor_tail_user_content(key_context_markdown: str) -> str:
    """compress_history_and_key 共用末条 user：任务细则在 system 模板；此处仅触发语 + key 摘录（长度一致，避免 trim 偏斜）。"""
    kc = (key_context_markdown or "").strip()
    ref = kc if kc else "（暂无已持久化要点；请仅从上方对话提炼。）"
    return (
        "【你的任务】请读完上方的 user / assistant / tool 对话，并严格按对话最前面的 system 说明完成任务。\n\n"
        "【已有要点摘录】（来自 key_context.md，供增量对照，勿堆砌重复）：\n\n"
        + ref
    )


def _compress_executor_msgs_usable(msgs: List) -> bool:
    return (
        len(msgs) >= 2
        and isinstance(msgs[0], SystemMessage)
        and isinstance(msgs[-1], UserMessage)
    )


def _compress_executor_message_bundle(
    instruction_text: str,
    key_context_markdown: str,
    dialogue_work: List,
    *,
    tool_line_max: int,
    reasoning_max: int,
) -> List:
    """system（细则）→ 待摘要对话（旧→新）→ 末条 user（任务命令 + key 摘录）。"""
    dialogue = _dialogue_work_to_chat_messages(
        dialogue_work,
        tool_line_max=tool_line_max,
        reasoning_max=reasoning_max,
    )
    tail_body = _compress_executor_tail_user_content(key_context_markdown)
    out: List = [SystemMessage(content=(instruction_text or "").strip())]
    out.extend(dialogue)
    out.append(UserMessage(content=tail_body))
    return out


def _dialogue_messages_to_trim_blocks(msgs: List) -> List[List]:
    """
    将对话 middle 段切成「不可再分」块，供 token 裁剪时整组丢弃，避免 assistant+tool_calls 与 tool 分离。
    """
    blocks: List[List] = []
    i = 0
    n = len(msgs or [])
    while i < n:
        m = msgs[i]
        if isinstance(m, UserMessage):
            blocks.append([m])
            i += 1
            continue
        if isinstance(m, AssistantMessage):
            block = [m]
            i += 1
            if m.tool_calls:
                while i < n and isinstance(msgs[i], ToolMessage):
                    block.append(msgs[i])
                    i += 1
            blocks.append(block)
            continue
        if isinstance(m, ToolMessage):
            block: List = []
            while i < n and isinstance(msgs[i], ToolMessage):
                block.append(msgs[i])
                i += 1
            if block:
                blocks.append(block)
            continue
        blocks.append([m])
        i += 1
    return blocks


def _flatten_trim_blocks(blocks: List[List]) -> List:
    out: List = []
    for b in blocks:
        out.extend(b)
    return out


def _compress_executor_dialogue_api_valid(msgs: List) -> bool:
    """middle 段（不含首尾 system / 末条 task user）须满足 OpenAI tool 消息链约束。"""
    if len(msgs) < 2:
        return False
    middle = msgs[1:-1] if isinstance(msgs[-1], UserMessage) else msgs[1:]
    i = 0
    n = len(middle)
    while i < n:
        m = middle[i]
        if isinstance(m, UserMessage):
            i += 1
            continue
        if isinstance(m, AssistantMessage):
            if m.tool_calls:
                i += 1
                while i < n and isinstance(middle[i], ToolMessage):
                    i += 1
                continue
            i += 1
            continue
        if isinstance(m, ToolMessage):
            return False
        i += 1
    return True


def _trim_compress_executor_messages(msgs: List, max_tokens: int) -> List:
    """顺序：system → 对话块 → 末条 user。从最早**整块**删起；仍超则缩短末条 user。"""
    mt = max(1, int(max_tokens))
    if not msgs:
        return msgs
    if len(msgs) == 1:
        return msgs
    if not isinstance(msgs[0], SystemMessage):
        return msgs
    sys_m = msgs[0]
    rest = msgs[1:]
    if not rest:
        return msgs
    tail_u: Optional[UserMessage] = None
    middle_raw: List
    if isinstance(rest[-1], UserMessage):
        tail_u = rest[-1]
        middle_raw = rest[:-1]
    else:
        middle_raw = list(rest)
    blocks = _dialogue_messages_to_trim_blocks(middle_raw)
    bo = 0
    while bo <= len(blocks):
        flat_middle = _flatten_trim_blocks(blocks[bo:])
        cand: List = [sys_m] + flat_middle
        if tail_u is not None:
            cand.append(tail_u)
        if estimate_tokens(cand) <= mt:
            msgs = cand
            break
        bo += 1
    else:
        msgs = [sys_m] + ([tail_u] if tail_u is not None else [])
    guard = 0
    while msgs and estimate_tokens(msgs) > mt and guard < 32:
        guard += 1
        if len(msgs) >= 2 and isinstance(msgs[-1], UserMessage):
            c = str(msgs[-1].content or "")
            if len(c) <= 400:
                break
            msgs[-1].content = truncate_head_tail(c, max(400, len(c) // 2))
            continue
        break
    return msgs


def _compress_executor_prompt_caps() -> Tuple[int, int]:
    ratio = float(CONTEXT_COMPRESS_PROMPT_TOKEN_RATIO)
    if ratio < 1.0:
        ratio = 1.0
    cap_prompt = int(int(CONTEXT_WINDOW) * ratio)
    return cap_prompt, int(CONTEXT_WINDOW)


def _compress_executor_excerpt_fallback(
    dialogue_msgs: List,
    *,
    suffix: str,
) -> str:
    fb = _format_conversation_excerpt(
        dialogue_msgs,
        tool_line_max=8_000,
        reasoning_max=8_000,
    )
    body = ((fb or "")[:8000]).strip()
    if not body:
        return suffix.strip()
    return body + "\n" + suffix.strip()


def _extract_xml_block(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*>([\s\S]*?)</{tag}>", text or "", re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _parse_compress_dialogue_output(raw: str) -> Tuple[str, str]:
    """单次压缩模型输出 → (recap, key_body)。"""
    text = (raw or "").strip()
    if not text:
        return "", ""
    key_body = _extract_xml_block(text, "summary")
    recap = _extract_xml_block(text, "recap")
    if not recap:
        recap = re.sub(r"<analysis[^>]*>[\s\S]*?</analysis>", "", text, flags=re.IGNORECASE)
        recap = re.sub(r"<summary[^>]*>[\s\S]*?</summary>", "", recap, flags=re.IGNORECASE)
        recap = re.sub(r"</?recap>", "", recap, flags=re.IGNORECASE).strip()
    if not recap:
        recap = re.sub(r"</?summary>", "", text, flags=re.IGNORECASE).strip()
    if not key_body:
        logger.warning("compress_history_and_key 无 <summary> 块")
    if not recap:
        logger.warning("compress_history_and_key 无 <recap> 块")
    return recap, key_body


def _run_compress_executor_dialogue(
    key_context: str,
    dialogue_msgs: List,
    *,
    stream_sink: Optional[Callable[[str], None]] = None,
    hint_sink: Optional[Callable[[Any], None]] = None,
    hints: Optional[List[str]] = None,
    session_id: str = "",
) -> Tuple[str, str]:
    """组包 → trim → 执行端 chat（compress_history_and_key）→ 解析 recap + key。"""
    if not (key_context or "").strip() and not dialogue_msgs:
        return "（无来源文本，未生成 summary）", ""
    suffix = "[压缩失败，保留截断原文片段]"
    try:
        try:
            instr = load_prompt_template("compress_history_and_key")
        except Exception:
            instr = (
                "请根据对话一次性输出 <recap> 历史前情提要 </recap> 与 "
                "<summary> key_context 要点 </summary>。"
            )
        msgs = _compress_executor_message_bundle(
            instr,
            key_context,
            dialogue_msgs,
            tool_line_max=_KEY_BULLETS_SOURCE_TOOL_MAX,
            reasoning_max=_KEY_BULLETS_SOURCE_REASONING_MAX,
        )
        if not _compress_executor_msgs_usable(msgs):
            return "（无来源文本，未生成 summary）", ""
        cap_prompt, cap_window = _compress_executor_prompt_caps()
        msgs = _trim_compress_executor_messages(msgs, cap_prompt)
        msgs = _trim_compress_executor_messages(msgs, cap_window)
        if not _compress_executor_msgs_usable(msgs):
            return "（无来源文本，未生成 summary）", ""
        if not _compress_executor_dialogue_api_valid(msgs):
            logger.warning("compress_history_and_key 消息链无效，改用摘录兜底")
            return _compress_executor_excerpt_fallback(dialogue_msgs, suffix=suffix), ""
        for attempt in range(2):
            buffered: List[str] = []

            def _buffer_delta(piece: str) -> None:
                buffered.append(piece)

            call_msgs = msgs
            if attempt:
                strict = (
                    "上一次摘要输出格式无效，已丢弃。请重新输出，且只能使用：\n"
                    "<recap>历史前情摘要</recap>\n<summary>key_context 要点</summary>\n"
                    "不要输出 analysis、解释、Markdown 围栏或其它标签。"
                )
                call_msgs = list(msgs) + [UserMessage(content=strict)]
            if stream_sink is not None:
                raw = executor_chat_complete_stream(call_msgs, on_content_delta=_buffer_delta)
            else:
                raw = executor_chat_complete(call_msgs).strip()
            recap, key_body = _parse_compress_dialogue_output(raw)
            if recap and key_body:
                if stream_sink is not None:
                    for piece in buffered:
                        stream_sink(piece)
                return recap, key_body
            logger.warning("compress_history_and_key 格式无效，已丢弃输出并准备重试 attempt=%s", attempt + 1)
            _push_progress_hint(
                hints if hints is not None else [],
                hint_sink,
                f"【上下文摘要】第 {attempt + 1} 次摘要输出格式无效，已丢弃并准备重试…",
                kind="summary",
                session_id=session_id,
                with_pct=False,
            )
        logger.warning("compress_history_and_key 重试后仍格式无效，改用摘录兜底")
        _push_progress_hint(
            hints if hints is not None else [],
            hint_sink,
            "【上下文摘要】摘要输出格式重试后仍无效，已改用摘录兜底。",
            kind="summary",
            session_id=session_id,
            with_pct=False,
        )
        return _compress_executor_excerpt_fallback(dialogue_msgs, suffix=suffix), ""
    except Exception as e:
        logger.warning("compress_history_and_key 调用失败: %s", e)
        _push_progress_hint(
            hints if hints is not None else [],
            hint_sink,
            f"【上下文摘要】摘要模型调用失败，已改用摘录兜底：{e}",
            kind="summary",
            session_id=session_id,
            with_pct=False,
        )
        return _compress_executor_excerpt_fallback(dialogue_msgs, suffix=suffix), ""


def _llm_history_tail_within_token_budget_with_start(
    llm_history: List, max_tokens: int
) -> Tuple[List, int]:
    """返回 (尾部 deepcopy, 丢弃的前缀消息条数)。"""
    full = list(llm_history or [])
    if not full:
        return [], 0
    mt = int(max_tokens)
    best_start = len(full)
    for start in range(len(full)):
        if estimate_tokens(full[start:]) <= mt:
            best_start = start
            break
    return deepcopy(full[best_start:]), int(best_start)


def _upsert_compress_summary_key_context(session_id: str, summary_body: str) -> str:
    """压缩产生的关键信息：去掉旧「上下文摘要/压缩」块后写入一份合并正文。"""
    cur = session_manager.load_key_context(session_id)
    if (cur or "").strip():
        session_manager.append_key_context_history(session_id, cur, "before compact")
    merged = merge_compress_summary_into_key_context(cur, summary_body)
    session_manager.save_key_context(session_id, merged)
    return merged


def run_edit_key_context_instruction(
    session_id: str,
    instruction: str,
    hint_sink: Optional[Callable[[Any], None]] = None,
) -> Tuple[str, str]:
    """
    按自然语言说明编辑 key_context.md 全文（增删改规则、错误、经验等）。
    返回 (新全文, 给模型看的短说明)。
    """
    hints: List[str] = []
    instr = (instruction or "").strip()
    if not instr:
        cur = session_manager.load_key_context(session_id)
        return cur, "未提供 edit_instruction，未修改 key_context。"
    cur = session_manager.load_key_context(session_id)
    _push_progress_hint(
        hints,
        hint_sink,
        "【要点】正在根据编辑说明更新要点…",
        kind="key",
        session_id=session_id,
        with_pct=False,
    )
    try:
        tpl = load_prompt_template("edit_key_context")
    except Exception:
        tpl = (
            "你是会话关键信息编辑助手。根据「编辑说明」修改下面的全文，可增删改任意 Markdown 小节。\n"
            "输出必须是完整的新版 key_context，且放在唯一一对标签内：\n"
            "<key_context>\n...全文...\n</key_context>\n\n"
            "【当前全文】\n{current}\n\n【编辑说明】\n{instruction}\n"
        )
    prompt = tpl.format(current=cur or "（当前为空）", instruction=instr)
    try:
        raw = executor_text_complete(prompt).strip()
    except Exception as e:
        logger.warning("edit_key_context 执行器调用失败: %s", e)
        return cur, f"编辑失败：{e}"
    m = re.search(r"<key_context[^>]*>([\s\S]*?)</key_context>", raw, re.IGNORECASE)
    new_doc = (m.group(1).strip() if m else raw.strip())
    if not new_doc:
        return cur, "模型未输出有效正文，未修改。"
    if (cur or "").strip():
        session_manager.append_key_context_history(session_id, cur, "before edit")
    session_manager.save_key_context(session_id, new_doc)
    excerpt = new_doc if len(new_doc) <= 8000 else (new_doc[:8000] + "\n…（要点正文已截断）")
    _push_progress_hint(
        hints,
        hint_sink,
        f"【要点】已按说明更新要点\n\n{excerpt}",
        kind="key",
        session_id=session_id,
        with_pct=False,
    )
    return new_doc, "已按说明更新 key_context.md。"


def _micro_shrink_work_block(msgs: List, s: int, e: int) -> List:
    out: List = []
    for i in range(s, e + 1):
        m = msgs[i]
        if isinstance(m, AssistantMessage):
            mm: AssistantMessage = deepcopy(m)
            _md0 = dict(getattr(mm, "metadata", None) or {})
            _md0["micro_shrink"] = True
            mm.metadata = _md0
            if mm.tool_calls:
                _micro_shrink_tool_calling_assistant_inplace(mm)
            else:
                _strip_reasoning_inplace(mm)
                mm.content = _micro_shrink_truncate_plain(
                    str(mm.content or ""), int(MICRO_SHRINK_ASSISTANT_CHARS)
                )
            out.append(mm)
        elif isinstance(m, ToolMessage):
            mm = deepcopy(m)
            _micro_shrink_tool_message_content_inplace(mm)
            out.append(mm)
        else:
            out.append(deepcopy(m))
    return out


def _micro_shrink_user(m: UserMessage) -> UserMessage:
    prev = getattr(m, "metadata", None) or {}
    md = dict(prev) if isinstance(prev, dict) else {}
    md["micro_shrink"] = True
    c = _micro_shrink_truncate_plain(str(getattr(m, "content", "") or ""), int(MICRO_SHRINK_ASSISTANT_CHARS))
    return UserMessage(content=c, metadata=md)


def _micro_shrink_one_tool(m: ToolMessage) -> ToolMessage:
    t = deepcopy(m)
    _micro_shrink_tool_message_content_inplace(t)
    return t


def _micro_shrink_tool_segment(work: List, s: int, e: int) -> List:
    """仅含 Tool 的段（无前置带 tool_calls 的 assistant），仍做工具正文微压。"""
    out: List = []
    for k in range(s, e + 1):
        m = work[k]
        if isinstance(m, ToolMessage):
            out.append(_micro_shrink_one_tool(m))
        else:
            out.append(deepcopy(m))
    return out


def _micro_shrink_block(work: List, s: int, e: int, kind: str) -> List:
    """
    仅 CONTEXT_MICRO_WORK_ROUNDS 划出的「区域 B」内调用。
    User：metadata.micro_shrink=true（改写定位问句时可跳过）；Assistant：同上 meta + 正文微压，
    供 derive_dialogue 跳过微压终稿。
    """
    if kind == "user":
        return [_micro_shrink_user(work[s])]  # type: ignore[arg-type]
    if kind == "assistant":
        if isinstance(work[s], ToolMessage):
            return _micro_shrink_tool_segment(work, s, e)
        return _micro_shrink_work_block(work, s, e)
    return []


def _with_marker_systems(snapshot_llm: List, work_core: List) -> List:
    """保留会话(loop 标记)类 System，接压缩后的 work。"""
    head = [deepcopy(m) for m in (snapshot_llm or []) if _is_marker_system(m)]
    return head + list(work_core)


def _compress_entry_state(
    llm_history: List,
    session_id: str,
    *,
    force_user_compact: bool,
    key_context: str = "",
) -> Tuple[List, bool, int, int, int, bool, bool]:
    """
    压缩入口判定（context_will_attempt_compress 与 _compress_unified_in_place 共用）。
    整包 token 与右上角一致：`estimate_full_input_tokens_for_llm_history`。
    返回 (work, should_run, tail_keep, ut, full_pack, pack_over, ctx_force)。
    """
    work = _normalize_incomplete_turns(_filter_work_messages(list(llm_history or [])))
    if not work:
        return work, False, 0, 0, 0, False, False
    ut = _count_user_turns(work)
    tlim = int(CONTEXT_WINDOW)
    full_pack = _full_pack_tokens_for_session_preview(
        session_id,
        list(llm_history or []),
        key_context or "",
    )
    pack_over = int(full_pack) > tlim
    ctx_force = bool(force_user_compact) or pack_over
    n_keep_cfg = max(1, int(CONTEXT_KEEP_RECENT_TURNS))
    tail_keep = n_keep_cfg
    if ut <= n_keep_cfg:
        if bool(force_user_compact) and ut >= 1:
            should_run = True
            tail_keep = 1
        elif ctx_force and pack_over and ut >= 1:
            should_run = True
            tail_keep = 1
        else:
            should_run = False
    else:
        should_run = bool(force_user_compact) or pack_over
    return work, should_run, tail_keep, ut, full_pack, pack_over, ctx_force


def _compress_unified_in_place(
    llm_history: List,
    session_id: str,
    key_context: str,
    *,
    force_user_compact: bool,
    hint_sink: Optional[Callable[[Any], None]] = None,
) -> Tuple[List, str, bool, List[str], bool, Optional[str]]:
    hints: List[str] = []
    new_recap_text: Optional[str] = None
    snapshot_llm = deepcopy(llm_history)  # 异常/截尾兜底用；work 经 normalize 已与 llm_history 对象隔离，Phase D/E 原地改 work
    work, should_run, tail_keep, _ut, full_pack, pack_over, _ctx_force = _compress_entry_state(
        llm_history,
        session_id,
        force_user_compact=force_user_compact,
        key_context=key_context,
    )
    if not work:
        return list(llm_history or []), key_context, False, hints, False, None
    if not should_run:
        return list(llm_history or []), key_context, False, hints, False, None
    idx_full_start = _full_keep_start_index(work, tail_keep)
    idx_keep9 = _full_keep_start_index(work, tail_keep * 3)

    new_key = key_context

    try:
        work, dchg = _apply_phase_d(work, idx_full_start)
        if dchg:
            _push_progress_hint(
                hints,
                hint_sink,
                "【上下文裁剪】已对非关键信息进行裁剪",
                kind="trim",
                session_id=session_id,
                preview_llm_history=_preview_llm_for_ui_estimate(work),
                key_context=new_key,
            )

        changed_any = bool(dchg)
        cap_tokens = _compress_target_full_pack_cap_tokens()

        # Phase D 后即可判定是否达标，避免「仅微压已够仍强行跑 Phase E + 摘要模型」
        if _compress_ratio_reached(session_id, work, new_key):
            return _preview_llm_for_ui_estimate(work), new_key, changed_any, hints, False, None

        work, ech = _apply_phase_e(work, idx_keep9)
        if ech:
            _push_progress_hint(
                hints,
                hint_sink,
                "【上下文裁剪】已对较早段落中的思考过程进行裁剪",
                kind="trim",
                session_id=session_id,
                preview_llm_history=_preview_llm_for_ui_estimate(work),
                key_context=new_key,
            )

        changed_any = bool(dchg or ech)

        # 与右上角同口径：整包 ≤ CONTEXT_WINDOW×CONTEXT_COMPRESS_TARGET_RATIO。
        if _compress_ratio_reached(session_id, work, new_key):
            return _preview_llm_for_ui_estimate(work), new_key, changed_any, hints, False, None

        cur_key = new_key
        round_idx = 0
        used_llm_summary = False
        key_round_chunks: List[str] = []
        _push_progress_hint(
            hints,
            hint_sink,
            "【上下文摘要】裁剪后仍超限，开始生成历史摘要…",
            kind="summary",
            session_id=session_id,
            preview_llm_history=_preview_llm_for_ui_estimate(work),
            key_context=cur_key,
        )
        max_summary_rounds = max(1, int(CONTEXT_COMPRESS_MAX_ROUNDS))
        fallback_reason = "max_rounds"
        while round_idx < max_summary_rounds:
            if _compress_ratio_reached(session_id, work, cur_key):
                break
            round_idx += 1
            prefix, tail = _split_prefix_tail_for_summary_round(work, round_idx, tail_keep)
            if not prefix:
                fallback_reason = "no_prefix"
                _push_progress_hint(
                    hints,
                    hint_sink,
                    "【上下文摘要】没有足够可摘要的历史前缀，已转入截尾兜底。",
                    kind="summary",
                    session_id=session_id,
                    preview_llm_history=_preview_llm_for_ui_estimate(work),
                    key_context=cur_key,
                )
                break
            try:
                session_manager.backup_llm_compress_prefix(session_id, list(prefix))
            except Exception as _be:
                logger.warning("待压缩段快照失败（忽略）: %s", _be)
            summary, key_body, micro_prefix = _compress_summary_round(
                cur_key,
                prefix,
                tail,
                hint_sink=hint_sink,
                hints=hints,
                round_idx=round_idx,
                session_id=session_id,
            )
            used_llm_summary = True
            kb = (key_body or "").strip()
            if kb:
                key_round_chunks.append(kb)
            cur_key = _upsert_compress_summary_key_context(
                session_id, "\n\n".join(key_round_chunks)
            )
            work, recap = _merge_summary_into_work(summary, micro_prefix, tail)
            if recap:
                new_recap_text = recap
            _push_progress_hint(
                hints,
                hint_sink,
                f"【上下文摘要】完成 {round_idx} 轮历史摘要；完成关键 信息、经验与结论 的记录",
                kind="summary",
                session_id=session_id,
                preview_llm_history=_preview_llm_for_ui_estimate(work),
                key_context=cur_key,
            )

        new_key = cur_key

        if not _compress_ratio_reached(session_id, work, new_key):
            fb, _ok, did_trunc = compress_tail_fallback(
                _with_marker_systems(snapshot_llm, work),
                reason="max_rounds",
            )
            logger.debug(
                "compress exit fallback: fp_final=%s cap_tokens=%s full_pack_entry=%s did_trunc=%s",
                _full_pack_tokens_compress_work(session_id, work, new_key),
                cap_tokens,
                int(full_pack),
                did_trunc,
            )
            if fallback_reason == "no_prefix":
                fb_hint = (
                    "【上下文摘要】可摘要历史不足，已丢弃更早对话（保留至多约半窗 token 的尾部）。"
                    if did_trunc
                    else "【上下文摘要】可摘要历史不足；对话已在半窗预算内未再截断。"
                )
            else:
                if did_trunc:
                    fb_hint = (
                        f"【上下文摘要】摘要轮次已用尽（已尝试 {round_idx}/{max_summary_rounds} 轮），"
                        "已丢弃更早对话（保留至多约半窗 token 的尾部）。"
                    )
                else:
                    fb_hint = f"【上下文摘要】摘要轮次已用尽（已尝试 {round_idx}/{max_summary_rounds} 轮）；对话已在半窗预算内未再截断。"
            _push_progress_hint(
                hints,
                hint_sink,
                fb_hint,
                kind="summary",
                session_id=session_id,
                preview_llm_history=list(fb),
                key_context=new_key,
            )
            return fb, new_key, True, hints, used_llm_summary, new_recap_text

        return _preview_llm_for_ui_estimate(work), new_key, True, hints, used_llm_summary, new_recap_text
    except Exception as e:
        logger.exception("压缩流程失败，启用异常兜底: %s", e)
        fb, _chg, _ = compress_tail_fallback(snapshot_llm, reason="failure")
        _push_progress_hint(
            hints,
            hint_sink,
            "【上下文摘要】流程异常，已切换为失败兜底截尾。",
            kind="summary",
            session_id=session_id,
            preview_llm_history=list(fb),
            key_context=key_context,
        )
        return fb, key_context, True, hints, False, None


def context_will_attempt_compress(
    llm_history: List,
    session_id: str,
    *,
    force_user_compact: bool,
    key_context: str = "",
) -> bool:
    """在可能进入慢路径（实际会改 llm_history）时为 True。"""
    _work, should_run, *_rest = _compress_entry_state(
        llm_history,
        session_id,
        force_user_compact=force_user_compact,
        key_context=key_context,
    )
    return should_run


def run_context_policy(
    llm_history: List,
    key_context: str,
    session_id: str,
    *,
    force_user_compact: bool,
    hint_sink: Optional[Callable[[Any], None]] = None,
) -> Tuple[List, str, bool, List[str], bool, Optional[str]]:
    l = list(llm_history)
    return _compress_unified_in_place(
        l,
        session_id,
        key_context,
        force_user_compact=force_user_compact,
        hint_sink=hint_sink,
    )
