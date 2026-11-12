#!/usr/bin/env python3
"""抓「纸面阻塞」：阻塞理由还写着，但依据已经不存在了。

三条机械判据（对应 2026-11-12 查实的三类假阻塞）：
  CLAIM_GONE    理由提到 claim / 在途 / 他人改 → 标的文件没有活跃且未过期的 claim
  DECISION_MADE 理由提到待 D-x / 待产品决定 / 待 §6 → §6 该决策行已填取向
  PREREQ_DONE   理由提到前置 Wxx 未落库 → roadmap 里该前置已完成

原则：不猜。文件缺失、JSON 结构变化、解析不出标的，一律 UNKNOWN 并写明原因，
绝不静默当成「没命中」。退出码恒 0——这是提醒器，不是闸门。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROADMAP = REPO / "docs" / "research" / "2026-11-11-roadmap.md"
CLAIMS = REPO.parent / ".locks" / "claims.json"

CLAIM_HINT = re.compile(r"claim|在途|他人|重写中|持有", re.I)
DECISION_HINT = re.compile(r"待\s*(?:D-\d+|产品决定|§\s*6)")
PREREQ_HINT = re.compile(r"前置.*(?:未落库|未完成)")
FILE_RE = re.compile(r"[A-Za-z0-9_./\\-]+\.py")
W_RE = re.compile(r"W\d{2}")
D_RE = re.compile(r"D-\d+")
DECIDED_RE = re.compile(r"已(?:经)?(?:裁|定|拍板|决定)|已裁决|已裁定|采纳|按此落")
OPTION_RE = re.compile(r"^[ABC](?:\s|（|\(|：|:)")
NOTICE = ("本脚本抓的是「失效的理由分句」，不代表整条工单可开工"
          "（例：W19 仍被 W11 挡、W60 仍被 FZ-04 挡、W44 仍被 FZ-04 挡）。")


def split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_work_orders(text: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for no, line in enumerate(text.splitlines(), 1):
        if not re.match(r"^\|\s*W\d{2}\s*\|", line):
            continue
        cells = split_row(line)
        if len(cells) < 5:
            continue  # 畸形行由 scan 层按 UNKNOWN 处理不了，这里直接跳过
        rows[cells[0]] = {
            "id": cells[0],
            "files": cells[1],
            "content": cells[2],
            "dep": cells[3],
            "status": cells[4],
            "line": no,
        }
    return rows


def parse_decisions(text: str) -> dict[str, dict]:
    """§6 表：编号 | 议题 | 现状与证据 | 选项 | 建议（唯一承载取向的列）。"""
    out: dict[str, dict] = {}
    for no, line in enumerate(text.splitlines(), 1):
        if not re.match(r"^\|\s*D-\d+\s*\|", line):
            continue
        cells = split_row(line)
        if len(cells) < 5:
            continue
        out[cells[0]] = {
            "id": cells[0],
            "topic": cells[1],
            "evidence": cells[2],
            "options": cells[3],
            "advice": cells[4],
            "line": no,
        }
    return out


def load_claims() -> dict:
    if not CLAIMS.exists():
        raise FileNotFoundError(f"找不到 {CLAIMS}")
    data = json.loads(CLAIMS.read_text(encoding="utf-8"))
    claims = data.get("claims")
    if not isinstance(claims, dict):
        raise ValueError("claims.json 缺少 claims 字典（结构可能已变）")
    return claims


def claim_state(claims: dict, path: str, now: float) -> tuple[str, str]:
    entry = claims.get(path)
    if not entry:
        return "none", "无登记"
    cur = entry.get("current")
    if not cur:
        return "none", "无活跃 claim"
    hb = cur.get("heartbeat_at")
    ttl = cur.get("ttl_min")
    if not isinstance(hb, (int, float)) or not isinstance(ttl, (int, float)):
        raise ValueError(f"{path} 的 claim 缺 heartbeat_at/ttl_min")
    age_min = (now - hb) / 60
    if now < hb + ttl * 60:
        return "active", f"活跃 claim（{age_min:.1f} min 前心跳，TTL {ttl:.0f} min）"
    return "expired", f"claim 已过期（{age_min:.1f} min 前心跳 > TTL {ttl:.0f} min）"


def is_done(status: str) -> bool:
    s = status.strip()
    if s.startswith("完成") or s.startswith("已实现"):
        return True
    if "已被他线完成" in s:
        return True
    return "已完成" in s and "未完成" not in s


def is_decided(cell: str) -> bool:
    return bool(DECIDED_RE.search(cell)) or bool(OPTION_RE.match(cell.strip()))


def quote_advice(cell: str) -> str:
    """引用 §6 取向时去掉「已裁决/已裁定」这类盖章词——只有建议列，不替它盖章。"""
    s = cell.replace("**", "").strip()
    s = re.sub(r"已(?:经)?(?:裁决|裁定|拍板|决定)", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip("：:；; ")
    return s[:70]


def norm_path(p: str) -> str:
    return p.replace("\\", "/").lstrip("./")


def targets_from(wo: dict) -> list[str]:
    reason_files = FILE_RE.findall(wo.get("status", ""))
    col_files = FILE_RE.findall(wo.get("files", ""))
    seen: list[str] = []
    for p in reason_files + col_files:
        n = norm_path(p)
        if n not in seen:
            seen.append(n)
    return seen


def evaluate(wo: dict, rows: dict[str, dict], decisions: dict[str, dict],
             claims: dict | None, claims_error: str | None) -> list[tuple[str, str, str]]:
    """返回 [(判据, 标的, 现状)]；UNKNOWN 也走这里，绝不静默丢弃。"""
    reason = wo.get("status", "")
    hits: list[tuple[str, str, str]] = []

    if CLAIM_HINT.search(reason):
        paths = targets_from(wo)
        if not paths:
            hits.append(("UNKNOWN", "claim/在途", "理由提到 claim/在途，但解析不出标的文件"))
        elif claims is None:
            hits.append(("UNKNOWN", "claim/在途", f"claim 依据不可读：{claims_error}"))
        else:
            try:
                now = time.time()
                states = [(p,) + claim_state(claims, p, now) for p in paths]
            except Exception as exc:  # 结构变化 → UNKNOWN
                hits.append(("UNKNOWN", "claim/在途", f"claim 依据解析失败：{exc}"))
            else:
                if not any(s == "active" for _, s, _ in states):
                    note = "；".join(f"{p}: {msg}" for p, _, msg in states)
                    hits.append(("CLAIM_GONE", "、".join(paths), note))

    if DECISION_HINT.search(reason):
        ids = D_RE.findall(reason) or D_RE.findall(wo.get("dep", ""))
        if not ids:
            hits.append(("UNKNOWN", "D-x", "理由提到待决策，但解析不出 D 编号"))
        else:
            for d in ids:
                row = decisions.get(d)
                if row is None:
                    hits.append(("UNKNOWN", d, "§6 找不到该决策行"))
                    continue
                advice = (row.get("advice") or "").strip()
                if not advice:
                    hits.append(("UNKNOWN", d, "§6 该决策行没有取向列"))
                elif is_decided(advice):
                    hits.append(("DECISION_MADE", d, f"§6:{row['line']} 该决定已有取向：{quote_advice(advice)}"))
                else:
                    hits.append(("UNKNOWN", d, "§6 取向列存在但无法判定是否已填"))

    if PREREQ_HINT.search(reason):
        ids = W_RE.findall(reason) or W_RE.findall(wo.get("dep", ""))
        if not ids:
            hits.append(("UNKNOWN", "Wxx", "理由提到前置未落库，但解析不出 W 编号"))
        else:
            states = []
            broken = False
            for w in ids:
                row = rows.get(w)
                if row is None:
                    hits.append(("UNKNOWN", w, "roadmap 找不到该工单"))
                    broken = True
                    continue
                states.append((w, is_done(row["status"]), row["status"]))
            if not broken and states and all(done for _, done, _ in states):
                note = "；".join(f"{w}={s[:40]}" for w, _, s in states)
                hits.append(("PREREQ_DONE", "、".join(ids), note))

    return hits


def load_context() -> tuple[dict, dict, dict | None, str | None, str | None]:
    if not ROADMAP.exists():
        return {}, {}, None, None, f"找不到 {ROADMAP}"
    text = ROADMAP.read_text(encoding="utf-8")
    rows = parse_work_orders(text)
    decisions = parse_decisions(text)
    try:
        claims = load_claims()
        claims_error = None
    except Exception as exc:
        claims, claims_error = None, str(exc)
    return rows, decisions, claims, claims_error, None


def scan_live(rows: dict, decisions: dict, claims: dict | None, claims_error: str | None):
    scanned = [w for w in rows.values() if w["status"].startswith("阻塞")]
    hits, unknown = [], []
    for wo in scanned:
        for code, target, note in evaluate(wo, rows, decisions, claims, claims_error):
            item = {"work_order": wo["id"], "target": target, "code": code, "note": note}
            (unknown if code == "UNKNOWN" else hits).append(item)
    return scanned, hits, unknown


FIXTURES = [
    # 2026-11-12 修复前的原文（commit e9e41b94~1），用于反证三条判据
    ("W15", "W14", "阻塞：前置未落库", "", "PREREQ_DONE"),
    ("W36", "W35、D-3", "阻塞：待 §6 产品决定", "", "DECISION_MADE"),
    ("W37", "W36、D-9", "阻塞：待 §6 产品决定", "", "DECISION_MADE"),
    ("W45", "W10、W42", "阻塞：断言目标 `desktop/settings_panel.py`（`settings-convergence` 持有，128+/43- 在途）与 `desktop/spectrum_widget.py`（频谱显示线持有，22:19 起 120 min）都在他人手上重写中，此时写断言等于给在途改动判卷。解除条件：两者 claim 释放且落库后，按落库版写断言", "tests/test_spectrum_display_params.py", "CLAIM_GONE"),
    ("W58", "W57、W14", "阻塞：前置未落库", "desktop/command_palette.py", "PREREQ_DONE"),
    ("W49", "W48", "阻塞：视觉冻结未解冻（FZ-04 尺寸类，用户未发解冻指令）；W48 已落库。解除条件：书面解冻尺寸类改动", "desktop/right_column.py", None),
    ("W55", "W25", "阻塞：视觉冻结未解冻（FZ-04）", "desktop/themes.py", None),
    ("W56", "W55", "阻塞：视觉冻结未解冻（FZ-04）", "desktop/themes.py", None),
]


def self_test(rows, decisions, claims, claims_error) -> int:
    print("== 反证（修复前原文重放）==")
    failed = 0
    for wid, dep, status, files, expected in FIXTURES:
        wo = {"id": wid, "files": files, "dep": dep, "status": status}
        codes = [c for c, _, _ in evaluate(wo, rows, decisions, claims, claims_error)]
        ok = (expected in codes) if expected else (not codes)
        if not ok:
            failed += 1
        print(f"{'PASS' if ok else 'FAIL'}  {wid}  expected={expected or 'NO_HIT'}  got={codes or ['NO_HIT']}")
    print(f"反证结果：{'PASS' if failed == 0 else f'FAIL（{failed} 条不符）'}")
    return failed


def main() -> int:
    ap = argparse.ArgumentParser(description="抓 roadmap 里的纸面阻塞（提醒器，退出码恒 0）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--self-test", action="store_true", help="重放 2026-11-12 修复前原文做反证")
    args = ap.parse_args()

    try:
        rows, decisions, claims, claims_error, fatal = load_context()
        if fatal:
            payload = {"error": fatal, "rows": [], "counts": {}}
            print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else f"UNKNOWN: {fatal}")
            return 0

        if args.self_test:
            failed = self_test(rows, decisions, claims, claims_error)
            payload = {"self_test": "PASS" if failed == 0 else "FAIL", "failed": failed}
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        scanned, hits, unknown = scan_live(rows, decisions, claims, claims_error)
        counts = {}
        for item in hits:
            counts[item["code"]] = counts.get(item["code"], 0) + 1

        if args.json:
            print(json.dumps({
                "notice": NOTICE,
                "scanned_blocked": len(scanned),
                "hits": hits,
                "unknown": unknown,
                "counts": counts,
            }, ensure_ascii=False, indent=2))
            return 0

        print(f"扫描阻塞工单：{len(scanned)} 条（roadmap: {ROADMAP}）")
        print(NOTICE)
        if hits:
            print(f"{'工单号':<6} {'标的':<34} {'判据':<14} 现状")
            for it in hits:
                print(f"{it['work_order']:<6} {it['target'][:32]:<34} {it['code']:<14} {it['note']}")
        else:
            print("没有命中的纸面阻塞。")
        if unknown:
            print(f"\nUNKNOWN（依据不足，不计入命中）：{len(unknown)} 条")
            for it in unknown:
                print(f"  {it['work_order']}  {it['target']}  {it['note']}")
        print(f"\n计数：命中 {len(hits)} 条；UNKNOWN {len(unknown)} 条；扫描 {len(scanned)} 条。")
        return 0
    except Exception as exc:  # 提醒器永不阻断
        msg = f"UNKNOWN: 检查器自身失败：{exc}"
        print(json.dumps({"error": msg}, ensure_ascii=False) if "--json" in sys.argv else msg)
        return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
