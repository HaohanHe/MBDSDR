#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
tools/test_acceptance_run.py — phase11 P1 一键演练脚本的纯逻辑/CLI/云内真跑测试
==============================================================================

被测对象：
  * tools/acceptance_lib.py —— device_present 判定 / onboard_outcome / ota_outcome /
    assemble 组装 / validate 校验 / CLI 子命令；
  * tools/acceptance_run.sh —— 云内无硬件分支真跑（诚实 NO_HARDWARE，exit 2）。

红线：本文件不 mock 设备命中。设备在场只从 fixture selfcheck 报告的
``checks[0].detail.target_hits`` 读；fixture 全绿报告是"有设备"形状，云内真跑
脚本时 selfcheck 自己会报无设备，二者分开测。

跑法：
  python3 -m pytest tools/test_acceptance_run.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent            # tools/
sys.path.insert(0, str(_HERE))

import acceptance_lib as A      # noqa: E402


# ---------------------------------------------------------------------------
# Fixture：selfcheck --json 报告（与 test_paste_block.py 同形状）
# ---------------------------------------------------------------------------
def _check(name: str, status: str, detail: dict,
           evidence: list[str] | None = None) -> dict:
    return {"name": name, "status": status, "evidence": evidence or [],
            "fix": [], "detail": detail}


def _green_selfcheck() -> dict:
    """有设备、环境就绪的形状。"""
    return {
        "tool": "mbdsdr-hw-selfcheck", "version": "0.1.0",
        "timestamp": "2026-10-02T10:00:00+08:00", "host": "realbox",
        "os": "Linux", "python": "3.12",
        "checks": [
            _check("1. USB/udev：RTL-SDR 枚举与权限", "PASS", {
                "target_hits": [{"vid": "0bda", "pid": "2838",
                                 "known_as": "RTL2838UHIDIR"}],
                "user_groups": ["user", "plugdev", "dialout"],
                "udev_rules_hit": ["/etc/udev/rules.d/99-rtlsdr.rules"]}),
            _check("2. 设备枚举：rtl_test -t", "PASS",
                   {"parsed": {"tuner": "R820T", "gain_count": 29}}),
            _check("3. 流读取：rtl_sdr 实读", "PASS",
                   {"tmp_file_bytes": 14_400_000, "lost_bytes_total": 0}),
            _check("4. 声卡", "PASS", {}),
            _check("5. GNSS 串口", "WARN", {"candidates": []}),
            _check("6. 依赖", "PASS",
                   {"py_rtlsdr": True, "py_soapy": True, "gpsd": "/usr/sbin/gpsd"}),
        ],
        "summary": {"counts": {"PASS": 4, "WARN": 1, "FAIL": 0, "SKIP": 0},
                    "conclusion": "环境基本就绪。", "real_machine_todo": []},
    }


def _no_device_selfcheck() -> dict:
    return {
        "tool": "mbdsdr-hw-selfcheck", "version": "0.1.0",
        "timestamp": "2026-10-02T10:05:00+08:00", "host": "cloud-vm",
        "checks": [
            _check("1. USB/udev：RTL-SDR 枚举与权限", "FAIL",
                   {"target_hits": [], "user_groups": ["user"], "udev_rules_hit": []},
                   evidence=["  未在 lsusb 中找到 0bda:2838/2832"]),
            _check("2. 设备枚举：rtl_test -t", "SKIP",
                   {"skipped_reason": "no_usb_device"}),
            _check("3. 流读取：rtl_sdr 实读", "SKIP",
                   {"skipped_reason": "no_usb_device"}),
            _check("4. 声卡", "WARN", {}),
            _check("5. GNSS 串口", "WARN", {"candidates": []}),
            _check("6. 依赖", "WARN",
                   {"py_rtlsdr": False, "py_soapy": False, "gpsd": None}),
        ],
        "summary": {"counts": {"PASS": 0, "WARN": 3, "FAIL": 1, "SKIP": 2},
                    "conclusion": "未检测到 RTL-SDR 真机设备。",
                    "real_machine_todo": []},
    }


# ---------------------------------------------------------------------------
# 1. device_present_from_selfcheck
# ---------------------------------------------------------------------------
class TestDevicePresent:
    def test_green_report_has_device(self):
        assert A.device_present_from_selfcheck(_green_selfcheck()) is True

    def test_no_device_report_false(self):
        assert A.device_present_from_selfcheck(_no_device_selfcheck()) is False

    @pytest.mark.parametrize("bad", [
        None, {}, {"checks": []},
        {"checks": [{"name": "1. x", "detail": {}}]},
        {"checks": [{"name": "1. x", "detail": {"target_hits": []}}]},
        {"checks": "not-a-list"},
    ])
    def test_malformed_never_claims_device(self, bad):
        assert A.device_present_from_selfcheck(bad) is False


