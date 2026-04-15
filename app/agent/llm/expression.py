"""第二次 LLM 调用：对外表达"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from app.agent.llm.client import LLMClient
from app.agent.prompt.expression_prompt import build_expression_prompt
from app.agent.character.loader import CharacterConfig


def _build_expression_messages(
    system_prompt: str,
    config: CharacterConfig,
    inner_monologue: str,
    suppressed_thought: str | None,
    feeling_about_user: str,
) -> list[dict]:
    """构建表达生成的消息列表"""
    style = config.personality.speaking_style
    style_desc = (
        f"语气：{style.tone}\n"
        f"口头禅：{'、'.join(style.口头禅)}\n"
        f"说话方式：{'；'.join(style.sentence_patterns)}\n"
        f"禁忌：{'；'.join(style.forbidden)}"
    )

    user_prompt = build_expression_prompt(
        inner_monologue=inner_monologue,
        suppressed_thought=suppressed_thought,
        feeling_about_user=feeling_about_user,
        speaking_style_desc=style_desc,
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


async def generate_expression(
    llm_client: LLMClient,
    system_prompt: str,
    config: CharacterConfig,
    inner_monologue: str,
    suppressed_thought: str | None,
    feeling_about_user: str,
) -> str:
    """
    调用 LLM 生成对外表达（角色台词），非流式
    """
    messages = _build_expression_messages(
        system_prompt, config, inner_monologue, suppressed_thought, feeling_about_user,
    )
    return await llm_client.chat(messages, json_mode=False, temperature=0.9)


async def generate_expression_stream(
    llm_client: LLMClient,
    system_prompt: str,
    config: CharacterConfig,
    inner_monologue: str,
    suppressed_thought: str | None,
    feeling_about_user: str,
) -> AsyncGenerator[str, None]:
    """
    流式调用 LLM 生成对外表达（角色台词），逐块 yield 文本
    """
    messages = _build_expression_messages(
        system_prompt, config, inner_monologue, suppressed_thought, feeling_about_user,
    )
    async for chunk in llm_client.chat_stream(messages, temperature=0.9):
        yield chunk
