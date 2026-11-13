# SPDX-License-Identifier: MIT
"""连接超时的双线程竞态（红证，产品补丁未下）。

现状：`MainWindow._connect_backend_in_background` 的 `finally` 无条件把
`_connect_in_flight` 复位成 False。20 s 看门狗超时后后台线程仍卡在
`backend.connect()` 里（原生 open 卡在驱动/套接字），此时用户再点连接，
入口闸门是开的 → 起第二根线程 → 两根线程抢同一根棒子（同一 USB 设备第二次
open，实测语义就是 ACCESS(-3)）。

本文件先断「修好之后应该成立」的三件事：
1. 超时后有一个**公开可读**的状态 `connect_still_settling`，值为 True；
2. `_connect_in_flight` 不是被 `finally` 复位成 False（它与上面同真）；
3. 第二次调用不再触发第二次真实 `connect()`，返回 False，并给出可读提示
   （说清是「上一次仍在收尾」，而不是无声拒绝）。

假 backend 的 `connect()` 阻塞 21 s（> 20 s 看门狗），测试收尾用 Event 放行，
不把线程留在进程里；不用真实硬件、不断言耗时曲线。
"""
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

#: 假 backend 的 connect 阻塞时长：必须**超过** 20 s 看门狗，才进得了「已超时
#: 但线程还活着」这个窗口。取 21 s，留 1 s 余量给调度抖动。
_BLOCK_S = 21.0


class _BlockingBackend:
    """只实现本用例用到的 connect()；调用次数是本文件的核心观测量。"""

    def __init__(self, name: str = "A", block_s: float = _BLOCK_S):
        self.name = name
        self.connect_calls = 0
        self._block_s = block_s
        self.release = threading.Event()

    def connect(self) -> bool:
        self.connect_calls += 1
        self.release.wait(self._block_s)
        return False


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(isolated_profile, qtapp):
    import main_window as mw
    w = mw.MainWindow()
    yield w
    try:
        w.close()
    except Exception:  # noqa: BLE001 - 收尾失败不影响结论
        pass


def test_timed_out_connect_does_not_start_second_real_connect(window):
    be = _BlockingBackend("A")
    try:
        # 第一次：撞 20 s 看门狗超时，后台线程仍在 connect() 里。
        first = window._connect_backend_in_background(be)

        # 超时窗口内的可读状态：不是「空闲」，而是「上一次仍在收尾」。
        settling = getattr(window, "connect_still_settling", None)
        inflight = window._connect_in_flight

        # 第二次：修好后应立刻拒绝，且**不得**再发起一次真实 connect()。
        t0 = time.monotonic()
        second = window._connect_backend_in_background(be)
        waited = time.monotonic() - t0
        msg = window.statusBar().currentMessage()

        observed = {
            "first_returned": first,
            "connect_calls": be.connect_calls,
            "connect_still_settling": settling,
            "_connect_in_flight": inflight,
            "second_returned": second,
            "second_waited_s": round(waited, 2),
            "status": msg,
        }
        # 打出来是为了让红证自证：xfail 只报「有一条 xfailed」，看不出数字。
        print("W25-connect-race observed:", observed)
        # B2（2026-11-13）：超时不再是「失败」，而是三态里的 None = 结果未知；
        # 「不静默当成功」这条不变——None 不是 True。同步调用方仍按 False 收场。
        assert first is None, f"超时应返回 None（结果未知，≠失败≠成功）：{observed}"
        assert be.connect_calls == 1, (
            "超时后又发起了第二次真实 connect()（两根线程抢同一根棒子）："
            f"{observed}")
        assert settling is True, (
            "超时后没有可读的「上一次仍在收尾」状态（connect_still_settling）："
            f"{observed}")
        assert inflight is True, (
            "finally 把 _connect_in_flight 复位成了 False：入口闸门重新打开："
            f"{observed}")
        assert second is False, (
            f"上一次仍在收尾时，第二次连接必须明确返回 False：{observed}")
        assert waited < 5.0, (
            "第二次连接没有立即拒绝，而是又阻塞了一个完整超时窗口："
            f"{observed}")
        assert "上一次" in msg, (
            "第二次连接被拒时没有说清原因（应提示「上一次仍在收尾」）："
            f"{observed}")
    finally:
        be.release.set()
