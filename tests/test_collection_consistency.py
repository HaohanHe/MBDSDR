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

两类缺失必须分开报：**「收集失败」**（文件 import 就炸 / 语法错）与
**「未被收集」**（文件本身能显式收集，但裸目录收集不到，属命名或收集口径）。
混成一句的后果是：真因是 `ImportError` 的人会去改文件名。

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


# pytest 的报错行形如 "E   ImportError: No module named 'foo'"
_ERR_LINE = re.compile(r"^\s*E\s{2,}(\S.*)$", re.M)
_ERR_WORD = re.compile(r"(ImportError|SyntaxError|ModuleNotFoundError|\b\w*Error\b)")


def _first_error_line(text):
    """从收集输出里取「原始错误首行」；取不到就诚实说取不到。"""
    m = _ERR_LINE.search(text)
    if m:
        return m.group(1).strip()
    for line in text.splitlines():
        if _ERR_WORD.search(line) and not line.startswith("="):
            return line.strip()
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return "（无输出）"


def _recheck_file(rel):
    """对单个缺失文件用**显式路径**重跑收集，区分「坏了」与「没被收」。

    显式路径不受 python_files 限制：能收 = 文件本身没毛病，问题在命名/口径；
    收不了（RC≠0 或出现 ERROR）= 文件在收集阶段就炸了。
    返回 (ok, 原始错误首行)。
    """
    env = dict(os.environ)
    env["MBDSDR_TEST_LOCK"] = "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", rel,
         "-p", "no:cacheprovider"],
        cwd=ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    broken = proc.returncode != 0 or re.search(r"^ERROR ", text, re.M)
    return (not broken), ("" if not broken else _first_error_line(text))


def test_every_file_with_test_functions_is_collected():
    expected = _files_with_tests()
    collected, proc = _collected()

    missing = {f: n for f, n in expected.items() if f not in collected}
    if not missing:
        return

    not_collected, broken = {}, {}
    for f, n in sorted(missing.items()):
        ok, err = _recheck_file(f)
        (not_collected if ok else broken)[f] = (n, err)

    sections = []
    if broken:
        sections.append(
            "收集失败（先修这个文件的错误，不要改名）：\n"
            + "\n".join("  - %s：%s" % (f, err) for f, (_, err) in broken.items())
        )
    if not_collected:
        sections.append(
            "未被收集（文件本身能被显式收集，但裸 `pytest tests/` 收不到）：\n"
            + "\n".join("  - %s（%d 条 test_ 函数）" % (f, n)
                        for f, (n, _) in not_collected.items())
        )

    tail = "\n".join(
        line for line in (proc.stdout + proc.stderr).splitlines()[-25:]
        if line.strip()
    )
    assert False, (
        "以下文件含 test_ 函数（含类内），却没有出现在裸 "
        "`pytest --collect-only -q tests/` 的收集结果里：\n\n"
        + "\n\n".join(sections)
        + "\n\n修法：收集失败 → 修 import/语法；未被收集 → 文件名改成 "
        "test_*.py，确属手工脚本则移出 tests/ 或在文件顶部写明为什么不被收集。\n"
        "收集命令 RC=%s，末尾输出：\n%s" % (proc.returncode, tail)
    )


if __name__ == "__main__":
    sys.exit("用 pytest 跑：python -m pytest tests/test_collection_consistency.py -q")
