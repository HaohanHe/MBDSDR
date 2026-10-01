#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
MBDSDR 真机一条命令跑通联调向导
=================================

分步流程：detect → capture → record → decode → output（默认 all）。

红线：
  * 无硬件时每步明确失败原因，绝不 mock、绝不假数据。
  * 数字真实：采样点数 / 时长 / 文件字节自洽。
  * 产物全部标口径（captured / recorded）+ 参数 + 时间戳。

用法示例：
  python3 tools/onboarding/onboard.py --step detect
  python3 tools/onboarding/onboard.py --step all --freq 1090e6 --mode adsb
  python3 tools/onboarding/onboard.py --step all --freq 137.5e6 --mode apt --sr 2.4e6 --n 1.2e6
  python3 tools/onboarding/onboard.py --json --step detect
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

import numpy as np

# ---------------------------------------------------------------------------
# 路径引导：让脚本从仓库任意位置跑都能 import mbdsdr_ai / experiments.common
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent  # 仓库根
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
TOOL_NAME = "mbdsdr-onboarding"
TOOL_VERSION = "0.1.0"

# 默认参数
DEFAULT_FREQ_HZ = 1090e6       # ADS-B 1090 MHz
DEFAULT_SR_HZ = 2_400_000.0    # 2.4 MS/s
DEFAULT_N_SAMPLES = 240_000    # 0.1 s @ 2.4 MS/s（够 ADS-B 收几帧）
DEFAULT_GAIN_DB = 24.0

# rtl_sdr 输出：interleaved uint8 I/Q（offset binary，中心 127）
_RTL_BYTES_PER_SAMPLE = 2  # I + Q 各 1 字节

# SigMF cf32_le：complex64 = 2 × float32 = 8 字节/样本
_CF32_BYTES_PER_SAMPLE = 8

# 模式注册表：mode → (标签, 默认采样率, 说明)
MODES = {
    "adsb": {
        "label": "ADS-B Mode-S 1090 MHz",
        "default_sr": 2_400_000.0,
        "default_n": 240_000,
        "freq_hint": 1090e6,
        "desc": "飞机 ADS-B 报文直接 IQ 解码（CRC-24 校验）",
    },
    "apt": {
        "label": "NOAA APT 气象卫星云图",
        "default_sr": 2_400_000.0,
        "default_n": 4_800_000,  # 2 s @ 2.4 MS/s
        "freq_hint": 137.5e6,
        "desc": "NOAA 15/18/19 APT 广播（WFM 解调 → 图像）",
    },
    "cw": {
        "label": "CW 莫尔斯电报",
        "default_sr": 2_400_000.0,
        "default_n": 2_400_000,  # 1 s @ 2.4 MS/s
        "freq_hint": 7.02e6,
        "desc": "莫尔斯电报 AM 包络解码",
    },
    "ax25": {
        "label": "AX.25 / APRS 1200 baud",
        "default_sr": 2_400_000.0,
        "default_n": 4_800_000,  # 2 s @ 2.4 MS/s
        "freq_hint": 144.39e6,
        "desc": "APRS 位置报文（FM 解调 → AFSK → AX.25 FCS 校验）",
    },
}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class StepResult:
    step: str
    status: str = "RUNNING"   # PASS / FAIL / SKIP
    message: str = ""
    evidence: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def add_evidence(self, line: str) -> None:
        self.evidence.append(line)

    def add_fix(self, line: str) -> None:
        self.fixes.append(line)


def _now_iso_local() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _now_iso_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 工具：找 rtl_sdr
# ---------------------------------------------------------------------------
def find_rtl_sdr() -> Optional[str]:
    p = shutil.which("rtl_sdr")
    if p:
        return p
    local = Path.home() / ".local" / "bin" / "rtl_sdr"
    if local.exists() and os.access(local, os.X_OK):
        return str(local)
    return None


