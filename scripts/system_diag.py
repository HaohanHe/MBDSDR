#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""SDR + GNSS 一条命令聚合自诊断。

    python scripts/system_diag.py                     # 自动选设备/串口
    python scripts/system_diag.py --sdr-device rtlsdr:77771111153705700 --gnss-port COM4
    python scripts/system_diag.py --json              # 机器可读（stdout 只有 JSON）
    python scripts/system_diag.py --timeout 5         # 两侧共用等待上限

两侧各自复用 ``scripts/sdr_diag.py`` / ``scripts/gnss_diag.py`` 的
``run_diagnostics()``：五段顺序、失败即停、JSON 字段与退出码全部沿用，
本脚本只做汇总，不重新实现任何一段。SDR 侧走 ``sdr_diag.run_in_subprocess``，
设备 I/O 只在 sdr_diag 的隐藏子进程里发生，聚合层父进程不持有任何设备句柄；
启动前同样做残留诊断进程预检（``--force`` 跳过）。

总体判定（只看两侧退出码是否 0）：

- ``ok``：两侧都通过（GNSS 天线 OPEN/SHORT 只是 warning，不算失败）；
- ``degraded``：一侧失败、一侧通过（退出码 2）；
- ``failed``：两侧都失败（退出码 1）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

# 允许从仓库根目录直接运行（与 scripts/sdr_diag.py 同一约定）。
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _path in (_ROOT, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import gnss_diag  # noqa: E402  （同目录脚本，复用其 run_diagnostics）
import sdr_diag  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_DEGRADED = 2
#: 预检发现本机已有诊断实例（可能是卡死残留），拒绝继续堆进程。
EXIT_DIAG_RESIDUAL = 5

#: 总体结论的展示文本。
OVERALL_TEXT: Dict[str, str] = {
    "ok": "两侧都通过",
    "degraded": "部分失败",
    "failed": "两侧都失败",
}

#: SDR verdict → 人话短句（与 sdr_diag.VERDICT_TEXT 互补：这里要短）。
SDR_PHRASE: Dict[str, str] = {
    "ok": "样本正常",
    "no_device": "未发现设备",
    "device_busy": "设备被占用",
    "owner_unknown": "占用状态未知",
    "connect_failed": "连接失败",
    "tune_failed": "调谐失败",
    "no_samples": "无样本（硬件数据通路）",
    "runner_error": "诊断执行异常",
}

#: GNSS verdict → 人话短句。
GNSS_PHRASE: Dict[str, str] = {
    "ok": "定位正常",
    "no_port": "未发现串口",
    "open_failed": "串口打不开",
    "no_nmea": "收不到 NMEA",
    "no_fix": "无有效定位",
    "no_utc": "缺完整 UTC",
    "runner_error": "诊断执行异常",
}

Runner = Callable[[argparse.Namespace], Any]


@dataclass
class SystemResult:
    sdr: Dict[str, Any]
    gnss: Dict[str, Any]
    verdict: str
    exit_code: int
    summary: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sdr": self.sdr,
            "gnss": self.gnss,
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# 子诊断运行器（可注入，测试不碰硬件）
# ---------------------------------------------------------------------------
def _run_sdr(args: argparse.Namespace) -> Any:
    # 设备 I/O 只在 sdr_diag 的隐藏子进程里发生，聚合层父进程不碰设备。
    return sdr_diag.run_in_subprocess(args)


def _run_gnss(args: argparse.Namespace) -> Any:
    return gnss_diag.run_diagnostics(args)


def _normalize(result: Any) -> Dict[str, Any]:
    """把 DiagResult 或假 dict 统一成可序列化的子结果。"""
    data = result.to_dict() if hasattr(result, "to_dict") else result
    data = dict(data or {})
    data.setdefault("verdict", "unknown")
    data.setdefault("exit_code", EXIT_FAILED)
    data["stages"] = list(data.get("stages") or [])
    return data


def _safe_run(runner: Runner, args: argparse.Namespace) -> Dict[str, Any]:
    """单侧崩溃不得吞掉另一侧；崩溃按失败记，并留下原因。"""
    try:
        return _normalize(runner(args))
    except Exception as exc:  # 诊断脚本自身 bug 也要如实记账
        return {
            "verdict": "runner_error",
            "exit_code": EXIT_FAILED,
            "stages": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def _side_ok(data: Dict[str, Any]) -> bool:
    """是否通过只看退出码：GNSS 天线 warning 时退出码仍是 0。"""
    try:
        return int(data.get("exit_code", EXIT_FAILED)) == EXIT_OK
    except (TypeError, ValueError):
        return False


def _sdr_phrase(data: Dict[str, Any]) -> str:
    verdict = str(data.get("verdict") or "unknown")
    return SDR_PHRASE.get(verdict, verdict)


def _gnss_phrase(data: Dict[str, Any]) -> str:
    verdict = str(data.get("verdict") or "unknown")
    phrase = GNSS_PHRASE.get(verdict, verdict)
    antenna = data.get("antenna") or {}
    status = str(antenna.get("status") or "").strip().lower()
    if antenna.get("warning"):
        if status == "open":
            phrase += "（天线开路）"
        elif status == "short":
            phrase += "（天线短路）"
    return phrase


def build_summary(sdr: Dict[str, Any], gnss: Dict[str, Any]) -> str:
    return f"SDR：{_sdr_phrase(sdr)}；GNSS：{_gnss_phrase(gnss)}"


# ---------------------------------------------------------------------------
# 聚合主流程
# ---------------------------------------------------------------------------
def _sub_args(module: Any, timeout: Optional[float], **overrides: Any) -> argparse.Namespace:
    """以子脚本自己的默认值为底，只覆盖聚合层暴露的参数。"""
    args = module.build_parser().parse_args([])
    if timeout is not None:
        args.timeout = float(timeout)
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def run_diagnostics(
    sdr_device: str = "",
    gnss_port: str = "",
    timeout: Optional[float] = None,
    *,
    sdr_runner: Optional[Runner] = None,
    gnss_runner: Optional[Runner] = None,
) -> SystemResult:
    """依次跑 SDR 与 GNSS，返回两侧原始结果 + 总体结论。

    ``timeout`` 为 None 时沿用两侧各自默认（SDR 2.0 s / GNSS 10.0 s）；
    给定值则两侧共用。
    """
    sdr_runner = sdr_runner or _run_sdr
    gnss_runner = gnss_runner or _run_gnss

    sdr = _safe_run(sdr_runner, _sub_args(sdr_diag, timeout, device=sdr_device))
    gnss = _safe_run(gnss_runner, _sub_args(gnss_diag, timeout, port=gnss_port))

    sdr_ok = _side_ok(sdr)
    gnss_ok = _side_ok(gnss)
    if sdr_ok and gnss_ok:
        verdict, code = "ok", EXIT_OK
    elif sdr_ok or gnss_ok:
        verdict, code = "degraded", EXIT_DEGRADED
    else:
        verdict, code = "failed", EXIT_FAILED

    return SystemResult(
        sdr=sdr, gnss=gnss, verdict=verdict, exit_code=code,
        summary=build_summary(sdr, gnss))


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def _print_stages(title: str, data: Dict[str, Any], module: Any) -> None:
    print(f"── {title} ──")
    if title == "SDR" and data.get("device"):
        print(f"设备：{data['device']}")
    if title == "GNSS" and data.get("port"):
        print(f"串口：{data['port']} @ {data.get('baud', '')} 8N1")
    for i, stage in enumerate(data.get("stages") or [], 1):
        tag = "PASS" if stage.get("ok") else "FAIL"
        name = str(stage.get("name") or "")
        label = module.STAGE_LABELS.get(name, name)
        print(f"  [{i}/{len(module.STAGE_NAMES)}] {label}（{name}） {tag}  "
              f"{stage.get('detail', '')}")
    if not data.get("stages"):
        error = data.get("error") or "未执行任何阶段"
        print(f"  [无阶段] {error}")
    side_text = module.VERDICT_TEXT.get(str(data.get("verdict")), str(data.get("verdict")))
    print(f"  结论：{side_text}（verdict={data.get('verdict')}，"
          f"退出码 {data.get('exit_code')}）")


def print_human(result: SystemResult) -> None:
    print("MBDSDR 系统自诊断（SDR + GNSS）")
    print()
    _print_stages("SDR", result.sdr, sdr_diag)
    print()
    _print_stages("GNSS", result.gnss, gnss_diag)
    print()
    print(f"总结：{result.summary}")
    print(f"总体：{OVERALL_TEXT.get(result.verdict, result.verdict)}"
          f"（verdict={result.verdict}，退出码 {result.exit_code}）")
    if result.exit_code != EXIT_OK:
        print("建议：")
        if not _side_ok(result.sdr):
            for line in sdr_diag.advice(str(result.sdr.get("verdict"))):
                print(f"  - [SDR] {line}")
        if not _side_ok(result.gnss):
            for line in gnss_diag.advice(str(result.gnss.get("verdict"))):
                print(f"  - [GNSS] {line}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="SDR + GNSS 聚合自诊断（一条命令跑完两侧五段检查）")
    ap.add_argument("--sdr-device", default="",
                    help="SDR 设备标识（同 sdr_diag.py --device）；默认第一个非 debug 设备")
    ap.add_argument("--gnss-port", default="",
                    help="GNSS 串口，例如 COM4；默认按 9600 自动探测")
    ap.add_argument("--timeout", type=float, default=None,
                    help="两侧共用的等待上限秒数；不指定时各用自身默认"
                         "（SDR 2.0 s / GNSS 10.0 s）")
    ap.add_argument("--force", action="store_true",
                    help="跳过「本机已有诊断实例」预检（默认拒绝启动，避免继续堆进程）")
    ap.add_argument("--json", action="store_true",
                    help="stdout 输出结构化 JSON（sdr/gnss/verdict/summary）")
    return ap


def _blocked_payload(found: List[Any]) -> Dict[str, Any]:
    pids = [int(pid) for pid, _ in found]
    return {
        "sdr": {"verdict": "diag_residual", "exit_code": EXIT_DIAG_RESIDUAL,
                "stages": []},
        "gnss": {"verdict": "skipped", "exit_code": EXIT_DIAG_RESIDUAL,
                 "stages": []},
        "verdict": "diag_residual",
        "exit_code": EXIT_DIAG_RESIDUAL,
        "summary": (f"发现本机已有 {len(pids)} 个诊断进程在运行"
                    f"（PID {'、'.join(str(p) for p in pids)}），拒绝继续堆进程"),
        "residual_pids": pids,
    }


def _emit_blocked(found: List[Any], as_json: bool) -> int:
    payload = _blocked_payload(found)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_DIAG_RESIDUAL
    print("MBDSDR 系统自诊断（SDR + GNSS）")
    print()
    print("已阻止启动：" + str(payload["summary"]))
    print("建议：")
    for line in sdr_diag.advice("diag_residual"):
        print(f"  - {line}")
    return EXIT_DIAG_RESIDUAL


def main(
    argv: Optional[List[str]] = None,
    *,
    sdr_runner: Optional[Runner] = None,
    gnss_runner: Optional[Runner] = None,
    process_provider: Optional[Callable[[], Any]] = None,
) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.timeout is not None and args.timeout <= 0:
        ap.error("--timeout 必须 > 0")

    injected = sdr_runner is not None or gnss_runner is not None
    # 测试注入运行器时不扫描真实进程表；显式给 process_provider 则视为要测预检。
    if not args.force and (process_provider is not None or not injected):
        stuck = sdr_diag.find_stuck_diag_processes(
            exclude_pids=[os.getpid()], provider=process_provider)
        if stuck:
            return _emit_blocked(stuck, args.json)

    result = run_diagnostics(
        args.sdr_device, args.gnss_port, args.timeout,
        sdr_runner=sdr_runner, gnss_runner=gnss_runner)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print_human(result)
    # 父进程没有卡死的驱动线程；普通返回即可，绝不在自己进程内硬杀。
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
