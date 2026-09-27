"""分类方案服务（M2.1 加载 + M2.10 管理）：官方/自己/其他三档切换式（v1.3）。

- load_official_scheme()：category-tree-default.json → DB（方案 + 类型树 + 风格标签）
- activate_scheme / create_user_scheme（复制官方起步，US-M2-5）
- 风格标签存 Tag 表 dimension="STYLE"（v1.3 扩展；开关机制 UI 层实现）
"""
import json
from pathlib import Path
from typing import Any

from loguru import logger

from client.models import Category, CategoryScheme, Tag
from client.storage.database import get_session

DEFAULT_TREE = (
    Path(__file__).resolve().parent.parent.parent / "docs" / "category-tree-default.json"
)
DEFAULT_RULES = (
    Path(__file__).resolve().parent.parent.parent
    / "client" / "resources" / "rules_default.json"
)


def load_official_scheme(tree_path: Path | None = None) -> CategoryScheme:
    """官方方案加载：JSON → DB（幂等；JSON version 变更时重建——树结构调整）。

    返回官方方案（首次加载时设为激活方案）。树/风格就绪后 seed 官方默认规则。
    """
    tree_path = tree_path or DEFAULT_TREE
    session = get_session()
    try:
        data = json.loads(tree_path.read_text(encoding="utf-8"))
        scheme_meta = data["scheme"]
        existing = (
            session.query(CategoryScheme)
            .filter(CategoryScheme.source == "OFFICIAL", CategoryScheme.del_flag.is_(False))
            .first()
        )
        if existing:
            if existing.version == scheme_meta.get("version", "0.1"):
                # 幂等：树相同；styles 增量补缺（不重建树，避免破坏分类关联）
                _sync_styles(session, existing.id, data.get("styles", {}).get("items", []))
                logger.info(f"官方方案已存在: {existing.name}（跳过重建，styles 增量）")
                seed_default_rules(session, existing.id)
                return existing
            # JSON 版本变更 → 重建（逻辑删除旧树/标签，新建）
            logger.info(f"官方方案版本变更 {existing.version} → {scheme_meta.get('version')}，重建")
            session.query(Category).filter(Category.scheme_id == existing.id).update(
                {"del_flag": True})
            session.query(Tag).filter(Tag.scheme_id == existing.id).update(
                {"del_flag": True})
            existing.version = scheme_meta.get("version", "0.1")
            existing.name = scheme_meta["name"]
            scheme = existing
            session.commit()
        else:
            scheme = CategoryScheme(
                name=scheme_meta["name"],
                source="OFFICIAL",
                version=scheme_meta.get("version", "0.1"),
                is_active=True,  # 首次加载即激活
            )
            session.add(scheme)
            session.flush()

        # 类型树递归入库（先父后子；level/path/sort_order 按 JSON 顺序）
        def insert_tree(parent_id: str | None, nodes: list[dict], level: int, path: str) -> None:
            for order, node in enumerate(nodes):
                node_path = f"{path}/{node['name']}" if path else node["name"]
                category = Category(
                    scheme_id=scheme.id,
                    name=node["name"],
                    slug=node["id"],
                    parent_id=parent_id,
                    level=level,
                    path=node_path,
                    icon=node.get("icon"),
                    source="OFFICIAL",
                    sort_order=order,
                    is_leaf=node.get("is_leaf", False),
                )
                session.add(category)
                session.flush()
                if "children" in node:
                    insert_tree(category.id, node["children"], level + 1, node_path)

        insert_tree(None, data["type_tree"]["root"], 0, "")

        # 风格标签（dimension=STYLE，v1.3）
        for item in data["styles"]["items"]:
            session.add(
                Tag(
                    scheme_id=scheme.id,
                    name=item["name"],
                    dimension="STYLE",
                    source="OFFICIAL",
                    sort_order=0,
                )
            )
        session.commit()
        logger.info(
            f"官方方案加载完成: {data['type_tree']['name']} 树 + {len(data['styles']['items'])} 风格"
        )
        seed_default_rules(session, scheme.id)
        return scheme
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def seed_default_rules(session, scheme_id: str) -> int:
    """官方默认规则 seed（幂等：按 name 跳过已存在）。

    rules_default.json 的开发商高置信规则 → ClassificationRule（source=OFFICIAL）。
    用户可编辑/停用/删除；用户规则（priority 100）低于官方（50）。
    """
    from client.models import ClassificationRule

    if not DEFAULT_RULES.exists():
        return 0
    data = json.loads(DEFAULT_RULES.read_text(encoding="utf-8"))
    existing_names = {
        r.name for r in session.query(ClassificationRule).filter(
            ClassificationRule.scheme_id == scheme_id,
            ClassificationRule.source == "OFFICIAL",
            ClassificationRule.del_flag.is_(False)).all()
    }
    # slug → 分类 id（规则目标）
    slug_map = {
        c.slug: c.id for c in session.query(Category).filter(
            Category.scheme_id == scheme_id, Category.del_flag.is_(False)).all()
    }
    seeded = 0
    for item in data.get("rules", []):
        if item["name"] in existing_names:
            continue
        cat_id = slug_map.get(item.get("target_category_slug"))
        if cat_id is None:
            logger.warning(f"默认规则 {item['name']} 的目标节点不存在，跳过")
            continue
        session.add(
            ClassificationRule(
                scheme_id=scheme_id,
                name=item["name"],
                dimension="TYPE",
                target_category_id=cat_id,
                conditions=json.dumps(item["conditions"], ensure_ascii=False),
                priority=item.get("priority", 50),
                is_enabled=True,
                source="OFFICIAL",
            )
        )
        seeded += 1
    if seeded:
        session.commit()
        logger.info(f"官方默认规则 seed: {seeded} 条")
    return seeded


