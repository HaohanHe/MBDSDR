# SPDX-License-Identifier: MIT
import math
from pathlib import Path

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


# ---------------------------------------------------------------------------
# TDR 三档窗（2026-10-13 官方页核对后接线：rect/kaiser6/kaiser13）
# ---------------------------------------------------------------------------

def _cable_s11(freqs_hz, length_m, vf=0.66):
    """理想开口线：复 S11 = exp(-j*2*pi*f*tau)，tau=往返时延。"""
    c = 299792458.0
    tau = 2.0 * length_m / (vf * c)
    return [[math.cos(-2 * math.pi * f * tau), math.sin(-2 * math.pi * f * tau)]
            for f in freqs_hz]


def test_tdr_windowed_idft_recovers_cable_length():
    """物理夹具：500MHz-1GHz、101 点，1.0m 理想线（往返时延 4.4ns）。
    峰位 bin=tau*span≈4.4 -> bin4/5 -> L≈0.9-1.1m（bin 分辨率 0.9m）。
    三档窗均应命中同一 bin（官方默认 NORMAL=kaiser6）。"""
    from mbdsdr_ai.fill_evidence_example import tdr_length_py
    freqs = [500e6 + i * 5e6 for i in range(101)]
    s11 = _cable_s11(freqs, 1.0)
    for w in ("rect", "kaiser6", "kaiser13"):
        L = tdr_length_py(freqs, s11, vf=0.66, window=w)
        assert 0.7 <= L <= 1.3, f"window={w} 离谱: {L}"


def test_tdr_window_param_validation():
    from mbdsdr_ai.fill_evidence_example import tdr_length_py
    freqs = [500e6 + i * 5e6 for i in range(101)]
    s11 = _cable_s11(freqs, 1.0)
    try:
        tdr_length_py(freqs, s11, window="hann")
        assert False, "未知窗必须诚实报错，不许静默兜底"
    except ValueError:
        pass


def test_tdr_degenerate_inputs():
    from mbdsdr_ai.fill_evidence_example import tdr_length_py
    assert tdr_length_py([], []) == 0.0
    assert tdr_length_py([1e6, 1e6, 1e6], [[0.1, 0.0]] * 3) == 0.0  # span=0
    assert tdr_length_py([1e6, 2e6], [[0.1, 0.0]] * 2) == 0.0       # n<4


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
    import subprocess, sys, json, os
    # 子进程显式钉 UTF-8（与父进程解码口径无关）：父侧以 -X utf8 /
    # PYTHONIOENCODING 运行时，子进程默认走 locale（GBK）会让
    # capture_output(text=True) 的读取线程崩 UnicodeDecodeError——
    # 2026-10-15 踩过，此处钉死消除隐式耦合。
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    # 子进程 cwd 钉死到包父目录：-m 依赖 sys.path[0]=cwd 找到 mbdsdr_ai，
    # 从包目录内启动 pytest（cwd=mbdsdr_ai）会让子进程 import 失败——
    # 2026-10-31 踩过，与父进程启动目录解耦。
    cwd = str(Path(__file__).resolve().parents[2])
    sw = tmp_path / "sw.json"
    subprocess.run([sys.executable, "-m", "mbdsdr_ai.nanovna_dump", "--selftest",
                    "--out", str(sw)], check=True, capture_output=True, env=env,
                   cwd=cwd)
    out = subprocess.run([sys.executable, "-m", "mbdsdr_ai.fill_evidence_example",
                          "--input", str(sw), "--format", "json"],
                         check=True, capture_output=True, text=True,
                         encoding="utf-8", env=env, cwd=cwd)
    row = json.loads(out.stdout)
    for k in ("VSWR", "回波损耗_dB", "谐振_fr_MHz", "谐振_ESR_ohm",
              "电缆长度_m", "L_nH", "C_pF", "滤波器形态"):
        assert k in row
    assert "_MARKER" not in row  # 真机输入不再带占位


def test_cli_bad_field(tmp_path):
    import json, subprocess, sys, os
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    # cwd 钉死到包父目录（与 test_cli_input_evidence 同因）；断言要求
    # 诚实空态文案而非裸退出码——否则子进程 import 失败也会"通过"。
    cwd = str(Path(__file__).resolve().parents[2])
    (tmp_path / "bad.json").write_text(json.dumps({"frequencies_hz": [], "S11": []}),
                                       encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "mbdsdr_ai.fill_evidence_example",
                        "--input", str(tmp_path / "bad.json")],
                       capture_output=True, text=True, encoding="utf-8", env=env,
                       cwd=cwd)
    assert r.returncode == 1
    # 该输入（缺 S21）走"缺字段"诚实拒绝路径（fill_evidence_example
    # SystemExit 文案）；文案断言同时防裸退出码假阳性（子进程 import
    # 失败也会退出码 1 但不带此文案）。
    assert "缺字段" in (r.stdout + r.stderr), \
        f"未见诚实错误文案（子进程可能未真正跑到 CLI）: {r.stderr[-500:]}"
