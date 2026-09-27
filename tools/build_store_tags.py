"""Steam 商店页社区标签抓取（P2 store_tags 正源，2026-08-13）。

背景：
- build_metadata v1 曾把 genres 镜像写入 store_tags（数据缺陷 bug）
- appinfo.vdf 无 store_tags（实测否定，3650 条 0 个 section 有）
- SteamSpy API 403（封 IP）
- 商店页 app_tag 需 agecheck cookie（birthtime/lastagecheckage/mature_content）
  ——实测法环返回 12 个社区标签（Souls-like/Open World/Dark Fantasy...）

用法：
    python tools/build_store_tags.py --appids 1245620,730   # 小批量验证
    python tools/build_store_tags.py                        # 全量（断点续传+后台）
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from concurrent import futures
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from client.core.scanner import find_steamid32  # noqa: E402
from client.core.library_cache import get_local_library_appids  # noqa: E402
from client.utils.steam_path import detect_steam_path  # noqa: E402

META_DB = ROOT / "client" / "resources" / "metadata_prebuilt.db"
PROGRESS = ROOT / "tools" / "store_tags_progress.json"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}
COOKIES = {"birthtime": "568022401", "lastagecheckage": "1-0-2000",
           "mature_content": "1"}
CONCURRENCY = 4
TAG_RE = re.compile(r'class="app_tag"[^>]*>\s*([^<]+?)\s*<')


def fetch_page_tags(appid: str, timeout: int = 25) -> list[str] | None:
    """抓取单款商店页社区标签；失败/无标签返回 None（可重试）。"""
    for attempt in range(3):
        try:
            resp = httpx.get(
                f"https://store.steampowered.com/app/{appid}/",
                headers=UA, cookies=COOKIES, timeout=timeout,
                follow_redirects=True,
            )
            if resp.status_code != 200:
                time.sleep(1 + attempt)
                continue
            tags = [t.strip() for t in TAG_RE.findall(resp.text)]
            if len(tags) >= 3:  # agecheck 页（1 个 Violent）不算
                return tags
            time.sleep(0.5)
        except httpx.HTTPError:
            time.sleep(1 + attempt)
    return None


def _library_appids() -> list[str]:
    steam = detect_steam_path()
    sid = find_steamid32(steam) or ""
    return get_local_library_appids(steam, sid)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--appids", default="", help="指定 appid 列表（逗号分隔）")
    parser.add_argument("--limit", type=int, default=0, help="只抓前 N 个")
    args = parser.parse_args()

    if args.appids:
        appids = [a.strip() for a in args.appids.split(",") if a.strip()]
    else:
        appids = _library_appids()
    if args.limit:
        appids = appids[: args.limit]

    # 断点续传：跳过已成功的
    done: dict[str, list[str]] = {}
    if PROGRESS.exists():
        done = json.loads(PROGRESS.read_text(encoding="utf-8"))
    todo = [a for a in appids if a not in done]
    print(f"待抓 {len(todo)} 款（已完成 {len(done)}）", flush=True)

    t0 = time.monotonic()
    ok = 0
    with futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        for i, (appid, tags) in enumerate(
            zip(todo, pool.map(fetch_page_tags, todo)), start=1
        ):
            if tags:
                done[appid] = tags
                ok += 1
            if i % 10 == 0:
                PROGRESS.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
                print(f"  进度 {i}/{len(todo)}（成功 {ok}），"
                      f"耗时 {time.monotonic() - t0:.0f}s", flush=True)
    PROGRESS.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
    print(f"抓取完成: {ok}/{len(todo)} 款有标签（总耗时 {time.monotonic() - t0:.0f}s）")

    # 更新 metadata_prebuilt.db 的 store_tags（修复 genres 镜像 bug）
    if ok:
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        conn = sqlite3.connect(META_DB)
        updated = 0
        for appid, tags in done.items():
            row = conn.execute(
                "SELECT 1 FROM metadata_cache WHERE appid=?", (appid,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE metadata_cache SET store_tags=?, fetched_at=? WHERE appid=?",
                    (json.dumps(tags, ensure_ascii=False), now, appid))
                updated += 1
        conn.commit()
        conn.close()
        print(f"已写入预置库 store_tags: {updated} 款")


if __name__ == "__main__":
    main()
