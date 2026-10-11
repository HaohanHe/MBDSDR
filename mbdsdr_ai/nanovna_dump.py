#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""NanoVNA 真机一键 dump：连接 -> sweep -> read S11/S21 -> 落 JSON。

CLI（真实串口，无设备即诚实报错，不伪造）：
    python3 -m mbdsdr_ai.nanovna_dump --port /dev/ttyACM0 --out sweep.json
    python3 -m mbdsdr_ai.nanovna_dump          # 自动探测端口（VID:PID 提示）

测试用 replay 模式：--replay script.json 走 ReplayTransport，不碰硬件。
输出 JSON：model/version/sn/cal/sweep/frequencies/S11/S21/vswr/return_loss。
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Tuple

from .nanovna_client import (
    NanoVNAClient,
    ReplayTransport,
    NanoVNADriverError,
    find_nanovna_ports,
    vswr,
    return_loss_db,
)


def run_dump(port: str, start_hz: int, stop_hz: int, points: int,
             replay: str | None = None) -> dict:
    """执行一次完整 sweep 采集，返回可序列化 dict。无设备抛错。"""
    if replay:
        with open(replay, "r", encoding="utf-8") as f:
            script: List[Tuple[str, List[str]]] = [
                (cmd, list(resp)) for cmd, resp in json.load(f)
            ]
        client = NanoVNAClient(transport=ReplayTransport(script))
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
        try:
            client = NanoVNAClient(port=port)
            client.connect()
        except (OSError, NanoVNADriverError) as e:
            raise SystemExit(
                f"无法打开串口 {port}：{e}\n"
                "排查：(1) 端口是否存在；(2) 是否在 dialout 组 / udev 权限；"
                "(3) 是否已被其它程序占用。"
            ) from e

    client.connect()
    try:
        client.read_cal_status()
        client.set_sweep(start_hz, stop_hz, points)
        freqs = client.read_frequencies()
        s11 = client.read_data(0)
        s21 = client.read_data(1)
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
        "port": port or "replay",
        "sweep": {"start_hz": start_hz, "stop_hz": stop_hz, "points": points},
        "frequencies_hz": freqs,
        "S11": [[c.real, c.imag] for c in s11],
        "S21": [[c.real, c.imag] for c in s21],
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
    return out


def _selftest_script(start_hz: int, stop_hz: int, points: int):
    """合成演示脚本（标注合成，非真实测量）：跑通连接→sweep→data 形状校验。"""
    freqs = [start_hz + (stop_hz - start_hz) * i // max(1, points - 1)
             for i in range(points)]
    return [
        ["help", []],
        ["version", ["selftest-1.0"]],
        ["info", ["NanoVNA-H(selftest)"]],
        ["cal", ["load open short thru cal'ed"]],
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
    args = ap.parse_args(argv)

    replay = args.replay
    if args.selftest:
        import tempfile, os
        fd, replay = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_selftest_script(args.start, args.stop, args.points), f)

    try:
        data = run_dump(args.port, args.start, args.stop, args.points, replay)
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
