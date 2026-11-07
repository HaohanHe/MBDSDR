# SPDX-License-Identifier: MIT
"""LRPT 真录软位流跨实现对拍——Python 侧孪生（C++ 侧 test_lrpt_real_cross）。

共享 fixture：`tests/golden/lrpt_meteor_real_soft_{0,1}.f32`
（Meteor-M2 真录过境窗口赢家帧的软位流，已在抽取时套上获胜星座变换
pair-swap 族——两端解码输入逐位相同，约定已裁决不再各自猜）。

两端都必须：Viterbi K7 r1/2 → 组字节（ASM 前置）→ 解扰 → RS×4 交织
→ 892B VCDU 载荷逐字一致。manifest 里的 payload_head32 是第三方真值
（dump1090 式：真录实证，非自家编码器输出）。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mbdsdr_ai.lrpt_fec import (  # noqa: E402
    CADU_SIZE, LrptCaduDecoder,
)

GOLDEN = os.path.join(os.path.dirname(__file__), "golden")
MANIFEST = os.path.join(GOLDEN, "lrpt_meteor_real_soft_manifest.json")
ASM = bytes([0x1A, 0xCF, 0xFC, 0x1D])


def _manifest():
    if not os.path.isfile(MANIFEST):
        pytest.skip("LRPT 真录软位流 fixture 未随仓提供")
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


def _decode_soft(soft: np.ndarray) -> bytes:
    """软位流 → 892B VCDU 载荷。

    2026-11-07 起走 **woven 重组 + 擦除辅助 RS + 伴随式验证**——旧口径
    （concat + 无验证）解出的「真值」被法证证伪为 RS 误纠（纠正后重编码
    与收到码字距离 31-32 > t=16），整份 manifest 随 fixture 一起重钉。
    """
    decoder = LrptCaduDecoder()
    raw, rel = decoder.decode_viterbi_to_bytes(soft.astype(np.float64),
                                               return_reliability=True)
    assert raw[:4] == ASM, f"ASM 漂移：{raw[:4].hex()}"
    payload, errs, _used = decoder.decode_cadu_erasure(
        raw[:CADU_SIZE], rel[:CADU_SIZE], order="woven")
    assert all(e >= 0 for e in errs), f"RS 不可纠：{errs}"
    assert len(payload) == 892
    return payload


def test_fixture_count_floor():
    """fixture 数地板：真录至少 2 条全纠正帧（与 golden 测试同源）。"""
    m = _manifest()
    assert len(m["frames"]) >= 2, f"fixture 帧数回退：{len(m['frames'])}"


@pytest.mark.parametrize("idx", [0, 1])
def test_real_soft_decodes_to_pinned_vcdu(idx):
    """真录软位流解出的 VCDU 载荷头逐字命中 manifest 真值（跨实现锚）。"""
    m = _manifest()
    frames = m["frames"]
    if idx >= len(frames):
        pytest.skip(f"fixture {idx} 不存在")
    fr = frames[idx]
    path = os.path.join(GOLDEN, fr["file"])
    if not os.path.isfile(path):
        pytest.skip(f"fixture 文件缺失：{fr['file']}")
    soft = np.fromfile(path, dtype=np.float32)
    assert soft.size == fr["n_soft"], "fixture 长度漂移"
    payload = _decode_soft(soft)
    head = payload[:32].hex()
    assert head == fr["payload_head32"], \
        f"VCDU 载荷头漂移：期望 {fr['payload_head32']}，实际 {head}"
    ver = (payload[0] >> 6) & 3
    assert ver == fr["vcdu_version"] == 1


def test_rs_error_profile_pinned():
    """RS 纠错/擦除计数钉扎（防解码链静默变松/变紧）。"""
    m = _manifest()
    decoder = LrptCaduDecoder()
    for fr in m["frames"]:
        path = os.path.join(GOLDEN, fr["file"])
        if not os.path.isfile(path):
            continue
        soft = np.fromfile(path, dtype=np.float32).astype(np.float64)
        raw, rel = decoder.decode_viterbi_to_bytes(soft, return_reliability=True)
        _, errs, used = decoder.decode_cadu_erasure(
            raw[:CADU_SIZE], rel[:CADU_SIZE], order="woven")
        assert list(errs) == fr["rs_errors"], \
            f"RS 纠错计数漂移：{errs} != {fr['rs_errors']}"
        assert list(used) == fr["erasures_used"], \
            f"擦除数漂移：{used} != {fr['erasures_used']}"
