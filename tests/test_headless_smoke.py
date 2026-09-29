# SPDX-License-Identifier: MIT
"""
Headless 架构自检测试（验收标准）
=====================================

不实例化任何 QWidget，纯 Python API 走完一整条接收链路。
用 DebugSource（明确标记的调试信号源）或空后端。

断言：
  * 每一步返回正确类型；
  * sys.modules 里没有 PySide6 / PyQt（core 真的 headless）；
  * 无硬件（空后端）时数据方法返回 None、状态 connected=False（不造假）。
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

from mbdsdr_ai.core import SDRController, SDRUserError


@pytest.fixture(scope="module")
def ctrl(tmp_path_factory):
    cfg = tmp_path_factory.mktemp("core-smoke")
    c = SDRController(config_dir=str(cfg))
    yield c
    try:
        c.shutdown()
    except Exception:
        pass


def test_no_qt_imported():
    """core 包本身不得引入 Qt。

    在独立子进程里验证（套件中其它 GUI 测试会把 PySide6 加载进本进程的
    sys.modules，全局检查会被污染），这是 headless 架构的硬验收标准。
    """
    import subprocess
    import sys

    code = (
        "import sys;"
        "import mbdsdr_ai.core;"
        "qt=[m for m in sys.modules if m.startswith(('PySide','PyQt','shiboken'))];"
        "print('QT:'+','.join(qt) if qt else 'QT:NONE')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout.strip()
    assert out.endswith("QT:NONE"), f"core 引入了 Qt 绑定: {out}"


def test_no_backend_degrades_safely():
    """空后端（未 connect）：数据方法 None，状态 connected=False。"""
    c = SDRController(config_dir="/tmp/_mbdsdr_nobackend")
    assert c.is_connected() is False
    assert c.read_spectrum(256) is None
    assert c.read_iq(256) is None
    assert c.read_audio(256) is None
    status = c.get_status()
    assert status.connected is False
    assert status.device is None
    # 设备列表应至少包含 debug 项，但不冒充硬件
    devs = c.list_devices()
    assert any(getattr(d, "driver", "") == "debug" for d in devs)


def test_full_headless_flow(ctrl):
    """用调试信号源走完整链路。"""
    # 1. list_devices
    devs = ctrl.list_devices()
    assert isinstance(devs, list)

    # 2. connect（调试信号源）
    assert ctrl.connect("debug") is True
    assert ctrl.is_connected() is True
    assert ctrl.get_status().is_debug_source is True

    # 3. set_frequency / set_demod / set_gain
    ctrl.set_frequency(98_500_000.0)
    assert ctrl.get_frequency() == pytest.approx(98_500_000.0)
    ctrl.set_demod("WFM")
    assert ctrl.get_demod() == "WFM"
    ctrl.set_gain(20.0)
    ctrl.set_sample_rate(2_400_000.0)
    ctrl.set_bandwidth(200_000.0)
    ctrl.set_squelch(-60.0)

    # 4. start_audio（无头无设备 -> 允许 False，但必须返回 bool 不崩）
    audio_ok = ctrl.start_audio()
    assert isinstance(audio_ok, bool)

    # 5. read_spectrum
    spec = ctrl.read_spectrum(512)
    assert isinstance(spec, np.ndarray)
    assert spec.ndim == 1 and spec.size == 512
    assert np.isfinite(spec).all()

    # 6. read_iq
    iq = ctrl.read_iq(1024)
    assert isinstance(iq, np.ndarray)
    assert iq.size == 1024
    assert np.iscomplexobj(iq)

    # 7. read_audio
    audio = ctrl.read_audio(512)
    assert audio is None or isinstance(audio, np.ndarray)

    # 8. start_scan -> get_scan_results
    handle = ctrl.start_scan(98_000_000.0, 98_300_000.0, 50_000.0)
    assert isinstance(handle, str) and handle.startswith("scan-")
    # 轮询等待（最多 ~5s）
    import time
    for _ in range(50):
        if ctrl._scans[handle]["done"]:  # noqa: SLF001
            break
        time.sleep(0.1)
    results = ctrl.get_scan_results(handle)
    assert isinstance(results, list)
    ctrl.stop_scan(handle)

    # 9. start_decoder -> get_decoder_messages
    dhandle = ctrl.start_decoder("adsb")
    assert isinstance(dhandle, str) and dhandle.startswith("dec-")
    time.sleep(0.1)
    msgs = ctrl.get_decoder_messages(dhandle)
    assert isinstance(msgs, list)  # 无真实信号 -> 空列表，不造假
    ctrl.stop_decoder(dhandle)

    # 10. get_status
    status = ctrl.get_status()
    assert status.connected is True
    assert status.demod == "WFM"
    assert status.frequency_hz == pytest.approx(98_500_000.0)

    # 11. disconnect
    ctrl.disconnect()
    assert ctrl.is_connected() is False


def test_decoder_type_validation(ctrl):
    """未知解码器类型抛 DecoderNotAvailableError。"""
    with pytest.raises(SDRUserError):
        ctrl.start_decoder("nonexistent_mode_xyz")


def test_vfo_bookmark_roundtrip(ctrl, tmp_path):
    """多 VFO 与书签的基本增删查。"""
    v1 = ctrl.add_vfo(98_500_000.0, "WFM", 200_000.0)
    assert isinstance(v1, str)
    assert ctrl.set_primary_vfo(v1) is True
    vfos = ctrl.list_vfos()
    assert any(v.vfo_id == v1 and v.primary for v in vfos)
    ctrl.remove_vfo(v1)

    ctrl.add_bookmark(98_500_000.0, "Test FM", "FM", "test")
    near = ctrl.find_nearest_bookmark(98_501_000.0)
    assert near is not None and near["name"] == "Test FM"
    assert isinstance(ctrl.list_bookmarks("test"), list)
