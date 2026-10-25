# -*- coding: utf-8 -*-
"""把 Overpass 查到的周边设施按"距离+真方位角"排出来，画成极坐标方位图。

方位是真北基准 —— 磁罗盘被干扰时，用它对照寻星。

用法：
  python osm_rfi.py                    # 读 osm_raw.json，出 osm_rfi_polar.png
  python osm_rfi.py --in q.json --out rfi.png
  python osm_rfi.py --json             # stdout 纯机器可读 JSON（表格让位）
"""
import argparse, json, math, os, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D

# 中文字体（Windows 自带，避免图上全是方框）
for _p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
           r"C:\Windows\Fonts\simhei.ttf"):
    if os.path.exists(_p):
        font_manager.fontManager.addfont(_p)
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
        break
plt.rcParams["axes.unicode_minus"] = False

ME = (43.774156, 125.397813)

KIND = [  # (key, 中文名, 颜色, 点大小)
    ("crane", "塔吊/起重机", "#ff4444", 240),
    ("construction", "工地/在建", "#ff9933", 150),
    ("tower", "通信塔", "#44aaff", 130),
    ("mast", "桅杆", "#44aaff", 120),
    ("antenna", "天线", "#00ddaa", 120),
    ("power", "电力设施", "#cc66ff", 120),
    ("telecom", "电信设施", "#66ccff", 110),
]

SAT = [("METEOR-M2 3 08:53", 95.0, "#00ff88"),
       ("NOAA 19 10:36", 312.0, "#00ffcc"),
       ("ISS 08:06", 221.0, "#ffff00")]


def dist_az(lat, lon):
    R = 6371000.0
    p1, p2 = math.radians(ME[0]), math.radians(lat)
    dp = p2 - p1
    dl = math.radians(lon - ME[1])
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    dm = 2 * R * math.asin(math.sqrt(a))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return dm, (math.degrees(math.atan2(y, x)) + 360) % 360


def classify(tags):
    s = json.dumps(tags, ensure_ascii=False).lower()
    if "crane" in s:
        return 0
    if "construction" in s:
        return 1
    if "power" in s:
        return 5
    if "antenna" in s:
        return 4
    if "mast" in s:
        return 3
    if "tower" in s:
        return 2
    if "telecom" in s or "communication" in s:
        return 6
    return None


def main():
    ap = argparse.ArgumentParser(description="OSM 周边干扰源极坐标方位图（真北基准）")
    ap.add_argument("--in", dest="infile", default="osm_raw.json",
                    help="Overpass JSON（默认 osm_raw.json）")
    ap.add_argument("--out", dest="outfile", default="osm_rfi_polar.png",
                    help="输出图（默认 osm_rfi_polar.png）")
    ap.add_argument("--json", action="store_true",
                    help="输出机器可读 JSON（stdout 纯 JSON，无表格；图照常出）")
    a = ap.parse_args()

    if not os.path.exists(a.infile):
        raise SystemExit(f"[err] 输入不存在: {a.infile}（Overpass 查询结果 JSON，"
                         f"用 --in 指定）")
    try:
        d = json.load(open(a.infile, encoding="utf-8"))
    except Exception as e:
        raise SystemExit(f"[err] {a.infile} 解析不了: {e.__class__.__name__}: {e}")

    items = []
    for e in d.get("elements", []):
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lon = e.get("lon") or (e.get("center") or {}).get("lon")
        if lat is None:
            continue
        k = classify(e.get("tags", {}))
        if k is None:
            continue
        dm, az = dist_az(lat, lon)
        t = e.get("tags", {})
        items.append(dict(k=k, dm=dm, az=az,
                          name=str(t.get("name") or t.get("operator") or "")[:30],
                          tags=t))
    items.sort(key=lambda x: x["dm"])

    if a.json:
        out = [dict(kind=KIND[it["k"]][1], kind_key=KIND[it["k"]][0],
                    dist_m=round(it["dm"], 1), az_deg=round(it["az"], 2),
                    name=it["name"], tags=it["tags"]) for it in items]
        json.dump({"me": {"lat": ME[0], "lon": ME[1]},
                   "count": len(out), "items": out},
                  sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print("=== 周边设施（按距离，方位=真北基准）===")
        for it in items[:30]:
            _, nm, col, sz = KIND[it["k"]]
            extra = " ".join(f"{k}={v}" for k, v in it["tags"].items()
                             if k in ("man_made", "tower:type", "power", "landuse",
                                      "building", "height", "construction"))
            print(f"  {nm:<10s} {it['dm']:6.0f} m  方位 {it['az']:5.1f}°  "
                  f"{it['name']:<12s} {extra[:44]}")

    fig = plt.figure(figsize=(11, 9))
    ax = plt.subplot(111, polar=True)
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    for it in items:
        _, nm, col, sz = KIND[it["k"]]
        ax.scatter(math.radians(it["az"]), it["dm"], c=col, s=sz, alpha=.85,
                   edgecolors="white", linewidths=.6, zorder=3)
    for nm, az, c in SAT:
        ax.plot([math.radians(az)] * 2, [0, 3000], color=c, lw=2.2, ls="--", zorder=4)
        ax.scatter([math.radians(az)], [3180], c=c, s=190, marker="*", zorder=5)
        ax.annotate(nm, xy=(math.radians(az), 3450), color=c, fontsize=9, ha="center")
    ax.set_ylim(0, 3800)
    ax.set_yticks([500, 1000, 1500, 2000, 2500, 3000])
    ax.set_yticklabels(["500m", "1km", "1.5km", "2km", "2.5km", "3km"],
                       color="#9aa3b2", fontsize=8)
    ax.set_title("周边设施方位图 · 中心=你的位置（长春 43.7742N 125.3978E）\n"
                 "方位为真北基准，磁罗盘失效时用这张图对照寻星",
                 color="#e6e8ee", pad=18)
    ax.tick_params(colors="#9aa3b2")
    ax.grid(color="#3a4050")
    fig.patch.set_facecolor("#12141a")
    ax.set_facecolor("#1a1d26")
    leg = [Line2D([0], [0], marker="o", color="w", markerfacecolor=k[2],
                  markersize=9, label=k[1]) for k in KIND]
    leg += [Line2D([0], [0], color=c, lw=2, ls="--", label=f"过境方位 {n}")
            for n, a, c in SAT]
    ax.legend(handles=leg, loc="upper right", bbox_to_anchor=(1.30, 1.12),
              facecolor="#1a1d26", edgecolor="#3a4050", labelcolor="#e6e8ee", fontsize=9)
    plt.tight_layout()
    plt.savefig(a.outfile, dpi=125, facecolor="#12141a")
    if not a.json:
        print(f"\n[out] {a.outfile}")


if __name__ == "__main__":
    main()
