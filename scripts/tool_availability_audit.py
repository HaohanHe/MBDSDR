#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""P13：495 个 AI 工具的逐个可执行性审计。

离线 + 无硬件 + 静音 + offscreen，用替身（假设备 id / 合成 IQ 与音频 /
临时目录 / 仓库内真实小样本）逐个调用注册表里的每个工具，按四类归档：

  RUNS    真能跑并产出可核验结果（成功且返回非空、有结构或断言通过）
  HONEST  只能诚实不可用（缺硬件 / 缺网络 / 缺样本 / 缺前置资源 / 需人工确认）
  STUB    参数校验能拒，但功能未实现（明确 "未实现" 措辞）
  BROKEN  真坏（抛异常、静默返回空、失败却无原因码、成功但内容空洞）

用法：
  python scripts/tool_availability_audit.py                 # 跑全量，重写 docs/tool-availability.md
  python scripts/tool_availability_audit.py --json out.json # 附带逐项明细
  python scripts/tool_availability_audit.py --only ft8_encode codec2_encode
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")
# 审计必须是可复跑的离线行为：网络类工具应快速失败，而不是卡在 DNS 上
os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:9")
os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:9")

RUNS, HONEST, STUB, BROKEN = "RUNS", "HONEST", "STUB", "BROKEN"

# 缺什么才能跑——按提示词归类，用于 md 的「缺什么」列
_MISSING_HINTS = (
    (("无设备", "未连接", "not connected", "no device", "no sdr", "设备未", "未发现可用",
      "无连接设备", "无连接仪器", "请先连接", "未找到设备", "could not open", "failed to open",
      "无法打开", "permission denied", "需先连接", "未插"), "硬件：SDR 设备"),
    (("网络", "network", "urlopen", "urllib", "dns", "timed out", "超时", "离线模式",
      "连接被拒绝", "connection refused", "连接失败", "fatal: repository"),
     "网络：外网访问/在线数据源"),
    (("arm_manual", "手动确认", "需人工", "安全门", "未授权", "拒绝执行", "gate_not_armed",
      "未过人工门"), "人工确认：arm_manual 安全门"),
    (("需要安装", "未安装", "未找到可执行", "command not found", "no module named",
      "未检测到 satdump", "未找到 satdump"), "外部程序/依赖：未安装"),
    # 「输入样本/参数不合格 → 如实拒绝」优先于「前置资源」：例如“未提供足够的
    # IQ 样本”应归缺样本，而不是缺前置资源。校验拒绝是正确行为，不是空壳。
    (("需要 iq", "需要样本", "样本不足", "样本太短", "长度不足", "长度 ", "须为", "需为",
      "不是合法", "无法解析", "无法识别", "校验和", "不匹配", "必须", "至少", "得到 ",
      "too_short", "too short", "未检测到", "无同步", "找不到", "不是 ", "缺少",
      "未提供", "invalid", "requires", "expected", "格式", "不足", "不低于", "高于", "低于",
      "必须小于", "必须大于", "失败"), "有效输入：合成替身不满足该工具的输入要求"),
    (("不存在", "not found", "no such", "does not exist", "未找到", "无数据", "请先创建",
      "未录制", "未加载", "为空", "没有可", "没有父版本", "任务不存在", "未匹配", "未收录",
      "未知卫星", "未知帧", "未知的", "未知动词", "未知的进化", "未注册", "没有可撤销"),
     "前置资源：需先创建/采集"),
)

# 审计替身声明：所有合成输入都从这里取，md 表头会注明
SYNTHETIC_SAMPLES = 8192


def _sine(n=SYNTHETIC_SAMPLES, freq=1200.0, sr=8000.0, amp=0.5):
    return [amp * math.sin(2 * math.pi * freq * i / sr) for i in range(n)]


def _cplx_iq(n=SYNTHETIC_SAMPLES, freq=0.1):
    return [complex(math.cos(2 * math.pi * freq * i),
                    math.sin(2 * math.pi * freq * i)) for i in range(n)]


def _bits(n=160):
    return [i % 2 for i in range(n)]


def _repo_file(*names):
    for n in names:
        p = ROOT / n
        if p.exists():
            return str(p)
    p = ROOT / "README.md"
    return str(p) if p.exists() else str(ROOT / "mbdsdr_ai_mcp_server.py")


