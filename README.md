# 库集 KuJi

库集是一个 Windows 本地 Steam 游戏库分类工具。

- 扫描本机 Steam 游戏库（已安装 + librarycache + Steam Web API 自有游戏）
- 使用内置预分类数据与规则引擎给出分类建议
- 支持手动分类、多选批量分类、厂商视图
- 可选 AI 补全/分类（OpenAI 兼容接口，如 DeepSeek / Gemini）
- 可将分类安全写回 Steam 收藏夹，写回前自动备份，支持恢复

当前版本：v2.2

---

## 环境要求

- Windows 10 / 11
- Python 3.12
- Steam 客户端（写回功能需要）

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scriptsctivate
pip install -e .
python client\main.py
```

开发测试台：

```powershell
.\.venv\Scripts\python.exe -m client.ui.debug_console
```

或双击：

```
启动测试台.bat
```

## 打包

```powershell
.\.venv\Scripts\python.exe toolsuild_exe.py
```

默认使用 Nuitka，生成：

```
dist\main.dist\kuji.exe
```

## 预置数据说明

仓库内的 `client/resources/metadata_prebuilt.db` 是**剥离版**：

- `pre_classified`：1126 条 appid → 分类
- `pre_styles`：488 条 appid → 风格
- `app_type_override`：类型覆盖
- **已移除 `metadata_cache`**：不包含任何个人游戏名字/开发商/发行商等完整元数据

如需完整元数据，可自行运行：

```powershell
.\.venv\Scripts\python.exe toolsuild_metadata.py
```

该脚本需要本机 Steam 路径，并可能访问 Steam Store / Steam Web API。

## AI 功能（可选）

在软件「设置」中填写：

- AI API Key
- AI Base URL（需 OpenAI 兼容接口）
- AI Model

不配置 AI 不影响扫描、手动分类和写回。

AI 调用会把游戏名称和 AppID 发送给你配置的 AI 服务商，并可能产生费用。

## 写回 Steam 注意事项

1. 写回前必须完全退出 Steam
2. 软件会自动备份到 `%APPDATA%\KuJi\.backup`
3. 如果收藏夹出现异常：设置 → 备份与恢复 → 恢复最近一次备份
4. 建议先少量测试，再全量写回

## 反馈

- 反馈 QQ 群：1107903014
- 软件顶栏「反馈」按钮可导出诊断包（自动脱敏，不包含明文 API Key）

## 免责声明

本软件会修改 Steam 收藏夹相关数据，使用前请确认已了解风险。
请务必在写回前关闭 Steam，并保留备份。
因误操作、Steam 客户端更新、云同步冲突等造成的收藏夹异常，
请优先使用「设置 → 备份与恢复」恢复备份。

## License

本项目自有源码采用 **GNU General Public License v3.0**，见仓库根目录：

- `LICENSE`：GPL-3.0 全文
- `THIRD_PARTY_LICENSES.md`：PySide6 / Qt 等第三方组件说明

PySide6 / Qt 6 以 LGPLv3 或商业许可提供，本项目以动态链接方式使用；
分发打包版时请保留相应许可证和第三方声明。
