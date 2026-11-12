# SPDX-License-Identifier: MIT
"""环形缓冲生产者的读失败退避阶梯。

产品口径（2026-11-12 裁定）：
  * 连续失败 1–19 次：维持 5 ms；
  * 连续失败 20–199 次：转 50 ms；
  * 连续失败 ≥200 次且 ``link_state == "disconnected"``：转 500 ms；
  * 任一次成功读立刻复位（回到 5 ms 档）。

为什么必须复位：设备回来以后还按旧失败计数等 500 ms，等于把恢复延迟 100 倍；
为什么 ``≥200`` 还要 ``disconnected``：只有「链路已明确断开」才说明设备真的不在了，
仍标 stale/failed 时不升级到 500 ms，避免把慢数据误当成设备消失。

本文件同时锁死 ``_mark_link_lost`` 的「只在状态转移时写一次 status」：
拔线后每轮读都会失败，每次都重写等于刷屏并覆盖首因。
"""
from __future__ import annotations

import sys

import numpy as np
import pytest

_ROOT_DIR = __file__.rsplit("tests", 1)[0]
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from mbdsdr_ai.sdr_backend import (  # noqa: E402
    RTLSDRBackend,
    SDRStatus,
    _ThreadedRingReader,
    reader_backoff_s,
)


# ---------------------------------------------------------------- 纯函数阶梯
@pytest.mark.parametrize("failures,expected", [
    (0, 0.005),
    (1, 0.005),
    (19, 0.005),
    (20, 0.05),
    (199, 0.05),
    (200, 0.05),      # 未明确断开：不升级到 500ms
    (1000, 0.05),
])
def test_backoff_ladder_without_disconnect(failures, expected):
    assert reader_backoff_s(failures) == expected
    assert reader_backoff_s(failures, "") == expected
    assert reader_backoff_s(failures, "stale") == expected


@pytest.mark.parametrize("failures", [199, 200, 201, 10_000])
def test_backoff_ladder_disconnected_tail(failures):
    expected = 0.5 if failures >= 200 else 0.05
    assert reader_backoff_s(failures, "disconnected") == expected


# --------------------------------------------------- _produce_loop 同步驱动
def _drive(reader):
    """同步跑完 _produce_loop，返回本次请求的等待序列（不真 sleep）。"""
    waits = []
    reader._sleep = waits.append  # type: ignore[method-assign]
    reader._produce_loop()
    return waits


class _ScriptedRead:
    """按脚本返回：``"raise"`` / ``"empty"`` / ``ndarray``；脚本耗尽即停机。"""

    def __init__(self, ring, plan):
        self._ring = ring
        self._plan = list(plan)

    def __call__(self, n):
        if not self._plan:
            self._ring._stop = True      # 下一轮 while 检查直接退出
            return np.zeros(4, dtype=np.complex64)
        kind = self._plan.pop(0)
        if kind == "raise":
            raise IOError("usb bulk transfer failed")
        if kind == "empty":
            return np.zeros(0, dtype=np.complex64)
        return np.ones(4, dtype=np.complex64)


def _reader(plan, *, link_state="disconnected"):
    ring = _ThreadedRingReader(
        read_fn=None,
        block_size=4,
        ring_size=64,
        link_state_fn=(lambda: link_state) if link_state is not None else None,
    )
    ring._read_fn = _ScriptedRead(ring, plan)
    return ring


def test_loop_ladder_and_reset_on_success():
    # 21 次失败（19×5ms → 2×50ms）→ 一次成功复位 → 再失败回到 5ms
    plan = ["raise"] * 21 + ["ok"] + ["raise"] + ["ok"]
    waits = _drive(_reader(plan))
    assert waits == [0.005] * 19 + [0.05] * 2 + [0.005]


def test_loop_escalates_to_500ms_after_200_when_disconnected():
    plan = ["raise"] * 201 + ["ok"]
    waits = _drive(_reader(plan))
    assert waits[:19] == [0.005] * 19
    assert waits[19:199] == [0.05] * 180
    assert waits[199:] == [0.5, 0.5]


def test_loop_does_not_escalate_to_500ms_when_link_state_unknown():
    plan = ["raise"] * 201 + ["ok"]
    waits = _drive(_reader(plan, link_state=None))
    assert set(waits) == {0.005, 0.05}


def test_loop_counts_empty_chunk_as_failure():
    plan = ["empty"] * 20 + ["ok"]
    waits = _drive(_reader(plan))
    assert waits == [0.005] * 19 + [0.05]


# ------------------------------------------- _mark_link_lost 只在转移时写一次
def _bare_backend(link_state="connected", error=""):
    be = RTLSDRBackend.__new__(RTLSDRBackend)
    be.status = SDRStatus(connected=True, link_state=link_state, error=error)
    return be


def test_mark_link_lost_writes_status_only_on_transition():
    be = _bare_backend()
    be._mark_link_lost("disconnected", "首次：设备拔出")
    assert be.status.link_state == "disconnected"
    assert be.status.connected is False
    assert be.status.error == "首次：设备拔出"

    # 已 disconnected 的后续失败：不许再刷状态（否则状态栏/日志每轮都变）
    be._mark_link_lost("disconnected", "第二次：另一条错误文本")
    assert be.status.error == "首次：设备拔出"
    assert be.status.error_category == "disconnected"


def test_mark_link_lost_writes_again_after_recovery_transition():
    be = _bare_backend()
    be._mark_link_lost("disconnected", "首次：设备拔出")
    be.status.link_state = "connected"      # 成功读把它转回来
    be._mark_link_lost("disconnected", "再次：又拔了")
    assert be.status.error == "再次：又拔了"
