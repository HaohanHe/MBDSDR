"""
VFO 管理 + 频率管理器测试
=========================

对照上游：
- CubicSDR DemodulatorMgr.h:88-108  三态指针 activeContext/current/visual
- CubicSDR DemodulatorMgr.cpp:151-159 删除时清空指针
- SDR++ frequency_manager/main.cpp  书签增删改查 + JSON 持久化

测试：
1. VFO：创建2个VFO、切主听、改带宽、删除，断言状态一致。
2. FrequencyManager：增删书签、持久化到临时 JSON、读回，断言一致。
"""
import os
import sys
import json

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.vfo_manager import VfoManager, vfo_coverage
from mbdsdr_ai.frequency_manager import FrequencyManager


# ── VFO ─────────────────────────────────────────────────────────

def test_vfo_create_list():
    mgr = VfoManager()
    v1 = mgr.add(100e6, 12.5e3, "NFM")
    v2 = mgr.add(145e6, 12.5e3, "NFM")
    assert len(mgr.list_all()) == 2
    assert mgr.get(v1.vfo_id) is v1
    assert mgr.get(v2.vfo_id) is v2


def test_vfo_primary_switch():
    mgr = VfoManager()
    v1 = mgr.add(100e6, 12.5e3, "NFM")
    v2 = mgr.add(145e6, 12.5e3, "NFM")
    # 初始无主听
    assert mgr.active_vfo_id is None
    # 切到 v1
    mgr.set_primary(v1.vfo_id)
    assert mgr.active_vfo_id == v1.vfo_id
    # 切到 v2
    mgr.set_primary(v2.vfo_id)
    assert mgr.active_vfo_id == v2.vfo_id


def test_vfo_change_bandwidth():
    mgr = VfoManager()
    v1 = mgr.add(100e6, 12.5e3, "NFM")
    assert v1.bw_hz == 12.5e3
    v1.bw_hz = 25e3
    assert v1.bw_hz == 25e3


def test_vfo_remove_clears_pointers():
    """删除 VFO 后，若它是 current/active_context，指针应清空。"""
    mgr = VfoManager()
    v1 = mgr.add(100e6, 12.5e3, "NFM")
    v2 = mgr.add(145e6, 12.5e3, "NFM")
    mgr.set_primary(v1.vfo_id)
    assert mgr.current is v1
    # 删除 v1
    mgr.remove(v1.vfo_id)
    assert mgr.get(v1.vfo_id) is None
    assert mgr.current is None, "删除 current VFO 后指针应清空"
    assert len(mgr.list_all()) == 1


def test_vfo_ssb_asymmetric_coverage():
    """USB/LSB 占用区间不对称（对照 CubicSDR DemodulatorMgr.cpp:170-206）。"""
    lo, hi = vfo_coverage(14.074e6, 2.4e3, "USB")
    assert lo == 14.074e6
    assert hi == 14.074e6 + 2.4e3
    lo, hi = vfo_coverage(14.074e6, 2.4e3, "LSB")
    assert lo == 14.074e6 - 2.4e3
    assert hi == 14.074e6
    # FM 对称
    lo, hi = vfo_coverage(100e6, 200e3, "WFM")
    assert lo == 100e6 - 100e3
    assert hi == 100e6 + 100e3


# ── FrequencyManager ────────────────────────────────────────────

def test_fm_add_remove_bookmark(tmp_path):
    user_path = str(tmp_path / "bookmarks.json")
    fm = FrequencyManager(user_path=user_path)
    n_before = len(fm.list())
    bm = fm.add("测试台", 91.5e6, mode="WFM", category="测试",
                bandwidth_hz=200e3, note="test")
    assert len(fm.list()) == n_before + 1
    assert fm.remove("测试台") is True
    assert len(fm.list()) == n_before


def test_fm_persist_to_json(tmp_path):
    user_path = str(tmp_path / "bookmarks.json")
    fm1 = FrequencyManager(user_path=user_path)
    fm1.add("我的台", 99.1e6, mode="FM", category="自定义", note="persist")
    # 新实例读回
    fm2 = FrequencyManager(user_path=user_path)
    hits = [b for b in fm2.list() if b.name == "我的台"]
    assert len(hits) == 1
    assert hits[0].freq_hz == 99.1e6
    assert hits[0].mode == "FM"
    assert hits[0].note == "persist"


def test_fm_find_by_name():
    fm = FrequencyManager(user_path="/tmp/nonexistent_bookmarks_xyz.json")
    # 内置库应有调频广播
    found = fm.find("调频广播")
    assert found is not None
    assert found.start_hz == 87.5e6


def test_fm_nearest():
    fm = FrequencyManager(user_path="/tmp/nonexistent_bookmarks_xyz.json")
    # 121.5 MHz 是航空应急点频书签，nearest 应命中它
    bm = fm.nearest(121.5e6, within_hz=1e3)
    assert bm is not None
    assert "121.5" in bm.name
