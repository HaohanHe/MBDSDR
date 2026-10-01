#!/usr/bin/env python3
# SPDX-License-Identifier MIT
"""
tools/hw_selfcheck/test_selfcheck.py — 离线确定性测试。

不访问真实硬件、不跑真实子进程；通过 monkeypatch 注入假命令输出 / 假 /sys 树，
验证解析逻辑与“全缺失环境不 traceback”。

运行：
    cd <repo_root>
    python3 -m pytest tools/hw_selfcheck/test_selfcheck.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# 让本文件能 import 同目录的 selfcheck
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import selfcheck as sc  # noqa: E402


# ---------------------------------------------------------------------------
# 1. rtl_test 输出解析
# ---------------------------------------------------------------------------

FAKE_RTL_TEST_OUTPUT = """\
Found 1 device(s):
  0:  Realtek, RTL2838UHIDIR, SN: 00000001

Using device 0: Generic RTL2832U OEM
Found Rafael Micro R820T tuner
Supported gain values (29): 0.0 0.9 1.4 2.7 3.7 7.7 8.7 12.5 14.4 15.7 16.6 19.7 20.7 22.9 25.4 28.0 29.7 32.8 33.8 36.4 37.2 38.6 40.2 42.1 46.4 48.8 49.6 52.9 55.8
[R82XX] PLL not locked!
No E4000 tuner found, aborting.
"""


def test_parse_rtl_test_tuner_and_gains():
    p = sc.parse_rtl_test_output(FAKE_RTL_TEST_OUTPUT)
    assert p["tuner"] == "Rafael Micro R820T"
    assert p["gain_count"] == 29
    assert p["gain_list_db"][0] == "0.0"
    assert p["gain_list_db"][-1] == "55.8"
    assert len(p["devices"]) == 1
    assert p["devices"][0]["vendor"] == "Realtek"
    assert p["devices"][0]["product"] == "RTL2838UHIDIR"
    assert p["devices"][0]["serial"] == "00000001"


def test_parse_rtl_test_no_device():
    p = sc.parse_rtl_test_output("No supported devices found.\n")
    assert p.get("no_supported_devices") is True
    assert "tuner" not in p
    assert "gain_count" not in p


def test_parse_rtl_test_r828d_v4():
    out = (
        "Found 1 device(s):\n"
        "  0:  Realtek, RTL2838UHIDIR, SN: 00000001\n"
        "Using device 0: Generic RTL2832U OEM\n"
        "Found Rafael Micro R828D tuner\n"
        "RTL-SDR Blog V4 Detected\n"
        "Supported gain values (2): 0.0 49.6 \n"
    )
    p = sc.parse_rtl_test_output(out)
    assert p["tuner"] == "Rafael Micro R828D"
    assert p["gain_count"] == 2


# ---------------------------------------------------------------------------
# 2. rtl_sdr 丢包正则
# ---------------------------------------------------------------------------

def test_lost_bytes_regex_sum():
    blob = (
        "Reading samples in async mode...\n"
        "lost at least 128 bytes\n"
        "lost at least 256 bytes\n"
        "lost at least 0 bytes\n"  # 0 也会被捕获，但求和=384
        "User cancel, exiting...\n"
    )
    hits = sc._LOST_RE.findall(blob)
    total = sum(int(x) for x in hits)
    assert hits == ["128", "256", "0"]
    assert total == 384


def test_lost_bytes_regex_no_match():
    blob = "Reading samples in async mode...\nUser cancel, exiting...\n"
    assert sc._LOST_RE.findall(blob) == []


# ---------------------------------------------------------------------------
# 3. /sys USB 扫描（假树）
# ---------------------------------------------------------------------------

def test_scan_usb_via_sys_fake_tree(tmp_path):
    fake_root = tmp_path / "sys" / "bus" / "usb" / "devices"
    fake_root.mkdir(parents=True)

    d1 = fake_root / "1-1"
    d1.mkdir()
    (d1 / "idVendor").write_text("0bda\n")
    (d1 / "idProduct").write_text("2838\n")
    (d1 / "product").write_text("RTL2838UHIDIR\n")

    d2 = fake_root / "1-1.1"
    d2.mkdir()
    (d2 / "idVendor").write_text("046d\n")
    (d2 / "idProduct").write_text("c534\n")

    # d3 只有 idVendor 没有 idProduct，应被跳过
    d3 = fake_root / "usb1"
    d3.mkdir()
    (d3 / "idVendor").write_text("1d6b\n")

    found = sc.scan_usb_via_sys(str(fake_root))
    pairs = {(d["vid"], d["pid"]) for d in found}
    assert ("0bda", "2838") in pairs
    assert ("046d", "c534") in pairs
    assert ("1d6b", None) not in pairs
    # 命中目标设备时带 product 串
    rtl = next(d for d in found if d["vid"] == "0bda")
    assert "RTL2838UHIDIR" in rtl["product"]


def test_scan_usb_via_sys_missing_dir(monkeypatch):
    # /sys/bus/usb/devices 不存在时应返回空列表，不抛
    monkeypatch.setattr(sc.Path, "is_dir", lambda self: False)
    assert sc.scan_usb_via_sys() == []


# ---------------------------------------------------------------------------
# 4. 声卡解析
# ---------------------------------------------------------------------------

def test_parse_aplay_cards():
    sample = """\
