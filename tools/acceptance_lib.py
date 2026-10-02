#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
MBDSDR 真机端到端演练 · 纯逻辑库（checklist 组装 / 分类 / 校验）
================================================================

``tools/acceptance_run.sh`` 是真正分步执行的 bash 入口；本模块把其中**可测的
决策逻辑**全部抽成纯函数，既被 shell 通过 CLI 子命令调用，也被
``tools/test_acceptance_run.py`` 离线单测。红线与全仓一致：

  * 不碰硬件、不联网、不 mock 设备：设备是否在场只从 ``selfcheck --json`` 报告里
    读 ``checks[0].detail.target_hits``；没有就是没有，绝不编造。
  * 无硬件诚实空态：device_present=False 时，下游 onboard/ota 一律 SKIP 并写明
    跳过原因，退出码 2（这不是失败，是云 VM / 未插机器的预期状态）。
  * 纯 Python 标准库：与 diag_wizard/parse_hw_report 一致，云侧不一定有 numpy。

退出码约定（与 selfcheck/diag_wizard/onboard 既有口径对齐）：
  0  演练全绿：设备在场，且无任何演练步 FAIL（onboard decode 出 0 帧属诚实结果，
     不判 FAIL——通过口径是 capture/record PASS + SigMF 落盘，见 phase10 手册）。
  1  设备在场但至少一个演练步 FAIL（真机环境真有问题，需按提示修复）。
  2  未检测到 RTL-SDR 设备：下游全部 SKIP，诚实空态（云 VM / 未插机器）。
  3  用法 / 输入错误（raw JSONL 损坏、引用的报告文件缺失等）。

CLI 子命令（shell 与手动调试共用）：
  python3 tools/acceptance_lib.py device-present <selfcheck.json>
      # 打印 true/false
  python3 tools/acceptance_lib.py assemble --raw <steps.jsonl> --out <checklist.json>
      # 读 raw 行（每行一个步的原始结果），组装成最终检查表 JSON；退出码=检查表结论
  python3 tools/acceptance_lib.py validate <checklist.json>
      # 校验检查表结构；退出 0=合法，1=缺字段/状态非法
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import sys
from typing import Any, Optional

TOOL = "mbdsdr-acceptance-run"
VERSION = "0.1.0"

# 退出码
EXIT_OK = 0          # 设备在场且全绿
EXIT_HW_FAIL = 1     # 设备在场但有步 FAIL
EXIT_NO_HW = 2       # 无设备（诚实空态）
EXIT_USAGE = 3       # 用法/输入错误

# 演练步允许的状态
ALLOWED_STATUS = ("PASS", "FAIL", "SKIP")


# ---------------------------------------------------------------------------
# 1. 设备在场判定（只读 selfcheck --json 报告）
# ---------------------------------------------------------------------------
def device_present_from_selfcheck(report: dict) -> bool:
    """从 selfcheck --json 报告判定是否枚举到目标 RTL-SDR。

    selfcheck 第 1 项名以 "1." 开头，detail.target_hits 非空即在场。
    任何形状异常都返回 False（绝不猜成有设备）。
    """
    if not isinstance(report, dict):
        return False
    checks = report.get("checks")
    if not isinstance(checks, list):
        return False
    for c in checks:
        if not isinstance(c, dict):
            continue
        if str(c.get("name", "")).startswith("1."):
            detail = c.get("detail")
            if isinstance(detail, dict):
                hits = detail.get("target_hits")
                return bool(isinstance(hits, list) and len(hits) > 0)
    return False


