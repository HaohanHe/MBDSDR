# SPDX-License-Identifier: MIT
"""
A thread-per-block streaming flow-graph runtime.

This runtime executes a graph of signal-processing blocks with one OS thread per
block.  Blocks exchange samples through bounded ring buffers that apply
back-pressure: a writer blocks when the buffer is full, a reader blocks when it
is empty, and a ``stop()`` wakes everyone up so threads can exit cleanly.

Components:
  * :class:`TpbBuffer`   condition-variable bounded ring buffer (blocking I/O)
  * :class:`TpbBlock`    processing unit with ``forecast``/``work`` hooks
  * :class:`FlowGraph`   wiring, cycle/unconnected-port validation, and
                         Kahn topological ordering; independent sub-graphs can
                         be grouped with :meth:`FlowGraph.partition`
  * :class:`Scheduler`   launches one thread per block and drives the
                         forecast -> read input -> work -> write output loop

The runtime is written from scratch in Python (NumPy + ``threading``); blocks
are plain Python objects.  It does not rely on any external project's code.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Thread-safe blocking ring buffer
# ---------------------------------------------------------------------------
class TpbBuffer:
    """Bounded ring buffer that blocks on a full write / empty read.

    One writer (an upstream block thread) and one or more readers (downstream
    block threads).  Unlike a non-blocking queue, a full write waits on a
    ``not_full`` condition and an empty read waits on ``not_empty``; ``stop()``
    broadcasts both so all waiters can leave.
    """

    def __init__(self, capacity: int = 4096, dtype=np.complex64):
        if capacity < 2:
            raise ValueError("capacity must be >= 2")
        self.capacity = capacity
        self.data = np.zeros(capacity, dtype=dtype)
        self._w = 0            # ring write index
        self._r = 0            # ring read index
        self._count = 0        # number of samples currently buffered
        self._lock = threading.Lock()
        self._not_full = threading.Condition(self._lock)
        self._not_empty = threading.Condition(self._lock)
        self._stopped = False

    def write(self, items: np.ndarray, timeout: Optional[float] = None) -> bool:
        """Append ``items``; block while the buffer is full.

        Returns ``False`` if woken by ``stop()`` rather than completing the
        write.
        """
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
        """Read up to ``n`` samples; block until they are available.

        Returns ``None`` if woken by ``stop()``.
        """
        if n <= 0:
            return np.empty(0, dtype=self.data.dtype)
        deadline = None if timeout is None else time.time() + timeout
        with self._lock:
            while self._count < n:
                remaining = None
                if deadline is not None:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        # Time out: return whatever is buffered.
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
        """Wake every waiter so its loop can exit."""
        with self._lock:
            self._stopped = True
            self._not_full.notify_all()
            self._not_empty.notify_all()


# ---------------------------------------------------------------------------
# Block base class
# ---------------------------------------------------------------------------
class TpbBlock:
    """Base class for a processing block; subclasses override :meth:`work`.

    Per-thread lifecycle (driven by :class:`Scheduler`):
      1. ``forecast(nout)`` reports required input per port;
      2. read enough samples from each input buffer;
      3. run ``work(inputs, outputs)``;
      4. write the produced samples into each output buffer (blocking on space).
    """

    def __init__(self, name: str, ninputs: int = 1, noutputs: int = 1):
        self.name = name
        self.ninputs = ninputs
        self.noutputs = noutputs
        # Runtime wiring (filled by FlowGraph.setup).
        self._in_bufs: List[Optional[TpbBuffer]] = [None] * ninputs
        self._out_bufs: List[Optional[TpbBuffer]] = [None] * noutputs
        # Statistics.
        self.nitems_read = [0] * ninputs
        self.nitems_written = [0] * noutputs
        self._stop = threading.Event()

    # -- subclass hooks ------------------------------------------------------
    def forecast(self, noutput_items: int) -> List[int]:
        """Samples needed on each input port to emit ``noutput_items``."""
        return [noutput_items] * self.ninputs

    def work(self, inputs: List[np.ndarray], outputs: List[np.ndarray]) -> int:
        """Process data; ``outputs`` are pre-allocated. Return samples produced."""
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
# Built-in reference blocks (deterministic)
# ---------------------------------------------------------------------------
class VectorSource(TpbBlock):
    """A source (ninputs=0) that emits a fixed vector, repeating it."""

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
    """A sink (noutputs=0) that collects samples into a list."""

    def __init__(self, name: str):
        super().__init__(name, ninputs=1, noutputs=0)
        self.collected: List[complex] = []

    def work(self, inputs, outputs):
        self.collected.extend(inputs[0].tolist())
        return 0


class MultiplyConst(TpbBlock):
    """y = c * x (1:1)."""

    def __init__(self, name: str, c: complex = 1.0 + 0j):
        super().__init__(name, ninputs=1, noutputs=1)
        self.c = complex(c)

    def work(self, inputs, outputs):
        outputs[0][:] = self.c * inputs[0]
        return len(inputs[0])


# ---------------------------------------------------------------------------
# Flow graph: wiring / validation / ordering
# ---------------------------------------------------------------------------
class FlowGraph:
    """A graph of connected blocks."""

    def __init__(self, buffer_capacity: int = 4096, chunk: int = 1024):
        self.buffer_capacity = buffer_capacity
        self.chunk = chunk
        self.blocks: List[TpbBlock] = []
        # edges: (src_block, src_port, dst_block, dst_port)
        self.edges: List[Tuple[TpbBlock, int, TpbBlock, int]] = []

    # -- wiring --------------------------------------------------------------
    def add_block(self, blk: TpbBlock) -> None:
        if blk not in self.blocks:
            self.blocks.append(blk)

    def connect(self, src: TpbBlock, src_port: int, dst: TpbBlock, dst_port: int) -> None:
        if src not in self.blocks:
            self.add_block(src)
        if dst not in self.blocks:
            self.add_block(dst)
        self.edges.append((src, src_port, dst, dst_port))

    # -- validation ----------------------------------------------------------
    def validate(self) -> None:
        """Detect cycles and unconnected ports."""
        if not self.blocks:
            return
        # Three-colour DFS cycle detection.
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

        # Unconnected port check.
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

    # -- topological order ---------------------------------------------------
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

    # -- split into independent connected components -------------------------
    def partition(self) -> List[List[TpbBlock]]:
        """Group blocks into connected components (independent source->sink chains).

        Independent components may run in separate thread groups.
        """
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

    # -- assembly: allocate buffers and wire ports ---------------------------
    def setup(self) -> None:
        self.validate()
        # One ring buffer per output port.
        for b in self.blocks:
            for p in range(b.noutputs):
                b._out_bufs[p] = TpbBuffer(capacity=self.buffer_capacity)
        # Each edge pairs the upstream output buffer with the downstream input.
        for src, src_port, dst, dst_port in self.edges:
            dst._in_bufs[dst_port] = src._out_bufs[src_port]


# ---------------------------------------------------------------------------
# Scheduler: one thread per block
# ---------------------------------------------------------------------------
class Scheduler:
    """Runs the graph with one thread per block.

    Each block thread loops: forecast -> wait for input -> work -> write output,
    until stopped.
    """

    def __init__(self, fg: FlowGraph):
        self.fg = fg
        self._threads: List[threading.Thread] = []
        self._stop_evt = threading.Event()
        self._exceptions: List[BaseException] = []

    def _block_thread(self, blk: TpbBlock) -> None:
        """Main loop of one block thread."""
        try:
            while not self._stop_evt.is_set() and not blk._stop.is_set():
                # 1) read required input (sources need no input)
                if blk.ninputs > 0:
                    nout = self.fg.chunk
                    req = blk.forecast(nout)
                    inputs: List[np.ndarray] = []
                    ok = True
                    for i, ibuf in enumerate(blk._in_bufs):
                        assert ibuf is not None
                        data = ibuf.read(req[i], timeout=0.2)
                        if data is None:
                            ok = False
                            break
                        if len(data) < req[i]:
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

                # 2) pre-allocate output
                outputs = [
                    np.empty(nout, dtype=np.complex64) for _ in range(blk.noutputs)
                ]

                # 3) run the block
                nproduced = blk.work(inputs, outputs)
                if nproduced < 0:
                    nproduced = 0

                # 4) write output (blocks when downstream buffer is full)
                for p in range(blk.noutputs):
                    obuf = blk._out_bufs[p]
                    assert obuf is not None
                    out_data = outputs[p][:nproduced]
                    if len(out_data) > 0:
                        ok = obuf.write(out_data, timeout=1.0)
                        if not ok:
                            return  # stop
                    blk.nitems_written[p] += nproduced

                # 5) bookkeeping for consumed input (read() already moved the pointer)
                for i, n in enumerate(req):
                    blk.nitems_read[i] += n
        except Exception as e:
            self._exceptions.append(e)
            logger.exception("block %s thread exception", blk.name)
        finally:
            for obuf in blk._out_bufs:
                if obuf is not None:
                    obuf.stop()

    def run(self) -> None:
        """Launch all block threads (non-blocking)."""
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
        """Request that all blocks stop."""
        self._stop_evt.set()
        for blk in self.fg.blocks:
            blk.stop()

    def wait(self, timeout: Optional[float] = None) -> None:
        """Wait for all block threads to finish."""
        for t in self._threads:
            t.join(timeout=timeout)
        if self._exceptions:
            raise self._exceptions[0]
