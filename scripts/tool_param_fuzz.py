#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""P14：工具参数空间鲁棒性（模糊测试）。

对被审计为一类（真能跑）的工具逐个喂对抗性参数，只按三条判定：

  ① 不许崩溃：异常不得逃逸出 `ToolRegistry.call`（handler 内未捕获异常同算）；
  ② 不许挂死：单次调用超过 `--timeout` 秒即记挂死；
  ③ 失败必须结构化：success=False 时必须有错误码与可读原因；
     非法值（NaN/Inf、负数采样率、超界数值……）**不许**被当成合法值静默执行。

分类账本：
  OK        通过（合法输入照常返回，或非法输入被结构化拒绝）
  CRASH     崩溃（异常逃逸 / handler 抛异常被兜底成「调用失败: XxxError」）
  HANG      挂死（超过单次调用上限）
  MEMORY    内存炸弹（单用例进程 commit 增长超过 --mem-cap-mb）
  SILENT    静默接受（非法输入 success=True）
  UNSTRUCT  失败但无错误码（结构不合格）

安全：每次调用都带内存监控；超过上限立即注入异常中止，绝不让一次
fuzz 把整台机器拖死。`long_string` / `array_long` 按长度分档串行跑，
每跑完一档打印该档的输入长度、峰值内存与耗时。

用法：
  python scripts/tool_param_fuzz.py                 # 跑一类工具全量，写 docs/tool-param-fuzz.md
  python scripts/tool_param_fuzz.py --only ft8_encode sdr_set_frequency
  python scripts/tool_param_fuzz.py --limit 24      # 只抽样前 24 个（CI 抽样口径）
  python scripts/tool_param_fuzz.py --json out.json # 逐次调用明细
  python scripts/tool_param_fuzz.py --mem-cap-mb 1024
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")
os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:9")
os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:9")

OK, CRASH, HANG, SILENT, UNSTRUCT = "OK", "CRASH", "HANG", "SILENT", "UNSTRUCT"
#: 长输入压力用例在基础预算内跑不完、但未超过放宽预算：算吞吐慢，不算挂死。
SLOW = "SLOW"
#: handler 内部异常被注册表兜住（结构化失败：exception:<类型> + 原因）。
#: 按判定口径它不属于「异常逃逸」，单独记账，避免把已兜底的失败误报成崩溃。
HANDLED = "HANDLED"
#: 单用例进程 commit（私有内存）增长超过闸值：内存炸弹，立即中止。
MEMORY = "MEMORY"

#: 单次调用上限（秒）。超时即缺陷，不等它自己回来。
#: 10s 覆盖纯 Python DSP 的正常计算量（160 bit 载荷≈5–6s），
#: 真正不返回的环路仍会被 10s 闸拦住。
DEFAULT_TIMEOUT = 10.0
#: 单用例允许的进程 commit 增长（MB）。超过即判 MEMORY 并中止该线程：
#: 2026-11-05 事故——`long_string` 把 feldhell/psk31/rtty 的 Python float
#: 列表喂到 17.2GB，叠加打满系统 commit 后进程被拖死。
DEFAULT_MEM_CAP_MB = 1536.0
#: 与共享校验保持一致：任何数值参数的合理上界
ABS_LIMIT = 1e15
#: 超长字符串 / 超长列表的对抗长度（分档串行，逐档报资源占用）
LONG_STR = 100_000
LONG_LIST = 50_000
#: 文本类长输入档位：先小后大；跑完一档报一次峰值内存/耗时。
LONG_STR_TIERS = (2_000, 20_000, LONG_STR)
#: 数组类长输入档位
LONG_LIST_TIERS = (5_000, LONG_LIST)
#: 长输入用例的预算放宽倍数（纯 Python DSP 吃 10 万点本来就慢，不算挂死；
#: 超过放宽预算仍不返回才算挂死）。基础预算用于其余对抗用例。
STRESS_BUDGET_FACTOR = 6.0

#: 语义上不允许为负的参数名（正则，避免误伤 offset/doppler/经纬度/增益 dB）
_NONNEG_RE = re.compile(
    r"(sample_rate|samplerate|bandwidth|baud|duration|seconds|secs|"
    r"num_samples|n_samples|samples|points|count|num_|size|length|"
    r"frequency|freq|center_freq|prn|repeat|trials|rate|"
    r"decim|iter|rounds|limit|last_n|max_results|max_items|max_count|"
    r"max_tokens|max_iter|top_n|order|fft|sps|symbol_rate|oversampl|"
    r"slot|interval|period)",
    re.I)
