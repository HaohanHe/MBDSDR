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
    sanitize_unknown_claims,
    sanitize_unverified_ui_claims,
    sanitize_unverified_ui_positions,
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
