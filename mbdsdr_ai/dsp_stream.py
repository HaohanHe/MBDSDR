# SPDX-License-Identifier: MIT
"""
Double-buffered (ping-pong) streaming buffer.

A zero-copy hand-off between a producer (hardware read thread) and a consumer
(DSP / recording / spectrum thread).  The producer writes one array and then
calls :meth:`swap` to exchange the read/write pointers; the consumer reads the
other array and calls :meth:`flush` when done.  Two condition variables let
each side wait for the other without blocking on data copies -- the swap itself
is an O(1) pointer exchange.
"""

import threading
import numpy as np

from typing import Tuple, Optional


class PingPongStream:
    """A double-buffered ping-pong stream (pure data structure, no signal source).

    Producer:
      1. write directly into the array exposed by :attr:`write_buf`
      2. call :meth:`swap` after a frame is written (pointer exchange + notify)

    Consumer:
      1. call :meth:`read` (blocks until a frame is ready)
      2. read from ``readBuf[:dataSize]``
      3. call :meth:`flush` when done, allowing the next swap
    """

    def __init__(self, dtype=np.complex64, buffer_size: int = 1_000_000):
        """Allocate two equal arrays as the ping-pong buffers.

        Args:
            dtype: sample type, default complex64.
            buffer_size: samples per buffer, default 1,000,000.
        """
        self.dtype = dtype
        self.buffer_size = buffer_size

        # Producer writes write_buf; consumer reads read_buf.
        self._write_buf = np.empty(buffer_size, dtype=dtype)
        self._read_buf = np.empty(buffer_size, dtype=dtype)

        # Swap side: producer waits for the consumer to finish the previous frame.
        self._swap_mtx = threading.Lock()
        self._swap_cv = threading.Condition(self._swap_mtx)
        self._can_swap = True

        # Ready side: consumer waits for the producer to finish a frame.
        self._rdy_mtx = threading.Lock()
        self._rdy_cv = threading.Condition(self._rdy_mtx)
        self._data_ready = False

        # Stop flags.
        self._writer_stop = False
        self._reader_stop = False

        self._data_size = 0

    @property
    def write_buf(self) -> np.ndarray:
        """The current write buffer (producer writes its first n samples here)."""
        return self._write_buf

    def swap(self, n: int) -> bool:
        """Producer-side call after a frame is written: exchange read/write
        pointers and notify the consumer.

        Waits until the consumer has flushed the previous frame, then records
        the valid length, swaps the buffers, and marks data ready.

        Returns:
            True on success; False if the writer was stopped.
        """
        with self._swap_cv:
            self._swap_cv.wait_for(
                lambda: self._can_swap or self._writer_stop
            )
            if self._writer_stop:
                return False

            self._data_size = n
            self._write_buf, self._read_buf = self._read_buf, self._write_buf
            self._can_swap = False

        # Notify the consumer that data is ready.
        with self._rdy_cv:
            self._data_ready = True
            self._rdy_cv.notify_all()

        return True

    def read(self, timeout: Optional[float] = None) -> Tuple[np.ndarray, int]:
        """Consumer-side block until a frame is ready.

        Args:
            timeout: seconds to wait; None blocks indefinitely.

        Returns:
            ``(readBuf, dataSize)``; read the first ``dataSize`` samples.  If the
            reader was stopped, returns ``(readBuf, -1)``.
        """
        with self._rdy_cv:
            self._rdy_cv.wait_for(
                lambda: self._data_ready or self._reader_stop,
                timeout=timeout,
            )

            if self._reader_stop:
                return (self._read_buf, -1)

            n = self._data_size if self._data_ready else 0

        return (self._read_buf, n)

    def flush(self):
        """Consumer-side call after reading: allow the producer to swap again."""
        with self._rdy_cv:
            self._data_ready = False

        with self._swap_cv:
            self._can_swap = True
            self._swap_cv.notify_all()

    def stop_writer(self):
        """Stop the producer: wake a producer waiting in swap()."""
        with self._swap_cv:
            self._writer_stop = True
            self._swap_cv.notify_all()

    def clear_stop(self):
        """清除所有停止标志（writer + reader），可重新启动流。"""
        with self._swap_cv:
            self._writer_stop = False
            self._can_swap = True
            self._swap_cv.notify_all()

        with self._rdy_cv:
            self._reader_stop = False
            self._data_ready = False
            self._rdy_cv.notify_all()

    def stop_reader(self):
        """停止消费者：唤醒等待在 read() 的消费者线程。"""
        with self._rdy_cv:
            self._reader_stop = True
            self._rdy_cv.notify_all()
