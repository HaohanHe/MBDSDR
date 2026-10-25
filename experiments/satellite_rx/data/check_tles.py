#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
check_tles.py — TLE 入库校验（防污染，跑完才能入库）
=====================================================
Celestrak 批量拉取会混入错误行（`Invalid query` / `No GP data` / HTML 残片），
污染的 TLE 会让 skyfield 静默出垃圾轨道。本脚本按三行体结构逐行验：

  名称行 / "1 " 开头（TLE 第一行）/ "2 " 开头（TLE 第二行）

校验项：
  1. 元素行必须以 `1 ` / `2 ` 开头（README 铁律）
  2. 记录结构成套（名→1→2，或裸 1→2），顺序不许乱
  3. 无 `Invalid query` 等 Celestrak 错误行污染
  4. 元素行长度 ≥ 60（截断行会让轨道算错）

用法：
  python check_tles.py                # 校验本目录全部 *.txt / *.tle
  python check_tles.py <文件...>      # 只校验指定文件
退出码：0=全干净， 1=有问题（逐条列出）。
"""
import glob, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 注意：别用裸 "404" 当污染词——TLE 科学计数法里 64042-3 这类会误伤
POLLUTION = ("invalid query", "no gp data", "no elements found",
             "<html", "</html", "tle>", "bad request",
             "404 not found", "http error", "not found")
MIN_ELM_LEN = 60


def is_polluted(l):
    s = l.strip().lower()
    if s in ("404", "403", "500", "error"):
        return s
    for p in POLLUTION:
        if p in s:
            return p
    return None


def check_file(fn):
    """返回 (n_records, problems:list[str])"""
    problems = []
    try:
        raw = open(fn, encoding="utf-8", errors="replace").read().splitlines()
    except Exception as e:
        return 0, [f"读不了: {e}"]
    lines = [(i + 1, l.rstrip()) for i, l in enumerate(raw) if l.strip()]

    n_rec, state, pending_name = 0, 0, None   # state: 0=等名/1行, 1=等2行
    for ln, l in lines:
        bad = is_polluted(l)
        if bad:
            problems.append(f"行{ln}: 污染片段 {bad!r}: {l[:60]!r}")
            continue
        is1, is2 = l.startswith("1 "), l.startswith("2 ")
        if is1 or is2:
            if len(l) < MIN_ELM_LEN:
                problems.append(f"行{ln}: 元素行过短({len(l)}<{MIN_ELM_LEN}): {l[:50]!r}")
            if state == 1 and is1:
                problems.append(f"行{ln}: 上一记录缺 2 行就来了新的 1 行")
            if state == 0 and is2:
                problems.append(f"行{ln}: 没有 1 行就来了 2 行")
            if is2 and state == 1:
                n_rec += 1
                state, pending_name = 0, None
            elif is1:
                state = 1
            elif is2:
                state = 0
        else:
            # 名称行：只许出现在"等新记录"状态
            if state == 1:
                problems.append(f"行{ln}: 名称行插在 1/2 行中间: {l[:40]!r}")
            pending_name = l
    if state == 1:
        problems.append("文件结尾：最后一个记录缺 2 行")
    return n_rec, problems


def main():
    files = sys.argv[1:] or sorted(glob.glob(os.path.join(HERE, "*.txt"))) \
        + sorted(glob.glob(os.path.join(HERE, "*.tle")))
    if not files:
        print(f"[err] {HERE} 里没有 *.txt / *.tle 可校验")
        return 1
    bad_files = 0
    total = 0
    for fn in files:
        name = os.path.basename(fn)
        if not os.path.exists(fn):
            print(f"[FAIL] {name}: 文件不存在")
            bad_files += 1
            continue
        n, problems = check_file(fn)
        total += n
        if problems:
            bad_files += 1
            print(f"[FAIL] {name}: {n} 条记录，{len(problems)} 处问题")
            for p in problems[:10]:
                print(f"       - {p}")
            if len(problems) > 10:
                print(f"       … 还有 {len(problems) - 10} 处")
        else:
            print(f"[ok]   {name}: {n} 条记录，干净")
    print(f"{'=' * 56}\n共 {len(files)} 文件 / {total} 条记录，"
          f"{'全部干净' if bad_files == 0 else f'{bad_files} 个文件有问题'}")
    return 0 if bad_files == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
