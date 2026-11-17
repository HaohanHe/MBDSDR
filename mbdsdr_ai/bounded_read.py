# SPDX-License-Identifier: MIT
"""有界设备读：给没有 timeout 形参的同步驱动读加等待上限。

真机证据（2026-11-18）：RTL-SDR 已连接、已调谐（100.1 MHz / 2.4 MS/s / WFM）
后，pyrtlsdr ``read_bytes(16384)`` 12 秒无返回；``librtlsdr`` 的
``rtlsdr_read_sync`` 本身没有超时参数，调用方（CLI / 录制 / MCP）只能无限阻塞。

本机 pyrtlsdr 的 ``RtlSdr.read_bytes(num_bytes)`` 签名没有 timeout，不能
"直接传"；这里用常驻 daemon 工作线程 + 有界队列等待：

- 驱动成功 / 抛异常：原样交回调用线程（异常类型不变）；
- 超时：返回 ``(None, "read_timeout", 人话)``，调用方绝不无限阻塞；
- 同一 reader 只保留一个在途请求：设备卡死时旧读仍卡在驱动里，新请求排队，
  排队也满时立即返回 ``"read_inflight"``，不会每次超时都再堆一个线程。

不 join 无期线程：工作线程是 daemon，进程退出即回收；驱动里的阻塞读没有
可用的取消手段，硬 join 只会把退出流程也挂死。
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable, Optional, Tuple

#: 单次设备读的默认等待上限（秒）。RTL-SDR 正常一块 8192 样本在毫秒级返回；
#: 2 秒足够覆盖 USB 抖动，又不会让 CLI/录制在设备卡死时长时间挂住。
DEFAULT_READ_TIMEOUT_S = 2.0

REASON_TIMEOUT = "read_timeout"
REASON_INFLIGHT = "read_inflight"


class BoundedReader:
    """把无超时的阻塞读放进常驻 daemon 线程，按上限等待结果。

    ``read_fn(arg)`` 是真实驱动读（例如 ``RtlSdr.read_bytes``）；``arg``
    原样传入。一个实例只允许一个在途请求，避免设备卡死时线程无限累积。
    """

    def __init__(self, read_fn: Callable[[Any], Any],
                 timeout_s: float = DEFAULT_READ_TIMEOUT_S,
                 name: str = "mbdsdr-bounded-read"):
        self._read_fn = read_fn
        self.timeout_s = float(timeout_s)
        self._req: "queue.Queue" = queue.Queue(maxsize=1)
        self._done: "queue.Queue" = queue.Queue()
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run, name=name, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._req.get()
            if item is None:
                return
            token, arg = item
            try:
                self._done.put((token, True, self._read_fn(arg)))
            except Exception as exc:  # 交给调用线程按原类型处理
                self._done.put((token, False, exc))

    def read(self, arg: Any,
             timeout_s: Optional[float] = None) -> Tuple[Any, str, str]:
        """有界执行一次读，返回 ``(value, code, message)``。

        成功时 code/message 为空串；超时 value=None 且
        code=``"read_timeout"``；上一次读仍未返回时 code=``"read_inflight"``。
        驱动抛出的异常原样抛给调用方。
        """
        timeout = self.timeout_s if timeout_s is None else float(timeout_s)
        with self._seq_lock:
            self._seq += 1
            token = self._seq
        try:
            self._req.put_nowait((token, arg))
        except queue.Full:
            return None, REASON_INFLIGHT, (
                f"上一次设备读仍未返回（超过 {self.timeout_s:.1f}s），本次未下发")
        deadline = time.monotonic() + timeout
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                return None, REASON_TIMEOUT, (
                    f"超时未收到样本（{timeout:.1f}s 内读 {arg} 字节无返回）")
            try:
                got_token, ok, value = self._done.get(timeout=remain)
            except queue.Empty:
                return None, REASON_TIMEOUT, (
                    f"超时未收到样本（{timeout:.1f}s 内读 {arg} 字节无返回）")
            if got_token != token:
                continue  # 丢弃已超时请求的迟到结果，别把它当成这次的读数
            if not ok:
                raise value
            return value, "", ""

    def close(self) -> None:
        """尽力结束工作线程（阻塞在驱动里的读无法中断，daemon 随进程退出）。"""
        try:
            self._req.put_nowait(None)
        except queue.Full:
            pass
