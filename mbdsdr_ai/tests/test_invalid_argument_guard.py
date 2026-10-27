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
