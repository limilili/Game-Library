# 沙盒 Steam Web API（POC-5）

> 对应 PRD §12 Phase 0 POC-5（Steam Web API 调用，M1.5 可选 Key 完整库扫描基础）。
> 实测日期：2026-08-12，哩米提供测试 Key。

## 目录结构

```
webapi/
├── scripts/
│   ├── steam_api.py       # httpx 客户端：GetOwnedGames/GetAppList，200ms 限速 + 超时重试
│   ├── test_steam_api.py  # pytest（7 mock + 2 真实，需 Key 才跑）
│   └── analyze_real.py    # 真实对照：owned vs 本地已安装库（复用 POC-1 解析）
└── README.md
```

## ⚠️ 安全约定（铁律）

- **API Key 只从环境变量 `STEAM_API_KEY` 读取**，绝不写入代码/文档/仓库
- 运行真实调用：`STEAM_API_KEY=xxx python analyze_real.py`（单次命令内联，不落盘）
- 未设置 Key 时真实测试自动 skip，mock 单测无网络可跑

## 实测结论（2026-08-12）

| 结论 | 详情 |
|---|---|
| GetOwnedGames 可用 | owned 总数 115（含玩过的免费游戏），返回结构 `{appid, name, playtime_*}` |
| 本地已安装对照 | 32 款中 22 款在 owned；**缺失 10 款全部为免费未玩/工具/Demo**（OBS/加速器/Redistributables 等）——GetOwnedGames 仅返回玩过的免费游戏，**API 正常行为** |
| ⚠️ 国内网络延迟波动大 | 实测 3~10s+ 波动、偶发接近 15s 超时 → 客户端**必须重试**（已实现：默认 2 次 + 指数退避）+ Phase 1 需**可选代理**（v0 已有先例） |
| 限速 200ms | 已实现（PRD 约定），monkeypatch 精确验证 |
| 错误处理 | 401（无效 Key）/429（限速）/超时/网络错误 → 统一 `SteamAPIError`（带状态码），4xx 不重试 |

## Phase 1 落位

- `client/api/steam_api.py` 复用本客户端（改 httpx.AsyncClient + qasync，PRD §8.3）
- M1.5 完整库扫描 = GetOwnedGames 全量对照本地库（本沙盒 analyze_real.py 即雏形）
- 代理支持：v0 steam_community/steam_store 有 proxy 参数先例，Phase 1 补回
