"""
mbdsdr_ai.analysis — 信号分析与协议解析包
==========================================

移植自 URH (Universal Radio Hacker) 与 inspectrum，并加 AI 增强：

- :class:`ModulationClassifier` — 基于特征的自动调制识别（对照 URH
  ``ainterpretation/AutoInterpretation.py::detect_modulation``）。
- :class:`GardnerClockRecovery` / :class:`EarlyLateGate` — 符号定时恢复
  （URH 走开环平台长度路线，这里补上经典闭环算法）。
- :class:`ProtocolParser` — 同步字 / 长度字段 / CRC-16/CCITT 自动解析
  （对照 URH ``awre/engines/LengthEngine.py`` 与 ``util/GenericCRC.py``）。
- :class:`Spectrogram` — STFT 时频分析 + 选框测量（对照 inspectrum
  ``spectrogramplot.cpp::getLine`` 与 ``tuner.cpp``）。

设计原则：
- 全部纯 numpy/scipy，不依赖 PyQt / Cython。
- 支持流式输入（``feed`` 方法），可实时挂在频谱旁。
- 合成信号仅用于单测，不接 UI 当真实读数。
"""

from .modulation_classifier import ModulationClassifier, ModulationResult
from .clock_recovery import GardnerClockRecovery, EarlyLateGate, Symbol
from .protocol_parser import (
    ProtocolField,
    ProtocolMessage,
    ProtocolParser,
    FieldType,
)
from .spectrogram import Spectrogram, Selection

__all__ = [
    "ModulationClassifier",
    "ModulationResult",
    "GardnerClockRecovery",
    "EarlyLateGate",
    "Symbol",
    "ProtocolField",
    "ProtocolMessage",
    "ProtocolParser",
    "FieldType",
    "Spectrogram",
    "Selection",
]
