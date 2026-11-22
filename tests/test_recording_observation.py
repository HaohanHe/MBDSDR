# SPDX-License-Identifier: MIT
"""停止录制自动落观测档案（P1-08）：完整才写，不完整如实拒绝。"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.core.controller import SDRController  # noqa: E402


def _meta(tmp_path, *, complete=True, samples=8):
    data = tmp_path / "cap.sigmf-data"
    data.write_bytes(b"\x00" * 32)
    meta = {
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": 2_400_000.0,
            "core:frequency": 100_100_000.0,
            "core:total_num_samples": samples,
            "mbdsdr:gain_db": 20.0,
            "mbdsdr:device": "RTL-SDR #0",
            "mbdsdr:driver": "rtlsdr",
            "mbdsdr:complete": complete,
            "core:dataset": "cap.sigmf-data",
        },
        "captures": [{"core:sample_start": 0,
                      "core:datetime": "2026-11-19T15:54:20+00:00"}],
        "annotations": [],
    }
    if not complete:
        meta["global"]["mbdsdr:incomplete_reason"] = "未收到样本（0 字节）"
    p = tmp_path / "cap.sigmf-meta"
    p.write_text(json.dumps(meta), encoding="utf-8")
    return str(p)


@pytest.fixture()
def ctrl():
    return SDRController()


def test_complete_recording_produces_observation_record(tmp_path, ctrl):
    meta = _meta(tmp_path)
    path, err = ctrl._write_observation_record(meta)
    assert err is None, err
    assert path and path.endswith(".observation.json")
    assert os.path.exists(path)

    data = json.loads(open(path, encoding="utf-8").read())
    assert data["schema"] == "mbdsdr.observation/1"
    assert data["evidence"] == "real_hw"
    assert data["products"][0]["samples"] == 8
    assert data["tuning"]["center_freq_hz"] == 100_100_000.0
    # 设备身份取自产物 meta（本测试没有真实运行时快照）
    assert data["device"]["driver"] == "rtlsdr"


def test_incomplete_recording_writes_nothing(tmp_path, ctrl):
    meta = _meta(tmp_path, complete=False, samples=0)
    path, err = ctrl._write_observation_record(meta)
    assert path is None
    assert "未收到样本" in err
    assert not (tmp_path / "cap.observation.json").exists(), \
        "0 样本录制不得生成观测档案"


def test_missing_meta_is_reported_not_raised(tmp_path, ctrl):
    path, err = ctrl._write_observation_record(str(tmp_path / "nope.sigmf-meta"))
    assert path is None
    assert err  # 有原因
    assert not (tmp_path / "nope.observation.json").exists()


def test_archive_uses_snapshot_captured_at_start(tmp_path, ctrl):
    """观测的时空属性属于开始那一刻，不能被「结束后已断开」的空快照覆盖。

    回归背景（2026-11-20 真机实测）：GNSS 15 星定位中录了 2 秒 IQ，
    停止后取运行时快照已变成 `site.source=none`，档案里经纬度全空。
    """
    meta = _meta(tmp_path)
    snap = {
        "time": {"source": "gnss", "time_source": "nmea",
                 "precision": "秒（NMEA）", "following_system": False,
                 "last_update_utc": "2026-11-20T00:00:00+00:00"},
        "site": {"source": "gnss", "lat_deg": 43.7, "lon_deg": 126.6,
                 "alt_m": 215.0},
        "sdr": {"connected": True,
                "device": {"driver": "rtlsdr", "serial": "S1",
                           "name": "RTL-SDR #0"}},
    }
    path, err = ctrl._write_observation_record(meta, snapshot=snap)
    assert err is None, err
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["time"]["source"] == "gnss"
    assert data["site"]["source"] == "gnss"
    assert data["site"]["lat_deg"] == 43.7
    assert data["device"]["name"] == "RTL-SDR #0"
    assert any("观测开始时刻" in n for n in data["notes"]), \
        "用开始快照时必须在 notes 里写明来源时刻"


def test_archive_contains_ordered_timeline(tmp_path, ctrl):
    """「一次观测 = 时间轴」：起止事件必须落进档案且按 UTC 升序。"""
    meta = _meta(tmp_path)
    start = {"utc": "2026-11-20T00:00:00Z", "kind": "record_start",
             "detail": {"center_freq_hz": 100_100_000.0,
                        "sample_rate_hz": 2_400_000.0, "gain_db": 20.0}}
    stop = {"utc": "2026-11-20T00:00:02Z", "kind": "record_stop",
            "detail": {"samples": 8}}
    path, err = ctrl._write_observation_record(meta, timeline=[start, stop])
    assert err is None, err

    data = json.loads(open(path, encoding="utf-8").read())
    assert [e["kind"] for e in data["timeline"]] == \
        ["record_start", "record_stop"]
    assert data["timeline"][0]["utc"] < data["timeline"][1]["utc"]
    assert data["timeline"][0]["detail"]["center_freq_hz"] == 100_100_000.0


def test_empty_timeline_is_omitted_not_faked(tmp_path, ctrl):
    """没有事件就不编：timeline 为空列表，不能塞一条假事件。"""
    meta = _meta(tmp_path)
    path, err = ctrl._write_observation_record(meta)
    assert err is None
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["timeline"] == []