# ---------------------------------------------------------------------------
# 2. onboard --json 结果分类（按 phase10 手册通过口径）
# ---------------------------------------------------------------------------
def onboard_outcome(onboard_report: dict) -> dict:
    """把 onboard.py --json 的 steps 列表翻译成演练步结论。

    通过口径（phase10/P1-acceptance-runbook.md 步骤②）：
      * detect FAIL            -> 演练步 FAIL（设备没被认到 / 工具缺失）
      * capture FAIL           -> 演练步 FAIL（空文件 / 占用 / 越界参数）
      * record PASS 落盘 SigMF -> 演练步 PASS（decode 是否解出 0 帧是诚实信号
                                  结果，不判环境故障；仅记入 key_output）
      * 其余                   -> 演练步 FAIL
    """
    steps_raw = onboard_report.get("steps") if isinstance(onboard_report, dict) else None
    steps: dict[str, dict] = {}
    if isinstance(steps_raw, list):
        for s in steps_raw:
            if isinstance(s, dict) and s.get("step"):
                steps[str(s["step"])] = s

    def _st(name: str) -> str:
        s = steps.get(name)
        return str(s.get("status", "")) if isinstance(s, dict) else ""

    detect = steps.get("detect", {}) if isinstance(steps.get("detect"), dict) else {}
    capture = steps.get("capture", {}) if isinstance(steps.get("capture"), dict) else {}
    record = steps.get("record", {}) if isinstance(steps.get("record"), dict) else {}
    decode = steps.get("decode", {}) if isinstance(steps.get("decode"), dict) else {}

    rd = record.get("detail") if isinstance(record.get("detail"), dict) else {}
    sigmf_data = str(rd.get("sigmf_data", "")) if isinstance(rd, dict) else ""

    if _st("record") == "PASS" and sigmf_data:
        status = "PASS"
    elif _st("detect") == "FAIL":
        status = "FAIL"
    elif _st("capture") == "FAIL":
        status = "FAIL"
    else:
        status = "FAIL"

    dec_detail = decode.get("detail") if isinstance(decode.get("detail"), dict) else {}
    return {
        "status": status,
        "detect": _st("detect"),
        "capture": _st("capture"),
        "record": _st("record"),
        "decode": _st("decode"),
        "decode_message": str(decode.get("message", "")),
        "n_frames": dec_detail.get("n_frames", 0) if isinstance(dec_detail, dict) else 0,
        "sigmf_data": sigmf_data,
        "sigmf_dir": os.path.dirname(sigmf_data) if sigmf_data else "",
        "detect_message": str(detect.get("message", "")),
    }


# ---------------------------------------------------------------------------
# 3. exp_ota_run 输出分类
# ---------------------------------------------------------------------------
def ota_outcome(ota_stdout: str) -> str:
    """从 exp_ota_run.py 的 stdout 判定回填步结论。

      * 空态（"未发现任何 .sigmf-data" / "空态"） -> SKIP（无录制可回填）
      * 写了 CSV（"行，口径=recorded" / ota_recorded_metrics.csv） -> PASS
      * 其他 -> FAIL（理论上 exp_ota_run 恒退出 0；这里只认输出事实）
    """
    if not isinstance(ota_stdout, str):
        return "SKIP"
    if not ota_stdout.strip():
        # exp_ota_run 恒打印空态或 CSV 行；空输出=没有可评估的录制，归空态
        return "SKIP"
    if "空态" in ota_stdout or "未发现任何" in ota_stdout:
        return "SKIP"
    if "ota_recorded_metrics.csv" in ota_stdout or "行，口径=recorded" in ota_stdout:
        return "PASS"
    return "FAIL"


# ---------------------------------------------------------------------------
# 4. 工具：读文件 / 截断日志尾
# ---------------------------------------------------------------------------
def _read_text(path: str, max_bytes: int = 4096) -> Optional[str]:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(max_bytes)
    except OSError:
        return None


def _tail(text: Optional[str], n: int = 500) -> str:
    if not text:
        return ""
    text = text.strip()
    return text[-n:] if len(text) > n else text


