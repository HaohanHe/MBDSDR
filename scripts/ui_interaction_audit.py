#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
交互覆盖审计（死按钮 / 假按钮 / 静默异常）
==========================================

运行时拉起真实 ``MainWindow``（offscreen），遍历所有 ``QAction`` 与
``QAbstractButton``（菜单项、工具栏、面板按钮、dock 内控件，以及本次运行中
由入口动作构造出来的对话框按钮），逐项检查：

  1. ``enabled`` / ``visible`` / 主信号接收者数（``QObject.receivers()``）；
  2. 真实触发一次，是否有**可观察结果**：状态栏文案、弹窗、对话框、文件框、
     窗口/页签/dock 状态变化、控件文本/勾选/使能变化；
  3. 触发过程中的异常（同步抛出的 + 被 Qt 静默吞掉只打到 stderr 的），
     逐个捕获并计数。

判定三分类（互斥，异常优先）：

  * **真死** = 主信号 ``receivers() == 0``，点了不可能有反应；
  * **假活** = 有接收者，但触发后无任何可观察变化；
  * **抛异常** = 触发时同步抛出或被 Qt 吞掉的异常。

不作为"假活"处理的只有两种情况，且都必须在报告里逐条列出证据：
  * **诚实禁用**：``enabled == False``，且给出了"为什么不能用"；
  * **未实时触发**：会起线程/联网/写用户数据的动作，只做代码核对并登记
    依据（见 ``SKIP_LIVE``），不谎称已验证。

产物：``docs/interaction-audit.md``。退出码 0=三类均为空；1=有问题（未在
白名单内）或置灰没给理由。

运行::

    QT_QPA_PLATFORM=offscreen MBDSDR_NULL_AUDIO=1 MBDSDR_NO_HW=1 \\
        MBDSDR_OFFLINE=1 python scripts/ui_interaction_audit.py