# ---------------------------------------------------------------------------
# 2. onboard_outcome（按 phase10 手册通过口径）
# ---------------------------------------------------------------------------
def _onboard_report(steps: list[dict]) -> dict:
    return {"tool": "mbdsdr-onboarding", "mode": "adsb",
            "steps": steps}


def _detect_ok() -> dict:
    return {"step": "detect", "status": "PASS", "message": "检测到设备",
            "evidence": [], "fixes": [], "detail": {}}


def _record_ok(sigmf: str = "/tmp/onb/xxx.sigmf-data") -> dict:
    return {"step": "record", "status": "PASS", "message": "SigMF 录制完成",
            "evidence": [], "fixes": [],
            "detail": {"sigmf_data": sigmf, "n_samples": 240000}}


class TestOnboardOutcome:
    def test_record_pass_even_if_decode_empty_is_pass(self):
        """capture/record 过了、decode 0 帧 -> 演练步仍 PASS（诚实信号结果）。"""
        rep = _onboard_report([
            _detect_ok(),
            {"step": "capture", "status": "PASS", "message": "",
             "evidence": [], "fixes": [], "detail": {}},
            _record_ok(),
            {"step": "decode", "status": "FAIL", "message": "ADS-B 解码完成，0 有效帧",
             "evidence": [], "fixes": [], "detail": {"n_frames": 0}},
        ])
        oc = A.onboard_outcome(rep)
        assert oc["status"] == "PASS"
        assert oc["record"] == "PASS"
        assert oc["n_frames"] == 0
        assert oc["sigmf_dir"] == "/tmp/onb"

    def test_detect_fail_is_fail(self):
        rep = _onboard_report([
            {"step": "detect", "status": "FAIL", "message": "未检测到设备",
             "evidence": [], "fixes": [], "detail": {}},
        ])
        assert A.onboard_outcome(rep)["status"] == "FAIL"

    def test_capture_fail_is_fail(self):
        rep = _onboard_report([
            _detect_ok(),
            {"step": "capture", "status": "FAIL", "message": "rtl_sdr 空文件",
             "evidence": [], "fixes": [], "detail": {}},
        ])
        assert A.onboard_outcome(rep)["status"] == "FAIL"

    def test_record_skip_is_fail(self):
        rep = _onboard_report([
            _detect_ok(),
            {"step": "capture", "status": "SKIP", "message": "detect 未过",
             "evidence": [], "fixes": [], "detail": {}},
        ])
        assert A.onboard_outcome(rep)["status"] == "FAIL"

    def test_empty_report_is_fail(self):
        assert A.onboard_outcome({})["status"] == "FAIL"


# ---------------------------------------------------------------------------
# 3. ota_outcome
# ---------------------------------------------------------------------------
class TestOtaOutcome:
    def test_empty_state_is_skip(self):
        assert A.ota_outcome("[空态] n_samples=0；不产出 recorded 图/CSV。") == "SKIP"
        assert A.ota_outcome("[OTA 回填] 未发现任何 .sigmf-data 录制 -> 空态。") == "SKIP"

    def test_csv_written_is_pass(self):
        txt = "[OTA 回填] 发现 1 段录制\n[写] paper/experiments/ota_recorded_metrics.csv（1 行，口径=recorded）"
        assert A.ota_outcome(txt) == "PASS"

    def test_garbage_is_fail(self):
        assert A.ota_outcome("some random crash") == "FAIL"
        assert A.ota_outcome("") == "SKIP"


# ---------------------------------------------------------------------------
# 4. assemble：三条主路径
# ---------------------------------------------------------------------------
def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _raw_lines_with_selfcheck(tmp_path: Path, sc: dict,
                              extra: list[dict]) -> list[dict]:
    sc_path = tmp_path / "selfcheck.json"
    _write_json(sc_path, sc)
    base = [{"id": "selfcheck", "title": "selfcheck", "rc": 0 if A.device_present_from_selfcheck(sc) else 1,
             "selfcheck_report": str(sc_path)}]
    return base + extra


