"""LLM 响应缓存实现（基于 Zvec 向量索引）。

使用独立的 Zvec 集合存储历史 LLM 响应，通过 HNSW 索引实现 O(log n)
的语义相似度检索，避免对相似问题的重复 LLM 调用。与主知识库集合隔离。

支持定时清理：后台线程定期清理过期缓存条目，默认每小时执行一次。
"""

import asyncio
import os
import threading
import time
import uuid
from typing import Optional

import numpy as np
import zvec

from src.domain.exceptions import VectorStoreError
from src.infrastructure.interfaces.cache_backend import CacheBackend
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_CACHE_PATH = "data/zvec_llm_cache"
DEFAULT_CLEANUP_INTERVAL = 3600


class LLMResponseCache(CacheBackend):
    """基于 Zvec HNSW 索引的 LLM 响应缓存（异步）。

    用向量相似度替代精确 key 匹配：相似问题（语义相近）可直接复用
    历史 LLM 响应。集合与主知识库隔离，过期数据通过 TTL 在查询时
    应用层过滤，无需依赖 Zvec filter 表达式语法。

    线程安全：内部使用 threading.RLock 保护所有 Zvec 集合操作。
    所有公开方法提供异步版本，使用 asyncio.to_thread() 避免阻塞事件循环。

    定时清理：后台线程定期清理过期缓存条目，默认每小时执行一次。
    """

    def __init__(
        self,
        maxsize: int = None,
        ttl: int = None,
        threshold: float = None,
        cache_path: str = None,
        dimension: int = None,
        cleanup_interval: int = None,
    ):
        self._maxsize = maxsize if maxsize is not None else settings.llm_cache.maxsize
        self._ttl = ttl if ttl is not None else settings.llm_cache.ttl
        self._threshold = (
            threshold if threshold is not None else settings.llm_cache.similarity_threshold
        )
        self._dimension = dimension if dimension is not None else settings.zvec.dimension
        self._cache_path = cache_path or settings.zvec.cache_path or DEFAULT_CACHE_PATH
        self._cleanup_interval = cleanup_interval if cleanup_interval is not None else DEFAULT_CLEANUP_INTERVAL
        self._lock = threading.RLock()
        self._collection: Optional[zvec.Collection] = None
        self._stop_event = threading.Event()
        self._cleanup_thread: Optional[threading.Thread] = None
        with self._lock:
            self._init_collection()
        self._start_cleanup_thread()

    def _init_collection(self) -> None:
        """初始化 Zvec 缓存集合：存在则打开，否则创建。"""
        try:
            if os.path.exists(self._cache_path):
                existing_files = os.listdir(self._cache_path)
                if existing_files:
                    self._collection = zvec.open(path=self._cache_path)
                    logger.info(f"LLM 缓存集合已打开: {self._cache_path} (维度: {self._dimension})")
                    return
                os.rmdir(self._cache_path)
            self._collection = zvec.create_and_open(
                path=self._cache_path,
                schema=self._build_schema(),
            )
            logger.info(f"LLM 缓存集合已创建: {self._cache_path} (维度: {self._dimension})")
        except Exception as e:
            logger.error(f"LLM 缓存集合初始化失败: {e}", exc_info=True)
            raise VectorStoreError(f"Failed to initialize LLM cache collection: {e}")

    def _build_schema(self) -> zvec.CollectionSchema:
        """构建缓存集合 Schema：单向量字段 + 响应内容 + 时间戳。"""
        return zvec.CollectionSchema(
            name="llm_response_cache",
            vectors=[
                zvec.VectorSchema(
                    name="query_vector",
                    data_type=zvec.DataType.VECTOR_FP32,
                    dimension=self._dimension,
                    index_param=zvec.HnswIndexParam(
                        metric_type=zvec.MetricType.COSINE,
                        ef_construction=200,
                        m=16,
                    ),
                ),
            ],
            fields=[
                zvec.FieldSchema(
                    name="response",
                    data_type=zvec.DataType.STRING,
                    nullable=False,
                ),
                zvec.FieldSchema(
                    name="created_at",
                    data_type=zvec.DataType.INT64,
                    nullable=False,
                    index_param=zvec.InvertIndexParam(enable_range_optimization=True),
                ),
            ],
        )

    def _normalize(self, vec: np.ndarray) -> np.ndarray:
        """对向量做 L2 归一化，确保余弦相似度计算稳定。"""
        vec = np.asarray(vec, dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm == 0:
            return vec
        return vec / norm

    def get(self, key: np.ndarray) -> Optional[str]:
        """语义检索缓存（同步）。"""
        query_vec = self._normalize(key)
        if query_vec.shape[0] != self._dimension:
            logger.warning(
                f"查询向量维度不匹配: {query_vec.shape[0]} != {self._dimension}"
            )
            return None

        try:
            with self._lock:
                self._ensure_collection()
                now = int(time.time())
                min_created_at = now - self._ttl

                vector_query = zvec.Query(
                    field_name="query_vector",
                    vector=query_vec,
                )
                results = self._collection.query(queries=vector_query, topk=5)

            for r in results:
                similarity = 1 - r.score
                if similarity < self._threshold:
                    break
                created_at = r.fields.get("created_at", 0)
                if created_at >= min_created_at:
                    logger.debug(f"缓存命中，相似度: {similarity:.4f}")
                    return r.fields.get("response", "")
            return None
        except Exception as e:
            logger.error(f"LLM 缓存查询失败: {e}", exc_info=True)
            return None

    async def aget(self, key: np.ndarray) -> Optional[str]:
        """语义检索缓存（异步）。"""
        return await asyncio.to_thread(self.get, key)

    def set(self, key: np.ndarray, value: str) -> None:
        """写入缓存（同步）。"""
        try:
            doc_id = str(uuid.uuid4())
            query_vec = self._normalize(key)
            now = int(time.time())

            with self._lock:
                self._ensure_collection()
                self._evict_if_needed()

                self._collection.insert(
                    zvec.Doc(
                        id=doc_id,
                        vectors={"query_vector": query_vec},
                        fields={
                            "response": value,
                            "created_at": now,
                        },
                    )
                )
                self._collection.flush()
                self._collection.optimize()
                count = self._count()

            logger.debug(f"LLM 缓存已写入: {doc_id} (当前大小: {count})")
        except Exception as e:
            logger.error(f"LLM 缓存写入失败: {e}", exc_info=True)

    async def aset(self, key: np.ndarray, value: str) -> None:
        """写入缓存（异步）。"""
        await asyncio.to_thread(self.set, key, value)

    def _evict_if_needed(self) -> None:
        """软限制：超过 maxsize 时记录告警，依赖 TTL 自然过期。"""
        try:
            count = self._count()
            if count >= self._maxsize:
                logger.warning(
                    f"LLM 缓存已达容量上限 {self._maxsize}（当前: {count}），"
                    f"建议调大 LLM_CACHE_MAXSIZE 或缩短 LLM_CACHE_TTL"
                )
        except Exception as e:
            logger.warning(f"缓存容量检查失败: {e}")

    def _count(self) -> int:
        """返回当前缓存条目数。"""
        self._ensure_collection()
        try:
            return self._collection.stats.doc_count
        except Exception:
            return 0

    def cleanup_expired(self) -> int:
        """主动清理过期缓存条目（同步）。"""
        try:
            with self._lock:
                self._ensure_collection()
                now = int(time.time())
                min_created_at = now - self._ttl
                results = self._collection.query(
                    filter=f"created_at < {min_created_at}",
                    topk=self._maxsize,
                    output_fields=["created_at"],
                )
                expired_ids = [r.id for r in results]
                if not expired_ids:
                    logger.debug("无过期 LLM 缓存需要清理")
                    return 0
                self._collection.delete(ids=expired_ids)
                remaining = self._count()

            logger.info(
                f"已清理 {len(expired_ids)} 条过期 LLM 缓存"
                f"（剩余: {remaining}）"
            )
            return len(expired_ids)
        except Exception as e:
            logger.error(f"清理过期 LLM 缓存失败: {e}", exc_info=True)
            return 0

    async def acleanup_expired(self) -> int:
        """主动清理过期缓存条目（异步）。"""
        return await asyncio.to_thread(self.cleanup_expired)

    def _start_cleanup_thread(self) -> None:
        """启动后台清理线程，定期清理过期缓存。"""
        if self._cleanup_thread is not None:
            return
        
        def _cleanup_loop():
            """后台清理循环：每隔 cleanup_interval 秒执行一次清理。"""
            logger.info(f"LLM 缓存定时清理线程已启动（间隔: {self._cleanup_interval}s）")
            while not self._stop_event.is_set():
                try:
                    self.cleanup_expired()
                except Exception as e:
                    logger.error(f"定时清理过期缓存异常: {e}", exc_info=True)
                
                for _ in range(self._cleanup_interval):
                    if self._stop_event.is_set():
                        break
                    time.sleep(1)
            
            logger.info("LLM 缓存定时清理线程已停止")
        
        self._cleanup_thread = threading.Thread(
            target=_cleanup_loop,
            daemon=True,
            name="LLMResponseCacheCleanup",
        )
        self._cleanup_thread.start()

    def _stop_cleanup_thread(self) -> None:
        """停止后台清理线程。"""
        if self._cleanup_thread is None:
            return
        
        self._stop_event.set()
        self._cleanup_thread.join(timeout=5)
        if self._cleanup_thread.is_alive():
            logger.warning("LLM 缓存清理线程停止超时")
        self._cleanup_thread = None
        self._stop_event.clear()

    def close(self) -> None:
        """关闭并释放 Zvec 集合资源（同步）。"""
        self._stop_cleanup_thread()
        with self._lock:
            if self._collection:
                try:
                    self._collection.destroy()
                    logger.info("LLM 缓存集合已关闭")
                except Exception as e:
                    logger.error(f"关闭 LLM 缓存集合失败: {e}", exc_info=True)
                finally:
                    self._collection = None

    async def aclose(self) -> None:
        """关闭并释放 Zvec 集合资源（异步）。"""
        await asyncio.to_thread(self.close)

    def _ensure_collection(self) -> None:
        """惰性重连：集合未初始化时重新打开。"""
        if self._collection is None:
            self._init_collection()

    def __enter__(self) -> "LLMResponseCache":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    async def __aenter__(self) -> "LLMResponseCache":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.aclose()