"""
信号触发式长守听录音机（gated / VOX recorder）
================================================

长时间无人值守守听一个频率，**只在真正有信号时才落盘**：

- 无载波 / 只有底噪时不产生任何录音；
- 信号到来自动开录，带很短的 pre-roll，不会吃掉字头；
- 用平滑的音频包络门控，说话间隙的噪声被压成数字静音（不是嘶声）；
- 一次发射 / 一通对话结束（载波消失超过分段拖尾）自动切成一个 WAV；
- 每个 WAV 只含干净信号，并做温和的电平归一化。

适用于：守听机场塔台 / 进近（航空 AM）、火腿通联（NFM/SSB/CW），
也可用于宽带广播 WFM（连续载波，按解调后音频形态门控）。

判定核心（:class:`GatedRecorder`）与采集、解调完全分离，可离线用
合成信号严格验证；真实设备由 :func:`run_session` 经 SDR 后端驱动，
命令行入口见模块底部（``python -m mbdsdr_ai.gated_recorder``）。
"""

from __future__ import annotations

import json
import math
import os
import time
from collections import deque
from datetime import datetime
from typing import Callable, Deque, Dict, List, Optional

import numpy as np

from .squelch import AutoSquelch, NoiseSquelch, rms_dbfs


# =====================================================================
# 1) 平滑音频包络（快速起 / 慢速落，消除间隙嘶声，无爆音）
# =====================================================================
class EnvelopeShaper:
    """一阶 IIR 包络跟随门控，输出 audio*envelope。"""

    def __init__(self, sample_rate: float, attack_ms: float = 10.0,
                 release_ms: float = 60.0):
        self.sr = float(sample_rate)
        self._att = math.exp(-1.0 / max(attack_ms / 1000.0, 1e-4) / self.sr)
        self._rel = math.exp(-1.0 / max(release_ms / 1000.0, 1e-4) / self.sr)
        self.env: float = 0.0

    def shape(self, x: np.ndarray, gate: bool) -> np.ndarray:
        n = len(x)
        if n == 0:
            return x
        k = np.arange(1, n + 1)
        if gate:
            env = 1.0 - (1.0 - self.env) * (self._att ** k)
        else:
            env = self.env * (self._rel ** k)
        self.env = float(env[-1])
        return (x * env).astype(np.float32)

    def reset(self) -> None:
        self.env = 0.0


