#!/usr/bin/env python3
"""
标杆精髓默认参数回归测试
========================
覆盖 GQRX / direwolf / dump1090 / rtl_433 标杆源码移植到 MBDSDR 内核的关键默认值：

  T1  RTL-SDR 首启增益自动拉到离散增益表中点（gqrx mainwindow.cpp:571-579）
  T2  AX.25 去重升级为 CRC16 指纹 + 30s TTL（direwolf dedupe.c:134,245）
  T3  ADS-B ICAO 双缓冲老化表 + even/odd CPR 60s TTL（dump1090 icao_filter.c:23,26,118-125）
  T4  统一默认常量值（SDRStatus / AudioPlayer / rtl433 / adsb / ax25）

本测试不依赖真实硬件；RTL 后端用轻量假设备对象替代 pyrtlsdr 句柄，
仅验证默认参数决策逻辑本身。
"""

import os
import sys
import time
import types
from types import SimpleNamespace

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


# ---------------------------------------------------------------------------
# T1：RTL-SDR 首启增益中点
# ---------------------------------------------------------------------------
def test_rtl_first_gain_midpoint():
    from mbdsdr_ai.sdr_backend import RTLSDRBackend

    # 不连真实硬件，仅实例化（__init__ 不导入 rtlsdr）
    be = RTLSDRBackend(device_index=0)

    # 7 档离散增益表，中点索引 3 = 12.0 dB
    gain_table = [0.0, 6.0, 9.0, 12.0, 18.0, 24.0, 30.0]
    be._gain_table_db = gain_table
    # 模拟 readback_hw_state() 回读到硬件默认 0 dB
    be.status.gain_db = 0.0
    be.status.connected = True

    # 假设备句柄：记录 set_manual_gain_mode 调用与最终 gain
    fake_sdr = SimpleNamespace(gain=0.0, gain_mode=None)
    fake_sdr.set_manual_gain_mode = lambda m: setattr(fake_sdr, "gain_mode", m)
    be._sdr = fake_sdr

    be._maybe_apply_first_gain_midpoint()

    # 应拉到中点 12.0 dB（gain_table[7//2] = gain_table[3]）
    assert be.status.gain_db == 12.0, \
        f"首启增益应为中点 12.0 dB，实际 {be.status.gain_db}"
    assert fake_sdr.gain == 12.0, \
        f"假设备 gain 应为 12.0，实际 {fake_sdr.gain}"
    assert fake_sdr.gain_mode == 1, "应切到 manual gain mode"


def test_rtl_first_gain_no_override_when_user_set():
    """用户已手动设过非零增益时，首启逻辑不应覆盖。"""
    from mbdsdr_ai.sdr_backend import RTLSDRBackend

    be = RTLSDRBackend(device_index=0)
    be._gain_table_db = [0.0, 6.0, 12.0, 18.0, 24.0]
    be.status.gain_db = 20.0  # 用户手动设过
    be.status.connected = True
    fake_sdr = SimpleNamespace(gain=20.0, gain_mode=None)
    fake_sdr.set_manual_gain_mode = lambda m: setattr(fake_sdr, "gain_mode", m)
    be._sdr = fake_sdr

    be._maybe_apply_first_gain_midpoint()

    assert be.status.gain_db == 20.0, "用户手动设过的增益不应被首启逻辑覆盖"


def test_rtl_first_gain_empty_table_no_crash():
    """增益表为空时不应崩溃，也不应改 gain_db。"""
    from mbdsdr_ai.sdr_backend import RTLSDRBackend

    be = RTLSDRBackend(device_index=0)
    be._gain_table_db = []
    be.status.gain_db = 0.0
    be.status.connected = True
    be._sdr = SimpleNamespace(gain=0.0)

    be._maybe_apply_first_gain_midpoint()  # 不应抛异常

    assert be.status.gain_db == 0.0


# ---------------------------------------------------------------------------
# T2：AX.25 CRC16 指纹 + 30s TTL 去重
# ---------------------------------------------------------------------------
def _make_frame(src="BI4XYZ", dst="APRS", info=b"hello world"):
    from mbdsdr_ai.ax25 import AX25Frame
    return AX25Frame(
        destination=dst, dest_ssid=0,
        source=src, source_ssid=0,
        digipeaters=[], control=0x03, pid=0xF0,
        info=info, fcs_valid=True,
    )


def test_ax25_dedup_within_ttl():
    from mbdsdr_ai.ax25 import Digipeater, DEDUP_TTL_SEC
    assert DEDUP_TTL_SEC == 30, "DEDUP_TTL_SEC 应为 30s"

    d = Digipeater(mycall="N0CALL")
    f = _make_frame()

    # 第一次见到：不判重
    assert d._is_duplicate(f) is False
    # 立刻再见：TTL 窗口内同指纹 → 判重
    assert d._is_duplicate(f) is True


def test_ax25_dedup_expires_after_ttl():
    from mbdsdr_ai.ax25 import Digipeater, DEDUP_TTL_SEC
    d = Digipeater(mycall="N0CALL")
    f = _make_frame()

    assert d._is_duplicate(f) is False
    # 把缓冲里的时间戳手动拨到 TTL 之外
    old = time.time() - DEDUP_TTL_SEC - 1
    d.duplicate_buffer = [(fp, old) for (fp, _ts) in d.duplicate_buffer]

    # 超过 TTL，同指纹不应再判重
    assert d._is_duplicate(f) is False, "超过 30s TTL 后同指纹不应判重"


