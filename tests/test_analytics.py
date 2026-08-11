"""分析服务测试 — 日志、趋势、热门问题、满意度、缺口分析、反馈。"""

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
# 日志
# ================================================================


class TestLogs:
    def test_logs_empty(self, service):
        """无日志时返回空列表。"""
        result = service.get_logs("proj_empty")
        assert result["items"] == []
        assert result["total"] == 0

    def test_record_and_get_logs(self, service):
        """记录日志后可以查询到。"""
        # 模拟插入日志
        now = int(datetime.now(timezone.utc).timestamp())
        conn = service._get_connection()
        conn.execute(
            "INSERT INTO chat_logs (project_id, query, answer, sources, cache_hit, llm_used, prompt_tokens, completion_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("proj_test", "测试问题", "测试回答", "[]", 0, 1, 100, 50, now),
        )
        conn.commit()
        conn.close()

        result = service.get_logs("proj_test")
        assert result["total"] == 1
        assert result["items"][0]["query"] == "测试问题"

    def test_logs_pagination(self, service):
        """分页查询。"""
        now = int(datetime.now(timezone.utc).timestamp())
        conn = service._get_connection()
        for i in range(5):
            conn.execute(
                "INSERT INTO chat_logs (project_id, query, answer, sources, cache_hit, llm_used, prompt_tokens, completion_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("proj_page", f"问题{i}", f"回答{i}", "[]", 0, 1, 10, 5, now - i * 10),
            )
        conn.commit()
        conn.close()

        result = service.get_logs("proj_page", page=1, page_size=3)
        assert result["total"] == 5
        assert len(result["items"]) == 3

    def test_logs_search(self, service):
        """搜索日志。"""
        now = int(datetime.now(timezone.utc).timestamp())
        conn = service._get_connection()
        conn.execute("INSERT INTO chat_logs (project_id, query, answer, sources, cache_hit, llm_used, prompt_tokens, completion_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("proj_search", "退款流程", "退款需要...", "[]", 0, 1, 10, 5, now))
        conn.execute("INSERT INTO chat_logs (project_id, query, answer, sources, cache_hit, llm_used, prompt_tokens, completion_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("proj_search", "发货时间", "发货需要...", "[]", 0, 1, 10, 5, now))
        conn.commit()
        conn.close()

        result = service.get_logs("proj_search", search="退款")
        assert result["total"] == 1
        assert result["items"][0]["query"] == "退款流程"


# ================================================================
# 趋势
# ================================================================


class TestTrends:
    def test_trends_empty(self, service):
        """无数据时返回空列表。"""
        trends = service.get_trends("proj_empty", days=7)
        assert trends == []

    def test_trends_with_data(self, service):
        """有数据时返回趋势。"""
        now = int(datetime.now(timezone.utc).timestamp())
        conn = service._get_connection()
        for i in range(3):
            conn.execute(
                "INSERT INTO chat_logs (project_id, query, answer, sources, cache_hit, llm_used, prompt_tokens, completion_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("proj_trend", f"问题{i}", "回答", "[]", 0, 1, 10, 5, now - 86400 * i),
            )
        conn.commit()
        conn.close()

        trends = service.get_trends("proj_trend", days=7)
        assert len(trends) > 0
        assert trends[0]["calls"] > 0


# ================================================================
# 热门问题
# ================================================================


class TestTopQuestions:
    def test_top_questions_empty(self, service):
        """无数据时返回空列表。"""
        assert service.get_top_questions("proj_empty") == []

    def test_top_questions_with_data(self, service):
        """返回按频率排序的问题。"""
        now = int(datetime.now(timezone.utc).timestamp())
        conn = service._get_connection()
        for i in range(5):
            conn.execute(
                "INSERT INTO chat_logs (project_id, query, answer, sources, cache_hit, llm_used, prompt_tokens, completion_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("proj_top", "如何退款", "退款流程", "[]", 0, 1, 10, 5, now - i),
            )
        for i in range(2):
            conn.execute(
                "INSERT INTO chat_logs (project_id, query, answer, sources, cache_hit, llm_used, prompt_tokens, completion_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("proj_top", "发货时间", "发货流程", "[]", 0, 1, 10, 5, now - i),
            )
        conn.commit()
        conn.close()

        top = service.get_top_questions("proj_top", limit=10, days=7)
        assert len(top) == 2
        assert top[0]["query"] == "如何退款"
        assert top[0]["count"] == 5


# ================================================================
# 满意度
# ================================================================


class TestSatisfaction:
    def test_satisfaction_empty(self, service):
        """无反馈时返回 0。"""
        sat = service.get_satisfaction("proj_empty")
        assert sat["total"] == 0
        assert sat["satisfaction_rate"] == 0.0

    def test_satisfaction_with_data(self, service):
        """计算满意度。"""
        now = int(datetime.now(timezone.utc).timestamp())
        conn = service._get_connection()
        conn.execute(
            "INSERT INTO chat_logs (project_id, query, answer, sources, cache_hit, llm_used, prompt_tokens, completion_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("proj_sat", "问题", "回答", "[]", 0, 1, 10, 5, now),
        )
        log_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO feedback (log_id, project_id, rating, created_at) VALUES (?, ?, ?, ?)",
            (log_id, "proj_sat", "good", now),
        )
        conn.execute(
            "INSERT INTO feedback (log_id, project_id, rating, created_at) VALUES (?, ?, ?, ?)",
            (log_id, "proj_sat", "good", now),
        )
        conn.execute(
            "INSERT INTO feedback (log_id, project_id, rating, created_at) VALUES (?, ?, ?, ?)",
            (log_id, "proj_sat", "bad", now),
        )
        conn.commit()
        conn.close()

        sat = service.get_satisfaction("proj_sat", days=7)
        assert sat["total"] == 3
        assert sat["good"] == 2
        assert sat["bad"] == 1
        assert sat["satisfaction_rate"] == 66.7  # 2/3 * 100


# ================================================================
# 缺口分析
# ================================================================


class TestGaps:
    def test_gaps_empty(self, service):
        """无缺口时返回空列表。"""
        gaps = service.get_gaps("proj_empty")
        assert gaps["total"] == 0

    def test_gaps_no_source(self, service):
        """无来源文档的问题被识别为缺口。"""
        now = int(datetime.now(timezone.utc).timestamp())
        conn = service._get_connection()
        conn.execute(
            "INSERT INTO chat_logs (project_id, query, answer, sources, cache_hit, llm_used, prompt_tokens, completion_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("proj_gap", "答不上的问题", "抱歉我不知道", "[]", 0, 1, 10, 5, now),
        )
        conn.execute(
            "INSERT INTO chat_logs (project_id, query, answer, sources, cache_hit, llm_used, prompt_tokens, completion_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("proj_gap", "答不上的问题", "抱歉我不知道", "[]", 0, 1, 10, 5, now - 100),
        )
        conn.execute(
            "INSERT INTO chat_logs (project_id, query, answer, sources, cache_hit, llm_used, prompt_tokens, completion_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("proj_gap", "能答上的问题", "正确答案", '[{"doc_id":"d1"}]', 0, 1, 10, 5, now),
        )
        conn.commit()
        conn.close()

        gaps = service.get_gaps("proj_gap", days=7)
        assert gaps["total"] >= 1
        gap_queries = [g["query"] for g in gaps["items"]]
        assert "答不上的问题" in gap_queries
        assert "能答上的问题" not in gap_queries

    def test_gaps_bad_feedback(self, service):
        """差评问题被识别为缺口。"""
        now = int(datetime.now(timezone.utc).timestamp())
        conn = service._get_connection()
        conn.execute(
            "INSERT INTO chat_logs (project_id, query, answer, sources, cache_hit, llm_used, prompt_tokens, completion_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("proj_bad", "不满意的问题", "回答", '[{"doc_id":"d1"}]', 0, 1, 10, 5, now),
        )
        log_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO feedback (log_id, project_id, rating, created_at) VALUES (?, ?, ?, ?)",
            (log_id, "proj_bad", "bad", now),
        )
        conn.commit()
        conn.close()

        gaps = service.get_gaps("proj_bad", days=7)
        assert gaps["total"] >= 1