# ---------------------------------------------------------------------------
# 5. assemble：把 raw 步结果组装成最终检查表
# ---------------------------------------------------------------------------
def _step_from_raw(raw: dict) -> dict:
    """单个 raw 行 -> 最终 steps[] 里的一项（含 status/skip_reason/key_output）。

    raw 行由 acceptance_run.sh 写出，约定字段：
      id/title（必有）；rc（int，可空）；
      selfcheck_report（selfcheck 步：报告路径）；
      log（diag/ota 步：stdout 日志路径）+ paste_block(bool, diag 步)；
      onboard_json（onboard 步：onboard --json 路径）；
      recordings_dir（ota 步：喂入的录制目录）；
      skipped(bool)+skip_reason（无硬件分支由 shell 直接标记 SKIP）；
      manual(bool)（时空视图人工步）。
    """
    sid = str(raw.get("id", "?"))
    title = str(raw.get("title", sid))
    item: dict[str, Any] = {
        "id": sid,
        "title": title,
        "status": "SKIP",
        "rc": raw.get("rc"),
        "skip_reason": None,
        "key_output": "",
    }

    # 人工步 / shell 已标记 SKIP
    if raw.get("manual"):
        item["status"] = "SKIP"
        item["skip_reason"] = "人工核对步：脚本只输出操作提示，不自动验证桌面端"
        item["key_output"] = str(raw.get("hint", ""))
        return item
    if raw.get("skipped"):
        item["status"] = "SKIP"
        item["skip_reason"] = str(raw.get("skip_reason", "上游未通过，跳过"))
        return item

    if sid == "selfcheck":
        rep_path = str(raw.get("selfcheck_report", ""))
        rep = {}
        txt = _read_text(rep_path)
        parse_ok = False
        if txt:
            try:
                rep = json.loads(txt)
                parse_ok = True
            except json.JSONDecodeError:
                rep = {}
        # 演练步"selfcheck"只问：自检脚本是否跑通并产出可解析报告。
        # rc 0/1 都表示跑通（差异仅在环境有无 FAIL）；rc==2 才是脚本自身出错。
        # "有没有设备"由 device_present 单独表达，不在这里误判成步 FAIL。
        rc = raw.get("rc")
        item["status"] = "PASS" if (parse_ok and rc in (0, 1)) else "FAIL"
        summ = rep.get("summary", {}) if isinstance(rep, dict) else {}
        counts = summ.get("counts", {}) if isinstance(summ, dict) else {}
        item["key_output"] = (
            f"counts={counts}；结论={summ.get('conclusion', '')}"
        )
        item["artifact"] = rep_path

    elif sid == "diag_wizard_paste":
        log = _read_text(str(raw.get("log", "")))
        has_fence = bool(raw.get("paste_block"))
        item["status"] = "PASS" if has_fence else "FAIL"
        item["skip_reason"] = None if has_fence else "向导未产出围栏回传块"
        item["key_output"] = _tail(log)
        item["artifact"] = str(raw.get("log", ""))

    elif sid.startswith("onboard_"):
        oj = _read_text(str(raw.get("onboard_json", "")))
        report = {}
        if oj:
            try:
                report = json.loads(oj)
            except json.JSONDecodeError:
                report = {}
        oc = onboard_outcome(report)
        item["status"] = oc["status"]
        item["key_output"] = (
            f"detect={oc['detect']} capture={oc['capture']} "
            f"record={oc['record']} decode={oc['decode']} "
            f"n_frames={oc['n_frames']}；{oc['decode_message']}"
        )
        if oc["sigmf_data"]:
            item["artifact"] = oc["sigmf_data"]
        if oc["status"] == "PASS" and oc["decode"] in ("FAIL", "SKIP"):
            # 诚实：capture/record 过了但没解出东西——不是环境故障
            item["key_output"] += "（decode 空属信号侧诚实结果，不判环境 FAIL）"

    elif sid == "ota_backfill":
        log = _read_text(str(raw.get("log", "")))
        st = ota_outcome(log or "")
        item["status"] = st
        if st == "SKIP":
            item["skip_reason"] = "无 onboard 录制产物可回填（未检测到设备 / record 未落盘）"
        item["key_output"] = _tail(log)
        item["recordings_dir"] = str(raw.get("recordings_dir", ""))

    else:
        # 未知步：按 shell 给的 rc 推断，绝不静默吞掉
        item["status"] = "PASS" if raw.get("rc") == 0 else "FAIL"
        item["key_output"] = _tail(_read_text(str(raw.get("log", ""))))

    return item


