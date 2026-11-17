#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""SDR 五段自诊断：定位「能连上但 0 样本」这类硬件/驱动问题。

    python scripts/sdr_diag.py                          # 第一个非 debug 设备
    python scripts/sdr_diag.py --device rtlsdr:77771111153705700
    python scripts/sdr_diag.py --freq 100100000 --rate 2400000 --timeout 2.0
    python scripts/sdr_diag.py --json                   # 机器可读（stdout 只有 JSON）

五段顺序固定：枚举 → 占用登记 → 连接/打开 → 调谐/采样率 → 有界读样本。
任何一段失败即停止（后续阶段不会伪造 PASS），退出码见 ``EXIT_*``。

为什么要单列「占用登记」：设备被另一个进程（桌面端 / 另一个 CLI / SDR#）
持有时，枚举仍然看得到，但打开会拿到 LIBUSB_ERROR_ACCESS(-3)。把它与
「驱动打不开」分开，才能给出正确的补救动作。

有界读复用 :mod:`mbdsdr_ai.bounded_read`：pyrtlsdr 的 ``read_bytes`` 没有
timeout 形参，真机实测可 12 秒不返回。设备枚举/打开/调谐/读取全部放在同文件
的隐藏子进程入口（``--_worker``）里执行：父进程只按 ``--timeout`` 加启动宽限
等待，超时只终止子进程；终止后 2 s 仍存活会在结果里标 ``residual=true``。
启动前还会扫描本机已有诊断实例并在发现时拒绝启动（``--force`` 跳过），
避免继续堆进程、把 USB 句柄锁死。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# 允许从仓库根目录直接运行（与 scripts/rtl_selfcheck.py 同一约定）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.diag_guard import (  # noqa: E402
    RESIDUAL_GRACE_S, WORKER_STARTUP_GRACE_S, find_stuck_diag_processes,
    run_worker, write_json_atomic)

EXIT_OK = 0
EXIT_NO_DEVICE = 1
EXIT_BUSY = 2
EXIT_CONNECT_FAILED = 3
EXIT_NO_SAMPLES = 4
#: 预检发现本机已有诊断实例（或正在烧 CPU 的残留进程），拒绝继续堆进程。
#: 与 0/1/2/3/4 语义不冲突；``--force`` 可跳过预检。
EXIT_DIAG_RESIDUAL = 5

#: 固定五段；顺序是契约（测试按它断言）。
STAGE_NAMES: Tuple[str, ...] = ("enumerate", "claim", "open", "tune", "read")

STAGE_LABELS: Dict[str, str] = {
    "enumerate": "枚举",
    "claim": "占用登记",
    "open": "连接/打开",
    "tune": "调谐/采样率",
    "read": "有界读样本",
}

VERDICT_TEXT: Dict[str, str] = {
    "ok": "五段全部通过，设备能正常输出样本",
    "no_device": "未发现可用 SDR 设备",
    "device_busy": "设备正被其它程序占用",
    "owner_unknown": "无法判断设备占用状态（占用登记不可读）",
    "connect_failed": "设备能被枚举，但连接/打开失败",
    "tune_failed": "设备已打开，但调谐/采样率设置失败",
    "no_samples": "设备已连接并配置成功，但有界读拿不到样本",
    "worker_timeout": "设备子进程未在等待上限内返回（已尝试终止；未拿到样本）",
    "runner_error": "诊断子进程异常退出，没有产出可用结果",
    "diag_residual": "本机已有诊断实例在运行（可能是卡死残留），拒绝继续堆进程",
}

#: 与真机复现口径一致：2 s 内读 512 字节（= 256 个 cu8 复样本）。
READ_BYTES = 512
READ_SAMPLES = READ_BYTES // 2


@dataclass
class Stage:
    """一段诊断结果。``name`` 用稳定的英文键，展示层再翻中文。"""

    name: str
    ok: bool
    detail: str
    elapsed_ms: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class DiagResult:
    stages: List[Stage]
    verdict: str
    exit_code: int
    device: str = ""
    #: 设备子进程终止后仍存活（很可能还占着 USB 句柄）。
    residual: bool = False
    #: 执行设备 I/O 的子进程 PID（未走隔离时为空）。
    worker_pid: int = 0
    #: 预检拦截时发现的诊断进程 PID 列表。
    residual_pids: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device": self.device,
            "stages": [s.to_dict() for s in self.stages],
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "residual": self.residual,
            "worker_pid": self.worker_pid,
            "residual_pids": list(self.residual_pids),
        }


