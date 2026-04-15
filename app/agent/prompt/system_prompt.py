"""系统 Prompt 构建（简化版）

严格动静分离设计：
- build_static_system_prompt(): 纯静态部分，启动时计算，之后永远不变，可被 Cerebras 缓存
- build_dynamic_context(): 动态状态部分，每次请求生成，放入 User Prompt

静态 System Prompt 包含：
- 角色定义、语言风格
- 标签目录（动作/情绪列表）
- 输出格式规范

动态 User Prompt 包含：
- dynamic_context（PAD 状态 + 动力学趋势）
- memory_context（记忆）
- conversation_history（对话历史）

简化版本：
- 删除 motion_preferences 相关配置（数据库已有 action 标签约束）
- 删除 traits 字段（已在 personality.md 中描述）
"""

from __future__ import annotations

from app.agent.character.loader import CharacterConfig


def _build_tag_catalog() -> str:
    """构建动作和情绪标签目录（从 TagCatalogService 加载）"""
    from app.services.tag_catalog_service import get_tag_catalog_service
    
    tag_catalog = get_tag_catalog_service()
    
    # 获取标签列表
    action_tags = tag_catalog.get_actions()
    emotion_tags = tag_catalog.get_emotions()
    
    # 格式化
    action_list = "、".join(f"「{a}」" for a in action_tags) if action_tags else "无"
    emotion_list = "、".join(f"「{e}」" for e in emotion_tags) if emotion_tags else "无"
    
    return f"""## 可用动作标签（action）
{action_list}

## 可用情绪标签（emotion）
{emotion_list}

## 动作标签使用规则
- 使用格式：<a>{{"action": "动作名", "emotion": "情绪", "desp": "动作描述(20字以内)"}}</a>
- action 必须从"可用动作标签"列表中选择
- emotion 从"可用情绪标签"列表中选择，可为空字符串
- desp 是动作的自然语言描述，用于匹配具体动作
- 每个自然句最多 1 个动作标签（可以没有）
- 没有明显合适动作时，不要输出 <a> 标签"""


def _build_output_format_rules() -> str:
    """构建输出格式规范（纯静态规则，可被缓存）"""
    return """## 内置知识
- 当前时间已在对话上下文中提供，询问时间、日期、星期时无需调用工具
- 只有需要查询外部实时信息（天气、新闻、股票等）时才使用 tool_prompt

## 输出格式规范

### 完整输出模板
<t>{"tool_prompt": null, "emotion_delta":{"P":0.0,"A":0.0,"D":0.0}}</t>角色台词<m>{"trigger_keywords":["关键词"],"inner_monologue":"内心想法"}</m>

### 格式说明

1. **<t>标签**（必选，在开头）：
   - 内容为 JSON：`{"tool_prompt": ..., "emotion_delta": {...}}`
   - `tool_prompt`: 需要调用外部工具时填写任务描述，否则填 null
   - `emotion_delta`: 情绪变化量，范围 -1.0 到 1.0
      - P=愉悦度（正=愉悦，负=不悦）
      - A=激活度（正=兴奋，负=平静）
      - D=支配度（正=支配，负=顺从）
   - 示例：`<t>{"tool_prompt": null, "emotion_delta":{"P":0.0,"A":0.0,"D":0.0}}</t>`
   - emotion_delta 必须在 <t> 标签中输出，以便系统提前设置语音情绪

2. **角色台词**（在 <t> 和 <m> 之间）：
   - 直接输出1-3句台词文本，无需任何标签包裹
   - 若有动作标签，插入在台词开头
   - 示例：`今天天气真不错呢。` 或 `<a>{"action":"微笑","emotion":"开心","desp":"嘴角上扬"}</a>今天天气真不错呢。`
   - !!!特别重要，台词中不要出现任何其他的标签或格式，颜文字等,保持纯文本。否则会导致整个系统崩坏。

3. **<m>标签**（必选，在结尾）：
   - 内容为 JSON：`{"trigger_keywords":["关键词"],"inner_monologue":"内心想法"}`
   - `trigger_keywords`: 触发情绪变化的关键词数组
   - `inner_monologue`: 角色内心想法，100字内

### 完整示例

**普通对话**：
<t>{"tool_prompt": null, "emotion_delta":{"P":0.2,"A":0.1,"D":0.0}}</t>嗯，今天确实是个好天气呢。<a>{"action":"微笑","emotion":"愉悦","desp":"轻轻微笑"}</a>你有什么计划吗？<m>{"trigger_keywords":["好天气"],"inner_monologue":"难得这么好的天气，心情都变好了"}</m>

**需要工具调用**：
<t>{"tool_prompt": "查询北京今日天气情况", "emotion_delta":{"P":0.0,"A":0.0,"D":0.0}}</t>好的，正在为您查询天气。<m>{"trigger_keywords":[],"inner_monologue":"用户想知道天气，我需要查询一下"}</m>

### 重要规则
- 三个部分必须按顺序输出：<t>标签 → 台词 → <m>标签
- emotion_delta 必须在 <t> 标签中输出（不是 <m> 标签）
- 不要省略任何标签，tool_prompt 为空时也必须输出 null
- 台词部分不要添加任何额外标签或格式
- 不知道的事情不要编造，需要查询实时信息时使用 tool_prompt"""


