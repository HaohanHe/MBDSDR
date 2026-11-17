# SPDX-License-Identifier: MIT
"""按视图覆盖 `min_fov` 的契约（窄视场投影，D7）。

守四件事：

1. 目镜级视图（min_fov=0.5）的 2.3° 视场**保留 2.3°**，不再被 30° 夹掉；
2. 不传 `min_fov` 的既有视图仍以 30° 为下限，默认行为逐字不变；
3. `ViewState.copy()` 保留覆盖口径（goto/动画/快照不会悄悄退回 30°）；
4. 非法 `min_fov`（越界/非有限/大于 max_fov）当场拒绝，不静默改口径。

`view_params` 在传/不传 `min_fov` 两种情况下分别按目标视图口径夹取，
`fov_requested_deg` / `fov_clamped` 始终可见。
"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sky_interaction import (  # noqa: E402
    DEFAULT_MIN_FOV_DEG,
    ViewState,
)
from mbdsdr_ai.sky_pointing import (  # noqa: E402
    PoseObservation,
    fuse_poses,
    view_params,
)

NOW = 1_700_000_000.0


def _pose(fov=None):
    obs = PoseObservation(source="imu", t_utc=NOW - 0.5,
                          az_deg=123.4, alt_deg=45.6, accuracy_deg=1.0,
                          roll_deg=7.5, fov_deg=fov)
    return fuse_poses([obs], NOW)


# ---------------------------------------------------------------------------
# ViewState
# ---------------------------------------------------------------------------
def test_narrow_view_keeps_2_3_degrees():
    view = ViewState(fov_deg=2.3, min_fov=0.5)
    assert view.fov_deg == pytest.approx(2.3)
    assert view.min_fov == pytest.approx(0.5)


def test_default_view_still_clamps_to_30():
    assert DEFAULT_MIN_FOV_DEG == pytest.approx(30.0)
    assert ViewState().min_fov == pytest.approx(30.0)
    assert ViewState(fov_deg=2.3).fov_deg == pytest.approx(30.0)


def test_copy_keeps_min_fov_override():
    view = ViewState(fov_deg=2.3, min_fov=0.5)
    snapshot = view.copy()
    assert snapshot.min_fov == pytest.approx(0.5)
    assert snapshot.fov_deg == pytest.approx(2.3)
    # 快照自己的缩放仍按 0.5° 下限，而不是退回 30°。
    snapshot.zoom_to(0.1)
    assert snapshot.fov_deg == pytest.approx(0.5)
    # 原视图不被快照改动污染。
    assert view.fov_deg == pytest.approx(2.3)


@pytest.mark.parametrize("bad", [0.0, 0.4, -1.0, 181.0, float("nan"),
                                 float("inf"), "abc", None])
def test_invalid_min_fov_is_rejected(bad):
    with pytest.raises(ValueError, match="min_fov"):
        ViewState(fov_deg=2.3, min_fov=bad)


def test_min_fov_above_max_fov_is_rejected():
    with pytest.raises(ValueError, match="max_fov"):
        ViewState(fov_deg=2.3, min_fov=90.0, max_fov=60.0)


# ---------------------------------------------------------------------------
# view_params
# ---------------------------------------------------------------------------
def test_view_params_defaults_to_30_degree_floor():
    params = view_params(_pose(), fov_deg=2.3)
    assert params["valid"] is True
    assert params["fov_requested_deg"] == pytest.approx(2.3)
    assert params["fov_deg"] == pytest.approx(30.0)
    assert params["fov_clamped"] is True


def test_view_params_honours_target_view_min_fov():
    params = view_params(_pose(), fov_deg=2.3, min_fov=0.5)
    assert params["valid"] is True
    assert params["fov_requested_deg"] == pytest.approx(2.3)
    assert params["fov_deg"] == pytest.approx(2.3)
    assert params["fov_clamped"] is False
    # 未传视场时仍从位姿取；覆盖口径只影响夹取。
    pose_with_fov = _pose(fov=8.0)
    params2 = view_params(pose_with_fov, min_fov=0.5)
    assert params2["fov_deg"] == pytest.approx(8.0)
    assert params2["fov_clamped"] is False


def test_view_params_rejects_invalid_min_fov():
    for bad in (0.0, 0.4, 181.0, float("nan"), "abc"):
        out = view_params(_pose(), fov_deg=2.3, min_fov=bad)
        assert out["valid"] is False
        assert "min_fov" in out["reason"]


def test_view_params_never_returns_nan_fov():
    assert math.isfinite(view_params(_pose(), fov_deg=2.3,
                                     min_fov=0.5)["fov_deg"])
