"""商品导入服务测试 — CSV/JSON 解析、FAQ 模板。"""

import os
import tempfile
from unittest.mock import Mock, AsyncMock, patch

import pytest

from src.services.product_import import (
    ProductImportService,
    FAQTemplateService,
    FAQ_TEMPLATES,
)


@pytest.fixture
def import_service():
    return ProductImportService()


@pytest.fixture
def template_service():
    return FAQTemplateService()


# ================================================================
# CSV 解析
# ================================================================


class TestParseCsv:
    def test_parse_csv_with_chinese_headers(self, import_service):
        csv_content = "商品名称,商品描述,规格,价格,库存,标签\niPhone15,好手机,256GB,8999,100,手机,数码\n"
        products = import_service.parse_csv(csv_content)
        assert len(products) == 1
        assert products[0]["name"] == "iPhone15"
        assert products[0]["price"] == "8999"

    def test_parse_csv_with_english_headers(self, import_service):
        csv_content = "name,description,spec,price,stock,tags\nMacBook,笔记本,M3,12999,50,电脑\n"
        products = import_service.parse_csv(csv_content)
        assert len(products) == 1
        assert products[0]["name"] == "MacBook"
        assert products[0]["price"] == "12999"

    def test_parse_csv_multi_row(self, import_service):
        csv_content = "name,price\nA,10\nB,20\nC,30\n"
        products = import_service.parse_csv(csv_content)
        assert len(products) == 3

    def test_parse_csv_skip_empty_name(self, import_service):
        csv_content = "name,price\n,10\nB,20\n"
        products = import_service.parse_csv(csv_content)
        assert len(products) == 1
        assert products[0]["name"] == "B"

    def test_parse_csv_empty(self, import_service):
        assert import_service.parse_csv("") == []


# ================================================================
# JSON 解析
# ================================================================


class TestParseJson:
    def test_parse_json_list(self, import_service):
        data = '[{"name": "A", "price": "10"}, {"name": "B", "price": "20"}]'
        products = import_service.parse_json(data)
        assert len(products) == 2

    def test_parse_json_with_products_key(self, import_service):
        data = '{"products": [{"name": "A", "price": "10"}]}'
        products = import_service.parse_json(data)
        assert len(products) == 1

    def test_parse_json_empty_list(self, import_service):
        assert import_service.parse_json("[]") == []

    def test_parse_json_invalid(self, import_service):
        import json
        with pytest.raises(json.JSONDecodeError):
            import_service.parse_json("not json")


# ================================================================
# 商品转文档
# ================================================================


class TestProductToDocument:
    def test_full_product(self, import_service):
        doc = import_service.product_to_document({
            "name": "iPhone 15",
            "description": "旗舰手机",
            "spec": "256GB",
            "price": "8999",
            "stock": "100",
            "tags": "手机,数码",
        })
        assert doc["title"] == "iPhone 15"
        assert "旗舰手机" in doc["content"]
        assert "规格：256GB" in doc["content"]
        assert "价格：8999" in doc["content"]
        assert "库存：100" in doc["content"]
        assert "商品" in doc["tags"]
        assert "手机" in doc["tags"]

    def test_minimal_product(self, import_service):
        doc = import_service.product_to_document({
            "name": "测试商品",
        })
        assert doc["title"] == "测试商品"
        assert doc["content"] == ""
        assert doc["tags"] == ["商品"]

    def test_product_without_tags(self, import_service):
        doc = import_service.product_to_document({
            "name": "A",
            "description": "desc",
        })
        assert doc["tags"] == ["商品"]


# ================================================================
# 导入流程
# ================================================================


