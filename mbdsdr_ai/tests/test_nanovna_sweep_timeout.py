# SPDX-License-Identifier: MIT
"""分段扫描整体超时（缺口报告 P1：NanoVNA 静默挂起 ~204s）。

segmented_sweep 耗时随段数线性增长，早期无整体截止 → 多段扫描长时间
无响应。现加 timeout_s 硬上限：超时如实抛错、报已完成段数，
**绝不返回半截曲线**、绝不静默继续。
"""
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
