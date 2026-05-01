"""微信 Channel 实现

基于 wechatbot-sdk 实现微信消息收发。
"""

import asyncio
import base64
import logging
from datetime import datetime
from typing import Optional, Callable, Awaitable

from wechatbot import WeChatBot, IncomingMessage

from app.channel.base import BaseChannel, MessageHandler
from app.channel.types import InboundMessage, OutboundMessage, ContentBlock, MediaType
from app.channel.user_manager import UserManager, get_user_manager

logger = logging.getLogger(__name__)


class WeChatChannel(BaseChannel):
    """微信 Channel 实现
    
    基于 wechatbot-sdk，使用 iLink 协议连接微信。
    
    功能：
    - 扫码登录（凭证持久化）
    - 长轮询消息接收
    - 文本/图片/语音消息收发
    - 输入状态提示
    """
    
    def __init__(
        self,
        user_manager: Optional[UserManager] = None,
        cred_path: Optional[str] = None,
        on_qr_url: Optional[Callable[[str], None]] = None,
        on_scanned: Optional[Callable[[], None]] = None,
        on_expired: Optional[Callable[[], None]] = None,
    ):
        """
        Args:
            user_manager: 用户管理器实例
            cred_path: 凭证存储路径（默认 ~/.wechatbot/credentials.json）
            on_qr_url: QR 码 URL 回调（用于显示扫码二维码）
            on_scanned: 扫码成功回调
            on_expired: QR 码过期回调
        """
        self._user_manager = user_manager or get_user_manager()
        self._handler: Optional[MessageHandler] = None
        self._running = False
        
        # QR 码回调
        self._on_qr_url = on_qr_url or self._default_on_qr_url
        self._on_scanned = on_scanned or self._default_on_scanned
        self._on_expired = on_expired or self._default_on_expired
        
        # 创建 WeChatBot 实例
        self._bot = WeChatBot(
            cred_path=cred_path or "~/.wechatbot/credentials.json",
            on_qr_url=self._on_qr_url,
            on_scanned=self._on_scanned,
            on_expired=self._on_expired,
            on_error=self._on_error,
        )
        
        # 注册消息处理器
        self._bot.on_message(self._on_wechat_message)
        
        logger.info("[WeChatChannel] 初始化完成")
    
    @property
    def channel_id(self) -> str:
        return "wechat"

    @property
    def channel_prompt(self) -> str | None:
        return (
            "你现在通过微信与用户聊天。微信支持以下表达方式：\n"
            "- 微信表情包：可以用文字描述你正在发送的表情包内容\n"
            "- 图片、语音、视频消息\n"
            "微信用户交流风格偏简洁自然，请保持得体的表达方式。"
        )

    @property
    def supports_audio(self) -> bool:
        return True

    @property
    def supports_image(self) -> bool:
        return True

    @property
    def supports_video(self) -> bool:
        return True
    
    async def start(self) -> None:
        """启动 Channel
        
        包括：
        - 登录（扫码或使用已有凭证）
        - 开始消息轮询
        """
        logger.info("[WeChatChannel] 正在启动...")
        
        try:
            # 登录（如果已有凭证则自动跳过扫码）
            await self._bot.login()
            
            # 开始长轮询
            await self._bot.start()
            
            self._running = True
            logger.info("[WeChatChannel] 已启动，等待消息...")
            
        except Exception as e:
            logger.error(f"[WeChatChannel] 启动失败: {e}")
            raise
    
    async def stop(self) -> None:
        """停止 Channel"""
        logger.info("[WeChatChannel] 正在停止...")
        
        self._running = False
        self._bot.stop()
        
        logger.info("[WeChatChannel] 已停止")
    
    async def send(self, message: OutboundMessage) -> bool:
        """发送消息
        
        支持：
        - TEXT: 直接发送文本
        - IMAGE: base64 解码后发送图片
        - AUDIO: base64 解码后发送语音
        - FILE: base64 解码后发送文件
        
        Args:
            message: 出站消息
        
        Returns:
            是否发送成功
        """
        platform_user_id = await self.get_platform_user_id(message.user_id)
        if not platform_user_id:
            logger.warning(f"[WeChatChannel] 用户 {message.user_id} 无微信绑定")
            return False
        
        try:
            for content in message.contents:
                if content.type == MediaType.TEXT:
                    # 发送文本
                    await self._bot.send(platform_user_id, content.content)
                    logger.debug(f"[WeChatChannel] 发送文本: {content.content[:50]}...")
                
                elif content.type == MediaType.IMAGE:
                    # 发送图片
                    image_bytes = base64.b64decode(content.content)
                    await self._bot.send_media(platform_user_id, {"image": image_bytes})
                    logger.debug(f"[WeChatChannel] 发送图片: {len(image_bytes)} bytes")
                
                elif content.type == MediaType.AUDIO:
                    # 发送语音（作为文件）
                    audio_bytes = base64.b64decode(content.content)
                    await self._bot.send_media(platform_user_id, {
                        "file": audio_bytes,
                        "file_name": "voice.mp3"
                    })
                    logger.debug(f"[WeChatChannel] 发送语音: {len(audio_bytes)} bytes")
                
                elif content.type == MediaType.FILE:
                    # 发送文件
                    file_bytes = base64.b64decode(content.content)
                    await self._bot.send_media(platform_user_id, {
                        "file": file_bytes,
                        "file_name": content.file_name or "file"
                    })
                    logger.debug(f"[WeChatChannel] 发送文件: {content.file_name}")
            
            return True
            
        except Exception as e:
            logger.error(f"[WeChatChannel] 发送失败: {e}")
            return False
    
    def set_message_handler(self, handler: MessageHandler) -> None:
        """设置消息处理器"""
        self._handler = handler
        logger.debug("[WeChatChannel] 消息处理器已设置")
    
    async def get_platform_user_id(self, user_id: str) -> Optional[str]:
        """反向查询：统一 user_id → 微信账号 ID"""
        return await self._user_manager.get_platform_user_id(user_id, "wechat")
    
    async def send_typing(self, user_id: str) -> None:
        """发送"正在输入"状态"""
        platform_user_id = await self.get_platform_user_id(user_id)
        if platform_user_id:
            await self._bot.send_typing(platform_user_id)
    
    async def stop_typing(self, user_id: str) -> None:
        """停止"正在输入"状态"""
        platform_user_id = await self.get_platform_user_id(user_id)
        if platform_user_id:
            await self._bot.stop_typing(platform_user_id)
    
    def is_running(self) -> bool:
        """检查是否正在运行"""
        return self._running
    
    # ============================================================
    # 内部方法
    # ============================================================
    
    async def _on_wechat_message(self, msg: IncomingMessage):
        """处理微信消息，转换为 InboundMessage"""
        logger.info(f"[WeChatChannel] 收到消息: user={msg.user_id}, type={msg.type}")
        
        try:
            # 获取或创建用户
            user_id = await self._user_manager.get_or_create_user(
                platform="wechat",
                platform_user_id=msg.user_id
            )
            
            # 构建 ContentBlock 列表
            contents = []
            
            # 文本
            if msg.text:
                contents.append(ContentBlock(
                    type=MediaType.TEXT,
                    content=msg.text
                ))
            
            # 图片
            if msg.images:
                media = await self._bot.download(msg)
                if media:
                    image_base64 = base64.b64encode(media.data).decode('utf-8')
                    contents.append(ContentBlock(
                        type=MediaType.IMAGE,
                        content=image_base64,
                        mime_type="image/jpeg"
                    ))
            
            # 语音
            if msg.voices:
                media = await self._bot.download(msg)
                if media:
                    voice_base64 = base64.b64encode(media.data).decode('utf-8')
                    contents.append(ContentBlock(
                        type=MediaType.AUDIO,
                        content=voice_base64,
                        mime_type="audio/silk",  # 微信语音格式
                        duration=msg.voices[0].duration if msg.voices else None
                    ))
            
            # 文件
            if msg.files:
                media = await self._bot.download(msg)
                if media:
                    file_base64 = base64.b64encode(media.data).decode('utf-8')
                    contents.append(ContentBlock(
                        type=MediaType.FILE,
                        content=file_base64,
                        file_name=media.file_name,
                        mime_type=media.type if hasattr(media, 'type') else None
                    ))
            
            # 如果没有内容，跳过
            if not contents:
                logger.debug("[WeChatChannel] 消息无有效内容，跳过")
                return
            
            # 构造 InboundMessage
            inbound = InboundMessage(
                message_id=str(msg.timestamp.timestamp()),
                channel_id="wechat",
                user_id=user_id,
                contents=contents,
                timestamp=msg.timestamp,
                raw=msg.raw if hasattr(msg, 'raw') else None,
            )
            
            # 调用消息处理器
            if self._handler:
                await self._handler(inbound)
            
        except Exception as e:
            logger.error(f"[WeChatChannel] 消息处理失败: {e}")
    
    def _default_on_qr_url(self, url: str):
        """默认 QR 码回调"""
        logger.info(f"[WeChatChannel] 扫码登录: {url}")
        print(f"\n请扫描二维码登录微信: {url}\n")
    
    def _default_on_scanned(self):
        """默认扫码成功回调"""
        logger.info("[WeChatChannel] 扫码成功，等待确认登录...")
    
    def _default_on_expired(self):
        """默认 QR 码过期回调"""
        logger.warning("[WeChatChannel] QR 码已过期，请重新扫码")
    
    def _on_error(self, err: str):
        """错误回调"""
        logger.error(f"[WeChatChannel] 错误: {err}")