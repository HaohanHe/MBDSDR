#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面 GUI 真机端到端回归：枚举 -> 连接 -> fan-out -> 实时频谱 -> 音频。

无真实 RTL-SDR 时自动 skip（CI 绿色）；插入真机后用 offscreen 方式验证：
  1. 连接在热插拔轮询下保持（不被误断开）；
  2. fan-out ring 持续供数，频谱帧不断产出且帧间随真实信号变化；
  3. 后端 ReceiveChain 产出真实音频。

运行（插好 RTL-SDR）：
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_real_hardware.py -s
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))


def _find_rtl():
    """返回原生 RTL 设备 dict；无硬件/无驱动返回 None。"""
    try:
        from mbdsdr_ai.windows_setup import setup_rtlsdr_windows
        try:
            setup_rtlsdr_windows()
        except Exception:
            pass
        from mbdsdr_ai.sdr_backend import enumerate_all_sdr_devices
        for d in enumerate_all_sdr_devices():
            if d.get("driver") == "rtlsdr" and d.get("source") == "rtl_native":
                return d
    except Exception:
        return None
    return None


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.mark.skipif(_find_rtl() is None, reason="无真实 RTL-SDR，跳过真机 GUI 端到端")
def test_mainwindow_real_hardware_loop(qapp):
    import numpy as np
    from mbdsdr_ai.sdr_backend import build_backend_for_device
    from main_window import MainWindow

    dev = _find_rtl()
    assert dev is not None
    w = MainWindow()
    try:
        backend = build_backend_for_device(dev)
        assert backend is not None
        ok = w._connect_backend(backend, sample_rate=2_048_000.0, gain=20.0,
                                ppm=0, agc=True, offset_tuning=False)
        assert ok is True
        qapp.processEvents()

        # 跑满 3 秒，让至少一次 2s 热插拔轮询在连接态发生，验证不被误断开
        t_end = time.time() + 3.0
        while time.time() < t_end:
            qapp.processEvents()
            time.sleep(0.02)
        assert w._active_sdr_backend is not None, "热插拔轮询误断开连接中的设备"

        gen = w.spectrum.generator

        def snap():
            start = gen.wf_rows_written
            t0 = time.time()
            while gen.wf_rows_written <= start and time.time() - t0 < 1.5:
                w._poll_sdr_iq()
                qapp.processEvents()
                time.sleep(0.02)
            return np.array(gen.spectrum, dtype=np.float64), gen.wf_rows_written

        s1, n1 = snap()
        time.sleep(0.1)
        s2, n2 = snap()
        assert n2 > n1, "频谱帧停止产出（fan-out 未持续供数）"
        assert np.isfinite(s1).mean() > 0.8, "频谱几乎无有效数据"
        assert np.nanstd(s1) > 1.0, "频谱近乎平坦（疑似假数据）"
        both = np.isfinite(s1) & np.isfinite(s2)
        delta = float(np.abs(s1[both] - s2[both]).mean())
        assert delta > 0.2, "频谱两帧完全不变（疑似静态/合成）"

        audio = backend.read_audio(4800)
        assert audio is not None and len(audio) > 0, "后端无音频输出"
    finally:
        try:
            backend.stop_audio()
            backend.disconnect()
        except Exception:
            pass
        w.close()
        qapp.processEvents()
