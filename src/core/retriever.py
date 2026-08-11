"""检索引擎：编排 RAG 完整流程（嵌入→检索→重排序→生成→缓存）。

多轮对话支持：当传入 messages 时，跳过 LLM 响应缓存并透传历史到 LLM 客户端。
"""

import asyncio
from typing import Any, AsyncGenerator, List, Optional

import numpy as np

from src.domain.models import SearchResult
from src.domain.exceptions import (
    EmbeddingError,
    VectorStoreError,
    SenseNovaAPIError,
)
from src.infrastructure.interfaces.embedding_service import EmbeddingService
from src.infrastructure.interfaces.vector_store import VectorStore
from src.infrastructure.interfaces.cache_backend import CacheBackend
from src.infrastructure.interfaces.llm_client import LLMClient
from src.infrastructure.interfaces.reranker import Reranker
from src.services.handoff_judge import HandoffJudge
from src.utils.logger import get_logger

logger = get_logger(__name__)


class RetrievalResult:
    """检索结果：包含生成的回答和来源引用。"""

    def __init__(
        self,
        answer: str,
        sources: List[SearchResult],
        cache_hit: bool = False,
        llm_used: bool = True,
        reranked: bool = False,
        handoff_suggested: bool = False,
    ):
        self._answer = answer
        self._sources = sources
        self._cache_hit = cache_hit
        self._llm_used = llm_used
        self._reranked = reranked
        self._handoff_suggested = handoff_suggested

    @property
    def answer(self) -> str:
        return self._answer

    @property
    def sources(self) -> List[SearchResult]:
        return list(self._sources)

    @property
    def cache_hit(self) -> bool:
        return self._cache_hit

    @property
    def llm_used(self) -> bool:
        return self._llm_used

    @property
    def reranked(self) -> bool:
        return self._reranked

    @property
    def handoff_suggested(self) -> bool:
        return self._handoff_suggested

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RetrievalResult):
            return False
        return (
            self._answer == other._answer
            and self._sources == other._sources
            and self._cache_hit == other._cache_hit
        )

    def __repr__(self) -> str:
        return (
            f"RetrievalResult(answer='{self._answer[:30]}...', "
            f"sources={len(self._sources)}, cache_hit={self._cache_hit})"
        )


