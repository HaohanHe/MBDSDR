# SPDX-License-Identifier: MIT
"""GNSS 完整 UTC（含日期）从 NMEA → fix → GNSSPosition → 运行时快照的贯通。

只喂合成 NMEA：不开串口、不启后台线程、不联网。

诚实口径：
* RMC 的 hhmmss 必须与 ddmmyy 合并才算完整 UTC；
* ZDA 自带年月日，优先于 RMC；
* GGA/GLL 只有时刻没有日期 —— ``utc_datetime`` 必须为 None，不许拼当天日期；
* ``RuntimeSources.record_fix`` 只有拿到完整 UTC 才写时间引擎并标 nmea。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.gnss_monitor import RealGNSSMonitor  # noqa: E402
from mbdsdr_ai.runtime_sources import RuntimeSources  # noqa: E402
from mbdsdr_ai.serial_gnss import (  # noqa: E402
    NMEAParser,
    SerialGNSSReader,
    nmea_checksum,
)

UTC = timezone.utc

# --- 合成语句（校验和动态计算，绝不手写） ---
GGA_BODY = "GNGGA,072545.00,4352.0000,N,12519.0000,E,1,09,0.9,150.0,M,0.0,M,,"
RMC_BODY = "GPRMC,072545.00,A,4352.0000,N,12519.0000,E,5.0,30.0,240926,,A"
# ZDA 故意用另一天/时刻：只要它在场就必须压过 RMC
ZDA_BODY = "GNZDA,101010.00,25,09,2026,,"

EXPECT_RMC_UTC = datetime(2026, 9, 24, 7, 25, 45, tzinfo=UTC)
EXPECT_ZDA_UTC = datetime(2026, 9, 25, 10, 10, 10, tzinfo=UTC)


def mk(body: str) -> str:
    return f"${body}*{nmea_checksum(body):02X}"


def _reader_with(*bodies: str) -> SerialGNSSReader:
    """把合成语句逐条合并进 reader（等价于串口线程解析后的 _merge）。"""
    reader = SerialGNSSReader()
    parser = NMEAParser()
    for body in bodies:
        rec = parser.parse(mk(body))
        assert rec is not None, f"NMEA 解析失败：{body}"
        reader._merge(rec)
    return reader


def _as_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert dt.tzinfo is not None, f"utc_datetime 必须带 UTC 时区：{value!r}"
    return dt.astimezone(UTC)


class _FakeEngine:
    """最小时间引擎替身：只记 set_unix / set_time_now 调用。"""

    def __init__(self):
        self.unix = None
        self.rate = 1.0
        self.set_unix_calls = []
        self.set_time_now_calls = 0

    def set_unix(self, ts):
        self.unix = float(ts)
        self.rate = 0.0
        self.set_unix_calls.append(float(ts))

    def resume(self, rate=1.0):
        self.rate = float(rate)

    def set_time_now(self):
        self.unix = None
        self.rate = 1.0
        self.set_time_now_calls += 1

    def is_following_system(self) -> bool:
        return self.unix is None

    def now_unix(self) -> float:
        return self.unix if self.unix is not None else 0.0


# ============================================================
# 1. RMC（日期+时间）→ fix 完整 UTC → GNSSPosition 透传
# ============================================================

def test_rmc_with_date_fills_utc_datetime_and_position_passes_it():
    reader = _reader_with(RMC_BODY)
    fix = reader.get_fix()
    assert fix["source"] == "real"
    assert fix["utc_time"] == "072545.00"
    assert fix["date"] == "240926"
    assert fix["utc_datetime"] is not None
    assert _as_utc(fix["utc_datetime"]) == EXPECT_RMC_UTC

    monitor = RealGNSSMonitor()
    monitor._reader = reader
    pos = monitor.get_position()
    assert pos.source == "real"
    assert pos.utc_datetime == fix["utc_datetime"]
    assert pos.date == fix["date"]


# ============================================================
# 2. ZDA 优先（无论先收到哪一条）
# ============================================================

def test_zda_takes_priority_over_rmc_in_both_orders():
    for bodies in ((RMC_BODY, ZDA_BODY), (ZDA_BODY, RMC_BODY)):
        fix = _reader_with(*bodies).get_fix()
        assert fix["utc_datetime"] is not None
        assert _as_utc(fix["utc_datetime"]) == EXPECT_ZDA_UTC, bodies
        # date 与 ZDA 的日期一致（ddmmyy）
        assert fix["date"] == "250926", bodies


# ============================================================
# 3. 只有 GGA（无日期）→ utc_datetime=None，不许拼当天日期
# ============================================================

def test_gga_only_has_no_utc_datetime_and_no_invented_date():
    fix = _reader_with(GGA_BODY).get_fix()
    assert fix["source"] == "real"
    assert fix["latitude"] is not None
    assert fix["utc_datetime"] is None
    assert fix["date"] is None


# ============================================================
# 4. record_fix：完整 UTC 才写时间引擎，hhmmss-only 回退
# ============================================================

def _fix(**overrides):
    base = {
        "source": "real",
        "latitude": 43.8666667,
        "longitude": 125.3166667,
        "altitude_m": 150.0,
        "utc_time": "072545.00",
        "utc_datetime": None,
        "date": None,
        "timestamp": 1_700_000_000.0,
    }
    base.update(overrides)
    return base


def test_record_fix_with_complete_utc_writes_time_engine():
    engine = _FakeEngine()
    sources = RuntimeSources(time_engine=engine)
    snap = sources.record_fix(_fix(utc_datetime=EXPECT_RMC_UTC.isoformat()))
    assert engine.set_unix_calls == [EXPECT_RMC_UTC.timestamp()]
    assert snap["gnss"]["time_source"] == "nmea"
    assert snap["time"]["source"] == "gnss"
    assert snap["gnss"]["fix"] is True


def test_record_fix_combines_utc_time_and_date_when_utc_datetime_absent():
    engine = _FakeEngine()
    sources = RuntimeSources(time_engine=engine)
    sources.record_fix(_fix(utc_time="072545.00", date="240926"))
    assert engine.set_unix_calls == [EXPECT_RMC_UTC.timestamp()]


def test_record_fix_hhmmss_only_falls_back_with_reason():
    engine = _FakeEngine()
    sources = RuntimeSources(time_engine=engine)
    snap = sources.record_fix(_fix())
    # 没有完整 UTC：不写时间引擎，回退系统时钟并给出原因
    assert engine.set_unix_calls == []
    assert engine.is_following_system() is True
    assert snap["gnss"]["time_source"] == "none"
    assert snap["time"]["source"] == "system"
    assert "缺完整 UTC" in snap["gnss"]["reason"]