_NEG_EXEMPT_RE = re.compile(r"(offset|shift|doppler|delta|error|gain|power|"
                            r"db|latitude|longitude|lat|lon|azimuth|elevation|az|el)", re.I)
#: 速率/频率类参数：极小正值（1e-300）会让采样/分频计算爆量，按非法拒绝
_RATE_LIKE_RE = re.compile(r"(sample_rate|samplerate|baud|symbol_rate|sps|"
                           r"bandwidth|decim|oversampl|frequency|freq|rate|"
                           r"slot|interval|period|duration|window_s|time_s)", re.I)

#: 路径类参数名
_PATH_RE = re.compile(r"(path|file|filename|dir|iq_|wav|audio|recording)", re.I)
#: 采样类数组参数名
_SAMPLE_ARR_RE = re.compile(r"(sample|iq|audio|signal|mpx|bits|llr|tone|symbol|"
                            r"snapshot|observ|state|vec|taps|codeword)", re.I)


# ── 建 agent（与 P13 审计同一套替身，确保环境一致）──────────────────
def _build_agent(tmp):
    from mbdsdr_ai import model_manager as _mm
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig
    import mbdsdr_ai.sdr_backend as _sb

    _mm.ModelManager.fetch_models = lambda self, *a, **k: None
    _sb.SDRBackendManager._discover = lambda self: None
    agent = MBDSDRAgent(AgentConfig(api_key="sk-test", model="test-model"))
    reg = agent.tool_registry
    mgr = getattr(agent, "sdr_manager", None)
    if mgr is not None:
        mgr.backends.clear()
        mgr.active_backend = None
    evo = getattr(agent, "evolution", None)
    if evo is not None and getattr(evo, "version_store", None) is not None:
        from mbdsdr_ai.version_store import VersionStore

        evo.version_store = VersionStore(os.path.join(tmp, "evolution"))
        evo.version_store.snapshot(
            message="初始版本（模糊测试隔离库）",
            files={"system_prompt.txt": "", "tool_descriptions.json": "{}"},
            author="system")
    return agent, reg


# ── 参数挑选 ───────────────────────────────────────────────────
def _schema(tool):
    return (tool.get("definition", {}).get("function", {})
            .get("parameters", {}) or {})


def _props(tool):
    return (_schema(tool).get("properties", {}) or {})


def _required(tool):
    return set(_schema(tool).get("required", []) or [])


def _is_num(spec):
    return (spec or {}).get("type") in ("number", "integer")


def _is_numeric_array(spec):
    spec = spec or {}
    return (spec.get("type") == "array"
            and (spec.get("items", {}) or {}).get("type") in ("number", "integer"))


def _pick_numeric(tool):
    """挑一个数值参数：优先必填、优先频率/采样率等关键语义。"""
    props = _props(tool)
    req = _required(tool)
    cands = [n for n, s in props.items() if _is_num(s)]
    if not cands:
        return ""
    cands.sort(key=lambda n: (n not in req,
                              not _NONNEG_RE.search(n),
                              n))
    return cands[0]


def _pick_numeric_second(tool, first):
    props = _props(tool)
    req = _required(tool)
    cands = [n for n, s in props.items() if _is_num(s) and n != first]
    if not cands:
        return ""
    cands.sort(key=lambda n: (n not in req, n))
    return cands[0]


def _pick_string(tool, path_like=False):
    props = _props(tool)
    req = _required(tool)
    cands = [n for n, s in props.items() if (s or {}).get("type") == "string"]
    if path_like:
        pc = [n for n in cands if _PATH_RE.search(n)]
        if pc:
            cands = pc
    if not cands:
        return ""
    cands.sort(key=lambda n: (n not in req,
                              not _PATH_RE.search(n) if not path_like else 0,
                              n))
    return cands[0]


def _pick_array(tool):
    props = _props(tool)
    req = _required(tool)
    cands = [n for n, s in props.items() if _is_numeric_array(s)]
    if not cands:
        return ""
    cands.sort(key=lambda n: (n not in req,
                              not _SAMPLE_ARR_RE.search(n), n))
    return cands[0]


