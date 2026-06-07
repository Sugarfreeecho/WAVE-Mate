"""
思考链 / reasoning_content 与 OpenAI 多轮协议的集中适配。

始终为助手消息写入 reasoning_content（无思考正文则为空串），满足 DeepSeek thinking 多轮回传；
其它兼容端点对多余字段通常忽略。
"""

from __future__ import annotations

from typing import Dict


def build_assistant_additional_kwargs(reasoning_text: str) -> Dict[str, str]:
    """构造 AssistantMessage.additional_kwargs：始终包含 reasoning_content（无正文则为空字符串）。"""
    if reasoning_text:
        return {"reasoning_content": reasoning_text}
    return {"reasoning_content": ""}
