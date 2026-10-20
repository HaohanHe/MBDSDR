# SPDX-License-Identifier: MIT
"""Qt Linguist 构建脚本：desktop/i18n/*.ts -> *.qm。

用法（装好 PySide6 后一条命令）::

    python desktop/i18n/build_qm.py

  * 抽取：pyside6-lupdate desktop -ts desktop/i18n/desktop_en.ts
          desktop/i18n/desktop_ja.ts（新 tr() 字面量入册，旧译文保留）
  * 编译：pyside6-lrelease 各 .ts -> 同名 .qm（运行时由 desktop/l10n.py 加载）

仓库纪律：.qm 是构建产物不入库（AGENTS.md"无输出二进制"）；.ts 入库。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.dirname(HERE)
TS = [os.path.join(HERE, "desktop_en.ts"), os.path.join(HERE, "desktop_ja.ts")]


def main() -> int:
    # 注意：lupdate 给目录不扫 .py（默认扩展名列表无 py，2026-10-20 实测 0 抽取），
    # 必须显式传 .py 文件清单才会解析 self.tr()/translate() 字面量。
    # 工具走 PATH 上的原生 CLI（pyside6-lupdate / pyside6-lrelease）。
    py_files = sorted(
        os.path.join(DESKTOP, f) for f in os.listdir(DESKTOP) if f.endswith(".py"))
    extract = ["pyside6-lupdate", *py_files, "-ts", *TS]
    for ts in TS:
        build = ["pyside6-lrelease", ts, "-qm", ts[:-3] + ".qm"]
        for cmd in (extract if ts == TS[0] else [], build):
            if not cmd:
                continue
            r = subprocess.run(cmd, cwd=DESKTOP)
            if r.returncode != 0:
                return r.returncode
    print("i18n .qm built:", ", ".join(t[:-3] + ".qm" for t in TS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
