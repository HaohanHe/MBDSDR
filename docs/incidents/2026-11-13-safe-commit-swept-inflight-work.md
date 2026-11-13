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

## 八、裁定（2026-11-13，用户）：不回退

理由：那条线 20:39 之后的编辑叠在 `052828d1` 之上，单方面 revert 会把它的
在途编辑一起卷回去。**后续由那条线只提增量，并自行核对 `052828d1` 里的版本
是否就是它想要的版本**；若不一致，由它在自己的增量里改回来，不由本线动它。

裁定后的事实更新：20:41 那条线已自行提交
`e326d9d2 fix(safe_commit): 豁免路径的报告不再自称「拒绝提交」`（+25/−9），
增量正是它 20:39 在写的那一段。三件现在落在 `052828d1` + `e326d9d2` 两笔上，
工作区已干净；本线没有再碰这三个文件。

## 九、自查补充：提交说明到底有没有点名

`git show -s --format=%B 052828d1` 原文：

- **提到了**：`safe_commit.py` 文件名、「此前未提交的三件」及形态⑰/⑱/⑳，
  以及 `test_safe_commit_declaration.py` / `test_safe_commit_line_range.py`
  两个测试文件名。
- **漏了**：① 没写实现文件的完整路径 `scripts/safe_commit.py`；② 没写「这是
  另一条在途线正在写的活」，反而写成「此前一直未提交」，把别人的在途工作
  定性成无主遗留。

结论：**点名了文件，却给错了性质**——仍属「悄悄多带」的变体，不因写了文件名
而减等。记录在此。

## 十、补充：说明少算一件，且同一笔混入本线自己的修复

### 10.1 ⑲ 也在同一笔，说明只列了 ⑰⑱⑳

`git show --numstat --format= 052828d1` 原文（6 文件）：

```text
15      2       scripts/check_claim_coordination.py
752     31      scripts/safe_commit.py
13      0       tests/test_coordination_gate_blocking.py
247     0       tests/test_safe_commit_coordination.py
407     0       tests/test_safe_commit_declaration.py
207     0       tests/test_safe_commit_line_range.py
```

说明里只列了 ⑰（声明快照）、⑱（提交信息逐字校验）、⑳（`--exclude-hunk`
按行范围），**漏了 ⑲**：`_coordination_step`（`scripts/safe_commit.py:998`，
提交时把该笔的 §6.1 行一起带进去）与 `tests/test_safe_commit_coordination.py`
（+247 行）是同一形态的第四件。

结论：实际是**四件**，不是三件。**清点以 `--numstat` 为准，说明里的枚举不可
当作件数依据**——这次少算的一件没有被任何人发现，直到逐文件对账。

### 10.2 `check_claim_coordination.py` 的 +15/−2 是别线的活

同一笔里的 `scripts/check_claim_coordination.py`（+15/−2）不是那三/四件之一，
而是**本线（收尸线）**在 20:36 前完成并实测过的「登记面自身豁免两条分支」修复
（递归补行的根因）。它被卷进这一笔，后果不是内容错，而是**两件事共用一条
message**：按 message 追「登记面自身豁免」会指到 `052828d1`，按 message 追
「safe_commit 三件」也会指到 `052828d1`，两个方向的检索在同一处交叉。

这与 §九 的自查是同一病灶的两个面：说明既漏了件数（⑲），又把本线修复与
别线在途混在一句里。**一笔只该说一件事；说不清就不该合笔。**

## 十一、反向盲区：同一处「不占位」，一天内第二次（方向相反）

### 11.1 口径

`python scripts/audit_unclaimed_changes.py` 在 2026-11-13 20:45:05 的快照
共 37 条；其中「无活跃 claim」且「给不出凭据」的 7 条已逐条复核。凭据层级：
claim 记录 / §6.1 行 / `docs/pending-patches/` 补丁单 / 用户派单。

### 11.2 对三份补丁单的结果：0 命中

`2026-11-13-main-window-session-write-gate.md`、
`2026-11-13-keyboard-focus-chain-unlanded.md`、
`2026-11-13-main-window-tool-success-wiring.md` 三份单子对下列 7 条路径
**一条都没有提到**，所以「补丁单」这一层给不出任何依据。

### 11.3 4 条搬进 A 组（有 claim 记录 + 提交可指认）

