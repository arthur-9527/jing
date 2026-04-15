"""PAD 情绪状态机

⭐ 统一 PADState 类：
- 提供两种初始化方式：baseline dict 或直接 p, a, d 值
- 修复平滑公式数学错误
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass
class PADState:
    """
    PAD 情绪模型状态机
    P (Pleasure)  - 愉悦度  [-1, 1]
    A (Arousal)   - 激活度  [-1, 1]
    D (Dominance) - 支配度  [-1, 1]
    
    ⭐ 统一接口：
    - PADState.from_baseline(baseline_dict) - 从基线字典创建
    - PADState(p=0.0, a=0.0, d=0.0) - 直接创建（dataclass 方式）
    """
    p: float = 0.0  # Pleasure
    a: float = 0.0  # Arousal
    d: float = 0.0  # Dominance
    
    # 基线（可选，用于衰减）
    _baseline: dict[str, float] | None = None
    
    @classmethod
    def from_baseline(cls, baseline: dict[str, float]) -> PADState:
        """从基线字典创建 PADState"""
        return cls(
            p=baseline.get("P", 0.0),
            a=baseline.get("A", 0.0),
            d=baseline.get("D", 0.0),
            _baseline=baseline.copy(),
        )
    
    # ⭐ 兼容旧接口
    def __init__(self, baseline: dict[str, float] | None = None, p: float | None = None, a: float | None = None, d: float | None = None):
        """
        兼容两种初始化方式：
        - PADState(baseline={"P": 0.5, "A": 0.0, "D": 0.0}) - 旧接口
        - PADState(p=0.5, a=0.0, d=0.0) - 新接口
        """
        if baseline is not None:
            self.p = baseline.get("P", 0.0)
            self.a = baseline.get("A", 0.0)
            self.d = baseline.get("D", 0.0)
            self._baseline = baseline.copy()
        else:
            self.p = p if p is not None else 0.0
            self.a = a if a is not None else 0.0
            self.d = d if d is not None else 0.0
            self._baseline = None
    
    @property
    def baseline(self) -> dict[str, float]:
        """兼容旧接口的 baseline 属性"""
        if self._baseline is None:
            return {"P": self.p, "A": self.a, "D": self.d}
        return self._baseline

    def update(self, delta: dict[str, float]):
        """
        指数平滑更新 PAD 值
        
        ⭐ 修复数学错误：
        - 原公式：self.p * 0.8 + (self.p + clamped["P"]) * 0.2
          展开 = self.p + clamped["P"] * 0.2（权重无效！）
        - 新公式：self.p * 0.8 + clamped["P"] * 0.2
          正确的指数平滑
        
        - delta 钳制在 ±0.2
        - 权重：当前 0.8 / 新增 0.2
        """
        clamped = {}
        for k in ("P", "A", "D"):
            clamped[k] = max(-0.2, min(0.2, delta.get(k, 0.0)))

        # ⭐ 修复后的正确公式
        self.p = self.p * 0.8 + clamped["P"] * 0.2
        self.a = self.a * 0.8 + clamped["A"] * 0.2
        self.d = self.d * 0.8 + clamped["D"] * 0.2

        # 钳制最终值在 [-1, 1]
        self.p = max(-1.0, min(1.0, self.p))
        self.a = max(-1.0, min(1.0, self.a))
        self.d = max(-1.0, min(1.0, self.d))

    def decay(self, rate: float = 0.95):
        """向基线衰减"""
        self.p = self.p * rate + self.baseline["P"] * (1 - rate)
        self.a = self.a * rate + self.baseline["A"] * (1 - rate)
        self.d = self.d * rate + self.baseline["D"] * (1 - rate)

    def intensity(self, delta: dict[str, float]) -> float:
        """计算情绪变化的 L2 范数"""
        dp = delta.get("P", 0.0)
        da = delta.get("A", 0.0)
        dd = delta.get("D", 0.0)
        return (dp ** 2 + da ** 2 + dd ** 2) ** 0.5

    def clamp(self) -> PADState:
        """将 PAD 值钳制在 [-1, 1] 范围内"""
        return PADState(
            p=max(-1.0, min(1.0, self.p)),
            a=max(-1.0, min(1.0, self.a)),
            d=max(-1.0, min(1.0, self.d)),
        )

    def to_dict(self) -> dict[str, float]:
        return {"P": round(self.p, 4), "A": round(self.a, 4), "D": round(self.d, 4)}

    @classmethod
    def from_dict(cls, data: dict[str, float], baseline: dict[str, float]) -> PADState:
        state = cls(baseline)
        state.p = data.get("P", baseline["P"])
        state.a = data.get("A", baseline["A"])
        state.d = data.get("D", baseline["D"])
        return state

    def __repr__(self):
        return f"PADState(P={self.p:.3f}, A={self.a:.3f}, D={self.d:.3f})"