def _pick_bool(tool):
    props = _props(tool)
    req = _required(tool)
    cands = [n for n, s in props.items() if (s or {}).get("type") == "boolean"]
    if not cands:
        return ""
    cands.sort(key=lambda n: (n not in req, n))
    return cands[0]


def _sample_rate_param(tool):
    for n in _props(tool):
        if re.search(r"sample_rate|samplerate", n, re.I):
            return n
    return ""


def _is_nonneg(name):
    return bool(_NONNEG_RE.search(name)) and not bool(_NEG_EXEMPT_RE.search(name))


def _is_rate_like(name):
    return bool(_RATE_LIKE_RE.search(name)) and not bool(_NEG_EXEMPT_RE.search(name))


# ── 对抗用例生成 ─────────────────────────────────────────────────
class Case:
    __slots__ = ("cid", "args", "must_reject", "note", "stress", "size")

    def __init__(self, cid, args, must_reject, note, stress=False, size=0):
        self.cid = cid
        self.args = args
        self.must_reject = must_reject
        self.note = note
        self.stress = stress
        #: 输入规模（字符数/元素数），用于逐档资源报告
        self.size = size


def _cases(tool, baseline):
    props = _props(tool)
    req = _required(tool)
    cases = []

    # 1) 空字典 / 缺必填
    cases.append(Case("empty_args", {}, bool(req), "空字典（无任何参数）"))
    if req:
        miss = dict(baseline)
        miss.pop(sorted(req)[0], None)
        cases.append(Case("missing_required", miss, True, "缺一个必填参数"))

    # 2) 多余未知键（合法基线 + 垃圾键）：应被容忍，不算缺陷
    extra = dict(baseline)
    extra["__fuzz_unknown_key__"] = {"nested": [1, 2, 3]}
    extra["unknown_scalar"] = "whatever"
    cases.append(Case("extra_unknown_keys", extra, False, "多余未知键"))

    num = _pick_numeric(tool)
    if num:
        for cid, val, must, note in (
                ("numeric_type_string", "false", True, f"{num} 给字符串 'false'"),
                ("numeric_nan", float("nan"), True, f"{num} 给 NaN"),
                ("numeric_inf", float("inf"), True, f"{num} 给 +Inf"),
                ("numeric_huge", 1e300, True, f"{num} 给 1e300"),
                ("numeric_tiny", 1e-300, _is_rate_like(num),
                 f"{num} 给 1e-300（速率/频率类参数视为非法）"),
        ):
            a = dict(baseline)
            a[num] = val
            cases.append(Case(cid, a, must, note))
        a = dict(baseline)
        a[num] = -12345.0
        cases.append(Case("numeric_negative", a, _is_nonneg(num),
                          f"{num} 给负数（语义上不可为负则必须拒绝）"))
        if (props.get(num) or {}).get("type") == "number":
            a = dict(baseline)
            a[num] = "NaN"
            cases.append(Case("numeric_string_nan", a, True,
                              f"{num} 给字符串 'NaN'（不得强转成 NaN 放行）"))

    sr = _sample_rate_param(tool)
    if sr:
        a = dict(baseline)
        a[sr] = 0
        cases.append(Case("sample_rate_zero", a, True, f"{sr}=0"))
        a = dict(baseline)
        a[sr] = -1024000
        cases.append(Case("sample_rate_negative", a, True, f"{sr}<0"))

    s = _pick_string(tool, path_like=True)
    if s:
        a = dict(baseline)
        a[s] = "../../../../etc/passwd"
        cases.append(Case("path_traversal", a, False, f"{s} 给 ../../../../etc/passwd"))
    s2 = _pick_string(tool, path_like=False)
    if s2:
        # 长输入按档位串行（先小后大）：跑完一档报一次资源占用，
        # 绝不一次性把 10 万字符喂进去（2026-11-05 内存炸弹事故）。
        for n in LONG_STR_TIERS:
            a = dict(baseline)
            a[s2] = "A" * n
            cases.append(Case(f"long_string_{n}", a, False,
                              f"{s2} 给 {n} 字符", stress=True, size=n))
        a = dict(baseline)
        a[s2] = "abc\u0000\u0001\u001b\u202e汉🙂"
        cases.append(Case("unicode_control", a, False, f"{s2} 给 Unicode/控制字符"))

    arr = _pick_array(tool)
    if arr:
        a = dict(baseline)
        a[arr] = []
        cases.append(Case("array_empty", a, arr in req,
                          f"{arr} 给零长度数组"))
        a = dict(baseline)
        a[arr] = [0.5]
        cases.append(Case("array_single", a, False, f"{arr} 只给单点"))
        for n in LONG_LIST_TIERS:
            a = dict(baseline)
            a[arr] = [0.5] * n
            cases.append(Case(f"array_long_{n}", a, False,
                              f"{arr} 给 {n} 个元素", stress=True, size=n))
        a = dict(baseline)
        a[arr] = [float("nan"), 0.5, -0.5]
        cases.append(Case("array_nan_element", a, True, f"{arr} 含 NaN 元素"))

    b = _pick_bool(tool)
    if b:
        a = dict(baseline)
        a[b] = "false"
        cases.append(Case("bool_string", a, True, f"{b} 给字符串 'false'"))
    return cases


