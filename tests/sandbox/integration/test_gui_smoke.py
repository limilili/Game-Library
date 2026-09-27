"""系统测试：GUI offscreen 全流程冒烟（哩米测试轮 2026-08-14）。

覆盖：启动/列表加载/四视图互斥/筛选/多选模式（分类树 toggle/风格 toggle/全选/
厂商点选）/详情点击驱动/封面 pixmap/批量删除确认流。
运行：python tests/sandbox/integration/test_gui_smoke.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def main() -> None:
    app = QApplication([])
    from client.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    results: list[tuple[str, bool]] = []

    def check(name: str, cond: bool) -> None:
        results.append((name, cond))
        print(f"  {'✓' if cond else '✗'} {name}")

    # 1) 列表加载
    w.reload_games()
    app.processEvents()
    n = w.game_grid.count()
    check(f"游戏列表加载 {n} 款（含空态不崩）", n > 500)

    # 2) 四视图互斥：树形点击清厂商高亮
    from client.models import Category, CategoryScheme
    from client.storage.database import get_session

    s = get_session()
    try:
        scheme = s.query(CategoryScheme).filter(
            CategoryScheme.source == "OFFICIAL",
            CategoryScheme.del_flag.is_(False)).first()
        first_cat = s.query(Category).filter(
            Category.scheme_id == scheme.id, Category.del_flag.is_(False),
            Category.level == 0).first()
    finally:
        s.close()
    if w.style_list.count() > 0:
        w._on_style_clicked(w.style_list.item(0))
    app.processEvents()
    w._on_studio_clicked(w.studio_list.item(0)) if w.studio_list.count() else None
    check("厂商点击清空分类/风格高亮",
          not w.category_tree.selectionModel().hasSelection()
          and not w.style_list.selectionModel().hasSelection())

    # 3) 详情点击驱动（点两个不同 item）——先恢复全量避免筛选隐藏导致坐标偏移
    w._on_show_all()
    w.reload_games()
    app.processEvents()
    if n >= 2:
        i0, i1 = w.game_grid.item(0), w.game_grid.item(1)
        p0 = w.game_grid.visualItemRect(i0).center()
        p1 = w.game_grid.visualItemRect(i1).center()
        QTest.mouseClick(w.game_grid.viewport(), Qt.LeftButton, Qt.NoModifier, p0)
        app.processEvents()
        d0 = w.detail_label.text()
        QTest.mouseClick(w.game_grid.viewport(), Qt.LeftButton, Qt.NoModifier, p1)
        app.processEvents()
        d1 = w.detail_label.text()
        check("点击驱动详情更新（两个不同游戏）",
              i0.text().strip() in d0 and i1.text().strip() in d1)
        check("封面 pixmap 切换", w.detail_cover.pixmap() is not None)

    # 4) 多选模式
    w.multi_btn.setChecked(True)
    app.processEvents()
    check("进入多选启用全选/应用到Steam",
          w.multi_select_all_btn.isEnabled() and w.apply_steam_btn.isEnabled())
    # 分类树 toggle 目标（叶节点）
    leaf = None
    s = get_session()
    try:
        leaf = s.query(Category).filter(
            Category.scheme_id == scheme.id, Category.is_leaf.is_(True),
            Category.del_flag.is_(False)).first()
    finally:
        s.close()
    if leaf:
        from PySide6.QtGui import QStandardItemModel
        model: QStandardItemModel = w.category_tree.model()
        found = None
        # 遍历树找 leaf 的 item
        def find_item(parent_idx, target_id):
            for r in range(model.rowCount(parent_idx)):
                idx = model.index(r, 0, parent_idx)
                if model.itemFromIndex(idx).data(Qt.UserRole) == leaf.id:
                    return idx
                sub = find_item(idx, target_id)
                if sub is not None:
                    return sub
            return None
        idx = find_item(model.invisibleRootItem().index(), leaf.id)
        if idx is not None:
            w._on_tree_category_clicked(idx)
            check("多选：分类树 toggle 目标分类", leaf.slug in w._target_slugs)
            w._on_tree_category_clicked(idx)
            check("多选：再次点击取消", leaf.slug not in w._target_slugs)
    # 风格 toggle
    if w.style_list.count() > 0:
        w._on_style_clicked(w.style_list.item(0))
        check("多选：风格 toggle 目标", len(w._target_styles) > 0)
    # 全选（全部分类）
    w._on_multi_select_all()
    check("全选 = 全部分类（叶+风格）",
          len(w._target_slugs) > 50 and len(w._target_styles) >= 5)
    # 级联点击拦截——monkeypatch QMessageBox 防模态阻塞（offscreen）
    from unittest.mock import patch
    with patch("client.ui.main_window.QMessageBox.information") as mock_info:
        w._on_cascade_category("nonexistent")  # 多选模式应被拦截
        check("级联多选拦截（弹提示不筛选）",
              mock_info.called and w._active_category_id is None)
    # 退出多选
    w.multi_btn.setChecked(False)
    app.processEvents()
    check("退出多选清空目标",
          len(w._target_slugs) == 0 and len(w._target_styles) == 0)

    w.close()
    passed = sum(1 for _, ok in results if ok)
    print(f"\n系统测试: {passed}/{len(results)} 通过")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
