"""本地 Embedding 接口 - 使用 sentence-transformers

优化：使用 asyncio.to_thread() 将模型推理卸载到线程池，
避免阻塞事件循环（树莓派上约 50-200ms）。
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from loguru import logger

# 缓存配置
_CACHE_TTL_SECONDS = float(os.getenv("EMBEDDING_CACHE_TTL", "300"))
_CACHE_MAX_SIZE = int(os.getenv("EMBEDDING_CACHE_MAX", "1000"))

# ⭐ 专用线程池（避免占用默认线程池）
_embedding_executor: ThreadPoolExecutor | None = None
_embedding_cache: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()
_cache_lock: asyncio.Lock = asyncio.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """获取专用线程池（延迟创建）"""
    global _embedding_executor
    if _embedding_executor is None:
        # 单线程足够，embedding 推理是串行任务
        _embedding_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="embedding"
        )
        logger.info("[Embedding] 创建专用线程池 (max_workers=1)")
    return _embedding_executor


def _load_model():
    """加载 sentence-transformers 模型"""
    from sentence_transformers import SentenceTransformer
    from app.config import settings
    
    # 模型路径必须通过环境变量配置
    model_path = settings.LOCAL_EMBEDDING_MODEL_PATH
    if not model_path:
        raise ValueError(
            "LOCAL_EMBEDDING_MODEL_PATH 未配置！"
            "请在 .env 文件中设置模型路径，如：LOCAL_EMBEDDING_MODEL_PATH=models/embedding"
        )
    
    logger.info(f"[Embedding] 加载模型: {model_path}")
    return SentenceTransformer(model_path)


# 模型单例
_model = None


def _get_model():
    """获取模型实例（延迟加载）"""
    global _model
    if _model is None:
        _model = _load_model()
        logger.info("[Embedding] 模型加载完成")
    return _model


def preload_embedding_model():
    """预加载模型（在应用启动时调用）"""
    _get_model()


def _encode_sync(text: str) -> list[float]:
    """同步编码函数（在线程池中执行）"""
    model = _get_model()
    return model.encode(text).tolist()


def _encode_batch_sync(texts: list[str]) -> list[list[float]]:
    """同步批量编码函数（在线程池中执行）"""
    model = _get_model()
    return model.encode(texts).tolist()


async def get_embedding(text: str) -> list[float]:
    """
    获取文本的 embedding 向量（带内存缓存 + TTL + 线程池卸载）
    
    ⭐ 使用 asyncio.to_thread() 将模型推理卸载到线程池，
    避免阻塞事件循环（树莓派上约 50-200ms）。
    """
    now = time.monotonic()
    
    # 检查缓存
    async with _cache_lock:
        cached = _embedding_cache.get(text)
        if cached and (now - cached[0]) <= _CACHE_TTL_SECONDS:
            _embedding_cache.move_to_end(text)
            return cached[1]
    
    # ⭐ 模型推理卸载到线程池
    executor = _get_executor()
    loop = asyncio.get_running_loop()
    embedding = await loop.run_in_executor(executor, _encode_sync, text)
    
    # 更新缓存
    async with _cache_lock:
        _embedding_cache[text] = (time.monotonic(), embedding)
        _embedding_cache.move_to_end(text)
        while len(_embedding_cache) > _CACHE_MAX_SIZE:
            _embedding_cache.popitem(last=False)
    
    return embedding


async def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """
    批量获取 embedding 向量
    
    ⭐ 使用 asyncio.to_thread() 将批量推理卸载到线程池。
    """
    if not texts:
        return []
    
    now = time.monotonic()
    results: list[list[float] | None] = [None] * len(texts)
    uncached_indices: list[int] = []
    uncached_texts: list[str] = []
    
    # 检查缓存
    async with _cache_lock:
        for i, text in enumerate(texts):
            cached = _embedding_cache.get(text)
            if cached and (now - cached[0]) <= _CACHE_TTL_SECONDS:
                results[i] = cached[1]
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)
    
    # ⭐ 批量推理卸载到线程池
    if uncached_texts:
        executor = _get_executor()
        loop = asyncio.get_running_loop()
        new_embeddings = await loop.run_in_executor(
            executor, _encode_batch_sync, uncached_texts
        )
        
        # 更新缓存和结果
        async with _cache_lock:
            for idx, (i, text) in enumerate(zip(uncached_indices, uncached_texts)):
                embedding = new_embeddings[idx]
                results[i] = embedding
                _embedding_cache[text] = (time.monotonic(), embedding)
                _embedding_cache.move_to_end(text)
                while len(_embedding_cache) > _CACHE_MAX_SIZE:
                    _embedding_cache.popitem(last=False)
    
    return results  # type: ignore


def clear_cache():
    """清空 embedding 缓存（测试用）"""
    global _embedding_cache
    _embedding_cache = OrderedDict()


def get_cache_size() -> int:
    """获取缓存大小"""
    return len(_embedding_cache)


async def shutdown_embedding():
    """关闭线程池（应用关闭时调用）"""
    global _embedding_executor
    if _embedding_executor is not None:
        _embedding_executor.shutdown(wait=True)
        _embedding_executor = None
        logger.info("[Embedding] 线程池已关闭")