# SPDX-License-Identifier: MIT
"""NanoVNA 干净室客户端确定性测试（replay 自造 fixture）。

所有设备响应文本均为本项目按协议自造（见 docs/learn/nanovna/protocol-study.md），
不取自 nanovna-saver 仓库。replay 传输严格比对命令序列，借此验证：
命令序列正确、S 参数解析正确、射频换算数值正确（手工标准值）、
无设备空态、坏响应容错。
"""
from __future__ import annotations

import math

import pytest

from mbdsdr_ai.nanovna_client import (
    NanoVNAClient,
    NanoVNADriverError,
    ReplayTransport,
    return_loss_db,
    s11_to_impedance,
    s21_gain_db,
    s21_phase_deg,
    vswr,
)


# ---------------------------------------------------------------------------
# 自造 fixture：一段最小会话（3 个频点）
# ---------------------------------------------------------------------------
HELP = ("Commands: version reset freq data frequencies bandwidth "
        "sweep cal capture help info sn")
VERSION = "1.0.174-hugen"
INFO = ["NanoVNA-H", "Version: 1.0.174-hugen"]
SN = ["SN: A1B2C3D4"]

FREQS = ["1000000", "15500000", "30000000"]
# S11：故意取可手算的标准点
S11_LINES = [
    "0.100000 0.000000",   # Γ=0.1 实数
    "0.000000 0.000000",   # 理想匹配 Γ=0
    "-0.500000 0.500000",  # Γ=0.7071∠135
]
# S21：可手算幅度/相位
S21_LINES = [
    "0.500000 0.000000",   # 0.5 -> -6.02 dB
    "-1.000000 0.000000",  # -1 -> 0 dB, 180°
    "0.000000 -1.000000",  # -j -> 0 dB, -90°
]
CAL_LINE = "load open short thru cal'ed"


HANDSHAKE = [
    ("help", [HELP]),
    ("version", [VERSION]),
    ("info", INFO),
    ("sn", SN),
]

SWEEP_CMD = "sweep 1000000 30000000 101"
# 一轮完整测量（握手之后）的自造命令序列
FULL_MEASURE = [
    (SWEEP_CMD, []),
    ("frequencies", FREQS),
    ("data 0", S11_LINES),
    ("data 1", S21_LINES),
    ("cal", [CAL_LINE]),
]


def _connected(extra=None):
    """按握手 + 额外命令序列构造客户端并完成握手。返回 (client, transport)。"""
    script = list(HANDSHAKE) + list(extra or [])
    t = ReplayTransport(script)
    c = NanoVNAClient(transport=t)
    c.connect()
    return c, t


def _client():
    """握手 + 一轮完整测量（供只做握手/校验类断言的用例）。"""
    return _connected(FULL_MEASURE)


class TestHandshakeAndSequence:
    def test_handshake_fields(self):
        c, t = _client()
        assert c.version == VERSION
        assert c.info_lines[0] == "NanoVNA-H"
        assert c.sn == "SN: A1B2C3D4"
        assert "bandwidth" in c.features
        assert "capture" in c.features
        assert "sn" in c.features

    def test_command_sequence_exact(self):
        c, t = _client()
        c.set_sweep(1_000_000, 30_000_000, 101)
        freqs = c.read_frequencies()
        s11 = c.read_s11()
        s21 = c.read_s21()
        cal = c.get_cal_status()
        assert freqs == [1_000_000, 15_500_000, 30_000_000]
        assert len(s11) == 3 and len(s21) == 3
        assert cal == ["load", "open", "short", "thru", "cal'ed"]
        # 严格命令序列
        assert t.sent == [
            "help", "version", "info", "sn",
            "sweep 1000000 30000000 101",
            "frequencies", "data 0", "data 1", "cal",
        ]

    def test_unexpected_command_raises(self):
        t = ReplayTransport([("help", [HELP]), ("version", [VERSION])])
        c = NanoVNAClient(transport=t)
        c.connect(do_handshake=False)
        t.open()
        with pytest.raises(NanoVNADriverError):
            c._exec("bogus")  # 脚本里没有 bogus


