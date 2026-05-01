"""IM Channel 核心类型定义

统一消息格式，用于所有 IM 平台的消息收发。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class MediaType(str, Enum):
    """消息内容类型"""
    TEXT = "text"       # 文本
    AUDIO = "audio"     # 语音
    IMAGE = "image"     # 图片
    VIDEO = "video"     # 视频
    FILE = "file"       # 文件


@dataclass
class ContentBlock:
    """统一内容块
    
    所有入站/出站内容都使用此格式。
    Provider 负责将媒体转为 base64，LLM 直接处理多模态。
    """
    type: MediaType
    content: str                        # 文本内容 或 base64 编码的媒体数据
    mime_type: Optional[str] = None     # 媒体 MIME 类型 (image/jpeg, audio/ogg...)
    duration: Optional[float] = None    # 音频/视频时长（秒）
    file_name: Optional[str] = None     # 文件名（用于 FILE 类型）
    url: Optional[str] = None           # 原始 URL（用于直接转发，避免重新上传）
    
    def to_dict(self) -> dict:
        """转换为字典格式"""
        result = {
            "type": self.type.value,
            "content": self.content,
        }
        if self.mime_type:
            result["mime_type"] = self.mime_type
        if self.duration:
            result["duration"] = self.duration
        if self.file_name:
            result["file_name"] = self.file_name
        return result
    
    @classmethod
    def from_dict(cls, data: dict) -> "ContentBlock":
        """从字典创建"""
        return cls(
            type=MediaType(data["type"]),
            content=data["content"],
            mime_type=data.get("mime_type"),
            duration=data.get("duration"),
            file_name=data.get("file_name"),
        )


@dataclass
class InboundMessage:
    """入站消息
    
    所有 IM 平台的消息都转换为此格式。
    contents 列表可包含多个 ContentBlock（混合文本+图片+语音）。
    """
    message_id: str                          # 平台消息 ID
    channel_id: str                          # 平台标识 (wechat/telegram)
    user_id: str                             # 统一用户 ID (如 "u_001")
    contents: list[ContentBlock] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
    raw: Optional[dict] = None               # 原始消息数据（可选）
    
    @property
    def text_content(self) -> str:
        """获取纯文本内容（合并所有文本块）"""
        return "\n".join(
            c.content for c in self.contents 
            if c.type == MediaType.TEXT
        )
    
    @property
    def has_media(self) -> bool:
        """是否包含媒体内容"""
        return any(c.type != MediaType.TEXT for c in self.contents)
    
    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "message_id": self.message_id,
            "channel_id": self.channel_id,
            "user_id": self.user_id,
            "contents": [c.to_dict() for c in self.contents],
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class OutboundMessage:
    """出站消息
    
    Agent 生成的回复转换为此格式，再由各平台适配器发送。
    """
    channel_id: str              # 目标平台
    user_id: str                 # 统一用户 ID
    contents: list[ContentBlock] = field(default_factory=list)
    
    def add_text(self, text: str) -> "OutboundMessage":
        """添加文本内容"""
        self.contents.append(ContentBlock(type=MediaType.TEXT, content=text))
        return self
    
    def add_audio(self, base64_data: str, mime_type: str = "audio/ogg", duration: float = 0) -> "OutboundMessage":
        """添加音频内容"""
        self.contents.append(ContentBlock(
            type=MediaType.AUDIO, content=base64_data, 
            mime_type=mime_type, duration=duration
        ))
        return self
    
    def add_image(self, base64_data: str, mime_type: str = "image/png") -> "OutboundMessage":
        """添加图片内容"""
        self.contents.append(ContentBlock(
            type=MediaType.IMAGE, content=base64_data, mime_type=mime_type
        ))
        return self
    
    def add_file(self, base64_data: str, file_name: str, mime_type: Optional[str] = None) -> "OutboundMessage":
        """添加文件内容"""
        self.contents.append(ContentBlock(
            type=MediaType.FILE, content=base64_data, 
            mime_type=mime_type, file_name=file_name
        ))
        return self
    
    # URL 版本的添加方法（用于图生图、图生视频等场景）
    
    def add_image_url(self, url: str, mime_type: str = "image/png") -> "OutboundMessage":
        """添加图片（使用 URL）
        
        适用于图生图、外部图片链接等场景。
        QQ Channel 等平台发送多媒体消息需要 URL。
        
        Args:
            url: 图片 URL
            mime_type: MIME 类型
        
        Returns:
            self（支持链式调用）
        """
        self.contents.append(ContentBlock(
            type=MediaType.IMAGE, content="", url=url, mime_type=mime_type
        ))
        return self
    
    def add_audio_url(self, url: str, mime_type: str = "audio/ogg", duration: float = 0) -> "OutboundMessage":
        """添加音频（使用 URL）
        
        适用于外部音频链接、已上传的音频文件等场景。
        
        Args:
            url: 音频 URL
            mime_type: MIME 类型
            duration: 音频时长（秒）
        
        Returns:
            self（支持链式调用）
        """
        self.contents.append(ContentBlock(
            type=MediaType.AUDIO, content="", url=url, 
            mime_type=mime_type, duration=duration
        ))
        return self
    
    def add_video_url(self, url: str, mime_type: str = "video/mp4", duration: float = 0) -> "OutboundMessage":
        """添加视频（使用 URL）
        
        适用于图生视频、外部视频链接等场景。
        
        Args:
            url: 视频 URL
            mime_type: MIME 类型
            duration: 视频时长（秒）
        
        Returns:
            self（支持链式调用）
        """
        self.contents.append(ContentBlock(
            type=MediaType.VIDEO, content="", url=url, 
            mime_type=mime_type, duration=duration
        ))
        return self
    
    @property
    def text_content(self) -> str:
        """获取纯文本内容"""
        return "\n".join(
            c.content for c in self.contents 
            if c.type == MediaType.TEXT
        )
    
    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "channel_id": self.channel_id,
            "user_id": self.user_id,
            "contents": [c.to_dict() for c in self.contents],
        }