"""真机存档证据回归。

provenance: measured-archive —— 输入全部是 `experiments/nanovna_rh770/data/` 的
入库真机采集件（NanoVNA-H，2026-10-23，COM11），本文件不生成/合成/改写任何数据，
不得标成 self-generated。SHA256 钉扎：替换采集件必须同步更新哈希并附新采集记录。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

DATA = Path(__file__).resolve().parent.parent / "nanovna_rh770" / "data"
SCRIPT = Path(__file__).resolve().parent.parent / "nanovna_rh770" / "scripts" / "vna_cable_analyze.py"

W, H = 320, 240
FRAMES = ("live", "detail", "wide", "2m")

ARCHIVE_SHA256 = {
    "cable_open.json": "7095a425ff833cc7887f67ce8da7797b05a9cad6685c2927d9c5d0e61f2ef96f",
    "rh770.json": "85d11b3aa07dbc63031ab277245a7ca7b5833944fb3e4ae7a845db8d7bfe15c3",
    "vna_screen_live.rgb": "f23f536b77edc0935c3a73a5bb6e03d254b7e0318f50888d77b30a3da9a43fb4",
    "vna_screen_detail.rgb": "9f883dac43db276f631f79f8c5d5b71b04fed41a26ffe6b025d0ff4e77b4802c",
    "vna_screen_wide.rgb": "ab9065fbc79115df56fe7935f1f94b564ccec4fd6e25b59e28f1b79761f3e953",
    "vna_screen_2m.rgb": "15ecd170e8a4a312dab8e319d7afb437ecb1d54e1f4aaddc95d30d0be1ef9dc9",
}


@pytest.fixture(autouse=True)
def archives_pinned():
    """所有用例共用：先确认读到的就是被钉扎的原始采集件。"""
    for name, expected in ARCHIVE_SHA256.items():
        p = DATA / name
        assert p.is_file(), f"缺采集件 {name}"
        assert hashlib.sha256(p.read_bytes()).hexdigest() == expected, f"{name} 已被替换"


def _load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def _s11(seg: dict) -> np.ndarray:
    return np.abs(np.array([complex(*v) for v in seg["s11"]]))


def _module():
    spec = importlib.util.spec_from_file_location("vna_cable_analyze", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _nonblank_ratio(raw: bytes) -> float:
    """非空白像素比例 = 1 - 众数像素占比（RGB565 小端）。"""
    px = np.frombuffer(raw, dtype="<u2")
    _, counts = np.unique(px, return_counts=True)
    return 1.0 - counts.max() / px.size


def test_cable_tau_electrical_and_physical_length():
    """τ=|s|/(4π) → Le=c·τ → L=Le·VF；输出必须落在 0.963-1.021 m。"""
    r = _module().analyze()

    # 单程时延来自相位斜率；打印精度下 4.86 ns（原始 4.8655）
    assert 4.85 <= r["tau_ns"] <= 4.87
    assert round(r["electrical_length_m"], 3) == 1.459

    l_lo, l_hi = r["physical_length_m"]
    assert round(l_lo, 3) == 0.963 and round(l_hi, 3) == 1.021
    assert 0.9625 <= l_lo <= l_hi <= 1.0215

    # 交叉验证段 + 435MHz 回损（存档可直接复算）
    assert round(r["electrical_length2_m"], 3) == 1.556
    k435 = next(k for k in r["key_freqs"] if k["freq_hz"] == 435e6)
    assert abs(k435["return_loss_db"] - 1.53) <= 0.02
    assert abs(r["loss_db_range"][0] - 0.57) <= 0.01
    assert abs(r["loss_db_range"][1] - 0.85) <= 0.01


def test_capture_frames_size_content_and_bmp_derivation():
    """4 帧 320×240 RGB565；非空白比例阈值由四帧自身推出；BMP=54+W·H·3。"""
    ratios = []
    for name in FRAMES:
        raw = (DATA / f"vna_screen_{name}.rgb").read_bytes()
        assert len(raw) == W * H * 2, f"{name} 帧尺寸不符"
        assert np.frombuffer(raw, dtype="<u2").size == W * H
        ratios.append(_nonblank_ratio(raw))

    content_floor = 0.5 * min(ratios)  # 数据内推：最差帧的一半，不手填
    assert content_floor > 0.05
    for name, ratio in zip(FRAMES, ratios):
        assert ratio > content_floor, f"{name} 近似空白（ratio={ratio:.4f}）"

    assert 54 + W * H * 3 == 230454


def test_rh770_419_5mhz_resonance_is_untrusted():
    """存档 400-455MHz：最优在 400.0MHz 端，419.5MHz 未见谐振。

    噪声容差全部由数据推出：稳健二阶差分 MAD → σ；判据取 3σ。
    """
    seg = next(s for s in _load("rh770.json")["segments"] if s["start_hz"] == 400_000_000)
    f = np.array(seg["freqs_hz"], dtype=float)[:56]
    g = _s11(seg)[:56]

    step = float(np.median(np.diff(f)))
    assert step == 1e6
    assert f[0] == 400e6 and f[-1] == 455e6

    sigma = 1.4826 * float(np.median(np.abs(np.diff(g, n=2))))
    sigma_db = 20.0 / np.log(10) * sigma / float(g.min())
    tol = 3.0 * sigma_db

    # 全局最优在 400MHz 端（14.96dB，按推导容差钉扎）
    i_best = int(np.argmin(g))
    best_rl = -20 * np.log10(g[i_best])
    assert f[i_best] == 400e6
    assert abs(best_rl - 14.96) <= tol

    # 无显著回落：反向步长不得超过噪声；局部凹陷（相对两邻居弦线）同样不得超过噪声
    assert np.all(np.diff(g) >= -3.0 * sigma)
    for k in range(1, len(g) - 1):
        dip = 0.5 * (g[k - 1] + g[k + 1]) - g[k]
        assert dip <= 3.0 * sigma, f"{f[k]/1e6:.0f}MHz 出现显著凹陷"

    # 418/419 只是平台（10.32dB，彼此差不超过噪声）
    j418 = int(np.argmin(np.abs(f - 418e6)))
    j419 = int(np.argmin(np.abs(f - 419e6)))
    rl418, rl419 = -20 * np.log10(g[j418]), -20 * np.log10(g[j419])
    assert f[j418] == 418e6 and f[j419] == 419e6
    assert abs(rl418 - 10.32) <= tol and abs(rl419 - 10.32) <= tol
    assert abs(rl418 - rl419) <= tol

    # 419.5MHz 不是采样点；1MHz 栅格无法分辨，且比最优差 4.64dB
    assert 419.5e6 not in f
    assert min(abs(f - 419.5e6)) >= 0.5 * step
    assert best_rl - rl419 >= tol
