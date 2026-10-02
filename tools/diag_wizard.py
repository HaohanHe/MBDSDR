#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
MBDSDR 交互式诊断向导（diag wizard）
=====================================

输入 ``tools/hw_selfcheck/selfcheck.py --json`` 的机器可读报告，逐项解读
PASS / WARN / FAIL / SKIP，并把每一项问题**直接翻译成可复制粘贴的下一步
修复命令**——用户不用再去猜"报了 WARN 之后该敲什么"。

设计红线（与 selfcheck / parse_hw_report 一致）：
  * 纯 Python 标准库：云侧 / 真机都可能没有 numpy，本脚本绝不 import 项目包。
  * 只读 JSON：不安装系统包、不改 udev、不碰硬件；所有修复命令只是**打印出来
    给用户复制**，脚本自己绝不执行 sudo。
  * 无硬件诚实：报告显示未插设备时，明确告诉用户"这些 FAIL 属预期，不是机器坏了"。
  * 坏输入不崩：读不到有效 selfcheck JSON 时退出码 2 并给出明确提示。

输入方式（二选一）：
  python3 tools/diag_wizard.py report.json          # 从文件读
  python3 tools/diag_wizard.py < report.json        # 从 stdin 粘贴
  selfcheck.py --json | python3 tools/diag_wizard.py   # 流水线直连

用法：
  python3 tools/hw_selfcheck/selfcheck.py --json | python3 tools/diag_wizard.py
  python3 tools/diag_wizard.py report.json --json    # 额外输出结构化建议 JSON

退出码：
  0  报告有效，且无 FAIL（设备就绪 -> 给出 onboard 下一步；无硬件 -> 诚实空态）
  1  报告有效，但存在 FAIL（已逐项给出修复命令，修完重跑 selfcheck）
  2  坏输入（读不到有效 selfcheck --json 报告）
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

SELFCHECK_TOOL = "mbdsdr-hw-selfcheck"
ONBOARD_TOOL = "mbdsdr-onboarding"

# 需要的用户组（与 selfcheck.check_usb_udev 保持一致）
REQUIRED_GROUPS = ("plugdev", "dialout")

# onboard 一条命令的模式速查（参数核对自 onboard.MODES 默认值）
ONBOARD_MODE_HINTS = [
    ("adsb",  "1090e6",  "adsb",  None,                 "ADS-B 飞机报文（最容易出成果，先跑这个）"),
    ("apt",   "137.5e6", "apt",   "4800000",            "NOAA 气象卫星云图（需等卫星过境）"),
    ("ax25",  "144.39e6","ax25",  "4800000",            "APRS 位置报文"),
    ("cw",    "7020000", "cw",    "2400000",            "CW 莫尔斯电报"),
]


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class Advice:
    """单项检查的诊断结论 + 可复制命令。"""
    check: str                       # 检查名（完整）
    status: str = "WARN"              # PASS / WARN / FAIL / SKIP
    verdict: str = ""                 # 一句话结论
    commands: list[str] = field(default_factory=list)   # 可复制粘贴的修复命令
    note: str = ""                   # 补充说明


@dataclass
class WizardResult:
    advices: list[Advice] = field(default_factory=list)
    device_present: bool = False     # selfcheck 第 1 项是否命中 0bda:2838/2832
    fail_count: int = 0
    warn_count: int = 0
    pass_count: int = 0
    no_hardware_expected: bool = False   # 报告来自未插设备的机器（云 VM / 未插）
    next_steps: list[str] = field(default_factory=list)   # onboard + 回填建议
    summary_conclusion: str = ""


