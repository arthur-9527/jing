"""记忆系统数据类型定义"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LongTermMemoryResult:
    """长期记忆检索结果（Deep Path）"""
    success: bool
    context: str = ""           # 整合后的记忆上下文（1000-2000字）
    confidence: float = 0.0     # 意图分析置信度
    intent: dict | None = None  # 意图分析结果
    reason: str = ""            # 失败原因（success=False时）