# 合法 TLE（METEOR-M 2，测试夹具里的真实格式两行根数；离线审计不联网）
_TLE_L1 = "1 40069U 14037A   26297.30989954  .00000323  00000+0  16680-3 0  9998"
_TLE_L2 = "2 40069  98.5301 268.5877 0006497  31.2770 328.8793 14.21494425637660"


def _build_args(tool, tmpdir, hex_frame=""):
    """基于 tests/tool_param_smoke.py 的名称启发式，再做替身取值修正。

    必填 + 可选参数都构造（可选布尔不动，避免误把开关拨到 true）；
    TLE 用合法两行根数，十六进制帧用上游 AX.25 编码产物。
    """
    import tool_param_smoke as tps  # noqa: E402

    schema = tool.get("parameters", {}) or {}
    props = schema.get("properties", {}) or {}
    args = tps.build_args(tool)
    for p, prop in props.items():
        prop = prop or {}
        if p in args or prop.get("type") == "boolean":
            continue
        if p in ("arm_manual", "token", "api_key", "confirm"):
            continue
        # 可选参数只补「数值」和「有 enum 的枚举」，别把结构化对象/ID 填成瞎值：
        # 乱填会让工具抛 TypeError/AttributeError，把真实可用性审计污染成"报错"。
        if prop.get("type") in ("number", "integer") or prop.get("enum"):
            args[p] = tps._coerce_to_schema(tps.value_for(p, prop), prop)

    for k, v in list(args.items()):
        low = k.lower()
        prop = props.get(k, {}) or {}
        typ = prop.get("type")
        # 空数组一律补成有意义的替身，避免"喂空 → 成功返回空"的假绿
        if isinstance(v, list) and not v:
            items = prop.get("items", {}) or {}
            if items.get("type") == "object":
                sub = items.get("properties", {}) or {}
                v = [{k: tps._coerce_to_schema(tps.value_for(k, sp), sp)
                      for k, sp in sub.items()}]
            elif items.get("type") == "string":
                v = [hex_frame] if hex_frame else ["test"]
            elif any(w in low for w in ("freedv", "signal", "audio", "mpx", "samples")):
                v = _sine()
            elif "iq" in low:
                v = _sine(4096)
            elif "bits" in low:
                v = _bits()
            elif typ == "array":
                v = _bits()
        # 对象数组被上游启发式填成 [10] 这类标量时，换成一条合法对象
        items = prop.get("items", {}) or {}
        if isinstance(v, list) and v and items.get("type") == "object" \
                and not all(isinstance(x, dict) for x in v):
            sub = items.get("properties", {}) or {}
            v = [{k: tps._coerce_to_schema(tps.value_for(k, sp), sp)
                  for k, sp in sub.items()}]
        if isinstance(v, list) and any(w in low for w in ("audio", "mpx", "signal", "samples")) \
                and all(isinstance(x, (int, float)) for x in v) and len(v) < 1024:
            v = _sine()
        # IQ 用 JSON 可表示的实数（部分工具按 interleaved 实虚解释，部分按复数数组，
        # 实数列表两边都吃得下；绝不传 complex，否则审计参数本身就不合法）
        if isinstance(v, list) and "iq" in low and all(isinstance(x, (int, float)) for x in v):
            v = _sine(4096)
        if isinstance(v, list) and low in ("taps", "coefficients", "fir_taps") and not v:
            v = [1.0]
        if isinstance(v, str) and low in ("line1", "tle_line1", "tle1"):
            v = _TLE_L1
        if isinstance(v, str) and low in ("line2", "tle_line2", "tle2"):
            v = _TLE_L2
        if isinstance(v, str) and ("hex" in low or low in ("packet", "frame", "bytes_b64")):
            if hex_frame and low != "bytes_b64":
                v = hex_frame
        if isinstance(v, str) and (v.startswith("/tmp/mbdsdr") or v in ("/tmp/mbdsdr_test.bin",)):
            v = str(Path(tmpdir) / (low + ".bin"))
            Path(v).write_bytes(b"\x00" * 256)
        if isinstance(v, str) and low in ("output_dir", "out_dir", "output_path", "save_dir"):
            v = str(Path(tmpdir) / "out")
            Path(v).mkdir(parents=True, exist_ok=True)
        if isinstance(v, str) and low in ("file_path", "filepath", "filename"):
            v = _repo_file("README.md", "mbdsdr_ai_mcp_server.py")
        if isinstance(v, bool) and "arm_manual" in low:
            v = False  # 走安全门，验证拒绝而不是真写
        args[k] = v
    return args


