# SPDX-License-Identifier: MIT
"""ADS-B 地图空态必须说明"为什么没飞机"（2026-11-06）。

`adsb_lite.decode_adsb` 已经给了完整诊断（前导候选数 / CRC 通过·失败 /
自适应门限 / 噪声底 / 时长），但 `AdsbMapPanel._decode_and_update` 把
`result["note"]` 全丢了，只对用户显示一句「无 ADS-B 飞机（需 1090MHz）」：
- 没信号、信号弱、频段不符、解码器抛异常，四种状态在界面上完全一样；
- `except Exception: return 0` 让解码器坏了也伪装成"没信号"。

修法：画布空态第二行画真实诊断（不改布局、不改主空态文案），异常类型、
频段不符、IQ 总线异常各自有话说；同文案不重复排重绘。

反证：删掉 `_set_diag(result.get("note", ""))` → 前两条用例立即红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtWidgets import QApplication  # noqa: E402

import adsb_map_panel as amp  # noqa: E402


@pytest.fixture(scope="module")
def qtapp(isolated_profile):
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class _RecordingPainter:
    """只记录 drawText 的文本，其余绘制调用全部吞掉。"""

    instances = []
    Antialiasing = 1          # paintEvent 里引用 QPainter.Antialiasing

    def __init__(self, *_a, **_k):
        self.texts = []
        type(self).instances.append(self)

    def drawText(self, *_a, **_k):
        self.texts.append(str(_a[-1]))

    def window(self):
        """图层用 QPainter.window() 拿逻辑尺寸给空态牌居中。"""
        from PySide6.QtCore import QRectF
        return QRectF(0.0, 0.0, 600.0, 400.0)

    def __getattr__(self, _name):
        return lambda *_a, **_k: None


class TestDecodeDiagnostics:
    def test_no_signal_exposes_decoder_note(self, qtapp):
        p = amp.AdsbMapPanel()
        p.feed_iq(np.zeros(4096, dtype=np.complex64), 2_000_000.0)
        diag = p.diag_text
        assert "未检出有效 Mode-S 帧" in diag, diag
        assert "CRC" in diag and "前导候选" in diag, diag
        assert p.decoded_frames_total() == 0

    def test_decoder_exception_is_not_swallowed(self, qtapp, monkeypatch):
        p = amp.AdsbMapPanel()

        def _boom(*_a, **_k):
            raise RuntimeError("模拟解码器内部错误")

        monkeypatch.setattr(amp._adsb_lite, "decode_adsb", _boom)
        p.feed_iq(np.ones(4096, dtype=np.complex64), 2_000_000.0)
        diag = p.diag_text
        assert "RuntimeError" in diag and "模拟解码器内部错误" in diag, diag
        assert "未检出有效" not in diag, "异常被伪装成没信号"
        assert p.decoded_frames_total() == 0

    def test_real_frame_shows_frame_count(self, qtapp):
        from mbdsdr_ai import adsb_lite
        frame = adsb_lite.build_long_frame(0x8D, 0x40621D, b"\x00" * 7)
        iq = adsb_lite.synthesize_modes_iq([frame], sample_rate=2_000_000.0,
                                           noise_std=0.0)
        p = amp.AdsbMapPanel()
        p.feed_iq(iq, 2_000_000.0)
        assert p.decoded_frames_total() >= 1, p.diag_text
        assert "检出" in p.diag_text, p.diag_text
        assert "CRC 通过" in p.diag_text

    def test_module_missing_is_reported(self, qtapp, monkeypatch):
        p = amp.AdsbMapPanel()
        monkeypatch.setattr(amp, "_HAS_ADSB", False)
        p.feed_iq(np.ones(4096, dtype=np.complex64), 2_000_000.0)
        assert "解码模块不可用" in p.diag_text


class TestBusDiagnostics:
    def test_out_of_band_frame_is_explained(self, qtapp):
        from desktop.iq_bus import get_iq_bus
        p = amp.AdsbMapPanel()
        bus = get_iq_bus()
        bus.publish(np.zeros(1024, dtype=np.complex64), 2_400_000.0, 98.5e6)
        p._poll_iq_bus()
        diag = p.diag_text
        assert "98.500000 MHz" in diag, diag
        assert "不在 1090" in diag, diag
        assert p.decoded_frames_total() == 0

    def test_bus_exception_is_explained(self, qtapp, monkeypatch):
        p = amp.AdsbMapPanel()

        def _boom():
            raise OSError("模拟总线故障")

        monkeypatch.setattr("desktop.iq_bus.get_iq_bus", _boom)
        p._poll_iq_bus()
        assert "OSError" in p.diag_text, p.diag_text


class TestEmptyStatePainting:
    def test_diag_is_actually_drawn_in_empty_state(self, qtapp, monkeypatch):
        p = amp.AdsbMapPanel()
        p._set_diag("诊断可见性取证：CRC 通过 0 帧")
        _RecordingPainter.instances = []
        painter = _RecordingPainter()
        # 产品里是 OsmMapView.paintEvent 回调这个图层；这里直接调图层，
        # 断言"文字真的进了绘制调用"，比看属性更硬。
        p._paint_adsb_layer(painter, p.canvas.project)
        assert _RecordingPainter.instances
        texts = painter.texts
        assert amp.ADSB_EMPTY_TEXT not in texts, texts
        assert "诊断可见性取证：CRC 通过 0 帧" in texts, texts

    def test_same_text_does_not_repaint(self, qtapp, monkeypatch):
        p = amp.AdsbMapPanel()
        calls = []
        monkeypatch.setattr(p.canvas, "update", lambda: calls.append(1))
        p._set_diag("同一句")
        p._set_diag("同一句")
        assert len(calls) == 1
        p._set_diag("换一句")
        assert len(calls) == 2

    def test_long_text_is_truncated(self, qtapp):
        p = amp.AdsbMapPanel()
        p._set_diag("x" * 500)
        assert len(p.diag_text) == 200
