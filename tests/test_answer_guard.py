# SPDX-License-Identifier: MIT
"""回答侧诚实护栏：unknown 槽位不得被说成「未连接/未检测到」。

真机证据（2026-11-22，Qwen3-VL-8B-Thinking）：能力账本里 IMU/GNSS 为
``unknown``，模型仍回答"未检测到 IMU/GNSS"，把「没探测」误报成「没有硬件」。
本文件钉住窄改写规则：只改 unknown 槽位的硬件断言；missing 保留原话；
信号/定位结果/卫星数量这类非硬件对象不动。
"""
from __future__ import annotations

import pytest

from mbdsdr_ai.answer_guard import (
    StreamGuard,
    sanitize_answer,
    sanitize_numbered_steps,
    sanitize_unknown_claims,
    sanitize_unverified_ui_claims,
    sanitize_unverified_ui_positions,
    wants_numbered_steps,
)


class TestSanitizeUnknown:
    @pytest.mark.parametrize("text,want", [
        ("未检测到IMU姿态传感器，无法自动校准。", "IMU姿态传感器尚未确认，无法自动校准。"),
        ("IMU未连接，请手动对齐。", "IMU尚未确认，请手动对齐。"),
        ("当前GNSS未连接。", "当前GNSS尚未确认。"),
        ("未检测到 GNSS 定位模块。", "GNSS 定位模块尚未确认。"),
        ("未检测到SDR设备。", "SDR设备尚未确认。"),
        ("云台未连接。", "云台尚未确认。"),
    ])
    def test_unknown_slot_claims_are_rewritten(self, text, want):
        slot = ("imu" if "IMU" in text else
                "gnss" if "GNSS" in text else
                "sdr" if "SDR" in text else "gimbal")
        assert sanitize_unknown_claims(text, [slot]) == want

    @pytest.mark.parametrize("text", [
        "未检测到GNSS信号。",          # 信号弱，不是没有模块
        "GNSS定位未连接。",            # 定位结果，不是硬件
        "未检测到北斗卫星。",           # 卫星数，不是模块
        "未检测到信号。",              # 非硬件对象
        "SDR 已连接，正在接收。",       # 不是否定句
    ])
    def test_non_hardware_or_non_negative_text_is_untouched(self, text):
        assert sanitize_unknown_claims(text, ["gnss", "sdr", "imu"]) == text

    def test_missing_slot_is_not_rewritten(self):
        """missing 是「确认没有」，模型说未连接是对的。"""
        assert sanitize_unknown_claims("IMU未连接。", []) == "IMU未连接。"

    def test_idempotent(self):
        once = sanitize_unknown_claims("未检测到IMU。", ["imu"])
        assert sanitize_unknown_claims(once, ["imu"]) == once

    def test_empty_text(self):
        assert sanitize_unknown_claims("", ["imu"]) == ""


class TestStreamGuard:
    def test_emits_at_sentence_boundary_with_rewrite(self):
        out = []
        guard = StreamGuard(out.append, ["imu"])
        guard.feed("未检测到")
        assert out == []                       # 句子没完，先缓冲
        guard.feed("IMU姿态传感器。继续")
        assert out == ["IMU姿态传感器尚未确认。"]
        guard.flush()
        assert out[-1] == "继续"
        assert "".join(out) == "IMU姿态传感器尚未确认。继续"

    def test_flush_without_punctuation(self):
        out = []
        guard = StreamGuard(out.append, ["gnss"])
        guard.feed("GNSS未连接")
        guard.flush()
        assert out == ["GNSS尚未确认"]

    def test_long_buffer_without_punctuation_is_flushed_in_chunks(self):
        out = []
        guard = StreamGuard(out.append, ["imu"])
        guard.feed("未检测到IMU" + "啊" * 100)
        assert out and out[0].startswith("IMU尚未确认")
        guard.flush()

    def test_no_unknown_slots_is_identity(self):
        out = []
        guard = StreamGuard(out.append, [])
        guard.feed("IMU未连接。")
        guard.flush()
        assert out == ["IMU未连接。"]


