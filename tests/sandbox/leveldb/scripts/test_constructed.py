"""POC-2 步骤3：沙盒 LevelDB 读取验证（pytest 断言）。

验证目标（POC-2 备选路线成功标准）：
- 从沙盒 LevelDB 读取预知分类 key-value
- 按 appid 提取标签列表，与预知数据（build_constructed_db.py 的 KNOWN_DATA）完全一致
- 提取逻辑只匹配 Tags 路径，忽略 Apps/SteamCloud 等其他块

用法：
    pytest tests/sandbox/leveldb/scripts/test_constructed.py -v
    python  tests/sandbox/leveldb/scripts/test_constructed.py   # 打印 appid→标签对照表
"""
import sys
from pathlib import Path

import plyvel
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_constructed_db import DB_PATH, KNOWN_DATA  # noqa: E402

TAGS_PATH = "UserRoamingConfigStore/Software/Valve/Steam/Users/{account}/Tags/"


def extract_tags(db: plyvel.DB, account_id: str) -> dict[int, list[str]]:
    """按 appid 提取标签列表（只匹配 `.../Tags/<appid>/TagName` 路径键）。"""
    result: dict[int, list[str]] = {}
    prefix = TAGS_PATH.format(account=account_id).encode("utf-8")
    for key, value in db:
        if not key.startswith(prefix):
            continue
        parts = key[len(prefix):].decode("utf-8").split("/")
        if len(parts) == 2 and parts[1] == "TagName":
            appid = int(parts[0])
            result.setdefault(appid, []).append(value.decode("utf-8"))
    return result


@pytest.fixture(scope="module")
def db() -> plyvel.DB:
    """只读打开构造库（独立打开，验证真实读库）。"""
    db = plyvel.DB(DB_PATH.as_posix(), create_if_missing=False)
    yield db
    db.close()


def test_db_opens_readonly(db: plyvel.DB) -> None:
    """库可只读打开，且非空。"""
    count = sum(1 for _ in db)
    assert count == 8, f"期望 8 个键，实际 {count}"


def test_account1_tags_match(db: plyvel.DB) -> None:
    """账号 1：按 appid 提取的标签列表与预知数据完全一致。"""
    expected = {int(k): v for k, v in KNOWN_DATA["1234567890"].items()}
    assert extract_tags(db, "1234567890") == expected


def test_account2_tags_match(db: plyvel.DB) -> None:
    """账号 2：多账号场景，同样逐项一致。"""
    expected = {int(k): v for k, v in KNOWN_DATA["2987654321"].items()}
    assert extract_tags(db, "2987654321") == expected


def test_ignores_non_tag_keys(db: plyvel.DB) -> None:
    """干扰键（Apps/OverlayAppEnable、SteamCloud/Enabled）不会被提取为标签。"""
    tags = extract_tags(db, "1234567890")
    assert len(tags) == 4, f"账号 1 应只有 4 个 appid，实际 {tags}"
    assert "OverlayAppEnable" not in tags and "SteamCloud" not in tags
    # 无标签 appid 不得出现
    assert 570 in tags and 730 in tags


def test_unknown_appid_returns_empty(db: plyvel.DB) -> None:
    """未知 appid 返回空列表。"""
    tags = extract_tags(db, "1234567890")
    assert tags.get(999999) is None


def test_chinese_tag_utf8(db: plyvel.DB) -> None:
    """中文标签 UTF-8 往返一致（魂系 = Elden Ring 的预知标签）。"""
    tags = extract_tags(db, "1234567890")
    assert tags[1245620] == ["魂系"]


def test_per_account_isolation(db: plyvel.DB) -> None:
    """账号隔离：账号 1 不应包含账号 2 的标签。"""
    tags1 = extract_tags(db, "1234567890")
    tags2 = extract_tags(db, "2987654321")
    assert tags1[570] == ["MOBA"] and tags2[570] == ["电竞"]
    assert 1085660 not in tags2


if __name__ == "__main__":
    print("=== appid → 标签列表 对照表（沙盒构造库） ===")
    with plyvel.DB(DB_PATH.as_posix(), create_if_missing=False) as db:
        for account in sorted(KNOWN_DATA):
            print(f"[账号 {account}]")
            for appid, tags in sorted(extract_tags(db, account).items()):
                print(f"  {appid} -> {tags}")
