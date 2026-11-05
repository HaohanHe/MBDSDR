# -*- coding: utf-8 -*-
"""MBDSDR 性能账「后半」探针（可复跑，只测不改）。

四个独立小节，互不依赖：

* ``startup``  —— 带时间戳的启动分阶段（Qt 构造 / 字体 / 星表加载 / 后端枚举）
* ``replay``   —— 回放读盘时 GUI 线程心跳（同步 vs 后台线程 vs 24M 上限）
* ``spectrum`` —— 2.4 MS/s、1024 点 FFT 下的实际帧率 / 单帧 / 热点 / numpy 对比
* ``tle``      —— TLE 联网真实耗时、黑洞最坏耗时、当前超时配置

所有数字都是本机实测（``time.perf_counter`` / ``GetProcessMemoryInfo``），
脚本不写任何"估算值"进结果；估算只出现在给读者的公式说明里。

用法::

    python scripts/perf_probe_deep.py --section startup --runs 3
    python scripts/perf_probe_deep.py --section replay
    python scripts/perf_probe_deep.py --section spectrum
    python scripts/perf_probe_deep.py --section tle
    python scripts/perf_probe_deep.py --section all --json docs/perf-evidence/deep_<ts>.json

环境：Windows / ``QT_QPA_PLATFORM=offscreen``。启动段与回放段都在独立子进程里
跑，避免 import 缓存和峰值内存互相污染。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import statistics
import subprocess
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desktop"))
PY = sys.executable
TMP = os.path.join(os.path.dirname(ROOT), ".tmp")


def _md_table(rows, headers):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


def _stat(samples):
    if not samples:
        return {}
    s = sorted(samples)
    return {
        "n": len(s),
        "median_ms": round(statistics.median(s), 2),
        "min_ms": round(s[0], 2),
        "p95_ms": round(s[max(0, int(len(s) * 0.95) - 1)], 2),
        "max_ms": round(s[-1], 2),
    }


def _cpu_name():
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Processor).Name"],
            text=True, timeout=10, stderr=subprocess.DEVNULL)
        return out.strip()
    except Exception:
        return platform.processor() or "unknown"


def _run(cmd, env=None, timeout=600):
    p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True,
                       text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-800:])
    return p


def _child_env(**extra):
    env = dict(os.environ, MBDSDR_ROOT=ROOT, PYTHONPATH=ROOT + os.pathsep +
               os.path.join(ROOT, "desktop"), QT_QPA_PLATFORM="offscreen")
    env.update({k: str(v) for k, v in extra.items()})
    return env


# =========================================================================== #
# 1) 启动分阶段
# =========================================================================== #
_STARTUP_CHILD = r"""
import json, os, statistics, sys, time
ROOT = os.environ["MBDSDR_ROOT"]
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "desktop"))
marks = {}
box = {"star_ms": 0.0, "star_calls": 0, "star_bytes": 0,
       "enum_ms": 0.0, "enum_devices": 0, "connect_ms": 0.0,
       "listdev_ms": 0.0, "listdev_calls": 0,
       "ef_ms": 0.0, "ef_calls": 0,
       "kernel_ms": 0.0, "mode_shell_ms": 0.0,
       "settings_init_ms": 0.0, "config_load_ms": 0.0}
t_all = time.perf_counter()
def mark(n):
    marks[n] = round((time.perf_counter() - t_all) * 1000.0, 1)

mark("process_start")
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtCore import QEvent, QTimer
mark("qt_import")
from themes import get_theme, DEFAULT_THEME, get_app_font
from main_window import MainWindow
mark("main_window_import")
from PySide6.QtGui import QFontDatabase
QUICK = os.environ.get("STARTUP_QUICK") == "1"
STUB = os.environ.get("STARTUP_STUB_EF") == "1"
EF_PROFILE = os.environ.get("STARTUP_EF_PROFILE") == "1"
OLD_EF = os.environ.get("STARTUP_OLD_EF") == "1"
SYNC_ENUM = os.environ.get("STARTUP_SYNC_ENUM") == "1"
ef_types = {}

app = QApplication.instance() or QApplication([])
mark("qt_app_create")

t = time.perf_counter(); fams = QFontDatabase.families()
marks["font_families"] = round((time.perf_counter() - t) * 1000.0, 1)
t = time.perf_counter(); app.setFont(get_app_font())
marks["font_set"] = round((time.perf_counter() - t) * 1000.0, 1)
t = time.perf_counter(); get_theme(DEFAULT_THEME).apply(app)
marks["theme_apply"] = round((time.perf_counter() - t) * 1000.0, 1)

# 星表：首次 get_catalog() → load_catalog()（npz 解压 + SHA 校验）
import mbdsdr_ai.skyengine.star_catalog as scat
try:
    scat.get_catalog.cache_clear()
except Exception:
    pass
_orig_load = scat.load_catalog
def _load(*a, **k):
    t = time.perf_counter()
    r = _orig_load(*a, **k)
    box["star_ms"] += (time.perf_counter() - t) * 1000.0
    box["star_calls"] += 1
    return r
scat.load_catalog = _load
try:
    box["star_bytes"] = os.path.getsize(scat._DEFAULT_CATALOG_PATH)
except Exception:
    pass

# 后端：启动 200ms 后 QTimer.singleShot 枚举 + 连接；SettingsPanel 构造期也可能同步枚举
import mbdsdr_ai.sdr_backend as sb
_orig_enum = sb.enumerate_all_sdr_devices
_orig_build = sb.build_backend_for_device
def _enum(*a, **k):
    t = time.perf_counter()
    r = _orig_enum(*a, **k)
    box["enum_ms"] += (time.perf_counter() - t) * 1000.0
    try:
        box["enum_devices"] = len(r)
    except Exception:
        pass
    return r
def _build(*a, **k):
    t = time.perf_counter()
    r = _orig_build(*a, **k)
    box["connect_ms"] += (time.perf_counter() - t) * 1000.0
    return r
sb.enumerate_all_sdr_devices = _enum
sb.build_backend_for_device = _build

# 设置面板构造 + 配置读取（都是 GUI 线程同步）
import settings_panel as sp
_orig_sp_init = sp.SettingsPanel.__init__
def _sp_init(self, *a, **k):
    t = time.perf_counter()
    r = _orig_sp_init(self, *a, **k)
    box["settings_init_ms"] += (time.perf_counter() - t) * 1000.0
    return r
sp.SettingsPanel.__init__ = _sp_init
_orig_cfg = sp._load_json_dict
def _cfg(*a, **k):
    t = time.perf_counter()
    r = _orig_cfg(*a, **k)
    box["config_load_ms"] += (time.perf_counter() - t) * 1000.0
    return r
sp._load_json_dict = _cfg

# 主窗口内的两个大阶段 + eventFilter（构造期 Qt 事件风暴的入口）
_orig_kernel = MainWindow._build_kernel_panels
_orig_shell = MainWindow._build_mode_shell
_orig_ef = MainWindow.eventFilter
def _kernel(self, *a, **k):
    t = time.perf_counter()
    r = _orig_kernel(self, *a, **k)
    box["kernel_ms"] += (time.perf_counter() - t) * 1000.0
    return r
def _shell(self, *a, **k):
    t = time.perf_counter()
    r = _orig_shell(self, *a, **k)
    box["mode_shell_ms"] += (time.perf_counter() - t) * 1000.0
    return r
