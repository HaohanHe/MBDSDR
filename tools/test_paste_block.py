#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
tools/test_paste_block.py — diag_wizard --paste 回传块 往返/坏输入/回归测试
==========================================================================

phase10 P1 交付物：diag_wizard.py 新增 ``--paste`` 模式（人类可读报告末尾
追加围栏包裹的机器可解析 JSON 回传块），parse_hw_report.py 兼容解析该块。

本文件只做离线确定性测试，不碰真机、不联网：

  * 往返一致：build_paste_block -> render_paste_block -> parse_text ->
    analyze_diagwizard，关键字段逐一比对；
  * 误分类防护：回传块自带 "checks" 列表，必须识别成 diagwizard，
    绝不能被旧的形状启发式误判成 selfcheck；
  * 坏输入：围栏 JSON 截断/损坏时 extract 自动跳过，不崩；纯垃圾报错；
  * 主入口：--paste 输出含围栏标记与回传块；不带 --paste 不含标记；退出码；
  * 回归：既有 selfcheck/onboard fixture 的分类与解析行为不变。

跑法：
  python3 -m pytest tools/test_paste_block.py -v
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent            # tools/
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE / "onboarding"))

import diag_wizard as W          # noqa: E402
import parse_hw_report as P      # noqa: E402


# ---------------------------------------------------------------------------
# Fixture：与 tools/test_diag_wizard.py 同形状的全绿真机报告
# ---------------------------------------------------------------------------
def _check(name: str, status: str, detail: dict,
           evidence: list[str] | None = None) -> dict:
    return {"name": name, "status": status, "evidence": evidence or [],
            "fix": [], "detail": detail}


def _all_green_report() -> dict:
    return {
        "tool": W.SELFCHECK_TOOL,
        "version": "0.1.0",
        "timestamp": "2026-10-02T10:00:00+08:00",
        "host": "fixture-box",
        "os": "Linux-fixture",
        "python": "3.12.11",
        "checks": [
            _check("1. USB/udev：RTL-SDR 枚举与权限", "PASS", {
                "target_hits": [{"vid": "0bda", "pid": "2838",
                                 "product": "RTL2838UHIDIR",
                                 "known_as": "RTL2838UHIDIR (目标真机)"}],
                "user_groups": ["user", "plugdev", "dialout"],
                "udev_rules_hit": ["/etc/udev/rules.d/99-rtlsdr.rules"],
            }),
            _check("2. 设备枚举：rtl_test -t", "PASS",
                   {"parsed": {"tuner": "Rafael Micro R820T", "gain_count": 29}}),
            _check("3. 流读取：rtl_sdr 实读", "PASS",
                   {"tmp_file_bytes": 14_400_000, "lost_bytes_total": 0}),
            _check("4. 声卡：aplay -l", "PASS", {}),
            _check("5. GNSS 串口", "WARN", {"candidates": []}),
            _check("6. 依赖", "PASS",
                   {"py_rtlsdr": True, "py_soapy": True,
                    "gpsd": "/usr/sbin/gpsd"}),
        ],
        "summary": {"counts": {"PASS": 4, "WARN": 1, "FAIL": 0, "SKIP": 0},
                    "conclusion": "环境基本就绪：未发现阻断性问题。",
                    "real_machine_todo": []},
    }


def _no_device_report() -> dict:
    return {
        "tool": W.SELFCHECK_TOOL,
        "version": "0.1.0",
        "timestamp": "2026-10-02T10:05:00+08:00",
        "host": "cloud-vm",
        "os": "Linux-cloud",
        "python": "3.12.11",
        "checks": [
            _check("1. USB/udev：RTL-SDR 枚举与权限", "FAIL",
                   {"target_hits": [], "user_groups": ["user"],
                    "udev_rules_hit": []},
                   evidence=["  未在 lsusb 中找到 0bda:2838/2832"]),
            _check("2. 设备枚举：rtl_test -t", "SKIP",
                   {"skipped_reason": "no_usb_device"}),
            _check("3. 流读取：rtl_sdr 实读", "SKIP",
                   {"skipped_reason": "no_usb_device"}),
            _check("4. 声卡：aplay -l", "WARN", {}),
            _check("5. GNSS 串口", "WARN", {"candidates": []}),
            _check("6. 依赖", "WARN",
                   {"py_rtlsdr": False, "py_soapy": False, "gpsd": None}),
        ],
        "summary": {"counts": {"PASS": 0, "WARN": 3, "FAIL": 1, "SKIP": 2},
                    "conclusion": "未检测到 RTL-SDR 真机设备。",
                    "real_machine_todo": []},
    }


