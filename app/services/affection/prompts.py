"""好感度评估 LLM Prompt 模板 - 三维社交关系模型（9级分层）"""

from typing import Dict

from app.services.affection.models import (
    AffectionState,
    AffectionLevelResult,
    AffectionDimension,
    DIMENSION_DESCRIPTIONS,
    DELTA_MIN,
    DELTA_MAX,
)


def build_affection_context_prompt(
    affection_state: AffectionState,
    level_result: AffectionLevelResult,
    pad_state: dict,
    pad_dynamics: dict,
    personality_text: str,
    emotion_traits_text: str,
) -> str:
    """构建动态好感度语境生成 Prompt（结构化输出：关系描述 + 动态提示词）

    让LLM根据当前情绪+好感度级别+人设，生成两部分内容：
    1. relationship_description: 角色此刻对用户的感受描述（注入 User Prompt）
    2. dynamic_prompt_hints: 角色在当前关系状态下对话的注意事项（注入 System Prompt）

    Args:
        affection_state: 当前三维好感度状态
        level_result: 三维好感度级别分类结果（9级制）
        pad_state: 当前PAD情绪状态 {"P": float, "A": float, "D": float}
        pad_dynamics: 情绪动力学 {"velocity": {...}, "acceleration": {...}, "intensity": float}
        personality_text: 角色人设描述（从 personality.md 加载）
        emotion_traits_text: 角色情绪特点（从 personality.md 加载）

    Returns:
        prompt 字符串
    """
    # 三维级别描述（9级标签）
    level_desc = level_result.to_context_string()

    # PAD状态描述
    p = pad_state.get("P", 0.0)
    a = pad_state.get("A", 0.0)
    d = pad_state.get("D", 0.0)

    # 动力学趋势
    intensity = pad_dynamics.get("intensity", 0.0) if pad_dynamics else 0.0
    trend = ""
    if intensity > 0.25:
        trend = "（情绪剧烈波动）"
    elif intensity > 0.15:
        trend = "（情绪有明显变化）"

    # 角色人设（精简版，只保留关键信息）
    personality_section = ""
    if personality_text:
        personality_section = f"""
## 角色人设
{personality_text[:500]}
"""

    emotion_section = ""
    if emotion_traits_text:
        emotion_section = f"""
## 情绪特点
{emotion_traits_text[:300]}
"""

    return f"""你是一个好感度语境生成助手。请根据角色人设、当前情绪状态和好感度级别，生成两部分内容。

{personality_section}
{emotion_section}

{level_desc}

当前情绪状态：
- 愉悦度(P)：{p:.2f}（正值=愉悦，负值=不悦）{trend}
- 激活度(A)：{a:.2f}（正值=兴奋，负值=平静）
- 支配度(D)：{d:.2f}（正值=支配，负值=顺从）

请生成以下两部分内容，以 JSON 格式输出：

1. **relationship_description**（关系描述，50字以内）：
   角色此刻对用户的感受和态度，要体现具体的级别标签（如"比较信任"、"很亲密"等）。
   这是给角色自己看的内心独白，用第一人称或第三人称均可。

2. **dynamic_prompt_hints**（动态提示词，100字以内）：
   基于当前好感度级别，给角色对话时的行为指引。包括：
   - 语气建议（如：热情/冷淡/礼貌/亲切）
   - 话题建议（适合聊什么，不适合聊什么）
   - 距离感（可以多亲近，保持多大距离）
   - 情绪基调（角色的主导情绪应如何表现）

要求：
1. 必须符合角色人设（性格、说话方式、情绪特点）
2. 严格依据给定的好感度级别标签，不要自行修改
3. 如果好感度较低，提示词应强调谨慎、疏离；较高则强调亲近、信任
4. 语言要自然、有温度，不要机械罗列数值

输出格式（严格 JSON）：
{{
    "relationship_description": "角色对用户的内心感受描述",
    "dynamic_prompt_hints": "对话时的行为指引和语气建议"
}}

请直接输出 JSON，不要加任何标签或说明。"""


