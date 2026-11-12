# SPDX-License-Identifier: MIT
"""桌面 SDR 会话的 AI 侧委托（单权威接线第 2 步）。

所属层：desktop。本模块的写路径必须 marshal 回 GUI 线程（QObject + Signal
跨线程投递），是「真的需要 Qt 类型贯穿」，无法用无 Qt 的回调替代；原先是
mbdsdr_ai 里的一个 Qt 岛，D1 收口时整模块迁到界面层，避免纯逻辑包依赖 Qt
（守卫见 tests/test_import_direction_contract.py）。

为什么需要
----------
桌面 ``MainWindow._active_sdr_backend`` 与 ``agent.sdr_manager`` 原本是
两个互不通信的实例：用户对 AI 说「断开」，AI 断开的是自己那个，桌面里
正在收的那台仍开着——状态说成功、物理世界没变。

本模块让 ``agent.sdr_manager`` 变成桌面的**委托**，而不是再开一套后端：

* 读：``get_active()`` 直通桌面当前激活后端（桌面是唯一权威）；
* 写：``connect`` / ``disconnect`` / ``switch_device`` marshal 回 GUI
  线程，由桌面自己的 ``_connect_backend`` / ``_teardown_connection``
  执行，**桌面确认生效后才返回 True**；没确认就返回 False 并说明原因；
* 其余成员（VFO、扫描、IQ 文件、平台信息……）委托内部真
  ``SDRBackendManager``，AI 原有能力不变。

尚未接好（别当成已完成）
------------------------
显式注入那一行还没落（见 ``docs/COORDINATION.md``「等待项：AI SDR 会话
显式注入」）。窗口只能惰性解析：``window`` 传可调用对象时，**第一次工具
调用**才去取宿主窗口；解析不到就显式拒绝并说明原因，绝不假装成功。
``connect_sdr`` / ``open_iq_file`` 仍作用在内部 manager 上，不随桌面会话走。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

#: 等 GUI 线程响应的上限。工具接口是同步的，不能让 AI 无限挂着。
_GUI_TIMEOUT_S = 10.0

try:  # pragma: no cover - 无 Qt 的 CLI 环境走降级分支
    from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal, Slot
    _QT_OK = True
except Exception:  # noqa: BLE001
    QCoreApplication = QObject = QThread = object  # type: ignore[assignment]
    Signal = Slot = None  # type: ignore[assignment]
    _QT_OK = False

_NO_WINDOW = ("AI 侧尚未接上桌面窗口（显式注入未落，且本次未解析到宿主）："
              "这次不碰硬件，也不假装成功。")


class _Call:
    """一次跨线程调用的结果槽。"""

    __slots__ = ("fn", "args", "done", "ok", "note")

    def __init__(self, fn, args):
        self.fn = fn
        self.args = args
        self.done = threading.Event()
        self.ok = False
        self.note = ""


if _QT_OK:
    class _Bridge(QObject):
        """GUI 线程侧执行器：所有桌面操作都落到它所在的线程。"""

        requested = Signal(object)

        @Slot(object)
        def _deliver(self, call: "_Call") -> None:
            try:
                call.ok, call.note = call.fn(*call.args)
            except Exception as exc:  # noqa: BLE001 - 失败要变成返回值，不是崩线程
                call.ok = False
                call.note = f"{type(exc).__name__}: {exc}"
                logger.exception("桌面 SDR 会话操作失败")
            finally:
                call.done.set()
else:  # pragma: no cover
    _Bridge = None  # type: ignore[assignment]


class DesktopSdrSession:
    """``agent.sdr_manager`` 的桌面委托实现（接口见模块 docstring）。"""

    def __init__(self, window: Any = None, *, local_manager=None):
        """``window`` 可以是宿主窗口，也可以是返回宿主窗口的可调用对象（惰性解析）。"""
        self._window_ref = window
        self._resolved: Any = None
        if local_manager is None:
            # 后端模块只有一份，在 AI 内核里（desktop/ 下没有 sdr_backend）。
            # 本模块从 mbdsdr_ai 迁出后沿用了原来的 ``from .sdr_backend``，
            # 在包上下文下会被解析成不存在的 desktop.sdr_backend。
            from mbdsdr_ai.sdr_backend import SDRBackendManager
            local_manager = SDRBackendManager()
        self._local = local_manager
        #: 只有存在事件循环（桌面）时才建桥：没有它说明是 CLI/单测，
        #: 直接在本线程执行即可。桥必须在 GUI 线程构造，故此处立即建。
        self._bridge = (
            _Bridge() if (_QT_OK and QCoreApplication.instance() is not None)
            else None
        )
        if self._bridge is not None:
            self._bridge.requested.connect(self._bridge._deliver)
        #: 与 SDRBackendManager 同名属性对齐：工具层会读它给出可操作提示。
        self.last_connect_notice = ""

    # ------------------------------------------------------------ 窗口解析
    def _window(self):
        win = self._resolved
        if win is not None:
            return win
        ref = self._window_ref
        try:
            win = ref() if callable(ref) else ref
        except Exception:  # noqa: BLE001 - 解析失败只影响本次，不缓存失败
            logger.exception("桌面窗口解析失败")
            return None
        self._resolved = win
        return win

    # ------------------------------------------------------------ 读：直通
    def get_active(self):
        win = self._window()
        return None if win is None else getattr(win, "_active_sdr_backend", None)

    def get_active_backend(self):
        return self.get_active()

    def list_devices(self):
        return self._local.list_devices()

    def list_all_devices(self):
        return self._local.list_all_devices()

    # ------------------------------------------------------------ 写：回 GUI
    def connect(self, device_id: str = None) -> bool:
        ok, note = self._run_in_gui(self._connect_on_gui, device_id)
        self.last_connect_notice = note
        return ok

    def disconnect(self):
        ok, note = self._run_in_gui(self._disconnect_on_gui)
        self.last_connect_notice = note
        return ok

    def switch_device(self, device_id: str) -> bool:
        ok, note = self._run_in_gui(self._switch_on_gui, device_id)
        self.last_connect_notice = note
        return ok

    def _connect_on_gui(self, device_id: Optional[str]) -> Tuple[bool, str]:
        win = self._window()
        if win is None:
            return False, _NO_WINDOW
        backend = self._lookup(device_id)
        if backend is None:
            return False, self._unknown_device_notice(device_id)
        if getattr(win, "_active_sdr_backend", None) is backend:
            return True, f"设备 {device_id} 已是桌面当前连接"
        ok = bool(win._connect_backend(backend))
        confirmed = getattr(win, "_active_sdr_backend", None) is backend
        if ok and confirmed:
            return True, f"已由桌面连接 {device_id}"
        return False, self._unconfirmed_notice("连接", device_id, ok, win)

    def _disconnect_on_gui(self) -> Tuple[bool, str]:
        win = self._window()
        if win is None:
            return False, _NO_WINDOW
        if getattr(win, "_active_sdr_backend", None) is None:
            return True, "桌面当前没有活动连接（已是断开态）"
        win._teardown_connection()
        if getattr(win, "_active_sdr_backend", None) is None:
            return True, "桌面已断开"
        return False, "桌面未确认断连（_teardown_connection 返回后仍有激活后端）"

    def _switch_on_gui(self, device_id: str) -> Tuple[bool, str]:
        win = self._window()
        if win is None:
            return False, _NO_WINDOW
        backend = self._lookup(device_id)
        if backend is None:
            return False, self._unknown_device_notice(device_id)
        if getattr(win, "_active_sdr_backend", None) is backend:
            return True, f"设备 {device_id} 已是桌面当前连接"
        if getattr(win, "_active_sdr_backend", None) is not None:
            win._teardown_connection()
        ok = bool(win._connect_backend(backend))
        confirmed = getattr(win, "_active_sdr_backend", None) is backend
        if ok and confirmed:
            return True, f"已由桌面切换到 {device_id}"
        return False, self._unconfirmed_notice("切换", device_id, ok, win)

    # ------------------------------------------------------------ 内部
    def _lookup(self, device_id: Optional[str]):
        if not device_id:
            return None
        return self._local.backends.get(str(device_id))

    def _unknown_device_notice(self, device_id: Optional[str]) -> str:
        known = ", ".join(sorted(self._local.backends)) or "（空）"
        return f"设备 {device_id!r} 不在 AI 侧设备表中，未操作。可用：{known}"

    def _unconfirmed_notice(self, what: str, device_id, ok: bool, win) -> str:
        active = getattr(win, "_active_sdr_backend", None)
        return (f"桌面未确认{what} {device_id}（_connect_backend 返回 {ok}，"
                f"当前激活后端={type(active).__name__ if active else 'None'}）")

    def _run_in_gui(self, fn, *args) -> Tuple[bool, str]:
        """在 GUI 线程执行 ``fn``；没有事件循环或已在 GUI 线程时直接执行。"""
        bridge = self._bridge
        if bridge is None:
            return fn(*args)
        current = QThread.currentThread()
        gui = bridge.thread()
        if current is gui or current == gui:
            return fn(*args)
        call = _Call(fn, args)
        bridge.requested.emit(call)
        if not call.done.wait(_GUI_TIMEOUT_S):
            return False, f"桌面在 {_GUI_TIMEOUT_S:.0f}s 内未响应，本次操作状态未知"
        return call.ok, call.note

    def __getattr__(self, name):
        # 只接本类没显式实现的名字：VFO/扫描/IQ/平台信息等仍由内部真
        # manager 提供，保持 AI 原有能力不变。
        try:
            local = object.__getattribute__(self, "_local")
        except AttributeError:
            raise AttributeError(name) from None
        return getattr(local, name)
