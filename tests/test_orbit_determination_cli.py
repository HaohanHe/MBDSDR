# SPDX-License-Identifier: MIT
"""观测→定轨 CLI（``experiments/exp_doppler_orbit.py --iq``）判据。

合成样本与 ``tests/test_orbit_determination_chain.py`` 同一组参数（逐位一致）：
ISS TLE、39.9N/116.4E/50m、435 MHz、48 kHz、20 s；链上偏差 2.184 km（记录为 2.2 km）。

钉住两件事：

1. 合成样本能跑通：输出偏差与直接调用链路**逐位一致**，快照里能看见
   「依据哪份 TLE / 什么时刻 / 什么站址」；
2. 缺 TLE、缺站址、缺中心频率/时刻/采样率、IQ 为空、观测不足——一律退出码
   非零、stderr 指明缺什么、**不写快照、不打印轨道**（不许返回看似合理的结果）。
"""
import json
import os
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from experiments.exp_doppler_orbit import main  # noqa: E402
from mbdsdr_ai.orbit_determination import (  # noqa: E402
    OMEGA_E,
    doppler_orbit_determine,
    extract_doppler_observations,
    geodetic_to_ecef,
    leosat_state_from_tle,
)

ISS_L1 = ("1 25544U 98067A   08264.51780074  .00016553  00000-0  10270-3 0  2897"
          ).ljust(69)
ISS_L2 = ("2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563530"
          ).ljust(69)

LAT_DEG, LON_DEG, ALT_M = 39.9, 116.4, 50.0
F0_HZ = 435.0e6
FS_HZ = 48000.0
DURATION_S = 20.0
EPOCH_UNIX = 1222351200.0
SCHEMA = "mbdsdr.orbit_determination_evidence/1"


def _synthesize_iq() -> np.ndarray:
    """按 TLE 真轨迹合成 20 s 复基带 IQ（与链路测试同一组参数，无随机数）。"""
    rx = geodetic_to_ecef(LAT_DEG, LON_DEG, ALT_M / 1000.0)
    rx_vel = np.array([-OMEGA_E * rx[1], OMEGA_E * rx[0], 0.0])
    n = int(round(DURATION_S * FS_HZ))
    grid_t = EPOCH_UNIX + np.arange(0.0, DURATION_S + 0.05, 0.05)
    grid_fd = np.empty(len(grid_t), dtype=np.float64)
    for i, ti in enumerate(grid_t):
        r, v = leosat_state_from_tle(ISS_L1, ISS_L2, float(ti))
        los = r - rx
        grid_fd[i] = (-float(np.dot(los / np.linalg.norm(los), v - rx_vel))
                      * 1000.0 / 299792458.0 * F0_HZ)
    fds = np.interp(EPOCH_UNIX + np.arange(n) / FS_HZ, grid_t, grid_fd)
    phase = 2.0 * np.pi * np.cumsum(fds) / FS_HZ
    return np.exp(1j * phase).astype(np.complex64)


def _write_case(tmp_path):
    """落一份 cf32 IQ + 两行 TLE 文件 + 空 IQ；返回 (iq 数组, 路径...)。"""
    iq = _synthesize_iq()
    iq_path = tmp_path / "synth.cf32"
    np.ascontiguousarray(iq).view(np.float32).tofile(str(iq_path))
    tle_path = tmp_path / "iss.tle"
    tle_path.write_text(ISS_L1.rstrip() + "\n" + ISS_L2.rstrip() + "\n",
                        encoding="utf-8")
    empty_path = tmp_path / "empty.cf32"
    empty_path.write_bytes(b"")
    return iq, str(iq_path), str(tle_path), str(empty_path)


def _args(iq_path, tle_path, snap_path):
    return ["--iq", iq_path, "--fs", "48000", "--f0", "435e6",
            "--t0", str(EPOCH_UNIX), "--tle-file", tle_path,
            "--lat", str(LAT_DEG), "--lon", str(LON_DEG),
            "--alt", str(ALT_M), "--window-s", "0.4", "--hop-s", "0.2",
            "--snr-threshold-db", "8", "--min-observations", "5",
            "--evidence-out", snap_path]


def _without(args, flag):
    i = args.index(flag)
    return args[:i] + args[i + 2:]


