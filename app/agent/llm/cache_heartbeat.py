"""Cerebras Prompt Caching 心跳保活机制

Cerebras 缓存 TTL 保证 5 分钟，最长可达 1 小时。
通过定期发送轻量心跳请求，可以保持缓存活跃，避免被驱逐。

心跳策略：
- 每 4 分钟发送一次（留 1 分钟 buffer）
- 使用最短的 user message（"ok"）
- 只请求 1 个 output token
- 缓存命中的 system prompt 不额外计费（或低价）
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.llm.client import LLMClient

logger = logging.getLogger(__name__)


class CacheHeartbeat:
    """
    Cerebras 缓存心跳保活器
    
    在对话间隔较长时（如用户长时间未输入），
    定期发送轻量请求保持缓存活跃。
    
    使用方式：
    ```python
    heartbeat = CacheHeartbeat(llm_client, static_system_prompt)
    await heartbeat.start()
    
    # 在每次用户对话时，可以触发即时心跳
    await heartbeat.pulse()
    
    # 清理时
    await heartbeat.stop()
    ```
    """

    # 心跳间隔（秒）：4 分钟，留 1 分钟 buffer
    DEFAULT_INTERVAL = 240

    def __init__(
        self,
        llm_client: "LLMClient",
        static_system_prompt: str,
        *,
        interval: int | None = None,
        enabled: bool = True,
    ):
        """
        Args:
            llm_client: LLM 客户端实例
            static_system_prompt: 静态 system prompt（用于触发缓存）
            interval: 心跳间隔（秒），默认 240 秒（4 分钟）
            enabled: 是否启用心跳，默认 True
        """
        self.llm = llm_client
        self.static_system_prompt = static_system_prompt
        self.interval = interval or self.DEFAULT_INTERVAL
        self.enabled = enabled

        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._last_heartbeat: float = 0.0
        self._heartbeat_count: int = 0

    async def start(self) -> None:
        """启动心跳循环"""
        if not self.enabled:
            logger.debug("Cache heartbeat 已禁用")
            return

        if self._task and not self._task.done():
            logger.debug("Cache heartbeat 已在运行")
            return

        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.info("Cache heartbeat 已启动，间隔: %d 秒", self.interval)

    async def stop(self) -> None:
        """停止心跳循环"""
        if self._stop_event:
            self._stop_event.set()

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.info(
            "Cache heartbeat 已停止，累计心跳: %d 次",
            self._heartbeat_count
        )

    async def pulse(self) -> None:
        """
        立即发送一次心跳
        
        适用于：
        - 用户刚发送消息后，触发一次心跳刷新缓存 TTL
        - 在长时间空闲后的第一次请求前调用
        """
        if not self.enabled:
            return

        try:
            await self._send_heartbeat()
            self._last_heartbeat = asyncio.get_event_loop().time()
        except Exception as e:
            logger.warning("Pulse heartbeat 失败: %s", e)

    async def _heartbeat_loop(self) -> None:
        """心跳主循环"""
        while True:
            try:
                # 发送心跳
                await self._send_heartbeat()
                self._last_heartbeat = asyncio.get_event_loop().time()

                # 等待下一次心跳
                # 使用 wait_for 支持提前取消
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.interval
                )
                # 如果 stop_event 被设置，wait_for 会正常完成
                break

            except asyncio.TimeoutError:
                # 超时后继续下一次循环
                continue
            except asyncio.CancelledError:
                # 被取消时正常退出
                break
            except Exception as e:
                # 只打 warn 日志，不做其他操作
                logger.warning("Heartbeat 失败: %s，%d 秒后重试", e, 10)
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=10
                    )
                    break
                except asyncio.TimeoutError:
                    continue

    async def _send_heartbeat(self) -> None:
        """发送一次轻量心跳请求"""
        import time
        start_time = time.monotonic()

        messages = [
            {"role": "system", "content": self.static_system_prompt},
            {"role": "user", "content": "ok"},
        ]

        try:
            # 使用最小 token 消耗：temperature=0
            response = await self.llm.chat(
                messages,
                temperature=0,
            )
            self._heartbeat_count += 1
            elapsed = (time.monotonic() - start_time) * 1000
            logger.debug(
                "Heartbeat #%d 完成，耗时: %.0fms，响应: %s",
                self._heartbeat_count,
                elapsed,
                response[:20] if response else "(空)"
            )
        except Exception as e:
            logger.debug("Heartbeat 请求失败（非关键）: %s", e)
            raise

    @property
    def is_running(self) -> bool:
        """心跳是否正在运行"""
        return self._task is not None and not self._task.done()

    @property
    def heartbeat_count(self) -> int:
        """累计心跳次数"""
        return self._heartbeat_count


# ─────────────────────────────────────────────────────────────────────────────
# 全局心跳管理器（可选，用于多 Agent 场景）
# ─────────────────────────────────────────────────────────────────────────────

_heartbeat_manager: "CacheHeartbeatManager | None" = None


class CacheHeartbeatManager:
    """
    全局心跳管理器
    
    用于管理多个 Agent 实例的缓存心跳。
    只需一个后台任务即可为所有 Agent 保持缓存活跃。
    """

    def __init__(self):
        self._heartbeats: dict[str, CacheHeartbeat] = {}
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()

    def register(self, agent_id: str, heartbeat: CacheHeartbeat) -> None:
        """注册一个 Agent 的心跳"""
        self._heartbeats[agent_id] = heartbeat

    def unregister(self, agent_id: str) -> None:
        """取消注册"""
        self._heartbeats.pop(agent_id, None)

    async def start(self) -> None:
        """启动管理器"""
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """停止管理器"""
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        """主循环"""
        while not self._stop_event.is_set():
            # 触发所有注册的心跳
            for agent_id, heartbeat in list(self._heartbeats.items()):
                try:
                    if heartbeat.is_running:
                        await heartbeat.pulse()
                except Exception as e:
                    logger.warning("Agent %s 心跳失败: %s", agent_id, e)

            # 每 4 分钟触发一次
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=240
                )
                break
            except asyncio.TimeoutError:
                continue


def get_heartbeat_manager() -> CacheHeartbeatManager:
    """获取全局心跳管理器"""
    global _heartbeat_manager
    if _heartbeat_manager is None:
        _heartbeat_manager = CacheHeartbeatManager()
    return _heartbeat_manager