# ── 单次调用 + 超时 ─────────────────────────────────────────────
class _TimeoutAbort(BaseException):
    """注入超时线程的异步异常（BaseException 不被 handler 的 except Exception 吞掉）。"""


def _abort_thread(t):
    """超时/超内存后向工作线程注入异步异常，避免挂死线程继续吃 CPU/内存。"""
    if t is None or t.ident is None:
        return
    try:
        import ctypes
        ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(t.ident), ctypes.py_object(_TimeoutAbort))
    except (AttributeError, OSError, ValueError):
        # 线程已退出或当前解释器不暴露 ctypes 能力：注入是尽力而为，
        # 不影响本次调用的 HANG/MEMORY 判定（判定已在 join 处完成）。
        return


# ── 进程内存探针（RSS / commit）───────────────────────────────────
#: 返回 ``(rss_mb, commit_mb)`` 的探针；None 表示当前平台不可用。
_MEM_PROBE = None
_MEM_PROBE_READY = False
_MEM_PROBE_WARNING_PRINTED = False


class _ProcessMemoryCountersEx:
    """Windows PROCESS_MEMORY_COUNTERS_EX（此处只用到 WS/PrivateUsage）。"""


def _mem_probe_supported() -> bool:
    """当前平台是否应能取到进程内存（只判平台，不依赖探针实现）。"""
    return sys.platform == "win32" or Path("/proc/self/status").exists()


def _make_mem_probe():
    """构造内存探针：Windows 走 psapi，其它平台读 /proc/self/status。

    平台不支持返回 None；平台支持但构造失败直接抛出——不得把「探针代码坏了」
    静默降级成「平台不支持」。
    """
    if sys.platform != "win32":
        status = Path("/proc/self/status")
        if not status.exists():
            return None

        def _linux():
            try:
                for line in status.read_text(encoding="utf-8").splitlines():
                    if line.startswith("VmRSS:"):
                        rss_kb = float(line.split()[1])
                        return rss_kb / 1024.0, rss_kb / 1024.0
            except (OSError, ValueError, IndexError):
                return None
            return None

        return _linux

    import ctypes
    from ctypes import wintypes

    class _PMC(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    psapi = ctypes.WinDLL("psapi.dll")
    kernel32 = ctypes.WinDLL("kernel32.dll")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_PMC), ctypes.c_ulong]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    hproc = kernel32.GetCurrentProcess()

    def _win():
        pmc = _PMC()
        pmc.cb = ctypes.sizeof(_PMC)
        if not psapi.GetProcessMemoryInfo(hproc, ctypes.byref(pmc), pmc.cb):
            return None
        return (pmc.WorkingSetSize / 1048576.0,
                pmc.PrivateUsage / 1048576.0)

    return _win


def _mem_probe():
    """当前进程 ``(rss_mb, commit_mb)``；拿不到时返回 None。"""
    global _MEM_PROBE, _MEM_PROBE_READY, _MEM_PROBE_WARNING_PRINTED
    if not _MEM_PROBE_READY:
        _MEM_PROBE = _make_mem_probe()
        _MEM_PROBE_READY = True
    if _MEM_PROBE is None:
        if not _MEM_PROBE_WARNING_PRINTED:
            print("[mem] 当前平台拿不到进程内存，本次不设内存闸（其余判据照常）",
                  flush=True)
            _MEM_PROBE_WARNING_PRINTED = True
        return None
    return _MEM_PROBE()


