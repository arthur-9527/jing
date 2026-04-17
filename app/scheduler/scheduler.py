"""定时任务调度器核心实现

使用 APScheduler AsyncIOScheduler 实现异步定时任务调度。
提供任务的添加、删除、暂停、恢复、查询等接口。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Optional, Union

from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ⭐ 将 APScheduler 日志级别设置为 INFO（关闭 DEBUG 日志）
logging.getLogger('apscheduler').setLevel(logging.WARNING)
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.job import Job as APSJob
from apscheduler.schedulers.base import STATE_RUNNING

from app.scheduler.models import (
    JobInfo,
    JobTriggerType,
    JobStatusResponse,
    CronTriggerConfig,
    IntervalTriggerConfig,
    DateTriggerConfig,
)

logger = logging.getLogger(__name__)


class TaskScheduler:
    """定时任务调度器
    
    核心功能：
    - 启动/关闭调度器
    - 添加/删除任务
    - 暂停/恢复任务
    - 查询任务状态
    - 修改任务配置
    
    支持触发器类型：
    - cron: Cron 表达式触发
    - interval: 固定间隔触发
    - date: 指定时间一次性触发
    """
    
    def __init__(self):
        """初始化调度器"""
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._jobs_registry: dict[str, dict[str, Any]] = {}  # 任务元数据注册表
        self._started = False
    
    @property
    def scheduler(self) -> AsyncIOScheduler:
        """获取调度器实例"""
        if self._scheduler is None:
            self._scheduler = AsyncIOScheduler(
                timezone="Asia/Shanghai",
                job_defaults={
                    "coalesce": True,      # 合并错过的任务
                    "max_instances": 1,    # 每个任务最大并发实例数
                    "misfire_grace_time": 60,  # 错过执行的容忍时间（秒）
                }
            )
        return self._scheduler
    
    @property
    def is_running(self) -> bool:
        """调度器是否正在运行"""
        if not self._started:
            return False
        if self._scheduler is None:
            return False
        return self._scheduler.state == STATE_RUNNING
    
    # ==================== 调度器生命周期 ====================
    
    async def start(self) -> None:
        """启动调度器"""
        if self.is_running:
            logger.warning("调度器已在运行中")
            return
        
        logger.info("启动定时任务调度器...")
        self.scheduler.start()
        self._started = True
        logger.info("定时任务调度器已启动，当前任务数: %d", len(self.get_jobs()))
    
    async def stop(self, wait: bool = True) -> None:
        """关闭调度器
        
        Args:
            wait: 是否等待正在执行的任务完成
        """
        if not self.is_running:
            logger.warning("调度器未在运行")
            return
        
        logger.info("关闭定时任务调度器...")
        self.scheduler.shutdown(wait=wait)
        self._started = False
        logger.info("定时任务调度器已关闭")
    
    # ==================== 任务添加 ====================
    
    def add_job(
        self,
        func: Callable,
        job_id: str,
        name: str,
        trigger_type: Union[JobTriggerType, str],
        trigger_config: dict[str, Any],
        description: Optional[str] = None,
        args: Optional[list[Any]] = None,
        kwargs: Optional[dict[str, Any]] = None,
        enabled: bool = True,
        replace_existing: bool = False,
    ) -> APSJob:
        """添加定时任务
        
        Args:
            func: 任务执行函数（异步或同步）
            job_id: 任务唯一标识
            name: 任务名称
            trigger_type: 触发器类型 (cron/interval/date)
            trigger_config: 触发器配置
            description: 任务描述
            args: 函数参数列表
            kwargs: 函数关键字参数
            enabled: 是否立即启用
            replace_existing: 是否替换已存在的同名任务
            
        Returns:
            APScheduler Job 对象
            
        Example:
            # 每小时执行
            scheduler.add_job(
                func=my_task,
                job_id="hourly_task",
                name="每小时任务",
                trigger_type="cron",
                trigger_config={"minute": "0"},
            )
            
            # 每5分钟执行
            scheduler.add_job(
                func=my_task,
                job_id="interval_task",
                name="间隔任务",
                trigger_type="interval",
                trigger_config={"minutes": 5},
            )
        """
        # 构建触发器
        trigger = self._build_trigger(trigger_type, trigger_config)
        
        # 添加任务
        job = self.scheduler.add_job(
            func=func,
            trigger=trigger,
            id=job_id,
            name=name,
            args=args or (),
            kwargs=kwargs or {},
            replace_existing=replace_existing,
        )
        
        # 注册任务元数据
        self._jobs_registry[job_id] = {
            "name": name,
            "description": description,
            "trigger_type": trigger_type if isinstance(trigger_type, str) else trigger_type.value,
            "trigger_config": trigger_config,
            "enabled": enabled,
        }
        
        # 如果不启用，暂停任务
        if not enabled:
            self.pause_job(job_id)
        
        logger.info(
            "添加任务: id=%s, name=%s, trigger=%s, next_run=%s",
            job_id, name, trigger_type, job.next_run_time
        )
        
        return job
    
    def add_cron_job(
        self,
        func: Callable,
        job_id: str,
        name: str,
        cron_config: Union[CronTriggerConfig, dict[str, Any]],
        description: Optional[str] = None,
        args: Optional[list[Any]] = None,
        kwargs: Optional[dict[str, Any]] = None,
        enabled: bool = True,
        replace_existing: bool = False,
    ) -> APSJob:
        """添加 Cron 定时任务（便捷方法）
        
        Args:
            func: 任务执行函数
            job_id: 任务唯一标识
            name: 任务名称
            cron_config: Cron 配置（支持 CronTriggerConfig 或 dict）
            
        Example:
            # 每小时整点执行
            scheduler.add_cron_job(task, "hourly", "每小时", {"minute": "0"})
            
            # 每天凌晨2点
            scheduler.add_cron_job(task, "daily", "每天", {"hour": "2", "minute": "0"})
            
            # 每周一早上3点
            scheduler.add_cron_job(task, "weekly", "每周", {"day_of_week": "0", "hour": "3"})
        """
        if isinstance(cron_config, CronTriggerConfig):
            config = cron_config.to_dict()
        else:
            config = cron_config
        
        return self.add_job(
            func=func,
            job_id=job_id,
            name=name,
            trigger_type=JobTriggerType.CRON,
            trigger_config=config,
            description=description,
            args=args,
            kwargs=kwargs,
            enabled=enabled,
            replace_existing=replace_existing,
        )
    
    def add_interval_job(
        self,
        func: Callable,
        job_id: str,
        name: str,
        interval_config: Union[IntervalTriggerConfig, dict[str, Any]],
        description: Optional[str] = None,
        args: Optional[list[Any]] = None,
        kwargs: Optional[dict[str, Any]] = None,
        enabled: bool = True,
        replace_existing: bool = False,
    ) -> APSJob:
        """添加间隔定时任务（便捷方法）
        
        Args:
            func: 任务执行函数
            job_id: 任务唯一标识
            name: 任务名称
            interval_config: 间隔配置
            
        Example:
            # 每5分钟执行
            scheduler.add_interval_job(task, "5min", "每5分钟", {"minutes": 5})
            
            # 每小时执行
            scheduler.add_interval_job(task, "1hour", "每小时", {"hours": 1})
        """
        if isinstance(interval_config, IntervalTriggerConfig):
            config = interval_config.to_dict()
        else:
            config = interval_config
        
        return self.add_job(
            func=func,
            job_id=job_id,
            name=name,
            trigger_type=JobTriggerType.INTERVAL,
            trigger_config=config,
            description=description,
            args=args,
            kwargs=kwargs,
            enabled=enabled,
            replace_existing=replace_existing,
        )
    
    # ==================== 任务删除 ====================
    
    def remove_job(self, job_id: str) -> bool:
        """删除任务
        
        Args:
            job_id: 任务唯一标识
            
        Returns:
            是否成功删除
        """
        try:
            self.scheduler.remove_job(job_id)
            self._jobs_registry.pop(job_id, None)
            logger.info("删除任务: id=%s", job_id)
            return True
        except Exception as e:
            logger.warning("删除任务失败: id=%s, error=%s", job_id, e)
            return False
    
    # ==================== 任务暂停/恢复 ====================
    
    def pause_job(self, job_id: str) -> bool:
        """暂停任务
        
        Args:
            job_id: 任务唯一标识
            
        Returns:
            是否成功暂停
        """
        try:
            self.scheduler.pause_job(job_id)
            if job_id in self._jobs_registry:
                self._jobs_registry[job_id]["enabled"] = False
            logger.info("暂停任务: id=%s", job_id)
            return True
        except Exception as e:
            logger.warning("暂停任务失败: id=%s, error=%s", job_id, e)
            return False
    
    def resume_job(self, job_id: str) -> bool:
        """恢复任务
        
        Args:
            job_id: 任务唯一标识
            
        Returns:
            是否成功恢复
        """
        try:
            self.scheduler.resume_job(job_id)
            if job_id in self._jobs_registry:
                self._jobs_registry[job_id]["enabled"] = True
            logger.info("恢复任务: id=%s", job_id)
            return True
        except Exception as e:
            logger.warning("恢复任务失败: id=%s, error=%s", job_id, e)
            return False
    
    # ==================== 任务查询 ====================
    
    def get_job(self, job_id: str) -> Optional[APSJob]:
        """获取单个任务
        
        Args:
            job_id: 任务唯一标识
            
        Returns:
            APScheduler Job 对象或 None
        """
        return self.scheduler.get_job(job_id)
    
    def get_jobs(self) -> list[APSJob]:
        """获取所有任务列表
        
        Returns:
            任务列表
        """
        return self.scheduler.get_jobs()
    
    def get_job_info(self, job_id: str) -> Optional[JobInfo]:
        """获取任务详细信息
        
        Args:
            job_id: 任务唯一标识
            
        Returns:
            JobInfo 对象或 None
        """
        job = self.get_job(job_id)
        if job is None:
            return None
        
        metadata = self._jobs_registry.get(job_id, {})
        
        return JobInfo(
            job_id=job.id,
            name=metadata.get("name", job.name),
            description=metadata.get("description"),
            trigger_type=metadata.get("trigger_type", "cron"),
            trigger_config=metadata.get("trigger_config", {}),
            enabled=metadata.get("enabled", True),
            next_run_time=job.next_run_time,
            last_run_time=self._get_last_run_time(job),
        )
    
    def get_job_status(self, job_id: str) -> Optional[JobStatusResponse]:
        """获取任务状态详情
        
        Args:
            job_id: 任务唯一标识
            
        Returns:
            JobStatusResponse 或 None
        """
        job = self.get_job(job_id)
        if job is None:
            return None
        
        metadata = self._jobs_registry.get(job_id, {})
        
        return JobStatusResponse(
            job_id=job.id,
            name=metadata.get("name", job.name),
            description=metadata.get("description"),
            trigger_type=metadata.get("trigger_type", "cron"),
            trigger_config=metadata.get("trigger_config", {}),
            enabled=metadata.get("enabled", True),
            next_run_time=job.next_run_time,
            last_run_time=self._get_last_run_time(job),
            is_running=self._is_job_running(job),
            pending=job.pending,
        )
    
    def get_all_jobs_info(self) -> list[JobInfo]:
        """获取所有任务信息列表
        
        Returns:
            JobInfo 列表
        """
        jobs = self.get_jobs()
        return [
            self.get_job_info(job.id)
            for job in jobs
            if self.get_job_info(job.id) is not None
        ]
    
    def get_all_jobs_status(self) -> list[JobStatusResponse]:
        """获取所有任务状态列表
        
        Returns:
            JobStatusResponse 列表
        """
        jobs = self.get_jobs()
        return [
            self.get_job_status(job.id)
            for job in jobs
            if self.get_job_status(job.id) is not None
        ]
    
    # ==================== 任务修改 ====================
    
    def modify_job(
        self,
        job_id: str,
        trigger_type: Optional[Union[JobTriggerType, str]] = None,
        trigger_config: Optional[dict[str, Any]] = None,
        args: Optional[list[Any]] = None,
        kwargs: Optional[dict[str, Any]] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> bool:
        """修改任务配置
        
        Args:
            job_id: 任务唯一标识
            trigger_type: 新触发器类型（可选）
            trigger_config: 新触发器配置（可选）
            args: 新函数参数（可选）
            kwargs: 新函数关键字参数（可选）
            name: 新任务名称（可选）
            description: 新描述（可选）
            
        Returns:
            是否成功修改
        """
        try:
            job = self.get_job(job_id)
            if job is None:
                logger.warning("任务不存在: id=%s", job_id)
                return False
            
            # 修改触发器
            if trigger_type and trigger_config:
                trigger = self._build_trigger(trigger_type, trigger_config)
                self.scheduler.modify_job(job_id, trigger=trigger)
                self._jobs_registry[job_id]["trigger_type"] = (
                    trigger_type.value if isinstance(trigger_type, JobTriggerType) else trigger_type
                )
                self._jobs_registry[job_id]["trigger_config"] = trigger_config
            
            # 修改参数
            if args is not None or kwargs is not None:
                self.scheduler.modify_job(
                    job_id,
                    args=args or job.args,
                    kwargs=kwargs or job.kwargs,
                )
            
            # 修改元数据
            if name:
                self._jobs_registry[job_id]["name"] = name
            if description:
                self._jobs_registry[job_id]["description"] = description
            
            logger.info("修改任务成功: id=%s", job_id)
            return True
        except Exception as e:
            logger.warning("修改任务失败: id=%s, error=%s", job_id, e)
            return False
    
    # ==================== 手动触发 ====================
    
    async def run_job_now(self, job_id: str) -> bool:
        """立即执行任务（不等待触发）
        
        Args:
            job_id: 任务唯一标识
            
        Returns:
            是否成功触发
        """
        try:
            job = self.get_job(job_id)
            if job is None:
                logger.warning("任务不存在: id=%s", job_id)
                return False
            
            # 获取任务函数和参数
            func = job.func
            args = job.args or ()
            kwargs = job.kwargs or {}
            
            logger.info("手动触发任务: id=%s", job_id)
            
            # 执行任务
            if asyncio.iscoroutinefunction(func):
                await func(*args, **kwargs)
            else:
                func(*args, **kwargs)
            
            return True
        except Exception as e:
            logger.error("手动触发任务失败: id=%s, error=%s", job_id, e)
            return False
    
    # ==================== 内部方法 ====================
    
    def _build_trigger(
        self,
        trigger_type: Union[JobTriggerType, str],
        trigger_config: dict[str, Any],
    ) -> Union[CronTrigger, IntervalTrigger, DateTrigger]:
        """构建触发器
        
        Args:
            trigger_type: 触发器类型
            trigger_config: 触发器配置
            
        Returns:
            触发器对象
        """
        type_str = trigger_type if isinstance(trigger_type, str) else trigger_type.value
        
        if type_str == JobTriggerType.CRON.value:
            return CronTrigger(**trigger_config, timezone="Asia/Shanghai")
        elif type_str == JobTriggerType.INTERVAL.value:
            return IntervalTrigger(**trigger_config, timezone="Asia/Shanghai")
        elif type_str == JobTriggerType.DATE.value:
            return DateTrigger(**trigger_config, timezone="Asia/Shanghai")
        else:
            raise ValueError(f"未知触发器类型: {type_str}")
    
    def _get_last_run_time(self, job: APSJob) -> Optional[datetime]:
        """获取任务上次执行时间"""
        # APScheduler Job 没有 last_run_time 属性，需要通过其他方式获取
        # 这里返回 None，后续可以通过任务注册表记录
        return None
    
    def _is_job_running(self, job: APSJob) -> bool:
        """检查任务是否正在执行"""
        # APScheduler 没有直接的方法检查任务是否正在执行
        # 这里返回 False，可以通过 job.pending 判断是否有待执行的实例
        return False


# ==================== 全局调度器实例 ====================

_scheduler_instance: Optional[TaskScheduler] = None


def get_scheduler() -> TaskScheduler:
    """获取全局调度器实例"""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = TaskScheduler()
    return _scheduler_instance


def init_scheduler() -> TaskScheduler:
    """初始化并返回全局调度器实例"""
    return get_scheduler()
