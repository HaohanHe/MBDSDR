# SPDX-License-Identifier: MIT
"""分段扫描整体超时 + _exec 总 deadline（缺口报告 P1：NanoVNA 静默挂起 ~204s）。

segmented_sweep 耗时随段数线性增长，早期无整体截止 → 多段扫描长时间
无响应。现加 timeout_s 硬上限：超时如实抛错、报已完成段数，
**绝不返回半截曲线**、绝不静默继续。

_exec 侧同源缺口：设备完全静默时 4096×0.05s ≈ 204s 死等。现加
time.monotonic() 总 deadline（默认 10s）：超时如实抛错并报已收行数。
"""
import time

import pytest

from mbdsdr_ai.nanovna_client import (NanoVNAClient, NanoVNADriverError,
                                      ReplayTransport)

# 两段、每段 11 点的合法回放脚本（不依赖真实硬件）
_SCRIPT = [
    ["sweep 430000000 430500000 11", []],
    ["frequencies", [str(430_000_000 + i * 50_000) for i in range(11)]],
    ["data 0", ["0.5 0.0"] * 11],
    ["data 1", ["0.1 -0.02"] * 11],
    ["sweep 430500000 431000000 11", []],
    ["frequencies", [str(430_500_000 + i * 50_000) for i in range(11)]],
    ["data 0", ["0.5 0.0"] * 11],
    ["data 1", ["0.1 -0.02"] * 11],
]


def test_reject_non_positive_timeout():
    """timeout_s 必须 > 0，非正值诚实报错（不接受"永不超时"的隐式约定）。"""
    c = NanoVNAClient(transport=ReplayTransport(_SCRIPT))
    with pytest.raises(NanoVNADriverError):
        c.segmented_sweep(430_000_000, 431_000_000, 21, timeout_s=0)


def test_timeout_aborts_honestly_without_half_curve():
    """超时必须如实抛错：不静默挂起，也不拼半截曲线。"""
    c = NanoVNAClient(transport=ReplayTransport(_SCRIPT))
    with pytest.raises(NanoVNADriverError) as ei:
        c.segmented_sweep(430_000_000, 431_000_000, 21, timeout_s=1e-9)
    assert "超时" in str(ei.value), f"应如实报超时，实际: {ei.value}"


# ── _exec 总 deadline：设备完全静默不再 ~204s 死等 ──────────────────────────

class _SilentTransport:
    """完全静默的设备：write 收下、readline 永远空（模拟拔线/死固件）。"""

    def __init__(self):
        self._open = False

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def write(self, data: bytes) -> None:
        pass

    def readline(self) -> bytes:
        return b""

    def reset_input_buffer(self) -> None:
        pass


def test_exec_silent_device_hits_deadline_not_204s():
    """静默设备必须在 deadline 内如实抛错，绝不跑满 4096 轮（~204s）。"""
    c = NanoVNAClient(transport=_SilentTransport())
    c.connect(do_handshake=False)
    t0 = time.monotonic()
    with pytest.raises(NanoVNADriverError) as ei:
        c._exec("version", deadline_s=0.3)
    elapsed = time.monotonic() - t0
    assert "超时" in str(ei.value), f"应如实报超时，实际: {ei.value}"
    assert elapsed < 5.0, f"deadline 未生效，实跑 {elapsed:.1f}s"


def test_exec_rejects_non_positive_deadline():
    """deadline_s 必须 >0，拒绝"永不超时"的隐式约定。"""
    c = NanoVNAClient(transport=_SilentTransport())
    c.connect(do_handshake=False)
    with pytest.raises(NanoVNADriverError):
        c._exec("version", deadline_s=0)


def test_exec_normal_response_unaffected():
    """正常回放路径不受 deadline 影响（已有提示符即刻返回）。"""
    c = NanoVNAClient(transport=ReplayTransport([["version", ["1.0.1"]]]))
    c.connect(do_handshake=False)
    assert c._exec("version", deadline_s=5.0) == ["1.0.1"]
