#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
parse_hw_report.py 确定性离线测试
=================================

不碰真机、不联网。所有 fixture 都是**按 selfcheck.py / onboard.py 真实代码结构
手工构造的 JSON**（字段名逐字段核对自源码），覆盖：

  * 好 JSON 完整解析（设备在场 + 全链路 onboard）；
  * 聊天文本内多段 JSON 自动提取（混杂人类对话）；
  * 无设备 JSON（target_hits 空、检查 2/3 SKIP）；
  * 部分字段缺失容错（detail 空 dict、gpsd=null、缺 key）；
  * onboard 早退出裸 {"steps":[...]} 形状；
  * 纯垃圾输入 → ReportParseError，不崩。

跑法：
  python3 -m pytest tools/onboarding/test_parse_hw_report.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import parse_hw_report as P  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture：selfcheck 全绿（设备在场）
# ---------------------------------------------------------------------------
def good_selfcheck_json() -> dict:
    return {
        "tool": "mbdsdr-hw-selfcheck",
        "version": "0.1.0",
        "timestamp": "2026-10-02T09:00:00+08:00",
        "host": "realbox",
        "os": "Linux-6.x-x86_64",
        "python": "3.12.11",
        "checks": [
            {
                "name": "1. USB/udev：RTL-SDR 枚举与权限",
                "status": "PASS",
                "evidence": ["lsusb 枚举到 4 个 USB 设备", "  命中 USB 设备 0bda:2838"],
                "fix": [],
                "detail": {
                    "sys_scanned": ["1d6b:0002", "0bda:2838 RTL2838UHIDIR"],
                    "target_hits": [{
                        "sysdir": "1-1.2",
                        "vid": "0bda", "pid": "2838",
                        "product": "RTL2838UHIDIR",
                        "known_as": "RTL2838UHIDIR (目标真机)",
                    }],
                    "user_groups": ["user", "plugdev", "dialout"],
                    "udev_rules_hit": ["/etc/udev/rules.d/99-rtlsdr.rules"],
                },
            },
            {
                "name": "2. 设备枚举：rtl_test -t（tuner 型号 / 增益档数）",
                "status": "PASS",
                "evidence": ["  tuner 型号：Rafael Micro R820T"],
                "fix": [],
                "detail": {
                    "rc": 0,
                    "output_tail": "...",
                    "parsed": {
                        "devices": [{
                            "idx": "0", "vendor": "Generic",
                            "product": "RTL2838UHIDIR", "serial": "00000001",
                        }],
                        "tuner": "Rafael Micro R820T",
                        "gain_count": 29,
                        "gain_list_db": ["-9.9", "0.0", "14.7", "24.0", "49.6"],
                    },
                },
            },
            {
                "name": "3. 流读取：rtl_sdr 实读 ≤3s（2.4 MS/s）丢包统计",
                "status": "PASS",
                "evidence": ["  3 秒实读无丢包上报"],
                "fix": [],
                "detail": {"rc": 0, "output_tail": "", "tmp_file_bytes": 14400000,
                           "lost_bytes_total": 0},
            },
            {
                "name": "4. 声卡：aplay -l / arecord -l",
                "status": "PASS",
                "evidence": [],
                "fix": [],
                "detail": {
                    "playback_cards": [{"index": "0", "short": "PCH", "name": "HDA Intel PCH"}],
                    "capture_cards": [{"index": "0", "short": "PCH", "name": "HDA Intel PCH"}],
                    "dev_snd_entries": ["by-path", "pcmC0D0c", "timer"],
                },
            },
            {
                "name": "5. GNSS 串口：/dev/ttyUSB*,/dev/ttyACM*,/dev/serial/by-id",
                "status": "PASS",
                "evidence": [],
                "fix": [],
                "detail": {
                    "candidates": ["/dev/ttyUSB0"],
                    "probes": [{
                        "device": "/dev/ttyUSB0", "baud": 9600,
                        "ok": True, "sample": "$GPGGA,",
                        "reason": "matched NMEA line: $GPGGA,090000.00,...",
                    }],
                },
            },
            {
                "name": "6. 依赖：pyrtlsdr / SoapySDR / gpsd",
                "status": "PASS",
                "evidence": [],
                "fix": [],
                "detail": {"py_rtlsdr": True, "py_soapy": True,
                            "gpsd": "/usr/sbin/gpsd", "gpsd_client": "/usr/bin/cgps"},
            },
        ],
        "summary": {
            "counts": {"PASS": 6, "WARN": 0, "FAIL": 0, "SKIP": 0},
            "conclusion": "环境基本就绪：未发现阻断性问题。",
            "real_machine_todo": [],
        },
    }


