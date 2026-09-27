"""POC-2 交叉验证：cloudstorage JSON 解析结果 = 真实收藏夹数据。

数据源：沙盒副本 tests/sandbox/leveldb/real_cloudstorage/cloud-storage-namespace-1.json
（复制自真实 Steam userdata/12345678，哩米已核对 78 个收藏夹与客户端一致）

运行：pytest tests/sandbox/leveldb/scripts/test_cloudstorage.py -v
"""
import json
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parent.parent
CLOUD_JSON = SANDBOX / "real_cloudstorage" / "cloud-storage-namespace-1.json"


def load_collections():
    """解析 user-collections：返回 (全部条目, 有效列表, 已删除列表)。"""
    data = json.loads(CLOUD_JSON.read_text(encoding="utf-8"))
    entries = [e for e in data if e[0].startswith("user-collections")]
    active, deleted = [], []
    for key, meta in entries:
        if meta.get("is_deleted"):
            deleted.append(key)
        elif "value" in meta:
            active.append((key, json.loads(meta["value"])))
        else:
            raise AssertionError(f"异常条目（非删除且无 value）: {key}")
    return entries, active, deleted


def find_by_name(active, name: str):
    for key, inner in active:
        if inner.get("name") == name:
            return key, inner
    raise AssertionError(f"未找到收藏夹: {name}")


@pytest.fixture(scope="module")
def parsed():
    entries, active, deleted = load_collections()
    return {"entries": entries, "active": active, "deleted": deleted}


def test_top_level_entries(parsed) -> None:
    """顶层 JSON 共 520 个条目（云同步键值对）。"""
    data = json.loads(CLOUD_JSON.read_text(encoding="utf-8"))
    assert len(data) == 520


def test_collection_counts(parsed) -> None:
    """145 个 user-collections：78 有效 + 67 已删除。"""
    assert len(parsed["entries"]) == 145
    assert len(parsed["active"]) == 78
    assert len(parsed["deleted"]) == 67


def test_favorite(parsed) -> None:
    """收藏夹（favorite）：24 个 appid，名称为「收藏夹」。"""
    key, inner = find_by_name(parsed["active"], "收藏夹")
    assert key == "user-collections.favorite"
    assert len(inner["added"]) == 24
    assert inner["removed"] == []


def test_hidden(parsed) -> None:
    """已隐藏（hidden）：17 个 appid。"""
    key, inner = find_by_name(parsed["active"], "已隐藏")
    assert key == "user-collections.hidden"
    assert len(inner["added"]) == 17


def test_kuji_prefix_count(parsed) -> None:
    """库集_ 前缀分类共 42 个（库集分类体系已真实写入）。"""
    kuji = [k for k, i in parsed["active"] if i.get("name", "").startswith("库集_")]
    assert len(kuji) == 42


def test_kuji_fps(parsed) -> None:
    """库集_FPS（第一人称射击）：42 个 appid。"""
    _, inner = find_by_name(parsed["active"], "库集_FPS（第一人称射击）")
    assert len(inner["added"]) == 42
    assert 1085660 in inner["added"]  # Destiny 2


def test_kuji_souls(parsed) -> None:
    """库集_硬核动作（魂系）：36 个 appid。"""
    _, inner = find_by_name(parsed["active"], "库集_硬核动作（魂系）")
    assert len(inner["added"]) == 36


def test_kuji_fei(parsed) -> None:
    """库集_废萌：25 个 appid。"""
    _, inner = find_by_name(parsed["active"], "库集_废萌")
    assert len(inner["added"]) == 25


def test_emoji_collection(parsed) -> None:
    """emoji 收藏夹名：🎮漫威蜘蛛侠系列 3 个 appid。"""
    _, inner = find_by_name(parsed["active"], "🎮漫威蜘蛛侠系列")
    assert inner["added"] == [1817070, 1817190, 2651280]


def test_removed_shelf(parsed) -> None:
    """已下架：1 个 appid [460870]。"""
    _, inner = find_by_name(parsed["active"], "已下架")
    assert inner["added"] == [460870]


def test_deleted_entries_structure(parsed) -> None:
    """已删除条目：is_deleted=true 且无 value 字段。"""
    data = json.loads(CLOUD_JSON.read_text(encoding="utf-8"))
    deleted_meta = [e[1] for e in data if e[0].startswith("user-collections") and e[1].get("is_deleted")]
    assert len(deleted_meta) == len(parsed["deleted"])
    assert all("value" not in m for m in deleted_meta)


def test_collection_schema(parsed) -> None:
    """有效收藏夹结构：id/name/added/removed 字段齐全。"""
    for key, inner in parsed["active"]:
        assert set(inner) >= {"id", "name", "added", "removed"}, key
        assert isinstance(inner["added"], list)
        assert isinstance(inner["removed"], list)
