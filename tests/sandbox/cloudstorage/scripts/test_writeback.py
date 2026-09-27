"""POC-3 pytest：cloudstorage JSON 原子写入 + 备份 + 回滚（沙盒验证）。

数据源：tests/sandbox/cloudstorage/data/cloud-storage-namespace-1.json
（复制自 leveldb/real_cloudstorage/，真实 78 有效收藏夹 + 67 已删除，哩米已核对）。
每个测试从数据源复制到 tmp_path 操作，不改动数据源。

运行：pytest tests/sandbox/cloudstorage/scripts/test_writeback.py -v
"""
import json
from pathlib import Path

import pytest

from writeback import (
    STORE_NAME,
    atomic_write,
    backup,
    file_sha256,
    read_store,
    rollback,
    serialize,
    soft_delete,
    upsert_collection,
)

DATA_SRC = Path(__file__).resolve().parent.parent / "data" / STORE_NAME


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    """复制数据源到临时目录作为工作副本。"""
    work = tmp_path / STORE_NAME
    work.write_bytes(Path(DATA_SRC).read_bytes())
    return work


def test_serialize_matches_steam_format() -> None:
    """Steam 序列化格式字节级匹配：无空格紧凑 + 中文 UTF-8 直写（2026-08-11 实测）。"""
    raw = Path(DATA_SRC).read_bytes()
    assert serialize(read_store(DATA_SRC)) == raw


def test_atomic_write_roundtrip(store: Path) -> None:
    """原子写入回读：内容可解析、与序列化字节一致、无 .tmp 残留。"""
    data = read_store(store)
    atomic_write(store, data)
    assert store.read_bytes() == serialize(data)
    assert not store.with_suffix(store.suffix + ".tmp").exists()


def test_atomic_write_failure_keeps_original(store: Path) -> None:
    """原子性：写入失败（非法数据）时原文件 sha256 不变。"""
    original = file_sha256(store)
    with pytest.raises(TypeError):
        atomic_write(store, [{"bad": object()}])  # 不可序列化 → 抛异常
    assert file_sha256(store) == original
    assert not store.with_suffix(store.suffix + ".tmp").exists()


def test_upsert_existing_collection(store: Path) -> None:
    """更新既有收藏夹：value 正确、version 递增、timestamp 更新。"""
    data = read_store(store)
    before = {e[0]: e[1] for e in data}["user-collections.favorite"]
    before_version = before["version"]
    before_ts = before["timestamp"]
    upsert_collection(data, "user-collections.favorite", "收藏夹", [1, 2, 3], [])
    atomic_write(store, data)

    reloaded = {e[0]: e[1] for e in read_store(store)}
    after = reloaded["user-collections.favorite"]
    assert json.loads(after["value"])["added"] == [1, 2, 3]
    assert after["version"] == str(int(before_version) + 1)
    assert after["timestamp"] >= before_ts
    assert after["conflictResolutionMethod"] == before["conflictResolutionMethod"]
    assert after["strMethodId"] == before["strMethodId"]


def test_upsert_new_collection(store: Path) -> None:
    """新增收藏夹：六字段结构完整，value 解析正确。"""
    data = read_store(store)
    key = "user-collections.uc-poc3test"
    upsert_collection(data, key, "POC3测试", [570, 730], [])
    atomic_write(store, data)

    entries = {e[0]: e[1] for e in read_store(store)}
    meta = entries[key]
    assert set(meta) == {"key", "timestamp", "value", "version",
                         "conflictResolutionMethod", "strMethodId"}
    inner = json.loads(meta["value"])
    assert inner == {"id": "uc-poc3test", "name": "POC3测试", "added": [570, 730], "removed": []}
    assert meta["version"] == "1"


def test_soft_delete_structure(store: Path) -> None:
    """软删除：is_deleted=true、无 value 字段、条目保留在顶层。"""
    data = read_store(store)
    key = "user-collections.uc-poc3test"
    upsert_collection(data, key, "待删除", [570], [])
    soft_delete(data, key)
    atomic_write(store, data)

    entries = {e[0]: e[1] for e in read_store(store)}
    meta = entries[key]
    assert meta["is_deleted"] is True
    assert "value" not in meta
    assert set(meta) == {"key", "timestamp", "is_deleted", "version"}


def test_backup_matches_original(store: Path, tmp_path: Path) -> None:
    """备份：时间戳目录 + 与源文件 sha256 一致。"""
    bpath = backup(store, tmp_path / ".backup")
    assert bpath.exists()
    assert bpath.parent.name.isdigit()  # 时间戳命名
    assert bpath.parent.parent.name == ".backup"
    assert file_sha256(bpath) == file_sha256(store)


def test_rollback_restores_exact_original(store: Path, tmp_path: Path) -> None:
    """完整回滚：多次写入（更新+新增+软删除）后回滚，与原始 sha256 完全一致。"""
    original = file_sha256(store)
    bpath = backup(store, tmp_path / ".backup")

    data = read_store(store)
    upsert_collection(data, "user-collections.favorite", "收藏夹", [1], [])
    upsert_collection(data, "user-collections.uc-poc3new", "新增", [2], [])
    soft_delete(data, "user-collections.uc-poc3new")
    atomic_write(store, data)
    assert file_sha256(store) != original  # 已改动

    rollback(store, bpath)
    assert file_sha256(store) == original


def test_store_still_valid_after_all_ops(store: Path, tmp_path: Path) -> None:
    """机制完整性：写入+回滚后 JSON 可解析且条目数恢复原始（520 个）。"""
    original_len = len(read_store(DATA_SRC))
    bpath = backup(store, tmp_path / ".backup")
    data = read_store(store)
    upsert_collection(data, "user-collections.favorite", "收藏夹", [99], [])
    upsert_collection(data, "user-collections.uc-extra", "额外", [100], [])
    atomic_write(store, data)
    assert len(read_store(store)) == original_len + 1  # 新增 1 条（favorite 是更新）
    rollback(store, bpath)
    assert len(read_store(store)) == original_len
