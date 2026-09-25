"""
PingPongStream / StreamSplitter / DSPChain 单元测试
====================================================

纯 numpy 多线程测试，无硬件依赖。
对照 SDR++ stream.h / splitter.h / chain.h 的语义验证。
"""

import sys
import os
import time
import threading

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.dsp_stream import PingPongStream
from mbdsdr_ai.dsp import StreamSplitter, DSPChain, DCBlocker


# ═══════════════════════════════════════════════════════
# 1. PingPongStream 基本功能
# ═══════════════════════════════════════════════════════

class TestPingPongStreamBasic:
    def test_single_frame_producer_consumer(self):
        """生产者写一帧 → swap → 消费者 read 拿到相同数据。"""
        stream = PingPongStream(dtype=np.complex64, buffer_size=1024)

        # 生产者写入 256 个样本（直接用 complex64 避免精度差异）
        rng = np.random.default_rng(42)
        test_data = (rng.standard_normal(256) + 1j * rng.standard_normal(256)).astype(np.complex64)
        stream.write_buf[:256] = test_data
        assert stream.swap(256) is True

        # 消费者读取
        buf, n = stream.read(timeout=2.0)
        assert n == 256, f"应返回 256 个样本，实际 {n}"
        np.testing.assert_array_equal(buf[:n], test_data)

    def test_read_returns_correct_data_after_swap(self):
        """swap 后 readBuf 指向刚写入的数据。"""
        stream = PingPongStream(dtype=np.float32, buffer_size=512)

        frame1 = np.arange(256, dtype=np.float32)
        stream.write_buf[:256] = frame1
        stream.swap(256)

        buf, n = stream.read(timeout=2.0)
        assert n == 256
        np.testing.assert_array_equal(buf[:n], frame1)

    def test_flush_allows_next_swap(self):
        """flush 后生产者可以再次 swap（乒乓切换）。"""
        stream = PingPongStream(dtype=np.complex64, buffer_size=1024)

        # 第一帧
        frame1 = np.ones(128, dtype=np.complex64)
        stream.write_buf[:128] = frame1
        assert stream.swap(128) is True

        buf, n = stream.read(timeout=2.0)
        assert n == 128
        stream.flush()

        # 第二帧（验证 can_swap 已恢复）
        frame2 = np.zeros(128, dtype=np.complex64)
        stream.write_buf[:128] = frame2
        assert stream.swap(128) is True

        buf2, n2 = stream.read(timeout=2.0)
        assert n2 == 128
        np.testing.assert_array_equal(buf2[:n2], frame2)

    def test_stop_reader_unblocks_read(self):
        """stop_reader() 应唤醒阻塞在 read() 的消费者。"""
        stream = PingPongStream(dtype=np.complex64, buffer_size=256)

        result = {}
        def consumer():
            result['buf'], result['n'] = stream.read()

        t = threading.Thread(target=consumer, daemon=True)
        t.start()
        time.sleep(0.1)  # 确保消费者已进入 read 阻塞

        stream.stop_reader()
        t.join(timeout=2.0)

        assert result['n'] == -1, f"reader 停止后应返回 -1，实际 {result['n']}"

    def test_stop_writer_unblocks_swap(self):
        """stop_writer() 应唤醒阻塞在 swap() 的生产者。"""
        stream = PingPongStream(dtype=np.complex64, buffer_size=256)

        # 先 swap 一帧，让 can_swap = False
        stream.write_buf[:10] = np.ones(10, dtype=np.complex64)
        stream.swap(10)
        # 此时 can_swap = False，下一次 swap 会阻塞

        result = {}
        def producer():
            stream.write_buf[:10] = np.zeros(10, dtype=np.complex64)
            result['ok'] = stream.swap(10)

        t = threading.Thread(target=producer, daemon=True)
        t.start()
        time.sleep(0.1)  # 确保生产者已进入 swap 阻塞

        stream.stop_writer()
        t.join(timeout=2.0)

        assert result['ok'] is False, "writer 停止后 swap 应返回 False"

    def test_clear_stop_resumes_stream(self):
        """clear_stop() 后流可重新使用。"""
        stream = PingPongStream(dtype=np.complex64, buffer_size=256)
        stream.stop_writer()
        stream.stop_reader()
        stream.clear_stop()

        # 验证可以正常生产消费
        stream.write_buf[:64] = np.ones(64, dtype=np.complex64)
        assert stream.swap(64) is True
        buf, n = stream.read(timeout=2.0)
        assert n == 64


