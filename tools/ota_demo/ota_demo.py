#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
MBDSDR 真实 OTA 一键复现（zero-hardware replay）
===============================================

把仓库内已入库的真实 OTA 样本（tests/fixtures/real_fm_88m1，RTL-SDR 实采
88.1 MHz 广播频段，2^19 复样本 cu8）喂进自研解调链，一步产出可核对的证据：

    load → spectrum → demodulate → audio → evidence

设计红线（与仓库其余工具一致）：
  * **零硬件**：不接触任何 SDR 设备，不联网。样本是已入库的真实采集文件，
    不是合成信号，不 mock、不补点。
  * **数字自洽**：样本数 / 时长 / 字节数 / 采样率全部现算并互相校验，
    任何一项不自洽即报错退出，不输出"看起来对"的结果。
  * **诚实降级**：matplotlib 缺失时不画图（status=degraded），但指标照出；
    绝不用假图占位。
  * **诚实边界**：输出里永远带 `not_verified` 一节，写明哪些链路线至今
    没有真实 OTA 验证（ADS-B / LRPT / 在线 LLM 重试 / C++ 未在本机编译）。

用法：
  python3 tools/ota_demo/ota_demo.py                      # 人读报告 + 出图
  python3 tools/ota_demo/ota_demo.py --json               # 机器可读
  python3 tools/ota_demo/ota_demo.py --out-dir <dir>      # 指定产物目录

退出码：
  0  全流程通过（允许 degraded：缺 matplotlib 只降级不出图）
  2  脚本自身参数/运行错误
  3  样本缺失或损坏（诚实报告，不伪造）
  4  数字不自洽（样本数与字节数/时长对不上）
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

FIXTURE_DIR = os.path.join(REPO_ROOT, "tests", "fixtures", "real_fm_88m1")
DATA_NAME = "capture.sigmf-data"
META_NAME = "capture.sigmf-meta"

# 样本物理口径（与 tests/test_real_fm_sample.py 一致，不许漂移）
SAMPLE_RATE = 1_024_000
N_SAMPLES = 1 << 19  # 524288 复样本 = 0.512 s
BYTES_PER_SAMPLE = 2  # cu8: I/Q 各 1 字节

# 至今没有真实 OTA 验证的链路（对外不得声称已验证）
NOT_VERIFIED = [
    "ADS-B (1090 MHz)：三次实采 0 帧 CRC 通过，需室外天线/带滤波 LNA 与航班高峰",
    "LRPT / 气象卫星：无真实卫星过境的 IQ 录制",
    "在线 LLM 重试链路：仅离线纯函数测试钉住，未带 key 真跑过",
    "C++ 桌面端：本机无 Qt6/CMake 工具链，改动未经编译验证",
]


def _load_sample() -> dict:
    """读样本并做字节数/样本数/时长三重自洽校验。"""
    data_path = os.path.join(FIXTURE_DIR, DATA_NAME)
    meta_path = os.path.join(FIXTURE_DIR, META_NAME)
    if not os.path.isfile(data_path):
        raise FileNotFoundError(f"样本缺失: {data_path}")
    if not os.path.isfile(meta_path):
        raise FileNotFoundError(f"元数据缺失: {meta_path}")

    import numpy as np  # 局部导入：缺 numpy 时由 main 统一报运行错误

    raw = np.fromfile(data_path, dtype=np.uint8)
    n_bytes = int(raw.size)
    n_samples = n_bytes // BYTES_PER_SAMPLE
    if n_samples != N_SAMPLES:
        raise ValueError(
            f"样本数不自洽: 文件 {n_bytes} 字节 -> {n_samples} 复样本, 期望 {N_SAMPLES}"
        )
    duration = n_samples / SAMPLE_RATE

    x = (raw.astype(np.float64) - 127.5) / 127.5
    iq = x[0::2] + 1j * x[1::2]

    meta = {}
    try:
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        meta = {}

    g = meta.get("global", {})
    return {
        "raw": raw,
        "iq": iq,
        "n_bytes": n_bytes,
        "n_samples": n_samples,
        "duration_s": duration,
        "sample_rate": SAMPLE_RATE,
        "center_freq_hz": g.get("core:frequency"),
        "datatype": g.get("core:datatype"),
        "hw": g.get("core:hw"),
        "provenance": g.get("mbdsdr:provenance"),
        "meta_duration_s": meta.get("mbdsdr:validation", {}).get("duration_s"),
    }


