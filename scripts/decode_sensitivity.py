"""scripts/decode_sensitivity.py — 各解码器的误码率/灵敏度基线（合成信号，离线）。

给「这个解码器到底多灵敏」一个有数可查的答案：用**仓内编码器**合成已知报文，
按固定网格注入加性高斯白噪声（AWGN），逐档统计「CRC 通过且字段正确」的成功率，
取最低达到目标成功率的 SNR 作为该模式的**实现灵敏度基线**。

口径与事实来源（不做任何凭记忆的数字）：
  * AIS：ITU-R M.1371 链路层（9600 bit/s GMSK，BT=0.4，HDLC 标志/FCS-16，
    NRZI）。波形取自随仓合成 fixture
    ``tests/fixtures/decode_pipeline/ais_two_frames.cf32``（NOT OTA），
    判决由本仓 ``ais_decode_iq`` 完成——测的是本仓实现，不是任何厂商设备。
  * ADS-B 1090ES：ICAO Annex 10 Vol IV / RTCA DO-260（8µs 前导 + PPM，
    CRC-24 多项式 0xFFF409）。波形取自
    ``tests/fixtures/decode_pipeline/adsb_three_frames.cf32``（NOT OTA），
    判决由 ``decode_baseband(max_frames=8)`` 完成，成功 = 三帧中至少
    一帧 CRC 通过且 ICAO/呼号与 manifest 真值一致。
  * APRS / AX.25：TAPR AX.25 + Bell 202 AFSK 1200bd（mark 1200Hz /
    space 2200Hz）。音频取自随仓 fixture
    ``tests/fixtures/decode_pipeline/aprs_two_frames_48000.wav``（合成，
    NOT OTA），判决由 ``decode_pipeline.decode_aprs_audio`` 完成，
    成功 = 两帧 CRC-16 通过且整帧/信息字段与 manifest 真值逐字节一致。
  * FT4：4-FSK GFSK（BT=1.0，12 kS/s，105 符号），波形与判决均在
    ``mbdsdr_ai.ft4_modem``；成功 = CRC 通过且消息文本等于合成真值。
  * POCSAG：CCIR Radiopaging Code No.1（1200bd FSK 音频，BCH(31,21)）；
    波形 ``pocsag_encode``、判决 ``pocsag_decode``；成功 = 21-bit 地址
    与报文均与合成真值一致。
  * ACARS：ARINC 618 风格 MSK（2400bd，中心 1800Hz，mark 2400/space
    1200）；波形 ``acars_protocol.acars_encode``、判决
    ``acars_decoder.acars_decode_iq``；成功 = CRC 通过且模式/注册号/
    标签/正文逐字段一致。

必须如实声明的三点（否则这张表就变成「凭记忆的数字」）：
  1. 每个模式的 SNR 口径由 ``MODE_BASIS`` 自述：噪声加在哪个域（复基带 /
     鉴频后音频）、带宽怎么定义（本脚本一律「噪声带宽 = fs/2 全带白噪」，
     未按占用带宽归一）、SNR 按每样本平均功率；
     **不同域/不同带宽的 dB 值不可横向换算**，更不是天线口面 SNR，
     也不是任何法规/适航门限；
  2. 全是**合成波形**（无多径、无频偏、无采样时钟误差、无邻道干扰），
     因此只能回答「本实现相对本实现的相对灵敏度」，不能回答「能否收真实 OTA 信号」；
     真实录的结论见 docs/learn/decoder-capability-matrix.md 的 evidence 栏；
  3. 表格里的阈值会随 numpy 版本/浮点实现有 ±1 次抖动，故测试只钉**方向性**
     （高 SNR 必成功、极低 SNR 必失败、阈值落在网格内、同种子可复现）、
     **90%/100% 档位与临界区间**，不钉具体数字；非单调一律标
     「边缘效应，未解释机理」，不挑好看的档位当结论。

用法：
    python scripts/decode_sensitivity.py                     # 六模式，各用其默认网格
    python scripts/decode_sensitivity.py --modes aprs --snr -8:16:2 --trials 30
    python scripts/decode_sensitivity.py --snr -10:16:2 --trials 20 --out logs/sensitivity.json
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

logger = logging.getLogger(__name__)

DEFAULT_SNR_RANGE = (-10.0, 16.0, 2.0)
DEFAULT_TRIALS = 20
DEFAULT_TARGET = 0.9
DEFAULT_SEED = 20261104

# 每个模式的合成用例：AIS/ADS-B/APRS 的真值来自随仓 fixture 的 manifest，
# 不在脚本里另造；FT4/POCSAG/ACARS 用仓内编码器现场合成（真值同源）。
PIPELINE_FIXTURE_DIR = os.path.join(ROOT, "tests", "fixtures", "decode_pipeline")
APRS_WAV_FILE = "aprs_two_frames_48000.wav"
APRS_FIXTURE_DIR = PIPELINE_FIXTURE_DIR

# 尚未迁到 fixture 的模式的固定合成用例（呼号按仓规用中性示例 BI1AAA）。
FT4_TEST_TEXT = "CQ BI1AAA OM74"
POCSAG_TEST_ADDRESS = 54321
POCSAG_TEST_MESSAGE = "HELLO"
POCSAG_TEST_BAUD = 1200
ACARS_TEST_FIELDS = {"mode": "2", "reg": "BA1234", "label": "H1",
                     "text": "TEST MESSAGE"}
ACARS_TEST_BAUD = 2400


def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_wav_fixture(path: str):
    import wave

    import numpy as np

    with wave.open(path, "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{os.path.basename(path)} 不是 16-bit PCM 单声道")
        rate = float(w.getframerate())
        pcm = w.readframes(w.getnframes())
    audio = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768.0
    return audio, rate


def _read_cf32_fixture(path: str):
    import numpy as np

    raw = np.fromfile(path, dtype="<f4")
    if raw.size % 2:
        raise ValueError(f"{os.path.basename(path)} 的 float32 数量为奇数")
    return (raw[0::2] + 1j * raw[1::2]).astype(np.complex128)


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
    meta = _load_json(os.path.join(PIPELINE_FIXTURE_DIR, "manifest.json"))["ais"]
    iq = _read_cf32_fixture(os.path.join(PIPELINE_FIXTURE_DIR, meta["file"]))
    return {
        "mode": "ais",
        "fs": float(meta["fs_hz"]),
        "iq": iq,
        "mmsi": int(meta["expected_fields"][0]["mmsi"]),
        "frame_count": int(meta["frame_count"]),
        "condition": (f"GMSK 9600 bit/s，fs={float(meta['fs_hz']):g}"
                       f"（9600×{int(float(meta['fs_hz']) // 9600)}），"
                       f"{meta['frame_count']} 帧 Type 1/5"),
        "source": ("ITU-R M.1371；波形=随仓合成 fixture "
                   "tests/fixtures/decode_pipeline/ais_two_frames.cf32（NOT OTA）"),
    }


def build_adsb_case() -> dict:
    meta = _load_json(os.path.join(PIPELINE_FIXTURE_DIR, "manifest.json"))["adsb"]
    iq = _read_cf32_fixture(os.path.join(PIPELINE_FIXTURE_DIR, meta["file"]))
    truth = next(f for f in meta["expected_frames"] if f.get("callsign"))
    return {
        "mode": "adsb",
        "fs": float(meta["fs_hz"]),
        "iq": iq,
        "truth": dict(truth),
        "frame_count": int(meta["frame_count"]),
        "condition": (f"1090ES PPM 1 Mbit/s，fs={float(meta['fs_hz']):g}"
                       f"（{int(float(meta['fs_hz']) / 1e6)} 样本/µs），"
                       f"{meta['frame_count']} 帧 DF17"),
        "source": ("ICAO Annex 10 Vol IV / RTCA DO-260；波形=随仓合成 fixture "
                   "tests/fixtures/decode_pipeline/adsb_three_frames.cf32（NOT OTA）"),
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


def build_ft4_case() -> dict:
    import numpy as np

    from mbdsdr_ai.ft4_codec import build_frame, encode_ft4_tones
    from mbdsdr_ai.ft4_modem import Ft4Modulator
    from mbdsdr_ai.ft8_encode import pack77_standard

    call1, call2, grid = FT4_TEST_TEXT.split()
    message77 = pack77_standard(call1, call2, grid)
    frame = np.asarray(build_frame(encode_ft4_tones(message77)), dtype=np.int8)
    return {
        "mode": "ft4",
        "fs": 12000.0,
        "iq": Ft4Modulator().modulate(frame),
        "truth_text": FT4_TEST_TEXT,
        "condition": "4-FSK GFSK（BT=1.0，12 kS/s，105 符号/5.04 s），消息 CQ BI1AAA OM74",
        "source": "FT4 4-FSK/LDPC(174,91)；波形=mbdsdr_ai.ft4_modem.Ft4Modulator",
    }


def build_pocsag_case() -> dict:
    from mbdsdr_ai.pocsag_decoder import pocsag_encode

    fs = 22050.0
    return {
        "mode": "pocsag",
        "fs": fs,
        "audio": pocsag_encode(POCSAG_TEST_ADDRESS, POCSAG_TEST_MESSAGE,
                               POCSAG_TEST_BAUD, sample_rate=fs),
        "address": POCSAG_TEST_ADDRESS,
        "message": POCSAG_TEST_MESSAGE,
        "baud": POCSAG_TEST_BAUD,
        "frame_count": 1,
        "condition": f"FSK 音频 {POCSAG_TEST_BAUD} bd，fs={fs:g}，地址 {POCSAG_TEST_ADDRESS} / HELLO",
        "source": "CCIR Radiopaging Code No.1（BCH(31,21)）；波形=mbdsdr_ai.pocsag_decoder.pocsag_encode",
    }


def build_acars_case() -> dict:
    from mbdsdr_ai.acars_protocol import acars_encode

    fs = 48000.0
    return {
        "mode": "acars",
        "fs": fs,
        "audio": acars_encode(ACARS_TEST_FIELDS["mode"],
                              reg=ACARS_TEST_FIELDS["reg"],
                              label=ACARS_TEST_FIELDS["label"],
                              text=ACARS_TEST_FIELDS["text"],
                              baud=ACARS_TEST_BAUD, sample_rate=fs),
        "truth": dict(ACARS_TEST_FIELDS),
        "baud": ACARS_TEST_BAUD,
        "frame_count": 1,
        "condition": f"MSK 音频 {ACARS_TEST_BAUD} bd（中心 1800 Hz），fs={fs:g}，1 帧",
        "source": "ARINC 618 风格 MSK；波形=mbdsdr_ai.acars_protocol.acars_encode",
    }


def evaluate_aprs(audio, fs, case=None) -> tuple:
    """返回 (成功?, 失败原因, 明细)。成功=2/2 帧且整帧/信息字段逐字节一致。"""
    from mbdsdr_ai.decode_pipeline import decode_aprs_audio

    try:
        r = decode_aprs_audio(audio, target_fs=fs)
    except Exception as exc:
        # 边界：单次试次解码异常不该中断整轮扫描，但必须留下可查原因
        # （异常类型 + 原文进 detail，随逐档 / JSON 报告落地），不是静默降级。
        detail = {"error": f"{type(exc).__name__}: {exc}",
                  "frames_decoded": 0, "frames_dropped": 0}
        return False, f"decode_error:{type(exc).__name__}", detail
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

    truth_mmsi = (case or {}).get("mmsi")
    try:
        out = ais.ais_decode_iq(iq, fs)
    except Exception as exc:                     # 解码异常也算失败，但原因要留痕
        return False, f"decode_error:{type(exc).__name__}"
    hit = any(m.get("mmsi") == truth_mmsi for m in out["messages"])
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

    truth = (case or {}).get("truth") or {}
    try:
        r = adsb.decode_baseband(iq, fs=fs, long_frame=True, max_frames=8)
    except Exception as exc:
        return False, f"decode_error:{type(exc).__name__}"
    frames = r.get("frames") or []
    for f in frames:
        if f.get("crc_ok") and f.get("df") == 17 \
                and f.get("icao") == truth.get("icao") \
                and f.get("callsign") == truth.get("callsign"):
            return True, ""
    if not r.get("found"):
        return False, "preamble_not_found"
    if not r.get("crc_ok"):
        return False, "crc_failed"
    return False, "payload_mismatch"


def evaluate_ft4(iq, fs, case=None) -> tuple:
    """返回 (成功?, 失败原因)。成功=CRC 通过且消息文本等于合成真值。"""
    from mbdsdr_ai.ft4_modem import Ft4Receiver

    truth = case["truth_text"] if case else FT4_TEST_TEXT
    try:
        r = Ft4Receiver(fs=int(round(fs))).decode(iq)
    except Exception as exc:
        # 单次试次异常按失败计，但不许静默：异常类型 + 原文落日志，原因码进报告。
        logger.warning("FT4 单次试次解码异常（按失败计）：%s: %s",
                       type(exc).__name__, exc)
        return False, f"decode_error:{type(exc).__name__}"
    if r.get("ok") and r.get("crc_ok") and r.get("text") == truth:
        return True, ""
    sync = r.get("sync")
    if sync is None or not getattr(sync, "synced", False):
        return False, "sync_not_found"
    if not r.get("parity_ok"):
        return False, "ldpc_parity_failed"
    if not r.get("crc_ok"):
        return False, "crc_failed"
    return False, "payload_mismatch"


def evaluate_pocsag(audio, fs, case=None) -> tuple:
    """返回 (成功?, 失败原因, 明细)。成功=地址与报文均与合成真值一致。"""
    from mbdsdr_ai.pocsag_decoder import pocsag_decode

    case = case or {}
    try:
        res = pocsag_decode(audio, fs, case.get("baud", POCSAG_TEST_BAUD))
    except Exception as exc:
        logger.warning("POCSAG 单次试次解码异常（按失败计）：%s: %s",
                       type(exc).__name__, exc)
        detail = {"error": f"{type(exc).__name__}: {exc}",
                  "frames_decoded": 0}
        return False, f"decode_error:{type(exc).__name__}", detail
    detail = {"frames_decoded": len(res)}
    if not res:
        return False, "nothing_detected", detail
    for r in res:
        if r.get("address") == case.get("address"):
            if str(r.get("message", "")).rstrip() == case.get("message"):
                return True, "", detail
            return False, "payload_mismatch", detail
    return False, "address_not_recovered", detail


def evaluate_acars(audio, fs, case=None) -> tuple:
    """返回 (成功?, 失败原因, 明细)。成功=CRC 通过且模式/注册号/标签/正文一致。"""
    from mbdsdr_ai.acars_decoder import acars_decode_iq

    case = case or {}
    try:
        res = acars_decode_iq(audio, sample_rate=fs,
                              baud_rate=case.get("baud", ACARS_TEST_BAUD))
    except Exception as exc:
        logger.warning("ACARS 单次试次解码异常（按失败计）：%s: %s",
                       type(exc).__name__, exc)
        detail = {"error": f"{type(exc).__name__}: {exc}",
                  "frames_decoded": 0}
        return False, f"decode_error:{type(exc).__name__}", detail
    detail = {"frames_decoded": len(res)}
    if not res:
        return False, "nothing_detected", detail
    truth = case.get("truth", ACARS_TEST_FIELDS)
    for m in res:
        if not m.get("crc_ok"):
            continue
        if (m.get("mode") == truth["mode"]
                and str(m.get("reg", "")).strip() == truth["reg"]
                and m.get("label") == truth["label"]
                and m.get("text") == truth["text"]):
            return True, "", detail
    if all(not m.get("crc_ok") for m in res):
        return False, "crc_failed", detail
    return False, "payload_mismatch", detail


MODE_CASES = {
    "ais": build_ais_case,
    "adsb": build_adsb_case,
    "aprs": build_aprs_case,
    "ft4": build_ft4_case,
    "pocsag": build_pocsag_case,
    "acars": build_acars_case,
}
MODE_EVALUATORS = {
    "ais": evaluate_ais,
    "adsb": evaluate_adsb,
    "aprs": evaluate_aprs,
    "ft4": evaluate_ft4,
    "pocsag": evaluate_pocsag,
    "acars": evaluate_acars,
}

# 每个模式自述口径：噪声加在哪个域、带宽怎么定义、SNR 按每样本/每符号怎么算。
# 这是「不同模式 dB 不可横比」的唯一书面来源，文档总表直接从这张表生成。
MODE_BASIS = {
    "ais": {
        "domain": "复基带（基带 IQ）",
        "bandwidth": "噪声带宽 = fs/2 = 120 kHz（fs=240 kS/s，全带白噪，未按 9600 bit/s 占用带宽归一）",
        "snr": "每样本：突发（含前导）内平均信号功率 / 噪声方差；GMSK 1 bit/符号（25 样本/符号）",
    },
    "adsb": {
        "domain": "复基带（基带 IQ）",
        "bandwidth": "噪声带宽 = fs/2 = 4 MHz（fs=8 M，全带白噪，未按 PPM 主瓣归一）",
        "snr": "每样本：整帧平均信号功率 / 噪声方差；PPM 1 bit/符号（8 样本/bit）",
    },
    "aprs": {
        "domain": "音频域（NFM 鉴频后）",
        "bandwidth": "噪声带宽 = fs/2 = 24 kHz（全带白噪，未按 1200 bd AFSK 占用带宽归一）",
        "snr": "每样本：整段平均信号功率 / 噪声方差；AFSK 1 bit/符号",
    },
    "ft4": {
        "domain": "复基带（基带 IQ）",
        "bandwidth": "噪声带宽 = fs/2 = 6 kHz（全带白噪，未按 4-FSK 约 83 Hz 占用带宽归一）",
        "snr": "每样本：整帧（5.04 s）平均信号功率 / 噪声方差；4-FSK 2 bit/符号（LDPC(174,91)）",
    },
    "pocsag": {
        "domain": "音频域（FM 鉴频后）",
        "bandwidth": "噪声带宽 = fs/2 = 11.025 kHz（全带白噪，未按 1200 bd FSK 占用带宽归一）",
        "snr": "每样本：整段（含 576 bit 前导）平均信号功率 / 噪声方差；FSK 1 bit/符号 + BCH(31,21)",
    },
    "acars": {
        "domain": "音频域（AM/FM 鉴频后）",
        "bandwidth": "噪声带宽 = fs/2 = 24 kHz（全带白噪，未按 2400 bd MSK 占用带宽归一）",
        "snr": "每样本：整段平均信号功率 / 噪声方差；MSK 1 bit/符号 + 奇校验/CRC-16",
    },
}

# 每个模式的默认档位网格（≥6 档；高 SNR 端必须能到 100%，低端必须能到 0%）。
MODE_DEFAULT_GRID = {
    "ais": parse_snr_grid("-10:16:2"),
    "adsb": parse_snr_grid("-10:16:2"),
    "aprs": parse_snr_grid("-10:16:2"),
    "ft4": parse_snr_grid("-24:0:2"),
    "pocsag": parse_snr_grid("0:18:2"),
    "acars": parse_snr_grid("-6:6:2"),
}
# 抽查用的「确定成功点」（测试断言 high-SNR 必成功）；不一定等于网格顶：
# POCSAG 在 18 dB 仍有偶发失败（实测 4/5），故取 28 dB，不拿网格顶冒充保证。
MODE_HIGH_SNR_DB = {"ais": 16.0, "adsb": 16.0, "aprs": 16.0,
                    "ft4": 0.0, "pocsag": 28.0, "acars": 12.0}

# 允许出现的失败原因分类（测试据此断言「失败必须有分类原因」）。
KNOWN_FAILURE_REASONS = {
    "nothing_detected", "no_frame_recovered", "truncated_frame", "crc_failed",
    "payload_mismatch", "insufficient_samples", "preamble_not_found",
    "fcs_or_stuffing_reject", "sync_not_found", "ldpc_parity_failed",
    "address_not_recovered",
}

# 每个模式一个种子偏移：同种子下各模式噪声样本不共享，避免统计相关。
MODE_SEED_OFFSET = {"ais": 0, "adsb": 100003, "aprs": 200003,
                    "ft4": 300007, "pocsag": 400009, "acars": 500009}


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
            "first_failure_code": first_failure,
        }
        if audio_mode:
            row.update({
                "frames_decoded": decoded_total,
                "frames_dropped": dropped_total,
                "frames_expected": int(case.get("frame_count", 0) * trials),
            })
        rows.append(row)
    return {
        "mode": mode,
        "fs_hz": case["fs"],
        "condition": case["condition"],
        "source": case["source"],
        "basis": dict(MODE_BASIS[mode]),
        "grid_db": [float(v) for v in snr_values],
        "alternate_failure_reason": "synthetic AWGN only: no multipath/freq-offset/clock-error",
        "rows": rows,
        "thresholds": derive_thresholds(rows, target),
        "monotonicity": derive_monotonicity(rows),
    }


def np_rng(seed: int):
    """延迟导入 numpy，脚本顶层保持无重依赖。"""
    import numpy as np
    return np.random.default_rng(int(seed))


def derive_thresholds(rows: list, target: float = DEFAULT_TARGET) -> dict:
    """从扫描结果取阈值：最低达标档、最低 100% 档、首个出现成功的档。

    临界区间 = [首个成功率 > 0 的档, 首次 100% 的档]；两端都必须在网格内，
    否则显式 null（不拿网格外的数字凑答案）。
    """
    def lowest(min_rate, strict=False):
        for r in sorted(rows, key=lambda x: x["snr_db"]):
            rate = r["success_rate"]
            if strict:
                if rate > 1e-12:
                    return r["snr_db"]
            elif rate >= min_rate - 1e-12:
                return r["snr_db"]
        return None

    first_success = lowest(0.0, strict=True)
    at_target = lowest(target)
    at_full = lowest(1.0)
    critical = ([first_success, at_full]
                if first_success is not None and at_full is not None else None)
    return {
        "target_rate": float(target),
        "threshold_snr_db": at_target,
        "threshold_100pct_snr_db": at_full,
        "first_success_snr_db": first_success,
        "critical_band_db": critical,
        "evaluable": at_target is not None,
    }


def derive_monotonicity(rows: list, jitter: int = 1) -> dict:
    """方向性判定：SNR 升高时成功数不得下降（允许相邻档回落 ≤ ``jitter`` 次）。

    回落超过 jitter 即记一条 violation，并统一标注「边缘效应，未解释机理」，
    不挑好看的档位当结论。
    """
    ordered = sorted(rows, key=lambda x: x["snr_db"])
    violations = []
    for prev, cur in zip(ordered, ordered[1:]):
        drop = int(prev["successes"]) - int(cur["successes"])
        if drop > int(jitter):
            violations.append({
                "from_snr_db": prev["snr_db"],
                "to_snr_db": cur["snr_db"],
                "from_successes": int(prev["successes"]),
                "to_successes": int(cur["successes"]),
                "drop": drop,
            })
    return {
        "monotonic": not violations,
        "jitter_trials": int(jitter),
        "violations": violations,
        "note": ("" if not violations
                 else "边缘效应，未解释机理"),
    }


def run_sensitivity(modes: list, snr_values=None, trials: int = DEFAULT_TRIALS,
                    seed: int = DEFAULT_SEED,
                    target: float = DEFAULT_TARGET) -> dict:
    """跑多模式扫描，产出可当审计证据的 JSON 报告。

    ``snr_values=None`` 时每个模式用各自的默认网格（``MODE_DEFAULT_GRID``），
    dB 值跨模式不可横比，因此默认不强行共享一张网格。
    """
    grids = {m: (list(snr_values) if snr_values is not None
                 else list(MODE_DEFAULT_GRID[m])) for m in modes}
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "definition": {
            "snr": "per-sample signal/noise power ratio in the mode's own "
                   "domain (complex baseband or post-discriminator audio); "
                   "noise bandwidth = fs/2, not normalized to occupied "
                   "bandwidth; dB values are NOT comparable across modes",
            "basis_per_mode": {m: dict(MODE_BASIS[m]) for m in modes},
            "success": "CRC/FCS passes AND decoded fields equal the synthesized truth",
            "noise": "additive white Gaussian, no multipath/frequency offset/clock error",
            "scope": "implementation baseline on synthetic waveforms only (NOT OTA)",
        },
        "seed": int(seed),
        "trials": int(trials),
        "snr_grid_db": ([float(v) for v in snr_values]
                        if snr_values is not None else None),
        "target_rate": float(target),
        "modes": {m: sweep_mode(m, grids[m], trials, seed, target)
                  for m in modes},
    }


def _basis_text(basis: dict) -> str:
    return (f"{basis['domain']}｜{basis['bandwidth']}｜{basis['snr']}")


def _critical_text(t: dict) -> str:
    band = t.get("critical_band_db")
    if not band:
        return "—（网格内缺端点）"
    return f"{band[0]:g}..{band[1]:g} dB"


def _monotonic_text(m: dict) -> str:
    mo = m.get("monotonicity") or {}
    if mo.get("monotonic"):
        return "是"
    detail = "；".join(
        f"{v['from_snr_db']:g}→{v['to_snr_db']:g} dB 成功数 "
        f"{v['from_successes']}→{v['to_successes']}" for v in mo.get("violations", []))
    return f"否（{mo.get('note', '')}：{detail}）"


def markdown_table(report: dict) -> str:
    """把扫描报告整成一页 markdown（供能力矩阵文档/人工查阅复用）。

    总表只钉四件事：方向性（是否单调）、90% 档、100% 档、临界区间；
    不钉具体 dB 数值（各模式口径不同，禁止横比）。
    """
    lines = [
        "> 全部为**合成 fixture + AWGN**，非 OTA；不同模式的噪声域/带宽口径不同，"
        "dB 值**不可横向比较**。",
        "",
        "| 模式 | 口径（噪声域｜带宽｜SNR） | 档位网格 | 90% 档 | 100% 档 | 临界区间 | 首个失败码 | 是否单调 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for mode, m in report["modes"].items():
        t = m["thresholds"]
        grid = m["grid_db"]
        first_bad = next((r["first_failure_code"] for r in m["rows"]
                          if r["first_failure_code"]), "")
        lines.append(
            f"| `{mode}` | {_basis_text(m['basis'])} | "
            f"{grid[0]:g}..{grid[-1]:g} dB（{len(grid)} 档 / {report['trials']} 次） | "
            f"{_fmt_db(t['threshold_snr_db'])} | "
            f"{_fmt_db(t['threshold_100pct_snr_db'])} | "
            f"{_critical_text(t)} | "
            f"{first_bad or '—（网格内无失败）'} | "
            f"{_monotonic_text(m)} |"
        )
    lines += [
        "",
        "临界区间 = 「首个成功率 > 0 的档」到「首次 100% 的档」；"
        "相邻档成功数允许回落 ≤1 次（20 次/档下的 ±1 次抖动），"
        "回落更多即判非单调并标「边缘效应，未解释机理」。",
    ]
    for mode, m in report["modes"].items():
        rows = m.get("rows") or []
        if not rows or "frames_decoded" not in rows[0]:
            continue
        lines += [
            "",
            f"`{mode}` 逐档明细（成功 = CRC/FCS 通过且字段与合成真值一致）：",
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
    ap.add_argument("--modes", default=",".join(sorted(MODE_CASES)),
                    help=f"逗号分隔，可选 {sorted(MODE_CASES)}")
    ap.add_argument("--snr", default=None,
                    help="SNR 网格 start:stop:step 或逗号列表（dB）；缺省用各模式默认网格")
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
    snr_values = parse_snr_grid(args.snr) if args.snr else None

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
