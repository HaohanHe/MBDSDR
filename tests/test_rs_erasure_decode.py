# SPDX-License-Identifier: MIT
"""RS 擦除辅助解码（erasure-assisted RS）——能力边界钉扎。

背景（LRPT 队列 #9）：Meteor-M2 过境真录里，RS(255,223)×4 只有一部分
帧 4 块全可纠，其余「部分帧」整帧被丢（lrpt_fec.decode_cadu 现行为）。
擦除辅助解码用「已知可疑位置」把预算从 t=16 提到 2e+f ≤ 32：纯擦除情形
可纠 32 个符号（是纯纠错的两倍）。这是抬图像覆盖率的关键杠杆。

本文件只钉**算法层**能力：
  - 2e+f ≤ 32 必须解出且逐字节等于原文；
  - 2e+f > 32 必须诚实报不可纠（绝不返回错数据 + corrected=True）；
  - 擦除位置声明多余（该位置其实没错）时，幅值应解为 0，原文仍正确；
  - prim≠1（fsphil/SSDV）路径同样成立，钉住位置映射。

修复前判据：``decode()`` 无 erasures 形参 → TypeError，故本文件在旧代码
上是红（测试体失败）而非收集错误——用模块属性访问以保持这一点。
"""
import numpy as np
import pytest

from mbdsdr_ai import fec as fec_mod


def _rs():
    return fec_mod.ReedSolomon()


def _ssdv_rs():
    return fec_mod.ReedSolomon(nsym=32, fcr=112, prim=11,
                               prim_poly=0x187, ccsds_invert=False)


def _mk(rng, nerr, nerase, rs=None, erase_may_be_clean=False):
    """构造 (原文, 码字, 擦除位置)。错误位置与擦除位置互不重叠。"""
    rs = rs or _rs()
    data = bytes(rng.integers(0, 256, rs.k).tolist())
    cw = bytearray(rs.encode(data))
    pos = rng.choice(255, nerr + nerase, replace=False).tolist()
    err_pos, era_pos = pos[:nerr], pos[nerr:]
    for p in err_pos:
        cw[p] ^= int(rng.integers(1, 256))
    if not erase_may_be_clean:
        for p in era_pos:
            cw[p] ^= int(rng.integers(1, 256))   # 擦除位置确实全错
    return data, bytes(cw), era_pos


def test_pure_erasures_up_to_32_recovers():
    """纯擦除 32 个符号（f=32, e=0，2e+f=32 边界）必须全对。

    这是擦除能力的硬证明：纯纠错上限是 16，擦除把上限翻到 32。
    """
    rs = _rs()
    rng = np.random.default_rng(2026)
    data, cw, era = _mk(rng, 0, 32, rs)
    r = rs.decode(cw, erasures=era)
    assert r.corrected is True
    assert r.nerrors == 0, "纯擦除情形不应报告纠错数"
    assert r.data == data, "32 个已知位置擦除必须逐字节恢复"


@pytest.mark.parametrize("nerr,nerase", [(8, 16), (4, 24), (12, 8), (1, 30)])
def test_mixed_errors_and_erasures_within_budget(nerr, nerase):
    """2e+f ≤ 32 的混合情形必须解出（预算分配自由）。"""
    rs = _rs()
    rng = np.random.default_rng(nerr * 100 + nerase)
    data, cw, era = _mk(rng, nerr, nerase, rs)
    r = rs.decode(cw, erasures=era)
    assert r.corrected is True
    assert r.nerrors == nerr
    assert r.data == data


@pytest.mark.parametrize("nerr,nerase", [(9, 16), (17, 0), (16, 1), (20, 4)])
def test_over_budget_is_rejected_honestly(nerr, nerase):
    """2e+f > 32 必须报不可纠，绝不返回错数据却称成功。"""
    rs = _rs()
    rng = np.random.default_rng(nerr * 7 + nerase)
    data, cw, era = _mk(rng, nerr, nerase, rs)
    r = rs.decode(cw, erasures=era)
    assert r.nerrors == -1, f"2*{nerr}+{nerase} > 32 却报纠错成功"
    assert r.data != data or r.corrected is False


def test_redundant_erasure_position_is_harmless():
    """多声明一个「其实没错」的擦除位置 → 幅值解为 0，原文仍正确。"""
    rs = _rs()
    rng = np.random.default_rng(4242)
    data, cw, era = _mk(rng, 8, 15, rs)
    extra = [p for p in range(255) if p not in era][0]   # 码字本身没错
    r = rs.decode(cw, erasures=era + [extra])
    assert r.corrected is True
    assert r.nerrors == 8
    assert r.data == data, "冗余擦除位置不得破坏解码结果"


def test_no_errors_no_erasures_still_fast_path():
    """无错无擦除走快路径：nerrors=0、corrected=False、数据原样。"""
    rs = _rs()
    data = bytes(range(223))
    cw = rs.encode(data)
    r = rs.decode(cw)
    assert r.nerrors == 0
    assert r.corrected is False
    assert r.data == data


def test_erasure_count_reported():
    """RSResult 需如实报告擦除数，供上层统计「靠擦除救回来的帧」。"""
    rs = _rs()
    rng = np.random.default_rng(777)
    data, cw, era = _mk(rng, 2, 10, rs)
    r = rs.decode(cw, erasures=era)
    assert getattr(r, "nerasures", None) == 10
    assert r.nerrors == 2


def test_duplicate_erasure_positions_are_deduplicated():
    """重复声明的擦除位置不得重复消耗预算（去重后再算 2e+f）。"""
    rs = _rs()
    rng = np.random.default_rng(31337)
    data, cw, era = _mk(rng, 4, 20, rs)
    r = rs.decode(cw, erasures=era + era)     # 同一批位置报两遍
    assert r.corrected is True
    assert r.data == data
    assert getattr(r, "nerasures", 0) == len(set(era))


def test_erasure_path_works_for_prim11_ssdv():
    """prim=11（fsphil/SSDV 域）位置映射同样正确——不是只对 CCSDS 巧合成立。"""
    rs = _ssdv_rs()
    rng = np.random.default_rng(88)
    data, cw, era = _mk(rng, 6, 20, rs)
    r = rs.decode(cw, erasures=era)
    assert r.corrected is True
    assert r.data == data


def test_erasure_out_of_range_is_rejected():
    """位置越界（≥255 或负数）必须显式报错，不得静默忽略。"""
    rs = _rs()
    data = bytes(range(223))
    cw = rs.encode(data)
    with pytest.raises(ValueError):
        rs.decode(cw, erasures=[255])
    with pytest.raises(ValueError):
        rs.decode(cw, erasures=[-1])


def test_too_many_erasures_rejected_before_decoding():
    """擦除数 > nsym 直接判不可纠（2e+f 预算本身已破），不进入 BM。"""
    rs = _rs()
    rng = np.random.default_rng(9)
    data, cw, _ = _mk(rng, 0, 0, rs)
    r = rs.decode(cw, erasures=list(range(33)))
    assert r.nerrors == -1
