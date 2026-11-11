# SPDX-License-Identifier: MIT
"""tests/test_status_panel_decode_result.py — 解码结果落地到既有面板（P5.3）。

守的点：
  1. 解码输出（文本/报文/帧/图像统计）真的能在既有「状态」面板里看到，
     不是只进日志；
  2. 三类诚实错误码（未知模式/缺依赖/缺样本）在界面上显示为不同的人话，
     不伪装成成功；
  3. 空解码如实写「0 条」+ 原因，不补占位报文。

实现侧 `format_decode_result` 是纯函数（不依赖 Qt），因此诚实性口径可以
在无界面环境里单独钉死；面板部分按仓内约定 offscreen 跑。
"""
import os
import sys
import wave

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop.status_panel import (  # noqa: E402
    StatusPanel, format_decode_result, is_decode_tool,
)

app = QApplication.instance() or QApplication(sys.argv)


def _write_wav(path, samples, rate=12000):
    pcm = np.asarray(samples, dtype=np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return str(path)


# ---------------------------------------------------------------------------
# 纯函数：错误态 / 报文 / 空态
# ---------------------------------------------------------------------------
class TestDecodeToolDetection:
    def test_prefixed_tool_names_are_decode_tools(self):
        assert is_decode_tool("sdr_decode_ais")
        assert is_decode_tool("sdr_decode_navtex")
        assert is_decode_tool("decode_lrpt")

    def test_non_decode_tools_are_ignored(self):
        assert not is_decode_tool("get_version")
        assert not is_decode_tool("sdr_get_status")
        assert not is_decode_tool("")


class TestHonestErrorStates:
    def test_missing_sample_is_visible(self):
        view = format_decode_result("sdr_decode_navtex", {
            "mode": "navtex", "ok": False, "status": "error",
            "error_code": "missing_sample", "error": "输入文件不存在（缺样本）",
        })
        assert "缺样本" in view["summary"]
        assert "missing_sample" in view["detail"]
        assert view["ok"] is False

    def test_unknown_mode_is_distinct(self):
        view = format_decode_result("sdr_decode_x", {
            "ok": False, "error_code": "unknown_mode",
            "error": "未知数字模式", "supported_modes": ["ais", "navtex"],
        })
        assert "未知模式" in view["summary"]
        assert "缺样本" not in view["summary"]
        assert "缺依赖" not in view["summary"]

    def test_missing_dependency_is_distinct(self):
        view = format_decode_result("sdr_decode_ais", {
            "mode": "ais", "ok": False, "error_code": "missing_dependency",
            "error": "缺少内置依赖模块 mbdsdr_ai.ais_decoder",
        })
        assert "缺依赖" in view["summary"]
        assert "mbdsdr_ai.ais_decoder" in view["detail"]

    def test_not_implemented_is_distinct(self):
        view = format_decode_result("sdr_decode_ft8", {
            "mode": "ft8", "ok": False, "error_code": "not_implemented",
            "note": "通用入口尚未接线",
        })
        assert "未接线" in view["summary"]
        assert "通用入口尚未接线" in view["detail"]

    def test_three_error_labels_differ(self):
        codes = ("unknown_mode", "missing_dependency", "missing_sample")
        labels = [format_decode_result("sdr_decode_x", {
            "mode": "x", "ok": False, "error_code": c})["summary"] for c in codes]
        assert len(set(labels)) == 3


class TestMessageLanding:
    def test_messages_are_rendered(self):
        view = format_decode_result("sdr_decode_navtex", {
            "mode": "navtex", "ok": True, "status": "decoded",
            "output_kind": "messages", "num_messages": 3,
            "evidence_level": "no_sample",
            "messages": [
                {"type": "ZCZC", "station": "R", "text": "GALE WARNING"},
                {"type": "ZCZC", "station": "R", "text": "NAV WARNING"},
                {"type": "ZCZC", "station": "R", "text": "SEA ICE"},
            ],
        })
        assert "3 条报文" in view["summary"]
        assert "真实度: 缺样本" in view["summary"]
        assert "GALE WARNING" in view["detail"]
        assert "NAV WARNING" in view["detail"]
        assert "SEA ICE" in view["detail"]

    def test_empty_decode_is_honest(self):
        """P0-2 契约：空结果 ok=False / status=empty / nothing_detected。

        面板不再显示「完成 · 0 条报文」，而是明确「未检测到帧」，并保留
        输入无能量的说明。
        """
        view = format_decode_result("sdr_decode_navtex", {
            "mode": "navtex", "ok": False, "status": "empty",
            "error_code": "nothing_detected", "output_kind": "messages",
            "num_messages": 0, "messages": [], "signal_present": False,
            "note": "输入无信号能量（静音），未解出报文。",
        })
        assert "未检测到帧" in view["summary"]
        assert view["ok"] is False
        assert "0 条报文" not in view["summary"]
        assert "静音" in view["detail"]

    def test_no_output_says_no_output(self):
        view = format_decode_result("sdr_decode_x", {"mode": "x"})
        assert "本次解码无输出" in view["detail"]

    def test_adsb_frame_fields_shown(self):
        view = format_decode_result("sdr_decode_adsb", {
            "mode": "adsb", "found": True, "crc_ok": True,
            "icao_hex": "7809AB", "callsign": "CES1234",
            "evidence_level": "real",
        })
        assert "ICAO 7809AB" in view["detail"]
        assert "CES1234" in view["detail"]
        assert "真实" in view["summary"]

    def test_router_result_feeds_view(self, tmp_path):
        """真实路由结果（P5.2）→ 面板视图，跨层字段对得上。

        2048 样本 @12kHz 只有 0.17s，连一个 NAVTEX 定相+报文都不够：
        现在必须显式 insufficient_samples，而不是 ok=true + 0 条。
        """
        from mbdsdr_ai.decoders import decode_digital_mode
        f = _write_wav(tmp_path / "silence.wav", np.zeros(2048))
        res = decode_digital_mode(f, "navtex", output_dir=str(tmp_path / "o"))
        view = format_decode_result("sdr_decode_navtex", res)
        assert res["evidence_level"] == "no_sample"
        assert "真实度: 缺样本" in view["summary"]
        assert view["error_code"] == "insufficient_samples"
        assert "样本不足一帧" in view["summary"]


# ---------------------------------------------------------------------------
# 面板落地（offscreen）
# ---------------------------------------------------------------------------
class TestStatusPanelLanding:
    def test_initial_state_is_empty_not_fake(self):
        panel = StatusPanel()
        assert "尚无解码结果" in panel.decode_summary.text()
        assert panel.decode_detail.isHidden()

    def test_decode_result_updates_panel(self):
        panel = StatusPanel()
        panel.on_tool_result("sdr_decode_ais", {
            "mode": "ais", "ok": False, "error_code": "missing_sample",
            "evidence_level": "no_sample",
            "error": "输入文件不存在（缺样本，不是解码失败）: /tmp/x.cf32",
        })
        assert "缺样本" in panel.decode_summary.text()
        assert "缺样本，不是解码失败" in panel.decode_detail.toPlainText()
        assert panel.decode_detail.isVisibleTo(panel)

    def test_messages_land_in_panel(self):
        panel = StatusPanel()
        panel.on_tool_result("sdr_decode_pocsag", {
            "mode": "pocsag", "ok": True, "output_kind": "messages",
            "num_messages": 1, "evidence_level": "partial",
            "messages": [{"address": 123456, "function": 0, "text": "TESTSMS"}],
        })
        txt = panel.decode_detail.toPlainText()
        assert "TESTSMS" in txt
        assert "1 条报文" in panel.decode_summary.text()
        assert "部分" in panel.decode_summary.text()

    def test_other_tool_does_not_touch_decode_card(self):
        panel = StatusPanel()
        before = panel.decode_summary.text()
        panel.on_tool_result("sdr_get_status", {"ok": True})
        assert panel.decode_summary.text() == before

    def test_snapshot_survives_for_tests(self):
        panel = StatusPanel()
        panel.on_tool_result("sdr_decode_navtex", {
            "mode": "navtex", "ok": False, "error_code": "unsupported_format",
        })
        assert panel.decode_last["error_code"] == "unsupported_format"
