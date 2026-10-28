# SPDX-License-Identifier: MIT
"""入参有限性校验（P1「Python schema 无边界」部分销账）。

_NUM / _INT / _ARR 只声明 type、无数值约束，NaN/±Inf 可静默污染整条 DSP 链。
现在进 handler 之前统一拦下，报 invalid_argument（用户可修正的参数错误），
且**绝不静默替换成 0** —— 那才是造假。
"""
import numpy as np
import pytest

from mbdsdr_ai.core.mcp_server import MCPToolSet

F = np.linspace(88e6, 108e6, 32)
DB = -60 + 25 * np.exp(-((F - 98e6) / 2e6) ** 2)


def _call(tmp_path, freqs, db, **extra):
    ts = MCPToolSet()
    args = {"freqs_hz": freqs, "db": db,
            "path": str(tmp_path / "s.png"), "arm_manual": True}
    args.update(extra)
    return ts.call_tool("export_spectrum_snapshot", args)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_argument_rejected(tmp_path, bad):
    """NaN / ±Inf 一律拦下，报 invalid_argument，不是 internal_error。"""
    out = _call(tmp_path, [bad] + F.tolist()[1:], DB.tolist())
    assert out.get("ok") is not True
    assert out.get("error") == "invalid_argument", \
        f"应报 invalid_argument（用户可修正），实际: {out}"


def test_oversized_array_rejected(monkeypatch, tmp_path):
    """超长数组必须拦下（schema 无 maxItems，这里补长度闸）。

    用 monkeypatch 把上限打小，避免真去分配 1e6 元素拖慢测试。
    """
    import mbdsdr_ai.core.mcp_server as ms

    monkeypatch.setattr(ms, "MAX_ARG_ARRAY_LEN", 8)
    ts = ms.MCPToolSet()
    out = ts.call_tool("export_spectrum_snapshot", {
        "freqs_hz": [1e6 * i for i in range(64)],
        "db": [0.0] * 64,
        "path": str(tmp_path / "s.png"), "arm_manual": True})
    assert out.get("ok") is not True
    assert out.get("error") == "invalid_argument", f"实际: {out}"
    assert "过长" in str(out), f"应说明数组过长，实际: {out}"


def test_finite_arguments_still_accepted(tmp_path):
    """正向对照：有限数值必须照常放行，不能误伤正常调用。"""
    out = _call(tmp_path, F.tolist(), DB.tolist())
    assert out.get("ok") is True, f"有限入参应正常通过，实际: {out}"


# ---------------------------------------------------------------------------
# 数值区间上下界（P1「Python schema 无边界」续：NaN/Inf、长度之后的第三闸）
# ---------------------------------------------------------------------------
def test_freq_out_of_range_rejected(tmp_path):
    """负频率必须拦下（物理上不可能）。"""
    out = _call(tmp_path, [-1.0] + F.tolist()[1:], DB.tolist())
    assert out.get("ok") is not True
    assert out.get("error") == "invalid_argument", f"实际: {out}"
    assert "区间" in str(out), f"应说明越界，实际: {out}"


def test_freq_absurdly_high_rejected(tmp_path):
    """超过 10 GHz 量级的频率必须拦下。"""
    out = _call(tmp_path, [50e9] + F.tolist()[1:], DB.tolist())
    assert out.get("ok") is not True
    assert out.get("error") == "invalid_argument"


def test_db_out_of_range_rejected(tmp_path):
    """离谱 dB 值必须拦下。"""
    out = _call(tmp_path, F.tolist(), [9999.0] + DB.tolist()[1:])
    assert out.get("ok") is not True
    assert out.get("error") == "invalid_argument"


def test_range_boundary_values_accepted(tmp_path):
    """区间端点必须放行（边界不是越界）。"""
    import mbdsdr_ai.core.mcp_server as ms
    ms._check_arg_range("db", -300.0, "db")
    ms._check_arg_range("db", 300.0, "db")
    ms._check_arg_range("hz", 0.0, "hz")
    ms._check_arg_range("hz", 10e9, "hz")


def test_no_clamping_happens(tmp_path):
    """越界绝不静默钳位——必须报错（钳位等于替用户改数据）。"""
    import mbdsdr_ai.core.mcp_server as ms
    with pytest.raises(ms.InvalidArgumentError):
        ms._check_arg_range("hz", -1.0, "hz")


def test_unregistered_key_not_range_checked():
    """未注册的键不参与区间校验（不误伤未知参数）。"""
    import mbdsdr_ai.core.mcp_server as ms
    ms._check_arg_range("some_unknown_key", 1e30, "some_unknown_key")  # 不抛
