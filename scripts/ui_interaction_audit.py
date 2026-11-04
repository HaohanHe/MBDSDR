#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
交互覆盖审计（死按钮）
======================

运行时遍历主窗口里的 QAction / QAbstractButton（不是手写清单），逐项检查：

  1. 有没有接槽：QAction.triggered(bool) / QAbstractButton 的 clicked/toggled/
     pressed 至少一个信号有接收者（``QObject.receivers("2<sig>")``）；
  2. 触发后是否有可观测结果：状态栏文案、弹窗、对话框、文件选择框、或
     可见 UI 状态（文本/勾选/页签/使能）发生变化；
  3. 不可用时是否诚实：置灰项必须能给出"为什么不能"（控件提示 / 面板状态
     文案 / 下方 DISABLED_REASONS 登记）。

结论三选一：可用 / 诚实禁用 / 死按钮。死按钮必须为零，否则本脚本退出 1。

产物：docs/interaction-audit.md（一行一个动作）。

运行（必须 offscreen + 无硬件环境，避免真机/真录音干扰）::

    QT_QPA_PLATFORM=offscreen MBDSDR_NULL_AUDIO=1 MBDSDR_NO_HW=1 \\
        python scripts/ui_interaction_audit.py

退出码：0=无死按钮；1=存在未解释的死按钮或未登记理由的禁用项。
"""
from __future__ import annotations

import contextlib
import io
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOC = os.path.join(_ROOT, "docs", "interaction-audit.md")

# ---------------------------------------------------------------------------
# 置灰理由登记：键 = "种类|名称|父容器类名"，值 = 为什么此刻不能用。
# 审计脚本发现置灰项没登记理由就退出 1（新增按钮必须顺手写清楚）。
# ---------------------------------------------------------------------------
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
    "按钮|断开|NtripConfigDialog": "NTRIP 配置对话框内尚未建立连接",
    "菜单项|工具(&T) / FM 扫频找台|*": "未连接 SDR（FM 扫频需要可调谐的本地接收机）",
}

#: 允许"点击无效果"的上下文例外（按钮只在某个形态/状态下才有意义）。
CONTEXT_EXEMPT: dict[str, str] = {
    "按钮|Cancel|*": "所在对话框仅在连接/配置时弹出；Cancel 走 QDialog.reject，"
                     "关闭对话框且不改任何配置",
}

#: 不在本审计里实时点击的动作：会启动后台线程/阻塞网络/写用户数据。
#: 键支持 "种类|名称|*"（按名称）或 "种类|名称|位置"（精确）；值 = 代码核对
#: 到的真实可观测结果（不是"大概能用"）。
SKIP_LIVE: dict[str, str] = {
    "按钮|发送|*": "启动 AI 推理线程；按钮立即变「思考中…」，回复/失败写回对话区",
    "按钮|找一个信号强的 FM 电台|*": "同「发送」：把问题填进对话框并进入思考态",
    "按钮|扫频 87-108 MHz 找所有电台|*": "同「发送」：把问题填进对话框并进入思考态",
    "按钮|调谐到 98.5 MHz 并录 30 秒|*": "同「发送」：把问题填进对话框并进入思考态",
    "按钮|开始跟踪|*": "按秒轮询硬件指向；无硬件时由跟踪面板写「未连接」状态",
    "按钮|开始扫描|*": "启动扫频线程；无后端时由面板写状态文案",
    "按钮|开始录音|*": "启动录音线程并落盘到 ~/mbdsdr_recordings，失败写状态栏",
    "按钮|拉取挂载点列表|*": "发起 NTRIP 网络请求，结果写面板状态标签",
}

INTERNAL_BUTTONS = {
    # Qt 自带的滚动/折叠/表角按钮，不是业务动作
    "ScrollLeftButton", "ScrollRightButton", "qt_menubar_ext_button",
    "qt_toolbar_ext_button", "qt_tableview_cornerbutton",
}


def _receivers(obj, sig: str) -> int:
    try:
        return int(obj.receivers(sig))
    except Exception:  # noqa: BLE001
        return -1


def _parent_label(obj) -> str:
    """父容器类名（面板级），用于位置列与理由登记键。"""
    p = obj.parent()
    while p is not None:
        if p.objectName() or type(p).__name__.endswith("Panel"):
            return p.objectName() or type(p).__name__
        p = p.parent()
    return "-"


def _location(obj) -> str:
    parts = []
    p = obj.parent()
    while p is not None and len(parts) < 4:
        name = p.objectName() or type(p).__name__
        if name not in ("qt_scrollarea_viewport", "qt_tabwidget_stackedwidget",
                        "QWidget"):
            parts.append(name)
        p = p.parent()
    return " / ".join(reversed(parts)) or "-"


def _wired(obj) -> bool:
    if obj.inherits("QAction"):
        return _receivers(obj, "2triggered(bool)") > 0
    for sig in ("2clicked(bool)", "2toggled(bool)", "2pressed()"):
        if _receivers(obj, sig) > 0:
            return True
    return False


def _walk_menu(menu, prefix=""):
    for act in menu.actions():
        if act.isSeparator():
            continue
        if act.menu() is not None:
            yield from _walk_menu(act.menu(), f"{prefix}{act.text()} / ")
        elif act.text():
            yield f"{prefix}{act.text()}", act


def iter_controls(win):
    """产出 (种类, 名称, 位置, 对象, 是否有槽, 备注)。"""
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import (QAbstractButton, QCheckBox, QDialogButtonBox,
                                   QRadioButton, QWidgetAction)

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
        wired, note = _wired(btn), ""
        if not wired and isinstance(btn, (QCheckBox, QRadioButton)):
            # 输入控件：没有独立槽，值由所在对话框的"确认"流程读取
            wired = True
            note = "输入控件：勾选值在对话框确认时读取"
        elif not wired and isinstance(btn.parent(), QDialogButtonBox):
            # Qt 标准按钮盒：accept/reject 由 C++ 内部接线
            wired = True
            note = "Qt 标准按钮盒：accept/reject 语义"
        yield ("按钮", btn.text() or btn.toolTip(), _location(btn), btn,
               wired, note)


def _fingerprint(win) -> str:
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import (QAbstractButton, QComboBox, QDialog,
                                   QDockWidget, QLabel, QLineEdit, QStackedWidget,
                                   QTabWidget, QWidget)
    out = []
    for w in win.findChildren(QWidget):
        if isinstance(w, QLabel):
            out.append(f"L{w.objectName()}:{w.text()}")
        elif isinstance(w, QAbstractButton):
            out.append(f"B{w.objectName()}:{w.text()}:{int(w.isChecked())}:"
                       f"{int(w.isEnabled())}:{int(w.isVisible())}")
        elif isinstance(w, QLineEdit):
            out.append(f"E{w.objectName()}:{w.text()}")
        elif isinstance(w, QComboBox):
            out.append(f"C{w.objectName()}:{w.currentIndex()}:{w.currentText()}")
        elif isinstance(w, QTabWidget):
            out.append(f"T{w.objectName()}:{w.currentIndex()}")
        elif isinstance(w, QStackedWidget):
            out.append(f"P{w.objectName()}:{w.currentIndex()}")
        elif isinstance(w, QDockWidget):
            out.append(f"D{w.objectName()}:{int(w.isVisible())}")
        elif isinstance(w, QDialog):
            out.append(f"G{w.windowTitle()}:{int(w.isVisible())}")
    for a in win.findChildren(QAction):
        if a.text() and not a.isSeparator():
            out.append(f"A{a.text()}:{int(a.isChecked())}:{int(a.isEnabled())}")
    out.append(f"S:{win.statusBar().currentMessage()}")
    return "\n".join(out)


def _reveal(obj) -> None:
    """把动作所在的页签/堆栈页切到前台（否则可见性判断会让真实动作"看起来没反应"）。"""
    from PySide6.QtWidgets import QApplication, QStackedWidget, QTabWidget
    p = obj.parent()
    while p is not None:
        if isinstance(p, QTabWidget):
            for i in range(p.count()):
                if p.widget(i) is not None and p.widget(i).isAncestorOf(obj):
                    p.setCurrentIndex(i)
                    break
        elif isinstance(p, QStackedWidget):
            if p.isAncestorOf(obj):
                for i in range(p.count()):
                    if p.widget(i).isAncestorOf(obj):
                        p.setCurrentIndex(i)
                        break
        p = p.parent()
    QApplication.processEvents()


def _restore_mode(win) -> None:
    """把顶层形态栈切回 IDE 页（避免后续动作在星象仪页上被探测）。"""
    stack = getattr(win, "_mode_stack", None)
    if stack is not None and stack.count() > 0 and stack.currentIndex() != 0:
        stack.setCurrentIndex(0)


def _edge_key(row) -> str:
    return f"{row['name']}|{row['loc'].split(' / ')[-1]}"


def probe(win, obj, evidence: dict) -> tuple[list[str], bool, str]:
    """触发一次，收集可观测证据；返回 (证据列表, 是否异常, stderr)。"""
    before = _fingerprint(win)
    evidence["msg"].clear()
    evidence["dialog"].clear()
    evidence["file"].clear()
    evidence["quit"].clear()
    evidence["stat"].clear()
    evidence["close"].clear()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            if obj.inherits("QAction"):
                obj.trigger()
            elif obj.isCheckable():
                obj.toggle()
            else:
                obj.click()
        except Exception as exc:  # noqa: BLE001
            evidence["exc"].append(repr(exc))
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    after = _fingerprint(win)
    obs = []
    if evidence["stat"]:
        obs.append("状态栏：" + evidence["stat"][0][:70])
    if evidence["close"]:
        obs.append("窗口关闭")
    if evidence["quit"]:
        obs.append("退出应用")
    if evidence["msg"]:
        obs.append("弹窗：" + evidence["msg"][0][:70])
    if evidence["dialog"]:
        obs.append("对话框：" + "、".join(str(d) for d in evidence["dialog"])[:70])
    if evidence["file"]:
        obs.append("文件选择：" + "、".join(evidence["file"]))
    if before != after and not obs:
        obs.append("界面状态变化")
    exc = ""
    txt = err.getvalue()
    if "Traceback" in txt:
        exc = txt.strip().splitlines()[-1][:100]
    return obs, bool(exc), exc


def audit(win) -> list[dict]:
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox

    evidence = {"msg": [], "dialog": [], "file": [], "quit": [],
                "stat": [], "close": [], "exc": []}

    # ---- 打桩：模态框/文件框一律不真开（offscreen 下 exec 会挂死） ----
    def _msg(kind):
        def f(parent, title, text, *a, **k):
            evidence["msg"].append(f"{kind}「{title}」{text}")
            return QMessageBox.Ok
        return staticmethod(f)

    for name, kind in (("information", "提示"), ("warning", "警告"),
                       ("critical", "错误"), ("question", "询问"),
                       ("about", "关于")):
        setattr(QMessageBox, name, _msg(kind))
    orig_exec = QDialog.exec
    QDialog.exec = lambda self: (evidence["dialog"].append(self.windowTitle()),
                                 QDialog.Rejected)[1]
    orig_open = QFileDialog.getOpenFileName
    QFileDialog.getOpenFileName = staticmethod(
        lambda *a, **k: (evidence["file"].append("打开"), ("", ""))[1])
    orig_save = QFileDialog.getSaveFileName
    QFileDialog.getSaveFileName = staticmethod(
        lambda *a, **k: (evidence["file"].append("保存"), ("", ""))[1])
    orig_input = QInputDialog.getText
    QInputDialog.getText = staticmethod(
        lambda *a, **k: (evidence["file"].append("输入"), ("", False))[1])
    QInputDialog.getDouble = staticmethod(
        lambda *a, **k: (evidence["file"].append("输入"), (0.0, False))[1])
    QInputDialog.getItem = staticmethod(
        lambda *a, **k: (evidence["file"].append("输入"), ("", False))[1])

    orig_show = win.statusBar().showMessage
    win.statusBar().showMessage = lambda msg, timeout=0: (
        evidence["stat"].append(str(msg)), orig_show(msg, timeout))[0]
    orig_quit = win.close
    win.close = lambda *a, **k: evidence["close"].append(1)

    rows = []
    for kind, name, loc, obj, wired, note in iter_controls(win):
        enabled = obj.isEnabled()
        tip = obj.toolTip() if hasattr(obj, "toolTip") else ""
        row = dict(kind=kind, name=name, loc=loc, wired=wired,
                   enabled=enabled, tip=tip, obs=[], exc="", note=note)
        if enabled and note:
            row["obs"] = [note]
            row["skipped"] = True
            rows.append(row)
            continue
        if enabled:
            key = f"{kind}|{_edge_key(row)}"
            note = SKIP_LIVE.get(key) or SKIP_LIVE.get(f"{kind}|{name}|*")
            if note:
                row["obs"] = ["未实时触发：" + note]
                row["skipped"] = True
                rows.append(row)
                continue
            print(f"[probe] {kind} {name}", flush=True)
            _reveal(obj)
            obs, exc, exc_text = probe(win, obj, evidence)
            row["obs"], row["exc"] = obs, exc_text
            _restore_mode(win)
        rows.append(row)

    win.close = orig_quit
    win.statusBar().showMessage = orig_show
    QDialog.exec = orig_exec
    QFileDialog.getOpenFileName = orig_open
    QFileDialog.getSaveFileName = orig_save
    QInputDialog.getText = orig_input
    return rows


def _reason(row) -> str:
    key = f"{row['kind']}|{_edge_key(row)}"
    name_key = f"{row['kind']}|{row['name']}|*"
    if key in DISABLED_REASONS:
        return DISABLED_REASONS[key]
    if name_key in DISABLED_REASONS:
        return DISABLED_REASONS[name_key]
    if row["tip"] and row["kind"] == "按钮":
        # QAction.toolTip() 在没设提示时会回落到 text，不能拿来当"原因"
        return f"控件提示：{row['tip']}"
    return ""


def classify(rows) -> dict:
    dead, unreasoned = [], []
    for row in rows:
        if not row["wired"]:
            row["conclusion"] = "死按钮（未接槽）"
            dead.append((row, "没有接槽"))
            continue
        if row["enabled"]:
            if row["obs"]:
                row["conclusion"] = ("可用（代码核对）" if row.get("skipped")
                                     else "可用")
                continue
            key = f"{row['kind']}|{_edge_key(row)}"
            name_key = f"{row['kind']}|{row['name']}|*"
            note = CONTEXT_EXEMPT.get(key) or CONTEXT_EXEMPT.get(name_key)
            if note:
                row["conclusion"] = "可用（上下文限定）"
                row["obs"] = [note]
                continue
            dead.append((row, "点击无可观测结果"))
            row["conclusion"] = "死按钮"
            continue
        reason = _reason(row)
        if reason:
            row["conclusion"] = "诚实禁用"
            row["reason"] = reason
        else:
            unreasoned.append(row)
            row["conclusion"] = "诚实禁用（理由未登记）"
    return {"dead": dead, "unreasoned": unreasoned}


def render(rows, problems) -> str:
    total = len(rows)
    enabled = sum(1 for r in rows if r["enabled"])
    disabled = total - enabled
    dead = len(problems["dead"])
    lines = [
        "# 交互覆盖审计（死按钮清单）",
        "",
        "> 本文件由 `scripts/ui_interaction_audit.py` 运行时遍历生成，请勿手改。",
        "> 运行环境：offscreen + `MBDSDR_NULL_AUDIO=1` + `MBDSDR_NO_HW=1` + "
        "`MBDSDR_OFFLINE=1`（无硬件/无网络/无样本）。",
        "> 判定：有槽 = 主信号至少一个接收者；可观测 = 状态栏/弹窗/对话框/"
        "文件框/界面状态变化；置灰必须给出原因。",
        "",
        f"- 遍历动作：**{total}** 个（可点 {enabled} / 置灰 {disabled}）",
        f"- 死按钮（有槽但点了没反应 / 没接槽）：**{dead}**",
        "",
        "| 动作 | 位置 | 有槽 | 可观测结果 | 不可用理由 | 结论 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in sorted(rows, key=lambda x: (x["kind"] != "菜单项", x["loc"], x["name"])):
        obs = "；".join(r["obs"]) if r["obs"] else "—"
        if r["exc"]:
            obs += f"；异常：{r['exc']}"
        reason = r.get("reason") or "—"
        lines.append(
            f"| {r['name']} | {r['loc']} | {'是' if r['wired'] else '**否**'} | "
            f"{obs} | {reason} | {r['conclusion']} |")
    lines += [
        "",
        "## 未覆盖项",
        "",
        "- 右键菜单（频谱「删除此 VFO」）在交互时才构造，无法静态遍历；"
        "已源码核对：菜单项选中后调用 `_delete_vfo`，属可用。",
        "- 工具栏 `QWidgetAction` 是控件容器，本身不可点，不计入动作数。",
        "- Qt 自带的页签滚动/工具栏折叠按钮不是业务动作，已剔除。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    # 用户目录隔离：本脚本会真实触发录音/配置写入，绝不能污染真实 home
    import tempfile
    home = os.path.join(tempfile.gettempdir(), "mbdsdr_interaction_audit_home")
    os.makedirs(home, exist_ok=True)
    os.environ["USERPROFILE"] = home
    os.environ["HOME"] = home

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    sys.path.insert(0, _ROOT)
    sys.path.insert(0, os.path.join(_ROOT, "desktop"))
    from main_window import MainWindow

    win = MainWindow()
    win.show()
    for timer in getattr(win, "_startup_timers", []):
        try:
            timer.stop()       # 不自动枚举/连接真硬件
        except Exception:  # noqa: BLE001
            pass
    # 审计口径固定为"无硬件基线态"
    win._panels_set_sdr_connected(False)
    app.processEvents()

    rows = audit(win)
    problems = classify(rows)
    text = render(rows, problems)
    os.makedirs(os.path.dirname(_DOC), exist_ok=True)
    with open(_DOC, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[interaction-audit] 动作 {len(rows)} 个，死按钮 {len(problems['dead'])} 个，"
          f"置灰无理由 {len(problems['unreasoned'])} 个 -> {_DOC}")

    rc = 0
    for row, why in problems["dead"]:
        print(f"  DEAD {row['kind']} {row['name']} @ {row['loc']}: {why}")
        rc = 1
    for row in problems["unreasoned"]:
        print(f"  NO-REASON {row['kind']} {row['name']} @ {row['loc']}")
        rc = 1
    try:
        win._disconnect()
    except Exception:  # noqa: BLE001
        pass
    win.deleteLater()
    app.processEvents()
    return rc


if __name__ == "__main__":
    # 审计会临时打桩 Qt 静态方法并造出很多短命对话框；解释器退出时
    # offscreen 平台偶发 Qt 析构崩溃（STATUS_STACK_BUFFER_OVERRUN）。
    # 结果文件已落盘，这里直接硬退出，避免把一次成功审计伪装成崩溃。
    rc = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc)
