"""SenseNova API 客户端扩展测试 — Prompt 构建、payload、流式、解析。"""

import json
import pytest
from unittest.mock import Mock, AsyncMock, patch

from src.services.sensenova_client import (
    SenseNovaClient,
    PromptBuilder,
    TokenMonitor,
)
from src.domain.exceptions import SenseNovaAPIError


# ================================================================
# PromptBuilder 扩展测试
# ================================================================


class TestPromptBuilderExtended:
    def test_language_instructions_zh(self):
        instructions = PromptBuilder._build_language_instructions("zh")
        assert "中文" in instructions
        assert "知识库问答助手" in instructions

    def test_language_instructions_en(self):
        instructions = PromptBuilder._build_language_instructions("en")
        assert "English" in instructions
        assert "knowledge base" in instructions

    def test_build_history_text(self):
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好，有什么可以帮你的？"},
            {"role": "user", "content": "退货政策是什么"},
        ]
        text = PromptBuilder._build_history_text(messages)
        assert "用户：你好" in text
        assert "助手：你好，有什么可以帮你的？" in text
        assert "用户：退货政策是什么" in text

    def test_build_history_text_empty(self):
        assert PromptBuilder._build_history_text([]) == ""

    def test_build_qa_prompt_with_history(self):
        prompt = PromptBuilder.build_qa_prompt(
            query="退货",
            context=["退货需在7天内申请"],
            messages=[{"role": "user", "content": "之前的问题"}],
            language="zh",
        )
        assert "退货需在7天内申请" in prompt
        assert "退货" in prompt
        assert "之前的问题" in prompt
        assert "对话历史" in prompt

    def test_build_qa_prompt_with_system_prompt(self):
        prompt = PromptBuilder.build_qa_prompt(
            query="问题",
            context=["上下文"],
            system_prompt="自定义系统提示词",
        )
        assert "自定义系统提示词" in prompt
        # 不应包含默认指令
        assert "知识库问答助手" not in prompt

    def test_build_qa_prompt_no_context(self):
        prompt = PromptBuilder.build_qa_prompt("问题", [])
        assert "问题" in prompt

    def test_build_summary_prompt_empty(self):
        prompt = PromptBuilder.build_summary_prompt("")
        assert "总结" in prompt


# ================================================================
# TokenMonitor
# ================================================================


class TestTokenMonitorExtended:
    def test_get_stats_empty(self):
        monitor = TokenMonitor()
        stats = monitor.get_stats()
        assert stats["total_calls"] == 0
        assert stats["total_tokens"] == 0

    def test_record_single(self):
        monitor = TokenMonitor()
        monitor.record(10, 5)
        assert monitor.get_stats()["total_tokens"] == 15


# ================================================================
# _build_payload
# ================================================================


class TestBuildPayload:
    def test_payload_single_message(self):
        client = SenseNovaClient(api_key="test")
        payload = client._build_payload("Hello", max_tokens=512, temperature=0.5)
        assert payload["model"] is not None
        assert payload["messages"][0]["role"] == "user"
        assert payload["messages"][0]["content"] == "Hello"
        assert payload["max_tokens"] == 512
        assert payload["temperature"] == 0.5

    def test_payload_with_messages(self):
        client = SenseNovaClient(api_key="test")
        messages = [{"role": "system", "content": "you are a bot"}, {"role": "user", "content": "hi"}]
        payload = client._build_payload("fallback", messages=messages)
        assert payload["messages"] == messages

    def test_payload_with_stop(self):
        client = SenseNovaClient(api_key="test")
        payload = client._build_payload("test", stop=["\n", "."])
        assert payload["stop"] == ["\n", "."]


# ================================================================
# _parse_response
# ================================================================


class TestParseResponse:
    def test_parse_success(self):
        client = SenseNovaClient(api_key="test")
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "你好"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        result = client._parse_response(mock_resp)
        assert result == "你好"

    def test_parse_with_reasoning(self):
        client = SenseNovaClient(api_key="test")
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"reasoning": "thinking..."}}],
            "usage": {},
        }
        result = client._parse_response(mock_resp)
        assert result == "thinking..."

    def test_parse_error_status(self):
        client = SenseNovaClient(api_key="test")
        mock_resp = Mock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        with pytest.raises(SenseNovaAPIError):
            client._parse_response(mock_resp)


# ================================================================
# generate_answer with messages
# ================================================================


