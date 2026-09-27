"""WFM 立体声/去加重 + RTL 枚举去重 回归测试。

锁定此前两个真机发现的问题：
1. demod_wfm：L/R 共用一个去加重对象再 reset()，导致右声道每块从零起振；
   改为 L/R 独立 IIR 状态后，立体声声道分离与单声道回退必须保持正确。
2. enumerate_all_sdr_devices：osmosdr 的 rtl 条目与原生 RTLSDR 是同一根棒，
   之前枚举成两条（其中一条 label 是 bytes、调谐器 Unknown）。
"""
import numpy as np
import pytest

from mbdsdr_ai.demod_wfm import DemodWFM
from mbdsdr_ai.demod_nfm import DeemphasisIIR


def _synth_stereo_iq(if_sr=250000.0, duration=0.6, deviation=75000.0):
    """生成含 19kHz 导频、3kHz 差信号的立体声调频复信号。"""
    t = np.arange(int(if_sr * duration)) / if_sr
    M = 0.6 * np.cos(2 * np.pi * 1000 * t)
    D = 0.4 * np.cos(2 * np.pi * 3000 * t)
    mpx = M + 0.15 * np.cos(2 * np.pi * 19000 * t) + D * np.cos(2 * np.pi * 38000 * t)
    phase = np.cumsum(2 * np.pi * deviation / if_sr * mpx)
    return np.exp(1j * phase).astype(np.complex128), t


def test_stereo_separation_sign():
    iq, t = _synth_stereo_iq()
    L, R = DemodWFM(if_sr=250000.0, bandwidth=150000.0).process(iq)
    n = min(len(L), len(R), len(t))
    tt = t[:n]
    cL = float(np.mean(L[:n] * np.cos(2 * np.pi * 3000 * tt)))
    cR = float(np.mean(R[:n] * np.cos(2 * np.pi * 3000 * tt)))
    # 差信号 D 与 L 同相、与 R 反相
    assert cL > 0, f"L 3k 分量应与 D 同相, got {cL}"
    assert cR < 0, f"R 3k 分量应与 D 反相, got {cR}"
    assert abs(cL + cR) < abs(cL) * 1.5  # 两路大小相近


def test_no_pilot_falls_back_to_mono():
    if_sr = 250000.0
    t = np.arange(int(if_sr * 0.5)) / if_sr
    M = 0.6 * np.cos(2 * np.pi * 1000 * t)
    phase = np.cumsum(2 * np.pi * 75000.0 / if_sr * M)
    iq = np.exp(1j * phase).astype(np.complex128)
    L, R = DemodWFM(if_sr=if_sr, bandwidth=150000.0).process(iq)
    n = min(len(L), len(R))
    assert np.max(np.abs(L[:n] - R[:n])) < 1e-6, "无导频时必须退化为单声道"


def test_deemphasis_attenuates_highs():
    de = DeemphasisIIR(50e-6, 250000.0)
    def gain(f):
        de.reset()
        x = np.sin(2 * np.pi * f * np.arange(200000) / 250000.0)
        y = de.process(x)
        return float(np.sqrt(np.mean(y[-20000:] ** 2) / np.mean(x[-20000:] ** 2)))
    g1k, g10k = gain(1000.0), gain(10000.0)
    assert g1k > 0.8
    assert g10k < g1k * 0.5, f"去加重应明显压低高频: 1k={g1k:.3f} 10k={g10k:.3f}"


def test_rtl_enumeration_dedups_native_and_osmo(monkeypatch):
    import mbdsdr_ai.sdr_backend as sb

    class _FakeNative:
        @staticmethod
        def list_devices():
            return [{"index": 0, "tuner": "FC0012", "serial": "77771111153705700"}]

    class _FakeOsmo:
        @staticmethod
        def enumerate():
            return [{
                "driver": "rtl", "index": 0,
                "label": "RTL-SDR #0 b'Generic RTL2832U'",  # bytes 标签残留
                "serial": "", "device_string": "rtl=0",
            }]

    monkeypatch.setattr(sb.SoapySDRBackend, "list_devices", staticmethod(lambda: []))
    monkeypatch.setattr(sb.RTLSDRBackend, "list_devices", staticmethod(_FakeNative.list_devices))
    monkeypatch.setattr(sb.PlutoSDRBackend, "enumerate", staticmethod(lambda: []))
    import mbdsdr_ai.osmosdr_source as osm
    monkeypatch.setattr(osm, "DeviceEnumerator", _FakeOsmo)

    devs = sb.enumerate_all_sdr_devices()
    rtl = [d for d in devs if d.get("driver") in ("rtlsdr", "rtl")]
    assert len(rtl) == 1, f"同一根 RTL 棒应只出现一条, got {rtl}"
    assert not isinstance(rtl[0].get("label"), bytes)
    # 保留信息更全的原生条目
    assert rtl[0].get("source") == "rtl_native"
