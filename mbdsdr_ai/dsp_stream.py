"""
MBDSDR AI 内核 - 双缓冲乒乓 Stream
====================================

对照 SDR++ 源码（repos/sdrpp/core/src/dsp/stream.h）实现 Python 版乒乓缓冲区：

  - STREAM_BUFFER_SIZE = 1_000_000 样本（stream.h:9）
  - swap() 用条件变量等待 canSwap，交换 writeBuf/readBuf 指针后通知消费者
    （stream.h:43-68）
  - read() 阻塞等待 dataReady，返回有效数据长度（stream.h:70-76）

这是 SDR 内核的数据流基石：生产者（硬件读取线程）直接写 writeBuf，
消费者（DSP/录音/频谱线程）从 readBuf 读，两者通过指针交换零拷贝交接。
"""

import threading
import numpy as np

from typing import Tuple, Optional


class PingPongStream:
    """双缓冲乒乓 Stream（纯数据结构，不产生任何信号）。

    对照 sdrpp/core/src/dsp/stream.h 模板类 stream<T>。

    生产者：
      1. 直接写 self.write_buf 指向的 numpy 数组
      2. 写完后调用 swap(n)，指针交换 + 通知消费者

    消费者：
      1. 调用 read() 阻塞等待
      2. 返回 (readBuf, dataSize)，从 readBuf[:dataSize] 读数据
      3. 读完调用 flush()，通知生产者可以再次 swap

    两个线程（生产/消费）通过两个 Condition 各自等待对方就绪，
    不会互相阻塞在数据拷贝上——swap 只是交换指针，O(1)。
    """

    def __init__(self, dtype=np.complex64, buffer_size: int = 1_000_000):
        """分配两个等大的 numpy 数组作为乒乓缓冲。

        Args:
            dtype: 样本数据类型，默认 complex64（IQ 复基带）。
            buffer_size: 每缓冲样本数，默认 1,000,000（stream.h:9）。
        """
        self.dtype = dtype
        self.buffer_size = buffer_size

        # 两个缓冲区：生产者写 writeBuf，消费者读 readBuf
        self._write_buf = np.empty(buffer_size, dtype=dtype)
        self._read_buf = np.empty(buffer_size, dtype=dtype)

        # ── swap 侧（生产者等待消费者消费完上一帧）──
        self._swap_mtx = threading.Lock()
        self._swap_cv = threading.Condition(self._swap_mtx)
        self._can_swap = True          # stream.h:131

        # ── ready 侧（消费者等待生产者写完一帧）──
        self._rdy_mtx = threading.Lock()
        self._rdy_cv = threading.Condition(self._rdy_mtx)
        self._data_ready = False       # stream.h:135

        # ── 停止标志 ──
        self._writer_stop = False      # stream.h:138
        self._reader_stop = False      # stream.h:137

        self._data_size = 0            # stream.h:140

    @property
    def write_buf(self) -> np.ndarray:
        """当前写缓冲（生产者直接写入此数组的前 n 个位置）。"""
        return self._write_buf

    def swap(self, n: int) -> bool:
        """生产者写完一帧后调用：交换读写指针，通知消费者。

        对照 stream.h:43-68 swap(int size)。

        等待消费者 flush() 释放上一帧（canSwap=True），然后：
          1. 记录 dataSize = n
          2. 交换 writeBuf / readBuf 指针
          3. 设置 dataReady = True，通知消费者

        Returns:
            True  if swap 成功；
            False if writer 被 stopWriter() 停止。
        """
        with self._swap_cv:
            # 等待 canSwap 或 writerStop（stream.h:47）
            self._swap_cv.wait_for(
                lambda: self._can_swap or self._writer_stop
            )
            if self._writer_stop:
                return False

            # 记录有效数据长度，交换指针
            self._data_size = n
            self._write_buf, self._read_buf = self._read_buf, self._write_buf
            self._can_swap = False

        # 通知消费者：数据就绪（stream.h:61-65）
        # 注意：Python Condition.notify() 必须在持有锁时调用
        with self._rdy_cv:
            self._data_ready = True
            self._rdy_cv.notify_all()

        return True

    def read(self, timeout: Optional[float] = None) -> Tuple[np.ndarray, int]:
        """消费者阻塞等待一帧数据就绪。

        对照 stream.h:70-76 read()。

        Args:
            timeout: 超时秒数；None = 无限阻塞。

        Returns:
            (readBuf, dataSize) 元组；readBuf 是完整数组（消费者只读前
            dataSize 个样本）。若 reader 被 stopReader() 停止，返回
            (readBuf, -1)。
        """
        with self._rdy_cv:
            if timeout is not None:
                self._rdy_cv.wait_for(
                    lambda: self._data_ready or self._reader_stop,
                    timeout=timeout,
                )
            else:
                self._rdy_cv.wait_for(
                    lambda: self._data_ready or self._reader_stop,
                )

            if self._reader_stop:
                return (self._read_buf, -1)

            # 注意：即使 data_ready 为 False（超时），也返回当前 readBuf
            # 调用方需检查 data_size > 0 判断是否有有效数据
            n = self._data_size if self._data_ready else 0

        return (self._read_buf, n)

    def flush(self):
        """消费者读完后调用：通知生产者可以 swap 下一帧。

        对照 stream.h:78-92 flush()。
        """
        with self._rdy_cv:
            self._data_ready = False

        with self._swap_cv:
            self._can_swap = True
            self._swap_cv.notify_all()

    def stop_writer(self):
        """停止生产者：唤醒等待在 swap() 的生产者线程。"""
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
