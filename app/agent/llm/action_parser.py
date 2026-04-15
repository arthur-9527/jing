"""专用动作解析 LLM 调用"""

from __future__ import annotations

import logging
from typing import Iterable

from app.agent.llm.client import LLMClient
from app.agent.prompt.action_prompt import build_action_prompt

logger = logging.getLogger(__name__)

# 8维标签值域（与 video_analysis_service 上传侧保持一致）
_VALID_INTENSITIES = {"low", "medium", "high", "extreme"}
_VALID_SCENES = {"indoor", "outdoor", "stage", "urban", "nature", "fantasy", "studio"}
_VALID_SPEEDS = {"slow", "normal", "fast", "very_fast"}
_VALID_RHYTHMS = {"steady", "dynamic", "syncopated", "flowing", "sharp"}
_VALID_COMPLEXITIES = {"simple", "moderate", "complex"}


def _normalize_action_item(item: dict) -> dict:
    """规范化动作解析结果为8维标签结构"""
    action = item.get("action")
    if action is not None and not isinstance(action, str):
        action = None

    emotion = item.get("emotion") or ""
    if not isinstance(emotion, str):
        emotion = ""

    intensity = item.get("intensity") if item.get("intensity") in _VALID_INTENSITIES else "medium"
    scene = item.get("scene") if item.get("scene") in _VALID_SCENES else "indoor"
    speed = item.get("speed") if item.get("speed") in _VALID_SPEEDS else "normal"
    rhythm = item.get("rhythm") if item.get("rhythm") in _VALID_RHYTHMS else "steady"
    complexity = item.get("complexity") if item.get("complexity") in _VALID_COMPLEXITIES else "simple"

    # system 必须是 "others"
    system = item.get("system", "others")
    if system != "others":
        system = "others"

    duration = item.get("duration", 0.0)
    try:
        duration = max(float(duration), 0.0)
    except (TypeError, ValueError):
        duration = 0.0

    return {
        "action": action.strip() if isinstance(action, str) and action.strip() else None,
        "emotion": emotion.strip() if isinstance(emotion, str) else "",
        "intensity": intensity,
        "style": item.get("style", "") or "",
        "scene": scene,
        "speed": speed,
        "rhythm": rhythm,
        "complexity": complexity,
        "system": system,
        "duration": duration,
    }


async def parse_action_intents(
    llm_client: LLMClient,
    *,
    action_phrases: Iterable[str],
    pad_state: dict,
    expression_text: str,
    canonical_candidates_by_phrase: dict[str, list[str]] | None = None,
    blocked_actions: list[str] | None = None,
    preferred_actions: list[str] | None = None,
    timeout: float | None = None,
    use_fast: bool = True,
) -> list[dict] | None:
    """解析动作短语为结构化意图列表（8维标签）。"""
    action_phrases = [p.strip() for p in action_phrases if isinstance(p, str) and p.strip()]
    if not action_phrases:
        return None

    prompt = build_action_prompt(
        action_phrases,
        pad_state,
        expression_text,
        canonical_candidates_by_phrase=canonical_candidates_by_phrase,
        blocked_actions=blocked_actions,
        preferred_actions=preferred_actions,
    )
    messages = [
        {"role": "system", "content": "你是严格 JSON 输出的动作解析器。"},
        {"role": "user", "content": prompt},
    ]

    try:
        result = await llm_client.chat_json(
            messages,
            temperature=0.2,
            use_fast=use_fast,
            timeout=timeout,
        )
    except Exception as e:
        logger.warning("动作解析失败: %s", e)
        return None

    if not isinstance(result, dict):
        return None
    actions = result.get("actions")
    if not isinstance(actions, list):
        return None

    normalized = [_normalize_action_item(item) for item in actions if isinstance(item, dict)]
    if not normalized:
        return None
    return normalized
