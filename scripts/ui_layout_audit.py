#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
UI 布局不变式汇总审计入口
==========================

一次跑完「所有尺寸 × 所有规则」，并输出哪条规则在哪个尺寸下失败。

覆盖 tests/test_ui_layout_locks.py 里的四条硬规则（用户原话锁）：
  1. 顶部频率读数不逐位换行
  2. 频谱标注 / 刻度文字不重叠、不越出谱面
  3. 三栏比例合理，ADS-B 空态不吞主区
  4. 任何可见控件不越出父容器
外加关键面板截图基线（宽松比对）。

用法:
    python scripts/ui_layout_audit.py
    python scripts/ui_layout_audit.py --update-baselines   # 重建基线后人工复核
    python scripts/ui_layout_audit.py -- -k ratio          # 透传 pytest 参数

退出码: 与 pytest 一致（0=全绿，1=有用例失败，2/4=中断/用法错误）。
可进 CI（本脚本只做入口，不接线；CI 归另一条线）。
"""
import argparse
import os
import re
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEST_FILE = os.path.join("tests", "test_ui_layout_locks.py")
_STATUS = ("PASSED", "FAILED", "SKIPPED", "ERROR", "XFAIL", "XPASS")
_NODE_RE = re.compile(r"::(?P<rule>[A-Za-z0-9_]+)\[(?P<args>[^\]]*)\]")


_PRIORITY = {"ERROR": 4, "FAILED": 3, "SKIPPED": 2, "PASSED": 1,
             "XPASS": 1, "XFAIL": 1}


def _parse_matrix(output: str, sizes):
    """从 pytest -v 输出提取 {规则: {尺寸: 状态}}（同规则同尺寸多面板取最坏）。"""
    matrix = {}
    for raw in output.splitlines():
        line = raw.strip()
        if "::" not in line:
            continue
        status = None
        for s in _STATUS:
            if re.search(rf"\b{s}\b", line):
                status = s
        if status is None:
            continue
        node = line.split()[0]
        m = _NODE_RE.search(node)
        if m:
            rule = m.group("rule")
            args = m.group("args").split("-")
        else:
            # 无参数用例（如窄栏频率读数）归入 all 列
            rule = node.split("::")[-1]
            args = []
        size = next((a for a in args if re.fullmatch(r"\d+x\d+", a)), "all")
        per = matrix.setdefault(rule, {})
        if size not in per or _PRIORITY.get(status, 0) > _PRIORITY.get(per[size], 0):
            per[size] = status
    return matrix


def _print_matrix(matrix, sizes, updating=False):
    if not matrix:
        print("\n[ui_layout_audit] 未能从 pytest 输出解析出规则矩阵。")
        return
    if any("all" in per for per in matrix.values()):
        sizes = list(sizes) + ["all"]
    print("\n[ui_layout_audit] 规则 × 尺寸：")
    width = max(len(r) for r in matrix)
    header = "  " + "规则".ljust(width) + "  " + "  ".join(
        s.ljust(8) for s in sizes)
    print(header)
    print("  " + "-" * (len(header) - 2))
    for rule in sorted(matrix):
        cells = []
        for s in sizes:
            st = matrix[rule].get(s, "-")
            cells.append(st.ljust(8))
        print("  " + rule.ljust(width) + "  " + "  ".join(cells))
    failed = [(r, s) for r, per in matrix.items()
              for s, st in per.items() if st in ("FAILED", "ERROR")]
    if failed:
        print("\n[ui_layout_audit] 失败清单：")
        for rule, size in failed:
            print(f"  - {rule} @ {size}")
    elif updating:
        print("\n[ui_layout_audit] 基线已重建（截图用例按 SKIPPED 计）；"
              "请 diff 复核 tests/baselines/*.png 后再提交。")
    else:
        print("\n[ui_layout_audit] 全部尺寸全部规则通过。")


def main() -> int:
    ap = argparse.ArgumentParser(description="MBDSDR UI 布局不变式审计")
    ap.add_argument("--update-baselines", action="store_true",
                    help="重建 tests/baselines/ 下的截图基线（需人工复核）")
    ap.add_argument("pytest_args", nargs=argparse.REMAINDER,
                    help="透传给 pytest 的参数（写在 -- 之后）")
    args = ap.parse_args()

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.setdefault("MBDSDR_OFFLINE", "1")
    env["NO_COLOR"] = "1"
    if args.update_baselines:
        env["MBDSDR_UI_BASELINE_UPDATE"] = "1"
        print("[ui_layout_audit] 基线更新模式：写入后请 diff 复核 PNG。")

    cmd = [sys.executable, "-m", "pytest", _TEST_FILE, "-v",
           "--tb=short", "--color=no", "-p", "no:cacheprovider"]
    extra = [a for a in args.pytest_args if a != "--"]
    cmd.extend(extra)

    print("[ui_layout_audit] " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=_ROOT, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    output = proc.stdout.replace("\r", "")
    sys.stdout.write(output)
    if proc.stderr:
        sys.stderr.write(proc.stderr.replace("\r", ""))

    sizes = [f"{w}x{h}" for w, h in ((1000, 600), (1600, 900))]
    _print_matrix(_parse_matrix(output, sizes), sizes,
                  updating=args.update_baselines)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
