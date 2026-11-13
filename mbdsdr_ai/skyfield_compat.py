"""skyfield 可选依赖兼容层（零内部依赖叶子）
===========================================

``skyfield`` 属 satellite 可选依赖组；缺它时过境预报/多普勒曲线必须给出
可操作的诚实报错，而不是 ``AttributeError: NoneType``。

本层原先住在 ``sat_passes`` 里，导致 ``sat_tracker`` / ``sat_illumination``
为了一个「依赖可用性检查 + 时间系统缓存」反向依赖整个过境预测重模块
（D1 环）。下沉到此叶子后，``sat_passes`` 原位再导出，
``sat_passes.make_timescale is skyfield_compat.make_timescale``，行为不变。
"""
from __future__ import annotations

try:
    from skyfield.api import EarthSatellite, Topos, Time, load as _sky_load
    _HAS_SKYFIELD = True
except ImportError:  # skyfield 属 satellite 可选依赖组；缺它模块仍可导入
    EarthSatellite = None  # type: ignore[assignment,misc]
    Topos = None  # type: ignore[assignment,misc]
    Time = None  # type: ignore[assignment,misc]
    _sky_load = None  # type: ignore[assignment]
    _HAS_SKYFIELD = False


def _require_skyfield() -> None:
    """skyfield 缺失时给出可操作的诚实报错（而非 AttributeError: NoneType）。"""
    if not _HAS_SKYFIELD:
        raise ImportError(
            "skyfield 未安装：过境预报/多普勒曲线需要它"
            "（pip install 'mbdsdr_ai[satellite]' 或 pip install skyfield）")


def make_timescale():
    """返回 skyfield 时间系统（缓存，避免重复加载 ephemeris/UT1 表）。"""
    if not hasattr(make_timescale, "_ts"):
        _require_skyfield()
        make_timescale._ts = _sky_load.timescale()
    return make_timescale._ts
