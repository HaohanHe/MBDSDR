#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""收集一致性守卫：文件里有 test_ 函数，就必须被裸 pytest 收集到。

起因（2026-11-12 实测）：`tests/` 下 18 个文件、151 条断言拿不到收集——
13 个是文件名没有 `test_` 前缀，5 个是把 `test_` 写在类里。CI 没出事只是因为
`scripts/ci_pytest_groups.py` 用 `*roundtrip*` 口径 + **显式路径**传参（显式
路径不受 python_files 限制），于是「CI 跑过的」和「本地 `pytest tests/` 跑过
的」不是同一集合。这类缺口 CI 绿、本地也绿，但绿的不是同一批东西。

判定方式（不猜）：期望集合来自**源码扫描**（正则 `^\\s*def test_`，含类内
方法）；实际集合来自 `pytest --collect-only -q tests/` 的**真实输出**。
用文件名正则去猜收集结果会漏掉类内测试——正是这次漏掉 77 条的同一个原因。

容器根目录的收集范围见 `.github/workflows/ci.yml` 的 matrix：
`tests/`（A/B 分组）、`mbdsdr_ai/tests`、`experiments/tests`、`tools/onboarding`、
`tools/hw_selfcheck`。本守卫只管 `tests/`（其余根的收集一致性另案）。
"""
import os
import re
import subprocess
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)

# 类内方法也命中（缩进任意）；只认 test_ 前缀，与 pytest 默认 python_functions 一致。
_DEF_TEST = re.compile(r"^[ \t]*def test_", re.M)
# --collect-only -q 的节点行：tests/xxx.py::test_yyy[param]
_NODE = re.compile(r"^(tests/[^\s:]+\.py)::", re.M)


def _files_with_tests():
    """源码侧期望：{相对路径: test_ 函数条数}。"""
    expected = {}
    for dirpath, dirnames, filenames in os.walk(TESTS_DIR):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if not name.endswith(".py") or name.startswith("conftest"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8", errors="replace") as fh:
                hits = len(_DEF_TEST.findall(fh.read()))
            if hits:
                rel = os.path.relpath(path, ROOT).replace("\\", "/")
                expected[rel] = hits
    return expected


def _collected():
    """实际侧：裸 `pytest --collect-only -q tests/` 收到的文件集合。

    内层只收集、不执行用例、不建 QApplication，所以关掉跨进程 Qt 锁——
    外层会话已经持锁，内层若再抢会互相等死。
    """
    env = dict(os.environ)
    env["MBDSDR_TEST_LOCK"] = "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/",
         "-p", "no:cacheprovider"],
        cwd=ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    files = set(_NODE.findall(proc.stdout))
    return files, proc


def test_every_file_with_test_functions_is_collected():
    expected = _files_with_tests()
    collected, proc = _collected()

    missing = {f: n for f, n in expected.items() if f not in collected}
    detail = "\n".join(
        "  - %s（%d 条 test_ 函数）" % (f, n) for f, n in sorted(missing.items())
    )
    tail = "\n".join(
        line for line in (proc.stdout + proc.stderr).splitlines()[-25:]
        if line.strip()
    )
    assert not missing, (
        "以下文件含 test_ 函数（含类内），但裸 `pytest --collect-only -q tests/` "
        "收不到它们：\n%s\n"
        "修法：文件名改成 test_*.py；确属手工脚本则移出 tests/ 或在文件顶部写明"
        "为什么不被收集。\n"
        "收集命令 RC=%s，末尾输出：\n%s" % (detail, proc.returncode, tail)
    )


if __name__ == "__main__":
    sys.exit("用 pytest 跑：python -m pytest tests/test_collection_consistency.py -q")
