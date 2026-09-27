"""appinfo.vdf 二进制解析器测试（P2，2026-08-13）。

- 构造样例：按 ValveResourceFormat/SteamAppInfo v41 格式手写最小 appinfo.vdf，
  精确断言解析结果（appid/common.name/type/嵌套/类型值）
- 真实文件抽验：本机 appinfo.vdf 的法环 1245620（存在时验证，缺失时跳过）
"""
import struct
from pathlib import Path

import pytest

from client.core.appinfo_parser import AppInfoError, extract_store_tags, parse_appinfo

APPINFO = Path(r"C:\Program Files (x86)\Steam\appcache\appinfo.vdf")


def _build_appinfo(entries: list[tuple[int, bytes, str, str]]) -> bytes:
    """构造 v41 appinfo.vdf。

    entries: [(appid, kv_data, common_name, common_type)]
    只支持 common.name/common.type 两个键的 KV1 构造（测试够用）。
    """
    # 字符串表：index 0=appinfo, 1=appid, 2=common, 3=name, 4=type,
    # 5+=值（每 app 两个：name 值 + type 值）
    strings = ["appinfo", "appid", "common", "name", "type"]

    def kv_for(entry: tuple[int, bytes, str, str]) -> bytes:
        """构造 appinfo KV1 Data。"""
        pass

    # 每个 app 的 KV1 Data 结构：
    # 00 00000000(appinfo) 02 01000000(appid=u32) 00 02000000(common)
    #    01 03000000(name) <name\x00> 01 04000000(type) <type\x00> 08
    out = bytearray()
    bodies = []
    for appid, _, name, atype in entries:
        for s in (name, atype):
            if s not in strings:
                strings.append(s)
        def idx(s): return strings.index(s)
        body = bytearray()
        body += b"\x00" + struct.pack("<i", idx("appinfo"))  # 根: ChildObject appinfo
        body += b"\x02" + struct.pack("<i", idx("appid")) + struct.pack("<I", appid)
        body += b"\x00" + struct.pack("<i", idx("common"))   # common 对象
        body += b"\x01" + struct.pack("<i", idx("name")) + name.encode("utf-8") + b"\x00"
        body += b"\x01" + struct.pack("<i", idx("type")) + atype.encode("utf-8") + b"\x00"
        body += b"\x08"                                     # common 结束
        body += b"\x08"                                     # 根结束
        bodies.append(bytes(body))

    # 字符串表
    strtab = struct.pack("<I", len(strings))
    for s in strings:
        strtab += s.encode("utf-8") + b"\x00"

    # 条目区
    entries_bin = bytearray()
    for (appid, _, _, _), body in zip(entries, bodies):
        size = 60 + len(body)  # 头尾部 60 字节 + Data
        entries_bin += struct.pack("<I", appid)
        entries_bin += struct.pack("<I", size)
        entries_bin += struct.pack("<I", 1)      # InfoState
        entries_bin += struct.pack("<I", 1700000000)  # LastUpdated
        entries_bin += struct.pack("<Q", 0)      # PICS token
        entries_bin += b"\x00" * 20              # SHA1
        entries_bin += struct.pack("<I", 1)      # ChangeNumber
        entries_bin += b"\x00" * 20              # BinaryDataHash (v40+)
        entries_bin += body
    entries_bin += struct.pack("<I", 0) + struct.pack("<I", 0)  # 结束标记

    header = struct.pack("<I", 0x07564429)       # magic v41
    header += struct.pack("<I", 1)               # universe
    header += struct.pack("<q", 16 + len(entries_bin))  # string table offset
    return bytes(header + entries_bin + strtab)


class TestConstructed:
    def test_single_app(self, tmp_path):
        raw = _build_appinfo([(1245620, b"", "ELDEN RING", "Game")])
        f = tmp_path / "appinfo.vdf"
        f.write_bytes(raw)
        parsed = parse_appinfo(f, {1245620})
        assert parsed[1245620]["appid"] == 1245620
        assert parsed[1245620]["common"]["name"] == "ELDEN RING"
        assert parsed[1245620]["common"]["type"] == "Game"

    def test_multi_app_and_filter(self, tmp_path):
        raw = _build_appinfo([
            (70, b"", "Half-Life", "Game"),
            (240, b"", "Counter-Strike: Source", "Game"),
            (1054830, b"", "Proton 4.2", "Tool"),
        ])
        f = tmp_path / "appinfo.vdf"
        f.write_bytes(raw)
        # 全量
        all_parsed = parse_appinfo(f)
        assert set(all_parsed) == {70, 240, 1054830}
        # 过滤
        only = parse_appinfo(f, {1054830})
        assert set(only) == {1054830}
        assert only[1054830]["common"]["type"] == "Tool"

    def test_chinese_name_utf8(self, tmp_path):
        raw = _build_appinfo([(1245620, b"", "艾尔登法环", "Game")])
        f = tmp_path / "appinfo.vdf"
        f.write_bytes(raw)
        parsed = parse_appinfo(f, {1245620})
        assert parsed[1245620]["common"]["name"] == "艾尔登法环"

    def test_bad_magic(self, tmp_path):
        f = tmp_path / "bad.vdf"
        f.write_bytes(b"\x00" * 64)
        with pytest.raises(AppInfoError):
            parse_appinfo(f)

    def test_no_store_tags_returns_empty(self, tmp_path):
        raw = _build_appinfo([(1245620, b"", "ELDEN RING", "Game")])
        f = tmp_path / "appinfo.vdf"
        f.write_bytes(raw)
        tags = extract_store_tags(f, {1245620})
        assert tags == {1245620: []}


@pytest.mark.skipif(not APPINFO.exists(), reason="本机无 appinfo.vdf")
class TestRealFile:
    def test_elden_ring(self):
        parsed = parse_appinfo(APPINFO, {1245620})
        tree = parsed[1245620]
        assert tree["appid"] == 1245620
        assert tree["common"]["name"] == "ELDEN RING"
        assert tree["common"]["type"] == "Game"
        assert tree["extended"]["developer"] == "FromSoftware, Inc."

    def test_full_scan_sane(self):
        parsed = parse_appinfo(APPINFO)
        # 全量解析条数在合理范围（本机实测 3650 条）
        assert len(parsed) > 1000
        # 每条都有 appid 字段
        for appid, tree in list(parsed.items())[:50]:
            assert tree.get("appid") == appid
