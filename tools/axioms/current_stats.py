# SPDX-License-Identifier: MIT
"""`docs/axioms.md` 当前计数的唯一生成器。

文档里只允许有一个「当前计数」块；块内文字由本模块生成，命令：

    python -m tools.axioms.current_stats

`tests/test_axioms_doc_current_stats.py` 会用它当次输出与文档块逐字比对，
不一致即红。这里不重新实现计数逻辑——采集走 ``survey.collect_all``，豁免
走 ``model.load_waivers/apply_waivers``，汇总走 ``Report8.summary``，
与 ``scripts/axioms.py`` 是同一条路径。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.axioms.checks import skip_report  # noqa: E402
from tools.axioms.model import (  # noqa: E402
    DEFAULT_WAIVER_FILE,
    Repo,
    apply_waivers,
    load_waivers,
)
from tools.axioms.report8 import Report8  # noqa: E402
from tools.axioms.survey import collect_all  # noqa: E402

BEGIN_MARK = "<!-- axioms-current-stats:begin"
END_MARK = "<!-- axioms-current-stats:end -->"


def current_summary(root=None):
    """跑一次全量采集 + 豁免，返回 ``Report8.summary()`` 的字典。"""
    repo = Repo(root or ROOT)
    axioms, dims = collect_all(repo)
    waivers = load_waivers(repo, DEFAULT_WAIVER_FILE)
    for dim in dims:
        apply_waivers(dim.findings, waivers)
    return Report8(axioms, dims, [], skip_report()).summary()


def render_block(root=None) -> str:
    """生成文档块内的两行计数（不含标记本身）。"""
    s = current_summary(root)
    dims = " / ".join(
        f"{dim} {s['by_dimension'][dim]['total']}"
        for dim in sorted(s["by_dimension"])
    )
    return (
        f"违规 {s['total']} 条：应修 {s['must_fix']}，复核 {s['review']}，"
        f"豁免 {s['waived']}。\n{dims}。"
    )


if __name__ == "__main__":
    print(render_block())
