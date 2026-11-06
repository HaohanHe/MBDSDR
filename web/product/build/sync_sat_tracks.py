# -*- coding: utf-8 -*-
"""卫星天球轨迹事实源（真实 SGP4，不是示意折线）。

事实源：`mbdsdr_ai/sat_tracker.py::_BUILTIN_TLES`（Celestrak 快照，仓库内置）。
用 `sgp4` 把 TLE 传播成 TEME 地心位置，再按仓库 `celestial_geometry.py` 的
IAU2006 岁差矩阵做**逆变换**，输出 J2000 (ICRS) 方向的赤经赤纬。

**为什么必须转 J2000**：前端 14-astro-frame.js / 31-celestial.js 现在对**所有**
天球方向统一施加 `Rf · Qᵀ N P Q`（岁差 + 章动 + LST），恒星、星座、DSO 都是
J2000。TEME 本身是「真赤道、平春分点」的 of-date 系，若原样喂进去会被重复
施加一次岁差（2026 年约 0.36°，窄视场下肉眼可见）。因此这里先做
`v_J2000 = Qᵀ Pᵀ Q · v_TEME`，前端再乘 `Qᵀ N P Q`，净效果只剩 `Qᵀ N Q`，
即把平春分点修正到真春分点——正是 TEME 定义所缺的那一步。

**科学口径（not_verified，前端必须如实标注）**：
- TEME→J2000 用的是逆岁差近似，**未做严格 TEME→ITRF 框架变换**
  （极移、ΔUT1、真赤道/平春分点的完整拆分）；前端章动是仓库的简化 6 主项
  模型，不是完整 IAU2000A/B 77 项；
- 是**地心方向**，未做站址视差（topocentric）修正；LEO 近地时与地面站
  实测方位可差数十度，因此绝不据此给升落/过境结论；
- TLE 是静态快照，外推到构建日之后误差随时间增大；
- 站址 `trusted=false`，本产品不提供可信过境预报。

抽不到 TLE / sgp4 缺失 / 坐标链不可用 / 某颗星传播报错时**拒绝伪造**，
直接失败或不产出该星。
输出：`build/sat-tracks.json` 与 `src/13-sat-tracks.js`。
"""
from __future__ import annotations

import ast
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import ROOT, REPO  # noqa: E402

SRC, BUILD = ROOT / "src", ROOT / "build"
STEP_SEC = 90
HALF_SEC = 6 * 3600
COUNT = 2 * HALF_SEC // STEP_SEC + 1
HALF_STEPS = HALF_SEC // STEP_SEC
JD_UNIX_EPOCH = 2440587.5

try:
    import sgp4
    from sgp4.api import Satrec
except ImportError as exc:
    raise SystemExit(f"[sat-tracks] sgp4 缺失，拒绝伪造轨迹：{exc}")

# 仓库天文坐标链：逆岁差用它的 precession_matrix，不另写几何
sys.path.insert(0, str(REPO))
try:
    import numpy as np
    from mbdsdr_ai import celestial_geometry as cg
except ImportError as exc:
    raise SystemExit(f"[sat-tracks] 仓库 celestial_geometry 不可用，拒绝伪造：{exc}")

