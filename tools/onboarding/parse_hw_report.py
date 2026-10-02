#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
MBDSDR 真机回传报告 · 云侧解析器
================================

真机用户跑完两条命令后把输出贴回聊天，本脚本负责：

  1. 从一段**任意聊天文本**里自动抽出所有 JSON 对象（支持多段 JSON 混杂在
     人类对话、命令回显、报错信息之间）；
  2. 识别哪段是 ``selfcheck.py --json``、哪段是 ``onboard.py --json``
     （含 detect 失败时只吐裸 ``{"steps":[...]}`` 的早退出形状）；
  3. 逐字段核对后输出**人类可读的结构化结论**：
       - SDR 设备（有/无、tuner、增益档、丢包统计）
       - 声卡
       - GNSS 串口（NMEA 判定）
       - 依赖缺失
       - onboard 各步结果与产物清单
       - **后续可做步骤建议清单**（可执行、与检测到的事实挂钩）

设计原则：
  * **纯 Python 标准库**：云侧 / 聊天侧可能没有 numpy，本脚本绝不 import
    onboard / selfcheck，只吃 JSON 文本。
  * **容错**：字段缺失、detail 为空 dict、某段 JSON 损坏都不崩；缺什么就
    诚实标“未知”，绝不臆造字段。
  * **坏输入不崩**：找不到任何 JSON 对象时给出明确错误并以退出码 2 退出。

用法：
  python3 parse_hw_report.py report.txt          # 从文件读
  cat report.txt | python3 parse_hw_report.py    # 从 stdin 读
  python3 parse_hw_report.py report.txt --json   # 同时吐结构化 JSON 结论
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Iterable

SELFCHECK_TOOL = "mbdsdr-hw-selfcheck"
ONBOARD_TOOL = "mbdsdr-onboarding"


# ---------------------------------------------------------------------------
# 1. 从聊天文本里抽 JSON 对象
# ---------------------------------------------------------------------------
def extract_json_objects(text: str) -> list[dict]:
    """从一段可能混杂了人类对话的文本里，抽出所有能被 json.loads 解析的
    JSON **对象**（顶层是 ``{...}``）。

    实现：逐字符做花括号配对，正确跳过字符串字面量里的花括号与转义引号；
    每当一层花括号闭合时就尝试解析该片段。解析失败（损坏片段）直接跳过，
    绝不抛异常。返回所有成功解析出的 dict，顺序与文本中出现顺序一致。
    """
    objects: list[dict] = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch != "{":
            i += 1
            continue

        # 从位置 i 开始做配对扫描
        depth = 0
        in_str = False
        escape = False
        j = i
        end = -1
        while j < n:
            c = text[j]
            if in_str:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        end = j
                        break
            j += 1

        if end == -1:
            # 这个 { 没有闭合，后面再没有可配对的了，结束
            break

        snippet = text[i : end + 1]
        try:
            obj = json.loads(snippet)
        except (json.JSONDecodeError, ValueError):
            obj = None
        if isinstance(obj, dict):
            objects.append(obj)
        # 从闭合处之后继续找下一个对象（不嵌套重复收录内层对象）
        i = end + 1
    return objects


# ---------------------------------------------------------------------------
# 2. 分类：这段 JSON 是 selfcheck 还是 onboard？
# ---------------------------------------------------------------------------
def classify_block(block: dict) -> str | None:
    """返回 'selfcheck' / 'onboard' / None（无法识别）。"""
    tool = block.get("tool")
    if tool == SELFCHECK_TOOL or ("checks" in block and isinstance(block.get("checks"), list)):
        return "selfcheck"
    if tool == ONBOARD_TOOL or ("steps" in block and isinstance(block.get("steps"), list)):
        return "onboard"
    return None


# ---------------------------------------------------------------------------
# 3. selfcheck 分析
# ---------------------------------------------------------------------------
def _find_check(checks: list[dict], prefix: str) -> dict | None:
    for c in checks:
        if isinstance(c, dict) and str(c.get("name", "")).startswith(prefix):
            return c
    return None


def _status_emoji(status: str) -> str:
    return {
        "PASS": "[PASS]",
        "WARN": "[WARN]",
        "FAIL": "[FAIL]",
        "SKIP": "[SKIP]",
        "RUNNING": "[..]",
    }.get(status, f"[{status or '?'}]")


