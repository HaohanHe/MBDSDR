"""Offscreen 渲染截图：起 WebServer，push 几帧 IQ，playwright 截图。"""
import sys, time, threading, json
import numpy as np

sys.path.insert(0, "/home/user/Doubao/chats/38438160041798146")
from mbdsdr_ai.web.server import WebServer

class FakeBackend:
    connected = True
    frequency = 98_000_000
    mode = "WFM"
    gain = 30.0

    def set_frequency(self, hz): self.frequency = hz
    def set_gain(self, db): self.gain = db
    def set_mode(self, m): self.mode = m

be = FakeBackend()
srv = WebServer(host="127.0.0.1", port=8765, backend=be, fft_size=1024, target_bins=256, fps=10)
srv.start()

# 模拟后端：在 98MHz 中心上放两个窄带峰 + 噪声
def feeder():
    t = np.arange(1024)
    base = np.random.default_rng(0).normal(0, 0.05, 1024) + 1j*np.random.default_rng(1).normal(0, 0.05, 1024)
    for _ in range(40):
        sig = base.copy()
        sig += 0.9 * np.exp(1j*2*np.pi*0.15*t)   # 峰1
        sig += 0.7 * np.exp(1j*2*np.pi*0.35*t)   # 峰2
        srv.spectrum_streamer.push_iq(sig.astype(np.complex64), 98e6, 2e6)
        time.sleep(0.1)

threading.Thread(target=feeder, daemon=True).start()
time.sleep(0.5)

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/local/bin/chromium", args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    pg.on("console", lambda m: print("JS:", m.text))
    pg.on("pageerror", lambda e: print("PAGEERR:", e))
    pg.goto(f"http://127.0.0.1:{srv.port}/")
    pg.wait_for_timeout(2500)
    pg.screenshot(path="/home/user/Doubao/chats/38438160041798146/mbdsdr_ai/artifacts/web_spectrum.png", full_page=True)
    print("shot saved")
    b.close()
srv.stop()
