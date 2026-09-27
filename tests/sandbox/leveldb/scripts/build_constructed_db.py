"""POC-2 步骤2：构造沙盒 LevelDB（预知数据）。

模拟 Steam sharedconfig.vdf（LevelDB 二进制库）的假设键结构：
    UserRoamingConfigStore/Software/Valve/Steam/Users/{account_id}/Tags/{appid}/TagName
值：UTF-8 编码的标签名（含中文/英文）。

⚠️ 键结构为**假设格式**（参考 Repressurizer 路径拼接键），真实结构以步骤 4
真实 sharedconfig.vdf dump 归纳为准，届时按真实格式校正提取逻辑。

用法：python build_constructed_db.py
"""
import os
from pathlib import Path

import plyvel

# ⚠️ 数据目录必须在 ASCII 路径：plyvel(LevelDB Windows ANSI API) 无法打开中文路径
# （2026-08-10 实测，见 temp/docs/POC-2-LevelDB-失败记录.md 条目 2）
# 2026-08-11 项目更名 ASCII 路径 Game library，数据目录默认在沙盒内 data/（gitignore）；
# 其他环境可用 KUJI_POC_DATA 环境变量覆盖
SANDBOX = Path(__file__).resolve().parent.parent
DATA_ROOT = Path(os.environ.get("KUJI_POC_DATA", SANDBOX / "data"))
DB_PATH = DATA_ROOT / "constructed"

# 预知数据：(account_id, appid) -> 标签列表（每个键一个标签，可多键）
KNOWN_DATA = {
    "1234567890": {  # 账号 1
        570: ["MOBA"],  # Dota 2
        730: ["FPS"],  # CS2
        1085660: ["RPG"],  # Destiny 2
        1245620: ["魂系"],  # Elden Ring（中文标签）
    },
    "2987654321": {  # 账号 2
        570: ["电竞"],
        730: ["射击"],
    },
}

# 非 Tags 干扰键：验证提取逻辑只匹配 Tags 路径、忽略其他块
EXTRA_KEYS = [
    (b"UserRoamingConfigStore/Software/Valve/Steam/Users/1234567890/Apps/570/OverlayAppEnable", b"\x01"),
    (b"UserRoamingConfigStore/Software/Valve/Steam/Users/1234567890/SteamCloud/Enabled", b"\x01"),
]


def build() -> None:
    import shutil

    if DB_PATH.exists():
        shutil.rmtree(DB_PATH)
    DB_PATH.mkdir(parents=True)

    db = plyvel.DB(DB_PATH.as_posix(), create_if_missing=True)
    try:
        for account, apps in KNOWN_DATA.items():
            for appid, tags in apps.items():
                key = (
                    f"UserRoamingConfigStore/Software/Valve/Steam/Users/"
                    f"{account}/Tags/{appid}/TagName"
                )
                for tag in tags:
                    db.put(key.encode("utf-8"), tag.encode("utf-8"))
        for k, v in EXTRA_KEYS:
            db.put(k, v)
        tag_count = sum(
            len(tags) for apps in KNOWN_DATA.values() for tags in apps.values()
        )
        print(f"写入完成：{len(KNOWN_DATA)} 个账号、{tag_count} 条标签、{len(EXTRA_KEYS)} 个干扰键")
        print(f"数据库路径：{DB_PATH}")
    finally:
        db.close()


if __name__ == "__main__":
    build()
