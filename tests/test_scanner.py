"""M1 扫描模块测试：解析/枚举/来源判定（构造样例）。

真实对照（哩米实机 2026-08-12）：总数 1181 = 自有 122 + 家庭共享 1059，已安装 32，
与 librarycache 实测一致；名称补全 store appdetails（GetAppList 已停用）。

运行：pytest tests/test_scanner.py -v
"""
import json
from pathlib import Path

import pytest

from client.core.library_cache import get_local_library_appids
from client.core.scanner import _scan_installed, scan_steam_library, steamid32_to_steamid64
from client.utils.vdf_parser import (
    dump,
    is_fully_installed,
    parse_appmanifest,
    parse_library_folders,
    parse_vdf_text,
)


# ── 构造样例 ─────────────────────────────────────────────────────


def _manifest(appid: int, name: str, state_flags: int, last_owner: str = "") -> str:
    owner = f'\t"LastOwner"\t\t"{last_owner}"\n' if last_owner else ""
    return (
        '"AppState"\n{\n'
        f'\t"appid"\t\t"{appid}"\n'
        f'\t"name"\t\t"{name}"\n'
        f'\t"StateFlags"\t\t"{state_flags}"\n'
        f'\t"installdir"\t\t"{name}"\n'
        f'{owner}}}\n'
    )


def _libraryfolders(steam: Path, dlib: Path) -> str:
    s = steam.as_posix().replace("/", "\\\\")
    d = dlib.as_posix().replace("/", "\\\\")
    return (
        '"libraryfolders"\n{\n'
        '\t"0"\n\t{\n'
        f'\t\t"path"\t\t"{s}"\n'
        '\t\t"apps"\n\t\t{\n\t\t\t"730"\t\t"1"\n\t\t}\n\t}\n'
        '\t"1"\n\t{\n'
        f'\t\t"path"\t\t"{d}"\n'
        '\t\t"apps"\n\t\t{\n\t\t\t"1245620"\t\t"1"\n\t\t}\n\t}\n'
        "}\n"
    )


@pytest.fixture()
def fake_steam(tmp_path: Path) -> tuple[Path, Path]:
    """构造：主库（730 已装 + 570 未装）+ 第二库（1245620 已装）。"""
    steam = tmp_path / "Steam"
    dlib = tmp_path / "SteamLibrary"
    (steam / "steamapps").mkdir(parents=True)
    (dlib / "steamapps").mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        _libraryfolders(steam, dlib), encoding="utf-8"
    )
    (steam / "steamapps" / "appmanifest_730.acf").write_text(
        _manifest(730, "Counter-Strike 2", 4, "76561197960278073"), encoding="utf-8"
    )  # LastOwner = steamid32 12345 的 64 位
    (steam / "steamapps" / "appmanifest_570.acf").write_text(
        _manifest(570, "Dota 2", 2), encoding="utf-8"  # 未完全安装 → 过滤
    )
    (dlib / "steamapps" / "appmanifest_1245620.acf").write_text(
        _manifest(1245620, "ELDEN RING", 4, "76561197960278073"), encoding="utf-8"
    )
    return steam, dlib


# ── 解析 ─────────────────────────────────────────────────────────


def test_parse_libraryfolders_multilib(fake_steam: tuple[Path, Path]) -> None:
    """多库解析：路径还原 + 编号顺序。"""
    steam, dlib = fake_steam
    libs = parse_library_folders(steam)
    assert [l["index"] for l in libs] == [0, 1]
    assert libs[1]["path"] == str(dlib)


def test_stateflags_filter(fake_steam: tuple[Path, Path]) -> None:
    """StateFlags：4 完全安装，2 需更新过滤。"""
    steam, _ = fake_steam
    assert is_fully_installed(parse_appmanifest(steam / "steamapps" / "appmanifest_730.acf"))
    assert not is_fully_installed(parse_appmanifest(steam / "steamapps" / "appmanifest_570.acf"))


def test_roundtrip_dump_parse() -> None:
    """roundtrip：dump → parse 一致。"""
    data = {"AppState": {"appid": "730", "nested": {"a": "b"}}}
    assert parse_vdf_text(dump(data)) == data


# ── 扫描与来源判定 ───────────────────────────────────────────────


def test_scan_installed_multilib(fake_steam: tuple[Path, Path]) -> None:
    """本地扫描：跨库枚举已安装，未完全安装过滤，LastOwner 保留。"""
    steam, _ = fake_steam
    installed = _scan_installed(steam)
    assert set(installed) == {"730", "1245620"}
    assert installed["730"]["last_owner"] == "76561197960278073"


def test_steamid_conversion() -> None:
    assert steamid32_to_steamid64("12345") == "76561197960278073"


def test_library_cache_enumeration(tmp_path: Path) -> None:
    """librarycache 枚举：渲染 JSON + achievement_progress 并集（家庭共享源）。"""
    lc = tmp_path / "userdata" / "12345" / "config" / "librarycache"
    lc.mkdir(parents=True)
    (lc / "1000.json").write_text("[]", encoding="utf-8")
    (lc / "2000.json").write_text("[]", encoding="utf-8")
    (lc / "achievement_progress.json").write_text(
        json.dumps({"mapCache": [["3000", {}]]}), encoding="utf-8"
    )
    appids = get_local_library_appids(tmp_path, "12345")
    assert appids == ["1000", "2000", "3000"]


def test_scan_source_judgment(fake_steam: tuple[Path, Path], tmp_path: Path, monkeypatch) -> None:
    """来源判定：LastOwner==本人 → OWNED；librarycache 独有 → FAMILY_SHARED。"""
    steam, _ = fake_steam
    # 家庭共享源：librarycache 添加 9999（本人不拥有）
    lc = steam / "userdata" / "12345" / "config" / "librarycache"
    lc.mkdir(parents=True)
    (lc / "9999.json").write_text("[]", encoding="utf-8")
    # 关闭真实网络（store 元数据补全）
    monkeypatch.setattr(
        "client.api.steam_api.SteamClient.get_app_metadata",
        lambda self, ids, **kw: {a: {"name": f"Game {a}"} for a in ids},
    )

    games = scan_steam_library(steam, steamid32="12345", use_api=False)
    assert games["730"]["source"] == "OWNED"  # LastOwner == 本人
    assert games["1245620"]["source"] == "OWNED"
    assert games["9999"]["source"] == "FAMILY_SHARED"  # 仅 librarycache
