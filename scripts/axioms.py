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

from tools.axioms.checks import skip_report  # noqa: E402
from tools.axioms.model import DEFAULT_WAIVER_FILE, Repo, apply_waivers, load_waivers  # noqa: E402
from tools.axioms.ratchet import (  # noqa: E402
    DEFAULT_ACCEPTED,
    DEFAULT_BASELINE,
    accept_record,
    accepted_fingerprints,
    append_accepted,
    build_baseline,
    compare,
    load_accepted,
    load_baseline,
    render,
    render_accept,
    write_baseline,
)
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
    parser.add_argument("--ratchet", dest="ratchet", action="store_true", default=None,
                        help="启用棘轮（默认：基线存在即启用）")
    parser.add_argument("--no-ratchet", dest="ratchet", action="store_false",
                        help="本次不做棘轮比对")
    parser.add_argument("--baseline", default="", help=f"基线路径（默认 {DEFAULT_BASELINE}）")
    parser.add_argument("--accept-current", action="store_true",
                        help="把当前违规写为新基线；必须带 --reason，并会打印接受清单")
    parser.add_argument("--accept-reason", "--reason", dest="accept_reason", default="",
                        help="写基线时记录的理由（必填，落盘到 docs/axioms-accepted.json）")
    args = parser.parse_args(argv)

    if args.accept_current and args.rule:
        print("拒绝：--accept-current 不能与 --rule 一起用（会只保留被过滤的条目）。", file=sys.stderr)
        return 2
    if args.accept_current and not args.accept_reason.strip():
        print("拒绝写基线：--accept-current 必须带 --reason，说明这次为什么接受这些新增违规。\n"
              "静默刷新基线等于把闸门关掉。", file=sys.stderr)
        return 2

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

    # 棘轮始终看全量（未过滤），否则 --rule 会把基线算残。
    all_findings = [f for dim in dims for f in dim.findings]
    skips_snapshot = skip_report()

    if args.rule:
        rule = args.rule.upper()
        for dim in dims:
            dim.findings = [f for f in dim.findings if f.rule.upper() == rule or dim.id == rule]

    self_test = [] if args.no_self_test else run_selftest(repo)
    report = Report8(axioms, dims, self_test, skips_snapshot)

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

    baseline_path = args.baseline or DEFAULT_BASELINE
    if args.accept_current:
        if any(not t["ok"] for t in self_test):
            print("\n拒绝写基线：反证自检未通过，检查器本身不可信。", file=sys.stderr)
            return 1
        old = load_baseline(repo, baseline_path)
        old_total = len((old or {}).get("entries", {}))
        # 首次建基线时「当前全部」都是这次被接受的条目，必须一并记账，
        # 否则接受清单会是空的——等于审计第一次就是瞎的。
        result = compare(all_findings, old if old else {"entries": {}})
        head = repo.head()
        new_data = build_baseline(repo, all_findings, head, args.accept_reason)
        full = write_baseline(repo, baseline_path, new_data)
        who = os.environ.get("MBDSDR_CLAIM_OWNER") or os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
        session = (os.environ.get("MBDSDR_SESSION_ID") or os.environ.get("TRAE_SESSION_ID")
                   or os.environ.get("CLAUDE_SESSION_ID") or "")
        record = accept_record(repo, result, head, args.accept_reason, who, session)
        ledger = append_accepted(repo, record)
        print(render_accept(result, old_total, int(new_data["total_active"]), full,
                            args.accept_reason, record, ledger))
        return 0

    ratchet_on = args.ratchet if args.ratchet is not None else True
    if not ratchet_on:
        return 0
    baseline = load_baseline(repo, baseline_path)
    if baseline is None:
        print(f"\n── 棘轮 ──\n尚未建立基线：运行 python scripts/axioms.py --accept-current 建立。"
              f"（路径 {baseline_path}）")
        return 0
    result = compare(all_findings, baseline)
    accepted = accepted_fingerprints(load_accepted(repo, DEFAULT_ACCEPTED))
    print(render(result, baseline_path, accepted, DEFAULT_ACCEPTED, result.current_fps))
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
