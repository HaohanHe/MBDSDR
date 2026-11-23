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

八个工具的分工
--------------
- `sky_whats_here`      ：我有指向，问这一片有什么（视场枚举）
- `sky_object_lookup`   ：我有名字，问它是谁（身份查询）
- `sky_asterism_lookup` ：我有星官名，问它由什么组成（结构查询）
- `sky_culture_lookup`  ：同一颗星在不同天空文化下各叫什么（多文化查询）
- `sky_meteor_showers`  ：我问今晚，答哪场流星雨在活动、辐射点在哪
- `sky_tonight`         ：我问今晚能不能看，答云量与极光；缺测就说不猜
- `sky_observing_sites` ：我在哪，附近有哪些有官方坐标的台址/公众点（距离参照）
- `sky_dso_lookup`      ：那团模糊是什么（梅西耶/亮 NGC：角径、视场、星等）
- `sky_pose_fuse`       ：多条带时间/精度的位姿观测 → 融合位姿（或明确不可用）
- `sky_identify_at`     ：给定地平指向 → 视场内的肉眼可见恒星（认星）
- `sky_solve_status`    ：星点解算器是否可用（tetra3 / ASTAP，如实报）
- `sky_health`          ：当前指向链路的健康级别与降级原因
- `sky_calibration_save`：校准并落盘指向档案（唯一的写工具，走确认门）
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional

from .tool_results import ToolResult

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

#: 多文化查询的返回上限：条目按文化/组独立，不合并，但也得整份塞进输出预算。
MAX_CULTURE_GROUPS_RETURNED = 6
MAX_CULTURE_MATCHES_RETURNED = 5
MAX_CULTURE_HIPS_RETURNED = 24

#: 按 HIP 查多文化时单次最多返回多少个文化条目。
#:
#: 文化包扩到 30 个后，北极星（HIP 11767）命中 21 个包，全量输出约 8186 字符，
#: 超过 tool_output_max_chars 的 8000 预算被头尾截断——JSON 直接变碎片。
#: 宁可少给几个文化，也要让整份 JSON 完整可解析；其余文化由调用方用
#: ``culture`` 参数逐个筛选。
MAX_CULTURE_ITEMS = 10

#: 每个文化条目最多保留多少个所属组（groups_omitted 如实报告省略数）。
MAX_CULTURE_GROUPS_PER_ITEM = 2

#: 核心体系优先出现；其余文化条目按 id 排序补齐。
CORE_CULTURE_ORDER = (
    "iau_88",
    "chinese_xingguan",
    "japanese",
    "indian",
    "norse",
    "indian_nakshatras",
    "seri",
)


def _culture_rank(culture_id: str):
    """排序键：核心体系在前（按声明顺序），其余按 id 字典序。"""
    try:
        return (0, CORE_CULTURE_ORDER.index(culture_id))
    except ValueError:
        return (1, culture_id)


#: 携带 ``jd_ut1`` 的工具接受的时间范围：2000-01-01 .. 2100-01-01（UT1 粗边界）。
#:
#: 不是天文限制，是**输入卫生**：Windows 的 ``datetime.fromtimestamp`` 对
#: 超大/超小时间戳抛 ``OSError [Errno 22]``，模型随手给个 ``jd_ut1=1`` 就会
#: 把异常抛到工具层。这里先拒，并告诉调用方合法区间。
JD_MIN = 2451544.5
JD_MAX = 2488069.5


def _reject_bad_jd(raw: Any) -> Optional[Dict[str, Any]]:
    """``jd_ut1`` 参数体检：非数值/非有限/越界都返回结构化失败。"""
    try:
        jd = float(raw)
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid_jd",
                "reason": f"jd_ut1 必须是儒略日数值，收到 {raw!r}"}
    if not math.isfinite(jd) or not (JD_MIN <= jd <= JD_MAX):
        return {"ok": False, "error": "jd_out_of_range",
                "reason": (f"jd_ut1 超出可换算区间 {JD_MIN}..{JD_MAX}"
                           "（2000-01-01 .. 2100-01-01），收到 "
                           f"{jd!r}；拒绝换算到本地时间")}
    return None


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


