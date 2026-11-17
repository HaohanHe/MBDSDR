# SPDX-License-Identifier: MIT
"""
Q2 时空对齐：GNSS 授时 → 解析为 UTC 时刻（确定性离线测试）

不联网、不接串口：
  - 注入**固定** NMEA 语句（带正确 XOR 校验和）；
  - 断言 NMEA → UTC 时刻的解析与回退口径（无日期就不猜）。

只测核心库的解析结果。「经 onboarding 写出 SigMF 再读回」的端到端
用例在 ``tests/test_onboarding_gnss_timestamp.py``：那几条要走开发工具
``tools/onboarding``，放在这里会让 ``mbdsdr_ai`` 反向依赖 ``tools``（D1）。

运行：python3 -m pytest mbdsdr_ai/tests/test_gnss_timestamp_alignment.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.serial_gnss import nmea_checksum  # noqa: E402
from mbdsdr_ai.gnss_timestamps import (  # noqa: E402
    TIME_SOURCE_PRECISION,
    nmea_log_to_utc_datetime,
    resolve_capture_datetime,
    to_sigmf_iso,
    nmea_hhmmss_to_seconds,
)


def nmea(body: str) -> str:
    return f"${body}*{nmea_checksum(body):02X}"


# 固定 NMEA 日志：RMC 给 2026-10-02 07:25:45Z，GGA 同刻。
RMC = nmea("GNRMC,072545.00,A,4352.0000,N,12519.0000,E,0.0,0.0,021026,,A")
GGA = nmea("GNGGA,072545.00,4352.0000,N,12519.0000,E,1,09,0.9,150.0,M,,,")
ZDA = nmea("GNZDA,072545.00,02,10,2026,,")


def test_nmea_parse_rmc_date_time():
    dt = nmea_log_to_utc_datetime([GGA, RMC])
    assert dt is not None
    assert dt.year == 2026 and dt.month == 10 and dt.day == 2
    assert dt.hour == 7 and dt.minute == 25 and dt.second == 45
    assert dt.tzinfo is not None


def test_nmea_zda_preferred():
    dt = nmea_log_to_utc_datetime([GGA, RMC, ZDA])
    assert dt is not None
    assert dt.isoformat().startswith("2026-10-02T07:25:45")


def test_gga_only_no_date_is_honest_none():
    """仅 GGA（无 RMC/ZDA）无法锚定日期 → 返回 None，绝不猜日期。"""
    assert nmea_log_to_utc_datetime([GGA]) is None


def test_resolve_nmea_vs_none():
    """无 PPS 的 NMEA 授时 → nmea；无授时源 → none（本机时钟，如实标注）。"""
    dt = nmea_log_to_utc_datetime([RMC])
    ct = resolve_capture_datetime(dt)
    assert ct.time_source == "nmea"
    assert ct.datetime_iso == "2026-10-02T07:25:45Z"
    # 无 PPS 时精度必须保守：只说秒级，不得宣称亚微秒级
    assert ct.precision == TIME_SOURCE_PRECISION["nmea"]
    assert "秒级" in ct.precision
    assert "亚微秒" not in ct.precision

    fixed = lambda: datetime(2026, 10, 2, 8, 0, 0, tzinfo=timezone.utc)
    ct2 = resolve_capture_datetime(None, clock=fixed)
    assert ct2.time_source == "none"
    assert ct2.datetime_iso == "2026-10-02T08:00:00Z"
    assert ct2.as_capture_fields()["mbdsdr:time_source"] == "none"
    assert ct2.as_capture_fields()["mbdsdr:time_precision"] == ct2.precision


def test_resolve_pps_precision():
    """有 PPS 才能真正宣称亚微秒级；自动升级与显式 pps 都要生效。"""
    dt = nmea_log_to_utc_datetime([RMC, ZDA])
    auto = resolve_capture_datetime(dt, has_pps=True)
    assert auto.time_source == "pps"
    assert auto.precision == TIME_SOURCE_PRECISION["pps"]
    assert "亚微秒" in auto.precision

    explicit = resolve_capture_datetime(dt, time_source="pps")
    assert explicit.time_source == "pps"

    # 声称 pps 却没给授时时刻：不得拿本机时钟冒充 → unknown
    fixed = lambda: datetime(2026, 10, 2, 8, 0, 0, tzinfo=timezone.utc)
    no_dt = resolve_capture_datetime(None, clock=fixed, time_source="pps")
    assert no_dt.time_source == "unknown"


def test_resolve_ntp_and_unknown():
    """NTP 来源给毫秒级；未知来源按最保守口径。"""
    fixed = lambda: datetime(2026, 10, 2, 8, 0, 0, tzinfo=timezone.utc)

    ntp = resolve_capture_datetime(clock=fixed, time_source="ntp")
    assert ntp.time_source == "ntp"
    assert "毫秒" in ntp.precision

    unknown = resolve_capture_datetime(
        datetime(2026, 10, 2, 7, 25, 45, tzinfo=timezone.utc),
        time_source="unknown")
    assert unknown.time_source == "unknown"
    assert unknown.precision == TIME_SOURCE_PRECISION["unknown"]

    # 旧口径归一：gnss → nmea，system → none
    dt = nmea_log_to_utc_datetime([RMC])
    assert resolve_capture_datetime(dt, time_source="gnss").time_source == "nmea"
    assert resolve_capture_datetime(None, clock=fixed,
                                    time_source="system").time_source == "none"


def test_to_sigmf_iso_utc_z():
    dt = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert to_sigmf_iso(dt) == "2026-01-02T03:04:05Z"


# 端到端（经 onboarding 写 SigMF）用例已迁至 tests/test_onboarding_gnss_timestamp.py，
# 见模块 docstring 的 D1 说明。
