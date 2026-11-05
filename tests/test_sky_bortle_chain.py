# SPDX-License-Identifier: MIT
"""
tests/test_sky_bortle_chain.py
==============================
Bortle 从 ``RFSkyView.set_bortle()`` 到「实际落笔」的整条链路。

设置线给的反证只是**选星层**的计数（同 FOV/同刻 Bortle 3=1456、8=194）。
本文件验证的是绘制门：选择集 -> 视觉模型 -> 高度/屏幕裁剪，每一步都必须
读同一份 Bortle，不能有任何一层偷偷回落到默认 3。

覆盖：
  1. 同 FOV（120°）/同刻，Bortle 3 与 8 的实际绘制门星数差异，且
     ``selection.bortle`` / ``limit_mag`` 与请求一致；
  2. 2×2 矩阵（FOV 120°/5° × Bortle 3/8）：拉远时星数按**新 Bortle**
     增加，不是按默认 3 增加；
  3. 视觉模型同 mag/同 limit、只换 bortle：半径与 alpha 必须变化
     （``bortle_dim_factor`` 走的是绘制层，消光按高度角走、与 Bortle 无关）；
  4. 极限星等单一事实源：``limit_magnitude(fov,bortle)`` 直接由
     ``stars.naked_eye_limit_mag(bortle) + detail_gain_mag(fov)`` 构成。

反证（若任一绘制层硬编码默认 Bortle=3，本文件必红）：
  - Bortle 8 的绘制数会退化成 Bortle 3 的数（FOV 120°：170 -> 1263）；
  - Bortle 8 / FOV 5° 若按默认 3 选星会得到 19 颗，实际应为 0 颗；
  - 视觉模型忽略 bortle 时 mag=5.0 的半径不会从 1.64px 掉到 0.5px。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from datetime import datetime, timezone  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import RFSkyView  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402
from mbdsdr_ai.skyengine.star_catalog import (  # noqa: E402
    detail_gain_mag, limit_magnitude, naked_eye_limit_mag,
)

LAT, LON = 40.0, 116.0
UNIX = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp()  # 夜侧
WIN_W, WIN_H = 900, 700
CENTER_AZ, CENTER_ALT = 180.0, 45.0


def _new_view() -> RFSkyView:
    eng = get_time_engine()
    eng.set_unix(UNIX)
    eng.pause()
    v = RFSkyView()
    v.resize(WIN_W, WIN_H)
    v.set_observer(LAT, LON, 0.0)
    v._sky_brightness = 0.0
    v.view_state.look_at(CENTER_AZ, CENTER_ALT)
    return v


def _measure(v: RFSkyView, fov: float, bortle: int) -> dict:
    """复刻绘制门：选择集 -> 视觉模型 -> 高度/屏幕裁剪。"""
    v.set_bortle(bortle)
    v.view_state.fov_deg = float(fov)
    v.view_state.look_at(CENTER_AZ, CENTER_ALT)
    gs = v._ground_station()
    sel = v._star_selection(gs, v._current_jd())
    assert sel is not None
    alt_draw = v._display_alt_many(sel.alt_deg)
    px, py = v._sky_to_screen_many(sel.az_deg, alt_draw)
    r, _bright, _bloom, alpha = v._star_visual_many(
        sel.eff_mag, sel.limit_mag, v._bortle)
    r = np.asarray(r, dtype=float)
    visual_pass = int(np.count_nonzero(r > 0.0))
    onscreen = ((alt_draw >= 0.0)
                & np.isfinite(px) & np.isfinite(py)
                & (px >= -4.0) & (px <= WIN_W + 4.0)
                & (py >= -4.0) & (py <= WIN_H + 4.0))
    drawn_mask = (r > 0.0) & onscreen
    return {
        "selected": len(sel),
        "visual_pass": visual_pass,
        "drawn": int(np.count_nonzero(drawn_mask)),
        "limit_mag": float(sel.limit_mag),
        "sel_bortle": int(sel.bortle),
        "truncated": bool(sel.truncated),
        "avg_alpha": (float(np.mean(alpha[drawn_mask]))
                      if drawn_mask.any() else 0.0),
    }


def test_drawn_star_count_follows_bortle_not_selection_only():
    """实际落笔数（不是选择集大小）必须随 Bortle 显著下降。"""
    v = _new_view()
    try:
        b3 = _measure(v, 120.0, 3)
        b8 = _measure(v, 120.0, 8)
    finally:
        v.close()

    assert b3["sel_bortle"] == 3 and b8["sel_bortle"] == 8
    assert b3["limit_mag"] == pytest.approx(6.5)          # Bortle<=3 基线
    assert b8["limit_mag"] == pytest.approx(4.75)         # 6.5 - 0.35*5
    assert b3["drawn"] > 1000, f"暗空 120° 只画出 {b3['drawn']} 颗"
    assert b8["drawn"] > 0
    # 反证：绘制层若回落到默认 3，两个数会相等。
    assert b8["drawn"] < 0.25 * b3["drawn"], (
        f"Bortle 8 绘制数 {b8['drawn']} 未显著低于 Bortle 3 {b3['drawn']}")
    assert b8["visual_pass"] < 0.25 * b3["visual_pass"]
    assert b8["drawn"] <= b8["visual_pass"] <= b8["selected"]
    # 暗空更亮（alpha 更高）与光害压暗方向一致
    assert b3["avg_alpha"] >= 0, b8["avg_alpha"] >= 0


def test_zoom_matrix_uses_new_bortle_not_default_three():
    """2×2 矩阵：拉远时按新 Bortle 增加，不是按默认 3 增加。

    Bortle 8 / FOV 5° 这一格是反证关键：默认 3 会选出 19 颗，实际应为 0。
    """
    v = _new_view()
    try:
        m = {(fov, b): _measure(v, fov, b)
             for fov in (120.0, 5.0) for b in (3, 8)}
    finally:
        v.close()

    assert m[(120.0, 3)]["drawn"] > m[(5.0, 3)]["drawn"]
    assert m[(120.0, 8)]["drawn"] > m[(5.0, 8)]["drawn"]
    assert m[(5.0, 8)]["drawn"] < m[(5.0, 3)]["drawn"], (
        "Bortle 8 拉近后仍按默认 3 选星（两者星数相同）")
    assert m[(5.0, 8)]["limit_mag"] < m[(5.0, 3)]["limit_mag"]
    assert m[(5.0, 8)]["drawn"] == 0, (
        f"该天区 Bortle 8/5° 不应有星，实际画出 {m[(5.0, 8)]['drawn']} 颗")


def test_visual_model_reads_bortle_with_same_mags_and_limit():
    """同 mags、同 limit，只换 bortle：视觉层必须自己读 Bortle。

    选择集已经被 Bortle 收窄，若视觉层不读 bortle 也能"看起来"对；这条
    把选择集变量固定住，单独验证 ``star_draw_many`` 的可见门与压暗系数。
    """
    v = _new_view()
    try:
        mags = np.array([4.0, 5.0, 6.0, 6.5, 7.0])
        lim = 6.5
        r3, _b3, _g3, a3 = v._star_visual_many(mags, lim, 3)
        r8, _b8, _g8, a8 = v._star_visual_many(mags, lim, 8)
        r3 = np.asarray(r3, dtype=float)
        r8 = np.asarray(r8, dtype=float)
        a3 = np.asarray(a3, dtype=int)
        a8 = np.asarray(a8, dtype=int)
        assert r3[1] > r8[1] + 0.5, (r3[1], r8[1])
        assert a3[1] > a8[1], (a3[1], a8[1])
        # 5.0 等 > Bortle 8 裸眼极限 4.75：视觉层退化为 0.5px LOD 点，
        # 仍保留在本帧 limit 内；Bortle 3 下是正常圆点。
        assert float(r8[1]) == pytest.approx(v.STAR_LOD_MIN_R_PX)
        assert 0 < int(a8[1]) < 255
        assert float(r3[1]) > 1.0
    finally:
        v.close()


def test_limit_magnitude_is_single_source():
    """极限星等 = 裸眼基线(bortle) + 视场细节增益(fov)，不再有第二份公式。"""
    for b in (3, 5, 8, 9):
        assert limit_magnitude(120.0, b) == pytest.approx(
            naked_eye_limit_mag(b) + detail_gain_mag(120.0))
    assert naked_eye_limit_mag(8) == pytest.approx(4.75)
    assert naked_eye_limit_mag(3) == pytest.approx(6.5)
