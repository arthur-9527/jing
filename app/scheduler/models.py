"""定时任务模型定义"""

from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field


class JobTriggerType(str, Enum):
    """任务触发器类型"""
    CRON = "cron"          # Cron 表达式触发
    INTERVAL = "interval"  # 固定间隔触发
    DATE = "date"          # 指定时间一次性触发


class JobInfo(BaseModel):
    """任务信息模型"""
    job_id: str = Field(..., description="任务唯一标识")
    name: str = Field(..., description="任务名称")
    description: Optional[str] = Field(None, description="任务描述")
    trigger_type: JobTriggerType = Field(..., description="触发器类型")
    trigger_config: dict[str, Any] = Field(default_factory=dict, description="触发器配置")
    enabled: bool = Field(default=True, description="是否启用")
    next_run_time: Optional[datetime] = Field(None, description="下次执行时间")
    last_run_time: Optional[datetime] = Field(None, description="上次执行时间")
    
    class Config:
        use_enum_values = True


class CronTriggerConfig(BaseModel):
    """Cron 触发器配置"""
    year: Optional[str] = None
    month: Optional[str] = None
    day: Optional[str] = None
    week: Optional[str] = None
    day_of_week: Optional[str] = None
    hour: Optional[str] = "0"
    minute: Optional[str] = "0"
    second: Optional[str] = "0"
    
    def to_dict(self) -> dict:
        """转换为字典，过滤 None 值"""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class IntervalTriggerConfig(BaseModel):
    """间隔触发器配置"""
    weeks: Optional[int] = None
    days: Optional[int] = None
    hours: Optional[int] = None
    minutes: Optional[int] = None
    seconds: Optional[int] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    
    def to_dict(self) -> dict:
        """转换为字典，过滤 None 值"""
        result = {}
        for k, v in self.model_dump().items():
            if v is not None:
                if isinstance(v, datetime):
                    result[k] = v
                else:
                    result[k] = v
        return result


class DateTriggerConfig(BaseModel):
    """一次性日期触发器配置"""
    run_date: datetime = Field(..., description="执行时间")
    
    def to_dict(self) -> dict:
        return {"run_date": self.run_date}


class JobCreateRequest(BaseModel):
    """创建任务请求"""
    job_id: str = Field(..., description="任务唯一标识")
    name: str = Field(..., description="任务名称")
    description: Optional[str] = Field(None, description="任务描述")
    trigger_type: JobTriggerType = Field(..., description="触发器类型")
    trigger_config: dict[str, Any] = Field(..., description="触发器配置")
    enabled: bool = Field(default=True, description="是否立即启用")
    args: Optional[list[Any]] = Field(default=None, description="任务函数参数")
    kwargs: Optional[dict[str, Any]] = Field(default=None, description="任务函数关键字参数")


class JobUpdateRequest(BaseModel):
    """更新任务请求"""
    name: Optional[str] = None
    description: Optional[str] = None
    trigger_type: Optional[JobTriggerType] = None
    trigger_config: Optional[dict[str, Any]] = None
    enabled: Optional[bool] = None


class JobStatusResponse(BaseModel):
    """任务状态响应"""
    job_id: str
    name: str
    description: Optional[str]
    trigger_type: str
    trigger_config: dict[str, Any]
    enabled: bool
    next_run_time: Optional[datetime]
    last_run_time: Optional[datetime]
    is_running: bool = Field(default=False, description="是否正在执行")
    pending: bool = Field(default=False, description="是否等待执行")