#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
check_data_licenses.py — 数据文件许可合规的可执行检查
=====================================================

规则：仓库内**数据文件**的来源声明若属于 GPL 家族，不得出现在 MIT 主库路径
``mbdsdr_ai/`` 下；确实无法替换的必须在本脚本 ``WHITELIST`` 中显式列出并写明
理由。写法与 ``docs/data-inventory.md`` 第 3 节同步。

扫描范围：``.json/.csv/.tsv/.npz/.npy/.dat/.txt/.xml/.yaml/.yml``；
``.npz`` 只读 ``meta`` 数组，其它按 UTF-8 文本扫描。命中判定只认**许可声明**
形态：``GPL-3.0`` / ``GPLv3`` / ``GNU General Public License`` /
``"license": "GPL..."`` / ``SPDX-License-Identifier: GPL...``。
不认裸 ``GPL`` 子串——base64 向量等数据里会偶然出现这三个字母，那是误报。

“MIT 主库路径”定义为 ``mbdsdr_ai/``。该路径下的 GPL 声明 = **违规**；
路径外的同类声明只作提示（``--verbose`` 打印），不算违规，因为它们是
测试向量/第三方板的来源标注，不进入随包分发的库。

用法::

    python scripts/check_data_licenses.py            # 绿=0，红=1
    python scripts/check_data_licenses.py --json     # 机器可读
    python scripts/check_data_licenses.py --verbose  # 同时列出路径外提示
    python scripts/check_data_licenses.py --probe    # 反证：临时放 GPL 文件，必须报红

退出码：0 合规；1 存在未豁免的 GPL 数据文件；2 扫描错误。
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
MIT_PACKAGE = REPO_ROOT / "mbdsdr_ai"
DATA_EXTENSIONS = {
    ".json", ".csv", ".tsv", ".npz", ".npy", ".dat", ".txt", ".xml",
    ".yaml", ".yml",
}
SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules", "dist",
    "scratch", ".mypy_cache", ".pytest_cache", ".audit_tmp",
}
# 构建目录（build/build-local/…）里全是 CMake 自动生成物，不属随仓数据。
SKIP_PREFIXES = ("build",)
GPL_PATTERNS = [
    re.compile(r"(?i)\bGPL(?:-|\s*v|v)?[-\s]?[0-9]"),          # GPL-3.0 / GPLv2 / GPL 3
    re.compile(r"(?i)\bGNU\s+(?:General\s+Public\s+License|GPL)"),
    re.compile(r"(?i)General\s+Public\s+License"),
    re.compile(r"(?i)((?:source_?)?licen[cs]e[\"'\s:=]{1,8})GPL"),  # "license": "GPL"
    re.compile(r"(?i)SPDX-License-Identifier\s*[:=]\s*GPL"),
]

# path -> 理由。空理由视为配置错误。
WHITELIST: Dict[str, str] = {
    "mbdsdr_ai/data/constellations_iau.json": (
        "88 个 IAU 星座中 61 个中文名取自 Stellarium zh_CN.po 译文"
        "（GPL-2.0-or-later）；均为 2–6 字事实性专名，不含可版权文本，"
        "连线为 CC BY-SA 4.0。后续计划：改用 Wikidata CC0 标签后移除本豁免。"
    ),
}


class ScanError(RuntimeError):
    pass


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _iter_data_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in DATA_EXTENSIONS:
            continue
        parts = path.relative_to(root).parts
        dirs = parts[:-1]
        if any(d in SKIP_DIRS or d.lower().startswith(SKIP_PREFIXES)
               for d in dirs):
            continue
        yield path


def _text_of(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".npz":
        try:
            import numpy as np
            with np.load(path, allow_pickle=False) as npz:
                if "meta" not in npz:
                    return ""
                return str(npz["meta"].item())
        except Exception as exc:  # 读不了就报扫描错误，不静默放过
            raise ScanError(f"{path}: npz meta 读取失败: {exc}") from exc
    if suffix == ".npy":
        try:
            import numpy as np
            return str(np.load(path, allow_pickle=False).item())
        except Exception:
            return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ScanError(f"{path}: 读取失败: {exc}") from exc


def _hits(text: str) -> List[str]:
    """返回命中的许可声明片段（去重）；只认声明形态，不做裸子串匹配。"""
    found = set()
    for pat in GPL_PATTERNS:
        for m in pat.finditer(text):
            found.add(m.group(0).strip())
    return sorted(found)


def scan(root: Path = REPO_ROOT) -> dict:
    """返回 {scanned, hits, violations, warnings}；不回显全文，只回显命中词。"""
    root = Path(root)
    scanned = 0
    hits: List[dict] = []
    for path in _iter_data_files(root):
        scanned += 1
        rel = _rel(path, root)
        for word in _hits(_text_of(path)):
            hits.append({"path": rel, "matched": word,
                         "in_mit_package": rel.startswith("mbdsdr_ai/")})
    violations = []
    warnings = []
    for item in hits:
        if item["path"] in WHITELIST and WHITELIST[item["path"]].strip():
            item["whitelisted"] = True
            item["reason"] = WHITELIST[item["path"]]
            warnings.append(item)
        elif item["in_mit_package"]:
            violations.append(item)
        else:
            warnings.append(item)
    bad_whitelist = [p for p, why in WHITELIST.items() if not why.strip()]
    if bad_whitelist:
        raise ScanError(f"WHITELIST 条目缺理由: {bad_whitelist}")
    return {"root": str(root), "scanned": scanned, "hits": hits,
            "violations": violations, "warnings": warnings}


def _probe(root: Path) -> int:
    """反证：临时写入一个 GPL 源数据文件，扫描必须报红，然后清理。"""
    target = root / "mbdsdr_ai" / "data" / "_gpl_probe.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(
        {"meta": {"source": "probe", "source_license": "GPL-3.0-or-later"}},
        ensure_ascii=False), encoding="utf-8")
    try:
        result = scan(root)
    finally:
        target.unlink(missing_ok=True)
    red = [v for v in result["violations"] if v["path"].endswith("_gpl_probe.json")]
    if red:
        print("PROBE RED OK: GPL 探针被检出并拦截")
        return 0
    print("PROBE FAILED: GPL 探针未被检出")
    return 2


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=REPO_ROOT)
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--verbose", action="store_true",
                    help="列出路径外的 GPL 来源标注（仅提示，不算违规）")
    ap.add_argument("--probe", action="store_true", help="反证：必须报红")
    args = ap.parse_args(argv)
    try:
        if args.probe:
            return _probe(args.root)
        result = scan(args.root)
    except ScanError as exc:
        print(f"SCAN ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["violations"]:
            print(f"合规检查失败：{len(result['violations'])} 个 GPL 数据文件在 "
                  f"mbdsdr_ai/ 下且未豁免", file=sys.stderr)
            return 1
        return 0
    else:
        print(f"scanned data files: {result['scanned']}")
        for item in result["warnings"]:
            if item.get("whitelisted"):
                print(f"  WHITELIST: {item['path']} ({item['matched']})")
            elif args.verbose:
                print(f"  OUTSIDE-MIT: {item['path']} ({item['matched']})")
        for item in result["violations"]:
            print(f"  VIOLATION: {item['path']} ({item['matched']})")
    if result["violations"]:
        print(f"合规检查失败：{len(result['violations'])} 个 GPL 数据文件在 "
              f"mbdsdr_ai/ 下且未豁免", file=sys.stderr)
        return 1
    print("合规检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
