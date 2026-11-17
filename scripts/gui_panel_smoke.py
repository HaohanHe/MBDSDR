#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
桌面面板离屏走查（GUI panel smoke）
===================================

按 ``desktop/panel_registry.py`` 的声明逐个构造面板，离屏截一张 PNG，
并把「构造是否成功 / 耗时 / 异常」落成 ``report.json``。

这是**证据采集**，不是审美判断，也不替代人工走查：截图只证明面板在该尺寸
下能构造并画出首帧，不证明布局合理。

用法::

    python scripts/gui_panel_smoke.py
    python scripts/gui_panel_smoke.py --out dist/gui-smoke --size 1280x800
    python scripts/gui_panel_smoke.py --only spectrum
    python scripts/gui_panel_smoke.py --no-theme     # 对照组：不套应用主题
    python scripts/gui_panel_smoke.py --settle-ms 0  # 不等稳定态（中间态对照）

稳定态：面板构造后先跑 ``--settle-ms``（默认 800，0 表示不等）毫秒的事件
循环再截图。设备面板的枚举在后台线程里跑（首帧后 400 ms 才发起），构造完
立刻 grab 只会拍到「正在枚举设备…」这一中间态；等事件循环把结果派发回来，
截图才代表最终态。面板若暴露 ``enumeration_finished`` / ``is_enumerating``
之类可测属性，满足条件时提前结束；没有就固定等满，不靠猜。
``report.json`` 的每个面板都记 ``settle_ms``（配置值）与
``settle_elapsed_ms``（实际等待），以及 ``texts``（截图时刻抓到的关键文本，
device/gain 必抓状态行与值标签），便于判断拍的是最终态还是中间态。

主题：建好 QApplication 后、构造任何面板之前，按应用启动路径调用
``desktop/themes.py`` 的 ``get_theme(DEFAULT_THEME).apply(app)``（即
``MainWindow._apply_theme`` 内部调用的同一入口），不在这里另写 QSS。
``report.json`` 记录 ``theme_applied`` / ``theme_entry`` / ``theme_name``
（以及 ``theme``：生效主题名或 ``"unavailable: 原因"``）；
若主题无法套用则 ``theme_applied=false``、``theme_entry`` 仍写尝试过的入口、
``theme="unavailable: 原因"``，面板照常截图，但截图证据的结论只能是「未套主题」。

判定口径（诚实优先）：
  * ``PanelSpec.factory is None`` → ``skipped``，原因写「注册表未声明工厂」；
  * 可选面板工厂返回 ``None``（``_optional_factory`` 吞掉导入/构造异常）→
    ``skipped``，原因如实写「工厂返回 None，模块/类缺失或构造失败已被工厂吞掉」；
  * 构造/渲染/存图抛异常 → ``failed``，记录异常类型、消息与栈帧（最多 3 帧）；
  * 单个面板失败只记账，继续走下一个；有任何 ``failed`` 退出码 1。

环境：脚本自己 setdefault ``QT_QPA_PLATFORM=offscreen`` / ``MBDSDR_NO_HW=1`` /
``MBDSDR_OFFLINE=1`` / ``MBDSDR_NULL_AUDIO=1``，并把 HOME/APPDATA/QSettings
重定向到一次性临时目录，绝不读写用户真实配置。运行前请确认没有 pytest Qt
套件正在跑（仓库 Qt 用例共用一把全局锁；本脚本是独立探针，不参与该锁）。

