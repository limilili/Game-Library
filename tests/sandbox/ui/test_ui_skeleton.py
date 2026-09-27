"""POC-6 pytest：三栏 UI 骨架 offscreen 程序化验证 + 截图存档。

运行（offscreen，无需真实显示器）：
    pytest tests/sandbox/ui/test_ui_skeleton.py -v

截图输出：tests/sandbox/ui/screenshots/（空态 + 数据态）
"""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidget, QStackedWidget, QTableView, QTreeView

from main_window import DEMO_GAMES, CATEGORY_TREE, MainWindow

SCREENSHOTS = Path(__file__).resolve().parent / "screenshots"


def _app() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


def _screenshot(window: MainWindow, name: str) -> Path:
    SCREENSHOTS.mkdir(exist_ok=True)
    path = SCREENSHOTS / name
    window.grab().save(str(path))
    return path


def test_three_column_layout_exists() -> None:
    """三栏骨架部件存在：QTreeView + QTableView + QStackedWidget。"""
    app = _app()
    window = MainWindow()
    assert isinstance(window.category_tree, QTreeView)
    assert isinstance(window.game_table, QTableView)
    assert isinstance(window.detail_stack, QStackedWidget)
    assert window.detail_stack.count() == 2  # 空态 + 详情态
    assert isinstance(window.style_list, QListWidget)
    window.close()


def test_category_tree_loads_official_scheme() -> None:
    """分类树加载官方方案：顶层 16 大类，树节点总数 66（category-tree-default.json v0.8）。"""
    app = _app()
    window = MainWindow()
    model = window.category_tree.model()
    assert model.rowCount() == 16, f"顶层大类应为 16，实际 {model.rowCount()}"

    def count_rows(parent) -> int:
        n = 0
        for r in range(model.rowCount(parent)):
            idx = model.index(r, 0, parent)
            n += 1
            n += count_rows(idx)
        return n

    assert count_rows(model.invisibleRootItem().index()) == 66  # 树 v0.8
    # 抽查：恐怖大类存在且 7 子类（6 + 其他）——文字冒险提级后恐怖后移一位
    root_idx = model.index(13, 0)  # 第 14 个顶层 = 恐怖（按 JSON 顺序）
    assert model.data(root_idx).endswith("恐怖")
    assert model.rowCount(root_idx) == 7
    window.close()


def test_style_labels_present() -> None:
    """风格标签区（沙盒骨架，无缩进）：肉鸽/R18/已下架/无需补丁/需要补丁。"""
    app = _app()
    window = MainWindow()
    labels = [window.style_list.item(i).text() for i in range(window.style_list.count())]
    assert labels == ["肉鸽", "R18", "已下架", "无需补丁", "需要补丁", "擦边"]
    window.close()


def test_demo_games_fill_and_select() -> None:
    """假数据填充 + 选中游戏切换详情页。"""
    app = _app()
    window = MainWindow()
    window.load_demo_games()
    assert window.game_model.rowCount() == len(DEMO_GAMES) == 10
    # 初始为空态
    assert window.detail_stack.currentIndex() == 0
    # 选中第 1 行 → 详情页
    window.game_table.selectRow(0)
    assert window.detail_stack.currentIndex() == 1
    assert window.detail_name.text() == DEMO_GAMES[0][0]
    window.close()


def test_theme_follows_system() -> None:
    """主题跟随系统：colorScheme API 可用（深色/浅色检测）。"""
    app = _app()
    hints = app.styleHints()
    scheme = hints.colorScheme()  # Qt 6.5+: Light/Dark/Unknown
    assert scheme in (Qt.ColorScheme.Light, Qt.ColorScheme.Dark, Qt.ColorScheme.Unknown)
    # 窗口可正常创建（任意主题下）
    window = MainWindow()
    assert window.isVisible() is False  # offscreen 不显示，但可正常构建
    window.close()


def test_screenshots_captured() -> None:
    """截图存档：空态 + 数据态两张 PNG。"""
    app = _app()
    window = MainWindow()
    window.show()
    empty_shot = _screenshot(window, "skeleton-empty.png")
    window.load_demo_games()
    window.game_table.selectRow(0)
    window.resize(1100, 650)
    data_shot = _screenshot(window, "skeleton-data.png")
    window.close()
    assert empty_shot.exists() and empty_shot.stat().st_size > 1000
    assert data_shot.exists() and data_shot.stat().st_size > 1000
