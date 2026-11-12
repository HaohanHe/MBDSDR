# SPDX-License-Identifier: MIT
"""onboarding 的 GNSS 授时端到端用例（D1：核心库不得依赖开发工具）。

这些用例走 ``tools/onboarding/onboard.py::step_record``，测的是**开发工具**
的行为，不是 ``mbdsdr_ai`` 的契约。原先放在 ``mbdsdr_ai/tests/`` 下，导致
``mbdsdr_ai -> tools`` 出现 6 条反向依赖边；按 D1 判据
（``tests/scripts -> tools`` 允许，``mbdsdr_ai -> tools`` 一律应修）迁到这里。

纯解析侧的用例仍留在 ``mbdsdr_ai/tests/test_gnss_timestamp_alignment.py``：
那一半确实只依赖核心库。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from mbdsdr_ai.serial_gnss import nmea_checksum  # noqa: E402


def nmea(body: str) -> str:
    return f"${body}*{nmea_checksum(body):02X}"


# 固定 NMEA 日志：RMC 给 2026-10-02 07:25:45Z，GGA 同刻。
RMC = nmea("GNRMC,072545.00,A,4352.0000,N,12519.0000,E,0.0,0.0,021026,,A")
GGA = nmea("GNGGA,072545.00,4352.0000,N,12519.0000,E,1,09,0.9,150.0,M,,,")


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