class TestUnverifiedUi:
    @pytest.mark.parametrize("text,want", [
        ("当前指向的天区会以绿色光晕显示。", "当前指向的天区会以实际界面效果显示。"),
        ("点击屏幕中央查看实时星图。", "按实际界面提示查看实时星图。"),
        ("拖动可调整视角。", "按实际界面提示操作。"),
        ("长按屏幕触发识别。", "按实际界面提示触发识别。"),
        ("双指缩放星图。", "按实际界面提示缩放星图。"),
    ])
    def test_fake_ui_phrases_are_neutralized(self, text, want):
        assert sanitize_unverified_ui_claims(text) == want

    @pytest.mark.parametrize("text", [
        "准星下方会显示星名与距中心角距。",
        "点「视场 60°」可切到 25°、8°。",
        "用「左/右 5°」手动对齐。",
        "顶部有「指天视图」入口。",
    ])
    def test_verified_ui_facts_are_untouched(self, text):
        assert sanitize_unverified_ui_claims(text) == text

    def test_sanitize_answer_composes_both_rules(self):
        text = "未检测到IMU，天区会有绿色光晕。"
        out = sanitize_answer(text, ["imu"])
        assert "IMU尚未确认" in out
        assert "绿色光晕" not in out
        assert "实际界面效果" in out


class TestSlideGestureHallucination:
    """实测 8B 反复编造「左右滑动屏幕」；真实控件是底部三个按钮。

    pointing_page.dart 的 _AlignControls（约 636-673 行）只有「左 5°」
    「右 5°」「复位」三个 TextButton，没有滑动手势。
    """

    @pytest.mark.parametrize("text", [
        "如果罗盘未连接，会提示“方向未知”，这时可以手动左右滑动屏幕调整5°对齐。",
        "请左右滑动屏幕手动对齐5°，直到准星稳定锁定目标。",
        "如果屏幕提示“方向未知”，请左右滑动屏幕手动对齐5°",
        "也可以左右滑动手机屏幕微调方位。",
    ])
    def test_slide_gesture_is_rewritten_to_real_buttons(self, text):
        out = sanitize_unverified_ui_claims(text)
        assert "滑动" not in out
        assert "「左 5°」「右 5°」按钮" in out

    @pytest.mark.parametrize("text", [
        "请转动手机调整方位。",
        "转动身体让准星对准目标。",
    ])
    def test_real_physical_actions_are_untouched(self, text):
        assert sanitize_unverified_ui_claims(text) == text

    def test_idempotent(self):
        once = sanitize_unverified_ui_claims("请左右滑动屏幕手动对齐5°")
        assert sanitize_unverified_ui_claims(once) == once

    def test_empty_text(self):
        assert sanitize_unverified_ui_claims("") == ""


class TestFakeHeadingInput:
    """实测 8B 编造「手动输入方位」；pointing_page.dart 没有方位输入框。"""

    @pytest.mark.parametrize("text", [
        "若罗盘未连接，需手动输入方位（如\"朝北\"），但当前设备未检测到航向数据，"
        "请先确认手机是否已连接罗盘/IMU。",
        "当前设备未检测到航向数据，请手动输入方位（例如\"朝北\"），"
        "或先将手机放在桌面使用星图软件辅助定位。",
        "请手动输入一个方位。",
    ])
    def test_heading_input_is_rewritten_to_real_buttons(self, text):
        out = sanitize_unverified_ui_claims(text)
        assert "输入方位" not in out
        assert "「左 5°」「右 5°」按钮" in out

    @pytest.mark.parametrize("text", [
        "界面会显示当前方位角。",
        "请手动转动手机调整方位。",
    ])
    def test_real_heading_facts_are_untouched(self, text):
        assert sanitize_unverified_ui_claims(text) == text

    def test_idempotent(self):
        once = sanitize_unverified_ui_claims("请手动输入方位（如\"朝北\"）。")
        assert sanitize_unverified_ui_claims(once) == once

    def test_empty_text(self):
        assert sanitize_unverified_ui_claims("") == ""