# ---------------------------------------------------------------------------
# 默认依赖（可注入，测试不碰硬件）
# ---------------------------------------------------------------------------
def _default_owner_state(info: Any) -> Any:
    """查询设备占用登记（只读，不写登记、不打开设备）。"""
    from mbdsdr_ai.device_identity import IDENTITY_STABLE, device_identity_key
    from mbdsdr_ai.device_owner import get_owner_state

    identity = dict(getattr(info, "identity", {}) or {})
    if not identity and (getattr(info, "driver", "") or "").lower() == "rtlsdr":
        from mbdsdr_ai.sdr_backend import rtlsdr_native_entry
        identity = rtlsdr_native_entry(
            {"index": getattr(info, "enum_index", 0),
             "serial": getattr(info, "serial", "")})
    key, quality, _warning = device_identity_key(identity)
    if not key:
        raise ValueError("设备缺少统一身份信息，无法查询占用登记；请重新枚举")
    return get_owner_state(key, allow_alias_match=quality != IDENTITY_STABLE)


def _is_busy_error(exc: BaseException) -> bool:
    try:
        from mbdsdr_ai.error_handler import (
            DeviceBusyError, OwnerRegistryUnavailableError)
    except Exception:  # pragma: no cover - 产品错误树不可用时按普通失败处理
        return False
    return isinstance(exc, (DeviceBusyError, OwnerRegistryUnavailableError))


# ---------------------------------------------------------------------------
# 设备选择
# ---------------------------------------------------------------------------
def _is_debug(info: Any) -> bool:
    driver = (getattr(info, "driver", "") or "").lower()
    return driver in {"debug", "test", "null"} or driver.startswith("debug")


def _device_text(info: Any) -> str:
    key = str(getattr(info, "device_key", "") or "")
    name = str(getattr(info, "name", "") or "未知设备")
    return f"{name} [{key}]" if key else name


def select_device(devices: List[Any], spec: str = "") -> Tuple[Optional[Any], str]:
    """按 ``--device`` 选一台；空 spec = 第一个非 debug 设备。

    返回 ``(device, reason)``；失败时 device 为 None、reason 是给人看的原因。
    """
    candidates = [d for d in (devices or []) if not _is_debug(d)]
    if not candidates:
        return None, "枚举结果里没有真实 SDR 设备（只有调试/测试源或一台都没有）"
    spec = (spec or "").strip()
    if not spec:
        return candidates[0], ""
    for info in candidates:
        serial = str(getattr(info, "serial", "") or "")
        names = {
            str(getattr(info, "device_key", "") or ""),
            str(getattr(info, "name", "") or ""),
            serial,
            str(getattr(info, "enum_index", "")),
        }
        if serial:
            names.add(f"{getattr(info, 'driver', '')}:{serial}")
        if spec in names:
            return info, ""
    return None, f"枚举中找不到 --device {spec}"


# ---------------------------------------------------------------------------
# 诊断主流程
# ---------------------------------------------------------------------------
def _stage(name: str, started: float, ok: bool, detail: str) -> Stage:
    return Stage(name=name, ok=ok, detail=detail,
                 elapsed_ms=round((time.monotonic() - started) * 1000.0, 1))


