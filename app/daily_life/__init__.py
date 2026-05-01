"""日常事务系统模块

功能：
1. 定时随机生成角色日常活动场景（逛街、做蛋糕、学跳舞等）
2. 记录到数据库，作为日记素材
3. 生成情绪事件（heartbeat）和内心独白
4. 不播报，纯后台活动

使用方式：
    from app.daily_life import DailyLifeScheduler, get_daily_life_scheduler
    
    scheduler = get_daily_life_scheduler()
    await scheduler.start()
"""

from .models import DailyLifeEvent
from .config import DailyLifeSettings, get_daily_life_settings
from .scheduler import DailyLifeScheduler, get_daily_life_scheduler

__all__ = [
    "DailyLifeEvent",
    "DailyLifeSettings",
    "get_daily_life_settings",
    "DailyLifeScheduler",
    "get_daily_life_scheduler",
]