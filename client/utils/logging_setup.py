"""loguru 日志配置（CLAUDE.md：技术调试日志分级，%APPDATA%/KuJi/logs/，10MB 轮转保留 7 天）。

用户操作日志（操作历史）独立存 SQLite，与本模块无关。
"""
import os
import sys
from pathlib import Path

from loguru import logger

LOGS_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "KuJi" / "logs"


def setup_logging(level: str = "INFO") -> None:
    """初始化 loguru：控制台（INFO）+ all.log（DEBUG）+ error.log（ERROR）。"""
    logger.remove()
    logger.add(sys.stderr, level=level)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger.add(
        LOGS_DIR / "all.log",
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
    )
    logger.add(
        LOGS_DIR / "error.log",
        level="ERROR",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
    )
