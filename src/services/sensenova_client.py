"""SenseNova API 客户端：封装 Prompt 构建、API 调用、Token 监控。"""

import json
import threading
from typing import AsyncGenerator, Dict, List, Optional

import httpx

from src.domain.exceptions import SenseNovaAPIError
from src.infrastructure.interfaces.llm_client import LLMClient
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

        return f"""你是一个专业的 AI 知识库助手，基于以下参考资料回答问题。

## 参考资料
{context_text}

## 用户问题
{query}

## 回答规范
1. 严格根据参考资料回答，不要编造信息
2. 如果参考资料不足以完整回答，基于已有信息尽量回答，并说明你回答的依据
3. **回答必须完整，不要中途截断**——把每个要点展开说明，确保内容自然收尾
4. 保持清晰、有条理的结构，适当使用分段、列表等格式
5. 语气友好、专业

## 回答"""

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


class SenseNovaClient(LLMClient):
    """SenseNova API 客户端（异步），实现 LLMClient 接口。

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
        self._api_key = api_key or settings.llm.api_key
        self._api_base = api_base or settings.llm.api_base
        self._model = model or settings.llm.model
        self._timeout = timeout or settings.llm.timeout
        self._max_retries = max_retries or settings.llm.max_retries
        self._token_monitor = TokenMonitor()
        self._lock = threading.RLock()
        self._async_client: Optional[httpx.AsyncClient] = None

        if not self._api_key:
            logger.warning("SenseNova API Key 未配置，API 调用可能失败")

    def _get_client(self) -> httpx.AsyncClient:
        """获取或创建异步客户端（线程安全）。

        使用双重检查锁定模式：先在锁外快速检查，获取锁后再次检查，
        避免每次调用都获取锁，同时保证线程安全。
        """
        if self._async_client is None:
            with self._lock:
                if self._async_client is None:
                    self._async_client = httpx.AsyncClient(timeout=self._timeout)
        return self._async_client

    def _build_payload(
        self,
        prompt: str,
        max_tokens: int = 2048,
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
        """解析 API 响应，兼容 content 和 reasoning 字段。"""
        if response.status_code != 200:
            raise SenseNovaAPIError(
                f"API 请求失败: {response.status_code} - {response.text}"
            )

        data = response.json()
        message = data["choices"][0].get("message", {})
        # 优先取 content，部分推理模型使用 reasoning 字段
        content = message.get("content") or message.get("reasoning", "")
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
        max_tokens: int = 2048,
        temperature: float = 0.7,
        stop: Optional[List[str]] = None,
    ) -> str:
        """调用 SenseNova API 生成文本（异步）。

        使用 httpx.AsyncClient 进行异步请求，不阻塞事件循环。

        Args:
            prompt: 输入 Prompt
            max_tokens: 最大输出 Token 数（默认 2048）
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

    async def stream_answer(
        self, query: str, context: List[str]
    ) -> AsyncGenerator[str, None]:
        """流式生成回答（异步生成器）。

        使用 SSE（Server-Sent Events）协议逐 token 返回文本增量。
        兼容 OpenAI 格式的流式响应。

        Args:
            query: 用户查询
            context: 上下文片段列表

        Yields:
            回答文本增量（通常是 token 级别）
        """
        if not self._api_key:
            raise SenseNovaAPIError("SenseNova API Key 未配置")

        prompt = PromptBuilder.build_qa_prompt(query, context)
        url = f"{self._api_base}/chat/completions"
        payload = self._build_payload(prompt, max_tokens=2048)
        payload["stream"] = True
        headers = self._build_headers()

        prompt_tokens = 0
        completion_tokens = 0

        logger.debug(f"流式请求开始: {url}, 模型: {self._model}, 上下文长度: {len(context)}")

        try:
            client = self._get_client()
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                logger.debug(f"流式响应状态码: {resp.status_code}")
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise SenseNovaAPIError(
                        f"流式 API 请求失败: {resp.status_code} - {body.decode()}"
                    )

                buffer = ""
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            choices = data.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                # 标准 OpenAI 格式: delta.content
                                content = delta.get("content", "")
                                # 部分模型（如 DeepSeek）将内容放在 delta.reasoning 中
                                if not content:
                                    content = delta.get("reasoning", "")
                                if content:
                                    completion_tokens += 1
                                    yield content
                            usage = data.get("usage")
                            if usage:
                                prompt_tokens = usage.get("prompt_tokens", 0)
                                completion_tokens = usage.get("completion_tokens", completion_tokens)
                        except json.JSONDecodeError:
                            continue

            with self._lock:
                if prompt_tokens == 0:
                    prompt_tokens = len(prompt) // 4
                self._token_monitor.record(prompt_tokens, completion_tokens)

            logger.debug(f"SenseNova 流式调用完成，返回约 {completion_tokens} tokens")
        except httpx.HTTPError as e:
            raise SenseNovaAPIError(f"流式 HTTP 请求失败: {e}")
        except Exception as e:
            raise SenseNovaAPIError(f"流式生成失败: {e}")

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
