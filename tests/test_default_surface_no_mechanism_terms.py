# SPDX-License-Identifier: MIT
"""默认界面不露底层机制（settle Task 6.2 / FR-05）。

主界面默认态（专业面板未展开）不得出现「授时状态 / 能力协商中 / 已同步」
这类底层机制字样；它们只应留在日志与展开后的专业面板里。
"""
import json
import logging
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton, QApplication, QGroupBox, QLabel, QTabWidget,
)

log = logging.getLogger(__name__)
app = QApplication.instance() or QApplication(sys.argv)

FORBIDDEN = ("授时状态", "能力协商中", "已同步")


@pytest.fixture(scope="module")
def window(isolated_profile):
    from main_window import MainWindow

    qs = QSettings("MBDSDR", "Desktop")
    keys = ("window_geometry", "three_column_sizes",
            "three_column_layout_version")
    backup = {k: qs.value(k) for k in keys}
    for k in keys:
        qs.remove(k)
    qs.sync()

    gui_cfg = os.path.expanduser("~/.mbdsdr/gui_config.json")
    cfg_backup = None
    if os.path.exists(gui_cfg):
        with open(gui_cfg, "r", encoding="utf-8") as fh:
            cfg_backup = fh.read()
        try:
            data = json.loads(cfg_backup)
            data.pop("window", None)
            with open(gui_cfg, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
        except Exception:
            pass

    from settings import DesktopSettings
    ds = DesktopSettings.load()
    old_mode = ds.get("app_mode", "ide")
    ds.set("app_mode", "ide")
    w = MainWindow()
    for timer in getattr(w, "_startup_timers", []):
        try:
            timer.stop()
        except Exception:
            pass
    w.show()
    app.processEvents()
    yield w
    try:
        w._disconnect()
    except Exception as exc:  # noqa: BLE001
        log.warning("收尾失败：%s: %s", type(exc).__name__, exc)
    w.close()
    w.deleteLater()
    app.processEvents()
    try:
        ds.set("app_mode", old_mode)
    except Exception:
        pass
    for k, v in backup.items():
        if v is None:
            qs.remove(k)
        else:
            qs.setValue(k, v)
    qs.sync()
    if cfg_backup is not None:
        with open(gui_cfg, "w", encoding="utf-8") as fh:
            fh.write(cfg_backup)


def _visible_texts(w):
    out = []
    for wdg in w.findChildren(QLabel):
        if wdg.isVisible():
            out.append(("QLabel", wdg.text()))
    for wdg in w.findChildren(QAbstractButton):
        if wdg.isVisible():
            out.append((type(wdg).__name__, wdg.text()))
    for wdg in w.findChildren(QGroupBox):
        if wdg.isVisible():
            out.append(("QGroupBox", wdg.title()))
    for wdg in w.findChildren(QTabWidget):
        if wdg.isVisible():
            for i in range(wdg.count()):
                out.append(("QTabWidget", wdg.tabText(i)))
    return out


def test_default_surface_has_no_mechanism_terms(window):
    texts = _visible_texts(window)
    assert len(texts) >= 20, f"可见文本只扫到 {len(texts)} 条：用例覆盖面不足"
    hits = [(kind, text) for kind, text in texts
            if any(term in text for term in FORBIDDEN)]
    assert not hits, f"默认界面出现了底层机制字样：{hits[:8]}"


_ENG = re.compile(r"(?<![A-Za-z])(az|alt|vmag|gmst|fov|jd|icrs|j2000)(?![A-Za-z])",
                  re.IGNORECASE)
_ZH_ENG = ("方位角", "地平高度", "视场", "儒略日")


def test_default_surface_has_no_unexplained_engineering_terms(window):
    """Task 5.4 的默认态一半：不出现未解释的工程读数/英文缩写。"""
    texts = _visible_texts(window)
    assert len(texts) >= 20
    hits = [(kind, text) for kind, text in texts
            if _ENG.search(text) or any(t in text for t in _ZH_ENG)]
    assert not hits, f"默认界面出现了未解释的工程术语：{hits[:8]}"


def test_detail_drawer_reveals_complete_readouts(window):
    """Task 5.4 的另一半：展开「详细」后工程读数完整可读（功能没删）。

    默认态隐藏 ≠ 删除。点开「详细」后，顶栏抽屉与底坞里的频率/采样率/
    GNSS/多普勒/发射/增益/带宽/设备/信号必须同时可见，且每项既有字段名
    也有值或诚实的「—」占位（不许空着或只剩冒号）。
    """
    fields = {
        "status_freq": "频率",
        "status_sr": "采样率",
        "status_gps": "GNSS",
        "doppler_readout": "多普勒",
        "tx_readout": "发射",
        "status_gain": "增益",
        "status_bw": "带宽",
        "status_dev": "设备",
        "status_rssi": "信号",
    }
    btn = window._focus_btn
    old_size = window.size()
    try:
        # 窄窗有响应式让位（<940 隐藏采样率、<1180 隐藏多普勒芯片），
        # 本条验「展开后完整可读」，按宽窗口径量。
        window.resize(1280, 800)
        for _ in range(12):
            app.processEvents()
        assert window.width() >= 1180, window.width()
        btn.setChecked(True)
        app.processEvents()
        assert window._top_detail.isVisible(), "「详细」展开后顶栏抽屉仍不可见"
        for attr, word in fields.items():
            wdg = getattr(window, attr)
            assert wdg.isVisible(), f"{attr} 展开后仍不可见"
            text = wdg.text().strip()
            assert word in text, f"{attr} 读数字段缺失：{text!r}"
            tail = text.split(":", 1)[-1].strip() if ":" in text else ""
            assert tail, f"{attr} 只有字段名没有值/未知占位：{text!r}"
        # 未知必须写成「—」，数字读数必须带单位或至少含数字；不许空白糊弄。
        for attr in ("status_freq", "status_sr", "status_gain",
                     "status_bw", "status_rssi"):
            text = getattr(window, attr).text()
            assert "—" in text or re.search(r"\d", text), (attr, text)
    finally:
        btn.setChecked(False)
        app.processEvents()
        window.resize(old_size)
        for _ in range(6):
            app.processEvents()
    assert not window._top_detail.isVisible(), "收尾后「详细」应重新收起"


def test_immersive_sky_card_uses_human_words_not_engineering_terms():
    """Task 5.2/5.3：默认（沉浸）星空卡只讲人话，工程读数留在详细卡/API。"""
    from rf_sky_view import (RFSkyView, format_view_axis,
                             format_view_axis_human)

    v = RFSkyView()
    try:
        v.resize(900, 900)
        v.set_observer(40.0, 116.0, 0.0)
        v.set_immersive(True)
        geo = v._immersive_card_geometry()
        rows = geo["rows"]
        assert [k for k, _ in rows] == ["位置", "星空", "天色"]
        text = " ".join([geo["clock"], geo["date_text"]]
                        + [f"{k} {val}" for k, val in rows])
        for term in ("az", "alt", "vmag", "gmst", "fov",
                     "方位角", "地平高度", "视场"):
            assert re.search(rf"(?<![A-Za-z]){term}(?![A-Za-z])", text,
                             re.IGNORECASE) is None, (term, text)
        # 功能没删：数值视轴仍在格式化 API 里，供详细状态卡/日志使用。
        assert "AZ" in format_view_axis(123.4, 45.0)
        assert "朝向" in format_view_axis_human(123.4, 45.0)
    finally:
        v.close()

