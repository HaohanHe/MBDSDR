# SPDX-License-Identifier: MIT
"""
mbdsdr_ai.analysis — 信号分析与协议解析包
==========================================

依据公开信号分析与协议解析方法独立实现，并加 AI 增强；
URH (Universal Radio Hacker) 与 inspectrum 仅作技术参考与致谢，本仓未包含其源代码：

- :class:`ModulationClassifier` — 基于特征的自动调制识别（参考 URH
  自动调制识别的特征工程思路）。
- :class:`GardnerClockRecovery` / :class:`EarlyLateGate` — 符号定时恢复
  （经典闭环 Gardner / 早迟门算法，教材方法）。
- :class:`ProtocolParser` — 同步字 / 长度字段 / CRC-16/CCITT 自动解析
  （通用同步与 CRC 校验思路）。
- :class:`Spectrogram` — STFT 时频分析 + 选框测量（经典 STFT / 选框测量方法）。

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
