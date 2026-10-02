#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Phase14 P3 — 活动过境预测与多普勒对接（录入位）
================================================================

活动：2026-10-08~10「航天七十载·星火传未来」业余无线电图像通联
卫星：JAMX01（静安梦想星）、ASRTU-1（阿斯图友谊号）
目的：录入官方发布的 TLE + 下行频率后，一条命令算出本机未来过境时刻表与
      每次过境的多普勒频移范围，供 SDR 对准与 NCO 多普勒补偿使用。

复用现有零件（不重写轨道传播）：
  * mbdsdr_ai.sat_passes.GroundStation / predict_passes / compute_doppler_curve
  * mbdsdr_ai.spacetime_predict.predict_passes_report（时间源显式 + TLE 新鲜度三态）
  * mbdsdr_ai.doppler_compensation.remove_doppler_shift（NCO 补偿，见接收手册）

红线（与全仓一致）：
  * 频率 / TLE 一律**录入位留空**，待主办方官方发布后填入；本脚本**绝不猜测、
    绝不硬编码**任何频率或 TLE。
  * 未录入 TLE 时，本脚本如实打印「未录入，跳过」并退出，**不伪造过境**。
  * 云内无硬件：本脚本只做轨道几何计算，不读串口、不联网收信号。
  * MIT。

用法：
  1) 把下面 CONFIG 区的 GROUND_STATION 与 EVENT_SATELLITES 填好（或由官方发布后填）；
  2) python3 docs/learn/phase14/pass_predict.py
  3) 加 --json 出机器可读报告（供接收手册/邮件清单抄录过境 UTC 与多普勒）。

注意：本脚本依赖 skyfield/sgp4（requirements.txt 已含）；离线可用（时间系统已随包
内置），但 TLE 需手动粘贴进 tle_lines，不会替你去网上找 JAMX01/ASRTU-1。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
# docs/learn/phase14 -> parents[0]=learn [1]=docs [2]=仓库根
_ROOT = _HERE.parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mbdsdr_ai.sat_passes import (  # noqa: E402
    GroundStation,
    predict_passes,
    compute_doppler_curve,
)
from mbdsdr_ai.spacetime_predict import predict_passes_report  # noqa: E402

# ==========================================================================
#                              录 入 位
#  —— 以下字段待主办方官方发布后填入；发布前保持空 / None，禁止猜测。
# ==========================================================================

# 地面站（操作者本机位置）。填本机经纬度即可，海拔可留 0。
GROUND_STATION: Dict[str, Any] = {
    "lat_deg": None,      # TODO(官方发布后/本机)：本机纬度，如 43.81
    "lon_deg": None,      # TODO(本机)：本机经度，如 125.32
    "alt_m":   0.0,       # 海拔 m（平原环境可保持 0）
}

# 活动卫星表。downlink_hz / tle_lines 全部留空 —— 待官方发布填入，**不猜测**。
EVENT_SATELLITES: List[Dict[str, Any]] = [
    {
        "name": "JAMX01 (静安梦想星)",
        "norad_catnr": None,     # 主办方发布 NORAD 编号后填（整数）；无则手动填 tle_lines
        "tle_lines": [],         # TODO：粘贴官方 TLE 三行，如 ["JAMX01", "1 ......", "2 ......"]
        "downlink_hz": None,     # TODO：官方下行频率 Hz（SSTV/SSDV 共用或分列，以官方为准）
    },
    {
        "name": "ASRTU-1 (阿斯图友谊号)",
        "norad_catnr": None,
        "tle_lines": [],         # TODO：粘贴官方 TLE 三行
        "downlink_hz": None,     # TODO：官方下行频率 Hz
    },
]

PREDICT_HOURS = 72.0           # 向前预测小时数（活动 10-08~10，建议至少覆盖 72h）
MIN_ELEVATION_DEG = 10.0       # 最低仰角阈值（度）；卫星太低信号差，建议 >=10°
# ==========================================================================


def _build_ground_station() -> Tuple[Optional[GroundStation], Optional[str]]:
    """解析地面站；经纬度未填则返回 (None, 原因)，绝不猜默认站。"""
    lat = GROUND_STATION.get("lat_deg")
    lon = GROUND_STATION.get("lon_deg")
    if lat is None or lon is None:
        return None, "GROUND_STATION 经纬度未填（lat_deg/lon_deg），无法预测。"
    return GroundStation(lat_deg=float(lat), lon_deg=float(lon),
                         alt_m=float(GROUND_STATION.get("alt_m", 0.0))), None


def _resolve_tle(sat: Dict[str, Any]) -> Tuple[Optional[List[str]], str]:
    """拿到该卫星的 TLE 文本；优先手动粘贴的 tle_lines，其次按 catnr 在线拉取。

    都没有 → 返回 (None, 原因)，调用方据此诚实跳过，不伪造过境。
    """
    tle = [l for l in (sat.get("tle_lines") or []) if l and str(l).strip()]
    if len(tle) >= 2:
        return tle, "user-pasted"
    catnr = sat.get("norad_catnr")
    if catnr:
        # 在线拉取（celestrak）；失败就老实告诉操作者手动粘贴，不伪造。
        try:
            from mbdsdr_ai.orbit import fetch_tle  # 延迟导入，离线时不强制联网
            l1, l2 = fetch_tle(int(catnr))
            return [sat["name"], l1, l2], f"celestrak(catnr={catnr})"
        except Exception as e:  # noqa: BLE001
            return None, (f"norad_catnr={catnr} 在线拉 TLE 失败（{type(e).__name__}）；"
                          f"请手动把官方 TLE 粘进 tle_lines。")
    return None, "未录入 TLE（norad_catnr / tle_lines 均空）——待官方发布填入。"


