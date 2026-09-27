"""M2.2 分类编辑器测试：增删改 + 官方只读。"""
import pytest

from client.core.category_editor import (
    EditorError,
    add_category,
    add_tag,
    delete_category,
    delete_tag,
    rename_category,
)
from client.core.scheme_service import create_user_scheme, get_scheme_tree, load_official_scheme


def test_official_scheme_readonly() -> None:
    """官方方案只读：增/删/改均拒绝。"""
    official = load_official_scheme()
    with pytest.raises(EditorError, match="官方方案只读"):
        add_category(official.id, "测试类")
    node = get_scheme_tree(official.id)[0]
    with pytest.raises(EditorError, match="官方方案只读"):
        rename_category(node.id, "改名")
    with pytest.raises(EditorError, match="官方方案只读"):
        delete_category(node.id)


def test_add_rename_delete_user_category() -> None:
    """自己方案：顶层新增 → 子节点新增 → 重命名（子树 path 同步）→ 删除。"""
    user = create_user_scheme("编辑器测试")
    top = add_category(user.id, "我的大类", icon="⭐")
    assert top.level == 0 and top.is_leaf
    child = add_category(user.id, "子类", parent_id=top.id)
    assert child.level == 1 and child.path == "我的大类/子类"

    rename_category(top.id, "我的大类V2")
    tree = get_scheme_tree(user.id)
    child_after = next(c for c in tree if c.level == 1)
    assert child_after.path == "我的大类V2/子类"  # 子树 path 同步更新

    with pytest.raises(EditorError, match="子分类"):
        delete_category(top.id)  # 有子节点拒绝
    delete_category(child.id)
    delete_category(top.id)
    assert all(c.del_flag for c in get_scheme_tree(user.id))


def test_add_delete_tag() -> None:
    """风格标签增删。"""
    user = create_user_scheme("标签测试")
    tag = add_tag(user.id, "我流标签")
    assert tag.dimension == "STYLE" and tag.source == "USER_CREATED"
    delete_tag(tag.id)
    from client.core.scheme_service import get_scheme_styles

    assert get_scheme_styles(user.id) == []
