# SPDX-License-Identifier: MIT
"""观测档案（PRD P1-08）：无产物拒绝导出、来源不冒充、往返一致。"""
import csv
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.observation_record import (  # noqa: E402
    CSV_COLUMNS,
    SCHEMA,
    ObservationRecord,
    product_from_sigmf,
    record_from_snapshot,
)


def _snapshot(*, site_source="gnss", time_source="gnss"):
    return {
        "time": {
            "source": time_source,
            "time_source": "nmea" if time_source == "gnss" else "none",
            "precision": "秒（NMEA）" if time_source == "gnss" else "未对时",
            "following_system": False if time_source == "gnss" else True,
            "last_update_utc": "2026-11-19T15:08:38+00:00" if time_source == "gnss" else None,
        },
        "site": {
            "source": site_source,
            "lat_deg": 43.7 if site_source == "gnss" else None,
            "lon_deg": 126.6 if site_source == "gnss" else None,
            "alt_m": 215.0 if site_source == "gnss" else None,
        },
        "sdr": {
            "connected": True,
            "device": {"driver": "rtlsdr", "serial": "77771111153705700",
                       "name": "RTL-SDR #0"},
        },
        "gnss": {"connected": True, "fix": "real"},
    }


def _record(**over):
    base = dict(
        observation_id="obs-0001",
        snapshot=_snapshot(),
        started_utc="2026-11-19T15:00:00+00:00",
        ended_utc="2026-11-19T15:05:00+00:00",
        target={"name": "NOAA 15", "norad_id": 25338},
        tuning={"center_freq_hz": 137_620_000.0, "sample_rate_hz": 2_400_000.0,
                "gain_db": 20.0, "doppler_residual_hz": 12.5},
        products=[{"path": "cap.sigmf-meta", "kind": "sigmf-meta", "bytes": 1024}],
        result={"summary": "解出 3 条 APT 行"},
        evidence="real_hw",
    )
    base.update(over)
    return record_from_snapshot(**base)


# ---------------------------------------------------------------------------
# 一、无产物拒绝导出
# ---------------------------------------------------------------------------

def test_empty_products_are_refused(tmp_path):
    rec = _record(products=[])
    errs = rec.validate_for_export()
    assert any("没有观测产物" in e for e in errs)

    j = tmp_path / "obs.json"
    ok, err = rec.write_json(str(j))
    assert ok is False and "没有观测产物" in err
    assert not j.exists(), "拒绝导出时不得留下文件"

    c = tmp_path / "obs.csv"
    ok, err = rec.write_csv(str(c))
    assert ok is False
    assert not c.exists()


def test_product_without_path_is_refused():
    rec = _record(products=[{"kind": "sigmf-meta"}])
    assert any("缺少 path" in e for e in rec.validate_for_export())


def test_invalid_evidence_level_is_refused():
    rec = _record(evidence="probably_real")
    assert any("证据强度" in e for e in rec.validate_for_export())


# ---------------------------------------------------------------------------
# 二、来源不冒充
# ---------------------------------------------------------------------------

def test_system_time_does_not_poss_out_as_gnss():
    rec = _record(snapshot=_snapshot(time_source="system"))
    assert rec.time["source"] == "system"
    assert rec.time["precision"] == "未对时"
    assert rec.time["last_update_utc"] is None


def test_missing_site_keeps_none_not_zero():
    rec = _record(snapshot=_snapshot(site_source="none"))
    assert rec.site["source"] == "none"
    assert rec.site["lat_deg"] is None
    assert rec.site["lon_deg"] is None
    assert rec.site["alt_m"] is None


def test_site_altitude_flows_into_record():
    """快照里的站址海拔必须原样进档案，不能只剩经纬度。"""
    rec = _record()
    assert rec.site["source"] == "gnss"
    assert rec.site["alt_m"] == 215.0


# ---------------------------------------------------------------------------
# 三、序列化往返
# ---------------------------------------------------------------------------

def test_json_roundtrip_and_schema(tmp_path):
    rec = _record()
    p = tmp_path / "obs.json"
    ok, err = rec.write_json(str(p))
    assert ok is True, err

    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["schema"] == SCHEMA
    assert data["observation_id"] == "obs-0001"
    assert data["target"]["norad_id"] == 25338
    assert data["tuning"]["center_freq_hz"] == 137_620_000.0
    assert data["products"][0]["path"] == "cap.sigmf-meta"
    assert not list(tmp_path.glob("*.tmp")), "原子写不得残留临时文件"