def run_diagnostics(
    args: argparse.Namespace,
    *,
    list_devices: Optional[Callable[[], List[Any]]] = None,
    open_device: Optional[Callable[[Any], Any]] = None,
    probe_owner: Optional[Callable[[Any], Any]] = None,
    progress_path: str = "",
) -> DiagResult:
    """跑完五段并返回结构化结果；任何一段失败即停止并给出退出码。

    ``progress_path`` 只给子进程入口用：每段结束时原子写一次进度，父进程
    超时终止子进程后仍能如实指出「断在哪一段」。
    """
    stages: List[Stage] = []
    device_label = ""

    def record(st: Stage) -> None:
        stages.append(st)
        if progress_path:
            write_json_atomic(progress_path, {
                "device": device_label,
                "stages": [s.to_dict() for s in stages],
            })

    def finish(verdict: str, code: int) -> DiagResult:
        return DiagResult(stages=stages, verdict=verdict,
                          exit_code=code, device=device_label)

    # 依赖注入：测试传假实现；生产路径共用同一台 DeviceManager。
    if list_devices is None or open_device is None:
        from mbdsdr_ai.device_manager import DeviceManager
        manager = DeviceManager()
        list_devices = list_devices or manager.list_devices
        open_device = open_device or manager.open
    probe_owner = probe_owner or _default_owner_state

    # ── 1/5 枚举 ───────────────────────────────────────────────────────
    started = time.monotonic()
    try:
        devices = list(list_devices() or [])
    except Exception as exc:
        record(_stage("enumerate", started, False,
                      f"枚举抛异常：{type(exc).__name__}: {exc}"))
        return finish("no_device", EXIT_NO_DEVICE)

    info, reason = select_device(devices, getattr(args, "device", "") or "")
    if info is None:
        detail = reason
        if devices:
            detail += "；当前可见：" + "、".join(_device_text(d) for d in devices)
        record(_stage("enumerate", started, False, detail))
        return finish("no_device", EXIT_NO_DEVICE)

    device_label = _device_text(info)
    detail = f"发现 {len(devices)} 台，选中 {device_label}"
    warning = str(getattr(info, "identity_warning", "") or "")
    if warning:
        detail += f"；注意：{warning}"
    record(_stage("enumerate", started, True, detail))

    # ── 2/5 占用登记 ───────────────────────────────────────────────────
    started = time.monotonic()
    if (getattr(info, "driver", "") or "").lower() == "rtltcp":
        record(_stage("claim", started, True,
                      "网络源（rtl_tcp）：不参与本机 USB 占用登记"))
    else:
        try:
            from mbdsdr_ai.device_owner import (
                OWNER_STATE_FREE, OWNER_STATE_OWNED, owner_state_text)
            state = probe_owner(info)
            text = owner_state_text(state)
        except Exception as exc:
            record(_stage(
                "claim", started, False,
                f"占用登记不可读，无法判断是否被占用：{type(exc).__name__}: {exc}"))
            return finish("owner_unknown", EXIT_BUSY)
        if state.state == OWNER_STATE_FREE:
            record(_stage("claim", started, True, text))
        else:
            verdict = "device_busy" if state.state == OWNER_STATE_OWNED else "owner_unknown"
            record(_stage("claim", started, False, text))
            return finish(verdict, EXIT_BUSY)

    # ── 3/5 连接/打开 ──────────────────────────────────────────────────
    started = time.monotonic()
    try:
        device = open_device(info)
    except Exception as exc:
        detail = f"打开失败：{type(exc).__name__}: {exc}"
        record(_stage("open", started, False, detail))
        if _is_busy_error(exc):
            return finish("device_busy", EXIT_BUSY)
        return finish("connect_failed", EXIT_CONNECT_FAILED)
    record(_stage("open", started, True, f"已打开 {device_label}"))

    # 从这里开始必须保证设备被关闭（close() 同时归还占用登记）。
    try:
        # ── 4/5 调谐/采样率 ────────────────────────────────────────────
        started = time.monotonic()
        rate = float(getattr(args, "rate", 2_400_000.0))
        freq = float(getattr(args, "freq", 100_100_000.0))
        try:
            device.set_sample_rate(rate)
            device.set_frequency(freq)
        except Exception as exc:
            record(_stage(
                "tune", started, False,
                f"设置采样率 {rate/1e6:.3f} MS/s、频率 {freq/1e6:.3f} MHz 失败："
                f"{type(exc).__name__}: {exc}"))
            return finish("tune_failed", EXIT_CONNECT_FAILED)

        detail = f"已下发 采样率 {rate/1e6:.3f} MS/s、中心频率 {freq/1e6:.3f} MHz"
        try:
            backend = getattr(device, "_backend", None)
            actual_rate = getattr(backend, "sample_rate", None)
            actual_freq = getattr(backend, "center_freq", None)
            if actual_rate is not None and actual_freq is not None:
                detail += (f"；驱动回读 {float(actual_rate)/1e6:.3f} MS/s、"
                           f"{float(actual_freq)/1e6:.3f} MHz")
        except Exception:
            detail += "；驱动不支持回读（精确值未知）"
        record(_stage("tune", started, True, detail))

        # ── 5/5 有界读样本 ─────────────────────────────────────────────
        started = time.monotonic()
        timeout = float(getattr(args, "timeout", 2.0))
        from mbdsdr_ai.bounded_read import BoundedReader
        reader = BoundedReader(
            lambda n: device.read_samples(n), timeout,
            name="mbdsdr-sdr-diag-read")
        try:
            samples, code, message = reader.read(READ_SAMPLES)
        except Exception as exc:
            reason = getattr(device, "last_read_reason", None) or {}
            extra = f"（{reason.get('message')}）" if reason.get("message") else ""
            record(_stage(
                "read", started, False,
                f"有界读抛异常：{type(exc).__name__}: {exc}{extra}"))
            return finish("no_samples", EXIT_NO_SAMPLES)
        finally:
            reader.close()

        if samples is None or len(samples) == 0:
            reason = getattr(device, "last_read_reason", None) or {}
            why = reason.get("message") or ""
            if not why and code == "read_timeout":
                why = (f"{timeout:.1f} s 内读 {READ_BYTES} 字节"
                       f"（{READ_SAMPLES} 样本）无返回")
            if not why:
                why = message or "驱动返回空数组，无更多原因"
            record(_stage(
                "read", started, False,
                f"未拿到样本（上限 {timeout:.1f} s）：{why}"))
            return finish("no_samples", EXIT_NO_SAMPLES)

        import numpy as np
        arr = np.asarray(samples)
        level = float(np.sqrt(np.mean(np.abs(arr) ** 2)) + 1e-12)
        level_db = 20.0 * math.log10(level)
        record(_stage(
            "read", started, True,
            f"读到 {len(arr)} 个样本（{len(arr) * 2} 字节），"
            f"平均功率 {level_db:.1f} dBFS"))
        return finish("ok", EXIT_OK)
    finally:
        try:
            device.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def advice(verdict: str) -> List[str]:
    """按失败段给出可执行排查动作（只写与该段证据相符的）。"""
    if verdict == "no_device":
        return [
            "插好设备并换一个 USB 口（主板直连，别经 HUB）；换一根数据线。",
            "确认驱动：Windows 用 Zadig 把 RTL2832U 装成 WinUSB；"
            "Linux 黑名单 dvb_usb_rtl28xxu 后重新插拔。",
            "关掉其它可能独占设备的程序（SDR#/GQRX/rtl_fm/另一个 MBDSDR）。",
            "用 rtl_test 交叉验证：若官方工具也看不到设备，问题在设备/驱动/USB。",
        ]
    if verdict in {"device_busy", "owner_unknown"}:
        return [
            "关掉其它占用 SDR 的程序（SDR#、GQRX、CubicSDR、rtl_fm/rtl_tcp、"
            "另一个 MBDSDR 实例）后重跑。",
            "占用登记不可读时，检查登记文件/权限；本脚本不会替你强抢设备。",
        ]
    if verdict == "connect_failed":
        return [
            "确认驱动：Windows 用 Zadig 装 WinUSB；Linux 黑名单 dvb_usb_rtl28xxu。",
            "换 USB 口/线，排除供电不足与劣质 HUB。",
            "用 rtl_test 交叉验证打开是否成功，区分库/驱动问题与设备问题。",
        ]
    if verdict == "tune_failed":
        return [
            "确认频率/采样率在设备规格内（RTL-SDR：24 MHz–1.764 GHz，0.225–3.2 MS/s）。",
            "关掉其它占用程序后重试；仍失败时用 rtl_test 验证驱动是否支持该配置。",
        ]
    if verdict == "no_samples":
        return [
            "换 USB 口/线（优先 USB2.0 直连，不经 HUB），再跑同一条命令。",
            "确认驱动匹配：Windows 用 Zadig 装 WinUSB；Linux 黑名单 dvb_usb_rtl28xxu。",
            "关掉其它占用 SDR 的程序（SDR#、GQRX、CubicSDR、rtl_fm/rtl_tcp、"
            "另一个 MBDSDR 实例）。",
            "用 rtl_test -s 2400000 与 rtl_sdr.exe -f 100100000 -s 2400000 -n 256 out.bin "
            "交叉验证：官方工具同样 0 字节，说明数据通路在驱动/USB 层，"
            "不能据此断定本软件坏了。",
        ]
    if verdict == "worker_timeout":
        return [
            "诊断子进程没有在 --timeout + 宽限内返回，父进程已终止它；"
            "没有样本证据，故沿用「无样本」退出码 4。",
            "若结果里 residual=true：子进程终止后仍存活，很可能仍占着 USB 句柄。"
            "重启系统，或拔插 RTL-SDR 让驱动释放句柄后再试。",
            "换 USB 口/线（USB2.0 直连），并用 rtl_test -s 2400000 交叉验证驱动是否也会卡住。",
        ]
    if verdict == "runner_error":
        return [
            "诊断子进程异常退出，未产出结果；这不是「设备无样本」的证据。",
            "先确认脚本本身可运行：python scripts/sdr_diag.py --timeout 2",
            "如无输出，检查 Python 环境/依赖（pyrtlsdr、numpy）是否完整。",
        ]
    if verdict == "diag_residual":
        return [
            "结束残留诊断进程：任务管理器 → 详细信息 → 按 PID 结束；"
            "或 PowerShell: Stop-Process -Id <PID> -Force。",
            "若进程杀不掉（已损坏并占着 USB 句柄）：重启系统，"
            "或拔插 RTL-SDR 让驱动释放句柄。",
            "确认本机没有其它诊断在跑后再重试；确定不需要该检查时可加 --force。",
        ]
    return []


