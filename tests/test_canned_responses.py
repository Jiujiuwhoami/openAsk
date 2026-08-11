"""话术库服务测试。"""

import os
import tempfile
import pytest

from src.services.canned_response_service import CannedResponseService


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def service(db_path):
    return CannedResponseService(db_path=db_path)


class TestCannedResponses:
    def test_create(self, service):
        rid = service.create("proj_1", "user_1", "欢迎语", "您好，欢迎咨询！", category="问候", shortcut="/hi")
        assert rid > 0

    def test_get_by_id(self, service):
        rid = service.create("proj_1", "user_1", "欢迎语", "您好，欢迎咨询！")
        item = service.get_by_id(rid)
        assert item is not None
        assert item["title"] == "欢迎语"
        assert item["content"] == "您好，欢迎咨询！"

    def test_list(self, service):
        service.create("proj_1", "user_1", "话术1", "内容1", category="问候")
        service.create("proj_1", "user_1", "话术2", "内容2", category="物流")
        result = service.list("proj_1")
        assert result["total"] == 2

    def test_list_filter_by_category(self, service):
        service.create("proj_1", "user_1", "话术1", "内容1", category="问候")
        service.create("proj_1", "user_1", "话术2", "内容2", category="物流")
        result = service.list("proj_1", category="问候")
        assert result["total"] == 1
        assert result["items"][0]["category"] == "问候"

    def test_list_project_separation(self, service):
        service.create("proj_1", "user_1", "话术1", "内容1")
        service.create("proj_2", "user_1", "话术2", "内容2")
        result = service.list("proj_1")
        assert result["total"] == 1

    def test_update(self, service):
        rid = service.create("proj_1", "user_1", "旧标题", "旧内容")
        updated = service.update(rid, title="新标题", content="新内容")
        assert updated is True
        item = service.get_by_id(rid)
        assert item["title"] == "新标题"
        assert item["content"] == "新内容"

    def test_update_invalid_field(self, service):
        rid = service.create("proj_1", "user_1", "标题", "内容")
        updated = service.update(rid, invalid_field="test")
        assert updated is False

    def test_delete(self, service):
        rid = service.create("proj_1", "user_1", "标题", "内容")
        deleted = service.delete(rid)
        assert deleted is True
        assert service.get_by_id(rid) is None

    def test_delete_not_found(self, service):
        assert service.delete(99999) is False

    def test_global_vs_personal(self, service):
        service.create("proj_1", "user_1", "全局话术", "内容", is_global=True)
        service.create("proj_1", "user_2", "个人话术", "内容", is_global=False)
        # user_1 看到全局 + 自己的（没有 user_2 的个人话术）
        result = service.list("proj_1", user_id="user_1")
        assert result["total"] == 1  # 只有全局
        # user_2 看到全局 + 自己的
        result = service.list("proj_1", user_id="user_2")
        assert result["total"] == 2

    def test_list_categories(self, service):
        service.create("proj_1", "user_1", "话术1", "内容1", category="问候")
        service.create("proj_1", "user_1", "话术2", "内容2", category="物流")
        cats = service.list_categories("proj_1")
        assert "问候" in cats
        assert "物流" in cats

    def test_shortcut(self, service):
        rid = service.create("proj_1", "user_1", "欢迎语", "您好！", shortcut="/hi")
        item = service.get_by_id(rid)
        assert item["shortcut"] == "/hi"