# ---------------------------------------------------------------------------
# 输入校验
# ---------------------------------------------------------------------------
def validate_report(data: Any) -> Optional[str]:
    """校验输入是否为有效的 selfcheck --json 报告。返回 None 表示通过，
    否则返回人类可读的错误原因（触发退出码 2）。"""
    if not isinstance(data, dict):
        return "顶层不是 JSON 对象（是 %s）" % type(data).__name__

    checks = data.get("checks")
    if not isinstance(checks, list) or not checks:
        # 常见情况：贴回来的是 onboard 报告
        if data.get("tool") == ONBOARD_TOOL or "steps" in data:
            return ("这是 onboard 报告（tool=%s），不是 selfcheck 报告。\n"
                    "  本向导只吃 `selfcheck.py --json` 的输出；\n"
                    "  先跑：python3 tools/hw_selfcheck/selfcheck.py --json"
                    % data.get("tool"))
        return ("JSON 里找不到 'checks' 列表——这不是 selfcheck --json 的输出。\n"
                "  请确认用了 `--json` 参数，并贴回了从第一个 { 到最后一个 } 的完整输出。")

    if "summary" not in data or not isinstance(data["summary"], dict):
        return "JSON 里找不到 'summary' 对象——报告可能被截断，请重贴完整输出。"
    return None


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _find_check(checks: list[dict], prefix: str) -> Optional[dict]:
    for c in checks:
        if isinstance(c, dict) and str(c.get("name", "")).startswith(prefix):
            return c
    return None


def _detail(check: dict) -> dict:
    d = check.get("detail")
    return d if isinstance(d, dict) else {}


_UDEV_RULE_BLOCK = """\
sudo tee /etc/udev/rules.d/99-mbdsdr-rtlsdr.rules >/dev/null <<'EOF'
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", MODE="0660", GROUP="plugdev"
EOF
sudo udevadm control --reload && sudo udevadm trigger"""


# ---------------------------------------------------------------------------
# 逐项诊断：把 selfcheck 的状态翻译成"下一步敲什么"
# ---------------------------------------------------------------------------
def diagnose_check1_usb(check: dict) -> Advice:
    d = _detail(check)
    hits = d.get("target_hits") or []
    groups = d.get("user_groups") or []
    rules = d.get("udev_rules_hit") or []
    status = check.get("status", "WARN")

    a = Advice(check=str(check.get("name", "1. USB/udev")), status=status)

    if hits:
        dev = hits[0] if isinstance(hits[0], dict) else {}
        a.verdict = "已枚举到设备 %s:%s" % (dev.get("vid", "?"), dev.get("pid", "?"))
        missing_groups = [g for g in REQUIRED_GROUPS if g not in groups]
        if status == "FAIL" and missing_groups:
            a.commands.append(
                "sudo usermod -aG plugdev,dialout $USER   # 然后注销/重新登录才生效")
            a.note = "设备在场，但当前用户不在 %s 组" % "/".join(missing_groups)
        elif not rules:
            a.commands.append(_UDEV_RULE_BLOCK)
            a.note = ("设备在场但未安装 udev 规则——重启或重插后可能变成 Permission denied。"
                      "复制上面整块命令写入规则并重载。")
        elif status == "PASS":
            a.verdict += "，用户组与 udev 规则就绪"
            a.note = "无需处理。"
    else:
        a.verdict = "未枚举到 RTL-SDR（0bda:2838/2832）"
        a.commands.append("lsusb | grep -E '0bda:(2838|2832)'   # 复核内核是否枚举到设备")
        a.commands.append("dmesg | tail -20   # 若看到 USB reset/enumerate 报错，换 USB2.0 直连口或换线")
        a.note = ("设备未插 / 未被内核枚举。云 VM、容器或未插硬件的开发机出现此 FAIL 属预期，"
                  "不是机器故障。")
    return a


def diagnose_check2_rtl_test(check: dict) -> Advice:
    d = _detail(check)
    status = check.get("status", "WARN")
    evidence = " ".join(check.get("evidence", []))
    a = Advice(check=str(check.get("name", "2. 设备枚举 rtl_test")), status=status)

    if d.get("skipped_reason") == "no_usb_device":
        a.verdict = "无设备，rtl_test 跳过（预期）"
        a.note = "USB 层没看到设备，本项不具备执行条件，插好设备后会自动复测。"
        return a

    if "未找到 rtl_test" in evidence:
        a.verdict = "缺少 rtl_test 命令（librtlsdr 未安装）"
        a.commands.append("sudo apt install rtl-sdr   # Debian/Ubuntu；源码编译见 repos/librtlsdr/")
        a.note = "selfcheck 与 onboard capture 步都靠 rtl_sdr/rtl_test 二进制。"
        return a

    parsed = d.get("parsed") or {}
    if parsed.get("no_supported_devices"):
        a.verdict = "USB 枚举到了但 rtl_test 打不开设备（No supported devices）"
        a.commands.append("sudo modprobe -r dvb_usb_rtl28xxu   # 释放被 DVB-T 电视驱动占用的 RTL-SDR")
        a.note = "常见根因：内核 dvb_usb_rtl28xxu 抢先绑定了设备。执行后重插 USB 再复测。"
        return a

    tuner = parsed.get("tuner")
    gains = parsed.get("gain_count")
    if tuner and gains is not None:
        a.verdict = "tuner=%s，增益 %s 档，正常" % (tuner, gains)
        a.note = "无需处理。"
    else:
        a.verdict = "rtl_test 跑了但没解析到 tuner 型号"
        a.commands.append("rtl_test -t   # 手动跑一遍观察完整 stderr")
        a.note = "多为固件未就绪 / USB 线质量差，重插或换线后复测。"
    return a


