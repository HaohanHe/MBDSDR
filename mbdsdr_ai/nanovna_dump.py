#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""NanoVNA 真机一键 dump：连接 -> sweep -> read S11/S21 -> 落 JSON。

CLI（真实串口，无设备即诚实报错，不伪造）：
    python3 -m mbdsdr_ai.nanovna_dump --port /dev/ttyACM0 --out sweep.json
    python3 -m mbdsdr_ai.nanovna_dump --screenshot screen.bmp --out sweep.json
    python3 -m mbdsdr_ai.nanovna_dump          # 自动探测端口（VID:PID 提示）

真机串口打开带**有界重试**（默认 4 次、间隔 2.5s）：消化 usbser.sys 偶发
PermissionError(13) 等瞬时失败；全部失败仍诚实 SystemExit（不伪造数据）。
测试用 replay 模式：--replay script.json 走 ReplayTransport，不碰硬件、不重试。
输出 JSON：model/version/sn/cal/tcxo_hz/sweep/frequencies/S11/S21/vswr/return_loss。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import List, Tuple

from .nanovna_client import (
    NanoVNAClient,
    ReplayTransport,
    NanoVNADriverError,
    find_nanovna_ports,
    s11_phase_deg,
    s21_phase_deg,
    vswr,
    return_loss_db,
)

# 真机（非 replay）串口打开的有界重试参数：
# Windows usbser.sys 对已枚举/刚重置的 CDC 设备偶发 PermissionError(13)
# 等瞬时失败，重试一次往往即可自愈。取值：重试 4 次（含首次共 5 次尝试）、
# 固定间隔 2.5s（任务给定 2-3s 退避区间取中值），最坏多等约 10s，全部失败
# 仍诚实 SystemExit（带最后一次错误，不伪造数据）。
# 两个参数均可注入：测试传 open_retries=1 / retry_delay_s=0.0 做到零等待。
OPEN_MAX_RETRIES = 4
OPEN_RETRY_DELAY_S = 2.5


def run_dump(port: str, start_hz: int, stop_hz: int, points: int,
             replay: str | None = None, screenshot: str | None = None,
             open_retries: int = OPEN_MAX_RETRIES,
             retry_delay_s: float = OPEN_RETRY_DELAY_S) -> dict:
    """执行一次完整 sweep 采集，返回可序列化 dict。无设备抛错。

    screenshot 非空时在采集完成后抓取设备屏幕（capture 命令，RGB565 -> BMP
    写出到该路径）；固件不支持 / 传输失败时该项为诚实错误信封，不影响测量数据。

    真机路径的串口打开带**有界重试**：open_retries 次重试（含首次共
    1+open_retries 次尝试）、相邻尝试间隔 retry_delay_s 秒，用于消化
    usbser.sys 偶发 PermissionError(13) 等瞬时失败；全部失败仍保持诚实
    失败（SystemExit 带最后一次错误信息，不伪造数据）。replay 路径无硬件
    时序问题，完全不重试、不加等待。
    """
    if replay:
        with open(replay, "r", encoding="utf-8") as f:
            script: List[Tuple[str, List[str]]] = [
                (cmd, list(resp)) for cmd, resp in json.load(f)
            ]
        client = NanoVNAClient(transport=ReplayTransport(script))
        client.connect()
    else:
        if not port:
            found = find_nanovna_ports()
            if not found:
                raise SystemExit(
                    "未探测到 NanoVNA（VID:PID 0x0483:0x5740）。\n"
                    "请用 --port 显式指定串口（Linux /dev/ttyACM0，Windows COMx），"
                    "并确认 USB 已连、udev/dialout 权限。"
                )
            port = found[0][0]
        client = _open_with_retry(port, open_retries=open_retries,
                                  retry_delay_s=retry_delay_s)

    try:
        client.read_cal_status()
        tcxo_hz = client.read_tcxo()
        client.set_sweep(start_hz, stop_hz, points)
        freqs = client.read_frequencies()
        s11 = client.read_data(0)
        s21 = client.read_data(1)
        shot = None
        if screenshot:
            shot = _capture_screenshot(client, screenshot)
    finally:
        client.close()

    if len(freqs) != len(s11):
        raise SystemExit(
            f"数据形状不一致：freqs={len(freqs)} S11={len(s11)}，"
            "请检查设备是否完成扫频。"
        )

    out = {
        "model": client.info_lines[0] if client.info_lines else "unknown",
        "version": client.version,
        "sn": client.sn,
        "cal": list(client.cal_status),
        "tcxo_hz": tcxo_hz,
        "port": port or "replay",
        "sweep": {"start_hz": start_hz, "stop_hz": stop_hz, "points": points},
        "frequencies_hz": freqs,
        "S11": [[c.real, c.imag] for c in s11],
        "S21": [[c.real, c.imag] for c in s21],
        # 官方 11 种迹线格式中之 PHASE 的 Python 侧接线（此前 s21_phase_deg/
        # s11_phase_deg 已存在却未进任何输出——半适配，2026-10-13 转正）。
        "s11_phase_deg": [round(s11_phase_deg(c), 2) for c in s11],
        "s21_phase_deg": [round(s21_phase_deg(c), 2) for c in s21],
        "vswr": [round(vswr(c), 3) for c in s11],
        "return_loss_db": [round(return_loss_db(c), 2) for c in s11],
    }
    # 摘要行：一眼看最低驻波点
    v = [vswr(c) for c in s11]
    k = min(range(len(v)), key=lambda i: v[i])
    out["summary"] = {
        "min_vswr": round(v[k], 3),
        "at_hz": freqs[k] if k < len(freqs) else 0,
    }
    if shot is not None:
        out["screenshot_bmp"] = shot
    return out


