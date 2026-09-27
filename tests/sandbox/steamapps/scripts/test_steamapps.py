r"""POC-1 pytest：VDF/ACF 解析验证（构造样例精确断言 + 真实库一致性对照）。

构造样例（tmp_path 动态生成，避免扫到真实盘符）：
- libraryfolders.vdf：2 个库（主库 tmp/Steam + 第二库 tmp/SteamLibrary）
- 5 个 appmanifest：含中文名/emoji/引号转义、嵌套区块、未完全安装（StateFlags=2）、未安装（StateFlags=0）

真实对照（analyze_real）：只读解析本机 Steam，与文件系统枚举一致。

运行：pytest tests/sandbox/steamapps/scripts/test_steamapps.py -v
"""
from pathlib import Path

import pytest

from vdf_parser import (
    VDFParseError,
    dump,
    is_fully_installed,
    list_appmanifests,
    parse_appmanifest,
    parse_library_folders,
    parse_vdf_text,
)

# ── 构造样例 ─────────────────────────────────────────────────────


def _manifest_text(appid: int, name: str, installdir: str, state_flags: int, extra: str = "") -> str:
    # 真实 ACF 中值内引号以 \" 转义（2026-08-11 实机格式）
    name = name.replace('"', '\\"')
    installdir = installdir.replace('"', '\\"')
    return (
        '"AppState"\n'
        "{\n"
        f'\t"appid"\t\t"{appid}"\n'
        '\t"Universe"\t\t"1"\n'
        f'\t"name"\t\t"{name}"\n'
        f'\t"StateFlags"\t\t"{state_flags}"\n'
        f'\t"installdir"\t\t"{installdir}"\n'
        f'\t"LastUpdated"\t\t"1783496877"\n'
        f"{extra}"
        "}\n"
    )


def _libraryfolders_text(steam_path: Path, dlib_path: Path) -> str:
    """动态生成 libraryfolders.vdf（库路径指向 tmp_path，防止扫到真实盘符）。"""
    s = steam_path.as_posix().replace("/", "\\\\")
    d = dlib_path.as_posix().replace("/", "\\\\")
    return (
        '"libraryfolders"\n'
        "{\n"
        '\t"contentstatsid"\t\t"3237365120000185686"\n'
        '\t"0"\n'
        "\t{\n"
        f'\t\t"path"\t\t"{s}"\n'
        '\t\t"label"\t\t""\n'
        '\t\t"apps"\n'
        "\t\t{\n"
        '\t\t\t"730"\t\t"1321932713"\n'
        '\t\t\t"570"\t\t"9502988948"\n'
        "\t\t}\n"
        "\t}\n"
        '\t"1"\n'
        "\t{\n"
        f'\t\t"path"\t\t"{d}"\n'
        '\t\t"label"\t\t"第二库"\n'
        '\t\t"apps"\n'
        "\t\t{\n"
        '\t\t\t"1245620"\t\t"826275581"\n'
        "\t\t}\n"
        "\t}\n"
        "}\n"
    )


# 主库（库 0）：完全安装 / 特殊字符 / 未完全安装 / 未安装
MAIN_MANIFESTS = {
    730: _manifest_text(730, "Counter-Strike 2", "Counter-Strike 2", 4),
    1091500: _manifest_text(
        1091500, "赛博朋克 2077 🔥\"扩展\"", "Cyberpunk 2077", 4,
        '\t"InstalledDepots"\n\t{\n\t\t"1091501"\n\t\t{\n'
        '\t\t\t"manifest"\t\t"5753583882400741046"\n'
        '\t\t\t"size"\t\t"25674515"\n'
        "\t\t}\n\t}\n",
    ),
    570: _manifest_text(570, "Dota 2", "dota 2 beta", 2),  # 需更新 → 过滤
    999999: _manifest_text(999999, "未安装游戏", "uninstalled", 0),  # 未安装 → 过滤
}
# 第二库（库 1）：完全安装 + 嵌套多区块
D_LIB_MANIFESTS = {
    1245620: _manifest_text(
        1245620, "ELDEN RING", "ELDEN RING", 4,
        '\t"UserConfig"\n\t{\n\t\t"language"\t\t"schinese"\n\t}\n'
        '\t"SharedDepots"\n\t{\n\t\t"228989"\t\t"25674515"\n\t}\n',
    ),
}


