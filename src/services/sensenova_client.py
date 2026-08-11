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
    def _build_language_instructions(cls, language: str) -> str:
        """按语言返回回答指令。"""
        if language == "en":
            return (
                "You are a professional knowledge base Q&A assistant. Your task is to answer "
                "the user's questions based on the provided reference materials.\n\n"
                "## Response Guidelines\n"
                "1. **Answer based solely on the reference materials** — do not fabricate information not found in them\n"
                "2. **Paraphrase in your own words** — avoid copying large passages verbatim\n"
                "3. **Cite sources** — mark the document index at the end of sentences, e.g. [Source 1][Source 3]\n"
                "4. **Ignore irrelevant content** — if a reference document is unrelated to the question, skip it\n"
                "5. **Questions unrelated to the references** — if the user asks about you yourself "
                "(who/what you are, your capabilities) or general knowledge, answer directly from your own "
                "knowledge and note that the references do not contain this information\n"
                "6. **Cite accurately** — only add [Source N] for information actually taken from the "
                "references; never cite sources for information from your own knowledge or common sense\n"
                "7. **Be honest when unable to answer** — if the references contain no useful information, "
                "tell the user directly that no relevant information was found\n"
                "8. **Response style** — natural, clear, conversational. Use paragraphs for structure "
                "but avoid mechanical lists\n"
                "9. **Appropriate length** — cover the topic concisely without unnecessary detail\n"
                "10. **Answer in English** — always respond in English\n"
                "11. **Skip reasoning** — do not output your chain-of-thought or reasoning process. "
                "Answer the user directly.\n"
                "12. **Thinking process (if any)** — if you produce internal reasoning, express it in "
                "complete, coherent sentences; avoid fragmented words or sentences"
            )
        # 默认中文指令
        return (
            "你是一位专业的知识库问答助手。你的任务是基于提供的参考资料，用中文回答用户的问题。\n\n"
            "## 回答规范\n"
            "1. **仅基于参考资料回答**，不得编造参考资料中不存在的信息\n"
            "2. **必须用自己的话重述**，禁止直接复制参考资料原文大段粘贴\n"
            "3. **标注来源**：引用参考资料时在句末标注对应的文档编号，如 [来源1][来源3]\n"
            "4. **忽略无关内容**：如果某篇参考资料与问题无关，忽略它，不要强行纳入回答\n"
            "5. **问题与参考资料无关时**：如果问题询问的是助手自身信息（如你是什么、你是谁、你的能力等）或通用常识问题，请直接根据你自己的知识如实回答，并明确说明参考资料中不包含相关信息\n"
            "6. **来源标注要准确**：只有确实来自参考资料的信息才能标注 [来源N]；来自自身知识或常识的信息绝对不要添加来源标注\n"
            "7. **无法回答时坦诚相告**：如果参考资料中完全没有可用的信息，直接告诉用户找不到相关信息，不要强行编造答案\n"
            "8. **回答风格**：自然、清晰的口语化表达，像在跟用户对话一样。适当分段让结构清晰，但不要机械罗列\n"
            "9. **篇幅适中**：根据问题的复杂度，回答长度控制在能说清楚即可，不要过度展开无关细节\n"
            "10. **不要输出推理过程**：直接回答用户问题，不要在回答前展示你的分析思路或思考过程\n"
            "11. **思考过程（如模型会输出）**：如产生内部思考，请用完整、连贯的句子表达，保持逻辑清晰，不要输出碎片化的词语或残缺句子"
        )

    @classmethod
    def _build_history_text(cls, messages: List[dict]) -> str:
        """将对话历史渲染为文本块。"""
        parts = []
        for msg in messages:
            role_label = "用户" if msg["role"] == "user" else "助手"
            parts.append(f"{role_label}：{msg['content']}")
        return "\n\n".join(parts)

    @classmethod
    def build_qa_prompt(
        cls,
        query: str,
        context: List[str],
        system_prompt: Optional[str] = None,
        messages: Optional[List[dict]] = None,
        language: str = "zh",
    ) -> str:
        """构建问答 Prompt。

        Args:
            query: 用户查询
            context: 检索到的上下文内容列表
            system_prompt: 自定义系统 Prompt，为 None 时使用默认指令
            messages: 多轮对话历史（[{role, content}]）
            language: 回答语言（zh/en）
        """
        context_text = "\n\n".join(
            [f"<doc index=\"{i + 1}\">\n{c.strip()}\n</doc>" for i, c in enumerate(context)]
        )

        instructions = system_prompt or cls._build_language_instructions(language)

        prompt_parts = [instructions]

        # 参考资料
        prompt_parts.append(f"<instructions>\n## 参考资料\n{context_text}")

        # 对话历史（如果有）
        if messages:
            history_text = cls._build_history_text(messages)
            prompt_parts.append(f"## 对话历史\n{history_text}")

        # 用户问题
        prompt_parts.append(f"## 用户问题\n{query}")

        prompt_parts.append("</instructions>\n\n## 回答")
        return "\n\n".join(prompt_parts)

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
        """获取或创建异步客户端（线程安全）。"""
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
        messages: Optional[List[dict]] = None,
    ) -> Dict:
        """构建请求 payload。

        当提供 messages 时，使用多消息格式（兼容 OpenAI 多轮对话协议）。
        否则使用单条 user 消息（兼容旧版单轮调用）。
        """
        if messages:
            # 多轮对话格式：messages 已包含完整历史 + 当前问题
            payload = {
                "model": self._model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        else:
            # 单轮格式：prompt 作为单条 user 消息
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
        messages: Optional[List[dict]] = None,
    ) -> str:
        """调用 SenseNova API 生成文本（异步）。

        Args:
            prompt: 输入 Prompt（单轮时为完整 prompt，多轮时仅用于 fallback）
            max_tokens: 最大输出 Token 数
            temperature: 温度参数
            stop: 停止序列
            messages: 多轮消息列表（有则使用此格式替代单条 prompt）

        Returns:
            生成的文本内容
        """
        if not self._api_key:
            raise SenseNovaAPIError("SenseNova API Key 未配置")

        url = f"{self._api_base}/chat/completions"
        payload = self._build_payload(prompt, max_tokens, temperature, stop, messages=messages)
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

    async def generate_answer(
        self,
        query: str,
        context: List[str],
        system_prompt: Optional[str] = None,
        messages: Optional[List[dict]] = None,
        language: str = "zh",
    ) -> str:
        """根据上下文生成回答（异步）。

        当提供了 messages（多轮历史）时，将当前上下文和问题拼接为 system 消息，
        与历史消息一起组成完整的多轮消息列表发送给 API。
        """
        if messages:
            # 多轮对话：构建系统消息（含参考资料 + 指令），历史作为 messages 列表
            context_text = "\n\n".join(
                [f"<doc index=\"{i + 1}\">\n{c.strip()}\n</doc>" for i, c in enumerate(context)]
            )
            instructions = system_prompt or PromptBuilder._build_language_instructions(language)
            system_content = f"{instructions}\n\n## 参考资料\n{context_text}"

            full_messages = [
                {"role": "system", "content": system_content},
                *messages,
                {"role": "user", "content": query},
            ]
            return await self.complete("", messages=full_messages)
        else:
            # 单轮对话：使用传统 prompt 拼接
            prompt = PromptBuilder.build_qa_prompt(
                query, context, system_prompt=system_prompt, language=language
            )
            return await self.complete(prompt)

    async def stream_answer(
        self,
        query: str,
        context: List[str],
        system_prompt: Optional[str] = None,
        messages: Optional[List[dict]] = None,
        language: str = "zh",
    ) -> AsyncGenerator[dict, None]:
        """流式生成回答（异步生成器）。

        使用 SSE（Server-Sent Events）协议逐 token 返回文本增量。
        兼容 OpenAI 格式的流式响应。支持多轮对话历史。

        Args:
            query: 用户查询
            context: 上下文片段列表
            system_prompt: 自定义系统 Prompt
            messages: 多轮对话历史（[{role, content}]）
            language: 回答语言（zh/en）

        Yields:
            包含 type 和 content 的字典
        """
        if not self._api_key:
            raise SenseNovaAPIError("SenseNova API Key 未配置")

        url = f"{self._api_base}/chat/completions"

        if messages:
            # 多轮对话格式
            context_text = "\n\n".join(
                [f"<doc index=\"{i + 1}\">\n{c.strip()}\n</doc>" for i, c in enumerate(context)]
            )
            instructions = system_prompt or PromptBuilder._build_language_instructions(language)
            system_content = f"{instructions}\n\n## 参考资料\n{context_text}"

            full_messages = [
                {"role": "system", "content": system_content},
                *messages,
                {"role": "user", "content": query},
            ]
            # 推理模型（如 DeepSeek-R1）会输出长思考过程，加大 max_tokens 避免回答被截断
            payload = {
                "model": self._model,
                "messages": full_messages,
                "max_tokens": 4096,
                "temperature": 0.7,
                "stream": True,
            }
        else:
            # 单轮格式
            prompt = PromptBuilder.build_qa_prompt(
                query, context, system_prompt=system_prompt, language=language
            )
            payload = {
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 4096,
                "temperature": 0.7,
                "stream": True,
            }

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
                                # 推理模型（如 DeepSeek-R1）的推理链
                                reasoning = delta.get("reasoning_content", "") or delta.get("reasoning", "")
                                if reasoning:
                                    yield {"type": "reasoning", "content": reasoning}
                                if content:
                                    completion_tokens += 1
                                    yield {"type": "content", "content": content}
                            usage = data.get("usage")
                            if usage:
                                prompt_tokens = usage.get("prompt_tokens", 0)
                                completion_tokens = usage.get("completion_tokens", completion_tokens)
                        except json.JSONDecodeError:
                            continue

            with self._lock:
                if prompt_tokens == 0:
                    prompt_tokens = len(payload.get("messages", [{"content": ""}])[0].get("content", "")) // 4
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