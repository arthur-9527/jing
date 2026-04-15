"""本地 Embedding 服务 - 使用 sentence-transformers"""

from __future__ import annotations

import asyncio
import os
import time
from collections import OrderedDict
from typing import Optional

# 缓存配置
_CACHE_TTL_SECONDS = float(os.getenv("EMBEDDING_CACHE_TTL", "300"))
_CACHE_MAX_SIZE = int(os.getenv("EMBEDDING_CACHE_MAX", "1000"))

_embedding_cache: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()
_cache_lock: Optional[asyncio.Lock] = None

# 模型实例
_model = None
_model_path: Optional[str] = None
_model_load_lock = asyncio.Lock()


def _get_cache_lock() -> asyncio.Lock:
    """获取缓存锁（延迟初始化）"""
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


def _get_model_path() -> str:
    """获取模型路径"""
    return os.getenv(
        "LOCAL_EMBEDDING_MODEL_PATH",
        "/home/test/raspi_mmd/models/BAAI_bge-small-zh-v1.5"
    )


def load_model() -> "SentenceTransformer":
    """
    同步加载模型（在事件循环外调用，如模块导入时）
    返回模型实例
    """
    global _model, _model_path
    
    current_path = _get_model_path()
    
    if _model is None or _model_path != current_path:
        from sentence_transformers import SentenceTransformer
        print(f"[LocalEmbedding] 加载模型: {current_path}")
        _model = SentenceTransformer(current_path)
        _model_path = current_path
        print(f"[LocalEmbedding] 模型加载完成，维度: {_model.get_sentence_embedding_dimension()}")
    
    return _model


async def get_model() -> "SentenceTransformer":
    """
    异步获取模型（确保模型已加载）
    """
    # 如果没有事件循环，直接返回同步加载的模型
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 没有运行中的事件循环，同步加载
        return load_model()
    
    # 有事件循环，异步加载
    async with _model_load_lock:
        return load_model()


def encode_sync(text: str) -> list[float]:
    """同步编码单条文本"""
    model = load_model()
    embedding = model.encode(text)
    return embedding.tolist()


async def get_embedding(text: str) -> list[float]:
    """
    获取文本的 embedding 向量（带缓存）
    """
    now = time.monotonic()
    cache_lock = _get_cache_lock()
    
    async with cache_lock:
        # 检查缓存
        cached = _embedding_cache.get(text)
        if cached and (now - cached[0]) <= _CACHE_TTL_SECONDS:
            _embedding_cache.move_to_end(text)
            return cached[1]
    
    # 模型推理（同步，sentence-transformers 会释放 GIL）
    embedding = encode_sync(text)
    
    async with cache_lock:
        _embedding_cache[text] = (time.monotonic(), embedding)
        _embedding_cache.move_to_end(text)
        while len(_embedding_cache) > _CACHE_MAX_SIZE:
            _embedding_cache.popitem(last=False)
    
    return embedding


def encode_batch_sync(texts: list[str]) -> list[list[float]]:
    """同步批量编码"""
    model = load_model()
    embeddings = model.encode(texts)
    return embeddings.tolist()


async def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """
    批量获取 embedding 向量
    """
    # 检查缓存
    cache_lock = _get_cache_lock()
    results: list[Optional[list[float]]] = [None] * len(texts)
    uncached_texts: list[tuple[int, str]] = []
    
    now = time.monotonic()
    async with cache_lock:
        for i, text in enumerate(texts):
            cached = _embedding_cache.get(text)
            if cached and (now - cached[0]) <= _CACHE_TTL_SECONDS:
                results[i] = cached[1]
            else:
                uncached_texts.append((i, text))
    
    # 批量推理未缓存的
    if uncached_texts:
        uncached_texts_only = [t for _, t in uncached_texts]
        new_embeddings = encode_batch_sync(uncached_texts_only)
        
        async with cache_lock:
            for (i, text), embedding in zip(uncached_texts, new_embeddings):
                results[i] = embedding
                _embedding_cache[text] = (time.monotonic(), embedding)
                _embedding_cache.move_to_end(text)
                while len(_embedding_cache) > _CACHE_MAX_SIZE:
                    _embedding_cache.popitem(last=False)
    
    return results


def preload_model():
    """
    预加载模型（在应用启动时调用）
    """
    load_model()
