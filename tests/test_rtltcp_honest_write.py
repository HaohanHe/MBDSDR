# SPDX-License-Identifier: MIT
"""rtl_tcp 写命令诚实性：下发失败不许冒充成功（2026-11-06 审计 B 节两条 P1）。

缺陷形态（修复前）：
1. RtlTcpDeviceAdapter 的 center_freq/sample_rate/gain/freq_correction setter
   不看 RtlTcpClient.set_* 的 bool 返回值就更新本地缓存。rtl_tcp 无查询命令，
   RTLSDRBackend._apply_frequency 的「写后回读 ×3」读的正是这个缓存 →
   链路已断时三次重试全部"验证通过"，断线调谐被报成成功。
2. RtlTcpBackend._apply_sample_rate 吸附到 SDR++ 离散档后下发，但基类
   set_sample_rate 成功分支用 _resolve_applied_sample_rate()（默认=请求值）
   回写 status → 吸附档被请求值盖掉（请求 2.000M / 实发 2.048M），
   DSP fs_in 与真实流率分叉。

断言分三层：
- 行为层：死链路（sendall 抛 ConnectionResetError，走真实 _send_cmd 归类）
  下 setter 抛 OSError 且缓存不动；RTLSDRBackend.set_frequency 返回 False、
  status 回滚、进故障态。
- 协议层：假 server 逐字节记录命令——采样率请求 2.000M 时线上实发
  2.048M，且 status/get_sample_rate 与线上一致（生效值唯一事实源）。
- 源码钉扎：setter 必须先判返回值再写缓存；RtlTcpBackend 必须覆写
  _resolve_applied_sample_rate。

反证（旧实现同场景假成功）在仓库外探针 D:/mbdsdr/output/_probe_rtltcp_honest_reverse.py。
"""

import os
import socket
import struct
import sys
import threading
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.rtltcp_client import (  # noqa: E402
    RtlTcpBackend, RtlTcpClient, RtlTcpDeviceAdapter)
from mbdsdr_ai.sdr_backend import RTLSDRBackend  # noqa: E402


