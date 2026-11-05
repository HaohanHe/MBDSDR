# SPDX-License-Identifier: MIT
"""静噪口径守卫：余量刻度（margin）不得被当成绝对 dBFS 门。

背景（docs/limitations-register.md 附 H）：
桌面滑杆 ``-100..-20`` 是「比噪声基线高多少 dB」的余量；老代码把它原样
写进 ``SDRStatus.squelch_db`` 并当绝对门下发，导致 -50（6 dB 余量）被解释成
很高的绝对门——弱信号进不来、界面显示在接收却没声音。

本文件钉住 Python 侧的两个拦点：
1. ``SDRBackend.set_squelch`` 只记余量与来源，绝不下发绝对门；
2. ``ReceiveChain`` 构造/运行改用 ``BaselineSquelch``（余量换算），
   与 ``desktop/receive_pipeline.py`` 同一实现。
"""

import inspect
import os
import sys
from types import SimpleNamespace

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.receive_chain import ReceiveChain  # noqa: E402
from mbdsdr_ai.sdr_backend import (  # noqa: E402
    SQUELCH_APPLIED_BY_VALUES,
    SDRBackend,
    SDRStatus,
)

_FS_IN = 48000
_BLOCK = 4800


def _run_chain(squelch_db: float) -> dict:
    """先 10 帧纯噪声标定，再 30 帧弱 AM 信号（1 kHz 调制）；统计开门帧。"""
    rng = np.random.default_rng(11)
    chain = ReceiveChain(fs_in=_FS_IN, mode="AM", bandwidth=10000,
                         frequency_offset=0.0, squelch_db=squelch_db,
                         gain_db=0.0)
    # 前 10 帧只用于标定噪声底（BaselineSquelch 标定期保持开门，不计入统计）
    for _ in range(10):
        iq = (rng.standard_normal(_BLOCK)
              + 1j * rng.standard_normal(_BLOCK)).astype(np.complex64)
        chain.process(iq)
    # 标定完成后再看 6 帧纯噪声：余量门应保持关闭
    noise_only_open = 0
    for _ in range(6):
        iq = (rng.standard_normal(_BLOCK)
              + 1j * rng.standard_normal(_BLOCK)).astype(np.complex64)
        if np.any(chain.process(iq) != 0):
            noise_only_open += 1
    signal_open = 0
    for i in range(30):
        t = (np.arange(_BLOCK) + i * _BLOCK) / float(_FS_IN)
        env = 1.0 + 0.5 * np.sin(2 * np.pi * 1000.0 * t)
        amp = 1.5  # 弱信号：约高于噪声底 6~16 dB 之间
        iq = (amp * env + rng.standard_normal(_BLOCK)
              + 1j * rng.standard_normal(_BLOCK)).astype(np.complex64)
        if np.any(chain.process(iq) != 0):
            signal_open += 1
    return {"noise_open": noise_only_open, "signal_open": signal_open}


def test_margin_scale_gates_noise_but_opens_weak_signal():
    """同一噪声底：-50（6 dB 余量）必须开门，-20（16 dB）必须关门。

    反证：若仍把数字当绝对 dBFS，两个值都低于噪声 RMS（约 -14 dBFS），
    会「全开」——噪声帧 10/10 开门，本测试直接红。
    """
    r50 = _run_chain(-50.0)
    r20 = _run_chain(-20.0)

    assert r50["noise_open"] == 0, "6 dB 余量不该被噪声打开"
    assert r20["noise_open"] == 0, "16 dB 余量更不该被噪声打开"
    # 余量越小（6 dB）越松，开门帧必须严格多于 16 dB 余量。
    assert r50["signal_open"] > r20["signal_open"], (
        f"-50 应比 -20 开得更多，实际 {r50} vs {r20}")
    assert r20["signal_open"] == 0, "-20（16 dB）不该放过该弱信号"


def test_backend_set_squelch_never_downlinks_absolute_threshold():
    """余量口径下，绝对门字段必须保持 None，且链上收到的是余量原值。"""
    stub = SimpleNamespace(status=SDRStatus(), _receive_chain=None)

    class _Chain:
        def __init__(self):
            self.calls = []

        def set_squelch(self, db):
            self.calls.append(db)

    chain = _Chain()
    stub._receive_chain = chain

    assert SDRBackend.set_squelch(stub, -50.0) is True
    assert stub.status.squelch_db == -50.0
    assert stub.status.squelch_applied_by == "desktop-margin"
    assert stub.status.squelch_threshold_dbfs is None, "余量路径不得填绝对门"
    assert chain.calls == [-50.0], "下发的是余量刻度，不是换算后的 dBFS 门"


def test_applied_by_contract_and_backend_path():
    """``none``（默认）/``desktop-margin``（后端写入）取值正确；
    ``cpp-auto`` / ``cpp-manual`` 由 C++ 侧回写（本线不改，钉住字面量）。"""
    assert SQUELCH_APPLIED_BY_VALUES == (
        "desktop-margin", "cpp-auto", "cpp-manual", "none")

    status = SDRStatus()
    assert status.squelch_applied_by == "none"
    assert status.squelch_threshold_dbfs is None

    stub = SimpleNamespace(status=status, _receive_chain=None)
    SDRBackend.set_squelch(stub, -20.0)
    assert status.squelch_applied_by == "desktop-margin"

    for label in ("cpp-auto", "cpp-manual"):
        assert label in SQUELCH_APPLIED_BY_VALUES


def test_field_docs_declare_margin_vs_dbfs():
    """静态守卫：``squelch_db`` 说明必须写「余量」，``squelch_threshold_dbfs``
    必须写「dBFS」，防止未来把两者又混回一套。"""
    src = inspect.getsource(SDRStatus)

    def field_block(name: str) -> str:
        lines = src.splitlines()
        idx = next(i for i, line in enumerate(lines)
                   if line.strip().startswith(f"{name}:"))
        start = idx
        while start > 0 and lines[start - 1].strip().startswith("#"):
            start -= 1
        return "\n".join(lines[start:idx + 1])

    margin_block = field_block("squelch_db")
    absolute_block = field_block("squelch_threshold_dbfs")
    assert "余量" in margin_block, "squelch_db 必须在注释里写明是余量刻度"
    assert "dBFS" in absolute_block, (
        "squelch_threshold_dbfs 必须在注释里写明是绝对 dBFS 门")
