#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
天空文化图册（culture gallery）
===============================

为 ``mbdsdr_ai.skyengine.constellations.system_ids()`` 里的每一套天空文化
各渲染一张离屏预览图，并生成 ``index.html`` 图册与 ``report.json`` 清单。

口径（诚实优先）：
  * 所有图共用同一固定站址、同一模拟时刻、同一视角与 FOV；
  * ``QT_QPA_PLATFORM=offscreen`` 离屏渲染，不代表真实夜空；
  * 只打开当前文化体系，其余文化全关；
  * 单个文化失败（异常、超时、段错误）只记账，继续下一个；有任何失败
    时退出码为 1。

每张图在独立子进程里渲染：这样既能把单张渲染时间卡在 ``--timeout`` 内，
也能让崩溃/卡死的文化不拖垮整批。

用法::

    python scripts/culture_gallery.py
    python scripts/culture_gallery.py --out dist/culture-gallery --size 1280x800
    python scripts/culture_gallery.py --only japanese,indian --fov 90
    python scripts/culture_gallery.py --timeout 60 --unix 1790000000

产物::

    <out>/<id>.png       每套文化一张预览图
    <out>/index.html     缩略图 + 显示名 + 许可 + 来源 + 统计
    <out>/report.json    逐包清单（文件名/字节数/渲染耗时/UTC/失败项）
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# offscreen 插件不带字体库：Windows 下不指到系统字体目录，文字会变方框。
if sys.platform == "win32":
    _win_fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    if os.path.isdir(_win_fonts):
        os.environ.setdefault("QT_QPA_FONTDIR", _win_fonts)
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")
os.environ.setdefault("QT_SCALE_FACTOR", "1.0")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DESKTOP = os.path.join(_REPO, "desktop")
for _path in (_REPO, _DESKTOP):
    if _path not in sys.path:
        sys.path.insert(0, _path)

_DEFAULT_OUT = os.path.join("dist", "culture-gallery")
_SIZE_RE = re.compile(r"^(\d+)x(\d+)$")

#: 固定站址（中性示例值，不指向任何真实用户位置）。
DEFAULT_LAT = 43.9
DEFAULT_LON = 125.3
DEFAULT_ALT = 0.0
#: 固定视角：朝向正南、画面中心仰角 45°，配合 90° FOV 约覆盖 0°–90°。
DEFAULT_CENTER_AZ = 180.0
DEFAULT_CENTER_ALT = 45.0
DEFAULT_FOV = 90.0
DEFAULT_TIMEOUT = 120.0

DISCLAIMER = "固定视角、离屏渲染、非真实夜空"


def _human_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / 1024 / 1024:.2f} MB"


def _utc_iso(unix_ts: float) -> str:
    return datetime.fromtimestamp(
        float(unix_ts), timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z")


def _resolve_out(raw: str) -> str:
    path = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(_REPO, raw))
    os.makedirs(path, exist_ok=True)
    return path


def _safe_file_stem(system_id: str) -> str:
    """文化 id → 文件名；只保留 ASCII 字母数字、下划线与短横线。"""
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(system_id))
    return stem.strip("._") or "culture"


def _all_system_ids() -> Tuple[str, ...]:
    from mbdsdr_ai.skyengine import constellations as constel
    return tuple(constel.system_ids())


def _pack_meta(system_id: str) -> Dict[str, Any]:
    """包元数据与统计（groups/edges/names）；未知 id 抛 KeyError。"""
    from mbdsdr_ai.skyengine.sky_culture import get_pack
    pack = get_pack(system_id)
    return {
        "id": pack.id,
        "display_name": pack.display_name or pack.native_name or pack.id,
        "native_name": pack.native_name,
        "license": pack.license,
        "license_url": pack.license_url,
        "source": pack.source,
        "source_url": pack.source_url,
        "attribution": pack.attribution,
        "stats": {
            "groups": len(pack.groups),
            "edges": len(pack.edges),
            "names": len(pack.names),
        },
    }


def _clean_old_artifacts(out_dir: str) -> None:
    for name in os.listdir(out_dir):
        if name.lower().endswith(".png") or (
                name.startswith(".") and name.endswith(".child.json")):
            try:
                os.remove(os.path.join(out_dir, name))
            except OSError:
                pass


def _child_report_path(out_dir: str, system_id: str) -> str:
    return os.path.join(out_dir, f".{_safe_file_stem(system_id)}.child.json")