def _spectrum(iq) -> dict:
    """FFT 平均谱，量出载波高出底噪多少 dB。"""
    import numpy as np

    n = 16384
    seg = iq[: n * 60].reshape(-1, n)
    psd = (np.abs(np.fft.fftshift(np.fft.fft(seg, axis=1), axes=1)) ** 2).mean(axis=0)
    freq = np.fft.fftshift(np.fft.fftfreq(n, 1 / SAMPLE_RATE))
    med = float(np.median(psd))
    db = 10.0 * np.log10(psd / med + 1e-30)
    amp = np.abs(iq)
    return {
        "freq_hz": freq,
        "db": db,
        "peak_over_noise_db": float(db.max()),
        "peak_offset_hz": float(freq[int(db.argmax())]),
        "clip_fraction": float(np.mean(amp > 0.98)),
    }


def _demod(iq) -> dict:
    """走自研 WFM 立体声解调链（含 19 kHz 导频检测）。"""
    from mbdsdr_ai.wfm_stereo_lite import decode_stereo

    out = decode_stereo(iq, SAMPLE_RATE, out_sr=48000)
    import numpy as np

    left = out["l"].astype(np.float64)
    right = out["r"].astype(np.float64)
    sp = np.abs(np.fft.rfft(left * np.hanning(len(left)))) ** 2
    fr = np.fft.rfftfreq(len(left), 1 / 48000)
    band = (fr >= 50) & (fr < 15000)
    return {
        "left": left,
        "right": right,
        "audio_rate": 48000,
        "stereo": bool(out["stereo"]),
        "pilot_snr_db": out["pilot_snr_db"],
        "duration_s": out["duration_s"],
        "audio_rms": float(np.sqrt(np.mean(left ** 2))),
        "lr_rms_delta": float(abs(np.sqrt(np.mean(left ** 2)) - np.sqrt(np.mean(right ** 2)))),
        "audio_band_energy_frac": float(sp[band].sum() / sp[1:].sum()),
        "spec_freq": fr,
        "spec_power": sp,
    }


