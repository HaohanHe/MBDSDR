# SPDX-License-Identifier: MIT
"""链路错误码上浮回归（无硬件，无需网络）。

钉住用户要求：超时 / 拒绝 / 半开 / 环形缓冲积压四类失败必须带稳定 code，
而不是只有一句「断开了」。code 逐字取自 ``mbdsdr_ai/error_catalog.py``。
"""
import os
import socket
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.error_catalog import ERRORS  # noqa: E402
from mbdsdr_ai.link_state import (  # noqa: E402
    CATEGORY_EOF,
    CATEGORY_IDLE,
    CATEGORY_OVERFLOW,
    CATEGORY_REFUSED,
    CATEGORY_RESET,
    CATEGORY_TIMEOUT,
    BUFFER_LEVEL_HIGH,
    error_code_for_buffer_level,
    error_code_for_category,
)
from mbdsdr_ai.rtltcp_client import RtlTcpClient  # noqa: E402
from mbdsdr_ai.spyserver_client import SpyServerClient  # noqa: E402


@pytest.mark.parametrize("category, code", [
    (CATEGORY_TIMEOUT, "link_timeout"),
    (CATEGORY_REFUSED, "link_refused"),
    (CATEGORY_RESET, "link_reset"),
    (CATEGORY_EOF, "link_half_open"),
    (CATEGORY_IDLE, "link_half_open"),
    (CATEGORY_OVERFLOW, "buffer_overflow"),
])
def test_category_maps_to_catalog_code(category, code):
    assert error_code_for_category(category) == code
    assert code in ERRORS, f"{code} 必须在 error_catalog 登记"


def test_unmapped_category_has_no_fake_code():
    """没有合适码的类别如实留空，不硬套一个「看起来相关」的码。"""
    assert error_code_for_category("dns") == ""
    assert error_code_for_category("handshake") == ""
    assert error_code_for_category("unknown") == ""


def test_buffer_high_maps_to_overflow_only():
    assert error_code_for_buffer_level(BUFFER_LEVEL_HIGH) == "buffer_overflow"
    assert error_code_for_buffer_level("ok") == ""
    assert error_code_for_buffer_level("") == ""


def _closed_port() -> int:
    """拿一个刚释放的本地端口：连接它应被拒绝（不会真的建连）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_rtl_tcp_refused_carries_code():
    c = RtlTcpClient("127.0.0.1", _closed_port(), idle_timeout=0.5)
    assert c.connect() is False
    assert c.error_category == CATEGORY_REFUSED
    assert c.error_code == "link_refused"
    assert "拒绝连接" in c.error


def test_spy_server_connect_failure_carries_matching_code():
    """SpyServer 连不上时必须带码，且码与类别严格一致。

    本机 Windows 上连一个刚释放的回环端口时报 timeout（不是 refused），
    所以这里不写死类别——只钉「有码且与类别对得上」，避免测试替平台背书。
    """
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    port = holder.getsockname()[1]
    try:
        c = SpyServerClient("127.0.0.1", port, timeout=0.5)
        with pytest.raises(Exception):
            c.start()
        assert c.error_category in (CATEGORY_REFUSED, CATEGORY_TIMEOUT)
        assert c.error_code == error_code_for_category(c.error_category)
        assert c.error_code in ("link_refused", "link_timeout")
    finally:
        holder.close()


def test_stale_carries_half_open_code():
    """半开（连接在但没数据）在客户端层就必须带 link_half_open。"""
    c = RtlTcpClient("127.0.0.1", 1234, idle_timeout=0.5)
    c._mark_stale("超过 0.5s 未收到 IQ 数据（心跳缺失），连接仍在但数据流已中断")
    assert c.error_category == CATEGORY_IDLE
    assert c.error_code == "link_half_open"


def test_troubleshooting_exposes_link_codes():
    """错误码必须真的能在 troubleshooting 里搜到，而不是只躺在目录里。"""
    sys.path.insert(0, os.path.join(_ROOT, "desktop"))
    from troubleshooting import build_remedies, validate_remedies
    remedies = build_remedies()
    codes = {r.code for r in remedies}
    for code in ("link_timeout", "link_refused", "link_half_open",
                 "link_reset", "buffer_overflow"):
        assert code in codes, f"{code} 没有接入 troubleshooting"
    half = next(r for r in remedies if r.code == "link_half_open")
    assert "连接仍在" in half.symptom
    assert "「连接」" in half.next_step
    assert validate_remedies([half]) == []
