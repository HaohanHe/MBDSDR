# SPDX-License-Identifier: MIT
"""星官连线的置信度形态（PRD v2 §13.2）：线是自证的，所以"准"与"不准"不许长得一样。

为什么这不是"多一个开关"
------------------------
线画在星上，用户是拿它当"这几颗确实连在一起"来读的。所以：

* 解算过、误差小 → 实线：这条形状你可以信；
* 只粗指向、或**根本没有误差自报** → 虚线：方向大致对，形状别当真；
* 误差超限 → **一条都不画**。画一条偏十几度的线比不画更糟，
  因为用户会记住错的形状。

门槛用**视场比例**而不是绝对角度：2.3° 目镜要求 0.23°，60° 广角允许 6°，
同一个绝对阈值在两种视场下一个太松一个太紧。

未绑定真实指向时是 `ideal` 档（实线）——那是"看天"语义，星表几何本身精确，
不存在"解算误差"这回事。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sky_confidence.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import RFSkyView  # noqa: E402
from mbdsdr_ai.skyengine import constellations as _constel  # noqa: E402


def _new_view(size: int = 500) -> RFSkyView:
    v = RFSkyView()
    v.resize(size, size)
    return v


def _bind(view, az, alt, fov, uncertainty=None, source="test"):
    ok = view.bind_pointing(az, alt, fov, source=source,
                            uncertainty_deg=uncertainty)
    assert ok, "绑定失败，后续断言无意义"
    return view


class TestUnboundIsIdeal:
    def test_no_binding_is_ideal(self):
        """没绑定真实指向 = 虚拟天空，星表算出来的形状本身精确。"""
        v = _new_view()
        assert v._constellation_confidence() == ("ideal", None)
        v.close()

    def test_user_takeover_returns_to_ideal(self):
        """用户拖过/缩过 = 已接管，回到"看天"语义，不该继续画虚线。"""
        v = _new_view()
        _bind(v, 180.0, 45.0, 30.0, uncertainty=8.0)
        assert v._constellation_confidence()[0] != "ideal"
        # 手动把视图推走，超过 drift 容差
        v.view_state.look_at(20.0, 70.0)
        v.view_state.zoom_to(100.0)
        assert v.pointing_binding["still_bound"] is False
        assert v._constellation_confidence() == ("ideal", None)
        v.close()


class TestBands:
    def test_confident_when_error_small(self):
        v = _new_view()
        # 视场 30° → 门槛 3.0°
        _bind(v, 180.0, 45.0, 30.0, uncertainty=1.0)
        band, ratio = v._constellation_confidence()
        assert band == "confident"
        assert ratio == pytest.approx(1.0 / 30.0)
        v.close()

    def test_exactly_on_threshold_is_confident(self):
        """边界闭合：等于门槛仍算"准"，免得浮点抖动把它甩到虚线档。"""
        v = _new_view()
        limit = float(RFSkyView.CONFIDENT_FOV_FRACTION)
        _bind(v, 180.0, 45.0, 30.0, uncertainty=30.0 * limit)
        assert v._constellation_confidence()[0] == "confident"
        v.close()

    def test_coarse_when_error_medium(self):
        v = _new_view()
        _bind(v, 180.0, 45.0, 30.0, uncertainty=6.0)  # 20%
        assert v._constellation_confidence()[0] == "coarse"
        v.close()

    def test_none_when_error_too_large(self):
        v = _new_view()
        _bind(v, 180.0, 45.0, 30.0, uncertainty=15.0)  # 50%
        assert v._constellation_confidence()[0] == "none"
        v.close()

    def test_missing_uncertainty_is_dashed_not_solid(self):
        """没自报误差 → 虚线。画实线等于替解算器担保。"""
        v = _new_view()
        _bind(v, 180.0, 45.0, 30.0, uncertainty=None)
        assert v._constellation_confidence() == ("unknown", None)
        v.close()

    def test_nan_uncertainty_is_unknown(self):
        v = _new_view()
        _bind(v, 180.0, 45.0, 30.0, uncertainty=float("nan"))
        assert v._constellation_confidence()[0] == "unknown"
        v.close()

    def test_negative_uncertainty_is_unknown(self):
        """负误差没有物理意义，不许当成"比零还准"。"""
        v = _new_view()
        _bind(v, 180.0, 45.0, 30.0, uncertainty=-1.0)
        assert v._constellation_confidence()[0] == "unknown"
        v.close()


class TestThresholdScalesWithFov:
    def test_same_error_flips_band_by_fov(self):
        """同一绝对误差在窄视场是"超限"，在宽视场是"准"——门槛必须跟视场走。"""
        narrow = _new_view()
        _bind(narrow, 180.0, 45.0, 2.3, uncertainty=1.0)   # 43%
        assert narrow._constellation_confidence()[0] == "none"
        narrow.close()

        wide = _new_view()
        _bind(wide, 180.0, 45.0, 60.0, uncertainty=1.0)    # 1.7%
        assert wide._constellation_confidence()[0] == "confident"
        wide.close()


class TestDrawGuards:
    """绘制路径的守卫：读源码断言形态真的接进去了（防回退成"一律实线"）。"""

    def _src(self):
        import rf_sky_view as rsv

        return open(rsv.__file__, encoding="utf-8").read()

    def test_draw_constellations_consults_confidence(self):
        src = self._src()
        start = src.index("def _draw_constellations")
        end = src.index("def _on_screen", start)
        body = src[start:end]
        assert "_constellation_confidence()" in body, \
            "绘制没查置信度档位——线又变成一律实线了"
        assert 'confidence == "none"' in body, \
            "误差超限时必须一条都不画"
        assert "DashLine" in body, "粗指向/未知误差必须画虚线"

    def test_band_constants_are_documented_as_provisional(self):
        """门槛是起点不是实测结论，注释必须写清楚，别被当成精度宣称。"""
        src = self._src()
        idx = src.index("CONFIDENT_FOV_FRACTION")
        window = src[idx:idx + 1200]
        assert "实测" in window, "门槛常量必须声明为待实测校准，不能当成精度宣称"


class TestStatusCardHonesty:
    """状态卡必须与画布说同一句话：画不出线时卡片不许显示正常体。"""

    def test_over_limit_says_not_drawn(self):
        v = _new_view()
        _bind(v, 180.0, 45.0, 30.0, uncertainty=15.0)  # 50% > 33% → 不画
        text = v.constellation_systems_text()
        assert text.startswith("IAU")
        assert "未画线" in text and "超限" in text
        v.close()

    def test_unknown_uncertainty_says_dashed(self):
        v = _new_view()
        _bind(v, 180.0, 45.0, 30.0, uncertainty=None)
        assert "虚线(未自报误差)" in v.constellation_systems_text()
        v.close()

    def test_coarse_says_dashed_with_error(self):
        v = _new_view()
        _bind(v, 180.0, 45.0, 30.0, uncertainty=6.0)  # 20%
        assert "虚线(误差 6.00°)" in v.constellation_systems_text()
        v.close()

    def test_confident_has_no_suffix(self):
        v = _new_view()
        _bind(v, 180.0, 45.0, 30.0, uncertainty=1.0)
        assert v.constellation_systems_text() == "IAU"
        v.close()

    def test_user_takeover_has_no_suffix(self):
        v = _new_view()
        _bind(v, 180.0, 45.0, 30.0, uncertainty=15.0)
        v.view_state.look_at(20.0, 70.0)
        v.view_state.zoom_to(100.0)
        assert v.constellation_systems_text() == "IAU"
        v.close()

    def test_unbound_has_no_suffix(self):
        v = _new_view()
        assert v.constellation_systems_text() == "IAU"
        v.close()

    def test_incomplete_asterisms_counted_when_chinese_layer_on(self):
        v = _new_view()
        v.set_constellation_systems(
            [_constel.SYSTEM_IAU, _constel.SYSTEM_CHINESE])
        _bind(v, 180.0, 45.0, 30.0, uncertainty=1.0)  # confident：只有数据后缀
        text = v.constellation_systems_text()
        assert "数据不全 4 官" in text

        # 独立推导：不信产品里的计数函数，直接遍历数据层的未解析 token。
        # 数据变化时这个数字必须是有意为之，改测试前先确认数据层确实变了。
        from mbdsdr_ai.skyengine import xingguan as _xingguan

        data = _xingguan.get_xingguan()
        expected = 0
        for region in data.regions:
            if region.unresolved_tokens:
                expected += 1
            expected += sum(1 for xg in region.xingguan if xg.unresolved_tokens)
        assert expected == 4
        assert f"数据不全 {expected} 官" in text
        v.close()

    def test_unbound_never_reports_incomplete_data(self):
        """纯看天模式不受数据口径影响：没绑定就不追任何状态后缀。"""
        v = _new_view()
        v.set_constellation_systems(
            [_constel.SYSTEM_IAU, _constel.SYSTEM_CHINESE])
        assert v.constellation_systems_text() == "IAU + 中国星官"
        v.close()
