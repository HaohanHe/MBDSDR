#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P1 反证：更新 TLE 后，天空图必须画新根数（而不是自己那份 47 天前的）。

背景
----
执行侧 ``mbdsdr_ai.sat_tracker.SatelliteTracker`` 在「更新 TLE」后换掉了
``_catalog``；天空图 ``desktop/rf_sky_view.py`` 的 ``SatelliteTracker``
却只在 ``__init__`` 里读过一次目录。两边的历元因此分叉：闸门放行了新数据，
画面还在画旧根数。

做法（离线、可重复）
--------------------
1. 隔离 HOME（不读用户真实缓存）；
2. 执行侧载入历元 26268 的内置 NOAA 15，再 ``load_tle`` 成历元 26315；
3. 天空图向其投影（若实现了订阅）后 ``refresh()``；
4. 比较两侧 NOAA 15 的历元列。

退出码：0 一致且已换新 / 1 天空图仍旧（缺陷复现） / 2 无法判定。
用法：python scripts/repro/2026-11-11-p1-sky-catalog-stale.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["MBDSDR_OFFLINE"] = "1"   # 本反证不联网

OLD_EPOCH = "26268.86268050"
NEW_EPOCH = "26315.50000000"
NEW_L1 = "1 25338U 98030A   26315.50000000  .00000085  00000+0  52080-4 0  9991"
NEW_L2 = "2 25338  98.5049 286.7672 0011219  56.3023 303.9227 14.27170971475651"


def _epoch(rows, name):
    """历元列：兼容天空图目录（line1）与执行侧目录（tle=[l1,l2]）。"""
    for r in rows or []:
        if r.get("name") != name:
            continue
        line1 = r.get("line1")
        if not line1:
            tle = r.get("tle") or []
            line1 = tle[0] if len(tle) >= 1 else ""
        return str(line1)[18:32]
    return None


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="mbdsdr-sky-repro-")
    for var in ("HOME", "USERPROFILE"):
        os.environ[var] = tmp

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    from mbdsdr_ai.sat_tracker import SatelliteTracker as LoopTracker
    from desktop.rf_sky_view import RFSkyView, SatelliteTracker as SkyTracker

    owner = LoopTracker(None)
    sky = SkyTracker(RFSkyView(), None, None)
    owner_before = _epoch(owner.list_satellites(), "NOAA 15")

    # 若已实现订阅，绑定之；否则按旧行为（天空图自持启动快照）
    bind = getattr(sky, "set_catalog_source", None)
    if bind is not None:
        from desktop.main_window import MainWindow
        from types import SimpleNamespace
        holder = SimpleNamespace(_loop_sat_tracker=owner)
        bind(lambda: MainWindow._sky_catalog_source(holder))

    ok = owner.load_tle(["NOAA 15", NEW_L1, NEW_L2])
    owner_after = _epoch(owner.list_satellites(), "NOAA 15")
    if not ok or owner_after != NEW_EPOCH:
        print(f"[2] 执行侧未能换新：before={owner_before} after={owner_after}")
        return 2

    sky.refresh()
    sky_after = _epoch(sky._catalog, "NOAA 15")

    print(f"执行侧 NOAA 15 历元：{owner_before} -> {owner_after}")
    print(f"天空图 NOAA 15 历元：{sky_after}")
    if sky_after == owner_after:
        print("[0] 一致：天空图已画新根数")
        return 0
    print(f"[1] 缺陷复现：天空图仍是 {sky_after}（应为 {owner_after}）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
