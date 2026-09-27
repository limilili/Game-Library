"""库集 v2.2 开发测试台（哩米需求 2026-08-24）。

用途：不需要完整主窗口，也能单独验证扫描、元数据、AI 补全/分类、反馈导出。

启动：
    python -m client.ui.debug_console
或双击项目根目录的「启动测试台.bat」。
"""
import os
import sys
from pathlib import Path

from loguru import logger
from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

APP_VERSION = "v2.2"

_COLUMNS = ["封面", "AppID", "名称", "类型", "来源", "已安装", "开发商", "发行商", "分类"]


class DebugConsole(QMainWindow):
    """v2.2 开发测试台。"""

    log_signal = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"库集 {APP_VERSION} — 开发测试台")
        self.resize(1200, 760)
        self._worker = None
        self._icon_cache: dict[str, QIcon] = {}
        self._icon_token = 0
        self._log_sink_id = logger.add(
            self._log_sink,
            level="DEBUG",
            format="{time:HH:mm:ss} | {level: <8} | {message}",
            colorize=False,
        )
        self.log_signal.connect(self._append_log)
        self._build_ui()
        self._refresh_all()

    # ── UI ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        self.info_label = QLabel()
        self.info_label.setWordWrap(True)
        root.addWidget(self.info_label)

        btn_row = QHBoxLayout()
        self.scan_btn = QPushButton("扫描游戏库")
        self.scan_btn.clicked.connect(self._on_scan)
        self.meta_btn = QPushButton("补全元数据")
        self.meta_btn.clicked.connect(self._on_metadata)
        self.ai_btn = QPushButton("AI 补全/分类")
        self.ai_btn.clicked.connect(self._on_ai)
        self.refresh_btn = QPushButton("刷新数据")
        self.refresh_btn.clicked.connect(self._refresh_all)
        self.feedback_btn = QPushButton("导出反馈包")
        self.feedback_btn.clicked.connect(self._on_feedback)
        self.settings_btn = QPushButton("设置")
        self.settings_btn.clicked.connect(self._on_settings)
        for b in (self.scan_btn, self.meta_btn, self.ai_btn,
                  self.refresh_btn, self.feedback_btn, self.settings_btn):
            btn_row.addWidget(b)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self.stats_label = QLabel()
        root.addWidget(self.stats_label)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setIconSize(QSize(40, 60))
        self.table.verticalHeader().setDefaultSectionSize(70)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 54)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(8, QHeaderView.Stretch)
        for col in (1, 3, 4, 5, 6, 7):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        root.addWidget(self.table, stretch=1)

        log_label = QLabel("实时日志")
        root.addWidget(log_label)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(220)
        root.addWidget(self.log_view)

        self.setCentralWidget(central)

    # ── 数据 ────────────────────────────────────────────────────

    def _refresh_all(self) -> None:
        self._refresh_info()
        self._load_games()

    def _refresh_info(self) -> None:
        from client.config import get_settings
        from client.utils.steam_path import detect_steam_path

        settings = get_settings()
        steam = detect_steam_path()
        lines = [
            f"版本：{APP_VERSION}    数据库：{settings.resolved_db_path}",
            f"Steam：{steam or '未检测到'}",
            f"Steam API Key：{'已配置' if settings.steam_api_key_enc else '未配置'}    "
            f"SteamGridDB：{'已配置' if settings.steamgriddb_key_enc else '未配置'}    "
            f"AI：{'已配置' if settings.ai_api_key_enc else '未配置'}",
        ]
        self.info_label.setText("    ".join(lines))

    def _load_games(self) -> None:
        from client.models import Category, Game, GameClassification
        from client.storage.database import get_session

        session = get_session()
        try:
            games = session.query(Game).filter(Game.del_flag.is_(False)).order_by(Game.name).all()
            cat_map = {
                c.id: c.name for c in session.query(Category).filter(
                    Category.del_flag.is_(False)).all()
            }
            cls_map: dict[str, str] = {}
            for gc in session.query(GameClassification).filter(
                    GameClassification.dimension == "TYPE",
                    GameClassification.del_flag.is_(False)).all():
                name = cat_map.get(gc.category_id)
                if name:
                    cls_map[gc.game_appid] = name
            total = len(games)
            installed = sum(1 for g in games if g.is_installed)
            shared = sum(1 for g in games if g.source == "FAMILY_SHARED")
            classified = sum(1 for g in games if g.appid in cls_map)
            with_meta = sum(1 for g in games if g.steam_tags and g.steam_tags != "[]")
        finally:
            session.close()

        self.stats_label.setText(
            f"游戏总数：{total}    已安装：{installed}    家庭共享：{shared}    "
            f"有元数据：{with_meta}    已分类：{classified}"
        )

        self._icon_token += 1
        token = self._icon_token
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(total)
            for row, g in enumerate(games):
                # 封面列先留空，稍后分批异步设置，避免一次性卡住 UI
                self.table.setItem(row, 0, QTableWidgetItem())
                values = [
                    g.appid, g.name, g.app_type or "",
                    "家庭共享" if g.source == "FAMILY_SHARED" else "自有",
                    "是" if g.is_installed else "否",
                    g.developer or "", g.publisher or "",
                    cls_map.get(g.appid, ""),
                ]
                for col, value in enumerate(values, start=1):
                    self.table.setItem(row, col, QTableWidgetItem(str(value)))
        finally:
            self.table.setUpdatesEnabled(True)

        batch_size = 40

        def apply_icons(start: int) -> None:
            if token != self._icon_token:
                return
            end = min(start + batch_size, len(games))
            for i in range(start, end):
                item = self.table.item(i, 0)
                if item is None:
                    continue
                icon = self._cover_icon(games[i].appid)
                if icon is not None and not icon.isNull():
                    item.setIcon(icon)
            if end < len(games):
                QTimer.singleShot(0, lambda e=end: apply_icons(e))

        apply_icons(0)

    def _cover_icon(self, appid: str) -> QIcon:
        """读取已有缩略图作为表格封面；不存在则不生成，避免卡顿。"""
        cached = self._icon_cache.get(appid)
        if cached is not None:
            return cached
        icon = QIcon()
        try:
            from client.core.cover_art import _thumb_dir

            thumb = _thumb_dir() / f"{appid}.jpg"
            if thumb.is_file():
                pix = QPixmap(str(thumb)).scaled(
                    40, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                icon = QIcon(pix)
        except Exception:
            pass
        self._icon_cache[appid] = icon
        return icon

    # ── 任务 ────────────────────────────────────────────────────

    def _busy(self) -> bool:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "任务进行中", "有任务正在运行，请稍候。")
            return True
        return False

    def _set_buttons_enabled(self, enabled: bool) -> None:
        for b in (self.scan_btn, self.meta_btn, self.ai_btn,
                  self.refresh_btn, self.feedback_btn):
            b.setEnabled(enabled)

    def _on_scan(self) -> None:
        if self._busy():
            return
        from client.config import get_settings
        from client.utils.steam_path import detect_steam_path
        from client.ui.main_window import ScanWorker

        steam_path = detect_steam_path()
        if not steam_path:
            QMessageBox.warning(self, "未找到 Steam", "未检测到 Steam 路径。")
            return
        settings = get_settings()
        worker = ScanWorker(steam_path, None, settings.steam_api_key, settings.proxy)
        worker.finished_ok.connect(
            lambda total, shared: (
                self._set_buttons_enabled(True),
                logger.info(f"扫描完成：{total} 款（家庭共享 {shared}）"),
                self._refresh_all(),
                QMessageBox.information(self, "扫描完成", f"共 {total} 款（家庭共享 {shared}）"),
            )
        )
        worker.failed.connect(
            lambda msg: (self._set_buttons_enabled(True),
                         QMessageBox.critical(self, "扫描失败", msg))
        )
        worker.finished.connect(lambda: self._set_buttons_enabled(True))
        self._set_buttons_enabled(False)
        self._worker = worker
        worker.start()

    def _on_metadata(self) -> None:
        if self._busy():
            return
        from client.config import get_settings
        from client.core.scanner import find_steamid32
        from client.utils.steam_path import detect_steam_path
        from client.ui.main_window import MetadataFillWorker

        steam_path = detect_steam_path()
        if not steam_path:
            QMessageBox.warning(self, "未找到 Steam", "未检测到 Steam 路径。")
            return
        steamid32 = find_steamid32(steam_path)
        if not steamid32:
            QMessageBox.warning(self, "无账号", "未找到 Steam 账号目录，请在设置中填写 Steam ID。")
            return
        worker = MetadataFillWorker(steam_path, steamid32)
        worker.progress.connect(lambda done, total: self.stats_label.setText(f"补全元数据 {done}/{total}"))
        worker.finished_ok.connect(
            lambda n: (self._set_buttons_enabled(True), logger.info(f"补全元数据完成：{n} 款"),
                       self._refresh_all(),
                       QMessageBox.information(self, "补全完成", f"本轮补全 {n} 款元数据"))
        )
        worker.failed.connect(
            lambda msg: (self._set_buttons_enabled(True),
                         QMessageBox.critical(self, "补全失败", msg))
        )
        worker.finished.connect(lambda: self._set_buttons_enabled(True))
        self._set_buttons_enabled(False)
        self._worker = worker
        worker.start()

    def _on_ai(self) -> None:
        if self._busy():
            return
        from client.config import get_settings
        from client.ui.main_window import AIFillWorker

        settings = get_settings()
        if not (settings.ai_api_key and settings.ai_base_url and settings.ai_model):
            QMessageBox.warning(self, "未配置 AI", "请先在设置中填写 AI API Key / Base URL / Model。")
            return
        ret = QMessageBox.question(
            self, "AI 补全/分类",
            f"将对最多 {AIFillWorker.MAX_BATCH} 款缺少元数据的游戏调用 AI。\n\n"
            "这可能需要较长时间，并可能产生 API 费用。\n"
            "AI 只会生成建议，确认后才会写入数据库。是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        worker = AIFillWorker()
        worker.progress.connect(lambda done, total: self.stats_label.setText(f"AI 补全/分类 {done}/{total}"))
        worker.suggestions_ready.connect(self._on_ai_suggestions_ready)
        worker.cancelled.connect(
            lambda collected, _classified: (
                self._set_buttons_enabled(True),
                QMessageBox.information(self, "AI 已取消", f"已收集 {collected} 条建议，未写入数据库。"),
            )
        )
        worker.failed.connect(
            lambda msg: (self._set_buttons_enabled(True),
                         QMessageBox.critical(self, "AI 失败", msg))
        )
        worker.finished.connect(lambda: self._set_buttons_enabled(True))
        self._set_buttons_enabled(False)
        self._worker = worker
        worker.start()

    def _on_ai_suggestions_ready(self, suggestions: list) -> None:
        from PySide6.QtWidgets import QDialog

        from client.core.ai_apply import apply_ai_suggestions
        from client.ui.main_window import AIResultsDialog

        self._set_buttons_enabled(True)
        if not suggestions:
            QMessageBox.information(self, "没有需要处理的游戏", "库里暂时没有缺少元数据的游戏。")
            return
        dlg = AIResultsDialog(suggestions, self)
        if dlg.exec() != QDialog.Accepted:
            logger.info("AI 建议未应用")
            return
        selected = dlg.selected_suggestions()
        if not selected:
            QMessageBox.information(self, "未选择", "没有勾选任何建议，数据库没有改动。")
            return
        filled, classified = apply_ai_suggestions(selected)
        logger.info(f"AI 建议已应用：元数据 {filled} 款，分类 {classified} 款")
        self._refresh_all()
        QMessageBox.information(
            self, "AI 建议已应用",
            f"已写入元数据 {filled} 款，分类 {classified} 款。",
        )

    def _on_feedback(self) -> None:
        try:
            from client.utils.feedback import export_feedback

            path = export_feedback()
        except Exception as e:
            logger.exception("反馈包生成失败")
            QMessageBox.critical(self, "反馈包生成失败", str(e))
            return
        QMessageBox.information(self, "反馈包已生成", f"已生成：\n{path}")
        try:
            import os
            os.startfile(str(path.parent))
        except Exception:
            pass

    def _on_settings(self) -> None:
        from PySide6.QtWidgets import QDialog

        from client.ui.settings_dialog import SettingsDialog

        if SettingsDialog(self).exec() == QDialog.Accepted:
            logger.info("设置已保存")
            self._refresh_info()

    # ── 日志 ────────────────────────────────────────────────────

    def _log_sink(self, message) -> None:
        try:
            self.log_signal.emit(str(message).rstrip())
        except Exception:
            pass

    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        self.log_view.moveCursor(QTextCursor.End)

    def closeEvent(self, event) -> None:
        try:
            logger.remove(self._log_sink_id)
        except Exception:
            pass
        super().closeEvent(event)


def main() -> None:
    from client.config import ensure_config_file
    from client.utils.logging_setup import setup_logging

    setup_logging()
    ensure_config_file()
    app = QApplication(sys.argv)
    app.setApplicationName("库集测试台")
    win = DebugConsole()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