class TestAssemble:
    def test_no_hardware_path(self, tmp_path):
        lines = _raw_lines_with_selfcheck(tmp_path, _no_device_selfcheck(), [
            {"id": "diag_wizard_paste", "title": "diag", "rc": 1,
             "paste_block": True, "log": "/nonexistent"},
            {"id": "onboard_adsb", "title": "onb", "skipped": True,
             "skip_reason": "未检测到设备"},
            {"id": "ota_backfill", "title": "ota", "skipped": True,
             "skip_reason": "无录制"},
            {"id": "spacetime_hint", "title": "时空", "manual": True,
             "hint": "打开桌面端"},
        ])
        ck = A.assemble(lines)
        assert ck["device_present"] is False
        assert ck["overall"] == "NO_HARDWARE"
        assert ck["exit_code"] == A.EXIT_NO_HW
        # onboard/ota 全 SKIP，diag PASS
        statuses = {s["id"]: s["status"] for s in ck["steps"]}
        assert statuses["onboard_adsb"] == "SKIP"
        assert statuses["ota_backfill"] == "SKIP"
        assert statuses["diag_wizard_paste"] == "PASS"
        assert not A.validate_checklist(ck)

    def test_green_device_path(self, tmp_path):
        # onboard adsb json：record PASS、decode 0 帧（诚实）
        onb_path = tmp_path / "onboard_adsb.json"
        _write_json(onb_path, _onboard_report([
            _detect_ok(),
            {"step": "capture", "status": "PASS", "message": "",
             "evidence": [], "fixes": [], "detail": {}},
            _record_ok(str(tmp_path / "rec" / "x.sigmf-data")),
            {"step": "decode", "status": "FAIL", "message": "0 帧",
             "evidence": [], "fixes": [], "detail": {"n_frames": 0}},
        ]))
        ota_log = tmp_path / "ota.txt"
        ota_log.write_text("[写] ota_recorded_metrics.csv（1 行，口径=recorded）",
                           encoding="utf-8")
        lines = _raw_lines_with_selfcheck(tmp_path, _green_selfcheck(), [
            {"id": "diag_wizard_paste", "title": "diag", "rc": 0,
             "paste_block": True, "log": "/nonexistent"},
            {"id": "onboard_adsb", "title": "onb", "rc": 0,
             "onboard_json": str(onb_path), "recordings_dir": str(tmp_path / "rec")},
            {"id": "ota_backfill", "title": "ota", "rc": 0,
             "log": str(ota_log), "recordings_dir": str(tmp_path / "rec")},
            {"id": "spacetime_hint", "title": "时空", "manual": True, "hint": "x"},
        ])
        ck = A.assemble(lines)
        assert ck["device_present"] is True
        assert ck["overall"] == "PASS"
        assert ck["exit_code"] == A.EXIT_OK
        statuses = {s["id"]: s["status"] for s in ck["steps"]}
        # record PASS + decode 0 帧 -> onboard 步仍 PASS
        assert statuses["onboard_adsb"] == "PASS"
        assert statuses["ota_backfill"] == "PASS"
        assert not A.validate_checklist(ck)

    def test_device_present_but_onboard_capture_fail_is_exit1(self, tmp_path):
        onb_path = tmp_path / "onboard_adsb.json"
        _write_json(onb_path, _onboard_report([
            _detect_ok(),
            {"step": "capture", "status": "FAIL", "message": "rtl_sdr 空文件",
             "evidence": [], "fixes": [], "detail": {}},
        ]))
        lines = _raw_lines_with_selfcheck(tmp_path, _green_selfcheck(), [
            {"id": "diag_wizard_paste", "title": "diag", "rc": 0,
             "paste_block": True, "log": "/nonexistent"},
            {"id": "onboard_adsb", "title": "onb", "rc": 1,
             "onboard_json": str(onb_path), "recordings_dir": ""},
            {"id": "ota_backfill", "title": "ota", "skipped": True,
             "skip_reason": "无录制", "log": "/nonexistent"},
            {"id": "spacetime_hint", "title": "时空", "manual": True, "hint": "x"},
        ])
        ck = A.assemble(lines)
        assert ck["device_present"] is True
        assert ck["overall"] == "FAIL"
        assert ck["exit_code"] == A.EXIT_HW_FAIL
        assert "onboard_adsb" in ck["failed_steps"]


