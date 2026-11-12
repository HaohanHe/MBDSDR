"""七环验收的诚实性契约（快测，不碰硬件、不出声、不发射）。

锁定三条不可退让的规则：

1. SKIP 原因码只有四个；写第五个码必须直接报错，不允许自由发挥。
2. ``skip_result`` 的结果永远是 ``SKIP`` + 码，不可能变成 ``PASS``。
3. ``run_all._normalize`` 必须把 ``UNVERIFIED`` 降级为 ``SKIP``（缺省 ``NO_DATA``），
   否则「没验的项」会从汇总里消失。

另：本文件里的硬件阈值断言（``PILOT_CENTER_HZ`` / ``PILOT_FREQ_TOL_HZ`` /
``PILOT_SNR_MIN_DB`` / ``PLAYBACK_RMS_RATIO_MIN/MAX``）是**冻结的硬件导出值**，
改动必须在提交说明里给出新证据（真机读数及其测量不确定度），不允许先改数后补理由。
两类断言分工不同：拼装测试只证「代码与人工卡没有手抄走样」，冻结测试才证
「这个数还是当时认定的那个数」——两者都不可或缺。
"""

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "hw_acceptance"))

import common  # noqa: E402
import run_all  # noqa: E402

CODE_SET = {"NO_HW", "NO_DATA", "NEEDS_OPERATOR", "NEEDS_TIME_WINDOW"}


def _functions_referencing(path: Path, name: str) -> set:
    """返回源码里真正引用该名字的函数名（AST 语义，不是子串计数）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef)
        and any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(fn))
    }


def _naked_pilot_tolerance_literals(path: Path) -> list:
    """找出 ``abs(f_p - 19000) < 100/300`` 这类绕开常量的裸字面量比较。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if "f_p" not in ast.unparse(node.left):
            continue
        for comparator in node.comparators:
            if (isinstance(comparator, ast.Constant)
                    and comparator.value in (100, 100.0, 300, 300.0)):
                hits.append(ast.unparse(node))
    return hits


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


def test_real_devices_retries_unidentifiable_device(monkeypatch):
    """上一进程刚释放句柄时序列号读不到（空串、身份不可分辨）；必须重试到真序列号。

    旧实现用 'rtl-0' 占位号，已被移除：占位号随枚举顺序变，不是身份。
    """
    from mbdsdr_ai.device_manager import DeviceInfo, DeviceManager

    calls = []

    def fake_list(_self):
        calls.append(1)
        ser = "" if len(calls) == 1 else "77771111153705700"
        return [DeviceInfo(name="RTL-SDR #0", driver="rtlsdr", serial=ser, enum_index=0)]

    monkeypatch.setattr(DeviceManager, "list_devices", fake_list)
    monkeypatch.setattr(common.time, "sleep", lambda _s: None)
    devs = common.real_devices()
    assert [d.serial for d in devs] == ["77771111153705700"]
    assert len(calls) == 2


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


# ------------------------------------------------- 导频测量法 / 噪声底阈值（同为实测结论）
import numpy as np  # noqa: E402

import ring2_spectrum_waterfall as ring2  # noqa: E402
import ring3_demod_audio as ring3  # noqa: E402

FS = 2.4e6


def _iq_pilot_plus_spur(seconds: float = 0.36) -> np.ndarray:
    """2.4 MS/s 复基带：19 kHz 导频 + 181159 Hz 杂散（后者无滤波抽取会折到 18841 Hz）。"""
    n = int(FS * seconds)
    t = np.arange(n) / FS
    mpx = 0.09 * np.cos(2 * np.pi * 19000.0 * t) + 0.5 * np.cos(2 * np.pi * 181159.0 * t)
    return np.exp(1j * 2 * np.pi * np.cumsum(mpx) / FS)


def test_pilot_measurement_ignores_aliased_spur():
    """断 18841 Hz 是折叠加法产物：带抗混叠重采样后必须读回 19000 Hz。"""
    r = ring3._pilot(FS, _iq_pilot_plus_spur())
    assert r is not None
    f_p, db, _segs = r
    assert abs(f_p - 19000.0) < 3.0, f"导频被杂散拽到 {f_p:.1f} Hz"
    assert db > 10.0, f"导频只高出邻域 {db:.1f} dB"


