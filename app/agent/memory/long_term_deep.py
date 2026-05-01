"""长期记忆深度检索模块（Deep Path）

完整流程：
  Step 1: 意图分析 (LLM) → intent + confidence
  Step 2: 结果检索 → 多源并行检索，合并排序
  Step 3: 字数裁剪 → 取高分结果，裁剪到 1000-2000 字
  Step 4: LLM 整合 → 结构化的整合记忆
  Step 5: 返回结构化结果

失败处理：
  - confidence < 0.6 → 返回 {"success": false, "reason": "意图分析置信度过低"}
  - 检索无结果 → 返回 {"success": false, "reason": "检索无匹配结果"}
  - 整合失败 → 返回 {"success": false, "reason": "整合失败"}
"""

from __future__ import annotations

import logging
import time

from app.agent.llm.client import LLMClient
from app.agent.memory.interfaces import LongTermMemoryResult
from app.agent.memory.intent_analyzer import analyze_intent, is_intent_valid, CONFIDENCE_THRESHOLD
from app.agent.memory.search_router import execute_search
from app.agent.memory.result_integrator import integrate_results

logger = logging.getLogger(__name__)


# ── 字数裁剪配置 ──────────────────────────────────────────────────────────────

MIN_CHARS = 1000  # 最小字数
MAX_CHARS = 2000  # 最大字数


# ── 核心函数 ──────────────────────────────────────────────────────────────────────

async def retrieve_long_term_memories_deep(
    llm_client: LLMClient,
    character_id: str,
    user_id: str,
    user_input: str,
    conversation_context: list[dict],
) -> LongTermMemoryResult:
    """
    长期记忆检索（Deep Path）
    
    流程:
    1. LLM 分析意图 → 返回检索策略 + confidence
       - confidence < 0.6 → 返回 {"success": false}
    2. 根据策略执行检索（并行）
       - 无结果 → 返回 {"success": false}
    3. 字数裁剪 → 取高分结果，裁剪到 1000-2000 字
    4. LLM 整合 → 结构化的整合记忆
    5. 返回结构化结果
    
    Args:
        llm_client: LLM 客户端
        character_id: 角色ID
        user_id: 用户ID
        user_input: 用户输入文本
        conversation_context: 对话上下文（Redis 中的历史消息）
    
    Returns:
        LongTermMemoryResult:
        - success=True: context 包含整合后的记忆（1000-2000字）
        - success=False: reason 说明失败原因
    
    示例：
        result = await retrieve_long_term_memories_deep(
            llm_client, "daji", "default_user",
            "去年我们去哪玩了?", []
        )
        
        if result.success:
            print(result.context)  # 整合后的记忆上下文
        else:
            print(result.reason)   # 失败原因
    """
    t0 = time.monotonic()
    
    logger.info(f"[长期记忆 Deep] 开始检索: user_input='{user_input}'")
    
    # ── Step 1: 意图分析 ─────────────────────────────────────────────────────────
    t_intent = time.monotonic()
    
    intent = await analyze_intent(llm_client, user_input, conversation_context)
    
    elapsed_intent = (time.monotonic() - t_intent) * 1000
    logger.info(f"[长期记忆 Deep] Step 1 意图分析: {elapsed_intent:.0f}ms, "
               f"confidence={intent.get('confidence', 0):.2f}")
    
    # 判断意图是否有效
    if not is_intent_valid(intent):
        elapsed_total = (time.monotonic() - t0) * 1000
        logger.warning(f"[长期记忆 Deep] 检索失败: 置信度 {intent.get('confidence', 0):.2f} < {CONFIDENCE_THRESHOLD}")
        
        return LongTermMemoryResult(
            success=False,
            confidence=intent.get("confidence", 0.0),
            intent=intent,
            reason=f"意图分析置信度过低 ({intent.get('confidence', 0):.2f} < {CONFIDENCE_THRESHOLD})",
        )
    
    # ── Step 2: 结果检索 ─────────────────────────────────────────────────────────
    t_search = time.monotonic()
    
    # 传递用户原始输入用于生成 embedding（语义更完整）
    results = await execute_search(character_id, user_id, intent, user_input=user_input)
    
    elapsed_search = (time.monotonic() - t_search) * 1000
    logger.info(f"[长期记忆 Deep] Step 2 结果检索: {elapsed_search:.0f}ms, {len(results)} 条")
    
    # 判断是否有检索结果
    if not results:
        elapsed_total = (time.monotonic() - t0) * 1000
        logger.warning(f"[长期记忆 Deep] 检索失败: 无匹配结果")
        
        return LongTermMemoryResult(
            success=False,
            confidence=intent.get("confidence", 0.0),
            intent=intent,
            reason="检索无匹配结果",
        )
    
    # ── Step 3: 字数裁剪 ─────────────────────────────────────────────────────────
    t_trim = time.monotonic()
    
    trimmed_memories = trim_by_score_and_chars(results, MIN_CHARS, MAX_CHARS)
    
    elapsed_trim = (time.monotonic() - t_trim) * 1000
    total_chars = sum(len(m.get("content", "")) for m in trimmed_memories)
    logger.info(f"[长期记忆 Deep] Step 3 字数裁剪: {elapsed_trim:.0f}ms, "
               f"{len(trimmed_memories)} 条, 总字数 {total_chars}")
    
    # ── Step 4: LLM 整合 ─────────────────────────────────────────────────────────
    t_integrate = time.monotonic()
    
    context = await integrate_results(llm_client, user_input, trimmed_memories)
    
    elapsed_integrate = (time.monotonic() - t_integrate) * 1000
    logger.info(f"[长期记忆 Deep] Step 4 LLM 整合: {elapsed_integrate:.0f}ms, {len(context)} 字")
    
    # 判断整合是否成功
    if not context:
        elapsed_total = (time.monotonic() - t0) * 1000
        logger.warning(f"[长期记忆 Deep] 整合失败")
        
        return LongTermMemoryResult(
            success=False,
            confidence=intent.get("confidence", 0.0),
            intent=intent,
            reason="整合失败",
        )
    
    # ── Step 5: 返回结构化结果 ─────────────────────────────────────────────────
    elapsed_total = (time.monotonic() - t0) * 1000
    
    logger.info(f"[长期记忆 Deep] 检索成功: 总耗时 {elapsed_total:.0f}ms, "
               f"context {len(context)} 字")
    
    return LongTermMemoryResult(
        success=True,
        context=context,
        confidence=intent.get("confidence", 0.0),
        intent=intent,
    )


