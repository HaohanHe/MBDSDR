#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VNA 屏幕截图取证 + 70cm 段数值锚定。

安全纪律（前序会话实测教训，硬性）：
- 禁发 scan / scan 0 / reset / pause —— 裸 scan = 持续流模式，会卡死设备
- sweep <start> <stop> <pts> 本身就是扫描触发器，发一次出一遍新扫
- capture 协议（实测固件 1.2.50）：回显行 -> 153600B RGB565(320x240 小端)
  -> "ch> " 提示符，115200 下约 13.4s
本脚本同时真机验证仓内 NanoVNAClient.capture_screen 代码路径。
"""
import importlib.util
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

CLIENT_PATH = "../../mbdsdr_ai/nanovna_client.py"
OUT = Path("data")
PORT = "COM11"
W, H = 320, 240


def load_client():
    spec = importlib.util.spec_from_file_location("nanovna_client_ev", CLIENT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["nanovna_client_ev"] = mod  # dataclass 装饰器需要模块已注册
    spec.loader.exec_module(mod)
    return mod


def rgb565_to_image(raw: bytes) -> Image.Image:
    arr = np.frombuffer(raw, dtype="<u2").reshape(H, W)
    r = ((arr >> 11) & 0x1F).astype(np.uint8) * 255 // 31
    g = ((arr >> 5) & 0x3F).astype(np.uint8) * 255 // 63
    b = (arr & 0x1F).astype(np.uint8) * 255 // 31
    return Image.fromarray(np.stack([r, g, b], axis=-1), "RGB")


def shot(client, tag: str) -> Image.Image:
    t0 = time.time()
    raw = client.capture_screen(timeout_s=45.0)
    dt = time.time() - t0
    if len(raw) != W * H * 2:
        raise RuntimeError(f"帧长异常 {len(raw)} != {W * H * 2}")
    (OUT / f"vna_screen_{tag}.rgb").write_bytes(raw)
    img = rgb565_to_image(raw)
    img.save(OUT / f"vna_screen_{tag}.png")
    img.resize((W * 2, H * 2), Image.NEAREST).save(OUT / f"vna_screen_{tag}_2x.png")
    gray = np.asarray(img.convert("L"))
    print(f"[{tag}] 帧 {len(raw)}B / {dt:.1f}s，非暗像素 {100 * (gray > 40).mean():.1f}%")
    return img


def read_s11(client):
    lines = client._exec("data 0", read_timeout=0.05)
    pts = []
    for ln in lines:
        p = ln.split()
        if len(p) == 2:
            try:
                pts.append(complex(float(p[0]), float(p[1])))
            except ValueError:
                pass
    return pts


def main():
    mod = load_client()
    client = mod.NanoVNAClient(port=PORT)
    client.connect(do_handshake=True)
    try:
        print("version:", client.version)
        print("features:", sorted(client.features))
        print("设备内cal:", client.get_cal_status())

        # 1) 现场：用户此刻屏幕上的内容（原样保留）
        shot(client, "live")

        # 2) 全景：250-450MHz（70cm 段全貌，含 408 坑与 435 参照点）
        client.set_sweep(250_000_000, 450_000_000, 101)
        time.sleep(1.0)
        shot(client, "wide")

        # 3) 特写：380-440MHz（坑位特写）+ data 0 回读做数值锚定
        client.set_sweep(380_000_000, 440_000_000, 101)
        time.sleep(1.0)
        shot(client, "detail")
        pts = read_s11(client)
        print(f"回读 S11 点: {len(pts)}")
        if len(pts) >= 101:
            f0, f1, n = 380e6, 440e6, 101
            rl = [-20.0 * np.log10(abs(g)) if abs(g) > 0 else 99.0 for g in pts]
            freqs = [f0 + i * (f1 - f0) / (n - 1) for i in range(n)]
            order = sorted(range(n), key=lambda i: rl[i])[:5]
            print("最深5点 (MHz, RL_dB):")
            for i in order:
                print(f"  {freqs[i] / 1e6:8.2f}  {rl[i]:7.2f}")
            i435 = min(range(n), key=lambda i: abs(freqs[i] - 435e6))
            print(f"435MHz 最近点: {freqs[i435] / 1e6:.2f}MHz  RL={rl[i435]:.2f}dB")
    finally:
        client.close()
    print("OK")


if __name__ == "__main__":
    main()
