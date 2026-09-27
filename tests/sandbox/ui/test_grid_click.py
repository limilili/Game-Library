"""沙盒复现：MainWindow 点击第二个图，详情是否更新（点击驱动）。

哩米反馈 2026-08-14：点击中间游戏网格时，点一个图应显示一个图，
实际只显示第一个点击的图。修复方案：itemSelectionChanged → itemClicked 点击驱动。

运行：python tests/sandbox/ui/test_grid_click.py（顶层代码，pytest 不收集）
"""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def main() -> None:
    app = QApplication([])
    from client.ui.main_window import MainWindow

    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    app.processEvents()

    # 等待列表加载
    window.reload_games()
    app.processEvents()

    n = window.game_grid.count()
    print("网格游戏数:", n)
    if n < 2:
        print("游戏不足 2 款，无法复现")
        return

    item0 = window.game_grid.item(0)
    item1 = window.game_grid.item(1)
    print("item0:", item0.text(), "| item1:", item1.text())

    pos0 = window.game_grid.visualItemRect(item0).center()
    pos1 = window.game_grid.visualItemRect(item1).center()

    # 点击第一个
    QTest.mouseClick(window.game_grid.viewport(), Qt.LeftButton, Qt.NoModifier, pos0)
    app.processEvents()
    d0 = window.detail_label.text()
    pix0 = window.detail_cover.pixmap()
    print("点击 item0 后详情含 item0:", item0.text().strip() in d0)

    # 点击第二个
    QTest.mouseClick(window.game_grid.viewport(), Qt.LeftButton, Qt.NoModifier, pos1)
    app.processEvents()
    d1 = window.detail_label.text()
    pix1 = window.detail_cover.pixmap()
    print("点击 item1 后详情含 item1:", item1.text().strip() in d1)
    print("封面 pixmap 是否变化:", pix0 is not None and pix1 is not None and
          pix0.cacheKey() != pix1.cacheKey())

    # 再点回第一个（点击已选中项也应刷新——旧逻辑会取消选中不更新）
    QTest.mouseClick(window.game_grid.viewport(), Qt.LeftButton, Qt.NoModifier, pos0)
    app.processEvents()
    d2 = window.detail_label.text()
    pix2 = window.detail_cover.pixmap()
    print("再点 item0 后详情含 item0:", item0.text().strip() in d2)
    print("封面 pixmap 回到 item0:", pix2 is not None and pix2.cacheKey() == pix0.cacheKey())

    # 封面对比
    from client.core.cover_art import get_cover_path

    print("item1 封面路径:", get_cover_path(item1.data(Qt.UserRole)))


if __name__ == "__main__":
    main()
