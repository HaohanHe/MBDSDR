# SPDX-License-Identifier: MIT
"""录制文件分析的频谱帧来源标记（Task 98）
==========================================

``sdr_analyze_recording`` 分析的是**离线录制样本**，其频谱帧必须标
``source="replay"``，不许沿用 ``compute_spectrum`` 的默认 ``"live"``
（默认值是给实时设备链路用的）。

测试边界：``_analyze_recording`` 只返回文本报告、不暴露 ``SpectrumData``，
因此这里用「真函数 + 来源间谍」：包装 ``SpectrumProcessor.compute_spectrum``，
转发到真实实现并截获返回的 ``SpectrumData``，断言其 ``source``。喂给工具的
是入仓的真实 OTA 录制 ``tests/fixtures/real_fm_88m1``（SigMF cu8，88.1 MHz
FM，fs=1.024 MHz）——落到临时目录的 ``.cu8`` + sidecar 后走真实读回路径，
不是合成波形。

正控：实时路径（``_spectrum_analyze`` + 后端桩）仍必须是 ``"live"``，
防止「把所有调用一刀切成 replay」的假修复。

运行:
    python -m pytest tests/test_replay_spectrum_source.py -v
"""
import json
import os
import sys
import types

os.environ.setdefault("MBDSDR_NO_HW", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from mbdsdr_ai import sdr_tools as st  # noqa: E402
from mbdsdr_ai.spectrum_processor import SpectrumData  # noqa: E402

_FIXTURE_DIR = os.path.join(_ROOT, "tests", "fixtures", "real_fm_88m1")
_SIGMF_DATA = os.path.join(_FIXTURE_DIR, "capture.sigmf-data")
_SIGMF_META = os.path.join(_FIXTURE_DIR, "capture.sigmf-meta")


def _spy_compute_spectrum(monkeypatch):
    """包装 compute_spectrum：转发真实现，收集返回的 SpectrumData。"""
    seen = []
    real = st.SpectrumProcessor.compute_spectrum

    def spy(self, samples, center_freq, sample_rate, fft_size=None,
            source="live"):
        data = real(self, samples, center_freq, sample_rate,
                    fft_size=fft_size, source=source)
        seen.append(data)
        return data

    monkeypatch.setattr(st.SpectrumProcessor, "compute_spectrum", spy)
    return seen


@pytest.fixture(scope="module")
def real_capture(tmp_path_factory):
    """把入仓真实 OTA 录制转成工具能读的 .cu8 + sidecar（在临时目录）。"""
    if not (os.path.isfile(_SIGMF_DATA) and os.path.isfile(_SIGMF_META)):
        pytest.skip("缺少真实 FM fixture：tests/fixtures/real_fm_88m1")

    with open(_SIGMF_META, encoding="utf-8") as f:
        meta = json.load(f)
    sample_rate = float(meta["global"]["core:sample_rate"])
    center_hz = float(meta["global"]["core:frequency"])

    out_dir = tmp_path_factory.mktemp("real_fm_88m1")
    path = out_dir / "real_fm_88m1.cu8"
    with open(_SIGMF_DATA, "rb") as src, open(path, "wb") as dst:
        dst.write(src.read())
    (out_dir / "real_fm_88m1.cu8.json").write_text(
        json.dumps({"sample_rate_hz": sample_rate, "center_hz": center_hz}),
        encoding="utf-8")
    return str(path), sample_rate, center_hz


def test_analyze_recording_marks_frames_replay(real_capture, monkeypatch):
    path, sample_rate, center_hz = real_capture
    seen = _spy_compute_spectrum(monkeypatch)

    report = st._analyze_recording({"file_path": path, "correct_iq": False,
                                    "fft_size": 1024})

    assert not report.startswith("错误"), report[:200]
    assert "录制文件分析报告" in report
    assert "实时" not in report, "离线报告文案不得自称实时"

    assert len(seen) == 1, f"录制分析应只产生一帧频谱，实际 {len(seen)} 帧"
    frame = seen[0]
    assert isinstance(frame, SpectrumData)
    assert frame.source == "replay", (
        f"录制文件仍是 {frame.source!r}：离线样本不许冒充实时谱")
    # 真实样本确实被分析了（真实信号高于噪声底，不是空跑）
    assert frame.peak_power_db > frame.noise_floor_db
    assert frame.sample_rate == sample_rate
    assert frame.center_freq == center_hz


class _LiveBackend:
    """最小实时后端桩：只提供读 IQ / 频率 / 采样率。"""

    def __init__(self):
        self.status = types.SimpleNamespace(connected=True)

    def read_samples(self, n):
        t = np.arange(n) / 2.4e6
        return (0.3 * np.exp(2j * np.pi * 100e3 * t)).astype(np.complex64)

    def get_frequency(self):
        return 98.5e6

    def get_sample_rate(self):
        return 2.4e6


class _Mgr:
    def __init__(self, backend):
        self._backend = backend

    def get_active(self):
        return self._backend


def test_live_path_still_live(monkeypatch):
    """正控：实时设备链路不受影响，仍是 "live"。"""
    seen = _spy_compute_spectrum(monkeypatch)
    out = st._spectrum_analyze(_Mgr(_LiveBackend()),
                               st.SpectrumProcessor(fft_size=1024),
                               {"fft_size": 1024})

    assert "错误" not in out
    assert len(seen) == 1
    assert seen[0].source == "live", (
        f"实时链路被误标成 {seen[0].source!r}")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
