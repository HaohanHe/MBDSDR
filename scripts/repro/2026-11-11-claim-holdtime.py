# SPDX-License-Identifier: MIT
"""file_claim 持有期 vs TTL 实测（只读取证，不改任何 claim 状态）。

数据源
------
`D:\\mbdsdr\\.locks\\claims.json`（`scripts/file_claim.py` 的注册表）。
每个路径条目带 `current`（在途）与 `history`（已关闭样本），
两种样本都可直接算持有期，不需要跑任何会写状态的操作。

口径
----
* **已关闭样本**：`released_at - acquired_at`，只取两侧都存在且非负的记录。
* **在途样本**：`now - current.acquired_at`（即「到现在还占着」的时长）。
* **超时**：在途持有分钟 > 该条自己的 `ttl_min`（`ttl_min` 只在 `current`
  里有，history 老样本不带，所以「历史超时占比」只能用默认 60 分钟近似）。
* 长尾（>6h）当作僵尸样本单列：它们是进程退出没释放、后来被
  `expired_steal` / `orphan_takeover` 收掉的，混杂在总体分位数里会把
  均值拉爆（实测 mean 105.7min vs P50 7.0min），所以同时给「剔除 >6h」口径。

复跑
----
    python scripts/repro/2026-11-11-claim-holdtime.py

输出即案卷 `docs/pending-patches/2026-11-11-claim-holdtime-baseline.md` §1 的
基线表；注册表是活文件，随时会变，**引用前必须重跑取数**。

实测快照（2026-11-11 23:31:45，registry version 1）
----------------------------------------------------
已关闭 n=1260：P50=7.0 / P90=40.7 / P95=113.1 / max=8705.6 分钟；
>60min 7.1%，>6h 1.75%，>24h 1.03%（全为僵尸）。
在途 n=59：P50=88.2 / P90=8547.8 分钟；超自身 TTL 50/59 = 84.7%。
"""
import json
import statistics as st
import time
from pathlib import Path

REG = Path(r"D:\mbdsdr\.locks\claims.json")


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def main():
    d = json.loads(REG.read_text(encoding="utf-8"))
    claims = d["claims"]
    now = time.time()
    print("registry version:", d.get("version"), "updated_at:", d.get("updated_at"))

    done = []      # (分钟, owner, closed_reason, path)
    openr = []     # (分钟, owner, ttl_min, path)
    for path, rec in claims.items():
        for h in (rec.get("history") or []):
            a, r = h.get("acquired_at"), h.get("released_at")
            if a is None or r is None or r < a:
                continue
            done.append(((r - a) / 60, h.get("owner", ""),
                         h.get("closed_reason", ""), path))
        cur = rec.get("current")
        if cur and cur.get("acquired_at"):
            openr.append(((now - cur["acquired_at"]) / 60, cur.get("owner", ""),
                          cur.get("ttl_min"), path))

    print(f"\n=== 已关闭 n={len(done)}；在途 n={len(openr)} ===")
    if done:
        mins = [x[0] for x in done]
        print(f"已关闭（分钟）：mean={st.mean(mins):.1f} P50={pct(mins,.5):.1f} "
              f"P75={pct(mins,.75):.1f} P90={pct(mins,.9):.1f} "
              f"P95={pct(mins,.95):.1f} P99={pct(mins,.99):.1f} max={max(mins):.1f}")
        for thr in (10, 30, 60, 90, 120, 360, 1440):
            c = sum(1 for m in mins if m > thr)
            print(f"  > {thr:>4}min: {c:>4} ({c / len(mins) * 100:.2f}%)")
        normal = [m for m in mins if m <= 360]
        print(f"  剔除 >6h（僵尸）：n={len(normal)} P50={pct(normal,.5):.1f} "
              f"P90={pct(normal,.9):.1f} P95={pct(normal,.95):.1f} "
              f"max={max(normal):.1f}")
        reasons = {}
        for _, _, r, _ in done:
            reasons[r] = reasons.get(r, 0) + 1
        print("  closed_reason:",
              dict(sorted(reasons.items(), key=lambda kv: -kv[1])))

    if openr:
        held = [x[0] for x in openr]
        print(f"\n在途 n={len(openr)}：P50={pct(held,.5):.1f} "
              f"P90={pct(held,.9):.1f} max={max(held):.1f}")
        over = [x for x in openr if x[2] and x[0] > x[2]]
        print(f"  超过自身 TTL: {len(over)}/{len(openr)} "
              f"({len(over) / len(openr) * 100:.1f}%)")
        ttls = {}
        for _, _, t, _ in openr:
            ttls[t] = ttls.get(t, 0) + 1
        print("  TTL 分布:",
              dict(sorted(ttls.items(), key=lambda kv: (kv[0] is None, kv[0]))))
        print("  最长 5 个：")
        for held_m, owner, ttl, path in sorted(openr, reverse=True)[:5]:
            print(f"    {held_m:7.1f}min ttl={ttl} {owner} {path}")

    if done:
        by = {}
        for dur, owner, _, _ in done:
            by.setdefault(owner, []).append(dur)
        print("\n按 owner（已关闭样本，前 10）：")
        for owner, xs in sorted(by.items(), key=lambda kv: -len(kv[1]))[:10]:
            print(f"  {owner:28s} n={len(xs):4d} P50={pct(xs,.5):6.1f} "
                  f"P90={pct(xs,.9):6.1f} max={max(xs):7.1f}")


if __name__ == "__main__":
    main()
