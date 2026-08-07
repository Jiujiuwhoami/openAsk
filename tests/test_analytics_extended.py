"""分析服务扩展测试 — 日志导出、转人工请求。"""

import os
import tempfile
import pytest
from datetime import datetime, timezone

from src.services.analytics_service import AnalyticsService


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def service(db_path):
    return AnalyticsService(db_path=db_path)


# ================================================================
# 日志导出
# ================================================================


class TestExportLogs:
    def _insert_log(self, service, project_id, query, answer, sources="[]", cache_hit=0, created_at=None):
        now = created_at or int(datetime.now(timezone.utc).timestamp())
        conn = service._get_connection()
        conn.execute(
            "INSERT INTO chat_logs (project_id, query, answer, sources, cache_hit, llm_used, prompt_tokens, completion_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (project_id, query, answer, sources, cache_hit, 1, 10, 5, now),
        )
        conn.commit()
        conn.close()

    def test_export_csv(self, service):
        self._insert_log(service, "proj_exp", "问题1", "回答1")
        csv_output = service.export_logs("proj_exp", format="csv")
        assert "问题1" in csv_output
        assert "回答1" in csv_output
        assert "ID" in csv_output
        assert csv_output.count("\n") >= 2

    def test_export_json(self, service):
        self._insert_log(service, "proj_exp", "问题1", "回答1")
        json_output = service.export_logs("proj_exp", format="json")
        assert "问题1" in json_output
        assert "回答1" in json_output
        assert json_output.startswith("[")

    def test_export_empty(self, service):
        csv_output = service.export_logs("proj_empty", format="csv")
        # 即使没有数据，也应返回带表头的 CSV
        assert csv_output.strip() != ""

    def test_export_with_date_filter(self, service):
        now = int(datetime.now(timezone.utc).timestamp())
        # 3天前的日志
        old = now - 86400 * 10
        self._insert_log(service, "proj_date", "旧问题", "旧回答", created_at=old)
        self._insert_log(service, "proj_date", "新问题", "新回答", created_at=now)

        # 只导出最近 7 天
        result = service.export_logs("proj_date", format="csv", start_date=now - 86400 * 7)
        assert "新问题" in result
        assert "旧问题" not in result


# ================================================================
# 转人工请求
# ================================================================


class TestHandoff:
    def test_record_handoff(self, service):
        request_id = service.record_handoff(
            project_id="proj_1",
            query="怎么退货",
        )
        assert request_id > 0

    def test_record_handoff_full(self, service):
        request_id = service.record_handoff(
            project_id="proj_1",
            query="退货流程",
            conversation_id="conv_abc",
            contact_email="user@example.com",
            contact_phone="13800000000",
            note="加急处理",
        )
        assert request_id > 0

    def test_list_handoffs_empty(self, service):
        result = service.list_handoffs("proj_1")
        assert result["total"] == 0
        assert result["items"] == []

    def test_list_handoffs(self, service):
        service.record_handoff("proj_1", "问题1")
        service.record_handoff("proj_1", "问题2")
        service.record_handoff("proj_1", "问题3")

        result = service.list_handoffs("proj_1")
        assert result["total"] == 3
        assert len(result["items"]) == 3

    def test_list_handoffs_pagination(self, service):
        for i in range(5):
            service.record_handoff("proj_1", f"问题{i}")

        result = service.list_handoffs("proj_1", page=1, page_size=2)
        assert result["total"] == 5
        assert len(result["items"]) == 2

    def test_list_handoffs_filter_by_status(self, service):
        service.record_handoff("proj_1", "问题1")
        req_id = service.record_handoff("proj_1", "问题2")
        service.resolve_handoff(req_id)

        result = service.list_handoffs("proj_1", status="pending")
        assert result["total"] == 1

        result = service.list_handoffs("proj_1", status="resolved")
        assert result["total"] == 1

    def test_resolve_handoff(self, service):
        req_id = service.record_handoff("proj_1", "问题")
        result = service.resolve_handoff(req_id)
        assert result is True

        # 验证已解决
        handoffs = service.list_handoffs("proj_1", status="resolved")
        assert handoffs["total"] == 1
        assert handoffs["items"][0]["id"] == req_id

    def test_resolve_handoff_not_found(self, service):
        result = service.resolve_handoff(99999)
        assert result is False

    def test_handoff_fields(self, service):
        service.record_handoff(
            "proj_1", "问题", conversation_id="conv_1",
            contact_email="a@b.com", contact_phone="138", note="备注",
        )
        result = service.list_handoffs("proj_1")
        item = result["items"][0]
        assert item["query"] == "问题"
        assert item["conversation_id"] == "conv_1"
        assert item["contact_email"] == "a@b.com"
        assert item["contact_phone"] == "138"
        assert item["note"] == "备注"
        assert item["status"] == "pending"
        assert item["created_at"] > 0
        assert item["resolved_at"] == 0