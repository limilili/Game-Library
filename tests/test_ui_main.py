"""M4 UI 冒烟测试（offscreen）：三栏渲染 + 方案切换 + 游戏列表加载。

运行：pytest tests/test_ui_main.py -v
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox, QListWidget, QTreeView

from client.core.scheme_service import create_user_scheme, load_official_scheme
from client.models import Game
from client.storage.database import get_session
from client.ui.main_window import MainWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_window_three_column_and_scheme() -> None:
    """三栏渲染：分类树 62 节点 + 风格 3 + 方案切换器。"""
    load_official_scheme()
    app = _app()
    window = MainWindow()
    assert isinstance(window.category_tree, QTreeView)
    assert isinstance(window.game_grid, QListWidget)
    assert isinstance(window.scheme_combo, QComboBox)
    model = window.category_tree.model()
    assert model.rowCount() == 16  # 顶层 16 大类（文字冒险提级 v0.8）
    assert window.style_list.count() == 6  # 风格 6 标签（含擦边）
    window.close()


def test_scheme_switch_updates_tree() -> None:
    """方案切换：新建自己方案（复制）→ 切换 → 树结构保留。"""
    official = load_official_scheme()
    user = create_user_scheme("我的方案", copy_from=official)
    app = _app()
    window = MainWindow()
    idx = window.scheme_combo.findData(user.id)
    window.scheme_combo.setCurrentIndex(idx)
    assert window.category_tree.model().rowCount() == 16
    window.close()


def test_games_table_loaded() -> None:
    """游戏网格：封面卡片加载（appid 存于 UserRole）。"""
    load_official_scheme()
    session = get_session()
    session.add(Game(appid="730", name="Counter-Strike 2", source="OWNED", is_installed=True))
    session.add(Game(appid="999", name="家庭共享游戏", source="FAMILY_SHARED"))
    session.commit()
    session.close()

    app = _app()
    window = MainWindow()
    assert window.game_grid.count() == 2
    assert window.game_grid.item(0).text() == "Counter-Strike 2"
    assert window.game_grid.item(0).data(Qt.UserRole) == "730"
    # 点击第一项 → 详情更新（点击驱动，哩米 2026-08-14）
    window._on_game_clicked(window.game_grid.item(0))
    assert "Counter-Strike 2" in window.detail_label.text()
    window.close()


def test_no_games_empty_state() -> None:
    """空库：网格显示空态提示（不可选），不崩溃。"""
    load_official_scheme()
    app = _app()
    window = MainWindow()
    assert window.game_grid.count() == 1  # 空态提示 item（哩米打磨 2026-08-14）
    hint = window.game_grid.item(0)
    assert hint.flags() & Qt.ItemIsSelectable == Qt.ItemFlag.NoItemFlags  # 提示不可选
    assert "还没有游戏" in hint.text()  # 空库引导扫描（哩米 2026-08-14 分发场景）
    window.close()
