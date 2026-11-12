# SPDX-License-Identifier: MIT
"""设备会话占用态（gpredict 第三档）的数据源。

数据源 = ``connect_request_store`` 的会话快照：
``begin_session(key)`` 冻结「本次实际下发值」，``end_session(key)`` 结束占用。
本文件只测**数据源**，不测显示（控件置灰/徽标/提示文案属待决策项，
见 docs/research/2026-11-12-gpredict-occupancy-decision.md）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DESKTOP = os.path.join(_ROOT, "desktop")
for _p in (_ROOT, _DESKTOP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import connect_request_store as store  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_store():
    store.reset()
    yield
    store.reset()


def test_begin_session_freezes_values_used_for_connect():
    """会话开始冻结当前请求；会话期间 store 被改，快照不跟着变。"""
    store.update("dev-a", sample_rate_hz=2_048_000.0, gain_db=30.0, ppm=0)
    snap = store.begin_session("dev-a")

    assert store.is_occupied("dev-a")
    assert snap.sample_rate_hz == 2_048_000.0
    assert snap.gain_db == 30.0
    assert snap.ppm == 0

    # 反证保护：快照若是活引用（返回 request_for），这里会一起变成 1.024M。
    store.update("dev-a", sample_rate_hz=1_024_000.0)
    assert store.request_for("dev-a").sample_rate_hz == 1_024_000.0
    assert store.session_values("dev-a").sample_rate_hz == 2_048_000.0


def test_begin_session_keeps_first_snapshot():
    """重复 begin 不覆盖首次快照：正在生效的值不能被后来的请求换掉。"""
    store.update("dev-a", gain_db=30.0)
    first = store.begin_session("dev-a")
    store.update("dev-a", gain_db=40.0)
    second = store.begin_session("dev-a")

    assert second == first
    assert store.session_values("dev-a").gain_db == 30.0


def test_end_session_clears_occupancy_and_returns_snapshot():
    store.update("dev-a", ppm=25)
    store.begin_session("dev-a")
    snap = store.end_session("dev-a")

    assert snap is not None and snap.ppm == 25
    assert not store.is_occupied("dev-a")
    assert store.session_values("dev-a") is None
    assert store.end_session("dev-a") is None, "重复 end 不得抛错或返回旧值"


def test_end_session_does_not_touch_request():
    """结束占用只清状态：请求是用户的意图，不能因断开被清空。"""
    store.update("dev-a", sample_rate_hz=2_400_000.0)
    store.begin_session("dev-a")
    store.end_session("dev-a")

    assert store.request_for("dev-a").sample_rate_hz == 2_400_000.0


def test_idle_device_has_no_session():
    assert not store.is_occupied("never-connected")
    assert store.session_values("never-connected") is None


def test_two_devices_occupy_independently():
    store.update("dev-a", sample_rate_hz=2_048_000.0)
    store.update("dev-b", sample_rate_hz=1_024_000.0)
    store.begin_session("dev-a")

    assert store.is_occupied("dev-a")
    assert not store.is_occupied("dev-b")
    assert store.session_values("dev-b") is None
    assert store.session_values("dev-a").sample_rate_hz == 2_048_000.0


def test_reset_clears_sessions():
    store.begin_session("dev-a")
    store.reset()

    assert not store.is_occupied("dev-a")
    assert store.session_values("dev-a") is None
