"""好感度系统数据模型 - 三维社交关系模型（9级分层）"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List, Tuple
from enum import Enum


# ============ 固定常量 ============

# 单次好感度增量范围（每个维度）
DELTA_MIN = -5.0
DELTA_MAX = 5.0

# 每个维度好感度范围
AFFECTION_MIN = -100.0
AFFECTION_MAX = 100.0

# 初始好感度范围（每个维度）
INIT_MIN = 0.0
INIT_MAX = 20.0

# 感性好感度保留比例（日记结算时使用）
RETAINED_RATIO = 0.2  # 保留20%进入base

# 9级分层区间宽度
LEVEL_COUNT = 9
LEVEL_WIDTH = (AFFECTION_MAX - AFFECTION_MIN) / LEVEL_COUNT  # ≈ 22.22


# ============ 维度枚举 ============

class AffectionDimension(str, Enum):
    """好感度维度 - 社交关系模型"""
    TRUST = "trust"       # 信任：可靠性、一致性、信守承诺
    INTIMACY = "intimacy"  # 亲密：情感交流深度、自我暴露
    RESPECT = "respect"   # 尊重：能力认可、价值观认同


# 维度中文描述（用于LLM prompt）
DIMENSION_DESCRIPTIONS = {
    AffectionDimension.TRUST: "信任 - 可靠性、一致性、信守承诺",
    AffectionDimension.INTIMACY: "亲密 - 情感交流深度、自我暴露",
    AffectionDimension.RESPECT: "尊重 - 能力认可、价值观认同",
}


# ============ 9级分层定义 ============

def _build_level_thresholds() -> List[Tuple[int, float, float]]:
    """构建9级阈值列表 [(level, low, high), ...]"""
    thresholds = []
    for i in range(LEVEL_COUNT):
        low = AFFECTION_MIN + i * LEVEL_WIDTH
        high = AFFECTION_MIN + (i + 1) * LEVEL_WIDTH
        # 最顶层包含上界
        if i == LEVEL_COUNT - 1:
            high = AFFECTION_MAX + 0.01  # +epsilon 确保包含 100.0
        thresholds.append((i + 1, low, high))
    return thresholds

LEVEL_THRESHOLDS = _build_level_thresholds()


# 每个维度的9级中文标签
DIMENSION_LEVEL_LABELS_ZH = {
    AffectionDimension.TRUST: {
        1: "极度怀疑的", 2: "很不信任的", 3: "不太信任的",
        4: "有些怀疑的", 5: "中立的",
        6: "有些信任的", 7: "比较信任的", 8: "很信任的", 9: "非常信任的",
    },
    AffectionDimension.INTIMACY: {
        1: "极度疏远的", 2: "很冷漠的", 3: "不太亲近的",
        4: "有些疏离的", 5: "普通的",
        6: "有些熟悉的", 7: "比较亲密的", 8: "很亲密的", 9: "非常亲密的",
    },
    AffectionDimension.RESPECT: {
        1: "极度鄙视的", 2: "很不尊重的", 3: "不太尊重的",
        4: "有些轻视的", 5: "中立的",
        6: "有些认可的", 7: "比较尊重的", 8: "很尊重的", 9: "非常尊敬的",
    },
}

# 每个维度的9级英文标签（用于 event_subtype 等）
DIMENSION_LEVEL_LABELS_EN = {
    AffectionDimension.TRUST: {
        1: "extremely_suspicious", 2: "very_distrustful", 3: "somewhat_distrustful",
        4: "slightly_suspicious", 5: "neutral",
        6: "slightly_trusting", 7: "fairly_trusting", 8: "very_trusting", 9: "extremely_trusting",
    },
    AffectionDimension.INTIMACY: {
        1: "extremely_distant", 2: "very_cold", 3: "somewhat_distant",
        4: "slightly_cold", 5: "neutral",
        6: "slightly_familiar", 7: "fairly_intimate", 8: "very_intimate", 9: "extremely_intimate",
    },
    AffectionDimension.RESPECT: {
        1: "extremely_disdainful", 2: "very_disrespectful", 3: "somewhat_disrespectful",
        4: "slightly_disdainful", 5: "neutral",
        6: "slightly_acknowledging", 7: "fairly_respecting", 8: "very_respecting", 9: "extremely_admiring",
    },
}

# 维度短标签（用于 event_subtype 前缀）
DIMENSION_LEVEL_LABELS = {
    AffectionDimension.TRUST: "trust",
    AffectionDimension.INTIMACY: "intimacy",
    AffectionDimension.RESPECT: "respect",
}

# 兼容旧代码：全局中文标签 lookup（level_name → 中文）
LEVEL_DESCRIPTIONS: Dict[str, str] = {}
for _dim in AffectionDimension:
    for _lv, _label in DIMENSION_LEVEL_LABELS_ZH[_dim].items():
        LEVEL_DESCRIPTIONS[DIMENSION_LEVEL_LABELS_EN[_dim][_lv]] = _label


# ============ 9级分类接口 ============

@dataclass
class AffectionLevel:
    """单个维度的好感度级别"""
    dimension: str          # "trust" / "intimacy" / "respect"
    level: int              # 1-9
    label_zh: str           # 中文标签，如 "比较信任的"
    label_en: str           # 英文标签，如 "fairly_trusting"
    value: float            # 原始数值
    range_low: float        # 区间下限
    range_high: float       # 区间上限

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "level": self.level,
            "label_zh": self.label_zh,
            "label_en": self.label_en,
            "value": round(self.value, 2),
            "range_low": round(self.range_low, 2),
            "range_high": round(self.range_high, 2),
        }


@dataclass
class AffectionLevelResult:
    """三维好感度级别分类结果 —— 专用接口的输出

    用法:
        result = classify_affection_levels(trust=55.2, intimacy=78.1, respect=12.5)
        result.trust.label_zh    # "比较信任的"
        result.summary           # "信任(比较信任的,L7) | 亲密(很亲密的,L8) | 尊重(中立的,L5)"
    """
    trust: AffectionLevel
    intimacy: AffectionLevel
    respect: AffectionLevel

    @property
    def summary(self) -> str:
        """一行摘要，用于日志和快速查看"""
        parts = []
        for lv in [self.trust, self.intimacy, self.respect]:
            dim_name = {"trust": "信任", "intimacy": "亲密", "respect": "尊重"}.get(lv.dimension, lv.dimension)
            parts.append(f"{dim_name}({lv.label_zh}, L{lv.level})")
        return " | ".join(parts)

    def to_dict(self) -> dict:
        """转为 Redis 可存储的 dict"""
        return {
            "trust": self.trust.to_dict(),
            "intimacy": self.intimacy.to_dict(),
            "respect": self.respect.to_dict(),
            "summary": self.summary,
        }

    def to_context_string(self) -> str:
        """生成用于 LLM 上下文的层级描述文本"""
        return (
            f"当前好感度级别：\n"
            f"  - 信任：{self.trust.label_zh}（L{self.trust.level}/9）\n"
            f"  - 亲密：{self.intimacy.label_zh}（L{self.intimacy.level}/9）\n"
            f"  - 尊重：{self.respect.label_zh}（L{self.respect.level}/9）"
        )


def get_affection_level(dim: AffectionDimension, value: float) -> int:
    """获取指定维度在给定好感度值下的级别编号（1-9）

    Args:
        dim: 好感度维度
        value: 好感度总分值

    Returns:
        级别编号 1-9
    """
    for lv, low, high in LEVEL_THRESHOLDS:
        if low <= value < high:
            return lv
    # 兜底：极值情况返回边界级别
    if value >= AFFECTION_MAX:
        return LEVEL_COUNT
    return 1


def _get_level_info(dim: AffectionDimension, value: float) -> AffectionLevel:
    """根据维度和数值构建完整的 AffectionLevel 对象"""
    lv = get_affection_level(dim, value)
    range_low, range_high = AFFECTION_MIN, AFFECTION_MAX
    for thr_lv, thr_low, thr_high in LEVEL_THRESHOLDS:
        if thr_lv == lv:
            range_low, range_high = thr_low, thr_high if thr_high <= AFFECTION_MAX else AFFECTION_MAX
            break

    return AffectionLevel(
        dimension=dim.value,
        level=lv,
        label_zh=DIMENSION_LEVEL_LABELS_ZH[dim][lv],
        label_en=DIMENSION_LEVEL_LABELS_EN[dim][lv],
        value=value,
        range_low=range_low,
        range_high=range_high,
    )


def classify_affection_levels(
    trust: float,
    intimacy: float,
    respect: float,
) -> AffectionLevelResult:
    """专用接口：输入3个维度好感度数值，输出3个维度的级别文本

    这是固化接口，供 LLM 语境生成、关系描述等模块使用。

    Args:
        trust: 信任维度值 [-100, 100]
        intimacy: 亲密维度值 [-100, 100]
        respect: 尊重维度值 [-100, 100]

    Returns:
        AffectionLevelResult，包含三个维度的完整级别信息
    """
    return AffectionLevelResult(
        trust=_get_level_info(AffectionDimension.TRUST, trust),
        intimacy=_get_level_info(AffectionDimension.INTIMACY, intimacy),
        respect=_get_level_info(AffectionDimension.RESPECT, respect),
    )


# ============ 兼容别名（旧代码迁移） ============

def get_affection_stage(dim: AffectionDimension, value: float) -> str:
    """[已废弃] 返回英文级别名，兼容旧代码

    请改用 get_affection_level(dim, value) 获取级别编号，
    或 classify_affection_levels(...) 获取完整结构化输出。
    """
    lv = get_affection_level(dim, value)
    return DIMENSION_LEVEL_LABELS_EN[dim][lv]


# ============ 级别变化事件 ============

@dataclass
class LevelTransition:
    """单个维度的级别变化事件（替代旧的 StageTransition）"""
    dimension: AffectionDimension
    from_level: int
    to_level: int
    from_label: str       # 中文标签
    to_label: str         # 中文标签
    old_value: float
    new_value: float

    @property
    def from_level_name(self) -> str:
        """旧代码兼容：from_stage 别名 → 返回英文标签"""
        return DIMENSION_LEVEL_LABELS_EN[self.dimension][self.from_level]

    @property
    def to_level_name(self) -> str:
        """旧代码兼容：to_stage 别名 → 返回英文标签"""
        return DIMENSION_LEVEL_LABELS_EN[self.dimension][self.to_level]

    @property
    def from_stage(self) -> str:
        """兼容旧代码"""
        return self.from_level_name

    @property
    def to_stage(self) -> str:
        """兼容旧代码"""
        return self.to_level_name

    @property
    def is_upgrade(self) -> bool:
        """判断是否为升级（好感度上升）"""
        return self.new_value > self.old_value

    def to_trigger_text(self) -> str:
        """生成触发文本"""
        direction = "进入" if self.is_upgrade else "跌回"
        dim_desc = DIMENSION_DESCRIPTIONS[self.dimension].split(" - ")[0]
        return f"{dim_desc}从「{self.from_label}」{direction}「{self.to_label}」阶段（L{self.from_level}→L{self.to_level}）"

    def to_subtype(self) -> str:
        """生成 event_subtype"""
        base = DIMENSION_LEVEL_LABELS[self.dimension]
        suffix = "_rise" if self.is_upgrade else "_fall"
        return base + suffix


# 兼容旧代码别名
StageTransition = LevelTransition


# ============ 数据模型 ============

@dataclass
class DimensionState:
    """单个维度的好感度状态"""
    dimension: AffectionDimension
    base: float = 0.0  # 基础好感度（永久，日记结算）
    emotional_retained: float = 0.0  # 感性保留值（定时衰减，日记结算后归零）

    @property
    def emotional_current(self) -> float:
        """当前感性好感度 = emotional_retained（由定时器衰减）"""
        return self.emotional_retained

    @property
    def total(self) -> float:
        """该维度总好感度 = 基础 + 感性（限制范围）"""
        total = self.base + self.emotional_retained
        return max(AFFECTION_MIN, min(AFFECTION_MAX, total))

    @property
    def level(self) -> int:
        """当前级别编号 (1-9)"""
        return get_affection_level(self.dimension, self.total)

    @property
    def level_label(self) -> str:
        """当前级别中文标签"""
        return DIMENSION_LEVEL_LABELS_ZH[self.dimension][self.level]

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "dimension": self.dimension.value,
            "base": self.base,
            "emotional_retained": self.emotional_retained,
            "emotional_current": self.emotional_retained,
            "total": self.total,
            "level": self.level,
            "level_label": self.level_label,
        }


@dataclass
class AffectionState:
    """三维好感度状态"""
    character_id: str
    user_id: str
    dimensions: Dict[AffectionDimension, DimensionState] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        """确保三个维度都存在"""
        for dim in AffectionDimension:
            if dim not in self.dimensions:
                self.dimensions[dim] = DimensionState(dimension=dim)

    def get_dimension(self, dim: AffectionDimension) -> DimensionState:
        """获取指定维度状态"""
        return self.dimensions.get(dim, DimensionState(dimension=dim))

    def get_levels(self) -> AffectionLevelResult:
        """获取三维级别分类"""
        return AffectionLevelResult(
            trust=_get_level_info(AffectionDimension.TRUST, self.get_dimension(AffectionDimension.TRUST).total),
            intimacy=_get_level_info(AffectionDimension.INTIMACY, self.get_dimension(AffectionDimension.INTIMACY).total),
            respect=_get_level_info(AffectionDimension.RESPECT, self.get_dimension(AffectionDimension.RESPECT).total),
        )

    def to_dict(self) -> dict:
        """转换为字典（用于Redis存储/上下文注入）"""
        result = {
            "character_id": self.character_id,
            "user_id": self.user_id,
            "updated_at": self.updated_at.isoformat(),
        }
        for dim in AffectionDimension:
            dim_state = self.get_dimension(dim)
            result[f"{dim.value}_base"] = dim_state.base
            result[f"{dim.value}_emotional"] = dim_state.emotional_current
            result[f"{dim.value}_total"] = dim_state.total
            result[f"{dim.value}_level"] = dim_state.level
        return result

    def to_context_string(self) -> str:
        """生成用于LLM上下文的描述字符串（含级别标签）"""
        lines = ["当前好感度状态："]
        for dim in AffectionDimension:
            dim_state = self.get_dimension(dim)
            desc = DIMENSION_DESCRIPTIONS[dim]
            label = dim_state.level_label
            lines.append(
                f"  - {desc}: {label}（L{dim_state.level}/9）"
                f" | 基础{dim_state.base:.1f} + 感性{dim_state.emotional_current:.1f} = {dim_state.total:.1f}"
            )
        return "\n".join(lines)


@dataclass
class AffectionAssessment:
    """LLM好感度评估结果（三维）"""
    trust_delta: float = 0.0
    intimacy_delta: float = 0.0
    respect_delta: float = 0.0
    reasoning: Optional[str] = None  # 评估理由（仅日志用）

    def __post_init__(self):
        """限制每个维度增量范围"""
        self.trust_delta = max(DELTA_MIN, min(DELTA_MAX, self.trust_delta))
        self.intimacy_delta = max(DELTA_MIN, min(DELTA_MAX, self.intimacy_delta))
        self.respect_delta = max(DELTA_MIN, min(DELTA_MAX, self.respect_delta))

    def get_delta(self, dim: AffectionDimension) -> float:
        """获取指定维度的增量"""
        if dim == AffectionDimension.TRUST:
            return self.trust_delta
        elif dim == AffectionDimension.INTIMACY:
            return self.intimacy_delta
        elif dim == AffectionDimension.RESPECT:
            return self.respect_delta
        return 0.0

    def has_any_delta(self, threshold: float = 0.01) -> bool:
        """检查是否存在任何维度的增量（超过阈值）"""
        return (
            abs(self.trust_delta) > threshold
            or abs(self.intimacy_delta) > threshold
            or abs(self.respect_delta) > threshold
        )

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "trust_delta": self.trust_delta,
            "intimacy_delta": self.intimacy_delta,
            "respect_delta": self.respect_delta,
            "reasoning": self.reasoning,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AffectionAssessment":
        """从字典创建"""
        return cls(
            trust_delta=data.get("trust_delta", 0.0),
            intimacy_delta=data.get("intimacy_delta", 0.0),
            respect_delta=data.get("respect_delta", 0.0),
            reasoning=data.get("reasoning"),
        )


# ============ 状态快照 ============

@dataclass
class AffectionSnapshot:
    """好感度+情绪状态快照（用于变化检测）

    记录某一时刻的完整状态，用于判断后续状态变化是否超过阈值。
    """
    # PAD 情绪状态
    pad_p: float = 0.0
    pad_a: float = 0.0
    pad_d: float = 0.0

    # 三维好感度
    trust_total: float = 0.0
    intimacy_total: float = 0.0
    respect_total: float = 0.0

    # 时间戳
    timestamp: datetime = field(default_factory=datetime.now)

    def change_score(self, other: "AffectionSnapshot") -> float:
        """计算与另一个快照的变化分数

        变化分数用于判断是否需要重新生成好感度语境：
        - PAD变化：任意维度变化超过 0.3 则触发（缩放10倍 ≈ 3分）
        - 好感度变化：任意维度变化超过 5.0 则触发

        Returns:
            变化分数，超过 5.0 建议重新生成
        """
        # PAD 变化（缩放10倍，因为PAD范围是[-1,1]，好感度范围是[-100,100]）
        pad_change = max(
            abs(self.pad_p - other.pad_p),
            abs(self.pad_a - other.pad_a),
            abs(self.pad_d - other.pad_d),
        ) * 10.0

        # 好感度变化
        affection_change = max(
            abs(self.trust_total - other.trust_total),
            abs(self.intimacy_total - other.intimacy_total),
            abs(self.respect_total - other.respect_total),
        )

        # 综合变化分数
        return max(pad_change, affection_change)

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "pad_p": self.pad_p,
            "pad_a": self.pad_a,
            "pad_d": self.pad_d,
            "trust_total": self.trust_total,
            "intimacy_total": self.intimacy_total,
            "respect_total": self.respect_total,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AffectionSnapshot":
        """从字典创建"""
        return cls(
            pad_p=data.get("pad_p", 0.0),
            pad_a=data.get("pad_a", 0.0),
            pad_d=data.get("pad_d", 0.0),
            trust_total=data.get("trust_total", 0.0),
            intimacy_total=data.get("intimacy_total", 0.0),
            respect_total=data.get("respect_total", 0.0),
            timestamp=datetime.fromisoformat(data.get("timestamp", datetime.now().isoformat())),
        )
