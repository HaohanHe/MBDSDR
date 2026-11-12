"""八维合规报告的输出（文本 / Markdown / JSON）。"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from typing import Dict, List, Sequence

from .checks import build_axioms
from .model import Axiom, Finding, priority_score
from .survey import Dimension


class Report8:
    def __init__(self, axioms: Sequence[Axiom], dims: Sequence[Dimension], self_test: Sequence[dict]):
        self.axioms = list(axioms)
        self.dims = list(dims)
        self.self_test = list(self_test)
        self._by_id = {a.id: a for a in self.axioms}

    class _Meta:
        def __init__(self, loss: int, freq: int, evidence: int):
            self.loss = loss
            self.freq = freq
            self.evidence = evidence

    def _meta_for(self, rule: str):
        exact = self._by_id.get(rule)
        if exact is not None:
            return exact
        base = rule.split("-")[0]
        return self._by_id.get(base) or {
            "D1": self._Meta(2, 3, 2), "D2": self._Meta(3, 3, 2),
            "D3": self._Meta(1, 3, 2), "D4": self._Meta(3, 2, 2),
            "D5": self._Meta(3, 2, 2), "D6": self._Meta(2, 2, 2),
            "D7": self._Meta(1, 1, 2), "D8": self._Meta(1, 1, 2),
        }.get(base, self._Meta(1, 1, 2))

    # -- 汇总 --
    def summary(self) -> Dict[str, object]:
        by_dim = {}
        for d in self.dims:
            active = d.active()
            by_dim[d.id] = {
                "title": d.title,
                "total": len(d.findings),
                "active": len(active),
                "waived": len(d.waived()),
                "must_fix": sum(1 for f in active if f.severity == "must-fix"),
                "review": sum(1 for f in active if f.severity != "must-fix"),
            }
        all_findings = [f for d in self.dims for f in d.findings]
        active_all = [f for f in all_findings if not f.waived]
        return {
            "total": len(all_findings),
            "active": len(active_all),
            "waived": len(all_findings) - len(active_all),
            "must_fix": sum(1 for f in active_all if f.severity == "must-fix"),
            "review": sum(1 for f in active_all if f.severity != "must-fix"),
            "self_test_failed": sum(1 for t in self.self_test if not t.get("ok")),
            "by_dimension": by_dim,
        }

    def ranked(self) -> List[Finding]:
        items = [f for d in self.dims for f in d.findings]
        return sorted(items, key=lambda f: (-priority_score(f, self._meta_for(f.rule)), f.path, f.line))

    # -- 文本 --
    def render_text(self, max_per_dim: int = 40, prioritize: bool = False) -> str:
        s = self.summary()
        out: List[str] = []
        out.append("=" * 78)
        out.append("工程治理合规报告（八维）—— 只读静态，全部输入来自本仓库")
        out.append("=" * 78)
        out.append(
            f"违规 {s['total']} 条：应修 {s['must_fix']}，复核 {s['review']}，豁免 {s['waived']}；"
            f"反证自检 {'全部通过' if not s['self_test_failed'] else '失败 %d 条' % s['self_test_failed']}"
        )
        out.append("")
        out.append("维度    违规  应修  复核  豁免  标题")
        for d in self.dims:
            row = s["by_dimension"][d.id]
            out.append(
                f"{d.id:<6} {row['active']:>4} {row['must_fix']:>5} {row['review']:>5} "
                f"{row['waived']:>5}  {d.title}"
            )

        if prioritize:
            out.append("")
            out.append("── 按派活规则排序（先做上面的）──")
            for i, f in enumerate(self.ranked()[:30], 1):
                a = self._meta_for(f.rule)
                out.append(
                    f"{i:>3}. [{priority_score(f, a):>4}] {f.rule} {f.path}:{f.line}"
                    f"{'（豁免）' if f.waived else ''}"
                )

        for d in self.dims:
            active = d.active()
            waived = d.waived()
            out.append("")
            out.append(f"── {d.id} {d.title} ── 违规 {len(active)} / 豁免 {len(waived)}")
            out.append(f"   规范：{d.rule}")
            for fact in d.facts:
                out.append(f"   事实：{fact}")
            for f in active[:max_per_dim]:
                tag = "应修" if f.severity == "must-fix" else "复核"
                out.append(f"   [{tag}] {f.rule} {f.path}:{f.line}  {f.snippet}")
                out.append(f"        为什么违反：{f.why}")
                out.append(f"        怎么修：{f.fix}")
            if len(active) > max_per_dim:
                out.append(f"   … 其余 {len(active) - max_per_dim} 条省略（--max-per-dim 调整）")
            for f in waived:
                out.append(f"   [豁免] {f.rule} {f.path}:{f.line}  {f.waiver_reason}")

        out.append("")
        out.append("── 反证自检（真实违规片段必须红，对应修复必须绿）──")
        for t in self.self_test:
            out.append(
                f"   {'PASS' if t.get('ok') else 'FAIL'} {t['rule']:<3} {t['name']}："
                f"正例 {t.get('positive_hits')} / 负例 {t.get('negative_hits')}"
            )
            if not t.get("ok"):
                out.append(f"        {t.get('detail', '')}  来源：{t.get('provenance', '')}")
        out.append("")
        return "\n".join(out)

    # -- Markdown --
    def render_markdown(self, max_per_dim: int = 40) -> str:
        s = self.summary()
        out: List[str] = []
        out.append("# 工程治理合规报告（八维）")
        out.append("")
        out.append(f"- 生成时间：{date.today().isoformat()}")
        out.append(f"- 违规：{s['total']}（应修 {s['must_fix']}，复核 {s['review']}，豁免 {s['waived']}）")
        out.append(f"- 反证自检：{'全部通过' if not s['self_test_failed'] else '失败 %d 条' % s['self_test_failed']}")
        out.append("")
        out.append("## 八维总览")
        out.append("")
        out.append("| 维度 | 标题 | 违规 | 应修 | 复核 | 豁免 |")
        out.append("|---|---|---:|---:|---:|---:|")
        for d in self.dims:
            r = s["by_dimension"][d.id]
            out.append(f"| {d.id} | {d.title} | {r['active']} | {r['must_fix']} | {r['review']} | {r['waived']} |")
        out.append("")
        out.append("## 反证自检")
        out.append("")
        out.append("| 规则 | 反证 | 结果 | 正例 | 负例 | 来源 |")
        out.append("|---|---|---|---:|---:|---|")
        for t in self.self_test:
            out.append(
                f"| {t['rule']} | {t['name']} | {'PASS' if t.get('ok') else 'FAIL'} | "
                f"{t.get('positive_hits')} | {t.get('negative_hits')} | {t.get('provenance', '')} |"
            )
        out.append("")
        for d in self.dims:
            active = d.active()
            waived = d.waived()
            out.append(f"## {d.id} {d.title}（违规 {len(active)}，豁免 {len(waived)}）")
            out.append("")
            out.append(f"> 规范：{d.rule}")
            out.append("")
            for fact in d.facts:
                out.append(f"- 事实：{fact}")
            out.append("")
            for f in active[:max_per_dim]:
                tag = "应修" if f.severity == "must-fix" else "复核"
                out.append(f"- **[{tag}]** `{f.rule}` `{f.path}:{f.line}` — {f.snippet}")
                out.append(f"  - 为什么：{f.why}")
                out.append(f"  - 怎么修：{f.fix}")
            if len(active) > max_per_dim:
                out.append(f"- … 其余 {len(active) - max_per_dim} 条省略")
            for f in waived:
                out.append(f"- **豁免** `{f.rule}` `{f.path}:{f.line}` — {f.waiver_reason}")
            out.append("")
        return "\n".join(out)

    # -- JSON --
    def to_dict(self) -> Dict[str, object]:
        return {
            "summary": self.summary(),
            "axioms": [a.meta() for a in self.axioms],
            "dimensions": [
                {
                    "id": d.id,
                    "title": d.title,
                    "rule": d.rule,
                    "facts": d.facts,
                    "findings": [
                        {
                            "rule": f.rule,
                            "path": f.path,
                            "line": f.line,
                            "snippet": f.snippet,
                            "why": f.why,
                            "fix": f.fix,
                            "severity": f.severity,
                            "waived": f.waived,
                            "waiver_reason": f.waiver_reason,
                            "waiver_expired": f.waiver_expired,
                            "priority": priority_score(f, self._meta_for(f.rule)),
                        }
                        for f in d.findings
                    ],
                }
                for d in self.dims
            ],
            "self_test": self.self_test,
        }
