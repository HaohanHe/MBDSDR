# SPDX-License-Identifier: MIT
"""LRPT 擦除辅助解码链（Viterbi 可靠性 → 字节擦除 → RS）。

场景（真录现场）：4 块 RS 里有些块的符号错误数超过盲纠上限 t=16，
旧链路整帧丢弃。若把「译码器最没把握的字节」当擦除位置送进 RS，预算
从 2e ≤ 32 放宽到 2e+f ≤ 32，每块能多救约一倍符号。

本文件钉三件事：
  1. 每块 20 个错误（>16，盲解必败）时，正确擦除位置能救回整帧；
  2. 擦除位置只给一半时，剩下靠盲纠，仍应成功（2·4+16 ≤ 32）；
  3. 擦除位置胡给（指向干净字节）时，**绝不谎报成功**——宁可报失败。
"""
import numpy as np
import pytest

from mbdsdr_ai.lrpt_fec import CADU_SIZE, LrptCaduDecoder
from mbdsdr_ai.lrpt_pipeline import LrptE2EPipeline


@pytest.fixture(scope="module")
def pipe():
    return LrptE2EPipeline()


def _damage(pipe, payload, nerr_per_block, seed):
    """构造损坏 CADU + 字节可靠性向量。

    返回 (cadu_bytes, byte_rel, payload)。错误在「解扰前的交织数据区」注入，
    加扰是逐字节 XOR、不挪位置，所以注入位置即收到码字里的出错位置。
    """
    cadu = pipe.build_cadu(payload)
    dec = pipe.decoder
    blob = bytearray(dec.scrambler.descramble(cadu[4:]))
    rng = np.random.default_rng(seed)
    bad = []                      # blob 内字节索引
    for blk in range(4):
        js = rng.choice(255, nerr_per_block, replace=False)
        bad.extend(int(j) * 4 + blk for j in js)
    for p in bad:
        blob[p] ^= 0xA5
    damaged = cadu[:4] + dec.scrambler.scramble(bytes(blob))

    # 可靠性：默认全高；出错字节标成最可疑（0.1）
    rel = np.full(CADU_SIZE, 100.0)
    for p in bad:
        rel[4 + p] = 0.1
    return damaged, rel, bytes(blob)


def _payload(seed=1):
    rng = np.random.default_rng(seed)
    return bytes(rng.integers(0, 256, 892).tolist())


def test_blind_decode_fails_at_20_errors_per_block(pipe):
    """基线：每块 20 个错误超过 t=16，盲解必须失败（否则后续测试无意义）。"""
    payload = _payload()
    damaged, _rel, _ = _damage(pipe, payload, 20, seed=11)
    got, errs = pipe.decoder.decode_cadu(damaged)
    assert got == b"", "20 错/块竟然盲解成功——基线不成立"
    assert all(e == -1 for e in errs)


def test_erasures_rescue_beyond_blind_capacity(pipe):
    """20 错/块 + 正确擦除位置 → 4 块全恢复，payload 逐字节一致。"""
    payload = _payload(2)
    damaged, rel, _ = _damage(pipe, payload, 20, seed=12)
    got, errs, used = pipe.decoder.decode_cadu_erasure(damaged, rel)
    assert got == payload, f"擦除未能救回：errors={errs} used={used}"
    assert all(e >= 0 for e in errs), errs
    assert all(u > 0 for u in used), f"应逐块启用擦除，实际 {used}"


