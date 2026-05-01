"""动作处理模块 - 统一处理 A 标签解析、匹配、入队

职责：
1. 解析 <a>...</a> 标签，提取 action/emotion/desp
2. 验证 action 和 emotion 标签
3. 匹配动作文件
4. 调用 AgentService.queue_action_structs 入队
"""

import json
import re
import logging

from app.realtime.agent_service import get_agent_service
from app.services.tag_catalog_service import get_tag_catalog_service

logger = logging.getLogger(__name__)

# 正则提取 <a> 标签内容
_A_TAG_PATTERN = re.compile(r'<a>(.*?)</a>', re.DOTALL)

# 固定 trigger_context 长度
TRIGGER_CONTEXT_LENGTH = 5


def _parse_action_json(text: str) -> dict | None:
    """
    解析动作标签内的 JSON，支持多种策略。
    
    策略：
    1. 直接 json.loads
    2. raw_decode 只解析第一个 JSON
    3. 正则提取 JSON 块
    4. 尝试修复被截断的 JSON
    5. 尝试提取 action/emotion 字段
    """
    text = text.strip()
    if not text:
        return None
    
    # 策略1：直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # 策略2：raw_decode
    try:
        decoder = json.JSONDecoder()
        result, _ = decoder.raw_decode(text)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    
    # 策略3：正则提取 JSON 块
    try:
        match = re.search(r'\{(?:[^{}]|\{[^{}]*\})*\}', text)
        if match:
            return json.loads(match.group())
    except json.JSONDecodeError:
        pass
    
    # 策略4：修复被截断的 JSON
    try:
        truncated_match = re.search(r'(\{"[^"]*":\s*"[^"]*"[^}]*)$', text)
        if truncated_match:
            partial = truncated_match.group(1)
            return json.loads(partial + '"}')
    except (json.JSONDecodeError, ValueError):
        pass
    
    # 策略5：提取字段
    try:
        action_match = re.search(r'"action"\s*:\s*"([^"]+)"', text)
        if action_match:
            result = {"action": action_match.group(1)}
            emotion_match = re.search(r'"emotion"\s*:\s*"([^"]+)"', text)
            if emotion_match:
                result["emotion"] = emotion_match.group(1)
            else:
                result["emotion"] = ""
            desp_match = re.search(r'"desp"\s*:\s*"([^"]+)"', text)
            if desp_match:
                result["desp"] = desp_match.group(1)
            else:
                result["desp"] = ""
            return result
    except Exception:
        pass
    
    return None


def _extract_trigger_context(following_text: str) -> tuple[str, str]:
    """
    提取 trigger_context 和 trigger_char。
    
    Args:
        following_text: </a> 后的台词文本
    
    Returns:
        (trigger_context, trigger_char) - trigger_context 是前5个非空字符，trigger_char 是第一个字符
    """
    # 提取前5个非空字符
    chars = []
    for ch in following_text:
        if not ch.isspace():
            chars.append(ch)
            if len(chars) >= TRIGGER_CONTEXT_LENGTH:
                break
    
    trigger_context = "".join(chars)
    trigger_char = trigger_context[0] if trigger_context else ""
    
    return trigger_context, trigger_char


async def process_action(
    action_data: str,
    following_text: str,
) -> bool:
    """
    处理单个动作数据。
    
    Args:
        action_data: 原始 <a>...</a> 标签字符串
        following_text: </a> 后的台词文本（用于提取 trigger_context）
    
    Returns:
        是否成功处理
    """
    # 1. 提取 <a> 标签内容
    tag_match = _A_TAG_PATTERN.search(action_data)
    if not tag_match:
        logger.warning(f"[ActionProcessor] 未找到 <a> 标签: {action_data[:50]}")
        return False
    
    tag_content = tag_match.group(1).strip()
    if not tag_content:
        logger.warning(f"[ActionProcessor] 空动作标签")
        return False
    
    # 2. 解析 JSON
    action_json = _parse_action_json(tag_content)
    if not action_json:
        logger.warning(f"[ActionProcessor] JSON 解析失败: {tag_content[:50]}")
        return False
    
    action = action_json.get("action", "").strip()
    emotion = action_json.get("emotion", "").strip()
    desp = action_json.get("desp", "").strip()
    
    if not action:
        logger.warning(f"[ActionProcessor] action 为空")
        return False
    
    logger.info(f"[ActionProcessor] 解析: action={action}, emotion={emotion}, desp={desp}")
    
    # 3. 验证标签
    tag_catalog = get_tag_catalog_service()
    
    if action and not tag_catalog.validate_action(action):
        logger.warning(f"[ActionProcessor] 无效 action 标签: {action}")
        return False
    
    if emotion and not tag_catalog.validate_emotion(emotion):
        logger.warning(f"[ActionProcessor] 无效 emotion 标签: {emotion}，清空")
        emotion = ""
    
    # 4. 提取 trigger_context
    trigger_context, trigger_char = _extract_trigger_context(following_text)
    if not trigger_char:
        logger.warning(f"[ActionProcessor] 无法提取 trigger_char")
        return False
    
    logger.info(f"[ActionProcessor] trigger_context='{trigger_context}', trigger_char='{trigger_char}'")
    
    # 5. 匹配动作
    matched = await tag_catalog.match_motion_by_tags(
        action=action,
        emotion=emotion,
        desp=desp,
    )
    
    if not matched:
        logger.warning(f"[ActionProcessor] 未匹配到动作: action={action}, emotion={emotion}")
        return False
    
    logger.info(
        f"[ActionProcessor] 动作匹配成功: {matched['display_name']} "
        f"(score={matched['score']:.3f})"
    )
    
    # 6. 构建结构体并入队
    action_struct = {
        "action_name": json.dumps({"action": action, "emotion": emotion, "desp": desp}),
        "trigger_char": trigger_char,
        "matched_motion": matched,
    }
    
    try:
        agent_service = get_agent_service()
        await agent_service.queue_action_structs([action_struct])
        logger.info(f"[ActionProcessor] 动作已入队: {matched['display_name']}")
        return True
    except Exception as e:
        logger.error(f"[ActionProcessor] 入队失败: {e}")
        return False


async def process_actions_batch(
    action_events: list[dict],
    expression: str = "",  # ⭐ 保留参数但不再用于查找位置
) -> None:
    """
    批量处理动作数据（用于投机采样确认后）。
    
    ⭐ 改进：直接使用 action_events 中的 trigger_context，不再在 expression 中查找位置。
    
    Args:
        action_events: 动作事件数组，结构为 {"action_data": str, "trigger_context": str}
        expression: 完整台词文本（保留参数，用于日志等）
    """
    if not action_events:
        return
    
    logger.info(f"[ActionProcessor] 批量处理 {len(action_events)} 个动作")
    
    for event in action_events:
        action_data = event.get("action_data", "")
        trigger_context = event.get("trigger_context", "")
        
        if not action_data:
            continue
        
        # ⭐ 直接使用 trigger_context 作为 following_text
        # trigger_context 已经是提取好的后5个字
        await process_action(action_data, trigger_context)
    
    logger.info(f"[ActionProcessor] 批量处理完成")


# 全局实例
_action_processor = None


def get_action_processor():
    """获取动作处理器实例"""
    global _action_processor
    if _action_processor is None:
        # 当前只需提供函数，不需要实例状态
        _action_processor = True
    return _action_processor