def _analyze_satellite(sat: Dict[str, Any], gs: GroundStation) -> Dict[str, Any]:
    """对一颗卫星：预测过境 +（若有频率）多普勒曲线。返回机器可读 dict。"""
    name = sat["name"]
    tle, src = _resolve_tle(sat)
    if tle is None:
        return {"satellite": name, "ready": False, "reason": src, "passes": []}

    rep = predict_passes_report(tle, gs, hours=PREDICT_HOURS,
                                min_alt=MIN_ELEVATION_DEG)
    out: Dict[str, Any] = {
        "satellite": name,
        "ready": True,
        "tle_source": src,
        "time_source": rep["time_source"],
        "prediction_start_utc": rep["prediction_start_utc"],
        "tle_freshness": rep["tle"],
        "n_passes": rep["n_passes"],
        "passes": rep["passes"],
    }

    freq = sat.get("downlink_hz")
    if freq:
        # 有频率才补多普勒曲线；没有就只给时刻表（频率也是录入位）。
        full = predict_passes(tle, gs, hours=PREDICT_HOURS,
                              min_alt=MIN_ELEVATION_DEG)
        dop_rows = []
        for p, pass_obj in zip(rep["passes"], full):
            curve = compute_doppler_curve(tle, gs, pass_obj, float(freq), num_points=40)
            if curve:
                ds = [pt.doppler_hz for pt in curve]
                dop_rows.append({
                    "rise_utc": p["rise_utc"],
                    "peak_abs_doppler_hz": round(max(abs(d) for d in ds), 0),
                    "doppler_aos_hz": round(ds[0], 0),
                    "doppler_los_hz": round(ds[-1], 0),
                    "doppler_min_hz": round(min(ds), 0),
                    "doppler_max_hz": round(max(ds), 0),
                })
        out["downlink_hz"] = float(freq)
        out["doppler"] = dop_rows
    else:
        out["doppler_note"] = "downlink_hz 未填：只出过境时刻表，未算多普勒（待官方发布）。"
    return out


def _print_human(results: List[Dict[str, Any]]) -> None:
    print("=" * 72)
    print("MBDSDR Phase14 活动过境预测 / 多普勒对接")
    print("=" * 72)
    for r in results:
        print(f"\n● {r['satellite']}")
        if not r["ready"]:
            print(f"  [空态] {r['reason']}")
            continue
        fr = r["tle_freshness"]
        print(f"  TLE 来源={r['tle_source']}  新鲜度={fr['status']} "
              f"(epoch={fr['epoch_utc']}, age={fr['age_days']}d)  "
              f"时间源={r['time_source']}")
        if fr["status"] == "stale":
            print("  ⚠ TLE 偏旧，过境方位/多普勒可能漂移，建议用官方最新 TLE。")
        print(f"  未来 {PREDICT_HOURS:.0f}h 内仰角>={MIN_ELEVATION_DEG:.0f}° 过境 {r['n_passes']} 次：")
        for i, p in enumerate(r["passes"]):
            line = (f"   #{i+1} AOS {p['rise_utc']} az={p['rise_az_deg']:.0f}° | "
                    f"中天 {p['max_alt_time_utc']} alt={p['max_alt_deg']:.1f}° | "
                    f"LOS {p['set_utc']} az={p['set_az_deg']:.0f}° | "
                    f"dur={p['duration_s']/60:.1f}min")
            print(line)
        dop = r.get("doppler")
        if dop:
            f0 = r["downlink_hz"] / 1e6
            print(f"  多普勒（下行 {f0:.3f} MHz；AOS=接近为+，LOS=远离为-）：")
            for i, d in enumerate(dop):
                print(f"   #{i+1} {d['rise_utc']} 峰|fd|={d['peak_abs_doppler_hz']:.0f} Hz "
                      f"(AOS {d['doppler_aos_hz']:+.0f} → LOS {d['doppler_los_hz']:+.0f}, "
                      f"范围 {d['doppler_min_hz']:+.0f}~{d['doppler_max_hz']:+.0f})")
        else:
            print(f"  [空态] {r.get('doppler_note','')}")
    print("\n" + "=" * 72)
    ready = [r for r in results if r["ready"]]
    if not ready:
        print("提示：尚未录入任何活动卫星的 TLE/频率（录入位留空）。")
        print("      官方发布后填入本脚本顶部 CONFIG 区的 tle_lines / downlink_hz 再跑。")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Phase14 活动过境预测与多普勒对接（录入位）")
    ap.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    args = ap.parse_args(argv)

    gs, gs_reason = _build_ground_station()
    if gs is None:
        msg = {"ready": False, "reason": gs_reason, "satellites": []}
        if args.json:
            print(json.dumps(msg, ensure_ascii=False, indent=2))
        else:
            print(f"[空态] {gs_reason}\n请在脚本顶部 GROUND_STATION 填入本机经纬度。")
        return 0

    results = [_analyze_satellite(sat, gs) for sat in EVENT_SATELLITES]
    if args.json:
        print(json.dumps({"ground_station": GROUND_STATION,
                          "predict_hours": PREDICT_HOURS,
                          "min_elevation_deg": MIN_ELEVATION_DEG,
                          "satellites": results},
                         ensure_ascii=False, indent=2))
    else:
        _print_human(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
