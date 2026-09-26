"""rtl_tcp 网络接收源测试：用本地假 rtl_tcp server 自证协议实现正确。

不依赖真实硬件 / 网络：起一个 localhost TCP 假 server（threading + socket），
它先吐 12 字节 dongle_info，再持续吐已知 8-bit IQ（I=200, Q=50 交替），
并把客户端发来的 5 字节命令包原样记录下来。测试断言：

  1. 设备信息解析：tuner_type=5(R820T), tuner_gain_count=14。
  2. 命令包逐字节正确：set_freq/set_rate/set_gain/set_ppm → (1,..)(2,..)(3,..)(4,..)，
     且原始帧是大端 struct.pack(">BI", ...)。
  3. IQ 真换算：(I-128)/128.0, (Q-128)/128.0 → real=0.5625, imag=-0.609375。
  4. 连不上时 connect() 返回 False 且 error 含"连接失败"，绝不伪造 IQ。
  5. RtlTcpBackend 全流程：连假 server → setters → read_samples → disconnect。

协议帧结构对照 repos/librtlsdr/src/rtl_tcp.c：
  - dongle_info_t 12 字节：rtl_tcp.c:79-83 + 发送 :619-627
  - 命令包 5 字节 packed {u8 cmd, u32 BE param}：rtl_tcp.c:281-284 + 派发 :315-375
"""

import os
import sys
import time
import socket
import struct
import threading

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.rtltcp_client import RtlTcpClient, RtlTcpBackend


# ═══════════════════════════════════════════════════════════
# 假 rtl_tcp server
# ═══════════════════════════════════════════════════════════

