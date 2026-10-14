# SPDX-License-Identifier: MIT
"""A2UI 编排演示：脚本化「外部 agent」走完整环，产出可双击打开的 HTML。

跑法（无需 Qt、无需硬件）：
    python -m mbdsdr_ai.aigui_demo --out a2ui_demo.html

演示四幕（对应 A2UI v1.0 官方消息族）：
    1) createSurface      默认三栏工作台（280/800/280）
    2) updateComponents   选中 tab:VNA、收起 tab:CW/ADS-B（show=false）
    3) updateDataModel    把一条状态 JSON 送进只读数据面板
    4) 未知 id            故意引用 tab:不存在 -> 诚实占位（不渲染）
布局模型的唯一事实源在 C++ renderer；本演示只证明 agent 侧信封与协议渲染
闭环可用（本机 Qt MinGW 损坏期间的可用见证）。
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List

from .aigui_render import render
from .aigui_messages import (arrange_to_envelope,
                             message_create_surface,
                             message_update_data_model)

DEFAULT_OUT = os.path.expanduser("~/.mbdsdr/aigui/a2ui_demo.html")


def demo_messages() -> List[Dict[str, Any]]:
    """四幕信封（agent 视角的动作序列，非手工拼 JSON）。"""
    default_ws = [
        {"id": "splitter", "component": "container",
         "props": {"sizes": [280, 800, 280], "stretch": [1, 3, 1]}},
        {"id": "left_rail", "component": "rail"},
        {"id": "center_spectrum", "component": "view"},
        {"id": "right_tabs", "component": "tab_container",
         "props": {"current": "tab:FT8"},
         "children": ["tab:CW", "tab:ADS-B", "tab:FT8", "tab:VNA",
                      "tab:电台"]},
        {"id": "status_bar", "component": "status"},
    ]
    focus_vna = [
        {"id": "right_tabs", "component": "tab_container",
         "props": {"current": "tab:VNA"}},
        {"id": "tab:VNA", "component": "tab", "props": {"selected": True}},
        {"id": "tab:CW", "component": "tab",
         "props": {"visible": False}},
        {"id": "tab:ADS-B", "component": "tab",
         "props": {"visible": False}},
    ]
    messages = [
        message_create_surface(components=default_ws),
        arrange_to_envelope("updateComponents", focus_vna),
        message_update_data_model(
            {"note": "demo", "frequency_hz": 435_274_000,
             "demod": "FM", "connected": True},
            path="/status"),
        # 第四幕：未知 id 必须被诚实占位，而不是被渲染或静默丢弃
        arrange_to_envelope("updateComponents",
                            [{"id": "tab:不存在", "component": "tab"}]),
    ]
    return messages


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="A2UI 编排演示（无 Qt 可跑）")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="输出 HTML 路径（默认 ~/.mbdsdr/aigui/a2ui_demo.html）")
    ap.add_argument("--print-json", action="store_true",
                    help="同时打印四幕信封 JSON（agent 视角审计用）")
    args = ap.parse_args(argv)

    messages = demo_messages()
    if args.print_json:
        for m in messages:
            print(json.dumps(m, ensure_ascii=False))

    page = render(messages, title="MBDSDR A2UI demo")
    out = os.path.expanduser(args.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print("已写出 %s（%d 字节）——双击或浏览器打开即可看到四幕编排结果。"
          % (out, len(page.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
