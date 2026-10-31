# SPDX-License-Identifier: MIT
"""P0-4 测向/定位原子工具测试（合成向量口径，容差钉扎）。

合成信号纪律（AGENTS 第 2 条）：全部信号由解析式生成（已知真值的
平面波 / 已知站几何的 TDOA），误差断言对**真值**给容差，不对自身实现
自环。歧义/失败路径显式断言（多峰不报单一方位、<3 站拒绝、残差超门限
拒绝）。
"""
import math

import numpy as np
import pytest

from mbdsdr_ai.df_position import array_correlator_df, multistation_tdoa_fix
from mbdsdr_ai.interferometry import C_M_S

FS = 1e6
FC = 435.4e6


def _plane_wave(positions, bearing_deg, n, snr_db=None, seed=7):
    """生成 K 阵元窄带平面波快拍（方位角自北顺时针口径）。"""
    rng = np.random.default_rng(seed)
    pos = np.asarray(positions, dtype=float)
    th = math.radians(bearing_deg)
    u = np.array([math.sin(th), math.cos(th)])
    t = np.arange(n) / FS
    sig = np.exp(2j * math.pi * 50e3 * t)  # 基带内频偏
    x = []
    for p in pos:
        delay = (p[0] * u[0] + p[1] * u[1]) / C_M_S
        # 接收信号 = 源延迟 τ：基带相位 -2πf·τ（与测向工具导向矢量同约定）
        x.append(np.exp(-2j * math.pi * FC * delay) * sig)
    x = np.asarray(x)
    if snr_db is not None:
        p_sig = np.mean(np.abs(x) ** 2)
        p_noise = p_sig / 10.0 ** (snr_db / 10.0)
        x = x + math.sqrt(p_noise / 2) * (
            rng.standard_normal(x.shape) + 1j * rng.standard_normal(x.shape))
    return x


# ── 阵列相关测向 ──────────────────────────────────────────────
def test_array_df_known_bearing():
    """3×3 方阵（半波长间距），已知方位 → 估计误差 < 1°、无歧义。"""
    d = (C_M_S / FC) / 2.0
    # 线性阵有前后镜像模糊（sinθ 对称）——测向须用 2D 阵消镜像；
    # 阵元数压低旁瓣（2×2 旁瓣高达 -1.3 dB，会触发歧义门限）
    positions = [[i * d, j * d] for i in range(3) for j in range(3)]
    x = _plane_wave(positions, 37.0, 512, snr_db=30)
    res = array_correlator_df(x, FS, FC, positions)
    assert not res["ambiguity"], f"单源方阵不应歧义: {res['peaks'][:3]}"
    assert res["primary_bearing_deg"] == pytest.approx(37.0, abs=1.0)


def test_array_df_ula_mirror_ambiguity():
    """线性阵前后镜像（37° vs 143°）是物理本质——必须显式报歧义，
    不静默挑峰。"""
    d = (C_M_S / FC) / 2.0
    positions = [[i * d, 0.0] for i in range(4)]
    x = _plane_wave(positions, 37.0, 512, snr_db=30)
    res = array_correlator_df(x, FS, FC, positions)
    assert res["ambiguity"]
    assert res["primary_bearing_deg"] is None
    bearings = [p["bearing_deg"] for p in res["peaks"]]
    assert any(abs(b - 37.0) < 2 for b in bearings)
    assert any(abs(b - 143.0) < 2 for b in bearings)


def test_array_df_ambiguity_exposed():
    """双源（同功率）→ 歧义显式暴露，不报单一方位。"""
    d = (C_M_S / FC) / 2.0
    positions = [[i * d, 0.0] for i in range(4)]
    x1 = _plane_wave(positions, 30.0, 512, seed=1)
    x2 = _plane_wave(positions, 150.0, 512, seed=2)
    res = array_correlator_df(x1 + x2, FS, FC, positions)
    assert res["ambiguity"], "双同功率源必须报歧义"
    assert res["primary_bearing_deg"] is None
    bearings = [p["bearing_deg"] for p in res["peaks"]]
    assert any(abs(b - 30.0) < 2 for b in bearings)
    assert any(abs(b - 150.0) < 2 for b in bearings)


def test_array_df_grating_flag():
    """相邻间距 > λ/2 → grating_lobes 标注为 True；= λ/2 则 False。"""
    d = (C_M_S / FC) * 1.2  # 超半波长
    positions = [[i * d, 0.0] for i in range(4)]
    x = _plane_wave(positions, 80.0, 512, snr_db=30)
    res = array_correlator_df(x, FS, FC, positions)
    assert res["grating_lobes"]
    d_half = (C_M_S / FC) / 2.0
    positions = [[i * d_half, 0.0] for i in range(4)]
    x = _plane_wave(positions, 80.0, 512, snr_db=30)
    res = array_correlator_df(x, FS, FC, positions)
    assert not res["grating_lobes"]


