# SPDX-License-Identifier: MIT
"""状态栏常驻的「审计漏记」告警。

审计账本写不进时是 fail-open（不阻断工具调用），原来只往 stderr 打一行。
桌面端通常没有控制台，那行字等于没人看见——「只告警等于没有」在这里的
具体形态是「审计已经失效，而界面一切正常」。

本模块把 :mod:`mbdsdr_ai.audit_health` 的累计漏记计数摆到状态栏右侧的
常驻标签上（**不是** ``showMessage`` 的瞬时提示，不会被后续状态覆盖）：

* 计数为 0 → 标签隐藏，不占地方；
* 达到阈值（``AUDIT_DROP_VISIBLE_THRESHOLD``，当前是 1）→ 显示
  「审计漏记 N 条 · 最近原因 · 最近时刻」，tooltip 里给按原因分组、
  计数文件路径与 ``python -m mbdsdr_ai.audit_health`` 查询入口；
* 每 ``AUDIT_ALARM_POLL_MS`` 重新读一次健康文件——漏记可能发生在别的
  进程（MCP 工作进程），不能只靠本进程的事件驱动。

界面只呈现数字，不在这里判断「多少条算严重」：阈值由
``audit_health`` 定，改阈值只改一处。
"""

from __future__ import annotations

import logging
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mbdsdr_ai.audit_health import (  # noqa: E402
    audit_drop_snapshot,
    reason_text,
)

log = logging.getLogger(__name__)

#: 轮询间隔：漏记是低频事件，读的又只是一个几百字节的小 JSON。
AUDIT_ALARM_POLL_MS = 15_000


def alarm_text(snapshot: dict) -> str:
    """状态栏文案：累计条数 + 最近一次原因 + 时刻（缺项如实省略）。

    计数文件读不出来时显示「历史漏记状态未知」——**绝不显示 0 条**：
    0 会被读成「审计一直正常」，而真相是「不知道漏了多少」。
    """
    snap = dict(snapshot or {})
    if snap.get("state") == "unknown":
        text = "历史漏记状态未知"
        pending = int(snap.get("dropped") or 0)
        if pending > 0:
            text += f"（本次会话已漏记 {pending} 条）"
        return text
    total = int(snap.get("dropped") or 0)
    text = f"审计漏记 {total} 条"
    reason = str(snap.get("last_reason_text") or "").strip()
    if reason:
        text += f" · {reason}"
    when = str(snap.get("last_iso") or "").strip()
    if when:
        text += f" · {when}"
    return text


def alarm_tooltip(snapshot: dict) -> str:
    """详细的「为什么/在哪查」：按原因分组、计数文件、查询命令。"""
    snap = dict(snapshot or {})
    total = int(snap.get("dropped") or 0)
    if snap.get("state") == "unknown":
        lines = [
            "审计漏记计数文件存在但读不出来（损坏或没有读权限），"
            "无法确认历史累计漏记条数；本次会话内发生的漏记仍会在此显示。",
            f"累计（仅本次会话可确认）：{total} 条",
        ]
    else:
        lines = [
            f"工具审计账本写不进时不会阻断工具调用（fail-open），"
            f"但每次漏记都会计数。累计漏记 {total} 条。",
        ]
        when = str(snap.get("last_iso") or "").strip() or "时刻未知"
        reason = str(snap.get("last_reason_text") or "").strip() or "原因未知"
        detail = str(snap.get("last_detail") or "").strip()
        lines.append(f"最近一次：{when}，{reason}"
                     + (f"（{detail[:200]}）" if detail else ""))
        grouped = dict(snap.get("by_reason") or {})
        if grouped:
            parts = [f"{reason_text(str(r))} {int(n)} 条"
                     for r, n in sorted(grouped.items())]
            lines.append("按原因：" + "；".join(parts))
        ledger = str(snap.get("last_ledger") or "").strip()
        if ledger:
            lines.append(f"最近失败的账本：{ledger}")
    path = str(snap.get("path") or "").strip()
    if path:
        lines.append(f"计数文件：{path}")
    lines.append("查询入口：python -m mbdsdr_ai.audit_health")
    return "\n".join(lines)


def _build_label_class():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel

    class AuditDropAlarm(QLabel):
        """状态栏右侧的审计漏记常驻标签（默认隐藏）。

        只做呈现：数据来自 :func:`mbdsdr_ai.audit_health.audit_drop_snapshot`，
        显隐由 ``visible`` 决定，不在界面里另算阈值。
        """

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self.setObjectName("audit_drop_alarm")
            self.setTextFormat(Qt.PlainText)
            self.setTextInteractionFlags(Qt.TextSelectableByMouse)
            # 纯状态展示，不进 Tab 链；键盘用户仍能通过状态栏读屏拿到文案。
            self.setFocusPolicy(Qt.NoFocus)
            self._snapshot: dict = {}
            self.setStyleSheet(
                "QLabel#audit_drop_alarm {"
                " color: #E8B04B; background: transparent;"
                " border: 1px solid #E8B04B; border-radius: 4px;"
                " padding: 0 6px; font-size: 9pt; }")
            self.setVisible(False)

        def snapshot(self) -> dict:
            return dict(self._snapshot)

        def refresh(self, snapshot=None) -> bool:
            """重新读一次健康文件并刷新显隐；返回当前是否可见。

            读失败不得让状态栏报错或把已有告警清掉（那会变成「故障时反而
            看不见」）。
            """
            if snapshot is None:
                try:
                    snapshot = audit_drop_snapshot()
                except Exception as exc:  # noqa: BLE001 - 告警面板不得破 UI
                    log.debug("审计漏记快照读取失败（保留上次显示）：%s", exc)
                    return self.isVisible()
            return self.apply_snapshot(snapshot)

        def apply_snapshot(self, snapshot) -> bool:
            self._snapshot = dict(snapshot or {})
            if not self._snapshot.get("visible"):
                self.setText("")
                self.setToolTip("")
                self.setVisible(False)
                return False
            self.setText(alarm_text(self._snapshot))
            self.setToolTip(alarm_tooltip(self._snapshot))
            self.setVisible(True)
            return True

    return AuditDropAlarm


def build_audit_alarm(parent=None):
    """构造一个审计漏记告警标签（默认隐藏）。"""
    return _build_label_class()(parent)


def install_audit_alarm(window, *, interval_ms: int = AUDIT_ALARM_POLL_MS,
                        health_path: str = ""):
    """把告警标签挂到主窗状态栏右侧，并起一个轮询定时器。

    返回标签对象（调用方需保活；定时器以标签为父，随标签销毁）。
    窗口没有 ``statusBar()`` 时只返回未挂载的标签，不抛异常——启动路径
    不该因为告警面板而失败。
    """
    from PySide6.QtCore import QTimer

    label = build_audit_alarm(window)
    try:
        bar = window.statusBar()
    except (AttributeError, RuntimeError):
        bar = None
    if bar is not None:
        bar.addPermanentWidget(label)
    if health_path:
        def _refresh() -> None:
            try:
                label.apply_snapshot(audit_drop_snapshot(health_path))
            except Exception as exc:  # noqa: BLE001
                log.debug("审计漏记快照读取失败：%s", exc)
    else:
        _refresh = label.refresh
    timer = QTimer(label)
    timer.setInterval(int(interval_ms))
    timer.timeout.connect(_refresh)
    timer.start()
    label._timer = timer
    _refresh()
    return label


def __getattr__(name: str):
    """PEP 562：只有真的要用 Qt 标签时才构造类（纯数据测试可先不碰 Qt）。"""
    if name == "AuditDropAlarm":
        return _build_label_class()
    raise AttributeError(name)