# ═══════════════════════════════════════════════════════
# 2. PingPongStream 多线程压力测试
# ═══════════════════════════════════════════════════════

class TestPingPongStreamMultithread:
    def test_continuous_producer_consumer_no_loss_no_dup(self):
        """生产者持续写，消费者持续读，数据不丢不重。"""
        stream = PingPongStream(dtype=np.complex64, buffer_size=4096)
        frame_size = 512
        total_frames = 50

        received_frames = []
        errors = []

        def producer():
            for i in range(total_frames):
                # 每帧用序号标记前几个样本，便于校验
                data = np.full(frame_size, i, dtype=np.complex64)
                stream.write_buf[:frame_size] = data
                ok = stream.swap(frame_size)
                if not ok:
                    errors.append("swap failed")
                    return

        def consumer():
            for i in range(total_frames):
                buf, n = stream.read(timeout=5.0)
                if n < 0:
                    errors.append("read returned -1")
                    return
                # 记录帧序号（第一个样本的实部）
                received_frames.append(int(buf[0].real))
                stream.flush()

        pt = threading.Thread(target=producer, daemon=True)
        ct = threading.Thread(target=consumer, daemon=True)
        pt.start()
        ct.start()
        pt.join(timeout=10.0)
        ct.join(timeout=10.0)

        assert not errors, f"线程错误: {errors}"
        assert len(received_frames) == total_frames, \
            f"应收到 {total_frames} 帧，实际 {len(received_frames)}"
        # 帧序号应严格递增，不丢不重
        expected = list(range(total_frames))
        assert received_frames == expected, \
            f"帧序列错误：期望 {expected[:10]}...，实际 {received_frames[:10]}..."

    def test_producer_faster_than_consumer_backpressure(self):
        """生产者比消费者快时，通过 swap 阻塞实现背压。"""
        stream = PingPongStream(dtype=np.complex64, buffer_size=2048)
        frame_size = 256
        total_frames = 20

        received = []
        errors = []

        def producer():
            for i in range(total_frames):
                stream.write_buf[:frame_size] = i
                ok = stream.swap(frame_size)
                if not ok:
                    errors.append("swap stopped")
                    return

        def consumer():
            # 消费者故意慢一点
            while len(received) < total_frames:
                buf, n = stream.read(timeout=5.0)
                if n < 0:
                    return
                received.append(int(buf[0].real))
                time.sleep(0.005)  # 模拟处理延迟
                stream.flush()

        pt = threading.Thread(target=producer, daemon=True)
        ct = threading.Thread(target=consumer, daemon=True)
        pt.start()
        ct.start()
        pt.join(timeout=15.0)
        ct.join(timeout=15.0)

        assert not errors, f"线程错误: {errors}"
        assert len(received) == total_frames, \
            f"应收到 {total_frames} 帧，实际 {len(received)}"
        assert received == list(range(total_frames))


# ═══════════════════════════════════════════════════════
# 3. StreamSplitter 测试
# ═══════════════════════════════════════════════════════

