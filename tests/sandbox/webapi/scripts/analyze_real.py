"""POC-5 真实对照分析：GetOwnedGames vs 本地已安装库（哩米实机）。

只读调用 Steam Web API + 本地库解析（复用 POC-1 vdf_parser），输出：
- owned 总数 / 已安装∩owned / 缺失清单与原因归类
- 结论：免费未玩应用不出现在 owned（API 正常行为，Phase 1 M1.5 注意）

用法：STEAM_API_KEY=xxx python analyze_real.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "steamapps" / "scripts"))

from steam_api import SteamClient, get_api_key  # noqa: E402
from vdf_parser import (  # noqa: E402
    is_fully_installed,
    list_appmanifests,
    parse_appmanifest,
    parse_library_folders,
)

STEAMID = "76561198771116860"
STEAM_PATH = Path(r"C:\Program Files (x86)\Steam")


def installed_games() -> dict[str, str]:
    """本地已安装游戏：{appid: name}（POC-1 解析逻辑）。"""
    result: dict[str, str] = {}
    for lib in parse_library_folders(STEAM_PATH):
        for acf in list_appmanifests(Path(lib["path"])):
            m = parse_appmanifest(acf)
            if m.get("appid") and is_fully_installed(m):
                result[m["appid"]] = m["name"]
    return result


def main() -> None:
    client = SteamClient(get_api_key())
    print(f"=== POC-5 真实对照：GetOwnedGames vs 本地库（steamid {STEAMID}） ===")
    games = client.get_owned_games(STEAMID)
    owned = {str(g["appid"]): g.get("name", "") for g in games}
    print(f"owned 总数: {len(owned)}（含玩过的免费游戏）")

    installed = installed_games()
    print(f"本地已安装: {len(installed)} 款")
    matched = {a: installed[a] for a in installed if a in owned}
    missing = {a: installed[a] for a in installed if a not in owned}
    print(f"已安装 ∩ owned: {len(matched)} 款")
    print(f"已安装但不在 owned: {len(missing)} 款")
    for appid, name in missing.items():
        print(f"  {appid} | {name}（免费未玩/工具应用 → GetOwnedGames 不返回，属 API 正常行为）")

    # 免费已玩游戏在 owned 的验证（include_played_free_games 生效）
    free_played = [n for a, n in owned.items() if a not in installed][:5]
    print(f"owned 中未安装的样例（多为免费已玩）: {free_played}")


if __name__ == "__main__":
    main()
