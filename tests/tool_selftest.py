"""全量工具可调用性自检。

在没有真实硬件（无 RTL-SDR 连接）的默认环境下，遍历全部已注册工具，
用最小/保守参数 dry-call，分类结果：
  ok       —— 工具正常返回（含模拟器/离线路径）
  friendly —— 明确报告"无设备/未连接/需要硬件"等预期不可用
  crash    —— 抛异常或返回 traceback 文本（这是要修的：未接硬件不该崩）

用法：python3 tests/tool_selftest.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mbdsdr_ai.agent import MBDSDRAgent, AgentConfig  # noqa: E402
from mbdsdr_ai.sdr_tools import register_sdr_tools  # noqa: E402

FRIENDLY_HINTS = (
    "无设备", "未连接", "没有设备", "设备不存在", "需要", "请先",
    "no device", "not connected", "no sdr", "unavailable", "需要连接",
    "未打开", "请打开", "需要打开", "尚未", "不支持",
    "请指定", "不存在", "为空", "找不到", "已设置", "已切换",
    "可选", "不是一个", "未知的", "没有", "不能为空", "必须",
)
# 真正的崩溃 = 未捕获的内部异常（traceback 或裸异常类型）；
# 带清楚业务消息的 ValueError/FileNotFoundError 属于友好参数校验，不算崩溃。
CRASH_HINTS = (
    "traceback (most recent", "isadirectoryerror", "permissionerror",
    "keyerror", "indexerror", "typeerror", "attributeerror",
)


def _safe_default(name: str, prop: dict):
    t = prop.get("type", "string")
    low = name.lower()
    if t == "string" and ("path" in low or "file" in low or low.endswith("_file")):
        return "README.md"
    if t == "number":
        if "sample_rate" in low or "rate" in low:
            return 2400000.0
        if "bandwidth" in low:
            return 1000000.0
        if "gain" in low:
            return 20.0
        if "freq" in low:
            return 100000000.0
        return 0.0
    if t == "integer":
        return 1
    if t == "boolean":
        return False
    if t == "array":
        return []
    return ""


def _min_args(definition: dict) -> dict:
    fn = definition.get("function", {})
    params = fn.get("parameters", {})
    props = params.get("properties", {})
    required = params.get("required", []) or []
    args = {}
    for r in required:
        if r in props:
            args[r] = _safe_default(r, props[r])
    return args


def main() -> int:
    ag = MBDSDRAgent(AgentConfig())
    register_sdr_tools(ag)
    tr = ag.tool_registry
    names = tr.get_tool_names()

    ok, friendly, crash = [], [], []
    crash_detail = []

    for name in names:
        try:
            definition = tr.tools[name]["definition"]
        except Exception:
            definition = {"function": {"parameters": {}}}
        args = _min_args(definition)
        try:
            res = tr.call(name, args)
        except Exception as e:  # noqa: BLE001
            crash.append(name)
            crash_detail.append((name, "RAISED: " + repr(e)))
            continue
        content = getattr(res, "content", str(res)) or ""
        low = content.lower()
        is_friendly = any(h in low for h in (h.lower() for h in FRIENDLY_HINTS))
        is_crash = any(h in low for h in CRASH_HINTS)
        if getattr(res, "success", False):
            ok.append(name)
        elif is_friendly and not is_crash:
            friendly.append(name)
        else:
            crash.append(name)
            crash_detail.append((name, content[:160].replace("\n", " ")))

    print(f"=== 工具可调用性自检（无硬件环境）===")
    print(f"总工具数: {len(names)}")
    print(f"  正常 ok       : {len(ok)}")
    print(f"  友好不可用     : {len(friendly)}")
    print(f"  崩溃/异常返回  : {len(crash)}")
    if crash:
        print("\n--- 崩溃/异常工具清单（需修）---")
        for n, msg in crash_detail:
            print(f"  {n}: {msg}")
    else:
        print("\n无工具崩溃：未接硬件时全部友好降级。")
    # 退出码：有崩溃则非零（CI 用）
    return 1 if crash else 0


if __name__ == "__main__":
    raise SystemExit(main())