@pytest.fixture()
def constructed(tmp_path: Path) -> tuple[Path, Path]:
    """生成构造样例库：libraryfolders + 5 个 appmanifest（2 库）。返回 (主库, 第二库)。"""
    steam = tmp_path / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    dlib = tmp_path / "SteamLibrary"
    (dlib / "steamapps").mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        _libraryfolders_text(steam, dlib), encoding="utf-8"
    )
    for appid, text in MAIN_MANIFESTS.items():
        (steam / "steamapps" / f"appmanifest_{appid}.acf").write_text(text, encoding="utf-8")
    for appid, text in D_LIB_MANIFESTS.items():
        (dlib / "steamapps" / f"appmanifest_{appid}.acf").write_text(text, encoding="utf-8")
    return steam, dlib


# ── 解析器基础 ───────────────────────────────────────────────────


def test_parse_libraryfolders_paths(constructed: tuple[Path, Path]) -> None:
    """解析库列表：2 个库，路径还原、label 提取（Windows 反斜杠）。"""
    steam, dlib = constructed
    libs = parse_library_folders(steam)
    assert [l["index"] for l in libs] == [0, 1]
    assert libs[0]["path"] == str(steam)
    assert libs[1]["path"] == str(dlib)
    assert libs[1]["label"] == "第二库"


def test_libraryfolders_apps_map(constructed: tuple[Path, Path]) -> None:
    """库的 apps 映射：{appid: size}，仅含安装在该库的游戏。"""
    steam, _ = constructed
    libs = parse_library_folders(steam)
    assert libs[0]["apps"] == {"730": "1321932713", "570": "9502988948"}
    assert libs[1]["apps"] == {"1245620": "826275581"}


def test_manifest_special_chars(constructed: tuple[Path, Path]) -> None:
    """特殊字符名：中文/emoji/引号转义完整提取。"""
    steam, _ = constructed
    m = parse_appmanifest(steam / "steamapps" / "appmanifest_1091500.acf")
    assert m["name"] == '赛博朋克 2077 🔥"扩展"'
    assert m["installdir"] == "Cyberpunk 2077"


def test_manifest_nested_blocks_ignored(constructed: tuple[Path, Path]) -> None:
    """嵌套区块（InstalledDepots/UserConfig/SharedDepots）解析为 dict，不影响顶层字段。"""
    steam, _ = constructed
    m = parse_appmanifest(steam / "steamapps" / "appmanifest_1091500.acf")
    assert m["appid"] == "1091500"
    assert "InstalledDepots" in m
    assert m["InstalledDepots"]["1091501"]["manifest"] == "5753583882400741046"


def test_state_flags_filter(constructed: tuple[Path, Path]) -> None:
    """StateFlags 过滤：仅 4（FullyInstalled）计入已安装。"""
    steam, _ = constructed
    assert is_fully_installed(parse_appmanifest(steam / "steamapps" / "appmanifest_730.acf"))
    assert not is_fully_installed(parse_appmanifest(steam / "steamapps" / "appmanifest_570.acf"))
    assert not is_fully_installed(
        parse_appmanifest(steam / "steamapps" / "appmanifest_999999.acf")
    )


def test_scan_all_libraries(constructed: tuple[Path, Path]) -> None:
    """全库扫描：2 库共 5 个 acf，过滤后 3 个完全安装，appid 正确。"""
    steam, _ = constructed
    installed: dict[str, str] = {}
    for lib in parse_library_folders(steam):
        for acf in list_appmanifests(Path(lib["path"])):
            m = parse_appmanifest(acf)
            if m.get("appid") and is_fully_installed(m):
                installed[m["appid"]] = m["name"]
    assert installed == {
        "730": "Counter-Strike 2",
        "1091500": '赛博朋克 2077 🔥"扩展"',
        "1245620": "ELDEN RING",
    }


def test_roundtrip_dump_parse(constructed: tuple[Path, Path]) -> None:
    """roundtrip：dump → parse 与原 dict 一致（顺序保持，嵌套区块完整）。"""
    steam, dlib = constructed
    original = parse_appmanifest(dlib / "steamapps" / "appmanifest_1245620.acf")
    reloaded = parse_vdf_text(dump(original))
    assert reloaded == original
    assert list(reloaded.keys()) == list(original.keys())


def test_unterminated_string_raises() -> None:
    """未闭合字符串报 VDFParseError。"""
    with pytest.raises(VDFParseError):
        parse_vdf_text('"broken" "unclosed')
