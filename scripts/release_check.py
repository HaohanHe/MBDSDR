#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""发布前自检（可重复执行）：构建 → 启动冒烟 → 三类缺件 → 窄屏下限 → 日志落点。

设计口径：
* 第一步就是 ``docs``：跑 ``scripts/verify_claims.py --check-lines --strict-lines``，
  文档行号断言不通过则整个发布检查 FAIL——闸门接在这里，「忘了跑」变成不可能；
* ``claims`` 步是 ``docs`` 的另一半：跑 ``tests/test_claims_guard.py``（白名单只减
  不增 + 新增/改写断言必须有四元组 + 已登记红出口）。``docs`` 只看「写了四元组的
  行是否还指得准」，看不见「新写了一条没有四元组的断言」——那条判据在 pytest 里，
  两半都在 ``ALL_STEPS`` 里才算闭合；
* ``guards`` 步把「只在 docs/code-guards.md 登记、没有任何入口」的廉价机检统一
  接进来（无 Qt、不写盘、单项实测 < 15s；清单与不接入理由见 ``GUARD_CHECKS``），
  任一非 0 即 FAIL——闸门不接入口等于没有；
* 所有产物（build/dist/config/日志 diff）只落 D 盘；落点若在 C 盘直接判 FAIL；
* 缺件场景不是「看有没有弹窗」，而是**弹窗标题 + 正文逐字全匹配**
  （整棵子树缺失 / 只缺单文件 / 可选件缺失 三类各一份期望原文）；
* 窄屏下线按 ``scripts/ui_narrow_breakdown.py --json`` 的实测数字断言，
  红就如实报 FAIL，不会为了绿改产品代码或调阈值；
* ``narrow`` 步两套指纹**各自独立判**：dpr1（DPR 1.0）≤1344×700、
  真屏 windows（DPR 1.5）≤1360×728，任一越线整步 FAIL；真屏拿不到时
  ``SKIP(no_gui)``，不允许一套绿掩盖另一套红；
* ``overlap`` 步与窄屏线共用**同一口径**（``ui_narrow_breakdown.py`` §4f），
  输出两套指纹 × 声明最小与 1344×700 共四个「挤压 / 越界」数字（尺寸标签
  以实际值为准，脚本会断言实际 == 请求），
  每个都有自己的实测上限（``SIZE_LIMITS``）；不另造第二套统计；
* 结果三态 PASS/FAIL/SKIP 逐条落 ``.tmp/release_check/result.json``，
  SKIP 带原因码（``no_gui`` 真屏指纹拿不到、``not_run`` 失败即停后未执行、
  ``no_device`` 留给需要真硬件的步骤）；
* 日志落点：启动日志本身不得落在任何 ``C:\\Users\\<user>`` 路径；快照已知
  用户目录，并只扫**产品日志的声明落点**（本地/漫游 AppData 的
  ``MBDSDR\\logs``、仓库与安装目录的 ``logs/``）中本次运行窗口内新增的
  ``*.log``，任一命中即 FAIL，detail 逐个列出扫描根；
* 任一步 FAIL 立即停止后续步骤，并打印该步「下一步该查什么」；
* build 步自动与「上一次 dist」比对（``TMP_ROOT`` 下编号最大的 pyi-distN），
  无需人肉指定基准。

用法::

    # 日常闸（11 步）：源码级判据 + 对最近一次产物的启动/缺件探针
    python scripts/release_check.py

    # 发版前完整闸门（15 步）：另加 build / subtree / single / signal
    python scripts/release_check.py --release

    # 临时只跑某几步
    python scripts/release_check.py --steps smoke,optional,log

