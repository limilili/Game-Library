# 沙盒 LevelDB（POC-2 备选路线验证）

> 对应 PRD §12 Phase 0 POC-2 备选验证；沙盒只读/构造操作，不接触真实 Steam 写入。

## 目录结构

```
leveldb/
├── data/                       # ⚠️ 沙盒数据目录（gitignore，不入库）
│   ├── constructed/            # 步骤2：构造库（build_constructed_db.py 生成）
│   └── real_copy/              # 步骤4：真实 Local Storage LevelDB 副本
├── real_cloudstorage/          # ⚠️ 真实 cloudstorage JSON 副本（gitignore，不入库）
├── real_analysis.txt           # ⚠️ 步骤4 分析输出（gitignore，不入库）
├── scripts/
│   ├── build_constructed_db.py # 步骤2：构造脚本
│   ├── dump_db.py              # 通用 dump 工具（独立进程只读遍历）
│   ├── probe_path.py           # 中文路径兼容性探针（已转正）
│   ├── test_constructed.py     # 步骤3：pytest 断言（7 项）
│   ├── analyze_real.py         # 步骤4：真实 LevelDB + cloudstorage JSON 分析
│   └── test_cloudstorage.py    # 步骤4：交叉验证 pytest 断言（12 项）
└── README.md
```

## ⚠️ 数据目录位置（中文路径限制）

沙盒数据（constructed/、real_copy/）在 **`tests/sandbox/leveldb/data/`**（gitignore），
可用环境变量 `KUJI_POC_DATA` 覆盖。

原因（2026-08-10 实测）：plyvel 内置 LevelDB 的 Windows 实现使用 ANSI 文件 API，
**无法打开含中文的路径**。当时项目路径 `Desktop/库集/2` 含中文，数据先迁至 ASCII
临时目录；**2026-08-11 项目文件夹更名为 ASCII 路径 `Desktop/Game library`**，
数据目录随之迁回沙盒内。该限制对「用户 Steam 安装路径含中文」场景依然成立
（GBK bytes 传参为候选绕过方案，需检测系统代码页）。详细实验见
`temp/docs/POC-2-LevelDB-失败记录.md` 条目 2。

## 键结构（步骤 4 实测已校正）

步骤 2-3 构造库采用假设格式（参考 Repressurizer 路径拼接键 + 学习清单 LevelDB 结论）：

```
UserRoamingConfigStore/Software/Valve/Steam/Users/{account_id}/Tags/{appid}/TagName -> 标签名(UTF-8)
```

步骤 4 实测结论（2026-08-10）：
- 本机真实 `sharedconfig.vdf` 为**文本 VDF**（非 LevelDB），真实分类数据在 cloudstorage JSON（user-collections）
- 真实 LevelDB（htmlcache Local Storage）值带编码前缀：0x00 = UTF-16LE、0x01 = UTF-8，解析时按前缀解码
- 详见 `temp/docs/已验证信息.md` §6

预知数据定义在 `build_constructed_db.py` 的 `KNOWN_DATA`（改数据需同步改步骤 3 pytest 断言）。
