"""自动分类脚本 v2：预制元数据（genres/社区标签）→ 官方分类树叶节点映射。

哩米需求（2026-08-12）：自动分类，分类结果作为预制元数据（metadata_prebuilt.db
新增 pre_classified 表），扫描后直接读取，无需每次重新分类。

v2 改进（2026-08-13，P2 自动分类质量）：
- 修复 v1 顺序匹配缺陷：「动作+冒险」94 款等组合 genres 被塞进「其他动作」（485 款/53%）
- **组合决策**：genres 组合优先（动作+冒险→动作冒险、动作+角色扮演→ARPG、角色扮演+策略→SRPG…）
- **双语**：cc=us 锁区抓取的英文 genre（Action/RPG/Strategy…）同样可分类
- **TAG_MAP 高置信映射**：只保留社区标签特征词（FPS/魂系/无双/视觉小说…），
  不与 genres 冲突；等 appinfo.vdf 社区标签落地后自动生效（store_tags 目前是 genres 镜像）
- **pre_classified 全量重建**：消除 v1 的 stale 残留行
- confidence：TAG_MAP 命中 0.9 / genres 组合 0.7

用法：python tools/auto_classify.py [--appids 1245620,730] [--full-rebuild]
"""
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
META_DB = ROOT / "client" / "resources" / "metadata_prebuilt.db"

# ── 社区标签特征词 → 精确叶节点（高置信，v2 只保留 genres 不会出现的词）──
# store_tags 现为真实社区标签（build_store_tags.py 抓取，2026-08-13 起）。
# 匹配优先级：具体玩法标签在前，通用兜底（roguelike）最后——
# 例：杀戮尖塔 tags=[Roguelike, Deckbuilding] 须命中 dbg 而非 rpg-other。
TAG_MAP: dict[str, str] = {
    # ── 强信号标签（最优先，2026-08-13 JRPG 校准：JRPG 标签几乎只出现在
    # 日式 RPG——P5R/轨迹/传说等被 stealth/visual novel/musou 劫持的修复）──
    "jrpg": "jrpg", "日式rpg": "jrpg",
    # ── 多人细分（哩米 2026-08-13：多人大类 = MOBA/派对游戏/PVP/PVE/双人游戏）──
    # 只保留专属标签：pvp/pve/competitive 太泛（联机游戏全有，会劫持主玩法——
    # 实测法环/黑魂→PVP 误判），不映射；PVPVE（塔科夫类）→ PVP（哩米指定）
    "moba": "pvp",
    "party game": "party-game", "party": "party-game",
    "pvpve": "pvp",
    "duo": "duo-game", "2 player": "duo-game", "two player": "duo-game",
    # ── 具体玩法标签（高置信，优先）──
    # 射击细分
    "fps": "fps", "第一人称射击": "fps",
    "tps": "tps", "第三人称射击": "tps",
    "弹幕": "stg", "stg": "stg",
    # 动作细分
    "souls": "soulslike", "souls-like": "soulslike", "soulslike": "soulslike",
    "类魂": "soulslike", "魂系": "soulslike",
    "无双": "musou", "musou": "musou",
    "潜行": "stealth", "stealth": "stealth", "潜入": "stealth",
    "银河城": "metroidvania", "metroidvania": "metroidvania",
    "平台跳跃": "platformer", "platformer": "platformer",
    # 冒险细分
    "视觉小说": "galgame", "visual novel": "galgame", "galgame": "galgame",
    "废萌": "galgame", "galgame": "galgame",
    "电波": "galgame", "galgame": "galgame",
    "乙女": "galgame", "otome": "galgame",
    "点击式": "point-click", "point-and-click": "point-click",
    "互动电影": "interactive-film", "interactive movie": "interactive-film",
    # 真人互动（哩米 2026-08-14：完蛋我被美女包围了类真人 FMV；互动电影留给 3D 演出。
    # 不用 fmv 标签——量子破碎/风暴崛起等过场播片也算 FMV，误判 RTS/TPS；真人名单走 REVIEW）
    "真人互动": "live-action", "真人": "live-action",
    "live action": "live-action",
    # RPG 细分
    "crpg": "crpg", "类博德之门": "crpg", "博德之门": "crpg",
    "mmorpg": "mmorpg",
    "暗黑": "arpg-dark", "hack and slash": "arpg-dark", "arpg": "arpg-dark",
    # 策略细分
    # 城市建造/殖民模拟优先于 rts（2026-08-13：冰汽时代/纪元等城市管理游戏
    # 被社区标 RTS，但应按模拟经营归模拟大类——哩米模拟榜单校准）
    "city builder": "sim-management", "colony sim": "sim-management",
    "rts": "rts", "即时战略": "rts",
    "4x": "4x", "grand strategy": "war-strategy",
    "塔防": "tower-defense", "tower defense": "tower-defense",
    "战争策略": "war-strategy",
    "srpg": "srpg", "策略rpg": "srpg",
    # 卡牌细分（在 roguelike 之前：杀戮尖塔类先命中 dbg）
    "card": "card", "card": "card", "card": "card",
    "卡组构筑": "card", "deckbuilder": "card", "deck building": "card",
    "deckbuilding": "card",
    # 恐怖细分
    "僵尸": "horror-zombie", "zombie": "horror-zombie",
    "克苏鲁": "horror-lovecraft", "lovecraft": "horror-lovecraft",
    "心理恐怖": "horror-psychological", "psychological horror": "horror-psychological",
    "生存恐怖": "horror-survival", "survival horror": "horror-survival",
    "恐怖解谜": "horror-puzzle", "潜行恐怖": "horror-stealth",
    # 其他具体
    "moba": "pvp",
    "模拟经营": "sim-management", "经营": "sim-management",
    "farming sim": "sim-management", "farming": "sim-management",
    "life sim": "sim-management", "city builder": "sim-management",
    "恋爱模拟": "sim-dating", "dating sim": "sim-dating",
    "生存建造": "survival-craft", "base building": "survival-craft",
    "crafting": "survival-craft", "survival": "survival-craft",
    "节奏": "rhythm", "rhythm": "rhythm", "音游": "rhythm",
    "竞速": "racing-sports", "racing": "racing-sports",
    "格斗": "fighting", "fighting": "fighting",
    "解谜": "puzzle", "puzzle": "puzzle",
    # 无 sandbox：社区标签太泛（巫师3/GTA 等开放世界 RPG 也被标 Sandbox，
    # 实测误判→沙盒；真沙盒游戏有 survival/crafting 标签走生存建造）
    # ── 通用兜底（低置信，最后匹配）──
    # 肉鸽细分：action roguelike → 其他动作（幸存者/动作肉鸽主流去向）
    "action roguelike": "action-other",
    "肉鸽": "rpg-other", "roguelike": "rpg-other", "roguelite": "rpg-other",
    "traditional roguelike": "rpg-other",
}

