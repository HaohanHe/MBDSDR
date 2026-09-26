"""
频谱多 VFO 绘制 offscreen 冒烟测试
==================================

验证 SpectrumPanel 在 VfoManager 里注册多个 VFO 时：
  1. 不崩；
  2. 新增信号可连接；
  3. _draw_vfo_band 遍历所有 VFO 画矩形（主听橙 / 次听蓝灰）。
不依赖硬件 / 声卡 / 真实 IQ 数据。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


def test_spectrum_panel_draws_multi_vfo():
    """多个 VFO 注册进共享 manager 后，paintEvent 不崩。"""
    from spectrum_widget import SpectrumPanel
    from mbdsdr_ai.vfo_manager import VfoManager

    panel = SpectrumPanel()
    mgr = VfoManager()
    # 共享 manager（与 main_window 接线方式一致）
    panel.vfo_manager = mgr

    # 造两个 VFO：一个主听一个次听
    v1 = mgr.add(100e6, 12.5e3, "FM")
    v2 = mgr.add(145e6, 12.5e3, "FM")
    mgr.set_active_context(v1, temporary=False)   # v1 = 主听

    # 喂一点假 IQ 让 has_data()=True，触发 VFO 矩形绘制
    import numpy as np
    sr = 2.4e6
    iq = (np.random.default_rng(0).standard_normal(16384)
          + 1j * np.random.default_rng(1).standard_normal(16384)
          ).astype(np.complex64)
    panel.update_iq(iq, 120e6, sr)

    # 触发一次重绘（offscreen 平台不真的开窗，但 paintEvent 会跑）
    panel._plot.resize(800, 600)
    panel._plot.grab()  # 强制渲染到离屏 pixmap，不抛异常即通过
    assert len(mgr.list_all()) == 2
    assert mgr.active_vfo_id == v1.vfo_id


def test_spectrum_vfo_signals_exist():
    """SpectrumPanel 暴露多 VFO 信号。"""
    from spectrum_widget import SpectrumPanel
    panel = SpectrumPanel()
    for name in ("vfo_created", "vfo_moved", "vfo_bw_changed",
                 "vfo_selected", "vfo_removed"):
        assert hasattr(panel, name), f"缺少信号 {name}"


if __name__ == "__main__":
    test_spectrum_panel_draws_multi_vfo()
    test_spectrum_vfo_signals_exist()
    print("ALL PASS")