class FakeRtlTcpServer(threading.Thread):
    """最小 rtl_tcp server：先吐 12 字节设备信息，再吐已知 8-bit IQ 流。

    协议帧对照 repos/rtl_433/tests/rtl_tcp_serve.py:13-15：
      server→client: b"RTL0" + uint32be tuner_type + uint32be gain_count
      client→server: 5 字节命令（1B cmd + uint32be param），记录不解释
      server→client: 交错 unsigned 8-bit I,Q 流
    """

    # 已知 IQ 模式：I=200, Q=50 交替
    #   real=(200-128)/128 = 0.5625
    #   imag=( 50-128)/128 = -0.609375
    IQ_PATTERN = bytes([200, 50]) * 4096  # 8192 字节 = 4096 复样本

    def __init__(self, host: str = "127.0.0.1"):
        super().__init__(daemon=True)
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((host, 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        # 记录客户端命令（线程安全）
        self.received_cmds = []   # [(cmd_byte:int, param_uint32:int), ...]
        self.received_raw = []    # [bytes(5), ...]  原始帧，用于大端校验
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._conn = None

    def run(self):
        try:
            self._conn, _ = self._srv.accept()
        except OSError:
            return
        try:
            # 12 字节 dongle_info：magic=RTL0, tuner_type=5(R820T), gain_count=14
            # 对照 rtl_tcp.c:619-627：memcpy "RTL0" + htonl(tuner_type) + htonl(gains)
            self._conn.sendall(b"RTL0" + struct.pack(">II", 5, 14))
        except OSError:
            return
        # 命令接收线程：5 字节一包，记录不解释
        threading.Thread(target=self._read_cmds, daemon=True).start()
        # 持续吐已知 IQ 模式（对照 rtl_tcp.c rtlsdr_callback 流式下发）
        while not self._stop.is_set():
            try:
                self._conn.sendall(self.IQ_PATTERN)
            except OSError:
                return

    def _read_cmds(self):
        while not self._stop.is_set():
            frame = self._recvn(5)
            if frame is None:
                return
            cmd, param = struct.unpack(">BI", frame)
            with self._lock:
                self.received_raw.append(frame)
                self.received_cmds.append((cmd, param))

    def _recvn(self, n: int):
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self._conn.recv(n - len(buf))
            except OSError:
                return None
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def wait_cmds(self, n: int, timeout: float = 2.0) -> bool:
        """轮询等待 server 收到至少 n 条命令。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if len(self.received_cmds) >= n:
                    return True
            time.sleep(0.005)
        return False

    def stop(self):
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass


# ═══════════════════════════════════════════════════════════
# 用例 1：设备信息解析
# ═══════════════════════════════════════════════════════════

def test_device_info_parsing():
    srv = FakeRtlTcpServer()
    srv.start()
    try:
        c = RtlTcpClient("127.0.0.1", srv.port)
        assert c.connect() is True, f"连接失败: {c.error}"
        # dongle_info: tuner_type=5(R820T), gain_count=14（大端解析）
        assert c.tuner_type == 5, f"tuner_type 应为 5(R820T)，实际 {c.tuner_type}"
        assert c.tuner_gain_count == 14, \
            f"tuner_gain_count 应为 14，实际 {c.tuner_gain_count}"
        c.close()
    finally:
        srv.stop()


# ═══════════════════════════════════════════════════════════
# 用例 2：命令包逐字节正确 + 大端
# ═══════════════════════════════════════════════════════════

def test_command_packets():
    srv = FakeRtlTcpServer()
    srv.start()
    try:
        c = RtlTcpClient("127.0.0.1", srv.port)
        assert c.connect() is True, f"连接失败: {c.error}"
        # 依次下发四类命令
        assert c.set_frequency(100_000_000) is True
        assert c.set_sample_rate(2_048_000) is True
        assert c.set_gain(200) is True           # 200 = 20.0 dB（0.1dB 单位）
        assert c.set_freq_correction(10) is True  # ppm

        # 等 server 记录到全部 5 条（set_gain 会先 0x03=gain_mode(1) 再 0x04=gain）
        assert srv.wait_cmds(5, timeout=2.0), \
            f"server 只收到 {len(srv.received_cmds)} 条命令: {srv.received_cmds}"

        with srv._lock:
            cmds = list(srv.received_cmds)
            raw = list(srv.received_raw)

        # 语义断言：解析后的 (cmd, param) 正确（真实 osmocom rtl_tcp opcode）
        assert (1, 100_000_000) in cmds, f"缺 set_freq: {cmds}"
        assert (2, 2_048_000) in cmds, f"缺 set_sample_rate: {cmds}"
        assert (3, 1) in cmds, f"缺 gain_mode=1(manual): {cmds}"
        assert (4, 200) in cmds, f"缺 set_gain(0.1dB): {cmds}"
        assert (5, 10) in cmds, f"缺 set_freq_correction(ppm): {cmds}"

        # 字节序断言：原始 5 字节帧必须是大端 struct.pack(">BI", ...)
        assert struct.pack(">BI", 1, 100_000_000) in raw, \
            f"set_freq 帧不是大端: {[r.hex() for r in raw]}"
        assert struct.pack(">BI", 5, 10) in raw, \
            f"set_ppm 帧不是大端: {[r.hex() for r in raw]}"
        c.close()
    finally:
        srv.stop()


# ═══════════════════════════════════════════════════════════
# 用例 3：8-bit IQ → complex64 真换算
# ═══════════════════════════════════════════════════════════

def test_iq_conversion():
    srv = FakeRtlTcpServer()
    srv.start()
    try:
        c = RtlTcpClient("127.0.0.1", srv.port)
        assert c.connect() is True, f"连接失败: {c.error}"
        iq = c.read_samples(4)
        assert iq is not None, "read_samples 返回 None（连接断了？）"
        assert iq.dtype == np.complex64, f"dtype 应为 complex64，实际 {iq.dtype}"
        assert len(iq) == 4, f"应读 4 样本，实际 {len(iq)}"
        # I=200, Q=50 → real=(200-128)/128=0.5625, imag=(50-128)/128=-0.609375
        expected_real = (200 - 128) / 128.0
        expected_imag = (50 - 128) / 128.0
        assert np.allclose(iq.real, expected_real), \
            f"real 应为 {expected_real}，实际 {iq.real}"
        assert np.allclose(iq.imag, expected_imag), \
            f"imag 应为 {expected_imag}，实际 {iq.imag}"
        c.close()
    finally:
        srv.stop()


# ═══════════════════════════════════════════════════════════
# 用例 4：连不上 → connect() False + error 含"连接失败"
# ═══════════════════════════════════════════════════════════

def test_connection_failure():
    # 127.0.0.1:1 基本不可能在监听 → ECONNREFUSED
    backend = RtlTcpBackend("127.0.0.1", port=1)
    ok = backend.connect()
    assert ok is False, "连不上的源 connect() 必须返回 False"
    assert backend.status.connected is False
    assert "连接失败" in backend.status.error, \
        f"status.error 应含'连接失败'，实际: {backend.status.error!r}"
    # 不能伪造数据
    assert backend.read_samples(1024) is None
    backend.disconnect()


# ═══════════════════════════════════════════════════════════
# 用例 5：RtlTcpBackend 全流程
# ═══════════════════════════════════════════════════════════

def test_backend_full_flow():
    srv = FakeRtlTcpServer()
    srv.start()
    try:
        backend = RtlTcpBackend("127.0.0.1", port=srv.port, ppm=0)
        assert backend.connect() is True, f"connect 失败: {backend.status.error}"
        assert backend.status.connected is True
        assert backend.status.sample_rate_hz == pytest.approx(2_048_000.0)

        # setters 走基类封装（连接检查 + 范围钳位 + 失败回滚），都应成功
        assert backend.set_frequency(100_000_000.0) is True
        assert backend.set_sample_rate(2_048_000.0) is True
        assert backend.set_gain(20.0) is True
        assert backend.set_ppm(5) is True

        # 从环形缓冲读 IQ（生产者线程在后台持续填）
        iq = backend.read_samples(4096)
        assert iq is not None, "环形缓冲应能读到数据"
        assert len(iq) == 4096
        assert iq.dtype == np.complex64
        assert np.all(np.isfinite(iq))
        # 假 server 持续吐 I=200,Q=50 → 所有样本都同一个复数值
        expected_real = (200 - 128) / 128.0
        expected_imag = (50 - 128) / 128.0
        assert np.allclose(iq.real, expected_real, atol=1e-6), \
            f"real 均值 {iq.real.mean():.4f} != {expected_real}"
        assert np.allclose(iq.imag, expected_imag, atol=1e-6), \
            f"imag 均值 {iq.imag.mean():.4f} != {expected_imag}"

        backend.disconnect()
        assert backend.status.connected is False
        # disconnect 后再读应返回 None，不崩
        assert backend.read_samples(16) is None
    finally:
        srv.stop()


# ═══════════════════════════════════════════════════════════
# 用例 6：握手失败（magic 不对）→ connect() False
# ═══════════════════════════════════════════════════════════

class _BadMagicServer(threading.Thread):
    """发错误 magic 的假 server，验证客户端拒绝非 rtl_tcp 源。"""
    def __init__(self):
        super().__init__(daemon=True)
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]

    def run(self):
        try:
            conn, _ = self._srv.accept()
            conn.sendall(b"NOPE" + struct.pack(">II", 5, 14))  # 错误 magic
        except OSError:
            pass

    def stop(self):
        try:
            self._srv.close()
        except OSError:
            pass


def test_bad_magic_rejected():
    srv = _BadMagicServer()
    srv.start()
    try:
        c = RtlTcpClient("127.0.0.1", srv.port)
        assert c.connect() is False, "magic 不对必须拒绝"
        assert "连接失败" in c.error
        assert c.tuner_type == 0  # 未解析成功
    finally:
        srv.stop()
