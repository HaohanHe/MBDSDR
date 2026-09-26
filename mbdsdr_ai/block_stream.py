"""
MBDSDR AI 内核 - 块流式架构（移植 GNU Radio block flowgraph）
============================================================

对照上游源码（见 docs/learn/gnuradio_gqrx.md）：

  - StreamTag     <-> gnuradio-runtime/include/gnuradio/tags.h:28-55  tag_t{offset,key,value,srcid}
  - StreamBuffer  <-> gnuradio-runtime/lib/buffer.cc:57-143           环形缓冲 + 写指针
  - StreamReader  <-> gnuradio-runtime/include/gnuradio/buffer_reader.h:226-231  多 reader 独立读位置
  - Block         <-> gnuradio-runtime/lib/block.cc:34-113            forecast/work/output_multiple/history
  - FlowGraph     <-> gnuradio-runtime/lib/flowgraph.cc:384-453      DFS 拓扑排序 + 三色判环

这是 GNU Radio "block + circular buffer + stream tags + flowgraph" 核心抽象的
Python 简化版：不做 TPB 线程调度器的复杂部分，但保留块/缓冲/标签/图执行四件套。

设计要点（与上游一致）：
  * 缓冲是有界环形，一个写者（上游块的输出），多个读者（fan-out）。
  * 每个 reader 独立维护 read_index 与 abs_read_offset（单调增）。
  * 标签按绝对样本 offset 存储、按区间查询，随数据流从输入传播到输出。
  * 图用 Kahn 拓扑排序执行；检测到环抛错（对应 flowgraph.cc:440-441）。

我们的增强：
  * FlowGraph.auto_tune()：根据输入信号统计（RMS/峰值/占空比）自动给增益/滤波块
    提参数建议（不强行改写硬件，返回建议列表，可一键 apply）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# 流标签（对照 tags.h:28-55 tag_t）
# ---------------------------------------------------------------------------
@dataclass(order=False)
class StreamTag:
    """一个贴在样本流上的键值元数据。

    Attributes:
        offset: 标签落在哪个样本上（绝对偏移，自流起点单调增）。
        key:    标签名，如 ``"freq"`` / ``"rx_rate"`` / ``"gain"`` / ``"time"``。
        value:  任意可 JSON 化的值。
        srcid:  产生该标签的块名（追踪来源，对应 tags.h:39）。
    """

    offset: int
    key: str
    value: Any
    srcid: str = ""

    def __lt__(self, other: "StreamTag") -> bool:  # 对应 tags.h:42-45
        return self.offset < other.offset

    def shifted(self, delta: int, new_srcid: str = "") -> "StreamTag":
        """返回 offset 平移 delta 后的副本（标签沿流传播时用）。"""
        return StreamTag(
            offset=self.offset + delta,
            key=self.key,
            value=self.value,
            srcid=new_srcid or self.srcid,
        )


# ---------------------------------------------------------------------------
# 环形缓冲 + 多 reader（对照 buffer.cc / buffer_reader.h）
# ---------------------------------------------------------------------------
class StreamReader:
    """环形缓冲上的一个读位置（对照 buffer_reader.h:226-231）。

    每个下游输入端口对应一个 reader，互不影响读进度；fan-out 时一个 buffer
    可挂多个 reader。
    """

    def __init__(self, buf: "StreamBuffer", preload: int = 0):
        self._buf = buf
        # d_read_index：环形内下标；d_abs_read_offset：自起点读过的总数
        self._read_idx = 0
        self._abs_read = buf._abs_write - preload

    @property
    def abs_read_offset(self) -> int:
        return self._abs_read

    def items_available(self) -> int:
        """当前可读样本数（对照 buffer_reader.h:85）。"""
        return self._buf._abs_write - self._abs_read

    def peek(self, n: int) -> np.ndarray:
        """读取（不消费）前 n 个样本，返回连续 numpy 数组副本。"""
        if n > self.items_available():
            raise ValueError(
                f"peek({n}) but only {self.items_available()} available"
            )
        buf = self._buf
        end = self._read_idx + n
        if end <= buf.capacity:
            return buf.data[self._read_idx:end].copy()
        # 跨回绕点：拼两段
        first = buf.capacity - self._read_idx
        out = np.empty(n, dtype=buf.data.dtype)
        out[:first] = buf.data[self._read_idx:]
        out[first:] = buf.data[: n - first]
        return out

    def consume(self, n: int) -> None:
        """推进读指针（对照 buffer_reader.h:108 update_read_pointer）。"""
        if n < 0:
            raise ValueError("consume n<0")
        self._read_idx = (self._read_idx + n) % self._buf.capacity
        self._abs_read += n

    def tags_in_range(self, start: int, end: int) -> List[StreamTag]:
        """返回绝对 offset ∈ [start, end) 的标签（对照 buffer_reader.h:145）。"""
        return self._buf.tags_in_range(start, end)


class StreamBuffer:
    """有界环形样本缓冲（对照 buffer.cc:57-143）。

    一个写者（上游块输出），多个读者。标签按 offset 存在有序列表里。
    """

    def __init__(self, capacity: int = 4096, dtype=np.complex64):
        if capacity < 2:
            raise ValueError("capacity must be >= 2")
        self.capacity = capacity
        self.data = np.zeros(capacity, dtype=dtype)
        self._write_idx = 0            # buffer.cc:71 d_write_index
        self._abs_write = 0            # buffer.cc:72 d_abs_write_offset
        self._tags: List[StreamTag] = []  # buffer.cc:165 d_item_tags（multimap）
        self._readers: List[StreamReader] = []
        self._done = False
        self._lock = threading.Lock()

    # -- 写侧 ---------------------------------------------------------------
    @property
    def abs_write_offset(self) -> int:
        return self._abs_write

    def add_reader(self, preload: int = 0) -> StreamReader:
        r = StreamReader(self, preload=preload)
        self._readers.append(r)
        return r

    def _slowest_reader_pos(self) -> int:
        """最落后 reader 的绝对读位置；无 reader 视为已追上写位置。"""
        if not self._readers:
            return self._abs_write
        return min(r._abs_read for r in self._readers)

    def write(self, items: np.ndarray) -> None:
        """写入 items 个样本并推进写指针（对照 buffer.cc:126-143）。

        有界：会覆盖最慢 reader 未读数据时抛 BufferError，避免静默写花。
        """
        n = len(items)
        if n == 0:
            return
        with self._lock:
            # 可用空间 = capacity - (abs_write - slowest_read)
            available = self.capacity - (self._abs_write - self._slowest_reader_pos())
            if n > available:
                raise BufferError(
                    f"StreamBuffer overflow: need {n}, have {available}; "
                    "reader falling behind"
                )
            end = self._write_idx + n
            if end <= self.capacity:
                self.data[self._write_idx:end] = items
            else:
                first = self.capacity - self._write_idx
                self.data[self._write_idx:] = items[:first]
                self.data[: n - first] = items[first:]
            self._write_idx = (self._write_idx + n) % self.capacity
            self._abs_write += n

    def add_tag(self, tag: StreamTag) -> None:
        """插入一个标签，按 offset 有序存放（对照 buffer.cc:162-166）。"""
        import bisect

        # 找到插入位置保持有序
        offsets = [t.offset for t in self._tags]
        idx = bisect.bisect_left(offsets, tag.offset)
        self._tags.insert(idx, tag)

    def tags_in_range(self, start: int, end: int) -> List[StreamTag]:
        """返回 offset ∈ [start, end) 的所有标签。"""
        import bisect

        offsets = [t.offset for t in self._tags]
        lo = bisect.bisect_left(offsets, start)
        hi = bisect.bisect_left(offsets, end)
        return list(self._tags[lo:hi])

    def prune_tags(self, max_keep: int = 4096) -> None:
        """回收过旧标签（对照 buffer.cc:168-205 prune_tags）。"""
        cutoff = self._abs_write - max_keep
        while self._tags and self._tags[0].offset < cutoff:
            self._tags.pop(0)

    def set_done(self) -> None:
        self._done = True

    @property
    def done(self) -> bool:
        return self._done


# ---------------------------------------------------------------------------
# Block 基类（对照 block.cc:34-113）
# ---------------------------------------------------------------------------
class Block:
    """信号处理块基类。

    子类实现 :meth:`work`。Source 块 ninputs=0；Sink 块 noutputs=0。

    生命周期（由 FlowGraph 调度）：
      1. ``forecast(nout)`` 问每个输入端口需要多少样本；
      2. 调度器从各输入 reader 取到所需样本，拼成连续数组；
      3. 调用 ``work(inputs, outputs)``，返回实际产出 nproduced；
      4. 调度器按 :meth:`consumed` 推进各输入 reader，并把标签从输入映射到输出。
    """

    def __init__(
        self,
        name: str,
        ninputs: int = 1,
        noutputs: int = 1,
        history: int = 1,
        output_multiple: int = 1,
    ):
        self.name = name
        self.ninputs = ninputs
        self.noutputs = noutputs
        self.history = history            # block.cc:44 d_history
        self.output_multiple = output_multiple  # block.cc:38
        # 运行期状态
        self._in_readers: List[StreamReader] = []
        self._out_buffers: List[Optional[StreamBuffer]] = [None] * noutputs
        self._pending_out_tags: List[StreamTag] = []
        self.nitems_produced = 0
        self.nitems_read = [0] * ninputs

    # -- 子类可覆写 ---------------------------------------------------------
    def forecast(self, noutput_items: int) -> List[int]:
        """要产出 noutput_items 个样本，每个输入端口需要多少（对照 block.cc:93-98）。

        默认 1:1：需要 noutput_items + history - 1。
        """
        return [noutput_items + self.history - 1] * self.ninputs

    def consumed(self, req_inputs: List[int], nproduced: int) -> List[int]:
        """本次 work 后每个输入端口实际消费多少样本。

        默认：
          * 有输出的块（1:1）：消费 nproduced 个（留下 history-1 个供下次回看）；
          * Sink 块（noutputs==0）：消费全部读到的 req_inputs（它没有产出可对齐）。
        抽取器覆写为 [nproduced * decim]，内插器覆写为 [nproduced // interp]。
        """
        if self.noutputs == 0:
            return list(req_inputs)
        return [nproduced] * self.ninputs

    def work(self, inputs: List[np.ndarray], outputs: List[np.ndarray]) -> int:
        """处理数据。outputs 已预分配为 noutput_items 长度；返回 nproduced。

        Source 块忽略 inputs；Sink 块忽略 outputs（返回 0）。
        """
        raise NotImplementedError

    # -- 标签辅助（work 内调用）--------------------------------------------
    def add_output_tag(self, rel_offset: int, key: str, value: Any) -> None:
        """在相对本次输出起点 rel_offset 处贴标签（block.cc:216 add_item_tag）。"""
        self._pending_out_tags.append(
            StreamTag(offset=rel_offset, key=key, value=value, srcid=self.name)
        )


# ---------------------------------------------------------------------------
# 几个内置参考块（确定性，便于测试）
# ---------------------------------------------------------------------------
class VectorSource(Block):
    """从给定数组无限循环吐出样本的 source。"""

    def __init__(self, name: str, vector: np.ndarray, repeat: bool = True):
        super().__init__(name, ninputs=0, noutputs=1)
        self._vec = np.asarray(vector)
        self._pos = 0
        self.repeat = repeat

    def work(self, inputs, outputs):
        n = len(outputs[0])
        out = outputs[0]
        written = 0
        while written < n:
            chunk = min(n - written, len(self._vec) - self._pos)
            out[written:written + chunk] = self._vec[self._pos:self._pos + chunk]
            written += chunk
            self._pos += chunk
            if self._pos >= len(self._vec):
                if self.repeat:
                    self._pos = 0
                else:
                    out[written:] = 0
                    break
        return written


class VectorSink(Block):
    """把收到的样本收集到 list 的 sink，便于断言。"""

    def __init__(self, name: str):
        super().__init__(name, ninputs=1, noutputs=0)
        self.collected: List[complex] = []
        self.collected_tags: List[StreamTag] = []

    def work(self, inputs, outputs):
        self.collected.extend(inputs[0].tolist())
        return 0


class MultiplyConst(Block):
    """y = c * x（1:1，确定性）。"""

    def __init__(self, name: str, c: float = 1.0):
        super().__init__(name, ninputs=1, noutputs=1)
        self.c = c

    def work(self, inputs, outputs):
        outputs[0][:] = self.c * inputs[0]
        return len(inputs[0])


class ProbeSignalStats(Block):
    """统计输入 RMS/峰值/占空比，供 AI 自动调参读取。"""

    def __init__(self, name: str):
        super().__init__(name, ninputs=1, noutputs=1)
        self.rms = 0.0
        self.peak = 0.0
        self.mean_power = 0.0

    def work(self, inputs, outputs):
        x = inputs[0]
        power = np.abs(x) ** 2
        self.mean_power = float(np.mean(power)) if len(x) else 0.0
        self.rms = float(np.sqrt(self.mean_power))
        self.peak = float(np.max(np.abs(x))) if len(x) else 0.0
        outputs[0][:] = x
        return len(x)


# ---------------------------------------------------------------------------
# FlowGraph（对照 flowgraph.cc:384-453 拓扑排序）
# ---------------------------------------------------------------------------
class FlowGraph:
    """块连接图：连接块、拓扑排序、单线程/多线程执行。"""

    def __init__(self, chunk: int = 1024):
        self.chunk = chunk
        self.blocks: List[Block] = []
        # edges: (src_block, src_port, dst_block, dst_port)
        self.edges: List[Tuple[Block, int, Block, int]] = []
        self._sorted: List[Block] = []
        # AI 自动调参钩子
        self.tune_listeners: List[Callable[[Dict[str, Any]], None]] = []

    # -- 连接 ---------------------------------------------------------------
    def connect(self, src: Block, src_port: int, dst: Block, dst_port: int) -> None:
        if src not in self.blocks:
            self.blocks.append(src)
        if dst not in self.blocks:
            self.blocks.append(dst)
        self.edges.append((src, src_port, dst, dst_port))

    # -- 拓扑排序（Kahn，对照 flowgraph.cc:384-453）------------------------
    def topological_sort(self) -> List[Block]:
        """返回 source→sink 顺序；检测到环抛 RuntimeError。"""
        indeg: Dict[Block, int] = {b: 0 for b in self.blocks}
        adj: Dict[Block, List[Block]] = {b: [] for b in self.blocks}
        for src, _, dst, _ in self.edges:
            adj[src].append(dst)
            indeg[dst] += 1

        # source 优先（对照 sort_sources_first flowgraph.cc:403）
        queue = [b for b in self.blocks if indeg[b] == 0]
        order: List[Block] = []
        while queue:
            b = queue.pop(0)
            order.append(b)
            for nxt in adj[b]:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    queue.append(nxt)
        if len(order) != len(self.blocks):
            raise RuntimeError("flow graph has loops!")  # flowgraph.cc:441
        self._sorted = order
        return order

    # -- 装配：建缓冲 + reader ---------------------------------------------
    def setup(self) -> None:
        order = self.topological_sort()
        # 先给每个输出端口建一个 StreamBuffer
        for b in order:
            for p in range(b.noutputs):
                b._out_buffers[p] = StreamBuffer(capacity=self.chunk * 8)
        # 建边：上游 buffer.add_reader 给下游输入
        for src, src_port, dst, dst_port in self.edges:
            buf = src._out_buffers[src_port]
            reader = buf.add_reader()
            dst._in_readers.insert(dst_port, reader)

    # -- 执行一步 -----------------------------------------------------------
    def _run_block(self, b: Block) -> bool:
        """执行单个块一次。返回是否产出了数据。"""
        nout = self.chunk
        # 1) forecast
        req = b.forecast(nout)
        # 2) 检查输入可用
        for i, reader in enumerate(b._in_readers):
            if reader.items_available() < req[i]:
                return False
        # 3) 取连续输入切片
        inputs = [r.peek(req[i]) for i, r in enumerate(b._in_readers)]
        # 4) 预分配输出
        outputs = [np.empty(nout, dtype=np.complex64) for _ in range(b.noutputs)]
        # 记录输入 reader 起点绝对偏移（标签传播用）
        in_start = [r.abs_read_offset for r in b._in_readers]
        out_start = [buf.abs_write_offset for buf in b._out_buffers]

        # 5) work
        b._pending_out_tags.clear()
        nproduced = b.work(inputs, outputs)
        if nproduced < 0:
            nproduced = 0
        # 对齐 output_multiple
        nproduced -= nproduced % b.output_multiple

        # 6) 写输出缓冲
        for p in range(b.noutputs):
            b._out_buffers[p].write(outputs[p][:nproduced])
        b.nitems_produced += nproduced

        # 7) 传播标签（TPP_ALL_TO_ALL：输入标签 -> 所有输出）
        if b.noutputs > 0:
            consumed = b.consumed(req, nproduced)
            for i, reader in enumerate(b._in_readers):
                for tag in reader.tags_in_range(in_start[i], in_start[i] + consumed[i]):
                    rel = tag.offset - in_start[i]
                    for p in range(b.noutputs):
                        b._out_buffers[p].add_tag(
                            StreamTag(
                                offset=out_start[p] + rel,
                                key=tag.key,
                                value=tag.value,
                                srcid=tag.srcid,
                            )
                        )
        # work 内显式贴的标签
        for tag in b._pending_out_tags:
            for p in range(b.noutputs):
                b._out_buffers[p].add_tag(
                    StreamTag(
                        offset=out_start[p] + tag.offset,
                        key=tag.key,
                        value=tag.value,
                        srcid=tag.srcid,
                    )
                )

        # 8) 推进输入 reader
        consumed = b.consumed(req, nproduced)
        for i, reader in enumerate(b._in_readers):
            reader.consume(consumed[i])
            b.nitems_read[i] += consumed[i]
        return nproduced > 0

    def run(self, max_samples: int) -> None:
        """单线程执行，直到所有 source 累计产出 >= max_samples。"""
        if not self._sorted:
            self.setup()
        produced_total = 0
        idle_rounds = 0
        while produced_total < max_samples and idle_rounds < len(self.blocks) + 2:
            progressed = False
            for b in self._sorted:
                if self._run_block(b):
                    progressed = True
                    if b.ninputs == 0:  # source
                        produced_total += self.chunk
            idle_rounds = 0 if progressed else idle_rounds + 1

    # -- AI 自动调参（我们的增强）------------------------------------------
    def attach_tune_listener(self, fn: Callable[[Dict[str, Any]], None]) -> None:
        """注册一个监听信号统计的回调，用于自动调增益/滤波参数。"""
        self.tune_listeners.append(fn)

    def auto_tune_suggestion(self, probe: ProbeSignalStats) -> Dict[str, Any]:
        """根据输入信号统计给出增益/带宽建议（不直接写硬件）。

        规则（启发式，确定性）：
          * RMS < 0.01  -> 信号弱，建议增大前端增益 +6 dB；
          * RMS > 0.5   -> 信号强/可能削顶，建议减小增益 -3 dB；
          * peak/rms > 12 -> 峰均比过高，建议收窄滤波带宽 20%；
          * 否则保持。
        """
        sug: Dict[str, Any] = {"gain_delta_db": 0.0, "bandwidth_scale": 1.0}
        if probe.rms < 0.01:
            sug["gain_delta_db"] = 6.0
        elif probe.rms > 0.5:
            sug["gain_delta_db"] = -3.0
        if probe.rms > 0 and probe.peak / probe.rms > 12.0:
            sug["bandwidth_scale"] = 0.8
        return sug
