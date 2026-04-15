"""
插值引擎 - 关键帧插值 & 动作过渡

功能：
1. interpolate_keyframes: 将 DB 中 ~10fps 关键帧插值到目标帧率 (30fps)
2. interpolate_transition: 两个动作之间的过渡插值
"""

import math
import numpy as np
from typing import Optional

from .types import VPDFrame, BoneFrame, MorphFrame


def _quat_slerp(q0: np.ndarray, q1: np.ndarray, t: float) -> np.ndarray:
    """
    四元数球面线性插值 (SLERP)

    Args:
        q0: 起始四元数 [x, y, z, w]
        q1: 目标四元数 [x, y, z, w]
        t: 插值因子 0.0 ~ 1.0

    Returns:
        插值后的四元数 [x, y, z, w]
    """
    # 归一化
    q0 = q0 / (np.linalg.norm(q0) + 1e-10)
    q1 = q1 / (np.linalg.norm(q1) + 1e-10)

    # 点积判断方向
    dot = np.dot(q0, q1)

    # 如果点积为负，翻转一个四元数（取最短路径）
    if dot < 0.0:
        q1 = -q1
        dot = -dot

    dot = min(dot, 1.0)

    # 角度很小时退化为 LERP，避免除零
    if dot > 0.9995:
        result = q0 + t * (q1 - q0)
        return result / (np.linalg.norm(result) + 1e-10)

    theta_0 = math.acos(dot)
    theta = theta_0 * t
    sin_theta = math.sin(theta)
    sin_theta_0 = math.sin(theta_0)

    s0 = math.cos(theta) - dot * sin_theta / sin_theta_0
    s1 = sin_theta / sin_theta_0

    result = s0 * q0 + s1 * q1
    return result / (np.linalg.norm(result) + 1e-10)


def _lerp(a: list[float], b: list[float], t: float) -> list[float]:
    """线性插值"""
    return [a[i] + (b[i] - a[i]) * t for i in range(len(a))]


def _interpolate_two_frames(
    frame_a: VPDFrame,
    frame_b: VPDFrame,
    t: float,
    fi: int = 0,
) -> VPDFrame:
    """
    在两帧之间插值，生成中间帧。

    Args:
        frame_a: 起始帧
        frame_b: 目标帧
        t: 插值因子 0.0 ~ 1.0
        fi: 输出帧的帧序号
    """
    # 构建 frame_b 的骨骼名称映射
    b_bone_map: dict[str, BoneFrame] = {b.name: b for b in frame_b.bones}

    interpolated_bones: list[BoneFrame] = []

    for bone_a in frame_a.bones:
        bone_b = b_bone_map.get(bone_a.name)

        if bone_b is None:
            # 目标帧缺少此骨骼，插值到零位 (bind pose offset = 0)
            translation = _lerp(bone_a.translation, [0.0, 0.0, 0.0], t)
            q_a = np.array(bone_a.quaternion, dtype=np.float64)
            q_identity = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float64)
            q_result = _quat_slerp(q_a, q_identity, t)
        else:
            translation = _lerp(bone_a.translation, bone_b.translation, t)
            q_a = np.array(bone_a.quaternion, dtype=np.float64)
            q_b = np.array(bone_b.quaternion, dtype=np.float64)
            q_result = _quat_slerp(q_a, q_b, t)

        interpolated_bones.append(BoneFrame(
            name=bone_a.name,
            translation=translation,
            quaternion=q_result.tolist(),
        ))

    # 目标帧中有但起始帧没有的骨骼，从零位插值过去
    a_bone_names = {b.name for b in frame_a.bones}
    for bone_b in frame_b.bones:
        if bone_b.name not in a_bone_names:
            translation = _lerp([0.0, 0.0, 0.0], bone_b.translation, t)
            q_identity = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float64)
            q_b = np.array(bone_b.quaternion, dtype=np.float64)
            q_result = _quat_slerp(q_identity, q_b, t)
            interpolated_bones.append(BoneFrame(
                name=bone_b.name,
                translation=translation,
                quaternion=q_result.tolist(),
            ))

    # morphs 也做线性插值
    morphs = _interpolate_morphs(frame_a.morphs, frame_b.morphs, t)

    return VPDFrame(bones=interpolated_bones, morphs=morphs, fi=fi)


def _interpolate_morphs(
    morphs_a: list[MorphFrame],
    morphs_b: list[MorphFrame],
    t: float,
) -> list[MorphFrame]:
    """morph 权重线性插值"""
    b_map = {m.name: m.weight for m in morphs_b}
    a_names = set()
    result: list[MorphFrame] = []

    for m in morphs_a:
        a_names.add(m.name)
        target_weight = b_map.get(m.name, 0.0)
        weight = m.weight + (target_weight - m.weight) * t
        result.append(MorphFrame(name=m.name, weight=weight))

    for m in morphs_b:
        if m.name not in a_names:
            result.append(MorphFrame(name=m.name, weight=m.weight * t))

    return result


# === 公开 API ===


def interpolate_keyframes(
    keyframes: list[VPDFrame],
    target_fps: int = 30,
    original_fps: int = 10,
) -> list[VPDFrame]:
    """
    将稀疏关键帧插值到目标帧率。

    例如 DB 存了 10fps 的关键帧，插值到 30fps，
    每两个关键帧之间插入 2 个中间帧。

    Args:
        keyframes: DB 中的关键帧列表（已按 frame_index 排序）
        target_fps: 目标帧率
        original_fps: 关键帧的原始帧率（DB 中的采样率）

    Returns:
        插值后的完整帧列表
    """
    if not keyframes:
        return []

    if len(keyframes) == 1:
        keyframes[0].fi = 0
        return [keyframes[0]]

    # 计算每两个关键帧之间需要的总帧数
    ratio = target_fps / original_fps  # 例如 30/10 = 3
    steps = max(1, round(ratio))  # 每段插 steps 帧（含起始帧）

    result: list[VPDFrame] = []
    fi_counter = 0

    for i in range(len(keyframes) - 1):
        frame_a = keyframes[i]
        frame_b = keyframes[i + 1]

        # 起始帧
        frame_a.fi = fi_counter
        result.append(frame_a)
        fi_counter += 1

        # 中间插值帧
        for s in range(1, steps):
            t = s / steps
            interp_frame = _interpolate_two_frames(frame_a, frame_b, t, fi=fi_counter)
            result.append(interp_frame)
            fi_counter += 1

    # 最后一帧
    keyframes[-1].fi = fi_counter
    result.append(keyframes[-1])

    return result


def interpolate_transition(
    from_frame: VPDFrame,
    to_frame: VPDFrame,
    steps: int = 10,
    start_fi: int = 0,
) -> list[VPDFrame]:
    """
    生成两帧之间的过渡插值帧（用于动作切换/打断）。

    Args:
        from_frame: 当前帧（过渡起点）
        to_frame: 目标帧（过渡终点）
        steps: 过渡帧数
        start_fi: 起始帧序号

    Returns:
        过渡帧列表（不含 from_frame 和 to_frame 本身）
    """
    result: list[VPDFrame] = []
    for i in range(1, steps + 1):
        # 使用包含终点的插值，确保目标帧缺失骨骼时能被清零
        t = i / steps  # 0 < t <= 1，包含终点
        frame = _interpolate_two_frames(from_frame, to_frame, t, fi=start_fi + i - 1)
        result.append(frame)
    return result