def build_static_system_prompt(config: CharacterConfig) -> str:
    """
    构建完整静态 System Prompt（可被 Cerebras 自动缓存）

    此函数应在 Agent 初始化时调用一次，结果存储后永远不变。
    包含所有静态内容：角色定义、标签目录、输出格式。

    Args:
        config: 角色配置

    Returns:
        完整的静态 system prompt 字符串
    """
    rules = "\n".join(f"- {r}" for r in config.core_rules) if config.core_rules else ""
    
    # 获取说话风格
    style = config.speaking_style
    if style:
        口头禅 = "、".join(f"「{w}」" for w in style.口头禅) if style.口头禅 else "无"
        patterns = "\n".join(f"  - {p}" for p in style.sentence_patterns) if style.sentence_patterns else "无特别要求"
        forbidden = "\n".join(f"  - {f}" for f in style.forbidden) if style.forbidden else "无特别禁忌"
        tone = style.tone or "自然、友好"
    else:
        口头禅 = "无"
        patterns = "无特别要求"
        forbidden = "无特别禁忌"
        tone = "自然、友好"
    
    # 获取性格描述（从 personality.md 加载）
    personality_text = config.personality_text or ""
    
    # 构建情绪特点部分
    emotion_section = ""
    if config.emotion_traits_text:
        emotion_section = f"\n## 情绪特点\n{config.emotion_traits_text}\n"
    if config.emotion_triggers_text:
        emotion_section += f"\n## 敏感词汇\n{config.emotion_triggers_text}\n"

    tag_catalog = _build_tag_catalog()
    output_format_rules = _build_output_format_rules()

    # 构建核心规则部分
    core_rules_section = f"\n## 核心规则\n{rules}" if rules else ""

    prompt = f"""你是{config.name}。

## 角色介绍
{personality_text}
{emotion_section}
## 语言风格
语气：{tone}
口头禅：{口头禅}
说话方式：
{patterns}
禁忌：
{forbidden}

{tag_catalog}

{output_format_rules}
{core_rules_section}"""

    return prompt


def build_dynamic_context(emotion_service) -> str:
    """
    构建动态上下文（当前状态）
    
    此函数在每次请求时调用，结果放入 User Prompt 的开头。
    
    极简设计：
    - 只提供 PAD 数值和变化趋势
    - LLM 根据角色性格描述自主生成台词
    
    Args:
        emotion_service: EmotionService 实例或任何有 get_dynamic_context() 方法的对象
        
    Returns:
        动态上下文字符串
    """
    # 如果传入的是 EmotionService，直接使用其方法
    if hasattr(emotion_service, 'get_dynamic_context'):
        return emotion_service.get_dynamic_context()
    
    # 兼容旧接口：如果是 PADState 或有 p/a/d 属性的对象
    if hasattr(emotion_service, 'p'):
        return f"""## 当前情绪状态
愉悦度(P)={emotion_service.p:.2f} | 激活度(A)={emotion_service.a:.2f} | 支配度(D)={emotion_service.d:.2f}"""
    
    # 默认返回
    return """## 当前情绪状态
愉悦度(P)=0.00 | 激活度(A)=0.00 | 支配度(D)=0.00"""