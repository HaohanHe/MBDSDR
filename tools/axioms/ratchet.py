"""棘轮：存量只减不增，新增违规即红。

设计要点（为什么不是「总数 272」）：

* 基线按**条目指纹**存，不是按数量。指纹 = sha1(规则 + 路径 + 规范化片段 + 同键序号)，
  不含行号——行号会随无关改动漂移，含行号会让一次 import 变成几百条「新增+消失」。
* 只把**未豁免**的条目计入棘轮额度。豁免由 D8 的 review_by 管理：到期后该条
  重新变成应修，从而以「新增」身份触发棘轮，不会被旧基线悄悄吸收。
* 修掉一条，基线不会自动缩小；`--accept-current` 才落盘，并强制打印
  「这次接受了哪几条新增」「哪几条消失」两份清单，避免静默刷新。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from .model import Finding, Repo

DEFAULT_BASELINE = "docs/axioms-baseline.json"
SCHEMA = 2

# 有些规则的片段里会写「另一个文件:行号」（例如 D3-name-role 指认同名实现）。
# 那个行号与被记条目无关，对方文件挪一行就会造出一对假的「新增+消失」，所以指纹里抹平。
_EMBEDDED_LINE = re.compile(r"(?P<path>[A-Za-z0-9_./\\-]+\.(?:py|md|json|txt|toml|ya?ml|ini|cfg)):\d+")


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def snippet_key(text: str) -> str:
    """指纹用的片段：折叠空白，并抹掉片段内嵌的 `文件:行号`。"""
    return _EMBEDDED_LINE.sub(r"\g<path>:#L", _norm(text))


def fingerprint(rule: str, path: str, snippet: str, ordinal: int) -> str:
    raw = f"{rule}\0{path}\0{snippet_key(snippet)}\0{ordinal}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def fingerprint_map(findings: Sequence[Finding]) -> Dict[str, Finding]:
    """给每条违规算一个不含行号的稳定指纹。

    同一 (规则, 路径, 片段) 出现多次时，按行号顺序追加序号，保证可区分。
    """
    return {fp: f for fp, _, f in fingerprint_pairs(findings)}


def fingerprint_pairs(findings: Sequence[Finding]) -> List[Tuple[str, int, Finding]]:
    groups: Dict[Tuple[str, str, str], int] = {}
    out: List[Tuple[str, int, Finding]] = []
    for f in sorted(findings, key=lambda x: (x.rule, x.path, x.line, x.snippet)):
        key = (f.rule, f.path, snippet_key(f.snippet))
        ordinal = groups.get(key, 0)
        groups[key] = ordinal + 1
        out.append((fingerprint(f.rule, f.path, f.snippet, ordinal), ordinal, f))
    return out


def _entry(f: Finding, ordinal: int) -> Dict[str, object]:
    return {
        "rule": f.rule,
        "path": f.path,
        "line": f.line,
        "snippet": _norm(f.snippet),
        "ordinal": ordinal,
        "severity": f.severity,
    }


def load_baseline(repo: Repo, path: str) -> Optional[Dict[str, object]]:
    full = path if os.path.isabs(path) else os.path.join(repo.root, path)
    if not os.path.isfile(full):
        return None
    with open(full, encoding="utf-8") as fh:
        return json.load(fh)


def build_baseline(repo: Repo, findings: Sequence[Finding], head: str,
                   reason: str = "") -> Dict[str, object]:
    active = [f for f in findings if not f.waived]
    entries = {fp: _entry(f, ordinal) for fp, ordinal, f in fingerprint_pairs(active)}
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "head": head,
        "reason": reason,
        "total_active": len(entries),
        "waived_not_tracked": len([f for f in findings if f.waived]),
        "entries": entries,
    }


def entry_map(baseline: Dict[str, object]) -> Dict[str, Dict[str, object]]:
    """从基线条目重算指纹。

    条目里存的是指纹的组成部分（规则/路径/片段/序号），不是指纹本身；
    这样指纹算法（例如片段规范化）改进后，旧基线仍然对得上，不会被误判成一片新增。
    """
    out: Dict[str, Dict[str, object]] = {}
    for e in (baseline.get("entries") or {}).values():
        fp = fingerprint(str(e.get("rule", "")), str(e.get("path", "")),
                         str(e.get("snippet", "")), int(e.get("ordinal", 0)))
        out[fp] = e
    return out


def write_baseline(repo: Repo, path: str, data: Dict[str, object]) -> str:
    full = path if os.path.isabs(path) else os.path.join(repo.root, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return full


@dataclass
class RatchetResult:
    baseline_total: int = 0
    current_total: int = 0
    new: List[Tuple[str, Finding]] = field(default_factory=list)
    gone: List[Tuple[str, Dict[str, object]]] = field(default_factory=list)
    escalated: List[Tuple[str, Finding]] = field(default_factory=list)
    downgraded: List[Tuple[str, Finding]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.new and not self.escalated

    @property
    def failed(self) -> bool:
        return bool(self.new or self.escalated)


def compare(current: Sequence[Finding], baseline: Dict[str, object]) -> RatchetResult:
    entries = entry_map(baseline)
    active = [f for f in current if not f.waived]
    current_map = fingerprint_map(active)
    result = RatchetResult(baseline_total=len(entries), current_total=len(current_map))
    for fp, f in current_map.items():
        if fp not in entries:
            result.new.append((fp, f))
            continue
        old_sev = str(entries[fp].get("severity", "must-fix"))
        if f.severity == "must-fix" and old_sev != "must-fix":
            result.escalated.append((fp, f))
        elif f.severity != "must-fix" and old_sev == "must-fix":
            result.downgraded.append((fp, f))
    for fp, e in entries.items():
        if fp not in current_map:
            result.gone.append((fp, e))
    return result


def describe(entry: Dict[str, object]) -> str:
    return f"{entry.get('rule')} {entry.get('path')}:{entry.get('line')}  {entry.get('snippet')}"


def render(result: RatchetResult, baseline_path: str) -> str:
    lines = ["", "── 棘轮（存量只减不增）──"]
    lines.append(
        f"基线 {result.baseline_total} 条（豁免项不占额度，由 D8 到期机制管理）；"
        f"当前 {result.current_total} 条；新增 {len(result.new)} / 消失 {len(result.gone)} / "
        f"升级 {len(result.escalated)} / 降级 {len(result.downgraded)}"
    )
    if result.new:
        lines.append("新增违规（棘轮红）：")
        for fp, f in result.new:
            lines.append(f"  + [{fp}] {f.rule} {f.path}:{f.line}  {_norm(f.snippet)}")
            lines.append(f"      为什么：{f.why}")
            lines.append(f"      怎么修：{f.fix}")
    if result.escalated:
        lines.append("严重度升级（棘轮红）：")
        for fp, f in result.escalated:
            lines.append(f"  ^ [{fp}] {f.rule} {f.path}:{f.line}  复核 → 应修  {_norm(f.snippet)}")
    if result.gone:
        lines.append("已消失（下次 --accept-current 时从基线下调）：")
        for fp, e in result.gone:
            lines.append(f"  - [{fp}] {describe(e)}")
    if result.downgraded:
        lines.append("已降级为复核（不阻塞）：")
        for fp, f in result.downgraded:
            lines.append(f"  v [{fp}] {f.rule} {f.path}:{f.line}  应修 → 复核")
    if result.ok:
        lines.append("OK：没有新增违规。")
        if result.gone:
            lines.append(f"提示：可运行 --accept-current 将基线从 {result.baseline_total} 下调 "
                         f"{len(result.gone)} 条（当前 {result.current_total}）。")
    else:
        lines.append("修复新增项，或显式运行 --accept-current 接受（会打印接受清单）。")
    lines.append(f"基线文件：{baseline_path}")
    return "\n".join(lines)


def render_accept(result: RatchetResult, before: int, after: int,
                  accepted_path: str, reason: str) -> str:
    lines = ["", "── 棘轮基线更新 ──"]
    lines.append(f"基线 {before} → {after}")
    if reason:
        lines.append(f"接受理由：{reason}")
    if result.new:
        lines.append(f"本次接受的新增违规 {len(result.new)} 条：")
        for fp, f in result.new:
            lines.append(f"  + [{fp}] {f.rule} {f.path}:{f.line}  {_norm(f.snippet)}")
    else:
        lines.append("本次没有新增违规被接受。")
    if result.escalated:
        lines.append(f"本次接受的严重度升级 {len(result.escalated)} 条：")
        for fp, f in result.escalated:
            lines.append(f"  ^ [{fp}] {f.rule} {f.path}:{f.line}  复核 → 应修")
    if result.gone:
        lines.append(f"从基线移除的已消失项 {len(result.gone)} 条：")
        for fp, e in result.gone:
            lines.append(f"  - [{fp}] {describe(e)}")
    if result.downgraded:
        lines.append(f"降级为复核 {len(result.downgraded)} 条：")
        for fp, f in result.downgraded:
            lines.append(f"  v [{fp}] {f.rule} {f.path}:{f.line}")
    lines.append(f"基线文件：{accepted_path}")
    return "\n".join(lines)
