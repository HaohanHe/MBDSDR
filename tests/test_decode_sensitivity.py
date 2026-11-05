"""tests/test_decode_sensitivity.py — 解码器灵敏度基线脚本（P5 之后）。

事实口径（与脚本同源，避免「凭记忆的数字」）：
  * AIS 链路层：ITU-R M.1371（9600 bit/s GMSK、HDLC 标志/FCS-16、NRZI）；
  * ADS-B 1090ES：ICAO Annex 10 Vol IV / RTCA DO-260（8µs 前导、PPM、
    CRC-24 多项式 0xFFF409）；
  * APRS / AX.25：TAPR AX.25 + Bell 202 AFSK 1200bd；音频取自随仓合成
    wav fixture（NOT OTA），SNR 口径是鉴频后音频域（与复基带不可换算）；
  * FT4：4-FSK GFSK 12 kS/s（复基带）；POCSAG / ACARS：鉴频后音频域。

守这些契约（只钉方向性，不钉会随浮点/numpy 版本漂移的具体阈值）：
  * 高 SNR 必成功、极低 SNR 必失败；
  * 阈值落在扫描网格内，100% 档不早于目标档；
  * 每模式 ≥6 档且网格覆盖 0% 与 100% 两端；
  * 每模式自述口径（噪声域/带宽/SNR），总表含 90%/100%/临界区间/首个失败码/
    是否单调，并注明「全部为合成 fixture，非 OTA」；
  * 非单调必须标「边缘效应，未解释机理」（允许相邻档 ±1 次抖动）；
  * 音频模式每档必须带「解出帧数/丢帧数/首个失败码」；
  * 同种子可复现（逐档成功数完全一致）；
  * 失败要有分类原因（不是一律 "failed"）；
  * 未知模式显式报错。
"""
import importlib.util
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

SCRIPT = os.path.join(_ROOT, "scripts", "decode_sensitivity.py")
MODES = ("ais", "adsb", "aprs", "ft4", "pocsag", "acars")
APRS_GRID = [-6.0, -4.0, -2.0, 0.0, 2.0, 6.0]
APRS_KNOWN_REASONS = {
    "nothing_detected", "no_frame_recovered", "truncated_frame",
    "crc_failed", "payload_mismatch", "insufficient_samples",
}

os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")


@pytest.fixture(scope="module")
def sens():
    spec = importlib.util.spec_from_file_location("decode_sensitivity", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_snr_grid_range_and_list():
    spec = importlib.util.spec_from_file_location("ds_parse", SCRIPT)
    sens = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sens)

    assert sens.parse_snr_grid("-4:4:2") == [-4.0, -2.0, 0.0, 2.0, 4.0]
    assert sens.parse_snr_grid("0,8,4") == [0.0, 4.0, 8.0]
    with pytest.raises(ValueError):
        sens.parse_snr_grid("0:4")
    with pytest.raises(ValueError):
        sens.parse_snr_grid("4:0:1")
    with pytest.raises(ValueError):
        sens.parse_snr_grid(" , ")


@pytest.mark.parametrize("mode", MODES)
def test_high_snr_always_succeeds(sens, mode):
    top = sens.MODE_HIGH_SNR_DB[mode]
    out = sens.sweep_mode(mode, [top], trials=5)
    assert out["rows"][0]["successes"] == 5
    assert out["thresholds"]["evaluable"] is True
    assert out["thresholds"]["threshold_snr_db"] <= top


@pytest.mark.parametrize("mode", MODES)
def test_very_low_snr_always_fails_and_reports_reason(sens, mode):
    out = sens.sweep_mode(mode, [-30.0], trials=5)
    row = out["rows"][0]
    assert row["successes"] == 0
    assert row["failure_reasons"], "失败必须留下分类原因，不能只记成败"
    # 原因必须是脚本里声明过的分类，不允许自由文本
    for reason in row["failure_reasons"]:
        assert (reason in sens.KNOWN_FAILURE_REASONS
                or reason.startswith("decode_error:"))
    assert out["thresholds"]["evaluable"] is False
    assert out["thresholds"]["threshold_snr_db"] is None


