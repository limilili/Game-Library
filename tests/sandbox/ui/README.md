# 沙盒三栏 UI 骨架（POC-6）

> 对应 PRD §12 Phase 0 POC-6（PySide6 三栏布局骨架）与 §8.3 桌面 UI 设计。
> v1.3 已并入：分类树加载官方方案（玩法树）+ 风格标签区（小窗）。

## 目录结构

```
ui/
├── main_window.py        # 三栏骨架主窗口（可独立运行）
├── test_ui_skeleton.py   # offscreen 程序化验证（6 项断言）
├── screenshots/          # 截图存档（空态 + 数据态）
└── README.md
```

## 验证内容（2026-08-12，PySide6 6.11.1）

| 验证点 | 结果 |
|---|---|
| 三栏布局：QTreeView（分类树）+ QTableView（游戏列表）+ QStackedWidget（详情/预览） | ✅ 6/6 断言全绿 |
| 分类树加载官方方案（category-tree-default.json）：顶层 15 大类、62 节点、恐怖 6 子类 | ✅ |
| 风格标签区：肉鸽/派对/R18（v1.3 风格小窗） | ✅ |
| 假数据填充（10 款）→ 选中游戏 → 详情页切换 | ✅ |
| 主题跟随系统（QStyleHints.colorScheme） | ✅ |
| 截图存档：空态 + 数据态（1100×650） | ✅ |

## 运行

```bash
# 交互模式（真实窗口，可点选体验）
python tests/sandbox/ui/main_window.py

# offscreen 验证 + 截图
pytest tests/sandbox/ui/test_ui_skeleton.py -v
```

## Phase 1 落位说明

- 骨架验证通过 → Phase 1 在 `client/ui/` 落位（main_window / library_view / category_tree / detail 面板），接入真实数据流（SQLite + 扫描结果 + 方案切换）
- 风格小窗与「开关=加入分类树」机制为骨架级展示，交互逻辑 Phase 1 实现
- 详情面板 Phase 1 扩展（元数据 / AI 建议 M4.9 / 分类编辑）
