# SPDX-License-Identifier: MIT
"""watcher 行为测试（目录/信号值守 + 触发录制，合成块序列，无硬件）。

注意：watcher.py 是「信号活动值守/触发录制」（固定频率守听 + 自适应门限 +
pre-roll 触发录制），不是文件系统监视。采集经 acquire(n)->IQ 回调注入，
测试用确定性合成块序列驱动状态机。

oracle 来源（读码语义）：
- band_power_db：10log10(mean|x|²)；
- activity_windows_from_power：超门限段按 hang_s 合并短衰落，
  时长 < min_active_s 的段丢弃；窗口为块下标闭区间 (start, end)；
- watch_capture：先采 noise_dwell_s 估噪声底（中位数），门限 = 底 + margin_db
  （或绝对 threshold_db）；连续 min_active_s 超门限触发录制，触发块恰出现一次
  （修复前 list(ring) 含当前块又 append 造成重复）；带 preroll_s 触发前缓冲；
  hang_s 拖尾结束或达 max_record_s 停止；采集无数据 → 显式 timeout 空态。
"""
import numpy as np
import pytest

from mbdsdr_ai.watcher import activity_windows_from_power, band_power_db, watch_capture

SR = 8000.0
BLOCK = 1000
BLOCK_S = BLOCK / SR          # 0.125s


def _make_script(plan, seed=3, noise_amp=0.01, sig_amp=1.0, tone_hz=100.0):
    """plan: 0=噪声块, 1=信号块。返回 acquire(n) 回调（确定性）。"""
    rng = np.random.default_rng(seed)
    state = {"i": 0}

    def _noise():
        return noise_amp * (rng.standard_normal(BLOCK) + 1j * rng.standard_normal(BLOCK))

    def _sig():
        t = np.arange(BLOCK) / SR
        return sig_amp * np.exp(2j * np.pi * tone_hz * t) + _noise()

    def acquire(n):
        assert n == BLOCK
        i = state["i"]
        state["i"] += 1
        return (_sig if plan[min(i, len(plan) - 1)] else _noise)()

    acquire.state = state
    return acquire


# ── band_power_db ───────────────────────────────────────────

def test_band_power_db_known_amplitude():
    x = np.full(BLOCK, 0.5, dtype=np.complex128)     # |x|²=0.25
    assert band_power_db(x) == pytest.approx(10 * np.log10(0.25))
    assert band_power_db(np.zeros(BLOCK, dtype=np.complex128)) == pytest.approx(-200.0)


# ── activity_windows_from_power（纯函数） ────────────────────

def test_activity_windows_basic_segment():
    """一段连续超门限 → 单窗口 (start, end) 闭区间。"""
    p = np.full(20, -40.0)
    p[5:10] = 0.0
    wins = activity_windows_from_power(p, BLOCK_S, threshold_db=-20.0,
                                       min_active_s=0.15, hang_s=0.5)
    assert wins == [(5, 9)]


def test_activity_windows_hang_merges_short_fade():
    """hang_s=0.5s → 4 块拖尾；中间 2 块衰落被合并为一个窗口。"""
    p = np.full(20, -40.0)
    p[5:8] = 0.0
    p[8:10] = -40.0        # 2 块衰落 < 4
    p[10:13] = 0.0
    wins = activity_windows_from_power(p, BLOCK_S, threshold_db=-20.0,
                                       min_active_s=0.15, hang_s=0.5)
    assert wins == [(5, 12)]


def test_activity_windows_long_fade_splits():
    """衰落块数 > hang_blocks → 拆成两个窗口。"""
    p = np.full(30, -40.0)
    p[5:8] = 0.0
    p[8:14] = -40.0        # 6 块衰落 > 4
    p[14:17] = 0.0
    wins = activity_windows_from_power(p, BLOCK_S, threshold_db=-20.0,
                                       min_active_s=0.15, hang_s=0.5)
    assert wins == [(5, 7), (14, 16)]


def test_activity_windows_min_active_drops_blip():
    """1 块毛刺 < min_active_s(0.3s→2 块) 被丢弃；2 块保留。"""
    p = np.full(20, -40.0)
    p[7] = 0.0
    assert activity_windows_from_power(p, BLOCK_S, threshold_db=-20.0,
                                       min_active_s=0.3, hang_s=0.5) == []
    p2 = np.full(20, -40.0)
    p2[7:9] = 0.0
    assert activity_windows_from_power(p2, BLOCK_S, threshold_db=-20.0,
                                       min_active_s=0.3, hang_s=0.5) == [(7, 8)]


def test_activity_windows_threshold_inclusive():
    """超门限判定为 >=（恰好等于门限算活动）。"""
    p = np.full(10, -40.0)
    p[3:6] = -20.0
    wins = activity_windows_from_power(p, BLOCK_S, threshold_db=-20.0,
                                       min_active_s=0.1, hang_s=0.1)
    assert wins == [(3, 5)]


# ── watch_capture：触发 / pre-roll / 无重复块 ─────────────────