# ================================================================
# 反馈
# ================================================================


class TestCsat:
    """CSAT 满意度评价测试。"""

    def test_record_csat(self, service):
        req_id = service.record_csat(
            conversation_id="conv_1", project_id="proj_1", rating=5,
            tags=["解决了我问题", "回复很快"], feedback="非常满意",
        )
        assert req_id > 0

    def test_csat_stats_empty(self, service):
        stats = service.get_csat_stats("proj_empty")
        assert stats["total"] == 0

    def test_csat_stats(self, service):
        service.record_csat("conv_1", "proj_1", 5)
        service.record_csat("conv_2", "proj_1", 4)
        service.record_csat("conv_3", "proj_1", 2)

        stats = service.get_csat_stats("proj_1")
        assert stats["total"] == 3
        assert stats["avg_rating"] > 0

    def test_csat_distribution(self, service):
        service.record_csat("conv_1", "proj_1", 5)
        service.record_csat("conv_2", "proj_1", 5)
        service.record_csat("conv_3", "proj_1", 1)

        dist = service.get_csat_distribution("proj_1")
        total = sum(d["count"] for d in dist)
        assert total == 3

    def test_list_csat(self, service):
        service.record_csat("conv_1", "proj_1", 5, agent_id="agent_1", feedback="好")
        result = service.list_csat("proj_1")
        assert result["total"] == 1
        assert result["items"][0]["rating"] == 5
        assert result["items"][0]["agent_id"] == "agent_1"


class TestAgentPerformance:
    """客服绩效统计测试。"""

    def test_empty(self, service):
        result = service.get_agent_performance("proj_empty")
        assert result["total"] == 0

    def test_with_data(self, service):
        # 创建会话并指派给 agent
        from src.services.conversation_service import ConversationService
        conv_service = ConversationService()
        conv = conv_service.create_conversation("proj_agent", "测试")
        conv_service.update_status(conv.conversation_id, "agent", "agent_1")
        conv_service.add_message(conv.conversation_id, "agent", "客服回复1")
        conv_service.add_message(conv.conversation_id, "agent", "客服回复2")

        # 记录 CSAT
        service.record_csat(conv.conversation_id, "proj_agent", 5, agent_id="agent_1")

        result = service.get_agent_performance("proj_agent")
        assert result["total"] >= 1
        agent = result["items"][0]
        assert agent["agent_id"] == "agent_1"
        assert agent["conversations"] >= 1
        assert agent["messages_sent"] >= 2
        assert agent["csat_total"] >= 1
        assert agent["csat_avg"] > 0


class TestFeedback:
    def test_record_feedback(self, service):
        """记录反馈。"""
        now = int(datetime.now(timezone.utc).timestamp())
        conn = service._get_connection()
        conn.execute(
            "INSERT INTO chat_logs (project_id, query, answer, sources, cache_hit, llm_used, prompt_tokens, completion_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("proj_fb", "问题", "回答", "[]", 0, 1, 10, 5, now),
        )
        log_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        service.record_feedback(log_id, "proj_fb", "good")
        sat = service.get_satisfaction("proj_fb", days=7)
        assert sat["total"] == 1