# ── genres 双语规范化（中文 l=schinese / 英文 cc=us）──
_GENRE_NORM: dict[str, str] = {
    "角色扮演": "rpg", "rpg": "rpg", "role-playing": "rpg",
    "动作": "action", "action": "action",
    "冒险": "adventure", "adventure": "adventure",
    "射击": "shooter", "shooter": "shooter",
    "策略": "strategy", "strategy": "strategy",
    "模拟": "simulation", "simulation": "simulation",
    "竞速": "racing", "racing": "racing",
    "体育": "sports", "sports": "sports",
    "休闲": "casual", "casual": "casual",
    "独立": "indie", "indie": "indie",
    "恐怖": "horror", "horror": "horror",
    "大型多人在线": "mmo", "massively multiplayer": "mmo",
    "益智": "puzzle", "puzzle": "puzzle",
    "卡牌": "card", "card": "card",
    "格斗": "fighting", "fighting": "fighting",
    "沙盒": "sandbox", "sandbox": "sandbox",
    "免费开玩": "f2p", "free to play": "f2p",
    "抢先体验": "early", "early access": "early",
    "实用工具": "utility", "utility": "utility",
    "视频制作": "video", "video production": "video",
    "软件": "software", "software": "software",
    "教育": "education", "education": "education",
    "音频制作": "audio", "audio production": "audio",
    "网页游戏": "web", "web publishing": "web",
    "休闲模拟": "casual",
}

# 无分类信号的 genre（开发/商业属性，不参与决策）
_IGNORE = {"indie", "f2p", "early", "utility", "video", "software", "education", "audio", "web"}