def _segments_of(item):
    segs = item.segments
    return segs() if callable(segs) else segs


def _pick_view_center(system_id: str, lat: float, lon: float, alt_m: float,
                      unix_ts: float, fov_deg: float,
                      fallback_az: float, fallback_alt: float
                      ) -> Dict[str, Any]:
    """在固定站址/时刻下，为这套文化挑一个能看见最多连线的画面中心。

    只在「两端都在地平以上」的线段里找；用 15°×10° 的粗网格搜索使
    FOV 半球内可见段数最多的方向。找不到（整层都在地平以下）就退回
    固定默认方向——失败也不伪装成有内容。
    """
    import math

    from mbdsdr_ai.celestial_geometry import (
        GroundStation, azalt_from_vec, j2000_to_altaz, vec_from_radec,
    )
    from mbdsdr_ai.skyengine import constellations as constel
    from mbdsdr_ai.skyengine import jtime

    layer = constel.constellation_layer(system_id)
    station = GroundStation(lon, lat, alt_m)
    jd = jtime.unix_to_jd(float(unix_ts))

    visible: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    for item in layer.items:
        for star_a, star_b in _segments_of(item):
            points = []
            for star in (star_a, star_b):
                az, alt = azalt_from_vec(j2000_to_altaz(
                    vec_from_radec(star.ra_deg, star.dec_deg), station, jd))
                if not math.isfinite(alt) or alt <= 0.0:
                    break
                points.append((az % 360.0, alt))
            if len(points) == 2:
                visible.append((points[0], points[1]))

    fallback = {
        "center_az": fallback_az,
        "center_alt": fallback_alt,
        "framing": "fixed-fallback",
        "above_horizon_segments": len(visible),
        "visible_segments": 0,
    }
    if not visible:
        return fallback

    radius = float(fov_deg) * 0.5

    def _angle(az1, alt1, az2, alt2):
        a1, e1, a2, e2 = map(math.radians, (az1, alt1, az2, alt2))
        cos_d = (math.sin(e1) * math.sin(e2)
                 + math.cos(e1) * math.cos(e2) * math.cos(a1 - a2))
        return math.degrees(math.acos(max(-1.0, min(1.0, cos_d))))

    best = None
    for az in range(0, 360, 15):
        for alt in range(10, 81, 10):
            count = 0
            for (p1, p2) in visible:
                if (_angle(p1[0], p1[1], az, alt) <= radius
                        and _angle(p2[0], p2[1], az, alt) <= radius):
                    count += 1
            key = (-count, abs(alt - 45.0), abs(az - 180.0))
            if best is None or key < best[0]:
                best = (key, az, alt, count)
    _key, az, alt, count = best
    return {
        "center_az": float(az),
        "center_alt": float(alt),
        "framing": "auto",
        "above_horizon_segments": len(visible),
        "visible_segments": count,
    }


