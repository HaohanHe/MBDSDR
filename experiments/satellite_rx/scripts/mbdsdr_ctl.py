#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
mbdsdr_ctl.py — MBDSDR 桌面端统一遥控入口（大工具形态）
=========================================================
把 ControlHub(50732) 的 60+ 命令封装成顺手的小命令，屏蔽参数名坑
（freq_hz/rate_hz/muted 各自不同），内置重试（HTTP 时通时断）。

用法：
  python mbdsdr_ctl.py status                      # 引擎状态（connected/freq/rate）
  python mbdsdr_ctl.py connect                     # 连本机 rtl_tcp:1234
  python mbdsdr_ctl.py tune 137.1                  # 调谐(MHz，支持 137.1m/137100000)
  python mbdsdr_ctl.py rate 1024000                # 采样率(Hz，支持 1.024m)
  python mbdsdr_ctl.py gain 30                     # 增益(dB)
  python mbdsdr_ctl.py mode am|nfm|wfm|usb|lsb|cw|dsb
  python mbdsdr_ctl.py mute on|off
  python mbdsdr_ctl.py record start|stop
  python mbdsdr_ctl.py scan 144 148 25              # 扫频(MHz, 步进kHz)
  python mbdsdr_ctl.py vfo add 137.1 --label NOAA   # 加 VFO
  python mbdsdr_ctl.py doppler on --sat "NOAA 19"  # 多普勒补偿
  python mbdsdr_ctl.py snapshot                    # 导出频谱截图
  python mbdsdr_ctl.py raw <tool> '<json args>'    # 任意命令逃生门
"""
import argparse, json, sys, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:50732"


def post(tool, args, retries=4):
    body = json.dumps({"tool": tool, "args": args}).encode()
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(BASE + "/command", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e
            time.sleep(0.8 * (i + 1))
    return {"ok": False, "error": f"ControlHub 无响应({retries}次重试): {last}"}


def get(path="/status", retries=3):
    for i in range(retries):
        try:
            with urllib.request.urlopen(BASE + path, timeout=15) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e
            time.sleep(0.8 * (i + 1))
    return {"ok": False, "error": f"ControlHub 无响应: {last}"}


def hz(s):
    """频率解析：'137.1'/'137.1m'/'137100000'/'137.1M' 都吃"""
    s = str(s).strip().lower()
    if s.endswith("m"):
        return float(s[:-1]) * 1e6
    if s.endswith("k"):
        return float(s[:-1]) * 1e3
    v = float(s)
    return v * 1e6 if v < 1e4 else v


def show(r):
    if not r.get("ok", False):
        print(f"[fail] {r.get('error', r)}")
        sys.exit(1)
    keys = {k: v for k, v in r.items() if k not in ("ok", "command")}
    print(f"[ok] {r.get('command', '')} " + "  ".join(f"{k}={v}" for k, v in keys.items()))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    pc = sub.add_parser("connect")
    pc.add_argument("--host", default="127.0.0.1")
    pc.add_argument("--port", type=int, default=1234)

    p = sub.add_parser("tune"); p.add_argument("freq_hz")
    p = sub.add_parser("rate"); p.add_argument("rate_hz")
    p = sub.add_parser("gain"); p.add_argument("gain_db", type=float)
    p = sub.add_parser("mode"); p.add_argument("mode")
    p = sub.add_parser("mute"); p.add_argument("onoff", choices=["on", "off"])
    sub.add_parser("record").add_argument("action", choices=["start", "stop"])
    p = sub.add_parser("scan"); p.add_argument("start_mhz", type=float)
    p.add_argument("stop_mhz", type=float); p.add_argument("step_khz", type=float)
    p = sub.add_parser("vfo"); p.add_argument("action", choices=["add", "select", "remove"])
    p.add_argument("freq_hz"); p.add_argument("--label", default="")
    p = sub.add_parser("doppler"); p.add_argument("onoff", choices=["on", "off"])
    p.add_argument("--sat", default="")
    sub.add_parser("snapshot")
    p = sub.add_parser("raw"); p.add_argument("tool"); p.add_argument("args", nargs="?", default="{}")

    a = ap.parse_args()

    if a.cmd == "status":
        r = get()
        if not r.get("ok"):
            print(f"[fail] {r.get('error')}"); sys.exit(1)
        print(f"connected={r.get('connected')}  freq={r.get('readback_center_hz')} Hz  "
              f"rate={r.get('readback_sample_rate_hz')} Hz  gain={r.get('readback_gain_db')} dB")
        print(f"source={r.get('source')}  status={r.get('status')}  mode={r.get('mode')}")
        # telemetry 状态：遥测可用性 + 锁定/EVM 质量（数字模式判决依据）
        tel = r.get("telemetry_available")
        dopp = "on" if r.get("doppler_enabled") else "off"
        if r.get("doppler_available"):
            dopp += "(可用)"
        print(f"telemetry={'可用' if tel else '不可用'}  "
              f"carrier_locked={r.get('carrier_locked')}  "
              f"symbol_locked={r.get('symbol_locked')}  "
              f"evm={r.get('evm_percent')}%  doppler={dopp}")
        if r.get("error_message"):
            print(f"error_message={r.get('error_message')}")
        return
    if a.cmd == "connect":
        show(post("connect_network_source", {"host": a.host, "port": a.port}))
    elif a.cmd == "tune":
        show(post("tune", {"freq_hz": int(hz(a.freq_hz))}))
    elif a.cmd == "rate":
        show(post("set_sample_rate", {"rate_hz": int(hz(a.rate_hz))}))
    elif a.cmd == "gain":
        show(post("set_gain", {"gain_db": a.gain_db}))
    elif a.cmd == "mode":
        show(post("set_mode", {"mode": a.mode.upper()}))
    elif a.cmd == "mute":
        show(post("set_muted", {"muted": a.onoff == "on"}))
    elif a.cmd == "record":
        show(post("start_recording" if a.action == "start" else "stop_recording", {}))
    elif a.cmd == "scan":
        show(post("scan_band", {"start_hz": int(a.start_mhz * 1e6),
                                "stop_hz": int(a.stop_mhz * 1e6),
                                "step_hz": int(a.step_khz * 1e3)}))
    elif a.cmd == "vfo":
        if a.action == "add":
            show(post("vfo_add", {"freq_hz": int(hz(a.freq_hz)), "label": a.label}))
        elif a.action == "select":
            show(post("vfo_select", {"vfo_id": int(a.freq_hz)}))
        else:
            show(post("vfo_remove", {"vfo_id": int(a.freq_hz)}))
    elif a.cmd == "doppler":
        show(post("set_doppler_compensation",
                  {"enabled": a.onoff == "on", "satellite": a.sat}))
    elif a.cmd == "snapshot":
        show(post("export_spectrum_snapshot", {}))
    elif a.cmd == "raw":
        try:
            args = json.loads(a.args)
        except json.JSONDecodeError as e:
            print(f"[fail] args 不是合法 JSON: {e}")
            print(f"      收到: {a.args}")
            print("       正确姿势: python mbdsdr_ctl.py raw set_anr '{\"enabled\": true}'")
            print("       （对象用 {} 包起来，外层单引号防 shell 吃掉引号）")
            sys.exit(1)
        if not isinstance(args, dict):
            print(f"[fail] args 须是 JSON 对象（{{\"tool参数\": 值}}），收到 {type(args).__name__}")
            sys.exit(1)
        show(post(a.tool, args))


if __name__ == "__main__":
    main()
