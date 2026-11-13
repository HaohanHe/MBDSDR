# SPDX-License-Identifier: MIT
"""host:port 只有一份解析：桌面入口与环境变量入口必须同结果。

旧行为：``MBDSDR_RTL_TCP=[::1]:1234`` 走 ``parse_rtl_tcp_env`` 的
``rpartition(":")``，主机被解析成带方括号的 ``"[::1]"``，连接必失败；
而界面 ``MainWindow._parse_host_port`` 解析成 ``"::1"`` 能连。同一个地址
两个入口两种结果。裸 IPv6 ``fe80::1`` 两边还会一起被切成
``("fe80:", 1)``（端口 1）。

现在两处都走 ``mbdsdr_ai.net_utils.parse_host_port``：
- ``[::1]:1234`` → ``("::1", 1234)``
- 裸 ``fe80::1`` → ``None``（拒绝，必须写 ``[fe80::1]:1234``）

反证：把 ``parse_host_port`` 的裸 IPv6 拒绝分支去掉，``test_bare_ipv6``
必红；把 ``parse_rtl_tcp_env`` 换回旧的 ``rpartition`` 实现，
``test_env_bracketed_ipv6`` 必红。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.net_utils import parse_host_port  # noqa: E402
from mbdsdr_ai.sdr_backend import parse_rtl_tcp_env  # noqa: E402


@pytest.mark.parametrize("text,expected", [
    ("[::1]:1234", ("::1", 1234)),
    ("[::1]", ("::1", 1234)),
    ("[fe80::1]:7355", ("fe80::1", 7355)),
    ("192.168.0.5:1234", ("192.168.0.5", 1234)),
    ("192.168.0.5", ("192.168.0.5", 1234)),
    (" 192.168.0.5:1234 ", ("192.168.0.5", 1234)),
    ("localhost", ("localhost", 1234)),
])
def test_accepts_explicit_forms(text, expected):
    assert parse_host_port(text) == expected


@pytest.mark.parametrize("text", [
    "fe80::1",          # 裸 IPv6：与 host:port 无法区分
    "::1",              # 同上
    "192.168.0.5:0",    # 端口越界
    "192.168.0.5:70000",
    "192.168.0.5:abc",
    "[::1]:abc",
    "[::1",             # 括号没闭合
    "a:b:c",            # 多个冒号且无方括号
    "",
    "   ",
])
def test_rejects_unparseable(text):
    assert parse_host_port(text) is None


def test_env_bracketed_ipv6():
    """环境变量入口必须得到与界面相同的裸主机名（旧实现是 '[::1]'）。"""
    got = parse_rtl_tcp_env("[::1]:1234,192.168.0.5", default_port=1234)
    assert got == [("::1", 1234), ("192.168.0.5", 1234)]


def test_env_bare_ipv6_skipped_with_warning(caplog):
    """裸 IPv6 不再被切成 ('fe80:', 1)，而是跳过并留下可查的 warning。"""
    with caplog.at_level("WARNING"):
        got = parse_rtl_tcp_env("fe80::1,10.0.0.2:1234")
    assert got == [("10.0.0.2", 1234)]
    assert any("fe80::1" in r.message for r in caplog.records), \
        "跳过的条目必须留下带原文的提示，不能静默丢掉配置"


def test_env_spyserver_default_port():
    assert parse_rtl_tcp_env("[::1]", default_port=7355) == [("::1", 7355)]
