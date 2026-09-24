#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实验1：泛在 PNT 多源融合精度蒙特卡洛（论文数据）。

真值固定。每个定位源的观测 = 真值 + 高斯噪声(σ=标称精度)。
融合引擎用逆方差加权(权重=1/σ²)。统计各场景融合后 RMSE / 平均水平误差。
场景：
  A 单 GNSS(5m)
  B GNSS+WiFi(50m)
  C GNSS+WiFi+LEO PNT(1m) 全融合
  D GNSS 受干扰失效 -> 降级到 LEO+WiFi
  E 全失效降级到 WiFi 粗定位
"""
import sys, os, math, csv, random, json
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TRUE_LAT, TRUE_LON = 43.8868, 125.3245
METERS_PER_DEG_LAT = 111320.0
METERS_PER_DEG_LON = 111320.0 * math.cos(math.radians(TRUE_LAT))
N = 300


def obs(true, sigma_m):
    """从真值加高斯噪声生成一个观测。"""
    dlat = random.gauss(0, sigma_m) / METERS_PER_DEG_LAT
    dlon = random.gauss(0, sigma_m) / METERS_PER_DEG_LON
    return true[0] + dlat, true[1] + dlon


def fused(obs_list):
    """逆方差加权融合。obs_list=[(lat,lon,sigma_m),...]"""
    w = [1.0 / (s * s) for _, _, s in obs_list]
    tw = sum(w)
    fl = sum(lat * wi for (lat, _, _), wi in zip(obs_list, w)) / tw
    fo = sum(lon * wi for (_, lon, _), wi in zip(obs_list, w)) / tw
    # 融合后理论精度 = sqrt(1/sum(1/sigma^2))
    fuse_sigma = math.sqrt(1.0 / sum(1.0 / (s * s) for _, _, s in obs_list))
    return fl, fo, fuse_sigma


def err_m(lat, lon):
    return math.hypot((lat - TRUE_LAT) * METERS_PER_DEG_LAT,
                      (lon - TRUE_LON) * METERS_PER_DEG_LON)


def run(scene):
    errs = []
    for _ in range(N):
        g = obs((TRUE_LAT, TRUE_LON), 5.0)
        w = obs((TRUE_LAT, TRUE_LON), 50.0)
        l = obs((TRUE_LAT, TRUE_LON), 1.0)
        if scene == "A":
            obs_list = [(g[0], g[1], 5.0)]
        elif scene == "B":
            obs_list = [(g[0], g[1], 5.0), (w[0], w[1], 50.0)]
        elif scene == "C":
            obs_list = [(g[0], g[1], 5.0), (w[0], w[1], 50.0), (l[0], l[1], 1.0)]
        elif scene == "D":  # GNSS被干扰,降级
            obs_list = [(w[0], w[1], 50.0), (l[0], l[1], 1.0)]
        else:  # E 只剩WiFi
            obs_list = [(w[0], w[1], 50.0)]
        fl, fo, _ = fused(obs_list)
        errs.append(err_m(fl, fo))
    rmse = math.sqrt(sum(e * e for e in errs) / len(errs))
    mean = sum(errs) / len(errs)
    return rmse, mean, max(errs)


def main():
    random.seed(42)
    rows = []
    print(f"{'场景':<28}{'RMSE(m)':>10}{'均值(m)':>10}{'最大(m)':>10}")
    desc = {
        "A": "单GNSS(5m)",
        "B": "GNSS+WiFi融合(5/50m)",
        "C": "全源融合 GNSS+WiFi+LEO(1m)",
        "D": "GNSS受扰降级->LEO+WiFi",
        "E": "全失效降级->仅WiFi(50m)",
    }
    for sc in "ABCDE":
        rmse, mean, mx = run(sc)
        print(f"{desc[sc]:<28}{rmse:>10.2f}{mean:>10.2f}{mx:>10.2f}")
        rows.append({"scene": sc, "description": desc[sc],
                     "rmse_m": round(rmse, 2), "mean_err_m": round(mean, 2),
                     "max_err_m": round(mx, 2), "trials": N})
    os.makedirs("paper/experiments", exist_ok=True)
    out = "paper/experiments/pnt_fusion_montecarlo.csv"
    with open(out, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=["scene", "description", "rmse_m", "mean_err_m", "max_err_m", "trials"])
        wr.writeheader(); wr.writerows(rows)
    print(f"\n已写 {out}")

    # --- 论文级 JSON 结论输出 ---
    rmses = {r["scene"]: r["rmse_m"] for r in rows}
    result = {
        "experiment": "pnt_fusion_montecarlo",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "scenes": list("ABCDE"),
            "trials_per_scene": N,
            "seed": 42,
        },
        "metrics": {
            "best_rmse_m": min(rmses.values()),
            "best_scene": min(rmses, key=rmses.get),
            "worst_rmse_m": max(rmses.values()),
            "worst_scene": max(rmses, key=rmses.get),
            "scene_A_rmse_m": rmses.get("A"),
            "scene_E_rmse_m": rmses.get("E"),
        },
        "samples": {
            "total": len(rows) * N,
            "scenes": len(rows),
            "trials_per_scene": N,
        },
        "output_files": [out],
    }
    print("\n=== JSON RESULT ===")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
