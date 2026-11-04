# SPDX-License-Identifier: MIT
"""tests/test_decoder_capability_matrix.py — P5.4 能力矩阵文档一致性。

守两件事：
  1. 文档是生成的、不是手抄的：`scripts/decoder_capability_report.py`
     的输出必须与 `docs/learn/decoder-capability-matrix.md` **逐字一致**
     （改能力表忘刷新文档会直接红）；
  2. 表里登记的复跑路径必须真的存在：测试文件 / 脚本路径都在仓内，
     「复跑方式」不能是空话。
"""
import os
import sys
import importlib.util

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

SCRIPT = os.path.join(_ROOT, "scripts", "decoder_capability_report.py")


def _load_report():
    spec = importlib.util.spec_from_file_location("decoder_capability_report", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_doc_is_generated_and_in_sync():
    report = _load_report()
    with open(report.DOC_PATH, encoding="utf-8") as f:
        actual = f.read()
    assert actual == report.render(), (
        "capacity matrix 文档与生成结果不一致——跑 "
        "`python scripts/decoder_capability_report.py --write` 后再提交")


def test_every_capability_has_evidence_and_rerun():
    report = _load_report()
    rows = report.build_rows()
    for row in rows:
        assert row["evidence"].strip()
        assert row["rerun"].strip().startswith("python -m pytest ")


def test_rerun_paths_exist():
    report = _load_report()
    for row in report.build_rows():
        cmd = row["rerun"]
        paths = cmd.replace("python -m pytest", "").replace("-q", "").split()
        for p in paths:
            assert os.path.exists(os.path.join(_ROOT, p)), (
                f"{row['mode']} 的复跑路径不存在: {p}")


def test_truth_labels_cover_all_levels():
    report = _load_report()
    rows = report.build_rows()
    labels = {r["truth_label"] for r in rows}
    assert labels == {"真实", "部分", "缺样本"}, labels


def test_doc_has_row_per_mode():
    report = _load_report()
    with open(report.DOC_PATH, encoding="utf-8") as f:
        doc = f.read()
    for row in report.build_rows():
        assert f"`{row['mode']}`" in doc


def test_verify_covers_every_matrix_row():
    """`--verify` 必须真的跑整张表：十行 EVIDENCE + 三条 EXTRA_CHAINS。

    守卫点：文档写「只跑全部最小命令」，若 verify 漏掉 LRPT / DVB-S2 两行，
    这句话就成了假话。同一命令（ft4/wspr 共用）只跑一次。
    """
    report = _load_report()
    covered = {cmd for _mode, cmd in report.verify_commands()}
    for row in report.build_rows():
        assert row["rerun"] in covered, \
            f"{row['mode']} 的复跑命令未被 --verify 覆盖: {row['rerun']}"
    # 去重生效：同一条命令不得出现两次
    cmds = [cmd for _mode, cmd in report.verify_commands()]
    assert len(cmds) == len(set(cmds))
