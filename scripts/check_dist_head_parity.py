#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""产物与构建 HEAD 的一致性对拍（发布前一条命令出结论）。

用法::

    python scripts/check_dist_head_parity.py --dist d:\\mbdsdr\\.tmp\\wf2-dist\\MBDSDR
    python scripts/check_dist_head_parity.py --dist <dir> --head 05674ef5 --json out.json

判据（写死在这里，改判据须同时改本文件与应用文档）:

1. **可读源逐字一致**：两侧都存在的可读文件（非 BINARY_EXTS）用
   ``git hash-object <dist 文件>`` 取得内容哈希，与 ``git ls-tree`` 记录的
   HEAD blob 哈希比对。等价于 ``git cat-file blob`` 逐字节比较；文本按 git
   内容口径（CRLF/LF 过滤）而不是按工作区落盘字节。``datas`` 的 dist 目标名
   与源路径可能不同（spec 把 ``desktop/assets`` 装成 ``assets/``），此时按
   「HEAD 里恰好一个路径以 ``/<dist 相对路径>`` 结尾」唯一后缀配对，配对结果
   单独列在「别名映射」里，不静默吞掉。
2. **二进制/编译产物只判存在且非空**：``.exe/.dll/.pyd/.pyc/.zip/.npz/.png/
   .ico/.qm/.pdf/.ttf/...`` 不硬比字节——不同编译器/平台/UPX 下字节必然不同。
3. **dist 有而 HEAD 无** = 陈旧残留或生成物，只在项目命名空间
   （``assets/``、``mbdsdr_ai/``、``third_party_licenses/``、``_internal`` 根级文件）
   内报告；第三方顶层目录（numpy/scipy/PySide6/…）不算。
4. **HEAD 有而 dist 无** = 漏打，只检查打包声明覆盖的路径：
   ``desktop/assets/``（spec 的 datas 源）、``third_party_licenses/``、
   根 ``LICENSE``/``NOTICE.md``、``mbdsdr_ai/`` 下的数据文件，外加 spec
   显式声明的 ``mbdsdr_ai/astronomy.py``。缺件按别名映射回 dist 侧比对。
5. **``mbdsdr_ai/tests/`` 不得出现在产物里**：spec 的 ``datas`` 已过滤开发
   资产；产物里命中该前缀即 FAIL（防止 spec 被改回去没人发现）。

豁免（不参与任何清单，只在报告里给计数）:

- ``.pyc`` / ``__pycache__``：PyInstaller 的编译缓存，不是源；
- ``.py`` 不参与「漏打」：默认 ``noarchive=False`` 时模块在 ``PYZ`` 归档里，
  产物中看不到独立文件属正常（诊断构建 ``MBDSDR_NOARCHIVE=1`` 才会落盘）；
- ``mbdsdr_ai/tests/``：spec 明确排除（``excludes=['mbdsdr_ai.tests']``）；
- 第三方顶层目录与 ``_internal`` 根级运行库（``base_library.zip``、``.dll``/``.pyd``）；
- ``MBDSDR.exe``：引导层产物，按存在且非空判。

字节数只作展示，不作判据：同一文本在 dist 侧可能是 CRLF、在 HEAD blob 里是 LF
（实测 ``assets/icon.svg`` dist 1241 B vs HEAD 1211 B，哈希一致），别再当差异查一遍。

