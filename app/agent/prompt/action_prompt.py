"""动作解析 Prompt 模板"""

from __future__ import annotations

from typing import Iterable


# 8维标签体系定义（与动作库上传侧值域保持一致）
TAG_SYSTEM_INFO = """
## 标签体系（8维，必须全部输出）
- action: 核心动作类型，如"挥手""走路""跳舞"（权重最高）
- emotion: 情绪标签，如"happy""sad""excited""calm""neutral""confident""shy""dreamy"
- intensity: 动作强度，如"low""medium""high""extreme"
- style: 动作风格，如"cute""cool""elegant""energetic""graceful""powerful""gentle""playful"
- scene: 场景类型，如"indoor""outdoor""stage""urban""nature""fantasy""studio"
- speed: 动作速度，如"slow""normal""fast""very_fast"
- rhythm: 节奏类型，如"steady""dynamic""syncopated""flowing""sharp"
- complexity: 复杂度，如"simple""moderate""complex"
- system: 系统标签，必须为"others"（用于筛选动作库）
"""


def _format_candidate_section(
    action_phrases: list[str],
    canonical_candidates_by_phrase: dict[str, list[str]] | None,
) -> str:
    if not canonical_candidates_by_phrase:
        return "- 未提供候选 canonical action；若无法确定则输出 null。"

    lines: list[str] = []
    for idx, phrase in enumerate(action_phrases, start=1):
        candidates = canonical_candidates_by_phrase.get(phrase) or []
        if candidates:
            candidate_text = "、".join(candidates)
        else:
            candidate_text = "（无候选，必要时输出 null）"
        lines.append(f"- {idx}. {phrase} -> {candidate_text}")
    return "\n".join(lines)


def _format_rule_list(actions: Iterable[str] | None, *, empty_text: str) -> str:
    values = [action for action in (actions or []) if action]
    if not values:
        return empty_text
    return "、".join(values)


def build_action_prompt(
    action_phrases: Iterable[str],
    pad_state: dict,
    expression_text: str,
    *,
    canonical_candidates_by_phrase: dict[str, list[str]] | None = None,
    blocked_actions: list[str] | None = None,
    preferred_actions: list[str] | None = None,
) -> str:
    """构建动作解析 Prompt。

    目标：把动作短语解析成结构化标签，用于动作匹配。
    """
    action_phrase_list = [phrase for phrase in action_phrases if phrase]
    actions_text = "\n".join(
        f"- {idx + 1}. {phrase}" for idx, phrase in enumerate(action_phrase_list)
    )
    candidate_text = _format_candidate_section(action_phrase_list, canonical_candidates_by_phrase)
    preferred_text = _format_rule_list(preferred_actions, empty_text="无")
    blocked_text = _format_rule_list(blocked_actions, empty_text="无")
    prompt = f"""你是动作解析器，只负责从动作短语中解析动作意图。

请根据以下信息输出 JSON（必须是严格 JSON，不要额外解释）：

## 当前情绪（PAD）
{pad_state}

## 角色已生成台词
{expression_text}

## 动作短语列表（按顺序）
{actions_text}

## 每个动作短语的 canonical action 候选
{candidate_text}

## 角色约束
优先动作：{preferred_text}
禁止动作：{blocked_text}

{TAG_SYSTEM_INFO}

输出格式（必须保持数组长度与顺序一致）：
{{
  "actions": [
    {{
      "action": "动作名或短语（可为 null）",
      "emotion": "happy|sad|angry|excited|calm|surprised|scared|neutral|shy|confident|dreamy",
      "intensity": "low|medium|high|extreme",
      "style": "风格标签",
      "scene": "indoor|outdoor|stage|urban|nature|fantasy|studio",
      "speed": "slow|normal|fast|very_fast",
      "rhythm": "steady|dynamic|syncopated|flowing|sharp",
      "complexity": "simple|moderate|complex",
      "system": "others",
      "duration": 0.0
    }}
  ]
}}

要求：
- 只输出 JSON，不要其他文本。
- 输出数组长度与输入动作短语数量必须一致，顺序必须一致。
- 若该动作不应触发，action 为 null，其他字段给默认值。
- 若提供了候选 canonical actions，action 优先直接从该候选列表中选择；除非都不合适，否则不要发明新动作名。
- 必须避开禁止动作；优先使用优先动作和候选中更自然、日常、轻量的表达。
- 所有8个标签维度必须填写，system 必须是 "others"。
"""
    return prompt
