# SPDX-License-Identifier: MIT
"""WAV 读取（叶子模块）。

`_read_wav_audio` 原本长在 ``decoders`` 里，但它是通用音频原语：decode_router
等模块只用它读 wav，却因此依赖整个卫星解码模块。本模块只依赖 numpy 与标准库
``wave``，不 import 任何 ``mbdsdr_ai`` 模块；``decoders`` 原处保留同名别名
（``_read_wav_audio``），既有调用方不变。
"""
from typing import Optional, Tuple

import numpy as np


def read_wav_audio(input_path: str) -> Tuple[Optional[np.ndarray], float, Optional[str]]:
    """读 .wav 为单声道浮点音频。返回 (audio, sample_rate, error)。"""
    try:
        import wave
        with wave.open(input_path, "rb") as wf:
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
            sw = wf.getsampwidth()
            ch = wf.getnchannels()
            sr = float(wf.getframerate())
        if sw == 2:
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32767.0
        elif sw == 1:
            audio = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
        elif sw == 4:
            audio = np.frombuffer(raw, dtype=np.int32).astype(np.float64) / 2147483647.0
        else:
            return None, 0.0, f"不支持的 WAV 采样宽度: {sw} 字节"
        if ch > 1:
            audio = audio.reshape(-1, ch).mean(axis=1)
    except Exception as e:
        return None, 0.0, f"读取 WAV 失败: {e}"
    if audio.size == 0:
        return None, sr, "WAV 中没有音频样本"
    return audio, sr, None
