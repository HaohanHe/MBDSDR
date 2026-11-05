# SPDX-License-Identifier: MIT
"""R3 解调与音频 —— WFM 鉴频数据面（全程静音，不开任何声卡）。

判据（自动断言）：
  * read_audio 返回 >= 4096 帧，RMS 在 (1e-4, 1.0) 之间（有能量且未削顶）
  * 音频能量集中在 15 kHz 以下（<15k / 15–24k 能量比 > 2）
  * 信号级 MPX 能测到 19 kHz 立体声导频（若该台为立体声；单声道台标“无导频”不判失败）
人眼项：音质/爆音/底噪听感——需真实出声，由操作者手动验，本脚本永不开声卡。
"""
from __future__ import annotations

import os
import time
import wave

import numpy as np

import common

FS = 2.4e6
AUDIO_FS = 48000


def _pilot(fs: float, iq: np.ndarray) -> tuple[float, float] | None:
    """信号级 MPX 找 19 kHz 导频，返回 (频率 Hz, 高于邻域 dB)。"""
    x = np.asarray(iq, dtype=np.complex128)
    x = x - np.mean(x)
    dphi = np.diff(np.unwrap(np.angle(x)))
    mpx = dphi * fs / (2 * np.pi)
    dec = max(1, int(fs // 192000))
    mpx = mpx[::dec]
    fs2 = fs / dec
    n = min(131072, len(mpx))
    if n < 8192:
        return None
    sp = np.abs(np.fft.fft(mpx[:n] * np.hanning(n)))
    fr = np.arange(n) * fs2 / n
    # 导频只在 19 kHz ±500 Hz 内找；邻域取 16.5–18 kHz + 20–21.5 kHz。
    # 原来在 15–23 kHz 取全局最大会把节目/SCA 内容误当导频（实测 20.47 kHz）。
    band = (fr > 18500) & (fr < 19500)
    idx = int(np.argmax(sp[band]))
    mag = float(sp[band][idx])
    neigh_mask = (((fr > 16500) & (fr < 18000)) |
                  ((fr > 20000) & (fr < 21500)))
    neigh = float(np.median(sp[neigh_mask]) + 1e-12)
    return float(fr[band][idx]), 20 * np.log10(mag / neigh)


def _offline(skip_code: str = "NO_HW", why: str = "") -> dict:
    """无真机时的替代验收：真实 OTA 录音 → 产品解调链（wfm_stereo_lite），静音落盘。

    来历时序：``tests/fixtures/real_fm_88m1``（external-truth / real_ota，
    sha256 逐次校验）。本项 PASS 只证「解调数据面」，不证声卡/实时流。
    """
    out = common.artifact_dir("r3_demod_audio")
    fx = common.real_fm_fixture()
    if not fx.get("ok"):
        return common.skip_result(
            "R3", "解调与音频", "NO_DATA",
            f"{fx.get('reason', '真实 OTA 样本不可用')}；无真机且无替代样本，"
            "音频 RMS/导频均无数字可报。",
            metrics={"device_count": common.rtl_device_count()},
            checks=[common.check("offline_fixture", False, "真实 OTA 样本缺失或校验失败",
                                 skipped=True)],
            user_message_on_failure=common.USER_MSG["no_device"], device_required=True)

    fs, iq = fx["sample_rate"], fx["iq"]
    from mbdsdr_ai.wfm_stereo_lite import decode_stereo

    dec = decode_stereo(iq, fs, out_sr=AUDIO_FS)
    a = np.asarray(dec["l"], dtype=np.float64)
    frames = int(len(a))
    rms = float(np.sqrt(np.mean(a ** 2))) if frames else 0.0
    n = min(32768, frames)
    sp = np.abs(np.fft.fft(a[:n] * np.hanning(n))) if n > 0 else np.zeros(1)
    fr = np.arange(n) * AUDIO_FS / n if n > 0 else np.zeros(1)
    e_lo = float(sp[(fr > 50) & (fr < 15000)].sum())
    e_hi = float(sp[(fr >= 15000) & (fr < 24000)].sum()) + 1e-12
    ratio = e_lo / e_hi
    pilot = _pilot(fs, iq)

    checks = [
        common.check("fixture_is_real_ota",
                     fx["provenance"] == "real_ota" and fx["sha256"] == fx["expected_sha256"],
                     f"provenance={fx['provenance']} sha256={fx['sha256'][:16]}…"),
        common.check("read_audio_frames", frames >= 4096, f"{frames} 帧（离线解调）"),
        common.check("audio_rms_in_range", 1e-4 < rms < 1.0, f"RMS={rms:.4f}"),
        common.check("energy_below_15k", ratio > 2.0, f"<15k/>15k 能量比={ratio:.1f}"),
        common.check("audio_duration_matches_capture",
                     0.45 < float(dec.get("duration_s", 0.0)) < 0.56,
                     f"duration_s={dec.get('duration_s')}（样本 0.512 s）"),
        common.check("stereo_flag_from_product_chain", bool(dec.get("stereo")),
                     f"stereo={dec.get('stereo')} pilot_snr={dec.get('pilot_snr_db')}"),
    ]
    if pilot is None:
        checks.append(common.check("stereo_pilot_19k", False,
                                   "IQ 不足或无导频（单声道台可能合法）", skipped=True))
    else:
        f_p, snr_p = pilot
        checks.append(common.check("stereo_pilot_19k",
                                   abs(f_p - 19000) < 300 and snr_p > 10,
                                   f"导频 {f_p:.0f} Hz，高于邻域 {snr_p:.1f} dB"))

    wav_path = f"{out}/wfm_audio_replay.wav"
    pcm16 = (np.clip(a, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(AUDIO_FS)
        wf.writeframes(pcm16.tobytes())

    ok = all(c["ok"] for c in checks if not c["skipped"])
    metrics = {
        "mode": "offline_replay(real_ota)",
        "offline_substitute": "PASS" if ok else "FAIL",
        "fixture": {"sha256": fx["sha256"], "provenance": fx["provenance"],
                    "hardware": fx["hardware"], "capture_utc": fx["capture_utc"],
                    "path": fx["path"]},
        "target_hz": round(fx["center_hz"], 0),
        "frames": frames,
        "audio_rms": round(rms, 5),
        "energy_ratio_below_15k": round(ratio, 1),
        "wav_bytes": os.path.getsize(wav_path),
        "stereo": bool(dec.get("stereo")),
        "pilot_hz": round(pilot[0], 0) if pilot else None,
        "pilot_snr_db": round(pilot[1], 1) if pilot else None,
        "soundcard_opened": False,
        "replay_note": "产品解调链 decode_stereo 回放真实 OTA 样本；未打开声卡。",
    }
    if ok:
        return common.skip_result(
            "R3", "解调与音频", skip_code,
            f"{why or '无真机：实时流/声卡未验。'}"
            "离线替代（真实 OTA 样本 → 产品解调链）PASS，"
            f"RMS={rms:.4f}、<15k/>15k={ratio:.1f}、"
            f"导频={pilot[0]:.0f} Hz/{pilot[1]:.1f} dB；"
            "替代项只证解调数据面。",
            metrics, checks,
            common.USER_MSG["device_access_denied"] if skip_code == "NEEDS_OPERATOR"
            else common.USER_MSG["no_device"],
            ["音质/爆音/底噪听感（需真实出声）"], True, [wav_path])
    return common.result(
        "R3", "解调与音频", common.FAIL, metrics, checks,
        common.USER_MSG["no_signal"], ["音质/爆音/底噪听感（需真实出声）"], True,
        [wav_path],
        "离线替代失败：真实 OTA 样本经产品解调链未达判据（真缺陷，不是缺硬件）。")


def run(freq_hz: float) -> dict:
    if not common.device_present():
        return _offline()
    out = common.artifact_dir("r3_demod_audio")
    device_id = f"rtlsdr:{common.real_devices()[0].serial}"
    try:
        ctrl = common.open_controller(device_id)
    except Exception as e:
        if common.access_denied(e):
            return _offline("NEEDS_OPERATOR",
                            f"设备已枚举但打不开（{type(e).__name__}），实时流无法验证；")
        raise
    try:
        ctrl.set_sample_rate(FS)
        found, scan_pf = common.find_strong_fm(ctrl, 1024, FS)
        target = freq_hz or found
        if not target:
            return common.result("R3", "解调与音频", common.FAIL,
                                 {"scan_peak_floor_db": round(scan_pf, 1)},
                                 [common.check("station_found", False, "无显著 FM 台")],
                                 common.USER_MSG["no_signal"], ["音质听感（需出声）"], True)
        ctrl.set_frequency(target)
        ctrl.set_demod("WFM")
        time.sleep(0.3)

        audio = ctrl.read_audio(96000)
        if audio is None or len(audio) < 4096:
            return common.result("R3", "解调与音频", common.FAIL,
                                 {"target_hz": target},
                                 [common.check("read_audio", False, "None/过短")],
                                 common.USER_MSG["no_signal"], ["音质听感（需出声）"], True)
        a = np.asarray(audio, dtype=np.float32)
        channels = int(a.shape[1]) if a.ndim == 2 else 1
        if a.ndim == 2:
            a = a.mean(axis=1).astype(np.float32)
        rms = float(np.sqrt(np.mean(a ** 2)))

        n = min(32768, len(a))
        sp = np.abs(np.fft.fft(a[:n] * np.hanning(n)))
        fr = np.arange(n) * AUDIO_FS / n
        e_lo = float(sp[(fr > 50) & (fr < 15000)].sum())
        e_hi = float(sp[(fr >= 15000) & (fr < 24000)].sum()) + 1e-12
        ratio = e_lo / e_hi

        checks = [
            common.check("read_audio_frames", True, f"{len(a)} 帧 / {channels} 声道"),
            common.check("audio_rms_in_range", 1e-4 < rms < 1.0, f"RMS={rms:.4f}"),
            common.check("energy_below_15k", ratio > 2.0,
                         f"<15k/>15k 能量比={ratio:.1f}"),
        ]

        pilot = None
        iq = ctrl.read_iq(131072)
        if iq is not None and len(iq) >= 65536:
            pilot = _pilot(FS, iq)
        if pilot is None:
            checks.append(common.check("stereo_pilot_19k", False,
                                       "IQ 不足，无法测导频", skipped=True))
        else:
            f_p, snr_p = pilot
            ok_p = abs(f_p - 19000) < 100 and snr_p > 10
            weak = snr_p <= 10
            # 单声道台没有 19 kHz 导频是合法状态，不判失败，标 skipped 并写明。
            verdict = "立体声台" if ok_p else (
                "导频过弱，可能为单声道台（不判失败）" if weak else "19 kHz 导频频率异常")
            checks.append(common.check(
                "stereo_pilot_19k", ok_p,
                f"导频 {f_p:.0f} Hz，高于邻域 {snr_p:.1f} dB；{verdict}",
                skipped=not ok_p and weak))

        # 证据：把这段音频写成 wav（不经过声卡，仅落盘）
        wav_path = f"{out}/wfm_audio.wav"
        pcm = np.clip(a, -1.0, 1.0)
        pcm16 = (pcm * 32767).astype(np.int16)
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(AUDIO_FS)
            wf.writeframes(pcm16.tobytes())

        metrics = {
            "target_hz": round(target, 0),
            "frames": len(a),
            "channels": channels,
            "audio_rms": round(rms, 5),
            "energy_ratio_below_15k": round(ratio, 1),
            "pilot_hz": round(pilot[0], 0) if pilot else None,
            "pilot_snr_db": round(pilot[1], 1) if pilot else None,
            "soundcard_opened": False,
        }
        status = common.PASS if all(c["ok"] for c in checks if not c["skipped"]) else common.FAIL
        return common.result(
            "R3", "解调与音频", status, metrics, checks, common.USER_MSG["no_signal"],
            ["音质/爆音/底噪听感（需真实出声，脚本不开声卡）"], True,
            [wav_path],
            "数据面全自动；听感项需操作者放行出声后单独验。")
    finally:
        ctrl.disconnect()


if __name__ == "__main__":
    args = common.common_args("R3 解调与音频")
    out = common.artifact_dir("r3_demod_audio")
    res = common.guarded("R3", "解调与音频", lambda: run(args.freq),
                         device_required=False)
    raise SystemExit(common.emit(res, out))
