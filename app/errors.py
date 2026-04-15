#!/usr/bin/env python3
"""
统一错误处理机制

定义标准错误码、异常类和响应格式。
"""

from enum import Enum
from typing import Optional, Any
from pydantic import BaseModel


class ErrorCode(str, Enum):
    """标准错误码"""
    
    # 通用错误 (1xxx)
    UNKNOWN_ERROR = "E1000"
    INVALID_REQUEST = "E1001"
    VALIDATION_ERROR = "E1002"
    NOT_FOUND = "E1003"
    PERMISSION_DENIED = "E1004"
    RATE_LIMITED = "E1005"
    
    # 数据库错误 (2xxx)
    DATABASE_ERROR = "E2000"
    DATABASE_CONNECTION = "E2001"
    DATABASE_QUERY = "E2002"
    
    # 服务错误 (3xxx)
    SERVICE_UNAVAILABLE = "E3000"
    AGENT_SERVICE_ERROR = "E3001"
    TTS_SERVICE_ERROR = "E3002"
    ASR_SERVICE_ERROR = "E3003"
    LLM_SERVICE_ERROR = "E3004"
    OPENCLAW_SERVICE_ERROR = "E3005"
    
    # WebSocket 错误 (4xxx)
    WS_CONNECTION_ERROR = "E4000"
    WS_AUTH_ERROR = "E4001"
    WS_PROTOCOL_ERROR = "E4002"
    
    # 资源错误 (5xxx)
    RESOURCE_NOT_FOUND = "E5000"
    MOTION_NOT_FOUND = "E5001"
    CHARACTER_NOT_FOUND = "E5002"
    
    # 文件错误 (6xxx)
    FILE_UPLOAD_ERROR = "E6000"
    FILE_TOO_LARGE = "E6001"
    FILE_INVALID_FORMAT = "E6002"


class ErrorResponse(BaseModel):
    """标准错误响应格式"""
    
    code: ErrorCode
    message: str
    detail: Optional[str] = None
    data: Optional[Any] = None
    
    class Config:
        use_enum_values = True


class AppException(Exception):
    """应用异常基类"""
    
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        detail: Optional[str] = None,
        data: Optional[Any] = None,
    ):
        self.code = code
        self.message = message
        self.detail = detail
        self.data = data
        super().__init__(message)
    
    def to_response(self) -> ErrorResponse:
        """转换为响应格式"""
        return ErrorResponse(
            code=self.code,
            message=self.message,
            detail=self.detail,
            data=self.data,
        )


# ===== 具体异常类 =====

class DatabaseError(AppException):
    """数据库异常"""
    
    def __init__(self, message: str = "Database error", detail: Optional[str] = None):
        super().__init__(ErrorCode.DATABASE_ERROR, message, detail)


class ServiceUnavailableError(AppException):
    """服务不可用异常"""
    
    def __init__(self, service_name: str = "Service", detail: Optional[str] = None):
        super().__init__(
            ErrorCode.SERVICE_UNAVAILABLE,
            f"{service_name} is unavailable",
            detail,
        )


class AgentServiceError(AppException):
    """Agent 服务异常"""
    
    def __init__(self, message: str = "Agent service error", detail: Optional[str] = None):
        super().__init__(ErrorCode.AGENT_SERVICE_ERROR, message, detail)


class TTSServiceError(AppException):
    """TTS 服务异常"""
    
    def __init__(self, message: str = "TTS service error", detail: Optional[str] = None):
        super().__init__(ErrorCode.TTS_SERVICE_ERROR, message, detail)


class ASRServiceError(AppException):
    """ASR 服务异常"""
    
    def __init__(self, message: str = "ASR service error", detail: Optional[str] = None):
        super().__init__(ErrorCode.ASR_SERVICE_ERROR, message, detail)


class LLMServiceError(AppException):
    """LLM 服务异常"""
    
    def __init__(self, message: str = "LLM service error", detail: Optional[str] = None):
        super().__init__(ErrorCode.LLM_SERVICE_ERROR, message, detail)