def _rendered_paste(report: dict) -> str:
    """模拟 ``selfcheck --json | diag_wizard --paste`` 的完整 stdout。"""
    res = W.build_result(report)
    human = W.render_human(res, report)
    block = W.build_paste_block(res, report)
    return human + "\n\n" + W.render_paste_block(block)


# ---------------------------------------------------------------------------
# 1. 往返一致
# ---------------------------------------------------------------------------
class TestRoundTrip:
    def test_all_green_roundtrip_fields(self):
        report = _all_green_report()
        text = _rendered_paste(report)

        blocks = P.extract_json_objects(text)
        # 回传块是文本里唯一的 JSON 对象（向导不回显输入 JSON）
        assert len(blocks) == 1
        block = blocks[0]
        assert P.classify_block(block) == "diagwizard"

        dw = P.analyze_diagwizard(block)
        assert dw["tool"] == W.WIZARD_TOOL
        assert dw["version"] == W.WIZARD_VERSION
        assert dw["timestamp"] == "2026-10-02T10:00:00+08:00"
        assert dw["host"] == "fixture-box"
        assert dw["exit_code"] == 0
        assert dw["device_present"] is True
        assert dw["no_hardware_expected"] is False
        assert dw["checks_summary"] == {"PASS": 4, "WARN": 1, "FAIL": 0}
        assert dw["summary_conclusion"].startswith("环境基本就绪")
        # 6 项检查逐项带 name/status/verdict
        assert len(dw["checks"]) == 6
        c1 = dw["checks"][0]
        assert c1["status"] == "PASS"
        assert "0bda:2838" in c1["verdict"]

    def test_all_green_next_steps_carry_onboard_and_backfill(self):
        text = _rendered_paste(_all_green_report())
        _, _, blocks = P.parse_text(text)
        dw = P.analyze_diagwizard(next(b for b in blocks
                                       if P.classify_block(b) == "diagwizard"))
        joined = "\n".join(dw["next_steps"])
        assert "onboard.py --step all --freq 1090e6 --mode adsb" in joined
        assert "exp_ota_run.py" in joined
        assert "--recordings-dir" in joined

    def test_no_device_roundtrip_exit_1_and_empty_next_steps(self):
        text = _rendered_paste(_no_device_report())
        blocks = P.extract_json_objects(text)
        dw = P.analyze_diagwizard(blocks[0])
        assert dw["exit_code"] == 1
        assert dw["device_present"] is False
        assert dw["no_hardware_expected"] is True
        assert dw["checks_summary"]["FAIL"] == 1
        # 无设备时不给 onboard 下一步（与向导 decide_next_steps 一致）
        assert dw["next_steps"] == []

    def test_suggested_commands_deduplicated_and_complete(self):
        report = _all_green_report()
        # 把第 1 项改成缺用户组 -> 产生 usermod 命令
        report["checks"][0]["detail"]["user_groups"] = ["user"]
        report["checks"][0]["status"] = "FAIL"
        report["summary"]["counts"]["FAIL"] = 1
        res = W.build_result(report)
        block = W.build_paste_block(res, report)
        cmds = block["suggested_commands"]
        # 去重保序
        assert len(cmds) == len(set(cmds))
        assert any("usermod -aG plugdev,dialout" in c for c in cmds)


# ---------------------------------------------------------------------------
# 2. 误分类防护：带 "checks" 列表的回传块不得被当成 selfcheck
# ---------------------------------------------------------------------------
class TestMisclassificationGuard:
    def test_paste_block_not_classified_as_selfcheck(self):
        block = W.build_paste_block(W.build_result(_all_green_report()),
                                    _all_green_report())
        assert P.classify_block(block) != "selfcheck"
        assert P.classify_block(block) == "diagwizard"

    def test_paste_block_does_not_leak_into_selfcheck_analysis(self):
        """parse_text 遇到回传块时 sc 必须为 None（它不是 selfcheck 报告）。"""
        text = _rendered_paste(_no_device_report())
        sc, ob, blocks = P.parse_text(text)
        assert sc is None
        assert ob is None
        assert any(P.classify_block(b) == "diagwizard" for b in blocks)


