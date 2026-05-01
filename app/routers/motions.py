"""动作相关 API 路由 - 使用 Stone 数据层"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from uuid import UUID

from app.services.motion_service import MotionService
from app.services.search_service import SearchService
from app.agent.memory.embedding import get_embedding
from app.schemas.motion import (
    MotionListQuery,
    MotionListResponse,
    MotionListItem,
    MotionDetailResponse,
    MotionDetail,
    KeyframeResponse,
    KeyframeItem,
    SingleKeyframeResponse,
)
from app.schemas.search import (
    TagSearchQuery,
    TagSearchResponse,
    TagSearchResult,
    SemanticSearchQuery,
    SemanticSearchResponse,
    SemanticSearchResult,
)

router = APIRouter(prefix="/api/motions", tags=["motions"])


@router.get("", response_model=MotionListResponse)
async def get_motion_list(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: Optional[str] = None,
    is_loopable: Optional[bool] = None,
    min_duration: Optional[float] = None,
    max_duration: Optional[float] = None,
):
    """获取动作列表"""
    query = MotionListQuery(
        page=page,
        page_size=page_size,
        status=status,
        is_loopable=is_loopable,
        min_duration=min_duration,
        max_duration=max_duration,
    )

    service = MotionService()
    motions, total = await service.get_motion_list(query)

    return MotionListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[
            MotionListItem(
                id=m['id'],
                name=m['name'],
                display_name=m['display_name'],
                original_duration=m['original_duration'],
                keyframe_count=m['keyframe_count'],
                is_loopable=m['is_loopable'],
                is_interruptible=m['is_interruptible'],
                status=m['status'],
            )
            for m in motions
        ],
    )


@router.get("/{motion_id}", response_model=MotionDetailResponse)
async def get_motion_detail(
    motion_id: UUID,
):
    """获取动作详情"""
    service = MotionService()
    motion = await service.get_motion_with_tags(motion_id)

    if not motion:
        raise HTTPException(status_code=404, detail="动作不存在")

    # 构建标签列表
    tags = []
    for tag in motion.get('tags', []):
        tags.append({
            "tag_type": tag['tag_type'],
            "tag_name": tag['tag_name'],
            "weight": tag.get('weight', 1.0),
        })

    return MotionDetailResponse(
        data=MotionDetail(
            id=motion['id'],
            name=motion['name'],
            display_name=motion['display_name'],
            description=motion['description'],
            original_fps=motion['original_fps'],
            original_frames=motion['original_frames'],
            original_duration=motion['original_duration'],
            keyframe_count=motion['keyframe_count'],
            is_loopable=motion['is_loopable'],
            is_interruptible=motion['is_interruptible'],
            status=motion['status'],
            source_file=motion['source_file'],
            tags=tags,
        )
    )


@router.get("/{motion_id}/keyframes", response_model=KeyframeResponse)
async def get_keyframes(
    motion_id: UUID,
    start_frame: Optional[int] = Query(default=None),
    end_frame: Optional[int] = Query(default=None),
):
    """获取动作关键帧"""
    service = MotionService()

    # 验证动作是否存在
    motion = await service.get_motion_by_id(motion_id)
    if not motion:
        raise HTTPException(status_code=404, detail="动作不存在")

    keyframes = await service.get_keyframes(motion_id, start_frame, end_frame)

    return KeyframeResponse(
        motion_id=motion_id,
        motion_name=motion['name'],
        total_frames=motion['keyframe_count'],
        fps=motion['original_fps'],
        keyframes=[
            KeyframeItem(
                frame_index=kf['frame_index'],
                original_frame=kf['original_frame'],
                timestamp=kf['timestamp'],
                bone_data=kf['bone_data'],
            )
            for kf in keyframes
        ],
    )


@router.get("/{motion_id}/keyframes/{frame_index}", response_model=SingleKeyframeResponse)
async def get_single_keyframe(
    motion_id: UUID,
    frame_index: int,
):
    """获取单帧数据"""
    service = MotionService()

    # 验证动作是否存在
    motion = await service.get_motion_by_id(motion_id)
    if not motion:
        raise HTTPException(status_code=404, detail="动作不存在")

    keyframe = await service.get_single_keyframe(motion_id, frame_index)
    if not keyframe:
        raise HTTPException(status_code=404, detail="帧不存在")

    return SingleKeyframeResponse(
        motion_id=motion_id,
        frame_index=keyframe['frame_index'],
        original_frame=keyframe['original_frame'],
        timestamp=keyframe['timestamp'],
        bone_data=keyframe['bone_data'],
    )


@router.get("/search/by-tags", response_model=TagSearchResponse)
async def search_by_tags(
    emotion: Optional[str] = None,
    action: Optional[str] = None,
    scene: Optional[str] = None,
    intensity: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=100),
):
    """根据标签搜索动作"""
    query = TagSearchQuery(
        emotion=emotion,
        action=action,
        scene=scene,
        intensity=intensity,
        limit=limit,
    )

    search_service = SearchService()
    results = await search_service.search_by_tags(query)

    # 构建查询参数用于返回
    query_params = {}
    if emotion:
        query_params["emotion"] = emotion
    if action:
        query_params["action"] = action
    if scene:
        query_params["scene"] = scene
    if intensity:
        query_params["intensity"] = intensity

    return TagSearchResponse(
        query=query_params,
        results=[
            TagSearchResult(
                id=r["id"],
                name=r["name"],
                display_name=r["display_name"],
                original_duration=r["original_duration"],
                match_score=r["match_score"],
                matched_tags=r["matched_tags"],
            )
            for r in results
        ],
    )


@router.get("/search/semantic", response_model=SemanticSearchResponse)
async def search_semantic(
    query: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(default=10, ge=1, le=100),
):
    """语义搜索动作"""
    search_service = SearchService()

    # 获取查询文本的 embedding
    embedding = await get_embedding(query)

    if not embedding:
        # 如果没有 embedding API，返回空结果
        return SemanticSearchResponse(
            query=query,
            results=[]
        )

    # 使用 embedding 搜索
    results = await search_service.search_semantic(embedding, limit)

    return SemanticSearchResponse(
        query=query,
        results=[
            SemanticSearchResult(
                id=r["id"],
                name=r["name"],
                display_name=r["display_name"],
                original_duration=r["original_duration"],
                similarity=r["similarity"],
            )
            for r in results
        ],
    )