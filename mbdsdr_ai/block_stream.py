# SPDX-License-Identifier: MIT
"""
A small block-stream processing framework.

This module provides the minimal machinery needed to wire independent signal
processing *blocks* together into a data-flow graph and to execute it:

  * :class:`StreamTag`     a key/value annotation pinned to a sample offset
  * :class:`StreamBuffer`  a bounded ring buffer fed by one writer, read by many
  * :class:`StreamReader`  an independent read position on a ring buffer
  * :class:`Block`         a processing unit with ``forecast``/``work``/``consumed``
  * :class:`FlowGraph`     builds the graph, topologically sorts it, and runs it

The design follows the classic block-diagram streaming model that is common in
software-defined-radio and audio frameworks: buffers carry samples, tags travel
alongside the sample stream, and the scheduler walks the graph from sources to
sinks.  This implementation is deliberately lightweight (single-threaded, in
NumPy) and keeps no reference to any external project's source layout.

Key invariants:
  * A buffer is a bounded ring with one writer (an upstream block output) and
    many readers (fan-out); writing over the slowest reader raises instead of
    silently corrupting data.
  * Every reader keeps its own ring index and a monotonically increasing
    absolute sample offset.
  * Tags are stored ordered by absolute offset and queried by offset range;
    they are propagated from a block's inputs to its outputs.
  * The graph is ordered with Kahn's algorithm; a cycle raises ``RuntimeError``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Stream tags
# ---------------------------------------------------------------------------
@dataclass(order=False)
class StreamTag:
    """A key/value annotation attached to a position in the sample stream.

    Attributes:
        offset: absolute sample index this tag refers to (monotonic from start).
        key:    tag name, e.g. ``"freq"`` / ``"rx_rate"`` / ``"gain"`` / ``"time"``.
        value:  any JSON-serialisable value.
        srcid:  name of the block that produced the tag (provenance).
    """

    offset: int
    key: str
    value: Any
    srcid: str = ""

    def __lt__(self, other: "StreamTag") -> bool:
        return self.offset < other.offset

    def shifted(self, delta: int, new_srcid: str = "") -> "StreamTag":
        """Return a copy whose offset is shifted by ``delta`` (used when tags
        propagate through a resampling or offsetting block)."""
        return StreamTag(
            offset=self.offset + delta,
            key=self.key,
            value=self.value,
            srcid=new_srcid or self.srcid,
        )


# ---------------------------------------------------------------------------
# Ring buffer and its readers
# ---------------------------------------------------------------------------
class StreamReader:
    """One independent read position on a :class:`StreamBuffer`.

    Each downstream input port owns a reader; multiple readers may attach to a
    single buffer for fan-out and advance at their own pace.
    """

    def __init__(self, buf: "StreamBuffer", preload: int = 0):
        self._buf = buf
        # Ring index within the buffer, and total samples consumed since start.
        self._read_idx = 0
        self._abs_read = buf._abs_write - preload

    @property
    def abs_read_offset(self) -> int:
        return self._abs_read

    def items_available(self) -> int:
        """Number of samples currently readable through this reader."""
        return self._buf._abs_write - self._abs_read

    def peek(self, n: int) -> np.ndarray:
        """Read (without consuming) the next ``n`` samples as a contiguous copy."""
        if n > self.items_available():
            raise ValueError(
                f"peek({n}) but only {self.items_available()} available"
            )
        buf = self._buf
        end = self._read_idx + n
        if end <= buf.capacity:
            return buf.data[self._read_idx:end].copy()
        # Wrap around the ring boundary: concatenate the two segments.
        first = buf.capacity - self._read_idx
        out = np.empty(n, dtype=buf.data.dtype)
        out[:first] = buf.data[self._read_idx:]
        out[first:] = buf.data[: n - first]
        return out

    def consume(self, n: int) -> None:
        """Advance the read position by ``n`` samples."""
        if n < 0:
            raise ValueError("consume n<0")
        self._read_idx = (self._read_idx + n) % self._buf.capacity
        self._abs_read += n

    def tags_in_range(self, start: int, end: int) -> List[StreamTag]:
        """Return tags whose offset lies in ``[start, end)``."""
        return self._buf.tags_in_range(start, end)


class StreamBuffer:
    """A bounded ring buffer of samples.

    One writer (an upstream block output) and any number of readers.  Tags are
    kept in an offset-ordered list.
    """

    def __init__(self, capacity: int = 4096, dtype=np.complex64):
        if capacity < 2:
            raise ValueError("capacity must be >= 2")
        self.capacity = capacity
        self.data = np.zeros(capacity, dtype=dtype)
        self._write_idx = 0            # ring index of the next write slot
        self._abs_write = 0            # total samples written since start
        self._tags: List[StreamTag] = []  # offset-ordered tag list
        self._readers: List[StreamReader] = []
        self._done = False
        self._lock = threading.Lock()

    @property
    def abs_write_offset(self) -> int:
        return self._abs_write

    def add_reader(self, preload: int = 0) -> StreamReader:
        r = StreamReader(self, preload=preload)
        self._readers.append(r)
        return r

    def _slowest_reader_pos(self) -> int:
        """Absolute read position of the lagging-most reader; if no reader is
        attached we treat it as already caught up with the writer."""
        if not self._readers:
            return self._abs_write
        return min(r._abs_read for r in self._readers)

    def write(self, items: np.ndarray) -> None:
        """Write samples and advance the write pointer.

        Raises ``BufferError`` if the write would overwrite data the slowest
        reader has not yet consumed, rather than silently corrupting it.
        """
        n = len(items)
        if n == 0:
            return
        with self._lock:
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
        """Insert a tag, keeping the tag list offset-ordered."""
        import bisect

        offsets = [t.offset for t in self._tags]
        idx = bisect.bisect_left(offsets, tag.offset)
        self._tags.insert(idx, tag)

    def tags_in_range(self, start: int, end: int) -> List[StreamTag]:
        """Return all tags whose offset lies in ``[start, end)``."""
        import bisect

        offsets = [t.offset for t in self._tags]
        lo = bisect.bisect_left(offsets, start)
        hi = bisect.bisect_left(offsets, end)
        return list(self._tags[lo:hi])

    def prune_tags(self, max_keep: int = 4096) -> None:
        """Drop tags older than the most recent ``max_keep`` samples."""
        cutoff = self._abs_write - max_keep
        while self._tags and self._tags[0].offset < cutoff:
            self._tags.pop(0)

    def set_done(self) -> None:
        self._done = True

    @property
    def done(self) -> bool:
        return self._done


# ---------------------------------------------------------------------------
# Block base class
# ---------------------------------------------------------------------------
class Block:
    """Base class for a signal processing block.

    Subclasses implement :meth:`work`.  Source blocks have ``ninputs == 0``;
    sink blocks have ``noutputs == 0``.

    Scheduling contract (driven by :class:`FlowGraph`):
      1. ``forecast(nout)`` reports how many samples each input port needs to
         produce ``nout`` output samples;
      2. the scheduler assembles those samples from the input readers;
      3. ``work(inputs, outputs)`` runs and returns the number produced;
      4. the scheduler advances the input readers by ``consumed(...)`` and maps
         input tags onto the output offsets.
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
        self.history = history
        self.output_multiple = output_multiple
        # Runtime wiring state.
        self._in_readers: List[StreamReader] = []
        self._out_buffers: List[Optional[StreamBuffer]] = [None] * noutputs
        self._pending_out_tags: List[StreamTag] = []
        self.nitems_produced = 0
        self.nitems_read = [0] * ninputs

    # -- subclass hooks ------------------------------------------------------
    def forecast(self, noutput_items: int) -> List[int]:
        """Samples needed on each input port to emit ``noutput_items``.

        Default is 1:1 and asks for ``noutput_items + history - 1``.
        """
        return [noutput_items + self.history - 1] * self.ninputs

    def consumed(self, req_inputs: List[int], nproduced: int) -> List[int]:
        """How many samples each input port actually consumed this call.

        Default:
          * a block with outputs (1:1) consumes ``nproduced`` (leaving
            ``history-1`` samples for the next look-back);
          * a sink (``noutputs == 0``) consumes everything it was given.
        Decimators override this to ``nproduced * decim``; interpolators to
        ``nproduced // interp``.
        """
        if self.noutputs == 0:
            return list(req_inputs)
        return [nproduced] * self.ninputs

    def work(self, inputs: List[np.ndarray], outputs: List[np.ndarray]) -> int:
        """Process data; ``outputs`` are pre-sized to ``noutput_items``.

        Return the number of samples actually produced.  Source blocks ignore
        ``inputs``; sink blocks ignore ``outputs`` (return 0).
        """
        raise NotImplementedError

    # -- tag helper (call inside work) --------------------------------------
    def add_output_tag(self, rel_offset: int, key: str, value: Any) -> None:
        """Emit a tag at ``rel_offset`` relative to the start of this output."""
        self._pending_out_tags.append(
            StreamTag(offset=rel_offset, key=key, value=value, srcid=self.name)
        )


