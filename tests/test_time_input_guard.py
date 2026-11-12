# SPDX-License-Identifier: MIT
"""时钟输入守卫：测试不得把裸系统时钟当作被测输入喂进去。

背景
====
同一决策链混用两个时间源（例如过境预测用模拟时刻、可见性用墙钟）会让
断言随日期漂移，CI 早晚随机红。根因通常不是生产代码，而是测试直接写
``time.time()`` / ``datetime.now()`` 作为被测入口的入参。

本守卫用 AST 扫 ``tests/`` 与 ``mbdsdr_ai/tests/``：

* 违规：裸时钟调用**直接出现在某次调用的实参里**（含算术/条件表达式），
  即它被当作被测输入。
* 允许：先赋值给局部变量（``now = time.time()``，测量/接线层），或整行
  带 ``# timeguard: allow`` 注释（仅限 NTP 校时、模拟时钟偏移这类
  「被测对象本身就是与墙钟比较」的用例）。

理论上限
========
静态守卫不是万能的：``now = time.time(); f(now)`` 这类先赋值再传入的写法
不会被判红（它属于「测量层」）。守卫的目标是挡住最常见的漂移来源，
配合「显式 ``start_unix`` / ``at_unix`` 注入」的测试约定使用。
"""
from __future__ import annotations

import ast
import os
import re
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_DIRS = ("tests", os.path.join("mbdsdr_ai", "tests"))
SELF = os.path.abspath(__file__)

ALLOW_MARKER = "timeguard: allow"

# 裸时钟：模块.函数 或 from-import 后的本地名
_CLOCK_ATTRS = {
    ("time", "time"),
    ("datetime", "now"),
    ("datetime", "utcnow"),
    ("datetime", "today"),
    # PySide6 的静态取时刻。按 (类名, 方法名) 精确匹配：``QDate.fromString`` /
    # ``QTime.fromString`` / ``QDateTime.fromSecsSinceEpoch`` 这类解析、换算
    # 调用不取当前时刻，不在表内，不会误伤。
    ("QDateTime", "currentDateTime"),
    ("QDateTime", "currentDateTimeUtc"),
    ("QDate", "currentDate"),
    ("QTime", "currentTime"),
    # 取「此刻」的 epoch 变体：同样是墙钟，只是返回类型不同。
    ("QDateTime", "currentSecsSinceEpoch"),
    ("QDateTime", "currentMSecsSinceEpoch"),
}

#: Qt 取时刻的类名：from-import 后是普通 Name（``QDateTime.currentDateTime()``）。
_QT_CLOCK_CLASSES = ("QDateTime", "QDate", "QTime")


def _module_files() -> list[str]:
    out: list[str] = []
    for rel in SCAN_DIRS:
        base = os.path.join(ROOT, rel)
        for dirpath, _dirnames, filenames in os.walk(base):
            for fn in filenames:
                if fn.endswith(".py") and fn.startswith("test_"):
                    p = os.path.join(dirpath, fn)
                    if os.path.abspath(p) != SELF:
                        out.append(p)
    return sorted(out)


def _imports(tree: ast.AST) -> tuple[set[str], set[str]]:
    """返回 (别名模块名, from-import 名)。"""
    mods: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.asname or a.name)
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                names.add(a.asname or a.name)
    return mods, names


#: 允许出现在链根的名字：模块名（含别名）、``datetime`` 模块、Qt 时间类。
_CLOCK_ROOTS = ("time", "datetime", "date") + _QT_CLOCK_CLASSES


def _attr_chain(node: ast.AST) -> list[str]:
    """``a.b.c`` → ["a", "b", "c"]；链根不是 Name 时返回空表（如 ``self.x``）。"""
    out: list[str] = []
    while isinstance(node, ast.Attribute):
        out.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        out.append(node.id)
        out.reverse()
        return out
    return []


