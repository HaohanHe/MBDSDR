# SPDX-License-Identifier: MIT
"""NetworkIQBackend 接线与契约（审计交接单 B 节 P2）。

旧病灶：完整实现却没进 _discover()/enumerate/工厂；connect() 不写
status（拒绝时还原样抛异常）；get_status() 每次新建对象丢掉 error 等状态；
set_* 返回 None 违反 bool 契约；TCP EOF 后缓冲里的整样本永久卡死。
全部用本地回环/关闭端口确定性验证，无硬件、无外网。
"""
import socket
import threading

import numpy as np
import pytest

import mbdsdr_ai.sdr_backend as sb


ENV_VALUE = "tcp://127.0.0.1:1234?rate=2.4e6&freq=100e6"


def _tcp_server(payload: bytes, port_holder):
    """回环 TCP 服务端：accept 后发完 payload 即 shutdown(WR)（制造 EOF）。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port_holder.append(srv.getsockname()[1])

    def run():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            try:
                conn.sendall(payload)
                conn.shutdown(socket.SHUT_WR)
                while conn.recv(4096):
                    pass
                conn.close()
            except OSError:
                pass

    threading.Thread(target=run, daemon=True).start()
    return srv


def _closed_port() -> int:
    """占位即关的端口：回环 connect 必吃 RST（确定性拒绝）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _backend(port=1234, **kw):
    params = dict(protocol="tcp", host="127.0.0.1", port=port,
                  sample_rate_hz=2.4e6, center_freq_hz=100e6)
    params.update(kw)
    return sb.NetworkIQBackend(**params)


class TestParseEnv:
    def test_empty_is_empty(self):
        assert sb.parse_network_iq_env("") == []
        assert sb.parse_network_iq_env(None) == []
        assert sb.parse_network_iq_env(" , ,") == []

    def test_full_tcp_entry(self):
        out = sb.parse_network_iq_env(
            "tcp://10.0.0.5:1234?rate=2048000&freq=100000000&fmt=cs16&byteorder=big")
        assert out == [dict(protocol="tcp", host="10.0.0.5", port=1234,
                            sample_rate_hz=2048000.0, center_freq_hz=100000000.0,
                            fmt="cs16", byteorder="big")]

    def test_udp_defaults(self):
        out = sb.parse_network_iq_env("udp://0.0.0.0:9000?rate=1e6&freq=144e6")
        assert len(out) == 1
        assert out[0]["protocol"] == "udp"
        assert out[0]["fmt"] == "cu8" and out[0]["byteorder"] == "little"
        assert out[0]["sample_rate_hz"] == 1e6

    def test_multiple_entries(self):
        out = sb.parse_network_iq_env(
            "tcp://h1:1?rate=1e6&freq=1e6, udp://h2:2?rate=2e6&freq=2e6")
        assert [c["host"] for c in out] == ["h1", "h2"]

    @pytest.mark.parametrize("bad", [
        "tcp://h:1",                            # 缺 rate/freq（不许猜默认）
        "tcp://h:1?rate=1e6",                   # 缺 freq
        "http://h:1?rate=1e6&freq=1e6",         # 未知协议
        "tcp://h:0?rate=1e6&freq=1e6",          # 端口 0
        "tcp://h:99999?rate=1e6&freq=1e6",      # 端口越界
        "tcp://:1?rate=1e6&freq=1e6",           # 空主机
        "tcp://h:1?rate=-5&freq=1e6",           # 负采样率
        "tcp://h:1?rate=abc&freq=1e6",          # 非数字
        "tcp://h:1?rate=1e6&freq=1e6&fmt=c64",  # 未知格式
        "tcp://h:1?rate=1e6&freq=1e6&byteorder=mid",
    ])
    def test_garbage_skipped_not_faked(self, bad):
        assert sb.parse_network_iq_env(bad) == []

    def test_mixed_keeps_only_good(self):
        out = sb.parse_network_iq_env("bad:x, tcp://h:1?rate=1e6&freq=1e6")
        assert len(out) == 1 and out[0]["host"] == "h"


