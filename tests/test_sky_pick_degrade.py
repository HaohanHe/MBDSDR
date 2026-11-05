#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
tests/test_sky_pick_degrade.py — `_find_body_at` 的静默 catch-all 收口
=======================================================================

审计清单：`desktop/rf_sky_view.py` 的 `_find_body_at` 有 6 处静默 catch-all
（2 处空 `except: pass` + 4 处静默 return/continue）。事故前例：`float(None)`
被 `except Exception: pass` 吞掉，整个 DSO 图层既不画也点不中、还不报错。

收口口径：
  - **外部数据边界**（历表、DSO 目录、单条星表行、批量换算、HYG id）：
    只降级受影响的那一部分，同时写日志 + `_pick_degrade` 状态位；
  - **编程错误**（AttributeError/IndexError 等）：原样抛出，宁可测试红。

反证（修复前必红）：
  - 行星历表抛 `ValueError`：旧代码 `pass`，`_pick_degrade` 为空、无日志；
  - 行星历表抛 `AttributeError`（编程错误）：旧代码静默吞掉，`pytest.raises`
    拿不到异常；
  - 批量落屏抛 `ValueError`：旧代码 `star_idx=[]`，整帧星表不可点。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import math  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import RFSkyView  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402

LAT, LON = 40.0, 116.0
UNIX = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp()
WIN = 700
CENTER_AZ, CENTER_ALT = 180.0, 45.0
FOV = 30.0


def _new_view(size: int = WIN) -> RFSkyView:
    eng = get_time_engine()
    eng.set_unix(UNIX)
    eng.pause()
    v = RFSkyView()
    v.resize(size, size)
    v.set_observer(LAT, LON, 0.0)
    v.view_state.fov_deg = FOV
    v._bortle = 3
    v._sky_brightness = 0.0
    v.view_state.look_at(CENTER_AZ, CENTER_ALT)
    return v


def _pickable_stars(v, sel, unnamed_only=False):
    """返回 [(index, px, py)]：当帧视口内真的能画出来（r_pick>0）的星。"""
    out = []
    for i in range(len(sel)):
        if unnamed_only and str(sel.name[i] or ""):
            continue
        alt_draw = v._display_alt_deg(float(sel.alt_deg[i]))
        if alt_draw < 0.0:
            continue
        scr = v._sky_to_screen_finite(float(sel.az_deg[i]), alt_draw)
        if scr is None or not (10.0 < scr.x() < WIN - 10.0
                               and 10.0 < scr.y() < WIN - 10.0):
            continue
        r_pick = v._star_visual(
            float(sel.eff_mag[i]), sel.limit_mag, v._bortle)[0]
        if r_pick <= 0:
            continue
        out.append((i, scr.x(), scr.y()))
    return out


class _RowProxy:
    """真实选星结果的行级代理：只在目标行上替换一个字段。"""

    def __init__(self, real, **overrides):
        self._real = real
        for k, val in overrides.items():
            setattr(self, k, val)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __len__(self):
        return len(self._real)

    def __getitem__(self, i):
        return self._real[i]


@pytest.fixture()
def view_and_star():
    v = _new_view()
    try:
        sel = v._star_selection(v._ground_station(), v._current_jd())
        v._star_sel = sel
        stars = _pickable_stars(v, sel)
        assert stars, "测试前提：视场内应有可拾取恒星"
        idx, px, py = stars[0]
        yield v, sel, idx, px, py
    finally:
        v.close()


# --------------------------------------------------------------------------
# ① 行星历表 ValueError（外部边界）→ 降级 + 日志，恒星照常可点
# --------------------------------------------------------------------------
def test_planet_value_error_degrades_with_log_and_star_pick_still_works(
        view_and_star, monkeypatch, caplog):
    v, sel, idx, px, py = view_and_star

    def boom(_jd):
        raise ValueError("ephemeris out of range")

    monkeypatch.setattr(v, "_planet_positions", boom)
    got = v._find_body_at(px, py, tolerance=3.0)
    assert got is not None and got["kind"] == "star", "行星降级不应拖垮恒星拾取"
    assert "行星历表不可用" in v._pick_degrade
    assert any("行星历表不可用" in r.getMessage() for r in caplog.records), \
        "外部边界降级必须留下日志"


