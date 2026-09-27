"""从 appinfo.vdf 补全预置元数据：name/developer/publisher/type（P2 复杂部分）。

背景（2026-08-13）：
- appinfo.vdf 不含 store_tags 社区标签（实测 3650 条 0 个 section 有）；
  社区标签需另寻数据源（SteamSpy 403 / 商店页面网络不稳）。
- 但 appinfo.vdf 有 common.name / common.type / extended.developer / publisher——
  锁区游戏（Store API 抓取失败）可补名、补开发商、识别工具软件。

用法：
    python tools/build_appinfo_meta.py            # 补全本机 1181 款
    python tools/build_appinfo_meta.py --appids 730,1245620
"""
import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from client.core.appinfo_parser import parse_appinfo  # noqa: E402
from client.utils.steam_path import detect_steam_path  # noqa: E402

META_DB = ROOT / "client" / "resources" / "metadata_prebuilt.db"
APPINFO = detect_steam_path() / "appcache" / "appinfo.vdf"


def _library_appids() -> set[int]:
    from client.core.library_cache import get_local_library_appids
    from client.core.scanner import find_steamid32

    steamid32 = find_steamid32(detect_steam_path()) or ""
    return {int(a) for a in get_local_library_appids(detect_steam_path(), steamid32)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--appids", default="", help="只处理指定 appid（逗号分隔）")
    args = parser.parse_args()

    if args.appids:
        appids = {int(a) for a in args.appids.split(",") if a.strip()}
    else:
        appids = _library_appids()
    print(f"目标 {len(appids)} 款")

    parsed = parse_appinfo(APPINFO, appids)
    print(f"appinfo.vdf 命中 {len(parsed)} 款")

    conn = sqlite3.connect(META_DB)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS metadata_cache (
            appid TEXT PRIMARY KEY, name TEXT, developer TEXT, publisher TEXT,
            release_date TEXT, store_tags TEXT, genres TEXT,
            review_score INTEGER, review_count INTEGER,
            supported_languages TEXT, fetched_at TEXT, expires_at TEXT)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_type_override (
            appid TEXT PRIMARY KEY, app_type TEXT, source TEXT, updated_at TEXT)"""
    )
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    type_overrides = 0
    updated = inserted = 0
    for appid, tree in parsed.items():
        common = tree.get("common", {}) or {}
        extended = tree.get("extended", {}) or {}
        name = common.get("name") or ""
        dev = extended.get("developer") or ""
        pub = extended.get("publisher") or ""
        atype = common.get("type") or ""
        # common.type: Game / Tool / Beta / Config 等（appinfo 权威应用类型，
        # 比 Store API type 覆盖更全——锁区/下架游戏也有）
        if atype and atype.lower() != "game":
            conn.execute(
                "INSERT OR REPLACE INTO app_type_override (appid, app_type, source, updated_at) "
                "VALUES (?, ?, 'appinfo.vdf', ?)",
                (str(appid), atype, now))
            type_overrides += 1
        row = conn.execute(
            "SELECT name, developer, publisher FROM metadata_cache WHERE appid=?",
            (str(appid),)).fetchone()
        if row is None:
            if not name:
                continue
            conn.execute(
                "INSERT INTO metadata_cache (appid, name, developer, publisher, "
                "store_tags, genres, fetched_at, expires_at) VALUES (?,?,?,?,'[]','[]','','')",
                (str(appid), name, dev or None, pub or None))
            inserted += 1
            continue
        cur_name, cur_dev, cur_pub = row
        new_name, new_dev, new_pub = cur_name, cur_dev, cur_pub
        # 占位名/空名 → 补真名
        if not cur_name or cur_name.startswith("["):
            new_name = name
        # 锁区缺开发商 → 补
        if not cur_dev and dev:
            new_dev = dev
        if not cur_pub and pub:
            new_pub = pub
        if (new_name, new_dev, new_pub) != (cur_name, cur_dev, cur_pub):
            conn.execute(
                "UPDATE metadata_cache SET name=?, developer=?, publisher=? WHERE appid=?",
                (new_name, new_dev, new_pub, str(appid)))
            updated += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM metadata_cache").fetchone()[0]
    conn.close()
    print(f"完成: 补名/补开发商 {updated} 款, 新增 {inserted} 款（预置库共 {total}）, "
          f"类型覆盖 {type_overrides} 款（Tool/Beta 等非 Game）")


if __name__ == "__main__":
    main()
