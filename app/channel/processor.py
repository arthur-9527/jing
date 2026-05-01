"""Channel 消息处理器

独立的消息处理流程，平行于 ASR→LLM→TTS pipeline。

流程：
Channel In → ChannelProcessor → Channel Out

支持两种模式：
1. IM LLM 模式：多模态预处理 → LLM(JSON) → 多模态响应
2. Echo 模式：简单转发（用于测试）
"""

import logging
from typing import Optional, Dict, Any, Callable

from app.channel.types import InboundMessage, OutboundMessage
from app.channel.im_processor import IMChannelProcessor, register_im_processor, get_im_processor

logger = logging.getLogger(__name__)


class ChannelProcessor:
    """Channel 消息处理器
    
    支持两种模式：
    - IM LLM 模式：使用 IMChannelProcessor 处理多模态消息
    - Echo 模式：简单转发（用于测试）
    
    Attributes:
        _im_processor: IMChannelProcessor 实例
        _send_callback: 发送消息的回调函数
    """
    
    def __init__(
        self,
        character_config: Optional[Dict[str, Any]] = None,
        reference_image_path: Optional[str] = None,
        echo_mode: bool = False,
    ):
        """初始化处理器
        
        Args:
            character_config: 角色配置字典（用于 IM LLM 模式）
            reference_image_path: 参考图路径（用于图片/视频生成）
            echo_mode: 是否使用 Echo 模式（简单转发，用于测试）
        """
        self._echo_mode = echo_mode
        self._im_processor: Optional[IMChannelProcessor] = None
        self._send_callback: Optional[Callable] = None
        
        if echo_mode:
            logger.info("[ChannelProcessor] 初始化完成 (Echo 模式)")
        else:
            # 初始化 IM LLM 模式
            if character_config:
                character_id = character_config.get("character_id", "default")
                self._im_processor = register_im_processor(
                    character_id=character_id,
                    character_config=character_config,
                    reference_image_path=reference_image_path,
                )
                logger.info(f"[ChannelProcessor] 初始化完成 (IM LLM 模式), 角色: {character_id}")
            else:
                logger.warning("[ChannelProcessor] 无角色配置，将使用 Echo 模式")
                self._echo_mode = True
    
    def set_send_callback(self, callback: Callable):
        """设置发送消息的回调函数
        
        用于异步任务完成后推送消息
        
        Args:
            callback: 异步函数，接收 OutboundMessage
        """
        self._send_callback = callback
        if self._im_processor:
            self._im_processor.set_send_callback(callback)
    
    async def handle(self, message: InboundMessage) -> Optional[OutboundMessage]:
        """处理消息
        
        根据模式选择处理方式：
        - IM LLM 模式：多模态预处理 → LLM → 响应执行
        - Echo 模式：简单转发
        
        Args:
            message: 入站消息
            
        Returns:
            出站消息（立即可推送），或 None（异步生成中）
        """
        if self._echo_mode:
            return await self._handle_echo(message)
        else:
            return await self._handle_im_llm(message)
    
    async def _handle_echo(self, message: InboundMessage) -> Optional[OutboundMessage]:
        """Echo 模式处理
        
        简单转发：
        - 文本 → 返回固定回复
        - 媒体 → 直接转发
        
        Args:
            message: 入站消息
            
        Returns:
            出站消息
        """
        from app.channel.types import ContentBlock, MediaType
        
        outbound = OutboundMessage(
            channel_id=message.channel_id,
            user_id=message.user_id,
        )
        
        # 分离文本和媒体
        text_contents = [c for c in message.contents if c.type == MediaType.TEXT]
        media_contents = [c for c in message.contents if c.type != MediaType.TEXT]
        
        # 处理文本内容
        if text_contents:
            text = "\n".join(c.content for c in text_contents)
            logger.info(f"[ChannelProcessor] Echo 文本: {text[:50]}...")
            outbound.add_text(f"收到: {text[:20]}")
        
        # 处理媒体内容（直接转发）
        if media_contents:
            logger.info(f"[ChannelProcessor] Echo 媒体: {len(media_contents)} 个")
            for media in media_contents:
                if media.url:
                    outbound.contents.append(ContentBlock(
                        type=media.type,
                        content="",
                        mime_type=media.mime_type,
                        url=media.url,
                    ))
        
        if not outbound.contents:
            return None
        
        return outbound
    
    async def _handle_im_llm(self, message: InboundMessage) -> Optional[OutboundMessage]:
        """IM LLM 模式处理
        
        使用 IMChannelProcessor：
        1. 多模态预处理（ASR/Vision）
        2. LLM 调用（JSON 输出）
        3. 响应执行
        
        Args:
            message: 入站消息
            
        Returns:
            立即可推送的消息（text 类型），或 None（异步生成中）
        """
        if not self._im_processor:
            logger.error("[ChannelProcessor] IM Processor 未初始化")
            return await self._handle_echo(message)
        
        return await self._im_processor.handle(message)


# ---------------------------------------------------------------------------
# 全局实例管理
# ---------------------------------------------------------------------------

_channel_processor: Optional[ChannelProcessor] = None


def get_channel_processor() -> ChannelProcessor:
    """获取 ChannelProcessor 实例（Echo 模式，用于测试）"""
    global _channel_processor
    if _channel_processor is None:
        _channel_processor = ChannelProcessor(echo_mode=True)
    return _channel_processor


def init_channel_processor(
    character_config: Dict[str, Any],
    reference_image_path: Optional[str] = None,
) -> ChannelProcessor:
    """初始化 ChannelProcessor（IM LLM 模式）
    
    Args:
        character_config: 角色配置字典
        reference_image_path: 参考图路径
    
    Returns:
        ChannelProcessor 实例
    """
    global _channel_processor
    _channel_processor = ChannelProcessor(
        character_config=character_config,
        reference_image_path=reference_image_path,
        echo_mode=False,
    )
    return _channel_processor


def reset_channel_processor() -> None:
    """重置 ChannelProcessor（用于测试）"""
    global _channel_processor
    _channel_processor = None
    from app.channel.im_processor import reset_im_processors
    reset_im_processors()