def test_pilot_capture_accounting_is_measured_not_faked():
    """采集记账：captured_s/丢样率必须由真实样本数算出，不能直接等于请求值。

    假控制器只给 6 个块就返回空（模拟「墙钟没读满」）。若有人把
    pilot_captured_s 直接写成请求时长，这里的恒等式立刻红。
    """
    class _FakeCtrl:
        block = 262144

        def __init__(self):
            self.got = 0

        def read_iq(self, _n):
            if self.got >= 6:
                return np.zeros(0, dtype=np.complex64)
            self.got += 1
            rng = np.random.default_rng(20261106 + self.got)
            return (rng.normal(size=self.block)
                    + 1j * rng.normal(size=self.block)).astype(np.complex64)

    r = ring3._pilot_from_ctrl(_FakeCtrl(), FS, seconds=6.0)
    assert r is not None
    samples = 6 * 262144
    captured = samples / FS
    assert r["requested_s"] == 6.0
    assert abs(r["captured_s"] - captured) < 1e-3, f"captured_s={r['captured_s']}"
    assert abs(r["drop_pct"] - (1.0 - captured / 6.0) * 100.0) < 0.2, r["drop_pct"]
    # MPX 点数与采集时长、段数三者自洽（200 kHz，段长 65536；每块重采样最多多出 1 点）
    assert abs(r["mpx_points"] - captured * 200000) <= 6, r["mpx_points"]
    assert r["segments"] == r["mpx_points"] // 65536
    assert r["throughput_msps"] is None or r["throughput_msps"] > 0


def test_old_decimation_would_report_the_alias():
    """反证：旧法 mpx[::12] 无抗混叠，181159 Hz 折到 200000−181159=18841 Hz。"""
    x = _iq_pilot_plus_spur()
    x = x - x.mean()
    mpx = np.angle(x[1:] * np.conj(x[:-1])) * FS / (2 * np.pi)
    mpx = mpx[::12]
    fs2 = FS / 12
    n = min(131072, len(mpx))
    sp = np.abs(np.fft.fft(mpx[:n] * np.hanning(n)))
    fr = np.arange(n) * fs2 / n
    band = (fr > 18500) & (fr < 19500)
    f_old = float(fr[band][int(np.argmax(sp[band]))])
    assert abs(f_old - 18841.0) < 5.0, f"旧法读数 {f_old:.1f} Hz（预期 18841）"


def test_floor_drift_threshold_inside_measured_envelope():
    """阈值必须落在 (6.81, 1.5×6.81) 内：既不能假红，也不能放宽。

    6.81 是 10 次同台同增益实测的最大极差；1.5×6.81 是「1.5 倍实测最大极差」口径。
    反证：把 ring2.FLOOR_DRIFT_MAX_DB 临时改成 6.0 → 第一条断言红；
    改成 10.2 → 第二条断言红。
    """
    thr = ring2.FLOOR_DRIFT_MAX_DB
    assert thr > 6.81, (
        f"阈值 {thr} dB 没覆盖 10 次实测的最大极差 6.81 dB，正常观测会假红")
    max_1p5x = round(1.5 * 6.81, 1)  # 10.2：1.5 倍口径的上界（取到 0.1 dB）
    assert thr < max_1p5x, (
        f"阈值 {thr} dB ≥ 1.5×实测最大极差 {max_1p5x} dB，属于为好看放宽")


# ------------------------------------------------- 阈值单一真值源（不许再分叉）
import ring4_recording_playback as ring4  # noqa: E402

RING3 = ROOT / "scripts" / "hw_acceptance" / "ring3_demod_audio.py"


def test_pilot_acceptance_thresholds_match_doc_card():
    """拼装层：ring3 两条路径与人工卡共用同一份常量，不许手抄走样。

    本用例**不判断数值对错**，那是 ``test_hardware_thresholds_are_frozen_*`` 的事。
    这里只证明：夹具路径与真机路径引用同一个容差常量、没有一处写回裸字面量，
    且人工卡里的判据串与常量拼出的串逐字一致。

    反证：把任一路径写回裸字面量（`abs(f_p - 19000) < 100/300`）→ 红；
    只改文档卡、不改常量（或反过来）→ 红。
    """
    tol = ring3.PILOT_FREQ_TOL_HZ
    snr = ring3.PILOT_SNR_MIN_DB
    text = DOC.read_text(encoding="utf-8")
    assert f"19000±{tol:.0f} Hz 且峰/邻域 >{snr:.0f} dB" in text, (
        "人工卡与 ring3 导频阈值已分叉：文档里找不到由常量拼出的判据串")

    ring3_sites = _functions_referencing(RING3, "PILOT_FREQ_TOL_HZ")
    assert len(ring3_sites) >= 2, (
        f"ring3 的夹具路径与真机路径没有共用同一个容差常量：{ring3_sites}")
    naked = _naked_pilot_tolerance_literals(RING3)
    assert naked == [], f"ring3 又写回了裸容差字面量（绕开常量）：{naked}"


