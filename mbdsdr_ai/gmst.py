# SPDX-License-Identifier: MIT
"""GMST 单一内核：IAU 1982 折叠式（Vallado 式 3-54）。

口径登记（2026-11-14 实测，7 个实现、4 个 JD，见
``tests/test_gmst_single_source.py``）：

====== ============================================== =========================
口径   实现                                            与 A 的实测差
====== ============================================== =========================
A      ``gmst_rad_iau1982``（本模块）                    —
       orbit._gmst_days / orbit_determination.gmst_rad  四个 JD 上逐位一致
       / new_spacetime_timeline._gmst_rad               (0.000000000")
       / astronomy.jd_to_gmst
B      atmosphere._gmst_deg / solar_system._gmst_deg    0.000018" – 0.000222"
       （IAU 1982 简化式，度）                          随历元增长，非逐位相同
C      celestial_geometry.gmst                          0.0145" – 0.1261"
       （Capitaine 2003，度）                           **不同物理模型**
====== ============================================== =========================

**本模块只管 A。** B 是同一模型的另一种截断，替换会静默改数值；C 是星象仪
IAU 2006 岁差 / 章动链的一环，合并等于破坏高精度链。唯一真值必须落在
**同一口径内**，不能跨口径一刀切——这是从 QGIS 3.44.15 的
「CRS 唯一真值 + 按 CRS 对点名 operation + 缓存键带历元」学到的治理次序：
先分口径、再收敛重复、再常态对拍。

``8640184.812866`` 项不可漏：漏掉会让 GMST 偏数小时，过境 AOS/LOS 整体错位，
不是采样步长能解释的差。
"""
from __future__ import annotations

import math

__all__ = ["gmst_rad_iau1982", "gmst_deg_iau1982"]

#: J2000 起算的 GMST 常数项（秒）。
GMST_BASE_SEC = 67310.54841
#: 每儒略世纪的 GMST 增量（秒）：876600h * 3600 + 8640184.812866。
GMST_CENTURY_SEC = 876600.0 * 3600.0 + 8640184.812866
J2000_JD = 2451545.0


def gmst_rad_iau1982(jd_ut1: float) -> float:
    """IAU 1982 折叠式 GMST（弧度，[0, 2π)）。``jd_ut1`` 为 UT1 儒略日。

    这是口径 A 的**唯一实现**；同口径的其它模块一律委托到这里，不许再抄
    多项式。口径 B/C 见模块 docstring，不在此收敛。
    """
    t = (jd_ut1 - J2000_JD) / 36525.0
    gmst_sec = (GMST_BASE_SEC
                + GMST_CENTURY_SEC * t
                + 0.093104 * t * t
                - 6.2e-6 * t * t * t)
    return math.radians((gmst_sec % 86400.0) / 240.0) % (2.0 * math.pi)


def gmst_deg_iau1982(jd_ut1: float) -> float:
    """同一内核的度出口（[0, 360)）。不重复算多项式。"""
    return math.degrees(gmst_rad_iau1982(jd_ut1)) % 360.0
