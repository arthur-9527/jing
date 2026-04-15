#!/usr/bin/env python3
"""
投机采样器 - 基于ASR中间结果的并发LLM请求管理

核心思路：
1. ASR产生带句尾标点的中间结果时，提前发起LLM请求（并发）
2. 最终结果确认后，匹配最佳投机结果
3. 命中则直接使用，节省LLM延时；未命中则发起新请求

预期收益：
- 理想情况：减少约1000ms (63%)
- 平均情况（50%命中率）：减少约500ms (30%)
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger

from app.config import settings
from app.services.text_utils import match_score, is_similar


# 句尾标点集合
SENTENCE_END_MARKERS = ('。', '！', '？', '…', '.', '?', '!')


def is_sentence_end(text: str) -> bool:
    """检测文本是否以句尾标点结束"""
    text = text.strip()
    if not text:
        return False
    # 最后一个字符是句尾标点，且至少2个字符
    if text[-1] in SENTENCE_END_MARKERS:
        return len(text) >= 2
    return False


@dataclass
class SpeculativeRequest:
    """单个投机请求
    
    ⭐ 职责划分：
    - 投机采样器：收集流式数据、简单分类存储、预匹配动作
    - UnifiedStreamProcessor：推送TTS、触发动作播放、后续处理
    
    ⭐ 预计算字段（投机采样器整理）：
    - expression_chunks: 文本片段列表
    - action_events: 动作事件列表
    - metadata: 最终元数据
    - expression: 完整文本（拼接后）
    - matched_motion_by_phrase: 预匹配的动作结果
    """
    text: str                           # 中间结果文本
    item_id: str                        # ASR会话item_id
    task: Optional[asyncio.Task] = None # LLM任务
    state: str = "pending"              # pending/running/completed/cancelled
    
    # ⭐ 流式数据存储（LLM直接输出，实时更新）
    stream_items: list = field(default_factory=list)
    
    # ⭐ 简单分类存储（边生成边分类）
    expression_chunks: list = field(default_factory=list)  # 文本片段
    action_events: list = field(default_factory=list)      # 动作事件
    emotion_delta: Optional[dict] = None                   # 情绪增量
    metadata: Optional[dict] = None                        # 最终元数据
    tool_prompt: Optional[str] = None                      # 工具提示词
    
    # ⭐ 预计算结果（流结束后整理）
    expression: str = ""                                   # 完整文本
    matched_motion_by_phrase: dict = field(default_factory=dict)  # 预匹配动作
    turn_context: Optional[dict] = None                    # 对话上下文
    first_pass: Optional[dict] = None                      # 第一阶段结果
    
    # ⭐ 时间戳和事件
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    completed_event: asyncio.Event = field(default_factory=asyncio.Event)
    chunk_available: asyncio.Event = field(default_factory=asyncio.Event)


class SpeculativeSampler:
    """投机采样器 - 并发LLM请求管理"""
    
    def __init__(self):
        # 投机请求列表
        self._requests: list[SpeculativeRequest] = []
        # 当前ASR会话ID（用于区分不同轮次）
        self._current_item_id: str = ""
        # 锁（保护请求列表）
        self._lock = asyncio.Lock()
        # Agent引用（延迟设置）
        self._agent: Any = None
        # 统计
        self._stats = {
            "total_requests": 0,
            "cancelled_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
        }
    
    def set_agent(self, agent: Any):
        """设置EmotionalAgent引用"""
        self._agent = agent
        logger.info("[SpecSampler] Agent已设置")
    
    # ========== 句尾触发：发起投机请求 ==========
    
    async def on_sentence_end(self, text: str, item_id: str):
        """ASR中间结果带句尾标点时触发投机请求
        
        Args:
            text: ASR中间结果文本
            item_id: ASR会话item_id
        """
        if not self._agent:
            logger.warning("[SpecSampler] Agent未设置，跳过投机请求")
            return
        
        # 更新当前item_id
        self._current_item_id = item_id
        
        async with self._lock:
            # 80%去重检查
            for req in self._requests:
                if req.state != "cancelled" and self._is_similar(text, req.text, threshold=0.8):
                    logger.debug(f"[SpecSampler] 跳过相似请求: {text[:30]}...")
                    return
            
            # 创建投机请求
            request = SpeculativeRequest(text=text, item_id=item_id)
            request.state = "pending"
            
            # 创建LLM任务（后台运行）
            request.task = asyncio.create_task(
                self._run_speculative_llm(request)
            )
            self._requests.append(request)
            self._stats["total_requests"] += 1
            
            logger.info(f"[SpecSampler] 投机请求已发起: {text[:40]}...")
    
    async def _run_speculative_llm(self, request: SpeculativeRequest):
        """执行投机LLM请求（后台任务）
        
        ⭐ 职责划分：
        - 投机采样器：收集流式数据、简单分类存储、预匹配动作
        - UnifiedStreamProcessor：推送TTS、触发动作播放、后续处理
        
        ⭐ 边生成边分类存储：
        - 文本 chunk → expression_chunks
        - action 事件 → action_events
        - emotion_delta → request.emotion_delta
        - tool_prompt → request.tool_prompt
        - meta → request.metadata
        
        ⭐ 流结束后预计算：
        - 拼接完整 expression
        - 预匹配动作 matched_motion_by_phrase
        - 保存 turn_context、first_pass
        """
        request.state = "running"
        request.stream_items = []
        
        try:
            # ⭐ 边生成边分类存储
            async for item in self._agent.chat_stream_speculative(request.text, "default_user"):
                # ⭐ 实时更新 stream_items（外部可随时读取）
                request.stream_items.append(item)
                
                # ⭐ 简单分类存储（边生成边分类）
                if isinstance(item, str):
                    request.expression_chunks.append(item)
                elif isinstance(item, dict):
                    item_type = item.get("type")
                    
                    if item_type == "action":
                        action_name = item.get("action_name")
                        trigger_char = item.get("trigger_char")
                        if action_name and trigger_char:
                            request.action_events.append({
                                "action_name": action_name,
                                "trigger_char": trigger_char,
                            })
                    elif item_type == "emotion_delta":
                        request.emotion_delta = item.get("emotion_delta")
                    elif item_type == "tool_prompt":
                        request.tool_prompt = item.get("tool_prompt")
                    elif item_type == "meta":
                        request.metadata = item
                        # ⭐ 从 meta 中提取上下文
                        request.turn_context = item.get("turn_context")
                        request.first_pass = item.get("first_pass")
                        request.matched_motion_by_phrase = item.get("matched_motion_by_phrase", {})
                
                # ⭐ 每个 chunk 后通知（用于状态B实时监听）
                request.chunk_available.set()
            
            # ⭐ 流结束后预计算
            request.expression = "".join(request.expression_chunks)
            
            # ⭐ 如果 meta 中没有预匹配动作，尝试预匹配
            if not request.matched_motion_by_phrase and request.action_events:
                request.matched_motion_by_phrase = await self._precompute_motion_match(
                    request.expression,
                    request.action_events,
                )
                logger.info(
                    f"[SpecSampler] 预匹配动作完成，匹配数: {len(request.matched_motion_by_phrase)}"
                )
            
            request.state = "completed"
            request.completed_at = time.time()
            request.completed_event.set()
            request.chunk_available.set()
            
            elapsed = request.completed_at - request.created_at
            logger.info(
                f"[SpecSampler] LLM完成! elapsed={elapsed:.2f}s, "
                f"expression={len(request.expression)}chars, "
                f"actions={len(request.action_events)}, "
                f"text={request.text[:30]}..."
            )
            
        except asyncio.CancelledError:
            request.state = "cancelled"
            request.completed_event.set()
            request.chunk_available.set()
            self._stats["cancelled_requests"] += 1
            logger.debug(f"[SpecSampler] 请求被取消: {request.text[:30]}...")
        except Exception as e:
            request.state = "cancelled"
            request.completed_event.set()
            request.chunk_available.set()
            logger.warning(f"[SpecSampler] LLM失败: {e}")
    
    async def _precompute_motion_match(
        self,
        expression: str,
        action_events: list[dict],
    ) -> dict[str, dict]:
        """预匹配动作（消除确认后延迟）
        
        Args:
            expression: 完整文本
            action_events: 动作事件列表
            
        Returns:
            {action_name: matched_motion} 字典
        """
        matched_motion_by_phrase = {}
        
        if not self._agent:
            return matched_motion_by_phrase
        
        try:
            for action in action_events:
                action_name = action.get("action_name")
                if action_name:
                    matched_motion = await self._agent.match_motion_from_action(action_name)
                    if matched_motion:
                        matched_motion_by_phrase[action_name] = matched_motion
        except Exception as e:
            logger.warning(f"[SpecSampler] 预匹配动作失败: {e}")
        
        return matched_motion_by_phrase
    
    def _extract_expression(self, stream_items: list) -> str:
        """从流式结果中提取文本expression"""
        chunks = []
        for item in stream_items:
            if isinstance(item, str):
                chunks.append(item)
        return "".join(chunks)
    
    # ========== Redis 最终结果触发确认 ==========
    
    async def confirm_on_redis_final(self, final_text: str) -> tuple[Optional[dict], Optional[SpeculativeRequest]]:
        """Redis收到ASR最终结果时确认投机结果
        
        ⭐ 与 get_result_for_final 的区别：
        - 在 RedisAggregator 中调用，提前确认
        - 匹配阈值 0.8
        - 最早优先（而非分数最高）
        - 确认后取消其他请求，等待该请求完成
        
        Args:
            final_text: ASR最终结果文本
            
        Returns:
            (结果, 匹配的请求)
            - (result, request): 找到匹配请求，等待完成后返回结果
            - (None, None): 无匹配，需要新请求
        """
        async with self._lock:
            # 找最佳匹配请求（阈值0.8，最早优先）
            best_request = self._find_best_match(final_text, threshold=0.8)
            
            if not best_request:
                # 无匹配，取消所有投机请求
                await self._cancel_all_requests()
                self._stats["cache_misses"] += 1
                logger.info(f"[SpecSampler] Redis确认: 无匹配缓存，需要新请求: {final_text[:30]}...")
                return (None, None)
            
            # 找到匹配请求，取消其他请求
            logger.info(f"[SpecSampler] Redis确认: 找到匹配请求, text={best_request.text[:30]}...")
            await self._cancel_other_requests(best_request)
            
            # 等待该请求完成（不设超时，自然等待）
            if best_request.state == "running":
                logger.info(f"[SpecSampler] Redis确认: 等待LLM完成...")
                result = await self._wait_for_request(best_request)
                if result:
                    self._stats["cache_hits"] += 1
                    return (result, best_request)
                else:
                    self._stats["cache_misses"] += 1
                    return (None, None)
            
            elif best_request.state == "completed":
                # 已完成，直接返回
                self._stats["cache_hits"] += 1
                logger.info(
                    f"[SpecSampler] Redis确认: 缓存已就绪, "
                    f"elapsed={(best_request.completed_at - best_request.created_at):.2f}s"
                )
                return (best_request.result, best_request)
            
            else:
                # pending或其他状态，等待
                logger.debug(f"[SpecSampler] Redis确认: 请求状态={best_request.state}")
                result = await self._wait_for_request(best_request)
                if result:
                    self._stats["cache_hits"] += 1
                    return (result, best_request)
                self._stats["cache_misses"] += 1
                return (None, None)
    
    # ========== 最终匹配：获取缓存结果 ==========
    
    async def get_result_for_final(self, final_text: str) -> tuple[Optional[dict], Optional[SpeculativeRequest], bool]:
        """最终结果确认后，尝试获取投机缓存
        
        ⭐ 核心改动：返回请求对象，支持两种状态处理：
        - 状态A（LLM先完成）：result 已就绪，直接推送
        - 状态B（确认先到达）：request 正在运行，实时监听推送
        
        Args:
            final_text: ASR最终结果文本
            
        Returns:
            (结果, 请求对象, 是否需要等待)
            - (result, request, False): 缓存已就绪（状态A），直接推送 result
            - (None, request, True): 正在进行中（状态B），实时监听 request.stream_items
            - (None, None, False): 无匹配，需要新请求
        """
        async with self._lock:
            # 找最佳匹配请求
            best_request = self._find_best_match(final_text)
            
            if not best_request:
                # 无匹配，取消所有投机请求
                await self._cancel_all_requests()
                self._stats["cache_misses"] += 1
                logger.info(f"[SpecSampler] 无匹配缓存，需要新请求: {final_text[:30]}...")
                return (None, None, False)
            
            # 检查请求状态
            if best_request.state == "completed":
                # ✅ 状态A：已完成，直接返回完整结果
                self._stats["cache_hits"] += 1
                logger.info(
                    f"[SpecSampler] 缓存命中（状态A）! text={best_request.text[:30]}..., "
                    f"elapsed={(best_request.completed_at - best_request.created_at):.2f}s"
                )
                # 取消其他请求
                await self._cancel_other_requests(best_request)
                return (best_request.result, best_request, False)
            
            if best_request.state == "running":
                # ⏳ 状态B：正在进行，返回请求对象供实时监听
                logger.info(f"[SpecSampler] 缓存命中（状态B）! 实时监听: {best_request.text[:30]}...")
                # 取消其他请求
                await self._cancel_other_requests(best_request)
                # ⭐ 返回请求对象，由调用者实时监听 stream_items
                return (None, best_request, True)
            
            # pending状态（很少见）
            logger.debug("[SpecSampler] 请求pending，需要等待")
            return (None, best_request, True)
    
    async def stream_from_running_request(self, request: SpeculativeRequest):
        """从正在运行的投机请求中实时获取输出（状态B专用）
        
        ⭐ 状态B处理：确认后实时监听 LLM 输出，每个 chunk 立即 yield
        
        Args:
            request: 投机请求对象
            
        Yields:
            stream_items 中的每个 item（文本或事件）
        """
        last_index = 0
        
        logger.info(f"[SpecSampler] 开始实时监听投机请求: {request.text[:30]}...")
        
        while True:
            # 等待新 chunk 或完成
            await request.chunk_available.wait()
            request.chunk_available.clear()
            
            # 获取新增的 chunks（从上次读取位置开始）
            current_items = request.stream_items
            if len(current_items) > last_index:
                new_items = current_items[last_index:]
                last_index = len(current_items)
                
                for item in new_items:
                    yield item
            
            # 如果已完成，结束监听
            if request.state == "completed":
                logger.info(f"[SpecSampler] 投机请求已完成，结束监听")
                break
            
            # 如果被取消或出错，也结束监听
            if request.state in ("cancelled", "failed"):
                logger.warning(f"[SpecSampler] 投机请求状态异常: {request.state}")
                break
    
    async def _wait_for_request(self, request: SpeculativeRequest) -> Optional[dict]:
        """等待单个请求完成
        
        ⭐ 优化：使用 asyncio.Event 替代轮询，避免最多 50ms 不必要延迟。
        """
        # 等待完成事件（零延迟，事件驱动）
        await request.completed_event.wait()
        
        if request.state == "completed":
            return request.result
        return None
    
    # ========== 匹配逻辑 ==========
    
    def _find_best_match(self, final_text: str, threshold: float = 0.8) -> Optional[SpeculativeRequest]:
        """找到最匹配的投机请求
        
        Args:
            final_text: ASR最终结果文本
            threshold: 匹配阈值，默认0.8
            
        Returns:
            最早达到阈值的投机请求（而非分数最高的）
        """
        # 最早优先：遍历请求列表（按创建时间排序），找到第一个达到阈值的
        for req in self._requests:
            if req.state == "cancelled":
                continue
            
            score = self._match_score(final_text, req.text)
            if score >= threshold:
                logger.debug(f"[SpecSampler] 找到匹配请求: score={score:.2f}, text={req.text[:30]}...")
                return req
        
        logger.debug(f"[SpecSampler] 无匹配请求（阈值={threshold})")
        return None
    
    def _match_score(self, final_text: str, cached_text: str) -> float:
        """计算匹配分数（使用公共模块的标准化文本匹配）"""
        return match_score(final_text, cached_text)
    
    def _is_similar(self, text1: str, text2: str, threshold: float) -> bool:
        """相似度检查（使用公共模块的标准化文本匹配）"""
        return is_similar(text1, text2, threshold)
    
    # ========== 任务取消 ==========
    
    async def _cancel_other_requests(self, keep_request: Optional[SpeculativeRequest]):
        """取消除指定请求外的所有投机请求"""
        cancelled_count = 0
        
        for req in self._requests:
            if req != keep_request and req.state in ("pending", "running"):
                if req.task:
                    req.task.cancel()
                    try:
                        await req.task
                    except asyncio.CancelledError:
                        pass
                req.state = "cancelled"
                cancelled_count += 1
        
        if cancelled_count > 0:
            self._stats["cancelled_requests"] += cancelled_count
            logger.info(f"[SpecSampler] 已取消 {cancelled_count} 个无用请求")
    
    async def _cancel_all_requests(self):
        """取消所有投机请求"""
        await self._cancel_other_requests(None)
    
    # ========== 清理机制 ==========
    
    async def on_turn_end(self):
        """一轮对话结束，清理所有投机请求"""
        await self._cancel_all_requests()
        self._requests.clear()
        self._current_item_id = ""
        logger.info("[SpecSampler] 本轮投机请求已清理")
    
    async def on_interrupt(self):
        """用户打断时，取消所有投机请求"""
        await self._cancel_all_requests()
        logger.info("[SpecSampler] 打断，已取消所有投机请求")
    
    # ========== 统计 ==========
    
    def get_stats(self) -> dict:
        """获取统计数据"""
        return {
            **self._stats,
            "current_requests": len([r for r in self._requests if r.state != "cancelled"]),
            "hit_rate": (
                self._stats["cache_hits"] / 
                (self._stats["cache_hits"] + self._stats["cache_misses"])
                if (self._stats["cache_hits"] + self._stats["cache_misses"]) > 0
                else 0
            ),
        }


class DisabledSpeculativeSampler:
    """禁用状态的投机采样器 - 空实现"""
    
    def set_agent(self, agent: Any):
        """空实现 - 不设置 Agent"""
        pass
    
    async def on_sentence_end(self, text: str, item_id: str):
        """空实现 - 不触发投机请求"""
        pass
    
    async def confirm_on_redis_final(self, final_text: str) -> tuple[Optional[dict], Optional[SpeculativeRequest]]:
        """空实现 - 返回无匹配，需要新请求"""
        return (None, None)
    
    async def get_result_for_final(self, final_text: str) -> tuple[Optional[dict], Optional[SpeculativeRequest], bool]:
        """空实现 - 返回无匹配，需要新请求"""
        return (None, None, False)
    
    async def on_turn_end(self):
        """空实现 - 无需清理"""
        pass
    
    async def on_interrupt(self):
        """空实现 - 无需取消"""
        pass
    
    def get_stats(self) -> dict:
        """返回禁用状态的统计"""
        return {
            "total_requests": 0,
            "cancelled_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "current_requests": 0,
            "hit_rate": 0,
            "enabled": False,
        }


# 全局实例（单例）
_speculative_sampler: Optional[SpeculativeSampler] = None
_disabled_sampler: Optional[DisabledSpeculativeSampler] = None


def get_speculative_sampler() -> SpeculativeSampler | DisabledSpeculativeSampler:
    """获取全局投机采样器实例
    
    根据 settings.SPECULATIVE_SAMPLING_ENABLED 返回：
    - True: 返回正常工作的 SpeculativeSampler 实例
    - False: 返回空实现的 DisabledSpeculativeSampler 实例
    """
    global _speculative_sampler, _disabled_sampler
    
    if settings.SPECULATIVE_SAMPLING_ENABLED:
        if _speculative_sampler is None:
            _speculative_sampler = SpeculativeSampler()
            logger.info("[SpecSampler] 全局实例已创建（已启用）")
        return _speculative_sampler
    else:
        if _disabled_sampler is None:
            _disabled_sampler = DisabledSpeculativeSampler()
            logger.info("[SpecSampler] 投机采样已禁用")
        return _disabled_sampler


def is_speculative_sampling_enabled() -> bool:
    """检查投机采样是否启用"""
    return settings.SPECULATIVE_SAMPLING_ENABLED
