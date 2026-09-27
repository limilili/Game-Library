"""POC-3 核心库：cloudstorage 镜像 JSON 的原子写入 + 备份 + 回滚（沙盒验证）。

对应 PRD M3（VDF 操作模块，写回路线 cloudstorage JSON）。
2026-08-11 实测确认（真实 cloudstorage JSON 字节级匹配）：
- Steam 序列化格式 = json.dumps(ensure_ascii=False, separators=(',', ':'))，无换行无缩进
- 条目结构：[key, meta]，meta 含 key/timestamp/value/version/conflictResolutionMethod/strMethodId
- value 为 JSON 字符串 {"id","name","added","removed"}；软删除条目 is_deleted=true 且无 value 字段

⚠️ 沙盒只读/副本操作：本模块只操作 tests/sandbox/cloudstorage/data/ 下的工作副本，
绝不接触真实 Steam 文件（CLAUDE.md 铁律）。
"""
import hashlib
import json
import shutil
import time
from pathlib import Path

STORE_NAME = "cloud-storage-namespace-1.json"

# Steam 客户端序列化格式（2026-08-11 字节级实测匹配）
SERIALIZE_KW = {"ensure_ascii": False, "separators": (",", ":")}


def read_store(path: Path) -> list:
    """读取 cloudstorage 镜像 JSON（顶层为 list）。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def serialize(data: list) -> bytes:
    """按 Steam 格式序列化（与真实文件字节级一致）。"""
    return json.dumps(data, **SERIALIZE_KW).encode("utf-8")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_write(path: Path, data: list) -> None:
    """原子写入：同目录临时文件 + 序列化 + os.replace（参考 dtq1997 原子写入）。

    失败（如序列化异常）时原文件保持不变；成功时无 .tmp 残留。
    """
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = serialize(data)  # 先序列化，失败则直接抛，不碰任何文件
    tmp.write_bytes(payload)
    try:
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def backup(path: Path, backup_dir: Path) -> Path:
    """时间戳备份（模拟 PRD M3.1：%APPDATA%/KuJi/.backup/<timestamp>/）。

    备份前校验原文件可解析；返回备份文件路径。
    """
    path = Path(path)
    read_store(path)  # 校验：不可解析的源文件不备份
    ts = time.strftime("%Y%m%d%H%M%S")
    target_dir = Path(backup_dir) / ts
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / STORE_NAME
    shutil.copy2(path, target)
    return target


def _touch_timestamp() -> int:
    return int(time.time())


def upsert_collection(
    data: list, key: str, name: str, added: list[int], removed: list[int]
) -> None:
    """新增或更新 user-collections 条目（保留 Steam 六字段结构）。

    已存在：更新 value/timestamp，version 递增，保留 conflictResolutionMethod/strMethodId。
    不存在：新建条目，version 从 "1" 起（⚠️ Steam 新条目 version 起点未实测，Phase 1 vdf_ops 校准）。
    """
    value = json.dumps(
        {"id": key.removeprefix("user-collections."), "name": name,
         "added": added, "removed": removed},
        **SERIALIZE_KW,
    )
    for entry in data:
        if entry[0] == key:
            meta = entry[1]
            meta["timestamp"] = _touch_timestamp()
            meta["value"] = value
            meta["version"] = str(int(meta.get("version", "0")) + 1)
            return
    data.append(
        [
            key,
            {
                "key": key,
                "timestamp": _touch_timestamp(),
                "value": value,
                "version": "1",
                "conflictResolutionMethod": "custom",
                "strMethodId": "union-collections",
            },
        ]
    )


def soft_delete(data: list, key: str) -> None:
    """软删除条目：meta 替换为 {key, timestamp, is_deleted:true, version}（与实测结构一致）。"""
    for i, entry in enumerate(data):
        if entry[0] == key:
            old = entry[1]
            data[i] = [
                key,
                {
                    "key": key,
                    "timestamp": _touch_timestamp(),
                    "is_deleted": True,
                    "version": str(int(old.get("version", "0")) + 1),
                },
            ]
            return
    raise KeyError(f"条目不存在: {key}")


def rollback(path: Path, backup_path: Path) -> None:
    """从备份恢复（写入失败自动回滚，PRD M3.2）。恢复后文件哈希 == 备份哈希。"""
    path = Path(path)
    shutil.copy2(backup_path, path)