def analyze_selfcheck(block: dict) -> dict:
    """从 selfcheck --json 的 dict 里抽出结构化结论。全部字段做存在性保护。"""
    checks = block.get("checks", []) if isinstance(block.get("checks"), list) else []
    summary = block.get("summary", {}) if isinstance(block.get("summary"), dict) else {}

    out: dict[str, Any] = {
        "tool": block.get("tool", SELFCHECK_TOOL),
        "host": block.get("host"),
        "os": block.get("os"),
        "python": block.get("python"),
        "timestamp": block.get("timestamp"),
        "counts": summary.get("counts", {}),
        "conclusion": summary.get("conclusion"),
        "real_machine_todo": summary.get("real_machine_todo", []),
        "device": {},
        "soundcard": {},
        "gnss": {},
        "deps": {},
    }

    # ---- 检查 1：USB / udev ----
    c1 = _find_check(checks, "1.") or {}
    d1 = c1.get("detail", {}) if isinstance(c1.get("detail"), dict) else {}
    hits = d1.get("target_hits", []) or []
    device = out["device"]
    device["present"] = len(hits) > 0
    device["hits"] = [
        {
            "vid": h.get("vid"),
            "pid": h.get("pid"),
            "product": h.get("product"),
            "known_as": h.get("known_as"),
        }
        for h in hits
        if isinstance(h, dict)
    ]
    device["udev_rules"] = d1.get("udev_rules_hit", []) or []
    device["has_udev_rule"] = len(device["udev_rules"]) > 0
    groups = d1.get("user_groups", []) or []
    device["user_groups"] = groups
    device["in_required_groups"] = bool({"plugdev", "dialout"} & set(groups))
    device["check1_status"] = c1.get("status")

    # ---- 检查 2：rtl_test tuner ----
    c2 = _find_check(checks, "2.") or {}
    d2 = c2.get("detail", {}) if isinstance(c2.get("detail"), dict) else {}
    parsed2 = d2.get("parsed", {}) if isinstance(d2.get("parsed"), dict) else {}
    device["tuner"] = parsed2.get("tuner")
    device["gain_count"] = parsed2.get("gain_count")
    gl = parsed2.get("gain_list_db") or []
    device["gain_list_db"] = gl[:8]
    dev_list = parsed2.get("devices", []) or []
    device["serials"] = [
        (dv.get("serial") or "")[:3] + "***" if isinstance(dv, dict) and dv.get("serial") else None
        for dv in dev_list
    ]
    device["rtl_test_status"] = c2.get("status")
    device["rtl_test_skipped_reason"] = d2.get("skipped_reason")

    # ---- 检查 3：rtl_sdr 丢包 ----
    c3 = _find_check(checks, "3.") or {}
    d3 = c3.get("detail", {}) if isinstance(c3.get("detail"), dict) else {}
    device["lost_bytes_total"] = d3.get("lost_bytes_total")
    device["tmp_file_bytes"] = d3.get("tmp_file_bytes")
    device["stream_status"] = c3.get("status")
    device["stream_skipped_reason"] = d3.get("skipped_reason")

    # ---- 检查 4：声卡 ----
    c4 = _find_check(checks, "4.") or {}
    d4 = c4.get("detail", {}) if isinstance(c4.get("detail"), dict) else {}
    out["soundcard"] = {
        "status": c4.get("status"),
        "playback_cards": d4.get("playback_cards", []) or [],
        "capture_cards": d4.get("capture_cards", []) or [],
        "dev_snd_entries": d4.get("dev_snd_entries"),
    }

    # ---- 检查 5：GNSS ----
    c5 = _find_check(checks, "5.") or {}
    d5 = c5.get("detail", {}) if isinstance(c5.get("detail"), dict) else {}
    probes = d5.get("probes", []) or []
    nmea_ok = [
        p for p in probes
        if isinstance(p, dict) and p.get("ok")
    ]
    out["gnss"] = {
        "status": c5.get("status"),
        "candidates": d5.get("candidates", []) or [],
        "nmea_found": len(nmea_ok) > 0,
        "nmea_probes": [
            {"device": p.get("device"), "baud": p.get("baud"), "reason": p.get("reason")}
            for p in probes
            if isinstance(p, dict)
        ],
    }

    # ---- 检查 6：依赖 ----
    c6 = _find_check(checks, "6.") or {}
    d6 = c6.get("detail", {}) if isinstance(c6.get("detail"), dict) else {}
    missing = []
    if d6.get("py_rtlsdr") is False:
        missing.append("python 绑定 rtlsdr (pyrtlsdr)")
    if d6.get("py_soapy") is False:
        missing.append("python 绑定 SoapySDR")
    if not d6.get("gpsd"):
        missing.append("gpsd 守护进程")
    out["deps"] = {
        "status": c6.get("status"),
        "py_rtlsdr": d6.get("py_rtlsdr"),
        "py_soapy": d6.get("py_soapy"),
        "gpsd": d6.get("gpsd"),
        "gpsd_client": d6.get("gpsd_client"),
        "missing": missing,
    }
    return out


