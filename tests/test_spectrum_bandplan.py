# SPDX-License-Identifier: MIT
"""
tests/test_spectrum_bandplan.py
================================
验证 SDR++ 频段彩带仿妆（desktop/bandplan.json 皮进 + 谱面绘制/交互）:

  1. 加载器: 条目数、必需字段合法 (label 非空 / 0<start<stop / category 在
     分类色表内); v2 可选字段 desc/modulation/priority/color 解析与缺省;
     缺文件/坏 JSON/坏条目诚实跳过; Region 3 边界钉扎 (40m=7.0-7.2,
     2m=144-146, 41m 短波 7.2-7.45);
  2. 几何: _band_x_range 视口内夹紧、视口外 None; 视图平移偏移
     (view_offset_hz) 进入 _view_center_hz 联动;
  3. 标签分级: _bandplan_label_placements 纯函数——窄带不画、碰撞剔除、
     高优先级先存、悬停/选中 forced 强制、视口边缘夹紧;
  4. 悬停/点击: mouseMove 命中设 bandplan_hover + QToolTip 气泡、最窄带
     优先 (子带盖母带)、leaveEvent 清态; 单击选中常驻描边、点空白清除、
     调谐语义不变;
  5. 面板集成/视觉: SpectrumPanel offscreen 三档缩放渲染, 色条像素落在
     预期分类色上 (z-order 回归), PNG 存证 .tmp-tool-results/bandplan_shots/;
  6. 瀑布顶缘色条: schema "谱面+瀑布色带" 的瀑布半边——顶缘 4px 条带内
     分类色/带外干净/开关语义/瀑布画布关闭时照画 (频率锚)。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_spectrum_bandplan.py -v
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolTip  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from spectrum_widget import (  # noqa: E402
    BANDPLAN_CATEGORY_COLORS,
    SpectrumPanel,
    _band_color,
    _band_x_range,
    _bandplan_label_placements,
    _load_bandplan,
    _parse_band_entry,
    _view_center_hz,
)

_SHOT_DIR = os.path.join(_ROOT, ".tmp-tool-results", "bandplan_shots")


# ---------------------------------------------------------------------------
# 加载器
# ---------------------------------------------------------------------------
class TestLoader:
    def test_entries_sane(self):
        bp = _load_bandplan()
        assert len(bp) >= 15
        for b in bp:
            assert b["label"]
            assert 0.0 < b["start_hz"] < b["stop_hz"]
            assert b["category"] in BANDPLAN_CATEGORY_COLORS

    def test_expected_bands_present(self):
        bp = _load_bandplan()
        labels = " ".join(b["label"] for b in bp)
        for want in ("FM 广播", "航空语音", "低轨卫星下行", "40m 业余"):
            assert want in labels

    def test_deterministic_cache(self):
        assert _load_bandplan() is _load_bandplan()

    def test_region3_boundaries(self):
        """Region 3/中国口径边界钉扎（与 ITU R3/IARU R3 对拍）。"""
        bp = {b["label"]: b for b in _load_bandplan()}
        assert (bp["40m 业余"]["start_hz"], bp["40m 业余"]["stop_hz"]) \
            == (7000000.0, 7200000.0)
        assert (bp["2m 业余"]["start_hz"], bp["2m 业余"]["stop_hz"]) \
            == (144000000.0, 146000000.0)
        assert (bp["短波广播 41m"]["start_hz"], bp["短波广播 41m"]["stop_hz"]) \
            == (7200000.0, 7450000.0)
        # 航空语音 ICAO Annex 10: 118.000–136.975 MHz
        assert bp["航空语音"]["stop_hz"] == 136975000.0


class TestLoaderV2:
    """v2 可选字段与异常输入。"""

    @staticmethod
    def _write(tmp_path, payload):
        p = tmp_path / "bp.json"
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return str(p)

    def test_optional_fields_parsed(self, tmp_path):
        p = self._write(tmp_path, {"bands": [
            {"label": "测试段", "start_hz": 100, "stop_hz": 200,
             "category": "amateur", "priority": "high",
             "modulation": "SSB", "desc": "样例", "color": "#123456"},
        ]})
        (b,) = _load_bandplan(p)
        assert b["priority"] == "high"
        assert b["modulation"] == "SSB"
        assert b["desc"] == "样例"
        assert b["color"] == "#123456"

    def test_priority_defaults_low_and_invalid_low(self, tmp_path):
        p = self._write(tmp_path, {"bands": [
            {"label": "A", "start_hz": 100, "stop_hz": 200,
             "category": "amateur"},
            {"label": "B", "start_hz": 300, "stop_hz": 400,
             "category": "amateur", "priority": "urgent"},
        ]})
        a, b = _load_bandplan(p)
        assert a["priority"] == "low"
        assert b["priority"] == "low"

    def test_invalid_color_ignored(self, tmp_path):
        p = self._write(tmp_path, {"bands": [
            {"label": "A", "start_hz": 100, "stop_hz": 200,
             "category": "amateur", "color": "不是颜色"},
        ]})
        (b,) = _load_bandplan(p)
        assert "color" not in b

    def test_unknown_category_skipped(self, tmp_path):
        p = self._write(tmp_path, {"bands": [
            {"label": "A", "start_hz": 100, "stop_hz": 200,
             "category": "martian"},
        ]})
        assert _load_bandplan(p) == []

    def test_missing_file_returns_empty(self, tmp_path):
        assert _load_bandplan(str(tmp_path / "nope.json")) == []

    def test_malformed_json_returns_empty(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        assert _load_bandplan(str(p)) == []

    def test_bad_entries_skipped(self, tmp_path):
        p = self._write(tmp_path, {"bands": [
            {"label": "反向", "start_hz": 300, "stop_hz": 200,
             "category": "amateur"},                       # start>=stop
            {"label": "缺止频", "start_hz": 100,
             "category": "amateur"},                       # 缺 stop_hz
            "not-a-dict",                                  # 非对象
            {"label": "好", "start_hz": 100, "stop_hz": 200,
             "category": "aviation"},
        ]})
        out = _load_bandplan(p)
        assert len(out) == 1 and out[0]["label"] == "好"

    def test_bands_not_list_returns_empty(self, tmp_path):
        p = self._write(tmp_path, {"bands": "oops"})
        assert _load_bandplan(p) == []

    def test_explicit_path_not_cached(self, tmp_path):
        p = self._write(tmp_path, {"bands": [
            {"label": "A", "start_hz": 100, "stop_hz": 200,
             "category": "amateur"},
        ]})
        assert _load_bandplan(p) is not _load_bandplan(p)


# ---------------------------------------------------------------------------
# 几何 / 缩放平移联动
# ---------------------------------------------------------------------------
class TestGeometry:
    RECT = QRectF(0.0, 0.0, 1000.0, 100.0)

    def test_in_view(self):
        # 中心 100 MHz, 跨度 2.4 MHz: 99.1-100.9 MHz 全在视口内
        rng = _band_x_range(self.RECT, 100e6, 2.4e6, 99.1e6, 100.9e6)
        assert rng is not None
        x0, x1 = rng
        assert 0.0 < x0 < x1 < 1000.0
        # 中心对齐: 99.1MHz 在左半, 100.9MHz 在右半
        assert x0 < 500.0 < x1

    def test_clamped_at_edge(self):
        rng = _band_x_range(self.RECT, 100e6, 2.4e6, 99.0e6, 101.5e6)
        assert rng is not None
        x0, x1 = rng
        assert x0 >= 0.0 and x1 <= 1000.0

    def test_out_of_view_returns_none(self):
        # 40m 业余 7-7.2 MHz 对 100 MHz±1.2 MHz 视口完全在外
        assert _band_x_range(self.RECT, 100e6, 2.4e6, 7.0e6, 7.2e6) is None
        # 上缘之外同理
        assert _band_x_range(self.RECT, 100e6, 2.4e6, 103.0e6, 108.0e6) is None

    def test_view_offset_shifts_view_center(self):
        """Ctrl 拖动平移 (view_offset_hz) 必须进入彩带映射的频率源。"""
        panel = SpectrumPanel()
        st = panel._state
        panel.generator.center_freq_hz = 100e6
        st.view_offset_hz = 0.0
        assert _view_center_hz(st) == 100e6
        st.view_offset_hz = 1.5e6
        assert _view_center_hz(st) == 101.5e6
        # 同一频段在平移后 x 区间整体左移 (view_center 增大 → x 减小)
        span = panel.generator.sample_rate_hz
        r0 = _band_x_range(self.RECT, 100e6, span, 99.0e6, 101.0e6)
        r1 = _band_x_range(self.RECT, 101.5e6, span, 99.0e6, 101.0e6)
        assert r0 is not None and r1 is not None and r1[0] < r0[0]


# ---------------------------------------------------------------------------
# 标签分级（纯函数）
# ---------------------------------------------------------------------------
class TestLabelPlacement:
    @staticmethod
    def _measure(s):
        return float(len(s)) * 10.0

    def test_narrow_band_no_label(self):
        items = [(0.0, 30.0, "ABCDEF", "low", False)]  # tw=60 > 30-8
        assert _bandplan_label_placements(items, self._measure, 0.0, 1000.0) \
            == {}

    def test_label_centered_in_band(self):
        items = [(0.0, 200.0, "ABCD", "low", False)]   # tw=40
        out = _bandplan_label_placements(items, self._measure, 0.0, 1000.0)
        assert out == {0: 80.0}

    def test_collision_drops_later_low(self):
        # 母带与子带标签相撞：同级按 x0 序，后者被剔除
        items = [(0.0, 400.0, "PARENT", "low", False),   # tw=60 → 170..230
                 (150.0, 250.0, "SUB", "low", False)]    # tw=30 → 185..215
        out = _bandplan_label_placements(items, self._measure, 0.0, 1000.0)
        assert out == {0: 170.0}

    def test_high_priority_survives_collision(self):
        # 子带 high 优先放置，母带 low 被剔除
        items = [(0.0, 400.0, "PARENT", "low", False),
                 (150.0, 250.0, "SUB", "high", False)]
        out = _bandplan_label_placements(items, self._measure, 0.0, 1000.0)
        assert out == {1: 185.0}

    def test_forced_always_labeled(self):
        # 悬停/选中带即使塞不下也强制给标签（夹紧在视口内）
        items = [(0.0, 30.0, "ABCDEFGHIJ", "low", True)]  # tw=100
        out = _bandplan_label_placements(items, self._measure, 0.0, 1000.0)
        assert out == {0: 2.0}

    def test_clamped_to_viewport_right_edge(self):
        items = [(900.0, 1400.0, "ABCDEF", "low", False)]  # tw=60
        out = _bandplan_label_placements(items, self._measure, 0.0, 1000.0)
        assert out == {0: 938.0}

    def test_empty_items(self):
        assert _bandplan_label_placements([], self._measure, 0.0, 1000.0) == {}


# ---------------------------------------------------------------------------
# 悬停 / 点击交互
# ---------------------------------------------------------------------------
def _x_for_freq(widget, center_hz, span_hz, freq_hz):
    f_left = center_hz - span_hz / 2.0
    return (freq_hz - f_left) / span_hz * widget.width()


def _move(widget, x, y=40):
    ev = QMouseEvent(QEvent.MouseMove, QPointF(float(x), float(y)),
                     Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    widget.mouseMoveEvent(ev)


def _click(widget, x, y=40):
    press = QMouseEvent(QEvent.MouseButtonPress, QPointF(float(x), float(y)),
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    release = QMouseEvent(QEvent.MouseButtonRelease,
                          QPointF(float(x), float(y)),
                          Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    widget.mousePressEvent(press)
    widget.mouseReleaseEvent(release)


class TestHoverClick:
    def _panel(self, center_hz, span_hz):
        panel = SpectrumPanel()
        panel.resize(900, 320)
        panel.generator.center_freq_hz = center_hz
        panel.generator.sample_rate_hz = span_hz
        panel.show()
        app.processEvents()
        return panel

    def test_hover_sets_state_and_tip(self, monkeypatch):
        panel = self._panel(137.4875e6, 3.0e6)
        cw = panel._fft_plot
        tips = []
        monkeypatch.setattr(QToolTip, "showText",
                            staticmethod(lambda *a, **k: tips.append(a)))
        hides = []
        monkeypatch.setattr(QToolTip, "hideText",
                            staticmethod(lambda *a, **k: hides.append(1)))
        x = _x_for_freq(cw, 137.4875e6, 3.0e6, 137.5e6)
        _move(cw, x)
        idx = panel._state.bandplan_hover
        assert idx is not None
        assert panel._state.bandplan[idx]["label"] == "低轨卫星下行"
        assert tips and "低轨卫星下行" in tips[-1][1]
        assert "137.0000" in tips[-1][1] and "138.0000" in tips[-1][1]
        # 悬停同一目标不重复弹气泡
        n = len(tips)
        _move(cw, x)
        assert len(tips) == n
        # 移到无频段处清态并收起气泡
        _move(cw, _x_for_freq(cw, 137.4875e6, 3.0e6, 138.5e6))
        assert panel._state.bandplan_hover is None
        assert hides

    def test_hover_narrowest_wins(self, monkeypatch):
        monkeypatch.setattr(QToolTip, "showText", staticmethod(lambda *a, **k: None))
        monkeypatch.setattr(QToolTip, "hideText", staticmethod(lambda *a, **k: None))
        panel = self._panel(145.9e6, 2.0e6)
        cw = panel._fft_plot
        _move(cw, _x_for_freq(cw, 145.9e6, 2.0e6, 145.9e6))
        idx = panel._state.bandplan_hover
        assert idx is not None
        # 145.9 同时在 2m 业余(2 MHz) 与 业余卫星上行(0.2 MHz) 内，窄者优先
        assert panel._state.bandplan[idx]["label"] == "业余卫星上行"

    def test_leave_clears_hover(self, monkeypatch):
        monkeypatch.setattr(QToolTip, "showText", staticmethod(lambda *a, **k: None))
        hides = []
        monkeypatch.setattr(QToolTip, "hideText",
                            staticmethod(lambda *a, **k: hides.append(1)))
        panel = self._panel(137.4875e6, 3.0e6)
        cw = panel._fft_plot
        _move(cw, _x_for_freq(cw, 137.4875e6, 3.0e6, 137.5e6))
        assert panel._state.bandplan_hover is not None
        cw.leaveEvent(QEvent(QEvent.Leave))
        assert panel._state.bandplan_hover is None
        assert hides

    def test_click_selects_and_clears(self, monkeypatch):
        monkeypatch.setattr(QToolTip, "showText", staticmethod(lambda *a, **k: None))
        monkeypatch.setattr(QToolTip, "hideText", staticmethod(lambda *a, **k: None))
        panel = self._panel(137.4875e6, 3.0e6)
        cw = panel._fft_plot
        gen = panel.generator
        # 单击 137.5 MHz：选中低轨卫星下行 + 调谐跟随（center 模式语义不变）
        _click(cw, _x_for_freq(cw, 137.4875e6, 3.0e6, 137.5e6))
        idx = panel._state.bandplan_selected
        assert idx is not None
        assert panel._state.bandplan[idx]["label"] == "低轨卫星下行"
        assert abs(gen.center_freq_hz - 137.5e6) <= 10e3  # snap 10 kHz
        # 单击 138.5 MHz（带外空白）：清除选中
        _click(cw, _x_for_freq(cw, gen.center_freq_hz, 3.0e6, 138.5e6))
        assert panel._state.bandplan_selected is None

    def test_band_at_respects_switch(self):
        panel = self._panel(137.4875e6, 3.0e6)
        cw = panel._fft_plot
        x = _x_for_freq(cw, 137.4875e6, 3.0e6, 137.5e6)
        assert cw._band_at(x) is not None
        panel._state.show_bandplan = False
        assert cw._band_at(x) is None


# ---------------------------------------------------------------------------
# 面板集成 / 视觉（三档缩放像素验收 + PNG 存证）
# ---------------------------------------------------------------------------
class TestOverlayOrder:
    def test_strip_above_spectrum_fill(self):
        """彩带色条/标签必须在谱线渐变填充之上（防被盖住的回归）。"""
        import numpy as np
        panel = SpectrumPanel()
        panel.resize(800, 300)
        panel.generator.center_freq_hz = 137.5e6
        panel.generator.sample_rate_hz = 4.0e6
        rng = np.random.default_rng(0)
        iq = (rng.standard_normal(8192)
              + 1j * rng.standard_normal(8192)).astype(np.complex64)
        panel.generator.push_iq(iq, center_freq_hz=137.5e6,
                                sample_rate_hz=4.0e6)
        panel.show()
        app.processEvents()
        panel.repaint()
        img = panel.grab().toImage()
        # 全图找航空绿带色条像素（g 显著大于 r/b, 且非青色 accent）
        green = 0
        for y in range(0, img.height(), 2):
            for x in range(0, img.width() // 2, 2):
                c = img.pixelColor(x, y)
                if (c.green() > c.red() + 25 and c.green() > c.blue() + 25
                        and c.green() < 140 and c.alpha() > 100):
                    green += 1
        assert green > 20, "航空绿带色条被谱线填充盖住（z-order 回归）"


class TestPanelIntegration:
    def test_panel_has_bandplan_and_renders(self):
        panel = SpectrumPanel()
        panel.resize(800, 300)
        assert panel._state.bandplan, "面板应自带 bandplan 数据"
        assert panel._state.show_bandplan is True
        # 视图对准 FM 广播段, 让彩带真正进绘制路径
        panel.generator.center_freq_hz = 98.0e6
        panel.show()
        app.processEvents()
        panel.repaint()
        # 关闭开关后路径也不崩
        panel._bandplan_chk.setChecked(False)
        assert panel._state.show_bandplan is False
        panel.repaint()

    def test_empty_bandplan_renders_clean(self):
        """空频段表（缺文件/全坏条目的诚实空态）绘制不崩、命中为 None。"""
        panel = SpectrumPanel()
        panel.resize(800, 300)
        panel._state.bandplan = []
        panel.show()
        app.processEvents()
        panel.repaint()
        assert panel._fft_plot._band_at(100) is None


class TestVisualZoomLevels:
    """三档带宽/缩放的无头视觉验收：色条位置与分类色正确。

    只断言色条像素（实现行为）；不断言标签字形——offscreen 环境缺 CJK
    字体时中文渲染为方框属环境伪影，标签放置逻辑由 TestLabelPlacement
    纯函数覆盖。PNG 存证 .tmp-tool-results/bandplan_shots/。
    """

    @staticmethod
    def _render(center_hz, span_hz, name, w=960, h=320):
        # 不推 IQ：色条/标签绘制与有无数据无关（overlay 遍无条件执行），
        # 无数据时谱面只有网格+背景，色条像素不被谱线渐变填充污染；
        # 有数据的 z-order 回归由 TestOverlayOrder 覆盖。
        panel = SpectrumPanel()
        panel.resize(w, h)
        panel.generator.center_freq_hz = center_hz
        panel.generator.sample_rate_hz = span_hz
        panel.show()
        app.processEvents()
        panel.repaint()
        cw = panel._fft_plot
        img = cw.grab().toImage()
        os.makedirs(_SHOT_DIR, exist_ok=True)
        img.save(os.path.join(_SHOT_DIR, name))
        return panel, cw, img

    @staticmethod
    def _strip_samples(img, cw, center_hz, span_hz, f0, f1, inset=0.15):
        """在色条行上取频段 [f0,f1] 内缩 inset 的像素样本。"""
        w = cw.width()
        y = cw.height() - 18 - 2  # spec_rect 底部 4px 色条的中部
        f_left = center_hz - span_hz / 2.0
        x0 = int((f0 - f_left) / span_hz * w)
        x1 = int((f1 - f_left) / span_hz * w)
        span_px = max(1, x1 - x0)
        xa = x0 + int(span_px * inset)
        xb = x1 - int(span_px * inset)
        return [img.pixelColor(x, y) for x in range(max(0, xa),
                                                    min(img.width(), xb), 2)]

    @staticmethod
    def _assert_hue(samples, channel, margin=25):
        """多数样本的主通道符合预期（r/g/b 哪个显著大）。"""
        assert samples, "采样为空（频段应落在视口内）"
        hits = 0
        for c in samples:
            r, g, b = c.red(), c.green(), c.blue()
            if channel == "r" and r > g + margin and r > b + margin:
                hits += 1
            elif channel == "g" and g > r + margin and g > b + margin:
                hits += 1
            elif channel == "b" and b > r + margin and b > g + margin:
                hits += 1
        assert hits > len(samples) * 0.5, \
            f"色条主通道 {channel} 命中 {hits}/{len(samples)}"

    def test_zoom_40m(self):
        """档 1: 40m——业余红 7.0-7.2 与短波青 7.2-7.45 相邻不混色。"""
        center, span = 7.225e6, 1.25e6
        _p, cw, img = self._render(center, span, "zoom_40m.png")
        red = self._strip_samples(img, cw, center, span, 7.05e6, 7.15e6)
        teal = self._strip_samples(img, cw, center, span, 7.25e6, 7.40e6)
        self._assert_hue(red, "r")
        assert sum(1 for c in teal
                   if c.blue() > c.red() + 10 and c.green() > c.red() + 10) \
            > len(teal) * 0.5, "41m 短波青带未落在 7.2 右侧"

    def test_zoom_fm_broadcast(self):
        """档 2: FM 广播 87.5-108 整段蓝色横贯视口。"""
        center, span = 97.75e6, 20.5e6
        _p, cw, img = self._render(center, span, "zoom_fm.png")
        mid = self._strip_samples(img, cw, center, span, 95.0e6, 100.0e6)
        edge = self._strip_samples(img, cw, center, span, 88.0e6, 90.0e6)
        self._assert_hue(mid, "b")
        self._assert_hue(edge, "b")

    def test_zoom_leo_downlink(self):
        """档 3: 低轨卫星下行 137-138 灰带，左侧邻接航空绿，右缘外无彩带。"""
        center, span = 137.4875e6, 3.0e6
        _p, cw, img = self._render(center, span, "zoom_leo.png")
        gray = self._strip_samples(img, cw, center, span, 137.1e6, 137.9e6)
        green = self._strip_samples(img, cw, center, span,
                                    136.3e6, 136.9e6, inset=0.0)
        self._assert_hue(green, "g")
        # 灰带: r/g/b 接近且中低亮度（alpha 混合后不饱和）
        assert gray, "灰带采样为空"
        neutral = sum(1 for c in gray
                      if abs(c.red() - c.green()) < 25
                      and abs(c.green() - c.blue()) < 25
                      and 40 < c.green() < 200)
        assert neutral > len(gray) * 0.5, "低轨卫星下行灰带未成中性色"
        # 右缘带外（138.5-138.9 MHz）不应出现彩带色条像素
        out = self._strip_samples(img, cw, center, span, 138.5e6, 138.9e6)
        colored = sum(1 for c in out
                      if (c.green() > c.red() + 25 and c.green() > c.blue() + 25)
                      or (c.red() > c.green() + 25 and c.red() > c.blue() + 25))
        assert colored <= len(out) * 0.1, "带外不应残留彩带色条"


# ---------------------------------------------------------------------------
# 瀑布顶缘频段色条（bandplan.json schema "谱面+瀑布色带" 的瀑布半边）
# ---------------------------------------------------------------------------
class TestWaterfallStrip:
    """瀑布顶缘 4px 彩带条与谱面 overlay 同几何/同色/同开关语义。

    视口取低轨下行档（135.99-138.99 MHz）：带内有航空绿 136.3-136.9，
    带外 138.5-138.9 应干净；不推 IQ——顶缘条是视口装饰不依赖数据。
    """

    CENTER, SPAN = 137.4875e6, 3.0e6

    def _render(self, name):
        panel = SpectrumPanel()
        panel.resize(960, 320)
        panel.generator.center_freq_hz = self.CENTER
        panel.generator.sample_rate_hz = self.SPAN
        panel.show()
        app.processEvents()
        panel.repaint()
        wf = panel._wf_plot
        img = wf.grab().toImage()
        os.makedirs(_SHOT_DIR, exist_ok=True)
        img.save(os.path.join(_SHOT_DIR, name))
        return panel, wf, img

    def _samples(self, img, wf, f0, f1):
        w = wf.width()
        f_left = self.CENTER - self.SPAN / 2.0
        x0 = int((f0 - f_left) / self.SPAN * w)
        x1 = int((f1 - f_left) / self.SPAN * w)
        return [img.pixelColor(x, 1)
                for x in range(max(0, x0 + 2), min(w, x1 - 2), 2)]

    @staticmethod
    def _colored(c):
        return ((c.green() > c.red() + 25 and c.green() > c.blue() + 25)
                or (c.red() > c.green() + 25 and c.red() > c.blue() + 25)
                or (c.blue() > c.red() + 25 and c.blue() > c.green() + 25))

    def test_top_strip_band_hue_and_clean_outside(self):
        _panel, wf, img = self._render("wf_strip_leo.png")
        green = self._samples(img, wf, 136.3e6, 136.9e6)
        assert green, "航空绿带内采样为空（应落在视口内）"
        hits = sum(1 for c in green
                   if c.green() > c.red() + 25 and c.green() > c.blue() + 25)
        assert hits > len(green) * 0.5, "瀑布顶缘缺航空绿色条"
        out = self._samples(img, wf, 138.5e6, 138.9e6)
        colored = sum(1 for c in out if self._colored(c))
        assert colored <= max(1, len(out) * 0.1), "瀑布顶缘带外渗出彩带"

    def test_toggle_off_clears_strip(self):
        panel, wf, _img = self._render("wf_strip_off.png")
        panel._state.show_bandplan = False
        panel.repaint()
        app.processEvents()
        img = wf.grab().toImage()
        green = self._samples(img, wf, 136.3e6, 136.9e6)
        colored = sum(1 for c in green if self._colored(c))
        assert colored <= max(1, len(green) * 0.1), "关开关后瀑布顶缘仍有色条"

    def test_strip_survives_waterfall_off(self):
        """瀑布画布关掉时顶缘条照画——它是频率锚不是数据。"""
        panel, wf, _img = self._render("wf_strip_nocanvas.png")
        panel._state.show_waterfall = False
        panel.repaint()
        app.processEvents()
        img = wf.grab().toImage()
        green = self._samples(img, wf, 136.3e6, 136.9e6)
        hits = sum(1 for c in green
                   if c.green() > c.red() + 25 and c.green() > c.blue() + 25)
        assert hits > len(green) * 0.5, "瀑布关闭时顶缘频段锚丢失"