def diagnose_check3_stream(check: dict) -> Advice:
    d = _detail(check)
    status = check.get("status", "WARN")
    a = Advice(check=str(check.get("name", "3. 流读取 rtl_sdr 实读")), status=status)

    if d.get("skipped_reason") == "no_usb_device":
        a.verdict = "无设备，实读跳过（预期）"
        a.note = "插好设备后自动复测。"
        return a

    lost = d.get("lost_bytes_total") or 0
    bytes_written = d.get("tmp_file_bytes") or 0

    if bytes_written <= 0:
        a.verdict = "rtl_sdr 实读写空文件（采不到 IQ）"
        a.commands.append("pgrep -a rtl_tcp; pgrep -a gqrx; pgrep -a dump1090   # 找占用设备的进程")
        a.commands.append("# 杀掉占用进程，或直接拔出 RTL-SDR 重新插入，然后重跑 selfcheck")
        a.note = "常见根因：设备被其他 SDR 软件占用，或 udev 规则缺失导致无读权限。"
    elif lost > 0:
        a.verdict = "采到数据但有 %s 字节丢包（USB 带宽/调度压力）" % lost
        a.commands.append("# 把设备从 USB3.0 Hub 换到主板后置 USB2.0 直连口，再复测")
        a.note = "少量丢包不影响首次出成果；严重丢包时 onboard 会报字节数偏差。"
    elif status == "PASS":
        a.verdict = "3 秒实读 %s 字节、无丢包上报" % f"{bytes_written:,}"
        a.note = "无需处理。"
    else:
        a.verdict = "实读状态未明，请重跑 selfcheck --json 观察"
    return a


def diagnose_check4_soundcard(check: dict) -> Advice:
    a = Advice(check=str(check.get("name", "4. 声卡")), status=check.get("status", "WARN"))
    evidence = " ".join(check.get("evidence", []))
    if check.get("status") == "PASS":
        a.verdict = "声卡可用"
        a.note = "SSTV/SSB 监听需要；纯 IQ 采集模式不依赖。"
    elif "aplay / arecord 均不可用" in evidence:
        a.verdict = "alsa-utils 未安装（aplay/arecord 缺失）"
        a.commands.append("sudo apt install alsa-utils")
        a.note = "仅影响声卡检测与监听回放；RTL-SDR IQ 采集不依赖声卡。"
    else:
        a.verdict = "未检测到音频设备（无头机器常见）"
        a.note = "云 VM / 无声卡开发机出现 WARN 属预期。"
    return a


def diagnose_check5_gnss(check: dict) -> Advice:
    d = _detail(check)
    a = Advice(check=str(check.get("name", "5. GNSS 串口")), status=check.get("status", "WARN"))
    candidates = d.get("candidates") or []
    if not candidates:
        a.verdict = "未发现 /dev/ttyUSB* /dev/ttyACM*（GNSS 模块可选）"
        a.note = "GNSS 授时是可选增强：不接 GNSS 时 SigMF 时间戳自动退化为系统 UTC 并如实标注。"
    else:
        probes = d.get("probes") or []
        nmea_ok = any(p.get("ok") for p in probes if isinstance(p, dict))
        if nmea_ok:
            a.verdict = "GNSS 串口已输出 NMEA 语句"
            a.note = "onboard --gnss <nmea.log> 可把 GNSS UTC 写进 SigMF 时间戳。"
        else:
            a.verdict = "发现串口但未读到 NMEA（模块未定位/波特率不对）"
            a.commands.append("sudo cat /dev/ttyUSB0   # Ctrl-C 退出；肉眼看是否有 $ 开头行")
            a.note = "常见波特率 9600 / 38400 / 115200；模块需接天线并在室外定位。"
    return a


