# SPDX-License-Identifier: MIT
"""时间机器：把观测时刻跳到任意一天（对齐 Stellarium ``DateTimeDialog``）。

学的是它的**口径**而不是皮：``DateTimeDialog::setDateTime(double newJd)``
把用户输入的本地时刻换算成 JD 交给 ``core->setJD()``，再让整个天空按新
时刻重算；「回到现在」是单独的按钮。本模块照同一条链落地：

* 输入框是**本地时间**（用户脑子里想的是"今晚九点"，不是 UTC）；
* 应用时只交出一个 Unix 秒给主窗，由主窗调 ``new_spacetime.set_unix``
  并立刻刷历表——本模块不碰时间引擎、不碰天空视图，避免两条事实源；
* 状态行如实写「模拟中 · 比现在 +2 天 3 小时」，不把跳过去说成实时。

不做什么：不提供"假日期"（比如永远停在某个好看的夜晚）。跳过去之后
星、月、行星全部按该时刻真实计算，白天看不见星就是看不见星。
"""
from __future__ import annotations

import time
from typing import Callable, Optional, Tuple

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QDateTimeEdit, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

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
                 real_now_provider: Optional[Callable[[], float]] = None):
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
        self._on_apply = on_apply
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

        hint = QLabel("跳到任意时刻看那时的天空 · 观测条件设定，不是观测数据", self)
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
            "把时刻归零到今天此刻（点「跳过去」才改引擎）")
        self.now_btn.clicked.connect(self._go_now)
        jump.addWidget(self.now_btn)
        self.night_btn = QPushButton("今夜", self)
        self.night_btn.setToolTip(
            "按站点真实日落算天文暮光终（太阳 −18°），天全黑的时刻")
        self.night_btn.setEnabled(self._night_resolver is not None)
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
        fresh = float(self._real_now())
        was_real = abs(float(self._now_unix) - fresh) < 0.5
        self._real_unix = fresh
        self._now_unix = fresh
        self._load_current()
        # 幂等动作也要有回执：本来就在「现在」时界面一个字都不变，
        # 用户会以为按钮坏了（UI 交互守卫专门抓这类「点了没反应」）。
        # 只改输入框、不改引擎，所以文案不能说「实时 / 已恢复流速」，
        # 要写明还得点「跳过去」才生效。
        self.status.setText("已在现在 · 点「跳过去」生效" if was_real
                            else "已回到现在 · 点「跳过去」生效")
        self._terminal_status = self.status.text()

    def _go_night(self) -> None:
        if self._night_resolver is None:
            return
        try:
            got = self._night_resolver()
        except Exception:
            got = None
        if not got:
            # 极昼/极夜没有这个事件：明说，不硬凑一个时刻。
            self._terminal_status = "这个纬度当天没有天文暮光终（极昼/极夜），不猜时刻"
            self.status.setText(self._terminal_status)
            return
        t, alt_deg = got
        qdt = qdatetime_from_unix(t.timestamp())
        self.edit.setDateTime(qdt)
        self.status.setText(
            f"天文暮光终：太阳 {alt_deg:g}° · {qdt.toString('MM-dd HH:mm')}")
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
