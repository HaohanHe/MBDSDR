#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""按半拆分根 tests/ 套件，给 CI 并行 job 用。

背景：根 tests/ 是百余个测试文件的大套件，跨文件的单例 QApplication 累积
使它在本机约 40 分钟、在 CI 侧历史实测也约 40 分钟。CI 原来给这个根一个
45 分钟的 job（墙钟偏长，且余量很薄）。

收件范围（历史上只收 `test_*.py`，漏掉了 `*_test.py` 与
`*roundtrip*.py` 两族共 31 个文件——phase71 修正）：
 - `test_*.py`     主测试族；
 - `*_test.py`     外部工具封装的对拍（kismet/satdump/rtklib 等）；
 - `*roundtrip*.py` 协议编解码往返（合成向量，非硬件）。
**故意不收**：`tool_selftest.py` / `tool_health_check.py` /
`tool_param_smoke.py` / `smoke_live_tools.py` 四个是 main() 风格的
周期性体检脚本（产出 JSON 报告、按需手动跑），不是单测；`conftest.py` /
`fake_rtl.py` 是测试基建。

拆法：把全部测试文件排序后**交替**分配到 N 组（相邻文件分散到不同
组，避免慢文件扎堆），每组一个 CI job 独立进程跑。分文件跑还能顺带减少
QApplication 累积带来的相互影响。

用法：
    python scripts/ci_pytest_groups.py --group 0 --total 2   # 打印该组的文件
    python scripts/ci_pytest_groups.py --check                # 自检：分组并集=全集、无重复

分组只依赖文件名的排序结果，因此**不需维护清单文件**：新增测试文件后
分组自动跟着变（`--check` 会保证并集仍是全集）。
"""
from __future__ import annotations

import argparse
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(REPO, "tests")


def test_files() -> list[str]:
    """根 tests/ 下的测试文件（排序后返回相对路径）。

    覆盖 `test_*.py` / `*_test.py` / `*roundtrip*.py` 三种命名；
    体检脚本（tool_selftest 等）与测试基建（conftest/fake_rtl）不在此列。
    """
    names = [n for n in os.listdir(TESTS_DIR)
             if n.endswith(".py")
             and (n.startswith("test_") or n.endswith("_test.py")
                  or "roundtrip" in n)]
    return sorted(os.path.join("tests", n) for n in names)


def group_files(group: int, total: int) -> list[str]:
    if total < 1:
        raise SystemExit("--total 必须 ≥ 1")
    if not 0 <= group < total:
        raise SystemExit(f"--group 必须在 0..{total - 1}")
    files = test_files()
    return [f for i, f in enumerate(files) if i % total == group]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="按半拆分根 tests/ 套件")
    ap.add_argument("--group", type=int, default=None)
    ap.add_argument("--total", type=int, default=2)
    ap.add_argument("--check", action="store_true",
                    help="自检：各组并集=全集、无重复")
    args = ap.parse_args(argv)

    files = test_files()
    if not files:
        print("根 tests/ 下没有测试文件", file=sys.stderr)
        return 1

    if args.check:
        seen: list[str] = []
        for g in range(args.total):
            seen.extend(group_files(g, args.total))
        if sorted(seen) != files:
            missing = sorted(set(files) - set(seen))
            dup = [f for f in set(seen) if seen.count(f) > 1]
            print(f"分组异常：缺 {missing} / 重复 {dup}", file=sys.stderr)
            return 1
        print(f"分组自检通过：{len(files)} 个文件 / {args.total} 组")
        for g in range(args.total):
            gf = group_files(g, args.total)
            print(f"  组 {g}: {len(gf)} 个文件")
        return 0

    if args.group is None:
        ap.error("需要 --group 或 --check")
    for f in group_files(args.group, args.total):
        print(f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