def _norm_genres(genres: str) -> set[str]:
    """genres JSON 字符串 → 规范化 genre 集合。"""
    try:
        raw = json.loads(genres or "[]")
    except (ValueError, TypeError):
        return set()
    out = set()
    for g in raw:
        if not isinstance(g, str):
            continue
        key = g.strip().lower()
        out.add(_GENRE_NORM.get(key, key))
    return out


def _genre_fallback(genres: set[str]) -> str | None:
    """genres 组合决策（v2 核心）：组合优先，无信号 → None。"""
    sig = genres - _IGNORE
    if not sig:
        return None
    # 跨大类组合（有明确语义）
    if {"rpg", "action"} <= sig:
        return "arpg-dark"            # 动作+角色扮演 → ARPG（暗黑类）
    if {"rpg", "strategy"} <= sig:
        return "srpg"                 # 角色扮演+策略 → SRPG
    if {"action", "adventure"} <= sig:
        return "action-adventure"     # 动作+冒险 → 动作冒险（v1 主修复项）
    if {"action", "shooter"} <= sig:
        return "fps"                  # 动作+射击 → FPS
    if {"rpg", "simulation"} <= sig:
        return "sim-management"       # 角色扮演+模拟 → 模拟经营（星露谷/符文工房类）
    if {"rpg", "adventure"} <= sig:
        return "rpg-other"            # 角色扮演+冒险 → 其他RPG
    if {"action", "simulation"} <= sig:
        return "simulation-other"     # 动作+模拟 → 其他模拟
    # 单一 genre → 大类兜底叶
    if "mmo" in sig:
        return "mmorpg"
    if "shooter" in sig:
        return "fps"
    if "horror" in sig:
        return "horror-other"
    if "rpg" in sig:
        return "rpg-other"
    if "adventure" in sig:
        return "adventure-other"
    if "action" in sig:
        return "action-other"
    if "strategy" in sig:
        return "strategy-other"
    if "simulation" in sig:
        return "sim-management"
    if "racing" in sig or "sports" in sig:
        return "racing-sports"
    if "casual" in sig:
        return "casual"
    if "puzzle" in sig:
        return "puzzle"
    if "card" in sig:
        return "card"
    if "fighting" in sig:
        return "fighting"
    if "sandbox" in sig:
        return "sandbox"
    return None


def _match_tag(tag: str, keyword: str) -> bool:
    """标签匹配：短词（≤3 字符）精确匹配，长词子串匹配。

    2026-08-13 修复：子串匹配曾把 "rts" 误配进 "Martial Arts"（暖雪/卡包修仙→RTS）。
    """
    k = keyword.lower()
    if len(k) <= 3:
        return tag == k
    return k in tag


