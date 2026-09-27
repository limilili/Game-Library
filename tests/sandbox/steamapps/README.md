# 沙盒 Steam 库解析（POC-1）

> 对应 PRD §12 Phase 0 POC-1（VDF/ACF 文件解析，M1 扫描模块基础验证）。
> 真实文件只读解析，不写入任何真实 Steam 文件（CLAUDE.md 铁律）。

## 目录结构

```
steamapps/
├── scripts/
│   ├── vdf_parser.py        # VDF 解析器（tokenize + parse + dump + 库专用读取）
│   ├── analyze_real.py      # 真实库只读对照分析（哩米实机：2 库 32 游戏）
│   └── test_steamapps.py    # pytest 断言（8 项：构造精确断言）
└── README.md
```

## 实机格式结论（2026-08-11）

1. **libraryfolders.vdf**：顶层 `"libraryfolders"`，库编号 `"0"/"1"/...` 为键 → `{path, label, contentid, apps:{appid:size}}`；`path` 中 `\\` 表示单反斜杠（Windows 路径），**解析时须解码 `\\` → `\`**
2. **appmanifest_*.acf**：UTF-8 无 BOM，顶层 `"AppState"`；字段 `"key"\t\t"value"`，值内引号 `\"` 转义；嵌套区块（InstalledDepots/UserConfig/SharedDepots）用 `{}`，解析为 dict
3. **StateFlags 位标记**：`4` = 完全安装（FullyInstalled）；`2` = 需更新、`0` = 未安装等状态需过滤（v0 未做此过滤，POC-1 补齐）
4. **多库扫描**：解析 libraryfolders → 遍历各库 steamapps/appmanifest_*.acf（appid 从文件名提取，与 manifest 内 appid 交叉校验）

## 解析器 API（Phase 1 vdf_ops/vdf_parser 落位参考）

- `parse_vdf_text/parse_vdf_file` → `{key: value}`（嵌套为 dict，保序）
- `dump(data)` → VDF 文本（roundtrip 验证：dump → parse 一致）
- `parse_library_folders(steam_path)` → 库列表（index/path/label/apps）
- `list_appmanifests(library_path)` → appmanifest_*.acf 列表（按 appid 升序）
- `parse_appmanifest(file)` → AppState 区块 dict
- `is_fully_installed(manifest)` → StateFlags & 4

运行：`pytest tests/sandbox/steamapps/scripts/test_steamapps.py -v` + `python scripts/analyze_real.py`
