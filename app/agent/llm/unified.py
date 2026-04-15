"""合并的内心独白 + 对外表达 LLM 调用（t标签 + m标签 + 流式台词）

严格动静分离：
- System Prompt: 完全静态（角色定义、标签目录、动作规则、输出格式）
- User Prompt: 只有动态内容（PAD状态、记忆、对话历史）

输出格式：
<t>{"tool_prompt": ...}</t>:角色台词<a>动作</a>:<m>{"emotion_delta":..., "trigger_keywords":[...], "inner_monologue":"..."}</m>

流式解析使用状态机：
- NORMAL: 正常文本输出
- MAYBE_TAG: 检测到 '<'，等待确认是否为有效标签
- IN_TAG: 确认进入标签，正在缓存内容
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncGenerator
from enum import Enum

from app.agent.llm.client import LLMClient
from app.agent.prompt.unified_prompt import build_unified_prompt

logger = logging.getLogger(__name__)

_DEFAULT_META = {
    "emotion_delta": {"P": 0.0, "A": 0.0, "D": 0.0},
    "motion": None,
    "trigger_keywords": [],
    "inner_monologue": "",
    "tool_prompt": None,
}


class ParserState(Enum):
    """解析器状态"""
    NORMAL = "normal"           # 正常文本输出
    MAYBE_TAG = "maybe_tag"     # 检测到 '<'，等待确认
    IN_TAG = "in_tag"           # 确认在标签内，缓存内容


# 有效的标签类型
VALID_TAGS = {'t', 'a', 'm'}


def _parse_t_tag(text: str) -> dict:
    """从 <t> 标签内容中解析 tool_prompt 和 emotion_delta
    
    Args:
        text: <t> 标签内的内容，预期是 JSON 格式 {"tool_prompt": ..., "emotion_delta": {...}}
        
    Returns:
        包含 tool_prompt 和 emotion_delta 的字典
    """
    text = text.strip()
    logger.debug("[T标签] 解析内容: %s", text[:200])
    
    result = {
        "tool_prompt": None,
        "emotion_delta": {"P": 0.0, "A": 0.0, "D": 0.0},
    }
    
    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 尝试提取 JSON 块
        json_match = re.search(r'\{(?:[^{}]|\{[^{}]*\})*\}', text)
        if json_match:
            try:
                data = json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
    
    if not isinstance(data, dict):
        logger.warning("[T标签] JSON 解析失败，使用默认值。原文: %s", text[:200])
        return result
    
    # 解析 tool_prompt
    tool_prompt = data.get("tool_prompt")
    if tool_prompt is not None and not isinstance(tool_prompt, str):
        tool_prompt = str(tool_prompt)
    result["tool_prompt"] = tool_prompt.strip() if tool_prompt and isinstance(tool_prompt, str) else None
    
    # 解析 emotion_delta
    emotion_delta = data.get("emotion_delta", {})
    if isinstance(emotion_delta, dict):
        for k in ("P", "A", "D"):
            if k in emotion_delta:
                try:
                    result["emotion_delta"][k] = float(emotion_delta[k])
                except (ValueError, TypeError):
                    result["emotion_delta"][k] = 0.0
    
    logger.info(
        "[T标签] 解析结果: tool_prompt=%s, emotion_delta=%s",
        result["tool_prompt"], result["emotion_delta"]
    )
        
    return result


def _parse_a_tag(text: str) -> str | None:
    """从 <a> 标签内容中解析动作信息
    
    Args:
        text: <a> 标签内的内容，预期是 JSON 格式
        
    Returns:
        action_name 字符串或 None
    """
    text = text.strip()
    logger.debug("[A标签] 解析内容: %s", text[:200])
    
    # 直接返回内容（可能是 JSON 或纯文本）
    if text:
        logger.info("[A标签] 提取到动作: %s", text)
        return text
    
    return None


def _parse_m_tag(text: str) -> dict:
    """从 <m> 标签内容中解析 trigger_keywords 和 inner_monologue
    
    ⭐ 注意：emotion_delta 已移至 <t> 标签，<m> 标签不再包含 emotion_delta
    
    Args:
        text: <m> 标签内的内容，预期是 JSON 格式 {"trigger_keywords": [...], "inner_monologue": "..."}
        
    Returns:
        包含 trigger_keywords, inner_monologue 的字典
    """
    text = text.strip()
    logger.debug("[M标签] 解析内容: %s", text[:200])
    
    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 尝试 raw_decode 只解析第一个 JSON 值
        try:
            decoder = json.JSONDecoder()
            data, _ = decoder.raw_decode(text)
        except (json.JSONDecodeError, ValueError):
            pass
        # 尝试提取 JSON 块
        if data is None:
            match = re.search(r'\{(?:[^{}]|\{[^{}]*\})*\}', text)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    pass

    if not isinstance(data, dict):
        logger.warning("[M标签] JSON 解析失败，使用默认值。原文: %s", text[:200])
        result = {
            "trigger_keywords": [],
            "inner_monologue": "",
        }
        logger.info("[M标签] 解析结果(默认): %s", result)
        return result

    if not isinstance(data.get("trigger_keywords"), list):
        data["trigger_keywords"] = []

    if "inner_monologue" not in data:
        data["inner_monologue"] = ""

    result = {
        "trigger_keywords": data["trigger_keywords"],
        "inner_monologue": data["inner_monologue"],
    }
    logger.info("[M标签] 解析结果: trigger_keywords=%s, inner_monologue=%s",
                result["trigger_keywords"], 
                result["inner_monologue"][:50] + "..." if len(result["inner_monologue"]) > 50 else result["inner_monologue"])
    
    return result


def _parse_meta(text: str) -> dict:
    """从 meta 标签内容中解析 JSON（保留用于向后兼容）"""
    text = text.strip()
    meta = None
    try:
        meta = json.loads(text)
    except json.JSONDecodeError:
        # 尝试 raw_decode 只解析第一个 JSON 值
        try:
            decoder = json.JSONDecoder()
            meta, _ = decoder.raw_decode(text)
        except (json.JSONDecodeError, ValueError):
            pass
        # 尝试提取 JSON 块
        if meta is None:
            match = re.search(r'\{(?:[^{}]|\{[^{}]*\})*\}', text)
            if match:
                try:
                    meta = json.loads(match.group())
                except json.JSONDecodeError:
                    pass

    if not isinstance(meta, dict):
        logger.warning("meta JSON 解析失败，使用默认值。原文: %s", text[:200])
        return dict(_DEFAULT_META)

    # 确保 emotion_delta 完整
    delta = meta.get("emotion_delta", {})
    for k in ("P", "A", "D"):
        if k not in delta:
            delta[k] = 0.0
        delta[k] = float(delta[k])
    meta["emotion_delta"] = delta

    if not isinstance(meta.get("trigger_keywords"), list):
        meta["trigger_keywords"] = []

    if "inner_monologue" not in meta:
        meta["inner_monologue"] = ""

    tool_prompt = meta.get("tool_prompt")
    if not isinstance(tool_prompt, str) or not tool_prompt.strip():
        meta["tool_prompt"] = None
    else:
        meta["tool_prompt"] = tool_prompt.strip()
        logger.info("提取到 tool_prompt: %s", meta["tool_prompt"])

    meta["motion"] = None

    return meta


# 容错正则模式：兼容 <t>、:t>、t> 等变体
# 开头可以是 < 或 : 或无符号，结尾可以是 > 或无符号
_T_TAG_PATTERN = re.compile(r'[:<]?t>?(.*?)</t>', re.DOTALL)
_M_TAG_PATTERN = re.compile(r'[:<]?m>?(.*?)</m>', re.DOTALL)
_META_TAG_PATTERN = re.compile(r'[:<]?meta>?(.*?)</meta>', re.DOTALL)


def parse_full_response(text: str) -> dict:
    """从完整文本中解析 t标签 + 台词 + m标签，用于非流式模式
    
    新格式: <t>{"tool_prompt":...}</t>:台词:<m>{"emotion_delta":...}</m>
    旧格式: <meta>{"emotion_delta":...,"tool_prompt":...}</meta>台词
    
    容错支持：
    - <t>、:t>、t> 等开标签变体
    - <m>、:m>、m> 等开标签变体
    
    Returns:
        包含所有元数据和 expression 的字典
    """
    logger.info("[解析] 完整响应文本(前500字符): %s", text[:500])
    
    result = {
        "emotion_delta": {"P": 0.0, "A": 0.0, "D": 0.0},
        "motion": None,
        "trigger_keywords": [],
        "inner_monologue": "",
        "tool_prompt": None,
        "expression": "",
    }
    
    # 尝试新格式: <t>...</t> 和 <m>...</m>（使用容错正则）
    t_match = _T_TAG_PATTERN.search(text)
    m_match = _M_TAG_PATTERN.search(text)
    
    logger.debug("[解析] T标签匹配: %s", "找到" if t_match else "未找到")
    logger.debug("[解析] M标签匹配: %s", "找到" if m_match else "未找到")
    
    if t_match:
        t_data = _parse_t_tag(t_match.group(1))
        result["tool_prompt"] = t_data.get("tool_prompt")
        result["emotion_delta"] = t_data.get("emotion_delta", {"P": 0.0, "A": 0.0, "D": 0.0})
        logger.info("[解析] T标签位置: %d-%d", t_match.start(), t_match.end())
    
    if m_match:
        m_data = _parse_m_tag(m_match.group(1))
        # ⭐ emotion_delta 已在 t 标签中处理，不再从 m 标签获取
        result["trigger_keywords"] = m_data["trigger_keywords"]
        result["inner_monologue"] = m_data["inner_monologue"]
        logger.info("[解析] M标签位置: %d-%d", m_match.start(), m_match.end())
    
    # 提取台词（两个标签之间的内容，去除 <a> 标签）
    if t_match and m_match:
        # 台词在 </t> 和 <m> 之间
        t_end = t_match.end()
        m_start = m_match.start()
        expression = text[t_end:m_start]
        # 去除前后的冒号和空白
        expression = expression.strip()
        if expression.startswith(':'):
            expression = expression[1:].lstrip()
        if expression.endswith(':'):
            expression = expression[:-1].rstrip()
    elif t_match:
        # 只有 t 标签，台词在 </t> 之后
        expression = text[t_match.end():].strip()
        if expression.startswith(':'):
            expression = expression[1:].lstrip()
    elif m_match:
        # 只有 m 标签，台词在 <m> 之前（从开头到 <m>）
        expression = text[:m_match.start()].strip()
        if expression.endswith(':'):
            expression = expression[:-1].rstrip()
    else:
        # 回退到旧格式: <meta>...</meta>
        meta_match = re.search(r'<meta>(.*?)</meta>', text, re.DOTALL)
        if meta_match:
            logger.info("[解析] 使用旧格式 <meta> 标签")
            meta = _parse_meta(meta_match.group(1))
            result["emotion_delta"] = meta["emotion_delta"]
            result["trigger_keywords"] = meta["trigger_keywords"]
            result["inner_monologue"] = meta["inner_monologue"]
            result["tool_prompt"] = meta.get("tool_prompt")
            expression = text[meta_match.end():].strip()
        else:
            logger.warning("[解析] 未找到任何标签，整个文本作为台词")
            expression = text.strip()
    
    # 去除台词中的 <a> 标签
    expression = re.sub(r'<a>.*?</a>', '', expression, flags=re.DOTALL).strip()
    result["expression"] = expression or "……"
    
    logger.info("[解析] 最终结果: tool_prompt=%s, emotion_delta=%s, expression=%s",
                result["tool_prompt"], result["emotion_delta"], 
                result["expression"][:50] + "..." if len(result["expression"]) > 50 else result["expression"])
    
    return result


async def generate_unified(
    llm_client: LLMClient,
    system_prompt: str,
    memory_context: str,
    *,
    dynamic_context: str = "",
    conversation_history: str = "",
) -> dict:
    """非流式：单次 LLM 调用同时生成 meta + 台词

    Args:
        llm_client: LLM 客户端
        system_prompt: 静态系统提示词（初始化时构建，之后不变）
        memory_context: 动态记忆上下文
        dynamic_context: 动态 PAD 状态
        conversation_history: 动态对话历史

    Returns:
        包含 meta 信息和 expression 的字典
    """
    user_prompt = build_unified_prompt(
        memory_context,
        dynamic_context=dynamic_context,
        conversation_history=conversation_history,
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        content = await llm_client.chat(messages, temperature=0.8)
        logger.info("[LLM] 原始输出: %s", content[:500] if len(content) > 500 else content)
        return parse_full_response(content)
    except Exception as e:
        logger.warning("统一生成失败，使用默认值: %s", e)
        result = dict(_DEFAULT_META)
        result["expression"] = "……"
        return result


async def generate_unified_stream(
    llm_client: LLMClient,
    system_prompt: str,
    memory_context: str,
    *,
    dynamic_context: str = "",
    conversation_history: str = "",
) -> AsyncGenerator[str | dict, None]:
    """
    流式输出（状态机解析方案）
    
    ⭐ 核心改进：使用状态机逐字符解析，确保标签内容不被分段输出到 TTS。

    Args:
        llm_client: LLM 客户端
        system_prompt: 静态系统提示词（初始化时构建，之后不变）
        memory_context: 动态记忆上下文
        dynamic_context: 动态 PAD 状态
        conversation_history: 动态对话历史

    Yields:
        文本片段或字典:
        - str  台词片段（优先输出）
        - {"type": "action", "action_name": str, "trigger_char": str}  动作触发
        - {"type": "emotion_delta", "emotion_delta": dict}  ⭐ 解析完 <t> 标签时立即输出（用于 TTS 情绪设置）
        - {"type": "tool_prompt", "tool_prompt": str|None}  解析完 <t> 标签时
        - {"type": "meta", ...}  解析完 <m> 标签时（流结束时）

    状态机解析设计：
    - NORMAL: 正常文本输出
    - MAYBE_TAG: 检测到 '<'，等待确认是否为有效标签（t/a/m）
    - IN_TAG: 确认进入标签，缓存内容直到标签尾
    
    状态转换：
    - NORMAL + '<' → MAYBE_TAG（保留 '<'，不输出）
    - MAYBE_TAG + 't'/'a'/'m' → 继续等待
    - MAYBE_TAG + '>' → IN_TAG（确认标签头）
    - MAYBE_TAG + 其他 → NORMAL（输出保留的字符）
    - IN_TAG + '</tag>' → 解析标签，回到 NORMAL
    """
    import time
    
    # 构建 prompt 并计时
    t_prompt = time.monotonic()
    user_prompt = build_unified_prompt(
        memory_context,
        dynamic_context=dynamic_context,
        conversation_history=conversation_history,
    )
    logger.info(f"[Timing] build_unified_prompt: {(time.monotonic()-t_prompt)*1000:.0f}ms")
    logger.info(f"[Timing] user_prompt 长度: {len(user_prompt)} chars")
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    # ⭐ 状态机变量
    state = ParserState.NORMAL
    pending_chars = ""              # 待确认的字符（如 "<t"）
    current_tag: str | None = None  # 当前标签类型（'t', 'a', 'm'）
    tag_buffer = ""                 # 标签内容缓存
    
    # 待触发的动作
    pending_action_name: str | None = None
    
    # T 标签缓存（延迟解析，流结束后输出 tool_prompt）
    cached_t_content: str = ""
    # ⭐ T 标签解析结果（立即输出 emotion_delta）
    pending_emotion_delta: dict | None = None
    
    # M 标签缓存（延迟解析，流结束后输出）
    cached_m_content: str = ""
    
    # 用于日志的原始输出收集
    raw_output_chunks = []
    
    # 输出缓冲（用于合并连续的普通字符）
    output_buffer = ""

    def _flush_output():
        """输出缓冲区的内容"""
        nonlocal output_buffer, pending_action_name
        if not output_buffer:
            return
        
        # 去除前导冒号（标签分隔符）
        text = output_buffer
        if text.startswith(':'):
            text = text[1:]
        text = text.rstrip(':')
        
        if not text:
            output_buffer = ""
            return
        
        # 如果有待触发动作，先输出动作字典
        if pending_action_name:
            trigger_char = None
            for ch in text:
                if not ch.isspace():
                    trigger_char = ch
                    break
            if trigger_char:
                yield {
                    "type": "action",
                    "action_name": pending_action_name,
                    "trigger_char": trigger_char,
                }
                pending_action_name = None
        
        yield text
        output_buffer = ""

    def _process_char(ch: str):
        """处理单个字符（状态机核心逻辑）"""
        nonlocal state, pending_chars, current_tag, tag_buffer, output_buffer
        nonlocal cached_t_content, cached_m_content, pending_action_name
        
        if state == ParserState.NORMAL:
            if ch == '<':
                # 可能是标签开始，保留字符，进入 MAYBE_TAG
                pending_chars = '<'
                state = ParserState.MAYBE_TAG
            else:
                # 正常字符，加入输出缓冲
                output_buffer += ch
        
        elif state == ParserState.MAYBE_TAG:
            pending_chars += ch
            
            # 检查是否形成有效标签头
            if len(pending_chars) == 2:
                # 检查第二个字符是否为有效标签类型
                second_char = pending_chars[1]
                if second_char in VALID_TAGS:
                    # 可能是 <t, <a, <m，继续等待
                    pass
                elif second_char == '/':
                    # 可能是标签尾 </t>, </a>, </m>，但这应该在 IN_TAG 状态
                    # 如果在 MAYBE_TAG 遇到 '</'，说明之前漏掉了标签头
                    # 作为普通文本输出
                    output_buffer += pending_chars
                    pending_chars = ""
                    state = ParserState.NORMAL
                else:
                    # 不是有效标签，输出保留的字符
                    output_buffer += pending_chars
                    pending_chars = ""
                    state = ParserState.NORMAL
            
            elif len(pending_chars) == 3:
                # 检查是否为完整标签头 <t>, <a>, <m>
                if pending_chars in ['<t>', '<a>', '<m>']:
                    # 确认进入标签
                    current_tag = pending_chars[1]  # 't', 'a', 'm'
                    tag_buffer = ""
                    pending_chars = ""
                    state = ParserState.IN_TAG
                    logger.debug("[状态机] 进入 <%s> 标签", current_tag)
                elif pending_chars.startswith('</'):
                    # 标签尾在 MAYBE_TAG 状态，不正常，输出
                    output_buffer += pending_chars
                    pending_chars = ""
                    state = ParserState.NORMAL
                else:
                    # 继续等待（可能是 <t{ 或 <tx 等情况）
                    pass
            
            elif len(pending_chars) >= 4:
                # 超过 3 个字符仍未确认标签头，检查是否有效
                # 可能是 <t{（标签内容紧接）或无效标签
                if pending_chars[:3] in ['<t>', '<a>', '<m>']:
                    # 标签头已确认，但内容紧接（如 "<t>{"）
                    current_tag = pending_chars[1]
                    # 后面的字符属于标签内容
                    tag_buffer = pending_chars[3:]
                    pending_chars = ""
                    state = ParserState.IN_TAG
                    logger.debug("[状态机] 进入 <%s> 标签（内容紧接）", current_tag)
                else:
                    # 不是有效标签头，输出保留的字符
                    output_buffer += pending_chars
                    pending_chars = ""
                    state = ParserState.NORMAL
        
        elif state == ParserState.IN_TAG:
            # 在标签内，缓存所有内容
            tag_buffer += ch
            
            # 检查是否遇到标签尾
            tag_end = f"</{current_tag}>"
            if tag_buffer.endswith(tag_end):
                # 标签结束，解析内容
                content = tag_buffer[:-len(tag_end)]
                logger.debug("[状态机] <%s> 标签结束，内容: %s", current_tag, content[:100])
                
                # 根据标签类型处理
                if current_tag == 't':
                    cached_t_content = content
                    # ⭐ 立即解析 T 标签，设置 pending_emotion_delta（主循环会检查并输出）
                    t_data = _parse_t_tag(content)
                    pending_emotion_delta = t_data.get("emotion_delta", {"P": 0.0, "A": 0.0, "D": 0.0})
                    logger.info("[状态机] T标签完成，emotion_delta=%s（立即输出）", pending_emotion_delta)
                elif current_tag == 'a':
                    action_name = _parse_a_tag(content)
                    if action_name:
                        if pending_action_name:
                            logger.warning("[状态机] 动作标签未匹配触发字，已被覆盖: %s", pending_action_name)
                        pending_action_name = action_name
                        logger.info("[状态机] 提取动作标签: %s", action_name)
                elif current_tag == 'm':
                    cached_m_content = content
                    logger.debug("[状态机] M标签内容已缓存")
                
                # 重置状态
                tag_buffer = ""
                current_tag = None
                state = ParserState.NORMAL

    async for chunk in llm_client.chat_stream(messages, temperature=0.8):
        raw_output_chunks.append(chunk)
        
        # 逐字符处理（_process_char 只修改状态，不返回值）
        for ch in chunk:
            _process_char(ch)
        
        # ⭐ 检查是否需要立即输出 emotion_delta（T 标签完成时设置）
        if pending_emotion_delta is not None:
            yield {
                "type": "emotion_delta",
                "emotion_delta": pending_emotion_delta,
            }
            logger.info("[流式] 已输出 emotion_delta 事件: %s", pending_emotion_delta)
            pending_emotion_delta = None  # 清空，只输出一次
            # ⭐ T 标签刚完成，立即刷新已有台词到 TTS（不等后续 chunk 凑阈值）
            if output_buffer:
                logger.info("[流式] T标签完成，立即推送台词缓冲区，长度=%d", len(output_buffer))
                for item in _flush_output():
                    yield item

        # 后续台词：NORMAL 状态且有内容就推送
        elif state == ParserState.NORMAL and output_buffer:
            for item in _flush_output():
                yield item

    # 记录原始输出
    raw_output = "".join(raw_output_chunks)
    logger.info("[流式] LLM原始输出(前500字符): %s", raw_output[:500])

    # ⭐ 流结束后处理
    
    # 处理剩余状态
    if state == ParserState.MAYBE_TAG:
        # 未完成的标签头，输出保留的字符
        output_buffer += pending_chars
        pending_chars = ""
        state = ParserState.NORMAL
        logger.warning("[状态机] 未完成的标签头: %s", pending_chars)
    
    elif state == ParserState.IN_TAG:
        # 未完成的标签，缓存内容（等待解析）
        if current_tag == 't':
            cached_t_content = tag_buffer
        elif current_tag == 'm':
            cached_m_content = tag_buffer
        logger.warning("[状态机] 未完成的 <%s> 标签: %s", current_tag, tag_buffer[:100])
    
    # 输出剩余的普通文本
    if output_buffer:
        for item in _flush_output():
            yield item

    # 解析 T 标签并输出
    if cached_t_content:
        t_data = _parse_t_tag(cached_t_content)
        tool_prompt = t_data.get("tool_prompt")  # ⭐ 只取 tool_prompt 字段
        logger.info("[流式] T标签延迟解析完成，tool_prompt=%s", tool_prompt)
        yield {"type": "tool_prompt", "tool_prompt": tool_prompt}
    else:
        yield {"type": "tool_prompt", "tool_prompt": None}

    # 解析 M 标签并输出（⭐ 不再包含 emotion_delta）
    if cached_m_content:
        m_data = _parse_m_tag(cached_m_content)
        result = {
            "type": "meta",
            "trigger_keywords": m_data["trigger_keywords"],
            "inner_monologue": m_data["inner_monologue"],
            "motion": None,
            "tool_prompt": None,
        }
        logger.info("[流式] M标签解析完成: %s", result)
        yield result
    else:
        # 没有找到 M 标签，输出默认值
        logger.warning("[流式] 未找到 <m> 标签，使用默认值")
        yield {
            "type": "meta",
            "trigger_keywords": [],
            "inner_monologue": "",
            "motion": None,
            "tool_prompt": None,
        }
