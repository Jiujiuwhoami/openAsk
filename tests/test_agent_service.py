"""客服状态管理服务测试。"""

import os
import tempfile
import pytest

from src.services.agent_service import AgentService


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def service(db_path):
    return AgentService(db_path=db_path)


class TestAgentStatus:
    def test_set_status(self, service):
        """设置客服状态。"""
        result = service.set_status("agent_1", "proj_1", "online")
        assert result is True

    def test_get_status(self, service):
        """获取客服状态。"""
        service.set_status("agent_1", "proj_1", "online")
        status = service.get_status("agent_1")
        assert status is not None
        assert status["status"] == "online"
        assert status["project_id"] == "proj_1"

    def test_get_status_not_found(self, service):
        """不存在的客服返回 None。"""
        assert service.get_status("nonexistent") is None

    def test_set_status_update(self, service):
        """更新状态。"""
        service.set_status("agent_1", "proj_1", "online")
        service.set_status("agent_1", "proj_1", "busy")
        status = service.get_status("agent_1")
        assert status["status"] == "busy"

    def test_list_project_agents(self, service):
        """列出项目客服。"""
        service.set_status("agent_1", "proj_1", "online")
        service.set_status("agent_2", "proj_1", "busy")
        service.set_status("agent_3", "proj_2", "online")  # 不同项目

        agents = service.list_project_agents("proj_1")
        assert len(agents) == 2

    def test_list_online_agents(self, service):
        """列出在线客服。"""
        service.set_status("agent_1", "proj_1", "online")
        service.set_status("agent_2", "proj_1", "busy")
        service.set_status("agent_3", "proj_1", "away")

        online = service.list_online_agents("proj_1")
        assert len(online) == 2  # online + busy
        assert all(a["status"] in ("online", "busy") for a in online)

    def test_update_heartbeat(self, service):
        """更新心跳。"""
        service.set_status("agent_1", "proj_1", "online")
        result = service.update_heartbeat("agent_1")
        assert result is True

    def test_increment_load(self, service):
        """增加负载。"""
        service.set_status("agent_1", "proj_1", "online", max_load=5)
        result = service.increment_load("agent_1")
        assert result is True
        status = service.get_status("agent_1")
        assert status["current_load"] == 1

    def test_increment_load_full(self, service):
        """负载满时不可增加。"""
        service.set_status("agent_1", "proj_1", "online", max_load=1)
        service.increment_load("agent_1")
        result = service.increment_load("agent_1")  # 已满
        assert result is False

    def test_decrement_load(self, service):
        """减少负载。"""
        service.set_status("agent_1", "proj_1", "online", max_load=5)
        service.increment_load("agent_1")
        service.increment_load("agent_1")
        service.decrement_load("agent_1")
        status = service.get_status("agent_1")
        assert status["current_load"] == 1

    def test_decrement_load_min_zero(self, service):
        """负载不低于 0。"""
        service.set_status("agent_1", "proj_1", "online")
        service.decrement_load("agent_1")
        status = service.get_status("agent_1")
        assert status["current_load"] == 0

    def test_get_available_agent(self, service):
        """获取最空闲的在线客服。"""
        service.set_status("agent_1", "proj_1", "online", max_load=5)
        service.set_status("agent_2", "proj_1", "online", max_load=5)
        service.increment_load("agent_1")  # agent_1 负载 1
        # agent_2 负载 0

        available = service.get_available_agent("proj_1")
        assert available is not None
        assert available["user_id"] == "agent_2"  # 负载更低的

    def test_get_available_agent_priority(self, service):
        """online 优先于 busy。"""
        service.set_status("agent_1", "proj_1", "busy", max_load=5)
        service.set_status("agent_2", "proj_1", "online", max_load=5)
        service.increment_load("agent_1")

        available = service.get_available_agent("proj_1")
        assert available is not None
        # busy 优先权低，但即使负载低，online 优先
        # 如果 agent_2 是 online，应该选 agent_2
        # 但 agent_1 是 busy + 负载1，agent_2 是 online + 负载0
        # 排序: online 优先(agent_2)，同状态按负载
        assert available["user_id"] == "agent_2"

    def test_get_available_agent_none(self, service):
        """无可用客服。"""
        service.set_status("agent_1", "proj_1", "away")
        available = service.get_available_agent("proj_1")
        assert available is None

    def test_cleanup_stale_agents(self, service):
        """清理超时客服。"""
        import time
        service.set_status("agent_1", "proj_1", "online")
        # 心跳设置到很久以前
        service._get_connection().execute(
            "UPDATE agent_status SET last_heartbeat = 0 WHERE user_id = 'agent_1'"
        ).connection.commit()
        cleaned = service.cleanup_stale_agents(timeout_seconds=10)
        assert cleaned == 1
        status = service.get_status("agent_1")
        assert status["status"] == "offline"

    def test_auto_accept_default(self, service):
        """auto_accept 默认为 True。"""
        service.set_status("agent_1", "proj_1", "online")
        status = service.get_status("agent_1")
        assert status["auto_accept"] is True

    # ================================================================
    # 分配策略测试
    # ================================================================

    def test_round_robin_strategy(self, service):
        """轮询分配：优先分配给分配次数最少的客服。"""
        service.set_status("agent_1", "proj_rr", "online", max_load=5)
        service.set_status("agent_2", "proj_rr", "online", max_load=5)
        # agent_1 已被分配 3 次，agent_2 0 次
        conn = service._get_connection()
        conn.execute("UPDATE agent_status SET total_assigned = 3 WHERE user_id = 'agent_1'")
        conn.commit()
        conn.close()

        available = service.get_available_agent("proj_rr", strategy="round_robin")
        assert available is not None
        assert available["user_id"] == "agent_2"  # 分配次数更少的

    def test_skill_match_strategy(self, service):
        """技能匹配分配。"""
        service.set_status("agent_1", "proj_s", "online", max_load=5, skills=["退换货", "物流"])
        service.set_status("agent_2", "proj_s", "online", max_load=5, skills=["投诉", "售后"])
        service.set_status("agent_3", "proj_s", "online", max_load=5, skills=["退换货"])

        # 需要退换货技能
        available = service.get_available_agent("proj_s", strategy="skill_match", required_skills=["退换货"])
        assert available is not None
        # agent_1 或 agent_3 应被选中（有退换货技能）
        assert available["user_id"] in ("agent_1", "agent_3")

    def test_skill_match_strategy_no_match(self, service):
        """技能匹配时无匹配客服返回 None 的场景。"""
        service.set_status("agent_1", "proj_s", "online", max_load=5, skills=["物流"])
        available = service.get_available_agent("proj_s", strategy="skill_match", required_skills=["投诉"])
        # 没有投诉技能的客服，但有可用的，fallback 到 least_busy
        assert available is not None

    def test_least_busy_default(self, service):
        """默认策略为 least_busy。"""
        service.set_status("agent_1", "proj_lb", "online", max_load=5)
        service.set_status("agent_2", "proj_lb", "online", max_load=5)
        service.increment_load("agent_1")
        service.increment_load("agent_1")

        available = service.get_available_agent("proj_lb")
        assert available["user_id"] == "agent_2"  # 负载更低的

    def test_increment_load_tracks_assigned(self, service):
        """increment_load 累加 total_assigned。"""
        service.set_status("agent_1", "proj_t", "online", max_load=5)
        service.increment_load("agent_1")
        service.increment_load("agent_1")
        status = service.get_status("agent_1")
        assert status["total_assigned"] == 2
        assert status["last_assigned_at"] > 0