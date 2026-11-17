# SPDX-License-Identifier: MIT
"""星点解算适配器测试：用测试替身覆盖成功/星少/无匹配/超时/乱码。

这里的「解算」全部由替身完成：
* tetra3 用 ``sys.modules`` 注入的假模块；
* ASTAP 用临时目录里的假可执行脚本。

因此本文件只证明**适配器协议与错误映射**，不证明真实解算精度。
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.celestial_geometry import (  # noqa: E402
    GroundStation,
    altaz_to_j2000,
    radec_from_vec,
    vec_from_azalt,
)
from mbdsdr_ai.sky_solve import (  # noqa: E402
    MIN_SOLVE_STARS,
    solve_frame,
    solver_status,
)

_IMAGE = (1920, 1080)
_FOV = 8.0
_JD = 2460000.5
_LAT = 40.0
_LON = 116.0
_PRIOR = {"az_deg": 120.0, "alt_deg": 45.0, "roll_deg": 10.0}
_OBSERVER = {"lat_deg": _LAT, "lon_deg": _LON, "jd_ut1": _JD}


def _stars(n=MIN_SOLVE_STARS):
    return [(100.0 + 10.0 * i, 200.0 + 7.0 * i) for i in range(n)]


def _radec_for(az_deg, alt_deg):
    station = GroundStation(_LON, _LAT, 0.0, "test")
    v = altaz_to_j2000(vec_from_azalt(az_deg, alt_deg), station, _JD)
    return radec_from_vec(v)


def _inject_tetra3(monkeypatch, result):
    module = types.ModuleType("tetra3")

    class Tetra3:
        def solve_from_centroids(self, centroids, size, **kwargs):
            return result

    module.Tetra3 = Tetra3
    monkeypatch.setitem(sys.modules, "tetra3", module)
    return module


def _fake_astap_script(tmp_path):
    """假 ASTAP：按 FAKE_ASTAP_MODE 分支，成功时写 .ini。"""
    script = tmp_path / "fake_astap.py"
    script.write_text(
        "import os, sys, time\n"
        "args = sys.argv[1:]\n"
        "out_base = None\n"
        "for i, a in enumerate(args):\n"
        "    if a == '-o':\n"
        "        out_base = args[i + 1]\n"
        "mode = os.environ.get('FAKE_ASTAP_MODE', 'success')\n"
        "if mode == 'timeout':\n"
        "    time.sleep(5)\n"
        "    sys.exit(0)\n"
        "if mode == 'nonzero':\n"
        "    sys.stderr.write('ASTAP internal text that must not leak\\n')\n"
        "    sys.exit(2)\n"
        "if mode == 'garbage':\n"
        "    with open(out_base + '.ini', 'w', encoding='utf-8') as fh:\n"
        "        fh.write('not a solver output\\n')\n"
        "    sys.exit(0)\n"
        "lines = [\n"
        "    'PLTSOLVD=T',\n"
        "    'CRVAL1=' + os.environ['FAKE_ASTAP_RA'],\n"
        "    'CRVAL2=' + os.environ['FAKE_ASTAP_DEC'],\n"
        "    'ROLL=10.0',\n"
        "    'FOV=8.0',\n"
        "    'MATCHES=15',\n"
        "    'RMSE=4.0',\n"
        "]\n"
        "with open(out_base + '.ini', 'w', encoding='utf-8') as fh:\n"
        "    fh.write('\\n'.join(lines) + '\\n')\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        exe = tmp_path / "fake_astap.cmd"
        exe.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n',
            encoding="utf-8")
    else:
        exe = tmp_path / "fake_astap.sh"
        exe.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n',
            encoding="utf-8")
        exe.chmod(0o755)
    return str(exe)


def _astap_call(tmp_path, monkeypatch, mode, timeout_s=5.0):
    ra, dec = _radec_for(_PRIOR["az_deg"], _PRIOR["alt_deg"])
    monkeypatch.setenv("FAKE_ASTAP_MODE", mode)
    monkeypatch.setenv("FAKE_ASTAP_RA", repr(ra))
    monkeypatch.setenv("FAKE_ASTAP_DEC", repr(dec))
    exe = _fake_astap_script(tmp_path)
    image = tmp_path / "frame.fits"
    image.write_bytes(b"FAKE FITS")
    return solve_frame(_stars(), _IMAGE, _FOV, solver="astap",
                       astap_path=exe, image_path=str(image),
                       prior=_PRIOR, observer=_OBSERVER, timeout_s=timeout_s)


# ---------------------------------------------------------------------------
# tetra3
# ---------------------------------------------------------------------------
def test_tetra3_success_maps_deltas_stars_residual(monkeypatch):
    ra, dec = _radec_for(_PRIOR["az_deg"], _PRIOR["alt_deg"])
    _inject_tetra3(monkeypatch, {
        "RA": ra, "Dec": dec, "Roll": 10.0, "FOV": 8.0,
        "Matches": 12, "RMSE": 0.002,  # tetra3 的 RMSE 按度计
    })
    out = solve_frame(_stars(), _IMAGE, _FOV, solver="tetra3",
                      prior=_PRIOR, observer=_OBSERVER)
    assert out["ok"] is True, out["reason"]
    assert out["solver"] == "tetra3"
    assert out["matched_stars"] == 12
    assert out["residual_arcsec"] == pytest.approx(7.2, abs=1e-6)
    assert out["fov_deg"] == pytest.approx(_FOV)
    assert out["az_delta_deg"] == pytest.approx(0.0, abs=1e-6)
    assert out["alt_delta_deg"] == pytest.approx(0.0, abs=1e-6)
    assert out["roll_delta_deg"] == pytest.approx(0.0, abs=1e-6)
    assert out["reason"] == ""


def test_tetra3_is_reported_available_when_module_imports(monkeypatch):
    _inject_tetra3(monkeypatch, None)
    assert solver_status() == {"available": True, "name": "tetra3"}


def test_too_few_stars_is_reported_before_solver(monkeypatch):
    _inject_tetra3(monkeypatch, None)
    out = solve_frame(_stars(MIN_SOLVE_STARS - 1), _IMAGE, _FOV,
                      solver="tetra3", prior=_PRIOR, observer=_OBSERVER)
    assert out["ok"] is False
    assert out["error"] == "too_few_stars"
    assert out["reason"].startswith("星点不足")


def test_tetra3_no_match_is_structured(monkeypatch):
    _inject_tetra3(monkeypatch, None)
    out = solve_frame(_stars(), _IMAGE, _FOV, solver="tetra3",
                      prior=_PRIOR, observer=_OBSERVER)
    assert out["ok"] is False
    assert out["error"] == "no_match"
    assert "无匹配" in out["reason"]


def test_missing_tetra3_and_astap_is_honest(monkeypatch):
    monkeypatch.delitem(sys.modules, "tetra3", raising=False)
    monkeypatch.delenv("MBDSDR_ASTAP_PATH", raising=False)
    out = solve_frame(_stars(), _IMAGE, _FOV)
    assert out["ok"] is False
    assert out["error"] == "unavailable"
    assert "未安装 tetra3" in out["reason"]


# ---------------------------------------------------------------------------
# ASTAP
# ---------------------------------------------------------------------------
def test_astap_success_parses_output_without_leaking_text(tmp_path, monkeypatch):
    out = _astap_call(tmp_path, monkeypatch, "success")
    assert out["ok"] is True, out["reason"]
    assert out["solver"] == "astap"
    assert out["matched_stars"] == 15
    assert out["residual_arcsec"] == pytest.approx(4.0)
    assert out["az_delta_deg"] == pytest.approx(0.0, abs=1e-6)
    assert out["alt_delta_deg"] == pytest.approx(0.0, abs=1e-6)
    assert "CRVAL" not in str(out)
    assert "PLTSOLVD" not in str(out)


def test_astap_timeout_maps_to_timeout(tmp_path, monkeypatch):
    out = _astap_call(tmp_path, monkeypatch, "timeout", timeout_s=0.3)
    assert out["ok"] is False
    assert out["error"] == "timeout"
    assert "超时" in out["reason"]


def test_astap_nonzero_exit_maps_to_code_only(tmp_path, monkeypatch):
    out = _astap_call(tmp_path, monkeypatch, "nonzero")
    assert out["ok"] is False
    assert out["error"] == "nonzero_exit"
    assert out["exit_code"] == 2
    # ASTAP 的 stderr 原文不得泄漏到上层。
    assert "must not leak" not in str(out)


def test_astap_garbage_output_maps_to_parse_error(tmp_path, monkeypatch):
    out = _astap_call(tmp_path, monkeypatch, "garbage")
    assert out["ok"] is False
    assert out["error"] == "parse_error"
    assert "不可解析" in out["reason"]


def test_astap_without_executable_is_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("MBDSDR_ASTAP_PATH", raising=False)
    image = tmp_path / "frame.fits"
    image.write_bytes(b"FAKE FITS")
    out = solve_frame(_stars(), _IMAGE, _FOV, solver="astap",
                      image_path=str(image), prior=_PRIOR,
                      observer=_OBSERVER)
    assert out["ok"] is False
    assert out["error"] == "not_configured"
