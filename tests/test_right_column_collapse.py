# SPDX-License-Identifier: MIT
"""右栏窄宽收窄（候选 A）回归锁。

用户 2026-11-12 裁定：窄宽下右栏收成 48px 图标条，断点 978 批准。六条要求
逐条落成断言：

  1. 断点冻结：收窄态内容宽 852 不得触发展开（正反各走一次，防打摆）。
  2. 保存路径不许把 48 写进 three_column_sizes，且不动收窄几何、不发
     splitterMoved；配置解出来必须是三值、含用户原右栏宽。
  3. 浮层不抢焦点、不暂停刷新；Esc / 点外部 / 收起按钮三条关闭路径都测。
  4. 窄条三控件都有 accessibleName 与 tooltip；录音键有「录制中」状态描述。
  5. 鼠标 handle 4（970/992）与触控 handle 10（958/980）两口径 5 张基线
     都必须 0 触发，且余量写进断言（触控只有 2px）。
  6. 反证：断点若取 970 → 最小窗口不触发；若取 992 → 1000x600 基线没余量。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_right_column_collapse.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtCore import QPoint, QSettings, QTimer, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolButton  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

#: 用户批准的断点：三栏舒适下限 404+240+326 + 手柄 4×2。
BREAKPOINT = 978
#: 5 张基线（1000x600 会被最小高 680 夹高，宽不变）。
BASELINE_WINDOWS = [(1000, 600), (1000, 680), (1280, 720),
                    (1600, 900), (1920, 1080)]
#: 屏宽五档 → 窗口最小宽（max(860, min(978, int(0.94×屏宽)))）。
SCREEN_MIN_WINDOWS = [(1920, 978), (1280, 978), (1024, 962),
                      (915, 860), (860, 860)]


def _should(content: int, breakpoint: int = BREAKPOINT) -> bool:
    """与 MainWindow._right_should_collapse 同一判定（纯函数，免建窗口）。"""
    from main_window import MainWindow
    return MainWindow._right_should_collapse(content, breakpoint)


@pytest.fixture(scope="module")
def window(isolated_profile):
    import main_window as mw

    w = mw.MainWindow()
    w.show()
    QTest.qWait(450)   # 让 showEvent 的 _fit_window_to_screen 跑完
    yield w
    try:
        w._recording = False
        if getattr(w, "_record_timer", None) is not None:
            w._record_timer.stop()
        w.close()
    except Exception:
        pass


def _shrink_to_min(w):
    """把窗口收到「最小宽」——不同屏幕下 860 可能被 978 夹住，按实际来。"""
    target = max(860, w.minimumWidth())
    w.resize(target, 680)
    QTest.qWait(150)
    return w._right_content_width()


def _widen(w):
    w.resize(1400, 760)
    QTest.qWait(150)


# ---------------------------------------------------------------------------
# 1. 断点冻结 + 正反两次，不许打摆
# ---------------------------------------------------------------------------
def test_breakpoint_frozen_and_lies_in_legal_interval(window):
    bp = window._right_collapse_breakpoint
    # 必须落在「最小窗口内容宽 < 断点 ≤ 最窄基线内容宽」之间
    # 鼠标口径 (970, 992]，触控口径 (958, 980] —— 978 同时满足。
    assert 970 < bp <= 992
    assert 958 < bp <= 980
    window._update_right_column_mode()
    assert window._right_collapse_breakpoint == bp


def test_collapse_at_narrow_and_no_oscillation(window):
    content = _shrink_to_min(window)
    assert content < window._right_collapse_breakpoint
    assert window._right_collapsed is True
    assert window._main_splitter.sizes()[2] == window._RIGHT_STRIP_W
    # 收窄态连打五次：不得自己弹回展开（断点若被重算成 700 就会打摆）
    for _ in range(5):
        window._update_right_column_mode()
        assert window._right_collapsed is True, content
        assert window._main_splitter.sizes()[2] == window._RIGHT_STRIP_W
    # 反向：够宽必须展开，且不得又缩回去
    _widen(window)
    assert window._right_collapsed is False
    assert window._main_splitter.sizes()[2] > window._RIGHT_STRIP_W
    for _ in range(3):
        window._update_right_column_mode()
        assert window._right_collapsed is False
    # 再收窄：回到窄条（正反都试过）
    _shrink_to_min(window)
    assert window._right_collapsed is True


# ---------------------------------------------------------------------------
# 2. 保存路径：48 绝不入库、几何不动、不发 splitterMoved、解出来是三值
# ---------------------------------------------------------------------------
def test_save_while_collapsed_stores_three_expanded_values(window):
    _shrink_to_min(window)
    assert window._right_collapsed is True
    remembered = list(window._expanded_column_sizes or [])
    assert len(remembered) == 3 and remembered[2] > window._RIGHT_STRIP_W

    sizes_before = list(window._main_splitter.sizes())
    moved = []
    window._main_splitter.splitterMoved.connect(
        lambda *args: moved.append(args))
    try:
        window._save_window_state()
    finally:
        window._main_splitter.splitterMoved.disconnect()

    # 保存前后收窄几何原样
    assert list(window._main_splitter.sizes()) == sizes_before
    assert window._main_splitter.sizes()[2] == window._RIGHT_STRIP_W
    assert moved == [], "保存路径触发了 splitterMoved"

    raw = QSettings("MBDSDR", "Desktop").value("three_column_sizes")
    vals = window._decode_three_column_state(raw)
    assert vals is not None and len(vals) == 3
    # 存的是展开态三值（比例守恒），不是 48
    assert vals[2] != window._RIGHT_STRIP_W
    # 解码器在超宽临时 splitter 上还原，绝对值被放大，只比比例
    ratio = lambda v: [x / float(sum(v) or 1) for x in v]  # noqa: E731
    for got, want in zip(ratio(vals), ratio(remembered)):
        assert abs(got - want) < 0.005, (vals, remembered)
    # 版本号不动：语义没变，一个键一种含义
    assert QSettings("MBDSDR", "Desktop").value(
        "three_column_layout_version") == 5


# ---------------------------------------------------------------------------
# 3. 浮层：不抢焦点、不暂停刷新、三条关闭路径
# ---------------------------------------------------------------------------
def _open_overlay(w):
    _shrink_to_min(w)
    assert w._right_collapsed is True
    w._toggle_right_overlay()
    QTest.qWait(60)
    assert w._right_overlay is not None and w._right_overlay.isVisible()
    return w._right_overlay


def test_overlay_is_child_and_does_not_grab_focus(window):
    before = QApplication.focusWidget()
    ov = _open_overlay(window)
    # 子控件（不是独立窗口）且无模态：不可能挡住主事件循环
    assert ov.parentWidget() is not None
    assert ov.isWindow() is False
    assert QApplication.activeModalWidget() is None
    assert QApplication.focusWidget() is not ov
    if before is not None:
        assert QApplication.focusWidget() is before
    # 事件循环照跑（不是「看起来卡住」）
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    QTest.qWait(80)
    timer.stop()
    assert len(ticks) >= 3
    window._close_right_overlay()


def test_overlay_closes_by_button_escape_and_outside_click(window):
    # ① 收起按钮
    ov = _open_overlay(window)
    closes = [b for b in ov.findChildren(QToolButton)
              if b.accessibleName() == "收起右栏"]
    assert closes, "浮层缺少收起按钮"
    QTest.mouseClick(closes[0], Qt.LeftButton)
    QTest.qWait(30)
    assert window._right_overlay is None

    # ② Esc
    _open_overlay(window)
    QTest.keyClick(window, Qt.Key_Escape)
    QTest.qWait(30)
    assert window._right_overlay is None

    # ③ 点外部（splitter 中部）
    _open_overlay(window)
    QTest.mouseClick(window._main_splitter, Qt.LeftButton,
                     pos=QPoint(20, 20))
    QTest.qWait(30)
    assert window._right_overlay is None

    # 三条路径之后右栏都回到 stack，且仍是收窄态
    assert window._right_col.parentWidget() is window._right_stack_host
    assert window._right_collapsed is True


# ---------------------------------------------------------------------------
# 4. 可访问性：三控件名/提示齐全，录音有「录制中」描述
# ---------------------------------------------------------------------------
def test_strip_controls_are_accessible(window):
    for btn in (window.right_strip_lamp, window.right_strip_record,
                window.right_strip_expand):
        assert btn.accessibleName().strip(), btn.objectName()
        assert btn.toolTip().strip(), btn.objectName()
        assert btn.width() == 44 and btn.height() == 44
    # 48 = 44 + 2×2
    assert window._RIGHT_STRIP_W == 44 + 2 * 2
    try:
        window._recording = True
        window._sync_right_strip_record()
        assert "录制中" in window.right_strip_record.accessibleDescription()
        assert "停止录音" in window.right_strip_record.toolTip()
    finally:
        window._recording = False
        window._sync_right_strip_record()
    assert "未录制" in window.right_strip_record.accessibleDescription()


# ---------------------------------------------------------------------------
# 5. 两种 handle 口径：5 张基线 0 触发，余量写进断言
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("handle", [4, 10])
def test_baselines_never_collapse_in_both_handle_modes(window, handle):
    bp = window._right_collapse_breakpoint
    contents = [w - 2 * handle for w, _h in BASELINE_WINDOWS]
    assert all(c >= bp for c in contents), contents
    assert not any(_should(c, bp) for c in contents)
    # 边界余量：触控口径最窄 980−978=2，鼠标 992−978=14
    margin = min(contents) - bp
    assert margin >= (2 if handle == 10 else 1), (handle, margin)


# ---------------------------------------------------------------------------
# 6. 反证两条：970 不够、992 越界
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("_screen,win_w", SCREEN_MIN_WINDOWS)
def test_every_screen_min_window_collapses(window, _screen, win_w):
    # 按用户给的公式，五档最小窗口内容宽最大 970 < 978，全部触发
    assert _should(win_w - 8, window._right_collapse_breakpoint) is True


def test_reverse_970_leaves_min_window_expanded_and_must_be_rejected(window):
    """断点若取 970：1920/1280 屏最小窗口内容宽正好 970，不触发（必红）。

    真实断点 978 时同一处必须触发——上面那条用例就是它的门禁。
    """
    bp = window._right_collapse_breakpoint
    assert _should(970, bp) is True          # 真实断点：必须收窄
    assert _should(970, 970) is False        # 970 作断点：不触发 → 不合格


def test_reverse_992_leaves_baseline_zero_margin_and_must_be_rejected(window):
    """断点若取 992：1000x600 基线内容宽 992，余量 0（必红）。

    把「最窄基线内容宽 − 断点 ≥ 1」写死成断言：改 992 立刻红。
    """
    bp = window._right_collapse_breakpoint
    narrowest = min(w - 2 * 4 for w, _h in BASELINE_WINDOWS)
    assert narrowest - bp >= 1
    assert narrowest - 992 < 1