def _old_ef(self, obj, ev):
    # 改动前（HEAD 2361-2381）的原样实现，用于改前/改后 A/B。
    if obj is getattr(self, "_ai_cmd_parent", None) \
            and ev.type() == QEvent.Resize:
        self._position_ai_cmd_bar()
    if obj is getattr(self, "_planetarium_sky_slot", None) \
            and ev.type() == QEvent.Resize:
        self._position_planetarium_ai_card()
    if obj is getattr(self, "sky_view", None) \
            and ev.type() == QEvent.Resize:
        self._position_all_sky_btn()
        self._position_planetarium_overlay()
    if obj is getattr(self, "_planetarium_strip", None):
        if ev.type() == QEvent.Enter:
            self._set_planetarium_strip_hover(True)
        elif ev.type() == QEvent.Leave:
            self._set_planetarium_strip_hover(False)
    if ev.type() == QEvent.ShortcutOverride:
        if self._handle_shortcut_override(ev):
            return True
    return QMainWindow.eventFilter(self, obj, ev)

_impl_ef = _old_ef if OLD_EF else _orig_ef

def _ef(self, obj, ev):
    box["ef_calls"] += 1
    t = time.perf_counter()
    r = _impl_ef(self, obj, ev)
    dt = (time.perf_counter() - t) * 1000.0
    box["ef_ms"] += dt
    if EF_PROFILE:
        try:
            name = QEvent.Type(ev.type()).name
        except Exception:
            name = str(int(ev.type()))
        rec = ef_types.get(name)
        if rec is None:
            rec = ef_types[name] = [0, 0.0]
        rec[0] += 1
        rec[1] += dt
    return r

MainWindow._build_kernel_panels = _kernel
MainWindow._build_mode_shell = _shell
if SYNC_ENUM:
    # A/B 用：把启动枚举改回 GUI 线程内联，等价于改动前的阻塞行为。
    MainWindow._auto_enumerate_and_connect_async = \
        lambda self: self._auto_enum_worker()
if STUB:
    # 仅用于 A/B：把 eventFilter 换成只调 C++ 基类，测它本身吃掉多少构造时间
    def _stub_ef(self, obj, ev):
        return QMainWindow.eventFilter(self, obj, ev)
    MainWindow.eventFilter = _stub_ef
else:
    MainWindow.eventFilter = _ef

# 关键回调加绝对时间戳：定位首帧后那 2 s 到底花在哪个回调上（线程内也记）。
def _wrap(label, name):
    orig = getattr(MainWindow, name)
    def ww(self, *a, **k):
        marks[label + "_start"] = round((time.perf_counter() - t_all) * 1000.0, 1)
        r = orig(self, *a, **k)
        marks[label + "_end"] = round((time.perf_counter() - t_all) * 1000.0, 1)
        return r
    setattr(MainWindow, name, ww)
for _label, _name in (("async_enter", "_auto_enumerate_and_connect_async"),
                      ("enum_worker", "_auto_enum_worker"),
                      ("auto_ready", "_on_auto_enum_ready"),
                      ("auto_select", "_auto_select_and_connect"),
                      ("connect_backend", "_connect_backend"),
                      ("start_streams", "_start_iq_streams"),
                      ("backfill", "_backfill_real_rf_readings"),
                      ("sky_sat", "_update_sky_satellites")):
    _wrap(_label, _name)

# _start_iq_streams 内部拆账（只测不改）：声卡打开 / IQ 读流初始化 / 流水线组装
_orig_ars = MainWindow._active_read_samples
def _ars(self, *a, **k):
    t = time.perf_counter(); r = _orig_ars(self, *a, **k)
    box["active_read_ms"] = box.get("active_read_ms", 0.0) \
        + (time.perf_counter() - t) * 1000.0
    return r
MainWindow._active_read_samples = _ars
_orig_bp = MainWindow._build_pipeline
def _bp(self, *a, **k):
    t = time.perf_counter()
    try:
        return _orig_bp(self, *a, **k)
    finally:
        box["build_pipeline_ms"] = box.get("build_pipeline_ms", 0.0) \
            + (time.perf_counter() - t) * 1000.0
MainWindow._build_pipeline = _bp

# _build_pipeline 内部子调用拆账：真机下 build 966 ms，stub 下只 1.5 ms，
# 到底落在 pipe 构造 / pipe.start / VFO offset 哪一步（全部只测不改）。
def _add_ms(key, t):
    box[key] = box.get(key, 0.0) + (time.perf_counter() - t) * 1000.0

import receive_pipeline as _rp
_rp_ctor = _rp.ReceivePipeline.__init__
def _rp_init(self, *a, **k):
    t = time.perf_counter(); _rp_ctor(self, *a, **k); _add_ms("pipe_ctor_ms", t)
_rp.ReceivePipeline.__init__ = _rp_init
_rp_start = _rp.ReceivePipeline.start
def _rp_st(self):
    t = time.perf_counter()
    try:
        return _rp_start(self)
    finally:
        _add_ms("pipe_start_ms", t)
_rp.ReceivePipeline.start = _rp_st

import mbdsdr_ai.dsp as _dsp
_dsp_vfo_ctor = _dsp.VFO.__init__
def _dsp_vfo(self, *a, **k):
    t = time.perf_counter(); _dsp_vfo_ctor(self, *a, **k); _add_ms("vfo_ctor_ms", t)
_dsp.VFO.__init__ = _dsp_vfo

import mbdsdr_ai.vfo_manager as _vm
for _vn in ("add", "bind_dsp", "set_active_context"):
    _vo = getattr(_vm.VfoManager, _vn)
    def _vmk(n, o):
        def _vw(self, *a, **k):
            t = time.perf_counter()
            try:
                return o(self, *a, **k)
            finally:
                _add_ms("vfo_" + n + "_ms", t)
        return _vw
    setattr(_vm.VfoManager, _vn, _vmk(_vn, _vo))

_upd_orig = MainWindow._update_main_vfo_offset
def _upd(self, *a, **k):
    t = time.perf_counter()
    try:
        return _upd_orig(self, *a, **k)
    finally:
        _add_ms("vfo_offset_ms", t)
MainWindow._update_main_vfo_offset = _upd

import mbdsdr_ai.audio_out as _ao
_oa_start = _ao.AudioPlayer.start
def _audio_start(self):
    t = time.perf_counter()
    try:
        return _oa_start(self)
    finally:
        box["audio_start_ms"] = box.get("audio_start_ms", 0.0) \
            + (time.perf_counter() - t) * 1000.0
_ao.AudioPlayer.start = _audio_start
# 后端构造 vs 硬件 connect 的拆分（build 只 new 对象，connect 才碰 USB）
import mbdsdr_ai.sdr_backend as _sb
_build_orig = _sb.build_backend_for_device
def _build_marked(dev):
    marks["build_backend_start"] = round((time.perf_counter() - t_all) * 1000.0, 1)
    b = _build_orig(dev)
    marks["build_backend_end"] = round((time.perf_counter() - t_all) * 1000.0, 1)
    for _n in ("connect", "set_sample_rate", "set_bandwidth", "set_frequency",
               "set_ppm", "set_offset_tuning", "set_agc", "set_gain",
               "start_stream", "stop_stream", "disconnect", "get_frequency"):
        _o = getattr(b, _n, None)
        if callable(_o):
            def _mk(n, o):
                def _w(*a, **k):
                    _t = time.perf_counter()
                    r = o(*a, **k)
                    _key = "backend_" + n + "_ms"
                    box[_key] = box.get(_key, 0.0) + (time.perf_counter() - _t) * 1000.0
                    return r
                return _w
            setattr(b, _n, _mk(_n, _o))
    return b
