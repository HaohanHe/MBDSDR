"""远程控制确定性测试。

对照 mbdsdr_ai/remote_control.py（上游 gqrx/remote_control.cpp）。
"""
import socket
import time

import pytest

from mbdsdr_ai.remote_control import (
    CLOSE_CONNECTION, RemoteControl, RPRT_ERR, RPRT_OK,
)


class FakeBackend:
    def __init__(self):
        self.freq = 100_000_000
        self.mode = "FM"
        self.gain = 10.0

    def get_freq(self):
        return self.freq

    def set_freq(self, hz):
        self.freq = hz
        return True

    def get_mode(self):
        return self.mode

    def set_mode(self, m):
        self.mode = m
        return True

    def get_gain(self):
        return self.gain

    def set_gain(self, g):
        self.gain = g
        return True


class BrokenBackend:
    """后端方法抛异常——服务器必须吞掉，不能崩。"""

    def get_freq(self):
        raise RuntimeError("boom")

    def set_freq(self, hz):
        raise RuntimeError("boom")


def test_get_set_freq_with_backend():
    be = FakeBackend()
    rc = RemoteControl(backend=be)
    assert rc.process_line("f") == "100000000\n"
    assert rc.process_line("f 145000000") == RPRT_OK
    assert be.freq == 145_000_000
    assert rc.process_line("f") == "145000000\n"


def test_get_set_mode():
    be = FakeBackend()
    rc = RemoteControl(backend=be)
    assert rc.process_line("m") == "FM\n"
    assert rc.process_line("m usb") == RPRT_OK
    assert be.mode == "USB"
    assert rc.process_line("m") == "USB\n"


def test_get_set_gain():
    be = FakeBackend()
    rc = RemoteControl(backend=be)
    assert rc.process_line("g") == "10.0\n"
    assert rc.process_line("g 25.5") == RPRT_OK
    assert be.gain == 25.5


def test_invalid_args_return_rprt1():
    be = FakeBackend()
    rc = RemoteControl(backend=be)
    assert rc.process_line("f abc") == RPRT_ERR
    assert rc.process_line("g xyz") == RPRT_ERR


def test_unknown_command_rprt1():
    rc = RemoteControl(backend=FakeBackend())
    assert rc.process_line("bogus 1") == RPRT_ERR


def test_q_returns_close_sentinel():
    rc = RemoteControl(backend=FakeBackend())
    assert rc.process_line("q") == CLOSE_CONNECTION


def test_no_backend_safe_degradation():
    """无后端时所有命令返回 RPRT 1 但不崩。"""
    rc = RemoteControl(backend=None)
    assert rc.process_line("f") == RPRT_ERR
    assert rc.process_line("f 100000000") == RPRT_ERR
    assert rc.process_line("m") == RPRT_ERR
    assert rc.process_line("m FM") == RPRT_ERR
    assert rc.process_line("g") == RPRT_ERR
    assert rc.process_line("g 10") == RPRT_ERR


def test_broken_backend_does_not_crash():
    rc = RemoteControl(backend=BrokenBackend())
    # 后端抛异常 -> 吞掉返回 RPRT 1，不向外抛
    assert rc.process_line("f") == RPRT_ERR
    assert rc.process_line("f 100") == RPRT_ERR


def test_websocket_handle_multiline():
    be = FakeBackend()
    rc = RemoteControl(backend=be)
    out = rc.ws_handle("f\nm\ng\nq\n")
    assert out == "100000000\nFM\n10.0\n"


def test_tcp_server_end_to_end():
    """真正起 TCP 服务器，用 socket 客户端打通 f/m/g/q。"""
    be = FakeBackend()
    rc = RemoteControl(backend=be, host="127.0.0.1", port=0)
    # port=0 让 OS 分配；但我们需要知道端口——改用动态探测
    import socketserver
    # 直接用一个高端口
    rc = RemoteControl(backend=be, host="127.0.0.1", port=17356)
    rc.start()
    try:
        time.sleep(0.1)
        with socket.create_connection(("127.0.0.1", 17356), timeout=2) as s:
            f = s.makefile("rw")
            f.write("f\n"); f.flush()
            assert f.readline().strip() == "100000000"
            f.write("f 439000000\n"); f.flush()
            assert f.readline().strip() == "RPRT 0"
            f.write("g 12.5\n"); f.flush()
            assert f.readline().strip() == "RPRT 0"
            f.write("g\n"); f.flush()
            assert f.readline().strip() == "12.5"
            f.write("q\n"); f.flush()
    finally:
        rc.stop()
