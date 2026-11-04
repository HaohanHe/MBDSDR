# -*- coding: utf-8 -*-
"""P7.1 工具选择准确性回归（离线、确定性，不需要真实模型）。

测什么
------
真模型的语义选择要联网才能测；本脚本测的是**发给模型的候选工具集**这一层：
  * 标准答案里的工具是否存在于注册表（能力覆盖）；
  * 该工具是否被 `tool_budget.select_tools` 保留进候选 schema；
  * 明显不该因本请求进入候选的危险工具是否被裁掉（误调工具）。

准确率 = 三项同时满足的用例数 / 用例总数。基准集在
`tests/fixtures/ai_intent_corpus.json`，加用例只需加一条 JSON。

用法：
    python scripts/ai_intent_regression.py [--budget 8192] [--limit N]
        [--out output/ai/intent_regression_<时间戳>.json]

退出码：准确率 < MIN_ACCURACY 时非 0（CI 可作门禁）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 门禁下限：只许随基准集扩容而提高，不许为了变绿而降低（tests 钉住）
MIN_ACCURACY = 0.95

CORPUS_PATH = os.path.join(ROOT, "tests", "fixtures", "ai_intent_corpus.json")
DEFAULT_BUDGET = 8192          # 与 32k 窗口的 25% 预算一致


def load_corpus(path: str = CORPUS_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def evaluate_cases(cases: list, registry_tools: dict, definitions: list,
                   budget: int, categories: dict | None = None) -> dict:
    """跑基准集；返回机器可读报告（纯函数，测试可直接调）。"""
    from mbdsdr_ai.tool_budget import select_tools

    results = []
    passed = 0
    for case in cases:
        sel = select_tools(definitions, case["request"],
                           budget_tokens=budget, categories=categories)
        kept = set(sel.kept_names)
        catalog_missing = [n for n in case.get("expect_kept", [])
                           if n not in registry_tools]
        missing = [n for n in case.get("expect_kept", []) if n not in kept]
        wrong = [n for n in case.get("forbid_kept", []) if n in kept]
        ok = not catalog_missing and not missing and not wrong
        if ok:
            passed += 1
        results.append({
            "id": case["id"],
            "category": case["category"],
            "request": case["request"],
            "ok": ok,
            "catalog_missing": catalog_missing,
            "missing_from_candidates": missing,
            "wrongly_kept": wrong,
            "kept_count": len(kept),
            "tokens": sel.tokens,
        })

    total = len(results)
    accuracy = round(passed / total, 4) if total else 0.0
    return {
        "total": total,
        "passed": passed,
        "accuracy": accuracy,
        "budget_tokens": budget,
        "catalog_size": len(registry_tools),
        "cases": results,
        "failures": [r for r in results if not r["ok"]],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig

    agent = MBDSDRAgent(AgentConfig(api_key="sk-test"))
    tools = agent.tool_registry.tools
    defs = agent.tool_registry.get_tool_definitions(language="zh")
    cats = {name: str((meta or {}).get("category", ""))
            for name, meta in tools.items()}

    corpus = load_corpus()
    cases = corpus["cases"]
    if args.limit:
        cases = cases[:args.limit]

    report = evaluate_cases(cases, tools, defs, args.budget, cats)
    report["version"] = corpus.get("version")
    report["generated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    report["offline"] = True
    report["note"] = ("仅验证确定性候选层（注册表覆盖 + 预算裁剪）；"
                      "未调用真实模型，真模型语义选择准确率未验证。")

    out_dir = os.path.join(ROOT, "output", "ai")
    os.makedirs(out_dir, exist_ok=True)
    out_path = args.out or os.path.join(
        out_dir, f"intent_regression_{dt.datetime.now():%Y%m%d_%H%M%S}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"准确率 {report['accuracy']:.1%}（{report['passed']}/{report['total']}），"
          f"候选工具预算 {args.budget} tokens，注册表 {report['catalog_size']} 个工具")
    for r in report["failures"]:
        print(f"  ✗ {r['id']} [{r['category']}] {r['request']}")
        if r["catalog_missing"]:
            print(f"      注册表缺工具: {r['catalog_missing']}")
        if r["missing_from_candidates"]:
            print(f"      候选集漏掉: {r['missing_from_candidates']}")
        if r["wrongly_kept"]:
            print(f"      误收工具: {r['wrongly_kept']}")
    print(f"结果: {out_path}")
    return 0 if report["accuracy"] >= MIN_ACCURACY else 1


if __name__ == "__main__":
    sys.exit(main())
