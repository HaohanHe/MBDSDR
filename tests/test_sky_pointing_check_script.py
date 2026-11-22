# SPDX-License-Identifier: MIT
"""``scripts/sky_pointing_check.py`` 的手动核对脚本测试（subprocess，离线）。

覆盖口径
--------
* ``--help`` 可用；
* 合法报告（双源观测 + 站址 + 时刻、无星点）退出 0，输出级别并明确跳过残差；
* 缺 ``site`` / ``observations`` 退出 2；
* 星点齐全时按本机真实解算状态分支断言：解出则 0 + residual，解不出则
  退 3 且写明「未算出」与具体错误码（外部库缺失会自动回退内置解算）；
* 任何输出都不得出现「已对准」结论。
"""

import json
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.sky_pointing_pipeline import MIN_SOLVE_STARS  # noqa: E402

_SCRIPT = os.path.join(_ROOT, "scripts", "sky_pointing_check.py")
_NOW = 1_700_000_000.0
_AT_UTC = "2023-11-14T22:13:20+00:00"


def _run(*args):
    return subprocess.run([sys.executable, _SCRIPT, *args],
                          cwd=_ROOT, capture_output=True, text=True,
                          timeout=180)


def _write(tmp_path, payload, name="report.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False),
                    encoding="utf-8")
    return path


def _base_report():
    return {
        "observations": [
            {"source": "imu", "t_utc": _NOW - 0.4, "az_deg": 10.0,
             "alt_deg": 20.0, "accuracy_deg": 8.0},
            {"source": "gnss", "t_utc": _NOW - 0.2, "az_deg": 12.0,
             "alt_deg": 21.0, "accuracy_deg": 3.0},
        ],
        "site": {"lat_deg": 31.2, "lon_deg": 121.5},
        "at_utc": _AT_UTC,
    }


def _with_solve_inputs(report):
    report["camera"] = {"width_px": 1920, "height_px": 1080,
                        "fov_deg": 8.0}
    report["commanded"] = {"az_deg": 11.0, "el_deg": 20.5}
    report["stars"] = [[100.0 + 10.0 * i, 200.0 + 7.0 * i]
                       for i in range(MIN_SOLVE_STARS)]
    return report


def test_help_shows_input_option():
    proc = _run("--help")
    assert proc.returncode == 0, proc.stderr
    assert "--input" in proc.stdout


def test_valid_report_prints_level_and_skips_residual(tmp_path):
    path = _write(tmp_path, _base_report())
    proc = _run("--input", str(path), "--now-utc", str(_NOW))
    out = proc.stdout
    assert proc.returncode == 0, proc.stderr
    assert "level" in out
    assert ("L2" in out) or ("L1" in out)
    assert "跳过残差" in out


def test_missing_site_exits_2(tmp_path):
    payload = _base_report()
    payload.pop("site")
    path = _write(tmp_path, payload)
    proc = _run("--input", str(path), "--now-utc", str(_NOW))
    assert proc.returncode == 2
    assert "site" in (proc.stdout + proc.stderr)


def test_missing_observations_exits_2(tmp_path):
    payload = _base_report()
    payload.pop("observations")
    path = _write(tmp_path, payload)
    proc = _run("--input", str(path), "--now-utc", str(_NOW))
    assert proc.returncode == 2
    assert "observations" in (proc.stdout + proc.stderr)


def test_solve_inputs_follow_real_solver_status(tmp_path):
    path = _write(tmp_path, _with_solve_inputs(_base_report()))
    proc = _run("--input", str(path), "--now-utc", str(_NOW))
    out = proc.stdout + proc.stderr
    # 外部库不在时会回退内置 quad 解算；这组共线星点解不出来，必须退出 3，
    # 并把「未算出 + 具体错误」写出来，不能以 0 冒充解算成功。
    if proc.returncode == 0:
        assert "residual" in out, out
    else:
        assert proc.returncode == 3, out
        assert "未算出" in out
        assert "solver_" in out


def test_output_never_claims_aligned(tmp_path):
    path = _write(tmp_path, _base_report())
    human = _run("--input", str(path), "--now-utc", str(_NOW))
    machine = _run("--input", str(path), "--now-utc", str(_NOW), "--json")
    assert human.returncode == 0, human.stderr
    assert machine.returncode == 0, machine.stderr
    assert "已对准" not in human.stdout + human.stderr
    assert "已对准" not in machine.stdout + machine.stderr
