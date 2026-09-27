"""M3 写回铁律测试：双备份/差异分析/原子写入/验证/回滚（沙盒副本）。

运行：pytest tests/test_m3_writeback.py -v
"""
import json
import os
from pathlib import Path

import pytest

from client.core import vdf_ops
from client.core.vdf_ops import (
    STORE_NAME,
    atomic_write,
    backup_store,
    build_diff,
    file_sha256,
    read_store,
    serialize,
    write_categories,
)
from client.models import BackupRecord
from client.storage.database import get_session

# 真实数据副本（沙盒，POC-3 已验证）：78 有效收藏夹 + 67 已删除
REAL_COPY = (
    Path(__file__).resolve().parent.parent
    / "tests" / "sandbox" / "cloudstorage" / "data" / STORE_NAME
)


@pytest.fixture()
def fake_steam(tmp_path: Path) -> Path:
    """构造 Steam 目录：cloudstorage 镜像 JSON（用真实副本）+ sharedconfig.vdf。"""
    cs = tmp_path / "userdata" / "12345" / "config" / "cloudstorage"
    cs.mkdir(parents=True)
    store = cs / STORE_NAME
    if REAL_COPY.exists():
        store.write_bytes(REAL_COPY.read_bytes())
    else:
        store.write_text("[]", encoding="utf-8")
    config_dir = cs.parent
    (config_dir / "sharedconfig.vdf").write_text('"UserRoamingConfigStore"\n{\n}\n', encoding="utf-8")
    return tmp_path


def test_serialize_matches_steam_format() -> None:
    """Steam 序列化格式字节级匹配（POC-3 回归）。"""
    if not REAL_COPY.exists():
        pytest.skip("公开仓库未包含真实 sandbox 数据副本")
    raw = REAL_COPY.read_bytes()
    assert serialize(read_store(REAL_COPY)) == raw


def test_atomic_write_roundtrip(fake_steam: Path, tmp_path: Path) -> None:
    """原子写入回读 + 失败不破坏原文件。"""
    store = vdf_ops.get_store_path(fake_steam, "12345")
    data = read_store(store)
    atomic_write(store, data)
    assert store.read_bytes() == serialize(data)
    assert not store.with_suffix(store.suffix + ".tmp").exists()

    original = file_sha256(store)
    with pytest.raises(TypeError):
        atomic_write(store, [{"bad": object()}])
    assert file_sha256(store) == original


def test_backup_double(monkeypatch, fake_steam: Path, tmp_path: Path) -> None:
    """M3.1 双备份：JSON + sharedconfig + 哈希 + BackupRecord 入库。"""
    monkeypatch.setattr(vdf_ops, "backup_dir", lambda: tmp_path / ".backup")
    record = backup_store(fake_steam, "12345", notes="测试")
    assert record.sha256 == file_sha256(vdf_ops.get_store_path(fake_steam, "12345"))
    ts_dir = tmp_path / ".backup" / record.created_at_ts
    assert (ts_dir / STORE_NAME).exists()
    assert (ts_dir / "sharedconfig.vdf").exists()
    session = get_session()
    assert session.query(BackupRecord).count() == 1
    session.close()


def test_build_diff() -> None:
    """M3.2 差异分析：新增/删除/保留。"""
    current = {"730": ["FPS"], "570": ["MOBA", "电竞"]}
    target = {"730": ["FPS", "竞技射击"], "570": ["电竞"], "999": ["新游戏"]}
    diff = build_diff(current, target)
    assert diff["add"] == {"730": ["竞技射击"], "999": ["新游戏"]}
    assert diff["remove"] == {"570": ["MOBA"]}
    assert diff["keep"] == {"730": ["FPS"], "570": ["电竞"]}


def test_write_and_verify(fake_steam: Path) -> None:
    """M3.8 写回 + 验证：目标分类全部写入且回读通过（库集前缀全量同步）。"""
    store = vdf_ops.get_store_path(fake_steam, "12345")
    target = {"1245620": ["库集_硬核动作（魂系）"], "730": ["库集_FPS（第一人称射击）"]}
    result = write_categories(fake_steam, "12345", target)
    assert result["verified"] is True
    # 回读确认
    reloaded = read_store(store)
    names = set()
    for key, meta in reloaded:
        if key.startswith("user-collections") and isinstance(meta, dict) and "value" in meta:
            inner = json.loads(meta["value"])
            if inner.get("name", "").startswith("库集_"):
                names.add(inner["name"])
    assert "库集_硬核动作（魂系）" in names and "库集_FPS（第一人称射击）" in names


def test_l2_removal_sync(fake_steam: Path) -> None:
    """审查 L2：写回支持删除——第二次写入移除 appid，Steam 收藏夹同步删除。"""
    store = vdf_ops.get_store_path(fake_steam, "12345")
    # 第一次：两游戏入 FPS
    write_categories(fake_steam, "12345", {
        "730": ["库集_FPS"], "570": ["库集_FPS"]})
    # 第二次：570 移除 → 全量同步后库集_FPS 只剩 730
    result = write_categories(fake_steam, "12345", {"730": ["库集_FPS"]})
    assert result["verified"] is True
    reloaded = read_store(store)
    for key, meta in reloaded:
        if key.startswith("user-collections") and "value" in meta:
            inner = json.loads(meta["value"])
            if inner.get("name") == "库集_FPS":
                assert inner["added"] == [730]  # 570 已被删除（int——客户端要求）
                break
    else:
        raise AssertionError("未找到库集_FPS")


