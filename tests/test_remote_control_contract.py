# SPDX-License-Identifier: MIT
"""远程控制口径契约：文档承诺 == 实际命令集。

v1 只承诺 f/m/g/q/Q 五条命令；remote_control_v2 的扩展命令未接线，
必须返回 RPRT -3（未实现），不能假装成功。文档表格由本测试解析，
新增/删除命令必须同时改代码与 docs/cli_usage.md。
"""
from __future__ import annotations

import os
import re

from mbdsdr_ai.core.cli import build_parser
from mbdsdr_ai.remote_control import (
    CLOSE_CONNECTION, RemoteControl, RPRT_ERR, RPRT_OK, RPRT_UNSUPPORTED,
    SUPPORTED_COMMANDS,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOC = os.path.join(_ROOT, "docs", "cli_usage.md")


class _Backend:
    def __init__(self):
        self.freq = 100_000_000
        self.mode = "FM"
        self.gain = 10.0

    def get_freq(self):
        return self.freq

    def set_freq(self, hz):
        self.freq = hz
        return True

    def get_mode(self):
        return self.mode

    def set_mode(self, mode):
        self.mode = mode
        return True

    def get_gain(self):
        return self.gain

    def set_gain(self, db):
        self.gain = db
        return True


def _doc_commands() -> set[str]:
    """解析「远程控制命令（v1）」表格首列，归一成命令名集合。"""
    with open(_DOC, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"^## 远程控制命令（v1）\s*$(.*?)(?=^## )", text,
                  flags=re.MULTILINE | re.DOTALL)
    assert m, "docs/cli_usage.md 缺少「远程控制命令（v1）」小节"
    section = m.group(1)
    commands: set[str] = set()
    for line in section.splitlines():
        row = re.match(r"^\|\s*(.+?)\s*\|\s*.+?\s*\|\s*$", line.strip())
        if not row:
            continue
        cell = row.group(1).replace("`", "")
        if cell in ("命令", "---"):
            continue
        for alternative in re.split(r"\s*/\s*", cell):
            token = alternative.split()[0] if alternative.split() else ""
            if token:
                commands.add(token)
    return commands


def test_doc_promise_equals_supported_commands():
    assert _doc_commands() == set(SUPPORTED_COMMANDS)


def test_every_advertised_command_is_actually_handled():
    rc = RemoteControl(backend=_Backend())
    assert rc.process_line("f").strip() == "100000000"
    assert rc.process_line("f 145000000") == RPRT_OK
    assert rc.process_line("m") == "FM\n"
    assert rc.process_line("m USB") == RPRT_OK
    assert rc.process_line("g") == "10.0\n"
    assert rc.process_line("g 20") == RPRT_OK
    assert rc.process_line("q") == CLOSE_CONNECTION
    assert rc.process_line("Q") == CLOSE_CONNECTION


def test_v2_only_commands_return_explicit_not_implemented():
    rc = RemoteControl(backend=_Backend())
    for command in ("l", "L", "s", "u", "A", "v", "d"):
        assert rc.process_line(command) == RPRT_UNSUPPORTED, command


def test_arbitrary_unknown_command_keeps_generic_error():
    rc = RemoteControl(backend=_Backend())
    assert rc.process_line("bogus 1") == RPRT_ERR


def test_cli_help_mentions_only_v1_command_surface():
    help_text = build_parser().format_help()
    assert "f/m/g/q/Q" in help_text
    assert "--remote-port" in help_text
