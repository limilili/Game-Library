"""WAL 并发模式测试（P2，2026-08-13）。

验证：
- journal_mode=wal（PRAGMA 每个连接幂等设置）
- busy_timeout=5000（锁等待而非立即报错）
- 多线程并发写+读不报 database is locked（WAL 核心收益）
"""
import threading

from client.models import Game
from client.storage.database import get_engine, get_session


def _journal_mode() -> str:
    conn = get_engine().raw_connection()
    try:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()


def _busy_timeout() -> int:
    conn = get_engine().raw_connection()
    try:
        return conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()


def test_journal_mode_is_wal():
    assert _journal_mode() == "wal"


def test_busy_timeout_set():
    assert _busy_timeout() == 5000


def test_concurrent_read_write_no_lock():
    """多线程并发写+读，30 轮迭代，不应出现 database is locked。"""
    errors: list[Exception] = []

    def writer():
        try:
            for i in range(30):
                s = get_session()
                try:
                    s.add(Game(appid=f"wal-w{i}", name=f"写{i}"))
                    s.commit()
                finally:
                    s.close()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def reader():
        try:
            for _ in range(30):
                s = get_session()
                try:
                    list(s.query(Game).limit(50).all())
                finally:
                    s.close()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [
        threading.Thread(target=writer),
        threading.Thread(target=reader),
        threading.Thread(target=reader),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发读写出现异常: {errors}"


def test_wal_checkpoint_works():
    """备份前 checkpoint（TRUNCATE）可安全执行。"""
    conn = get_engine().raw_connection()
    try:
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        assert row is not None
    finally:
        conn.close()
