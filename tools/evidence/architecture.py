#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""证据 3 —— 三端架构事实：从源码现取，不从散文抄。

README 里那张架构图声称"图里的数字取自源码"。这个组就是把那些数字
重新抓一遍：端口常量、移动端 rtl_tcp 字节归一化、只读 hub 绑定。
任何人改了这几处，本组会立刻显示新值；抓不到就报 FAIL，而不是让
证据单继续挂着过期的数字。

不导入 Qt、不联网、不需要硬件。
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    REPO_ROOT, add_repo_paths, finish, host_info, new_result, parse_common_args,
    which_all,
)

GID = "architecture"
TITLE = "三端架构事实（源码现取：端口 / rtl_tcp 归一化 / 只读 hub 绑定）"


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")


def _line_of(text: str, needle: str) -> int:
    idx = text.find(needle)
    if idx < 0:
        return -1
    return text.count("\n", 0, idx) + 1


def _pin(res, name: str, value, source: str, note: str = "") -> None:
    res["facts"][name] = {"value": value, "source": source, "note": note}


def run(args) -> dict:
    res = new_result(GID, TITLE)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # ---------------- 1) ControlHub 端口（C++ 侧唯一常量） ----------------
    tokens_h = _read("cpp/src/core/tokens.h")
    m = re.search(r"kControlHttpDefaultPort\s*=\s*(\d+)", tokens_h)
    if not m:
        res["status"] = "fail"
        res["notes"].append(
            "cpp/src/core/tokens.h 里找不到 kControlHttpDefaultPort —— "
            "架构图里的端口数字已失去来源，必须重取后更新 README")
    else:
        _pin(res, "control_http_port", int(m.group(1)),
             f"cpp/src/core/tokens.h:{_line_of(tokens_h, m.group(0))}",
             "ControlHub 监听端口；cpp/src/control/control_http_server.cpp 直接引用")

    # ---------------- 2) 移动端 rtl_tcp 字节 → 浮点归一化 ----------------
    iq_dart = _read("mobile/lib/dsp/iq.dart")
    norm = re.search(r"\(\s*b\s*-\s*127\.5\s*\)\s*/\s*127\.5", iq_dart)
    if not norm:
        res["status"] = "fail"
        res["notes"].append(
            "mobile/lib/dsp/iq.dart 里找不到 (b - 127.5) / 127.5 —— "
            "移动端 IQ 归一化口径变了，证据单那句要重核")
    else:
        _pin(res, "rtl_tcp_uint8_to_float", "(b - 127.5) / 127.5",
             f"mobile/lib/dsp/iq.dart:{_line_of(iq_dart, norm.group(0))}",
             "rtl_tcp 送来的 cu8 字节如何变成 [-1,1] 复数样本")

    # ---------------- 3) 移动端只读绑定 ControlHub ----------------
    shell = _read("mobile/lib/app/home_shell.dart")
    hub_host = _line_of(shell, "controlHubHost:")
    hub_port = _line_of(shell, "controlHubPort:")
    if hub_host < 0 or hub_port < 0:
        res["status"] = "fail"
        res["notes"].append(
            "mobile/lib/app/home_shell.dart 里找不到 controlHubHost/Port 绑定 —— "
            "移动端与 ControlHub 的接线已断或改名")
    else:
        _pin(res, "mobile_hub_binding", "controlHubHost + controlHubPort",
             f"mobile/lib/app/home_shell.dart:{hub_host},{hub_port}",
             "移动端只从设置里读主机/端口，不自己算")

    # ---------------- 4) 三端规模（可数，不引用宣传数字） ----------------
    def count(glob: str, suffixes) -> int:
        return sum(1 for p in REPO_ROOT.glob(glob)
                   if p.is_file() and p.suffix in suffixes)

    res["facts"]["three_ends"] = {
        "cpp_sources": count("cpp/src/**/*", {".cpp", ".h"}),
        "python_modules": count("mbdsdr_ai/**/*", {".py"}),
        "dart_sources": count("mobile/lib/**/*", {".dart"}),
        "note": "文件数，不是工具数；工具数以运行时 tool registry 为准",
    }
    reg_sites = sum(
        1 for p in (REPO_ROOT / "mbdsdr_ai").rglob("*.py")
        if "register_tool" in p.read_text(encoding="utf-8", errors="replace"))
    res["facts"]["tool_registration_files"] = {
        "value": reg_sites,
        "note": "含 register_tool 调用的 Python 文件数（同一工具可能多处注册）",
    }

    # ---------------- 5) 星表随仓库下发（离线可画星的前提） ----------------
    npz = REPO_ROOT / "mbdsdr_ai" / "data" / "full_sky_hyg_v41.npz"
    if npz.is_file():
        tracked = "unknown"
        try:
            import subprocess
            out_git = subprocess.run(
                ["git", "ls-files", "--error-unmatch",
                 str(npz.relative_to(REPO_ROOT))],
                cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=20)
            tracked = "yes" if out_git.returncode == 0 else "no"
        except (OSError, subprocess.SubprocessError):
            pass
        _pin(res, "star_catalog_npz", f"{npz.stat().st_size / 1e6:.1f} MB",
             str(npz.relative_to(REPO_ROOT)),
             f"入库={tracked}；随仓库下发，全新克隆无需联网即可画星")
        if tracked == "no":
            res["status"] = "fail"
            res["notes"].append(
                "星表 npz 未入库 —— 全新克隆将画不出星，"
                "所有星空截图失去可复现性")
    else:
        res["status"] = "fail"
        res["notes"].append(
            f"缺 {npz.relative_to(REPO_ROOT)} —— 离线画星的前置数据没随仓库走")

    # ---------------- 6) 本机能不能编 C++（决定 not_verified 口径） ----------------
    toolchain = which_all(["g++", "qmake6", "cmake"])
    res["facts"]["cpp_toolchain"] = {
        **toolchain,
        "note": ("只反映 PATH 上有没有工具链：工具链本体可能装了但没进 PATH。"
                 "因此结论一律写「未做本地编译验证」，不写「本机没有工具链」"),
    }
    if not toolchain.get("g++"):
        res["not_verified"].append(
            "C++ 桌面端：g++/qmake6 不在 PATH（工具链本体可能已安装但未配置），"
            "所有 C++ 结论来自源码审计，未经编译与运行验证")

    res["facts"]["host"] = host_info()
    return res


def main(argv=None) -> int:
    args = parse_common_args(argv)
    started = time.time()
    add_repo_paths()
    try:
        res = run(args)
    except Exception as exc:  # noqa: BLE001 — 顶层兜底，必须诚实报 FAIL
        import traceback
        traceback.print_exc()
        res = new_result(GID, TITLE, status="fail")
        res["notes"].append(f"{type(exc).__name__}: {exc}")
    return finish(res, Path(args.out), args.json_out, started)


if __name__ == "__main__":
    sys.exit(main())
