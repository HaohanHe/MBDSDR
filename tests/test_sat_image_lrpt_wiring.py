# SPDX-License-Identifier: MIT
"""SatelliteImagePanel：METEOR LRPT 从死占位变成真入口（2026-11-06）。

旧行为：选 METEOR-M2 → 开始解码 → 采四分钟 APT 音频 → 停止，
永远只得到一句「图像重组待实现」。可仓库里 `compose_visible_image`
早就实现了，真实原因是 QPSK 数字链路根本不吃音频。

新行为：模式切换时按钮变成「选择录制 IQ 解码」，走与气象云图面板
**同一个** DecodeWorker(family="meteor")，采样率优先读 SigMF meta，
成功出真图、失败写明真实原因并保持空态。

测试用同步假 QThread/假 Worker 替换真线程，因此整个信号链（started →
run → finished → quit → idle）在同一线程里跑完，无竞态、无需 wait。
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402
from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import satellite_image_panel as sip  # noqa: E402
import weather_panel as wp  # noqa: E402

LRPT_SAT = "METEOR-M2 (LRPT)"


@pytest.fixture(scope="module")
def qtapp(isolated_profile):
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class _FakeThread(QObject):
    started = Signal()
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False

    def isRunning(self):
        return self._running

    def start(self):
        self._running = True
        self.started.emit()

    def quit(self):
        self._running = False
        self.finished.emit()

    def wait(self, _ms=0):
        return True


class _FakeWorker(QObject):
    progress = Signal(str)
    failed = Signal(str)
    finished = Signal(object)

    seen = []
    result = {"ok": False, "error": "默认失败", "frames": 0}

    def __init__(self, params):
        super().__init__()
        self.params = params
        type(self).seen.append(params)

    def moveToThread(self, _thread):
        pass

    def request_stop(self):
        pass

    def run(self):
        self.progress.emit("假进度：解调中")
        self.finished.emit(dict(type(self).result))


@pytest.fixture
def fake_chain(qtapp, tmp_path, monkeypatch):
    _FakeWorker.seen = []
    _FakeWorker.result = {"ok": False, "error": "默认失败", "frames": 0}
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    iq = tmp_path / "capture.sigmf-data"
    iq.write_bytes(b"")
    (tmp_path / "capture.sigmf-meta").write_text(json.dumps({
        "global": {"core:sample_rate": 2_400_000.0}}), encoding="utf-8")
    monkeypatch.setattr(sip, "_IMG_DIR", str(img_dir))
    monkeypatch.setattr(sip, "QThread", _FakeThread)
    monkeypatch.setattr(wp, "DecodeWorker", _FakeWorker)
    monkeypatch.setattr(
        sip.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(iq), "")))
    panel = sip.SatelliteImagePanel()
    return panel, tmp_path, img_dir, iq


# ---------------------------------------------------------------------------
class TestModeSwitch:
    def test_noaa_needs_sdr_lrpt_does_not(self, qtapp):
        p = sip.SatelliteImagePanel()
        assert p.start_btn.text() == "开始解码"
        assert not p.start_btn.isEnabled()          # 未接 SDR
        assert not p.sr_spin.isEnabled()            # IQ 采样率只对 LRPT 有意义
        # 入口只留一个：早期版本并排挂了两个几乎同名按钮，真机截图里
        # 用户不知道点哪个；不允许再加回来。
        assert not hasattr(p, "iq_btn")

        p.sat_combo.setCurrentText(LRPT_SAT)
        assert p.start_btn.text() == "选择录制 IQ 解码"
        assert p.start_btn.isEnabled()              # 离线解码不需要在线 SDR
        assert p.sr_spin.isEnabled()

        p.sat_combo.setCurrentText("NOAA-19 (APT)")
        assert p.start_btn.text() == "开始解码"
        assert not p.start_btn.isEnabled()
        p.set_sdr_connected(True)
        assert p.start_btn.isEnabled()

    def test_audio_buffer_no_longer_claims_missing_renderer(self, qtapp):
        p = sip.SatelliteImagePanel()
        p.sat_combo.setCurrentText(LRPT_SAT)
        # 直接构造"误喂音频"的场景，验证文案不再说谎（不去点按钮，
        # 那个动作在真实 UI 里弹文件选择框，测试里不该弹模态框）
        p._running = True
        p.feed_audio(np.zeros(128, dtype=np.float32), 24000)
        p._decode_buffer()
        text = p.stats_label.text()
        assert "不能从 APT 音频解码" in text
        assert "图像重组待实现" not in text
        assert "选择录制 IQ 解码" in text
        assert p.image_label.text() == sip.SAT_FAILED_TEXT


# ---------------------------------------------------------------------------
class TestSampleRateSource:
    def test_sigmf_meta_wins(self, fake_chain):
        p, tmp_path, _, iq = fake_chain
        sr, note = p._iq_sample_rate(str(iq))
        assert sr == pytest.approx(2_400_000.0)
        assert "SigMF meta" in note

    def test_no_meta_falls_back_to_explicit_manual_value(self, fake_chain):
        p, tmp_path, _, _ = fake_chain
        (tmp_path / "capture.sigmf-meta").unlink()
        p.sr_spin.setValue(1.024)
        sr, note = p._iq_sample_rate(str(tmp_path / "capture.sigmf-data"))
        assert sr == pytest.approx(1_024_000.0)
        assert "手动输入" in note and "无有效 SigMF meta" in note

    def test_broken_meta_is_not_silently_assumed(self, fake_chain):
        p, tmp_path, _, iq = fake_chain
        (tmp_path / "capture.sigmf-meta").write_text("{not json", encoding="utf-8")
        sr, note = p._iq_sample_rate(str(iq))
        assert sr == pytest.approx(1_000_000.0)
        assert "手动输入" in note


# ---------------------------------------------------------------------------
class TestDecodeWiring:
    def test_failure_keeps_honest_empty_state(self, fake_chain):
        p, _, _, _ = fake_chain
        _FakeWorker.result = {
            "ok": False, "frames": 3,
            "error": "帧首 cbc465… 属于「meteor_sat_asm_8b」口径，"
                     "帧约定尚未统一（not_verified），不出图",
        }
        p.sat_combo.setCurrentText(LRPT_SAT)
        p.start_btn.click()
        assert len(_FakeWorker.seen) == 1
        params = _FakeWorker.seen[0]
        assert params["family"] == "meteor"
        assert params["sample_rate"] == pytest.approx(2_400_000.0)
        assert params["out_png"].endswith("_lrpt.png")
        assert p.image_label.text() == sip.SAT_FAILED_TEXT
        assert "not_verified" in p.stats_label.text()
        assert "帧数 3" in p.stats_label.text()
        assert not p.save_btn.isEnabled()
        assert p.progress.value() == 0
        assert p.image_path() is None
        # idle 之后按钮恢复，且空态没被"成功"覆盖
        assert p.start_btn.isEnabled()

    def test_success_shows_real_decoder_png_and_history(self, fake_chain):
        from PIL import Image
        p, _, img_dir, _ = fake_chain

        def _run_with_png(self):
            out = self.params["out_png"]
            Image.new("L", (8, 8), 128).save(out)
            self.progress.emit("假进度：段重组")
            self.finished.emit({
                "ok": True, "png": out, "width": 8, "height": 8,
                "frames": 12, "satellite": LRPT_SAT,
            })

        orig_run = _FakeWorker.run
        _FakeWorker.run = _run_with_png
        try:
            p.sat_combo.setCurrentText(LRPT_SAT)
            p.start_btn.click()
        finally:
            _FakeWorker.run = orig_run
        assert p.image_path() is not None
        assert os.path.isfile(p.image_path())
        assert p.save_btn.isEnabled()
        assert "Meteor LRPT 录制 IQ" in p.stats_label.text()
        assert "帧数: 12" in p.stats_label.text()
        assert p.progress.value() == 100
        names = [p.history.item(i).text() for i in range(p.history.count())]
        assert any(n.endswith("_lrpt.png") for n in names), names

    def test_close_event_with_live_thread_does_not_raise(self, fake_chain):
        p, _, _, _ = fake_chain
        # 造一个"仍在跑"的假线程，验证 closeEvent 会 quit+wait 而不抛
        th = _FakeThread()
        th._running = True
        p._decode_thread = th
        p._decode_worker = _FakeWorker({"family": "meteor"})
        p.closeEvent(QCloseEvent())
        assert not th.isRunning()