# ---------------------------------------------------------------------------
# 子进程：渲染单张图
# ---------------------------------------------------------------------------
def _render_child(args: argparse.Namespace) -> int:
    report_path = args.child_report
    row: Dict[str, Any] = {
        "id": args.only,
        "ok": False,
        "file": None,
        "bytes": None,
        "width": None,
        "height": None,
        "render_ms": None,
        "utc": _utc_iso(args.unix),
        "framing": None,
        "center_az": None,
        "center_alt": None,
        "above_horizon_segments": None,
        "visible_segments": None,
        "error": None,
        "stack_frames": [],
    }
    started = time.perf_counter()
    widget = None
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)

        from mbdsdr_ai.new_spacetime import get_time_engine
        from mbdsdr_ai.skyengine import constellations as constel
        from rf_sky_view import RFSkyView

        if args.only not in constel.system_ids():
            raise KeyError(f"未知天空文化体系: {args.only!r}")

        engine = get_time_engine()
        engine.set_unix(float(args.unix))
        engine.pause()

        frame = _pick_view_center(
            args.only, args.lat, args.lon, args.alt, args.unix, args.fov,
            args.center_az, args.center_alt)
        row.update(frame)

        view = RFSkyView()
        widget = view
        # 交互形态给控件设了 400x376 的最小尺寸；图册要求严格按 --size
        # 出图，这里解除下限（只影响本脚本内的截图实例）。
        view.setMinimumSize(0, 0)
        view.resize(args.width, args.height)
        view.set_observer(args.lat, args.lon, args.alt)
        view.set_constellation_systems([args.only])
        view.view_state.look_at(frame["center_az"], frame["center_alt"])
        view.view_state.zoom_to(args.fov)
        # 图册只保留星空与所选文化连线：关 HUD/网格/地景/轨迹等。
        for key in ("hud", "azgrid", "eqgrid", "orbits",
                    "landscape", "atmosphere", "dso", "sats"):
            view.set_layer(key, False)
        view.set_show_compass(False)
        # 沉浸态：收起搜索框/红色角标/大状态卡，只留紧凑三行读数。
        view.set_immersive(True)

        view.resize(args.width, args.height)
        view.show()
        app.processEvents()

        render_started = time.perf_counter()
        pixmap = view.grab()
        row["render_ms"] = round(
            (time.perf_counter() - render_started) * 1000.0, 1)
        if pixmap.isNull() or pixmap.width() <= 0 or pixmap.height() <= 0:
            raise RuntimeError("grab() 返回空图像")
        row["width"] = pixmap.width()
        row["height"] = pixmap.height()

        filename = f"{_safe_file_stem(args.only)}.png"
        target = os.path.join(args.out, filename)
        if not pixmap.save(target, "PNG"):
            raise RuntimeError(f"QPixmap.save 失败：{target}")
        row.update(ok=True, file=filename,
                   bytes=os.path.getsize(target))
    except Exception as exc:  # noqa: BLE001 - 单张失败只记账
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["stack_frames"] = [
            f"{f.filename}:{f.lineno} in {f.name}"
            for f in traceback.extract_tb(exc.__traceback__)[-3:]][::-1]
    finally:
        row["elapsed_ms"] = round(
            (time.perf_counter() - started) * 1000.0, 1)
        if widget is not None:
            try:
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                pass
        try:
            from PySide6.QtCore import QEvent
            app = QApplication.instance()
            if app is not None:
                app.sendPostedEvents(None, QEvent.DeferredDelete)
                app.processEvents()
        except Exception:  # noqa: BLE001
            pass
        if report_path:
            with open(report_path, "w", encoding="utf-8") as fh:
                json.dump(row, fh, ensure_ascii=False, indent=2)
    return 0 if row["ok"] else 1


# ---------------------------------------------------------------------------
# 父进程：逐包调度
# ---------------------------------------------------------------------------
def _failed_row(system_id: str, error: str, utc: str,
                meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    row = {
        "id": system_id,
        "file": f"{_safe_file_stem(system_id)}.png",
        "bytes": None,
        "width": None,
        "height": None,
        "render_ms": None,
        "elapsed_ms": 0.0,
        "utc": utc,
        "framing": None,
        "center_az": None,
        "center_alt": None,
        "above_horizon_segments": None,
        "visible_segments": None,
        "status": "failed",
        "error": error,
        "stack_frames": [],
    }
    if meta:
        row.update(meta)
    return row


def _run_culture(system_id: str, out_dir: str, args: argparse.Namespace,
                 utc: str) -> Dict[str, Any]:
    try:
        meta = _pack_meta(system_id)
    except KeyError:
        return _failed_row(system_id, f"未知天空文化体系: {system_id!r}", utc)
    except Exception as exc:  # noqa: BLE001 - 单包元数据坏了不影响其它
        return _failed_row(
            system_id, f"{type(exc).__name__}: {exc}", utc)

    child_report = _child_report_path(out_dir, system_id)
    cmd = [
        sys.executable, os.path.abspath(__file__),
        "--child", "--only", system_id,
        "--out", out_dir,
        "--size", f"{args.width}x{args.height}",
        "--fov", str(args.fov),
        "--unix", str(args.unix),
        "--lat", str(args.lat),
        "--lon", str(args.lon),
        "--alt", str(args.alt),
        "--center-az", str(args.center_az),
        "--center-alt", str(args.center_alt),
        "--child-report", child_report,
    ]
    started = time.perf_counter()
    timeout_error = None
    try:
        proc = subprocess.run(
            cmd, cwd=_REPO, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=args.timeout)
        returncode = proc.returncode
        stderr = (proc.stderr or "").strip()
    except subprocess.TimeoutExpired as exc:
        returncode = -1
        timeout_error = f"渲染超时（>{args.timeout:g}s）"
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        try:
            os.remove(os.path.join(out_dir, f"{_safe_file_stem(system_id)}.png"))
        except OSError:
            pass
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)

    child: Dict[str, Any] = {}
    try:
        with open(child_report, "r", encoding="utf-8") as fh:
            child = json.load(fh)
    except (OSError, ValueError):
        pass
    finally:
        try:
            os.remove(child_report)
        except OSError:
            pass

    row = {
        **meta,
        "id": system_id,
        "file": f"{_safe_file_stem(system_id)}.png",
        "bytes": child.get("bytes"),
        "width": child.get("width"),
        "height": child.get("height"),
        "render_ms": child.get("render_ms"),
        "elapsed_ms": elapsed_ms,
        "utc": utc,
        "framing": child.get("framing"),
        "center_az": child.get("center_az"),
        "center_alt": child.get("center_alt"),
        "above_horizon_segments": child.get("above_horizon_segments"),
        "visible_segments": child.get("visible_segments"),
        "status": "ok" if (returncode == 0 and child.get("ok")) else "failed",
        "error": None,
        "stack_frames": child.get("stack_frames") or [],
    }
    if row["status"] == "ok":
        target = os.path.join(out_dir, row["file"])
        if not os.path.isfile(target):
            row["status"] = "failed"
            row["error"] = f"子进程报告成功但文件不存在：{row['file']}"
        elif row["bytes"] is None:
            row["bytes"] = os.path.getsize(target)
    else:
        row["error"] = (
            timeout_error
            or child.get("error")
            or f"子进程退出码 {returncode}"
            + (f"：{stderr.splitlines()[-1]}" if stderr else ""))
        # 失败时删掉可能写了一半的图，避免被误当成本次证据。
        try:
            os.remove(os.path.join(out_dir, row["file"]))
        except OSError:
            pass
        row["file"] = None
    return row


