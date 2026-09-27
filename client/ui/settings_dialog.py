"""设置面板（M4.5 MVP）：Steam ID / API Key（AES 加密存储）/ 代理 / AI API / 备份恢复。"""
import os

from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from client.config import get_settings


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(560)
        self.settings = get_settings()

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.steam_id_edit = QLineEdit(self.settings.steam_id)
        self.api_key_edit = QLineEdit(self.settings.steam_api_key)
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.sgdb_key_edit = QLineEdit(self.settings.steamgriddb_key)
        self.sgdb_key_edit.setEchoMode(QLineEdit.Password)
        self.proxy_edit = QLineEdit(self.settings.proxy)
        self.ai_key_edit = QLineEdit(self.settings.ai_api_key)
        self.ai_key_edit.setEchoMode(QLineEdit.Password)
        self.ai_base_url_edit = QLineEdit(self.settings.ai_base_url)
        self.ai_model_edit = QLineEdit(self.settings.ai_model)

        form.addRow("Steam ID（可选，补充账号检测）", self.steam_id_edit)
        form.addRow("Steam Web API Key（可留空）", self.api_key_edit)
        form.addRow("SteamGridDB Key（封面/名称兜底，可留空）", self.sgdb_key_edit)
        form.addRow("代理（可选，国内网络）", self.proxy_edit)
        form.addRow("AI API Key（可留空）", self.ai_key_edit)
        form.addRow("AI Base URL（如 https://api.deepseek.com/v1）", self.ai_base_url_edit)
        form.addRow("AI Model（如 deepseek-chat）", self.ai_model_edit)
        layout.addLayout(form)

        # 备份与恢复（哩米 2026-08-24：写回安全入口）
        backup_box = QGroupBox("备份与恢复")
        backup_layout = QVBoxLayout(backup_box)
        backup_layout.addWidget(QLabel(
            "每次写回 Steam 前会自动备份。若收藏夹出现异常，"
            "可在这里打开备份目录或恢复最近一次备份。"
        ))
        backup_btns = QHBoxLayout()
        open_btn = QPushButton("打开备份文件夹")
        open_btn.clicked.connect(self._open_backup_dir)
        restore_btn = QPushButton("恢复最近一次备份")
        restore_btn.clicked.connect(self._restore_latest_backup)
        backup_btns.addWidget(open_btn)
        backup_btns.addWidget(restore_btn)
        backup_btns.addStretch()
        backup_layout.addLayout(backup_btns)
        layout.addWidget(backup_box)

        btns = QHBoxLayout()
        cancel = QPushButton("取消")
        save = QPushButton("保存")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._on_save)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(save)
        layout.addLayout(btns)

    def _on_save(self) -> None:
        self.settings.steam_id = self.steam_id_edit.text().strip()
        self.settings.proxy = self.proxy_edit.text().strip()
        self.settings.set_steam_api_key(self.api_key_edit.text().strip())
        self.settings.set_steamgriddb_key(self.sgdb_key_edit.text().strip())
        self.settings.set_ai_api_key(self.ai_key_edit.text().strip())
        self.settings.ai_base_url = self.ai_base_url_edit.text().strip()
        self.settings.ai_model = self.ai_model_edit.text().strip()
        self.settings._persist()
        self.accept()

    def _open_backup_dir(self) -> None:
        """打开备份目录（备份文件按时间戳存放在 %APPDATA%/KuJi/.backup）。"""
        try:
            from client.core import vdf_ops

            path = vdf_ops.backup_dir()
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))
        except Exception as e:
            QMessageBox.critical(self, "打开失败", str(e))

    def _restore_latest_backup(self) -> None:
        """恢复最近一次备份（用于写回异常后手动回滚）。"""
        from client.core import vdf_ops
        from client.core.scanner import find_steamid32
        from client.models import BackupRecord
        from client.storage.database import get_session
        from client.utils.steam_path import detect_steam_path

        steam_path = detect_steam_path()
        if not steam_path:
            QMessageBox.warning(self, "未找到 Steam", "未检测到 Steam 路径，无法恢复。")
            return
        steamid32 = find_steamid32(steam_path)
        if not steamid32:
            QMessageBox.warning(self, "未找到账号", "未找到 Steam 账号，无法恢复。")
            return
        session = get_session()
        try:
            record = (
                session.query(BackupRecord)
                .order_by(BackupRecord.created_at_ts.desc())
                .first()
            )
        finally:
            session.close()
        if record is None:
            QMessageBox.information(self, "没有备份", "还没有可恢复的备份记录。")
            return
        if vdf_ops.is_steam_running():
            QMessageBox.warning(self, "Steam 正在运行", "恢复备份前请先关闭 Steam。")
            return
        ret = QMessageBox.question(
            self, "恢复最近一次备份",
            f"将恢复备份：\n时间：{record.created_at_ts}\n"
            f"说明：{record.notes or ''}\n\n"
            "恢复后请重新打开 Steam 查看。确定继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        try:
            vdf_ops.rollback(record, vdf_ops.get_store_path(steam_path, steamid32))
        except Exception as e:
            QMessageBox.critical(self, "恢复失败", str(e))
            return
        QMessageBox.information(self, "恢复完成", "已恢复最近一次备份，请重新打开 Steam 查看。")
