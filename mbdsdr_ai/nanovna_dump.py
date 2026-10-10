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
                    "请用 --port 显式指定串口，并确认 USB 已连、udev/dialout 权限。"
                )
            port = found[0][0]
        client = NanoVNAClient(port=port)

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

    return {
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


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="NanoVNA 真机一键 dump")
    ap.add_argument("--port", default="", help="串口路径，如 /dev/ttyACM0；缺省自动探测")
    ap.add_argument("--start", type=int, default=430_000_000, help="扫频起点 Hz")
    ap.add_argument("--stop", type=int, default=440_000_000, help="扫频终点 Hz")
    ap.add_argument("--points", type=int, default=101, help="扫频点数")
    ap.add_argument("--out", default="-", help="输出 JSON 路径；- 为 stdout")
    ap.add_argument("--replay", default=None, help="测试用：自造协议脚本 JSON（不碰硬件）")
    args = ap.parse_args(argv)

    try:
        data = run_dump(args.port, args.start, args.stop, args.points, args.replay)
    except NanoVNADriverError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1

    text = json.dumps(data, ensure_ascii=False, indent=2)
    if args.out == "-":
        print(text)
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"已写出 {args.out}（{len(data['frequencies_hz'])} 点）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
