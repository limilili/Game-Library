"""P2 自动分类 v2 组合决策映射测试（2026-08-13）。

覆盖：
- genres 组合决策（v1 主修复：动作+冒险不再进其他动作）
- 双语（cc=us 英文 genre）
- TAG_MAP 社区标签高置信（appinfo.vdf 落地后生效路径）
- 危险关键词防误判（魂/war 等）
- classify_one 返回 (slug, confidence)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from auto_classify import classify_one  # noqa: E402


def _c(genres: str, tags: str = "", name: str = "") -> str | None:
    return classify_one(genres, tags, name)[0]


class TestGenreCombo:
    """genres 组合决策（v2 核心）。"""

    def test_action_adventure_to_action_adventure(self):
        # v1 主修复项：94 款「动作+冒险」被塞进其他动作
        assert _c('["动作","冒险"]') == "action-adventure"

    def test_action_rpg_to_arpg(self):
        assert _c('["动作","角色扮演"]') == "arpg-dark"

    def test_action_adventure_rpg_to_arpg(self):
        assert _c('["动作","冒险","角色扮演"]') == "arpg-dark"

    def test_rpg_strategy_to_srpg(self):
        assert _c('["角色扮演","策略"]') == "srpg"

    def test_rpg_adventure_to_rpg_other(self):
        assert _c('["角色扮演","冒险"]') == "rpg-other"

    def test_pure_action_to_action_other(self):
        assert _c('["动作"]') == "action-other"

    def test_pure_adventure_to_adventure_other(self):
        assert _c('["冒险"]') == "adventure-other"

    def test_pure_rpg_to_rpg_other(self):
        assert _c('["角色扮演"]') == "rpg-other"

    def test_pure_strategy_to_strategy_other(self):
        assert _c('["策略"]') == "strategy-other"

    def test_action_plus_indie_ignores_indie(self):
        # 独立是开发属性，不参与决策
        assert _c('["动作","独立"]') == "action-other"

    def test_pure_indie_no_signal(self):
        assert _c('["独立"]') is None

    def test_indie_f2p_no_signal(self):
        assert _c('["独立","免费开玩"]') is None

    def test_empty_genres_no_signal(self):
        assert _c('[]') is None
        assert _c(None) is None

    def test_shooter_to_fps(self):
        assert _c('["射击"]') == "fps"

    def test_horror_to_horror_other(self):
        assert _c('["恐怖"]') == "horror-other"

    def test_mmo_to_mmorpg(self):
        assert _c('["大型多人在线"]') == "mmorpg"

    def test_simulation_to_sim_management(self):
        assert _c('["模拟"]') == "sim-management"

    def test_racing_sports_to_racing(self):
        assert _c('["竞速"]') == "racing-sports"
        assert _c('["体育"]') == "racing-sports"

    def test_casual_and_puzzle(self):
        assert _c('["休闲"]') == "casual"
        assert _c('["益智"]') == "puzzle"


class TestBilingual:
    """cc=us 英文 genre。"""

    def test_english_combo(self):
        assert _c('["Action","RPG"]') == "arpg-dark"
        assert _c('["Action","Adventure"]') == "action-adventure"

    def test_english_single(self):
        assert _c('["Strategy"]') == "strategy-other"
        assert _c('["Racing"]') == "racing-sports"
        assert _c('["Horror"]') == "horror-other"


class TestTagMap:
    """TAG_MAP 社区标签高置信（appinfo.vdf 落地后生效路径）。"""

    def test_high_conf_confidence(self):
        slug, conf = classify_one('[]', '["FPS"]', '')
        assert slug == "fps" and conf == 0.9
        slug, conf = classify_one('["动作"]', '', '')
        assert slug == "action-other" and conf == 0.7

    def test_fps_tag(self):
        assert _c('[]', '["FPS"]') == "fps"

    def test_soulslike_tag(self):
        assert _c('[]', '["Souls-like"]') == "soulslike"

    def test_visual_novel_tag(self):
        assert _c('[]', '["Visual Novel"]') == "galgame"

    def test_tower_defense_tag(self):
        assert _c('[]', '["Tower Defense"]') == "tower-defense"

    def test_stealth_tag(self):
        assert _c('[]', '["Stealth"]') == "stealth"

    def test_deckbuilder_tag(self):
        assert _c('[]', '["Deckbuilding"]') == "card"

    def test_multi_tag_priority(self):
        """多标签优先级（2026-08-13 重排）：具体标签先于通用兜底——
        杀戮尖塔 tags=[Roguelike, Deckbuilding] 须命中 dbg 而非 rpg-other。"""
        assert _c('[]', '["Roguelike","Card Game","Deckbuilding","Strategy"]') == "card"
        assert _c('[]', '["Souls-like","Open World","Dark Fantasy","RPG"]') == "soulslike"
        assert _c('[]', '["Farming Sim","Pixel Graphics","Life Sim","RPG"]') == "sim-management"

    def test_sandbox_not_mapped(self):
        """sandbox 标签太泛（巫师3/GTA 也被标）——不映射，走 genres/其他信号。"""
        assert _c('["角色扮演"]', '["Open World","RPG","Sandbox"]', "巫师 3：狂猎") == "rpg-other"


class TestFalsePositiveGuard:
    """危险关键词防误判。"""

    def test_hun_not_in_souls(self):
        # 「魂」单字会误判《灵魂骇客》等 JRPG；只用 类魂/魂系/souls 系列
        assert _c('["动作"]', "", "灵魂骇客2") == "action-other"

    def test_war_not_in_warframe(self):
        # "war" 会误判 Warframe（TPS）；只用 战争策略 全词
        assert _c('[]', '["Warframe"]') is None

    def test_tag_match_is_lowercase_insensitive(self):
        assert _c('[]', '["fps"]') == "fps"