两条命令的分工（2026-11-13 裁定）：``build`` 产主包（实测 201.5–280.6s），
``subtree`` 产并消费 noarchive 诊断包（实测 237.4–347.0s，其中 ~233–343s 是
第二次 PyInstaller 构建，检测本体仅数秒），``single``/``signal`` 复用同一
诊断包（各约 3–4s）。四步合计占整条命令 54%–66% 的墙钟，且判据只在「真包」
上成立；日常闸不跑它们，但保留 smoke/optional/qt——那三步消费上一次
``--release`` 产出的 dist，缺包时如实 FAIL，不会假绿。
"""

from __future__ import annotations

import argparse
import ctypes
import difflib
import json
import os
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

#: 三类缺件的期望弹窗原文（逐字全匹配；{log} 是本次启动日志的已知落点）。
EXPECT_TITLE_FAIL = "MBDSDR 启动失败"
EXPECT_TITLE_DLL = "MBDSDR 运行库缺失"
#: ``subtree`` 删的是 ``_internal/scipy/_external`` 整棵目录；``scipy`` 与
#: ``scipy.signal`` 的目录仍在，所以自检**只报** _external 下的两个探针。
#: （2026-11-13 前的期望列了 scipy/scipy.signal，是目录行走探测生效前的旧文本。）
EXPECT_SUBTREE_BODY = (
    "启动自检失败：缺少必需组件（已中止启动）\n"
    "\n"
    "下面这些是应用正常工作的必需项，缺一项都无法继续：\n"
    "\n"
    "• SciPy 私有子树 _external（完整性探针）"
    "（scipy._external.array_api_compat.common）\n"
    "  缺了会怎样：运行期懒加载 scipy 公共子模块时报 ModuleNotFoundError\n"
    "  怎么修：安装包解压不完整：重新解压/重装完整包，"
    "不要删 _internal 里的文件\n"
    "\n"
    "• SciPy array API 兼容层 numpy.fft（完整性探针）"
    "（scipy._external.array_api_compat.numpy.fft）\n"
    "  缺了会怎样：FFT / 频谱计算在运行期懒加载时报 ModuleNotFoundError\n"
    "  怎么修：安装包解压不完整：重新解压/重装完整包，"
    "不要删 _internal 里的文件\n"
    "\n"
    "请按每项的「怎么修」处理后重新启动。\n"
    "\n"
    "日志：{log}"
)
EXPECT_SINGLE_BODY = (
    "启动自检失败：缺少必需组件（已中止启动）\n"
    "\n"
    "下面这些是应用正常工作的必需项，缺一项都无法继续：\n"
    "\n"
    "• SciPy array API 兼容层 numpy.fft（完整性探针）"
    "（scipy._external.array_api_compat.numpy.fft）\n"
    "  缺了会怎样：FFT / 频谱计算在运行期懒加载时报 ModuleNotFoundError\n"
    "  怎么修：安装包解压不完整：重新解压/重装完整包，"
    "不要删 _internal 里的文件\n"
    "\n"
    "请按每项的「怎么修」处理后重新启动。\n"
    "\n"
    "日志：{log}"
)
#: ``signal`` 删的是整棵 ``_internal/scipy/signal`` 目录。这是「目录行走探测」
#: （517d050b）换掉旧期望时**必须补上**的断言：删期望可以，被删掉的那条被测
#: 行为要另有用例兜住，否则就是拿「少测一条」换绿。
EXPECT_SIGNAL_BODY = (
    "启动自检失败：缺少必需组件（已中止启动）\n"
    "\n"
    "下面这些是应用正常工作的必需项，缺一项都无法继续：\n"
    "\n"
    "• SciPy 信号处理子包（scipy.signal）\n"
    "  缺了会怎样：滤波器、解调链、重采样不可用\n"
    "  怎么修：重装 scipy（pip install --force-reinstall scipy）\n"
    "\n"
    "请按每项的「怎么修」处理后重新启动。\n"
    "\n"
    "日志：{log}"
)
EXPECT_OPTIONAL_BODY = (
    "缺少运行库：librtlsdr.dll\n"
    "\n"
    "这些 DLL 应随安装包放在 _internal 目录（或 exe 同目录）。\n"
    "请重新安装完整包，不要手工删 _internal 里的文件；"
    "若使用外部 SDR 驱动，也可安装对应驱动后重试；"
    "若杀毒软件隔离了它们，请恢复后重试。"
)
EXPECT_QT_BODY = (
    "Qt 平台插件缺失，未能创建窗口\n"
    "\n"
    "缺少 Qt 平台插件 platforms/qwindows.dll（当前平台 windows）。\n"
    "常见原因：安装包没解压完整，或只拷贝了 MBDSDR.exe。\n"
    "修复：重新解压完整安装包，_internal 目录必须与 exe 放在一起。\n"
    "\n"
    "日志：{log}"
)

DEFAULT_DIST = os.path.join(TMP_ROOT, "pyi-dist12")
DEFAULT_WORK = os.path.join(TMP_ROOT, "pyi-build12")
DEFAULT_DIAG_DIST = os.path.join(TMP_ROOT, "pyi-dist12-noarchive")
DEFAULT_DIAG_WORK = os.path.join(TMP_ROOT, "pyi-build12-noarchive")

ALL_STEPS = ("docs", "claims", "gen_reconcile", "action_dup", "guards", "build",
             "smoke", "subtree", "single", "signal", "optional", "qt", "narrow",
             "overlap", "log")

#: 日常闸：去掉四个「产物步」——build 产主包，subtree 产 noarchive 诊断包并
#: 删 _external，single/signal 复用同一诊断包删单文件/单目录。这四步的判据
#: 只在真包上成立，且合计占整条命令 54%–66% 墙钟；日常改代码不需要每次重打包。
#: 保留 smoke/optional/qt 是对**最近一次 release 产物**的探针：缺包会 FAIL，
#: 不会用旧包冒充 HEAD 的打包结论（打包结论以 ``--release`` 为准）。
DAILY_STEPS = ("docs", "claims", "gen_reconcile", "action_dup", "guards", "smoke",
               "optional", "qt", "narrow", "overlap", "log")

#: 廉价机检清单（无 Qt、不写盘、单次实测 < 15s，2026-11-13 逐条实跑）：
#: 这些闸门原先只写在 docs/code-guards.md 里，没有任何入口覆盖——即「登记了但
#: 没人跑」。发版前统一跑一遍，单项超时 120s；任一非 0 即整步 FAIL。
#: 口径（为什么不把慢的/红的塞进来）：单次 > 30s 或有已知未清库存（如
#: lint_swallow_all 37s、audit_evidence 51s、check_red_lifecycle 27s、
#: check_help_paths 全量 112s）只登记触发条件，不挂这里——把发版闸门变成
#: 「反正一直红」只会教人跳过它。
GUARD_CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("check_card_links.py", ()),                       # 0.2s
    ("gen_shortcut_table.py", ("--check",)),           # 0.2s
    ("fetch_constellation_names_cc0.py", ("--check",)),  # 0.2s 离线核对
    ("fetch_dso_supplement.py", ("--check",)),         # 0.2s 离线核对
    ("settings_inventory.py", ("--check",)),           # 1.4s
    ("theme_contrast.py", ()),                         # 0.3s
    ("install_git_hooks.py", ("--check",)),            # 0.3s 钩子是否为本机最新
    ("ai_tool_consistency.py", ("--limit", "40")),     # 2.6s
    ("tool_honesty_scan.py", ()),                      # 4.9s
    ("access_layer_fault_injection.py", ()),           # 5.0s
    ("ai_intent_regression.py", ("--limit", "40")),    # 5.5s
    ("audit_source_text_pins.py", ()),                 # 9.1s
    ("check_data_licenses.py", ("--check",)),          # 12.6s
    ("make_decode_fixtures.py", ("--check",)),         # 4.2s 生成器与 fixture/manifest 一致
)

#: 只提示、不改判的检查：退出码恒 0（如路线图阻塞提醒器），输出要人读，
#: 因此只把结论行附在 detail 里，绝不让它把整步判 FAIL。
ADVISORY_CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("check_roadmap_blocking.py", ()),                 # 0.2s，恒 0
)

#: 窄屏验收：两套指纹各自独立判（任一越线整步 FAIL），不换算、不互相代表。
#: 两套**都跑 windows 平台插件**（要有真字体；offscreen 下 QFontDatabase
#: .families()==0，文字按方框度量，尺寸类判据不可信），窗口用
#: WA_DontShowOnScreen 不上屏。两套只差 DPR：
#: ``dpr1``（DPR 1.0，QT_SCALE_FACTOR=1）按 1366×768 屏客户区 1344×700 判；
#: ``windows``（系统 DPR，本机 1.5）按 1360×728 判（逻辑像素）。
NARROW_MAX_DPR1 = (1344, 700)
NARROW_MAX_WINDOWS = (1360, 728)
#: 仅作信息行：真屏逻辑 → 物理，用来暴露「逻辑过关但物理塞不进 1366×768」。
NARROW_SCREEN_PHYSICAL = (1366, 768)

#: 布局口径见 ``scripts/ui_narrow_breakdown.py`` §4f（与窄屏线共用的唯一口径，
#: 不另造一套）：
#:   扫描根 topBar / bottomDock / mainToolBar / centralWidget；
#:   越界 = 子控件 bottomRight 映射到父控件后 x>父宽+1 或 y>父高+1；
#:   挤压 = QLabel/QAbstractButton/QComboBox/QLineEdit 且 sizeHint().width() > width()+2。
#: TEMPORARY_WIDENED —— 暂定上界，**不是设计目标**，UI 收口后必须重测收紧。
#:
#: 旧基线（2026-11-05 由窄屏线实测，当前工作区不可复现）：
#:   dpr1 1000×600 29/1、1344×700 9/1；windows 1000×600 14/1、1344×700 9/1。
#: 注意：「1000×600」只是请求标签，窗口最小尺寸硬墙（实测 1000×680）会把它
#: 夹高，实际测的是 1000×680；2026-11-11 起改为按声明的 minimumSize 请求，
#: 并断言「实际尺寸 == 请求尺寸」。
#: 现测区间（2026-11-06 02:00-02:30，同一命令连跑 5-6 遍）：
#:   dpr1    1000×600 30/1 ~ 36/1（5/6 落在 36/1）；1344×700 9/1 ~ 15/1；
#:   windows 1000×600 13/1（6/6 稳定）；1344×700 7/1（6/6 稳定，比旧值更好）。
#: 漂移原因：工作区里 desktop/control_panel.py、desktop/main_window.py 有未提交
#: 改动（topBar 新增 多普勒/发射/GNSS/UTC 等 statusValue），且 ControlPanel 分栏
#: 最小宽在 310↔365 之间随启动时序变化，同一命令连跑数字会跳。
#: 逐项放宽（相对旧基线）：
#:   dpr1    1000×600 挤压 29→36（+7），越界 1→1（不变）
#:   dpr1    1344×700 挤压  9→15（+6），越界 1→1（不变）
#:   windows 1000×600 挤压 14→13（-1，反而收紧），越界 1→1（不变）
#:   windows 1344×700 挤压  9→7（-2，反而收紧），越界 1→1（不变）
#: 待 desktop/control_panel.py、desktop/main_window.py 未提交改动收口后重测收紧。
#: 检索标记：TEMPORARY_WIDENED（grep 本文件与 docs/packaging.md）。
SIZE_LIMITS = {
    ("dpr1", "min"): (36, 1),         # (挤压, 越界) TEMPORARY_WIDENED
    ("dpr1", "1344x700"): (15, 1),    # TEMPORARY_WIDENED
    ("windows", "min"): (13, 1),      # 比旧基线更严
    ("windows", "1344x700"): (7, 1),  # 比旧基线更严
}

#: FAIL 后下一步该查什么（按步骤名一一对应，失败即停时打印）
FAIL_HINTS = {
    "docs": "四元组断言跑 `python scripts/verify_claims.py --check-lines` 看「错引 / 需重定位」"
            "逐条明细，按 docs/evidence-standard.md §7.7 重定位并取该行 blame 作基准，"
            "不许把基准写成 HEAD，也不许为了过闸而扩大白名单；"
            "渲染点漂移跑 `python scripts/check_help_paths.py` 看「文档行号漂移」，"
            "按 §三/§四 的真实渲染行改坐标（先确认该行含锚点字面量）",
    "claims": "跑 `python -m pytest tests/test_claims_guard.py -q -rf` 看 FAILED 明细："
              "未带四元组的新断言 → 补 <hash> — <path>:<line> — <字面量> 或删断言；"
              "白名单失效行 → 删行并同步调小 waived-total（只减不增）；"
              "确实不可复核的历史结论 → 登记 docs/known-red-register.md 的 K 编号"
              "并加 claims-gate-exempt 标记（K 必须真实存在，悬空直接红）",
    "gen_reconcile": "跑 `python scripts/check_generated_artifacts.py` 看哪些生成物"
                     "与生成器不一致：按行内「重生成」命令重跑（不要手改产物），"
                     "再 `--list` 核对豁免理由是否仍然成立；不要在 pre-commit 加这道闸"
                     "（钩子变慢会诱发 --no-verify）",
    "action_dup": "跑 `python scripts/check_action_duplication.py --gate` 看未豁免项："
                  "真重复要把多个 QAction 实例并成一个，同名不同标识要定单一归属；"
                  "只在「他线正在改、本线这轮动不了」时才挂 "
                  "docs/action-duplication-waivers.md，且必须带 owner + 到期 + 原因，"
                  "到期自动 FAIL，不许加整体放水开关",
    "guards": "按 detail 点名的脚本逐个单跑看明细：谁红修谁，不许把脚本从"
              "GUARD_CHECKS 里删掉来变绿；若某个脚本长期红且属于待清库存，"
              "按 docs/code-guards.md 的登记写清「暂不接入 + 触发条件」，"
              "而不是让它常年红着挡住发版",
    "build": "查 PyInstaller 输出与 --workpath 下的 warn-MBDSDR.txt；"
             "对照 desktop/MBDSDR.spec 的 datas/hiddenimports",
    "smoke": "看启动日志 [smoke] 行的失败模块名；"
             "私有子树漏收对照 docs/packaging.md §3.1",
    "subtree": "确认 noarchive 诊断包里有 _internal/scipy/_external；"
               "查 Analysis-00.toc 的 scipy._external.* 条目",
    "single": "确认诊断包里存在 fft.pyc；"
              "查 _packaging_verdict 是否把单文件缺失判成「解压不完整」",
    "signal": "确认 noarchive 诊断包里有 _internal/scipy/signal 整棵目录；"
              "删整目录必须报「SciPy 信号处理子包（scipy.signal）」——"
              "目录行走探测生效后，这是唯一能证明 scipy.signal 仍被检查的用例",
    "optional": "确认 _internal/librtlsdr.dll 存在；"
                "对照 desktop/startup_paths.py 的 runtime_dll_report()",
    "qt": "确认 _internal/PySide6/plugins/platforms/qwindows.dll 存在；"
          "查 desktop/main.py 的 _qt_platform_plugin_problem()",
    "narrow": "跑 scripts/ui_narrow_breakdown.py（dpr1 与 windows 各一次；"
              "两套都用 windows 平台插件，offscreen 无字体不可用）"
              "看逐控件拆账；两套指纹任一红都归 UX 线，禁止调阈值过关",
    "overlap": "口径是 ui_narrow_breakdown.py §4f，四个数字上限见 SIZE_LIMITS"
                 "（实测棘轮）；先看该步逐项输出与 breakdown 的 squeezed/clipped"
                 " 样本对账，数字变差属 UI 布局线回归，不要改上限迁就",
    "log": "查启动日志落点与 C:\\Users 扫描命中项；"
           "对照 desktop/startup_paths.py 的候选顺序",
}

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


def _dialog_body(hwnd: int) -> str:
    """弹窗正文：MessageBox 的正文在唯一的 Static 子控件里（按钮不算）。"""
    bodies = [t.split("] ", 1)[1] for t in _child_texts(hwnd)
              if t.startswith("[Static] ")]
    return max(bodies, key=len) if bodies else ""


def _normalize_text(text: str) -> str:
    """逐字比对前的唯一归一：CRLF→LF、去行尾空白、去首尾空行。"""
    return "\n".join(
        line.rstrip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ).strip()


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


def _diff_dists(base: str, now: str) -> tuple[list, list, list, list[str]]:
    """与基线包逐个文件比对，三类各自按大小降序，报告只留 top 10。"""
    _b_count, _b_size, base_files = _dist_stats(base)
    _n_count, _n_size, now_files = _dist_stats(now)
    added = sorted(((k, now_files[k]) for k in set(now_files) - set(base_files)),
                   key=lambda kv: kv[1], reverse=True)
    removed = sorted(((k, base_files[k]) for k in set(base_files) - set(now_files)),
                     key=lambda kv: kv[1], reverse=True)
    changed = sorted(((k, base_files[k], now_files[k])
                      for k in set(base_files) & set(now_files)
                      if base_files[k] != now_files[k]),
                     key=lambda kv: abs(kv[2] - kv[1]), reverse=True)
    lines = ["新增 top 10："] + [f"  + {k} ({s} B)" for k, s in added[:10]]
    lines += ["删除 top 10："] + [f"  - {k} ({s} B)" for k, s in removed[:10]]
    lines += ["大小变化 top 10："] + [
        f"  ~ {k} {a} -> {b} B（{b - a:+d}）" for k, a, b in changed[:10]]
    if not (added or removed or changed):
        lines = ["无差异（文件数/大小逐文件一致）"]
    return added, removed, changed, lines


def _prev_dist_dir(current: str) -> str | None:
    """自动找「上一次 dist」：TMP_ROOT 下编号最大且不是本次的那个 pyi-distN。"""
    cur = os.path.normcase(os.path.abspath(current))
    best: tuple[int, str] | None = None
    try:
        names = os.listdir(TMP_ROOT)
    except OSError:
        return None
    for name in names:
        if not name.startswith("pyi-dist"):
            continue
        suffix = name[len("pyi-dist"):]
        if not suffix.isdigit():
            continue
        path = os.path.join(TMP_ROOT, name)
        if (not os.path.isdir(path)
                or os.path.normcase(os.path.abspath(path)) == cur):
            continue
        if best is None or int(suffix) > best[0]:
            best = (int(suffix), path)
    return best[1] if best else None


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
                 expected_body: str,
                 extra_env: dict[str, str] | None) -> tuple[bool, str]:
    """启动缺件弹窗：标题与**整段正文逐字**相等，不是「包含关键字」。

    ``expected_body`` 里唯一的动态值是 ``{log}``（本次启动日志落点），
    由调用方传入唯一的 ``log_path``；除它以外不允许任何模糊匹配。
    """
    before = _read_log(log_path)
    proc = _launch(exe, extra_env)
    try:
        found = _wait_window(proc.pid, expected_title, timeout=40)
        if found is None:
            tail = "\n".join(_read_log(log_path)[len(before):][-8:])
            return False, f"40s 内没出现标题含「{expected_title}」的窗口；日志尾部：\n{tail}"
        actual_title = _window_text(found[0])
        if actual_title != expected_title:
            return False, (f"标题不符：期望「{expected_title}」"
                           f"实际「{actual_title}」")
        actual = _normalize_text(_dialog_body(found[0]))
        # MessageBox 先建窗再填正文；标题一出现就取会读到空 Static（实测：
        # 连续跑 subtree→single 时 single 偶发空正文）。这里只等正文出现，
        # 判据仍是逐字全匹配，等不到照旧按空正文判 FAIL。
        deadline = time.time() + 3
        while not actual and time.time() < deadline:
            time.sleep(0.1)
            actual = _normalize_text(_dialog_body(found[0]))
        expected = _normalize_text(expected_body.format(log=log_path))
        if actual != expected:
            diff = "\n".join(difflib.unified_diff(
                expected.splitlines(), actual.splitlines(),
                fromfile="期望原文", tofile="实际弹窗", lineterm=""))
            return False, "正文不是逐字匹配：\n" + diff
        return True, f"标题/正文逐字匹配：{expected_title}｜{len(expected)} 字符"
    finally:
        _kill(proc)


#: 每套指纹的 ui_narrow_breakdown.py --json 结果（进程内缓存：narrow 与
#: overlap 同一份数据，避免同一口径跑两遍还给出两套数字）。
_BREAKDOWN: dict[str, tuple[dict | None, str]] = {}


def _breakdown(platform: str) -> tuple[dict | None, str]:
    """跑一次 ``ui_narrow_breakdown.py --json``（返回 (数据, 错误文本)）。

    ``platform`` 只区分 DPR 指纹：``dpr1`` 与 ``windows`` **都用 windows
    平台插件**（offscreen 没有字体，QFontDatabase.families()==0，文字尺寸
    不可信）；脚本内部用 WA_DontShowOnScreen 不上屏。
    """
    cached = _BREAKDOWN.get(platform)
    if cached is not None:
        return cached
    script = os.path.join(ROOT, "scripts", "ui_narrow_breakdown.py")
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "windows"
    if platform == "windows":
        # 系统默认缩放（本机 DPR 1.5 / 行高 18），不强制 1.0。
        env.pop("QT_SCALE_FACTOR", None)
        env.pop("QT_ENABLE_HIGHDPI_SCALING", None)
    else:
        # dpr1 指纹固定 DPR 1.0，不受调用方 shell 里的缩放开关影响。
        env["QT_SCALE_FACTOR"] = "1"
        env["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    try:
        proc = subprocess.run([sys.executable, script, "--json"], cwd=ROOT,
                              capture_output=True, text=True, errors="replace",
                              env=env, timeout=240)
    except subprocess.TimeoutExpired:
        result: tuple[dict | None, str] = (None, "ui_narrow_breakdown 240s 超时")
        _BREAKDOWN[platform] = result
        return result
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        result = (None, err[-400:] or f"退出码 {proc.returncode}")
    else:
        try:
            result = (json.loads(proc.stdout), "")
        except json.JSONDecodeError as exc:
            result = (None, f"--json 输出不是合法 JSON：{exc}")
    _BREAKDOWN[platform] = result
    return result


def _headless(err: str) -> bool:
    low = err.lower()
    return ("could not load the qt platform plugin" in low
            or "failed to start" in low
            or "no available platform" in low)


def _narrow_fingerprint(label: str, platform: str) -> tuple[str, str, str]:
    """单套指纹按**自己那套口径**断言，返回 (状态, 原因码, 说明)。

    dpr1（DPR 1.0）比 ≤1344×700；windows（DPR 1.5）比 ≤1360×728。
    两套不换算、不互相代表——一套绿不许掩盖另一套红。真屏逻辑尺寸换算出的
    物理尺寸只作信息行（暴露「逻辑过关但物理塞不进 1366×768」）。
    """
    data, err = _breakdown(platform)
    if data is None:
        if _headless(err):
            return ("SKIP", "no_gui",
                    f"{label}: SKIP（跑不了窗口：无显示/无桌面会话，"
                    f"加载不到 windows 平台插件）：{err[-300:]}")
        return "FAIL", "", f"{label}: ui_narrow_breakdown 失败：{err[-400:]}"
    if "env" not in data:
        return "FAIL", "", f"{label}: JSON 里没有 env 段"
    summary = data.get("summary") or {}
    size = summary.get("window_minimumSizeHint") or summary.get("window_minimumSize")
    if not size:
        return "FAIL", "", f"{label}: summary 里没有 window_minimumSizeHint"
    width, height = int(size[0]), int(size[1])
    info = data["env"]
    dpr = float(info.get("device_pixel_ratio") or 0)
    line = info.get("font_line_height")
    limit = NARROW_MAX_DPR1 if platform == "dpr1" else NARROW_MAX_WINDOWS
    ok = width <= limit[0] and height <= limit[1]
    over_w = max(0, width - limit[0])
    over_h = max(0, height - limit[1])
    phys_w, phys_h = round(width * dpr), round(height * dpr)
    fits = (phys_w <= NARROW_SCREEN_PHYSICAL[0]
            and phys_h <= NARROW_SCREEN_PHYSICAL[1])
    phys_note = f"；物理 {phys_w}×{phys_h}" + ("" if fits else "（信息：>1366×768）")
    over_note = "" if ok else f"；超限 +{over_w}px 宽 / +{over_h}px 高"
    return ("PASS" if ok else "FAIL"), "", (
        f"{label}: DPR {dpr} 行高 {line} 实际 {width}×{height}"
        f"（门槛 ≤{limit[0]}×{limit[1]}）→ {'PASS' if ok else 'FAIL'}"
        f"{over_note}{phys_note}")


def _step_narrow() -> tuple[str, str, str]:
    """两套指纹都测：dpr1 与 windows；任一 FAIL 则整步 FAIL。

    两套都跑 windows 平台插件（offscreen 无字体，尺寸类判据不可信）。
    拿不到窗口（无桌面会话）不是静默跳过，而是 SKIP + 原因码 ``no_gui``；
    只要另一套有 FAIL，整步仍是 FAIL。
    """
    rows = [_narrow_fingerprint("dpr1", "dpr1"),
            _narrow_fingerprint("windows", "windows")]
    statuses = [s for s, _c, _m in rows]
    if "FAIL" in statuses:
        status = "FAIL"
    elif "SKIP" in statuses:
        status = "SKIP"
    else:
        status = "PASS"
    codes = [c for _s, c, _m in rows if c]
    return status, "、".join(codes), " ｜ ".join(m for _s, _c, m in rows)


def _step_overlap() -> tuple[str, str, str]:
    """四个口径数字（尺寸 × 指纹 × 挤压/越界），每个都有自己的上限。

    口径 = ``ui_narrow_breakdown.py`` §4f；上限见 ``SIZE_LIMITS``（实测棘轮，
    理由写在该常量旁）。两套指纹各自判，任一越线即整步 FAIL；拿不到窗口时
    SKIP + ``no_gui``。每个测量点先判「实际尺寸 == 请求尺寸」——窗口最小尺寸
    会夹住请求（旧标签 1000×600 实际是 1000×680），不相等即 FAIL，避免拿
    别的尺寸当结论。
    """
    statuses: list[str] = []
    codes: list[str] = []
    rows: list[str] = []
    for label, platform in (("dpr1", "dpr1"), ("windows", "windows")):
        data, err = _breakdown(platform)
        if data is None:
            if _headless(err):
                statuses.append("SKIP")
                codes.append("no_gui")
                rows.append(f"{label}: SKIP（拿不到窗口指纹）：{err[-200:]}")
            else:
                statuses.append("FAIL")
                rows.append(f"{label}: FAIL（ui_narrow_breakdown：{err[-200:]}）")
            continue
        cal = (data.get("summary") or {}).get("caliber") or {}
        target = cal.get("overflow_at_1344x700") or {}
        declared = cal.get("overflow_at_declared_minimum") or {}
        sizes = cal.get("sizes") or {}
        if not sizes:
            statuses.append("FAIL")
            rows.append(f"{label}: FAIL（ui_narrow_breakdown 未输出尺寸断言，"
                        f"无法确认测的是不是请求的尺寸；旧版脚本的 offscreen "
                        f"数字不可信）")
            continue
        bad_sizes = [k for k in ("declared", "1344x700")
                     if not (sizes.get(k) or {}).get("equal")]
        if bad_sizes:
            detail = "、".join(
                f"{k}: 请求 {sizes.get(k, {}).get('requested')} → "
                f"实际 {sizes.get(k, {}).get('actual')}" for k in bad_sizes)
            statuses.append("FAIL")
            rows.append(f"{label}: FAIL（尺寸断言不过，{detail}）")
            continue
        parts: list[str] = []
        ok_all = True
        for size_key, block in (("min", declared), ("1344x700", target)):
            sq = int(block.get("squeezed_count") or 0)
            cl = int(block.get("clipped_count") or 0)
            max_sq, max_cl = SIZE_LIMITS[(platform, size_key)]
            ok = sq <= max_sq and cl <= max_cl
            ok_all = ok_all and ok
            actual = (sizes.get("declared") if size_key == "min"
                      else sizes.get("1344x700")) or {}
            actual_s = "×".join(str(v) for v in actual.get("actual", []))
            parts.append(
                f"{size_key}（实际 {actual_s}）挤压 {sq}（上限 ≤{max_sq}）/ "
                f"越界 {cl}（上限 ≤{max_cl}）→ {'PASS' if ok else 'FAIL'}")
        statuses.append("PASS" if ok_all else "FAIL")
        rows.append(f"{label}: " + "；".join(parts))
    status = ("FAIL" if "FAIL" in statuses
              else "SKIP" if "SKIP" in statuses else "PASS")
    return status, "、".join(codes), (" ｜ ".join(rows)
                                     + " ｜ 上限来源：TEMPORARY_WIDENED"
                                       "（暂定值，待 UI 收口后重测收紧）")


def _preflight_env() -> tuple[bool, str]:
    """跑全量前先确认环境一致：``import mbdsdr_ai`` 必须成功。

    DSO 目录（``mbdsdr_ai/data/dso_catalog.json``）在工作区被并发改动时会
    短暂不一致（实测 ``_validate_payload`` 报「M1 缺 magnitude_source」），
    后续 UI 步骤（narrow/overlap/smoke）会因此误红。把环境问题拎出来单独报，
    不让窄屏线替它背锅。
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-c", "import mbdsdr_ai"],
            cwd=ROOT, capture_output=True, text=True, errors="replace",
            timeout=120, env=os.environ.copy())
    except subprocess.TimeoutExpired:
        return False, "import mbdsdr_ai 120s 超时"
    if proc.returncode == 0:
        return True, "import mbdsdr_ai 成功"
    tail = (proc.stderr or "").strip().splitlines()
    return False, "\n".join(tail[-6:]) or f"退出码 {proc.returncode}"


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


