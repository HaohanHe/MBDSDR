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
