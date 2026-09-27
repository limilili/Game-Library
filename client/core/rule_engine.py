"""规则引擎（M2.3）：PRD §5.3 规则条件 JSON，6 字段 8 操作符 + 嵌套 AND/OR/NOT。

条件格式（PRD §5.3）：
{"schema_version": 1, "operator": "AND", "conditions": [
  {"field": "steam_tags", "op": "contains_any", "value": ["Visual Novel"]},
  {"field": "release_year", "op": "gt", "value": 2020},
  {"operator": "OR", "conditions": [...]}   # 嵌套
]}
"""
import json
from typing import Any

from loguru import logger

# PRD 支持的字段与操作符
FIELDS = {"steam_tags", "developer", "publisher", "release_year", "review_score", "appid"}
OPERATORS = {"contains_any", "contains_all", "not_contains", "equals",
             "not_equals", "gt", "lt", "in"}


class RuleEngineError(ValueError):
    pass


def _resolve_field(field: str, game: dict[str, Any], metadata: dict[str, Any] | None) -> Any:
    """字段取值：game（ACF/扫描数据）优先，metadata（Steam Store 缓存）兜底。"""
    if field == "steam_tags":
        tags = game.get("steam_tags")
        if not tags and metadata:
            tags = metadata.get("store_tags") or metadata.get("genres") or []
        return tags if isinstance(tags, list) else []
    if field == "developer":
        return (game.get("developer") or metadata.get("developer")) if metadata else game.get("developer")
    if field == "publisher":
        return (game.get("publisher") or metadata.get("publisher")) if metadata else game.get("publisher")
    if field == "release_year":
        value = (game.get("release_year") or metadata.get("release_date")) if metadata else game.get("release_year")
        return int(value) if value else None
    if field == "review_score":
        return metadata.get("review_score") if metadata else game.get("review_score")
    if field == "appid":
        return str(game.get("appid", ""))
    raise RuleEngineError(f"未知字段: {field}")


def evaluate_condition(cond: dict[str, Any], game: dict[str, Any],
                       metadata: dict[str, Any] | None = None) -> bool:
    """单条件求值。"""
    # 嵌套块
    if "operator" in cond and "conditions" in cond:
        return evaluate_block(cond, game, metadata)

    field = cond.get("field", "")
    op = cond.get("op", "")
    value = cond.get("value")
    if field not in FIELDS:
        raise RuleEngineError(f"未知字段: {field}")
    if op not in OPERATORS:
        raise RuleEngineError(f"未知操作符: {op}")

    actual = _resolve_field(field, game, metadata)
    try:
        if op == "contains_any":
            return bool(actual) and any(item in actual for item in (value or []))
        if op == "contains_all":
            return bool(actual) and all(item in actual for item in (value or []))
        if op == "not_contains":
            if actual is None or actual == "" or actual == []:
                return False
            return not any(item in actual for item in (value or []))
        if op == "equals":
            return str(actual) == str(value)
        if op == "not_equals":
            return str(actual) != str(value)
        if op in ("gt", "lt"):
            if actual is None or value is None:
                return False
            return actual > value if op == "gt" else actual < value
        if op == "in":
            return str(actual) in {str(v) for v in (value or [])}
    except TypeError:
        return False
    raise RuleEngineError(f"未处理的操作符: {op}")


def evaluate_block(block: dict[str, Any], game: dict[str, Any],
                   metadata: dict[str, Any] | None = None) -> bool:
    """嵌套 AND/OR/NOT 求值。"""
    operator = block.get("operator", "AND").upper()
    conditions = block.get("conditions", [])
    results = [evaluate_condition(c, game, metadata) for c in conditions]
    if operator == "AND":
        return all(results)
    if operator == "OR":
        return any(results)
    if operator == "NOT":
        return not results[0] if results else True
    raise RuleEngineError(f"未知逻辑操作符: {operator}")


def rule_matches(rule: dict[str, Any], game: dict[str, Any],
                 metadata: dict[str, Any] | None = None) -> bool:
    """整条规则求值（conditions 顶层按 AND 处理；含 schema_version 校验）。"""
    if rule.get("schema_version", 1) != 1:
        raise RuleEngineError("不支持的规则 schema_version")
    block = {"operator": "AND", "conditions": rule.get("conditions", [])}
    return evaluate_block(block, game, metadata)


def count_rule_matches(rule: dict[str, Any], games: list[dict[str, Any]]) -> int:
    """实时预览命中数（M2.3 / M4.8）。"""
    return sum(1 for g in games if rule_matches(rule, g))
