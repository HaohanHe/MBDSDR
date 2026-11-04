# SPDX-License-Identifier: MIT
"""
tests/test_ui_layout_locks.py
=============================
把用户当场骂过的界面毛病锁成回归锁（不是改设计，是防复发）。

四条硬规则（1000×600 / 1600×900 两档都必须过）:

  1. 顶部频率读数**绝不按字符换行** —— 用户原话是「频率读数变成
     『1 5 6 - 5 2』这样一位一行」。宽度不足时整体缩放，永远单排。
  2. 频谱/瀑布的标注与刻度文字**互不重叠** —— 峰值标注压字事故
     （「156.255MHz -14.5dB」和相邻峰标签糊在一起），频轴端点刻度
     半截被裁。判定用文本矩形几何（recording painter + 真实字体
     度量），不是截图肉眼看。
  3. 三栏比例合理，**ADS-B 空态不吞主区** —— 截图里中栏空地图吃掉
     大半窗口，「无 ADS-B 飞机」一行字配一块死黑。
  4. **任何控件不越出父容器边界** —— 中栏 300px 时地图画布 240 +
     飞机列表 160 + 间距会排到 414px，列表右侧被父容器裁掉。

另附关键面板截图基线（tests/baselines/，宽松比对：尺寸/宽高比/粗网格
色块，不做逐像素死比，避免字体差异误报）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui_layout_locks.py -v
    python scripts/ui_layout_audit.py                    # 规则×尺寸汇总矩阵
    python scripts/ui_layout_audit.py --update-baselines # 重建基线（需人工复核）
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, QRectF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QScrollArea, QTabBar, QWidget,
)

app = QApplication.instance() or QApplication(sys.argv)

SIZES = [(1000, 600), (1600, 900)]
BASELINE_DIR = os.path.join(_ROOT, "tests", "baselines")


def _pump(ms: int = 120) -> None:
    """跑事件循环但不 quit——Qt6 下 app.quit() 会把顶层窗口藏起来，
    隐藏后页签页不再布局（会得到 640×480 默认几何的假数据）。"""
    import time
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


# ---------------------------------------------------------------------------
# 窗口 fixture：每档尺寸一个 MainWindow（模块级，控制构造耗时）
# ---------------------------------------------------------------------------
def _isolate_settings():
    """备份并清空窗口几何/分栏持久化，测试后原样还原。"""
    s = QSettings("MBDSDR", "Desktop")
    keys = ("window_geometry", "three_column_sizes",
            "three_column_layout_version")
    backup = {k: s.value(k) for k in keys}
    for k in keys:
        s.remove(k)
    s.sync()
    gui_cfg = os.path.expanduser("~/.mbdsdr/gui_config.json")
    cfg_backup = None
    if os.path.exists(gui_cfg):
        with open(gui_cfg, "r", encoding="utf-8") as f:
            cfg_backup = f.read()
        try:   # 摘掉 window 键：_load_gui_config 定时器不再改测试窗口几何
            data = json.loads(cfg_backup)
            if "window" in data:
                data.pop("window")
                with open(gui_cfg, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def restore():
        for k, v in backup.items():
            if v is None:
                s.remove(k)
            else:
                s.setValue(k, v)
        s.sync()
        if cfg_backup is not None:
            with open(gui_cfg, "w", encoding="utf-8") as f:
                f.write(cfg_backup)

    return restore


@pytest.fixture(scope="module", params=SIZES,
                ids=[f"{w}x{h}" for w, h in SIZES])
def main_window(request):
    width, height = request.param
    restore = _isolate_settings()
    from main_window import MainWindow
    win = MainWindow()
    for t in getattr(win, "_startup_timers", []):
        try:
            t.stop()
        except Exception:
            pass
    win.resize(width, height)
    win.show()
    _pump(200)
    yield win
    try:
        win._disconnect()
    except Exception:
        pass
    win.close()
    win.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    restore()


def _select_left(win, key: str) -> None:
    tab = win.left_tab
    for i in range(tab.count()):
        if tab.tabBar().tabData(i) == key:
            tab.setCurrentIndex(i)
            break
    _pump(80)


def _select_right(win, key: str) -> None:
    tab = win.right_tab
    for i in range(tab.count()):
        if tab.tabBar().tabData(i) == key:
            tab.setCurrentIndex(i)
            break
    _pump(80)


# ---------------------------------------------------------------------------
# 文本几何采集：用 recording painter 跑真实绘制函数（不截屏肉眼判定）
# ---------------------------------------------------------------------------
class _RecordingPainter:
    """只记录 drawText 的画笔替身：setPen/setFont/fontMetrics 足够。"""

    def __init__(self):
        self._font = QFont()
        self._fm = QFontMetricsF(self._font)
        self.texts = []            # [(x, y_baseline, text, QFontMetricsF)]

    def setPen(self, *_a, **_k):
        pass

    def setFont(self, font):
        self._font = font
        self._fm = QFontMetricsF(font)

    def fontMetrics(self):
        return self._fm

    def drawText(self, *args):
        if len(args) == 2:
            pt, text = args
            self.texts.append((float(pt.x()), float(pt.y()), str(text), self._fm))


def _text_rects(rec):
    """文本矩形 [(x0, x1, y0, y1, text)]（基线 → 上下界）。"""
    out = []
    for x, y, text, fm in rec.texts:
        w = fm.horizontalAdvance(text)
        out.append((x, x + w, y - fm.ascent(), y + fm.descent(), text))
    return out


def _overlaps(a, b):
    return a[0] < b[1] and a[1] > b[0] and a[2] < b[3] and a[3] > b[2]


def _assert_no_overlap(rects, where):
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert not _overlaps(a, b), (
                f"{where}: 标注压字 {a[4]!r} / {b[4]!r} "
                f"rect={tuple(round(v, 1) for v in a[:4])} vs "
                f"{tuple(round(v, 1) for v in b[:4])}")


def _peak_fm():
    f = QFont()
    f.setPointSize(8)          # 与 _draw_peaks 完全一致
    return QFontMetricsF(f)


# ---------------------------------------------------------------------------
# 规则 1：顶部频率读数不按字符换行
# ---------------------------------------------------------------------------
class TestFrequencyReadoutDoesNotWrap:
    def test_readout_never_wraps_digit_by_digit(self, main_window):
        """用户原话：「1 5 6 - 5 2」逐位换行——频率读数必须整行单排。"""
        _select_right(main_window, "control")
        fd = main_window.control_panel.freq_display
        fd.set_frequency(156_525_000)
        _pump(50)
        cells = fd._cells()
        assert cells, "频率读数没有可布局字符格"
        rows = {round(c[1].top(), 1) for c in cells}
        assert len(rows) == 1, f"频率读数出现 {len(rows)} 行（逐位换行）"
        assert min(c[1].left() for c in cells) >= -0.5, "频率读数左侧越界"
        assert max(c[1].right() for c in cells) <= fd.width() + 0.5, \
            "频率读数右侧越界（被裁尾）"
        # 字格不互相压：x 区间按顺序严格递进
        ordered = sorted((c[1].left(), c[1].right()) for c in cells)
        for (l1, r1), (l2, _r2) in zip(ordered, ordered[1:]):
            assert l2 >= r1 - 0.5, "频率读数相邻字符格交叠"

    def test_narrow_dock_shrinks_instead_of_wrapping(self):
        """窄栏（260/300px 右栏）同样不许换行——整体缩放而不是裁/折。"""
        from freq_display import FreqDisplay
        for width in (300, 260, 200):
            fd = FreqDisplay()
            fd.set_frequency(156_525_000)
            fd.resize(width, 60)
            cells = fd._cells()
            assert len({round(c[1].top(), 1) for c in cells}) == 1, \
                f"width={width} 频率读数换行了"
            assert min(c[1].left() for c in cells) >= -0.5
            assert max(c[1].right() for c in cells) <= width + 0.5
            fd.deleteLater()

    def test_status_labels_never_word_wrap(self, main_window):
        """顶栏/频谱头/底坞的频率 QLabel 不许开 wordWrap（换行即裁版）。"""
        for label in (main_window.status_freq, main_window.freq_label,
                      main_window.dock_freq_label):
            assert not label.wordWrap(), f"{label.objectName()} 开启了自动换行"


# ---------------------------------------------------------------------------
# 规则 2：频谱标注 / 刻度文字互不重叠
# ---------------------------------------------------------------------------
class TestSpectrumLabelsDoNotCollide:
    def test_axis_ticks_never_overlap_or_leave_plot(self, main_window):
        """频率轴两端刻度原按中心对齐，首尾各半截越出谱面（像缺位）。"""
        import spectrum_widget as sw
        _select_left(main_window, "spectrum")
        plot = main_window.spectrum._fft_plot
        rect = QRectF(0, 0, plot.width(),
                      max(1, plot.height() - sw._SPEC_BOTTOM_PAD))
        assert rect.width() > 100 and rect.height() > 40

        rec = _RecordingPainter()
        sw._draw_freq_scale(rec, rect, 100e6, 2.048e6, QColor("white"))
        freq_rects = _text_rects(rec)
        # 刻度数自适应：窄谱面可降到 2~4 个，宽谱面保持 5 个
        assert 2 <= len(freq_rects) <= 5, \
            f"频轴刻度数量异常：{len(freq_rects)}"
        _assert_no_overlap(freq_rects, "频轴刻度")
        for r in freq_rects:
            assert r[0] >= rect.left() - 0.5, f"刻度 {r[4]} 左越界 {r[0]:.1f}"
            assert r[1] <= rect.right() + 0.5, f"刻度 {r[4]} 右越界 {r[1]:.1f}"

        rec2 = _RecordingPainter()
        sw._draw_db_scale(rec2, rect, main_window.spectrum._state,
                          QColor("white"))
        db_rects = _text_rects(rec2)
        _assert_no_overlap(db_rects, "dB 刻度")
        for r in db_rects:
            assert r[0] >= rect.left() - 0.5
            assert r[1] <= rect.right() + 0.5

    def test_peak_labels_do_not_stack(self, main_window):
        """截图压字事故：相邻峰标签糊成一团——必须分行错开或诚实丢弃。"""
        import spectrum_widget as sw
        _select_left(main_window, "spectrum")
        width = main_window.spectrum._fft_plot.width()
        fm = _peak_fm()
        labels = [(20.0, 100.0, "156.255MHz -14.5dB"),
                  (26.0, 104.0, "156.294MHz -13.2dB"),
                  (32.0, 101.0, "156.410MHz -11.2dB")]
        placed = sw._peak_label_placements(
            labels, fm.horizontalAdvance, 2.0, width - 2.0, fm.height() + 2.0)
        assert len(placed) >= 2, "三个紧邻峰至少要有两个能放置（其余可丢弃）"
        rects = []
        for tx, ty, text, row in placed:
            rects.append((tx, tx + fm.horizontalAdvance(text),
                          ty - fm.ascent(), ty + fm.descent(), text))
        _assert_no_overlap(rects, "峰值标注")
        for r in rects:
            assert r[0] >= -0.5 and r[1] <= width + 0.5, f"峰值标注越界 {r[4]}"

    def test_bandplan_labels_do_not_stack(self, main_window):
        """频段彩带标签同样不许叠字；低优先级在窄视口先消失。"""
        import spectrum_widget as sw
        rect = QRectF(0, 0, main_window.spectrum._fft_plot.width(), 300)
        fm = _peak_fm()
        # 用真实 bandplan（120MHz 附近：航空导航 / 航空语音 两个相邻带）
        items = []
        for b in sw._load_bandplan():
            rng = sw._band_x_range(rect, 120e6, 2.048e6,
                                   float(b["start_hz"]), float(b["stop_hz"]))
            if rng is None:
                continue
            items.append((rng[0], rng[1], str(b["label"]),
                          str(b.get("priority", "low")), False))
        tx_map = sw._bandplan_label_placements(
            items, fm.horizontalAdvance, rect.x(), rect.width())
        rects = []
        for idx, tx in tx_map.items():
            text = items[idx][2]
            rects.append((tx, tx + fm.horizontalAdvance(text),
                          rect.y(), rect.y() + 12, text))
        _assert_no_overlap(rects, "频段标签")
        for r in rects:
            assert r[0] >= rect.x() - 0.5
            assert r[1] <= rect.right() + 0.5


# ---------------------------------------------------------------------------
# 规则 3：三栏比例 / 空态不吞主区
# ---------------------------------------------------------------------------
class TestColumnsAndEmptyPanels:
    def test_three_columns_ratio_sane(self, main_window):
        """左栏工作面拿弹性；中/右固定基线，空态地图不许吃成主区。"""
        sizes = list(main_window._main_splitter.sizes())
        assert len(sizes) == 3
        total = sum(sizes)
        assert total > 0
        left, center, right = sizes
        assert left >= center, f"左栏被中栏压过: {sizes}"
        assert 0.33 <= left / total <= 0.75, f"左栏份额异常: {sizes}"
        assert 0.12 <= center / total <= 0.35, f"空态中栏吞主区: {sizes}"
        assert 0.12 <= right / total <= 0.35, f"右栏份额异常: {sizes}"

    def test_empty_adsb_panel_does_not_take_main_area(self, main_window):
        """「无 ADS-B 飞机」空态：地图面板宽度不得超过中栏本身。"""
        panel = main_window.adsb_map_panel
        assert panel.aircraft_count() == 0, "本用例要求 ADS-B 空态"
        center = main_window._main_splitter.widget(1)
        assert panel.width() <= center.width() + 1
        # 空态也不许把画布撑得比列还宽（画布是空态视觉主体）
        assert panel.canvas.width() <= panel.width()
        # 地图画布与列表必须落在面板内（此前后者被挤出 13px）
        assert panel.canvas.geometry().right() <= panel.width() + 1
        assert panel.table.geometry().right() <= panel.width() + 1


# ---------------------------------------------------------------------------
# 规则 4：控件不越出父容器
# ---------------------------------------------------------------------------
def _visible_overflow(root, skip=("RFSkyView",)):
    """可见控件越出父容器的清单（几何判定）。

    跳过：天空视图子树（另一条线在改月相/FOV/标签避让，形状仍在变）；
    滚动区内容子树（右栏面板宽度契约是另一条线的页签/滚动议题，
    现状已如实记录为欠账）；QTabBar 内部滑动按钮（Qt 有意摆到界外）。
    """
    bad = []

    def walk(w, path, in_scroll=False):
        for c in w.children():
            if not isinstance(c, QWidget) or c.isWindow():
                continue
            p = path + "/" + (c.objectName() or c.__class__.__name__)
            skip_here = in_scroll or isinstance(c, QScrollArea) \
                or isinstance(c, QTabBar) or any(s in p for s in skip)
            if c.isVisible() and c.isVisibleTo(root) and not skip_here:
                if c.parentWidget() is not None:
                    g = c.geometry()
                    pw = c.parentWidget().width()
                    ph = c.parentWidget().height()
                    if (g.left() < -1 or g.top() < -1
                            or g.right() > pw + 1 or g.bottom() > ph + 1):
                        bad.append((p, g.x(), g.y(), g.width(), g.height(),
                                    pw, ph))
            walk(c, p, skip_here)

    walk(root, "")
    return bad


class TestWidgetsStayInsideParents:
    def test_no_widget_escapes_its_parent(self, main_window):
        """任何可见控件不得越出父容器（否则右侧/底部被静默裁掉）。"""
        bad = _visible_overflow(main_window)
        assert not bad, "控件越出父容器:\n" + "\n".join(map(str, bad[:20]))


# ---------------------------------------------------------------------------
# 截图基线：固定面板 + 宽松比对（尺寸/宽高比/32×24 粗网格色块）
# ---------------------------------------------------------------------------
_SIG_COLS, _SIG_ROWS = 32, 24
_UPDATE = os.environ.get("MBDSDR_UI_BASELINE_UPDATE") == "1"


def _normalize(img: QImage, max_w: int = 320) -> QImage:
    if img.width() > max_w:
        return img.scaledToWidth(max_w, Qt.SmoothTransformation)
    return img


def _signature(img: QImage, cols: int = _SIG_COLS, rows: int = _SIG_ROWS):
    small = img.scaled(cols, rows, Qt.IgnoreAspectRatio,
                       Qt.SmoothTransformation)
    cells = []
    for y in range(small.height()):
        for x in range(small.width()):
            c = QColor(small.pixel(x, y))
            cells.append((c.red(), c.green(), c.blue()))
    return cells


def _diff_stats(cur, old):
    diffs = [max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))
             for a, b in zip(cur, old)]
    return max(diffs), sum(diffs) / len(diffs)


def _panel_image(win, name: str) -> QImage:
    if name == "spectrum":
        _select_left(win, "spectrum")
        return win.spectrum.grab().toImage()
    if name == "adsb_empty":
        return win.adsb_map_panel.grab().toImage()
    if name == "freq_readout":
        _select_right(win, "control")
        fd = win.control_panel.freq_display
        fd.set_frequency(156_525_000)   # 固定值：不依赖前序用例/硬件
        _pump(40)
        return fd.grab().toImage()
    raise AssertionError(f"未知面板 {name}")


@pytest.mark.parametrize("panel", ["spectrum", "adsb_empty", "freq_readout"])
def test_panel_screenshot_matches_baseline(main_window, panel):
    """关键面板截图基线：锁尺寸/宽高比/主要色块，不做逐像素死比。"""
    img = _normalize(_panel_image(main_window, panel))
    assert not img.isNull(), f"{panel} 截图为空"
    size = f"{main_window.width()}x{main_window.height()}"
    path = os.path.join(BASELINE_DIR, f"{panel}_{size}.png")

    if _UPDATE:
        os.makedirs(BASELINE_DIR, exist_ok=True)
        assert img.save(path, "PNG"), f"基线写入失败: {path}"
        pytest.skip(f"已更新基线 {os.path.basename(path)}")

    assert os.path.exists(path), (
        f"基线缺失 {path}；先运行 python scripts/ui_layout_audit.py "
        f"--update-baselines 并人工复核")
    base = QImage(path)
    assert not base.isNull(), f"基线无法读取: {path}"
    assert (img.width(), img.height()) == (base.width(), base.height()), (
        f"{panel} 尺寸漂移: 当前 {img.width()}x{img.height()} "
        f"基线 {base.width()}x{base.height()}")
    # 宽高比（尺寸已等，等价于对缩放宽度的稳定性）
    assert abs(img.width() / img.height() - base.width() / base.height()) < 0.01

    # 两档尺度：粗网格看「主要色块区域」，细网格容忍字形/抗锯齿差异
    worst, mean = _diff_stats(_signature(img, 8, 6), _signature(base, 8, 6))
    assert worst <= 45 and mean <= 10.0, (
        f"{panel} 主要色块区域漂移（worst={worst}, mean={mean:.1f}）: "
        f"{path}；确属有意改版再 --update-baselines")
    worst, mean = _diff_stats(_signature(img), _signature(base))
    assert worst <= 100 and mean <= 20.0, (
        f"{panel} 细节色块漂移（worst={worst}, mean={mean:.1f}）: {path}")
