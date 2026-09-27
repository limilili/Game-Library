"""预置数据覆盖率统计（哩米 2026-09-27）。

作用：统计用户本机游戏库中，有多少能被内置预分类/预置元数据覆盖，
以及还有多少需要 AI 或手动补全。结果会随「反馈」诊断包一起导出，
方便开发者评估分享给测试群后的真实覆盖率。
"""
import sqlite3
from pathlib import Path

from loguru import logger

PREBUILT_DB = (
    Path(__file__).resolve().parent.parent.parent
    / "client" / "resources" / "metadata_prebuilt.db"
)


def build_coverage_report() -> dict:
    """返回覆盖率统计字典；数据库/预置库异常时对应项为 0。"""
    report: dict = {
        "total_games": 0,
        "prebuilt_classified": 0,
        "prebuilt_category_hit": 0,
        "category_hit_rate": 0.0,
        "prebuilt_metadata": 0,
        "prebuilt_metadata_hit": 0,
        "metadata_hit_rate": None,
        "prebuilt_styles": 0,
        "local_classified": 0,
        "local_classified_rate": 0.0,
        "uncovered": 0,
    }

    # 1) 本地游戏与已有分类
    local_appids: set[str] = set()
    local_classified: set[str] = set()
    try:
        from client.models import Game, GameClassification
        from client.storage.database import get_session

        session = get_session()
        try:
            local_appids = {
                str(g.appid) for g in session.query(Game).filter(
                    Game.del_flag.is_(False)).all()
            }
            local_classified = {
                str(gc.game_appid) for gc in session.query(GameClassification).filter(
                    GameClassification.dimension == "TYPE",
                    GameClassification.del_flag.is_(False)).all()
            }
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"覆盖率统计：读取本地库失败: {e}")

    # 2) 预置库
    pre_cls: set[str] = set()
    pre_meta: set[str] = set()
    n_styles = 0
    if PREBUILT_DB.exists():
        try:
            conn = sqlite3.connect(PREBUILT_DB)
            try:
                pre_cls = {
                    str(a) for (a,) in conn.execute(
                        "SELECT appid FROM pre_classified "
                        "WHERE category_slug IS NOT NULL AND category_slug != ''"
                    )
                }
                try:
                    pre_meta = {
                        str(a) for (a,) in conn.execute(
                            "SELECT appid FROM metadata_cache")
                    }
                except sqlite3.OperationalError:
                    pre_meta = set()  # 发布版可能已剥离 metadata_cache
                try:
                    n_styles = conn.execute("SELECT COUNT(*) FROM pre_styles").fetchone()[0]
                except sqlite3.OperationalError:
                    n_styles = 0
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"覆盖率统计：读取预置库失败: {e}")

    total = len(local_appids)
    hit_cls = local_appids & pre_cls
    hit_meta = local_appids & pre_meta
    classified = local_appids & local_classified
    covered = local_appids & (pre_cls | local_classified)

    report.update({
        "total_games": total,
        "prebuilt_classified": len(pre_cls),
        "prebuilt_category_hit": len(hit_cls),
        "category_hit_rate": (len(hit_cls) / total) if total else 0.0,
        "prebuilt_metadata": len(pre_meta),
        "prebuilt_metadata_hit": len(hit_meta),
        "metadata_hit_rate": (len(hit_meta) / total) if total else None,
        "prebuilt_styles": n_styles,
        "local_classified": len(classified),
        "local_classified_rate": (len(classified) / total) if total else 0.0,
        "uncovered": total - len(covered),
    })
    return report


def format_coverage_report() -> str:
    """格式化为可读文本。"""
    r = build_coverage_report()
    total = r["total_games"]
    lines = [
        "=== 库集预置数据覆盖率 ===",
        f"本机游戏总数: {total}",
        f"预置分类数据: {r['prebuilt_classified']} 条",
        f"预置分类命中: {r['prebuilt_category_hit']} / {total} "
        f"({r['category_hit_rate']:.1%})",
    ]
    if r["metadata_hit_rate"] is None:
        lines.append("预置元数据命中: 发布版不含 metadata_cache（按设计剥离）")
    else:
        lines.append(
            f"预置元数据命中: {r['prebuilt_metadata_hit']} / {total} "
            f"({r['metadata_hit_rate']:.1%})"
        )
    lines.extend([
        f"本地已分类: {r['local_classified']} / {total} "
        f"({r['local_classified_rate']:.1%})",
        f"预置与本地均未覆盖: {r['uncovered']} 款",
        f"预置风格标签: {r['prebuilt_styles']} 条",
        "说明：本机库覆盖率不代表其他用户，仅供反馈诊断参考。",
    ])
    return "\n".join(lines) + "\n"
