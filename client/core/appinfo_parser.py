"""appinfo.vdf 二进制 VDF 解析器（P2「appinfo.vdf 离线解析」落地，2026-08-13）。

appinfo.vdf 是 Valve 二进制容器（magic 0x07564429 = v41），随 Steam 启动自动更新
（appcache/appinfo.vdf），覆盖全量商店应用元数据。格式依据
ValveResourceFormat/SteamAppInfo + ValveKeyValue 源码实测破译：

- 文件头: magic(4) + universe(4) + string_table_offset(int64, v41+)
- 字符串表: 偏移处 uint32 count + count 个 null 结尾 UTF-8 字符串
- App 条目（appid==0 结束）:
    appid(4) + size(4, 从 size 字段后到 Data 末尾) + InfoState(4) + LastUpdated(4)
    + PICS token(8) + SHA1(20) + ChangeNumber(4) + BinaryDataHash(20, v40+)
    + Data（KV1 二进制 KeyValues）
- KV1 节点: <type(1)> <key: int32 字符串表索引> <value...>，dict 结束 type=0x08
  类型: 0=ChildObject 1=String(null 结尾) 2=Int32 3=Float32 4=Pointer(8)
       6=UInt64 7=Int64

用途：提取本机库游戏的 store_tags（社区标签）/developer/publisher，修正
metadata_prebuilt.db store_tags 字段（v1 误写为 genres 镜像）。
"""
import struct
from pathlib import Path
from typing import Any

# 条目头固定字段（InfoState+LastUpdated+Token+SHA1+ChangeNumber+BinaryDataHash）
_HEADER_TAIL = 4 + 4 + 8 + 20 + 4 + 20  # = 60


class AppInfoError(ValueError):
    pass


def _load_strings(data: bytes, strtab_off: int) -> list[str]:
    """读字符串表（v41+）：count + count 个 null 结尾 UTF-8 字符串。"""
    count = struct.unpack_from("<I", data, strtab_off)[0]
    pos = strtab_off + 4
    strings = []
    for _ in range(count):
        end = data.index(b"\x00", pos)
        strings.append(data[pos:end].decode("utf-8", "replace"))
        pos = end + 1
    return strings


def parse_appinfo(
    appinfo_path: str | Path,
    appids: set[int] | None = None,
    max_sections: int = 0,
) -> dict[int, dict[str, Any]]:
    """解析 appinfo.vdf，返回 {appid: KV 树}。

    appids 为空时解析全部；max_sections>0 限制条数（探测用）。
    """
    data = Path(appinfo_path).read_bytes()
    if len(data) < 16:
        raise AppInfoError("appinfo.vdf 过短")
    magic = struct.unpack_from("<I", data, 0)[0]
    version = magic & 0xFF
    if (magic >> 8) != 0x075644:
        raise AppInfoError(f"非 appinfo.vdf（magic={magic:#x}）")
    if version < 41:
        raise AppInfoError(f"v{version} 无字符串表，暂不支持")
    strtab_off = struct.unpack_from("<q", data, 8)[0]
    strings = _load_strings(data, strtab_off)

    def parse_node(body: bytes, off: int) -> tuple[int, dict]:
        """KV1 节点解析（<type><key idx><value> 循环至 0x08）。"""
        result: dict[str, Any] = {}
        n = len(body)
        while off < n:
            t = body[off]
            if t == 0x08:
                return off + 1, result
            off += 1
            if off + 4 > n:  # key 索引越界（尾部残缺）
                return off, result
            idx = struct.unpack_from("<i", body, off)[0]
            off += 4
            key = strings[idx] if 0 <= idx < len(strings) else f"<idx{idx}>"
            if t == 0x00:  # ChildObject
                off, child = parse_node(body, off)
                result[key] = child
            elif t == 0x01:  # String（null 结尾）
                end = body.find(b"\x00", off)
                if end < 0:
                    return off, result
                result[key] = body[off:end].decode("utf-8", "replace")
                off = end + 1
            elif t == 0x02:  # Int32
                if off + 4 > n:
                    return off, result
                result[key] = struct.unpack_from("<i", body, off)[0]
                off += 4
            elif t == 0x03:  # Float32
                if off + 4 > n:
                    return off, result
                result[key] = struct.unpack_from("<f", body, off)[0]
                off += 4
            elif t == 0x04:  # Pointer（8B）
                if off + 8 > n:
                    return off, result
                result[key] = struct.unpack_from("<Q", body, off)[0]
                off += 8
            elif t == 0x05:  # Color（4B）
                if off + 4 > n:
                    return off, result
                result[key] = struct.unpack_from("<I", body, off)[0]
                off += 4
            elif t == 0x06:  # UInt64
                if off + 8 > n:
                    return off, result
                result[key] = struct.unpack_from("<Q", body, off)[0]
                off += 8
            elif t == 0x07:  # Int64
                if off + 8 > n:
                    return off, result
                result[key] = struct.unpack_from("<q", body, off)[0]
                off += 8
            else:  # 未知类型：跳过不解析（容错）
                return off, result
        return off, result

    out: dict[int, dict[str, Any]] = {}
    pos = 16
    while pos + 8 <= len(data):
        appid = struct.unpack_from("<I", data, pos)[0]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        if appid == 0:
            break
        if 60 < size < 8_000_000 and (appids is None or appid in appids):
            body = data[pos + 8 + _HEADER_TAIL : pos + 8 + size]
            if body:
                # 根节点：body[0]=type(ChildObject)、body[1..4]=根 key 索引
                # （通常索引 0="appinfo"），嵌套内容从 body[5] 起
                _, tree = parse_node(body, 5)
                out[appid] = tree
        pos += 8 + size
        if max_sections and len(out) >= max_sections:
            break
    return out


def extract_store_tags(
    appinfo_path: str | Path, appids: set[int]
) -> dict[int, list[str]]:
    """提取指定 appid 的 store_tags 社区标签（appinfo 无则空列表）。"""
    parsed = parse_appinfo(appinfo_path, appids)
    result: dict[int, list[str]] = {}
    for appid, tree in parsed.items():
        st = tree.get("store_tags")
        if isinstance(st, list):
            result[appid] = [str(x) for x in st]
        elif isinstance(st, dict):  # 兼容嵌套结构
            result[appid] = [str(v) for v in st.values()]
        else:
            result[appid] = []
    return result