# ---------------------------------------------------------------------------
# Built-in reference blocks (deterministic, for testing)
# ---------------------------------------------------------------------------
class VectorSource(Block):
    """A source that emits a fixed vector, repeating it forever (or once)."""

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
    """A sink that collects received samples into a Python list (for assertions)."""

    def __init__(self, name: str):
        super().__init__(name, ninputs=1, noutputs=0)
        self.collected: List[complex] = []
        self.collected_tags: List[StreamTag] = []

    def work(self, inputs, outputs):
        self.collected.extend(inputs[0].tolist())
        return 0


class MultiplyConst(Block):
    """y = c * x (1:1, deterministic)."""

    def __init__(self, name: str, c: float = 1.0):
        super().__init__(name, ninputs=1, noutputs=1)
        self.c = c

    def work(self, inputs, outputs):
        outputs[0][:] = self.c * inputs[0]
        return len(inputs[0])


class ProbeSignalStats(Block):
    """Measure input RMS / peak / duty-cycle for an auto-tuning advisor."""

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
# Flow graph
# ---------------------------------------------------------------------------
class FlowGraph:
    """Wires blocks together, topologically sorts them, and executes them."""

    def __init__(self, chunk: int = 1024):
        self.chunk = chunk
        self.blocks: List[Block] = []
        # edges: (src_block, src_port, dst_block, dst_port)
        self.edges: List[Tuple[Block, int, Block, int]] = []
        self._sorted: List[Block] = []
        # Auto-tuning hooks.
        self.tune_listeners: List[Callable[[Dict[str, Any]], None]] = []

    # -- wiring -------------------------------------------------------------
    def connect(self, src: Block, src_port: int, dst: Block, dst_port: int) -> None:
        if src not in self.blocks:
            self.blocks.append(src)
        if dst not in self.blocks:
            self.blocks.append(dst)
        self.edges.append((src, src_port, dst, dst_port))

    # -- topological order (Kahn's algorithm) ------------------------------
    def topological_sort(self) -> List[Block]:
        """Return a source-to-sink ordering; raise ``RuntimeError`` on a cycle."""
        indeg: Dict[Block, int] = {b: 0 for b in self.blocks}
        adj: Dict[Block, List[Block]] = {b: [] for b in self.blocks}
        for src, _, dst, _ in self.edges:
            adj[src].append(dst)
            indeg[dst] += 1

        # Start from source blocks (zero in-degree) first.
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
            raise RuntimeError("flow graph has loops!")
        self._sorted = order
        return order

    # -- assembly: allocate buffers and readers -----------------------------
    def setup(self) -> None:
        order = self.topological_sort()
        # One ring buffer per output port.
        for b in order:
            for p in range(b.noutputs):
                b._out_buffers[p] = StreamBuffer(capacity=self.chunk * 8)
        # Each edge attaches a reader on the upstream buffer to the downstream port.
        for src, src_port, dst, dst_port in self.edges:
            buf = src._out_buffers[src_port]
            reader = buf.add_reader()
            dst._in_readers.insert(dst_port, reader)

    # -- single-block execution step ---------------------------------------
    def _run_block(self, b: Block) -> bool:
        """Run one block once. Returns whether it produced any data."""
        nout = self.chunk
        # 1) ask how much input this block needs
        req = b.forecast(nout)
        # 2) wait for the required input on every input port
        for i, reader in enumerate(b._in_readers):
            if reader.items_available() < req[i]:
                return False
        # 3) take contiguous input slices
        inputs = [r.peek(req[i]) for i, r in enumerate(b._in_readers)]
        # 4) pre-allocate output
        outputs = [np.empty(nout, dtype=np.complex64) for _ in range(b.noutputs)]
        # record reader/buffer positions for tag propagation
        in_start = [r.abs_read_offset for r in b._in_readers]
        out_start = [buf.abs_write_offset for buf in b._out_buffers]

        # 5) run the block
        b._pending_out_tags.clear()
        nproduced = b.work(inputs, outputs)
        if nproduced < 0:
            nproduced = 0
        nproduced -= nproduced % b.output_multiple

        # 6) write into output buffers
        for p in range(b.noutputs):
            b._out_buffers[p].write(outputs[p][:nproduced])
        b.nitems_produced += nproduced

        # 7) propagate input tags onto every output port
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
        # output tags the block emitted explicitly inside work()
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

        # 8) advance input readers
        consumed = b.consumed(req, nproduced)
        for i, reader in enumerate(b._in_readers):
            reader.consume(consumed[i])
            b.nitems_read[i] += consumed[i]
        return nproduced > 0

    def run(self, max_samples: int) -> None:
        """Single-threaded run until sources have produced at least ``max_samples``."""
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

    # -- auto-tuning advisor ------------------------------------------------
    def attach_tune_listener(self, fn: Callable[[Dict[str, Any]], None]) -> None:
        """Register a callback that receives signal statistics for auto-tuning."""
        self.tune_listeners.append(fn)

    def auto_tune_suggestion(self, probe: ProbeSignalStats) -> Dict[str, Any]:
        """Heuristic gain/bandwidth advice from signal statistics (advisory only).

        Rules (deterministic):
          * RMS < 0.01    -> weak signal, suggest +6 dB front-end gain;
          * RMS > 0.5     -> strong/overdriven, suggest -3 dB gain;
          * peak/rms > 12 -> high crest factor, suggest narrowing bandwidth 20%;
          * otherwise keep settings.
        """
        sug: Dict[str, Any] = {"gain_delta_db": 0.0, "bandwidth_scale": 1.0}
        if probe.rms < 0.01:
            sug["gain_delta_db"] = 6.0
        elif probe.rms > 0.5:
            sug["gain_delta_db"] = -3.0
        if probe.rms > 0 and probe.peak / probe.rms > 12.0:
            sug["bandwidth_scale"] = 0.8
        return sug
