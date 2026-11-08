# SPDX-License-Identifier: MIT
"""LRPT 图像内容级真值对拍（队列 #10②）——独立 meteor_decode 像素基准。

fixture：`tests/golden/lrpt_meteor_content_golden.json`。每条记录给一段
MCU 熵数据（entropy_hex，输入）与独立解码器 meteor_decode（dbdexter-dev，
MIT，commit e7ca456）对**同一熵数据**解出的像素（pixels_hex，真值）。
我方 lrpt_image 链解 entropy_hex，逐块与真值对拍。

为什么是内容级而非协议级：协议层（VCDU/M_PDU/包结构/DC 系数）已由
test_lrpt_meteor_real_golden 钉死；但像素域曾藏两处真 bug——zigzag 表误填
逆置换、_blocks_to_spatial 双重 +128 饱和——round-trip 自洽测试全绿却解出
垃圾图。本协议级断言（系数/计数/几何）照不到像素，唯有独立实现的像素
真值能揪出。本测试即该真值回归。

来历：pixels 是 meteor_decode 输出（external-truth），非我方编码器自生成；
输入熵数据来自 TU Berlin 真录 meteor_m2_72k.wav 经 meteor_demod/meteor_decode
独立 FEC 链回收。两链 IDCT 一浮点（scipy ortho）一 Q14 定点，取整地板约
0.5 MAD，容差 MAD<2。详见 provenance.json 与 fixture 内 provenance 块。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mbdsdr_ai.lrpt_image import (  # noqa: E402
    _blocks_to_spatial, decode_mcu_packet,
)

GOLDEN = os.path.join(os.path.dirname(__file__), "golden",
                      "lrpt_meteor_content_golden.json")
TOL_MAD = 2.0


def _fixture():
    if not os.path.isfile(GOLDEN):
        pytest.skip("LRPT 内容级 fixture 未随仓提供")
    with open(GOLDEN, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def doc():
    return _fixture()


def test_fixture_floor(doc):
    """fixture 规模与多样性地板：≥50 条、三通道齐全、质量桶≥10。"""
    recs = doc["records"]
    assert len(recs) >= 50, f"内容级 fixture 回退：{len(recs)} < 50"
    assert {r["apid"] for r in recs} == {64, 65, 68}, "三通道覆盖不全"
    assert len({r["q"] for r in recs}) >= 10, "质量多样性不足"


def test_truth_is_independent_output(doc):
    """真值类别自报独立第三方实现输出（防自生成冒充外部真值）。"""
    assert doc["truth_class"] == "independent-third-party-implementation-output"
    assert doc["provenance"]["independent_decoder"].startswith("meteor_decode")
    # 真值像素必须是真实图像（有纹理），不是全 128 占位
    flat = 0
    for r in doc["records"]:
        pix = np.frombuffer(bytes.fromhex(r["pixels_hex"]), dtype=np.uint8)
        if pix.std() < 1.0:
            flat += 1
    assert flat == 0, f"{flat} 条记录真值像素近乎平坦——疑似占位/损坏"


def test_content_matches_independent_decoder(doc):
    """逐块像素对拍：我方 JPEG 链 == meteor_decode 独立像素（MAD<2）。

    这是内容级真值回归：zigzag 逆置换或双重 +128 任一 bug 都会把 MAD
    推到 ≫2（实测垃圾图 MAD 20-120），此处逐块钉死。
    """
    worst = 0.0
    n_blocks = 0
    failures = []
    for r in doc["records"]:
        entropy = bytes.fromhex(r["entropy_hex"])
        ref = np.frombuffer(bytes.fromhex(r["pixels_hex"]),
                            dtype=np.uint8).reshape(r["n_blocks"], 8, 8)
        coefs = decode_mcu_packet(entropy)
        assert len(coefs) == r["n_blocks"], (
            f"APID{r['apid']} seq{r['seq']} 块数漂移："
            f"{len(coefs)} != {r['n_blocks']}")
        for i, c in enumerate(coefs):
            ours = _blocks_to_spatial(c, r["q"])
            mad = float(np.abs(ours - ref[i].astype(np.float64)).mean())
            worst = max(worst, mad)
            n_blocks += 1
            if mad >= TOL_MAD:
                failures.append((r["apid"], r["seq"], i, round(mad, 3)))
    assert n_blocks >= 400, f"对拍块数过少：{n_blocks}"
    assert not failures, (
        f"{len(failures)} 块像素偏离独立真值（MAD≥{TOL_MAD}）：{failures[:10]}")
    # 取整地板应远低于容差（浮点 vs Q14 定点 ~0.5）；>1.5 说明路径漂移
    assert worst < 1.5, f"最坏块 MAD={worst:.3f} 高于取整地板，疑似路径漂移"