class TestRegistration:
    def test_discover_registers_from_env(self, monkeypatch):
        monkeypatch.setenv(sb.NETWORK_IQ_ENV, ENV_VALUE)
        mgr = sb.SDRBackendManager()
        dev = [d for d in mgr.list_devices()
               if d["device_id"] == "net_tcp_127.0.0.1_1234"]
        assert len(dev) == 1
        assert dev[0]["type"] == "network_iq"
        assert dev[0]["connected"] is False   # 登记 != 连接

    def test_discover_without_env_registers_nothing(self, monkeypatch):
        monkeypatch.delenv(sb.NETWORK_IQ_ENV, raising=False)
        mgr = sb.SDRBackendManager()
        assert not [d for d in mgr.list_devices() if d["type"] == "network_iq"]

    def test_discover_garbage_env_registers_nothing(self, monkeypatch):
        monkeypatch.setenv(sb.NETWORK_IQ_ENV, "tcp://h:1, http://x:1?rate=1&freq=1")
        mgr = sb.SDRBackendManager()
        assert not [d for d in mgr.list_devices() if d["type"] == "network_iq"]

    def test_enumerate_includes_env_entry(self, monkeypatch):
        monkeypatch.setenv(sb.NETWORK_IQ_ENV, ENV_VALUE)
        devs = sb.enumerate_all_sdr_devices()
        ent = [d for d in devs if d.get("driver") == "network_iq"]
        assert len(ent) == 1
        args = ent[0]["device_args"]
        assert args["host"] == "127.0.0.1" and args["port"] == 1234
        assert args["sample_rate_hz"] == 2.4e6

    def test_factory_builds_network_iq(self, monkeypatch):
        monkeypatch.setenv(sb.NETWORK_IQ_ENV, ENV_VALUE)
        devs = sb.enumerate_all_sdr_devices()
        ent = next(d for d in devs if d.get("driver") == "network_iq")
        be = sb.build_backend_for_device(ent)
        assert isinstance(be, sb.NetworkIQBackend)
        assert be.status.connected is False
        assert be.status.frequency_hz == 100e6
        assert be.status.sample_rate_hz == 2.4e6


class TestBackendContract:
    def test_get_status_is_same_object(self):
        b = _backend()
        # 旧实现每次新建 SDRStatus，error/recording 等状态全丢
        assert b.get_status() is b.status
        assert b.get_status().frequency_hz == 100e6
        assert b.get_status().sample_rate_hz == 2.4e6

    def test_connect_refused_is_false_with_error(self):
        b = _backend(port=_closed_port())
        assert b.connect() is False
        assert b.status.connected is False
        assert b.status.link_state == "failed"
        assert "127.0.0.1" in b.status.error

    def test_connect_success_writes_status(self):
        ph = []
        srv = _tcp_server(b"", ph)
        try:
            b = _backend(port=ph[0])
            assert b.connect() is True
            assert b.status.connected is True
            assert b.status.link_state == "connected"
            assert b.status.error == ""
            b.disconnect()
            assert b.status.connected is False
            assert b.status.link_state == "disconnected"
            assert b.read_samples(16) is None
        finally:
            srv.close()

    def test_setters_return_bool_and_sync_status(self):
        b = _backend()
        assert b.set_frequency(144.05e6) is True
        assert b.status.frequency_hz == 144.05e6
        assert b.set_sample_rate(1e6) is True
        assert b.status.sample_rate_hz == 1e6
        assert b.set_frequency(float("nan")) is False
        assert b.status.error
        assert b.set_sample_rate(-1) is False
        assert b.set_sample_rate(2e9) is False   # 超出设备声明范围（1e9 上界）
        # 失败后 status 不被污染
        assert b.status.frequency_hz == 144.05e6
        assert b.status.sample_rate_hz == 1e6

    def test_eof_tail_not_stranded(self):
        # 服务端发 10 个 cu8 样本（20 字节）即 EOF；请求 16 个。
        # 旧实现：凑不满 32 字节 → recv b"" → None，20 字节真数据永久卡死。
        payload = bytes([0, 255] + [128, 128] * 9)
        assert len(payload) == 20
        ph = []
        srv = _tcp_server(payload, ph)
        try:
            b = _backend(port=ph[0])
            assert b.connect() is True
            iq = b.read_samples(16)
            assert iq is not None and len(iq) == 10   # 短读返回真数据，绝不补零
            assert abs(iq[0].real + 1.0) < 1e-6
            assert abs(iq[0].imag - 0.9921875) < 1e-4
            assert b.read_samples(16) is None          # 耗尽后诚实 None
            b.disconnect()
        finally:
            srv.close()

    def test_eof_fractional_tail_discarded(self):
        # 10 个整样本 + 1 个补不齐的散字节：整样本返回，散字节如实丢弃
        payload = bytes([128, 128] * 10) + b"\x7f"
        ph = []
        srv = _tcp_server(payload, ph)
        try:
            b = _backend(port=ph[0])
            assert b.connect() is True
            iq = b.read_samples(16)
            assert iq is not None and len(iq) == 10
            assert b.read_samples(16) is None
            b.disconnect()
        finally:
            srv.close()

    def test_samples_read_counted(self):
        ph = []
        srv = _tcp_server(bytes([128, 128] * 32), ph)
        try:
            b = _backend(port=ph[0])
            assert b.connect() is True
            assert b.read_samples(8) is not None
            assert b.read_samples(8) is not None
            # 旧实现 get_status 新建对象，samples_read 永远 0
            assert b.get_status().samples_read == 16
            b.disconnect()
        finally:
            srv.close()