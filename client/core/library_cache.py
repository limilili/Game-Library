"""家庭共享扫描（M1.8，v1.5 P0）：本地 librarycache 枚举，零 API、含家庭共享。

数据源（v0 决策 #21 定稿，2026-08-04；2026-08-12 哩米实机复核：
1181 款 = 自有 115 + 家庭共享 1066）：
1. <steam_path>/userdata/<steamid32>/config/librarycache/*.json
   （新版 Steam 客户端按游戏缓存的库页面详情；滚动库页面补全）
2. .../librarycache/achievement_progress.json（成就进度缓存，
   覆盖玩过/打开过的游戏；含隐藏游戏与 demo，可能含已移除游戏的历史残留）

⚠️ 已知差距：完整性依赖「滚库浏览」（客户端渲染过才进缓存），
未浏览过的家庭共享游戏可能缺失（PRD §10 限制表）。
"""
import json
from pathlib import Path

from loguru import logger

ACHIEVEMENT_PROGRESS_FILE = "achievement_progress.json"


def get_local_library_appids(steam_path: Path, steamid32: str) -> list[str]:
    """枚举本地全部可下载游戏 appid（自有 + 家庭共享 + demo + 隐藏）。"""
    cache_dir = Path(steam_path) / "userdata" / steamid32 / "config" / "librarycache"
    if not cache_dir.is_dir():
        logger.warning(f"librarycache 目录不存在: {cache_dir}")
        return []

    rendered = {
        p.stem for p in cache_dir.glob("*.json")
        if p.name != ACHIEVEMENT_PROGRESS_FILE
    }
    progressed: set[str] = set()
    progress_file = cache_dir / ACHIEVEMENT_PROGRESS_FILE
    if progress_file.is_file():
        try:
            data = json.loads(progress_file.read_text(encoding="utf-8", errors="replace"))
            mc = data.get("mapCache", [])
            if isinstance(mc, dict):
                progressed = {str(k) for k in mc}
            elif isinstance(mc, list):
                progressed = {
                    str(pair[0]) for pair in mc if isinstance(pair, (list, tuple))
                }
        except (ValueError, AttributeError) as e:
            logger.warning(f"achievement_progress.json 解析失败: {e}")

    appids = sorted(rendered | progressed)
    extra = sorted(progressed - rendered)
    logger.info(
        f"本地库列表: {len(appids)} 款（渲染缓存 {len(rendered)} + 成就进度补充 {len(extra)}）"
    )
    if extra:
        logger.info(f"仅来自成就进度（隐藏/demo/已移除）: {', '.join(extra[:10])}…")
    return appids