# 前端标准 ICRS 基 <-> 仓库赤道基（见 14-astro-frame.js 的口径说明）
Q = np.array([[0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [-1.0, 0.0, 0.0]])
QT = Q.T
_PINV_CACHE: dict[float, "np.ndarray"] = {}


def precession_inverse_front(jd_tt: float) -> "np.ndarray":
    """前端标准基下的逆岁差 (Qᵀ P Q)⁻¹ = Qᵀ Pᵀ Q。"""
    key = round(jd_tt, 6)
    m = _PINV_CACHE.get(key)
    if m is None:
        m = QT @ cg.precession_matrix(jd_tt).T @ Q
        _PINV_CACHE[key] = m
    return m


def literal_assign(tree: ast.AST, name: str):
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == name:
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return ast.literal_eval(node.value)
    return None


src = (REPO / "mbdsdr_ai/sat_tracker.py").read_text(encoding="utf-8")
tree = ast.parse(src, filename="mbdsdr_ai/sat_tracker.py")
tles = literal_assign(tree, "_BUILTIN_TLES") or []
tle_date = literal_assign(tree, "BUILTIN_TLE_DATE") or ""
if not tles:
    raise SystemExit("[sat-tracks] 未抽到 _BUILTIN_TLES，拒绝生成")

# 与 31-celestial.js 的 TIME.baseUnix 完全同规则：当日 UTC 22:00
_now = datetime.now(timezone.utc)
t0 = _now.replace(hour=22, minute=0, second=0, microsecond=0).timestamp()


def ra_dec_j2000(sat, t_unix):
    """TEME 地心位置 -> J2000 ICRS 方向的 (RA, Dec)，单位度。"""
    jd = t_unix / 86400.0 + JD_UNIX_EPOCH
    jd_int, fr = math.floor(jd), jd - math.floor(jd)
    err, r, _v = sat.sgp4(jd_int, fr)
    if err:
        return None
    radius = math.sqrt(r[0] ** 2 + r[1] ** 2 + r[2] ** 2)
    if not math.isfinite(radius) or radius <= 0:
        return None
    v_teme = np.array([r[0] / radius, r[1] / radius, r[2] / radius])
    v_j2000 = precession_inverse_front(jd) @ v_teme
    n = float(np.linalg.norm(v_j2000))
    if not math.isfinite(n) or n <= 0:
        return None
    ra = math.degrees(math.atan2(v_j2000[1], v_j2000[0])) % 360.0
    dec = math.degrees(math.asin(max(-1.0, min(1.0, v_j2000[2] / n))))
    return ra, dec


tracks: dict[str, list[int]] = {}
skipped: list[str] = []
for entry in tles:
    if not isinstance(entry, list) or len(entry) < 3:
        skipped.append("bad TLE entry shape")
        continue
    name, l1, l2 = str(entry[0]), str(entry[1]), str(entry[2])
    norad = l1[2:7].strip()
    try:
        sat = Satrec.twoline2rv(l1, l2)
    except Exception as exc:  # 具体异常由 sgp4 决定，转成诚实跳过
        skipped.append(f"{name}: twoline2rv {type(exc).__name__}: {exc}")
        continue
    flat: list[int] = []
    for k in range(COUNT):
        rd = ra_dec_j2000(sat, t0 - HALF_SEC + k * STEP_SEC)
        if rd is None:
            flat = []  # 放弃整条，不拿前一个值冒充
            break
        flat.append(round(rd[0] * 100))
        flat.append(round(rd[1] * 100))
    if len(flat) != COUNT * 2:
        skipped.append(f"{name} ({norad}): SGP4 / 坐标链失败，未产出轨迹")
        continue
    tracks[norad] = flat

if not tracks:
    raise SystemExit("[sat-tracks] 没有任何可用轨迹，拒绝生成空壳事实源")

meta = {
    "source": "mbdsdr_ai/sat_tracker.py::_BUILTIN_TLES (Celestrak snapshot) + sgp4 "
              "+ mbdsdr_ai/celestial_geometry.py::precession_matrix",
    "sgp4Version": getattr(sgp4, "__version__", "unknown"),
    "tleDate": tle_date,
    "epochUnix": t0,
    "stepSec": STEP_SEC,
    "count": COUNT,
    "halfSteps": HALF_STEPS,
    "halfSpanSec": HALF_SEC,
    "frame": "J2000 ICRS geocentric RA/Dec (degrees, x100 integer encoded)",
    "transform": "TEME -> J2000 via Q^T P^T Q (inverse IAU2006 precession); "
                 "frontend applies Q^T N P Q, net = Q^T N Q (mean->true equinox)",
    "satCount": len(tracks),
    "skipped": skipped,
    "notVerified": [
        "TEME→J2000 为逆岁差近似，未做严格 TEME→ITRF 框架变换（极移 / ΔUT1 / 真赤道拆分）",
        "前端章动是仓库简化 6 主项模型，非完整 IAU2000A/B 77 项，不宣称 mas 精度",
        "地心方向，未做站址视差 (topocentric) 修正；LEO 近地时方位误差可达数十度",
        "TLE 为静态快照，外推误差随时间增大，不用于精确过境",
        "站址 trusted=false，不提供升落/过境结论",
    ],
}

payload = json.dumps({"meta": meta, "tracks": tracks}, ensure_ascii=False,
                     separators=(",", ":"))
BUILD.mkdir(parents=True, exist_ok=True)
SRC.mkdir(parents=True, exist_ok=True)
(BUILD / "sat-tracks.json").write_text(
    json.dumps({"meta": meta, "tracks": tracks}, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8")
(SRC / "13-sat-tracks.js").write_text(
    "/* 由 build/sync_sat_tracks.py 生成，勿手改。真实 SGP4 + 逆岁差 -> J2000。 */\n"
    "(function (g) {\n  \"use strict\";\n"
    "  const MB = g.MBDSDR = g.MBDSDR || {};\n"
    "  MB.SATTRACKS = " + payload + ";\n"
    "})(typeof window !== \"undefined\" ? window : globalThis);\n",
    encoding="utf-8")

print(f"[sat-tracks] {len(tracks)} sats x {COUNT} samples @ {STEP_SEC}s, "
      f"TLE {tle_date}, sgp4 {meta['sgp4Version']}, frame {meta['frame']}, "
      f"js {(SRC / '13-sat-tracks.js').stat().st_size} bytes"
      + (f", skipped={skipped}" if skipped else ""))
