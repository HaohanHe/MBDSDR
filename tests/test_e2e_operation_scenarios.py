# SPDX-License-Identifier: MIT
"""端到端操作场景（P12）：把「懂卫星 + 懂 SDR + 懂天象」串成可复跑的链路。

三个场景（每个都能单独复跑）：

  A. 过境 → 下行 → 解码 → 归档 → 库内可查
     python -m pytest tests/test_e2e_operation_scenarios.py::test_scenario_a_pass_to_archive -q -s
  B. 天区方位 → 人工确认（替身）→ 接收 → 频谱出现信号
     python -m pytest tests/test_e2e_operation_scenarios.py::test_scenario_b_sky_to_spectrum -q -s
  C. 多步计划 → 真实计算 → 缺硬件时如实降级
     python -m pytest tests/test_e2e_operation_scenarios.py::test_scenario_c_plan_and_degrade -q -s

替身边界（每条都会在输出里标注）：
  - 模型：注入假模型，按预期产出 tool_calls（C 的「拆解」由假模型给定，不是模型自主推理）。
  - SDR：无真机；A 的 APT 音频是仓内合成器产物，B 的 IQ 是合成单音 + 噪声，
    两者都在报告行里写 `[SYNTHETIC]`。
  - 时间：B 用 TimeEngine 把「当前时刻」拨到过境窗口，属于测试替身。
  - 真实的部分：SGP4 过境/方位/多普勒、频谱 FFT、APT 解码、录制库索引读写。

已知限制：TLE 可能过期，过境时刻精度由报告里的 `time_precision_s` 决定（本套件
按同一份 TLE 跨模块核对，不额外放宽）。
"""
import datetime
import json
import os
import re
import sys
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai import orbit                                    # noqa: E402
from mbdsdr_ai.decoders import SATELLITE_FREQUENCIES           # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine            # noqa: E402
from mbdsdr_ai.noaa_apt_lite import (                          # noqa: E402
    synthesize_apt_audio, synthesize_test_images,
)
from mbdsdr_ai.recording_library import RecordingLibrary       # noqa: E402
from mbdsdr_ai.spectrum_processor import SpectrumProcessor     # noqa: E402

#: 测试用中性示例站址（与仓内其他用例同一约定，非任何真实站点）
LAT, LON, ALT = 45.0, 120.0, 0.0
#: 场景卫星：有 APT 下行且有稳定过境
SAT = "NOAA 19"
C_M_S = 299_792_458.0


def _make_agent():
    from mbdsdr_ai import model_manager as _mm
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig
    original = _mm.ModelManager.fetch_models
    _mm.ModelManager.fetch_models = lambda self, *a, **k: None
    try:
        return MBDSDRAgent(AgentConfig(api_key="sk-test", model="test-model"))
    finally:
        _mm.ModelManager.fetch_models = original


def _reset_time_basis():
    """把全局 TimeEngine 恢复到「墙钟 · 1× · 运行」。

    本套件的 pass_case 用 datetime.now() 取起点，而 orbit / sdr_tools 用
    TimeEngine；别处用例若把引擎留在模拟时刻（场景 B 自己也会拨表），
    跨模块对拍就会基于两个不同起点。显式重置，不靠测试执行顺序。
    """
    eng = get_time_engine()
    eng.set_time_now()
    eng.set_rate(1.0)
    eng.resume()


@pytest.fixture(autouse=True)
def _isolate_global_time():
    _reset_time_basis()
    yield
    _reset_time_basis()


@pytest.fixture
def agent():
    """每个场景一个独立 Agent：场景 A/B 会切换当前 SDR 源，C 需要「无硬件」初态。"""
    return _make_agent()


