"""分类编辑器 Service（M2.2）：自己方案树增删改；官方方案只读（PRD M2.2）。

MVP 范围（计划默认决策）：树形增删改（非拖拽）；删除有子节点的分类需先删子节点。
"""
from loguru import logger

from client.models import Category, CategoryScheme, Tag
from client.storage.database import get_session


class EditorError(ValueError):
    pass


def _ensure_editable(session, scheme_id: str) -> CategoryScheme:
    """官方方案只读校验。"""
    scheme = session.get(CategoryScheme, scheme_id)
    if not scheme or scheme.del_flag:
        raise EditorError(f"方案不存在: {scheme_id}")
    if scheme.source == "OFFICIAL":
        raise EditorError("官方方案只读，请在「自己」方案中编辑")
    return scheme


def add_category(scheme_id: str, name: str, parent_id: str | None = None,
                 icon: str | None = None) -> Category:
    """新增分类节点（自己方案）。parent 为空则作为顶层大类。"""
    session = get_session()
    try:
        _ensure_editable(session, scheme_id)
        level, path, sort_order = 0, name, 0
        if parent_id:
            parent = session.get(Category, parent_id)
            if not parent or parent.del_flag or parent.scheme_id != scheme_id:
                raise EditorError(f"父节点不存在: {parent_id}")
            level = parent.level + 1
            path = f"{parent.path}/{name}"
            siblings = (
                session.query(Category)
                .filter(Category.parent_id == parent_id, Category.del_flag.is_(False))
                .count()
            )
            sort_order = siblings
        else:
            siblings = (
                session.query(Category)
                .filter(Category.scheme_id == scheme_id, Category.parent_id.is_(None),
                        Category.del_flag.is_(False))
                .count()
            )
            sort_order = siblings
        node = Category(
            scheme_id=scheme_id,
            name=name,
            slug=f"user-{name}-{sort_order}",
            parent_id=parent_id,
            level=level,
            path=path,
            icon=icon,
            source="USER_CREATED",
            sort_order=sort_order,
            is_leaf=True,
        )
        session.add(node)
        session.commit()
        logger.info(f"新增分类: {path}（方案 {scheme_id[:8]}）")
        return node
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def rename_category(category_id: str, new_name: str) -> None:
    """重命名（同步更新子树 path）。"""
    session = get_session()
    try:
        node = session.get(Category, category_id)
        if not node or node.del_flag:
            raise EditorError("节点不存在")
        _ensure_editable(session, node.scheme_id)
        old_path = node.path
        node.name = new_name
        node.path = f"{node.path.rsplit('/', 1)[0]}/{new_name}" if "/" in node.path else new_name
        # 子树 path 前缀更新
        children = (
            session.query(Category)
            .filter(Category.scheme_id == node.scheme_id, Category.del_flag.is_(False))
            .filter(Category.path.like(f"{old_path}/%"))
            .all()
        )
        for child in children:
            child.path = child.path.replace(f"{old_path}/", f"{node.path}/", 1)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def delete_category(category_id: str) -> None:
    """删除分类（有子节点时拒绝；逻辑删除 del_flag）。"""
    session = get_session()
    try:
        node = session.get(Category, category_id)
        if not node or node.del_flag:
            raise EditorError("节点不存在")
        _ensure_editable(session, node.scheme_id)
        has_children = (
            session.query(Category)
            .filter(Category.parent_id == node.id, Category.del_flag.is_(False))
            .count()
        )
        if has_children:
            raise EditorError(f"「{node.name}」存在子分类，请先删除子分类")
        node.del_flag = True
        session.commit()
        logger.info(f"删除分类: {node.path}")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def add_tag(scheme_id: str, name: str, dimension: str = "STYLE") -> Tag:
    """新增标签（风格/评价/状态）。"""
    session = get_session()
    try:
        _ensure_editable(session, scheme_id)
        count = (
            session.query(Tag)
            .filter(Tag.scheme_id == scheme_id, Tag.dimension == dimension,
                    Tag.del_flag.is_(False))
            .count()
        )
        tag = Tag(
            scheme_id=scheme_id,
            name=name,
            dimension=dimension,
            source="USER_CREATED",
            sort_order=count,
        )
        session.add(tag)
        session.commit()
        return tag
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def delete_tag(tag_id: str) -> None:
    session = get_session()
    try:
        tag = session.get(Tag, tag_id)
        if not tag or tag.del_flag:
            raise EditorError("标签不存在")
        _ensure_editable(session, tag.scheme_id)
        tag.del_flag = True
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
