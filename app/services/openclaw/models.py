"""
OpenClaw WebSocket 服务数据模型

定义任务状态、Session状态、任务对象等核心数据结构。
"""

import time
import json
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Optional, Any, Dict
from datetime import datetime


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"           # 刚创建，等待分配session
    ASSIGNED = "assigned"         # 已分配session，等待发送到OpenClaw
    RUNNING = "running"           # 已发送chat.send，等待OpenClaw响应
    OPENCLAW_DONE = "openclaw_done"  # OpenClaw返回结果，等待LLM二次处理
    POST_PROCESSING = "post_processing"  # LLM二次处理中
    COMPLETED = "completed"       # 二次处理完成，最终结果可用
    FAILED = "failed"             # 执行出错
    TIMEOUT = "timeout"           # 超时
    CANCELLED = "cancelled"       # 用户取消


class SessionStatus(str, Enum):
    """Session状态枚举"""
    IDLE = "idle"             # 空闲，可分配新任务
    BUSY = "busy"             # 忙碌，正在执行任务


@dataclass
class Task:
    """OpenClaw任务数据模型"""
    id: str                              # 任务唯一ID (UUID)
    tool_prompt: str                     # LLM的工具调用提示
    session_key: Optional[str] = None    # 分配的session_key
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Dict[str, Any]] = None      # OpenClaw返回原始结果
    final_result: Optional[Dict[str, Any]] = None  # LLM二次处理后的最终结果
    error: Optional[str] = None          # 错误信息
    created_at: float = field(default_factory=time.time)
    assigned_at: Optional[float] = None
    started_at: Optional[float] = None
    openclaw_done_at: Optional[float] = None  # OpenClaw完成时间
    completed_at: Optional[float] = None  # 二次处理完成时间
    run_id: Optional[str] = None         # OpenClaw的runId
    retry_count: int = 0                 # 重试次数

    # 用于二次处理的上下文（任务提交时传入）
    user_input: Optional[str] = None     # 用户输入
    memory_context: Optional[str] = None # 记忆上下文
    conversation_history: Optional[str] = None  # 对话历史
    inner_monologue: Optional[str] = None # 第一阶段内心独白
    emotion_delta: Optional[Dict[str, float]] = None  # 情绪变化

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于Redis存储）"""
        data = {
            "id": self.id,
            "tool_prompt": self.tool_prompt,
            "session_key": self.session_key,
            "status": self.status.value,
            "result": json.dumps(self.result) if self.result else None,
            "final_result": json.dumps(self.final_result) if self.final_result else None,
            "error": self.error,
            "created_at": self.created_at,
            "assigned_at": self.assigned_at,
            "started_at": self.started_at,
            "openclaw_done_at": self.openclaw_done_at,
            "completed_at": self.completed_at,
            "run_id": self.run_id,
            "retry_count": self.retry_count,
            # 二次处理上下文
            "user_input": self.user_input,
            "memory_context": self.memory_context,
            "conversation_history": self.conversation_history,
            "inner_monologue": self.inner_monologue,
            "emotion_delta": json.dumps(self.emotion_delta) if self.emotion_delta else None,
        }
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        """从字典创建实例（从Redis读取）"""
        # 解析 JSON 字段
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

        result = parse_json_field(data.get("result"))
        final_result = parse_json_field(data.get("final_result"))
        emotion_delta = parse_json_field(data.get("emotion_delta"))

        # ⭐ 转换时间戳为float（Redis存储时可能是字符串）
        def _parse_time(value):
            if value is None:
                return None
            try:
                return float(value)
            except (ValueError, TypeError):
                return None

        return cls(
            id=data["id"],
            tool_prompt=data["tool_prompt"],
            session_key=data.get("session_key"),
            status=TaskStatus(data.get("status", TaskStatus.PENDING)),
            result=result,
            final_result=final_result,
            error=data.get("error"),
            created_at=float(data.get("created_at", time.time())),
            assigned_at=_parse_time(data.get("assigned_at")),
            started_at=_parse_time(data.get("started_at")),
            openclaw_done_at=_parse_time(data.get("openclaw_done_at")),
            completed_at=_parse_time(data.get("completed_at")),
            run_id=data.get("run_id"),
            retry_count=int(data.get("retry_count", 0)),
            # 二次处理上下文
            user_input=data.get("user_input"),
            memory_context=data.get("memory_context"),
            conversation_history=data.get("conversation_history"),
            inner_monologue=data.get("inner_monologue"),
            emotion_delta=emotion_delta,
        )

    def to_public_dict(self) -> Dict[str, Any]:
        """转换为公开字典（用于API返回，隐藏敏感信息）"""
        return {
            "id": self.id,
            "status": self.status.value,
            "session_key": self.session_key,
            "result": self.result,
            "error": self.error,
            "created_at": datetime.fromtimestamp(self.created_at).isoformat(),
            "completed_at": datetime.fromtimestamp(self.completed_at).isoformat()
            if self.completed_at else None,
            "duration": (
                self.completed_at - self.started_at
                if self.completed_at and self.started_at
                else None
            ),
        }

    def to_result_dict(self) -> Dict[str, Any]:
        """转换为结果字典（用于LLM调用返回）"""
        if self.status == TaskStatus.COMPLETED:
            return {
                "ok": True,
                "task_id": self.id,
                "session_key": self.session_key,
                "openclaw_result": self.result,  # OpenClaw原始结果
                "final_result": self.final_result,  # LLM二次处理后的最终结果
                "error": None,
            }
        elif self.status in (TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED):
            return {
                "ok": False,
                "task_id": self.id,
                "session_key": self.session_key,
                "openclaw_result": self.result,
                "final_result": None,
                "error": self.error or f"任务状态: {self.status.value}",
            }
        else:
            # 任务未完成，返回当前状态
            return {
                "ok": False,
                "task_id": self.id,
                "session_key": self.session_key,
                "status": self.status.value,
                "openclaw_result": self.result,
                "final_result": None,
                "error": f"任务未完成，当前状态: {self.status.value}",
            }


@dataclass
class SessionState:
    """Session状态（内存维护，不持久化）"""
    session_key: str                      # Session标识 (如 "agent:main:chat1")
    status: SessionStatus = SessionStatus.IDLE
    current_task_id: Optional[str] = None # 当前正在执行的任务ID
    last_used: float = field(default_factory=time.time)
    run_id: Optional[str] = None          # 当前OpenClaw runId

    def is_idle(self) -> bool:
        """检查是否空闲"""
        return self.status == SessionStatus.IDLE

    def is_busy(self) -> bool:
        """检查是否忙碌"""
        return self.status == SessionStatus.BUSY

    def assign_task(self, task_id: str) -> None:
        """分配任务"""
        self.status = SessionStatus.BUSY
        self.current_task_id = task_id
        self.last_used = time.time()

    def release(self) -> None:
        """释放任务"""
        self.status = SessionStatus.IDLE
        self.current_task_id = None
        self.run_id = None
        self.last_used = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "session_key": self.session_key,
            "status": self.status.value,
            "current_task_id": self.current_task_id,
            "last_used": self.last_used,
            "run_id": self.run_id,
        }


@dataclass
class ChatMessage:
    """OpenClaw chat消息格式"""
    content: list[Dict[str, Any]]  # [{"type": "text", "text": "..."}]

    def to_plain_text(self) -> str:
        """提取纯文本内容"""
        return "".join(
            item.get("text", "")
            for item in self.content
            if isinstance(item, dict) and item.get("type") == "text"
        )

    @classmethod
    def from_str(cls, text: str) -> "ChatMessage":
        """从字符串创建"""
        return cls(content=[{"type": "text", "text": text}])

    @classmethod
    def from_dict(cls, data: Any) -> "ChatMessage":
        """从字典创建（支持两种格式）"""
        if isinstance(data, str):
            return cls.from_str(data)
        elif isinstance(data, dict):
            content = data.get("content", [])
            if isinstance(content, list):
                return cls(content=content)
        return cls(content=[])

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {"content": self.content}


# 用于类型提示
TaskDict = Dict[str, Any]
SessionDict = Dict[str, Any]
