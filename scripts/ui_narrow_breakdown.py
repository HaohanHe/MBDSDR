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
* **验收必须按状态矩阵量**，不许拿「当前恰好是什么形态」当口径。三个可见性
  开关（``topBarDetail`` / ``QToolBar`` / 焦点模式）显式组合成四个状态：

  ============= ============ ========== ============== ==========================
  状态           topBarDetail QToolBar   set_focus_mode 说明
  ============= ============ ========== ============== ==========================
  ``focus_mode``  隐藏         隐藏       True           开箱默认（main_window.py:2104）
  ``legacy_gate`` 隐藏         可见       False          旧闸门口径，只作 6px 反证对照
  ``detail_open`` 可见         隐藏       False          只展开读数条，隔离 56px
  ``default``     可见         可见       False          点「详细」后的真实形态（24px）
  ============= ============ ========== ============== ==========================

  旧版只测 ``focus_mode``，再手动 ``hide()/show()`` 造出 ``legacy_gate`` 这个
  产品里不存在的中间态——它把滑杆列越界量成 **+6px**，真实 ``default`` 是
  **+24px**（差 18px）。每个状态都先断言可见性（不一致退出码 4），再逐档
  断言「实际尺寸 == 请求尺寸」（夹取退出码 3）。

  2026-11-11 频谱线把三根量程滑杆收进工具栏「显示」弹出层后，``dbSliderCol``
  的 ``parentWidget()`` 变成 ``QMenu``、未弹出时 ``isVisible()==False``，
  旧量尺 ``slider_column_report`` 对真窗口恒得 ``visible=False /
  actual=480（未布局的默认几何）/ rows=[] / overflow_px=0``——那个 0 是
  「量不到」，不是「没越界」。闸门改用 ``spectrum_panel_report``：面板
  ``minimumSizeHint().height()`` 与工具栏 ``minimumSizeHint().width()``
  对实得尺寸，越界 > 0 才判红。

运行::

    python scripts/ui_narrow_breakdown.py
    python scripts/ui_narrow_breakdown.py --json      # 只输出 JSON，便于机器比对

字体缩放反证（系统字体 125% / 150%）::

    MBDSDR_GATE_FONT_SCALE=1.25 python scripts/ui_narrow_breakdown.py --json

它只放大字号，不改 DPI 指纹（``QT_FONT_DPI`` 会连 devicePixelRatio 一起改）。
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
    QApplication, QComboBox, QDoubleSpinBox, QFrame, QGroupBox, QLabel,
    QLineEdit, QPushButton, QCheckBox, QSlider, QScrollArea, QStyle,
    QTabWidget, QToolBar, QToolButton, QWidget,
)

#: 验收三档（唯一口径，见 ``docs/narrow-screen-plan.md`` §0.0）。
#: ``960×633`` 宽被声明最小宽 1000 改写、``1265×618`` 高被声明最小高 680
#: 改写，两者物理不可达，已作废，不得再出现在测量点里。
TIERS = ((1000, 680), (1265, 680), (1440, 900))

#: 请求尺寸被窗口最小尺寸夹住时的退出码（与「字体库为 0」的 2 区分）。
EXIT_SIZE_CLAMPED = 3
#: 状态开关没有落到预期可见性时的退出码（量到的不是该状态，数字作废）。
EXIT_STATE_MISMATCH = 4


#: 验收状态矩阵（唯一口径，组合见模块 docstring）。
#: 顺序 = 输出顺序；``default`` 必须留在最后，它同时是 release_check.py
#: 读的验收口径。
STATES = (
    ("focus_mode", {"detail": False, "toolbars": False, "focus": True}),
    ("legacy_gate", {"detail": False, "toolbars": True, "focus": False}),
    ("detail_open", {"detail": True, "toolbars": False, "focus": False}),
    ("default", {"detail": True, "toolbars": True, "focus": False}),
)


class SizeClamped(RuntimeError):
    """请求尺寸被最小尺寸夹住：此时任何数字都不能当测量结果。"""


class StateMismatch(RuntimeError):
    """状态开关没有落到预期可见性：此时量到的是别的状态。"""


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


