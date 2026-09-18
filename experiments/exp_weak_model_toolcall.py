#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实验3：弱模型 tool-calling 成功率（论文数据）。
用 Qwen3.5-4B，给一批工具 schema，问开放式任务，统计：
  - call_rate: 是否调用了工具（而非纯文本硬答）
  - pick_rate: 是否选中了期望的工具
  - arg_ok: 关键参数是否填对
"""
import sys, os, json, csv, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.WARNING)

from mbdsdr_ai.agent import MBDSDRAgent, AgentConfig
from mbdsdr_ai.sdr_tools import register_sdr_tools

# (问题, 期望工具, 关键参数必须出现)
CASES = [
    ("把呼号 BI4MIB 编成摩尔斯电码", "morse_encode", ["text"]),
    ("我在长春43.8868,125.3245，预测 NOAA 19 最近过境时间", "satellite_predict_pass", ["observer_lat", "observer_lon"]),
    ("用 BI4MIB 在长春43.8868,125.3245 发一条APRS位置", "sdr_aprs_encode", ["source", "latitude"]),
    ("算长春43.8868,125.3245到北京39.9042,116.4074大圆距离", "gis_distance", ["lat1", "lon1"]),
    ("我在长春，现在天上有哪些卫星可见", "sdr_satellite_sky_view", ["latitude", "longitude"]),
    ("分析当前频谱找出峰值频率", "sdr_spectrum_analyze", []),
    ("从88MHz扫到108MHz找最强FM台", "sdr_ai_sweep", ["freq_start_hz", "freq_end_hz"]),
    ("当前频谱里有没有干扰源", "signal_detect_interference", []),
    ("解析这条NMEA: $GNRMC,072545.00,A,4352.0000,N,12519.0000,E,0.0,0.0,250926,,,A*6B", "gnss_parse_rmc", ["nmea"]),
    ("看看L1频段的监测参数", "gnss_monitor_band", ["band_name"]),
]


def main():
    cfg = json.load(open(os.path.expanduser("~/.mbdsdr/config.json")))
    ag = MBDSDRAgent(AgentConfig(api_key=cfg["api_key"], base_url=cfg["api_base"],
                                 model=cfg.get("model", "Qwen/Qwen3.5-4B"), max_output_tokens=400))
    register_sdr_tools(ag)
    # 只暴露本题集合相关工具，降低干扰
    names = {c[1] for c in CASES}
    schemas = [{"type": "function",
                "function": {"name": t["name"], "description": t.get("description", ""),
                             "parameters": t.get("parameters", {})}}
               for t in ag.tool_registry.list_tools() if t["name"] in names]

    rows = []
    call_ok = pick_ok = arg_ok = 0
    for q, expect, must_args in CASES:
        r = ag.model_manager.chat([{"role": "user", "content": q}], tools=schemas, tool_choice="auto")
        tcs = r.get("tool_calls") or []
        called = len(tcs) > 0
        picked = bool(tcs) and tcs[0].get("function", {}).get("name") == expect
        arg_fill = False
        if picked:
            try:
                args = json.loads(tcs[0]["function"]["arguments"])
                arg_fill = all(a in args for a in must_args)
            except Exception:
                arg_fill = False
        call_ok += called; pick_ok += picked; arg_ok += (picked and arg_fill)
        rows.append({"task": q[:30], "expected": expect, "called": called,
                     "picked_expected": picked, "args_ok": arg_fill})
        print(f"[{'CALL' if called else 'NONE'}] [{'PICK' if picked else 'WRONG'}] [{'ARG' if arg_fill else '-'}] {expect}")

    n = len(CASES)
    print(f"\n调用率 {call_ok}/{n}={100*call_ok//n}%  选对工具 {pick_ok}/{n}={100*pick_ok//n}%  填对参数 {arg_ok}/{n}={100*arg_ok//n}%")
    os.makedirs("paper/experiments", exist_ok=True)
    out = "paper/experiments/weak_model_toolcall.csv"
    with open(out, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=["task", "expected", "called", "picked_expected", "args_ok"])
        wr.writeheader(); wr.writerows(rows)
    print("已写", out)


if __name__ == "__main__":
    main()
