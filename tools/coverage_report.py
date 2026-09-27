"""预置数据覆盖率统计工具（哩米 2026-09-27）。

用法：
    python tools/coverage_report.py
    python tools/coverage_report.py --json
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from client.core.coverage import build_coverage_report, format_coverage_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="库集预置数据覆盖率统计")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()
    if args.json:
        print(json.dumps(build_coverage_report(), ensure_ascii=False, indent=2))
    else:
        print(format_coverage_report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
