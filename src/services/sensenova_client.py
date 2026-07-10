"""SenseNova API 客户端：封装 Prompt 构建、API 调用、Token 监控。"""

import json
import threading
from typing import Dict, List, Optional

import httpx

from src.domain.exceptions import SenseNovaAPIError
from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.retry import retry_with_backoff

logger = get_logger(__name__)


class PromptBuilder:
    """Prompt 构建器：根据上下文和查询构建合适的 Prompt。"""

    @classmethod
    def build_qa_prompt(cls, query: str, context: List[str]) -> str:
        """构建问答 Prompt。

        Args:
            query: 用户查询
            context: 检索到的上下文内容列表

        Returns:
            完整的 Prompt 文本
        """
        context_text = "\n\n".join([f"- {c}" for c in context])

        return f"""你是一个专业的客服助手，请根据提供的上下文回答用户问题。

上下文信息：
{context_text}

用户问题：{query}

要求：
1. 严格根据上下文内容回答，不要编造信息
2. 如果上下文没有相关信息，直接说"无法回答"
3. 回答要简洁明了，不要冗长
4. 保持友好的语气

回答："""

    @classmethod
    def build_summary_prompt(cls, text: str) -> str:
        """构建摘要 Prompt。"""
        return f"""请对以下文本进行简要总结：

{text}

总结要求：
1. 不超过 100 字
2. 包含核心要点
3. 语言简洁

总结："""


class TokenMonitor:
    """Token 监控器：统计和记录 API 调用的 Token 使用情况。"""

    def __init__(self):
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._total_calls = 0

    def record(self, prompt_tokens: int, completion_tokens: int) -> None:
        """记录一次 API 调用的 Token 使用。"""
        self._total_prompt_tokens += prompt_tokens
        self._total_completion_tokens += completion_tokens
        self._total_calls += 1
        logger.debug(
            f"Token 使用: 输入 {prompt_tokens}, 输出 {completion_tokens}, "
            f"累计: 输入 {self._total_prompt_tokens}, 输出 {self._total_completion_tokens}"
        )

    def get_stats(self) -> Dict[str, int]:
        """获取 Token 使用统计。"""
        return {
            "total_calls": self._total_calls,
            "total_prompt_tokens": self._total_prompt_tokens,
            "total_completion_tokens": self._total_completion_tokens,
            "total_tokens": self._total_prompt_tokens + self._total_completion_tokens,
        }


class SenseNovaClient:
    """SenseNova API 客户端（异步）。

    负责与 SenseNova API 进行交互，封装了：
    - Prompt 构建（PromptBuilder）
    - 异步 HTTP 请求（httpx.AsyncClient）
    - 重试机制（retry_with_backoff）
    - Token 监控（TokenMonitor）

    Examples:
        >>> client = SenseNovaClient()
        >>> response = await client.complete("你好")
        >>> print(response)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
    ):
        self._api_key = api_key or settings.sense_nova.api_key
        self._api_base = api_base or settings.sense_nova.api_base
        self._model = model or settings.sense_nova.model
        self._timeout = timeout or settings.sense_nova.timeout
        self._max_retries = max_retries or settings.sense_nova.max_retries
        self._token_monitor = TokenMonitor()
        self._lock = threading.RLock()
        self._async_client: Optional[httpx.AsyncClient] = None

        if not self._api_key:
            logger.warning("SenseNova API Key 未配置，API 调用可能失败")

    def _get_client(self) -> httpx.AsyncClient:
        """获取或创建异步客户端。"""
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=self._timeout)
        return self._async_client

    def _build_payload(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
        stop: Optional[List[str]] = None,
    ) -> Dict:
        """构建请求 payload。"""
        payload = {
            "model": self._model,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if stop:
            payload["stop"] = stop
        return payload

    def _build_headers(self) -> Dict[str, str]:
        """构建请求 headers。"""
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _parse_response(self, response: httpx.Response) -> str:
        """解析 API 响应。"""
        if response.status_code != 200:
            raise SenseNovaAPIError(
                f"API 请求失败: {response.status_code} - {response.text}"
            )

        data = response.json()
        content = data["choices"][0]["message"]["content"]
        prompt_tokens = data.get("usage", {}).get("prompt_tokens", 0)
        completion_tokens = data.get("usage", {}).get("completion_tokens", 0)

        with self._lock:
            self._token_monitor.record(prompt_tokens, completion_tokens)

        logger.debug(f"SenseNova API 调用成功，返回 {len(content)} 字符")
        return content

    @retry_with_backoff(max_retries=3)
    async def complete(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
        stop: Optional[List[str]] = None,
    ) -> str:
        """调用 SenseNova API 生成文本（异步）。

        使用 httpx.AsyncClient 进行异步请求，不阻塞事件循环。

        Args:
            prompt: 输入 Prompt
            max_tokens: 最大输出 Token 数
            temperature: 温度参数（0-1，越高越随机）
            stop: 停止序列

        Returns:
            生成的文本内容

        Raises:
            SenseNovaAPIError: API 调用失败
        """
        if not self._api_key:
            raise SenseNovaAPIError("SenseNova API Key 未配置")

        url = f"{self._api_base}/chat/completions"
        payload = self._build_payload(prompt, max_tokens, temperature, stop)
        headers = self._build_headers()

        try:
            client = self._get_client()
            response = await client.post(url, headers=headers, json=payload)
            return self._parse_response(response)
        except httpx.HTTPError as e:
            raise SenseNovaAPIError(f"HTTP 请求失败: {e}")
        except json.JSONDecodeError as e:
            raise SenseNovaAPIError(f"JSON 解析失败: {e}")
        except KeyError as e:
            raise SenseNovaAPIError(f"API 返回格式错误: {e}")

    async def generate_answer(self, query: str, context: List[str]) -> str:
        """根据上下文生成回答（异步）。"""
        prompt = PromptBuilder.build_qa_prompt(query, context)
        return await self.complete(prompt)

    @property
    def is_configured(self) -> bool:
        """返回 API 密钥是否已配置。"""
        return bool(self._api_key)

    def get_token_stats(self) -> Dict[str, int]:
        """获取 Token 使用统计。"""
        return self._token_monitor.get_stats()

    async def close(self):
        """关闭异步客户端。"""
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None
