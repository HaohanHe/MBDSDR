# SPDX-License-Identifier: MIT
"""生成 web/内核「星点画/不画」集合相等的金标准。

用途
----
锁死一条强断言（用户 2026-11-11 裁定 ④）：
    同一星表、同一时刻、同一极限星等，web 的星点可见性判据与内核
    ``mbdsdr_ai.skyengine.star_catalog`` 的「地平门 + 逐星消光后星等门」
    必须选出**完全相同的星集**。

为什么用 J2000.0 时刻
--------------------
web 的 ``eachStar`` 会先把 HYG 实测自行推进到模拟历元；内核 ``select_for_view``
用星表 J2000 坐标。取 ``jd_ut1 = 2451545.0`` 使自行位移严格为 0，本次比对只
考察「可见性律」，自行漂移由 proper-motion-golden 单独钉扎，避免两件事混在一起。

为什么不含视锥
--------------
视锥/屏幕矩形裁剪是 web 投影层独有的（内核用球面锥，web 用屏幕矩形），
属于「呈现层允许分层」的部分。本金标准只比较投影之前的 ``starVisible`` 判据：
地平门 alt >= -0.6°、mag + extinction(alt) <= L。

复现
----
    python web/product/build/gen_star_visibility_golden.py

输出：``web/product/build/star-visibility-golden.json``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from mbdsdr_ai.celestial_geometry import GroundStation  # noqa: E402
from mbdsdr_ai.skyengine.star_catalog import (  # noqa: E402
    EXTINCTION_K,
    _HORIZON_GATE_DEG,
    _j2000_to_altaz_matrix,
    extinction_delta_mag_array,
    limit_magnitude,
)

PRODUCT = Path(__file__).resolve().parents[1]
CATALOG = PRODUCT / "build" / "sky-catalog.json"
OUT = PRODUCT / "build" / "star-visibility-golden.json"

JD_J2000 = 2451545.0
FOV_DEG = 5.0
BORTLE = 3
# web 随仓星表深度上限（CAT.meta.magMax）；窄视场下内核 limit_magnitude(5°,3)=7.79
# 被 web 夹到 7.5，两边取同一 L 才可比。默认广角 fov=60 时 L=6.5，
# 该仰角带被消光整段压黑（added=0），所以用 7.5 让「画/不画」差异可见。
LIMIT_MAG = 7.5
OLD_GATE_DEG = -1.2
# 朝地平、方位选 32.17°：J2000 时刻该锥内恰有一颗 3.46 等星落在
# alt=-0.97°（旧 -1.2° 门会画、新 -0.6° 门不画），用于画面级计数。
FRAME_AZ = 32.17
FRAME_ALT = 0.0
FRAME_MARGIN_DEG = 1.0


def main() -> None:
    cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    stars = cat["stars"]
    n = len(stars)

    ra_mdeg = np.array([s[0] for s in stars], dtype=np.float64)
    dec_mdeg = np.array([s[1] for s in stars], dtype=np.float64)
    vmag = np.array([s[2] for s in stars], dtype=np.float64) / 100.0

    station = GroundStation(0.0, 0.0, 0.0)   # 中性默认站址（与 web 初始 state 一致）
    M = _j2000_to_altaz_matrix(station, JD_J2000)
    ra = np.radians(ra_mdeg / 1000.0)
    dec = np.radians(dec_mdeg / 1000.0)
    cd = np.cos(dec)
    v_j2000 = np.column_stack((cd * np.sin(ra), np.sin(dec), -cd * np.cos(ra)))
    v_alt = v_j2000 @ M.T
    alt = np.degrees(np.arcsin(np.clip(-v_alt[:, 2], -1.0, 1.0)))

    limit = float(LIMIT_MAG)
    ext = extinction_delta_mag_array(alt, k=EXTINCTION_K)
    eff = vmag + ext

    after = (alt >= _HORIZON_GATE_DEG) & (eff <= limit)
    before = (alt >= OLD_GATE_DEG) & (eff <= limit)
    band_all = (alt > OLD_GATE_DEG) & (alt <= _HORIZON_GATE_DEG)
    added = before & ~after

    # 诚实对照：默认广角 fov=60（内核 L=6.5）时，该仰角带被消光整段压黑，
    # 改门前后的可见星数完全一样。这组数字一并落盘，防止只看 7.5 的差异。
    d_limit = float(limit_magnitude(60.0, BORTLE))
    d_after = (alt >= _HORIZON_GATE_DEG) & (eff <= d_limit)
    d_before = (alt >= OLD_GATE_DEG) & (eff <= d_limit)

    # 「画面」级：同一 fov 锥内、中心 az=32.17° / alt=0°（朝地平）的画/不画数量。
    center = np.array([np.cos(np.radians(FRAME_ALT)) * np.cos(np.radians(FRAME_AZ)),
                       np.cos(np.radians(FRAME_ALT)) * np.sin(np.radians(FRAME_AZ)),
                       np.sin(np.radians(FRAME_ALT))])
    r_deg = min(180.0, 0.5 * FOV_DEG + FRAME_MARGIN_DEG)
    in_cone = (v_alt @ center) >= np.cos(np.radians(r_deg))
    frame_after = int(np.count_nonzero(after & in_cone))
    frame_before = int(np.count_nonzero(before & in_cone))

    payload = {
        "meta": {
            "source": "mbdsdr_ai/skyengine/star_catalog.py: _HORIZON_GATE_DEG / "
                      "extinction_delta_mag_array / limit_magnitude (imported, not re-derived)",
            "generatedBy": "web/product/build/gen_star_visibility_golden.py",
            "catalog": "web/product/build/sky-catalog.json",
            "catalogStarCount": n,
            "jdUt1": JD_J2000,
            "pmNote": "jd=J2000.0 → 自行位移为 0；自行本身由 proper-motion-golden 单独钉扎",
            "station": {"lon": 0.0, "lat": 0.0, "altM": 0.0},
            "fovDeg": FOV_DEG,
            "bortle": BORTLE,
            "limitMag": limit,
            "limitMagReason": "web MAG_CAP=7.5 at narrow fov; kernel limit_magnitude(5,3)=7.79 clipped to 7.5",
            "defaultWideField": {"fovDeg": 60.0, "limitMag": d_limit,
                                 "after": int(np.count_nonzero(d_after)),
                                 "before": int(np.count_nonzero(d_before)),
                                 "added": int(np.count_nonzero(d_before & ~d_after))},
            "extinctionK": EXTINCTION_K,
            "horizonGateDeg": _HORIZON_GATE_DEG,
            "oldHorizonGateDeg": OLD_GATE_DEG,
            "frameCenter": {"az": FRAME_AZ, "alt": FRAME_ALT},
            "frameMarginDeg": FRAME_MARGIN_DEG,
            "band": "alt in (-1.2, -0.6] (full table, no mag gate)",
            "note": "visibleIndices 不含视锥/屏幕裁剪；投影层允许分层，见 parity 文档",
        },
        "bandStarCount": int(np.count_nonzero(band_all)),
        "visibleAfterGateCount": int(np.count_nonzero(after)),
        "visibleBeforeGateCount": int(np.count_nonzero(before)),
        "addedByOldGateCount": int(np.count_nonzero(added)),
        "frameAfterCount": frame_after,
        "frameBeforeCount": frame_before,
        "visibleIndices": np.flatnonzero(after).astype(int).tolist(),
        "addedIndices": np.flatnonzero(added).astype(int).tolist(),
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(
        f"[star-visibility] stars={n} L={limit} band={payload['bandStarCount']} "
        f"after={payload['visibleAfterGateCount']} before={payload['visibleBeforeGateCount']} "
        f"added={payload['addedByOldGateCount']} frame{int(FOV_DEG)}(after/before)="
        f"{frame_after}/{frame_before} (fov60 added={payload['meta']['defaultWideField']['added']}) "
        f"-> {OUT.name} ({OUT.stat().st_size} bytes)"
    )


if __name__ == "__main__":
    main()
