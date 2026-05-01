"""Redis Key 命名规范 - Stone 数据层核心模块

统一管理 Redis Key 命名：
- 标准化 Key 格式
- 按业务域分类
- 支持动态构建
"""

from typing import Literal
from enum import Enum


class RedisNamespace(str, Enum):
    """Redis Key 命名空间"""

    AGENT = "agent"
    EMOTION = "emotion"
    AFFECTION = "affection"
    TASK = "task"
    PLAYBACK = "playback"
    CONVERSATION = "conv"
    OPENCLAW = "openclaw"


class RedisKeyType(str, Enum):
    """Redis Key 类型"""

    # 情绪系统
    EMOTION_STATE = "emotion_state"
    HEART_EVENT = "heart_event"
    HEART_EVENTS_LIST = "heart_events_list"

    # 好感度系统
    AFFECTION_STATE = "affection_state"
    AFFECTION_CONTEXT = "affection_context"

    # 任务系统
    TASK = "task"
    TASK_QUEUE = "task_queue"
    TASK_STATS = "task_stats"

    # 播报系统
    PLAYBACK_TASK = "playback_task"
    PLAYBACK_QUEUE = "playback_queue"

    # 对话历史
    CONVERSATION = "conversation"
    CONVERSATION_PERSISTENT = "conversation_persistent"

    # OpenClaw
    OPENCLAW_TASK = "openclaw_task"
    OPENCLAW_QUEUE = "openclaw_queue"


# ============================================================
# Key 模板定义
# ============================================================

KEY_TEMPLATES: dict[str, str] = {
    # 情绪系统
    "emotion_state": "{ns}:emotion:{character_id}",
    "heart_event": "{ns}:heart:{character_id}:{user_id}:{event_id}",
    "heart_events_list": "{ns}:heart:list:{character_id}:{user_id}",
    # 好感度系统
    "affection_state": "{ns}:affection:{character_id}:{user_id}",
    "affection_context": "{ns}:affection:ctx:{character_id}:{user_id}",
    # 任务系统
    "task": "{ns}:task:{task_id}",
    "task_queue": "{ns}:queue:task:{queue_name}",
    "task_stats": "{ns}:stats:task",
    # 播报系统
    "playback_task": "{ns}:playback:{task_id}",
    "playback_queue": "{ns}:queue:playback",
    # 对话历史
    "conversation": "{ns}:conv:{channel}:{user_id}",
    "conversation_persistent": "{ns}:conv:persistent:{channel}:{user_id}",
    # OpenClaw
    "openclaw_task": "{ns}:openclaw:task:{task_id}",
    "openclaw_queue": "{ns}:queue:openclaw",
}