# --------------------------------------------------------------------------
# ② 行星历表 AttributeError（编程错误）→ 必须抛出，不许吞
# --------------------------------------------------------------------------
def test_planet_programming_error_propagates(view_and_star, monkeypatch):
    v, _sel, _idx, px, py = view_and_star

    def boom(_jd):
        raise AttributeError("eph.magnetude typo")

    monkeypatch.setattr(v, "_planet_positions", boom)
    with pytest.raises(AttributeError):
        v._find_body_at(px, py, tolerance=3.0)


# --------------------------------------------------------------------------
# ③ DSO 目录缺模块（外部边界）→ 降级 + 日志，恒星照常可点
# --------------------------------------------------------------------------
def test_dso_import_failure_degrades_with_log(view_and_star,
                                              monkeypatch, caplog):
    v, _sel, _idx, px, py = view_and_star
    # 仅置 sys.modules[...]=None 不够：包对象上已缓存 `dso` 属性时，
    # `from mbdsdr_ai.astronomy import dso` 会直接取属性、根本不查
    # sys.modules。必须同时摘掉包属性，才真的走到导入失败分支。
    import mbdsdr_ai.astronomy as _pkg
    monkeypatch.delattr(_pkg, "dso", raising=False)
    monkeypatch.setitem(sys.modules, "mbdsdr_ai.astronomy.dso", None)

    got = v._find_body_at(px, py, tolerance=3.0)
    assert got is not None and got["kind"] == "star"
    assert "DSO 目录不可用" in v._pick_degrade
    assert any("DSO 目录不可用" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------
# ④ 批量落屏 ValueError（外部边界）→ 逐颗降级路径仍能点中
# --------------------------------------------------------------------------
def test_batch_screen_failure_falls_back_to_per_star(view_and_star,
                                                     monkeypatch, caplog):
    v, sel, idx, px, py = view_and_star

    def boom(_az, _alt):
        raise ValueError("bad batch")

    monkeypatch.setattr(v, "_sky_to_screen_many", boom)
    got = v._find_body_at(px, py, tolerance=3.0)
    assert got is not None and got["kind"] == "star", \
        "批量换算失败后必须逐颗降级，而不是整帧不可点"
    assert f"star@{idx}" in got["key"] or got["hyg_id"] >= 0
    assert "星表批量换算失败" in v._pick_degrade
    assert any("星表批量换算失败" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------
# ⑤ 单条星表行 TypeError → 跳过该行 + 记录，其它星仍可点
# --------------------------------------------------------------------------
def test_malformed_row_is_skipped_not_swallowed(view_and_star,
                                                monkeypatch, caplog):
    v, sel, idx, px, py = view_and_star

    def boom(_az, _alt):
        raise ValueError("bad batch")

    # 强制走逐颗路径，让「坏行」一定被迭代到
    monkeypatch.setattr(v, "_sky_to_screen_many", boom)
    mags = np.asarray(sel.eff_mag, dtype=float)
    faint = int(mags.argmax())
    # 必须选目标行之外的坏行：否则被弄坏的就是"要点中的那颗"，
    # 测试会误报成"降级把可点的星也弄丢了"。
    bad = faint if faint != idx else int(mags.argmin())
    em = np.asarray(sel.eff_mag, dtype=object).copy()
    em[bad] = "not-a-number"
    v._star_sel = _RowProxy(sel, eff_mag=em)

    got = v._find_body_at(px, py, tolerance=3.0)
    assert got is not None and got["kind"] == "star"
    assert "星表行数据异常" in v._pick_degrade
    assert any("星表行数据异常" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------
# ⑥ HYG id 缺失 → hyg=-1，不拿 0 冒充编号，key 不撞车
# --------------------------------------------------------------------------
def test_missing_hyg_id_uses_row_fallback_not_zero(view_and_star,
                                                   caplog):
    v, sel, idx, px, py = view_and_star
    stars = _pickable_stars(v, sel, unnamed_only=True)
    assert stars, "测试前提：视场内应有无名星"
    idx, px, py = stars[0]

    hyg = np.asarray(sel.hyg_id, dtype=object).copy()
    hyg[idx] = None
    v._star_sel = _RowProxy(sel, hyg_id=hyg)

    got = v._find_body_at(px, py, tolerance=3.0)
    assert got is not None and got["kind"] == "star"
    assert got["hyg_id"] == -1, "缺 id 不得用 0 冒充"
    assert got["key"] == f"star@{idx}", "缺 id 的 key 应退化为行号，避免撞车"
    assert "HYG id 缺失" in v._pick_degrade
    assert any("HYG id 缺失" in r.getMessage() for r in caplog.records)
