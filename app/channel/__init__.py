"""IM Channel 模块

为陪伴型桌面虚拟人系统增加 IM Channel 接入层，支持用户通过 IM 平台与虚拟人交互。
"""

from app.channel.types import (
    MediaType,
    ContentBlock,
    InboundMessage,
    OutboundMessage,
)
from app.channel.base import BaseChannel, MessageHandler
from app.channel.user_manager import UserManager, get_user_manager
from app.channel.manager import ChannelManager, get_channel_manager, ResponseHandler
from app.channel.providers.wechat import WeChatChannel

__all__ = [
    # Types
    "MediaType",
    "ContentBlock",
    "InboundMessage",
    "OutboundMessage",
    # Base
    "BaseChannel",
    "MessageHandler",
    # User Manager
    "UserManager",
    "get_user_manager",
    # Channel Manager
    "ChannelManager",
    "get_channel_manager",
    "ResponseHandler",
    # Providers
    "WeChatChannel",
]