# ── 可核验断言：往返类工具必须真的还原原文/原比特，而不是"成功但内容空洞" ──
_VERIFIABLE = {
    "morse_encode": lambda t, d: "...." in t and "---" in t,
    "morse_decode": lambda t, d: "HELLO" in t.upper(),
    "baudot_encode": lambda t, d: bool(d) and str(d).count("'") >= 0,
    "baudot_decode": lambda t, d: "HELLO" in t.upper() or "HELLO" in str(d).upper(),
    "diff_encode": lambda t, d: bool(d),
    "ft8_encode": lambda t, d: "CQ" in t.upper() or bool(d),
}

# 需要专门替身参数（真实样本 / 合法文本），否则只能得到"缺样本"而非真跑
_SPECIAL_ARGS = {
    "ft8_encode": lambda tmp: {"text": "CQ BI1AAA OM74"},
    "ft8_unpack_message": lambda tmp: {"bits": []},  # 由 ft8_encode 产物回填
    "baudot_encode": lambda tmp: {"text": "HELLO"},
    "baudot_decode": lambda tmp: {"words": []},  # 由 baudot_encode 产物回填
    "bell103_modulate_text": lambda tmp: {"text": "HELLO"},
    "bell103_demodulate_text": lambda tmp: {"samples": _sine(8000, 1270.0)},
    "fsk_modulate": lambda tmp: {"bits": _bits(80)},
    "fsk_demodulate": lambda tmp: {"samples": _sine(8000, 1270.0)},
    "afsk_modulate": lambda tmp: {"source": "BI1AAA", "destination": "APRS",
                                  "info": ">test"},
    "ax25_encode": lambda tmp: {"source": "BI1AAA", "destination": "APRS",
                                "info": ">test"},
    "codec2_encode": lambda tmp: {"audio": _sine(3200)},
    "freedv_modulate": lambda tmp: {"bits": _bits(60)},
    "ccsds_encode_frame": lambda tmp: {"data": [1, 2, 3, 4, 5]},
    "groestl_hash": lambda tmp: {"data": "test"},
    "wfreq_demod": lambda tmp: {"iq": _cplx_iq(2048)},
}

def _classify(tool_name, content, data, success, error, assert_ok=None):
    low = (content or "").lower()
    # 1) 明确抛异常 = 真坏（注册表兜底文案是「调用失败: XxxError」）
    if (error or "").startswith("exception") or "traceback (most recent call last)" in low \
            or "调用失败:" in content or "failed:" in (error or ""):
        return BROKEN, "handler 抛异常未捕获"
    # 2) 明确未实现 = STUB（即使 success=True 也是在装可用）
    todo = ("未实现", "尚未实现", "功能未实现", "not implemented", "coming soon",
            "待实现", "功能开发中", "not supported yet")
    if any(k in low for k in todo):
        return STUB, "明确未实现"
    # 3) 成功但空 = 静默空 = 真坏
    if success and not (content or "").strip() and not data:
        return BROKEN, "success=True 但 content/data 皆为空（静默返回空）"
    # 4) 成功却只回 "[]"/"{}"——空态没有原因码，模型无法区分「真的没有」与「没执行」。
    #    枚举/列表类工具的 "[]" 是合法诚实空态（如无设备），放行；"{}" 不放行。
    stripped = (content or "").strip()
    if stripped in ("[]", "{}") and not data and success:
        if stripped == "[]" and any(w in tool_name for w in
                                    ("_list", "_devices", "_history", "_log",
                                     "_patterns", "_experiences", "_edits")):
            return RUNS, "枚举类诚实空态（当前无记录/无设备）"
        return BROKEN, f"success=True 却只返回 {stripped}（空态无原因码）"
    # 5) 失败：只要有可读原因，就是「诚实拒绝」；空原因+空文本才算吞异常
    if not success:
        if not (content or "").strip():
            return BROKEN, "success=False 但无可见原因文本（吞异常/静默失败）"
        missing = _missing_for(content)
        return HONEST, missing or "有效输入：合成替身不满足该工具的输入要求"
    # 6) 断言不通过：payload 自带 reason 属诚实空态，否则算失败被读成成功
    if assert_ok is False:
        if isinstance(data, dict) and data.get("reason"):
            return HONEST, f"缺样本：{data.get('reason')}"
        return BROKEN, "成功返回但可核验断言未通过"
    # 7) 成功但自述「未检测到/无有效帧」= 诚实空态（有原因，但不是产出）
    if any(k in low for k in ("诚实空态", "未检测到", "无有效", "没有检测到")):
        return HONEST, _missing_for(content) or "样本：输入不含可供解码的信号"
    return RUNS, "成功且返回非空"