def test_l2_legacy_collection_migrated(fake_steam: Path) -> None:
    """审查 L2：旧「库集_」收藏夹不在目标 → 软删除（42 旧类自动迁移清理）。"""
    store = vdf_ops.get_store_path(fake_steam, "12345")
    data = read_store(store)
    # 预置旧收藏夹
    import uuid
    legacy_key = f"user-collections.uc-{uuid.uuid4().hex[:8]}"
    data.append([legacy_key, {
        "key": legacy_key, "timestamp": 1,
        "value": json.dumps({"id": "legacy", "name": "库集_旧分类",
                             "added": [999], "removed": []}, ensure_ascii=False),
        "version": "1", "conflictResolutionMethod": "custom",
        "strMethodId": "union-collections"}])
    # 用户自有收藏夹（非库集前缀）
    user_key = f"user-collections.uc-{uuid.uuid4().hex[:8]}"
    data.append([user_key, {
        "key": user_key, "timestamp": 1,
        "value": json.dumps({"id": "mine", "name": "收藏夹",
                             "added": [570], "removed": []}, ensure_ascii=False),
        "version": "1"}])
    atomic_write(store, data)

    result = write_categories(fake_steam, "12345", {"730": ["库集_新分类"]})
    assert result["verified"] is True
    assert result["deleted"] >= 1  # 旧库集收藏夹软删除（真实副本自带 42 旧类 → 43）
    reloaded = read_store(store)
    legacy = next(e for e in reloaded if e[0] == legacy_key)
    assert legacy[1].get("is_deleted") is True
    user = next(e for e in reloaded if e[0] == user_key)
    assert "value" in user[1]  # 用户自有收藏夹不受影响
    inner = json.loads(user[1]["value"])
    assert inner["added"] == [570]


def test_a3_structure_validation(fake_steam: Path) -> None:
    """审查 A3：写前结构快照校验——格式变更时拒绝写入（StoreFormatError）。"""
    from client.core.vdf_ops import StoreFormatError, validate_store_structure

    # 正常结构通过
    validate_store_structure(read_store(vdf_ops.get_store_path(fake_steam, "12345")))
    # 异常结构拒绝
    with pytest.raises(StoreFormatError):
        validate_store_structure({"not": "a list"})
    with pytest.raises(StoreFormatError):
        validate_store_structure([["key", {"key": "mismatch"}]])
    with pytest.raises(StoreFormatError):
        validate_store_structure([["user-collections.x",
                                   {"key": "user-collections.x", "value": "not-json"}]])
    with pytest.raises(StoreFormatError):
        validate_store_structure([["user-collections.x",
                                   {"key": "user-collections.x",
                                    "value": '{"added": "not-list"}'}]])


def test_write_rollback_restores(monkeypatch, fake_steam: Path, tmp_path: Path) -> None:
    """M3.3 回滚：写入后从备份恢复，哈希与备份一致。"""
    monkeypatch.setattr(vdf_ops, "backup_dir", lambda: tmp_path / ".backup")
    store = vdf_ops.get_store_path(fake_steam, "12345")
    original_hash = file_sha256(store)
    record = backup_store(fake_steam, "12345")
    write_categories(fake_steam, "12345", {"1245620": ["新分类"]})
    assert file_sha256(store) != original_hash
    vdf_ops.rollback(record, store)
    assert file_sha256(store) == original_hash


class TestAppidIntWriteback:
    """2026-08-13 端到端验收发现：Steam 客户端忽略字符串 appid 收藏夹。
    added 必须写 int（v0 收藏夹 int 客户端可见，str 不可见）。"""

    def test_added_written_as_int(self, fake_steam: Path):
        result = write_categories(fake_steam, "12345", {"1245620": ["库集_测试夹"]})
        assert result["verified"]
        data = read_store(vdf_ops.get_store_path(fake_steam, "12345"))
        for key, meta in data:
            if not key.startswith("user-collections") or "value" not in meta:
                continue
            inner = json.loads(meta["value"])
            if inner.get("name") == "库集_测试夹":
                assert inner["added"] == [1245620], "added 应为 int（客户端可见性）"
                return
        raise AssertionError("未找到库集_测试夹条目")

    def test_verify_int_store_str_target(self, fake_steam: Path):
        """文件内 int（Steam 规范化）与目标 str 集合比较仍通过。"""
        result = write_categories(fake_steam, "12345", {"730": ["库集_测试夹"]})
        assert result["verified"]
        # 模拟 Steam 规范化后的存储：added 已是 int
        result2 = write_categories(fake_steam, "12345", {"730": ["库集_测试夹"]})
        assert result2["verified"]

    def test_removed_full_sync(self, fake_steam: Path):
        """全量同步：从 added 移除即删除（union 集合语义，v0/Steam 自身
        收藏夹 removed 均为空）；440 不应残留。"""
        write_categories(fake_steam, "12345",
                         {"730": ["库集_测试夹"], "440": ["库集_测试夹"]})
        result = write_categories(fake_steam, "12345", {"730": ["库集_测试夹"]})
        assert result["verified"]
        data = read_store(vdf_ops.get_store_path(fake_steam, "12345"))
        for key, meta in data:
            if not key.startswith("user-collections") or "value" not in meta:
                continue
            inner = json.loads(meta["value"])
            if inner.get("name") == "库集_测试夹":
                assert inner["added"] == [730], "全量同步后 added 应只剩 730"
                assert 440 not in inner["added"]
                return
        raise AssertionError("未找到库集_测试夹条目")
