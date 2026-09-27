"""端到端写回验收（P2 收尾）：预标注分类 + 规则命中 → Steam 收藏夹全量同步。

流程：
    python tools/writeback_acceptance.py preview   # 只读：构建写回目标并统计
    python tools/writeback_acceptance.py write     # 备份 + 写回 + 写后验证（须 Steam 关闭）
    python tools/writeback_acceptance.py check     # 回读统计（Steam 同步后）
    python tools/writeback_acceptance.py rollback  # 从最近备份恢复

铁律（vdf_ops.py:11）：真实写入前必须 哩米确认 + Steam 关闭 + 备份成功。
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from loguru import logger  # noqa: E402

from client.core.scanner import find_steamid32  # noqa: E402
from client.core.vdf_ops import (  # noqa: E402
    KUJI_PREFIX, backup_store, get_store_path, is_steam_running, write_categories,
)
from client.utils.steam_path import detect_steam_path  # noqa: E402

STEAM_PATH = detect_steam_path()
STEAMID32 = find_steamid32(STEAM_PATH) or ""


def build_target() -> dict[str, list[str]]:
    """构建写回目标 {appid: [库集_收藏夹名]}：预标注分类 + 规则命中。

    与 UI _build_target_from_rules 同逻辑（手动分类 TYPE + 启用规则按 priority）。
    """
    import sqlite3
    import os

    db = os.path.join(os.environ["APPDATA"], "KuJi", "kuji.db")
    conn = sqlite3.connect(db)
    target: dict[str, list[str]] = {}
    # 1) 预标注/手动分类（TYPE 维度；收藏夹名用叶名——Steam 名不允许斜杠）
    rows = conn.execute(
        """SELECT gc.game_appid, c.name FROM game_classifications gc
           JOIN categories c ON gc.category_id = c.id
           WHERE gc.dimension='TYPE' AND gc.del_flag=0 AND c.del_flag=0"""
    ).fetchall()
    for appid, path in rows:
        target.setdefault(appid, []).append(KUJI_PREFIX + path)
    # 2) 规则命中（当前激活方案的启用规则，按 priority 第一条命中）
    scheme = conn.execute(
        "SELECT id FROM category_schemes WHERE is_active=1 AND del_flag=0").fetchone()
    if scheme:
        rules = conn.execute(
            """SELECT r.name, r.conditions, r.priority, c.name FROM classification_rules r
               JOIN categories c ON r.target_category_id = c.id
               WHERE r.scheme_id=? AND r.is_enabled=1 AND r.del_flag=0
               ORDER BY r.priority""", (scheme[0],)).fetchall()
        from client.core.rule_engine import rule_matches

        games = conn.execute(
            """SELECT appid, developer, publisher, steam_tags FROM games
               WHERE del_flag=0 AND app_type='game'""").fetchall()
        for appid, dev, pub, tags in games:
            try:
                tag_list = json.loads(tags or "[]")
            except ValueError:
                tag_list = []
            gdict = {"appid": appid, "steam_tags": tag_list,
                     "developer": dev, "publisher": pub}
            for _, conds, _, path in rules:
                try:
                    cond = json.loads(conds)
                except ValueError:
                    continue
                if rule_matches({"conditions": cond}, gdict):
                    target.setdefault(appid, []).append(KUJI_PREFIX + path)
                    break
    conn.close()
    return target


def cmd_preview() -> None:
    target = build_target()
    by_cat: dict[str, list[str]] = {}
    for appid, cats in target.items():
        for c in cats:
            by_cat.setdefault(c, []).append(appid)
    print(f"写回目标: {len(target)} 款游戏 → {len(by_cat)} 个「{KUJI_PREFIX}」收藏夹\n")
    print("== 收藏夹分布（Top 20）==")
    for cat, apps in sorted(by_cat.items(), key=lambda x: -len(x[1]))[:20]:
        print(f"  {cat.replace(KUJI_PREFIX, ''):<24} {len(apps)} 款")
    print("\n== 空收藏夹（target 外不写，不会产生空夹）==")
    print("  （write_categories 仅同步 target 内收藏夹，无空夹风险）")
    single = sum(1 for apps in by_cat.values() if len(apps) == 1)
    print(f"\n单游戏收藏夹: {single} 个 | 总写入条目(游戏×收藏夹): {sum(len(v) for v in by_cat.values())}")


def cmd_write() -> None:
    if is_steam_running():
        print("❌ Steam 正在运行——铁律：写回前必须关闭 Steam")
        return
    target = build_target()
    print(f"写回目标: {len(target)} 款游戏")
    record = backup_store(STEAM_PATH, STEAMID32, notes="端到端写回验收")
    print(f"备份完成: {record.file_path}（sha256 {record.sha256[:16]}）")
    result = write_categories(STEAM_PATH, STEAMID32, target)
    print(f"写回完成: written={result['written']} deleted={result.get('deleted')} "
          f"verified={result['verified']}")
    if result["verified"]:
        print("✅ 写后回读验证通过——请打开 Steam 验收收藏夹")


def cmd_check() -> None:
    store = get_store_path(STEAM_PATH, STEAMID32)
    data = json.loads(store.read_text(encoding="utf-8"))
    active = deleted = 0
    by_cat: dict[str, int] = {}
    for key, meta in data:
        if not key.startswith("user-collections") or not isinstance(meta, dict):
            continue
        if meta.get("is_deleted"):
            deleted += 1
            continue
        value = meta.get("value")
        if not isinstance(value, str):
            continue
        try:
            inner = json.loads(value)
        except ValueError:
            continue
        name = inner.get("name", "")
        if name.startswith(KUJI_PREFIX):
            active += 1
            by_cat[name] = len(inner.get("added", []))
    print(f"库集_收藏夹: {active} 个活跃（{deleted} 个软删除），共 {sum(by_cat.values())} 个游戏条目")
    for name, n in sorted(by_cat.items(), key=lambda x: -x[1])[:10]:
        print(f"  {name.replace(KUJI_PREFIX, ''):<24} {n} 款")


def cmd_rollback() -> None:
    """从最近备份恢复（backup_records 最新一条）。"""
    from client.core.vdf_ops import get_session, read_store

    session = get_session()
    try:
        from client.models import BackupRecord

        record = (session.query(BackupRecord)
                  .filter(BackupRecord.notes.contains("端到端写回验收"))
                  .order_by(BackupRecord.created_at.desc()).first())
        if record is None:
            print("未找到验收备份记录")
            return
        store = get_store_path(STEAM_PATH, STEAMID32)
        shutil.copy2(record.file_path, store)
        print(f"已恢复: {record.file_path}")
    finally:
        session.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "preview"
    {"preview": cmd_preview, "write": cmd_write,
     "check": cmd_check, "rollback": cmd_rollback}[cmd]()
