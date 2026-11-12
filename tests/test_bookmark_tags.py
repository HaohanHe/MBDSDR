# SPDX-License-Identifier: MIT
"""
书签频谱标签（gqrx 书签竖旗）钉扎测试（P1-8）
==============================================

桥接两套系统：控制面板/频率管理器的持久书签 → 频谱顶部暗金药丸 +
贯顶虚线；点击药丸发 bookmark_activated → 主窗口调谐（带模式切模式）。

  * set_bookmarks 归一化/清空；
  * 工具栏「书签」开关 + 断连置灰；
  * 命中几何：±30px 容差、视口外不命中、关闭开关后不命中；
  * 点击药丸带 → 只发 bookmark_activated，不发调谐；
  * 点击药丸带以下 → 不发 bookmark_activated（退回普通交互）；
  * 离屏渲染冒烟（无数据也画标签）；
  * 主窗口：收藏 → 标签同步；点击标签 → 调谐 + 模式跟随。

不依赖硬件 / 声卡。
"""
import logging
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

log = logging.getLogger(__name__)

from PySide6.QtCore import QPointF, Qt, QEvent  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _press(widget, x, y, modifiers=Qt.KeyboardModifier.NoModifier):
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(x, y),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     modifiers)
    widget.mousePressEvent(ev)


def _panel_with_bookmark(center=100e6, span=2.4e6):
    """谱面中央放一枚书签（x=plot 宽度一半），返回 (panel, freq, x_center)。"""
    from spectrum_widget import SpectrumPanel
    panel = SpectrumPanel()
    plot = panel._plot
    plot.resize(800, 400)
    gen = panel.generator
    gen.center_freq_hz = float(center)
    gen.sample_rate_hz = float(span)
    freq = float(center)
    panel.set_bookmarks([(freq, "测试台")])
    return panel, freq, plot.width() / 2.0


# ---------------------------------------------------------------------------
# setter / 开关
# ---------------------------------------------------------------------------
def test_set_bookmarks_normalizes_and_clears(qapp):
    from spectrum_widget import SpectrumPanel
    panel = SpectrumPanel()
    panel.set_bookmarks([(98.5e6, "电台"), (145e6, 42)])   # 名字非 str 也收
    # 旧 2 元组按 mode=""、bandwidth=0.0（未记录）补齐
    assert panel._state.bookmarks == [
        (98.5e6, "电台", "", 0.0), (145e6, "42", "", 0.0)]
    panel.set_bookmarks([(145e6, "带带宽", "NFM", 12_500.0)])
    assert panel._state.bookmarks == [(145e6, "带带宽", "NFM", 12_500.0)]
    panel.set_bookmarks(None)
    assert panel._state.bookmarks == []


def test_bookmark_toggle_and_available_when_disconnected(qapp):
    """书签旗标是纯显示项：断连时也必须可用（G12，2026-11-12 用户裁定）。

    旧契约断言「断连 → 书签开关置灰」，等于强迫用户先插设备才能配画法；
    现契约只把依赖数据的 DSP 项按连接状态置灰，显示项始终可用。
    """
    from spectrum_widget import SpectrumPanel
    panel = SpectrumPanel()
    assert panel._state.show_bookmarks is True
    panel._bookmark_chk.setChecked(False)
    assert panel._state.show_bookmarks is False
    panel._bookmark_chk.setChecked(True)
    panel.set_connected(True)
    assert panel._bookmark_chk.isEnabled()
    panel.set_connected(False)
    assert panel._bookmark_chk.isEnabled()
    # DSP 项仍置灰（旧契约这部分保留）
    assert not panel._fft_combo.isEnabled()


# ---------------------------------------------------------------------------
# 命中几何与点击语义
# ---------------------------------------------------------------------------
def test_tag_hit_geometry(qapp):
    panel, freq, xc = _panel_with_bookmark()
    plot = panel._plot
    assert plot._bookmark_tag_at(xc) == pytest.approx(freq)      # 正中
    assert plot._bookmark_tag_at(xc + 25.0) == pytest.approx(freq)  # 容差内
    assert plot._bookmark_tag_at(xc + 40.0) is None              # 容差外
    assert plot._bookmark_tag_at(0.0) is None                    # 视口边缘外
    panel._bookmark_chk.setChecked(False)
    assert plot._bookmark_tag_at(xc) is None                     # 开关关掉


