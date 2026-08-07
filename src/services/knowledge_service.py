"""知识库业务服务：编排文档加载→切分→嵌入→存储的完整流程（异步）。"""

import asyncio
import hashlib
import os
import uuid
from typing import List, Optional

from langchain_core.documents import Document

from src.domain.models import DEFAULT_PROJECT_ID, Document as DomainDocument, SearchResult
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
    4. 向量存储（VectorStore，异步）

    Examples:
        >>> svc = KnowledgeService()
        >>> await svc.load_faq_documents()
        >>> count = await svc.count_documents()
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

    async def load_document(self, file_path: str) -> DomainDocument:
        """加载单个文档文件，转换为领域模型（异步）。"""
        return await asyncio.to_thread(self._load_document_sync, file_path)

    def _load_document_sync(self, file_path: str) -> DomainDocument:
        """加载单个文档文件（同步）。"""
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
        project_id: str = DEFAULT_PROJECT_ID,
        skip_duplicate_check: bool = False,
    ) -> DomainDocument:
        """从文本创建文档并存储到向量库（异步）。

        Args:
            skip_duplicate_check: 是否跳过内容去重检查
        """
        # 内容去重
        if not skip_duplicate_check:
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            existing = await self._vector_store.asearch_by_hash(content_hash, project_id=project_id)
            if existing:
                from src.domain.exceptions import KnowledgeBaseError
                raise KnowledgeBaseError(f"内容已存在: {existing.title}")

        doc = DomainDocument(
            doc_id=str(uuid.uuid4()),
            content=content,
            title=title,
            tags=tags or [],
            source=source,
            project_id=project_id,
        )

        # 存储 content_hash 到文档元数据
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        embedding = await self._embedding_service.encode(doc.content)
        await self._vector_store.ainsert(doc, embedding, project_id=project_id, content_hash=content_hash)

        logger.info(f"文档已创建并存储: {doc.title} ({doc.doc_id}) [project={project_id}]")
        return doc

    async def update_document(
        self,
        doc_id: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
        tags: Optional[List[str]] = None,
        source: Optional[str] = None,
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> DomainDocument:
        """更新文档（异步）。"""
        existing_doc = await self._vector_store.aget(doc_id, project_id=project_id)
        if not existing_doc:
            raise KnowledgeBaseError(f"文档不存在: {doc_id}")

        if content is None:
            content = existing_doc.content
        if title is None:
            title = existing_doc.title
        if tags is None:
            tags = existing_doc.tags
        if source is None:
            source = existing_doc.source

        updated_doc = DomainDocument(
            doc_id=doc_id,
            content=content,
            title=title,
            tags=tags,
            source=source,
            project_id=existing_doc.project_id,
            created_at=existing_doc.created_at,
        )

        embedding = await self._embedding_service.encode(content)
        await self._vector_store.aupsert(updated_doc, embedding, project_id=project_id)

        logger.info(f"文档已更新: {doc_id} [project={project_id}]")
        return updated_doc

    async def load_and_store_document(
        self, file_path: str, project_id: str = DEFAULT_PROJECT_ID
    ) -> DomainDocument:
        """加载文档并存储到向量库（异步）。"""
        doc = await self.load_document(file_path)
        # 写入 project_id
        doc._project_id = project_id

        embedding = await self._embedding_service.encode(doc.content)
        await self._vector_store.ainsert(doc, embedding, project_id=project_id)

        logger.info(f"文档已加载并存储: {doc.title} ({doc.doc_id}) [project={project_id}]")
        return doc

    async def load_faq_documents(self, project_id: str = DEFAULT_PROJECT_ID) -> List[DomainDocument]:
        """加载所有 FAQ 文档并存储到向量库（异步批量）。"""
        if not os.path.exists(FAQ_DIR):
            raise KnowledgeBaseError(f"FAQ 目录不存在: {FAQ_DIR}")

        docs_to_store = []
        for filename in sorted(os.listdir(FAQ_DIR)):
            if not filename.endswith(".md"):
                continue
            file_path = os.path.join(FAQ_DIR, filename)
            try:
                doc = await self.load_document(file_path)
                docs_to_store.append(doc)
                logger.debug(f"FAQ 文档已加载: {filename}")
            except Exception as e:
                logger.error(f"加载 FAQ 文档失败: {filename} | {e}")

        if not docs_to_store:
            return []

        # 写入 project_id
        for doc in docs_to_store:
            doc._project_id = project_id

        contents = [doc.content for doc in docs_to_store]
        embeddings = await self._embedding_service.encode_batch(contents)
        for i, doc in enumerate(docs_to_store):
            await self._vector_store.ainsert(doc, embeddings[i], project_id=project_id)

        logger.info(f"FAQ 文档加载完成，共 {len(docs_to_store)} 条 [project={project_id}]")
        return docs_to_store

    async def load_directory(
        self, dir_path: str, split_long_docs: bool = True
    ) -> List[DomainDocument]:
        """加载目录下所有支持的文档并存储到向量库（异步批量）。"""
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
                langchain_docs = await asyncio.to_thread(loader.load)

                if split_long_docs:
                    langchain_docs = await asyncio.to_thread(self._splitter_long.split, langchain_docs)

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
        embeddings = await self._embedding_service.encode_batch(contents)
        for i, doc in enumerate(docs_to_store):
            await self._vector_store.ainsert(doc, embeddings[i])

        logger.info(f"目录文档加载完成，共 {len(docs_to_store)} 条")
        return docs_to_store

    async def delete_document(
        self, doc_id: str, project_id: str = DEFAULT_PROJECT_ID
    ) -> bool:
        """删除指定文档（异步）。"""
        return await self._vector_store.adelete(doc_id, project_id=project_id)

    async def batch_delete_documents(
        self, doc_ids: List[str], project_id: str = DEFAULT_PROJECT_ID
    ) -> int:
        """批量删除文档（异步）。返回成功删除的数量。"""
        count = 0
        for doc_id in doc_ids:
            try:
                if await self._vector_store.adelete(doc_id, project_id=project_id):
                    count += 1
            except Exception as e:
                logger.warning(f"批量删除失败: {doc_id} | {e}")
        logger.info(f"批量删除完成: {count}/{len(doc_ids)} [project={project_id}]")
        return count

    async def count_documents(self, project_id: str = DEFAULT_PROJECT_ID) -> int:
        """返回当前知识库文档总数（异步）。"""
        return await self._vector_store.acount(project_id=project_id)

    async def get_by_id(
        self, doc_id: str, project_id: str = DEFAULT_PROJECT_ID
    ) -> Optional[DomainDocument]:
        """根据文档 ID 获取文档（异步）。"""
        return await self._vector_store.aget(doc_id, project_id=project_id)

    async def list_documents(
        self, page: int = 1, page_size: int = 10, project_id: str = DEFAULT_PROJECT_ID
    ) -> List[DomainDocument]:
        """分页列出所有文档（异步）。

        使用 ZvecStore 的分页查询方法，避免加载全部文档到内存。
        """
        return await self._vector_store.alist_paginated(
            page=page, page_size=page_size, project_id=project_id
        )

    async def search(
        self, query: str, top_k: int = 5, project_id: str = DEFAULT_PROJECT_ID
    ) -> List[SearchResult]:
        """检索知识库，返回最相似的文档及分数（异步）。"""
        embedding = await self._embedding_service.encode(query)
        results = await self._vector_store.asearch(
            embedding, top_k=top_k, project_id=project_id
        )
        return list(results)

    async def batch_search(
        self,
        queries: List[str],
        top_k: int = 5,
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> List[List[SearchResult]]:
        """批量检索知识库（异步）。

        先批量编码所有查询，再一次性批量检索，减少锁竞争。
        """
        if not queries:
            return []

        embeddings = await self._embedding_service.encode_batch(queries)

        if hasattr(self._vector_store, "abatch_search"):
            batch_results = await self._vector_store.abatch_search(
                [embeddings[i] for i in range(len(queries))],
                top_k=top_k,
                project_id=project_id,
            )
        else:
            batch_results = []
            for i in range(len(queries)):
                results = await self._vector_store.asearch(
                    embeddings[i], top_k=top_k, project_id=project_id
                )
                batch_results.append(results)

        return [list(results) for results in batch_results]

    async def close(self) -> None:
        """关闭资源（异步）。"""
        await self._vector_store.aclose()

    def __enter__(self) -> "KnowledgeService":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(self.close())
        except RuntimeError:
            asyncio.run(self.close())

    async def __aenter__(self) -> "KnowledgeService":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()