"""
from __future__ import annotations

import contextlib
import io
import logging
from typing import Optional
import os
import re
import sys
import tempfile
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")
os.environ.setdefault("QT_SCALE_FACTOR", "1.0")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOC = os.path.join(_ROOT, "docs", "interaction-audit.md")

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 登记表
# ---------------------------------------------------------------------------

#: 无独立槽、但值在"对话框确认/父流程"里被读取的输入控件。
#: 键 = "名称|所在对话框"，值 = 读取点（源码符号，可核对）。
INPUT_CONTROLS: dict[str, str] = {
    "Offset Tuning（直流抵消）|选择 SDR 设备":
        "无 clicked/toggled 槽；布尔值在 `_connect_dialog` 里经 "
        "`offset_chk.isChecked()` 读取后下发后端（main_window.py 设备参数区）",
    "启用自动增益 (AGC)|选择 SDR 设备":
        "无 clicked/toggled 槽；布尔值在 `_connect_dialog` 里经 "
        "`agc_chk.isChecked()` 读取后下发后端（main_window.py 设备参数区）",
}

#: 置灰理由：键 = "种类|名称|父容器类名"（父容器可用 *），值 = 为什么不能用。
DISABLED_REASONS: dict[str, str] = {
    "按钮|断开|*": "未连接 SDR，没有可断开的设备（连接成功后自动启用）",
    "按钮|录音|*": "未连接 SDR，没有 IQ 信号可录（连接成功后自动启用）",
    "按钮|瀑布平滑|*": "频谱无 IQ 数据时置灰（收到首帧数据后联动启用）",
    "按钮|峰值|*": "频谱无 IQ 数据时置灰（收到首帧数据后联动启用）",
    "按钮|开始解码|*": "未选择录制文件，没有可解码的样本",
    "按钮|停止解码|*": "当前没有解码会话在跑",
    "按钮|保存当前图像为 PNG|*": "没有已解码的图像可保存",
    "按钮|硬件 AGC|*": "未连接 SDR，无法下发硬件增益策略",
    "按钮|启用 ANR|*": "未连接 SDR，没有 IQ 数据可做降噪",
    "按钮|识别信号|*": "未连接 SDR，没有实时信号可识别",
    "按钮|应用该解调链|*": "未连接 SDR，解调链无处下发",
    "按钮|思考中...|*": "AI 推理进行中，按钮文案即状态；完成后恢复为「发送」",
    "按钮|取消|*": "当前没有正在运行的扫描任务",
    "按钮|停止跟踪|*": "当前没有正在运行的卫星跟踪任务",
    "按钮|开始扫描|*": "无扫频后端（未连接 SDR）：scanner_panel 的 "
                       "`scan_btn.setEnabled(ready and not scanning)` 里 ready=False",
    "按钮|开始录音|*": "未连接 SDR：control_panel 随 `_sdr_connected` "
                       "整组置灰（control_panel.py 的 `w.setEnabled(self._sdr_connected)`）",
    # 「以当前频率为输入」的三个动作：连接成功 ≠ 拿到频率真值。
    # 后端读回失败时界面按红线保持「—」，这三个动作没有起点/对象，
    # 由 control_panel._sync_freq_actions_enabled 统一置灰；
    # set_frequency_hz 一拿到真值就自动放开。
    "按钮|-|ControlPanel": "未拿到频率真值（未连接或后端读回失败）：±步进没有起点，"
                           "由 `_sync_freq_actions_enabled` 置灰，拿到真值即启用",
    "按钮|+|ControlPanel": "未拿到频率真值（未连接或后端读回失败）：±步进没有起点，"
                           "由 `_sync_freq_actions_enabled` 置灰，拿到真值即启用",
    "按钮|收藏当前频率|ControlPanel": "未拿到频率真值，没有可收藏的频点；"
                                      "`set_frequency_hz` 回填后自动启用",
    "按钮|断开|NtripConfigDialog": "NTRIP 配置对话框内尚未建立连接",
    "按钮|连接|ModulePanel": "未发现 SDR 设备：module_panel 的 "
                            "`_on_devices_refreshed` 在枚举结果为空时 "
                            "`connect_btn.setEnabled(False)`；点「刷新」重新枚举到设备后自动启用",
    "按钮|连接|DevicePanel": "正在后台枚举 / 未发现 SDR 设备 / 已连接：device_panel "
                            "`connect_btn` 随 `_devices` 与连接状态置灰（构造时「正在枚举设备…」、"
                            "`_on_devices_refreshed` 空结果、`set_connected(True)` 已连接）",
    "按钮|OK|对话框:选择 SDR 设备": "设备下拉当前是占位项（currentData() 为 None，即无真设备）"
                                   "或正在刷新枚举：`ok_btn.setEnabled(enabled and not busy['on'])`，"
                                   "选中真实设备后自动启用",
    "菜单项|工具(&T) / FM 扫频找台|*": "未连接 SDR（FM 扫频需要可调谐的本地接收机）",
    "菜单项|工具(&T) / 停止卫星跟踪|*":
        "当前无跟踪目标：主窗口 `_sync_tracking_ui` 随跟踪状态启用/置灰",
}

#: 会起线程 / 联网 / 写用户数据的动作：不实时触发，只登记代码依据。
SKIP_LIVE: dict[str, str] = {
    "按钮|发送|*": "启动 AI 推理线程（ai_panel 写对话区并切「思考中…」）；"
                   "不在审计里真跑模型",
    "按钮|当前信号质量怎么样|*": "同「发送」：快捷指令把问题填进对话框并起 AI 推理线程",
    "按钮|找一个信号强的 FM 电台|*": "同「发送」：填充问题并进入 AI 思考态",
    "按钮|扫频 87-108 MHz 找所有电台|*": "同「发送」：填充问题并进入 AI 思考态",
    "按钮|调谐到 98.5 MHz 并录 30 秒|*": "同「发送」：填充问题并进入 AI 思考态",
    "按钮|开始跟踪|*": "按秒轮询硬件指向（sat_track_panel.start_tracking）；"
                       "真跑会起定时器并写后端",
    "按钮|开始扫描|*": "启动扫频线程（scanner_panel）；真跑会独占设备",
    "按钮|开始录音|*": "启动录音线程并落盘（control_panel），审计不写真实录音",
    "按钮|拉取挂载点列表|*": "发起 NTRIP 网络请求（ntrip_panel）",
    "按钮|启用 Web 服务|*": "绑定端口起 HTTP 服务（settings_panel）",
    "按钮|启用远程控制|*": "起远程控制服务（settings_panel）",
    "菜单项|工具(&T) / 更新卫星 TLE (Celestrak)|*": "联网拉 Celestrak TLE",
    "按钮|更新 TLE|*": "联网拉 Celestrak TLE（sat_track_panel）",
    "菜单项|文件(&F) / 退出|*": "关闭主窗口退出进程；等价于 win.close()，"
                                "审计窗口还要继续用，不真退出",
    "按钮|退出星象仪\n(Esc)|*":
        "星象仪浮层按钮只在星象仪形态显示（`_exit_planetarium` 里 "
        "`_planetarium_strip.hide()`，IDE 形态不可见也不可点）；审计每条动作前"
        "强制回 IDE 页，此处点击等于 no-op。星象仪形态下的真实点击路径由 "
        "`tests/test_planetarium_entry_visibility.py::test_overlay_exit_button_is_clickable` 覆盖",
}

#: 幂等按钮：点击前必须先把同组的值扰动成非默认，否则前后快照必然一致。
#: 键 = "种类|名称|*"（与 SKIP_LIVE 同口径），值 = 扰动口径说明。
PERTURB_BEFORE: dict[str, str] = {
    "按钮|恢复本组默认|*":
        "该按钮把同组的 rc_enabled/rc_port/web_enabled/web_port 写回 schema "
        "默认；在默认值上直接点必然零变化（这是审计口径问题，不是产品缺陷）。"
        "先就近把同组 spin 改成另一个合法值；spin 都置灰时退回翻转 checkbox"
        "（blockSignals，避免真的起监听/写用户数据），再点。",
}

#: 已确认是问题、但修复点不在本线文件权限内的条目（白名单必须给理由）。
FALSE_ALIVE_WHITELIST: dict[str, str] = {
    "按钮|刷新|DevicePanel":
        "`device_panel._refresh_devices` 确实重枚举设备并把状态写成"
        "「检测到 N 台设备」；设备集合/状态未变时界面零变化（幂等刷新，"
        "非死按钮）。可选改进：每次刷新都走状态栏提示，需改 "
        "`desktop/device_panel.py`，归操作逻辑线，已登记限制总账",
    "按钮|刷新设备列表|对话框:选择 SDR 设备":
        "`main_window._reload_devices` 重跑 enumerate 并重建下拉框；设备集合"
        "不变时文本/索引一致（幂等刷新，非死按钮）。可选改进：刷新后给"
        "「检测到 N 台设备」提示，需改 `desktop/main_window.py`，"
        "归操作逻辑线，已登记限制总账",
}

INTERNAL_BUTTONS = {
    "ScrollLeftButton", "ScrollRightButton", "qt_menubar_ext_button",
    "qt_toolbar_ext_button", "qt_tableview_cornerbutton",
}


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------
def _receivers(obj, sig: str) -> int:
    try:
        return int(obj.receivers(sig))
    except Exception as exc:  # noqa: BLE001 - 控件可能已被销毁，按「未知」保守计，但要留证
        log.warning("读取信号接收者失败（%s）：%s: %s",
                    sig, type(exc).__name__, exc)
        return -1


def _wired(obj) -> bool:
    """主信号是否有接收者。严格口径：receivers()==0 就是没接线。"""
    if obj.inherits("QAction"):
        return (_receivers(obj, "2triggered(bool)") > 0
                or _receivers(obj, "2triggered()") > 0)
    for sig in ("2clicked(bool)", "2clicked()", "2toggled(bool)",
                "2pressed()"):
        if _receivers(obj, sig) > 0:
            return True
    return False


def _panel_of(obj) -> str:
    """最近的面板/对话框/dock 名称，作为"所在面板"列。"""
    from PySide6.QtWidgets import QDialog, QDockWidget, QMainWindow

    p = obj.parent()
    while p is not None:
        t = type(p).__name__
        if isinstance(p, QDialog):
            return f"对话框:{p.windowTitle() or t}"
        if isinstance(p, QDockWidget):
            return f"dock:{p.windowTitle() or p.objectName() or t}"
        if t.endswith("Panel") or t.endswith("Dock"):
            return p.objectName() or t
        if isinstance(p, QMainWindow):
            return "MainWindow"
        p = p.parent()
    return "-"


def _location(obj) -> str:
    """父容器路径（最多 4 层），用于定位同名按钮。"""
    parts = []
    p = obj.parent()
    while p is not None and len(parts) < 4:
        name = p.objectName() or type(p).__name__
        if name not in ("qt_scrollarea_viewport", "qt_tabwidget_stackedwidget",
                        "QWidget"):
            parts.append(name)
        p = p.parent()
    return " / ".join(reversed(parts)) or "-"


def _walk_menu(menu, prefix=""):
    for act in menu.actions():
        if act.isSeparator():
            continue
        if act.menu() is not None:
            yield from _walk_menu(act.menu(), f"{prefix}{act.text()} / ")
        elif act.text():
            yield f"{prefix}{act.text()}", act


def iter_controls(win, label_actions=True):
    """产出 (种类, 名称, 位置, 对象, 是否有槽, 备注)。

    这是给 ``tests/test_interaction_wiring.py`` 等守卫复用的最小接口；
    审计脚本另用 ``collect_controls`` 保留 enabled/visible 等元数据。
    """
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QAbstractButton, QWidgetAction

    seen: set[int] = set()
    for label, act in _walk_menu(win.menuBar()):
        seen.add(id(act))
        yield ("菜单项", label, "菜单栏", act, _wired(act), "")

    for act in win.findChildren(QAction):
        if isinstance(act, QWidgetAction) or id(act) in seen:
            continue       # 工具栏里的容器动作，本身不可点
        if act.isSeparator() or act.menu() is not None or not act.text():
            continue
        yield ("菜单项", act.text(), _location(act), act, _wired(act), "")

    for btn in win.findChildren(QAbstractButton):
        if btn.objectName() in INTERNAL_BUTTONS:
            continue
        if not btn.text() and not btn.toolTip():
            continue
        yield ("按钮", btn.text() or btn.toolTip(), _location(btn), btn,
               _wired(btn), "")


def _is_visible(w) -> bool:
    try:
        return bool(w.isVisible())
    except Exception as exc:  # noqa: BLE001 - 控件可能已被销毁，按「不可见」保守计，但要留证
        log.warning("读取控件可见性失败（%s）：%s: %s",
                    w.objectName() or type(w).__name__, type(exc).__name__, exc)
        return False


def collect_controls(win) -> list[dict]:
    """一次性物化控件清单（避免探测过程中新建的控件混进主清单）。"""
    rows = []
    for kind, name, loc, obj, wired, note in iter_controls(win):
        rows.append({
            "kind": kind, "name": name, "loc": loc, "obj": obj,
            "wired": wired, "note": note,
            "enabled": bool(obj.isEnabled()),
            "visible": _is_visible(obj),
            "panel": _panel_of(obj),
        })
    return rows


# ---------------------------------------------------------------------------
# 可观察证据
# ---------------------------------------------------------------------------
def _snapshot(win) -> dict:
    """控件状态快照：只取"用户看得见"的字段，做前后 diff。"""
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QComboBox,
                                   QDialog, QDockWidget, QLabel, QLineEdit,
                                   QStackedWidget, QTabWidget, QWidget)

    snap: dict[str, str] = {}
    for w in win.findChildren(QWidget):
        # objectName 不唯一（多个面板都有 statusValue），必须带 id 才不丢字段
        key = f"{type(w).__name__}({w.objectName()})#{id(w)}"
        try:
            if isinstance(w, QLabel):
                # visible 也要采：AI 面板状态条的「▾」只是切换一个 QLabel
                # 的可见性（_toggle_live_details），只看文本会把正常展开
                # 判成「点了没反应」。
                snap[key] = (f"text={w.text()[:120]!r} "
                             f"visible={int(w.isVisible())}")
            elif isinstance(w, QAbstractButton):
                snap[key] = (f"text={w.text()[:60]!r} checked={int(w.isChecked())} "
                             f"enabled={int(w.isEnabled())} "
                             f"visible={int(w.isVisible())}")
            elif isinstance(w, QLineEdit):
                # styleSheet 也采集：输入校验的可见反馈就是「输入框变红」
                # （control_panel._on_freq_input 解析失败时 setStyleSheet + 700ms
                # 恢复）。不采集这一维，正常拒绝会被当成「点了没反应」。
                snap[key] = (f"text={w.text()[:80]!r} "
                             f"style={w.styleSheet()[:80]!r}")
            elif isinstance(w, QComboBox):
                snap[key] = f"idx={w.currentIndex()} text={w.currentText()[:60]!r}"
            elif isinstance(w, QAbstractSpinBox):
                # 数值控件也是用户看得见的状态：「恢复本组默认」把端口写回去，
                # 不采集它就只能看到"零变化"。
                snap[key] = f"value={w.text()!r}"
            elif isinstance(w, QTabWidget):
                snap[key] = f"idx={w.currentIndex()}"
            elif isinstance(w, QStackedWidget):
                snap[key] = f"idx={w.currentIndex()}"
            elif isinstance(w, QDockWidget):
                snap[key] = f"visible={int(w.isVisible())}"
        except Exception as exc:  # noqa: BLE001 - 单个控件快照失败不阻断整表，但要留证
            log.warning("控件快照失败（%s）：%s: %s",
                        _widget_key(w), type(exc).__name__, exc)
            continue
    for a in win.findChildren(QAction):
        if a.text() and not a.isSeparator():
            snap[f"QAction({a.text()})#{id(a)}"] = (
                f"checked={int(a.isChecked())} enabled={int(a.isEnabled())} "
                f"visible={int(a.isVisible())}")
    # 状态栏不放进快照：showMessage 已被挂钩单独记账，避免"消息超时清空"
    # 被误当成按钮有效果。
    snap["__window__"] = (f"visible={int(win.isVisible())} "
                          f"fullscreen={int(win.isFullScreen())} "
                          f"maximized={int(win.isMaximized())} "
                          f"minimized={int(win.isMinimized())} "
                          f"geo={win.width()}x{win.height()}")
    for w in win.findChildren(QDialog):
        snap[f"QDialog({w.windowTitle() or type(w).__name__})#{id(w)}"] = (
            f"visible={int(w.isVisible())}")
    return snap


#: 纯遥测读数（会自行跳动，不是按钮效果）：SNR / RSSI / S 表 / UTC / 电平。
_TELEMETRY = re.compile(
    r"text=(?:'|\")(?:SNR|RSSI|S[0-9]|UTC:|信号|电源|范围|\d{2}:\d{2}:\d{2})")


def _is_telemetry(old, new) -> bool:
    return bool(old and new and _TELEMETRY.match(str(old))
                and _TELEMETRY.match(str(new)))


def _widget_key(w) -> str:
    return f"{type(w).__name__}({w.objectName()})#{id(w)}"


def _live_widgets(win) -> dict:
    """key → 控件对象：供 _diff 判定「整键消失」是真销毁还是重排/隐藏。"""
    from PySide6.QtWidgets import QWidget
    return {_widget_key(w): w for w in win.findChildren(QWidget)}


def _still_alive(obj) -> bool:
    """C++ 对象是否还活着（PySide 删对象后 Python wrapper 仍在，isValid 才可靠）。"""
    try:
        import shiboken6
        return bool(shiboken6.isValid(obj))
    except Exception as exc:  # noqa: BLE001 - shiboken 缺失/版本差异按「未销毁」处理，但要留证
        log.warning("判定控件存活失败（shiboken6.isValid）：%s: %s",
                    type(exc).__name__, exc)
        return True


def _diff(before: dict, after: dict, limit=5, exclude=(),
          before_widgets=None) -> list[str]:
    """控件状态前后差异。

    两类变化都算按钮效果：
      * 同键值变化（可见性/勾选/文本/索引）；
      * **整键消失且 C++ 对象已销毁**——`WA_DeleteOnClose` 的对话框点「关闭」
        后所有键消失，过去被当成「无变化」，于是正常关闭被误判为假活。
    仍不算：键消失但对象还活着（列表重排/换父/移出窗口）、新增控件（懒加载）。
    """
    out = []
    for k in before.keys() | after.keys():
        if k in exclude:
            continue
        old, new = before.get(k), after.get(k)
        if old is None:
            continue        # 新增控件 = 列表重排/懒加载噪音，不作证据
        if new is None:
            obj = (before_widgets or {}).get(k)
            if obj is not None and not _still_alive(obj):
                name = re.sub(r"#\d+", "", str(k))   # 报告里去掉 id
                out.append(f"{name}: {old} -> <已销毁（关闭/删除）>")
                if len(out) >= limit:
                    break
            continue        # 对象还活着 = 重排/换父，不作证据
        if old == new or _is_telemetry(old, new):
            continue
        name = re.sub(r"#\d+", "", str(k))      # 报告里去掉 id，保留可读名
        out.append(f"{name}: {old} -> {new}")
        if len(out) >= limit:
            break
    return out


def _perturb_container(btn) -> str:
    """点幂等按钮前，把同组输入先改成非默认值（否则「点了等于没点」）。

    「恢复本组默认」读同组控件回写默认值：审计若在默认值上直接点，前后快照
    必然一致，会被误判成假活——这是**审计口径问题**，不是产品缺陷。
    优先级：spin（数值可见）→ combo（换索引）→ checkbox（blockSignals 翻转，
    避免真的起监听/写用户数据）。
    """
    from PySide6.QtWidgets import QAbstractSpinBox, QCheckBox, QComboBox

    ancestors = []
    p = btn.parent()
    while p is not None and len(ancestors) < 4:
        ancestors.append(p)
        p = p.parent()
    if not ancestors:
        return ""

    # 就近的「同组」优先（不要把设置页里无关的 FFT/采样率一起扰动）：
    # 数值 → 下拉 → 勾选。置灰的 spin 也照改：reset 一样会把它的值写回默认，
    # 禁用的控件照样能程序化改值，快照也照样看得到。
    for p in ancestors:
        for w in p.findChildren(QAbstractSpinBox):
            if w is btn:
                continue
            try:
                step = w.singleStep() or 1
                cur, lo, hi = w.value(), w.minimum(), w.maximum()
                nxt = cur + step * 2
                if nxt > hi:
                    nxt = cur - step * 2
                if nxt < lo or nxt == cur:
                    continue
                w.setValue(nxt)
                return (f"扰动 {w.objectName() or type(w).__name__}: "
                        f"{cur} -> {nxt}")
            except Exception as exc:  # noqa: BLE001 - 单个控件扰动失败不阻断审计，但要留证
                log.warning("扰动数值控件失败（%s）：%s: %s",
                            w.objectName() or type(w).__name__,
                            type(exc).__name__, exc)
                continue

        for w in p.findChildren(QComboBox):
            if w is btn or w.count() < 2:
                continue
            try:
                old = w.currentIndex()
                w.setCurrentIndex((old + 1) % w.count())
                return (f"扰动 {w.objectName() or type(w).__name__}: "
                        f"idx {old} -> {w.currentIndex()}")
            except Exception as exc:  # noqa: BLE001 - 单个控件扰动失败不阻断审计，但要留证
                log.warning("扰动下拉控件失败（%s）：%s: %s",
                            w.objectName() or type(w).__name__,
                            type(exc).__name__, exc)
                continue

        for w in p.findChildren(QCheckBox):
            try:
                old = w.isChecked()
                w.blockSignals(True)            # 只改显示态，不触发启停服务
                w.setChecked(not old)
                w.blockSignals(False)
                return (f"扰动 {w.objectName() or type(w).__name__}: "
                        f"checked {int(old)} -> {int(w.isChecked())}")
            except Exception as exc:  # noqa: BLE001 - 单个控件扰动失败不阻断审计，但要留证
                log.warning("扰动勾选控件失败（%s）：%s: %s",
                            w.objectName() or type(w).__name__,
                            type(exc).__name__, exc)
                continue
    return ""


class _ExceptionCatcher:
    """捕获被 Qt 吞掉、只走 sys.excepthook 的槽异常。"""

    def __init__(self):
        self.errors: list[str] = []
        self._old = sys.excepthook

    def install(self):
        sys.excepthook = self._hook

    def restore(self):
        sys.excepthook = self._old

    def _hook(self, typ, val, tb):
        self.errors.append("".join(
            traceback.format_exception(typ, val, tb))[-600:])


def _reveal(obj) -> list[str]:
    """把控件所在页签/堆栈页/dock/对话框切到可交互状态；返回为此做的切换。"""
    from PySide6.QtWidgets import (QApplication, QDialog, QDockWidget,
                                   QStackedWidget, QTabWidget, QWidget)

    switched = []
    # isAncestorOf 只接受 QWidget。菜单/工具栏 QAction 不是控件，直接传进去
    # 会 TypeError；改用它的关联控件做 ancestry 判定，没有关联控件就跳过页切换
    # （菜单项靠弹出菜单可达，不依赖某个堆栈页）。
    target = obj if isinstance(obj, QWidget) else next(
        (w for w in getattr(obj, "associatedWidgets", lambda: [])()
         if isinstance(w, QWidget)), None)
    p = obj.parent()
    while p is not None:
        if isinstance(p, QTabWidget):
            for i in range(p.count()):
                w = p.widget(i)
                if w is not None and target is not None and w.isAncestorOf(target):
                    if p.currentIndex() != i:
                        switched.append(f"切页签 {type(p).__name__} -> {i}")
                        p.setCurrentIndex(i)
                    break
        elif isinstance(p, QStackedWidget):
            for i in range(p.count()):
                w = p.widget(i)
                if w is not None and target is not None and w.isAncestorOf(target):
                    if p.currentIndex() != i:
                        switched.append(f"切堆栈 {type(p).__name__} -> {i}")
                        p.setCurrentIndex(i)
                    break
        elif isinstance(p, QDockWidget) and not p.isVisible():
            p.setVisible(True)
            switched.append(f"打开 dock {p.windowTitle() or p.objectName()}")
        elif isinstance(p, QDialog) and not p.isVisible():
            p.show()
            switched.append(f"打开对话框 {p.windowTitle() or type(p).__name__}")
        p = p.parent()
    QApplication.processEvents()
    return switched


def _spy_signals(obj) -> tuple[dict, list]:
    """给对象的主信号挂计数探针（在 receivers 统计之后挂，避免掩盖 0）。"""
    counts: dict[str, int] = {}
    conns = []

    def _spy(name):
        def cb(*_a, **_k):
            counts[name] = counts.get(name, 0) + 1
        return cb

    try:
        if obj.inherits("QAction"):
            obj.triggered.connect(_spy("triggered"))
        else:
            obj.clicked.connect(_spy("clicked"))
            obj.toggled.connect(_spy("toggled"))
            obj.pressed.connect(_spy("pressed"))
    except Exception as exc:  # noqa: BLE001 - 个别信号连不上不阻断审计，但要留证
        log.warning("挂接信号探针失败（%s）：%s: %s",
                    obj.objectName() or type(obj).__name__,
                    type(exc).__name__, exc)
    return counts, conns


def _detect_volatile(win, ms: int = 1600) -> set:
    """全局识别"自己会变"的字段：UTC 时钟、SNR/电平读数、状态栏瞬时值。

    观测一个完整秒级 tick，之后所有探测都排除这些字段，避免把时钟跳动
    当成"按钮有效果"。
    """
    import time

    from PySide6.QtWidgets import QApplication

    changes: dict[str, int] = {}
    prev = _snapshot(win)
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.05)
        cur = _snapshot(win)
        for k in prev.keys() | cur.keys():
            if prev.get(k) != cur.get(k):
                changes[k] = changes.get(k, 0) + 1
        prev = cur
    # 只变一次的多半是启动初始化（例如频率读数从 0 落到上次值）；
    # 至少 tick 两次才算"自变字段"，否则会把真实按钮效果也剔掉。
    return {k for k, n in changes.items() if n >= 2}


def _probe(win, row, evidence, volatile_global=()) -> dict:
    """触发一次并采集证据（返回 row 的观测字段）。"""
    from PySide6.QtWidgets import QApplication

    obj = row["obj"]
    # 可勾选控件点完要还原：它们背后连着持久化配置（专注/瀑布/主题…），
    # 留在点击后的状态既改了用户偏好，也会跨用例泄漏。实测「详细」按钮
    # 被点一次即写入 focus_mode=false，后续布局锁用例凭空多出 95px chrome
    # （detail 56 + 主工具栏 39），8 张基线图全部对不上。
    checkable_before = None
    try:
        if obj.isCheckable():
            checkable_before = obj.isChecked()
    except RuntimeError:
        pass
    # 先空跑一段事件循环，把"自己会变的字段"（UTC 时钟、SNR 读数、谱线状态）
    # 识别为 volatile 并从证据里剔除，别拿时钟跳动冒充按钮有效果。
    quiet1 = _snapshot(win)
    QApplication.processEvents()
    QApplication.processEvents()
    quiet2 = _snapshot(win)
    volatile = {k for k in quiet1.keys() | quiet2.keys()
                if quiet1.get(k) != quiet2.get(k)}
    before = quiet2
    # key → 控件：_diff 靠它区分「整键消失 = 面板销毁」与「重排/换父」。
    before_widgets = _live_widgets(win)
    for key in ("msg", "dialog", "file", "stat", "close", "new_top"):
        evidence[key].clear()
    # 非模态对话框是 show() 出来的（如 NTRIP 设置），exec 打桩抓不到，
    # 用"点击前已可见的对话框 id 集合"来判定新冒出来的那个。
    from PySide6.QtWidgets import QApplication, QDialog
    visible_before = {id(d) for d in win.findChildren(QDialog) if d.isVisible()}
    # 新顶层窗口也要按「点击前已可见的集合」判定：地点面板是
    # QFrame(Qt.Tool)、浮层 detach 换 flag 后是新顶层、菜单走 popup()——
    # 这些都不是 QDialog，只查 findChildren(QDialog) 一律判成「没反应」。
    # （evidence["new_top"] 此前只读不写，等于这条路一直是死的。）
    tops_before = {id(w) for w in QApplication.topLevelWidgets() if w.isVisible()}

    counts, _ = _spy_signals(obj)
    catcher = _ExceptionCatcher()
    catcher.install()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            if obj.inherits("QAction"):
                obj.trigger()
            elif obj.isCheckable():
                obj.click()
            else:
                obj.click()
        except Exception:  # noqa: BLE001
            row["exception"] = traceback.format_exc()[-600:]
        # 点完立刻采一次：异步按钮（如「刷新过境」）的「更新中…」会在槽里
        # 同步写上，但后台线程可能在同一轮事件循环里就把它撤掉；只靠
        # processEvents 之后的快照会漏掉这段真实可见的中间态。
        instant = _snapshot(win)
        QApplication.processEvents()
        QApplication.processEvents()
    catcher.restore()

    after = _snapshot(win)
    # 观测完再还原勾选态：setChecked 走 toggled 槽把配置写回旧值（与用户
    # 手动点回去同一条路径），不额外引入「审计专用」复位逻辑。
    if checkable_before is not None:
        try:
            if obj.isChecked() != checkable_before:
                obj.setChecked(checkable_before)
                QApplication.processEvents()
        except RuntimeError:
            pass        # 点击已销毁该控件（关面板/退出），无从还原
    changed = _diff(before, after, exclude=volatile | set(volatile_global),
                    before_widgets=before_widgets)
    if not changed:
        changed = _diff(before, instant, exclude=volatile | set(volatile_global),
                        before_widgets=before_widgets)
    for d in win.findChildren(QDialog):
        if d.isVisible() and id(d) not in visible_before:
            evidence["dialog"].append(d.windowTitle() or type(d).__name__)
    # 新出现的可见顶层窗口（QFrame 浮层面板 / detach 后的浮窗 / 菜单）。
    # 只认「点击后才可见」的，按 id 差集判定；提示气泡 QTipLabel 是
    # 悬停噪音，不记。
    for w in QApplication.topLevelWidgets():
        if w is win or not w.isVisible() or id(w) in tops_before:
            continue
        cls = type(w).__name__
        if cls in ("QTipLabel", "QComboBoxPrivateContainer"):
            continue
        try:
            from PySide6.QtWidgets import QMenu
            label = (w.title() if isinstance(w, QMenu)
                     else (w.windowTitle() or w.objectName() or cls))
        except Exception:  # noqa: BLE001 - 取标题失败不影响「有窗口弹出」这个事实
            label = cls
        prefix = "菜单：" if cls == "QMenu" else ""
        text = f"{prefix}{label or cls}"
        if text not in evidence["new_top"]:
            evidence["new_top"].append(text)
    obs = []
    if evidence["stat"]:
        obs.append("状态栏：" + str(evidence["stat"][0])[:80])
    if evidence["close"]:
        obs.append("窗口关闭")
    if evidence["msg"]:
        obs.append("弹窗：" + str(evidence["msg"][0])[:80])
    if evidence["dialog"]:
        obs.append("对话框：" + "、".join(map(str, evidence["dialog"]))[:80])
    if evidence["file"]:
        obs.append("文件框：" + "、".join(evidence["file"]))
    if evidence["new_top"]:
        obs.append("新窗口：" + "、".join(evidence["new_top"])[:80])
    if changed:
        obs.append("界面变化：" + "；".join(changed))

    stderr_text = err.getvalue()
    if "Traceback" in stderr_text:
        # Qt 把槽异常打到 stderr（默认 excepthook）时也能被这里兜住
        row.setdefault("exception", stderr_text.strip()[-600:])
    if catcher.errors:
        row["exception"] = catcher.errors[-1]
    row["emitted"] = counts
    row["obs"].extend(obs)
    return row


# ---------------------------------------------------------------------------
# 审计主流程
# ---------------------------------------------------------------------------
def _install_dialog_stubs(evidence):
    """模态框/文件框一律不真开（offscreen 下 exec 会挂死）。

    注意：`QMenu.exec(QPoint)` 是 Shiboken 重载，**给 QMenu.exec 赋 Python
    函数拦不住它**（2026-11-09 faulthandler 实测）。菜单挂起只能从产品侧
    改用非模态 `popup()` 解决，别在这里留一个看似生效的假桩。
    """
    from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox

    def _msg(kind):
        def f(parent, title, text, *a, **k):
            evidence["msg"].append(f"{kind}「{title}」{text}")
            return QMessageBox.Ok
        return staticmethod(f)

    old = {}
    for name, kind in (("information", "提示"), ("warning", "警告"),
                       ("critical", "错误"), ("question", "询问"),
                       ("about", "关于")):
        old[("msg", name)] = getattr(QMessageBox, name)
        setattr(QMessageBox, name, _msg(kind))
    old["exec"] = QDialog.exec
    QDialog.exec = lambda self: (evidence["dialog"].append(self.windowTitle()),
                                 QDialog.Rejected)[1]
    old["open"] = QFileDialog.getOpenFileName
    QFileDialog.getOpenFileName = staticmethod(
        lambda *a, **k: (evidence["file"].append("打开"), ("", ""))[1])
    old["save"] = QFileDialog.getSaveFileName
    QFileDialog.getSaveFileName = staticmethod(
        lambda *a, **k: (evidence["file"].append("保存"), ("", ""))[1])
    # 选目录同样必须打桩：offscreen 下 getExistingDirectory 会进原生模态循环
    # 永不返回（2026-11-05 实测：test_no_false_alive_and_no_swallowed_exceptions
    # 卡死 19 分钟零输出，faulthandler 栈尖在 settings_panel._browse_record_dir）。
    old["dir"] = QFileDialog.getExistingDirectory
    QFileDialog.getExistingDirectory = staticmethod(
        lambda *a, **k: (evidence["file"].append("选目录"), "")[1])
    old["open_many"] = QFileDialog.getOpenFileNames
    QFileDialog.getOpenFileNames = staticmethod(
        lambda *a, **k: (evidence["file"].append("打开"), ([], ""))[1])
    old["get_text"] = QInputDialog.getText
    QInputDialog.getText = staticmethod(
        lambda *a, **k: (evidence["file"].append("输入"), ("", False))[1])
    old["get_double"] = QInputDialog.getDouble
    QInputDialog.getDouble = staticmethod(
        lambda *a, **k: (evidence["file"].append("输入"), (0.0, False))[1])
    old["get_item"] = QInputDialog.getItem
    QInputDialog.getItem = staticmethod(
        lambda *a, **k: (evidence["file"].append("输入"), ("", False))[1])
    return old


def _restore_dialog_stubs(old):
    from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox

    for name in ("information", "warning", "critical", "question", "about"):
        setattr(QMessageBox, name, old[("msg", name)])
    QDialog.exec = old["exec"]
    QFileDialog.getOpenFileName = old["open"]
    QFileDialog.getSaveFileName = old["save"]
    QFileDialog.getExistingDirectory = old["dir"]
    QFileDialog.getOpenFileNames = old["open_many"]
    QInputDialog.getText = old["get_text"]
    QInputDialog.getDouble = old["get_double"]
    QInputDialog.getItem = old["get_item"]


def _reason(row) -> str:
    name_key = f"{row['kind']}|{row['name']}|*"
    loc_key = f"{row['kind']}|{row['name']}|{row['panel']}"
    if loc_key in DISABLED_REASONS:
        return DISABLED_REASONS[loc_key]
    if name_key in DISABLED_REASONS:
        return DISABLED_REASONS[name_key]
    tip = getattr(row["obj"], "toolTip", lambda: "")()
    if tip and row["kind"] == "按钮":
        return f"控件提示：{tip}"
    return ""


def _input_reason(row) -> str:
    key = f"{row['name']}|{str(row['panel']).replace('对话框:', '')}"
    return INPUT_CONTROLS.get(key, "")


def audit(win) -> list[dict]:
    from PySide6.QtWidgets import QDialog

    evidence = {"msg": [], "dialog": [], "file": [], "stat": [], "close": [],
                "new_top": [], "exc": []}
    old_stubs = _install_dialog_stubs(evidence)

    orig_show = win.statusBar().showMessage
    win.statusBar().showMessage = lambda msg, timeout=0: (
        evidence["stat"].append(str(msg)), orig_show(msg, timeout))[0]
    orig_close = win.close
    win.close = lambda *a, **k: evidence["close"].append(1)

    rows = collect_controls(win)
    # 只审计本次运行前就存在的对话框；探测中新建的对话框单独走第二阶段，
    # 免得它们混进主清单造成"幽灵按钮"。
    initial_dialogs = {id(d) for d in win.findChildren(QDialog)}
    volatile = _detect_volatile(win)
    _probe_rows(win, rows, evidence, volatile)
    rows.extend(_audit_runtime_dialogs(win, initial_dialogs, evidence, volatile))

    win.close = orig_close
    win.statusBar().showMessage = orig_show
    _restore_dialog_stubs(old_stubs)
    return rows


def _init_row(row) -> None:
    row.setdefault("obs", [])
    row.setdefault("exception", "")
    row.setdefault("emitted", {})
    row["skipped"] = ""
    row["switched"] = []
    row["pre"] = []        # 审计前置动作（切页签/开 dock），不算"按钮有效果"
    row["enabled_initial"] = row["enabled"]
    row["visible_initial"] = row["visible"]


def _probe_rows(win, rows, evidence, volatile=()) -> None:
    """逐行真触发一次（enabled/visible 以触发当时的状态为准）。"""
    from PySide6.QtWidgets import QApplication

    for row in rows:
        _init_row(row)
        # 前面的动作可能整块重建/销毁面板（实测 _SectionHeader 的 C++ 端
        # 在探测到一半时已释放）。清单是启动时的快照，这类行不是死按钮，
        # 是快照与实时 UI 的正常竞态；如实标注后跳过，别让审计自己崩掉。
        try:
            row["obj"].parent()
        except RuntimeError as exc:
            row["destroyed"] = str(exc)
            continue

        if not row["wired"]:
            input_note = _input_reason(row)
            if input_note:
                row["obs"] = [input_note]
            continue

        row["switched"] = _reveal(row["obj"])
        # 前面的动作可能改变了全局状态（例如连接设备后解锁控制面板），
        # 所以 enabled/visible 取"点击当下"的真实值，不拿启动快照冒充。
        row["enabled"] = bool(row["obj"].isEnabled())
        row["visible"] = _is_visible(row["obj"])
        row["pre"] = list(row["switched"])
        if not row["enabled"]:
            reason = _reason(row)
            if reason:
                row["reason"] = reason
            continue

        key = f"{row['kind']}|{row['name']}|*"
        loc_key = f"{row['kind']}|{row['name']}|{row['panel']}"
        skip = SKIP_LIVE.get(loc_key) or SKIP_LIVE.get(key)
        if skip:
            row["skipped"] = skip
            continue
        if PERTURB_BEFORE.get(loc_key) or PERTURB_BEFORE.get(key):
            desc = _perturb_container(row["obj"])
            if desc:
                row["pre"].append(desc)     # 前置动作，不算"按钮有效果"
                QApplication.processEvents()
        print(f"[probe] {row['kind']} {row['name']} @ {row['panel']}",
              flush=True)
        _probe(win, row, evidence, volatile)
        # 留在 IDE 页，避免后续动作在星象仪页上被探测
        stack = getattr(win, "_mode_stack", None)
        if stack is not None and stack.count() > 0 and stack.currentIndex() != 0:
            stack.setCurrentIndex(0)
            QApplication.processEvents()


def _audit_runtime_dialogs(win, initial_ids, evidence, volatile=()) -> list[dict]:
    """第二阶段：入口动作触发后新建的对话框，其按钮也要逐个点一遍。

    对话框在审计里不是 exec 出来的（exec 被打桩立即 Rejected），对象仍在，
    可以 show() 后点 OK/Cancel/刷新等，验证它们真的会产生可观察结果。
    """
    from PySide6.QtWidgets import QAbstractButton, QDialog

    out = []
    seen: set[tuple[str, str]] = set()
    for dlg in win.findChildren(QDialog):
        if id(dlg) in initial_ids:
            continue
        title = dlg.windowTitle() or type(dlg).__name__
        for btn in dlg.findChildren(QAbstractButton):
            if btn.objectName() in INTERNAL_BUTTONS:
                continue
            if not btn.text() and not btn.toolTip():
                continue
            key = (title, btn.text() or btn.toolTip())
            if key in seen:
                # 同一入口被多个动作触发会造出多个同类对话框实例；
                # 按「标题+控件名」去重，只审计一份（同类实例同码同行为）。
                continue
            seen.add(key)
            out.append({
                "kind": "按钮",
                "name": btn.text() or btn.toolTip(),
                "loc": _location(btn),
                "obj": btn,
                "wired": _wired(btn),
                "note": "",
                "enabled": bool(btn.isEnabled()),
                "visible": _is_visible(btn),
                "panel": f"对话框:{title}",
                "runtime_dialog": True,
            })
    # 对话框按钮较少，直接在同一轮探测；对话框每次点击前由 _reveal 打开
    _probe_rows(win, out, evidence, volatile)
    return out


def classify(rows) -> dict:
    dead, false_alive, exceptions, unreasoned = [], [], [], []
    for row in rows:
        if row.get("destroyed"):
            row["conclusion"] = "探测前已被销毁（前面动作重建面板），不计入"
            continue
        row["conclusion"] = ""
        if row["exception"]:
            row["conclusion"] = "抛异常"
            exceptions.append(row)
            continue
        if not row["wired"]:
            if _input_reason(row):
                row["conclusion"] = "输入控件（无独立槽，值被下游读取）"
            else:
                row["conclusion"] = "真死（receivers()=0）"
                dead.append(row)
            continue
        if not row["enabled"]:
            if not row.get("reason"):
                row["reason"] = _reason(row)
            if row.get("reason"):
                row["conclusion"] = "诚实禁用"
            else:
                row["conclusion"] = "诚实禁用（理由未登记）"
                unreasoned.append(row)
            continue
        if row["skipped"]:
            row["conclusion"] = "未实时触发（代码核对）"
            continue
        if row["obs"]:
            row["conclusion"] = "可用"
            continue
        if not row.get("visible_initial", True):
            # 判定必须拿「启动形态」的可见性：_reveal 会把控件所在页签/堆栈
            # 人为切出来，触发当下 row["visible"] 几乎恒为 True，用它等于
            # 没判（2026-11-11 审计收紧后 4 条星象仪按钮被误记假活）。
            # 用户当下点不到的控件，程序化 click 属测量假阳性，不计假活。
            # 真死/抛异常/禁用理由三条不受影响（它们在前面已判）。
            row["conclusion"] = ("当前形态不可见（浮层/折叠页/它形态），"
                                 "用户点不到，不计假活")
            continue
        fired = sum(row.get("emitted", {}).values())
        row["obs"].append(
            f"主信号已触发 {fired} 次；状态栏/弹窗/窗口/控件状态均无变化")
        key = f"{row['kind']}|{row['name']}|{row['panel']}"
        note = FALSE_ALIVE_WHITELIST.get(key)
        if note:
            row["conclusion"] = "假活（已登记未修）"
            row["whitelist"] = note
        else:
            row["conclusion"] = "假活"
        false_alive.append(row)
    return {"dead": dead, "false_alive": false_alive,
            "exceptions": exceptions, "unreasoned": unreasoned}


def _esc(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", "<br>")


def render(rows, problems) -> str:
    total = len(rows)
    actions = sum(1 for r in rows if r["kind"] == "菜单项")
    buttons = total - actions
    enabled = sum(1 for r in rows if r["enabled"])
    visible = sum(1 for r in rows if r["visible"])
    counts = {}
    for r in rows:
        counts[r["conclusion"]] = counts.get(r["conclusion"], 0) + 1

    lines = [
        "# 交互覆盖审计（死按钮 / 假按钮 / 静默异常）",
        "",
        "> 由 `scripts/ui_interaction_audit.py` 运行时遍历生成，请勿手改。",
        "> 环境：offscreen + `MBDSDR_NULL_AUDIO=1` + `MBDSDR_NO_HW=1` + "
        "`MBDSDR_OFFLINE=1`，HOME/QSettings 隔离到临时目录。",
        "> 口径：`receivers()==0` = 真死；有接收者但触发后无状态/信号下游/"
        "窗口/弹窗变化 = 假活；触发抛出或被 Qt 吞掉的异常单列。",
        "> 防误报：触发前先空跑事件循环，把 UTC 时钟/SNR 读数等自变字段"
        "识别为 volatile 并从 diff 里剔除；运行时新建的同类对话框按"
        "「标题+控件名」去重。",
        "",
        "## 结论",
        "",
        f"- 遍历动作 **{total}** 个（QAction {actions} / QAbstractButton "
        f"{buttons}；enabled {enabled} / visible {visible}）",
        f"- **真死** {len(problems['dead'])} 个",
        f"- **假活** {len(problems['false_alive'])} 个"
        + (f"（其中 {sum(1 for r in problems['false_alive'] if r.get('whitelist'))} "
           "个已登记未修）" if problems["false_alive"] else ""),
        f"- **抛异常** {len(problems['exceptions'])} 个",
        f"- 诚实禁用 {counts.get('诚实禁用', 0)} 个；"
        f"未实时触发 {counts.get('未实时触发（代码核对）', 0)} 个；"
        f"输入控件 {counts.get('输入控件（无独立槽，值被下游读取）', 0)} 个",
        "",
    ]

    def _table(items):
        out = ["| 动作 | 类型 | 所在面板 | 位置 | 证据 | 结论 |",
               "| --- | --- | --- | --- | --- | --- |"]
        for r in items:
            ev = "；".join(r["obs"]) if r["obs"] else "—"
            if r.get("exception"):
                ev = "异常：" + _esc(r["exception"].strip().splitlines()[-1])
            elif r.get("skipped"):
                ev = "未实时触发：" + _esc(r["skipped"])
            if r.get("whitelist"):
                ev += "；登记：" + _esc(r["whitelist"])
            out.append(f"| {_esc(r['name'])} | {r['kind']} | {_esc(r['panel'])} | "
                       f"{_esc(r['loc'])} | {_esc(ev)} | {_esc(r['conclusion'])} |")
        return out

    lines += ["## 一、真死（receivers() == 0，点了不可能有反应）", ""]
    lines += _table(problems["dead"]) if problems["dead"] else ["无。"]
    lines += ["", "## 二、假活（有接收者，但触发后无可观察变化）", ""]
    lines += _table(problems["false_alive"]) if problems["false_alive"] else ["无。"]
    lines += ["", "## 三、抛异常（同步抛出 / 被 Qt 吞掉只打到 stderr）", ""]
    lines += _table(problems["exceptions"]) if problems["exceptions"] else ["无。"]

    lines += ["", "## 四、已登记的假活白名单（幂等/状态相关，待他线改）", "",
              "> 本轮复现情况随设备枚举结果变化；白名单里的每一条都要有理由，",
              "> pytest 守卫也读这份表（`FALSE_ALIVE_WHITELIST`）。", "",
              "| 动作 | 理由 |", "| --- | --- |"]
    for key, why in sorted(FALSE_ALIVE_WHITELIST.items()):
        lines.append(f"| {_esc(key)} | {_esc(why)} |")

    lines += ["", "## 五、诚实禁用但没写为什么", ""]
    lines += _table(problems["unreasoned"]) if problems["unreasoned"] else ["无。"]

    lines += ["", "## 附：全量明细", ""]
    lines += ["| 动作 | 类型 | 所在面板 | 启用 | 可见 | 有槽 | 可观察结果 | 结论 |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in sorted(rows, key=lambda x: (x["kind"] != "菜单项", x["panel"], x["name"])):
        obs = "；".join(r["obs"]) if r["obs"] else "—"
        if r.get("exception"):
            obs = "异常：" + _esc(r["exception"].strip().splitlines()[-1])
        if r.get("skipped"):
            obs = "未实时触发：" + _esc(r["skipped"])
        if r.get("whitelist"):
            obs += "；登记：" + _esc(r["whitelist"])
        if r.get("pre"):
            obs = "前置：" + _esc("；".join(r["pre"])) + "；" + obs
        lines.append(
            f"| {_esc(r['name'])} | {r['kind']} | {_esc(r['panel'])} | "
            f"{'是' if r['enabled'] else '否'} | {'是' if r['visible'] else '否'} | "
            f"{'是' if r['wired'] else '**否**'} | {_esc(obs)} | "
            f"{_esc(r['conclusion'])} |")

    lines += [
        "",
        "## 未覆盖项（如实列出，不计入通过）",
        "",
        "- 右键菜单（频谱「删除此 VFO」）在交互时才构造；已源码核对：菜单项"
        "选中后调用 `_delete_vfo`，属可用。",
        "- 工具栏 `QWidgetAction` 是控件容器，本身不可点，不计入动作数。",
        "- Qt 自带的页签滚动/工具栏折叠按钮不是业务动作，已剔除。",
        "- `SKIP_LIVE` 里的动作只做代码核对，未实时触发（起线程/联网/写用户数据）。",
        "",
    ]
    return "\n".join(lines)


_AUDIT_ROOT: Optional[str] = None


def setup_isolated_environment() -> str:
    """HOME + QSettings 隔离到**每次新建**的临时目录。

    不能复用固定目录：上一轮留下的 gui_config/bookmarks 会让本轮结果漂移
    （设备下拉框初始内容不同，探测结论就不同）。
    """
    global _AUDIT_ROOT
    if _AUDIT_ROOT is None:
        _AUDIT_ROOT = tempfile.mkdtemp(prefix="mbdsdr_audit_")
    home = os.path.join(_AUDIT_ROOT, "home")
    os.makedirs(home, exist_ok=True)
    os.environ["USERPROFILE"] = home
    os.environ["HOME"] = home
    # DesktopSettings.default_config_path() 在 Windows 上走 %APPDATA%：
    # 不重定向的话，审计点一次设置面板按钮就会把真实用户配置写脏
    # （tests/test_ui_settings_isolation_guard.py 正是守这条）。
    for var in ("APPDATA", "LOCALAPPDATA"):
        os.environ[var] = home
    return home


def build_main_window():
    """构造真实主窗口（停机启动定时器，断开真硬件）。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope,
                      os.path.join(_AUDIT_ROOT or tempfile.gettempdir(),
                                   "settings"))
    sys.path.insert(0, _ROOT)
    sys.path.insert(0, os.path.join(_ROOT, "desktop"))
    from main_window import MainWindow

    win = MainWindow()
    win.show()
    for timer in getattr(win, "_startup_timers", []):
        try:
            timer.stop()       # 不自动枚举/连接真硬件
        except Exception as exc:  # noqa: BLE001 - 停启动定时器失败不阻断审计，但要留证
            log.warning("停启动定时器失败：%s: %s",
                        type(exc).__name__, exc)
    win._panels_set_sdr_connected(False)
    app.processEvents()
    return app, win


