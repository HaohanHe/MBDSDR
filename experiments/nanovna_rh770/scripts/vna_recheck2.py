#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VNA 截帧复查（uint32 安全转换）+ 70cm 段谐振重定位（380-550MHz）。"""
import importlib.util
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

W, H = 320, 240
VNA_DATA = Path("data")


def conv(raw: bytes) -> np.ndarray:
    arr = np.frombuffer(raw, dtype="<u2").reshape(H, W).astype(np.uint32)
    r = ((arr >> 11) & 0x1F) * 255 // 31
    g = ((arr >> 5) & 0x3F) * 255 // 63
    b = (arr & 0x1F) * 255 // 31
    return np.stack([r, g, b], axis=-1).astype(np.uint8)


def preview(tag: str) -> None:
    raw = (VNA_DATA / f"vna_screen_{tag}.rgb").read_bytes()
    img8 = conv(raw)
    Image.fromarray(img8, "RGB").save(VNA_DATA / f"vna_screen_{tag}.png")
    Image.fromarray(img8, "RGB").resize((W * 2, H * 2), Image.NEAREST).save(
        VNA_DATA / f"vna_screen_{tag}_2x.png")
    gray = (img8[..., 0].astype(np.uint32) * 299
            + img8[..., 1].astype(np.uint32) * 587
            + img8[..., 2].astype(np.uint32) * 114) // 1000
    print(f"--- {tag}: gray max={gray.max()} lit(>40)={100 * (gray > 40).mean():.1f}%")
    small = gray.reshape(24, 10, 64, 5).mean(axis=(1, 3))
    chars = " .:-=+*#%@"
    for row in small:
        print("  " + "".join(chars[min(9, int(v / 26))] for v in row))


def load_client():
    spec = importlib.util.spec_from_file_location(
        "nvc_ev", "../../mbdsdr_ai/nanovna_client.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["nvc_ev"] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    for tag in ("live", "wide", "detail"):
        preview(tag)

    print("=== 侦察 380-550MHz ===")
    mod = load_client()
    c = mod.NanoVNAClient(port="COM11")
    c.connect(do_handshake=False)
    try:
        f0, f1, n = 380e6, 550e6, 101
        c.set_sweep(int(f0), int(f1), n)
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
        print("pts:", len(pts))
        if len(pts) >= 101:
            rl = [-20.0 * np.log10(abs(g)) if abs(g) > 0 else 99.0 for g in pts]
            fr = [f0 + i * (f1 - f0) / (n - 1) for i in range(n)]
            order = sorted(range(n), key=lambda i: rl[i])[:8]
            print("最深8点 (MHz, RL_dB):")
            for i in order:
                print(f"  {fr[i] / 1e6:8.2f}  {rl[i]:7.2f}")
            step = max(1, n // 18)
            print("profile:", " ".join(
                f"{fr[i] / 1e6:.0f}:{rl[i]:.0f}" for i in range(0, n, step)))
    finally:
        c.close()
    print("OK")


if __name__ == "__main__":
    main()
