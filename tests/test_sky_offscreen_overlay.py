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

from rf_sky_view import (AntennaPointing, RFSkyView, SkyObject,
                         antenna_pointing_label)

from mbdsdr_ai.sky_actions import SkyActionBus, apply_look_at

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


# ------------------------------------------------ 契约 3（2026-11-16 扩面）
# 同一类兜底还活在这几层：轨迹 / GNSS / 天线 / 晨昏辉光。用户真机看到
# 「放大后中间一堆火星、还有一根线挂着一个点」，就是它们把背向点搬到中心。
def test_trajectory_behind_camera_draws_nothing(app):
    v = _view(app)
    v._trajectories = {"BEHIND": [(175.0, 5.0), (180.0, 10.0), (185.0, 15.0)]}
    img = _render(v, v._draw_trajectories)
    assert int(_ink(img).sum()) == 0
    assert _center_ink(img) == 0

    v._trajectories = {"FRONT": [(0.0, 20.0), (5.0, 25.0), (10.0, 30.0)]}
    assert int(_ink(_render(v, v._draw_trajectories)).sum()) > 0


def test_gnss_behind_camera_draws_nothing(app):
    v = _view(app)
    v.update_gnss_satellites([{"talker": "GP", "sats": [
        {"id": 7, "azimuth": 180.0, "elevation": 10.0, "snr_db": 40.0}]}])
    img = _render(v, v._draw_gnss_satellites)
    assert int(_ink(img).sum()) == 0
    assert _center_ink(img) == 0

    v.update_gnss_satellites([{"talker": "GP", "sats": [
        {"id": 7, "azimuth": 0.0, "elevation": 20.0, "snr_db": 40.0}]}])
    assert int(_ink(_render(v, v._draw_gnss_satellites)).sum()) > 0


def test_antenna_behind_camera_draws_nothing(app):
    v = _view(app)
    v._antenna = AntennaPointing(azimuth_deg=180.0, elevation_deg=45.0,
                                 beamwidth_deg=10.0, has_pointing=True)
    pointer = _render(v, v._draw_antenna_pointer)
    assert int(_ink(pointer).sum()) == 0
    assert _center_ink(pointer) == 0
    assert int(_ink(_render(v, v._draw_antenna_beam)).sum()) == 0

    v._antenna = AntennaPointing(azimuth_deg=0.0, elevation_deg=25.0,
                                 beamwidth_deg=10.0, has_pointing=True)
    assert int(_ink(_render(v, v._draw_antenna_pointer)).sum()) > 0


def test_unknown_pointing_draws_no_beam_or_pointer(app):
    """没有旋转器数据时（has_pointing=False）不许画占位波束/指针。

    构造函数的默认值是正北 45°、30° 波束、5 dBi——那是占位，不是测量。
    默认 IDE 视图（天顶、fov 120）恰好把它投在视线正下方，看上去就是
    「一根竖线中间一个点」，用户会当成渲染故障。
    """
    v = _view(app)
    assert v._antenna.has_pointing is False
    assert int(_ink(_render(v, v._draw_antenna_pointer)).sum()) == 0
    assert int(_ink(_render(v, v._draw_antenna_beam)).sum()) == 0
    assert _center_ink(_render(v, v._draw_antenna_pointer)) == 0

    # 注入真实指向后才会出现；清除后回到空态。
    v.set_antenna_pointing(0.0, 25.0, beamwidth_deg=10.0)
    assert int(_ink(_render(v, v._draw_antenna_pointer)).sum()) > 0
    v.clear_antenna_pointing()
    assert int(_ink(_render(v, v._draw_antenna_pointer)).sum()) == 0


def test_status_card_says_no_pointing_data(app):
    """状态卡「指向(几何)」行在无数据时写「—」，不写 AZ 0.0°。"""
    v = _view(app)
    card = _render(v, v._draw_info_overlay)
    # 指向行基线 y=card_y+20 → 文字上沿约 +8；取横向 80..300 区间
    y = int(card.height() - RFSkyView.INFO_CARD_H - 12 + 20)
    text_ink = int(_ink(card)[max(0, y - 12):y + 2, 80:300].sum())
    assert text_ink > 0, "无数据时该行仍应有「—」占位，不能整行空白"

    v.set_antenna_pointing(120.0, 30.0)
    with_pointing = _render(v, v._draw_info_overlay)
    assert int(_ink(with_pointing)[max(0, y - 12):y + 2, 80:300].sum()) > 0


def test_pointing_label_follows_source(app):
    """标签按来源分档：指令角/模拟回读不得被写成「实测」。"""
    assert antenna_pointing_label(AntennaPointing()) == "指向(几何)"
    for src, want in (("readback", "指向(实测)"),
                      ("simulated", "指向(模拟)"),
                      ("command", "指向(指令)"),
                      ("", "指向(几何)")):
        a = AntennaPointing(has_pointing=True, pointing_source=src)
        assert antenna_pointing_label(a) == want, src


