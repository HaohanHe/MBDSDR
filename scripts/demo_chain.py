#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""M1 教学链路演示：三条 12 分钟脚本（无硬件 / 断网 / 有硬件）。

每次运行打印 8 步：标题、看哪个界面、期望看到什么、缺条件时的诚实空态、
以及可复跑的证据（命令或文件）。能用现有模块真算的就真算；算不出来就
明说缺什么，绝不打印假过境时间或假频率。

用法::

    python scripts/demo_chain.py --mode nohw
    python scripts/demo_chain.py --mode offline
    python scripts/demo_chain.py --mode hw

退出码约定与 ``scripts/sdr_diag.py`` / ``scripts/system_diag.py`` 一致：

* ``0``  演示流程跑完（无硬件/断网档的正常出口）；
* ``1``  有硬件档缺设备（``EXIT_NO_DEVICE``，与 sdr_diag 同码，不自创）。

本脚本只读本地数据，不在 nohw/offline 档发起任何网络请求；hw 档只做设备
枚举，不打开设备（避免抢占用户正在使用的 SDR）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXIT_OK = 0
#: 与 ``scripts/sdr_diag.py`` 的 ``EXIT_NO_DEVICE`` 同值，不自创退出码。
EXIT_NO_DEVICE = 1

TOTAL_STEPS = 8
#: 每档每一步的预计分钟数；总和写死在 ``MODE_MINUTES``，口径上限 12 分钟。
MODE_MINUTES: Dict[str, Tuple[float, ...]] = {
    "nohw": (1.0, 0.5, 1.0, 1.0, 1.0, 1.0, 1.0, 0.5),
    "offline": (1.0, 0.5, 1.0, 1.0, 1.0, 1.0, 1.0, 0.5),
    "hw": (1.0, 0.5, 1.0, 1.5, 1.5, 1.0, 1.0, 0.5),
}
MODE_LABELS = {
    "nohw": "无硬件（nohw）",
    "offline": "断网（offline）",
    "hw": "有硬件（hw）",
}
MODE_NOTES = {
    "nohw": "无 SDR、无 AI key，演示星表/时间/过境与诚实空态",
    "offline": "MBDSDR_OFFLINE=1，联网能力显式拒绝或本地可用",
    "hw": "有 SDR，演示选星→调谐→录音→回放；无设备时按实缺项退出 1",
}

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "real_fm_88m1"
SAT_LINKS = ROOT / "mbdsdr_ai" / "data" / "sat_links.json"
#: 演示优先检查的卫星（真实 NORAD 编号，缓存文件名即 CATNR）。
DEMO_CATNRS = (25544, 25338, 33591)


# ---------------------------------------------------------------------------
# 输出小工具
# ---------------------------------------------------------------------------
def _fmt_seconds(value: float) -> str:
    if value < 1.0:
        return f"{value * 1000:.0f} 毫秒"
    return f"{value:.1f} 秒"


def _step(n: int, minutes: float, title: str, panel: str, expect: str,
          empty: str, evidence: str) -> None:
    print(f"\n[{n}/{TOTAL_STEPS}] {title}（预计 {minutes:g} 分钟）")
    print(f"  界面/面板：{panel}")
    print(f"  期望看到：{expect}")
    print(f"  缺条件空态：{empty}")
    print(f"  证据：{evidence}")


def _print_header(mode: str) -> float:
    minutes = MODE_MINUTES[mode]
    total = float(sum(minutes))
    print("MBDSDR M1 教学链路演示（scripts/demo_chain.py）")
    print(f"模式：{MODE_LABELS[mode]}——{MODE_NOTES[mode]}")
    print(f"预计总时长：{total:g} 分钟（{TOTAL_STEPS} 步，口径 ≤12 分钟/条）")
    if total > 12.0:  # 写死口径自检；正常不会触发
        raise SystemExit(f"内部错误：{mode} 档预计时长 {total:g} 分钟超过 12 分钟")
    return total


# ---------------------------------------------------------------------------
# 数据读取（失败只返回原因，不伪造）
# ---------------------------------------------------------------------------
def _station():
    try:
        from mbdsdr_ai.station import station_from_config
        return station_from_config()
    except Exception as exc:  # noqa: BLE001 - 演示脚本如实报错
        return _StationError(f"{type(exc).__name__}: {exc}")


