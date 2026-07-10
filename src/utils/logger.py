"""统一日志配置。"""

import logging
import logging.handlers
import sys
from typing import Optional

from src.utils.config import settings


LOG_MAX_BYTES = 5 * 1024 * 1024  # 5MB
LOG_BACKUP_COUNT = 3  # 保留 3 个备份


def setup_logger(
    name: str,
    level: Optional[str] = None,
    log_file: Optional[str] = None,
    log_format: Optional[str] = None,
    max_bytes: int = LOG_MAX_BYTES,
    backup_count: int = LOG_BACKUP_COUNT,
) -> logging.Logger:
    """创建并配置 Logger，同时输出到控制台和文件（支持轮转）。

    日志轮转策略：
    - 单文件最大 5MB
    - 保留最多 3 个备份文件
    - 文件名格式：app.log, app.log.1, app.log.2, app.log.3
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, (level or settings.logging.level).upper(), logging.DEBUG))

    if logger.handlers:
        return logger

    formatter = logging.Formatter(log_format or settings.logging.format)

    # 控制台 handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件 handler（带轮转）
    file_path = log_file or settings.logging.file
    if file_path:
        file_handler = logging.handlers.RotatingFileHandler(
            file_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """获取已配置的 Logger，未配置则自动初始化。"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        return setup_logger(name)
    return logger
