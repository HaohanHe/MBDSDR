# SPDX-License-Identifier: MIT
"""APRS 实时解调的跨块连续性（S4）：滑动窗口，不是逐块独立解调。

修的是什么
----------
`aprs_panel.feed_audio()` 以前对**每一块音频独立调用** `AFSKModem.demodulate()`。
而那个函数的位定时（PLL）是从零起相位的，它假设输入是一段完整独立的录音。

实时喂进来的音频是分块的（典型 1024–8192 样本 @48k ≈ 21–170 ms），
而一个 AX.25 帧约 300–500 ms（含前导）——**几乎每个帧都跨块**。
于是"看着在实时跑、实际解不出长帧"，比明确不支持更隐蔽：
界面上永远空白，用户以为是没收到信号。

修法：维护 2 秒滑动窗口，每块追加后对**整个窗口**解调；用静默门限和
帧签名去重把重算浪费压下去。

本文件的核心断言
----------------
`test_frame_spanning_blocks_is_decoded`：把一帧切成多块喂进去，必须解出 1 帧。
这条在旧实现下是 0 帧——它就是这个缺陷的判别式。
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from mbdsdr_ai.ax25 import AFSKModem, AX25Frame  # noqa: E402

from aprs_panel import AprsPanel  # noqa: E402


def _make_frame() -> AX25Frame:
    fr = AX25Frame()
    fr.destination = "APRS"
    fr.dest_ssid = 0
    fr.source = "BI1AAA"
    fr.source_ssid = 7
    fr.info = b"!3959.99N/11619.99E>test"
    return fr


def _modulated(**kw) -> np.ndarray:
    modem = AFSKModem()
    audio = modem.modulate(_make_frame(), **kw)
    return np.asarray(audio, dtype=np.float64)


def _panel():
    return AprsPanel()


def _collected(panel):
    out = []
    panel.packet_ready.connect(lambda pkt: out.append(pkt))
    return out


class TestCrossBlockContinuity:
    def test_frame_spanning_blocks_is_decoded(self):
        """一帧切成多块喂 —— 必须解出帧。旧实现（逐块）在这里是 0。"""
        audio = _modulated()
        fs = 48000.0
        block = 4096  # 85ms/块；帧长约 0.4–0.5s，必然跨 5+ 块
        assert audio.size > block * 3, "样本太短，测不出跨块"

        panel = _panel()
        got = _collected(panel)
        for start in range(0, audio.size, block):
            panel.feed_audio(audio[start:start + block], fs)
        panel.close()

        assert got, "跨块帧没解出来——滑动窗口没生效（逐块解调的老病）"
        assert any("BI1AAA" in str(p.get("source", "")) for p in got), got

    def test_frame_is_not_reported_twice(self):
        """滑动窗口会让同一帧在连续几次解调里重复出现，只许报一次。"""
        audio = _modulated()
        panel = _panel()
        got = _collected(panel)
        for start in range(0, audio.size, 4096):
            panel.feed_audio(audio[start:start + 4096], 48000.0)
        # 再整体喂一遍（窗口里还是同一帧）
        panel.feed_audio(audio, 48000.0)
        panel.close()
        assert len(got) >= 1
        sources = [str(p.get("source", "")) for p in got]
        assert sources.count("BI1AAA") == 1, f"同一帧被报了多次：{sources}"

    def test_silence_skips_demod(self):
        """静默时不跑解调——没有信号还每块跑 2 秒相关运算是纯浪费。"""
        panel = _panel()
        for _ in range(10):
            panel.feed_audio(np.zeros(4096, dtype=np.float64), 48000.0)
        diag = panel.stream_diagnostics()
        panel.close()
        assert diag["decode_calls"] == 0, "静默时仍在解调"
        assert diag["feed_count"] == 0

    def test_window_is_bounded(self):
        """窗口必须有上限，否则长跑会吃满内存。"""
        panel = _panel()
        panel.feed_audio(np.full(48000 * 5, 0.5, dtype=np.float64), 48000.0)
        diag = panel.stream_diagnostics()
        panel.close()
        cap = int(round(panel.STREAM_WINDOW_S * 48000.0))
        assert diag["buffered_samples"] <= cap


class TestSampleRateMismatch:
    def test_resamples_instead_of_reading_wrong_rate(self):
        """采样率与 modem 不一致时必须重采样，不能直接当成 48k 去解。"""
        audio = _modulated()
        # 造一段 24k 音频：把 48k 音频隔点取（频率关系保持不变）
        down = audio[::2]
        panel = _panel()
        got = _collected(panel)
        for start in range(0, down.size, 2048):
            panel.feed_audio(down[start:start + 2048], 24000.0)
        panel.close()
        assert got, "24k 输入没被正确重采样到 modem 采样率"


class TestDiagnostics:
    def test_reports_counts_and_timing(self):
        audio = _modulated()
        panel = _panel()
        panel.feed_audio(audio[:8192], 48000.0)
        diag = panel.stream_diagnostics()
        panel.close()
        assert diag["feed_count"] >= 1
        assert diag["decode_calls"] >= 1
        assert diag["avg_decode_ms"] >= 0.0
        assert diag["window_s"] == pytest.approx(panel.STREAM_WINDOW_S)

    def test_dedup_table_does_not_grow_without_bound(self):
        audio = _modulated()
        panel = _panel()
        for _ in range(50):
            panel.feed_audio(audio, 48000.0)
        diag = panel.stream_diagnostics()
        panel.close()
        assert diag["dedup_entries"] <= 8, "去重表长跑会无限增长"


class TestWiringGuard:
    def test_feed_audio_uses_window_not_raw_block(self):
        """源码守卫：不许退回"直接把这一块丢给 demodulate"（AST 结构断言）。"""
        import ast

        import aprs_panel as mod

        tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "feed_audio")

        def _is_audio_buf(node) -> bool:
            return (isinstance(node, ast.Attribute) and node.attr == "_audio_buf"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "self")

        assert any(_is_audio_buf(n) for n in ast.walk(fn)), "没有滑动缓冲"
        demod_calls = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "demodulate"
        ]
        assert any(n.args and _is_audio_buf(n.args[0]) for n in demod_calls), \
            "解调的不是窗口，说明又退回逐块独立解调"
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
        assert "STREAM_DEDUP_S" in (names | attrs), "没有去重，同一帧会被反复上报"
