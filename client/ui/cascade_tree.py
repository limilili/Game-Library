"""级联式分类浏览（哩米需求 2026-08-12）：只显示大类，点击后右边逐级显示子类。

实现：N 个 QListWidget 水平联动（Finder 列视图风格）——
第 1 列顶层大类 → 点击出第 2 列子类 → 第 3 列……直到叶节点；
点击分类（含父级）发 category_selected 信号（供游戏列表筛选）。
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QWidget,
)


class CascadeTree(QWidget):
    """级联分类树：水平列联动。"""

    category_selected = Signal(str)  # category_id（点击分类时发出；空串=清空）

    def __init__(self, categories: list, counts: dict[str, int], parent=None) -> None:
        super().__init__(parent)
        self._categories = categories  # 全部节点（level/sort_order 有序）
        self._counts = counts
        self._columns: list[QListWidget] = []
        self._current_path: list[str] = []  # 当前选中的各级分类 id
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self._build_column(None)

    # ── 构建 ────────────────────────────────────────────────────
    def _by_parent(self) -> dict[str | None, list]:
        result: dict[str | None, list] = {}
        for c in self._categories:
            result.setdefault(c.parent_id, []).append(c)
        return result

    def _build_column(self, parent_id: str | None) -> None:
        """在现有列右侧追加一列（parent_id 的子节点）。"""
        col = QListWidget()
        col.setFixedWidth(170)
        by_parent = self._by_parent()
        for c in sorted(by_parent.get(parent_id, []), key=lambda x: x.sort_order):
            label = f"{c.icon} {c.name}" if c.icon else c.name
            count = self._counts.get(c.id, 0)
            if count:
                label = f"{label}（{count}）"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, c.id)
            col.addItem(item)
        col.itemClicked.connect(lambda item, pid=parent_id: self._on_column_click(col, item, pid))
        self._columns.append(col)
        self._layout.addWidget(col)

    def _on_column_click(self, col: QListWidget, item: QListWidgetItem, parent_id) -> None:
        """点击某列：截断右侧列 → 若有子类右推新列 → 发出分类选择。"""
        category_id = item.data(Qt.UserRole)
        # 截断右侧所有列
        while self._columns[-1] is not col:
            last = self._columns.pop()
            self._layout.removeWidget(last)
            last.deleteLater()
        # 更新当前路径（该列前的路径保持不变）
        col_idx = self._columns.index(col)
        self._current_path = self._current_path[:col_idx]
        self._current_path.append(category_id)
        # 有子类 → 右推新列
        node = next((c for c in self._categories if c.id == category_id), None)
        if node is not None:
            has_children = any(c.parent_id == category_id for c in self._categories)
            if has_children:
                self._build_column(category_id)
        self.category_selected.emit(category_id)

    # ── 重置 ─────────────────────────────────────────────────────
    def clear_selection(self) -> None:
        """清空所有列选中（三视图互斥时调用，哩米 2026-08-13）。"""
        for col in self._columns:
            col.clearSelection()

    def reset(self, categories: list, counts: dict[str, int]) -> None:
        """外部数据刷新后重建（仅第一列）。"""
        for col in self._columns[1:]:
            self._layout.removeWidget(col)
            col.deleteLater()
        self._columns = [self._columns[0]]
        first = self._columns[0]
        first.clear()
        self._categories = categories
        self._counts = counts
        for c in sorted(
            [x for x in categories if x.parent_id is None], key=lambda x: x.sort_order
        ):
            label = f"{c.icon} {c.name}" if c.icon else c.name
            count = counts.get(c.id, 0)
            if count:
                label = f"{label}（{count}）"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, c.id)
            first.addItem(item)
        self._current_path = []
