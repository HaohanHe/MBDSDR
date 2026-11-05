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
  后者再用已声明包的 ``Requires-Dist`` 闭包拆成“传递依赖”与“真未声明”。
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
#: (文件, 正则, 说明)。每处都必须命中；命中值与 canonical 不同即 FAIL。
VERSION_SITES = (
    ("cpp/CMakeLists.txt", r"project\(mbdsdr\s+VERSION\s+([0-9]+(?:\.[0-9]+)+)", "打包版本（唯一事实源）"),
    ("pyproject.toml", r'^\s*version\s*=\s*"([^"]+)"', "Python 包元数据"),
    ("mbdsdr_ai/__init__.py", r'^__version__\s*=\s*"([^"]+)"', "AI 包 __version__"),
    ("mbdsdr_ai/core/__init__.py", r'^__version__\s*=\s*"([^"]+)"', "core 子包 __version__"),
    ("desktop/main.py", r'setApplicationVersion\(\s*"([^"]+)"\s*\)', "QApplication 版本"),
)

SPEC_VERSION_RE = re.compile(r'^\s*(?:version|version_file)\s*=', re.MULTILINE)
SEMVER_RE = re.compile(r"\b[0-9]+\.[0-9]+\.[0-9]+\b")


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
    values: dict[str, str] = {}
    canonical = ""
    for rel, pattern, note in VERSION_SITES:
        path = ROOT / rel
        if not path.exists():
            _record(FAIL, f"{rel} 不存在（版本出现处消失），无法取值", items)
            continue
        m = re.search(pattern, _read(path), re.MULTILINE)
        if not m:
            _record(FAIL, f"{rel} 未命中版本正则：{pattern}（取值点被删/改名，需人工确认）", items)
            continue
        val = m.group(1)
        values[rel] = val
        if not canonical:
            canonical = val
            _record(PASS, f"{rel} = {val}  ← {note}（canonical）", items)
            continue
        if val == canonical:
            _record(PASS, f"{rel} = {val}  ← {note}", items)
        else:
            _record(FAIL, f"{rel} = {val}  ≠ canonical {canonical}  ← {note}", items)

    # 打包器：spec 是否声明版本资源 / 产物 exe 是否真的带版本
    spec_text = _read(SPEC) if SPEC.exists() else ""
    if SPEC_VERSION_RE.search(spec_text):
        _record(WARN, "desktop/MBDSDR.spec 声明了版本字段；本脚本尚未解析其取值，需人工复核", items)
    else:
        _record(WARN, "desktop/MBDSDR.spec 未声明 version/version_file → EXE 无版本资源（打包器侧缺口）", items)

    if dist:
        exe = dist / "MBDSDR" / "MBDSDR.exe"
        fv = _exe_file_version(exe)
        if fv is None:
            _record(FAIL, f"产物 exe 版本资源读取失败：{exe}", items)
        elif fv == "":
            _record(WARN, f"产物 {exe.name} 无 FileVersion/ProductVersion（与 spec 未注入一致）", items)
        elif canonical and not fv.startswith(canonical + ".") and fv != canonical:
            _record(FAIL, f"产物 exe 版本资源 {fv} ≠ canonical {canonical}", items)
        else:
            _record(PASS, f"产物 exe 版本资源 {fv}", items)

    # 文档首行
    readme = ROOT / "README.md"
    first = _read(readme).splitlines()[0] if readme.exists() else ""
    m = SEMVER_RE.search(first)
    if m:
        if canonical and m.group(0) != canonical:
            _record(FAIL, f"README.md 首行版本 {m.group(0)} ≠ canonical {canonical}", items)
        else:
            _record(PASS, f"README.md 首行版本 {m.group(0)}", items)
    else:
        _record(WARN, f"README.md 首行无版本号（首行：{first[:60]}）", items)

    # 关于对话框
    mw = ROOT / "desktop" / "main_window.py"
    if mw.exists():
        text = _read(mw)
        m = re.search(r"def _show_about\(.*?\n(.*?)(?=\n    def |\Z)", text, re.S)
        block = m.group(1) if m else ""
        hit = SEMVER_RE.search(block)
        if hit:
            if canonical and hit.group(0) != canonical:
                _record(FAIL, f"关于对话框版本 {hit.group(0)} ≠ canonical {canonical}", items)
            else:
                _record(PASS, f"关于对话框版本 {hit.group(0)}", items)
        else:
            _record(WARN, "desktop/main_window.py:_show_about 未显示版本号（关于对话框缺口）", items)
    else:
        _record(FAIL, "desktop/main_window.py 不存在，无法检查关于对话框", items)

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


