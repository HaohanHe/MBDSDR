# SPDX-License-Identifier: MIT
"""rtl_tcp 协议没有的控制必须显式"不支持"（2026-11-11，报告 #9）。

旧行为：`RtlTcpDeviceAdapter` 的 AGC/直采/偏置/Offset 是空操作却 return True；
`RtlTcpBackend` 继承基类默认实现，带宽/AGC 也直接 return True。面板显示
「已生效」，远端 rtl_tcp 一个字节都没收到。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / "desktop"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from mbdsdr_ai.rtltcp_client import RtlTcpBackend, RtlTcpDeviceAdapter  # noqa: E402


class _FakeClient:
    """只记录下发，不碰 socket。"""

    def __init__(self, agc_ok: bool = True):
        self.agc_calls = []
        self.agc_ok = agc_ok

    def set_agc(self, enabled: bool) -> bool:
        self.agc_calls.append(bool(enabled))
        return self.agc_ok


def _backend(agc_ok: bool = True):
    b = RtlTcpBackend.__new__(RtlTcpBackend)
    b._client = _FakeClient(agc_ok)
    b.status = SimpleNamespace(error="")
    return b


def test_backend_reports_bandwidth_as_unsupported():
    b = _backend()
    assert b._apply_bandwidth(300_000) is False
    assert "没有带宽命令" in b.status.error


def test_backend_bias_tee_and_direct_sampling_are_unsupported():
    b = _backend()
    assert b.set_bias_tee(True) is False
    assert "偏置供电" in b.status.error
    b.status.error = ""
    assert b.set_direct_sampling("q") is False
    assert "直采" in b.status.error


def test_backend_offset_tuning_probe_is_false_and_set_refuses():
    b = _backend()
    assert b.supports_offset_tuning() is False
    assert b.set_offset_tuning(True) is False
    assert "Offset Tuning" in b.status.error


def test_backend_agc_only_claims_success_when_frame_really_sent():
    b = _backend(agc_ok=True)
    assert b._apply_agc(True) is True
    assert b._client.agc_calls == [True]
    b2 = _backend(agc_ok=False)
    assert b2._apply_agc(True) is False
    assert "AGC 下发失败" in b2.status.error


def test_adapter_no_longer_pretends_success():
    a = RtlTcpDeviceAdapter.__new__(RtlTcpDeviceAdapter)
    a._client = _FakeClient()
    assert a.supports_agc_mode is False
    assert a.supports_direct_sampling is False
    assert a.supports_bias_tee is False
    assert a.supports_offset_tuning is False
    assert a.set_agc_mode(True) is False
    assert a.set_direct_sampling("q") is False
    assert a.set_bias_tee(True) is False
    assert a.set_offset_tuning(True) is False
    # 一个字节都不许发给远端（不冒充"已下发"）
    assert a._client.agc_calls == []
