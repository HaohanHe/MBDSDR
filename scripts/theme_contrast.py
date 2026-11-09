# SPDX-License-Identifier: MIT
"""主题对比度审计（WCAG 2.1）。

为什么要它
----------
参考项目 Glasswork 的材质只有四条声明（background / backdrop-filter /
box-shadow / color），但它给了一条硬约束：**玻璃上的文字对比度不得低于
4.5:1**，浓度由这条倒推。我们的主题一直是「看着调」，没有任何可测下限，
于是浅色主题的强调色/成功色长期停在 2.5–2.8（低于图标可辨的 3:1），
选中态白字压在橙底上只有 3.09。

本脚本把「看着还行」换成数字：逐主题、逐前景/背景配对算对比度，不达标的
直接退非零退出码，供 CI 与 `tests/test_theme_contrast.py` 使用。
对比度算法本身住在 `desktop/themes.py`（设计约束归设计系统，脚本只做报表）。

用法：
    python scripts/theme_contrast.py            # 打印全部，有失败则 exit 1
    python scripts/theme_contrast.py --quiet    # 只打印失败项

判定口径（WCAG 2.1）：
  * 正文/次要文字：4.5:1（AA 正文）
  * 图标/边框/状态色：3:1（AA 非文本对比度）
  * 卡片描边：1.5:1（能看出边界即可，WCAG 不强制）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "desktop"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from themes import THEMES, _on_primary, contrast_ratio  # noqa: E402

#: (前景键, 背景键, 最低比值, 说明)
PAIRS = (
    ("text", "bg", 4.5, "正文 on 页底"),
    ("text", "card", 4.5, "正文 on 卡片"),
    ("text_secondary", "bg", 4.5, "次要文字 on 页底"),
    ("text_secondary", "card", 4.5, "次要文字 on 卡片"),
    ("primary_text", "bg", 4.5, "强调文字 on 页底"),
    ("primary_text", "card", 4.5, "强调文字 on 卡片"),
    ("accent", "bg", 3.0, "强调色（图标/边框）"),
    ("accent", "card", 3.0, "强调色 on 卡片"),
    ("success", "bg", 3.0, "成功色"),
    ("danger", "bg", 3.0, "危险色"),
    ("border", "card", 1.5, "卡片描边可辨"),
)


def audit() -> list[tuple[str, str, str, float, float, bool]]:
    """返回 (主题, 前景键, 背景键, 比值, 阈值, 是否达标)。"""
    rows: list[tuple[str, str, str, float, float, bool]] = []
    for name, theme in THEMES.items():
        for fg, bgk, need, _label in PAIRS:
            if fg not in theme.colors or bgk not in theme.colors:
                continue
            ratio = contrast_ratio(theme.colors[fg], theme.colors[bgk])
            rows.append((name, fg, bgk, ratio, need, ratio >= need))
        # 选中态：强调色底上的文字（QPalette.HighlightedText）
        if "primary" in theme.colors:
            ink = _on_primary(theme.colors)
            ratio = contrast_ratio(ink, theme.colors["primary"])
            rows.append((name, f"on_primary({ink})", "primary", ratio, 4.5, ratio >= 4.5))
    return rows


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    quiet = "--quiet" in args
    rows = audit()
    failures = [r for r in rows if not r[5]]
    if not quiet:
        current = None
        for name, fg, bgk, ratio, need, ok in rows:
            if name != current:
                print(f"\n=== {name} ===")
                current = name
            flag = "OK  " if ok else "FAIL"
            print(f"  {flag} {ratio:5.2f} (需 {need:.1f})  {fg} on {bgk}")
    if failures:
        if quiet:
            for name, fg, bgk, ratio, need, _ in failures:
                print(f"FAIL {name}: {fg} on {bgk} = {ratio:.2f} < {need:.1f}")
        print(f"\n{len(failures)} 项不达标（共 {len(rows)} 项）")
        return 1
    print(f"\n全部达标（{len(rows)} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
