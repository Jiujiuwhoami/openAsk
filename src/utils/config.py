"""配置管理：基于 pydantic-settings 的类型安全配置。

支持从 .env 文件和环境变量加载配置，
支持类型校验、默认值、嵌套配置。

使用方式：
    from src.utils.config import settings
    api_key = settings.llm.api_key
"""

from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ------------------------------------------------------------------
# 子模型基础配置：env_file 必须在每个嵌套类上显式声明，
# 否则 pydantic-settings 2.x 不会传播父类的 .env 文件路径。
# 见 https://github.com/pydantic/pydantic-settings/issues/915
# ------------------------------------------------------------------
_SETTINGS_BASE_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
)


class LLMSettings(BaseSettings):
    """LLM API 配置（兼容 OpenAI 接口格式）。"""

    model_config = SettingsConfigDict(**_SETTINGS_BASE_CONFIG, env_prefix="LLM_")

    api_key: str = ""
    api_base: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    timeout: int = 30
    max_retries: int = 3


class ZvecSettings(BaseSettings):
    """Zvec 向量数据库配置。"""

    model_config = SettingsConfigDict(**_SETTINGS_BASE_CONFIG, env_prefix="ZVEC_")

    data_path: str = "data/zvec"
    dimension: int = 384
    cache_path: str = "data/zvec_llm_cache"
    cache_dimension: int = 384


class EmbeddingSettings(BaseSettings):
    """嵌入服务配置。"""

    model_config = SettingsConfigDict(**_SETTINGS_BASE_CONFIG, env_prefix="EMBEDDING_")

    model_name: str = "all-MiniLM-L6-v2"
    batch_size: int = 32
    device: str = "cpu"
    normalize_embeddings: bool = True


class ApiSettings(BaseSettings):
    """API 服务配置。"""

    model_config = SettingsConfigDict(**_SETTINGS_BASE_CONFIG, env_prefix="API_")

    host: str = "127.0.0.1"
    port: int = 8000
    workers: int = 4
    cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:3000", "http://localhost:8000"])
    api_key: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v


class RateLimitSettings(BaseSettings):
    """限流配置。"""

    model_config = SettingsConfigDict(**_SETTINGS_BASE_CONFIG, env_prefix="RATE_LIMIT_")

    enabled: bool = True
    per_user: str = "60/minute"
    global_limit: str = "1000/minute"
    strategy: str = "sliding_window"
    storage_uri: str = "memory://"


class EmbeddingCacheSettings(BaseSettings):
    """查询 Embedding 缓存配置（相同查询 → 复用向量）。"""

    model_config = SettingsConfigDict(**_SETTINGS_BASE_CONFIG, env_prefix="EMBEDDING_CACHE_")

    enabled: bool = True
    maxsize: int = 10000
    ttl: int = 3600  # 1 hour


class LLMCacheSettings(BaseSettings):
    """LLM 缓存配置。"""

    model_config = SettingsConfigDict(**_SETTINGS_BASE_CONFIG, env_prefix="LLM_CACHE_")

    enabled: bool = True
    maxsize: int = 1000
    ttl: int = 86400
    similarity_threshold: float = 0.95
    storage_uri: Optional[str] = None


class LoggingSettings(BaseSettings):
    """日志配置。"""

    model_config = SettingsConfigDict(**_SETTINGS_BASE_CONFIG, env_prefix="LOG_")

    level: str = "INFO"
    file: str = "app.log"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


class MetricsSettings(BaseSettings):
    """监控配置。"""

    model_config = SettingsConfigDict(**_SETTINGS_BASE_CONFIG, env_prefix="METRICS_")

    enabled: bool = True
    port: int = 8000


class MultiModalSettings(BaseSettings):
    """多模态服务配置。"""

    model_config = SettingsConfigDict(**_SETTINGS_BASE_CONFIG, env_prefix="MULTIMODAL_")

    enabled: bool = False
    provider: str = "generic"
    api_key: str = ""
    api_base: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    timeout: int = 30


class TenantStorageSettings(BaseSettings):
    """租户存储配置（SQLite 优先）。"""

    model_config = SettingsConfigDict(**_SETTINGS_BASE_CONFIG, env_prefix="TENANT_")

    storage_type: str = "sqlite"
    storage_path: str = "data/tenants.db"
    default_tenant_id: str = "default"
    default_tenant_api_key: str = ""


class RerankerSettings(BaseSettings):
    """重排序服务配置。"""

    model_config = SettingsConfigDict(**_SETTINGS_BASE_CONFIG, env_prefix="RERANKER_")

    enabled: bool = True
    model_name: str = "BAAI/bge-reranker-v2-m3"
    device: str = "cpu"
    recall_top_k: int = 20
    rerank_top_k: int = 5


class Settings(BaseSettings):
    """全局配置根配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "OpenAsk"
    environment: str = "development"
    debug: bool = False

    llm: LLMSettings = Field(default_factory=LLMSettings)
    zvec: ZvecSettings = Field(default_factory=ZvecSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    embedding_cache: EmbeddingCacheSettings = Field(default_factory=EmbeddingCacheSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    llm_cache: LLMCacheSettings = Field(default_factory=LLMCacheSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    metrics: MetricsSettings = Field(default_factory=MetricsSettings)
    multimodal: MultiModalSettings = Field(default_factory=MultiModalSettings)
    reranker: RerankerSettings = Field(default_factory=RerankerSettings)
    tenant: TenantStorageSettings = Field(default_factory=TenantStorageSettings)


settings = Settings()


__all__ = [
    "settings",
    "Settings",
    "LLMSettings",
    "ZvecSettings",
    "EmbeddingSettings",
    "ApiSettings",
    "EmbeddingCacheSettings",
    "RateLimitSettings",
    "LLMCacheSettings",
    "LoggingSettings",
    "MetricsSettings",
    "MultiModalSettings",
    "RerankerSettings",
    "TenantStorageSettings",
]
