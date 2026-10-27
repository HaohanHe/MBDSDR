# SPDX-License-Identifier: MIT
"""orbit_sky_plot / orbit_error_plot 工具面测试：注册 + 人工门 + 真实落盘 + 诚实空态。

matplotlib 属可选依赖：缺失时整文件诚实跳过（与仓内"无 env 即诚实 skip"
口径一致），绝不伪造 PNG。
"""
import os
import sys

import pytest

pytest.importorskip("matplotlib",
                    reason="orbit 出图工具需要 matplotlib（可选依赖）")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402


@pytest.fixture(scope="module")
def reg():
    return MBDSDRAgent().tool_registry


def test_orbit_plot_tools_registered(reg):
    names = set(reg.get_tool_names())
    assert "orbit_sky_plot" in names
    assert "orbit_error_plot" in names


def test_gate_blocks_unarmed_sky_plot(reg, tmp_path):
    target = tmp_path / "sky.png"
    # 1) 缺 arm_manual：registry 层 required 校验先拦。
    r = reg.call("orbit_sky_plot", {"az_deg": [10, 20], "el_deg": [30, 40],
                                    "save_path": str(target)})
    assert r.success is False
    assert not target.exists(), "未武装时不得落盘"
    # 2) 显式 arm_manual=False：到达 handler 人工门，返回 gated 信封。
    r2 = reg.call("orbit_sky_plot", {"az_deg": [10, 20], "el_deg": [30, 40],
                                     "save_path": str(target),
                                     "arm_manual": False})
    assert r2.success is False
    assert (r2.data or {}).get("gated") is True, r2.content[:120]
    assert not target.exists(), "未武装时不得落盘"


def test_sky_plot_writes_real_png(reg, tmp_path):
    target = tmp_path / "sky.png"
    az = [10.0 * i for i in range(30)]
    el = [5.0 + 0.5 * i for i in range(30)]
    r = reg.call("orbit_sky_plot", {"az_deg": az, "el_deg": el,
                                    "save_path": str(target),
                                    "arm_manual": True})
    assert r.success is True, r.content[:160]
    assert os.path.isfile(str(target)), f"PNG 必须真实落盘: {r.data}"
    assert os.path.getsize(str(target)) > 0, "PNG 不得为空文件"
    assert r.data.get("n_points") == 30
    assert r.data.get("bytes", 0) == os.path.getsize(str(target))


def test_sky_plot_empty_observation_is_honest(reg, tmp_path):
    target = tmp_path / "sky.png"
    r = reg.call("orbit_sky_plot", {"az_deg": [], "el_deg": [],
                                    "save_path": str(target),
                                    "arm_manual": True})
    assert r.success is False
    assert r.content, "空观测必须显式拒绝并说明（诚实空态）"
    assert not target.exists()


def test_gate_blocks_unarmed_error_plot(reg, tmp_path):
    target = tmp_path / "conv.png"
    seq = [1.0] * 10
    r = reg.call("orbit_error_plot", {"t": seq, "pos_err_rls_km": seq,
                                       "pos_err_ekf_km": seq,
                                       "residuals_km_s": seq,
                                       "save_path": str(target)})
    assert r.success is False
    assert not target.exists(), "未武装时不得落盘"


def test_error_plot_writes_real_png(reg, tmp_path):
    target = tmp_path / "conv.png"
    n = 20
    t = [float(i) for i in range(n)]
    rls = [10.0 / (1 + i) for i in range(n)]
    ekf = [8.0 / (1 + i) for i in range(n)]
    res = [0.001 * ((-1) ** i) for i in range(n)]
    r = reg.call("orbit_error_plot", {"t": t, "pos_err_rls_km": rls,
                                       "pos_err_ekf_km": ekf,
                                       "residuals_km_s": res,
                                       "save_path": str(target),
                                       "arm_manual": True})
    assert r.success is True, r.content[:160]
    assert os.path.isfile(str(target)), f"PNG 必须真实落盘: {r.data}"
    assert os.path.getsize(str(target)) > 0
    assert r.data.get("n_points") == n


def test_error_plot_length_mismatch_is_honest(reg, tmp_path):
    target = tmp_path / "conv.png"
    r = reg.call("orbit_error_plot", {"t": [1.0] * 5,
                                       "pos_err_rls_km": [1.0] * 4,
                                       "pos_err_ekf_km": [1.0] * 5,
                                       "residuals_km_s": [1.0] * 5,
                                       "save_path": str(target),
                                       "arm_manual": True})
    assert r.success is False
    assert "长度不一致" in r.content, r.content[:160]
    assert not target.exists()
