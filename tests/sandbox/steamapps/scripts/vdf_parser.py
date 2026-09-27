"""POC-1 VDF 解析器：libraryfolders.vdf + appmanifest_*.acf（Steam 本地库清单）。

2026-08-11 实机确认（本机真实文件）：
- libraryfolders.vdf：顶层 "libraryfolders"，库编号 "0"/"1"/... 为键，含 path/label/contentid/apps
- appmanifest_*.acf：顶层 "AppState"，UTF-8 无 BOM，字段 "key"\t\t"value"，
  嵌套区块（InstalledDepots/UserConfig）用 {}，值可含 \\" 转义
- StateFlags：4 = 完全安装（FullyInstalled）；2 = 需要更新等未完全状态

沙盒只读/构造操作：真实文件只读解析，不写入（CLAUDE.md 铁律）。
"""
from pathlib import Path
from typing import Any


class VDFParseError(ValueError):
    pass


# ── 解析 ─────────────────────────────────────────────────────────


def _tokenize(text: str) -> list[str]:
    """tokenize VDF：引号字符串（\\" 转义）、{、}。"""
    tokens: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c in "{}":
            tokens.append(c)
            i += 1
            continue
        if c == '"':
            buf: list[str] = []
            i += 1
            while i < n:
                c = text[i]
                if c == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    if nxt == '"':  # \" → "
                        buf.append('"')
                        i += 2
                        continue
                    if nxt == "\\":  # \\ → \（Windows 路径，实机确认）
                        buf.append("\\")
                        i += 2
                        continue
                if c == '"':
                    i += 1
                    break
                buf.append(c)
                i += 1
            else:
                raise VDFParseError("未闭合的字符串")
            tokens.append("".join(buf))
            continue
        raise VDFParseError(f"意外的字符 {c!r}（位置 {i}）")
    return tokens


def _parse_into(tokens: list[str], start: int, result: dict[str, Any]) -> int:
    """从 start 解析 key-value 对；遇到 } 结束当前区块。返回 } 之后的 token 下标。"""
    i = start
    n = len(tokens)
    while i < n:
        token = tokens[i]
        if token == "}":
            return i + 1
        key = token
        i += 1
        if i >= n:
            raise VDFParseError(f"键 {key!r} 缺少值")
        if tokens[i] == "{":
            block: dict[str, Any] = {}
            i = _parse_into(tokens, i + 1, block)
            result[key] = block
        else:
            result[key] = tokens[i]
            i += 1
    return i


def parse_vdf_text(text: str) -> dict[str, Any]:
    """解析 VDF 文本，返回 {key: value}（嵌套区块为 dict，保持出现顺序）。"""
    tokens = _tokenize(text)
    result: dict[str, Any] = {}
    _parse_into(tokens, 0, result)
    return result


def parse_vdf_file(filepath: str | Path) -> dict[str, Any]:
    return parse_vdf_text(Path(filepath).read_text(encoding="utf-8"))


# ── 序列化（roundtrip 验证用） ───────────────────────────────────


def dump(data: dict[str, Any], indent: int = 0) -> str:
    """序列化为 VDF 文本（roundtrip 验证：dump → parse 一致）。"""
    pad = "\t" * indent
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{pad}\"{key}\"")
            lines.append(f"{pad}{{")
            lines.append(dump(value, indent + 1))
            lines.append(f"{pad}}}")
        else:
            escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f"{pad}\"{key}\"\t\t\"{escaped}\"")
    return "\n".join(lines)


# ── Steam 库专用读取 ─────────────────────────────────────────────


def parse_library_folders(steam_path: str | Path) -> list[dict[str, Any]]:
    """解析 libraryfolders.vdf，返回 [{index, path, label, apps: {appid: size}}]。

    path 中 \\\\ 已由 tokenizer 还原为 \\（Windows 路径）。
    """
    vdf = parse_vdf_file(Path(steam_path) / "steamapps" / "libraryfolders.vdf")
    libraries: list[dict[str, Any]] = []
    # 数字键（库编号）按数值排，非数字键（contentstatsid 等）排最后
    folders = vdf.get("libraryfolders", {})
    for index in sorted(
        folders,
        key=lambda s: (1, s) if not s.isdigit() else (0, int(s)),
    ):
        entry = vdf["libraryfolders"][index]
        if not isinstance(entry, dict) or "path" not in entry:
            continue
        apps = entry.get("apps", {})
        libraries.append(
            {
                "index": int(index),
                "path": entry["path"],
                "label": entry.get("label", ""),
                "apps": apps if isinstance(apps, dict) else {},
            }
        )
    return libraries


def list_appmanifests(library_path: str | Path) -> list[Path]:
    """枚举库目录下 appmanifest_*.acf（按 appid 升序）。"""
    acf_dir = Path(library_path) / "steamapps"
    if not acf_dir.is_dir():
        return []
    return sorted(
        p for p in acf_dir.glob("appmanifest_*.acf")
        if p.stem.removeprefix("appmanifest_").isdigit()
    )


def parse_appmanifest(filepath: str | Path) -> dict[str, Any]:
    """解析单个 appmanifest.acf（顶层 AppState 区块）。"""
    vdf = parse_vdf_file(filepath)
    state = vdf.get("AppState", {})
    if not isinstance(state, dict):
        raise VDFParseError(f"无 AppState 区块: {filepath}")
    return state


def is_fully_installed(manifest: dict[str, Any]) -> bool:
    """完全安装判定：StateFlags 含 4（FullyInstalled 位）。"""
    try:
        flags = int(manifest.get("StateFlags", "0"))
    except (TypeError, ValueError):
        return False
    return bool(flags & 4)
