# SPDX-License-Identifier: MIT
"""
MBDSDR AI - NanoVNA (H / H4) 串口客户端
==========================================

干净室（clean-room）**从零自写**的 NanoVNA 文本协议客户端。

协议事实来自对 GPL-3.0 上游（ttrftech/NanoVNA 固件、NanoVNA-H-hugen 固件、
nanovna-saver）的**机制学习**（命令字 / 数据格式 / 握手时序），实现为本项目
独立编写，未复制任何上游代码。串口文本命令属功能性互操作接口（同 AT/SCPI）。

设计要点：
- 传输层抽象：真实 pyserial 串口 与 脚本回放（replay）可互换，
  使无硬件时仍能做确定性测试（fixture 全部自造）。
- 诚实空态：无设备 / 未连接时不伪造数据，明确抛错或返回空。
- 协议（详见 docs/learn/nanovna/protocol-study.md）：
  USB CDC 115200 8N1；命令以 "\\r" 结尾；回包行结束 "\\r\\n"；
  一轮响应以提示符 "ch> " 收尾；`data N` 每行一对 "re im" 复数。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 射频常数
# ---------------------------------------------------------------------------
Z0_DEFAULT = 50.0  # 标准参考阻抗 (Ohm)

# NanoVNA H/H4 的 USB VID:PID 提示（仅用于枚举过滤，不硬编码端口）
# 来源：nanovna-saver Hardware/Hardware.py:48-52（协议事实）。
NANOVNA_VID_PID: Tuple[Tuple[int, int], ...] = (
    (0x0483, 0x5740),  # STM32 CDC，NanoVNA / H / H4
)


class NanoVNADriverError(RuntimeError):
    """与 NanoVNA 通信或状态相关的错误。无设备时优先抛此异常。"""


# ===========================================================================
# 射频换算（纯函数，通用工程公式；不依赖设备）
# ===========================================================================

def gamma_mag(s: complex) -> float:
    """反射系数模 |Gamma|。"""
    return abs(s)


def return_loss_db(s11: complex) -> float:
    """S11 -> 回波损耗 (dB)。无反射时趋于 +inf。"""
    g = abs(s11)
    if g <= 0.0:
        return math.inf
    return -20.0 * math.log10(g)


def vswr(s11: complex) -> float:
    """S11 -> 电压驻波比 VSWR。|Gamma|->1 时趋于 +inf。"""
    g = abs(s11)
    if g >= 1.0:
        return math.inf
    if g <= 0.0:
        return 1.0
    return (1.0 + g) / (1.0 - g)


def s11_to_impedance(s11: complex, z0: float = Z0_DEFAULT) -> complex:
    """S11 -> 复阻抗 Z (Ohm)，Z = Z0*(1+S11)/(1-S11)。"""
    denom = 1.0 - s11
    if denom == 0:
        return complex(math.inf, math.inf)
    return z0 * (1.0 + s11) / denom


def s21_gain_db(s21: complex) -> float:
    """S21 -> 幅度增益/损耗 (dB)。"""
    a = abs(s21)
    if a <= 0.0:
        return -math.inf
    return 20.0 * math.log10(a)


def s21_phase_deg(s21: complex) -> float:
    """S21 -> 相位 (度)。"""
    return math.degrees(math.atan2(s21.imag, s21.real))


# ===========================================================================
# 传输层抽象
# ===========================================================================

class _Transport:
    """最小串口传输接口。真实串口与回放都实现它。"""

    def open(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    @property
    def is_open(self) -> bool:
        raise NotImplementedError

    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def readline(self) -> bytes:
        """读一行（含结尾换行）；超时/无数据返回 b""。"""
        raise NotImplementedError

    def reset_input_buffer(self) -> None:  # noqa: D401 - 镜像 pyserial 命名
        pass


class SerialTransport(_Transport):
    """真实 pyserial 串口传输。延迟 import serial，未装/无设备时友好报错。"""

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 0.05):
        self._port = port
        self._baudrate = baudrate
        self._timeout = timeout
        self._ser = None  # type: Optional["serial.Serial"]  # noqa: F821

    def open(self) -> None:
        try:
            import serial  # 延迟导入：无 pyserial 也能 import 本模块跑 replay
        except ImportError as exc:  # pragma: no cover - 环境相关
            raise NanoVNADriverError(
                "需要 pyserial 才能连接真实 NanoVNA（pip install pyserial）"
            ) from exc
        try:
            self._ser = serial.Serial(
                self._port, self._baudrate, timeout=self._timeout
            )
        except Exception as exc:
            raise NanoVNADriverError(f"无法打开串口 {self._port}: {exc}") from exc

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None

    @property
    def is_open(self) -> bool:
        return bool(self._ser is not None and self._ser.is_open)

    def write(self, data: bytes) -> None:
        if self._ser is None:
            raise NanoVNADriverError("串口未连接")
        self._ser.write(data)

    def readline(self) -> bytes:
        if self._ser is None:
            return b""
        return self._ser.readline()

    def reset_input_buffer(self) -> None:
        if self._ser is not None:
            self._ser.reset_input_buffer()


@dataclass
class _ScriptedResponse:
    expected: str
    lines: List[bytes] = field(default_factory=list)


class ReplayTransport(_Transport):
    """脚本回放传输：按命令顺序喂出自造 fixture 响应，用于确定性测试。

    script: 有序 (expected_command, [response_lines]) 列表。每个 expected_command
    在 write 时被严格比对（去尾随空白）；命中后把 response_lines 逐行吐出，
    最后追加一行提示符 b"ch> \\r\\n"，与真机收尾一致。
    未预期命令会立刻抛错（借此断言"命令序列正确"）。
    """

    def __init__(self, script: Sequence[Tuple[str, Sequence[str]]]):
        self._script = [
            _ScriptedResponse(
                expected=exp, lines=[ln.encode("ascii") for ln in lines]
            )
            for exp, lines in script
        ]
        self._cursor = 0
        self._queue: List[bytes] = []
        self.sent: List[str] = []
        self._open = False

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def write(self, data: bytes) -> None:
        cmd = data.decode("ascii").strip()
        self.sent.append(cmd)
        if self._cursor >= len(self._script):
            raise NanoVNADriverError(f"回放脚本耗尽，收到未预期命令: {cmd!r}")
        expected = self._script[self._cursor].expected
        if cmd != expected:
            raise NanoVNADriverError(
                f"命令序列不符: 第{self._cursor}步期望 {expected!r}，实发 {cmd!r}"
            )
        # 吐出该命令的响应行，末尾补提示符
        self._queue = list(self._script[self._cursor].lines)
        self._queue.append(b"ch> \r\n")
        self._cursor += 1

    def readline(self) -> bytes:
        if not self._queue:
            return b""
        return self._queue.pop(0)

    def reset_input_buffer(self) -> None:
        self._queue.clear()


# ===========================================================================
# 串口枚举（VID/PID 提示，不硬编码端口）
# ===========================================================================

def find_nanovna_ports(
    vid_pid_hints: Sequence[Tuple[int, int]] = NANOVNA_VID_PID,
) -> List[Tuple[str, int, int]]:
    """枚举串口，按 VID:PID 提示过滤 NanoVNA。返回 [(port, vid, pid), ...]。

    只做提示过滤：真实端口路径交给调用方显式选择，本函数不假设具体 tty 名。
    无 pyserial 或无设备时返回空列表（诚实空态，不伪造）。
    """
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    hints = set(vid_pid_hints)
    found: List[Tuple[str, int, int]] = []
    for p in list_ports.comports():
        if p.vid is None or p.pid is None:
            continue
        if (p.vid, p.pid) in hints:
            found.append((p.device, p.vid, p.pid))
    return found


# ===========================================================================
# 客户端
# ===========================================================================

@dataclass
class SweepInfo:
    start_hz: int
    stop_hz: int
    points: int


class NanoVNAClient:
    """NanoVNA H/H4 文本协议客户端。

    Parameters
    ----------
    transport:
        注入的传输层（测试用 ReplayTransport）。与 ``port`` 二选一。
    port:
        真实串口路径，如 "/dev/ttyACM0" 或 "COM3"。传此参数走 SerialTransport。
    """

    PROMPT_PREFIX = "ch>"
    DEFAULT_BAUD = 115200

    def __init__(
        self,
        transport: Optional[_Transport] = None,
        port: Optional[str] = None,
    ) -> None:
        if transport is not None and port is not None:
            raise NanoVNADriverError("transport 与 port 只能给其一")
        if transport is not None:
            self._transport = transport
        elif port is not None:
            self._transport = SerialTransport(port, self.DEFAULT_BAUD)
        else:
            # 诚实空态：既无注入传输也无端口
            raise NanoVNADriverError(
                "未指定 NanoVNA：请传 port=（真实串口）或 transport=（回放/注入）。"
                "可用 nanovna_client.find_nanovna_ports() 列出候选。"
            )
        self.version: str = "unknown"
        self.info_lines: List[str] = []
        self.features: set = set()
        self.cal_status: List[str] = []
        self.sn: str = ""
        self.sweep: Optional[SweepInfo] = None

    # -- 生命周期 ----------------------------------------------------------
    def connect(self, do_handshake: bool = True) -> None:
        self._transport.open()
        if not self._transport.is_open:
            raise NanoVNADriverError("传输层未成功打开")
        if do_handshake:
            self._handshake()

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> "NanoVNAClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- 协议原语 ----------------------------------------------------------
    def _drain(self) -> None:
        """尽力排空输入缓冲（对齐上游 drain 节奏）。"""
        self._transport.reset_input_buffer()

    def _exec(self, command: str, read_timeout: float = 0.05) -> List[str]:
        """发一条命令，收行直到提示符 'ch>'，剔除回显行。

        返回去掉提示符后的响应行列表（已 strip）。
        """
        if not self._transport.is_open:
            raise NanoVNADriverError("未连接 NanoVNA")
        self._drain()
        self._transport.write((command + "\r").encode("ascii"))
        lines: List[str] = []
        idle = 0
        # 宽松上限 + 空闲计数，防止异常固件不回提示符导致死循环。
        for _ in range(4096):
            time.sleep(read_timeout)
            raw = self._transport.readline()
            if not raw:
                idle += 1
                # 已收到内容后再连续空读数次即视为本轮结束（兼容不回提示符的固件）
                if lines and idle >= 2:
                    break
                continue
            line = raw.decode("ascii", errors="replace").strip()
            if not line:
                continue
            if line == command:  # 回显抑制
                continue
            if line.startswith(self.PROMPT_PREFIX):
                break
            lines.append(line)
            idle = 0
        return lines

    # -- 握手 / 能力探测 ---------------------------------------------------
    def _handshake(self) -> None:
        # help：探测能力
        help_lines = self._exec("help")
        help_text = " ".join(help_lines)
        if "capture" in help_text:
            self.features.add("capture")
        if "bandwidth" in help_text:
            self.features.add("bandwidth")
        if "sn:" in help_text or "sn" in help_text.split():
            self.features.add("sn")
        # version
        ver_lines = self._exec("version")
        if ver_lines:
            self.version = ver_lines[0]
        # info（多行；首行常为板名）
        self.info_lines = self._exec("info")
        # sn（可选）
        if "sn" in self.features:
            sn_lines = self._exec("sn")
            self.sn = " ".join(sn_lines)

    # -- 业务命令 ----------------------------------------------------------
    def set_sweep(self, start_hz: int, stop_hz: int, points: int = 101) -> SweepInfo:
        if stop_hz <= start_hz:
            raise NanoVNADriverError(
                f"stop({stop_hz}) 必须大于 start({start_hz})"
            )
        if points <= 0:
            raise NanoVNADriverError(f"points 必须为正，收到 {points}")
        self._exec(f"sweep {int(start_hz)} {int(stop_hz)} {int(points)}")
        self.sweep = SweepInfo(int(start_hz), int(stop_hz), int(points))
        return self.sweep

    def set_bandwidth(self, bw_khz: int = 1000) -> None:
        valid = (1000, 300, 100, 30, 10)
        if bw_khz not in valid:
            raise NanoVNADriverError(
                f"bandwidth 仅支持 {valid}（kHz），收到 {bw_khz}"
            )
        self._exec(f"bandwidth {bw_khz}")

    def read_frequencies(self) -> List[int]:
        """读频点表（Hz，整数）。"""
        out: List[int] = []
        for line in self._exec("frequencies"):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(int(float(line)))
            except ValueError:
                # 坏行容错：跳过非数字行
                continue
        return out

    def read_data(self, channel: int) -> List[complex]:
        """读 S 参数。channel=0 -> S11，channel=1 -> S21。"""
        if channel not in (0, 1):
            raise NanoVNADriverError("channel 仅支持 0(S11) / 1(S21)")
        out: List[complex] = []
        for line in self._exec(f"data {channel}"):
            parts = line.split()
            if len(parts) < 2:
                continue  # 坏行容错
            try:
                re, im = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            out.append(complex(re, im))
        return out

    def read_s11(self) -> List[complex]:
        return self.read_data(0)

    def read_s21(self) -> List[complex]:
        return self.read_data(1)

    def get_cal_status(self) -> List[str]:
        """读校准状态：空格分隔的已置位项列表。"""
        lines = self._exec("cal")
        joined = " ".join(lines)
        self.cal_status = joined.split() if joined else []
        return self.cal_status

    # -- 便捷派生量（单点） ------------------------------------------------
    @staticmethod
    def s11_metrics(s11: complex) -> Dict[str, float]:
        return {
            "mag": gamma_mag(s11),
            "return_loss_db": return_loss_db(s11),
            "vswr": vswr(s11),
            "impedance": s11_to_impedance(s11),
        }

    @staticmethod
    def s21_metrics(s21: complex) -> Dict[str, float]:
        return {
            "gain_db": s21_gain_db(s21),
            "phase_deg": s21_phase_deg(s21),
        }