def _install_font_scale(scale: float) -> None:
    """把主题字体整体放大 ``scale`` 倍（模拟系统字体 125% / 150%）。

    不能改 ``QT_FONT_DPI``：实测它改的是屏幕 DPI，会把 devicePixelRatio
    一起改掉（DPR1.5 指纹下设 120，实测 ``dpr=1.25``），量到的就不是
    「只放大字体」。这里包 ``themes.get_app_font``——主题在 MainWindow 构造
    里调用它，包一层只放大字号，DPI 指纹保持不动。
    """
    import themes

    real = themes.get_app_font

    def scaled():
        f = real()
        f.setPointSizeF(f.pointSizeF() * scale)
        return f

    themes.get_app_font = scaled


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
    if isinstance(w, QGroupBox):
        return w.title()
    if isinstance(w, QFrame) and w.frameShape() in (QFrame.VLine, QFrame.HLine):
        return "separator"
    return ""


def _label_of(w: QWidget) -> str:
    return f"{type(w).__name__}({w.objectName() or '-'})"


def _who_text(row: dict) -> str:
    """责任控件的一行摘要：``名字'文本'+越界px``（滑杆/分隔线不重复文本）。"""
    text = ("" if row.get("text") in ("slider", "separator", "", None)
            else repr(row.get("text")))
    return f"{row['label']}{text}+{row['overflow']}"


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


def slider_column_report(col) -> dict:
    """旧量尺：竖排滑杆列的越界量（**已不用于真窗口**，只喂自检构造样本）。

    「越界 px」= 列内子控件底边超出列底边的最大值，正是用户看到被裁掉
    的那一截；``deficit`` = 列自报最小高 − 实得高，是挤压总量。两者不是
    同一个数：布局会先把子控件按比例压缩，压缩到极限才向外溢出。

    2026-11-11 弹层化后 ``dbSliderCol`` 挂在 ``QMenu`` 上、列布局的直接子项
    变成 3 个嵌套 ``QHBoxLayout``（滑杆不再是列的直接子控件），本函数在真
    窗口上恒得 ``rows=[] / overflow_px=0``。真窗口改用
    :func:`spectrum_panel_report`；本函数保留是给
    ``tests/test_narrow_gate_selfcheck.py`` 的构造样本（固定高的容器 + 固定
    高子控件）钉住「越界公式与红榜判定仍能抓到 24px」。
    """
    if col is None or col.layout() is None:
        return {"error": "no_dbSliderCol"}
    avail = col.height()
    rows = []
    slider_n = 0
    for i in range(col.layout().count()):
        cw = col.layout().itemAt(i).widget()
        if cw is None:
            continue
        label = _label_of(cw)
        if isinstance(cw, QSlider):
            slider_n += 1
            label = f"QSlider#{slider_n}"   # 三根滑杆都没名字，按顺序编号
        rows.append({
            "label": label,
            "text": _text_of(cw),
            "y": cw.y(),
            "height": cw.height(),
            "min_height": cw.minimumHeight(),
            "overflow": max(0, cw.y() + cw.height() - avail - 1),
        })
    needed = col.minimumSizeHint().height()
    return {
        "visible": col.isVisible(),
        "needed": needed,
        "actual": avail,
        "deficit": max(0, needed - avail),
        "overflow_px": max((r["overflow"] for r in rows), default=0),
        "offenders": sorted([r for r in rows if r["overflow"] > 0],
                            key=lambda r: -r["overflow"]),
        "rows": rows,
    }


def spectrum_panel_report(panel) -> dict:
    """频谱面板的越界量（当前量尺）：声明最小 vs 实得尺寸。

    为什么换尺：三根量程滑杆挂在工具栏「显示」``QMenu`` 的 ``QWidgetAction``
    上，``dbSliderCol.parentWidget()`` 是 ``QMenu``，未弹出时不参与面板布局；
    且列的布局直接子项是 3 个嵌套 ``QHBoxLayout``，旧量尺的行循环
    ``itemAt(i).widget()`` 一个控件都取不到，``overflow_px`` 结构上恒为 0。
    旧读数的 0 是「量不到」，不是「没越界」（对照实测见 commit 说明）。

    新口径只量两件可解释的事，差值都带明确责任方：
      * 高：面板 ``minimumSizeHint().height()`` − 面板 ``height()``
        （页面被压扁＝内容裁切）；
      * 宽：工具栏 ``minimumSizeHint().width()`` − 工具栏 ``width()``
        （「更多 / 显示」入口被裁）。
    """
    if panel is None:
        return {"error": "no_spectrum_panel"}
    tb = getattr(panel, "_toolbar", None)
    min_w = panel.minimumSizeHint().width()
    min_h = panel.minimumSizeHint().height()
    tb_min = tb.minimumSizeHint().width() if tb is not None else 0
    tb_act = tb.width() if tb is not None else 0
    over_h = max(0, min_h - panel.height())
    over_w = max(0, tb_min - tb_act)
    offenders = []
    if over_h > 0:
        offenders.append({"label": "spectrumPanel(高)", "text": "",
                          "overflow": over_h})
    if over_w > 0:
        offenders.append({"label": "spectrumToolbar(宽)", "text": "",
                          "overflow": over_w})
    offenders.sort(key=lambda r: -r["overflow"])
    return {
        "part": "spectrumPanel",
        "visible": panel.isVisible(),
        "panel_min": [min_w, min_h],
        "panel_actual": [panel.width(), panel.height()],
        "toolbar_min_w": tb_min,
        "toolbar_actual_w": tb_act,
        "axis": {"width": over_w, "height": over_h},
        "needed": min_h,
        "actual": panel.height(),
        "deficit": over_h,
        "overflow_px": max(over_h, over_w),
        "offenders": offenders,
        "rows": [],
    }