def _is_log_file_name(name: str) -> bool:
    """``*.log`` 与轮转形态 ``*.log.1`` / ``*.log.2``（不认 ``startup`` 这类词）。"""
    low = name.lower()
    if low.endswith(".log"):
        return True
    _head, sep, tail = low.rpartition(".log.")
    return bool(sep) and bool(_head) and tail.isdigit()


def _is_product_attributed(path: str) -> bool:
    """全盘扫时的**产品归属**判据：路径（目录或文件名）里含 ``mbdsdr``。

    收窄口径靠「声明根」隐含归属；全盘口径没有根可依，必须显式判归属，
    否则会把 Trae / QQMusic / aTrust / clash 这些**别人家的** ``*.log``
    一起算成产品写入（实测 21 条），闸门永远不可能绿。
    """
    return "mbdsdr" in path.lower()


def _product_log_roots(home: str | None = None,
                       dist_dir: str | None = None) -> list[str]:
    """产品日志的**声明落点**（明确位置，不再全盘扫 ``C:\\Users``）。

    只扫这些根，且只认 ``*.log`` / ``*.log.N``：
      * ``%LOCALAPPDATA%\\MBDSDR\\logs``、``%APPDATA%\\MBDSDR\\logs``；
      * 仓库 ``logs/`` 与仓库父目录 ``logs/``（当前启动日志 ``D:\\mbdsdr\\logs``）；
      * 安装目录 ``logs/`` 与 ``安装目录\\MBDSDR\\logs/``。

    收窄不等于少扫：``do_log`` 必须把声明根与其中存在的根逐个打进 detail，
    让人一眼能看出扫的是哪几个位置。
    """
    if home is None:
        home = os.path.expanduser("~")
        local = os.environ.get("LOCALAPPDATA") or os.path.join(
            home, "AppData", "Local")
        roaming = os.environ.get("APPDATA") or os.path.join(
            home, "AppData", "Roaming")
    else:
        local = os.path.join(home, "AppData", "Local")
        roaming = os.path.join(home, "AppData", "Roaming")
    roots = [
        os.path.join(local, "MBDSDR", "logs"),
        os.path.join(roaming, "MBDSDR", "logs"),
        os.path.join(TMP_ROOT, "logs"),
        os.path.join(ROOT, "logs"),
    ]
    if dist_dir:
        roots.insert(0, os.path.join(dist_dir, "MBDSDR", "logs"))
        roots.insert(1, os.path.join(dist_dir, "logs"))
    out: list[str] = []
    seen: set[str] = set()
    for root in roots:
        key = os.path.normcase(os.path.abspath(root))
        if key in seen:
            continue
        seen.add(key)
        out.append(os.path.abspath(root))
    return out


