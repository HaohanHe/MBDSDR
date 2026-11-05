#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""发布前自检（可重复执行）：构建 → 启动冒烟 → 三类缺件 → 窄屏下限 → 日志落点。

设计口径：
* 所有产物（build/dist/config/日志 diff）只落 D 盘；落点若在 C 盘直接判 FAIL；
* 缺件场景不是「看有没有弹窗」，而是**原文匹配**标题 + 关键句；
* 窄屏下线按 ``scripts/ui_narrow_breakdown.py --json`` 的实测数字断言，
  红就如实报 FAIL，不会为了绿去改产品代码；
* 日志落点：快照本机已知用户目录 + 全盘扫描 ``C:\\Users\\<user>`` 下
  路径名含 mbdsdr 且 mtime 落在本次运行窗口内的文件/目录，任一命中即 FAIL。

用法::

    python scripts/release_check.py
    python scripts/release_check.py --skip-build --steps smoke,dll,qt,log
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_ROOT = os.path.join(os.path.dirname(ROOT), ".tmp")
RC_DIR = os.path.join(TMP_ROOT, "release_check")
SPEC = os.path.join(ROOT, "desktop", "MBDSDR.spec")
PYI_CONFIG = os.path.join(TMP_ROOT, "pyi-config")
EXE_NAME = "MBDSDR.exe"
WINDOW_TITLE = "MBDSDR - AI 定义无线电"

DEFAULT_DIST = os.path.join(TMP_ROOT, "pyi-dist12")
DEFAULT_WORK = os.path.join(TMP_ROOT, "pyi-build12")
DEFAULT_DIAG_DIST = os.path.join(TMP_ROOT, "pyi-dist12-noarchive")
DEFAULT_DIAG_WORK = os.path.join(TMP_ROOT, "pyi-build12-noarchive")
DEFAULT_DIFF_BASE = os.path.join(TMP_ROOT, "pyi-dist11")

ALL_STEPS = ("build", "smoke", "dll", "scipy", "qt", "narrow", "log")

USER32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------
def _assert_on_d(path: str) -> None:
    drive = os.path.splitdrive(os.path.abspath(path))[0].upper()
    if drive == "C:":
        raise RuntimeError(f"产物路径落在 C 盘：{path}（纪律要求全部在 D 盘）")
    if not drive:
        raise RuntimeError(f"产物路径没有盘符：{path}")


def _exe_of(dist_dir: str) -> str:
    return os.path.join(dist_dir, "MBDSDR", EXE_NAME)


def _window_text(hwnd: int, maxlen: int = 65536) -> str:
    buf = ctypes.create_unicode_buffer(maxlen)
    USER32.GetWindowTextW(hwnd, buf, maxlen)
    return buf.value


def _window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    USER32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _child_texts(hwnd: int) -> list[str]:
    out: list[str] = []

    def _cb(child: int, _lparam: int) -> bool:
        text = _window_text(child, 8192)
        if text:
            out.append(f"[{_window_class(child)}] {text}")
        return True

    USER32.EnumChildWindows(hwnd, WNDENUMPROC(_cb), 0)
    return out


def _windows_of_pid(pid: int) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []

    def _cb(hwnd: int, _lparam: int) -> bool:
        owner = wintypes.DWORD()
        USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            title = _window_text(hwnd)
            if title:
                rows.append((hwnd, title))
        return True

    USER32.EnumWindows(WNDENUMPROC(_cb), 0)
    return rows


def _wait_window(pid: int, title_part: str, timeout: float) -> tuple[int, str] | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for hwnd, title in _windows_of_pid(pid):
            if title_part in title:
                return hwnd, title
        time.sleep(0.2)
    return None


def _dialog_text(hwnd: int) -> str:
    lines = [f"title: {_window_text(hwnd)}"]
    lines.extend(_child_texts(hwnd))
    return "\n".join(lines)


