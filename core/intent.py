"""
意图路由的纯函数工具。

对应 skill_aicoding/USER_INTENT_ROUTING.md：
- 确定性规则拦截明显无效输入（空 / 纯数字 / 无文字乱码），不交给模型；
- 兼容读取 LangGraph API 不同版本的消息对象；
- 为无效输入生成针对性确认话术，避免固定模板。

本模块为纯函数，无外部依赖，可独立测试。frontend/graph.py 与 main.py 共用。
"""

from __future__ import annotations

import re
from typing import Any

# 空 / 纯数字 / 无文字乱码 视为明显无效
_INVALID_RE = re.compile(r"[一-鿿A-Za-z]")


def _message_text(message: Any) -> str:
    """
    从单条消息对象提取文本 content，兼容 LangGraph API 不同版本：
    - langchain HumanMessage / AIMessage（.content）
    - 协议消息 dict（{"type":..., "content":...} 或 {"role":...}）
    - content 为字符串
    - content 为 content-blocks 列表（[{"type":"text","text":...}]）
    """
    if message is None:
        return ""
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        content = message.get("content", "")
    else:
        content = getattr(message, "content", "")

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") in {"text", "input_text"}:
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content) if content else ""


def _message_is_human(message: Any) -> bool:
    """判断消息是否为用户消息，兼容 HumanMessage / type=='human' / role=='user'。"""
    if isinstance(message, str):
        return False
    if isinstance(message, dict):
        msg_type = message.get("type") or message.get("role")
        return msg_type in {"human", "user"}
    if hasattr(message, "type") and getattr(message, "type") == "human":
        return True
    role = getattr(message, "role", None)
    return role in {"human", "user"}


def latest_human_text(messages: list) -> str:
    """
    读取最近一条用户消息的文本。兼容多种消息形态，避免升级 API 后读空。
    """
    for message in reversed(messages or []):
        if _message_is_human(message):
            return _message_text(message).strip()
    return ""


def is_obviously_invalid_input(text: str) -> bool:
    """
    仅拦截无法表达有效意图的输入：空、纯数字、无任何中文/英文的乱码。
    不要用关键词规则判断所有意图，否则"帮我看看"等依赖上下文的表达会被误伤。
    """
    if text is None:
        return True
    compact = re.sub(r"\s+", "", text)
    if not compact or compact.isdigit():
        return True
    return _INVALID_RE.search(compact) is None


def invalid_input_clarification(text: str) -> str:
    """
    为明显无效输入生成针对性确认话术（引用原话），避免固定模板的死板回复。
    """
    if text and text.strip():
        return (
            f"你刚才输入的是“{text.strip()}”，是不是输错了？"
            "你可以直接告诉我想聊什么，或者说明需要分析的地区和问题。"
        )
    return "你好像还没有输入具体内容。你可以直接告诉我想聊什么，或者说明需要分析的地区和问题。"
