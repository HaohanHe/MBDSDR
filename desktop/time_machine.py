# SPDX-License-Identifier: MIT
"""时间机器：把观测时刻跳到任意一天（对齐 Stellarium ``DateTimeDialog``）。

学的是它的**口径**而不是皮：``DateTimeDialog::setDateTime(double newJd)``
把用户输入的本地时刻换算成 JD 交给 ``core->setJD()``，再让整个天空按新
时刻重算；「回到现在」是单独的按钮。本模块照同一条链落地：

* 输入框是**本地时间**（用户脑子里想的是"今晚九点"，不是 UTC）；
* 应用时只交出一个 Unix 秒给主窗，由主窗调 ``new_spacetime.set_unix``
  并立刻刷历表——本模块不碰时间引擎、不碰天空视图，避免两条事实源；
* 「回到现在」用独立 on_now 回调立即恢复实况；无回调时仅填框，明示未应用；
* 状态行如实写「模拟中 · 比现在 +2 天 3 小时」，不把跳过去说成实时。

不做什么：不提供"假日期"（比如永远停在某个好看的夜晚）。跳过去之后
星、月、行星全部按该时刻真实计算，白天看不见星就是看不见星。
"""
from __future__ import annotations

import time
from datetime import datetime
from math import floor
from typing import Callable, Optional, Tuple

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QDateTimeEdit, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

# 输入框是本地时间：系统时区退到冻结偏移时，必须在同一处说明
# 「当前为固定偏移，不随夏令时调整」，否则用户会以为跨夏令时的换算仍然对。
from mbdsdr_ai.time_basis import local_time_note

__all__ = ["TimeMachineDialog", "format_offset",
           "unix_from_qdatetime", "qdatetime_from_unix"]


def unix_from_qdatetime(qdt: QDateTime) -> int:
    """QDateTime → Unix 秒（绝对时刻）。

    时区换算只走 Qt：``toPython()`` 会丢掉 QDateTime 自带的时区，返回 naive
    datetime，随后 ``.timestamp()`` 按**本机**时区重解释。实测带
    America/New_York 的秋季回拨歧义时刻 2026-11-01 01:30：
    ``toSecsSinceEpoch()``=1793511000（Qt 取标准时 −05:00），
    ``toPython().timestamp()``=1793467800（按本机 +08:00 解释），差 43200 s。
    """
    return int(qdt.toSecsSinceEpoch())


def qdatetime_from_unix(unix_s: float) -> QDateTime:
    """Unix 秒 → 本地时区的 QDateTime（输入框显示的是本地时刻）。"""
    return QDateTime.fromSecsSinceEpoch(int(round(float(unix_s))))


def format_offset(delta_s: float) -> str:
    """把「与现在的差」写成人话；0 附近直接说「与现在同步」。"""
    d = float(delta_s)
    if abs(d) < 60.0:
        return "与现在同步"
    sign = "+" if d > 0 else "−"
    d = abs(d)
    days, rest = divmod(int(d), 86400)
    hours, rest = divmod(rest, 3600)
    mins = rest // 60
    bits = []
    if days:
        bits.append(f"{days} 天")
    if hours:
        bits.append(f"{hours} 小时")
    if mins and not days:
        bits.append(f"{mins} 分")
    return f"{sign}{' '.join(bits) or '不到 1 分'}"