def test_synthetic_sample_reproduces_chain_2p2km(tmp_path, capsys):
    iq, iq_path, tle_path, _ = _write_case(tmp_path)
    snap = tmp_path / "snap.json"
    rc = main(_args(iq_path, tle_path, str(snap)))
    out = capsys.readouterr()

    assert rc == 0, out.err
    payload = json.loads(snap.read_text(encoding="utf-8"))

    # 直接调用链路作为对拍基准（同一份 IQ、同一初值）
    obs = extract_doppler_observations(
        iq, FS_HZ, f0=F0_HZ, t0=EPOCH_UNIX, window_s=0.4, hop_s=0.2,
        snr_threshold_db=8.0, min_observations=5)
    r0, v0 = leosat_state_from_tle(ISS_L1, ISS_L2, EPOCH_UNIX)
    direct = doppler_orbit_determine(
        obs, rx_lat=LAT_DEG, rx_lon=LON_DEG, rx_alt=ALT_M,
        init_state=np.concatenate([r0, v0, [0.0]]),
        f0=F0_HZ, estimator="ekf")
    r_true, _ = leosat_state_from_tle(ISS_L1, ISS_L2, obs[-1][0])
    expected_km = float(
        np.linalg.norm(np.asarray(direct["state"])[0:3] - r_true))

    assert len(obs) == 99
    assert payload["result"]["n_observations"] == len(obs)
    assert payload["result"]["deviation_km"] == pytest.approx(
        expected_km, abs=1e-9), "CLI 偏差必须与链路直接调用逐位一致"
    assert 2.0 <= expected_km <= 2.4, "链上记录的偏差是 2.2 km"

    # 输出里必须能看见「依据哪份 TLE、什么时刻、什么站址」
    assert "NORAD 25544" in out.out
    assert "2008-09-25T14:00:00Z" in out.out
    assert "lat=39.9000 lon=116.4000 alt=50.0" in out.out
    assert "2.184" in out.out

    assert payload["schema"] == SCHEMA
    assert payload["status"] == {"complete": True, "uncorrected": []}
    assert payload["inputs"]["tle"]["line1"] == ISS_L1.rstrip()
    assert payload["inputs"]["tle"]["satellite_number"] == "25544"
    assert payload["inputs"]["station"] == {
        "lat_deg": LAT_DEG, "lon_deg": LON_DEG, "alt_m": ALT_M}
    assert payload["inputs"]["t0_utc"] == "2008-09-25T14:00:00Z"
    assert payload["inputs"]["iq"]["n_samples"] == int(DURATION_S * FS_HZ)
    assert len(payload["inputs"]["iq"]["sha256"]) == 64


def test_snapshot_replay_same_result(tmp_path):
    """同一快照输入回放两次，结论逐字段一致（快照可复核）。"""
    _, iq_path, tle_path, _ = _write_case(tmp_path)
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    assert main(_args(iq_path, tle_path, str(a))) == 0
    assert main(_args(iq_path, tle_path, str(b))) == 0
    pa = json.loads(a.read_text(encoding="utf-8"))
    pb = json.loads(b.read_text(encoding="utf-8"))
    assert pa["result"] == pb["result"]


def test_missing_tle_exits_nonzero(tmp_path, capsys):
    _, iq_path, _, _ = _write_case(tmp_path)
    snap = tmp_path / "snap.json"
    args = _without(_args(iq_path, "unused", str(snap)), "--tle-file")

    assert main(args) != 0
    captured = capsys.readouterr()
    assert "缺少 TLE" in captured.err
    assert "[估计]" not in captured.out and "[偏差]" not in captured.out
    assert not snap.exists()


def test_missing_station_exits_nonzero(tmp_path, capsys):
    _, iq_path, tle_path, _ = _write_case(tmp_path)
    snap = tmp_path / "snap.json"
    args = _args(iq_path, tle_path, str(snap))
    for flag in ("--lat", "--lon", "--alt"):
        args = _without(args, flag)

    assert main(args) != 0
    captured = capsys.readouterr()
    assert "观测站坐标" in captured.err
    assert "[估计]" not in captured.out and "[偏差]" not in captured.out
    assert not snap.exists()


def test_empty_iq_exits_nonzero(tmp_path, capsys):
    _, _, tle_path, empty_path = _write_case(tmp_path)
    snap = tmp_path / "snap.json"

    assert main(_args(empty_path, tle_path, str(snap))) != 0
    captured = capsys.readouterr()
    assert "IQ 为空" in captured.err
    assert "[估计]" not in captured.out and "[偏差]" not in captured.out
    assert not snap.exists()


def test_insufficient_observations_exits_nonzero(tmp_path, capsys):
    _, iq_path, tle_path, _ = _write_case(tmp_path)
    snap = tmp_path / "snap.json"
    args = _args(iq_path, tle_path, str(snap))
    args[args.index("--min-observations") + 1] = "200"

    assert main(args) != 0
    captured = capsys.readouterr()
    assert "观测不足" in captured.err
    assert "[估计]" not in captured.out and "[偏差]" not in captured.out
    assert not snap.exists()


@pytest.mark.parametrize("flag,fragment", [
    ("--f0", "中心频率"),
    ("--t0", "观测起始时刻"),
    ("--fs", "采样率"),
])
def test_missing_required_input_names_what_is_missing(tmp_path, capsys,
                                                      flag, fragment):
    _, iq_path, tle_path, _ = _write_case(tmp_path)
    snap = tmp_path / "snap.json"
    args = _without(_args(iq_path, tle_path, str(snap)), flag)

    assert main(args) != 0
    captured = capsys.readouterr()
    assert fragment in captured.err
    assert not snap.exists()
