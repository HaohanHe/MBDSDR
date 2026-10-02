#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
tools/test_diag_wizard.py — diag_wizard.py 离线确定性测试
========================================================

不碰真机、不联网。两类 fixture：

  * 【真机分支】按 selfcheck.py 真实 --json 结构手工构造 dict（字段名逐字段
    核对自 tools/hw_selfcheck/selfcheck.py 的 CheckResult/detail），覆盖
    FAIL/WARN/PASS 各分支的建议映射；
  * 【无硬件分支】在云 VM 上**真跑** selfcheck.py --json（本环境无硬件，
    它会诚实报告未插设备），再喂给向导，验证空态识别。

跑法：
  python3 -m pytest tools/test_diag_wizard.py -v
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import diag_wizard as W  # noqa: E402

SELFCHECK_PY = _HERE / "hw_selfcheck" / "selfcheck.py"


# ---------------------------------------------------------------------------
# Fixture 构造器：按 selfcheck.py 真实 JSON 形状拼装
# ---------------------------------------------------------------------------
def _check(name: str, status: str, detail: dict,
           evidence: list[str] | None = None) -> dict:
    return {
        "name": name,
        "status": status,
        "evidence": evidence or [],
        "fix": [],
        "detail": detail,
    }


def _report(checks: list[dict], counts: dict | None = None,
            conclusion: str = "") -> dict:
    return {
        "tool": W.SELFCHECK_TOOL,
        "version": "0.1.0",
        "timestamp": "2026-10-02T10:00:00+08:00",
        "host": "fixture-box",
        "os": "Linux-fixture",
        "python": "3.12.11",
        "checks": checks,
        "summary": {
            "counts": counts or {"PASS": 0, "WARN": 0, "FAIL": 0, "SKIP": 0},
            "conclusion": conclusion,
            "real_machine_todo": [],
        },
    }


def _report_all_green() -> dict:
    """设备在场、全链路就绪的标准真机报告。"""
    return _report([
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
               {"py_rtlsdr": True, "py_soapy": True, "gpsd": "/usr/sbin/gpsd"}),
    ], counts={"PASS": 4, "WARN": 1, "FAIL": 0, "SKIP": 0},
       conclusion="环境基本就绪：未发现阻断性问题。")


def _report_no_device() -> dict:
    """云 VM / 未插设备：第 1 项 FAIL 且 target_hits 空，2/3 SKIP。"""
    return _report([
        _check("1. USB/udev：RTL-SDR 枚举与权限", "FAIL",
               {"target_hits": [], "user_groups": ["user"],
                "udev_rules_hit": []},
               evidence=["  未在 lsusb / /sys 中找到 0bda:2838/2832"]),
        _check("2. 设备枚举：rtl_test -t", "SKIP",
               {"skipped_reason": "no_usb_device"}),
        _check("3. 流读取：rtl_sdr 实读", "SKIP",
               {"skipped_reason": "no_usb_device"}),
        _check("4. 声卡：aplay -l", "WARN", {}),
        _check("5. GNSS 串口", "WARN", {"candidates": []}),
        _check("6. 依赖", "WARN",
               {"py_rtlsdr": False, "py_soapy": False, "gpsd": None}),
    ], counts={"PASS": 0, "WARN": 3, "FAIL": 1, "SKIP": 2},
       conclusion="未检测到 RTL-SDR 真机设备。")


def _advice_text(res: W.WizardResult, check_prefix: str) -> str:
    for a in res.advices:
        if a.check.startswith(check_prefix):
            return a.verdict + "\n" + "\n".join(a.commands)
    raise AssertionError(f"没有找到以 {check_prefix!r} 开头的检查建议")


# ---------------------------------------------------------------------------
# 1. 校验逻辑（坏输入 -> 退出码 2）
# ---------------------------------------------------------------------------
class TestValidate:
    def test_plain_text_rejected(self):
        assert W.validate_report("not a dict") == "顶层不是 JSON 对象（是 str）"

    def test_missing_checks_rejected(self):
        err = W.validate_report({"tool": "x"})
        assert err is not None
        assert "checks" in err

    def test_onboard_report_rejected_with_hint(self):
        err = W.validate_report({"tool": W.ONBOARD_TOOL, "steps": []})
        assert err is not None
        assert "onboard" in err

    def test_valid_report_passes(self):
        assert W.validate_report(_report_all_green()) is None


class TestBadInputMain:
    def _run(self, payload: str | None, path: str | None,
             monkeypatch, tmp_path) -> tuple[int, str]:
        """模拟 main 的输入源：文件 或 stdin。"""
        argv = []
        if path:
            p = tmp_path / path
            p.write_text(payload or "", encoding="utf-8")
            argv = [str(p)]
        elif payload is not None:
            monkeypatch.setattr(sys, "stdin",
                                type("S", (), {"read": lambda self: payload})())
        else:
            monkeypatch.setattr(sys, "stdin",
                                type("S", (), {"read": lambda self: ""})())
        return W.main(argv), ""

    def test_garbage_file_exit_2(self, monkeypatch, tmp_path):
        rc, _ = self._run("hello world not json", "r.txt", monkeypatch, tmp_path)
        assert rc == 2

    def test_onboard_json_exit_2(self, monkeypatch, tmp_path):
        payload = json.dumps({"tool": W.ONBOARD_TOOL, "steps": []})
        rc, _ = self._run(payload, "r.txt", monkeypatch, tmp_path)
        assert rc == 2

    def test_empty_stdin_exit_2(self, monkeypatch, tmp_path):
        rc, _ = self._run(None, None, monkeypatch, tmp_path)
        assert rc == 2


