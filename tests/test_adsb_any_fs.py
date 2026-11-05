"""ADS-B「任意采样率入口」契约与两条真实链路的收口测试。

背景（实测事实，不是推测）：
  * ``decode_baseband`` 的严格契约是 fs ≥ 2 MS/s 且为 1 MS/s 整数倍；
    onboard 默认录制 2.4 MS/s 直接送进去会 ValueError，验收步恒失败。
  * ``experiments/exp_ota_run.py`` 的扫描循环曾把 ValueError 吞成
    ``crc_ok=False``，表现为「候选一堆、CRC 恒 0」。
  * ``modulate_baseband`` 曾用 ``round(fs/1e6)`` 把 2.4 MS/s 当成 2 MS/s
    合成，波形与采样率元数据不自洽。

这里钉住四件事：
  1. 任意 fs 入口必须带限重采样到合法档，并把 ``source_fs_hz`` /
     ``decode_fs_hz`` / ``resample_note`` 放进返回值（下游能看见换过采样率）；
  2. 严格入口仍然拒绝非整数 MHz（它的契约没有被放宽）；
  3. 合成侧拒绝非整数 MHz，不再 ``round()`` 糊过去。
  4. ADS-B 路径上只允许 ``mbdsdr_ai/adsb.py::resample_to_legal_fs`` 一份
     自拼重采样实现；实验/脚本侧再冒出 ``Fraction`` + ``resample_poly``
     副本时守卫测试必须变红（含旁路文件反证）。
"""

from __future__ import annotations

import os
import sys
import tempfile

import numpy as np
import pytest
from fractions import Fraction
from scipy.signal import resample_poly

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbdsdr_ai import adsb  # noqa: E402

ICAO = "4CA8EE"
CALLSIGN = "BAW123"
PIPELINE_DIR = os.path.join(ROOT, "tests", "fixtures", "decode_pipeline")


def _frame_iq_at(fs: float, lead_us: float = 50.0, pad: int = 1500) -> np.ndarray:
    """在指定（可能非法的）采样率上构造一帧 DF17，带少量噪声与前后空白。"""
    frame = adsb.build_identification_frame(ICAO, CALLSIGN)
    native = adsb.modulate_baseband(frame, fs=4e6, lead_us=lead_us)
    ratio = Fraction(int(round(fs)), 4_000_000)
    iq = resample_poly(native, ratio.numerator, ratio.denominator)
    rng = np.random.default_rng(20261106)
    noise = (rng.standard_normal(pad) + 1j * rng.standard_normal(pad)) * 0.01
    return np.concatenate([noise, iq, noise]).astype(np.complex64)


def test_any_fs_reports_decode_rate_and_note():
    iq = _frame_iq_at(2.4e6)
    r = adsb.decode_baseband_any_fs(iq, 2.4e6, max_frames=8)
    assert r["num_frames"] >= 1
    assert any(f["crc_ok"] and f["icao"] == ICAO for f in r["frames"])
    assert r["source_fs_hz"] == pytest.approx(2_400_000.0)
    assert r["decode_fs_hz"] == pytest.approx(adsb.RESAMPLE_TARGET_HZ)  # 固定 8 MS/s
    assert r["resample_note"], "换过采样率就必须在返回值里写清"
    assert r["decode_error"] == ""


@pytest.mark.parametrize("fs", [2.048e6, 2.4e6, 2.8e6, 3.2e6])
def test_any_fs_decodes_common_sdr_capture_rates(fs):
    iq = _frame_iq_at(fs, pad=2500)
    r = adsb.decode_baseband_any_fs(iq, fs, max_frames=8)
    assert r["num_frames"] >= 1, f"{fs/1e6:.3f} MS/s 录制解不出帧"
    assert r["decode_fs_hz"] >= 2e6 and r["decode_fs_hz"] % 1e6 == 0
    assert r["decode_fs_hz"] >= float(fs)


def test_legal_fs_passthrough_does_not_resample():
    """已合法档位不得重采样，也不许写假的 resample_note。"""
    with open(os.path.join(PIPELINE_DIR, "adsb_three_frames.cf32"), "rb") as f:
        raw = np.frombuffer(f.read(), dtype="<f4")
    iq = (raw[0::2] + 1j * raw[1::2]).astype(np.complex128)
    r = adsb.decode_baseband_any_fs(iq, 8e6, max_frames=8)
    assert r["decode_fs_hz"] == pytest.approx(8e6)
    assert r["resample_note"] == ""
    assert r["num_frames"] >= 1