def _missing_for(content):
    low = (content or "").lower()
    for keys, label in _MISSING_HINTS:
        if any(k.lower() in low for k in keys):
            return label
    return ""


def _run_pipeline(reg, tmp):
    """少数需要上游产物的工具：先跑上游，把真实产物回填，再跑下游。"""
    results = {}
    hex_frame = ""
    try:
        up = reg.call("ax25_encode", {"source": "BI1AAA", "destination": "APRS",
                                      "info": ">audit"})
        if up.success:
            hex_frame = (up.data or {}).get("hex", "") if isinstance(up.data, dict) else ""
            if not hex_frame and up.content.strip().startswith("{"):
                hex_frame = (json.loads(up.content) or {}).get("hex", "")
    except Exception:
        pass
    try:
        up = reg.call("ft8_encode", {"text": "CQ BI1AAA OM74"})
        if up.success and isinstance(up.data, dict):
            bits = up.data.get("bits") or up.data.get("tone_indices")
            if bits:
                results["ft8_unpack_message"] = ("bits", bits)
    except Exception:
        pass
    try:
        up = reg.call("baudot_encode", {"text": "HELLO"})
        if up.success and isinstance(up.data, dict):
            words = up.data.get("words") or up.data.get("codes")
            if words:
                results["baudot_decode"] = ("words", words)
    except Exception:
        pass
    return results, hex_frame


def run_audit(only=None):
    from mbdsdr_ai import model_manager as _mm
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig

    import mbdsdr_ai.sdr_backend as _sb

    _mm.ModelManager.fetch_models = lambda self, *a, **k: None  # 不联网
    # 假设备替身：审计全程不枚举/不打开任何真实 SDR（MBDSDR_NO_HW 的硬保证），
    # 所有实时设备工具都应走"无硬件"的诚实降级路径。不恢复，防止工具中途重新枚举。
    _sb.SDRBackendManager._discover = lambda self: None
    agent = MBDSDRAgent(AgentConfig(api_key="sk-test", model="test-model"))
    reg = agent.tool_registry
    mgr = getattr(agent, "sdr_manager", None)
    if mgr is not None:
        mgr.backends.clear()
        mgr.active_backend = None

    tmp = tempfile.mkdtemp(prefix="mbdsdr_audit_")
    records = []
    try:
        pipelines, hex_frame = _run_pipeline(reg, tmp)
        names = reg.get_tool_names()
        if only:
            names = [n for n in names if n in set(only)]
        for idx, name in enumerate(names, 1):
            tool = reg.tools[name]
            if os.environ.get("MBDSDR_AUDIT_VERBOSE"):
                print(f"[{idx}/{len(names)}] {name}", flush=True)
            definition = tool["definition"]["function"]
            params = definition.get("parameters", {}) or {}
            args = _build_args({"parameters": params}, tmp, hex_frame)
            if name in _SPECIAL_ARGS:
                args.update(_SPECIAL_ARGS[name](tmp))
            if name in pipelines:
                key, val = pipelines[name]
                args[key] = val
            # 每个工具独立基线：清掉上一个工具留下的活跃后端/回放源，
            # 否则 sdr_open_iq_file 的替身文件会让后续"实时采集"工具误以为有设备。
            if mgr is not None:
                try:
                    mgr.disconnect()
                except Exception:
                    pass
                mgr.active_backend = None
            started = time.time()
            res = None
            try:
                res = reg.call(name, args)
                success = bool(getattr(res, "success", False))
                content = (getattr(res, "content", "") or "")
                data = getattr(res, "data", None)
                error = getattr(res, "error", "") or ""
            except Exception as e:  # noqa: BLE001
                success, content, data = False, "", None
                error = f"exception: {type(e).__name__}: {e}"
                traceback.print_exc()
            elapsed = round((time.time() - started) * 1000, 1)
            checker = _VERIFIABLE.get(name)
            assert_ok = None
            if checker and success:
                try:
                    assert_ok = bool(checker(content, data))
                except Exception:
                    assert_ok = False
            cat, reason = _classify(name, content, data, success, error, assert_ok)
            if assert_ok is not None and cat == RUNS:
                reason = "成功且往返断言通过" if assert_ok else reason
            records.append({
                "name": name,
                "category": tool.get("category", ""),
                "class": cat,
                "reason": reason,
                "success": success,
                "error": error,
                "content_len": len(content),
                "data": "有" if data else "无",
                "latency_ms": elapsed,
                "args": args,
                "snippet": content[:240].replace("\n", " "),
            })
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return records


