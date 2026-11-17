#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Web 端到端冒烟：启动 → 状态 → 一帧频谱 → 关闭。

默认使用调试信号源（无硬件也可跑），端口交给操作系统分配，避免占用 8000。
每一步打印一行 JSON 证据；任一步失败打印原因并非零退出。

用法：
    python scripts/web_smoke.py
    python scripts/web_smoke.py --device debug --timeout 15
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.core import SDRController  # noqa: E402
from mbdsdr_ai.web.server import WebServer  # noqa: E402


class SmokeError(RuntimeError):
    """冒烟失败（带可读原因）。"""


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def _get_json(base: str, path: str) -> dict:
    # 显式禁用代理：本机回环不应被系统/环境代理劫持（有前科）。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(base + path, timeout=3) as resp:
            body = resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise SmokeError(f"GET {path} 失败: {e}") from e
    try:
        data = json.loads(body)
    except ValueError as e:
        raise SmokeError(f"GET {path} 返回非 JSON: {e}") from e
    if not isinstance(data, dict):
        raise SmokeError(f"GET {path} 返回的不是对象: {type(data).__name__}")
    return data


def run(device: str, timeout: float) -> int:
    ctrl = SDRController()
    server: WebServer | None = None
    try:
        ctrl.connect(device)
        ctrl.set_frequency(98_500_000)
        ctrl.set_demod("WFM")
        ctrl.set_sample_rate(2_400_000)

        server = WebServer(host="127.0.0.1", port=0, backend=ctrl)
        server.start()
        base = f"http://127.0.0.1:{server.port}"
        _emit({"step": "start", "url": base, "device": device,
               "is_debug_source": bool(ctrl.get_status().is_debug_source)})

        status = _get_json(base, "/api/status")
        if status.get("connected") is not True:
            raise SmokeError(
                f"/api/status 未报告 connected=true: {status.get('connected')!r}")
        _emit({"step": "status", **status})

        deadline = time.monotonic() + max(1.0, float(timeout))
        spectrum = None
        while time.monotonic() < deadline:
            spectrum = _get_json(base, "/api/spectrum")
            if spectrum.get("connected") is True and spectrum.get("data") is True:
                break
            iq = ctrl.read_iq(4096)
            if iq is None or getattr(iq, "size", 0) < 64:
                raise SmokeError("调试源未产生 IQ 样本")
            server.spectrum_streamer.push_iq(
                iq, ctrl.get_frequency(), ctrl.get_sample_rate(), force=True)
            time.sleep(0.05)
        else:
            raise SmokeError(
                f"{timeout:g}s 内未取到频谱帧: {spectrum!r}" if spectrum
                else f"{timeout:g}s 内未取到频谱响应")

        bins = spectrum.get("bins")
        if not isinstance(bins, list) or not bins:
            raise SmokeError(f"频谱帧 bins 为空: {type(bins).__name__}")
        _emit({
            "step": "spectrum",
            "bins": len(bins),
            "center_freq_hz": spectrum.get("center_freq_hz"),
            "samp_rate_hz": spectrum.get("samp_rate_hz"),
            "peak_freq_hz": spectrum.get("peak_freq_hz"),
            "db_min": spectrum.get("db_min"),
            "db_max": spectrum.get("db_max"),
        })
        return 0
    except Exception as e:
        print(f"web_smoke FAILED: {e}", file=sys.stderr, flush=True)
        return 1
    finally:
        if server is not None:
            server.stop()
            _emit({"step": "stop", "web_stopped": True})
        ctrl.shutdown()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="MBDSDR Web 端到端冒烟")
    p.add_argument("--device", default="debug",
                   help="SDR 设备（默认 debug 调试信号源；无硬件时用它）")
    p.add_argument("--timeout", type=float, default=10.0,
                   help="等待首帧频谱的最长秒数（默认 10）")
    args = p.parse_args(argv)
    return run(args.device, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