class TestImportProducts:
    def test_import_csv_success(self, import_service):
        """使用同步 mock 测试导入（import_products 内部创建独立事件循环）。"""
        import asyncio

        mock_knowledge = Mock()
        # 使用 asyncio.sleep(0, ...) 返回不受事件循环关联的协程
        def _create(*args, **kwargs):
            return asyncio.sleep(0, result="doc_1")
        mock_knowledge.create_document_from_text = _create

        result = import_service.import_products(
            file_content="name,price\nA,10\nB,20\n",
            filename="products.csv",
            knowledge_service=mock_knowledge,
            project_id="proj_1",
        )
        assert result["success"] == 2
        assert result["failed"] == 0

    def test_import_json_success(self, import_service):
        import asyncio

        mock_knowledge = Mock()
        def _create(*args, **kwargs):
            return asyncio.sleep(0, result="doc_1")
        mock_knowledge.create_document_from_text = _create

        result = import_service.import_products(
            file_content='[{"name": "A", "price": "10"}]',
            filename="products.json",
            knowledge_service=mock_knowledge,
            project_id="proj_1",
        )
        assert result["success"] == 1
        assert result["failed"] == 0

    @pytest.mark.asyncio
    async def test_import_unsupported_format(self, import_service):
        result = import_service.import_products(
            file_content="some data",
            filename="data.txt",
            knowledge_service=Mock(),
            project_id="proj_1",
        )
        assert result["success"] == 0
        assert result["failed"] == 0
        assert len(result["errors"]) == 1
        assert "不支持的文件格式" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_import_empty_data(self, import_service):
        result = import_service.import_products(
            file_content="name,price\n",
            filename="products.csv",
            knowledge_service=Mock(),
            project_id="proj_1",
        )
        # 只有标题行，没有数据
        assert result["success"] == 0
        assert result["failed"] == 0
        assert len(result["errors"]) == 1


# ================================================================
# FAQ 模板
# ================================================================


class TestFAQTemplateService:
    def test_list_templates(self, template_service):
        templates = template_service.list_templates()
        assert len(templates) >= 3
        ids = [t["id"] for t in templates]
        assert "return_policy" in ids
        assert "shipping" in ids
        assert "payment" in ids

    def test_list_templates_has_fields(self, template_service):
        templates = template_service.list_templates()
        for t in templates:
            assert "id" in t
            assert "name" in t
            assert "description" in t
            assert "document_count" in t

    def test_get_template_exists(self, template_service):
        tmpl = template_service.get_template("return_policy")
        assert tmpl is not None
        assert tmpl["name"] == "退换货流程"
        assert len(tmpl["documents"]) >= 2

    def test_get_template_not_found(self, template_service):
        assert template_service.get_template("nonexistent") is None

    @pytest.mark.asyncio
    async def test_apply_template_success(self, template_service):
        mock_knowledge = Mock()
        async def fake_create(*args, **kwargs):
            return "doc_1"
        mock_knowledge.create_document_from_text = fake_create

        result = await template_service.apply_template(
            "return_policy",
            knowledge_service=mock_knowledge,
            project_id="proj_1",
        )
        assert result["success"] >= 2
        assert result["failed"] == 0

    @pytest.mark.asyncio
    async def test_apply_template_not_found(self, template_service):
        result = await template_service.apply_template(
            "nonexistent",
            knowledge_service=Mock(),
            project_id="proj_1",
        )
        assert result["success"] == 0
        assert result["failed"] == 0
        assert len(result["errors"]) == 1


# ================================================================
# FAQ_TEMPLATES 数据完整性
# ================================================================


class TestFAQTemplatesData:
    def test_all_templates_have_required_keys(self):
        required = {"name", "description", "documents"}
        for tid, tmpl in FAQ_TEMPLATES.items():
            for key in required:
                assert key in tmpl, f"{tid} 缺少 {key}"

    def test_all_documents_have_required_keys(self):
        for tid, tmpl in FAQ_TEMPLATES.items():
            for doc in tmpl["documents"]:
                assert "title" in doc, f"{tid} 文档缺少 title"
                assert "content" in doc, f"{tid} 文档缺少 content"
                assert "tags" in doc, f"{tid} 文档缺少 tags"