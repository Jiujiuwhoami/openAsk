"""SenseNova API 客户端测试。"""

import pytest
from unittest.mock import Mock, patch, AsyncMock

from src.services.sensenova_client import (
    SenseNovaClient,
    PromptBuilder,
    TokenMonitor,
)
from src.domain.exceptions import SenseNovaAPIError


def test_prompt_builder_build_qa():
    """测试 Prompt 构建器构建问答 Prompt。"""
    query = "退货政策是什么？"
    context = ["退货需在收到商品后7天内申请", "需保持商品完好"]

    prompt = PromptBuilder.build_qa_prompt(query, context)

    assert query in prompt
    assert "退货需在收到商品后7天内申请" in prompt
    assert "需保持商品完好" in prompt


def test_prompt_builder_build_summary():
    """测试 Prompt 构建器构建摘要 Prompt。"""
    text = "这是一段需要被总结的文本内容。"

    prompt = PromptBuilder.build_summary_prompt(text)

    assert "总结" in prompt
    assert text in prompt


def test_token_monitor():
    """测试 Token 监控器。"""
    monitor = TokenMonitor()

    monitor.record(100, 50)
    monitor.record(200, 100)

    stats = monitor.get_stats()

    assert stats["total_calls"] == 2
    assert stats["total_prompt_tokens"] == 300
    assert stats["total_completion_tokens"] == 150
    assert stats["total_tokens"] == 450


@pytest.mark.asyncio
async def test_client_no_api_key():
    """测试未配置 API Key 的客户端。"""
    client = SenseNovaClient(api_key="")

    with pytest.raises(SenseNovaAPIError):
        await client.complete("测试")


@pytest.mark.asyncio
async def test_client_complete_success():
    """测试 API 调用成功。"""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "测试回答"}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 20},
    }

    client = SenseNovaClient(api_key="test-key")
    mock_async_client = AsyncMock()
    mock_async_client.post.return_value = mock_response
    client._async_client = mock_async_client

    result = await client.complete("测试")

    assert result == "测试回答"
    mock_async_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_client_complete_failure():
    """测试 API 调用失败。"""
    mock_response = Mock()
    mock_response.status_code = 500
    mock_response.text = "服务器错误"

    client = SenseNovaClient(api_key="test-key")
    mock_async_client = AsyncMock()
    mock_async_client.post.return_value = mock_response
    client._async_client = mock_async_client

    with pytest.raises(SenseNovaAPIError):
        await client.complete("测试")


@pytest.mark.asyncio
async def test_generate_answer():
    """测试生成回答。"""
    client = SenseNovaClient(api_key="test-key")

    mock_complete = AsyncMock(return_value="这是回答")
    client.complete = mock_complete

    result = await client.generate_answer("问题", ["上下文1", "上下文2"])

    assert result == "这是回答"
    mock_complete.assert_called_once()
