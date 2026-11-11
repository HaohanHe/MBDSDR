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
        mr.unregister(sentinel)
    assert mr.get(sentinel) is None


def test_mode_registry_state_shared_across_module_instances():
    """裸名与包名可以是两个模块对象，但注册表必须是同一份状态。

    状态唯一持有者是 `mode_registry_store`（只按裸名导入，两种上下文命中
    同一个 sys.modules 条目）。反证：把状态改回模块级字典 `_REGISTRY`，
    包名实例与裸名实例各持一份，本用例必红。
    """
    import desktop.mode_registry as pkg_mod

    sentinel = "ZZSHARED"
    desc = mr.DemodMode(
        mode=sentinel, display_name="共享状态测试", default_bandwidth_hz=3333.0,
        audio_cutoff_hz=1234.0, demod_func="", outputs_audio=False)
    try:
        # 包名实例写 → 裸名读方必须读到
        pkg_mod.register(desc)
        assert mr.get(sentinel) is not None, "经包名注册的模式，裸名读方读不到"
        assert mr.default_bandwidth(sentinel) == 3333.0

        # 反向：裸名实例注销 → 包名读方必须也看不到
        mr.unregister(sentinel)
        assert pkg_mod.get(sentinel) is None, "裸名注销后，包名读方仍能看到"

        # 裸名实例写 → 包名读方必须读到
        mr.register(desc)
        assert pkg_mod.get(sentinel) is not None, "经裸名注册的模式，包名读方读不到"
    finally:
        mr.unregister(sentinel)
    assert mr.get(sentinel) is None and pkg_mod.get(sentinel) is None


def test_mode_registry_dict_is_not_exposed():
    """读方只能拿函数，不能拿注册表字典（docs/redundancy-register.md §5.11.1）。"""
    for name in ("_REGISTRY", "_runtime"):
        assert not hasattr(mr, name), f"mode_registry 不应再暴露 {name}"
    assert callable(mr.register) and callable(mr.get) and callable(mr.list_modes)


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
