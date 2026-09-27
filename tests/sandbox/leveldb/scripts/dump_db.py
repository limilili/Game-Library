"""POC-2 通用 dump 工具：独立打开 LevelDB 只读遍历，打印全部 key/value。

用途：
- 步骤2：构造后核对（独立进程打开，排除写入方缓存干扰，保证"独立核对"）
- 步骤4：真实 sharedconfig.vdf 解析（归纳键结构）

用法：python dump_db.py <db_path> [--hex-values]
"""
import sys

import plyvel


def dump(path: str, hex_values: bool = False) -> None:
    db = plyvel.DB(path, create_if_missing=False)
    try:
        print(f"=== dump: {path} ===")
        n = 0
        for k, v in db:
            n += 1
            v_repr = v.hex() if hex_values else repr(v)
            print(f"{k.decode('utf-8', errors='replace')} -> {v_repr}")
        print(f"=== 共 {n} 个键 ===")
    finally:
        db.close()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    hex_flag = "--hex-values" in sys.argv
    if not args:
        sys.exit("用法: python dump_db.py <db_path> [--hex-values]")
    dump(args[0], hex_flag)
