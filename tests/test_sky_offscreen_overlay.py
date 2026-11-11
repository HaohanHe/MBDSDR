# SPDX-License-Identifier: MIT
"""反证：相机背后的罗盘字母 / 对象标记绝不允许落到屏幕正中。

2026-11-11 真机实证（截图 ``20261111_200158_center.png``）：投影失败
（相机背后的天区，``sky_to_screen`` 返回 NaN）被 ``_sky_to_screen`` 兜成
``_sky_center()``，而 ``_draw_compass`` / ``_draw_objects`` 只判「在不在屏上」。
屏幕中心当然在屏上，于是背向的 ``N`` 与 30° 刻度全部堆在画面正中。

本文件锁定三条契约：
1. ``_sky_to_screen_finite`` 对背向天区返回 None（不是中心点）；
2. ``_on_screen(None)`` 为 False；
3. 罗盘层与对象标记层在背向天区**一个像素都不画**。

把任一处改回「兜到中心」，这里必须红。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from rf_sky_view import RFSkyView, SkyObject

W, H = 800, 600
CENTER_BOX = 44


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _view(app):
    v = RFSkyView()
    v.resize(W, H)
    v.set_observer(40.0, 116.0, 0.0)
    v.view_state.look_at(0.0, 30.0)      # 朝北看，alt 30°
    v.view_state.fov_deg = 90.0
    v._hovered_object = None
    return v


def _render(v, draw):
    img = QImage(W, H, QImage.Format_ARGB32)
    img.fill(QColor("white"))
    painter = QPainter(img)
    try:
        draw(painter)
    finally:
        painter.end()
    return img


def _ink(img):
    """非白像素掩码（True = 画了东西）。"""
    buf = np.frombuffer(img.constBits(), dtype=np.uint8, count=img.sizeInBytes())
    arr = buf.reshape((img.height(), img.bytesPerLine() // 4, 4))[:, :img.width(), :3]
    return np.any(arr != 255, axis=2)


def _center_ink(img):
    mask = _ink(img)
    y0 = H // 2 - CENTER_BOX // 2
    x0 = W // 2 - CENTER_BOX // 2
    return int(mask[y0:y0 + CENTER_BOX, x0:x0 + CENTER_BOX].sum())


# ---------------------------------------------------------------- 契约 1/2
def test_behind_camera_has_no_position(app):
    v = _view(app)
    # 朝北看时，(az 180, alt 0) 在相机正后方，投影结果非有限。
    assert v._sky_to_screen_finite(180.0, 0.0) is None
    # 正前方必须有位置（否则这个用例只是在测「全部返回 None」）。
    front = v._sky_to_screen_finite(0.0, 20.0)
    assert front is not None and v._on_screen(front)
    # 旧的兜底仍然是中心点——它只配给「一定要有个像素」的路径用。
    legacy = v._sky_to_screen(180.0, 0.0)
    assert (round(legacy.x()), round(legacy.y())) == (W // 2, H // 2)


def test_on_screen_rejects_none(app):
    v = _view(app)
    assert v._on_screen(None) is False


# ---------------------------------------------------------------- 契约 3
def test_compass_never_stacks_at_center(app):
    v = _view(app)
    img = _render(v, v._draw_compass)
    # 有东西被画出来（背向的几枚在正前方/侧前方，仍应正常显示）
    assert int(_ink(img).sum()) > 0
    # 但屏幕正中一个像素都不许有
    assert _center_ink(img) == 0


def test_behind_camera_object_draws_nothing(app):
    v = _view(app)
    behind = SkyObject("BEHIND", azimuth_deg=180.0, elevation_deg=10.0)
    v._objects = [behind]
    assert int(_ink(_render(v, v._draw_objects)).sum()) == 0

    front = SkyObject("FRONT", azimuth_deg=0.0, elevation_deg=20.0)
    v._objects = [front]
    assert int(_ink(_render(v, v._draw_objects)).sum()) > 0