def no_device_selfcheck_json() -> dict:
    """云 VM / 无硬件典型形状（与本机真跑结果一致）。"""
    return {
        "tool": "mbdsdr-hw-selfcheck",
        "version": "0.1.0",
        "timestamp": "2026-10-02T08:13:03+08:00",
        "host": "sandbox",
        "os": "Linux-6.x",
        "python": "3.12.11",
        "checks": [
            {"name": "1. USB/udev：RTL-SDR 枚举与权限", "status": "FAIL",
             "evidence": [], "fix": [],
             "detail": {"sys_scanned": [], "target_hits": [],
                        "user_groups": ["user"], "udev_rules_hit": []}},
            {"name": "2. 设备枚举：rtl_test -t（tuner 型号 / 增益档数）",
             "status": "SKIP", "evidence": [], "fix": [],
             "detail": {"skipped_reason": "no_usb_device"}},
            {"name": "3. 流读取：rtl_sdr 实读 ≤3s（2.4 MS/s）丢包统计",
             "status": "SKIP", "evidence": [], "fix": [],
             "detail": {"skipped_reason": "no_usb_device"}},
            {"name": "4. 声卡：aplay -l / arecord -l", "status": "WARN",
             "evidence": [], "fix": [], "detail": {}},
            {"name": "5. GNSS 串口：/dev/ttyUSB*,/dev/ttyACM*,/dev/serial/by-id",
             "status": "WARN", "evidence": [], "fix": [], "detail": {"candidates": []}},
            {"name": "6. 依赖：pyrtlsdr / SoapySDR / gpsd", "status": "WARN",
             "evidence": [], "fix": [],
             "detail": {"py_rtlsdr": False, "py_soapy": False,
                        "gpsd": None, "gpsd_client": None}},
        ],
        "summary": {
            "counts": {"PASS": 0, "WARN": 3, "FAIL": 1, "SKIP": 2},
            "conclusion": "未检测到 RTL-SDR 真机设备；...",
            "real_machine_todo": ["插入 RTL-SDR", "加入用户组"],
        },
    }


def good_onboard_json() -> dict:
    return {
        "tool": "mbdsdr-onboarding",
        "version": "0.1.0",
        "timestamp": "2026-10-02T09:00:05+08:00",
        "host": "realbox",
        "mode": "adsb",
        "params": {"freq_hz": 1090000000.0, "sample_rate_hz": 2400000.0,
                    "n_samples": 240000, "gain_db": 24.0},
        "steps": [
            {"step": "detect", "status": "PASS", "message": "检测到 RTL-SDR 设备",
             "evidence": [], "fixes": [],
             "detail": {"selfcheck_summary": {}, "selfcheck_checks": []}},
            {"step": "capture", "status": "PASS", "message": "采集完成",
             "evidence": [], "fixes": [],
             "detail": {"rc": 0, "raw_bytes_written": 480000, "lost_bytes_total": 0,
                        "actual_samples": 240000}},
            {"step": "record", "status": "PASS", "message": "SigMF 录制完成",
             "evidence": [], "fixes": [],
             "detail": {"sigmf_data": "/x/20261002.sigmf-data",
                        "sigmf_meta": "/x/20261002.sigmf-meta",
                        "n_samples": 240000, "duration_s": 0.1}},
            {"step": "decode", "status": "PASS", "message": "ADS-B 解码完成，3 有效帧",
             "evidence": [], "fixes": [],
             "detail": {"n_samples": 240000, "mode": "adsb",
                        "decoded_frames": [{"icao": "AABBCC"}], "n_frames": 3}},
            {"step": "output", "status": "PASS", "message": "产物落盘完成：4 个文件",
             "evidence": [], "fixes": [],
             "detail": {"artifacts": ["/x/a_messages.txt", "/x/a_messages.json",
                                      "/x/spectrum.png", "/x/manifest.json"],
                        "out_dir": "/x"}},
        ],
    }