class OpenClawServiceError(AppException):
    """OpenClaw 服务异常"""
    
    def __init__(self, message: str = "OpenClaw service error", detail: Optional[str] = None):
        super().__init__(ErrorCode.OPENCLAW_SERVICE_ERROR, message, detail)


class WSConnectionError(AppException):
    """WebSocket 连接异常"""
    
    def __init__(self, message: str = "WebSocket connection error", detail: Optional[str] = None):
        super().__init__(ErrorCode.WS_CONNECTION_ERROR, message, detail)


class WSAuthError(AppException):
    """WebSocket 认证异常"""
    
    def __init__(self, message: str = "WebSocket authentication failed", detail: Optional[str] = None):
        super().__init__(ErrorCode.WS_AUTH_ERROR, message, detail)


class MotionNotFoundError(AppException):
    """动作不存在异常"""
    
    def __init__(self, motion_id: str, detail: Optional[str] = None):
        super().__init__(
            ErrorCode.MOTION_NOT_FOUND,
            f"Motion not found: {motion_id}",
            detail,
            data={"motion_id": motion_id},
        )


class CharacterNotFoundError(AppException):
    """角色不存在异常"""
    
    def __init__(self, character_id: str, detail: Optional[str] = None):
        super().__init__(
            ErrorCode.CHARACTER_NOT_FOUND,
            f"Character not found: {character_id}",
            detail,
            data={"character_id": character_id},
        )


class FileUploadError(AppException):
    """文件上传异常"""
    
    def __init__(self, message: str = "File upload error", detail: Optional[str] = None):
        super().__init__(ErrorCode.FILE_UPLOAD_ERROR, message, detail)


class FileTooLargeError(AppException):
    """文件过大异常"""
    
    def __init__(self, max_size: int, actual_size: int, detail: Optional[str] = None):
        super().__init__(
            ErrorCode.FILE_TOO_LARGE,
            f"File too large: max {max_size} bytes, got {actual_size} bytes",
            detail,
            data={"max_size": max_size, "actual_size": actual_size},
        )


# ===== 错误码映射 =====

ERROR_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.UNKNOWN_ERROR: "Unknown error",
    ErrorCode.INVALID_REQUEST: "Invalid request",
    ErrorCode.VALIDATION_ERROR: "Validation error",
    ErrorCode.NOT_FOUND: "Resource not found",
    ErrorCode.PERMISSION_DENIED: "Permission denied",
    ErrorCode.RATE_LIMITED: "Rate limited",
    
    ErrorCode.DATABASE_ERROR: "Database error",
    ErrorCode.DATABASE_CONNECTION: "Database connection error",
    ErrorCode.DATABASE_QUERY: "Database query error",
    
    ErrorCode.SERVICE_UNAVAILABLE: "Service unavailable",
    ErrorCode.AGENT_SERVICE_ERROR: "Agent service error",
    ErrorCode.TTS_SERVICE_ERROR: "TTS service error",
    ErrorCode.ASR_SERVICE_ERROR: "ASR service error",
    ErrorCode.LLM_SERVICE_ERROR: "LLM service error",
    ErrorCode.OPENCLAW_SERVICE_ERROR: "OpenClaw service error",
    
    ErrorCode.WS_CONNECTION_ERROR: "WebSocket connection error",
    ErrorCode.WS_AUTH_ERROR: "WebSocket authentication error",
    ErrorCode.WS_PROTOCOL_ERROR: "WebSocket protocol error",
    
    ErrorCode.RESOURCE_NOT_FOUND: "Resource not found",
    ErrorCode.MOTION_NOT_FOUND: "Motion not found",
    ErrorCode.CHARACTER_NOT_FOUND: "Character not found",
    
    ErrorCode.FILE_UPLOAD_ERROR: "File upload error",
    ErrorCode.FILE_TOO_LARGE: "File too large",
    ErrorCode.FILE_INVALID_FORMAT: "Invalid file format",
}