"""M1.4：读取现有 Steam 分类（cloudstorage 收藏集镜像 JSON，POC-2 落位版）。

数据源：userdata/<id>/config/cloudstorage/cloud-storage-namespace-1.json
条目结构（实测）：[key, {key, timestamp, value, version, ...}]，
value 为 JSON 字符串 {"id","name","added":[...],"removed":[...]}；软删除 is_deleted=true 无 value。
"""
import json
from pathlib import Path

from loguru import logger

STORE_NAME = "cloud-storage-namespace-1.json"


def read_existing_categories(steam_path: Path, steamid32: str) -> dict[str, list[str]]:
    """读取现有 Steam 分类：{appid: [分类名, ...]}。

    失败（文件缺失/解析错误）返回空 dict 并记 warning——现有分类读取非致命（降级策略）。
    """
    store = (
        Path(steam_path)
        / "userdata"
        / steamid32
        / "config"
        / "cloudstorage"
        / STORE_NAME
    )
    if not store.exists():
        logger.warning(f"cloudstorage 镜像 JSON 不存在: {store}")
        return {}

    result: dict[str, list[str]] = {}
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        logger.warning(f"cloudstorage 镜像 JSON 解析失败: {e}")
        return {}

    for key, meta in data:
        if not key.startswith("user-collections") or not isinstance(meta, dict):
            continue
        if meta.get("is_deleted") or "value" not in meta:
            continue
        try:
            inner = json.loads(meta["value"])
        except (ValueError, TypeError):
            continue
        name = inner.get("name", "")
        for appid in inner.get("added", []):
            result.setdefault(str(appid), []).append(name)
    logger.info(f"现有 Steam 分类: {len(result)} 个游戏有分类")
    return result
