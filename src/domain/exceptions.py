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


class UserNotFoundError(AppError):
    """用户不存在。"""
    pass


class UserAlreadyExistsError(AppError):
    """用户已存在（邮箱重复）。"""
    pass


class InvalidCredentialsError(AppError):
    """邮箱或密码错误。"""
    pass


class UserSuspendedError(AppError):
    """用户已被禁用。"""
    pass


class ProjectNotFoundError(AppError):
    """项目不存在。"""
    pass


class ProjectSuspendedError(AppError):
    """项目已被禁用。"""
    pass