def _open_with_retry(port: str, *, open_retries: int,
                     retry_delay_s: float) -> NanoVNAClient:
    """打开真机串口并握手；打开失败有界重试，仍失败 -> SystemExit（诚实失败）。

    open_retries：首次失败后的最大重试次数（总尝试 = 1 + open_retries）。
    retry_delay_s：相邻两次尝试之间的间隔（秒；<=0 表示不退避，测试注入用）。
    每次失败都会打印串口诊断（含当前可见的 NanoVNA 候选端口）；SystemExit
    携带**最后一次**错误信息，绝不伪造数据。
    """
    attempts = max(1, int(open_retries) + 1)  # 首次 + N 次重试
    client: NanoVNAClient | None = None
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            client = NanoVNAClient(port=port)
            client.connect()
            return client
        except (OSError, NanoVNADriverError) as e:
            if client is not None:
                try:
                    client.close()  # 释放半开/半握手的传输层再重试
                except Exception:  # 清理失败不掩蔽真实错误
                    pass
                client = None
            last_err = e
            _diagnose_open_failure(port, e, attempt, attempts)
            if attempt < attempts and retry_delay_s > 0:
                time.sleep(retry_delay_s)
    raise SystemExit(
        f"无法打开串口 {port}：{last_err}\n"
        "排查：(1) 端口是否存在；(2) 是否在 dialout 组 / udev 权限；"
        "(3) 是否已被其它程序占用。"
    ) from last_err


def _diagnose_open_failure(port: str, err: Exception,
                           attempt: int, attempts: int) -> None:
    """串口打开失败的一次轻量诊断（仅真机路径，replay 不触发）。

    打印失败明细，并列出当前可见的 NanoVNA 候选端口
    （mbdsdr_ai.nanovna_client.find_nanovna_ports()，VID:PID
    0x0483:0x5740 枚举）；没有候选就是空列表——诚实呈现，不猜端口。
    """
    print(f"[nanovna_dump] 第 {attempt}/{attempts} 次打开 {port} 失败：{err}",
          file=sys.stderr)
    try:
        found = find_nanovna_ports()
    except Exception as e:  # 诊断分支自己不许再炸（诚实报告枚举失败）
        print(f"[nanovna_dump] NanoVNA 候选枚举失败：{e}", file=sys.stderr)
        return
    if found:
        cand = ", ".join(f"{p} (VID:PID {vid:04X}:{pid:04X})"
                         for p, vid, pid in found)
    else:
        cand = "（未枚举到 NanoVNA；确认 USB 已连、设备已被系统识别）"
    print(f"[nanovna_dump] 当前可见 NanoVNA 候选（VID:PID 0483:5740）：{cand}",
          file=sys.stderr)


