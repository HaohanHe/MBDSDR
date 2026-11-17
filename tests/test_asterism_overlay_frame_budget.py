# SPDX-License-Identifier: MIT
"""
tests/test_asterism_overlay_frame_budget.py
============================================
把「T08 数据层不接入桌面实时绘制热路径」钉成可执行约束。

约束
----
桌面实时绘制入口 ``desktop/rf_sky_view.py::_draw_constellations`` 对每个打开
的天空文化体系调用一次 ``_layer_points``，后者内部对**整张星表**做一次批量
坐标换算 ``j2000_to_altaz_many``。因此一帧星官层的调用次数必须恰好等于
「打开的体系数」：

    只开中国星官 -> 1 次；只开 IAU -> 1 次；两套同开 -> 2 次。

为什么用「调用次数」而不是「耗时」
----------------------------------
耗时断言在 CI 并行/共享机器上会抖动，同一条代码今天绿明天红；调用次数是
确定性的，能把「每帧多算 N 次」这种退化直接表达成可执行判据。当前实现每
体系 1 次是正确形态：``mbdsdr_ai/overlay_asterisms.build_overlay``（T08）在
``_build_one`` 里**逐星**调用 ``j2000_to_altaz_many``（全量约 1244 次/帧、
约 170 ms/帧），一旦有人把它接进热路径，计数会立刻从 1 跳到上千，本测试
变红——这就是禁止 T08 进热路径的机械表达。

另外，每次调用的入参第一维必须 > 1（批量调用）；出现单星调用说明有人把
逐星换算塞回了热路径。失败时打印调用栈摘要便于定位。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_asterism_overlay_frame_budget.py -q
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import traceback  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import numpy as np  # noqa: E402

from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import RFSkyView  # noqa: E402
from mbdsdr_ai.skyengine import constellations as _constel  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402

# 中性示例站点（项目约定：仓库内不出现真实坐标）
LAT, LON, HEIGHT_M = 45.0, 120.0, 0.0
CENTER_AZ, CENTER_ALT = 180.0, 60.0
FIXED_UNIX = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp()

BUDGET_FAIL_HINT = (
    "每体系一帧只允许一次批量换算；变多说明有人把 T08/逐星换算接进了热路径"
)


def _fix_time() -> None:
    eng = get_time_engine()
    eng.set_unix(FIXED_UNIX)
    eng.pause()


def _new_view() -> RFSkyView:
    _fix_time()
    view = RFSkyView()
    view.resize(700, 700)
    view.set_observer(LAT, LON, HEIGHT_M)
    return view


def _install_counter(monkeypatch):
    """替换 rf_sky_view 命名空间里的批量坐标换算，记录每帧调用。

    ``rf_sky_view`` 是用 ``from ... import j2000_to_altaz_many`` 引入的，
    因此必须 patch 该模块自己的同名属性，而不是坐标模块里那份。
    每次调用记录入参第一维：正常热路径是整表批量（> 1），逐星实现会是 1。
    """
    import rf_sky_view

    real = rf_sky_view.j2000_to_altaz_many
    calls = []

    def counting(*args, **kwargs):
        arr = np.asarray(args[0])
        dim = int(arr.shape[0]) if arr.ndim >= 1 else 1
        entry = {"dim": dim}
        if dim <= 1:
            stack = traceback.extract_stack()
            entry["stack"] = "".join(traceback.format_list(stack[-7:-1]))
        calls.append(entry)
        return real(*args, **kwargs)

    monkeypatch.setattr(rf_sky_view, "j2000_to_altaz_many", counting)
    return calls


def _draw_frame(view: RFSkyView) -> None:
    img = QImage(view.width(), view.height(), QImage.Format.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    try:
        view._draw_constellations(painter)
    finally:
        painter.end()


def _measured_frame_calls(monkeypatch, systems):
    """预热一帧甩掉缓存/一次性开销，再清空计数画正式一帧。

    预热帧本身也会调批量换算，所以顺序必须是「预热 -> clear -> 计数帧」。
    视点方向只影响可见性，不影响批量换算次数；``_layer_points`` 始终对整表
    做一次换算，因此这里取一个有内容的天空方向即可。
    """
    calls = _install_counter(monkeypatch)
    view = _new_view()
    assert view._show_constellations is True
    view.set_constellation_systems(systems)
    view.view_state.look_at(CENTER_AZ, CENTER_ALT)

    _draw_frame(view)          # 预热：缓存 _constel_prepared 等一次性开销
    calls.clear()
    _draw_frame(view)          # 正式计数帧
    return list(calls)


def _count(calls) -> int:
    return sum(1 for _ in calls)


def test_chinese_only_costs_one_batch_call_per_frame(monkeypatch):
    calls = _measured_frame_calls(monkeypatch, [_constel.SYSTEM_CHINESE])
    assert _count(calls) == 1, (
        f"中国星官单层一帧调用了 {_count(calls)} 次批量坐标换算。"
        f"{BUDGET_FAIL_HINT}"
    )


def test_iau_only_costs_one_batch_call_per_frame(monkeypatch):
    calls = _measured_frame_calls(monkeypatch, [_constel.SYSTEM_IAU])
    assert _count(calls) == 1, (
        f"IAU 单层一帧调用了 {_count(calls)} 次批量坐标换算。"
        f"{BUDGET_FAIL_HINT}"
    )


def test_both_systems_cost_two_batch_calls_per_frame(monkeypatch):
    calls = _measured_frame_calls(
        monkeypatch, [_constel.SYSTEM_IAU, _constel.SYSTEM_CHINESE])
    assert _count(calls) == 2, (
        f"两套体系同开一帧调用了 {_count(calls)} 次批量坐标换算。"
        f"{BUDGET_FAIL_HINT}"
    )


def test_every_frame_call_is_batch_not_per_star(monkeypatch):
    """防退化：每次换算的入参第一维必须 > 1，不能退化成逐星调用。"""
    calls = _measured_frame_calls(
        monkeypatch, [_constel.SYSTEM_IAU, _constel.SYSTEM_CHINESE])
    bad = [c for c in calls if c["dim"] <= 1]
    if bad:
        details = "\n".join(c["stack"] for c in bad[:3])
        raise AssertionError(
            f"一帧内有 {len(bad)} 次 j2000_to_altaz_many 的入参第一维 <= 1，"
            f"说明逐星换算被接回了热路径。调用栈摘要:\n{details}"
        )