def test_csv_columns_and_values(tmp_path):
    rec = _record()
    p = tmp_path / "obs.csv"
    ok, err = rec.write_csv(str(p))
    assert ok is True, err

    with open(p, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert tuple(rows[0].keys()) == CSV_COLUMNS
    assert rows[0]["observation_id"] == "obs-0001"
    assert rows[0]["time_source"] == "gnss"
    assert rows[0]["product_count"] == "1"
    assert rows[0]["product_bytes"] == "1024"
    assert rows[0]["evidence"] == "real_hw"


# ---------------------------------------------------------------------------
# 四、从 SigMF meta 读产物
# ---------------------------------------------------------------------------

def _write_meta(tmp_path, *, complete=True, with_dataset=True):
    data = tmp_path / "cap.sigmf-data"
    if with_dataset:
        data.write_bytes(b"\x00" * 64)
    meta = {
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": 2_400_000.0,
            "core:frequency": 137_620_000.0,
            "core:total_num_samples": 8,
            "mbdsdr:gain_db": 20.0,
            "mbdsdr:device": "RTL-SDR #0",
            "mbdsdr:driver": "rtlsdr",
            "mbdsdr:complete": complete,
        },
        "captures": [{"core:sample_start": 0,
                      "core:datetime": "2026-11-19T15:00:00+00:00"}],
        "annotations": [],
    }
    if with_dataset:
        meta["global"]["core:dataset"] = "cap.sigmf-data"
    if not complete:
        meta["global"]["mbdsdr:incomplete_reason"] = "未收到样本（0 字节）"
    p = tmp_path / "cap.sigmf-meta"
    p.write_text(json.dumps(meta), encoding="utf-8")
    return p


def test_product_from_sigmf_reads_declared_fields(tmp_path):
    meta_path = _write_meta(tmp_path)
    prod = product_from_sigmf(str(meta_path))
    assert prod["center_freq_hz"] == 137_620_000.0
    assert prod["sample_rate_hz"] == 2_400_000.0
    assert prod["samples"] == 8
    assert prod["complete"] is True
    assert prod["bytes"] == 64
    assert prod["sha256"] and len(prod["sha256"]) == 64
    assert prod["started_utc"] == "2026-11-19T15:00:00+00:00"


def test_product_from_sigmf_surfaces_incomplete_reason(tmp_path):
    meta_path = _write_meta(tmp_path, complete=False)
    prod = product_from_sigmf(str(meta_path))
    assert prod["complete"] is False
    assert "未收到样本" in prod["incomplete_reason"]


def test_product_from_sigmf_missing_file_is_reported_not_faked(tmp_path):
    prod = product_from_sigmf(str(tmp_path / "nope.sigmf-meta"))
    assert "error" in prod
    assert prod.get("center_freq_hz") is None


# ---------------------------------------------------------------------------
# 五、设备断开后：设备身份从产物 meta 回填（两个真来源取其一）
# ---------------------------------------------------------------------------

def _snapshot_without_device():
    snap = _snapshot()
    snap["sdr"] = {"connected": False, "device": {}}
    return snap


def test_device_identity_falls_back_to_product_meta():
    prod = {"path": "cap.sigmf-meta", "driver": "rtlsdr",
            "device": "RTL-SDR #0", "bytes": 64}
    rec = _record(snapshot=_snapshot_without_device(), products=[prod])
    assert rec.device["driver"] == "rtlsdr"
    assert rec.device["name"] == "RTL-SDR #0"
    assert any("产物 SigMF meta" in n for n in rec.notes), \
        "回填来源必须在 notes 里写明，不能当成快照值"


def test_snapshot_device_wins_over_product_meta():
    prod = {"path": "cap.sigmf-meta", "driver": "rtlsdr",
            "device": "旧名字", "bytes": 64}
    rec = _record(products=[prod])          # 默认快照里设备在线
    assert rec.device["name"] == "RTL-SDR #0"
    assert rec.notes == [], "快照有设备时不该加回填说明"


def test_snapshot_device_as_string_is_accepted():
    """真实 RuntimeSources 的 ``sdr.device`` 是设备名字符串，不是字典。

    回归背景：初版按 ``dev.get("name")`` 解析，离线测试用字典形状通过，
    一连真机就 ``AttributeError: 'str' object has no attribute 'get'``。
    """
    snap = _snapshot()
    snap["sdr"] = {"connected": True, "device": "RTL-SDR #0"}
    prod = {"path": "cap.sigmf-meta", "driver": "rtlsdr",
            "device": "RTL-SDR #0", "bytes": 64}
    rec = _record(snapshot=snap, products=[prod])

    assert rec.device["name"] == "RTL-SDR #0"
    assert rec.device["driver"] == "rtlsdr", "driver 仍应从产物 meta 回填"
    assert any("driver" in n and "产物" in n for n in rec.notes), \
        "回填的部分要在 notes 里点名"

