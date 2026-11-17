#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""GNSS 五段自诊断：定位「串口开了但没定位 / 没有完整 UTC」这类接入问题。

    python scripts/gnss_diag.py                      # 自动探测串口（按 --baud）
    python scripts/gnss_diag.py --port COM4
    python scripts/gnss_diag.py --port COM4 --baud 9600 --timeout 10
    python scripts/gnss_diag.py --json                # 机器可读（stdout 只有 JSON）

五段顺序固定：端口打开 → 收到 NMEA → 有效定位 → 时间完整性（UTC 日期）→
天线状态。任何一段失败即停止（后续阶段不会伪造 PASS），退出码见 ``EXIT_*``。

与 ``scripts/sdr_diag.py`` 对称：只回答「链路断在哪一段」，不评估信号质量。

天线状态是**观测**不是门槛：GPTXT 报 ``ANTENNA OPEN`` / ``ANTENNA SHORT``
时该段仍记 PASS（脚本的退出码不因天线告警而变），但会在结论里明确写成
warning——「没收到告警」与「天线正常」是两件事，未收到明确状态词时如实
记 ``unknown``。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

# 允许从仓库根目录直接运行（与 scripts/sdr_diag.py 同一约定）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EXIT_OK = 0
EXIT_NO_PORT = 1
EXIT_OPEN_FAILED = 2
EXIT_NO_NMEA = 3
EXIT_NO_FIX = 4
EXIT_NO_UTC = 5

#: 固定五段；顺序是契约（测试按它断言）。
STAGE_NAMES: Tuple[str, ...] = ("open", "nmea", "fix", "utc", "antenna")

STAGE_LABELS: Dict[str, str] = {
    "open": "端口打开",
    "nmea": "收到 NMEA",
    "fix": "有效定位",
    "utc": "时间完整性",
    "antenna": "天线状态",
}

VERDICT_TEXT: Dict[str, str] = {
    "ok": "五段全部通过，串口能输出可用的 GNSS 定位与完整 UTC",
    "no_port": "未发现可用 GNSS 串口",
    "open_failed": "串口存在但打不开（被占用/无权限/设备异常）",
    "no_nmea": "串口已打开，但收不到合法 NMEA 语句",
    "no_fix": "收到 NMEA，但没有有效定位",
    "no_utc": "有定位，但缺少完整的 UTC 日期",
}

#: 轮询间隔（秒）：串口是 ~1 Hz 的 NMEA 流，50 ms 足够，又不空转烧 CPU。
POLL_INTERVAL = 0.05

#: 定位段补采 GGA 统计的窗口（秒）：坐标常先由 RMC 给出，GGA 的星数/质量
#: 还在同一历元前面；不补这一下，证据里会出现「有坐标但 satellites=None」。
FIX_STATS_WAIT = 2.0

#: 天线段的额外等待上限（秒）：GPTXT 常在完整历元的开头，刚连上时往往还没到；
#: 给它 3 s（>2 个 1 Hz 历元）就能看到，没有状态词时也不至于干等满 --timeout。
ANTENNA_WAIT = 3.0

#: 天线告警状态（来自 GPTXT）：只对这些值记 warning。
ANTENNA_WARNINGS = ("open", "short")


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
    port: str = ""
    baud: int = 0
    fix: Dict[str, Any] = None  # type: ignore[assignment]
    antenna: Dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.fix is None:
            self.fix = {}
        if self.antenna is None:
            self.antenna = {"status": "unknown", "text": None, "warning": False}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "port": self.port,
            "baud": self.baud,
            "stages": [s.to_dict() for s in self.stages],
            "fix": self.fix,
            "antenna": self.antenna,
            "verdict": self.verdict,
            "exit_code": self.exit_code,
        }


# ---------------------------------------------------------------------------
# 默认依赖（可注入，测试不碰硬件）
# ---------------------------------------------------------------------------
def _default_list_ports() -> List[Dict[str, Any]]:
    from mbdsdr_ai.serial_gnss import SerialGNSSReader
    return SerialGNSSReader.list_candidate_ports()


def _default_make_reader() -> Any:
    from mbdsdr_ai.serial_gnss import SerialGNSSReader
    return SerialGNSSReader()


def _fix_view(fix: Dict[str, Any]) -> Dict[str, Any]:
    """把 reader 的 fix 投影成稳定的 JSON 字段（不把整个内部快照原样吐出）。"""
    fix = fix or {}
    return {
        "source": fix.get("source"),
        "latitude": fix.get("latitude"),
        "longitude": fix.get("longitude"),
        "altitude_m": fix.get("altitude_m"),
        "satellites": fix.get("satellites"),
        "hdop": fix.get("hdop"),
        "fix_quality": fix.get("fix_quality"),
        "utc_time": fix.get("utc_time"),
        "utc_datetime": fix.get("utc_datetime"),
        "date": fix.get("date"),
    }