**** List of PLAYBACK Hardware Devices ****
card 0: Intel [HDA Intel PCH], device 0: ALC256 Analog [ALC256 Analog]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
card 1: AOA [HDMI], device 3: HDMI 0 [HDMI 0]
  Subdevices: 1/1
"""
    cards = sc._parse_cards(sample)
    assert len(cards) == 2
    assert cards[0]["index"] == "0"
    assert cards[0]["short"] == "Intel"
    assert cards[0]["name"] == "HDA Intel PCH"
    assert cards[1]["short"] == "AOA"


# ---------------------------------------------------------------------------
# 5. NMEA 判定
# ---------------------------------------------------------------------------

def test_nmea_prefix_match():
    # 模拟 _try_read_nmea 的判定逻辑
    samples = [
        b"$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47\r\n",
        b"$GNRMC,...",
        b"$GNGSA,A,3,",
    ]
    for line in samples:
        assert line.startswith(sc.NMEA_PREFIXES), line[:20]


def test_nmea_rejects_garbage():
    garbage = [b"", b"NOISY DATA", b"AT+CGNSPWR=1\r\n", b"\x00\x01\x02"]
    for line in garbage:
        assert not line.startswith(sc.NMEA_PREFIXES)


# ---------------------------------------------------------------------------
# 6. 全缺失环境不 traceback（端到端 main()）
# ---------------------------------------------------------------------------

def test_main_no_hardware_no_traceback(monkeypatch, capsys):
    """模拟一台啥都没有的机器：所有命令缺失、无 /sys、无 /dev。"""
    # 所有 which 都返回 None
    monkeypatch.setattr(sc.shutil, "which", lambda name: None)
    # /sys/bus/usb/devices 不存在
    monkeypatch.setattr(sc.os.path, "isdir", lambda p: False)
    # glob 找不到任何 ttyUSB
    monkeypatch.setattr(sc.glob, "glob", lambda pat: [])
    # 用户组只有自己
    monkeypatch.setattr(sc, "current_user_groups", lambda: {"user"})
    # importlib 找不到 SDR 绑定
    monkeypatch.setattr(sc.importlib.util, "find_spec", lambda name: None)

    # 不抛异常即可
    rc = sc.main(["--json"])
    captured = capsys.readouterr()
    data = json.loads(captured.out)  # 必须是合法 JSON
    assert data["tool"] == "mbdsdr-hw-selfcheck"
    assert len(data["checks"]) == 6
    # 无硬件场景：第 2、3 项应为 SKIP
    statuses = {c["name"][:6]: c["status"] for c in data["checks"]}
    assert any(v == "SKIP" for v in statuses.values())
    # 至少有一个 FAIL（USB 没设备）
    assert data["summary"]["counts"]["FAIL"] >= 1
    # 退出码应是非 0（有 FAIL）
    assert rc != 0


def test_main_human_output_no_crash(monkeypatch, capsys):
    monkeypatch.setattr(sc.shutil, "which", lambda name: None)
    monkeypatch.setattr(sc.os.path, "isdir", lambda p: False)
    monkeypatch.setattr(sc.glob, "glob", lambda pat: [])
    monkeypatch.setattr(sc, "current_user_groups", lambda: {"user"})
    monkeypatch.setattr(sc.importlib.util, "find_spec", lambda name: None)

    rc = sc.main([])  # 人类可读模式
    out = capsys.readouterr().out
    assert "MBDSDR 真机环境自检" in out
    assert "结论：" in out
    assert rc != 0


# ---------------------------------------------------------------------------
# 7. summarize 逻辑
# ---------------------------------------------------------------------------

def test_summary_no_device_lists_todo():
    usb = sc.CheckResult("1. USB/udev：RTL-SDR 枚举与权限", status=sc.FAIL,
                         evidence=["未在 lsusb / /sys/bus/usb 中找到 0bda:2838/2832"],
                         detail={"target_hits": []})
    other = sc.CheckResult("x", status=sc.WARN)
    s = sc.summarize([usb, other])
    assert "未检测到" in s["conclusion"]
    assert len(s["real_machine_todo"]) >= 3


def test_summary_all_pass():
    results = [sc.CheckResult(f"chk{i}", status=sc.PASS) for i in range(3)]
    s = sc.summarize(results)
    assert s["counts"]["PASS"] == 3
    assert s["counts"]["FAIL"] == 0
    assert s["real_machine_todo"] == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