def assemble(raw_lines: list[dict], meta: Optional[dict] = None) -> dict:
    """把 shell 写出的 raw 步结果（已解析成 dict 列表）组装成最终检查表。"""
    meta = meta or {}
    steps = [_step_from_raw(r) for r in raw_lines]

    # 设备在场：以 selfcheck 步引用的报告为准
    device_present = False
    for r in raw_lines:
        if r.get("id") == "selfcheck":
            rep = {}
            txt = _read_text(str(r.get("selfcheck_report", "")))
            if txt:
                try:
                    rep = json.loads(txt)
                except json.JSONDecodeError:
                    rep = {}
            device_present = device_present_from_selfcheck(rep)
            break

    counts = {k: 0 for k in ALLOWED_STATUS}
    for s in steps:
        if s["status"] in counts:
            counts[s["status"]] += 1

    fails = [s["id"] for s in steps if s["status"] == "FAIL"]

    if not device_present:
        overall = "NO_HARDWARE"
        exit_code = EXIT_NO_HW
    elif fails:
        overall = "FAIL"
        exit_code = EXIT_HW_FAIL
    else:
        overall = "PASS"
        exit_code = EXIT_OK

    return {
        "tool": TOOL,
        "version": VERSION,
        "timestamp": str(meta.get("timestamp")
                         or dt.datetime.now().astimezone().isoformat(timespec="seconds")),
        "host": str(meta.get("host") or platform.node()),
        "device_present": bool(device_present),
        "no_hardware_expected": not device_present,
        "summary": {
            "PASS": counts["PASS"],
            "FAIL": counts["FAIL"],
            "SKIP": counts["SKIP"],
            "n_steps": len(steps),
        },
        "steps": steps,
        "failed_steps": fails,
        "overall": overall,
        "exit_code": exit_code,
    }


# ---------------------------------------------------------------------------
# 6. validate：检查表结构校验（pytest / CI 可直接调用）
# ---------------------------------------------------------------------------
def validate_checklist(obj: Any) -> list[str]:
    """返回错误列表；空列表=合法。"""
    errs: list[str] = []
    if not isinstance(obj, dict):
        return ["顶层不是 JSON 对象"]
    for k in ("tool", "version", "timestamp", "host", "device_present",
              "summary", "steps", "overall", "exit_code"):
        if k not in obj:
            errs.append(f"缺字段: {k}")
    if errs:
        return errs
    if obj["tool"] != TOOL:
        errs.append(f"tool 字段不匹配: {obj['tool']!r} != {TOOL!r}")
    if not isinstance(obj["device_present"], bool):
        errs.append("device_present 必须是 bool")
    summ = obj["summary"]
    if not isinstance(summ, dict) or "PASS" not in summ or "FAIL" not in summ \
            or "SKIP" not in summ:
        errs.append("summary 缺 PASS/FAIL/SKIP 计数")
    steps = obj["steps"]
    if not isinstance(steps, list) or not steps:
        errs.append("steps 必须是非空列表")
    else:
        for i, s in enumerate(steps):
            if not isinstance(s, dict):
                errs.append(f"steps[{i}] 不是对象")
                continue
            for k in ("id", "title", "status"):
                if k not in s:
                    errs.append(f"steps[{i}] 缺字段 {k}")
            if s.get("status") not in ALLOWED_STATUS:
                errs.append(f"steps[{i}].status 非法: {s.get('status')!r}")
    if obj["overall"] not in ("PASS", "FAIL", "NO_HARDWARE"):
        errs.append(f"overall 非法: {obj['overall']!r}")
    ec = obj["exit_code"]
    if ec not in (EXIT_OK, EXIT_HW_FAIL, EXIT_NO_HW):
        errs.append(f"exit_code 非法: {ec!r}")
    # 一致性：exit_code 与 overall 对齐
    if obj["overall"] == "PASS" and ec != EXIT_OK:
        errs.append("overall=PASS 但 exit_code != 0")
    if obj["overall"] == "NO_HARDWARE" and ec != EXIT_NO_HW:
        errs.append("overall=NO_HARDWARE 但 exit_code != 2")
    if obj["overall"] == "FAIL" and ec != EXIT_HW_FAIL:
        errs.append("overall=FAIL 但 exit_code != 1")
    return errs


