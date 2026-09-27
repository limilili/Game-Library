"""审查 L1 回归测试：RATING 多标签约束行为（2026-08-13 修复）。

验证：
1. TYPE 维度：同一游戏同一方案只能一条（部分唯一索引）
2. RATING/STATUS/STYLE：同一游戏可挂多个不同标签
3. 同一标签不重复（去重约束）
"""
import pytest
from sqlalchemy.exc import IntegrityError

from client.models import Category, CategoryScheme, Game, GameClassification, Tag
from client.storage.database import get_session


@pytest.fixture()
def _setup() -> tuple[str, str, str, str]:
    """建方案/游戏/两个标签，返回 (scheme_id, appid, tag1_id, tag2_id)。"""
    session = get_session()
    scheme = CategoryScheme(name="L1测试方案", source="USER")
    game = Game(appid="730", name="CS2")
    session.add_all([scheme, game])
    session.flush()  # UUID 主键生成
    tag1 = Tag(scheme_id=scheme.id, name="神作", dimension="RATING")
    tag2 = Tag(scheme_id=scheme.id, name="耐玩", dimension="RATING")
    session.add_all([tag1, tag2])
    session.commit()
    ids = (scheme.id, game.appid, tag1.id, tag2.id)
    session.close()
    return ids


def test_multiple_rating_tags_allowed(_setup) -> None:
    """PRD §5.3：评价维度可挂多个标签（神作+耐玩）——L1 修复后必须成立。"""
    scheme_id, appid, tag1_id, tag2_id = _setup
    session = get_session()
    session.add(GameClassification(game_appid=appid, scheme_id=scheme_id,
                                   dimension="RATING", tag_id=tag1_id))
    session.add(GameClassification(game_appid=appid, scheme_id=scheme_id,
                                   dimension="RATING", tag_id=tag2_id))
    session.commit()  # 旧约束会在此抛 IntegrityError
    n = session.query(GameClassification).filter(
        GameClassification.game_appid == appid,
        GameClassification.dimension == "RATING").count()
    session.close()
    assert n == 2


def test_duplicate_tag_blocked(_setup) -> None:
    """同一游戏同一维度同一标签不可重复。"""
    scheme_id, appid, tag1_id, _ = _setup
    session = get_session()
    session.add(GameClassification(game_appid=appid, scheme_id=scheme_id,
                                   dimension="RATING", tag_id=tag1_id))
    session.commit()
    session.add(GameClassification(game_appid=appid, scheme_id=scheme_id,
                                   dimension="RATING", tag_id=tag1_id))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.close()


def test_type_dimension_still_unique(_setup) -> None:
    """TYPE 维度唯一性保持：同一游戏同一方案两条 TYPE 记录仍被拒。"""
    scheme_id, appid, _, _ = _setup
    session = get_session()
    scheme = session.get(CategoryScheme, scheme_id)
    cat = Category(scheme_id=scheme_id, name="FPS", slug="fps", path="FPS", is_leaf=True)
    session.add(cat)
    session.flush()
    session.add(GameClassification(game_appid=appid, scheme_id=scheme_id,
                                   dimension="TYPE", category_id=cat.id))
    session.commit()
    session.add(GameClassification(game_appid=appid, scheme_id=scheme_id,
                                   dimension="TYPE", category_id=cat.id))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.close()
