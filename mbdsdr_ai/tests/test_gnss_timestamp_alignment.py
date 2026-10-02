# SPDX-License-Identifier: MIT
"""
Q2 时空对齐：GNSS 授时 → SigMF captures[0] 写读一致性（确定性离线测试）

不联网、不接串口：
  - 注入**固定** NMEA 语句（带正确 XOR 校验和）+ **固定**时钟；
  - 断言 onboard.record 写出的 captures[0].core:datetime 与 mbdsdr:time_source，
    再用 playback.parse_sigmf_meta 读回，写读一致；
  - 无 GNSS 时退化为系统时间并标 time_source="system"（诚实空态）。

运行：python3 -m pytest mbdsdr_ai/tests/test_gnss_timestamp_alignment.py -v
"""
from __future__ import annotations

import os
import sys
import json
import numpy as np
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.serial_gnss import nmea_checksum  # noqa: E402
from mbdsdr_ai.gnss_timestamps import (  # noqa: E402
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


def test_resolve_gnss_vs_system():
    dt = nmea_log_to_utc_datetime([RMC])
    ct = resolve_capture_datetime(dt)
    assert ct.time_source == "gnss"
    assert ct.datetime_iso == "2026-10-02T07:25:45Z"

    fixed = lambda: datetime(2026, 10, 2, 8, 0, 0, tzinfo=timezone.utc)
    ct2 = resolve_capture_datetime(None, clock=fixed)
    assert ct2.time_source == "system"
    assert ct2.datetime_iso == "2026-10-02T08:00:00Z"
    assert ct2.as_capture_fields()["mbdsdr:time_source"] == "system"


def test_to_sigmf_iso_utc_z():
    dt = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert to_sigmf_iso(dt) == "2026-01-02T03:04:05Z"


def _write_raw_uint8(path: str, n: int = 1000):
    """写一个最小 interleaved uint8 I/Q 录制（任意值）。"""
    rng = np.random.default_rng(0)
    raw = rng.integers(0, 256, size=2 * n, dtype=np.uint8)
    raw.tofile(path)


def test_onboard_record_gnss_writes_and_reads_back(tmp_path):
    """端到端：--gnss NMEA 日志 → captures[0].core:datetime=GNSS UTC，读回一致。"""
    from tools.onboarding.onboard import step_record

    raw = tmp_path / "raw.bin"
    _write_raw_uint8(str(raw))
    out = tmp_path / "out"

    gnss_file = tmp_path / "gnss.nmea"
    gnss_file.write_text("\n".join([GGA, RMC]) + "\n", encoding="utf-8")

    fixed = lambda: datetime(2026, 10, 2, 8, 0, 0, tzinfo=timezone.utc)
    res = step_record(str(raw), str(out), 1090e6, 2_400_000.0, 24.0, "adsb",
                      gnss_file=str(gnss_file), clock=fixed)
    assert res.status == "PASS", res.message
    meta_path = res.detail["sigmf_meta"]
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    cap = meta["captures"][0]
    # GNSS 时间写入 core:datetime，并显式标注来源
    assert cap["core:datetime"] == "2026-10-02T07:25:45Z"
    assert cap["mbdsdr:time_source"] == "gnss"

    # 读取端 playback.parse_sigmf_meta 对齐
    from mbdsdr_ai.playback import parse_sigmf_meta
    parsed = parse_sigmf_meta(meta_path)
    assert parsed["datetime"] == "2026-10-02T07:25:45Z"
    assert parsed["time_source"] == "gnss"


def test_onboard_record_no_gnss_falls_back_system(tmp_path):
    """无 --gnss：core:datetime=注入的系统时钟，标 time_source=system。"""
    from tools.onboarding.onboard import step_record

    raw = tmp_path / "raw.bin"
    _write_raw_uint8(str(raw))
    out = tmp_path / "out"
    fixed = lambda: datetime(2026, 10, 2, 8, 0, 0, tzinfo=timezone.utc)
    res = step_record(str(raw), str(out), 1090e6, 2_400_000.0, 24.0, "adsb",
                      clock=fixed)
    assert res.status == "PASS", res.message
    with open(res.detail["sigmf_meta"], encoding="utf-8") as f:
        cap = json.load(f)["captures"][0]
    assert cap["core:datetime"] == "2026-10-02T08:00:00Z"
    assert cap["mbdsdr:time_source"] == "system"


def test_onboard_record_gga_only_falls_back_system(tmp_path):
    """--gnss 只有 GGA（无日期）→ 不能锚定日期，诚实退回系统时间。"""
    from tools.onboarding.onboard import step_record

    raw = tmp_path / "raw.bin"
    _write_raw_uint8(str(raw))
    out = tmp_path / "out"
    gnss_file = tmp_path / "gga.nmea"
    gnss_file.write_text(GGA + "\n", encoding="utf-8")
    fixed = lambda: datetime(2026, 10, 2, 8, 0, 0, tzinfo=timezone.utc)
    res = step_record(str(raw), str(out), 1090e6, 2_400_000.0, 24.0, "adsb",
                      gnss_file=str(gnss_file), clock=fixed)
    assert res.status == "PASS"
    with open(res.detail["sigmf_meta"], encoding="utf-8") as f:
        cap = json.load(f)["captures"][0]
    assert cap["mbdsdr:time_source"] == "system"