def test_click_tag_emits_activation_not_tune(qapp):
    panel, freq, xc = _panel_with_bookmark()
    activated, tuned = [], []
    panel.bookmark_activated.connect(lambda *args: activated.append(args))
    panel.freq_changed.connect(tuned.append)
    _press(panel._plot, xc, 5.0)
    # 与书签面板同一参数集：(freq_hz, mode, bandwidth_hz)
    assert len(activated) == 1
    assert activated[0][0] == pytest.approx(freq)
    assert activated[0][1] == ""
    assert activated[0][2] == 0.0
    assert tuned == []           # 药丸带内点击不调谐、不拖 VFO


def test_click_below_tag_band_is_normal(qapp):
    panel, _freq, xc = _panel_with_bookmark()
    activated = []
    panel.bookmark_activated.connect(lambda *args: activated.append(args))
    _press(panel._plot, xc, 60.0)        # 药丸带以下 → 普通交互
    assert activated == []


def test_tag_render_smoke_no_data(qapp):
    """未连接也画书签（频率轴仍在），离屏渲染不崩。"""
    from spectrum_widget import SpectrumPanel
    panel = SpectrumPanel()
    panel.set_bookmarks([(98.5e6, "MBC")])
    panel._plot.resize(800, 300)
    panel._plot.grab()


# ---------------------------------------------------------------------------
# 主窗口接线
# ---------------------------------------------------------------------------
#: 本模块会写这些配置键（app_mode / 调谐后的频率与模式）；模块结束一律
#: 还原，否则同会话后面的模块会读到「上一个测试留下的模式」。
_STATE_KEYS = ("app_mode", "demod_mode", "frequency_hz", "bandwidth_hz")


@pytest.fixture(scope="module")
def mw(isolated_profile, qapp):
    # isolated_profile 见 tests/conftest.py：本夹具写 app_mode 与书签，
    # 不隔离就会写进用户真实配置 / 书签库。
    from main_window import MainWindow
    w = MainWindow()
    saved = {k: w.settings.get(k) for k in _STATE_KEYS}
    w.settings.set("app_mode", "ide")
    w.set_app_mode("ide")
    yield w
    try:
        w._disconnect()
    except Exception as exc:  # noqa: BLE001 - 收尾失败必须留证，不静默
        log.warning("test_bookmark_tags.py fixture 收尾失败（MainWindow._disconnect）：%s: %s",
                    type(exc).__name__, exc)
    # 清掉本测试收藏，不污染真实书签库
    w._bookmarks.clear()
    w._sync_spectrum_bookmarks()
    # 不给别的模块留状态：写回进入本模块前的值并立即落盘。
    for k, v in saved.items():
        w.settings.set(k, v)
    try:
        w.settings.flush()
    except Exception:
        pass


def test_main_window_add_syncs_tag(mw):
    before = len(mw._bookmarks)
    mw._on_bookmark_added(98.5e6, "同步测试", "AM")
    added = mw._bookmarks[before:]
    assert len(added) == 1
    hz, name, mode, builtin, bw = added[0]
    assert (hz, name, mode, builtin) == (98.5e6, "同步测试", "AM", False)
    assert bw == pytest.approx(float(mw._vfo_bw))   # 写入端带带宽
    tags = mw.spectrum._state.bookmarks
    assert any(abs(t[0] - 98.5e6) < 1 and t[1] == "同步测试"
               and t[2] == "AM" and t[3] == pytest.approx(bw)
               for t in tags)


def test_main_window_tag_activation_tunes_with_mode(mw):
    mw._bookmarks.append((145.0e6, "激活测试", "NFM", False, 0.0))
    mw._sync_spectrum_bookmarks()
    before = mw.spectrum.generator.center_freq_hz
    mw._on_bookmark_tune_requested(145.0e6, "NFM", 0.0)
    assert mw.spectrum.generator.center_freq_hz == pytest.approx(145.0e6)
    assert mw.spectrum.generator.center_freq_hz != before
    if mw._demod_cfg is not None:
        assert mw._demod_cfg.mode == "NFM"


def test_main_window_tag_activation_unknown_freq_keeps_mode(mw):
    """未收录频点（防御）：按当前模式调谐，不崩。"""
    mode = mw._demod_cfg.mode if mw._demod_cfg is not None else "NFM"
    mw._on_bookmark_tune_requested(433.0e6, mode, 0.0)
    assert mw.spectrum.generator.center_freq_hz == pytest.approx(433.0e6)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
