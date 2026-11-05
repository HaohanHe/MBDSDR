#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""热插拔（拔线）半自动验收：人做物理动作，脚本负责观察、计时与判定。

三种后端的热插拔路径不同，必须分开验：

  --mode usb        本地 RTL-SDR（RTLSDRBackend）：拔 / 插 USB
  --mode rtl-tcp    远程 rtl_tcp：拔网线（半开）或杀服务端进程（RST/FIN）
  --mode spyserver  远程 SpyServer：拔网线（半开）或重启服务端进程

脚本**不碰硬件动作**：由操作者按倒计时拔线/插回，脚本只轮询后端状态并记录
时间戳，最后按 ``docs/hw-acceptance.md`` 的判据给 PASS/FAIL 并写 result.json。

用法（PowerShell）：

    # 先看计划与判据，不产生任何动作（无人拔线时的实跑口径）
    python scripts/hw_acceptance/manual_usb_cycle.py --mode usb

    # 真跑（需要人守在设备旁）
    python scripts/hw_acceptance/manual_usb_cycle.py --mode rtl-tcp \
        --host 127.0.0.1 --port 1234 --interactive

    python scripts/hw_acceptance/manual_usb_cycle.py --mode spyserver \
        --host 127.0.0.1 --port 7355 --cut server --interactive

