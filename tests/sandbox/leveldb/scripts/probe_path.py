"""探针：验证 plyvel(plyvel-ci) 在 Windows 上对 ASCII/中文路径的打开行为。"""
import os
import shutil
import tempfile
from pathlib import Path

import plyvel

# 实验1：纯 ASCII 路径（正斜杠）
ascii_path = (Path(tempfile.gettempdir()) / "kuji_poc_probe").as_posix()
shutil.rmtree(ascii_path, ignore_errors=True)
try:
    db = plyvel.DB(ascii_path, create_if_missing=True)
    db.put(b"k", b"v")
    db.close()
    db = plyvel.DB(ascii_path, create_if_missing=False)
    print("[1] ASCII 路径 打开+写入+重读:", ascii_path, "->", db.get(b"k"))
    db.close()
except Exception as e:
    print("[1] ASCII 路径 失败:", repr(e))
shutil.rmtree(ascii_path, ignore_errors=True)

# 实验2：中文路径（正斜杠）
cn_path = (Path(os.getcwd()) / "tests" / "sandbox" / "leveldb" / "constructed").as_posix()
shutil.rmtree(cn_path, ignore_errors=True)
os.makedirs(cn_path, exist_ok=True)
try:
    db = plyvel.DB(cn_path, create_if_missing=True)
    db.put(b"k", b"v")
    db.close()
    print("[2] 中文路径(str) 成功")
except Exception as e:
    print("[2] 中文路径(str) 失败:", repr(e))

# 实验3：中文路径传 bytes（UTF-8 编码）
try:
    db = plyvel.DB(cn_path.encode("utf-8"), create_if_missing=False)
    print("[3] 中文路径(bytes-UTF8) 打开成功, 读取:", db.get(b"k"))
    db.close()
except Exception as e:
    print("[3] 中文路径(bytes-UTF8) 失败:", repr(e))

# 实验4：中文路径传 bytes（GBK 编码，系统 ANSI 代码页）——建库+写入+重读
try:
    db = plyvel.DB(cn_path.encode("gbk"), create_if_missing=True)
    db.put(b"k", b"v")
    db.close()
    db = plyvel.DB(cn_path.encode("gbk"), create_if_missing=False)
    print("[4] 中文路径(bytes-GBK) 建库+写入+重读:", db.get(b"k"))
    db.close()
    shutil.rmtree(cn_path)
except Exception as e:
    print("[4] 中文路径(bytes-GBK) 失败:", repr(e))