def build_emotional_assessment_prompt(
    affection_state: AffectionState,
    inner_monologue: str,
    emotion_delta: dict,
    emotion_intensity: float,
    user_input: str,
    expression: str,
    personality_text: str = "",
    emotion_traits_text: str = "",
    emotion_triggers_text: str = "",
) -> str:
    """构建三维感性好感度评估 prompt（带角色人设）

    Args:
        affection_state: 当前三维好感度状态
        inner_monologue: 内心独白
        emotion_delta: 情绪变化 {"P": float, "A": float, "D": float}
        emotion_intensity: 情绪强度
        user_input: 用户说的话
        expression: 角色回复
        personality_text: 角色人设描述（可选）
        emotion_traits_text: 角色情绪特点（可选）
        emotion_triggers_text: 角色敏感词汇（可选）

    Returns:
        prompt 字符串
    """
    # 构建三维状态描述
    state_desc = affection_state.to_context_string()

    # 角色人设部分（如果有）
    personality_section = ""
    if personality_text:
        personality_section = f"""
## 角色人设
{personality_text[:500]}
"""

    emotion_traits_section = ""
    if emotion_traits_text:
        emotion_traits_section = f"""
## 情绪特点
{emotion_traits_text[:300]}
"""

    triggers_section = ""
    if emotion_triggers_text:
        triggers_section = f"""
## 敏感词汇
{emotion_triggers_text[:200]}
"""

    character_context = f"{personality_section}{emotion_traits_section}{triggers_section}"

    return f"""你是一个好感度评估助手。角色刚经历了一个情绪波动时刻，请评估这对角色对用户三维好感度的影响。
{character_context}
{state_desc}

情绪变化：
- P（愉悦度）变化：{emotion_delta.get('P', 0.0):.2f}
- A（激活度）变化：{emotion_delta.get('A', 0.0):.2f}
- D（支配度）变化：{emotion_delta.get('D', 0.0):.2f}
- 情绪强度：{emotion_intensity:.2f}

角色内心独白：
{inner_monologue}

用户说的话：
{user_input}

角色的回复：
{expression}

三维好感度定义：
- 信任 (trust)：可靠性、一致性、信守承诺。用户说到做到、保守秘密会增加信任；食言、欺骗会降低信任。
- 亲密 (intimacy)：情感交流深度、自我暴露。深度对话、分享内心会增加亲密；冷漠、敷衍会降低亲密。
- 尊重 (respect)：能力认可、价值观认同。认可角色能力、尊重角色选择会增加尊重；贬低、控制会降低尊重。

评估原则：
1. 仅评估感性好感度变化（直觉感受），各维度范围 [""" + f"{DELTA_MIN}" + """, """ + f"{DELTA_MAX}" + """]
2. 情绪波动越剧烈，好感度变化越明显
3. 根据互动内容判断主要影响哪个维度
4. 普通互动好感度变化应接近0
5. 考虑内心独白中反映的真实感受
6. 同一次互动可能影响多个维度，也可能只影响一个
7. 重要：必须结合角色人设和情绪特点进行评估：
   - 如果角色"不太容易生气"，则负面事件的好感度惩罚应较小
   - 如果角色对某些敏感词汇"极度敏感"，则这些词触发的变化应更大
   - 如果角色"很难哄好"，则正面安抚的好感度恢复应较慢

请返回JSON格式（直接输出JSON，不要加任何说明）：
{"trust_delta": 0.0, "intimacy_delta": 0.0, "respect_delta": 0.0, "reasoning": ""}

注意：trust_delta/intimacy_delta/respect_delta 必须是-5到5之间的数字，不能是单词或占位符。"""


def build_rational_assessment_prompt(
    affection_state: AffectionState,
    emotional_summaries: Dict[AffectionDimension, float],
    diary_content: str,
    heartbeat_events_summary: str = "",
    personality_text: str = "",
    emotion_traits_text: str = "",
) -> str:
    """构建三维理性好感度评估 prompt（日记时，带角色人设）

    Args:
        affection_state: 当前三维好感度状态
        emotional_summaries: 各维度今日感性总结值
        diary_content: 日记内容
        heartbeat_events_summary: 今日心动事件摘要
        personality_text: 角色人设描述（可选）
        emotion_traits_text: 角色情绪特点（可选）

    Returns:
        prompt 字符串
    """
    # 构建三维状态描述
    state_desc = affection_state.to_context_string()

    # 构建感性总结描述
    emotional_desc = "今日感性总结（各维度）："
    for dim in AffectionDimension:
        desc = DIMENSION_DESCRIPTIONS[dim]
        value = emotional_summaries.get(dim, 0.0)
        emotional_desc += f"\n  - {desc}: {value:.2f}"

    heartbeat_section = ""
    if heartbeat_events_summary:
        heartbeat_section = f"""
今日心动事件：
{heartbeat_events_summary}
"""

    # 角色人设部分（如果有）
    personality_section = ""
    if personality_text:
        personality_section = f"""
## 角色人设
{personality_text[:500]}
"""

    emotion_traits_section = ""
    if emotion_traits_text:
        emotion_traits_section = f"""
## 情绪特点
{emotion_traits_text[:300]}
"""

    character_context = f"{personality_section}{emotion_traits_section}"

    return f"""你是一个好感度评估助手。请根据角色今天的日记，评估三维理性好感度的变化。
{character_context}
{state_desc}

{emotional_desc}
{heartbeat_section}
日记内容：
{diary_content}

三维好感度定义：
- 信任 (trust)：可靠性、一致性、信守承诺。今日用户是否展现了可靠、一致的行为？
- 亲密 (intimacy)：情感交流深度、自我暴露。今日是否有深度交流或情感连接的时刻？
- 尊重 (respect)：能力认可、价值观认同。今日用户是否尊重了角色的选择和立场？

评估原则：
1. 理性好感度反映经过思考后的稳定判断，各维度范围 [{DELTA_MIN}, {DELTA_MAX}]
2. 感性总结已经计入，理性增量是额外思考后的调整
3. 考虑今日关系进展、深度交流、信任建立等
4. 单日理性变化通常较小，重大事件除外
5. 不同维度可能有不同的变化方向
6. 重要：必须结合角色人设进行评估：
   - 如果角色"情绪持久"，则今日的情绪事件影响应更持久
   - 如果角色对某些行为"敏感"，则相关事件影响应更大

请返回JSON格式（示例数值）：
{{
    "trust_delta": 0.0,
    "intimacy_delta": 0.0,
    "respect_delta": 0.0,
    "reasoning": "简短评估理由（可选）"
}}

注意：各维度 delta 范围必须在 [{DELTA_MIN}, {DELTA_MAX}] 之间。"""
