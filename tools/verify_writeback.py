"""库集真实 Steam 写回最小验收工具（哩米 2026-09-27）。

没有第二台电脑时，用这个工具在本机做一次最小化真实写回：
只添加一个「库集_验收测试」收藏夹（只含 1 个游戏），验证 Steam 能读取后，
可用 --rollback 恢复最近备份。

安全设计：
- 默认只预览，不写入任何内容。
- 只有显式 --apply 才执行写入。
- 写入前自动备份，可用 --rollback 恢复。
- 写入前检查 Steam 是否运行。

用法：
  python tools/verify_writeback.py                    # 只预览
  python tools/verify_writeback.py --apply            # 执行最小写入
  python tools/verify_writeback.py --rollback         # 恢复最近一次备份
  python tools/verify_writeback.py --appid 730 --apply
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from loguru import logger  # noqa: E402


def _first_game_appid() -> str | None:
    from client.models import Game
    from client.storage.database import get_session

    session = get_session()
    try:
        g = session.query(Game).filter(Game.del_flag.is_(False)).order_by(Game.name).first()
        return g.appid if g else None
    finally:
        session.close()


def _latest_backup():
    from client.models import BackupRecord
    from client.storage.database import get_session

    session = get_session()
    try:
        return (
            session.query(BackupRecord)
            .order_by(BackupRecord.created_at_ts.desc())
            .first()
        )
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="库集写回最小验收工具")
    parser.add_argument("--appid", default="", help="用于测试的 AppID，默认取库里第一款游戏")
    parser.add_argument("--apply", action="store_true", help="执行写入（默认只预览）")
    parser.add_argument("--rollback", action="store_true", help="恢复最近一次备份")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认")
    args = parser.parse_args()

    from client.core import vdf_ops
    from client.core.existing_categories import read_existing_categories
    from client.core.scanner import find_steamid32
    from client.utils.steam_path import detect_steam_path

    steam_path = detect_steam_path()
    if not steam_path:
        print("[失败] 未检测到 Steam 路径")
        return 1
    steamid32 = find_steamid32(steam_path)
    if not steamid32:
        print("[失败] 未找到 Steam 账号（可先在软件设置里填 Steam ID）")
        return 1

    # ── 恢复模式 ──
    if args.rollback:
        record = _latest_backup()
        if record is None:
            print("[失败] 没有可恢复的备份记录")
            return 1
        if vdf_ops.is_steam_running():
            print("[失败] Steam 正在运行，请先完全关闭 Steam")
            return 1
        if not args.yes:
            ans = input(f"将恢复备份 {record.created_at_ts}（{record.notes or ''}），确认？[y/N] ")
            if ans.strip().lower() not in ("y", "yes"):
                print("已取消")
                return 0
        try:
            vdf_ops.rollback(record, vdf_ops.get_store_path(steam_path, steamid32))
        except Exception as e:
            print(f"[失败] 恢复失败: {e}")
            return 1
        print("[完成] 已恢复最近一次备份，请重新打开 Steam 查看")
        return 0

    # ── 预览/写入模式 ──
    appid = args.appid.strip() or _first_game_appid()
    if not appid:
        print("[失败] 数据库里没有游戏，请先在软件里扫描游戏库")
        return 1

    collection = vdf_ops.KUJI_PREFIX + "验收测试"
    # build_diff 预览格式：{appid: [收藏夹名]}；update_categories 写入格式：{收藏夹名: {appid}}
    target_view = {str(appid): [collection]}
    desired = {collection: {str(appid)}}
    current = read_existing_categories(steam_path, steamid32)
    diff = vdf_ops.build_diff(current, target_view)

    print("=" * 60)
    print(f"测试 AppID: {appid}")
    print(f"测试收藏夹: {collection}")
    print("差异预览:")
    print(f"  新增: {diff['add']}")
    print(f"  删除: {diff['remove']}")
    print(f"  保留: {diff['keep']}")
    print("=" * 60)

    if not args.apply:
        print("当前是预览模式，没有写入任何内容。")
        print("确认没问题后，关闭 Steam，再运行：")
        print(f"  python tools/verify_writeback.py --appid {appid} --apply")
        return 0

    if vdf_ops.is_steam_running():
        print("[失败] Steam 正在运行，请先完全关闭 Steam")
        return 1
    if not args.yes:
        ans = input(f"将写入「{collection}」（仅 1 个游戏），写入前会自动备份。确认？[y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            print("已取消")
            return 0

    try:
        record = vdf_ops.backup_store(steam_path, steamid32, notes="写回验收测试")
        print(f"[备份] {record.created_at_ts} -> {record.file_path}")
        result = vdf_ops.update_categories(steam_path, steamid32, desired)
    except Exception as e:
        print(f"[失败] 写入异常: {e}")
        return 1

    print(f"[完成] 已更新 {result.get('written', 0)} 个收藏夹，"
          f"验证 {'通过' if result.get('verified') else '失败'}")
    print("请打开 Steam 检查是否出现「库集_验收测试」收藏夹。")
    print("验证完成后运行：")
    print("  python tools/verify_writeback.py --rollback")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