# ---------------------------------------------------------------------------
# 4. onboard 分析
# ---------------------------------------------------------------------------
def analyze_onboard(block: dict) -> dict:
    steps = block.get("steps", []) if isinstance(block.get("steps"), list) else []
    out: dict[str, Any] = {
        "tool": block.get("tool"),   # 早退出裸 steps 时为 None
        "host": block.get("host"),
        "mode": block.get("mode"),
        "params": block.get("params", {}) if isinstance(block.get("params"), dict) else {},
        "steps": [],
        "artifacts": [],
        "out_dir": None,
    }
    for s in steps:
        if not isinstance(s, dict):
            continue
        detail = s.get("detail", {}) if isinstance(s.get("detail"), dict) else {}
        entry = {
            "step": s.get("step"),
            "status": s.get("status"),
            "message": s.get("message", ""),
        }
        st = s.get("step")
        if st == "capture":
            entry["raw_bytes_written"] = detail.get("raw_bytes_written")
            entry["lost_bytes_total"] = detail.get("lost_bytes_total")
            entry["actual_samples"] = detail.get("actual_samples")
        elif st == "record":
            entry["sigmf_data"] = detail.get("sigmf_data")
            entry["sigmf_meta"] = detail.get("sigmf_meta")
            entry["n_samples"] = detail.get("n_samples")
            entry["duration_s"] = detail.get("duration_s")
        elif st == "decode":
            entry["n_frames"] = detail.get("n_frames")
            entry["decoded_text"] = detail.get("decoded_text")
            entry["apt_image_shape"] = detail.get("apt_image_shape")
        elif st == "output":
            arts = detail.get("artifacts", []) or []
            out["artifacts"] = [a for a in arts if isinstance(a, str)]
            out["out_dir"] = detail.get("out_dir")
        out["steps"].append(entry)
    return out


