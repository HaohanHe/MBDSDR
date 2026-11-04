# SPDX-License-Identifier: MIT
"""
截图基线"受控更新"守卫
=======================

锁定 scripts/ui_baseline.guarded_update 的三条硬规则：
  1. 没有 --reason（人工确认）不许覆盖；
  2. 只允许颜色漂移；尺寸/宽高比这类几何漂移即使带 reason 也红；
  3. 每次更新必须产出可复核摘要（尺寸/粗细色块/变化像素占比）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_baseline_update_guard.py -q
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

from ui_baseline import BaselineUpdateRefused, guarded_update  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


def _make(path, w, h, fill, patch=None):
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(fill))
    if patch:
        color, x, y, pw, ph = patch
        for py in range(y, min(y + ph, h)):
            for px in range(x, min(x + pw, w)):
                img.setPixelColor(px, py, QColor(color))
    img.save(path, "PNG")
    return img


def test_update_requires_reason(tmp_path):
    path = str(tmp_path / "panel_1000x600.png")
    _make(path, 320, 192, "#F5F3EF")
    img = _make(str(tmp_path / "new.png"), 320, 192, "#F5F3EF")
    with pytest.raises(BaselineUpdateRefused, match="reason"):
        guarded_update(path, img, "")


def test_color_only_update_allowed_and_summarized(tmp_path):
    path = str(tmp_path / "panel_1000x600.png")
    _make(path, 320, 192, "#F5F3EF")
    img = _make(str(tmp_path / "new.png"), 320, 192, "#F5F3EF",
                patch=("#4F6B79", 40, 60, 80, 40))
    summary = guarded_update(path, img, "文字色对比度修正（只改颜色）")
    # 摘要必须可复核：尺寸/两档色块/像素占比都在
    assert summary["size"][:2] == (320, 192) and summary["size"][2:] == (320, 192)
    assert summary["coarse"][0] > 0, "颜色变了但粗色块没检测到差异"
    assert summary["pixel_ratio"] > 0.0
    assert "size " in summary["text"] and "8×6" in summary["text"] \
        and "变化像素" in summary["text"]
    # 真的写入了
    assert QColor(QImage(path).pixelColor(60, 80)).name().lower() \
        == QColor("#4F6B79").name().lower()


def test_geometry_size_change_refused_even_with_reason(tmp_path):
    path = str(tmp_path / "panel_1000x600.png")
    _make(path, 320, 192, "#F5F3EF")
    wider = _make(str(tmp_path / "new.png"), 336, 192, "#F5F3EF")
    with pytest.raises(BaselineUpdateRefused, match="几何"):
        guarded_update(path, wider, "只是重建基线")


def test_geometry_height_change_refused_even_with_reason(tmp_path):
    path = str(tmp_path / "panel_1000x600.png")
    _make(path, 320, 192, "#F5F3EF")
    taller = _make(str(tmp_path / "new.png"), 320, 204, "#F5F3EF")
    with pytest.raises(BaselineUpdateRefused, match="几何"):
        guarded_update(path, taller, "只是重建基线")


def test_missing_baseline_can_be_created_with_reason(tmp_path):
    path = str(tmp_path / "panel_1920x1080.png")
    img = _make(str(tmp_path / "new.png"), 320, 180, "#1E1E20")
    summary = guarded_update(path, img, "补测新面板基线")
    assert "新建基线" in summary["text"]
    assert os.path.exists(path)
