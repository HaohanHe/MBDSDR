#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""发布清单机检：版本号一致性 / 依赖 pin 存在性 / 许可证与合规文件 / 安装器。

四项都只读仓库与已构建产物，不改产品代码、不调阈值。任一项 FAIL → 退出码 1。

口径：
* **版本号**：以 ``cpp/CMakeLists.txt`` 的 ``project(mbdsdr VERSION x.y.z)``
  为打包单一事实源（``tests/test_pyproject_packaging.py`` 已钉死 pyproject 同号），
  其余出现处逐一取值比对；打包器/文档首行/关于对话框若有“确实没有版本号”
  的缺口，报 WARN（不是取值不一致），有取值且不同才 FAIL。
* **依赖**：声明面 = 根 ``requirements.txt`` + ``desktop/requirements.txt``
  的实际依赖行 + spec 里 ``collect_submodules``/datas 明确点名的包。
  产物定位 = ``<dist>/MBDSDR/_internal`` 顶层 + exe 内嵌 ``PYZ.pyz`` 模块表
  （纯 Python 包不在 ``_internal`` 里，这是 PyInstaller 6 onedir 的正常布局，
  只看 ``_internal`` 会假报缺失）。差集分「声明了但产物没有」「产物有但没声明」；
  后者只有两种状态：**已归因**（指得到 ``Requires-Dist`` 闭包，或指得到一条
  从项目/已声明包出发的 import 链，且在产物解释器里 import 成功）→ PASS；
  **未归因**（两种情况都指不到，或链上的包在产物里其实 import 不起来）→ FAIL。
  静态 xref 只解释「PyInstaller 为什么收集它」，不能单独作为可发布理由。
* **许可证**：复用 ``scripts/check_data_licenses.py`` 的既有 GPL 扫描结论
  （不另造一套规则）；再查产物里是否带项目 LICENSE/NOTICE、copyleft 组件
  （GPL/LGPL）的许可证文本与 NOTICE 登记。
* **安装器**：仓库若真存在 Inno Setup / NSIS / WiX 等安装器描述文件，则要求
  配套冒烟实现（本脚本发现安装器但无冒烟时 FAIL 提示补）；当前仓库没有安装器
  脚本，输出「无安装器，本条不适用」并给出证据（glob 结果 + 打包脚本自述），
  本条 SKIP，不算绿也不算红。

用法::

    python scripts/release_manifest.py
    python scripts/release_manifest.py --dist d:\\mbdsdr\\.tmp\\pyi-dist12
    python scripts/release_manifest.py --only version,deps --json
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import subprocess
import sys
from ctypes import wintypes
from importlib import metadata as im
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP_ROOT = ROOT.parent / ".tmp"
SPEC = ROOT / "desktop" / "MBDSDR.spec"
CHECK_LICENSE = ROOT / "scripts" / "check_data_licenses.py"

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"


# ----------------------------------------------------------------------
# 通用
# ----------------------------------------------------------------------
def _norm(name: str) -> str:
    """PEP 503 风格归一：``websocket-client`` / ``websocket_client`` 同键。"""
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _default_dist() -> Path:
    """取 ``.tmp`` 下编号最大的 ``pyi-distN``（与 release_check 的 build 步同口径）。"""
    best: tuple[int, Path] | None = None
    for p in TMP_ROOT.glob("pyi-dist*"):
        m = re.fullmatch(r"pyi-dist(\d+)", p.name)
        if not m or not p.is_dir():
            continue
        n = int(m.group(1))
        if best is None or n > best[0]:
            best = (n, p)
    if best is None:
        raise SystemExit(f"找不到已构建产物：{TMP_ROOT}\\pyi-distN（先跑 release_check 的 build 步）")
    return best[1]


def _record(status: str, text: str, items: list[dict]) -> None:
    items.append({"status": status, "text": text})


def _counts(items: list[dict]) -> dict[str, int]:
    out = {PASS: 0, WARN: 0, FAIL: 0, SKIP: 0}
    for it in items:
        out[it["status"]] += 1
    return out


def _status_of(items: list[dict]) -> str:
    c = _counts(items)
    if c[FAIL]:
        return FAIL
    if c[WARN]:
        return WARN
    if c[PASS]:
        return PASS
    return SKIP


# ----------------------------------------------------------------------
# 1. 版本号一致性
# ----------------------------------------------------------------------
#: 唯一手抄版本点。其余出现处必须从这里派生，不得再写死字面量。
VERSION_FILE = ROOT / "mbdsdr_ai" / "VERSION"
#: 版本字面量：排除 IP（192.168.4.1）与四段版本后缀。
VERSION_LITERAL_RE = re.compile(r"(?<![\d.])(\d+\.\d+\.\d+)(?![\d.])")
STRICT_VERSION_RE = re.compile(r"\d+\.\d+\.\d+")

