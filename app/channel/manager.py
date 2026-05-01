"""Channel Manager

负责：
- Channel 注册/生命周期
- 消息路由
- Agent 调用
"""

import asyncio
import logging
from typing import Optional, Callable, Awaitable

from app.channel.base import BaseChannel, MessageHandler
from app.channel.types import InboundMessage, OutboundMessage, ContentBlock, MediaType
from app.channel.user_manager import UserManager, get_user_manager

logger = logging.getLogger(__name__)


# 消息响应处理器类型
# 接收 InboundMessage，返回回复内容（文本或 OutboundMessage）
ResponseHandler = Callable[[InboundMessage], Awaitable[Optional[str | OutboundMessage]]]


class ChannelManager:
    """Channel 管理器
    
    负责：
    - Channel 注册/生命周期
    - 消息路由：inbound → handler → outbound
    - 统一的消息处理流程
    
    使用方式：
    1. 创建 ChannelManager
    2. 注册各个 Channel（wechat, telegram...）
    3. 设置消息响应处理器（处理消息、生成回复）
    4. 启动所有 Channel
    """
    
    def __init__(
        self,
        user_manager: Optional[UserManager] = None,
    ):
        """
        Args:
            user_manager: 用户管理器实例
        """
        self._channels: dict[str, BaseChannel] = {}
        self._user_manager = user_manager or get_user_manager()
        self._response_handler: Optional[ResponseHandler] = None
        self._running = False
        
        logger.info("[ChannelManager] 初始化完成")
    
    def register_channel(self, channel: BaseChannel) -> None:
        """注册 Channel
        
        Args:
            channel: Channel 实例
        """
        channel_id = channel.channel_id
        if channel_id in self._channels:
            logger.warning(f"[ChannelManager] Channel {channel_id} 已存在，将被覆盖")
        
        self._channels[channel_id] = channel
        channel.set_message_handler(self._handle_message)
        
        logger.info(f"[ChannelManager] Channel {channel_id} 已注册")
    
    def set_response_handler(self, handler: ResponseHandler) -> None:
        """设置消息响应处理器
        
        当收到消息时，调用此处理器生成回复。
        
        Args:
            handler: 异步函数，接收 InboundMessage，返回回复内容
                     - 返回 str: 直接作为文本回复
                     - 返回 OutboundMessage: 多模态回复
                     - 返回 None: 不回复
        """
        self._response_handler = handler
        logger.info("[ChannelManager] 响应处理器已设置")
    
    async def start(self) -> None:
        """启动所有 Channel"""
        if self._running:
            logger.warning("[ChannelManager] 已在运行中")
            return
        
        logger.info(f"[ChannelManager] 正在启动 {len(self._channels)} 个 Channel...")
        
        self._running = True
        
        # 并行启动所有 Channel
        start_tasks = []
        for channel_id, channel in self._channels.items():
            start_tasks.append(self._start_channel(channel_id, channel))
        
        if start_tasks:
            await asyncio.gather(*start_tasks, return_exceptions=True)
        
        logger.info("[ChannelManager] 所有 Channel 已启动")
    
    async def _start_channel(self, channel_id: str, channel: BaseChannel) -> None:
        """启动单个 Channel"""
        try:
            await channel.start()
            logger.info(f"[ChannelManager] Channel {channel_id} 启动成功")
        except Exception as e:
            logger.error(f"[ChannelManager] Channel {channel_id} 启动失败: {e}")
    
    async def stop(self) -> None:
        """停止所有 Channel"""
        logger.info("[ChannelManager] 正在停止...")
        
        self._running = False
        
        # 并行停止所有 Channel
        stop_tasks = []
        for channel_id, channel in self._channels.items():
            stop_tasks.append(self._stop_channel(channel_id, channel))
        
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)
        
        logger.info("[ChannelManager] 所有 Channel 已停止")
    
    async def _stop_channel(self, channel_id: str, channel: BaseChannel) -> None:
        """停止单个 Channel"""
        try:
            await channel.stop()
            logger.info(f"[ChannelManager] Channel {channel_id} 停止成功")
        except Exception as e:
            logger.error(f"[ChannelManager] Channel {channel_id} 停止失败: {e}")
    
    async def _handle_message(self, message: InboundMessage) -> None:
        """处理消息（核心流程）
        
        流程：
        1. 收到 InboundMessage
        2. 调用响应处理器生成回复
        3. 通过原 Channel 发送回复
        
        Args:
            message: 入站消息
        """
        channel_id = message.channel_id
        user_id = message.user_id
        
        logger.info(f"[ChannelManager] 处理消息: channel={channel_id}, user={user_id}")
        logger.debug(f"[ChannelManager] 消息内容: {message.text_content[:50] if message.text_content else '(无文本)'}...")
        
        try:
            # 检查是否有响应处理器
            if not self._response_handler:
                logger.warning("[ChannelManager] 未设置响应处理器，无法处理消息")
                return
            
            # 发送输入状态（可选）
            channel = self._channels.get(channel_id)
            if channel:
                await channel.send_typing(user_id)
            
            # 调用响应处理器
            response = await self._response_handler(message)
            
            # 停止输入状态
            if channel:
                await channel.stop_typing(user_id)
            
            # 处理响应
            if response is None:
                logger.debug("[ChannelManager] 无回复内容")
                return
            
            # 构造 OutboundMessage
            outbound: OutboundMessage
            if isinstance(response, str):
                # 文本回复
                outbound = OutboundMessage(
                    channel_id=channel_id,
                    user_id=user_id,
                )
                outbound.add_text(response)
            else:
                # OutboundMessage 回复
                outbound = response
            
            # 发送回复
            if channel:
                success = await channel.send(outbound)
                if success:
                    logger.info(f"[ChannelManager] 回复已发送: {outbound.text_content[:50] if outbound.text_content else '(多模态)'}...")
                else:
                    logger.error("[ChannelManager] 回复发送失败")
            
        except Exception as e:
            logger.error(f"[ChannelManager] 消息处理失败: {e}")
    
    def get_channel(self, channel_id: str) -> Optional[BaseChannel]:
        """获取 Channel"""
        return self._channels.get(channel_id)
    
    def get_registered_channels(self) -> list[str]:
        """获取已注册的 Channel ID 列表"""
        return list(self._channels.keys())
    
    def is_running(self) -> bool:
        """检查是否正在运行"""
        return self._running
    
    # ---------------------------------------------------------------------------
    # 主动发送接口
    # ---------------------------------------------------------------------------
    
    async def send_to_user(
        self,
        user_id: str,
        content: str | OutboundMessage,
        channel_id: Optional[str] = None,
    ) -> bool:
        """主动向用户发送消息
        
        系统可调用此方法主动发送消息，无需等待用户消息触发。
        
        使用场景：
        - 日常事件生成后，向高好感度用户发送消息
        - 工具调用完成后，向用户通知结果
        - 定时提醒、推送等
        
        Args:
            user_id: 统一用户 ID（如 "u_001")
            content: 消息内容
                     - str: 纯文本消息
                     - OutboundMessage: 多模态消息（文本/图片/语音等）
            channel_id: 目标平台 ID（可选）
                        - 如果指定，使用指定的 Channel
                        - 如果不指定，自动选择用户绑定的第一个可用 Channel
        
        Returns:
            是否发送成功
        
        Example:
            # 发送纯文本
            await channel_manager.send_to_user("u_001", "今天天气真好~")
            
            # 发送多模态消息
            outbound = OutboundMessage(channel_id="wechat", user_id="u_001")
            outbound.add_text("看这张照片！")
            outbound.add_image(base64_data)
            await channel_manager.send_to_user("u_001", outbound)
        """
        try:
            # 1. 确定目标 Channel
            target_channel: Optional[BaseChannel] = None
            target_channel_id: Optional[str] = channel_id
            
            if target_channel_id:
                # 使用指定的 Channel
                target_channel = self._channels.get(target_channel_id)
                if not target_channel:
                    logger.warning(f"[ChannelManager] 指定的 Channel {target_channel_id} 不存在")
                    return False
            else:
                # 自动选择 Channel：查询用户绑定的平台
                bindings = await self._user_manager.get_user_bindings(user_id)
                if not bindings:
                    logger.warning(f"[ChannelManager] 用户 {user_id} 无平台绑定")
                    return False
                
                # 尝试按用户绑定的平台顺序查找可用的 Channel
                for binding in bindings:
                    platform = binding.get("platform")
                    if platform and platform in self._channels:
                        target_channel = self._channels[platform]
                        target_channel_id = platform
                        break
                
                if not target_channel:
                    logger.warning(f"[ChannelManager] 用户 {user_id} 绑定的平台无可用 Channel: {bindings}")
                    return False
            
            # 2. 直接调用 BaseChannel 的 send_to_user 方法
            success = await target_channel.send_to_user(user_id, content)
            
            if success:
                # 获取文本内容用于日志
                text_preview = ""
                if isinstance(content, str):
                    text_preview = content[:50]
                elif hasattr(content, 'text_content') and content.text_content:
                    text_preview = content.text_content[:50]
                else:
                    text_preview = "(多模态)"
                
                logger.info(
                    f"[ChannelManager] 主动发送成功: channel={target_channel_id}, "
                    f"user={user_id}, content={text_preview}..."
                )
            else:
                logger.warning(f"[ChannelManager] 主动发送失败: channel={target_channel_id}, user={user_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"[ChannelManager] 主动发送异常: {e}")
            return False
    
    async def send_to_users_batch(
        self,
        user_ids: list[str],
        content: str | OutboundMessage,
        channel_id: Optional[str] = None,
    ) -> dict[str, bool]:
        """批量向多个用户发送消息
        
        Args:
            user_ids: 用户 ID 列表
            content: 消息内容（str 或 OutboundMessage）
            channel_id: 目标平台（可选）
        
        Returns:
            发送结果字典 {user_id: success}
        """
        results = {}
        for user_id in user_ids:
            results[user_id] = await self.send_to_user(user_id, content, channel_id)
        
        success_count = sum(1 for v in results.values() if v)
        logger.info(f"[ChannelManager] 批量发送完成: 成功 {success_count}/{len(user_ids)}")
        
        return results


# ---------------------------------------------------------------------------
# 全局实例
# ---------------------------------------------------------------------------

_channel_manager: Optional[ChannelManager] = None


def get_channel_manager() -> ChannelManager:
    """获取 Channel Manager 实例"""
    global _channel_manager
    if _channel_manager is None:
        _channel_manager = ChannelManager()
    return _channel_manager


def reset_channel_manager() -> None:
    """重置 Channel Manager（用于测试）"""
    global _channel_manager
    _channel_manager = None