无 ``--interactive`` 或标准输入不是终端时，输出 ``SKIPPED(no_operator)`` 并
写明原因；无设备/无端点分别输出 ``SKIPPED(no_device|no_endpoint)``。
退出码：FAIL=1，PASS/SKIP=0（SKIP 不算通过）。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
for _p in (_REPO, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import common  # noqa: E402  （同目录公共层：结果信封 / 设备枚举 / 产物目录）

from mbdsdr_ai.rtltcp_client import RtlTcpBackend        # noqa: E402
from mbdsdr_ai.sdr_backend import RTLSDRBackend          # noqa: E402
from mbdsdr_ai.spyserver_client import SpyServerBackend  # noqa: E402

#: 判据上限与来源。数字来自本机实测（docs/network-layer.md §0/§2/§7）：
#: 进程被杀 RST 检出 0.000s；正常 FIN 0.103s(rtl)/0.098s(spy)；
#: 半开 5s 阈值 stale 5.004/5.010s、硬判 10.024/10.027s；
#: 换端口 connect→首帧 0.022s/0.020s。
#: ``UNVERIFIED_EXT`` = 沿用同量级实测上限，USB/重插路径尚未真机实测，
#: 第一次真机跑完后用实测值校准。
LIMITS = {
    ("usb", "usb"): dict(detect=0.5, stale=None, first_frame=1.0,
                         source="UNVERIFIED_EXT：沿用 RST 0.000s / FIN 0.103s 的 "
                                "0.5s 上限；USB 拔插未真机实测"),
    ("rtl-tcp", "cable"): dict(detect=11.0, stale=5.5, first_frame=1.0,
                               source="实测：stale 5.004s / 硬判 10.034s；"
                                      "重插首帧按换端口 0.022s 取 1.0s 上限"),
    ("rtl-tcp", "server"): dict(detect=0.5, stale=None, first_frame=1.0,
                                source="实测：RST 0.000s / FIN 0.103s；"
                                       "首帧按换端口 0.022s 取 1.0s 上限"),
    ("spyserver", "cable"): dict(detect=11.0, stale=5.5, first_frame=1.0,
                                 source="实测：stale 5.010s / 硬判 10.027s；"
                                        "重插首帧沿用 rtl 换端口 0.020s 的 1.0s 上限"
                                        "（spy 未单独计时，UNVERIFIED_EXT）"),
    ("spyserver", "server"): dict(detect=0.5, stale=None, first_frame=1.0,
                                  source="实测：RST 0.000s / FIN 0.098s；"
                                         "首帧同上，UNVERIFIED_EXT"),
}

_CUT_WORDS = {
    "usb": "拔掉 RTL-SDR 的 USB 线",
    "cable": "拔掉网线（或禁用网卡），保持设备供电",
    "server": "停止 rtl_tcp / spyserver 服务端进程",
}
_PLUG_WORDS = {
    "usb": "把 USB 线插回",
    "cable": "把网线插回",
    "server": "重新启动服务端进程",
}


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=["usb", "rtl-tcp", "spyserver"],
                    default="usb")
    ap.add_argument("--cut", choices=["usb", "cable", "server"], default="")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--interactive", action="store_true",
                    help="启用人工提示（无人守着时不要加）")
    ap.add_argument("--cut-timeout", type=float, default=25.0)
    ap.add_argument("--replug-timeout", type=float, default=30.0)
    ap.add_argument("--countdown", type=float, default=5.0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.out:
        os.environ["MBDSDR_HW_ACCEPTANCE_DIR"] = args.out
    if not args.cut:
        args.cut = "usb" if args.mode == "usb" else "cable"
    if args.port == 0:
        args.port = 1234 if args.mode == "rtl-tcp" else 7355
    if args.mode == "usb" and args.cut != "usb":
        ap.error("--mode usb 只能用 --cut usb")
    if args.mode != "usb" and args.cut == "usb":
        ap.error("网络后端请用 --cut cable|server")
    return args


def _skip(mode: str, code: str, detail: str, limits: dict) -> int:
    res = common.result(
        "HOTPLUG", f"热插拔-{mode}", f"SKIPPED({code})",
        metrics={"reason_code": code, "limits": limits},
        checks=[common.check("operator_present", False, detail, skipped=True)],
        user_message_on_failure=detail,
        human_required=[f"{_CUT_WORDS[limits.get('cut', 'usb')]}（人工）"],
        device_required=mode == "usb",
        notes="未执行任何拔插动作；本结果不产生耗时数字，也不冒充 PASS。")
    return common.emit(res, common.artifact_dir(f"hotplug_{mode}"))


def _make_backend(args):
    if args.mode == "usb":
        return RTLSDRBackend(device_index=0)
    if args.mode == "rtl-tcp":
        return RtlTcpBackend(args.host, args.port)
    return SpyServerBackend(args.host, args.port)


def _first_frame(be, n: int = 4096, timeout: float = 5.0):
    """返回 (frame, elapsed)；超时返回 (None, None)。"""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        try:
            s = be.read_samples(n)
        except Exception:
            s = None
        if s is not None and len(s) == n:
            return s, time.perf_counter() - t0
        time.sleep(0.02)
    return None, None


def _countdown(seconds: float) -> None:
    print(f"  {int(seconds)} 秒倒计时，结束瞬间执行动作……", flush=True)
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        time.sleep(0.05)
    print("  现在动手！", flush=True)


def _wait_cut(be, timeout: float):
    """等到 connected=False。计时以**最后一帧真实数据**为 T0，不含人工延迟。

    返回 (stale_s, detect_s)：两者都是「静默持续了多久」，所以直接可与
    实测的 5s/10s 心跳判据对齐（操作者晚按几秒不会污染数字）。
    """
    last_ok = time.perf_counter()
    t_stale = None
    while time.perf_counter() - last_ok < timeout:
        try:
            s = be.read_samples(4096)
        except Exception:
            s = None
        now = time.perf_counter()
        if s is not None and len(s) == 4096:
            last_ok = now
        if t_stale is None and be.status.link_state == "stale":
            t_stale = now - last_ok
        if be.status.connected is False:
            return t_stale, now - last_ok
        time.sleep(0.02)
    return t_stale, None


def _wait_replug(be, timeout: float):
    """插回后等待首帧，必要时重连。

    返回 (首帧软件延迟, 人工等待总时长, 是否重连)。软件延迟从「重连成功」
    起算；没有重连（TCP 未断）时退回从倒计时结束起算。
    """
    t0 = time.perf_counter()
    last_try = -1.0
    t_link = None
    reconnected = False
    while time.perf_counter() - t0 < timeout:
        now = time.perf_counter()
        if be.status.connected is False and now - last_try > 0.5:
            last_try = now
            try:
                ok = be.connect()
            except Exception:
                ok = False
            if ok:
                t_link = time.perf_counter()
                reconnected = True
        try:
            s = be.read_samples(4096)
        except Exception:
            s = None
        if s is not None and len(s) == 4096:
            now = time.perf_counter()
            latency = now - (t_link if t_link is not None else t0)
            return latency, now - t0, reconnected
        time.sleep(0.05)
    return None, None, reconnected


def main() -> int:
    args = _parse_args()
    limits = LIMITS[(args.mode, args.cut)]
    limits = dict(limits, cut=args.cut)

    print(f"[hotplug] mode={args.mode} cut={args.cut} "
          f"endpoint={args.host}:{args.port if args.mode != 'usb' else '-'}")
    print(f"[hotplug] 判据：检出 ≤{limits['detect']}s"
          + (f"、stale ≤{limits['stale']}s" if limits["stale"] else "")
          + f"、插回首帧 ≤{limits['first_frame']}s")
    print(f"[hotplug] 来源：{limits['source']}")

    if not args.interactive:
        return _skip(args.mode, "no_operator",
                     "未启用 --interactive：没有操作者执行物理拔插，按红线 SKIP",
                     limits)
    if not sys.stdin.isatty():
        return _skip(args.mode, "no_operator",
                     "标准输入不是终端：无法提示人工拔插", limits)
    if os.environ.get("MBDSDR_NO_HW") == "1":
        return _skip(args.mode, "no_hw_env",
                     "MBDSDR_NO_HW=1：本会话禁止触碰真实设备/链路", limits)
    if args.mode == "usb" and common.rtl_device_count() == 0:
        return _skip(args.mode, "no_device",
                     "librtlsdr 未枚举到 RTL-SDR；无法执行 USB 拔插", limits)

    be = _make_backend(args)
    try:
        ok = be.connect()
    except Exception as exc:
        ok = False
        print(f"[hotplug] connect 异常：{type(exc).__name__}: {exc}")
    if not ok:
        try:
            be.disconnect()
        except Exception:
            pass
        return _skip(args.mode, "no_endpoint",
                     f"{args.mode} {args.host}:{args.port} 建连失败；"
                     "没有可拔的链路，不能产生数字", limits)

    frame, t_ready = _first_frame(be, timeout=5.0)
    if frame is None:
        be.disconnect()
        return _skip(args.mode, "no_stream",
                     "建连成功但 5s 内没有首帧；不是健康链路，拒绝开始计时",
                     limits)
    print(f"[hotplug] 基线：首帧 {t_ready:.3f}s 已到，准备拔线")
    print(f"[hotplug] 步骤 1：{_CUT_WORDS[args.cut]}（倒计时结束后动手）")
    try:
        _countdown(args.countdown)
        t_stale, t_detect = _wait_cut(be, args.cut_timeout)
    except KeyboardInterrupt:
        be.disconnect()
        return _skip(args.mode, "operator_cancel",
                     "操作者中断：拔线阶段未完成，不产生判定", limits)
    detect_ok = t_detect is not None and t_detect <= limits["detect"]
    stale_ok = (limits["stale"] is None or
                (t_stale is not None and t_stale <= limits["stale"]))
    print(f"[hotplug] 检出：stale={t_stale if t_stale is None else round(t_stale, 3)}s "
          f"disconnected={t_detect if t_detect is None else round(t_detect, 3)}s "
          f"category={be.status.error_category or '-'} "
          f"link_state={be.status.link_state or '-'}")

    if t_detect is None:
        be.disconnect()
        res = common.result(
            "HOTPLUG", f"热插拔-{args.mode}", common.FAIL,
            metrics={"stale_s": t_stale, "detect_s": None, "limits": limits,
                     "error_category": be.status.error_category},
            checks=[common.check("cut_detected", False,
                                 f"{args.cut_timeout}s 内没有转 disconnected")],
            user_message_on_failure="拔线没有被检出：链路状态一直显示在线，可能心跳失效。",
            notes="未检出断开，后续插回步骤没有执行。")
        return common.emit(res, common.artifact_dir(f"hotplug_{args.mode}"))

    print(f"[hotplug] 步骤 2：{_PLUG_WORDS[args.cut]}（倒计时结束后动手）")
    try:
        _countdown(args.countdown)
        t_first, t_human, reconnected = _wait_replug(be, args.replug_timeout)
    except KeyboardInterrupt:
        be.disconnect()
        return _skip(args.mode, "operator_cancel",
                     "操作者中断：插回阶段未完成，不产生判定", limits)
    first_ok = t_first is not None and t_first <= limits["first_frame"]
    print(f"[hotplug] 恢复：首帧软件延迟="
          f"{t_first if t_first is None else round(t_first, 3)}s "
          f"(含人工等待 {t_human if t_human is None else round(t_human, 3)}s) "
          f"reconnect_used={reconnected}")

    category = be.status.error_category or ""
    category_ok = True
    if args.mode != "usb" and args.cut == "server":
        category_ok = category in ("eof", "reset", "idle", "unknown")

    checks = [
        common.check("frames_before_cut", frame is not None,
                     "拔线前已有真实首帧"),
        common.check("cut_detected_within_limit", detect_ok,
                     f"detect={t_detect:.3f}s ≤ {limits['detect']}s"
                     if t_detect is not None else "未检出"),
        common.check("stale_within_limit", stale_ok,
                     "不适用（直接断开而非半开）" if limits["stale"] is None
                     else f"stale={t_stale}s ≤ {limits['stale']}s"),
        common.check("state_disconnected", be.status.connected is False or first_ok,
                     f"link_state={be.status.link_state}"),
        common.check("category_sane", category_ok, f"category={category or '-'}"),
        common.check("first_frame_after_replug", first_ok,
                     f"first={t_first}s ≤ {limits['first_frame']}s"),
    ]
    status = common.PASS if all(c["ok"] for c in checks) else common.FAIL
    res = common.result(
        "HOTPLUG", f"热插拔-{args.mode}", status,
        metrics={"stale_s": t_stale, "detect_s": t_detect,
                 "first_frame_s": t_first, "human_wait_s": t_human,
                 "reconnect_used": reconnected,
                 "error_category": category, "limits": limits,
                 "cut": args.cut, "endpoint": f"{args.host}:{args.port}"},
        checks=checks,
        user_message_on_failure="拔线后状态或恢复时间超出实测上限，见 metrics 与 checks。",
        human_required=[f"{_CUT_WORDS[args.cut]}（人工）",
                        f"{_PLUG_WORDS[args.cut]}（人工）"],
        device_required=args.mode == "usb",
        notes="物理动作由操作者完成；脚本只观察与计时，判据上限见 limits.source。")
    try:
        be.disconnect()
    except Exception:
        pass
    return common.emit(res, common.artifact_dir(f"hotplug_{args.mode}"))


if __name__ == "__main__":
    sys.exit(main())
