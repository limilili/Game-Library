"""补抓无封面游戏的封面（哩米需求 2026-08-14）。

数据源：Steam CDN 公开封面（无需 key）——
- 竖版优先：https://cdn.akamai.steamstatic.com/steam/apps/{appid}/library_600x900_2x.jpg
- 兜底：header.jpg（460×215）

下载到 Steam librarycache/<appid>/ 下（Steam 客户端同目录，cover_art 索引
按文件尺寸识别竖版/横版，文件名不影响）→ 重建封面索引 + 缩略图。

跳过非游戏（tool/config/music/dlc 等——GUI「仅游戏」本来就不显示）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from loguru import logger  # noqa: E402

from client.core.cover_art import (  # noqa: E402
    _resolve_steam_path, build_cover_index, get_cover_path, _thumb_dir,
)

CDN = "https://cdn.akamai.steamstatic.com/steam/apps/{appid}/{name}"
_PLAYABLE = ("game", "demo", "unknown", "delisted")


def main() -> None:
    from client.models import Game
    from client.storage.database import get_session

    steam_path = _resolve_steam_path()
    client = httpx.Client(timeout=20, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"})
    session = get_session()
    try:
        games = session.query(Game).filter(
            Game.del_flag.is_(False), Game.app_type.in_(_PLAYABLE)).all()
        missing = [g for g in games if not get_cover_path(g.appid)]
    finally:
        session.close()
    logger.info(f"无封面可下载 {len(missing)} 款（{len(games)} 款可显示）")

    ok = fail = 0
    for g in missing:
        lib_dir = steam_path / "appcache" / "librarycache" / g.appid
        lib_dir.mkdir(parents=True, exist_ok=True)
        # 2026-08-14 修复：统一命名 header.jpg——横版命中 get_cover_path 的
        # header 兜底；竖版文件也会被 build_cover_index 尺寸识别（双赢）
        target = lib_dir / "header.jpg"
        if target.exists() and target.stat().st_size > 1000:
            ok += 1
            continue
        # 竖版优先，header 兜底；2026-08-14 增强：新游戏走 store_item_assets
        # 新 CDN 节点 + library_capsule（本地缓存同款竖版）+ appdetails API 兜底
        got = False
        candidates = [
            f"https://cdn.akamai.steamstatic.com/steam/apps/{g.appid}/library_600x900_2x.jpg",
            f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{g.appid}/library_600x900_2x.jpg",
            f"https://cdn.akamai.steamstatic.com/steam/apps/{g.appid}/library_capsule_schinese.jpg",
            f"https://cdn.akamai.steamstatic.com/steam/apps/{g.appid}/header.jpg",
        ]
        for url in candidates:
            try:
                r = client.get(url)
                if r.status_code == 200 and len(r.content) > 1000:
                    target.write_bytes(r.content)
                    got = True
                    break
            except httpx.HTTPError:
                continue
        if not got:
            # appdetails API 拿官方 header_image（build_metadata 同源）
            try:
                r = client.get(
                    f"https://store.steampowered.com/api/appdetails?appids={g.appid}"
                    f"&cc=us&l=schinese")
                d = r.json().get(str(g.appid), {}).get("data", {})
                img = d.get("header_image")
                if img:
                    r = client.get(img)
                    if r.status_code == 200 and len(r.content) > 1000:
                        target.write_bytes(r.content)
                        got = True
            except (httpx.HTTPError, ValueError):
                pass
        if got:
            ok += 1
        else:
            fail += 1
            logger.warning(f"{g.appid} {g.name} 无封面（商店可能已下架）")
    logger.info(f"封面补抓完成: 成功 {ok} / 失败 {fail}")

    # 重建索引 + 缩略图
    build_cover_index()
    thumb_dir = _thumb_dir()
    from client.core.cover_art import get_thumb_path
    from PySide6.QtGui import QPixmap, QImage
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    rebuilt = 0
    for g in missing:
        path = get_cover_path(g.appid)
        if not path:
            continue
        thumb = get_thumb_path(g.appid)
        if thumb and thumb.exists():
            continue
        pix = QPixmap(str(path))
        if pix.isNull():
            continue
        scaled = pix.scaled(120, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        thumb_dir.mkdir(parents=True, exist_ok=True)
        scaled.save(str(thumb), "JPG", 85)
        rebuilt += 1
    logger.info(f"缩略图重建 {rebuilt} 张")
    logger.info(f"最终仍有 {sum(1 for g in missing if not get_cover_path(g.appid))} 款无封面")


if __name__ == "__main__":
    main()
