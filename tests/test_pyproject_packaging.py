# SPDX-License-Identifier: MIT
"""pyproject 打包元数据对账（缺口台账 P1「Python 无 lock/元数据」）。

四件对账，全部落钉：
1. 元数据在位：name / version / requires-python / license；
2. 版本一致：pyproject 的 version 与 cpp/CMakeLists.txt 的
   project(mbdsdr VERSION x.y.z) 同号（打包产物版本单一事实源）；
3. 依赖对账（正向）：mbdsdr_ai 包内第三方导入必须已在 pyproject 声明
   （硬依赖或任一 optional 分组），打包后不许 ImportError；
4. 依赖对账（反向）：声明的硬依赖必须真被导入，不许囤积死依赖。

扫描口径：mbdsdr_ai/**/*.py 但不含 mbdsdr_ai/tests/**（测试另有 test
extra 与仓库根模块依赖，不进 wheel）。BOM 源文件用 utf-8-sig 读——
Python 导入会吞 BOM，裸 utf-8 读出会把 U+FEFF 交给 ast.parse 炸 SyntaxError
（test_gate_error_codes.py 实测踩坑）。
"""
import ast
import pathlib
import re
import sys

try:  # Python 3.11+ 标准库
    import tomllib
except ImportError:  # pragma: no cover - 3.10：tomli 按 python_version marker 装在依赖里
    import tomli as tomllib  # type: ignore

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG_DIR = ROOT / "mbdsdr_ai"

# 导入名 → PyPI 发行包名对照（改名发行的依赖）
_IMPORT2DIST = {
    "PIL": "pillow",
    "iio": "pylibiio",
    "rtlsdr": "pyrtlsdr",
    "serial": "pyserial",
    "websocket": "websocket-client",
    "yaml": "pyyaml",
}

# 系统级硬件绑定：不走 PyPI（apt / 厂商渠道安装），诚实豁免而非假声明。
_SYSTEM_BINDINGS = {"SoapySDR", "hackrf", "uhd"}


def _meta() -> dict:
    with open(ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)


def _import_names() -> set[str]:
    """mbdsdr_ai 包（不含 tests/）所有导入的顶层模块名（含 try 守卫与懒加载）。"""
    names: set[str] = set()
    for py in PKG_DIR.rglob("*.py"):
        if "tests" in py.parts:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8-sig"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def _third_party() -> set[str]:
    local = {p.stem for p in PKG_DIR.glob("*.py")}
    local |= {p.name for p in PKG_DIR.iterdir() if p.is_dir()}
    local |= {"mbdsdr_ai"}
    return {n for n in _import_names()
            if n not in sys.stdlib_module_names and n not in local}


def _declared() -> set[str]:
    """pyproject 全部声明依赖（硬 + 所有 optional 组），归一为小写发行名。"""
    proj = _meta()["project"]

    def norm(dep: str) -> str:
        # 名字段止于任一版本约束符（"[<>=",如 "pyrtlsdr<0.4" -> "pyrtlsdr"）。
        return re.split(r"[<>=\[]", dep, maxsplit=1)[0].strip().lower()

    out = {norm(d) for d in proj["dependencies"]}
    for group in proj.get("optional-dependencies", {}).values():
        out |= {norm(d) for d in group}
    return out


def _version() -> str:
    """版本单一事实源：mbdsdr_ai/VERSION。"""
    return (PKG_DIR / "VERSION").read_text(encoding="utf-8").strip()


def test_metadata_present():
    meta = _meta()["project"]
    assert meta["name"] == "mbdsdr-ai"
    assert "version" not in meta, "版本不许在 pyproject 再手抄一份"
    assert "version" in meta["dynamic"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", _version()), _version()
    assert meta["requires-python"].startswith(">=")
    assert meta["license"]["text"] == "MIT"


def test_version_matches_cpp_project():
    """CMake 的 project 版本必须从 mbdsdr_ai/VERSION 派生，不许各说各话。"""
    cm = (ROOT / "cpp" / "CMakeLists.txt").read_text(encoding="utf-8")
    assert re.search(
        r"file\(READ.*mbdsdr_ai/VERSION.*MBDSDR_VERSION\)", cm
    ), "cpp/CMakeLists.txt 没有从 mbdsdr_ai/VERSION 读取版本"
    m = re.search(r"project\(mbdsdr\s+VERSION\s+\$\{([A-Za-z0-9_]+)\}", cm)
    assert m, "cpp/CMakeLists.txt 的 project 版本没有引用读取结果"
    assert not re.search(r"project\(mbdsdr\s+VERSION\s+[0-9]", cm), \
        "cpp/CMakeLists.txt 仍写死版本字面量"
    dyn = _meta()["tool"]["setuptools"]["dynamic"]["version"]
    assert dyn == {"file": "mbdsdr_ai/VERSION"}


def test_declared_deps_cover_imports():
    """import 了的第三方必须已声明（打包后不许 ImportError）。"""
    declared = _declared()
    missing = {imp for imp in _third_party()
               if imp not in _SYSTEM_BINDINGS
               and _IMPORT2DIST.get(imp, imp).lower() not in declared}
    assert not missing, f"导入了但 pyproject 未声明: {sorted(missing)}"


def test_no_orphan_declared_deps():
    """声明了的硬依赖必须真被导入（不许囤积死依赖）。"""
    proj = _meta()["project"]
    hard = {d.split("[")[0].split(">")[0].split("=")[0].strip().lower()
            for d in proj["dependencies"]}
    imported_dist = {_IMPORT2DIST.get(n, n).lower() for n in _import_names()}
    orphans = {d for d in hard if d not in imported_dist}
    assert not orphans, f"声明了但无人导入的死依赖: {sorted(orphans)}"
