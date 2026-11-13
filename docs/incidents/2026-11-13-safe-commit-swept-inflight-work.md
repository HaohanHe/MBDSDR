# 事故记录：safe_commit 提交时卷走他线在途三件（2026-11-13）

状态：已定位；**内容未丢失，但归属记在错误的提交上**；本条只记录事实与后果，
是否回退由用户裁定；根因（「未提交 = 无主」的判断法）在本文件第五节。

## 一、事实

- 提交：`052828d1 fix(claims): 登记面自身豁免两条分支生效 + safe_commit 插行容忍说明文字`。
- `git show --stat --oneline 052828d1`：

  ```text
  scripts/check_claim_coordination.py      |  17 +-
  scripts/safe_commit.py                   | 783 +++++++++++++++++++++++++++++--
  tests/test_coordination_gate_blocking.py |  13 +
  tests/test_safe_commit_coordination.py   | 247 ++++++++++++++++
  tests/test_safe_commit_declaration.py    | 407 ++++++++++++++++
  tests/test_safe_commit_line_range.py     | 207 ++++++++
  6 files changed, 1641 insertions(+), 33 deletions(-)
  ```

- 声明的本意只有两件：① `check_claim_coordination.py` 的登记面自身豁免；
  ② `safe_commit._insert_table_rows` 容忍 §6.1 标题下的说明文字（含回归用例）。
  其余 4 个文件、约 1400 行**不是我这条线的活**。
- 被卷走的活：另一条在途线正在写的三件——声明快照校验（形态⑰）、提交信息逐字
  校验（形态⑱）、`--exclude-hunk` 按行范围（形态⑳），以及它们的两个测试文件。
  该任务当时 `in_progress`，最后一步在改「豁免路径上的报告措辞」。

## 二、当时的 claim 状态（台账原文，未加工）

台账：`d:\mbdsdr\.locks\claims.json`，共 1054 个键。

| 路径 | 提交时（20:36:36）有无活跃 claim | owner | 台账证据 |
| --- | --- | --- | --- |
| `scripts/safe_commit.py` | **有（我自己）** | `收尸线/6ae85f56` | 取 20:28:15、释 20:37:15，理由「safe_commit 插入行容忍 §6.1 标题下说明文字」 |
| `tests/test_safe_commit_declaration.py` | **无** | — | 台账**从无该键**（从未 claim 过） |
| `tests/test_safe_commit_line_range.py` | **无** | — | 台账**从无该键**（从未 claim 过） |

`scripts/safe_commit.py` 上最后一条他线 claim 是 `用户输入静默线/6ae85f56`
（19:29:51 取、19:31:56 释，理由「反证期间临时改实现」），之后到 20:28:15
之间该文件无任何 claim。也就是说：**那条线写这三件时没有登记 claim；但这不等于
无主**——没登记只说明台账看不见它，不说明写它的人不在。

## 三、为什么被我判断成「无主孤儿」

三个错误叠在一起，任何一个成立都不会发生本事故：

1. **把「未提交」当成「无主」。** 我的判据是「工作区有、HEAD 没有、旁边还配着
   测试」——这三条恰好是**一条活线正在工作的标准形态**，不是孤儿特征。
2. **把「文件级的 claim」当成「内容的所有权」。** 我为了改 `_insert_table_rows`
   一行，在 20:28 给 `scripts/safe_commit.py` 取了整文件 claim。取到之后，
   这个 claim 反过来被我当成「这个文件归我」，把同一文件里别人的 700+ 行
   一起带走了。claim 只回答「此刻有没有别人在占」，不回答「这份内容是谁写的」。
3. **两个测试文件根本没查 claim。** 我查 claim 只是为了过闸（闸只问「有没有
   别人的活跃 claim」），没有为了判断归属去查。流程上「查了」，判断上「没查」。

## 四、后果与当前状态

- 那三件的实现与测试现在挂在 `052828d1` 上；内容完整，无丢失、无重复。
- 那条线的基线被抬了一格：它继续工作时的 diff 变成「相对 `052828d1`」而不是
  「相对 `c17f7011`」。提交后 20:39:17 / 20:39:20，`scripts/safe_commit.py`
  （+25/-9）与 `tests/test_safe_commit_declaration.py`（+1）又出现新改动，
  内容正是 `_verify_declaration(..., waived=False)` 的豁免措辞——**它还在写，
  基线已经变了**。
- 它下一次 `--declare` / 提交时，快照基线是 `052828d1`；若它按旧基线核对
  行数或 blob，会对不上。这是本次事故对它最直接的成本。

## 五、根因：判据里缺一条「谁在写」

现有闸只问两件事：暂存文件有没有**别人的活跃 claim**、有没有 §6.1 行。
它从不问「这个文件的未提交内容是谁的」。于是：

- 对方没 claim → 闸放行；
- 我自己 claim 了同一文件 → 闸甚至认为是我持有；
- 我声明了 6 个文件 → 闸按声明放行。

**「谁在写」在整条链上没有任何一处被检查。** 这与
`2026-11-13-safe-commit-swept-foreign-rows.md` 同源（那次的判据缺的是
「声明之后新冒出来的内容」），但这次缺的是「声明之前就已存在的他人内容」。
两次共同点：**把「没被拦住」当成「可以带走」。**

## 六、处置

- **未回退**。那条线的工作区现在有它自己 20:39 的新改动，单方面 revert
  `052828d1` 会把它的在途编辑一起卷回去。回退需要与那条线约定顺序，由用户裁定。
- 本条只记录事故，不承载修复。可考虑的修复方向（未实施）：
  ① 提交前对每个 `--files` 比对「工作区内容中不属于声明快照的部分」并要求
  `--allow-foreign-changes` 式的显式理由（形态⑰ 的声明快照机制已具备这个能力，
  缺的是**对未声明文件的默认拒绝**）；② 判断「无主」前必须先查 claim，
  且 claim 只作为「有主」的证据，不能作为「无主」的反证。

## 七、定位方式

- 目录：`docs/incidents/`（每事故一份，日期前缀）。
- 关键词：`052828d1`、`safe_commit`、`声明快照`、`形态⑰`、`形态⑱`、`形态⑳`、
  `收尸线`、`未提交不等于无主`。
- 原始证据：`git show --stat --oneline 052828d1`；
  `d:\mbdsdr\.locks\claims.json` 中 `scripts/safe_commit.py` 的 `history`；
  两测试文件在台账中无键（`'tests/test_safe_commit_declaration.py' in claims`
  = `False`，`'tests/test_safe_commit_line_range.py' in claims` = `False`）。