_sb.build_backend_for_device = _build_marked

# 「预热度」A/B：真实下一刀的最小候选是启动早期后台 import scipy.signal。
# 这里在构造前显式导入，等价于「预热线程已先完成」，用来量
# _start_iq_streams 里还剩多少真实工作（不含首次导入的 1 s）。
PREWARM = os.environ.get("STARTUP_PREWARM_SCIPY") == "1"
if PREWARM:
    _tp = time.perf_counter()
    import scipy.signal  # noqa: F401
    marks["scipy_import_ms"] = round((time.perf_counter() - _tp) * 1000.0, 1)

# 真实产品入口（desktop/main.py）在 QApplication 之前会跑
# `_run_import_smoke()` + `_startup_dependency_problems()`，两者都 import
# scipy.signal。这个开关复刻那条路径，用来区分「产品里是否本来就不慢」。
SELFCHECK = os.environ.get("STARTUP_MAIN_SELFCHECK") == "1"
if SELFCHECK:
    _ts = time.perf_counter()
    import main as _main_mod
    _main_mod._startup_dependency_problems()
    marks["main_selfcheck_ms"] = round((time.perf_counter() - _ts) * 1000.0, 1)

t = time.perf_counter(); w = MainWindow()
marks["main_window_init"] = round((time.perf_counter() - t) * 1000.0, 1)
mark("main_window_init_end")
if QUICK:
    print("PHASES_JSON " + json.dumps(marks))
    raise SystemExit(0)
# GUI 心跳从构造结束就开始：200ms 单发定时器可能在 processEvents 里就触发，
# 只在 grab 之后起表会漏掉那一段。存 (绝对时刻, 间隔) 便于按阶段切片。
hb = []
hb_last = [time.perf_counter()]
def _hb():
    now = time.perf_counter()
    hb.append((round((now - t_all) * 1000.0, 1), (now - hb_last[0]) * 1000.0))
    hb_last[0] = now
hb_timer = QTimer(); hb_timer.setInterval(10)
hb_timer.timeout.connect(_hb); hb_timer.start()

w.show(); app.processEvents()
mark("show_process_events")
t = time.perf_counter(); w.grab()
marks["grab_first_frame"] = round((time.perf_counter() - t) * 1000.0, 1)
mark("grab_end")
box["ef_calls_at_show"] = box["ef_calls"]
box["ef_ms_at_show"] = box["ef_ms"]

QTimer.singleShot(3500, app.quit)
app.exec()
hb_timer.stop()
mark("deferred_end")
if hb:
    s = sorted(g for _, g in hb)
    marks["hb_n"] = len(s)
    marks["hb_p50_ms"] = round(statistics.median(s), 2)
    marks["hb_p95_ms"] = round(s[max(0, int(len(s) * 0.95) - 1)], 2)
    marks["hb_max_ms"] = round(s[-1], 2)
    marks["hb_max_at_ms"] = max(hb, key=lambda x: x[1])[0]
    post = [g for r, g in hb if r >= marks["grab_end"]]
    if post:
        marks["hb_max_after_first_frame_ms"] = round(max(post), 2)
        marks["hb_p95_after_first_frame_ms"] = round(
            sorted(post)[max(0, int(len(post) * 0.95) - 1)], 2)
marks["ef_after_show_calls"] = box["ef_calls"] - box["ef_calls_at_show"]
marks["ef_after_show_ms"] = round(box["ef_ms"] - box["ef_ms_at_show"], 1)
if EF_PROFILE:
    marks["ef_by_type"] = {
        k: [v[0], round(v[1], 1)]
        for k, v in sorted(ef_types.items(), key=lambda kv: -kv[1][1])[:12]
    }
marks["star_load_ms"] = round(box["star_ms"], 1)
marks["star_load_calls"] = box["star_calls"]
marks["star_catalog_mb"] = round(box["star_bytes"] / 1048576.0, 1)
marks["backend_enum_ms"] = round(box["enum_ms"], 1)
marks["backend_enum_devices"] = box["enum_devices"]
marks["backend_connect_ms"] = round(box["connect_ms"], 1)
for _k, _v in box.items():
    if _k.startswith("backend_"):
        marks[_k] = round(_v, 1)
marks["eventfilter_ms"] = round(box["ef_ms"], 1)
marks["eventfilter_calls"] = box["ef_calls"]
for _k in ("audio_start_ms", "active_read_ms", "build_pipeline_ms",
           "pipe_ctor_ms", "pipe_start_ms", "vfo_ctor_ms",
           "vfo_add_ms", "vfo_bind_dsp_ms", "vfo_set_active_context_ms",
           "vfo_offset_ms"):
    marks[_k] = round(box.get(_k, 0.0), 1)