def diagnose_check6_deps(check: dict) -> Advice:
    d = _detail(check)
    a = Advice(check=str(check.get("name", "6. Python/系统依赖")), status=check.get("status", "WARN"))
    missing: list[str] = []
    if d.get("py_rtlsdr") is False:
        missing.append("python3 -m pip install --user pyrtlsdr   # 或 apt install python3-rtlsdr")
    if d.get("py_soapy") is False:
        missing.append("python3 -m pip install --user SoapySDR   # 可选，mbdsdr_ai 原型用到再装")
    if not d.get("gpsd"):
        missing.append("sudo apt install gpsd gpsd-clients   # GNSS 授时守护（可选）")

    if not missing:
        a.verdict = "Python 绑定与 gpsd 齐全"
        a.note = "无需处理。"
    else:
        a.verdict = "缺 %d 项依赖（影响 mbdsdr_ai Python 原型 / GNSS 守护，不影响 C++ 桌面端）" % len(missing)
        a.commands.extend(missing)
    return a


# ---------------------------------------------------------------------------
# 汇总决策
# ---------------------------------------------------------------------------
DIAGNOSERS = [
    ("1.", diagnose_check1_usb),
    ("2.", diagnose_check2_rtl_test),
    ("3.", diagnose_check3_stream),
    ("4.", diagnose_check4_soundcard),
    ("5.", diagnose_check5_gnss),
    ("6.", diagnose_check6_deps),
]


def decide_next_steps(res: WizardResult) -> None:
    """设备就绪且无 FAIL 时，给出 onboard 一条命令 + 论文回填路径。"""
    if res.fail_count > 0 or not res.device_present:
        return

    res.next_steps.append("=" * 56)
    res.next_steps.append("设备就绪！一条命令跑通首条链路（ADS-B 最容易出成果）：")
    res.next_steps.append("  python3 tools/onboarding/onboard.py --step all --freq 1090e6 --mode adsb")
    res.next_steps.append("")
    res.next_steps.append("其他模式速查（参数核对自 tools/onboarding/onboard.py）：")
    for mode, freq, mode_flag, n, desc in ONBOARD_MODE_HINTS:
        narg = f" --n {n}" if n else ""
        res.next_steps.append(f"  --freq {freq:>9} --mode {mode_flag:<6}{narg}   # {desc}")
    res.next_steps.append("")
    res.next_steps.append("onboard 产物默认落在：paper/experiments/onboarding_<mode>_<时间戳>/")
    res.next_steps.append("（内含 *.sigmf-data + *.sigmf-meta + *_messages.json）")
    res.next_steps.append("")
    res.next_steps.append("把录制目录回填成论文 recorded 口径指标（CSV + 图）：")
    res.next_steps.append("  python3 experiments/exp_ota_run.py \\")
    res.next_steps.append("      --recordings-dir paper/experiments/onboarding_<mode>_<时间戳>")
    res.next_steps.append("  # 产出：paper/experiments/ota_recorded_metrics.csv + figures/")


def build_result(report: dict) -> WizardResult:
    checks = report.get("checks", []) if isinstance(report.get("checks"), list) else []
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}

    res = WizardResult(summary_conclusion=str(summary.get("conclusion", "")))

    # 先看设备是否在场（第 1 项 detail.target_hits）
    c1 = _find_check(checks, "1.") or {}
    d1 = _detail(c1)
    res.device_present = bool(d1.get("target_hits"))

    for prefix, fn in DIAGNOSERS:
        c = _find_check(checks, prefix)
        if c is None:
            continue
        res.advices.append(fn(c))

    counts = summary.get("counts", {}) if isinstance(summary.get("counts"), dict) else {}
    res.fail_count = int(counts.get("FAIL", 0))
    res.warn_count = int(counts.get("WARN", 0))
    res.pass_count = int(counts.get("PASS", 0))

    # 无硬件场景：FAIL 来自"没插设备"，诚实标注为预期
    if not res.device_present and res.fail_count > 0:
        res.no_hardware_expected = True

    decide_next_steps(res)
    return res


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
_COLOR = {"PASS": "\033[32m", "WARN": "\033[33m", "FAIL": "\033[31m", "SKIP": "\033[90m"}
_RESET = "\033[0m"


