"""审核与版本回滚服务测试 — 版本保存、审核流程、状态查询。"""

import os
import tempfile

import pytest

from src.services.review_service import ReviewService


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def service(db_path):
    return ReviewService(db_path=db_path)


# ================================================================
# 版本保存与查询
# ================================================================


class TestSaveVersion:
    def test_save_first_version(self, service):
        """保存第一个版本返回版本号 1。"""
        v = service.save_version("doc_1", "proj_1", "标题", "内容")
        assert v == 1

    def test_save_increments_version(self, service):
        """同一文档多次保存版本号递增。"""
        service.save_version("doc_1", "proj_1", "标题", "v1")
        v2 = service.save_version("doc_1", "proj_1", "标题", "v2")
        v3 = service.save_version("doc_1", "proj_1", "标题", "v3")
        assert v2 == 2
        assert v3 == 3

    def test_versions_isolated_by_project(self, service):
        """不同项目的同一 doc_id 版本号独立。"""
        service.save_version("doc_1", "proj_a", "标题", "v1")
        v = service.save_version("doc_1", "proj_b", "标题", "v1")
        assert v == 1

    def test_save_with_tags(self, service):
        """保存带标签的版本。"""
        service.save_version("doc_1", "proj_1", "标题", "内容", tags=["a", "b"])
        versions = service.get_versions("doc_1", "proj_1")
        assert len(versions) == 1


class TestGetVersions:
    def test_get_versions_empty(self, service):
        """无版本时返回空列表。"""
        assert service.get_versions("doc_1", "proj_1") == []

    def test_get_versions_ordered_desc(self, service):
        """版本历史按版本号倒序。"""
        service.save_version("doc_1", "proj_1", "标题", "v1")
        service.save_version("doc_1", "proj_1", "标题", "v2")
        versions = service.get_versions("doc_1", "proj_1")
        assert [v["version"] for v in versions] == [2, 1]

    def test_get_versions_meta_fields(self, service):
        """版本列表包含 id/version/title/status/created_at。"""
        service.save_version("doc_1", "proj_1", "标题", "v1")
        versions = service.get_versions("doc_1", "proj_1")
        v = versions[0]
        assert set(v.keys()) == {"id", "version", "title", "status", "created_at"}

    def test_get_version_content(self, service):
        """获取指定版本完整内容。"""
        service.save_version("doc_1", "proj_1", "标题", "内容", tags=["x"])
        v = service.get_version("doc_1", "proj_1", 1)
        assert v["title"] == "标题"
        assert v["content"] == "内容"
        assert v["tags"] == ["x"]
        assert v["version"] == 1

    def test_get_version_not_found(self, service):
        """不存在的版本返回 None。"""
        assert service.get_version("doc_1", "proj_1", 99) is None


# ================================================================
# 审核流程
# ================================================================


class TestReviewWorkflow:
    def test_submit_review_sets_pending(self, service):
        """提交审核后状态变为 pending。"""
        service.save_version("doc_1", "proj_1", "标题", "内容")
        service.submit_review("doc_1", "proj_1")
        assert service.get_status("doc_1", "proj_1") == "pending"

    def test_approve_sets_approved(self, service):
        """审核通过后状态变为 approved。"""
        service.save_version("doc_1", "proj_1", "标题", "内容")
        service.submit_review("doc_1", "proj_1")
        service.approve("doc_1", "proj_1")
        assert service.get_status("doc_1", "proj_1") == "approved"

    def test_reject_returns_to_draft(self, service):
        """审核拒绝后回到 draft。"""
        service.save_version("doc_1", "proj_1", "标题", "内容")
        service.submit_review("doc_1", "proj_1")
        service.reject("doc_1", "proj_1")
        assert service.get_status("doc_1", "proj_1") == "draft"

    def test_approve_without_submit_has_no_effect(self, service):
        """未提交审核时 approve 无效（状态仍为 draft）。"""
        service.save_version("doc_1", "proj_1", "标题", "内容")
        service.approve("doc_1", "proj_1")
        assert service.get_status("doc_1", "proj_1") == "draft"

    def test_full_workflow(self, service):
        """完整流程：draft → pending → approved。"""
        service.save_version("doc_1", "proj_1", "标题", "内容")
        assert service.get_status("doc_1", "proj_1") == "draft"
        service.submit_review("doc_1", "proj_1")
        assert service.get_status("doc_1", "proj_1") == "pending"
        service.approve("doc_1", "proj_1")
        assert service.get_status("doc_1", "proj_1") == "approved"

    def test_status_unknown_doc_returns_draft(self, service):
        """不存在的文档返回 draft。"""
        assert service.get_status("doc_404", "proj_404") == "draft"