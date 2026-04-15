"""Agent 相关 API 路由"""

from fastapi import APIRouter, HTTPException
from uuid import UUID
from pydantic import BaseModel
from loguru import logger
from typing import Optional

router = APIRouter(prefix="/api/agent", tags=["agent"])


class TriggerMotionRequest(BaseModel):
    """触发动作请求"""
    motion_id: UUID


class TriggerMotionResponse(BaseModel):
    """触发动作响应"""
    success: bool = True


@router.post("/trigger-motion", response_model=TriggerMotionResponse)
async def trigger_motion(request: TriggerMotionRequest):
    """接收 OpenClaw 触发的动作 ID

    仅记录日志，不做验证和处理。
    """
    logger.info(f"[Agent] 收到动作触发请求：motion_id={request.motion_id}")
    return TriggerMotionResponse()


# ===== 状态管理 API =====

class StateResponse(BaseModel):
    """状态响应"""
    current_state: str
    state_duration: float
    is_idle: bool
    is_speaking: bool


class StateStatsResponse(BaseModel):
    """状态统计响应"""
    current_state: str
    state_duration: float
    tts_start_count: int
    tts_stop_count: int
    waiting_for_lip_empty: bool
    total_transitions: int
    state_durations: dict


class StateHistoryResponse(BaseModel):
    """状态历史响应"""
    events: list[dict]
    count: int


@router.get("/state", response_model=StateResponse)
async def get_current_state():
    """获取当前 Agent 状态"""
    from app.services.agent_service import get_agent_service
    
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


@router.get("/state/stats", response_model=StateStatsResponse)
async def get_state_stats():
    """获取状态统计信息"""
    from app.services.agent_service import get_agent_service
    
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


@router.get("/state/history", response_model=StateHistoryResponse)
async def get_state_history(limit: int = 10):
    """获取状态转换历史
    
    Args:
        limit: 返回的历史记录数量，默认10条
    """
    from app.services.agent_service import get_agent_service
    
    service = get_agent_service()
    if not service or not service._state_manager:
        raise HTTPException(status_code=503, detail="Agent service not initialized")
    
    state_manager = service._state_manager
    events = state_manager.get_state_history(limit=limit)
    
    return StateHistoryResponse(
        events=events,
        count=len(events),
    )


@router.post("/state/reset")
async def reset_state():
    """强制重置状态到 IDLE（用于错误恢复）"""
    from app.services.agent_service import get_agent_service
    
    service = get_agent_service()
    if not service or not service._state_manager:
        raise HTTPException(status_code=503, detail="Agent service not initialized")
    
    state_manager = service._state_manager
    await state_manager.force_to_idle()
    
    logger.info("[Agent] 状态已强制重置到 IDLE")
    return {"success": True, "current_state": "idle"}