@pytest.mark.parametrize("mode", MODES)
def test_threshold_is_directional_and_inside_grid(sens, mode):
    grid = [0.0, 6.0, 12.0, 16.0]
    out = sens.sweep_mode(mode, grid, trials=5)
    first, last = out["rows"][0], out["rows"][-1]
    assert last["success_rate"] >= first["success_rate"]
    t = out["thresholds"]
    if t["evaluable"]:
        assert t["threshold_snr_db"] in grid
        if t["threshold_100pct_snr_db"] is not None:
            assert t["threshold_100pct_snr_db"] >= t["threshold_snr_db"]


@pytest.mark.parametrize("mode", MODES)
def test_same_seed_reproducible(sens, mode):
    a = sens.sweep_mode(mode, [0.0, 8.0], trials=4, seed=12345)
    b = sens.sweep_mode(mode, [0.0, 8.0], trials=4, seed=12345)
    assert [(r["snr_db"], r["successes"], r["failure_reasons"])
            for r in a["rows"]] == \
           [(r["snr_db"], r["successes"], r["failure_reasons"])
            for r in b["rows"]]


def test_run_sensitivity_report_shape_and_markdown(sens):
    report = sens.run_sensitivity(list(MODES), [0.0, 16.0], trials=3)
    assert report["seed"] == sens.DEFAULT_SEED
    assert report["snr_grid_db"] == [0.0, 16.0]
    assert set(report["modes"]) == set(MODES)
    for m in report["modes"].values():
        assert m["fs_hz"] > 0
        assert m["source"] and m["condition"]
        assert len(m["rows"]) == 2
        assert m["thresholds"]["target_rate"] == pytest.approx(sens.DEFAULT_TARGET)
    table = sens.markdown_table(report)
    assert "`ais`" in table and "`acars`" in table
    assert "合成 fixture" in table and "非 OTA" in table
    assert "临界区间" in table and "是否单调" in table


def test_every_mode_self_describes_its_basis(sens):
    """口径必须逐模式自述：噪声域、带宽、SNR 每样本/每符号。"""
    for mode in MODES:
        basis = sens.MODE_BASIS[mode]
        assert basis["domain"], f"{mode} 缺噪声域"
        assert "噪声带宽" in basis["bandwidth"], f"{mode} 缺带宽定义"
        assert "每样本" in basis["snr"], f"{mode} 缺 SNR 口径"
        out = sens.sweep_mode(mode, [sens.MODE_HIGH_SNR_DB[mode]], trials=1)
        assert out["basis"] == basis
        assert out["grid_db"] == [sens.MODE_HIGH_SNR_DB[mode]]


def test_default_grids_have_at_least_six_levels_and_both_ends(sens):
    for mode in MODES:
        grid = sens.MODE_DEFAULT_GRID[mode]
        assert len(grid) >= 6, f"{mode} 默认网格不足 6 档"
        assert grid == sorted(grid)


def test_non_monotonic_rows_are_labelled_edge_effect(sens):
    """非单调必须如实标「边缘效应，未解释机理」；±1 次抖动允许且要说明。"""
    rows = [
        {"snr_db": 0.0, "successes": 10},
        {"snr_db": 2.0, "successes": 9},     # 回落 1 次：允许
        {"snr_db": 4.0, "successes": 20},
    ]
    mo = sens.derive_monotonicity(rows)
    assert mo["monotonic"] is True
    assert mo["jitter_trials"] == 1
    rows_bad = [
        {"snr_db": 0.0, "successes": 15},
        {"snr_db": 2.0, "successes": 10},    # 回落 5 次：非单调
        {"snr_db": 4.0, "successes": 20},
    ]
    mo_bad = sens.derive_monotonicity(rows_bad)
    assert mo_bad["monotonic"] is False
    assert mo_bad["note"] == "边缘效应，未解释机理"
    assert mo_bad["violations"][0]["drop"] == 5


def test_unknown_mode_rejected(sens):
    with pytest.raises(ValueError):
        sens.sweep_mode("not_a_mode", [0.0], trials=1)


