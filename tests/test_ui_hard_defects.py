# SPDX-License-Identifier: MIT
"""
tests/test_ui_hard_defects.py
=============================
截图硬缺陷修复钉扎（第三件）：

  1. 峰值标注碰撞剔除（_peak_label_placements 纯函数）：
     标注锚定自己的峰不水平漂移（防误归属），冲突沿纵向分行错开、
     行满/出界诚实丢弃、右界不越界。
  2. Max/Min 纵轴滑块方向（用户裁决：上推 = 图形上移）：
     invertedAppearance 后旋钮顶=量程最小端；夹逻辑（max>=min+10，
     SDR++ fftMax 同款）把顶档收敛到 db_min+10；三档单调性——
     同一信号在「旋钮顶（max 档）」的绘制位置必须比中档、底档更靠上。
  3. 三栏比例：弹性权重给左栏工作面（stretch=1），中栏 ADS-B 空态
     stretch=0 不吞主区；1000×600 与 1600×900 两档窗口下左栏份额
     不小于中栏，ADS-B 面板最小宽 240（可压）。
  4. 顶部频率读数（FreqDisplay）整体缩放适配：窄宽度下逐级缩字号/
     极端压格宽，整行完整单排、x 不为负、右缘不越界——绝不裁尾、
     绝不按字符换行。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui_hard_defects.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtCore import QRectF, QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from spectrum_widget import _db_to_y, _peak_label_placements  # noqa: E402
from freq_display import FreqDisplay  # noqa: E402


@pytest.fixture
def clean_window_settings():
    """隔离窗口几何持久化（QSettings + gui_config.json 双通道）：
    备份→清空→跑→还原。既不读旧存量（几何/分栏比例/最大化标志会
    盖过测试 resize），也不把测试几何写进用户真实配置。"""
    s = QSettings("MBDSDR", "Desktop")
    keys = ("window_geometry", "three_column_sizes",
            "three_column_layout_version")
    backup = {k: s.value(k) for k in keys}
    for k in keys:
        s.remove(k)
    s.sync()
    import json as _json
    gui_cfg = os.path.expanduser("~/.mbdsdr/gui_config.json")
    cfg_backup = None
    if os.path.exists(gui_cfg):
        with open(gui_cfg, "r", encoding="utf-8") as f:
            cfg_backup = f.read()
        try:  # 摘掉 window 键：_load_gui_config 的 100ms 定时器不再改几何
            data = _json.loads(cfg_backup)
            if "window" in data:
                data.pop("window")
                with open(gui_cfg, "w", encoding="utf-8") as f:
                    _json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    yield
    for k, v in backup.items():
        if v is None:
            s.remove(k)
        else:
            s.setValue(k, v)
    s.sync()
    if cfg_backup is not None:
        with open(gui_cfg, "w", encoding="utf-8") as f:
            f.write(cfg_backup)


def _stop_startup_timers(win):
    """停掉启动延迟定时器（_load_gui_config 会在 100ms 后覆写窗口几何、
    _auto_enumerate_and_connect 会碰硬件枚举）——布局测试不需要它们。"""
    for t in getattr(win, "_startup_timers", []):
        try:
            t.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 1. 峰值标注碰撞剔除（纯函数）
# ---------------------------------------------------------------------------
class TestPeakLabelPlacements:
    @staticmethod
    def _measure(text):
        return float(len(text)) * 6.0      # 固定字宽替身（纯函数无需字体）

    def test_no_overlap_same_row(self):
        pts = [(10.0, 50.0, "156.255MHz -14.5dB"),
               (30.0, 60.0, "156.294MHz -13.2dB"),
               (50.0, 55.0, "156.410MHz -11.2dB")]
        out = _peak_label_placements(pts, self._measure, 0.0, 400.0, 12.0)
        # 同行内标签区间两两不交叠
        by_row = {}
        for tx, _ty, label, row in out:
            by_row.setdefault(row, []).append((tx, label))
        for row, items in by_row.items():
            items.sort()
            for (x1, l1), (x2, l2) in zip(items, items[1:]):
                assert x2 >= x1 + self._measure(l1) + 4.0 - 1e-9, \
                    f"row {row} 叠字: {l1} / {l2}"

    def test_overflow_falls_to_next_row(self):
        # 三个紧邻峰（截图现场）：前两个放不下同一行时进不同行
        pts = [(10.0, 50.0, "156.255MHz -14.5dB"),
               (12.0, 60.0, "156.294MHz -13.2dB"),
               (14.0, 55.0, "156.410MHz -11.2dB")]
        out = _peak_label_placements(pts, self._measure, 0.0, 200.0, 12.0)
        rows = {label: row for _x, _y, label, row in out}
        assert len(set(rows.values())) >= 2, "紧邻峰必须分行错开"
        # 行号越大越靠下（y 递减单调）
        ys = {label: y for _x, y, label, row in out}
        for label, row in rows.items():
            base_y = {l: y0 for _x0, y0, l in pts}[label]
            assert ys[label] == base_y - 6.0 - row * 12.0

    def test_right_boundary_clamped(self):
        pts = [(390.0, 50.0, "157.046MHz -12.1dB")]
        out = _peak_label_placements(pts, self._measure, 0.0, 400.0, 12.0)
        if out:  # 放得下则必须在界内，放不下则诚实丢弃
            tx, _y, label, _r = out[0]
            assert tx + self._measure(label) <= 400.0
        pts2 = [(399.0, 50.0, "157.046MHz -12.1dB")]
        out2 = _peak_label_placements(pts2, self._measure, 0.0, 400.0, 12.0)
        assert out2 == [], "越界标签必须诚实丢弃（不叠不压）"

    def test_max_rows_drop(self):
        # 5 个同 x 标签，max_rows=4 → 第 5 个被丢弃
        pts = [(10.0, 50.0 + i, f"peak{i}") for i in range(5)]
        out = _peak_label_placements(pts, self._measure, 0.0, 200.0, 12.0)
        assert len(out) == 4
        assert {label for _x, _y, label, _r in out} == \
            {"peak0", "peak1", "peak2", "peak3"}


# ---------------------------------------------------------------------------
# 2. Max/Min 滑块方向（三档单调性）
# ---------------------------------------------------------------------------
class TestSliderDirectionMonotonic:
    def test_inverted_appearance_flags(self):
        """两个滑杆都开启 invertedAppearance（顶=最小值）。"""
        from spectrum_widget import SpectrumPanel
        w = SpectrumPanel()
        assert w._db_max_slider.invertedAppearance() is True
        assert w._db_min_slider.invertedAppearance() is True
        w.deleteLater()

    def test_knob_top_converges_to_min_edge(self):
        """invertedAppearance：旋钮推到顶（Max 档）→ 取量程最小端，
        夹逻辑（max >= min+10，SDR++ fftMax 同款）收敛到 db_min+10。"""
        from spectrum_widget import SpectrumPanel
        w = SpectrumPanel()
        db_min0 = float(w._state.db_min)
        # 顶档（用户「推到 Max」）：inverted 下 setValue(minimum) 即旋钮在顶
        w._db_max_slider.setValue(w._db_max_slider.minimum())
        assert w._db_max_slider.value() == int(db_min0) + 10
        assert w._state.db_max == db_min0 + 10.0
        w.deleteLater()

    def test_trace_moves_up_three_steps(self):
        """三档单调性：同一弱信号，max 档（顶）> 中档 > 底档 的绘制高度。

        强信号超出量程会被夹在顶边（两档同高是正确行为）；用 -95dB 弱
        信号（三档都在量程内）钉「推到 max 图形往上走」的单调方向。
        """
        rect = QRectF(0, 0, 400, 300)
        db_signal, db_min = -95.0, -100.0
        # inverted 语义三档：顶=-160(经夹→min+10=-90)、中=-80、底=0
        y_top = _db_to_y(rect, db_signal, db_min, db_min + 10.0)   # max 档
        y_mid = _db_to_y(rect, db_signal, db_min, -80.0)           # 中档
        y_bot = _db_to_y(rect, db_signal, db_min, 0.0)             # 底档
        assert y_top < y_mid < y_bot, \
            f"推到 max 图形应更靠上: {y_top} {y_mid} {y_bot}"


# ---------------------------------------------------------------------------
# 3. FreqDisplay 整体缩放适配
# ---------------------------------------------------------------------------
class TestFreqDisplayFit:
    @staticmethod
    def _make(width, hz=156_525_000):
        fd = FreqDisplay()
        fd.set_frequency(hz)
        fd.resize(width, 60)
        return fd

    def test_narrow_width_no_clip(self):
        for width in (290, 200, 160, 120, 90, 60, 40):
            fd = self._make(width)
            cells = fd._cells()
            assert cells, f"width={width} 无格"
            xs = [c[1].left() for c in cells]
            rights = [c[1].right() for c in cells]
            assert min(xs) >= -0.5, f"width={width} 左溢出 {min(xs)}"
            assert max(rights) <= width + 0.5, \
                f"width={width} 右溢出 {max(rights)}"
            # 单排：所有格同 y（绝不按字符换行）
            ys = {round(c[1].top(), 1) for c in cells}
            assert len(ys) == 1, f"width={width} 出现多行"
            fd.deleteLater()

    def test_font_shrinks_when_narrow(self):
        wide = self._make(400)
        narrow = self._make(120)
        cw_wide = wide._cells()[0][1].width()
        cw_narrow = narrow._cells()[0][1].width()
        assert cw_narrow < cw_wide, "窄宽度必须整体缩小"
        wide.deleteLater()
        narrow.deleteLater()

    def test_centered(self):
        fd = self._make(200)
        cells = fd._cells()
        left = min(c[1].left() for c in cells)
        right = max(c[1].right() for c in cells)
        # 左右边距相等即居中
        assert abs(left - (200 - right)) <= 2.0, "整行应居中"
        fd.deleteLater()


# ---------------------------------------------------------------------------
# 4. 三栏比例（权重/尺寸来源 + 两种窗口宽）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication(sys.argv)


class TestThreeColumnLayout:
    def _sizes_at(self, qapp, w, h):
        from main_window import MainWindow
        win = MainWindow()
        _stop_startup_timers(win)
        win.resize(w, h)
        win.show()
        qapp.processEvents()
        sizes = list(win._main_splitter.sizes())
        try:
            win._disconnect()
        except Exception:
            pass
        win.close()
        return sizes

    def test_stretch_gives_left_the_flex(self, qapp, clean_window_settings):
        """窗口加宽 600px：增量必须全部进左栏（stretch=1 的行为证据）。"""
        s1 = self._sizes_at(qapp, 1000, 600)
        s2 = self._sizes_at(qapp, 1600, 900)
        d_left = s2[0] - s1[0]
        d_center = s2[1] - s1[1]
        d_right = s2[2] - s1[2]
        # 中/右栏固定基线：宽度增长 ≤4px（handle 取整容差）
        assert abs(d_center) <= 4, (s1, s2)
        assert abs(d_right) <= 4, (s1, s2)
        # 左栏吃掉几乎全部增量（600 ± 8：边框/手柄/最小宽钳位）
        assert d_left >= 592, (s1, s2)

    @pytest.mark.parametrize("w,h", [(1000, 600), (1600, 900)])
    def test_left_not_starved(self, qapp, clean_window_settings, w, h):
        sizes = self._sizes_at(qapp, w, h)
        assert all(s > 0 for s in sizes), f"{w}x{h} 出现零宽栏"
        # 左栏工作面份额不得小于中栏（ADS-B 空态不许占主区）
        assert sizes[0] >= sizes[1], (w, h, sizes)

    def test_adsb_panel_minimum_shrunk(self, qapp, clean_window_settings):
        from main_window import MainWindow
        win = MainWindow()
        adsb = getattr(win, "adsb_map_panel", None)
        assert adsb is not None
        assert adsb.minimumWidth() <= 240, "ADS-B 空态最小宽必须可压"
        win.close()


# ---------------------------------------------------------------------------
# 5. 两种窗口宽下频谱页无死黑证据（截图烟证）
# ---------------------------------------------------------------------------
class TestTwoWidthsSmoke:
    @pytest.mark.parametrize("w,h", [(1000, 600), (1600, 900)])
    def test_spectrum_tab_renders(self, qapp, clean_window_settings,
                                  w, h, tmp_path):
        """两档窗口宽：切到频谱页，截图非纯黑、控件不重叠崩溃。"""
        from main_window import MainWindow
        win = MainWindow()
        _stop_startup_timers(win)
        win.resize(w, h)
        win.show()
        qapp.processEvents()
        tab = win.left_tab
        idx = next(i for i in range(tab.count())
                   if tab.tabBar().tabData(i) == "spectrum")
        tab.setCurrentIndex(idx)
        qapp.processEvents()
        img = win.grab().toImage()
        assert not img.isNull()
        out = tmp_path / f"spectrum_{w}x{h}.png"
        img.save(str(out))
        assert out.exists() and out.stat().st_size > 10_000
        try:
            win._disconnect()
        except Exception:
            pass
        win.close()
