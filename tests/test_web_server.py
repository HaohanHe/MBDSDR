"""
test_web_server.py — WebServer HTTP / WebSocket 确定性单测。

验证：
  * 无后端时 GET /api/status -> connected=false，/api/spectrum -> connected=false（红线）
  * GET / 状态页 200
  * 接上假后端 + push IQ 后 /api/spectrum 返回真实 bins
  * WS /ws/spectrum 收到二进制频谱帧
  * WS /ws/audio 收到 PCM 帧
"""
import json
import os
import sys
import time
import urllib.request

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.web import WebServer, decode_spectrum_frame, SPECTRUM_MARKER, AUDIO_MARKER


class _FakeBackend:
    def __init__(self, connected=True):
        self.connected = connected


def _get_json(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read().decode())


def test_no_backend_safe_fallback():
    with WebServer(host="127.0.0.1", port=0, backend=None) as srv:
        status = _get_json(f"http://127.0.0.1:{srv.port}/api/status")
        assert status["connected"] is False
        assert status["service"] == "mbdsdr_ai.web"

        spec = _get_json(f"http://127.0.0.1:{srv.port}/api/spectrum")
        assert spec == {"connected": False}


def test_index_page_and_404():
    with WebServer(host="127.0.0.1", port=0, backend=None) as srv:
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/", timeout=5) as r:
            assert r.status == 200
            body = r.read().decode()
            assert "MBDSDR" in body
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/nope", timeout=5)
            assert False, "expected 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404


def test_spectrum_api_after_backend_pushes():
    be = _FakeBackend(connected=True)
    with WebServer(host="127.0.0.1", port=0, backend=be, fft_size=512, target_bins=128) as srv:
        status = _get_json(f"http://127.0.0.1:{srv.port}/api/status")
        assert status["connected"] is True

        # 后端还没推 IQ
        spec = _get_json(f"http://127.0.0.1:{srv.port}/api/spectrum")
        assert spec["connected"] is True
        assert spec["data"] is False

        # 推一帧已知正弦
        n = 512
        t = np.arange(n) / 1_000_000.0
        iq = (0.5 * np.exp(2j * np.pi * 50_000.0 * t)).astype(np.complex64)
        srv.spectrum_streamer.push_iq(iq, 100_000.0, 1_000_000.0, force=True)

        spec = _get_json(f"http://127.0.0.1:{srv.port}/api/spectrum")
        assert spec["connected"] is True and spec["data"] is True
        assert len(spec["bins"]) == 128
        assert spec["center_freq_hz"] == 100_000
        # 峰值在 150kHz 附近
        assert abs(spec["peak_freq_hz"] - 150_000.0) <= 1_000_000 / 128


def test_ws_spectrum_and_audio():
    websockets_sync = pytest.importorskip("websockets.sync.client")
    connect = websockets_sync.connect

    be = _FakeBackend(connected=True)
    srv = WebServer(host="127.0.0.1", port=0, backend=be, fft_size=512, target_bins=128)
    srv.start()
    try:
        # --- spectrum ws ---
        with connect(f"ws://127.0.0.1:{srv.port}/ws/spectrum", open_timeout=5) as ws:
            n = 512
            t = np.arange(n) / 1_000_000.0
            iq = (0.5 * np.exp(2j * np.pi * -20_000.0 * t)).astype(np.complex64)
            srv.spectrum_streamer.push_iq(iq, 100_000.0, 1_000_000.0, force=True)
            msg = ws.recv(timeout=5)
            assert isinstance(msg, (bytes, bytearray))
            assert msg[0] == SPECTRUM_MARKER
            decoded = decode_spectrum_frame(bytes(msg))
            assert len(decoded["bins"]) == 128

        # --- audio ws ---
        with connect(f"ws://127.0.0.1:{srv.port}/ws/audio", open_timeout=5) as ws:
            pcm = (np.ones(256, dtype=np.int16) * 1000)
            srv.audio_streamer.push_audio(pcm)
            msg = ws.recv(timeout=5)
            assert msg[0] == AUDIO_MARKER
            assert len(msg) == 1 + 256 * 2
    finally:
        srv.stop()
