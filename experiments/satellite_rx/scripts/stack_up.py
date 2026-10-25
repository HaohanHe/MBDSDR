#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
stack_up.py — MBDSDR 接收栈的守护式启动器（稳定性基础设施）
================================================================
解决的问题：mbdsdr.exe 长时间运行后 ControlHub(50732) 会卡死
（进程活着、端口无响应、内存持续上涨），遥控入口随之报废。

本脚本：
  1. 一键拉起整套栈：rtl_tcp（带频率/采样率）→ mbdsdr.exe → ControlHub 连接
  2. 持续健康巡检（/status 探活）
  3. 发现卡死：自动杀 rtl_tcp+mbdsdr → 重启 → 重连 → 恢复频率/采样率/模式
  4. 状态落盘（stack_state.json），重启后按它恢复

用法：
  python stack_up.py --freq 137.1 --rate 1024000 --mode NFM     # 起栈并守护
  python stack_up.py --freq 137.1 --no-guard                     # 只起栈不守护
  python stack_up.py --check                                      # 只体检一次
  python stack_up.py --stop                                       # 全停
"""
import argparse, json, os, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "stack_state.json")
RTL_TCP = r"C:\rtl-sdr\bin\rtl_tcp.exe"
MBDSDR = r"D:\mbdsdr\MBDSDR\cpp\build-local\mbdsdr.exe"
BASE = "http://127.0.0.1:50732"
ENV_EXTRA = {"PATH": r"C:\rtl-sdr\bin;C:\QtLocal\Tools\mingw1310_64\bin;"
                     r"C:\QtLocal\6.8.2\mingw_64\bin;" + os.environ.get("PATH", "")}


def ctl(tool, args=None, timeout=12):
    body = json.dumps({"tool": tool, "args": args or {}}).encode()
    try:
        req = urllib.request.Request(BASE + "/command", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": str(e)}


def alive():
    try:
        with urllib.request.urlopen(BASE + "/", timeout=6) as r:
            return r.status == 200
    except Exception:
        return False


def kill(proc):
    subprocess.run(["taskkill", "/F", "/IM", proc],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start_stack(freq_hz, rate_hz, detached=False):
    kill("rtl_tcp.exe"); kill("mbdsdr.exe")
    time.sleep(1.5)
    # DETACHED_PROCESS 只用于 --no-guard（父进程会退出，否则子进程被带走）。
    # **守护模式必须不 detach**：detached 进程失去正常 GUI 渲染上下文，
    # 瀑布/频谱画出来是黑的（数据在、ringCount_ 正常，但渲染上下文坏了）
    # ——今天排查 25+ 轮的终极大坑。
    DETACHED = 0x00000008
    flags = DETACHED if detached else 0
    subprocess.Popen([RTL_TCP, "-a", "127.0.0.1", "-p", "1234",
                      "-s", str(int(rate_hz)), "-f", str(int(freq_hz))],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     env={**os.environ, **ENV_EXTRA},
                     creationflags=flags)
    time.sleep(2.0)
    subprocess.Popen([MBDSDR], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     cwd=os.path.dirname(MBDSDR),
                     env={**os.environ, **ENV_EXTRA},
                     creationflags=flags)
    for _ in range(30):
        if alive():
            break
        time.sleep(1.0)
    r = ctl("connect_network_source", {"host": "127.0.0.1", "port": 1234}, timeout=25)
    return r


def apply_colormap():
    """起栈后自动刷色图（绕过 UI 启动加载时序 bug）。

    UI 的构造/Re-apply 加载持久化色图实测不生效（下拉还显示"经典"），
    运行时 set_color_map 一定生效——这里补上启动路径。
    """
    import json as _json
    st = load_state()
    p = r"D:\mbdsdr\output\ariss\sdrpp_style.json"
    try:
        reg = _json.load(open(r"registry_view.json")) if False else None
    except Exception:
        pass
    # 直接从 Windows 注册表读 view/wfColormapFile（PowerShell 不可用时回退默认）
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\mbdsdr\MBDSDR\view")
        p = winreg.QueryValueEx(k, "wfColormapFile")[0] or p
        winreg.CloseKey(k)
    except Exception:
        pass
    r = ctl("set_color_map", {"file_path": p}, timeout=20)
    return r.get("ok", False), p


def save_state(freq_hz, rate_hz, mode):
    json.dump({"freq_hz": int(freq_hz), "rate_hz": int(rate_hz), "mode": mode},
              open(STATE, "w"))


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    return {"freq_hz": 137100000, "rate_hz": 1024000, "mode": "NFM"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--freq", default="137.1")
    ap.add_argument("--rate", default="1024000")
    ap.add_argument("--mode", default="NFM")
    ap.add_argument("--no-guard", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--interval", type=float, default=15.0, help="巡检间隔秒")
    a = ap.parse_args()

    if a.stop:
        kill("mbdsdr.exe"); kill("rtl_tcp.exe")
        print("[stop] 全停")
        return
    if a.check:
        print("[check] ControlHub", "活" if alive() else "死")
        r = ctl("get_status", {}, timeout=8)
        print("[check]", json.dumps(r, ensure_ascii=False)[:220])
        return

    def hz(s):
        s = str(s).strip().lower()
        if s.endswith("m"): return float(s[:-1]) * 1e6
        if s.endswith("k"): return float(s[:-1]) * 1e3
        v = float(s)
        return v * 1e6 if v < 1e4 else v

    f, r_ = hz(a.freq), hz(a.rate)
    save_state(f, r_, a.mode.upper())
    print(f"[up] 起栈：rtl_tcp({f/1e6:.4f}MHz,{r_/1e6:.3f}MS/s) + mbdsdr")
    res = start_stack(f, r_, detached=a.no_guard)
    print("[up] connect ->", res.get("ok"), res.get("error", ""))
    if res.get("ok"):
        ctl("set_sample_rate", {"rate_hz": int(r_)})
        ctl("tune", {"freq_hz": int(f)})
        ctl("set_mode", {"mode": a.mode.upper()})
        cm_ok, cm_path = apply_colormap()
        print(f"[up] 色图 {cm_path} -> {'ok' if cm_ok else 'fail'}")
        st = ctl("get_status", {}, timeout=10)
        print(f"[up] 就绪：freq={st.get('readback_center_hz')} "
              f"rate={st.get('readback_sample_rate_hz')} mode={st.get('mode')}")
    if a.no_guard:
        return

    print(f"[guard] 开始巡检（每 {a.interval}s）… Ctrl+C 停")
    fails = 0
    n = 0
    while True:
        time.sleep(a.interval)
        n += 1
        if alive():
            fails = 0
            st = ctl("get_status", {}, timeout=10)
            if not st.get("ok"):
                fails += 1
            else:
                if n % 20 == 0:
                    print(f"[guard] {time.strftime('%H:%M:%S')} 正常 "
                          f"freq={st.get('readback_center_hz')}")
                continue
        else:
            fails += 1
        if fails >= 2:
            s = load_state()
            print(f"[guard] {time.strftime('%H:%M:%S')} ControlHub 卡死({fails}次) → 重启恢复")
            res = start_stack(s["freq_hz"], s["rate_hz"])
            if res.get("ok"):
                ctl("set_sample_rate", {"rate_hz": int(s["rate_hz"])})
                ctl("tune", {"freq_hz": int(s["freq_hz"])})
                ctl("set_mode", {"mode": s["mode"]})
                apply_colormap()
                print("[guard] 已恢复")
            fails = 0


if __name__ == "__main__":
    main()
