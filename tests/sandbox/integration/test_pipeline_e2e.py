"""集成测试：分类管道端到端（树→预分类→同步→写回目标→风格一致性）。

运行：python tests/sandbox/integration/test_pipeline_e2e.py
（沙盒脚本，pytest 不收集——顶层执行依赖真实 DB）
"""
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def main() -> None:
    t0 = time.time()
    # 1) 树加载（官方方案 v0.9）
    from client.core.scheme_service import get_scheme_tree, load_official_scheme

    scheme = load_official_scheme()
    tree = get_scheme_tree(scheme.id)
    print(f"[1] 树加载: {len(tree)} 节点, version={scheme.version} ✓")

    # 2) 预分类全量
    import sqlite3

    conn = sqlite3.connect(ROOT / "client" / "resources" / "metadata_prebuilt.db")
    cur = conn.cursor()
    n_pre = cur.execute("SELECT COUNT(*) FROM pre_classified").fetchone()[0]
    n_cls = cur.execute(
        "SELECT COUNT(*) FROM pre_classified WHERE category_slug IS NOT NULL"
    ).fetchone()[0]
    print(f"[2] 预分类: {n_cls}/{n_pre} 已分类 ✓")
    assert n_pre >= 1100 and n_cls / n_pre > 0.9, "分类覆盖率异常"

    # 3) 同步到运行库（TYPE）
    from client.core.metadata_loader import sync_prebuilt_classification

    synced = sync_prebuilt_classification()
    print(f"[3] TYPE 同步: {synced} 条 ✓")

    # 4) 写回目标构建（与 preview 同逻辑）
    from writeback_acceptance import build_target

    target = build_target()
    c = Counter()
    for appid, names in target.items():
        for n in names:
            c[n] += 1
    print(f"[4] 写回目标: {len(target)} 款 → {len(c)} 收藏夹（{sum(c.values())} 条目）✓")
    bad = [n for n in c if "/" in n]
    assert not bad, f"收藏夹名含斜杠: {bad}"
    print("   收藏夹名无斜杠 ✓")

    # 5) 风格一致性（补丁子集 ⊆ R18）
    from client.models import GameClassification, Tag
    from client.storage.database import get_session

    s = get_session()
    try:
        tags = {t.name: t.id for t in s.query(Tag).filter(
            Tag.scheme_id == scheme.id, Tag.del_flag.is_(False)).all()}
        counts = {}
        for tname in ("R18", "无需补丁", "需要补丁", "擦边", "肉鸽"):
            counts[tname] = s.query(GameClassification).filter(
                GameClassification.tag_id == tags[tname],
                GameClassification.del_flag.is_(False)).count()
        print(f"[5] 风格: R18={counts['R18']} 无需={counts['无需补丁']} "
              f"需要={counts['需要补丁']} 擦边={counts['擦边']} 肉鸽={counts['肉鸽']}")
        assert counts["无需补丁"] + counts["需要补丁"] + counts["擦边"] <= counts["R18"]
        print("   补丁子集 ⊆ R18 ✓")
    finally:
        s.close()
    print(f"集成测试完成: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