def classify_one(genres: str, store_tags: str, name: str) -> tuple[str | None, float]:
    """单游戏分类：返回 (叶节点 slug, confidence) 或 (None, 0)。

    1) TAG_MAP 社区标签高置信匹配（confidence=0.9；短词精确/长词子串）
    2) genres 组合决策（confidence=0.7）
    """
    try:
        tags_list = json.loads(store_tags or "[]")
    except (ValueError, TypeError):
        tags_list = []
    if not isinstance(tags_list, list):
        tags_list = []
    name_l = (name or "").lower()
    # TAG_MAP 优先级（dict 顺序：具体标签在前、roguelike 兜底最后）优先于
    # 标签顺序——2026-08-13 修复：原按 tags 顺序遍历，tags[0]=Roguelike 先命中
    # 兜底导致后面的 Platformer/Deckbuilding 等精确标签被跳过（霓虹深渊→其他RPG）。
    for keyword, slug in TAG_MAP.items():
        k = keyword.lower()
        for tag in tags_list:
            if not isinstance(tag, str):
                continue
            t = tag.lower()
            if k == "party" and "party-based" in t:
                continue  # party-based rpg = 队伍制 RPG，非派对游戏（哩米 2026-08-13）
            if _match_tag(t, k):
                return slug, 0.9
        if k in name_l and not k.isascii():
            # 中文名全文匹配（2026-08-13 限定中文关键词：英文关键词匹配名字
            # 太泛——"party" 曾误配 Party Hard 潜行游戏）
            return slug, 0.9
    return _genre_fallback(_norm_genres(genres)), 0.7


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--appids", default="", help="只分类指定 appid")
    parser.add_argument("--full-rebuild", action="store_true",
                        help="重建 pre_classified（先清空旧行，默认仅 upsert）")
    args = parser.parse_args()

    # AI 复核覆盖表（2026-08-13：REVIEW 修正并入生成流程——否则每次
    # --full-rebuild 重建 pre_classified 会把 AI 修正冲掉，分类反复「灾难」）
    # None 值 = 明确「不分类」（软件/合集非游戏，如 Wallpaper Engine/OBS），
    # 2026-08-14：不再过滤——None 写入 overlay 后由下方 `if slug` 跳过落库
    overlay: dict[str, str | None] = {}
    try:
        from ai_classify_50 import AI_CLASSIFY, REVIEW_ADJUST, REVIEW_FULL

        for src in (AI_CLASSIFY, REVIEW_ADJUST, REVIEW_FULL):
            for appid, (slug, _reason) in src.items():
                overlay[appid] = slug
    except ImportError:
        pass

    conn = sqlite3.connect(META_DB)
    # 厂商画像 → TYPE 覆盖（哩米 2026-08-13 拓展：只做 Galgame/魂系/JRPG 的
    # 厂商按厂商归类——如 TYPE-MOON/柚子社→Galgame、FromSoftware→魂系）
    studio_overlay: dict[str, str] = {}
    try:
        import json as _json

        def studio_match(studio: str, text: str) -> bool:
            """厂商名匹配：短名（≤3 字符）按逗号分隔精确匹配，
            长名子串匹配。2026-08-14 修复：'key' 曾命中 'Donkey Crew'（Last Oasis→Galgame）。"""
            s = studio.lower()
            t = text.lower()
            if len(s) <= 3:
                return s in [part.strip() for part in t.split(",")]
            return s in t

        profile = _json.loads(
            (ROOT / "client" / "resources" / "studio_profiles.json").read_text(encoding="utf-8"))
        studio_map: list[tuple[str, str]] = []
        for slug, studios in profile.get("type_studios", {}).items():
            for s in studios:
                studio_map.append((s.lower(), slug))
        for appid, dev, pub in conn.execute(
                "SELECT appid, developer, publisher FROM metadata_cache"):
            for studio, slug in studio_map:
                if studio_match(studio, dev or "") or studio_match(studio, pub or ""):
                    studio_overlay[appid] = slug
                    break
    except (ImportError, OSError, ValueError):
        pass
    if args.appids:
        ids = [a.strip() for a in args.appids.split(",")]
        rows = conn.execute(
            "SELECT appid, name, store_tags, genres FROM metadata_cache "
            f"WHERE appid IN ({','.join('?' * len(ids))})", ids).fetchall()
    else:
        rows = conn.execute(
            "SELECT appid, name, store_tags, genres FROM metadata_cache").fetchall()

    conn.execute(
        """CREATE TABLE IF NOT EXISTS pre_classified (
            appid TEXT PRIMARY KEY, category_slug TEXT, confidence REAL,
            classified_at TEXT)"""
    )
    if args.full_rebuild:
        conn.execute("DELETE FROM pre_classified")  # 全量重建，消除 v1 stale 残留

    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    classified = 0
    for appid, name, store_tags, genres in rows:
        slug, confidence = classify_one(genres, store_tags or "", name or "")
        if appid in overlay:  # 人工 REVIEW 最高优先（哩米 2026-08-14：
            # 逐款确认压过厂商画像——WSS playground 曾把主播女孩劫持回 Galgame）
            slug = overlay[appid]
            confidence = max(confidence, 0.9)
        elif appid in studio_overlay:  # 厂商画像（高置信：只做该类型的厂商）
            slug = studio_overlay[appid]
            confidence = 0.95
        if slug:
            conn.execute(
                "INSERT OR REPLACE INTO pre_classified "
                "(appid, category_slug, confidence, classified_at) VALUES (?, ?, ?, ?)",
                (appid, slug, confidence, now),
            )
            classified += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM metadata_cache").fetchone()[0]
    conn.close()
    print(f"自动分类完成: {classified}/{total} 款（{classified * 100 // max(total, 1)}%）")


if __name__ == "__main__":
    main()
