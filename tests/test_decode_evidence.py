# SPDX-License-Identifier: MIT
"""D2 解码证据分级：可溯源守卫（不抄第二份结论表）。

口径（唯一定义在 `mbdsdr_ai.decode_router`）：
  ota_golden     有真实空口金标样本 + 测试对拍；
  synthetic_only 只有仓内合成回环测试；
  no_sample      连样本都没有，只按协议数学性质钉扎。

本文件**不再手抄「模式 → 等级」的第二份表**——那只是把结论重复一遍，
测试会跟着错误结论一起变绿。改为检查能力表自己声明的 `evidence_source`
（相对仓库根的测试/夹具路径）是否真实存在，以及等级与来历的硬约束：

  1. 每行必须给出非空的 `evidence_source`，且每个路径都在仓内存在；
  2. 每个模式至少能指到一个仓内测试文件（`test_*.py` / `*_test.py`）；
  3. `ota_golden` 行必须 `truth_source == "external-truth"`，且引用的
     golden 测试文件/金标清单存在；
  4. `no_sample` 不得被 `capability_matrix()` 显示为 `supported=True`；
     当前库存里没有任何模式是 `no_sample`（11 个模式都能指到仓内测试/夹具），
     这条库存事实也显式断言，防止以后悄悄回流。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai import decode_router as dm  # noqa: E402

ALLOWED = {"ota_golden", "synthetic_only", "no_sample"}
PKG_DIR = os.path.dirname(os.path.abspath(dm.__file__))
TEST_ROOTS = ("tests/", "mbdsdr_ai/tests/")


def _is_test_file(rel_path: str) -> bool:
    name = os.path.basename(rel_path)
    return rel_path.startswith(TEST_ROOTS) and (
        name.startswith("test_") or name.endswith("_test.py"))


def test_evidence_vocabulary_is_exactly_the_three_levels():
    assert set(dm.EVIDENCE_LEVELS) == ALLOWED
    assert dm.EVIDENCE_OTA_GOLDEN == "ota_golden"
    assert dm.EVIDENCE_SYNTHETIC_ONLY == "synthetic_only"
    assert dm.EVIDENCE_NO_SAMPLE == "no_sample"


def test_every_capability_has_traceable_evidence_source():
    """每行的每条依据都必须是仓内真实存在的路径，且至少含一个测试文件。"""
    for mode, cap in dm.DIGITAL_MODE_CAPABILITIES.items():
        assert cap.evidence in ALLOWED, f"{mode} evidence={cap.evidence!r} 非法"
        assert cap.evidence_source, (
            f"{mode} 没给 evidence_source：证据等级必须有仓内可溯源依据")
        for rel in cap.evidence_source:
            assert not os.path.isabs(rel), (
                f"{mode} 的 evidence_source 必须相对仓库根: {rel}")
            assert os.path.exists(os.path.join(_ROOT, rel)), (
                f"{mode} 引用的依据不存在: {rel}")
        assert any(_is_test_file(rel) for rel in cap.evidence_source), (
            f"{mode} 没有任何测试文件作为依据（只有夹具/文档不算可复跑证据）: "
            f"{cap.evidence_source}")


def test_ota_golden_rows_are_external_truth_with_existing_golden():
    """金标行：来历必须是 external-truth，且点名了仓内 golden 测试/清单。"""
    golden_rows = [
        (mode, cap) for mode, cap in dm.DIGITAL_MODE_CAPABILITIES.items()
        if cap.evidence == dm.EVIDENCE_OTA_GOLDEN
    ]
    assert golden_rows, "没有任何 ota_golden 行：ADS-B/FT8 的金标测试去哪了？"
    for mode, cap in golden_rows:
        assert cap.truth_source == "external-truth", (
            f"{mode} 标 ota_golden 但来历是 {cap.truth_source}")
        named = [rel for rel in cap.evidence_source
                 if "golden" in os.path.basename(rel).lower()]
        assert named, f"{mode} 标 ota_golden 却没引用任何 golden 测试/清单"
        for rel in named:
            assert os.path.exists(os.path.join(_ROOT, rel)), (
                f"{mode} 引用的 golden 文件不存在: {rel}")


def test_synthetic_only_rows_do_not_claim_external_truth():
    for mode, cap in dm.DIGITAL_MODE_CAPABILITIES.items():
        if cap.evidence != dm.EVIDENCE_SYNTHETIC_ONLY:
            continue
        assert cap.truth_source != "external-truth", (
            f"{mode} 只有合成证据，却把来历写成 external-truth")


def test_rtl433_is_registered_into_the_same_table():
    cap = dm.DIGITAL_MODE_CAPABILITIES.get("rtl433")
    assert cap is not None, "rtl_433 未登记进能力表"
    assert cap.mode == "rtl433"
    assert "rtl433" in cap.dependency
    assert dm.DIGITAL_MODE_HANDLERS.get("rtl433") is dm._route_rtl433
    assert os.path.exists(os.path.join(PKG_DIR, cap.impl))


def test_matrix_exposes_evidence_and_supported_flag():
    rows = {r["mode"]: r for r in dm.capability_matrix()}
    assert set(rows) == set(dm.DIGITAL_MODE_CAPABILITIES)
    for mode, row in rows.items():
        cap = dm.DIGITAL_MODE_CAPABILITIES[mode]
        assert row["evidence"] in ALLOWED
        assert row["evidence"] == cap.evidence
        assert row["evidence_source"] == list(cap.evidence_source)
        assert row["supported"] is dm.evidence_supported(row["evidence"])


def test_no_sample_rule_is_never_supported():
    """规则本身的直接单元断言（不依赖任何具体模式）。"""
    assert dm.evidence_supported(dm.EVIDENCE_NO_SAMPLE) is False
    assert dm.evidence_supported(dm.EVIDENCE_SYNTHETIC_ONLY) is True
    assert dm.evidence_supported(dm.EVIDENCE_OTA_GOLDEN) is True


def test_no_mode_is_currently_no_sample():
    """当前库存事实：11 个模式都能指到仓内金标/合成测试，无一「零样本」。

    这条是显式快照而不是空转用例——若以后新增一个真的连样本都没有的模式，
    把它标成 no_sample 后本断言会红，必须在同一提交里说明依据并更新此处；
    这样既不会静默回流，也不留一个永远为真的循环断言。
    """
    no_sample = sorted(
        mode for mode, cap in dm.DIGITAL_MODE_CAPABILITIES.items()
        if cap.evidence == dm.EVIDENCE_NO_SAMPLE)
    assert no_sample == [], (
        "以下模式被标 no_sample，但能力表已要求逐行给出仓内测试依据；"
        f"若确属零样本请在此登记原因: {no_sample}")
