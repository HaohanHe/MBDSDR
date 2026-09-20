#!/usr/bin/env python3
"""
RTL-SDR 一键 FM 广播接收（MBDSDR）
==================================
插上 RTL2832U 棒，把当地调频广播解调为 WAV 音频，验证完整接收链路：

    python scripts/rtl_fm_listen.py --freq 98.5 --duration 10
    python scripts/rtl_fm_listen.py --freq 98.5 --ppm 42
    python scripts/rtl_fm_listen.py --tcp 127.0.0.1:1234 --freq 98.5   # rtl_tcp 兜底

输出：experiments/fm/<freq>mhz_<时间戳>.wav（48kHz 单声道，已做 50µs 去加重）
装了 sounddevice 会同时实时播放；没装则只存文件（Crostini 里可直接点开 WAV 听）。
另报告 RDS 57kHz 副载波是否存在（完整 RDS 文本解码为后续模块）。

中国大陆/欧洲/澳洲去加重 50µs（默认）；美国/韩国用 --deemph 75。
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def acquire(args):
    """返回 (IQ复数数组, 实际采样率)。"""
    import numpy as np
    from mbdsdr_ai.sdr_backend import RTLSDRBackend

    if args.tcp:
        host, _, port_s = args.tcp.partition(":")
        be = RTLSDRBackend(host=host, port=int(port_s or "1234"), ppm=args.ppm)
    else:
        be = RTLSDRBackend(device_index=args.index, ppm=args.ppm)

    if not be.connect():
        print("[FAIL] 打不开 RTL-SDR。先运行 python scripts/rtl_selfcheck.py 排查（驱动/USB透传/占用）。")
        sys.exit(1)
    print(f"[OK] 设备已打开，调谐器={be.tuner_name}")

    sr = args.rate
    be.set_sample_rate(sr)
    be.set_frequency(int(args.freq * 1e6))
    be.set_agc(True)
    # FM 广播段不需要 direct sampling
    n = int(sr * args.duration)
    print(f"[..] 调谐 {args.freq:.2f} MHz，采样率 {sr/1e6:.3f} MHz，采集 {args.duration:.1f}s ...")
    t0 = time.time()
    chunks = []
    got = 0
    block = int(sr)  # 每秒一块
    while got < n:
        part = be.read_samples(min(block, n - got))
        if part is None or len(part) == 0:
            time.sleep(0.02)
            continue
        chunks.append(np.asarray(part, dtype=np.complex128))
        got += len(part)
    be.disconnect()
    x = np.concatenate(chunks)[:n]
    print(f"[OK] 采到 {len(x)} 样本，用时 {time.time()-t0:.1f}s")
    return x, float(sr)


def main():
    ap = argparse.ArgumentParser(description="RTL-SDR 一键 FM 广播接收")
    ap.add_argument("--freq", type=float, default=98.0, help="FM 频率 MHz，默认 98.0")
    ap.add_argument("--duration", type=float, default=8.0, help="采集秒数，默认 8")
    ap.add_argument("--rate", type=float, default=240000, help="IQ 采样率 Hz，默认 240k")
    ap.add_argument("--ppm", type=int, default=0, help="晶振频偏校正 ppm")
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--tcp", type=str, default="", help="rtl_tcp 地址 host:port")
    ap.add_argument("--deemph", type=float, default=50.0, help="去加重 µs，中/欧 50，美/韩 75")
    ap.add_argument("--audio-sr", type=int, default=48000)
    args = ap.parse_args()

    import numpy as np
    import wave
    from mbdsdr_ai.dsp import wfm_broadcast_demod, rds_decode_from_wfm

    x, sr = acquire(args)

    # RDS 副载波检测（在原始基带速率上做）
    try:
        rds = rds_decode_from_wfm(x, sr)
        print(f"[RDS] 57kHz 副载波: {'检测到' if rds['rds_present'] else '未检测到'} "
              f"(相对电平 {rds['carrier_db']} dB)")
    except Exception as e:
        print(f"[RDS] 检测跳过: {e}")

    # WFM 解调
    audio = wfm_broadcast_demod(x, sr, audio_sr=args.audio_sr, deemph_us=args.deemph)
    rms = float(np.sqrt(np.mean(audio ** 2)))
    print(f"[OK] 解调音频 {len(audio)} 样本 @ {args.audio_sr}Hz，RMS={20*np.log10(rms+1e-12):.1f}dB")
    if rms < 1e-4:
        print("[WARN] 音频几乎静音：可能该频点无电台、天线未接、ppm 偏差大或频率不对，换强台复测。")

    # 存 WAV
    outdir = os.path.join(os.getcwd(), "experiments", "fm")
    os.makedirs(outdir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(outdir, f"{args.freq:.2f}mhz_{ts}.wav")
    pcm = np.clip(audio, -1, 1)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(args.audio_sr)
        wf.writeframes((pcm * 32767).astype(np.int16).tobytes())
    print(f"[OK] 已保存: {path}")

    # 可选实时播放
    try:
        import sounddevice as sd
        print("[..] 实时播放中（Ctrl+C 跳过）...")
        sd.play(audio, args.audio_sr)
        sd.wait()
    except ImportError:
        print("[i] 未装 sounddevice，不实时播放；在文件管理器点开上面的 WAV 即可听。")
    except Exception as e:
        print(f"[i] 实时播放失败（不影响文件）: {e}")


if __name__ == "__main__":
    main()
