"""
与 OpenAI 多轮对话（system / user / assistant / tool）对齐的轻量消息类型。

设计目标
--------
- 会话 JSON 与 `_message_to_dict` / `_dict_to_message`（见 agent_harness）保持兼容
- 无第三方 Message 基类，便于静态分析与类型标注

类名约定
--------
UserMessage / SystemMessage / AssistantMessage / ToolMessage 与历史代码及落盘结构一致，请勿随意重命名。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class UserMessage:
    """用户单轮消息（对齐 OpenAI user role）。
    content 可为纯文本 str，或多模态内容数组（List[Dict]）。"""

    __slots__ = ("content", "metadata")

    def __init__(self, content: Any = "", metadata: Optional[Dict[str, Any]] = None):
        self.content = content or ""
        self.metadata = metadata if metadata is not None else {}

    def text(self) -> str:
        """提取可读文本；多模态场景下仅拼接 text 类型片段。"""
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            parts: List[str] = []
            for item in self.content:
                if isinstance(item, dict) and str(item.get("type", "")).lower() == "text":
                    t = item.get("text")
                    if isinstance(t, str) and t.strip():
                        parts.append(t.strip())
            return "\n".join(parts)
        return str(self.content)

    def is_multimodal(self) -> bool:
        return isinstance(self.content, list)


class SystemMessage:
    """系统提示或内部状态行（多轮中可能映射为带前缀的 user，由 agent_loop 决定）。"""

    __slots__ = ("content",)

    def __init__(self, content: str = ""):
        self.content = content


class AssistantMessage:
    """助手消息（对齐 OpenAI assistant role）：可含 tool_calls 与 additional_kwargs（如 reasoning_content）。"""

    __slots__ = ("content", "tool_calls", "metadata", "additional_kwargs")

    def __init__(
        self,
        content: str = "",
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        additional_kwargs: Optional[Dict[str, Any]] = None,
    ):
        self.content = content or ""
        self.tool_calls = tool_calls
        self.metadata = metadata or {}
        self.additional_kwargs = additional_kwargs or {}

    def model_copy(self, *, update: Dict[str, Any]) -> "AssistantMessage":
        """浅拷贝并合并 update。"""
        content = update.get("content", self.content)
        tool_calls = update.get("tool_calls", self.tool_calls)
        metadata = dict(update.get("metadata", self.metadata))
        additional_kwargs = dict(update.get("additional_kwargs", self.additional_kwargs))
        return AssistantMessage(
            content=content,
            tool_calls=tool_calls,
            metadata=metadata,
            additional_kwargs=additional_kwargs,
        )


class ToolMessage:
    """与 assistant.tool_calls 中某 id 对应的工具返回。"""

    __slots__ = ("content", "tool_call_id")

    def __init__(self, content: str = "", tool_call_id: str = ""):
        self.content = content
        self.tool_call_id = tool_call_id