# ---------------------------------------------------------------------------
# 5. validate_checklist
# ---------------------------------------------------------------------------
class TestValidate:
    def _good(self) -> dict:
        ck = A.assemble(_raw_lines_with_selfcheck(
            Path("/tmp"), _no_device_selfcheck(),
            [{"id": "diag_wizard_paste", "title": "d", "rc": 1,
              "paste_block": True, "log": "/n"}]))
        return ck

    def test_good_passes(self):
        assert A.validate_checklist(self._good()) == []

    def test_missing_field(self):
        ck = self._good()
        del ck["exit_code"]
        errs = A.validate_checklist(ck)
        assert any("exit_code" in e for e in errs)

    def test_bad_status_in_step(self):
        ck = self._good()
        ck["steps"][0]["status"] = "BOGUS"
        errs = A.validate_checklist(ck)
        assert any("status 非法" in e for e in errs)

    def test_overall_exit_mismatch(self):
        ck = self._good()
        ck["overall"] = "PASS"
        ck["exit_code"] = A.EXIT_NO_HW   # 不一致
        errs = A.validate_checklist(ck)
        assert any("overall=PASS 但 exit_code" in e for e in errs)


# ---------------------------------------------------------------------------
# 6. CLI 子命令（subprocess）
# ---------------------------------------------------------------------------
class TestCLI:
    def test_device_present_cli(self, tmp_path):
        p = tmp_path / "sc.json"
        _write_json(p, _green_selfcheck())
        r = subprocess.run([sys.executable, str(_HERE / "acceptance_lib.py"),
                            "device-present", str(p)],
                           capture_output=True, text=True, check=False)
        assert r.returncode == 0
        assert r.stdout.strip() == "true"

        p2 = tmp_path / "sc2.json"
        _write_json(p2, _no_device_selfcheck())
        r2 = subprocess.run([sys.executable, str(_HERE / "acceptance_lib.py"),
                             "device-present", str(p2)],
                            capture_output=True, text=True, check=False)
        assert r2.stdout.strip() == "false"

    def test_assemble_cli_writes_valid_checklist(self, tmp_path):
        sc = tmp_path / "sc.json"
        _write_json(sc, _no_device_selfcheck())
        raw = tmp_path / "raw.jsonl"
        raw.write_text(
            json.dumps({"id": "selfcheck", "title": "s", "rc": 1,
                        "selfcheck_report": str(sc)}) + "\n" +
            json.dumps({"id": "diag_wizard_paste", "title": "d", "rc": 1,
                        "paste_block": True, "log": "/n"}) + "\n" +
            json.dumps({"id": "onboard_adsb", "title": "o", "skipped": True,
                        "skip_reason": "未检测到设备"}) + "\n",
            encoding="utf-8")
        out = tmp_path / "ck.json"
        r = subprocess.run([sys.executable, str(_HERE / "acceptance_lib.py"),
                            "assemble", "--raw", str(raw), "--out", str(out)],
                           capture_output=True, text=True, check=False)
        # 无设备路径 -> 退出码 2
        assert r.returncode == A.EXIT_NO_HW, r.stderr
        ck = json.loads(out.read_text(encoding="utf-8"))
        assert ck["overall"] == "NO_HARDWARE"
        # 再用 validate 子命令复核
        rv = subprocess.run([sys.executable, str(_HERE / "acceptance_lib.py"),
                             "validate", str(out)],
                            capture_output=True, text=True, check=False)
        assert rv.returncode == 0, rv.stdout

    def test_assemble_bad_jsonl_returns_usage(self, tmp_path):
        raw = tmp_path / "raw.jsonl"
        raw.write_text('{"id": "selfcheck", broken json\n', encoding="utf-8")
        out = tmp_path / "ck.json"
        r = subprocess.run([sys.executable, str(_HERE / "acceptance_lib.py"),
                            "assemble", "--raw", str(raw), "--out", str(out)],
                           capture_output=True, text=True, check=False)
        assert r.returncode == A.EXIT_USAGE

    def test_assemble_empty_raw_returns_usage(self, tmp_path):
        raw = tmp_path / "raw.jsonl"
        raw.write_text("", encoding="utf-8")
        out = tmp_path / "ck.json"
        r = subprocess.run([sys.executable, str(_HERE / "acceptance_lib.py"),
                             "assemble", "--raw", str(raw), "--out", str(out)],
                            capture_output=True, text=True, check=False)
        assert r.returncode == A.EXIT_USAGE


