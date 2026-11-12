#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""打印「本地领先 origin/main 多少笔」——供人手动看一眼。

为什么存在：`ci.yml` 只在 push 到 main / 对 main 的 PR 时触发。本地领先的提交
再多，CI 徽章都还停留在上一次推送的树上。看到「CI 绿」时，先用这个数判断它
有没有可能覆盖当前 HEAD。

为什么**不进钩子**：开发期间这个数长期非零，挂进 pre-commit 只会让提交被
`--no-verify` 绕过，反而训练人忽视钩子。它只是一条手动查询。

用法：python scripts/origin_lag.py
"""
import subprocess
import sys


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              check=False).stdout.strip()
    except OSError as exc:      # git 不在 PATH
        return f"<{exc}>"


def main() -> int:
    ahead = _git("rev-list", "--count", "origin/main..HEAD")
    if not ahead.isdigit():
        print(f"读不到 origin/main（远端引用可能不存在）：{ahead or '<空>'}")
        return 2
    last = _git("log", "-1", "--format=%h %ci %s", "origin/main")
    print(f"本地领先 origin/main：{ahead} 个提交")
    print(f"origin/main 最后一次推送：{last}")
    print("提示：CI 只在 push 到 main / 对 main 的 PR 时触发；该数 > 0 时，"
          "「CI 绿」不代表当前 HEAD。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
