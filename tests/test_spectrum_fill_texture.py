# SPDX-License-Identifier: MIT
"""
tests/test_spectrum_fill_texture.py
===================================
谱迹填充「渐变贴图 + 整数 QRegion 裁剪」的两条守卫（2026-11-11）。

背景：`_draw_spectrum` 原来把渐变笔刷挂在 `drawPolygon` 上，噪声谱每扫描线
数百个跨度，光栅器逐跨度算渐变；改为「1×h 渐变贴图 + 整数区域裁剪后贴一次」
后 `_draw_spectrum` 与整幅重绘都显著下降。代价是填充边界从浮点多边形变成
整数顶点，上下边界有 1 px 量级的位移（非抗锯齿）。

等价口径（固定，不得放宽）：
* 与旧渐变笔刷实现的逐像素差异 **≤0.7%**；
* 差异像素**全部**落在填充多边形边界 **±1 px 带内**——不是「任意 0.7%」，
  否则阈值会退化成免责牌；
* 单通道最大差 ≤70（同口径，用于挡住色带级别的错位）。

另有一条缓存守卫：渐变贴图按画布高度缓存，**高度变化必须重建**，不得把
旧高度的图拉去贴新高度（QRegion 每次绘制按当前 fillpoly 现建，不缓存）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_spectrum_fill_texture.py -v
"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QBrush, QColor, QImage, QLinearGradient, QPainter, QPen, QPolygonF,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import spectrum_widget as sw  # noqa: E402

#: 填充边界带半宽（px）：判决口径固定为 ±1 px，带用 3 px 纯色描边生成。
_BAND_HALF_PX = 1
_BAND_PEN_W = 2 * _BAND_HALF_PX + 1

#: 差异上限（固定口径，见模块 docstring）。
_MAX_DIFF_PCT = 0.7
_MAX_CHANNEL = 70


def _make_panel(fft: int = 1024, bins: int = 1024, nan_gaps: bool = False):
    """真 IQ 噪声谱：与 docs 里 7 用例口径同源（固定种子、固定 dB 量程）。"""
    panel = sw.SpectrumPanel()
    panel.resize(1400, 800)
    panel.show()
    app.processEvents()
    gen = panel.generator
    gen.reconfigure(fft, bins)
    gen.set_avg_frames(1)
    rng = np.random.default_rng(7)
    iq = (rng.standard_normal(fft)
          + 1j * rng.standard_normal(fft)).astype(np.complex64)
    for _ in range(40):
        gen.push_iq(iq, 100e6, 2.4e6)
    if nan_gaps:
        spec = gen.spectrum
        spec[100:160] = np.nan      # 一段「未采到」
        spec[400] = np.nan          # 单点退化段
        spec[700:760] = np.nan
    app.processEvents()
    assert gen.has_data(), "先要有真 IQ 才能断言填充"
    state = panel._state
    # 与文档 7 用例同口径：量程走产品自动量程（不是测试自选 dB 区间）
    sw._auto_db_range(state)
    state.view_offset_hz = 0.0
    return panel, state


def _spec_rect(panel) -> QRectF:
    w = panel._fft_plot.width()
    h = max(1, panel._fft_plot.height() - sw._SPEC_BOTTOM_PAD)
    return QRectF(0, 0, w, h)


def _paint_gradient_fill(painter, state, fillpoly, rect):
    """旧实现（HEAD）的填充：渐变笔刷 drawPolygon；其余描边由调用方负责。"""
    grad = QLinearGradient(0, rect.y(), 0, rect.y() + rect.height())
    top = QColor(sw.PAL_TRACE)
    top.setAlpha(sw._TRACE_FILL_TOP_ALPHA)
    bot = QColor(sw.PAL_TRACE)
    bot.setAlpha(sw._TRACE_FILL_BOT_ALPHA)
    grad.setColorAt(0.0, top)
    grad.setColorAt(1.0, bot)
    painter.setBrush(QBrush(grad))
    painter.drawPolygon(fillpoly)
    painter.setBrush(Qt.NoBrush)


def _render(state, rect, use_gradient: bool) -> np.ndarray:
    """只画谱线/填充层（背景同色），把填充实现之外的所有差异排除掉。"""
    w, h = int(round(rect.width())), int(round(rect.height()))
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(sw.PAL_BG))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing, False)
    real = sw._fill_spectrum_under
    if use_gradient:
        sw._fill_spectrum_under = _paint_gradient_fill
    try:
        sw._draw_spectrum(painter, state, rect)
    finally:
        sw._fill_spectrum_under = real
        painter.end()
    return np.frombuffer(bytes(img.constBits()), dtype=np.uint8).reshape(
        h, img.bytesPerLine())[:, :w * 4].reshape(h, w, 4).copy()


def _fill_polygons(state, rect):
    """按 `_draw_spectrum` 的同一几何（`_spectrum_xy`）重建填充多边形。"""
    x, y, floor_y, finite = sw._spectrum_xy(state, rect)
    rx0, rw = rect.x(), rect.width()
    n = len(x)
    polys = []

    def _one(xs, ys):
        poly = QPolygonF([QPointF(float(a), float(b)) for a, b in zip(xs, ys)])
        fp = QPolygonF()
        fp.append(QPointF(float(xs[0]), floor_y))
        fp += poly
        fp.append(QPointF(float(xs[-1]), floor_y))
        polys.append(fp)

    if finite.all():
        _one(x, y)
        return polys
    start = None
    for i in range(n + 1):
        ok = bool(finite[i]) if i < n else False
        if ok and start is None:
            start = i
        elif not ok and start is not None:
            a, b = start, i
            start = None
            if b - a >= 2:
                _one(x[a:b], y[a:b])
    return polys


def _boundary_band(state, rect) -> np.ndarray:
    """填充多边形边界 ±1 px 带（非抗锯齿 3 px 描边），True=允许差异。"""
    w, h = int(round(rect.width())), int(round(rect.height()))
    band = QImage(w, h, QImage.Format.Format_Grayscale8)
    band.fill(0)
    painter = QPainter(band)
    painter.setRenderHint(QPainter.Antialiasing, False)
    painter.setPen(QPen(QColor(255, 255, 255), _BAND_PEN_W))
    painter.setBrush(Qt.NoBrush)
    for fp in _fill_polygons(state, rect):
        painter.drawPolygon(fp)
    painter.end()
    arr = np.frombuffer(bytes(band.constBits()), dtype=np.uint8).reshape(
        h, band.bytesPerLine())[:, :w]
    return arr > 0


def _diff_stats(a: np.ndarray, b: np.ndarray, band: np.ndarray) -> dict:
    d = np.abs(a.astype(np.int16) - b.astype(np.int16))
    bad = d.max(axis=2) > 0
    return {
        "total": int(bad.size),
        "bad": int(bad.sum()),
        "pct": 100.0 * float(bad.sum()) / float(bad.size),
        "maxch": int(d.max()),
        "outside": int((bad & ~band).sum()),
        "rows": (int(np.where(bad.any(axis=1))[0].min()),
                 int(np.where(bad.any(axis=1))[0].max())) if bad.any() else None,
    }


class TestFillTexturePixelEquivalence:
    """守卫一：差异 ≤0.7% 且全部落在填充边界 ±1 px 带内。"""

    def test_noise_spectrum(self):
        panel, state = _make_panel()
        try:
            rect = _spec_rect(panel)
            current = _render(state, rect, use_gradient=False)
            legacy = _render(state, rect, use_gradient=True)
            stats = _diff_stats(current, legacy, _boundary_band(state, rect))
            assert stats["pct"] <= _MAX_DIFF_PCT, stats
            assert stats["maxch"] <= _MAX_CHANNEL, stats
            assert stats["outside"] == 0, (
                f"{stats['outside']} px 的差异落在填充边界 ±1 px 带外：{stats}")
        finally:
            panel.deleteLater()
            app.processEvents()

    def test_nan_gaps_keep_segments_inside_band(self):
        panel, state = _make_panel(nan_gaps=True)
        try:
            rect = _spec_rect(panel)
            current = _render(state, rect, use_gradient=False)
            legacy = _render(state, rect, use_gradient=True)
            stats = _diff_stats(current, legacy, _boundary_band(state, rect))
            assert stats["pct"] <= _MAX_DIFF_PCT, stats
            assert stats["maxch"] <= _MAX_CHANNEL, stats
            assert stats["outside"] == 0, (
                f"{stats['outside']} px 的差异落在填充边界 ±1 px 带外：{stats}")
        finally:
            panel.deleteLater()
            app.processEvents()


class TestFillCacheKeyedByHeight:
    """守卫二：渐变贴图按高度缓存；改高度后不得复用旧尺寸的图。"""

    def test_height_change_rebuilds_image(self):
        state = types.SimpleNamespace(_spec_fill_img=None, _spec_fill_h=-1)
        first = sw._trace_fill_image(state, 120)
        assert first.height() == 120
        assert state._spec_fill_h == 120
        # 同高度必须复用（每帧重建会白付一次渐变渲染）
        assert sw._trace_fill_image(state, 120) is first

        taller = sw._trace_fill_image(state, 240)
        assert taller.height() == 240, (
            f"改高到 240 后仍拿到 {taller.height()} 的旧图（缓存没按高度失效）")
        assert state._spec_fill_h == 240
        assert taller is not first

        # 再回到旧高度：不得把 240 的图当 120 用（单槽缓存必须重建）
        again = sw._trace_fill_image(state, 120)
        assert again.height() == 120
        assert state._spec_fill_h == 120
        assert again is not taller

    def test_rebuilt_image_ends_match_new_height(self):
        state = types.SimpleNamespace(_spec_fill_img=None, _spec_fill_h=-1)
        sw._trace_fill_image(state, 120)
        img = sw._trace_fill_image(state, 240)
        top_alpha = img.pixelColor(0, 0).alpha()
        bot_alpha = img.pixelColor(0, 239).alpha()
        assert abs(top_alpha - sw._TRACE_FILL_TOP_ALPHA) <= 2, top_alpha
        assert abs(bot_alpha - sw._TRACE_FILL_BOT_ALPHA) <= 2, bot_alpha