def test_odd_mhz_is_resampled_not_passthrough():
    """3 MHz 能过严格契约，但 chip=sps//2 取整失真，any_fs 必须重采样。"""
    iq = _frame_iq_at(3e6)
    assert adsb.is_legal_decode_fs(3e6)      # 严格契约认为它合法
    r = adsb.decode_baseband_any_fs(iq, 3e6, max_frames=8)
    assert r["decode_fs_hz"] == pytest.approx(adsb.RESAMPLE_TARGET_HZ)
    assert "奇数 MHz" in r["resample_note"]
    assert r["num_frames"] >= 1


def test_strict_entry_still_rejects_non_integer_mhz():
    iq = _frame_iq_at(2.4e6)
    with pytest.raises(ValueError, match="1 MS/s 整数倍"):
        adsb.decode_baseband(iq, 2.4e6)


def test_tx_rejects_non_integer_mhz_instead_of_rounding():
    frame = adsb.build_identification_frame(ICAO, CALLSIGN)
    with pytest.raises(ValueError, match="round"):
        adsb.modulate_baseband(frame, fs=2.4e6)


def test_onboard_adsb_step_passes_on_2p4_ms():
    """反证点 1：把任意 fs 入口改回直接抛，这条必须红。"""
    from tools.onboarding import onboard

    iq = _frame_iq_at(2.4e6, lead_us=50.0)
    with tempfile.NamedTemporaryFile(suffix=".cf32", delete=False,
                                     dir=ROOT) as f:
        iq.tofile(f.name)
        path = f.name
    try:
        r = onboard.step_decode(path, "adsb", 2.4e6, 1090e6,
                                expected_icao=ICAO,
                                expected_callsign=CALLSIGN)
        assert r.status == "PASS", r.message
        assert r.detail["n_frames"] >= 1
        assert r.detail["decode_fs_hz"] >= 2e6
        assert r.detail["resample_note"]
        assert r.detail["decode_error"] == ""
    finally:
        os.unlink(path)


def test_experiment_scan_on_2p4_ms_is_not_silently_zero():
    """反证点 2：2.4 MS/s 录制必须解出 CRC 帧，且异常不得被吞成静默 0。"""
    from experiments.exp_ota_run import _scan_adsb_frames

    iq = _frame_iq_at(2.4e6, lead_us=50.0)
    scan = _scan_adsb_frames(iq, 2.4e6)
    assert scan["crc_ok"] >= 1, scan
    assert scan["decode_fs_hz"] % 1e6 == 0


def test_experiment_wrapper_delegates_to_single_implementation():
    """`experiments/exp_ota_run.py::_adsb_decode_iq` 只是薄包装。

    行为判据（不是静态检查）：IQ、目标 fs、note 文案三项都必须与
    ``adsb.resample_to_legal_fs`` 逐项一致——旧副本自拼 note 时会漂移。
    """
    from experiments.exp_ota_run import _adsb_decode_iq

    iq = _frame_iq_at(2.4e6, lead_us=50.0)
    want_iq, want_fs, want_note = adsb.resample_to_legal_fs(iq, 2.4e6)
    got_iq, got_fs, got_note = _adsb_decode_iq(iq, 2.4e6)
    assert np.array_equal(got_iq, want_iq.astype(np.complex64))
    assert got_fs == pytest.approx(want_fs)
    assert got_note == want_note


def test_experiment_wrapper_resamples_odd_mhz():
    """旧副本只查整数 MHz，3 MS/s 会直通；薄包装必须继承官方入口的奇数档重采样。"""
    from experiments.exp_ota_run import _adsb_decode_iq

    iq = _frame_iq_at(3e6)
    got_iq, got_fs, got_note = _adsb_decode_iq(iq, 3e6)
    assert got_fs == pytest.approx(adsb.RESAMPLE_TARGET_HZ)
    assert "奇数 MHz" in got_note
    assert got_iq.size > iq.size


def test_experiment_wrapper_passthrough_keeps_samples_and_note():
    """合法偶数档（2 MS/s）不得重采样，也不许产生 note。"""
    from experiments.exp_ota_run import _adsb_decode_iq

    iq = np.zeros(1024, dtype=np.complex64)
    got_iq, got_fs, got_note = _adsb_decode_iq(iq, 2e6)
    assert got_iq.size == iq.size
    assert got_fs == pytest.approx(2e6)
    assert got_note == ""


# ---------------------------------------------------------------------------
# 机械闸门：ADS-B 路径上只许有 mbdsdr_ai/adsb.py::resample_to_legal_fs
# 这一份自拼重采样实现。判据按「函数级」匹配，避免误伤 APT/AIS/音频等
# 与 ADS-B 无关的 resample_poly 调用点。
# ---------------------------------------------------------------------------
_ADSB_PATH_TOKENS = ("adsb", "1090", "decode_baseband", "_find_preamble")
_SELF_BUILT_RESAMPLER = ("resample_poly", "Fraction", "RESAMPLE_TARGET_HZ")


