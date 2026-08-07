"""动态限流器测试 — 解析、滑动窗口、内存模式、Redis 降级。"""

import time
from unittest.mock import Mock, patch, MagicMock

import pytest
from fastapi import Request

from src.utils.dynamic_limiter import (
    _parse_rate,
    _SlidingWindow,
    ProjectLimiter,
    _redis_key,
)


# ================================================================
# _parse_rate
# ================================================================


class TestParseRate:
    def test_standard_minute(self):
        assert _parse_rate("60/minute") == (60, 60)

    def test_standard_second(self):
        assert _parse_rate("10/second") == (10, 1)

    def test_standard_hour(self):
        assert _parse_rate("1000/hour") == (1000, 3600)

    def test_standard_day(self):
        assert _parse_rate("5000/day") == (5000, 86400)

    def test_no_slash(self):
        assert _parse_rate("60") == (60, 60)

    def test_invalid_count(self):
        assert _parse_rate("abc/minute") == (60, 60)

    def test_unknown_unit(self):
        assert _parse_rate("60/year") == (60, 60)  # 未知单位回退到分钟

    def test_case_insensitive(self):
        assert _parse_rate("100/MINUTE") == (100, 60)

    def test_lowercase(self):
        assert _parse_rate("5/Second") == (5, 1)


# ================================================================
# _SlidingWindow
# ================================================================


class TestSlidingWindow:
    def test_new_window_allows(self):
        w = _SlidingWindow(10, 60)
        assert w.is_allowed() is True

    def test_remaining_at_max(self):
        w = _SlidingWindow(10, 60)
        assert w.remaining() == 10

    def test_remaining_after_request(self):
        w = _SlidingWindow(10, 60)
        w.is_allowed()
        assert w.remaining() == 9

    def test_exhausted(self):
        w = _SlidingWindow(3, 60)
        assert w.is_allowed() is True
        assert w.is_allowed() is True
        assert w.is_allowed() is True
        assert w.is_allowed() is False

    def test_window_expires(self):
        """窗口过期后重新允许（使用快过期的时间戳）。"""
        w = _SlidingWindow(1, 0.01)
        assert w.is_allowed() is True
        assert w.is_allowed() is False  # 马上超限
        time.sleep(0.02)
        assert w.is_allowed() is True  # 窗口已过期

    def test_remaining_zero_when_exhausted(self):
        w = _SlidingWindow(2, 60)
        w.is_allowed()
        w.is_allowed()
        assert w.remaining() == 0

    def test_thread_safety(self):
        """并发请求不破坏内部状态。"""
        import threading
        w = _SlidingWindow(100, 60)
        errors = []

        def hammer():
            try:
                for _ in range(20):
                    w.is_allowed()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=hammer) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        # 最多允许 100 次，5*20=100 次请求，刚好用完
        assert w.remaining() == 0


# ================================================================
# _redis_key
# ================================================================


class TestRedisKey:
    def test_redis_key_format(self):
        assert _redis_key("project:proj_1") == "rl:project:proj_1"

    def test_redis_key_empty(self):
        assert _redis_key("") == "rl:"


# ================================================================
# MemoryBackend ProjectLimiter
# ================================================================


