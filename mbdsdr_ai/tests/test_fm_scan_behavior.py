# SPDX-License-Identifier: MIT
"""fm_scan 行为测试（合成 IQ，确定性 seed，无硬件）。

oracle 来源（读码语义，非拍脑袋）：
- _carrier_strength：中心 ±75kHz 信道平均 PSD 与带外中位 PSD 之差（dB）；
- analyze_fm_frequency.active = strength_db >= carrier_threshold_db（闭区间边界）；
- _detect_pilot_19k：鉴频后 mpx 在 19k±500Hz 的能量占比 > -18dB 判立体声；
- scan_fm_band：只把 active 频点收进 stations，按 strength_db 降序；
- synthesize_fm_station 用全局 np.random，测试内固定 np.random.seed。
"""
import numpy as np
import pytest

from mbdsdr_ai.fm_scan import (
    FMStation,
    _carrier_strength,
    _detect_pilot_19k,
    analyze_fm_frequency,
    compare_with_last_scan,
    load_presets,
    save_presets,
    scan_fm_band,
    synthesize_fm_station,
)

SR = 1e6
N = 16384
SEED = 20261028


def _station(freq_offset_hz=0.0, stereo=True, noise_level=0.05):
    return synthesize_fm_station(
        freq_offset_hz=freq_offset_hz, sample_rate=SR, n_samples=N,
        stereo=stereo, noise_level=noise_level,
    )


def _noise(n=N):
    return 0.05 * (np.random.randn(n) + 1j * np.random.randn(n))


@pytest.fixture(autouse=True)
def _deterministic_rng():
    """synthesize_fm_station 走全局 RNG，逐用例固定种子保证可复现。"""
    np.random.seed(SEED)
    yield


# ── 单频点分析 ──────────────────────────────────────────────

def test_carrier_strength_separates_station_from_noise():
    """强 FM 台信道 PSD 明显高于带外噪声底；纯噪声两者接近 0dB。"""
    np.random.seed(SEED)
    s_str, s_nf = _carrier_strength(_station(), SR)
    n_str, n_nf = _carrier_strength(_noise(), SR)
    assert s_str > 20.0                    # 恒包络宽带台：信道内能量集中
    assert n_str < 5.0                     # 白噪声：信道内外 PSD 同量级
    assert s_str - n_str > 15.0
    assert s_nf < 0.0                      # 0.05 幅度噪声的带外底为有限负 dB


def test_analyze_active_boundary_inclusive():
    """active = strength >= threshold：门限恰为实测强度时判有台，+1dB 判无台。"""
    np.random.seed(SEED)
    info = analyze_fm_frequency(_station(), SR)
    assert info["active"] is True
    strength = float(info["strength_db"])
    assert analyze_fm_frequency(_station(), SR, carrier_threshold_db=strength)["active"] is True
    assert analyze_fm_frequency(_station(), SR, carrier_threshold_db=strength + 1.0)["active"] is False


def test_stereo_pilot_detected_on_stereo_mpx():
    """立体声合成含 19k 导频 → stereo=True，pilot_snr 高于 -18dB 门限。"""
    np.random.seed(SEED)
    info = analyze_fm_frequency(_station(stereo=True), SR)
    assert info["stereo"] is True
    assert info["pilot_snr_db"] > -18.0


def test_mono_mpx_reports_not_stereo():
    """mono 合成无导频 → stereo=False，pilot_snr 远低于门限。"""
    np.random.seed(SEED)
    info = analyze_fm_frequency(_station(stereo=False), SR)
    assert info["stereo"] is False
    assert info["pilot_snr_db"] < -18.0
    assert info["active"] is True           # 有载波但非立体声


def test_detect_pilot_short_and_low_sr_inputs():
    """n<64 或 sr<60kHz 的导频检测显式不可判（False, None），不猜测。"""
    assert _detect_pilot_19k(np.zeros(32, dtype=np.complex128), SR) == (False, None)
    assert _detect_pilot_19k(np.zeros(256, dtype=np.complex128), 48000.0) == (False, None)


def test_zero_and_short_iq_honest_no_station():
    """全零/过短输入：强度按码内退化路径给出，active=False，不伪报台。"""
    z = analyze_fm_frequency(np.zeros(N, dtype=np.complex128), SR)
    assert z["active"] is False and z["stereo"] is False
    assert float(z["strength_db"]) == 0.0       # 信道与噪声底同为 -180dB
    assert float(z["noise_floor_db"]) == -180.0
    short = analyze_fm_frequency(np.zeros(16, dtype=np.complex128), SR)
    assert short["active"] is False and short["pilot_snr_db"] is None


# ── 扫频主函数 ──────────────────────────────────────────────

