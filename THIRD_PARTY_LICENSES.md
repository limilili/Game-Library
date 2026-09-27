# 第三方组件与许可证说明

本项目自有源码采用 GNU GPL v3.0，见仓库根目录 `LICENSE`。

发布打包版（exe / zip）时，请一并保留本文件、`LICENSE` 以及以下第三方声明。

## PySide6 / Qt 6

- 组件：PySide6、Qt 6（QtCore / QtGui / QtWidgets 等）
- 许可证：GNU Lesser General Public License v3（LGPLv3）或 Qt 商业许可证
- PySide6 同时提供商业许可选项

合规说明：

- 本项目以动态链接方式使用 PySide6/Qt 的 DLL / pyd，用户可以在打包目录中替换这些库文件。
- 发布包中应保留本说明和 LGPLv3 许可证文本，或提供官方许可证链接。
- 如果修改了 PySide6/Qt 本身，修改部分需要按 LGPLv3 条款开放。

官方链接：

- GNU LGPLv3: https://www.gnu.org/licenses/lgpl-3.0.html
- Qt 开源 LGPL 义务: https://www.qt.io/licensing/open-source-lgpl-obligations
- Qt for Python 许可证: https://doc.qt.io/qtforpython-6/licenses.html

## Python 运行时

- 组件：Python 3.12
- 许可证：Python Software Foundation License
- 官方链接：https://docs.python.org/3/license.html

## 主要 Python 依赖

以下组件按其各自许可证分发，通常为 MIT / BSD / Apache-2.0：

- SQLAlchemy
- Alembic
- pydantic / pydantic-settings
- PyYAML
- httpx / httpcore / h11
- loguru
- cryptography
- certifi
- greenlet

完整许可证文本可在安装后的 `*.dist-info/licenses/` 目录中查看。

## 发布建议

GitHub Release 中建议附带：

1. `LICENSE`（GPL-3.0 全文）
2. `THIRD_PARTY_LICENSES.md`（本文件）
3. 对应版本的源码链接（指向本仓库 commit / tag）