# ---------------------------------------------------------------------------
# 5. 后续可做步骤建议（与检测到的事实挂钩）
# ---------------------------------------------------------------------------
def build_recommendations(sc: dict | None, ob: dict | None) -> list[str]:
    recs: list[str] = []

    if sc is None:
        recs.append("未解析到 selfcheck 报告：请先贴回 `selfcheck.py --json` 的完整输出。")
    else:
        dev = sc.get("device", {})
        if not dev.get("present"):
            recs.append("【硬件未就绪】未检测到 RTL-SDR（0bda:2838/2832）→ 插好设备到 USB2.0 直连口后重跑 selfcheck。")
            recs.append("【权限准备】把当前用户加入 plugdev,dialout 组并重新登录：sudo usermod -aG plugdev,dialout $USER")
            recs.append("【udev 规则】安装 librtlsdr 自带 99-rtlsdr.rules 到 /etc/udev/rules.d/ 并重载 udev。")
        else:
            # 设备在场
            tuner_ok = dev.get("tuner")
            if not dev.get("has_udev_rule"):
                recs.append("【缺 udev 规则】已看到设备但未安装 0bda:2838/2832 udev 规则 → 先安装规则，否则重启/重插后可能权限不足。")
            if not dev.get("in_required_groups"):
                recs.append("【缺用户组】当前用户不在 plugdev/dialout 组 → sudo usermod -aG plugdev,dialout $USER 后重新登录。")
            stream = dev.get("stream_status")
            if stream == "PASS" and tuner_ok:
                recs.append("【设备就绪】selfcheck 第 1/2/3 项通过 → 可直接跑：python3 tools/onboarding/onboard.py --step all --freq 1090e6 --mode adsb")
            elif dev.get("lost_bytes_total"):
                recs.append("【有丢包】检测到 USB 丢包 → 换 USB2.0 直连口、避免 USB3.0 Hub 后重测。")

        # 依赖
        missing = sc.get("deps", {}).get("missing", [])
        if missing:
            recs.append(f"【缺依赖】{', '.join(missing)} → 影响 mbdsdr_ai Python 原型/GNSS 守护（不影响 C++/Qt 桌面端）。")

        # GNSS
        gnss = sc.get("gnss", {})
        if gnss.get("candidates") and not gnss.get("nmea_found"):
            recs.append("【GNSS】发现串口但未读到 NMEA → 确认模块已定位/天线接好；试用 sudo cat /dev/ttyUSB0 肉眼看 $ 开头行，或换波特率。")

    # onboard 侧建议
    if ob is not None:
        steps = {s.get("step"): s.get("status") for s in ob.get("steps", [])}
        msgs = {s.get("step"): s.get("message", "") for s in ob.get("steps", [])}
        dec_step = next((s for s in ob.get("steps", []) if s.get("step") == "decode"), {})
        out_step = next((s for s in ob.get("steps", []) if s.get("step") == "output"), {})

        if steps.get("detect") == "FAIL":
            recs.append("【onboard】detect 步失败 → 先按上面 selfcheck 建议把 SDR 设备弄好，再重跑 onboard。")
        if "采样率" in (msgs.get("capture") or "") and "超出" in (msgs.get("capture") or ""):
            recs.append("【参数越界】capture 报采样率超出 rtl-sdr 范围(225k~3.2M) → 把 --sr 改回 2400000。")
        if "中心频率" in (msgs.get("capture") or "") and "超出" in (msgs.get("capture") or ""):
            recs.append("【参数越界】capture 报中心频率超出 RTL-SDR 范围(24~1700MHz) → 检查 --freq 单位（Hz，如 1090e6）。")
        if "未找到 rtl_sdr" in (msgs.get("capture") or ""):
            recs.append("【缺命令】capture 找不到 rtl_sdr → 编译安装 librtlsdr 或 apt install rtl-sdr。")
        if "未写入任何数据" in (msgs.get("capture") or "") or "文件为空" in (msgs.get("capture") or ""):
            recs.append("【设备被占？】capture 写出空文件 → 确认没有 rtl_tcp/gqrx/dump1090 占用设备，拔出重插后重试。")
        if steps.get("decode") == "PASS" and steps.get("output") == "PASS":
            arts = ob.get("artifacts", [])
            recs.append(f"【全链路通过】产物已落盘（{len(arts)} 个），目录：{ob.get('out_dir') or '(见 output 步 evidence)'}。")
        elif steps.get("decode") == "FAIL":
            nf = dec_step.get("n_frames")
            recs.append("【decode 未出帧】采到了信号但解码 0 有效帧 → 换天线位置/调增益/确认频率上有目标信号。")

    if not recs:
        recs.append("未从报告中提取到明确的待办项；如与预期不符，请把两段 JSON 完整贴回。")
    return recs


