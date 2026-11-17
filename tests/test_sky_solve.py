# SPDX-License-Identifier: MIT
"""星点解算适配契约测试：缺库如实不可用，输入不足/非法先于依赖判定。

这些测试不安装、不调用任何解算库，只验证：
* 无 tetra3 时 ``solver_status`` 的不可用原因；
* 星点少于下限时给出「星点不足」而不是「解算失败」；
* NaN 坐标、坏形状、非正图像尺寸、越界 FOV 一律拒绝。
"""

import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sky_solve import (  # noqa: E402
    MIN_SOLVE_STARS,
    solve_frame,
    solver_status,
)

_IMAGE = (1920, 1080)
_FOV = 8.0


def _stars(n=12):
    return [(100.0 + 10.0 * i, 200.0 + 7.0 * i) for i in range(n)]


def test_solver_status_is_honest_without_tetra3():
    if importlib.util.find_spec("tetra3") is not None:
        pytest.skip("环境已安装 tetra3，本用例只验证缺失分支")
    status = solver_status()
    assert status == {"available": False,
                      "reason": "未安装 tetra3；ASTAP 需外部可执行文件"}


def test_solve_frame_rejects_too_few_stars():
    out = solve_frame(_stars(MIN_SOLVE_STARS - 1), _IMAGE, _FOV)
    assert out["ok"] is False
    assert out["reason"].startswith("星点不足")
    assert str(MIN_SOLVE_STARS) in out["reason"]


def test_solve_frame_rejects_invalid_star_coordinates():
    stars = _stars(MIN_SOLVE_STARS)
    stars[0] = (float("nan"), 100.0)
    out = solve_frame(stars, _IMAGE, _FOV)
    assert out["ok"] is False
    assert "输入非法" in out["reason"]
    assert "非有限" in out["reason"]


def test_solve_frame_rejects_malformed_star_entries():
    out = solve_frame([(1.0,) for _ in range(MIN_SOLVE_STARS)], _IMAGE, _FOV)
    assert out["ok"] is False
    assert "输入非法" in out["reason"]

    out2 = solve_frame("not stars", _IMAGE, _FOV)
    assert out2["ok"] is False
    assert "输入非法" in out2["reason"]


def test_solve_frame_rejects_bad_image_size_and_fov():
    assert solve_frame(_stars(), (0, 1080), _FOV)["ok"] is False
    assert solve_frame(_stars(), (1920, -1), _FOV)["ok"] is False
    assert solve_frame(_stars(), (1920,), _FOV)["ok"] is False
    assert solve_frame(_stars(), None, _FOV)["ok"] is False

    out = solve_frame(_stars(), _IMAGE, 0.0)
    assert out["ok"] is False
    assert "fov_deg" in out["reason"]

    out2 = solve_frame(_stars(), _IMAGE, float("inf"))
    assert out2["ok"] is False
    assert "fov_deg" in out2["reason"]


def test_solve_frame_valid_input_reports_unavailable_or_unimplemented():
    out = solve_frame(_stars(MIN_SOLVE_STARS), _IMAGE, _FOV)
    assert out["ok"] is False
    # 无 tetra3：如实不可用；有 tetra3：本期只交付契约，也不许伪造结果。
    assert ("tetra3" in out["reason"]) or ("契约" in out["reason"])