# ---------------------------------------------------------------------------
# 7. CLI
# ---------------------------------------------------------------------------
def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return json.load(f)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MBDSDR 演练纯逻辑库 / CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_dp = sub.add_parser("device-present", help="打印 selfcheck 报告是否枚举到设备")
    p_dp.add_argument("selfcheck_json")

    p_as = sub.add_parser("assemble", help="读 raw JSONL 组装检查表")
    p_as.add_argument("--raw", required=True, help="raw 步结果 JSONL 路径")
    p_as.add_argument("--out", required=True, help="检查表输出 JSON 路径")

    p_v = sub.add_parser("validate", help="校验检查表 JSON")
    p_v.add_argument("checklist_json")

    args = ap.parse_args(argv)

    if args.cmd == "device-present":
        try:
            rep = _read_json(args.selfcheck_json)
        except (OSError, json.JSONDecodeError) as e:
            print(f"false", file=sys.stderr)
            print(f"[ERROR] 读 selfcheck 报告失败: {e}", file=sys.stderr)
            return EXIT_USAGE
        print("true" if device_present_from_selfcheck(rep) else "false")
        return EXIT_OK

    if args.cmd == "assemble":
        raw_lines: list[dict] = []
        try:
            with open(args.raw, "r", encoding="utf-8", errors="replace") as f:
                for ln, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError as e:
                        print(f"[ERROR] raw 第 {ln} 行不是合法 JSON: {e}",
                              file=sys.stderr)
                        return EXIT_USAGE
                    if isinstance(obj, dict):
                        raw_lines.append(obj)
        except OSError as e:
            print(f"[ERROR] 读 raw 文件失败: {e}", file=sys.stderr)
            return EXIT_USAGE
        if not raw_lines:
            print("[ERROR] raw 文件为空，没有任何步结果", file=sys.stderr)
            return EXIT_USAGE

        checklist = assemble(raw_lines)
        errs = validate_checklist(checklist)
        if errs:
            print("[ERROR] 组装出的检查表未通过自校验:", file=sys.stderr)
            for e in errs:
                print(f"  - {e}", file=sys.stderr)
            return EXIT_USAGE
        try:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(checklist, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print(f"[ERROR] 写检查表失败: {e}", file=sys.stderr)
            return EXIT_USAGE

        s = checklist["summary"]
        print(f"[演练检查表] overall={checklist['overall']} "
              f"device_present={checklist['device_present']} "
              f"PASS={s['PASS']} FAIL={s['FAIL']} SKIP={s['SKIP']} "
              f"-> {args.out}")
        return int(checklist["exit_code"])

    if args.cmd == "validate":
        try:
            obj = _read_json(args.checklist_json)
        except (OSError, json.JSONDecodeError) as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            return EXIT_USAGE
        errs = validate_checklist(obj)
        if errs:
            for e in errs:
                print(f"  - {e}")
            return 1
        print("OK: 检查表结构合法")
        return 0

    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
