"""scripts/decode_sensitivity.py — 各解码器的误码率/灵敏度基线（合成信号，离线）。

给「这个解码器到底多灵敏」一个有数可查的答案：用**仓内编码器**合成已知报文，
按固定网格注入加性高斯白噪声（AWGN），逐档统计「CRC 通过且字段正确」的成功率，
取最低达到目标成功率的 SNR 作为该模式的**实现灵敏度基线**。

口径与事实来源（不做任何凭记忆的数字）：
  * AIS：ITU-R M.1371 链路层（9600 bit/s GMSK，BT=0.4，HDLC 标志/FCS-16，
    NRZI）。波形由 ``mbdsdr_ai.ais_decoder.ais_modulate_iq`` 生成，判决由
    本仓 ``ais_decode_iq`` 完成——测的是本仓实现，不是任何厂商设备。
  * ADS-B 1090ES：ICAO Annex 10 Vol IV / RTCA DO-260（8µs 前导 + PPM，
    CRC-24 多项式 0xFFF409）。波形由 ``mbdsdr_ai.adsb.modulate_baseband``
    生成，判决由 ``decode_baseband`` 完成。
  * APRS / AX.25：TAPR AX.25 + Bell 202 AFSK 1200bd（mark 1200Hz /
    space 2200Hz）。音频取自随仓 fixture
    ``tests/fixtures/decode_pipeline/aprs_two_frames_48000.wav``（合成，
    NOT OTA），判决由 ``decode_pipeline.decode_aprs_audio`` 完成，
    成功 = 两帧 CRC-16 通过且整帧/信息字段与 manifest 真值逐字节一致。

必须如实声明的三点（否则这张表就变成「凭记忆的数字」）：
  1. 这里的 SNR 是**复基带突发内**的平均每样本信噪比（信号功率含前导与数据）；
     APRS 是**鉴频后音频域**的每样本信噪比（与复基带口径不同，不可横向换算），
     不是接收机噪声系数换算出来的天线口面 SNR，也不是任何法规/适航门限；
  2. 全是**合成波形**（无多径、无频偏、无采样时钟误差、无邻道干扰），
     因此只能回答「本实现相对本实现的相对灵敏度」，不能回答「能否收真实 OTA 信号」；
     真实录的结论见 docs/learn/decoder-capability-matrix.md 的 evidence 栏；
  3. 表格里的阈值会随 numpy 版本/浮点实现有 ±1 档抖动，故测试只钉**方向性**
     （高 SNR 必成功、极低 SNR 必失败、阈值落在网格内、同种子可复现）
     与 APRS 的**临界档区间**，不钉具体数字。

用法：
    python scripts/decode_sensitivity.py                     # 默认 AIS+ADS-B+APRS，JSON 到 stdout
    python scripts/decode_sensitivity.py --modes aprs --snr -8:16:2 --trials 30
    python scripts/decode_sensitivity.py --out logs/sensitivity.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DEFAULT_SNR_RANGE = (-10.0, 16.0, 2.0)
DEFAULT_TRIALS = 20
DEFAULT_TARGET = 0.9
DEFAULT_SEED = 20261104

# 每个模式的合成用例：固定报文内容，解码侧按同样字段比对（成功=CRC 通过且字段对）。
AIS_TEST_MMSI = 366000001
ADS_B_TEST_ICAO = "4CA8EE"
ADS_B_TEST_CALLSIGN = "BAW123"
APRS_WAV_FILE = "aprs_two_frames_48000.wav"
APRS_FIXTURE_DIR = os.path.join(ROOT, "tests", "fixtures", "decode_pipeline")


# ---------------------------------------------------------------------------
# SNR 网格解析与噪声注入
# ---------------------------------------------------------------------------
def parse_snr_grid(text: str) -> list:
    """``start:stop:step``（含 stop）或逗号列表 ``-6,0,6`` → 升序 dB 列表。"""
    text = str(text).strip()
    if ":" in text:
        parts = [float(p) for p in text.split(":")]
        if len(parts) != 3:
            raise ValueError(f"SNR 网格格式应为 start:stop:step，收到 {text!r}")
        start, stop, step = parts
        if step <= 0:
            raise ValueError("SNR 网格步长必须为正")
        out, v, eps = [], start, 1e-9
        while v <= stop + eps:
            out.append(round(v, 6))
            v += step
        if not out:
            raise ValueError(f"SNR 网格为空（start={start:g} stop={stop:g}）")
        return out
    vals = sorted({round(float(p), 6) for p in text.split(",") if p.strip()})
    if not vals:
        raise ValueError("SNR 网格为空")
    return vals


def _add_awgn(iq, snr_db: float, rng):
    """复基带加噪声（复用 adsb.add_awgn 的功率口径，避免两套定义漂移）。"""
    from mbdsdr_ai.adsb import add_awgn
    return add_awgn(iq, snr_db, rng)


def _add_awgn_audio(audio, snr_db: float, rng):
    """实数音频加噪声：噪声功率 = 信号平均功率 / 10^(SNR/10)。"""
    import numpy as np
    a = np.asarray(audio, dtype=np.float64)
    sig = float(np.mean(a * a)) if a.size else 0.0
    std = math.sqrt(sig / (10.0 ** (float(snr_db) / 10.0))) if sig > 0 else 0.0
    return a + rng.normal(0.0, std, a.size)


# ---------------------------------------------------------------------------
# 合成用例（clean IQ 只算一次；每个 SNR×trial 只重采样噪声）
# ---------------------------------------------------------------------------
def build_ais_case() -> dict:
    from mbdsdr_ai import ais_decoder as ais

    payload = ais.build_type123(mmsi=AIS_TEST_MMSI, lat_deg=37.0,
                                lon_deg=-122.0, sog_knots=10.0,
                                cog_deg=45.0, heading=90, msg_type=1)
    wire = ais.ais_frame_encode(payload)
    fs = 48000.0                       # 9600×5：满足 ≥2 样本/符号且整除
    return {
        "mode": "ais",
        "fs": fs,
        "iq": ais.ais_modulate_iq(wire, fs),
        "condition": "GMSK 9600 bit/s, fs=48000 (9600×5), 单帧 Type 1 168 bit",
        "source": "ITU-R M.1371；波形=mbdsdr_ai.ais_decoder.ais_modulate_iq",
    }


def build_adsb_case() -> dict:
    from mbdsdr_ai import adsb

    frame = adsb.build_identification_frame(ADS_B_TEST_ICAO,
                                            ADS_B_TEST_CALLSIGN)
    fs = 4e6                           # 4 样本/µs（4 样本/bit）
    return {
        "mode": "adsb",
        "fs": fs,
        "iq": adsb.modulate_baseband(frame, fs=fs, lead_us=0.0),
        "condition": "1090ES PPM 1 Mbit/s, fs=4e6 (4 样本/bit), DF17 识别帧",
        "source": "ICAO Annex 10 Vol IV / RTCA DO-260；波形=mbdsdr_ai.adsb.modulate_baseband",
    }


def build_aprs_case() -> dict:
    """随仓 48k APRS wav fixture + manifest 真值（不另造一套合成真值）。"""
    import wave

    import numpy as np

    path = os.path.join(APRS_FIXTURE_DIR, APRS_WAV_FILE)
    with wave.open(path, "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{APRS_WAV_FILE} 不是 16-bit PCM 单声道")
        rate = float(w.getframerate())
        pcm = w.readframes(w.getnframes())
    audio = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768.0

    with open(os.path.join(APRS_FIXTURE_DIR, "manifest.json"),
              encoding="utf-8") as f:
        manifest = json.load(f)
    wav_meta = next(f for f in manifest["aprs_wav"]["files"]
                    if f["file"] == APRS_WAV_FILE)
    truth = [(f["frame_hex"], f["info_hex"]) for f in wav_meta["frames"]]
    return {
        "mode": "aprs",
        "fs": rate,
        "audio": audio,
        "truth": truth,
        "frame_count": int(wav_meta["frame_count"]),
        "condition": (f"Bell 202 AFSK 1200bd，fs={rate:g} 音频域"
                      f"（{APRS_WAV_FILE}），2 帧 AX.25"),
        "source": ("TAPR AX.25 / Bell 202；音频=随仓合成 fixture "
                   "tests/fixtures/decode_pipeline/aprs_two_frames_48000.wav"
                   "（NOT OTA）"),
    }


def evaluate_aprs(audio, fs, case=None) -> tuple:
    """返回 (成功?, 失败原因, 明细)。成功=2/2 帧且整帧/信息字段逐字节一致。"""
    from mbdsdr_ai.decode_pipeline import decode_aprs_audio

    try:
        r = decode_aprs_audio(audio, target_fs=fs)
    except Exception as exc:
        return False, f"decode_error:{type(exc).__name__}", {}
    frames = r.get("content", {}).get("frames", []) if r.get("ok") else []
    detail = {
        "frames_decoded": len(frames),
        "frames_dropped": int(r.get("dropped_frames", 0) or 0),
    }
    if not r.get("ok"):
        return False, (r.get("reason") or "nothing_detected"), detail
    truth = case["truth"] if case else []
    got = [(f["frame_hex"], f["info_hex"]) for f in frames]
    if got == truth:
        return True, "", detail
    # 解出的帧集与真值不一致（含只解出 1/2 帧）：沿用既有 `payload_mismatch`
    # 分类，不新造失败码；「解出几帧」由 detail.frames_decoded 明示。
    return False, "payload_mismatch", detail


def evaluate_ais(iq, fs, case=None) -> tuple:
    """返回 (成功?, 失败原因)。成功=CRC 通过的 Type 1 报文且 MMSI 正确。"""
    from mbdsdr_ai import ais_decoder as ais

    try:
        out = ais.ais_decode_iq(iq, fs)
    except Exception as exc:                     # 解码异常也算失败，但原因要留痕
        return False, f"decode_error:{type(exc).__name__}"
    hit = any(m.get("mmsi") == AIS_TEST_MMSI for m in out["messages"])
    if hit:
        return True, ""
    if out["num_messages"] > 0:
        return False, "payload_mismatch"
    if out["dropped_frames"] > 0:
        return False, "fcs_or_stuffing_reject"
    return False, "nothing_detected"


def evaluate_adsb(iq, fs, case=None) -> tuple:
    """返回 (成功?, 失败原因)。成功=前导命中 + CRC 通过 + ICAO/呼号正确。"""
    from mbdsdr_ai import adsb

    try:
        r = adsb.decode_baseband(iq, fs=fs, long_frame=True)
    except Exception as exc:
        return False, f"decode_error:{type(exc).__name__}"
    if r.get("found") and r.get("crc_ok") and r.get("df") == 17 \
            and r.get("icao") == ADS_B_TEST_ICAO \
            and r.get("callsign") == ADS_B_TEST_CALLSIGN:
        return True, ""
    if not r.get("found"):
        return False, "preamble_not_found"
    if not r.get("crc_ok"):
        return False, "crc_failed"
    return False, "payload_mismatch"


MODE_CASES = {
    "ais": build_ais_case,
    "adsb": build_adsb_case,
    "aprs": build_aprs_case,
}
MODE_EVALUATORS = {
    "ais": evaluate_ais,
    "adsb": evaluate_adsb,
    "aprs": evaluate_aprs,
}

# 每个模式一个种子偏移：同种子下各模式噪声样本不共享，避免统计相关。
MODE_SEED_OFFSET = {"ais": 0, "adsb": 100003, "aprs": 200003}


# ---------------------------------------------------------------------------
# 扫描与阈值
# ---------------------------------------------------------------------------
def sweep_mode(mode: str, snr_values: list, trials: int = DEFAULT_TRIALS,
               seed: int = DEFAULT_SEED,
               target: float = DEFAULT_TARGET) -> dict:
    """对单个模式逐档 SNR 跑 ``trials`` 次，返回逐档成功数与失败原因分类。"""
    if mode not in MODE_CASES:
        raise ValueError(f"未知灵敏度模式: {mode!r}（可选 {sorted(MODE_CASES)}）")
    case = MODE_CASES[mode]()
    evaluate = MODE_EVALUATORS[mode]
    audio_mode = "audio" in case
    rows = []
    for idx, snr in enumerate(snr_values):
        rng = np_rng(seed + MODE_SEED_OFFSET.get(mode, 0) + idx)
        successes = 0
        reasons = {}
        decoded_total = 0
        dropped_total = 0
        first_failure = ""
        for _ in range(int(trials)):
            signal = (_add_awgn_audio(case["audio"], snr, rng) if audio_mode
                      else _add_awgn(case["iq"], snr, rng))
            res = evaluate(signal, case["fs"], case)
            if len(res) == 3:
                ok, reason, detail = res
            else:
                ok, reason, detail = res[0], res[1], {}
            decoded_total += int(detail.get("frames_decoded", 0))
            dropped_total += int(detail.get("frames_dropped", 0))
            if ok:
                successes += 1
            else:
                reasons[reason] = reasons.get(reason, 0) + 1
                if not first_failure:
                    first_failure = reason
        row = {
            "snr_db": float(snr),
            "successes": successes,
            "trials": int(trials),
            "success_rate": round(successes / float(trials), 4),
            "failure_reasons": reasons,
        }
        if audio_mode:
            row.update({
                "frames_decoded": decoded_total,
                "frames_dropped": dropped_total,
                "frames_expected": int(case.get("frame_count", 0) * trials),
                "first_failure_code": first_failure,
            })
        rows.append(row)
    return {
        "mode": mode,
        "fs_hz": case["fs"],
        "condition": case["condition"],
        "source": case["source"],
        "alternate_failure_reason": "synthetic AWGN only: no multipath/freq-offset/clock-error",
        "rows": rows,
        "thresholds": derive_thresholds(rows, target),
    }


def np_rng(seed: int):
    """延迟导入 numpy，脚本顶层保持无重依赖。"""
    import numpy as np
    return np.random.default_rng(int(seed))


def derive_thresholds(rows: list, target: float = DEFAULT_TARGET) -> dict:
    """从扫描结果取阈值：最低达标档、最低 100% 档；网格内无达标则显式 null。"""
    def lowest(min_rate):
        for r in sorted(rows, key=lambda x: x["snr_db"]):
            if r["success_rate"] >= min_rate - 1e-12:
                return r["snr_db"]
        return None

    return {
        "target_rate": float(target),
        "threshold_snr_db": lowest(target),
        "threshold_100pct_snr_db": lowest(1.0),
        "evaluable": lowest(target) is not None,
    }


def run_sensitivity(modes: list, snr_values: list, trials: int = DEFAULT_TRIALS,
                    seed: int = DEFAULT_SEED,
                    target: float = DEFAULT_TARGET) -> dict:
    """跑多模式扫描，产出可当审计证据的 JSON 报告。"""
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "definition": {
            "snr": "complex baseband per-sample SNR averaged over the burst "
                   "(preamble+data, no silence lead); APRS is measured on the "
                   "post-discriminator 48k audio (not comparable to baseband)",
            "success": "CRC/FCS passes AND decoded fields equal the synthesized truth",
            "noise": "additive white Gaussian, no multipath/frequency offset/clock error",
            "scope": "implementation baseline on synthetic waveforms only",
        },
        "seed": int(seed),
        "trials": int(trials),
        "snr_grid_db": [float(v) for v in snr_values],
        "target_rate": float(target),
        "modes": {m: sweep_mode(m, snr_values, trials, seed, target)
                  for m in modes},
    }


def markdown_table(report: dict) -> str:
    """把扫描报告整成一页 markdown（供能力矩阵文档/人工查阅复用）。"""
    lines = [
        "| 模式 | 采样条件 | 目标成功率 | 阈值 SNR | 100% 档 SNR | 网格范围 | 证据 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for mode, m in report["modes"].items():
        t = m["thresholds"]
        grid = report["snr_grid_db"]
        lines.append(
            f"| `{mode}` | {m['condition']} | {t['target_rate']:.0%} | "
            f"{_fmt_db(t['threshold_snr_db'])} | "
            f"{_fmt_db(t['threshold_100pct_snr_db'])} | "
            f"{grid[0]:g}..{grid[-1]:g} dB / {report['trials']} 次 | "
            f"合成 AWGN，种子 {report['seed']} |"
        )
    for mode, m in report["modes"].items():
        rows = m.get("rows") or []
        if not rows or "frames_decoded" not in rows[0]:
            continue
        lines += [
            "",
            f"`{mode}` 逐档明细（成功 = 两帧 CRC 通过且整帧/信息字段与真值"
            f"逐字节一致）：",
            "",
            "| SNR (dB) | 解出帧 / 期望 | 丢帧（CRC 失败） | 成功率 | 首个失败码 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for r in rows:
            lines.append(
                f"| {r['snr_db']:g} | {r['frames_decoded']} / "
                f"{r['frames_expected']} | {r['frames_dropped']} | "
                f"{r['success_rate']:.0%} | "
                f"{r['first_failure_code'] or '—（该档无失败）'} |")
        lines += [
            "",
            "注：`丢帧` 计的是 CRC 校验失败的候选帧；极低 SNR 下噪声会产生"
            "**多于真值帧数**的候选，因此该数可以超过「期望帧数」，"
            "不代表样本丢失。",
        ]
    return "\n".join(lines)


def _fmt_db(value) -> str:
    return "—（网格内未达标）" if value is None else f"{value:g} dB"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="解码器灵敏度基线（合成信号 + AWGN 扫描，不碰硬件）")
    ap.add_argument("--modes", default="ais,adsb,aprs",
                    help=f"逗号分隔，可选 {sorted(MODE_CASES)}")
    ap.add_argument("--snr", default="%g:%g:%g" % DEFAULT_SNR_RANGE,
                    help="SNR 网格 start:stop:step 或逗号列表（dB）")
    ap.add_argument("--trials", type=int, default=DEFAULT_TRIALS,
                    help="每档 SNR 的试验次数")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--target", type=float, default=DEFAULT_TARGET,
                    help="判定达标所需成功率（默认 0.9）")
    ap.add_argument("--out", help="报告 JSON 输出路径（缺省打印到 stdout）")
    ap.add_argument("--md", help="另存一页 markdown 表格")
    argv = sys.argv[1:] if argv is None else argv
    args = ap.parse_args(argv)

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    unknown = [m for m in modes if m not in MODE_CASES]
    if unknown:
        ap.error(f"未知模式: {unknown}（可选 {sorted(MODE_CASES)}）")
    if args.trials <= 0:
        ap.error("--trials 必须为正整数")
    snr_values = parse_snr_grid(args.snr)

    os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
    os.environ.setdefault("MBDSDR_NO_HW", "1")

    report = run_sensitivity(modes, snr_values, args.trials, args.seed,
                             args.target)

    if args.md:
        os.makedirs(os.path.dirname(os.path.abspath(args.md)), exist_ok=True)
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(markdown_table(report) + "\n")
        print(args.md)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(args.out)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
