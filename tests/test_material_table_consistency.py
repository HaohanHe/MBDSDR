# -*- coding: utf-8 -*-
"""材料表 ↔ 实现 一致性门禁。

依据 AGENTS.md 第 2 条："文档与实现掰腕子，按 bug 处理"。本门禁把能力地图
§1.5「材料能力完整表」与真实实现对拍：

1. 表里「对应工具」列的每个工具名都必须在 tool_registry 里存在；
2. 表必须是五份材料（行数与材料数固定）；
3. 「验证方式」列引用的测试文件必须真实存在（文档说"由 X 验证"，
   X 得在仓里）。

解析：只认反引号包着的标识符与 tests/ 路径，避免正文散文误伤。
"""
import io
import os
import re
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402

MAP_PATH = os.path.join(_ROOT, "docs", "learn", "capability-map.md")
TABLE_HEADER = "## 1.5 材料能力完整表"
EXPECTED_MATERIALS = 5

TOOL_RE = re.compile(r"`([a-z][a-z0-9_]{2,})`")
TESTFILE_RE = re.compile(r"`((?:mbdsdr_ai/)?tests/[A-Za-z0-9_./-]+\.py)`")
#: 「文件 + 声明条数」形式：`(tests/x.py)`（7 项，……）
CLAIM_COUNT_RE = re.compile(
    r"`((?:mbdsdr_ai/)?tests/[A-Za-z0-9_./-]+\.py)`[（(]?(\d+)\s*项")


def _material_rows():
    txt = io.open(MAP_PATH, encoding="utf-8").read()
    start = txt.find(TABLE_HEADER)
    assert start >= 0, f"能力地图缺少 {TABLE_HEADER} 小节"
    end = txt.find("\n## ", start + 10)
    section = txt[start:end if end > 0 else len(txt)]
    rows = [ln for ln in section.split("\n") if ln.strip().startswith("|")]
    body = rows[2:]                      # 去表头与分隔行
    out = []
    for ln in body:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        out.append({"material": cells[0], "tools_cell": cells[2],
                    "verify_cell": cells[3]})
    return out


def _registry_names():
    return set(MBDSDRAgent().tool_registry.get_tool_names())


def test_material_table_has_five_materials():
    rows = _material_rows()
    assert len(rows) == EXPECTED_MATERIALS, \
        f"材料表应有 {EXPECTED_MATERIALS} 份材料，实为 {len(rows)}"


def test_every_tool_in_material_table_exists():
    names = _registry_names()
    missing = []
    for row in _material_rows():
        for tool in TOOL_RE.findall(row["tools_cell"]):
            if tool not in names:
                missing.append(f"{row['material']}: {tool}")
    assert not missing, "材料表列了不存在的工具:\n  " + "\n  ".join(missing)


def test_every_referenced_test_file_exists():
    missing = []
    for row in _material_rows():
        for rel in TESTFILE_RE.findall(row["verify_cell"]):
            p = os.path.join(_ROOT, rel.replace("/", os.sep))
            if not os.path.isfile(p):
                missing.append(f"{row['material']}: {rel}")
    assert not missing, "材料表引用了不存在的测试文件:\n  " + "\n  ".join(missing)


#: 收集结果缓存（pytest --collect-only 较慢，整模块只做一次）
_COLLECT_CACHE = {}


def _collected_count(rel):
    """该测试文件实际收集到的用例数（参数化已展开）。"""
    if rel in _COLLECT_CACHE:
        return _COLLECT_CACHE[rel]
    path = os.path.join(_ROOT, rel.replace("/", os.sep))
    if not os.path.isfile(path):
        _COLLECT_CACHE[rel] = None
        return None
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", path],
        capture_output=True, text=True, encoding="utf-8", errors="ignore",
        cwd=_ROOT, timeout=900).stdout
    n = len([ln for ln in out.split("\n") if "::" in ln])
    _COLLECT_CACHE[rel] = n
    return n


def test_claimed_test_counts_match_reality():
    """文档里写的"（N 项）"必须等于实际收集到的用例数。"""
    txt = io.open(MAP_PATH, encoding="utf-8").read()
    start = txt.find(TABLE_HEADER)
    end = txt.find("\n## ", start + 10)
    section = txt[start:end if end > 0 else len(txt)]
    bad = []
    for rel, claimed in CLAIM_COUNT_RE.findall(section):
        actual = _collected_count(rel)
        if actual is None:
            bad.append(f"{rel}: 文件不存在")
        elif actual != int(claimed):
            bad.append(f"{rel}: 文档写 {claimed} 项，实际 {actual} 项")
    assert not bad, "材料表用例数与实现不符:\n  " + "\n  ".join(bad)
