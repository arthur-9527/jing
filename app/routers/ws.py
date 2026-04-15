"""WebSocket 相关路由"""

import asyncio
import json
import uuid
from typing import Dict, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database import get_db_session
from app.services.motion_service import MotionService
from app.services.agent_ws_manager import agent_ws_manager, AgentStatus
from app.services.agent_service import get_agent_service

router = APIRouter(tags=["websocket"])


class MotionStreamManager:
    """动作流管理器"""
    
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
    
    async def connect(self, websocket: WebSocket, motion_id: str):
        """连接 WebSocket"""
        await websocket.accept()
        self.active_connections[motion_id] = websocket
    
    def disconnect(self, motion_id: str):
        """断开连接"""
        if motion_id in self.active_connections:
            del self.active_connections[motion_id]
    
    async def send_frame(self, motion_id: str, frame_data: dict):
        """发送帧数据"""
        if motion_id in self.active_connections:
            try:
                await self.active_connections[motion_id].send_json(frame_data)
            except Exception:
                self.disconnect(motion_id)


# 全局流管理器
stream_manager = MotionStreamManager()


@router.websocket("/ws/echo")
async def echo_websocket(websocket: WebSocket):
    """
    Echo WebSocket 测试端点
    
    用于测试 WebSocket 连接稳定性，不依赖数据库
    客户端发送的消息会被原样返回
    """
    try:
        await websocket.accept()
        
        # 发送连接确认
        await websocket.send_json({
            "type": "connected",
            "message": "WebSocket echo test connected"
        })
        
        # Echo 循环
        while True:
            message = await websocket.receive_text()
            # 返回收到的消息
            await websocket.send_json({
                "type": "echo",
                "original": message,
                "timestamp": asyncio.get_event_loop().time()
            })
            
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({
                "type": "error",
                "message": str(e)
            })
        except Exception:
            pass


@router.websocket("/ws/motion-stream")
async def motion_stream(
    websocket: WebSocket,
    motion_id: str = Query(..., description="动作 ID"),
):
    """
    实时动作流 WebSocket
    
    连接后，服务端会流式传输指定动作的所有关键帧
    """
    try:
        # 验证 motion_id 格式
        try:
            uuid = UUID(motion_id)
        except ValueError:
            await websocket.accept()
            await websocket.send_json({
                "type": "error",
                "error": {
                    "code": "INVALID_MOTION_ID",
                    "message": "无效的动作 ID 格式"
                }
            })
            await websocket.close()
            return
        
        # 获取数据库会话
        async with get_db_session() as db:
            service = MotionService(db)

            # 验证动作是否存在
            motion = await service.get_motion_by_id(uuid)
            if not motion:
                await websocket.accept()
                await websocket.send_json({
                    "type": "error",
                    "error": {
                        "code": "MOTION_NOT_FOUND",
                        "message": "动作不存在"
                    }
                })
                await websocket.close()
                return

            # 连接 WebSocket
            await stream_manager.connect(websocket, motion_id)

            # 发送连接确认
            await websocket.send_json({
                "type": "connected",
                "motion_id": str(motion.id),
                "total_frames": motion.keyframe_count,
                "fps": motion.original_fps
            })

            # 获取关键帧
            keyframes = await service.get_keyframes(uuid)

            # 流式发送每一帧
            for kf in keyframes:
                frame_data = {
                    "type": "frame",
                    "frame_index": kf.frame_index,
                    "timestamp": kf.timestamp,
                    "bone_data": kf.bone_data
                }
                await websocket.send_json(frame_data)

                # 根据帧率计算延迟 (模拟实时播放)
                # 实际使用时可以根据需要调整或移除延迟
                frame_delay = 1.0 / motion.original_fps
                await asyncio.sleep(frame_delay)

            # 发送完成消息
            await websocket.send_json({
                "type": "complete"
            })

        stream_manager.disconnect(motion_id)
            
    except WebSocketDisconnect:
        stream_manager.disconnect(motion_id)
    except Exception as e:
        try:
            await websocket.send_json({
                "type": "error",
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(e)
                }
            })
        except Exception:
            pass
        stream_manager.disconnect(motion_id)


@router.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket):
    """
    Agent WebSocket 端点
    
    功能：
    - 接收前端音频/文本输入
    - 推送口型帧 (lip_frame)
    - 推送动作帧 (motion_frame)
    - 推送文本消息 (text)
    - 推送状态更新 (status)
    
    消息协议：
    - 客户端发送: audio, text, interrupt, ping
    - 服务端推送: connected, lip_frame, motion_frame, text, status, pong
    """
    client_id = str(uuid.uuid4())
    
    try:
        # 连接 WebSocket 管理器
        await agent_ws_manager.connect(websocket, client_id)
        
        # 接收消息循环
        while True:
            try:
                # 尝试接收 JSON 消息
                message = await websocket.receive_json()
                await agent_ws_manager.handle_client_message(client_id, message)
                
            except json.JSONDecodeError:
                # 尝试接收二进制音频数据
                message = await websocket.receive()
                if "bytes" in message:
                    # 二进制数据作为音频处理
                    audio_data = message["bytes"]
                    if audio_data and agent_ws_manager._on_audio_received:
                        import base64
                        # base64 编码后调用回调
                        audio_b64 = base64.b64encode(audio_data).decode()
                        # 这里简化处理，直接传递 bytes
                        if agent_ws_manager._on_audio_received:
                            agent_ws_manager._on_audio_received(audio_data, client_id)
                            
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({
                "type": "error",
                "message": str(e)
            })
        except Exception:
            pass
    finally:
        await agent_ws_manager.disconnect(client_id)