def _memory_watcher(stop, state, cap_mb):
    """采样本进程内存，记录峰值；commit 增长超闸值即置 exceeded 并退出。"""
    while not stop.is_set():
        snap = _mem_probe()
        if snap is None:
            return
        rss, commit = snap
        if rss > state["peak_rss"]:
            state["peak_rss"] = rss
        if commit > state["peak_commit"]:
            state["peak_commit"] = commit
        if commit - state["base_commit"] > cap_mb:
            state["exceeded"] = True
            return
        stop.wait(0.05)


def _call_with_timeout(reg, name, args, timeout, mem_cap_mb=DEFAULT_MEM_CAP_MB):
    holder = {}
    before = _mem_probe() or (0.0, 0.0)
    state = {"base_rss": before[0], "base_commit": before[1],
             "peak_rss": before[0], "peak_commit": before[1], "exceeded": False}

    def _run():
        try:
            holder["res"] = reg.call(name, args)
        except BaseException as e:  # noqa: BLE001 - 崩溃就是缺陷，必须记账
            holder["exc"] = e

    t = threading.Thread(target=_run, daemon=True)
    stop = threading.Event()
    watcher = threading.Thread(target=_memory_watcher, args=(stop, state, mem_cap_mb),
                               daemon=True)
    t0 = time.time()
    watcher.start()
    t.start()
    while t.is_alive():
        t.join(0.05)
        if state["exceeded"]:
            _abort_thread(t)
            t.join(0.5)
            break
        if time.time() - t0 > timeout:
            break
    elapsed = time.time() - t0
    stop.set()
    hung = t.is_alive()
    if hung:
        _abort_thread(t)
    peak = state["peak_commit"]
    return (holder.get("res"), holder.get("exc"), elapsed, hung,
            state["peak_rss"], peak, state["exceeded"],
            peak - state["base_commit"])


def _summary_args(args):
    """紧凑描述参数，避免把 20 万元素数组 json.dumps 成巨型字符串（会 MemoryError）。"""
    parts = []
    for k, v in (args or {}).items():
        if isinstance(v, (list, tuple)):
            parts.append(f"{k}=<{type(v).__name__} len={len(v)}>")
        elif isinstance(v, str):
            parts.append(f"{k}={v[:40]!r}{'…' if len(v) > 40 else ''}")
        elif isinstance(v, dict):
            parts.append(f"{k}=<dict keys={list(v)[:5]}>")
        else:
            parts.append(f"{k}={v!r}")
    return ", ".join(parts)[:300]


def _judge(case, res, exc, elapsed, budget, timeout,
           mem_peak_mb=0.0, mem_growth_mb=0.0, mem_exceeded=False,
           mem_cap_mb=DEFAULT_MEM_CAP_MB):
    if mem_exceeded:
        return MEMORY, (f"单用例 commit 增长 {mem_growth_mb:.0f}MB > 闸值 "
                        f"{mem_cap_mb:.0f}MB（峰值 {mem_peak_mb:.0f}MB，"
                        f"输入规模 {case.size}）→ 已中止")
    if exc is not None:
        return CRASH, f"异常逃逸 {type(exc).__name__}: {exc}"
    if elapsed >= budget:
        return HANG, f"超过单次上限 {budget:g}s"
    if res is None:
        return CRASH, "无结果对象"
    err = str(getattr(res, "error", "") or "")
    content = str(getattr(res, "content", "") or "")
    if err.startswith("exception"):
        return HANDLED, f"handler 内部异常（已兜底）：{err}｜{content[:120]}"
    if not res.success:
        if not err:
            return UNSTRUCT, f"失败无错误码：{content[:120]}"
        cat, detail = OK, f"结构化拒绝 {err}"
    elif case.must_reject:
        cat, detail = SILENT, f"非法输入被接受：{content[:140]}"
    else:
        cat, detail = OK, "成功"
    if cat == OK and elapsed >= timeout:
        return SLOW, f"吞吐慢：{elapsed:.1f}s（长输入预算 {budget:g}s）"
    return cat, detail


