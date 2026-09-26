"""
MBDSDR AI 内核 - GNU Radio 风格 TPB 流图调度器
=============================================

对照上游（见 docs/learn/sdrpp_gnuradio_port.md 第二节）：

  - FlowGraph 拓扑排序 + 三色判环
                     <-> gnuradio-runtime/lib/flowgraph.cc:384-453
  - TPB 每块一个线程   <-> gnuradio-runtime/lib/scheduler_tpb.cc:75-89
  - tpb_thread_body 主循环（READY/BLKD_IN/BLKD_OUT/DONE）
                     <-> gnuradio-runtime/lib/tpb_thread_body.cc:63-137
  - block_executor.run_one_iteration（forecast/general_work/consume/produce）
                     <-> gnuradio-runtime/lib/block_executor.cc:261-722
  - 环形缓冲           <-> gnuradio-runtime/lib/buffer.cc:57-143
  - 块间 StreamBuffer 通信
                     <-> 本项目 mbdsdr_ai/block_stream.py:121-210

我们的增强：
  * 纯 Python 块（GNU Radio 块要 C++ 或 SWIG；这里直接子类化 TpbBlock 即可）；
  * FlowGraph.partition() 自动把图拆成可并行子图（独立 source→sink 链）；
  * 背压用 threading.Condition 实现：上游写满则等，下游读空则等。

工作约定（与 block_executor.cc 一致）：
  - 每块一个线程，循环：forecast → 等输入可用 → 等输出空间 → work() → produce/consume；
  - source 块 ninputs=0；sink 块 noutputs=0；
  - work(inputs, outputs) 返回 nproduced；块自己调 consume(i, n) 推进读指针。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# 复用已有 StreamBuffer / StreamReader 的数据结构设计（block_stream.py:71-210）
# 但 TPB 调度需要"阻塞写/阻塞读"语义，所以这里加一层带 Condition 的环形缓冲。


# ---------------------------------------------------------------------------
# 线程安全阻塞环形缓冲（TPB 版 StreamBuffer）
# ---------------------------------------------------------------------------
class TpbBuffer:
    """有界环形缓冲，写满阻塞、读空阻塞，对应 buffer.cc:57-143。

    一个写者（上游块线程），一个或多个读者（下游块线程，fan-out）。
    与 block_stream.StreamBuffer 的区别：
      - 写满不抛 BufferError，而是 wait 在 not_full 条件上；
      - 读空不抛 ValueError，而是 wait 在 not_empty 条件上；
      - stop() 广播所有等待者退出。
    """

    def __init__(self, capacity: int = 4096, dtype=np.complex64):
        if capacity < 2:
            raise ValueError("capacity must be >= 2")
        self.capacity = capacity
        self.data = np.zeros(capacity, dtype=dtype)
        self._w = 0  # 写指针（环形下标）
        self._r = 0  # 读指针（环形下标），单读者；fan-out 用多 reader 见下
        self._count = 0  # 当前缓冲内样本数
        self._lock = threading.Lock()
        self._not_full = threading.Condition(self._lock)
        self._not_empty = threading.Condition(self._lock)
        self._stopped = False

    # -- 单读者接口（TPB 最常见 1:1 连接）-----------------------------------
    def write(self, items: np.ndarray, timeout: Optional[float] = None) -> bool:
        """写入 items；缓冲满则阻塞。返回 False 表示被 stop() 唤醒。"""
        n = len(items)
        if n == 0:
            return True
        deadline = None if timeout is None else time.time() + timeout
        with self._lock:
            while self._count + n > self.capacity:
                remaining = None
                if deadline is not None:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        return False
                if not self._not_full.wait(timeout=remaining):
                    return False
                if self._stopped:
                    return False
            # 写入（处理回绕）
            end = self._w + n
            if end <= self.capacity:
                self.data[self._w:end] = items
            else:
                first = self.capacity - self._w
                self.data[self._w:] = items[:first]
                self.data[: n - first] = items[first:]
            self._w = (self._w + n) % self.capacity
            self._count += n
            self._not_empty.notify_all()
            return True

    def read(self, n: int, timeout: Optional[float] = None) -> Optional[np.ndarray]:
        """读最多 n 个样本；不足则阻塞等。返回 None 表示被 stop() 唤醒。"""
        if n <= 0:
            return np.empty(0, dtype=self.data.dtype)
        deadline = None if timeout is None else time.time() + timeout
        with self._lock:
            while self._count < n:
                remaining = None
                if deadline is not None:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        # 超时：能读多少读多少（不报错）
                        if self._count == 0:
                            return None if self._stopped else np.empty(0, dtype=self.data.dtype)
                        break
                if not self._not_empty.wait(timeout=remaining):
                    return None
                if self._stopped and self._count == 0:
                    return None
            take = min(n, self._count)
            out = np.empty(take, dtype=self.data.dtype)
            end = self._r + take
            if end <= self.capacity:
                out[:] = self.data[self._r:end]
            else:
                first = self.capacity - self._r
                out[:first] = self.data[self._r:]
                out[first:] = self.data[: take - first]
            self._r = (self._r + take) % self.capacity
            self._count -= take
            self._not_full.notify_all()
            return out

    def available(self) -> int:
        with self._lock:
            return self._count

    def space(self) -> int:
        with self._lock:
            return self.capacity - self._count

    def stop(self) -> None:
        """唤醒所有等待者，让它们退出循环。"""
        with self._lock:
            self._stopped = True
            self._not_full.notify_all()
            self._not_empty.notify_all()


# ---------------------------------------------------------------------------
# TPB Block 基类
# ---------------------------------------------------------------------------
class TpbBlock:
    """信号处理块基类。子类覆写 work()。

    生命周期（由 Scheduler 在每块自己的线程里驱动，对应 tpb_thread_body.cc:63-137）：
      1. forecast(nout) -> list[int]：问每输入要多少样本；
      2. 从每输入缓冲读够样本；
      3. 检查每输出缓冲有空间；
      4. work(inputs, outputs) -> nproduced；
      5. 推进输入读指针（consume）。
    """

    def __init__(self, name: str, ninputs: int = 1, noutputs: int = 1):
        self.name = name
        self.ninputs = ninputs
        self.noutputs = noutputs
        # 运行期接线（由 FlowGraph.setup 填充）
        self._in_bufs: List[Optional[TpbBuffer]] = [None] * ninputs
        self._out_bufs: List[Optional[TpbBuffer]] = [None] * noutputs
        # 统计
        self.nitems_read = [0] * ninputs
        self.nitems_written = [0] * noutputs
        self._stop = threading.Event()

    # -- 子类覆写 -----------------------------------------------------------
    def forecast(self, noutput_items: int) -> List[int]:
        """要产 noutput_items 个样本，每输入要多少（block_executor.cc:523）。"""
        return [noutput_items] * self.ninputs

    def work(self, inputs: List[np.ndarray], outputs: List[np.ndarray]) -> int:
        """处理数据。outputs 已预分配。返回实际产出数。"""
        raise NotImplementedError

    def stop(self) -> None:
        self._stop.set()
        for b in self._out_bufs:
            if b is not None:
                b.stop()
        for b in self._in_bufs:
            if b is not None:
                b.stop()


# ---------------------------------------------------------------------------
# 几个内置参考块（确定性）
# ---------------------------------------------------------------------------
class VectorSource(TpbBlock):
    """从给定数组循环吐样本的 source（ninputs=0）。"""

    def __init__(self, name: str, vector: np.ndarray, repeat: bool = True):
        super().__init__(name, ninputs=0, noutputs=1)
        self._vec = np.asarray(vector, dtype=np.complex64)
        self._pos = 0
        self.repeat = repeat

    def work(self, inputs, outputs):
        n = len(outputs[0])
        out = outputs[0]
        written = 0
        while written < n:
            if self._pos >= len(self._vec):
                if not self.repeat:
                    out[written:] = 0
                    written = n
                    break
                self._pos = 0
            chunk = min(n - written, len(self._vec) - self._pos)
            out[written:written + chunk] = self._vec[self._pos:self._pos + chunk]
            written += chunk
            self._pos += chunk
        return written


class VectorSink(TpbBlock):
    """把收到的样本收集到 list 的 sink（noutputs=0）。"""

    def __init__(self, name: str):
        super().__init__(name, ninputs=1, noutputs=0)
        self.collected: List[complex] = []

    def work(self, inputs, outputs):
        self.collected.extend(inputs[0].tolist())
        return 0


class MultiplyConst(TpbBlock):
    """y = c * x（1:1）。"""

    def __init__(self, name: str, c: complex = 1.0 + 0j):
        super().__init__(name, ninputs=1, noutputs=1)
        self.c = complex(c)

    def work(self, inputs, outputs):
        outputs[0][:] = self.c * inputs[0]
        return len(inputs[0])


# ---------------------------------------------------------------------------
# FlowGraph：连接 / 校验 / 分区
# ---------------------------------------------------------------------------
class FlowGraph:
    """块连接图。对应 flowgraph.cc:384-453。"""

    def __init__(self, buffer_capacity: int = 4096, chunk: int = 1024):
        self.buffer_capacity = buffer_capacity
        self.chunk = chunk
        self.blocks: List[TpbBlock] = []
        # edges: (src_block, src_port, dst_block, dst_port)
        self.edges: List[Tuple[TpbBlock, int, TpbBlock, int]] = []

    # -- 连接 ---------------------------------------------------------------
    def add_block(self, blk: TpbBlock) -> None:
        if blk not in self.blocks:
            self.blocks.append(blk)

    def connect(self, src: TpbBlock, src_port: int, dst: TpbBlock, dst_port: int) -> None:
        if src not in self.blocks:
            self.add_block(src)
        if dst not in self.blocks:
            self.add_block(dst)
        self.edges.append((src, src_port, dst, dst_port))

    # -- 校验 ---------------------------------------------------------------
    def validate(self) -> None:
        """检测环 + 未连接端口。对应 flowgraph.cc:428-453。"""
        if not self.blocks:
            return
        # 1) 三色 DFS 判环
        WHITE, GREY, BLACK = 0, 1, 2
        color = {b: WHITE for b in self.blocks}
        adj: Dict[TpbBlock, List[TpbBlock]] = {b: [] for b in self.blocks}
        indeg: Dict[TpbBlock, int] = {b: 0 for b in self.blocks}
        for src, _, dst, _ in self.edges:
            adj[src].append(dst)
            indeg[dst] += 1

        def dfs(b: TpbBlock) -> None:
            color[b] = GREY
            for nxt in adj[b]:
                if color[nxt] == GREY:
                    raise RuntimeError(
                        f"flow graph has loops! (edge {b.name} -> {nxt.name})"
                    )
                if color[nxt] == WHITE:
                    dfs(nxt)
            color[b] = BLACK

        for b in self.blocks:
            if color[b] == WHITE:
                dfs(b)

        # 2) 未连接端口检查
        connected_in = {(dst, dst_port) for _, _, dst, dst_port in self.edges}
        connected_out = {(src, src_port) for src, src_port, _, _ in self.edges}
        for b in self.blocks:
            for p in range(b.ninputs):
                if (b, p) not in connected_in and b.ninputs > 0:
                    raise ValueError(
                        f"block {b.name} input port {p} is not connected"
                    )
            for p in range(b.noutputs):
                if (b, p) not in connected_out and b.noutputs > 0:
                    raise ValueError(
                        f"block {b.name} output port {p} is not connected"
                    )

    # -- 拓扑排序 -----------------------------------------------------------
    def topological_sort(self) -> List[TpbBlock]:
        indeg: Dict[TpbBlock, int] = {b: 0 for b in self.blocks}
        adj: Dict[TpbBlock, List[TpbBlock]] = {b: [] for b in self.blocks}
        for src, _, dst, _ in self.edges:
            adj[src].append(dst)
            indeg[dst] += 1
        queue = [b for b in self.blocks if indeg[b] == 0]
        order: List[TpbBlock] = []
        while queue:
            b = queue.pop(0)
            order.append(b)
            for nxt in adj[b]:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    queue.append(nxt)
        if len(order) != len(self.blocks):
            raise RuntimeError("flow graph has loops!")
        return order

    # -- 分区：找独立 source→sink 链（可并行子图）--------------------------
    def partition(self) -> List[List[TpbBlock]]:
        """把图拆成若干连通分量（每个分量一个 source→sink 链）。

        对应 flat_flowgraph 的 partition 思想：独立分量可放不同线程组。
        """
        # 用并查集
        parent = {b: b for b in self.blocks}

        def find(x: TpbBlock) -> TpbBlock:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: TpbBlock, b: TpbBlock) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for src, _, dst, _ in self.edges:
            union(src, dst)
        groups: Dict[TpbBlock, List[TpbBlock]] = {}
        for b in self.blocks:
            root = find(b)
            groups.setdefault(root, []).append(b)
        return list(groups.values())

    # -- 装配：建缓冲 + 接线 ------------------------------------------------
    def setup(self) -> None:
        self.validate()
        # 给每个输出端口建一个 TpbBuffer
        for b in self.blocks:
            for p in range(b.noutputs):
                b._out_bufs[p] = TpbBuffer(capacity=self.buffer_capacity)
        # 边：上游 buffer 给下游读（1:1 直连；fan-out 暂时不支持多 reader）
        for src, src_port, dst, dst_port in self.edges:
            dst._in_bufs[dst_port] = src._out_bufs[src_port]


# ---------------------------------------------------------------------------
# Scheduler：TPB 每块一线程
# ---------------------------------------------------------------------------
class Scheduler:
    """GNU Radio TPB 调度器的 Python 版。

    run() 给每块起一个线程，每块在自己线程里跑：
        while not stop:
            forecast -> 等输入 -> 等输出空间 -> work -> consume/produce
    对应 tpb_thread_body.cc:63-137。
    """

    def __init__(self, fg: FlowGraph):
        self.fg = fg
        self._threads: List[threading.Thread] = []
        self._stop_evt = threading.Event()
        self._exceptions: List[BaseException] = []

    def _block_thread(self, blk: TpbBlock) -> None:
        """每块一个线程的主循环。"""
        try:
            while not self._stop_evt.is_set() and not blk._stop.is_set():
                # 1) source 不需要 forecast 输入
                if blk.ninputs > 0:
                    nout = self.fg.chunk
                    req = blk.forecast(nout)
                    # 等输入可用
                    inputs: List[np.ndarray] = []
                    ok = True
                    for i, ibuf in enumerate(blk._in_bufs):
                        assert ibuf is not None
                        data = ibuf.read(req[i], timeout=0.2)
                        if data is None:
                            ok = False
                            break
                        if len(data) < req[i]:
                            # 输入不够，等一下
                            time.sleep(0.001)
                            ok = False
                            break
                        inputs.append(data)
                    if not ok:
                        continue
                else:
                    nout = self.fg.chunk
                    req = []
                    inputs = []

                # 2) 预分配输出
                outputs = [
                    np.empty(nout, dtype=np.complex64) for _ in range(blk.noutputs)
                ]

                # 3) work
                nproduced = blk.work(inputs, outputs)
                if nproduced < 0:
                    nproduced = 0

                # 4) 写输出缓冲（阻塞，对应 BLKD_OUT）
                for p in range(blk.noutputs):
                    obuf = blk._out_bufs[p]
                    assert obuf is not None
                    out_data = outputs[p][:nproduced]
                    if len(out_data) > 0:
                        ok = obuf.write(out_data, timeout=1.0)
                        if not ok:
                            return  # stop
                    blk.nitems_written[p] += nproduced

                # 5) 推进输入读指针（数据已经被 read() 取走，这里只记账）
                for i, n in enumerate(req):
                    blk.nitems_read[i] += n
        except Exception as e:
            self._exceptions.append(e)
            logger.exception("块 %s 线程异常", blk.name)
        finally:
            # 通知上下游本块结束
            for obuf in blk._out_bufs:
                if obuf is not None:
                    obuf.stop()

    def run(self) -> None:
        """启动所有块线程（非阻塞）。"""
        self.fg.setup()
        self._threads = []
        for blk in self.fg.blocks:
            t = threading.Thread(
                target=self._block_thread, args=(blk,),
                name=f"tpb:{blk.name}", daemon=True,
            )
            self._threads.append(t)
            t.start()

    def stop(self) -> None:
        """请求停止所有块。"""
        self._stop_evt.set()
        for blk in self.fg.blocks:
            blk.stop()

    def wait(self, timeout: Optional[float] = None) -> None:
        """等待所有块线程结束。"""
        for t in self._threads:
            t.join(timeout=timeout)
        if self._exceptions:
            raise self._exceptions[0]
