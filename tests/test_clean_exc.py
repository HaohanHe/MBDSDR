# SPDX-License-Identifier: MIT
"""clean_exc 异常消息整形助手的行为测试（phase69 消息质量收敛）。

契约（docs/learn/capability-map.md「错误:即失败」约定）：
- 消息本体已中文 → 原样返回，不叠加 "ValueError: " 前缀；
- 已知英文异常（numpy 空数组/matmul/FFT/除零/连接拒绝等）→ 中文说明；
- KeyError → 点名缺少的参数；
- 未收录英文 → 中文类型名 + 原文（保留排查细节）。
"""
import numpy as np

from mbdsdr_ai.tool_registry import clean_exc


def test_chinese_message_passthrough():
    """本体已中文的异常消息原样返回，不叠加异常类型前缀。"""
    assert clean_exc(ValueError("样本不足以解调一个符号")) == "样本不足以解调一个符号"
    assert clean_exc(ValueError("TLE 行 2 字段不足（读到 1 个，需 ≥ 8）")) == \
        "TLE 行 2 字段不足（读到 1 个，需 ≥ 8）"


def test_numpy_empty_array_messages():
    """numpy 空数组类英文消息映射为中文，且点名「输入数据为空」。"""
    assert "输入数据为空" in clean_exc(
        ValueError("zero-size array to reduction operation maximum "
                   "which has no identity"))
    assert "输入数据为空" in clean_exc(
        IndexError("index 63 is out of bounds for axis 0 with size 0"))
    assert "FFT 输入点数为 0" in clean_exc(
        ValueError("Invalid number of FFT data points (0) specified."))


def test_numpy_shape_messages():
    """形状/维度类英文消息映射为中文说明。"""
    out = clean_exc(ValueError(
        "not enough values to unpack (expected 3, got 0)"))
    assert "期望 3 个、实到 0 个" in out
    assert "维度不匹配" in clean_exc(ValueError(
        "matmul: Input operand 1 has a mismatch in its core dimension 0"))


def test_division_and_connection_messages():
    """除零与连接类英文消息映射为中文说明。"""
    assert "除零" in clean_exc(
        ZeroDivisionError("integer division or modulo by zero"))
    assert "连接被拒绝" in clean_exc(
        ConnectionRefusedError(111, "Connection refused"))


def test_windows_network_errors_mapped_not_computation_error():
    """Windows 网络错误（[WinError NNNN]）映射为中文连接类说明。

    2026-11-02 销账 settings-audit 七-5：此前 WinError 串不命中 _EN_MSG_MAP，
    英文包裹串（<urlopen error …>）泄漏或被打成「计算错误」——明明是连接问题
    却提示查输入参数。WinError 10061/10060/10065/10054 与 Linux errno 三条对齐。
    """
    out = clean_exc(ConnectionRefusedError(
        10061, "No connection could be made because the target machine actively refused it"))
    assert "连接被拒绝" in out
    assert "WinError" not in out
    assert "计算错误" not in out

    # urllib 包裹形态（Windows 上拉 HTTP 时 str(e) 的真实样子）
    out2 = clean_exc(OSError(
        "<urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。>"))
    assert "连接被拒绝" in out2
    assert "urlopen" not in out2
    assert "计算错误" not in out2

    out3 = clean_exc(TimeoutError("[WinError 10060] 由于连接方在一段时间后没有正确答复或连接的主机没有反应，连接尝试失败。"))
    assert "连接超时" in out3
    assert "计算错误" not in out3

    out4 = clean_exc(OSError(
        "[WinError 10065] A socket operation was attempted to an unreachable host."))
    assert "网络不可达" in out4

    out5 = clean_exc(OSError("[WinError 10054] 远程主机强迫关闭了一个现有的连接。"))
    assert "连接被对端重置" in out5


def test_key_error_names_the_parameter():
    """KeyError 点名缺少的参数名。"""
    assert "frequency_hz" in clean_exc(KeyError("frequency_hz"))
    assert "缺少必需参数" in clean_exc(KeyError("frequency_hz"))


def test_unmapped_english_keeps_detail_with_chinese_label():
    """未收录的英文消息：中文类型名 + 原文，不丢排查细节。"""
    out = clean_exc(RuntimeError("some novel english failure"))
    assert "运行时错误" in out
    assert "some novel english failure" in out


def test_real_numpy_exception_end_to_end():
    """端到端：真实 numpy 异常经 clean_exc 后为中文且不含英文关键词。"""
    try:
        np.array([], dtype=float).max()
    except Exception as e:  # numpy raises ValueError
        out = clean_exc(e)
    assert "输入数据为空" in out
    assert "zero-size" not in out
