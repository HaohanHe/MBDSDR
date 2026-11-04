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
    python scripts/ui_layout_audit.py --update-baselines --reason "..."  # 受控重建
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, QRectF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QScrollArea, QTabBar, QWidget,
)

app = QApplication.instance() or QApplication(sys.argv)

SIZES = [(800, 600), (1000, 600), (1280, 720), (1600, 900), (1920, 1080)]
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
def _isolate_environment(tmp_dir: str):
    """把用户目录整体重定向到临时目录：基线只反映代码，不反映跑它的人。

    为什么需要（实测）：
      * ``~/.mbdsdr/bookmarks.json`` 的 140 条书签会被画成谱面顶部的药丸，
        同一份代码在开发者机器与干净账号下拍出不同基线（worst 差 100+）；
      * ``%APPDATA%/mbdsdr/desktop_settings.json`` 会带回上一次的频率/增益，
        直接改变频谱中心；
      * ``%APPDATA%/MBDSDR/layouts.json``（layout_store）与 QSettings 里的
        窗口几何/三栏比例会把上一次的窗口状态带进来。

    隔离手段：
      * ``os.path.expanduser`` 的 ``~`` → 临时 HOME（覆盖 bookmarks/config/
        gui_config/recordings 等所有 ``~/.mbdsdr`` 读取）；
      * ``APPDATA`` / ``XDG_CONFIG_HOME`` / ``HOME`` 环境变量 → 临时目录
        （覆盖 DesktopSettings 与 layout_store 的平台路径）；
      * layout_store 单例 → 临时 layouts.json；
      * QSettings 的 3 个几何键备份后清空，测试结束原样还原。
    退出时全部还原，不碰用户真实文件。
    """
    import shutil
    from unittest.mock import patch

    import layout_store as _ls

    home = os.path.join(tmp_dir, "home")
    appdata = os.path.join(tmp_dir, "appdata")
    os.makedirs(home, exist_ok=True)
    os.makedirs(appdata, exist_ok=True)

    real_expanduser = os.path.expanduser

    def _temp_expanduser(path):
        if path == "~":
            return home
        if path.startswith("~/"):
            return os.path.join(home, *path[2:].replace("\\", "/").split("/"))
        return real_expanduser(path)

    patcher = patch("os.path.expanduser", _temp_expanduser)
    patcher.start()

    env_keys = ("APPDATA", "XDG_CONFIG_HOME", "HOME")
    env_backup = {k: os.environ.get(k) for k in env_keys}
    os.environ["APPDATA"] = appdata
    os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp_dir, "config")
    os.environ["HOME"] = home

    store_backup = _ls._default_store
    _ls._default_store = _ls.LayoutStore(os.path.join(tmp_dir, "layouts.json"))

    s = QSettings("MBDSDR", "Desktop")
    keys = ("window_geometry", "three_column_sizes",
            "three_column_layout_version")
    qs_backup = {k: s.value(k) for k in keys}
    for k in keys:
        s.remove(k)
    s.sync()

    def restore():
        for k, v in qs_backup.items():
            if v is None:
                s.remove(k)
            else:
                s.setValue(k, v)
        s.sync()
        _ls._default_store = store_backup
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        patcher.stop()
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return restore


def _clear_user_bookmarks(win) -> None:
    """基线必须与账号数据无关：清掉主窗口启动时从 ~/.mbdsdr/bookmarks.json
    灌进来的书签。

    实测证据：真实 HOME 的书签会被 `_sync_spectrum_bookmarks` 画成谱面
    顶部的药丸；同一份代码在开发者机器与干净账号下 worst 差 100+。
    隔离目录（_isolate_environment）已经保证读不到真实用户文件，这里再
    清一次是最后一道闸：任何绕过 expanduser 的读取路径也不会把账号数据
    拍进基线（用户数据版本另见 tests/test_bookmark_dirty_data.py）。
    """
    for fn in (
        lambda: win.spectrum.set_bookmarks([]),
        lambda: win.control_panel.load_bookmarks_from_list([]),
        lambda: win._bookmarks.clear(),
    ):
        try:
            fn()
        except Exception:
            pass


@pytest.fixture(scope="module", params=SIZES,
                ids=[f"{w}x{h}" for w, h in SIZES])
