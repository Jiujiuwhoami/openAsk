"""配置管理：基于 pydantic-settings 的类型安全配置。

支持从 .env 文件和环境变量加载配置，
支持类型校验、默认值、嵌套配置。

使用方式：
    from src.utils.config import settings
    api_key = settings.sense_nova.api_key
"""

from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SenseNovaSettings(BaseSettings):
    """SenseNova API 配置。"""

    model_config = SettingsConfigDict(env_prefix="SENSE_NOVA_", extra="ignore")

    api_key: str = ""
    api_base: str = "https://api.sensenova.cn/v1"
    model: str = "default"
    timeout: int = 30
    max_retries: int = 3


class ZvecSettings(BaseSettings):
    """Zvec 向量数据库配置。"""

    model_config = SettingsConfigDict(env_prefix="ZVEC_", extra="ignore")

    data_path: str = "data/zvec"
    dimension: int = 384
    cache_path: str = "data/zvec_llm_cache"
    cache_dimension: int = 384


class EmbeddingSettings(BaseSettings):
    """嵌入服务配置。"""

    model_config = SettingsConfigDict(env_prefix="EMBEDDING_", extra="ignore")

    model_name: str = "all-MiniLM-L6-v2"
    batch_size: int = 32
    device: str = "cpu"
    normalize_embeddings: bool = True


class ApiSettings(BaseSettings):
    """API 服务配置。"""

    model_config = SettingsConfigDict(env_prefix="API_", extra="ignore")

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

    model_config = SettingsConfigDict(env_prefix="RATE_LIMIT_", extra="ignore")

    enabled: bool = True
    per_user: str = "60/minute"
    global_limit: str = "1000/minute"
    strategy: str = "sliding_window"
    storage_uri: str = "memory://"


class LLMCacheSettings(BaseSettings):
    """LLM 缓存配置。"""

    model_config = SettingsConfigDict(env_prefix="LLM_CACHE_", extra="ignore")

    enabled: bool = True
    maxsize: int = 1000
    ttl: int = 86400
    similarity_threshold: float = 0.95
    storage_uri: Optional[str] = None


class LoggingSettings(BaseSettings):
    """日志配置。"""

    model_config = SettingsConfigDict(env_prefix="LOG_", extra="ignore")

    level: str = "INFO"
    file: str = "app.log"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


class MetricsSettings(BaseSettings):
    """监控配置。"""

    model_config = SettingsConfigDict(env_prefix="METRICS_", extra="ignore")

    enabled: bool = True
    port: int = 8000


class MultiModalSettings(BaseSettings):
    """多模态服务配置。"""

    model_config = SettingsConfigDict(env_prefix="MULTIMODAL_", extra="ignore")

    enabled: bool = False
    provider: str = "generic"
    api_key: str = ""
    api_base: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    timeout: int = 30


class RerankerSettings(BaseSettings):
    """重排序服务配置。"""

    model_config = SettingsConfigDict(env_prefix="RERANKER_", extra="ignore")

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

    app_name: str = "Zvec"
    environment: str = "development"
    debug: bool = False

    sense_nova: SenseNovaSettings = Field(default_factory=SenseNovaSettings)
    zvec: ZvecSettings = Field(default_factory=ZvecSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    llm_cache: LLMCacheSettings = Field(default_factory=LLMCacheSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    metrics: MetricsSettings = Field(default_factory=MetricsSettings)
    multimodal: MultiModalSettings = Field(default_factory=MultiModalSettings)
    reranker: RerankerSettings = Field(default_factory=RerankerSettings)


settings = Settings()


__all__ = [
    "settings",
    "Settings",
    "SenseNovaSettings",
    "ZvecSettings",
    "EmbeddingSettings",
    "ApiSettings",
    "RateLimitSettings",
    "LLMCacheSettings",
    "LoggingSettings",
    "MetricsSettings",
    "MultiModalSettings",
    "RerankerSettings",
]
