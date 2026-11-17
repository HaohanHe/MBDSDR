# SPDX-License-Identifier: MIT
"""D2 金标复现脚本测试（不依赖 subprocess / 网络 / 外部工具）。"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

SCRIPT = os.path.join(_ROOT, "scripts", "decode_golden.py")


def _load():
    spec = importlib.util.spec_from_file_location("decode_golden", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_golden_report_reproduces_fixed_inputs():
    mod = _load()
    report = mod.build_report()
    expected = {name: meta["expected"] for name, meta in mod.GOLDEN.items()}
    diff = mod.first_divergence(expected, report["cases"], "cases")
    assert diff is None, f"首个分歧字段: {diff}"


def test_report_json_is_stable_and_fixed_fields():
    mod = _load()
    a = json.dumps(mod.build_report(), ensure_ascii=False, sort_keys=True)
    b = json.dumps(mod.build_report(), ensure_ascii=False, sort_keys=True)
    assert a == b
    data = json.loads(a)
    assert data["schema"] == 1
    assert set(data["cases"]) == {"adsb", "aprs"}
    assert set(data["cases"]["adsb"]) == {
        "ok", "status", "error_code", "num_frames", "frames_hex",
        "icaos", "callsigns"}
    assert set(data["cases"]["aprs"]) == {
        "ok", "status", "error_code", "num_frames", "frames_hex",
        "sources", "info_hex"}


def test_first_divergence_reports_field_path():
    mod = _load()
    expected = {"adsb": {"num_frames": 3, "frames_hex": ["aa", "bb"]}}
    actual = {"adsb": {"num_frames": 2, "frames_hex": ["aa", "bb"]}}
    diff = mod.first_divergence(expected, actual, "cases")
    assert diff is not None
    assert diff[0] == "cases.adsb.num_frames"
    assert diff[1] == 3 and diff[2] == 2

    expected = {"aprs": {"frames_hex": ["aa", "bb"]}}
    actual = {"aprs": {"frames_hex": ["aa", "cc"]}}
    diff = mod.first_divergence(expected, actual, "cases")
    assert diff is not None and diff[0] == "cases.aprs.frames_hex[1]"


def test_main_returns_zero_on_golden_match(capsys):
    mod = _load()
    assert mod.main([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["cases"]["adsb"]["num_frames"] == 3
    assert out["cases"]["aprs"]["num_frames"] == 2


def test_main_returns_nonzero_and_prints_first_divergence(monkeypatch, capsys):
    mod = _load()
    broken = {"adsb": dict(mod.GOLDEN["adsb"]["expected"]),
              "aprs": dict(mod.GOLDEN["aprs"]["expected"])}
    broken["aprs"]["num_frames"] = 99
    monkeypatch.setattr(mod, "GOLDEN", {
        "adsb": mod.GOLDEN["adsb"],
        "aprs": {**mod.GOLDEN["aprs"], "expected": broken["aprs"]},
    })
    assert mod.main([]) == 1
    out = capsys.readouterr().out
    assert "首个分歧字段: cases.aprs.num_frames" in out