class TestSidePositionAndHeadingAngle:
    """实测 8B 把底部一行按钮说成「屏幕两侧」，并编造「输入方位角」。"""

    @pytest.mark.parametrize("text,want", [
        ("点击屏幕两侧的「左/右 5°」按钮进行手动对齐",
         "点击「左/右 5°」按钮进行手动对齐"),
        ("可点击屏幕两侧的「左/右 5°」手动对齐",
         "可点击「左/右 5°」手动对齐"),
    ])
    def test_unverified_side_position_is_removed(self, text, want):
        out = sanitize_answer(text, [])
        assert out == want
        assert "两侧" not in out

    def test_heading_angle_input_is_rewritten(self):
        out = sanitize_answer("此时可手动输入方位角（如\"正北 0°\"）继续操作。", [])
        assert "输入方位角" not in out
        assert "「左 5°」「右 5°」按钮" in out

    @pytest.mark.parametrize("text", [
        "状态行显示当前方位角。",
        "屏幕底部「左 5°」「右 5°」按钮用于手动对齐。",
    ])
    def test_real_position_and_angle_display_are_untouched(self, text):
        assert sanitize_answer(text, []) == text

    def test_idempotent(self):
        once = sanitize_answer("点击屏幕两侧的「左/右 5°」按钮进行手动对齐", [])
        assert sanitize_answer(once, []) == once

    def test_empty_text(self):
        assert sanitize_answer("", []) == ""


class TestUnverifiedUiPositions:
    """实测 8B 把底部真实按钮说成「屏幕右下角「左/右 5°」按钮」。"""

    @pytest.mark.parametrize("text", [
        # 顶部状态行（pointing_page.dart _StatusRow）本来就显示方位/仰角/视场。
        "顶部状态显示方位、仰角、视场。",
        # _AlignControls 是底部按钮行：左 5°/右 5°/复位。
        "屏幕底部「左 5°」「右 5°」「复位」按钮用于手动对齐。",
        # AppBar / 状态行里的视场按钮在顶部。
        "顶部「视场 60°」可切到 25°、8°。",
        # sky_page.dart AppBar actions 的「指天视图」入口在右上。
        "屏幕右上角「指天视图」按钮进入指天视图。",
    ])
    def test_verified_position_and_label_are_untouched(self, text):
        assert sanitize_unverified_ui_positions(text) == text

    @pytest.mark.parametrize("text,want", [
        ("屏幕右下角「左/右 5°」按钮。", "「左/右 5°」按钮。"),
        ("屏幕右下角有「复位」按钮。", "「复位」按钮。"),
        ("「复位」按钮位于屏幕右下角。", "「复位」按钮。"),
    ])
    def test_unverified_position_is_removed_label_kept(self, text, want):
        assert sanitize_unverified_ui_positions(text) == want

    def test_unverified_position_and_label_become_neutral(self):
        assert (sanitize_unverified_ui_positions("屏幕右下角「自动校准」按钮。")
                == "对应控件按钮。")

    def test_plain_numbered_list_is_untouched(self):
        text = ("1. 打开指天视图\n"
                "2. 举起手机对准目标\n"
                "3. 看准星下方的星名和距中心")
        assert sanitize_unverified_ui_positions(text) == text

    def test_numbered_list_keeps_steps_and_numbering(self):
        text = ("1. 打开指天视图\n"
                "2. 点屏幕右下角「设置」按钮\n"
                "3. 看准星")
        out = sanitize_unverified_ui_positions(text)
        assert out == ("1. 打开指天视图\n"
                       "2. 点对应控件按钮\n"
                       "3. 看准星")
        assert out.count("\n") == text.count("\n")

    def test_idempotent(self):
        once = sanitize_unverified_ui_positions("屏幕右下角「左/右 5°」按钮。")
        assert sanitize_unverified_ui_positions(once) == once

    def test_stream_guard_applies_position_rule_at_sentence_boundary(self):
        out = []
        guard = StreamGuard(out.append, [])
        guard.feed("屏幕右下角「左/右 5°」按钮")
        assert out == []                     # 句子没完，先缓冲
        guard.feed("。继续")
        assert out == ["「左/右 5°」按钮。"]
        guard.flush()
        assert "".join(out) == "「左/右 5°」按钮。继续"

    def test_sanitize_answer_applies_position_rule(self):
        assert (sanitize_answer("点屏幕右下角「自动校准」按钮。", [])
                == "点对应控件按钮。")

    def test_empty_text(self):
        assert sanitize_unverified_ui_positions("") == ""


