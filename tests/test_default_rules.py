"""官方默认规则测试（P2，2026-08-13）。

验证：
- seed 幂等（重复调用不重复插入）
- 规则 JSON 合法（conditions 可解析、目标 slug 存在于官方树、priority 合法）
- 开发商规则能真实命中（FromSoftware→soulslike 等，用 rule_matches 直接验证）
"""
import json

import pytest

from client.core.rule_engine import rule_matches
from client.core.scheme_service import (
    DEFAULT_RULES,
    get_active_scheme,
    load_official_scheme,
    seed_default_rules,
)
from client.models import ClassificationRule, Game
from client.storage.database import get_session

_RULE_JSON = json.loads(DEFAULT_RULES.read_text(encoding="utf-8"))


def _load_scheme():
    scheme = load_official_scheme()
    return scheme


def _rules_in_db() -> list[ClassificationRule]:
    session = get_session()
    try:
        scheme = get_active_scheme()
        return (
            session.query(ClassificationRule)
            .filter(
                ClassificationRule.scheme_id == scheme.id,
                ClassificationRule.source == "OFFICIAL",
                ClassificationRule.del_flag.is_(False),
            )
            .all()
        )
    finally:
        session.close()


def test_json_structure():
    """rules_default.json 结构合法。"""
    assert _RULE_JSON["schema_version"] == 1
    assert _RULE_JSON["source"] == "OFFICIAL"
    assert len(_RULE_JSON["rules"]) >= 10
    for r in _RULE_JSON["rules"]:
        assert r["name"] and 0 < r["priority"] <= 100
        assert r["target_category_slug"].startswith(
            ("soulslike", "jrpg", "galgame", "action", "dbg", "crpg",
             "wrpg", "war-strategy", "survival", "4x", "moba", "musou",
             "stealth", "metroidvania", "platformer", "mmorpg", "srpg",
             "fps", "tps", "stg", "sim", "racing", "casual", "puzzle",
             "tcg", "ccg", "lcg", "card", "horror", "sandbox", "rpg",
             "adventure", "strategy", "interactive", "point", "fighting",
             "rhythm", "moba", "coop", "multiplayer"))

    def walk(conds):
        for c in conds:
            if "conditions" in c:
                walk(c["conditions"])
            else:
                assert c["field"] in {"steam_tags", "developer", "publisher",
                                      "release_year", "review_score", "appid"}
                assert c["op"] in {"contains_any", "contains_all", "not_contains",
                                   "equals", "not_equals", "gt", "lt", "in"}
                assert c["value"]

    for r in _RULE_JSON["rules"]:
        walk(r["conditions"])


def test_seed_idempotent():
    scheme = _load_scheme()
    first = seed_default_rules(None, scheme.id) if False else None  # seed 已由 load 触发
    session = get_session()
    try:
        n1 = session.query(ClassificationRule).filter(
            ClassificationRule.scheme_id == scheme.id,
            ClassificationRule.source == "OFFICIAL",
            ClassificationRule.del_flag.is_(False)).count()
    finally:
        session.close()
    # 再 load 一次 → 不重复
    _load_scheme()
    session = get_session()
    try:
        n2 = session.query(ClassificationRule).filter(
            ClassificationRule.scheme_id == scheme.id,
            ClassificationRule.source == "OFFICIAL",
            ClassificationRule.del_flag.is_(False)).count()
    finally:
        session.close()
    assert n1 == n2 == len(_RULE_JSON["rules"])


def test_seeded_targets_exist_in_tree():
    """seed 后每条规则目标 slug 都对应官方树叶节点（seed 内部已校验，双保险）。"""
    _load_scheme()  # 测试库每测试清表，先加载方案+规则
    session = get_session()
    try:
        scheme = get_active_scheme()
        from client.models import Category

        slugs = {c.slug for c in session.query(Category).filter(
            Category.scheme_id == scheme.id, Category.del_flag.is_(False)).all()}
        rules = session.query(ClassificationRule).filter(
            ClassificationRule.scheme_id == scheme.id,
            ClassificationRule.source == "OFFICIAL").all()
        assert len(rules) == len(_RULE_JSON["rules"])
        for r in rules:
            cat = session.get(Category, r.target_category_id)
            assert cat is not None and cat.slug in slugs
    finally:
        session.close()


def _matches(rule: dict, dev: str | None, name: str = "") -> bool:
    return rule_matches(
        {"conditions": rule["conditions"]},
        {"appid": "1", "steam_tags": [], "developer": dev, "publisher": dev,
         "name": name},
    )


def test_developer_rules_match_real_values():
    """开发商规则对真实数据值命中（正式库实测值：FromSoftware, Inc. 等）。"""
    for rule in _RULE_JSON["rules"]:
        field = rule["conditions"][0]["field"]
        if field != "developer":
            continue
        keyword = rule["conditions"][0]["value"][0]
        assert _matches(rule, f"{keyword}, Inc.") or _matches(rule, keyword)


def test_fromsoftware_hits_soulslike():
    rule = next(r for r in _RULE_JSON["rules"] if r["target_category_slug"] == "soulslike")
    assert _matches(rule, "FromSoftware, Inc.")
    assert not _matches(rule, "Bandai Namco Entertainment")  # 无关开发商不命中
