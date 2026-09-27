"""打包脚本（v2，2026-08-14）：Nuitka 优先 / PyInstaller 备选，onedir 形态。

⚠️ Nuitka 必须在纯 ASCII 路径下运行（v0 实测：中文路径 Dependency Walker
8.3 短路径乱码 → Nuitka assert 崩溃）。项目当前路径含空格但 ASCII，
若遇崩溃请复制源码到 C:\\kuji-a 编译（v0 已验证方案）。

用法（项目根目录、.venv 环境）：
  python tools/build_exe.py                  # Nuitka（失败自动降级 PyInstaller）
  python tools/build_exe.py --pyinstaller    # 强制 PyInstaller
  python tools/build_exe.py --no-fallback    # Nuitka 失败不降级（调试）

产物：
  Nuitka      → dist/main.dist/kuji.exe（打包完成后复制资源进产物目录）
  PyInstaller → dist/kuji/kuji.exe（--collect-all 收集）

v2 资源清单（与运行时路径对应；打包即含完整元数据/分类，扫描即可用，
不依赖 API key/网络）：
  - client/resources/metadata_prebuilt.db  （预置分类 1126 + 元数据 1187，只读）
  - client/resources/studio_profiles.json  （厂商画像）
  - client/resources/rules_default.json    （默认规则）
  - docs/category-tree-default.json        （分类树，scheme_service.DEFAULT_TREE）
  - client/storage/migrations/ + alembic.ini（启动建表迁移）

安全说明：所有 subprocess 调用使用参数列表 + shell=False（无 shell 拼接）；
资源通过 shutil 复制而非命令行 option（规避路径转义/注入面）。
"""
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
MAIN_SCRIPT = ROOT / "client" / "main.py"
NUITKA_DIST = ROOT / "dist" / f"{MAIN_SCRIPT.stem}.dist"
PYINSTALLER_DIST = ROOT / "dist" / "kuji"

# 随包分发资源（src → 产物内相对路径；均为项目内常量路径）
RESOURCE_COPY = [
    (ROOT / "client" / "resources" / "metadata_prebuilt.db", "client/resources/metadata_prebuilt.db"),
    (ROOT / "client" / "resources" / "studio_profiles.json", "client/resources/studio_profiles.json"),
    (ROOT / "client" / "resources" / "rules_default.json", "client/resources/rules_default.json"),
    (ROOT / "docs" / "category-tree-default.json", "docs/category-tree-default.json"),
    (ROOT / "docs" / "用户使用说明.txt", "使用说明.txt"),
    (ROOT / "client" / "storage" / "migrations", "client/storage/migrations"),
    (ROOT / "alembic.ini", "alembic.ini"),
]


def _is_installed(module: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(module) is not None


def _pip_install(package: str) -> None:
    """幂等安装单个打包工具（参数列表，无 shell）。"""
    if _is_installed(package):
        print(f"依赖已就绪: {package}")
        return
    print(f"安装打包依赖: {package}")
    code = subprocess.run(
        [str(VENV_PY), "-m", "pip", "install", package],
        shell=False, timeout=900).returncode
    if code != 0:
        raise RuntimeError(f"pip 安装失败: {package}")


def build_nuitka() -> Path:
    """Nuitka 打包（参数全字面量；资源不经过命令行，打包后复制）。

    --include-module=logging.config/handlers：alembic 迁移 env.py 动态
    `from logging.config import fileConfig`——Nuitka 静态分析漏标准库
    隐式导入（2026-08-14 实测：打包产物启动即 ModuleNotFoundError）。
    """
    _pip_install("nuitka")
    code = subprocess.run(
        [
            str(VENV_PY), "-m", "nuitka",
            "--standalone",
            "--enable-plugin=pyside6",
            "--windows-console-mode=disable",
            "--output-dir=dist",
            "--output-filename=kuji.exe",
            "--assume-yes-for-downloads",
            "--include-module=logging.config",
            "--include-module=logging.handlers",
            str(MAIN_SCRIPT),
        ],
        shell=False, timeout=3600).returncode
    if code != 0:
        raise RuntimeError(f"Nuitka 失败 exit={code}")
    return NUITKA_DIST


def build_pyinstaller() -> Path:
    """PyInstaller 备选（--collect-all 收集 package data）。"""
    _pip_install("pyinstaller")
    code = subprocess.run(
        [
            str(VENV_PY), "-m", "PyInstaller",
            "--noconfirm", "--onefile",
            "--name", "kuji",
            "--collect-all", "client",
            "--collect-all", "alembic",
            str(MAIN_SCRIPT),
        ],
        shell=False, timeout=3600).returncode
    if code != 0:
        raise RuntimeError(f"PyInstaller 失败 exit={code}")
    return PYINSTALLER_DIST


def _strip_prebuilt(src: Path, dst: Path) -> None:
    """生成剥离版预置库（哩米 2026-08-15：隐私——metadata_cache 含打包者
    完整游戏元数据（名字/开发商/R18 标记），其他用户解包即可解析出游戏清单。

    剥离后只保留：pre_classified（appid→分类）/pre_styles（appid→风格）/
    app_type_override——分类功能照常（对方游戏按 appid 匹配）；
    对方游戏名字来自本机 Steam（acf/渲染缓存），开发商可「离线补全」。
    """
    import sqlite3 as _sqlite3

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    conn = _sqlite3.connect(dst)
    try:
        conn.execute("DELETE FROM metadata_cache")
        conn.commit()
    finally:
        conn.close()
    print("  [隐私] metadata_cache 已剥离（1187 款元数据不随包）")


def copy_resources(dist_dir: Path) -> None:
    """资源复制进产物目录（exe 旁同相对路径，与运行时 Path(__file__) 解析一致）。"""
    for src, dst in RESOURCE_COPY:
        target = dist_dir / dst
        if src.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(src, target)
        elif src.name == "metadata_prebuilt.db":
            _strip_prebuilt(src, target)  # 隐私剥离版
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
        print(f"  [资源] {dst}")


def verify(dist_dir: Path) -> bool:
    """产物资源验证：关键资源必须存在（缺失 = 运行时崩）。"""
    exe = dist_dir / "kuji.exe"
    if not exe.exists():
        print(f"[验证] ✗ exe 缺失: {exe}")
        return False
    ok = True
    for _, dst in RESOURCE_COPY:
        if not (dist_dir / dst).exists():
            print(f"[验证] ✗ 资源缺失: {dst}")
            ok = False
    size_mb = exe.stat().st_size // 1024 // 1024
    print(f"[验证] {'✓' if ok else '✗'} 资源完整，exe={exe}（{size_mb} MB）")
    return ok


def main() -> None:
    force_py = "--pyinstaller" in sys.argv
    no_fallback = "--no-fallback" in sys.argv
    try:
        dist = build_pyinstaller() if force_py else build_nuitka()
    except RuntimeError as e:
        if no_fallback:
            raise
        print(f"{e}\n→ 降级 PyInstaller")
        dist = build_pyinstaller()
    copy_resources(dist)
    if not verify(dist):
        raise SystemExit("资源验证失败，请检查 RESOURCE_COPY")
    print(f"\n打包完成: {dist}\n"
          "验收：双击 dist/kuji/kuji.exe 实跑（首次启动建 %APPDATA%/KuJi 数据库，"
          "扫描即可分类——元数据/分类已内置，无需 API key）。")


if __name__ == "__main__":
    main()
