#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MBDSDR 工具参数化批量真跑（第二轮体检）。

tool_health_check.py 只做无参调用，102 个工具停在 NEED_ARG。
本脚本从每个工具的 JSON Schema 读 required 参数，按参数名/类型自动构造
合理测试值，再真实调用，把它们推进到「真出数据 / 需硬件 / 参数仍不符」。

输出：
  OK        喂参后真实返回数据（非占位、非报错）
  NEED_HW   参数对，但需要真实 SDR/电台/云台
  BAD_ARG  参数构造不被接受（脚本侧测试值问题，非工具空壳）
  TODO      占位/未实现
  CRASH     抛异常
用法: python3 tests/tool_param_smoke.py
"""
import os
import sys
import json
import collections

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.agent import MBDSDRAgent, AgentConfig
from mbdsdr_ai.sdr_tools import register_sdr_tools

# 仓库内真实存在的小文件，供读文件类工具真跑
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_FILE = os.path.join(_REPO_ROOT, "README.md")
if not os.path.exists(_REPO_FILE):
    _REPO_FILE = os.path.join(_REPO_ROOT, "mbdsdr_ai_mcp_server.py")


def value_for(pname, schema):
    """按参数名语义 + 类型，给一个合理的测试值。"""
    n = pname.lower()
    typ = (schema or {}).get("type", "string")
    enum = (schema or {}).get("enum")
    if enum:
        return enum[0]

    # --- 精确业务字段（优先，避免被通用规则误兜成 test）---
    if n == "workflow_name":
        return "interference_localization"
    if n == "satellite":
        return "noaa19_apt"
    if n == "label":  # amr_add_sample 的调制标签，须是合法 ModulationType
        return "FSK"
    if n == "rssi_by_azimuth":
        return {"0": -82.0, "30": -75.0, "60": -68.0, "90": -74.0, "120": -80.0, "150": -83.0}
    if n == "iq_samples":
        # 一小段合成基带：交替 I/Q，64 点
        return [0.5 if i % 2 == 0 else 0.0 for i in range(64)]
    if n in ("subagent_id", "recording_id"):
        return "__nonexistent_resource__"  # 预期被业务校验拦下，归 NEED_RESOURCE
    if n in ("goal", "task", "objective"):
        return "扫描当前频段并报告最强信号"
    if n in ("old_section",):
        return "__no_such_section__"
    if n in ("new_section",):
        return "# test\n"
    if n in ("output_dir", "out_dir", "output_path"):
        return "/tmp/mbdsdr_param_out"
    if n in ("input_file", "iq_file", "wav_file", "csv_file"):
        return "/tmp/mbdsdr_param_in.bin"
    if n in ("file_path", "filepath", "filename"):
        # 给一个仓库内真实存在的小文件，读文件类工具能真跑
        return _REPO_FILE
    # --- 地理坐标 ---
    if n in ("lat", "latitude", "observer_lat", "source_lat", "ref_lat"):
        return 43.8868
    if n in ("lon", "longitude", "observer_lon", "source_lon", "ref_lon"):
        return 125.3245
    lat2 = ("lat" in n) and ("lon" not in n)
    lon2 = ("lon" in n) or ("lng" in n)
    if lat2 and typ in ("number", "integer"):
        return 43.8868
    if lon2 and typ in ("number", "integer"):
        return 125.3245
    # --- 频率（Hz）---
    if "center_freq" in n or n in ("freq_hz", "frequency_hz", "center_frequency_hz"):
        return 100_000_000
    if n in ("freq", "frequency"):
        # 音频解调类多按 Hz，但射频类也可能；给 100 MHz 通用
        return 100_000_000
    if "satellite" in n or n in ("sat_name",):
        return "NOAA 19"
    if "band" in n and "name" in n:
        return "L1"
    if n in ("band",):
        return "L1"
    # --- 采样率 / 音频 ---
    if "sample_rate" in n:
        return 1_024_000
    if n in ("baudrate", "baud"):
        return 1200
    if n in ("bandwidth_hz", "bandwidth"):
        return 12_500
    # --- 角度 ---
    if "azimuth" in n or n == "az":
        return 0.0
    if "elevation" in n or n == "el":
        return 45.0
    # --- 增益/功率/SNR ---
    if n in ("gain", "gain_db"):
        return 20
    if "snr" in n:
        return 10
    if "power" in n:
        return 0
    # --- 缩放/平移 ---
    if "factor" in n or "zoom" in n:
        return 2.0
    if "offset_hz" in n or n == "offset":
        return 1000
    # --- 时间/数量 ---
    if n in ("duration", "duration_s", "seconds", "secs"):
        return 1
    if n in ("n", "count", "num", "samples", "num_samples", "trials", "num_trials", "size"):
        return 10
    if "seed" in n:
        return 42
    # --- 设备 ---
    if n in ("device_id", "device", "serial"):
        return "mock"
    if "port" in n:
        return "/dev/ttyUSB0"
    # --- 无线电业务字段 ---
    if n in ("callsign", "call"):
        return "BI4MIB"
    if n == "source":
        return "BI4MIB"
    if n in ("destination", "dest", "target"):
        return "TEST"
    if n == "symbol":
        return "/-"
    if "digipeater" in n:
        return ["WIDE2-2"]
    if n in ("mode", "modulation", "demod_mode", "demod", "waveform"):
        return "NFM"
    if n in ("text", "message", "msg", "content"):
        return "HELLO"
    if n == "morse":
        return ".... . .-.. .-.. ---"
    if "url" in n:
        return "https://example.org"
    if "path" in n or "file" in n or "filename" in n:
        return "/tmp/mbdsdr_test.bin"
    if "command" in n or n == "cmd":
        return "help"
    if "query" in n or "question" in n or n == "prompt":
        return "test"
    if "name" in n:
        return "test"
    if "code" in n:
        return "TEST"
    # --- 兜底按类型 ---
    if typ == "integer":
        return 1
    if typ == "number":
        return 1.0
    if typ == "boolean":
        return True
    if typ == "array":
        return []
    if typ == "object":
        return {}
    return "test"


def build_args(tool):
    schema = tool.get("parameters", {}) or {}
    props = schema.get("properties", {}) or {}
    required = schema.get("required", []) or []
    args = {}
    for p in required:
        args[p] = value_for(p, props.get(p, {}))
    return args


def classify(txt, success, error):
    low = txt.lower()
    if any(k in low for k in ("缺少必填", "missing required", "缺参数", "required 参数", "缺少参数", "required:")):
        return "BAD_ARG"
    todo_strong = ("not implemented", "尚未实现", "功能未实现", "未实现该功能", "待实现",
                   "coming soon", "this feature is not", "功能开发中")
    todo_weak = ("todo", "占位", "placeholder")
    if any(k in low for k in todo_strong) or (not success and any(k in low for k in todo_weak)):
        return "TODO"
    if any(k in low for k in ("未连接", "not connected", "设备未", "硬件未", "no device", "未插", "需先连接",
                              "无法打开", "failed to open", "no sdr", "未找到设备", "permission denied",
                              "could not open", " unavailable", "不可用", "请先连接", "not available")):
        return "NEED_HW"
    # 前置资源不存在（子代理/录制/文件/内容段）——工具做了正确的业务校验，不是空壳也不是崩溃
    if any(k in low for k in ("子代理不存在", "录制不存在", "文件不存在", "未找到要替换", "no such",
                              "does not exist", "not found", "不存在", "未找到", "请先创建", "无数据",
                              "无法解析", "格式", "empty", "为空")):
        return "NEED_RESOURCE"
    if error or "traceback" in low:
        return "CRASH"
    if success and txt.strip():
        return "OK"
    return "OTHER"


def main():
    ag = MBDSDRAgent(AgentConfig())
    register_sdr_tools(ag)
    reg = ag.tool_registry

    # 频谱/采集类工具大多要求先有设备；先连一个 mock 设备
    try:
        reg.call("sdr_connect", {"device_id": "mock"})
    except Exception:
        pass

    # 预建文件类工具需要的临时目录/文件
    os.makedirs("/tmp/mbdsdr_param_out", exist_ok=True)
    with open("/tmp/mbdsdr_param_in.bin", "wb") as f:
        f.write(b"\x00" * 256)

    tools = reg.list_tools()
    buckets = collections.defaultdict(list)
    detail = []

    for t in tools:
        name = t.get("name")
        schema = t.get("parameters", {}) or {}
        required = schema.get("required", []) or []
        if not required:
            continue  # 无参工具已在第一轮体检覆盖
        args = build_args(t)
        try:
            res = reg.call(name, args)
            txt = res.content if hasattr(res, "content") else str(res)
            success = getattr(res, "success", False)
            error = getattr(res, "error", "")
            cat = classify(txt, success, error)
            snippet = (txt or str(error))[:90].replace("\n", " ")
        except Exception as e:
            cat, snippet = "CRASH", f"{type(e).__name__}: {e}"[:90]
        buckets[cat].append(name)
        detail.append((name, cat, args, snippet))

    for cat in ("OK", "NEED_HW", "NEED_RESOURCE", "BAD_ARG", "TODO", "CRASH", "OTHER"):
        items = buckets.get(cat, [])
        print(f"=== {cat}: {len(items)} ===")
        if cat in ("NEED_RESOURCE", "BAD_ARG", "TODO", "CRASH", "OTHER"):
            for n in items:
                snip = next((s for x, c, a, s in detail if x == n and c == cat), "")
                print(f"  - {n}: {snip}")
        else:
            for n in items[:15]:
                print(f"  - {n}")
            if len(items) > 15:
                print(f"  ... 另 {len(items)-15} 个")
        print()

    print("汇总:", {k: len(v) for k, v in buckets.items()})

    # 落盘明细，便于下一轮按 BAD_ARG 精确补值
    os.makedirs("paper/experiments", exist_ok=True)
    out = "tests/tool_param_smoke_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({n: {"cat": c, "args": a, "snippet": s} for n, c, a, s in detail},
                  f, ensure_ascii=False, indent=2)
    print("明细已写:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
