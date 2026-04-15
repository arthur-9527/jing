"""
任务系统数据模型 - 任务状态、任务对象、结果结构

核心数据结构：
1. TaskStatus: 主队列任务状态枚举
2. Task: 主队列任务对象
3. ProviderResult: Provider 返回的原始结果
4. BroadcastContent: 播报内容（二次改写后）
"""

import time
import json
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Optional, Any, Dict
from datetime import datetime


class TaskStatus(str, Enum):
    """主队列任务状态枚举
    
    状态流转：
    PENDING → SUBMITTED → RUNNING → PROVIDER_DONE → POST_PROCESSING → COMPLETED
    
    异常状态：
    FAILED: Provider 执行失败
    CANCELLED: 用户取消
    TIMEOUT: 超时
    """
    PENDING = "pending"                 # 刚创建，等待分配
    SUBMITTED = "submitted"             # 已提交给 Provider
    RUNNING = "running"                 # Provider 正在执行
    PROVIDER_DONE = "provider_done"     # Provider 返回原始结果
    POST_PROCESSING = "post_processing" # 二次改写中
    COMPLETED = "completed"             # 完成，已入播报队列
    FAILED = "failed"                   # 执行失败
    CANCELLED = "cancelled"             # 用户取消
    TIMEOUT = "timeout"                 # 超时


@dataclass
class Task:
    """主队列任务对象
    
    Attributes:
        id: 任务唯一 ID (UUID)
        tool_prompt: LLM 的工具调用提示
        provider_name: 执行的 Provider 名称
        status: 任务状态
        context: 任务上下文（用于二次改写）
        provider_result: Provider 返回的原始结果
        broadcast_content: 二次改写后的播报内容
        error: 错误信息
        created_at: 创建时间
        submitted_at: 提交给 Provider 的时间
        started_at: Provider 开始执行的时间
        provider_done_at: Provider 返回结果的时间
        completed_at: 完成时间
    """
    id: str
    tool_prompt: str
    provider_name: str = "openclaw"
    status: TaskStatus = TaskStatus.PENDING
    
    # 任务上下文（用于二次改写）
    context: Dict[str, Any] = field(default_factory=dict)
    
    # 结果
    provider_result: Optional[Dict[str, Any]] = None  # Provider 原始结果
    broadcast_content: Optional[Dict[str, Any]] = None  # 二次改写后的播报内容
    
    # 错误信息
    error: Optional[str] = None
    
    # 时间戳
    created_at: float = field(default_factory=time.time)
    submitted_at: Optional[float] = None
    started_at: Optional[float] = None
    provider_done_at: Optional[float] = None
    completed_at: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于 Redis 存储）
        
        Note: Redis hset 不接受 None 值，需要转换为空字符串
        """
        return {
            "id": self.id,
            "tool_prompt": self.tool_prompt,
            "provider_name": self.provider_name,
            "status": self.status.value,
            "context": json.dumps(self.context) if self.context else "",
            "provider_result": json.dumps(self.provider_result) if self.provider_result else "",
            "broadcast_content": json.dumps(self.broadcast_content) if self.broadcast_content else "",
            "error": self.error or "",
            "created_at": str(self.created_at),
            "submitted_at": str(self.submitted_at) if self.submitted_at else "",
            "started_at": str(self.started_at) if self.started_at else "",
            "provider_done_at": str(self.provider_done_at) if self.provider_done_at else "",
            "completed_at": str(self.completed_at) if self.completed_at else "",
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        """从字典创建实例（从 Redis 读取）"""
        def parse_json_field(value):
            if value is None:
                return None
            if isinstance(value, (dict, list)):
                return value
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    return None
            return None
        
        def parse_time(value):
            if value is None:
                return None
            try:
                return float(value)
            except (ValueError, TypeError):
                return None
        
        return cls(
            id=data["id"],
            tool_prompt=data["tool_prompt"],
            provider_name=data.get("provider_name", "openclaw"),
            status=TaskStatus(data.get("status", TaskStatus.PENDING)),
            context=parse_json_field(data.get("context")) or {},
            provider_result=parse_json_field(data.get("provider_result")),
            broadcast_content=parse_json_field(data.get("broadcast_content")),
            error=data.get("error"),
            created_at=float(data.get("created_at", time.time())),
            submitted_at=parse_time(data.get("submitted_at")),
            started_at=parse_time(data.get("started_at")),
            provider_done_at=parse_time(data.get("provider_done_at")),
            completed_at=parse_time(data.get("completed_at")),
        )
    
    def to_public_dict(self) -> Dict[str, Any]:
        """转换为公开字典（用于 API 返回）"""
        return {
            "id": self.id,
            "status": self.status.value,
            "provider_name": self.provider_name,
            "error": self.error,
            "created_at": datetime.fromtimestamp(self.created_at).isoformat(),
            "completed_at": datetime.fromtimestamp(self.completed_at).isoformat()
            if self.completed_at else None,
        }


@dataclass
class ProviderResult:
    """Provider 返回的原始结果
    
    Attributes:
        task_id: 任务 ID
        success: 是否成功
        content: 原始内容（未二次改写）
        panel_html: 原始 Panel（未二次处理）
        error: 错误信息
        metadata: Provider 附加元数据（可选）
    """
    task_id: str
    success: bool
    content: str
    panel_html: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于同步传输）"""
        return {
            "task_id": self.task_id,
            "success": self.success,
            "content": self.content,
            "panel_html": self.panel_html,
            "error": self.error,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProviderResult":
        """从字典创建实例"""
        return cls(
            task_id=data.get("task_id", ""),
            success=data.get("success", False),
            content=data.get("content", ""),
            panel_html=data.get("panel_html"),
            error=data.get("error"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class BroadcastContent:
    """播报内容（任务输出，二次改写后）
    
    Attributes:
        task_id: 任务 ID
        content: 最终台词（二次改写后）
        panel_html: 最终 Panel（位置处理后）
        action: 可选动作
    """
    task_id: str
    content: str
    panel_html: Optional[Dict[str, Any]] = None
    action: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于播报队列）"""
        return {
            "task_id": self.task_id,
            "content": self.content,
            "panel_html": self.panel_html,
            "action": self.action,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BroadcastContent":
        """从字典创建实例"""
        return cls(
            task_id=data.get("task_id", ""),
            content=data.get("content", ""),
            panel_html=data.get("panel_html"),
            action=data.get("action"),
        )
    
    def to_playback_task(self) -> Dict[str, Any]:
        """转换为播报队列任务格式（兼容 PlaybackScheduler）"""
        return {
            "id": self.task_id,
            "content": self.content,
            "panel_html": self.panel_html,
            "action": self.action,
        }