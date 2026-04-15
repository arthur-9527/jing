"""第一次 LLM 调用：内心独白"""

from __future__ import annotations

import json
import logging

from app.agent.llm.client import LLMClient
from app.agent.prompt.monologue_prompt import build_monologue_prompt

logger = logging.getLogger(__name__)


async def generate_inner_monologue(
    llm_client: LLMClient,
    system_prompt: str,
    user_input: str,
    memory_context: str,
    conversation_history: str,
) -> dict:
    """
    调用 LLM 生成内心独白
    Returns:
        {
            "inner_monologue": str,
            "emotion_delta": {"P": float, "A": float, "D": float},
            "suppressed_thought": str | None,
            "feeling_about_user": str,
            "trigger_keywords": list[str]
        }
    """
    user_prompt = build_monologue_prompt(user_input, memory_context, conversation_history)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        result = await llm_client.chat_json(messages, temperature=0.7, use_fast=True)
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("内心独白 JSON 解析失败，使用默认值: %s", e)
        result = {
            "inner_monologue": "（内心平静）",
            "emotion_delta": {"P": 0.0, "A": 0.0, "D": 0.0},
            "suppressed_thought": None,
            "feeling_about_user": "一般",
            "trigger_keywords": [],
        }

    # 确保 emotion_delta 字段完整
    delta = result.get("emotion_delta", {})
    for k in ("P", "A", "D"):
        if k not in delta:
            delta[k] = 0.0
        delta[k] = float(delta[k])
    result["emotion_delta"] = delta

    # 确保 trigger_keywords 为列表
    if not isinstance(result.get("trigger_keywords"), list):
        result["trigger_keywords"] = []

    return result