# ---------------------------------------------------------------------------
# Step 1: detect —— 复用 hw_selfcheck
# ---------------------------------------------------------------------------
def step_detect(timeout: float = 30.0) -> StepResult:
    """调用 hw_selfcheck/selfcheck.py --json，解析 RTL-SDR 设备状态。"""
    r = StepResult(step="detect", status="FAIL")

    selfcheck_path = _ROOT / "tools" / "hw_selfcheck" / "selfcheck.py"
    if not selfcheck_path.exists():
        r.message = f"hw_selfcheck 脚本不存在: {selfcheck_path}"
        r.add_fix("确认 tools/hw_selfcheck/selfcheck.py 存在（第四阶段已交付）")
        return r

    try:
        proc = subprocess.run(
            [sys.executable, str(selfcheck_path), "--json"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        r.message = f"hw_selfcheck 超时（{timeout}s）"
        r.add_fix("重跑或增大 --timeout")
        return r
    except Exception as e:  # noqa: BLE001
        r.message = f"hw_selfcheck 调用异常: {type(e).__name__}: {e}"
        return r

    if proc.returncode not in (0, 1):
        r.message = f"hw_selfcheck 退出码 {proc.returncode}"
        r.add_evidence(f"stderr: {proc.stderr.strip()[:500]}")
        return r

    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        r.message = f"hw_selfcheck JSON 解析失败: {e}"
        r.add_evidence(f"stdout 前 500 字符: {proc.stdout[:500]}")
        return r

    r.detail["selfcheck_summary"] = report.get("summary", {})
    r.detail["selfcheck_checks"] = [
        {"name": c["name"], "status": c["status"]} for c in report.get("checks", [])
    ]

    # 判断是否有 RTL-SDR 设备
    usb_check = next(
        (c for c in report.get("checks", []) if c["name"].startswith("1.")), None
    )
    has_device = False
    if usb_check:
        hits = usb_check.get("detail", {}).get("target_hits", [])
        has_device = len(hits) > 0
        for h in hits:
            r.add_evidence(f"USB 命中: {h.get('vid')}:{h.get('pid')} — {h.get('known_as', '?')}")

    if has_device:
        r.status = "PASS"
        r.message = "检测到 RTL-SDR 设备"
        r.add_evidence(f"hw_selfcheck 结论: {report.get('summary', {}).get('conclusion', '')}")
    else:
        r.status = "FAIL"
        r.message = "未检测到 RTL-SDR 设备（0bda:2838/2832）"
        r.add_fix("确认 RTL-SDR 已插入 USB2.0 口（避免 USB3.0 Hub）")
        r.add_fix("执行 `lsusb | grep -E '0bda:(2838|2832)'` 复核")
        r.add_fix("若有设备但打不开：sudo modprobe -r dvb_usb_rtl28xxu")
        todo = report.get("summary", {}).get("real_machine_todo", [])
        for t in todo:
            r.add_fix(f"  {t}")

    return r


# ---------------------------------------------------------------------------
# Step 2: capture —— rtl_sdr 起流写临时文件
# ---------------------------------------------------------------------------
def step_capture(
    freq_hz: float,
    sample_rate_hz: float,
    n_samples: int,
    out_raw_path: str,
    gain_db: float = DEFAULT_GAIN_DB,
    device_index: int = 0,
    timeout_safety: float = 30.0,
) -> StepResult:
    """跑 rtl_sdr -f freq -s sr -n nsamples 输出 interleaved uint8 IQ。"""
    r = StepResult(step="capture", status="FAIL")

    exe = find_rtl_sdr()
    if not exe:
        r.message = "未找到 rtl_sdr 命令（PATH 与 ~/.local/bin 均无）"
        r.add_fix("编译安装 librtlsdr 或 apt install rtl-sdr")
        return r

    # 采样率合理性检查（rtl-sdr 支持 225k~3.2M，典型 2.4M）
    if not (225_000 <= sample_rate_hz <= 3_200_000):
        r.message = f"采样率 {sample_rate_hz:.0f} Hz 超出 rtl-sdr 范围 (225k~3.2M)"
        r.add_fix("建议使用 2_400_000 Hz（2.4 MS/s）")
        return r

    # 频率范围检查（RTL2832U 24M~1700M）
    if not (24e6 <= freq_hz <= 1700e6):
        r.message = f"中心频率 {freq_hz/1e6:.1f} MHz 超出 RTL-SDR 范围 (24~1700 MHz)"
        r.add_fix("检查 --freq 单位（Hz），如 1090e6 = 1090 MHz")
        return r

    expected_bytes = n_samples * _RTL_BYTES_PER_SAMPLE
    est_seconds = n_samples / sample_rate_hz
    r.add_evidence(f"命令: {exe} -d {device_index} -f {freq_hz:.0f} -s {sample_rate_hz:.0f} "
                   f"-g {gain_db:.1f} -n {n_samples} {out_raw_path}")
    r.add_evidence(f"预计: {est_seconds:.3f} s, {expected_bytes:,} 字节 (uint8 I×Q interleaved)")

    # 硬超时：采集时长 + 启动/收尾余量
    hard_timeout = est_seconds + timeout_safety

    cmd = [
        exe,
        "-d", str(device_index),
        "-f", str(int(freq_hz)),
        "-s", str(int(sample_rate_hz)),
        "-g", str(gain_db),
        "-n", str(n_samples),
        out_raw_path,
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=hard_timeout, check=False)
    except subprocess.TimeoutExpired:
        r.message = f"rtl_sdr 超时（>{hard_timeout:.1f}s）"
        r.add_fix("检查设备是否被占用（rtl_tcp/gqrx/dump1090）；降低采样点数重跑")
        return r
    except FileNotFoundError:
        r.message = f"rtl_sdr 命令找不到: {exe}"
        return r
    except Exception as e:  # noqa: BLE001
        r.message = f"rtl_sdr 异常: {type(e).__name__}: {e}"
        return r

    stderr_tail = (proc.stderr or "").strip()[-800:]
    r.detail["rc"] = proc.returncode
    r.detail["stderr_tail"] = stderr_tail
    if stderr_tail:
        r.add_evidence(f"rtl_sdr stderr: {stderr_tail}")

    # 校验文件
    actual_bytes = 0
    try:
        actual_bytes = os.path.getsize(out_raw_path)
    except OSError:
        pass

    r.detail["raw_bytes_written"] = actual_bytes
    r.add_evidence(f"实际写入 {actual_bytes:,} 字节（预期 {expected_bytes:,}）")

    if actual_bytes <= 0:
        r.message = "rtl_sdr 未写入任何数据（文件为空）"
        r.add_fix("确认设备未被其他进程占用（rtl_tcp/gqrx/dump1090）")
        r.add_fix("拔出重插 USB，重新枚举")
        r.add_fix("检查 dmesg 是否有 USB 复位/枚举失败")
        return r

    # 丢包检测
    lost_bytes = 0
    for line in stderr_tail.splitlines():
        if "lost at least" in line.lower():
            import re
            m = re.search(r"lost at least\s+(\d+)\s+bytes", line, re.I)
            if m:
                lost_bytes += int(m.group(1))
    r.detail["lost_bytes_total"] = lost_bytes
    if lost_bytes > 0:
        r.add_evidence(f"警告：检测到丢包 {lost_bytes} 字节（USB 带宽/调度压力）")

    # 字节数自洽性检查（允许少量误差）
    expected_min = int(expected_bytes * 0.98)
    expected_max = int(expected_bytes * 1.02)
    if not (expected_min <= actual_bytes <= expected_max):
        r.message = (f"文件字节数 {actual_bytes:,} 与预期 {expected_bytes:,} 偏差过大"
                     f"（±2% 容差内 {expected_min:,}~{expected_max:,}）")
        r.add_fix("可能丢包严重，建议降低采样率或换 USB2.0 口")
        return r

    actual_samples = actual_bytes // _RTL_BYTES_PER_SAMPLE
    r.status = "PASS"
    r.message = f"采集完成：{actual_samples:,} 样本，{actual_bytes:,} 字节"
    r.detail["actual_samples"] = actual_samples
    r.detail["out_raw_path"] = out_raw_path
    return r


# ---------------------------------------------------------------------------
# Step 3: record —— uint8 IQ → SigMF cf32_le
# ---------------------------------------------------------------------------
def step_record(
    raw_path: str,
    out_dir: str,
    freq_hz: float,
    sample_rate_hz: float,
    gain_db: float,
    mode: str,
    hw_label: str = "RTL-SDR",
) -> StepResult:
    """把 rtl_sdr 的 interleaved uint8 IQ 转成 complex64 SigMF（与 IQPlayback 对齐）。"""
    r = StepResult(step="record", status="FAIL")

    if not os.path.exists(raw_path) or os.path.getsize(raw_path) == 0:
        r.message = f"raw IQ 文件不存在或为空: {raw_path}"
        r.add_fix("先跑 capture 步（--step capture 或 --step all）")
        return r

    os.makedirs(out_dir, exist_ok=True)

    # 读 uint8 interleaved I/Q
    try:
        raw = np.fromfile(raw_path, dtype=np.uint8)
    except OSError as e:
        r.message = f"读取 raw 文件失败: {e}"
        return r

    if raw.size % 2 != 0:
        r.message = f"raw 文件字节数 {raw.size} 为奇数，无法解析 interleaved I/Q"
        return r

    n_samples = raw.size // 2
    i = raw[0::2].astype(np.float32) - 127.5
    q = raw[1::2].astype(np.float32) - 127.5
    iq = (i + 1j * q).astype(np.complex64)

    r.add_evidence(f"读入 {raw.size:,} 字节 → {n_samples:,} 复样本 (complex64)")

    # 生成 SigMF 文件名
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{out_dir}/{stamp}_{int(freq_hz)}Hz_{mode}"
    data_path = base + ".sigmf-data"
    meta_path = base + ".sigmf-meta"

    # 写 data (complex64 little-endian)
    try:
        iq.tofile(data_path)
    except OSError as e:
        r.message = f"写 SigMF data 失败: {e}"
        return r

    actual_data_bytes = os.path.getsize(data_path)
    expected_data_bytes = n_samples * _CF32_BYTES_PER_SAMPLE
    r.add_evidence(f"SigMF data: {data_path} ({actual_data_bytes:,} 字节, 预期 {expected_data_bytes:,})")

    if actual_data_bytes != expected_data_bytes:
        r.message = f"SigMF data 字节数 {actual_data_bytes} ≠ 预期 {expected_data_bytes}"
        return r

    # 写 meta（与 cpp/src/dsp/recorder.cpp + playback.py 对齐）
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    meta = {
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": sample_rate_hz,
            "core:version": "1.0.0",
            "core:num_channels": 1,
            "core:frequency": freq_hz,
            "core:hw": hw_label,
            "core:author": "MBDSDR-onboarding",
            "core:num_samples": n_samples,
            "core:description": f"MBDSDR onboarding capture, mode={mode}, gain={gain_db}dB",
        },
        "captures": [
            {
                "core:sample_start": 0,
                "core:frequency": freq_hz,
                "core:datetime": now_iso,
                "mbdsdr:gain_db": gain_db,
                "mbdsdr:mode": mode,
            }
        ],
        "annotations": [],
    }

    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except OSError as e:
        r.message = f"写 SigMF meta 失败: {e}"
        return r

    r.add_evidence(f"SigMF meta: {meta_path}")
    r.add_evidence(f"  datatype=cf32_le, sample_rate={sample_rate_hz/1e6:.3f} MS/s, "
                   f"freq={freq_hz/1e6:.3f} MHz, samples={n_samples:,}")

    # 自洽验证：用 IQPlayback 回读
    try:
        from mbdsdr_ai.playback import IQPlayback, parse_sigmf_meta
        pb = IQPlayback()
        ok = pb.open(data_path)
        if ok:
            r.add_evidence(f"  IQPlayback 回读验证: {pb.n_samples} samples, "
                           f"fs={pb.sample_rate/1e6:.3f} MS/s, fc={pb.center_freq_hz/1e6:.3f} MHz")
            pb.close()
        else:
            r.message = "SigMF 写入后 IQPlayback 回读失败"
            r.add_fix("检查 meta JSON 格式与 data 文件 dtype")
            return r
    except ImportError as e:
        r.add_evidence(f"  (跳过 IQPlayback 回读验证: {e})")
    except Exception as e:  # noqa: BLE001
        r.add_evidence(f"  (IQPlayback 回读验证异常: {type(e).__name__}: {e})")

    r.status = "PASS"
    r.message = f"SigMF 录制完成: {n_samples:,} 样本"
    r.detail["sigmf_data"] = data_path
    r.detail["sigmf_meta"] = meta_path
    r.detail["n_samples"] = n_samples
    r.detail["duration_s"] = n_samples / sample_rate_hz if sample_rate_hz > 0 else 0
    return r


# ---------------------------------------------------------------------------
# Step 4: decode —— 真实解码器
# ---------------------------------------------------------------------------
def _demod_fm(iq: np.ndarray, fs: float, max_dev: float = 5_000.0) -> np.ndarray:
    """简单 FM 解调：atan2(conj(z1)*z2) 差分相位。返回实数音频包络。"""
    # 相位差分
    phase = np.angle(iq[1:] * np.conj(iq[:-1]))
    # 去归一化（粗略，不做完整去加重）
    audio = phase / (2 * np.pi) * fs / max_dev
    return audio.astype(np.float32)


def _demod_am_envelope(iq: np.ndarray) -> np.ndarray:
    """AM 包络检波：取幅度。"""
    return np.abs(iq).astype(np.float32)


def step_decode(
    sigmf_data_path: str,
    mode: str,
    sample_rate_hz: float,
    center_freq_hz: float,
) -> StepResult:
    """加载 SigMF IQ，按模式调用真实解码器。"""
    r = StepResult(step="decode", status="FAIL")

    if mode not in MODES:
        r.message = f"不支持的模式 '{mode}'，可选: {list(MODES.keys())}"
        return r

    if not os.path.exists(sigmf_data_path):
        r.message = f"SigMF data 文件不存在: {sigmf_data_path}"
        r.add_fix("先跑 record 步")
        return r

    # 加载 IQ
    try:
        iq = np.fromfile(sigmf_data_path, dtype=np.complex64)
    except OSError as e:
        r.message = f"加载 SigMF IQ 失败: {e}"
        return r

    r.add_evidence(f"加载 {iq.size:,} complex64 样本 @ {sample_rate_hz/1e6:.3f} MS/s")
    r.detail["n_samples"] = int(iq.size)
    r.detail["mode"] = mode

    # 按模式解码
    try:
        if mode == "adsb":
            from mbdsdr_ai import adsb
            results = adsb.decode_baseband(iq, sample_rate_hz)
            # decode_baseband 返回 dict 或 list[dict]
            frames = []
            if isinstance(results, dict):
                if results.get("crc_ok"):
                    frames.append(results)
            elif isinstance(results, list):
                frames = [f for f in results if f.get("crc_ok")]
            r.detail["decoded_frames"] = frames
            r.detail["n_frames"] = len(frames)
            r.add_evidence(f"ADS-B 解码：{len(frames)} 帧 CRC 校验通过")
            for i, f in enumerate(frames[:5]):
                r.add_evidence(f"  帧[{i}] icao={f.get('icao','?')} "
                               f"callsign={f.get('callsign','?')} "
                               f"altitude={f.get('altitude','?')}m "
                               f"speed={f.get('speed','?')}kt")
            r.status = "PASS" if frames else "FAIL"
            r.message = f"ADS-B 解码完成，{len(frames)} 有效帧"
            if not frames:
                r.add_fix("确认 1090 MHz 附近有飞机飞过；换天线位置/调高增益")

        elif mode == "ax25":
            from mbdsdr_ai.ax25 import AFSKModem
            # FM 解调 → 音频
            audio = _demod_fm(iq, sample_rate_hz, max_dev=5_000.0)
            # 重采样到 44100 Hz（AFSKModem 默认）
            fs_audio = 44100.0
            # 简单线性重采样
            n_out = int(audio.size * fs_audio / sample_rate_hz)
            audio_resampled = np.interp(
                np.linspace(0, audio.size - 1, n_out),
                np.arange(audio.size),
                audio,
            ).astype(np.float32)
            modem = AFSKModem()
            frames = modem.demodulate(audio_resampled)
            valid = [f for f in frames if f.fcs_valid]
            r.detail["decoded_frames"] = [
                {"src": f.source, "dst": f.destination,
                 "info": f.info[:80]}
                for f in valid
            ]
            r.detail["n_frames"] = len(valid)
            r.add_evidence(f"AX.25 解码：{len(valid)} 帧 FCS 校验通过")
            for i, f in enumerate(valid[:5]):
                r.add_evidence(f"  帧[{i}] {f.source} → {f.destination}: "
                               f"{f.info[:60]!r}")
            r.status = "PASS" if valid else "FAIL"
            r.message = f"AX.25 解码完成，{len(valid)} 有效帧"
            if not valid:
                r.add_fix("确认 APRS 频率（144.390 MHz 中国）有信号；调大增益")

        elif mode == "cw":
            from mbdsdr_ai import cw_decoder
            # AM 包络检波
            env = _demod_am_envelope(iq)
            # 降采样到合理音频率（11025 Hz 是 cw_decoder 默认）
            fs_audio = 11025.0
            n_out = int(env.size * fs_audio / sample_rate_hz)
            env_resampled = np.interp(
                np.linspace(0, env.size - 1, n_out),
                np.arange(env.size),
                env,
            ).tolist()
            dec = cw_decoder.decode_cw(env_resampled, sample_rate=fs_audio)
            text = dec.get("text", "")
            r.detail["decoded_text"] = text
            r.detail["cw_detail"] = {
                "wpm_est": dec.get("wpm_est"),
                "confidence": dec.get("confidence"),
                "dit_ms": dec.get("dit_ms"),
            }
            r.add_evidence(f"CW 解码结果: {text!r} "
                           f"(wpm≈{dec.get('wpm_est', '?')}, conf={dec.get('confidence', '?')})")
            r.status = "PASS" if text.strip() else "FAIL"
            r.message = f"CW 解码完成，{len(text.strip())} 字符"
            if not text.strip():
                r.add_fix("确认频率上有 CW 信号；调增益/调偏调点")

        elif mode == "apt":
            from mbdsdr_ai import noaa_apt_lite
            # WFM 解调 → 音频（APT 是 ~34 kHz 带宽，max_dev 取大些）
            audio = _demod_fm(iq, sample_rate_hz, max_dev=17_000.0)
            # APT 解码期望的采样率（noaa_apt_lite.APT_VIDEO_RATE）
            fs_apt = float(getattr(noaa_apt_lite, "APT_VIDEO_RATE", 4160.0))
            n_out = int(audio.size * fs_apt / sample_rate_hz)
            audio_apt = np.interp(
                np.linspace(0, audio.size - 1, n_out),
                np.arange(audio.size),
                audio,
            ).astype(np.float32)
            # 调用 APT 解码：decode_apt(audio, sample_rate) → dict
            result = noaa_apt_lite.decode_apt(audio_apt, sample_rate=fs_apt)
            img = result.get("image") or result.get("lines")
            r.detail["apt_result_keys"] = list(result.keys())
            r.detail["apt_image_shape"] = list(img.shape) if img is not None else None
            r.add_evidence(f"APT 解码: {len(result.get('lines_found', [])) if 'lines_found' in result else '?'} 行, "
                           f"image shape={img.shape if img is not None else 'None'}")
            r.status = "PASS" if img is not None and hasattr(img, 'size') and img.size > 0 else "FAIL"
            r.message = "APT 解码完成"
            if not (img is not None and hasattr(img, 'size') and img.size > 0):
                r.add_fix("确认 NOAA 卫星过境时间与仰角；对准天线；延长录制时长")
            r.detail["_apt_image"] = img  # 留给 output 步存图

    except ImportError as e:
        r.message = f"解码器模块导入失败: {e}"
        r.add_fix(f"确认 mbdsdr_ai 包完整安装（pip install -r requirements.txt）")
    except Exception as e:  # noqa: BLE001
        r.message = f"解码过程异常: {type(e).__name__}: {e}"
        r.add_fix(f"检查 IQ 数据质量；模式 {mode} 是否与频率匹配")

    return r


# ---------------------------------------------------------------------------
# Step 5: output —— 产物落盘
# ---------------------------------------------------------------------------
def step_output(
    out_dir: str,
    mode: str,
    freq_hz: float,
    sample_rate_hz: float,
    n_samples: int,
    decode_result: StepResult,
    data_origin: str = "captured",
) -> StepResult:
    """把解码结果落盘：报文/CSV/PNG/manifest，全部标口径+参数+时间戳。"""
    r = StepResult(step="output", status="FAIL")

    if data_origin not in ("captured", "recorded", "ota"):
        r.message = f"非法 data_origin: {data_origin}（只允许 captured/recorded/ota）"
        return r

    os.makedirs(out_dir, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    n_samples_out = int(n_samples)

    artifacts: list[str] = []

    # 1) 解码报文转储（JSON + TXT）
    frames = decode_result.detail.get("decoded_frames", [])
    messages_txt = os.path.join(out_dir, f"{stamp}_{mode}_messages.txt")
    messages_json = os.path.join(out_dir, f"{stamp}_{mode}_messages.json")

    with open(messages_txt, "w", encoding="utf-8") as f:
        f.write(f"# MBDSDR Onboarding Decoded Messages\n")
        f.write(f"# mode={mode} freq_hz={freq_hz:.0f} sample_rate_hz={sample_rate_hz:.0f}\n")
        f.write(f"# n_samples={n_samples_out} data_origin={data_origin}\n")
        f.write(f"# timestamp_utc={_now_iso_utc()}\n")
        f.write(f"# host={platform.node()}\n")
        f.write("=" * 60 + "\n\n")
        for i, fr in enumerate(frames):
            f.write(f"--- frame {i} ---\n")
            for k, v in fr.items():
                if k.startswith("_"):
                    continue
                f.write(f"  {k}: {v}\n")
            f.write("\n")
    artifacts.append(messages_txt)

    with open(messages_json, "w", encoding="utf-8") as f:
        json.dump({
            "tool": TOOL_NAME,
            "version": TOOL_VERSION,
            "mode": mode,
            "data_origin": data_origin,
            "params": {
                "freq_hz": freq_hz,
                "sample_rate_hz": sample_rate_hz,
                "n_samples": n_samples_out,
            },
            "timestamp_utc": _now_iso_utc(),
            "host": platform.node(),
            "frames": frames,
        }, f, ensure_ascii=False, indent=2)
    artifacts.append(messages_json)
    r.add_evidence(f"报文转储: {messages_txt} ({len(frames)} 帧)")

    # 2) 频谱 PNG（用 matplotlib Agg 无头）
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # 读 IQ 做 FFT 频谱
        sigmf_data = decode_result.detail.get("sigmf_data", "")
        if not sigmf_data or not os.path.exists(sigmf_data):
            # 尝试从 decode_result 找
            sigmf_data = decode_result.detail.get("_sigmf_data", "")

        if sigmf_data and os.path.exists(sigmf_data):
            iq = np.fromfile(sigmf_data, dtype=np.complex64)
            # 取前 1024*16 点做平均谱
            n_fft = 1024
            n_avgs = min(16, max(1, iq.size // n_fft))
            spec = np.zeros(n_fft, dtype=np.float64)
            for k in range(n_avgs):
                seg = iq[k * n_fft:(k + 1) * n_fft]
                if seg.size < n_fft:
                    break
                S = np.fft.fftshift(np.fft.fft(seg))
                spec += 20 * np.log10(np.abs(S) + 1e-12)
            spec /= n_avgs
            freqs_mhz = (np.fft.fftshift(np.fft.fftfreq(n_fft, 1.0 / sample_rate_hz))
                         / 1e6 + freq_hz / 1e6)

            fig, ax = plt.subplots(figsize=(8, 4))
            ax.plot(freqs_mhz, spec, lw=0.8)
            ax.set_xlabel("Frequency (MHz)")
            ax.set_ylabel("Power (dB)")
            ax.set_title(f"[{data_origin}, mode={mode}, N={n_samples_out}, "
                         f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d')}]")
            ax.grid(True, alpha=0.3)
            fig.tight_layout()
            spec_png = os.path.join(
                out_dir,
                f"spectrum__{data_origin}__{mode}__N{n_samples_out}__"
                f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d')}.png"
            )
            fig.savefig(spec_png, dpi=120)
            plt.close(fig)
            artifacts.append(spec_png)
            r.add_evidence(f"频谱图: {spec_png}")
    except ImportError:
        r.add_evidence("(matplotlib 不可用，跳过频谱图)")
    except Exception as e:  # noqa: BLE001
        r.add_evidence(f"(频谱图生成失败: {e})")

    # 3) APT 图像特殊处理
    apt_img = decode_result.detail.get("_apt_image")
    if apt_img is not None and mode == "apt":
        try:
            from PIL import Image
            apt_png = os.path.join(
                out_dir,
                f"apt_image__{data_origin}__{mode}__N{n_samples_out}__"
                f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d')}.png"
            )
            Image.fromarray((apt_img / apt_img.max() * 255).astype(np.uint8)).save(apt_png)
            artifacts.append(apt_png)
            r.add_evidence(f"APT 云图: {apt_png}")
        except ImportError:
            r.add_evidence("(PIL 不可用，跳过 APT 图像保存)")

    # 4) manifest.json（与 experiments/common/manifest.py 同口径）
    manifest_path = os.path.join(out_dir, f"{stamp}_manifest.json")
    manifest = {
        "script": "onboard.py",
        "data_origin": data_origin,
        "origin_label": {"captured": "捕获", "recorded": "录制",
                         "ota": "OTA"}[data_origin],
        "params": {
            "mode": mode,
            "freq_hz": freq_hz,
            "sample_rate_hz": sample_rate_hz,
            "n_samples": n_samples_out,
        },
        "n_samples": n_samples_out,
        "timestamp_utc": _now_iso_utc(),
        "code_version": "unknown",  # 不编造 git sha
        "license": "MIT",
        "artifacts": artifacts,
        "decode_summary": {
            "status": decode_result.status,
            "message": decode_result.message,
            "n_frames": decode_result.detail.get("n_frames", 0),
        },
    }
    try:
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        artifacts.append(manifest_path)
        r.add_evidence(f"manifest: {manifest_path}")
    except OSError as e:
        r.add_evidence(f"(manifest 写入失败: {e})")

    r.status = "PASS"
    r.message = f"产物落盘完成：{len(artifacts)} 个文件"
    r.detail["artifacts"] = artifacts
    r.detail["out_dir"] = out_dir
    return r


# ---------------------------------------------------------------------------
# 人类可读输出
# ---------------------------------------------------------------------------
_STATUS_COLOR = {"PASS": "\033[32m", "FAIL": "\033[31m", "SKIP": "\033[90m",
                 "RUNNING": "\033[36m"}
_RESET = "\033[0m"


def print_human(results: list[StepResult]) -> None:
    print("=" * 72)
    print(f"MBDSDR 真机联调向导  {_now_iso_local()}")
    print("=" * 72)
    for r in results:
        color = _STATUS_COLOR.get(r.status, "")
        print(f"\n[{color}{r.status}{_RESET}] step={r.step}")
        if r.message:
            print(f"  → {r.message}")
        for e in r.evidence:
            print(f"  {e}")
        if r.fixes:
            print("  下一步建议：")
            for f in r.fixes:
                print(f"   - {f}")
    print("\n" + "=" * 72)
    fails = [r for r in results if r.status == "FAIL"]
    if fails:
        print(f"失败 {len(fails)} 步：{[r.step for r in fails]}")
    else:
        print("全链路通过 ✓")
    print("=" * 72)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="MBDSDR 真机一条命令跑通联调向导",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
支持模式：
  {chr(10).join(f'  {k:10s} — {v["desc"]}' for k, v in MODES.items())}

示例：
  python3 onboard.py --step detect
  python3 onboard.py --step all --freq 1090e6 --mode adsb
  python3 onboard.py --step all --freq 137.5e6 --mode apt --n 4.8e6
  python3 onboard.py --json --step detect
        """,
    )
    ap.add_argument("--step", default="all",
                    choices=["detect", "capture", "record", "decode", "output", "all"],
                    help="执行步骤（默认 all 全链）")
    ap.add_argument("--freq", type=float, default=DEFAULT_FREQ_HZ,
                    help=f"中心频率 Hz（默认 {DEFAULT_FREQ_HZ:.0f}）")
    ap.add_argument("--sr", type=float, default=DEFAULT_SR_HZ,
                    help=f"采样率 Hz（默认 {DEFAULT_SR_HZ:.0f}）")
    ap.add_argument("--n", type=float, default=DEFAULT_N_SAMPLES,
                    help=f"采样点数（默认 {DEFAULT_N_SAMPLES:.0f}）")
    ap.add_argument("--gain", type=float, default=DEFAULT_GAIN_DB,
                    help=f"增益 dB（默认 {DEFAULT_GAIN_DB}）")
    ap.add_argument("--mode", default="adsb", choices=list(MODES.keys()),
                    help="解码模式（默认 adsb）")
    ap.add_argument("--out-dir", default="",
                    help="产物输出目录（默认 paper/experiments/onboarding_<mode>_<timestamp>）")
    ap.add_argument("--raw-path", default="",
                    help="capture 步 raw IQ 文件路径（默认临时文件）")
    ap.add_argument("--sigmf-data", default="",
                    help="decode/output 步直接指定 SigMF data 文件（跳过 capture/record）")
    ap.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="子进程超时（秒）")
    args = ap.parse_args(argv)

    n_samples = int(args.n)
    mode = args.mode

    # 模式默认参数覆盖
    mode_info = MODES[mode]
    if args.sr == DEFAULT_SR_HZ:
        args.sr = mode_info["default_sr"]
    if args.n == DEFAULT_N_SAMPLES:
        n_samples = mode_info["default_n"]

    # 输出目录
    if not args.out_dir:
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        args.out_dir = str(_ROOT / "paper" / "experiments" /
                           f"onboarding_{mode}_{stamp}")

    results: list[StepResult] = []
    tmp_raw: Optional[str] = None
    sigmf_data_path: str = args.sigmf_data

    try:
        # ---- detect ----
        if args.step in ("detect", "all"):
            det = step_detect(timeout=args.timeout)
            results.append(det)
            if det.status == "FAIL" and args.step == "all":
                if not args.json:
                    print_human(results)
                else:
                    print(json.dumps({"steps": [asdict(r) for r in results]},
                                     ensure_ascii=False, indent=2))
                return 1

        # ---- capture ----
        if args.step in ("capture", "all") and not sigmf_data_path:
            if args.step == "all" and (not results or results[0].status != "PASS"):
                cap = StepResult(step="capture", status="SKIP",
                                 message="detect 步未通过，跳过 capture")
                results.append(cap)
            else:
                if not args.raw_path:
                    tf = tempfile.NamedTemporaryFile(
                        prefix="mbdsdr_onboard_", suffix=".bin", delete=False)
                    tmp_raw = tf.name
                    tf.close()
                else:
                    tmp_raw = args.raw_path
                cap = step_capture(
                    freq_hz=args.freq,
                    sample_rate_hz=args.sr,
                    n_samples=n_samples,
                    out_raw_path=tmp_raw,
                    gain_db=args.gain,
                    timeout_safety=args.timeout,
                )
                results.append(cap)
                if cap.status == "FAIL" and args.step == "all":
                    if not args.json:
                        print_human(results)
                    else:
                        print(json.dumps({"steps": [asdict(r) for r in results]},
                                         ensure_ascii=False, indent=2))
                    return 1

        # ---- record ----
        if args.step in ("record", "all") and not sigmf_data_path:
            raw_path = tmp_raw or args.raw_path
            if not raw_path:
                rec = StepResult(step="record", status="FAIL",
                                 message="缺少 raw IQ 文件（先跑 capture 或指定 --raw-path）")
                results.append(rec)
            else:
                if args.step == "all" and (len(results) < 2 or results[-1].status != "PASS"):
                    rec = StepResult(step="record", status="SKIP",
                                     message="capture 步未通过，跳过 record")
                    results.append(rec)
                else:
                    rec = step_record(
                        raw_path=raw_path,
                        out_dir=args.out_dir,
                        freq_hz=args.freq,
                        sample_rate_hz=args.sr,
                        gain_db=args.gain,
                        mode=mode,
                    )
                    results.append(rec)
                    sigmf_data_path = rec.detail.get("sigmf_data", "")
                    if rec.status == "FAIL" and args.step == "all":
                        if not args.json:
                            print_human(results)
                        else:
                            print(json.dumps({"steps": [asdict(r) for r in results]},
                                             ensure_ascii=False, indent=2))
                        return 1

        # ---- decode ----
        if args.step in ("decode", "all"):
            if not sigmf_data_path or not os.path.exists(sigmf_data_path):
                dec = StepResult(step="decode", status="FAIL",
                                 message="缺少 SigMF data 文件（先跑 record 或指定 --sigmf-data）")
                results.append(dec)
            else:
                dec = step_decode(
                    sigmf_data_path=sigmf_data_path,
                    mode=mode,
                    sample_rate_hz=args.sr,
                    center_freq_hz=args.freq,
                )
                dec.detail["sigmf_data"] = sigmf_data_path
                results.append(dec)

        # ---- output ----
        if args.step in ("output", "all"):
            dec_result = next((r for r in reversed(results) if r.step == "decode"), None)
            if dec_result is None:
                out = StepResult(step="output", status="FAIL",
                                 message="无 decode 结果可输出（先跑 decode）")
                results.append(out)
            else:
                out = step_output(
                    out_dir=args.out_dir,
                    mode=mode,
                    freq_hz=args.freq,
                    sample_rate_hz=args.sr,
                    n_samples=n_samples,
                    decode_result=dec_result,
                    data_origin="captured",
                )
                results.append(out)

    finally:
        # 清理临时 raw 文件（保留 SigMF 产物）
        if tmp_raw and os.path.exists(tmp_raw) and not args.raw_path:
            try:
                os.unlink(tmp_raw)
            except OSError:
                pass

    # 输出
    if args.json:
        out_json = {
            "tool": TOOL_NAME,
            "version": TOOL_VERSION,
            "timestamp": _now_iso_local(),
            "host": platform.node(),
            "mode": mode,
            "params": {
                "freq_hz": args.freq,
                "sample_rate_hz": args.sr,
                "n_samples": n_samples,
                "gain_db": args.gain,
            },
            "steps": [asdict(r) for r in results],
        }
        print(json.dumps(out_json, ensure_ascii=False, indent=2, default=str))
    else:
        print_human(results)

    return 1 if any(r.status == "FAIL" for r in results) else 0


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGINT, lambda *_: sys.exit(130))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    sys.exit(main())