def render_human(res: WizardResult, report: dict) -> str:
    lines: list[str] = []
    bar = "=" * 72
    lines.append(bar)
    lines.append("MBDSDR 诊断向导")
    lines.append(f"主机：{report.get('host', '?')}  OS：{report.get('os', '?')}  "
                 f"时间：{report.get('timestamp', '?')}")
    lines.append(bar)

    if res.no_hardware_expected:
        lines.append("")
        lines.append("[提示] 本报告来自未插 RTL-SDR 的机器（云 VM / 开发机）。")
        lines.append("       下面的 FAIL 均为'未插设备'导致，属预期状态，不是机器故障。")
        lines.append("       真机流程：插设备到 USB2.0 直连口 -> 重跑 selfcheck --json -> 重跑本向导。")

    for a in res.advices:
        color = _COLOR.get(a.status, "")
        lines.append("")
        lines.append(f"[{color}{a.status}{_RESET}] {a.check}")
        lines.append(f"  结论：{a.verdict}")
        if a.note:
            lines.append(f"  说明：{a.note}")
        for cmd in a.commands:
            # 命令块逐行打印，每行前缀 $ 便于整段复制
            for i, line in enumerate(cmd.splitlines()):
                lines.append(f"    $ {line}" if i == 0 else f"      {line}")

    lines.append("")
    lines.append("-" * 72)
    lines.append(f"汇总：PASS={res.pass_count}  WARN={res.warn_count}  FAIL={res.fail_count}")
    lines.append(f"selfcheck 结论：{res.summary_conclusion}")

    if res.next_steps:
        lines.append("")
        lines.extend(res.next_steps)
    elif res.fail_count > 0:
        lines.append("")
        lines.append("下一步：按上面带 $ 的命令逐项修复，然后重跑：")
        lines.append("  python3 tools/hw_selfcheck/selfcheck.py --json | python3 tools/diag_wizard.py")
    lines.append(bar)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="MBDSDR 交互式诊断向导：读 selfcheck --json，逐项给出可复制修复命令")
    ap.add_argument("path", nargs="?", help="selfcheck --json 报告文件路径（缺省从 stdin 读）")
    ap.add_argument("--json", action="store_true", help="额外输出结构化建议 JSON 到 stdout")
    args = ap.parse_args(argv)

    if args.path:
        try:
            with open(args.path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            print(f"[ERROR] 无法读取文件 {args.path}: {e}", file=sys.stderr)
            return 2
    else:
        text = sys.stdin.read()

    text = text.strip()
    if not text:
        print("[ERROR] 输入为空。请用以下任一方式提供 selfcheck --json 报告：",
              file=sys.stderr)
        print("  python3 tools/diag_wizard.py report.json", file=sys.stderr)
        print("  python3 tools/hw_selfcheck/selfcheck.py --json | python3 tools/diag_wizard.py",
              file=sys.stderr)
        return 2

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[ERROR] 输入不是合法 JSON：{e}", file=sys.stderr)
        print("  请确认：1) 跑 selfcheck 时加了 --json 参数；2) 贴回的是从第一个 { 到最后一个 } 的完整输出。",
              file=sys.stderr)
        return 2

    err = validate_report(data)
    if err:
        print(f"[ERROR] {err}", file=sys.stderr)
        return 2

    report = data if isinstance(data, dict) else {}
    res = build_result(report)

    print(render_human(res, report))
    if args.json:
        print("\n--- structured JSON ---")
        print(json.dumps({
            "device_present": res.device_present,
            "no_hardware_expected": res.no_hardware_expected,
            "counts": {"PASS": res.pass_count, "WARN": res.warn_count, "FAIL": res.fail_count},
            "advices": [asdict(a) for a in res.advices],
            "next_steps": res.next_steps,
        }, ensure_ascii=False, indent=2))

    return 1 if res.fail_count > 0 else 0


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGINT, lambda *_: sys.exit(130))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    sys.exit(main())
