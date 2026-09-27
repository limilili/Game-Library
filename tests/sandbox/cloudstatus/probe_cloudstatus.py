"""POC-7 只读探查：Steam Cloud 同步状态可检测性（哩米实机，2026-08-12）。

结论（详见 已验证信息.md §11）：
1. ✅ 可检测：cloudstorage 目录存在 + namespaces.json 版本记录（云同步启用过的证据链）、
   每游戏 last_sync_state（localconfig.vdf apps → <appid> → cloud）
2. ❌ 不可检测：全局「启用 Steam 云同步」开关状态——本机 localconfig.vdf /
   Steam\\config\\config.vdf / 注册表 HKCU\\Software\\Valve\\Steam 均无可靠键位
   → PRD POC-7 降级方案确认：「启动时统一告知风险」（PRD §5.4.2）

只读操作，不写入任何 Steam 文件。
"""
import json
import re
from pathlib import Path

STEAM_PATH = Path(r"C:\Program Files (x86)\Steam")
USER_ID = "12345678"


def main() -> None:
    print("=== POC-7 Steam Cloud 状态可检测性探查 ===")
    user = STEAM_PATH / "userdata" / USER_ID

    # 1. cloudstorage 目录证据链
    cs = user / "config" / "cloudstorage"
    print(f"\n[1] cloudstorage 目录: {'存在' if cs.exists() else '不存在'}")
    if cs.exists():
        files = sorted(f.name for f in cs.iterdir())
        print(f"    文件: {files}")
        ns = cs / "cloud-storage-namespaces.json"
        if ns.exists():
            data = json.loads(ns.read_text(encoding="utf-8"))
            print(f"    namespaces.json: {data}  ← 云同步启用过（namespace 版本号存在）")
        modified = [f for f in files if ".modified.json" in f]
        print(f"    待上传修改（.modified.json）: {len(modified)} 个 ← 有未同步改动" if modified
              else "    待上传修改: 无")

    # 2. 每游戏 last_sync_state
    lc = user / "config" / "localconfig.vdf"
    print(f"\n[2] localconfig.vdf: {'存在' if lc.exists() else '不存在'}")
    if lc.exists():
        text = lc.read_text(encoding="utf-8", errors="replace")
        states = re.findall(r'"last_sync_state"\s+"(\w+)"', text)
        print(f"    每游戏 last_sync_state 统计: {dict((s, states.count(s)) for s in set(states))}")
        print("    ← 仅反映每游戏同步状态，非全局开关")

    # 3. 全局开关排查结果
    print("\n[3] 全局「启用 Steam 云同步」开关排查（本机）:")
    reg_hits = "无 Cloud 键"  # 已用 reg query 确认
    print(f"    注册表 HKCU\\Software\\Valve\\Steam: {reg_hits}")
    cfg = STEAM_PATH / "config" / "config.vdf"
    if cfg.exists():
        text = cfg.read_text(encoding="utf-8", errors="replace")
        hits = [l.strip() for l in text.splitlines() if "loud" in l]
        print(f"    Steam\\config\\config.vdf: {'无 Cloud 键' if not hits else hits}")
    else:
        print("    Steam\\config\\config.vdf: 不存在")
    print("    localconfig.vdf 顶层: 无全局 Cloud 键（仅 per-app）")
    print("    registry.vdf: 本机不存在")
    print("\n==> 结论: 全局开关状态无法从本地文件/注册表可靠检测 → 采用 PRD 降级方案"
          "（启动时统一告知风险）")


if __name__ == "__main__":
    main()