class Retriever:
    """检索引擎：编排完整的 RAG 流程（异步）。

    核心流程（生产级）：
    1. 将用户查询转为向量（EmbeddingService，异步）
    2. 查询 LLM 响应缓存（CacheBackend，异步）
    3. 缓存命中 → 直接返回
    4. 缓存未命中或有对话历史 → 向量检索召回（VectorStore，异步）→ 重排序精排（Reranker）
       → 构建上下文 → 调用 LLM（携带历史消息）→ 返回结果 → 写入缓存（无历史时）

    多轮对话：当传入 messages 时，跳过缓存，并将历史消息透传给 LLM 客户端。
    """

    def __init__(
        self,
        embedding_service: EmbeddingService,
        vector_store: VectorStore,
        cache_backend: CacheBackend,
        llm_client: LLMClient,
        reranker: Optional[Reranker] = None,
        embedding_cache=None,
        stats_registry=None,
    ):
        self._embedding_service = embedding_service
        self._vector_store = vector_store
        self._cache_backend = cache_backend
        self._llm_client = llm_client
        self._reranker = reranker
        self._embedding_cache = embedding_cache
        self._stats_registry = stats_registry
        self._system_prompt: Optional[str] = None

    def _record_stats(
        self,
        project_id: str,
        *,
        cache_hit: bool,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """记录一次检索/生成请求到统计注册表。"""
        if self._stats_registry is not None:
            try:
                self._stats_registry.record(
                    project_id=project_id,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cache_hit=cache_hit,
                )
            except Exception as e:
                logger.warning(f"统计记录失败: {e}")

    def _check_handoff_needed(
        self,
        sources: List[SearchResult],
        query: str,
        messages: Optional[List[dict]] = None,
    ) -> bool:
        """多维判定是否需要建议转人工客服。

        使用 HandoffJudge 引擎评估检索质量、重复提问、情感等多个维度。
        替代原有的单一阈值逻辑。
        """
        if not hasattr(self, "_handoff_judge"):
            self._handoff_judge = HandoffJudge()
        decision = self._handoff_judge.evaluate(
            query=query,
            sources=sources,
            history=messages or [],
        )
        return decision.suggested

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        system_prompt: Optional[str] = None,
        project_id: str = "default",
        cache_backend: Optional[CacheBackend] = None,
        messages: Optional[List[dict]] = None,
        language: str = "zh",
    ) -> RetrievalResult:
        """执行检索，返回回答和来源（异步）。

        Args:
            query: 用户查询文本
            top_k: 返回的最相关文档数量
            system_prompt: 自定义系统提示词
            project_id: 项目 ID
            cache_backend: 缓存后端（默认使用实例的）
            messages: 多轮对话历史（[{role, content}]）
            language: 回答语言（zh/en）

        Returns:
            RetrievalResult：包含回答、来源、缓存命中状态、handoff 建议
        """
        effective_prompt = system_prompt or self._system_prompt

        if not query.strip():
            logger.warning("空查询，直接返回")
            return RetrievalResult(
                answer="请提供有效的查询内容", sources=[], llm_used=False, handoff_suggested=True
            )

        try:
            query_vector = await self._encode_query(query)
        except EmbeddingError as e:
            logger.error(f"查询编码失败: {e}")
            return RetrievalResult(
                answer="系统暂无法处理该查询，请稍后重试",
                sources=[],
                llm_used=False,
                handoff_suggested=True,
            )

        # 有历史消息时跳过缓存（多轮上下文不同，缓存会错误命中）
        if not messages:
            cached_answer = await self._check_cache(query_vector, cache_backend=cache_backend)
            if cached_answer:
                logger.debug("缓存命中，直接返回")
                sources = await self._get_sources_for_cache(query_vector, top_k, project_id=project_id)
                self._record_stats(project_id, cache_hit=True)
                return RetrievalResult(
                    answer=cached_answer,
                    sources=sources,
                    cache_hit=True,
                    llm_used=False,
                    handoff_suggested=False,
                )
        else:
            logger.debug("有对话历史，跳过缓存")

        try:
            recall_top_k = (
                self._reranker.recall_top_k
                if self._reranker and self._reranker.is_enabled
                else top_k
            )
            search_results = await self._vector_search(query_vector, recall_top_k, project_id=project_id)
        except VectorStoreError as e:
            logger.error(f"向量检索失败: {e}")
            return RetrievalResult(
                answer="无法检索到相关信息，请稍后重试",
                sources=[],
                llm_used=False,
                handoff_suggested=True,
            )

        if not search_results:
            logger.debug("未检索到相关文档")
            return RetrievalResult(
                answer="未找到相关信息",
                sources=[],
                llm_used=False,
                handoff_suggested=True,
            )

        reranked = False
        if self._reranker and self._reranker.is_enabled:
            try:
                search_results = await self._reranker.rerank(
                    query, search_results, top_k=top_k
                )
                reranked = True
                logger.debug(f"重排序完成，结果数量: {len(search_results)}")
            except Exception as e:
                logger.warning(f"重排序失败，降级为不重排序: {e}")

        handoff = self._check_handoff_needed(search_results, query, messages=messages)
        answer = await self._generate_answer(
            query, search_results, system_prompt=effective_prompt,
            messages=messages, language=language,
        )

        # 无历史时写入缓存
        if not messages:
            await self._cache_result(query_vector, answer, cache_backend=cache_backend)

        self._record_stats(project_id, cache_hit=False)

        logger.info(f"查询: '{query[:100]}' | 回答: {len(answer)} 字符 | 来源: {len(search_results)} 篇")
        return RetrievalResult(
            answer=answer,
            sources=search_results,
            cache_hit=False,
            llm_used=True,
            reranked=reranked,
            handoff_suggested=handoff,
        )

    async def retrieve_stream(
        self,
        query: str,
        top_k: int = 5,
        system_prompt: Optional[str] = None,
        project_id: str = "default",
        cache_backend: Optional[CacheBackend] = None,
        messages: Optional[List[dict]] = None,
        language: str = "zh",
    ) -> AsyncGenerator[dict, None]:
        """流式检索，逐事件返回结果（异步生成器）。

        事件类型:
          - sources: 检索到的来源文档
          - reasoning_delta: 推理链文本增量
          - answer_delta: 回答文本增量
          - cache_hit: 是否缓存命中
          - handoff_suggested: 是否建议转人工
          - done: 流式结束
          - error: 错误信息
        """
        if not query.strip():
            yield {"event": "error", "data": "请提供有效的查询内容"}
            yield {"event": "done", "data": None}
            return

        try:
            query_vector = await self._encode_query(query)
        except EmbeddingError as e:
            logger.error(f"查询编码失败: {e}")
            yield {"event": "error", "data": "系统暂无法处理该查询，请稍后重试"}
            yield {"event": "done", "data": None}
            return

        if not messages:
            cached_answer = await self._check_cache(query_vector, cache_backend=cache_backend)
            if cached_answer:
                logger.debug("缓存命中，直接返回")
                sources = await self._get_sources_for_cache(query_vector, top_k, project_id=project_id)
                sources_data = [
                    {
                        "doc_id": s.doc_id,
                        "title": s.title,
                        "content": s.content,
                        "score": round(s.score, 4),
                    }
                    for s in sources
                ]
                yield {"event": "sources", "data": sources_data}
                self._record_stats(project_id, cache_hit=True)
                yield {"event": "cache_hit", "data": True}
                yield {"event": "handoff_suggested", "data": False}
                yield {"event": "answer_delta", "data": cached_answer}
                yield {"event": "done", "data": None}
                return
        else:
            logger.debug("有对话历史，跳过缓存")

        try:
            recall_top_k = (
                self._reranker.recall_top_k
                if self._reranker and self._reranker.is_enabled
                else top_k
            )
            search_results = await self._vector_search(query_vector, recall_top_k, project_id=project_id)
        except VectorStoreError as e:
            logger.error(f"向量检索失败: {e}")
            yield {"event": "error", "data": "无法检索到相关信息，请稍后重试"}
            yield {"event": "done", "data": None}
            return

        if not search_results:
            logger.debug("未检索到相关文档")
            yield {"event": "sources", "data": []}
            yield {"event": "cache_hit", "data": False}
            yield {"event": "handoff_suggested", "data": True}
            yield {"event": "answer_delta", "data": "未找到相关信息"}
            yield {"event": "done", "data": None}
            return

        reranked = False
        if self._reranker and self._reranker.is_enabled:
            try:
                search_results = await self._reranker.rerank(
                    query, search_results, top_k=top_k
                )
                reranked = True
                logger.debug(f"重排序完成，结果数量: {len(search_results)}")
            except Exception as e:
                logger.warning(f"重排序失败，降级为不重排序: {e}")

        sources_data = [
            {
                "doc_id": s.doc_id,
                "title": s.title,
                "content": s.content,
                "score": round(s.score, 4),
            }
            for s in search_results
        ]
        yield {"event": "sources", "data": sources_data}
        yield {"event": "cache_hit", "data": False}

        handoff = self._check_handoff_needed(search_results, query, messages=messages)
        yield {"event": "handoff_suggested", "data": handoff}

        effective_prompt = system_prompt or self._system_prompt
        answer_chunks: list[str] = []
        try:
            context = self._build_context(search_results)
            if not context:
                fallback = "\n\n".join([r.title for r in search_results if r.title]) or "未找到相关信息"
                yield {"event": "answer_delta", "data": fallback}
                answer_chunks.append(fallback)
            else:
                if hasattr(self._llm_client, "stream_answer"):
                    async for chunk in self._llm_client.stream_answer(
                        query, context, system_prompt=effective_prompt,
                        messages=messages, language=language,
                    ):
                        if chunk["type"] == "reasoning":
                            yield {"event": "reasoning_delta", "data": chunk["content"]}
                        elif chunk["type"] == "content":
                            answer_chunks.append(chunk["content"])
                            yield {"event": "answer_delta", "data": chunk["content"]}
                else:
                    answer = await self._llm_client.generate_answer(
                        query, context, system_prompt=effective_prompt,
                        messages=messages, language=language,
                    )
                    answer_chunks.append(answer)
                    yield {"event": "answer_delta", "data": answer}

            full_answer = "".join(answer_chunks)
            logger.info(f"[流式] 查询: '{query[:100]}' | 回答: {len(full_answer)} 字符 | 来源: {len(search_results)} 篇")
            logger.debug(f"LLM 流式生成完成，长度: {len(full_answer)}")

            if not messages:
                await self._cache_result(query_vector, full_answer, cache_backend=cache_backend)

            self._record_stats(project_id, cache_hit=False)

        except SenseNovaAPIError as e:
            logger.error(f"LLM 调用失败，降级为返回原始文档: {e}")
            fallback = self._fallback_answer(search_results)
            yield {"event": "answer_delta", "data": fallback}
        except Exception as e:
            logger.error(f"LLM 生成回答异常，降级为返回原始文档: {e}")
            fallback = self._fallback_answer(search_results)
            yield {"event": "answer_delta", "data": fallback}

        yield {"event": "done", "data": {"reranked": reranked}}

    async def _encode_query(self, query: str) -> np.ndarray:
        """将查询文本编码为向量（异步）。"""
        if self._embedding_cache is not None:
            cached = await self._embedding_cache.aget(query)
            if cached is not None:
                return cached

        vec = await self._embedding_service.encode(query)

        if self._embedding_cache is not None:
            await self._embedding_cache.aset(query, vec)

        return vec

    async def _check_cache(self, query_vector: np.ndarray, cache_backend: Optional[CacheBackend] = None) -> Optional[str]:
        """检查缓存是否命中（异步）。"""
        try:
            backend = cache_backend or self._cache_backend
            if hasattr(backend, "aget"):
                return await backend.aget(query_vector)
            return backend.get(query_vector)
        except Exception as e:
            logger.warning(f"缓存查询失败，降级为直接检索: {e}")
            return None

    async def _get_sources_for_cache(self, query_vector: np.ndarray, top_k: int, project_id: str = "default") -> List[SearchResult]:
        """缓存命中时，获取来源文档（用于引用展示，异步）。"""
        try:
            if hasattr(self._vector_store, "asearch"):
                return await self._vector_store.asearch(query_vector, top_k=top_k, project_id=project_id)
            return self._vector_store.search(query_vector, top_k=top_k, project_id=project_id)
        except Exception as e:
            logger.warning(f"缓存命中但获取来源失败: {e}")
            return []

    async def _vector_search(
        self,
        query_vector: np.ndarray,
        top_k: int,
        project_id: str = "default",
    ) -> List[SearchResult]:
        """执行向量检索，返回最相关的文档（异步）。"""
        if hasattr(self._vector_store, "asearch"):
            return await self._vector_store.asearch(query_vector, top_k=top_k, project_id=project_id)
        return self._vector_store.search(query_vector, top_k=top_k, project_id=project_id)

    def _build_context(self, search_results: List[SearchResult]) -> List[str]:
        """从检索结果构建上下文列表。"""
        context = []
        for result in search_results:
            if result.content:
                context.append(result.content)
            elif result.title:
                context.append(result.title)
        return context

    async def _generate_answer(
        self,
        query: str,
        search_results: List[SearchResult],
        system_prompt: Optional[str] = None,
        messages: Optional[List[dict]] = None,
        language: str = "zh",
    ) -> str:
        """基于检索结果生成回答（异步）。"""
        context = self._build_context(search_results)
        if not context:
            return "\n\n".join([r.title for r in search_results if r.title]) or "未找到相关信息"

        try:
            answer = await self._llm_client.generate_answer(
                query, context, system_prompt=system_prompt,
                messages=messages, language=language,
            )
            logger.debug(f"LLM 生成回答成功，长度: {len(answer)}")
            return answer
        except SenseNovaAPIError as e:
            logger.error(f"LLM 调用失败，降级为返回原始文档: {e}")
            return self._fallback_answer(search_results)
        except Exception as e:
            logger.error(f"LLM 生成回答异常，降级为返回原始文档: {e}")
            return self._fallback_answer(search_results)

    def _fallback_answer(self, search_results: List[SearchResult]) -> str:
        """构建降级答案：拼接检索到的文档内容。"""
        pieces = []
        for i, result in enumerate(search_results[:3], 1):
            if result.title:
                pieces.append(f"【{i}】{result.title}")
            if result.content:
                content_preview = result.content[:500]
                if len(result.content) > 500:
                    content_preview += "..."
                pieces.append(content_preview)
        if pieces:
            return "\n\n".join(pieces)
        return "未找到相关信息"

    async def _cache_result(self, query_vector: np.ndarray, answer: str, cache_backend: Optional[CacheBackend] = None) -> None:
        """将结果写入缓存（异步）。"""
        try:
            backend = cache_backend or self._cache_backend
            if hasattr(backend, "aset"):
                await backend.aset(query_vector, answer)
            else:
                backend.set(query_vector, answer)
            logger.debug("结果已写入缓存")
        except Exception as e:
            logger.warning(f"缓存写入失败: {e}")

    async def close(self) -> None:
        """异步关闭所有资源。"""
        if hasattr(self._llm_client, "close"):
            try:
                await self._llm_client.close()
            except Exception as e:
                logger.warning(f"关闭 LLM 客户端失败: {e}")
        if hasattr(self._vector_store, "aclose"):
            await self._vector_store.aclose()
        else:
            self._vector_store.close()

    def __enter__(self) -> "Retriever":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(self.close())
        except RuntimeError:
            asyncio.run(self.close())

    async def __aenter__(self) -> "Retriever":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()