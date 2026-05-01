"""实时语音流路由 - WebSocket + Agent State API

合并原 app/routers/ws.py 和 app/routers/agent.py 的内容。
"""

import asyncio
import json
import uuid
from typing import Dict, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID as _UUID
from pydantic import BaseModel
from loguru import logger

from app.stone import get_database
from app.services.motion_service import MotionService
from app.realtime.agent_ws_manager import agent_ws_manager, AgentStatus
from app.realtime.agent_service import get_agent_service

router = APIRouter(tags=["realtime"])


# ============================================================================
# WebSocket - 动作流
# ============================================================================

class MotionStreamManager:
    """动作流管理器"""

    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, motion_id: str):
        await websocket.accept()
        self.active_connections[motion_id] = websocket

    def disconnect(self, motion_id: str):
        if motion_id in self.active_connections:
            del self.active_connections[motion_id]

    async def send_frame(self, motion_id: str, frame_data: dict):
        if motion_id in self.active_connections:
            try:
                await self.active_connections[motion_id].send_json(frame_data)
            except Exception:
                self.disconnect(motion_id)


stream_manager = MotionStreamManager()


@router.websocket("/ws/echo")
async def echo_websocket(websocket: WebSocket):
    """Echo WebSocket 测试端点"""
    try:
        await websocket.accept()
        await websocket.send_json({
            "type": "connected",
            "message": "WebSocket echo test connected"
        })
        while True:
            message = await websocket.receive_text()
            await websocket.send_json({
                "type": "echo",
                "original": message,
                "timestamp": asyncio.get_event_loop().time()
            })
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


@router.websocket("/ws/motion-stream")
async def motion_stream(
    websocket: WebSocket,
    motion_id: str = Query(..., description="动作 ID"),
):
    """实时动作流 WebSocket"""
    try:
        try:
            uuid_obj = _UUID(motion_id)
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

        async with get_database().get_session() as db:
            service = MotionService(db)
            motion = await service.get_motion_by_id(uuid_obj)
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

            await stream_manager.connect(websocket, motion_id)
            await websocket.send_json({
                "type": "connected",
                "motion_id": str(motion.id),
                "total_frames": motion.keyframe_count,
                "fps": motion.original_fps
            })

            keyframes = await service.get_keyframes(uuid_obj)
            for kf in keyframes:
                frame_data = {
                    "type": "frame",
                    "frame_index": kf.frame_index,
                    "timestamp": kf.timestamp,
                    "bone_data": kf.bone_data
                }
                await websocket.send_json(frame_data)
                frame_delay = 1.0 / motion.original_fps
                await asyncio.sleep(frame_delay)

            await websocket.send_json({"type": "complete"})

        stream_manager.disconnect(motion_id)

    except WebSocketDisconnect:
        stream_manager.disconnect(motion_id)
    except Exception as e:
        try:
            await websocket.send_json({
                "type": "error",
                "error": {"code": "INTERNAL_ERROR", "message": str(e)}
            })
        except Exception:
            pass
        stream_manager.disconnect(motion_id)


@router.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket):
    """Agent WebSocket 端点

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
        await agent_ws_manager.connect(websocket, client_id)

        while True:
            try:
                message = await websocket.receive_json()
                await agent_ws_manager.handle_client_message(client_id, message)

            except json.JSONDecodeError:
                message = await websocket.receive()
                if "bytes" in message:
                    audio_data = message["bytes"]
                    if audio_data and agent_ws_manager._on_audio_received:
                        agent_ws_manager._on_audio_received(audio_data, client_id)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        await agent_ws_manager.disconnect(client_id)


# ============================================================================
# Agent State API
# ============================================================================


class TriggerMotionRequest(BaseModel):
    motion_id: _UUID


class TriggerMotionResponse(BaseModel):
    success: bool = True


class StateResponse(BaseModel):
    current_state: str
    state_duration: float
    is_idle: bool
    is_speaking: bool


class StateStatsResponse(BaseModel):
    current_state: str
    state_duration: float
    tts_start_count: int
    tts_stop_count: int
    waiting_for_lip_empty: bool
    total_transitions: int
    state_durations: dict


class StateHistoryResponse(BaseModel):
    events: list[dict]
    count: int


@router.post("/api/agent/trigger-motion", response_model=TriggerMotionResponse)
async def trigger_motion(request: TriggerMotionRequest):
    """接收 OpenClaw 触发的动作 ID"""
    logger.info(f"[Agent] 收到动作触发请求：motion_id={request.motion_id}")
    return TriggerMotionResponse()


@router.get("/api/agent/state", response_model=StateResponse)
async def get_current_state():
    """获取当前 Agent 状态"""
    service = get_agent_service()
    if not service or not service._state_manager:
        raise HTTPException(status_code=503, detail="Agent service not initialized")

    state_manager = service._state_manager
    return StateResponse(
        current_state=state_manager.current_state.value,
        state_duration=round(state_manager.state_duration, 2),
        is_idle=state_manager.is_idle,
        is_speaking=state_manager.is_speaking,
    )


@router.get("/api/agent/state/stats", response_model=StateStatsResponse)
async def get_state_stats():
    """获取状态统计信息"""
    service = get_agent_service()
    if not service or not service._state_manager:
        raise HTTPException(status_code=503, detail="Agent service not initialized")

    state_manager = service._state_manager
    stats = state_manager.get_stats()

    return StateStatsResponse(
        current_state=stats["current_state"],
        state_duration=stats["state_duration"],
        tts_start_count=stats["tts_start_count"],
        tts_stop_count=stats["tts_stop_count"],
        waiting_for_lip_empty=stats["waiting_for_lip_empty"],
        total_transitions=stats["total_transitions"],
        state_durations=stats["state_durations"],
    )


@router.get("/api/agent/state/history", response_model=StateHistoryResponse)
async def get_state_history(limit: int = 10):
    """获取状态转换历史"""
    service = get_agent_service()
    if not service or not service._state_manager:
        raise HTTPException(status_code=503, detail="Agent service not initialized")

    state_manager = service._state_manager
    events = state_manager.get_state_history(limit=limit)

    return StateHistoryResponse(
        events=events,
        count=len(events),
    )


@router.post("/api/agent/state/reset")
async def reset_state():
    """强制重置状态到 IDLE（用于错误恢复）"""
    service = get_agent_service()
    if not service or not service._state_manager:
        raise HTTPException(status_code=503, detail="Agent service not initialized")

    state_manager = service._state_manager
    await state_manager.force_to_idle()

    logger.info("[Agent] 状态已强制重置到 IDLE")
    return {"success": True, "current_state": "idle"}