def _sync_styles(session, scheme_id: str, items: list[dict]) -> None:
    """styles 增量补缺（树不变时不重建，哩米需求：新增「已下架」风格标签）。"""
    existing_names = {
        t.name for t in session.query(Tag).filter(
            Tag.scheme_id == scheme_id, Tag.dimension == "STYLE",
            Tag.del_flag.is_(False)).all()
    }
    for item in items:
        if item["name"] not in existing_names:
            session.add(
                Tag(
                    scheme_id=scheme_id,
                    name=item["name"],
                    dimension="STYLE",
                    source="OFFICIAL",
                    sort_order=0,
                )
            )
    if any(i["name"] not in existing_names for i in items):
        session.commit()


def list_schemes() -> list[CategoryScheme]:
    session = get_session()
    try:
        return (
            session.query(CategoryScheme)
            .filter(CategoryScheme.del_flag.is_(False))
            .order_by(CategoryScheme.created_at)
            .all()
        )
    finally:
        session.close()


def get_active_scheme() -> CategoryScheme | None:
    session = get_session()
    try:
        return (
            session.query(CategoryScheme)
            .filter(CategoryScheme.is_active.is_(True), CategoryScheme.del_flag.is_(False))
            .first()
        )
    finally:
        session.close()


def activate_scheme(scheme_id: str) -> None:
    """切换激活方案（全局唯一激活）。"""
    session = get_session()
    try:
        session.query(CategoryScheme).filter(
            CategoryScheme.del_flag.is_(False)
        ).update({"is_active": False})
        scheme = session.get(CategoryScheme, scheme_id)
        if not scheme or scheme.del_flag:
            raise ValueError(f"方案不存在: {scheme_id}")
        scheme.is_active = True
        session.commit()
        logger.info(f"已切换激活方案: {scheme.name}")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_user_scheme(name: str, copy_from: CategoryScheme | None = None) -> CategoryScheme:
    """新建「自己」方案；copy_from 提供时深度复制其树/标签（US-M2-5 复制起步）。

    copy_from 为 None 时创建空白方案（树为空，用户自行构建）。
    """
    session = get_session()
    try:
        scheme = CategoryScheme(
            name=name,
            source="USER",
            is_active=False,
            copied_from_scheme_id=copy_from.id if copy_from else None,
        )
        session.add(scheme)
        session.flush()

        if copy_from:
            # 复制树（新 UUID；source=USER_CREATED；保持结构）
            id_map: dict[str, str] = {}
            categories = (
                session.query(Category)
                .filter(Category.scheme_id == copy_from.id, Category.del_flag.is_(False))
                .order_by(Category.level, Category.sort_order)
                .all()
            )
            for src in categories:
                new = Category(
                    scheme_id=scheme.id,
                    name=src.name,
                    slug=src.slug,
                    parent_id=id_map.get(src.parent_id) if src.parent_id else None,
                    level=src.level,
                    path=src.path,
                    icon=src.icon,
                    source="USER_CREATED",
                    sort_order=src.sort_order,
                    is_leaf=src.is_leaf,
                )
                session.add(new)
                session.flush()
                id_map[src.id] = new.id
            # 复制风格/评价/状态标签
            for src in (
                session.query(Tag)
                .filter(Tag.scheme_id == copy_from.id, Tag.del_flag.is_(False))
                .all()
            ):
                session.add(
                    Tag(
                        scheme_id=scheme.id,
                        name=src.name,
                        dimension=src.dimension,
                        source="USER_CREATED",
                        sort_order=src.sort_order,
                    )
                )
        session.commit()
        logger.info(f"新建自己方案: {name}（复制自 {copy_from.name if copy_from else '空'}）")
        return scheme
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_scheme_tree(scheme_id: str) -> list[Category]:
    """方案类型树（按 level/sort_order 排序，供 UI 渲染）。"""
    session = get_session()
    try:
        return (
            session.query(Category)
            .filter(Category.scheme_id == scheme_id, Category.del_flag.is_(False))
            .order_by(Category.level, Category.sort_order)
            .all()
        )
    finally:
        session.close()


