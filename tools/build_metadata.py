"""预制元数据构建脚本：Steam Store appdetails 批量抓取 → metadata_prebuilt.db。

用途（哩米需求 2026-08-12）：扫描后直接读取预制元数据（开发商/类型/标签/发行日期），
不需要每次联网重拉。数据源 store.steampowered.com/api/appdetails（无 Key，l=schinese）。

限速策略（POC-5 实测 store 429 严格）：并发 3 + 429 退避（1s/2s）+ 断点续传。

用法：
    python tools/build_metadata.py                      # 全量（librarycache appid）
    python tools/build_metadata.py --limit 10           # 小批量验证
    python tools/build_metadata.py --appids 1245620,730 # 指定 appid

产出：client/resources/metadata_prebuilt.db（SQLite，metadata_cache 同构表）
"""
import argparse
import concurrent.futures as futures
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
STEAM_PATH = Path(r"C:\Program Files (x86)\Steam")  # 兜底；优先检测（审查修复 2026-08-13）
OUT_DB = ROOT / "client" / "resources" / "metadata_prebuilt.db"
PROGRESS = ROOT / "tools" / "metadata_progress.json"  # 断点续传
CONCURRENCY = 6  # store 恢复后提速（429 退避兜底；2026-08-12 实测 3 并发全量约 2 小时）
TIMEOUT = 15


def _steam_path() -> Path:
    """Steam 路径：注册表/常见路径检测优先（非默认安装用户）。"""
    try:
        from client.utils.steam_path import detect_steam_path

        detected = detect_steam_path()
        if detected:
            return detected
    except Exception:
        pass
    return STEAM_PATH


def _library_appids() -> list[str]:
    """本机 librarycache appid（覆盖 1181 款，含家庭共享）。"""
    import sys

    sys.path.insert(0, str(ROOT))
    from client.core.library_cache import get_local_library_appids
    from client.core.scanner import find_steamid32

    steam_path = _steam_path()
    steamid32 = find_steamid32(steam_path) or ""  # 检测优先（审查修复：去硬编码）
    return get_local_library_appids(steam_path, steamid32)


def fetch_appdetails(appid: str) -> dict | None:
    """抓取单款元数据（含 429 退避重试 3 次 + cc=us 锁区降级，v0 方案）。"""
    for attempt in range(3):
        try:
            resp = httpx.get(
                "https://store.steampowered.com/api/appdetails",
                params={"appids": appid, "l": "schinese"},
                timeout=TIMEOUT,
            )
            if resp.status_code == 429:
                time.sleep(1 + attempt)
                continue
            if resp.status_code != 200:
                return None
            info = resp.json().get(appid, {})
            if info.get("success"):
                parsed = _parse_appdetails(appid, info["data"])
                # genres 为空（锁区页面无类型数据）→ cc=us 降级重试（P2：未分类补齐）
                if parsed["genres"] != "[]":
                    return parsed
            # 锁区（国区无页面或 genres 空）→ cc=us 降级重试（v0 实测：4065090 验证通过）
            resp_us = httpx.get(
                "https://store.steampowered.com/api/appdetails",
                params={"appids": appid, "l": "schinese", "cc": "us"},
                timeout=TIMEOUT,
            )
            if resp_us.status_code == 200:
                info_us = resp_us.json().get(appid, {})
                if info_us.get("success"):
                    return _parse_appdetails(appid, info_us["data"])
            return None
        except httpx.HTTPError:
            time.sleep(0.5 * (attempt + 1))
    return None


def _parse_appdetails(appid: str, d: dict) -> dict:
    """appdetails data → 元数据字典（与 steam_api._extract_metadata 对齐）。"""
    import json as _json

    langs = d.get("supported_languages", {})
    if isinstance(langs, dict):
        langs = list(langs.keys())[:8]
    elif isinstance(langs, str):
        langs = [langs]
    else:
        langs = []
    genres = [g["description"] for g in d.get("genres", [])]
    return {
        "appid": appid,
        "name": d.get("name", ""),
        "type": d.get("type", "unknown"),
        "developer": ", ".join(d.get("developers", [])) or None,
        "publisher": ", ".join(d.get("publishers", [])) or None,
        "release_date": d.get("release_date", {}).get("date") or None,
        "store_tags": _json.dumps(genres, ensure_ascii=False),
        "genres": _json.dumps(genres, ensure_ascii=False),
        "supported_languages": _json.dumps(langs, ensure_ascii=False),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只抓前 N 个（验证用）")
    parser.add_argument("--appids", default="", help="指定 appid 列表（逗号分隔）")
    parser.add_argument("--force", action="store_true",
                        help="强制重抓（跳过断点续传 done 过滤）")
    args = parser.parse_args()

    if args.appids:
        appids = [a.strip() for a in args.appids.split(",") if a.strip()]
    else:
        appids = _library_appids()
    if args.limit:
        appids = appids[: args.limit]

    # 断点续传：跳过已成功的（--force 除外）
    done: dict[str, dict] = {}
    if PROGRESS.exists():
        done = json.loads(PROGRESS.read_text(encoding="utf-8"))
    todo = appids if args.force else [a for a in appids if a not in done]
    print(f"待抓 {len(todo)} 款（已完成 {len(done)}）", flush=True)

    t0 = time.monotonic()
    with futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        for i, (appid, result) in enumerate(
            zip(todo, pool.map(fetch_appdetails, todo)), start=1
        ):
            if result:
                done[appid] = result
            if i % 25 == 0:
                PROGRESS.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
                print(
                    f"  进度 {i}/{len(todo)}（成功 {len(done)}），"
                    f"耗时 {time.monotonic() - t0:.0f}s", flush=True
                )
    PROGRESS.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")

    # 写 SQLite（metadata_cache 同构：appid PK + 元数据字段）
    OUT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(OUT_DB)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS metadata_cache (
            appid TEXT PRIMARY KEY, name TEXT, developer TEXT, publisher TEXT,
            release_date TEXT, store_tags TEXT, genres TEXT,
            review_score INTEGER, review_count INTEGER,
            supported_languages TEXT, fetched_at TEXT, expires_at TEXT)"""
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    conn.executemany(
        """INSERT OR REPLACE INTO metadata_cache
           (appid, name, developer, publisher, release_date, store_tags, genres,
            review_score, review_count, supported_languages, fetched_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)""",
        [
            (appid, m["name"], m["developer"], m["publisher"], m["release_date"],
             m["store_tags"], m["genres"], m["supported_languages"], now, now)
            for appid, m in done.items()
        ],
    )
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM metadata_cache").fetchone()[0]
    conn.close()
    print(f"完成: metadata_prebuilt.db 共 {count} 款（总耗时 {time.monotonic() - t0:.0f}s）")


if __name__ == "__main__":
    main()