# ---------------------------------------------------------------------------
# 3. 坏输入
# ---------------------------------------------------------------------------
class TestBadInput:
    def test_truncated_paste_block_skipped_not_crashing(self):
        """围栏 JSON 没闭合 -> extract 跳过该片段；文本里再无别的 JSON -> 报错。"""
        text = (
            "刚才跑了向导，输出贴一半：\n"
            + W.PASTE_BEGIN + "\n"
            + '{"tool": "mbdsdr-diag-wizard", "version": "0.1.0", "checks": [{"name": "1.'
        )
        with pytest.raises(P.ReportParseError):
            P.parse_text(text)

    def test_corrupt_block_alongside_valid_selfcheck(self):
        """损坏（花括号平衡但 JSON 非法）的块被跳过，夹在其中的合法 selfcheck
        仍能解析。注意：文本开头若出现不闭合花括号会触发 extractor 的既有
        '首个 { 不闭合即整体停止' 行为，因此坏块必须是平衡片段。"""
        sc_json = json.dumps({
            "tool": "mbdsdr-hw-selfcheck", "checks": [
                {"name": "1. USB/udev：RTL-SDR 枚举与权限", "status": "PASS",
                 "evidence": [], "fix": [],
                 "detail": {"target_hits": [{"vid": "0bda", "pid": "2838",
                                             "product": "RTL2838",
                                             "known_as": "RTL2838"}],
                            "user_groups": ["plugdev"], "udev_rules_hit": []}}
            ], "summary": {"counts": {"PASS": 1, "WARN": 0, "FAIL": 0, "SKIP": 0}}},
            ensure_ascii=False)
        chat = (
            "上面贴了一段坏掉的内容：\n"
            + '{"broken block": "trailing comma makes json invalid", }' + "\n"
            + sc_json + "\n"
            + "上面是好的 selfcheck。\n"
        )
        sc, ob, blocks = P.parse_text(chat)
        assert sc is not None
        assert sc["device"]["present"] is True

    def test_pure_garbage_still_raises(self):
        with pytest.raises(P.ReportParseError):
            P.parse_text("没有任何 JSON 的聊天文本\n$ lsusb\ncommand not found\n")

    def test_empty_text_still_raises(self):
        with pytest.raises(P.ReportParseError):
            P.parse_text("")


# ---------------------------------------------------------------------------
# 4. 主入口端到端（subprocess，与用户真机操作一致）
# ---------------------------------------------------------------------------
class TestMainPasteFlag:
    def _run_wizard(self, report: dict, extra_args: list[str],
                    tmp_path: Path) -> subprocess.CompletedProcess:
        p = tmp_path / "selfcheck_report.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(_HERE / "diag_wizard.py"), str(p), *extra_args],
            capture_output=True, text=True, timeout=30, check=False)

    def test_paste_flag_emits_fenced_block(self, tmp_path):
        proc = self._run_wizard(_all_green_report(), ["--paste"], tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert W.PASTE_BEGIN in proc.stdout
        assert W.PASTE_END in proc.stdout
        # 围栏内确实是合法 JSON
        begin = proc.stdout.index(W.PASTE_BEGIN) + len(W.PASTE_BEGIN)
        end = proc.stdout.index(W.PASTE_END)
        fenced = proc.stdout[begin:end].strip()
        block = json.loads(fenced)
        assert block["tool"] == W.WIZARD_TOOL
        assert block["device_present"] is True
        # 该 stdout 可直接喂给 parse_hw_report
        proc2 = subprocess.run(
            [sys.executable, str(_HERE / "onboarding" / "parse_hw_report.py")],
            input=proc.stdout, capture_output=True, text=True, timeout=30,
            check=False)
        assert proc2.returncode == 0, proc2.stderr
        assert "诊断向导回传块" in proc2.stdout

    def test_without_paste_flag_has_no_fence(self, tmp_path):
        proc = self._run_wizard(_all_green_report(), [], tmp_path)
        assert proc.returncode == 0
        assert W.PASTE_BEGIN not in proc.stdout
        assert "mbdsdr-diag-wizard" not in proc.stdout

    def test_paste_on_no_device_report_exit_1(self, tmp_path):
        proc = self._run_wizard(_no_device_report(), ["--paste"], tmp_path)
        assert proc.returncode == 1
        assert W.PASTE_BEGIN in proc.stdout


# ---------------------------------------------------------------------------
# 5. 回归：既有 fixture 分类/解析行为不变
# ---------------------------------------------------------------------------
class TestRegression:
    def test_legacy_selfcheck_fixture_still_selfcheck(self):
        sc = {
            "tool": "mbdsdr-hw-selfcheck",
            "checks": [{"name": "1. x", "status": "PASS", "evidence": [],
                        "fix": [], "detail": {"target_hits": []}}],
            "summary": {"counts": {}},
        }
        assert P.classify_block(sc) == "selfcheck"
        assert P.analyze_selfcheck(sc)["device"]["present"] is False

    def test_bare_steps_fixture_still_onboard(self):
        bare = {"steps": [{"step": "detect", "status": "FAIL", "message": "no device",
                           "evidence": [], "fixes": [], "detail": {}}]}
        assert P.classify_block(bare) == "onboard"
        ob = P.analyze_onboard(bare)
        assert ob["tool"] is None

    def test_unrecognized_block_returns_none(self):
        assert P.classify_block({"hello": "world"}) is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
