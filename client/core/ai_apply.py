"""应用 AI 建议（哩米 2026-09-27）：用户确认后再写元数据与分类。

AI 结果不再直接入库：AIFillWorker 只收集建议，主窗口弹出 AIResultsDialog，
用户勾选确认后调用本模块 apply_ai_suggestions()。
"""
import json
from typing import Any

from loguru import logger


def apply_ai_suggestions(suggestions: list[dict[str, Any]]) -> tuple[int, int]:
    """把确认后的 AI 建议写入 Game / MetadataCache / GameClassification。

    返回 (补全元数据数, 分类数)。不会覆盖用户手动分类（source=MANUAL）。
    """
    from client.core.scheme_service import get_active_scheme
    from client.models import Category, Game, GameClassification, MetadataCache
    from client.storage.database import get_session

    scheme = get_active_scheme()
    if scheme is None:
        return 0, 0

    filled = 0
    classified = 0
    session = get_session()
    try:
        for item in suggestions:
            appid = str(item.get("appid", ""))
            if not appid:
                continue
            game = session.query(Game).filter(
                Game.appid == appid, Game.del_flag.is_(False)).first()
            if game is None:
                continue

            new_name = str(item.get("name") or "").strip()
            if new_name and (not game.name or game.name.startswith("[")):
                game.name = new_name  # 只覆盖占位名，不覆盖真实名称
            if item.get("type"):
                game.app_type = str(item["type"])
            if item.get("developer"):
                game.developer = str(item["developer"])
            if item.get("publisher"):
                game.publisher = str(item["publisher"])
            genres = item.get("genres") or []
            if genres and (not game.steam_tags or game.steam_tags == "[]"):
                game.steam_tags = json.dumps(genres, ensure_ascii=False)

            mc = session.query(MetadataCache).filter(
                MetadataCache.appid == appid).first()
            if mc is None:
                mc = MetadataCache(appid=appid)
                session.add(mc)
            elif mc.del_flag:
                mc.del_flag = False  # 复活历史逻辑删除缓存
            if genres:
                tags_json = json.dumps(genres, ensure_ascii=False)
                mc.store_tags = tags_json
                mc.genres = tags_json
            if item.get("developer"):
                mc.developer = str(item["developer"])
            if item.get("publisher"):
                mc.publisher = str(item["publisher"])
            filled += 1

            slug = item.get("category_slug") or ""
            if not slug:
                continue
            # 用户手动分类优先，不覆盖
            manual = session.query(GameClassification).filter(
                GameClassification.game_appid == appid,
                GameClassification.scheme_id == scheme.id,
                GameClassification.dimension == "TYPE",
                GameClassification.del_flag.is_(False),
                GameClassification.source == "MANUAL",
            ).first()
            if manual is not None:
                continue
            cat = session.query(Category).filter(
                Category.scheme_id == scheme.id,
                Category.slug == slug,
                Category.del_flag.is_(False),
            ).first()
            if cat is None:
                continue
            record = session.query(GameClassification).filter(
                GameClassification.game_appid == appid,
                GameClassification.scheme_id == scheme.id,
                GameClassification.dimension == "TYPE",
                GameClassification.del_flag.is_(False),
            ).first()
            source = "AI" if item.get("source") == "AI" else "USER_RULE"
            if record is None:
                record = GameClassification(
                    game_appid=appid, scheme_id=scheme.id,
                    dimension="TYPE", source=source, is_confirmed=True,
                )
                session.add(record)
            record.category_id = cat.id
            record.source = source
            record.is_confirmed = True
            classified += 1

        session.commit()
        logger.info(f"AI 建议已应用: 元数据 {filled} 款, 分类 {classified} 款")
        return filled, classified
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