def red_entries(col: dict, rc: dict) -> list[dict]:
    """红榜判定（唯一口径）：频谱面板或右栏越界 > 0 即入红，并带责任控件。

    闸门 ``main()`` 与自检共用这一段，避免「测量函数改了、判红逻辑没改」
    各自一套。
    """
    reds = []
    if col.get("overflow_px", 0) > 0:
        reds.append({
            "part": col.get("part", "dbSliderCol"),
            "overflow_px": col["overflow_px"],
            "needed": col.get("needed"),
            "actual": col.get("actual"),
            "axis": col.get("axis"),
            "who": [_who_text(o) for o in col.get("offenders", [])[:5]],
        })
    if rc.get("overflow_px", 0) > 0:
        reds.append({
            "part": "rightColumn",
            "overflow_px": rc["overflow_px"],
            "needed": rc.get("body_min_w"),
            "actual": (rc.get("viewport") or [None])[0],
            "who": [f"{w['label']}{w['text']!r}:{w['min_w']}"
                    for w in rc.get("widest", [])[:5]],
        })
    return reds


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
    try:
        font_scale = float(os.environ.get("MBDSDR_GATE_FONT_SCALE", "1") or 1)
    except ValueError:
        font_scale = 1.0
    if font_scale != 1.0:
        _install_font_scale(font_scale)
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
        "font_scale": font_scale,
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

    # ---------- 4b. 声明最小（硬墙）与内容最小必须分开报 ----------
    # minimumSizeHint() 只是「内容想要多高多宽」；真正挡住窗口的是
    # setMinimumSize(1000, 680)。别把 hint 当最小宽。
    declared = [win.minimumWidth(), win.minimumHeight()]

    # ---------- 4f. 口径核对：状态矩阵 × 验收档（唯一验收口径） ----------
    # 旧版只测「专注态」（读数条与工具栏都隐藏），再手动 hide/show 造出一个
    # 产品里不存在的中间态（读数条隐藏 + 工具栏可见），把滑杆列的越界量成
    # +6px；真实详细态是 +24px。下面把三个可见性开关显式组合成状态，
    # 每个状态都先断言可见性，再逐档断言「实际尺寸 == 请求尺寸」。

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

    def _spectrum_panel() -> dict:
        """频谱面板的越界量（声明最小 vs 实得），量尺在 ``spectrum_panel_report``。

        旧口径量的是 ``dbSliderCol`` 容器；滑杆收进「显示」弹层后它挂在
        ``QMenu`` 上，未弹出时既不布局也不可见，读数恒 0（量不到）。
        """
        return spectrum_panel_report(win.spectrum)

    def _right_column() -> dict:
        """右栏横向真红的量尺：容器最小宽 vs 滚动视口宽（body ≤ 视口）。"""
        area = win.findChild(QScrollArea, "rightColumnScroll")
        body = win.findChild(QWidget, "rightColumnBody")
        if area is None or body is None:
            return {"error": "no_rightColumn"}
        vp = area.viewport()
        widest = []
        for c in body.findChildren(QWidget):
            if not c.isVisible() or c.width() <= 0:
                continue
            need = c.minimumSizeHint().width()
            if need > vp.width():
                widest.append({"label": _label_of(c), "text": _text_of(c),
                               "min_w": need})
        widest.sort(key=lambda r: -r["min_w"])
        return {
            "viewport": [vp.width(), vp.height()],
            "body": [body.width(), body.height()],
            "body_min_w": body.minimumSizeHint().width(),
            "overflow_px": max(0, body.minimumSizeHint().width() - vp.width() - 1),
            "hscroll_visible": area.horizontalScrollBar().isVisible(),
            "hscroll_range": area.horizontalScrollBar().maximum(),
            "widest": widest[:5],
        }

    def _apply_state(name: str, state: dict) -> None:
        """把三个可见性开关显式设成 ``state`` 的组合（不是「碰巧是什么」）。"""
        win.set_focus_mode(bool(state["focus"]))
        td = win.findChild(QWidget, "topBarDetail")
        if td is not None:
            td.setVisible(bool(state["detail"]))
        for bar in win.findChildren(QToolBar):
            bar.setVisible(bool(state["toolbars"]))
        _pump(app, 20)

    def _assert_state(name: str, state: dict) -> dict:
        """断言状态真的生效；不一致就抛 ``StateMismatch``（退出码 4）。"""
        td = win.findChild(QWidget, "topBarDetail")
        bars = win.findChildren(QToolBar)
        got = {
            "detail": bool(td is not None and td.isVisible()),
            "toolbars": bool(bars) and all(b.isVisible() for b in bars),
            "focus": bool(getattr(win, "_focus_mode", None)),
        }
        bad = [k for k in ("detail", "toolbars", "focus")
               if got[k] != bool(state[k])]
        if bad:
            raise StateMismatch(
                f"状态 {name} 未生效：期望 {state}，实际 {got}"
                f"（不一致项 {bad}）。量到的不是该状态，数字作废。")
        return got

    matrix: dict = {}
    try:
        _pump(app, 20)
        for name, state in STATES:
            _apply_state(name, state)
            _assert_state(name, state)
            ms = win.minimumSizeHint()
            entry = {
                "visible": dict(state),
                "minimumSizeHint": [ms.width(), ms.height()],
                "tiers": {},
            }
            points = [("declared", declared[0], declared[1])]
            points += [(f"{tw}x{th}", tw, th) for tw, th in TIERS]
            # 1344×700 不是验收档，只是 release_check.py 沿用的诊断点；
            # 只在验收态测一次，避免把同一份账重复记四遍。
            if name == "default":
                points.append(("1344x700", 1344, 700))
            for key, tw, th in points:
                size = _resize_exact(tw, th, label=f"{name}@{key}")
                col = _spectrum_panel()
                rc = _right_column()
                # 只有 release_check.py 对账的两个点等稳定帧（计数抖动实测
                # 会有 1~6 个差），其余档位取当帧即可——它们是同一份记账。
                scan = (_scan_stable()
                        if name == "default" and key in ("declared", "1344x700")
                        else _scan_overflow())
                reds = red_entries(col, rc)
                entry["tiers"][key] = {
                    "size": size,
                    "spectrum_panel": col,
                    "right_column": rc,
                    "squeezed_count": scan["squeezed_count"],
                    "clipped_count": scan["clipped_count"],
                    "squeezed": scan["squeezed"],
                    "clipped": scan["clipped"],
                    "red": reds,
                }
            matrix[name] = entry
        # 量完回到专注态：后面的 summary 里其它字段仍按「开箱默认」读取。
        _apply_state("focus_mode", STATES[0][1])
        win.resize(1400, 900)
        _pump(app, 40)
    except SizeClamped as exc:
        print(f"错误：{exc}", file=sys.stderr)
        win.close()
        return EXIT_SIZE_CLAMPED
    except StateMismatch as exc:
        print(f"错误：{exc}", file=sys.stderr)
        win.close()
        return EXIT_STATE_MISMATCH

    result["states"] = matrix
    default_tiers = matrix["default"]["tiers"]
    sizes = {k: v["size"] for k, v in default_tiers.items()}
    result["caliber"] = {
        "declared_minimum": declared,
        "content_minimumSizeHint": matrix["focus_mode"]["minimumSizeHint"],
        "states": matrix,
        "sizes": sizes,
        # 硬断言之后这里恒为空（夹具被夹住时进程已退出码 3）；
        # 保留键位给 release_check.py 对账。
        "size_mismatch": [k for k, v in sizes.items() if not v["equal"]],
        # 旧键位保留，口径已改成 ``default``（读数条+工具栏可见、焦点关闭）：
        # 旧版在这里量的是专注态，正是「尺子短 18px」的来源。
        "overflow_at_declared_minimum": default_tiers["declared"],
        "overflow_at_1344x700": default_tiers["1344x700"],
        "tiers": {"sizes": {k: v["size"] for k, v in default_tiers.items()},
                  "overflow": default_tiers},
    }
    if not args.json:
        print("\n== 验收矩阵（状态 × 尺寸，唯一口径） ==")
        for name, entry in matrix.items():
            v = entry["visible"]
            flags = "读数条{} / 工具栏{} / 焦点{}".format(
                "可见" if v["detail"] else "隐藏",
                "可见" if v["toolbars"] else "隐藏",
                "开" if v["focus"] else "关")
            print(f"  [{name}] {flags}｜窗口 minHint "
                  f"{entry['minimumSizeHint'][0]}×"
                  f"{entry['minimumSizeHint'][1]}")
            for key, t in entry["tiers"].items():
                a = t["size"]["actual"]
                sc = t["spectrum_panel"]
                rc = t["right_column"]
                ax = sc.get("axis") or {}
                pm = sc.get("panel_min") or ["?", "?"]
                pa = sc.get("panel_actual") or ["?", "?"]
                print(f"    {'RED' if t['red'] else 'OK '} {key:<10} "
                      f"实际 {a[0]}×{a[1]}｜频谱面板 最小{pm[0]}×{pm[1]}"
                      f"/实{pa[0]}×{pa[1]}"
                      f"/越界 宽+{ax.get('width', 0)} 高+{ax.get('height', 0)}"
                      f"｜右栏 需{rc.get('body_min_w', '?')}/视口"
                      f"{(rc.get('viewport') or ['?'])[0]}"
                      f"/越界+{rc.get('overflow_px', 0)}"
                      f"｜挤压 {t['squeezed_count']} 越界 {t['clipped_count']}")
        print("\n== 红榜（状态 / 档位 / 越界 px / 责任控件） ==")
        any_red = False
        for name, entry in matrix.items():
            for key, t in entry["tiers"].items():
                for r in t["red"]:
                    any_red = True
                    axis = ""
                    if r.get("axis"):
                        axis = (f"［宽+{r['axis']['width']} / "
                                f"高+{r['axis']['height']}］")
                    print(f"  RED [{name}] {key} {r['part']} "
                          f"+{r['overflow_px']}px{axis}（需要 {r['needed']}，"
                          f"实得 {r['actual']}）← "
                          f"{'、'.join(r['who']) or '（无逐项）'}")
        if not any_red:
            print("  （无）")
        print("\n== 频谱面板量尺（声明最小 vs 实得，default 态） ==")
        for key in ["declared"] + [f"{w}x{h}" for w, h in TIERS]:
            blk = matrix["default"]["tiers"][key]["spectrum_panel"]
            print(f"  {key:<10} 面板最小高 {blk['panel_min'][1]} / 实得高 "
                  f"{blk['panel_actual'][1]}｜工具栏最小宽 "
                  f"{blk['toolbar_min_w']} / 实际宽 {blk['toolbar_actual_w']}"
                  f"｜越界 {blk['overflow_px']}px")

    # ---------- 5. 汇总 ----------
    # 键名沿用（release_check.py 消费 window_minimumSizeHint），语义按新口径：
    #   window_minimumSizeHint = **真实默认态**（读数条+工具栏可见、焦点关闭）
    #                            的内容最小；旧版这里是专注态，量短了 18px。
    #   window_minimumSizeHint_focus_mode = 开箱默认（专注态）的内容最小，仅参考。
    #   window_minimumWidth    = 声明最小（setMinimumSize 硬墙，真正挡住窗口的那个）
    summary = {
        "window_minimumSizeHint": list(matrix["default"]["minimumSizeHint"]),
        "window_minimumSizeHint_focus_mode": list(
            matrix["focus_mode"]["minimumSizeHint"]),
        "window_minimumWidth": declared,
        "window_minimumSize": [win.minimumSize().width(),
                               win.minimumSize().height()],
        "topBar_width": top.minimumSizeHint().width(),
        "mainToolBar_width": tb_sum,
        "spectrum_width": spec.minimumSizeHint().width(),
        "height": h["sum"],
        "state_overflow": {
            name: {key: {"spectrum_panel_px": t["spectrum_panel"].get(
                             "overflow_px", 0),
                         "right_column_px": t["right_column"].get("overflow_px", 0),
                         "red": bool(t["red"])}
                   for key, t in entry["tiers"].items()}
            for name, entry in matrix.items()
        },
        "caliber": result["caliber"],
    }
    result["summary"] = summary
    if not args.json:
        print("\n== 环境指纹 ==")
        for k, v in env.items():
            print(f"  {k}: {v}")
        print("\n== 汇总（对账用） ==")
        for k in ("window_minimumSizeHint", "window_minimumSizeHint_focus_mode",
                  "window_minimumWidth", "window_minimumSize", "topBar_width",
                  "mainToolBar_width", "spectrum_width", "height"):
            print(f"  {k}: {summary[k]}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