def _resampler_violations(root: str) -> list:
    """返回 (相对路径:行号, 行文本) 列表；空列表 = 只有唯一实现。"""
    import ast

    def _call_name(node):
        fn = node.func
        return fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")

    allow = os.path.abspath(os.path.join(root, "mbdsdr_ai", "adsb.py"))
    hits: list = []
    for sub in ("experiments", "scripts", "mbdsdr_ai"):
        base = os.path.join(root, sub)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "tests")]
            for name in filenames:
                if not name.endswith(".py") or name.startswith("test_"):
                    continue           # 测试夹具的采样率改造不算生产实现
                path = os.path.join(dirpath, name)
                if os.path.abspath(path) == allow:
                    continue
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        src = f.read()
                    tree = ast.parse(src)
                except (OSError, SyntaxError):
                    continue           # 语法坏文件自有别的闸门管
                lines = src.splitlines()
                func_nodes = [n for n in ast.walk(tree)
                              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                scopes = list(func_nodes)
                covered = set()
                for fn_node in func_nodes:
                    covered.update(range(fn_node.lineno,
                                         getattr(fn_node, "end_lineno", fn_node.lineno) + 1))
                module_lines = [ln for i, ln in enumerate(lines, 1) if i not in covered]
                if any(t in "\n".join(module_lines).lower() for t in _ADSB_PATH_TOKENS):
                    scopes.append(("module", module_lines))
                for scope in scopes:
                    if isinstance(scope, tuple):     # 模块级：只看函数体外的调用
                        for node in ast.walk(tree):
                            if isinstance(node, ast.Call) and node.lineno not in covered \
                                    and _call_name(node) in _SELF_BUILT_RESAMPLER:
                                hits.append((f"{os.path.relpath(path, root)}:{node.lineno}",
                                             lines[node.lineno - 1].strip()))
                        continue
                    start = scope.lineno
                    end = getattr(scope, "end_lineno", len(lines))
                    seg = "\n".join(lines[start - 1:end]).lower()
                    if not any(t in seg for t in _ADSB_PATH_TOKENS):
                        continue
                    for node in ast.walk(scope):
                        if isinstance(node, ast.Call) \
                                and _call_name(node) in _SELF_BUILT_RESAMPLER:
                            hits.append((f"{os.path.relpath(path, root)}:{node.lineno}",
                                         lines[node.lineno - 1].strip()))
    return hits


def test_adsb_resampler_is_single_implementation():
    """全仓扫：ADS-B 路径上不得再出现第二份 Fraction/resample_poly 自拼实现。"""
    hits = _resampler_violations(ROOT)
    assert hits == [], (
        "ADS-B 路径冒出第二份自拼重采样实现（唯一实现应为 "
        "mbdsdr_ai/adsb.py::resample_to_legal_fs）：\n"
        + "\n".join(f"  {p}: {text}" for p, text in hits))


def test_adsb_resampler_guard_is_red_on_bypass_copy(tmp_path):
    """反证：把旧副本（18 行 Fraction + resample_poly + 自拼 note）塞回一个
    旁路文件，闸门必须变红；跑完立即删掉，不留在工作树里。"""
    bypass = os.path.join(ROOT, "experiments", "_adsb_bypass_probe_tmp.py")
    with open(bypass, "w", encoding="utf-8") as f:
        f.write('''"""临时旁路：复刻旧版 _adsb_decode_iq（反证用，测试结束即删）。"""
import numpy as np


def _adsb_decode_iq(iq: np.ndarray, fs: float):
    """把录制 IQ 送到 decode_baseband 能接受的采样率档位（旧副本）。"""
    from fractions import Fraction
    sps = int(round(fs / 1e6))
    if fs >= 2e6 and abs(fs / 1e6 - sps) <= 1e-6:
        return iq, float(fs), ""
    target_hz = 8_000_000
    from scipy.signal import resample_poly
    ratio = Fraction(target_hz, int(round(fs)))
    out = resample_poly(iq, ratio.numerator, ratio.denominator)
    note = (f"fs={fs:.0f}Hz 非整数MHz（decode_baseband 不支持），"
            f"已重采样到 {target_hz / 1e6:.0f}MHz 解码；录制文件未改")
    return out.astype(np.complex64), float(target_hz), note
''')
    try:
        hits = _resampler_violations(ROOT)
        assert hits, "旁路文件塞回旧实现后闸门仍然绿——守卫失效"
        assert any("_adsb_bypass_probe_tmp.py" in p for p, _ in hits), hits
        assert any("Fraction(" in text for _, text in hits), hits
        assert any("resample_poly(" in text for _, text in hits), hits
    finally:
        os.unlink(bypass)
    # 删掉后必须恢复绿
    assert _resampler_violations(ROOT) == []
