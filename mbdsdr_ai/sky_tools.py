# SPDX-License-Identifier: MIT
"""星空工具面：让模型能回答"我指的这一片天里有什么"。

为什么要有这个模块
------------------
在这之前，模型能查卫星（`tle_parse` / `sat_pass_predict` / `doppler_calc`），
但**答不了"那颗星是什么"**——全仓 `grep skyengine` 在 `agent.py` /
`sdr_tools.py` / `tool_registry.py` 里零命中，星表 83479 颗星对 AI 是不可见的。

而"AI 讲解层"正是这个项目区别于自动寻星设备的地方：它们解决"找得到"，
我们要解决"认得它、懂它"。没有工具面，那句话就只能是空话。

设计口径
--------
* **只读**。这三个工具一个字节都不写盘、不动硬件，不触发任何确认门。
* **站址与时刻必须真**。不传就从 agent 的观测者与时间引擎取；取不到就
  **报错要求先设置**，绝不拿示例站顶替——示例站的星图是真的，但"你在哪"
  是假的，合起来就是假观测。
* **距离缺失就说缺失**。HYG 里部分星没有 `dist_pc`（0 或非有限），
  此时光行时给 `null`，不许用 0 或平均值填充。
* **光行时用年，不用秒**。用户能感知的单位是"光走了 8.6 年"，
  不是"2.7e17 秒"。

三个工具的分工
--------------
- `sky_whats_here`      ：我有指向，问这一片有什么（视场枚举）
- `sky_object_lookup`   ：我有名字，问它是谁（身份查询）
- `sky_asterism_lookup` ：我有星官名，问它由什么组成（结构查询）
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

#: 一 parsec 合多少光年。光行时 = 距离(pc) × 这个数。
PC_TO_LIGHT_YEAR = 3.261563777

#: 视场枚举的默认上限星等。比这更暗的星不给——给了模型也讲不出所以然，
#: 只会把回答淹掉。真正要看暗星由调用方显式抬 limit_mag。
DEFAULT_LIMIT_MAG = 6.0

#: 一次最多返回多少颗星。
#:
#: 这个数字不是随便定的：tool_registry 的输出上限是 4000 字符，
#: 超了就会 head 60% + tail 40% 中间砍掉——**对 JSON 是毁灭性的**，
#: 模型拿到的是无法解析的残片，比不给更糟。所以宁可少给几颗，
#: 也要让整份 JSON 完整。12 颗按当前字段约 1800 字符，留足余量。
MAX_STARS_RETURNED = 12

#: 一次最多返回多少个星官。同理，星官条目比星条目长。
MAX_ASTERISMS_RETURNED = 4


def _site_from_observer(agent) -> Dict[str, Any]:
    """取观测站事实。

    注意一个真实存在的歧义：`Observer()` 的默认值是 (0.0, 0.0, 0m)，
    而赤道本初子午线（几内亚湾）也是 (0, 0)。所以**不能**把 (0,0) 当成
    "未设置"直接拒绝——那会把真在那里的观测者判成没配站址。

    做法：照实返回坐标，另给一个 `site_warning`。不撒谎（坐标是真的），
    也不装懂（提醒它可能是默认值）。
    """
    obs = getattr(agent, "observer", None)
    if obs is None:
        raise ValueError(
            "agent 没有观测者对象。请先调用 astro_set_observer 设置站址。")
    lat = getattr(obs, "latitude_deg", None)
    lon = getattr(obs, "longitude_deg", None)
    if lat is None or lon is None:
        raise ValueError(
            "观测站缺少经纬度。请先调用 astro_set_observer 补全站址。")
    height = float(getattr(obs, "height_m", 0.0) or 0.0)
    warning = ""
    if abs(float(lat)) < 1e-9 and abs(float(lon)) < 1e-9:
        warning = ("站址是 (0.0°, 0.0°)——这是 Observer 的默认值，"
                   "也可能是真在赤道本初子午线。若尚未设置站址，"
                   "请先调用 astro_set_observer。")
    return {"lat_deg": float(lat), "lon_deg": float(lon),
            "alt_m": height, "site_warning": warning}


def _current_jd(agent) -> float:
    """当前 UT1 儒略日：优先时间引擎（支持时间机器/模拟），否则真实 now。"""
    from .new_spacetime import get_time_engine, utc_to_jd

    import datetime as _dt

    try:
        eng = get_time_engine()
        return float(eng.tick())
    except Exception:
        return utc_to_jd(_dt.datetime.now(_dt.timezone.utc))


def _station_from(lat: float, lon: float, alt_m: float = 0.0):
    from .celestial_geometry import GroundStation

    return GroundStation(float(lon), float(lat), float(alt_m), "tool")


def _light_years(dist_pc: Optional[float]) -> Optional[float]:
    """视差距离转光行时（年）。缺失/非有限/非正一律 None，不编。"""
    if dist_pc is None:
        return None
    try:
        v = float(dist_pc)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0.0:
        return None
    return v * PC_TO_LIGHT_YEAR


def _fmt_ly(years: Optional[float]) -> str:
    """光行时的人话。缺失时说缺失。"""
    if years is None:
        return "距离未知"
    if years < 100.0:
        return f"光走了 {years:.1f} 年"
    return f"光走了 {years:.0f} 年"


class _CatalogIndex:
    """按 HIP / 名称反查星表行号。构造一次即可复用。"""

    def __init__(self, catalog) -> None:
        import numpy as np

        self.catalog = catalog
        self._hip = np.asarray(catalog.hip)
        self._name = np.asarray(catalog.name)
        self._by_hip: Dict[int, int] = {
            int(h): i for i, h in enumerate(self._hip) if h > 0}

    def by_hip(self, hip: int) -> Optional[int]:
        return self._by_hip.get(int(hip))

    def by_name(self, text: str) -> Optional[int]:
        """西名精确匹配（忽略大小写）。中文名不在这里——那份在星官表里。"""
        if not text:
            return None
        target = text.strip().lower()
        for i, name in enumerate(self._name):
            if name and str(name).strip().lower() == target:
                return i
        return None

    def row(self, idx: int) -> Dict[str, Any]:
        c = self.catalog
        dist_pc = float(c.dist_pc[idx]) if c.dist_pc is not None else None
        ly = _light_years(dist_pc)
        return {
            "hip": int(c.hip[idx]) if c.hip[idx] else 0,
            "name": str(c.name[idx]) or "",
            "con": str(c.con[idx]) or "",
            "ra_deg": float(c.ra_deg[idx]),
            "dec_deg": float(c.dec_deg[idx]),
            "vmag": float(c.vmag[idx]),
            "bv": float(c.bv[idx]),
            "spect": str(c.spect[idx]) or "",
            "dist_pc": dist_pc if ly is not None else None,
            "light_years": ly,
            "light_time_text": _fmt_ly(ly),
        }


def _asterism_of_hip(agent) -> Dict[int, List[str]]:
    """HIP → 所属星官名列表。星官数据是静态的，但表本身要缓存在 agent 上。"""
    cache = getattr(agent, "_sky_hip_to_asterism", None)
    if cache is not None:
        return cache
    from .skyengine.xingguan import get_xingguan

    out: Dict[int, List[str]] = {}
    xg = get_xingguan()
    for region in xg.regions:
        for ast in region.xingguan:
            for star in ast.stars:
                out.setdefault(int(star.hip), []).append(ast.name_zh)
            for poly in ast.lines:
                for star in poly:
                    out.setdefault(int(star.hip), []).append(ast.name_zh)
    # 去重但保持顺序
    for hip, names in out.items():
        seen = []
        for n in names:
            if n not in seen:
                seen.append(n)
        out[hip] = seen
    try:
        agent._sky_hip_to_asterism = out
    except Exception:
        pass
    return out


def register_sky_tools(agent) -> None:
    """把星空工具注册到 agent（只读，不触发任何确认门）。"""
    reg = agent.tool_registry

    # ------------------------------------------------------------------
    # 1) 视场枚举：我有指向，这一片有什么
    # ------------------------------------------------------------------
    def _whats_here(args: Dict[str, Any]):
        from .celestial_geometry import GroundStation
        from .skyengine.star_catalog import get_catalog, select_for_view
        from .skyengine.xingguan import get_xingguan

        # 站址：显式传入优先；否则取当前观测站（并保留默认值警示）
        lat = args.get("lat_deg")
        lon = args.get("lon_deg")
        site_warning = ""
        if lat is not None and lon is not None:
            station = _station_from(lat, lon, args.get("alt_m", 0.0) or 0.0)
            site_note = "由调用参数给出"
        else:
            site = _site_from_observer(agent)
            station = _station_from(site["lat_deg"], site["lon_deg"],
                                    site["alt_m"])
            site_note = "取自当前观测站"
            site_warning = site["site_warning"]

        jd = args.get("jd_ut1")
        if jd is None:
            jd = _current_jd(agent)
            time_note = "取自时间引擎/当前时刻"
        else:
            time_note = "由调用参数给出"

        az = float(args["az_deg"])
        alt = float(args["alt_deg"])
        fov = float(args["fov_deg"])
        limit_mag = float(args.get("limit_mag") or DEFAULT_LIMIT_MAG)

        if not (0.0 < fov <= 180.0):
            raise ValueError(f"视场角必须在 (0, 180] 度内，收到 {fov}")

        catalog = get_catalog()
        sel = select_for_view(station, float(jd), az, alt, fov,
                              limit_mag=limit_mag,
                              max_count=2000)

        index = _CatalogIndex(catalog)
        hip_to_ast = _asterism_of_hip(agent)
        az_arr = sel.az_deg
        alt_arr = sel.alt_deg

        stars: List[Dict[str, Any]] = []
        for i in range(len(sel.vmag)):
            hip = int(sel.hip[i]) if sel.hip[i] else 0
            row_idx = index.by_hip(hip) if hip else None
            base = index.row(row_idx) if row_idx is not None else {
                "hip": hip,
                "name": str(sel.name[i]) or "",
                "con": str(sel.con[i]) or "",
                "ra_deg": float(sel.ra_deg[i]),
                "dec_deg": float(sel.dec_deg[i]),
                "vmag": float(sel.vmag[i]),
                "bv": float(sel.bv[i]),
                "spect": str(sel.spect[i]) or "",
                "dist_pc": None,
                "light_years": None,
                "light_time_text": "距离未知",
            }
            stars.append({
                "name": base["name"],
                "hip": hip,
                "con": base["con"],
                "vmag": round(float(sel.vmag[i]), 2),
                "az_deg": round(float(az_arr[i]), 2),
                "alt_deg": round(float(alt_arr[i]), 2),
                # 只给光行时，不给 dist_pc：模型讲距离用"光走了多少年"，
                # parsec 对听众没有意义，留着只会占字符预算。
                "light_years": (round(base["light_years"], 1)
                                if base["light_years"] is not None else None),
                "asterisms": hip_to_ast.get(hip, [])[:3] if hip else [],
            })

        # 亮的排前面：模型先讲最该讲的那颗。
        stars.sort(key=lambda s: s["vmag"])

        # 星官：只报"成员星落在此视场内"的，并说清装不装得下。
        xg = get_xingguan()
        seen_ast: Dict[str, Dict[str, Any]] = {}
        hips_here = {s["hip"] for s in stars if s["hip"]}
        for region in xg.regions:
            for ast in region.xingguan:
                members = {int(st.hip) for st in ast.stars}
                for poly in ast.lines:
                    members.update(int(st.hip) for st in poly)
                hit = members & hips_here
                if not hit:
                    continue
                entry = seen_ast.setdefault(ast.name_zh, {
                    "name": ast.name_zh,
                    "pinyin": ast.pinyin,
                    "region": region.name_zh,
                    "members_in_view": 0,
                    "members_total": len(members),
                })
                entry["members_in_view"] = len(hit)
        asterisms = sorted(seen_ast.values(),
                           key=lambda a: -a["members_in_view"])[:MAX_ASTERISMS_RETURNED]
        for a in asterisms:
            a["truncated"] = a["members_in_view"] < a["members_total"]

        total = len(stars)
        return {
            "ok": True,
            "site": {"lat_deg": float(station.latitude),
                     "lon_deg": float(station.longitude),
                     "note": site_note,
                     "warning": site_warning},
            "jd_ut1": float(jd),
            "time_note": time_note,
            "pointing": {"az_deg": az, "alt_deg": alt, "fov_deg": fov},
            "limit_mag": limit_mag,
            "star_count": total,
            "stars": stars[:MAX_STARS_RETURNED],
            # 如实告诉模型"这不是全部"：不说的话它会以为天上就这么多星。
            "stars_listed": min(total, MAX_STARS_RETURNED),
            "stars_omitted": max(0, total - MAX_STARS_RETURNED),
            "asterisms": asterisms,
            "catalog": {"name": "HYG v4.1", "license": "CC BY-SA 4.0"},
            "hint": ("讲距离用 light_years（光走了多少年），不要念 parsec；"
                     "asterisms 里 truncated=true 的要明说『你只看到其中一段』；"
                     "stars_omitted>0 说明还有更暗的星没列，需要时可抬 limit_mag。"),
        }

    reg.register(
        name="sky_whats_here",
        description=(
            "视场枚举：给定指向(方位角/地平高度)与视场角，列出这一片天里"
            "肉眼可见的恒星与它们所属的中国星官。返回每颗星的名称/星等/"
            "距离/光行时，以及星官成员在视场内外的比例。"
            "用于回答『我现在指的这一片有什么』『那几颗连起来像什么』。"
            "站址与时刻默认取当前观测站与时间引擎；要用别的时间/地点就显式传。"
            "只会读取星表，不改任何状态。"),
        parameters={"type": "object", "properties": {
            "az_deg": {"type": "number", "description": "指向方位角（度，北=0 东=90）"},
            "alt_deg": {"type": "number", "description": "指向地平高度（度，地平线以上为正）"},
            "fov_deg": {"type": "number", "description": "视场角（度）。目镜约 2.3，寻星镜约 8，手机广角约 60-70"},
            "limit_mag": {"type": "number", "description": f"极限星等，默认 {DEFAULT_LIMIT_MAG}"},
            "lat_deg": {"type": "number", "description": "可选：覆盖观测站纬度"},
            "lon_deg": {"type": "number", "description": "可选：覆盖观测站经度"},
            "alt_m": {"type": "number", "description": "可选：观测站海拔（米）"},
            "jd_ut1": {"type": "number", "description": "可选：UT1 儒略日，默认当前时刻"},
        }, "required": ["az_deg", "alt_deg", "fov_deg"]},
        handler=_whats_here,
        category="sky",
    )

    # ------------------------------------------------------------------
    # 2) 身份查询：我有名字，它是谁
    # ------------------------------------------------------------------
    def _lookup(args: Dict[str, Any]):
        from .skyengine.star_catalog import get_catalog
        from .skyengine.xingguan import (
            load_xingguan_star_names, xingguan_star_name)

        catalog = get_catalog()
        index = _CatalogIndex(catalog)
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query 不能为空：请给星名（西名/中文名）或 HIP 号")

        row_idx: Optional[int] = None
        matched_by = ""
        if query.upper().startswith("HIP"):
            digits = "".join(ch for ch in query if ch.isdigit())
            if digits:
                row_idx = index.by_hip(int(digits))
                matched_by = "HIP"
        elif query.isdigit():
            row_idx = index.by_hip(int(query))
            matched_by = "HIP"

        if row_idx is None:
            row_idx = index.by_name(query)
            if row_idx is not None:
                matched_by = "西名"

        if row_idx is None:
            # 中文星名：走星官中文名表（HIP -> 中文名）
            try:
                names = load_xingguan_star_names()
                hit_hip = None
                for hip_str, rec in names.stars.items():
                    zh = (rec.get("zh_name") or "")
                    alts = rec.get("alt_names") or []
                    if zh == query or query in alts:
                        hit_hip = int(rec.get("hip") or hip_str)
                        break
                if hit_hip is not None:
                    row_idx = index.by_hip(hit_hip)
                    matched_by = "中文星名"
            except Exception:
                row_idx = None

        if row_idx is None:
            return {
                "ok": False,
                "reason": f"星表里找不到 {query!r}",
                "hint": ("可尝试：西名（如 Vega）、HIP 号（如 HIP 91262）、"
                         "中文星名（如 织女一）。"
                         "如果这是星官名请改用 sky_asterism_lookup。"),
            }

        base = index.row(row_idx)
        hip = base["hip"]
        hip_to_ast = _asterism_of_hip(agent)
        zh = ""
        zh_alt: List[str] = []
        if hip:
            try:
                rec = xingguan_star_name(hip)
                if rec is not None:
                    # 返回的是 XingguanStarName 数据对象，不是字符串；
                    # 直接塞进 dict 会让工具结果无法序列化（实测 TypeError）。
                    zh = rec.zh_name or ""
                    zh_alt = list(rec.alt_names or ())[:4]
            except Exception:
                zh = ""

        return {
            "ok": True,
            "matched_by": matched_by,
            **base,
            "zh_name": zh,
            "zh_alt_names": zh_alt,
            "asterisms": hip_to_ast.get(hip, []) if hip else [],
            "hint": ("讲距离时用 light_years / light_time，不要念 parsec。"
                     "dist_pc 为 null 表示星表没有这颗星的视差，"
                     "此时必须说『距离未知』，不许估。"),
        }

    reg.register(
        name="sky_object_lookup",
        description=(
            "恒星身份查询：给西名 / 中文星名 / HIP 号，返回这颗星的"
            "星座、星等、颜色指数、光谱型、距离与光行时、所属中国星官。"
            "用于回答『这颗星叫什么』『它离我多远』『光走了多久』。"
            "只读星表；查不到会明说查不到并给可尝试的写法。"),
        parameters={"type": "object", "properties": {
            "query": {"type": "string",
                      "description": "星名（Vega / 织女一）或 HIP 号（HIP 91262 / 91262）"},
        }, "required": ["query"]},
        handler=_lookup,
        category="sky",
    )

    # ------------------------------------------------------------------
    # 3) 星官查询：它由什么组成
    # ------------------------------------------------------------------
    def _asterism(args: Dict[str, Any]):
        import numpy as np

        from .celestial_geometry import vec_from_radec
        from .skyengine.star_catalog import get_catalog
        from .skyengine.xingguan import get_xingguan

        name = str(args.get("name") or "").strip()
        if not name:
            raise ValueError("name 不能为空：请给星官中文名（如 北斗）")

        catalog = get_catalog()
        index = _CatalogIndex(catalog)
        xg = get_xingguan()

        target = None
        region_name = ""
        for region in xg.regions:
            for ast in region.xingguan:
                if ast.name_zh == name:
                    target = ast
                    region_name = region.name_zh
                    break
            if target is not None:
                break
        if target is None:
            available = [a.name_zh for r in xg.regions for a in r.xingguan][:20]
            return {
                "ok": False,
                "reason": f"星官数据里没有 {name!r}",
                "hint": f"可尝试其中一些：{'、'.join(available)}…",
            }

        members = []
        hips = set()
        for st in target.stars:
            hips.add(int(st.hip))
        for poly in target.lines:
            for st in poly:
                hips.add(int(st.hip))

        vecs = []
        for hip in sorted(hips):
            row_idx = index.by_hip(hip)
            if row_idx is None:
                members.append({"hip": hip, "note": "不在随仓星表内"})
                continue
            row = index.row(row_idx)
            members.append({
                "hip": row["hip"],
                "name": row["name"],
                "vmag": round(row["vmag"], 2),
                "light_years": (round(row["light_years"], 2)
                                if row["light_years"] is not None else None),
            })
            vecs.append(vec_from_radec(row["ra_deg"], row["dec_deg"]))

        # 跨度：成员星之间的最大角距。这是"装不装得下"的判据。
        span = 0.0
        if len(vecs) >= 2:
            mat = np.vstack(vecs)
            sim = np.clip(mat @ mat.T, -1.0, 1.0)
            span = float(np.degrees(np.arccos(sim.min())))

        polys = [[int(st.hip) for st in poly] for poly in target.lines
                 if len(poly) >= 2]
        return {
            "ok": True,
            "name": target.name_zh,
            "pinyin": target.pinyin,
            "english": target.name_en,
            "region": region_name,
            "star_count": len(members),
            "segment_count": sum(len(p) - 1 for p in polys),
            "span_deg": round(span, 2),
            "members": members,
            "has_lines": bool(polys),
            "note": ("这个星官只有一颗星，画不出连线，但它确实是一个『官』"
                     if not polys else ""),
            "hint": ("span_deg 比当前视场大时，用户只能看到其中一段——"
                     "要明说这一点，不要说『你应该看到一把勺子』。"),
        }

    reg.register(
        name="sky_asterism_lookup",
        description=(
            "中国星官查询：给星官中文名（如 北斗 / 参宿 / 织女），返回它属于"
            "哪个星区（三垣/二十八宿）、成员星列表与星等、总跨度（度）、"
            "以及能不能画出连线。"
            "用于回答『北斗是哪几颗』『它有多大』『为什么我在望远镜里看不出形状』。"
            "跨度大于视场时必须在回答里说清『你只看到其中一段』。"),
        parameters={"type": "object", "properties": {
            "name": {"type": "string", "description": "星官中文名，如 北斗"},
        }, "required": ["name"]},
        handler=_asterism,
        category="sky",
    )
