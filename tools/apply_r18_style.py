"""R18 风格打标（哩米需求 2026-08-13，v4 修正：1demo 不算无需补丁）。

规则（哩米确认）：
- R18 标签：收藏夹「1」+「1demo」+ 严格 R18 标签候选（Hentai/Eroge/NSFW）
- 「无需补丁」：仅收藏夹「1」（78 款——用户自己的完整版 R18）
- 「需要补丁」：色情候选 − 收藏夹「1」
- 「1demo」15 款：只标 R18（demo 版仍是 R18 游戏），不标补丁状态

数据源：
- tools/steam_cat_1.json：Steam 收藏夹「1」（完整版 R18）
- tools/steam_cat_1demo.json：Steam 收藏夹「1demo」（demo 版 R18）
- tools/r18_candidates.json：严格 R18 标签候选

用法：python tools/apply_r18_style.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from client.models import CategoryScheme, GameClassification, Tag  # noqa: E402
from client.storage.database import get_session  # noqa: E402

CAT1 = ROOT / "tools" / "steam_cat_1.json"
CAT1_DEMO = ROOT / "tools" / "steam_cat_1demo.json"
CANDS = ROOT / "tools" / "r18_candidates.json"

# 剔除名单（哩米指定 2026-08-13：不在「无需补丁」展示，R18 保留）
EXCLUDE_NO_PATCH = {"2696050", "3260620"}
# 补充名单（哩米指定：收藏夹1 之外但应标「无需补丁」的自有锁区游戏）
EXTRA_NO_PATCH = {"3587140"}  # Ark Re:Code（自有锁区 R18）

# 擦边名单（哩米 2026-08-13：日系卖肉/美少女擦边——有成人要素但非黄油，
# R18 家族子类；初始为明确卖肉的，可继续补充）
ECHI_SET = {
    "889510": "闪乱神乐 Burst（卖肉动作）",
    "502800": "闪乱神乐 EV（卖肉动作）",
    "696170": "闪乱神乐水枪（卖肉）",
    "411830": "闪乱神乐 SV（卖肉动作）",
}

# 正经作品名单（哩米 2026-08-13：「为黄而黄」才打 R18；剧情向/知名 IP
# 游戏即使有成人要素也不算黄油——不打 R18 系列标签）
LEGIT_EXCLUDE = {'552280', '2396980', '1434480', '745850', '2527500', '1289310', '880950', '1451940', '2622000', '326480', '411830', '340730', '2458530', '675240', '502800', '889510', '487430', '2825300', '315210', '3094010', '696170', '358750', '4177730', '333600', '385800', '420110', '602520', '1406990', '899970'}
# 补丁状态标签（重打时清 SYSTEM_PRE_DB 旧标）
_PATCH_TAGS = {"R18", "无需补丁", "需要补丁", "擦边"}


def _studio_hits(session) -> tuple[set[str], set[str]]:
    """厂商画像命中（哩米 2026-08-13：按厂商判断黄油/正经，比标签更准）。

    返回 (adult_appids, legit_appids)——黄油厂商的游戏强制 R18；
    正经厂商的游戏从 R18 剔除。
    """
    import json as _json

    from client.models import Game

    def _match(studio: str, text: str) -> bool:
        """厂商名匹配：短名（≤3 字符）按逗号分隔精确匹配，长名子串。
        2026-08-14 修复：'key' 曾命中 'Donkey Crew'（Last Oasis 误判 Galgame）。"""
        s = studio.lower()
        t = text.lower()
        if len(s) <= 3:
            return s in [part.strip() for part in t.split(",")]
        return s in t

    profile_path = ROOT / "client" / "resources" / "studio_profiles.json"
    adult_appids: set[str] = set()
    legit_appids: set[str] = set()
    if not profile_path.exists():
        return adult_appids, legit_appids
    profile = _json.loads(profile_path.read_text(encoding="utf-8"))
    a_pub = [k.lower() for k in profile.get("adult_publishers", [])]
    a_dev = [k.lower() for k in profile.get("adult_developers", [])]
    l_stu = [k.lower() for k in profile.get("legit_studios", [])]
    for game in session.query(Game).filter(Game.del_flag.is_(False)).all():
        pub = (game.publisher or "").lower()
        dev = (game.developer or "").lower()
        if any(_match(k, pub) for k in a_pub) or any(_match(k, dev) for k in a_dev):
            adult_appids.add(game.appid)
        if any(_match(k, pub) for k in l_stu) or any(_match(k, dev) for k in l_stu):
            legit_appids.add(game.appid)
    return adult_appids, legit_appids


def main() -> None:
    cat1 = {str(a) for a in json.loads(CAT1.read_text(encoding="utf-8"))}
    cat1_demo = {str(a) for a in json.loads(CAT1_DEMO.read_text(encoding="utf-8"))}
    cands = {c["appid"] for c in json.loads(CANDS.read_text(encoding="utf-8"))}
    session = get_session()
    adult_appids, legit_appids = _studio_hits(session)
    session.close()
    # 厂商画像：黄油厂商强制 R18；正经厂商剔除（在 LEGIT_EXCLUDE 基础上）
    legit = LEGIT_EXCLUDE | legit_appids
    r18 = (cat1 | cat1_demo | cands | EXTRA_NO_PATCH | adult_appids) - legit
    no_patch = ((cat1 - EXCLUDE_NO_PATCH) | EXTRA_NO_PATCH) - legit
    need_patch = ((cands | adult_appids) - cat1) - legit  # cat1 无需补丁，不重复
    print(f"R18: {len(r18)} 款（厂商补 {len(adult_appids - cat1 - cat1_demo - cands)}）| "
          f"无需补丁: {len(no_patch)} | 需要补丁: {len(need_patch)} | "
          f"正经厂商剔除: {len(legit_appids)}")

    session = get_session()
    try:
        scheme = (
            session.query(CategoryScheme)
            .filter(CategoryScheme.source == "OFFICIAL",
                    CategoryScheme.del_flag.is_(False)).first()
        )
        if not scheme:
            print("无官方方案")
            return
        tag_map = {t.name: t for t in session.query(Tag).filter(
            Tag.scheme_id == scheme.id, Tag.dimension == "STYLE",
            Tag.del_flag.is_(False)).all()}
        missing = _PATCH_TAGS - set(tag_map)
        if missing:
            print(f"缺少标签: {missing}")
            return

        # 重置旧打标（物理删除 SYSTEM_PRE_DB 记录——uq_classification_tag
        # 唯一约束不含 del_flag，软删除仍占位会导致 INSERT 冲突）
        tag_ids = [tag_map[n].id for n in _PATCH_TAGS]
        cleared = session.query(GameClassification).filter(
            GameClassification.scheme_id == scheme.id,
            GameClassification.dimension == "STYLE",
            GameClassification.tag_id.in_(tag_ids),
            GameClassification.source == "SYSTEM_PRE_DB",
            GameClassification.del_flag.is_(False),
        ).delete(synchronize_session=False)
        print(f"重置旧打标: {cleared} 条")

        existing = {
            (gc.game_appid, gc.tag_id)
            for gc in session.query(GameClassification).filter(
                GameClassification.scheme_id == scheme.id,
                GameClassification.dimension == "STYLE",
                GameClassification.del_flag.is_(False)).all()
        }
        added = 0
        assignments = [("R18", r18), ("无需补丁", no_patch), ("需要补丁", need_patch),
                         ("擦边", set(ECHI_SET))]
        for tag_name, appids in assignments:
            tag = tag_map[tag_name]
            for appid in appids:
                if (appid, tag.id) in existing:
                    continue
                session.add(GameClassification(
                    game_appid=appid, scheme_id=scheme.id,
                    dimension="STYLE", tag_id=tag.id, source="SYSTEM_PRE_DB"))
                added += 1
        session.commit()
        # 同步持久化到预置库（哩米 2026-08-14：打包产物需带风格——
        # 新用户运行库为空，STYLE 必须随包分发才能开箱即用）
        import sqlite3 as _sqlite3

        prebuilt = ROOT / "client" / "resources" / "metadata_prebuilt.db"
        pconn = _sqlite3.connect(prebuilt)
        pconn.execute("""CREATE TABLE IF NOT EXISTS pre_styles (
            appid TEXT NOT NULL, tag_name TEXT NOT NULL,
            PRIMARY KEY (appid, tag_name))""")
        pconn.execute("DELETE FROM pre_styles")
        for tag_name, appids in assignments:
            pconn.executemany(
                "INSERT OR REPLACE INTO pre_styles (appid, tag_name) VALUES (?, ?)",
                [(a, tag_name) for a in appids])
        pconn.commit()
        pconn.close()
        print(f"打标完成: 新增 {added} 条（pre_styles 已同步 {pconn.total_changes if False else 'OK'}）")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
