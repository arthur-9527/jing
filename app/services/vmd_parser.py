# -*- coding: utf-8 -*-
"""
VMD (Vocaloid Motion Data) binary file parser.
Parses VMD files and extracts bone frame data.

Reference: https://www.nicovideo.jp/watch/sm22639334
VMD File Format Specification
"""
import struct
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field


@dataclass
class BoneFrameData:
    """Single bone frame data."""
    bone_name: str
    frame_number: int
    position: Tuple[float, float, float]  # x, y, z
    rotation: Tuple[float, float, float, float]  # quaternion x, y, z, w
    interpolation: Optional[Tuple[int, ...]] = None  # 64 bytes as tuple for Bezier curves


@dataclass
class VMDData:
    """Parsed VMD file data."""
    model_name: str
    bone_frames: List[BoneFrameData] = field(default_factory=list)
    total_frames: int = 0
    
    def get_frames_by_index(self) -> Dict[int, Dict[str, dict]]:
        """
        Group bone frames by frame index.
        Returns: {frame_index: {bone_name: {"trans": [x,y,z], "quat": [x,y,z,w]}}}
        """
        frames_dict: Dict[int, Dict[str, dict]] = {}
        
        for bf in self.bone_frames:
            if bf.frame_number not in frames_dict:
                frames_dict[bf.frame_number] = {}
            
            frames_dict[bf.frame_number][bf.bone_name] = {
                "trans": list(bf.position),
                "quat": list(bf.rotation)
            }
        
        return frames_dict


class VMDParser:
    """
    Parser for VMD (Vocaloid Motion Data) binary files.
    
    VMD File Structure:
    - Header: 50 bytes (magic 30 bytes + model name 20 bytes)
    - Bone frames: 4 bytes count + 111 bytes per frame
    - Face morphs: 4 bytes count + 23 bytes per frame
    - Camera data (optional)
    - Light data (optional)
    """
    
    HEADER_SIZE = 50
    MAGIC_SIZE = 30
    MODEL_NAME_SIZE = 20
    BONE_NAME_SIZE = 15
    BONE_FRAME_SIZE = 111  # 15 + 4 + 12 + 16 + 64
    
    EXPECTED_MAGIC = b"Vocaloid Motion Data 0002"
    
    def __init__(self):
        self._data: Optional[bytes] = None
        self._offset: int = 0
    
    def parse(self, vmd_path: str) -> VMDData:
        """
        Parse a VMD file and return structured data.
        
        Args:
            vmd_path: Path to the VMD file
            
        Returns:
            VMDData object containing parsed motion data
        """
        path = Path(vmd_path)
        if not path.exists():
            raise FileNotFoundError(f"VMD file not found: {vmd_path}")
        
        with open(path, "rb") as f:
            self._data = f.read()
        
        self._offset = 0
        
        # Parse header
        model_name = self._parse_header()
        
        # Parse bone frames
        bone_frames = self._parse_bone_frames()
        
        # Calculate total frames
        max_frame = 0
        if bone_frames:
            max_frame = max(bf.frame_number for bf in bone_frames)
        
        return VMDData(
            model_name=model_name,
            bone_frames=bone_frames,
            total_frames=max_frame + 1  # frames are 0-indexed
        )
    
    def parse_bytes(self, vmd_data: bytes) -> VMDData:
        """
        Parse VMD from bytes.
        
        Args:
            vmd_data: Raw VMD file bytes
            
        Returns:
            VMDData object containing parsed motion data
        """
        self._data = vmd_data
        self._offset = 0
        
        # Parse header
        model_name = self._parse_header()
        
        # Parse bone frames
        bone_frames = self._parse_bone_frames()
        
        # Calculate total frames
        max_frame = 0
        if bone_frames:
            max_frame = max(bf.frame_number for bf in bone_frames)
        
        return VMDData(
            model_name=model_name,
            bone_frames=bone_frames,
            total_frames=max_frame + 1
        )
    
    def _parse_header(self) -> str:
        """Parse VMD file header."""
        # Read and verify magic
        magic = self._read_bytes(self.MAGIC_SIZE)
        if not magic.startswith(self.EXPECTED_MAGIC):
            raise ValueError(f"Invalid VMD magic: {magic[:26]}")
        
        # Read model name (Shift-JIS encoded)
        model_name_bytes = self._read_bytes(self.MODEL_NAME_SIZE)
        model_name = self._decode_shift_jis(model_name_bytes)
        
        return model_name
    
    def _parse_bone_frames(self) -> List[BoneFrameData]:
        """Parse bone animation frames."""
        # Read frame count
        frame_count = self._read_uint32()
        
        bone_frames = []
        for _ in range(frame_count):
            # Read bone name (15 bytes, Shift-JIS)
            bone_name_bytes = self._read_bytes(self.BONE_NAME_SIZE)
            bone_name = self._decode_shift_jis(bone_name_bytes)
            
            # Read frame number
            frame_number = self._read_uint32()
            
            # Read position (3 floats, 12 bytes)
            pos_x = self._read_float()
            pos_y = self._read_float()
            pos_z = self._read_float()
            
            # Read rotation quaternion (4 floats, 16 bytes)
            rot_x = self._read_float()
            rot_y = self._read_float()
            rot_z = self._read_float()
            rot_w = self._read_float()
            
            # Read interpolation data (64 bytes) for Bezier curve interpolation
            # Format: 4 groups (X, Y, Z, Rotation) x 16 bytes each
            # Each group: (x1, y1, x2, y2) control points + padding
            interp_bytes = self._read_bytes(64)
            interpolation = tuple(interp_bytes)
            
            bone_frames.append(BoneFrameData(
                bone_name=bone_name,
                frame_number=frame_number,
                position=(pos_x, pos_y, pos_z),
                rotation=(rot_x, rot_y, rot_z, rot_w),
                interpolation=interpolation
            ))
        
        return bone_frames
    
    def _read_bytes(self, size: int) -> bytes:
        """Read raw bytes."""
        data = self._data[self._offset:self._offset + size]
        self._offset += size
        return data
    
    def _skip_bytes(self, size: int) -> None:
        """Skip bytes."""
        self._offset += size
    
    def _read_uint32(self) -> int:
        """Read 32-bit unsigned int (little-endian)."""
        data = self._read_bytes(4)
        return struct.unpack("<I", data)[0]
    
    def _read_float(self) -> float:
        """Read 32-bit float (little-endian)."""
        data = self._read_bytes(4)
        return struct.unpack("<f", data)[0]
    
    def _decode_shift_jis(self, data: bytes) -> str:
        """Decode Shift-JIS bytes to string, stripping null bytes."""
        # Find null terminator
        null_idx = data.find(b"\x00")
        if null_idx >= 0:
            data = data[:null_idx]
        
        try:
            return data.decode("shift-jis")
        except UnicodeDecodeError:
            # Fallback: try to decode as much as possible
            return data.decode("shift-jis", errors="replace")


# Convenience function
def parse_vmd(vmd_path: str) -> VMDData:
    """Parse a VMD file."""
    parser = VMDParser()
    return parser.parse(vmd_path)


def parse_vmd_bytes(vmd_data: bytes) -> VMDData:
    """Parse VMD from bytes."""
    parser = VMDParser()
    return parser.parse_bytes(vmd_data)