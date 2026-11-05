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


# ---------------------------------------------------------------- 编号闸（R/A）
import check_ring_labels as labels  # noqa: E402

DOC = ROOT / "docs" / "hw-acceptance.md"


def test_doc_has_no_same_number_different_meaning():
    assert labels.check_text(DOC.read_text(encoding="utf-8")) == []


def test_suites_still_spell_the_same_ring_names():
    """口径漂移守卫：环改名后，文档里的 R/A 对照必须跟着改。"""
    for n, keyword in labels.RUN_ALL_RINGS.items():
        assert keyword in run_all.RINGS[n - 1][1], f"run_all R{n} 改了名？"
    src = (ROOT / "scripts" / "hw_acceptance" / "acceptance_suite.py").read_text(
        encoding="utf-8")
    for keyword in labels.ACCEPTANCE_RINGS.values():
        assert keyword in src, f"acceptance_suite 里找不到「{keyword}」"


def test_same_numbering_written_identically_goes_red():
    """反证：设备链也写成 R5/R6（与主口径同号同字），必须判红。"""
    bad = DOC.read_text(encoding="utf-8") + "\n设备链视角：R5 云台、R6 串口 GNSS。\n"
    problems = labels.check_text(bad)
    assert any("R5" in p for p in problems), problems
    assert any("R6" in p for p in problems), problems


def test_row_meaning_swapped_goes_red():
    """反证：把 R5 那一行写成云台，必须判红。"""
    text = DOC.read_text(encoding="utf-8").replace(
        "| R5 | 卫星追踪与过境 | run_all |", "| R5 | 云台回读 | run_all |")
    problems = labels.check_text(text)
    assert problems, "R5 被写成云台却没判红"


def test_missing_a_block_goes_red():
    """反证：把 A 段整段删掉（两套合并成一套编号），必须判红。"""
    text = "\n".join(line for line in DOC.read_text(encoding="utf-8").splitlines()
                     if not line.startswith("| A"))
    problems = labels.check_text(text)
    assert problems, "A 段缺失却没判红"
