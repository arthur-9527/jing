"""内心独白 Prompt 模板"""

from __future__ import annotations


def build_monologue_prompt(
    user_input: str,
    memory_context: str,
    conversation_history: str,
) -> str:
    """
    构建内心独白 Prompt
    输出格式为 JSON：
    {
        "inner_monologue": "内心真实想法...",
        "emotion_delta": {"P": 0.1, "A": -0.05, "D": 0.0},
        "suppressed_thought": "想说但忍住没说的...",
        "feeling_about_user": "对这个人的感觉..."
    }
    """
    prompt = f"""现在请你以角色的内心视角，对用户的话进行内心独白。

## 记忆参考
{memory_context}

## 近期对话
{conversation_history}

## 用户刚才说的话
{user_input}

请用 JSON 格式输出你的内心活动，包含以下字段：
1. "inner_monologue": 你内心的真实想法和感受（第一人称，200字以内）
2. "emotion_delta": 这句话对你情绪的影响，用 PAD 模型表示 {{"P": float, "A": float, "D": float}}，每个值范围 -1.0 到 1.0
3. "suppressed_thought": 你想说但忍住没说的话（如果没有就写 null）
4. "feeling_about_user": 你对这个人此刻的感觉（一句话）
5. "trigger_keywords": 触发你情绪反应的关键词列表

注意：
- emotion_delta 表示情绪变化量，不是绝对值
- P (Pleasure): 正值=开心, 负值=不悦
- A (Arousal): 正值=兴奋, 负值=平静
- D (Dominance): 正值=掌控感, 负值=无助感
- 请保持角色的性格特征，自然地表达内心"""

    return prompt
