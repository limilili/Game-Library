"""反馈诊断包导出（哩米 2026-08-24）：一键收集日志/系统信息/脱敏配置。

用户点击主窗口「反馈」后，生成 zip 到桌面，方便连同截图一起发给开发者。
不含 kuji.db 和明文 API Key。
"""
import os
import platform
import sys
import zipfile
from datetime import datetime
from pathlib import Path

# 发布前把这里改成你的反馈群号 / 邮箱 / 表单链接
FEEDBACK_CONTACT = "1107903014"

APP_VERSION = "v2.2"
MAX_LOG_BYTES = 5 * 1024 * 1024  # 每个日志文件最多收集末尾 5MB


def _kuji_dir() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "KuJi"


def _read_tail(path: Path, max_bytes: int = MAX_LOG_BYTES) -> bytes:
    """读取文件末尾内容（避免把超大日志整体塞进反馈包）。"""
    if not path.is_file():
        return b""
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
        return f.read()


def _sanitize_config() -> str:
    """导出脱敏配置；Key / SteamID / 代理一律不写原值。"""
    try:
        from client.config import get_settings

        data = get_settings().model_dump(exclude={"db_path"})
    except Exception as e:
        return f"# 读取配置失败: {e}\n"
    sensitive = {
        "steam_api_key_enc", "steamgriddb_key_enc", "ai_api_key_enc",
        "steam_id", "proxy",
    }
    for key in list(data):
        if key in sensitive:
            data[key] = "***（已配置）" if data.get(key) else ""
    try:
        import yaml
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    except Exception:
        return "\n".join(f"{k}: {v!r}" for k, v in data.items())


def _system_info() -> str:
    lines = [
        "=== 库集反馈诊断信息 ===",
        f"生成时间: {datetime.now().isoformat(timespec='seconds')}",
        f"软件版本: {APP_VERSION}",
        f"打包模式: {'是' if getattr(sys, 'frozen', False) or '__compiled__' in globals() else '否（源码运行）'}",
        f"Windows: {platform.platform()}",
        f"Python: {sys.version.split()[0]}",
        f"工作目录: {os.getcwd()}",
    ]
    try:
        from client.utils.steam_path import detect_steam_path
        lines.append(f"Steam 路径: {detect_steam_path() or '未检测到'}")
    except Exception as e:
        lines.append(f"Steam 路径检测失败: {e}")
    try:
        from client.config import get_settings
        settings = get_settings()
        lines.append(f"数据库路径: {settings.resolved_db_path}")
        lines.append(f"Steam Web API Key: {'已配置' if settings.steam_api_key_enc else '未配置'}")
        lines.append(f"SteamGridDB Key: {'已配置' if settings.steamgriddb_key_enc else '未配置'}")
        lines.append(f"AI API Key: {'已配置' if settings.ai_api_key_enc else '未配置'}")
        lines.append(f"AI Base URL: {'已配置' if settings.ai_base_url else '未配置'}")
        lines.append(f"AI Model: {'已配置' if settings.ai_model else '未配置'}")
        lines.append(f"Steam ID: {'已配置' if settings.steam_id else '未配置'}")
        lines.append(f"代理: {'已配置' if settings.proxy else '未配置'}")
    except Exception as e:
        lines.append(f"配置读取失败: {e}")
    logs_dir = _kuji_dir() / "logs"
    lines.append(f"日志目录: {logs_dir}")
    if logs_dir.is_dir():
        for f in sorted(logs_dir.glob("*.log")):
            lines.append(f"  - {f.name}: {f.stat().st_size} bytes")
    return "\n".join(lines) + "\n"


def _readme() -> str:
    return f"""库集反馈包
============

反馈 QQ 群：{FEEDBACK_CONTACT}

请把本 zip 和你遇到的问题一起发到群里，并尽量补充：
1. 问题描述（发生了什么）
2. 复现步骤（点击了什么、顺序是什么）
3. 截图或录屏
4. 出问题前在做什么

本包内容：
- system_info.txt：系统与配置状态（不含密钥明文）
- config_sanitized.yaml：脱敏后的设置
- coverage.txt：预置数据覆盖率统计
- logs/：最近的应用日志

注意：本反馈包不包含 kuji.db，也不包含明文 API Key。
"""


def export_feedback(output_dir: Path | None = None) -> Path:
    """生成反馈 zip，返回文件路径。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if output_dir is None:
        desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
        output_dir = desktop if desktop.is_dir() else _kuji_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / f"KuJi_feedback_{timestamp}.zip"

    logs_dir = _kuji_dir() / "logs"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", _readme())
        zf.writestr("system_info.txt", _system_info())
        zf.writestr("config_sanitized.yaml", _sanitize_config())
        try:
            from client.core.coverage import format_coverage_report

            zf.writestr("coverage.txt", format_coverage_report())
        except Exception as e:
            zf.writestr("coverage.txt", f"覆盖率统计失败: {e}\n")
        for name in ("all.log", "error.log"):
            data = _read_tail(logs_dir / name)
            if data:
                zf.writestr(f"logs/{name}", data)
        # 其他轮转日志也带上（最多再选 3 个）
        if logs_dir.is_dir():
            extras = sorted(
                [p for p in logs_dir.glob("*.log*") if p.name not in ("all.log", "error.log")],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:3]
            for p in extras:
                data = _read_tail(p)
                if data:
                    zf.writestr(f"logs/{p.name}", data)
    return zip_path