def _is_clock_call(node: ast.Call, mods: set[str], names: set[str]) -> bool:
    f = node.func
    if isinstance(f, ast.Attribute):
        # 两段 mod.fn()、三段 mod.Cls.fn()（datetime.datetime.now()）、
        # 四段 pkg.mod.Cls.fn()（PySide6.QtCore.QDateTime.currentDateTime()）。
        # 旧实现把三段式判断嵌在「f.value 是 Name」的分支里，永远进不去——
        # 声称覆盖、实际一行也管不到（2026-11-12 修，见守卫契约测试）。
        chain = _attr_chain(f)
        if len(chain) < 2:
            return False
        if chain[0] not in mods and chain[0] not in _CLOCK_ROOTS:
            return False
        cls, attr = chain[-2], chain[-1]
        return (cls, attr) in _CLOCK_ATTRS or (cls == "date" and attr == "today")
    if isinstance(f, ast.Name) and f.id in names and f.id in ("now", "utcnow", "today"):
        return True
    if isinstance(f, ast.Name) and f.id == "time" and "time" in names:
        return True
    return False


def _ancestors(tree: ast.AST) -> dict[int, ast.AST]:
    parent: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[id(child)] = node
    return parent


_TRANSPARENT = (
    ast.BinOp, ast.UnaryOp, ast.IfExp, ast.Compare, ast.Attribute,
    ast.Subscript, ast.Starred, ast.JoinedStr, ast.FormattedValue,
    ast.List, ast.Tuple, ast.Dict, ast.Set,
)


def _is_feeding_call(call: ast.Call, parent: dict[int, ast.AST]) -> bool:
    """时钟调用是否最终落在某次调用的实参位置（被测输入）。"""
    cur: ast.AST = call
    while id(cur) in parent:
        p = parent[id(cur)]
        if isinstance(p, ast.keyword):
            return True          # 关键字实参本身
        if isinstance(p, _TRANSPARENT):
            cur = p
            continue
        if isinstance(p, ast.Call):
            if cur in list(p.args) + [kw.value for kw in p.keywords]:
                return True
            cur = p               # 方法链：本层 Call 可能是更高层调用的实参
            continue
        return False
    return False


def scan_source(path: str, source: str
                ) -> tuple[list[tuple[int, str]], list[tuple[int, str, str]]]:
    """扫一份源码，返回 (违规, 豁免)。

    豁免不静默跳过：连同理由一起返回，调用方必须打印出来（见扫描用例）。
    """
    tree = ast.parse(source, filename=path)
    mods, names = _imports(tree)
    parent = _ancestors(tree)
    lines = source.splitlines()
    bad: list[tuple[int, str]] = []
    exempt: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_clock_call(node, mods, names):
            continue
        if not _is_feeding_call(node, parent):
            continue
        line = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
        if ALLOW_MARKER in line:
            reason = line.split(ALLOW_MARKER, 1)[1].lstrip(" :—-").strip()
            exempt.append((node.lineno, line.strip(), reason or "（未写理由）"))
            continue
        bad.append((node.lineno, line.strip()))
    return bad, exempt


def violations_in_source(path: str, source: str) -> list[tuple[int, str]]:
    return scan_source(path, source)[0]


def test_no_bare_clock_as_test_input():
    bad: list[str] = []
    for path in _module_files():
        try:
            with open(path, encoding="utf-8-sig") as fh:
                src = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        hits, exempt = scan_source(path, src)
        rel = os.path.relpath(path, ROOT)
        for lineno, text, reason in exempt:
            # 豁免必须可见：pytest 默认捕获输出，故同时写进 warnings summary。
            warnings.warn(f"[timeguard] 豁免 {rel}:{lineno} — {reason}｜{text}",
                          stacklevel=1)
        for lineno, text in hits:
            bad.append(f"{rel}:{lineno}: {text}")
    assert not bad, (
        "测试把裸系统时钟当被测输入（请改为显式 start_unix/at_unix 注入，"
        "或确属与墙钟比较的接线层则加 `# timeguard: allow — 理由`）：\n  "
        + "\n  ".join(bad)
    )


def test_guard_detects_bare_clock_and_respects_marker():
    """守卫自身要有反证：违规样本必红，标记/赋值样本不红。"""
    bad_src = "import time\nf(time.time())\n"
    assert violations_in_source("bad.py", bad_src) == [(2, "f(time.time())")]

    bad_kw_src = (
        "from datetime import datetime\n"
        "g(timestamp=datetime.now().timestamp())\n"
    )
    hits = violations_in_source("bad_kw.py", bad_kw_src)
    assert hits and hits[0][0] == 2

    allowed_src = (
        "import time\n"
        "now = time.time()\n"
        "f(now)\n"
        "h(time.time())  # timeguard: allow\n"
    )
    assert violations_in_source("ok.py", allowed_src) == []


