#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P1-45 独立复现脚本：中文口语常用星名在星象仪搜索里 0 命中。

roadmap 行 P1-45 的断言（原文）：
    「北极星/北斗七星/天狼星等中文常用名 0 命中」
    证据等级：实测查询（gap-sky #4）。

做法：offscreen 起真 ``RFSkyView``，走公开入口 ``_on_search_text``，统计
``kind == "star"`` 的结果。不碰硬件、不依赖 SGP4 网络。

判定只针对**原断言**（常用名 0 命中）：
  * 任一口语名（北极星／北斗七星／天狼星）星类结果 0 条 → 原断言复现；
  * 三者全部命中 → 未复现（已修）。

退出码（三态）：
    0  复现成功   —— 至少一个口语名 0 命中
    1  未复现     —— 三个口语名都有星类结果
    2  无法判定   —— 起不了 offscreen 视图 / 星表不可用

反证对照组：把 ``alias_names = common_star_alias_targets(text)`` 改回
``()``（并用 ``terms = (text,)`` 关掉「星」后缀归一），三个口语名必须
重新全 0。对照组失效则本脚本不可用于判定。

用法：
    python scripts/repro/2026-11-11-p1-45-star-common-name-search.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desktop"))

QUERIES = ("北极星", "北斗七星", "天狼星")


def main() -> int:
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        from rf_sky_view import RFSkyView
        view = RFSkyView()
    except Exception as exc:  # pragma: no cover - 环境不满足
        print(f"无法判定：offscreen 视图起不来（{type(exc).__name__}: {exc}）")
        return 2

    zero = []
    for q in QUERIES:
        view._on_search_text(q)
        stars = [r for r in view._search_results if r.get("kind") == "star"]
        names = [r.get("name") for r in stars]
        print(f"{q!r}: star={len(stars)} names={names}")
        if not stars:
            zero.append(q)
    app.processEvents()
    if zero:
        print(f"复现：{zero} 在恒星搜索结果里 0 命中")
        return 0
    print("未复现：三个口语名均有恒星结果")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