#: (文件, [(必须命中的正则, 说明)], 用途)。任一处失配 → FAIL
#: （这是防止「删掉取值点」被当成变绿的唯一手段，不许降级为 WARN）。
VERSION_DERIVATIONS = (
    ("cpp/CMakeLists.txt", (
        (r"file\(\s*READ\s+[^\n]*mbdsdr_ai/VERSION[^\n]*\)", "从 mbdsdr_ai/VERSION 读版本"),
        (r"project\(\s*mbdsdr\s+VERSION\s+\$\{MBDSDR_VERSION(?:_NUMERIC)?\}", "project() 用派生变量"),
    ), "CMake 工程版本"),
    ("pyproject.toml", (
        (r'^\s*dynamic\s*=\s*\[[^\]]*"version"[^\]]*\]', "[project] version 为 dynamic"),
        (r"\[tool\.setuptools\.dynamic\]", "setuptools 动态版本段"),
        (r'version\s*=\s*\{\s*file\s*=\s*"mbdsdr_ai/VERSION"\s*\}', "版本取自 VERSION 文件"),
    ), "pyproject 元数据"),
    ("mbdsdr_ai/version.py", (
        (r'with_name\("VERSION"\)', "读取 VERSION 文件"),
        (r"__version__\s*=\s*_read_version\(\)", "对外导出 __version__"),
    ), "版本读取实现"),
    ("mbdsdr_ai/__init__.py", (
        (r"from\s+\.version\s+import\s+__version__", "从 version.py 派生"),
    ), "AI 包 __version__"),
    ("mbdsdr_ai/core/__init__.py", (
        (r"from\s+\.\.version\s+import\s+__version__", "从 version.py 派生"),
    ), "core 子包 __version__"),
    ("desktop/main.py", (
        (r"from\s+mbdsdr_ai\.version\s+import\s+__version__", "导入共享版本"),
        (r"setApplicationVersion\(\s*_app_version\s*\)", "QApplication 版本取共享值"),
    ), "QApplication 版本"),
    ("desktop/MBDSDR.spec", (
        (r"os\.path\.join\(root,\s*'mbdsdr_ai',\s*'VERSION'\)", "读取 VERSION 文件"),
        (r"version\s*=\s*VSVersionInfo\(", "EXE 版本资源"),
    ), "EXE FileVersion"),
    ("cpp/src/main.cpp", (
        (r'#include\s+"mbdsdr_version\.h"', "包含 CMake 生成的版本头"),
        (r"setApplicationVersion\(\s*QStringLiteral\(MBDSDR_VERSION\)\s*\)", "C++ QApplication 版本取生成宏"),
    ), "C++ 应用版本"),
    ("cpp/src/ui/about_dialog.cpp", (
        (r'#include\s+"mbdsdr_version\.h"', "包含 CMake 生成的版本头"),
        (r"版本:\s*%1.*MBDSDR_VERSION", "C++ 关于对话框版本取生成宏"),
    ), "C++ 关于对话框版本"),
    ("scripts/real_hw_acceptance.py", (
        (r'os\.path\.join\(REPO_ROOT,\s*"mbdsdr_ai",\s*"VERSION"\)', "读取 VERSION 文件"),
        (r"_portable_version\(\)", "便携包目录名取派生版本"),
    ), "真机验收便携包路径"),
)

#: 关于对话框：必须显示 QApplication 版本，且块内不得写死版本。
ABOUT_RE = re.compile(r"def _show_about\(.*?\n(.*?)(?=\n    def |\Z)", re.S)
ABOUT_VERSION_RE = re.compile(r"applicationVersion\(\)")