@pytest.fixture
def pass_case():
    """真实过境：报告选 NOAA 19 的第一场，再把本地时刻还原成 epoch。

    **必须每个用例重算，不能 module 级缓存**：24h 过境窗口是墙钟相对的，
    module-scope 会在会话早期缓存一个中天时刻（实测 20:39:57），等场景
    A/C 在 25 分钟后调用 `sdr_satellite_passes` 时那场已滑出窗口，报告里
    只剩后续过境 → 断言必红。2026-11-09 全量固定顺序实测（K09），
    单跑/早段全量不复现。SGP4 24h 预测约 0.2s，重算代价可忽略。
    """
    _reset_time_basis()          # 与 orbit / sdr_tools 的 TimeEngine 对齐
    # SATELLITE_FREQUENCIES 的单位是 MHz（见 decoders._load_satellite_frequencies）
    nominal = float(SATELLITE_FREQUENCIES[SAT]) * 1e6
    report = orbit.predict_passes_report(
        LAT, LON, ALT / 1000.0, hours=24, min_elevation=10,
        satellite_type="weather", nominal_freq_hz=nominal)
    passes = [p for p in report["passes"] if p["satellite"] == SAT]
    if not passes:
        unavailable = [d for d in report["degraded"]
                       if d.get("reason") in ("tle_unavailable", "identity_mismatch")]
        if unavailable:
            pytest.skip(f"无可用真实 TLE，过境链路无法验证（非证据）: "
                        f"{unavailable[0]['reason']}")
        pytest.fail(f"24 小时内没有 {SAT} 过境（无降级条目），场景无法复跑")
    p = passes[0]

    hms = [int(v) for v in p["culmination_time"].split(":")]
    while len(hms) < 3:                      # 精度不足时报告只给 HH:MM
        hms.append(0)
    now = datetime.datetime.now()
    when = now.replace(hour=hms[0], minute=hms[1], second=hms[2], microsecond=0)
    if when < now - datetime.timedelta(minutes=5):
        when += datetime.timedelta(days=1)
    t_cul = time.mktime(when.timetuple())

    st = orbit.compute_satellite_state(SAT, LAT, LON, ALT / 1000.0, when=t_cul)
    if st is None:
        pytest.skip("SGP4/TLE 不可得，跨模块一致性无法验证（非证据）")
    return {"pass": p, "t_cul": t_cul, "state": st, "nominal_hz": nominal,
            "report": report}


def _doppler_hz(range_rate_kms, nominal_hz):
    """与天空视图一致的口径：f_doppler = f_nominal × v_los / c。"""
    return nominal_hz * range_rate_kms * 1000.0 / C_M_S


def _write_cf32(path, iq):
    np.asarray(iq, dtype=np.complex64).tofile(str(path))


def _write_audio_wav(path, audio, sample_rate=24000):
    from scipy.io import wavfile
    wavfile.write(str(path), sample_rate, np.asarray(audio, dtype=np.float32))


def _parse_sky_line(content, sat):
    line = next((ln for ln in content.splitlines() if ln.strip().startswith(sat)), None)
    assert line, f"天空视图里没有 {sat}:\n{content[:400]}"
    elev = float(re.search(r"仰角 ([-\d.]+)°", line).group(1))
    azim = float(re.search(r"方位 ([-\d.]+)°", line).group(1))
    dopp = float(re.search(r"多普勒 ([+-]\d+)Hz", line).group(1))
    return line, elev, azim, dopp


def _parse_aligned_lines(content):
    m = re.search(r"对齐行 (\d+)", content)
    return int(m.group(1)) if m else 0


def _culmination_in_report(content: str, tca: str, tol_s: int = 3) -> bool:
    """报告里是否存在与目标时刻相符的中天（按报告自己声明的精度比对）。

    `sdr_satellite_passes` 用 2 秒细采样找中天，报告首行明确写「中天采样偏差
    ≤1 秒」。fixture 与工具是**两次独立计算**，起点相差几十秒时采样网格整体
    平移，同一场过境会落到相邻采样点（实测 09:34:42 vs 09:34:43）。旧断言
    逐秒比字符串，把采样精度当成了时刻精度，在长批次里必然偶发红。

    这里仍要求「报告里必须有这场过境」：目标卫星名由调用方单独断言，本函数
    只接受与 tca 相差 ≤3 秒（两次各 ≤1s + 1s 边界余量）的中天行，远小于
    任意两次过境的间隔，不允许拿别场过境蒙混。
    """
    def _sec(hms: str) -> int:
        parts = [int(v) for v in hms.split(":")]
        while len(parts) < 3:
            parts.append(0)
        return parts[0] * 3600 + parts[1] * 60 + parts[2]

    target = _sec(tca)
    for m in re.finditer(r"中天: (\d{2}:\d{2}:\d{2})", content):
        delta = abs(_sec(m.group(1)) - target)
        if min(delta, 86400 - delta) <= tol_s:
            return True
    return False