def run() -> tuple[list[dict], dict]:
    setup_isolated_environment()
    app, win = build_main_window()
    try:
        rows = audit(win)
        problems = classify(rows)
    finally:
        try:
            win._disconnect()
        except Exception as exc:  # noqa: BLE001 - 收尾失败必须留证，不静默
            log.warning("审计收尾失败（MainWindow._disconnect）：%s: %s",
                        type(exc).__name__, exc)
    return rows, problems


def main() -> int:
    rows, problems = run()
    text = render(rows, problems)
    os.makedirs(os.path.dirname(_DOC), exist_ok=True)
    with open(_DOC, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[interaction-audit] 动作 {len(rows)} 个；真死 "
          f"{len(problems['dead'])}；假活 {len(problems['false_alive'])}；"
          f"抛异常 {len(problems['exceptions'])}；"
          f"置灰无理由 {len(problems['unreasoned'])} -> {_DOC}")

    rc = 0
    for row in problems["dead"]:
        print(f"  DEAD  {row['kind']} {row['name']} @ {row['panel']}")
        rc = 1
    for row in problems["false_alive"]:
        if not row.get("whitelist"):
            print(f"  FALSE-ALIVE {row['kind']} {row['name']} @ {row['panel']}")
            rc = 1
    for row in problems["exceptions"]:
        print(f"  EXCEPTION {row['kind']} {row['name']} @ {row['panel']}")
        rc = 1
    for row in problems["unreasoned"]:
        print(f"  NO-REASON {row['kind']} {row['name']} @ {row['panel']}")
        rc = 1
    return rc


if __name__ == "__main__":
    # 审计会临时打桩 Qt 静态方法并造出很多短命对话框；解释器退出时
    # offscreen 平台偶发 Qt 析构崩溃。结果文件已落盘，这里直接硬退出。
    rc = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc)