def _capture_screenshot(client, path: str) -> dict:
    """抓取设备屏幕并写 BMP。失败返回诚实错误信封（不伪造截图）。"""
    from .nanovna_client import CAPTURE_HEIGHT, CAPTURE_WIDTH, rgb565_to_bmp_bytes
    try:
        raw = client.capture_screen()
        bmp = rgb565_to_bmp_bytes(raw, CAPTURE_WIDTH, CAPTURE_HEIGHT)
        with open(path, "wb") as f:
            f.write(bmp)
        return {"ok": True, "path": path, "bytes": len(bmp),
                "width": CAPTURE_WIDTH, "height": CAPTURE_HEIGHT}
    except Exception as e:  # noqa: BLE001 - 诚实信封：截图失败不拖垮测量
        return {"ok": False, "error": str(e)}


def _selftest_script(start_hz: int, stop_hz: int, points: int):
    """合成演示脚本（标注合成，非真实测量）：跑通连接→sweep→data 形状校验。"""
    freqs = [start_hz + (stop_hz - start_hz) * i // max(1, points - 1)
             for i in range(points)]
    return [
        ["help", []],
        ["version", ["selftest-1.0"]],
        ["info", ["NanoVNA-H(selftest)"]],
        ["cal", ["load open short thru cal'ed"]],
        ["tcxo", ["usage: tcxo {TCXO frequency(Hz)}", "current: 26000000"]],
        [f"sweep {int(start_hz)} {int(stop_hz)} {int(points)}", []],
        ["frequencies", [str(f) for f in freqs]],
        ["data 0", ["0.3 0.0"] * points],
        ["data 1", ["0.1 0.0"] * points],
    ]


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="NanoVNA 真机一键 dump")
    ap.add_argument("--port", default="",
                    help="串口路径：Linux 如 /dev/ttyACM0；Windows 如 COM3。"
                         "缺省自动探测")
    ap.add_argument("--start", type=int, default=430_000_000, help="扫频起点 Hz")
    ap.add_argument("--stop", type=int, default=440_000_000, help="扫频终点 Hz")
    ap.add_argument("--points", type=int, default=101, help="扫频点数")
    ap.add_argument("--out", default="-", help="输出 JSON 路径；- 为 stdout")
    ap.add_argument("--replay", default=None, help="测试用：自造协议脚本 JSON（不碰硬件）")
    ap.add_argument("--selftest", action="store_true",
                    help="无设备自检：用合成回放跑通全流程（数据为合成演示，非真实测量）")
    ap.add_argument("--json-pretty", action="store_true",
                    help="缩进 2 输出 JSON（便于阅读/回传）；缺省紧凑单行")
    ap.add_argument("--cal", action="store_true",
                    help="dump 时在 stderr 直显校准状态（open/short/load/thru 逐项）")
    ap.add_argument("--screenshot", default="",
                    help="抓取设备屏幕（capture 命令，RGB565 320x240）转存为 BMP "
                         "到该路径；失败为诚实错误信封，不影响测量数据")
    args = ap.parse_args(argv)

    replay = args.replay
    if args.selftest:
        import tempfile, os
        fd, replay = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_selftest_script(args.start, args.stop, args.points), f)

    try:
        data = run_dump(args.port, args.start, args.stop, args.points, replay,
                        screenshot=args.screenshot or None)
    except NanoVNADriverError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1

    if args.cal:
        print("cal: " + " | ".join(data.get("cal", [])), file=sys.stderr)

    text = json.dumps(data, ensure_ascii=False,
                      indent=2 if args.json_pretty else None)
    if args.out == "-":
        print(text)
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"已写出 {args.out}（{len(data['frequencies_hz'])} 点）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