# ---------------------------------------------------------------------------
# 场景 A：过境 → 调谐 → 解码 → 归档 → 库内可查
# ---------------------------------------------------------------------------
def test_scenario_a_pass_to_archive(agent, pass_case, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    case = pass_case
    p, t_cul, nominal = case["pass"], case["t_cul"], case["nominal_hz"]
    shift = _doppler_hz(case["state"]["range_rate_kms"], nominal)
    tuned = nominal + shift

    # 1) 工具：过境预测（真实 SGP4；报告里同一颗星/同一时刻）
    res = agent.tool_registry.call("sdr_satellite_passes", {
        "latitude": LAT, "longitude": LON, "altitude": ALT,
        "hours": 24, "min_elevation": 10, "satellite_type": "weather",
        "nominal_freq_hz": nominal,
    })
    assert res.success, res.content[:300]
    assert SAT in res.content
    assert _culmination_in_report(res.content, p["culmination_time"]), \
        f"报告里没有目标过境的中天（{p['culmination_time']}±3s）"

    # 2) 工具：打开合成 IQ 源 + 调谐到 TCA 下行（调谐是真实后端动作）
    iq_path = tmp_path / "tune_stub.cf32"
    _write_cf32(iq_path, np.zeros(4096, dtype=np.complex64))
    r_open = agent.tool_registry.call("sdr_open_iq_file", {
        "path": str(iq_path), "sample_rate_hz": 48000,
        "center_freq_hz": int(round(nominal)),
    })
    assert r_open.success, r_open.content[:300]
    r_tune = agent.tool_registry.call("sdr_set_frequency",
                                     {"frequency_hz": int(round(tuned))})
    assert r_tune.success, r_tune.content[:300]
    backend = agent.sdr_manager.get_active()
    assert backend is not None and backend.get_frequency() == int(round(tuned))

    # 3) 工具：离线 APT 解码（合成音频，明确标注）
    images = synthesize_test_images(80)
    audio = synthesize_apt_audio(images[0], images[1], fs=24000,
                                 noise_std=0.01,
                                 rng=np.random.default_rng(20261104))
    wav_path = tmp_path / "noaa19_apt_synthetic.wav"
    _write_audio_wav(wav_path, audio, 24000)
    r_dec = agent.tool_registry.call("sdr_decode_noaa_apt",
                                     {"input_path": str(wav_path)})
    assert r_dec.success, r_dec.content[:300]
    aligned = _parse_aligned_lines(r_dec.content)
    assert aligned >= 60, r_dec.content[:400]
    pngs = sorted((tmp_path / "experiments" / "noaa_apt").glob("apt_*.png"))
    assert pngs, "解码没有落盘 PNG"

    # 4) 归档：登记进录制库，并写入同一场过境的结构化事实
    lib = RecordingLibrary(str(tmp_path / "library"))
    added = lib.add_recording(str(wav_path), metadata={
        "center_freq_hz": int(round(tuned)),
        "sample_rate_hz": 24000,
        "start_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t_cul)),
        "duration_s": round(len(audio) / 24000.0, 1),
        "device": "synthetic-apt",
    })
    assert added.ok, added.message
    entry_id = added.data["entry_id"]
    facts = {
        "satellite": SAT,
        "pass_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t_cul)),
        "nominal_freq_hz": int(round(nominal)),
        "doppler_shift_hz": round(shift, 1),
        "tuned_freq_hz": int(round(tuned)),
        "decode_lines": aligned,
        "decode_pngs": [str(x) for x in pngs],
        "substitute": "SYNTHETIC_APT_AUDIO",
        "tle_tier": p.get("tle_tier"),
        "time_precision_s": p.get("time_precision_s"),
    }
    upd = lib.update_entry(entry_id, notes=json.dumps(facts, ensure_ascii=False))
    assert upd.ok, upd.message

    # 5) 事后查询：库里这条记录与同一次过境/同一频率/同一解码结果一致
    listed = lib.list_entries()
    assert listed.ok
    entry = next(e for e in listed.data["entries"] if e.get("entry_id") == entry_id)
    assert entry["center_freq_hz"] == int(round(tuned))
    notes = json.loads(entry["notes"])
    assert notes["satellite"] == SAT
    assert notes["pass_utc"] == facts["pass_utc"]
    assert notes["tuned_freq_hz"] == int(round(nominal + _doppler_hz(
        case["state"]["range_rate_kms"], nominal)))
    assert notes["decode_lines"] == aligned
    assert all(os.path.exists(pth) for pth in notes["decode_pngs"])
    rng = p.get("doppler_hz") or {}
    if rng:
        assert rng["min_hz"] <= shift <= rng["max_hz"]

    print(f"[SYNTHETIC] 场景A 通过：{SAT} 过境 {p['culmination_time']}（精度 "
          f"{p.get('time_precision_s')}s, TLE {p.get('tle_tier')}），"
          f"调谐 {tuned/1e6:.6f}MHz（Δ={shift:+.1f}Hz），APT 合成解码对齐 "
          f"{aligned} 行，库条目 {entry_id} 可查")


# ---------------------------------------------------------------------------
# 场景 B：天区方位 → 人工确认（替身）→ 接收 → 频谱出现信号
# ---------------------------------------------------------------------------
def test_scenario_b_sky_to_spectrum(agent, pass_case, tmp_path, monkeypatch):
    case = pass_case
    p, nominal = case["pass"], case["nominal_hz"]
    t_off = case["t_cul"] - 120.0                 # 便于让多普勒非零

    engine = get_time_engine()
    engine.set_unix(t_off)                        # 替身：时间拨到过境窗口
    try:
        r_sky = agent.tool_registry.call("sky_view_visible",
                                         {"observer_lat": LAT, "observer_lon": LON,
                                          "min_elevation": 5.0})
        assert r_sky.success, r_sky.content[:300]
        line, elev, azim, dopp = _parse_sky_line(r_sky.content, SAT)

        st = orbit.compute_satellite_state(SAT, LAT, LON, ALT, when=t_off)
        # 两条路径必须同刻：工具走 TimeEngine（拨表后 now_unix 可带亚秒），
        # 参考侧显式传 t_off。判据只比较同一时刻的两个实现，与过境几何无关
        # （K13：修复 decoders 的整秒截断后，全过境范围内差 ~1e-4°）。
        assert abs(elev - st["elevation"]) <= 0.2
        assert abs(azim - st["azimuth"]) <= 1.0
        assert abs(dopp - _doppler_hz(st["range_rate_kms"], nominal)) <= 60

        # 「人工确认硬件」：测试替身（无真机），在报告行里标注
        hardware_confirmed = True
        assert hardware_confirmed

        # 接收：调谐到标称下行；信号以合成单音落在预测的多普勒偏移处
        iq_path = tmp_path / "noaa19_predictive.cf32"
        fs, n = 48000, 8192
        t = np.arange(n) / fs
        rng = np.random.default_rng(20261104)
        tone = np.exp(2j * np.pi * dopp * t)
        noise = (rng.normal(0, 1, n) + 1j * rng.normal(0, 1, n)) * 0.02
        _write_cf32(iq_path, (tone + noise).astype(np.complex64))

        r_open = agent.tool_registry.call("sdr_open_iq_file", {
            "path": str(iq_path), "sample_rate_hz": fs,
            "center_freq_hz": int(round(nominal)),
        })  # 默认循环回放：频谱工具读过一轮后还能再读同一批样本复核
        assert r_open.success, r_open.content[:300]
        r_tune = agent.tool_registry.call("sdr_set_frequency",
                                         {"frequency_hz": int(round(nominal))})
        assert r_tune.success, r_tune.content[:300]

        # 工具面：频谱分析真的看到这颗信号
        r_spec = agent.tool_registry.call("sdr_spectrum_analyze",
                                          {"fft_size": n, "num_samples": n})
        assert r_spec.success, r_spec.content[:300]
        spec_report = json.loads(r_spec.content)
        assert abs(spec_report["peak_freq_mhz"] * 1e6 - (nominal + dopp)) <= 1500
        assert spec_report["peak_power_db"] - spec_report["noise_floor_db"] > 20

        # 精确复核：同一批样本过 SpectrumProcessor（FFT 分辨率 5.86Hz）
        backend = agent.sdr_manager.get_active()
        iq = backend.read_samples(n)
        spectrum = SpectrumProcessor(fft_size=n).compute_spectrum(
            iq, int(round(nominal)), fs, n)
        bin_hz = fs / n
        assert abs(spectrum.peak_freq - (nominal + dopp)) <= 2.5 * bin_hz
        assert spectrum.peak_freq != nominal
    finally:
        engine.set_time_now()

    print(f"[SYNTHETIC] 场景B 通过：{SAT} 在 {p['culmination_time']} 前 120s 的"
          f"天空方位 仰角{elev:.1f}° 方位{azim:.1f}°，预测多普勒 {dopp:+.0f}Hz；"
          f"合成 IQ 频谱峰值落在 {spectrum.peak_freq - nominal:+.0f}Hz"
          f"（SNR {spectrum.peak_power_db - spectrum.noise_floor_db:.1f}dB）。"
          f"人工确认与 IQ 均为替身，非真机")


def test_scenario_b_sky_guard_is_live(agent, pass_case, monkeypatch):
    """反证（K13）：工具不跟随拨过的时刻时，场景 B 的仰角判据必须红。

    场景 B 的价值全在「工具与 sgp4 直算在同一拨表时刻对得上」。这里把工具
    的物理时刻固定回退 60 s（等价于「跳时刻后没跟上」），同一个判据必须失败。
    不用 sleep、不看当前几点：偏移是常量，任何时刻跑出同样结果。
    """
    from mbdsdr_ai import sdr_tools

    t_off = pass_case["t_cul"] - 120.0
    engine = get_time_engine()
    engine.set_unix(t_off)
    try:
        monkeypatch.setattr(sdr_tools, "_physics_now", lambda args: t_off - 60.0)
        r_sky = agent.tool_registry.call("sky_view_visible",
                                         {"observer_lat": LAT, "observer_lon": LON,
                                          "min_elevation": -90.0})
        assert r_sky.success, r_sky.content[:300]
        _, elev, _azim, _dopp = _parse_sky_line(r_sky.content, SAT)

        st = orbit.compute_satellite_state(SAT, LAT, LON, ALT, when=t_off)
        with pytest.raises(AssertionError):
            assert abs(elev - st["elevation"]) <= 0.2
    finally:
        engine.set_time_now()


# ---------------------------------------------------------------------------
# 场景 C：多步计划 → 真实计算 → 缺硬件如实降级
# ---------------------------------------------------------------------------
class _ScriptedModel:
    """按脚本产出 tool_calls 的假模型（「拆解」由脚本给定，非自主推理）。"""
    model = "test-model"

    def __init__(self, turns):
        self.turns = list(turns)
        self.i = 0

    def chat_stream(self, messages, tools=None, **kw):
        turn = self.turns[min(self.i, len(self.turns) - 1)]
        self.i += 1
        yield {"delta": turn.get("content", "")}
        yield {"done": True, "success": True, "content": turn.get("content", ""),
               "tool_calls": turn.get("tool_calls", []),
               "usage": {"total_tokens": 10}}

    def parse_tool_calls_from_text(self, *_a, **_k):
        return []


def _call(name, args, call_id):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def test_scenario_c_plan_and_degrade(agent, pass_case, monkeypatch):
    case = pass_case
    nominal = case["nominal_hz"]
    tca = case["pass"]["culmination_time"]
    shift_now = _doppler_hz(case["state"]["range_rate_kms"], nominal)

    steps = [
        "用 sdr_satellite_passes 找今晚适合收图的过境",
        "用 sdr_satellite_doppler 预置下行多普勒",
        "用 sdr_set_frequency 调谐并准备接收",
    ]
    model = _ScriptedModel([
        {"tool_calls": [_call("sdr_plan",
                              {"goal": "今晚选一颗适合收图的卫星并准备接收",
                               "steps": steps}, "c1")]},
        {"tool_calls": [_call("sdr_satellite_passes", {
            "latitude": LAT, "longitude": LON, "altitude": ALT,
            "hours": 24, "min_elevation": 10, "satellite_type": "weather",
            "nominal_freq_hz": nominal}, "c2")]},
        {"tool_calls": [_call("sdr_satellite_doppler", {
            "satellite_name": SAT, "frequency_hz": int(round(nominal)),
            "latitude": LAT, "longitude": LON, "altitude": ALT}, "c3")]},
        {"tool_calls": [_call("sdr_set_frequency",
                              {"frequency_hz": int(round(nominal + shift_now))}, "c4")]},
        {"content": "计划已执行；当前无 SDR 硬件，调谐失败，已降级为计划态，未实际接收。"},
    ])
    agent.model_manager = model
    # 场景 C 的前提是「无硬件」：显式断开，不靠测试执行顺序
    agent.sdr_manager.disconnect()
    assert agent.sdr_manager.get_active() is None, "场景 C 需要无硬件初态"

    executed = []
    original_call = agent.tool_registry.call

    def spy(name, args, *a, **k):
        result = original_call(name, args, *a, **k)
        executed.append((name, result))
        return result

    monkeypatch.setattr(agent.tool_registry, "call", spy)
    result = agent.chat("今晚哪颗过境最适合收图像？然后准备好接收。")
    content = (result or {}).get("content") or (result or {}).get("message") or ""

    assert [n for n, _ in executed] == [
        "sdr_plan", "sdr_satellite_passes", "sdr_satellite_doppler",
        "sdr_set_frequency"]
    # 计划步骤真的被执行（第一步就落到了 plan 工具）
    plan_result = executed[0][1]
    assert plan_result.success and "今晚" in plan_result.content
    # 真实过境数据里确实有目标卫星与推荐时刻
    pass_result = executed[1][1]
    assert pass_result.success and SAT in pass_result.content
    assert _culmination_in_report(pass_result.content, tca), \
        f"报告里没有目标过境的中天（{tca}±3s）"
    # 缺硬件 -> 诚实失败，不谎报已接收
    tune_result = executed[3][1]
    assert tune_result.success is False
    assert "无连接设备" in tune_result.content or "失败" in tune_result.content
    assert "降级" in content and "未实际接收" in content

    print(f"[SYNTHETIC] 场景C 通过：假模型给定 3 步计划，Agent 按序执行 "
          f"{[n for n, _ in executed]}；真实过境数据含 {SAT} {tca}；"
          f"调谐在无硬件时如实失败并降级（拆解为假模型替身）")


# ---------------------------------------------------------------------------
# 反证：中天容差不是「随便一个时刻都放行」
# ---------------------------------------------------------------------------
def test_culmination_tolerance_helper_is_not_a_free_pass():
    content = ("   中天: 09:34:43 最大仰角 30.3°\n"
               "   中天: 22:43:56 最大仰角 12.1°\n")
    assert _culmination_in_report(content, "09:34:42"), "1 秒采样差必须放行"
    assert not _culmination_in_report(content, "09:34:30"), "13 秒差必须拒绝"
    assert not _culmination_in_report(content, "22:40:00"), "别场过境不许蒙混"
    assert _culmination_in_report("   中天: 00:00:01\n", "23:59:59"), \
        "跨午夜按环形距离比较"
