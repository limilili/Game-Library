# 沙盒 Steam Cloud 状态检测（POC-7）

> 对应 PRD §12 Phase 0 POC-7（Cloud 状态检测；无法检测则降级为启动时统一告知）。
> 实测日期：2026-08-12，哩米实机（账号 12345678）。

## 结论

| 检测项 | 结果 | 方法 |
|---|---|---|
| 云同步**启用过** | ✅ 可检测 | `userdata/<id>/config/cloudstorage/` 存在 + `cloud-storage-namespaces.json` 有 namespace 版本记录（本机 `[[3,"6"],[1,"2392"]]`） |
| 每游戏同步状态 | ✅ 可检测 | localconfig.vdf `apps → <appid> → cloud → last_sync_state`（本机 81 synchronized + 1 changesincloud） |
| 待上传修改 | ✅ 可检测 | `cloud-storage-namespace-*.modified.json` 存在（本机 2 个） |
| **全局「启用 Steam 云同步」开关** | ❌ **不可检测** | 本机 localconfig.vdf 顶层 / `Steam\config\config.vdf` / 注册表 `HKCU\Software\Valve\Steam` / registry.vdf（不存在）均无可靠键位 |

## 处置（PRD 降级方案确认）

全局开关不可检测 → 按 PRD POC-7 预设降级：**启动时统一告知 Steam Cloud 风险**（PRD §5.4.2「Steam Cloud 风险告知，不根治只告知」）。
per-app 状态与 namespace 版本可作 Phase 1 写入前的辅助判断（如检测到大量 changesincloud/.modified.json 时提示用户先启动 Steam 同步）。

## 运行

```bash
python tests/sandbox/cloudstatus/probe_cloudstatus.py   # 只读探查
```