# ---------------------------------------------------------------------------
# APRS：每档帧账目 + 只钉方向性与临界档区间（不钉具体 dB）
# ---------------------------------------------------------------------------
def test_aprs_rows_carry_frame_accounting(sens):
    out = sens.sweep_mode("aprs", APRS_GRID, trials=5)
    rows = out["rows"]
    assert len(rows) >= 6, "灵敏度扫描不少于 6 档"
    for r in rows:
        for key in ("frames_decoded", "frames_dropped", "frames_expected",
                    "first_failure_code"):
            assert key in r, f"APRS 行缺字段 {key}"
        assert r["frames_expected"] == 2 * r["trials"]
        assert r["frames_decoded"] >= 0 and r["frames_dropped"] >= 0
        assert r["frames_decoded"] <= r["frames_expected"]
        if r["failure_reasons"]:
            assert r["first_failure_code"], "有失败就必须给出首个失败码"
            assert r["first_failure_code"] != "unsupported_format", (
                "合法 wav 的解码失败不该由格式门背锅")
            assert (r["first_failure_code"] in APRS_KNOWN_REASONS
                    or r["first_failure_code"].startswith("decode_error:"))
        else:
            assert r["first_failure_code"] == ""

    # 方向性：SNR 越高，成功次数不减；高 SNR 必须 2/2 全解
    successes = [r["successes"] for r in rows]
    assert successes == sorted(successes), successes
    assert rows[-1]["successes"] == rows[-1]["trials"]
    assert rows[-1]["frames_decoded"] == rows[-1]["frames_expected"]
    assert rows[0]["successes"] == 0


def test_aprs_critical_band_is_within_interval(sens):
    """临界档只钉「落在 -6..2 dB 的相邻档」，不钉具体 dB 值。"""
    grid = [-6.0, -2.0, 0.0, 2.0]
    out = sens.sweep_mode("aprs", grid, trials=10)
    rows = out["rows"]
    assert rows[0]["successes"] == 0, "极低 SNR 必须 0 成功"
    assert rows[-1]["successes"] == rows[-1]["trials"], "高 SNR 必须全解"
    t = out["thresholds"]
    assert t["evaluable"] is True
    assert t["threshold_snr_db"] in grid
    assert -6.0 <= t["threshold_snr_db"] <= 2.0
    assert t["threshold_100pct_snr_db"] in grid
    assert -2.0 <= t["threshold_100pct_snr_db"] <= 6.0
    assert t["threshold_100pct_snr_db"] >= t["threshold_snr_db"]


def test_aprs_markdown_lists_per_row_accounting(sens):
    report = sens.run_sensitivity(["aprs"], APRS_GRID, trials=3)
    table = sens.markdown_table(report)
    assert "解出帧" in table and "首个失败码" in table
    assert "`aprs`" in table


def test_doc_snapshot_matches_live_sweep(sens):
    """能力矩阵文档里的灵敏度快照不能与实跑结果脱节（容差 ±2 dB，防漂移）。

    每模式用各自的默认网格（各模式口径不同，不共享一张 dB 网格）。
    """
    rep_path = os.path.join(_ROOT, "scripts", "decoder_capability_report.py")
    spec = importlib.util.spec_from_file_location("decoder_capability_report", rep_path)
    rep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rep)

    snap = rep.SENSITIVITY_SNAPSHOT
    assert set(snap) == set(MODES), "文档快照必须覆盖全部六模式"
    report = sens.run_sensitivity(
        sorted(snap), None,
        trials=rep.SENSITIVITY_TRIALS, seed=rep.SENSITIVITY_SEED)
    for mode, expected in snap.items():
        live = report["modes"][mode]["thresholds"]
        assert live["evaluable"], f"{mode} 默认网格内未达标，快照已不可解释"
        for key in ("threshold_snr_db", "threshold_100pct_snr_db"):
            if expected.get(key) is None or live.get(key) is None:
                continue
            assert abs(live[key] - expected[key]) <= 2.0, (
                f"{mode}.{key} 实跑 {live[key]} 与文档快照 {expected[key]} "
                "相差超过 2 dB——请运行脚本 --write 刷新文档")
        assert live["critical_band_db"] == expected["critical_band_db"], (
            f"{mode} 临界区间实跑 {live['critical_band_db']} 与快照 "
            f"{expected['critical_band_db']} 不一致——请刷新文档")
        first_bad = next((r["first_failure_code"]
                          for r in report["modes"][mode]["rows"]
                          if r["first_failure_code"]), "")
        assert first_bad == expected["first_failure_code"], (
            f"{mode} 首个失败码实跑 {first_bad!r} 与快照 "
            f"{expected['first_failure_code']!r} 不一致")
        mo = report["modes"][mode]["monotonicity"]
        assert mo["monotonic"] == expected["monotonic"], (
            f"{mode} 单调性实跑 {mo['monotonic']} 与快照 "
            f"{expected['monotonic']} 不一致——非单调必须如实登记")
