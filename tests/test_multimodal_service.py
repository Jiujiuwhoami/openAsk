"""多模态服务测试 — OpenAI、通用、工厂（mock httpx）。"""

import json
import base64
from unittest.mock import Mock, patch, MagicMock, mock_open

import pytest
import numpy as np

from src.domain.exceptions import MultiModalError
from src.infrastructure.multimodal_service import (
    OpenAIMultiModalService,
    GenericMultiModalService,
    MultiModalServiceFactory,
)


# ================================================================
# 辅助函数
# ================================================================


def _make_mock_response(status_code=200, json_data=None):
    mock_resp = Mock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data or {
        "choices": [{"message": {"content": "这是一张图片的描述"}}],
    }
    mock_resp.text = json.dumps(json_data) if json_data else "ok"
    return mock_resp


# ================================================================
# OpenAIMultiModalService
# ================================================================


class TestOpenAIMultiModalService:
    @pytest.fixture
    def service(self):
        with patch("src.infrastructure.multimodal_service.settings") as ms:
            ms.multimodal.api_key = "sk-test"
            ms.multimodal.api_base = "https://api.openai.com/v1"
            ms.multimodal.model = "gpt-4o"
            return OpenAIMultiModalService(api_key="sk-test", api_base="https://api.openai.com/v1")

    def test_no_api_key_warns(self):
        with patch("src.infrastructure.multimodal_service.settings") as ms:
            ms.multimodal.api_key = ""
            ms.multimodal.api_base = "https://api.openai.com/v1"
            ms.multimodal.model = "gpt-4o"
            with patch("src.infrastructure.multimodal_service.logger") as mock_log:
                service = OpenAIMultiModalService(api_key="")
                mock_log.warning.assert_called_once()

    @patch("src.infrastructure.multimodal_service.httpx.Client")
    def test_describe_image_success(self, mock_httpx_client, service):
        mock_client = Mock()
        mock_client.post.return_value = _make_mock_response()
        mock_httpx_client.return_value.__enter__.return_value = mock_client

        with patch.object(service, "_encode_image", return_value="base64img"):
            result = service.describe_image("image.jpg")

        assert result == "这是一张图片的描述"
        mock_client.post.assert_called_once()

    @patch("src.infrastructure.multimodal_service.httpx.Client")
    def test_describe_image_with_custom_prompt(self, mock_httpx_client, service):
        mock_client = Mock()
        mock_client.post.return_value = _make_mock_response()
        mock_httpx_client.return_value.__enter__.return_value = mock_client

        with patch.object(service, "_encode_image", return_value="base64img"):
            result = service.describe_image("image.jpg", prompt="请描述颜色")

        assert result == "这是一张图片的描述"

    @patch("src.infrastructure.multimodal_service.httpx.Client")
    def test_chat_with_image(self, mock_httpx_client, service):
        mock_client = Mock()
        mock_client.post.return_value = _make_mock_response()
        mock_httpx_client.return_value.__enter__.return_value = mock_client

        with patch.object(service, "_encode_image", return_value="base64img"):
            result = service.chat_with_image("image.jpg", "图里有什么？")

        assert result == "这是一张图片的描述"

    @patch("src.infrastructure.multimodal_service.httpx.Client")
    def test_api_error_raises(self, mock_httpx_client, service):
        mock_client = Mock()
        mock_client.post.return_value = _make_mock_response(status_code=400)
        mock_httpx_client.return_value.__enter__.return_value = mock_client

        with patch.object(service, "_encode_image", return_value="base64img"):
            with pytest.raises(MultiModalError):
                service.describe_image("image.jpg")

    def test_call_api_no_api_key(self, service):
        service._api_key = ""
        with pytest.raises(MultiModalError, match="未配置"):
            service._call_api("image.jpg", "prompt")

    @patch("src.infrastructure.multimodal_service.httpx.Client")
    def test_httpx_error_raises(self, mock_httpx_client, service):
        import httpx
        mock_client = Mock()
        mock_client.post.side_effect = httpx.ConnectError("connection error")
        mock_httpx_client.return_value.__enter__.return_value = mock_client

        with patch.object(service, "_encode_image", return_value="base64img"):
            with pytest.raises(MultiModalError, match="HTTP"):
                service._call_api("image.jpg", "prompt")

    def test_encode_image_file_not_found(self, service):
        with pytest.raises(MultiModalError, match="图片编码"):
            service._encode_image("/nonexistent/path.jpg")

    @patch("builtins.open", new_callable=mock_open, read_data=b"fake_image_data")
    def test_encode_image_success(self, mock_file, service):
        result = service._encode_image("/path/to/image.jpg")
        assert result == base64.b64encode(b"fake_image_data").decode("utf-8")
        mock_file.assert_called_once_with("/path/to/image.jpg", "rb")

    @patch("src.infrastructure.multimodal_service.httpx.Client")
    def test_encode_image_uses_embedding_api(self, mock_httpx_client, service):
        """encode_image 调用 describe_image 后调用 embedding API。"""
        # 第一次调用：describe_image
        # 第二次调用：_get_text_embedding
        responses = [
            _make_mock_response(json_data={
                "choices": [{"message": {"content": "图片描述文本"}}],
            }),
            _make_mock_response(json_data={
                "data": [{"embedding": [0.1, 0.2, 0.3]}],
            }),
        ]
        mock_client = Mock()
        mock_client.post.side_effect = responses
        mock_httpx_client.return_value.__enter__.return_value = mock_client

        with patch.object(service, "_encode_image", return_value="base64img"):
            result = service.encode_image("image.jpg")

        assert isinstance(result, np.ndarray)
        assert result.shape == (3,)
        # 应该是调用了两次 API
        assert mock_client.post.call_count == 2

    @patch("src.infrastructure.multimodal_service.httpx.Client")
    def test_get_text_embedding_success(self, mock_httpx_client, service):
        mock_client = Mock()
        mock_client.post.return_value = _make_mock_response(json_data={
            "data": [{"embedding": [0.1, 0.2, 0.3]}],
        })
        mock_httpx_client.return_value.__enter__.return_value = mock_client

        result = service._get_text_embedding("some text")
        assert isinstance(result, np.ndarray)
        assert result.shape == (3,)
        assert np.isclose(result[0], 0.1)

    def test_get_text_embedding_no_api_key(self, service):
        service._api_key = ""
        with pytest.raises(MultiModalError, match="未配置"):
            service._get_text_embedding("text")


