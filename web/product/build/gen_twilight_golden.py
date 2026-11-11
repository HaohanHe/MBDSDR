# -*- coding: utf-8 -*-
"""晨昏天空亮度 → 极限星等压制 跨语言金标准（事实源 = 内核 skyengine/twilight.py）。

前端 14-astro-frame.js 的 skyBrightnessMagArcsec2 / twilightLimitPenaltyMag
逐式镜像内核：
  - mbdsdr_ai/skyengine/twilight.py::sky_brightness_mag_arcsec2(sun_alt)
        分段线性天顶亮度表（V mag/arcsec²），太阳越高越亮（数值越小）；
        alt>=10° 夹到 4.0、alt<=-90° 夹到夜值 21.6；None/NaN/非有限 → 夜值。
  - twilight.py::twilight_limit_penalty_mag(sun_alt)
        = max(0, 21.6 - sky_brightness_mag_arcsec2(sun_alt))
        天空比无月夜亮多少等，极限星等就压低多少等（背景限定探测 1:1）。

桌面 desktop/rf_sky_view.py:4246 已接线：
        limit = limit_magnitude(fov, bortle) - twilight_penalty(sun_alt)
tests/test_sky_realism_batch.py::TestTwilightPenalty 钉扎其不变量。前端
31-celestial.js effectiveLimit 同式合成（intro 淡入后再减惩罚；intro=1 即 base-penalty）。

本脚本直接 import 内核函数算出真值（不重写公式/不抄表），交给
tests/twilight_selftest.js 做分量级 + 合成级紧钉扎（1e-12 等）。任一侧常量/
表值/公式漂移都会立刻报错。合成基线 limit_magnitude 复用 #67 的内核事实源；
前端随仓 HYG 截断较浅（webDepthCap = sky-catalog.json meta.magMax = 7.5），
由 31-celestial.js 在调用点作为 depthCap 传入，本脚本同时给出 effectiveWeb。

太阳高度 None/NaN 不可 JSON 数值化，其 → 夜值 → 惩罚 0 的口径由 selftest
以 JS null/NaN 直接钉扎（与内核 test_penalty_unknown_sun_is_zero 同）。

输出：build/twilight-golden.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import ROOT, REPO  # noqa: E402

sys.path.insert(0, str(REPO))
from mbdsdr_ai.skyengine import twilight as ktw  # noqa: E402
from mbdsdr_ai.skyengine import stars as kstars  # noqa: E402
from mbdsdr_ai.skyengine import star_catalog as kcat  # noqa: E402
from mbdsdr_ai import atmosphere as katm  # noqa: E402

COMPONENT_TOL_MAG = 1e-12
COMPOSITION_TOL_MAG = 1e-12

# 确定性太阳高度网格：表 knots + 密集 0.5° 网格（-90..+30）+ 端点夹取探针 +
# knot 邻域（测分段边界）。无随机、无墙钟、无网络。
_KNOTS = [float(a) for a, _m in ktw._SKY_BRIGHTNESS_TABLE]


def sun_alt_grid() -> list[float]:
    vals: set[float] = set(_KNOTS)
    vals.update(a / 2.0 for a in range(-180, 61))      # -90 .. +30 step 0.5
    vals.update([60.0, 90.0, 120.0, -120.0, -180.0])    # 端点夹取探针
    for k in _KNOTS:                                    # knot 邻域（分段边界两侧）
        vals.add(round(k - 0.001, 6))
        vals.add(round(k + 0.001, 6))
    return sorted(vals)


# 合成网格：广角→窄视场 × Bortle × 天文夜→白天（含日落阈值 -0.833）。
FOVS_COMP = [160.0, 100.0, 60.0, 30.0, 22.0, 10.0, 2.0]
BORTLES_COMP = [3, 5, 7]
SUN_ALTS_COMP = [-18.0, -12.0, -6.0, -0.833, 0.0, 5.0, 30.0]


def web_depth_cap() -> float:
    """前端 MAG_CAP 的真实来源：sky-catalog.json meta.magMax（= CAT.meta.magMax）。"""
    p = ROOT / "build" / "sky-catalog.json"
    if not p.is_file():
        raise RuntimeError(f"缺 {p}（先跑 build/sync_sky_catalog.py）")
    meta = json.loads(p.read_text(encoding="utf-8"))["meta"]
    return float(meta["magMax"])


def main() -> None:
    cap = web_depth_cap()
    alts = sun_alt_grid()

    components = []
    for alt in alts:
        components.append({
            "sunAlt": alt,
            "skyBrightness": float(ktw.sky_brightness_mag_arcsec2(alt)),
            "penalty": float(ktw.twilight_limit_penalty_mag(alt)),
        })

    # 天空背景亮度因子（0=夜, 1=白昼）：前端渲染层用它调底色/星点压暗。
    # 内核 atmosphere.sky_brightness_factor 是唯一事实源（与 twilight 表是两套
    # 不同用途的亮度量，别混用：一个是 V mag/arcsec²，一个是 0..1 的显示因子）。
    day_factors = [{"sunAlt": alt,
                    "factor": float(katm.sky_brightness_factor(alt))}
                   for alt in alts]
    day_anchors = [
        {"sunAlt": -1.0, "factor": float(katm.sky_brightness_factor(-1.0))},
        {"sunAlt": -6.0, "factor": float(katm.sky_brightness_factor(-6.0))},
        {"sunAlt": 0.0, "factor": float(katm.sky_brightness_factor(0.0))},
    ]

    compositions = []
    for fov in FOVS_COMP:
        for b in BORTLES_COMP:
            nel = float(kstars.naked_eye_limit_mag(b))
            dg = float(kcat.detail_gain_mag(fov))
            base_kernel = float(kcat.limit_magnitude(fov, b))   # 内核 depthCap=9.0
            base_web = min(cap, nel + dg)                       # 前端 depthCap=7.5
            for alt in SUN_ALTS_COMP:
                pen = float(ktw.twilight_limit_penalty_mag(alt))
                compositions.append({
                    "fov": fov, "bortle": b, "sunAlt": alt,
                    "baseKernel": base_kernel, "baseWeb": base_web,
                    "penalty": pen,
                    "effectiveKernel": base_kernel - pen,
                    "effectiveWeb": base_web - pen,
                })

    meta = {
        "source": "mbdsdr_ai/skyengine/twilight.py::sky_brightness_mag_arcsec2 / "
                  "twilight_limit_penalty_mag (kernel fact source, MIT repo-owned; "
                  "imported not re-derived, table read from kernel not copied)",
        "desktopWiring": "desktop/rf_sky_view.py:4246 limit = limit_magnitude(fov,bortle) "
                         "- twilight_limit_penalty_mag(sun_alt); pinned by "
                         "tests/test_sky_realism_batch.py::TestTwilightPenalty",
        "kernelConstants": {
            "SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2": ktw.SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2,
            "SKY_BRIGHTNESS_TABLE": [[float(a), float(m)] for a, m in ktw._SKY_BRIGHTNESS_TABLE],
            "SKY_BRIGHT_BREAKS": [[float(a), float(b)] for a, b in katm._SKY_BRIGHT_BREAKS],
        },
        "dayFactorSource": "mbdsdr_ai/atmosphere.py::sky_brightness_factor "
                           "(_SKY_BRIGHT_BREAKS piecewise linear, imported not re-derived)",
        "dayFactorAnchors": day_anchors,
        "dayFactorTol": 1e-6,
        "webDepthCap": cap,
        "webDepthCapSource": "build/sky-catalog.json meta.magMax "
                             "(= web MAG_CAP = Number(CAT.meta.magMax || 7.5))",
        "formulas": {
            "skyBrightnessMagArcsec2": "None/NaN/non-finite -> 21.6; alt>=table[0].alt(10) -> "
                                       "table[0].mag(4.0); alt<=table[-1].alt(-90) -> 21.6; "
                                       "else descending-alt segment a_lo<=alt<=a_hi: "
                                       "t=(alt-a_lo)/(a_hi-a_lo), m_lo + t*(m_hi-m_lo)",
            "twilightLimitPenaltyMag": "max(0.0, 21.6 - sky_brightness_mag_arcsec2(sun_alt)); "
                                       "<=-18 (astronomical night) -> exactly 0",
            "composition": "effective = base - penalty(sun_alt) [desktop rf_sky_view.py:4246]; "
                           "baseKernel = limit_magnitude(fov,bortle) depthCap 9.0; "
                           "baseWeb = min(webDepthCap, naked_eye+detail_gain) depthCap 7.5; "
                           "web effectiveLimit pins at intro=1 (intro fade is web presentation)",
        },
        "componentTolMag": COMPONENT_TOL_MAG,
        "compositionTolMag": COMPOSITION_TOL_MAG,
        "sunAltSamples": alts,
        "compositionFovs": FOVS_COMP,
        "compositionBortles": BORTLES_COMP,
        "compositionSunAlts": SUN_ALTS_COMP,
        "componentCaseCount": len(components),
        "dayFactorCaseCount": len(day_factors),
        "compositionCaseCount": len(compositions),
        "caseCount": len(components) + len(day_factors) + len(compositions),
        "notVerified": [
            "sky-brightness table is an order-of-magnitude approximation of published twilight "
            "curves (Krisciunas & Schaefer 1991 PASP 103,1033; Patat et al. 2006 A&A 455,385), "
            "NOT radiometrically calibrated; aerosol/ozone/latitude/azimuth give ~+/-1 mag",
            "zenith-equivalent brightness only: NO azimuth dependence (sunward sky is much "
            "brighter than anti-sunward), NO moonlight term",
            "display-layer only (draw/not-draw, how-dim); never used for pointing / orbit "
            "determination / pass conclusions",
            "sun_alt input on the web comes from a low-precision analytic solar position "
            "(Meeus simplified, ~+/-0.5 deg, not a precise ephemeris); the penalty inherits "
            "that input uncertainty",
            "web depthCap=7.5 (HYG cutoff) shallower than kernel CATALOG_DEPTH_MAG=9.0; "
            "applied at the 31-celestial call site, not in the kernel mirror",
            "intro dark-adaptation fade is a web presentation layer; composition pinned at intro=1",
            "full sky-brightness photometric model (moon phase / actual calibrated sky surface "
            "brightness) still NOT implemented (kernel limitations-register L24)",
        ],
    }
    out = ROOT / "build" / "twilight-golden.json"
    out.write_text(json.dumps({"meta": meta, "components": components,
                               "dayFactors": day_factors,
                               "compositions": compositions},
                              ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print(f"[twilight-golden] {len(components)} sun-alt components + "
          f"{len(day_factors)} day factors + "
          f"{len(compositions)} compositions = {meta['caseCount']} cases "
          f"-> {out.name} ({out.stat().st_size} bytes)")
    print(f"[twilight-golden] night {ktw.SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2}; "
          f"brightness(0)={ktw.sky_brightness_mag_arcsec2(0.0)}, "
          f"brightness(-6)={ktw.sky_brightness_mag_arcsec2(-6.0)}, "
          f"brightness(-18)={ktw.sky_brightness_mag_arcsec2(-18.0)}; "
          f"penalty(-6)={ktw.twilight_limit_penalty_mag(-6.0)}, "
          f"penalty(-18)={ktw.twilight_limit_penalty_mag(-18.0)}, "
          f"penalty(0)={ktw.twilight_limit_penalty_mag(0.0)}; web depth {cap}")


if __name__ == "__main__":
    main()