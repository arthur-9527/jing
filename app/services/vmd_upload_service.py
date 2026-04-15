# -*- coding: utf-8 -*-
"""
VMD上传服务 - 整合VMD解析、视频分析、数据库存储

完全参考 text2vmd 的方式存储：
- 对缺失帧进行 Bezier 曲线插值
- 过滤 identity bones
- 每帧都存储完整的骨骼数据
"""
import os
import uuid
import shutil
import json
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
import asyncio
from loguru import logger

from app.config import settings
from app.models.motion import Motion, Keyframe
from app.models.tag import MotionTag, MotionTagMap
from app.services.vmd_parser import VMDParser, VMDData, BoneFrameData
from app.services.video_analysis_service import VideoAnalysisService, VideoAnalysisResult
from app.services.embedding_service import EmbeddingService
from app.services.vmd_interpolation_service import vmd_interpolation_service


@dataclass
class VMDUploadDraft:
    """VMD上传草稿"""
    upload_id: str
    vmd_path: str
    video_path: str
    text_prompt: str
    vmd_info: Dict[str, Any]
    video_info: Dict[str, Any]
    ai_result: Optional[VideoAnalysisResult] = None
    regenerate_count: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    status: str = "uploaded"  # uploaded/analyzing/completed/error


@dataclass
class VMDInfo:
    """VMD文件信息"""
    name: str
    file_size: int
    total_frames: int
    duration: float
    fps: int
    bone_count: int


@dataclass
class VideoInfo:
    """视频文件信息"""
    duration: float
    width: int
    height: int
    fps: float


