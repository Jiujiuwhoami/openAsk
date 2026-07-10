"""知识库业务服务：编排文档加载→切分→嵌入→存储的完整流程（异步）。"""

import os
import threading
import uuid
from typing import List, Optional

from langchain_core.documents import Document

from src.domain.models import Document as DomainDocument
from src.domain.exceptions import KnowledgeBaseError
from src.infrastructure.embedding_service import SentenceBertEmbeddingService
from src.infrastructure.zvec_store import ZvecStore
from src.infrastructure.interfaces.embedding_service import EmbeddingService
from src.infrastructure.interfaces.vector_store import VectorStore
from src.services.document_loader import DocumentLoaderFactory
from src.services.document_splitter import (
    DocumentSplitter,
    NoSplitStrategy,
    RecursiveSplitStrategy,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

FAQ_DIR = "data/documents/faq"


class KnowledgeService:
    """知识库业务服务（异步）。

    负责整个知识库管理流程：
    1. 文档加载（DocumentLoaderFactory）
    2. 文档切分（DocumentSplitter）
    3. 向量嵌入（EmbeddingService，异步批量）
    4. 向量存储（VectorStore）

    Examples:
        >>> svc = KnowledgeService()
        >>> await svc.load_faq_documents()
        >>> count = svc.count_documents()
    """

    def __init__(
        self,
        vector_store: Optional[VectorStore] = None,
        embedding_service: Optional[EmbeddingService] = None,
    ):
        self._vector_store = vector_store or ZvecStore()
        self._embedding_service = embedding_service or SentenceBertEmbeddingService()
        self._loader_factory = DocumentLoaderFactory()
        self._splitter_faq = DocumentSplitter(NoSplitStrategy())
        self._splitter_long = DocumentSplitter(RecursiveSplitStrategy())
        self._lock = threading.RLock()

    def load_document(self, file_path: str) -> DomainDocument:
        """加载单个文档文件，转换为领域模型（同步，文件 IO 很快）。

        Args:
            file_path: 文档文件路径

        Returns:
            DomainDocument 实例
        """
        loader = self._loader_factory.get_loader(file_path)
        langchain_docs = loader.load()

        if not langchain_docs:
            raise KnowledgeBaseError(f"文档加载后为空: {file_path}")

        langchain_doc = langchain_docs[0]
        content = langchain_doc.page_content
        title = langchain_doc.metadata.get("title", "") or os.path.splitext(
            os.path.basename(file_path)
        )[0]
        source = langchain_doc.metadata.get("source", file_path)

        return DomainDocument(
            doc_id=str(uuid.uuid4()),
            content=content,
            title=title,
            source=source,
        )

    async def create_document_from_text(
        self,
        title: str,
        content: str,
        tags: Optional[List[str]] = None,
        source: Optional[str] = None,
    ) -> DomainDocument:
        """从文本创建文档并存储到向量库（异步）。

        Args:
            title: 文档标题
            content: 文档内容
            tags: 标签列表
            source: 来源

        Returns:
            存储后的 DomainDocument 实例
        """
        doc = DomainDocument(
            doc_id=str(uuid.uuid4()),
            content=content,
            title=title,
            tags=tags or [],
            source=source,
        )

        with self._lock:
            embedding = await self._embedding_service.encode(doc.content)
            self._vector_store.insert(doc, embedding)

        logger.info(f"文档已创建并存储: {doc.title} ({doc.doc_id})")
        return doc

    async def load_and_store_document(self, file_path: str) -> DomainDocument:
        """加载文档并存储到向量库（异步）。

        Args:
            file_path: 文档文件路径

        Returns:
            存储后的 DomainDocument 实例
        """
        doc = self.load_document(file_path)

        with self._lock:
            embedding = await self._embedding_service.encode(doc.content)
            self._vector_store.insert(doc, embedding)

        logger.info(f"文档已加载并存储: {doc.title} ({doc.doc_id})")
        return doc

    async def load_faq_documents(self) -> List[DomainDocument]:
        """加载所有 FAQ 文档并存储到向量库（异步批量）。

        FAQ 文档使用 NoSplitStrategy（不切分），保持文档完整性。
        使用批量嵌入提升性能。

        Returns:
            已存储的文档列表
        """
        if not os.path.exists(FAQ_DIR):
            raise KnowledgeBaseError(f"FAQ 目录不存在: {FAQ_DIR}")

        docs_to_store = []
        for filename in sorted(os.listdir(FAQ_DIR)):
            if not filename.endswith(".md"):
                continue
            file_path = os.path.join(FAQ_DIR, filename)
            try:
                doc = self.load_document(file_path)
                docs_to_store.append(doc)
                logger.debug(f"FAQ 文档已加载: {filename}")
            except Exception as e:
                logger.error(f"加载 FAQ 文档失败: {filename} | {e}")

        if not docs_to_store:
            return []

        contents = [doc.content for doc in docs_to_store]
        with self._lock:
            embeddings = await self._embedding_service.encode_batch(contents)
            for i, doc in enumerate(docs_to_store):
                self._vector_store.insert(doc, embeddings[i])

        logger.info(f"FAQ 文档加载完成，共 {len(docs_to_store)} 条")
        return docs_to_store

    async def load_directory(
        self, dir_path: str, split_long_docs: bool = True
    ) -> List[DomainDocument]:
        """加载目录下所有支持的文档并存储到向量库（异步批量）。

        使用批量嵌入提升性能。

        Args:
            dir_path: 目录路径
            split_long_docs: 是否切分长文档

        Returns:
            已存储的文档列表
        """
        if not os.path.exists(dir_path):
            raise KnowledgeBaseError(f"目录不存在: {dir_path}")

        docs_to_store = []
        for filename in sorted(os.listdir(dir_path)):
            file_path = os.path.join(dir_path, filename)

            if not self._loader_factory.supports(file_path):
                logger.debug(f"跳过不支持的文件: {filename}")
                continue

            try:
                loader = self._loader_factory.get_loader(file_path)
                langchain_docs = loader.load()

                if split_long_docs:
                    langchain_docs = self._splitter_long.split(langchain_docs)

                for langchain_doc in langchain_docs:
                    content = langchain_doc.page_content
                    if not content.strip():
                        continue

                    title = langchain_doc.metadata.get("title", "") or os.path.splitext(
                        filename
                    )[0]
                    source = langchain_doc.metadata.get("source", file_path)

                    domain_doc = DomainDocument(
                        doc_id=str(uuid.uuid4()),
                        content=content,
                        title=title,
                        source=source,
                    )
                    docs_to_store.append(domain_doc)

                logger.debug(f"目录文档已加载: {filename}")
            except Exception as e:
                logger.error(f"加载目录文档失败: {filename} | {e}")

        if not docs_to_store:
            return []

        contents = [doc.content for doc in docs_to_store]
        with self._lock:
            embeddings = await self._embedding_service.encode_batch(contents)
            for i, doc in enumerate(docs_to_store):
                self._vector_store.insert(doc, embeddings[i])

        logger.info(f"目录文档加载完成，共 {len(docs_to_store)} 条")
        return docs_to_store

    def delete_document(self, doc_id: str) -> bool:
        """删除指定文档（同步，向量操作很快）。

        Args:
            doc_id: 文档 ID

        Returns:
            删除是否成功
        """
        return self._vector_store.delete(doc_id)

    def count_documents(self) -> int:
        """返回当前知识库文档总数（同步）。"""
        with self._lock:
            return self._vector_store.count()

    def get_by_id(self, doc_id: str) -> Optional[DomainDocument]:
        """根据文档 ID 获取文档（同步，向量操作很快）。

        Args:
            doc_id: 文档 ID

        Returns:
            DomainDocument 实例或 None
        """
        with self._lock:
            doc = self._vector_store.get(doc_id)
        return doc

    def list_documents(self, page: int = 1, page_size: int = 10) -> List[DomainDocument]:
        """分页列出所有文档（同步，向量操作很快）。

        Args:
            page: 页码（从1开始）
            page_size: 每页数量

        Returns:
            文档列表
        """
        with self._lock:
            all_docs = self._vector_store.list()
            start = (page - 1) * page_size
            end = start + page_size
            return all_docs[start:end]

    async def search(self, query: str, top_k: int = 5) -> List[DomainDocument]:
        """检索知识库，返回最相似的文档（异步）。

        Args:
            query: 查询文本
            top_k: 返回数量

        Returns:
            匹配的文档列表（按相似度排序）
        """
        with self._lock:
            embedding = await self._embedding_service.encode(query)
            results = self._vector_store.search(embedding, top_k=top_k)

        docs = []
        for result in results:
            docs.append(
                DomainDocument(
                    doc_id=result.doc_id,
                    content=result.content,
                    title=result.title,
                    tags=result.tags,
                )
            )
        return docs

    def close(self) -> None:
        """关闭资源。"""
        self._vector_store.close()

    def __enter__(self) -> "KnowledgeService":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
