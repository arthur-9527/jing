"""表达层 Prompt 模板"""

from __future__ import annotations


def build_expression_prompt(
    inner_monologue: str,
    suppressed_thought: str | None,
    feeling_about_user: str,
    speaking_style_desc: str,
) -> str:
    """
    构建对外表达 Prompt
    根据内心独白结果 + 语言风格，生成角色台词
    """
    suppressed = suppressed_thought or "（没有需要忍住的话）"

    prompt = f"""现在请你根据刚才的内心活动，生成你要对用户说的话。

## 你的内心独白
{inner_monologue}

## 你想说但忍住的话
{suppressed}

## 你对这个人的感觉
{feeling_about_user}

## 语言风格要求
{speaking_style_desc}

请直接输出你要说的话（角色台词），不需要 JSON 格式。要求：
1. 语言风格要符合角色设定，使用角色的语气和口头禅
2. 内心独白中的真实想法可以部分流露，但不要全部暴露
3. 忍住没说的话不要说出来，但可以通过语气暗示
4. 台词要自然，像是真人在对话
5. 长度适中，一般 1-3 句话"""

    return prompt
