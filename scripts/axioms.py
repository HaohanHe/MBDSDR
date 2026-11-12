#!/usr/bin/env python
"""一条命令跑完八维治理测绘。

用法：
    python scripts/axioms.py                    # 八维合规报告（含反证自检）
    python scripts/axioms.py --prioritize       # 额外输出按规则排序的派活清单
    python scripts/axioms.py --self-test-only   # 只验反证是否空过
    python scripts/axioms.py --json out.json --report docs/xxx.md

退出码：0 = 测绘完成（违规不阻塞）；1 = 反证自检失败；2 = 工具自身错误。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.axioms.model import DEFAULT_WAIVER_FILE, Repo, apply_waivers, load_waivers  # noqa: E402
from tools.axioms.report8 import Report8  # noqa: E402
from tools.axioms.selftest import run as run_selftest  # noqa: E402
from tools.axioms.survey import collect_all  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="八维工程治理测绘（只读）")
    parser.add_argument("--root", default=ROOT, help="仓库根目录")
    parser.add_argument("--json", default="", help="把完整结果写入 JSON")
    parser.add_argument("--report", default="", help="把 Markdown 报告写入文件")
    parser.add_argument("--max-per-dim", type=int, default=40, help="每维度最多打印的明细条数")
    parser.add_argument("--prioritize", action="store_true", help="额外输出派活优先顺序")
    parser.add_argument("--no-self-test", action="store_true", help="跳过反证自检")
    parser.add_argument("--self-test-only", action="store_true", help="只跑反证自检")
    parser.add_argument("--rule", default="", help="只显示某条规则（如 A03 / D1）")
    parser.add_argument("--fail-on", choices=["none", "must-fix"], default="none",
                        help="默认不因违规失败；接入 CI 时用 must-fix")
    args = parser.parse_args(argv)

    repo = Repo(args.root)

    if args.self_test_only:
        results = run_selftest(repo)
        failed = [r for r in results if not r["ok"]]
        for r in results:
            print(f"{'PASS' if r['ok'] else 'FAIL'} {r['rule']:<3} {r['name']}："
                  f"正例 {r['positive_hits']} / 负例 {r['negative_hits']}"
                  + ("" if r["ok"] else f"\n     {r['detail']}  来源：{r['provenance']}"))
        print(f"\n反证自检 {len(results)} 条，失败 {len(failed)} 条")
        return 0 if not failed else 1

    axioms, dims = collect_all(repo)
    waivers = load_waivers(repo, DEFAULT_WAIVER_FILE)
    for dim in dims:
        apply_waivers(dim.findings, waivers)

    if args.rule:
        rule = args.rule.upper()
        for dim in dims:
            dim.findings = [f for f in dim.findings if f.rule.upper() == rule or dim.id == rule]

    self_test = [] if args.no_self_test else run_selftest(repo)
    report = Report8(axioms, dims, self_test)

    print(report.render_text(max_per_dim=args.max_per_dim, prioritize=args.prioritize))

    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"JSON 已写入 {args.json}")
    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w", encoding="utf-8", newline="\n") as f:
            f.write(report.render_markdown(max_per_dim=args.max_per_dim))
        print(f"Markdown 报告已写入 {args.report}")

    if any(not t["ok"] for t in self_test):
        return 1
    if args.fail_on == "must-fix" and report.summary()["must_fix"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
