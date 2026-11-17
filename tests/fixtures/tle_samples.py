# SPDX-License-Identifier: MIT
"""测试专用 TLE 样本（**不是生产数据**）。

历史 ``decoders.BUILTIN_TLE`` 里的合成/凑整值按 D4 已从生产路径移除。
依赖固定轨道根数的测试（天空视图、过境链、SGP4 对拍）改用本文件：

    from tle_samples import TEST_TLE, ISS_TLE, install_test_tle

用法约定：
  * 只读常量：``TEST_TLE["ISS (ZARYA)"]``；
  * 需要让生产函数看到样本：``install_test_tle(monkeypatch)``，它把
    ``decoders.BUILTIN_TLE`` 临时替换为本表（测试结束自动还原）。

这些值历元为整天/半整天凑整，只适合做几何/链路回归，**不得当精确历元**。
"""
from __future__ import annotations

from typing import Dict, Tuple

TEST_TLE: Dict[str, Tuple[str, str]] = {
    "NOAA 15": (
        "1 25338U 98030A   26250.50000000  .00000050  00000-0  10000-3 0  9993",
        "2 25338  98.7000 100.0000 0010000  90.0000 270.0000 14.25000000400000",
    ),
    "NOAA 18": (
        "1 28654U 05018A   26250.50000000  .00000050  00000-0  10000-3 0  9994",
        "2 28654  99.1000 100.0000 0010000  90.0000 270.0000 14.10000000400000",
    ),
    "NOAA 19": (
        "1 33591U 09005A   26250.50000000  .00000050  00000-0  10000-3 0  9995",
        "2 33591  99.2000 100.0000 0010000  90.0000 270.0000 14.10000000400000",
    ),
    "ISS (ZARYA)": (
        "1 25544U 98067A   26250.50000000  .00000050  00000-0  10000-3 0  9996",
        "2 25544  51.6400 100.0000 0006700  90.0000 270.0000 15.50000000400000",
    ),
    "METEOR M2": (
        "1 44016U 17008A   26250.50000000  .00000050  00000-0  10000-3 0  9997",
        "2 44016  98.7000 100.0000 0001000  90.0000 270.0000 14.20000000400000",
    ),
    "FENGYUN 3D": (
        "1 54234U 22152A   26250.50000000  .00000050  00000-0  10000-3 0  9998",
        "2 54234  98.9000 100.0000 0010000  90.0000 270.0000 14.20000000400000",
    ),
}

ISS_TLE = TEST_TLE["ISS (ZARYA)"]
NOAA15_TLE = TEST_TLE["NOAA 15"]
NOAA19_TLE = TEST_TLE["NOAA 19"]


def install_test_tle(monkeypatch=None) -> Dict[str, Tuple[str, str]]:
    """把 ``decoders.BUILTIN_TLE`` 临时替换为测试样本。

    传 ``monkeypatch`` 时由 pytest 在用例结束自动还原；不传时直接替换，
    调用方需自行恢复（不推荐）。
    """
    from mbdsdr_ai import decoders

    replacement = dict(TEST_TLE)
    if monkeypatch is not None:
        monkeypatch.setattr(decoders, "BUILTIN_TLE", replacement, raising=False)
    else:  # pragma: no cover - 仅给非 pytest 脚本用
        decoders.BUILTIN_TLE.clear()
        decoders.BUILTIN_TLE.update(replacement)
    return replacement
