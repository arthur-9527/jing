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
    - PADState(baseline={"P": 0.5, "A": 0.0, "D": 0.0}) - 从基线字典创建
    - PADState(p=0.5, a=0.0, d=0.0) - 直接创建（dataclass 方式）
    """
    p: float = 0.0  # Pleasure
    a: float = 0.0  # Arousal
    d: float = 0.0  # Dominance
    
    # 基线（可选，用于衰减）
    _baseline: dict[str, float] | None = None
    
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