def _exe_file_version(exe: Path) -> str | None:
    """读 Windows PE 版本资源；返回 ``a.b.c.d``、``""``（无资源）、``None``（读失败）。"""
    if os.name != "nt" or not exe.exists():
        return None
    version = ctypes.WinDLL("version", use_last_error=True)

    class VS_FIXEDFILEINFO(ctypes.Structure):
        _fields_ = [
            ("dwSignature", wintypes.DWORD),
            ("dwStrucVersion", wintypes.DWORD),
            ("dwFileVersionMS", wintypes.DWORD),
            ("dwFileVersionLS", wintypes.DWORD),
        ]

    size = version.GetFileVersionInfoSizeW(str(exe), None)
    if not size:
        return ""
    buf = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(exe), 0, size, buf):
        return None
    ptr = ctypes.c_void_p()
    length = wintypes.UINT()
    if not version.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
        return None
    info = ctypes.cast(ptr, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
    ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
    if not (ms or ls):
        return ""
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def check_version(dist: Path | None) -> list[dict]:
    items: list[dict] = []
    if not VERSION_FILE.exists():
        _record(FAIL, f"缺少版本单一事实源 {VERSION_FILE.relative_to(ROOT)}", items)
        return items
    canonical = _read(VERSION_FILE).strip()
    if not STRICT_VERSION_RE.fullmatch(canonical):
        _record(FAIL, f"mbdsdr_ai/VERSION 内容不是 x.y.z：{canonical!r}", items)
        return items
    _record(PASS, f"canonical = {canonical}（mbdsdr_ai/VERSION，唯一手抄点）", items)

    # 各出现处：必须命中派生表达式；任一失配即 FAIL（不许改成正则软匹配）。
    for rel, rules, note in VERSION_DERIVATIONS:
        path = ROOT / rel
        if not path.exists():
            _record(FAIL, f"{rel} 不存在（版本派生点消失）", items)
            continue
        text = _read(path)
        missed = [desc for pat, desc in rules if not re.search(pat, text, re.MULTILINE)]
        if missed:
            _record(FAIL, f"{rel} 版本派生失配：未命中「{' / '.join(missed)}」← {note}"
                          f"（正则失配即 FAIL）", items)
            continue
        literals = [f"第 {i} 行 {m.group(1)}"
                    for i, line in enumerate(text.splitlines(), 1)
                    for m in [VERSION_LITERAL_RE.search(line)] if m]
        if literals:
            _record(FAIL, f"{rel} 写死版本字面量：{'；'.join(literals[:3])}"
                          f" ← {note}（版本只能来自 mbdsdr_ai/VERSION）", items)
        else:
            _record(PASS, f"{rel} 版本从 mbdsdr_ai/VERSION 派生 ← {note}", items)

    # 产物 exe：必须有 FileVersion，且与 canonical 一致（x.y.z 允许补 .0）。
    if dist:
        exe = dist / "MBDSDR" / "MBDSDR.exe"
        fv = _exe_file_version(exe)
        if fv is None:
            _record(FAIL, f"产物 exe 版本资源读取失败：{exe}", items)
        elif fv == "":
            _record(FAIL, f"产物 {exe.name} 无 FileVersion/ProductVersion（spec 版本资源未生效）", items)
        elif fv != canonical and not fv.startswith(canonical + "."):
            _record(FAIL, f"产物 exe 版本资源 {fv} ≠ canonical {canonical}", items)
        else:
            _record(PASS, f"产物 exe 版本资源 {fv}", items)

    # 文档首行
    readme = ROOT / "README.md"
    first = _read(readme).splitlines()[0] if readme.exists() else ""
    m = VERSION_LITERAL_RE.search(first)
    if m:
        if m.group(1) != canonical:
            _record(FAIL, f"README.md 首行版本 {m.group(1)} ≠ canonical {canonical}", items)
        else:
            _record(PASS, f"README.md 首行版本 {m.group(1)}", items)
    else:
        _record(FAIL, f"README.md 首行无版本号（首行：{first[:60]}）", items)

    # 关于对话框：必须显示 QApplication 版本，块内不得写死版本
    mw = ROOT / "desktop" / "main_window.py"
    if not mw.exists():
        _record(FAIL, "desktop/main_window.py 不存在，无法检查关于对话框", items)
    else:
        text = _read(mw)
        block_m = ABOUT_RE.search(text)
        block = block_m.group(1) if block_m else ""
        if not block_m:
            _record(FAIL, "desktop/main_window.py 找不到 _show_about（关于对话框检查点消失）", items)
        elif not ABOUT_VERSION_RE.search(block):
            _record(FAIL, "desktop/main_window.py:_show_about 未显示 QApplication.applicationVersion()", items)
        elif VERSION_LITERAL_RE.search(block):
            _record(FAIL, "desktop/main_window.py:_show_about 写死版本字面量"
                          f"（{VERSION_LITERAL_RE.search(block).group(1)}），应由 applicationVersion() 提供", items)
        else:
            _record(PASS, "关于对话框显示 QApplication.applicationVersion()（无写死版本）", items)

    # 安装器脚本
    installers = _installer_files()
    if installers:
        _record(WARN, f"安装器描述文件 {len(installers)} 个；版本号需在安装器冒烟里单独核对", items)
    else:
        _record(SKIP, "无安装器脚本（本条不适用，见第 4 项证据）", items)

    return items


# ----------------------------------------------------------------------
# 2. 依赖 pin 存在性
# ----------------------------------------------------------------------
def _parse_requirements(path: Path) -> dict[str, str]:
    """返回 {归一发行名: 原始声明行}，忽略注释与空行。"""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in _read(path).splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~\[;@\s]", line, maxsplit=1)[0].strip()
        if name:
            out[_norm(name)] = line
    return out


def _declared_requirements() -> dict[str, str]:
    merged = _parse_requirements(ROOT / "requirements.txt")
    merged.update(_parse_requirements(ROOT / "desktop" / "requirements.txt"))
    return merged


def _dist_to_imports() -> dict[str, list[str]]:
    """发行名 → 顶层 import 名（来自当前构建环境的 packages_distributions）。"""
    out: dict[str, list[str]] = {}
    for imp_name, dists in im.packages_distributions().items():
        for d in dists:
            out.setdefault(_norm(d), []).append(imp_name)
    return out


def _import_to_dist() -> dict[str, str]:
    """顶层 import 名 → 发行名（同一 import 名只取主发行包）。"""
    out: dict[str, str] = {}
    for imp_name, dists in im.packages_distributions().items():
        if dists:
            out[imp_name] = _norm(sorted(dists)[0])
    return out


def _artifact_imports(dist: Path) -> tuple[set[str], set[str], dict[str, list[str]]]:
    """产物里的顶层 import 名 + 全部模块名 + 来源说明。"""
    internal = dist / "MBDSDR" / "_internal"
    exe = dist / "MBDSDR" / "MBDSDR.exe"
    if not internal.is_dir() or not exe.exists():
        raise RuntimeError(f"产物结构不完整：{internal} 或 {exe} 不存在")
    names: set[str] = set()
    modules: set[str] = set()
    origin: dict[str, list[str]] = {}
    for p in internal.iterdir():
        if p.name == "__pycache__":
            continue
        if p.is_dir():
            names.add(p.name)
            origin.setdefault(p.name, []).append("_internal/")
        elif p.suffix.lower() in {".pyd", ".dll"}:
            names.add(p.stem)
            origin.setdefault(p.stem, []).append("_internal/")
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError as exc:  # pragma: no cover - 构建环境必有
        raise RuntimeError(f"需要 PyInstaller 读取内嵌 PYZ：{exc}") from exc
    pyz = CArchiveReader(str(exe)).open_embedded_archive("PYZ.pyz")
    for mod in pyz.toc:
        modules.add(mod)
        top = mod.split(".")[0]
        names.add(top)
        origin.setdefault(top, []).append("PYZ.pyz")
    return names, modules, origin


def _spec_modules() -> tuple[list[str], list[str]]:
    """spec 明确点名的模块与数据项（collect_submodules / datas 里的字面量）。"""
    text = _read(SPEC) if SPEC.exists() else ""
    mods = re.findall(r"collect_submodules\(\s*'([^']+)'\s*\)", text)
    mods += [f"{a}.{b}" for a, b in
             re.findall(r"_collect_namespace_submodules\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)", text)]
    datas = re.findall(r"\('assets'\s*,\s*'assets'\)", text)
    data_items = ["_internal/assets"] if datas else []
    if "astronomy.py" in text:
        data_items.append("_internal/mbdsdr_ai/astronomy.py")
    return mods, data_items


def _requires_closure(seed: set[str]) -> set[str]:
    """已声明包的 Requires-Dist 闭包（跳过 extra 专属依赖）。"""
    seen = set(seed)
    queue = list(seed)
    while queue:
        d = queue.pop()
        try:
            reqs = im.requires(d) or []
        except im.PackageNotFoundError:
            continue
        for req in reqs:
            head, _, marker = req.partition(";")
            if "extra" in marker:
                continue
            name = re.split(r"[<>=!~\[;@\s]", head.strip(), maxsplit=1)[0]
            if not name:
                continue
            n = _norm(name)
            if n not in seen:
                seen.add(n)
                queue.append(n)
    return seen


def _license_class(dist: str) -> str:
    """只认 license 字段与 ``License ::`` classifier，不做全文子串匹配。"""
    try:
        meta = im.metadata(dist)
    except im.PackageNotFoundError:
        return "unknown"
    expr = meta.get("License-Expression")
    classifiers = [c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    if expr:
        blob = expr
    elif classifiers:
        blob = " ".join(classifiers)
    else:
        blob = meta.get("License") or ""
    if re.search(r"\bAGPL|Affero", blob, re.I):
        return "AGPL"
    if re.search(r"\bLGPL|Lesser General Public", blob, re.I):
        return "LGPL"
    if re.search(r"\bGPL|GNU General Public License", blob, re.I):
        return "GPL"
    return "permissive/unknown"


TEST_FRAMEWORK = ("pytest", "_pytest", "pluggy", "iniconfig")


def _test_framework_hits(dist: Path, names: set[str], modules: set[str]) -> list[str]:
    """测试框架及其闭包在产物里的证据：顶层模块 / PYZ 模块 / dist-info 目录。"""
    hits: list[str] = []
    for needle in TEST_FRAMEWORK:
        if needle in names:
            hits.append(f"顶层 {needle}")
        if any(m == needle or m.startswith(needle + ".") for m in modules):
            hits.append(f"PYZ 模块 {needle}.*")
    internal = dist / "MBDSDR" / "_internal"
    if internal.is_dir():
        for p in internal.iterdir():
            low = p.name.lower()
            if any(low == n or low.startswith(n + ".") or low.startswith(n + "-")
                   for n in TEST_FRAMEWORK):
                hits.append(f"_internal/{p.name}")
    return sorted(set(hits))


def _xref_reverse(dist: Path) -> dict[str, set[str]] | None:
    """从 PyInstaller xref HTML 建「被谁 import」反向边；找不到构建目录则返回 None。

    xref 的每个节点同时有 `imports:` 与 `imported by:` 两段，两段的 href 含义相反；
    只取 `imports:` 段建正向边再反转，混读会把链走成假路径。
    """
    m = re.fullmatch(r"pyi-dist(\d+)", dist.name)
    if not m:
        return None
    xref = TMP_ROOT / f"pyi-build{m.group(1)}" / "MBDSDR" / "xref-MBDSDR.html"
    if not xref.exists():
        return None
    text = xref.read_text(encoding="utf-8", errors="replace")
    reverse: dict[str, set[str]] = {}
    for block in text.split('<div class="node">')[1:]:
        anchor = re.search(r'<a name="([^"]+)"></a>', block)
        if not anchor:
            continue
        src = anchor.group(1)
        imports = re.search(r"imports:(.*?)(?:imported by:|$)", block, re.S)
        if not imports:
            continue
        for dep in re.findall(r'href="#([^"]+)"', imports.group(1)):
            reverse.setdefault(dep, set()).add(src)
    return reverse


def _chain_for(imp: str, reverse: dict[str, set[str]] | None, roots: set[str],
               max_depth: int = 12) -> list[str]:
    """从产物里的 import 名向上走，找一条到达项目/已声明顶层包的 import 链。"""
    if not reverse:
        return []
    queue: list[tuple[str, list[str], int]] = [(imp, [imp], 0)]
    seen = {imp}
    expanded = 0
    while queue and expanded < 4000:
        node, path, depth = queue.pop(0)
        expanded += 1
        if node != imp and node.split(".")[0] in roots:
            return list(reversed(path))  # 根 → … → imp（import 方向）
        if depth >= max_depth:
            continue
        parents = sorted(reverse.get(node, ()))
        # 外部上游优先，但不能丢掉包内父子关系（openpyxl ← pandas.io.excel.*
        # ← pandas ← matplotlib.pyplot ← mbdsdr_ai 这类链全靠包内节点接起来）。
        external = [p for p in parents if p.split(".")[0] != node.split(".")[0]]
        internal = [p for p in parents if p.split(".")[0] == node.split(".")[0]]
        for parent in (external + internal)[:24]:
            if parent in seen:
                continue
            seen.add(parent)
            queue.append((parent, path + [parent], depth + 1))
    return []


def _runtime_import_probe(dist: Path, modules: list[str]) -> tuple[dict[str, bool], str]:
    """在产物解释器里逐个 import，给静态归因补运行时证据。

    返回 ``({模块: 是否导入成功}, 失败原因)``；产物不支持 ``--import-probe``
    （旧产物或没有该入口）时返回空结果 + 原因，调用方据此判 FAIL，不静默放行。
    """
    if not modules:
        return {}, ""
    exe = dist / "MBDSDR" / "MBDSDR.exe"
    if not exe.exists():
        return {}, f"产物 exe 不存在：{exe}"
    try:
        proc = subprocess.run(
            [str(exe), "--import-probe", ",".join(modules)],
            cwd=str(dist), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {}, f"运行产物解释器失败：{type(exc).__name__}: {exc}"
    out = (proc.stdout or "") + (proc.stderr or "")
    results: dict[str, bool] = {}
    for line in out.splitlines():
        m = re.match(r"\[probe\] (OK|FAIL) (\S+)", line.strip())
        if m:
            results[m.group(2)] = m.group(1) == "OK"
    if not results:
        if "unrecognized arguments" in out:
            return {}, "产物不支持 --import-probe（旧产物，需重打）"
        tail = " | ".join(out.strip().splitlines()[-2:]) if out.strip() else "(无输出)"
        return {}, (f"产物未输出探针结果（exit={proc.returncode}）：{tail[:200]}")
    return results, ""


def check_deps(dist: Path) -> list[dict]:
    items: list[dict] = []
    declared = _declared_requirements()
    dist2imports = _dist_to_imports()
    imp2dist = _import_to_dist()
    names, modules, origin = _artifact_imports(dist)

    # 硬断言：测试框架及其闭包不得进入产物（任一命中即 FAIL）。
    test_hits = _test_framework_hits(dist, names, modules)
    if test_hits:
        _record(FAIL, "产物泄漏测试框架：" + "；".join(test_hits), items)
    else:
        _record(PASS, "产物不含 pytest/_pytest/pluggy/iniconfig"
                      "（顶层模块、PYZ、dist-info 三处均无）", items)

    # 声明面（requirements + spec）
    spec_mods, spec_datas = _spec_modules()
    _record(PASS, f"声明面：根/desktop requirements 共 {len(declared)} 个发行包；"
                  f"spec 点名模块 {spec_mods or '（无）'}；数据项 {spec_datas or '（无）'}", items)

    missing: list[str] = []
    for dist_name, line in sorted(declared.items()):
        cand = [i for i in dist2imports.get(dist_name, []) if i in names]
        if cand:
            _record(PASS, f"{dist_name}  ← 「{line}」→ 产物 {cand[0]}"
                          f"（{'/'.join(sorted(set(origin[cand[0]])))}）", items)
        else:
            expected = dist2imports.get(dist_name, [dist_name.replace("-", "_")])
            missing.append(dist_name)
            _record(FAIL, f"{dist_name}  ← 「{line}」→ 未找到（期望 import 名 {expected}，"
                          f"_internal 与 PYZ 均无）", items)

    # spec 点名模块
    for mod in spec_mods:
        if "." in mod:
            count = sum(1 for m in modules if m.startswith(mod + "."))
            ok = count > 0
        else:
            count = 0
            ok = mod in names
        if ok:
            _record(PASS, f"spec collect_submodules('{mod}') → 产物有 {mod}"
                          + (f"（{count} 个模块）" if count else ""), items)
        else:
            _record(FAIL, f"spec collect_submodules('{mod}') → 产物没有 {mod}", items)
    for item in spec_datas:
        p = dist / "MBDSDR" / item
        if p.exists():
            _record(PASS, f"spec datas → {item}", items)
        else:
            _record(FAIL, f"spec datas → 产物缺 {item}", items)

    # 产物差集：每个额外包都要能说清「为什么在产物里」。
    # 两种状态：已归因（Requires-Dist 闭包，或 import 链 + 产物解释器 import 成功）
    # → PASS；未归因（指不到上游，或链上的包在产物里根本 import 不起来）→ FAIL。
    artifact_dists = {imp2dist[n] for n in names if n in imp2dist}
    declared_norm = set(declared)
    root_closures = {d: _requires_closure({d}) for d in declared_norm}
    roots = {i.split(".")[0] for d in declared_norm for i in dist2imports.get(d, [])}
    roots |= {"mbdsdr_ai", "main"}
    reverse = _xref_reverse(dist)
    extras = sorted(artifact_dists - declared_norm)
    transitive: list[str] = []
    chain_attributed: list[str] = []
    unattributed: list[str] = []

    # 运行时证据：一次进程导入所有额外包的 import 名（静态 xref 不能单独作证）。
    probes: dict[str, bool] = {}
    probe_error = ""
    if extras:
        wanted: list[str] = []
        for d in extras:
            for imp_name in sorted(dist2imports.get(d, [])):
                if imp_name in names and imp_name not in wanted:
                    wanted.append(imp_name)
        probes, probe_error = _runtime_import_probe(dist, wanted)

    for d in extras:
        owners = sorted(r for r, c in root_closures.items() if d in c and r != d)
        if owners:
            transitive.append(d)
            _record(PASS, f"差集② {d}：已归因（Requires-Dist 传递依赖 ← {', '.join(owners)}）", items)
            continue
        chat: list[str] = []
        for imp_name in dist2imports.get(d, []):
            chat = _chain_for(imp_name, reverse, roots)
            if chat:
                break
        if not chat:
            unattributed.append(d)
            _record(FAIL, f"差集② {d}：未归因（不在 Requires-Dist 闭包，xref 也没有给出上游；"
                          f"需人工确认它是被谁带入产物的）", items)
            cls = _license_class(d)
            if cls in {"GPL", "LGPL", "AGPL"}:
                _record(FAIL, f"未归因且 copyleft 的包混入产物：{d}（{cls}）", items)
            continue
        d_names = [n for n in sorted(dist2imports.get(d, [])) if n in names]
        if d_names and all(probes.get(n) is True for n in d_names):
            chain_attributed.append(d)
            _record(PASS, f"差集② {d}：已归因（import 链 {' → '.join(chat)}；"
                          f"产物解释器 import {', '.join(d_names)} 成功）", items)
        elif probe_error:
            unattributed.append(d)
            _record(FAIL, f"差集② {d}：静态 import 链 {' → '.join(chat)} 无法用运行时证据确认"
                          f"（{probe_error}）", items)
        else:
            bad = [n for n in d_names if not probes.get(n)] or ["(无已知 import 名)"]
            unattributed.append(d)
            _record(FAIL, f"差集② {d}：链 {' → '.join(chat)} 指到它，但产物解释器 import "
                          f"{', '.join(bad)} 失败", items)
    if not extras:
        _record(PASS, "产物里没有声明面之外的发行包", items)

    if missing:
        _record(FAIL, "差集①「声明了但产物没有」：" + ", ".join(sorted(missing)), items)
    else:
        _record(PASS, "差集①「声明了但产物没有」：空", items)
    if not extras:
        pass
    elif unattributed:
        _record(FAIL, f"差集②「产物里有但没声明」：{len(extras)} 个，"
                      f"已归因 {len(transitive) + len(chain_attributed)}"
                      f" / 未归因 {len(unattributed)}（未归因逐包见上，不可发布）", items)
    else:
        _record(PASS, f"差集②「产物里有但没声明」：{len(extras)} 个全部已归因"
                      f"（Requires-Dist 传递 {len(transitive)} / import 链 {len(chain_attributed)}，"
                      f"运行时 import 全部成功）", items)
    return items


# ----------------------------------------------------------------------
# 3. 许可证与合规文件
# ----------------------------------------------------------------------
#: 逐包许可证全文映射（产物内 `third_party_licenses/` 下）。
PACKAGE_TEXT = {
    "pyrtlsdr": "GPL-3.0.txt",
    "pyside6": "LGPL-3.0.txt",
    "shiboken6": "LGPL-3.0.txt",
}
COPYLEFT_TEXTS = {"GPL": "GPL-3.0.txt", "LGPL": "LGPL-3.0.txt", "AGPL": "AGPL-3.0.txt"}
THIRD_PARTY_TEXTS = ("GPL-2.0.txt", "GPL-3.0.txt", "LGPL-3.0.txt")


def _artifact_text(dist: Path, name: str) -> Path | None:
    for cand in (dist / "MBDSDR" / name,
                 dist / "MBDSDR" / "_internal" / name,
                 dist / "MBDSDR" / "third_party_licenses" / name,
                 dist / "MBDSDR" / "_internal" / "third_party_licenses" / name):
        if cand.is_file():
            return cand
    return None


def _artifact_license_files(dist: Path) -> list[Path]:
    base = dist / "MBDSDR"
    out: list[Path] = []
    for p in base.rglob("*"):
        if p.is_file() and re.fullmatch(r"(LICEN[CS]E|COPYING|NOTICE)(\..*)?", p.name, re.I):
            out.append(p)
    return out


def check_license(dist: Path) -> list[dict]:
    items: list[dict] = []

    # 复用既有数据 GPL 扫描（不另造规则）
    if not CHECK_LICENSE.exists():
        _record(FAIL, f"既有 GPL 扫描脚本不存在：{CHECK_LICENSE}", items)
    else:
        proc = subprocess.run([sys.executable, str(CHECK_LICENSE), "--check"],
                              cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
        out = (proc.stdout + proc.stderr).strip().splitlines()
        tail = " | ".join(out[-2:]) if out else "(无输出)"
        if proc.returncode == 0:
            _record(PASS, f"仓库数据 GPL 扫描（check_data_licenses.py）：{tail}", items)
        else:
            _record(FAIL, f"仓库数据 GPL 扫描红：{tail}", items)

    # 仓库侧合规文件
    for rel in ("LICENSE", "NOTICE.md", "LICENSING.md"):
        p = ROOT / rel
        _record(PASS if p.exists() else FAIL,
                f"仓库 {rel} {'在位' if p.exists() else '缺失'}", items)

    # 产物侧项目 LICENSE/NOTICE（只认包根/内置根，不把第三方 wheel 自带的 LICENSE 当项目声明）
    found = _artifact_license_files(dist)
    rel_found = [str(p.relative_to(dist)) for p in found]
    for need in ("LICENSE", "NOTICE.md"):
        candidates = (dist / "MBDSDR" / need, dist / "MBDSDR" / "_internal" / need)
        hit = next((p for p in candidates if p.exists()), None)
        if hit:
            _record(PASS, f"产物里有项目 {need}：{hit.relative_to(dist)}", items)
        else:
            _record(FAIL, f"产物缺项目自身 {need}（构建未把它打进包根/_internal；"
                          f"产物现有许可证类文件 {len(rel_found)} 个，均为第三方 wheel 自带）", items)

    # 第三方许可证全文（仓库 + 产物）
    for name in THIRD_PARTY_TEXTS:
        repo_p = ROOT / "third_party_licenses" / name
        _record(PASS if repo_p.exists() else FAIL,
                f"仓库 third_party_licenses/{name} {'在位' if repo_p.exists() else '缺失'}", items)
        art = _artifact_text(dist, name)
        if art:
            _record(PASS, f"产物随包许可证全文：{art.relative_to(dist)}", items)
        else:
            _record(FAIL, f"产物缺 third_party_licenses/{name}（LGPL/GPL 要求随分发附全文）", items)

    # copyleft 组件：逐项登记 + 许可证全文
    names, _, _ = _artifact_imports(dist)
    imp2dist = _import_to_dist()
    artifact_dists = {imp2dist[n] for n in names if n in imp2dist}
    notice = _read(ROOT / "NOTICE.md") if (ROOT / "NOTICE.md").exists() else ""
    licensing = _read(ROOT / "LICENSING.md") if (ROOT / "LICENSING.md").exists() else ""

    for d in sorted(artifact_dists):
        cls = _license_class(d)
        if cls not in {"GPL", "LGPL", "AGPL"}:
            continue
        if d in notice.lower():
            _record(PASS, f"NOTICE.md 已登记 copyleft 包 {d}（{cls}）", items)
        else:
            _record(FAIL, f"NOTICE.md 未登记 copyleft 包 {d}（{cls}）", items)
        text_name = PACKAGE_TEXT.get(d) or COPYLEFT_TEXTS.get(cls)
        hit = _artifact_text(dist, text_name) if text_name else None
        if hit:
            _record(PASS, f"{d} 许可证全文随包：{hit.relative_to(dist)}", items)
        else:
            _record(FAIL, f"{d}（{cls}）许可证全文不在产物（期望 third_party_licenses/{text_name}）", items)

    # pyrtlsdr 陈述必须是事实（随包），不能写「不随包 / 用户自装」
    py_lines = [ln for ln in (notice + "\n" + licensing).splitlines()
                if "pyrtlsdr" in ln.lower()]
    bad_py = [ln for ln in py_lines if re.search(r"不随包|用户自装|用户自行安装", ln)]
    if bad_py:
        _record(FAIL, "NOTICE/LICENSING 仍声称 pyrtlsdr「不随包 / 用户自装」，"
                      f"与产物事实相反：{bad_py[0].strip()}", items)
    elif "pyrtlsdr" in notice.lower() and "0.2.93" in notice and "rtlsdr" in notice.lower():
        _record(PASS, "NOTICE 的 pyrtlsdr 陈述与产物一致（0.2.93 随包 / rtlsdr 模块）", items)
    else:
        _record(FAIL, "NOTICE 未按事实登记 pyrtlsdr 0.2.93（随包分发 + rtlsdr 模块）", items)

    for dll in ("librtlsdr.dll", "rtlsdr.dll"):
        if (dist / "MBDSDR" / "_internal" / dll).exists():
            if "librtlsdr" not in notice.lower():
                _record(FAIL, f"产物随包 {dll}（osmocom librtlsdr，GPL-2.0），"
                              f"但 NOTICE.md 未登记该组件与许可证", items)
            elif not _artifact_text(dist, "GPL-2.0.txt"):
                _record(FAIL, f"产物随包 {dll}（GPL-2.0），但产物内没有 GPL-2.0 全文", items)
            else:
                _record(PASS, f"产物随包 {dll}，NOTICE + GPL-2.0 全文齐备", items)
        else:
            _record(FAIL, f"产物缺 {dll}（pyrtlsdr 的底层驱动；若构建刻意不带，需改 NOTICE）", items)

    # Qt / PySide6（LGPL-3.0）：许可证全文 + NOTICE 登记
    if "PySide6" in names:
        if _artifact_text(dist, "LGPL-3.0.txt"):
            _record(PASS, "PySide6/shiboken6 的 LGPL-3.0 全文随包", items)
        else:
            _record(FAIL, "PySide6 在产物里，但产物没有 LGPL-3.0 全文"
                          "（LGPL 要求随分发附许可证与可重新链接说明）", items)

    # 数据署名：产物里的 CC BY-SA 数据自带 meta 就不算缺
    data_dir = dist / "MBDSDR" / "_internal" / "mbdsdr_ai" / "data"
    if data_dir.is_dir():
        cc_files = []
        for p in data_dir.glob("*.json"):
            try:
                doc = json.loads(p.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            meta = doc.get("meta") if isinstance(doc, dict) else None
            if isinstance(meta, dict) and "CC BY-SA" in str(meta.get("source_license", "")).upper():
                cc_files.append((p.name, meta.get("attribution")))
        if cc_files:
            missing_attr = [n for n, a in cc_files if not a]
            if missing_attr:
                _record(WARN, "CC BY-SA 数据缺 attribution 字段：" + ", ".join(missing_attr), items)
            else:
                _record(PASS, f"CC BY-SA 数据自带 attribution（{len(cc_files)} 个文件）", items)

    return items


# ----------------------------------------------------------------------
# 4. 安装器冒烟
# ----------------------------------------------------------------------
INSTALLER_GLOBS = ("*.iss", "*.nsi", "*.wxs", "*.msix", "*.appxmanifest", "*.pkgproj")


def _installer_files() -> list[Path]:
    skip = {".git", "build", ".tmp", "__pycache__", "node_modules", ".pytest_cache"}
    out: list[Path] = []
    for pattern in INSTALLER_GLOBS:
        for p in ROOT.rglob(pattern):
            if any(part in skip for part in p.relative_to(ROOT).parts):
                continue
            out.append(p)
    return out


def check_installer() -> list[dict]:
    items: list[dict] = []
    found = _installer_files()
    if found:
        _record(FAIL, "发现安装器描述文件：" + ", ".join(str(p.relative_to(ROOT)) for p in found)
                      + "；但本脚本没有对应的安装/启动/卸载冒烟实现，发布前必须补齐", items)
        return items

    # 无安装器：给证据，避免“没找到”被当成“没查”
    pkg = ROOT / "scripts" / "package_windows.ps1"
    bat = ROOT / "desktop" / "build_windows.bat"
    evidence = []
    if pkg.exists():
        text = _read(pkg)
        m = re.search(r"诚实口径：本脚本只做.*?不做安装器（无注册表/快捷方式）", text)
        if m:
            evidence.append(f"scripts/package_windows.ps1 自述：{m.group(0)}")
        evidence.append("scripts/package_windows.ps1 产物是 zip 便携包 + SHA256，无安装/卸载入口")
    if bat.exists():
        text = _read(bat)
        evidence.append("desktop/build_windows.bat 只调 pyinstaller 出 onedir 目录，无 install/uninstall 目标")
    _record(SKIP, "无安装器，本条不适用（glob 未命中 " + "/".join(INSTALLER_GLOBS) + "）", items)
    for e in evidence:
        _record(SKIP, e, items)
    _record(SKIP, "替代覆盖：scripts/release_check.py 的 smoke 步已验证「便携包解压后启动到窗口出现」；"
                  "卸载残留无对象可查（没有安装器写注册表/快捷方式）", items)
    return items


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------
CHECKS = {
    "version": lambda dist: check_version(dist),
    "deps": lambda dist: check_deps(dist),
    "license": lambda dist: check_license(dist),
    "installer": lambda dist: check_installer(),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dist", type=Path, default=None,
                    help="PyInstaller onedir 产物目录（默认取 .tmp 下编号最大的 pyi-distN）")
    ap.add_argument("--only", default="version,deps,license,installer",
                    help="逗号分隔：" + ",".join(CHECKS))
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args(argv)

    selected = [s.strip() for s in args.only.split(",") if s.strip()]
    unknown = [s for s in selected if s not in CHECKS]
    if unknown:
        ap.error(f"未知检查项：{unknown}（可选：{list(CHECKS)}）")

    try:
        dist = args.dist.resolve() if args.dist else _default_dist()
    except SystemExit as exc:
        print(exc)
        return 1

    report: dict = {"dist": str(dist), "checks": []}
    all_items: list[dict] = []
    human: list[str] = []
    labels = {
        "version": "1/4 版本号一致性",
        "deps": "2/4 依赖 pin 存在性",
        "license": "3/4 许可证与合规文件",
        "installer": "4/4 安装器冒烟",
    }
    for name in selected:
        human.append(f"\n== {labels[name]} ==")
        try:
            items = CHECKS[name](dist)
        except Exception as exc:  # 机检自身失败也要明确红，不静默
            items = [{"status": FAIL, "text": f"检查执行失败：{type(exc).__name__}: {exc}"}]
        for it in items:
            human.append(f"  [{it['status']}] {it['text']}")
        status = _status_of(items)
        human.append(f"  -> {status}")
        report["checks"].append({"name": name, "status": status, "items": items})
        all_items.extend(items)

    c = _counts(all_items)
    overall = FAIL if c[FAIL] else (WARN if c[WARN] else (PASS if c[PASS] else SKIP))
    human.append(f"\n== 总判 == FAIL={c[FAIL]} WARN={c[WARN]} PASS={c[PASS]} SKIP={c[SKIP]} -> {overall}")
    human.append(f"产物：{dist}")
    report["summary"] = {"status": overall, **c}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("\n".join(human))
    return 1 if overall == FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
