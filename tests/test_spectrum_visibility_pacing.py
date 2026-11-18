"""FR-06：频谱与瀑布在隐藏时停表，显示时恢复。

缺陷形态：这两个 widget 的 QTimer 只管重绘，看不见时照烧 CPU。
星空视图（rf_sky_view）早有同一范式（showEvent 恢复 / hideEvent 停止），
这里补的是频谱侧缺的那一半——不新造机制，照已验证的范式接上。

测试不 show() 真实控件：SpectrumCurveWidget 的 paintEvent 依赖整个 panel 的
主题属性链，show() 会触发真实绘制并在测试桩下炸成 Qt 全局崩溃。
改为直接投递 QHideEvent/QShowEvent，只验停表逻辑本身。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtGui import QHideEvent, QShowEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from spectrum_widget import SpectrumCurveWidget, WaterfallWidget, _PlotState  # noqa: E402


class _PanelStub:
    """停表逻辑不碰 panel；paint 才是依赖大户，而本测试不触发 paint。"""
    generator = None


def _make(widget_cls):
    state = _PlotState(_PanelStub())
    return widget_cls(state)


def _hide(w):
    w.hideEvent(QHideEvent())


def _show(w):
    w.showEvent(QShowEvent())


def test_hidden_stops_curve_timer():
    w = _make(SpectrumCurveWidget)
    assert w._timer.isActive(), "构造后定时器必须运行"
    _hide(w)
    assert not w._timer.isActive(), "隐藏后频谱仍在重绘——白烧 CPU"
    w.close()


def test_reshown_resumes_curve_timer():
    w = _make(SpectrumCurveWidget)
    _hide(w)
    assert not w._timer.isActive()
    _show(w)
    assert w._timer.isActive(), "重新显示后定时器没恢复——画面会冻住"
    w.close()


def test_hidden_stops_waterfall_timer():
    w = _make(WaterfallWidget)
    assert w._timer.isActive()
    _hide(w)
    assert not w._timer.isActive(), "隐藏后瀑布仍在重绘"
    w.close()


def test_reshown_resumes_waterfall_timer():
    w = _make(WaterfallWidget)
    _hide(w)
    _show(w)
    assert w._timer.isActive(), "重新显示后瀑布冻住"
    w.close()


def test_interval_unchanged():
    """停表不能顺手改节拍——50/100ms 是既有行为，不许被这次改动带偏。"""
    curve = _make(SpectrumCurveWidget)
    wf = _make(WaterfallWidget)
    assert curve._timer.interval() == 50
    assert wf._timer.interval() == 100
    curve.close()
    wf.close()


def test_source_guard_both_classes_have_handlers():
    """读源码断言两个类都有 show/hide 守卫——防止重构时只留一个。"""
    import inspect

    for cls in (SpectrumCurveWidget, WaterfallWidget):
        src = inspect.getsource(cls)
        assert "def showEvent" in src, f"{cls.__name__} 缺 showEvent"
        assert "def hideEvent" in src, f"{cls.__name__} 缺 hideEvent"
        assert "if not self._timer.isActive()" in src
        assert "if self._timer.isActive()" in src
