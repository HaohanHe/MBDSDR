# SPDX-License-Identifier: MIT
"""观星台址目录：只收坐标能追溯到官方页面的地点。

为什么不照搬「观星点」列表
==========================

消费级 App 常内置几十个「观星点」，但既不写坐标来源，也不区分「科研
台址」和「公众可进入的营地」。用户按图索骥开到天文台门口被拦下，
比没有这个功能更糟。

本模块的取舍：

* 内置点全部是**有官方页面给出经纬度的地点**，`source_url` 逐条可点开
  核对；
* `access` 明确标注：``research`` 是科研台址（不对外开放），``public``
  是公众可进入的科普基地/景区——两者不能混为一谈；
* ``public`` 也不等于「随时能进」：预约、开放日、门票以官方公告为准，
  写进 ``note``；不确定开放状态的宁可不收；
* 官方页没给海拔时 ``alt_m`` 记 ``None``（GeoJSON 里是 ``null``），
  **不拿 0 m 冒充**；
* 内置表不收录「我记得大概在哪」的地点；用户自己的点走
  :func:`validate_sites` 导入，缺来源 URL 的会被拒绝；
* 输出可转 GeoJSON（``[lon, lat]``，与 QGIS 导出同口径）。

距离用 haversine 球面距离，误差 <0.5%（台址尺度够用），不引入 PROJ。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

__all__ = [
    "Site",
    "SITES",
    "ACCESS_KINDS",
    "haversine_km",
    "nearest_sites",
    "sites_as_geojson",
    "validate_sites",
]

#: 台址性质。``research`` 不代表可以进入，只代表「这是科研设施」。
ACCESS_KINDS = ("research", "public", "unknown")


@dataclass(frozen=True)
class Site:
    """一个台址。坐标字段是十进制度；``alt_m`` 缺失时为 ``None``。"""

    site_id: str
    name_zh: str
    name_en: str
    lat_deg: float
    lon_deg: float
    alt_m: Optional[float]
    access: str
    source_url: str
    note: str = ""
    verified_on: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


SITES = (
    Site(
        site_id="xinglong",
        name_zh="兴隆观测站",
        name_en="Xinglong Observatory (NAOC)",
        lat_deg=40.39417, lon_deg=117.57500, alt_m=960.0,
        access="research",
        source_url="http://www.xinglong-naoc.cn/html/en/",
        note="国家天文台兴隆站，LAMOST 所在地；科研台址，不对外开放。",
    ),
    Site(
        site_id="lijiang-gaomeigu",
        name_zh="丽江高美古观测站",
        name_en="Lijiang Gaomeigu Observatory (YNAO)",
        lat_deg=26.70889, lon_deg=100.03083, alt_m=3193.0,
        access="research",
        source_url="https://www.ynao.cas.cn/kyzb/202306/t20230617_6779436.html",
        note="云南天文台丽江站，2.4 米望远镜所在地；科研台址，不对外开放。",
    ),
    Site(
        site_id="ali",
        name_zh="阿里观测站",
        name_en="Ali Observatory (NAOC)",
        lat_deg=32.32167, lon_deg=80.02750, alt_m=5100.0,
        access="research",
        source_url="https://ali.xzdw.gov.cn/xwzx_1128/alyw/202108/t20210817_192849.html",
        note="海拔 5050–5350 m（A1/B1/C1 点），本表取 5100 m；科研台址。",
    ),
    Site(
        site_id="lenghu-saishiteng",
        name_zh="冷湖赛什腾山天文基地",
        name_en="Lenghu Saishiteng Observatory Site",
        lat_deg=38.61389, lon_deg=93.90000, alt_m=4200.0,
        access="research",
        source_url="https://must.astro.tsinghua.edu.cn/zh/design-metrics",
        note="赛什腾山 C 区 A 点（清华 MUST 页面给出坐标）；科研台址。",
    ),
    Site(
        site_id="xuyi",
        name_zh="盱眙天文观测站",
        name_en="Xuyi Observatory (PMO)",
        lat_deg=32.733333, lon_deg=118.466667, alt_m=180.0,
        access="research",
        source_url="https://pmo.cas.cn/jgsz2019/gctz2019/xygcz2025/",
        note=("紫金山天文台盱眙站，铁山寺跑马山。官方页：东经 118°28′、"
              "北纬 32°44′（分精度，约 1.5–1.9 km）、海拔 180 m；"
              "近地天体探测望远镜所在地。科研台址，不对外开放。"),
        verified_on="2026-11-17",
    ),
    Site(
        site_id="muztagh-ata",
        name_zh="慕士塔格观测站",
        name_en="Muztagh Ata Observatory (XAO)",
        lat_deg=38.329722, lon_deg=74.896667, alt_m=4500.0,
        access="research",
        source_url="http://xao.ac.cn/jgsz/ywtz/mstggcz/",
        note=("新疆天文台慕士塔格站（阿克陶县布伦口乡苏巴什村）。官方页："
              "38°19′47″N、74°53′48″E，海拔「约 4500 m」（原值带约数，"
              "不当作实测高程）；科研台址，不对外开放。"),
        verified_on="2026-11-17",
    ),
    # --- 公众点：只收「官方页给坐标 + 官方页能看出开放性质」的 ---
    Site(
        site_id="shahe-science-base",
        name_zh="国家天文台沙河科普基地",
        name_en="NAOC Shahe Science Popularization Base",
        lat_deg=40.100000, lon_deg=116.333333, alt_m=40.0,
        access="public",
        source_url="http://nao.cas.cn/ncb/kp/202203/t20220331_6417255.html",
        note=("原北京天文台沙河工作站。官方页：昌平区七马路，东经 116°20′、"
              "北纬 40°06′（分精度，约 1.8 km）、海拔 40 m；常年面向团体"
              "预约开放，个人参观以开放日公告为准。城市光害明显，是科普点"
              "不是暗夜点。"),
        verified_on="2026-11-17",
    ),
    Site(
        site_id="eboliang-yardang",
        name_zh="俄博梁雅丹（火星营地北 2 km）",
        name_en="Eboliang Yardang, Lenghu",
        lat_deg=38.354722, lon_deg=92.256944, alt_m=None,
        access="public",
        source_url="https://www.mangya.gov.cn/info/1418/95515.htm",
        note=("茫崖市政府页：冷湖镇以南 50 km，东经 92°15′25″、北纬 38°21′17″；"
              "火星营地在雅丹北侧约 2 km。官方页未给海拔，alt_m 记 null。"
              "景区开放与门票以当地公告为准；暗夜条件好，但无官方暗夜等级认证。"),
        verified_on="2026-11-17",
    ),
    Site(
        site_id="mohe-beijicun",
        name_zh="漠河北极村（景区范围中心）",
        name_en="Beijicun, Mohe",
        lat_deg=53.504167, lon_deg=122.354861, alt_m=None,
        access="public",
        source_url=("https://www.mohe.gov.cn/mohe/c101210/202309/"
                    "c13_258538.shtml"),
        note=("漠河市文旅局景区简介：景区地理坐标为东经 122°21′05″–122°21′30″、"
              "北纬 53°27′00″–53°33′30″（矩形范围，南北跨度约 12 km）；"
              "本表取范围中心，不是景区门址。官方页未给海拔，alt_m 记 null。"
              "官方宣传语称「全国唯一观赏北极光和极昼极夜的最佳观测点」；"
              "按本地磁纬 44.4°（本仓 tonight.geomagnetic_latitude 实算）"
              "极光仍要较强地磁活动，多数夜晚看不到；夏季极昼无法观星。"),
        verified_on="2026-11-17",
    ),
)

_EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float,
                 lat2: float, lon2: float) -> float:
    """球面大圆距离（km）。台址尺度误差 <0.5%。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2.0 * _EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def nearest_sites(lat_deg: float, lon_deg: float,
                  limit: int = 3,
                  sites: Optional[Iterable[Site]] = None) -> List[Dict[str, Any]]:
    """按距离升序返回最近的台址，带 ``distance_km``。"""
    if not (-90.0 <= float(lat_deg) <= 90.0):
        raise ValueError(f"纬度超出 [-90, 90]：{lat_deg}")
    if not (-180.0 <= float(lon_deg) <= 180.0):
        raise ValueError(f"经度超出 [-180, 180]：{lon_deg}")
    if limit <= 0:
        raise ValueError(f"limit 必须为正数，收到 {limit}")

    rows = []
    for s in (sites if sites is not None else SITES):
        d = haversine_km(lat_deg, lon_deg, s.lat_deg, s.lon_deg)
        row = s.to_dict()
        row["distance_km"] = round(d, 1)
        rows.append(row)
    rows.sort(key=lambda r: r["distance_km"])
    return rows[:limit]


