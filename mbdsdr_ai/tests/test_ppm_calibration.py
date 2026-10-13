# SPDX-License-Identifier: MIT
"""ppm 校准：纯函数钉扎 + 两个工具（estimate_ppm_offset / set_ppm_correction）。

钉点：
- +25ppm 已知值（435.000MHz 参考 / 435.010875MHz 实测）与负 ppm；
- clamp 边界（±limit 内不钳、超出钳位且第二元素 True）；
- expected<=0 / 非有限参考 → 诚实 ValueError（无参考不校准）；
- 两个工具 handle 直调：无设备 → 诚实错误信封（绝不 fake success）；
- 写工具 arm_manual 门：未显式武装 → gated 信封；
- 新工具归入既有域（sdr_analysis / sdr_frequency），不开新域。
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from mbdsdr_ai.ppm_calibration import DEFAULT_PPM_LIMIT, clamp_ppm, estimate_ppm

# 既有域集合（sdr_tools.register_sdr_tools 已用的 category）——新工具不得新开域。
_EXISTING_CATEGORIES = {
    "planning", "sdr_device", "sdr_frequency", "sdr_gain", "sdr_demod",
    "sdr_spectrum", "sdr_record", "sdr_analysis", "sdr_ai", "sdr_decode",
    "sdr_satellite", "satellite", "new_spacetime", "sdr_network",
    "radio_control", "gimbal", "hal", "digital_modes", "gnss_monitor",
    "sdr_location", "general", "mcp",
}


def _register_agent():
    """真实注册表 + 无硬件后端管理器（无设备 = 诚实空态，不 mock 硬件）。"""
    from mbdsdr_ai.sdr_backend import SDRBackendManager
    from mbdsdr_ai.sdr_tools import register_sdr_tools
    from mbdsdr_ai.tool_registry import ToolRegistry

    class _Agent:
        pass

    ag = _Agent()
    ag.tool_registry = ToolRegistry()
    ag.sdr_manager = SDRBackendManager()
    register_sdr_tools(ag)
    return ag


@pytest.fixture(scope="module")
def agent():
    return _register_agent()


def _handler(agent, name):
    return agent.tool_registry.tools[name]["handler"]


# ---------------------------------------------------------------------------
# estimate_ppm：已知值钉扎 + 诚实边界
# ---------------------------------------------------------------------------
class TestEstimatePpm:
    def test_positive_25ppm_pinned(self):
        # 435MHz 参考 +25ppm = +10875Hz（廉价棒 20-50ppm 的典型量级）
        assert estimate_ppm(435_000_000.0, 435_010_875.0) == pytest.approx(25.0)

    def test_negative_ppm(self):
        # 镜像场景：读数偏低 → 负 ppm
        assert estimate_ppm(435_000_000.0, 434_989_125.0) == pytest.approx(-25.0)

    def test_zero_offset(self):
        assert estimate_ppm(100_000_000.0, 100_000_000.0) == pytest.approx(0.0)

    @pytest.mark.parametrize("bad_expected", [0.0, -435_000_000.0, -1e-9])
    def test_nonpositive_expected_raises(self, bad_expected):
        # 没有已知参考就无从校准：诚实报错而不是返回 0.0 冒充"已校准"
        with pytest.raises(ValueError):
            estimate_ppm(bad_expected, 435_000_000.0)

    def test_nonfinite_expected_raises(self):
        with pytest.raises(ValueError):
            estimate_ppm(float("nan"), 435_000_000.0)
        with pytest.raises(ValueError):
            estimate_ppm(float("inf"), 435_000_000.0)


# ---------------------------------------------------------------------------
# clamp_ppm：边界 + 钳位标志（诚实告知"这个值可疑"）
# ---------------------------------------------------------------------------
class TestClampPpm:
    def test_default_limit_constant(self):
        assert DEFAULT_PPM_LIMIT == 200.0
        assert clamp_ppm(25.0)[1] is False  # 默认 limit 生效

    def test_within_limit_not_clamped(self):
        assert clamp_ppm(25.0) == (25.0, False)
        assert clamp_ppm(-25.0) == (-25.0, False)
        assert clamp_ppm(0.0) == (0.0, False)

    def test_boundary_not_clamped(self):
        assert clamp_ppm(200.0) == (200.0, False)
        assert clamp_ppm(-200.0) == (-200.0, False)

    def test_beyond_limit_clamped_and_flagged(self):
        # 超出仍按 limit 应用（不拒收），但第二元素 True 告知可疑
        assert clamp_ppm(437.5) == (200.0, True)
        assert clamp_ppm(-300.0) == (-200.0, True)

    def test_custom_limit(self):
        assert clamp_ppm(150.0, limit=100.0) == (100.0, True)
        assert clamp_ppm(99.9, limit=100.0) == (99.9, False)


# ---------------------------------------------------------------------------
# 读工具 estimate_ppm_offset：handle 直调
# ---------------------------------------------------------------------------
class TestEstimatePpmOffsetTool:
    def test_known_value_success(self, agent):
        r = _handler(agent, "estimate_ppm_offset")(
            {"expected_hz": 435_000_000, "measured_hz": 435_010_875})
        assert r.success is True
        assert r.data["ppm_raw"] == pytest.approx(25.0)
        assert r.data["ppm_clamped"] == pytest.approx(25.0)
        assert r.data["clamped"] is False
        # kalibrate 约定：建议下发值 = 原始误差取负
        assert r.data["suggested_correction_ppm"] == pytest.approx(-25.0)
        assert "+25.0000 ppm" in r.content

    def test_clamped_flag_reported_in_content(self, agent):
        # +437.5ppm 远超合理范围：钳位应用 + 内容里如实标"可疑"
        r = _handler(agent, "estimate_ppm_offset")(
            {"expected_hz": 435_000_000, "measured_hz": 435_000_000 * (1 + 437.5e-6)})
        assert r.success is True
        assert r.data["clamped"] is True
        assert r.data["ppm_clamped"] == pytest.approx(DEFAULT_PPM_LIMIT)
        assert "可疑" in r.content

    def test_nonpositive_expected_honest_error(self, agent):
        # 无参考不可校准：success=False 的诚实信封，绝非 ppm=0
        r = _handler(agent, "estimate_ppm_offset")(
            {"expected_hz": 0, "measured_hz": 435_010_875})
        assert r.success is False
        assert "expected_hz" in r.content

    def test_missing_params_honest_error(self, agent):
        r = _handler(agent, "estimate_ppm_offset")({})
        assert r.success is False

    def test_registered_in_existing_domain(self, agent):
        cat = agent.tool_registry.tools["estimate_ppm_offset"]["category"]
        assert cat == "sdr_analysis"
        assert cat in _EXISTING_CATEGORIES  # 不开新域


# ---------------------------------------------------------------------------
# 写工具 set_ppm_correction：arm_manual 门 / 无设备诚实错误 / 分发路径
# ---------------------------------------------------------------------------
class _StubAdapter:
    """RtlTcpDeviceAdapter 风格句柄：真实 freq_correction 属性 setter（不碰 Socket）。"""

    def __init__(self):
        self._freq_correction = 0

    @property
    def freq_correction(self):
        return self._freq_correction

    @freq_correction.setter
    def freq_correction(self, value):
        self._freq_correction = int(value)


class _StubBackend:
    """假后端替身：只记录调用，不假装硬件。"""

    def __init__(self, device_type="stub_radio", with_set_ppm=False, adapter=None):
        self.device = SimpleNamespace(device_type=device_type,
                                      name=f"{device_type} (stub)")
        self.calls = []
        if with_set_ppm:
            self.set_ppm = lambda ppm: (self.calls.append(ppm), True)[1]
        if adapter is not None:
            self._sdr = adapter


def _with_active_backend(agent, backend):
    agent.sdr_manager.active_backend = backend
    return _handler(agent, "set_ppm_correction")


class TestSetPpmCorrectionTool:
    def test_write_tool_gated_without_arm(self, agent):
        # 未显式武装：gated 信封（与 core/mcp_server 写工具门同语义）
        r = _handler(agent, "set_ppm_correction")({"ppm": 25.0})
        assert r.success is False
        assert r.data.get("gated") is True
        assert "手动模式" in r.content

    def test_no_device_honest_error(self, agent):
        # armed 但无设备：诚实错误信封，绝不 fake success
        agent.sdr_manager.active_backend = None
        r = _handler(agent, "set_ppm_correction")({"ppm": 25.0, "arm_manual": True})
        assert r.success is False
        assert "无连接设备" in r.content
        assert r.data.get("gated") is None

    def test_invalid_ppm_value(self, agent):
        agent.sdr_manager.active_backend = None
        r = _handler(agent, "set_ppm_correction")(
            {"ppm": "abc", "arm_manual": True})
        assert r.success is False
        assert "无效" in r.content

    def test_dispatch_via_backend_set_ppm(self, agent):
        backend = _StubBackend(device_type="rtl_sdr", with_set_ppm=True)
        r = _with_active_backend(agent, backend)(
            {"ppm": -25.0, "arm_manual": True})
        assert r.success is True
        assert backend.calls == [-25]
        assert r.data["ppm_applied"] == -25.0
        assert r.data["clamped"] is False

    def test_dispatch_via_rtltcp_adapter_setter(self, agent):
        # 后端无 set_ppm：走 _sdr 的 freq_correction 属性 setter（rtl_tcp 0x05）
        adapter = _StubAdapter()
        backend = _StubBackend(device_type="rtl_tcp", adapter=adapter)
        r = _with_active_backend(agent, backend)(
            {"ppm": -18.5, "arm_manual": True})
        assert r.success is True
        assert adapter.freq_correction == -18
        assert "freq_correction" in r.content

    def test_clamped_value_applied_but_flagged(self, agent):
        backend = _StubBackend(device_type="rtl_sdr", with_set_ppm=True)
        r = _with_active_backend(agent, backend)(
            {"ppm": 437.5, "arm_manual": True})
        assert r.success is True
        assert backend.calls == [200]          # 仍按 limit 钳位应用
        assert r.data["clamped"] is True
        assert "可疑" in r.content              # 但如实告知

    def test_backend_without_entry_honest_error(self, agent):
        # 无射频前端的后端（如回放源）：诚实报"不支持在线 ppm 校正"，不假装成功
        backend = _StubBackend(device_type="file_iq")
        r = _with_active_backend(agent, backend)(
            {"ppm": 25.0, "arm_manual": True})
        assert r.success is False
        assert "不支持在线 ppm 校正" in r.content
        assert "连接时传 ppm" in r.content

    def test_disconnected_backend_saved_not_faked(self, agent):
        # 后端未连接：如实标注"仅保存、reconnect 再发"，不声称已作用到硬件
        backend = _StubBackend(device_type="rtl_sdr", with_set_ppm=True)
        backend.status = SimpleNamespace(connected=False)
        r = _with_active_backend(agent, backend)(
            {"ppm": 25.0, "arm_manual": True})
        assert r.success is True
        assert "已保存" in r.content
        assert "reconnect" in r.content

    def test_connected_backend_no_save_note(self, agent):
        backend = _StubBackend(device_type="rtl_sdr", with_set_ppm=True)
        backend.status = SimpleNamespace(connected=True)
        r = _with_active_backend(agent, backend)(
            {"ppm": 25.0, "arm_manual": True})
        assert r.success is True
        assert "已保存" not in r.content

    def test_registered_in_existing_domain(self, agent):
        cat = agent.tool_registry.tools["set_ppm_correction"]["category"]
        assert cat == "sdr_frequency"
        assert cat in _EXISTING_CATEGORIES  # 不开新域
