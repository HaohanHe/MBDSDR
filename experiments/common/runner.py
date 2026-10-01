# SPDX-License-Identifier: MIT
"""
可复现蒙特卡洛运行器：固定种子、逐格多次试验、二项置信区间
=================================================================

解决 B2 审计 §5 的统计硬伤：旧脚本每格仅 8–10 次、无置信区间。本模块：

- 由一个主种子派生出确定性子流（:meth:`ExperimentRunner.make_rng`），
  同一 (seed, tag) 在任何机器上得到同一 numpy Generator。
- 逐格跑 N 次独立试验，统计成功次数 -> 成功率。
- 给两种二项置信区间：
    * Wilson score interval（推荐，n 小或 p 接近 0/1 也不越界）；
    * 正态近似 Wald interval（对照用）。
- 每次运行记录样本数 n_trials，供图题/manifest 引用。

红线：所有随机性都从主种子派生，禁止在实验脚本里另起 np.random 全局态；
CI 用 scipy.stats.norm.ppf 实算，不手填魔法数。
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple

import numpy as np
from scipy.stats import norm


# ---------------------------------------------------------------------------
# 二项置信区间
# ---------------------------------------------------------------------------
def wilson_ci(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score 95% 置信区间（默认 z=1.96）。返回 (lo, hi) ∈ [0,1]。

    k 成功数 / n 试验数。n=0 时返回 (0,0) 并由调用方记录空态。
    """
    k, n = int(k), int(n)
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    center = (p + z * z / (2.0 * n)) / (1.0 + z * z / n)
    half = (z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))) \
        / (1.0 + z * z / n)
    return (max(0.0, center - half), min(1.0, center + half))


def normal_ci(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """正态近似 Wald 区间（对照用；n 小或 p 贴近 0/1 时不如 Wilson）。"""
    k, n = int(k), int(n)
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    half = z * math.sqrt(max(0.0, p * (1.0 - p) / n))
    return (max(0.0, p - half), min(1.0, p + half))


@dataclass
class BinomialCell:
    """单个 (条件) 格点的二项统计结果。"""
    successes: int
    trials: int

    @property
    def rate(self) -> float:
        return self.successes / self.trials if self.trials > 0 else float("nan")

    @property
    def wilson(self) -> Tuple[float, float]:
        return wilson_ci(self.successes, self.trials)

    @property
    def normal(self) -> Tuple[float, float]:
        return normal_ci(self.successes, self.trials)

    @property
    def half_width_wilson(self) -> float:
        lo, hi = self.wilson
        return (hi - lo) / 2.0

    def as_row(self) -> Dict[str, float]:
        lo, hi = self.wilson
        nlo, nhi = self.normal
        return {
            "successes": self.successes,
            "n_trials": self.trials,
            "success_rate": round(self.rate, 6),
            "wilson_lo": round(lo, 6),
            "wilson_hi": round(hi, 6),
            "wilson_half": round(self.half_width_wilson, 6),
            "normal_lo": round(nlo, 6),
            "normal_hi": round(nhi, 6),
        }


# ---------------------------------------------------------------------------
# 固定种子运行器
# ---------------------------------------------------------------------------
class ExperimentRunner:
    """确定性运行器：主种子 + 标签 -> 子流 Generator；逐格跑试验。"""

    def __init__(self, seed: int):
        self.seed = int(seed)

    def make_rng(self, tag: str) -> np.random.Generator:
        """由 (主种子, tag) 派生一个独立、确定的 numpy Generator。

        用 blake2b 把 (seed, tag) 哈希成 64bit 子种子，避免"主种子+连续计数器"
        在增删试验后串味。同一 tag 永远得到同一 rng。
        """
        h = hashlib.blake2b(
            f"{self.seed}|{tag}".encode("utf-8"), digest_size=8)
        sub = int.from_bytes(h.digest(), "little")
        return np.random.default_rng(sub)

    def run_grid(self, grid: List[Tuple[str, float]], trials: int,
                 trial_fn) -> List[Dict[str, float]]:
        """在网格上逐格跑 trials 次二项试验。

        参数:
            grid     : [(条件名, 条件值), ...]，条件名写入 cell。
            trials   : 每格独立试验次数。
            trial_fn : callable(rng, cond_value, trial_index) -> bool（成功 True）。
                       rng 由 (seed, cond_name) 派生，保证逐格独立且可复现。
        返回: 逐格字典列表（含 BinomialCell.as_row() + 条件字段）。
        """
        rows: List[Dict[str, float]] = []
        for cond_name, cond_value in grid:
            rng = self.make_rng(cond_name)
            successes = 0
            for k in range(trials):
                if trial_fn(rng, cond_value, k):
                    successes += 1
            cell = BinomialCell(successes=successes, trials=trials)
            row = {"condition": cond_name, "condition_value": cond_value}
            row.update(cell.as_row())
            rows.append(row)
        return rows

    @staticmethod
    def z_from_confidence(confidence: float = 0.95) -> float:
        """由置信度取正态分位 z（0.95 -> 1.96）。"""
        return float(norm.ppf(1.0 - (1.0 - confidence) / 2.0))
