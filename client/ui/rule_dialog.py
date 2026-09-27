"""规则编辑对话框（M4.8 MVP）：条件 JSON + 目标分类 + 实时命中预览。

MVP 范围：编辑/新建一条规则（条件 JSON 文本 + 目标分类叶节点选择 + 命中数预览）。
"""
import json

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from client.core.rule_engine import RuleEngineError, count_rule_matches
from client.core.scheme_service import get_scheme_tree


class RuleDialog(QDialog):
    def __init__(self, scheme_id: str, games: list[dict], rule: dict | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("规则编辑")
        self.setMinimumWidth(520)
        self.scheme_id = scheme_id
        self.games = games
        self.result: dict | None = None

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(rule.get("name", "") if rule else "")
        self.target_combo = QComboBox()
        leaves = [c for c in get_scheme_tree(scheme_id) if c.is_leaf]
        for c in leaves:
            self.target_combo.addItem(f"{c.path}", c.slug)
        if rule and rule.get("target_category_slug"):
            idx = self.target_combo.findData(rule["target_category_slug"])
            self.target_combo.setCurrentIndex(max(idx, 0))
        self.conditions_edit = QLineEdit()
        if rule:
            self.conditions_edit.setText(
                json.dumps(rule.get("conditions", []), ensure_ascii=False))
        else:
            self.conditions_edit.setText(
                '[{"field": "steam_tags", "op": "contains_any", "value": []}]')
        self.hit_label = QLabel("命中预览：-")
        form.addRow("规则名", self.name_edit)
        form.addRow("目标分类（叶节点）", self.target_combo)
        form.addRow("条件 JSON", self.conditions_edit)
        layout.addLayout(form)
        layout.addWidget(self.hit_label)

        btns = QHBoxLayout()
        cancel = QPushButton("取消")
        save = QPushButton("保存")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._on_save)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(save)
        layout.addLayout(btns)

        self.conditions_edit.textChanged.connect(self._refresh_preview)
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        try:
            conditions = json.loads(self.conditions_edit.text())
            rule = {"conditions": conditions}
            hits = count_rule_matches(rule, self.games)
            self.hit_label.setText(f"命中预览：{hits} 款游戏")
        except (ValueError, RuleEngineError):
            self.hit_label.setText("命中预览：JSON 无效")

    def _on_save(self) -> None:
        if not self.name_edit.text().strip():
            return
        try:
            conditions = json.loads(self.conditions_edit.text())
            assert isinstance(conditions, list)
        except (ValueError, AssertionError):
            return
        self.result = {
            "name": self.name_edit.text().strip(),
            "target_category_slug": self.target_combo.currentData(),
            "conditions": conditions,
        }
        self.accept()
