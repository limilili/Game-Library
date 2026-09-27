"""主窗口（M4）：三栏布局 + 方案切换 + 扫描 + 写入预览确认。

数据流：games 表（扫描入库）→ 分类树（激活方案）→ 游戏列表 → 详情；
写入流程：预览确认（M4.6）→ Steam 检测（M3.6）→ 备份（M3.1）→ 写入（M3.8）。
"""
import json
import os
from pathlib import Path

from loguru import logger
from PySide6.QtCore import QSize, QThread, QTimer, Signal, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication,
    QHeaderView,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QListView,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableView,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from client.core import vdf_ops
from client.core.scheme_service import (
    get_active_scheme,
    get_scheme_styles,
    get_scheme_tree,
    list_schemes,
)
from client.models import Game
from client.storage.database import get_session
from client.utils.steam_path import detect_steam_path


# ── 后台扫描线程 ────────────────────────────────────────────────


class ScanWorker(QThread):
    finished_ok = Signal(int, int)  # 总数, 家庭共享数
    failed = Signal(str)

    def __init__(self, steam_path: Path, steamid32: str | None,
                 api_key: str, proxy: str, parent=None) -> None:
        super().__init__(parent)
        self.steam_path = steam_path
        self.steamid32 = steamid32
        self.api_key = api_key
        self.proxy = proxy

    def run(self) -> None:
        try:
            from client.core.scanner import scan_steam_library

            games = scan_steam_library(
                self.steam_path, steamid32=self.steamid32,
                api_key=self.api_key, proxy=self.proxy,
                fetch_names=False,  # 名称补全独立按钮触发（2026-08-12 防扫描卡死）
            )
            shared = sum(1 for g in games.values() if g["source"] == "FAMILY_SHARED")
            self.finished_ok.emit(len(games), shared)
        except Exception as e:
            logger.exception("扫描失败")
            self.failed.emit(str(e))


class MetadataFillWorker(QThread):
    """元数据补全后台任务（哩米需求：不只补名称，补全 developer/genres/标签等全字段）。

    借鉴 v0 metadata.py：查缓存 → 拉取（appdetails，含 cc=us 锁区降级）→ 写缓存；
    progress 信号驱动 UI 进度条。
    """

    progress = Signal(int, int)  # done, total
    finished_ok = Signal(int)
    failed = Signal(str)

    def __init__(self, steam_path: Path, steamid32: str, parent=None) -> None:
        super().__init__(parent)
        self.steam_path = steam_path
        self.steamid32 = steamid32

    def run(self) -> None:
        try:
            from client.api.steam_api import SteamClient
            from client.core.library_cache import get_local_library_appids
            from client.models import Game, MetadataCache
            from client.storage.database import get_session

            appids = get_local_library_appids(self.steam_path, self.steamid32)
            session = get_session()
            try:
                known = {g.appid for g in session.query(Game).filter(
                    Game.del_flag.is_(False)).all()}
            finally:
                session.close()
            missing = [a for a in appids if a not in known]

            def _progress(done: int, total: int) -> None:
                self.progress.emit(done, total)

            metas = SteamClient().get_app_metadata(missing, progress_callback=_progress)
            # 封面补全（本地未缓存的无封面游戏 → CDN 下载，哩米需求）
            from client.core.cover_art import download_cover, get_thumb_path

            missing_covers = [
                a for a in (set(metas) | set(missing)) if get_thumb_path(a) is None
            ]
            for done, appid in enumerate(missing_covers, start=1):
                download_cover(appid)
                self.progress.emit(len(metas) + done, len(metas) + len(missing_covers))
            if not metas:
                self.finished_ok.emit(0)
                return

            # 写回：Game（名称/标签占位替换）+ MetadataCache（规则字段）
            session = get_session()
            try:
                games = {g.appid: g for g in session.query(Game).filter(
                    Game.del_flag.is_(False)).all()}
                cache = {m.appid: m for m in session.query(MetadataCache).all()}
                for appid, meta in metas.items():
                    game = games.get(appid)
                    if game:
                        if game.name.startswith("["):  # 占位名称替换
                            game.name = meta["name"] or game.name
                        if meta["store_tags"] != "[]":
                            game.steam_tags = meta["store_tags"]
                        if meta.get("type"):
                            game.app_type = meta["type"]  # 工具软件识别（哩米指出）
                        if meta.get("developer"):
                            game.developer = meta["developer"]  # 开发商（哩米需求 2026-08-13）
                        if meta.get("publisher"):
                            game.publisher = meta["publisher"]  # 发行商
                    mc = cache.get(appid)
                    if mc is None:
                        mc = MetadataCache(appid=appid)
                        session.add(mc)
                        cache[appid] = mc
                    elif mc.del_flag:
                        mc.del_flag = False  # 复活历史逻辑删除缓存
                    mc.developer = meta["developer"]
                    mc.publisher = meta["publisher"]
                    mc.store_tags = meta["store_tags"]
                    mc.genres = meta["genres"]
                    mc.supported_languages = meta["supported_languages"]
                session.commit()
            finally:
                session.close()
            self.finished_ok.emit(len(metas))
        except Exception as e:
            logger.exception("元数据补全失败")
            self.failed.emit(str(e))



class AIFillWorker(QThread):
    """AI 补全/分类后台任务：只收集建议，不直接写库。

    用户在主窗口确认 AIResultsDialog 后，再由 AI 建议应用器写库。
    """

    progress = Signal(int, int)  # done, total
    suggestions_ready = Signal(list)  # list[dict] AI 建议
    cancelled = Signal(int, int)  # 已收集数, 0
    failed = Signal(str)
    MAX_BATCH = 50  # 每轮最多处理数量（控制耗时与 API 费用）

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

    def run(self) -> None:
        try:
            from client.api.ai_client import AIClient, AIError, _extract_json, build_metadata_prompt
            from client.core.rule_engine import rule_matches
            from client.core.scheme_service import get_active_scheme, get_scheme_tree
            from client.models import Category, Game, MetadataCache
            from client.storage.database import get_session

            scheme = get_active_scheme()
            if scheme is None:
                self.failed.emit("未找到激活方案")
                return

            session = get_session()
            try:
                categories = [
                    {"slug": c.slug, "name": c.name, "path": c.path}
                    for c in get_scheme_tree(scheme.id)
                ]
                cat_names = {c["slug"]: c["name"] for c in categories}
                rules = []
                from client.models import ClassificationRule
                rule_rows = session.query(ClassificationRule).filter(
                    ClassificationRule.scheme_id == scheme.id,
                    ClassificationRule.is_enabled.is_(True),
                    ClassificationRule.del_flag.is_(False),
                ).order_by(ClassificationRule.priority).all()
                cat_map = {c.id: c for c in session.query(Category).filter(
                    Category.scheme_id == scheme.id,
                    Category.del_flag.is_(False)).all()}
                for r in rule_rows:
                    try:
                        import json as _json
                        rules.append({
                            "name": r.name,
                            "priority": r.priority,
                            "target_category_slug": (
                                cat_map[r.target_category_id].slug
                                if r.target_category_id in cat_map else None
                            ),
                            "is_enabled": r.is_enabled,
                            "conditions": _json.loads(r.conditions or "[]"),
                        })
                    except Exception:
                        continue
                games = session.query(Game).filter(Game.del_flag.is_(False)).all()
                # 已有元数据缓存的游戏不再重复调用 AI（哩米 2026-09-27）
                meta_appids = {
                    m.appid for m in session.query(MetadataCache).all()
                    if (m.store_tags and m.store_tags != "[]")
                    or m.developer or m.publisher
                }
                todo = [
                    g for g in games
                    if (g.name.startswith("[")
                        or not (g.steam_tags and g.steam_tags != "[]")
                        or g.app_type in ("unknown", ""))
                    and g.appid not in meta_appids
                ]
            finally:
                session.close()

            if not todo:
                self.suggestions_ready.emit([])
                return
            todo = todo[:self.MAX_BATCH]

            client = AIClient()
            suggestions: list[dict] = []
            for done, game in enumerate(todo, start=1):
                if self.isInterruptionRequested():
                    self.cancelled.emit(len(suggestions), 0)
                    return
                self.progress.emit(done, len(todo))
                try:
                    system, user = build_metadata_prompt(
                        game.appid, game.name, categories
                    )
                    content = client.complete(system, user)
                    data = _extract_json(content)
                except AIError as e:
                    logger.error(f"AI API 调用失败：{e}")
                    self.failed.emit(f"AI API 调用失败：{e}")
                    return
                except Exception as e:
                    logger.warning(f"AI 补全失败 appid={game.appid}: {e}")
                    continue

                genres = data.get("genres") or []
                if not isinstance(genres, list):
                    genres = []
                gdict = {
                    "appid": str(game.appid),
                    "steam_tags": genres,
                    "developer": data.get("developer") or game.developer,
                    "publisher": data.get("publisher") or game.publisher,
                }
                metadata = {
                    "store_tags": genres,
                    "genres": genres,
                    "developer": gdict["developer"],
                    "publisher": gdict["publisher"],
                }
                hit_slug = None
                for rule in rules:
                    if not rule.get("target_category_slug"):
                        continue
                    try:
                        if rule_matches(rule, gdict, metadata):
                            hit_slug = rule["target_category_slug"]
                            break
                    except Exception:
                        continue
                ai_slug = data.get("category_slug") or ""
                final_slug = hit_slug or ai_slug or ""
                source = "RULE" if hit_slug else ("AI" if ai_slug else "")
                suggestions.append({
                    "appid": str(game.appid),
                    "name": data.get("name") or game.name,
                    "type": data.get("type") or game.app_type or "unknown",
                    "developer": data.get("developer") or game.developer or "",
                    "publisher": data.get("publisher") or game.publisher or "",
                    "genres": genres,
                    "category_slug": final_slug,
                    "category_name": cat_names.get(final_slug, ""),
                    "source": source,
                })

            self.suggestions_ready.emit(suggestions)
        except Exception as e:
            logger.exception("AI 补全失败")
            self.failed.emit(str(e))


