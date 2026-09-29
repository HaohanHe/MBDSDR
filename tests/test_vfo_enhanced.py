# SPDX-License-Identifier: MIT
"""
VFO 音频路由增强单元测试
==========================

覆盖：多 VFO 互斥静音、主听切换、手动静音覆盖。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.vfo_manager import VfoManager
from mbdsdr_ai.vfo_enhanced import VfoAudioRouter


def test_primary_mutes_others():
    mgr = VfoManager()
    v1 = mgr.add(100e6, 12.5e3, "FM")
    v2 = mgr.add(200e6, 10e3, "AM")
    v3 = mgr.add(300e6, 8e3, "USB")
    router = VfoAudioRouter(mgr)

    router.set_primary(v2.vfo_id)
    # v2 可听，v1/v3 静音
    assert v2.muted is False
    assert v1.muted is True
    assert v3.muted is True
    assert router.audible_vfo_id == v2.vfo_id


def test_switch_primary():
    mgr = VfoManager()
    v1 = mgr.add(100e6, 12.5e3, "FM")
    v2 = mgr.add(200e6, 10e3, "AM")
    router = VfoAudioRouter(mgr)

    router.set_primary(v1.vfo_id)
    assert router.audible_vfo_id == v1.vfo_id
    assert v2.muted is True

    router.set_primary(v2.vfo_id)
    assert router.audible_vfo_id == v2.vfo_id
    # v1 被静音
    assert v1.muted is True
    assert v2.muted is False


def test_manual_mute_overrides_primary():
    mgr = VfoManager()
    v1 = mgr.add(100e6, 12.5e3, "FM")
    router = VfoAudioRouter(mgr)
    router.set_primary(v1.vfo_id)
    assert router.audible_vfo_id == v1.vfo_id

    # 手动 mute primary
    router.toggle_manual_mute(v1.vfo_id)
    assert router.audible_vfo_id is None

    # 取消手动 mute 恢复
    router.toggle_manual_mute(v1.vfo_id)
    assert router.audible_vfo_id == v1.vfo_id


def test_advance_primary_cycles():
    mgr = VfoManager()
    v1 = mgr.add(100e6, 8e3, "FM")
    v2 = mgr.add(200e6, 8e3, "AM")
    v3 = mgr.add(300e6, 8e3, "USB")
    router = VfoAudioRouter(mgr)

    first = router.advance_primary()
    assert first.vfo_id == v1.vfo_id
    second = router.advance_primary()
    assert second.vfo_id == v2.vfo_id
    third = router.advance_primary()
    assert third.vfo_id == v3.vfo_id
    # 循环回 v1
    back = router.advance_primary()
    assert back.vfo_id == v1.vfo_id


def test_set_unknown_primary_returns_none():
    mgr = VfoManager()
    router = VfoAudioRouter(mgr)
    assert router.set_primary("nope") is None
    assert router.audible_vfo_id is None
