"""级联分类视图测试（哩米需求 2026-08-12）：大类 → 点击右推子类 → 逐级。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from client.core.scheme_service import get_scheme_tree, load_official_scheme
from client.ui.cascade_tree import CascadeTree


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_cascade_columns_advance() -> None:
    """点击大类 → 右侧出现子类列；点击子类 → 再右推；无子类则不再推。"""
    load_official_scheme()
    scheme = load_official_scheme()
    categories = get_scheme_tree(scheme.id)
    counts = {c.id: 1 for c in categories}
    app = _app()
    tree = CascadeTree(categories, counts)

    assert len(tree._columns) == 1  # 初始只有第一列
    first = tree._columns[0]
    assert first.count() == 16  # 顶层 16 大类（文字冒险提级 v0.8）

    # 点击 RPG（第 1 项）→ 第二列出现 JRPG/WRPG/SRPG/MMORPG/其他RPG
    rpg_item = first.item(0)
    tree._on_column_click(first, rpg_item, None)
    assert len(tree._columns) == 2
    second = tree._columns[1]
    assert second.count() == 5

    # 点击 JRPG → 无子类（树瘦身 v0.5：仅保留 JRPG 大类）→ 不右推
    jrpg_item = second.item(0)
    tree._on_column_click(second, jrpg_item, None)
    assert len(tree._columns) == 2

    # 点击 WRPG → 右推（WRPG 仍有子类）
    wrpg_item = second.item(1)
    tree._on_column_click(second, wrpg_item, None)
    assert len(tree._columns) == 3
    third = tree._columns[2]

    # 点击第二列其他项 → 第三列被截断重建
    tree._on_column_click(second, second.item(1), None)  # WRPG
    assert len(tree._columns) == 3
    assert tree._columns[2].count() == 4  # 开放世界/CRPG/ARPG/其他WRPG
    tree.close()


def test_cascade_reset() -> None:
    """reset：数据刷新后重建第一列。"""
    load_official_scheme()
    scheme = load_official_scheme()
    categories = get_scheme_tree(scheme.id)
    app = _app()
    tree = CascadeTree(categories, {})
    tree.reset(categories, {c.id: 2 for c in categories})
    assert tree._columns[0].count() == 16
    assert "（2）" in tree._columns[0].item(0).text()  # 数量显示
    tree.close()
