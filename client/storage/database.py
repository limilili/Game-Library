"""SQLite 管理：engine/session 工厂 + 初始化（Alembic 升级到最新）。

数据库位置：%APPDATA%/KuJi/kuji.db（Settings.resolved_db_path）。

WAL 模式（2026-08-13，P2）：读不阻塞写、写不阻塞读——扫描/UI/封面下载
多线程并发读写不再报 database is locked；busy_timeout=5000 锁等待 5s。
⚠️ WAL 产生 kuji.db-wal / kuji.db-shm 伴随文件——直接拷贝 kuji.db 做备份前
需先 PRAGMA wal_checkpoint(TRUNCATE)（或连同 wal 文件一起备份），否则丢尾部数据。
"""
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from client.config import get_settings

_engine = None
_session_factory: sessionmaker[Session] | None = None


def _set_pragma(dbapi_conn, _record) -> None:
    """每个新连接：WAL 并发模式 + 锁等待 5s（幂等，连接级设置）。

    不用 PRAGMA foreign_keys=ON——games 主键是 (appid, id) 复合，
    game_classifications 外键引用单列 appid 不满足 SQLite「被引用列须唯一」，
    FK=ON 会导致外键 mismatch 报错（2026-08-15 误引入致 140 测试挂）；
    一致性由应用层软删除（del_flag）保证。
    """
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def get_engine():
    """惰性创建 engine（线程安全由调用方保证；UI 主线程使用）。"""
    global _engine
    if _engine is None:
        db_path: Path = get_settings().resolved_db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{db_path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
        event.listen(_engine, "connect", _set_pragma)
    return _engine


def get_session() -> Session:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory()


def init_db() -> None:
    """初始化数据库：Alembic 升级到 head（首次即建全部表）。"""
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent.parent
    db_path = get_settings().resolved_db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "client" / "storage" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    command.upgrade(cfg, "head")