# =====================================================================
# 2) 门控录音机核心（零硬件依赖，feed 单声道音频 + 门控布尔）
# =====================================================================
class GatedRecorder:
    """
    feed(mono, gate) 逐块喂入解调后单声道音频与门控结果，
    分段写 WAV；返回本段时间产生的事件（segment_saved）。
    """

    def __init__(self, audio_sr: float, mode: str, outdir: str,
                 frequency_hz: float = 0.0,
                 preroll_s: float = 0.15,
                 attack_ms: float = 10.0, release_ms: float = 60.0,
                 segment_hang_s: float = 1.5,
                 max_segment_s: float = 300.0,
                 target_peak: float = 0.9, max_gain: float = 8.0,
                 min_segment_s: float = 0.25,
                 clock: Callable[[], float] = time.time):
        self.sr = float(audio_sr)
        self.mode = str(mode).upper()
        self.outdir = str(outdir)
        os.makedirs(self.outdir, exist_ok=True)
        self.frequency_hz = float(frequency_hz)
        self.preroll_n = int(round(preroll_s * self.sr))
        self.hang_n = int(round(segment_hang_s * self.sr))
        self.max_seg_n = int(round(max_segment_s * self.sr))
        self.target_peak = float(target_peak)
        self.max_gain = float(max_gain)
        self.min_seg_n = int(round(min_segment_s * self.sr))
        self._clock = clock

        self.shaper = EnvelopeShaper(self.sr, attack_ms, release_ms)
        self._ring: Deque[np.ndarray] = deque()
        self._ring_n = 0
        self._state = "IDLE"
        self._seg: List[np.ndarray] = []
        self._seg_n = 0
        self._closed_n = 0
        self._seg_start: float = 0.0
        self.session_id = datetime.fromtimestamp(self._clock()).strftime(
            "%Y%m%d_%H%M%S")
        self.manifest_path = os.path.join(
            self.outdir, f"session_{self.session_id}.jsonl")
        self.segments: List[Dict] = []

    # ---------------- pre-roll ring ----------------
    def _push_ring(self, blk: np.ndarray) -> None:
        self._ring.append(blk.copy())
        self._ring_n += len(blk)
        while self._ring and self._ring_n - len(self._ring[0]) >= self.preroll_n:
            self._ring_n -= len(self._ring.popleft())

    def _take_preroll(self) -> np.ndarray:
        if not self._ring:
            return np.zeros(0, np.float32)
        pre = np.concatenate(list(self._ring))
        if len(pre) > self.preroll_n:
            pre = pre[-self.preroll_n:]
        return pre.astype(np.float32)

    # ---------------- state machine ----------------
    def feed(self, mono: np.ndarray, gate: bool,
             now: Optional[float] = None) -> List[Dict]:
        now = self._clock() if now is None else now
        mono = np.ascontiguousarray(mono, dtype=np.float32)
        shaped = self.shaper.shape(mono, bool(gate))

        if self._state == "IDLE":
            self._push_ring(mono)
            if gate:
                self._open_segment(shaped, now)
            return []

        # REC
        self._seg.append(shaped)
        self._seg_n += len(shaped)
        if gate:
            self._closed_n = 0
        else:
            self._closed_n += len(shaped)
        if self._closed_n >= self.hang_n:
            return self._close_segment("signal_end", now)
        if self._seg_n >= self.max_seg_n:
            return self._close_segment("max_length", now)
        return []

    def _open_segment(self, current: np.ndarray, now: float) -> None:
        pre = self._take_preroll()
        self._seg = []
        if len(pre) > 0:
            fade = np.linspace(0.0, 1.0, len(pre), dtype=np.float32)
            self._seg.append((pre * fade).astype(np.float32))
        self._seg.append(current)
        self._seg_n = sum(len(b) for b in self._seg)
        self._closed_n = 0
        self._seg_start = now - (len(pre) / self.sr)
        self._state = "REC"

    def flush(self, now: Optional[float] = None) -> List[Dict]:
        """停止时收尾：若仍在录则落盘当前段。"""
        if self._state != "REC":
            return []
        return self._close_segment("flush", self._clock() if now is None else now)

    def _close_segment(self, reason: str, now: float) -> List[Dict]:
        audio = np.concatenate(self._seg) if self._seg else np.zeros(0, np.float32)
        self._seg = []
        self._seg_n = 0
        self._closed_n = 0
        self._state = "IDLE"
        self._ring.clear()
        self._ring_n = 0

        # 去掉结尾静音：相对段峰值 1% 的自适应阈值（残留包络噪声快速跌破），
        # 仅保留约 50ms 尾巴
        full_peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        cut_thr = max(1e-4, 0.01 * full_peak)
        active = np.flatnonzero(np.abs(audio) > cut_thr)
        if active.size == 0 or len(audio) < self.min_seg_n:
            return []  # 全静音 / 过短，丢弃，不产生垃圾文件
        end = min(len(audio), int(active[-1]) + int(0.05 * self.sr))
        audio = audio[:end]
        if len(audio) < self.min_seg_n:
            return []

        # 温和电平归一化（只在需要时；上限 max_gain，避免抬残噪）
        peak = float(np.max(np.abs(audio)))
        gain = self.target_peak / peak if peak > 0 else 1.0
        gain = min(gain, self.max_gain)
        audio = (audio * gain).astype(np.float32)

        ts = datetime.fromtimestamp(self._seg_start)
        stamp = ts.strftime("%Y%m%d_%H%M%S")
        fname = f"{stamp}_{self.mode}_{int(self.frequency_hz)}.wav"
        fpath = os.path.join(self.outdir, fname)
        from scipy.io import wavfile
        wavfile.write(fpath, int(self.sr), audio)

        entry = {
            "file": fname,
            "start_local": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_s": round(len(audio) / self.sr, 3),
            "mode": self.mode,
            "frequency_hz": self.frequency_hz,
            "peak": round(peak, 4),
            "gain_applied": round(gain, 2),
            "close_reason": reason,
        }
        self.segments.append(entry)
        with open(self.manifest_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return [{"type": "segment_saved", **entry}]


# =====================================================================
# 3) 信道解调 + 门控（原生 IQ -> 单声道音频 + 门控）
# =====================================================================
_MODE_CFG: Dict[str, Dict] = {
    "AM":  {"kind": "nb", "bw": 10000, "margin": 6.0, "demod": "am"},
    "NFM": {"kind": "nb", "bw": 12500, "margin": 8.0, "demod": "nfm"},
    "FM":  {"kind": "nb", "bw": 12500, "margin": 8.0, "demod": "nfm"},
    "USB": {"kind": "nb", "bw": 2800,  "margin": 6.0, "demod": "usb"},
    "LSB": {"kind": "nb", "bw": 2800,  "margin": 6.0, "demod": "lsb"},
    "CW":  {"kind": "nb", "bw": 500,   "margin": 5.0, "demod": "cw"},
    "WFM": {"kind": "wfm"},
}


class ChannelDemod:
    """把原生率 IQ 信道化、解调并给出门控（窄带 AutoSquelch / WFM NoiseSquelch）。"""

    def __init__(self, native_sr: float, mode: str,
                 audio_sr: float = 48000.0):
        from .dsp import VFO, WFMReceiver, am_demod, cw_demod, fm_demod, ssb_demod
        mode = mode.upper()
        if mode not in _MODE_CFG:
            raise ValueError(f"不支持的模式: {mode}")
        self.native_sr = float(native_sr)
        self.audio_sr = float(audio_sr)
        self.mode = mode
        self.cfg = _MODE_CFG[mode]
        self.kind = self.cfg["kind"]

        if self.kind == "nb":
            self.vfo = VFO(self.native_sr, self.audio_sr,
                           float(self.cfg["bw"]), 0.0)
            self.auto = AutoSquelch(
                margin_db=float(self.cfg["margin"]), hang_ms=200.0,
                sample_rate=self.audio_sr)
        else:
            self.wfm = WFMReceiver(self.native_sr, audio_sr=int(self.audio_sr))
            self.noise = NoiseSquelch(sample_rate=self.audio_sr)

        self._am = am_demod
        self._cw = cw_demod
        self._fm = fm_demod
        self._ssb = ssb_demod

    def reset(self) -> None:
        if self.kind == "nb":
            self.auto.reset()
        else:
            self.noise.reset()

    def _demod_nb(self, vo: np.ndarray) -> np.ndarray:
        d = self.cfg["demod"]
        if d == "am":
            return self._am(vo)
        if d == "nfm":
            return self._fm(vo, deviation=5000.0, sample_rate=self.audio_sr)
        if d == "usb":
            return self._ssb(vo, "USB", 1500.0, self.audio_sr)
        if d == "lsb":
            return self._ssb(vo, "LSB", 1500.0, self.audio_sr)
        if d == "cw":
            return self._cw(vo, 700.0, self.audio_sr)
        return np.zeros(len(vo), np.float32)

    def process(self, iq: np.ndarray):
        if self.kind == "nb":
            vo = self.vfo.process(iq)
            if vo is None or len(vo) < 16:
                return np.zeros(0, np.float32), False
            gate = bool(self.auto.update(rms_dbfs(vo), len(vo)))
            return np.ascontiguousarray(self._demod_nb(vo), np.float32), gate
        st = self.wfm.process(iq)
        if st is None or len(st) == 0:
            return np.zeros(0, np.float32), False
        mono = st.mean(axis=1) if getattr(st, "ndim", 1) == 2 else st
        mono = np.ascontiguousarray(mono, np.float32)
        gate = bool(self.noise.process_audio(mono))
        return mono, gate


# =====================================================================
# 4) 真实设备长守听会话
# =====================================================================
def _parse_freq(s: str) -> float:
    s = s.strip().lower()
    if s.endswith("m"):
        return float(s[:-1]) * 1e6
    if s.endswith("k"):
        return float(s[:-1]) * 1e3
    return float(s)


def _parse_duration(s: str) -> float:
    s = s.strip().lower()
    if s.endswith("h"):
        return float(s[:-1]) * 3600.0
    if s.endswith("m"):
        return float(s[:-1]) * 60.0
    return float(s)


def run_session(frequency_hz: float, mode: str, duration_s: float,
                outdir: str, sample_rate: float = 2_048_000.0,
                gain_db: Optional[float] = None,
                device_id: Optional[str] = None,
                block_n: int = 16384,
                clock: Callable[[], float] = time.time,
                acquire: Optional[Callable[[int], np.ndarray]] = None,
                verbose: bool = True) -> Dict:
    """
    连接真实 SDR（或注入 acquire 回调），长守听并门控录音。
    返回会话统计（段数、文件清单等）。
    """
    demod = ChannelDemod(sample_rate, mode)
    rec = GatedRecorder(demod.audio_sr, mode, outdir,
                        frequency_hz=frequency_hz, clock=clock)

    if acquire is None:
        from .agent import MBDSDRAgent
        agent = MBDSDRAgent()
        reg = agent.tool_registry
        kw = {"device_id": device_id} if device_id else {}
        reg.call("sdr_connect", kw)
        reg.call("sdr_set_sample_rate", {"sample_rate_hz": sample_rate})
        reg.call("sdr_set_frequency", {"frequency_hz": frequency_hz})
        if gain_db is not None:
            reg.call("sdr_set_gain", {"gain_db": gain_db})
        be = agent.sdr_manager.get_active()
        acquire = be.read_samples

    start = clock()
    deadline = start + duration_s
    last_report = start
    try:
        while clock() < deadline:
            iq = acquire(block_n)
            if iq is None or len(iq) == 0:
                continue
            mono, gate = demod.process(iq)
            if len(mono) == 0:
                continue
            for ev in rec.feed(mono, gate):
                if verbose:
                    print(f"[保存] {ev['file']}  {ev['duration_s']}s")
            now = clock()
            if verbose and now - last_report >= 30.0:
                last_report = now
                print(f"  守听 {int(now-start)}s，已存 {len(rec.segments)} 段")
    except KeyboardInterrupt:
        if verbose:
            print("收到中断，收尾中...")
    rec.flush()
    stats = {
        "frequency_hz": frequency_hz, "mode": mode,
        "dwell_s": round(clock() - start, 1),
        "segments": len(rec.segments),
        "outdir": outdir, "manifest": rec.manifest_path,
        "files": [e["file"] for e in rec.segments],
    }
    if verbose:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    return stats


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="mbdsdr_ai.gated_recorder",
        description="MBDSDR 信号触发式长守听录音机（有信号才录，只留干净信号）")
    p.add_argument("--freq", required=True, help="频率，如 119.450M / 439.850M")
    p.add_argument("--mode", required=True,
                   help="AM/NFM/FM/USB/LSB/CW/WFM")
    p.add_argument("--duration", default="1h", help="守听时长，如 4h / 30m / 3600")
    p.add_argument("--outdir", default=os.path.expanduser("~/mbdsdr_captures"),
                   help="WAV 与清单输出目录")
    p.add_argument("--sample-rate", type=float, default=2_048_000.0)
    p.add_argument("--gain", type=float, default=None, help="硬件增益 dB")
    p.add_argument("--device", default=None, help="设备 id（默认首个）")
    args = p.parse_args(argv)

    run_session(_parse_freq(args.freq), args.mode.upper(),
                _parse_duration(args.duration), args.outdir,
                sample_rate=args.sample_rate, gain_db=args.gain,
                device_id=args.device)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