class AIResultsDialog(QDialog):
    """AI 建议确认对话框：用户勾选后才写库。"""

    def __init__(self, suggestions: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI 补全/分类结果确认")
        self.resize(980, 520)
        self._suggestions = suggestions

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "以下是 AI 建议：默认只勾选「预制规则」命中的条目；\n"
            "「AI 建议」条目请自行确认后勾选，确认后才会写入数据库。\n"
            "不会自动写回 Steam；写回仍需你手动点击「应用到 Steam」。"
        ))

        columns = ["选择", "AppID", "名称", "建议分类", "来源", "开发商", "发行商", "标签"]
        self.table = QTableWidget(0, len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(28)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.Stretch)
        self.table.setRowCount(len(suggestions))
        for row, item in enumerate(suggestions):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            # 默认只勾选预制规则命中项；AI 建议默认不勾选，需用户确认（哩米 2026-09-27）
            check.setCheckState(Qt.Checked if item.get("source") == "RULE" else Qt.Unchecked)
            self.table.setItem(row, 0, check)
            values = [
                str(item.get("appid", "")),
                str(item.get("name", "")),
                str(item.get("category_name") or item.get("category_slug") or "未确定"),
                {"RULE": "预制规则", "AI": "AI 建议"}.get(item.get("source", ""), "无分类"),
                str(item.get("developer", "")),
                str(item.get("publisher", "")),
                "、".join(str(x) for x in (item.get("genres") or [])[:5]),
            ]
            for col, value in enumerate(values, start=1):
                self.table.setItem(row, col, QTableWidgetItem(value))
        layout.addWidget(self.table, stretch=1)

        btns = QHBoxLayout()
        select_all = QPushButton("全选")
        select_none = QPushButton("全不选")
        cancel = QPushButton("取消")
        apply_btn = QPushButton("应用选中")
        select_all.clicked.connect(lambda: self._set_all(Qt.Checked))
        select_none.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        cancel.clicked.connect(self.reject)
        apply_btn.clicked.connect(self.accept)
        btns.addWidget(select_all)
        btns.addWidget(select_none)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(apply_btn)
        layout.addLayout(btns)

    def _set_all(self, state) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None:
                item.setCheckState(state)

    def selected_suggestions(self) -> list[dict]:
        selected = []
        for row, item in enumerate(self._suggestions):
            check = self.table.item(row, 0)
            if check is not None and check.checkState() == Qt.Checked:
                selected.append(item)
        return selected


# ── 写入预览确认对话框（M4.6） ──────────────────────────────────