marks["kernel_panels_ms"] = round(box["kernel_ms"], 1)
marks["mode_shell_ms"] = round(box["mode_shell_ms"], 1)
marks["settings_panel_init_ms"] = round(box["settings_init_ms"], 1)
marks["config_load_ms"] = round(box["config_load_ms"], 1)
print("PHASES_JSON " + json.dumps(marks))
"""


def section_startup(runs=3):
    probe = os.path.join(TMP, "perf_probe_deep_startup.py")
    os.makedirs(TMP, exist_ok=True)
    with open(probe, "w", encoding="utf-8") as f:
        f.write(_STARTUP_CHILD)
    def _one(**extra):
        p = _run([PY, probe], env=_child_env(**extra), timeout=400)
        for ln in p.stdout.splitlines():
            if ln.startswith("PHASES_JSON "):
                return json.loads(ln[len("PHASES_JSON "):])
        raise RuntimeError("startup child 没有输出 PHASES_JSON")

    try:
        each = [_one() for _ in range(max(1, runs))]
        # eventFilter 同窗口 A/B：交替跑（同一时刻负载对两边等价），只测构造
        ab_normal, ab_stub = [], []
        for _ in range(max(1, runs)):
            ab_normal.append(_one(STARTUP_QUICK=1)["main_window_init"])
            ab_stub.append(_one(STARTUP_QUICK=1, STARTUP_STUB_EF=1)["main_window_init"])
        # eventFilter 改前/改后 A/B（旧实现由探针按 HEAD 原样重建）
        ef_old, ef_new = [], []
        for _ in range(max(1, runs)):
            ef_old.append(_one(STARTUP_QUICK=1, STARTUP_OLD_EF=1))
            ef_new.append(_one(STARTUP_QUICK=1))
        # 启动枚举：GUI 内联（改前）vs 后台线程（改后），看首帧后心跳最大间隔
        en_sync, en_async = [], []
        for _ in range(max(1, runs)):
            en_sync.append(_one(STARTUP_SYNC_ENUM=1))
            en_async.append(_one())
        # 同口径分型：旧/新实现交替跑（同一会话、同一负载窗口），各 runs 次，
        # 每类取中位。否则「改后分型表」与「改前/改后 A/B」不是一个口径。
        prof_runs = {"old": [], "new": []}
        for _ in range(max(1, runs)):
            prof_runs["old"].append(_one(STARTUP_EF_PROFILE=1, STARTUP_OLD_EF=1))
            prof_runs["new"].append(_one(STARTUP_EF_PROFILE=1))
        # scipy.signal 预热 A/B：交替跑（未预热 / 预热），看
        # _start_iq_streams 里的 1 s 是否全部来自首次 import。
        pre_off, pre_on = [], []
        for _ in range(max(1, runs)):
            pre_off.append(_one())
            pre_on.append(_one(STARTUP_PREWARM_SCIPY=1))
        pre_main = [_one(STARTUP_MAIN_SELFCHECK=1)]

        def _merge_prof(rs):
            types = set()
            for r in rs:
                types |= set(r.get("ef_by_type", {}))
            merged = {}
            for t in types:
                calls = [r.get("ef_by_type", {}).get(t, [0, 0.0])[0] for r in rs]
                mss = [r.get("ef_by_type", {}).get(t, [0, 0.0])[1] for r in rs]
                merged[t] = [int(statistics.median(calls)),
                             round(statistics.median(mss), 1)]
            return {
                "by_type": merged,
                "calls": int(statistics.median(
                    [r.get("eventfilter_calls", 0) for r in rs])),
                "total_ms": round(statistics.median(
                    [r.get("eventfilter_ms", 0.0) for r in rs]), 1),
            }

        ef_profile_old = _merge_prof(prof_runs["old"])
        ef_profile_new = _merge_prof(prof_runs["new"])
        if not each:
            raise RuntimeError("startup child 没有输出 PHASES_JSON")
        keys = [k for k in each[0] if isinstance(each[0][k], (int, float))]
        med = {k: round(statistics.median([r[k] for r in each if k in r]), 1)
               for k in keys}
        # 把累计 mark 换算成单段时长（marks 都是「进程起点 → 该点」的累计毫秒）
        for a, b, name in (
            ("process_start", "qt_import", "dur_qt_import"),
            ("qt_import", "main_window_import", "dur_main_window_import"),
            ("main_window_import", "qt_app_create", "dur_qt_app_create"),
            ("qt_app_create", "main_window_init_end", "dur_main_window_init"),
            ("main_window_init_end", "show_process_events", "dur_show_first_paint"),
        ):
            med[name] = round(med.get(b, 0.0) - med.get(a, 0.0), 1)
        med["dur_first_frame"] = round(
            med.get("show_process_events", 0.0) + med.get("grab_first_frame", 0.0), 1)
        # 前三名取「可归因的叶子块」，不含 main_window_init 这个容器
        stage_keys = ["eventfilter_ms", "backend_enum_ms", "kernel_panels_ms",
                      "mode_shell_ms", "settings_panel_init_ms",
                      "dur_main_window_import", "dur_show_first_paint",
                      "star_load_ms", "font_families", "font_set", "theme_apply"]
        top3 = sorted(((k, med.get(k, 0.0)) for k in stage_keys),
                      key=lambda kv: -kv[1])[:3]
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass
    def _span(r, a, b):
        if r.get(a) is None or r.get(b) is None:
            return None
        return round(r[b] - r[a], 1)

    def _enum_detail(r):
        return {
            "hb_max_ms": r.get("hb_max_ms"),
            "hb_max_at_ms": r.get("hb_max_at_ms"),
            "hb_p95_ms": r.get("hb_p95_ms"),
            "enum_ms_total": r.get("backend_enum_ms"),
            "enum_worker_ms": _span(r, "enum_worker_start", "enum_worker_end"),
            "connect_backend_ms": _span(r, "connect_backend_start", "connect_backend_end"),
            "start_streams_ms": _span(r, "start_streams_start", "start_streams_end"),
            "auto_ready_ms": _span(r, "auto_ready_start", "auto_ready_end"),
            "audio_start_ms": r.get("audio_start_ms"),
            "active_read_ms": r.get("active_read_ms"),
            "build_pipeline_ms": r.get("build_pipeline_ms"),
            "pipe_ctor_ms": r.get("pipe_ctor_ms"),
            "pipe_start_ms": r.get("pipe_start_ms"),
            "vfo_ctor_ms": r.get("vfo_ctor_ms"),
            "vfo_add_ms": r.get("vfo_add_ms"),
            "vfo_bind_dsp_ms": r.get("vfo_bind_dsp_ms"),
            "vfo_set_active_context_ms": r.get("vfo_set_active_context_ms"),
            "vfo_offset_ms": r.get("vfo_offset_ms"),
            "scipy_import_ms": r.get("scipy_import_ms"),
            "main_selfcheck_ms": r.get("main_selfcheck_ms"),
            "backend_connect_ms": r.get("backend_connect_ms"),
            "deferred_end": r.get("deferred_end"),
        }

    def _ab(old, new, key):
        mo = statistics.median([r[key] for r in old])
        mn = statistics.median([r[key] for r in new])
        return {"old_ms": old, "new_ms": new,
                "median_old_ms": round(mo, 1), "median_new_ms": round(mn, 1),
                "delta_ms": round(mn - mo, 1),
                "delta_pct": round((mn - mo) / mo * 100.0, 1) if mo else None}

    pre_off_d = [_enum_detail(r) for r in pre_off]
    pre_on_d = [_enum_detail(r) for r in pre_on]

    return {"runs": each, "median": med, "top3": top3,
            "ef_profile_old": ef_profile_old,
            "ef_profile_new": ef_profile_new,
            "eventfilter_ab": {
                "normal_init_ms": ab_normal,
                "stubbed_init_ms": ab_stub,
                "median_normal_ms": round(statistics.median(ab_normal), 1),
                "median_stubbed_ms": round(statistics.median(ab_stub), 1),
                "delta_ms": round(statistics.median(ab_normal)
                                   - statistics.median(ab_stub), 1),
            },
            "ef_opt_ab": _ab(ef_old, ef_new, "main_window_init"),
            "enum_bg_ab_hb_max": _ab(en_sync, en_async, "hb_max_ms"),
            "enum_bg_ab_enum_ms": _ab(en_sync, en_async, "backend_enum_ms"),
            "enum_bg_ab_detail": {
                "sync": [_enum_detail(r) for r in en_sync],
                "async": [_enum_detail(r) for r in en_async],
            },
            "scipy_prewarm_ab": {
                "off": pre_off_d,
                "on": pre_on_d,
                "start_streams": _ab(pre_off_d, pre_on_d, "start_streams_ms"),
                "build_pipeline": _ab(pre_off_d, pre_on_d, "build_pipeline_ms"),
                "scipy_import_ms": [
                    r.get("scipy_import_ms") for r in pre_on],
            },
            "main_selfcheck": _enum_detail(pre_main[0]),
            "stderr_note": "子进程 QT_QPA_PLATFORM=offscreen；"
                           "MBDSDR_NULL_AUDIO=1；无 MBDSDR_NO_HW"}


# =========================================================================== #
# 2) 回放读盘：GUI 线程心跳（只测）
# =========================================================================== #
_REPLAY_CHILD = r"""
import json, os, statistics, sys, threading, time
ROOT = os.environ["MBDSDR_ROOT"]
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "desktop"))
PATH = os.environ["REPLAY_FILE"]
MODE = os.environ["REPLAY_MODE"]
MAXS = int(os.environ["REPLAY_MAXSAMPLES"])
app = None
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, QTimer, Signal
app = QApplication.instance() or QApplication([])
from mbdsdr_ai import baseband_io

gaps = []
last = [time.perf_counter()]
def tick():
    now = time.perf_counter()
    gaps.append((now - last[0]) * 1000.0)
    last[0] = now

tm = QTimer(); tm.setInterval(10); tm.timeout.connect(tick); tm.start()
QTimer.singleShot(100, lambda: tick())   # 先打一个基准 tick

class Bridge(QObject):
    progress = Signal(int)
    finished = Signal(object)

bridge = Bridge()
state = {"pct": 0, "events": 0, "rec": None}
def on_progress(pct):
    state["pct"] = pct
    state["events"] += 1
def on_finished(rec):
    state["rec"] = rec