def main_window(request):
    width, height = request.param
    import tempfile
    tmp_dir = tempfile.mkdtemp(prefix="mbdsdr_ui_baseline_")
    restore = _isolate_environment(tmp_dir)
    from main_window import MainWindow
    win = MainWindow()
    for t in getattr(win, "_startup_timers", []):
        try:
            t.stop()
        except Exception:
            pass
    _clear_user_bookmarks(win)
    win.setProperty("_audit_requested", f"{width}x{height}")
    win.resize(width, height)
    win.show()
    _pump(200)
    # 强制一次全量重抛光再拍照：QComboBox 的 AdjustToContentsOnFirstShow
    # 按首次 show 时的 QSS 落地顺序算 sizeHint，同一进程里第 1 个窗口与
    # 后续窗口会停在两种稳态（实测 fd 270px / 325px 交替），基线因此
    # 依赖跑测顺序。重抛光把控件统一到 QSS 生效后的最终尺寸。
    app.setStyleSheet(app.styleSheet())
    _pump(150)
    yield win
    try:
        win._disconnect()
    except Exception:
        pass
    win.close()
    win.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    restore()


def test_baseline_environment_is_isolated(main_window):
    """基线只反映代码：必须读不到开发者的书签/主题/窗口状态。"""
    from themes import DEFAULT_THEME
    assert main_window._current_theme == DEFAULT_THEME, (
        f"基线环境带进了开发者的主题 {main_window._current_theme!r}")
    assert not getattr(main_window, "_bookmarks", []), (
        "基线环境带进了开发者书签")
    # 隔离目录生效：~/.mbdsdr/bookmarks.json 指向临时 HOME，不存在
    assert not os.path.exists(
        os.path.expanduser("~/.mbdsdr/bookmarks.json")), (
        "~/.mbdsdr 未重定向到临时目录，基线会依赖跑它的人")


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
        self.pills = []            # 书签药丸矩形（QRectF）

    def setPen(self, *_a, **_k):
        pass

    def setBrush(self, *_a, **_k):
        pass

    def drawLine(self, *_a, **_k):
        pass

    def drawRoundedRect(self, rect, *_a, **_k):
        self.pills.append(QRectF(rect))

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
        # 轴右端只画一次的自动单位（kHz/MHz/GHz）不计入刻度数，
        # 但同样纳入压字/越界检查（只增强，不放宽）。
        units = {"kHz", "MHz", "GHz"}
        tick_rects = [r for r in freq_rects if r[4] not in units]
        assert any(r[4] in units for r in freq_rects), "频轴缺少自动单位"
        # 刻度数自适应：窄谱面可降到 2~4 个，宽谱面保持 5 个
        assert 2 <= len(tick_rects) <= 5, \
            f"频轴刻度数量异常：{len(tick_rects)}"
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

    def test_bookmark_pills_do_not_stack(self, main_window):
        """近距离书签药丸原来直接压在一起（截图叠字）；现在按行下移，
        3 行放不下则只留虚线旗——锁住「药丸两两不相交」。"""
        import spectrum_widget as sw
        _select_left(main_window, "spectrum")
        panel = main_window.spectrum
        gen = panel.generator
        old_gen = (gen.center_freq_hz, gen.sample_rate_hz)
        old_bm = (panel._state.show_bookmarks, list(panel._state.bookmarks))
        try:
            gen.center_freq_hz = 100e6
            gen.sample_rate_hz = 2.048e6
            panel._state.show_bookmarks = True
            panel._state.bookmarks = [
                (100_000_000, "BOOKMARK-LONG-NAME-ALPHA"),
                (100_001_000, "BOOKMARK-LONG-NAME-BETA"),
                (100_002_000, "BOOKMARK-LONG-NAME-GAMMA"),
            ]
            rect = QRectF(0, 0, panel._fft_plot.width(),
                          panel._fft_plot.height())
            rec = _RecordingPainter()
            sw._draw_bookmark_tags(rec, panel._state, rect)
            pills = rec.pills
            assert len(pills) >= 2, f"书签药丸只画出 {len(pills)} 个"
            for i, a in enumerate(pills):
                for b in pills[i + 1:]:
                    assert not a.intersects(b), \
                        f"书签药丸叠字: {a} vs {b}"
                assert a.left() >= rect.left() - 0.5
                assert a.right() <= rect.right() + 0.5
                assert a.top() >= rect.top() - 0.5
        finally:
            gen.center_freq_hz, gen.sample_rate_hz = old_gen
            panel._state.show_bookmarks, panel._state.bookmarks = old_bm
            panel.update()

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

    def test_window_never_shrinks_below_declared_minimum(self, main_window):
        """请求 800×600 时窗口被最小尺寸钳到 1000×600——这是已拍板的设计，
        测试锁住「钳位后仍满足全部布局规则」，而不是偷偷跑一个假 800 档。"""
        requested = main_window.property("_audit_requested")
        assert main_window.minimumWidth() >= 1000
        assert main_window.minimumHeight() >= 600
        assert main_window.width() >= main_window.minimumWidth()
        if requested == "800x600":
            assert (main_window.width(), main_window.height()) != (800, 600), \
                "800×600 不可达：若未来放开最小尺寸，请补真实 800 档基线"

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
# 补充：长名称 / 长文本在窄容器里的截断规则
# ---------------------------------------------------------------------------
class TestLongTextTruncation:
    def test_module_node_name_is_pixel_elided(self):
        """信号流节点名按像素省略：原来「超过 16 字符才截」，全角名在
        108px 文本区里 16 字早已溢出，drawText 硬切且不给省略号。"""
        from module_panel import _elide_node_name
        f = QFont()
        f.setPointSize(8)
        fm = QFontMetricsF(f)
        avail = 120 - 12          # FlowCanvas 节点宽 120，文本区左右各留 6
        for name in ("WFM Demodulator 1234567890",
                     "超长卫星下行链路解调器节点名称示例"):
            elided = _elide_node_name(name, f, avail)
            assert elided != name, f"{name!r} 未被截断"
            assert elided.endswith("…"), f"{name!r} 截断无省略号: {elided!r}"
            assert fm.horizontalAdvance(elided) <= avail + 0.5, (
                f"{name!r} 省略后仍宽 {fm.horizontalAdvance(elided):.1f}px "
                f"> {avail}px")
        # 短名字原样保留（不许无脑加省略号）
        assert _elide_node_name("AM", f, avail) == "AM"

    def test_planetarium_ai_card_fits_narrow_sky_slot(self, main_window):
        """AI 回复卡按天空槽收窄：文字上限 520px 大于槽宽时，卡片右缘会
        顶出槽外被父容器裁掉半句话。槽被搬进窄容器（右 Dock）时必须收口。"""
        mw = main_window
        card = getattr(mw, "_planetarium_ai_card", None)
        assert card is not None, "缺少星象仪 AI 卡片"
        mw.set_app_mode("planetarium")
        slot = mw._planetarium_sky_slot
        old_min, old_max = slot.minimumWidth(), slot.maximumWidth()
        try:
            slot.setFixedWidth(300)      # 模拟窄槽（右 Dock 视口约 288px）
            _pump(80)
            assert slot.width() <= 320, f"天空槽未被收窄: {slot.width()}"
            mw._on_planetarium_ai_reply("长文本占位 " * 60)
            _pump(60)
            g = card.geometry()
            assert g.left() >= 0, f"AI 卡片左缘越界: {g}"
            assert g.right() <= slot.width() + 1, (
                f"AI 卡片顶出天空槽: card={g.x()}+{g.width()} "
                f"slot={slot.width()}x{slot.height()}")
        finally:
            slot.setMinimumWidth(old_min)
            slot.setMaximumWidth(old_max)
            mw.set_app_mode("ide")
            _pump(60)


