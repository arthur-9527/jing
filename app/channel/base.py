"""Channel 抽象基类

定义 IM 平台接入的标准接口。
"""

from abc import ABC, abstractmethod
from typing import Callable, Awaitable, Optional

from app.channel.types import InboundMessage, OutboundMessage


# 消息处理器类型
MessageHandler = Callable[[InboundMessage], Awaitable[None]]


class BaseChannel(ABC):
    """Channel 基类
    
    定义 IM 平台接入的标准接口。
    
    职责：
    1. 连接管理（start/stop）
    2. 消息收发（send）
    3. 消息解析（平台格式 → InboundMessage）
    4. 消息格式化（OutboundMessage → 平台格式）
    
    不负责：
    - 媒体理解（STT/VLM）→ 交给 LLM
    - 只做 base64 编码/解码
    """
    
    @property
    @abstractmethod
    def channel_id(self) -> str:
        """平台标识 (wechat/telegram)"""
        pass

    # ---------------------------------------------------------------------------
    # Channel 能力配置（子类可覆盖）
    # ---------------------------------------------------------------------------

    @property
    def channel_prompt(self) -> str | None:
        """Channel 专用提示词

        告知 AI 该平台支持的特殊表达方式（颜文字、表情包等）。
        返回 None 表示不注入额外提示词。

        子类覆盖示例：
            @property
            def channel_prompt(self) -> str:
                return "你现在通过QQ与用户聊天。QQ支持颜文字(kaomoji)..."
        """
        return None

    @property
    def supports_audio(self) -> bool:
        """该 Channel 是否支持发送音频/语音消息"""
        return False

    @property
    def supports_image(self) -> bool:
        """该 Channel 是否支持发送图片"""
        return False

    @property
    def supports_video(self) -> bool:
        """该 Channel 是否支持发送视频"""
        return False
    
    @abstractmethod
    async def start(self) -> None:
        """启动 Channel（开始监听消息）
        
        包括：
        - 登录/认证
        - 开始消息轮询
        - 初始化必要的资源
        """
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """停止 Channel
        
        包括：
        - 停止消息轮询
        - 断开连接
        - 清理资源
        """
        pass
    
    @abstractmethod
    async def send(self, message: OutboundMessage) -> bool:
        """发送消息
        
        根据 OutboundMessage.contents 逐个发送：
        - TEXT → 发送文本消息
        - AUDIO → 发送语音条（从 base64 解码）
        - IMAGE → 发送图片（从 base64 解码）
        - FILE → 发送文件
        
        Args:
            message: 出站消息
        
        Returns:
            是否发送成功
        """
        pass
    
    @abstractmethod
    def set_message_handler(self, handler: MessageHandler) -> None:
        """设置消息处理器
        
        当收到消息时，调用此处理器。
        Channel 负责将平台消息转换为 InboundMessage。
        
        Args:
            handler: 异步消息处理函数
        """
        pass
    
    @abstractmethod
    async def get_platform_user_id(self, user_id: str) -> Optional[str]:
        """反向查询：统一 user_id → 平台账号 ID
        
        用于发送消息时查找目标用户。
        
        Args:
            user_id: 统一用户 ID
        
        Returns:
            平台用户 ID，如果不存在则返回 None
        """
        pass
    
    async def send_typing(self, user_id: str) -> None:
        """发送"正在输入"状态（可选实现）
        
        子类可覆盖此方法以支持输入状态提示。
        
        Args:
            user_id: 目标用户 ID
        """
        pass
    
    async def stop_typing(self, user_id: str) -> None:
        """停止"正在输入"状态（可选实现）
        
        子类可覆盖此方法。
        
        Args:
            user_id: 目标用户 ID
        """
        pass
    
    def is_running(self) -> bool:
        """检查 Channel 是否正在运行
        
        子类可覆盖此方法。
        """
        return False
    
    # ---------------------------------------------------------------------------
    # 主动发送接口（默认实现）
    # ---------------------------------------------------------------------------
    
    async def send_to_user(
        self,
        user_id: str,
        content: str | OutboundMessage,
    ) -> bool:
        """主动向用户发送消息
        
        系统可调用此方法主动发送消息，无需等待用户消息触发。
        这是每个 Channel 的基础能力。
        
        使用场景：
        - 日常事件生成后，向用户发送消息
        - 工具调用完成后，向用户通知结果
        - 定时提醒、推送等
        
        Args:
            user_id: 统一用户 ID（如 "u_001")
            content: 消息内容
                     - str: 纯文本消息
                     - OutboundMessage: 多模态消息（文本/图片/语音等）
        
        Returns:
            是否发送成功
        
        Example:
            # 发送纯文本
            await channel.send_to_user("u_001", "今天天气真好~")
            
            # 发送多模态消息
            outbound = OutboundMessage(channel_id="wechat", user_id="u_001")
            outbound.add_text("看这张照片！")
            outbound.add_image(base64_data)
            await channel.send_to_user("u_001", outbound)
        """
        # 构造 OutboundMessage
        outbound: OutboundMessage
        if isinstance(content, str):
            # 纯文本，构造 OutboundMessage
            outbound = OutboundMessage(
                channel_id=self.channel_id,
                user_id=user_id,
            )
            outbound.add_text(content)
        else:
            # 已经是 OutboundMessage
            outbound = content
            # 确保 user_id 和 channel_id 正确
            outbound.user_id = user_id
            outbound.channel_id = self.channel_id
        
        # 发送输入状态（可选）
        await self.send_typing(user_id)
        
        # 调用核心 send 方法
        success = await self.send(outbound)
        
        # 停止输入状态
        await self.stop_typing(user_id)
        
        return success
    
    async def send_text(self, user_id: str, text: str) -> bool:
        """便捷方法：发送纯文本消息
        
        Args:
            user_id: 统一用户 ID
            text: 文本内容
        
        Returns:
            是否发送成功
        """
        return await self.send_to_user(user_id, text)