def test_playback_ratio_thresholds_match_doc_card():
    """拼装层：ring4 常量与人工卡逐字一致（数值对错由冻结测试负责）。

    反证：只改常量或只改文档卡（两者不同步）→ 本用例红。
    """
    lo, hi = ring4.PLAYBACK_RMS_RATIO_MIN, ring4.PLAYBACK_RMS_RATIO_MAX
    text = DOC.read_text(encoding="utf-8")
    assert f"{lo} – {hi}" in text, (
        "人工卡与 ring4 回放比阈值已分叉：文档里找不到由常量拼出的区间串")


def test_hardware_thresholds_are_frozen_with_recorded_provenance():
    """冻结层：硬件导出的具体数值，改动必须带新证据，不许先改数后补理由。

    出处（均为代码或实测来源，不是从常量反推出来的）：
      * ``PILOT_CENTER_HZ = 19000``：FM 立体声导频标称频率（19 kHz）。
      * ``PILOT_FREQ_TOL_HZ = 100``：源自 ring3 真机路径统一前的硬编码
        ``abs(f_p - 19000) < 100``；现判定点见 ring3_demod_audio.py:340–342。
        同一路径 `_analyze_mpx` 用 65536 点 Welch + 抛物线插值，频率不确定度约
        ±1 Hz，故 100 Hz 是约 100 倍的安全裕度，不是拟合/试出来的边界。
      * ``PILOT_SNR_MIN_DB = 10``：同真机路径统一前的硬编码 ``snr_p > 10``。
      * ``PLAYBACK_RMS_RATIO_MIN/MAX = 0.3/3.0``：ring4 的「文件回放/实时 RMS 比」
        区间；产品运行时链路不做这项判定，故此值只属于硬件验收口径。

    反证：100→300（同步改人工卡也一样）→ 本用例红；回放比上限 3.0→0.8 → 红。
    """
    assert ring3.PILOT_CENTER_HZ == 19000.0
    assert ring3.PILOT_FREQ_TOL_HZ == 100.0
    assert ring3.PILOT_SNR_MIN_DB == 10.0
    assert ring4.PLAYBACK_RMS_RATIO_MIN == 0.3
    assert ring4.PLAYBACK_RMS_RATIO_MAX == 3.0


def test_runtime_pilot_detection_is_a_distinct_named_threshold():
    """产品扫描 vs 硬件验收是两个场景：名字与值域必须分开。

    用途声明是注释/文档，无法用行为断言证明；这里只守住可执行部分。

    反证：把 fm_scan.PILOT_DETECT_BAND_HZ 改成 300 / 把阈值改成 10 → 本用例红。
    """
    from mbdsdr_ai import fm_scan

    assert fm_scan.PILOT_DETECT_BAND_HZ != ring3.PILOT_FREQ_TOL_HZ
    assert fm_scan.PILOT_DETECT_SNR_DB != ring3.PILOT_SNR_MIN_DB
    assert fm_scan.PILOT_DETECT_BAND_HZ > ring3.PILOT_FREQ_TOL_HZ
    assert fm_scan.PILOT_DETECT_SNR_DB < ring3.PILOT_SNR_MIN_DB


def test_floor_drift_threshold_matches_recorded_derivation():
    """钉住具体值：mean+3σ=8.14 → 取 0.5 dB 粒度上界 8.5（与 docs §6.3 一致）。"""
    assert ring2.FLOOR_DRIFT_MAX_DB == 8.5



def test_both_suites_share_the_same_floor_threshold():
    """A2 与 R2 量的是同一个统计量，阈值必须是同一个常量（不许两套各拍一个）。"""
    import acceptance_suite  # noqa: E402
    assert acceptance_suite.FLOOR_DRIFT_MAX_DB == ring2.FLOOR_DRIFT_MAX_DB

