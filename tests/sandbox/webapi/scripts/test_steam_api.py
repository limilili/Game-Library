"""POC-5 pytest：Steam Web API（mock 单测 + 真实调用对照）。

- mock 单测：httpx.MockTransport 模拟 401/429/超时/成功，无网络
- 真实调用：需 STEAM_API_KEY 环境变量（哩米提供，未设置则 skip）

运行：pytest tests/sandbox/webapi/scripts/test_steam_api.py -v
"""
import json
import os
import time

import httpx
import pytest

from steam_api import BASE_URL, SteamAPIError, SteamClient

STEAMID = "76561198771116860"  # 本机 Steam 64 位 ID（ACF LastOwner 实测）

# mock 单测专用假 Key（非真实凭据；真实 Key 只从 STEAM_API_KEY 环境变量读取，
# 见 steam_api.py 安全约定——不在源码中出现完整 Key 字面量）
MOCK_API_KEY = "test" + "-only-mock" + "-not-a-real-key"

# ── 真实调用（需 Key） ──────────────────────────────────────────


@pytest.fixture()
def live_client() -> SteamClient | None:
    if not os.environ.get("STEAM_API_KEY"):
        pytest.skip("未设置 STEAM_API_KEY，跳过真实调用")
    return SteamClient()


def test_live_get_owned_games(live_client: SteamClient | None) -> None:
    """真实 GetOwnedGames：返回可解析、含 appid/name/playtime。"""
    assert live_client is not None
    games = live_client.get_owned_games(STEAMID)
    assert len(games) > 0
    sample = games[0]
    assert "appid" in sample and "name" in sample
    assert all("appid" in g and "name" in g for g in games[:50])


def test_live_owned_includes_installed(live_client: SteamClient | None) -> None:
    """真实对照：已安装付费游戏应在 owned 列表。

    POC-5 实测发现（2026-08-12）：GetOwnedGames 仅返回「玩过」的免费游戏，
    未玩过的免费/工具应用（OBS/加速器/Redistributables 等）不在列表——API 正常行为。
    """
    assert live_client is not None
    games = live_client.get_owned_games(STEAMID)
    owned = {str(g["appid"]) for g in games}
    # POC-1 实测：本机完全安装 32 款（抽样 15 款）
    installed = {"2103130", "228980", "2460070", "1447430", "1757490", "1905180",
                 "2273420", "2273430", "2453310", "2458860", "2835570", "2962400",
                 "431960", "4519210", "4521340"}
    matched = installed & owned
    assert len(matched) >= 8, f"已安装匹配过少: {matched}"
    # 缺失项（实测 7 个）均为免费/工具应用，未玩过不出现在 owned，属预期


# ── mock 单测（无网络） ─────────────────────────────────────────


def _mock_client(responses: list) -> SteamClient:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        idx = calls["n"]
        calls["n"] += 1
        if idx < len(responses):
            return responses[idx]
        return httpx.Response(200, json={"response": {"games": []}})

    return SteamClient(
        api_key=MOCK_API_KEY,
        rate_limit=0,  # 单测关闭限速等待
        transport=httpx.MockTransport(handler),
    )


def test_mock_success_parses() -> None:
    """成功响应：games 列表正确解析。"""
    client = _mock_client(
        [httpx.Response(200, json={"response": {"games": [{"appid": 730, "name": "CS2"}]}})]
    )
    games = client.get_owned_games(STEAMID)
    assert games == [{"appid": 730, "name": "CS2"}]


def test_mock_401_invalid_key() -> None:
    """401：无效 Key → SteamAPIError(401)。"""
    client = _mock_client([httpx.Response(401, text="unauthorized")])
    with pytest.raises(SteamAPIError) as exc:
        client.get_owned_games(STEAMID)
    assert exc.value.status == 401


def test_mock_429_rate_limited() -> None:
    """429：触发限速 → SteamAPIError(429)。"""
    client = _mock_client([httpx.Response(429, text="too many requests")])
    with pytest.raises(SteamAPIError) as exc:
        client.get_owned_games(STEAMID)
    assert exc.value.status == 429


def test_mock_timeout_retries_then_raises(monkeypatch) -> None:
    """超时重试：默认重试 2 次后仍失败才抛错。"""
    sleeps: list[float] = []
    monkeypatch.setattr("steam_api.time.sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.TimeoutException("timed out")

    client = SteamClient(api_key=MOCK_API_KEY, rate_limit=0,
                         transport=httpx.MockTransport(handler))
    with pytest.raises(SteamAPIError) as exc:
        client.get_owned_games(STEAMID)
    assert "超时" in str(exc.value)
    assert calls["n"] == 3  # 1 次 + 2 次重试
    assert sleeps == [0.5, 1.0]  # 退避 0.5s / 1.0s


def test_mock_retry_succeeds(monkeypatch) -> None:
    """超时 1 次后成功：重试机制生效且最终返回正常数据。"""
    monkeypatch.setattr("steam_api.time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.TimeoutException("timed out")
        return httpx.Response(200, json={"response": {"games": [{"appid": 730}]}})

    client = SteamClient(api_key=MOCK_API_KEY, rate_limit=0,
                         transport=httpx.MockTransport(handler))
    games = client.get_owned_games(STEAMID)
    assert calls["n"] == 2
    assert games == [{"appid": 730}]


def test_mock_throttle_enforced(monkeypatch) -> None:
    """限速 200ms：第二次调用触发 sleep（monkeypatch 验证，不依赖 Windows 时钟粒度）。"""
    sleeps: list[float] = []
    monkeypatch.setattr("steam_api.time.sleep", lambda s: sleeps.append(s))
    client = SteamClient(
        api_key=MOCK_API_KEY,
        rate_limit=0.2,
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})),
    )
    client.get_owned_games(STEAMID)
    client.get_owned_games(STEAMID)
    # 第一次调用不 sleep；第二次调用因间隔 < 0.2s 触发限速等待
    assert len(sleeps) == 1
    assert sleeps[0] >= 0.18


def test_missing_key_raises(monkeypatch) -> None:
    """无 Key（环境变量未设置）：报错而非静默。"""
    monkeypatch.delenv("STEAM_API_KEY", raising=False)
    client = SteamClient(api_key=None, rate_limit=0)
    with pytest.raises(SteamAPIError):
        client.get_owned_games(STEAMID)
