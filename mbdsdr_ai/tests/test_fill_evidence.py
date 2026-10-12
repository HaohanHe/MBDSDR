# SPDX-License-Identifier: MIT
from mbdsdr_ai.fill_evidence_example import analyze_resonance_py


def test_resonance_py_basic():
    # 自造谷值：|Z| 在中点最小，两侧抬升
    freqs = [100e6 + i * 1e6 for i in range(11)]
    z = [1.0, 0.8, 0.6, 0.4, 0.2, 0.1, 0.2, 0.4, 0.6, 0.8, 1.0]
    s11 = [[v, 0.0] for v in z]
    r = analyze_resonance_py(freqs, s11)
    assert abs(r["fr_hz"] - 105e6) < 1e-6  # 谷在 index 5
    assert r["esr"] == 0.1
    assert r["bw_hz"] > 0


def test_tdr_positive_length():
    from mbdsdr_ai.fill_evidence_example import tdr_length_py
    freqs = [1e9 + i * 1e6 for i in range(11)]
    s11 = [[0.1, 0.0]] * 5 + [[0.9, 0.0]] + [[0.1, 0.0]] * 5  # 反射峰在中间
    L = tdr_length_py(freqs, s11, vf=0.66)
    assert L > 0.0


def test_lc_pin():
    from mbdsdr_ai.fill_evidence_example import reactance_lc_py
    # 1 uH @ 100 MHz -> X = 2π*1e8*1e-6 ≈ 628.3 ohm
    lnh, _ = reactance_lc_py(100e6, 2 * 3.14159265 * 100e6 * 1e-6)
    assert abs(lnh - 1000.0) < 5.0  # nH


def test_filter_shape():
    from mbdsdr_ai.fill_evidence_example import filter_shape_py
    assert filter_shape_py([0, 0, -30, -30, 0, 0, -30, -30]) in ("lowpass", "bandpass")


def test_resonance_empty_no_crash():
    from mbdsdr_ai.fill_evidence_example import analyze_resonance_py
    r = analyze_resonance_py([], [])
    assert r["bw_hz"] == 0.0 or r["fr_hz"] == 0.0


def test_tdr_empty():
    from mbdsdr_ai.fill_evidence_example import tdr_length_py
    assert tdr_length_py([], []) == 0.0


def test_cli_input_evidence(tmp_path):
    import subprocess, sys, json
    sw = tmp_path / "sw.json"
    subprocess.run([sys.executable, "-m", "mbdsdr_ai.nanovna_dump", "--selftest",
                    "--out", str(sw)], check=True, capture_output=True)
    out = subprocess.run([sys.executable, "-m", "mbdsdr_ai.fill_evidence_example",
                          "--input", str(sw), "--format", "json"],
                         check=True, capture_output=True, text=True)
    row = json.loads(out.stdout)
    for k in ("VSWR", "回波损耗_dB", "谐振_fr_MHz", "谐振_ESR_ohm",
              "电缆长度_m", "L_nH", "C_pF", "滤波器形态"):
        assert k in row
    assert "_MARKER" not in row  # 真机输入不再带占位


def test_cli_bad_field(tmp_path):
    import json, subprocess, sys
    (tmp_path / "bad.json").write_text(json.dumps({"frequencies_hz": [], "S11": []}),
                                       encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "mbdsdr_ai.fill_evidence_example",
                       "--input", str(tmp_path / "bad.json")],
                      capture_output=True, text=True)
    assert r.returncode == 1 or "空" in (r.stdout + r.stderr)