# ---------------------------------------------------------------------------
# 截图基线：固定面板 + 宽松比对（尺寸/宽高比/32×24 粗网格色块）
# ---------------------------------------------------------------------------
_SIG_COLS, _SIG_ROWS = 32, 24
_UPDATE = os.environ.get("MBDSDR_UI_BASELINE_UPDATE") == "1"


def _normalize(img: QImage, dpr: float = 1.0, max_logical_w: int = 320) -> QImage:
    """按逻辑像素归一：不同 QT_SCALE_FACTOR 下 grab 的像素数不同，
    先除以 DPR 再等比缩到逻辑宽度 ≤320，最后回到 DPR=1 的逻辑图。
    这样 125%/150%/200% 共用同一套基线，不用按 DPI 存多份。"""
    lw = img.width() / dpr
    lh = img.height() / dpr
    tw = min(float(max_logical_w), lw)
    th = tw * lh / lw
    out = img.scaled(max(1, round(tw)), max(1, round(th)),
                     Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    out.setDevicePixelRatio(1.0)
    return out


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


def _axis_label_geometry(win) -> list:
    """频轴 + dB 轴刻度标签的真实矩形（供基线 sidecar 做几何 diff）。"""
    import spectrum_widget as sw
    _select_left(win, "spectrum")
    plot = win.spectrum._fft_plot
    rect = QRectF(0, 0, plot.width(),
                  max(1, plot.height() - sw._SPEC_BOTTOM_PAD))
    rec_f = _RecordingPainter()
    sw._draw_freq_scale(rec_f, rect, sw._view_center_hz(plot.state),
                        plot.state.panel.generator.sample_rate_hz,
                        QColor("white"))
    rec_db = _RecordingPainter()
    sw._draw_db_scale(rec_db, rect, plot.state, QColor("white"))
    out = []
    for group, rec in (("freq", rec_f), ("db", rec_db)):
        for r in _text_rects(rec):
            out.append({"group": group, "text": r[4], "x0": r[0],
                        "x1": r[1], "y0": r[2], "y1": r[3]})
    return out


@pytest.mark.parametrize("panel", ["spectrum", "adsb_empty", "freq_readout"])
def test_panel_screenshot_matches_baseline(main_window, panel):
    """关键面板截图基线：锁尺寸/宽高比/主要色块，不做逐像素死比。"""
    dpr = main_window.devicePixelRatioF()
    img = _normalize(_panel_image(main_window, panel), dpr)
    assert not img.isNull(), f"{panel} 截图为空"
    size = f"{main_window.width()}x{main_window.height()}"
    path = os.path.join(BASELINE_DIR, f"{panel}_{size}.png")

    if _UPDATE:
        from ui_baseline import guarded_update
        reason = os.environ.get("MBDSDR_UI_BASELINE_REASON", "").strip()
        accept_geom = (os.environ.get(
            "MBDSDR_UI_BASELINE_ACCEPT_GEOMETRY") == "1")
        # 受控更新：--reason 必填；几何（尺寸/宽高比/刻度标签）变化默认
        # 拒绝，只有 --accept-geometry-change + 点名声明 token 才放行。
        labels = _axis_label_geometry(main_window) if panel == "spectrum" \
            else None
        summary = guarded_update(path, img, reason,
                                 accept_geometry_change=accept_geom,
                                 label_geometry=labels)
        print(f"[baseline] {os.path.basename(path)}: {summary['text']}"
              f"  reason={summary['reason']}", flush=True)
        pytest.skip(f"已更新基线 {os.path.basename(path)}")

    assert os.path.exists(path), (
        f"基线缺失 {path}；先运行 python scripts/ui_layout_audit.py "
        f"--update-baselines 并人工复核")
    base = QImage(path)
    assert not base.isNull(), f"基线无法读取: {path}"
    # 几何判据 = 绝对像素容差 + 比例容差双条件，两个容差都由归一化流程的
    # 量化误差上界推出（不是拍的）：grab 取整 ≤0.5px + _normalize 的
    # round() ≤0.5px，单图 ≤1px，被测图与基线各一张 → 最坏 2px。
    # 3px 无法由取整解释，只可能来自 ≥1px 真实布局变化，故不放宽。
    # 固定 1% 的旧判据会误杀纯取整差：320×240 高差 2px 的比值差就有
    # 0.011 > 1%（把尺子的精度问题记成了产品的漂移）。推导见
    # scripts/ui_baseline.py::geometry_tolerance。
    from ui_baseline import geometry_tolerance, geometry_within_tolerance
    if not geometry_within_tolerance(img.width(), img.height(),
                                     base.width(), base.height()):
        px, ratio = geometry_tolerance(base.width(), base.height())
        cur_r = img.width() / img.height()
        base_r = base.width() / base.height()
        assert False, (
            f"{panel} 几何漂移: 当前 {img.width()}x{img.height()} "
            f"基线 {base.width()}x{base.height()}（Δw={img.width() - base.width():+d} "
            f"Δh={img.height() - base.height():+d}，像素容差 ±{px:.0f}px；"
            f"宽高比 {cur_r:.4f} vs {base_r:.4f} Δ={cur_r - base_r:+.4f}，"
            f"比例容差 ±{ratio:.4f}）")
    if img.height() != base.height():
        # 尺寸有 ±2px 合法取整差时，先归一到同一分辨率再比色块：
        # 否则高度差 2px 会让整张色块网格重新采样，把几何取整误报成
        # "内容变了"（频谱曲线尤其明显）。尺寸/比例锁在上面已经生效。
        img = img.scaled(base.width(), base.height(), Qt.IgnoreAspectRatio,
                         Qt.SmoothTransformation)

    # 两档尺度：粗网格看「主要色块区域」，细网格容忍字形/抗锯齿差异
    worst, mean = _diff_stats(_signature(img, 8, 6), _signature(base, 8, 6))
    assert worst <= 45 and mean <= 10.0, (
        f"{panel} 主要色块区域漂移（worst={worst}, mean={mean:.1f}）: "
        f"{path}；确属有意改版再 --update-baselines")
    worst, mean = _diff_stats(_signature(img), _signature(base))
    assert worst <= 100 and mean <= 20.0, (
        f"{panel} 细节色块漂移（worst={worst}, mean={mean:.1f}）: {path}")