class TestSParameterParsing:
    def test_s11_complex(self):
        c, _ = _connected([(SWEEP_CMD, []), ("data 0", S11_LINES)])
        c.set_sweep(1_000_000, 30_000_000, 101)
        s11 = c.read_s11()
        assert s11[0] == complex(0.1, 0.0)
        assert s11[1] == complex(0.0, 0.0)
        assert s11[2] == complex(-0.5, 0.5)

    def test_s21_complex(self):
        c, _ = _connected([(SWEEP_CMD, []), ("data 1", S21_LINES)])
        c.set_sweep(1_000_000, 30_000_000, 101)
        s21 = c.read_s21()
        assert s21[0] == complex(0.5, 0.0)
        assert s21[2] == complex(0.0, -1.0)


class TestRfMathHandValues:
    def test_gamma0_match(self):
        s = complex(0.0, 0.0)
        assert vswr(s) == 1.0
        assert return_loss_db(s) == math.inf
        assert s11_to_impedance(s) == pytest.approx(complex(50, 0))

    def test_gamma_0p1(self):
        s = complex(0.1, 0.0)
        assert vswr(s) == pytest.approx(1.1 / 0.9, rel=1e-6)   # 1.2222
        assert return_loss_db(s) == pytest.approx(20.0, rel=1e-6)
        assert s11_to_impedance(s) == pytest.approx(
            complex(50 * 1.1 / 0.9, 0), rel=1e-6)

    def test_gamma_mag_half(self):
        # |Gamma| = sqrt(0.5)
        s = complex(-0.5, 0.5)
        g = abs(s)
        assert g == pytest.approx(math.sqrt(0.5))
        assert vswr(s) == pytest.approx((1 + g) / (1 - g), rel=1e-6)

    def test_s21_gain_phase(self):
        assert s21_gain_db(complex(0.5, 0.0)) == pytest.approx(
            20 * math.log10(0.5), rel=1e-6)                 # -6.02 dB
        assert s21_gain_db(complex(-1.0, 0.0)) == pytest.approx(0.0)
        assert s21_phase_deg(complex(-1.0, 0.0)) == pytest.approx(180.0)
        assert s21_phase_deg(complex(0.0, -1.0)) == pytest.approx(-90.0)

    def test_client_metrics_dict(self):
        m = NanoVNAClient.s11_metrics(complex(0.1, 0.0))
        assert m["vswr"] == pytest.approx(1.1 / 0.9)
        assert m["return_loss_db"] == pytest.approx(20.0)
        m2 = NanoVNAClient.s21_metrics(complex(0.5, 0.0))
        assert m2["gain_db"] == pytest.approx(-6.0206, abs=1e-3)


class TestHonestEmptyState:
    def test_no_transport_no_port_raises(self):
        with pytest.raises(NanoVNADriverError):
            NanoVNAClient()  # 既无注入也无端口 -> 明确报错，不伪造

    def test_transport_and_port_mutually_exclusive(self):
        with pytest.raises(NanoVNADriverError):
            NanoVNAClient(transport=ReplayTransport([]), port="/dev/ttyACM0")

    def test_set_sweep_validation(self):
        c, _ = _client()
        with pytest.raises(NanoVNADriverError):
            c.set_sweep(30_000_000, 1_000_000, 101)  # stop<=start
        with pytest.raises(NanoVNADriverError):
            c.set_sweep(1_000_000, 30_000_000, 0)    # points<=0

    def test_bandwidth_choices(self):
        c, _ = _client()
        with pytest.raises(NanoVNADriverError):
            c.set_bandwidth(42)


class TestBadResponseTolerance:
    def test_garbage_lines_skipped(self):
        # 跳过握手，脚本从首个真实命令开始
        script = [
            ("data 0", ["not-a-number", "", "   ", "0.2 0.1", "bad line 1 2 3"]),
        ]
        t = ReplayTransport(script)
        c = NanoVNAClient(transport=t)
        c.connect(do_handshake=False)
        s11 = c.read_s11()
        # 只有 "0.2 0.1" 一行能解析；坏行全部跳过，不崩溃
        assert s11 == [complex(0.2, 0.1)]

    def test_frequency_garbage_skipped(self):
        script = [
            ("frequencies", ["abc", "1000000", "", "1500000.5"]),
        ]
        t = ReplayTransport(script)
        c = NanoVNAClient(transport=t)
        c.connect(do_handshake=False)
        freqs = c.read_frequencies()
        assert freqs == [1_000_000, 1_500_000]
