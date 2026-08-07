"""Zvec 向量存储实现。"""

import asyncio
import os
import shutil
import threading
from typing import List, Optional

import numpy as np
import zvec

from src.domain.exceptions import VectorStoreError
from src.domain.models import DEFAULT_PROJECT_ID, Document, SearchResult
from src.infrastructure.interfaces.vector_store import VectorStore
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ZvecStore(VectorStore):
    """基于 Zvec 的向量存储实现（异步）。

    线程安全：内部使用 threading.RLock 保护所有 Zvec 集合操作
    （query / insert / upsert / delete / destroy），支持在 FastAPI 多线程
    请求场景下安全并发调用。

    所有公开方法提供异步版本，使用 asyncio.to_thread() 避免阻塞事件循环。
    """

    def __init__(self, data_path: str = None, dimension: int = None):
        self._data_path = data_path or settings.zvec.data_path
        self._dimension = dimension or settings.zvec.dimension
        self._lock = threading.RLock()
        self._collection: Optional[zvec.Collection] = None
        with self._lock:
            self._init_collection()

    def _init_collection(self) -> None:
        try:
            if os.path.exists(self._data_path):
                existing_files = os.listdir(self._data_path)
                if existing_files:
                    # zvec.open() 需要 LOCK 文件存在（destroy() 会删除它）
                    self._ensure_lock_file()
                    self._collection = zvec.open(path=self._data_path)
                    # 检查 schema 是否包含 project_id 字段
                    if not self._schema_has_field("project_id"):
                        logger.warning("Zvec 集合缺少 project_id 字段，正在迁移 schema...")
                        self._migrate_schema()
                        return
                    logger.info(f"Zvec 集合已打开: {self._data_path} (维度: {self._dimension})")
                    return
                os.rmdir(self._data_path)
            # 首次创建时 Zvec 会自动创建 LOCK
            self._collection = zvec.create_and_open(
                path=self._data_path,
                schema=self._build_schema(),
            )
            logger.info(f"Zvec 集合已创建: {self._data_path} (维度: {self._dimension})")
        except Exception as e:
            logger.error(f"Zvec 集合初始化失败: {e}", exc_info=True)
            raise VectorStoreError(f"Failed to initialize Zvec collection: {e}")

    def _schema_has_field(self, field_name: str) -> bool:
        """检查集合 schema 是否包含指定字段。"""
        try:
            for f in self._collection.schema.fields:
                if f.name == field_name:
                    return True
        except Exception:
            pass
        return False

    def _migrate_schema(self) -> None:
        """迁移旧 schema 集合：重建包含 project_id 字段的新集合。

        由于 zvec add_column 仅支持数值类型，STRING 类型字段
        需要重建集合。读取旧数据后以 project_id='default' 插入新集合。
        """
        old_path = self._data_path
        backup_path = f"{old_path}.migration_backup_{int(__import__('time').time())}"

        # 1. 读取旧数据
        old_docs = []
        old_vectors = []
        try:
            count = self._collection.stats.doc_count
            if count > 0:
                results = self._collection.query(topk=min(count, 10000))
                for r in results:
                    old_docs.append({
                        "id": r.id,
                        "fields": dict(r.fields),
                    })
                    vec = r.vectors.get("dense_embedding")
                    if vec is not None:
                        old_vectors.append(vec.tolist())
                    else:
                        old_vectors.append(None)
                logger.info(f"已读取 {len(old_docs)} 篇旧文档用于 schema 迁移")
        except Exception as e:
            logger.warning(f"读取旧文档失败（不影响重建）: {e}")

        # 2. 备份旧集合
        self._collection.destroy()
        self._collection = None
        if os.path.exists(backup_path):
            shutil.rmtree(backup_path)
        shutil.copytree(old_path, backup_path)
        logger.info(f"旧集合已备份到: {backup_path}")

        # 3. 删除旧集合并重建
        shutil.rmtree(old_path)
        self._collection = zvec.create_and_open(
            path=old_path,
            schema=self._build_schema(),
        )
        logger.info("Zvec 集合已重建（含 project_id 字段）")

        # 4. 重新插入旧数据（带 project_id='default'）
        reinserted = 0
        for doc, vec in zip(old_docs, old_vectors):
            try:
                fields = doc["fields"]
                from src.domain.models import Document
                document = Document(
                    doc_id=fields.get("doc_id", doc["id"]),
                    content=fields.get("content", ""),
                    title=fields.get("title", ""),
                    tags=fields.get("tags", []),
                    source=fields.get("source", ""),
                    project_id="default",
                    created_at=fields.get("created_at", 0),
                    updated_at=fields.get("updated_at", 0),
                )
                import numpy as np
                if vec is not None:
                    dense_vector = np.array(vec, dtype=np.float32)
                else:
                    # 向量缺失时生成零向量
                    dense_vector = np.zeros(self._dimension, dtype=np.float32)
                self._collection.insert(
                    zvec.Doc(
                        id=document.doc_id,
                        vectors={"dense_embedding": dense_vector},
                        fields={
                            "content": document.content,
                            "title": document.title,
                            "tags": document.tags,
                            "status": "active",
                            "doc_id": document.doc_id,
                            "created_at": document.created_at,
                            "updated_at": document.updated_at,
                            "source": document.source or "",
                            "project_id": "default",
                        },
                    )
                )
                reinserted += 1
            except Exception as e:
                logger.warning(f"重新插入文档失败（跳过）: {doc['id']} | {e}")

        logger.info(f"Schema 迁移完成: 重建集合, 重新插入 {reinserted}/{len(old_docs)} 篇文档")

    def _ensure_lock_file(self) -> None:
        """确保 zvec LOCK 文件存在（zvec.open() 的前提条件）。"""
        lock_path = os.path.join(self._data_path, "LOCK")
        if not os.path.exists(lock_path):
            parent = os.path.dirname(lock_path)
            if not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
            with open(lock_path, "w") as f:
                f.write("")
            logger.debug(f"已恢复 zvec LOCK 文件: {lock_path}")

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
                zvec.FieldSchema(
                    name="project_id",
                    data_type=zvec.DataType.STRING,
                    nullable=False,
                    index_param=zvec.InvertIndexParam(),
                ),
            ],
        )

    def _build_project_filter(
        self,
        project_id: str = DEFAULT_PROJECT_ID,
        extra_filter: Optional[str] = None,
    ) -> Optional[str]:
        """构建带项目隔离的 filter 表达式。

        Args:
            project_id: 项目 ID
            extra_filter: 已有 filter，用 AND 拼接

        Returns:
            组合后的 filter 表达式，或 None（无 filter）
        """
        # 安全检测：schema 不含 project_id 时跳过过滤（不应触发，仅防御）
        if self._collection is not None and not self._schema_has_field("project_id"):
            logger.warning("集合 schema 缺少 project_id 字段，跳过项目过滤")
            return extra_filter

        tid_filter = f"project_id = '{project_id}'"
        if extra_filter:
            return f"({tid_filter}) AND ({extra_filter})"
        return tid_filter

    def insert(self, doc: Document, dense_vector: np.ndarray, project_id: str = DEFAULT_PROJECT_ID) -> None:
        """插入文档到向量库（同步）。"""
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
                            "project_id": doc.project_id or project_id,
                        },
                    )
                )
            logger.debug(f"文档已插入: {doc.doc_id} (project={doc.project_id or project_id})")
        except Exception as e:
            logger.error(f"插入文档失败: {doc.doc_id} | {e}", exc_info=True)
            raise VectorStoreError(f"Failed to insert document: {e}")

    async def ainsert(
        self, doc: Document, dense_vector: np.ndarray, project_id: str = DEFAULT_PROJECT_ID
    ) -> None:
        """插入文档到向量库（异步）。"""
        await asyncio.to_thread(self.insert, doc, dense_vector, project_id)

    def upsert(self, doc: Document, dense_vector: np.ndarray, project_id: str = DEFAULT_PROJECT_ID) -> None:
        """更新或插入文档到向量库（同步）。"""
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
                            "project_id": doc.project_id or project_id,
                        },
                    )
                )
            logger.debug(f"文档已 upsert: {doc.doc_id} (project={doc.project_id or project_id})")
        except Exception as e:
            logger.error(f"Upsert 文档失败: {doc.doc_id} | {e}", exc_info=True)
            raise VectorStoreError(f"Failed to upsert document: {e}")

    async def aupsert(
        self, doc: Document, dense_vector: np.ndarray, project_id: str = DEFAULT_PROJECT_ID
    ) -> None:
        """更新或插入文档到向量库（异步）。"""
        await asyncio.to_thread(self.upsert, doc, dense_vector, project_id)

    def delete(self, doc_id: str, project_id: str = DEFAULT_PROJECT_ID) -> bool:
        """删除指定文档（同步）。先按 project_id 验证归属。"""
        try:
            with self._lock:
                self._ensure_collection()
                # 验证文档属于当前项目（防止跨项目删除）
                existing = self.get(doc_id, project_id)
                if not existing:
                    logger.warning(f"文档不存在或不属于当前项目: {doc_id} (project={project_id})")
                    return False
                self._collection.delete(ids=[doc_id])
            logger.debug(f"文档已删除: {doc_id} (project={project_id})")
            return True
        except Exception as e:
            logger.error(f"删除文档失败: {doc_id} | {e}", exc_info=True)
            raise VectorStoreError(f"Failed to delete document: {e}")

    async def adelete(self, doc_id: str, project_id: str = DEFAULT_PROJECT_ID) -> bool:
        """删除指定文档（异步）。"""
        return await asyncio.to_thread(self.delete, doc_id, project_id)

    def delete_by_project_id(self, project_id: str) -> int:
        """按项目 ID 批量删除所有文档。返回删除的文档数。"""
        try:
            with self._lock:
                self._ensure_collection()
                project_filter = self._build_project_filter(project_id, None)
                if project_filter:
                    count = self._collection.delete_by_filter(filter=project_filter)
                else:
                    # 默认项目：全量删除
                    count = self._collection.delete_by_filter(filter=None)
            logger.info(f"批量删除项目文档完成: project={project_id} deleted={count}")
            return count
        except Exception as e:
            logger.error(f"批量删除项目文档失败: project={project_id} | {e}", exc_info=True)
            raise VectorStoreError(f"Failed to delete documents for project {project_id}: {e}")

    async def adelete_by_project_id(self, project_id: str) -> int:
        """按项目 ID 批量删除所有文档（异步）。"""
        return await asyncio.to_thread(self.delete_by_project_id, project_id)

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 5,
        filter_expr: Optional[str] = None,
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> List[SearchResult]:
        """向量检索，返回 top-K 最相似的文档（同步）。

        自动按 project_id 过滤，仅返回当前项目的文档。
        """
        try:
            with self._lock:
                self._ensure_collection()
                vector_query = zvec.Query(
                    field_name="dense_embedding",
                    vector=query_vector,
                )
                effective_filter = self._build_project_filter(project_id, filter_expr)
                if effective_filter:
                    results = self._collection.query(
                        queries=vector_query,
                        topk=top_k,
                        filter=effective_filter,
                    )
                else:
                    results = self._collection.query(queries=vector_query, topk=top_k)

            search_results = []
            for r in results:
                search_results.append(
                    SearchResult(
                        doc_id=r.fields.get("doc_id", r.id),
                        score=r.score,
                        content=r.fields.get("content", ""),
                        title=r.fields.get("title", ""),
                        tags=r.fields.get("tags", []),
                        project_id=r.fields.get("project_id", DEFAULT_PROJECT_ID),
                    )
                )
            return search_results
        except Exception as e:
            logger.error(f"向量检索失败: {e}", exc_info=True)
            raise VectorStoreError(f"Failed to search: {e}")

    async def asearch(
        self,
        query_vector: np.ndarray,
        top_k: int = 5,
        filter_expr: Optional[str] = None,
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> List[SearchResult]:
        """向量检索，返回 top-K 最相似的文档（异步）。"""
        return await asyncio.to_thread(
            self.search, query_vector, top_k, filter_expr, project_id
        )

    def batch_search(
        self,
        query_vectors: List[np.ndarray],
        top_k: int = 5,
        filter_expr: Optional[str] = None,
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> List[List[SearchResult]]:
        """批量向量检索（同步）。

        在单次加锁内执行多次检索，减少锁竞争和 I/O 开销。
        自动按 project_id 过滤，仅返回当前项目的文档。
        """
        if not query_vectors:
            return []
        try:
            all_search_results = []
            with self._lock:
                self._ensure_collection()
                effective_filter = self._build_project_filter(project_id, filter_expr)
                for query_vector in query_vectors:
                    vector_query = zvec.Query(
                        field_name="dense_embedding",
                        vector=query_vector,
                    )
                    if effective_filter:
                        results = self._collection.query(
                            queries=vector_query,
                            topk=top_k,
                            filter=effective_filter,
                        )
                    else:
                        results = self._collection.query(
                            queries=vector_query, topk=top_k
                        )
                    search_results = []
                    for r in results:
                        search_results.append(
                            SearchResult(
                                doc_id=r.fields.get("doc_id", r.id),
                                score=r.score,
                                content=r.fields.get("content", ""),
                                title=r.fields.get("title", ""),
                                tags=r.fields.get("tags", []),
                                project_id=r.fields.get("project_id", DEFAULT_PROJECT_ID),
                            )
                        )
                    all_search_results.append(search_results)
            logger.debug(f"批量检索完成: {len(query_vectors)} 个查询")
            return all_search_results
        except Exception as e:
            logger.error(f"批量向量检索失败: {e}", exc_info=True)
            raise VectorStoreError(f"Failed to batch search: {e}")

    async def abatch_search(
        self,
        query_vectors: List[np.ndarray],
        top_k: int = 5,
        filter_expr: Optional[str] = None,
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> List[List[SearchResult]]:
        """批量向量检索（异步）。"""
        return await asyncio.to_thread(
            self.batch_search, query_vectors, top_k, filter_expr, project_id
        )

    def get(
        self, doc_id: str, project_id: str = DEFAULT_PROJECT_ID
    ) -> Optional[Document]:
        """根据文档 ID 获取文档（同步）。自动按 project_id 过滤。"""
        try:
            with self._lock:
                self._ensure_collection()
                results = self._collection.query(
                    filter=self._build_project_filter(project_id, f"doc_id = '{doc_id}'"),
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
                    project_id=r.fields.get("project_id", DEFAULT_PROJECT_ID),
                    created_at=r.fields.get("created_at", 0),
                    updated_at=r.fields.get("updated_at", 0),
                )
        except Exception as e:
            logger.error(f"获取文档失败: {doc_id} | {e}", exc_info=True)
            raise VectorStoreError(f"Failed to get document: {e}")

    async def aget(
        self, doc_id: str, project_id: str = DEFAULT_PROJECT_ID
    ) -> Optional[Document]:
        """根据文档 ID 获取文档（异步）。"""
        return await asyncio.to_thread(self.get, doc_id, project_id)

    def list(self, project_id: str = DEFAULT_PROJECT_ID) -> List[Document]:
        """列出所有文档（按创建时间降序，同步）。自动按 project_id 过滤。"""
        try:
            with self._lock:
                self._ensure_collection()
                all_docs = []
                effective_filter = self._build_project_filter(project_id, "status = 'active'")
                count = self._collection.stats.doc_count
                if count == 0:
                    return []
                results = self._collection.query(
                    filter=effective_filter,
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
                            project_id=r.fields.get("project_id", DEFAULT_PROJECT_ID),
                            created_at=r.fields.get("created_at", 0),
                            updated_at=r.fields.get("updated_at", 0),
                        )
                    )
                all_docs.sort(key=lambda x: x.created_at, reverse=True)
                return all_docs
        except Exception as e:
            logger.error(f"列出文档失败: {e}", exc_info=True)
            raise VectorStoreError(f"Failed to list documents: {e}")

    async def alist(self, project_id: str = DEFAULT_PROJECT_ID) -> List[Document]:
        """列出所有文档（按创建时间降序，异步）。"""
        return await asyncio.to_thread(self.list, project_id)

    def list_paginated(
        self, page: int = 1, page_size: int = 10, project_id: str = DEFAULT_PROJECT_ID
    ) -> List[Document]:
        """分页列出文档（同步）。自动按 project_id 过滤。

        全量加载后按创建时间降序排序并切片，避免依赖 Zvec 默认顺序导致的游标错乱。
        知识库文档总量通常不超过数千，全量加载在内存和性能上均可行。

        Args:
            page: 页码，从1开始
            page_size: 每页大小
            project_id: 项目 ID

        Returns:
            当前页的文档列表，按创建时间降序排列
        """
        try:
            with self._lock:
                self._ensure_collection()
                effective_filter = self._build_project_filter(project_id, "status = 'active'")
                count = self._collection.stats.doc_count
                if count == 0:
                    return []

                start = (page - 1) * page_size
                if start >= count:
                    return []

                # 获取全部 active 文档（含 project 过滤）
                all_results = self._collection.query(
                    filter=effective_filter,
                    topk=min(count, 1000),
                )

                docs = []
                for r in all_results:
                    docs.append(
                        Document(
                            doc_id=r.fields.get("doc_id", r.id),
                            content=r.fields.get("content", ""),
                            title=r.fields.get("title", ""),
                            tags=r.fields.get("tags", []),
                            source=r.fields.get("source", ""),
                            project_id=r.fields.get("project_id", DEFAULT_PROJECT_ID),
                            created_at=r.fields.get("created_at", 0),
                            updated_at=r.fields.get("updated_at", 0),
                        )
                    )

                # 按创建时间降序排列后切片
                docs.sort(key=lambda x: x.created_at, reverse=True)
                return docs[start:start + page_size]
        except Exception as e:
            logger.error(f"分页列出文档失败: {e}", exc_info=True)
            raise VectorStoreError(f"Failed to list documents: {e}")

    async def alist_paginated(
        self, page: int = 1, page_size: int = 10, project_id: str = DEFAULT_PROJECT_ID
    ) -> List[Document]:
        """分页列出文档（异步）。"""
        return await asyncio.to_thread(self.list_paginated, page, page_size, project_id)

    def count(self, project_id: str = DEFAULT_PROJECT_ID) -> int:
        """返回当前文档总数（同步）。自动按 project_id 过滤。"""
        try:
            with self._lock:
                self._ensure_collection()
                if project_id == DEFAULT_PROJECT_ID:
                    return self._collection.stats.doc_count
                # 精确计数：查询过滤后统计（Zvec 单次 topk 上限 100000）
                max_topk = 100000
                results = self._collection.query(
                    filter=self._build_project_filter(project_id, "status = 'active'"),
                    topk=max_topk,
                )
                return len(results)
        except Exception as e:
            logger.error(f"获取文档计数失败: {e}", exc_info=True)
            raise VectorStoreError(f"Failed to count: {e}")

    async def acount(self, project_id: str = DEFAULT_PROJECT_ID) -> int:
        """返回当前文档总数（异步）。"""
        return await asyncio.to_thread(self.count, project_id)

    def close(self) -> None:
        """关闭并释放 Zvec 集合资源（同步）。"""
        with self._lock:
            if self._collection:
                try:
                    self._collection.destroy()
                    logger.info("Zvec 集合已关闭")
                except Exception as e:
                    logger.error(f"关闭 Zvec 集合失败: {e}", exc_info=True)
                finally:
                    self._collection = None

    async def aclose(self) -> None:
        """关闭并释放 Zvec 集合资源（异步）。"""
        await asyncio.to_thread(self.close)

    def _ensure_collection(self) -> None:
        """惰性重连：集合未初始化时重新打开。"""
        if self._collection is None:
            self._init_collection()