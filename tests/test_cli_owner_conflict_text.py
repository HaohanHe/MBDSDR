#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""CLI 侧：占用被拒的两种结论必须是两句不同的话，且不另造判据。

产品侧结论只有一份，来自 ``sdr_backend.owner_conflict_text``；CLI 只做排版
（``错误:`` / ``建议:``）与退出码。本文件锁住四件事：

- 被占用：打印持有者 + pid + 「进程仍在运行」，下一步是关掉/释放那个程序；
- 登记不可读：打印「设备登记表不可用」「无法判断」，**不含 pid**，下一步是
  修或移除登记文件——与被占用那句一眼可分；
- 两者退出码同为 1（既有契约，docs/cli_usage.md），靠文案区分；
- 文案逐字等于 ``owner_conflict_text()``。反证：把 CLI 改成硬编码「设备忙」，
  第一条用例立刻红。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mbdsdr_ai import sdr_backend as sb
from mbdsdr_ai.core import cli
from mbdsdr_ai.core.errors import DeviceBusyError, OwnerRegistryUnavailableError

HOLDER = {"owner": "desktop", "pid": 43210, "device_id": "rtlsdr|serial:SN"}


class _FakeCtrl:
    """只实现 connect：失败在打开设备那一步，不需要任何其它能力。"""

    def __init__(self, exc: Exception):
        self._exc = exc

    def connect(self, device_id: str):
        raise self._exc


def _run(capsys, exc):
    code = cli.run(["--connect", "rtlsdr:SN"], controller=_FakeCtrl(exc))
    out = capsys.readouterr()
    return code, out.out, out.err


def _suggestion_lines(err: str):
    return [ln for ln in err.splitlines() if ln.startswith("建议: ")]


def test_busy_prints_holder_pid_and_release_step(capsys):
    # CLI 通道的文案：提示句应是 CLI 自己的枚举命令，不是 MCP 工具名。
    text = sb.owner_conflict_text(dict(HOLDER), channel=sb.OWNER_CHANNEL_CLI)
    code, out, err = _run(capsys, DeviceBusyError(text))

    assert code == 1
    assert out == ""
    # 逐字来自单一来源：CLI 不得二次加工或硬编码。
    assert f"错误: {text}" in err, err
    assert "--list-devices" in err, err
    assert "sdr_list_devices" not in err, err
    assert "43210" in err, err
    assert "进程仍在运行" in err, err
    assert "桌面端" in err, err
    lines = _suggestion_lines(err)
    assert lines and ("关闭" in lines[0] and "释放" in lines[0]), err
    # 不打印内部路径 / 登记文件名。
    assert "device_owner.json" not in err
    assert ".mbdsdr" not in err


def test_registry_unavailable_is_a_different_sentence(capsys):
    text = sb.owner_conflict_text({"owner": "", "pid": 0,
                                   "device_id": "rtlsdr|serial:SN",
                                   "reason": "JSON 读取/解析失败：JSONDecodeError"})
    code, out, err = _run(capsys, OwnerRegistryUnavailableError(text))

    assert code == 1
    assert out == ""
    assert f"错误: {text}" in err, err
    assert "设备登记表不可用" in err, err
    assert "无法判断" in err, err
    # 不能把「读不出来」说成「被别的程序占着」。
    assert "进程仍在运行" not in err
    assert "43210" not in err
    assert "登记表不可用" not in sb.owner_conflict_text(dict(HOLDER))
    lines = _suggestion_lines(err)
    assert lines and ("修复或移除" in lines[0]), err
    assert "device_owner.json" not in err


def test_channel_changes_only_the_executable_hint():
    """三个渠道的差异**只允许**出现在「用户接下来敲/点什么」那一句。

    反证：任何一处正文按渠道改写了（比如 CLI 版多一句解释），把提示句换回
    MCP 的那句后就不再与 MCP 版逐字相等，本条立刻红。
    """
    holders = [dict(HOLDER), sb.AmbiguousOwners("rtlsdr|serial:SN", ["a", "b"])]
    mcp_alt, mcp_amb = sb.owner_conflict_hints(sb.OWNER_CHANNEL_MCP)
    channels = (sb.OWNER_CHANNEL_MCP, sb.OWNER_CHANNEL_CLI,
                sb.OWNER_CHANNEL_DESKTOP)
    for holder in holders:
        texts = {ch: sb.owner_conflict_text(holder, channel=ch)
                 for ch in channels}
        assert len(set(texts.values())) == len(channels), texts
        for ch, text in texts.items():
            alt, amb = sb.owner_conflict_hints(ch)
            reverted = text.replace(alt, mcp_alt).replace(amb, mcp_amb)
            assert reverted == texts[sb.OWNER_CHANNEL_MCP], (ch, text)

    cli_busy = texts_for_busy(sb.OWNER_CHANNEL_CLI)
    desk_busy = texts_for_busy(sb.OWNER_CHANNEL_DESKTOP)
    assert "--list-devices" in cli_busy
    assert "sdr_list_devices" not in cli_busy
    assert "刷新设备列表" in desk_busy
    assert "sdr_list_devices" not in desk_busy and "--list-devices" not in desk_busy
    # 未知渠道按 MCP 处理，不凭空猜命令。
    assert (sb.owner_conflict_text(dict(HOLDER), channel="telegram")
            == sb.owner_conflict_text(dict(HOLDER)))


def texts_for_busy(channel: str) -> str:
    return sb.owner_conflict_text(dict(HOLDER), channel=channel)


def test_two_failures_do_not_share_wording(capsys):
    """同退出码 → 文案必须能区分，且下一步动作不同。"""
    busy_text = sb.owner_conflict_text(dict(HOLDER), channel=sb.OWNER_CHANNEL_CLI)
    unknown_text = sb.owner_conflict_text({"owner": "", "reason": "坏文件"})
    _, _, busy_err = _run(capsys, DeviceBusyError(busy_text))
    _, _, unknown_err = _run(capsys, OwnerRegistryUnavailableError(unknown_text))

    assert busy_err != unknown_err
    assert _suggestion_lines(busy_err) != _suggestion_lines(unknown_err)
    assert "登记表不可用" not in busy_err
    assert "进程仍在运行" not in unknown_err
