"""POC-2 步骤4：真实文件分析。

1. 打开复制的真实 Local Storage LevelDB（沙盒 real_copy），dump 全部键形态
2. 解析 cloudstorage JSON 的 user-collections（收藏夹），作为真实分类数据基准

输出：UTF-8 落盘到 tests/sandbox/leveldb/real_analysis.txt（避免 GBK 控制台问题）
"""
import argparse
import json
from pathlib import Path

import plyvel

SANDBOX = Path(__file__).resolve().parent.parent
OUT = SANDBOX / "real_analysis.txt"
REAL_DB = SANDBOX / "data" / "real_copy"
# cloudstorage JSON 用沙盒副本（复制自真实 Steam userdata/12345678，哩米已核对与客户端一致），
# 分析脚本不直接读取真实 Steam 文件（CLAUDE.md 沙盒原则）
CLOUD_JSON = SANDBOX / "real_cloudstorage" / "cloud-storage-namespace-1.json"

lines: list[str] = []


def decode_value(v: bytes) -> str:
    """真实 Steam LevelDB 值解码：首字节为编码标志（0x00=UTF-16LE，0x01=UTF-8）。

    2026-08-10 从真实 Local Storage LevelDB 实测确认（与 Repressurizer 描述一致）。
    """
    if not v:
        return ""
    if v[0] == 0x00:
        return v[1:].decode("utf-16-le", errors="replace")
    if v[0] == 0x01:
        return v[1:].decode("utf-8", errors="replace")
    return repr(v)


def decode_key(k: bytes) -> str:
    """键解码：UTF-8；键内 0x00/0x01 分隔符（`_origin\\x00\\x01键名`）显示为 |。"""
    s = k.decode("utf-8", errors="replace")
    return s.replace("\x00", "|").replace("\x01", "|")


# 1. 真实 LevelDB 全部键
db = plyvel.DB(REAL_DB.as_posix(), create_if_missing=False)
lines.append("=== 真实 Local Storage LevelDB（沙盒 real_copy）全部键 ===")
for k, v in db:
    lines.append(f"{decode_key(k)[:120]} -> {decode_value(v)[:80]}")
db.close()

# 2. cloudstorage JSON 的 user-collections
lines.append("")
lines.append("=== cloudstorage JSON（12345678）中的 user-collections ===")
data = json.loads(CLOUD_JSON.read_text(encoding="utf-8"))
collections = [e for e in data if e[0].startswith("user-collections")]
active = 0
for entry in collections:
    meta = entry[1]
    if meta.get("is_deleted"):
        lines.append(f"键: {entry[0]} | 已删除（is_deleted=true）")
        continue
    if "value" not in meta:
        lines.append(f"键: {entry[0]} | 无 value 字段: {json.dumps(meta, ensure_ascii=False)[:120]}")
        continue
    inner = json.loads(meta["value"])
    added = inner.get("added", [])
    active += 1
    lines.append(
        f"键: {entry[0]} | 收藏夹名: {inner.get('name')} | added {len(added)} 个 appid"
    )
    lines.append(f"appid 列表: {added}")
lines.append(f"（有效收藏夹共 {active} 个）")

parser = argparse.ArgumentParser(description="真实 LevelDB/cloudstorage 分析")
parser.add_argument(
    "--encoding",
    default="gb18030",
    choices=["gb18030", "utf-8"],
    help="输出文件编码：gb18030（GBK 超集，中文工具直接可读，默认）/ utf-8（通用工具）",
)
args = parser.parse_args()

OUT.write_text("\n".join(lines), encoding=args.encoding)
print(f"已写入: {OUT}（编码 {args.encoding}）")
