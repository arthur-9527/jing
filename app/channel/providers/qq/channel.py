"""QQ Channel 实现

基于 qq-botpy SDK 实现 QQ Bot 消息收发。
支持多 Bot 账号，每个账号绑定不同角色。

关键修复：
- 使用 public_messages (bit 25) 监听 C2C 私聊
- 使用 on_c2c_message_create 处理私聊消息
"""

import asyncio
import base64
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Awaitable, Dict, List, Any

import botpy
from botpy.message import C2CMessage, GroupMessage, Message

from app.channel.base import BaseChannel, MessageHandler
from app.channel.types import InboundMessage, OutboundMessage, ContentBlock, MediaType
from app.channel.user_manager import UserManager, get_user_manager

logger = logging.getLogger(__name__)


class QQBotClient(botpy.Client):
    """QQ Bot Client 包装
    
    用于接收消息并转发到 Channel 处理器。
    每个 Client 绑定一个角色。
    """
    
    def __init__(
        self,
        bot_id: str,
        character_id: str,
        message_handler: Optional[Callable] = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        self._bot_id = bot_id
        self._character_id = character_id
        self._message_handler = message_handler
    
    async def on_ready(self):
        """Bot 就绪"""
        logger.info(f"[QQBotClient] Bot '{self.robot.name}' (id={self._bot_id}) 已就绪!")
        logger.info(f"[QQBotClient] 绑定角色: {self._character_id}")
    
    async def on_c2c_message_create(self, message: C2CMessage):
        """处理 C2C 私聊消息
        
        这是 QQ 真正的私聊功能！
        """
        logger.info(f"[QQBotClient] 收到 C2C 私聊: bot={self._bot_id}, user={message.author.user_openid}")
        if self._message_handler:
            await self._message_handler(message, "c2c", self._bot_id, self._character_id)
    
    async def on_group_at_message_create(self, message: GroupMessage):
        """处理群聊 @ 消息"""
        logger.info(f"[QQBotClient] 收到群聊 @: bot={self._bot_id}, group={message.group_openid}")
        if self._message_handler:
            await self._message_handler(message, "group", self._bot_id, self._character_id)


class BotConfig:
    """单个 Bot 配置"""
    
    def __init__(
        self,
        bot_id: str,
        app_id: str,
        app_secret: str,
        character_id: str,
    ):
        self.bot_id = bot_id
        self.app_id = app_id
        self.app_secret = app_secret
        self.character_id = character_id


class QQChannel(BaseChannel):
    """QQ Channel 实现
    
    基于 qq-botpy SDK，支持：
    - 多 Bot 账号
    - 每个 Bot 绑定不同角色
    - C2C 私聊消息收发
    - 群聊 @ 消息收发
    
    配置文件：config/qq_bots.yaml
    """
    
    def __init__(
        self,
        user_manager: Optional[UserManager] = None,
        config_path: Optional[str] = None,
    ):
        """
        Args:
            user_manager: 用户管理器实例
            config_path: 配置文件路径（默认 config/qq_bots.yaml）
        """
        self._user_manager = user_manager or get_user_manager()
        self._handler: Optional[MessageHandler] = None
        self._running = False
        
        # 配置文件路径
        self._config_path = config_path or str(Path(__file__).parent.parent.parent.parent / "config" / "qq_bots.yaml")
        
        # 加载配置
        self._bot_configs: Dict[str, BotConfig] = {}
        self._load_config()
        
        if not self._bot_configs:
            raise ValueError("QQ Bot 配置缺失: 请在 config/qq_bots.yaml 中配置 Bot")
        
        # Bot 客户端映射: bot_id → QQBotClient
        self._bots: Dict[str, QQBotClient] = {}
        self._bot_tasks: Dict[str, asyncio.Task] = {}
        
        # 用户 openid → bot_id 映射（记录用户最后活跃的 Bot）
        self._user_bot_map: Dict[str, str] = {}
        
        # 消息发送映射: user_id → (openid, bot_id)
        self._send_map: Dict[str, tuple] = {}
        
        logger.info(f"[QQChannel] 初始化完成: {len(self._bot_configs)} 个 Bot")
        for bot_id, config in self._bot_configs.items():
            logger.info(f"[QQChannel]   - {bot_id}: character={config.character_id}")
    
    @property
    def channel_id(self) -> str:
        return "qq"

    @property
    def channel_prompt(self) -> str | None:
        return (
            "你现在通过 QQ 与用户聊天。QQ 支持以下表达方式：\n"
            "- 颜文字 (kaomoji)：如 (≧▽≦)、(*´▽`*)、(｡･ω･｡) 等，可以适度使用让回复更加生动可爱\n"
            "- QQ 表情包：可以用文字描述你正在发送的表情包内容\n"
            "- 图片、语音、视频消息\n"
            "请根据 QQ 的平台特性，让聊天风格更加活泼有趣。"
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
    
    def _load_config(self):
        """加载配置文件"""
        try:
            import yaml
            
            config_file = Path(self._config_path)
            if not config_file.exists():
                logger.warning(f"[QQChannel] 配置文件不存在: {self._config_path}")
                # 尝试从环境变量加载默认配置
                self._load_from_env()
                return
            
            with open(config_file, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            
            bots_config = config.get("bots", {})
            
            for bot_id, bot_cfg in bots_config.items():
                app_id = bot_cfg.get("app_id") or os.getenv("QQ_BOT_APPID", "")
                app_secret = bot_cfg.get("app_secret") or os.getenv("QQ_BOT_SECRET", "")
                character_id = bot_cfg.get("character", "daji")
                
                if app_id and app_secret:
                    self._bot_configs[bot_id] = BotConfig(
                        bot_id=bot_id,
                        app_id=app_id,
                        app_secret=app_secret,
                        character_id=character_id,
                    )
                    logger.debug(f"[QQChannel] 加载 Bot 配置: {bot_id}")
            
        except Exception as e:
            logger.error(f"[QQChannel] 加载配置失败: {e}")
            self._load_from_env()
    
    def _load_from_env(self):
        """从环境变量加载默认配置"""
        app_id = os.getenv("QQ_BOT_APPID")
        app_secret = os.getenv("QQ_BOT_SECRET")
        
        if app_id and app_secret:
            self._bot_configs["default"] = BotConfig(
                bot_id="default",
                app_id=app_id,
                app_secret=app_secret,
                character_id="daji",
            )
            logger.info("[QQChannel] 从环境变量加载默认 Bot 配置")
    
    async def start(self) -> None:
        """启动 Channel
        
        包括：
        - 初始化所有 QQ Bot 客户端
        - 连接 WebSocket
        - 开始监听消息
        """
        logger.info("[QQChannel] 正在启动...")
        
        try:
            # 创建所有 Bot 客户端
            for bot_id, config in self._bot_configs.items():
                # 使用 public_messages Intent（关键修复！）
                intents = botpy.Intents(public_messages=True)
                
                client = QQBotClient(
                    bot_id=bot_id,
                    character_id=config.character_id,
                    message_handler=self._on_qq_message,
                    intents=intents,
                )
                
                self._bots[bot_id] = client
                
                # 启动 Bot（在后台任务中运行）
                task = asyncio.create_task(
                    self._run_bot(bot_id, config)
                )
                self._bot_tasks[bot_id] = task
                
                logger.info(f"[QQChannel] Bot {bot_id} 启动任务已创建")
            
            self._running = True
            logger.info("[QQChannel] 已启动，等待消息...")
            
        except Exception as e:
            logger.error(f"[QQChannel] 启动失败: {e}")
            raise
    
    async def _run_bot(self, bot_id: str, config: BotConfig):
        """运行单个 Bot"""
        try:
            client = self._bots[bot_id]
            await client.start(appid=config.app_id, secret=config.app_secret)
        except Exception as e:
            logger.error(f"[QQChannel] Bot {bot_id} 运行错误: {e}")
            self._running = False
    
    async def stop(self) -> None:
        """停止 Channel"""
        logger.info("[QQChannel] 正在停止...")
        
        self._running = False
        
        for bot_id, client in self._bots.items():
            try:
                await client.close()
            except Exception as e:
                logger.error(f"[QQChannel] 关闭 Bot {bot_id} 失败: {e}")
        
        for bot_id, task in self._bot_tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        logger.info("[QQChannel] 已停止")
    
    async def send(self, message: OutboundMessage) -> bool:
        """发送消息
        
        支持：
        - TEXT: 发送文本消息
        - IMAGE: 发送图片（使用 URL）
        - AUDIO: 发送语音条（使用 URL）
        - VIDEO: 发送视频（使用 URL）
        
        Args:
            message: 出站消息
        
        Returns:
            是否发送成功
        """
        # 获取用户 openid 和 bot_id
        send_info = self._send_map.get(message.user_id)
        if not send_info:
            logger.warning(f"[QQChannel] 用户 {message.user_id} 无发送映射")
            return False
        
        openid, bot_id = send_info
        client = self._bots.get(bot_id)
        if not client:
            logger.warning(f"[QQChannel] Bot {bot_id} 不存在")
            return False
        
        try:
            for content in message.contents:
                if content.type == MediaType.TEXT:
                    # 发送 C2C 文本消息
                    await client.api.post_c2c_message(
                        openid=openid,
                        msg_type=0,
                        msg_id=None,  # 主动消息不需要 msg_id
                        content=content.content
                    )
                    logger.debug(f"[QQChannel] 发送文本: {content.content[:50]}...")
                
                elif content.type in (MediaType.IMAGE, MediaType.AUDIO, MediaType.VIDEO):
                    # 发送多媒体消息
                    if not content.url:
                        logger.warning(f"[QQChannel] 多媒体内容缺少 URL，跳过")
                        continue
                    
                    # 确定 file_type
                    file_type_map = {
                        MediaType.IMAGE: 1,  # 图片
                        MediaType.VIDEO: 2,  # 视频
                        MediaType.AUDIO: 3,  # 语音
                    }
                    file_type = file_type_map.get(content.type, 1)
                    
                    # Step 1: 上传媒体（使用 URL）
                    media = await client.api.post_c2c_file(
                        openid=openid,
                        file_type=file_type,
                        url=content.url,
                        srv_send_msg=False  # 不自动发送，我们手动发送
                    )
                    
                    # Step 2: 发送消息（使用 Media）
                    if media:
                        await client.api.post_c2c_message(
                            openid=openid,
                            msg_type=7,  # 媒体消息类型
                            media=media,
                            content=content.content or "",  # 可选附加文本
                        )
                        logger.info(f"[QQChannel] 发送多媒体成功: type={content.type.value}, url={content.url}")
                    else:
                        logger.warning(f"[QQChannel] 上传媒体失败: {content.url}")
            
            return True
            
        except Exception as e:
            logger.error(f"[QQChannel] 发送失败: {e}")
            return False
    
    def set_message_handler(self, handler: MessageHandler) -> None:
        """设置消息处理器"""
        self._handler = handler
        logger.debug("[QQChannel] 消息处理器已设置")
    
    async def get_platform_user_id(self, user_id: str) -> Optional[str]:
        """反向查询：统一 user_id → QQ 用户 open_id"""
        return await self._user_manager.get_platform_user_id(user_id, "qq")
    
    async def send_typing(self, user_id: str) -> None:
        """发送"正在输入"状态
        
        QQ Bot 暂不支持输入状态提示。
        """
        pass
    
    async def stop_typing(self, user_id: str) -> None:
        """停止"正在输入"状态"""
        pass
    
    def is_running(self) -> bool:
        """检查是否正在运行"""
        return self._running
    
    def get_bot_character(self, bot_id: str) -> Optional[str]:
        """获取 Bot 绑定的角色 ID"""
        config = self._bot_configs.get(bot_id)
        return config.character_id if config else None
    
    # ============================================================
    # 内部方法
    # ============================================================
    
    async def _on_qq_message(
        self,
        message,
        message_type: str,
        bot_id: str,
        character_id: str,
    ):
        """处理 QQ 消息，转换为 InboundMessage
        
        Args:
            message: QQ 消息对象（C2CMessage 或 GroupMessage）
            message_type: 消息类型（"c2c" 或 "group"）
            bot_id: Bot ID
            character_id: 角色 ID
        """
        try:
            # 获取发送者 openid
            if message_type == "c2c":
                platform_user_id = message.author.user_openid
                message_id = message.id
            else:
                # 群聊消息
                platform_user_id = message.author.member_openid
                message_id = message.id
            
            logger.info(f"[QQChannel] 处理消息: type={message_type}, bot={bot_id}, user={platform_user_id}")
            
            # 调试：打印消息完整结构
            logger.debug(f"[QQChannel] 消息原始数据: {message.__dict__ if hasattr(message, '__dict__') else message}")
            if hasattr(message, "attachments") and message.attachments:
                for i, att in enumerate(message.attachments):
                    logger.debug(f"[QQChannel] 附件[{i}]: {att.__dict__ if hasattr(att, '__dict__') else att}")
            
            # 获取或创建用户
            user_id = await self._user_manager.get_or_create_user(
                platform="qq",
                platform_user_id=platform_user_id
            )
            
            # 保存发送映射（用于回复）
            self._send_map[user_id] = (platform_user_id, bot_id)
            self._user_bot_map[platform_user_id] = bot_id
            
            logger.debug(f"[QQChannel] 保存映射: {user_id} → ({platform_user_id}, {bot_id})")
            
            # 构建 ContentBlock 列表
            contents = []
            
            # 文本内容
            if hasattr(message, "content") and message.content:
                contents.append(ContentBlock(
                    type=MediaType.TEXT,
                    content=message.content
                ))
            
            # 处理所有附件类型（图片、语音条、音频文件、视频）
            if hasattr(message, "attachments") and message.attachments:
                for attachment in message.attachments:
                    content_type = getattr(attachment, "content_type", "")
                    url = getattr(attachment, "url", "")
                    filename = getattr(attachment, "filename", "")
                    
                    if content_type.startswith("image/"):
                        # 图片：保存 URL 用于直接转发
                        contents.append(ContentBlock(
                            type=MediaType.IMAGE,
                            content="",  # Echo 模式不需要下载
                            mime_type=content_type,
                            url=url
                        ))
                        logger.debug(f"[QQChannel] 收到图片: {url}")
                    
                    elif content_type == "voice" or content_type.startswith("audio/"):
                        # QQ 语音条：content_type='voice'，文件格式通常是 AMR
                        # 普通音频文件：content_type='audio/xxx'
                        mime_type = content_type if content_type.startswith("audio/") else "audio/amr"
                        contents.append(ContentBlock(
                            type=MediaType.AUDIO,
                            content="",
                            mime_type=mime_type,
                            url=url
                        ))
                        logger.info(f"[QQChannel] 收到语音条: filename={filename}, url={url}")
                    
                    elif content_type.startswith("video/"):
                        # 视频：保存 URL
                        contents.append(ContentBlock(
                            type=MediaType.VIDEO,
                            content="",
                            mime_type=content_type,
                            url=url
                        ))
                        logger.debug(f"[QQChannel] 收到视频: {url}")
            
            # 如果没有内容，跳过
            if not contents:
                logger.debug("[QQChannel] 消息无有效内容，跳过")
                return
            
            # 构造 InboundMessage
            inbound = InboundMessage(
                message_id=message_id,
                channel_id="qq",
                user_id=user_id,
                contents=contents,
                timestamp=datetime.now(),
                raw={
                    "message_type": message_type,
                    "bot_id": bot_id,
                    "character_id": character_id,
                    "author": {
                        "openid": platform_user_id,
                    },
                    "content": message.content if hasattr(message, "content") else None,
                    "attachments": [a.__dict__ if hasattr(a, '__dict__') else str(a) for a in message.attachments] if hasattr(message, "attachments") and message.attachments else [],
                }
            )
            
            # 添加 Bot/角色信息到 raw
            inbound.raw["bot_id"] = bot_id
            inbound.raw["character_id"] = character_id
            
            # 调用消息处理器
            if self._handler:
                await self._handler(inbound)
            
        except Exception as e:
            logger.error(f"[QQChannel] 消息处理失败: {e}")
    
    async def _download_media(self, url: str) -> Optional[bytes]:
        """下载媒体文件
        
        Args:
            url: 媒体文件 URL
        
        Returns:
            媒体文件二进制数据
        """
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.read()
            return None
        except Exception as e:
            logger.error(f"[QQChannel] 下载媒体失败: {e}")
            return None