"""POC-5 Steam Web API 客户端：GetOwnedGames + GetAppList（httpx，200ms 限速，错误处理）。

安全约定：API Key **只从环境变量 STEAM_API_KEY 读取**，绝不写入代码/文档/仓库；
无效/缺失 Key 时报错或跳过真实调用（测试用 mock）。

用法：
    set STEAM_API_KEY=xxx  （Windows）或 export STEAM_API_KEY=xxx
    python analyze_real.py          # 真实调用 + 与本地库对照
    pytest test_steam_api.py -v     # mock 单测 + 真实调用（有 Key 才跑）
"""
import os
import time
from typing import Any

import httpx

BASE_URL = "https://api.steampowered.com"
# PRD 约定：Steam API 200ms 间隔限速
DEFAULT_RATE_LIMIT = 0.2


class SteamAPIError(Exception):
    """Steam Web API 错误（统一错误码：401 无效Key / 429 限速 / 超时等）。"""

    def __init__(self, status: int | None, detail: str) -> None:
        self.status = status
        super().__init__(f"SteamAPIError[{status}]: {detail}")


def get_api_key() -> str:
    key = os.environ.get("STEAM_API_KEY", "")
    if not key:
        raise SteamAPIError(None, "未设置 STEAM_API_KEY 环境变量")
    return key


class SteamClient:
    """Steam Web API 客户端（同步 httpx，带 200ms 限速 + 超时/网络错误重试）。

    POC-5 实测（2026-08-12）：国内网络访问 api.steampowered.com 延迟波动大
    （3~10s+，偶发接近超时）→ 必须重试；Phase 1 另需可选代理（v0 已有先例）。
    401/429 等业务错误不重试。
    """

    def __init__(
        self,
        api_key: str | None = None,
        rate_limit: float = DEFAULT_RATE_LIMIT,
        timeout: float = 15.0,
        retries: int = 2,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("STEAM_API_KEY", "")
        self.rate_limit = rate_limit
        self.retries = retries
        self._last_call = 0.0
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,  # mock 注入点（pytest）
        )

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_call = time.monotonic()

    def _call(self, interface: str, method: str, params: dict[str, Any]) -> dict:
        if not self.api_key:
            raise SteamAPIError(None, "未设置 STEAM_API_KEY 环境变量")
        self._throttle()
        query = {"key": self.api_key, "format": "json", **params}
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self._client.get(f"{BASE_URL}/{interface}/{method}/v0001/", params=query)
                break
            except httpx.TimeoutException as e:
                last_err = SteamAPIError(None, "请求超时")
                if attempt == self.retries:
                    raise last_err from e
                time.sleep(0.5 * (attempt + 1))  # 指数退避
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

    def get_owned_games(
        self, steamid: str, include_appinfo: bool = True, include_played_free: bool = True
    ) -> list[dict]:
        """GetOwnedGames：用户拥有的全部游戏（含免费游玩）。"""
        data = self._call(
            "IPlayerService",
            "GetOwnedGames",
            {
                "steamid": steamid,
                "include_appinfo": 1 if include_appinfo else 0,
                "include_played_free_games": 1 if include_played_free else 0,
            },
        )
        return (data.get("response", {}) or {}).get("games", [])

    def get_app_list(self) -> list[dict]:
        """GetAppList：Steam 全部应用列表（无 Key 也可用，限速仍生效）。"""
        data = self._call("ISteamApps", "GetAppList", {})
        return (data.get("applist", {}) or {}).get("apps", [])
