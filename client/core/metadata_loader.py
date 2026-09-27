"""预制元数据加载（哩米需求 2026-08-12）：扫描后直接读内置 metadata_prebuilt.db。

数据来源：tools/build_metadata.py 构建（Steam Store appdetails 批量抓取，
覆盖本机库 1181 款含家庭共享）。随应用分发，扫描后同步到 MetadataCache 表，
规则引擎（steam_tags/developer/release_year 等字段）直接可用，无需联网重拉。
"""
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from loguru import logger

PREBUILT_DB = (
    Path(__file__).resolve().parent.parent.parent
    / "client" / "resources" / "metadata_prebuilt.db"
)

_CN_DATE = re.compile(r"(\d{4}) 年 (\d{1,2}) 月 (\d{1,2}) 日")


def _parse_date(value: str | None) -> datetime | None:
    """appdetails 发行日期解析（l=schinese 返回「2020 年 9 月 8 日」）。失败返回 None。"""
    if not value:
        return None
    m = _CN_DATE.match(value)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    for fmt in ("%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def sync_prebuilt_classification() -> int:
    """把预制分类（pre_classified）同步为 GameClassification（官方方案 TYPE）。

    哩米需求（2026-08-12）：自动分类结果作为元数据，扫描后直接读取，
    无需每次重新分类。来源标记 SYSTEM_PRE_DB，用户可手动覆盖（MANUAL）。
    """
    if not PREBUILT_DB.exists():
        return 0
    from client.models import Category, CategoryScheme, GameClassification
    from client.storage.database import get_session

    conn = sqlite3.connect(PREBUILT_DB)
    rows = conn.execute(
        "SELECT appid, category_slug FROM pre_classified WHERE category_slug IS NOT NULL"
    ).fetchall()
    conn.close()
    if not rows:
        return 0

    session = get_session()
    try:
        scheme = (
            session.query(CategoryScheme)
            .filter(CategoryScheme.source == "OFFICIAL",
                    CategoryScheme.del_flag.is_(False))
            .first()
        )
        if not scheme:
            return 0
        slug_to_cat = {
            c.slug: c for c in session.query(Category).filter(
                Category.scheme_id == scheme.id, Category.del_flag.is_(False)).all()
        }
        existing = {
            (gc.game_appid, gc.scheme_id): gc
            for gc in session.query(GameClassification).filter(
                GameClassification.dimension == "TYPE",
                GameClassification.del_flag.is_(False)).all()
        }
        synced = 0
        for appid, slug in rows:
            cat = slug_to_cat.get(slug)
            if not cat:
                continue
            key = (appid, scheme.id)
            record = existing.get(key)
            if record is None:
                record = GameClassification(
                    game_appid=appid, scheme_id=scheme.id,
                    dimension="TYPE", source="SYSTEM_PRE_DB",
                )
                session.add(record)
                existing[key] = record
            if record.source == "MANUAL":  # 用户手动覆盖优先，不覆盖
                continue
            record.category_id = cat.id
            record.source = "SYSTEM_PRE_DB"
            record.is_confirmed = False
            synced += 1
        session.commit()
        logger.info(f"预制分类同步完成: {synced} 条 → GameClassification（官方方案）")
        return synced
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def sync_prebuilt_styles() -> int:
    """把预置风格（pre_styles）同步为 GameClassification（STYLE 维度）。

    哩米 2026-08-14：R18/肉鸽等风格打标随包分发——新用户运行库为空，
    prebuilt 的 pre_styles 表驱动风格区开箱即用（打包产物必备）。
    """
    if not PREBUILT_DB.exists():
        return 0
    import sqlite3

    from client.models import CategoryScheme, GameClassification, Tag
    from client.storage.database import get_session

    conn = sqlite3.connect(PREBUILT_DB)
    rows = conn.execute("SELECT appid, tag_name FROM pre_styles").fetchall()
    conn.close()
    if not rows:
        return 0

    session = get_session()
    try:
        scheme = (
            session.query(CategoryScheme)
            .filter(CategoryScheme.source == "OFFICIAL",
                    CategoryScheme.del_flag.is_(False))
            .first()
        )
        if not scheme:
            return 0
        tag_map = {t.name: t.id for t in session.query(Tag).filter(
            Tag.scheme_id == scheme.id, Tag.dimension == "STYLE",
            Tag.del_flag.is_(False)).all()}
        existing = {
            (gc.game_appid, gc.tag_id)
            for gc in session.query(GameClassification).filter(
                GameClassification.scheme_id == scheme.id,
                GameClassification.dimension == "STYLE",
                GameClassification.del_flag.is_(False)).all()
        }
        synced = 0
        for appid, tag_name in rows:
            tag_id = tag_map.get(tag_name)
            if not tag_id or (appid, tag_id) in existing:
                continue
            session.add(GameClassification(
                game_appid=appid, scheme_id=scheme.id,
                dimension="STYLE", tag_id=tag_id, source="SYSTEM_PRE_DB"))
            existing.add((appid, tag_id))
            synced += 1
        session.commit()
        logger.info(f"预置风格同步完成: {synced} 条 → GameClassification（STYLE）")
        return synced
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def apply_delisted_style() -> int:
    """已下架游戏 → 官方方案「已下架」风格标签（哩米需求 2026-08-12）。

    对 app_type='delisted' 的游戏打 STYLE 标签（与肉鸽/R18 并列展示）。
    """
    from client.models import CategoryScheme, Game, GameClassification, Tag
    from client.storage.database import get_session

    session = get_session()
    try:
        scheme = (
            session.query(CategoryScheme)
            .filter(CategoryScheme.source == "OFFICIAL",
                    CategoryScheme.del_flag.is_(False))
            .first()
        )
        if not scheme:
            return 0
        tag = (
            session.query(Tag)
            .filter(Tag.scheme_id == scheme.id, Tag.dimension == "STYLE",
                    Tag.name == "已下架", Tag.del_flag.is_(False))
            .first()
        )
        if not tag:
            return 0
        delisted = {
            g.appid for g in session.query(Game).filter(
                Game.app_type == "delisted", Game.del_flag.is_(False)).all()
        }
        existing = {
            gc.game_appid for gc in session.query(GameClassification).filter(
                GameClassification.scheme_id == scheme.id,
                GameClassification.dimension == "STYLE",
                GameClassification.tag_id == tag.id,
                GameClassification.del_flag.is_(False)).all()
        }
        n = 0
        for appid in delisted - existing:
            session.add(GameClassification(
                game_appid=appid, scheme_id=scheme.id,
                dimension="STYLE", tag_id=tag.id, source="SYSTEM_PRE_DB"))
            n += 1
        session.commit()
        return n
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def apply_roguelike_style() -> int:
    """肉鸽游戏 → 官方方案「肉鸽」风格标签（哩米需求 2026-08-13）。

    识别：store_tags 社区标签含 roguelike/roguelite/rogue-like（含 action roguelike、
    traditional roguelike、roguelike deckbuilder 等变体）。
    """
    import json as _json

    from client.models import CategoryScheme, Game, GameClassification, Tag
    from client.storage.database import get_session

    session = get_session()
    try:
        scheme = (
            session.query(CategoryScheme)
            .filter(CategoryScheme.source == "OFFICIAL",
                    CategoryScheme.del_flag.is_(False))
            .first()
        )
        if not scheme:
            return 0
        tag = (
            session.query(Tag)
            .filter(Tag.scheme_id == scheme.id, Tag.dimension == "STYLE",
                    Tag.name == "肉鸽", Tag.del_flag.is_(False))
            .first()
        )
        if not tag:
            return 0
        roguelike_appids = set()
        # 2026-08-14：不过滤 app_type——demo 也是游戏（卡包修仙 Demo 曾漏标）
        for game in session.query(Game).filter(Game.del_flag.is_(False)).all():
            try:
                tags = _json.loads(game.steam_tags or "[]")
            except ValueError:
                continue
            if any("roguelike" in str(t).lower() or "roguelite" in str(t).lower()
                   or "rogue-like" in str(t).lower() for t in tags):
                roguelike_appids.add(game.appid)
        existing = {
            gc.game_appid for gc in session.query(GameClassification).filter(
                GameClassification.scheme_id == scheme.id,
                GameClassification.dimension == "STYLE",
                GameClassification.tag_id == tag.id,
                GameClassification.del_flag.is_(False)).all()
        }
        n = 0
        for appid in roguelike_appids - existing:
            session.add(GameClassification(
                game_appid=appid, scheme_id=scheme.id,
                dimension="STYLE", tag_id=tag.id, source="SYSTEM_PRE_DB"))
            n += 1
        session.commit()
        # 同步持久化到预置库（哩米 2026-08-14：打包产物需带风格）
        import sqlite3 as _sqlite3

        pconn = _sqlite3.connect(PREBUILT_DB)
        pconn.execute("""CREATE TABLE IF NOT EXISTS pre_styles (
            appid TEXT NOT NULL, tag_name TEXT NOT NULL,
            PRIMARY KEY (appid, tag_name))""")
        pconn.executemany(
            "INSERT OR REPLACE INTO pre_styles (appid, tag_name) VALUES (?, '肉鸽')",
            [(a,) for a in roguelike_appids])
        pconn.commit()
        pconn.close()
        logger.info(f"肉鸽风格打标完成: {len(roguelike_appids)} 款（新增 {n}，pre_styles 已同步）")
        return n
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def apply_party_style() -> int:
    """派对游戏 → 官方方案「派对」风格标签（哩米需求 2026-08-13）。

    识别：store_tags 社区标签含 party/party game/board game/tabletop/quiz/drawing
    （排除 party-based——那是「队伍制 RPG」标签，FF7RE/轨迹等 JRPG 会被误抓；
    纯 local multiplayer 不算——格斗/合作游戏非派对）。
    """
    import json as _json

    from client.models import CategoryScheme, Game, GameClassification, Tag
    from client.storage.database import get_session

    session = get_session()
    try:
        scheme = (
            session.query(CategoryScheme)
            .filter(CategoryScheme.source == "OFFICIAL",
                    CategoryScheme.del_flag.is_(False))
            .first()
        )
        if not scheme:
            return 0
        tag = (
            session.query(Tag)
            .filter(Tag.scheme_id == scheme.id, Tag.dimension == "STYLE",
                    Tag.name == "派对", Tag.del_flag.is_(False))
            .first()
        )
        if not tag:
            return 0
        keys = ("party", "board game", "tabletop", "quiz", "drawing")
        party_appids = set()
        for game in session.query(Game).filter(
                Game.del_flag.is_(False), Game.app_type == "game").all():
            try:
                tags = _json.loads(game.steam_tags or "[]")
            except ValueError:
                continue
            low = [str(t).lower() for t in tags]
            if any(k in t for t in low for k in keys) \
                    and not any("party-based" in t for t in low):
                party_appids.add(game.appid)
        existing = {
            gc.game_appid for gc in session.query(GameClassification).filter(
                GameClassification.scheme_id == scheme.id,
                GameClassification.dimension == "STYLE",
                GameClassification.tag_id == tag.id,
                GameClassification.del_flag.is_(False)).all()
        }
        n = 0
        for appid in party_appids - existing:
            session.add(GameClassification(
                game_appid=appid, scheme_id=scheme.id,
                dimension="STYLE", tag_id=tag.id, source="SYSTEM_PRE_DB"))
            n += 1
        session.commit()
        logger.info(f"派对风格打标完成: {len(party_appids)} 款（新增 {n}）")
        return n
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def sync_prebuilt_metadata() -> int:
    """把预制元数据同步进 MetadataCache 表（幂等 upsert）。返回同步条数。

    预制库缺失时不报错（仅提示）——应用仍可用，规则字段降级为空。
    """
    if not PREBUILT_DB.exists():
        logger.warning(f"预制元数据不存在: {PREBUILT_DB}（规则引擎字段将为空）")
        return 0

    from client.models import MetadataCache
    from client.storage.database import get_session

    conn = sqlite3.connect(PREBUILT_DB)
    rows = conn.execute(
        """SELECT appid, name, developer, publisher, release_date,
                  store_tags, genres, review_score, review_count, supported_languages
           FROM metadata_cache"""
    ).fetchall()
    # appinfo.vdf 类型覆盖（Tool/Beta/Demo 等非 Game，锁区/下架游戏也有）
    try:
        type_rows = conn.execute(
            "SELECT appid, app_type FROM app_type_override").fetchall()
    except sqlite3.OperationalError:
        type_rows = []  # 旧预置库无此表
    conn.close()

    session = get_session()
    try:
        existing = {m.appid: m for m in session.query(MetadataCache).all()}
        for (appid, name, developer, publisher, release_date,
             store_tags, genres, review_score, review_count, supported_languages) in rows:
            meta = existing.get(appid)
            if meta is None:
                meta = MetadataCache(appid=appid)
                session.add(meta)
            elif meta.del_flag:
                meta.del_flag = False  # 复活历史逻辑删除缓存
            meta.developer = developer
            meta.publisher = publisher
            meta.release_date = _parse_date(release_date)  # 中文日期 → Date 类型
            meta.store_tags = store_tags or "[]"
            meta.genres = genres or "[]"
            meta.review_score = review_score
            meta.review_count = review_count
            meta.supported_languages = supported_languages or "[]"
        # appinfo.vdf 类型覆盖 → games.app_type（哩米指出：工具软件不该当游戏）
        from client.models import Game

        type_map = {appid: t for appid, t in type_rows}
        if type_map:
            g_by_appid = {g.appid: g for g in session.query(Game).filter(
                Game.del_flag.is_(False)).all()}
            for appid, atype in type_map.items():
                game = g_by_appid.get(appid)
                if game and game.app_type == "game":
                    game.app_type = atype.lower()  # Tool→tool, Demo→demo
        # 社区标签同步 → games.steam_tags（规则引擎 gdict 数据源；
        # 2026-08-13 补：build_store_tags 更新预置库后需同步到 games 表）
        # rows: appid(0) name(1) developer(2) publisher(3) release_date(4)
        #       store_tags(5) genres(6) ...
        tags_map = {row[0]: row[5] for row in rows}
        g_by_appid = {g.appid: g for g in session.query(Game).filter(
            Game.del_flag.is_(False)).all()}
        for appid, tags in tags_map.items():
            game = g_by_appid.get(appid)
            if game and tags:
                game.steam_tags = tags
        session.commit()
        logger.info(f"预制元数据同步完成: {len(rows)} 条（含 {len(type_map)} 款类型覆盖）")
        return len(rows)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def sync_prebuilt_names() -> int:
    """预置库 name → games 表占位名替换（`[appid]` → 真名）。返回替换条数。

    背景（2026-08-13 P2）：扫描 fetch_names=False 时名字缺失用 [appid] 占位
    （scanner.py:97）；占位替换原在 UI 元数据下载流程（main_window.py:135），
    无 UI 流程时正式库 1010/1183 款名字为占位符，影响展示与 TAG_MAP 名称匹配。
    """
    if not PREBUILT_DB.exists():
        return 0

    from client.models import Game
    from client.storage.database import get_session

    conn = sqlite3.connect(PREBUILT_DB)
    rows = conn.execute(
        "SELECT appid, name FROM metadata_cache WHERE name IS NOT NULL AND name != ''"
    ).fetchall()
    conn.close()

    session = get_session()
    try:
        by_appid = {appid: name for appid, name in rows}
        games = session.query(Game).filter(Game.del_flag.is_(False)).all()
        replaced = 0
        for game in games:
            if game.name and not game.name.startswith("["):
                continue  # 已有真名不动
            real = by_appid.get(game.appid)
            if real:
                game.name = real
                replaced += 1
        session.commit()
        logger.info(f"占位名替换完成: {replaced} 款")
        return replaced
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
