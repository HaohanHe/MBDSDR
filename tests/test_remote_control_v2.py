# SPDX-License-Identifier: MIT
"""RemoteControlV2 确定性测试：GQRX 完整命令集 + 无后端安全降级。"""
import pytest

from mbdsdr_ai.remote_control_v2 import RemoteControlV2
from mbdsdr_ai.remote_control import CLOSE_CONNECTION, RPRT_OK, RPRT_ERR


class FullBackend:
    """实现了 v2 全部 glue 方法的假后端。"""

    def __init__(self):
        self.freq = 100_000_000
        self.mode = "FM"
        self.gain = 10.0
        self.squelch = -50.0
        self.agc = True
        self.dsp = True
        self.recording = False
        self.calls = []

    # freq/mode/gain/squelch
    def get_freq(self): return self.freq
    def set_freq(self, hz): self.freq = hz; return True
    def get_mode(self): return self.mode
    def set_mode(self, m): self.mode = m; return True
    def get_gain(self): return self.gain
    def set_gain(self, g): self.gain = g; return True
    def get_squelch(self): return self.squelch
    def set_squelch(self, db): self.squelch = db; return True
    # dsp / record / agc
    def get_dsp_running(self): return self.dsp
    def set_dsp_running(self, b): self.dsp = b; return True
    def is_recording(self): return self.recording
    def start_recording(self): self.recording = True; return True
    def stop_recording(self): self.recording = False; return True
    def get_agc(self): return self.agc
    def set_agc(self, b): self.agc = b; return True
    def get_version(self): return "FakeBackend 9.9"
    def get_device_list(self):
        return [{"name": "RTL-SDR #0"}, {"name": "HackRF"}]


# ---------------------------------------------------------------------------
# 带后端
# ---------------------------------------------------------------------------
def test_lowercase_get_set_compat():
    be = FullBackend()
    rc = RemoteControlV2(backend=be)
    assert rc.process_line("f") == "100000000\n"
    assert rc.process_line("f 145000000") == RPRT_OK
    assert be.freq == 145_000_000


def test_uppercase_absolute_set():
    be = FullBackend()
    rc = RemoteControlV2(backend=be)
    assert rc.process_line("F 439000000") == RPRT_OK
    assert be.freq == 439_000_000
    assert rc.process_line("M USB") == RPRT_OK
    assert be.mode == "USB"
    assert rc.process_line("G 32.5") == RPRT_OK
    assert be.gain == 32.5
    # 大写无参 -> RPRT 1（设置命令需要参数）
    assert rc.process_line("F") == RPRT_ERR
    assert rc.process_line("M") == RPRT_ERR
    assert rc.process_line("G") == RPRT_ERR


def test_squelch_l_and_L():
    be = FullBackend()
    rc = RemoteControlV2(backend=be)
    assert rc.process_line("l") == "-50.0\n"
    assert rc.process_line("l -60") == RPRT_OK
    assert be.squelch == -60.0
    assert rc.process_line("L -80") == RPRT_OK
    assert be.squelch == -80.0
    assert rc.process_line("L") == RPRT_ERR


def test_dsp_toggle_s():
    be = FullBackend()
    rc = RemoteControlV2(backend=be)
    assert be.dsp is True
    assert rc.process_line("s") == RPRT_OK
    assert be.dsp is False
    assert rc.process_line("s") == RPRT_OK
    assert be.dsp is True


def test_record_toggle_u():
    be = FullBackend()
    rc = RemoteControlV2(backend=be)
    assert be.recording is False
    assert rc.process_line("u") == RPRT_OK
    assert be.recording is True
    assert rc.process_line("u") == RPRT_OK
    assert be.recording is False


def test_agc_A():
    be = FullBackend()
    rc = RemoteControlV2(backend=be)
    assert rc.process_line("A") == "1\n"
    assert rc.process_line("A 0") == RPRT_OK
    assert be.agc is False
    assert rc.process_line("A") == "0\n"


def test_version_v():
    be = FullBackend()
    rc = RemoteControlV2(backend=be)
    assert rc.process_line("v") == "FakeBackend 9.9\n"


def test_device_list_d():
    be = FullBackend()
    rc = RemoteControlV2(backend=be)
    out = rc.process_line("d")
    assert "RTL-SDR #0" in out and "HackRF" in out


def test_q_close():
    rc = RemoteControlV2(backend=FullBackend())
    assert rc.process_line("q") == CLOSE_CONNECTION
    assert rc.process_line("Q") == CLOSE_CONNECTION


def test_unknown_command():
    rc = RemoteControlV2(backend=FullBackend())
    assert rc.process_line("xyz") == RPRT_ERR


# ---------------------------------------------------------------------------
# 无后端：查询返回默认值，设置返回 RPRT 1
# ---------------------------------------------------------------------------
def test_no_backend_queries_return_defaults():
    rc = RemoteControlV2(backend=None)
    assert rc.process_line("f") == "100000000\n"
    assert rc.process_line("m") == "FM\n"
    assert rc.process_line("g") == "0.0\n"
    assert rc.process_line("l") == "-150.0\n"
    assert rc.process_line("A") == "1\n"
    assert "MBDSDR" in rc.process_line("v")
    assert rc.process_line("d") == "[]\n"


def test_no_backend_sets_rprt1():
    rc = RemoteControlV2(backend=None)
    assert rc.process_line("F 100000000") == RPRT_ERR
    assert rc.process_line("M USB") == RPRT_ERR
    assert rc.process_line("G 10") == RPRT_ERR
    assert rc.process_line("L -50") == RPRT_ERR
    assert rc.process_line("l -50") == RPRT_ERR      # 带参=设置
    assert rc.process_line("s") == RPRT_ERR
    assert rc.process_line("u") == RPRT_ERR
    assert rc.process_line("A 0") == RPRT_ERR


def test_broken_backend_no_crash():
    class Broken:
        def get_freq(self): raise RuntimeError("boom")
        def set_freq(self, h): raise RuntimeError("boom")
    rc = RemoteControlV2(backend=Broken())
    # 吞异常，不向外抛
    assert rc.process_line("f")  # 查询 -> 默认值（异常被吞）
    assert rc.process_line("F 100") == RPRT_ERR
