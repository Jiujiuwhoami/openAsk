"""敏感词过滤服务测试。"""

import os
import tempfile

import pytest

from src.services.sensitive_filter import SensitiveFilterService, BUILTIN_SENSITIVE_WORDS


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def service(db_path):
    return SensitiveFilterService(db_path=db_path)


class TestFilter:
    def test_contains_sensitive_no_words(self, service):
        assert service.contains_sensitive("正常文本", "proj_1") is False

    def test_contains_sensitive_after_add(self, service):
        service.add_word("proj_1", "敏感词")
        assert service.contains_sensitive("这是一个敏感词测试", "proj_1") is True
        assert service.contains_sensitive("正常文本", "proj_1") is False

    def test_filter_replaces(self, service):
        service.add_word("proj_1", "坏词")
        result = service.filter("这句话包含坏词", "proj_1")
        assert result == "这句话包含***"

    def test_filter_no_match(self, service):
        result = service.filter("安全文本", "proj_1")
        assert result == "安全文本"

    def test_filter_custom_replacement(self, service):
        service.add_word("proj_1", "secret")
        result = service.filter("this is secret", "proj_1", replacement="[REDACTED]")
        assert result == "this is [REDACTED]"

    def test_case_insensitive(self, service):
        service.add_word("proj_1", "bad")
        assert service.contains_sensitive("BAD", "proj_1") is True
        assert service.contains_sensitive("Bad", "proj_1") is True


class TestWordManagement:
    def test_add_word(self, service):
        service.add_word("proj_1", "词1")
        words = service.list_words("proj_1")
        assert "词1" in words

    def test_add_duplicate(self, service):
        service.add_word("proj_1", "词1")
        service.add_word("proj_1", "词1")  # 不应报错
        words = service.list_words("proj_1")
        assert len(words) == 1

    def test_remove_word(self, service):
        service.add_word("proj_1", "词1")
        service.remove_word("proj_1", "词1")
        words = service.list_words("proj_1")
        assert "词1" not in words

    def test_list_words_empty(self, service):
        assert service.list_words("proj_1") == []

    def test_list_words_multiple(self, service):
        service.add_word("proj_1", "词1")
        service.add_word("proj_1", "词2")
        words = service.list_words("proj_1")
        assert len(words) == 2


class TestProjectIsolation:
    def test_words_isolated_by_project(self, service):
        service.add_word("proj_a", "词A")
        service.add_word("proj_b", "词B")
        assert "词A" in service.list_words("proj_a")
        assert "词B" not in service.list_words("proj_a")
        assert "词B" in service.list_words("proj_b")

    def test_filter_isolated_by_project(self, service):
        service.add_word("proj_a", "词A")
        assert service.contains_sensitive("词A", "proj_a") is True
        assert service.contains_sensitive("词A", "proj_b") is False


class TestCache:
    def test_clear_cache_project(self, service):
        service.add_word("proj_1", "词1")
        assert service.contains_sensitive("词1", "proj_1") is True
        service.clear_cache("proj_1")
        # 缓存清理后仍能正常工作
        assert service.contains_sensitive("词1", "proj_1") is True

    def test_clear_cache_all(self, service):
        service.add_word("proj_1", "词1")
        service.add_word("proj_2", "词2")
        service.clear_cache()
        assert service.contains_sensitive("词1", "proj_1") is True
        assert service.contains_sensitive("词2", "proj_2") is True