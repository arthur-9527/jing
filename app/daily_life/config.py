"""日常事务系统配置

配置项：
- DAILY_LIFE_ENABLED: 总开关
- DAILY_LIFE_MIN_INTERVAL_MINUTES: 最小触发间隔（分钟）
- DAILY_LIFE_MAX_INTERVAL_MINUTES: 最大触发间隔（分钟）
- DAILY_LIFE_ACTIVE_START_HOUR: 活跃开始时间（小时）
- DAILY_LIFE_ACTIVE_END_HOUR: 活跃结束时间（小时）
- DAILY_LIFE_MAX_DAILY_EVENTS: 每日最大触发次数
"""

from pydantic_settings import BaseSettings
from typing import Optional


class DailyLifeSettings(BaseSettings):
    """日常事务系统配置"""
    
    # ===== 总开关 =====
    DAILY_LIFE_ENABLED: bool = False  # 默认关闭，需要手动开启
    
    # ===== 触发间隔（分钟）=====
    DAILY_LIFE_MIN_INTERVAL_MINUTES: int = 120  # 2小时
    DAILY_LIFE_MAX_INTERVAL_MINUTES: int = 360  # 6小时
    
    # ===== 活跃时段（小时）=====
    DAILY_LIFE_ACTIVE_START_HOUR: int = 8   # 早上8点开始
    DAILY_LIFE_ACTIVE_END_HOUR: int = 22    # 晚上10点结束（22:00后不触发）
    
    # ===== 每日上限 =====
    DAILY_LIFE_MAX_DAILY_EVENTS: int = 5    # 每天最多触发5次
    
    # ===== 情绪强度范围 =====
    DAILY_LIFE_MIN_INTENSITY: float = 0.2
    DAILY_LIFE_MAX_INTENSITY: float = 0.5
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


# ===== 全局配置实例（懒加载）=====
_settings: Optional[DailyLifeSettings] = None


def get_daily_life_settings() -> DailyLifeSettings:
    """获取日常事务系统配置实例"""
    global _settings
    if _settings is None:
        _settings = DailyLifeSettings()
    return _settings


def reload_daily_life_settings() -> DailyLifeSettings:
    """重新加载配置（用于测试）"""
    global _settings
    _settings = DailyLifeSettings()
    return _settings