"""FT8 解码优化 + 模式默认参数表 + 书签区间查询基准测试。

对标 WSJT-X / OpenWebRX 的工程结论，验证 MBDSDR 内核落地的六项改动：
1. LDPC 早停（bpdecode174_91.f90: ncheck 连续 5 轮不降且 >15 提前返回）
2. 联合拒绝门（ft8b.f90: nsync<=10 且 xsnr<-25 标记 rejected）
3. FT8 等数字模式参数表 MODE_PARAMS
4. 模式默认带宽表 MODE_BANDWIDTH（含未知模式兜底）
5. 书签区间查询 get_bookmarks_in_range
6. 近重复候选去重（sync8.f90: |df|<4Hz 且 |dt|<0.04s 只留最强）

纯 Python/numpy，无硬件依赖；不引入模拟信号发生器。
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbdsdr_ai import ft8_ldpc, ft8_decode          # noqa: E402
from mbdsdr_ai import frequency_manager as fm        # noqa: E402
from mbdsdr_ai import modes_defaults as md           # noqa: E402
from mbdsdr_ai.ft8_lite import (                      # noqa: E402
    dedup_candidates, FT8_DEDUP_FREQ_HZ, FT8_DEDUP_TIME_S)


# --------------------------------------------------------------------------
# 1. LDPC 早停
# --------------------------------------------------------------------------
def test_ldpc_early_stop_reduces_iterations():
    """明显不收敛的输入应被早停在 max_iter 之前，而非跑满 25 轮。"""
    import random
    random.seed(18)  # 确定性：该噪声输入在高位残差卡住，触发早停
    llr = [random.uniform(-2.0, 2.0) for _ in range(174)]
    decoded, iters = ft8_ldpc.ldpc_bp_decode(llr, max_iter=25)
    assert iters < 25, f"早停未生效，跑满 {iters} 轮"
    assert iters <= 20

    # 对照：干净全零码字仍应正常快速收敛（早停不得破坏正确译码路径）
    clean, it_clean = ft8_ldpc.ldpc_bp_decode([10.0] * 174, max_iter=25)
    assert sum(clean) == 0, "全零码字应解出全零信息"
    assert it_clean < 25


# --------------------------------------------------------------------------
# 2. 联合拒绝门
# --------------------------------------------------------------------------
def _all_tone0_energies():
    energies = [[0.0] * 8 for _ in range(58)]
    for e in energies:
        e[0] = 10.0
    return energies


def test_reject_gate_low_sync_low_snr():
    """低同步命中 + 低 SNR → rejected=True；仅低 SNR 但同步/信噪比未越界 → 拒绝。"""
    energies = _all_tone0_energies()
    # 全 0 硬判决音调序列：在 ICOS7 同步位置几乎不匹配（nsync≈3 ≤10）
    full_tones = [0] * 79

    bad = ft8_decode.decode_ft8_payload(energies, full_tones=full_tones,
                                       snr_db=-30.0)
    assert bad["rejected"] is True
    assert bad["reject_reason"] == "low_sync_low_snr"

    # 对照：同样低同步，但 SNR=-10（不 < -25）→ 不触发联合拒绝门
    ok = ft8_decode.decode_ft8_payload(energies, full_tones=full_tones,
                                       snr_db=-10.0)
    assert ok["rejected"] is False
    assert ok["reject_reason"] == ""

    # 对照：不传 snr_db（旧调用方）→ 不启用拒绝门，向后兼容
    legacy = ft8_decode.decode_ft8_payload(energies, full_tones=full_tones)
    assert legacy["rejected"] is False


# --------------------------------------------------------------------------
# 3. 数字模式参数表
# --------------------------------------------------------------------------
def test_mode_params_table():
    ft8 = fm.get_mode_params("FT8")
    assert ft8["tr_period_s"] == 15.0
    assert ft8["ftol_hz"] == 50
    assert ft8["nsps"] == 6912
    assert ft8["tone_spacing_hz"] == 6.25

    # 大小写不敏感
    assert fm.get_mode_params("ft8")["tr_period_s"] == 15.0
    # FT4 周期 7.5s
    assert fm.get_mode_params("FT4")["tr_period_s"] == 7.5
    # 未知模式返回空 dict
    assert fm.get_mode_params("NONEXISTENT") == {}


# --------------------------------------------------------------------------
# 4. 模式默认带宽表
# --------------------------------------------------------------------------
def test_mode_bandwidth_table():
    wfm = md.get_mode_bandwidth("WFM")
    assert wfm["low_hz"] == -75000
    assert wfm["high_hz"] == 75000

    usb = md.get_mode_bandwidth("usb")  # 大小写不敏感
    assert usb["low_hz"] == 300
    assert usb["high_hz"] == 3000

    # 未知模式兜底 ±6250
    fb = md.get_mode_bandwidth("TotallyUnknown")
    assert fb["low_hz"] == -6250
    assert fb["high_hz"] == 6250


# --------------------------------------------------------------------------
# 5. 书签区间查询
# --------------------------------------------------------------------------
def test_bookmarks_in_range():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "bookmarks.json")
        mgr = fm.FrequencyManager(user_path=path)
        # 清掉内置书签，只留可控自定义书签，避免内置频段干扰断言
        mgr.bookmarks = [
            fm.Bookmark("FT8 40m", freq_hz=7.074e6, mode="USB",
                        category="FT8", builtin=False),
            fm.Bookmark("FT8 20m", freq_hz=14.074e6, mode="USB",
                        category="FT8", builtin=False),
            fm.Bookmark("80m 段", start_hz=3.5e6, end_hz=4.0e6,
                        mode="USB", category="业余", builtin=False),
        ]

        # 区间覆盖 7.074M，但不覆盖 14.074M
        got = mgr.get_bookmarks_in_range(7.0e6, 8.0e6)
        names = {b.name for b in got}
        assert "FT8 40m" in names
        assert "FT8 20m" not in names
        assert "80m 段" not in names

        # 区间与 80m 频段（3.5-4.0M）相交
        got2 = mgr.get_bookmarks_in_range(3.8e6, 5.0e6)
        names2 = {b.name for b in got2}
        assert "80m 段" in names2
        assert "FT8 40m" not in names2

        # 空区间
        assert mgr.get_bookmarks_in_range(100e6, 101e6) == []


# --------------------------------------------------------------------------
# 6. 近重复候选去重
# --------------------------------------------------------------------------
def test_dedup_near_duplicate_candidates():
    # 两个候选频率差 2Hz（<4Hz）、时间相同（<0.04s）→ 只留 SNR 高者
    cands = [
        {"center_hz": 1000.0, "snr_est_db": -5.0, "time_s": 0.0},
        {"center_hz": 1002.0, "snr_est_db": -15.0, "time_s": 0.0},
    ]
    kept = dedup_candidates(cands)
    assert len(kept) == 1
    assert kept[0]["snr_est_db"] == -5.0  # SNR 高者保留

    # 对照：频率差 10Hz（>4Hz）→ 两个都保留
    cands2 = [
        {"center_hz": 1000.0, "snr_est_db": -5.0, "time_s": 0.0},
        {"center_hz": 1010.0, "snr_est_db": -15.0, "time_s": 0.0},
    ]
    assert len(dedup_candidates(cands2)) == 2

    # 阈值常量与标杆一致
    assert FT8_DEDUP_FREQ_HZ == 4.0
    assert FT8_DEDUP_TIME_S == 0.04


def main() -> int:
    tests = [
        test_ldpc_early_stop_reduces_iterations,
        test_reject_gate_low_sync_low_snr,
        test_mode_params_table,
        test_mode_bandwidth_table,
        test_bookmarks_in_range,
        test_dedup_near_duplicate_candidates,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