bridge.progress.connect(on_progress)
bridge.finished.connect(on_finished)

def once():
    kw = dict(chunk_samples=1 << 20,
              progress=lambda d, t: bridge.progress.emit(int(100 * d / max(1, t))))
    if MAXS > 0:
        kw["max_samples"] = MAXS
    return baseband_io.load_iq(PATH, **kw)

t0 = time.perf_counter()
if MODE == "sync":
    gaps.clear(); tick()          # 同步起点打一次基准，首个大间隔=读盘占用
    def do():
        try:
            state["rec"] = once()
        finally:
            for _ in range(6):
                app.processEvents()
            tm.stop()
            QTimer.singleShot(0, app.quit)
    QTimer.singleShot(0, do)
    app.exec()
    g = gaps                    # 保留首个大间隔（就是被测的 GUI 占用）
else:
    def work():
        try:
            rec = once()
        except Exception as e:
            rec = {"error": f"{type(e).__name__}: {e}"}
        try:
            bridge.finished.emit(rec)
        except Exception:
            pass
    threading.Thread(target=work, daemon=True).start()
    def poll():
        if state["rec"] is not None or time.perf_counter() - t0 > 60:
            for _ in range(4):
                app.processEvents()
            tm.stop()
            app.quit()
        else:
            QTimer.singleShot(20, poll)
    QTimer.singleShot(20, poll)
    app.exec()
    g = gaps[1:] if gaps else []

wall = time.perf_counter() - t0
out = {
    "mode": MODE,
    "max_samples": MAXS,
    "file_mb": round(os.path.getsize(PATH) / 1048576.0, 1),
    "wall_s": round(wall, 3),
    "ticks": len(g),
    "gap_p50_ms": round(statistics.median(g), 2) if g else None,
    "gap_p95_ms": round(sorted(g)[max(0, int(len(g) * 0.95) - 1)], 2) if g else None,
    "gap_max_ms": round(max(g), 2) if g else None,
    "progress_events": state["events"],
}
rec = state["rec"] or {}
for k in ("samples", "file_samples", "duration_s", "truncated", "cancelled",
          "error", "sample_rate", "center_freq_hz"):
    if k in rec:
        out[k] = rec[k]

try:
    import psutil
    _mi = psutil.Process().memory_info()
    out["ws_mb"] = round(_mi.rss / 1048576.0, 1)
    out["peak_ws_mb"] = round(getattr(_mi, "peak_wset", _mi.rss) / 1048576.0, 1)
except Exception:
    pass
print("REPLAY_JSON " + json.dumps(out, ensure_ascii=False))
"""


def _ensure_replay_file(path: str, target_mb: int = 1100):
    target = target_mb * 1048576
    if os.path.isfile(path) and os.path.getsize(path) == target:
        return False
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    chunk = bytes(16 * 1048576)
    written = 0
    with open(path, "wb") as f:
        while written < target:
            n = min(len(chunk), target - written)
            f.write(chunk[:n])
            written += n
        f.flush()
        os.fsync(f.fileno())
    return True


def section_replay(path: str, reuse: bool):
    probe = os.path.join(TMP, "perf_probe_deep_replay.py")
    os.makedirs(TMP, exist_ok=True)
    with open(probe, "w", encoding="utf-8") as f:
        f.write(_REPLAY_CHILD)
    if reuse and os.path.isfile(path) and os.path.getsize(path) == 1100 * 1048576:
        created = False
    else:
        created = _ensure_replay_file(path)
    cases = [("sync", 0), ("thread", 0),
             ("sync", 24_000_000), ("thread", 24_000_000)]
    results = []
    try:
        for mode, maxs in cases:
            env = _child_env(REPLAY_FILE=path, REPLAY_MODE=mode,
                             REPLAY_MAXSAMPLES=maxs)
            p = _run([PY, probe], env=env, timeout=300)
            for ln in p.stdout.splitlines():
                if ln.startswith("REPLAY_JSON "):
                    results.append(json.loads(ln[len("REPLAY_JSON "):]))
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass
        if created and not reuse:
            try:
                os.remove(path)
            except OSError:
                pass
    return {"file": path, "file_created": created, "cases": results}


# =========================================================================== #
# 3) 频谱：2.4 MS/s / 1024 点实际帧率 + 热点 + numpy vs 循环
# =========================================================================== #
_SPECTRUM_CHILD = r"""
import json, os, sys, time, statistics
ROOT = os.environ["MBDSDR_ROOT"]
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "desktop"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter
from PySide6.QtCore import QPoint, QTimer, Qt
import numpy as np
import spectrum_widget as sw
from spectrum_widget import _render_spectrum

# PySide6 的虚函数在类创建时绑定；运行期给类赋 paintEvent 不会拦到 Qt 的虚调用，
# 只能在建面板前换成"在类体里定义 paintEvent"的子类。
paint_ms = []
paint_ts = []
class CountingCurve(sw.SpectrumCurveWidget):
    def paintEvent(self, ev):
        paint_ts.append(time.perf_counter())
        t = time.perf_counter()
        super().paintEvent(ev)
        paint_ms.append((time.perf_counter() - t) * 1000.0)
sw.SpectrumCurveWidget = CountingCurve

app = QApplication.instance() or QApplication([])
panel = sw.SpectrumPanel()
panel.resize(900, 500)
panel.show()
app.processEvents()

# 1024 点档：schema 合法三档之一，性能档 1 同时把谱线 bin 调到 384
panel.generator.set_fft_size(1024)
panel.set_performance_mode(1)
_ivl = os.environ.get("SPECTRUM_INTERVAL_MS")
if _ivl:
    # 对照实验：只改节拍（默认 66 ms 是性能档），其余绘制/数据不变
    panel._plot._timer.setInterval(int(_ivl))
if os.environ.get("SPECTRUM_PRECISE") == "1":
    # 对照实验：只改定时器精度，不改绘制，量化 CoarseTimer 的 5% 抖动
    panel._plot._timer.setTimerType(Qt.PreciseTimer)
gen = panel.generator
SR = 2_400_000.0
gen.sample_rate_hz = SR
gen.center_freq_hz = 100e6
rng = np.random.default_rng(11)
block = (0.02 * rng.standard_normal(120_000)
         + 0.02 * rng.standard_normal(120_000) * 1j).astype(np.complex64)

push_ms = []
orig_push = gen.push_iq
def push(iq, f, sr):
    t = time.perf_counter()
    orig_push(iq, f, sr)
    push_ms.append((time.perf_counter() - t) * 1000.0)
gen.push_iq = push

feed = QTimer()
feed.setInterval(50)          # 2.4 MS/s × 50 ms = 120k 样本/块
feed.timeout.connect(lambda: gen.push_iq(block, 100e6, SR))
feed.start()

# 帧周期拆账：定时器实际触发时刻 / 绘制时刻 / 事件循环心跳。
timer_ts = []
panel._plot._timer.timeout.connect(lambda: timer_ts.append(time.perf_counter()))
hb_gaps = []
hb_last = [time.perf_counter()]
def _hb():
    now = time.perf_counter()
    hb_gaps.append((now - hb_last[0]) * 1000.0)
    hb_last[0] = now
hb_timer = QTimer(); hb_timer.setInterval(10)
hb_timer.timeout.connect(_hb); hb_timer.start()

t0 = time.perf_counter()
cpu0 = time.process_time()
dur_ms = int(os.environ.get("SPECTRUM_DURATION_MS") or 6000)
QTimer.singleShot(dur_ms, app.quit)
app.exec()
elapsed = time.perf_counter() - t0
cpu_s = time.process_time() - cpu0
feed.stop()
hb_timer.stop()
n_paint = len(paint_ms)

