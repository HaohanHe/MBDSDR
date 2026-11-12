# SPDX-License-Identifier: MIT
"""A03：沙箱结果截断/损坏不得报成成功（第二批）。

可达路径：stdout 超过 max_output_chars 被截断 → 结果标记还在但 JSON 被切掉。
旧行为 output=None 且 returncode=0 → success=True，结构化结果静默丢失。

反证：把解析失败改回 ``except Exception: pass`` 后本用例 success 变 True → 红。
"""
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.sandbox import Sandbox  # noqa: E402


def test_truncated_result_payload_is_not_success(tmp_path):
    """标记还在、JSON 载荷被切掉。"""
    sb = Sandbox(work_dir=str(tmp_path), timeout_seconds=30, max_output_chars=280)
    r = sb.execute('print("A" * 250)')

    assert r.return_code == 0
    assert r.success is False, "结果载荷被截断时不能报成功"
    assert "解析失败" in r.error


def test_truncation_that_swallows_marker_is_not_success(tmp_path):
    """截断把结果标记本身切掉，同样不能静默报成功。"""
    sb = Sandbox(work_dir=str(tmp_path), timeout_seconds=30, max_output_chars=200)
    r = sb.execute('print("A" * 300)')

    assert r.return_code == 0
    assert r.success is False
    assert "截断" in r.error


def test_normal_result_is_still_success(tmp_path):
    sb = Sandbox(work_dir=str(tmp_path), timeout_seconds=30, max_output_chars=200)
    r = sb.execute('print("ok")')
    assert r.success is True
    assert r.error == ""