# ---------------------------------------------------------------------------
# 2. 真机分支：fixture 构造的建议映射
# ---------------------------------------------------------------------------
class TestRealMachineBranches:
    def test_all_green_triggers_next_steps_chain(self):
        res = W.build_result(_report_all_green())
        assert res.device_present is True
        assert res.fail_count == 0
        blob = "\n".join(res.next_steps)
        # onboard 一条命令
        assert "onboard.py --step all --freq 1090e6 --mode adsb" in blob
        # 回填路径
        assert "exp_ota_run.py" in blob
        assert "--recordings-dir" in blob
        assert "paper/experiments/onboarding_" in blob

    def test_all_green_main_exit_0(self, tmp_path):
        p = tmp_path / "good.json"
        p.write_text(json.dumps(_report_all_green()), encoding="utf-8")
        assert W.main([str(p)]) == 0

    def test_no_device_fail_gives_replug_commands(self):
        res = W.build_result(_report_no_device())
        assert res.no_hardware_expected is True
        text = _advice_text(res, "1.")
        assert "lsusb | grep" in text
        assert "dmesg" in text

    def test_device_present_but_missing_group(self):
        report = _report_no_device()
        report["checks"][0]["status"] = "FAIL"
        report["checks"][0]["detail"] = {
            "target_hits": [{"vid": "0bda", "pid": "2838",
                             "product": "RTL2838UHIDIR", "known_as": "?"}],
            "user_groups": ["user"],          # 缺 plugdev/dialout
            "udev_rules_hit": ["/etc/udev/rules.d/99-rtlsdr.rules"],
        }
        report["summary"]["counts"]["FAIL"] = 1
        res = W.build_result(report)
        assert res.device_present is True
        text = _advice_text(res, "1.")
        assert "usermod -aG plugdev,dialout" in text

    def test_device_present_but_missing_udev_rule(self):
        report = _report_all_green()
        report["checks"][0]["status"] = "WARN"
        report["checks"][0]["detail"]["udev_rules_hit"] = []
        res = W.build_result(report)
        text = _advice_text(res, "1.")
        assert "99-mbdsdr-rtlsdr.rules" in text
        assert "udevadm control --reload" in text

    def test_warn_rtl_test_not_found(self):
        report = _report_all_green()
        report["checks"][1] = _check(
            "2. 设备枚举：rtl_test -t", "WARN", {},
            evidence=["未找到 rtl_test（PATH 与 ~/.local/bin 均无）"])
        res = W.build_result(report)
        text = _advice_text(res, "2.")
        assert "apt install rtl-sdr" in text

    def test_fail_no_supported_devices(self):
        report = _report_all_green()
        report["checks"][1] = _check(
            "2. 设备枚举：rtl_test -t", "FAIL",
            {"parsed": {"no_supported_devices": True}})
        res = W.build_result(report)
        text = _advice_text(res, "2.")
        assert "modprobe -r dvb_usb_rtl28xxu" in text

    def test_stream_empty_file_gives_pgrep_advice(self):
        report = _report_all_green()
        report["checks"][2] = _check(
            "3. 流读取：rtl_sdr 实读", "FAIL",
            {"tmp_file_bytes": 0, "lost_bytes_total": 0})
        res = W.build_result(report)
        text = _advice_text(res, "3.")
        assert "pgrep" in text

    def test_stream_warn_lost_bytes(self):
        report = _report_all_green()
        report["checks"][2] = _check(
            "3. 流读取：rtl_sdr 实读", "WARN",
            {"tmp_file_bytes": 14_000_000, "lost_bytes_total": 5_000_000})
        res = W.build_result(report)
        text = _advice_text(res, "3.")
        assert "USB2.0" in text

    def test_deps_missing_gives_pip_advice(self):
        report = _report_all_green()
        report["checks"][5] = _check(
            "6. 依赖", "WARN",
            {"py_rtlsdr": False, "py_soapy": False, "gpsd": None})
        res = W.build_result(report)
        text = _advice_text(res, "6.")
        assert "pip install --user pyrtlsdr" in text
        assert "gpsd" in text


# ---------------------------------------------------------------------------
# 3. 无硬件分支：云内真跑 selfcheck.py --json（本 VM 无设备）
# ---------------------------------------------------------------------------
class TestCloudRealNoHardware:
    def test_cloud_selfcheck_piped_into_wizard(self):
        assert SELFCHECK_PY.exists(), f"selfcheck 不存在: {SELFCHECK_PY}"
        proc = subprocess.run(
            [sys.executable, str(SELFCHECK_PY), "--json"],
            capture_output=True, text=True, timeout=60, check=False)
        # 无硬件时 selfcheck 自身退出码就是 1（有 FAIL），不代表向导坏
        assert proc.returncode in (0, 1), proc.stderr[-500:]

        report = json.loads(proc.stdout)   # 真报告，不 mock
        assert W.validate_report(report) is None

        res = W.build_result(report)
        # 云 VM 无硬件：设备必不在场，FAIL 来自"没插设备"
        assert res.device_present is False
        assert res.no_hardware_expected is True
        # 不应给出 onboard 下一步（设备不在场）
        assert res.next_steps == []

        # 端到端：向导 main 读这份真 JSON，退出码应为 1（有 FAIL）
        proc2 = subprocess.run(
            [sys.executable, str(_HERE / "diag_wizard.py")],
            input=proc.stdout, capture_output=True, text=True, timeout=30)
        assert proc2.returncode == 1
        assert "未插 RTL-SDR" in proc2.stdout or "未枚举到 RTL-SDR" in proc2.stdout
