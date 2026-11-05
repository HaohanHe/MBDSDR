# SPDX-License-Identifier: MIT
"""R3 解调与音频 —— WFM 鉴频数据面（全程静音，不开任何声卡）。

判据（自动断言）：
  * read_audio 返回 >= 4096 帧，RMS 在 (1e-4, 1.0) 之间（有能量且未削顶）
  * 音频能量集中在 15 kHz 以下（<15k / 15–24k 能量比 > 2）
  * 信号级 MPX 能测到 19 kHz 立体声导频（若该台为立体声；单声道台标“无导频”不判失败）
人眼项：音质/爆音/底噪听感——需真实出声，由操作者手动验，本脚本永不开声卡。
"""
from __future__ import annotations

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
    band = (fr > 15000) & (fr < 23000)
    idx = int(np.argmax(sp[band]))
    mag = float(sp[band][idx])
    neigh = float(np.median(sp[band]) + 1e-12)
    return float(fr[band][idx]), 20 * np.log10(mag / neigh)


def run(freq_hz: float) -> dict:
    out = common.artifact_dir("r3_demod_audio")
    from mbdsdr_ai.core import SDRController

    ctrl = SDRController()
    ctrl.connect(f"rtlsdr:{common.real_devices()[0].serial}")
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
                                       "IQ 不足或无导频（单声道台可能合法）", skipped=True))
        else:
            f_p, snr_p = pilot
            ok_p = abs(f_p - 19000) < 300 and snr_p > 10
            checks.append(common.check("stereo_pilot_19k", ok_p,
                                       f"导频 {f_p:.0f} Hz，高于邻域 {snr_p:.1f} dB;"
                                       f"{'立体声台' if ok_p else '未达立体声判据'}"))

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
    res = common.guarded("R3", "解调与音频", lambda: run(args.freq))
    raise SystemExit(common.emit(res, out))
