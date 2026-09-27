"""库集应用入口（PRD §8.3 client/main.py）：初始化 + QApplication + 主窗口。

启动流程：loguru → 数据库初始化（Alembic）→ 官方方案加载 → 主窗口。
"""
import sys

from loguru import logger

from client.core.scheme_service import load_official_scheme
from client.storage.database import init_db
from client.utils.logging_setup import setup_logging


def _excepthook(exc_type, exc_value, exc_tb) -> None:
    """未捕获异常写入日志（打包后 console 禁用——2026-08-14 打包排查：
    崩溃原因被吞，必须落盘）。"""
    from loguru import logger as _logger

    _logger.error(f"未捕获异常: {exc_type.__name__}: {exc_value}",
                  exc_info=(exc_type, exc_value, exc_tb))


def main() -> int:
    sys.excepthook = _excepthook
    setup_logging()
    logger.info("库集启动")
    try:
        from client.config import ensure_config_file

        ensure_config_file()  # 首次启动生成默认 config.yaml（干净环境验收修复）
    except Exception as e:
        logger.warning(f"配置初始化失败（忽略，使用默认值）: {e}")
    try:
        init_db()
    except Exception as e:
        # 解压不完整等启动失败 → 友好弹窗（哩米 2026-08-15：朋友双击打不开
        # 根因 alembic.ini 缺失——静默崩溃不可排查，必须弹窗说明）
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication(sys.argv)
        QMessageBox.critical(
            None, "库集启动失败",
            f"初始化数据库失败：{e}\n\n"
            "如果是解压后运行，请确认 kuji.exe 旁边的 alembic.ini 和 "
            "client 文件夹完整存在（解压不完整会导致此问题，请重新完整解压 zip）。")
        return 1
    load_official_scheme()  # 官方方案首次加载（幂等）

    from PySide6.QtWidgets import QApplication

    from client.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