class WritePreviewDialog(QDialog):
    """写入前预览：差异摘要 + 备份位置 + 确认/取消（PRD §5.4.1 流程 4）。"""

    def __init__(self, diff: dict, backup_note: str, cloud_note: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("写入确认（Steam 分类）")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        adds = sum(len(v) for v in diff["add"].values())
        removes = sum(len(v) for v in diff["remove"].values())
        keeps = sum(len(v) for v in diff["keep"].values())
        layout.addWidget(QLabel(f"新增 {adds} 条 / 删除 {removes} 条 / 保留 {keeps} 条"))
        layout.addWidget(QLabel(f"备份位置：{backup_note}"))
        if cloud_note:
            layout.addWidget(QLabel(f"⚠️ {cloud_note}"))
        btns = QHBoxLayout()
        cancel = QPushButton("取消")
        confirm = QPushButton("确认执行")
        confirm.setStyleSheet("background-color: #c0392b; color: white;")
        cancel.clicked.connect(self.reject)
        confirm.clicked.connect(self.accept)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(confirm)
        layout.addLayout(btns)


# ── 主窗口 ──────────────────────────────────────────────────────


def _publisher_matches(pub: str | None, studio: str) -> bool:
    """发行商精确匹配（逗号分隔字段），避免 contains 子串误匹配。"""
    target = (studio or "").strip().casefold()
    if not target:
        return False
    return any(part.strip().casefold() == target for part in (pub or "").split(","))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("库集 v2.2 · 游戏分类")
        self.resize(1200, 720)
        self.setMinimumSize(1000, 600)  # 防布局挤压（哩米打磨 2026-08-14）
        self.steam_path = detect_steam_path()
        self._worker: ScanWorker | None = None
        self._icon_cache: dict = {}  # 游戏网格封面 QIcon 缓存（哩米 2026-08-24）
        self._icon_token = 0         # 分批加载令牌：切列表时取消旧批次
        self._build_ui()
        self.reload_scheme()
        self.reload_games()

    # ── UI 构建 ─────────────────────────────────────────────────
    def _build_ui(self) -> None:
        # 顶栏：方案切换 + 扫描/写入/规则/设置按钮
        self.scheme_combo = QComboBox()
        self.scheme_combo.currentIndexChanged.connect(self._on_scheme_changed)
        self.scan_btn = QPushButton("扫描游戏库")
        self.scan_btn.clicked.connect(self._on_scan)
        self.names_btn = QPushButton("补全元数据")
        self.names_btn.clicked.connect(self._on_fill_names)
        self.names_btn.setToolTip("抓取缺失的完整元数据（名称/开发商/类型/标签），带进度显示")
        rule_btn = QPushButton("规则")
        rule_btn.clicked.connect(self._on_rule)
        settings_btn = QPushButton("设置")
        settings_btn.clicked.connect(self._on_settings)
        feedback_btn = QPushButton("反馈")
        feedback_btn.clicked.connect(self._on_feedback)



        top = QHBoxLayout()
        top.addWidget(QLabel("分类方案："))
        top.addWidget(self.scheme_combo)
        top.addStretch()
        top.addWidget(rule_btn)
        top.addWidget(settings_btn)
        top.addWidget(feedback_btn)
        top.addWidget(self.scan_btn)
        top.addWidget(self.names_btn)


        # 左栏：分类树（树形/级联/厂商三种视图）+ 风格区
        self.category_tree = QTreeView()
        self.category_tree.clicked.connect(self._on_tree_category_clicked)
        from client.ui.cascade_tree import CascadeTree

        self.cascade_tree = CascadeTree([], {})
        self.cascade_tree.category_selected.connect(self._on_cascade_category)
        self.studio_list = QListWidget()  # 厂商视图（发行商 ≥10 款，哩米 2026-08-13）
        self.studio_list.itemClicked.connect(self._on_studio_clicked)
        self.tree_tabs = QTabWidget()
        self.tree_tabs.addTab(self.category_tree, "树形")
        self.tree_tabs.addTab(self.cascade_tree, "级联")
        self.tree_tabs.addTab(self.studio_list, "厂商")
        self.style_list = QListWidget()
        self.style_list.itemClicked.connect(self._on_style_clicked)
        left = QVBoxLayout()
        left.addWidget(self.tree_tabs, 1)
        left.addWidget(QLabel("风格"))
        left.addWidget(self.style_list)

        # 中栏：游戏网格（封面卡片 + 名称在图下；哩米需求 2026-08-12）
        self.game_grid = QListWidget()
        self.game_grid.setViewMode(QListView.IconMode)
        self.game_grid.setResizeMode(QListView.Adjust)
        self.game_grid.setMovement(QListView.Static)
        # 右键复制菜单（哩米需求 2026-08-13：复制 AppID/名称/标签/分类，多选批量）
        self.game_grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.game_grid.customContextMenuRequested.connect(self._on_grid_context_menu)
        self._grid_sizes = {
            "小": (QSize(100, 150), QSize(120, 185)),
            "中": (QSize(140, 210), QSize(165, 245)),
            "大": (QSize(180, 270), QSize(210, 305)),  # 小黑盒风格大图
        }
        self._grid_mode = "小"
        self.game_grid.setIconSize(self._grid_sizes["小"][0])
        self.game_grid.setGridSize(self._grid_sizes["小"][1])
        self.game_grid.setSpacing(6)
        self.game_grid.setSelectionMode(QListWidget.ExtendedSelection)
        self.game_grid.setWordWrap(True)
        self.game_grid.setUniformItemSizes(False)
        # 筛选状态（哩米 2026-08-13：分类树 + 风格标签可叠加生效）
        self._active_category_id: str | None = None
        self._active_style: str | None = None
        self._active_studio: str | None = None
        # 多选模式（哩米 2026-08-14）：分类树点选目标分类集合 → 应用到 Steam
        self._multi_select = False
        self._target_slugs: set[str] = set()
        self._target_styles: set[str] = set()  # 多选目标风格（哩米 2026-08-14）
        self._target_studios: set[str] = set()  # 多选目标厂商（哩米 2026-08-15）
        # R18 系列风格点击重建过列表（只含该风格游戏）——切回分类/其他风格时需恢复全量
        self._r18_list_loaded = False
        # 小黑盒风格 QSS：深色圆角卡片 + hover 高亮 + 选中边框
        self.game_grid.setStyleSheet("""
            QListWidget#gameGrid {
                background: #1b1d23; border: none; padding: 8px;
                outline: 0;
            }
            QListWidget#gameGrid::item {
                border-radius: 6px; padding: 2px;
                color: #cfd2d9;
            }
            QListWidget#gameGrid::item:hover {
                background: rgba(64, 68, 80, 0.6);
            }
            QListWidget#gameGrid::item:selected {
                background: rgba(86, 121, 255, 0.35);
                border: 1px solid #5679ff;
            }
        """)
        self.game_grid.setObjectName("gameGrid")
        size_combo = QComboBox()
        size_combo.addItems(["小", "中", "大"])
        size_combo.currentTextChanged.connect(self._on_grid_size)
        select_all_btn = QPushButton("全选")
        select_all_btn.clicked.connect(self.game_grid.selectAll)
        clear_btn = QPushButton("清除选择")
        clear_btn.clicked.connect(self.game_grid.clearSelection)
        show_all_btn = QPushButton("显示全部")
        show_all_btn.clicked.connect(self._on_show_all)
        self.batch_label = QLabel("")
        self.games_only_check = QCheckBox("仅游戏（隐藏工具软件）")
        self.games_only_check.setChecked(True)
        self.games_only_check.toggled.connect(self.reload_games)
        # 多选模式（哩米 2026-08-14）：分类树左键点选目标分类（可多个），
        # 厂商视图点击 = 该厂商游戏加入网格选中；「应用到 Steam」增量写回
        self.multi_btn = QPushButton("多选")
        self.multi_btn.setCheckable(True)
        self.multi_btn.toggled.connect(self._on_multi_toggled)
        # 多选模式全选（哩米 2026-08-14：多选右边快捷全选网格游戏 + 风格标签全选目标）
        self.multi_select_all_btn = QPushButton("全选")
        self.multi_select_all_btn.setEnabled(False)
        self.multi_select_all_btn.clicked.connect(self._on_multi_select_all)
        self.apply_steam_btn = QPushButton("应用到 Steam")
        self.apply_steam_btn.setEnabled(False)
        self.apply_steam_btn.clicked.connect(self._on_apply_to_steam)
        # 批量删除「库集_」收藏夹（哩米 2026-08-14：只动库集前缀，用户自有不动）
        self.del_kuji_btn = QPushButton("批量删除库集分类")
        self.del_kuji_btn.clicked.connect(self._on_delete_kuji)
        # 全量写回（哩米审查 2026-08-14：GUI 闭环——删除/增量后一键全量同步，
        # 与命令行 write 同语义：增删双向 + 风格收藏夹白名单保留）
        self.full_write_btn = QPushButton("全量写回")
        self.full_write_btn.clicked.connect(self._on_full_writeback)
        # 离线补全（哩米 2026-08-14：v0 可按 Steam 路径取信息——appinfo.vdf
        # 离线解析 name/开发商/发行商/应用类型，不依赖 API key/网络）
        self.offline_meta_btn = QPushButton("离线补全")
        self.offline_meta_btn.clicked.connect(self._on_offline_meta)
        self.ai_fill_btn = QPushButton("AI 补全/分类")
        self.ai_fill_btn.clicked.connect(self._on_ai_fill)
        mid_top = QHBoxLayout()
        mid_top.addWidget(self.multi_btn)
        mid_top.addWidget(self.multi_select_all_btn)
        mid_top.addWidget(self.apply_steam_btn)
        mid_top.addWidget(self.full_write_btn)
        mid_top.addWidget(self.del_kuji_btn)
        mid_top.addWidget(self.offline_meta_btn)
        mid_top.addWidget(self.ai_fill_btn)
        mid_top.addWidget(self.batch_label)
        mid_top.addStretch()
        mid_top.addWidget(self.games_only_check)
        mid_top.addWidget(QLabel("封面尺寸："))
        mid_top.addWidget(size_combo)
        mid_top.addWidget(show_all_btn)
        mid_top.addWidget(select_all_btn)
        mid_top.addWidget(clear_btn)

        # 右栏：详情 + 手动分类指派（M4.2）
        # 封面用独立 QLabel + setPixmap（哩米 2026-08-14：QLabel rich text 的
        # <img> 有图片缓存问题——文字更新但图卡在第一个；setPixmap 确定性替换）
        self.detail_cover = QLabel()
        self.detail_cover.setFixedSize(180, 270)
        self.detail_cover.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.detail_cover.setStyleSheet(
            "background: #23252b; border-radius: 8px; color: #6a6e78;")
        self.detail_label = QLabel("选择游戏查看详情")
        self.detail_label.setWordWrap(True)
        self.detail_label.setAlignment(Qt.AlignTop)
        self.detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)  # 可选中复制
        self.classify_combo = QComboBox()
        self.classify_combo.setEnabled(False)
        apply_btn = QPushButton("应用分类")
        apply_btn.clicked.connect(self._on_apply_classification)
        right = QVBoxLayout()
        right.addWidget(QLabel("详情"))
        right.addWidget(self.detail_cover)
        right.addWidget(self.detail_label)
        right.addWidget(QLabel("手动分类（当前方案）："))
        right.addWidget(self.classify_combo)
        right.addWidget(apply_btn)
        right.addStretch()

        central = QWidget()
        root = QVBoxLayout(central)
        root.addLayout(top)
        body = QHBoxLayout()
        left_widget = QWidget()
        left_widget.setLayout(left)
        body.addWidget(left_widget, 2)  # 左栏缩小（哩米需求：为中间留空间）
        mid = QVBoxLayout()
        mid.addLayout(mid_top)
        mid.addWidget(self.game_grid, 1)
        body.addLayout(mid, 6)  # 中间网格最大
        right_widget = QWidget()
        right_widget.setLayout(right)
        body.addWidget(right_widget, 2)  # 右栏缩小
        root.addLayout(body)
        self.setCentralWidget(central)

        # 点击驱动详情（哩米 2026-08-14：itemSelectionChanged 依赖选择状态变化，
        # 点击已选中图会取消选中、轻微拖动变框选导致详情不更新——改 itemClicked 点击即显示）
        self.game_grid.itemClicked.connect(self._on_game_clicked)

    # ── 数据加载 ─────────────────────────────────────────────────
    def reload_scheme(self) -> None:
        schemes = list_schemes()
        self.scheme_combo.blockSignals(True)
        self.scheme_combo.clear()
        for s in schemes:
            self.scheme_combo.addItem(f"[{'官方' if s.source == 'OFFICIAL' else '自己'}] {s.name}", s.id)
        active = get_active_scheme()
        if active:
            idx = self.scheme_combo.findData(active.id)
            self.scheme_combo.setCurrentIndex(max(idx, 0))
        self.scheme_combo.blockSignals(False)
        self._render_tree(active.id if active else (schemes[0].id if schemes else None))

    def _render_tree(self, scheme_id: str | None) -> None:
        model = QStandardItemModel()
        if scheme_id:
            from client.core.scheme_service import count_classifications_by_category

            categories = get_scheme_tree(scheme_id)
            counts = count_classifications_by_category(scheme_id)  # 节点游戏数（哩米需求）
            by_parent: dict[str | None, list] = {}
            for c in categories:
                by_parent.setdefault(c.parent_id, []).append(c)
            self._add_nodes(model.invisibleRootItem(), by_parent, None, counts)
            styles = get_scheme_styles(scheme_id)
            self.style_list.clear()
            # 风格标签层级：无需补丁/需要补丁 是 R18 的子类（哩米 2026-08-13），
            # 渲染顺序 R18 后紧跟子类（缩进 + 原名存 UserRole 供点击筛选）
            _order = {"肉鸽": 0, "R18": 1, "无需补丁": 2,
                      "需要补丁": 3, "擦边": 4, "已下架": 5}
            styles = sorted(styles, key=lambda s: _order.get(s.name, 99))
            for s in styles:
                if s.name in ("无需补丁", "需要补丁", "擦边"):
                    label = f"　↳ {s.name}（R18）"
                else:
                    label = s.name
                if getattr(self, "_multi_select", False) and s.name in self._target_styles:
                    label += " ✓"  # 多选目标风格标记（哩米 2026-08-14）
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, s.name)
                if s.name in self._target_styles:
                    item.setForeground(QBrush(QColor("#5679ff")))
                self.style_list.addItem(item)
            # 级联视图同步刷新（哩米需求）
            self.cascade_tree.reset(categories, counts)
        self.category_tree.setModel(model)
        self.category_tree.expandAll()
        self.category_tree.setHeaderHidden(True)
        self._refresh_studios()

    def _refresh_studios(self) -> None:
        """厂商视图：发行商（拆分逗号）游戏数 ≥10 的收录（哩米 2026-08-13）。"""
        from client.models import Game
        from client.storage.database import get_session

        session = get_session()
        try:
            counter: dict[str, int] = {}
            for (pub,) in session.query(Game.publisher).filter(
                    Game.del_flag.is_(False), Game.publisher.isnot(None)).all():
                for p in pub.split(","):
                    name = p.strip()
                    if name:
                        counter[name] = counter.get(name, 0) + 1
        finally:
            session.close()
        self.studio_list.clear()
        for name in sorted((k for k, v in counter.items() if v >= 10),
                           key=lambda x: (-counter[x], x)):
            label = f"{name}（{counter[name]}）"
            if getattr(self, "_multi_select", False) and name in self._target_studios:
                label += " ✓"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, name)
            if name in self._target_studios:
                item.setForeground(QBrush(QColor("#5679ff")))
            self.studio_list.addItem(item)

    def _on_studio_clicked(self, item) -> None:
        """厂商视图点击：多选模式 = 该厂商游戏加入网格选中；否则筛选该厂商游戏。"""
        studio = item.data(Qt.UserRole)
        if self._multi_select:
            # 哩米 2026-08-15：多选模式下点厂商 = 把该厂商加入目标集合（再点取消）
            from client.models import Game
            from client.storage.database import get_session

            session = get_session()
            try:
                appids = {
                    g.appid for g in session.query(Game).filter(
                        Game.del_flag.is_(False),
                        Game.publisher.contains(studio)).all()
                    if _publisher_matches(g.publisher, studio)
                }
            finally:
                session.close()
            base = item.text().rsplit("（", 1)[0].rstrip(" ✓")
            if studio in self._target_studios:
                self._target_studios.discard(studio)
                item.setText(f"{base}（{len(appids)}）")
                item.setForeground(QBrush(QColor("#cfd2d9")))
            else:
                self._target_studios.add(studio)
                item.setText(f"{base} ✓（{len(appids)}）")
                item.setForeground(QBrush(QColor("#5679ff")))
            self.statusBar().showMessage(
                f"目标厂商 {len(self._target_studios)} 个：{studio}（{len(appids)} 款，再点一次取消）", 4000)
            return
        self._active_studio = studio
        self._active_category_id = None
        self._active_style = None
        if self._r18_list_loaded:
            self.reload_games()
            self._r18_list_loaded = False
        self.category_tree.clearSelection()
        self.cascade_tree.clear_selection()
        self.style_list.clearSelection()
        self._apply_filters()

    def _on_tree_category_clicked(self, index) -> None:
        """树形视图点击分类：多选模式 toggle 目标分类；否则筛选游戏网格。"""
        category_id = self.category_tree.model().itemFromIndex(index).data(Qt.UserRole)
        if not category_id:
            return
        if self._multi_select:
            self._toggle_target_category(index, category_id)
            return
        self._filter_games_by_category(category_id)

    def _toggle_target_category(self, index, category_id: str) -> None:
        """多选模式：左键单击添加目标分类，再次单击取消（哩米 2026-08-15：
        大类点击 = 目标「库集_大类名」（写回时聚合全部子类游戏）；叶 = 自身）。"""
        from client.models import Category
        from client.storage.database import get_session

        session = get_session()
        try:
            cat = session.query(Category).filter(Category.id == category_id).first()
            if not cat:
                return
            slug = cat.slug  # 大类/叶都作为目标（大类在写回时聚合子类）
        finally:
            session.close()
        item = self.category_tree.model().itemFromIndex(index)
        base = item.text().rstrip(" ✓")
        if slug in self._target_slugs:
            self._target_slugs.discard(slug)
            item.setText(base)
            item.setForeground(QBrush(QColor("#cfd2d9")))
        else:
            self._target_slugs.add(slug)
            item.setText(base + " ✓")
            item.setForeground(QBrush(QColor("#5679ff")))
        self.statusBar().showMessage(
            f"目标分类 {len(self._target_slugs)} 个：{'、'.join(self._target_names()[:6])}"
            + ("…" if len(self._target_slugs) > 6 else ""), 4000)

    def _target_names(self) -> list[str]:
        """目标 slug 集合 → 分类名列表（供写回使用）。"""
        if not self._target_slugs:
            return []
        from client.models import Category
        from client.storage.database import get_session

        session = get_session()
        try:
            cats = session.query(Category).filter(
                Category.slug.in_(self._target_slugs)).all()
            return [c.name for c in cats]
        finally:
            session.close()

    def _toggle_target_style(self, item, style_name: str) -> None:
        """多选模式：风格标签左键 toggle 目标（哩米 2026-08-14，与分类树同交互）。"""
        text = item.text()
        base = text.rstrip(" ✓")
        if style_name in self._target_styles:
            self._target_styles.discard(style_name)
            item.setText(base)
            item.setForeground(QBrush(QColor("#cfd2d9")))
        else:
            self._target_styles.add(style_name)
            item.setText(base + " ✓")
            item.setForeground(QBrush(QColor("#5679ff")))
        self.statusBar().showMessage(
            f"目标：分类 {len(self._target_slugs)} 个 + 风格 {len(self._target_styles)} 个", 3000)

    def _on_multi_select_all(self) -> None:
        """全选 = 全部分类选择（分类树全部节点含大类/子类 + 全部风格），不选游戏。

        哩米 2026-08-14 修正：全选指的是全部分类选择而不是游戏选择；
        2026-08-15 补充：大类的子类（WRPG 等非叶）同样纳入全选范围。
        """
        scheme = get_active_scheme()
        if not scheme:
            return
        from client.models import Category, Tag
        from client.storage.database import get_session

        session = get_session()
        try:
            slugs = [c.slug for c in session.query(Category).filter(
                Category.scheme_id == scheme.id,
                Category.del_flag.is_(False)).all()]
            styles = [t.name for t in session.query(Tag).filter(
                Tag.scheme_id == scheme.id, Tag.dimension == "STYLE",
                Tag.del_flag.is_(False)).all()]
        finally:
            session.close()
        self._target_slugs = set(slugs)
        self._target_styles = set(styles)
        self._target_studios.clear()
        self._render_tree(scheme.id)  # 重建树/风格列表（全部带 ✓ 标记）
        self.statusBar().showMessage(
            f"已全选分类 {len(slugs)} 个 + 风格 {len(styles)} 个（再点任一分类可取消）", 4000)

    def _on_multi_toggled(self, checked: bool) -> None:
        """进入/退出多选模式：进入清空筛选+展开分类树；退出清空目标。"""
        self._multi_select = checked
        self.apply_steam_btn.setEnabled(checked)
        self.multi_select_all_btn.setEnabled(checked)
        if checked:
            # 哩米审查 2026-08-14：进入多选清空筛选——避免「全选/厂商点选」
            # 只覆盖筛选子集导致应用范围与预期不符
            self._active_category_id = None
            self._active_style = None
            self._active_studio = None
            self._r18_list_loaded = False
            self.category_tree.clearSelection()
            self.cascade_tree.clear_selection()
            self.style_list.clearSelection()
            self.studio_list.clearSelection()
            self._target_studios.clear()
            self.reload_games()  # 恢复全量列表
            self._render_tree(get_active_scheme().id if get_active_scheme() else None)  # 刷新树/风格/厂商勾选状态
            self.category_tree.expandAll()  # 哩米：多选时强制展开（子类可见）
            self.statusBar().showMessage(
                "多选模式：树形/厂商/风格左键点选目标，「全选」= 全部分类", 5000)
        else:
            self._target_slugs.clear()
            self._target_styles.clear()
            self._target_studios.clear()
            self._render_tree(get_active_scheme().id if get_active_scheme() else None)

    def _missing_metadata_count(self) -> int:
        """统计还缺元数据的游戏数量（与 AIFillWorker 判定一致）。"""
        from client.models import Game
        from client.storage.database import get_session

        session = get_session()
        try:
            games = session.query(Game).filter(Game.del_flag.is_(False)).all()
            return sum(
                1 for g in games
                if g.name.startswith("[")
                or not (g.steam_tags and g.steam_tags != "[]")
                or g.app_type in ("unknown", "")
            )
        finally:
            session.close()

    def _ensure_safety_notice(self) -> bool:
        """首次写回前显示使用须知。"""
        from client.config import get_settings

        settings = get_settings()
        if settings.safety_notice_shown:
            return True
        ret = QMessageBox.question(
            self, "写回使用须知",
            "写回 Steam 前请确认：\n\n"
            "1. 必须完全关闭 Steam，否则可能被覆盖。\n"
            "2. 写回前会自动备份；如出现异常，可在设置-备份与恢复中恢复最近备份。\n"
            "3. AI 补全/分类会把游戏名称和 AppID 发送给 AI 服务商，可能产生费用。\n"
            "4. 遇到问题请加反馈 QQ 群：1107903014，或点击顶栏反馈导出诊断包。\n\n"
            "点击确定表示已知晓。",
            QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Ok,
        )
        if ret != QMessageBox.Ok:
            return False
        settings.safety_notice_shown = True
        settings._persist()
        return True

    def _on_apply_to_steam(self) -> None:
        """多选模式「应用到 Steam」：目标分类/风格 → 增量写回对应「库集_」收藏夹。"""
        if not self._ensure_safety_notice():
            return
        if not self._target_slugs and not self._target_styles and not self._target_studios:
            QMessageBox.information(
                self, "未选择分类", "请先左键点击树形/厂商/风格标签选择目标分类。")
            return
        from client.core.vdf_ops import (
            KUJI_PREFIX, backup_store, get_store_path, is_steam_running, update_categories,
        )
        from client.models import Category, CategoryScheme, Game, GameClassification
        from client.storage.database import get_session

        if is_steam_running():
            QMessageBox.warning(self, "Steam 运行中", "写回收藏夹前请先关闭 Steam。")
            return
        scheme = get_active_scheme()
        if not scheme:
            return
        session = get_session()
        try:
            valid_appids = {
                g.appid for g in session.query(Game.appid).filter(
                    Game.del_flag.is_(False)).all()
            }
            cats = {c.slug: c for c in session.query(Category).filter(
                Category.scheme_id == scheme.id,
                Category.slug.in_(self._target_slugs),
                Category.del_flag.is_(False)).all()}
            # 目标分类下的游戏 → 收藏夹名「库集_」+ 类名
            # （哩米 2026-08-15：叶 = 直接挂载；大类 = 聚合全部后代叶的游戏——
            # 点 RPG 大类 → 库集_RPG 含所有 RPG 游戏）
            desired: dict[str, set[str]] = {}
            all_cats = session.query(Category).filter(
                Category.scheme_id == scheme.id,
                Category.del_flag.is_(False)).all()
            cat_by_id = {c.id: c for c in all_cats}
            for slug, cat in cats.items():
                if cat.is_leaf:
                    target_ids = {cat.id}
                else:
                    # 聚合后代（含自身）
                    target_ids = {cat.id}
                    changed = True
                    while changed:
                        changed = False
                        for c in all_cats:
                            if c.parent_id in target_ids and c.id not in target_ids:
                                target_ids.add(c.id)
                                changed = True
                appids = {
                    gc.game_appid for gc in session.query(GameClassification).filter(
                        GameClassification.scheme_id == scheme.id,
                        GameClassification.dimension == "TYPE",
                        GameClassification.category_id.in_(target_ids),
                        GameClassification.del_flag.is_(False)).all()
                } & valid_appids
                if appids:
                    desired[KUJI_PREFIX + cat.name] = appids
            # 目标风格下的游戏 → 收藏夹名「库集_」+ 风格名（哩米 2026-08-14）
            if self._target_styles:
                from client.models import Tag
                tags = {t.name: t.id for t in session.query(Tag).filter(
                    Tag.scheme_id == scheme.id, Tag.dimension == "STYLE",
                    Tag.del_flag.is_(False)).all()}
                for sname in self._target_styles:
                    tag_id = tags.get(sname)
                    if not tag_id:
                        continue
                    appids = {
                        gc.game_appid for gc in session.query(GameClassification).filter(
                            GameClassification.scheme_id == scheme.id,
                            GameClassification.dimension == "STYLE",
                            GameClassification.tag_id == tag_id,
                            GameClassification.del_flag.is_(False)).all()
                    } & valid_appids
                    if appids:
                        desired[KUJI_PREFIX + sname] = appids
            # 目标厂商下的游戏 → 收藏夹名「库集_」+ 厂商名（哩米 2026-08-15）
            if self._target_studios:
                for studio in self._target_studios:
                    appids = {
                        g.appid for g in session.query(Game).filter(
                            Game.del_flag.is_(False),
                            Game.publisher.contains(studio)).all()
                        if _publisher_matches(g.publisher, studio)
                    }
                    if appids:
                        desired[KUJI_PREFIX + studio] = appids
            names = list(desired)
        finally:
            session.close()
        if not desired:
            QMessageBox.information(self, "无游戏", "目标分类下暂无游戏。")
            return
        # 确认弹窗（哩米 2026-08-15：防误操作——目标数量与收藏夹预览）
        preview_names = "\n".join(n.replace(KUJI_PREFIX, "库集_") for n in names[:8])
        if len(names) > 8:
            preview_names += f"\n…（共 {len(names)} 个收藏夹）"
        ret = QMessageBox.question(
            self, "确认应用到 Steam",
            f"将更新 {len(names)} 个收藏夹（只加不删）：\n{preview_names}\n\n"
            "写回前自动备份；如出现异常，可在「设置 → 备份与恢复」中恢复最近备份。\n"
            "确定继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        from client.core.scanner import find_steamid32
        from client.utils.steam_path import detect_steam_path

        steam_path = detect_steam_path()
        steamid32 = find_steamid32(steam_path) if steam_path else None
        if not steam_path or not steamid32:
            QMessageBox.warning(self, "Steam 未找到", "未检测到 Steam 账号。")
            return
        backup_store(steam_path, steamid32)
        result = update_categories(steam_path, steamid32, desired)
        QMessageBox.information(
            self, "应用完成",
            f"已更新 {result['written']} 个收藏夹：\n"
            + "\n".join(n.replace(KUJI_PREFIX, "库集_") for n in names)
            + "\n\n请打开 Steam 查看（写后验证 "
            + ("通过 ✓" if result["verified"] else "失败 ✗") + "）")

    def _add_nodes(self, parent, by_parent: dict, parent_id: str | None, counts: dict) -> None:
        for c in sorted(by_parent.get(parent_id, []), key=lambda x: x.sort_order):
            label = f"{c.icon} {c.name}" if c.icon else c.name
            count = counts.get(c.id, 0)
            if count:
                label = f"{label}（{count}）"
            if getattr(self, "_multi_select", False) and c.slug in self._target_slugs:
                label += " ✓"  # 多选模式目标标记（哩米 2026-08-14，树重建后保留）
            item = QStandardItem(label)
            item.setEditable(False)
            item.setData(c.id, Qt.UserRole)  # 分类 id（筛选用）
            if c.slug in self._target_slugs:
                item.setForeground(QBrush(QColor("#5679ff")))
            parent.appendRow(item)
            self._add_nodes(item, by_parent, c.id, counts)

    def reload_games(self) -> None:
        """填充游戏网格：封面卡片 + 名称在图下（缩略图缓存，防卡顿）。"""
        session = get_session()
        try:
            query = session.query(Game).filter(Game.del_flag.is_(False))
            if self.games_only_check.isChecked():
                # 仅游戏：隐藏明确非游戏（software/dlc/demo/music/video/application）；
                # unknown = 查不到商店页的真游戏（下架/锁区），必须保留（哩米指出 1213 案例）
                query = query.filter(Game.app_type.in_(
                    ("game", "unknown", "delisted")))
            games = query.order_by(Game.name).all()
            self._load_game_items(games)
            self.statusBar().showMessage(
                f"共 {len(games)} 款 · 右键复制 AppID/名称/标签/分类", 5000)
        finally:
            session.close()

    def _load_game_items(self, games: list) -> None:
        """构建游戏网格列表项（封面 + 名称；demo 加 [Demo] 标记——R18 标签可见性）。

        2026-08-24（哩米反馈卡顿）：条目先一次性建立，封面图标分批异步设置，
        避免一次性缩放 1000+ 张缩略图导致 UI 无响应。
        """
        icon_size = self._grid_sizes[self._grid_mode][0]
        # 大图模式用原图（300×450）保证清晰度；小/中模式用缩略图缓存
        use_original = self._grid_mode == "大"

        self.game_grid.clear()
        self._icon_token += 1
        token = self._icon_token
        if not games:  # 空态提示（哩米打磨 2026-08-14：搜索/筛选无结果不白屏；
            # 新用户首次启动空库 → 引导扫描，哩米 2026-08-14 分发场景）
            hint = QListWidgetItem(
                "还没有游戏\n\n"
                "首次使用请点击左上角「扫描游戏库」\n"
                "（读取本机 Steam 游戏，预置分类/元数据已内置，无需联网）")
            hint.setFlags(Qt.NoItemFlags)
            hint.setTextAlignment(Qt.AlignCenter)
            self.game_grid.addItem(hint)
            return

        pending: list[tuple[QListWidgetItem, object]] = []
        for g in games:
            item = QListWidgetItem()
            label = f"{g.name} [Demo]" if g.app_type == "demo" else g.name
            item.setText(label)
            item.setToolTip(f"{g.name}\nAppID: {g.appid}\n"
                            f"{'家庭共享' if g.source == 'FAMILY_SHARED' else '自有'}")
            item.setData(Qt.UserRole, g.appid)       # appid
            item.setData(Qt.UserRole + 1, g.source)  # 来源
            item.setData(Qt.UserRole + 2, g.existing_steam_categories or "[]")
            self.game_grid.addItem(item)
            pending.append((item, g))

        batch_size = 40

        def apply_icons(start: int) -> None:
            if token != self._icon_token:
                return  # 列表已被重新加载，丢弃旧批次
            end = min(start + batch_size, len(pending))
            for i in range(start, end):
                item, g = pending[i]
                item.setIcon(self._cover_icon_for(g.appid, use_original, icon_size))
            if end < len(pending):
                QTimer.singleShot(0, lambda e=end: apply_icons(e))

        apply_icons(0)

    def _cover_icon_for(self, appid: str, use_original: bool, icon_size: QSize) -> QIcon:
        """获取游戏封面 QIcon（带缓存；缺失封面不实时生成，避免阻塞 UI）。"""
        from client.core.cover_art import get_cover_path, get_thumb_path

        key = (appid, icon_size.width(), icon_size.height(), use_original)
        cached = self._icon_cache.get(key)
        if cached is not None:
            return cached
        path = get_cover_path(appid) if use_original else get_thumb_path(appid, generate=False)
        if path:
            icon = QIcon(QPixmap(str(path)).scaled(
                icon_size.width(), icon_size.height(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            icon = self._placeholder_icon(icon_size)
        self._icon_cache[key] = icon
        return icon

    def _placeholder_icon(self, icon_size: QSize | None = None) -> QIcon:
        """无封面占位：深色方块（Steam 风格），按尺寸缓存。"""
        if icon_size is None:
            icon_size = self._grid_sizes[self._grid_mode][0]
        key = ("__placeholder__", icon_size.width(), icon_size.height())
        cached = self._icon_cache.get(key)
        if cached is not None:
            return cached
        pix = QPixmap(icon_size.width(), icon_size.height())
        pix.fill(QColor(32, 34, 40))
        icon = QIcon(pix)
        self._icon_cache[key] = icon
        return icon

    # ── 交互 ─────────────────────────────────────────────────────
    def _on_scheme_changed(self) -> None:
        scheme_id = self.scheme_combo.currentData()
        if scheme_id:
            from client.core.scheme_service import activate_scheme

            activate_scheme(scheme_id)
            self._render_tree(scheme_id)
            self._refresh_classify_combo()

    def _refresh_classify_combo(self) -> None:
        """手动分类下拉：当前方案的叶节点。"""
        self.classify_combo.clear()
        scheme = get_active_scheme()
        if scheme:
            leaves = [c for c in get_scheme_tree(scheme.id) if c.is_leaf]
            for c in leaves:
                self.classify_combo.addItem(c.path, c.id)
            self.classify_combo.setEnabled(bool(leaves))

    def _on_settings(self) -> None:
        from client.ui.settings_dialog import SettingsDialog

        dlg = SettingsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            from client.utils.steam_path import detect_steam_path

            self.steam_path = detect_steam_path()

    def _on_feedback(self) -> None:
        """生成反馈诊断包（日志/系统信息/脱敏配置），方便用户反馈问题。"""
        try:
            from client.utils.feedback import export_feedback

            zip_path = export_feedback()
        except Exception as e:
            logger.exception("反馈包生成失败")
            QMessageBox.critical(self, "反馈包生成失败", str(e))
            return
        QMessageBox.information(
            self, "反馈包已生成",
            f"已生成反馈包：\n{zip_path}\n\n"
            "请把这个 zip 和你遇到的问题截图一起发送给开发者。\n"
            "包内已自动脱敏，不会包含你的 API Key。",
        )
        try:
            import os as _os
            _os.startfile(str(zip_path.parent))  # 自动打开所在文件夹
        except Exception:
            pass

    def _on_rule(self) -> None:
        """规则管理 MVP：新建/编辑规则（当前方案）。"""
        scheme = get_active_scheme()
        if not scheme:
            return
        session = get_session()
        try:
            from client.core.rule_engine import rule_matches

            from client.models import ClassificationRule, Game as GameModel

            from client.ui.rule_dialog import RuleDialog

            games = [
                {"appid": g.appid, "steam_tags": []}
                for g in session.query(GameModel).filter(GameModel.del_flag.is_(False)).all()
            ]
            dlg = RuleDialog(scheme.id, games, parent=self)
            if dlg.exec() != QDialog.Accepted or not dlg.result:
                return
            from client.core.scheme_service import get_scheme_tree

            target = next(
                (c for c in get_scheme_tree(scheme.id)
                 if c.slug == dlg.result["target_category_slug"]), None)
            if not target:
                return
            import json as _json

            rule = ClassificationRule(
                scheme_id=scheme.id,
                name=dlg.result["name"],
                dimension="TYPE",
                target_category_id=target.id,
                conditions=_json.dumps(dlg.result["conditions"], ensure_ascii=False),
            )
            session.add(rule)
            session.commit()
            logger.info(f"规则已保存: {rule.name}")
        finally:
            session.close()

    def _on_apply_classification(self) -> None:
        """应用分类（M4.2）：选中 1 款或多款游戏 → 下拉分类（哩米打磨 2026-08-14：
        原单选限制导致多选时按钮静默无反应——统一单/多选都应用）。"""
        items = self.game_grid.selectedItems()
        if not items:
            QMessageBox.information(self, "未选择游戏", "请先选择游戏（可 Ctrl 多选或全选）。")
            return
        category_id = self.classify_combo.currentData()
        if not category_id:
            QMessageBox.information(self, "未选择分类", "请先选择目标分类。")
            return
        scheme = get_active_scheme()
        if not scheme:
            return
        from client.core.classifier import apply_classification

        for item in items:
            apply_classification(item.data(Qt.UserRole), scheme.id, category_id,
                                 source="MANUAL")
        QMessageBox.information(
            self, "已应用",
            f"已为 {len(items)} 款游戏应用分类：{self.classify_combo.currentText()}")
        self.reload_games()
        self._refresh_tree()

    def _refresh_tree(self) -> None:
        """刷新当前激活方案的分类树（节点游戏数实时更新）。"""
        active = get_active_scheme()
        if active:
            self._render_tree(active.id)

    def _on_scan(self) -> None:
        if not self.steam_path:
            QMessageBox.warning(self, "Steam 未找到", "未检测到 Steam，请在设置中手动指定路径。")
            return
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "扫描中", "扫描正在进行，请稍候。")
            return
        from client.config import get_settings

        settings = get_settings()
        if settings.steam_api_key:
            scan_tip = "\n\n提示：家庭共享游戏依赖 Steam 库页面缓存；如数量偏少，请在 Steam 库中滚动一次后重新扫描。"
        else:
            scan_tip = ("\n\n提示：未配置 Steam Web API Key，自有游戏可能不完整；"
                        "家庭共享游戏依赖 Steam 库页面缓存，数量偏少时请在 Steam 库中滚动后重扫。")
        self.scan_btn.setEnabled(False)  # 防重入
        self._worker = ScanWorker(
            self.steam_path, None, settings.steam_api_key, settings.proxy, self
        )
        self._worker.finished_ok.connect(
            lambda total, shared: (
                self.scan_btn.setEnabled(True),
                QMessageBox.information(
                    self, "扫描完成",
                    f"共 {total} 款（自有 {total - shared} + 家庭共享 {shared}）" + scan_tip),
                self.reload_games(),
                self._refresh_tree(),  # 分类树数量刷新（哩米需求）
            )
        )
        self._worker.failed.connect(
            lambda msg: (self.scan_btn.setEnabled(True),
                         QMessageBox.critical(self, "扫描失败", msg))
        )
        self._worker.start()

    def _on_fill_names(self) -> None:
        """补全元数据（独立后台任务）：appdetails 全字段 + 进度条（哩米需求 2026-08-12）。"""
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "任务进行中", "有任务正在运行，请稍候。")
            return
        if not self.steam_path:
            QMessageBox.warning(self, "Steam 未找到", "未检测到 Steam。")
            return
        from client.core.scanner import find_steamid32

        steamid32 = find_steamid32(self.steam_path)
        if not steamid32:
            QMessageBox.warning(self, "无用户目录", "未找到 userdata 账号目录。")
            return
        from PySide6.QtWidgets import QProgressDialog

        self.names_btn.setEnabled(False)
        self._worker = MetadataFillWorker(self.steam_path, steamid32, self)
        self._progress = QProgressDialog("补全元数据（名称/开发商/类型/标签）…", "取消", 0, 100, self)
        self._progress.setWindowTitle("元数据补全")
        self._progress.setMinimumDuration(0)

        def _on_progress(done: int, total: int) -> None:
            self._progress.setMaximum(max(total, 1))
            self._progress.setValue(done)
            self._progress.setLabelText(f"补全元数据 {done}/{total}（名称/开发商/类型/标签）")

        self._worker.progress.connect(_on_progress)
        self._progress.canceled.connect(self._worker.requestInterruption)
        self._worker.cancelled.connect(
            lambda filled, classified: (
                self._progress.close(),
                self.ai_fill_btn.setEnabled(True),
                QMessageBox.information(
                    self, "AI 已取消",
                    f"已停止后续请求。本轮已补全 {filled} 款，分类 {classified} 款。"),
                self.reload_games(),
                self._refresh_tree(),
            )
        )
        self._worker.finished_ok.connect(
            lambda n: (self._progress.close(), self.names_btn.setEnabled(True),
                       QMessageBox.information(self, "补全完成", f"本轮补全 {n} 款元数据"),
                       self.reload_games()))
        self._worker.failed.connect(
            lambda msg: (self._progress.close(), self.names_btn.setEnabled(True),
                         QMessageBox.critical(self, "补全失败", msg)))
        self._worker.start()

    def _on_ai_fill(self) -> None:
        """AI 补全/分类：后台只收集建议，确认后才写库。"""
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "任务进行中", "有任务正在运行，请稍候。")
            return
        from PySide6.QtWidgets import QProgressDialog

        ret = QMessageBox.question(
            self, "AI 补全/分类",
            f"将对最多 {AIFillWorker.MAX_BATCH} 款缺少元数据的游戏调用 AI。\n\n"
            "这可能需要较长时间，并可能产生 API 费用。\n"
            "AI 只会生成建议，确认后才会写入数据库。是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return

        self.ai_fill_btn.setEnabled(False)
        self._worker = AIFillWorker(self)
        self._progress = QProgressDialog("AI 补全/分类（生成建议）…", "取消", 0, 100, self)
        self._progress.setWindowTitle("AI 补全/分类")
        self._progress.setMinimumDuration(0)

        def _on_progress(done: int, total: int) -> None:
            self._progress.setMaximum(max(total, 1))
            self._progress.setValue(done)
            self._progress.setLabelText(f"AI 补全/分类 {done}/{total}")

        self._worker.progress.connect(_on_progress)
        self._worker.suggestions_ready.connect(self._on_ai_suggestions_ready)
        self._worker.cancelled.connect(
            lambda collected, _classified: (
                self._progress.close(),
                self.ai_fill_btn.setEnabled(True),
                QMessageBox.information(
                    self, "AI 已取消",
                    f"已收集 {collected} 条建议，未写入数据库。"),
            )
        )
        self._worker.failed.connect(
            lambda msg: (self._progress.close(), self.ai_fill_btn.setEnabled(True),
                         QMessageBox.critical(self, "AI 补全失败", msg)))
        self._worker.start()

    def _on_ai_suggestions_ready(self, suggestions: list) -> None:
        """AI 建议就绪：弹窗让用户确认后再写库。"""
        self._progress.close()
        self.ai_fill_btn.setEnabled(True)
        if not suggestions:
            QMessageBox.information(self, "没有需要处理的游戏", "库里暂时没有缺少元数据的游戏。")
            return
        dlg = AIResultsDialog(suggestions, self)
        if dlg.exec() != QDialog.Accepted:
            QMessageBox.information(self, "已取消", "AI 建议未应用，数据库没有改动。")
            return
        selected = dlg.selected_suggestions()
        if not selected:
            QMessageBox.information(self, "未选择", "没有勾选任何建议，数据库没有改动。")
            return
        try:
            from client.core.ai_apply import apply_ai_suggestions

            filled, classified = apply_ai_suggestions(selected)
        except Exception as e:
            logger.exception("应用 AI 建议失败")
            QMessageBox.critical(self, "应用失败", str(e))
            return
        remain = self._missing_metadata_count()
        QMessageBox.information(
            self, "AI 建议已应用",
            f"已写入元数据 {filled} 款，分类 {classified} 款。\n"
            f"还有 {remain} 款缺元数据，可再次点击继续处理。",
        )
        self.reload_games()
        self._refresh_tree()

    def _on_write(self) -> None:
        """写入流程：差异预览 → Steam 检测 → 备份 → 写入（铁律三条件）。"""
        if not self.steam_path:
            QMessageBox.warning(self, "Steam 未找到", "未检测到 Steam。")
            return
        # 目标分类：MVP 演示用「库集_<顶级分类名>」映射（真实规则管道在 UI 编辑器接入后替换）
        target = self._build_target_from_rules()
        if not target:
            QMessageBox.information(self, "无目标分类", "当前没有可写入的分类目标。")
            return

        from client.core.scanner import find_steamid32

        steamid32 = find_steamid32(self.steam_path)
        if not steamid32:
            QMessageBox.warning(self, "无用户目录", "未找到 userdata 账号目录。")
            return

        # 1) Steam 运行检测（M3.6）
        if vdf_ops.is_steam_running():
            QMessageBox.warning(
                self, "Steam 正在运行",
                "写入前必须关闭 Steam（否则可能被覆盖）。请关闭 Steam 后重试。")
            return

        # 2) 差异分析（M3.2）
        from client.core.existing_categories import read_existing_categories

        current = read_existing_categories(self.steam_path, steamid32)
        diff = vdf_ops.build_diff(current, target)

        # 3) Cloud 提示（POC-7 结论）
        status = vdf_ops.cloud_status(self.steam_path, steamid32)
        cloud_note = ""
        if status["modified_pending"]:
            cloud_note = "检测到待同步的 Steam Cloud 修改，写入可能被云端覆盖，建议先在 Steam 完成同步。"

        # 4) 预览确认（M4.6 / PRD §5.4.1 流程 4）
        dlg = WritePreviewDialog(diff, backup_note=str(vdf_ops.backup_dir()), cloud_note=cloud_note)
        if dlg.exec() != QDialog.Accepted:
            return

        # 5) 备份（M3.1）→ 写入（M3.8）；审查 L3（2026-08-13）：异常也自动回滚
        try:
            record = vdf_ops.backup_store(self.steam_path, steamid32, notes="UI 写入")
        except Exception as e:
            logger.exception("备份失败（未写入任何内容）")
            QMessageBox.critical(self, "写入失败",
                                 f"备份失败，未写入任何内容：\n{e}")
            return
        try:
            result = vdf_ops.write_categories(self.steam_path, steamid32, target)
            if not result["verified"]:
                vdf_ops.rollback(record, vdf_ops.get_store_path(self.steam_path, steamid32))
                QMessageBox.critical(self, "写入失败", "写入后验证失败，已自动回滚。")
                return
        except Exception as e:
            # L3：写入异常（Steam 中途启动/文件占用等）→ 自动回滚到备份
            logger.exception("写入异常，自动回滚")
            try:
                vdf_ops.rollback(record, vdf_ops.get_store_path(self.steam_path, steamid32))
                rollback_ok = True
            except Exception:
                logger.exception("回滚失败")
                rollback_ok = False
            QMessageBox.critical(
                self, "写入失败",
                f"写入失败：{e}\n\n"
                + ("已自动回滚到写入前状态。" if rollback_ok
                   else "⚠️ 自动回滚也失败！请勿关闭 Steam 或修改分类，"
                        f"备份在 {record.file_path}，可手动恢复。"))
            return
        QMessageBox.information(
            self, "写入完成",
            f"已同步 {result['written']} 个分类（含 {result.get('deleted', 0)} 个旧分类清理）"
            f"并验证通过。\n"
            "提醒：如启用了 Steam Cloud，请勿在其他机器打开 Steam，否则分类可能被云端覆盖。")
        self.reload_games()

    def _build_target_from_rules(self) -> dict[str, list[str]]:
        """生成写入目标 {appid: [分类名]}：手动分类（GameClassification）+ 规则命中。

        分类名使用 Category.path（与 Steam 收藏夹名一致）；空规则/无手动分类时返回空。
        """
        session = get_session()
        try:
            scheme = get_active_scheme()
            if not scheme:
                return {}
            from client.models import ClassificationRule, Game as GameModel

            from client.core.rule_engine import rule_matches

            target: dict[str, list[str]] = {}
            # 只处理已入库游戏，避免预置元数据里的 appid 写入 Steam
            valid_appids = {
                g.appid for g in session.query(GameModel.appid).filter(
                    GameModel.del_flag.is_(False)).all()
            }
            # 1) 手动分类（TYPE 维度，MANUAL 来源）
            from client.models import Category, GameClassification

            manual = (
                session.query(GameClassification, Category)
                .join(Category, GameClassification.category_id == Category.id)
                .filter(
                    GameClassification.scheme_id == scheme.id,
                    GameClassification.dimension == "TYPE",
                    GameClassification.del_flag.is_(False),
                )
                .all()
            )
            for gc, cat in manual:
                if gc.game_appid not in valid_appids:
                    continue
                # 收藏夹名用叶节点名（2026-08-13 修复：原 cat.path 含「/」，
                # Steam 收藏夹名不允许斜杠；叶名全局唯一）
                target.setdefault(gc.game_appid, []).append(
                    vdf_ops.KUJI_PREFIX + cat.name)  # L2：库集前缀（全量同步范围）

            # 2) 规则命中（当前方案启用的规则，按 priority）
            rules = (
                session.query(ClassificationRule)
                .filter(
                    ClassificationRule.scheme_id == scheme.id,
                    ClassificationRule.is_enabled.is_(True),
                    ClassificationRule.del_flag.is_(False),
                )
                .order_by(ClassificationRule.priority)
                .all()
            )
            if rules:
                from client.models import Category as CatModel

                cat_map = {c.id: c for c in session.query(CatModel).filter(
                    CatModel.del_flag.is_(False)).all()}
                for game in session.query(GameModel).filter(GameModel.del_flag.is_(False)).all():
                    # 规则字段完整传入（2026-08-13 修复：原仅 appid+空 steam_tags，
                    # developer 等规则永不命中）；steam_tags 是 JSON 字符串
                    try:
                        tags = json.loads(game.steam_tags or "[]")
                    except ValueError:
                        tags = []
                    gdict = {
                        "appid": game.appid,
                        "steam_tags": tags if isinstance(tags, list) else [],
                        "developer": game.developer,
                        "publisher": game.publisher,
                    }
                    for rule in rules:
                        try:
                            cond = json.loads(rule.conditions)
                            matched = rule_matches({"conditions": cond}, gdict)
                        except Exception as e:
                            logger.warning(f"规则执行失败已跳过 [{rule.name}]: {e}")
                            continue
                        if matched:
                            cat = cat_map.get(rule.target_category_id)
                            if cat:
                                target.setdefault(game.appid, []).append(
                                    vdf_ops.KUJI_PREFIX + cat.name)  # L2 前缀
                            break
            return target
        finally:
            session.close()

    def _on_game_clicked(self, item) -> None:
        """点击游戏 → 立即显示该游戏详情（点击驱动，哩米 2026-08-14）。

        多选（Ctrl 点击）时显示已选数量；单选/点击已选中项都刷新详情——
        修复 itemSelectionChanged 依赖选择变化导致「只显示第一个点击的图」。
        """
        items = self.game_grid.selectedItems()
        if len(items) > 1:
            self.batch_label.setText(f"已选 {len(items)} 款游戏（可批量应用分类）")
            self.detail_label.setText(f"已选择 {len(items)} 款游戏\n\n可在右侧选择分类后点「批量应用分类」")
            return
        self.batch_label.setText("")
        self._show_game_detail(item)

    def _show_game_detail(self, item) -> None:
        """渲染单款游戏详情（封面大图 + 开发商/发行商 + 现有分类）。"""
        name = item.text()
        appid = item.data(Qt.UserRole)
        source = item.data(Qt.UserRole + 1)
        cats = json.loads(item.data(Qt.UserRole + 2) or "[]")
        # 开发商/发行商（哩米需求 2026-08-13）
        from client.models import Game
        from client.storage.database import get_session

        session = get_session()
        try:
            g = session.query(Game).filter(Game.appid == appid).first()
            developer = g.developer if g else None
            publisher = g.publisher if g else None
        finally:
            session.close()
        dev_line = f"开发商: {developer}<br/>" if developer else ""
        pub_line = f"发行商: {publisher}<br/>" if publisher else ""
        # 封面大图：独立 QLabel + setPixmap（确定性替换，修复 rich text img 缓存）
        from PySide6.QtGui import QPixmap
        from client.core.cover_art import get_cover_path

        path = get_cover_path(appid)
        if path:
            pix = QPixmap(str(path))
            if not pix.isNull():
                self.detail_cover.setPixmap(pix.scaled(
                    180, 270, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                self.detail_cover.clear()
                self.detail_cover.setText("无封面")
        else:
            self.detail_cover.clear()
            self.detail_cover.setText("无封面")
        self.detail_label.setTextFormat(Qt.RichText)
        self.detail_label.setText(
            f"{name}<br/><br/>"
            f"AppID: {appid}<br/>来源: {source}<br/>"
            f"{dev_line}{pub_line}"
            f"现有 Steam 分类: {'、'.join(cats) or '无'}"
        )
        # 单选时下拉预选该游戏当前分类（哩米打磨 2026-08-14：改分类直接确认即可）
        if self.classify_combo.count() and len(self.game_grid.selectedItems()) == 1:
            cur = self._game_type_slug(appid)
            idx = self.classify_combo.findData(cur) if cur else -1
            self.classify_combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _game_type_slug(self, appid: str) -> str | None:
        """查游戏当前 TYPE 分类 slug（供下拉预选）。"""
        from client.models import Category, CategoryScheme, GameClassification
        from client.storage.database import get_session

        session = get_session()
        try:
            scheme = (
                session.query(CategoryScheme)
                .filter(CategoryScheme.source == "OFFICIAL",
                        CategoryScheme.del_flag.is_(False)).first()
            )
            if not scheme:
                return None
            gc = (
                session.query(GameClassification)
                .filter(GameClassification.game_appid == appid,
                        GameClassification.scheme_id == scheme.id,
                        GameClassification.dimension == "TYPE",
                        GameClassification.category_id.isnot(None),
                        GameClassification.del_flag.is_(False))
                .first()
            )
            if not gc:
                return None
            cat = session.query(Category).filter(Category.id == gc.category_id).first()
            return cat.slug if cat else None
        finally:
            session.close()

    def _selected_appids(self) -> list[str]:
        """当前选中项（含多选）的 appid 列表。"""
        return [item.data(Qt.UserRole) for item in self.game_grid.selectedItems()]

    def _on_grid_context_menu(self, pos) -> None:
        """右键菜单：复制 AppID/名称/标签/分类（多选批量，哩米需求 2026-08-13）。"""
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QMenu

        items = self.game_grid.selectedItems()
        if not items:
            return
        menu = QMenu(self)
        act_appid = menu.addAction("复制 AppID")
        act_name = menu.addAction("复制名称")
        act_tags = menu.addAction("复制标签")
        act_cat = menu.addAction("复制分类")
        act = menu.exec(self.game_grid.mapToGlobal(pos))
        if act is None:
            return

        session = get_session()
        try:
            from client.models import Category, Game, GameClassification

            games = {
                g.appid: g for g in session.query(Game).filter(
                    Game.appid.in_([i.data(Qt.UserRole) for i in items])).all()
            }
            if act == act_appid:
                text = "\n".join(i.data(Qt.UserRole) for i in items)
            elif act == act_name:
                text = "\n".join(games[i.data(Qt.UserRole)].name
                                 for i in items if i.data(Qt.UserRole) in games)
            elif act == act_tags:
                lines = []
                for i in items:
                    g = games.get(i.data(Qt.UserRole))
                    if not g:
                        continue
                    try:
                        tags = "、".join(json.loads(g.steam_tags or "[]"))
                    except ValueError:
                        tags = ""
                    lines.append(f"{g.name}: {tags}")
                text = "\n".join(lines)
            elif act == act_cat:
                scheme = get_active_scheme()
                lines = []
                for i in items:
                    g = games.get(i.data(Qt.UserRole))
                    if not g:
                        continue
                    row = (
                        session.query(Category.name, Category.path)
                        .join(GameClassification, GameClassification.category_id == Category.id)
                        .filter(
                            GameClassification.game_appid == g.appid,
                            GameClassification.scheme_id == scheme.id,
                            GameClassification.dimension == "TYPE",
                            GameClassification.del_flag.is_(False),
                        ).first()
                    )
                    lines.append(f"{g.name}: {row[0] if row else '未分类'}")
                text = "\n".join(lines)
            else:
                return
        finally:
            session.close()
        QGuiApplication.clipboard().setText(text)

    def _on_grid_size(self, mode: str) -> None:
        """封面尺寸切换（小/中/大；大 = 小黑盒风格 600×900 全尺寸展示）。"""
        if mode not in self._grid_sizes:
            return
        self._grid_mode = mode
        icon_size, grid_size = self._grid_sizes[mode]
        self.game_grid.setIconSize(icon_size)
        self.game_grid.setGridSize(grid_size)
        self.reload_games()  # 重新加载图标（按新尺寸缩放）

    def _on_style_clicked(self, item) -> None:
        """点击风格标签：多选模式 = toggle 目标风格；否则只按风格筛选（互斥）。

        R18 系列标签（R18/无需补丁/需要补丁）加载全量游戏（含 demo，
        [Demo] 标记）——哩米需求 2026-08-13：收藏夹 1demo 的 15 款要能看见。
        """
        style_name = item.data(Qt.UserRole) or item.text()  # 缩进标签取原名
        if self._multi_select:
            self._toggle_target_style(item, style_name)
            return
        self._active_style = style_name
        self._active_category_id = None  # 互斥：清空分类树/级联选择（含高亮）
        self._active_studio = None  # 厂商视图互斥（哩米 2026-08-14）
        self.category_tree.clearSelection()
        self.cascade_tree.clear_selection()
        self.studio_list.clearSelection()  # 厂商高亮同步清空
        if style_name in ("R18", "无需补丁", "需要补丁"):
            # R18 系列：加载全量（含 demo，无视「仅游戏」过滤）
            scheme = get_active_scheme()
            if scheme:
                from client.models import Game, GameClassification, Tag
                from client.storage.database import get_session

                session = get_session()
                try:
                    tag = session.query(Tag).filter(
                        Tag.scheme_id == scheme.id, Tag.dimension == "STYLE",
                        Tag.name == style_name, Tag.del_flag.is_(False)).first()
                    if tag:
                        styled = {
                            gc.game_appid for gc in session.query(GameClassification).filter(
                                GameClassification.scheme_id == scheme.id,
                                GameClassification.dimension == "STYLE",
                                GameClassification.tag_id == tag.id,
                                GameClassification.del_flag.is_(False)).all()
                        }
                        games = session.query(Game).filter(
                            Game.appid.in_(styled), Game.del_flag.is_(False)
                        ).order_by(Game.name).all()
                        self._load_game_items(games)
                        self._r18_list_loaded = True
                finally:
                    session.close()
        else:
            # 普通风格：列表被 R18 系列重建过 → 恢复全量再过滤
            if self._r18_list_loaded:
                self.reload_games()
                self._r18_list_loaded = False
        self._apply_filters()

    def _on_show_all(self) -> None:
        """清除分类/风格筛选，显示全部游戏（恢复「仅游戏」过滤状态）。"""
        self._active_category_id = None
        self._active_style = None
        self._active_studio = None
        self._r18_list_loaded = False
        self.category_tree.clearSelection()
        self.cascade_tree.clear_selection()
        self.style_list.clearSelection()
        self.studio_list.clearSelection()  # 四视图高亮同步清空（哩米 2026-08-14）
        self.reload_games()

    def _on_batch_classify(self) -> None:
        """批量应用分类：选中多款游戏 → 当前下拉分类（哩米需求 2026-08-12）。"""
        appids = self._selected_appids()
        if not appids:
            QMessageBox.information(self, "未选择游戏", "请先选择游戏（可全选）。")
            return
        category_id = self.classify_combo.currentData()
        if not category_id:
            QMessageBox.information(self, "未选择分类", "请先在右侧选择目标分类。")
            return
        scheme = get_active_scheme()
        if not scheme:
            return
        from client.core.classifier import apply_classification

        for appid in appids:
            apply_classification(appid, scheme.id, category_id, source="MANUAL")
        QMessageBox.information(
            self, "批量应用完成",
            f"已为 {len(appids)} 款游戏应用分类：{self.classify_combo.currentText()}")
        self.reload_games()
        self._refresh_tree()

    def _on_full_writeback(self) -> None:
        """全量写回（哩米审查 2026-08-14：GUI 闭环）——与命令行 write 同语义：
        TYPE 分类全量同步（增删双向），风格收藏夹白名单保留。"""
        if not self._ensure_safety_notice():
            return
        from client.core.scanner import find_steamid32
        from client.core.vdf_ops import backup_store, is_steam_running, write_categories
        from client.utils.steam_path import detect_steam_path

        steam_path = detect_steam_path()
        steamid32 = find_steamid32(steam_path) if steam_path else None
        if not steam_path or not steamid32:
            QMessageBox.warning(self, "Steam 未找到", "未检测到 Steam 账号。")
            return
        if is_steam_running():
            QMessageBox.warning(self, "Steam 运行中", "写回收藏夹前请先关闭 Steam。")
            return
        # 与「应用到 Steam」共用同一套目标构建逻辑（哩米 2026-09-27 修复分叉）
        target = self._build_target_from_rules()
        if not target:
            QMessageBox.information(self, "无目标", "当前没有可写回的分类目标。")
            return
        ret = QMessageBox.question(
            self, "确认全量写回",
            "将按当前分类全量同步「库集_」收藏夹：\n"
            "游戏移出分类会从对应收藏夹移除；\n"
            "目标外的旧「库集_」收藏夹会被软删除（风格收藏夹保留）。\n"
            "写回前自动备份；如出现异常，可在「设置 → 备份与恢复」中恢复最近备份。\n\n确定继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        backup_store(steam_path, steamid32)
        result = write_categories(steam_path, steamid32, target)
        deleted_txt = f"，软删除 {result['deleted']} 个旧夹" if result["deleted"] else ""
        QMessageBox.information(
            self, "全量写回完成",
            f"已同步 {result['written']} 个收藏夹{deleted_txt}\n"
            f"（写后验证 {'通过 ✓' if result['verified'] else '失败 ✗'}）\n\n请打开 Steam 确认。")

    def _on_offline_meta(self) -> None:
        """离线补全元数据（哩米 2026-08-14）：appinfo.vdf 解析，后台线程不卡 UI。"""
        from PySide6.QtCore import QThread, Signal

        class _Worker(QThread):
            done = Signal(dict)
            failed = Signal(str)

            def run(self) -> None:
                try:
                    from client.core.appinfo_meta import enrich_from_appinfo

                    self.done.emit(enrich_from_appinfo())
                except Exception as e:  # 后台线程异常回传
                    self.failed.emit(str(e))

        self.offline_meta_btn.setEnabled(False)
        self.statusBar().showMessage("正在解析 appinfo.vdf（离线，无需网络/API key）…")
        worker = _Worker(self)

        def on_done(stats: dict) -> None:
            self.offline_meta_btn.setEnabled(True)
            self.statusBar().showMessage(
                f"离线补全完成：命中 {stats['hit']} 款，补名/补商 {stats['updated']} 款，"
                f"新增 {stats['inserted']} 款，类型覆盖 {stats['type_overrides']} 款", 6000)
            QMessageBox.information(
                self, "离线补全完成",
                f"appinfo.vdf 命中 {stats['hit']} 款（离线，无需 API key）\n"
                f"补名/补开发商 {stats['updated']} 款\n新增 {stats['inserted']} 款\n"
                f"应用类型覆盖（Tool/Beta 等）{stats['type_overrides']} 款\n\n"
                f"重新扫描游戏库后生效。")

        def on_failed(msg: str) -> None:
            self.offline_meta_btn.setEnabled(True)
            QMessageBox.warning(self, "离线补全失败", f"{msg}\n\n请确认 Steam 已启动过（appinfo.vdf 存在）。")

        worker.done.connect(on_done)
        worker.failed.connect(on_failed)
        worker.start()
        self._offline_worker = worker  # 防 GC

    def _on_delete_kuji(self) -> None:
        """批量删除所有「库集_」前缀收藏夹（哩米 2026-08-14，用户自有不动）。"""
        from client.core.scanner import find_steamid32
        from client.core.vdf_ops import KUJI_PREFIX, backup_store, delete_kuji_collections, is_steam_running, read_store, get_store_path
        from client.utils.steam_path import detect_steam_path

        steam_path = detect_steam_path()
        steamid32 = find_steamid32(steam_path) if steam_path else None
        if not steam_path or not steamid32:
            QMessageBox.warning(self, "Steam 未找到", "未检测到 Steam 账号。")
            return
        # 统计当前库集收藏夹数量
        store = read_store(get_store_path(steam_path, steamid32))
        count = 0
        for key, meta in store:
            if not key.startswith("user-collections") or not isinstance(meta, dict):
                continue
            if meta.get("is_deleted") or "value" not in meta:
                continue
            try:
                if json.loads(meta["value"]).get("name", "").startswith(KUJI_PREFIX):
                    count += 1
            except ValueError:
                continue
        if count == 0:
            QMessageBox.information(self, "无需删除", "当前没有「库集_」收藏夹。")
            return
        if is_steam_running():
            QMessageBox.warning(self, "Steam 运行中", "删除收藏夹前请先关闭 Steam。")
            return
        ret = QMessageBox.question(
            self, "确认删除",
            f"将删除 {count} 个「库集_」收藏夹（如 库集_JRPG、库集_FPS）。\n"
            "只删除库集管理的收藏夹，你自己的收藏夹（收藏夹/1demo/已隐藏 等）不受影响。\n"
            "删除前会自动备份，可回滚。\n\n确定继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        backup_store(steam_path, steamid32)
        result = delete_kuji_collections(steam_path, steamid32)
        QMessageBox.information(
            self, "删除完成",
            f"已删除 {result['deleted']} 个「库集_」收藏夹\n"
            f"（写后验证 {'通过 ✓' if result['verified'] else '失败 ✗'}）\n\n"
            "请打开 Steam 确认。备份可回滚（writeback rollback）。")

    def _on_cascade_category(self, category_id: str) -> None:
        """级联视图点击分类 → 筛选游戏网格（三视图互斥，哩米需求）。"""
        if self._multi_select:
            # 哩米 2026-08-14：级联子类不可见易误判，多选模式不支持级联
            QMessageBox.information(
                self, "不支持级联",
                "多选模式不支持在级联中使用，\n请到「分类树」或「厂商」视图中选择目标分类。")
            return
        self._filter_games_by_category(category_id)
        self.category_tree.clearSelection()  # 级联点击清空树形高亮

    def _apply_filters(self) -> None:
        """按当前筛选状态过滤游戏网格（三视图互斥，哩米 2026-08-13）。

        分类树/级联：显示该分类游戏，但风格标签游戏不入（只在风格区查看）；
        风格标签：显示该风格游戏；三者互斥。
        """
        scheme = get_active_scheme()
        if not scheme:
            return
        from client.models import Category, GameClassification, Tag
        from client.storage.database import get_session

        session = get_session()
        try:
            studio_appids: set[str] | None = None
            if self._active_studio:
                from client.models import Game

                studio_appids = {
                    g.appid for g in session.query(Game).filter(
                        Game.del_flag.is_(False),
                        Game.publisher.contains(self._active_studio)).all()
                    if _publisher_matches(g.publisher, self._active_studio)
                }
            classified: set[str] | None = None
            if self._active_category_id:
                cats = {self._active_category_id}
                all_cats = session.query(Category).filter(
                    Category.scheme_id == scheme.id,
                    Category.del_flag.is_(False)).all()
                changed = True
                while changed:
                    changed = False
                    for c in all_cats:
                        if c.parent_id in cats and c.id not in cats:
                            cats.add(c.id)
                            changed = True
                classified = {
                    gc.game_appid for gc in session.query(GameClassification).filter(
                        GameClassification.scheme_id == scheme.id,
                        GameClassification.dimension == "TYPE",
                        GameClassification.category_id.in_(cats),
                        GameClassification.del_flag.is_(False)).all()
                }
            styled: set[str] | None = None
            if self._active_style:
                tag = session.query(Tag).filter(
                    Tag.scheme_id == scheme.id, Tag.dimension == "STYLE",
                    Tag.name == self._active_style, Tag.del_flag.is_(False)).first()
                if tag:
                    styled = {
                        gc.game_appid for gc in session.query(GameClassification).filter(
                            GameClassification.scheme_id == scheme.id,
                            GameClassification.dimension == "STYLE",
                            GameClassification.tag_id == tag.id,
                            GameClassification.del_flag.is_(False)).all()
                    }
            # 风格标签游戏集合（分类树/级联排除用）
            style_styled: set[str] = set()
            if classified is not None:
                style_tags = [t.id for t in session.query(Tag).filter(
                    Tag.scheme_id == scheme.id, Tag.dimension == "STYLE",
                    Tag.del_flag.is_(False)).all()]
                if style_tags:
                    style_styled = {
                        gc.game_appid for gc in session.query(GameClassification).filter(
                            GameClassification.scheme_id == scheme.id,
                            GameClassification.dimension == "STYLE",
                            GameClassification.tag_id.in_(style_tags),
                            GameClassification.del_flag.is_(False)).all()
                    }
        finally:
            session.close()

        for i in range(self.game_grid.count()):
            item = self.game_grid.item(i)
            appid = item.data(Qt.UserRole)
            visible = True
            if classified is not None:
                visible = appid in classified and appid not in style_styled
            if styled is not None and appid not in styled:
                visible = False
            if studio_appids is not None and appid not in studio_appids:
                visible = False
            item.setHidden(not visible)

    def _filter_games_by_category(self, category_id: str) -> None:
        """分类树/级联点击：只按分类筛选（清空风格——三视图互斥）。

        哩米 2026-08-13：树形/风格/级联 三者互斥，选中高亮同步清空；
        R18 系列风格重建过列表 → 先恢复全量（否则分类游戏不在列表会空白）。
        """
        if self._r18_list_loaded:
            self.reload_games()
            self._r18_list_loaded = False
        self._active_category_id = category_id
        self._active_style = None
        self._active_studio = None
        self.style_list.clearSelection()
        self.cascade_tree.clear_selection()
        self.studio_list.clearSelection()  # 厂商高亮同步清空（哩米 2026-08-14）
        self._apply_filters()
