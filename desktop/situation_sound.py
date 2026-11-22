# SPDX-License-Identifier: MIT
"""情境音效：认出目标时按类别播一声短音（PRD v3 / FR-11）。

口径：
  - **自制素材**：不给仓库塞二进制音频，运行时用正弦 + 淡入淡出合成
    三个短 WAV（恒星 / 行星 / 深空），缓存到系统临时目录。
  - **默认低音量**：默认开启、音量 0.2；有静音开关（调用方提供按钮）。
  - **诚实降级**：没有音频输出设备、QtMultimedia 不可用、写盘失败时
    只记 `last_error` 并返回 False，绝不抛异常打断星图。
  - 只负责「播不播、播哪个」；类别到音的映射由 `category_for_kind()` 单点定义。
"""
from __future__ import annotations

import math
import os
import struct
import tempfile
import wave
from typing import Callable, Dict, List, Optional, Tuple

#: 合成采样率：短提示音不需要 44.1 kHz，22.05 kHz 足够且文件更小。
SAMPLE_RATE = 22050

#: 类别 → 音符序列 (频率 Hz, 时长 s, 相对振幅)。
#: 全部为自制正弦音，不含任何第三方音频素材。
CATEGORY_TONES: Dict[str, List[Tuple[float, float, float]]] = {
    # 恒星：一声清亮的高音，短促不刺耳
    "star": [(1046.5, 0.09, 0.55)],
    # 行星：两级上行，和恒星区分开
    "planet": [(659.3, 0.07, 0.50), (987.8, 0.09, 0.55)],
    # 深空：偏低、衰减更长，像"很远"
    "dso": [(440.0, 0.18, 0.55)],
}

#: 识别结果的 kind → 音效类别；未收录的类别静默（不硬凑一个音）。
_KIND_CATEGORY = {
    "star": "star",
    "planet": "planet",
    "moon": "planet",
    "dso": "dso",
    "deep_sky": "dso",
}

DEFAULT_VOLUME = 0.2


def category_for_kind(kind: object) -> Optional[str]:
    """把识别结果 kind 映射成音效类别；未知返回 None（不播）。"""
    if not isinstance(kind, str):
        return None
    return _KIND_CATEGORY.get(kind.strip().lower())


def render_tone_pcm(notes: List[Tuple[float, float, float]]) -> bytes:
    """把音符序列合成为 16-bit 单声道 PCM（带 5 ms 淡入淡出防爆音）。"""
    frames = bytearray()
    for freq, duration, amp in notes:
        n = max(1, int(round(duration * SAMPLE_RATE)))
        fade = max(1, int(round(0.005 * SAMPLE_RATE)))
        for i in range(n):
            env = 1.0
            if i < fade:
                env = i / fade
            elif i > n - fade:
                env = max(0.0, (n - i) / fade)
            value = amp * env * math.sin(2.0 * math.pi * freq * i / SAMPLE_RATE)
            frames += struct.pack("<h", int(max(-1.0, min(1.0, value)) * 32767))
    return bytes(frames)


def wav_bytes(notes: List[Tuple[float, float, float]]) -> bytes:
    """返回完整 WAV 字节；测试直接校验 RIFF 头与是否非静音。"""
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(render_tone_pcm(notes))
    return buf.getvalue()


class SituationSound:
    """短音播放器：默认开启、低音量；可注入假播放器做单测。"""

    def __init__(
        self,
        enabled: bool = True,
        volume: float = DEFAULT_VOLUME,
        cache_dir: Optional[str] = None,
        player_factory: Optional[Callable[[], object]] = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._volume = max(0.0, min(1.0, float(volume)))
        self._cache_dir = cache_dir or os.path.join(
            tempfile.gettempdir(), "mbdsdr_situation_sound")
        self._player_factory = player_factory
        self._players: Dict[str, object] = {}
        self._paths: Dict[str, str] = {}
        self.last_error: str = ""

    # ---- 开关与音量 ----
    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    @property
    def volume(self) -> float:
        return self._volume

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, float(volume)))
        for player in self._players.values():
            try:
                player.setVolume(self._volume)  # type: ignore[attr-defined]
            except Exception:
                pass

    # ---- 播放 ----
    def play(self, kind: object) -> bool:
        """按识别类别播一声；静音/未知类别/无音频设备都返回 False。"""
        if not self._enabled:
            return False
        category = category_for_kind(kind)
        if category is None:
            return False
        try:
            path = self._ensure_wav(category)
            player = self._ensure_player(category, path)
            if player is None:
                return False
            player.setVolume(self._volume)  # type: ignore[attr-defined]
            player.play()  # type: ignore[attr-defined]
            self.last_error = ""
            return True
        except Exception as exc:  # noqa: BLE001 - 音频失败不得打断星图
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def _ensure_wav(self, category: str) -> str:
        path = self._paths.get(category)
        if path and os.path.getsize(path) > 44:
            return path
        os.makedirs(self._cache_dir, exist_ok=True)
        path = os.path.join(self._cache_dir, f"{category}_v1.wav")
        data = wav_bytes(CATEGORY_TONES[category])
        if not (os.path.exists(path) and os.path.getsize(path) == len(data)):
            tmp = path + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        self._paths[category] = path
        return path

    def _ensure_player(self, category: str, path: str):
        player = self._players.get(category)
        if player is not None:
            return player
        if self._player_factory is not None:
            player = self._player_factory()
            player.setSource(path)  # type: ignore[attr-defined]
        else:
            try:
                from PySide6.QtCore import QUrl
                from PySide6.QtMultimedia import QMediaDevices, QSoundEffect
            except Exception as exc:  # noqa: BLE001 - 缺 QtMultimedia 即静默降级
                self.last_error = f"{type(exc).__name__}: {exc}"
                return None
            if not QMediaDevices.audioOutputs():
                self.last_error = "no_audio_device"
                return None
            player = QSoundEffect()
            player.setSource(QUrl.fromLocalFile(path))
        self._players[category] = player
        return player
