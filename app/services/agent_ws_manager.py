#!/usr/bin/env python3
"""
Agent WebSocket 管理器

负责：
1. 管理所有前端 WebSocket 连接
2. 广播口型帧、动作帧、文本消息给所有客户端
3. 处理客户端消息（音频流、文本消息等）
"""

import asyncio
import json
import os
import time
from typing import Dict, Set, Optional, Callable
from fastapi import WebSocket
from loguru import logger
from dataclasses import dataclass, field
from enum import Enum

from app.services.lipsync_service import LipMorph


class AgentStatus(Enum):
    """Agent 状态"""
    INITING = "initing"    # 初始化中 ⭐ 新增
    IDLE = "idle"          # 空闲
    LISTENING = "listening"  # 正在听
    THINKING = "thinking"   # 思考中
    SPEAKING = "speaking"  # 说话中


@dataclass
class AgentClient:
    """WebSocket 客户端"""
    websocket: WebSocket
    client_id: str
    connected_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


class AgentWSManager:
    """Agent WebSocket 管理器"""

    def __init__(self):
        self._clients: Dict[str, AgentClient] = {}
        self._lock = asyncio.Lock()
        self._current_status: AgentStatus = AgentStatus.IDLE
        
        # 回调函数
        self._on_audio_received: Optional[Callable[[bytes, str], None]] = None
        self._on_text_received: Optional[Callable[[str, str], None]] = None
        self._on_client_connected: Optional[Callable[[str], None]] = None
        self._on_client_disconnected: Optional[Callable[[str], None]] = None
        self._on_interrupt: Optional[Callable[[], None]] = None

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def current_status(self) -> AgentStatus:
        return self._current_status

    # ===== 回调设置 =====

    def set_on_audio_received(self, callback: Callable[[bytes, str], None]):
        """设置音频接收回调 (audio_data: bytes, client_id: str)"""
        self._on_audio_received = callback

    def set_on_text_received(self, callback: Callable[[str, str], None]):
        """设置文本接收回调 (text: str, client_id: str)"""
        self._on_text_received = callback

    def set_on_client_connected(self, callback: Callable[[str], None]):
        """设置客户端连接回调 (client_id: str)"""
        self._on_client_connected = callback

    def set_on_client_disconnected(self, callback: Callable[[str], None]):
        """设置客户端断开回调 (client_id: str)"""
        self._on_client_disconnected = callback

    def set_on_interrupt(self, callback: Callable[[], None]):
        """设置打断回调"""
        self._on_interrupt = callback

    # ===== 辅助方法 =====

    def _get_character_id(self) -> Optional[str]:
        """从 CHARACTER_CONFIG_PATH 解析 character_id
        
        CHARACTER_CONFIG_PATH 格式: "config/characters/{character_id}"
        例如: "config/characters/daji" -> "daji"
        """
        from app.config import settings
        
        config_path = settings.CHARACTER_CONFIG_PATH
        if not config_path:
            return None
        
        # 提取最后一个路径段作为 character_id
        parts = config_path.rstrip("/").split("/")
        if len(parts) >= 2 and parts[-2] == "characters":
            return parts[-1]
        
        # 兜底：直接取最后一个路径段
        return parts[-1] if parts else None

    # ===== 连接管理 =====

    async def connect(self, websocket: WebSocket, client_id: str) -> AgentClient:
        """客户端连接"""
        await websocket.accept()
        
        async with self._lock:
            client = AgentClient(websocket=websocket, client_id=client_id)
            self._clients[client_id] = client
            logger.info(f"[AgentWS] 客户端连接: {client_id}, 当前连接数: {self.client_count}")

        # 发送连接确认
        await self.send_to_client(client_id, {
            "type": "connected",
            "client_id": client_id,
            "timestamp": time.time()
        })

        # 发送当前状态
        await self.send_to_client(client_id, {
            "type": "status",
            "status": self._current_status.value
        })

        # 注意：模型 URL 不再自动发送，改为前端主动请求（get_model_url）
        # 这样解决后端先启动、前端后启动时模型 URL 丢失的问题

        # 回调
        if self._on_client_connected:
            try:
                self._on_client_connected(client_id)
            except Exception as e:
                logger.error(f"[AgentWS] 连接回调错误: {e}")

        return client

    async def disconnect(self, client_id: str):
        """客户端断开"""
        async with self._lock:
            client = self._clients.pop(client_id, None)
            if client:
                logger.info(f"[AgentWS] 客户端断开: {client_id}, 剩余连接数: {self.client_count}")

        # 回调
        if self._on_client_disconnected and client:
            try:
                self._on_client_disconnected(client_id)
            except Exception as e:
                logger.error(f"[AgentWS] 断开回调错误: {e}")

    # ===== 消息发送 =====

    async def send_to_client(self, client_id: str, message: dict):
        """发送消息给指定客户端"""
        async with self._lock:
            client = self._clients.get(client_id)
            if not client:
                return

        try:
            await client.websocket.send_json(message)
        except Exception as e:
            logger.error(f"[AgentWS] 发送消息失败: {client_id}, {e}")
            await self.disconnect(client_id)

    async def broadcast(self, message: dict, exclude: Optional[Set[str]] = None):
        """广播消息给所有客户端"""
        exclude = exclude or set()
        
        async with self._lock:
            clients = list(self._clients.items())

        for client_id, client in clients:
            if client_id in exclude:
                continue
            try:
                await client.websocket.send_json(message)
            except Exception as e:
                logger.error(f"[AgentWS] 广播消息失败: {client_id}, {e}")
                await self.disconnect(client_id)

    async def broadcast_lip_frame(self, morphs: list[LipMorph]):
        """广播口型帧"""
        logger.info(f"[AgentWS] 推送口型帧, timestamp={time.time():.3f}")
        await self.broadcast({
            "type": "lip_frame",
            "morphs": [
                {"name": m.name, "weight": m.weight}
                for m in morphs
            ],
            "timestamp": time.time()
        })

    async def broadcast_motion_frame(self, motion_id: str, frame_index: int, bone_data: dict):
        """广播动作帧"""
        logger.info(f"[AgentWS] 推送动作帧, motion_id={motion_id}, frame_index={frame_index}, timestamp={time.time():.3f}")
        await self.broadcast({
            "type": "motion_frame",
            "motion_id": motion_id,
            "frame_index": frame_index,
            "bone_data": bone_data,
            "timestamp": time.time()
        })

    async def broadcast_text(self, role: str, content: str):
        """广播文本消息"""
        await self.broadcast({
            "type": "text",
            "role": role,
            "content": content,
            "timestamp": time.time()
        })

    async def broadcast_status(self, status: AgentStatus):
        """广播状态更新"""
        self._current_status = status
        await self.broadcast({
            "type": "status",
            "status": status.value,
            "timestamp": time.time()
        })

    async def broadcast_panel_html(self, data: dict):
        """广播 HTML 面板控制消息"""
        await self.broadcast({
            "type": "panel_html",
            **data,
            "timestamp": time.time()
        })

    # ===== 消息接收 =====

    async def handle_client_message(self, client_id: str, message: dict):
        """处理客户端消息"""
        msg_type = message.get("type")

        try:
            if msg_type == "audio":
                # 音频数据 (base64 编码)
                audio_data = message.get("data")
                if audio_data and self._on_audio_received:
                    import base64
                    audio_bytes = base64.b64decode(audio_data)
                    self._on_audio_received(audio_bytes, client_id)

            elif msg_type == "text":
                # 文本消息
                text = message.get("content", "")
                if text and self._on_text_received:
                    self._on_text_received(text, client_id)

            elif msg_type == "interrupt":
                # 打断信号
                logger.info(f"[AgentWS] 收到打断信号: {client_id}")
                if self._on_interrupt:
                    try:
                        import asyncio
                        result = self._on_interrupt()
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error(f"[AgentWS] 打断回调错误: {e}")

            elif msg_type == "mute":
                # ⭐ 静音控制（前端发送 mute/unmute 指令）
                mute_value = message.get("value", True)
                from app.services.mute_strategy import set_mute, is_muted
                set_mute(mute_value)
                logger.info(f"[AgentWS] 静音控制: client={client_id}, mute={mute_value}")
                
                # ⭐ 广播静音状态给所有客户端（前端监听 mute_status）
                await self.broadcast({
                    "type": "mute_status",
                    "muted": is_muted(),
                    "timestamp": time.time()
                })

            elif msg_type == "ping":
                # 心跳
                await self.send_to_client(client_id, {
                    "type": "pong",
                    "timestamp": time.time()
                })

            elif msg_type == "get_model_url":
                # 前端主动请求模型 URL
                character_id = self._get_character_id()
                if character_id:
                    await self.send_to_client(client_id, {
                        "type": "model_url",
                        "model_url": f"/characters/{character_id}/model/{character_id}.pmx",
                        "model_name": character_id
                    })
                    logger.info(f"[AgentWS] 响应模型 URL 请求: character_id={character_id}")
                else:
                    await self.send_to_client(client_id, {
                        "type": "error",
                        "message": "No character configured"
                    })

            else:
                logger.warning(f"[AgentWS] 未知消息类型: {msg_type}")

        except Exception as e:
            logger.error(f"[AgentWS] 处理消息失败: {client_id}, {e}")


# 全局实例
agent_ws_manager = AgentWSManager()