def _has_fix(fix: Dict[str, Any]) -> bool:
    """有效定位 = 收到真实语句 且 经纬度都有值（不拿 0,0 或旧坐标冒充）。"""
    return (str((fix or {}).get("source") or "") == "real"
            and (fix or {}).get("latitude") is not None
            and (fix or {}).get("longitude") is not None)


def _has_utc(fix: Dict[str, Any]) -> bool:
    """完整 UTC = 有日期（ZDA 或 RMC 的 ddmmyy），只有 hhmmss 不算。"""
    return bool(str((fix or {}).get("utc_datetime") or "").strip())


# ---------------------------------------------------------------------------
# 诊断主流程
# ---------------------------------------------------------------------------
def _stage(name: str, started: float, ok: bool, detail: str) -> Stage:
    return Stage(name=name, ok=ok, detail=detail,
                 elapsed_ms=round((time.monotonic() - started) * 1000.0, 1))


def _poll(get_fix: Callable[[], Dict[str, Any]],
          predicate: Callable[[Dict[str, Any]], bool],
          timeout: float) -> Tuple[Dict[str, Any], bool]:
    """在 ``timeout`` 秒内轮询 ``get_fix()`` 直到 ``predicate`` 成立。

    至少调用一次，便于「刚打开串口就已经有缓存 fix」的场景立即通过。
    """
    deadline = time.monotonic() + max(float(timeout), 0.0)
    fix = get_fix() or {}
    while True:
        if predicate(fix):
            return fix, True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return fix, False
        time.sleep(min(POLL_INTERVAL, remaining))
        fix = get_fix() or {}