# ---------------------------------------------------------------------------
# 6. 渲染成人类可读报告
# ---------------------------------------------------------------------------
def render_report(sc: dict | None, ob: dict | None, blocks: list[dict]) -> str:
    lines: list[str] = []
    bar = "=" * 68
    lines.append(bar)
    lines.append("MBDSDR 真机回传 · 云侧解析结论")
    lines.append(bar)
    lines.append(f"共从贴回文本中识别到 {len(blocks)} 个 JSON 对象："
                 f"selfcheck={'有' if sc else '无'}，onboard={'有' if ob else '无'}")

    # ---- selfcheck ----
    if sc:
        lines.append("")
        lines.append("── SDR 设备 ──────────────────────────────")
        dev = sc["device"]
        if dev.get("present"):
            for h in dev.get("hits", []):
                lines.append(f"  • 检测到设备 {h.get('vid')}:{h.get('pid')} — {h.get('known_as')} ({h.get('product') or '?'})")
            if dev.get("tuner"):
                lines.append(f"  • tuner：{dev.get('tuner')}（增益 {dev.get('gain_count')} 档）")
            else:
                lines.append("  • tuner：未解析到（rtl_test 状态=%s）" % dev.get("rtl_test_status"))
            lost = dev.get("lost_bytes_total")
            lines.append(f"  • 3 秒实读丢包：{lost if lost is not None else '未测/跳过'} 字节（流读状态={dev.get('stream_status')}）")
        else:
            lines.append("  • 未检测到 RTL-SDR 设备（target_hits 为空）")
        lines.append(f"  • udev 规则：{'已装 ' + str(dev.get('udev_rules')) if dev.get('has_udev_rule') else '未安装'}")

        lines.append("")
        lines.append("── 声卡 ────────────────────────────────")
        sca = sc["soundcard"]
        lines.append(f"  状态={sca.get('status')}  播放卡={len(sca.get('playback_cards', []))}  "
                     f"录音卡={len(sca.get('capture_cards', []))}  /dev/snd={'存在' if sca.get('dev_snd_entries') else '不存在'}")

        lines.append("")
        lines.append("── GNSS 串口 ────────────────────────────")
        gn = sc["gnss"]
        if not gn.get("candidates"):
            lines.append("  状态=%s  未发现任何 /dev/ttyUSB* /dev/ttyACM*" % gn.get("status"))
        else:
            lines.append(f"  状态={gn.get('status')}  候选串口={gn.get('candidates')}")
            lines.append(f"  NMEA 判定：{'读到 $-开头 NMEA 语句' if gn.get('nmea_found') else '未读到 NMEA（模块未定位/波特率不对/未接线）'}")

        lines.append("")
        lines.append("── 依赖 ─────────────────────────────────")
        de = sc["deps"]
        lines.append(f"  状态={de.get('status')}")
        lines.append(f"  python rtlsdr={'OK' if de.get('py_rtlsdr') else 'MISSING'}  "
                     f"SoapySDR={'OK' if de.get('py_soapy') else 'MISSING'}  "
                     f"gpsd={de.get('gpsd') or 'MISSING'}")
        if de.get("missing"):
            lines.append(f"  缺失项：{', '.join(de['missing'])}")

    # ---- onboard ----
    if ob:
        lines.append("")
        lines.append("── Onboard 分步结果 ─────────────────────")
        p = ob.get("params", {})
        if ob.get("mode"):
            lines.append(f"  mode={ob.get('mode')}  freq={p.get('freq_hz')}  sr={p.get('sample_rate_hz')}  "
                         f"n={p.get('n_samples')}  gain={p.get('gain_db')}dB")
        for s in ob.get("steps", []):
            lines.append(f"  {_status_emoji(s.get('status'))} step={s.get('step')}: {s.get('message')}")
            if s.get("raw_bytes_written") is not None:
                lines.append(f"        写入 {s['raw_bytes_written']:,} 字节，丢包 {s.get('lost_bytes_total')} 字节")
            if s.get("n_samples") is not None and s.get("step") == "record":
                lines.append(f"        SigMF：{s.get('n_samples'):,} 样本，时长 {s.get('duration_s'):.3f}s")
            if s.get("step") == "decode" and s.get("n_frames") is not None:
                lines.append(f"        解码有效帧：{s.get('n_frames')}")
        arts = ob.get("artifacts", [])
        if arts:
            lines.append(f"  产物清单（{len(arts)}）：")
            for a in arts:
                lines.append(f"    - {a}")

    # ---- 建议 ----
    lines.append("")
    lines.append("── 后续可做步骤建议 ──────────────────────")
    for i, r in enumerate(build_recommendations(sc, ob), 1):
        lines.append(f"  {i}. {r}")
    lines.append(bar)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 7. 顶层入口
# ---------------------------------------------------------------------------
class ReportParseError(Exception):
    """贴回文本里没有任何可识别的 JSON 对象。"""


def parse_text(text: str) -> tuple[dict | None, dict | None, list[dict]]:
    """从一段聊天文本里解析出 (selfcheck报告, onboard报告, 全部JSON块列表)。"""
    blocks = extract_json_objects(text)
    if not blocks:
        raise ReportParseError(
            "贴回的文本里没有找到任何 JSON 对象。请确认：\n"
            "  1. 用了 `--json` 参数跑两条命令（不加 --json 是人类可读报告，无法解析）；\n"
            "  2. 把命令的**完整输出**（从第一个 { 到最后一个 }）都贴了回来；\n"
            "  3. 不要手动删掉行首的缩进或引号。"
        )

    sc_block = next((b for b in blocks if classify_block(b) == "selfcheck"), None)
    ob_block = next((b for b in blocks if classify_block(b) == "onboard"), None)

    sc = analyze_selfcheck(sc_block) if sc_block else None
    ob = analyze_onboard(ob_block) if ob_block else None
    return sc, ob, blocks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MBDSDR 真机回传 JSON 云侧解析器")
    ap.add_argument("path", nargs="?", help="报告文本文件路径（缺省从 stdin 读）")
    ap.add_argument("--json", action="store_true", help="额外输出结构化 JSON 结论到 stdout")
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

    try:
        sc, ob, blocks = parse_text(text)
    except ReportParseError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2

    print(render_report(sc, ob, blocks))
    if args.json:
        print("\n--- structured JSON ---")
        print(json.dumps({"selfcheck": sc, "onboard": ob,
                          "recommendations": build_recommendations(sc, ob)},
                         ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
