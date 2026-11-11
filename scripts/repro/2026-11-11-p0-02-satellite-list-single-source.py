#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-02 独立复现脚本：四套卫星清单是否仍是四份 divergent 的源码表。

roadmap 断言（原文）：
    「四套卫星清单／两套 TLE 缓存，同机不同答案」
    证据等级：实测（逐名核对 → 交集 4 / 并集 8）。

做法（不联网、不需要 SDR）：
  * ``orbit.BUILTIN_SATS``：名称 → NORAD CATNR（唯一事实源候选）
  * ``new_spacetime_tle.DEFAULT_SATELLITES``：模块 ``__getattr__`` 惰性投影
  * ``rf_sky_view._load_celestrak_tle_catalog`` 内的 ``SATELLITE_META``：
    用 AST 取字面量（不触发网络/缓存读取）
  * ``sat_tracker.BUILTIN_DOWNLINK_HZ``：下行频率表的名称集合
  逐名求交集/并集，并检查同名 CATNR 是否冲突；再扫 TLE 缓存目录口径。

退出码（三态）：
    0  复现成功   —— 四份清单名称集合仍不一致（交集 < 并集），或同名 CATNR 冲突
    1  未复现     —— 四份清单名称集合完全一致且无 CATNR 冲突
    2  无法判定   —— 某个源码表已不存在/无法解析（前置不成立）

反证对照：把 ``orbit.BUILTIN_SATS`` 当作唯一源，检查另外三份是否只是投影。
若某份是独立字面量且与唯一源不同，脚本会打印具体差异——这正是旧行为。

用法：
    python scripts/repro/2026-11-11-p0-02-satellite-list-single-source.py
"""
from __future__ import annotations

import ast
import os
import re
import sys
import tempfile

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def _ast_satellite_meta(path: str):
    """从 rf_sky_view 的 _load_celestrak_tle_catalog 里取 SATELLITE_META 字面量。"""
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    candidates = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "SATELLITE_META":
                candidates.append(node.value)
    for value in candidates:
        try:
            raw = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            continue
        out = {}
        for name, meta in raw.items():
            if isinstance(meta, dict) and "catnr" in meta:
                out[str(name)] = int(meta["catnr"])
        if out:
            return out
    return None


def _cache_dirs_from_sources() -> dict:
    """扫源码里写死的 TLE 缓存目录（只做文本取证，不访问文件）。"""
    hits = {}
    pattern = re.compile(r"\.mbdsdr[/\\](tle_cache|new_spacetime_tle|tle)\b")
    for dirpath, _dirnames, filenames in os.walk(os.path.join(ROOT, "mbdsdr_ai")):
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, "r", encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                continue
            for m in pattern.finditer(text):
                hits.setdefault(m.group(1), set()).add(
                    os.path.relpath(p, ROOT).replace("\\", "/"))
    for dirpath, _dirnames, filenames in os.walk(os.path.join(ROOT, "desktop")):
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, "r", encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                continue
            for m in pattern.finditer(text):
                hits.setdefault(m.group(1), set()).add(
                    os.path.relpath(p, ROOT).replace("\\", "/"))
    return hits


def main() -> int:
    profile = tempfile.mkdtemp(prefix="p0-02-profile-", dir=TMP_ROOT)
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[key] = profile
    os.environ.setdefault("MBDSDR_OFFLINE", "1")
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))

    try:
        from mbdsdr_ai import orbit
        from mbdsdr_ai import new_spacetime_tle as nst
        from mbdsdr_ai import sat_tracker
    except Exception as exc:  # noqa: BLE001
        print(f"[环境不满足] 导入失败：{type(exc).__name__}: {exc}")
        return 2

    print(f"repo   : {ROOT}")
    print(f"python : {sys.version.split()[0]}")

    orbit_map = {str(k): int(v) for k, v in dict(orbit.BUILTIN_SATS).items()}
    try:
        nst_list = list(nst.DEFAULT_SATELLITES)
    except Exception as exc:  # noqa: BLE001
        print(f"[无法判定] new_spacetime_tle.DEFAULT_SATELLITES 不可读：{exc!r}")
        return 2
    nst_map = {}
    for item in nst_list:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            nst_map[str(item[0])] = int(item[1])

    rf_path = os.path.join(ROOT, "desktop", "rf_sky_view.py")
    rf_map = _ast_satellite_meta(rf_path) if os.path.exists(rf_path) else None
    if not rf_map:
        print("[无法判定] 未找到 rf_sky_view.SATELLITE_META 字面量")
        return 2

    downlink_names = set(str(k) for k in sat_tracker.BUILTIN_DOWNLINK_HZ)

    tables = {
        "orbit.BUILTIN_SATS": orbit_map,
        "new_spacetime_tle.DEFAULT_SATELLITES": nst_map,
        "rf_sky_view.SATELLITE_META": rf_map,
        "sat_tracker.BUILTIN_DOWNLINK_HZ": downlink_names,
    }
    sets = {k: set(v) for k, v in tables.items()}

    _hr("四份源码表逐名核对")
    for name, s in sets.items():
        print(f"  {name:<40} {len(s)} 项：{sorted(s)}")

    inter = set.intersection(*sets.values())
    union = set.union(*sets.values())
    print(f"\n  交集 {len(inter)} / 并集 {len(union)}")
    print(f"  交集：{sorted(inter)}")
    print(f"  只在部分表出现：{sorted(union - inter)}")

    # 同名 CATNR 冲突检查（三份带 CATNR 的表）
    conflicts = []
    cat_tables = {
        "orbit": orbit_map,
        "new_spacetime_tle": nst_map,
        "rf_sky_view": rf_map,
    }
    for name in union:
        vals = {t: m[name] for t, m in cat_tables.items() if name in m}
        if len(set(vals.values())) > 1:
            conflicts.append((name, vals))
    print(f"  同名 CATNR 冲突：{conflicts if conflicts else '无'}")

    _hr("TLE 缓存目录口径（源码文本取证）")
    caches = _cache_dirs_from_sources()
    for key in sorted(caches):
        print(f"  ~/.mbdsdr/{key:<18} 出现在：{sorted(caches[key])}")
    print(f"  缓存目录种类：{len(caches)}")

    reproduced = (len(inter) < len(union)) or bool(conflicts)
    print(f"\n结论：当前实现复现 P0-02（四套清单仍不同源）: {reproduced}")
    if reproduced:
        if conflicts:
            print("[复现] 仍存在同名不同 CATNR 的硬冲突（会算出两条不同轨道）。")
        else:
            print("[复现] 名称集合仍不一致——同一颗星在不同入口可见性不同；"
                  "虽然已无同名 CATNR 冲突，但「四套清单」本身仍在。")
        return 0
    print("[未复现] 四份清单名称集合一致且无 CATNR 冲突。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
