# SPDX-License-Identifier: MIT
"""NetworkIQBackend 测试（sdrpp network_source 机制对等）。

用本地回环 server 验证：协议/格式/字节序守卫、未连接诚实 None、
TCP 保序读取、cu8/cs16/cf32 解码、data_health 口径。"""
import socket
import threading

import numpy as np
import pytest

from mbdsdr_ai.sdr_backend import NetworkIQBackend


def _tcp_server(payload: bytes, port_holder):
    """常驻 TCP 服务端：每 accept 一个连接就把 payload 发完再等下一个。"""
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


def _connect_read(b: NetworkIQBackend, n: int):
    """连接-读取小工具：服务端常驻，重试到读到为止（失败返回 None）。"""
    for _ in range(50):
        try:
            b.connect()
        except OSError:
            continue
        iq = b.read_samples(n)
        if iq is not None:
            return iq
        b.disconnect()
    return None


def test_param_guards():
    for bad in (
        dict(protocol="http", host="h", port=1, sample_rate_hz=1e6,
             center_freq_hz=1e6),
        dict(protocol="tcp", host="h", port=1, sample_rate_hz=1e6,
             center_freq_hz=1e6, fmt="c64"),
        dict(protocol="tcp", host="h", port=1, sample_rate_hz=1e6,
             center_freq_hz=1e6, byteorder="mid"),
    ):
        with pytest.raises(ValueError):
            NetworkIQBackend(**bad)


def test_port_zero_rejected():
    """port<=0 视为无效端点（诚实拒绝，不建半截连接）。"""
    with pytest.raises(ValueError):
        NetworkIQBackend(protocol="udp", host="h", port=0,
                         sample_rate_hz=1e6, center_freq_hz=1e6)


def test_not_connected_returns_none():
    b = NetworkIQBackend(protocol="tcp", host="127.0.0.1", port=1,
                         sample_rate_hz=2.4e6, center_freq_hz=100e6)
    assert b.read_samples(16) is None
    h = b.data_health()
    assert h["connected"] is False and h["buffered_bytes"] == 0


def test_tcp_cu8_roundtrip():
    n = 64
    # cu8 量化口径：(v-128)/128 —— 0 -> -1.0；128 -> 0.0；255 -> +0.9921875
    payload = bytes([0, 255, 128, 128] * (n // 4)) + bytes(
        [128, 128, 0, 255] * (n // 4))
    ph = []
    srv = _tcp_server(payload, ph)
    b = NetworkIQBackend(protocol="tcp", host="127.0.0.1", port=ph[0],
                         sample_rate_hz=2.4e6, center_freq_hz=100e6)
    iq = _connect_read(b, n)
    assert iq is not None and len(iq) == n
    assert abs(iq[0].real + 1.0) < 1e-6
    assert abs(iq[0].imag - 0.9921875) < 1e-4
    assert abs(iq[1].real) < 1e-6
    b.disconnect()
    srv.close()


def test_cs16_byteorder():
    """cs16 小端解码 + 续读语义。

    载荷发 2 倍（32 样本）只读前 8 个：即使 TCP 把流拆成多段，
    首个 recv 拿到的部分已足够覆盖需求，剩余留在缓冲（后续读取可见）。
    """
    n = 8
    base = np.array([32767, -32768, 0, 0] * 8, dtype="<i2").tobytes()
    ph = []
    srv = _tcp_server(base, ph)
    b = NetworkIQBackend(protocol="tcp", host="127.0.0.1", port=ph[0],
                         sample_rate_hz=2.4e6, center_freq_hz=100e6,
                         fmt="cs16", byteorder="little")
    iq = _connect_read(b, n)
    assert iq is not None and len(iq) == n
    assert abs(iq[0].real - 32767 / 32768.0) < 1e-4
    assert abs(iq[0].imag + 1.0) < 1e-4
    assert abs(iq[1].real) < 1e-6
    assert len(b._buf) >= 4                  # 至少还有样本留在缓冲（续读口径）
    b.disconnect()
    srv.close()


def test_udp_datagram():
    """UDP 收发闭环 + 数据面健康口径。

    客户端 bind 系统分配端口（避开夹具服务端占用），feeder 向该端口回发。
    """
    n = 4
    payload = bytes([128] * (n * 2))
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]     # 先占后放，确保端口空闲
    probe.close()

    b = NetworkIQBackend(protocol="udp", host="127.0.0.1", port=free_port,
                         sample_rate_hz=2.4e6, center_freq_hz=100e6,
                         fmt="cu8")
    assert b.connect() is True
    feeder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    feeder.sendto(payload, ("127.0.0.1", free_port))
    iq = None
    for _ in range(50):
        iq = b.read_samples(n)
        if iq is not None:
            break
    assert iq is not None and len(iq) == n
    assert abs(iq[0].real) < 1e-6
    h = b.data_health()
    assert h["protocol"] == "udp" and h["udp_dropped_note"]
    b.disconnect()
    feeder.close()


def test_status_reports_endpoint_params():
    b = NetworkIQBackend(protocol="tcp", host="10.0.0.9", port=5555,
                         sample_rate_hz=1_000_000, center_freq_hz=144_050_000)
    assert b.get_status().frequency_hz == 144_050_000
    assert b.get_status().connected is False
    assert b.device.device_id == "net_tcp_10.0.0.9_5555"