def test_scan_detects_single_station_and_misses_noise_bins():
    """97.5-98.5 扫描只在 98.0 检出台；噪声频点不进列表。"""
    def acquire(center, sr, n):
        if abs(center - 98.0e6) < 50e3:
            return _station(stereo=True)
        return _noise(n)

    r = scan_fm_band(acquire, 97.5, 98.5, 100.0, SR, N, carrier_threshold_db=6.0)
    assert [s.freq_mhz for s in r.stations] == [98.0]
    st = r.stations[0]
    assert st.active is True and st.stereo is True
    assert st.strength_db > 20.0
    assert r.noise_floor_db < 0.0
    assert r.step_khz == 100.0 and r.dwell_samples == N


def test_scan_orders_stations_by_strength_desc():
    """两台不同信噪：结果按 strength_db 降序，强台在前。"""
    def acquire(center, sr, n):
        if abs(center - 98.0e6) < 50e3:
            return _station(noise_level=0.3)     # 弱台
        if abs(center - 100.0e6) < 50e3:
            return _station(noise_level=0.02)    # 强台
        return _noise(n)

    r = scan_fm_band(acquire, 97.5, 100.5, 100.0, SR, N, carrier_threshold_db=6.0)
    assert [s.freq_mhz for s in r.stations] == [100.0, 98.0]
    strengths = [s.strength_db for s in r.stations]
    assert strengths == sorted(strengths, reverse=True)
    assert strengths[0] > strengths[1]


def test_scan_skips_none_and_exception_acquire():
    """acquire 抛异常/返回 None 的频点跳过，不影响其余频点检出。"""
    def acquire(center, sr, n):
        if abs(center - 97.7e6) < 50e3:
            raise RuntimeError("tuner busy")
        if abs(center - 97.9e6) < 50e3:
            return None
        if abs(center - 98.0e6) < 50e3:
            return _station()
        return _noise(n)

    r = scan_fm_band(acquire, 97.5, 98.5, 100.0, SR, N, carrier_threshold_db=6.0)
    assert [s.freq_mhz for s in r.stations] == [98.0]


def test_scan_all_none_gives_empty_honest_result():
    """全部采集失败：stations 为空、噪声底退化 -120，不编造数据。"""
    r = scan_fm_band(lambda *a: None, 97.5, 98.5, 100.0, SR, N)
    assert r.stations == []
    assert r.noise_floor_db == -120.0


# ── 预设持久化（重定向到 tmp，绝不写用户 ~/.mbdsdr） ───────

@pytest.fixture()
def presets_file(tmp_path, monkeypatch):
    f = tmp_path / "fm_presets.json"
    monkeypatch.setattr("mbdsdr_ai.fm_scan.PRESETS_DIR", str(tmp_path))
    monkeypatch.setattr("mbdsdr_ai.fm_scan.PRESETS_FILE", str(f))
    return f


def test_presets_roundtrip_and_compare_diff(presets_file):
    """save/load 往返；对比语义：±50kHz 容差内算 persistent，差集分 new/gone。"""
    old = [
        {"freq_mhz": 98.0, "strength_db": 30.0, "stereo": True, "active": True},
        {"freq_mhz": 100.0, "strength_db": 25.0, "stereo": False, "active": True},
    ]
    path = save_presets(old, {"range": "87-108"})
    assert path == str(presets_file)
    loaded = load_presets()
    assert loaded["stations"] == old
    assert loaded["last_scan"] == {"range": "87-108"}

    current = [
        {"freq_mhz": 98.02, "strength_db": 31.0, "stereo": True, "active": True},  # 容差内
        {"freq_mhz": 104.0, "strength_db": 20.0, "stereo": False, "active": True},  # 新增
    ]
    diff = compare_with_last_scan(current)
    assert [s["freq_mhz"] for s in diff["new"]] == [104.0]
    assert [s["freq_mhz"] for s in diff["gone"]] == [100.0]
    assert [s["freq_mhz"] for s in diff["persistent"]] == [98.02]


def test_load_presets_missing_file_empty_structure(presets_file):
    """文件不存在 → 显式空结构，不抛不伪造。"""
    data = load_presets()
    assert data == {"stations": [], "last_scan": None, "updated_at": None}


# ── 结果序列化 ──────────────────────────────────────────────

def test_scan_result_to_dict_shape():
    def acquire(center, sr, n):
        return _station() if abs(center - 98.0e6) < 50e3 else _noise(n)

    r = scan_fm_band(acquire, 97.5, 98.5, 100.0, SR, N, carrier_threshold_db=6.0)
    d = r.to_dict()
    assert d["f_start_mhz"] == 97.5 and d["f_stop_mhz"] == 98.5
    assert isinstance(d["stations"], list) and len(d["stations"]) == 1
    assert set(d["stations"][0]) == {"freq_mhz", "strength_db", "stereo", "active"}
    assert isinstance(FMStation(98.0, 30.0, True, True).to_dict(), dict)
