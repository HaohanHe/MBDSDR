#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补拍 100-250MHz 段（2m 段 148MHz 谐振）屏幕证据。仅 sweep+capture。"""
import importlib.util
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

W, H = 320, 240
OUT = Path("data")

spec = importlib.util.spec_from_file_location(
    "nvc3", "../../mbdsdr_ai/nanovna_client.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["nvc3"] = mod
spec.loader.exec_module(mod)

c = mod.NanoVNAClient(port="COM11")
c.connect(do_handshake=False)
try:
    c.set_sweep(100_000_000, 250_000_000, 101)
    time.sleep(1.2)
    raw = c.capture_screen(timeout_s=45.0)
    assert len(raw) == W * H * 2, len(raw)
    (OUT / "vna_screen_2m.rgb").write_bytes(raw)
    arr = np.frombuffer(raw, dtype="<u2").reshape(H, W).astype(np.uint32)
    r = ((arr >> 11) & 0x1F) * 255 // 31
    g = ((arr >> 5) & 0x3F) * 255 // 63
    b = (arr & 0x1F) * 255 // 31
    img = np.stack([r, g, b], axis=-1).astype(np.uint8)
    Image.fromarray(img, "RGB").save(OUT / "vna_screen_2m.png")
    Image.fromarray(img, "RGB").resize((W * 2, H * 2), Image.NEAREST).save(
        OUT / "vna_screen_2m_2x.png")
    print(f"2m shot: {len(raw)}B saved")
finally:
    c.close()
print("OK")
