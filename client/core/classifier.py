"""分类决策管道（M2.4）：预标注（空跑）→ 规则引擎 → 未分类；结果写入激活方案。

PRD §5.3 决策管道（v1.3：写入当前激活方案）：
1. 内置预标注匹配（Q1 后置，接口保留空跑）
2. 规则引擎（当前方案的规则，按 priority 排序）
3. 标记「未分类」
"""
from typing import Any

from loguru import logger

from client.models import GameClassification
from client.storage.database import get_session


def classify_game(
    game: dict[str, Any],
    rules: list[dict[str, Any]],
    scheme_id: str,
    metadata: dict[str, Any] | None = None,
    pre_classified: dict[str, str] | None = None,
) -> str | None:
    """单游戏分类：返回命中的分类 slug（叶节点）或 None（未分类）。

    - pre_classified（Q1 预标注 {appid: category_slug}，当前为空跑）优先
    - 否则规则引擎按 priority 依次匹配（数值小优先）
    """
    # 第 1 步：内置预标注（Q1 后置：数据为空，接口保留）
    if pre_classified:
        hit = pre_classified.get(game.get("appid", ""))
        if hit:
            return hit

    # 第 2 步：规则引擎
    from client.core.rule_engine import rule_matches

    for rule in sorted(rules, key=lambda r: r.get("priority", 100)):
        if not rule.get("is_enabled", True):
            continue
        try:
            if rule_matches(rule, game, metadata):
                return rule.get("target_category_slug")
        except Exception as e:  # 单条规则异常不影响其他规则
            logger.warning(f"规则执行异常 [{rule.get('name', '?')}]: {e}")
            continue
    return None  # 第 3 步：未分类


def apply_classification(
    game_appid: str,
    scheme_id: str,
    category_id: str | None,
    source: str = "USER_RULE",
) -> None:
    """写入 TYPE 维度分类（同 appid+scheme+TYPE 唯一，upsert）。"""
    session = get_session()
    try:
        record = (
            session.query(GameClassification)
            .filter(
                GameClassification.game_appid == game_appid,
                GameClassification.scheme_id == scheme_id,
                GameClassification.dimension == "TYPE",
                GameClassification.del_flag.is_(False),
            )
            .first()
        )
        if record is None:
            record = GameClassification(
                game_appid=game_appid,
                scheme_id=scheme_id,
                dimension="TYPE",
                source=source,
            )
            session.add(record)
        record.category_id = category_id
        record.is_confirmed = source == "MANUAL"
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