def test_ax25_dedup_different_payload():
    """不同 info 的帧指纹不同，不应互相判重。"""
    from mbdsdr_ai.ax25 import Digipeater
    d = Digipeater(mycall="N0CALL")
    f1 = _make_frame(info=b"first packet")
    f2 = _make_frame(info=b"second packet")

    assert d._is_duplicate(f1) is False
    assert d._is_duplicate(f2) is False, "不同 info 的帧不应判重"


# ---------------------------------------------------------------------------
# T3：ADS-B ICAO 双缓冲老化 + CPR TTL
# ---------------------------------------------------------------------------
def test_adsb_icao_seen_then_flip():
    from mbdsdr_ai.adsb import ADSBDecoder, ICAO_FILTER_TTL_SEC
    assert ICAO_FILTER_TTL_SEC == 60.0

    dec = ADSBDecoder()
    dec._mark_icao("ABC123")
    assert dec._is_icao_recent("ABC123") is True
    assert dec._is_icao_recent("DEF456") is False

    # 模拟过了一个 TTL 周期
    dec._icao_last_flip = time.time() - ICAO_FILTER_TTL_SEC - 1
    # 一次翻转后：旧 active 变 inactive，ABC123 仍在 inactive set 中（2*TTL 宽限）
    dec._maybe_flip_icao()
    assert dec._is_icao_recent("ABC123") is True, \
        "一次翻转后 ICAO 应仍在宽限窗口内"

    # 再过一个 TTL，第二次翻转：旧 inactive set 被清空
    dec._icao_last_flip = time.time() - ICAO_FILTER_TTL_SEC - 1
    dec._maybe_flip_icao()
    assert dec._is_icao_recent("ABC123") is False, \
        "两次翻转（约 120s）后 ICAO 应被清除"


def test_adsb_cpr_purge_stale():
    from mbdsdr_ai.adsb import ADSBDecoder, ICAO_FILTER_TTL_SEC
    dec = ADSBDecoder()

    now = time.time()
    # 一条新鲜条目，一条过期条目
    dec._even["AAA111"] = {"cpr_lat": 100, "cpr_lon": 200, "ts": now - 10}
    dec._odd["BBB222"] = {"cpr_lat": 300, "cpr_lon": 400,
                          "ts": now - ICAO_FILTER_TTL_SEC - 5}

    dec._purge_stale_cpr()

    assert "AAA111" in dec._even, "10s 前的 even 条目应保留"
    assert "BBB222" not in dec._odd, "超过 60s 的 odd 条目应被清除"


# ---------------------------------------------------------------------------
# T4：默认常量值
# ---------------------------------------------------------------------------
def test_default_sdr_status():
    from mbdsdr_ai.sdr_backend import SDRStatus
    s = SDRStatus()
    # 首启守 FM 广播段 98 MHz
    assert s.frequency_hz == 98_000_000.0, f"默认频点应为 98 MHz，实际 {s.frequency_hz}"
    # 采样率 None = 未选定，由后端子类决定
    assert s.sample_rate_hz is None, f"基类默认采样率应为 None，实际 {s.sample_rate_hz}"
    # 静噪 -150 dB = 完全开门（对齐 dockrxopt.cpp:697）
    assert s.squelch_db == -150.0, f"默认静噪应为 -150.0，实际 {s.squelch_db}"


def test_audio_default_gain():
    from mbdsdr_ai.audio_out import AudioPlayer
    p = AudioPlayer()
    assert p.gain == 0.5, f"音频默认增益应为 0.5（≈-6dB），实际 {p.gain}"


def test_rtl433_defaults():
    from mbdsdr_ai import rtl433_decoder as r433
    assert r433.RTL433_DEFAULT_SAMPLE_RATE == 250_000
    assert r433.RTL433_DEFAULT_FREQUENCY_HZ == 433_920_000
    assert r433.RTL433_DEFAULT_GAIN_DB is None, "RTL433 默认增益 None=AGC"
    assert r433.RTL433_MIN_SNR_DB == 9.0
    assert r433.RTL433_MIN_LEVEL_DB == -12.1442


def test_adsb_defaults():
    from mbdsdr_ai import adsb
    assert adsb.MODES_DEFAULT_FREQ_HZ == 1_090_000_000
    assert adsb.MODES_DEFAULT_SPS == 2_400_000
    assert adsb.MODES_DEFAULT_GAIN_DB == 999_999, "999999=AGC 哨兵"
    assert adsb.ICAO_FILTER_TTL_SEC == 60.0


def test_backend_subclass_sample_rates():
    """各后端子类在 __init__ 里设了自己的甜点采样率。"""
    from mbdsdr_ai.sdr_backend import (
        RTLSDRBackend, HackRFBackend, USRPBackend, PlutoSDRBackend,
    )
    rtl = RTLSDRBackend(device_index=0)
    assert rtl.status.sample_rate_hz == 2_048_000.0

    hackrf = HackRFBackend()
    assert hackrf.status.sample_rate_hz == 8_000_000.0

    usrp = USRPBackend()
    assert usrp.status.sample_rate_hz == 1_000_000.0

    pluto = PlutoSDRBackend()
    assert pluto.status.sample_rate_hz == 2_000_000.0