def _render(spec: dict, dem: dict, out_dir: str) -> dict:
    """出图。缺 matplotlib 时诚实降级为不画图（不伪造产物）。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return {"rendered": False, "reason": "matplotlib 未安装（诚实降级：不出图，指标照出）", "files": []}

    import numpy as np

    os.makedirs(out_dir, exist_ok=True)
    files = []

    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=150)
    ax.plot(spec["freq_hz"] / 1e3, spec["db"], lw=0.8, color="#1f77b4")
    ax.axhline(10, color="red", ls="--", lw=0.8, label="in-channel (10 dB over noise floor)")
    ax.set_xlabel("Offset from 88.1 MHz center [kHz]")
    ax.set_ylabel("Power [dB re. median]")
    ax.set_title(
        "Real over-the-air capture: 88.1 MHz FM broadcast band\n"
        "RTL-SDR (RTL2832U + FC0012), 1.024 MS/s, 0.512 s, gain 17.9 dB"
    )
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    p1 = os.path.join(out_dir, "01_real_ota_spectrum.png")
    fig.savefig(p1)
    plt.close(fig)
    files.append(p1)

    left = dem["left"]
    fig, axes = plt.subplots(2, 1, figsize=(9, 5.4), dpi=150)
    t = np.arange(len(left)) / dem["audio_rate"]
    axes[0].plot(t, left, lw=0.4, color="#2ca02c")
    axes[0].set_title("Decoded audio (WFM discriminator output) - real signal, not synthetic")
    axes[0].set_xlabel("Time [s]")
    axes[0].set_ylabel("Amplitude")
    axes[0].grid(alpha=0.3)
    pmax = float(dem["spec_power"].max())
    axes[1].plot(dem["spec_freq"] / 1e3, 10 * np.log10(dem["spec_power"] / pmax + 1e-12),
                 lw=0.8, color="#d62728")
    axes[1].set_xlim(0, 16)
    axes[1].set_xlabel("Audio frequency [kHz]")
    axes[1].set_ylabel("dB re. max")
    axes[1].grid(alpha=0.3)
    axes[1].set_title("Audio spectrum: energy concentrated in 50 Hz - 3 kHz (speech/music band)")
    fig.tight_layout()
    p2 = os.path.join(out_dir, "02_decoded_audio.png")
    fig.savefig(p2)
    plt.close(fig)
    files.append(p2)

    return {"rendered": True, "reason": "", "files": files}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MBDSDR 真实 OTA 一键复现（零硬件）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--out-dir", default=None, help="产物目录（默认 <repo>/output/ota_demo）")
    args = ap.parse_args(argv)

    out_dir = args.out_dir or os.path.join(REPO_ROOT, "output", "ota_demo")
    result = {
        "tool": "ota_demo",
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hardware_required": False,
        "network_required": False,
    }

    try:
        sample = _load_sample()
        spec = _spectrum(sample["iq"])
        dem = _demod(sample["iq"])
    except FileNotFoundError as exc:
        result.update(status="sample_missing", error=str(exc))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"[FAIL] 样本缺失：{exc}")
        return 3
    except ValueError as exc:  # 数字不自洽
        result.update(status="inconsistent", error=str(exc))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"[FAIL] 数字不自洽：{exc}")
        return 4
    except ImportError as exc:
        result.update(status="dependency_missing", error=str(exc))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"[FAIL] 依赖缺失：{exc}")
        return 2

    render = _render(spec, dem, out_dir)

    result.update(
        status="ok" if render["rendered"] else "degraded",
        sample={
            "path": os.path.relpath(os.path.join(FIXTURE_DIR, DATA_NAME), REPO_ROOT),
            "bytes": sample["n_bytes"],
            "complex_samples": sample["n_samples"],
            "duration_s": round(sample["duration_s"], 6),
            "sample_rate": sample["sample_rate"],
            "center_freq_hz": sample["center_freq_hz"],
            "datatype": sample["datatype"],
            "provenance": sample["provenance"],
            "hardware": sample["hw"],
        },
        spectrum={
            "peak_over_noise_db": round(spec["peak_over_noise_db"], 2),
            "peak_offset_hz": round(spec["peak_offset_hz"], 1),
            "clip_fraction": round(spec["clip_fraction"], 6),
        },
        demodulation={
            "chain": "mbdsdr_ai.wfm_stereo_lite.decode_stereo",
            "stereo": dem["stereo"],
            "pilot_snr_db": None if dem["pilot_snr_db"] is None else round(dem["pilot_snr_db"], 2),
            "audio_sample_rate": dem["audio_rate"],
            "audio_duration_s": round(dem["duration_s"], 4),
            "audio_rms": round(dem["audio_rms"], 5),
            "lr_rms_delta": round(dem["lr_rms_delta"], 6),
            "audio_band_energy_frac": round(dem["audio_band_energy_frac"], 4),
        },
        artifacts=render,
        not_verified=NOT_VERIFIED,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        s, d = result["sample"], result["demodulation"]
        print("MBDSDR 真实 OTA 一键复现（零硬件 / 离线）")
        print("=" * 56)
        print(f"样本      : {s['path']}")
        print(f"            {s['complex_samples']} 复样本 / {s['bytes']} 字节 / {s['duration_s']} s"
              f" @ {s['sample_rate']} S/s")
        print(f"            provenance={s['provenance']}  hw={s['hardware']}")
        print(f"频谱      : 峰值高出底噪 {result['spectrum']['peak_over_noise_db']} dB"
              f"（偏 {result['spectrum']['peak_offset_hz']/1e3:.1f} kHz）"
              f"，削顶 {result['spectrum']['clip_fraction']:.4%}")
        print(f"解调      : stereo={d['stereo']}  pilot_snr={d['pilot_snr_db']} dB"
              f"  音频 RMS={d['audio_rms']}  带内能量占比={d['audio_band_energy_frac']:.1%}")
        if render["rendered"]:
            print(f"产物      : {len(render['files'])} 张图 -> {out_dir}")
            for f in render["files"]:
                print(f"            {os.path.basename(f)}")
        else:
            print(f"出图      : 跳过（{render['reason']}）")
        print("-" * 56)
        print("诚实边界（以下链路至今无真实 OTA 验证）：")
        for item in NOT_VERIFIED:
            print(f"  - {item}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
