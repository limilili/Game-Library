"""Steam 安装路径检测（M1.1）：注册表 HKCU\\Software\\Valve\\Steam 的 SteamPath + 常见位置兜底。"""
import winreg
from pathlib import Path

from loguru import logger

_COMMON_PATHS = [
    Path(r"C:\Program Files (x86)\Steam"),
    Path(r"C:\Program Files\Steam"),
    Path(r"D:\Steam"),
    Path(r"D:\SteamLibrary\..\Steam"),
]


def detect_steam_path() -> Path | None:
    """检测 Steam 安装路径。

    优先注册表 SteamPath（POC-7 已确认本机存在）；失败降级常见路径；
    再失败返回 None（由 UI 引导用户手动选择，PRD M1.1 降级策略）。
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "SteamPath")
            path = Path(str(value))
            if (path / "steam.exe").exists():
                logger.debug(f"注册表检测到 Steam: {path}")
                return path
            logger.warning(f"注册表 SteamPath 存在但 steam.exe 缺失: {path}")
    except OSError as e:
        logger.debug(f"注册表读取失败: {e}")

    for candidate in _COMMON_PATHS:
        if (candidate / "steam.exe").exists():
            logger.debug(f"常见路径兜底: {candidate}")
            return candidate

    logger.warning("未检测到 Steam 安装路径")
    return None
