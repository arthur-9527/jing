"""Channel Providers

各个 IM 平台的 Channel 实现。
"""

from app.channel.providers.wechat import WeChatChannel

__all__ = ["WeChatChannel"]