def _finite_number(value: Any) -> Optional[float]:
    """转有限 float；非数值/NaN/Inf 返回 None（调用方负责给原因）。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _failure(code: str, reason: str) -> ToolResult:
    """统一失败信封：``<code>（<人话原因>）``，error 同时带稳定码。"""
    return ToolResult(False, f"{code}（{reason}）", error=code)


#: 校准档案写入失败时给模型的下一步；键沿用 ``sky_calibration`` 的既有错误码，
#: 不新增错误码（``mbdsdr_ai/error_catalog.py`` 已登记这些码）。
_CALIBRATION_NEXT_STEP = {
    "too_few_points": "至少给两个不同方向的校准点（夹角 ≥ 5°）后重试。",
    "pathological": "换两个夹角足够大且不接近天顶的方向，再重新校准。",
    "residual_too_large": "核对观测目标与读数后重新校准；不要放宽阈值硬存。",
    "bad_path": "给一个可写的 JSON 文件路径（目录不存在会自动创建）后重试。",
    "write_failed": "检查目标目录权限与磁盘空间后重试；原档案未被改动。",
    "verify_failed": "检查磁盘/编码后重试；目标档案未被替换。",
    "invalid": "检查设备名、视场与校准来源后重试。",
}
_CALIBRATION_DEFAULT_NEXT_STEP = (
    "按 reason 修正 observations/device_id/fov_deg/source 后重试。")


def _calibration_next_step(code: str) -> str:
    return _CALIBRATION_NEXT_STEP.get(code, _CALIBRATION_DEFAULT_NEXT_STEP)


def _parse_pose_observations(raw: Any):
    """把工具参数里的观测数组转成 ``PoseObservation`` 序列。

    返回 ``(observations, code, reason)``：结构/数值非法时 observations 为
    None，并给出稳定错误码与人话原因；语义问题（陈旧/低精度/相互抵消）交给
    ``fuse_poses`` 自己如实拒绝，这里不预判朝向。
    """
    from .sky_pointing import PoseObservation

    if not isinstance(raw, (list, tuple)):
        return None, "bad_observations", (
            "observations 必须是数组，每项含 source/t_utc/az_deg/alt_deg/"
            f"accuracy_deg；收到 {type(raw).__name__}")
    required = ("source", "t_utc", "az_deg", "alt_deg", "accuracy_deg")
    out: List[Any] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            return None, "bad_observations", (
                f"第 {i} 项不是对象，收到 {type(item).__name__}")
        missing = [k for k in required if k not in item]
        if missing:
            return None, "bad_observations", (
                f"第 {i} 项缺少字段 {', '.join(missing)}")
        t = _finite_number(item["t_utc"])
        az = _finite_number(item["az_deg"])
        alt = _finite_number(item["alt_deg"])
        acc = _finite_number(item["accuracy_deg"])
        roll_raw = item.get("roll_deg", 0.0)
        roll = _finite_number(roll_raw)
        if None in (t, az, alt, acc, roll):
            return None, "bad_observations", (
                f"第 {i} 项的 t_utc/az_deg/alt_deg/accuracy_deg/roll_deg "
                "必须是有限数值（不允许 NaN/Inf）")
        fov: Optional[float] = None
        if item.get("fov_deg") is not None:
            fov = _finite_number(item["fov_deg"])
            if fov is None:
                return None, "bad_observations", (
                    f"第 {i} 项的 fov_deg 必须是有限数值")
        out.append(PoseObservation(
            source=str(item["source"]), t_utc=float(t), az_deg=float(az),
            alt_deg=float(alt), accuracy_deg=float(acc),
            roll_deg=float(roll), fov_deg=fov))
    return out, "", ""


def _pose_level_text(level: str) -> str:
    """融合级别的中文说明；不出现「是否对准」的结论。"""
    return {
        "single": "单一来源粗位姿",
        "fused": "多源融合位姿",
    }.get(level, "无可用位姿")



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


def _groups_with_hip(pack, hip: int):
    """该文化包里连线经过这颗星的所有组（星座/星群/星官）。"""
    return [g for g in pack.groups
            if any(hip in poly for poly in g.lines)]


def _group_payload(group) -> Dict[str, Any]:
    return {
        "group_id": group.group_id,
        "kind": group.kind,
        "names": dict(group.names),
    }


def _hips_payload(hips: List[int]) -> Dict[str, Any]:
    listed = hips[:MAX_CULTURE_HIPS_RETURNED]
    return {
        "hips": listed,
        "hip_count": len(hips),
        "hips_omitted": max(0, len(hips) - len(listed)),
    }


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
            bad = _reject_bad_jd(jd)
            if bad is not None:
                return bad
            jd = float(jd)
            time_note = "由调用参数给出"

        az = float(args["az_deg"])
        alt = float(args["alt_deg"])
        fov = float(args["fov_deg"])
        limit_mag = float(args.get("limit_mag") or DEFAULT_LIMIT_MAG)

        if not (0.0 < fov <= 180.0):
            raise ValueError(f"视场角必须在 (0, 180] 度内，收到 {fov}")

        culture_raw = args.get("culture_ids")
        culture_requested = culture_raw is not None
        requested_cultures: tuple = ()
        unknown_cultures: tuple = ()
        if culture_requested:
            from .skyengine.sky_culture import split_culture_ids

            requested_cultures, unknown_cultures = split_culture_ids(culture_raw)

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
        listed = stars[:MAX_STARS_RETURNED]
        if culture_requested:
            from .skyengine.sky_culture import names_for_hip

            for star in listed:
                hip = star["hip"]
                all_names = names_for_hip(hip) if hip > 0 else {}
                # 缺名的文化不出现该键；不回退别家名字，也不拿西名冒充。
                star["culture_names"] = {
                    cid: dict(all_names[cid])
                    for cid in requested_cultures if cid in all_names
                }
        out = {
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
            "stars": listed,
            # 如实告诉模型"这不是全部"：不说的话它会以为天上就这么多星。
            "stars_listed": min(total, MAX_STARS_RETURNED),
            "stars_omitted": max(0, total - MAX_STARS_RETURNED),
            "asterisms": asterisms,
            "catalog": {"name": "HYG v4.1", "license": "CC BY-SA 4.0"},
            "hint": ("讲距离用 light_years（光走了多少年），不要念 parsec；"
                     "light_years 为 null 表示星表没有可靠距离，"
                     "必须说『距离未知』，不许填 0；"
                     "asterisms 里 truncated=true 的要明说『你只看到其中一段』；"
                     "stars_omitted>0 说明还有更暗的星没列，需要时可抬 limit_mag。"
                     + (" culture_names 按文化分开：某文化没有该星名时不会"
                        "出现该键，不得用别家名字顶替。"
                        if culture_requested else "")),
        }
        if culture_requested:
            out["unknown_cultures"] = list(unknown_cultures)
        return out

    reg.register(
        name="sky_whats_here",
        description=(
            "视场枚举：给定指向(方位角/地平高度)与视场角，列出这一片天里"
            "肉眼可见的恒星与它们所属的中国星官。返回每颗星的名称/星等/"
            "距离/光行时，以及星官成员在视场内外的比例。"
            "传入 culture_ids 时另按文化取星名：每颗星带 culture_names"
            "（{文化id: {语言: 名称}}），只看请求的文化；某文化没有该星名时"
            "该键不出现，绝不回退成别家名字；未知 id 在 unknown_cultures 列出。"
            "用于回答『我现在指的这一片有什么』『那几颗连起来像什么』"
            "『这颗星在其他文化里叫什么』。"
            "站址与时刻默认取当前观测站与时间引擎；要用别的时间/地点就显式传。"
            "只会读取星表，不改任何状态。"),
        parameters={"type": "object", "properties": {
            "az_deg": {"type": "number", "description": "指向方位角（度，北=0 东=90）"},
            "alt_deg": {"type": "number", "description": "指向地平高度（度，地平线以上为正）"},
            "fov_deg": {"type": "number", "description": "视场角（度）。目镜约 2.3，寻星镜约 8，手机广角约 60-70"},
            "limit_mag": {"type": "number", "description": f"极限星等，默认 {DEFAULT_LIMIT_MAG}"},
            "culture_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": ("可选：要取星名的天空文化 id 列表"
                                "（如 iau_88 / chinese_xingguan）；"
                                "不传则不返回文化名")},
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

        # 推荐视场（FR-01）：跨度 × 1.2 留边，**向上**取整到 5°。
        # 必须向上——四舍五入会把 5.51°×1.2=6.6° 压成 5°，装不下星官，
        # 那样"推荐"就是在骗人（实测踩到：昴宿 span=5.51° 曾算出 fov=5°）。
        # 只用现有 span，不新造第二套跨度算法；无成员（span=0）时留 null。
        recommended_fov = (int(math.ceil(span * 1.2 / 5.0) * 5)
                           if span > 0 else None)
        fov_note = (
            f"这个星官跨度约 {span:.0f}°，用约 {recommended_fov}° 的视场看得全"
            if recommended_fov is not None
            else "成员星数据不足，无法给出推荐视场"
        )

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
            "recommended_fov_deg": recommended_fov,
            "fov_note": fov_note,
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

    # ------------------------------------------------------------------
    # 4) 流星雨：今晚有没有、辐射点在哪
    # ------------------------------------------------------------------
    def _meteor_showers(args: Dict[str, Any]):
        import datetime as _dt

        from .astronomy import meteor_showers as _ms
        from .celestial_geometry import (
            azalt_from_vec, j2000_to_altaz, vec_from_radec)
        from .skyengine import jtime as _jt

        jd = args.get("jd_ut1")
        if jd is None:
            jd = _current_jd(agent)
            time_note = "取自时间引擎/当前时刻"
        else:
            bad = _reject_bad_jd(jd)
            if bad is not None:
                return bad
            jd = float(jd)
            time_note = "由调用参数给出"

        lat = args.get("lat_deg")
        lon = args.get("lon_deg")
        if lat is None or lon is None:
            site = _site_from_observer(agent)
            lat, lon = site["lat_deg"], site["lon_deg"]
            alt_m = site["alt_m"]
            site_note = "取自 agent 观测站"
            site_warning = site["site_warning"]
        else:
            alt_m = float(args.get("alt_m") or 0.0)
            site_note = "由调用参数给出"
            site_warning = ""

        gs = _station_from(lat, lon, alt_m)
        now = _dt.datetime.fromtimestamp(
            _jt.jd_to_unix(jd), _dt.timezone.utc)
        rows = []
        for s, frac in _ms.active_showers(now):
            az, alt = azalt_from_vec(j2000_to_altaz(
                vec_from_radec(s.ra_deg, s.dec_deg), gs, jd))
            rows.append({
                "code": s.code,
                "name_zh": s.name_zh,
                "name_en": s.name_en,
                "activity": round(float(frac), 2),
                "zhr_peak": s.zhr_peak,
                "peak_utc": f"{s.peak_month:02d}-{s.peak_day:02d}",
                "radiant_az_deg": round(float(az), 1),
                "radiant_alt_deg": round(float(alt), 1),
                "above_horizon": bool(float(alt) > 0.0),
                "parent": s.parent,
                "velocity_kms": s.velocity_kms,
            })
        return {
            "ok": True,
            "date_utc": now.strftime("%Y-%m-%d"),
            "jd_ut1": jd,
            "time_note": time_note,
            "site": {"lat_deg": lat, "lon_deg": lon, "alt_m": alt_m,
                     "note": site_note, "site_warning": site_warning},
            "showers": rows,
            "note": ("activity 是活动包络（1=峰值，0=活动期边界），"
                     "不是今夜每小时流星数；zhr_peak 是理想暗空下的峰值"
                     "参考值，实际可见数受月光/云量/光污染影响。"
                     "radiant 在地平线下时 above_horizon=false。"),
        }

    reg.register(
        name="sky_meteor_showers",
        description=(
            "流星雨查询：给时刻（可选给站址），返回当天活动的流星雨、"
            "活动包络强度、峰值 ZHR、辐射点方位/高度与是否在地平线上。"
            "用于回答『今晚有流星雨吗』『英仙座流星雨往哪看』。"
            "只读，不改任何状态；没有活动项时返回空列表而不是编一场。"),
        parameters={"type": "object", "properties": {
            "jd_ut1": {"type": "number",
                       "description": "可选：UT1 儒略日，默认当前时刻"},
            "lat_deg": {"type": "number", "description": "可选：覆盖观测站纬度"},
            "lon_deg": {"type": "number", "description": "可选：覆盖观测站经度"},
            "alt_m": {"type": "number", "description": "可选：观测站海拔（米）"},
        }},
        handler=_meteor_showers,
        category="sky",
    )

    # ------------------------------------------------------------------
    # 5) 今晚能不能看：云量 + 极光粗判 + 单时刻月光；缺测不猜
    # ------------------------------------------------------------------
    def _moon_sample(lat: float, lon: float):
        """本地历表算单时刻月球照亮比例/高度；算不出就返回 None（不猜）。

        不联网下载历表：``get_moon_position`` 在缺文件且离线时返回 None，
        异常也一律吞掉——月光是附加信息，不能拖垮云量结论。
        """
        try:
            from .new_spacetime import jd_to_utc
            from .solar_system import GroundStation as _SStation
            from .solar_system import get_moon_position

            when = jd_to_utc(_current_jd(agent))
            pos = get_moon_position(when, _SStation(lat, lon, 0.0))
            if pos is None or pos.illumination is None:
                return None
            return {
                "illumination": float(pos.illumination),
                "alt_deg": float(pos.alt_deg),
                "at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source": f"local_ephemeris:{pos.backend}",
            }
        except Exception:
            return None

    def _tonight(args: Dict[str, Any]):
        from datetime import datetime, timezone

        from . import space_weather as _sw
        from .astronomy import tonight as _tn

        site = _site_from_observer(agent)
        lat = args.get("lat_deg")
        lon = args.get("lon_deg")
        if lat is None or lon is None:
            lat, lon = site["lat_deg"], site["lon_deg"]
            site_note = "取自 agent 观测站"
            site_warning = site["site_warning"]
        else:
            lat, lon = float(lat), float(lon)
            site_note = "由调用参数给出"
            site_warning = ""

        allow_live = bool(args.get("allow_live", True))
        cached = _sw.latest_snapshot(max_age_h=0.25)
        stale_site = ""
        if cached is not None and not _sw.snapshot_station_matches(
                cached[0], lat, lon):
            # 云量是站点量：别站的快照不能拿来判当前站址的天空。
            st = (cached[0].get("station") or {})
            stale_site = (f"站址不符（快照 {st.get('lat')}, {st.get('lon')}）")
            cached = None
        if cached is not None:
            snap, path = cached
            source_note = f"新鲜快照（15 分钟内，站址匹配）：{path}"
        elif allow_live:
            # 不落盘：工具调用不应留下用户没要求的文件。
            snap = _sw.collect_snapshot(lat=lat, lon=lon)
            source_note = ("现采（未落盘）" if not stale_site else
                           f"现采（未落盘）；{stale_site}，不拿别站云量顶替")
        else:
            snap = None
            source_note = ("没有新鲜快照，且 allow_live=false" if not stale_site
                           else f"{stale_site}且 allow_live=false，不拿别站云量顶替")

        report = _tn.assess_tonight(snap, lat, lon,
                                    moon=_moon_sample(float(lat), float(lon)),
                                    at=datetime.now(timezone.utc))
        out = dict(report)
        out.update({
            "ok": True,
            "source_note": source_note,
            "site": {"lat_deg": lat, "lon_deg": lon, "note": site_note,
                     "site_warning": site_warning},
        })
        return out

    reg.register(
        name="sky_tonight",
        description=(
            "今夜可看性：给站址（可选覆盖），返回云量判定、极光粗判、"
            "采样时刻的月光、处于活动期的流星雨与一句结论。用于回答"
            "『今晚适合看星吗』『能看到极光吗』『今晚有什么流星雨』。"
            "云量取采集时刻的实况，不是整夜预报；没有云量时说缺测并明确"
            "『不按晴天假设』；极光只用 Kp 与地磁纬度的经验门槛，不是"
            "极光椭圆预报；月光只按采样时刻算，不外推整夜；流星雨只给"
            "活动期与峰值 ZHR，不给『今晚每小时几颗』。优先读 15 分钟内的"
            "环境快照，没有才现采。"),
        parameters={"type": "object", "properties": {
            "lat_deg": {"type": "number", "description": "可选：覆盖观测站纬度"},
            "lon_deg": {"type": "number", "description": "可选：覆盖观测站经度"},
            "allow_live": {"type": "boolean",
                           "description": "可选：没有新鲜快照时是否现采（默认 true）"},
        }},
        handler=_tonight,
        category="sky",
    )

    # ------------------------------------------------------------------
    # 6) 附近台址：官方坐标的科研台址 + 公众点，access 分开说
    # ------------------------------------------------------------------
    def _observing_sites(args: Dict[str, Any]):
        from .astronomy import observing_sites as _obs

        site = _site_from_observer(agent)
        lat = args.get("lat_deg")
        lon = args.get("lon_deg")
        if lat is None or lon is None:
            lat, lon = site["lat_deg"], site["lon_deg"]
            site_note = "取自 agent 观测站"
            site_warning = site["site_warning"]
        else:
            lat, lon = float(lat), float(lon)
            site_note = "由调用参数给出"
            site_warning = ""

        limit = int(args.get("limit") or 3)
        rows = _obs.nearest_sites(lat, lon, limit=limit)
        return {
            "ok": True,
            "site": {"lat_deg": lat, "lon_deg": lon, "note": site_note,
                     "site_warning": site_warning},
            "sites": rows,
            "caveat": ("内置点分两类：access=research 是科研台址，不对外开放；"
                       "access=public 是科普基地/景区，也需预约、开放日或门票，"
                       "public 不代表随时能进。两类都只用于距离参照，"
                       "坐标来源见各项 source_url。"),
        }

    reg.register(
        name="sky_observing_sites",
        description=(
            "按站址返回最近的台址与公众科普/观星点（内置 4 个科研台址 + "
            "2 个公众点，均有官方坐标与来源 URL），带球面距离。"
            "access=research 的科研台址不对外开放；access=public 也需预约"
            "或开放日，不是旅游推荐。用于回答『附近有哪些天文台/观星地』。"),
        parameters={"type": "object", "properties": {
            "lat_deg": {"type": "number", "description": "可选：覆盖观测站纬度"},
            "lon_deg": {"type": "number", "description": "可选：覆盖观测站经度"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20,
                      "description": "可选：返回数量，默认 3"},
        }},
        handler=_observing_sites,
        category="sky",
    )

    # ------------------------------------------------------------------
    # 7) 多文化查询：同一颗星在不同天空文化下各叫什么
    # ------------------------------------------------------------------
    def _culture_lookup(args: Dict[str, Any]):
        from .skyengine import sky_culture as sc

        hip_raw = args.get("hip")
        name = str(args.get("name") or "").strip()
        if hip_raw is None and not name:
            raise ValueError("hip 与 name 至少给一个：hip 查星，name 查组/西名")
        if hip_raw is not None and name:
            raise ValueError("hip 与 name 只能二选一，不要同时给")

        culture_id = str(args.get("culture") or "").strip()
        registry = sc.load_registry()
        packs = list(registry)
        if culture_id:
            pack = registry.get(culture_id)
            if pack is None:
                return {
                    "ok": False,
                    "matched": False,
                    "reason": f"未知天空文化 id {culture_id!r}",
                    "available_cultures": list(registry.ids()),
                }
            packs = [pack]

        def _header(pack) -> Dict[str, Any]:
            return {
                "culture": pack.id,
                "display_name": pack.display_name,
                "license": pack.license,
                "license_url": pack.license_url,
            }

        if hip_raw is not None:
            if isinstance(hip_raw, bool):
                raise ValueError("hip 必须是正整数")
            try:
                hip = int(hip_raw)
            except (TypeError, ValueError):
                raise ValueError(f"hip 必须是整数，收到 {hip_raw!r}") from None
            if hip <= 0:
                raise ValueError(f"hip 必须是正整数，收到 {hip_raw!r}")

            cultures: List[Dict[str, Any]] = []
            for pack in packs:
                entry = pack.names.get(hip)
                groups = _groups_with_hip(pack, hip)
                if not entry and not groups:
                    continue
                item = _header(pack)
                item.update({
                    "names": dict(entry) if entry else {},
                    "groups": [_group_payload(g)
                               for g in groups[:MAX_CULTURE_GROUPS_PER_ITEM]],
                    "group_count": len(groups),
                    "groups_omitted": max(
                        0, len(groups) - MAX_CULTURE_GROUPS_PER_ITEM),
                })
                cultures.append(item)

            if not cultures:
                scope = f"文化 {culture_id!r}" if culture_id else "任何文化包"
                return {
                    "ok": False,
                    "matched": False,
                    "hip": hip,
                    "reason": f"HIP {hip} 不在{scope}的名称或连线里",
                    "hint": ("只报数据里真实出现的 HIP；不返回最近邻，"
                             "也不猜一个名字。"),
                }
            # 核心体系先出现，其余按 id 排序——模型先讲最该讲的那几种。
            cultures.sort(key=lambda c: _culture_rank(c["culture"]))
            total = len(cultures)
            listed = cultures[:MAX_CULTURE_ITEMS]
            truncated = total > len(listed)
            out = {
                "ok": True,
                "matched": True,
                "query": {"hip": hip},
                "culture_count": total,
                "cultures": listed,
                "cultures_shown": len(listed),
                "cultures_truncated": truncated,
                "note": ("每个文化各自命名，绝不合并成一个名字；"
                         "names 的语言键由该文化数据决定。"),
            }
            if truncated:
                out["hint"] = (
                    f"只列出 {len(listed)}/{total} 个文化（核心体系优先）；"
                    "其余文化未返回，用 culture 参数筛选其余文化逐个查，"
                    "不得把这里当成全量。")
            return out

        target = name.lower()
        group_hits: List[Dict[str, Any]] = []
        star_hits: List[Dict[str, Any]] = []
        for pack in packs:
            for group in pack.groups:
                if any(str(v).strip().lower() == target
                       for v in group.names.values()):
                    item = _header(pack)
                    item.update({"group": _group_payload(group)})
                    item.update(_hips_payload(sorted({
                        int(h) for poly in group.lines for h in poly})))
                    group_hits.append(item)
            for hip, entry in pack.names.items():
                if not any(str(v).strip().lower() == target
                           for v in entry.values()):
                    continue
                groups = _groups_with_hip(pack, hip)
                if groups:
                    for group in groups[:MAX_CULTURE_GROUPS_RETURNED]:
                        item = _header(pack)
                        item.update({"hip": int(hip),
                                     "group": _group_payload(group)})
                        item.update(_hips_payload(sorted({
                            int(h) for poly in group.lines for h in poly})))
                        star_hits.append(item)
                else:
                    item = _header(pack)
                    item.update({"hip": int(hip), "group": None})
                    item.update(_hips_payload([int(hip)]))
                    star_hits.append(item)

        # 组名优先：名字命中组名时不再按成员星重复列举，避免同一组出现两次。
        matched_by = "group_name" if group_hits else "star_name"
        hits = group_hits or star_hits
        if not hits:
            scope = f"文化 {culture_id!r}" if culture_id else "任何文化包"
            return {
                "ok": False,
                "matched": False,
                "name": name,
                "reason": f"{name!r} 不在{scope}的组名或星名里",
                "hint": ("可传文化 id 缩小范围，或改用 hip 查；"
                         "无匹配时不返回最近邻凑数。"),
            }
        listed = hits[:MAX_CULTURE_MATCHES_RETURNED]
        return {
            "ok": True,
            "matched": True,
            "query": {"name": name},
            "matched_by": matched_by,
            "match_count": len(hits),
            "matches": listed,
            "matches_omitted": max(0, len(hits) - len(listed)),
            "note": ("每项独立标注所属文化与许可，不跨文化合并；"
                     "hips 是该组连线的成员，不是位置排序。"),
        }

    reg.register(
        name="sky_culture_lookup",
        description=(
            "多文化天空查询：给 HIP 号或名称，返回同一颗星在不同天空文化"
            "（IAU 88、中国星官、日本、印度、北欧、毛利、萨米等包）下各自"
            "的名称、所属组（星座/星群/星官）与该文化的许可证。按名称查时"
            "返回匹配的组与成员 HIP 列表。文化之间绝不合并名称；查不到就"
            "明说查不到，不返回最近邻凑数。只读本地数据，不写盘、不联网。"),
        parameters={"type": "object", "properties": {
            "hip": {"type": "integer", "minimum": 1,
                    "description": "Hipparcos 编号（如 11767 是北极星）"},
            "name": {"type": "string",
                     "description": "组名或西名（如 Ursa Minor / Polaris）"},
            "culture": {"type": "string",
                        "description": "可选：只查某个文化 id（如 japanese）"},
        }},
        handler=_culture_lookup,
        category="sky",
    )

    # ------------------------------------------------------------------
    # 8) DSO 查询：那团模糊是什么（梅西耶/亮 NGC）
    # ------------------------------------------------------------------
    def _dso_lookup(args: Dict[str, Any]):
        """按名称或天区查深空天体。数据只有公开来源，距离缺失就说缺失。"""
        import json
        from pathlib import Path

        data_path = Path(__file__).resolve().parent / "data" / "dso_catalog.json"
        try:
            catalog = json.loads(data_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"DSO 目录不可读：{exc}") from exc
        objects = catalog.get("objects") or []
        if not objects:
            raise ValueError("DSO 目录为空——不编造天体")

        query = str(args.get("name") or "").strip().lower()
        az = args.get("az_deg")
        alt = args.get("alt_deg")
        fov = args.get("fov_deg")

        def _names(o):
            out = []
            for key in ("name", "name_en", "zh_name"):
                v = o.get(key)
                if v:
                    out.append(str(v).lower())
            for v in (o.get("aliases_en") or []) + (o.get("aliases_zh") or []):
                if v:
                    out.append(str(v).lower())
            cross = o.get("cross_ids") or {}
            for key in ("m", "ngc", "ic"):
                if cross.get(key) is not None:
                    out.extend([f"{key}{cross[key]}".lower(),
                                f"{key} {cross[key]}".lower()])
            return out

        hits = []
        if query:
            for o in objects:
                if any(query == n or query in n for n in _names(o)):
                    hits.append(o)
            if not hits:
                return {
                    "ok": False,
                    "reason": f"DSO 目录里没有 {query!r}",
                    "hint": "试试 M31 / M42 / 仙女座星系，或给 az_deg + alt_deg 按天区查",
                }
        elif az is not None and alt is not None:
            # 按天区查：需要站址+时刻把 az/alt 转成 J2000 才能与目录比。
            # 这里走视场枚举的思路：用当前观测者与时刻算每个 DSO 的 az/alt。
            from .celestial_geometry import (
                GroundStation, vec_from_azalt, vec_from_radec,
                j2000_to_altaz, azalt_from_vec)
            from .new_spacetime import utc_to_jd
            import datetime

            def _sep_deg(az1, alt1, az2, alt2):
                """两地平方向的角距：向量点积，与 span_deg 同一数学口径。"""
                v1 = vec_from_azalt(float(az1), float(alt1))
                v2 = vec_from_azalt(float(az2), float(alt2))
                d = max(-1.0, min(1.0, float(v1 @ v2)))
                return math.degrees(math.acos(d))

            lat = args.get("lat_deg")
            lon = args.get("lon_deg")
            if lat is None or lon is None:
                obs = getattr(agent, "observer", None)
                if obs is None:
                    raise ValueError("按天区查 DSO 需要站址：请传 lat_deg/lon_deg "
                                     "或先设置观测站")
                lat, lon = obs.latitude_deg, obs.longitude_deg
            station = GroundStation(float(lon), float(lat), 0.0, "dso_lookup")
            jd = args.get("jd_ut1") or utc_to_jd(
                datetime.datetime.now(datetime.timezone.utc))
            fov = float(fov) if fov is not None else 30.0
            if fov <= 0 or fov > 180:
                raise ValueError("fov_deg 必须在 (0, 180] 之间")
            for o in objects:
                if o.get("ra_deg") is None or o.get("dec_deg") is None:
                    continue
                v = j2000_to_altaz(
                    vec_from_radec(float(o["ra_deg"]), float(o["dec_deg"])),
                    station, float(jd))
                o_az, o_alt = azalt_from_vec(v)
                if float(o_alt) < -6.0:
                    continue
                if _sep_deg(az, alt, float(o_az), float(o_alt)) <= fov / 2.0:
                    hits.append(o)
            if not hits:
                return {
                    "ok": True, "count": 0, "objects": [],
                    "note": (f"以 {float(az):g}°/{float(alt):g}° 为中心 "
                             f"{fov:g}° 内没有目录中的 DSO（目录仅 117 条："
                             "Messier 全量 + 少数亮 NGC），没有不等于天上没有"),
                }
        else:
            raise ValueError("请给 name（名称/编号）或 az_deg + alt_deg（天区）")

        listed = []
        for o in hits[:12]:
            size = o.get("size_arcmin")
            dist_pc = o.get("distance_pc")
            ly = _light_years(dist_pc)
            rec = {
                "name": o.get("name") or o.get("name_en") or o.get("zh_name"),
                "zh_name": o.get("zh_name"),
                "type": o.get("dso_type") or o.get("catalog_type"),
                "ra_deg": o.get("ra_deg"),
                "dec_deg": o.get("dec_deg"),
                "magnitude": o.get("magnitude"),
                "size_arcmin": size,
                "constellation": o.get("constellation"),
                # 距离来自 OpenNGC 自带视差；缺失/非正一律 None，不编。
                "distance_pc": dist_pc if ly is not None else None,
                "light_years": ly,
                "distance_source": o.get("distance_source"),
            }
            # σ 源不提供时省略字段（null 语义），避免把 12 条对象撑爆输出预算。
            if o.get("distance_uncertainty_pc") is not None:
                rec["distance_uncertainty_pc"] = o["distance_uncertainty_pc"]
            # 长轴方向只对星系说（与桌面信息卡同一口径）；星团/星云即使
            # 目录里有 PosAng 也不展示，避免把「成员星分布」说成星系朝向。
            if rec["type"] == "galaxy" and o.get("position_angle_deg") is not None:
                rec["position_angle_deg"] = o["position_angle_deg"]
                rec["position_angle_source"] = o.get("position_angle_source")
            # 角径 → 推荐视场（与星官同一口径：×1.2 向上取整到 5°）。
            # 单位换算：角分 → 度。缺角径就留 null，不猜。
            if size is not None and float(size) > 0:
                span_deg = float(size) / 60.0
                rec["recommended_fov_deg"] = int(math.ceil(span_deg * 1.2 / 5.0) * 5)
            else:
                rec["recommended_fov_deg"] = None
            listed.append(rec)

        return {
            "ok": True,
            "count": len(listed),
            "objects_omitted": max(0, len(hits) - len(listed)),
            "objects": listed,
            "catalog": {"name": "dso_catalog", "source": "OpenNGC",
                        "license": "CC BY-SA 4.0", "count": len(objects)},
            "note": ("DSO 目录仅 117 条（Messier + 亮 NGC），查不到≠天上没有；"
                     "距离取自 OpenNGC 自带视差（71/117），缺失说未知，不要编"),
            "hint": ("角径→『约 X 度，适合 Y° 视场』；有距离说『约 Z 光年"
                     "（目录视差）』；星系有长轴方向说『约 N°（目录）』；"
                     "null 说未知，不要估"),
        }

    reg.register(
        name="sky_dso_lookup",
        description=(
            "深空天体查询：给梅西耶编号/中文名/英文俗名（如 M42 / 猎户座大星云），"
            "返回身份、赤经赤纬、星等、角径、所属星座与推荐观测视场；"
            "有目录数据的再给距离与星系长轴方向（带来源，缺失即 null）。"
            "也可给 az_deg+alt_deg+fov_deg 按当前天区查。"
            "距离取自 OpenNGC 自带视差（71/117 有值，带来源）；缺失必须说"
            "『距离未知』，不许编。"
            "目录只含 Messier 全量与少数亮 NGC（117 条），查不到不等于天上没有。"
            "只读本地公开数据（OpenNGC CC BY-SA 4.0），不写盘不联网。"),
        parameters={"type": "object", "properties": {
            "name": {"type": "string",
                     "description": "编号或名称，如 M31 / M42 / 仙女座星系"},
            "az_deg": {"type": "number", "description": "方位角（度，0=北）；与 alt_deg 一起用"},
            "alt_deg": {"type": "number", "description": "高度角（度）；与 az_deg 一起用"},
            "fov_deg": {"type": "number", "description": "视场（度，0-180），默认 30"},
            "lat_deg": {"type": "number", "description": "按天区查时的观测站纬度；不传则用 agent 当前观测站"},
            "lon_deg": {"type": "number", "description": "按天区查时的观测站经度；不传则用 agent 当前观测站"},
            "jd_ut1": {"type": "number", "description": "按天区查时的儒略日；不传则用当前时刻"},
        }},
        handler=_dso_lookup,
        category="sky",
    )

    # ------------------------------------------------------------------
    # 9) 位姿融合：把多条带时间/精度的读数合成一个位姿，或如实说不可用
    # ------------------------------------------------------------------
    def _pose_fuse(args: Dict[str, Any]):
        from .sky_pointing import fuse_poses

        observations, code, reason = _parse_pose_observations(
            args.get("observations"))
        if observations is None:
            return _failure(code, reason)

        now = args.get("now_utc")
        if now is None:
            now_value = time.time()
            time_note = "未提供 now_utc，按当前 UTC Unix 秒判定陈旧"
        else:
            now_value = _finite_number(now)
            if now_value is None:
                return _failure("bad_now_utc",
                                f"now_utc 必须是有限 Unix 秒，收到 {now!r}")
            time_note = "由调用参数给出"

        pose = fuse_poses(observations, now_value)
        return {
            "ok": True,
            "valid": bool(pose.get("valid")),
            "az_deg": pose.get("az_deg"),
            "alt_deg": pose.get("alt_deg"),
            "roll_deg": pose.get("roll_deg"),
            "fov_deg": pose.get("fov_deg"),
            "accuracy_deg": pose.get("accuracy_deg"),
            "sources": pose.get("sources", []),
            "dropped": pose.get("dropped", []),
            "level": pose.get("level", "none"),
            "level_text": _pose_level_text(pose.get("level", "none")),
            "reason": pose.get("reason", ""),
            "now_utc": now_value,
            "time_note": time_note,
            "units": "角度为度，时间为 UTC Unix 秒",
            "hint": ("valid=true 时 az_deg/alt_deg 可直接用于显示；accuracy_deg "
                     "是统计标准误，不是保证误差；valid=false 时按 reason 如实"
                     "说明不可用，不要猜朝向。"),
        }

    reg.register(
        name="sky_pose_fuse",
        description=(
            "位姿融合：给定多条传感器观测（每项含 source、t_utc、az_deg、"
            "alt_deg、accuracy_deg，可选 roll_deg/fov_deg），按精度加权融合成"
            "一个地平位姿，并逐条报告哪些观测被丢弃及原因（陈旧/低精度/非法/"
            "相互抵消）。全部不可用时 valid=false 且给 reason，不猜朝向。"
            "只做纯数值融合，不读写设备、不写盘。"),
        parameters={"type": "object", "properties": {
            "observations": {
                "type": "array",
                "description": "位姿观测数组",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string",
                                   "description": "来源标签，如 imu / gnss / camera"},
                        "t_utc": {"type": "number",
                                  "description": "观测时刻（UTC Unix 秒）"},
                        "az_deg": {"type": "number",
                                   "description": "方位角（度，北=0 东=90）"},
                        "alt_deg": {"type": "number",
                                    "description": "地平高度（度，-90..90）"},
                        "accuracy_deg": {"type": "number",
                                         "description": "自报 1σ 精度（度，>0）"},
                        "roll_deg": {"type": "number",
                                     "description": "画面横滚（度），默认 0"},
                        "fov_deg": {"type": "number",
                                    "description": "可选：该观测的视场直径（度）"},
                    },
                    "required": ["source", "t_utc", "az_deg", "alt_deg",
                                 "accuracy_deg"],
                },
            },
            "now_utc": {"type": "number",
                        "description": "当前 UTC Unix 秒；缺省用系统当前时刻"},
        }, "required": ["observations"]},
        handler=_pose_fuse,
        category="sky",
        write=False,
    )

    # ------------------------------------------------------------------
    # 10) 指向即识别：给定地平指向，返回视场内的肉眼可见恒星
    # ------------------------------------------------------------------
    def _identify_at(args: Dict[str, Any]):
        from .sky_pointing import identify_direction

        az = _finite_number(args.get("az_deg"))
        alt = _finite_number(args.get("alt_deg"))
        fov = _finite_number(args.get("fov_deg"))
        if az is None or alt is None or fov is None:
            return _failure("bad_query",
                            "az_deg/alt_deg/fov_deg 必须是有限数值")
        if not (-90.0 <= alt <= 90.0):
            return _failure("bad_query",
                            f"alt_deg 越界（{alt}°，合法范围 -90..90）")
        if not (0.0 < fov <= 180.0):
            return _failure("bad_query",
                            f"fov_deg 必须在 (0, 180] 度内，收到 {fov}")

        jd = args.get("jd")
        if jd is None:
            jd_value = _current_jd(agent)
            time_note = "取自时间引擎/当前时刻"
        else:
            bad = _reject_bad_jd(jd)
            if bad is not None:
                return _failure("bad_jd", bad["reason"])
            jd_value = float(jd)
            time_note = "由调用参数给出"

        lat_raw = args.get("lat_deg")
        lon_raw = args.get("lon_deg")
        if (lat_raw is None) != (lon_raw is None):
            return _failure("bad_query",
                            "lat_deg 与 lon_deg 必须同时提供，或都不提供")
        if lat_raw is None:
            try:
                site = _site_from_observer(agent)
            except ValueError as exc:
                return _failure("no_site", str(exc))
            lat, lon = site["lat_deg"], site["lon_deg"]
            site_note = "取自当前观测站"
            site_warning = site["site_warning"]
        else:
            lat = _finite_number(lat_raw)
            lon = _finite_number(lon_raw)
            if lat is None or lon is None:
                return _failure("bad_query",
                                "lat_deg/lon_deg 必须是有限数值")
            site_note = "由调用参数给出"
            site_warning = ""

        result = identify_direction(az, alt, fov, jd_value, lat, lon, limit=3)
        if not result.get("matched"):
            return _failure("no_stars",
                            str(result.get("reason") or "视场内没有可用恒星"))

        return {
            "ok": True,
            "matched": True,
            "query": result.get("query"),
            "site": {"lat_deg": lat, "lon_deg": lon, "note": site_note,
                     "warning": site_warning},
            "time_note": time_note,
            "limit_mag": result.get("limit_mag"),
            "total_matched": result.get("total_matched"),
            "truncated": result.get("truncated"),
            "stars": result.get("stars", []),
            "catalog": result.get("catalog"),
            "hint": ("数值单位为度；angular_distance_deg 是相对指向的角距。"
                     "只说星表里有的星与星官，不要补编目标。"),
        }

    reg.register(
        name="sky_identify_at",
        description=(
            "指向即识别：给定地平指向 az_deg/alt_deg 与视场直径 fov_deg，"
            "返回视场内的肉眼可见恒星（按星等从亮到暗，最多 3 颗）及所属中国"
            "星官、相对指向的角距。站址与时刻默认取当前观测站与时间引擎，"
            "也可显式传 jd/lat_deg/lon_deg。无站址、参数非法或视场内没有"
            "星表恒星时返回结构化失败原因，不拿最近邻凑数。只读星表。"),
        parameters={"type": "object", "properties": {
            "az_deg": {"type": "number",
                       "description": "指向方位角（度，北=0 东=90）"},
            "alt_deg": {"type": "number",
                        "description": "指向地平高度（度，-90..90）"},
            "fov_deg": {"type": "number",
                        "description": "视场直径（度，0-180）"},
            "jd": {"type": "number",
                   "description": "可选：UT1 儒略日，默认当前时刻"},
            "lat_deg": {"type": "number",
                        "description": "可选：观测站纬度；与 lon_deg 同时给"},
            "lon_deg": {"type": "number",
                        "description": "可选：观测站经度；与 lat_deg 同时给"},
        }, "required": ["az_deg", "alt_deg", "fov_deg"]},
        handler=_identify_at,
        category="sky",
        write=False,
    )

    # ------------------------------------------------------------------
    # 11) 解算器状态：装了没有、叫什么、不可用为什么
    # ------------------------------------------------------------------
    def _solve_status(args: Dict[str, Any]):
        from . import sky_solve

        status = sky_solve.solver_status()
        available = bool(status.get("available"))
        return {
            "ok": True,
            "available": available,
            "name": str(status.get("name") or ""),
            "reason": str(status.get("reason") or ""),
            # 已弃用但仍保留一版的适配器（名字 → 替代建议）；当前通常为空。
            "deprecated": dict(status.get("deprecated") or {}),
            "probed": ["tetra3", "astap"],
            "note": ("只探测本机是否安装/可定位解算器；available=false 时"
                     "不得说解算可用，也不得假装已解算。deprecated 里的"
                     "适配器仍可用，只是已知会被替代。"),
        }

    reg.register(
        name="sky_solve_status",
        description=(
            "星点解算器状态：探测本机是否可用 tetra3（Python 库）或 ASTAP"
            "（外部可执行文件，路径由 MBDSDR_ASTAP_PATH 指定）。如实返回"
            "available/name/reason；未安装或不可用时不得说可用；deprecated"
            "列出仍保留一版但已知会被替代的适配器（名字 → 替代建议）。无参数，"
            "只读探测，不安装、不下载、不启动外部进程。"),
        parameters={"type": "object", "properties": {}},
        handler=_solve_status,
        category="sky",
        write=False,
    )

    # ------------------------------------------------------------------
    # 12) 指向链路健康：级别、来源快照、降级原因
    # ------------------------------------------------------------------
    def _health(args: Dict[str, Any]):
        from . import sky_solve
        from .sky_health import health_snapshot

        raw = args.get("observations")
        if raw is None:
            return _failure("bad_observations",
                            "observations 不能省略；没有观测时请传空数组")
        observations, code, reason = _parse_pose_observations(raw)
        if observations is None:
            return _failure(code, reason)

        now = args.get("now_utc")
        if now is None:
            now_value = time.time()
            time_note = "未提供 now_utc，按当前系统时刻"
        else:
            now_value = _finite_number(now)
            if now_value is None:
                return _failure("bad_now_utc",
                                f"now_utc 必须是有限 Unix 秒，收到 {now!r}")
            time_note = "由调用参数给出"

        snapshot = health_snapshot(
            observations, now_value, sky_solve.solver_status(),
            camera_last_frame_utc=args.get("camera_last_frame_utc"),
            manual_override=bool(args.get("manual_override") or False),
            solve_within_tolerance=args.get("solve_within_tolerance"),
        )
        out = dict(snapshot)
        out["time_note"] = time_note
        out["units"] = "时间字段为 UTC Unix 秒"
        out["hint"] = ("level 只是链路分级（L0-L4），不是对目标的判定；"
                       "reason 已写明降级原因与相机无帧状态。")
        return out

    reg.register(
        name="sky_health",
        description=(
            "指向链路健康快照：给定位姿观测数组与当前时刻，报告级别 L0-L4、"
            "每个来源的可用性与年龄、融合位姿是否可用、解算器状态、相机是否"
            "无帧，以及人话降级原因。manual_override=true 表示用户已手动接管；"
            "solve_within_tolerance=false 表示解算残差超阈值，不提升到 L3。"
            "只读计算，不接硬件、不写盘。"),
        parameters={"type": "object", "properties": {
            "observations": {
                "type": "array",
                "description": "位姿观测数组（同 sky_pose_fuse；可为空数组）",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string",
                                   "description": "来源标签，如 imu / gnss / camera"},
                        "t_utc": {"type": "number",
                                  "description": "观测时刻（UTC Unix 秒）"},
                        "az_deg": {"type": "number",
                                   "description": "方位角（度，北=0 东=90）"},
                        "alt_deg": {"type": "number",
                                    "description": "地平高度（度，-90..90）"},
                        "accuracy_deg": {"type": "number",
                                         "description": "自报 1σ 精度（度，>0）"},
                        "roll_deg": {"type": "number",
                                     "description": "画面横滚（度），默认 0"},
                        "fov_deg": {"type": "number",
                                    "description": "可选：该观测的视场直径（度）"},
                    },
                    "required": ["source", "t_utc", "az_deg", "alt_deg",
                                 "accuracy_deg"],
                },
            },
            "now_utc": {"type": "number",
                        "description": "当前 UTC Unix 秒；缺省用系统当前时刻"},
            "camera_last_frame_utc": {
                "type": "number",
                "description": "相机最后一帧的 UTC Unix 秒；不给则按未知处理"},
            "manual_override": {
                "type": "boolean",
                "description": "用户是否已手动接管指向（true → L4）"},
            "solve_within_tolerance": {
                "type": "boolean",
                "description": "最近一次解算残差是否在阈值内；false 时不报 L3"},
        }, "required": ["observations"]},
        handler=_health,
        category="sky",
        write=False,
    )

    # ------------------------------------------------------------------
    # 13) 校准档案落盘：唯一的写工具，确认门由 tool_registry/tx_guard 统一拦截
    # ------------------------------------------------------------------
    def _calibration_save(args: Dict[str, Any]):
        from . import sky_calibration

        max_residual = args.get("max_residual_deg")
        if max_residual is None:
            max_residual = sky_calibration.MAX_CALIBRATION_RESIDUAL_DEG
        result = sky_calibration.calibrate_and_save(
            args.get("observations"),
            device_id=args.get("device_id"),
            fov_deg=args.get("fov_deg"),
            path=args.get("path"),
            source=args.get("source") or "two_star",
            max_residual_deg=max_residual,
        )
        if not result.get("ok"):
            code = str(result.get("error") or "bad_input")
            failure = {
                "ok": False,
                "error": code,
                "reason": str(result.get("reason") or "校准失败且未给出原因"),
                "next_step": _calibration_next_step(code),
            }
            if result.get("residual_deg") is not None:
                failure["residual_deg"] = result["residual_deg"]
            return failure

        profile = result.get("profile") or {}
        return {
            "ok": True,
            "path": result.get("path"),
            "residual_deg": result.get("residual_deg"),
            # 只回界面可见的档案摘要；boresight/roll 是内部修正量，刻意不暴露。
            "profile": {
                "device_id": profile.get("device_id"),
                "fov_deg": profile.get("fov_deg"),
                "created_utc": profile.get("created_utc"),
                "verified_utc": profile.get("verified_utc"),
                "source": profile.get("source"),
            },
            "reason": "",
            "note": ("residual_deg 是逐点残差的最大值；超过阈值的校准会被拒绝，"
                     "不会写入文件。档案已原子写入并读回校验。"),
        }

    reg.register(
        name="sky_calibration_save",
        description=(
            "指向校准落盘：给定至少两个「已知方向 ↔ 当前读数」校准点，解出"
            "方位/俯仰偏置与横滚零位，并原子写入 JSON 校准档案。"
            "会写盘、可能覆盖同名文件——需用户确认目标路径；校准求解失败或"
            "最大残差超过阈值时拒绝写入，失败时不写文件（原档案保持原样），"
            "不产出「差不多能用」的档案。"
            "成功只返回路径、最大残差与档案摘要（设备名/视场/时间/来源），"
            "不返回内部偏置数值。写操作受外部确认门约束：模型不能自备授权。"),
        parameters={"type": "object", "properties": {
            "observations": {
                "type": "array",
                "description": ("校准点数组，至少 2 项且已知方向夹角 ≥ 5°；"
                                "每项含 known_az_deg/known_alt_deg/"
                                "read_az_deg/read_alt_deg（度）"),
                "items": {
                    "type": "object",
                    "properties": {
                        "known_az_deg": {"type": "number",
                                         "description": "已知方向方位角（度）"},
                        "known_alt_deg": {"type": "number",
                                          "description": "已知方向高度角（度）"},
                        "read_az_deg": {"type": "number",
                                        "description": "当前读数方位角（度）"},
                        "read_alt_deg": {"type": "number",
                                         "description": "当前读数高度角（度）"},
                    },
                    "required": ["known_az_deg", "known_alt_deg",
                                 "read_az_deg", "read_alt_deg"],
                },
            },
            "device_id": {"type": "string",
                          "description": "用户命名的设备标识，写入档案"},
            "fov_deg": {"type": "number",
                        "description": "校准时使用的视场直径（度，0-180]"},
            "path": {"type": "string",
                     "description": ("目标 JSON 档案路径；目录不存在会创建，"
                                     "同名文件会被原子替换")},
            "source": {"type": "string", "enum": ["two_star", "landmark"],
                       "description": "校准来源，默认 two_star"},
            "max_residual_deg": {
                "type": "number",
                "description": ("允许的最大逐点残差（度），默认 0.5；"
                                "超过即拒绝写入")},
        }, "required": ["observations", "device_id", "fov_deg", "path"]},
        handler=_calibration_save,
        category="sky",
        write=True,
    )