def sites_as_geojson(sites: Optional[Iterable[Site]] = None) -> Dict[str, Any]:
    """台址 → FeatureCollection（Point，坐标序 ``[lon, lat]``）。"""
    features = []
    for s in (sites if sites is not None else SITES):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [float(s.lon_deg), float(s.lat_deg)]},
            "properties": {
                "site_id": s.site_id,
                "name_zh": s.name_zh,
                "name_en": s.name_en,
                "alt_m": None if s.alt_m is None else float(s.alt_m),
                "access": s.access,
                "source_url": s.source_url,
                "verified_on": s.verified_on,
                "note": s.note,
                "role": "observing_site",
            },
        })
    return {"type": "FeatureCollection", "features": features}


def _valid_url(url: str) -> bool:
    try:
        p = urlparse(url)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)


def validate_sites(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """校验用户导入的台址列表（JSON 数组的每一项）。

    规则与 IQ 样本清单同口径：缺来源的不要。返回
    ``{ok, accepted, rejected, counts}``，坏项带序号与中文理由。
    """
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}

    for idx, item in enumerate(rows, 1):
        reasons: List[str] = []
        if not isinstance(item, dict):
            rejected.append({"index": idx, "site_id": "", "reasons": [
                "每一项必须是 JSON 对象"]})
            continue

        sid = str(item.get("site_id") or "").strip()
        if not sid:
            reasons.append("site_id 为空")
        elif sid in seen:
            reasons.append(f"site_id 与第 {seen[sid]} 项重复：{sid}")
        else:
            seen[sid] = idx

        if not str(item.get("name_zh") or "").strip():
            reasons.append("name_zh 为空")

        lat = item.get("lat_deg")
        lon = item.get("lon_deg")
        if isinstance(lat, bool) or not isinstance(lat, (int, float)):
            reasons.append("lat_deg 必须是数字")
        elif not (-90.0 <= float(lat) <= 90.0):
            reasons.append(f"lat_deg 超出 [-90, 90]：{lat}")
        if isinstance(lon, bool) or not isinstance(lon, (int, float)):
            reasons.append("lon_deg 必须是数字")
        elif not (-180.0 <= float(lon) <= 180.0):
            reasons.append(f"lon_deg 超出 [-180, 180]：{lon}")

        alt = item.get("alt_m", 0.0)
        if isinstance(alt, bool) or not isinstance(alt, (int, float)):
            reasons.append("alt_m 必须是数字")
        elif float(alt) < -500.0 or float(alt) > 9000.0:
            reasons.append(f"alt_m 超出合理范围：{alt}")

        access = str(item.get("access") or "").strip()
        if access not in ACCESS_KINDS:
            reasons.append(f"access 必须是 {ACCESS_KINDS} 之一，收到 {access!r}")

        src = str(item.get("source_url") or "").strip()
        if not src:
            reasons.append("source_url 为空：没有来源的坐标不收")
        elif not _valid_url(src):
            reasons.append(f"source_url 不是 http(s) 链接：{src}")

        if reasons:
            rejected.append({"index": idx, "site_id": sid,
                             "reasons": reasons})
        else:
            accepted.append(item)

    return {
        "ok": not rejected,
        "accepted": accepted,
        "rejected": rejected,
        "counts": {"accepted": len(accepted), "rejected": len(rejected)},
    }
