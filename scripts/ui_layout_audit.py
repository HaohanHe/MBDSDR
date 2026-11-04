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
    python scripts/ui_layout_audit.py --update-baselines --reason "..."  # 仅颜色漂移
    python scripts/ui_layout_audit.py --update-baselines \
        --accept-geometry-change --reason "spectrum-axis-nice-steps: ..."  # 有意几何变更
    python scripts/ui_layout_audit.py -- -k ratio          # 透传 pytest 参数

``--accept-geometry-change`` 是第二条合法通道：尺寸/宽高比或刻度标签
几何变化时默认拒绝更新；带该参数后 reason 必须点名
``scripts/ui_baseline.py`` 的 ``DESIGN_CHANGES`` 声明，否则仍拒绝。
提交说明里必须同样点名该设计变更，便于追溯。

退出码: 与 pytest 一致（0=全绿，1=有用例失败，2/4=中断/用法错误）。
可进 CI（本脚本只做入口，不接线；CI 归另一条线）。
"""
import argparse
import os
import re
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPTS)   # 声明表 DESIGN_CHANGES 与守卫共用同一来源
_TEST_FILE = os.path.join("tests", "test_ui_layout_locks.py")
_STATUS = ("PASSED", "FAILED", "SKIPPED", "ERROR", "XFAIL", "XPASS")
_NODE_RE = re.compile(r"::(?P<rule>[A-Za-z0-9_]+)\[(?P<args>[^\]]*)\]")


_PRIORITY = {"ERROR": 4, "FAILED": 3, "SKIPPED": 2, "PASSED": 1,
             "XPASS": 1, "XFAIL": 1}


def _parse_matrix(output: str):
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
        if "::" not in node:
            continue   # 过滤 "FAILED tests/...::case" 这类 summary 行
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


def _print_matrix(matrix, sizes, scales, updating=False):
    if not matrix:
        print("\n[ui_layout_audit] 未能从 pytest 输出解析出规则矩阵。")
        return
    cols = [(f"{s}@{lab}", s, lab) for s in sizes for lab in scales]
    cols += [("all", "all", None)]
    print("\n[ui_layout_audit] 规则 × (尺寸@DPI)：")
    width = max(len(r) for r in matrix)
    header = "  " + "规则".ljust(width) + "  " + "  ".join(
        c[0].ljust(13) for c in cols)
    print(header)
    print("  " + "-" * (len(header) - 2))
    for rule in sorted(matrix):
        cells = []
        per = matrix[rule]
        for _label, s, lab in cols:
            key = (s, lab) if s != "all" else ("all", None)
            cells.append(per.get(key, "-").ljust(13))
        print("  " + rule.ljust(width) + "  " + "  ".join(cells))
    failed = [(r, s, lab) for r, per in matrix.items()
              for (s, lab), st in per.items()
              if st in ("FAILED", "ERROR")]
    if failed:
        print("\n[ui_layout_audit] 失败清单：")
        for rule, size, lab in failed:
            where = f"{size} @ {lab}" if size != "all" else "通用"
            print(f"  - {rule}  [{where}]")
    elif updating:
        print("\n[ui_layout_audit] 基线已重建（截图用例按 SKIPPED 计）；"
              "请 diff 复核 tests/baselines/*.png 后再提交。")
    else:
        print("\n[ui_layout_audit] 全部尺寸×DPI×规则通过。")


def main() -> int:
    ap = argparse.ArgumentParser(description="MBDSDR UI 布局不变式审计")
    ap.add_argument("--update-baselines", action="store_true",
                    help="重建 tests/baselines/ 下的截图基线（需人工复核）")
    ap.add_argument("--accept-geometry-change", action="store_true",
                    help="允许本次有意的几何变更（尺寸/宽高比/刻度标签位置）；"
                         "必须配 --reason 且点名 scripts/ui_baseline.py 的 "
                         "DESIGN_CHANGES 声明，否则仍拒绝")
    ap.add_argument("--reason", default="",
                    help="基线更新的原因（人工确认，--update-baselines 时必填）")
    ap.add_argument("pytest_args", nargs=argparse.REMAINDER,
                    help="透传给 pytest 的参数（写在 -- 之后）")
    args = ap.parse_args()

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.setdefault("MBDSDR_OFFLINE", "1")
    env["NO_COLOR"] = "1"
    # 150%/200% 是用户点名的两档；125% 也覆盖，统一给 3 档 DPI。
    # 基线只在 100%（DPR=1）重建/比对：测试内部按逻辑像素归一，
    # 因此 125/150/200 复用同一套基线，不按 DPI 存多份。
    scales = [("100%", "1.0")] if args.update_baselines else \
        [("125%", "1.25"), ("150%", "1.5"), ("200%", "2.0")]
    if args.accept_geometry_change and not args.update_baselines:
        ap.error("--accept-geometry-change 只能与 --update-baselines 同时使用")
    if args.update_baselines:
        if not args.reason.strip():
            ap.error("--update-baselines 必须配 --reason \"<原因>\"（人工确认）")
        env["MBDSDR_UI_BASELINE_UPDATE"] = "1"
        env["MBDSDR_UI_BASELINE_REASON"] = args.reason.strip()
        if args.accept_geometry_change:
            import ui_baseline
            env["MBDSDR_UI_BASELINE_ACCEPT_GEOMETRY"] = "1"
            print("[ui_layout_audit] 有意几何变更通道：几何 diff 会逐张打印；"
                  "reason 必须点名以下声明之一，且提交说明同样点名：")
            for token, desc in ui_baseline.DESIGN_CHANGES.items():
                print(f"  - {token}：{desc}")
        print("[ui_layout_audit] 基线更新模式（仅 100% DPI）："
              f"reason={args.reason.strip()}")
        print("[ui_layout_audit] 每张图会打印尺寸/色块/像素差异摘要；"
              "几何漂移会被拒绝。写入后请 diff 复核 PNG。")

    cmd = [sys.executable, "-m", "pytest", _TEST_FILE, "-v",
           "--tb=short", "--color=no", "-p", "no:cacheprovider"]
    if args.update_baselines:
        cmd.append("-s")   # 让每张基线的差异摘要直接打印出来
    extra = [a for a in args.pytest_args if a != "--"]
    cmd.extend(extra)

    sizes = [f"{w}x{h}" for w, h in
             ((800, 600), (1000, 600), (1280, 720), (1600, 900), (1920, 1080))]
    merged: dict = {}
    rc = 0
    for label, factor in scales:
        env["QT_SCALE_FACTOR"] = factor
        print(f"\n===== QT_SCALE_FACTOR={factor} ({label}) =====")
        proc = subprocess.run(cmd, cwd=_ROOT, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        output = proc.stdout.replace("\r", "")
        sys.stdout.write(output)
        if proc.stderr:
            sys.stderr.write(proc.stderr.replace("\r", ""))
        rc = rc or proc.returncode
        for rule, per in _parse_matrix(output).items():
            dst = merged.setdefault(rule, {})
            for size, st in per.items():
                key = ("all", None) if size == "all" else (size, label)
                if key not in dst or _PRIORITY.get(st, 0) > _PRIORITY.get(dst[key], 0):
                    dst[key] = st

    _print_matrix(merged, sizes, [s[0] for s in scales],
                  updating=args.update_baselines)
    return rc


if __name__ == "__main__":
    sys.exit(main())
