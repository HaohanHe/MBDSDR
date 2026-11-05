# -*- coding: utf-8 -*-
"""卫星天球轨迹事实源（真实 SGP4，不是示意折线）。

事实源：`mbdsdr_ai/sat_tracker.py::_BUILTIN_TLES`（Celestrak 快照，仓库内置）。
用 `sgp4` 把 TLE 传播成 TEME 地心位置，再取赤经赤纬，按固定步长采样。

**科学口径（not_verified，前端必须如实标注）**：
- TEME ≈ 瞬时真赤道，这里直接当 J2000 用，**未做岁差/章动/自行链**；
- 是**地心方向**，未做站址视差（topocentric）修正；LEO 近地时与地面站
  实测方位可差数十度，因此绝不据此给升落/过境结论；
- TLE 是静态快照，外推到构建日之后误差随时间增大；
- 站址 `trusted=false`，本产品不提供可信过境预报。

抽不到 TLE / sgp4 缺失 / 某颗星传播报错时**拒绝伪造**，直接失败或不产出该星。
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


def ra_dec(sat, t_unix):
    x = t_unix / 86400.0 + JD_UNIX_EPOCH
    jd = math.floor(x)
    err, r, _v = sat.sgp4(jd, x - jd)
    if err:
        return None
    radius = math.sqrt(r[0] ** 2 + r[1] ** 2 + r[2] ** 2)
    if not math.isfinite(radius) or radius <= 0:
        return None
    ra = math.degrees(math.atan2(r[1], r[0])) % 360.0
    dec = math.degrees(math.asin(max(-1.0, min(1.0, r[2] / radius))))
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
        rd = ra_dec(sat, t0 - HALF_SEC + k * STEP_SEC)
        if rd is None:
            flat = []  # 放弃整条，不拿前一个值冒充
            break
        flat.append(round(rd[0] * 100))
        flat.append(round(rd[1] * 100))
    if len(flat) != COUNT * 2:
        skipped.append(f"{name} ({norad}): SGP4 传播失败，未产出轨迹")
        continue
    tracks[norad] = flat

if not tracks:
    raise SystemExit("[sat-tracks] 没有任何可用轨迹，拒绝生成空壳事实源")

meta = {
    "source": "mbdsdr_ai/sat_tracker.py::_BUILTIN_TLES (Celestrak snapshot) + sgp4",
    "sgp4Version": getattr(sgp4, "__version__", "unknown"),
    "tleDate": tle_date,
    "epochUnix": t0,
    "stepSec": STEP_SEC,
    "count": COUNT,
    "halfSteps": HALF_STEPS,
    "halfSpanSec": HALF_SEC,
    "frame": "TEME geocentric RA/Dec (degrees, ×100 integer encoded)",
    "satCount": len(tracks),
    "skipped": skipped,
    "notVerified": [
        "TEME 直接近似 J2000，未做岁差/章动/自行链",
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
    "/* 由 build/sync_sat_tracks.py 生成，勿手改。真实 SGP4 传播，非示意折线。 */\n"
    "(function (g) {\n  \"use strict\";\n"
    "  const MB = g.MBDSDR = g.MBDSDR || {};\n"
    "  MB.SATTRACKS = " + payload + ";\n"
    "})(typeof window !== \"undefined\" ? window : globalThis);\n",
    encoding="utf-8")

print(f"[sat-tracks] {len(tracks)} sats × {COUNT} samples @ {STEP_SEC}s, "
      f"TLE {tle_date}, sgp4 {meta['sgp4Version']}, "
      f"js {(SRC / '13-sat-tracks.js').stat().st_size} bytes"
      + (f", skipped={skipped}" if skipped else ""))