def test_qt_clock_attrs_are_covered():
    """Qt 的静态取时刻不能被漏掉（2026-11-12 补：此前三条全漏报）。

    按 (类名, 方法名) 精确匹配——``QDate.fromString`` / ``QTime.fromString``
    这类解析调用不取当前时刻，必须保持不报，否则守卫就成了「带 Qt 就报」的噪声。
    """
    pinned = {
        ("QDateTime", "currentDateTime"),
        ("QDateTime", "currentDateTimeUtc"),
        ("QDate", "currentDate"),
        ("QTime", "currentTime"),
        ("QDateTime", "currentSecsSinceEpoch"),
        ("QDateTime", "currentMSecsSinceEpoch"),
    }
    assert pinned <= _CLOCK_ATTRS

    src = (
        "from PySide6.QtCore import QDate, QDateTime, QTime\n"
        "f(QDateTime.currentDateTime())\n"
        "f(QDateTime.currentDateTimeUtc())\n"
        "f(QDate.currentDate())\n"
        "f(QTime.currentTime())\n"
        "f(QDateTime.currentSecsSinceEpoch())\n"
        "f(QDateTime.currentMSecsSinceEpoch())\n"
    )
    assert sorted(n for n, _ in violations_in_source("qt.py", src)) == [2, 3, 4, 5, 6, 7]

    parse_src = (
        "from PySide6.QtCore import QDate, QTime\n"
        "f(QDate.fromString('2026-11-12', 'yyyy-MM-dd'))\n"
        "f(QTime.fromString('12:00', 'HH:mm'))\n"
    )
    assert violations_in_source("qt_parse.py", parse_src) == []


def test_three_part_clock_call_is_detected():
    """三段式 ``datetime.datetime.now()`` 落在 feeding 位置必须报。

    回归钉扎：这段判断曾嵌在 ``isinstance(f.value, ast.Name)`` 分支内，永远
    进不去——注释声称覆盖三段式，实际一行也管不到。把 ``_attr_chain`` 退化成
    「只看 f.value 是不是 Name」即可复现该漏报（反证见提交说明）。
    """
    src = (
        "import datetime\n"
        "f(datetime.datetime.now())\n"
        "g(when=datetime.datetime.now(datetime.timezone.utc))\n"
        "h(datetime.date.today())\n"
        "k(datetime.datetime.utcnow())\n"
    )
    assert sorted(n for n, _ in violations_in_source("three.py", src)) == [2, 3, 4, 5]


def test_no_guard_marker_abuse():
    """标记只允许出现在「被测对象就是墙钟」的场景，防止随手豁免。"""
    pat = re.compile(r"#\s*timeguard: allow")
    offenders: list[str] = []
    for path in _module_files():
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pat.search(line):
                rel = os.path.relpath(path, ROOT)
                offenders.append(f"{rel}:{i}")
    # 白名单：NTP 校时 / 模拟时钟偏移 / TimeEngine 跟随系统时钟的对照 /
    # 调度任务需要一个「相对未来的 run_at」，断言不依赖具体时刻
    allowed_files = {
        os.path.join("mbdsdr_ai", "tests", "test_time_sync_behavior.py"),
        os.path.join("mbdsdr_ai", "tests", "test_time_engine.py"),
        os.path.join("tests", "test_full_integration.py"),
        os.path.join("tests", "test_full_integration_v2.py"),
        # 首次打开的「选位置不跳时间 / 流速循环后回到此刻」两条，
        # 被测对象就是引擎是否仍实时跟随墙钟，属设计允许的对照场景。
        os.path.join("tests", "test_first_open_welcome.py"),
        # TLE 新鲜度：被测对象就是「历元年龄 vs 墙钟」，生产侧 freshness()
        # 本身用墙钟（tle_integrity.py）。改用 TimeEngine 会造出生产侧不存在
        # 的口径；该值不参与指向/多普勒/录制对齐。
        os.path.join("tests", "test_tle_tracking_policy.py"),
    }
    misplaced = [o for o in offenders
                 if os.path.normpath(o.split(":")[0]) not in
                 {os.path.normpath(p) for p in allowed_files}]
    assert not misplaced, (
        "timeguard 豁免只允许出现在与墙钟本身比较的用例中：\n  "
        + "\n  ".join(misplaced))