class TestStreamSplitter:
    def test_one_in_two_out_same_data(self):
        """1 进 2 出，两个下游拿到相同数据拷贝。"""
        # 输入流
        in_stream = PingPongStream(dtype=np.complex64, buffer_size=1024)

        # 两个输出流
        out1 = PingPongStream(dtype=np.complex64, buffer_size=1024)
        out2 = PingPongStream(dtype=np.complex64, buffer_size=1024)

        splitter = StreamSplitter(in_stream)
        splitter.bind(out1)
        splitter.bind(out2)

        # 生产者写输入流
        rng = np.random.default_rng(123)
        test_data = (rng.standard_normal(512) + 1j * rng.standard_normal(512)).astype(np.complex64)
        in_stream.write_buf[:512] = test_data
        in_stream.swap(512)

        # 手动跑一次 run_once（不用后台线程）
        n = splitter.run_once()
        assert n == 512

        # 两个下游都应拿到相同数据
        buf1, n1 = out1.read(timeout=2.0)
        assert n1 == 512
        np.testing.assert_array_equal(buf1[:n1], test_data)

        buf2, n2 = out2.read(timeout=2.0)
        assert n2 == 512
        np.testing.assert_array_equal(buf2[:n2], test_data)

    def test_unbind_removes_output(self):
        """unbind 后下游不再收到数据。"""
        in_stream = PingPongStream(dtype=np.complex64, buffer_size=1024)
        out1 = PingPongStream(dtype=np.complex64, buffer_size=1024)
        out2 = PingPongStream(dtype=np.complex64, buffer_size=1024)

        splitter = StreamSplitter(in_stream)
        splitter.bind(out1)
        splitter.bind(out2)
        splitter.unbind(out2)

        # 写一帧
        test_data = np.ones(256, dtype=np.complex64)
        in_stream.write_buf[:256] = test_data
        in_stream.swap(256)
        splitter.run_once()

        # out1 应有数据
        buf1, n1 = out1.read(timeout=2.0)
        assert n1 == 256

        # out2 应超时无数据（read 返回 0）
        buf2, n2 = out2.read(timeout=0.5)
        assert n2 == 0, f"unbind 后不应有数据，实际 n={n2}"

    def test_splitter_with_background_thread(self):
        """后台线程模式下多帧连续分发（生产/消费并发运行）。"""
        in_stream = PingPongStream(dtype=np.complex64, buffer_size=2048)
        out1 = PingPongStream(dtype=np.complex64, buffer_size=2048)
        out2 = PingPongStream(dtype=np.complex64, buffer_size=2048)

        splitter = StreamSplitter(in_stream)
        splitter.bind(out1)
        splitter.bind(out2)
        splitter.start()

        total_frames = 10
        frame_size = 256
        out1_frames = []
        out2_frames = []
        errors = []

        def producer():
            for i in range(total_frames):
                data = np.full(frame_size, i, dtype=np.complex64)
                in_stream.write_buf[:frame_size] = data
                ok = in_stream.swap(frame_size)
                if not ok:
                    errors.append("producer swap stopped")
                    return

        def consumer1():
            for _ in range(total_frames):
                buf, n = out1.read(timeout=5.0)
                if n < 0:
                    return
                if n > 0:
                    out1_frames.append(int(buf[0].real))
                    out1.flush()

        def consumer2():
            for _ in range(total_frames):
                buf, n = out2.read(timeout=5.0)
                if n < 0:
                    return
                if n > 0:
                    out2_frames.append(int(buf[0].real))
                    out2.flush()

        pt = threading.Thread(target=producer, daemon=True)
        c1t = threading.Thread(target=consumer1, daemon=True)
        c2t = threading.Thread(target=consumer2, daemon=True)
        pt.start()
        c1t.start()
        c2t.start()

        pt.join(timeout=10.0)
        c1t.join(timeout=10.0)
        c2t.join(timeout=10.0)

        splitter.stop()

        assert not errors, f"线程错误: {errors}"
        assert len(out1_frames) == total_frames, \
            f"out1 应收到 {total_frames} 帧，实际 {len(out1_frames)}: {out1_frames}"
        assert len(out2_frames) == total_frames, \
            f"out2 应收到 {total_frames} 帧，实际 {len(out2_frames)}: {out2_frames}"
        assert out1_frames == list(range(total_frames)), out1_frames
        assert out2_frames == list(range(total_frames)), out2_frames