# ---------------------------------------------------------------------------
# 1. 好 JSON 完整解析
# ---------------------------------------------------------------------------
def test_good_selfcheck_full_parse():
    sc = P.analyze_selfcheck(good_selfcheck_json())
    dev = sc["device"]
    assert dev["present"] is True
    assert dev["hits"][0]["vid"] == "0bda"
    assert dev["hits"][0]["known_as"].startswith("RTL2838UHIDIR")
    assert dev["tuner"] == "Rafael Micro R820T"
    assert dev["gain_count"] == 29
    assert dev["lost_bytes_total"] == 0
    assert dev["has_udev_rule"] is True
    assert dev["in_required_groups"] is True
    # 序列号模糊化：00000001 -> 000***
    assert dev["serials"] == ["000***"]
    # GNSS
    assert sc["gnss"]["nmea_found"] is True
    assert sc["gnss"]["candidates"] == ["/dev/ttyUSB0"]
    # 依赖
    assert sc["deps"]["missing"] == []
    assert sc["deps"]["gpsd"] == "/usr/sbin/gpsd"


def test_good_onboard_full_parse():
    ob = P.analyze_onboard(good_onboard_json())
    assert ob["mode"] == "adsb"
    assert ob["params"]["freq_hz"] == 1090000000.0
    statuses = {s["step"]: s["status"] for s in ob["steps"]}
    assert statuses == {"detect": "PASS", "capture": "PASS", "record": "PASS",
                        "decode": "PASS", "output": "PASS"}
    # decode 步抽出 n_frames
    dec = next(s for s in ob["steps"] if s["step"] == "decode")
    assert dec["n_frames"] == 3
    # output 步抽出产物清单
    assert len(ob["artifacts"]) == 4
    assert ob["out_dir"] == "/x"


def test_device_ready_recommendation():
    sc = P.analyze_selfcheck(good_selfcheck_json())
    ob = P.analyze_onboard(good_onboard_json())
    recs = P.build_recommendations(sc, ob)
    joined = " ".join(recs)
    # 设备就绪 → 建议直接跑 adsb 捕获
    assert any("可直接跑" in r and "adsb" in r for r in recs)
    # 全链路通过 → 提示产物落盘
    assert "全链路通过" in joined


# ---------------------------------------------------------------------------
# 2. 聊天文本多段 JSON 提取
# ---------------------------------------------------------------------------
def test_multiple_json_in_chat_text_extraction():
    sc_txt = json.dumps(good_selfcheck_json(), ensure_ascii=False)
    ob_txt = json.dumps(good_onboard_json(), ensure_ascii=False)
    chat = (
        "你好，刚在真机上跑了两条命令，结果贴下面：\n"
        "=== selfcheck ===\n" + sc_txt + "\n"
        "=== onboard ===\n" + ob_txt + "\n"
        "麻烦看看能不能开始收 ADS-B，谢谢！\n"
    )
    blocks = P.extract_json_objects(chat)
    # 两个顶层对象都被抽出
    assert len(blocks) == 2
    assert P.classify_block(blocks[0]) == "selfcheck"
    assert P.classify_block(blocks[1]) == "onboard"

    sc, ob, allb = P.parse_text(chat)
    assert sc is not None and ob is not None
    assert sc["device"]["present"] is True
    assert ob["mode"] == "adsb"


