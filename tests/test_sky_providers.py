# SPDX-License-Identifier: MIT
"""provider 注册表的离线契约（Task 94）。

不联网、不碰硬件、不启动线程：
1. 多个 provider 的读数都被收集，且可直接喂 ``fuse_poses``；
2. 单个 provider 抛异常只影响它自己（其余读数照常返回）；
3. 空注册表返回空结果；注销后不再调用；
4. 注册/读取的参数错误当场拒绝，不静默吞掉。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sky_pointing import PoseObservation, fuse_poses  # noqa: E402
from mbdsdr_ai.sky_providers import ProviderReading, ProviderRegistry  # noqa: E402

NOW = 1_700_000_000.0


def _reading(source, az=10.0, alt=20.0, accuracy=2.0, t=NOW, fov=8.0):
    return ProviderReading(source=source, az_deg=az, alt_deg=alt,
                           accuracy_deg=accuracy, t_utc=t, fov_deg=fov)


def test_two_providers_are_collected_and_feed_fuse_poses():
    reg = ProviderRegistry()
    reg.register("imu", lambda: _reading("imu", az=33.0, alt=52.0))
    reg.register("phone", lambda: _reading("phone", az=33.4, alt=52.2,
                                           accuracy=1.0))

    out = reg.read_all(now_utc=NOW)
    assert set(out["readings"]) == {"imu", "phone"}
    assert out["errors"] == {}
    assert all(isinstance(r, PoseObservation) for r in out["readings"].values())

    fused = fuse_poses(list(out["readings"].values()), NOW)
    assert fused["valid"] is True
    assert fused["level"] == "fused"
    assert set(fused["sources"]) == {"imu", "phone"}
    assert fused["az_deg"] == pytest.approx(33.28, abs=0.1)


def test_one_provider_failure_does_not_block_others():
    def broken():
        raise RuntimeError("传感器掉线")

    reg = ProviderRegistry()
    reg.register("imu", lambda: _reading("imu"))
    reg.register("broken", broken)
    reg.register("camera", lambda: _reading("camera", az=11.0))

    out = reg.read_all(now_utc=NOW)
    assert set(out["readings"]) == {"imu", "camera"}
    assert set(out["errors"]) == {"broken"}
    assert out["errors"]["broken"]["type"] == "RuntimeError"
    assert "传感器掉线" in out["errors"]["broken"]["message"]


def test_empty_registry_returns_empty_result():
    out = ProviderRegistry().read_all(now_utc=NOW)
    assert out == {"readings": {}, "errors": {}}


def test_unregistered_provider_is_no_longer_called():
    calls = []

    def provider():
        calls.append(1)
        return _reading("imu")

    reg = ProviderRegistry()
    reg.register("imu", provider)
    assert reg.names() == ("imu",)
    reg.read_all(now_utc=NOW)
    assert calls == [1]

    assert reg.unregister("imu") is True
    assert reg.names() == ()
    out = reg.read_all(now_utc=NOW)
    assert out["readings"] == {} and out["errors"] == {}
    assert calls == [1], "注销后不得再调用 provider"

    assert reg.unregister("imu") is False


def test_object_with_read_method_and_mapping_reading():
    class Phone:
        def read(self):
            return {"az_deg": 100.0, "alt_deg": 30.0, "accuracy_deg": 3.0}

    reg = ProviderRegistry()
    reg.register("phone", Phone())
    out = reg.read_all(now_utc=NOW)
    assert out["errors"] == {}
    obs = out["readings"]["phone"]
    # 读数没给 source/t_utc：source 用注册名，时间用 now_utc 登记。
    assert obs.source == "phone"
    assert obs.t_utc == pytest.approx(NOW)
    assert obs.fov_deg is None


def test_reading_without_time_is_an_error_not_a_guess():
    reg = ProviderRegistry()
    reg.register("imu", lambda: _reading("imu", t=None))

    out = reg.read_all()
    assert out["readings"] == {}
    assert out["errors"]["imu"]["type"] == "ValueError"
    assert "t_utc" in out["errors"]["imu"]["message"]

    # 提供 now_utc 时才算「登记读取时刻」，读数可参与融合。
    out = reg.read_all(now_utc=NOW)
    assert out["errors"] == {}
    assert out["readings"]["imu"].t_utc == pytest.approx(NOW)


def test_register_rejects_bad_names_and_providers():
    reg = ProviderRegistry()
    with pytest.raises(ValueError):
        reg.register("", lambda: _reading("x"))
    with pytest.raises(TypeError):
        reg.register("bad", object())
    reg.register("imu", lambda: _reading("imu"))
    with pytest.raises(ValueError):
        reg.register("imu", lambda: _reading("imu2"))


def test_bad_return_type_is_isolated_as_error():
    reg = ProviderRegistry()
    reg.register("imu", lambda: _reading("imu"))
    reg.register("weird", lambda: ["not a reading"])

    out = reg.read_all(now_utc=NOW)
    assert set(out["readings"]) == {"imu"}
    assert out["errors"]["weird"]["type"] == "TypeError"
    assert "ProviderReading" in out["errors"]["weird"]["message"]


def test_invalid_now_utc_is_rejected():
    reg = ProviderRegistry()
    for bad in (float("nan"), float("inf"), "nope"):
        with pytest.raises(ValueError):
            reg.read_all(now_utc=bad)


def test_provider_reading_to_observation_keeps_all_fields():
    reading = _reading("imu", az=359.0, alt=-5.0, accuracy=1.5,
                       t=NOW - 0.25, fov=2.3)
    obs = reading.to_observation()
    assert (obs.source, obs.az_deg, obs.alt_deg, obs.accuracy_deg,
            obs.t_utc, obs.fov_deg) == ("imu", 359.0, -5.0, 1.5,
                                        NOW - 0.25, 2.3)
