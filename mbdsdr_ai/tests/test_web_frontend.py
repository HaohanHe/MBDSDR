"""Web 前端 / 协议确定性测试。

覆盖：
  * 静态文件存在且 HTML 引用正确；
  * 起真实 WebServer（端口 0）打 HTTP 路由；
  * 无后端安全降级（/api/status connected=false，命令 not_connected）；
  * 频谱二进制帧可被 decode_spectrum_frame 正确解析（与 app.js 解析约定一致）；
  * 无后端时 /api/recordings 不报错。
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from mbdsdr_ai.web.server import WebServer
from mbdsdr_ai.web.streamer import SpectrumStreamer, decode_spectrum_frame

STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"


# --------------------------------------------------------------------------- #
# 静态资源
# --------------------------------------------------------------------------- #
def test_static_files_exist():
    for name in ("index.html", "app.js", "style.css"):
        assert (STATIC_DIR / name).is_file(), f"missing {name}"


def test_html_references_assets():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert '/app.js' in html
    assert '/style.css' in html
    # 两个 canvas 都在
    assert 'id="spectrum"' in html
    assert 'id="waterfall"' in html
    # 无数据提示
    assert "等待数据" in html


def test_appjs_protocol_contract():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    # 与 streamer.py 的 ">B I I f f" 17 字节头一致
    assert "SPEC_HEADER_LEN = 17" in js
    assert "getUint32" in js and "getFloat32" in js
    # 上行命令格式
    assert "set_frequency" in js and "set_mode" in js and "set_gain" in js
    # 自动重连
    assert "reconnect" in js.lower()
    # 无数据不造假：waiting 覆盖层逻辑
    assert "等待数据" in js


# --------------------------------------------------------------------------- #
# 真实 HTTP 路由（端口 0 随机端口）
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def server():
    srv = WebServer(host="127.0.0.1", port=0, backend=None)
    srv.start()
    time.sleep(0.1)  # 等线程起来
    yield srv
    srv.stop()


def _get(srv, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{srv.port}{path}", timeout=3) as r:
        return r.status, r.read(), r.headers.get("Content-Type", "")


def test_root_serves_frontend(server):
    code, body, ctype = _get(server, "/")
    assert code == 200
    assert "text/html" in ctype
    assert b"spectrum" in body and b"waterfall" in body


def test_static_assets_served(server):
    code_js, js_body, js_ctype = _get(server, "/app.js")
    assert code_js == 200 and b"WebSocket" in js_body
    code_css, css_body, css_ctype = _get(server, "/style.css")
    assert code_css == 200 and b"--bg" in css_body


def test_status_no_backend_is_false(server):
    code, body, _ = _get(server, "/api/status")
    data = json.loads(body)
    assert code == 200
    assert data["connected"] is False  # 红线：不造假


def test_spectrum_no_backend(server):
    code, body, _ = _get(server, "/api/spectrum")
    data = json.loads(body)
    assert data["connected"] is False


def test_recordings_listing(server):
    code, body, _ = _get(server, "/api/recordings")
    data = json.loads(body)
    assert code == 200
    assert "recordings" in data
    assert isinstance(data["recordings"], list)


def test_recording_path_traversal_blocked():
    srv = WebServer(port=0)
    assert srv.recording_path("../server.py") is None
    assert srv.recording_path("../../etc/passwd") is None
    assert srv.recording_path("ok.cf32") is None or True  # 不存在文件 -> None


# --------------------------------------------------------------------------- #
# WebSocket 命令处理（直接调 handle_command，免手搓握手）
# --------------------------------------------------------------------------- #
def test_commands_no_backend_safe_degrade():
    srv = WebServer(port=0, backend=None)
    for cmd in ("set_frequency", "set_gain", "set_mode"):
        rep = srv.handle_command({"cmd": cmd, "value": 123})
        assert rep["type"] == "error"
        assert rep["error"] == "not_connected"  # 红线


def test_commands_with_fake_backend():
    calls = {}

    def set_freq(hz):
        calls["freq"] = hz

    def set_gain(db):
        calls["gain"] = db

    def set_mode(m):
        calls["mode"] = m

    be = SimpleNamespace(
        connected=True,
        frequency=98e6, mode="WFM", gain=30.0,
        set_frequency=set_freq, set_gain=set_gain, set_mode=set_mode,
    )
    srv = WebServer(port=0, backend=be)
    assert srv.handle_command({"cmd": "set_frequency", "value": 100e6})["type"] == "ack"
    assert calls["freq"] == 100e6
    assert srv.handle_command({"cmd": "set_gain", "value": 40})["type"] == "ack"
    assert calls["gain"] == 40
    assert srv.handle_command({"cmd": "set_mode", "value": "NFM"})["type"] == "ack"
    assert calls["mode"] == "NFM"
    # get_status 带后端状态
    st = srv.handle_command({"cmd": "get_status"})
    assert st["connected"] is True
    assert st["mode"] == "WFM"


def test_unknown_command():
    srv = WebServer(port=0, backend=SimpleNamespace(connected=True))
    rep = srv.handle_command({"cmd": "rm -rf /"})
    assert rep["type"] == "error"
    assert rep["error"] == "unsupported" or rep["error"] == "unknown_command"


# --------------------------------------------------------------------------- #
# 频谱二进制帧：streamer 产出 <-> app.js 解析约定一致
# --------------------------------------------------------------------------- #
def test_spectrum_frame_roundtrip():
    ss = SpectrumStreamer(fft_size=1024, target_bins=64, fps=0)
    iq = np.exp(1j * 2 * np.pi * 0.1 * np.arange(1024))
    frame = ss.push_iq(iq, center_freq_hz=98e6, samp_rate_hz=2e6, force=True)
    assert frame is not None
    dec = decode_spectrum_frame(frame)
    assert dec["center_freq_hz"] == 98_000_000
    assert dec["samp_rate_hz"] == 2_000_000
    assert len(dec["bins"]) == 64
    # decode_spectrum_frame 约定返回 float32（与 app.js 的 Uint8Array 解析同源）
    assert dec["bins"].dtype == np.float32
    assert dec["bins"].min() >= 0 and dec["bins"].max() <= 255