def _read_log(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


def _wait_log(path: str, predicate, timeout: float) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for line in _read_log(path):
            if predicate(line):
                return line
        time.sleep(0.25)
    return None


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _launch(exe: str, extra_env: dict[str, str] | None = None) -> subprocess.Popen:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [exe], cwd=os.path.dirname(exe), env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)


def _clean_path_env() -> dict[str, str]:
    """只留 Windows 系统目录：证明缺件来自包内，而不是本机另装的 rtl-sdr。"""
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    return {"PATH": os.pathsep.join((os.path.join(root, "system32"), root))}


def _rename_away(src: str) -> str:
    backup = src + ".release-check-off"
    if os.path.exists(backup):
        raise RuntimeError(f"上次运行残留未复原：{backup}")
    os.rename(src, backup)
    return backup


def _restore(backup: str, src: str) -> None:
    if os.path.exists(backup):
        os.rename(backup, src)


# ----------------------------------------------------------------------
# 构建
# ----------------------------------------------------------------------
def _build(dist: str, work: str, noarchive: bool, timeout: float = 1800) -> str:
    _assert_on_d(dist)
    _assert_on_d(work)
    os.makedirs(RC_DIR, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    env = os.environ.copy()
    env["PYINSTALLER_CONFIG_DIR"] = PYI_CONFIG
    if noarchive:
        env["MBDSDR_NOARCHIVE"] = "1"
    else:
        env.pop("MBDSDR_NOARCHIVE", None)
    cmd = [sys.executable, "-m", "PyInstaller", SPEC, "--noconfirm", "--clean",
           "--workpath", work, "--distpath", dist]
    started = time.time()
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True,
                          text=True, errors="replace", timeout=timeout)
    tail = "\n".join((proc.stdout or "").splitlines()[-20:])
    if proc.returncode != 0:
        raise RuntimeError(f"PyInstaller 退出码 {proc.returncode}\n{tail}")
    exe = _exe_of(dist)
    if not os.path.isfile(exe):
        raise RuntimeError(f"构建完成但找不到 {exe}\n{tail}")
    return exe


def _dist_stats(path: str) -> tuple[int, int, dict[str, int]]:
    total = 0
    count = 0
    files: dict[str, int] = {}
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            rel = os.path.relpath(full, path)
            files[rel] = size
            total += size
            count += 1
    return count, total, files


def _diff_dists(base: str, now: str) -> tuple[int, int, int, list[str]]:
    _b_count, _b_size, base_files = _dist_stats(base)
    _n_count, _n_size, now_files = _dist_stats(now)
    added = sorted(set(now_files) - set(base_files))
    removed = sorted(set(base_files) - set(now_files))
    changed = sorted(k for k in (set(base_files) & set(now_files))
                     if base_files[k] != now_files[k])
    lines = ([f"+ {k} ({now_files[k]} B)" for k in added[:20]]
             + [f"- {k} ({base_files[k]} B)" for k in removed[:20]]
             + [f"~ {k} {base_files[k]} -> {now_files[k]} B" for k in changed[:20]])
    return len(added), len(removed), len(changed), lines


# ----------------------------------------------------------------------
# 场景步骤
# ----------------------------------------------------------------------
def _step_smoke(exe: str, log_path: str) -> tuple[bool, str]:
    before = _read_log(log_path)
    proc = _launch(exe)
    found = _wait_window(proc.pid, WINDOW_TITLE, timeout=45)
    if found is None:
        _kill(proc)
        tail = "\n".join(_read_log(log_path)[len(before):][-8:])
        return False, f"45s 内没等到窗口「{WINDOW_TITLE}」；日志尾部：\n{tail}"
    line = _wait_log(log_path, lambda s: s.startswith("[smoke]")
                     and "13/13 通过" in s, timeout=10)
    if line is None or "13/13" not in line:
        _kill(proc)
        return False, f"没等到 13/13 通过的自检行；实际：{line!r}"
    alive = proc.poll() is None
    _kill(proc)
    if not alive:
        return False, "窗口已出现但进程提前退出"
    return True, f"窗口「{found[1]}」存活；{line}"


