# -*- coding: utf-8 -*-
"""真实模型「引导深度」冒烟：新手 / 老火腿 / 让模型直接来。

为什么要有这个脚本
------------------
引导深度必须由模型判断，不能写成固定问卷。系统提示词里已加「引导深度由你
判断」策略（`mbdsdr_ai/context_manager.py`），但**提示词写得对不等于模型
照做**。本脚本用真实 API 跑三段对话，把回答与工具调用记下来，供人复核；
同时给几条可机检的硬边界（API 错误、空回答、危险写工具）。

没有 API key 时**不联网、不伪造结果**：直接打印 SKIP 并返回 0。

配置来源（优先级与产品一致）：
  1. 环境变量 MBDSDR_API_KEY / MBDSDR_BASE_URL / MBDSDR_MODEL
  2. ~/.mbdsdr/config.json 的 api_key / base_url / model

用法：
    python scripts/ai_guidance_smoke.py
    python scripts/ai_guidance_smoke.py --persona novice --out output/ai/x.json

退出码：0=跑完（SKIP 也是 0）；1=出现硬失败（API 错误/空回答）。
软观察（回复过长、连续提问、疑似固定流程）只进报告，不直接判红——
它们需要人看一眼语义，避免把正常追问误判成问卷。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

#: 行首编号清单（"1. " / "1) "），用来抓模型无视「不要编号」的风格漂移。
_NUMBERED_LIST_RE = re.compile(r"(?m)^\s*\d+[.)、]\s")

#: 本产品指天视图**没有**的 UI 交互词（2026-11-22 真实 8B 连续编造过
#: 滑动屏幕/输入方位/罗盘图标/准星自动追踪）。出现即软观察，供人复核
#: 护栏是否漏过；真实的“点击按钮/转动手机”不在此列。
_UI_AFFORDANCE_WORDS = ("滑动", "拖动", "长按", "双指", "输入框",
                        "输入方位", "罗盘图标", "自动追踪", "自动跟踪")

#: 不该在「只问怎么用」的对话里出现的动手写工具（发现即记硬观察，供人复核）。
WRITE_TOOLS = {
    "set_frequency", "set_mode", "set_gain", "set_sample_rate", "set_bandwidth",
    "start_recording", "stop_recording", "gimbal_point", "gimbal_track_satellite",
    "mount_slew", "tx_transmit", "sdr_transmit_cw", "sdr_transmit_iq",
}

PERSONAS = {
    "novice": [
        "我完全不懂无线电，也不懂天文。刚买了台天文望远镜，想把手机架上去看星星。"
        "你别给我讲原理，告诉我现在第一步做什么。"
    ],
    "expert": [
        "我是老火腿，SDR 和天线都接好了，直接告诉我 145.500 MHz NFM 怎么收，别讲基础。"
    ],
    "direct": [
        "我完全不懂，手机架好了，接下来呢？",
        "好，你直接来吧。",
    ],
}


def load_agent_config():
    from mbdsdr_ai.config import AgentConfig, ConfigStore, CONFIG_PATH

    cfg = AgentConfig()
    path = os.path.expanduser(CONFIG_PATH)
    try:
        data = ConfigStore(path).read() or {}
    except Exception:
        data = {}
    file_cfg = AgentConfig.from_dict(data) if data else AgentConfig()
    if not cfg.api_key:
        cfg.api_key = file_cfg.api_key
    if not cfg.api_key and not data:
        return cfg
    # base_url/model 以文件为准（与桌面配置一致）；env 仅在文件仍是默认时兜底。
    if file_cfg.base_url:
        cfg.base_url = file_cfg.base_url
    if file_cfg.model:
        cfg.model = file_cfg.model
    return cfg


def _tool_name(tc):
    fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
    return fn.get("name") or (tc.get("name") if isinstance(tc, dict) else "") or "?"


def _observations(persona: str, turns) -> list:
    notes = []
    for i, t in enumerate(turns, 1):
        content = t["content"]
        calls = set(t["tool_calls"])
        writes = sorted(calls & WRITE_TOOLS)
        if writes:
            notes.append(f"{persona}#{i} 调用了写工具 {writes}（只问用法时需人复核）")
        if len(content) > 1200:
            notes.append(f"{persona}#{i} 回复 {len(content)} 字（可能一次灌输过多）")
        questions = content.count("？") + content.count("?")
        if questions > 2:
            notes.append(f"{persona}#{i} 连续提问 {questions} 次（可能退化问卷）")
        if persona == "expert":
            basics = [w for w in ("什么是", "基础", "科普", "先了解") if w in content]
            if basics:
                notes.append(f"{persona}#{i} 对专家讲了基础词 {basics}")
        if any(w in content for w in ("Star Walk", "SkyView", "Stellarium")):
            notes.append(f"{persona}#{i} 推荐了外部 App（用户未明确要求时属偏离本产品）")
        if "arm_manual" in content or "confirm_token" in content:
            notes.append(f"{persona}#{i} 出现确认门内部术语/令牌，疑似编造操作步骤")
        if len(_NUMBERED_LIST_RE.findall(content)) >= 2:
            notes.append(f"{persona}#{i} 出现行首编号清单（违反「只给当前一步」）")
        ui_hits = [w for w in _UI_AFFORDANCE_WORDS if w in content]
        if ui_hits:
            notes.append(
                f"{persona}#{i} 出现未核实的 UI 交互词 {ui_hits}"
                "（护栏可能漏过，需人复核）")
        if persona == "novice" and i == 1 and not content:
            notes.append(f"{persona}#{i} 空回答")
    return notes


def run(cfg, personas, max_out):
    from mbdsdr_ai.agent import MBDSDRAgent

    cfg.max_output_tokens = max_out
    cfg.timeout = 120
    cfg.tool_timeout_s = 15.0
    cfg.enable_self_evolution = False
    cfg.enable_session_log = False

    report = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "model": cfg.model,
        "base_url": cfg.base_url,
        "api_key_present": bool(cfg.api_key),
        "personas": [],
    }
    hard_fail = False
    for name in personas:
        agent = MBDSDRAgent(cfg)
        turns = []
        for text in PERSONAS[name]:
            t0 = time.time()
            r = agent.chat(text)
            sel = getattr(agent, "last_tool_selection", None)
            cands = list(getattr(sel, "kept_names", []) or [])
            results = []
            for x in (r.get("tool_results") or []):
                if not isinstance(x, dict):
                    continue
                results.append({
                    "tool": x.get("tool_name") or "?",
                    "success": bool(x.get("success")),
                    "preview": str(x.get("content") or "")[:200],
                    "error": str(x.get("error") or "")[:160],
                })
            turns.append({
                "user": text,
                "elapsed_s": round(time.time() - t0, 2),
                "error": r.get("error") or r.get("error_code") or "",
                "content": r.get("content") or "",
                "tool_calls": [_tool_name(tc) for tc in (r.get("tool_calls") or [])],
                "tool_results": results,
                "candidates": {
                    "count": len(cands),
                    "has_skill_tools": all(
                        n in cands for n in ("skill_list", "skill_load")),
                    "sky_tools": sorted(n for n in cands if n.startswith("sky_"))[:12],
                },
            })
            if r.get("error"):
                hard_fail = True
        item = {"persona": name, "turns": turns,
                "observations": _observations(name, turns)}
        report["personas"].append(item)
        print(f"\n=== {name} ===")
        for i, t in enumerate(turns, 1):
            print(f"--- turn {i} {t['elapsed_s']}s error={t['error']!r} "
                  f"tools={t['tool_calls']}")
            print(t["content"].replace("\n", " ")[:400])
        for note in item["observations"]:
            print(f"  ! {note}")
    report["verdict"] = "hard_fail" if hard_fail else "ran"
    return report, hard_fail


def main() -> int:
    # 只在真正跑探针时改环境：import 本模块（测试会 import 纯函数）不得
    # 给整个 pytest 进程留下 MBDSDR_NO_HW，否则会影响其他硬件用例。
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", choices=sorted(PERSONAS) + ["all"], default="all")
    ap.add_argument("--max-output-tokens", type=int, default=1024)
    ap.add_argument("--out", default=None)
    ap.add_argument("--allow-hw", action="store_true",
                    help="允许探测真实硬件（默认 NO_HW，只测引导文本；"
                         "要验证「专家-直接调谐」必须带此开关）")
    args = ap.parse_args()
    if not args.allow_hw:
        os.environ.setdefault("MBDSDR_NO_HW", "1")
    os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")

    cfg = load_agent_config()
    if not cfg.api_key:
        print("SKIP: 没有 API key（环境变量 MBDSDR_API_KEY 或 "
              "~/.mbdsdr/config.json 的 api_key）。未联网、未生成任何结果。")
        return 0

    names = sorted(PERSONAS) if args.persona == "all" else [args.persona]
    report, hard_fail = run(cfg, names, args.max_output_tokens)

    out_dir = os.path.join(ROOT, "output", "ai")
    os.makedirs(out_dir, exist_ok=True)
    out_path = args.out or os.path.join(
        out_dir, f"guidance_smoke_{dt.datetime.now():%Y%m%d_%H%M%S}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nverdict={report['verdict']}  结果: {out_path}")
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
