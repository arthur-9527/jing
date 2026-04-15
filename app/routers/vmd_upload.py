# -*- coding: utf-8 -*-
"""
VMD上传API路由
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List

from app.database import get_db
from app.services.vmd_upload_service import vmd_upload_service
from pydantic import BaseModel


router = APIRouter(prefix="/api/vmd", tags=["vmd_upload"])


class VMDUploadResponse(BaseModel):
    """上传响应"""
    success: bool = True
    upload_id: str
    status: str
    vmd_info: dict
    video_info: dict
    ai_result: Optional[dict] = None
    preview_url: Optional[str] = None


class RegenerateRequest(BaseModel):
    """重新生成请求"""
    text_prompt: Optional[str] = None


class RegenerateResponse(BaseModel):
    """重新生成响应"""
    success: bool = True
    upload_id: str
    ai_result: Optional[dict] = None
    regenerate_count: int


class ConfirmRequest(BaseModel):
    """确认保存请求"""
    display_name: Optional[str] = None
    tags: Optional[List[dict]] = None
    is_loopable: bool = False
    is_interruptible: bool = True


class ConfirmResponse(BaseModel):
    """确认保存响应"""
    success: bool = True
    motion_id: str
    name: str
    display_name: str
    status: str
    tags: List[dict]


class ErrorResponse(BaseModel):
    """错误响应"""
    success: bool = False
    error_code: str
    message: str


@router.post("/upload")
async def upload_vmd(
    vmd_file: UploadFile = File(..., description="VMD动作文件"),
    video_file: UploadFile = File(..., description="预览视频文件"),
    text_prompt: str = Form(..., description="生成文本描述"),
    db: AsyncSession = Depends(get_db)
):
    """
    上传VMD和视频文件，自动进行视频分析和标签生成
    
    返回AI分析结果供预览，用户确认后可保存入库
    """
    try:
        # 验证文件类型
        if not vmd_file.filename.lower().endswith('.vmd'):
            raise HTTPException(
                status_code=400,
                detail="Invalid VMD file format"
            )
        
        if not video_file.filename.lower().endswith(('.mp4', '.mov', '.avi', '.webm')):
            raise HTTPException(
                status_code=400,
                detail="Invalid video file format. Supported formats: mp4, mov, avi, webm"
            )
        
        # 读取文件数据
        vmd_data = await vmd_file.read()
        video_data = await video_file.read()
        
        # 上传并分析
        result = await vmd_upload_service.upload(
            vmd_data=vmd_data,
            vmd_filename=vmd_file.filename,
            video_data=video_data,
            video_filename=video_file.filename,
            text_prompt=text_prompt
        )
        
        return {
            "success": True,
            **result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Upload error: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error_code": "UPLOAD_ERROR",
                "message": str(e)
            }
        )


@router.post("/upload/{upload_id}/regenerate")
async def regenerate_analysis(
    upload_id: str,
    request: RegenerateRequest = RegenerateRequest(),
    db: AsyncSession = Depends(get_db)
):
    """
    重新生成AI分析结果
    
    不需要重新上传文件，使用已上传的视频重新分析
    """
    try:
        result = await vmd_upload_service.regenerate(
            upload_id=upload_id,
            new_text_prompt=request.text_prompt
        )
        
        return {
            "success": True,
            **result
        }
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        print(f"Regenerate error: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error_code": "REGENERATE_ERROR",
                "message": str(e)
            }
        )


@router.post("/upload/{upload_id}/confirm")
async def confirm_save(
    upload_id: str,
    request: ConfirmRequest = ConfirmRequest(),
    db: AsyncSession = Depends(get_db)
):
    """
    确认保存，将VMD数据入库
    
    使用AI生成的标签或用户指定的标签
    """
    try:
        result = await vmd_upload_service.confirm(
            upload_id=upload_id,
            db=db,
            display_name=request.display_name,
            tags_override=request.tags,
            is_loopable=request.is_loopable,
            is_interruptible=request.is_interruptible
        )
        
        return {
            "success": True,
            **result
        }
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        print(f"Confirm error: {e}")
        await db.rollback()
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error_code": "CONFIRM_ERROR",
                "message": str(e)
            }
        )


@router.get("/upload/{upload_id}")
async def get_upload_status(
    upload_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    获取上传状态和AI分析结果
    """
    try:
        result = vmd_upload_service.get_draft(upload_id)
        
        if result is None:
            raise HTTPException(status_code=404, detail="Upload not found")
        
        return {
            "success": True,
            **result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Get status error: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error_code": "STATUS_ERROR",
                "message": str(e)
            }
        )


@router.delete("/upload/{upload_id}")
async def delete_upload(
    upload_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    删除上传的临时文件
    """
    try:
        draft = vmd_upload_service.get_draft(upload_id)
        
        if draft is None:
            raise HTTPException(status_code=404, detail="Upload not found")
        
        # 清理草稿
        import shutil
        from pathlib import Path
        temp_dir = Path(f"/tmp/vmd_uploads/{upload_id}")
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        
        return {
            "success": True,
            "message": "Upload deleted"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Delete error: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error_code": "DELETE_ERROR",
                "message": str(e)
            }
        )