def run_diagnostics(
    args: argparse.Namespace,
    *,
    make_reader: Optional[Callable[[], Any]] = None,
    list_ports: Optional[Callable[[], List[Dict[str, Any]]]] = None,
) -> DiagResult:
    """跑完五段并返回结构化结果；任何一段失败即停止并给出退出码。"""
    stages: List[Stage] = []
    port_arg = str(getattr(args, "port", "") or "").strip()
    baud = int(getattr(args, "baud", 9600) or 9600)
    timeout = float(getattr(args, "timeout", 10.0))
    result = DiagResult(stages=stages, verdict="", exit_code=EXIT_OK,
                        baud=baud)

    def finish(verdict: str, code: int) -> DiagResult:
        result.verdict = verdict
        result.exit_code = code
        return result

    # 依赖注入：测试传假实现；生产路径共用同一个 SerialGNSSReader。
    if make_reader is None:
        make_reader = _default_make_reader
    if list_ports is None:
        list_ports = _default_list_ports

    # ── 1/5 端口打开（含自动探测） ─────────────────────────────────────
    started = time.monotonic()
    port = port_arg
    detected_note = ""
    reader = None
    if not port:
        try:
            candidates = list(list_ports() or [])
        except Exception as exc:
            candidates = []
            candidate_error = f"枚举串口抛异常：{type(exc).__name__}: {exc}"
        else:
            candidate_error = ""
        names = [str(c.get("device") or "") for c in candidates]
        names = [name for name in names if name]
        if not names:
            detail = candidate_error or "系统中没有任何串口设备"
            stages.append(_stage("open", started, False, detail))
            return finish("no_port", EXIT_NO_PORT)
        # 有候选就逐个真打开。不能只信 auto_detect()：它把「被占用/无权限」
        # 吞成 None，会把 open_failed 谎报成 no_port（Task 114）。
        errors: List[str] = []
        for candidate in names:
            attempt = make_reader()
            try:
                opened = bool(attempt.start(candidate, baud))
            except Exception as exc:
                opened = False
                errors.append(
                    f"{candidate}: {type(exc).__name__}: {exc}")
            else:
                if not opened:
                    last = getattr(attempt, "last_error", None)
                    errors.append(
                        f"{candidate}: {last or '打开失败（端口不存在、被其它程序占用或无权限）'}")
            if opened:
                reader = attempt
                port = candidate
                detected_note = "（自动探测命中）"
                break
            try:
                attempt.stop()
            except Exception:
                pass
        if reader is None:
            detail = (f"候选串口 {len(names)} 个（{'、'.join(names)}）"
                      f"逐个打开均失败：{errors[-1]}；"
                      "请关闭占用该端口的程序后重试")
            stages.append(_stage("open", started, False, detail))
            return finish("open_failed", EXIT_OPEN_FAILED)

    if reader is None:
        reader = make_reader()
    result.port = port
    try:
        if not detected_note:
            try:
                opened = bool(reader.start(port, baud))
            except Exception as exc:
                stages.append(_stage(
                    "open", started, False,
                    f"打开 {port}@{baud} 抛异常：{type(exc).__name__}: {exc}"))
                return finish("open_failed", EXIT_OPEN_FAILED)
            if not opened:
                last = getattr(reader, "last_error", None)
                suffix = f"（{last}）" if last else ""
                stages.append(_stage(
                    "open", started, False,
                    f"打开 {port}@{baud} 失败{suffix}："
                    "端口不存在、被其它程序占用或无权限"))
                return finish("open_failed", EXIT_OPEN_FAILED)
        stages.append(_stage(
            "open", started, True,
            f"已打开 {port} @ {baud} 8N1{detected_note}"))

        # 从这里开始必须保证串口被关闭（reader.stop() 会 join 读线程）。
        # ── 2/5 收到 NMEA ──────────────────────────────────────────────
        started = time.monotonic()
        fix, got_nmea = _poll(
            reader.get_fix,
            lambda f: str(f.get("source") or "") == "real",
            timeout)
        if not got_nmea:
            result.fix = _fix_view(fix)
            stages.append(_stage(
                "nmea", started, False,
                f"在 {timeout:.1f} s 内未收到任何合法 NMEA 语句"
                f"（检查波特率是否为 {baud}、TX/RX 是否接反、模块是否上电）"))
            return finish("no_nmea", EXIT_NO_NMEA)
        result.fix = _fix_view(fix)
        stages.append(_stage(
            "nmea", started, True,
            "已收到合法 NMEA（校验和通过），fix.source=real"))

        # ── 3/5 有效定位 ───────────────────────────────────────────────
        started = time.monotonic()
        fix, got_fix = _poll(reader.get_fix, _has_fix, timeout)
        if not got_fix:
            result.fix = _fix_view(fix)
            stages.append(_stage(
                "fix", started, False,
                f"收到 NMEA 但 {timeout:.1f} s 内仍无有效定位："
                f"satellites={fix.get('satellites')}, "
                f"fix_quality={fix.get('fix_quality')}（天线/天空视野/冷启动）"))
            return finish("no_fix", EXIT_NO_FIX)
        if fix.get("satellites") is None and not fix.get("fix_quality"):
            # 只补一次短窗口：GGA 缺失的模块不会因此多等一整个 --timeout。
            fix, _ = _poll(
                reader.get_fix,
                lambda f: f.get("satellites") is not None
                or bool(f.get("fix_quality")),
                min(timeout, FIX_STATS_WAIT))
        result.fix = _fix_view(fix)
        stats = []
        if fix.get("satellites") is not None:
            stats.append(f"卫星 {fix.get('satellites')}")
        if fix.get("fix_quality") is not None:
            stats.append(f"fix_quality={fix.get('fix_quality')}")
        if fix.get("hdop") is not None:
            stats.append(f"hdop={fix.get('hdop')}")
        suffix = "，" + "，".join(stats) if stats else "（本历元尚未收到 GGA 统计）"
        stages.append(_stage(
            "fix", started, True,
            f"已定位 {fix.get('latitude'):.6f}, {fix.get('longitude'):.6f}"
            f"{suffix}"))

        # ── 4/5 时间完整性（UTC 日期） ─────────────────────────────────
        started = time.monotonic()
        fix, got_utc = _poll(reader.get_fix, _has_utc, timeout)
        if not got_utc:
            result.fix = _fix_view(fix)
            stages.append(_stage(
                "utc", started, False,
                f"有定位但 {timeout:.1f} s 内没有完整 UTC："
                f"仅有 hhmmss={fix.get('utc_time')!r}，"
                "缺 ZDA 或有效 RMC 日期（ddmmyy）"))
            return finish("no_utc", EXIT_NO_UTC)
        result.fix = _fix_view(fix)
        stages.append(_stage(
            "utc", started, True,
            f"完整 UTC：{fix.get('utc_datetime')}（date={fix.get('date')}）"))

        # ── 5/5 天线状态（观测项，不改变退出码） ───────────────────────
        # 刚连上时可能落在历元中段（GPTXT/GGA 已过），先给一个短窗口等状态词。
        started = time.monotonic()
        antenna_wait = min(timeout, ANTENNA_WAIT)
        fix, _ = _poll(
            reader.get_fix,
            lambda f: str(f.get("antenna") or "").strip().lower()
            in ("open", "short", "ok"),
            antenna_wait)
        result.fix = _fix_view(fix)
        status = str(fix.get("antenna") or "unknown").strip().lower()
        text = fix.get("antenna_text")
        warning = status in ANTENNA_WARNINGS
        result.antenna = {"status": status, "text": text, "warning": warning}
        if status == "open":
            detail = ("GPTXT 告警 ANTENNA OPEN（天线开路/未接天线）："
                      f"{text or '模块报天线开路'}")
        elif status == "short":
            detail = (f"GPTXT 告警 ANTENNA SHORT（天线短路）："
                      f"{text or '模块报天线短路'}")
        elif status == "ok":
            detail = "GPTXT 报 ANTENNA OK"
        else:
            detail = (f"{antenna_wait:.1f} s 内未收到明确的 GPTXT 天线状态"
                      "（不把“没有告警”当作“天线正常”）")
        stages.append(_stage("antenna", started, True, detail))
        return finish("ok", EXIT_OK)
    finally:
        try:
            reader.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def advice(verdict: str) -> List[str]:
    """按失败段给出可执行排查动作（只写与该段证据相符的）。"""
    if verdict == "no_port":
        return [
            "插好 GNSS 模块，换一个 USB 口/线（优先主板直连，不经 HUB）。",
            "在设备管理器确认出现「USB-SERIAL CH340 (COMx)」，记下口名后加 --port。",
            "若模块不是 9600，显式指定波特率：--baud 38400 / 115200。",
        ]
    if verdict == "open_failed":
        return [
            "关掉其它占用串口的程序（串口助手、另一个 MBDSDR、Arduino IDE 等）。",
            "确认端口号仍存在（拔插后 COM 号可能变化）；无权限时用普通用户重新插拔。",
            "换 USB 口/线，排除 CH340 供电或接触不良。",
        ]
    if verdict == "no_nmea":
        return [
            "确认波特率：模块出厂常见 9600，RTK 模块常为 38400/115200。",
            "确认 TX/RX 交叉接线正确、模块已上电（GNSS 模块上电到出语句可能要数秒）。",
            "用串口助手以 8N1 打开同一端口，确认能看到 $GNGGA/$GNRMC 文本。",
        ]
    if verdict == "no_fix":
        return [
            "把天线放到室外/窗边，冷启动首次定位可能需要 30 s–几分钟。",
            "检查天线与馈线：GPTXT 若报 ANTENNA OPEN/SHORT 先修天线链路。",
            "确认星数在增长（satellites 字段）；长期为 0 说明射频前端/天线有问题。",
        ]
    if verdict == "no_utc":
        return [
            "确认模块是否输出 ZDA/RMC；部分模块默认关闭 RMC 或在无效状态下发空日期。",
            "定位有效后再等一个历元（1 Hz 下约 1 s），RMC 日期才会填实。",
            "用模块配置工具打开 RMC（含 ddmmyy）或 ZDA 语句后重跑。",
        ]
    return []