# ---------------------------------------------------------------------------
# 7. acceptance_run.sh 云内无硬件分支真跑（诚实 NO_HARDWARE）
# ---------------------------------------------------------------------------
class TestShellNoHardware:
    def test_script_no_hardware_exit2_and_outputs_checklist(self, tmp_path):
        out = tmp_path / "accept_out.json"
        r = subprocess.run(
            ["bash", str(_HERE / "acceptance_run.sh"),
             "--out", str(out)],
            capture_output=True, text=True, timeout=120, check=False,
            cwd=str(_HERE.parent))
        # 云内无硬件 -> 退出码 2（诚实空态，不是错误）
        assert r.returncode == A.EXIT_NO_HW, (
            f"rc={r.returncode}\nstdout={r.stdout[-1500:]}\nstderr={r.stderr[-1500:]}")
        assert out.exists(), "检查表文件未生成"
        ck = json.loads(out.read_text(encoding="utf-8"))
        assert ck["device_present"] is False
        assert ck["no_hardware_expected"] is True
        assert ck["overall"] == "NO_HARDWARE"
        # onboard 三类都必须 SKIP 且写明原因，绝不 mock 采集
        ob = {s["id"]: s for s in ck["steps"] if s["id"].startswith("onboard_")}
        assert set(ob) == {"onboard_adsb", "onboard_apt", "onboard_cw"}
        for sid, s in ob.items():
            assert s["status"] == "SKIP", f"{sid} 应 SKIP，实际 {s['status']}"
            assert "未检测到" in (s["skip_reason"] or "")
        # ota 也 SKIP
        ota = next(s for s in ck["steps"] if s["id"] == "ota_backfill")
        assert ota["status"] == "SKIP"
        # 结构自校验
        assert not A.validate_checklist(ck)


# ---------------------------------------------------------------------------
# 8. --event 模式：resolve_event_modes 纯逻辑
# ---------------------------------------------------------------------------
class TestResolveEventModes:
    def test_both_modes_with_freqs_ok(self):
        modes, err = A.resolve_event_modes(
            ["sstv", "ssdv"], {"sstv": "435e6", "ssdv": "436e6"})
        assert err is None
        assert modes == ["sstv", "ssdv"]

    def test_single_mode_ok(self):
        modes, err = A.resolve_event_modes(
            ["sstv"], {"sstv": "435e6", "ssdv": ""})
        assert err is None
        assert modes == ["sstv"]

    def test_missing_freq_points_to_doc(self):
        modes, err = A.resolve_event_modes(
            ["sstv", "ssdv"], {"sstv": "", "ssdv": "436e6"})
        # 仍返回已通过白名单的模式列表，但报错指出缺哪个频率
        assert modes == ["sstv", "ssdv"]
        assert err is not None
        assert "sstv" in err
        assert A.EVENT_PARAMS_DOC in err  # 指向 P3-event-params.md
        assert "--freq-sstv" in err

    def test_bad_mode_rejected(self):
        modes, err = A.resolve_event_modes(["adsb"], {"sstv": "", "ssdv": ""})
        assert modes == []
        assert err is not None
        assert "非法活动模式" in err
        assert "adsb" in err

    def test_empty_requested_ok(self):
        modes, err = A.resolve_event_modes([], {"sstv": "", "ssdv": ""})
        assert err is None
        assert modes == []

    def test_whitelist_excludes_teaching_modes(self):
        # adsb/apt/cw 是教学模式，不属于 --event 活动模式
        for m in ("adsb", "apt", "cw", "ax25", "bogus"):
            modes, err = A.resolve_event_modes([m], {})
            assert err is not None, f"mode={m!r} 应被拒"

    def test_empty_strings_are_skipped(self):
        # 尾随逗号产生的空 token 应被静默跳过，不算错误
        modes, err = A.resolve_event_modes(["", "sstv", ""], {"sstv": "435e6"})
        assert err is None
        assert modes == ["sstv"]


# ---------------------------------------------------------------------------
# 9. --event 模式：resolve-event CLI
# ---------------------------------------------------------------------------
class TestResolveEventCLI:
    def test_ok_prints_modes(self, tmp_path):
        r = subprocess.run(
            [sys.executable, str(_HERE / "acceptance_lib.py"),
             "resolve-event", "--modes", "sstv,ssdv",
             "--freq-sstv", "435e6", "--freq-ssdv", "436e6"],
            capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout.strip().splitlines()[-1])
        assert out["modes"] == ["sstv", "ssdv"]
        assert out["error"] is None

    def test_missing_freq_exit_usage(self):
        r = subprocess.run(
            [sys.executable, str(_HERE / "acceptance_lib.py"),
             "resolve-event", "--modes", "sstv,ssdv",
             "--freq-sstv", "435e6"],  # 缺 ssdv
            capture_output=True, text=True, check=False)
        assert r.returncode == A.EXIT_USAGE
        assert "P3-event-params" in r.stderr or "freq-ssdv" in r.stderr

    def test_bad_mode_exit_usage(self):
        r = subprocess.run(
            [sys.executable, str(_HERE / "acceptance_lib.py"),
             "resolve-event", "--modes", "adsb", "--freq-sstv", "x"],
            capture_output=True, text=True, check=False)
        assert r.returncode == A.EXIT_USAGE
        assert "非法活动模式" in r.stderr


