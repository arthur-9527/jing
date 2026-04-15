"""工具结果二次重写 Prompt 模板（简化版）

LLM 任务：分析 HTML + content，生成角色台词 + 动作标签
Panel 配置由程序处理，LLM 不需要输出 <panel> 标签
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.character.loader import CharacterConfig


def _build_motion_rules() -> str:
    """构建动作标签规则（参考主提示词 system_prompt.py）
    
    注意：motion_preferences 字段已从 CharacterConfig 中删除，
    此函数只输出 <a> 标签格式规范，不包含角色约束。
    """
    return """## 动作标签规则
- 格式：<a>{"action": "动作名", "emotion": "情绪", "desp": "描述(20字以内)"}</a>
- 正例：<a>{"action": "微笑", "emotion": "开心", "desp": "嘴角上扬"}</a>
- ❌ 禁止空标签：<a>[]</a>、<a>{}</a>、<a></a> 都是无效的，不要输出
- action 必须是有效的动作名（非空字符串），从可用动作标签中选择
- emotion 可为空字符串，但 action 不能为空
- desp 是动作的自然语言描述，20字以内
- 每个自然句最多 1 个动作标签，必须在句首
- 没有合适动作时，不要输出任何 <a> 标签（完全不输出，而不是输出空标签）"""


def build_tool_rewrite_prompt(
    user_input: str,
    tool_result: str,
    panel_html_content: str | None = None,
    config: "CharacterConfig | None" = None,
) -> str:
    """构建基于工具结果的二次重写 Prompt（简化版）
    
    Args:
        user_input: 用户原始输入（理解上下文）
        tool_result: 工具返回的文本内容（成功=结果，失败=错误信息）
        panel_html_content: 面板 HTML 内容（让 AI 理解展示的信息）
        config: 角色配置（用于动作规则）
    
    Returns:
        简化后的 Prompt 字符串
    """
    # 面板内容区块
    panel_section = ""
    if panel_html_content:
        panel_section = f"""
## 面板展示内容
以下是展示给用户的可视化面板内容，请理解其中的信息：
```
{panel_html_content}
```"""

    # 动作规则区块（不再依赖 config，直接输出规则）
    motion_section = f"\n{_build_motion_rules()}"

    return f"""基于工具返回结果，生成角色台词回复用户。

## 用户输入
{user_input}

## 工具返回结果
{tool_result}
{panel_section}
{motion_section}

请直接输出台词，要求：
- 可选输出 <a> 动作标签（句首）
- 台词 1-3 句，适合 TTS 播放
- 保持角色语言风格
- 自然整合工具结果，如工具失败可简洁说明原因"""