def diffs_ms(ts):
    return [(b - a) * 1000.0 for a, b in zip(ts, ts[1:])]

timer_gap = diffs_ms(timer_ts)
paint_gap = diffs_ms(paint_ts)
# timeout → 该次 paint 的延迟（update 请求到真正落到 paintEvent 的排队时间）
paint_latency = []
j = 0
for p in paint_ts:
    while j + 1 < len(timer_ts) and timer_ts[j + 1] <= p:
        j += 1
    if timer_ts and timer_ts[j] <= p:
        paint_latency.append((p - timer_ts[j]) * 1000.0)
# 心跳间隔超出节拍（10ms）的部分 = GUI 线程被别的活占住的时间
hb_block_total = sum(max(0.0, g - 10.0) for g in hb_gaps)

# 长时间稳定性：按 60 s 窗口切 fps 与 paint 中位，看是否随时间退化
win = {}
if paint_ts:
    for p, d in zip(paint_ts, paint_ms):
        k = int((p - paint_ts[0]) // 60.0)
        win.setdefault(k, ([], []))
        win[k][0].append(p)
        win[k][1].append(d)
stability = []
for k in sorted(win):
    ts_k, d_k = win[k]
    dur = (ts_k[-1] - ts_k[0]) if len(ts_k) > 1 else 0.0
    stability.append({
        "window_s": k * 60,
        "paints": len(ts_k),
        "fps": round((len(ts_k) - 1) / dur, 2) if dur > 0 else None,
        "paint_median_ms": round(statistics.median(d_k), 2),
    })

def st(xs):
    if not xs:
        return {}
    s = sorted(xs)
    return {"n": len(s), "median_ms": round(statistics.median(s), 2),
            "p95_ms": round(s[max(0, int(len(s) * 0.95) - 1)], 2),
            "max_ms": round(s[-1], 2)}

# 渲染热点：同一帧连续 200 次 cProfile
W, H = panel._plot.width(), panel._plot.height()
img = QImage(W, H, QImage.Format_ARGB32)
def frame():
    img.fill(0)
    pa = QPainter(img)
    _render_spectrum(pa, panel._plot.state, W, H)
    pa.end()
for _ in range(5):
    frame()
import cProfile, pstats, io
pr = cProfile.Profile()
pr.enable()
for _ in range(200):
    frame()
pr.disable()
buf = io.StringIO()
stt = pstats.Stats(pr, stream=buf).sort_stats("tottime")
stt.print_stats(14)
prof_top = []
import re
for l in buf.getvalue().splitlines():
    m = re.match(r"\s*(\d+)\s+(\d+\.\d+)\s+\d+\.\d+\s+(\d+\.\d+)\s+\d+\.\d+\s+(.*)", l)
    if m:
        prof_top.append({"calls": int(m.group(1)),
                         "tottime_s": round(float(m.group(2)), 4),
                         "cumtime_s": round(float(m.group(3)), 4),
                         "func": m.group(4).strip()})
prof_top = prof_top[:8]
prof_text = buf.getvalue()[-2600:]

# 直接渲染单帧（不含 Qt 合成）：同一状态连续 100 帧
frame_ms = []
for _ in range(5):
    frame()
for _ in range(100):
    t = time.perf_counter()
    frame()
    frame_ms.append((time.perf_counter() - t) * 1000.0)

# numpy vs 纯 Python 循环（同一 1024 点数据，同一语义）
x = block[:1024]
win = np.hanning(1024)
spec = np.abs(np.fft.fft(x * win))
def bench(fn, n=30):
    for _ in range(3):
        fn()
    ts = []
    for _ in range(n):
        t = time.perf_counter(); fn(); ts.append((time.perf_counter() - t) * 1000.0)
    return round(statistics.median(ts), 3)
def np_db():
    return 20.0 * np.log10(spec / (1024 * 0.5) + 1e-12)
import math
def py_db():
    return [20.0 * math.log10(float(v) / (1024 * 0.5) + 1e-12) for v in spec.tolist()]
# 1024 -> 384：当前代码走 Python 逐 bin 循环（len % nb != 0）
def loop_resample():
    nb = 384
    edges = np.linspace(0, len(spec), nb + 1).astype(int)
    out = np.empty(nb, dtype=np.float32)
    for i in range(nb):
        lo, hi = edges[i], edges[i + 1]
        seg = spec[lo:hi]
        out[i] = float(seg.max()) if seg.size else np.nan
    return out
def vec_resample():
    nb = 384
    edges = np.linspace(0, len(spec), nb + 1).astype(int)
    return np.maximum.reduceat(spec, edges[:-1]).astype(np.float32)

micro = {
    "mag_db_numpy_ms": bench(np_db),
    "mag_db_python_loop_ms": bench(py_db, n=10),
    "resample_1024to384_python_loop_ms": bench(loop_resample),
    "resample_1024to384_reduceat_ms": bench(vec_resample),
}
out = {
    "config": {
        "fft_size": gen.fft_size, "num_bins": gen.num_bins,
        "sample_rate_hz": SR, "feed_block_samples": int(len(block)),
        "feed_interval_ms": 50,
        "curve_timer_ms": panel._plot._timer.interval(),
        "waterfall_timer_ms": panel._wf_plot._timer.interval(),
        "widget": [panel._plot.width(), panel._plot.height()],
    },
    "measured": {
        "elapsed_s": round(elapsed, 3),
        "paints": n_paint,
        "fps": round(n_paint / elapsed, 2),
        "cpu_percent": round(cpu_s / max(elapsed, 1e-9) * 100.0, 1),
        "stability_60s": stability,
        "paint": st(paint_ms),
        "push_iq": st(push_ms),
        "direct_render": st(frame_ms),
        "timer_type": str(panel._plot._timer.timerType()),
        "timer_gap": st(timer_gap),
        "paint_gap": st(paint_gap),
        "paint_latency_from_timeout": st(paint_latency),
        "heartbeat_gap": st(hb_gaps),
        "heartbeat_gap_gt15ms": sum(1 for g in hb_gaps if g > 15.0),
        "heartbeat_blocked_ms_total": round(hb_block_total, 1),
        "heartbeat_blocked_ms_per_frame": round(
            hb_block_total / max(1, len(timer_gap)), 2),
        "timer_gap_median_minus_interval_ms": (
            round(statistics.median(timer_gap) - panel._plot._timer.interval(), 2)
            if timer_gap else None),
    },
    "profile_top": prof_top,
    "profile_text": prof_text,
    "microbench": micro,
}
print("SPEC_JSON " + json.dumps(out, ensure_ascii=False))
"""


def section_spectrum():
    probe = os.path.join(TMP, "perf_probe_deep_spectrum.py")
    os.makedirs(TMP, exist_ok=True)
    with open(probe, "w", encoding="utf-8") as f:
        f.write(_SPECTRUM_CHILD)
    try:
        p = _run([PY, probe], env=_child_env(),
                 timeout=180 + int(os.environ.get("SPECTRUM_DURATION_MS") or 6000) // 1000)
        for ln in p.stdout.splitlines():
            if ln.startswith("SPEC_JSON "):
                return json.loads(ln[len("SPEC_JSON "):])
        raise RuntimeError("spectrum child 没有输出 SPEC_JSON")
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass


# =========================================================================== #
# 3b) 接收流水线启动拆账：_start_iq_streams 的 844–1013 ms 花在哪
# =========================================================================== #
_STREAMS_CHILD = r"""
import json, os, statistics, sys, time
ROOT = os.environ["MBDSDR_ROOT"]
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "desktop"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ["MBDSDR_OFFLINE"] = "1"      # 启动定时器不碰真实硬件
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

box = {k: 0.0 for k in ("audio_start_ms", "vfo_ctor_ms", "vfo_mgr_ms",
                        "pipe_ctor_ms", "pipe_start_ms", "build_ms",
                        "start_ms", "stop_ms")}

def add(key, t):
    box[key] += (time.perf_counter() - t) * 1000.0

app = QApplication.instance() or QApplication([])
from main_window import MainWindow

# 只测不改：在子进程里包住各构造点，定位 844–1013 ms 的组成
import mbdsdr_ai.dsp as _dsp
_ov = _dsp.VFO.__init__
def _vfo_init(self, *a, **k):
    t = time.perf_counter(); _ov(self, *a, **k); add("vfo_ctor_ms", t)
_dsp.VFO.__init__ = _vfo_init
import mbdsdr_ai.audio_out as _ao
_oa = _ao.AudioPlayer.start
def _audio_start(self):
    t = time.perf_counter()
    try:
        return _oa(self)
    finally:
        add("audio_start_ms", t)
_ao.AudioPlayer.start = _audio_start
import receive_pipeline as _rp
_op = _rp.ReceivePipeline.__init__
def _pipe_init(self, *a, **k):
    t = time.perf_counter(); _op(self, *a, **k); add("pipe_ctor_ms", t)
_rp.ReceivePipeline.__init__ = _pipe_init
_ops = _rp.ReceivePipeline.start
def _pipe_start(self):
    t = time.perf_counter()
    try:
        return _ops(self)
    finally:
        add("pipe_start_ms", t)
_rp.ReceivePipeline.start = _pipe_start
import mbdsdr_ai.vfo_manager as _vm
_oadd = _vm.VfoManager.add
def _vfo_add(self, *a, **k):
    t = time.perf_counter(); r = _oadd(self, *a, **k); add("vfo_mgr_ms", t); return r
_vm.VfoManager.add = _vfo_add

mw = MainWindow()
mw.show()
QTimer.singleShot(300, app.quit)
app.exec()

class _StubBackend:
    class _Dev:
        name = "stub-rtl"
    device = _Dev()
    def __init__(self):
        self._f = 100e6
        self._blk = (0.02 * np.random.randn(131072)
                     + 0.02j * np.random.randn(131072)).astype(np.complex64)
    def read_samples(self, n):
        return self._blk[:min(n, len(self._blk))]
    def get_frequency(self):
        return self._f

mw._active_sdr_backend = _StubBackend()
runs = []
for i in range(3):
    for k in box:
        box[k] = 0.0
    t = time.perf_counter()
    mw._start_iq_streams()
    box["start_ms"] = (time.perf_counter() - t) * 1000.0
    box["build_ms"] = box["start_ms"] - box["audio_start_ms"]
    time.sleep(0.15)                     # 让读线程真跑一拍
    t = time.perf_counter()
    mw._stop_iq_streams()
    box["stop_ms"] = (time.perf_counter() - t) * 1000.0
    runs.append({k: round(v, 1) for k, v in box.items()})
mw.close()

def med(k):
    return round(statistics.median([r[k] for r in runs]), 1)

out = {
    "runs": runs,
    "median": {k: med(k) for k in runs[0]},
    "note": "MBDSDR_NULL_AUDIO=1：audio_start 只测量打开/降级路径；"
            "真实声卡打开耗时另计（受设备/驱动影响）",
}
print("STREAMS_JSON " + json.dumps(out, ensure_ascii=False))
"""


def section_streams():
    probe = os.path.join(TMP, "perf_probe_deep_streams.py")
    os.makedirs(TMP, exist_ok=True)
    with open(probe, "w", encoding="utf-8") as f:
        f.write(_STREAMS_CHILD)
    try:
        p = _run([PY, probe], env=_child_env(), timeout=300)
        for ln in p.stdout.splitlines():
            if ln.startswith("STREAMS_JSON "):
                return json.loads(ln[len("STREAMS_JSON "):])
        raise RuntimeError("streams child 没有输出 STREAMS_JSON")
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass


# =========================================================================== #
# 4) TLE 联网
# =========================================================================== #
_TLE_CHILD = r"""
import json, os, socket, sys, threading, time
ROOT = os.environ["MBDSDR_ROOT"]
sys.path.insert(0, ROOT)
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
out = {}
from mbdsdr_ai.new_spacetime_tle import TLEManager, CELESTRAK_BASE
from mbdsdr_ai.tle_fetch import FetchPolicy
tm = TLEManager(cache_dir=os.path.join(os.environ["MBDSDR_TMP"], "tle_cache_probe"))
out["config"] = {
    "manager_timeout_s": tm.timeout,
    "manager_hard_deadline_s": tm.timeout + 5.0,
    "fetcher_request_timeout_s": tm._store().policy.request_timeout_s,
    "fetcher_hard_deadline_s": tm._store().policy.hard_deadline_s,
    "fetchpolicy_default_request_s": FetchPolicy().request_timeout_s,
    "fetchpolicy_default_hard_s": FetchPolicy().hard_deadline_s,
    "celestrak_base": CELESTRAK_BASE,
}

from mbdsdr_ai.orbit import _bounded_urlopen
import urllib.request
UA = {"User-Agent": "MBDSDR-perf-probe"}

def time_url(url, timeout_s=10.0, hard_s=15.0):
    req = urllib.request.Request(url, headers=UA)
    t = time.perf_counter()
    try:
        r = _bounded_urlopen(req, timeout_s, hard_s)
        body = r.read()
        r.close()
        return {"ok": True, "wall_s": round(time.perf_counter() - t, 3),
                "bytes": len(body)}
    except Exception as e:
        return {"ok": False, "wall_s": round(time.perf_counter() - t, 3),
                "error": f"{type(e).__name__}: {e}"}

# 真实 Celestrak 请求 × 3
real = []
for _ in range(3):
    real.append(time_url(CELESTRAK_BASE + "?CATNR=25544&FORMAT=tle"))
out["celestrak_real"] = real

# 真实用户路径：TLEManager.fetch（含 sidecar 缓存写）
fetches = []
for _ in range(2):
    t = time.perf_counter()
    try:
        ent = tm.fetch(25544, "ISS")
        fetches.append({"ok": True, "wall_s": round(time.perf_counter() - t, 3),
                        "name": getattr(ent, "name", "ISS")})
    except Exception as e:
        fetches.append({"ok": False, "wall_s": round(time.perf_counter() - t, 3),
                        "error": f"{type(e).__name__}: {e}"})
out["manager_fetch"] = fetches

# 最坏情况：本机黑洞服务器 —— 接受连接但永不回响应头
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", 0))
srv.listen(5)
port = srv.getsockname()[1]
stop = threading.Event()
conns = []
def acceptor():
    while not stop.is_set():
        try:
            c, _ = srv.accept()
            conns.append(c)
        except OSError:
            return
threading.Thread(target=acceptor, daemon=True).start()
url = f"http://127.0.0.1:{port}/tle"
out["blackhole_timeout"] = time_url(url, 10.0, 15.0)
out["blackhole_hard_deadline_1s"] = time_url(url, 10.0, 1.0)
stop.set()
try:
    srv.close()
except OSError:
    pass
for c in conns:
    try:
        c.close()
    except OSError:
        pass
print("TLE_JSON " + json.dumps(out, ensure_ascii=False))
"""


def section_tle():
    probe = os.path.join(TMP, "perf_probe_deep_tle.py")
    os.makedirs(TMP, exist_ok=True)
    with open(probe, "w", encoding="utf-8") as f:
        f.write(_TLE_CHILD)
    cache = os.path.join(TMP, "tle_cache_probe")
    try:
        p = _run([PY, probe], env=_child_env(MBDSDR_TMP=TMP), timeout=300)
        for ln in p.stdout.splitlines():
            if ln.startswith("TLE_JSON "):
                return json.loads(ln[len("TLE_JSON "):])
        raise RuntimeError("tle child 没有输出 TLE_JSON")
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass
        import shutil
        shutil.rmtree(cache, ignore_errors=True)


# =========================================================================== #
def _markdown(result: dict) -> str:
    md = []
    if "startup" in result:
        s = result["startup"]
        m = s["median"]
        rows = [
            ("Qt 导入（PySide6）", m.get("dur_qt_import")),
            ("main_window 导入", m.get("dur_main_window_import")),
            ("QApplication 构造", m.get("dur_qt_app_create")),
            ("字体（families + setFont）",
             round(m.get("font_families", 0) + m.get("font_set", 0), 1)),
            ("主题 apply", m.get("theme_apply")),
            ("MainWindow 构造（容器）", m.get("dur_main_window_init")),
            ("  └ eventFilter（构造期事件）", m.get("eventfilter_ms")),
            ("  └ _build_kernel_panels", m.get("kernel_panels_ms")),
            ("      └ SettingsPanel 构造", m.get("settings_panel_init_ms")),
            ("  └ _build_mode_shell", m.get("mode_shell_ms")),
            ("  └ 星表加载（首帧内，1.9 MB npz）", m.get("star_load_ms")),
            ("show + processEvents（首帧）", m.get("dur_show_first_paint")),
            ("grab 首帧", m.get("grab_first_frame")),
            ("**进程 → 首帧合计**", m.get("dur_first_frame")),
            ("首帧后枚举累计（含后台线程 + 面板轮询）", m.get("backend_enum_ms")),
            ("**进程 → 3.5 s 观察窗结束**", m.get("deferred_end")),
        ]
        md += ["## 1) 启动分阶段（独立子进程，时间戳=perf_counter，单位 ms）", "",
               _md_table(rows, ["阶段", "中位 ms（3 次独立进程）"]), "",
               f"构造期 eventFilter 调用 {m.get('eventfilter_calls')} 次；"
               f"星表 {m.get('star_catalog_mb')} MB；"
               f"后端枚举设备 {m.get('backend_enum_devices')} 台。", "",
               "前三名（可归因叶子块）：" + "、".join(
                   f"`{k}` {v} ms" for k, v in s["top3"]), "",
               "eventFilter 同窗口 A/B（只测构造，n=3）："
               f"正常 {s['eventfilter_ab']['median_normal_ms']} ms → "
               f"置空 {s['eventfilter_ab']['median_stubbed_ms']} ms，"
               f"差 **{s['eventfilter_ab']['delta_ms']} ms**。", "",
               "eventFilter 改前/改后（旧实现按 HEAD 原样重建，交替 3 次）："
               f"{s['ef_opt_ab']['median_old_ms']} ms → "
               f"{s['ef_opt_ab']['median_new_ms']} ms（"
               f"{s['ef_opt_ab']['delta_pct']}%）。", "",
               "启动自动连接 GUI 占用（sync=枚举+connect 在 GUI，async=只在后台）："
               f"sync {s['enum_bg_ab_detail']['sync']}；async "
               f"{s['enum_bg_ab_detail']['async']}。", ""]
    if "replay" in result:
        md += ["## 2) 回放读盘 GUI 心跳（10 ms QTimer）", "",
               _md_table([(c["mode"], c["max_samples"], c["file_mb"],
                           c["wall_s"], c["gap_p50_ms"], c["gap_p95_ms"],
                           c["gap_max_ms"], c.get("peak_ws_mb"),
                           c.get("samples"), c["progress_events"])
                          for c in result["replay"]["cases"]],
                         ["模式", "max_samples", "文件 MB", "读盘 s",
                          "p50 gap", "p95 gap", "max gap", "峰值 WS MB",
                          "样本", "进度事件"]), ""]
    if "spectrum" in result:
        sp = result["spectrum"]
        cfg = sp["config"]
        mea = sp["measured"]
        md += ["## 3) 频谱（2.4 MS/s / 1024 点）", "",
               f"配置：FFT {cfg['fft_size']} / bins {cfg['num_bins']} / "
               f"曲线定时器 {cfg['curve_timer_ms']} ms / 每块 "
               f"{cfg['feed_block_samples']} 样本 @ {cfg['feed_interval_ms']} ms", "",
               f"实测：{mea['elapsed_s']} s 内 **{mea['paints']} 帧 → "
               f"{mea['fps']} fps**；单帧 {mea['paint']}；push_iq {mea['push_iq']}", "",
               _md_table([(p["tottime_s"], p["calls"], p["func"])
                          for p in sp["profile_top"]],
                         ["tottime s", "calls", "函数"]), "",
               _md_table([(k, v) for k, v in sp["microbench"].items()],
                         ["微基准", "中位 ms"]), ""]
    if "tle" in result:
        t = result["tle"]
        md += ["## 4) TLE 联网", "",
               _md_table([(k, v) for k, v in t["config"].items()], ["配置", "值"]), "",
               "真实 Celestrak：" + json.dumps(t["celestrak_real"], ensure_ascii=False), "",
               "TLEManager.fetch：" + json.dumps(t["manager_fetch"], ensure_ascii=False), "",
               "本机黑洞（收连接不回包）：请求超时 10 s → "
               + json.dumps(t["blackhole_timeout"], ensure_ascii=False),
               "硬截止 1 s 验证 → "
               + json.dumps(t["blackhole_hard_deadline_1s"], ensure_ascii=False), ""]
    s = result.get("streams")
    if s:
        md += ["## 3b) 流水线启动拆账（_start_iq_streams）", "",
               _md_table([(k, v) for k, v in s["median"].items()],
                         ["项", "中位 ms"]), "",
               s.get("note", ""), ""]
    return "\n".join(md)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", default="all",
                    choices=["all", "startup", "replay", "spectrum", "streams",
                             "tle"])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--replay-file",
                    default=os.path.join(TMP, "replay_1100MiB.cf32"))
    ap.add_argument("--keep-replay-file", action="store_true")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    result = {"generated_at": dt.datetime.now().isoformat(timespec="seconds"),
              "env": {"cpu": _cpu_name(), "cpu_count": os.cpu_count(),
                      "python": sys.version.split()[0],
                      "platform": platform.platform(),
                      "qt_platform": os.environ.get("QT_QPA_PLATFORM")}}
    if a.section in ("all", "startup"):
        result["startup"] = section_startup(a.runs)
    if a.section in ("all", "replay"):
        result["replay"] = section_replay(a.replay_file, a.keep_replay_file)
    if a.section in ("all", "spectrum"):
        result["spectrum"] = section_spectrum()
    if a.section in ("all", "streams"):
        result["streams"] = section_streams()
    if a.section in ("all", "tle"):
        result["tle"] = section_tle()

    text = _markdown(result)
    print(text)
    out = a.json or os.path.join(
        ROOT, "output", "bench",
        f"deep_perf_{dt.datetime.now():%Y%m%d_%H%M%S}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\nJSON: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
