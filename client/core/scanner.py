"""游戏扫描管线（M1）：已安装（ACF）+ 家庭共享（librarycache）+ 现有分类 → SQLite。

四路数据（v0 验证 + POC-1/5 实测）：
1. 本地 ACF：已安装游戏（StateFlags & 4 过滤，多库去重）
2. Steam API GetOwnedGames：自有游戏（可选 Key；不含家庭共享）
3. 本地 librarycache：全部可下载（含家庭共享，零 API，M1.8）
4. 现有 Steam 分类（cloudstorage JSON，M1.4）

来源判定（v1.5）：自有集（API owned / LastOwner==本人 的已安装）之外
仅出现在 librarycache → FAMILY_SHARED。
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from client.core.existing_categories import read_existing_categories
from client.core.library_cache import get_local_library_appids
from client.models import Game
from client.storage.database import get_session
from client.utils.vdf_parser import (
    is_fully_installed,
    list_appmanifests,
    parse_appmanifest,
    parse_library_folders,
    parse_vdf_file,
)

STEAMID64_OFFSET = 76561197960265728  # steamid32 → 64 转换基数


def steamid32_to_steamid64(steamid32: str) -> str:
    return str(STEAMID64_OFFSET + int(steamid32))


def normalize_steam_id(value: str) -> str | None:
    """把用户输入的 Steam ID 规范化为 steamid32（账号 ID）。

    支持：
    - SteamID64（17 位，> STEAMID64_OFFSET）
    - SteamID32 / account id（纯数字）
    - https://steamcommunity.com/profiles/<steamid64>
    - [U:1:<accountid>]
    """
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    m = re.search(r"profiles/(\d+)", text)
    if m:
        text = m.group(1)
    m = re.match(r"^\[U:1:(\d+)\]$", text)
    if m:
        return m.group(1)
    if text.isdigit():
        num = int(text)
        if num >= STEAMID64_OFFSET:
            return str(num - STEAMID64_OFFSET)
        return text
    return None


def _steamid32_from_loginusers(steam_path: Path) -> str | None:
    """读取 config/loginusers.vdf，返回当前登录账号的 steamid32。

    多账号时 Steam 会在这里记录 AutoLogin / Timestamp；优先选 AutoLogin=1
    且 Timestamp 最大的账号，避免仅按 userdata 目录 mtime 猜错账号。
    """
    path = Path(steam_path) / "config" / "loginusers.vdf"
    if not path.is_file():
        return None
    try:
        data = parse_vdf_file(path)
    except Exception as e:
        logger.debug(f"loginusers.vdf 解析失败: {e}")
        return None
    users = data.get("users", {}) if isinstance(data, dict) else {}
    if not isinstance(users, dict):
        return None
    candidates: list[tuple[int, int, int, str]] = []
    for sid64, info in users.items():
        sid64 = str(sid64)
        if not sid64.isdigit() or len(sid64) < 17:
            continue
        if not isinstance(info, dict):
            info = {}
        auto = 1 if str(info.get("AutoLogin", "0")) == "1" else 0
        remember = 1 if str(info.get("RememberPassword", "0")) == "1" else 0
        try:
            ts = int(str(info.get("Timestamp", "0")) or 0)
        except ValueError:
            ts = 0
        candidates.append((auto, ts, remember, sid64))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    sid64 = candidates[0][3]
    return str(int(sid64) - STEAMID64_OFFSET)


def find_steamid32(steam_path: Path) -> str | None:
    """取 userdata 下第一个数字目录作为当前账号（多账号时取最新访问？MVP 取第一个）。

    若本地未检测到，则回退到设置中的 Steam ID（补充选项）。
    """
    # 1) loginusers.vdf 当前登录账号（多账号场景最可靠）
    sid32 = _steamid32_from_loginusers(steam_path)
    if sid32 and (Path(steam_path) / "userdata" / sid32).is_dir():
        return sid32

    # 2) userdata 修改时间兜底
    userdata = Path(steam_path) / "userdata"
    if userdata.is_dir():
        ids = [p.name for p in userdata.iterdir() if p.name.isdigit()]
        if ids:
            return max(ids, key=lambda i: (userdata / i).stat().st_mtime)

    # 3) 设置里的 Steam ID 补充
    try:
        from client.config import get_settings

        return normalize_steam_id(get_settings().steam_id)
    except Exception:
        return None


def _scan_installed(steam_path: Path) -> dict[str, dict[str, Any]]:
    """本地 ACF 扫描：{appid: {name, install_path, library_path, last_owner}}。"""
    results: dict[str, dict[str, Any]] = {}
    for lib in parse_library_folders(steam_path):
        lib_path = Path(lib["path"])
        for acf in list_appmanifests(lib_path):
            try:
                manifest = parse_appmanifest(acf)
            except Exception as e:
                logger.debug(f"解析 ACF 失败 ({acf.name}): {e}")
                continue
            appid = manifest.get("appid", "")
            if not appid or not is_fully_installed(manifest):
                continue
            if appid in results:  # 跨库重复，保留首次
                continue
            results[appid] = {
                "name": manifest.get("name", ""),
                "install_path": str(lib_path / "steamapps" / "common" / manifest.get("installdir", "")),
                "library_path": str(lib_path),
                "last_owner": manifest.get("LastOwner", ""),
                "last_played": _parse_ts(manifest.get("LastPlayed")),
                "playtime_minutes": int(manifest.get("Playtime", "0") or 0),
            }
    logger.info(f"本地已安装: {len(results)} 款")
    return results


def _parse_ts(value: str | None) -> datetime | None:
    """ACF 时间戳（秒）→ datetime（UTC naive）。"""
    if not value or not value.isdigit():
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)


def scan_steam_library(
    steam_path: Path,
    steamid32: str | None = None,
    api_key: str = "",
    proxy: str = "",
    use_api: bool = True,
    fetch_names: bool = False,
    sync_metadata: bool = True,
) -> dict[str, dict[str, Any]]:
    """主扫描入口。返回 {appid: game_dict}（并 upsert 到 SQLite）。

    use_api=False 时跳过 GetOwnedGames（离线/无 Key 模式）。
    fetch_names=False 时不查 store 名称（秒级完成；名称缺失用 [appid] 占位，
    由「补全名称」独立任务收敛——2026-08-12 实测 store 限速严格，阻塞扫描体验差）。
    sync_metadata=True 时同步预制元数据到 MetadataCache（哩米需求：扫描即读预制，
    不联网重拉；预制缺失时静默降级）。
    """
    if sync_metadata:
        try:
            from client.core.metadata_loader import (
                apply_delisted_style,
                sync_prebuilt_classification,
                sync_prebuilt_metadata,
                sync_prebuilt_styles,
            )
            from client.core.scheme_service import load_official_scheme

            load_official_scheme()  # 确保官方方案存在（分类同步前置，幂等）
            sync_prebuilt_metadata()
            sync_prebuilt_classification()  # 预制分类 → GameClassification（哩米需求）
            sync_prebuilt_styles()  # 预置风格（R18/肉鸽）→ STYLE（哩米 2026-08-14 打包分发）
            apply_delisted_style()  # 已下架游戏 → 风格标签（哩米需求）
        except Exception as e:
            logger.warning(f"预制元数据同步失败（降级）: {e}")
    steamid32 = steamid32 or find_steamid32(steam_path)
    if not steamid32:
        logger.warning("未找到 steamid32（userdata 目录）")

    # 1. 已安装
    installed = _scan_installed(steam_path)

    # 2. 自有集（API owned ∪ LastOwner==本人）；api_key 为空时从 Settings 回退
    owned: set[str] = set()
    api_names: dict[str, str] = {}
    me64 = steamid32_to_steamid64(steamid32) if steamid32 else ""
    for appid, info in installed.items():
        if info.get("last_owner") == me64:
            owned.add(appid)
    if use_api and steamid32:
        if not api_key:
            from client.config import get_settings

            api_key = get_settings().steam_api_key
        if api_key:
            try:
                from client.api.steam_api import SteamClient

                client = SteamClient(api_key=api_key, proxy=proxy)
                games = client.get_owned_games(steamid32_to_steamid64(steamid32))
                owned |= {str(g["appid"]) for g in games}
                api_names = {str(g["appid"]): g.get("name", "") for g in games}
                logger.info(f"Steam API owned: {len(owned)} 款（自有）")
            except Exception as e:
                logger.warning(f"Steam API 扫描失败（降级为本地）: {e}")
        else:
            logger.warning("未配置 Steam API Key，自有判定降级为 LastOwner（不准）")

    # 3. 家庭共享（librarycache）
    library_appids: list[str] = []
    if steamid32:
        library_appids = get_local_library_appids(steam_path, steamid32)

    # 4. 名称补全（可选，默认关闭：store 限速严格，独立「补全名称」任务触发）
    app_names: dict[str, str] = {}
    if fetch_names:
        try:
            from client.api.steam_api import SteamClient

            app_names = SteamClient(api_key=api_key, proxy=proxy).get_app_names(
                [a for a in set(library_appids) - set(installed) - set(api_names)][:200]
            )
        except Exception as e:
            logger.warning(f"Store 名称补全失败: {e}")

    # 5. 合并 + 来源判定
    # 2026-08-15: Steam API owned games must enter final list
    all_appids = set(installed) | set(library_appids) | set(api_names)
    games: dict[str, dict[str, Any]] = {}
    for appid in all_appids:
        inst = installed.get(appid, {})
        name = (
            inst.get("name")
            or api_names.get(appid)
            or app_names.get(appid)
            or f"[{appid}]"  # 名称兜底
        )
        games[appid] = {
            "appid": appid,
            "name": name,
            "install_path": inst.get("install_path"),
            "library_path": inst.get("library_path"),
            "is_installed": appid in installed,
            "source": "OWNED" if appid in owned else "FAMILY_SHARED",
            "last_played": inst.get("last_played"),
            "playtime_minutes": inst.get("playtime_minutes", 0),
            "scanned_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }
    logger.info(
        f"扫描完成: {len(games)} 款（自有 {sum(1 for g in games.values() if g['source'] == 'OWNED')}"
        f" + 家庭共享 {sum(1 for g in games.values() if g['source'] == 'FAMILY_SHARED')}）"
    )

    # 6. 现有分类 → Game.existing_steam_categories
    if steamid32:
        existing = read_existing_categories(steam_path, steamid32)
        for appid, cats in existing.items():
            if appid in games:
                games[appid]["existing_steam_categories"] = json.dumps(cats, ensure_ascii=False)

    # 7. 入库（upsert）
    _upsert_games(games)
    return games


def _upsert_games(games: dict[str, dict[str, Any]]) -> None:
    """扫描结果 upsert 到 games 表（appid 主键），并从预制元数据填充 steam_tags。"""
    session = get_session()
    try:
        # 查询包含逻辑删除记录：若同一 appid 曾被删除，重新扫描时复活，避免重复插入
        existing = {g.appid: g for g in session.query(Game).all()}
        # 预制元数据 → steam_tags + developer/publisher（规则引擎字段数据源，哩米需求 2026-08-13）
        from client.models import MetadataCache

        meta_info = {
            m.appid: m for m in session.query(MetadataCache).filter(
                MetadataCache.store_tags.isnot(None)).all()
        }
        for appid, info in games.items():
            game = existing.get(appid)
            if game is None:
                game = Game(appid=appid)
                session.add(game)
            elif game.del_flag:
                game.del_flag = False  # 复活历史逻辑删除记录
            # 名称保护：已有真实名称（SGDB 补全等）不被扫描占位覆盖（2026-08-13 修复）
            if not game.name or game.name.startswith("["):
                game.name = info["name"]
            game.install_path = info.get("install_path")
            game.library_path = info.get("library_path")
            game.is_installed = info.get("is_installed", False)
            game.source = info.get("source", "OWNED")
            game.last_played = info.get("last_played")
            game.playtime_minutes = info.get("playtime_minutes", 0)
            game.existing_steam_categories = info.get("existing_steam_categories", "[]")
            game.scanned_at = info.get("scanned_at")
            meta = meta_info.get(appid)
            if meta:
                if meta.store_tags:
                    game.steam_tags = meta.store_tags
                if meta.developer:
                    game.developer = meta.developer  # 开发商（哩米需求）
                if meta.publisher:
                    game.publisher = meta.publisher  # 发行商
        session.commit()
        logger.info(f"入库完成: {len(games)} 款（含元数据标签 {sum(1 for g in games if g in meta_info)}）")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