退出码：0 = 无差异；1 = 有差异（清单或禁止路径任一命中）；2 = 参数/环境错误。
不修改任何打包清单，只报事实。
"""

import argparse
import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BINARY_EXTS = {
    ".exe", ".dll", ".pyd", ".pyc", ".so", ".dylib", ".zip", ".npz", ".npy",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".ttf", ".otf",
    ".qm", ".pak", ".pdf", ".mo", ".dat", ".bin", ".pkl", ".mat", ".afm",
    ".bsp", ".obj", ".pdb", ".lib", ".a",
}

PROJECT_TOP = {"assets", "mbdsdr_ai", "third_party_licenses"}
PROJECT_ROOT_FILES = {"LICENSE", "NOTICE.md"}
# spec 显式当 data 带进来的源文件（静态分析看不见的动态加载路径）。
EXPLICIT_DATAS = {"mbdsdr_ai/astronomy.py"}
HEAD_ONLY_EXCLUDE_DIRS = ("mbdsdr_ai/tests/",)
# 发行物里不得出现的项目路径（spec 已过滤的开发资产）。
FORBIDDEN_DIST_PREFIXES = ("mbdsdr_ai/tests/",)


def _run(cmd, cwd=None, check=True):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise SystemExit(f"命令失败：{' '.join(cmd)}\n{proc.stderr}")
    return proc


def head_inventory(head):
    """返回 (blob 哈希, 字节数) 的字典：path -> (sha1, size)。"""
    out = _run(["git", "-C", REPO_ROOT, "ls-tree", "-r", "-l", "--full-tree", head]).stdout
    inv = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        meta, path = line.split("\t", 1)
        parts = meta.split()
        if len(parts) < 4 or parts[3] == "-":
            size = 0
        else:
            size = int(parts[3])
        inv[path] = (parts[2], size)
    return inv


def head_top_levels(head):
    out = _run(["git", "-C", REPO_ROOT, "ls-tree", "--full-tree", head]).stdout
    tops = set()
    for line in out.splitlines():
        if not line.strip():
            continue
        meta, path = line.split("\t", 1)
        if meta.split()[1] == "tree":
            tops.add(path.rstrip("/"))
    return tops


def blob_hash(path):
    proc = _run(["git", "-C", REPO_ROOT, "hash-object", path])
    return proc.stdout.strip()


def is_binary(path):
    return os.path.splitext(path)[1].lower() in BINARY_EXTS


def dist_files(dist):
    out = []
    for root, dirs, files in os.walk(dist):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, dist).replace(os.sep, "/")
            out.append((rel, full, os.path.getsize(full)))
    return out


def map_to_head(rel):
    if rel.startswith("_internal/"):
        return rel[len("_internal/"):]
    return rel


def expected_head_paths(inv):
    """打包声明覆盖的 HEAD 路径（漏打检查的候选集）。"""
    for path in inv:
        if path in PROJECT_ROOT_FILES:
            yield path
            continue
        if (path.startswith("assets/") or path.startswith("desktop/assets/")
                or path.startswith("third_party_licenses/")):
            yield path
            continue
        if path.startswith(HEAD_ONLY_EXCLUDE_DIRS):
            continue
        if not path.startswith("mbdsdr_ai/"):
            continue
        if path in EXPLICIT_DATAS:
            yield path
            continue
        ext = os.path.splitext(path)[1].lower()
        if ext in (".py", ".pyc"):
            continue
        yield path


def main(argv=None):
    ap = argparse.ArgumentParser(description="产物 ↔ 构建 HEAD 一致性对拍")
    ap.add_argument("--dist", required=True, help="onedir 产物根（含 MBDSDR.exe 的目录）")
    ap.add_argument("--head", default="HEAD", help="构建所用 commit（默认 HEAD）")
    ap.add_argument("--json", default=None, help="把完整结果写成 JSON")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.dist):
        print(f"dist 目录不存在：{args.dist}")
        return 2
    try:
        head_sha = _run(["git", "-C", REPO_ROOT, "rev-parse", args.head]).stdout.strip()
    except SystemExit as exc:
        print(str(exc))
        return 2

    inv = head_inventory(head_sha)
    tops = head_top_levels(head_sha)
    files = dist_files(args.dist)

    third_party_tops = set()
    runtime_root_files = 0
    third_party_files = 0
    binary_existing = 0
    hashed = 0
    hash_diff = []
    dist_only = []

    def resolve_alias(head_path):
        """datas 的 dist 目标名可能与源路径不同（spec 把 desktop/assets 装成 assets/）。

        仅当 HEAD 里恰好有一个路径以 ``/<head_path>`` 结尾时认定为同一文件；
        多个候选视为歧义，不自动配对。
        """
        if head_path in inv:
            return head_path
        suffix = "/" + head_path
        cands = [p for p in inv if p.endswith(suffix)]
        return cands[0] if len(cands) == 1 else None

    dist_by_head_path = {}
    alias_map = []
    forbidden = []
    for rel, full, size in files:
        hp = map_to_head(rel)
        if any(hp == pre.rstrip("/") or hp.startswith(pre)
               for pre in FORBIDDEN_DIST_PREFIXES):
            forbidden.append({"path": hp, "bytes": size})
        top = hp.split("/")[0] if "/" in hp else hp
        in_project = top in PROJECT_TOP or (("/" not in hp) and hp in PROJECT_ROOT_FILES)
        target = hp if hp in inv else (resolve_alias(hp) if in_project else None)
        if target is not None:
            dist_by_head_path[target] = (full, size)
            if target != hp:
                alias_map.append({"dist_path": hp, "head_path": target})
            if is_binary(target):
                binary_existing += 1
            else:
                hashed += 1
                digest = blob_hash(full)
                if digest != inv[target][0]:
                    hash_diff.append({"path": target, "dist_bytes": size,
                                      "head_bytes": inv[target][1],
                                      "head_blob": inv[target][0], "dist_blob": digest})
            continue
        if in_project:
            dist_only.append({"path": hp, "bytes": size})
        elif "/" not in hp:
            runtime_root_files += 1
        else:
            third_party_tops.add(top)
            third_party_files += 1

    head_only = []
    for path in expected_head_paths(inv):
        if path not in dist_by_head_path:
            head_only.append({"path": path, "head_bytes": inv[path][1]})

    problems = bool(hash_diff or dist_only or head_only or forbidden)
    print(f"构建 HEAD：{head_sha}")
    print(f"产物：{os.path.abspath(args.dist)}；文件 {len(files)} 个")
    print(f"直比（可读源）：{hashed}；一致 {hashed - len(hash_diff)}；"
          f"不一致 {len(hash_diff)}")
    if alias_map:
        print(f"别名映射：{len(alias_map)} 个（datas 目标名与源路径不同，唯一后缀配对）")
        for item in alias_map:
            print(f"  - {item['dist_path']} → {item['head_path']}")
    print(f"二进制仅判存在：{binary_existing}；第三方包文件 {third_party_files} 个"
          f"（{len(third_party_tops)} 个顶层目录，豁免）；"
          f"运行库根文件 {runtime_root_files} 个（豁免）")
    print(f"dist 有而 HEAD 无：{len(dist_only)}")
    for item in dist_only:
        print(f"  - {item['path']}  {item['bytes']} B")
    print(f"HEAD 有而 dist 无：{len(head_only)}")
    for item in head_only:
        print(f"  - {item['path']}  {item['head_bytes']} B")
    print(f"哈希不一致：{len(hash_diff)}")
    for item in hash_diff:
        print(f"  - {item['path']}  dist {item['dist_bytes']} B / "
              f"head {item['head_bytes']} B")
    print(f"禁止路径（{'、'.join(FORBIDDEN_DIST_PREFIXES)}）：{len(forbidden)}")
    for item in forbidden:
        print(f"  - {item['path']}  {item['bytes']} B")
    print("结论：" + ("FAIL（见上方清单）" if problems else "PASS（无差异）"))
    if third_party_tops:
        print("豁免的第三方顶层目录：" + ", ".join(sorted(third_party_tops)))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({
                "head": head_sha,
                "dist": os.path.abspath(args.dist),
                "files": len(files),
                "hashed": hashed,
                "hash_match": hashed - len(hash_diff),
                "hash_diff": hash_diff,
                "alias_map": alias_map,
                "dist_only": dist_only,
                "head_only": head_only,
                "forbidden": forbidden,
                "binary_existing": binary_existing,
                "third_party_tops": sorted(third_party_tops),
                "third_party_files": third_party_files,
                "runtime_root_files": runtime_root_files,
                "result": "FAIL" if problems else "PASS",
            }, fh, ensure_ascii=False, indent=2)

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