def test_sun_glow_behind_camera_draws_nothing(app):
    v = _view(app)
    v._sun = {"az": 180.0, "alt": 2.0}      # 背向、贴地平：辉光强度不为零
    img = _render(v, v._draw_atmosphere_glow)
    assert int(_ink(img).sum()) == 0


# ------------------------------------------- 非投影类中心杂物（2026-11-17）
# 这两处不走投影兜底，但同样会在视线正中留下"一根竖线 + 一个点"。审计见
# docs/learn/2026-11-17-sky-zoom-clutter-audit.md（R1 / R2）。
class _PathRecorder:
    """假画家：只收 QPainterPath，其它绘制调用一律忽略。"""

    def __init__(self):
        self.paths = []

    def __getattr__(self, name):
        return lambda *args, **kwargs: None

    def drawPath(self, path):  # noqa: N802 (Qt 命名)
        self.paths.append(path)


def _path_points(path):
    return [(path.elementAt(i).x, path.elementAt(i).y)
            for i in range(path.elementCount())]


def test_antenna_pointer_hidden_in_immersive_mode(app):
    """星象仪里不许出现"未跟踪的默认指向"十字+圆点（R1）。

    波束层早已为此在沉浸态主动不画；指针层漏了同一道守卫，于是放大后
    正中恒有一个"十字 + 3px 圆点"——用户报的"一根竖线中间一个点"。
    """
    v = _view(app)
    v._antenna = AntennaPointing(azimuth_deg=0.0, elevation_deg=45.0,
                                 beamwidth_deg=10.0, has_pointing=True)
    v.set_immersive(True)
    assert int(_ink(_render(v, v._draw_antenna_pointer)).sum()) == 0, \
        "沉浸态（星象仪）不得画默认天线指向"
    v.set_immersive(False)
    assert int(_ink(_render(v, v._draw_antenna_pointer)).sum()) > 0, \
        "IDE 形态仍要显示天线指向——守卫只该针对沉浸态"


def test_azimuth_grid_does_not_converge_at_zenith(app):
    """12 条等方位射线不得全部终止在视线正中（R2）。

    默认视线就是天顶（ViewState.center_alt=90），而旧实现把射线采样到
    alt=90，于是 12 条线在正中汇成一点，叠上穿过中心的子午线，就是
    "一根竖线 + 中心一个点"。极点留空后，中心 2px 内不该有任何网格点。
    """
    v = _view(app)
    v.view_state.look_at(0.0, 90.0)
    v.view_state.fov_deg = 120.0
    rec = _PathRecorder()
    v._draw_az_grid(rec)
    assert rec.paths, "方位网格必须仍然落笔（否则用例只是在测一个死层）"
    pts = _path_points(rec.paths[0])
    assert len(pts) > 100, "120° 视场下网格点不该只剩寥寥几个"
    cx, cy = W / 2.0, H / 2.0
    at_center = [p for p in pts
                 if abs(p[0] - cx) <= 2.0 and abs(p[1] - cy) <= 2.0]
    assert not at_center, (
        f"有 {len(at_center)} 条射线终止在视线正中；采样到 alt=90 会把 12 条"
        "子午线扎成一朵，必须留出极点空隙")


def test_look_at_action_really_moves_the_view(app):
    """``sky_look_at`` 的端到端落点：总线排水后视图轴真的转过去。

    这是「自然语言能不能真的转视角」的唯一机械证据——只测 validate 不够，
    必须证明 UI 线程执行器把角度落到了 ``view_state`` 上。
    """
    v = _view(app)
    bus = SkyActionBus(executor=lambda a: apply_look_at(v, a))
    queued = bus.enqueue({"action": "look_at", "az_deg": 90.0,
                          "alt_deg": 10.0, "fov_deg": 30.0})
    assert queued["status"] == "QUEUED"
    res = bus.drain()[0]
    assert res["status"] == "DONE", res
    assert abs(v.view_state.center_az - 90.0) < 1e-6
    assert abs(v.view_state.center_alt - 10.0) < 1e-6
    assert abs(v.view_state.fov_deg - 30.0) < 1e-6
    assert abs(res["alt_deg"] - 10.0) < 1e-6


def test_look_at_without_open_view_does_not_fake_success(app):
    """天空视图没打开时必须 UNAVAILABLE，不能回报「已转向」。"""
    res = apply_look_at(None, {"action": "look_at",
                               "az_deg": 0.0, "alt_deg": 45.0})
    assert res["ok"] is False
    assert res["status"] == "UNAVAILABLE"