class TimeMachineDialog(QDialog):
    """跳到任意时刻的小面板。

    参数全部由调用方注入（当前时刻、暮光终求解、应用回调），对话框自己
    不读全局状态——好测，也不会在关闭后留下引擎被改动的副作用。
    """

    def __init__(self, parent=None, *, now_unix: Optional[float] = None,
                 real_unix: Optional[float] = None,
                 night_resolver: Optional[Callable[[], Optional[Tuple[datetime, float]]]] = None,
                 on_apply: Optional[Callable[[float], None]] = None,
                 real_now_provider: Optional[Callable[[], float]] = None,
                 night_resolver_for_date: Optional[
                     Callable[[QDateTime], Optional[Tuple[datetime, float]]]] = None,
                 on_now: Optional[Callable[[], Optional[bool]]] = None):
        super().__init__(parent)
        self.setWindowTitle("时间机器")
        self.setModal(True)
        self.setMinimumWidth(420)
        #: 「现在」的取值口。默认墙钟；测试注入固定值。「回到现在」必须
        #: 每次现取，不能复用对话框构造时的快照（开久了会跳到旧时刻）。
        self._real_now = real_now_provider or time.time
        self._real_unix = float(real_unix if real_unix is not None else self._real_now())
        self._now_unix = float(now_unix if now_unix is not None else self._real_unix)
        self._night_resolver = night_resolver
        # 显式区分新旧契约，不靠捕获 TypeError 猜回调签名。
        self._night_resolver_for_date = night_resolver_for_date
        self._on_apply = on_apply
        self._on_now = on_now
        #: 最近一次动作的终态回执（已在现在/已回到现在、天文暮光终、
        #: 极昼极夜没有事件、跳转失败）。刷新路径只回显它，不能把回执
        #: 改写成「模拟中 · 比现在 …」；用户重新选时刻才清空。
        self._terminal_status: str = ""
        self._build_ui()
        self._load_current()

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)

        title = QLabel("时间机器", self)
        title.setObjectName("tmTitle")
        lay.addWidget(title)

        hint_text = ("跳到任意时刻看那时的天空 · 观测条件设定，不是观测数据\n"
                     "输入为本机本地时间，不是站址时区；入夜按所选日，不固定为今天")
        note = local_time_note()
        if note:
            # 本地时间不可信时先说实话，再让用户继续输入。
            hint_text = f"{hint_text}\n{note}"
        hint = QLabel(hint_text, self)
        hint.setObjectName("tmHint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.edit = QDateTimeEdit(self)
        self.edit.setCalendarPopup(True)
        self.edit.setDisplayFormat("yyyy-MM-dd  HH:mm:ss")
        self.edit.setMinimumHeight(34)
        lay.addWidget(self.edit)

        quick = QHBoxLayout()
        quick.setSpacing(6)
        self._quick_buttons = []
        for label, seconds in (("−1 天", -86400), ("−1 小时", -3600),
                               ("+1 小时", 3600), ("+1 天", 86400)):
            b = QPushButton(label, self)
            b.clicked.connect(lambda _=False, s=seconds: self._shift(s))
            quick.addWidget(b)
            self._quick_buttons.append(b)
        lay.addLayout(quick)

        jump = QHBoxLayout()
        jump.setSpacing(6)
        self.now_btn = QPushButton("回到现在", self)
        self.now_btn.setToolTip(
            "立即恢复跟随系统当前时间和 1× 流速"
            if self._on_now is not None else
            "仅把输入框填到点击时的现在，尚未应用（点「跳过去」才改引擎）")
        self.now_btn.clicked.connect(self._go_now)
        jump.addWidget(self.now_btn)
        self.night_btn = QPushButton(
            "今夜" if self._night_resolver and not self._night_resolver_for_date
            else "所选日入夜", self)
        self.night_btn.setToolTip(
            "按输入框的本机本地日期和当前站址求暮光终；判据是太阳几何高度 "
            "-18°（天文暮光终定义，不是设备回读）。仅填入时刻，点「跳过去」生效"
            if self._night_resolver_for_date else
            "按调用方提供的今夜求解器填入时刻，点「跳过去」生效")
        self.night_btn.setEnabled(
            self._night_resolver_for_date is not None or self._night_resolver is not None)
        if not self.night_btn.isEnabled():
            self.night_btn.setToolTip("入夜不可用：未设置站址或暮光求解器")
        self.night_btn.clicked.connect(self._go_night)
        jump.addWidget(self.night_btn)
        jump.addStretch(1)
        self.ok_btn = QPushButton("跳过去", self)
        self.ok_btn.setObjectName("tmGo")
        self.ok_btn.setDefault(True)
        self.ok_btn.setMinimumHeight(32)
        self.ok_btn.clicked.connect(self._apply)
        jump.addWidget(self.ok_btn)
        lay.addLayout(jump)

        self.status = QLabel("", self)
        self.status.setObjectName("tmHint")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        # 任何时刻变化都立刻重算「与现在的差」：快捷按钮、日历、手打都要
        # 有反馈，否则点「+1 天」界面像没反应。
        self.edit.dateTimeChanged.connect(lambda _dt: self._on_time_edited())

        self.setStyleSheet(
            "QDialog { background-color: #14161c; }"
            # QLabel 默认吃 palette 的浅底，深色对话框里会变成一条条白条
            # （图层面板标题同款事故）；先统一透明，再单独上文字色。
            "QLabel { background: transparent; }"
            "QLabel#tmTitle { color: #e8eaf0; font-size: 15px;"
            " font-weight: 600; }"
            "QLabel#tmHint { color: #8f95a3; font-size: 12px; }"
            "QDateTimeEdit { background: #1b1e26; color: #e8eaf0;"
            " border: 1px solid #6A7284; border-radius: 8px;"
            " padding: 6px 10px; font-size: 13px; }"
            "QDateTimeEdit::drop-down { border: none;"
            " background: transparent; width: 24px; }"
            "QPushButton { background: #1b1e26; color: #d3d7e0;"
            " border: 1px solid #6A7284; border-radius: 8px;"
            " padding: 6px 12px; }"
            "QPushButton:hover { border-color: #8f95a3; }"
            "QPushButton:disabled { color: #5b6478; }"
            "QPushButton#tmGo { background: #2b6cf6; border-color: #2b6cf6;"
            " color: #ffffff; font-weight: 600; padding: 6px 16px; }"
            "QPushButton#tmGo:hover { background: #3b7bff; }"
        )

    # ------------------------------------------------------------------ #
    def _load_current(self) -> None:
        self.edit.setDateTime(qdatetime_from_unix(self._now_unix))
        self._refresh_status()

    def selected_unix(self) -> float:
        """输入框里的本地时刻 → Unix 秒；时区换算交给 Qt（见 unix_from_qdatetime）。"""
        return float(unix_from_qdatetime(self.edit.dateTime()))

    def _shift(self, seconds: int) -> None:
        self.edit.setDateTime(self.edit.dateTime().addSecs(int(seconds)))

    def _go_now(self) -> None:
        # 「现在」要现取：对话框开 10 分钟后点它，必须跳到此刻，而不是
        # 构造对话框那一刻（旧实现复用 _real_unix 快照）。
        try:
            if self._on_now is not None and self._on_now() is False:
                raise RuntimeError("返回现在未成功")
            fresh = float(self._real_now())
        except Exception:
            # 引擎或刷新链可能只完成了一部分，不能宣称时刻未变或已实时。
            self._terminal_status = "回到现在失败：未能确认实时状态"
            self.status.setText(self._terminal_status)
            return
        was_real = abs(float(self._now_unix) - fresh) < 0.5
        self._real_unix = fresh
        self._now_unix = fresh
        self._load_current()
        # 幂等动作也要有回执：本来就在「现在」时界面一个字都不变，
        # 用户会以为按钮坏了（UI 交互守卫专门抓这类「点了没反应」）。
        if self._on_now is not None:
            self.status.setText("已回到现在 · 实时跟随系统时间 · 1×")
        else:
            # 独立面板没有返回实况回调：仍保留只填框的旧契约。
            self.status.setText(("已在现在" if was_real else "已回到现在")
                                + " · 仅填入，未应用 · 点「跳过去」生效")
        self._terminal_status = self.status.text()

    def _go_night(self) -> None:
        if self._night_resolver_for_date is None and self._night_resolver is None:
            return
        try:
            if self._night_resolver_for_date is not None:
                got = self._night_resolver_for_date(self.edit.dateTime())
            else:
                got = self._night_resolver()
        except Exception:
            self._terminal_status = "暮光终计算失败：时刻未改变，不猜时刻"
            self.status.setText(self._terminal_status)
            return
        if not got:
            # 极昼/极夜没有这个事件：明说，不硬凑一个时刻。
            day_label = "所选本地日" if self._night_resolver_for_date else "求解日期"
            self._terminal_status = (
                f"当前站址在{day_label}没有暮光终事件（可能极昼/极夜），不猜时刻")
            self.status.setText(self._terminal_status)
            return
        t, alt_deg = got
        # 输入框精度为秒；取事件所在秒，避免午夜前的事件显示成隔日。
        qdt = qdatetime_from_unix(floor(t.timestamp())).toTimeZone(self.edit.timeZone())
        self.edit.setDateTime(qdt)
        label = "天文暮光终" if alt_deg == -18.0 else "民用暮光终，天未全黑"
        self.status.setText(
            f"{label}：太阳 {alt_deg:g}° · {qdt.toString('MM-dd HH:mm')}"
            " · 仅填入，点「跳过去」生效")
        self._terminal_status = self.status.text()

    def _on_time_edited(self) -> None:
        """用户重新选时刻 = 新一轮：上一轮的回执作废，回到实时差。"""
        self._terminal_status = ""
        self._refresh_status()

    def _refresh_status(self) -> None:
        if self._terminal_status:
            # 终态回执只在这里回显：dateTimeChanged / 主动刷新不得改写。
            self.status.setText(self._terminal_status)
            return
        delta = self.selected_unix() - self._real_unix
        if abs(delta) < 60.0:
            self.status.setText("当前就是现在（点「跳过去」生效）")
        else:
            self.status.setText(f"模拟中 · 比现在 {format_offset(delta)}")
        if not self.night_btn.isEnabled():
            self.status.setText(self.status.text() + " · " + self.night_btn.toolTip())

    def _apply(self) -> None:
        ts = self.selected_unix()
        self._refresh_status()
        if self._on_apply is not None:
            try:
                self._on_apply(ts)
            except Exception:
                self._terminal_status = "跳转失败：时间引擎不可用，时刻未改变"
                self.status.setText(self._terminal_status)
                return
        self.accept()
