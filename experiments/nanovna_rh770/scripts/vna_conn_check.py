#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""连通性/状态检查：100-250MHz（2m 段）+ 250-380MHz（补全中间段）。
安全纪律：仅 sweep + data 0，禁 scan/pause/reset。"""
import importlib.util
import sys
import time

import numpy as np

spec = importlib.util.spec_from_file_location(
    "nvc2", "../../mbdsdr_ai/nanovna_client.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["nvc2"] = mod
spec.loader.exec_module(mod)

c = mod.NanoVNAClient(port="COM11")
c.connect(do_handshake=False)
try:
    for f0, f1 in [(100e6, 250e6), (250e6, 380e6)]:
        c.set_sweep(int(f0), int(f1), 101)
        time.sleep(1.2)
        lines = c._exec("data 0", read_timeout=0.05)
        pts = []
        for ln in lines:
            p = ln.split()
            if len(p) == 2:
                try:
                    pts.append(complex(float(p[0]), float(p[1])))
                except ValueError:
                    pass
        n = len(pts)
        print(f"=== {f0 / 1e6:.0f}-{f1 / 1e6:.0f}MHz  pts={n}")
        if n < 101:
            continue
        rl = [-20.0 * np.log10(abs(g)) if abs(g) > 0 else 99.0 for g in pts]
        fr = [f0 + i * (f1 - f0) / (n - 1) for i in range(n)]
        step = max(1, n // 15)
        print("  profile:", " ".join(
            f"{fr[i] / 1e6:.0f}:{rl[i]:.0f}" for i in range(0, n, step)))
        best = max(range(n), key=lambda i: rl[i])
        print(f"  best: {fr[best] / 1e6:.2f}MHz  RL={rl[best]:.1f}dB")
finally:
    c.close()
print("OK")
