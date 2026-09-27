"""appinfo.vdf 离线元数据补全（哩米需求 2026-08-14：v0 可按 Steam 路径取信息）。

不依赖 API key / 网络——解析本机 appinfo.vdf（Steam 自动更新）：
common.name / common.type（权威应用类型 Game/Tool/Beta/Config）+ extended.developer/publisher。

用法（GUI「离线补全」按钮 / CLI tools/build_appinfo_meta.py 均走此函数）。
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from loguru import logger

from client.core.appinfo_parser import parse_appinfo
from client.utils.steam_path import detect_steam_path

META_DB = Path(__file__).resolve().parent.parent.parent / "client" / "resources" / "metadata_prebuilt.db"


def enrich_from_appinfo(
    appids: set[int] | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> dict:
    """appinfo.vdf 离线补全 metadata_cache（补名/补开发商/补应用类型）。

    返回 {"target", "hit", "updated", "inserted", "type_overrides"}。
    """
    steam_path = detect_steam_path()
    appinfo_path = steam_path / "appcache" / "appinfo.vdf"
    if not appinfo_path.exists():
        logger.warning(f"appinfo.vdf 不存在: {appinfo_path}")
        return {"target": 0, "hit": 0, "updated": 0, "inserted": 0, "type_overrides": 0}

    if appids is None:
        from client.core.library_cache import get_local_library_appids
        from client.core.scanner import find_steamid32

        steamid32 = find_steamid32(steam_path) or ""
        appids = {int(a) for a in get_local_library_appids(steam_path, steamid32)}

    parsed = parse_appinfo(appinfo_path, appids)
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
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    type_overrides = updated = inserted = 0
    total = len(parsed)
    for i, (appid, tree) in enumerate(parsed.items()):
        common = tree.get("common", {}) or {}
        extended = tree.get("extended", {}) or {}
        name = common.get("name") or ""
        dev = extended.get("developer") or ""
        pub = extended.get("publisher") or ""
        atype = common.get("type") or ""
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
        if not cur_name or cur_name.startswith("["):
            new_name = name
        if not cur_dev and dev:
            new_dev = dev
        if not cur_pub and pub:
            new_pub = pub
        if (new_name, new_dev, new_pub) != (cur_name, cur_dev, cur_pub):
            conn.execute(
                "UPDATE metadata_cache SET name=?, developer=?, publisher=? WHERE appid=?",
                (new_name, new_dev, new_pub, str(appid)))
            updated += 1
        if progress_cb and i % 200 == 0:
            progress_cb(i, total)
    conn.commit()
    conn.close()
    logger.info(
        f"appinfo 离线补全: 目标 {len(appids)} / 命中 {len(parsed)} / "
        f"补名补商 {updated} / 新增 {inserted} / 类型覆盖 {type_overrides}")
    return {"target": len(appids), "hit": len(parsed),
            "updated": updated, "inserted": inserted, "type_overrides": type_overrides}