def print_human(result: DiagResult) -> None:
    print("MBDSDR GNSS 自诊断（五段）")
    if result.port:
        print(f"串口：{result.port} @ {result.baud} 8N1")
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
    antenna = result.antenna or {}
    if antenna.get("warning"):
        print(f"警告：天线状态 {str(antenna.get('status')).upper()}——"
              "定位可用性可能受影响（不影响本次退出码）")
    if result.exit_code != EXIT_OK:
        print("建议：")
        for line in advice(result.verdict):
            print(f"  - {line}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="GNSS 五段自诊断（端口打开→收到 NMEA→有效定位→"
                    "时间完整性→天线状态）")
    ap.add_argument("--port", default="",
                    help="串口名，例如 COM4 或 /dev/ttyUSB0；"
                         "默认按 --baud 自动探测")
    ap.add_argument("--baud", type=int, default=9600,
                    help="波特率，默认 9600（8N1）")
    ap.add_argument("--timeout", type=float, default=10.0,
                    help="等待 NMEA/定位/UTC 的单段上限秒数，默认 10.0 s")
    ap.add_argument("--json", action="store_true",
                    help="stdout 输出结构化 JSON（stages[]/fix/antenna/verdict）")
    return ap


def main(
    argv: Optional[List[str]] = None,
    *,
    make_reader: Optional[Callable[[], Any]] = None,
    list_ports: Optional[Callable[[], List[Dict[str, Any]]]] = None,
) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.timeout <= 0:
        ap.error("--timeout 必须 > 0")
    if args.baud <= 0:
        ap.error("--baud 必须 > 0")
    result = run_diagnostics(
        args, make_reader=make_reader, list_ports=list_ports)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print_human(result)
    try:
        sys.stdout.flush()
    except Exception:
        pass
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
