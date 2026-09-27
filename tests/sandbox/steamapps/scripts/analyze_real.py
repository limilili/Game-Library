"""POC-1 步骤：真实 Steam 库只读对照分析（哩米实机）。

只读解析本机真实 Steam 库（libraryfolders.vdf + appmanifest_*.acf），
与文件系统枚举对照，输出一致性统计。不写入任何真实文件。

用法：python analyze_real.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vdf_parser import (  # noqa: E402
    is_fully_installed,
    list_appmanifests,
    parse_appmanifest,
    parse_library_folders,
)

STEAM_PATH = Path(r"C:\Program Files (x86)\Steam")


def main() -> None:
    print(f"=== 真实库对照分析：{STEAM_PATH} ===")
    libs = parse_library_folders(STEAM_PATH)
    print(f"库数量: {len(libs)}")
    total_installed = 0
    total_acf = 0
    for lib in libs:
        acfs = list_appmanifests(Path(lib["path"]))
        total_acf += len(acfs)
        installed = [
            m for m in (parse_appmanifest(a) for a in acfs)
            if m.get("appid") and is_fully_installed(m)
        ]
        total_installed += len(installed)
        print(f"  库[{lib['index']}] {lib['path']}: {len(acfs)} 个 acf，"
              f"完全安装 {len(installed)} 个")
        for m in installed[:3]:
            print(f"    例: {m['appid']} | {m['name']} | {m.get('installdir')}")

    # 文件系统对照：按文件名枚举 appid，与解析结果集合一致
    fs_ids = set()
    for lib in libs:
        fs_ids |= {
            p.stem.removeprefix("appmanifest_")
            for p in list_appmanifests(Path(lib["path"]))
        }
    parsed_ids = set()
    for lib in libs:
        for acf in list_appmanifests(Path(lib["path"])):
            m = parse_appmanifest(acf)
            if m.get("appid"):
                parsed_ids.add(m["appid"])
    print(f"\nacf 总数: {total_acf} | 解析 appid 数: {len(parsed_ids)} | 文件系统枚举: {len(fs_ids)}")
    print(f"appid 集合一致: {fs_ids == parsed_ids}")
    missing = fs_ids - parsed_ids
    extra = parsed_ids - fs_ids
    if missing:
        print("  文件系统有但解析缺失:", sorted(missing))
    if extra:
        print("  解析有但文件系统缺失:", sorted(extra))
    print(f"完全安装总数: {total_installed}（排除 StateFlags≠4 的未完全安装/更新中）")


if __name__ == "__main__":
    main()
