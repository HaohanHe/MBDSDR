"""tests/test_decode_pipeline_e2e.py — 解码流水线端到端可证（P5 续）。

要证的不是「能跑」，而是每一级输入/输出都可量、内容逐字节对得上、失败有
明确原因、耗时能换算成实时率。

信号链（三条都从**随仓的 .cf32 fixture** 起读，不引用仓外绝对路径）：

    raw IQ (.cf32) → load_iq → downconvert（混频+多相抽取 或 FM 鉴频）
        → demod（GMSK 差分/位定时、PPM 位判决 或 Bell202 AFSK+NRZI）
        → sync（AIS HDLC 标志+FCS、ADS-B 8µs 前导 或 AX.25 0x7E 标志）
        → frame（payload/字节+CRC）→ content（字段/NMEA/AX.25 地址+payload）

事实来源：
  * AIS 链路层 ITU-R M.1371（9600bd GMSK、HDLC 标志、FCS-16、NRZI）；
  * ADS-B 1090ES ICAO Annex 10 Vol IV / RTCA DO-260（8µs 前导、PPM、
    CRC-24 0xFFF409）；
  * APRS/AX.25 TAPR AX.25 + Bell 202 AFSK 1200bd（mark 1200Hz、space 2200Hz、
    NRZI、HDLC 0x7E、位填充、CRC-16/X.25）。

fixture 的诚实声明：`tests/fixtures/decode_pipeline/manifest.json` 里写明
「已知帧经仓内编码器重新调制，非 OTA 实录」；它证明的是**流水线各级与已知
真值逐字节一致**，不证明空口接收性能（空口结论见 P2/P4 的真实录证据）。
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "decode_pipeline"
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"


def _manifest():
    assert MANIFEST_PATH.exists(), (
        "缺少随仓 fixture manifest；运行 python scripts/make_decode_fixtures.py")
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pipe():
    import mbdsdr_ai.decode_pipeline as m
    return m


def _assert_stage_shape(stages, names):
    assert [s["name"] for s in stages] == names, (
        f"stage 顺序/缺失: {[s['name'] for s in stages]} != {names}")
    for s in stages:
        for key in ("input_samples", "output_samples", "dropped_frames",
                    "elapsed_ms", "unit_in", "unit_out"):
            assert key in s, f"stage {s.get('name')} 缺字段 {key}"
        assert s["input_samples"] >= 0
        assert s["output_samples"] >= 0
        assert s["dropped_frames"] >= 0
        assert s["elapsed_ms"] >= 0.0


# ---------------------------------------------------------------------------
# 1) 每级输入/输出可观测 + 随仓 fixture
# ---------------------------------------------------------------------------
def test_fixtures_are_in_repo_and_manifest_is_honest():
    m = _manifest()
    for key in ("ais", "adsb", "aprs"):
        p = FIXTURE_DIR / m[key]["file"]
        assert p.exists() and p.stat().st_size > 0
        assert str(p).startswith(str(FIXTURE_DIR))
    for key, meta in m["aprs_failures"].items():
        p = FIXTURE_DIR / meta["file"]
        assert p.exists() and p.stat().st_size > 0
        assert meta["expected_reason"]
    text = MANIFEST_PATH.read_text(encoding="utf-8").lower()
    assert "synthetic" in text and "not ota" in text


def test_ais_pipeline_reports_every_stage(pipe):
    m = _manifest()["ais"]
    r = pipe.run_ais_pipeline(
        str(FIXTURE_DIR / m["file"]), fs=float(m["fs_hz"]),
        target_fs=float(m["target_fs_hz"]))
    assert r["ok"] is True
    _assert_stage_shape(r["stages"],
                        ["load_iq", "downconvert", "demod", "sync",
                         "frame", "content"])
    dc = r["stages"][1]
    assert dc["note"].startswith("applied=True"), "fixture 应在高采样率，下变频必须真做"
    assert dc["output_samples"] == round(
        dc["input_samples"] * m["target_fs_hz"] / m["fs_hz"])  # 抽取后样本数
    assert r["stages"][-1]["output_samples"] == m["frame_count"]


def test_adsb_pipeline_reports_every_stage(pipe):
    m = _manifest()["adsb"]
    r = pipe.run_adsb_pipeline(
        str(FIXTURE_DIR / m["file"]), fs=float(m["fs_hz"]),
        target_fs=float(m["target_fs_hz"]))
    assert r["ok"] is True
    _assert_stage_shape(r["stages"],
                        ["load_iq", "downconvert", "sync", "demod",
                         "frame", "content"])
    sync = r["stages"][2]
    assert sync["output_samples"] == m["frame_count"], \
        "sync 级输出应等于 fixture 里独立前导的个数"
    assert r["stages"][-1]["output_samples"] == m["frame_count"]


def test_aprs_pipeline_reports_every_stage(pipe):
    m = _manifest()["aprs"]
    r = pipe.run_aprs_pipeline(
        str(FIXTURE_DIR / m["file"]), fs=float(m["fs_hz"]),
        target_fs=float(m["target_fs_hz"]))
    assert r["ok"] is True
    _assert_stage_shape(r["stages"],
                        ["load_iq", "downconvert", "demod", "sync",
                         "frame", "content"])
    dc = r["stages"][1]
    assert "FM 鉴频" in dc["note"], "APRS 的 downconvert 必须是真鉴频+重采样"
    assert dc["unit_in"] == "complex_samples"
    assert dc["unit_out"] == "audio_samples"
    assert r["stages"][2]["unit_out"] == "bits"
    assert r["stages"][3]["unit_out"] == "frame_candidates"
    assert r["stages"][-1]["output_samples"] == m["frame_count"]


def test_aprs_content_matches_expected_bytes(pipe):
    """呼号、digipeater 路径、payload、整帧字节都必须逐字节对上。"""
    m = _manifest()["aprs"]
    r = pipe.run_aprs_pipeline(
        str(FIXTURE_DIR / m["file"]), fs=float(m["fs_hz"]),
        target_fs=float(m["target_fs_hz"]))
    assert r["content"]["payloads_hex"] == [f["info_hex"] for f in m["frames"]]
    got = r["content"]["frames"]
    assert len(got) == m["frame_count"]
    for g, e in zip(got, m["frames"]):
        assert g["frame_hex"] == e["frame_hex"], "整帧字节（含 FCS）必须一致"
        assert g["info_hex"] == e["info_hex"], "payload 必须逐字节一致"
        assert (g["source"], g["source_ssid"]) == (e["source"], e["source_ssid"])
        assert (g["destination"], g["dest_ssid"]) == \
            (e["destination"], e["dest_ssid"])
        assert g["digipeaters"] == e["digipeaters"], "digipeater 路径必须一致"
        assert g["control"] == e["control"] and g["pid"] == e["pid"]
        assert g["fcs_valid"] is True
    # 至少 1 帧真的带 digipeater 路径，另 1 帧为空路径（不是恒有/恒无）
    paths = [g["digipeaters"] for g in got]
    assert any(p for p in paths) and any(not p for p in paths), paths


# ---------------------------------------------------------------------------
# 2) 内容逐字节对拍（不是「解出来了」）
# ---------------------------------------------------------------------------
def test_ais_content_matches_expected_bytes(pipe):
    m = _manifest()["ais"]
    r = pipe.run_ais_pipeline(
        str(FIXTURE_DIR / m["file"]), fs=float(m["fs_hz"]),
        target_fs=float(m["target_fs_hz"]))
    assert r["content"]["payloads_hex"] == m["expected_payloads_hex"]
    fields = r["content"]["messages"]
    assert [x["mmsi"] for x in fields] == [e["mmsi"] for e in m["expected_fields"]]
    assert [x["type"] for x in fields] == [e["type"] for e in m["expected_fields"]]
    assert [x.get("callsign") for x in fields] == \
        [e.get("callsign") for e in m["expected_fields"]]


def test_adsb_content_matches_expected_bytes(pipe):
    m = _manifest()["adsb"]
    r = pipe.run_adsb_pipeline(
        str(FIXTURE_DIR / m["file"]), fs=float(m["fs_hz"]),
        target_fs=float(m["target_fs_hz"]))
    assert r["content"]["frames_hex"] == m["expected_frames_hex"]
    got = [(x["icao"], x.get("callsign")) for x in r["content"]["frames"]]
    want = [(e["icao"], e.get("callsign")) for e in m["expected_frames"]]
    assert got == want


# ---------------------------------------------------------------------------
# 3) 失败路径：四类输入都必须给明确原因，且绝不以空内容冒充成功
# ---------------------------------------------------------------------------
def _write_cf32(path, iq):
    arr = np.empty(iq.size * 2, dtype=np.float32)
    arr[0::2] = np.real(iq)
    arr[1::2] = np.imag(iq)
    arr.tofile(str(path))


@pytest.mark.parametrize("mode", ["ais", "adsb"])
@pytest.mark.parametrize("kind", ["noise", "zeros"])
def test_noise_and_zeros_never_report_success(pipe, tmp_path, mode, kind):
    m = _manifest()[mode]
    n = 40000 if mode == "ais" else 4000
    fs = float(m["fs_hz"])
    if kind == "zeros":
        iq = np.zeros(n, dtype=np.complex128)
    else:
        rng = np.random.default_rng(20261105)
        iq = (rng.standard_normal(n) +
              1j * rng.standard_normal(n)).astype(np.complex128)
    path = tmp_path / f"{mode}_{kind}.cf32"
    _write_cf32(path, iq)

    run = pipe.run_ais_pipeline if mode == "ais" else pipe.run_adsb_pipeline
    r = run(str(path), fs=fs, target_fs=float(m["target_fs_hz"]))
    assert r["ok"] is False
    assert r["content"] == {}, "失败时不允许带任何伪造内容"
    assert r["reason"] in {"preamble_not_found", "nothing_detected",
                           "no_frame_recovered"}, r["reason"]
    assert r["stage"] in {"sync", "frame"}
    assert r["detail"]


@pytest.mark.parametrize("mode", ["ais", "adsb"])
def test_sample_rate_mismatch_is_explicit(pipe, tmp_path, mode):
    m = _manifest()[mode]
    if mode == "ais":
        bad_fs = 44100.0                # 44100/9600 非整数
    else:
        bad_fs = 1_500_000.0            # 1090ES 要求 1MHz 整数倍
    iq = np.zeros(2048, dtype=np.complex128)
    path = tmp_path / f"{mode}_badrate.cf32"
    _write_cf32(path, iq)
    run = pipe.run_ais_pipeline if mode == "ais" else pipe.run_adsb_pipeline
    r = run(str(path), fs=bad_fs, target_fs=float(m["target_fs_hz"]))
    assert r["ok"] is False and r["content"] == {}
    assert r["reason"] == "sample_rate_mismatch"
    assert r["stage"] == "downconvert"
    assert str(bad_fs) in r["detail"] or f"{bad_fs:g}" in r["detail"]


@pytest.mark.parametrize("mode", ["ais", "adsb"])
def test_short_input_is_explicit(pipe, tmp_path, mode):
    m = _manifest()[mode]
    path = tmp_path / f"{mode}_short.cf32"
    _write_cf32(path, np.zeros(8, dtype=np.complex128))
    run = pipe.run_ais_pipeline if mode == "ais" else pipe.run_adsb_pipeline
    r = run(str(path), fs=float(m["fs_hz"]), target_fs=float(m["target_fs_hz"]))
    assert r["ok"] is False and r["content"] == {}
    assert r["reason"] == "insufficient_samples"
    assert r["stage"] in {"demod", "sync"}
    assert r["required_samples"] > 0


def test_empty_file_is_explicit(pipe, tmp_path):
    p = tmp_path / "empty.cf32"
    p.write_bytes(b"")
    m = _manifest()["ais"]
    r = pipe.run_ais_pipeline(str(p), fs=float(m["fs_hz"]),
                              target_fs=float(m["target_fs_hz"]))
    assert r["ok"] is False and r["content"] == {}
    assert r["reason"] == "iq_empty"
    assert r["stage"] == "load_iq"


@pytest.mark.parametrize("case,filename,reason,stage", [
    ("noise", "aprs_noise.cf32", "nothing_detected", "sync"),
    ("truncated", "aprs_truncated.cf32", "truncated_frame", "frame"),
    ("crc", "aprs_crc_fail.cf32", "crc_failed", "frame"),
])
def test_aprs_failure_paths_have_explicit_reason_codes(
        pipe, case, filename, reason, stage):
    """三条失败路径各给具体原因码；失败时 content 必须为空且 ok=False。"""
    m = _manifest()
    meta = m["aprs_failures"][filename]
    assert meta["expected_reason"] == reason, "manifest 与测试契约不一致"
    r = pipe.run_aprs_pipeline(
        str(FIXTURE_DIR / meta["file"]), fs=float(meta["fs_hz"]),
        target_fs=float(meta["target_fs_hz"]))
    assert r["ok"] is False
    assert r["status"] == "error"
    assert r["error_code"] == reason
    assert r["reason"] == reason
    assert r["stage"] == stage
    assert r["content"] == {}, f"{case}: 失败不允许带伪造 content"
    assert r["detail"], f"{case}: 必须有可读 detail"

    if case == "noise":
        assert r["flag_count"] == 0, "噪声 fixture 不应出现 HDLC 标志"
        assert r["content"] == {}
    elif case == "truncated":
        assert r["truncated_fragments"] >= 1 or r["tail_bits"] > 0
        assert r["dropped_frames"] == 0, "截断不是 CRC 失败，不能混计"
    else:
        assert r["dropped_frames"] >= 1
        frame_stage = [s for s in r["stages"] if s["name"] == "frame"][0]
        assert frame_stage["dropped_frames"] >= 1


def test_aprs_short_input_and_bad_rate_are_explicit(pipe, tmp_path):
    p = tmp_path / "aprs_short.cf32"
    _write_cf32(p, np.zeros(8, dtype=np.complex128))
    r = pipe.run_aprs_pipeline(str(p), fs=96000.0, target_fs=48000.0)
    assert r["ok"] is False and r["content"] == {}
    assert r["reason"] == "insufficient_samples"
    assert r["stage"] == "demod"

    r2 = pipe.run_aprs_pipeline(str(p), fs=8000.0, target_fs=48000.0)
    assert r2["ok"] is False and r2["content"] == {}
    assert r2["reason"] == "sample_rate_mismatch"
    assert r2["stage"] == "downconvert"


# ---------------------------------------------------------------------------
# 4) 性能：单帧耗时、实时率（对 2.4 MS/s）、跟不上时的行为
# ---------------------------------------------------------------------------
def test_performance_summary_reports_realtime_ratio(pipe):
    m = _manifest()["adsb"]
    r = pipe.run_adsb_pipeline(
        str(FIXTURE_DIR / m["file"]), fs=float(m["fs_hz"]),
        target_fs=float(m["target_fs_hz"]))
    perf = pipe.performance_summary(r, nominal_sample_rate=2.4e6)
    assert perf["n_frames"] == m["frame_count"]
    assert perf["total_elapsed_ms"] > 0
    assert perf["per_frame_ms"] > 0
    assert perf["throughput_samples_per_s"] > 0
    assert perf["realtime_ratio_vs_2_4msps"] > 0
    assert perf["behavior_when_behind"]
    assert "卡死" in perf["behavior_when_behind"]
    assert "丢弃" in perf["behavior_when_behind"]


def test_warm_benchmark_reports_verdict_and_runs(pipe):
    """冷启动含 import/滤波预热（会给出误导性的「跟不上」），必须预热后再判定。"""
    m = _manifest()["adsb"]
    perf = pipe.benchmark_pipeline(
        "adsb", str(FIXTURE_DIR / m["file"]), fs=float(m["fs_hz"]),
        target_fs=float(m["target_fs_hz"]), repeats=3)
    assert perf["ok"] is True
    assert perf["warmup_runs"] == 1
    assert perf["measured_runs"] == 3
    assert len(perf["ratio_per_run"]) == 4          # 1 次预热 + 3 次实测
    assert perf["realtime_ratio_vs_2_4msps"] > 0
    assert isinstance(perf["keeps_up_with_2_4msps"], bool)
    assert perf["per_frame_ms"] > 0
    assert "卡死" in perf["behavior_when_behind"]


def test_benchmark_failure_is_not_a_success(pipe, tmp_path):
    p = tmp_path / "silence.cf32"
    _write_cf32(p, np.zeros(4000, dtype=np.complex128))
    m = _manifest()["adsb"]
    perf = pipe.benchmark_pipeline(
        "adsb", str(p), fs=float(m["fs_hz"]),
        target_fs=float(m["target_fs_hz"]), repeats=2)
    assert perf["ok"] is False
    assert perf["reason"] == "preamble_not_found"
    assert perf["stage"] == "sync"


def test_summary_declares_synthetic_not_ota_and_drop_semantics(pipe):
    m = _manifest()["adsb"]
    r = pipe.run_adsb_pipeline(
        str(FIXTURE_DIR / m["file"]), fs=float(m["fs_hz"]),
        target_fs=float(m["target_fs_hz"]))
    perf = pipe.performance_summary(r)
    scope = perf["evidence_scope"].lower()
    assert "synthetic" in scope and "not ota" in scope
    assert "空口" in perf["evidence_scope"]
    assert "FCS/CRC" in perf["drop_accounting"]
    assert "SDR" in perf["drop_accounting"]


def test_behind_consequences_are_observable(pipe):
    """构造「消费侧慢 4×」的必然跟不上的场景，不能只给一个 bool。"""
    m = _manifest()["adsb"]
    r = pipe.run_adsb_pipeline(
        str(FIXTURE_DIR / m["file"]), fs=float(m["fs_hz"]),
        target_fs=float(m["target_fs_hz"]))
    rep = pipe.behind_report(r, speed_factor=0.25)     # 0.25× = 慢 4 倍
    assert rep["keeps_up"] is False
    assert rep["bottleneck_stage"] in {
        "load_iq", "downconvert", "sync", "demod", "frame", "content"}
    assert rep["bottleneck_share_pct"] > 0
    # 同步无队列：不积压、观测不到源端溢出——必须是明确数字/布尔，不是形容词
    assert rep["backlog_samples"] == 0
    assert rep["source_overflow"] is False
    assert "本模块不丢样本" in rep["backlog_interpretation"]
    assert "SDR" in rep["backlog_interpretation"]
    assert "FCS/CRC" in rep["drop_accounting"]
    assert set(x["name"] for x in rep["stages"]) == {
        s["name"] for s in r["stages"]}


def test_behind_report_can_say_can_keep_up(pipe):
    """同一份输入，消费侧快 2× 时必须给出 keeps_up=True（不能恒为 False）。"""
    m = _manifest()["ais"]
    r = pipe.run_ais_pipeline(
        str(FIXTURE_DIR / m["file"]), fs=float(m["fs_hz"]),
        target_fs=float(m["target_fs_hz"]))
    rep = pipe.behind_report(r, speed_factor=2.0)
    assert rep["keeps_up"] is True
    assert rep["speed_factor"] == 2.0


def test_adsb_frequency_offset_tolerance_200_and_1000_hz(pipe):
    """±200 Hz 应 3/3；±1 kHz 只有 2/3（一帧 CRC 失败）——据实断言，不粉饰。

    实测（8 MS/s fixture，3 帧）：
      ±200 Hz → 3/3；±1 kHz → 2/3；且偏移增大并不单调
      （±2 kHz 3/3、±5 kHz 2/3、±10 kHz 3/3）。所以这里只钉
      「±200 Hz 全解」与「±1 kHz 至少 2 帧且 ok=True」，边界原文写进
      docs/limitations-register.md。
    """
    m = _manifest()["adsb"]
    rows = pipe.sweep_frequency_offset(
        "adsb", str(FIXTURE_DIR / m["file"]), fs=float(m["fs_hz"]),
        target_fs=float(m["target_fs_hz"]),
        offsets_hz=[-1000.0, -200.0, 0.0, 200.0, 1000.0])
    assert [r["offset_hz"] for r in rows] == [-1000.0, -200.0, 0.0, 200.0, 1000.0]
    by_off = {r["offset_hz"]: r for r in rows}
    for off in (-200.0, 0.0, 200.0):
        assert by_off[off]["ok"] is True, by_off[off]
        assert by_off[off]["frames"] == m["frame_count"], by_off[off]
    for off in (-1000.0, 1000.0):
        assert by_off[off]["ok"] is True, by_off[off]
        assert by_off[off]["frames"] >= 2, by_off[off]


def test_frequency_offset_sweep_reports_failure_honestly(pipe):
    """偏到远超信号带外时，必须给 ok=False + reason，而不是 0 帧仍称成功。"""
    m = _manifest()["adsb"]
    path = str(FIXTURE_DIR / m["file"])
    rows = pipe.sweep_frequency_offset(
        "adsb", path, fs=float(m["fs_hz"]),
        target_fs=float(m["target_fs_hz"]), offsets_hz=[3.9e6])
    assert rows[0]["frames"] == 0
    assert rows[0]["ok"] is False
    assert rows[0]["reason"] in {"preamble_not_found", "no_frame_recovered",
                                 "nothing_detected"}


def test_realtime_ratio_formula_is_consistent(pipe):
    """实时率 = 输入样本吞吐 / 2.4MS/s；用报告字段反算必须一致（±1%）。"""
    m = _manifest()["ais"]
    r = pipe.run_ais_pipeline(
        str(FIXTURE_DIR / m["file"]), fs=float(m["fs_hz"]),
        target_fs=float(m["target_fs_hz"]))
    perf = pipe.performance_summary(r, nominal_sample_rate=2.4e6)
    expect = (perf["throughput_samples_per_s"] / 2.4e6)
    assert abs(perf["realtime_ratio_vs_2_4msps"] - expect) / expect < 0.01
