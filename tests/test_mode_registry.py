#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
test_mode_registry.py — 解调模式注册表 + 迁移后 UI/解调调度 offscreen 测试
=========================================================================

守护本轮模块化改造：
  1. 注册表内置 9 个模式齐全；
  2. register() 可动态追加新模式；
  3. default_bandwidth 与旧 MODE_VFO_BANDWIDTH 逐模式一致（数学/参数不变）；
  4. demodulate() 对每个模拟模式喂随机 IQ 不崩溃、返回非空；
  5. to_bandwidth_dict() 导出全部模式；
  6. ControlPanel 的 mode_combo 项来自注册表；
  7. MainWindow._demod_at_48k 对各模式返回非空数组（注册表分发等价旧 if-else）。

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_mode_registry.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import mode_registry as mr  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# 1. 内置模式齐全
# ─────────────────────────────────────────────────────────────────────
EXPECTED_BUILTIN = ["WFM", "NFM", "FM", "AM", "USB", "LSB", "CW", "RAW", "DIG"]


def test_mode_registry_builtin_modes():
    names = mr.mode_names()
    for m in EXPECTED_BUILTIN:
        assert m in names, f"内置模式 {m} 缺失；现有 {names}"
    assert len(names) >= len(EXPECTED_BUILTIN)


def test_mode_registry_descriptors_have_required_fields():
    for m in EXPECTED_BUILTIN:
        d = mr.get(m)
        assert d is not None, f"{m} 描述符为 None"
        assert d.display_name, f"{m} 缺 display_name"
        assert d.default_bandwidth_hz > 0, f"{m} 带宽非正"


# ─────────────────────────────────────────────────────────────────────
# 2. 动态注册新模式
# ─────────────────────────────────────────────────────────────────────
def test_mode_registry_register_new():
    sentinel = "ZZTEST"
    before = mr.get(sentinel)
    assert before is None, f"测试前 {sentinel} 不应存在"
    desc = mr.DemodMode(
        mode=sentinel,
        display_name="测试模式",
        default_bandwidth_hz=3333.0,
        audio_cutoff_hz=1234.0,
        demod_func="",           # 无解调函数
        outputs_audio=False,
    )
    try:
        mr.register(desc)
        got = mr.get(sentinel)
        assert got is not None
        assert got.display_name == "测试模式"
        assert mr.default_bandwidth(sentinel) == 3333.0
        assert mr.audio_cutoff(sentinel) == 1234.0
        assert sentinel in mr.mode_names()
    finally:
        # 清理，避免污染其它测试
        mr._REGISTRY.pop(sentinel, None)
    assert mr.get(sentinel) is None


# ─────────────────────────────────────────────────────────────────────
# 3. 默认带宽与旧 MODE_VFO_BANDWIDTH 一致
# ─────────────────────────────────────────────────────────────────────
# 旧字典的精确值（迁移前 control_panel.MODE_VFO_BANDWIDTH）
LEGACY_BANDWIDTH = {
    "WFM": 180_000.0,
    "NFM": 12_500.0,
    "FM": 12_500.0,
    "AM": 6_000.0,
    "USB": 2_400.0,
    "LSB": 2_400.0,
    "CW": 500.0,
}


def test_mode_registry_default_bandwidth():
    for m, expected in LEGACY_BANDWIDTH.items():
        assert mr.default_bandwidth(m) == pytest.approx(expected), \
            f"{m} 默认带宽 {mr.default_bandwidth(m)} != 旧值 {expected}"
    # 未知模式回退 12.5kHz
    assert mr.default_bandwidth("NOPE") == pytest.approx(12_500.0)


def test_control_panel_alias_matches_registry():
    """control_panel.MODE_VFO_BANDWIDTH 向后兼容别名 == 注册表导出。"""
    from control_panel import MODE_VFO_BANDWIDTH
    for m, expected in LEGACY_BANDWIDTH.items():
        assert MODE_VFO_BANDWIDTH[m] == pytest.approx(expected), \
            f"别名 {m}={MODE_VFO_BANDWIDTH[m]} != {expected}"


# ─────────────────────────────────────────────────────────────────────
# 4. demodulate() 对每个模拟模式喂随机 IQ 不崩溃
# ─────────────────────────────────────────────────────────────────────
def test_mode_registry_demodulate():
    rng = np.random.default_rng(123)
    iq = (rng.standard_normal(32768) + 1j * rng.standard_normal(32768))
    iq = iq.astype(np.complex64) * 0.1
    # 有解调函数且输出音频的窄带模式（WFM 走专用路径，不在此测）
    for mode in ["NFM", "FM", "AM", "USB", "LSB", "CW"]:
        out = mr.demodulate(mode, iq, sample_rate=48000)
        assert out is not None, f"{mode} demodulate 返回 None"
        assert len(out) > 0, f"{mode} 解调结果为空"
    # RAW/DIG 无解调函数 → 返回 None（不崩）
    assert mr.demodulate("RAW", iq, sample_rate=48000) is None
    assert mr.demodulate("DIG", iq, sample_rate=48000) is None
    # 未知模式 → None
    assert mr.demodulate("NOPE", iq, sample_rate=48000) is None


# ─────────────────────────────────────────────────────────────────────
# 5. to_bandwidth_dict() 导出全部模式
# ─────────────────────────────────────────────────────────────────────
def test_mode_registry_to_bandwidth_dict():
    d = mr.to_bandwidth_dict()
    for m in EXPECTED_BUILTIN:
        assert m in d, f"导出字典缺 {m}"
        assert d[m] == mr.default_bandwidth(m)


# ─────────────────────────────────────────────────────────────────────
# 6. ControlPanel mode_combo 项来自注册表
# ─────────────────────────────────────────────────────────────────────
def test_control_panel_mode_combo_from_registry():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from control_panel import ControlPanel
    cp = ControlPanel()
    items = [cp.mode_combo.itemText(i) for i in range(cp.mode_combo.count())]
    # 下拉项必须是注册表中已注册的模式名（不是硬编码字面量逃逸）
    for it in items:
        assert mr.get(it) is not None, f"combo 项 {it} 不在注册表"
    # 保持原 UX 顺序
    assert items == ["FM", "WFM", "NFM", "AM", "USB", "LSB", "CW"]


# ─────────────────────────────────────────────────────────────────────
# 7. MainWindow._demod_at_48k 注册表分发返回非空
# ─────────────────────────────────────────────────────────────────────
def test_main_window_demod_dispatch():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from main_window import MainWindow
    from mbdsdr_ai import dsp as _dsp
    rng = np.random.default_rng(7)
    vfo = (rng.standard_normal(32768) + 1j * rng.standard_normal(32768))
    vfo = vfo.astype(np.complex64) * 0.1
    # 静态方法，无需构造整个 MainWindow
    for mode in ["NFM", "FM", "AM", "USB", "LSB", "CW"]:
        out = MainWindow._demod_at_48k(_dsp, mode, vfo)
        assert out is not None and len(out) > 0, f"_demod_at_48k({mode}) 为空"
    # 未知模式落到兜底 FM 75k，也应非空
    out = MainWindow._demod_at_48k(_dsp, "NOPE", vfo)
    assert out is not None and len(out) > 0
