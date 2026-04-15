# -*- coding: utf-8 -*-
"""
VMD 插值服务 - 完全参考 text2vmd 的 batch_upload_service 实现

功能：
1. 对缺失帧进行 Bezier 曲线插值
2. 过滤 identity bones（静止骨骼）
3. 生成完整的帧序列（每帧都有所有骨骼数据）
"""
import numpy as np
from typing import Dict, List, Tuple, Optional, Any
from loguru import logger

from app.services.vmd_parser import VMDData, BoneFrameData


class VMDInterpolationService:
    """
    VMD 插值服务
    
    将稀疏的 VMD 关键帧插值为完整的帧序列，
    每帧都包含所有骨骼的完整数据。
    """
    
    def bezier_interpolate(self, t: float, x1: int, y1: int, x2: int, y2: int) -> float:
        """
        计算 Bezier 曲线插值
        
        VMD 使用三次 Bezier 曲线：
        - 起点: (0, 0)
        - 控制点 1: (x1/127, y1/127)
        - 控制点 2: (x2/127, y2/127)
        - 终点: (1, 1)
        
        Args:
            t: 时间参数 [0, 1]
            x1, y1: 第一个控制点 (0-127)
            x2, y2: 第二个控制点 (0-127)
            
        Returns:
            插值后的 y 值 [0, 1]
        """
        # 归一化控制点
        cx1 = x1 / 127.0
        cy1 = y1 / 127.0
        cx2 = x2 / 127.0
        cy2 = y2 / 127.0
        
        # 使用 Newton-Raphson 方法求解参数 s
        s = t
        
        for _ in range(10):  # 最大迭代次数
            # Bezier x(s) = 3*cx1*s*(1-s)^2 + 3*cx2*s^2*(1-s) + s^3
            x = 3 * cx1 * s * (1 - s) ** 2 + 3 * cx2 * s ** 2 * (1 - s) + s ** 3
            
            if abs(x - t) < 1e-6:
                break
            
            # 导数: dx/ds
            dx = 3 * cx1 * (1 - s) ** 2 - 6 * cx1 * s * (1 - s) + \
                 6 * cx2 * s * (1 - s) - 3 * cx2 * s ** 2 + 3 * s ** 2
            
            if abs(dx) < 1e-10:
                break
                
            s = s - (x - t) / dx
            s = max(0, min(1, s))  # 限制在 [0, 1]
        
        # 计算 y(s)
        y = 3 * cy1 * s * (1 - s) ** 2 + 3 * cy2 * s ** 2 * (1 - s) + s ** 3
        
        return y
    
    def extract_bezier_params(self, interpolation: Optional[Tuple[int, ...]], axis: str) -> Tuple[int, int, int, int]:
        """
        从插值数据中提取 Bezier 控制点
        
        VMD 插值数据布局 (64 字节):
        - 字节 0-15: X 轴（位置 X）
        - 字节 16-31: Y 轴（位置 Y）
        - 字节 32-47: Z 轴（位置 Z）
        - 字节 48-63: 旋转
        
        每个轴有: x1, y1, x2, y2 控制点
        
        Args:
            interpolation: 64 字节元组
            axis: 'x', 'y', 'z', 或 'r'（旋转）
            
        Returns:
            (x1, y1, x2, y2) 控制点
        """
        if interpolation is None or len(interpolation) < 64:
            # 默认为线性插值
            return (20, 20, 107, 107)
        
        axis_offset = {'x': 0, 'y': 16, 'z': 32, 'r': 48}
        offset = axis_offset.get(axis.lower(), 48)
        
        # VMD 存储格式: 每个曲线有 x1_a, y1_a, x2_a, y2_a
        x1 = interpolation[offset]
        y1 = interpolation[offset + 4]
        x2 = interpolation[offset + 8]
        y2 = interpolation[offset + 12]
        
        return (x1, y1, x2, y2)
    
    def slerp(self, q1: List[float], q2: List[float], t: float) -> List[float]:
        """
        四元数球面线性插值
        
        Args:
            q1: 起始四元数 [x, y, z, w]
            q2: 目标四元数 [x, y, z, w]
            t: 插值参数 [0, 1]
            
        Returns:
            插值后的四元数 [x, y, z, w]
        """
        q1 = np.array(q1, dtype=np.float64)
        q2 = np.array(q2, dtype=np.float64)
        
        # 归一化
        q1 = q1 / np.linalg.norm(q1)
        q2 = q2 / np.linalg.norm(q2)
        
        # 计算点积
        dot = np.dot(q1, q2)
        
        # 取最短路径
        if dot < 0:
            q2 = -q2
            dot = -dot
        
        dot = np.clip(dot, -1.0, 1.0)
        
        # 如果四元数非常接近，使用线性插值
        if dot > 0.9995:
            result = q1 + t * (q2 - q1)
            return (result / np.linalg.norm(result)).tolist()
        
        # SLERP
        theta_0 = np.arccos(dot)
        theta = theta_0 * t
        
        q3 = q2 - q1 * dot
        q3 = q3 / np.linalg.norm(q3)
        
        result = q1 * np.cos(theta) + q3 * np.sin(theta)
        return (result / np.linalg.norm(result)).tolist()
    
    def lerp(self, v1: List[float], v2: List[float], t: float) -> List[float]:
        """线性插值（用于位置）"""
        return [
            v1[0] + (v2[0] - v1[0]) * t,
            v1[1] + (v2[1] - v1[1]) * t,
            v1[2] + (v2[2] - v1[2]) * t
        ]
    
    def interpolate_missing_frames(
        self,
        frames_dict: Dict[int, Dict[str, dict]],
        total_frames: int,
        keyframe_data: Dict[int, Dict[str, BoneFrameData]]
    ) -> Dict[int, Dict[str, dict]]:
        """
        使用 Bezier 曲线插值缺失的帧
        
        确保生成从 0 到 total_frames-1 的所有帧
        
        Args:
            frames_dict: 现有帧 {frame_idx: {bone_name: {trans, quat}}}
            total_frames: 期望的总帧数
            keyframe_data: 带插值参数的原始关键帧数据
            
        Returns:
            完整的帧字典，包含 0 到 total_frames-1 的所有帧
        """
        existing_frames = sorted(frames_dict.keys())
        
        if not existing_frames:
            logger.warning("[VMDInterpolation] 没有现有帧")
            return frames_dict
        
        # 获取所有出现在任何关键帧中的骨骼名称
        all_bones = set()
        for frame_data in frames_dict.values():
            all_bones.update(frame_data.keys())
        
        logger.info(f"[VMDInterpolation] 插值: 现有帧={len(existing_frames)}, 总帧数={total_frames}, 骨骼数={len(all_bones)}")
        
        # 构建查找表：每个骨骼的所有关键帧
        bone_keyframes: Dict[str, List[int]] = {bone: [] for bone in all_bones}
        for frame_idx, frame_data in frames_dict.items():
            for bone_name in frame_data.keys():
                bone_keyframes[bone_name].append(frame_idx)
        
        # 对每个骨骼的帧排序
        for bone_name in bone_keyframes:
            bone_keyframes[bone_name] = sorted(bone_keyframes[bone_name])
        
        # 生成 0 到 total_frames-1 的所有帧
        result_frames: Dict[int, Dict[str, dict]] = {}
        
        for frame_idx in range(total_frames):
            if frame_idx in frames_dict:
                # 使用现有帧数据
                result_frames[frame_idx] = frames_dict[frame_idx].copy()
            else:
                # 插值这一帧
                result_frames[frame_idx] = {}
                
                for bone_name in all_bones:
                    kf_list = bone_keyframes[bone_name]
                    
                    if not kf_list:
                        continue
                    
                    # 找到这一帧的前后关键帧
                    prev_kf = None
                    next_kf = None
                    
                    for kf in kf_list:
                        if kf <= frame_idx:
                            prev_kf = kf
                        if kf >= frame_idx and next_kf is None:
                            next_kf = kf
                            break
                    
                    if prev_kf is None and next_kf is None:
                        continue
                    
                    # 如果只有前或只有后，保持该值
                    if prev_kf is None:
                        # 在第一个关键帧之前：使用第一个关键帧的值
                        result_frames[frame_idx][bone_name] = frames_dict[next_kf][bone_name].copy()
                        continue
                    
                    if next_kf is None or next_kf == prev_kf:
                        # 在最后一个关键帧之后或在关键帧上：使用最后的已知值
                        result_frames[frame_idx][bone_name] = frames_dict[prev_kf][bone_name].copy()
                        continue
                    
                    # 在 prev_kf 和 next_kf 之间插值
                    start_bone = frames_dict[prev_kf].get(bone_name)
                    end_bone = frames_dict[next_kf].get(bone_name)
                    
                    if start_bone is None or end_bone is None:
                        continue
                    
                    t_linear = (frame_idx - prev_kf) / (next_kf - prev_kf)
                    
                    # 从结束帧获取插值参数
                    interp_params = None
                    if next_kf in keyframe_data and bone_name in keyframe_data[next_kf]:
                        bf_data = keyframe_data[next_kf][bone_name]
                        interp_params = bf_data.interpolation
                    
                    # 使用 Bezier 进行位置插值
                    if interp_params:
                        tx = self.bezier_interpolate(t_linear, *self.extract_bezier_params(interp_params, 'x'))
                        ty = self.bezier_interpolate(t_linear, *self.extract_bezier_params(interp_params, 'y'))
                        tz = self.bezier_interpolate(t_linear, *self.extract_bezier_params(interp_params, 'z'))
                        tr = self.bezier_interpolate(t_linear, *self.extract_bezier_params(interp_params, 'r'))
                        
                        # 对每个轴分别插值位置
                        trans = [
                            start_bone['trans'][0] + (end_bone['trans'][0] - start_bone['trans'][0]) * tx,
                            start_bone['trans'][1] + (end_bone['trans'][1] - start_bone['trans'][1]) * ty,
                            start_bone['trans'][2] + (end_bone['trans'][2] - start_bone['trans'][2]) * tz
                        ]
                        
                        # 使用 Bezier 加权的 SLERP 插值旋转
                        quat = self.slerp(start_bone['quat'], end_bone['quat'], tr)
                    else:
                        # 回退到线性插值
                        trans = self.lerp(start_bone['trans'], end_bone['trans'], t_linear)
                        quat = self.slerp(start_bone['quat'], end_bone['quat'], t_linear)
                    
                    result_frames[frame_idx][bone_name] = {
                        'trans': trans,
                        'quat': quat
                    }
        
        logger.info(f"[VMDInterpolation] 插值完成: 生成了 {len(result_frames)} 帧")
        return result_frames
    
    def filter_identity_bones(self, bone_data: Dict[str, dict]) -> Dict[str, dict]:
        """
        过滤掉恒等变换的骨骼（静止的骨骼）
        
        移除位置为 [0,0,0] 且旋转为 [0,0,0,1] 的骨骼
        
        Args:
            bone_data: {bone_name: {trans: [x,y,z], quat: [x,y,z,w]}}
            
        Returns:
            过滤后的骨骼数据
        """
        filtered = {}
        
        for bone_name, data in bone_data.items():
            trans = data.get('trans', [0, 0, 0])
            quat = data.get('quat', [0, 0, 0, 1])
            
            # 检查是否是恒等变换
            is_identity_pos = all(abs(v) < 1e-6 for v in trans)
            is_identity_rot = (
                abs(quat[0]) < 1e-6 and
                abs(quat[1]) < 1e-6 and
                abs(quat[2]) < 1e-6 and
                abs(quat[3] - 1.0) < 1e-6
            )
            
            if not (is_identity_pos and is_identity_rot):
                filtered[bone_name] = data
        
        return filtered
    
    def process_vmd(self, vmd_data: VMDData) -> Tuple[Dict[int, Dict[str, dict]], int, int]:
        """
        处理 VMD 数据：插值并过滤
        
        Args:
            vmd_data: 解析后的 VMD 数据
            
        Returns:
            Tuple of (完整帧数据, 插值的帧数, 过滤的骨骼数)
        """
        # 获取原始帧数据（按帧索引分组）
        frames_dict = vmd_data.get_frames_by_index()
        original_frame_count = len(frames_dict)
        
        logger.info(f"[VMDInterpolation] 处理 VMD: {len(vmd_data.bone_frames)} 骨骼帧, {vmd_data.total_frames} 总帧")
        
        # 构建关键帧数据查找表（包含插值参数）
        keyframe_data: Dict[int, Dict[str, BoneFrameData]] = {}
        for bf in vmd_data.bone_frames:
            if bf.frame_number not in keyframe_data:
                keyframe_data[bf.frame_number] = {}
            keyframe_data[bf.frame_number][bf.bone_name] = bf
        
        # 插值缺失的帧
        frames_dict = self.interpolate_missing_frames(
            frames_dict, 
            vmd_data.total_frames,
            keyframe_data
        )
        frames_interpolated = len(frames_dict) - original_frame_count
        
        # 过滤 identity bones 并统计
        total_bones_before = sum(len(frame) for frame in frames_dict.values())
        for frame_idx in frames_dict:
            frames_dict[frame_idx] = self.filter_identity_bones(frames_dict[frame_idx])
        total_bones_after = sum(len(frame) for frame in frames_dict.values())
        bones_filtered = total_bones_before - total_bones_after
        
        logger.info(f"[VMDInterpolation] 处理完成:")
        logger.info(f"  - 帧插值: {frames_interpolated}")
        logger.info(f"  - 骨骼过滤: {bones_filtered}")
        logger.info(f"  - 最终帧数: {len(frames_dict)}")
        
        return frames_dict, frames_interpolated, bones_filtered


# 单例实例
vmd_interpolation_service = VMDInterpolationService()