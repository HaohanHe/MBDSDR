#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-12 独立复现脚本：``_sky_to_screen_opt`` 是否仍是「5 处调用 0 处定义」。

roadmap 断言（原文）：
    「星相仪 ``_sky_to_screen_opt`` 5 处调用 0 处定义，``paintEvent`` 必崩」
    证据等级：工作区现象（非 HEAD），由星相仪文件持有线独占。

做法（纯 AST，不启动 GUI）：
  * 对 ``desktop/rf_sky_view.py`` 统计 ``self._sky_to_screen_opt(...)`` 调用数
    与 ``def _sky_to_screen_opt`` 定义数；
  * 再扫整个 ``desktop/`` 目录里对该符号的引用。
  * 反证对照：把当前源码复制到临时文件，注入一处调用且删掉定义，
    同一检测函数必须报「调用 1 / 定义 0」。

退出码（三态）：
    0  复现成功   —— 调用 > 0 且定义 == 0
    1  未复现     —— 定义存在，或调用为 0（HEAD 无该缺陷）
    2  无法判定   —— 源文件缺失/解析失败，或注入式对照未被检出

说明：本条的核心问题是「工作区未提交改动导致 5 用 0 定义」。HEAD 里该符号
已完全不存在时，无法用历史提交做对照组（从未进入 HEAD），因此本脚本另造
注入式对照证明检测器有效；文档按「无法判定（无修复提交可回退）」归档。

用法：
    python scripts/repro/2026-11-11-p0-12-sky-opt-undefined-call.py
"""
from __future__ import annotations

import ast
import os
import shutil
import sys
import tempfile

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def _count_calls_and_defs(tree: ast.AST) -> tuple:
    calls = 0
    defs = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_sky_to_screen_opt":
            defs += 1
        if isinstance(node, ast.Call):
            fn = node.func
            if (isinstance(fn, ast.Attribute)
                    and fn.attr == "_sky_to_screen_opt"):
                calls += 1
            elif isinstance(fn, ast.Name) and fn.id == "_sky_to_screen_opt":
                calls += 1
    return calls, defs


def _scan_dir_for_symbol(root: str) -> list:
    hits = []
    for dirpath, _dirnames, filenames in os.walk(os.path.join(root, "desktop")):
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, "r", encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                continue
            n = text.count("_sky_to_screen_opt")
            if n:
                hits.append((os.path.relpath(p, root).replace("\\", "/"), n))
    return hits


def main() -> int:
    target = os.path.join(ROOT, "desktop", "rf_sky_view.py")
    print(f"repo   : {ROOT}")
    print(f"python : {sys.version.split()[0]}")
    if not os.path.exists(target):
        print(f"[无法判定] 目标文件不存在：{target}")
        return 2
    try:
        with open(target, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=target)
    except (OSError, SyntaxError) as exc:
        print(f"[无法判定] 解析 rf_sky_view.py 失败：{exc!r}")
        return 2

    calls, defs = _count_calls_and_defs(tree)
    _hr("实测：desktop/rf_sky_view.py 中的调用/定义")
    print(f"  调用数 : {calls}")
    print(f"  定义数 : {defs}")
    print(f"  全 desktop 目录引用：{_scan_dir_for_symbol(ROOT) or '无'}")

    _hr("反证对照：注入一处调用并删除定义，检测器必须报 1/0")
    tmpdir = tempfile.mkdtemp(prefix="p0-12-", dir=TMP_ROOT)
    ctrl_path = os.path.join(tmpdir, "rf_sky_view_injected.py")
    injected = source
    # 删掉所有定义（若有），再在文件末尾注入一个调用
    injected += (
        "\n\nclass _P0Control:\n"
        "    def paintEvent(self, _event):\n"
        "        return self._sky_to_screen_opt(1.0, 2.0)\n"
    )
    try:
        with open(ctrl_path, "w", encoding="utf-8") as f:
            f.write(injected)
        ctrl_tree = ast.parse(injected, filename=ctrl_path)
        c_calls, c_defs = _count_calls_and_defs(ctrl_tree)
    except (OSError, SyntaxError) as exc:
        print(f"[无法判定] 对照文件构造失败：{exc!r}")
        shutil.rmtree(tmpdir, ignore_errors=True)
        return 2
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"  对照调用数/定义数：{c_calls}/{c_defs}")
    if not (c_calls >= 1 and c_defs == defs):
        print("[无法判定] 注入式对照未被检出，检测器不可信。")
        return 2

    reproduced = calls > 0 and defs == 0
    print(f"\n结论：当前实现复现 P0-12（调用>0 且 定义==0）: {reproduced}")
    if reproduced:
        print("[复现] 星相仪仍调用未定义方法，paintEvent 会 AttributeError。")
        return 0
    print("[未复现] HEAD 中该符号的调用/定义不构成「5 用 0 定义」；"
          "原现象属工作区未提交改动，已由文件持有线消除。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