class VMDUploadService:
    """
    VMD上传服务
    
    工作流程:
    1. 上传文件 → 创建草稿
    2. 视频分析 → 生成标签
    3. 确认保存 → 入库
    """
    
    def __init__(self):
        self._drafts: Dict[str, VMDUploadDraft] = {}
        self._parser = VMDParser()
        self._video_service = VideoAnalysisService()
        self._embedding_service = EmbeddingService()
    
    def _ensure_temp_dir(self) -> Path:
        """确保临时目录存在"""
        temp_dir = Path(settings.VMD_UPLOAD_TEMP_DIR)
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir
    
    async def upload(
        self,
        vmd_data: bytes,
        vmd_filename: str,
        video_data: bytes,
        video_filename: str,
        text_prompt: str
    ) -> Dict[str, Any]:
        """
        上传VMD和视频文件，解析VMD，进行视频分析
        
        Args:
            vmd_data: VMD文件字节数据
            vmd_filename: VMD文件名
            video_data: 视频文件字节数据
            video_filename: 视频文件名
            text_prompt: 文本描述
            
        Returns:
            上传结果，包含草稿信息和AI分析结果
        """
        upload_id = str(uuid.uuid4())
        temp_dir = self._ensure_temp_dir() / upload_id
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        # 保存文件
        vmd_path = temp_dir / vmd_filename
        video_path = temp_dir / video_filename
        
        with open(vmd_path, "wb") as f:
            f.write(vmd_data)
        
        with open(video_path, "wb") as f:
            f.write(video_data)
        
        # 解析VMD
        vmd_info = self._parse_vmd(vmd_path)
        
        # 获取视频信息
        video_info = self._get_video_info(video_path)
        
        # 创建草稿
        draft = VMDUploadDraft(
            upload_id=upload_id,
            vmd_path=str(vmd_path),
            video_path=str(video_path),
            text_prompt=text_prompt,
            vmd_info={
                "name": vmd_filename,
                "file_size": len(vmd_data),
                "total_frames": vmd_info.total_frames,
                "duration": vmd_info.total_frames / 30.0,  # 默认30fps
                "fps": 30,
                "bone_count": self._count_unique_bones(vmd_info)
            },
            video_info={
                "duration": video_info.duration if video_info else vmd_info.total_frames / 30.0,
                "width": video_info.width if video_info else 0,
                "height": video_info.height if video_info else 0,
                "fps": video_info.fps if video_info else 30
            },
            status="uploaded"
        )
        
        self._drafts[upload_id] = draft
        
        # 进行视频分析（异步）
        try:
            draft.status = "analyzing"
            ai_result = await self._video_service.analyze_video(
                video_path=str(video_path),
                text_prompt=text_prompt,
                duration=draft.vmd_info["duration"]
            )
            draft.ai_result = ai_result
            draft.status = "completed"
        except Exception as e:
            print(f"Video analysis error: {e}")
            draft.status = "error"
        
        return self._build_upload_response(draft)
    
    async def regenerate(
        self,
        upload_id: str,
        new_text_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        重新生成AI分析结果（不重新上传文件）
        
        Args:
            upload_id: 上传ID
            new_text_prompt: 新的文本描述（可选）
            
        Returns:
            新的AI分析结果
        """
        if upload_id not in self._drafts:
            raise ValueError(f"Upload not found: {upload_id}")
        
        draft = self._drafts[upload_id]
        
        # 检查重新生成次数
        if draft.regenerate_count >= 5:
            raise ValueError("Maximum regenerate count exceeded")
        
        # 更新文本描述
        if new_text_prompt:
            draft.text_prompt = new_text_prompt
        
        # 重新分析
        try:
            draft.status = "analyzing"
            ai_result = await self._video_service.analyze_video(
                video_path=draft.video_path,
                text_prompt=draft.text_prompt,
                duration=draft.vmd_info["duration"]
            )
            draft.ai_result = ai_result
            draft.regenerate_count += 1
            draft.status = "completed"
        except Exception as e:
            print(f"Regenerate error: {e}")
            draft.status = "error"
        
        return {
            "upload_id": upload_id,
            "ai_result": self._build_ai_result(draft.ai_result) if draft.ai_result else None,
            "regenerate_count": draft.regenerate_count
        }
    
    async def confirm(
        self,
        upload_id: str,
        db: AsyncSession,
        display_name: Optional[str] = None,
        tags_override: Optional[List[Dict[str, Any]]] = None,
        is_loopable: bool = False,
        is_interruptible: bool = True
    ) -> Dict[str, Any]:
        """
        确认保存，将数据入库
        
        完全参考 text2vmd 的方式：
        - 对缺失帧进行 Bezier 曲线插值
        - 过滤 identity bones
        - 每帧都存储完整的骨骼数据
        
        Args:
            upload_id: 上传ID
            db: 数据库会话
            display_name: 显示名称（可选）
            tags_override: 标签覆盖（可选）
            is_loopable: 是否可循环
            is_interruptible: 是否可中断
            
        Returns:
            保存后的motion信息
        """
        if upload_id not in self._drafts:
            raise ValueError(f"Upload not found: {upload_id}")
        
        draft = self._drafts[upload_id]
        
        if draft.status != "completed":
            raise ValueError(f"Upload not completed: {draft.status}")
        
        # 读取VMD数据
        with open(draft.vmd_path, "rb") as f:
            vmd_data = f.read()
        
        # 解析 VMD
        vmd_parsed = self._parser.parse_bytes(vmd_data)
        
        # 使用插值服务处理 VMD 数据（参考 text2vmd）
        # 这一步会：
        # 1. 对缺失帧进行 Bezier 曲线插值
        # 2. 过滤 identity bones
        # 3. 返回每帧都有完整骨骼数据的帧字典
        frames_dict, frames_interpolated, bones_filtered = vmd_interpolation_service.process_vmd(vmd_parsed)
        
        logger.info(f"[VMDUpload] 处理完成: 插值帧={frames_interpolated}, 过滤骨骼={bones_filtered}")
        
        # 获取AI结果或使用默认
        ai_result = draft.ai_result
        if ai_result is None:
            ai_result = VideoAnalysisResult(
                suggested_name=display_name or draft.vmd_info["name"],
                description=draft.text_prompt[:100] if draft.text_prompt else "动作描述",
                tags=[],
                confidence=0.5
            )
        
        # 确定显示名称
        final_name = display_name or ai_result.suggested_name or draft.vmd_info["name"]
        
        # 生成embedding
        embed_text = f"{final_name}. {ai_result.description}"
        embedding = await self._embedding_service.get_embedding(embed_text)
        
        # 计算实际保存的帧数和时长
        final_frame_count = len(frames_dict)
        fps = draft.vmd_info["fps"]  # VMD 的原始帧率（通常是 30fps）
        final_duration = final_frame_count / fps if fps > 0 else 0
        
        # 创建motion记录
        motion = Motion(
            name=draft.vmd_info["name"],
            display_name=final_name,
            description=ai_result.description,
            original_fps=fps,
            original_frames=final_frame_count,
            original_duration=final_duration,
            keyframe_count=final_frame_count,  # 现在每一帧都是关键帧
            is_loopable=is_loopable,
            is_interruptible=is_interruptible,
            status="active",
            embedding=embedding,
            source_file=draft.video_path
        )
        
        db.add(motion)
        await db.flush()
        
        # 创建关键帧记录 - 按 text2vmd 的格式存储
        # 每帧都存储完整的骨骼数据
        keyframes = []
        for frame_idx in sorted(frames_dict.keys()):
            bone_data = frames_dict[frame_idx]
            
            # 计算时间戳
            timestamp = frame_idx / fps if fps > 0 else 0
            
            # 按 text2vmd 的格式存储：{"bone_name": {"trans": [...], "quat": [...]}}
            # bone_data 已经是这个格式
            kf = Keyframe(
                motion_id=motion.id,
                frame_index=frame_idx,
                original_frame=frame_idx,
                timestamp=timestamp,
                bone_data=bone_data
            )
            keyframes.append(kf)
        
        if keyframes:
            db.add_all(keyframes)
            logger.info(f"[VMDUpload] 保存了 {len(keyframes)} 帧关键帧数据")
        
        # 创建标签记录
        tags_to_use = tags_override if tags_override else (ai_result.tags if ai_result else [])
        await self._create_tags(db, motion.id, tags_to_use)
        
        await db.commit()
        
        # 清理草稿
        self._cleanup_draft(upload_id)
        
        logger.info(f"[VMDUpload] Motion 保存成功: {motion.id}")
        
        return {
            "motion_id": str(motion.id),
            "name": motion.name,
            "display_name": motion.display_name,
            "status": motion.status,
            "tags": tags_to_use,
            "frames_saved": len(keyframes),
            "frames_interpolated": frames_interpolated,
            "bones_filtered": bones_filtered
        }
    
    def get_draft(self, upload_id: str) -> Optional[Dict[str, Any]]:
        """获取草稿状态"""
        if upload_id not in self._drafts:
            return None
        
        draft = self._drafts[upload_id]
        return self._build_upload_response(draft)
    
    def _build_upload_response(self, draft: VMDUploadDraft) -> Dict[str, Any]:
        """构建上传响应"""
        return {
            "upload_id": draft.upload_id,
            "status": draft.status,
            "vmd_info": draft.vmd_info,
            "video_info": draft.video_info,
            "ai_result": self._build_ai_result(draft.ai_result) if draft.ai_result else None,
            "preview_url": None  # TODO: 上传到OSS后返回URL
        }
    
    def _build_ai_result(self, ai_result: VideoAnalysisResult) -> Dict[str, Any]:
        """构建AI分析结果"""
        return {
            "suggested_name": ai_result.suggested_name,
            "description": ai_result.description,
            "tags": ai_result.tags,
            "confidence": ai_result.confidence
        }
    
    def _parse_vmd(self, vmd_path: Path) -> VMDData:
        """解析VMD文件"""
        return self._parser.parse(str(vmd_path))
    
    def _count_unique_bones(self, vmd_data: VMDData) -> int:
        """统计唯一骨骼数量"""
        bones = set()
        for bf in vmd_data.bone_frames:
            bones.add(bf.bone_name)
        return len(bones)
    
    def _get_video_info(self, video_path: Path) -> Optional[VideoInfo]:
        """获取视频信息"""
        try:
            import cv2
            cap = cv2.VideoCapture(str(video_path))
            if not cap.isOpened():
                return None
            
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            
            cap.release()
            
            duration = frame_count / fps if fps > 0 else 0
            
            return VideoInfo(
                duration=duration,
                width=width,
                height=height,
                fps=fps
            )
        except ImportError:
            return None
        except Exception as e:
            print(f"Video info error: {e}")
            return None
    
    async def _create_tags(
        self,
        db: AsyncSession,
        motion_id: str,
        tags: List[Dict[str, Any]]
    ):
        """创建或获取标签，并建立关联（同时生成标签向量）"""
        for tag_info in tags:
            tag_type = tag_info.get("type", "action")
            tag_name = tag_info.get("name", "")
            display_name = tag_info.get("display_name", tag_name)
            weight = tag_info.get("weight", 1.0)
            
            if not tag_name:
                continue
            
            # 查找或创建标签
            stmt = select(MotionTag).where(
                MotionTag.tag_type == tag_type,
                MotionTag.tag_name == tag_name
            )
            result = await db.execute(stmt)
            tag = result.scalar_one_or_none()
            
            if tag is None:
                # 创建新标签并生成 embedding
                tag_embedding = await self._embedding_service.get_embedding(tag_name)
                tag = MotionTag(
                    tag_type=tag_type,
                    tag_name=tag_name,
                    display_name=display_name,
                    embedding=tag_embedding
                )
                db.add(tag)
                await db.flush()
            elif tag.embedding is None:
                # 补生成缺失的 embedding
                tag_embedding = await self._embedding_service.get_embedding(tag_name)
                tag.embedding = tag_embedding
                await db.flush()
            
            # 创建关联
            tag_map = MotionTagMap(
                motion_id=motion_id,
                tag_id=tag.id,
                weight=weight
            )
            db.add(tag_map)
    
    def _cleanup_draft(self, upload_id: str):
        """清理草稿文件"""
        if upload_id in self._drafts:
            draft = self._drafts[upload_id]
            draft_dir = Path(draft.vmd_path).parent
            if draft_dir.exists():
                shutil.rmtree(draft_dir, ignore_errors=True)
            del self._drafts[upload_id]
    
    def cleanup_expired_drafts(self):
        """清理过期的草稿"""
        expired_ids = []
        ttl = timedelta(hours=settings.VMD_UPLOAD_TTL_HOURS)
        now = datetime.now()
        
        for upload_id, draft in self._drafts.items():
            if now - draft.created_at > ttl:
                expired_ids.append(upload_id)
        
        for upload_id in expired_ids:
            self._cleanup_draft(upload_id)


# 全局单例
vmd_upload_service = VMDUploadService()