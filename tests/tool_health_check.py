#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MBDSDR 工具批量体检：对全部已注册工具逐个真实调用，分类结果。

输出四类：
  OK        真实返回了数据（非占位）
  NEED_ARG  缺必填参数（工具本身在，只是要参数）
  NEED_HW   需要硬件/设备连接
  TODO      明确占位/未实现/NotImplemented
  CRASH     抛异常

用法: python3 tests/tool_health_check.py
"""
import os, sys, json, traceback, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.agent import MBDSDRAgent, AgentConfig
from mbdsdr_ai.sdr_tools import register_sdr_tools


def classify(name, res):
    txt = res.content if hasattr(res, "content") else str(res)
    low = txt.lower()
    # 先判语义，再判 error，避免"缺参提示"被误判成崩溃
    if any(k in low for k in ("缺少必填", "missing required", "缺参数", "required 参数", "参数说明")):
        return "NEED_ARG", txt[:100]
    # 占位判定要严：明确未实现措辞才算；裸 todo/占位/placeholder 可能出现在正常数据
    # （如 commit message、文件名），只有配合调用失败才算，避免误判。
    todo_strong = ("not implemented", "尚未实现", "功能未实现", "未实现该功能", "待实现",
                   "coming soon", "this feature is not", "功能开发中")
    todo_weak = ("todo", "占位", "placeholder")
    if any(k in low for k in todo_strong) or (not getattr(res, "success", True)
                                              and any(k in low for k in todo_weak)):
        return "TODO", txt[:120]
    if any(k in low for k in ("未连接", "not connected", "设备未", "硬件未", "no device", "未插", "需先连接")):
        return "NEED_HW", txt[:100]
    if getattr(res, "error", "") or "traceback" in low:
        return "CRASH", txt[:120]
    if res.success and txt.strip():
        return "OK", txt[:100]
    return "OTHER", txt[:100]


def main():
    ag = MBDSDRAgent(AgentConfig())
    register_sdr_tools(ag)
    reg = ag.tool_registry
    tools = reg.list_tools()
    print(f"共 {len(tools)} 个工具，逐个体检...\n")

    buckets = collections.defaultdict(list)
    for t in tools:
        name = t.get("name") if isinstance(t, dict) else getattr(t, "name", str(t))
        # 尝试无参调用；缺参也算健康
        try:
            res = reg.call(name, {})
            cat, snippet = classify(name, res)
        except Exception as e:
            cat, snippet = "CRASH", f"{type(e).__name__}: {e}"[:120]
        buckets[cat].append((name, snippet))

    for cat in ("OK", "NEED_ARG", "NEED_HW", "TODO", "CRASH", "OTHER"):
        items = buckets.get(cat, [])
        print(f"=== {cat}: {len(items)} ===")
        if cat in ("TODO", "CRASH", "OTHER"):
            for n, s in items:
                print(f"  - {n}: {s}")
        else:
            # 只列前12个
            for n, s in items[:12]:
                print(f"  - {n}: {s}")
            if len(items) > 12:
                print(f"  ... 另 {len(items)-12} 个")
        print()

    print("汇总:", {k: len(v) for k, v in buckets.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
