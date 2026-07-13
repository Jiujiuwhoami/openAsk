"""API 请求/响应模型定义。"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """健康检查响应模型。"""

    status: str = Field(..., description="服务状态")
    version: str = Field(..., description="版本号")
    timestamp: datetime = Field(..., description="时间戳")
    zvec_status: str = Field(..., description="Zvec 向量数据库状态")
    embedding_status: str = Field(..., description="嵌入服务状态")
    llm_status: str = Field(..., description="LLM 服务状态")
    cache_status: str = Field(..., description="缓存服务状态")
    document_count: int = Field(..., description="知识库文档数量")


class ChatRequest(BaseModel):
    """聊天请求模型。"""

    query: str = Field(..., description="用户查询", min_length=1, max_length=2000)
    top_k: int = Field(10, description="返回文档数量", ge=1, le=20)


class Source(BaseModel):
    """来源文档信息。"""

    doc_id: str = Field(..., description="文档 ID")
    title: str = Field(..., description="文档标题")
    content: str = Field(..., description="文档内容预览")
    score: float = Field(..., description="相似度分数")


class ChatResponse(BaseModel):
    """聊天响应模型。"""

    answer: str = Field(..., description="生成的回答")
    sources: List[Source] = Field(..., description="引用的来源文档")
    cache_hit: bool = Field(..., description="是否命中缓存")
    llm_used: bool = Field(..., description="是否使用了 LLM")


class ChatStreamEvent(BaseModel):
    """流式聊天事件模型。"""

    event: str = Field(..., description="事件类型: sources / answer_delta / cache_hit / done / error")
    data: object = Field(None, description="事件数据")


class DocumentRequest(BaseModel):
    """创建文档请求模型。"""

    title: str = Field(..., description="文档标题", min_length=1, max_length=200)
    content: str = Field(..., description="文档内容", min_length=1)
    tags: Optional[List[str]] = Field(None, description="标签列表")
    source: Optional[str] = Field(None, description="来源")


class UpdateDocumentRequest(BaseModel):
    """更新文档请求模型。"""

    title: Optional[str] = Field(None, description="文档标题", min_length=1, max_length=200)
    content: Optional[str] = Field(None, description="文档内容")
    tags: Optional[List[str]] = Field(None, description="标签列表")
    source: Optional[str] = Field(None, description="来源")


class DocumentResponse(BaseModel):
    """文档响应模型。"""

    doc_id: str = Field(..., description="文档 ID")
    title: str = Field(..., description="文档标题")
    content: str = Field(..., description="文档内容")
    tags: List[str] = Field(..., description="标签列表")
    source: Optional[str] = Field(..., description="来源")
    created_at: int = Field(..., description="创建时间戳")
    updated_at: int = Field(..., description="更新时间戳")


class SearchRequest(BaseModel):
    """搜索请求模型。"""

    query: str = Field(..., description="搜索关键词", min_length=1, max_length=2000)
    top_k: int = Field(10, description="返回数量", ge=1, le=50)


class BatchSearchRequest(BaseModel):
    """批量搜索请求模型。"""

    queries: List[str] = Field(..., description="搜索关键词列表", min_length=1, max_length=50)
    top_k: int = Field(10, description="每个查询返回数量", ge=1, le=50)


class BatchSearchResultItem(BaseModel):
    """单条批量搜索结果。"""

    query_index: int = Field(..., description="查询索引")
    query: str = Field(..., description="查询内容")
    results: List["SearchResultResponse"] = Field(..., description="搜索结果列表")


class SearchResultResponse(BaseModel):
    """搜索结果响应模型。"""

    doc_id: str = Field(..., description="文档 ID")
    title: str = Field(..., description="文档标题")
    content: str = Field(..., description="文档内容预览")
    score: float = Field(..., description="相似度分数")


class PaginatedResponse(BaseModel):
    """分页响应模型。"""

    items: List[DocumentResponse] = Field(..., description="数据列表")
    total: int = Field(..., description="总数量")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")


class ErrorResponse(BaseModel):
    """错误响应模型。"""

    error: str = Field(..., description="错误类型")
    message: str = Field(..., description="错误信息")
    timestamp: datetime = Field(..., description="时间戳")


class DeleteResponse(BaseModel):
    """删除响应模型。"""

    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="提示信息")