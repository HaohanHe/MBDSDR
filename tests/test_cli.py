# SPDX-License-Identifier: MIT
"""
CLI 确定性测试：验证 argparse 解析与命令分发。

用注入的 controller（真实 SDRController + 调试信号源）避免依赖硬件；
关键是验证：错误参数退出码非零、无设备时给出建议、happy path 退出码 0。
"""

from __future__ import annotations

import pytest

from mbdsdr_ai.core import SDRController
from mbdsdr_ai.core.cli import build_parser, run


@pytest.fixture
def ctrl(tmp_path):
    c = SDRController(config_dir=str(tmp_path))
    yield c
    try:
        c.shutdown()
    except Exception:
        pass


def test_parser_accepts_all_flags():
    """argparse 能识别任务要求的全部开关。"""
    p = build_parser()
    args = p.parse_args([
        "--list-devices", "--list-audio",
        "--connect", "debug", "--freq", "100e6", "--demod", "FM",
        "--gain", "20", "--sample-rate", "2.4e6",
        "--audio-out", "0", "--start-audio", "--spectrum", "256",
        "--scan", "88e6", "108e6", "100e3",
        "--decode", "adsb", "--record", "/tmp/x.sigmf-data",
        "--remote-port", "7356", "--web-port", "8000",
        "--config", "/tmp/cfg",
    ])
    assert args.list_devices is True
    assert args.connect == "debug"
    assert args.freq == pytest.approx(100e6)
    assert args.demod == "FM"
    assert args.scan == pytest.approx([88e6, 108e6, 100e3])
    assert args.decode == "adsb"


def test_list_devices_exit_zero(ctrl, capsys):
    rc = run(["--list-devices"], controller=ctrl)
    assert rc == 0
    out = capsys.readouterr().out
    assert "debug" in out.lower()


def test_data_action_without_connect_errors(ctrl, capsys):
    """--spectrum 但未连接 -> 退出码非零 + 明确建议。"""
    rc = run(["--spectrum", "256"], controller=ctrl)
    assert rc == 2
    err = capsys.readouterr().err
    assert "未连接" in err
    assert "--connect" in err


def test_bad_demod_returns_error(ctrl, capsys):
    rc = run(["--connect", "debug", "--demod", "FOO"], controller=ctrl)
    assert rc == 1
    err = capsys.readouterr().err
    assert "不支持的解调模式" in err


def test_happy_path_spectrum(ctrl, capsys):
    rc = run([
        "--connect", "debug",
        "--freq", "98500000",
        "--demod", "WFM",
        "--spectrum", "256",
    ], controller=ctrl)
    assert rc == 0
    out = capsys.readouterr().out
    assert "频谱帧" in out