def _first_class_names():
    """从 P13 总账解析一类工具名单，保证模糊测试口径与账本一致。"""
    md = ROOT / "docs" / "tool-availability.md"
    if not md.exists():
        return None
    names = []
    for line in md.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\| `([^`]+)` \| [^|]+ \| 一类·真跑 \|", line)
        if m:
            names.append(m.group(1))
    return names


# ── 主流程 ─────────────────────────────────────────────────────
def run_fuzz(only=None, limit=0, timeout=DEFAULT_TIMEOUT, offset=0,
             mem_cap_mb=DEFAULT_MEM_CAP_MB):
    import tool_availability_audit as audit

    tmp = tempfile.mkdtemp(prefix="mbdsdr_fuzz_")
    records = []
    try:
        agent, reg = _build_agent(tmp)
        pipelines, hex_frame = audit._run_pipeline(reg, tmp)

        names = _first_class_names()
        if names is None:
            names = reg.get_tool_names()
        if only:
            names = [n for n in names if n in set(only)] if names else []
            if not names:
                names = [n for n in only if n in reg.tools]
        if offset:
            names = names[offset:]
        if limit:
            names = names[:limit]

        for idx, name in enumerate(names, 1):
            if name not in reg.tools:
                continue
            tool = reg.tools[name]
            if os.environ.get("MBDSDR_FUZZ_VERBOSE"):
                print(f"[{idx}/{len(names)}] {name}", flush=True)
            baseline = audit._build_args({"parameters": _schema(tool)}, tmp, hex_frame)
            if name in audit._SPECIAL_ARGS:
                baseline.update(audit._SPECIAL_ARGS[name](tmp))
            if name in pipelines:
                k, v = pipelines[name]
                baseline[k] = v
            mgr = getattr(agent, "sdr_manager", None)
            if mgr is not None:
                try:
                    mgr.disconnect()
                except (AttributeError, RuntimeError, OSError):
                    # 每个工具跑完后的清理：后端可能已处于断开状态，
                    # 清理失败不影响本次观测，后续工具各自建新 agent。
                    pass
                mgr.active_backend = None

            try:
                cases = _cases(tool, baseline)
            except Exception as e:  # noqa: BLE001 - 用例生成失败也要记账
                records.append({"tool": name, "case": "build_cases", "class": CRASH,
                                "detail": f"用例生成异常 {type(e).__name__}: {e}",
                                "latency_s": 0.0, "args": "", "snippet": ""})
                continue

            for case in cases:
                budget = timeout * STRESS_BUDGET_FACTOR if case.stress else timeout
                (res, exc, elapsed, hung, peak_rss, peak_commit,
                 mem_exceeded, mem_growth) = _call_with_timeout(
                    reg, name, case.args, budget, mem_cap_mb)
                cat, detail = _judge(case, res, exc, elapsed, budget, timeout,
                                     peak_commit, mem_growth, mem_exceeded,
                                     mem_cap_mb)
                if hung and cat != MEMORY:
                    cat, detail = HANG, f"超过单次上限 {budget:g}s"
                if case.stress:
                    # 逐档报告资源占用：输入规模 / 峰值内存 / 耗时 / 结论
                    print(f"[mem] {name} {case.cid}: 输入={case.size} "
                          f"rss_peak={peak_rss:.0f}MB commit_peak={peak_commit:.0f}MB "
                          f"(+{mem_growth:.0f}MB) elapsed={elapsed:.2f}s -> {cat}",
                          flush=True)
                records.append({
                    "tool": name,
                    "case": case.cid,
                    "note": case.note,
                    "must_reject": case.must_reject,
                    "stress": case.stress,
                    "input_size": case.size,
                    "mem_rss_peak_mb": round(peak_rss, 1),
                    "mem_commit_peak_mb": round(peak_commit, 1),
                    "mem_growth_mb": round(mem_growth, 1),
                    "budget_s": round(budget, 1),
                    "class": cat,
                    "detail": detail,
                    "latency_s": round(elapsed, 3),
                    "args": _summary_args(case.args),
                    "snippet": str(getattr(res, "content", "") or "")[:200].replace("\n", " "),
                    "error": str(getattr(res, "error", "") or "") if res is not None else "",
                })
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return records


#: 本轮修复记录（渲染进账本，保证「改了什么」可追溯）
FIX_LOG = [
    "共享校验 ①：`validate_arguments` 拒绝非有限数值（NaN/±Inf）——数字参数与 "
    "`\"NaN\"`/`\"inf\"` 字符串强转路径都在此收口。",
    "共享校验 ②：`validate_arguments` 拒绝绝对值 > 1e15 的数值（1e300 这类 "
    "物理上不可能的极大值不再进入 handler 触发 OverflowError/内存爆炸）。",
    "共享校验 ③：`validate_arguments` 对整数参数先做有限性检查，避免 "
    "`int(NaN)`/`int(Inf)` 在注册表层抛 ValueError/OverflowError（原为真·异常逃逸）。",
    "共享校验 ④：语义不可为负的参数（sample_rate/bandwidth/duration/count/frequency 等）"
    "统一拒绝负值；offset/doppler/gain/经纬度等允许负值的参数不在此列。",
    "共享校验 ⑤：`sample_rate`/`sample_rate_hz` 为 0 视为非法（后续除零/空 IQ 的直接来源）。",
    "共享校验 ⑥：失败结果必须带错误码；注册表对无码失败统一补 `handler_error`。",
    "共享校验 ⑦：速率/频率/时长类参数（含 `rate`/`slot_s`/`decimation`）的极小正值"
    "（<1e-9）一律拒绝——`1e-300` 的 slot 会把槽数算到爆炸（fhss_hop_track 实测挂死）。",
    "共享校验 ⑧：必填数字数组非空、元素类型与 schema 一致且有限；整数数组拒浮点，"
    "避免 bytes()/索引运算在 handler 内抛 TypeError。",
    "共享校验 ⑨：负数语义扩展到 `rate`/`decimation`/`max_iter`/`limit`/`last_n`/"
    "`order`/`fft`/`slot` 等，slice 负索引与负迭代次数不再进 handler。",
    "回归锁：`tests/test_tool_param_fuzz_guard.py` 固定 NaN/Inf/负数采样率/1e300/"
    "NaN 数组元素等典型「静默接受」案例。",
    "内存炸弹收口（2026-11-05 事故）：`feldhell_decode`/`psk31_decode`/`rtty_decode` "
    "的波形从 Python float 列表改为 `array('d')`，FeldHell 包络与 RTTY 4 路正交能量"
    "改为流式累加；4000 字符实测 RSS feldhell 480MB / psk31 231MB / rtty 168MB"
    "（优化前 2000 字符 feldhell 已 1.1GB、4000 字符被 2.5GB 监控强杀）。"
    "文本往返上限同步收紧到 4000 字符并写入工具 schema 描述，超限秒回 "
    "`param_out_of_range`。模糊测试本身新增单用例内存闸（默认 1536MB）与"
    "长输入分档串行，逐档打印峰值内存。",
]


