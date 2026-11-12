#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`config_schema` 每个 item 的 source 文案：引用的符号必须真实存在。

背景：source 里大量写成「全路径 + 符号名」（`desktop/xxx.py` /
`mbdsdr_ai/xxx.py` + 符号），行号会随改动腐烂，符号名也会随重命名腐烂。
本检查遍历 `ALL_ITEMS` 的 source：

  * Python 路径 → import 对应模块后按点链 `getattr`；点链末段取不到时，
    回退到「该标识符是否出现在这个模块源文件里」（实例属性如
    `_PlotState.show_peaks` 只在 `__init__` 里赋值，类上没有）。
  * C++ / Dart 路径 → 在引用文件里文本 grep 符号名。
  * 模块点写（`desktop.themes.DEFAULT_THEME`、`mbdsdr_ai.sdr_backend.
    RTLSDRBackend.SAMPLE_RATES`）→ 按最长可 import 前缀拆开再 `getattr`。

只钉「符号存在」，不比对整句文案（文案改写不该红）。完整口径（能证明
什么 / 不能证明什么 / 符号还在但语义已变需人工复核）见
``desktop/config_schema.py`` 模块 docstring。
纯散文里的提及（没有可判定符号）不猜、不判红；用
``unverifiable_mentions()`` 打清单人工核对。确属历史沿革、无符号可判定的
段落须显式标注 ``历史说明，不参与机检``，本检查据此跳过——标记不是豁免
符号检查的护身符，只是把「已归档的说明」和「待补符号的欠账」分开。
"""
import importlib
import os
import re
import sys
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config_schema import ALL_ITEMS  # noqa: E402

PY_PATH = re.compile(r"(?<![\w.])((?:desktop|mbdsdr_ai)/[\w./-]+\.py)")
NATIVE_PATH = re.compile(r"(?<![\w.])((?:cpp/src|lib)/[\w./-]+\.(?:cpp|h|hpp|dart))")
ANY_PATH = re.compile(f"(?:{PY_PATH.pattern})|(?:{NATIVE_PATH.pattern})")

#: 常量名：允许前导下划线（`_PEAK_REL_DB_DEFAULT`）。
CAPS = re.compile(r"\b_?[A-Z][A-Z0-9_]{3,}\b")
#: 点链（`_PlotState.show_peaks`、`SpectrumDataGenerator.AVG_FRAMES`）。
QUALIFIED = re.compile(r"\b[_A-Z][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b")
#: 函数调用写法（`set_waterfall_depth()`）。
CALL = re.compile(r"\b([a-z_][a-z0-9_]{2,})\(")
#: 路径后紧跟的第一个标识符（`logging_setup.py:configure_logging`）。
FIRST_TOKEN = re.compile(r"^\s*:?\d*(?:\s*[-–]\s*\d+)?\s*([A-Za-z_][A-Za-z0-9_]*)")
#: 模块点写（不是 .py 文件路径）。
DOTTED_MODULE = re.compile(
    r"\b(?:desktop|mbdsdr_ai)(?:\.[a-z_][a-z0-9_]*)+\.[A-Z][A-Za-z0-9_]*"
    r"(?:\.[A-Za-z0-9_]+)*")
#: 斜杠式模块点写（`desktop/rf_sky_view.DISPLAY_REFRACTION_PRESSURE_HPA`）。
DOTTED_SLASH = re.compile(
    r"\b(?:desktop|mbdsdr_ai)/[a-z_][a-z0-9_/]*\.[A-Z][A-Za-z0-9_]*"
    r"(?:\.[A-Za-z0-9_]+)*")
#: C++ 常量命名习惯（kStepValuesHz / kWaterfallDepthChoices）。
NATIVE_SYMBOL = re.compile(r"\bk[A-Z][A-Za-z0-9_]*\b")
#: C++ / Dart 的方法调用写法（spectrum_engine.h 的 setWindowType()）。
#: 有些落点只有方法没有 k 常量，文本 grep 方法名同样能钉住「符号存在」。
NATIVE_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")

#: 历史沿革段落的显式标记：写了它就从散文清单跳过（见模块 docstring）。
HISTORICAL_NOTE = "历史说明，不参与机检"

#: 不是符号的字面量/缩写（日志级别、字面量、单位、协议名）。
NOISE = frozenset({
    "TRUE", "FALSE", "NONE", "NULL",
    "INFO", "DEBUG", "WARNING", "ERROR", "CRITICAL",
    "USB", "GNSS", "FFT", "JSON", "CSV", "HTTP", "HTTPS", "MQTT",
    "UI", "AI", "IQ", "RF", "SDR", "AGC", "VFO", "DSP", "API", "SPDX", "MIT",
})


def _module_of(path: str):
    if path.startswith("desktop/"):
        return importlib.import_module(path[len("desktop/"):-3].replace("/", "."))
    return importlib.import_module("mbdsdr_ai." + path[len("mbdsdr_ai/"):-3].replace("/", "."))


def _attr_exists(mod, dotted: str) -> bool:
    obj = mod
    for part in dotted.split("."):
        if not hasattr(obj, part):
            return False
        obj = getattr(obj, part)
    return True


def _text_has(path: str, name: str) -> bool:
    text = (_ROOT / path).read_text(encoding="utf-8", errors="replace")
    return re.search(rf"\b{re.escape(name)}\b", text) is not None


def _segments(source: str):
    """按路径出现位置切段：返回 [(path, is_python, 该路径之后的文案)]。"""
    hits = list(ANY_PATH.finditer(source))
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(source)
        path = m.group(0)
        yield path, bool(PY_PATH.fullmatch(path)), source[m.end():end]


def _python_symbols(segment: str):
    found = [t for t in CAPS.findall(segment) if t not in NOISE]
    found += QUALIFIED.findall(segment)
    found += CALL.findall(segment)
    head = FIRST_TOKEN.match(segment)
    if head and head.group(1) not in NOISE:
        found.append(head.group(1))
    out = []
    for sym in found:
        if sym not in out:
            out.append(sym)
    return out


def _native_symbols(segment: str):
    out = []
    for sym in NATIVE_SYMBOL.findall(segment) + NATIVE_CALL.findall(segment):
        if sym not in NOISE and sym not in out:
            out.append(sym)
    return out


def _resolve_dotted_module(ref: str) -> bool:
    """`desktop.themes.DEFAULT_THEME` / `desktop/rf_sky_view.SYM` /
    `mbdsdr_ai.sdr_backend.RTLSDRBackend.SAMPLE_RATES` → 最长可 import 前缀拆解。"""
    if "/" in ref:
        root, rest = ref.split("/", 1)
        body = rest.replace("/", ".").split(".")
        prefix = "mbdsdr_ai." if root == "mbdsdr_ai" else ""
    else:
        parts = ref.split(".")
        if parts[0] == "desktop":
            body, prefix = parts[1:], ""
        else:
            body, prefix = parts, ""
    for cut in range(len(body) - 1, 0, -1):
        try:
            mod = importlib.import_module(prefix + ".".join(body[:cut]))
        except ImportError:
            continue
        return _attr_exists(mod, ".".join(body[cut:]))
    return False


def scan(items):
    """返回 (失败清单, 已核实条数, 无可判定符号的提及, 有引用的 item key 集合)。

    失败清单元素为 ``(kind, item_key, path_or_ref, symbol)``，
    kind ∈ {"py", "native", "dotted"}。
    """
    failures, prose, covered = [], [], set()
    verified = 0
    for item in items:
        source = (item.source or "").strip()
        if not source:
            continue
        claimed = False
        for path, is_py, segment in _segments(source):
            if HISTORICAL_NOTE in segment:
                continue
            symbols = _python_symbols(segment) if is_py else _native_symbols(segment)
            if not symbols:
                prose.append((item.key, path, segment.strip()[:70]))
                continue
            claimed = True
            kind = "py" if is_py else "native"
            if is_py:
                try:
                    mod = _module_of(path)
                except Exception as e:  # noqa: BLE001
                    failures.append((kind, item.key, path, f"import 失败：{e}"))
                    continue
                for sym in symbols:
                    if _attr_exists(mod, sym) or _text_has(path, sym.split(".")[-1]):
                        verified += 1
                    else:
                        failures.append((kind, item.key, path, sym))
            else:
                if not (_ROOT / path).exists():
                    failures.append((kind, item.key, path, "文件不存在"))
                    continue
                for sym in symbols:
                    if _text_has(path, sym):
                        verified += 1
                    else:
                        failures.append((kind, item.key, path, sym))
        for ref in DOTTED_MODULE.findall(source) + DOTTED_SLASH.findall(source):
            claimed = True
            if _resolve_dotted_module(ref):
                verified += 1
            else:
                failures.append(("dotted", item.key, ref, ref.rsplit(".", 1)[-1]))
        if claimed:
            covered.add(item.key)
        else:
            prose.append((item.key, "-", source[:70]))
    return failures, verified, prose, covered


def unverifiable_mentions():
    """没有可判定符号的提及——只列清单，不猜符号。"""
    return scan(ALL_ITEMS)[2]


# ---------------------------------------------------------------- 机器检查
def test_python_source_symbols_exist():
    failures, verified, _, _ = scan(ALL_ITEMS)
    broken = [f"{k}: {p} 没有符号 {s}" for kind, k, p, s in failures if kind == "py"]
    assert not broken, "source 引用了不存在的 Python 符号：\n" + "\n".join(broken)
    assert verified >= 25, f"只核实到 {verified} 条符号，抽取规则可能失效"


def test_native_source_symbols_exist():
    failures, _, _, _ = scan(ALL_ITEMS)
    broken = [f"{k}: {p} 没有符号 {s}" for kind, k, p, s in failures if kind == "native"]
    assert not broken, "source 引用了不存在的 C++/Dart 符号：\n" + "\n".join(broken)


def test_dotted_module_references_resolve():
    failures, _, _, _ = scan(ALL_ITEMS)
    broken = [f"{k}: 模块点写 {p} 解析不到符号" for kind, k, p, _s in failures
              if kind == "dotted"]
    assert not broken, "\n".join(broken)


def test_scan_covers_most_items():
    """抽取不能悄悄空转：多数 item 都应至少有一条可核实引用。"""
    _, _, _, covered = scan(ALL_ITEMS)
    assert len(covered) >= 30, (
        f"{len(ALL_ITEMS)} 个 item 里只有 {len(covered)} 条抽到了可核实符号，"
        "抽取规则需复查")


def test_historical_note_skips_prose_todo():
    """标了「历史说明，不参与机检」的段落不进散文清单；抹掉标记就回来。"""
    item = next(i for i in ALL_ITEMS if i.key == "record_dir")
    assert HISTORICAL_NOTE in item.source, "record_dir 的历史段落没写标记"
    assert all(k != "record_dir" for k, _p, _t in scan([item])[2]), \
        "标了历史说明仍被当成散文欠账"
    plain = replace(item, source=item.source.replace(HISTORICAL_NOTE, ""))
    assert any(k == "record_dir" for k, _p, _t in scan([plain])[2]), \
        "抹掉标记后仍不判散文：这条规则没有实际作用"


# ---------------------------------------------------------------- 变异反证
def test_mutation_makes_check_red_then_green():
    """任一处符号改成不存在的名字必须红；改回即绿。"""
    ok_item = next(i for i in ALL_ITEMS if "WF_DEPTH_CHOICES" in (i.source or ""))
    bad_item = replace(ok_item, source=ok_item.source.replace(
        "WF_DEPTH_CHOICES", "NO_SUCH_SYMBOL_XYZ"))
    failures, _, _, _ = scan([bad_item])
    assert any(s == "NO_SUCH_SYMBOL_XYZ" for _k, _key, _p, s in failures), \
        f"变异未被发现（检查形同虚设）：{failures}"
    assert scan([ok_item])[0] == [], "改回原符号后仍红"
