"""游戏封面图（哩米需求 2026-08-12：竖版封面直观展示预分类）。

数据源：`%Steam%/appcache/librarycache/<appid>/` 下 Steam 客户端图片缓存——
2026-08-12 实测 7361 张：**300×450 竖版封面 918 款**（与 600×900 同比例，哩米所指）、
600×900 仅 1 款、460×215 header 2013 款。
选图优先级：300×450 竖版（哈希文件名）> 600×900 > header.jpg（460×215）。

性能（2026-08-12 修复卡顿）：全量解码 1181 张原图会冻结 UI →
缩略图缓存 %APPDATA%/KuJi/covers/<appid>.jpg（120×180），首次访问逐张生成（~50ms），
之后秒级加载。
"""
import json
import os
import struct
from pathlib import Path

STEAM_PATH = Path(r"C:\Program Files (x86)\Steam")
_CACHE_FILE = "cover_index.json"
THUMB_DIR_NAME = "covers"
THUMB_SIZE = (120, 180)


def _resolve_steam_path() -> Path:
    """Steam 路径解析（2026-08-15：统一走自动检测，不再读取已移除的设置项）。"""
    try:
        from client.utils.steam_path import detect_steam_path

        detected = detect_steam_path()
        if detected:
            return detected
    except Exception:
        pass
    return STEAM_PATH

# 竖版优先（300×450 与 600×900 同比例）；header 兜底
_PREFERRED = [(300, 450), (600, 900)]


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """读 JPEG SOF 段得尺寸（只需文件头 4KB）。"""
    i = 2
    n = len(data)
    while i < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            if i + 9 > n:
                return None
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        if i + 4 > n:
            return None
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def _cache_path() -> Path:
    base = Path(os.environ.get("APPDATA", str(Path.home()))) / "KuJi"
    return base / _CACHE_FILE


def _load_index() -> dict[str, str]:
    p = _cache_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {}


def build_cover_index() -> dict[str, str]:
    """扫描 librarycache 全目录（含子目录，2026-08-12 哩米指出）为每个 appid 选封面。

    结构：<appid>/header.jpg（一层）+ <appid>/<hash>/library_capsule.jpg 等（子目录，
    滚库新增）。优先级：library_capsule（300×450 竖版）> 300×450 哈希 jpg > 600×900
    > header.jpg > library_hero。
    """
    index: dict[str, str] = {}
    lc = _resolve_steam_path() / "appcache" / "librarycache"
    if not lc.is_dir():
        return index
    for d in lc.iterdir():
        if not d.is_dir() or not d.name.isdigit():
            continue
        best: Path | None = None
        header: Path | None = None
        # 子目录内标准命名（滚库新增；哩米 2026-08-12 指出）。
        # 注意语言变体：library_capsule_schinese.jpg 等（_schinese 后缀）同样有效
        for sub in d.iterdir():
            if not sub.is_dir():
                continue
            for f in sub.iterdir():
                if not f.is_file() or f.suffix.lower() != ".jpg":
                    continue
                name = f.name
                if name.startswith("library_capsule") and best is None:
                    best = f
                elif name.startswith("library_hero") and header is None:
                    header = f
                elif name.startswith("library_header") and header is None:
                    header = f
                elif name.startswith("library_logo") and header is None:
                    header = f
        # 一层文件（旧缓存）
        for f in d.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower() != ".jpg":
                continue
            if f.name == "header.jpg":
                if header is None:
                    header = f
                continue
            try:
                size = _jpeg_size(f.read_bytes()[:4096])
            except OSError:
                continue
            if size in _PREFERRED and best is None:
                best = f
            if size == (600, 900) and best is None:
                best = f
        if best is None:
            best = header
        if best is not None:
            index[d.name] = str(best)
    try:
        _cache_path().parent.mkdir(parents=True, exist_ok=True)
        _cache_path().write_text(json.dumps(index), encoding="utf-8")
    except OSError:
        pass
    return index


def get_cover_path(appid: str) -> Path | None:
    """返回 appid 封面路径（竖版优先）；索引未建时构建。"""
    index = _load_index()
    if not index:
        index = build_cover_index()
    path = index.get(appid)
    if path and Path(path).exists():
        return Path(path)
    # 兜底：header.jpg
    header = _resolve_steam_path() / "appcache" / "librarycache" / appid / "header.jpg"
    return header if header.exists() else None


def get_thumb_path(appid: str, generate: bool = True) -> Path | None:
    """缩略图路径（120×180）：存在直接返回。

    generate=False 时只读已有缓存，不从原图生成——避免主线程 UI 卡顿
    （哩米 2026-08-24：游戏列表加载不再实时生成缩略图）。
    """
    thumb = _thumb_dir() / f"{appid}.jpg"
    if thumb.exists():
        return thumb
    if not generate:
        return None
    src = get_cover_path(appid)
    if src is None:
        return None
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPixmap

        pix = QPixmap(str(src)).scaled(
            THUMB_SIZE[0], THUMB_SIZE[1], Qt.KeepAspectRatio, Qt.SmoothTransformation)
        thumb.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(thumb), "JPG", 85)
        return thumb
    except Exception:
        return src  # 生成失败退回原图