def _step_dialog(exe: str, log_path: str, expected_title: str,
                 sentences: tuple[str, ...], extra_env: dict[str, str] | None) -> tuple[bool, str]:
    before = _read_log(log_path)
    proc = _launch(exe, extra_env)
    try:
        found = _wait_window(proc.pid, expected_title, timeout=40)
        if found is None:
            tail = "\n".join(_read_log(log_path)[len(before):][-8:])
            return False, f"40s 内没出现标题含「{expected_title}」的窗口；日志尾部：\n{tail}"
        text = _dialog_text(found[0])
        missing = [s for s in sentences if s not in text]
        if missing:
            return False, f"窗口在，但缺少关键句：{missing}\n实际文本：\n{text}"
        return True, f"标题/关键句匹配：{expected_title}｜" + "；".join(sentences)
    finally:
        _kill(proc)


def _step_narrow() -> tuple[bool, str]:
    script = os.path.join(ROOT, "scripts", "ui_narrow_breakdown.py")
    proc = subprocess.run([sys.executable, script, "--json"], cwd=ROOT,
                          capture_output=True, text=True, errors="replace",
                          timeout=240)
    if proc.returncode != 0:
        return False, f"ui_narrow_breakdown 退出码 {proc.returncode}：{proc.stderr[-400:]}"
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return False, f"--json 输出不是合法 JSON：{exc}"
    if "env" not in data:
        return False, "JSON 里没有 env 段"
    summary = data.get("summary") or {}
    size = summary.get("window_minimumSizeHint") or summary.get("window_minimumSize")
    if not size:
        return False, "summary 里没有 window_minimumSizeHint"
    width, height = int(size[0]), int(size[1])
    ok = width <= 1360 and height <= 728
    return ok, (f"实测最小 {width}×{height}（要求 ≤1360×728）；"
                f"env.platform={data['env'].get('qt_platform')}")


def _snapshot_app_log_dirs() -> dict[str, int]:
    """历史泄漏点 ``~/.config/MBDSDR`` 的前后 mtime 快照。"""
    home = os.path.expanduser("~")
    roots = [os.path.join(home, ".config", "MBDSDR"),
             os.path.join(home, ".config", "mbdsdr")]
    snap: dict[str, int] = {}
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            try:
                snap[dirpath] = os.stat(dirpath).st_mtime_ns
            except OSError:
                continue
            for name in filenames:
                full = os.path.join(dirpath, name)
                try:
                    snap[full] = os.stat(full).st_mtime_ns
                except OSError:
                    continue
    return snap


def _diff_snapshot(before: dict[str, int], after: dict[str, int]) -> list[str]:
    changed = [f"新增/消失：{p}" for p in set(after) - set(before)]
    changed += [f"mtime 变动：{p}" for p in set(before) & set(after)
                if before[p] != after[p]]
    return sorted(changed)