class TestProjectLimiterMemory:
    @pytest.fixture
    def limiter(self):
        with patch("src.utils.dynamic_limiter.settings") as mock_settings:
            mock_settings.rate_limit.storage_uri = "memory://"
            limiter = ProjectLimiter()
            limiter._redis = None  # 确保内存模式
            yield limiter

    @pytest.fixture
    def mock_request(self):
        req = Mock(spec=Request)
        req.headers = {}
        req.client = Mock()
        req.client.host = "192.168.1.1"
        return req

    def test_is_allowed_without_project(self, limiter, mock_request):
        assert limiter.is_allowed(mock_request) is True

    def test_is_allowed_with_project(self, limiter, mock_request):
        project = Mock()
        project.project_id = "proj_1"
        project.rate_limit_per_user = "60/minute"
        assert limiter.is_allowed(mock_request, project) is True

    def test_remaining_with_project(self, limiter, mock_request):
        project = Mock()
        project.project_id = "proj_1"
        project.rate_limit_per_user = "10/minute"
        assert limiter.get_remaining(mock_request, project) == 10
        limiter.is_allowed(mock_request, project)
        assert limiter.get_remaining(mock_request, project) == 9

    def test_get_remaining_unlimited(self, limiter, mock_request):
        """未限流的 key 返回 max_req。"""
        project = Mock()
        project.project_id = "proj_new"
        project.rate_limit_per_user = "5/minute"
        assert limiter.get_remaining(mock_request, project) == 5

    def test_key_by_x_forwarded_for(self, limiter):
        req = Mock(spec=Request)
        req.headers = {"x-forwarded-for": "10.0.0.1, 10.0.0.2"}
        req.client = Mock()
        req.client.host = "ignored"
        key = limiter._get_key(req)
        assert key == "ip:10.0.0.1"

    def test_key_by_client_host(self, limiter, mock_request):
        key = limiter._get_key(mock_request)
        assert key == "ip:192.168.1.1"

    def test_rate_limit_respected(self, limiter, mock_request):
        """限流 3/min 的 project 第 4 次被拒绝。"""
        project = Mock()
        project.project_id = "proj_limited"
        project.rate_limit_per_user = "3/minute"
        assert limiter.is_allowed(mock_request, project) is True
        assert limiter.is_allowed(mock_request, project) is True
        assert limiter.is_allowed(mock_request, project) is True
        assert limiter.is_allowed(mock_request, project) is False

    def test_different_projects_isolated(self, limiter, mock_request):
        """不同项目的限流独立。"""
        p1 = Mock()
        p1.project_id = "proj_a"
        p1.rate_limit_per_user = "2/minute"
        p2 = Mock()
        p2.project_id = "proj_b"
        p2.rate_limit_per_user = "2/minute"

        limiter.is_allowed(mock_request, p1)
        limiter.is_allowed(mock_request, p1)
        assert limiter.is_allowed(mock_request, p1) is False  # p1 超限
        assert limiter.is_allowed(mock_request, p2) is True  # p2 还有额度

    def test_dynamic_rate_update(self, limiter, mock_request):
        """运行时更新限流配置，旧窗口被替换。"""
        project = Mock()
        project.project_id = "proj_dynamic"
        project.rate_limit_per_user = "2/minute"

        limiter.is_allowed(mock_request, project)
        limiter.is_allowed(mock_request, project)
        assert limiter.is_allowed(mock_request, project) is False

        # 更新限流值
        project.rate_limit_per_user = "5/minute"
        assert limiter.is_allowed(mock_request, project) is True  # 新窗口


# ================================================================
# Redis 降级
# ================================================================


class TestProjectLimiterRedisFallback:
    def test_redis_connection_failure_logs_warning(self):
        """Redis 连接失败时降级到内存，不崩溃。"""
        import redis as _redis_module
        with patch("src.utils.dynamic_limiter.settings") as mock_settings:
            mock_settings.rate_limit.storage_uri = "redis://localhost:16379"
            # 模拟 Redis.from_url 本身抛出异常（连接失败）
            with patch.object(_redis_module.Redis, "from_url") as mock_from_url:
                mock_from_url.side_effect = Exception("connection refused")
                limiter = ProjectLimiter()
                assert limiter._redis is None  # 降级到内存

    def test_redis_operation_failure_falls_back(self, limiter, mock_request):
        """Redis 操作失败时降级到内存。"""
        project = Mock()
        project.project_id = "proj_fallback"
        project.rate_limit_per_user = "60/minute"

        # 模拟 Redis 存在但操作失败
        limiter._redis = Mock()
        limiter._lua_allow = Mock(side_effect=Exception("redis error"))
        # 缓存中还没有该 key，所以 is_allowed 应该走内存降级
        result = limiter.is_allowed(mock_request, project)
        assert result is True  # 降级成功

    @pytest.fixture
    def limiter(self):
        with patch("src.utils.dynamic_limiter.settings") as mock_settings:
            mock_settings.rate_limit.storage_uri = "memory://"
            limiter = ProjectLimiter()
            limiter._redis = None
            yield limiter

    @pytest.fixture
    def mock_request(self):
        req = Mock(spec=Request)
        req.headers = {}
        req.client = Mock()
        req.client.host = "192.168.1.1"
        return req


# ================================================================
# 全局实例
# ================================================================


class TestProjectLimiterInstance:
    def test_global_instance_exists(self):
        from src.utils.dynamic_limiter import project_limiter
        assert isinstance(project_limiter, ProjectLimiter)