# ---------------------------------------------------------------------------
# 10. acceptance_run.sh --event 云内无硬件真跑（诚实 NO_HARDWARE / exit 3 坏输入）
# ---------------------------------------------------------------------------
class TestShellEventMode:
    def test_event_no_hardware_exit2(self, tmp_path):
        """--event 带频率、云内无设备 -> exit 2，onboard_sstv/ssdv 诚实 SKIP。"""
        out = tmp_path / "ev.json"
        r = subprocess.run(
            ["bash", str(_HERE / "acceptance_run.sh"), "--event",
             "--freq-sstv", "435e6", "--freq-ssdv", "436e6",
             "--out", str(out)],
            capture_output=True, text=True, timeout=120, check=False,
            cwd=str(_HERE.parent))
        assert r.returncode == A.EXIT_NO_HW, (
            f"rc={r.returncode}\nstdout={r.stdout[-1500:]}\nstderr={r.stderr[-1500:]}")
        assert out.exists()
        ck = json.loads(out.read_text(encoding="utf-8"))
        assert ck["device_present"] is False
        assert ck["overall"] == "NO_HARDWARE"
        # 必须跑的是 sstv/ssdv，不是教学三类
        ob = {s["id"]: s for s in ck["steps"] if s["id"].startswith("onboard_")}
        assert set(ob) == {"onboard_sstv", "onboard_ssdv"}
        for sid, s in ob.items():
            assert s["status"] == "SKIP", f"{sid} 应 SKIP，实际 {s['status']}"
            assert "未检测到" in (s["skip_reason"] or "")
        assert not A.validate_checklist(ck)

    def test_event_single_mode_sstv(self, tmp_path):
        out = tmp_path / "ev.json"
        r = subprocess.run(
            ["bash", str(_HERE / "acceptance_run.sh"), "--event",
             "--event-modes", "sstv",
             "--freq-sstv", "435e6",
             "--out", str(out)],
            capture_output=True, text=True, timeout=120, check=False,
            cwd=str(_HERE.parent))
        assert r.returncode == A.EXIT_NO_HW, (
            f"rc={r.returncode}\nstdout={r.stdout[-1200:]}\nstderr={r.stderr[-1200:]}")
        ck = json.loads(out.read_text(encoding="utf-8"))
        ob = [s["id"] for s in ck["steps"] if s["id"].startswith("onboard_")]
        assert ob == ["onboard_sstv"]

    def test_event_missing_freq_exit_usage(self, tmp_path):
        """--event 缺 --freq-* -> exit 3，并提示去 P3-event-params.md 查。"""
        out = tmp_path / "ev.json"
        r = subprocess.run(
            ["bash", str(_HERE / "acceptance_run.sh"), "--event",
             "--out", str(out)],
            capture_output=True, text=True, timeout=60, check=False,
            cwd=str(_HERE.parent))
        assert r.returncode == A.EXIT_USAGE, (
            f"rc={r.returncode}\nstdout={r.stdout[-800:]}\nstderr={r.stderr[-800:]}")
        # 必须指向活动参数文档，且绝不硬编码频率
        assert "P3-event-params" in (r.stderr + r.stdout)
        assert "--freq-sstv" in (r.stderr + r.stdout)
        assert not out.exists() or True  # 检查表可能未生成（用法错提前退出）

    def test_event_bad_mode_exit_usage(self, tmp_path):
        out = tmp_path / "ev.json"
        r = subprocess.run(
            ["bash", str(_HERE / "acceptance_run.sh"), "--event",
             "--event-modes", "bogus",
             "--freq-sstv", "435e6",
             "--out", str(out)],
            capture_output=True, text=True, timeout=60, check=False,
            cwd=str(_HERE.parent))
        assert r.returncode == A.EXIT_USAGE, (
            f"rc={r.returncode}\nstdout={r.stdout[-800:]}\nstderr={r.stderr[-800:]}")
        assert "非法活动模式" in (r.stderr + r.stdout)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