def print_human(result: DiagResult) -> None:
    print("MBDSDR SDR 自诊断（五段）")
    if result.device:
        print(f"设备：{result.device}")
    total = len(STAGE_NAMES)
    for i, st in enumerate(result.stages, 1):
        tag = "PASS" if st.ok else "FAIL"
        label = STAGE_LABELS.get(st.name, st.name)
        print(f"  [{i}/{total}] {label}（{st.name}） {tag}  "
              f"{st.detail}  ({st.elapsed_ms:.1f} ms)")
    executed = {st.name for st in result.stages}
    missing = [STAGE_LABELS[n] for n in STAGE_NAMES if n not in executed]
    if missing:
        print(f"  [跳过] 未执行：{'、'.join(missing)}（上游阶段失败）")
    print(f"\n结论：{VERDICT_TEXT.get(result.verdict, result.verdict)}"
          f"（退出码 {result.exit_code}）")
    if result.residual_pids:
        print("已有诊断进程 PID：" + "、".join(str(p) for p in result.residual_pids))
    if result.residual:
        print(f"警告：诊断子进程 PID {result.worker_pid} 终止后 "
              f"{RESIDUAL_GRACE_S:.0f} s 内仍存活（residual=true）；"
              "需重启系统或拔插设备释放 USB 句柄，该 PID 可能仍在烧 CPU。")
    if result.exit_code != EXIT_OK:
        print("建议：")
        for line in advice(result.verdict):
            print(f"  - {line}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="SDR 五段自诊断（枚举→占用登记→连接/打开→调谐/采样率→有界读样本）")
    ap.add_argument("--device", default="",
                    help="设备标识，例如 rtlsdr:77771111153705700；"
                         "默认第一个非 debug 设备")
    ap.add_argument("--freq", type=float, default=100_100_000.0,
                    help="中心频率 Hz，默认 100.1 MHz")
    ap.add_argument("--rate", type=float, default=2_400_000.0,
                    help="采样率 Hz，默认 2.4 MS/s")
    ap.add_argument("--timeout", type=float, default=2.0,
                    help="单次有界读的等待上限秒数，默认 2.0 s；"
                         "设备 I/O 子进程的硬上限是它再加启动宽限")
    ap.add_argument("--force", action="store_true",
                    help="跳过「本机已有诊断实例」预检（默认拒绝启动，避免继续堆进程）")
    ap.add_argument("--json", action="store_true",
                    help="stdout 输出结构化 JSON（stages[] + verdict），不加建议文本")
    # 隐藏入口：父进程用真子进程跑设备 I/O；该模式不打印、不走预检。
    ap.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--_result", default="", help=argparse.SUPPRESS)
    return ap


# ---------------------------------------------------------------------------
# 子进程隔离（父进程绝不碰设备）
# ---------------------------------------------------------------------------
def _result_from_dict(data: Dict[str, Any]) -> DiagResult:
    stages: List[Stage] = []
    for item in data.get("stages") or []:
        try:
            stages.append(Stage(
                name=str(item.get("name") or ""),
                ok=bool(item.get("ok")),
                detail=str(item.get("detail") or ""),
                elapsed_ms=float(item.get("elapsed_ms") or 0.0),
            ))
        except Exception:
            continue
    return DiagResult(
        stages=stages,
        verdict=str(data.get("verdict") or ""),
        exit_code=int(data.get("exit_code") or 0),
        device=str(data.get("device") or ""),
        residual=bool(data.get("residual")),
    )


def _worker_failure_result(outcome: Any, timeout_s: float) -> DiagResult:
    """子进程超时/崩溃且没有完整结果时，按已完成的阶段如实收口。"""
    data = outcome.result or {}
    result = _result_from_dict(data) if data.get("stages") else DiagResult(
        stages=[], verdict="", exit_code=EXIT_NO_SAMPLES,
        device=str(data.get("device") or ""))
    completed = {st.name for st in result.stages}
    pending = next((name for name in STAGE_NAMES if name not in completed), "")
    if outcome.timed_out:
        verdict = "worker_timeout"
        detail = (f"诊断子进程（PID {outcome.pid}）在 {timeout_s:.1f}s + 宽限 "
                  f"{WORKER_STARTUP_GRACE_S:.1f}s 内未返回")
        if outcome.residual:
            detail += (f"；terminate 后 {RESIDUAL_GRACE_S:.0f}s 仍存活"
                       "（residual=true；需重启系统或拔插设备释放 USB 句柄）")
        else:
            detail += "；已终止子进程"
    else:
        verdict = "runner_error"
        detail = f"诊断子进程异常退出（returncode={outcome.returncode}）"
        tail = (getattr(outcome, "stderr_tail", "") or "").strip()
        if tail:
            detail += f"：{tail.splitlines()[-1][:300]}"
    if pending:
        result.stages.append(Stage(
            name=pending, ok=False, detail=detail,
            elapsed_ms=round(float(getattr(outcome, "elapsed_s", 0.0)) * 1000.0, 1)))
    result.verdict = verdict
    result.exit_code = EXIT_NO_SAMPLES
    result.residual = bool(getattr(outcome, "residual", False))
    result.worker_pid = int(getattr(outcome, "pid", 0) or 0)
    return result


def run_in_subprocess(args: argparse.Namespace) -> DiagResult:
    """把设备枚举/打开/调谐/读取全部交给同文件的隐藏子进程入口。"""
    timeout_s = float(getattr(args, "timeout", 2.0))
    fd, result_path = tempfile.mkstemp(
        prefix="mbdsdr-sdr-diag-", suffix=".json")
    os.close(fd)
    command = [
        sys.executable, os.path.abspath(__file__),
        "--_worker", "--_result", result_path,
        "--device", str(getattr(args, "device", "") or ""),
        "--freq", repr(float(getattr(args, "freq", 100_100_000.0))),
        "--rate", repr(float(getattr(args, "rate", 2_400_000.0))),
        "--timeout", repr(timeout_s),
    ]
    try:
        outcome = run_worker(command, timeout_s, result_path)
        data = outcome.result or {}
        if data.get("verdict") is not None and data.get("verdict") != "":
            # 正常返回，或「结果已写完后解释器被驱动线程挂住」——两种都用
            # 子进程写出的完整结果；后者只额外标注 residual。
            result = _result_from_dict(data)
            result.worker_pid = int(outcome.pid or 0)
            result.residual = bool(outcome.residual)
            return result
        return _worker_failure_result(outcome, timeout_s)
    finally:
        try:
            os.unlink(result_path)
        except OSError:
            pass


def _blocked_result(found: List[Tuple[int, str]]) -> DiagResult:
    pids = [int(pid) for pid, _ in found]
    return DiagResult(stages=[], verdict="diag_residual",
                      exit_code=EXIT_DIAG_RESIDUAL, residual_pids=pids)


def _emit_result(result: DiagResult, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print_human(result)


def main(
    argv: Optional[List[str]] = None,
    *,
    list_devices: Optional[Callable[[], List[Any]]] = None,
    open_device: Optional[Callable[[Any], Any]] = None,
    probe_owner: Optional[Callable[[Any], Any]] = None,
    process_provider: Optional[Callable[[], Any]] = None,
) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.timeout <= 0:
        ap.error("--timeout 必须 > 0")

    if args._worker:
        # 隐藏入口：只在这个真子进程里碰设备。不打印、不预检。
        result = run_diagnostics(args, progress_path=args._result)
        if args._result:
            write_json_atomic(args._result, result.to_dict())
        return int(result.exit_code)

    injected = any(x is not None for x in
                   (list_devices, open_device, probe_owner))
    # 测试注入依赖时不扫描真实进程表；显式给 process_provider 则视为要测预检。
    if not args.force and (process_provider is not None or not injected):
        stuck = find_stuck_diag_processes(
            exclude_pids=[os.getpid()], provider=process_provider)
        if stuck:
            result = _blocked_result(stuck)
            _emit_result(result, args.json)
            return result.exit_code

    if injected:
        result = run_diagnostics(
            args, list_devices=list_devices, open_device=open_device,
            probe_owner=probe_owner)
    else:
        result = run_in_subprocess(args)
    _emit_result(result, args.json)
    # 父进程没有卡死的驱动线程；普通返回即可，绝不在自己进程内硬杀。
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
