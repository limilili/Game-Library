"""Steam Web API 客户端（POC-5 落位版）：GetOwnedGames + GetAppList（全量名称映射）。

POC-5 实测结论（2026-08-12，已验证信息 §10）：
- GetOwnedGames 仅返回「玩过的免费游戏」；家庭共享游戏不返回
- 国内网络延迟波动大（3~10s+）→ 必须重试 + 可选代理
- 限速 200ms（PRD 约定）
- GetAppList 无 Key 可调（家庭共享游戏名称补全，M1.8 配套）

Key 安全：只从 Settings.steam_api_key（AES 加密存储）读取，不入源码。
"""
import json
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from client.config import get_settings

BASE_URL = "https://api.steampowered.com"
APP_LIST_CACHE = "app_list.json"


def _extract_metadata(appid: str, d: dict) -> dict:
    """appdetails data → 元数据字典（全字段，v0 字段对齐）。"""
    langs = d.get("supported_languages", {})
    if isinstance(langs, dict):
        langs = list(langs.keys())[:8]
    elif isinstance(langs, str):
        langs = [langs]
    else:
        langs = []
    genres = [g["description"] for g in d.get("genres", [])]
    return {
        "appid": appid,
        "name": d.get("name", ""),
        "type": d.get("type", "unknown"),  # game|software|dlc|demo|music|video（工具软件识别）
        "developer": ", ".join(d.get("developers", [])) or None,
        "publisher": ", ".join(d.get("publishers", [])) or None,
        "release_date": d.get("release_date", {}).get("date") or None,
        "store_tags": json.dumps(genres, ensure_ascii=False),
        "genres": json.dumps(genres, ensure_ascii=False),
        "supported_languages": json.dumps(langs, ensure_ascii=False),
    }


class SteamAPIError(Exception):
    """Steam Web API 错误（统一错误码：401 无效Key / 429 限速 / 超时等）。"""

    def __init__(self, status: int | None, detail: str) -> None:
        self.status = status
        super().__init__(f"SteamAPIError[{status}]: {detail}")


class SteamClient:
    """Steam Web API 客户端（同步 httpx，限速 + 超时/网络错误重试；4xx 不重试）。"""

    def __init__(self, api_key: str = "", proxy: str = "", rate_limit: float = 0.2,
                 timeout: float = 15.0, retries: int = 2) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.steam_api_key
        proxy = proxy or settings.proxy
        self.rate_limit = rate_limit or settings.api_rate_limit_ms / 1000.0
        self.retries = retries or settings.api_retries
        self._last_call = 0.0
        self._client = httpx.Client(timeout=timeout, proxy=proxy or None)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_call = time.monotonic()

    def _call(self, interface: str, method: str, params: dict[str, Any],
              version: str = "v0001") -> dict:
        query = {"format": "json", **params}
        if self.api_key:
            query["key"] = self.api_key
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self._client.get(
                    f"{BASE_URL}/{interface}/{method}/{version}/", params=query
                )
                break
            except httpx.TimeoutException as e:
                last_err = SteamAPIError(None, "请求超时")
                if attempt == self.retries:
                    raise last_err from e
                time.sleep(0.5 * (attempt + 1))
            except httpx.HTTPError as e:
                last_err = SteamAPIError(None, f"网络错误: {e}")
                if attempt == self.retries:
                    raise last_err from e
                time.sleep(0.5 * (attempt + 1))
        if resp.status_code == 401:
            raise SteamAPIError(401, "无效的 API Key")
        if resp.status_code == 429:
            raise SteamAPIError(429, "触发限速（429），请降低调用频率")
        if resp.status_code >= 400:
            raise SteamAPIError(resp.status_code, resp.text[:200])
        return resp.json()

    def get_owned_games(self, steamid: str) -> list[dict]:
        """GetOwnedGames：自有游戏（含玩过的免费游戏；不含家庭共享）。"""
        data = self._call(
            "IPlayerService", "GetOwnedGames",
            {"steamid": steamid, "include_appinfo": 1, "include_played_free_games": 1},
        )
        return (data.get("response", {}) or {}).get("games", [])

    # ── 应用元数据补全（Store appdetails，无 Key；哩米需求 2026-08-12） ──
    # ⚠️ GetAppList 已停用（实测 404）；store 限速严格（并发 8 触发 429）→ 并发 3 + 退避。
    # v0 借鉴：fallback_cc="us" 锁区降级（国区拉不到带美区重试，v0 实测验证）。
    def get_app_metadata(
        self,
        missing_appids: list[str],
        concurrency: int = 3,
        force_refresh: bool = False,
        progress_callback=None,
    ) -> dict[str, dict]:
        """补全完整元数据 {appid: {name, developer, publisher, genres, ...}} 并本地缓存。

        返回本次补全的新映射；progress_callback(done, total) 每完成一个调用一次。
        """
        import concurrent.futures as futures
        import time as _time

        from client.config import APP_DIR

        cache_path = APP_DIR / APP_LIST_CACHE
        cache: dict[str, dict] = {}
        if cache_path.exists() and not force_refresh:
            try:
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass
        todo = [a for a in missing_appids if a not in cache]
        if not todo:
            return {}
        logger.info(f"Store appdetails 补全元数据: {len(todo)} 个（并发 {concurrency}）")

        def _fetch(appid: str) -> tuple[str, dict | None]:
            for attempt in range(3):  # 429 退避重试（1s / 2s）
                try:
                    resp = httpx.get(
                        "https://store.steampowered.com/api/appdetails",
                        params={"appids": appid, "l": "schinese"},
                        timeout=15,
                    )
                    if resp.status_code == 429:
                        _time.sleep(1 + attempt)
                        continue
                    if resp.status_code == 200:
                        info = resp.json().get(appid, {})
                        if info and info.get("success"):
                            return appid, _extract_metadata(appid, info["data"])
                        # 锁区/无国区页 → cc=us 降级重试（v0 实测方案）
                        resp_us = httpx.get(
                            "https://store.steampowered.com/api/appdetails",
                            params={"appids": appid, "l": "schinese", "cc": "us"},
                            timeout=15,
                        )
                        if resp_us.status_code == 200:
                            info_us = resp_us.json().get(appid, {})
                            if info_us and info_us.get("success"):
                                return appid, _extract_metadata(appid, info_us["data"])
                    return appid, None  # 404/无商店页：不重试
                except httpx.HTTPError:
                    _time.sleep(0.5 * (attempt + 1))
            return appid, None

        done_count = [0]
        results: dict[str, dict] = {}

        def _on_done(fut: futures.Future) -> None:
            appid, meta = fut.result()
            if meta:
                results[appid] = meta
            done_count[0] += 1
            if progress_callback:
                progress_callback(done_count[0], len(todo))

        with futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futs = [pool.submit(_fetch, a) for a in todo]
            for f in futs:
                f.add_done_callback(_on_done)
            for f in futs:
                f.result()  # 等待全部完成

        cache.update(results)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        logger.info(f"元数据补全完成: 新增 {len(results)} 个（失败 {len(todo) - len(results)}）")
        return results