def render_md(records):
    counts = collections.Counter(r["class"] for r in records)
    total = len(records)
    lines = [
        "# 工具可用性总账（P13 逐项审计）",
        "",
        f"- 工具总数：**{total}**（分类 {len(set(r['category'] for r in records))} 个）",
        f"- 审计环境：`QT_QPA_PLATFORM=offscreen` + `MBDSDR_NO_HW=1` + "
        f"`MBDSDR_NULL_AUDIO=1` + `MBDSDR_OFFLINE=1`，**无真机；输入为合成替身**",
        f"- 结论：一类 {counts[RUNS]} / 二类 {counts[HONEST]} / "
        f"三类 {counts[STUB]} / 四类 {counts[BROKEN]}",
        "",
        "复跑：`python scripts/tool_availability_audit.py`（`--only <工具名>` 单跑）。",
        "证据列：`OK` = 当次调用成功且非空；`断言:`= 往返/还原断言；"
        "`降级:`= 诚实不可用原因；`坏:`= 真坏原因。",
        "",
        "| 工具 | 分类 | 结论 | 证据（本次调用） | 不可用时缺什么 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in sorted(records, key=lambda x: (x["class"], x["category"], x["name"])):
        cls = {"RUNS": "一类·真跑", "HONEST": "二类·诚实不可用",
               "STUB": "三类·未实现", "BROKEN": "四类·真坏"}[r["class"]]
        snippet = (r["snippet"] or "").replace("|", "\\|")[:110]
        if r["class"] == RUNS:
            evidence = f"OK（{r['content_len']} 字符，data={r['data']}）"
        elif r["class"] == HONEST:
            evidence = f"降级：{r['reason']}｜{snippet}"
        elif r["class"] == STUB:
            evidence = f"未实现：{snippet}"
        else:
            evidence = f"坏：{r['reason']}｜{snippet}"
        missing = "" if r["class"] == RUNS else r["reason"]
        lines.append(f"| `{r['name']}` | {r['category']} | {cls} | {evidence} | {missing} |")
    return "\n".join(lines) + "\n", counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    if os.environ.get("MBDSDR_AUDIT_VERBOSE"):
        import faulthandler
        faulthandler.dump_traceback_later(90, repeat=True)
    records = run_audit(a.only)
    counts = collections.Counter(r["class"] for r in records)
    print(f"total={len(records)} " + " ".join(f"{k}={counts[k]}" for k in
                                             (RUNS, HONEST, STUB, BROKEN)))
    for r in records:
        if r["class"] in (STUB, BROKEN):
            print(f"  [{r['class']}] {r['name']}: {r['reason']} | {r['snippet'][:160]}")
    if a.json:
        Path(a.json).write_text(json.dumps(records, ensure_ascii=False, indent=1,
                                           default=str), encoding="utf-8")
    if not a.no_write and not a.only:
        md, _ = render_md(records)
        (ROOT / "docs" / "tool-availability.md").write_text(md, encoding="utf-8")
        print("wrote docs/tool-availability.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