def _step_warmup(exe: str, timeout: float = 90) -> tuple[bool, str]:
    """预热启动：只把 exe 拉起来一次，等任意顶层窗口出现后关掉。

    目的不是判据，而是把「冷启动 49.5s / 热启动 2.3s」两种事实分开：文件
    缓存与首次 DLL 加载留给这一次，随后的 smoke（45s 窗口）与 qt（40s 窗口）
    才在同一状态下读秒。预热失败不影响 PASS/FAIL，只如实打印耗时供归因。
    """
    started = time.time()
    proc = _launch(exe)
    try:
        found = None
        deadline = started + timeout
        while time.time() < deadline:
            # 只认产品窗口（主窗口或三类失败弹窗）；输入法会先建一个
            # "Default IME" 顶层窗口，拿它当预热完成会提前到 0.6s，
            # 文件缓存还没热，读出来的 smoke 秒数不可信。
            rows = [w for w in _windows_of_pid(proc.pid) if "MBDSDR" in w[1]]
            if rows:
                found = rows[0]
                break
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        elapsed = time.time() - started
        if found is None:
            alive = proc.poll() is None
            return False, (f"{elapsed:.1f}s 内没等到窗口"
                           f"（进程{'存活' if alive else '已退出'}）")
        return True, f"{elapsed:.1f}s 出现窗口「{found[1][:40]}」"
    finally:
        _kill(proc)


