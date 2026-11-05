"""七环验收的诚实性契约（快测，不碰硬件、不出声、不发射）。

锁定三条不可退让的规则：

1. SKIP 原因码只有四个；写第五个码必须直接报错，不允许自由发挥。
2. ``skip_result`` 的结果永远是 ``SKIP`` + 码，不可能变成 ``PASS``。
3. ``run_all._normalize`` 必须把 ``UNVERIFIED`` 降级为 ``SKIP``（缺省 ``NO_DATA``），
   否则「没验的项」会从汇总里消失。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "hw_acceptance"))

import common  # noqa: E402
import run_all  # noqa: E402

CODE_SET = {"NO_HW", "NO_DATA", "NEEDS_OPERATOR", "NEEDS_TIME_WINDOW"}


def test_skip_codes_are_exactly_four():
    assert set(common.SKIP_CODES) == CODE_SET


def test_unknown_skip_code_rejected():
    with pytest.raises(ValueError):
        common.skip_result("R9", "x", "NO_POWER", "没有这个码")


def test_skip_result_is_never_pass():
    r = common.skip_result("R1", "连接与识别", "NO_HW", "无设备")
    assert r["status"] == common.SKIP
    assert r["skip_code"] == "NO_HW"
    assert r["status"] != common.PASS


def test_normalize_demotes_unverified_to_skip():
    r = run_all._normalize({
        "ring": "R6", "status": common.UNVERIFIED,
        "metrics": {}, "checks": [],
    })
    assert r["status"] == common.SKIP
    assert r["skip_code"] == "NO_DATA"


def test_normalize_backfills_missing_code_on_skip():
    r = run_all._normalize({
        "ring": "R2", "status": common.SKIP, "skip_code": "",
        "metrics": {}, "checks": [],
    })
    assert r["status"] == common.SKIP
    assert r["skip_code"] in CODE_SET


def test_normalize_keeps_pass_and_fail():
    assert run_all._normalize(
        {"ring": "R1", "status": common.PASS, "metrics": {}, "checks": []}
    )["status"] == common.PASS
    assert run_all._normalize(
        {"ring": "R1", "status": common.FAIL, "metrics": {}, "checks": []}
    )["status"] == common.FAIL