class TestPositionWithVerbAndFovSwitch:
    """实测 8B：「屏幕顶部点击「手动对齐」」「视场会切换至 8°」。"""

    def test_position_then_verb_then_label_is_neutralized(self):
        out = sanitize_unverified_ui_positions("屏幕顶部点击「手动对齐」按钮。")
        assert "顶部" not in out
        assert "对应控件" in out
        assert out.startswith("点击")

    def test_verified_position_with_verb_stays(self):
        text = "屏幕底部点击「左 5°」按钮。"
        assert sanitize_unverified_ui_positions(text) == text

    def test_fov_auto_switch_is_rewritten(self):
        out = sanitize_unverified_ui_claims("重新对齐后视场会切换至8°（最窄档）。")
        assert "自动" not in out
        assert "视场需要手动切到 8°" in out

    def test_fov_manual_instruction_untouched(self):
        text = "点顶部视场按钮切到 8°。"
        assert sanitize_unverified_ui_claims(text) == text


class TestNumberedSteps:
    """系统提示词禁止编号清单，但 8B 反复给「1. 2. 3.」；用确定性格栏收口。"""

    def test_markers_removed_by_default(self):
        text = "1. 打开指天视图\n2. 对准亮星\n3. 看准星"
        assert sanitize_numbered_steps(text) == (
            "打开指天视图\n对准亮星\n看准星")

    def test_allow_keeps_markers(self):
        text = "1. A\n2. B"
        assert sanitize_numbered_steps(text, allow_numbered=True) == text

    def test_single_marker_removed(self):
        assert sanitize_numbered_steps("1. 只有一步") == "只有一步"

    def test_decimal_numbers_untouched(self):
        text = "145.500 MHz 是频率\n2.4 MS/s 是采样率"
        assert sanitize_numbered_steps(text) == text

    def test_wants_numbered_steps(self):
        assert wants_numbered_steps("给我完整步骤")
        assert wants_numbered_steps("一步一步教我")
        assert not wants_numbered_steps("怎么收 145.500？")

    def test_sanitize_answer_respects_flag(self):
        text = "1. A\n2. B"
        assert sanitize_answer(text, []) == "A\nB"
        assert sanitize_answer(text, [], allow_numbered=True) == text

    def test_stream_guard_strips_per_line(self):
        out = []
        guard = StreamGuard(out.append, [], allow_numbered=False)
        guard.feed("1. 打开指天视图\n2. 对准亮星\n")
        guard.flush()
        assert "".join(out) == "打开指天视图\n对准亮星\n"

    def test_stream_guard_keeps_when_allowed(self):
        out = []
        guard = StreamGuard(out.append, [], allow_numbered=True)
        guard.feed("1. A\n2. B\n")
        guard.flush()
        assert "".join(out) == "1. A\n2. B\n"


class TestAgentIntegration:
    def test_chat_sanitizes_final_content_and_stream(self, monkeypatch):
        """agent.chat 的流式与非流式都必须过同一条护栏。"""
        monkeypatch.setenv("MBDSDR_NO_HW", "1")
        from mbdsdr_ai.agent import MBDSDRAgent
        from mbdsdr_ai.config import AgentConfig

        agent = MBDSDRAgent(AgentConfig(
            api_key="sk-offline", enable_self_evolution=False,
            enable_session_log=False))

        def fake_impl(user, rounds, on_delta=None, images=None):
            if on_delta is not None:
                on_delta("未检测到IMU。")
            return {"content": "未检测到IMU。",
                    "tool_calls": [], "tool_results": []}

        monkeypatch.setattr(agent, "_chat_impl", fake_impl)
        chunks = []
        result = agent.chat("x", on_delta=chunks.append)
        assert chunks == ["IMU尚未确认。"]
        assert result["content"] == "IMU尚未确认。"