class _MiniRtlTcpServer:
    """最小 rtl_tcp server：握手吐 dongle_info，之后只记录 5 字节命令包。

    不吐 IQ 流（本组用例只验命令面）；帧结构对照 rtl_tcp.c:281-284,619-627。
    """

    def __init__(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        self.cmds = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._conn = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self._conn, _ = self._srv.accept()
            self._conn.sendall(b"RTL0" + struct.pack(">II", 5, 14))
        except OSError:
            return
        while not self._stop.is_set():
            try:
                self._conn.settimeout(0.2)
                frame = self._recvn(5)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return
            if frame is None:
                return
            cmd, param = struct.unpack(">BI", frame)
            with self._lock:
                self.cmds.append((cmd, param))

    def _recvn(self, n):
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self._conn.recv(n - len(buf))
            except (TimeoutError, socket.timeout):
                raise
            except OSError:
                return None
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def wait_cmds(self, n, timeout=2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if len(self.cmds) >= n:
                    return True
            time.sleep(0.005)
        return False

    def snapshot(self):
        with self._lock:
            return list(self.cmds)

    def stop(self):
        self._stop.set()
        for s in (self._conn, self._srv):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass


class _DeadSocket:
    """sendall 必抛 ConnectionResetError 的 socket 替身。

    只替换 _sock，_send_cmd 的归类/标记断开/error 写入全走真实代码。
    """

    def sendall(self, data):
        raise ConnectionResetError("模拟对端已断开")

    def recv(self, n):
        return b""

    def settimeout(self, t):
        pass

    def shutdown(self, how):
        pass

    def close(self):
        pass


@pytest.fixture()
def server():
    srv = _MiniRtlTcpServer()
    yield srv
    srv.stop()


@pytest.fixture()
def adapter(server):
    a = RtlTcpDeviceAdapter("127.0.0.1", server.port)
    yield a
    a.close()


def _kill_link(client):
    client._sock = _DeadSocket()


def _stop_producer(backend):
    """先停生产者再杀链路：否则读线程抢先标记断开，set_* 走未连接早退分支，
    命令面的失败归因就不确定了（本组要验的是写命令路径本身）。"""
    rr = getattr(backend, "_ring_reader", None)
    if rr is not None:
        rr.stop()
        backend._ring_reader = None


class TestAdapterWriteHonesty:
    def test_dead_link_freq_setter_raises_and_cache_unchanged(self, adapter):
        adapter.center_freq = 100_000_000
        assert adapter.center_freq == 100_000_000
        _kill_link(adapter._client)
        with pytest.raises(OSError):
            adapter.center_freq = 433_000_000
        # 缓存必须停在最后一次真正下发成功的值，不许拿请求值冒充
        assert adapter.center_freq == 100_000_000

    def test_dead_link_rate_setter_raises_and_cache_unchanged(self, adapter):
        adapter.sample_rate = 2_048_000
        _kill_link(adapter._client)
        with pytest.raises(OSError):
            adapter.sample_rate = 1_024_000
        assert adapter.sample_rate == 2_048_000

    def test_dead_link_gain_setter_raises_and_cache_unchanged(self, adapter):
        adapter.gain = 20.0
        _kill_link(adapter._client)
        with pytest.raises(OSError):
            adapter.gain = 30.0
        assert adapter.gain == 20.0

    def test_dead_link_ppm_setter_raises_and_cache_unchanged(self, adapter):
        adapter.freq_correction = 5
        _kill_link(adapter._client)
        with pytest.raises(OSError):
            adapter.freq_correction = 10
        assert adapter.freq_correction == 5

    def test_live_link_setter_updates_cache_and_wire(self, adapter, server):
        adapter.center_freq = 100_500_000
        adapter.sample_rate = 2_048_000
        assert adapter.center_freq == 100_500_000
        assert adapter.sample_rate == 2_048_000
        assert server.wait_cmds(2)
        cmds = server.snapshot()
        assert (RtlTcpClient.CMD_SET_FREQUENCY, 100_500_000) in cmds
        assert (RtlTcpClient.CMD_SET_SAMPLE_RATE, 2_048_000) in cmds

    def test_error_message_carries_client_reason(self, adapter):
        _kill_link(adapter._client)
        with pytest.raises(OSError) as ei:
            adapter.center_freq = 100_000_000
        assert str(ei.value), "失败必须带可操作原因，不许空异常"


class TestBackendTuneFailureE2E:
    def test_dead_link_set_frequency_false_rollback_fault(self, server):
        """RTLSDRBackend(host=rtl_tcp) 断线调谐：False + 回滚 + 故障态。

        修复前：适配器 setter 吞掉下发失败、缓存照写 → 写后回读 ×3 全部
        匹配 → set_frequency 返回 True（假调谐成功）。
        """
        b = RTLSDRBackend(host="127.0.0.1", port=server.port)
        try:
            assert b.connect() is True
            assert b.set_frequency(100_000_000.0) is True
            old = b.get_frequency()
            _stop_producer(b)
            _kill_link(b._sdr._client)
            assert b.set_frequency(433_000_000.0) is False
            assert b.get_frequency() == old          # 回滚
            assert b.status.error                    # 失败可见
            assert b._tuner_faulted is True          # 与本地 USB 同纪律
        finally:
            b.disconnect()

    def test_dead_link_set_sample_rate_false_rollback(self, server):
        b = RTLSDRBackend(host="127.0.0.1", port=server.port)
        try:
            assert b.connect() is True
            old = b.get_sample_rate()
            _stop_producer(b)
            _kill_link(b._sdr._client)
            assert b.set_sample_rate(1_024_000.0) is False
            assert b.get_sample_rate() == old
            assert b.status.error
        finally:
            b.disconnect()


class TestRtlTcpBackendAppliedRate:
    def test_snapped_rate_is_status_and_wire_value(self, server):
        """请求 2.000M → 线上实发 2.048M，status 回读也必须是 2.048M。

        修复前：_apply_sample_rate 先把吸附档写进 status，基类成功分支
        又用默认 _resolve_applied_sample_rate（=请求值）盖回 2.000M。
        """
        b = RtlTcpBackend("127.0.0.1", server.port)
        try:
            assert b.connect() is True
            n0 = len(server.snapshot())
            assert b.set_sample_rate(2_000_000.0) is True
            snapped = float(RTLSDRBackend.nearest_sample_rate(2_000_000))
            assert snapped == 2_048_000.0
            assert b.status.sample_rate_hz == snapped
            assert b.get_sample_rate() == snapped
            assert server.wait_cmds(n0 + 1)
            wire = [p for c, p in server.snapshot()[n0:]
                    if c == RtlTcpClient.CMD_SET_SAMPLE_RATE]
            assert wire and wire[-1] == int(snapped), "线上实发必须是吸附档"
        finally:
            b.disconnect()

    def test_exact_tier_request_unchanged(self, server):
        b = RtlTcpBackend("127.0.0.1", server.port)
        try:
            assert b.connect() is True
            assert b.set_sample_rate(1_536_000.0) is True
            assert b.get_sample_rate() == 1_536_000.0
        finally:
            b.disconnect()

    def test_failed_rate_write_rolls_back_to_previous(self, server):
        b = RtlTcpBackend("127.0.0.1", server.port)
        try:
            assert b.connect() is True
            assert b.set_sample_rate(2_400_000.0) is True
            old = b.get_sample_rate()
            _stop_producer(b)
            _kill_link(b._client)
            assert b.set_sample_rate(1_024_000.0) is False
            assert b.get_sample_rate() == old
            assert b.status.error
        finally:
            b.disconnect()


class TestSourcePins:
    def test_adapter_setters_check_return_before_cache(self):
        import inspect
        from mbdsdr_ai import rtltcp_client as m
        src = inspect.getsource(m.RtlTcpDeviceAdapter)
        # 四个可写属性都必须先判下发结果（_raise_if_dead）再写缓存
        assert src.count("_raise_if_dead(") >= 5, (
            "center_freq/sample_rate/gain/freq_correction/set_manual_gain_mode "
            "五处下发失败都必须显式抛出，不许静默写缓存")

    def test_rtltcp_backend_overrides_applied_rate_resolver(self):
        assert "_resolve_applied_sample_rate" in RtlTcpBackend.__dict__, (
            "不覆写就会被基类用请求值盖掉吸附档")