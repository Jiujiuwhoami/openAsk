"""领域异常体系。"""


class AppError(Exception):
    """根异常：所有应用异常的基类。"""
    pass


class KnowledgeBaseError(AppError):
    """知识库操作异常。"""
    pass


class DocumentNotFoundError(KnowledgeBaseError):
    """文档不存在。"""
    pass


class EmbeddingError(AppError):
    """向量化失败。"""
    pass


class VectorStoreError(AppError):
    """向量数据库操作异常。"""
    pass


class SenseNovaAPIError(AppError):
    """SenseNova API 调用异常。"""
    pass


class MultiModalError(AppError):
    """多模态服务调用异常。"""
    pass