def test_watch_capture_triggered_with_preroll_no_duplicate():
    """噪声 8 块 → 信号 6 块 → 噪声收尾。
    断言：触发；录制含触发前 pre-roll；触发块恰一次（缺陷回归钉）；
    recorded_s 与样本长一致。"""
    plan = [0] * 8 + [1] * 6 + [0] * 12
    acq = _make_script(plan)
    out = watch_capture(acq, SR, block_n=BLOCK, noise_dwell_s=1.0, max_wait_s=5.0,
                        max_record_s=10.0, margin_db=8.0, min_active_s=0.25,
                        hang_s=0.5, preroll_s=0.25)
    assert out["status"] == "triggered"
    assert out["reason"] == "活动结束"
    assert out["threshold_db"] == pytest.approx(out["noise_floor_db"] + 8.0)

    s = out["samples"]
    nb = len(s) // BLOCK
    blocks = [s[i * BLOCK:(i + 1) * BLOCK] for i in range(nb)]

    # 无重复块（修复前触发块被 append 两次 → block1 == block2）
    for i in range(nb):
        for j in range(i + 1, nb):
            assert not np.allclose(blocks[i], blocks[j]), f"块 {i} 与 {j} 重复"

    # 6 个信号块全部超门限；拖尾保留噪声尾（min_active 确认在第 2 信号块触发）
    above = [b for b in blocks if band_power_db(b) >= out["threshold_db"]]
    below = [b for b in blocks if band_power_db(b) < out["threshold_db"]]
    assert len(above) == 6
    assert 1 <= len(below) <= 2                   # 拖尾裁剪后保留 1-2 个噪声尾块

    assert out["recorded_s"] == pytest.approx(len(s) / SR)
    assert out["waited_s"] > 0
    assert out["peak_db"] > out["threshold_db"]


def test_watch_capture_preroll_includes_blocks_before_trigger():
    """min_active=1 块即触发、preroll=4 块：录制开头必须是触发前的噪声块。"""
    plan = [0] * 8 + [1] * 6 + [0] * 12
    acq = _make_script(plan, seed=17)
    out = watch_capture(acq, SR, block_n=BLOCK, noise_dwell_s=1.0, max_wait_s=5.0,
                        max_record_s=10.0, margin_db=8.0, min_active_s=0.125,
                        hang_s=0.5, preroll_s=0.5)
    assert out["status"] == "triggered"
    s = out["samples"]
    blocks = [s[i * BLOCK:(i + 1) * BLOCK] for i in range(len(s) // BLOCK)]
    # preroll_blocks = 4：触发在第 1 个信号块，pre-roll = 触发前 3 个噪声块 + ...
    # ring 含噪声尾与触发块；录制应以低于门限的块开头
    assert band_power_db(blocks[0]) < out["threshold_db"]
    assert band_power_db(blocks[1]) < out["threshold_db"]
    # 触发块（第 1 个信号块）恰好出现一次
    trig = blocks[3] if band_power_db(blocks[3]) >= out["threshold_db"] else None
    assert trig is not None
    dup = sum(1 for b in blocks if np.allclose(b, trig))
    assert dup == 1


def test_watch_capture_max_record_caps_length():
    """持续活动超出 max_record_s：状态机按时长截断，reason=达到最长录制时长。"""
    plan = [0] * 8 + [1] * 50
    acq = _make_script(plan, seed=9)
    out = watch_capture(acq, SR, block_n=BLOCK, noise_dwell_s=1.0, max_wait_s=5.0,
                        max_record_s=0.5, margin_db=8.0, min_active_s=0.25,
                        hang_s=0.5, preroll_s=0.25)
    assert out["status"] == "triggered"
    assert out["reason"] == "达到最长录制时长"
    assert out["recorded_s"] == pytest.approx(0.5, abs=2 * BLOCK_S)
    assert len(out["samples"]) <= int(0.5 * SR) + 2 * BLOCK + BLOCK  # 上限含 pre-roll/尾块


def test_watch_capture_timeout_honest_empty():
    """纯噪声：等待窗口耗尽 → timeout，samples 空数组，reason 明确。"""
    acq = _make_script([0] * 40, seed=11)
    out = watch_capture(acq, SR, block_n=BLOCK, noise_dwell_s=0.5, max_wait_s=1.0,
                        max_record_s=1.0, margin_db=8.0, min_active_s=0.25,
                        hang_s=0.5, preroll_s=0.25)
    assert out["status"] == "timeout"
    assert len(out["samples"]) == 0
    assert out["reason"] == "等待窗口内无持续活动"
    assert out["recorded_s"] == 0.0
    assert out["waited_s"] == pytest.approx(1.0)


def test_watch_capture_absolute_threshold():
    """threshold_db 给定时跳过自适应 margin，用绝对门限。"""
    plan = [0] * 8 + [1] * 6 + [0] * 12
    acq = _make_script(plan, seed=13)
    out = watch_capture(acq, SR, block_n=BLOCK, noise_dwell_s=1.0, max_wait_s=5.0,
                        max_record_s=10.0, margin_db=8.0, min_active_s=0.25,
                        hang_s=0.5, preroll_s=0.25, threshold_db=-20.0)
    assert out["status"] == "triggered"
    assert out["threshold_db"] == -20.0


def test_watch_capture_no_data_explicit_timeout():
    """acquire 恒返回空数组：显式 timeout + reason=采集无数据，不伪造录制。"""
    out = watch_capture(lambda n: np.zeros(0, dtype=np.complex128), SR,
                        block_n=BLOCK, noise_dwell_s=0.5, max_wait_s=1.0,
                        max_record_s=1.0, margin_db=8.0)
    assert out["status"] == "timeout"
    assert out["reason"] == "采集无数据"
    assert len(out["samples"]) == 0
    assert out["noise_floor_db"] == 0.0