def _scan_userprofile_log_writes(since: float,
                                 home: str | None = None,
                                 roots: list[str] | None = None,
                                 full: bool = False
                                 ) -> tuple[list[str], int]:
    """扫产品日志写入，两种口径，都只认 ``*.log`` / ``*.log.N``。

    * ``full=False``（日常闸）：只扫**声明落点**（见 ``_product_log_roots``）。
      2026-11-13 第二次收紧：判据没错，扫描根错了。全盘扫 ``C:\\Users`` 一次
      1,029,800 个文件 / 76–100s，只为证明 0 命中；改为只走产品日志的明确位置，
      报表里照样打「扫了多少文件 / 命中几个 / 声明哪几个根」，避免收窄变成静默少扫。
    * ``full=True``（发版前闸）：回到整个用户目录全盘扫。收窄版的盲区正是
      ``%TEMP%`` 里那种**不在任何声明落点**的写入（例：别的会话把
      ``mbdsdr_ai/startup_sequence.py`` 复制到 ``%TEMP%\\axfinal``），
      只有全盘扫看得见。两条命令互补，不是拿收窄换绿。
    """
    if full:
        top = home if home is not None else os.path.expanduser("~")
        roots = [top]
    else:
        roots = list(roots) if roots is not None else _product_log_roots(home)
    hits: list[str] = []
    lock = threading.Lock()
    counter = [0]

    def _scan(top: str) -> None:
        local: list[str] = []
        scanned = 0
        for dirpath, _dirnames, filenames in os.walk(top):
            for name in filenames:
                scanned += 1
                if not _is_log_file_name(name):
                    continue
                full = os.path.join(dirpath, name)
                try:
                    if os.stat(full).st_mtime >= since:
                        local.append(full)
                except OSError:
                    continue
        with lock:
            hits.extend(local)
            counter[0] += scanned

    existing = [r for r in roots if os.path.isdir(r)]
    if existing:
        with ThreadPoolExecutor(max_workers=min(8, len(existing))) as pool:
            list(pool.map(_scan, existing))
    return hits, counter[0]


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="MBDSDR 发布前自检")
    ap.add_argument("--release", action="store_true",
                    help="发版前完整闸门：在 11 步日常闸之外加 "
                         "build/subtree/single/signal（产物四步）")
    ap.add_argument("--steps", default=None,
                    help=f"逗号分隔，覆盖默认步骤集；可选：{','.join(ALL_STEPS)}")
    ap.add_argument("--skip-build", action="store_true",
                    help="复用已有 dist，不重新构建")
    ap.add_argument("--dist-dir", default=DEFAULT_DIST)
    ap.add_argument("--work-dir", default=DEFAULT_WORK)
    ap.add_argument("--diag-dist-dir", default=DEFAULT_DIAG_DIST)
    ap.add_argument("--diag-work-dir", default=DEFAULT_DIAG_WORK)
    ap.add_argument("--diff-against", default=None,
                    help="体积/文件数比对基准；默认自动取 .tmp 下上一次 pyi-distN")
    ap.add_argument("--skip-user-scan", action="store_true",
                    help="跳过产品日志落点扫描（只保留已知落点快照）")
    ap.add_argument("--full-scan", action="store_true",
                    help="产品日志改为全盘扫整个用户目录（发版前跑一次）；"
                         "日常闸只扫声明落点。两者互补，不可互相替代")
    ap.add_argument("--reuse-diag", action="store_true",
                    help="复用已有 noarchive 诊断构建（调试用）")
    ap.add_argument("--keep-going", action="store_true",
                    help="FAIL 后继续跑剩余步骤（只用于出完整报告；默认失败即停）")
    args = ap.parse_args()
    if args.full_scan and args.skip_user_scan:
        print("--full-scan 与 --skip-user-scan 互斥：全盘扫不能同时被跳过",
              file=sys.stderr)
        return 2

    profile = "release" if args.release else "daily"
    defaults = ALL_STEPS if args.release else DAILY_STEPS
    raw_steps = args.steps if args.steps is not None else ",".join(defaults)
    wanted = {s.strip() for s in raw_steps.split(",") if s.strip()}
    unknown = wanted - set(ALL_STEPS)
    if unknown:
        print(f"未知步骤：{sorted(unknown)}", file=sys.stderr)
        return 2
    if args.skip_build:
        wanted.discard("build")

    os.makedirs(RC_DIR, exist_ok=True)
    print(f"闸门：{profile}（{len(wanted)} 步："
          + ",".join(s for s in ALL_STEPS if s in wanted) + "）", flush=True)

    # 环境预检：DSO 数据不一致是环境问题，不是 UI 红灯，单独退出码 3。
    env_ok, env_msg = _preflight_env()
    print(f"预检：{env_msg}", flush=True)
    if not env_ok:
        print("环境不一致（DSO 数据/dso_catalog.json）：`import mbdsdr_ai` 失败，"
              "UI 步骤未启动；先修 mbdsdr_ai/data/dso_catalog.json 再跑闸门。\n"
              + env_msg, file=sys.stderr)
        return 3

    run_start = time.time()
    before_user = _snapshot_app_log_dirs()

    # 日志落点：与产品同一策略（startup_paths 的唯一实现）
    sys.path.insert(0, os.path.join(ROOT, "desktop"))
    from startup_paths import startup_log_path  # noqa: E402
    log_path = startup_log_path()
    if not log_path:
        print("启动日志无可用落点（产品会走内存缓冲），本项按 FAIL 处理",
              file=sys.stderr)

    results: list[dict] = []

    def run(name: str, fn) -> None:
        if name not in wanted:
            return
        started = time.time()
        reason = ""
        try:
            raw = fn()
            if (isinstance(raw, tuple) and len(raw) == 3
                    and isinstance(raw[0], str)):
                status, reason, detail = raw
            elif (isinstance(raw, tuple) and len(raw) == 2
                    and isinstance(raw[0], str)):
                status, detail = raw
            else:
                ok, detail = raw
                status = "PASS" if ok else "FAIL"
        except Exception as exc:  # noqa: BLE001 - 步骤异常一律记 FAIL，不能崩在闸门里
            status, detail = "FAIL", f"{type(exc).__name__}: {exc}"
        elapsed = time.time() - started
        results.append({"step": name, "status": status,
                        "reason_code": reason if status == "SKIP" else "",
                        "seconds": round(elapsed, 1), "detail": detail})
        print(f"[{status}] {name}  {elapsed:.1f}s  {detail}".replace(
            "\n", "\n    "), flush=True)

    def do_docs() -> tuple[bool, str]:
        """文档断言闸门：四元组（verify_claims）+ 渲染点（check_render_points）
        + 回退事件表（check_rollback_table）。

        三者输入集不相交：verify_claims 只认带 hash+字面量的行，裸 ``path:N``
        渲染点引用根本不在它的池子里，§6.10.2 回退表则是列内计数与提交号的
        对账。只接一个，另一边红着也能打印 PASS。
        ``MBDSDR_ROLLBACK_TABLE_DOC`` 只在反证时用：指向一份被改坏的表副本，
        让这一步必红——它只会让这一步更严，不能把红变绿。
        """
        script = os.path.join(ROOT, "scripts", "verify_claims.py")
        proc = subprocess.run(
            [sys.executable, script, "--check-lines", "--strict-lines"],
            cwd=ROOT, capture_output=True, text=True, timeout=900)
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        lines = out.splitlines()
        dist = next((ln.strip() for ln in lines
                     if "含字面量" in ln and "合计" in ln), "（未产出分布行）")
        step4 = next((ln.strip() for ln in lines
                      if ln.strip().startswith("第 4 步")), "")
        problems: list[str] = []
        if proc.returncode != 0:
            bad = next((ln.strip() for ln in lines
                        if ln.strip().startswith("结果：")), "（未产出明细行）")
            bad_w = next((ln.strip() for ln in lines
                          if "白名单不合规" in ln), "")
            problems.append(f"四元组退出码 {proc.returncode}；{dist}；{step4}；"
                            + f"{bad} {bad_w}".strip())
        try:
            sys.path.insert(0, os.path.join(ROOT, "scripts"))
            import check_help_paths
            render = check_help_paths.check_render_points()
        except Exception as e:  # noqa: BLE001 - 跑不起来也算没查，不能静默当绿
            render = [f"渲染点闸门跑不起来：{type(e).__name__}: {e}"]
        if render:
            problems.append(f"渲染点 {len(render)} 处：" + "；".join(render[:3]))

        rollback = os.path.join(ROOT, "scripts", "check_rollback_table.py")
        rb_cmd = [sys.executable, rollback]
        rb_doc = os.environ.get("MBDSDR_ROLLBACK_TABLE_DOC")
        if rb_doc:
            rb_cmd += ["--doc", rb_doc]
        rb = subprocess.run(rb_cmd, cwd=ROOT, capture_output=True, text=True,
                            timeout=120)
        rb_lines = [ln.strip() for ln in
                    ((rb.stdout or "") + "\n" + (rb.stderr or "")).splitlines()
                    if ln.strip()]
        rb_detail = next((ln for ln in reversed(rb_lines)
                          if ln.startswith(("PASS", "FAIL"))), "（未产出结论行）")
        if rb.returncode != 0:
            bad = [ln for ln in rb_lines if ln.startswith("FAIL")]
            problems.append(f"回退表 {rb_detail}："
                            + (" / ".join(bad[:3]) if bad
                               else f"退出码 {rb.returncode}"))
        if problems:
            return False, "；".join(problems)
        return True, "；".join(p for p in (dist, step4, "渲染点 0 处",
                                           rb_detail) if p)

    def do_claims() -> tuple[bool, str]:
        """文档断言闸门的另一半：``tests/test_claims_guard.py``。

        ``docs`` 步跑 ``verify_claims --check-lines``，只判「已写四元组的行是否
        还指得准」；「新写了一条没有四元组的断言」不在它的输入集里（裸断言没有
        hash，进不了它的池子）。这里跑 pytest 判据：白名单只减不增、失效行必须
        销账、新增/改写断言必须带四元组、已登记红出口必须绑真实 K 编号。
        两半都必须在 ``ALL_STEPS`` 里，缺一半就等于放行另一半。
        """
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_claims_guard.py",
             "-q", "-rf", "-p", "no:cacheprovider"],
            cwd=ROOT, capture_output=True, text=True, timeout=900)
        lines = [ln.strip() for ln in
                 ((proc.stdout or "") + "\n" + (proc.stderr or "")).splitlines()
                 if ln.strip()]
        summary = next((ln for ln in reversed(lines)
                        if ln[:1].isdigit() and " in " in ln
                        and (" passed" in ln or " failed" in ln)),
                       "（未产出汇总行）")
        failed = [ln for ln in lines if ln.startswith("FAILED ")]
        if proc.returncode == 0:
            return True, summary
        detail = f"{summary}；{len(failed)} 条失败"
        if failed:
            detail += "：" + " / ".join(failed[:3])
        return False, detail

    def do_gen_reconcile() -> tuple[bool, str]:
        """生成物对账：重跑文档/表格/清单生成器，比对产物是否同步。

        刻意不挂 pre-commit：钩子变慢会诱发 --no-verify（既有裁定）。这里
        （发版前）是唯一自动闸门；日常改动相关源文件时手动跑 ``--changed``。
        """
        script = os.path.join(ROOT, "scripts", "check_generated_artifacts.py")
        proc = subprocess.run([sys.executable, script, "--json"],
                              cwd=ROOT, capture_output=True, text=True,
                              timeout=1800)
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        try:
            data = json.loads((proc.stdout or "").strip())
        except json.JSONDecodeError:
            return False, "对账输出不是 JSON（生成器崩了）：" + \
                out.strip().replace("\n", " ")[:300]
        if proc.returncode == 0:
            # 单报一条关切的单次耗时：后端能力表要现场调用 supports_*，
            # 若哪天它慢到不可接受，这里能直接看见，不用翻 JSON。
            secs = next((r.get("seconds") for r in data.get("results", [])
                         if r.get("id") == "backend_capability"), None)
            extra = (f"；后端能力表单次 {secs:.2f}s"
                     if isinstance(secs, (int, float)) else "")
            return True, (f"{data.get('checked', 0)} 个生成物与生成器一致"
                          f"{extra}；豁免 {data.get('exempt', 0)}（理由见 --list）")
        bad = [r.get("path", "?") for r in data.get("results", [])
               if r.get("status") != "ok"]
        return False, "生成物不同步/生成器失败：" + (", ".join(bad) or "见脚本输出")

    def do_action_dup() -> tuple[bool, str]:
        """命令/QAction 重复注册闸门：与 docs 同级，真重复或同名不同标识即 FAIL。

        ``MBDSDR_ACTION_DUP_PROBE=1`` 只在反证时用：往真实菜单里注入两个
        同 id 实例，让闸门必红——它只会让这一步更严，不能把红变绿。
        """
        script = os.path.join(ROOT, "scripts", "check_action_duplication.py")
        cmd = [sys.executable, script, "--gate"]
        probe = os.environ.get("MBDSDR_ACTION_DUP_PROBE") == "1"
        if probe:
            cmd.append("--probe-duplicate")
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True,
                              text=True, timeout=600)
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        counts = [ln for ln in lines if ln.startswith("[INFO] 正常配对")
                  or ln.startswith("[INFO] objectName")]
        detail = "；".join(counts) if counts else "（未产出计数行）"
        if probe:
            detail = "反证注入探针；" + detail
        if proc.returncode != 0:
            bad = [ln for ln in lines if ln.startswith("[FAIL]")]
            return False, detail + "；" + (" / ".join(bad) if bad
                                          else f"退出码 {proc.returncode}")
        return True, detail

    def do_guards() -> tuple[bool, str]:
        """廉价机检统一入口：把「只在文档里登记、没人跑」的闸门接进来。

        每个脚本单进程、单次超时 120s；输出只留最后一非空行，避免把发版
        日志刷满。``ADVISORY_CHECKS`` 只附结论、不参与判定（如路线图提醒器
        恒退出 0）。任何一项非 0 即整步 FAIL，明细点名到脚本。
        """
        env = os.environ.copy()
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["PYTHONIOENCODING"] = "utf-8"
        bad: list[str] = []
        parts: list[str] = []

        def _tail(proc: subprocess.CompletedProcess) -> str:
            lines = [ln.strip() for ln in
                     ((proc.stdout or "") + "\n" + (proc.stderr or "")).splitlines()
                     if ln.strip()]
            return (lines[-1] if lines else "")[:150]

        for script, extra in GUARD_CHECKS:
            path = os.path.join(ROOT, "scripts", script)
            try:
                proc = subprocess.run([sys.executable, path, *extra], cwd=ROOT,
                                      capture_output=True, text=True,
                                      errors="replace", env=env, timeout=120)
            except subprocess.TimeoutExpired:
                bad.append(f"{script}(超时>120s)")
                parts.append(f"{script}: TIMEOUT")
                continue
            if proc.returncode != 0:
                bad.append(f"{script}(rc={proc.returncode})")
                parts.append(f"{script}: FAIL {_tail(proc)}")
            else:
                parts.append(f"{script}: PASS")
        for script, extra in ADVISORY_CHECKS:
            path = os.path.join(ROOT, "scripts", script)
            try:
                proc = subprocess.run([sys.executable, path, *extra], cwd=ROOT,
                                      capture_output=True, text=True,
                                      errors="replace", env=env, timeout=120)
                parts.append(f"{script}: 提示 {_tail(proc)}")
            except subprocess.TimeoutExpired:
                parts.append(f"{script}: 提示（超时>120s，未参与判定）")
        detail = "；".join(parts)
        if bad:
            return False, "红：" + "、".join(bad) + "｜" + detail
        return True, detail

    def do_build() -> tuple[bool, str]:
        exe = _build(args.dist_dir, args.work_dir, noarchive=False)
        count, size, _files = _dist_stats(args.dist_dir)
        detail = f"{exe}；{count} 文件 / {size / 2 ** 20:.1f} MiB"
        # 默认自动与「上一次 dist」比（TMP_ROOT 下 pyi-distN 里编号最大且
        # 不是本次的那个），不需要人肉写基准。
        base = args.diff_against or _prev_dist_dir(args.dist_dir)
        if base and os.path.isdir(base):
            added, removed, changed, lines = _diff_dists(base, args.dist_dir)
            detail += (f"；对 {os.path.basename(base)}："
                       f"+{len(added)} -{len(removed)} ~{len(changed)}")
            with open(os.path.join(RC_DIR, "dist_diff.txt"), "w",
                      encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            # exe 增长单独一行：是代码增长还是打包器/引导层变动，能归因就归因。
            old_exe = os.path.join(base, "MBDSDR", EXE_NAME)
            new_exe = os.path.join(args.dist_dir, "MBDSDR", EXE_NAME)
            if os.path.isfile(old_exe) and os.path.isfile(new_exe):
                old_sz = os.path.getsize(old_exe)
                new_sz = os.path.getsize(new_exe)
                # changed 是 (相对路径, 旧大小, 新大小) 三元组，不是字符串。
                mod_changes = [c[0] for c in changed
                               if not c[0].lower().endswith(".exe")]
                if mod_changes:
                    why = f"未归因（另有 {len(mod_changes)} 个模块/依赖文件同时变化）"
                else:
                    why = "打包器/引导层变动（模块文件无变化）"
                print(f"  {EXE_NAME} {old_sz:,} → {new_sz:,} B"
                      f"（{new_sz - old_sz:+,} B）；{why}", flush=True)
        return True, detail

    def do_smoke() -> tuple[bool, str]:
        if not log_path:
            return False, "启动日志无落点"
        return _step_smoke(_exe_of(args.dist_dir), log_path)

    diag_cache: dict[str, str] = {}

    def diag_exe() -> str:
        # 发布构建的纯模块在 PYZ 归档里，删不到目录；用 noarchive 诊断构建
        # （同一 spec，仅 MBDSDR_NOARCHIVE=1）把 scipy/_external 变成落盘文件。
        if args.reuse_diag and os.path.isfile(_exe_of(args.diag_dist_dir)):
            return _exe_of(args.diag_dist_dir)
        if "exe" not in diag_cache:
            diag_cache["exe"] = _build(args.diag_dist_dir, args.diag_work_dir,
                                       noarchive=True)
        return diag_cache["exe"]

    def do_subtree() -> tuple[bool, str]:
        exe = diag_exe()
        subtree = os.path.join(args.diag_dist_dir, "MBDSDR", "_internal",
                               "scipy", "_external")
        if not os.path.isdir(subtree):
            return False, ("noarchive 诊断构建里没有 "
                           f"{subtree}，无法模拟整棵子树缺失")
        backup = _rename_away(subtree)
        try:
            return _step_dialog(exe, log_path, EXPECT_TITLE_FAIL,
                                EXPECT_SUBTREE_BODY, None)
        finally:
            _restore(backup, subtree)
            if not os.path.isdir(subtree):
                raise RuntimeError(f"复原失败：{subtree}")

    def do_single() -> tuple[bool, str]:
        exe = diag_exe()
        target = os.path.join(args.diag_dist_dir, "MBDSDR", "_internal",
                              "scipy", "_external", "array_api_compat",
                              "numpy", "fft.pyc")
        if not os.path.isfile(target):
            return False, f"找不到 {target}"
        backup = _rename_away(target)
        try:
            return _step_dialog(exe, log_path, EXPECT_TITLE_FAIL,
                                EXPECT_SINGLE_BODY, None)
        finally:
            _restore(backup, target)
            if not os.path.isfile(target):
                raise RuntimeError(f"复原失败：{target}")

    def do_signal() -> tuple[bool, str]:
        """删整棵 ``scipy/signal`` 目录：目录行走探测生效后，这是唯一能证明
        ``scipy.signal`` 仍被启动自检覆盖的用例（旧期望已随 subtree/single
        的陈旧文本一起删掉，不能拿「少测一条」换绿）。"""
        exe = diag_exe()
        target = os.path.join(args.diag_dist_dir, "MBDSDR", "_internal",
                              "scipy", "signal")
        if not os.path.isdir(target):
            return False, f"找不到 {target}"
        backup = _rename_away(target)
        try:
            return _step_dialog(exe, log_path, EXPECT_TITLE_FAIL,
                                EXPECT_SIGNAL_BODY, None)
        finally:
            _restore(backup, target)
            if not os.path.isdir(target):
                raise RuntimeError(f"复原失败：{target}")

    def do_optional() -> tuple[bool, str]:
        exe = _exe_of(args.dist_dir)
        target = os.path.join(args.dist_dir, "MBDSDR", "_internal",
                              "librtlsdr.dll")
        if not os.path.isfile(target):
            return False, f"找不到 {target}"
        backup = _rename_away(target)
        try:
            return _step_dialog(exe, log_path, EXPECT_TITLE_DLL,
                                EXPECT_OPTIONAL_BODY, _clean_path_env())
        finally:
            _restore(backup, target)
            if not os.path.isfile(target):
                raise RuntimeError(f"复原失败：{target}")

    def do_qt() -> tuple[bool, str]:
        exe = _exe_of(args.dist_dir)
        target = os.path.join(args.dist_dir, "MBDSDR", "_internal", "PySide6",
                              "plugins", "platforms", "qwindows.dll")
        if not os.path.isfile(target):
            return False, f"找不到 {target}"
        backup = _rename_away(target)
        try:
            return _step_dialog(exe, log_path, EXPECT_TITLE_FAIL,
                                EXPECT_QT_BODY, None)
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
        roots = _product_log_roots(dist_dir=args.dist_dir)
        if args.skip_user_scan:
            return True, (f"产品日志落点扫描已跳过；声明根 {len(roots)} 个："
                          f"{'；'.join(roots)}；日志={log_path}")
        started = time.time()
        hits, scanned = _scan_userprofile_log_writes(
            run_start, roots=roots, full=bool(args.full_scan))
        elapsed = time.time() - started
        if args.full_scan:
            product = [h for h in hits if _is_product_attributed(h)]
            foreign = len(hits) - len(product)
            mode = (f"本次是**全盘**扫（用户目录 {os.path.expanduser('~')}，"
                    f"{elapsed:.1f}s，产品归属命中 {len(product)} 条/"
                    f"非产品 {foreign} 条已忽略）")
            root_text = f"全盘：{os.path.expanduser('~')}"
            existing = 1 if os.path.isdir(os.path.expanduser("~")) else 0
            roots = [os.path.expanduser("~")]
            hits = product
        else:
            mode = f"本次是声明落点扫（{elapsed:.1f}s）"
            root_text = "；".join(roots)
            existing = sum(1 for r in roots if os.path.isdir(r))
        if hits:
            return False, (f"产品日志落点出现本次写入（{mode}，扫 {scanned} 个文件，"
                           f"命中 {len(hits)}）：\n"
                           + "\n".join(hits[:20])
                           + f"\n扫描根（存在 {existing}/{len(roots)}）：{root_text}")
        return True, (f"产品日志落点：{mode}，声明 {len(roots)} 个根/存在 {existing} 个，"
                      f"共 {scanned} 个文件，命中 0"
                      f"（*.log/.log.N，mtime 过滤）；根：{root_text}；"
                      f"日志={log_path}")

    steps = (
        ("docs", do_docs),
        ("claims", do_claims),
        ("gen_reconcile", do_gen_reconcile),
        ("action_dup", do_action_dup),
        ("guards", do_guards),
        ("build", do_build),
        ("smoke", do_smoke),
        ("subtree", do_subtree),
        ("single", do_single),
        ("signal", do_signal),
        ("optional", do_optional),
        ("qt", do_qt),
        ("narrow", _step_narrow),
        ("overlap", _step_overlap),
        ("log", do_log),
    )
    warmed = [False]

    def warm_once() -> None:
        """预热一次（不计入判据）：build 成功后或 smoke 之前执行。"""
        if warmed[0]:
            return
        warmed[0] = True
        exe = _exe_of(args.dist_dir)
        if not os.path.isfile(exe):
            print(f"[预热] 跳过：{exe} 不存在（先跑 --release 出包）",
                  flush=True)
            return
        ok, detail = _step_warmup(exe)
        print(f"[预热] {'完成' if ok else '未完成'}：{detail}（不计入判据）",
              flush=True)

    fail_step: str | None = None
    for name, fn in steps:
        if name not in wanted:
            continue
        if name in ("smoke", "qt"):
            warm_once()
        run(name, fn)
        if name == "build" and results[-1]["status"] == "PASS":
            warm_once()
        if results[-1]["status"] == "FAIL":
            fail_step = name
            if not args.keep_going:
                # 失败即停：后面的步骤只会被同一缺陷污染，先修当前这条。
                print(f"下一步：{FAIL_HINTS.get(name, '按上面的失败信息排查')}",
                      flush=True)
                break

    done = {r["step"] for r in results}
    for name in ALL_STEPS:
        if name in wanted and name not in done:
            why = f"未执行（前序失败：{fail_step}）" if fail_step else "未执行（未选中）"
            results.append({"step": name, "status": "SKIP",
                            "reason_code": "not_run", "seconds": 0.0,
                            "detail": why})

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = [r["step"] for r in results if r["status"] == "FAIL"]
    skipped_rows = [r for r in results if r["status"] == "SKIP"]
    skipped = len(skipped_rows)
    total = sum(r["seconds"] for r in results)
    print("-" * 72)
    print(f"汇总：PASS {passed} / FAIL {len(failed)} / SKIP {skipped}  "
          f"｜总耗时 {total:.1f}s")
    if failed:
        print("FAIL 步骤：" + "、".join(failed))
    for r in skipped_rows:
        print(f"SKIP {r['step']}（{r['reason_code'] or 'unknown'}）：{r['detail']}")

    exit_code = 1 if failed else (2 if skipped else 0)
    payload = {
        "schema": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_seconds": round(total, 1),
        "summary": {"passed": passed, "failed": len(failed), "skipped": skipped,
                    "exit_code": exit_code, "fail_steps": failed,
                    "skip_steps": [{"step": r["step"],
                                    "reason_code": r["reason_code"]}
                                   for r in skipped_rows]},
        "steps": results,
    }
    out_json = os.path.join(RC_DIR, "result.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"结果：{out_json}")
    if failed:
        return 1
    # 只测了一半不能静默当绿：SKIP 用独立退出码，让 CI 一眼看见。
    return 2 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
