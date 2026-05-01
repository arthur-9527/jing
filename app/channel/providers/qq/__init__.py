"""QQ Bot Channel Provider

支持多 Bot 账号，每个账号绑定不同角色。
"""

from app.channel.providers.qq.channel import QQChannel, QQBotClient, BotConfig

__all__ = ["QQChannel", "QQBotClient", "BotConfig"]
