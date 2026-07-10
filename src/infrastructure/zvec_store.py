"""Zvec 向量存储实现。"""

import os
import threading
from typing import List, Optional

import numpy as np
import zvec

from src.domain.exceptions import VectorStoreError
from src.domain.models import Document, SearchResult
from src.infrastructure.interfaces.vector_store import VectorStore
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ZvecStore(VectorStore):
    """基于 Zvec 的向量存储实现。

    线程安全：内部使用 threading.RLock 保护所有 Zvec 集合操作
    （query / insert / upsert / delete / destroy），支持在 FastAPI 多线程
    请求场景下安全并发调用。
    """

    def __init__(self, data_path: str = None, dimension: int = None):
        self._data_path = data_path or settings.zvec.data_path
        self._dimension = dimension or settings.zvec.dimension
        self._lock = threading.RLock()  # 可重入锁，保护所有 Zvec 集合操作
        self._collection: Optional[zvec.Collection] = None
        with self._lock:
            self._init_collection()

    def _init_collection(self) -> None:
        try:
            if os.path.exists(self._data_path):
                existing_files = os.listdir(self._data_path)
                if existing_files:
                    self._collection = zvec.open(path=self._data_path)
                    logger.info(f"Zvec 集合已打开: {self._data_path} (维度: {self._dimension})")
                    return
                # 目录存在但为空，删除后重建
                os.rmdir(self._data_path)
            self._collection = zvec.create_and_open(
                path=self._data_path,
                schema=self._build_schema(),
            )
            logger.info(f"Zvec 集合已创建: {self._data_path} (维度: {self._dimension})")
        except Exception as e:
            logger.error(f"Zvec 集合初始化失败: {e}", exc_info=True)
            raise VectorStoreError(f"Failed to initialize Zvec collection: {e}")

    def _build_schema(self) -> zvec.CollectionSchema:
        return zvec.CollectionSchema(
            name="knowledge_base",
            vectors=[
                zvec.VectorSchema(
                    name="dense_embedding",
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
                    name="content",
                    data_type=zvec.DataType.STRING,
                    nullable=False,
                    index_param=zvec.FtsIndexParam(
                        tokenizer_name="jieba",
                        filters=["lowercase"],
                    ),
                ),
                zvec.FieldSchema(
                    name="title",
                    data_type=zvec.DataType.STRING,
                    nullable=False,
                    index_param=zvec.FtsIndexParam(
                        tokenizer_name="jieba",
                        filters=["lowercase"],
                    ),
                ),
                zvec.FieldSchema(
                    name="tags",
                    data_type=zvec.DataType.ARRAY_STRING,
                    nullable=True,
                    index_param=zvec.InvertIndexParam(),
                ),
                zvec.FieldSchema(
                    name="status",
                    data_type=zvec.DataType.STRING,
                    nullable=False,
                    index_param=zvec.InvertIndexParam(),
                ),
                zvec.FieldSchema(
                    name="doc_id",
                    data_type=zvec.DataType.STRING,
                    nullable=False,
                    index_param=zvec.InvertIndexParam(),
                ),
                zvec.FieldSchema(
                    name="created_at",
                    data_type=zvec.DataType.INT64,
                    nullable=False,
                    index_param=zvec.InvertIndexParam(enable_range_optimization=True),
                ),
                zvec.FieldSchema(
                    name="updated_at",
                    data_type=zvec.DataType.INT64,
                    nullable=False,
                    index_param=zvec.InvertIndexParam(enable_range_optimization=True),
                ),
                zvec.FieldSchema(
                    name="source",
                    data_type=zvec.DataType.STRING,
                    nullable=True,
                ),
            ],
        )

    def insert(self, doc: Document, dense_vector: np.ndarray) -> None:
        """插入文档到向量库。

        线程安全：内部使用 RLock 保护 Zvec 集合写入操作。
        """
        try:
            with self._lock:
                self._ensure_collection()
                self._collection.insert(
                    zvec.Doc(
                        id=doc.doc_id,
                        vectors={"dense_embedding": dense_vector},
                        fields={
                            "content": doc.content,
                            "title": doc.title,
                            "tags": doc.tags,
                            "status": "active",
                            "doc_id": doc.doc_id,
                            "created_at": doc.created_at,
                            "updated_at": doc.updated_at,
                            "source": doc.source or "",
                        },
                    )
                )
            logger.debug(f"文档已插入: {doc.doc_id}")
        except Exception as e:
            logger.error(f"插入文档失败: {doc.doc_id} | {e}", exc_info=True)
            raise VectorStoreError(f"Failed to insert document: {e}")

    def upsert(self, doc: Document, dense_vector: np.ndarray) -> None:
        """更新或插入文档到向量库。

        线程安全：内部使用 RLock 保护 Zvec 集合写入操作。
        """
        try:
            with self._lock:
                self._ensure_collection()
                self._collection.upsert(
                    zvec.Doc(
                        id=doc.doc_id,
                        vectors={"dense_embedding": dense_vector},
                        fields={
                            "content": doc.content,
                            "title": doc.title,
                            "tags": doc.tags,
                            "status": "active",
                            "doc_id": doc.doc_id,
                            "created_at": doc.created_at,
                            "updated_at": doc.updated_at,
                            "source": doc.source or "",
                        },
                    )
                )
            logger.debug(f"文档已 upsert: {doc.doc_id}")
        except Exception as e:
            logger.error(f"Upsert 文档失败: {doc.doc_id} | {e}", exc_info=True)
            raise VectorStoreError(f"Failed to upsert document: {e}")

    def delete(self, doc_id: str) -> bool:
        """删除指定文档。

        线程安全：内部使用 RLock 保护 Zvec 集合删除操作。
        """
        try:
            with self._lock:
                self._ensure_collection()
                self._collection.delete(ids=[doc_id])
            logger.debug(f"文档已删除: {doc_id}")
            return True
        except Exception as e:
            logger.error(f"删除文档失败: {doc_id} | {e}", exc_info=True)
            raise VectorStoreError(f"Failed to delete document: {e}")

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 5,
        filter_expr: Optional[str] = None,
    ) -> List[SearchResult]:
        """向量检索，返回 top-K 最相似的文档。

        线程安全：内部使用 RLock 保护 Zvec 集合查询操作。
        """
        try:
            with self._lock:
                self._ensure_collection()
                vector_query = zvec.Query(
                    field_name="dense_embedding",
                    vector=query_vector,
                )
                if filter_expr:
                    results = self._collection.query(
                        queries=vector_query,
                        topk=top_k,
                        filter=filter_expr,
                    )
                else:
                    results = self._collection.query(queries=vector_query, topk=top_k)

            # Zvec 返回结果是独立副本，可在锁外遍历转换
            search_results = []
            for r in results:
                search_results.append(
                    SearchResult(
                        doc_id=r.fields.get("doc_id", r.id),
                        score=r.score,
                        content=r.fields.get("content", ""),
                        title=r.fields.get("title", ""),
                        tags=r.fields.get("tags", []),
                    )
                )
            return search_results
        except Exception as e:
            logger.error(f"向量检索失败: {e}", exc_info=True)
            raise VectorStoreError(f"Failed to search: {e}")

    def get(self, doc_id: str) -> Optional[Document]:
        """根据文档 ID 获取文档。

        线程安全：内部使用 RLock 保护 Zvec 集合操作。
        """
        try:
            with self._lock:
                self._ensure_collection()
                results = self._collection.query(
                    queries=zvec.Query(
                        field_name="doc_id",
                        term=doc_id,
                    ),
                    topk=1,
                )
                if not results:
                    return None
                r = results[0]
                return Document(
                    doc_id=r.fields.get("doc_id", r.id),
                    content=r.fields.get("content", ""),
                    title=r.fields.get("title", ""),
                    tags=r.fields.get("tags", []),
                    source=r.fields.get("source", ""),
                    created_at=r.fields.get("created_at", 0),
                    updated_at=r.fields.get("updated_at", 0),
                )
        except Exception as e:
            logger.error(f"获取文档失败: {doc_id} | {e}", exc_info=True)
            raise VectorStoreError(f"Failed to get document: {e}")

    def list(self) -> List[Document]:
        """列出所有文档。

        线程安全：内部使用 RLock 保护 Zvec 集合操作。
        """
        try:
            with self._lock:
                self._ensure_collection()
                all_docs = []
                count = self._collection.stats.doc_count
                if count == 0:
                    return []
                dummy_vector = np.zeros(self._dimension, dtype=np.float32)
                results = self._collection.query(
                    queries=zvec.Query(
                        field_name="dense_embedding",
                        vector=dummy_vector,
                    ),
                    topk=min(count, 1000),
                )
                for r in results:
                    all_docs.append(
                        Document(
                            doc_id=r.fields.get("doc_id", r.id),
                            content=r.fields.get("content", ""),
                            title=r.fields.get("title", ""),
                            tags=r.fields.get("tags", []),
                            source=r.fields.get("source", ""),
                            created_at=r.fields.get("created_at", 0),
                            updated_at=r.fields.get("updated_at", 0),
                        )
                    )
                return all_docs
        except Exception as e:
            logger.error(f"列出文档失败: {e}", exc_info=True)
            raise VectorStoreError(f"Failed to list documents: {e}")

    def count(self) -> int:
        """返回当前文档总数。

        线程安全：内部使用 RLock 保护 Zvec 集合操作。
        """
        try:
            with self._lock:
                self._ensure_collection()
                return self._collection.stats.doc_count
        except Exception as e:
            logger.error(f"获取文档计数失败: {e}", exc_info=True)
            raise VectorStoreError(f"Failed to count: {e}")

    def close(self) -> None:
        """关闭并释放 Zvec 集合资源。

        线程安全：获取锁后再 destroy，防止与查询/写入操作并发。
        """
        with self._lock:
            if self._collection:
                try:
                    self._collection.destroy()
                    logger.info("Zvec 集合已关闭")
                except Exception as e:
                    logger.error(f"关闭 Zvec 集合失败: {e}", exc_info=True)
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