# ---------------------------------------------------------------------------
# 产物
# ---------------------------------------------------------------------------
def _write_report(out_dir: str, report: Dict[str, Any]) -> str:
    path = os.path.join(out_dir, "report.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    return path


def _card(row: Dict[str, Any]) -> str:
    name = html.escape(str(row.get("display_name") or row["id"]))
    native = html.escape(str(row.get("native_name") or ""))
    license_text = html.escape(str(row.get("license") or ""))
    license_url = str(row.get("license_url") or "")
    source_url = str(row.get("source_url") or "")
    stats = row.get("stats") or {}
    stats_line = ("groups {g} · edges {e} · names {n}".format(
        g=stats.get("groups", 0), e=stats.get("edges", 0),
        n=stats.get("names", 0)))
    view_line = ""
    if row.get("status") == "ok":
        image = (
            f'<a href="{html.escape(row["file"])}">'
            f'<img loading="lazy" src="{html.escape(row["file"])}" '
            f'alt="{name}"></a>')
        meta_bits = [stats_line]
        if row.get("bytes") is not None:
            meta_bits.append(_human_size(int(row["bytes"])))
        if row.get("render_ms") is not None:
            meta_bits.append(f"渲染 {float(row['render_ms']):.0f} ms")
        if row.get("visible_segments") is not None:
            view_line = (
                f'<div class="meta">视向 az {float(row["center_az"]):.0f}° · '
                f'alt {float(row["center_alt"]):.0f}°（自动取景）；'
                f'地平以上可见 {row["visible_segments"]}/'
                f'{row["above_horizon_segments"]} 段</div>')
    else:
        image = f'<div class="err">渲染失败<br><code>{html.escape(str(row.get("error") or ""))}</code></div>'
        meta_bits = [stats_line]
    lic = (f'<a href="{html.escape(license_url)}">{license_text}</a>'
           if license_url else license_text)
    src = (f'<a href="{html.escape(source_url)}">{html.escape(source_url)}</a>'
           if source_url else "（无来源链接）")
    return f"""
    <figure class="card">
      {image}
      <figcaption>
        <h3>{name}</h3>
        <div class="native">{native}</div>
        <div class="meta">{' · '.join(meta_bits)}</div>
        {view_line}
        <div class="meta">许可：{lic}</div>
        <div class="meta source">来源：{src}</div>
        <div class="id">{html.escape(row['id'])}</div>
      </figcaption>
    </figure>"""


def _write_index(out_dir: str, report: Dict[str, Any]) -> str:
    rows = report["packs"]
    cards = "".join(_card(row) for row in rows)
    totals = report["totals"]
    path = os.path.join(out_dir, "index.html")
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>天空文化图册</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 28px; background: #0d1117; color: #d7dde5;
         font-family: system-ui, "Segoe UI", "Microsoft YaHei", sans-serif; }}
  h1 {{ margin: 0 0 6px; font-size: 24px; }}
  .notice {{ border-left: 4px solid #d29922; background: #1c1a12;
             padding: 10px 14px; margin: 14px 0 8px; line-height: 1.7; }}
  .notice strong {{ color: #f0c36d; }}
  .summary {{ color: #8b949e; font-size: 13px; margin-bottom: 22px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
           gap: 18px; }}
  .card {{ margin: 0; background: #161b22; border: 1px solid #30363d;
           border-radius: 10px; overflow: hidden; }}
  .card img {{ display: block; width: 100%; height: auto; background: #000; }}
  figcaption {{ padding: 12px 14px 14px; }}
  h3 {{ margin: 0 0 2px; font-size: 16px; color: #e6edf3; }}
  .native {{ color: #b9c3ce; font-size: 13px; margin-bottom: 8px; }}
  .meta {{ color: #8b949e; font-size: 12.5px; margin-top: 3px; }}
  .meta a {{ color: #6cb6ff; text-decoration: none; word-break: break-all; }}
  .id {{ color: #57606a; font-size: 11.5px; margin-top: 8px; font-family: ui-monospace, monospace; }}
  .err {{ padding: 28px 14px; background: #2d1518; color: #ffb3b3;
          font-size: 13px; text-align: center; word-break: break-all; }}
</style>
</head>
<body>
  <h1>天空文化图册</h1>
  <div class="notice">
    <strong>{DISCLAIMER}</strong>：所有预览图使用同一固定站址
    （{report['station']['lat']:.1f}°N, {report['station']['lon']:.1f}°E,
    海拔 {report['station']['alt_m']:.0f} m）、同一模拟时刻
    （{html.escape(report['utc'])}）与 {report['view']['fov_deg']:g}° 视场角；
    为让每套文化的连线可见，画面中心按其当时地平以上的连线自动取景。
    图册只用于机械确认「该文化的连线能画出来」，不是观测记录，
    也不代表这个方向、这个时刻的真实星空。
  </div>
  <div class="summary">
    共 {totals['selected']} 套文化：成功 {totals['ok']} / 失败
    {totals['failed']}，总大小 {_human_size(totals['total_bytes'])}，
    生成耗时 {totals['duration_ms'] / 1000.0:.1f}s。
    渲染环境：离屏（QT_QPA_PLATFORM=offscreen）。
  </div>
  <div class="grid">{cards}
  </div>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return path


def _print_summary(report: Dict[str, Any], report_path: str,
                   index_path: str) -> None:
    totals = report["totals"]
    print(f"[culture-gallery] {totals['selected']} 套文化：成功 "
          f"{totals['ok']} / 失败 {totals['failed']}"
          f"（{_human_size(totals['total_bytes'])}，"
          f"{totals['duration_ms'] / 1000.0:.1f}s）")
    print(f"[culture-gallery] 固定站址 {report['station']['lat']:.1f}N/"
          f"{report['station']['lon']:.1f}E，UTC {report['utc']}，"
          f"FOV {report['view']['fov_deg']:g}°")
    for row in report["packs"]:
        if row["status"] == "ok":
            print(f"  OK   {row['id']:<32} {row['file']} "
                  f"({_human_size(int(row['bytes'] or 0))}, "
                  f"{row['render_ms']} ms)")
        else:
            print(f"  FAIL {row['id']:<32} {row['error']}")
    print(f"[culture-gallery] 图册：{index_path}")
    print(f"[culture-gallery] 报告：{report_path}")


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="为每套天空文化离屏渲染一张预览图并生成 HTML 图册")
    ap.add_argument("--out", default=_DEFAULT_OUT,
                    help=f"输出目录（默认 {_DEFAULT_OUT}）")
    ap.add_argument("--size", default="1280x800", metavar="WxH",
                    help="单张图尺寸（默认 1280x800）")
    ap.add_argument("--fov", type=float, default=DEFAULT_FOV,
                    help=f"视场角（默认 {DEFAULT_FOV:g}，范围 0.5–120）")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    metavar="SECONDS",
                    help=f"单张渲染超时（默认 {DEFAULT_TIMEOUT:g}s）")
    ap.add_argument("--only", default=None,
                    help="只渲染指定文化，逗号分隔（默认全部）")
    ap.add_argument("--unix", type=float, default=None,
                    help="固定模拟时刻（Unix 秒）；缺省取启动时刻")
    # 内部/可复现参数：固定站址与视角，默认值即任务口径。
    ap.add_argument("--lat", type=float, default=DEFAULT_LAT, help=argparse.SUPPRESS)
    ap.add_argument("--lon", type=float, default=DEFAULT_LON, help=argparse.SUPPRESS)
    ap.add_argument("--alt", type=float, default=DEFAULT_ALT, help=argparse.SUPPRESS)
    ap.add_argument("--center-az", type=float, default=DEFAULT_CENTER_AZ,
                    help=argparse.SUPPRESS)
    ap.add_argument("--center-alt", type=float, default=DEFAULT_CENTER_ALT,
                    help=argparse.SUPPRESS)
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--child-report", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    match = _SIZE_RE.match(str(args.size).strip())
    if not match or int(match.group(1)) <= 0 or int(match.group(2)) <= 0:
        ap.error(f"--size 需形如 1280x800，收到 {args.size!r}")
    args.width, args.height = int(match.group(1)), int(match.group(2))
    if not (0.5 <= args.fov <= 120.0):
        ap.error(f"--fov 需在 0.5–120，收到 {args.fov!r}")
    if args.timeout <= 0:
        ap.error(f"--timeout 需大于 0，收到 {args.timeout!r}")
    return args


def _selected_ids(args: argparse.Namespace) -> Tuple[List[str], List[str]]:
    known = _all_system_ids()
    if args.only is None:
        return list(known), []
    requested = [x.strip() for x in str(args.only).split(",") if x.strip()]
    if not requested:
        raise ValueError("--only 不能为空")
    selected: List[str] = []
    unknown: List[str] = []
    for sid in requested:
        if sid in known:
            if sid not in selected:
                selected.append(sid)
        elif sid not in unknown:
            unknown.append(sid)
    return selected, unknown


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)

    if args.child:
        if not args.only or "," in args.only:
            print("--child 需要且只接受一个 --only", file=sys.stderr)
            return 2
        return _render_child(args)

    if args.unix is None:
        args.unix = time.time()
    utc = _utc_iso(args.unix)
    out_dir = _resolve_out(args.out)

    try:
        selected, unknown = _selected_ids(args)
    except ValueError as exc:
        print(f"[culture-gallery] {exc}", file=sys.stderr)
        return 2

    _clean_old_artifacts(out_dir)
    started = time.perf_counter()

    rows: List[Dict[str, Any]] = []
    for sid in unknown:
        rows.append(_failed_row(sid, f"未知天空文化体系: {sid!r}", utc))
    for sid in selected:
        rows.append(_run_culture(sid, out_dir, args, utc))

    duration_ms = round((time.perf_counter() - started) * 1000.0, 1)
    ok_rows = [r for r in rows if r["status"] == "ok"]
    failed_rows = [r for r in rows if r["status"] != "ok"]
    report = {
        "schema": "mbdsdr.culture_gallery.v1",
        "generated_utc": _utc_iso(time.time()),
        "utc": utc,
        "simulated_unix": float(args.unix),
        "out_dir": out_dir,
        "size": f"{args.width}x{args.height}",
        "station": {"lat": args.lat, "lon": args.lon, "alt_m": args.alt},
        "view": {
            "framing": "auto",
            "fallback_center_az_deg": args.center_az,
            "fallback_center_alt_deg": args.center_alt,
            "fov_deg": args.fov,
        },
        "disclaimer": DISCLAIMER,
        "totals": {
            "selected": len(rows),
            "ok": len(ok_rows),
            "failed": len(failed_rows),
            "total_bytes": sum(int(r.get("bytes") or 0) for r in ok_rows),
            "duration_ms": duration_ms,
        },
        "packs": rows,
        "failures": [
            {"id": r["id"], "error": r.get("error")} for r in failed_rows],
    }
    index_path = _write_index(out_dir, report)
    report_path = _write_report(out_dir, report)
    _print_summary(report, report_path, index_path)
    return 0 if not failed_rows else 1


if __name__ == "__main__":
    sys.exit(main())
