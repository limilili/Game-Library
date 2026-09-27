"""VDF 操作（M3）：cloudstorage JSON 写回 + 双备份 + 回滚 + 验证 + 检测。

对应 PRD §5.4 安全写入流程（POC-3 落位版）：
- M3.1 双备份：cloudstorage JSON + sharedconfig.vdf → %APPDATA%/KuJi/.backup/<ts>/（哈希校验）
- M3.2 增量应用：目标分类 vs 现有分类差异分析（新增/删除/保留）
- M3.3 一键回滚：从备份恢复
- M3.6 Steam 运行检测（运行中禁止写入）
- M3.7 Cloud 状态辅助（POC-7：per-app 状态可读，全局开关不可检测 → 写前提示）
- M3.8 写入验证：写后回读比对

铁律：真实写入前必须 哩米确认 + Steam 关闭 + 备份成功（三者缺一不可）；
本模块所有写入函数默认只操作沙盒副本路径，真实路径由调用方显式传入。
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from loguru import logger

from client.models import BackupRecord
from client.storage.database import get_session

STORE_NAME = "cloud-storage-namespace-1.json"
SHARED_CONFIG = "sharedconfig.vdf"
# 库集管理的 Steam 收藏夹前缀（全量同步范围；非此前缀的用户收藏夹绝不触碰）
KUJI_PREFIX = "库集_"
# 风格收藏夹名白名单（哩米 2026-08-14：多选增量写回建立的「库集_风格」收藏夹
# 不在 TYPE 全量 target 内——全量写回软删除旧库集夹时须跳过，否则互相摧毁）
STYLE_COLLECTION_NAMES = {"肉鸽", "R18", "无需补丁", "需要补丁", "擦边", "已下架"}
# Steam 序列化格式（POC-3 字节级实测匹配）
SERIALIZE_KW = {"ensure_ascii": False, "separators": (",", ":")}


def get_store_path(steam_path: Path, steamid32: str) -> Path:
    return Path(steam_path) / "userdata" / steamid32 / "config" / "cloudstorage" / STORE_NAME


def get_sharedconfig_path(steam_path: Path, steamid32: str) -> Path:
    return Path(steam_path) / "userdata" / steamid32 / "config" / SHARED_CONFIG


def backup_dir() -> Path:
    base = Path(os.environ.get("APPDATA", str(Path.home()))) / "KuJi" / ".backup"
    return base


def read_store(path: Path) -> list:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def serialize(data: list) -> bytes:
    return json.dumps(data, **SERIALIZE_KW).encode("utf-8")


def file_sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ── M3.6 Steam 运行检测 ─────────────────────────────────────────


def is_steam_running() -> bool:
    """Steam 进程检测（tasklist；失败时保守返回 True——禁止写入）。

    中文系统 tasklist 输出为 GBK——显式 encoding 解码（2026-08-13 修复：
    默认 UTF-8 解码抛 UnicodeDecodeError → stdout=None → 崩溃，写回被误拦）。
    """
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq steam.exe", "/NH"],
            capture_output=True, text=True, timeout=10,
            encoding="gbk", errors="replace",
        )
        return "steam.exe" in (out.stdout or "")
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning(f"Steam 进程检测失败（保守禁止写入）: {e}")
        return True


# ── M3.1 双备份 ─────────────────────────────────────────────────


class StoreFormatError(RuntimeError):
    """Steam 数据格式变更（A3：写前结构快照校验失败——Valve 更新格式时拒绝写入）。"""


def validate_store_structure(data: list) -> None:
    """写前结构校验（审查 A3，2026-08-13）：cloudstorage JSON 结构快照。

    校验内容（POC-2/POC-3 实测的结构不变量）：
    1. 顶层为 list；条目为 [key, meta]
    2. meta 为 dict，含 key 字段且与条目 key 一致
    3. user-collections 有效条目 value 为 JSON 字符串 {name, added[], removed[]}
    4. 软删除条目 is_deleted=true 无 value
    不匹配 → StoreFormatError（Steam 可能改了格式，拒绝写入保护用户数据）。
    """
    if not isinstance(data, list):
        raise StoreFormatError(f"顶层结构应为 list，实际 {type(data).__name__}")
    for entry in data:
        if not isinstance(entry, list) or len(entry) != 2:
            raise StoreFormatError(f"条目应为 [key, meta]，实际 {type(entry).__name__}")
        key, meta = entry
        if not isinstance(key, str) or not isinstance(meta, dict):
            raise StoreFormatError(f"条目类型异常: key={type(key).__name__}, meta={type(meta).__name__}")
        if meta.get("key") != key:
            raise StoreFormatError(f"meta.key({meta.get('key')!r}) 与条目 key({key!r}) 不一致")
        if meta.get("is_deleted"):
            continue
        # 仅 user-collections 条目严格校验 value 结构（showcases 等非收藏夹条目结构不同）
        if not key.startswith("user-collections"):
            continue
        value = meta.get("value")
        if not isinstance(value, str):
            raise StoreFormatError(f"条目 {key} 的 value 应为 JSON 字符串，实际 {type(value).__name__}")
        try:
            inner = json.loads(value)
        except ValueError as e:
            raise StoreFormatError(f"条目 {key} 的 value 非 JSON: {e}") from e
        if not isinstance(inner, dict) or not isinstance(inner.get("added"), list) \
                or not isinstance(inner.get("removed"), list):
            raise StoreFormatError(f"条目 {key} 的 value 缺少 name/added/removed 结构")


def backup_store(steam_path: Path, steamid32: str, notes: str = "") -> BackupRecord:
    """双备份（cloudstorage JSON + sharedconfig.vdf）→ .backup/<ts>/，记录哈希。

    审查 A3（2026-08-13）：备份前先做写前结构快照校验（validate_store_structure），
    Steam 格式变更时拒绝写入保护用户数据。
    """
    store = get_store_path(steam_path, steamid32)
    if not store.exists():
        raise FileNotFoundError(f"cloudstorage 镜像 JSON 不存在: {store}")

    # A3：写前结构校验（格式变更防护）
    validate_store_structure(read_store(store))

    ts = time.strftime("%Y%m%d%H%M%S")
    target_dir = backup_dir() / ts
    target_dir.mkdir(parents=True, exist_ok=True)

    store_backup = target_dir / STORE_NAME
    shutil.copy2(store, store_backup)

    shared = get_sharedconfig_path(steam_path, steamid32)
    shared_backup = None
    if shared.exists():  # sharedconfig.vdf 不存在时跳过（POC-2 实测：部分环境无此文件）
        shared_backup = target_dir / SHARED_CONFIG
        shutil.copy2(shared, shared_backup)

    session = get_session()
    try:
        record = BackupRecord(
            created_at_ts=ts,
            file_path=str(store_backup),
            sha256=file_sha256(store_backup),
            steam_categories_snapshot=json.dumps(
                read_store(store), ensure_ascii=False
            ),
            game_count=0,
            category_count=0,
            notes=notes or ("双备份" if shared_backup else "仅 JSON（sharedconfig 不存在）"),
            is_auto=False,
        )
        session.add(record)
        session.commit()
        logger.info(
            f"M3.1 备份完成: {ts}（JSON {store_backup.name} + "
            f"{'sharedconfig' if shared_backup else '无 sharedconfig'}）"
        )
        return record
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ── M3.2 差异分析 ───────────────────────────────────────────────


def build_diff(
    current: dict[str, list[str]], target: dict[str, list[str]]
) -> dict[str, Any]:
    """目标分类 vs 现有分类差异（appid → 分类列表）。

    current: 现有 {appid: [分类名]}（M1.4 读取）；target: 库集目标 {appid: [分类名]}。
    返回 {add: {appid: [新增分类]}, remove: {appid: [移除分类]}, keep: {appid: [不变分类]}}
    """
    add: dict[str, list[str]] = {}
    remove: dict[str, list[str]] = {}
    keep: dict[str, list[str]] = {}
    for appid, targets in target.items():
        cur = set(current.get(appid, []))
        new_added = [c for c in targets if c not in cur]
        removed = [c for c in cur if c not in targets]
        kept = [c for c in cur if c in targets]
        if new_added:
            add[appid] = new_added
        if removed:
            remove[appid] = removed
        if kept:
            keep[appid] = kept
    return {"add": add, "remove": remove, "keep": keep}


# ── M3.8 写入 + 验证 ────────────────────────────────────────────


def write_categories(
    steam_path: Path, steamid32: str, target: dict[str, list[str]]
) -> dict[str, Any]:
    """写回目标分类（全量同步 + 原子写入 + 写后回读验证，M3.2/M3.8）。

    审查修复 L2（2026-08-13）：原实现只加不删（remove 差异白算）且旧「库集_」
    收藏夹永不清理。现语义：
    - 库集管理的收藏夹（KUJI_PREFIX 前缀）**全量同步**：added 集合与目标完全一致
      （增/删双向生效）
    - 目标外的旧「库集_」收藏夹软删除（is_deleted=true，旧 42 类自动迁移清理）
    - 非库集收藏夹（用户自有）绝不触碰

    注意：调用方必须先执行 backup_store 且确认 Steam 未运行（铁律）。
    target: {appid: [分类名]}。返回 {written, deleted, verified, store_path}
    """
    store_path = get_store_path(steam_path, steamid32)
    data = read_store(store_path)

    # 目标收藏夹集合：{name: sorted(appids)}
    desired: dict[str, set[str]] = {}
    for appid, names in target.items():
        for name in names:
            desired.setdefault(name, set()).add(str(appid))

    written = 0
    deleted = 0
    now = int(time.time())
    for entry in data:
        key, meta = entry
        if not key.startswith("user-collections") or not isinstance(meta, dict):
            continue
        name = _entry_name(meta)
        if name is None:
            continue  # 软删除/异常条目不动
        if name in desired:
            # 全量同步：added 与目标一致（增删双向；appid 写 int——客户端要求）
            inner = json.loads(meta["value"])
            wanted = sorted(desired.pop(name))
            inner["added"] = [_as_int_appid(a) for a in wanted]
            wanted_str = {str(w) for w in wanted}
            inner["removed"] = [a for a in inner.get("removed", []) if str(a) not in wanted_str]
            meta["timestamp"] = now
            meta["value"] = json.dumps(inner, **SERIALIZE_KW)
            meta["version"] = str(int(meta.get("version", "0")) + 1)
            written += 1
        elif name.startswith(KUJI_PREFIX):
            # 目标外旧库集收藏夹 → 软删除（L2：旧 42 类迁移清理）；
            # 风格收藏夹（库集_肉鸽 等）不在 TYPE target 内——白名单保留（哩米 2026-08-14）
            style_name = name[len(KUJI_PREFIX):]
            if style_name in STYLE_COLLECTION_NAMES:
                continue
            entry[1] = {
                "key": key,
                "timestamp": now,
                "is_deleted": True,
                "version": str(int(meta.get("version", "0")) + 1),
            }
            deleted += 1

    # 新增目标中不存在的收藏夹
    for name, appids in desired.items():
        key = _new_collection_key(data, name)
        _set_entry_added(data, key, name, sorted(appids))
        written += 1

    atomic_write(store_path, data)

    # M3.8 写后回读验证
    reloaded = read_store(store_path)
    verified = _verify_target(reloaded, target)
    logger.info(
        f"M3.8 写回完成: {written} 收藏夹同步, {deleted} 旧收藏夹软删除, "
        f"验证 {'通过' if verified else '失败'}"
    )
    return {"written": written, "deleted": deleted, "verified": verified,
            "store_path": str(store_path)}


def atomic_write(path: Path, data: list) -> None:
    """原子写入（POC-3 落位：同目录 .tmp + os.replace，失败原文件不变）。"""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = serialize(data)
    tmp.write_bytes(payload)
    try:
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _existing_collections(data: list) -> dict[str, str]:
    """{分类名: key}（有效条目）。"""
    result: dict[str, str] = {}
    for key, meta in data:
        if not key.startswith("user-collections") or not isinstance(meta, dict):
            continue
        if meta.get("is_deleted") or "value" not in meta:
            continue
        try:
            inner = json.loads(meta["value"])
            result[inner.get("name", "")] = key
        except ValueError:
            continue
    return result


def update_categories(
    steam_path: Path, steamid32: str, desired: dict[str, set[str]]
) -> dict[str, Any]:
    """增量更新指定收藏夹（多选模式「应用到 Steam」，哩米 2026-08-14）。

    与 write_categories 全量同步不同：只把 desired 内收藏夹的 added 合并
    目标 appid（只加不删），其他收藏夹一律不动（不软删除）——用于 GUI
    选中部分分类后增量写回。

    注意：调用方必须先执行 backup_store 且确认 Steam 未运行（铁律）。
    desired: {收藏夹名: appids}。
    """
    store_path = get_store_path(steam_path, steamid32)
    data = read_store(store_path)

    written = 0
    now = int(time.time())
    existing = _existing_collections(data)
    for name, appids in desired.items():
        key = existing.get(name)
        if key is None:
            # 目标收藏夹不存在 → 新建
            key = _new_collection_key(data, name)
            _set_entry_added(data, key, name, sorted(appids))
            written += 1
            continue
        # 合并 added（只加不删）
        for entry in data:
            if entry[0] != key:
                continue
            meta = entry[1]
            if not isinstance(meta, dict) or "value" not in meta:
                continue
            inner = json.loads(meta["value"])
            merged = set(inner.get("added", [])) | {int(a) for a in appids}
            if merged != set(inner.get("added", [])):
                inner["added"] = sorted(merged)
                meta["timestamp"] = now
                meta["value"] = json.dumps(inner, **SERIALIZE_KW)
                meta["version"] = str(int(meta.get("version", "0")) + 1)
                written += 1
            break

    atomic_write(store_path, data)

    # 写后回读验证：目标收藏夹均存在且包含目标 appid
    reloaded = read_store(store_path)
    cur = _existing_collections(reloaded)
    verified = all(
        name in cur
        and set(desired[name]) <= {str(a) for a in _entry_appids(reloaded, cur[name])}
        for name in desired
    )
    logger.info(f"增量写回完成: {written} 收藏夹更新, 验证 {'通过' if verified else '失败'}")
    return {"written": written, "verified": verified, "store_path": str(store_path)}


def _entry_appids(data: list, key: str) -> list:
    """收藏夹条目 appid 列表。"""
    for k, meta in data:
        if k != key or not isinstance(meta, dict):
            continue
        try:
            return json.loads(meta.get("value", "{}")).get("added", [])
        except ValueError:
            return []
    return []


def delete_kuji_collections(steam_path: Path, steamid32: str) -> dict[str, Any]:
    """批量删除所有「库集_」前缀收藏夹（哩米 2026-08-14）。

    只处理名称以 KUJI_PREFIX 开头且未删除的 user-collections 条目——
    用户自有收藏夹（收藏夹/1demo/已隐藏 等）绝不触碰。
    软删除（is_deleted=true）与写回清理语义一致，Steam 客户端会自动移除。

    注意：调用方必须先执行 backup_store 且确认 Steam 未运行（铁律）。
    """
    store_path = get_store_path(steam_path, steamid32)
    data = read_store(store_path)

    deleted = 0
    now = int(time.time())
    for entry in data:
        key, meta = entry
        if not key.startswith("user-collections") or not isinstance(meta, dict):
            continue
        if meta.get("is_deleted") or "value" not in meta:
            continue
        try:
            name = json.loads(meta["value"]).get("name", "")
        except ValueError:
            continue
        if name.startswith(KUJI_PREFIX):
            entry[1] = {
                "key": key,
                "timestamp": now,
                "is_deleted": True,
                "version": str(int(meta.get("version", "0")) + 1),
            }
            deleted += 1

    atomic_write(store_path, data)

    # 写后回读验证：库集_ 前缀有效条目应为 0
    reloaded = read_store(store_path)
    remaining = 0
    for key, meta in reloaded:
        if not key.startswith("user-collections") or not isinstance(meta, dict):
            continue
        if meta.get("is_deleted") or "value" not in meta:
            continue
        try:
            if json.loads(meta["value"]).get("name", "").startswith(KUJI_PREFIX):
                remaining += 1
        except ValueError:
            continue
    verified = remaining == 0
    logger.info(
        f"批量删除库集收藏夹: {deleted} 个, 剩余 {remaining}, "
        f"验证 {'通过' if verified else '失败'}"
    )
    return {"deleted": deleted, "verified": verified, "store_path": str(store_path)}


def _new_collection_key(data: list, name: str) -> str:
    """新建收藏夹 key（uc- 前缀随机，与 Steam 格式一致）。"""
    import uuid

    key = f"user-collections.uc-{uuid.uuid4().hex[:8]}"
    data.append(
        [
            key,
            {
                "key": key,
                "timestamp": int(time.time()),
                "value": json.dumps(
                    {"id": key.removeprefix("user-collections."), "name": name,
                     "added": [], "removed": []},
                    **SERIALIZE_KW,
                ),
                "version": "1",
                "conflictResolutionMethod": "custom",
                "strMethodId": "union-collections",
            },
        ]
    )
    return key


def _upsert_entry(data: list, key: str, name: str, appid: str) -> None:
    """把 appid 加入收藏夹条目（added 去重，str/int 兼容比较；L2 后仅兼容用）。"""
    for entry in data:
        if entry[0] == key:
            meta = entry[1]
            meta["timestamp"] = int(time.time())
            inner = json.loads(meta["value"])
            if str(appid) not in {str(a) for a in inner["added"]}:
                inner["added"].append(_as_int_appid(appid))
            if str(appid) in {str(a) for a in inner["removed"]}:
                inner["removed"].remove(appid)
            meta["value"] = json.dumps(inner, **SERIALIZE_KW)
            meta["version"] = str(int(meta.get("version", "0")) + 1)
            return


def _entry_name(meta: dict) -> str | None:
    """条目的收藏夹名（软删除/无 value/解析失败 → None）。"""
    if meta.get("is_deleted") or "value" not in meta:
        return None
    try:
        return json.loads(meta["value"]).get("name")
    except (ValueError, TypeError):
        return None


def _as_int_appid(value) -> int | str:
    """appid 规范化：数字字符串 → int（2026-08-13 实测：Steam 客户端忽略
    字符串 appid 的收藏夹——v0 收藏夹 added 为 int 客户端可见，我们写 str 不可见）。"""
    s = str(value)
    return int(s) if s.isdigit() else value


def _set_entry_added(data: list, key: str, name: str, appids: list[str]) -> None:
    """设置收藏夹条目的 added 集合（全量覆盖；appid 写 int——客户端要求）。"""
    for entry in data:
        if entry[0] == key:
            meta = entry[1]
            inner = json.loads(meta["value"])
            inner["added"] = [_as_int_appid(a) for a in appids]
            meta["timestamp"] = int(time.time())
            meta["value"] = json.dumps(inner, **SERIALIZE_KW)
            meta["version"] = str(int(meta.get("version", "0")) + 1)
            return


def _verify_target(data: list, target: dict[str, list[str]]) -> bool:
    """验证（L2 全量同步语义）：
    1. 目标 (appid, 分类名) 全部命中
    2. 库集收藏夹的 added 集合与目标完全一致（无多余残留）
    3. 无目标外残留的库集收藏夹（旧类已软删除）
    """
    by_name: dict[str, set[str]] = {}
    kuji_names: set[str] = set()
    for key, meta in data:
        if not key.startswith("user-collections") or not isinstance(meta, dict):
            continue
        name = _entry_name(meta)
        if name is None:
            continue
        if name.startswith(KUJI_PREFIX):
            kuji_names.add(name)
        try:
            inner = json.loads(meta["value"])
            # added 元素可能是 int（Steam 规范化）或 str（旧写回）——统一 str 比较
            by_name.setdefault(name, set()).update(
                str(a) for a in inner.get("added", []))
        except (ValueError, TypeError):
            continue
    # 目标集合
    desired: dict[str, set[str]] = {}
    for appid, names in target.items():
        for name in names:
            desired.setdefault(name, set()).add(str(appid))
    # 1) 目标命中 + 2) 库集集合精确一致
    for name, appids in desired.items():
        if name.startswith(KUJI_PREFIX) and by_name.get(name, set()) != appids:
            return False
        if not name.startswith(KUJI_PREFIX):
            if not appids <= by_name.get(name, set()):
                return False
    # 3) 目标外无残留库集收藏夹
    return not (kuji_names - set(desired))


# ── M3.3 回滚 ──────────────────────────────────────────────────


def rollback(record: BackupRecord, store_path: Path) -> None:
    """从备份恢复（M3.3）：把备份文件恢复到指定目标路径。

    ⚠️ 目标路径必须由调用方显式传入（真实写入流程传入真实 store 路径；
    测试传沙盒路径）——2026-08-12 曾因内部硬编码真实路径导致测试污染真实文件。
    """
    backup_file = Path(record.file_path)
    if not backup_file.exists():
        raise FileNotFoundError(f"备份文件不存在: {backup_file}")
    store_path = Path(store_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_file, store_path)

    # 同时恢复 sharedconfig.vdf（备份时若存在；哩米 2026-09-27 修复回滚不完整）
    shared_backup = backup_file.parent / SHARED_CONFIG
    if shared_backup.exists():
        shared_target = store_path.parent.parent / SHARED_CONFIG
        shared_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(shared_backup, shared_target)
        logger.info(f"M3.3 回滚 sharedconfig: {shared_backup} → {shared_target}")

    logger.info(f"M3.3 回滚完成: {backup_file} → {store_path}")


# ── POC-7 辅助：cloud 状态提示数据 ──────────────────────────────


def cloud_status(steam_path: Path, steamid32: str) -> dict[str, Any]:
    """写前 Cloud 状态辅助（POC-7 结论：全局开关不可检测 → 提供 per-app/待上传信息供提示）。

    返回 {modified_pending: bool, synchronized_count: int, changesincloud_count: int}
    """
    cs_dir = Path(steam_path) / "userdata" / steamid32 / "config" / "cloudstorage"
    modified = list(cs_dir.glob("*.modified.json")) if cs_dir.is_dir() else []
    return {"modified_pending": bool(modified), "modified_files": len(modified)}