class TestGenerateAnswer:
    @pytest.mark.asyncio
    async def test_generate_answer_with_messages(self):
        client = SenseNovaClient(api_key="test")
        mock_complete = AsyncMock(return_value="回答")
        client.complete = mock_complete

        result = await client.generate_answer(
            "问题", ["上下文"],
            messages=[{"role": "user", "content": "历史"}],
        )
        assert result == "回答"
        mock_complete.assert_called_once()
        args, kwargs = mock_complete.call_args
        # 验证多轮消息格式包含 system
        messages = kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "问题"

    @pytest.mark.asyncio
    async def test_generate_answer_with_system_prompt(self):
        client = SenseNovaClient(api_key="test")
        client.complete = AsyncMock(return_value="回答")
        result = await client.generate_answer("问题", [], system_prompt="自定义", messages=[{"role": "user", "content": "hi"}])
        assert result == "回答"

    @pytest.mark.asyncio
    async def test_generate_answer_english(self):
        client = SenseNovaClient(api_key="test")
        client.complete = AsyncMock(return_value="answer")
        result = await client.generate_answer("question", ["context"], language="en")
        assert result == "answer"


# ================================================================
# is_configured / get_token_stats / close
# ================================================================


class TestClientProperties:
    def test_is_configured_true(self):
        client = SenseNovaClient(api_key="key")
        assert client.is_configured is True

    def test_is_configured_false(self):
        client = SenseNovaClient(api_key="test-key")
        client._api_key = ""  # 绕过 settings 回退
        assert client.is_configured is False

    def test_get_token_stats(self):
        client = SenseNovaClient(api_key="test")
        stats = client.get_token_stats()
        assert stats["total_calls"] == 0

    @pytest.mark.asyncio
    async def test_close(self):
        client = SenseNovaClient(api_key="test")
        mock_http = AsyncMock()
        client._async_client = mock_http
        await client.close()
        mock_http.aclose.assert_called_once()
        assert client._async_client is None

    @pytest.mark.asyncio
    async def test_close_no_client(self):
        client = SenseNovaClient(api_key="test")
        client._async_client = None
        await client.close()  # 不应抛出异常


# ================================================================
# stream_answer 测试
# ================================================================


class TestStreamAnswer:
    @pytest.mark.asyncio
    async def test_stream_no_api_key(self):
        client = SenseNovaClient(api_key="test-key")
        client._api_key = ""
        with pytest.raises(SenseNovaAPIError, match="未配置"):
            async for _ in client.stream_answer("问题", []):
                pass

    def _make_mock_stream_client(self, status_code=200, lines=None):
        """创建模拟的 HTTP 客户端，支持 async with stream()"""
        import types

        # 真实异步生成器作为 aiter_lines（async for 需要真实 __aiter__）
        async def _aiter_lines():
            for line in (lines or []):
                yield line

        mock_response = Mock()
        mock_response.status_code = status_code
        mock_response.aiter_lines = _aiter_lines
        mock_response.aread = AsyncMock(return_value=b"error")

        # 创建 async context manager 用于 stream()
        class AsyncContextManager:
            async def __aenter__(self):
                return mock_response
            async def __aexit__(self, *args):
                pass

        mock_http = Mock()
        mock_http.stream = Mock(return_value=AsyncContextManager())
        return mock_http

    @pytest.mark.asyncio
    async def test_stream_single_message(self):
        client = SenseNovaClient(api_key="test")
        client._async_client = self._make_mock_stream_client(lines=[
            'data: {"choices":[{"delta":{"content":"你"}}]}',
            'data: {"choices":[{"delta":{"content":"好"}}]}',
            'data: {"choices":[{"delta":{}}]}',
            "data: [DONE]",
        ])

        chunks = []
        async for chunk in client.stream_answer("问题", ["上下文"]):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[0]["type"] == "content"
        assert chunks[0]["content"] == "你"
        assert chunks[1]["content"] == "好"

    @pytest.mark.asyncio
    async def test_stream_with_reasoning(self):
        client = SenseNovaClient(api_key="test")
        client._async_client = self._make_mock_stream_client(lines=[
            'data: {"choices":[{"delta":{"reasoning_content":"思考中..."}}]}',
            'data: {"choices":[{"delta":{"content":"答案"}}]}',
            "data: [DONE]",
        ])

        chunks = []
        async for chunk in client.stream_answer("问题", ["上下文"]):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[0]["type"] == "reasoning"
        assert chunks[1]["type"] == "content"

    @pytest.mark.asyncio
    async def test_stream_error_status(self):
        client = SenseNovaClient(api_key="test")
        client._async_client = self._make_mock_stream_client(status_code=500, lines=[])

        with pytest.raises(SenseNovaAPIError):
            async for _ in client.stream_answer("问题", ["上下文"]):
                pass

    @pytest.mark.asyncio
    async def test_stream_with_messages(self):
        client = SenseNovaClient(api_key="test")
        client._async_client = self._make_mock_stream_client(lines=[
            'data: {"choices":[{"delta":{"content":"回答"}}]}',
            "data: [DONE]",
        ])

        send_count = 0
        async for _ in client.stream_answer("问题", ["上下文"], messages=[{"role": "user", "content": "历史"}]):
            send_count += 1
        assert send_count == 1