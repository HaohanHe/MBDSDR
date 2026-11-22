# SPDX-License-Identifier: MIT
"""``mbdsdr.spatial/1`` 离线观测包契约测试（spec S3）。

覆盖：正常档案、无站址、无 IQ、空时间轴、录音缺失、星点截断、
重复生成确定性、CLI 真实频谱帧、CLI 默认星表 / --no-stars。
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import wave
from datetime import datetime, timezone

import numpy as np

from mbdsdr_ai.astronomy import jd_to_lst, unix_to_jd
from mbdsdr_ai.observation_record import (
    ObservationRecord,
    product_from_sigmf,
)
from mbdsdr_ai.spatial_bundle import (
    SCHEMA,
    _altaz_geometric,
    build_spatial_bundle,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_CLI_PATH = os.path.join(_ROOT, "scripts", "export_spatial_bundle.py")

_EXPECTED_KEYS = {
    "schema", "created_utc", "observation_id", "site", "time", "sky",
    "spectrum", "audio", "timeline", "evidence", "blockers",
}


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
def _write_sigmf(tmp_path, name="fixture", n=32768,
                 rate=2.4e6, freq=100.1e6):
    """造一对真实的小 SigMF 数据/元数据文件（确定性双音信号）。"""
    data_path = tmp_path / f"{name}.sigmf-data"
    meta_path = tmp_path / f"{name}.sigmf-meta"
    t = np.arange(n, dtype=np.float64)
    x = (0.5 * np.exp(2j * np.pi * 0.25 * t)
         + 0.1 * np.exp(2j * np.pi * -0.1 * t)).astype(np.complex64)
    x.tofile(str(data_path))
    meta = {
        "global": {
            "core:datatype": "cf32_le",
            "core:version": "1.0.0",
            "core:sample_rate": rate,
            "core:frequency": freq,
            "core:dataset": data_path.name,
            "core:total_num_samples": n,
            "mbdsdr:complete": True,
        },
        "captures": [{
            "core:sample_offset": 0,
            "core:datetime": "2026-01-01T00:00:00Z",
            "mbdsdr:time_source": "none",
        }],
        "annotations": [],
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return meta_path, data_path


def _timeline():
    return [
        {"utc": "2026-01-01T00:00:00+00:00", "kind": "record_start",
         "detail": {"center_freq_hz": 100.1e6, "sample_rate_hz": 2.4e6},
         "samples": 0},
        {"utc": "2026-01-01T00:00:01+00:00", "kind": "tune",
         "detail": {"from_hz": 100.1e6, "to_hz": 98.0e6}, "samples": 2400000},
    ]


def _record(**overrides):
    base = dict(
        observation_id="obs-spatial-test",
        started_utc="2026-01-01T00:00:00+00:00",
        time={"source": "system", "precision": "本机时钟（未授时）"},
        site={"source": "gnss", "lat_deg": 10.0, "lon_deg": 20.0,
              "alt_m": 100.0},
        tuning={"center_freq_hz": 100.1e6, "sample_rate_hz": 2.4e6,
                "gain_db": 30.0},
        products=[],
        evidence="real_hw",
        timeline=_timeline(),
    )
    base.update(overrides)
    return ObservationRecord(**base)


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "export_spatial_bundle", _CLI_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 1. 正常档案
# ---------------------------------------------------------------------------
def test_normal_archive_has_full_contract(tmp_path):
    meta_path, _ = _write_sigmf(tmp_path)
    rec = _record(products=[product_from_sigmf(str(meta_path))])

    bundle = build_spatial_bundle(rec)

    assert set(bundle) == _EXPECTED_KEYS
    assert bundle["schema"] == SCHEMA
    assert bundle["observation_id"] == "obs-spatial-test"
    assert bundle["site"] == {"lat_deg": 10.0, "lon_deg": 20.0,
                              "alt_m": 100.0, "source": "gnss"}
    assert bundle["time"]["utc"] == "2026-01-01T00:00:00+00:00"
    assert bundle["time"]["source"] == "system"
    assert bundle["time"]["precision"] == "本机时钟（未授时）"
    assert bundle["evidence"] == "real_hw"
    assert len(bundle["timeline"]) == 2
    assert [e["kind"] for e in bundle["timeline"]] == ["record_start", "tune"]
    assert bundle["timeline"][1]["samples"] == 2400000
    assert bundle["spectrum"]["frames"] == []
    assert bundle["spectrum"]["center_freq_hz"] == 100.1e6
    # 没有帧时来源写 none：头显不得在「没有频谱帧」旁声称「录制回放」。
    assert bundle["spectrum"]["source"] == "none"
    assert bundle["sky"] == {"stars": [], "passes": [],
                             "total_stars": 0, "truncated": False,
                             "source": "none",
                             "altaz_source": "geometric",
                             "altaz_refraction": False,
                             "altaz_pm_applied": 0,
                             "altaz_pm_missing": 0}
    assert bundle["audio"] == []
    assert bundle["blockers"] == []


# ---------------------------------------------------------------------------
# 2. 无站址
# ---------------------------------------------------------------------------
def test_no_site_source_keeps_none_not_zero():
    rec = _record(site={"source": "none", "lat_deg": None, "lon_deg": None,
                        "alt_m": None})

    bundle = build_spatial_bundle(rec)

    assert any("没有可用站址" in b for b in bundle["blockers"])
    assert bundle["site"]["lat_deg"] is None
    assert bundle["site"]["lon_deg"] is None
    assert bundle["site"]["alt_m"] is None
    assert bundle["site"]["source"] == "none"


# ---------------------------------------------------------------------------
# 3. 无 IQ
# ---------------------------------------------------------------------------
def test_no_iq_marks_blocker_and_empty_frames():
    rec = _record(products=[])

    bundle = build_spatial_bundle(rec)

    assert bundle["spectrum"]["frames"] == []
    assert bundle["spectrum"]["source"] == "none"
    assert "没有可回放的 IQ 数据" in bundle["blockers"]


# ---------------------------------------------------------------------------
# 3a. 频谱来源：只有确有帧才算「录制回放」
# ---------------------------------------------------------------------------
def test_spectrum_source_recorded_only_with_frames():
    rec = _record()

    without_frames = build_spatial_bundle(rec)
    assert without_frames["spectrum"]["source"] == "none"

    with_frames = build_spatial_bundle(
        rec, spectrum={"frames": [{"at_s": 0.0, "db": [-90.0, -80.0]}]})
    assert with_frames["spectrum"]["source"] == "recorded"


# ---------------------------------------------------------------------------
# 3b. 录制总样本数（spectrum.total_samples）
# ---------------------------------------------------------------------------
def test_spectrum_total_samples_sums_product_samples():
    rec = _record(products=[{"path": "a.sigmf-meta", "samples": 4800}])

    bundle = build_spatial_bundle(rec)

    assert bundle["spectrum"]["total_samples"] == 4800


def test_spectrum_total_samples_none_without_product_samples():
    rec = _record(products=[{"path": "a.sigmf-meta"}])

    bundle = build_spatial_bundle(rec)

    assert bundle["spectrum"]["total_samples"] is None


# ---------------------------------------------------------------------------
# 4. 空时间轴
# ---------------------------------------------------------------------------
def test_empty_timeline_marks_blocker():
    rec = _record(timeline=[])

    bundle = build_spatial_bundle(rec)

    assert bundle["timeline"] == []
    assert any("时间轴为空" in b for b in bundle["blockers"])


# ---------------------------------------------------------------------------
# 5. 录音缺失
# ---------------------------------------------------------------------------
def test_missing_audio_kept_with_blocker(tmp_path):
    missing = str(tmp_path / "no_such_recording.wav")
    rec = _record()

    bundle = build_spatial_bundle(
        rec, audio=[{"path": missing, "label": "FM 录音",
                     "utc": "2026-01-01T00:00:02+00:00"}])

    assert len(bundle["audio"]) == 1
    assert bundle["audio"][0]["path"] == missing
    assert bundle["audio"][0]["label"] == "FM 录音"
    assert f"录音文件不存在：{missing}" in bundle["blockers"]


# ---------------------------------------------------------------------------
# 6. 星点截断
# ---------------------------------------------------------------------------
def test_stars_truncated_by_magnitude():
    rec = _record()
    stars = [
        {"name": "C", "ra_deg": 30.0, "dec_deg": 3.0, "mag": 4.0},
        {"name": "A", "ra_deg": 10.0, "dec_deg": 1.0, "mag": 1.0},
        {"name": "B", "ra_deg": 20.0, "dec_deg": 2.0, "mag": 2.0},
    ]

    bundle = build_spatial_bundle(rec, stars=stars, max_stars=2)

    sky = bundle["sky"]
    assert len(sky["stars"]) == 2
    assert sky["truncated"] is True
    assert sky["total_stars"] == 3
    assert [s["name"] for s in sky["stars"]] == ["A", "B"]
    assert sky["stars"][0]["ra_deg"] == 10.0
    assert sky["stars"][0]["dec_deg"] == 1.0


# ---------------------------------------------------------------------------
# 7. 确定性（created_utc 除外）
# ---------------------------------------------------------------------------
def test_repeat_build_is_byte_stable_except_created():
    rec = _record()
    kwargs = dict(
        stars=[{"name": "A", "ra_deg": 10.0, "dec_deg": 1.0, "mag": 1.0}],
        passes=[{"name": "ISS", "peak_utc": "2026-01-01T00:10:00+00:00"}],
        spectrum={"frames": [{"at_s": 0.5, "db": [-10.0, -20.0]}],
                  "center_freq_hz": 100.1e6, "sample_rate_hz": 2.4e6,
                  "duration_s": 3.5},
        audio=[],
    )

    first = build_spatial_bundle(rec, **kwargs)
    second = build_spatial_bundle(rec, **kwargs)

    assert first["created_utc"] != ""
    first.pop("created_utc")
    second.pop("created_utc")
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == \
        json.dumps(second, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# 8. CLI：真实 SigMF 文件的频谱帧
# ---------------------------------------------------------------------------
def test_cli_exports_real_spectrum_frames(tmp_path):
    meta_path, _ = _write_sigmf(tmp_path)
    rec = _record(products=[product_from_sigmf(str(meta_path))])
    archive = tmp_path / "obs.observation.json"
    archive.write_text(rec.to_json(), encoding="utf-8")
    outdir = tmp_path / "bundle_out"

    rc = _load_cli().main([str(archive), "-o", str(outdir),
                           "--frames", "4", "--bins", "16"])

    assert rc == 0
    bundle_path = outdir / "bundle.json"
    assert bundle_path.exists()
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["schema"] == SCHEMA
    frames = bundle["spectrum"]["frames"]
    assert len(frames) == 4
    for frame in frames:
        assert len(frame["db"]) == 16
        assert all(isinstance(v, float) for v in frame["db"])
    assert [f["at_s"] for f in frames] == sorted(f["at_s"] for f in frames)
    assert bundle["spectrum"]["center_freq_hz"] == 100.1e6
    assert bundle["blockers"] == []


def test_cli_exports_frames_for_desktop_sidecar_recording(tmp_path):
    """桌面 UI 录音（``.iq`` + ``.iq.json``，非 SigMF）也要能出头显频谱帧。

    真机 2026-11-22：UI 录制档案的 products 里已写明 sample_rate_hz /
    format=cf32 / samples，但 IQPlayback 只认 SigMF，导出得到
    ``blockers=1 frames=0``。档案里已有的事实必须被用上，不能让
    「录制 → 头显」断在容器格式差异上，也不能为此猜一个采样率。
    """
    n = 32768
    rate = 1.536e6
    freq = 98.0e6
    data_path = tmp_path / "baseband_x.iq"
    t = np.arange(n, dtype=np.float64)
    signal = (0.5 * np.exp(2j * np.pi * 0.25 * t)
              + 0.1 * np.exp(2j * np.pi * -0.1 * t)).astype(np.complex64)
    signal.tofile(str(data_path))

    rec = _record(
        tuning={"center_freq_hz": freq, "sample_rate_hz": rate,
                "gain_db": 7.1},
        products=[{
            "path": str(data_path) + ".json",
            "data_path": str(data_path),
            "format": "cf32",
            "samples": n,
            "sample_rate_hz": rate,
            "center_freq_hz": freq,
        }])
    archive = tmp_path / "desk.observation.json"
    archive.write_text(rec.to_json(), encoding="utf-8")
    outdir = tmp_path / "bundle_desk"

    rc = _load_cli().main([str(archive), "-o", str(outdir),
                           "--frames", "4", "--bins", "16"])

    assert rc == 0
    bundle = json.loads((outdir / "bundle.json").read_text(encoding="utf-8"))
    assert bundle["blockers"] == []
    assert len(bundle["spectrum"]["frames"]) == 4
    assert bundle["spectrum"]["sample_rate_hz"] == rate
    assert bundle["spectrum"]["center_freq_hz"] == freq


# ---------------------------------------------------------------------------
# 9. CLI：默认附随仓 HYG 星表
# ---------------------------------------------------------------------------
def test_cli_default_attaches_hyg_stars(tmp_path):
    meta_path, _ = _write_sigmf(tmp_path)
    rec = _record(products=[product_from_sigmf(str(meta_path))])
    archive = tmp_path / "obs.observation.json"
    archive.write_text(rec.to_json(), encoding="utf-8")
    outdir = tmp_path / "bundle_hyg"

    # 星表加载慢，只取 50 颗（走默认路径，不是 --stars-json）。
    rc = _load_cli().main([str(archive), "-o", str(outdir),
                           "--no-spectrum", "--stars-limit", "50"])

    assert rc == 0
    bundle = json.loads((outdir / "bundle.json").read_text(encoding="utf-8"))
    sky = bundle["sky"]
    assert sky["source"] == "hyg"
    assert len(sky["stars"]) == 50
    for star in sky["stars"]:
        assert isinstance(star["ra_deg"], float)
        assert isinstance(star["dec_deg"], float)
        assert isinstance(star["mag"], float)
    assert sky["total_stars"] > len(sky["stars"])
    assert sky["truncated"] is True


# ---------------------------------------------------------------------------
# 10. CLI：--no-stars 不附星
# ---------------------------------------------------------------------------
def test_cli_no_stars_yields_empty_sky(tmp_path):
    meta_path, _ = _write_sigmf(tmp_path)
    rec = _record(products=[product_from_sigmf(str(meta_path))])
    archive = tmp_path / "obs.observation.json"
    archive.write_text(rec.to_json(), encoding="utf-8")
    outdir = tmp_path / "bundle_nostars"

    rc = _load_cli().main([str(archive), "-o", str(outdir),
                           "--no-spectrum", "--no-stars"])

    assert rc == 0
    bundle = json.loads((outdir / "bundle.json").read_text(encoding="utf-8"))
    assert bundle["sky"]["stars"] == []
    assert bundle["sky"]["source"] == "none"
    assert bundle["sky"]["total_stars"] == 0
    assert bundle["sky"]["truncated"] is False


# ---------------------------------------------------------------------------
# 11-14. 星点地平坐标（几何 alt/az）
# ---------------------------------------------------------------------------
_LAT = 43.79
_LON = 87.6
_WHEN = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def _jd_when() -> float:
    return unix_to_jd(_WHEN.timestamp())


def _lst_deg_when() -> float:
    return math.degrees(jd_to_lst(_jd_when(), _LON)) % 360.0


def _record_at_site(**overrides):
    return _record(started_utc=_WHEN.isoformat(),
                   site={"source": "gnss", "lat_deg": _LAT, "lon_deg": _LON,
                         "alt_m": 0.0},
                   **overrides)


def test_altaz_zenith_invariant():
    rec = _record_at_site()
    star = {"name": "Z", "ra_deg": _lst_deg_when(), "dec_deg": _LAT,
            "mag": 1.0}

    bundle = build_spatial_bundle(rec, stars=[star])

    sky = bundle["sky"]
    assert sky["altaz_source"] == "geometric"
    assert sky["altaz_refraction"] is False
    out = sky["stars"][0]
    assert math.isclose(out["alt_deg"], 90.0, abs_tol=1e-6)
    assert 0.0 <= out["az_deg"] < 360.0
    assert out["visible"] is True


def test_altaz_horizon_invariant():
    rec = _record_at_site()
    star = {"name": "H", "ra_deg": _lst_deg_when(), "dec_deg": _LAT - 90.0,
            "mag": 2.0}

    bundle = build_spatial_bundle(rec, stars=[star])

    out = bundle["sky"]["stars"][0]
    assert math.isclose(out["alt_deg"], 0.0, abs_tol=1e-6)
    assert 0.0 <= out["az_deg"] < 360.0


def test_altaz_polaris_sits_near_latitude():
    # 北极星始终绕天极转：任意时角高度都在 43.05..44.53 之间；
    # 取 H=+90°（上/下中天之间）时高度 ≈ 纬度。
    rec = _record_at_site()
    star = {"name": "Polaris", "ra_deg": _lst_deg_when() - 90.0,
            "dec_deg": 89.26, "mag": 2.0}

    bundle = build_spatial_bundle(rec, stars=[star])

    out = bundle["sky"]["stars"][0]
    assert 43.2 <= out["alt_deg"] <= 44.4
    assert out["visible"] is True


# ---------------------------------------------------------------------------
# 14b. 自行接入：真实亮星 / 缺 hyg_id / 缺侧车
# ---------------------------------------------------------------------------
SIRIUS_HYG_ID = 32263


def test_altaz_applies_proper_motion_to_sirius():
    from mbdsdr_ai.skyengine import star_catalog as sc
    from mbdsdr_ai.skyengine.proper_motion import (
        load_pm_table, pm_for_hyg_ids, propagate_radec_many)

    sirius = next(s for s in sc.brightest_stars(2000)
                  if s["hyg_id"] == SIRIUS_HYG_ID)
    bundle = build_spatial_bundle(_record_at_site(), stars=[sirius])

    sky = bundle["sky"]
    out = sky["stars"][0]
    assert sky["altaz_pm_applied"] >= 1
    assert sky["altaz_pm_missing"] == 0
    # 条目里的赤经/赤纬仍是 J2000，没有被烘焙成当日值。
    assert out["ra_deg"] == sirius["ra_deg"]
    assert out["dec_deg"] == sirius["dec_deg"]

    table = load_pm_table()
    assert table is not None
    pmra, pmdec = pm_for_hyg_ids(np.array([SIRIUS_HYG_ID]), table)
    ra_pm, dec_pm = propagate_radec_many(
        sirius["ra_deg"], sirius["dec_deg"],
        float(pmra[0]), float(pmdec[0]), _jd_when())
    exp_alt, exp_az = _altaz_geometric(ra_pm, dec_pm, _LAT, _LON, _jd_when())
    assert math.isclose(out["alt_deg"], exp_alt, abs_tol=1e-6)
    assert math.isclose(out["az_deg"], exp_az, abs_tol=1e-6)

    static_alt, static_az = _altaz_geometric(
        sirius["ra_deg"], sirius["dec_deg"], _LAT, _LON, _jd_when())
    assert abs(out["alt_deg"] - static_alt) > 0.001
    assert abs(out["az_deg"] - static_az) > 0.001


def test_star_without_hyg_id_stays_static_and_counts_missing():
    star = {"name": "A", "ra_deg": 10.0, "dec_deg": 20.0, "mag": 1.0}

    sky = build_spatial_bundle(_record_at_site(), stars=[star])["sky"]

    assert sky["altaz_pm_applied"] == 0
    assert sky["altaz_pm_missing"] == 1
    out = sky["stars"][0]
    assert out["hyg_id"] is None
    exp_alt, exp_az = _altaz_geometric(10.0, 20.0, _LAT, _LON, _jd_when())
    assert out["alt_deg"] == exp_alt
    assert out["az_deg"] == exp_az


def test_missing_pm_table_keeps_all_stars_static(monkeypatch):
    import mbdsdr_ai.spatial_bundle as sb

    monkeypatch.setattr(sb, "load_pm_table", lambda *args, **kwargs: None)
    stars = [
        {"name": "Sirius", "ra_deg": 101.287, "dec_deg": -16.716,
         "mag": -1.46, "hyg_id": SIRIUS_HYG_ID},
        {"name": "A", "ra_deg": 10.0, "dec_deg": 20.0, "mag": 2.0},
    ]

    sky = build_spatial_bundle(_record_at_site(), stars=stars)["sky"]

    assert sky["altaz_pm_applied"] == 0
    assert sky["altaz_pm_missing"] == 2
    by_name = {s["name"]: s for s in sky["stars"]}
    for star in stars:
        out = by_name[star["name"]]
        exp_alt, exp_az = _altaz_geometric(
            star["ra_deg"], star["dec_deg"], _LAT, _LON, _jd_when())
        assert out["alt_deg"] == exp_alt
        assert out["az_deg"] == exp_az


def _angular_sep_deg(alt1, az1, alt2, az2) -> float:
    """两点地平坐标的球面角距（度）；用余弦式，天顶附近仍稳定。"""
    a1, z1, a2, z2 = map(math.radians, (alt1, az1, alt2, az2))
    cos_sep = (math.sin(a1) * math.sin(a2)
               + math.cos(a1) * math.cos(a2) * math.cos(z1 - z2))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))


def test_bundle_stars_recomputable_from_package_alone_within_0_1_deg():
    """S4 验收：只用**包内**星表 + 站址 + 时刻独立复算，角距 ≤ 0.1°。

    复算走 ``astronomy.equatorial_to_altaz``（球面三角 + acos 路径），与
    ``spatial_bundle._altaz_geometric`` 是两条独立实现；包内 alt/az 还含
    自行传播，二者差应在 0.1° 预算内（亮星 26 年自行 < 0.02°）。
    """
    from mbdsdr_ai.astronomy import (
        EquatorialCoord,
        Observer,
        equatorial_to_altaz,
    )
    from mbdsdr_ai.skyengine import star_catalog as sc

    stars = sc.brightest_stars(50)
    bundle = build_spatial_bundle(_record_at_site(), stars=stars)
    sky = bundle["sky"]

    assert sky["altaz_source"] == "geometric"
    assert sky["altaz_refraction"] is False
    assert len(sky["stars"]) >= 3

    # 只用包里写下的值复算：不碰原始星表、不碰夹具常量。
    site = bundle["site"]
    utc = bundle["time"]["utc"]
    assert site["source"] == "gnss"
    assert utc
    jd = unix_to_jd(datetime.fromisoformat(utc).timestamp())
    observer = Observer(longitude_deg=site["lon_deg"],
                        latitude_deg=site["lat_deg"],
                        height_m=site["alt_m"] or 0.0)

    checked = 0
    for star in sky["stars"]:
        alt, az = star.get("alt_deg"), star.get("az_deg")
        if alt is None or az is None:
            continue
        ref = equatorial_to_altaz(
            EquatorialCoord(ra_deg=star["ra_deg"], dec_deg=star["dec_deg"]),
            observer, jd)
        sep = _angular_sep_deg(alt, az, ref.alt_deg, ref.az_deg)
        assert sep <= 0.1, f"{star['name']!r} 角距 {sep:.4f}° 超出 0.1°"
        checked += 1
    assert checked >= 3, f"只有 {checked} 颗星带地平坐标，不足以验收"


def _write_sigmf_fm(tmp_path, n=9600, rate=48000.0, freq=100.1e6,
                    tone_hz=1000.0, dev_hz=10000.0, name="fixture_fm"):
    """造一份真实 FM 调制的 SigMF（合成 IQ 必须真过解调链才出 WAV）。"""
    data_path = tmp_path / f"{name}.sigmf-data"
    meta_path = tmp_path / f"{name}.sigmf-meta"
    t = np.arange(n, dtype=np.float64) / rate
    phase = (dev_hz / tone_hz) * np.sin(2.0 * np.pi * tone_hz * t)
    x = np.exp(1j * phase).astype(np.complex64)
    x.tofile(str(data_path))
    meta = {
        "global": {
            "core:datatype": "cf32_le",
            "core:version": "1.0.0",
            "core:sample_rate": rate,
            "core:frequency": freq,
            "core:dataset": data_path.name,
            "core:total_num_samples": n,
            "mbdsdr:complete": True,
        },
        "captures": [{
            "core:sample_offset": 0,
            "core:datetime": "2026-01-01T00:00:00Z",
            "mbdsdr:time_source": "none",
        }],
        "annotations": [],
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return meta_path, data_path, rate, n


def _fm_record(meta_path, rate, n):
    return _record(
        products=[product_from_sigmf(str(meta_path))],
        tuning={"center_freq_hz": 100.1e6, "sample_rate_hz": rate,
                "gain_db": 30.0},
        timeline=[
            {"utc": "2026-01-01T00:00:00+00:00", "kind": "record_start",
             "detail": {"sample_rate_hz": rate}, "samples": 0},
            {"utc": "2026-01-01T00:00:00.100000+00:00",
             "kind": "record_stop", "detail": {}, "samples": n},
        ])


# ---------------------------------------------------------------------------
# 15. CLI --audio-out：真实解调出 WAV 并挂进 bundle
# ---------------------------------------------------------------------------
def test_cli_audio_out_writes_real_demodulated_wav(tmp_path):
    meta_path, _, rate, n = _write_sigmf_fm(tmp_path)
    rec = _fm_record(meta_path, rate, n)
    archive = tmp_path / "obs_fm.observation.json"
    archive.write_text(rec.to_json(), encoding="utf-8")
    outdir = tmp_path / "bundle_audio"
    wav_path = outdir / "audio.wav"

    rc = _load_cli().main([str(archive), "-o", str(outdir), "--no-stars",
                           "--frames", "2", "--bins", "4",
                           "--audio-out", str(wav_path), "--demod", "wfm"])

    assert rc == 0
    assert wav_path.exists()
    assert wav_path.read_bytes()[:4] == b"RIFF"
    assert wav_path.read_bytes()[8:12] == b"WAVE"
    with wave.open(str(wav_path), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 48000
        n_frames = wf.getnframes()
        pcm = np.frombuffer(wf.readframes(n_frames), dtype=np.int16)
    assert n_frames > 0
    assert pcm.size == n_frames
    assert np.any(pcm != 0)

    bundle = json.loads((outdir / "bundle.json").read_text(encoding="utf-8"))
    assert len(bundle["audio"]) == 1
    audio = bundle["audio"][0]
    assert audio["path"] == str(wav_path)
    assert audio["label"].startswith("demod:")
    assert audio["label"] == "demod:wfm"
    assert audio["utc"] == rec.started_utc
    assert not any("录音文件不存在" in b for b in bundle["blockers"])


# ---------------------------------------------------------------------------
# 16. --demod nfm：走窄带链（demodulate + audio_to_playback）同样产出真音频
# ---------------------------------------------------------------------------
def test_cli_audio_out_nfm_uses_narrowband_chain(tmp_path):
    meta_path, _, rate, n = _write_sigmf_fm(tmp_path)
    rec = _fm_record(meta_path, rate, n)
    archive = tmp_path / "obs_fm.observation.json"
    archive.write_text(rec.to_json(), encoding="utf-8")
    outdir = tmp_path / "bundle_nfm"
    wav_path = outdir / "audio_nfm.wav"

    rc = _load_cli().main([str(archive), "-o", str(outdir), "--no-stars",
                           "--frames", "2", "--bins", "4",
                           "--audio-out", str(wav_path), "--demod", "nfm"])

    assert rc == 0
    with wave.open(str(wav_path), "rb") as wf:
        assert wf.getframerate() == 48000
        assert wf.getnchannels() == 1
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    assert pcm.size > 0
    assert np.any(pcm != 0)
    bundle = json.loads((outdir / "bundle.json").read_text(encoding="utf-8"))
    assert bundle["audio"][0]["label"] == "demod:nfm"


# ---------------------------------------------------------------------------
# 17. 解调模式非法 → 退出 2 且说明原因
# ---------------------------------------------------------------------------
def test_cli_unsupported_demod_mode_exits_2(tmp_path, capsys):
    meta_path, _, rate, n = _write_sigmf_fm(tmp_path)
    rec = _fm_record(meta_path, rate, n)
    archive = tmp_path / "obs_fm.observation.json"
    archive.write_text(rec.to_json(), encoding="utf-8")
    outdir = tmp_path / "bundle_bad_mode"

    rc = _load_cli().main([str(archive), "-o", str(outdir), "--no-stars",
                           "--audio-out", str(outdir / "audio.wav"),
                           "--demod", "qpsk"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "qpsk" in err
    assert "不支持" in err
    assert not (outdir / "bundle.json").exists()
    assert not (outdir / "audio.wav").exists()


# ---------------------------------------------------------------------------
# 18. 没有数据产物 → audio 为空 + 真实原因的 blocker，bundle 仍写出
# ---------------------------------------------------------------------------
def test_cli_audio_out_without_data_marks_blocker(tmp_path):
    rec = _record(products=[], timeline=[
        {"utc": "2026-01-01T00:00:00+00:00", "kind": "record_start",
         "detail": {}, "samples": 0},
    ])
    archive = tmp_path / "obs_nodata.observation.json"
    archive.write_text(rec.to_json(), encoding="utf-8")
    outdir = tmp_path / "bundle_nodata"

    rc = _load_cli().main([str(archive), "-o", str(outdir), "--no-stars",
                           "--audio-out", str(outdir / "audio.wav")])

    assert rc == 0
    bundle = json.loads((outdir / "bundle.json").read_text(encoding="utf-8"))
    assert bundle["audio"] == []
    assert any("解调失败" in b for b in bundle["blockers"])
    assert not (outdir / "audio.wav").exists()


# ---------------------------------------------------------------------------
# 19. 数据文件缺失（登记了产物但文件不在）→ audio 为空 + blocker
# ---------------------------------------------------------------------------
def test_cli_audio_out_with_missing_data_file_marks_blocker(tmp_path):
    data_path = tmp_path / "gone.sigmf-data"
    meta_path = tmp_path / "gone.sigmf-meta"
    meta_path.write_text(json.dumps({
        "global": {
            "core:datatype": "cf32_le",
            "core:version": "1.0.0",
            "core:sample_rate": 48000.0,
            "core:frequency": 100.1e6,
            "core:dataset": data_path.name,
            "core:total_num_samples": 9600,
            "mbdsdr:complete": True,
        },
        "captures": [{"core:datetime": "2026-01-01T00:00:00Z"}],
        "annotations": [],
    }), encoding="utf-8")
    rec = _fm_record(meta_path, 48000.0, 9600)
    archive = tmp_path / "obs_missing.observation.json"
    archive.write_text(rec.to_json(), encoding="utf-8")
    outdir = tmp_path / "bundle_missing"

    rc = _load_cli().main([str(archive), "-o", str(outdir), "--no-stars",
                           "--audio-out", str(outdir / "audio.wav")])

    assert rc == 0
    bundle = json.loads((outdir / "bundle.json").read_text(encoding="utf-8"))
    assert bundle["audio"] == []
    assert "解调失败：没有可回放的 IQ 数据文件" in bundle["blockers"]
    assert not (outdir / "audio.wav").exists()


def test_altaz_missing_time_keeps_stars_bare():
    rec = _record(started_utc=None,
                  timeline=[{"kind": "record_start", "samples": 0}],
                  site={"source": "gnss", "lat_deg": _LAT, "lon_deg": _LON,
                        "alt_m": 0.0})
    star = {"name": "A", "ra_deg": 10.0, "dec_deg": 20.0, "mag": 1.0}

    bundle = build_spatial_bundle(rec, stars=[star])

    out = bundle["sky"]["stars"][0]
    assert "alt_deg" not in out
    assert "az_deg" not in out
    assert "visible" not in out
    assert bundle["sky"]["altaz_source"] == "unavailable"
    assert bundle["sky"]["altaz_pm_applied"] == 0
    assert bundle["sky"]["altaz_pm_missing"] == 0
    assert any("没有观测时刻" in b for b in bundle["blockers"])


# ---------------------------------------------------------------------------
# 20-21. sky.passes：排序 / 缺失字段保持 None
# ---------------------------------------------------------------------------
def test_passes_sorted_by_start_utc():
    rec = _record()
    passes = [
        {"norad_id": 40069, "name": "METEOR M2",
         "start_utc": "2026-01-01T03:00:00Z",
         "end_utc": "2026-01-01T03:10:00Z",
         "max_alt_deg": 25.0, "duration_s": 600},
        {"norad_id": 25544, "name": "ISS",
         "start_utc": "2026-01-01T01:00:00Z",
         "end_utc": "2026-01-01T01:07:00Z",
         "max_alt_deg": 62.3, "duration_s": 420},
    ]

    bundle = build_spatial_bundle(rec, passes=passes)

    got = bundle["sky"]["passes"]
    assert [p["start_utc"] for p in got] == [
        "2026-01-01T01:00:00Z", "2026-01-01T03:00:00Z"]
    assert [p["name"] for p in got] == ["ISS", "METEOR M2"]
    assert not any("过境" in b for b in bundle["blockers"])


def test_pass_missing_field_stays_none():
    rec = _record()

    bundle = build_spatial_bundle(rec, passes=[{
        "norad_id": 25544, "name": "ISS",
        "start_utc": "2026-01-01T01:00:00Z",
        "end_utc": "2026-01-01T01:07:00Z",
        "duration_s": 420,
    }])

    p = bundle["sky"]["passes"][0]
    assert "max_alt_deg" in p
    assert p["max_alt_deg"] is None
    assert p["norad_id"] == 25544
    assert p["duration_s"] == 420.0


# ---------------------------------------------------------------------------
# 22-24. CLI：--passes-json / --passes-hours
# ---------------------------------------------------------------------------
def _archive(tmp_path, name="obs.observation.json"):
    rec = _record(products=[])
    archive = tmp_path / name
    archive.write_text(rec.to_json(), encoding="utf-8")
    return archive


def test_cli_passes_json_exports_two(tmp_path, capsys):
    archive = _archive(tmp_path)
    payload = {"passes": [
        {"norad_id": 25544, "name": "ISS",
         "start_utc": "2026-01-01T01:00:00Z",
         "end_utc": "2026-01-01T01:07:00Z",
         "max_alt_deg": 62.3, "duration_s": 420},
        {"norad_id": 40069, "name": "METEOR M2",
         "start_utc": "2026-01-01T03:00:00Z",
         "end_utc": "2026-01-01T03:10:00Z",
         "max_alt_deg": 25.0, "duration_s": 600},
    ]}
    passes_path = tmp_path / "passes.json"
    passes_path.write_text(json.dumps(payload), encoding="utf-8")
    outdir = tmp_path / "bundle_passes"

    rc = _load_cli().main([str(archive), "-o", str(outdir),
                           "--no-spectrum", "--no-stars",
                           "--passes-json", str(passes_path)])

    assert rc == 0
    assert "passes=2" in capsys.readouterr().out
    bundle = json.loads((outdir / "bundle.json").read_text(encoding="utf-8"))
    expected = [dict(p, tle_age_days=None, suspect=False,
                     suspect_reasons=[]) for p in payload["passes"]]
    assert bundle["sky"]["passes"] == expected


def test_cli_bad_passes_json_exits_2(tmp_path, capsys):
    archive = _archive(tmp_path)
    outdir = tmp_path / "bundle_bad_passes"

    for i, (text, needle) in enumerate((
            ("{not json", "读取过境数据失败"),
            ('{"no_passes": []}', "passes"))):
        bad = tmp_path / f"bad_passes_{i}.json"
        bad.write_text(text, encoding="utf-8")
        rc = _load_cli().main([str(archive), "-o", str(outdir),
                               "--no-spectrum", "--no-stars",
                               "--passes-json", str(bad)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "读取过境数据失败" in err
        assert needle in err
        assert not (outdir / "bundle.json").exists()


def test_cli_passes_hours_without_tle_cache_marks_blocker(
        tmp_path, monkeypatch, capsys):
    empty_cache = tmp_path / "empty_tle_cache"
    empty_cache.mkdir()
    monkeypatch.setenv("MBDSDR_TLE_CACHE_DIR", str(empty_cache))

    archive = _archive(tmp_path)
    outdir = tmp_path / "bundle_no_tle"

    rc = _load_cli().main([str(archive), "-o", str(outdir),
                           "--no-spectrum", "--no-stars",
                           "--passes-hours", "6"])

    assert rc == 0
    assert "passes=0" in capsys.readouterr().out
    bundle = json.loads((outdir / "bundle.json").read_text(encoding="utf-8"))
    assert bundle["sky"]["passes"] == []
    assert any("没有 TLE 缓存" in b or "没有站址" in b
               for b in bundle["blockers"])


# ---------------------------------------------------------------------------
# 25-30. sky.passes 合理性护栏（S4.2c）：只打标，不删条目
# ---------------------------------------------------------------------------
def _pass_entry(**overrides):
    base = dict(
        norad_id=25544,
        name="ISS",
        start_utc="2026-01-01T01:00:00Z",
        end_utc="2026-01-01T01:10:00Z",
        max_alt_deg=40.0,
        duration_s=600.0,
    )
    base.update(overrides)
    return base


def test_long_duration_pass_marked_suspect():
    # 真机实测值：20235.2 s（5.62 h）对近地目标不合理。
    bundle = build_spatial_bundle(_record(), passes=[_pass_entry(
        norad_id=44016, name="ATLAS 5 CENTAUR DEB",
        start_utc="2026-11-20T16:42:16Z",
        end_utc="2026-11-20T22:19:32Z",
        max_alt_deg=37.2, duration_s=20235.2)])

    p = bundle["sky"]["passes"][0]
    assert p["suspect"] is True
    assert any("历时异常" in r for r in p["suspect_reasons"])
    assert p["duration_s"] == 20235.2


def test_stale_tle_pass_marked_suspect():
    bundle = build_spatial_bundle(_record(), passes=[_pass_entry(
        tle_age_days=45)])

    p = bundle["sky"]["passes"][0]
    assert p["tle_age_days"] == 45.0
    assert p["suspect"] is True
    assert any("TLE 过旧（45.0 天）" in r for r in p["suspect_reasons"])


def test_normal_pass_not_suspect():
    bundle = build_spatial_bundle(_record(), passes=[_pass_entry(
        tle_age_days=2)])

    p = bundle["sky"]["passes"][0]
    assert p["suspect"] is False
    assert p["suspect_reasons"] == []


def test_pass_missing_fields_flagged():
    bundle = build_spatial_bundle(_record(), passes=[
        _pass_entry(norad_id=40069, name="METEOR M2",
                    start_utc="2026-01-01T02:00:00Z",
                    duration_s=0, max_alt_deg=None),
        _pass_entry(norad_id=25544, name="ISS", start_utc=None,
                    end_utc=None),
    ])

    first = bundle["sky"]["passes"][0]
    assert first["suspect"] is True
    assert "缺少最大仰角" in first["suspect_reasons"]
    assert "历时非正" in first["suspect_reasons"]

    last = bundle["sky"]["passes"][1]
    assert last["suspect"] is True
    assert "缺少起止时刻" in last["suspect_reasons"]


def test_suspect_passes_keep_count_and_order():
    bundle = build_spatial_bundle(_record(), passes=[
        _pass_entry(norad_id=1, name="C",
                    start_utc="2026-01-01T03:00:00Z", duration_s=600),
        _pass_entry(norad_id=2, name="A",
                    start_utc="2026-01-01T01:00:00Z", duration_s=600),
        _pass_entry(norad_id=3, name="B", start_utc="2026-01-01T02:00:00Z",
                    duration_s=20235.2, max_alt_deg=37.2,
                    tle_age_days=45),
    ])

    got = bundle["sky"]["passes"]
    assert len(got) == 3
    assert [p["start_utc"] for p in got] == [
        "2026-01-01T01:00:00Z", "2026-01-01T02:00:00Z",
        "2026-01-01T03:00:00Z"]
    assert [p["suspect"] for p in got] == [False, True, False]
    assert [p["norad_id"] for p in got] == [2, 3, 1]


def test_cli_passes_json_keeps_tle_age_days(tmp_path, capsys):
    archive = _archive(tmp_path)
    payload = {"passes": [_pass_entry(tle_age_days=45)]}
    passes_path = tmp_path / "passes_age.json"
    passes_path.write_text(json.dumps(payload), encoding="utf-8")
    outdir = tmp_path / "bundle_passes_age"

    rc = _load_cli().main([str(archive), "-o", str(outdir),
                           "--no-spectrum", "--no-stars",
                           "--passes-json", str(passes_path)])

    assert rc == 0
    assert "passes=1" in capsys.readouterr().out
    bundle = json.loads((outdir / "bundle.json").read_text(encoding="utf-8"))
    p = bundle["sky"]["passes"][0]
    assert p["tle_age_days"] == 45.0
    assert p["suspect"] is True
    assert any("TLE 过旧" in r for r in p["suspect_reasons"])