| 路径 | 凭据 |
| --- | --- |
| `desktop/mcp_worker.py` | claim 20:45:56–20:46:39（S1-S4结果伪造线/6ae85f56，理由「协议缺口：三条返回都带 success」）；提交 `9196439a` 20:46:29 |
| `tests/test_mcp_worker_behavior.py` | claim 20:45:57–20:46:39（同上）；提交 `9196439a` |
| `tests/test_ai_steps_behavior.py` | claim 20:47:04–20:47:45（理由「信封用例改判」）；提交 `b898f4a3` 20:47:32 |
| `tests/test_ai_tool_consistency.py` | claim 20:47:04–20:47:44（理由「补账①用例」）；提交 `b898f4a3` |

### 11.4 剩余 3 条的收口（2026-11-13 21:21 复核）

三条线索是**查找方向**，不是证据；下列每条都附了可复核的文件 / 行 / 命令。
结论按「有没有可指认的凭据」分档，不按「像谁干的」分档。

| 路径 | 20:45 状态 | 收口结论 | 依据 |
| --- | --- | --- | --- |
| `docs/perf-evidence/2026-11-11/theme_switch_lowload_retest.json` | ?? | **A 组（直接引用）**：`守卫账本线` 的研究文档逐字点名此文件为证据 | `docs/research/2026-11-11-p0-static-inference-check-rest.md:123`「证据 `docs/perf-evidence/2026-11-11/theme_switch_lowload_retest.json`」；同段 L121 的 `103.1% / 108.6%`、`243–397 ms` 与该 JSON 的 `load_before/load_after`、`wall_ms` 逐数对上；该 research 文件在台账里的 owner 是 `守卫账本线` |
| `docs/learn/2026-11-13-qgis-3.44.15-source-study.md` | ?? | **A 组（间接凭据）**：台账里唯一一条 QGIS 相关 claim 来自 `Qt翻译与设置显式线/6ae85f56`，其理由与当天该线的提交同题，且文档落点正是该线改过的文件 | ① `.locks/claims.json` 中 `desktop/l10n.py` 的 history 有 `owner=Qt翻译与设置显式线/6ae85f56`、`claim_reason="QGIS学习项：加载 qtbase 翻译 + 设置显式标记，需改本文件"`（全仓唯一含 `QGIS` 的 claim 记录）；② 该线当天提交 `c4a2f345`（19:37:38）`feat(i18n): 加载 Qt 自带 qtbase 翻译`；③ 文档 §8.1（L313–328）逐条对照 `desktop/l10n.py`、`desktop/i18n/build_qm.py`。**该文件本身从未被 claim**，所以这是「旁证链」不是「占位凭据」——按 A 组记，但标注凭据类型 |
| `tools/axioms/__init__.py` | M | **不属于任何线：它不是一条改动**——工作区内容与 HEAD 同一 blob，`git diff` 为空 | `git hash-object tools/axioms/__init__.py` = `8072776681…` = `git rev-parse HEAD:tools/axioms/__init__.py`；`git diff --quiet -- tools/axioms/__init__.py` 退出码 0；`git status` 仍报 `M` 是行尾假象（`core.autocrlf=true`、工作区存 LF），文件 mtime 为 2026-11-13 00:40:12。既然内容没变，就既不搬 A 组也不记「无凭据」，按「假改动」销账 |

销账口径：前两条**搬进 A 组**（一条直接、一条间接且注明凭据类型），第三条按
「无内容差异」销账。三条都**没有替任何线补 claim、也没有替任何线写 §6.1 行**。

### 11.5 反向盲区（同一处缺陷，方向相反）

20:45:05 做审计快照时，上表 4 条路径**没有任何 claim**；51 秒后
（20:45:56/57）那四处才被 claim。也就是说：

- 上一次（`052828d1`）是「**占了位就当成内容归我**」；
- 这一次是「**干活不占位，别人就看不见**」。

两者是同一个盲区的两面：都拿「claim 表里有没有」当「有没有主」。区别只在
谁吃亏——上次是别人被我卷走，这次是那条线的在途改动在审计里显示为「无主」。

**明确回答「此刻是否正被工具成败线改」**：是。20:45 那一刻，`desktop/mcp_worker.py`
与 `tests/test_ai_steps_behavior.py`、`tests/test_ai_tool_consistency.py`、
`tests/test_mcp_worker_behavior.py` 正被「S1-S4结果伪造线/6ae85f56」修改且
未提交；它们分别在 `9196439a`（20:46:29，mcp_worker + 其行为测试）与
`b898f4a3`（20:47:32，ai_steps + main_window + tool_registry + 两个测试）落库，
**现在已不是未提交状态**。

本线对这条线只做记录：**没有替它加 claim，也没有替它写 §6.1 行**。