产物：``<out>/*.png`` + ``<out>/report.json``（每次运行先清掉旧 PNG，避免
上一次的截图被当成这一次的证据）。
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from typing import Dict, List, Optional

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# offscreen 插件不带字体库：Windows 下不指到系统字体目录，所有文字都是方框
# （实测 QFontDatabase.families() == 0）。这只影响截图可读性，不影响构造。
if sys.platform == "win32":
    _win_fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    if os.path.isdir(_win_fonts):
        os.environ.setdefault("QT_QPA_FONTDIR", _win_fonts)
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("QT_SCALE_FACTOR", "1.0")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DESKTOP = os.path.join(_REPO, "desktop")
_DEFAULT_OUT = os.path.join("dist", "gui-smoke")
_SIZE_RE = re.compile(r"^(\d+)x(\d+)$")


def _human_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / 1024 / 1024:.2f} MB"


def _isolate_environment() -> str:
    """HOME/APPDATA 指到一次性临时目录；返回临时根。"""
    root = tempfile.mkdtemp(prefix="mbdsdr_gui_smoke_")
    home = os.path.join(root, "home")
    os.makedirs(home, exist_ok=True)
    for var in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[var] = home
    return root


def _resolve_out(raw: str) -> str:
    path = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(_REPO, raw))
    os.makedirs(path, exist_ok=True)
    return path


def _clean_old_pngs(out_dir: str) -> int:
    removed = 0
    for name in os.listdir(out_dir):
        if name.lower().endswith(".png"):
            try:
                os.remove(os.path.join(out_dir, name))
                removed += 1
            except OSError:
                pass
    return removed


def _size_of(path: Optional[str]) -> Optional[int]:
    if not path:
        return None
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _frames(exc: BaseException, limit: int = 3) -> List[str]:
    """栈帧人话列表（最内层在前）；只留位置，不贴大段日志。"""
    frames = traceback.extract_tb(exc.__traceback__)
    out = []
    for frame in reversed(frames[-limit:]):
        out.append(f"{frame.filename}:{frame.lineno} in {frame.name}")
    return out


def _pick_specs(registry_obj, only: Optional[str]):
    specs = registry_obj.list()
    if not only:
        return specs, None
    wanted = [s for s in specs if s.id == only or s.name == only]
    if not wanted:
        return None, (f"--only {only!r} 未匹配任何面板 id/名称；可用 id："
                      + ", ".join(s.id for s in specs))
    return wanted, None


def _apply_app_theme(app):
    """按应用启动路径套用真实主题。

    与 ``MainWindow._apply_theme`` 调的是同一个入口（``Theme.apply(app)``：
    装 QPalette + 字体 + app 级 QSS）。这里不写任何自己的 QSS。

    返回 ``(applied, entry, theme_name, detail)``。主题入口不可导入或 apply
    抛异常时如实降级（applied=False），由调用方写进报告。
    """
    entry = "desktop/themes.py:Theme.apply"
    try:
        from themes import DEFAULT_THEME, get_theme
    except Exception as exc:  # noqa: BLE001 - 降级不拦截图
        return (False, entry, None,
                f"导入 themes 失败（{type(exc).__name__}: {exc}）")
    try:
        theme = get_theme(DEFAULT_THEME)
        theme.apply(app)
        return True, entry, theme.name, None
    except Exception as exc:  # noqa: BLE001 - 降级不拦截图
        return (False, entry, None,
                f"Theme.apply 失败（{type(exc).__name__}: {exc}）")


def _palette_facts(widget, theme_name: Optional[str]) -> Dict[str, object]:
    """读面板实际继承到的 QPalette 背景/文字色，并与当前主题令牌对比。

    这是「截图里的浅底淡字是不是主题没套上」的判据：套主题后控件的
    Window/WindowText 应等于 ``tokens().theme_colors(theme_name)`` 的
    bg/text；未套主题时是平台默认调色板，通常对不上。

    Base 只记录、不参与判定：QSS 的 ``QWidget { background-color:
    palette(window); }`` 在 show()/polish 之后会把控件自身的 Base 角色
    重解成 window 色（实测 #2b2c2f → #000000），此时 Base 已不能代表
    「卡片底」，拿它对比 card 令牌只会误报。
    """
    from PySide6.QtGui import QPalette

    from tokens import contrast_ratio, tokens

    pal = widget.palette()
    bg = pal.color(QPalette.Window).name()
    card = pal.color(QPalette.Base).name()
    text = pal.color(QPalette.WindowText).name()
    facts: Dict[str, object] = {
        "bg": bg,
        "card": card,
        "text": text,
        "contrast_text_on_bg": round(contrast_ratio(text, bg), 2),
        "contrast_text_on_card": round(contrast_ratio(text, card), 2),
        "matches_theme_colors": None,
    }
    if theme_name:
        expected = tokens().theme_colors(theme_name)
        facts["matches_theme_colors"] = (
            bg.lower() == expected["bg"].lower()
            and text.lower() == expected["text"].lower()
        )
    return facts


def _enumeration_flag(widget) -> Optional[bool]:
    """读面板自己声明的 ``enumeration_finished``；没声明返回 None。"""
    finished = getattr(widget, "enumeration_finished", None)
    if finished is not None:
        value = finished() if callable(finished) else finished
        return bool(value)
    return None


def _settle(app, widget, settle_ms: int) -> float:
    """跑事件循环直到累计 ``settle_ms``；返回实际等待毫秒。

    提前结束只认面板自己声明的可测属性：``enumeration_finished`` 为 True，
    或 ``is_enumerating`` 先被观察到 True、随后变 False（刚构造时它可能还是
    False，直接当成「已结束」会又拍回中间态）。都没声明就固定等满，不猜。
    """
    if settle_ms <= 0:
        return 0.0
    started = time.perf_counter()
    deadline = started + settle_ms / 1000.0
    saw_enumerating = False
    while True:
        app.processEvents()
        if _enumeration_flag(widget) is True:
            break
        probe = getattr(widget, "is_enumerating", None)
        if probe is not None:
            value = bool(probe() if callable(probe) else probe)
            if value:
                saw_enumerating = True
            elif saw_enumerating:
                break
        if time.perf_counter() >= deadline:
            break
        time.sleep(0.02)
    return round((time.perf_counter() - started) * 1000.0, 1)


def _label_text(obj, attr: str) -> Optional[str]:
    """读控件/子控件的当前文本；缺失或已销毁返回 None。"""
    try:
        target = getattr(obj, attr, None)
        if target is None:
            return None
        getter = getattr(target, "currentText", None)
        if callable(getter):
            return getter()
        getter = getattr(target, "text", None)
        return getter() if callable(getter) else None
    except RuntimeError:
        return None


def _capture_texts(spec_id: str, widget) -> Dict[str, Optional[str]]:
    """截图时刻的关键文本：device 抓状态行/下拉框，gain 抓三档值标签。"""
    if spec_id == "device":
        return {"status": _label_text(widget, "status_label"),
                "device_combo": _label_text(widget, "dev_combo")}
    if spec_id == "gain":
        texts = {"total": _label_text(widget, "total_label")}
        for key in ("lna", "mixer", "vga"):
            texts[key] = _label_text(getattr(widget, key, None), "value_label")
        return texts
    return {}


def _run_one(spec, app, out_dir: str, width: int, height: int,
             theme_name: Optional[str] = None, settle_ms: int = 800
             ) -> Dict[str, object]:
    """构造 → resize → show → grab → 存图；异常折成记录，不向调用方抛。"""
    row: Dict[str, object] = {
        "id": spec.id,
        "name": spec.name,
        "area": spec.area,
        "ok": False,
        "status": "failed",
        "reason": None,
        "error": None,
        "error_type": None,
        "stack_frames": [],
        "elapsed_ms": None,
        "screenshot": None,
        "screenshot_bytes": None,
        "screenshot_size": None,
        "widget_class": None,
        "palette": None,
        "settle_ms": settle_ms,
        "settle_elapsed_ms": None,
        "texts": {},
    }
    if spec.factory is None:
        row.update(status="skipped", reason="注册表未声明工厂（factory is None）")
        return row

    started = time.perf_counter()
    widget = None
    try:
        widget = spec.factory()
        if widget is None:
            row.update(
                status="skipped",
                reason="工厂返回 None（可选面板：模块/类缺失，或构造失败已被 "
                       "工厂自身的 try/except 吞掉）",
            )
            return row
        row["widget_class"] = type(widget).__name__

        widget.resize(width, height)
        widget.show()
        app.processEvents()
        # 有些面板在首次空闲（QTimer.singleShot(0)）里才收窄/折叠分组框，
        # 而 resize() 会被「折叠前」的最小尺寸钳住且之后不会回弹（实测
        # sat_track 被钳到 1222 高）。等延后初始化跑完再按目标尺寸调一次。
        widget.resize(width, height)
        app.processEvents()

        # 等稳定态：设备的枚举在后台线程里（首帧后 400 ms 才发起），马上
        # grab 只会拍到「正在枚举设备…」。等事件循环把结果派发回来再截图。
        row["settle_elapsed_ms"] = _settle(app, widget, settle_ms)

        row["texts"] = _capture_texts(spec.id, widget)
        pixmap = widget.grab()
        if pixmap.isNull() or pixmap.width() <= 0 or pixmap.height() <= 0:
            raise RuntimeError("grab() 返回空图像")
        row["palette"] = _palette_facts(widget, theme_name)
        shot_name = f"{spec.id}.png"
        shot_path = os.path.join(out_dir, shot_name)
        if not pixmap.save(shot_path, "PNG"):
            raise RuntimeError(f"QPixmap.save 失败：{shot_path}")

        row.update(
            ok=True,
            status="ok",
            screenshot=shot_name,
            screenshot_bytes=_size_of(shot_path),
            screenshot_size=[pixmap.width(), pixmap.height()],
        )
    except Exception as exc:  # noqa: BLE001 - 单面板失败只记账，继续下一个
        row.update(
            error=f"{type(exc).__name__}: {exc}",
            error_type=type(exc).__name__,
            stack_frames=_frames(exc),
        )
    finally:
        row["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
        if widget is not None:
            try:
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                pass              # C++ 侧已销毁，包装器悬空
        try:
            from PySide6.QtCore import QEvent
            app.sendPostedEvents(None, QEvent.DeferredDelete)
        except Exception:  # noqa: BLE001
            pass
        app.processEvents()
        gc.collect()
    return row


def _write_report(out_dir: str, report: Dict[str, object]) -> str:
    path = os.path.join(out_dir, "report.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    return path


def _print_summary(report: Dict[str, object], report_path: str) -> None:
    totals = report["totals"]
    print(f"[gui-smoke] 主题：{report.get('theme_entry')} "
          f"(theme_applied={report.get('theme_applied')}，"
          f"theme_name={report.get('theme_name')})")
    print(f"[gui-smoke] {totals['total']} 个面板：成功 {totals['ok']} / "
          f"跳过 {totals['skipped']} / 失败 {totals['failed']}"
          f"（{report['size']}，{report['duration_ms'] / 1000.0:.1f}s）")
    for row in report["panels"]:
        label = {"ok": "OK  ", "skipped": "SKIP", "failed": "FAIL"}[row["status"]]
        head = (f"  {label} {row['id']:<14} {row['name']:<12} "
                f"{(row['elapsed_ms'] or 0):>7.1f}ms")
        if row["status"] == "ok":
            head += (f"  {row['screenshot']} "
                     f"({_human_size(row['screenshot_bytes'] or 0)}, "
                     f"{row['screenshot_size'][0]}x{row['screenshot_size'][1]})")
        elif row["status"] == "skipped":
            head += f"  {row['reason']}"
        else:
            head += f"  {row['error']}"
        print(head)
        if row["status"] == "failed" and row["stack_frames"]:
            print(f"         首个栈帧：{row['stack_frames'][0]}")
    print(f"[gui-smoke] 报告：{report_path}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="离屏构造 panel_registry 中的每个面板并截图（只做证据采集）")
    ap.add_argument("--out", default=_DEFAULT_OUT,
                    help=f"截图与 report.json 的输出目录（默认 {_DEFAULT_OUT}）")
    ap.add_argument("--only", default=None,
                    help="只走查一个面板（匹配 id 或中文名称）")
    ap.add_argument("--size", default="1280x800", metavar="WxH",
                    help="每个面板的构造尺寸（默认 1280x800）")
    ap.add_argument("--no-theme", action="store_true",
                    help="对照组：不套用应用主题（截图是平台默认调色板下的样子）")
    ap.add_argument("--settle-ms", type=int, default=800, metavar="MS",
                    help="面板构造后等待稳定态的事件循环时长（默认 800，0 表示不等）")
    args = ap.parse_args(argv)

    if args.settle_ms < 0:
        ap.error(f"--settle-ms 需为 >= 0 的整数，收到 {args.settle_ms}")

    m = _SIZE_RE.match(args.size.strip())
    if not m or int(m.group(1)) <= 0 or int(m.group(2)) <= 0:
        ap.error(f"--size 需形如 1280x800，收到 {args.size!r}")
    width, height = int(m.group(1)), int(m.group(2))

    for path in (_REPO, _DESKTOP):
        if path not in sys.path:
            sys.path.insert(0, path)

    out_dir = _resolve_out(args.out)
    _clean_old_pngs(out_dir)

    tmp_root = _isolate_environment()
    started = time.perf_counter()
    report: Dict[str, object] = {
        "size": f"{width}x{height}",
        "out_dir": out_dir,
        "env": {"QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM"),
                "QT_QPA_FONTDIR": os.environ.get("QT_QPA_FONTDIR"),
                "MBDSDR_NO_HW": os.environ.get("MBDSDR_NO_HW"),
                "MBDSDR_OFFLINE": os.environ.get("MBDSDR_OFFLINE")},
        "totals": {"total": 0, "ok": 0, "skipped": 0, "failed": 0},
        "settle_ms": args.settle_ms,
        "theme_applied": False,
        "theme_entry": None,
        "theme_name": None,
        "panels": [],
    }

    app = None
    rc = 0
    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([sys.argv[0]])
        app.setQuitOnLastWindowClosed(False)
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope,
                          os.path.join(tmp_root, "qsettings"))

        # 主题必须在任何面板构造之前套上（与应用启动同序：app → theme →
        # 建窗/建面板）。--no-theme 是对照组，故意只拿平台默认调色板。
        if args.no_theme:
            report["theme_entry"] = None
            report["theme"] = None
        else:
            applied, entry, theme_name, detail = _apply_app_theme(app)
            report["theme_applied"] = applied
            report["theme_entry"] = entry
            report["theme_name"] = theme_name
            report["theme"] = theme_name if applied else f"unavailable: {detail}"
            if detail:
                report["theme_detail"] = detail
        theme_name = report["theme_name"]
        app.processEvents()

        from panel_registry import registry

        specs, err = _pick_specs(registry(), args.only)
        if err:
            print(f"[gui-smoke] {err}", file=sys.stderr)
            return 2

        report["totals"]["total"] = len(specs)
        for spec in specs:
            row = _run_one(spec, app, out_dir, width, height, theme_name,
                           settle_ms=args.settle_ms)
            report["panels"].append(row)
            report["totals"][row["status"]] += 1

        report["duration_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
        report_path = _write_report(out_dir, report)
        _print_summary(report, report_path)
        if report["totals"]["failed"]:
            rc = 1
    except Exception as exc:  # noqa: BLE001 - 环境级失败也要落报告
        report["fatal"] = f"{type(exc).__name__}: {exc}"
        report["fatal_frames"] = _frames(exc)
        report["duration_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
        report_path = _write_report(out_dir, report)
        print(f"[gui-smoke] 环境级失败：{report['fatal']}", file=sys.stderr)
        if report["fatal_frames"]:
            print(f"  首个栈帧：{report['fatal_frames'][0]}", file=sys.stderr)
        print(f"[gui-smoke] 报告：{report_path}", file=sys.stderr)
        rc = 1
    finally:
        if app is not None:
            try:
                app.processEvents()
                app.quit()
            except RuntimeError:
                pass
        shutil.rmtree(tmp_root, ignore_errors=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
