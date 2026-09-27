"""M2 分类核心测试：官方方案加载/方案管理/规则引擎/决策管道。

运行：pytest tests/test_m2_classify.py -v
"""
import pytest

from client.core.classifier import classify_game
from client.core.rule_engine import count_rule_matches, rule_matches
from client.core.scheme_service import (
    activate_scheme,
    create_user_scheme,
    get_active_scheme,
    get_scheme_styles,
    get_scheme_tree,
    list_schemes,
    load_official_scheme,
)
from client.models import Category, CategoryScheme


# ── 官方方案加载 ─────────────────────────────────────────────────


def test_load_official_scheme() -> None:
    """官方方案加载：树 62 节点 + 风格 3 标签 + 首次激活。"""
    scheme = load_official_scheme()
    assert scheme.source == "OFFICIAL"
    assert scheme.is_active is True
    tree = get_scheme_tree(scheme.id)
    assert len(tree) == 66  # 树 v0.7（真人互动子类加入文字冒险）
    roots = [c for c in tree if c.level == 0]
    assert len(roots) == 16  # 顶层 16 大类（文字冒险提级 v0.8）
    horror = next(c for c in tree if c.slug == "horror")
    horror_kids = [c for c in tree if c.parent_id == horror.id]
    assert len(horror_kids) == 7  # 6 子类 + 其他恐怖（兜底叶）
    styles = get_scheme_styles(scheme.id)
    assert [s.name for s in styles] == ["肉鸽", "R18", "已下架", "无需补丁", "需要补丁", "擦边"]


def test_load_official_scheme_idempotent() -> None:
    """幂等：重复加载不重建。"""
    first = load_official_scheme()
    second = load_official_scheme()
    assert first.id == second.id


# ── 方案管理 ─────────────────────────────────────────────────────


def test_scheme_switch_and_copy() -> None:
    """切换 + 复制起步：自己方案复制官方树，切回官方保留改动（US-M2-4/5）。"""
    official = load_official_scheme()
    user = create_user_scheme("我的方案", copy_from=official)
    assert user.source == "USER"
    assert user.copied_from_scheme_id == official.id
    user_tree = get_scheme_tree(user.id)
    assert len(user_tree) == 66  # 完整复制（树 v0.7）
    assert all(c.source == "USER_CREATED" for c in user_tree)

    activate_scheme(user.id)
    assert get_active_scheme().id == user.id
    activate_scheme(official.id)
    assert get_active_scheme().id == official.id
    assert len(list_schemes()) == 2


def test_create_blank_scheme() -> None:
    """空白方案：树为空可建。"""
    blank = create_user_scheme("空白")
    assert get_scheme_tree(blank.id) == []


# ── 规则引擎 ─────────────────────────────────────────────────────


def test_rule_conditions() -> None:
    game = {
        "appid": "1245620",
        "steam_tags": ["Souls-like", "Open World", "Dark Fantasy"],
        "release_year": 2022,
    }
    assert rule_matches({"conditions": [
        {"field": "steam_tags", "op": "contains_any", "value": ["Souls-like"]}], }, game)
    assert rule_matches({"conditions": [
        {"field": "steam_tags", "op": "contains_all", "value": ["Souls-like", "Open World"]},
        {"field": "release_year", "op": "gt", "value": 2020}], }, game)
    assert not rule_matches({"conditions": [
        {"field": "steam_tags", "op": "not_contains", "value": ["Horror"]},
        {"field": "release_year", "op": "lt", "value": 2020}], }, game)


def test_rule_nested_and_or() -> None:
    """嵌套 AND/OR。"""
    game = {"appid": "730", "steam_tags": ["FPS", "Multiplayer"]}
    rule = {"conditions": [
        {"operator": "OR", "conditions": [
            {"field": "steam_tags", "op": "contains_any", "value": ["FPS"]},
            {"field": "appid", "op": "equals", "value": "999"},
        ]},
        {"field": "steam_tags", "op": "contains_any", "value": ["Multiplayer"]},
    ]}
    assert rule_matches(rule, game)


def test_rule_count_matches() -> None:
    games = [
        {"appid": "1", "steam_tags": ["Visual Novel"]},
        {"appid": "2", "steam_tags": ["Action"]},
        {"appid": "3", "steam_tags": ["Visual Novel", "Horror"]},
    ]
    rule = {"conditions": [{"field": "steam_tags", "op": "contains_any",
                            "value": ["Visual Novel"]}]}
    assert count_rule_matches(rule, games) == 2


# ── 决策管道 ─────────────────────────────────────────────────────


def test_classify_pipeline() -> None:
    rules = [
        {"name": "低优先级", "priority": 100, "is_enabled": True,
         "target_category_slug": "low",
         "conditions": [{"field": "appid", "op": "equals", "value": "730"}]},
        {"name": "高优先级", "priority": 1, "is_enabled": True,
         "target_category_slug": "fps",
         "conditions": [{"field": "steam_tags", "op": "contains_any", "value": ["FPS"]}]},
    ]
    game = {"appid": "730", "steam_tags": ["FPS"]}
    assert classify_game(game, rules, scheme_id="s") == "fps"  # 高优先级先命中
    assert classify_game({"appid": "1", "steam_tags": []}, rules, "s") is None  # 未分类
    # 预标注优先（Q1 接口保留）
    assert classify_game(game, rules, "s", pre_classified={"730": "pre-db"}) == "pre-db"


def test_disabled_rule_skipped() -> None:
    rules = [{"name": "禁用", "priority": 1, "is_enabled": False,
              "target_category_slug": "x",
              "conditions": [{"field": "appid", "op": "equals", "value": "730"}]}]
    assert classify_game({"appid": "730"}, rules, "s") is None
