"""锁区检测（2026-08-13，哩米需求：锁区游戏 → R18 风格标签）。

判定：appdetails 默认国区 success=false 且 cc=us success=true → 锁区。
检测集合：OWNED 自有 29 款 + 严格 R18 标签候选 102 款（去重）。
断点续传 tools/locked_progress.json。
"""
import json
import sys
import time
from concurrent import futures
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROGRESS = ROOT / "tools" / "locked_progress.json"
OUT = ROOT / "tools" / "locked_result.json"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}
CONCURRENCY = 4


def check_locked(appid: str) -> bool | None:
    """返回 True=锁区 False=非锁区 None=检测失败。"""
    for attempt in range(3):
        try:
            resp = httpx.get(
                "https://store.steampowered.com/api/appdetails",
                params={"appids": appid, "l": "schinese"},
                headers=UA, timeout=20,
            )
            if resp.status_code == 429:
                time.sleep(1 + attempt)
                continue
            if resp.status_code != 200:
                time.sleep(1 + attempt)
                continue
            info = resp.json().get(appid, {})
            if info.get("success"):
                return False  # 国区可访问
            # 国区失败 → 美区验证
            resp_us = httpx.get(
                "https://store.steampowered.com/api/appdetails",
                params={"appids": appid, "l": "schinese", "cc": "us"},
                headers=UA, timeout=20,
            )
            if resp_us.status_code == 200 and resp_us.json().get(appid, {}).get("success"):
                return True  # 国区无、美区有 → 锁区
            return False  # 两边都无 → 非锁区（下架等）
        except httpx.HTTPError:
            time.sleep(1 + attempt)
    return None


def main() -> None:
    import sqlite3
    import os

    db = os.path.join(os.environ["APPDATA"], "KuJi", "kuji.db")
    conn = sqlite3.connect(db)
    owned = [r[0] for r in conn.execute(
        "SELECT appid FROM games WHERE source='OWNED' AND del_flag=0 AND app_type='game'")]
    cands = json.loads((ROOT / "tools" / "r18_candidates.json").read_text(encoding="utf-8"))
    cand_ids = [c["appid"] for c in cands]
    conn.close()
    appids = sorted(set(owned) | set(cand_ids))
    print(f"检测集合: {len(appids)} 款（OWNED {len(owned)} + R18 候选 {len(cand_ids)}）")

    done: dict[str, bool] = {}
    if PROGRESS.exists():
        done = json.loads(PROGRESS.read_text(encoding="utf-8"))
    todo = [a for a in appids if a not in done]
    print(f"待检测 {len(todo)}（已完成 {len(done)}）", flush=True)

    t0 = time.monotonic()
    with futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        for i, (appid, locked) in enumerate(
            zip(todo, pool.map(check_locked, todo)), start=1
        ):
            if locked is not None:
                done[appid] = locked
            if i % 10 == 0:
                PROGRESS.write_text(json.dumps(done), encoding="utf-8")
                print(f"  进度 {i}/{len(todo)}，耗时 {time.monotonic() - t0:.0f}s", flush=True)
    PROGRESS.write_text(json.dumps(done), encoding="utf-8")
    locked_ids = [a for a, v in done.items() if v]
    print(f"完成: 锁区 {len(locked_ids)} 款 / {len(done)} 款")
    OUT.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