# ── 字数裁剪函数 ──────────────────────────────────────────────────────────────────────

def trim_by_score_and_chars(
    results: list[dict],
    min_chars: int = MIN_CHARS,
    max_chars: int = MAX_CHARS,
) -> list[dict]:
    """
    按分数排序，裁剪到指定字数范围
    
    流程:
    1. 按 similarity 降序排序
    2. 从高分开始累加字数
    3. 达到 min_chars 后可继续添加（不超过 max_chars）
    4. 如果总字数超过 max_chars，截断最后一项
    
    Args:
        results: 检索结果列表（包含 similarity, content）
        min_chars: 最小字数（默认 1000）
        max_chars: 最大字数（默认 2000）
    
    Returns:
        裁剪后的记忆片段列表
    
    示例：
        trimmed = trim_by_score_and_chars(
            [
                {"similarity": 0.9, "content": "...很长的内容..."},
                {"similarity": 0.8, "content": "..."},
            ],
            min_chars=1000,
            max_chars=2000,
        )
    """
    # 按相似度降序排序
    sorted_results = sorted(
        results,
        key=lambda x: x.get("similarity", 0),
        reverse=True,
    )
    
    trimmed = []
    total_chars = 0
    
    for item in sorted_results:
        content = item.get("content", "")
        chars = len(content)
        
        # 检查是否超过最大字数
        if total_chars + chars > max_chars:
            # 如果还没达到最小字数，截断当前条
            if total_chars < min_chars:
                remaining = max_chars - total_chars
                if remaining > 0:
                    trimmed_item = item.copy()
                    trimmed_item["content"] = content[:remaining] + "..."
                    trimmed.append(trimmed_item)
                    total_chars += remaining
                    logger.debug(f"[裁剪] 截断最后一条: {chars} → {remaining} 字")
            break
        
        # 添加当前条
        trimmed.append(item)
        total_chars += chars
        
        # 如果已达到或超过最小字数，可以选择继续或停止
        # 这里继续添加直到达到最大字数限制
    
    logger.info(f"[裁剪] 输入 {len(results)} 条, 输出 {len(trimmed)} 条, "
               f"总字数 {total_chars} (目标 {min_chars}-{max_chars})")
    
    return trimmed


# ── 辅助函数 ──────────────────────────────────────────────────────────────────────

def get_long_term_context_summary(result: LongTermMemoryResult) -> str:
    """
    获取长期记忆上下文摘要
    
    Args:
        result: 长期记忆检索结果
    
    Returns:
        摘要文本（用于日志/调试）
    """
    if result.success:
        return f"成功 (confidence={result.confidence:.2f}, context={len(result.context)}字)"
    else:
        return f"失败 (reason={result.reason})"


# ── 模块导出 ──────────────────────────────────────────────────────────────────────

__all__ = [
    "retrieve_long_term_memories_deep",
    "trim_by_score_and_chars",
    "LongTermMemoryResult",
    "MIN_CHARS",
    "MAX_CHARS",
    "CONFIDENCE_THRESHOLD",
]