def _thumb_dir() -> Path:
    base = Path(os.environ.get("APPDATA", str(Path.home()))) / "KuJi"
    return base / THUMB_DIR_NAME


# CDN 竖版封面 600×900（appdetails library_capsule 字段返回官方 URL）
_ALLOWED_HOSTS = ("steamstatic.com", "steampowered.com", "steamgriddb.com", "sgdbcdn.com")


def _safe_fetch_image(url: str) -> bytes | None:
    """安全下载图片：https 协议 + 域名白名单 + 解析 IP 阻断私网/环回 + 禁重定向。

    （Mimosa SSRF 规则要求；库集为桌面客户端，此项为纵深防御）
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    url = url.replace("http://", "https://", 1)  # SGDB url 字段可能为 http（文档说明）
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return None
    host = parsed.hostname or ""
    if not any(host == h or host.endswith("." + h) for h in _ALLOWED_HOSTS):
        return None
    try:
        for info in socket.getaddrinfo(host, 443):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return None
    except OSError:
        return None
    try:
        import httpx

        resp = httpx.get(url, timeout=20, follow_redirects=False)
        if resp.status_code == 200 and resp.content[:2] == b"\xff\xd8":
            return resp.content
    except Exception:
        return None
    return None


def _sgdb_cover_url(appid: str) -> str | None:
    """SteamGridDB 查 600×900 封面 URL（社区上传，覆盖已下架游戏）。

    免费 key 每 5 分钟 1 万次额度（哩米提供 2026-08-12）。无 key 时返回 None。
    """
    from client.config import get_settings

    key = get_settings().steamgriddb_key
    if not key:
        return None
    try:
        import httpx

        resp = httpx.get(
            f"https://www.steamgriddb.com/api/v2/grids/steam/{appid}",
            params={"dimensions": "600x900"},
            headers={"Authorization": f"Bearer {key}"},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        data = resp.json().get("data") or []
        if not data:
            return None
        # 取评分最高的第一张
        best = max(data, key=lambda g: g.get("score", 0))
        return best.get("url") or None
    except Exception:
        return None


def download_cover(appid: str) -> bool:
    """从 CDN 下载 600×900 竖版封面 → 缩略图缓存。成功返回 True。

    本地 librarycache 未缓存的游戏（家庭共享未渲染）用此补全（哩米需求）。
    流程：appdetails（参数化请求）→ 官方 library_capsule URL → 安全下载。
    """
    try:
        appid = str(int(appid))  # 强类型：仅纯数字（SSRF 防护）
    except (TypeError, ValueError):
        return False
    thumb = _thumb_dir() / f"{appid}.jpg"
    if thumb.exists():
        return True
    try:
        import httpx

        # 1) SteamGridDB 优先（有 key 时；已知缺失游戏命中 ~2s，避免 store 超时拖累）
        content = None
        sgdb_url = _sgdb_cover_url(appid)
        if sgdb_url:
            content = _safe_fetch_image(sgdb_url)

        # 2) appdetails library_capsule（官方 600×900）
        if content is None:
            try:
                resp = httpx.get(
                    "https://store.steampowered.com/api/appdetails",
                    params={"appids": appid}, timeout=15,
                )
                if resp.status_code == 200:
                    info = resp.json().get(appid, {})
                    url = ((info.get("data") or {}).get("library_capsule", "")
                           if info.get("success") else "")
                    if url:
                        content = _safe_fetch_image(url)
            except Exception:
                pass  # store 网络波动 → 继续回退链

        # 3) 回退链：header.jpg → capsule_616x353
        if content is None:
            for fallback in (
                f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg",
                f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/capsule_616x353.jpg",
            ):
                content = _safe_fetch_image(fallback)
                if content is not None:
                    break
        if content is None:
            return False
        thumb.parent.mkdir(parents=True, exist_ok=True)
        tmp = thumb.with_suffix(".tmp")
        tmp.write_bytes(content)
        # 无 QApplication 时 QPixmap 会硬崩溃（segfault 非异常）——直接存原图
        from PySide6.QtGui import QGuiApplication

        if QGuiApplication.instance() is not None:
            try:
                from PySide6.QtCore import Qt
                from PySide6.QtGui import QPixmap

                pix = QPixmap(str(tmp)).scaled(
                    THUMB_SIZE[0], THUMB_SIZE[1], Qt.KeepAspectRatio, Qt.SmoothTransformation)
                pix.save(str(thumb), "JPG", 85)
            except Exception:
                tmp.replace(thumb)  # 缩放失败存原图
        else:
            tmp.replace(thumb)  # 无 GUI 上下文：存原图（UI 展示时 QIcon 自行缩放）
        tmp.unlink(missing_ok=True)
        return thumb.exists()
    except Exception:
        return False


def has_cover(appid: str) -> bool:
    return get_cover_path(appid) is not None
