"""多模态服务实现：支持多种云端 API。"""

import base64
import json
from typing import Optional

import httpx
import numpy as np

from src.domain.exceptions import MultiModalError
from src.infrastructure.interfaces.multimodal_service import MultiModalService
from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.retry import retry_with_backoff

logger = get_logger(__name__)


class OpenAIMultiModalService(MultiModalService):
    """OpenAI GPT-4V 多模态服务实现。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self._api_key = api_key or settings.multimodal.api_key
        self._api_base = api_base or settings.multimodal.api_base
        self._model = model or settings.multimodal.model

        if not self._api_key:
            logger.warning("OpenAI API Key 未配置")

    @retry_with_backoff(max_retries=3)
    def describe_image(self, image_path: str, prompt: Optional[str] = None) -> str:
        """描述图片内容。"""
        prompt = prompt or "请详细描述这张图片的内容。"
        return self._call_api(image_path, prompt)

    @retry_with_backoff(max_retries=3)
    def encode_image(self, image_path: str) -> np.ndarray:
        """将图片编码为向量（使用文本描述的嵌入）。

        由于 OpenAI API 不直接返回图片嵌入向量，这里先描述图片，
        再将描述文本转为向量。如果需要真正的图片嵌入，应使用 CLIP 等模型。
        """
        description = self.describe_image(image_path)
        return self._get_text_embedding(description)

    @retry_with_backoff(max_retries=3)
    def chat_with_image(self, image_path: str, query: str) -> str:
        """基于图片回答问题。"""
        return self._call_api(image_path, query)

    def _call_api(self, image_path: str, prompt: str) -> str:
        """调用 OpenAI API。"""
        if not self._api_key:
            raise MultiModalError("OpenAI API Key 未配置")

        url = f"{self._api_base}/chat/completions"
        encoded_image = self._encode_image(image_path)

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"},
                        },
                    ],
                }
            ],
            "max_tokens": 1024,
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=30) as client:
                response = client.post(url, headers=headers, json=payload)

            if response.status_code != 200:
                raise MultiModalError(
                    f"API 请求失败: {response.status_code} - {response.text}"
                )

            data = response.json()
            return data["choices"][0]["message"]["content"]

        except httpx.HTTPError as e:
            raise MultiModalError(f"HTTP 请求失败: {e}")
        except json.JSONDecodeError as e:
            raise MultiModalError(f"JSON 解析失败: {e}")
        except KeyError as e:
            raise MultiModalError(f"API 返回格式错误: {e}")

    def _get_text_embedding(self, text: str) -> np.ndarray:
        """获取文本嵌入（使用 OpenAI Embedding API）。"""
        if not self._api_key:
            raise MultiModalError("OpenAI API Key 未配置")

        url = f"{self._api_base}/embeddings"

        payload = {
            "model": "text-embedding-3-small",
            "input": text,
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=30) as client:
                response = client.post(url, headers=headers, json=payload)

            if response.status_code != 200:
                raise MultiModalError(
                    f"Embedding API 请求失败: {response.status_code} - {response.text}"
                )

            data = response.json()
            return np.array(data["data"][0]["embedding"], dtype=np.float32)

        except Exception as e:
            raise MultiModalError(f"获取文本嵌入失败: {e}")

    @staticmethod
    def _encode_image(image_path: str) -> str:
        """将图片文件编码为 base64。"""
        try:
            with open(image_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            raise MultiModalError(f"图片编码失败: {e}")


class GenericMultiModalService(MultiModalService):
    """通用多模态服务实现。

    支持自定义 API 格式，通过配置适配不同的云端服务。
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self._api_key = api_key or settings.multimodal.api_key
        self._api_base = api_base or settings.multimodal.api_base
        self._model = model or settings.multimodal.model

    @retry_with_backoff(max_retries=3)
    def describe_image(self, image_path: str, prompt: Optional[str] = None) -> str:
        """描述图片内容。"""
        return self._call_generic_api(image_path, prompt or "描述这张图片")

    @retry_with_backoff(max_retries=3)
    def encode_image(self, image_path: str) -> np.ndarray:
        """将图片编码为向量（通用实现）。"""
        description = self.describe_image(image_path)
        from src.infrastructure.embedding_service import SentenceBertEmbeddingService

        embedder = SentenceBertEmbeddingService()
        return embedder.encode(description)

    @retry_with_backoff(max_retries=3)
    def chat_with_image(self, image_path: str, query: str) -> str:
        """基于图片回答问题。"""
        return self._call_generic_api(image_path, query)

    def _call_generic_api(self, image_path: str, prompt: str) -> str:
        """调用通用多模态 API。"""
        if not self._api_key:
            raise MultiModalError("多模态 API Key 未配置")

        encoded_image = self._encode_image(image_path)

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"},
                        },
                    ],
                }
            ],
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=30) as client:
                response = client.post(self._api_base, headers=headers, json=payload)

            if response.status_code != 200:
                raise MultiModalError(
                    f"API 请求失败: {response.status_code} - {response.text}"
                )

            data = response.json()
            return data["choices"][0]["message"]["content"]

        except Exception as e:
            raise MultiModalError(f"多模态 API 调用失败: {e}")

    @staticmethod
    def _encode_image(image_path: str) -> str:
        """将图片文件编码为 base64。"""
        try:
            with open(image_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            raise MultiModalError(f"图片编码失败: {e}")


class MultiModalServiceFactory:
    """多模态服务工厂类。

    根据配置的提供商创建对应的多模态服务实例。

    Examples:
        >>> factory = MultiModalServiceFactory()
        >>> service = factory.get_service()
        >>> description = service.describe_image("image.jpg")
    """

    _PROVIDER_MAP = {
        "openai": OpenAIMultiModalService,
        "generic": GenericMultiModalService,
    }

    @classmethod
    def get_service(
        cls,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
    ) -> MultiModalService:
        """获取多模态服务实例。

        Args:
            provider: 服务提供商（openai / generic）
            api_key: API Key
            api_base: API 基础 URL
            model: 模型名称

        Returns:
            多模态服务实例

        Raises:
            MultiModalError: 不支持的服务提供商
        """
        provider = provider or settings.multimodal.provider
        api_key = api_key or settings.multimodal.api_key
        api_base = api_base or settings.multimodal.api_base
        model = model or settings.multimodal.model

        if provider not in cls._PROVIDER_MAP:
            raise MultiModalError(f"不支持的多模态服务提供商: {provider}")

        service_class = cls._PROVIDER_MAP[provider]
        logger.info(f"初始化多模态服务: {service_class.__name__}")
        return service_class(api_key=api_key, api_base=api_base, model=model)