"""测试轮综合：黑盒 + 白盒 + 压力（哩米 2026-08-14）。

黑盒：命令行工具输入输出（preview/check 不写真实数据）
白盒：vdf 序列化往返 / update_categories / delete_kuji 逻辑（沙盒副本）
压力：全量分类耗时 / 写回目标构建 / 封面索引构建 / 规则引擎批量

运行：python tests/sandbox/integration/test_blackbox_whitebox_stress.py
"""
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  {'✓' if cond else '✗'} {name}" + (f" — {detail}" if detail else ""))


def main() -> None:
    t0 = time.time()
    # ═══ 黑盒：命令行工具链（只读操作，不写真实 Steam）═══
    print("[黑盒] 命令行工具")
    from writeback_acceptance import build_target, cmd_preview  # noqa: F401

    target = build_target()
    check("preview 目标构建（输入=DB 输出=收藏夹映射）",
          len(target) > 1000 and all(
              all(isinstance(n, str) and n.startswith("库集_") for n in names)
              for names in target.values()),
          f"{len(target)} 款 → {sum(len(v) for v in target.values())} 条目")

    # ═══ 白盒：vdf 序列化往返 + update/delete（沙盒副本）═══
    print("[白盒] vdf 序列化 + 增量/删除逻辑（沙盒副本）")
    from client.core import vdf_ops

    real_store = vdf_ops.get_store_path(
        Path(r"C:\Program Files (x86)\Steam"), "12345678")
    if real_store.exists():
        sandbox = ROOT / "tests" / "sandbox" / "tmp_store"
        sb_dir = sandbox / "userdata" / "x" / "config" / "cloudstorage"
        sb_dir.mkdir(parents=True, exist_ok=True)
        sb_store = sb_dir / "cloud-storage-namespace-1.json"
        shutil.copy2(real_store, sb_store)
        data = vdf_ops.read_store(sb_store)
        # 序列化往返：read→atomic_write→read 一致性
        vdf_ops.atomic_write(sb_store, data)
        data2 = vdf_ops.read_store(sb_store)
        check("vdf 序列化往返一致", data == data2, f"{len(data)} 条目")
        # update_categories：增量加游戏到「库集_压力测试」收藏夹
        res = vdf_ops.update_categories(Path(sandbox), "x", {"库集_压力测试": {"730", "570"}})
        check("update_categories 新建收藏夹+合并", res["written"] >= 1 and res["verified"],
              f"written={res['written']} verified={res['verified']}")
        data3 = vdf_ops.read_store(sb_store)

        def _name_of(meta) -> str:
            if not isinstance(meta, dict) or not isinstance(meta.get("value"), str):
                return ""
            try:
                parsed = json.loads(meta["value"])
                return parsed.get("name", "") if isinstance(parsed, dict) else ""
            except ValueError:
                return ""

        check("增量后收藏夹存在", any(
            _name_of(m) == "库集_压力测试"
            for k, m in data3 if isinstance(m, dict)))
        # delete_kuji_collections：删除库集_前缀（沙盒里只有测试夹）
        res2 = vdf_ops.delete_kuji_collections(Path(sandbox), "x")
        data4 = vdf_ops.read_store(sb_store)
        remaining = [_name_of(m) for k, m in data4
                     if _name_of(m).startswith("库集_")]
        check("delete_kuji 只删库集_（沙盒）", res2["deleted"] >= 1 and not remaining,
              f"deleted={res2['deleted']}")
        shutil.rmtree(sandbox, ignore_errors=True)
    else:
        check("真实 store 定位（沙盒白盒跳过）", False, "store 不存在")

    # 白盒：REVIEW overlay 优先级（人工 > 厂商画像）
    from tools.ai_classify_50 import REVIEW_ADJUST  # noqa: F401
    import sqlite3

    conn = sqlite3.connect(ROOT / "client" / "resources" / "metadata_prebuilt.db")
    row = conn.execute("SELECT category_slug FROM pre_classified WHERE appid='1451940'").fetchone()
    check("REVIEW 人工优先于厂商画像（主播女孩=galgame）",
          row and row[0] == "galgame", f"1451940 → {row[0] if row else '?'}")
    conn.close()

    # ═══ 压力：性能基准 ═══
    print("[压力] 性能基准")
    t1 = time.time()
    from tools.auto_classify import classify_one

    rows = conn2 = sqlite3.connect(ROOT / "client" / "resources" / "metadata_prebuilt.db")
    games = rows.execute(
        "SELECT appid, name, store_tags, genres FROM metadata_cache").fetchall()
    t2 = time.time()
    for appid, name, tags, genres in games:
        classify_one(genres or "", tags or "", name or "")
    dt = time.time() - t2
    check(f"单游戏分类吞吐（{len(games)} 款）", dt < 10,
          f"{dt:.2f}s（{len(games) / dt:.0f} 款/s）")
    rows.close()

    t3 = time.time()
    from client.core.cover_art import build_cover_index

    idx = build_cover_index()
    dt3 = time.time() - t3
    check(f"封面索引构建（{len(idx)} 款）", dt3 < 30, f"{dt3:.1f}s")

    # 规则引擎批量（构造 500 条规则评估）
    from client.core.rule_engine import rule_matches

    rule = {"schema_version": 1, "conditions": [
        {"field": "steam_tags", "op": "contains_any", "value": ["FPS"]},
        {"field": "release_year", "op": "gt", "value": 2015}]}
    t4 = time.time()
    n_hit = sum(1 for appid, name, tags, genres in games
                if rule_matches(rule, {"appid": appid, "steam_tags": json.loads(tags or "[]")}))
    dt4 = time.time() - t4
    check(f"规则引擎批量（{len(games)} 款）", dt4 < 5, f"{dt4:.2f}s，命中 {n_hit}")

    print(f"\n综合测试: {sum(1 for _, ok, _ in results if ok)}/{len(results)} 通过"
          f"（总耗时 {time.time() - t0:.1f}s）")


if __name__ == "__main__":
    main()
