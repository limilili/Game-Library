"""pytest 全局 fixture：隔离 APPDATA（不污染真实 %APPDATA%/KuJi）+ 初始化测试数据库。

⚠️ 2026-08-12 事故：session fixture 设置 APPDATA 太晚（模块 import 之后），
若其失败则 _clean_db 可能清空真实库（games 1181 被误清）。修复：
1. pytest_configure（收集前）即设置 APPDATA——任何 import 都看到隔离路径
2. _clean_db 加保护：数据库不在隔离目录时拒绝执行
"""
import os

import pytest

_ISOLATED_APPDATA: str | None = None


def pytest_configure(config) -> None:
    """收集前设置 APPDATA（隔离真实用户配置/数据库）。"""
    global _ISOLATED_APPDATA
    import tempfile

    _ISOLATED_APPDATA = tempfile.mkdtemp(prefix="kuji-test-appdata-")
    os.environ["APPDATA"] = _ISOLATED_APPDATA
    from client.storage.database import init_db

    init_db()


@pytest.fixture(autouse=True)
def _clean_db() -> None:
    """每个测试前清空业务表；只允许操作隔离测试库（防御真实库被误清）。"""
    from client.config import get_settings
    from client.storage.database import get_session

    db_path = str(get_settings().resolved_db_path)
    if _ISOLATED_APPDATA not in db_path:  # 保护：非隔离库拒绝清理
        raise RuntimeError(
            f"拒绝清理非测试库: {db_path}（隔离目录 {_ISOLATED_APPDATA}）"
        )

    from client.models import (
        BackupRecord,
        Category,
        CategoryScheme,
        ClassificationRule,
        Game,
        GameClassification,
        MetadataCache,
        Tag,
    )

    session = get_session()
    try:
        for model in (
            GameClassification,
            ClassificationRule,
            Tag,
            Category,
            MetadataCache,
            BackupRecord,
            Game,
            CategoryScheme,
        ):
            session.query(model).delete(synchronize_session=False)
        session.commit()
    finally:
        session.close()
    yield
