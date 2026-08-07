"""敏感词过滤服务。

支持：
  - 内置敏感词库
  - 自定义敏感词（项目级别）
  - 提问时检查
  - 回答时过滤（替换为 ***）
"""

import os
import re
import sqlite3
import threading
from typing import List, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

# 内置敏感词库（示例，生产环境需完善）
BUILTIN_SENSITIVE_WORDS: List[str] = [
    # 这里仅作示例，实际部署时需要根据业务场景配置
]

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS sensitive_words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    word TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(project_id, word)
);
CREATE INDEX IF NOT EXISTS idx_sw_project ON sensitive_words(project_id);
"""


class SensitiveFilterService:
    """敏感词过滤服务。"""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or "data/sensitive.db"
        self._lock = threading.RLock()
        self._ensure_db()
        # 缓存编译后的正则
        self._cache: dict = {}
        self._cache_lock = threading.RLock()

    def _ensure_db(self) -> None:
        dir_path = os.path.dirname(self._db_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        with self._lock:
            conn = self._get_connection()
            try:
                conn.executescript(_INIT_SQL)
                conn.commit()
            finally:
                conn.close()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _build_pattern(self, words: List[str]) -> Optional[re.Pattern]:
        """构建编译后的正则表达式。"""
        if not words:
            return None
        pattern = "|".join(re.escape(w) for w in words)
        return re.compile(pattern, re.IGNORECASE)

    def _get_words(self, project_id: str) -> List[str]:
        """获取项目的所有敏感词（内置 + 自定义）。"""
        words = list(BUILTIN_SENSITIVE_WORDS)
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT word FROM sensitive_words WHERE project_id = ?", (project_id,)
            ).fetchall()
            words.extend(r["word"] for r in rows)
        finally:
            conn.close()
        return words

    def _get_pattern(self, project_id: str) -> Optional[re.Pattern]:
        """获取编译后的正则（带缓存）。"""
        with self._cache_lock:
            if project_id not in self._cache:
                words = self._get_words(project_id)
                self._cache[project_id] = self._build_pattern(words)
            return self._cache[project_id]

    def clear_cache(self, project_id: Optional[str] = None) -> None:
        """清除缓存。"""
        with self._cache_lock:
            if project_id:
                self._cache.pop(project_id, None)
            else:
                self._cache.clear()

    def contains_sensitive(self, text: str, project_id: str) -> bool:
        """检查文本是否包含敏感词。"""
        pattern = self._get_pattern(project_id)
        if pattern is None:
            return False
        return bool(pattern.search(text))

    def filter(self, text: str, project_id: str, replacement: str = "***") -> str:
        """过滤敏感词，替换为指定字符串。"""
        pattern = self._get_pattern(project_id)
        if pattern is None:
            return text
        return pattern.sub(replacement, text)

    # ---- 自定义敏感词管理 ----

    def add_word(self, project_id: str, word: str) -> None:
        """添加自定义敏感词。"""
        import time
        now = int(time.time())
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO sensitive_words (project_id, word, created_at) VALUES (?, ?, ?)",
                    (project_id, word, now),
                )
                conn.commit()
            finally:
                conn.close()
        self.clear_cache(project_id)
        logger.info(f"敏感词已添加: {project_id} → {word}")

    def remove_word(self, project_id: str, word: str) -> None:
        """移除自定义敏感词。"""
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    "DELETE FROM sensitive_words WHERE project_id = ? AND word = ?",
                    (project_id, word),
                )
                conn.commit()
            finally:
                conn.close()
        self.clear_cache(project_id)
        logger.info(f"敏感词已移除: {project_id} → {word}")

    def list_words(self, project_id: str) -> List[str]:
        """列出项目的自定义敏感词。"""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT word FROM sensitive_words WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
            return [r["word"] for r in rows]
        finally:
            conn.close()