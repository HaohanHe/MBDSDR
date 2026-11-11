# SPDX-License-Identifier: MIT
"""D-1（W76）两档口径：量程可以先信后验，开关必须验了才信。

裁定原文（2026-11-11）：
  * 读数量程类（频率/增益/采样率/带宽）：允许显示声明值，但必须标
    「声明 · 未验证」；允许用户输入，越界/设置失败如实报错，不许静默、
    不许夹取后当成功。
  * 开关类（偏置电压/AGC/Offset/直采）：``unknown`` 一律置灰 + tooltip
    「未检测到该能力，无法确认」，并给「重新探测」入口；只有 ``probed``
    证实支持才可点。

反证：把 ``switch_capability`` 的 declared 分支改成 ``enabled=bool(flag)``，
``test_declared_switch_stays_grey_until_probed`` 立即变红。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.device_capabilities import (  # noqa: E402
    STATUS_DECLARED,
    STATUS_PROBED,
    STATUS_UNKNOWN,
    SWITCH_KEYS,
    SWITCH_RESCAN_LABEL,
    SWITCH_UNKNOWN_TOOLTIP,
    build_capability_report,
    build_switch_report,
    check_range,
    switch_capability,
)


class TestSwitchTier:
    def test_all_four_switches_covered(self):
        report = build_switch_report({})
        assert set(report) == set(SWITCH_KEYS) == {
            "bias_tee", "agc", "offset_tuning", "direct_sampling"}

    def test_unknown_switch_is_grey_with_exact_tooltip_and_rescan(self):
        # 只有布尔标志、没有来源：不许当成「支持」
        report = build_switch_report({"supports_bias_tee": True})
        bias = report["bias_tee"]
        assert bias.state == STATUS_UNKNOWN
        assert bias.supported is None
        assert bias.enabled is False, "未知开关必须置灰"
        assert bias.tooltip == SWITCH_UNKNOWN_TOOLTIP
        assert "未知" in bias.text and "支持" not in bias.text
        assert bias.can_rescan is True, "未知开关必须给重新探测入口"
        assert SWITCH_RESCAN_LABEL == "重新探测"

    def test_declared_switch_stays_grey_until_probed(self):
        report = build_switch_report({
            "supports_bias_tee": True,
            "capability_status": {"bias_tee": STATUS_DECLARED},
        })
        bias = report["bias_tee"]
        assert bias.state == STATUS_DECLARED
        assert bias.enabled is False, "声明不算数：开关必须验了才信"
        assert "未验证" in bias.text
        assert bias.can_rescan is True
        assert bias.tooltip, "置灰必须有原因，不能只有灰"

    def test_probed_true_switch_is_clickable(self):
        report = build_switch_report({
            "supports_agc_mode": True,
            "capability_status": {"agc": STATUS_PROBED},
        })
        agc = report["agc"]
        assert agc.state == STATUS_PROBED
        assert agc.supported is True and agc.enabled is True
        assert agc.tooltip == "" and agc.can_rescan is False

    def test_probed_false_switch_is_grey_and_says_unsupported(self):
        report = build_switch_report({
            "supports_direct_sampling": False,
            "capability_status": {"direct_sampling": STATUS_PROBED},
        })
        ds = report["direct_sampling"]
        assert ds.supported is False
        assert ds.enabled is False
        assert "不支持" in ds.text and "不支持" in ds.tooltip
        assert ds.can_rescan is False, "已探测到不支持，不需要再探测"

    def test_callable_support_flag_is_not_treated_as_supported(self):
        # 后端把 supports_* 写成方法（如 sdr_backend.supports_offset_tuning）：
        # 方法存在只说明「可以试」，不说明设备支持 → 仍是未知。
        class _Dev:
            def supports_offset_tuning(self):
                return True

        cap = switch_capability(_Dev(), "offset_tuning")
        assert cap.state == STATUS_UNKNOWN and cap.enabled is False


class TestRangeTier:
    def test_declared_range_visible_marked_unverified(self):
        report = build_capability_report({
            "freq_range": (500_000, 1_766_000_000),
            "capability_status": {"freq_range": STATUS_DECLARED},
        })
        field = report["freq_range"]
        assert field.known is True
        assert "1.77 GHz" in field.text
        assert "未验证" in field.text, "声明量程必须标未验证"
        # 量程可以先信后验：允许按声明范围校验（用户可输入）
        ok, msg = check_range(report, "freq_range", 100_000_000)
        assert ok is True and msg == ""

    def test_range_out_of_bounds_reports_without_clamping(self):
        report = build_capability_report({
            "gain_range": (0.0, 40.0),
            "capability_status": {"gain_range": STATUS_DECLARED},
        })
        ok, msg = check_range(report, "gain_range", 99.0)
        assert ok is False, "越界必须如实报错，不许夹取后当成功"
        assert "超出" in msg and "40" in msg

    def test_unknown_range_cannot_be_validated_as_pass(self):
        report = build_capability_report({"gain_range": (0.0, 40.0)})
        ok, msg = check_range(report, "gain_range", 10.0)
        assert ok is None, "范围未知时不得冒充校验通过"
        assert "未知" in msg