# ═══════════════════════════════════════════════════════
# 4. DSPChain 测试
# ═══════════════════════════════════════════════════════

class _GainBlock:
    """测试用增益块（简单 block）。"""
    def __init__(self, gain: float):
        self.gain = gain

    def process(self, x: np.ndarray) -> np.ndarray:
        return x * self.gain

    def reset(self):
        pass


class TestDSPChain:
    def test_chain_all_enabled(self):
        """所有块启用时，按顺序串联处理。"""
        chain = DSPChain()
        chain.add_block("gain2", _GainBlock(2.0))
        chain.add_block("gain3", _GainBlock(3.0))

        x = np.ones(10, dtype=np.float32)
        result = chain.process(x)
        # 1 * 2 * 3 = 6
        np.testing.assert_array_almost_equal(result, np.full(10, 6.0))

    def test_chain_disable_block(self):
        """禁用某个块后，该块被跳过。"""
        chain = DSPChain()
        chain.add_block("gain2", _GainBlock(2.0))
        chain.add_block("gain3", _GainBlock(3.0))

        # 禁用中间的 gain3
        chain.set_block_enabled("gain3", False)

        x = np.ones(10, dtype=np.float32)
        result = chain.process(x)
        # 只经过 gain2: 1 * 2 = 2
        np.testing.assert_array_almost_equal(result, np.full(10, 2.0))

    def test_chain_enable_disable_toggle(self):
        """反复切换块的启用/禁用状态。"""
        chain = DSPChain()
        chain.add_block("gain10", _GainBlock(10.0))

        x = np.ones(5, dtype=np.float32)

        # 启用
        chain.set_block_enabled("gain10", True)
        np.testing.assert_array_almost_equal(chain.process(x), np.full(5, 10.0))

        # 禁用
        chain.set_block_enabled("gain10", False)
        np.testing.assert_array_almost_equal(chain.process(x), np.ones(5))

        # 再启用
        chain.set_block_enabled("gain10", True)
        np.testing.assert_array_almost_equal(chain.process(x), np.full(5, 10.0))

    def test_chain_with_dc_blocker(self):
        """集成测试：DCBlocker 在链中可独立开关。"""
        chain = DSPChain()
        chain.add_block("dc", DCBlocker(r=0.99))

        # 有直流偏置的信号
        x = np.ones(1000, dtype=np.complex64) + 0.5j

        # 启用 DC 阻断
        result_enabled = chain.process(x)
        # DCBlocker 后均值应接近 0
        assert np.abs(np.mean(result_enabled.real)) < 0.1, \
            f"启用 DC 阻断后实部均值应接近 0，实际 {np.mean(result_enabled.real)}"

        # 禁用 DC 阻断
        chain.set_block_enabled("dc", False)
        result_disabled = chain.process(x)
        np.testing.assert_array_equal(result_disabled, x)

    def test_chain_unknown_block_raises(self):
        """操作不存在的块应报错。"""
        chain = DSPChain()
        with pytest.raises(ValueError):
            chain.set_block_enabled("nonexistent", True)

    def test_chain_reset_calls_block_reset(self):
        """reset() 应调用所有块的 reset()。"""
        chain = DSPChain()
        dc = DCBlocker(r=0.99)
        chain.add_block("dc", dc)

        # 先处理一些数据，改变内部状态
        x = np.ones(100, dtype=np.complex64)
        chain.process(x)
        assert dc._x_prev != 0.0 or dc._y_prev != 0.0

        # reset 后状态归零
        chain.reset()
        assert dc._x_prev == 0.0
        assert dc._y_prev == 0.0
