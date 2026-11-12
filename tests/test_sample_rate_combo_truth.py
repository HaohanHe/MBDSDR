# SPDX-License-Identifier: MIT
"""采样率下拉的单一真值源：文案 / 未知项 / 模块面板不再持有静态档位表。

覆盖：
  1. 主窗口 `fill_sample_rate_combo`：每一项文案 == `format_sample_rate(itemData)`；
  2. 「未知」项 `data is None` 且**不夹带任何 MS/s 数字**（43ffe3f1 起：写
     2.4 MS/s 会被读成设备能力），应用侧兜底由 `connect_sample_rate_hz`
     唯一决定；
  3. 模块面板未连接态：只一项显式未知说明、置灰，不静默列全表（旧静态 5 档）；
  4. 模块面板已连接且能力受限态：档位 = 后端离散表 ∩ 设备量程。

设备样例必须显式标 `capability_status`（16042e4b 起：量程没标来源一律按
未知处理，展示层不替生产者补来源）。缺这一步，产品会诚实地只给「档位
未知」——那不是缺陷，是本用例的样例不完整。

反证（本地手工复现）：
  * 把任一文案改回 "2.048 MHz" → 用例 1 红；
  * 把未知项文案改回 "未知 · 2.400 MS/s"（夹带数字）→ 用例 2 红；
  * 把 module_panel 的静态档位表加回来 → 用例 3 红（未连接也列出 5 档）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from constants import (  # noqa: E402
    DEFAULT_SAMPLE_RATE_HZ, connect_sample_rate_hz, format_sample_rate)
from main_window import fill_sample_rate_combo  # noqa: E402
from module_panel import ModulePanel  # noqa: E402

#: RTL-SDR 类设备：离散档位表 ∩ [1.0, 2.56] MHz → 8 档
#: 量程必须带来源（declared/probed），否则产品按「来源未标注=未知」处理。
_RTL_DEVICE = {
    "label": "RTL-SDR",
    "driver": "rtlsdr",
    "sample_rate_range": (1_000_000, 2_560_000),
    "capability_status": {"sample_rate_range": "declared"},
}


def test_every_combo_text_is_formatted_from_item_data():
    """显示文案必须由 itemData 经 format_sample_rate 生成，不许各写一份字面量。"""
    combo = QComboBox()
    fill_sample_rate_combo(combo, _RTL_DEVICE)
    assert combo.count() == 8, [combo.itemText(i) for i in range(combo.count())]
    for index in range(combo.count()):
        data = combo.itemData(index)
        assert data is not None
        assert combo.itemText(index) == format_sample_rate(data), (
            f"第 {index} 项文案 {combo.itemText(index)!r} 与 "
            f"format_sample_rate({data}) 不一致")


def test_unknown_item_is_none_and_promise_matches_the_fallback():
    """未知项 data=None、文案不夹带 MS/s；应用侧兜底由唯一入口决定。"""
    combo = QComboBox()
    fill_sample_rate_combo(combo, None)
    assert combo.count() == 1, "量程未知时不得列出任何档位"
    assert combo.itemData(0) is None, "未知项必须是 data=None（应用侧才走兜底）"
    text = combo.itemText(0)
    assert "未知" in text
    # 43ffe3f1：未知态不许出现 MS/s 数字——那会被读成「设备能跑这个速率」。
    assert "MS/s" not in text and "Hz" not in text, \
        f"未知项文案夹带了采样率数字，会被读成设备能力: {text!r}"
    assert float(connect_sample_rate_hz(combo.currentData())) \
        == float(DEFAULT_SAMPLE_RATE_HZ)


def test_module_panel_unconnected_lists_no_fake_tiers():
    """未连接/设备未选定：只一项显式未知说明、置灰，不静默列全表。"""
    panel = ModulePanel()
    assert panel.sr_combo.count() == 1, [
        panel.sr_combo.itemText(i) for i in range(panel.sr_combo.count())]
    assert panel.sr_combo.itemData(0) is None
    assert "未知" in panel.sr_combo.itemText(0)
    assert not panel.sr_combo.isEnabled(), "未知档位还能点 = 选了不生效的假控件"
    assert panel.selected_sample_rate_hz() == float(DEFAULT_SAMPLE_RATE_HZ)


def test_module_panel_device_capability_limits_tiers():
    """已选设备且能力受限：档位 = 后端离散表 ∩ 量程，越界档不得出现。"""
    panel = ModulePanel()
    panel._apply_devices([dict(_RTL_DEVICE)])
    assert panel.sr_combo.isEnabled(), "设备报告了量程，档位应可选"
    assert panel.sr_combo.count() == 8
    for index in range(panel.sr_combo.count()):
        hz = float(panel.sr_combo.itemData(index))
        assert 1_000_000 <= hz <= 2_560_000
    texts = [panel.sr_combo.itemText(i) for i in range(panel.sr_combo.count())]
    assert format_sample_rate(250_000) not in texts, "250 kHz 低于设备量程"
    assert format_sample_rate(3_200_000) not in texts, "3.2 MS/s 超出设备量程"
    assert format_sample_rate(DEFAULT_SAMPLE_RATE_HZ) in texts
    assert float(panel.sr_combo.currentData()) == float(DEFAULT_SAMPLE_RATE_HZ)


def test_preferred_hz_selects_perf_default_tier():
    """通用连接下拉传 preferred_hz（性能档省电）→ 默认选中 eco 档，非设备基准 2.4M。

    用户「电脑没有那么强大…得让路」：工具栏「连接」走 _connect_dialog，默认
    选中档必须随性能档（eco 1.024 MS/s），否则 GNSS 被 2.4 MS/s 挤死原地不动。
    反证：不传 preferred_hz 的调用方（模块面板宽带解码）仍按设备基准 2.4M。
    """
    from perf_profile import sample_rate_for
    eco = float(sample_rate_for("eco"))
    combo = QComboBox()
    fill_sample_rate_combo(combo, _RTL_DEVICE, preferred_hz=eco)
    assert float(combo.currentData()) == eco, (
        f"传性能档默认率应选中 eco {eco:.0f}，实际 {combo.currentData()}")
    # eco 档必须在设备能力档位里（否则「选了又跳回去」）
    assert eco in [float(combo.itemData(i)) for i in range(combo.count())]
    # 反向：不传 preferred_hz 仍对齐设备基准（模块面板/旧调用方行为不变）
    combo2 = QComboBox()
    fill_sample_rate_combo(combo2, _RTL_DEVICE)
    assert float(combo2.currentData()) == float(DEFAULT_SAMPLE_RATE_HZ)
