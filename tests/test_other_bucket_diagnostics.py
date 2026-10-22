# SPDX-License-Identifier: MIT
"""OTHER 桶剩余欠账的失败消息质量门禁（phase70 消息收敛）。

契约（承接 phase68/69，见 docs/learn/capability-map.md）：
- 诚实失败 ≠ 英文堆栈：失败消息必须中文、可操作、点名参数与下一步；
- git clone 的失败按模式分类成中文诊断，未收录模式保留退出码与原文；
- socket.timeout（"timed out"）经 clean_exc 映射为中文，不泄漏英文；
- 协议族工具（DMR/P25/DAB/HD Radio）失败时给中文诊断而非英文结构化转储；
- smoke 分类器把"当前无连接设备"诚实空态归 NEED_HW，不沉入 OTHER。
"""
import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from mbdsdr_ai.agent import MBDSDRAgent, _git_clone_failure_zh  # noqa: E402
from mbdsdr_ai.tool_registry import clean_exc  # noqa: E402
from tool_param_smoke import classify  # noqa: E402


@pytest.fixture(scope="module")
def reg():
    return MBDSDRAgent().tool_registry


# ── git_clone_repo：失败模式 → 中文诊断 ────────────────────────────
@pytest.mark.parametrize("stderr,expect", [
    ("fatal: unable to access 'https://example.org/x.git/': Could not resolve host: example.org",
     "无法解析仓库主机名"),
    ("fatal: unable to access 'https://example.org/x.git/': Failed to connect to example.org port 443",
     "连不上仓库主机"),
    ("remote: Repository not found.\nfatal: repository 'https://example.org/x.git/' not found",
     "仓库不存在或无权访问"),
    ("remote: Support for password authentication was removed.\nfatal: Authentication failed",
     "认证失败"),
    ("fatal: destination path 'repos/x' already exists and is not an empty directory.",
     "目标目录已存在且非空"),
    ("fatal: the remote end hung up unexpectedly", "传输中断"),
])
def test_git_clone_failure_zh_classifies_common_modes(stderr, expect):
    out = _git_clone_failure_zh(128, stderr)
    assert expect in out, out
    assert "128" in out, "退出码必须保留，便于人工排查"
    assert "Could not resolve" not in out and "hung up" not in out, \
        f"已知模式的英文原文不得转述给模型: {out}"


def test_git_clone_failure_zh_unclassified_keeps_detail_for_humans():
    """未收录模式：中文引导 + 退出码 + 末行原文（clean_exc 同款纪律）。"""
    out = _git_clone_failure_zh(42, "fatal: some novel git failure mode")
    assert "人工排查" in out
    assert "42" in out
    assert "some novel git failure mode" in out


def test_git_clone_failure_zh_empty_stderr():
    """stderr 为空也不许崩，消息仍以中文引导收头。"""
    out = _git_clone_failure_zh(1, "")
    assert "克隆失败" in out and "1" in out


def test_git_clone_repo_handler_uses_zh_diagnostic(reg, monkeypatch):
    """端到端：handler 的 CalledProcessError 分支走中文诊断，不泄漏 git 英文。"""

    def _fake_run(*a, **kw):
        raise subprocess.CalledProcessError(
            128, "git", stderr="fatal: unable to access 'https://example.org/': "
                               "Could not resolve host: example.org")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    r = reg.call("git_clone_repo", {"url": "https://example.org/x.git"})
    assert r.success is False
    assert "无法解析仓库主机名" in r.content
    assert "Could not resolve" not in r.content


# ── clean_exc：socket.timeout 映射 ────────────────────────────────
def test_clean_exc_maps_bare_timed_out():
    """socket.timeout 的 str(e) 是裸 "timed out"（gimbal/设备面超时分支）。"""
    out = clean_exc(TimeoutError("timed out"))
    assert "连接超时" in out
    assert "timed out" not in out


def test_clean_exc_maps_read_operation_timed_out():
    out = clean_exc(TimeoutError("The read operation timed out"))
    assert "读取超时" in out
    assert "timed out" not in out


# ── HD Radio：空 IQ 输入给可操作指引 ──────────────────────────────
@pytest.mark.parametrize("tool", ["hdradio_ofdm_demod", "hdradio_decode_iq"])
def test_hdradio_empty_iq_message_is_actionable(reg, tool):
    r = reg.call(tool, {"iq": []})
    assert r.success is False
    assert "iq" in r.content and "744" in r.content, r.content


# ── DMR / P25：失败给中文诊断，点名输入要求 ───────────────────────
def test_dmr_decode_short_input_names_requirement(reg):
    r = reg.call("dmr_decode", {"slot_dibits": [0, 1, 2, 3]})
    assert r.success is False
    assert "144" in r.content and "dibit" in r.content, r.content


def test_dmr_decode_misaligned_input_diagnostic(reg):
    """长度够但没同步：诊断点名同步字/制式，而不是甩英文 burst=unknown。"""
    r = reg.call("dmr_decode", {"slot_dibits": [1, 2] * 72})
    assert r.success is False
    assert "同步" in r.content and "4FSK" in r.content, r.content


def test_p25_decode_short_input_names_requirement(reg):
    r = reg.call("p25_decode", {"nid_dibits": [0, 1]})
    assert r.success is False
    assert "24" in r.content and "NID" in r.content, r.content


# ── DAB：失败给中文诊断（FSYNC / 校验） ───────────────────────────
def test_dab_eti_parse_garbage_frame_diagnostic(reg):
    """帧长不足/无同步：诊断点名 FSYNC 与 6144 字节要求，不再甩 "ok=False, nst=0"。"""
    r = reg.call("dab_eti_parse", {"frame": [0] * 100})
    assert r.success is False
    assert "FSYNC" in r.content and "6144" in r.content, r.content
    assert "ok=False" not in r.content


def test_dab_decode_iq_garbage_diagnostic(reg):
    r = reg.call("dab_decode_iq", {"data": [0] * 100})
    assert r.success is False
    assert "FSYNC" in r.content, r.content
    assert "no ETI FSYNC found" not in r.content


# ── smoke 分类器：无连接设备 = NEED_HW ────────────────────────────
def test_classifier_no_device_state_is_need_hw():
    """sdr_set_* 家族的诚实空态措辞必须归 NEED_HW，不沉入 OTHER。"""
    msg = "操作失败：当前无连接设备，请先 sdr_connect 或 sdr_open_iq_file"
    assert classify(msg, False, "") == "NEED_HW"