def render_md(records, timeout=DEFAULT_TIMEOUT):
    counts = collections.Counter(r["class"] for r in records)
    total = len(records)
    tools = len({r["tool"] for r in records})
    bad = [r for r in records if r["class"] != OK]
    by_tool = collections.Counter(r["tool"] for r in bad)
    cases = collections.Counter(r["case"] for r in bad)
    lines = [
        "# 工具参数鲁棒性账本（P14 模糊测试）",
        "",
        f"- 覆盖：**{tools}** 个一类工具，共 **{total}** 次对抗调用",
        f"- 单次调用上限：常规 **{timeout:g}s**；长输入压力用例"
        f"放宽到 **{timeout * STRESS_BUDGET_FACTOR:g}s**（超预算才算挂死）；"
        f"单用例 commit 增长闸 **{DEFAULT_MEM_CAP_MB:g}MB**（超闸即判内存炸弹并中止）",
        f"- 环境：`MBDSDR_NO_HW=1` + `MBDSDR_OFFLINE=1` + `MBDSDR_NULL_AUDIO=1` + offscreen",
        f"- 结论：通过 {counts[OK]} / 崩溃（异常逃逸）{counts[CRASH]} / 挂死 {counts[HANG]} / "
        f"内存炸弹 {counts[MEMORY]} / "
        f"静默接受 {counts[SILENT]} / 未结构化失败 {counts[UNSTRUCT]} / "
        f"handler 内部异常（已兜底）{counts[HANDLED]} / 长输入吞吐慢 {counts[SLOW]}",
        "",
        "判定只看三条：①异常不逃逸；②单次不超时；③失败有码有原因，"
        "且非法值不得被当成合法值静默执行。",
        "",
        "> `HANDLED` = handler 抛出异常但被注册表兜住，返回 `exception:<类型>` 码"
        "与中文原因；按「异常逃逸」口径不算崩溃，但仍记在账上便于逐个收紧。",
        "",
        "复跑：`python scripts/tool_param_fuzz.py`（`--only <工具>` 单跑，"
        "`--limit N` 抽样）。全量按 `--offset/--limit` 分块跑（每块独立进程，"
        "避免线程/内存累积），再用 `--render` 合并明细；超时项逐个隔离复测，"
        "后给的明细覆盖同 `(tool, case)` 的前值。",
        "",
        "## 共享校验（不是 404 处各修一遍）",
        "",
    ]
    lines += [f"- {x}" for x in FIX_LOG]
    lines += [
        "",
        f"## 非通过项分布（按用例，{len(bad)} 条）",
        "",
        "| 对抗用例 | 缺陷数 | 说明 |",
        "| --- | --- | --- |",
    ]
    for cid, n in cases.most_common():
        note = next((r["note"] for r in bad if r["case"] == cid), "")
        lines.append(f"| `{cid}` | {n} | {note} |")
    lines += [
        "",
        f"## 非通过项逐项（{len(bad)} 条）",
        "",
        "| 工具 | 用例 | 类别 | 证据 |",
        "| --- | --- | --- | --- |",
    ]
    for r in bad:
        d = (r["detail"] or "").replace("|", "\\|")[:140]
        lines.append(f"| `{r['tool']}` | `{r['case']}` | {r['class']} | {d} |")
    if not bad:
        lines.append("| — | — | — | 无（全部通过） |")
    lines += [
        "",
        "## 一类工具里缺陷最多的前 20 个",
        "",
        "| 工具 | 缺陷数 |",
        "| --- | --- |",
    ]
    for t, n in by_tool.most_common(20):
        lines.append(f"| `{t}` | {n} |")
    if not by_tool:
        lines.append("| — | 0 |")
    return "\n".join(lines) + "\n", counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--offset", type=int, default=0, help="跳过前 N 个工具（分块跑）")
    ap.add_argument("--limit", type=int, default=0, help="只抽样前 N 个工具（CI 口径）")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--mem-cap-mb", type=float, default=DEFAULT_MEM_CAP_MB,
                    help="单用例进程 commit 增长上限（MB），超闸判 MEMORY 并中止")
    ap.add_argument("--json", default="")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--render", nargs="*", default=None,
                    help="从若干 --json 明细合并渲染 docs/tool-param-fuzz.md（分块跑后使用）")
    a = ap.parse_args()
    if os.environ.get("MBDSDR_FUZZ_VERBOSE"):
        import faulthandler
        faulthandler.enable()
    if a.render:
        # 后者覆盖前者（同 tool+case）：分块结果在前，隔离复测的覆盖明细在后。
        merged = {}
        for p in a.render:
            for r in json.loads(Path(p).read_text(encoding="utf-8")):
                merged[(r["tool"], r["case"])] = r
        records = list(merged.values())
        md, counts = render_md(records, a.timeout)
        (ROOT / "docs" / "tool-param-fuzz.md").write_text(md, encoding="utf-8")
        print(f"calls={len(records)} tools={len({r['tool'] for r in records})} " +
              " ".join(f"{k}={counts[k]}" for k in
                       (OK, CRASH, HANG, MEMORY, SILENT, UNSTRUCT, HANDLED, SLOW)))
        print("wrote docs/tool-param-fuzz.md")
        return 0
    records = run_fuzz(a.only, a.limit, a.timeout, a.offset, a.mem_cap_mb)
    counts = collections.Counter(r["class"] for r in records)
    print(f"calls={len(records)} tools={len({r['tool'] for r in records})} " +
          " ".join(f"{k}={counts[k]}" for k in
                   (OK, CRASH, HANG, MEMORY, SILENT, UNSTRUCT, HANDLED, SLOW)))
    for r in records:
        if r["class"] != OK:
            print(f"  [{r['class']}] {r['tool']} {r['case']}: {r['detail'][:150]}")
    if a.json:
        Path(a.json).write_text(json.dumps(records, ensure_ascii=False, indent=1,
                                           default=str), encoding="utf-8")
    if not a.no_write and not a.only and not a.limit:
        md, _ = render_md(records, a.timeout)
        (ROOT / "docs" / "tool-param-fuzz.md").write_text(md, encoding="utf-8")
        print("wrote docs/tool-param-fuzz.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
