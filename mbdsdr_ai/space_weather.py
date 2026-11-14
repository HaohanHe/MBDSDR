# SPDX-License-Identifier: MIT
"""空间天气 / 传播条件：采集、快照、判定。

立场
====

「今天传播怎么样」不能写成一句固定文案——同一句话在 Kp=1 和 Kp=7 的
日子里都出现，就等于拿文案冒充观测。本模块只做三件事：

1. **采集**：太阳活动（F10.7）、地磁活动（Kp）、太阳黑子数、GOES X 射线
   通量（D 层吸收的驱动量）、本地天气。每个值必须带**观测时刻**与**来源 URL**。
2. **快照**：整次采集落盘为 JSON，同一份快照可以回放重算，结论可追溯。
3. **判定**：:func:`assess` 是纯函数，只读快照。缺值就是缺值——
   **不设「默认平静」兜底**；必需项缺失时只输出「当前传播条件未知（原因）」，
   不给传播建议。

采集失败不抛给界面：失败以 ``error`` 记录在对应样本上，其余成功项照常保留。
所有网络入口都可注入 ``fetcher``，CI 无网络也能覆盖成功/失败两条路径。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = [
    "SNAPSHOT_SCHEMA",
    "SOURCES",
    "REQUIRED_KEYS",
    "OFFLINE_ENV",
    "fetch_json",
    "collect_snapshot",
    "default_station",
    "save_snapshot",
    "load_snapshot",
    "latest_snapshot",
    "snapshot_for_diagnosis",
    "assess",
    "format_propagation_section",
]

SNAPSHOT_SCHEMA = 1
OFFLINE_ENV = "MBDSDR_SPACE_WEATHER_OFFLINE"
SNAPSHOT_SUBDIR = os.path.join(".mbdsdr", "space_weather")
DEFAULT_TIMEOUT_S = 1.2

# 判定必需项：缺任一项即「传播条件未知」。
REQUIRED_KEYS = ("kp", "f107", "ssn")

SOURCES: Dict[str, Dict[str, str]] = {
    "kp": {
        "label": "地磁 Kp 指数",
        "url": "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
        "source": "NOAA SWPC planetary K index",
    },
    "f107": {
        "label": "F10.7 射电通量",
        "url": "https://services.swpc.noaa.gov/json/f107_cm_flux.json",
        "source": "NOAA SWPC F10.7 cm flux",
    },
    "ssn": {
        "label": "太阳黑子数",
        "url": "https://services.swpc.noaa.gov/json/solar-cycle/observed-solar-cycle-indices.json",
        "source": "NOAA SWPC observed solar cycle indices",
    },
    "xray": {
        "label": "GOES X 射线通量（0.1–0.8 nm，D 层吸收驱动量）",
        "url": "https://services.swpc.noaa.gov/json/goes/primary/xrays-6-hour.json",
        "source": "NOAA SWPC GOES primary X-ray",
    },
    "weather": {
        "label": "本地天气",
        "url": "https://api.open-meteo.com/v1/forecast",
        "source": "Open-Meteo current weather",
    },
}


# ═══════════════════════════════════════════════════════
# 1. 采集
# ═══════════════════════════════════════════════════════

def fetch_json(url: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> Any:
    """默认取数实现：标准库 urllib，带 UA 与硬超时。失败原样抛。"""
    req = urllib.request.Request(url, headers={"User-Agent": "MBDSDR/space-weather"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", errors="replace"))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sample(key: str, value: Optional[float], display: str, observed_at: str,
            fetched_at: str, unit: str = "", error: str = "") -> Dict[str, Any]:
    meta = SOURCES[key]
    return {
        "key": key,
        "label": meta["label"],
        "value": value,
        "display": display,
        "unit": unit,
        "observed_at": observed_at,
        "fetched_at": fetched_at,
        "source": meta["source"],
        "url": meta["url"],
        "error": error,
    }


def _fetch_kp(fetcher: Callable[..., Any], fetched_at: str) -> Dict[str, Any]:
    data = fetcher(SOURCES["kp"]["url"])
    # [[time_tag, Kp, a_running, station_count], ...]，首行是表头。
    rows = [r for r in data if isinstance(r, list) and len(r) >= 2]
    for r in reversed(rows):
        try:
            val = float(r[1])
        except (TypeError, ValueError):
            continue
        return _sample("kp", val, f"{val:g}", str(r[0]), fetched_at)
    raise ValueError("响应里没有可用的 Kp 数值")


def _fetch_f107(fetcher: Callable[..., Any], fetched_at: str) -> Dict[str, Any]:
    data = fetcher(SOURCES["f107"]["url"])
    for row in reversed(list(data)):
        try:
            val = float(row.get("flux"))
        except (AttributeError, TypeError, ValueError):
            continue
        return _sample("f107", val, f"{val:g}", str(row.get("time_tag", "")),
                       fetched_at, unit="sfu")
    raise ValueError("响应里没有可用的 F10.7 数值")


def _fetch_ssn(fetcher: Callable[..., Any], fetched_at: str) -> Dict[str, Any]:
    data = fetcher(SOURCES["ssn"]["url"])
    for row in reversed(list(data)):
        try:
            val = float(row.get("ssn"))
        except (AttributeError, TypeError, ValueError):
            continue
        stamp = str(row.get("time-tag") or row.get("time_tag") or "")
        return _sample("ssn", val, f"{val:g}", stamp, fetched_at)
    raise ValueError("响应里没有可用的太阳黑子数")


def _fetch_xray(fetcher: Callable[..., Any], fetched_at: str) -> Dict[str, Any]:
    data = fetcher(SOURCES["xray"]["url"])
    rows = [r for r in data if str(r.get("energy", "")).startswith("0.1-0.8")]
    for row in reversed(rows):
        try:
            val = float(row.get("flux"))
        except (AttributeError, TypeError, ValueError):
            continue
        cls = _xray_class(val)
        return _sample("xray", val, f"{cls}（{val:.2e} W/m²）",
                       str(row.get("time_tag", "")), fetched_at)
    raise ValueError("响应里没有 0.1–0.8 nm 通道数据")


def _fetch_weather(fetcher: Callable[..., Any], fetched_at: str,
                   lat: Optional[float], lon: Optional[float]) -> Dict[str, Any]:
    if lat is None or lon is None:
        raise ValueError("未配置地面站坐标（~/.mbdsdr/config.json 的 ground_station_lat/lon）")
    url = (f"{SOURCES['weather']['url']}?latitude={lat:.4f}&longitude={lon:.4f}"
           "&current=temperature_2m,relative_humidity_2m,precipitation,weather_code")
    data = fetcher(url)
    cur = (data or {}).get("current") or {}
    if "precipitation" not in cur:
        raise ValueError("响应里没有 current.precipitation")
    prec = float(cur["precipitation"])
    temp = cur.get("temperature_2m")
    code = cur.get("weather_code")
    bits = [f"降水 {prec:g} mm"]
    if temp is not None:
        bits.append(f"气温 {float(temp):g} ℃")
    if code is not None:
        bits.append(f"天气代码 {int(code)}")
    return _sample("weather", prec, "，".join(bits), str(cur.get("time", "")),
                   fetched_at, unit="mm")


def _xray_class(flux: float) -> str:
    """GOES X 射线通量分档（A/B/C/M/X）。"""
    if flux >= 1e-4:
        return "X"
    if flux >= 1e-5:
        return "M"
    if flux >= 1e-6:
        return "C"
    if flux >= 1e-7:
        return "B"
    return "A"


def collect_snapshot(
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    fetcher: Optional[Callable[..., Any]] = None,
    now: Optional[datetime] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Dict[str, Any]:
    """采集一次，返回快照 dict（不落盘）。

    单项失败只写进该样本的 ``error``，不影响其他项；五项全失败仍返回
    一份「全带 error」的快照，由 :func:`assess` 判定为未知。
    """
    fetch = fetcher or (lambda url: fetch_json(url, timeout_s))
    taken = now or _utc_now()
    fetched_at = _iso(taken)
    jobs = {
        "kp": lambda: _fetch_kp(fetch, fetched_at),
        "f107": lambda: _fetch_f107(fetch, fetched_at),
        "ssn": lambda: _fetch_ssn(fetch, fetched_at),
        "xray": lambda: _fetch_xray(fetch, fetched_at),
        "weather": lambda: _fetch_weather(fetch, fetched_at, lat, lon),
    }
    samples: Dict[str, Dict[str, Any]] = {}
    # 五项并发，整体耗时被单个超时封顶（串行时最坏 5×timeout，会拖住诊断）。
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {key: pool.submit(job) for key, job in jobs.items()}
        for key, fut in futures.items():
            try:
                samples[key] = fut.result(timeout=timeout_s + 0.5)
            except Exception as e:  # 采集失败不冒泡，记在样本上
                samples[key] = _sample(key, None, "未取到", "", fetched_at,
                                       error=f"{type(e).__name__}: {e}")
    return {
        "schema": SNAPSHOT_SCHEMA,
        "taken_at": fetched_at,
        "station": {"lat": lat, "lon": lon},
        "samples": samples,
    }


def default_station() -> Tuple[Optional[float], Optional[float]]:
    """从 ``~/.mbdsdr/config.json`` 读地面站坐标；没有就是 (None, None)。"""
    path = os.path.join(os.path.expanduser("~"), ".mbdsdr", "config.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:
        return None, None
    lat = cfg.get("ground_station_lat")
    lon = cfg.get("ground_station_lon")
    try:
        return (float(lat), float(lon)) if lat is not None and lon is not None else (None, None)
    except (TypeError, ValueError):
        return None, None


# ═══════════════════════════════════════════════════════
# 2. 快照落盘 / 回放
# ═══════════════════════════════════════════════════════

def _snapshot_dir(save_dir: Optional[str] = None) -> str:
    return save_dir or os.path.join(os.path.expanduser("~"), SNAPSHOT_SUBDIR)


def save_snapshot(snapshot: Dict[str, Any], save_dir: Optional[str] = None) -> str:
    """把快照写成 ``<taken_at>.json``，返回文件路径。"""
    d = _snapshot_dir(save_dir)
    os.makedirs(d, exist_ok=True)
    stamp = str(snapshot.get("taken_at") or _iso(_utc_now()))
    name = stamp.replace("-", "").replace(":", "").replace("T", "_")
    path = os.path.join(d, name + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return path


def load_snapshot(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        snap = json.load(fh)
    if int(snap.get("schema", 0)) != SNAPSHOT_SCHEMA:
        raise ValueError(f"快照 schema 不认识: {snap.get('schema')}")
    return snap


def latest_snapshot(save_dir: Optional[str] = None,
                    max_age_h: Optional[float] = None,
                    now: Optional[datetime] = None) -> Optional[Tuple[Dict[str, Any], str]]:
    """返回最新的 (snapshot, path)；超出 ``max_age_h`` 视为没有。"""
    d = _snapshot_dir(save_dir)
    if not os.path.isdir(d):
        return None
    files = [os.path.join(d, n) for n in os.listdir(d) if n.endswith(".json")]
    if not files:
        return None
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    path = files[0]
    if max_age_h is not None:
        age_h = ((now or _utc_now()).timestamp() - os.path.getmtime(path)) / 3600.0
        if age_h > max_age_h:
            return None
    try:
        return load_snapshot(path), path
    except Exception:
        return None


def snapshot_for_diagnosis(max_age_min: float = 15.0,
                           allow_live: bool = True,
                           save_dir: Optional[str] = None,
                           fetcher: Optional[Callable[..., Any]] = None,
                           now: Optional[datetime] = None
                           ) -> Tuple[Optional[Dict[str, Any]], str, str]:
    """诊断用取数：先看新鲜快照，再尝试现采。

    返回 ``(snapshot, path, note)``。``note`` 说明来源（"快照回放"/"现采"）
    或取不到的原因；没有快照且现采失败时 snapshot 为 ``None``。
    """
    cached = latest_snapshot(save_dir=save_dir, max_age_h=max_age_min / 60.0, now=now)
    if cached:
        return cached[0], cached[1], "新鲜快照"
    if not allow_live or os.environ.get(OFFLINE_ENV) == "1":
        return None, "", "没有新鲜快照，且已禁用联网采集"
    lat, lon = default_station()
    snap = collect_snapshot(lat=lat, lon=lon, fetcher=fetcher, now=now)
    try:
        path = save_snapshot(snap, save_dir=save_dir)
    except Exception:
        path = ""
    return snap, path, "现采"


# ═══════════════════════════════════════════════════════
# 3. 判定（纯函数，只读快照）
# ═══════════════════════════════════════════════════════

def _val(snapshot: Dict[str, Any], key: str) -> Optional[float]:
    s = (snapshot.get("samples") or {}).get(key) or {}
    v = s.get("value")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def assess(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """快照 → 结论。纯函数：同一快照两次调用结果完全相同。

    ``known=False`` 时 ``advice`` 为空，``unknown_reasons`` 写明每个缺项
    是「未取到：原因」还是「快照里没有」。**不存在默认平静分支**。
    """
    if not snapshot:
        return {"known": False, "conclusion": "当前传播条件未知（原因：没有可用快照）",
                "advice": [], "unknown_reasons": ["没有可用快照"], "samples": {}}

    samples = snapshot.get("samples") or {}
    reasons: List[str] = []
    for key in REQUIRED_KEYS:
        s = samples.get(key) or {}
        if _val(snapshot, key) is None:
            why = s.get("error") or "快照里缺少该值"
            reasons.append(f"{SOURCES[key]['label']}未取到（{why}）")
    if reasons:
        return {
            "known": False,
            "conclusion": "当前传播条件未知（" + "；".join(reasons) + "）",
            "advice": [],
            "unknown_reasons": reasons,
            "samples": samples,
        }

    kp = _val(snapshot, "kp")
    f107 = _val(snapshot, "f107")
    ssn = _val(snapshot, "ssn")
    xray = _val(snapshot, "xray")
    weather = _val(snapshot, "weather")

    advice: List[str] = []
    tags: List[str] = []

    if kp >= 5.0:
        tags.append(f"地磁扰动 Kp={kp:g}")
        advice.append(
            f"地磁扰动（Kp={kp:g}，观测 {samples['kp'].get('observed_at','?')}）："
            "HF 高纬/极区路径吸收与闪烁增强，低仰角卫星过境更易闪断；"
            "这是传播条件，不是硬件判据——先按此预期再决定是否重测。")
    elif kp >= 4.0:
        tags.append(f"地磁活跃 Kp={kp:g}")
        advice.append(
            f"地磁活跃（Kp={kp:g}）：HF 天波路径可能不稳，VHF/UHF 视距链路影响较小。")
    else:
        tags.append(f"地磁平静 Kp={kp:g}")
        advice.append(f"地磁平静（Kp={kp:g}）：若此时仍无信号，优先查本地链路。")

    if f107 >= 150.0:
        advice.append(
            f"太阳活动偏高（F10.7={f107:g} sfu）：电离层电子密度偏高，"
            "HF 可用频率上移，日侧 D 层吸收也更强。")
    elif f107 < 100.0:
        advice.append(
            f"太阳活动偏低（F10.7={f107:g} sfu）：电离层电子密度偏低，"
            "HF 可用频率下移，高仰角短波路径可能更吃力。")
    else:
        advice.append(f"太阳活动中等（F10.7={f107:g} sfu）。")

    if xray is not None:
        cls = _xray_class(xray)
        if cls in ("M", "X"):
            advice.append(
                f"GOES X 射线 {cls} 级：日侧 D 层吸收显著增强，"
                "短波可能中断（SID），HF 接收先按此解释。")
        elif cls == "C":
            advice.append("GOES X 射线 C 级：日侧 D 层吸收轻微增强，HF 影响有限。")

    wsample = samples.get("weather") or {}
    if weather is not None:
        if weather > 0.0:
            advice.append(
                f"本地正在降水（{wsample.get('display','')}）：Ku 以上频段雨衰增大，"
                "VHF/UHF 影响有限。")
        else:
            advice.append(f"本地无降水（{wsample.get('display','')}）。")
    else:
        advice.append(
            "本地天气未取到（" + (wsample.get("error") or "快照里没有")
            + "），本条不参与判断。")

    return {
        "known": True,
        "conclusion": "；".join(tags) + f"（太阳黑子数 {ssn:g}）",
        "advice": advice,
        "unknown_reasons": [],
        "samples": samples,
    }


def format_propagation_section(assessment: Dict[str, Any],
                               snapshot_path: str = "",
                               note: str = "") -> str:
    """把判定渲染成可直接给人看的多行文本（含时刻与来源）。"""
    samples = assessment.get("samples") or {}
    lines: List[str] = []
    head = "传播条件"
    if note:
        head += f"（{note}）"
    if snapshot_path:
        head += f"，快照 {snapshot_path}"
    lines.append(head + "：")
    for key in ("kp", "f107", "ssn", "xray", "weather"):
        s = samples.get(key)
        if not s:
            lines.append(f"- {SOURCES[key]['label']}：未取到（快照里没有）")
            continue
        if s.get("value") is None:
            lines.append(f"- {s['label']}：未取到（{s.get('error') or '原因未知'}）")
            continue
        lines.append(
            f"- {s['label']} = {s.get('display')}"
            f"（观测 {s.get('observed_at') or '时刻缺失'}，"
            f"采集 {s.get('fetched_at') or '?'}，来源 {s.get('source')} {s.get('url')}）")
    lines.append("结论：" + str(assessment.get("conclusion", "")))
    for i, a in enumerate(assessment.get("advice") or [], 1):
        lines.append(f"{i}. {a}")
    return "\n".join(lines)