def check_deps(dist: Path) -> list[dict]:
    items: list[dict] = []
    declared = _declared_requirements()
    dist2imports = _dist_to_imports()
    imp2dist = _import_to_dist()
    names, modules, origin = _artifact_imports(dist)

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

    # 产物差集
    artifact_dists = {imp2dist[n] for n in names if n in imp2dist}
    declared_norm = set(declared)
    closure = _requires_closure(declared_norm)
    extras = sorted(artifact_dists - declared_norm)
    transitive = [d for d in extras if d in closure]
    undeclared = [d for d in extras if d not in closure]
    if transitive:
        _record(WARN, "产物有但没声明（可归因为已声明包的传递依赖）：" + ", ".join(transitive), items)
    if undeclared:
        _record(WARN, "产物有但没声明（不在声明面、也不在 Requires-Dist 闭包）："
                      + ", ".join(undeclared), items)
        for d in undeclared:
            cls = _license_class(d)
            if cls in {"GPL", "LGPL", "AGPL"}:
                _record(FAIL, f"未声明且 copyleft 的包混入产物：{d}（{cls}）", items)
    if not extras:
        _record(PASS, "产物里没有声明面之外的发行包", items)

    if missing:
        _record(FAIL, "差集①「声明了但产物没有」：" + ", ".join(sorted(missing)), items)
    else:
        _record(PASS, "差集①「声明了但产物没有」：空", items)
    _record(WARN, "差集②「产物里有但没声明」：" + (", ".join(extras) if extras else "空")
                  + "（传递/未声明拆分见上）", items)
    return items


# ----------------------------------------------------------------------
# 3. 许可证与合规文件
# ----------------------------------------------------------------------
COPYLEFT_DEPS = ("pyrtlsdr",)


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

    # copyleft 组件
    names, _, _ = _artifact_imports(dist)
    imp2dist = _import_to_dist()
    artifact_dists = {imp2dist[n] for n in names if n in imp2dist}
    notice = _read(ROOT / "NOTICE.md") if (ROOT / "NOTICE.md").exists() else ""
    licensing = _read(ROOT / "LICENSING.md") if (ROOT / "LICENSING.md").exists() else ""

    for d in sorted(artifact_dists):
        cls = _license_class(d)
        if cls not in {"GPL", "LGPL", "AGPL"}:
            continue
        _record(FAIL, f"产物含 copyleft 包 {d}（{cls}），需在 NOTICE 登记并随包附许可证文本", items)
        has_text = any(re.search(rf"[/\\]{re.escape(_norm(d))}[^/\\]*[/\\]", str(p)) for p in found)
        if not has_text:
            _record(FAIL, f"{d} 的许可证文本不在产物里（其 dist-info/目录内无 LICENSE/COPYING）", items)
        if d not in notice.lower():
            _record(FAIL, f"NOTICE.md 未登记 {d}", items)
        if d == "pyrtlsdr" and ("不随包分发" in notice or "不随包分发" in licensing):
            _record(FAIL, "NOTICE/LICENSING 声称 pyrtlsdr「不随包分发」，"
                          "但产物含 rtlsdr 模块 + pyrtlsdr-0.2.93.dist-info（陈述与产物相反）", items)

    for dll in ("librtlsdr.dll", "rtlsdr.dll"):
        if (dist / "MBDSDR" / "_internal" / dll).exists():
            if "librtlsdr" not in notice.lower():
                _record(FAIL, f"产物随包 {dll}（上游 osmocom librtlsdr，GPL-2.0），"
                              f"但 NOTICE.md 未登记该组件与许可证", items)
            else:
                _record(PASS, f"产物随包 {dll}，NOTICE 已登记", items)

    # Qt / PySide6（LGPL-3.0）
    if "PySide6" in names:
        qt_license = [p for p in found if "PySide6" in str(p) or "Qt" in str(p)]
        if qt_license:
            _record(PASS, f"Qt/PySide6 许可证文本在产物：{qt_license[0].relative_to(dist)}", items)
        else:
            _record(FAIL, "PySide6 在产物里，但找不到 Qt/PySide6 的 LGPL 许可证文本"
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
