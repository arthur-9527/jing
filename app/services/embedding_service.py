"""本地 Embedding 服务 - 使用 sentence-transformers"""

import os
from typing import List, Optional

from app.config import settings


class EmbeddingService:
    """文本 Embedding 服务（本地模型）"""
    
    def __init__(self):
        self.model_path = settings.LOCAL_EMBEDDING_MODEL_PATH
        self.dimension = settings.EMBEDDING_DIM
        self._model = None
    
    def _get_model(self):
        """获取模型实例（延迟加载）"""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_path)
        return self._model
    
    async def get_embedding(self, text: str) -> Optional[List[float]]:
        """
        获取文本的 embedding
        
        Args:
            text: 输入文本
            
        Returns:
            embedding 向量 (512维)，如果失败则返回 None
        """
        try:
            model = self._get_model()
            # sentence-transformers 在 async 环境中可安全调用
            embedding = model.encode(text)
            return embedding.tolist()
        except Exception as e:
            print(f"[EmbeddingService] Error: {e}")
            return None
    
    async def get_embeddings(self, texts: List[str]) -> List[Optional[List[float]]]:
        """
        批量获取文本的 embedding
        
        Args:
            texts: 输入文本列表
            
        Returns:
            embedding 向量列表
        """
        if not texts:
            return []
        
        try:
            model = self._get_model()
            # 批量编码，效率更高
            embeddings = model.encode(texts)
            return [emb.tolist() for emb in embeddings]
        except Exception as e:
            print(f"[EmbeddingService] Batch error: {e}")
            return [None] * len(texts)
    
    async def close(self):
        """关闭服务（本地模型无需关闭）"""
        pass


# 全局单例（可选，用于预加载）
_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    """获取 EmbeddingService 单例"""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service


def preload_embedding_model():
    """预加载 embedding 模型（在应用启动时调用）"""
    from app.agent.memory.embedding import preload_embedding_model as preload_agent
    preload_agent()
    
    # 同时预加载 EmbeddingService 的模型
    get_embedding_service()._get_model()
