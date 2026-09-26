"""
mbdsdr_ai.web — Web 流式接口包
================================

移植自 OpenWebRX（repos/openwebrx）：
  - 多客户端共享一个 SDR 后端（owrx/connection.py）
  - FFT/音频 WebSocket 扇出（owrx/fft.py, owrx/websocket.py）

类：
  - WebServer          : HTTP + WebSocket 服务器（GET /, /api/status, /api/spectrum,
                         /ws/spectrum, /ws/audio）
  - SpectrumStreamer   : FFT→dB→峰值保留降采样→uint8 量化→广播
  - AudioStreamer      : int16 PCM 块→广播
  - decode_spectrum_frame: 解二进制频谱帧

红线：无后端时所有端点显式 {"connected": false}，绝不造假数据。
"""

from .streamer import (
    AudioStreamer,
    SpectrumStreamer,
    decode_spectrum_frame,
    SPECTRUM_MARKER,
    AUDIO_MARKER,
)
from .server import WebServer

__all__ = [
    "WebServer",
    "SpectrumStreamer",
    "AudioStreamer",
    "decode_spectrum_frame",
    "SPECTRUM_MARKER",
    "AUDIO_MARKER",
]