def test_array_df_honest_failures():
    """阵元 <2 / 快拍 <8 → 报错；纯噪声 → 不给方位（reason）。"""
    with pytest.raises(ValueError, match="至少需要 2 个阵元"):
        array_correlator_df([np.ones(16, dtype=complex)], FS, FC,
                            [[0.0, 0.0]])
    d = (C_M_S / FC) / 2.0
    positions = [[i * d, 0.0] for i in range(4)]
    with pytest.raises(ValueError, match="快拍太短"):
        array_correlator_df(_plane_wave(positions, 0.0, 4), FS, FC, positions)
    rng = np.random.default_rng(0)
    noise = (rng.standard_normal((4, 512)) + 1j * rng.standard_normal((4, 512)))
    res = array_correlator_df(noise, FS, FC, positions)
    assert res["primary_bearing_deg"] is None
    assert "reason" in res


# ── 多站 TDOA 定位 ───────────────────────────────────────────
def _tdoa_for(emitter, stations, ref=0):
    d = [math.hypot(emitter[0] - s[0], emitter[1] - s[1]) for s in stations]
    return [(d[i] - d[ref]) / C_M_S for i in range(len(stations))]


def test_tdoa_fix_three_stations():
    """3 站三角 + 已知发射源 → 位置误差在 GDOP 放大容差内（<1 m 无噪）。"""
    stations = [[0.0, 0.0], [3000.0, 0.0], [0.0, 4000.0]]
    emitter = [1200.0, 1500.0]
    res = multistation_tdoa_fix(stations, _tdoa_for(emitter, stations))
    assert res["converged"]
    err = math.hypot(res["pos_m"][0] - emitter[0],
                     res["pos_m"][1] - emitter[1])
    assert err < 1.0, f"定位误差 {err:.3f} m 超 1 m 容差"
    assert res["residual_rms_m"] < 1e-3
    assert res["gdop"] > 0


def test_tdoa_fix_four_stations_better_gdop():
    """加第 4 站（四边形）→ GDOP 优于三角，位置仍收敛。"""
    stations = [[0.0, 0.0], [3000.0, 0.0], [3000.0, 3000.0], [0.0, 3000.0]]
    emitter = [1400.0, 1600.0]
    res = multistation_tdoa_fix(stations, _tdoa_for(emitter, stations))
    tri = multistation_tdoa_fix(stations[:3], _tdoa_for(emitter, stations[:3]))
    assert res["gdop"] < tri["gdop"]


def test_tdoa_fix_too_few_stations():
    """<3 站显式拒绝（不降级硬解）。"""
    with pytest.raises(ValueError, match="至少需要 3 站"):
        multistation_tdoa_fix([[0.0, 0.0], [1000.0, 0.0]], [0.0, 1e-6])


def test_tdoa_fix_outlier_rejected():
    """粗差（某站时延差人为偏 500 m）→ 冗余观测下残差超门限拒绝。

    3 站只有 2 条独立观测（2 未知量，恰定方程），残差恒 0 查不出粗差——
    必须用 ≥4 站（有冗余）才能做残差门限剔除，这是双曲线定位的性质。
    """
    stations = [[0.0, 0.0], [3000.0, 0.0], [3000.0, 3000.0], [0.0, 3000.0]]
    emitter = [1200.0, 1500.0]
    tdoa = _tdoa_for(emitter, stations)
    tdoa[1] += 500.0 / C_M_S
    with pytest.raises(ValueError, match="残差"):
        multistation_tdoa_fix(stations, tdoa, max_resid_m=1.0)


def test_tdoa_fix_inconsistent_geometry():
    """时延与站几何完全矛盾（量级错乱）→ 不收敛或奇异，如实报错。"""
    stations = [[0.0, 0.0], [3000.0, 0.0], [0.0, 4000.0]]
    with pytest.raises(ValueError):
        multistation_tdoa_fix(stations, [0.0, 0.02, 0.015], max_iter=25)


# ── 工具注册面 ────────────────────────────────────────────────
def test_tools_registered():
    from mbdsdr_ai.tool_registry import ToolRegistry
    r = ToolRegistry()
    r.register_builtin_tools()
    names = {t["name"] for t in r.list_tools()}
    for want in ("array_correlator_df", "multistation_tdoa_fix"):
        assert want in names, f"工具 {want} 未注册"


def test_tool_handler_end_to_end():
    """handler 级：3 站 TDOA 定位吃进坐标与时延，吐出位置。"""
    import json as _json
    from mbdsdr_ai.tool_registry import ToolRegistry
    r = ToolRegistry()
    r.register_builtin_tools()
    stations = [[0.0, 0.0], [3000.0, 0.0], [0.0, 4000.0]]
    emitter = [1200.0, 1500.0]
    h = r.tools["multistation_tdoa_fix"]["handler"]
    res = h({"stations_m": stations, "tdoa_s": _tdoa_for(emitter, stations)})
    assert res.success
    assert math.hypot(res.data["pos_m"][0] - emitter[0],
                      res.data["pos_m"][1] - emitter[1]) < 1.0
    bad = h({"stations_m": stations[:2], "tdoa_s": [0.0, 1e-6]})
    assert not bad.success
