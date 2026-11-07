# SPDX-License-Identifier: MIT
"""#49 断连读数残留（审计交接单 B 节 P2）。

旧病灶：基类 disconnect() 只翻 connected/link_state，rssi_db/snr_db/
uptime_seconds/samples_read 保留上次会话值；重连 _samples_read 不清零，
新会话计数叠在旧会话上。全部用无硬件后端（FileIQ 真实读盘 + NetworkIQ
回环）确定性验证。
"""
import socket
import sys
import os
import threading
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mbdsdr_ai.sdr_backend as sb


def _file_backend(tmp_path):
    p = tmp_path / "src.npy"
    rng = np.random.default_rng(20261107)
    iq = (rng.standard_normal(4096) + 1j * rng.standard_normal(4096)) * 0.25
    np.save(p, iq.astype(np.complex64))
    return sb.FileIQBackend(str(p))


def _tcp_server(payload, port_holder):
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


class TestDisconnectResetsReadouts:
    def test_rssi_snr_return_to_never_sampled(self, tmp_path):
        b = _file_backend(tmp_path)
        assert b.connect() is True
        # 模拟会话中真实采到的信号读数（读路径会写这两个字段）
        b.status.rssi_db = -32.5
        b.status.snr_db = 18.0
        b.disconnect()
        assert b.status.rssi_db is None, "断连后 rssi 必须回到「从未采到」"
        assert b.status.snr_db is None, "断连后 snr 必须回到「从未采到」"

    def test_uptime_reset(self, tmp_path):
        b = _file_backend(tmp_path)
        assert b.connect() is True
        b._start_time = time.time() - 120.0   # 模拟已连接 2 分钟
        assert b.get_status().uptime_seconds >= 100.0
        b.disconnect()
        assert b.status.uptime_seconds == 0.0

    def test_samples_read_reset_and_reconnect_fresh(self, tmp_path):
        b = _file_backend(tmp_path)
        assert b.connect() is True
        assert b.read_samples(100) is not None
        assert b.get_status().samples_read == 100
        b.disconnect()
        assert b.status.samples_read == 0, "断连后会话计数必须清零"
        assert b._samples_read == 0, "重连不得叠在旧会话计数上"
        assert b.connect() is True
        assert b.read_samples(50) is not None
        assert b.get_status().samples_read == 50, "新会话从 0 起算，不是 150"
        b.disconnect()

    def test_last_data_age_reset(self, tmp_path):
        b = _file_backend(tmp_path)
        assert b.connect() is True
        b._last_ring_data_time = time.time() - 10.0
        assert b.get_status().last_data_age_s >= 9.0
        b.disconnect()
        assert b.status.last_data_age_s is None
        assert b._last_ring_data_time is None

    def test_network_iq_disconnect_resets_via_super(self):
        ph = []
        srv = _tcp_server(bytes([128, 128] * 64), ph)
        try:
            b = sb.NetworkIQBackend(
                protocol="tcp", host="127.0.0.1", port=ph[0],
                sample_rate_hz=2.4e6, center_freq_hz=100e6)
            assert b.connect() is True
            assert b.read_samples(16) is not None
            b.status.rssi_db = -40.0
            b.disconnect()
            assert b.status.connected is False
            assert b.status.samples_read == 0
            assert b._samples_read == 0
            assert b.status.rssi_db is None
            assert b.status.uptime_seconds == 0.0
        finally:
            srv.close()

    def test_disconnect_idempotent_on_fresh_backend(self, tmp_path):
        b = _file_backend(tmp_path)
        b.disconnect()   # 从未连接也要安全、不造假
        assert b.status.connected is False
        assert b.status.samples_read == 0
        assert b.status.rssi_db is None