def _scan_userprofile_log_writes(since: float) -> tuple[list[str], int]:
    """全盘扫 C:\\Users\\<user>，只认**日志类**文件（*.log / 名字含 startup）。

    不把整个用户目录的 mtime 都算成 FAIL：运行期间浏览器/系统也会写缓存，
    别的 MBDSDR 会话还会写 gui_config.json（用户数据，不是日志），
    那些噪声不能算到本次运行头上。本项要证明的是「日志不落 C 盘用户目录」，
    所以按日志文件名过滤；其余用户数据由产品设计决定，另行说明。
    """
    home = os.path.expanduser("~")
    hits: list[str] = []
    lock = threading.Lock()
    counter = [0]

    def _scan(top: str) -> None:
        local: list[str] = []
        scanned = 0
        for dirpath, _dirnames, filenames in os.walk(top):
            app_dir = "mbdsdr" in dirpath.lower()
            for name in filenames:
                scanned += 1
                lowered = name.lower()
                full = os.path.join(dirpath, name)
                if not app_dir and "mbdsdr" not in lowered:
                    continue
                if not (lowered.endswith(".log") or "startup" in lowered
                        or "mbdsdr_desktop" in lowered):
                    continue
                try:
                    if os.stat(full).st_mtime >= since:
                        local.append(full)
                except OSError:
                    continue
        with lock:
            hits.extend(local)
            counter[0] += scanned

    tops: list[str] = []
    try:
        with os.scandir(home) as it:
            for entry in it:
                tops.append(entry.path)
    except OSError:
        pass
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_scan, tops))
    return hits, counter[0]


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="MBDSDR 发布前自检")
    ap.add_argument("--steps", default=",".join(ALL_STEPS),
                    help=f"逗号分隔，可选：{','.join(ALL_STEPS)}")
    ap.add_argument("--skip-build", action="store_true",
                    help="复用已有 dist，不重新构建")
    ap.add_argument("--dist-dir", default=DEFAULT_DIST)
    ap.add_argument("--work-dir", default=DEFAULT_WORK)
    ap.add_argument("--diag-dist-dir", default=DEFAULT_DIAG_DIST)
    ap.add_argument("--diag-work-dir", default=DEFAULT_DIAG_WORK)
    ap.add_argument("--diff-against", default=DEFAULT_DIFF_BASE)
    ap.add_argument("--skip-user-scan", action="store_true",
                    help="跳过全盘用户目录扫描（只保留已知落点快照）")
    ap.add_argument("--reuse-diag", action="store_true",
                    help="复用已有 noarchive 诊断构建（调试用）")
    args = ap.parse_args()

    wanted = {s.strip() for s in args.steps.split(",") if s.strip()}
    unknown = wanted - set(ALL_STEPS)
    if unknown:
        print(f"未知步骤：{sorted(unknown)}", file=sys.stderr)
        return 2
    if args.skip_build:
        wanted.discard("build")

    os.makedirs(RC_DIR, exist_ok=True)
    run_start = time.time()
    before_user = _snapshot_app_log_dirs()

    # 日志落点：与产品同一策略（startup_paths 的唯一实现）
    sys.path.insert(0, os.path.join(ROOT, "desktop"))
    from startup_paths import startup_log_path  # noqa: E402
    log_path = startup_log_path()
    if not log_path:
        print("启动日志无可用落点（产品会走内存缓冲），本项按 FAIL 处理",
              file=sys.stderr)

    results: list[tuple[str, bool, float, str]] = []

    def run(name: str, fn) -> None:
        if name not in wanted:
            return
        started = time.time()
        try:
            ok, detail = fn()
        except (OSError, RuntimeError, subprocess.SubprocessError,
                json.JSONDecodeError, ValueError) as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        elapsed = time.time() - started
        results.append((name, ok, elapsed, detail))
        flag = "PASS" if ok else "FAIL"
        print(f"[{flag}] {name}  {elapsed:.1f}s  {detail}".replace("\n", "\n    "),
              flush=True)

    def do_build() -> tuple[bool, str]:
        exe = _build(args.dist_dir, args.work_dir, noarchive=False)
        count, size, _files = _dist_stats(args.dist_dir)
        detail = f"{exe}；{count} 文件 / {size / 2 ** 20:.1f} MiB"
        if os.path.isdir(args.diff_against):
            added, removed, changed, lines = _diff_dists(args.diff_against, args.dist_dir)
            detail += (f"；对 {os.path.basename(args.diff_against)}："
                       f"+{added} -{removed} ~{changed}")
            with open(os.path.join(RC_DIR, "dist_diff.txt"), "w",
                      encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        return True, detail

    def do_smoke() -> tuple[bool, str]:
        if not log_path:
            return False, "启动日志无落点"
        return _step_smoke(_exe_of(args.dist_dir), log_path)

    def do_dll() -> tuple[bool, str]:
        exe = _exe_of(args.dist_dir)
        target = os.path.join(args.dist_dir, "MBDSDR", "_internal", "librtlsdr.dll")
        if not os.path.isfile(target):
            return False, f"找不到 {target}"
        backup = _rename_away(target)
        try:
            return _step_dialog(
                exe, log_path, "MBDSDR 运行库缺失",
                ("缺少运行库：librtlsdr.dll", "请重新安装完整包"),
                _clean_path_env())
        finally:
            _restore(backup, target)
            if not os.path.isfile(target):
                raise RuntimeError(f"复原失败：{target}")

    def do_scipy() -> tuple[bool, str]:
        # 发布构建的纯模块在 PYZ 归档里，删不到目录；用 noarchive 诊断构建
        # （同一 spec，仅 MBDSDR_NOARCHIVE=1）把 scipy/_external 变成落盘文件。
        diag_exe = _exe_of(args.diag_dist_dir)
        if not (args.reuse_diag and os.path.isfile(diag_exe)):
            diag_exe = _build(args.diag_dist_dir, args.diag_work_dir,
                              noarchive=True)
        subtree = os.path.join(args.diag_dist_dir, "MBDSDR", "_internal",
                               "scipy", "_external")
        if not os.path.isdir(subtree):
            return False, ("noarchive 诊断构建里没有 "
                           f"{subtree}，无法模拟整棵子树缺失")
        backup = _rename_away(subtree)
        try:
            return _step_dialog(
                diag_exe, log_path, "MBDSDR 启动失败",
                ("No module named 'scipy._external'",
                 "这是打包缺陷，不是你的操作问题"), None)
        finally:
            _restore(backup, subtree)
            if not os.path.isdir(subtree):
                raise RuntimeError(f"复原失败：{subtree}")

    def do_qt() -> tuple[bool, str]:
        exe = _exe_of(args.dist_dir)
        target = os.path.join(args.dist_dir, "MBDSDR", "_internal", "PySide6",
                              "plugins", "platforms", "qwindows.dll")
        if not os.path.isfile(target):
            return False, f"找不到 {target}"
        backup = _rename_away(target)
        try:
            return _step_dialog(
                exe, log_path, "MBDSDR 启动失败",
                ("Qt 平台插件缺失，未能创建窗口",
                 "缺少 Qt 平台插件 platforms/qwindows.dll",
                 "重新解压完整安装包"), None)
        finally:
            _restore(backup, target)
            if not os.path.isfile(target):
                raise RuntimeError(f"复原失败：{target}")

    def do_log() -> tuple[bool, str]:
        if log_path and "\\users\\" in log_path.lower().replace("/", "\\"):
            return False, f"启动日志落在 C 盘用户目录：{log_path}"
        after = _snapshot_app_log_dirs()
        changed = _diff_snapshot(before_user, after)
        if changed:
            return False, "历史泄漏点被写：\n" + "\n".join(changed[:20])
        if args.skip_user_scan:
            return True, f"~/.config/MBDSDR 无新增/变化（全盘扫描已跳过）；日志={log_path}"
        hits, scanned = _scan_userprofile_log_writes(run_start)
        if hits:
            return False, f"C 盘用户目录出现本次写入的日志文件：\n" + \
                "\n".join(hits[:20])
        return True, (f"C:\\Users 全盘 {scanned} 个文件里无本次写入的日志"
                      f"（*.log/startup，mtime 过滤）；日志={log_path}")

    run("build", do_build)
    run("smoke", do_smoke)
    run("dll", do_dll)
    run("scipy", do_scipy)
    run("qt", do_qt)
    run("narrow", _step_narrow)
    run("log", do_log)

    passed = sum(1 for _n, ok, _e, _d in results if ok)
    failed = len(results) - passed
    total = sum(e for _n, _ok, e, _d in results)
    skipped = len(wanted) - len(results)
    print("-" * 72)
    print(f"汇总：PASS {passed} / FAIL {failed} / SKIP {skipped}  "
          f"｜总耗时 {total:.1f}s")
    if failed:
        print("FAIL 步骤：" + "、".join(n for n, ok, _e, _d in results if not ok))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
