# SPDX-License-Identifier: MIT
"""窄屏最小宽/高的逐控件拆账（只读，不改产品、不写用户配置）。

用途：把「窗口最小宽 1625 / 最小高 729」拆到具体控件，供
``docs/narrow-screen-plan.md`` 的数字来源与另一条线复跑对账。

纪律：
* 只读——不调用任何 set/flush/save，不改文件；
* ``HOME/USERPROFILE/APPDATA/LOCALAPPDATA`` 一律重定向到临时目录，
  真实 ``%APPDATA%/mbdsdr/desktop_settings.json`` 一个字节都不碰；
* **必须用 ``windows`` 平台插件**：本机 ``QT_QPA_PLATFORM=offscreen`` 下
  ``QFontDatabase.families()`` 为 0（实测），所有文字按方框度量，字号/控件高/
  挤压数全部不可信；脚本启动即断言字体库非空，为 0 直接退出码 2。
  窗口用 ``WA_DontShowOnScreen`` 不上屏，布局仍按请求尺寸计算；
* 每次 ``resize`` 后**硬断言**「实际尺寸 == 请求尺寸」：窗口有最小尺寸硬墙
  （实测 ``setMinimumSize(1000, 680)``），请求 960×633 会被夹成 1000×680、
  1265×618 会被抬成 1265×680。一旦不相等立即写 stderr 并以**退出码 3**
  终止——绝不把夹取后的尺寸当测量结果写进 JSON。
* 验收三档固定为 ``1000×680`` / ``1265×680`` / ``1440×900``
  （见 ``docs/narrow-screen-plan.md`` §0.0）；`960×633` 与 `1265×618` 已作废。

运行::

    python scripts/ui_narrow_breakdown.py
    python scripts/ui_narrow_breakdown.py --json      # 只输出 JSON，便于机器比对
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

_HOME = tempfile.mkdtemp(prefix="narrow-breakdown-home-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
for _k in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
    os.environ[_k] = _HOME
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFontDatabase, QFontMetrics  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QComboBox, QDoubleSpinBox, QFrame, QLabel, QLineEdit,
    QPushButton, QCheckBox, QSlider, QStyle, QTabWidget, QToolBar, QToolButton,
    QWidget,
)

#: 验收三档（唯一口径，见 ``docs/narrow-screen-plan.md`` §0.0）。
#: ``960×633`` 宽被声明最小宽 1000 改写、``1265×618`` 高被声明最小高 680
#: 改写，两者物理不可达，已作废，不得再出现在测量点里。
TIERS = ((1000, 680), (1265, 680), (1440, 900))

#: 请求尺寸被窗口最小尺寸夹住时的退出码（与「字体库为 0」的 2 区分）。
EXIT_SIZE_CLAMPED = 3


class SizeClamped(RuntimeError):
    """请求尺寸被最小尺寸夹住：此时任何数字都不能当测量结果。"""


def _isolate_qsettings() -> None:
    """把产品对 QSettings 的调用重定向到临时目录下的 ini（不读也不写用户注册表）。

    产品用两参构造 ``QSettings("MBDSDR", "Desktop")``，Qt 在 Windows 下这种写法
    **恒为 NativeFormat**（实测 ``setDefaultFormat(IniFormat)`` 后
    ``defaultFormat()==IniFormat``，但实例 ``format()`` 仍是 NativeFormat、
    ``fileName()`` 仍是 ``\\HKEY_CURRENT_USER\\Software\\MBDSDR\\Desktop``）。
    MainWindow 会 restore/save ``window_geometry`` 与 ``three_column_sizes``，
    于是上一次真实运行的窗口/分栏状态会漏进本次测量：同一命令连跑会在
    29/1 与 35/1 之间跳（35 态右栏被 restored 比例压窄约 55px）。

    对策：在 ``import main_window`` **之前**把 ``PySide6.QtCore.QSettings``
    换成一个适配器——两参构造改走 ``IniFormat`` + ``UserScope`` + 临时目录。
    产品里 ``from PySide6.QtCore import QSettings`` 取到的就是这个适配器，
    其余 Qt API 不受影响。
    """
    import PySide6.QtCore as qtcore

    real = qtcore.QSettings
    real.setPath(real.IniFormat, real.UserScope,
                 os.path.join(_HOME, "settings"))

    class _IniQSettings:
        def __new__(cls, *args, **kwargs):
            if (len(args) == 2 and isinstance(args[0], str)
                    and isinstance(args[1], str)):
                return real(real.IniFormat, real.UserScope, args[0], args[1])
            return real(*args, **kwargs)

    qtcore.QSettings = _IniQSettings


def _pump(app, n: int = 60) -> None:
    """按墙钟排空事件：``n`` 是事件次数下限，实际至少等 1.0s。

    固定次数的 ``processEvents`` 在不同机器/平台上耗时不同，实测同一条命令
    连跑两次会在 29/1 与 35/1 之间跳——差别来自产品里 700ms 的定时器是否已
    触发。这里按墙钟等待，保证每次测量都落在同一个稳态。
    """
    end = time.monotonic() + 1.0
    for _ in range(n):
        app.processEvents()
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def _text_of(w: QWidget) -> str:
    if isinstance(w, QLabel):
        return w.text()
    if isinstance(w, QComboBox):
        return w.currentText()
    if isinstance(w, QPushButton):
        return w.text()
    if isinstance(w, QCheckBox):
        return w.text()
    if isinstance(w, QToolButton):
        return w.text() or w.accessibleName()
    if isinstance(w, QLineEdit):
        return w.text() or w.placeholderText()
    if isinstance(w, QSlider):
        return "slider"
    if isinstance(w, QFrame) and w.frameShape() in (QFrame.VLine, QFrame.HLine):
        return "separator"
    return ""


def _label_of(w: QWidget) -> str:
    return f"{type(w).__name__}({w.objectName() or '-'})"


def _rows_for_layout(layout) -> list[tuple[QWidget, int, int, int]]:
    """(控件, 布局实际采用的最小宽, minimumSizeHint 宽, sizeHint 宽)。

    布局实际采用值取 ``QWidgetItem.minimumSize()`` —— 它按 sizePolicy 的
    Shrink 位在 minimumSize 与 minimumSizeHint 之间取舍，是唯一能对上
    ``layout.minimumSize()`` 的口径；直接用 minimumSizeHint 会差几十像素。
    """
    out = []
    for i in range(layout.count()):
        it = layout.itemAt(i)
        if it is None:
            continue
        w = it.widget()
        if w is None:
            continue
        out.append((w, max(0, it.minimumSize().width()),
                    max(0, w.minimumSizeHint().width()),
                    w.sizeHint().width()))
    return out


def _layout_spacers(layout) -> list[int]:
    """布局里的 spacer（addStretch/addSpacing）各自最小宽，漏掉它们就对不上账。"""
    out = []
    for i in range(layout.count()):
        it = layout.itemAt(i)
        if it is None or it.widget() is not None:
            continue
        sp = it.spacerItem()
        out.append(sp.sizeHint().width() if sp is not None else 0)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="只输出 JSON 汇总")
    args = ap.parse_args()

    app = QApplication.instance() or QApplication([])
    fams = QFontDatabase.families()
    if not fams:
        print("错误：QFontDatabase.families() 为 0（平台 %s）。"
              "字体会退化成方框，文字尺寸/控件高/挤压数全部不可信；"
              "本脚本拒绝出数。请改用 QT_QPA_PLATFORM=windows "
              "（窗口用 WA_DontShowOnScreen 不上屏）。" % app.platformName(),
              file=sys.stderr)
        return 2
    _isolate_qsettings()
    import main_window as mw

    win = mw.MainWindow()
    # 不上屏：windows 平台下也能按请求尺寸算布局，但不会真的弹窗。
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    # 初始尺寸就按声明最小（地板）请求：比地板小的请求会被夹取，而夹取后的
    # 结果正是本脚本明令拒绝的「假尺寸」。
    win.resize(win.minimumWidth(), win.minimumHeight())
    win.show()
    _pump(app)

    screen = app.primaryScreen()
    font = app.font()
    fm = QFontMetrics(font)
    env = {
        "qt_platform": app.platformName(),
        "font_families": len(fams),
        "device_pixel_ratio": round(screen.devicePixelRatio(), 4),
        "logical_dpi": round(screen.logicalDotsPerInch(), 2),
        "physical_dpi": round(screen.physicalDotsPerInch(), 2),
        "font_family": font.family(),
        "font_point_size": font.pointSizeF(),
        "font_line_height": fm.height(),
        "style": type(app.style()).__name__,
        "appdata_redirected_to": _HOME,
    }

    result: dict = {"env": env}

    def emit(title: str) -> None:
        if not args.json:
            print(f"\n== {title} ==")

    # ---------- 1. 顶栏 ----------
    top = win.findChild(QWidget, "topBar")
    top_rows = _rows_for_layout(top.layout())
    top_spacers = _layout_spacers(top.layout())
    top_item_count = top.layout().count()
    top_sum = sum(r[1] for r in top_rows) + sum(top_spacers)
    # 布局 spacing：QHBoxLayout
    top_spacing = top.layout().spacing() * max(0, top_item_count - 1)
    top_margins = (top.layout().contentsMargins().left()
                   + top.layout().contentsMargins().right())
    emit("顶栏 topBar（按布局采用的最小宽降序）")
    if not args.json:
        print(f"minimumSizeHint = {top.minimumSizeHint().width()}"
              f" | layout minimumSize 宽 = {top.layout().minimumSize().width()}"
              f" | 子控件+spacer 最小宽合计 {top_sum}"
              f" | spacing {top.layout().spacing()}×{max(0, top_item_count - 1)}"
              f"={top_spacing}"
              f" | 左右边距 {top_margins}"
              f" | 校验 {top_sum + top_spacing + top_margins}"
              f"（与 layout minimumSize 的差额见残差说明）")
    for w, lm, mh, sh in sorted(top_rows, key=lambda r: -r[1]):
        if not args.json:
            print(f"  {_label_of(w):<28} layout_min={lm:>4} "
                  f"minHint={mh:>4} hint={sh:>4} {_text_of(w)!r}")
    cum = 0
    if not args.json:
        print("  贡献排序（按布局采用的最小宽；累计 = 到该行为止的宽度和）：")
        for w, lm, mh, sh in sorted(top_rows, key=lambda r: -r[1]):
            if lm <= 0:
                continue
            cum += lm
            print(f"    {_label_of(w):<28} 自身={mh:>4} 布局采用={lm:>4} "
                  f"累计={cum:>4}")
    result["topBar"] = {
        "minimumSizeHint": top.minimumSizeHint().width(),
        "children_sum": top_sum,
        "spacing": top_spacing,
        "children": [{"label": _label_of(w), "layout_min": lm,
                      "min_hint": mh, "hint": sh, "text": _text_of(w)}
                     for w, lm, mh, sh in sorted(top_rows, key=lambda r: -r[1])],
    }

    # ---------- 2. 主工具栏 ----------
    tb = win.findChild(QToolBar, "mainToolBar")
    tb_rows = []
    for a in tb.actions():
        w = tb.widgetForAction(a)
        if w is None:
            continue
        tb_rows.append((w, max(0, w.minimumSizeHint().width(),
                               w.minimumSize().width()),
                        w.sizeHint().width()))
    tb_sum = sum(r[1] for r in tb_rows)
    emit("主工具栏 mainToolBar（按自身最小宽降序）")
    if not args.json:
        print(f"minimumSizeHint = {tb.minimumSizeHint().width()}"
              f"（QToolBar 自报的最小值只算「可放一个按钮」，不是可用下限）"
              f" | hint {tb.sizeHint().width()}"
              f" | 子控件最小宽合计 {tb_sum}（这才是可用下限）"
              f" | 实际高 {tb.height()}")
    for w, mw_, sh in sorted(tb_rows, key=lambda r: -r[1]):
        if not args.json:
            print(f"  {_label_of(w):<28} min={mw_:>4} hint={sh:>4} {_text_of(w)!r}")
    result["mainToolBar"] = {
        "minimumSizeHint": tb.minimumSizeHint().width(),
        "sizeHint": tb.sizeHint().width(),
        "children_sum": tb_sum,
        "height": tb.height(),
        "children": [{"label": _label_of(w), "min": mw_, "hint": sh,
                      "text": _text_of(w)} for w, mw_, sh in
                     sorted(tb_rows, key=lambda r: -r[1])],
    }

    # ---------- 3. 频谱工具栏 ----------
    spec = win.spectrum
    for i in range(win.left_tab.count()):
        if win.left_tab.tabText(i).strip() == "频谱":
            win.left_tab.setCurrentIndex(i)
            break
    _pump(app, 80)
    root = spec.layout()
    bar = None
    for i in range(root.count()):
        it = root.itemAt(i)
        if it.layout() is not None and type(it.layout()).__name__ in (
                "QHBoxLayout", "QBoxLayout"):
            bar = it.layout()
            break
    emit("频谱工具栏（按段拆）")
    if bar is None:
        if not args.json:
            print("  未找到频谱工具栏 QHBoxLayout")
        result["spectrumToolbar"] = {"error": "no_hbox_layout"}
    else:
        items = []
        for i in range(bar.count()):
            it = bar.itemAt(i)
            w = it.widget()
            if w is None:
                sp = it.spacerItem()
                items.append({"kind": "spacer",
                              "min": sp.sizeHint().width() if sp else 0,
                              "label": "<spacer>", "text": ""})
                continue
            items.append({"kind": "widget", "label": _label_of(w),
                          "min": w.minimumSizeHint().width(),
                          "hint": w.sizeHint().width(), "text": _text_of(w)})
        widgets = [it for it in items if it["kind"] == "widget"]
        sizes = sum(it["min"] for it in items)
        spacing = bar.spacing() * max(0, len(items) - 1)
        margins = bar.contentsMargins().left() + bar.contentsMargins().right()
        groups = {
            "① 常驻 窗/FFT": widgets[0:4],
            "更多菜单入口": widgets[4:5],
            "②③ 已折叠（不在工具栏，见 toolTip）": [],
            "④ 只读读数": widgets[-4:],
        }
        if not args.json:
            print(f"minimumSizeHint = {spec.minimumSizeHint().width()}"
                  f" | 项最小宽合计 {sizes} + spacing {spacing} + 边距 {margins}"
                  f" = {sizes + spacing + margins}")
        group_report = {}
        for name, g in groups.items():
            gs = sum(it["min"] for it in g)
            gsp = bar.spacing() * max(0, len(g) - 1)
            group_report[name] = {"sum": gs, "spacing": gsp, "total": gs + gsp}
            if not args.json:
                print(f"  {name}: min={gs} + spacing={gsp} -> {gs + gsp}")
        if not args.json:
            print("  逐项：")
            for it in items:
                if it["kind"] == "widget":
                    print(f"    {it['label']:<28} min={it['min']:>4} "
                          f"hint={it.get('hint', 0):>4} {it['text']!r}")
                else:
                    print(f"    {'<spacer>':<28} min={it['min']:>4}")
        result["spectrumToolbar"] = {
            "minimumSizeHint": spec.minimumSizeHint().width(),
            "items_sum": sizes, "spacing": spacing, "margins": margins,
            "total": sizes + spacing + margins,
            "groups": group_report,
            "items": items,
        }

    # ---------- 4. 高度构成 ----------
    emit("高度构成")
    central = win.centralWidget()
    page0 = central.widget(0) if hasattr(central, "widget") else central
    h = {
        "menuBar": win.menuBar().height(),
        "mainToolBar": tb.height(),
        "central_page0_min": page0.minimumSizeHint().height(),
        "statusBar": win.statusBar().height(),
        "sum": (win.menuBar().height() + tb.height()
                + page0.minimumSizeHint().height() + win.statusBar().height()),
        "central_page0": {},
    }
    page_items = []
    lay = page0.layout()
    if lay is not None:
        for i in range(lay.count()):
            it = lay.itemAt(i)
            w = it.widget()
            if w is None:
                continue
            page_items.append((w, w.minimumHeight(),
                               w.minimumSizeHint().height()))
    h["central_page0"] = {_label_of(w): {"minimumHeight": mh, "minimumSizeHint": msh}
                          for w, mh, msh in page_items}
    if not args.json:
        print(f"  菜单栏 {h['menuBar']} + 主工具栏 {h['mainToolBar']} + 中央 "
              f"{h['central_page0_min']} + 状态栏 {h['statusBar']} = {h['sum']}")
        for w, mh, msh in page_items:
            print(f"  中央行 {_label_of(w):<24} minimumHeight={mh:>4} "
                  f"minimumSizeHint={msh:>4}")
        if lay is not None:
            print(f"  中央布局 minimumSize 高 = {lay.minimumSize().height()}"
                  f" | spacing {lay.spacing()}"
                  f" | 上下边距 {lay.contentsMargins().top()}/"
                  f"{lay.contentsMargins().bottom()}")
        child_h = sorted({w.sizeHint().height() for w, _, _ in tb_rows})
        print(f"  主工具栏高 {tb.height()}｜子控件 sizeHint 高 {child_h}"
              f"（最大 {max(child_h) if child_h else 0}，"
              f"差 {tb.height() - (max(child_h) if child_h else 0)} = "
              f"QStyle 上下边距；tokens 里没有 toolbar_h 键）")
    result["height"] = h

    # ---------- 4c. 左栏页签拆账（高度主矛盾：514 是谁撑的） ----------
    lt = getattr(win, "left_tab", None)
    left_report: dict = {}
    if lt is not None:
        tabbar = lt.tabBar()
        pages = []
        for i in range(lt.count()):
            pw = lt.widget(i)
            rows = []
            playout = pw.layout()
            if playout is not None:
                for j in range(playout.count()):
                    it = playout.itemAt(j)
                    cw = it.widget()
                    if cw is None:
                        continue
                    rows.append({"label": _label_of(cw),
                                 "text": _text_of(cw),
                                 "min_hint_h": cw.minimumSizeHint().height(),
                                 "min_h": cw.minimumHeight()})
            pages.append({
                "title": lt.tabText(i),
                "minimum_height": pw.minimumHeight(),
                "minimum_size_hint": pw.minimumSizeHint().height(),
                "size_hint": pw.sizeHint().height(),
                "rows": rows,
            })
        left_report = {
            "tab_bar_height": tabbar.height(),
            "tab_bar_min_hint": tabbar.minimumSizeHint().height(),
            "tab_widget_min_hint": lt.minimumSizeHint().height(),
            "pages": pages,
        }
        if not args.json:
            print(f"  左栏 tabBar 高 {tabbar.height()}（minHint "
                  f"{tabbar.minimumSizeHint().height()}）｜tabWidget minHint "
                  f"{lt.minimumSizeHint().height()}")
            for p in pages:
                print(f"    页 {p['title']}: minHint {p['minimum_size_hint']}"
                      f" hint {p['size_hint']}")
                for r in p["rows"]:
                    print(f"      行 {r['label']:<24} minHintH={r['min_hint_h']:>4}"
                          f" minH={r['min_h']:>4} {r['text']!r}")
    result["leftDock"] = left_report

    # ---------- 4d. 工具栏 icon-only / 页签图标化的实测（只量 sizeHint） ----------
    probe: dict = {}
    try:
        ico = win.style().standardIcon(QStyle.SP_MediaPlay)
        text_btn = QPushButton("连接")
        icon_btn = QPushButton()
        icon_btn.setIcon(ico)
        icon_btn.setToolTip("连接")
        cb = QComboBox()
        cb.addItems(["FM"])
        text_tabs = QTabWidget()
        for t in ("星象仪模式", "卫星跟踪", "频谱"):
            text_tabs.addTab(QWidget(), t)
        icon_tabs = QTabWidget()
        for t in ("星象仪模式", "卫星跟踪", "频谱"):
            icon_tabs.addTab(QWidget(), ico, "")
            icon_tabs.setTabToolTip(icon_tabs.count() - 1, t)
        probe = {
            "text_button_hint_h": text_btn.sizeHint().height(),
            "icon_button_hint_h": icon_btn.sizeHint().height(),
            "combo_hint_h": cb.sizeHint().height(),
            "text_tabbar_hint_h": text_tabs.tabBar().sizeHint().height(),
            "icon_tabbar_hint_h": icon_tabs.tabBar().sizeHint().height(),
            "icon_tabbar_min_hint_h": icon_tabs.tabBar().minimumSizeHint().height(),
            "toolbar_current_h": tb.height(),
        }
        if not args.json:
            print(f"  icon-only 实测：文字按钮 {probe['text_button_hint_h']} → "
                  f"仅图标 {probe['icon_button_hint_h']}（combo "
                  f"{probe['combo_hint_h']}）；页签文字 "
                  f"{probe['text_tabbar_hint_h']} → 图标 "
                  f"{probe['icon_tabbar_hint_h']}")
    except Exception as exc:  # 探针失败不影响主账
        probe = {"error": repr(exc)}
        if not args.json:
            print(f"  探针失败：{exc!r}")
    result["probe"] = probe

    # ---------- 4e. 状态栏/菜单栏的下限 ----------
    result["chrome_floor"] = {
        "menu_bar_min_hint": win.menuBar().minimumSizeHint().height(),
        "status_bar_min_hint": win.statusBar().minimumSizeHint().height(),
    }
    if not args.json:
        print(f"  菜单栏 minHint {result['chrome_floor']['menu_bar_min_hint']}"
              f"｜状态栏 minHint {result['chrome_floor']['status_bar_min_hint']}")

    # ---------- 4b. 1625 到底谁撑的（隐藏对照实验） ----------
    def _win_min() -> tuple[int, int]:
        _pump(app, 40)
        ms = win.minimumSizeHint()
        return ms.width(), ms.height()

    baseline = _win_min()
    top.hide()
    top_hidden = _win_min()
    top.show()
    tb.hide()
    tb_hidden = _win_min()
    tb.show()
    _pump(app, 40)
    emit("归属对照（隐藏谁，窗口最小宽降到多少）")
    attribution = {
        "both_shown": list(baseline),
        "topBar_hidden": list(top_hidden),
        "mainToolBar_hidden": list(tb_hidden),
    }
    result["attribution"] = attribution
    if not args.json:
        print(f"  两者都在: {baseline}")
        print(f"  隐藏 topBar: {top_hidden}  → 降 {baseline[0] - top_hidden[0]}")
        print(f"  隐藏 mainToolBar: {tb_hidden}  → 降 {baseline[0] - tb_hidden[0]}")

    # ---------- 4f. 口径核对：声明最小宽 = 1000（硬墙），内容最小宽 = ?
    # minimumSizeHint() 只是「内容想要多宽」；真正挡住窗口的是
    # setMinimumSize(1000, 600)。两者必须分开报，别把 hint 当最小宽。
    # 顺带统计「按声明最小宽 1000 摆开时」有多少控件被压/越界。
    declared = [win.minimumWidth(), win.minimumHeight()]
    content_hint = list(baseline)

    def _scan_overflow() -> dict:
        from PySide6.QtWidgets import (
            QLabel, QAbstractButton, QComboBox, QLineEdit, QWidget, QToolBar,
        )
        roots = [(top.objectName() or "topBar", top),
                 ("bottomDock", win.findChild(QWidget, "bottomDock")),
                 ("mainToolBar", tb), ("central", win.centralWidget())]
        squeezed, clipped = [], []
        for root_name, root in roots:
            if root is None:
                continue
            for child in root.findChildren(QWidget):
                if not child.isVisible() or child.width() <= 0:
                    continue
                par = child.parentWidget()
                if par is None:
                    continue
                p = child.mapTo(par, child.rect().bottomRight())
                if p.x() > par.width() + 1 or p.y() > par.height() + 1:
                    clipped.append(f"{root_name}:"
                                   f"{par.objectName() or type(par).__name__}>"
                                   f"{child.objectName() or type(child).__name__}")
                if isinstance(child, (QLabel, QAbstractButton, QComboBox,
                                      QLineEdit)):
                    need = child.sizeHint().width()
                    if need > child.width() + 2:
                        txt = child.text() if hasattr(child, "text") else ""
                        squeezed.append(
                            f"{root_name}:"
                            f"{child.objectName() or type(child).__name__}"
                            f"「{txt[:12]}」{need}→{child.width()}")
        return {"squeezed_count": len(squeezed), "clipped_count": len(clipped),
                "squeezed": squeezed[:60], "clipped": clipped[:60]}

    def _scan_stable(timeout_s: float = 4.0) -> dict:
        """等到挤压/越界计数连续两帧不变再取数。

        状态栏文本和 700ms 定时器会异步改写控件内容，固定等待时间在不同机器上
        仍会让同一命令的数字跳（实测 29/1 ↔ 35/1）。这里以「计数稳定」为准，
        而不是以「等了多少毫秒」为准。
        """
        last = _scan_overflow()
        end = time.monotonic() + timeout_s
        stable = 0
        while time.monotonic() < end:
            _pump(app, 3)
            cur = _scan_overflow()
            if ((cur["squeezed_count"], cur["clipped_count"])
                    == (last["squeezed_count"], last["clipped_count"])):
                stable += 1
                if stable >= 2:
                    return cur
            else:
                stable = 0
            last = cur
        return last

    def _resize_exact(req_w: int, req_h: int, label: str = "") -> dict:
        """按请求尺寸摆开并**硬断言**实际 == 请求。

        窗口最小尺寸会夹住请求（旧 `960×633` / `1265×618` 就是这么测错的）。
        夹住时抛 ``SizeClamped``，由 ``main`` 转成退出码 3——测量结果作废，
        不许把夹取后的尺寸当成那个尺寸的账。
        """
        win.resize(req_w, req_h)
        _pump(app, 40)
        actual = [win.width(), win.height()]
        if actual != [req_w, req_h]:
            raise SizeClamped(
                f"{label or f'{req_w}×{req_h}'}：请求 {req_w}×{req_h}，"
                f"实际 {actual[0]}×{actual[1]}（窗口最小 "
                f"{win.minimumWidth()}×{win.minimumHeight()}）。"
                f"请求尺寸不可达，本次测量作废。")
        return {"requested": [req_w, req_h], "actual": actual, "equal": True}

    try:
        _pump(app, 20)
        # 声明最小（setMinimumSize 硬墙）就是能达到的最小尺寸，按它测量。
        size_declared = _resize_exact(*declared, label="declared")
        overflow_at_declared = _scan_stable()
        # 1344×700 不是验收档（验收见 TIERS），只是 release_check.py 沿用的
        # 诊断点；它高于地板、请求可达，同样受硬断言约束。
        size_target = _resize_exact(1344, 700, label="1344x700")
        overflow_at_target = _scan_stable()
        # 验收三档：每档都断言实际 == 请求，任何一档被夹住即整体作废。
        tier_sizes: dict = {}
        tier_overflow: dict = {}
        for tw, th in TIERS:
            key = f"{tw}x{th}"
            tier_sizes[key] = _resize_exact(tw, th, label=key)
            tier_overflow[key] = _scan_stable()
        win.resize(1400, 900)
        _pump(app, 40)
        sizes = {
            "declared": size_declared,
            "1344x700": size_target,
            **tier_sizes,
        }
    except SizeClamped as exc:
        print(f"错误：{exc}", file=sys.stderr)
        win.close()
        return EXIT_SIZE_CLAMPED
    result["caliber"] = {
        "declared_minimum": declared,
        "content_minimumSizeHint": content_hint,
        "sizes": sizes,
        # 硬断言之后这里恒为空（夹具被夹住时进程已退出码 3）；
        # 保留键位给 release_check.py 对账。
        "size_mismatch": [k for k, v in sizes.items() if not v["equal"]],
        "overflow_at_declared_minimum": overflow_at_declared,
        "overflow_at_1344x700": overflow_at_target,
        "tiers": {"sizes": tier_sizes, "overflow": tier_overflow},
    }
    if not args.json:
        print("\n== 口径核对（声明最小 vs 内容最小） ==")
        print(f"  声明最小（setMinimumSize 硬墙）: {declared[0]}×{declared[1]}")
        print(f"  内容最小（minimumSizeHint）: {content_hint[0]}×{content_hint[1]}"
              f"  ← 这是「内容想要」，不是窗口地板")
        print(f"  按声明最小 {declared[0]}×{declared[1]} 摆开"
              f"（实际 {size_declared['actual'][0]}×{size_declared['actual'][1]}）："
              f"挤压 {overflow_at_declared['squeezed_count']}"
              f" / 越界 {overflow_at_declared['clipped_count']}")
        print(f"  按 1344×700 摆开（实际 {size_target['actual'][0]}×"
              f"{size_target['actual'][1]}，诊断点非验收档）："
              f"挤压 {overflow_at_target['squeezed_count']}"
              f" / 越界 {overflow_at_target['clipped_count']}")
        for key, ov in tier_overflow.items():
            print(f"  验收档 {key}（实际 {tier_sizes[key]['actual'][0]}×"
                  f"{tier_sizes[key]['actual'][1]}）："
                  f"挤压 {ov['squeezed_count']} / 越界 {ov['clipped_count']}")

    # ---------- 5. 汇总 ----------
    # 键名沿用（release_check.py 消费 window_minimumSizeHint），但语义必须读清：
    #   window_minimumSizeHint = 内容最小（minimumSizeHint，会被字体度量放大/缩小）
    #   window_minimumWidth    = 声明最小（setMinimumSize 硬墙，真正挡住窗口的那个）
    summary = {
        "window_minimumSizeHint": list(baseline),
        "window_minimumWidth": declared,
        "window_minimumSize": [win.minimumSize().width(),
                               win.minimumSize().height()],
        "topBar_width": top.minimumSizeHint().width(),
        "mainToolBar_width": tb_sum,
        "spectrum_width": spec.minimumSizeHint().width(),
        "height": h["sum"],
        "caliber": result["caliber"],
    }
    result["summary"] = summary
    if not args.json:
        print("\n== 环境指纹 ==")
        for k, v in env.items():
            print(f"  {k}: {v}")
        print("\n== 汇总（对账用这 6 个数） ==")
        for k, v in summary.items():
            print(f"  {k}: {v}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