class RedisKeyBuilder:
    """Redis Key 构建器"""

    def __init__(self, namespace: str | RedisNamespace = RedisNamespace.AGENT):
        """初始化

        Args:
            namespace: 命名空间，默认为 'agent'
        """
        self._namespace = namespace.value if isinstance(namespace, RedisNamespace) else namespace

    def build(
        self,
        key_type: str | RedisKeyType,
        **kwargs,
    ) -> str:
        """构建 Redis Key

        Args:
            key_type: Key 类型
            **kwargs: Key 参数

        Returns:
            完整的 Redis Key
        """
        type_name = key_type.value if isinstance(key_type, RedisKeyType) else key_type

        if type_name not in KEY_TEMPLATES:
            raise ValueError(f"Unknown key type: {type_name}")

        template = KEY_TEMPLATES[type_name]
        return template.format(ns=self._namespace, **kwargs)

    # ============================================================
    # 情绪系统 Key
    # ============================================================

    def emotion_state(self, character_id: str) -> str:
        """情绪状态 Key"""
        return self.build("emotion_state", character_id=character_id)

    def heart_event(
        self, character_id: str, user_id: str, event_id: str | int
    ) -> str:
        """心动事件 Key"""
        return self.build(
            "heart_event",
            character_id=character_id,
            user_id=user_id,
            event_id=event_id,
        )

    def heart_events_list(self, character_id: str, user_id: str) -> str:
        """心动事件列表 Key（Sorted Set）"""
        return self.build(
            "heart_events_list",
            character_id=character_id,
            user_id=user_id,
        )

    # ============================================================
    # 好感度系统 Key
    # ============================================================

    def affection_state(self, character_id: str, user_id: str) -> str:
        """好感度状态 Key"""
        return self.build(
            "affection_state",
            character_id=character_id,
            user_id=user_id,
        )

    def affection_context(self, character_id: str, user_id: str) -> str:
        """好感度上下文 Key"""
        return self.build(
            "affection_context",
            character_id=character_id,
            user_id=user_id,
        )

    # ============================================================
    # 任务系统 Key
    # ============================================================

    def task(self, task_id: str) -> str:
        """任务详情 Key"""
        return self.build("task", task_id=task_id)

    def task_queue(self, queue_name: str = "pending") -> str:
        """任务队列 Key"""
        return self.build("task_queue", queue_name=queue_name)

    def task_stats(self) -> str:
        """任务统计 Key"""
        return self.build("task_stats")

    # ============================================================
    # 播报系统 Key
    # ============================================================

    def playback_task(self, task_id: str) -> str:
        """播报任务 Key"""
        return self.build("playback_task", task_id=task_id)

    def playback_queue(self) -> str:
        """播报队列 Key"""
        return self.build("playback_queue")

    # ============================================================
    # 对话历史 Key
    # ============================================================

    def conversation(
        self,
        channel: str = "default",
        user_id: str = None,
        character_id: str = None,
    ) -> str:
        """对话历史 Key

        支持两种参数格式：
        - channel + user_id
        - character_id + user_id（自动转换为 channel）
        """
        if character_id and user_id:
            # 使用 character_id 作为 channel
            return self.build(
                "conversation",
                channel=character_id,
                user_id=user_id,
            )
        elif channel and user_id:
            return self.build(
                "conversation",
                channel=channel,
                user_id=user_id,
            )
        else:
            raise ValueError("Either (channel, user_id) or (character_id, user_id) must be provided")

    def conversation_persistent(
        self,
        channel: str = "default",
        user_id: str = None,
        character_id: str = None,
    ) -> str:
        """持久化对话历史 Key"""
        if character_id and user_id:
            return self.build(
                "conversation_persistent",
                channel=character_id,
                user_id=user_id,
            )
        elif channel and user_id:
            return self.build(
                "conversation_persistent",
                channel=channel,
                user_id=user_id,
            )
        else:
            raise ValueError("Either (channel, user_id) or (character_id, user_id) must be provided")

    # ============================================================
    # OpenClaw Key
    # ============================================================

    def openclaw_task(self, task_id: str) -> str:
        """OpenClaw 任务 Key"""
        return self.build("openclaw_task", task_id=task_id)

    def openclaw_queue(self) -> str:
        """OpenClaw 队列 Key"""
        return self.build("openclaw_queue")


# ============================================================
# 全局默认构建器
# ============================================================

_default_builder: RedisKeyBuilder = RedisKeyBuilder()


def build_key(key_type: str | RedisKeyType, **kwargs) -> str:
    """使用默认构建器构建 Key"""
    return _default_builder.build(key_type, **kwargs)


def get_key_builder(namespace: str = None) -> RedisKeyBuilder:
    """获取 Key 构建器

    Args:
        namespace: 命名空间，默认使用全局默认
    """
    if namespace:
        return RedisKeyBuilder(namespace)
    return _default_builder


# ============================================================
# 兼容旧 Key 格式（过渡期）
# ============================================================


def legacy_affection_key(character_id: str, user_id: str) -> str:
    """旧好感度 Key 格式（兼容）"""
    return f"affection:{character_id}:{user_id}"


def legacy_emotion_key(character_id: str) -> str:
    """旧情绪 Key 格式（兼容）"""
    return f"emotion:{character_id}"


def legacy_conv_key(channel: str, user_id: str) -> str:
    """旧对话历史 Key 格式（兼容）"""
    return f"conv:{channel}:{user_id}"