def test_extract_handles_braces_inside_strings():
    # 字符串值里含花括号，不应破坏配对
    text = '前缀 {"a": "含 } } { 花括号的字符串", "b": {"c": 1}} 后缀'
    objs = P.extract_json_objects(text)
    assert len(objs) == 1
    assert objs[0]["a"].startswith("含")
    assert objs[0]["b"]["c"] == 1


# ---------------------------------------------------------------------------
# 3. 无设备 JSON
# ---------------------------------------------------------------------------
def test_no_device_selfcheck():
    sc = P.analyze_selfcheck(no_device_selfcheck_json())
    assert sc["device"]["present"] is False
    assert sc["device"]["hits"] == []
    # gpsd=null -> missing
    assert "gpsd 守护进程" in sc["deps"]["missing"]
    assert "python 绑定 rtlsdr (pyrtlsdr)" in sc["deps"]["missing"]
    # 声卡 detail 为空 dict 也不崩
    assert sc["soundcard"]["playback_cards"] == []

    recs = P.build_recommendations(sc, None)
    joined = " ".join(recs)
    assert "硬件未就绪" in joined
    assert "plugdev,dialout" in joined


# ---------------------------------------------------------------------------
# 4. 部分字段缺失容错
# ---------------------------------------------------------------------------
def test_partial_fields_missing_tolerance():
    # 缺 checks、summary、detail 全空
    weird = {"tool": "mbdsdr-hw-selfcheck", "checks": [
        {"name": "1. USB/udev：RTL-SDR 枚举与权限", "status": "FAIL",
         "evidence": [], "fix": [], "detail": {}},
    ], "summary": {}}
    sc = P.analyze_selfcheck(weird)  # 不应抛
    assert sc["device"]["present"] is False
    assert sc["device"]["hits"] == []
    assert sc["counts"] == {}  # summary.counts 缺失 -> {}

    # onboard：只有裸 steps，没有外层信封（detect 失败早退出形状）
    bare = {"steps": [
        {"step": "detect", "status": "FAIL", "message": "未检测到 RTL-SDR 设备",
         "evidence": [], "fixes": [], "detail": {}},
    ]}
    assert P.classify_block(bare) == "onboard"
    ob = P.analyze_onboard(bare)
    assert ob["tool"] is None  # 无外层信封
    assert ob["mode"] is None
    assert ob["steps"][0]["status"] == "FAIL"


def test_bare_steps_early_exit():
    chat = "出错了：\n" + json.dumps({"steps": [
        {"step": "detect", "status": "FAIL", "message": "未检测到设备",
         "evidence": [], "fixes": [], "detail": {}}
    ]}, ensure_ascii=False) + "\n为什么？"
    sc, ob, blocks = P.parse_text(chat)
    assert sc is None  # 这段里没有 selfcheck
    assert ob is not None
    recs = P.build_recommendations(sc, ob)
    assert any("detect 步失败" in r for r in recs)


# ---------------------------------------------------------------------------
# 5. 纯垃圾输入报错
# ---------------------------------------------------------------------------
def test_pure_garbage_raises():
    with pytest.raises(P.ReportParseError):
        P.parse_text("你好，今天天气不错，没有任何 JSON。\n$ lsusb\nbash: lsusb: command not found\n")


def test_empty_text_raises():
    with pytest.raises(P.ReportParseError):
        P.parse_text("")


def test_only_arrays_not_objects():
    # 只有 JSON 数组 []，没有对象 -> 也应报错（本工具只解析 {...}）
    with pytest.raises(P.ReportParseError):
        P.parse_text("[1,2,3] 这是个数组不是对象")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
