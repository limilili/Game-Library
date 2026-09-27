"""POC-6 三栏 UI 骨架：分类树 + 游戏列表 + 详情预览（+ 风格标签区）。

对应 PRD M4（三栏布局：QTreeView + QTableView + QStackedWidget；PRD §8.3 桌面 UI 设计）。
v1.3 已并入：分类树加载官方方案（docs/category-tree-default.json 玩法树）+ 风格标签区。

运行：python main_window.py            # 交互模式（窗口）
      pytest test_ui_skeleton.py      # offscreen 程序化验证 + 截图
"""
import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QTableView,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

SANDBOX = Path(__file__).resolve().parent
DOCS = SANDBOX.parent.parent.parent / "docs"  # 2/docs/category-tree-default.json
CATEGORY_TREE = DOCS / "category-tree-default.json"

# 假数据（骨架演示用；真实扫描数据在 Phase 1 接入）
DEMO_GAMES = [
    ("艾尔登法环", "1245620", "硬核动作（魂系）", 362),
    ("赛博朋克 2077", "1091500", "开放世界WRPG", 120),
    ("死亡细胞", "588650", "动作肉鸽", 45),
    ("空洞骑士", "367520", "类银河战士恶魔城", 88),
    ("女神异闻录5 皇家版", "1687950", "回合制JRPG", 160),
    ("命运石之门", "412830", "Galgame", 30),
    ("逃生", "239820", "潜行恐怖", 8),
    ("极限竞速：地平线5", "1551360", "竞速运动", 55),
    ("我的世界", "43210", "沙盒", 999),
    ("胡闹厨房2", "728880", "派对", 25),
]


def _load_category_tree() -> dict:
    """加载官方分类树 JSON（2/docs/category-tree-default.json）。"""
    return json.loads(CATEGORY_TREE.read_text(encoding="utf-8"))


def _build_tree_model(tree_data: dict) -> QStandardItemModel:
    """官方玩法树 JSON → QStandardItemModel（含 emoji 图标 + 样式）。"""
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(["分类"])

    def add_children(parent: QStandardItem, nodes: list) -> None:
        for node in nodes:
            label = node["name"]
            if node.get("icon"):
                label = f"{node['icon']} {label}"
            item = QStandardItem(label)
            item.setEditable(False)
            item.setData(node.get("id", ""), Qt.UserRole)
            parent.appendRow(item)
            if "children" in node:
                add_children(item, node["children"])

    root = model.invisibleRootItem()
    add_children(root, tree_data["type_tree"]["root"])
    return model


class MainWindow(QMainWindow):
    """三栏布局主窗口（POC-6 骨架）。"""

    def __init__(self, tree_data: dict | None = None) -> None:
        super().__init__()
        self.setWindowTitle("库集 · 三栏骨架（POC-6）")
        self.resize(1100, 650)
        self._tree_data = tree_data or _load_category_tree()

        # 左：分类树
        self.category_tree = QTreeView()
        self.category_tree.setModel(_build_tree_model(self._tree_data))
        self.category_tree.expandAll()
        self.category_tree.setHeaderHidden(True)

        # 中：游戏列表（列：名称 / AppID / 类型 / 时长h）
        self.game_table = QTableView()
        self.game_model = QStandardItemModel(0, 4, self)
        self.game_model.setHorizontalHeaderLabels(["名称", "AppID", "类型", "时长(h)"])
        self.game_table.setModel(self.game_model)
        self.game_table.horizontalHeader().setStretchLastSection(True)
        self.game_table.setSelectionBehavior(QTableView.SelectRows)

        # 右：详情/预览（QStackedWidget：空态 + 详情态）
        self.detail_stack = QStackedWidget()
        self.empty_page = QWidget()
        empty_layout = QVBoxLayout(self.empty_page)
        empty_label = QLabel("未选择游戏\n\n在列表中选择一款游戏查看详情")
        empty_label.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(empty_label)
        self.detail_page = QWidget()
        detail_layout = QVBoxLayout(self.detail_page)
        self.detail_name = QLabel("游戏名")
        self.detail_name.setStyleSheet("font-size: 18px; font-weight: bold;")
        self.detail_meta = QLabel("")
        self.detail_meta.setWordWrap(True)
        detail_layout.addWidget(self.detail_name)
        detail_layout.addWidget(self.detail_meta)
        detail_layout.addStretch()
        self.detail_stack.addWidget(self.empty_page)   # index 0
        self.detail_stack.addWidget(self.detail_page)  # index 1

        # 底部：风格标签区（v1.3 风格小窗）
        style_names = [s["name"] for s in self._tree_data["styles"]["items"]]
        self.style_list = QListWidget()
        self.style_list.addItems(style_names)
        self.style_list.setMaximumHeight(72)
        self.style_list.setSelectionMode(QListView.NoSelection)

        # 布局：左（树 + 风格）| 中（表）| 右（详情）
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("分类树"), 0, Qt.AlignLeft)
        left_layout.addWidget(self.category_tree, 1)
        left_layout.addWidget(QLabel("风格"))
        left_layout.addWidget(self.style_list)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(left_widget, 2)
        layout.addWidget(self.game_table, 4)
        layout.addWidget(self.detail_stack, 3)
        self.setCentralWidget(central)

        # 交互：选中游戏 → 详情
        self.game_table.selectionModel().selectionChanged.connect(self._on_game_selected)

    def load_demo_games(self, games: list[tuple] | None = None) -> None:
        """填充假数据（空数据态 → 数据态）。"""
        games = games or DEMO_GAMES
        self.game_model.setRowCount(len(games))
        for row, (name, appid, genre, hours) in enumerate(games):
            for col, text in enumerate((name, appid, genre, str(hours))):
                item = QStandardItem(text)
                item.setEditable(False)
                self.game_model.setItem(row, col, item)

    def _on_game_selected(self) -> None:
        index = self.game_table.selectionModel().currentIndex()
        if not index.isValid():
            return
        row = index.row()
        name = self.game_model.item(row, 0).text()
        appid = self.game_model.item(row, 1).text()
        genre = self.game_model.item(row, 2).text()
        hours = self.game_model.item(row, 3).text()
        self.detail_name.setText(name)
        self.detail_meta.setText(
            f"AppID：{appid}\n类型：{genre}\n时长：{hours} 小时\n\n（详情面板骨架，"
            f"Phase 1 接入元数据/AI 建议/分类编辑）"
        )
        self.detail_stack.setCurrentIndex(1)


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.load_demo_games()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
