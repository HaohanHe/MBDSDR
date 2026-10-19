# SPDX-License-Identifier: MIT
"""全包编译守卫：每个 .py 必须能在当前解释器下编译（单测试聚合，不稀释基线计数）。

背景：phase64 上游整合曾引入仅 Python 3.12+（PEP 701）可解析的 f-string
（外层单引号内再嵌同引号字典下标），在 3.11 及以下解释器上
`import mbdsdr_ai` 直接 SyntaxError——`pytest mbdsdr_ai/tests` 全量
collection error，而 3.12 环境毫无感知。本测试在任意解释器下都会
编译全部包文件，语法兼容回归在此现形；用聚合断言而非逐文件参数化，
避免 373 个元测试用例稀释产品回归基线计数。
"""
import os
import py_compile
import tempfile

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _iter_py_files():
    for dirpath, dirnames, filenames in os.walk(PKG_ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", ".git")]
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def test_all_package_files_compile_under_running_interpreter():
    """mbdsdr_ai 包全部 .py 必须能在当前运行的解释器版本下编译。"""
    failures = []
    count = 0
    with tempfile.TemporaryDirectory() as td:
        cfile = os.path.join(td, "out.pyc")
        for path in _iter_py_files():
            count += 1
            try:
                py_compile.compile(path, cfile=cfile, doraise=True)
            except py_compile.PyCompileError as e:
                rel = os.path.relpath(path, PKG_ROOT)
                failures.append(f"{rel}: {e}")
    # 守卫自身有效性：目录若被移动/清空，必须显式失败而不是空跑通过
    assert count > 300, f"扫描范围异常，仅 {count} 个 .py（预期 300+）"
    assert not failures, (
        f"{len(failures)}/{count} 个文件无法在当前解释器 "
        f"(Python {os.sys.version_info.major}.{os.sys.version_info.minor}) 编译:\n"
        + "\n".join(failures[:10])
    )
