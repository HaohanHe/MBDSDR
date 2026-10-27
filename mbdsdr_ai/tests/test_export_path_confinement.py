# SPDX-License-Identifier: MIT
"""导出工具路径收拢（缺口分析报告 P1「导出工具路径遍历」销账）。

三个导出写工具（export_spectrum_snapshot / export_peaks_csv /
export_vna_report）原本直接 open(a["path"])，调用方可传 ../../ 或任意
绝对路径覆盖任意文件。现收进允许根：MBDSDR_EXPORT_ROOT / CWD / 系统临时目录。

测试走**工具接口**端到端验证（而非只测内部函数），确保真实防护生效。
"""
import os

import numpy as np
import pytest

from mbdsdr_ai.core.mcp_server import MCPToolSet

F = np.linspace(88e6, 108e6, 64)
DB = -60 + 25 * np.exp(-((F - 98e6) / 2e6) ** 2)
S11 = [[0.2, 0.0]] * len(F)

# 越界目标选**确实存在且可写**的目录（仓库的父目录），否则"被拒"可能只是
# FileNotFoundError 导致的假通过。D:/mbdsdr 既不在 CWD 下、也不在临时目录下。
OUTSIDE_DIR = os.path.dirname(os.getcwd())
OUTSIDE = os.path.join(OUTSIDE_DIR, "mbdsdr_traversal_guard_evil.png")
# 经典 ../ 穿越：从 CWD 一路往上跳出仓库
ESCAPE = os.path.join(os.getcwd(), "..", "..", "..", "evil_escape.png")


def test_guard_target_really_writable():
    """前置断言：越界目标目录确实存在且可写。

    这条是防"假通过"的关键 —— 若目录不存在，拒绝就可能是文件系统报错
    而非收拢生效。目录可写 ⇒ 拒绝必然来自路径收拢。
    """
    assert os.path.isdir(OUTSIDE_DIR), f"{OUTSIDE_DIR} 应为存在的目录"
    assert os.access(OUTSIDE_DIR, os.W_OK), f"{OUTSIDE_DIR} 应可写"


def _cleanup():
    for p in (OUTSIDE, ESCAPE):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def _spectrum(path, **extra):
    ts = MCPToolSet()
    args = {"freqs_hz": F.tolist(), "db": DB.tolist(),
            "path": path, "arm_manual": True}
    args.update(extra)
    return ts.call_tool("export_spectrum_snapshot", args)


def test_absolute_outside_root_rejected():
    """越界绝对路径必须被拒：不 ok、报错且点明"越界"、文件没被写出来。

    断言错误文案含"越界"，用以证明拒绝来自路径收拢，
    而不是碰巧的文件系统错误（目录已在前置断言中确认为可写）。
    """
    _cleanup()
    try:
        out = _spectrum(OUTSIDE)
        _cleanup_assert(out, OUTSIDE)
    finally:
        _cleanup()


def _cleanup_assert(out, target):
    """断言：拒绝来自路径收拢，且报的是**用户级**参数错误码。

    关键：不能是 internal_error —— 路径写错是用户可修正的参数问题，
    谎报成内部崩溃违反仓库的诚实错误消息口径（phase68-70 收敛战役）。
    """
    assert out.get("ok") is not True
    assert out.get("error") == "export_path_out_of_root", \
        f"应报 export_path_out_of_root，实际: {out}"
    assert "越界" in str(out), f"拒绝原因应来自路径收拢，实际: {out}"
    assert not os.path.exists(target)


def test_dotdot_traversal_rejected():
    """../ 穿越必须被拒（路径遍历最典型的形态）。"""
    _cleanup()
    try:
        out = _spectrum(ESCAPE)
        _cleanup_assert(out, ESCAPE)
    finally:
        _cleanup()


def test_tmp_path_still_allowed(tmp_path):
    """正向对照：系统临时目录必须仍然放行（既有用例依赖它）。"""
    p = tmp_path / "snap.png"
    out = _spectrum(str(p))
    assert out.get("ok") is True, f"临时目录应放行，实际: {out}"
    assert p.exists()


@pytest.mark.parametrize("tool,args", [
    ("export_peaks_csv", {"peaks": [[98e6, -40.0, "fm"]]}),
    ("export_vna_report", {"s11": S11}),
])
def test_other_exports_also_confined(tool, args):
    """另两个导出工具同样受收拢保护。"""
    ts = MCPToolSet()
    payload = {"freqs_hz": F.tolist(), "path": OUTSIDE,
               "arm_manual": True}
    payload.update(args)
    out = ts.call_tool(tool, payload)
    assert out.get("ok") is not True
    assert "error" in out
    assert not os.path.exists(OUTSIDE)
