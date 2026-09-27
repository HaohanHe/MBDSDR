"""
主题 Token 对齐冒烟测试
======================

验证本次硬编码颜色替换后，各面板在 offscreen 平台下可正常实例化、
paintEvent 可执行（对 QWidget 调用 grab() 触发重绘），不崩溃。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_theme_tokens.py -q
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


# ----------------------------------------------------------------------
@pytest.mark.parametrize("module_name,class_name,kwargs", [
    ("rf_sky_view", "RFSkyViewPanel", {}),
    ("spectrum_widget", "SpectrumPanel", {}),
    ("constellation_panel", "ConstellationPanel", {}),
    ("sat_track_panel", "SatTrackPanel", {}),
    ("module_panel", "ModulePanel", {}),
    ("vfo_panel", "VfoPanel", {}),
    ("adsb_map_panel", "AdsbMapPanel", {}),
    ("control_panel", "ControlPanel", {}),
    ("status_panel", "StatusPanel", {}),
    ("doppler_panel", "DopplerPanel", {}),
    ("ai_panel", "AIPanel", {}),
    ("adsb_panel", "AdsbPanel", {}),
    ("satellite_image_panel", "SatelliteImagePanel", {}),
])
def test_panel_instantiates_and_paints(module_name, class_name, kwargs):
    """每个修改过的面板类可实例化并触发重绘（grab），不崩。"""
    mod = __import__(module_name)
    cls = getattr(mod, class_name)
    widget = cls(**kwargs)
    widget.resize(400, 300)
    widget.show()
    # grab() 会同步触发一次 paintEvent；offscreen 平台下不抛异常即通过
    pix = widget.grab()
    assert pix is not None
    assert not pix.isNull()


def test_tokens_singleton_consistent():
    """tokens 单例可导入，深色关键色存在。"""
    from tokens import tokens
    t = tokens()
    for key in ("bg_dark", "card_1", "card_2", "card_3", "accent",
                "success", "danger", "gray_300", "gray_400", "text_primary"):
        assert key in t.COLORS, f"missing token {key}"
    # 深色主题色值校验
    assert t.COLORS["bg_dark"] == "#000000"
    assert t.COLORS["accent"] == "#919cac"
    # text() 返回带 alpha 的 rgba 字符串
    sec = t.text("secondary")
    assert sec.startswith("rgba(")