def get_scheme_styles(scheme_id: str) -> list[Tag]:
    """方案风格标签（dimension=STYLE）。"""
    session = get_session()
    try:
        return (
            session.query(Tag)
            .filter(
                Tag.scheme_id == scheme_id,
                Tag.dimension == "STYLE",
                Tag.del_flag.is_(False),
            )
            .order_by(Tag.sort_order)
            .all()
        )
    finally:
        session.close()


def count_classifications_by_category(scheme_id: str) -> dict[str, int]:
    """统计方案内各分类节点的游戏数量（含子树聚合；哩米需求 2026-08-12）。

    返回 {category_id: 游戏数}——叶节点为直接挂载数，父节点为其子树总和。
    哩米 2026-08-14：风格标签游戏（R18/肉鸽等 STYLE 维度）**不入分类树/级联**
    ——计数同样排除，与筛选行为一致（风格游戏只在风格区查看）。
    """
    from client.models import Category, GameClassification, Tag

    session = get_session()
    try:
        categories = (
            session.query(Category)
            .filter(Category.scheme_id == scheme_id, Category.del_flag.is_(False))
            .all()
        )
        # 风格标签游戏集合（分类树/级联计数排除，哩米 2026-08-14）
        style_tag_ids = [t.id for t in session.query(Tag).filter(
            Tag.scheme_id == scheme_id, Tag.dimension == "STYLE",
            Tag.del_flag.is_(False)).all()]
        style_styled: set[str] = set()
        if style_tag_ids:
            style_styled = {
                gc.game_appid for gc in session.query(GameClassification).filter(
                    GameClassification.scheme_id == scheme_id,
                    GameClassification.dimension == "STYLE",
                    GameClassification.tag_id.in_(style_tag_ids),
                    GameClassification.del_flag.is_(False)).all()
            }
        # 直接挂载数（TYPE 维度，去重 appid，只统计已入库游戏）
        direct: dict[str, int] = {}
        from client.models import Game
        valid_appids = {
            g.appid for g in session.query(Game.appid).filter(
                Game.del_flag.is_(False)).all()
        }
        rows = (
            session.query(GameClassification.category_id, GameClassification.game_appid)
            .filter(
                GameClassification.scheme_id == scheme_id,
                GameClassification.dimension == "TYPE",
                GameClassification.del_flag.is_(False),
                GameClassification.category_id.isnot(None),
            )
            .all()
        )
        seen: dict[str, set[str]] = {}
        for category_id, appid in rows:
            if appid not in valid_appids:
                continue  # 预置元数据里有但用户库里没有的游戏不计入
            if appid in style_styled:
                continue  # 风格游戏不入分类树/级联（哩米 2026-08-14）
            seen.setdefault(category_id, set()).add(appid)
        direct = {cid: len(apps) for cid, apps in seen.items()}

        # 子树聚合（父 = 所有后代直接数之和，用集合去重）
        children_of: dict[str | None, list[Category]] = {}
        for c in categories:
            children_of.setdefault(c.parent_id, []).append(c)

        def aggregate(node: Category) -> set[str]:
            total = set(seen.get(node.id, set()))
            for child in children_of.get(node.id, []):
                total |= aggregate(child)
            return total

        result: dict[str, int] = {}
        for c in categories:
            result[c.id] = len(aggregate(c))
        return result
    finally:
        session.close()
