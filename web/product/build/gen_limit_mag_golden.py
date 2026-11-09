# -*- coding: utf-8 -*-
"""极限星等跨语言金标准（事实源 = 内核 skyengine，唯一权威，非自研）。

前端 14-astro-frame.js 的 nakedEyeLimitMag / detailGainMag / limitMagnitude
逐式镜像内核：
  - mbdsdr_ai/skyengine/stars.py::naked_eye_limit_mag(bortle)
        = 6.5 - 0.35·max(0, int(bortle)-3)   （Stellarium StelSkyDrawer 同语义）
  - mbdsdr_ai/skyengine/star_catalog.py::detail_gain_mag(fov)
        = clamp(0.5·log2(30/fov), 0, 1.5)    （fov>=30 → 0；渲染 LOD 经验式，非光度学）
  - star_catalog.py::limit_magnitude(fov, bortle)
        = min(CATALOG_DEPTH_MAG=9.0, naked_eye + detail_gain)

本脚本直接 import 内核函数算出真值（不重写公式），交给
tests/limit_mag_selftest.js 做分量级 + 合成级紧钉扎（1e-12 等）。
任一侧常量/公式漂移都会立刻报错。

前端随仓 HYG 截断较浅（MAG_CAP = sky-catalog.json meta.magMax = 7.5），
由 31-celestial.js 在调用点作为 depthCap 传入；本脚本同时给出
limitWeb = min(webDepthCap, naked_eye + detail_gain) 供集成层钉扎。
webDepthCap 读自 build/sky-catalog.json（前端 MAG_CAP 的真实来源），
绝不手填——星表深度变了金标准随之变。

输出：build/limit-mag-golden.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import ROOT, REPO  # noqa: E402

sys.path.insert(0, str(REPO))
from mbdsdr_ai.skyengine import stars as kstars  # noqa: E402
from mbdsdr_ai.skyengine import star_catalog as kcat  # noqa: E402

# 确定性网格：覆盖广角（增益 0）/ 30° 边界 / 窄视场 LOD / 封顶区 / 极小 fov 地板，
# 以及 Bortle 1–9 / 越界 / 分数（int 向零取整）。无随机、无墙钟、无网络。
FOVS = [160.0, 120.0, 100.0, 88.0, 60.0, 45.0, 30.0, 29.9, 22.0, 15.0,
        10.0, 5.0, 2.8, 2.0, 1.5, 1.0, 0.5, 1e-6, 0.0]
BORTLES = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, -1, 3.7, 5.9]
COMPONENT_TOL_MAG = 1e-12
COMPOSITION_TOL_MAG = 1e-12


def web_depth_cap() -> float:
    """前端 MAG_CAP 的真实来源：sky-catalog.json meta.magMax（= CAT.meta.magMax）。"""
    p = ROOT / "build" / "sky-catalog.json"
    if not p.is_file():
        raise RuntimeError(f"缺 {p}（先跑 build/sync_sky_catalog.py）")
    meta = json.loads(p.read_text(encoding="utf-8"))["meta"]
    return float(meta["magMax"])


def main() -> None:
    cap = web_depth_cap()
    cases = []
    for fov in FOVS:
        for b in BORTLES:
            nel = float(kstars.naked_eye_limit_mag(b))
            dg = float(kcat.detail_gain_mag(fov))
            ld = float(kcat.limit_magnitude(fov, b))      # 内核默认 depthCap=9.0
            lw = min(cap, nel + dg)                        # 前端 depthCap=7.5
            cases.append({
                "fov": fov, "bortle": b,
                "nakedEye": nel, "detailGain": dg,
                "limitDefault": ld, "limitWeb": lw,
            })

    meta = {
        "source": "mbdsdr_ai/skyengine/stars.py::naked_eye_limit_mag + "
                  "star_catalog.py::detail_gain_mag / limit_magnitude "
                  "(kernel fact source, MIT repo-owned; imported not re-derived)",
        "kernelConstants": {
            "DEFAULT_BORTLE": kstars.DEFAULT_BORTLE,
            "NAKED_EYE_LIMIT_SCALE": kstars.NAKED_EYE_LIMIT_SCALE,
            "NAKED_EYE_BORTLE_SLOPE": kstars.NAKED_EYE_BORTLE_SLOPE,
            "DETAIL_REF_FOV_DEG": kcat.DETAIL_REF_FOV_DEG,
            "MAX_DETAIL_GAIN_MAG": kcat.MAX_DETAIL_GAIN_MAG,
            "CATALOG_DEPTH_MAG": kcat.CATALOG_DEPTH_MAG,
        },
        "webDepthCap": cap,
        "webDepthCapSource": "build/sky-catalog.json meta.magMax "
                             "(= web MAG_CAP = Number(CAT.meta.magMax || 7.5))",
        "formulas": {
            "nakedEyeLimitMag": "6.5 - 0.35*max(0, int(bortle)-3) "
                                "[Stellarium StelSkyDrawer semantics; int truncates toward zero]",
            "detailGainMag": "fov=max(1e-6,fov); fov>=30 -> 0; "
                             "else clamp(0.5*log2(30/fov), 0, 1.5) [render LOD, NOT photometry]",
            "limitMagnitude": "min(depthCap, nakedEyeLimitMag(bortle) + detailGainMag(fov)); "
                              "kernel default depthCap = CATALOG_DEPTH_MAG = 9.0",
        },
        "componentTolMag": COMPONENT_TOL_MAG,
        "compositionTolMag": COMPOSITION_TOL_MAG,
        "fovSamples": FOVS,
        "bortleSamples": BORTLES,
        "caseCount": len(cases),
        "notVerified": [
            "detail_gain_mag is a bounded monotonic render-LOD empirical rule, "
            "NOT a photometric model and NOT a telescope-aperture gain",
            "full sky-brightness -> limiting-magnitude photometric model "
            "(moon phase / twilight / actual sky surface brightness) is NOT implemented "
            "(kernel limitations-register L24)",
            "web depthCap=7.5 (HYG catalog cutoff) is shallower than kernel "
            "CATALOG_DEPTH_MAG=9.0; applied at the 31-celestial call site, not in the kernel mirror",
            "intro dark-adaptation fade (effectiveLimit = 2 + (L-2)*intro) is a web "
            "presentation layer, not part of the kernel limiting magnitude",
        ],
    }
    out = ROOT / "build" / "limit-mag-golden.json"
    out.write_text(json.dumps({"meta": meta, "cases": cases},
                              ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print(f"[limit-mag-golden] {len(FOVS)} fov × {len(BORTLES)} bortle = "
          f"{len(cases)} cases -> {out.name} ({out.stat().st_size} bytes)")
    print(f"[limit-mag-golden] kernel depth {kcat.CATALOG_DEPTH_MAG} / web depth {cap}; "
          f"naked_eye(3)={kstars.naked_eye_limit_mag(3)}, "
          f"naked_eye(9)={kstars.naked_eye_limit_mag(9)}, "
          f"detail_gain(15)={kcat.detail_gain_mag(15)}, "
          f"detail_gain(60)={kcat.detail_gain_mag(60)}")


if __name__ == "__main__":
    main()