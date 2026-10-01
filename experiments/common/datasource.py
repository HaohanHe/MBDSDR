# SPDX-License-Identifier: MIT
"""
统一数据源：合成信道 / SigMF 录制回放 / WAV 回放（诚实空态）
=================================================================

口径（B2 审计 §2.2）：
- :class:`SyntheticChannel` —— **合成**数据。脚本内生成信号+加噪，只能标
  ``data_origin="synthetic"``（仿真）。绝不允许被冒称 OTA/录制。
- :class:`SigMFReplay` —— 包 :class:`mbdsdr_ai.playback.IQPlayback`，回放
  ``recorder.cpp`` 录下的标准 SigMF（.sigmf-data + .sigmf-meta）。中心频率/
  采样率一律从 meta 读，不硬编码。这是**真实录制**，标 ``recorded``。
- :class:`WavReplay` —— 包 :func:`mbdsdr_ai.orbit_determination.load_iq_file`
  的 .wav 分支（双声道 int16 PCM，左 I 右 Q）。

红线（与 playback.py / orbit_determination.py 既有诚实拒绝风格一致）：
- 扫描约定目录找不到任何 ``.sigmf-data`` 时，:func:`find_recordings` 返回空列表，
  OTA/录制 runner 据此打印"未提供录制数据，OTA/录制结果为空态"并跳过，
  **绝不**用合成数据补 OTA 格。
- 录制文件 meta 缺采样率时如实上报 sample_rate=0，不编造。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np


# ---------------------------------------------------------------------------
# 合成信道（仅 synthetic 口径）
# ---------------------------------------------------------------------------
class SyntheticChannel:
    """合成复基带信道：给干净信号加复高斯白噪声到指定带内 SNR(dB)。

    与 mbdsdr_ai.adsb.add_awgn / amr.synthesize_modulation_iq 同一功率约定：
    信号归一化单位功率，噪声功率 = sig_p · 10^(-snr/10)。
    """

    origin = "synthetic"

    @staticmethod
    def add_awgn(clean: np.ndarray, snr_db: float,
                 rng: np.random.Generator) -> np.ndarray:
        """给 clean 复信号加 AWGN 到带内 snr_db。snr_db=+inf 表示不加噪。"""
        x = np.asarray(clean, dtype=np.complex128)
        if not np.isfinite(snr_db):
            return x
        sig_p = float(np.mean(np.abs(x) ** 2))
        if sig_p <= 0:
            return x
        noise_p = sig_p * 10.0 ** (-snr_db / 10.0)
        w = np.sqrt(noise_p / 2.0) * (rng.standard_normal(x.size)
                                       + 1j * rng.standard_normal(x.size))
        return x + w.astype(np.complex128)


# ---------------------------------------------------------------------------
# 录制扫描（诚实空态）
# ---------------------------------------------------------------------------
def find_recordings(recordings_dir: str) -> List[str]:
    """扫描目录下所有 .sigmf-data 文件路径（递归一层）。

    目录不存在 / 无任何 .sigmf-data 时返回空列表 —— 这是合法的"空态"，
    调用方应据此跳过 OTA/录制段，而不是报错、更不是补合成数据。
    """
    found: List[str] = []
    if not recordings_dir or not os.path.isdir(recordings_dir):
        return found
    for name in sorted(os.listdir(recordings_dir)):
        p = os.path.join(recordings_dir, name)
        if os.path.isfile(p) and name.endswith(".sigmf-data"):
            found.append(p)
    return found


@dataclass
class RecordingHandle:
    """一段录制的只读句柄（元数据 + 惰性打开的回放器）。"""
    data_path: str
    meta_path: str
    sample_rate_hz: float = 0.0
    center_freq_hz: float = 0.0
    datetime: str = ""
    n_samples: int = 0
    available: bool = False
    note: str = ""


# ---------------------------------------------------------------------------
# SigMF 录制回放（包 playback.IQPlayback）
# ---------------------------------------------------------------------------
class SigMFReplay:
    """SigMF 录制回放源。薄包 mbdsdr_ai.playback.IQPlayback。"""

    origin = "recorded"

    def __init__(self, data_path: str):
        # 延迟导入：mbdsdr_ai 在仓库根，import 由入口脚本加 sys.path
        from mbdsdr_ai import playback as _pb
        self._pb_mod = _pb
        self.data_path = data_path
        self._pb = _pb.IQPlayback(loop=False)

    def open(self) -> RecordingHandle:
        ok = self._pb.open(self.data_path)
        meta = self._pb_mod.parse_sigmf_meta(
            self.data_path[: -len(".sigmf-data")] + ".sigmf-meta"
            if self.data_path.endswith(".sigmf-data") else self.data_path)
        return RecordingHandle(
            data_path=self.data_path,
            meta_path=self._pb.meta_path or "",
            sample_rate_hz=self._pb.sample_rate,
            center_freq_hz=self._pb.center_freq_hz,
            datetime=self._pb.datetime,
            n_samples=self._pb.n_samples,
            available=bool(ok),
            note=("已打开真实录制" if ok else "打开失败"),
        )

    def read(self, n: int) -> np.ndarray:
        return self._pb.read_samples(n)

    def close(self) -> None:
        self._pb.close()


# ---------------------------------------------------------------------------
# WAV 回放（包 orbit_determination.load_iq_file）
# ---------------------------------------------------------------------------
class WavReplay:
    """WAV 双声道 int16 (左 I 右 Q) 回放源。"""

    origin = "recorded"

    def __init__(self, wav_path: str):
        from mbdsdr_ai import orbit_determination as _od
        self._od = _od
        self.wav_path = wav_path

    def load(self):
        """返回 (complex128 iq, fs_hz)。文件不存在由底层抛 FileNotFoundError。"""
        return self._od.load_iq_file(self.wav_path)


# ---------------------------------------------------------------------------
# 顶层：按 --recording 选择源
# ---------------------------------------------------------------------------
def resolve_source(recording: Optional[str], recordings_dir: Optional[str]):
    """根据 CLI 参数决定数据源。

    返回 (source_kind, object_or_None, message)：
      - recording 给了具体文件 -> ('recorded', SigMFReplay/WavReplay, ...)
      - 只给 recordings_dir 且扫到文件 -> ('recorded', SigMFReplay(首个), ...)
      - 否则 -> ('none', None, "未提供录制数据，OTA/录制结果为空态")

    绝不返回合成对象冒充录制。
    """
    if recording:
        if recording.endswith(".wav"):
            return ("recorded", WavReplay(recording), f"回放 WAV: {recording}")
        return ("recorded", SigMFReplay(recording), f"回放 SigMF: {recording}")

    if recordings_dir:
        found = find_recordings(recordings_dir)
        if found:
            return ("recorded", SigMFReplay(found[0]),
                    f"回放目录首段录制: {found[0]}（共 {len(found)} 段）")

    return ("none", None,
            "未提供录制数据，OTA/录制结果为空态（不合成冒充 OTA）")
