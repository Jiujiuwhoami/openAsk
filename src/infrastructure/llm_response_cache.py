"""LLM 响应缓存实现（基于 Zvec 向量索引）。

使用独立的 Zvec 集合存储历史 LLM 响应，通过 HNSW 索引实现 O(log n)
的语义相似度检索，避免对相似问题的重复 LLM 调用。与主知识库集合隔离。
"""

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


class LLMResponseCache(CacheBackend):
    """基于 Zvec HNSW 索引的 LLM 响应缓存。

    用向量相似度替代精确 key 匹配：相似问题（语义相近）可直接复用
    历史 LLM 响应。集合与主知识库隔离，过期数据通过 TTL 在查询时
    应用层过滤，无需依赖 Zvec filter 表达式语法。

    线程安全：内部使用 threading.RLock 保护所有 Zvec 集合操作
    （query / insert / delete / destroy），支持在 FastAPI 多线程
    请求场景下安全并发调用。
    """

    def __init__(
        self,
        maxsize: int = None,
        ttl: int = None,
        threshold: float = None,
        cache_path: str = None,
        dimension: int = None,
    ):
        self._maxsize = maxsize if maxsize is not None else settings.llm_cache.maxsize
        self._ttl = ttl if ttl is not None else settings.llm_cache.ttl
        self._threshold = (
            threshold if threshold is not None else settings.llm_cache.similarity_threshold
        )
        self._dimension = dimension if dimension is not None else settings.zvec.dimension
        self._cache_path = cache_path or settings.zvec.cache_path or DEFAULT_CACHE_PATH
        self._lock = threading.RLock()  # 可重入锁，保护所有 Zvec 集合操作
        self._collection: Optional[zvec.Collection] = None
        with self._lock:
            self._init_collection()

    def _init_collection(self) -> None:
        """初始化 Zvec 缓存集合：存在则打开，否则创建。"""
        try:
            if os.path.exists(self._cache_path):
                existing_files = os.listdir(self._cache_path)
                if existing_files:
                    self._collection = zvec.open(path=self._cache_path)
                    logger.info(f"LLM 缓存集合已打开: {self._cache_path} (维度: {self._dimension})")
                    return
                # 目录存在但为空，删除后重建
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
        """对向量做 L2 归一化，确保余弦相似度计算稳定（避免精度/量纲差异）。"""
        vec = np.asarray(vec, dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm == 0:
            return vec
        return vec / norm

    def get(self, key: np.ndarray) -> Optional[str]:
        """语义检索缓存：查询 top-K 候选，应用层过滤过期数据，返回首个命中。

        线程安全：内部使用 RLock 保护 Zvec 集合查询操作。
        """
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
                # 查询多个候选，应用层过滤掉 TTL 已过期的记录
                results = self._collection.query(queries=vector_query, topk=5)

            # Zvec 返回结果是独立副本，可在锁外遍历
            for r in results:
                if r.score < self._threshold:
                    break
                created_at = r.fields.get("created_at", 0)
                if created_at >= min_created_at:
                    logger.debug(f"缓存命中，相似度: {r.score:.4f}")
                    return r.fields.get("response", "")
                # 相似但已过期，继续看下一个候选

            return None
        except Exception as e:
            # 缓存查询失败不应阻断主流程，降级为未命中
            logger.error(f"LLM 缓存查询失败: {e}", exc_info=True)
            return None

    def set(self, key: np.ndarray, value: str) -> None:
        """写入缓存：生成唯一 id，记录时间戳。

        线程安全：内部使用 RLock 保护 Zvec 集合写入操作。
        """
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
                count = self._count()

            logger.debug(f"LLM 缓存已写入: {doc_id} (当前大小: {count})")
        except Exception as e:
            # 写入失败不阻断主流程
            logger.error(f"LLM 缓存写入失败: {e}", exc_info=True)

    def _evict_if_needed(self) -> None:
        """软限制：超过 maxsize 时记录告警，依赖 TTL 自然过期。

        Zvec 不支持按标量字段排序查询的纯标量检索，因此不强制删除
        旧数据；超过容量时提示调大 maxsize 或缩短 ttl。
        """
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
        """主动清理过期缓存条目，返回删除的条数。

        基于 created_at 时间戳做纯标量过滤查询（不带向量），找出所有
        超过 TTL 的记录并批量删除。建议在低峰期定时调用（如每小时一次），
        以控制集合体积，避免 HNSW 索引膨胀影响查询性能。

        线程安全：内部使用 RLock 保护整个查询+删除操作。

        Returns:
            本次清理删除的条目数；查询或删除失败时返回 0。
        """
        try:
            with self._lock:
                self._ensure_collection()
                now = int(time.time())
                min_created_at = now - self._ttl
                # 纯标量查询：queries 留空，仅按 filter 过滤
                results = self._collection.query(
                    filter=f"created_at < {min_created_at}",
                    topk=self._maxsize,
                    output_fields=["created_at"],
                )
                expired_ids = [r.id for r in results]
                if not expired_ids:
                    logger.debug("无过期 LLM 缓存需要清理")
                    return 0
                # 批量删除
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

    def close(self) -> None:
        """关闭并释放 Zvec 集合资源。

        线程安全：获取锁后再 destroy，防止与查询/写入操作并发。
        """
        with self._lock:
            if self._collection:
                try:
                    self._collection.destroy()
                    logger.info("LLM 缓存集合已关闭")
                except Exception as e:
                    logger.error(f"关闭 LLM 缓存集合失败: {e}", exc_info=True)
                finally:
                    self._collection = None

    def _ensure_collection(self) -> None:
        """惰性重连：集合未初始化时重新打开。

        调用方必须已持有 self._lock。采用双重检查锁定模式：
        公开方法先在锁外快速判断一次（减少锁竞争），进入锁后再判断一次。
        这里是锁内判断分支，由各公开方法统一在 with self._lock 内调用。
        """
        if self._collection is None:
            self._init_collection()

    def __enter__(self) -> "LLMResponseCache":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
