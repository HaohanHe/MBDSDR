# SPDX-License-Identifier: MIT
"""
主流程纯键盘可达性（2026-11-13 测绘，对应 P1-37 / P1-40 / P1-34 / W55 / W61）
================================================================================

判据（用户口径）：插着设备、不摸鼠标，能否完成
「选设备 → 连接 → 设频率 → 设模式 → 开始收听 → 调增益 → 停止 → 断开」。
「有快捷键」不等于「能纯键盘完成」——中间缺一步焦点落不到，整条路就是断的。

本文件把测绘结论钉成可复跑断言：

  * 展开态（真实 ≥978px 内容宽）下，Tab 焦点环**包含右栏**，且
    频率输入 / 模式下拉 / 增益滑杆 / 收听下拉 / 断开按钮都能靠 Tab + 方向键/
    空格走到并生效；
  * 「跳到分组 ▾」可用 Space 开菜单、方向键选「设备」、Enter 展开并聚焦下拉；
  * 显式 Tab 链按「工具栏 → 顶栏 → 左栏 → 右栏 → 底坞」落地，且链外每个
    可见可聚焦控件仍能 Tab 到（反证，防止漏控件变成断链）；
  * 窄窗折叠后按「展开」时浮层真的显示右栏内容；关闭浮层后 ``right_col``
    的隐藏状态还原，主窗仍停在窄条（2026-11-13 修复，原 xfail 已摘）。

环境说明（哪些不能自动化）：
  * 没有真实 SDR：用 ``control_panel.set_sdr_connected(True)`` 与
    ``DevicePanel._apply_devices()`` 模拟「已插设备、已连接」的**控件启用态**；
    射频频段/音频链路不在此断言范围。
  * Ctrl+C / Ctrl+D 的**按键分发**在 offscreen QTest 下不经过 QShortcut 映射
    （仓库既有结论，见 test_shortcut_focus_priority），故这里只断言
    QAction 存在且 context=WindowShortcut；按键路径本身由 Qt 保证。

运行:
    python -m pytest tests/test_keyboard_main_flow.py -q
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QAction  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


def _region(w, x):
    """控件属于哪一栏（只用于断言消息，不参与行为判断）。"""
    if x is None:
        return "none"
    if w.connect_btn.parentWidget().isAncestorOf(x):
        return "topbar"
    if w.home_btn.parentWidget().isAncestorOf(x):
        return "dock"
    if w.left_tab.isAncestorOf(x):
        return "left"
    if w.right_tab.isAncestorOf(x):
        return "right"
    if w.centralWidget().isAncestorOf(x):
        return "center"
    return "other"


def _walk(w, start, steps):
    """从 start 收焦，连续按 Tab；返回每一站 (region, widget)。"""
    out = []
    start.setFocus(Qt.OtherFocusReason)
    app.processEvents()
    for _ in range(steps):
        cur = app.focusWidget()
        if cur is None:
            break
        QTest.keyClick(cur, Qt.Key_Tab)
        app.processEvents()
        out.append((_region(w, app.focusWidget()), app.focusWidget()))
    return out


def _section(w, title):
    for s in w.right_tab._sections:
        if s.title() == title:
            return s
    raise AssertionError("右栏没有「%s」节" % title)


def _expand(w, title):
    s = _section(w, title)
    if not s.is_expanded():
        s.header.setFocus(Qt.OtherFocusReason)
        app.processEvents()
        QTest.keyClick(s.header, Qt.Key_Space)
        app.processEvents()
    return s


@pytest.fixture(scope="module")
def win(isolated_profile):
    """真实主窗 + 「已插设备」的启用态。

    offscreen 屏只有 800×800，窗口内容宽 860 < 展开阈值 978，默认必然走
    折叠分支；这里把断点清零并调用产品的 ``_expand_right_column_inline()``，
    模拟真实 ≥978 窗口的展开态（不改产品代码）。
    """
    import main_window as mw

    w = mw.MainWindow()
    w.resize(1400, 800)
    w.show()
    w.activateWindow()
    app.processEvents()

    dp = getattr(w, "device_panel", None)
    if dp is not None:
        # 无硬件时热插拔轮询会把注入的假设备清空，测绘期间停掉
        dp._hotplug_timer.stop()
        # 构造里的 singleShot(400, refresh_devices_async) 停不掉：它若在本模块
        # 中段才触发，会把用例刚注入的假设备清成空表（红绿随机器负载翻转）。
        # 先等这次初始枚举收口，再让用例注入。
        QTest.qWait(500)
        app.processEvents()

    w._right_collapse_breakpoint = 0
    w._expand_right_column_inline()
    w.control_panel.set_sdr_connected(True)
    w.disconnect_btn.setEnabled(True)
    app.processEvents()
    _expand(w, "控制")
    yield w
    try:
        w._teardown_connection()
    except Exception:  # noqa: BLE001 - 清场失败不掩盖用例结论
        pass
    w.close()
    w.deleteLater()
    app.processEvents()


def test_connect_disconnect_actions_are_window_shortcuts(win):
    """连接/断开在登记表里有键，且是窗口级（不是窗口部件级）。"""
    got = {}
    for a in win.findChildren(QAction):
        ks = a.shortcut().toString()
        if ks in ("Ctrl+C", "Ctrl+D"):
            got[ks] = (a.text(), a.shortcutContext())
    assert "Ctrl+C" in got, "连接缺 Ctrl+C（file.connect）"
    assert "Ctrl+D" in got, "断开缺 Ctrl+D（file.disconnect）"
    for ks, (label, ctx) in got.items():
        assert ctx == Qt.WindowShortcut, "%s（%r）不是窗口级快捷键" % (ks, label)


def test_tab_ring_covers_right_column(win):
    """展开态从顶栏「连接」起走 60 步，焦点必须真的进右栏并经过频率输入框。"""
    _expand(win, "控制")
    seq = _walk(win, win.connect_btn, 60)
    regions = [r for r, _ in seq]
    assert "right" in regions, (
        "Tab 焦点环跳过了整条右栏（区域序列=%s）" % ">".join(regions))
    assert win.control_panel.freq_input in [x for _, x in seq], (
        "焦点环没经过频率输入框；右栏虽然出现在区域里，但控制节内容仍不可达")


def test_eight_step_flow_is_keyboard_only(win):
    """八步主流程：全程只用 Tab / 方向键 / 空格 / 回车 / 键入。"""
    # 1) 选设备：Tab 到「跳到分组 ▾」→ Space 开菜单 → 下到「设备」→ Enter
    nav = next(b for b in win.findChildren(QWidget)
               if getattr(b, "objectName", lambda: "")() == "rightTabNav")
    nav.setFocus(Qt.OtherFocusReason)
    app.processEvents()
    QTest.keyClick(nav, Qt.Key_Space)
    app.processEvents()
    menu = nav.menu()
    assert menu is not None and menu.isVisible(), "跳到分组菜单不能用 Space 打开"
    di = next(i for i, a in enumerate(menu.actions()) if a.text() == "设备")
    for _ in range(di + 1):
        QTest.keyClick(menu, Qt.Key_Down)
    QTest.keyClick(menu, Qt.Key_Return)
    app.processEvents()
    dev = _section(win, "设备")
    assert dev.is_expanded(), "Enter 选「设备」后该节没有展开"
    assert win.right_tab.isAncestorOf(app.focusWidget()) and \
        app.focusWidget() is win.device_panel.dev_combo, (
            "跳到设备后焦点没落在设备下拉上，用户还得再找")

    dp = win.device_panel
    dp._apply_devices([
        {"driver": "rtlsdr", "label": "Fake A", "index": 0,
         "serial": "AAA", "tuner": "R820T"},
        {"driver": "rtlsdr", "label": "Fake B", "index": 1,
         "serial": "BBB", "tuner": "R820T"},
    ])
    app.processEvents()
    assert dp.dev_combo.count() == 2, "假设备没进下拉，键盘选择无从谈起"
    first = dp.dev_combo.currentText()
    dp.dev_combo.setFocus(Qt.OtherFocusReason)
    app.processEvents()
    QTest.keyClick(dp.dev_combo, Qt.Key_Down)
    app.processEvents()
    assert dp.dev_combo.currentText() != first, "设备下拉不响应方向键"

    # 2) 连接：设备面板连接按钮必须能被 Tab 走到，空格能触发
    seen = [x for _, x in _walk(win, dp.dev_combo, 12)]
    assert dp.connect_btn in seen, "从设备下拉 Tab 12 步都没到连接按钮"
    fired = []
    dp.connect_requested.connect(lambda d: fired.append(d))
    dp.connect_btn.setFocus(Qt.OtherFocusReason)
    app.processEvents()
    QTest.keyClick(dp.connect_btn, Qt.Key_Space)
    app.processEvents()
    assert fired, "连接按钮按空格没有发出 connect_requested"

    # 3) 设频率：控制节 → 频率框 → 键入 → 回车
    _expand(win, "控制")
    cp = win.control_panel
    tuned = []
    cp.tune_sdr_requested.connect(lambda hz: tuned.append(hz))
    fi = cp.freq_input
    assert fi in [x for _, x in _walk(win, _section(win, "控制").header, 12)], (
        "从控制标题 Tab 12 步没到频率输入框")
    fi.setFocus(Qt.OtherFocusReason)
    app.processEvents()
    fi.clear()
    QTest.keyClicks(fi, "98.5M")
    QTest.keyClick(fi, Qt.Key_Return)
    app.processEvents()
    assert tuned and abs(tuned[-1] - 98_500_000.0) < 1.0, (
        "频率框键入 98.5M + Enter 没有发出 98.5 MHz 调谐请求：%r" % (tuned[-2:],))

    # 4) 设模式：模式下拉方向键 + 频谱局部键
    modes = []
    cp.mode_changed.connect(lambda m: modes.append(m))
    mc = cp.mode_combo
    before = mc.currentText()
    mc.setFocus(Qt.OtherFocusReason)
    app.processEvents()
    QTest.keyClick(mc, Qt.Key_Down)
    app.processEvents()
    assert mc.currentText() != before and modes, "模式下拉不响应方向键"

    plot = win.spectrum._plot
    plot.setFocus(Qt.OtherFocusReason)
    app.processEvents()
    freq0 = win.spectrum.generator.center_freq_hz
    QTest.keyClick(plot, Qt.Key_Right)
    app.processEvents()
    assert win.spectrum.generator.center_freq_hz > freq0, "谱面方向键不调谐"

    from PySide6.QtGui import QShortcut
    bound = {s.key().toString(): s for s in plot.findChildren(QShortcut)}
    hk = []
    win.spectrum.mode_hotkey_requested.connect(lambda m: hk.append(m))
    assert "N" in bound, "频谱没有装配局部键 N（NFM）"
    bound["N"].activated.emit()
    assert hk and hk[-1] == "NFM", "频谱局部键 N 没有请求 NFM"

    # 5) 开始收听 / 7) 停止：收听下拉
    lc = win.listen_combo
    # B1：收听下拉随真实连接态启停（未连接置灰）。本用例模拟「已连接」，
    # 必须过同一个开关，而不是直接把下拉 setEnabled(True)。
    win._panels_set_sdr_connected(True)
    app.processEvents()
    assert lc.isEnabled(), "已连接后收听下拉必须可用"
    assert {lc.itemText(i) for i in range(lc.count())} >= {"▶ 收听", "⏸ 停止解调"}
    lc.setFocus(Qt.OtherFocusReason)
    app.processEvents()
    QTest.keyClick(lc, Qt.Key_Down)
    app.processEvents()
    assert lc.currentText() == "🔇 不收听", "收听下拉 ↓ 没走到「不收听」"
    QTest.keyClick(lc, Qt.Key_Up)
    app.processEvents()
    assert lc.currentText() == "⏸ 停止解调", "收听下拉 ↑ 没回到「停止解调」"

    # 6) 调增益：控制 → 「接收」折叠组 → 空格 → Tab 到滑杆 → 方向键
    rx = next(h for h in cp.findChildren(QWidget, "controlFoldHeader")
              if "接收" in h.text())
    seen = [x for _, x in _walk(win, _section(win, "控制").header, 16)]
    assert rx in seen, "从控制标题 Tab 16 步没到「接收」折叠组"
    rx.setFocus(Qt.OtherFocusReason)
    app.processEvents()
    QTest.keyClick(rx, Qt.Key_Space)
    app.processEvents()
    assert cp.gain_slider.isVisible(), "空格展开「接收」后增益滑杆仍不可见"
    seen = [x for _, x in _walk(win, rx, 10)]
    assert cp.gain_slider in seen, "展开「接收」后 Tab 10 步没到增益滑杆"
    cp.gain_slider.setValue(10)
    cp.gain_slider.setFocus(Qt.OtherFocusReason)
    app.processEvents()
    QTest.keyClick(cp.gain_slider, Qt.Key_Right)
    app.processEvents()
    assert cp.gain_slider.value() == 11, "增益滑杆不响应方向键"

    # 8) 断开：顶栏断开按钮空格
    clicked = []
    win.disconnect_btn.clicked.connect(lambda: clicked.append(1))
    assert win.disconnect_btn.isEnabled()
    win.disconnect_btn.setFocus(Qt.OtherFocusReason)
    app.processEvents()
    QTest.keyClick(win.disconnect_btn, Qt.Key_Space)
    app.processEvents()
    assert clicked, "断开按钮按空格没有触发"


def test_explicit_tab_chain_is_visual_order(win):
    """显式链清单：左→右、上→下，且底坞排在整条右栏之后。

    这条钉住「哪些控件进了显式链」；链外控件的可达性由下一条反证。
    """
    names = ("toggle_docks_btn", "toolbar_mode_combo", "new_vfo_btn",
             "cycle_vfo_btn", "toolbar_volume", "toolbar_mute_btn",
             "record_btn", "replay_btn",
             "connect_btn", "disconnect_btn", "listen_combo",
             "_focus_btn", "_float_btn",
             "left_tab", "right_nav", "right_tab", "home_btn")
    expected = [getattr(win, n) for n in names]
    assert list(win._tab_order) == expected, (
        "显式 Tab 链与视觉顺序不一致：\n期望 "
        f"{names}\n实际 {[type(x).__name__ for x in win._tab_order]}")

    # 链上相邻两颗，在真实焦点环里必须真的相邻（含不可聚焦锚点）。
    # 例外：右栏顶部的「跳到分组」→ 右栏滚动区之间隔着整条右栏内容，
    # 这一段要求「中间只能经过右栏子树」（NoFocus 装饰控件不算 Tab 落点）。
    # 容器本身（QTabWidget）后面允许跟着它自己的子控件（如内部 QTabBar）——
    # Qt 不允许 setTabOrder(祖先, 外部叶子) 越过容器的子控件。
    for first, second in zip(expected, expected[1:]):
        cur = first.nextInFocusChain()
        hops = 0
        while cur is not second and hops < 4000:
            if first.isAncestorOf(cur):
                pass
            elif (first is win.right_nav
                    and (win.right_tab.isAncestorOf(cur) or cur is win.right_tab)):
                pass
            elif cur.focusPolicy() == Qt.NoFocus:
                pass
            else:
                raise AssertionError(
                    "链上的 %s 之后、%s 之前串进了不该有的可聚焦控件：%s(%s)"
                    % (type(first).__name__, type(second).__name__,
                       type(cur).__name__, cur.objectName()))
            cur = cur.nextInFocusChain()
            hops += 1
        assert cur is second, (
            f"链上的 {type(first).__name__} 之后走不到 {type(second).__name__}；"
            "setTabOrder 实际没有落地")


def test_controls_outside_explicit_chain_stay_tab_reachable(win):
    """反证：链外每个「可见且可用」的可聚焦控件仍必须真的能 Tab 到。

    显式链最大的风险是漏掉的控件从此 Tab 不到（比顺序错更糟）。这里不用
    nextInFocusChain 自证（那只能证明环没断），而是从「连接」出发真按 Tab
    走一整圈，收集实际拿到焦点的控件。
    """
    _expand(win, "控制")
    chain_ids = {id(x) for x in win._tab_order}

    def _focusables():
        out = []
        for x in win.findChildren(QWidget):
            if x.isWindow() or id(x) in chain_ids:
                continue
            if x.focusPolicy() == Qt.NoFocus:
                continue
            try:
                if not x.isVisible() or not x.isEnabled():
                    continue
            except RuntimeError:
                continue
            out.append(x)
        return out

    candidates = _focusables()
    assert len(candidates) > 20, (
        "链外可聚焦控件只有 %d 个，反证样本不足" % len(candidates))

    seen = set()
    win.connect_btn.setFocus(Qt.OtherFocusReason)
    app.processEvents()
    for _ in range(800):
        cur = app.focusWidget()
        if cur is None:
            break
        seen.add(id(cur))
        QTest.keyClick(cur, Qt.Key_Tab)
        app.processEvents()
        if app.focusWidget() is win.connect_btn and len(seen) > 1:
            break

    missing = [x for x in candidates if id(x) not in seen]
    detail = []
    for x in missing[:8]:
        detail.append(f"{type(x).__name__}({x.objectName() or x.text() if hasattr(x, 'text') else ''})")
    assert not missing, (
        "有 %d 个链外控件按 Tab 一整圈也到不了（漏掉即断）：%s"
        % (len(missing), "、".join(detail)))


def test_tab_order_does_not_jump_from_right_column_to_bottom_dock(win):
    _expand(win, "控制")
    seq = _walk(win, _section(win, "控制").header, 50)
    dock_at = [i for i, (r, _) in enumerate(seq) if r == "dock"]
    assert dock_at, "焦点环里没有底坞，用例前提不成立"
    after = [r for r, _ in seq[dock_at[0] + 1:]]
    # 焦点环是圆形的：走完一圈会自然回到右栏。缺陷的特征是「底坞之后
    # 立刻又出现右栏」，也就是在本圈回到顶栏之前就撞见 right。
    if "topbar" in after:
        after = after[:after.index("topbar")]
    else:
        after = [r for r in after if r != "dock"]
    assert "right" not in after, (
        "底坞之后、回到顶栏之前又进了右栏（%s）：底坞应排在整条右栏之后，"
        "而不是插在「新时空」和「扫频」之间" % ">".join(after))


def test_narrow_window_overlay_shows_right_column(win):
    w = win
    w._right_collapse_breakpoint = 978
    try:
        w._collapse_right_column()
        app.processEvents()
        assert not w._right_col.isVisible(), (
            "前置条件：收窄后 right_col 应隐藏")
        w.right_strip_expand.setFocus(Qt.OtherFocusReason)
        app.processEvents()
        QTest.keyClick(w.right_strip_expand, Qt.Key_Space)
        app.processEvents()
        assert w._right_overlay is not None and w._right_overlay.isVisible(), (
            "空格没有打开右栏浮层")
        assert w.right_tab.isVisible(), (
            "浮层已显示，但右栏内容仍 hidden（只剩「收起」按钮）")
        visible_headers = [s for s in w.right_tab._sections if s.header.isVisible()]
        assert len(visible_headers) >= 10, (
            "浮层里可见的节标题只有 %d 个，内容没有真正显示" % len(visible_headers))

        # 浮层关闭后，right_col 的 hide/show 状态必须还原成「收窄态 = 隐藏」，
        # 且主窗右栏仍停在窄条——不能修成「展开好了，收起后主窗右栏也变了」。
        QTest.keyClick(app.focusWidget() or w, Qt.Key_Escape)
        app.processEvents()
        assert w._right_overlay is None, "Esc 没有关闭浮层"
        assert w._right_stack.currentWidget() is w.right_strip, (
            "关闭浮层后右栏没有回到窄条态")
        assert not w._right_col.isVisible(), (
            "关闭浮层后 right_col 仍可见：隐藏状态没有还原，主窗右栏被改动")

        # 恢复展开态：right_col 必须重新可见（不能留下永久 hidden）。
        w._right_collapse_breakpoint = 0
        w._expand_right_column_inline()
        app.processEvents()
        assert w._right_stack.currentWidget() is w._right_col
        assert w._right_col.isVisible(), (
            "恢复展开态后 right_col 仍 hidden：浮层把显示状态改坏了")
    finally:
        QTest.keyClick(app.focusWidget() or w, Qt.Key_Escape)
        app.processEvents()
        if w._right_overlay is not None:
            w._close_right_overlay()
        w._right_collapse_breakpoint = 0
        w._expand_right_column_inline()
        app.processEvents()