class _StationError:
    def __init__(self, detail: str) -> None:
        self.detail = detail
        self.lat = None
        self.lon = None
        self.alt_m = 0.0
        self.source = "error"
        self.error = detail


def _catalog_counts() -> Tuple[Optional[int], Optional[int], str]:
    try:
        from mbdsdr_ai.skyengine.star_catalog import get_catalog
        cat = get_catalog()
        return int(cat.count), int(cat.count_below(6.5)), ""
    except Exception as exc:  # noqa: BLE001 - 缺星表就明说
        return None, None, f"{type(exc).__name__}: {exc}"


def _xingguan_count() -> Tuple[Optional[int], str]:
    try:
        from mbdsdr_ai.skyengine.xingguan import get_xingguan
        return len(get_xingguan()), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def _downlink_for(norad: int) -> Optional[Dict[str, Any]]:
    """从随仓公开下行链路目录按 NORAD 取一条频点（不联网）。"""
    try:
        payload = json.loads(SAT_LINKS.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 目录缺失则本步降级说明
        return None
    if not isinstance(payload, dict):
        return None
    for key, entry in payload.items():
        if not isinstance(entry, dict) or key.startswith("_"):
            continue
        if int(entry.get("norad_cat_id") or -1) != int(norad):
            continue
        for link in entry.get("links") or []:
            if isinstance(link, dict) and link.get("direction") == "downlink":
                return {
                    "display_name": entry.get("display_name") or key,
                    "freq_hz": float(link.get("freq_hz") or 0.0),
                    "mode": link.get("mode") or "",
                    "source": entry.get("source") or "",
                }
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fmt_utc(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC")


# ---------------------------------------------------------------------------
# 硬件前置检查（只枚举，不打开）
# ---------------------------------------------------------------------------
def _hw_preflight() -> int:
    print("\n硬件前置检查（不计入 8 步）")
    if os.environ.get("MBDSDR_NO_HW", "").strip() == "1":
        print("  缺设备：MBDSDR_NO_HW=1 显式禁用硬件枚举；本档不能演示真实调谐/录音。")
        print("  本脚本不会假装有设备。退出码 1（与 scripts/sdr_diag.py 的 EXIT_NO_DEVICE 一致）")
        return EXIT_NO_DEVICE
    try:
        from mbdsdr_ai.device_manager import DeviceManager
        devices = list(DeviceManager().list_devices() or [])
    except Exception as exc:  # noqa: BLE001 - 枚举失败也是缺设备
        print(f"  缺设备：SDR 枚举失败（{type(exc).__name__}: {exc}）。")
        print("  本脚本不会假装有设备。退出码 1（与 scripts/sdr_diag.py 的 EXIT_NO_DEVICE 一致）")
        return EXIT_NO_DEVICE
    if not devices:
        print("  缺设备：未枚举到任何 SDR。缺的是：rtl-sdr 兼容 USB 设备、"
              "SoapySDR 驱动模块，或 MBDSDR_RTL_TCP 远端源。")
        print("  本脚本不会假装有设备，也不会打开不存在的设备。")
        print("  退出码 1（与 scripts/sdr_diag.py 的 EXIT_NO_DEVICE 一致）")
        return EXIT_NO_DEVICE
    names = []
    for dev in devices:
        name = str(getattr(dev, "name", "") or getattr(dev, "driver", "") or "SDR")
        driver = str(getattr(dev, "driver", "") or "")
        serial = str(getattr(dev, "serial", "") or "")
        extra = "/".join(part for part in (driver, serial) if part)
        names.append(f"{name}（{extra}）" if extra else name)
    print(f"  发现 {len(devices)} 台 SDR：{'、'.join(names)}")
    print("  本脚本只枚举，不打开设备，避免抢占你正在用的 SDR。")
    return EXIT_OK


# ---------------------------------------------------------------------------
# 八步
# ---------------------------------------------------------------------------
def step_time_and_station(mode: str, minutes: float) -> Dict[str, Any]:
    _step(
        1, minutes, "时间引擎与站址",
        "天空视图顶部时间条 + 设置页「站址」",
        "时间条显示 UTC 与本机墙钟口径；站址显示来源（配置/手填/GNSS）",
        "站址未配置时显示「未设置站址」，不拿内置城市或 0.0,0.0 冒充",
        "mbdsdr_ai/skyengine/jtime.py、mbdsdr_ai/station.py",
    )
    ctx: Dict[str, Any] = {"station": None}
    try:
        from mbdsdr_ai.skyengine.jtime import TimeKernel, gmst_deg
        kernel = TimeKernel()
        print(f"  时间引擎实算：JD={kernel.jd:.5f}，GMST={gmst_deg(kernel.jd):.3f} 度，"
              f"现在={_fmt_utc(kernel.unix())}（本机墙钟）")
    except Exception as exc:  # noqa: BLE001
        print(f"  时间引擎不可用：{type(exc).__name__}: {exc}")
    fix = _station()
    ctx["station"] = fix
    if getattr(fix, "lat", None) is None:
        why = getattr(fix, "error", "") or "配置里没有 ground_station_lat/lon"
        print(f"  站址：未配置（source={getattr(fix, 'source', 'none')}；{why}）。"
              "过境步骤将不计算，避免拿示例坐标冒充本站。")
    else:
        print(f"  站址：{float(fix.lat):.6f}, {float(fix.lon):.6f}，"
              f"海拔 {float(getattr(fix, 'alt_m', 0.0) or 0.0):.1f} m，来源={fix.source}")
    return ctx


def step_catalog(mode: str, minutes: float) -> None:
    _step(
        2, minutes, "星表与星官",
        "天空视图 + 星卡/查询面板",
        "星表星数、6.5 等以内星数与中国星官区域数可读",
        "星表或星官数据缺失时明确报「数据缺失」，不回退到假星表",
        "mbdsdr_ai/data/full_sky_hyg_v41.npz、mbdsdr_ai/data/xingguan_chinese.json",
    )
    count, naked, err = _catalog_counts()
    if count is None:
        print(f"  星表：本机无法加载（{err}）；这一步不演示星数，也不回退到假星表。")
    else:
        print(f"  星表实算：HYG 全天 {count} 颗；星等 ≤6.5 有 {naked} 颗。")
    xg, xg_err = _xingguan_count()
    if xg is None:
        print(f"  中国星官：本机无法加载（{xg_err}）。")
    else:
        print(f"  中国星官实算：{xg} 个区域（三垣二十八宿）。")


def step_passes(mode: str, minutes: float, ctx: Dict[str, Any]) -> Dict[str, Any]:
    _step(
        3, minutes, "过境与多普勒",
        "过境面板 + 卫星信息卡",
        "列出未来 24 小时过境：UTC 升/中/落、最高仰角、TLE 历元与新鲜度",
        "无轨道缓存时打印缺失原因并说明未联网；不打印凑数的过境时间",
        "mbdsdr_ai/orbit.py、mbdsdr_ai/skyengine/satellites.py",
    )
    result: Dict[str, Any] = {"record": None, "pass": None}
    from mbdsdr_ai import orbit  # 延迟导入，nohw 档也只用本地缓存

    records: Dict[int, Dict[str, Any]] = {}
    reasons: List[str] = []
    for catnr in DEMO_CATNRS:
        try:
            records[catnr] = orbit.fetch_tle_record(
                catnr, allow_network=False, max_age_hours=24.0 * 90)
        except Exception as exc:  # noqa: BLE001 - 无缓存如实记录
            reasons.append(f"{catnr}: {exc}")
    if not records:
        cache_dir = os.environ.get("MBDSDR_TLE_CACHE_DIR") or "~/.mbdsdr/tle_cache"
        print(f"  无 TLE/无缓存：这一步本机无法演示，原因：未显式允许联网"
              f"（MBDSDR_ALLOW_NETWORK 未设），且缓存目录 {cache_dir} 里没有 "
              f"{'/'.join(str(c) for c in DEMO_CATNRS)} 的 .tle 文件；未发起网络请求。")
        if reasons:
            print(f"  读取失败原因：{reasons[0]}")
        return result

    fix = ctx.get("station")
    if getattr(fix, "lat", None) is None:
        print("  有 TLE 缓存，但站址未配置：这一步不计算过境，"
              "原因是不拿示例坐标冒充你的站点。")
        return result

    passes = orbit.predict_passes(
        float(fix.lat), float(fix.lon),
        float(getattr(fix, "alt_m", 0.0) or 0.0) / 1000.0,
        hours=24.0, min_elevation=10.0, satellite_type="all",
        start_unix=time.time(), tle_records=records)
    if not passes:
        print("  已用本机 TLE 真算，但未来 24 小时没有 ≥10° 的过境；"
              "这不是失败，也不代表数据有问题。")
        return result
    first = passes[0]
    pass_catnr = orbit.BUILTIN_SATS.get(str(first["satellite"]))
    rec = records.get(pass_catnr) or next(iter(records.values()))
    result["record"] = rec
    result["pass"] = first
    print(f"  TLE 实读：CATNR {rec.get('catnr')}（{rec.get('name') or '未命名'}），"
          f"历元 {rec.get('epoch_utc') or '未知'}，来源={rec.get('source')}，"
          f"刷新状态={rec.get('refresh_status')}")
    print(f"  过境实算：{first['satellite']} 升 {first['rise_utc']}，"
          f"中天 {first['culmination_utc']}（最高仰角 {first['max_elevation']:.1f}°），"
          f"落 {first['set_utc']}，持续 {first['duration_s']} s")
    print(f"  时刻口径：{first['time_basis']} / 精度 {first['precision_tier']}"
          f"（{first['time_precision_s']} s）；算法 {first['algorithm']}")
    print(f"  TLE 新鲜度：{first['tle_tier_label']}（{first['tle_age_days']:.2f} 天）；"
          f"精度风险：{first['accuracy_risk']}——{first['accuracy_note']}")
    return result


def step_select_tune(mode: str, minutes: float,
                     ctx: Dict[str, Any], pass_ctx: Dict[str, Any]) -> None:
    _step(
        4, minutes, "选星→调谐",
        "卫星信息卡 + 接收/调谐面板 + 状态卡",
        "选中卫星后显示 NORAD/COSPAR 与公开下行频点；调谐后状态卡显示「设定值」",
        "无 SDR：只演示目录与公开频点，不下发调谐；不出现「调谐成功」",
        "mbdsdr_ai/data/sat_links.json、mbdsdr_ai/skyengine/satellites.py",
    )
    rec = pass_ctx.get("record")
    first = pass_ctx.get("pass")
    if not rec:
        print("  无 TLE：只能演示到卫星目录层，无法给出过境指向与调谐建议。")
        return
    catnr = int(rec.get("catnr") or 0)
    try:
        from mbdsdr_ai.skyengine.satellites import SATELLITE_INDEX
        meta = SATELLITE_INDEX.get(catnr)
    except Exception:  # noqa: BLE001
        meta = None
    if meta is not None:
        print(f"  选星：{meta.name}，NORAD {meta.norad}，COSPAR "
              f"{meta.cospar_override or '未知'}，类型 {meta.kind_label}")
    else:
        print(f"  选星：CATNR {catnr}（目录索引未收录，不编造名称/COSPAR）")
    link = _downlink_for(catnr)
    if link is None:
        print("  调谐建议：公开下行目录里没有该星频点，不猜频率。")
    else:
        print(f"  调谐建议：{link['freq_hz'] / 1e6:.4f} MHz（{link['mode']}），"
              f"来源：{link['source']}")
    if first is not None:
        print(f"  指向：升起方位 {first['rise_azimuth']:.1f}°，"
              f"中天方位 {first['culmination_azimuth']:.1f}° / "
              f"仰角 {first['max_elevation']:.1f}°")
        if link is not None:
            try:
                from mbdsdr_ai import orbit
                fix = ctx.get("station")
                dop = orbit.doppler_correction(
                    str(first["satellite"]), link["freq_hz"],
                    float(fix.lat), float(fix.lon),
                    float(getattr(fix, "alt_m", 0.0) or 0.0) / 1000.0,
                    when=float(first["rise_epoch_unix"]))
                if "error" in dop:
                    print(f"  多普勒：{dop['error']}")
                else:
                    print(f"  多普勒实算（升起时刻，视线速度 "
                          f"{dop['range_rate_kms']:.3f} km/s）："
                          f"{dop['doppler_shift_hz']:+.1f} Hz，"
                          f"修正后 {dop['corrected_freq_mhz']:.6f} MHz")
            except Exception as exc:  # noqa: BLE001
                print(f"  多普勒：本机无法计算（{type(exc).__name__}: {exc}）。")
    if mode == "hw":
        print("  有 SDR：按上面方位/仰角对准，按频点调谐；状态卡应显示「设定值」，"
              "是否真的收到信号由频谱/解码确认。")
    else:
        print("  无 SDR：这一步只到目录与公开频点，不下发调谐，也不显示调谐成功；"
              "界面应保持设备未连接空态。")


def step_record_replay(mode: str, minutes: float) -> None:
    _step(
        5, minutes, "录音/回放",
        "录制中心 + 回放面板",
        "有设备时录一段 IQ 到 ~/.mbdsdr/recordings/ 再回放；回放先校验文件哈希",
        "无 SDR：录音不可演示；回放仍可用随仓真实 OTA 样本验证素材在位",
        "tests/fixtures/real_fm_88m1/、mbdsdr_ai/sdr_tools.py",
    )
    data_path = FIXTURE_DIR / "capture.sigmf-data"
    prov_path = FIXTURE_DIR / "provenance.json"
    if data_path.is_file():
        try:
            expected = ""
            if prov_path.is_file():
                prov = json.loads(prov_path.read_text(encoding="utf-8"))
                expected = str(((prov.get("files") or {})
                                .get("capture.sigmf-data") or {}).get("sha256") or "")
            actual = _sha256(data_path)
            size_mb = data_path.stat().st_size / (1024 * 1024)
            if expected and actual == expected:
                print(f"  回放素材在位并校验通过：{data_path.relative_to(ROOT)}"
                      f"（{size_mb:.2f} MiB，sha256 {actual[:16]}…）")
            elif expected:
                print(f"  回放素材哈希不一致：期望 {expected[:16]}…，实际 {actual[:16]}…；"
                      "这一步不声称可回放。")
            else:
                print(f"  回放素材在位：{data_path.relative_to(ROOT)}（{size_mb:.2f} MiB），"
                      "但 provenance.json 缺哈希，未做校验。")
        except Exception as exc:  # noqa: BLE001
            print(f"  回放素材读取失败：{type(exc).__name__}: {exc}")
    else:
        print(f"  本机无回放素材：{FIXTURE_DIR.relative_to(ROOT)} 不存在，"
              "这一步无法演示。")
    rec_dir = Path.home() / ".mbdsdr" / "recordings"
    try:
        files = sorted(p for p in rec_dir.glob("*") if p.is_file())
    except OSError:
        files = []
    print(f"  录制目录：{rec_dir}，现有 {len(files)} 个文件（目录不存在也算 0）。")
    if mode == "hw":
        print("  有 SDR：在录制中心开始录制，停止后到回放面板选同一条文件；"
              "录制时长与样本数为设备报告值，不是估算。")
    else:
        print("  无 SDR：不演示录音；本档只用随仓真实 OTA 样本证明回放素材与校验链路在位，"
              "不解码、不声称听到了音频。")


def step_ai(mode: str, minutes: float) -> None:
    _step(
        6, minutes, "AI 协商",
        "AI 面板 + 工具协商记录",
        "有 key 时可发起一次协商；无 key 时明确返回 api_key_not_set",
        "无 AI key：拒绝调用并给出错误码；离线：返回 llm_offline，未发请求",
        "mbdsdr_ai/agent.py:4519-4534、mbdsdr_ai/error_catalog.py:821",
    )
    try:
        from mbdsdr_ai.config import AgentConfig
        from mbdsdr_ai.error_catalog import user_message
        from mbdsdr_ai.model_manager import is_local_endpoint
        cfg = AgentConfig()
    except Exception as exc:  # noqa: BLE001
        print(f"  AI 配置无法读取：{type(exc).__name__}: {exc}")
        return
    offline = os.environ.get("MBDSDR_OFFLINE", "").strip().lower() in (
        "1", "true", "yes", "on")
    if offline:
        print(f"  AI 协商：离线：显式拒绝——{user_message('llm_offline')}")
        return
    if cfg.api_key:
        print("  AI 协商：检测到 API key（不打印值）。本脚本不发起真实模型调用，"
              "避免消耗额度；请用桌面 AI 面板完成一次协商。")
        return
    if is_local_endpoint(cfg.base_url):
        print(f"  AI 协商：本地端点 {cfg.base_url} 无需 key，本可协商；"
              "但本脚本不发起真实调用，避免在演示中等待本地服务。")
        return
    print("  AI 协商：无 AI key（MBDSDR_API_KEY / OPENAI_API_KEY 均未设置）"
          f"——拒绝调用：{user_message('api_key_not_set', detail='环境变量与配置文件均为空')}")


def step_offline(mode: str, minutes: float) -> None:
    _step(
        7, minutes, "断网行为",
        "设置页网络开关 + AI 面板 + 星表/过境面板",
        "联网工具显式拒绝并给出原因；星表、时间、过境、回放等本地能力照常可用",
        "未开启断网档时说明联网需显式允许，本步不发起任何网络请求",
        "mbdsdr_ai/net_policy.py、mbdsdr_ai/local_ai_probe.py、mbdsdr_ai/agent.py:693",
    )
    from mbdsdr_ai.net_policy import network_disabled
    offline = network_disabled()
    if offline:
        try:
            from types import SimpleNamespace
            from mbdsdr_ai.agent import MBDSDRAgent
            from mbdsdr_ai.tool_registry import ToolRegistry
            reg = ToolRegistry()
            MBDSDRAgent._register_web_tools(SimpleNamespace(tool_registry=reg))
            res = reg.call("web_fetch_url", {"url": "https://example.com"})
            print("  联网能力：离线：显式拒绝（web_fetch_url 未发起请求）")
            detail = (res.content or res.error or "").splitlines()
            print(f"    工具实际返回：{detail[0] if detail else '（空）'}")
        except Exception as exc:  # noqa: BLE001
            print(f"  联网工具检查失败：{type(exc).__name__}: {exc}")
        try:
            from mbdsdr_ai.local_ai_probe import probe_endpoint
            probe = probe_endpoint("https://api.siliconflow.cn/v1")
            print(f"  模型 API 探测：离线：显式拒绝（status={probe['status']}，未发起连接）")
        except Exception as exc:  # noqa: BLE001
            print(f"  模型 API 探测检查失败：{type(exc).__name__}: {exc}")
    else:
        from mbdsdr_ai.net_policy import network_allowed
        print(f"  当前未开启 MBDSDR_OFFLINE；显式允许联网={network_allowed()}。"
              "本步不发起任何网络请求；断网档请用 --mode offline。")
    count, naked, _ = _catalog_counts()
    if count is None:
        print("  本地可用：星表未加载（原因见第 2 步）；其余本地能力不冒充可用。")
    else:
        print(f"  本地可用：星表 {count} 颗（≤6.5 等 {naked} 颗）、时间引擎、"
              "过境缓存与随仓回放样本均可离线使用。")


def step_finish(mode: str, minutes: float, total_minutes: float,
                started: float) -> int:
    _step(
        8, minutes, "收尾检查",
        "本轮演示的终端输出 + docs/demo-scripts.md 节奏表",
        "8 步走完；预计总时长 ≤12 分钟；退出码 0",
        "任何一步缺条件都应留下「缺什么、为什么」的原话，不用凑数内容补齐",
        "docs/demo-scripts.md、python scripts/demo_chain.py --mode <档位>",
    )
    elapsed = time.monotonic() - started
    print(f"  完成步骤：{TOTAL_STEPS}/{TOTAL_STEPS}")
    print(f"  预计总时长：{total_minutes:g} 分钟；实际耗时：{_fmt_seconds(elapsed)}；"
          "口径上限 12 分钟")
    print("  诚实边界：无硬件档的调谐/录音是降级说明，不是成功演示；"
          "过境与频率均来自本机缓存/随仓数据，未编造。")
    print("  退出码 0")
    return EXIT_OK


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="M1 三条教学链路演示（无硬件/断网/有硬件），每条 ≤12 分钟")
    parser.add_argument("--mode", required=True, choices=("nohw", "offline", "hw"),
                        help="nohw=无硬件，offline=断网，hw=有 SDR")
    args = parser.parse_args(argv)
    mode = str(args.mode)

    if mode == "offline":
        # 与手动 ``MBDSDR_OFFLINE=1 python ...`` 等价：先设环境，再导入业务模块。
        os.environ["MBDSDR_OFFLINE"] = "1"

    started = time.monotonic()
    total_minutes = _print_header(mode)
    minutes = MODE_MINUTES[mode]

    if mode == "hw":
        code = _hw_preflight()
        if code != EXIT_OK:
            return code

    ctx = step_time_and_station(mode, minutes[0])
    step_catalog(mode, minutes[1])
    pass_ctx = step_passes(mode, minutes[2], ctx)
    step_select_tune(mode, minutes[3], ctx, pass_ctx)
    step_record_replay(mode, minutes[4])
    step_ai(mode, minutes[5])
    step_offline(mode, minutes[6])
    return step_finish(mode, minutes[7], total_minutes, started)


if __name__ == "__main__":
    raise SystemExit(main())