# ================================================================
# GenericMultiModalService
# ================================================================


class TestGenericMultiModalService:
    @pytest.fixture
    def service(self):
        with patch("src.infrastructure.multimodal_service.settings") as ms:
            ms.multimodal.api_key = "sk-test"
            ms.multimodal.api_base = "https://api.generic.com/v1"
            ms.multimodal.model = "gpt-4o"
            return GenericMultiModalService(api_key="sk-test", api_base="https://api.generic.com/v1")

    @patch("src.infrastructure.multimodal_service.httpx.Client")
    def test_describe_image_success(self, mock_httpx_client, service):
        mock_client = Mock()
        mock_client.post.return_value = _make_mock_response()
        mock_httpx_client.return_value.__enter__.return_value = mock_client

        with patch.object(service, "_encode_image", return_value="base64img"):
            result = service.describe_image("image.jpg")

        assert result == "这是一张图片的描述"

    @patch("src.infrastructure.multimodal_service.httpx.Client")
    def test_chat_with_image(self, mock_httpx_client, service):
        mock_client = Mock()
        mock_client.post.return_value = _make_mock_response()
        mock_httpx_client.return_value.__enter__.return_value = mock_client

        with patch.object(service, "_encode_image", return_value="base64img"):
            result = service.chat_with_image("image.jpg", "图里有什么？")

        assert result == "这是一张图片的描述"

    def test_no_api_key_raises(self, service):
        service._api_key = ""
        with pytest.raises(MultiModalError, match="未配置"):
            service._call_generic_api("image.jpg", "prompt")

    @patch("src.infrastructure.multimodal_service.httpx.Client")
    def test_api_error(self, mock_httpx_client, service):
        mock_client = Mock()
        mock_client.post.return_value = _make_mock_response(status_code=400)
        mock_httpx_client.return_value.__enter__.return_value = mock_client

        with patch.object(service, "_encode_image", return_value="base64img"):
            with pytest.raises(MultiModalError):
                service.describe_image("image.jpg")

    def test_encode_image_not_found(self, service):
        with pytest.raises(MultiModalError, match="图片编码"):
            service._encode_image("/nonexistent.jpg")

    @patch("builtins.open", new_callable=mock_open, read_data=b"test")
    def test_encode_image_success(self, mock_file, service):
        result = service._encode_image("test.jpg")
        assert result == base64.b64encode(b"test").decode("utf-8")

    @patch("src.infrastructure.multimodal_service.httpx.Client")
    def test_generic_http_error(self, mock_httpx_client, service):
        mock_client = Mock()
        mock_client.post.side_effect = Exception("network error")
        mock_httpx_client.return_value.__enter__.return_value = mock_client

        with patch.object(service, "_encode_image", return_value="base64img"):
            with pytest.raises(MultiModalError, match="多模态 API"):
                service._call_generic_api("image.jpg", "prompt")


# ================================================================
# MultiModalServiceFactory
# ================================================================


class TestMultiModalServiceFactory:
    def test_create_openai(self):
        with patch("src.infrastructure.multimodal_service.settings") as ms:
            ms.multimodal.provider = "openai"
            ms.multimodal.api_key = "sk-test"
            ms.multimodal.api_base = "https://api.openai.com/v1"
            ms.multimodal.model = "gpt-4o"
            service = MultiModalServiceFactory.get_service(provider="openai")
            assert isinstance(service, OpenAIMultiModalService)

    def test_create_generic(self):
        with patch("src.infrastructure.multimodal_service.settings") as ms:
            ms.multimodal.provider = "generic"
            ms.multimodal.api_key = "sk-test"
            ms.multimodal.api_base = "https://api.example.com/v1"
            ms.multimodal.model = "model"
            service = MultiModalServiceFactory.get_service(provider="generic")
            assert isinstance(service, GenericMultiModalService)

    def test_unknown_provider(self):
        with patch("src.infrastructure.multimodal_service.settings") as ms:
            ms.multimodal.provider = "unknown"
            ms.multimodal.api_key = "sk-test"
            ms.multimodal.api_base = "https://api.openai.com/v1"
            ms.multimodal.model = "gpt-4o"
            with pytest.raises(MultiModalError, match="不支持"):
                MultiModalServiceFactory.get_service(provider="unknown")

    def test_provider_map(self):
        assert "openai" in MultiModalServiceFactory._PROVIDER_MAP
        assert "generic" in MultiModalServiceFactory._PROVIDER_MAP