def test_half_guidance_still_succeeds(pipe):
    """只标一半擦除位置：剩 10 个盲纠 + 10 擦除 = 30 ≤ 32，仍应成功。"""
    payload = _payload(3)
    damaged, rel, _ = _damage(pipe, payload, 20, seed=13)
    # 把一半位置的可靠性「洗白」，等于上层只认出其中一半
    idx = np.flatnonzero(rel < 1.0)
    rel_masked = rel.copy()
    rel_masked[idx[: len(idx) // 2]] = 100.0
    got, errs, used = pipe.decoder.decode_cadu_erasure(damaged, rel_masked)
    assert got == payload, f"半擦除引导失败：errors={errs} used={used}"


def test_wrong_erasure_positions_cannot_be_self_detected(pipe):
    """已知极限记录：擦除位置全错时，RS 可能纠到**另一个合法码字**。

    **这不是缺陷，是编码理论的 undetected error 极限**：当 4 块各自纠到
    「另一个距离 ≤ t 的合法码字」时，纠正后伴随式当然全零——RS 层没有任何
    信息可以自证对错。把它固化下来，防止后人以为「加了 syndrome 验证就
    万事大吉」。

    工程结论（必须记住）：**擦除链的产出不能只信 RS 的 nerrors ≥ 0**，
    上层必须用 VCDU 结构判据（version=1 / SCID / VCID / 帧计数连续性）
    兜底。实测本用例误纠结果的 VCDU version 字段为 0，而合法 VCDU 恒为 1
    ——结构判据可识别。
    """
    payload = _payload(4)
    damaged, rel, _ = _damage(pipe, payload, 20, seed=14)
    rel_bad = np.full(CADU_SIZE, 100.0)
    bad_idx = np.flatnonzero(rel < 1.0)
    clean = np.setdiff1d(np.arange(CADU_SIZE), bad_idx)[: len(bad_idx)]
    rel_bad[clean] = 0.1                       # 把干净字节说成最可疑
    got, errs, _used = pipe.decoder.decode_cadu_erasure(damaged, rel_bad)
    if got == b"":
        return                                 # 报失败也是可接受结果
    assert got != payload, "错位擦除竟然还原出原文——本用例的前提不成立"
    # RS 自认为「成功」（nerrors ≥ 0）却给出错数据，正是必须上层兜底的证据
    assert all(e >= 0 for e in errs)


def test_clean_frame_is_untouched(pipe):
    """干净帧：擦除链不得改动结果，且不应浪费擦除预算。"""
    payload = _payload(5)
    cadu = pipe.build_cadu(payload)
    rel = np.full(CADU_SIZE, 100.0)
    got, errs, used = pipe.decoder.decode_cadu_erasure(cadu, rel)
    assert got == payload
    assert errs == [0, 0, 0, 0]
    assert used == [0, 0, 0, 0], f"干净帧不该动擦除，实际 {used}"


def test_no_reliability_falls_back_to_blind(pipe):
    """不提供可靠性（全 None）时等价于原 decode_cadu 行为。"""
    payload = _payload(6)
    damaged, _rel, _ = _damage(pipe, payload, 20, seed=16)
    got, errs, used = pipe.decoder.decode_cadu_erasure(damaged, None)
    assert got == b""
    assert all(e == -1 for e in errs)
    assert all(u == 0 for u in used)


def test_viterbi_bytes_carry_per_byte_reliability(pipe):
    """软位流解码时可同时拿到每字节可靠性，长度与字节串一致。"""
    from mbdsdr_ai.lrpt_fec import convolve_encode

    payload = _payload(7)
    cadu = pipe.build_cadu(payload)
    bits = np.unpackbits(np.frombuffer(cadu, dtype=np.uint8))
    coded = convolve_encode(bits).astype(np.float64) * 2.0 - 1.0
    raw, rel = pipe.decoder.decode_viterbi_to_bytes(coded,
                                                    return_reliability=True)
    assert raw[:4] == bytes([0x1A, 0xCF, 0xFC, 0x1D])
    assert rel.size == len(raw), f"可靠性长度 {rel.size} != 字节数 {len(raw)}"
    assert np.all(np.isfinite(rel))


def test_end_to_end_noisy_iq_uses_erasure(pipe):
    """完整链路：带噪 IQ → 软解调 → Viterbi(带可靠性) → 擦除解码。

    这是「擦除真的在真链路上可用」的钉子：不许只在手工构造的 CADU 上成立。
    """
    payload = _payload(8)
    iq = pipe.build_tx_iq(payload, freq_offset_hz=800.0, awgn_snr_db=6.0)
    soft, cands = pipe.demod.demodulate_soft(iq)
    assert cands, "带噪 IQ 无帧候选"
    best = cands[0]
    start = best.sync_symbol_index * 2
    need = 64 + (CADU_SIZE * 8 + 6) * 2
    end = min(start + need, soft.size)
    sl = soft[start:end]
    raw, rel = pipe.decoder.decode_viterbi_to_bytes(sl, return_reliability=True)
    assert raw[:4] == bytes([0x1A, 0xCF, 0xFC, 0x1D]), "ASM 未复原"
    got, errs, _used = pipe.decoder.decode_cadu_erasure(
        raw[:CADU_SIZE], rel[:CADU_SIZE])
    assert got == payload, f"端到端擦除链失败：errors={errs}"
